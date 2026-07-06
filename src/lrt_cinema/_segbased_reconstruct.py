"""Clean-room segmentation-based highlight reconstruction (survey #2).

THE ALGORITHM (darktable's "segmentation based" highlights mode, v2 —
Iain/garagecoder/Hanno Schwalm; the survey's only classical answer to
LARGE fully-blown areas). Clean-room reimplementation from the algorithm
structure extracted off dt's `src/iop/hlreconstruct/segbased.c` +
`segmentation.c` during the 2026-07-06 read-to-learn pass (no GPL code
copied — anti-drift rule 6; same discipline as the AMaZE/RCD/opposed
ports). Two independent mechanisms:

BASE LAYER: the reference runs its OPPOSED reconstruction into the
working buffer first (highlights.c hands `tmpout` from
`_process_opposed`, at the SEGMENTS clip magic) — candidates and the
rebuild then REFINE that base. This is the "averaging approximation"
fallback the reference's overview describes: segments whose candidate
search fails keep the opposed estimate. This port does the same via
`reconstruct_mosaic_opposed(clip_magic=0.987)`.

CANDIDATES (partial clipping — some channel survives nearby):
  1. Downsample the mosaic to a 1/3-resolution per-channel plane grid:
     each 3x3 photosite box (green-centred) contributes its per-channel
     means, stored in CUBE-ROOT domain; `refavg` = the opponent-mean
     pseudo-chromacity (mean of the OTHER two channels' cube roots).
  2. Per channel, flag plane cells whose value exceeds cbrt(clip_c);
     morphologically CLOSE the flag masks (radius `combine`, dt's
     quasi-disk footprints) so nearby blobs share a fate; segment the
     result (4-connected, minimum size 4, scan-order ids; unclipped
     4-neighbours are marked as segment BORDER cells).
  3. Per segment, pick the best unclipped CANDIDATE cell inside the
     bbox(+2): weight = smoothness (1 - 10*sqrt(local 5x5 std)) x
     (border cell ? 1.0 : 0.75); accept if weight > 1 - candidating.
     The candidate's value1 = 5x5 binomial mean of unclipped plane
     values (capped at clip), value2 = refavg at the candidate.
  4. Every clipped photosite in that segment is rebuilt in cube-root
     space:  out = cube( refavg_here + value1 - value2 )  — inpainting
     pseudo-chromacity, not luminance — and floored at the input value.

REBUILD (all three channels clipped — no data anywhere nearby):
  5. On the plane grid, take the all-clipped mask, close it with the
     mode's `recovery_close`, build a blurred luminance plane and an
     exact Euclidean distance transform of the mask.
  6. Seed gradients at the mask rim (4x Scharr magnitude of luminance
     where 0 < distance < 2), then propagate inward ring by ring
     (ring width 1.5): each ring cell averages the previous ring's
     gradients in its 5x5 neighbourhood, boosted by
     (1 + 1/distance^attenuate) and clamped at 1.5. Box-blur the
     segment slab (radius ~ segment max distance, 2 passes) to kill
     ridges, scale by the mode's strength correction, Gaussian-blur.
  7. Write back per clipped photosite with a sigmoid of the distance:
     out += gradient * strength / (1 + exp(-(distance - dshift))).

DOMAIN CONTRACT (slot 3): input is the BALANCED headroom mosaic (same
contract as `reconstruct_mosaic_opposed`); per-channel clip level is
`0.987 x wb_mul[c]` (dt's SEGMENTS magic 0.987 - vs 0.995 for opposed).
Balanced input collapses dt's WB bookkeeping: its `icoeffs`/late-WB
`correction` are identity here.

DOCUMENTED DEVIATIONS from the reference (validated against the dt-cli
anchor by metric, not bit-parity): scan-order segment ids via
scipy.ndimage.label + first-claim border assignment (dt marks borders
inside its flood fill; contention between two segments over one border
cell is resolved by id order in both); exact Gaussian (sigma 1.2)
instead of dt's fast approximation; scipy uniform_filter as the box
mean; Poisson noise NOT implemented (default 0 in dt; a timelapse wants
deterministic frames); Bayer only.
"""

from __future__ import annotations

import numpy as np

_CLIP_MAGIC = np.float32(0.987)     # dt highlights_clip_magics[SEGMENTS]
_HL_BORDER = 8                      # plane-grid working border
_SEG_BORDER = _HL_BORDER + 1        # segmentation working border
_MIN_SEG_SIZE = 4                   # smaller blobs stay unsegmented
_BINOMIAL5 = np.outer(np.array([1.0, 4.0, 6.0, 4.0, 1.0], np.float32),
                      np.array([1.0, 4.0, 6.0, 4.0, 1.0], np.float32))

RECOVERY_MODES = ("off", "small", "large", "smallf", "largef", "adapt",
                  "adaptf")
# per-mode gradient attenuation exponent / mask closing radius / sigmoid
# shift follow the reference's tables (index order = RECOVERY_MODES).
_ATTENUATE = {"off": 0.0, "small": 1.7, "large": 1.0, "smallf": 1.7,
              "largef": 1.0, "adapt": 1.0, "adaptf": 1.0}
_RECOVERY_CLOSE = {"off": 0, "small": 0, "large": 0, "smallf": 2,
                   "largef": 2, "adapt": 0, "adaptf": 2}

# dt's quasi-disk morphology footprints, radius 1..8 — per-radius ADDED
# offsets (dy, dx), transcribed tap-for-tap. The reference's radius-8
# block contains duplicate row -7 taps (i-w5+6 style quirks and an
# i-w7+6 re-OR on the +w7 line); duplicates are no-ops there, so row +7
# carries only 5 taps — verified tap-set-diff against the C source.
def _ring(*items):
    out = []
    for dy, xs in items:
        out.extend((dy, dx) for dx in xs)
    return out


_R = range
_FOOTPRINT_ADD = {
    1: _ring((-1, _R(-1, 2)), (0, _R(-1, 2)), (1, _R(-1, 2))),
    2: _ring((-2, _R(-1, 2)), (-1, (-2, 2)), (0, (-2, 2)), (1, (-2, 2)),
             (2, _R(-1, 2))),
    3: _ring((-3, _R(-2, 3)), (-2, (-3, -2, 2, 3)), (-1, (-3, 3)),
             (0, (-3, 3)), (1, (-3, 3)), (2, (-3, -2, 2, 3)),
             (3, _R(-2, 3))),
    4: _ring((-4, _R(-2, 3)), (-3, (-3, 3)), (-2, (-4, 4)), (-1, (-4, 4)),
             (0, (-4, 4)), (1, (-4, 4)), (2, (-4, 4)), (3, (-3, 3)),
             (4, _R(-2, 3))),
    5: _ring((-5, _R(-2, 3)), (-4, (-4, -3, 3, 4)), (-3, (-4, 4)),
             (-2, (-5, 5)), (-1, (-5, 5)), (0, (-5, 5)), (1, (-5, 5)),
             (2, (-5, 5)), (3, (-4, 4)), (4, (-4, -3, 3, 4)),
             (5, _R(-2, 3))),
    6: _ring((-6, _R(-2, 3)), (-5, (-4, -3, 3, 4)), (-4, (-5, 5)),
             (-3, (-5, 5)), (-2, (-6, 6)), (-1, (-6, 6)), (0, (-6, 6)),
             (1, (-6, 6)), (2, (-6, 6)), (3, (-5, 5)), (4, (-5, 5)),
             (5, (-4, -3, 3, 4)), (6, _R(-2, 3))),
    7: _ring((-7, _R(-3, 4)), (-6, (-4, -3, 3, 4)), (-5, (-6, -5, 5, 6)),
             (-4, (-6, 6)), (-3, (-7, -6, 6, 7)), (-2, (-7, 7)),
             (-1, (-7, 7)), (0, (-7, 7)), (1, (-7, 7)), (2, (-7, 7)),
             (3, (-7, -6, 6, 7)), (4, (-6, 6)), (5, (-6, -5, 5, 6)),
             (6, (-4, -3, 3, 4)), (7, _R(-3, 4))),
    8: _ring((-8, _R(-4, 5)), (-7, (-6, -5, -4, 4, 5, 6)),
             (-6, (-6, -5, 5, 6)), (-5, (-7, 6)),
             (-4, (-8, -7, 7, 8)), (-3, (-8, -7, 7, 8)), (-2, (-8, 8)),
             (-1, (-8, 8)), (0, (-8, 8)), (1, (-8, 8)), (2, (-8, 8)),
             (3, (-8, -7, 7, 8)), (4, (-8, -7, 7, 8)), (5, (-7, 7)),
             (6, (-6, -5, 5, 6)), (7, (-6, -5, -4, 4, 5)),
             (8, _R(-4, 5))),
}


def _footprint(radius: int) -> np.ndarray:
    """Boolean structuring element for dt's radius-r quasi-disk."""
    r = max(1, min(8, int(radius)))
    fp = np.zeros((2 * r + 1, 2 * r + 1), bool)
    for k in range(1, r + 1):
        for dy, dx in _FOOTPRINT_ADD[k]:
            fp[dy + r, dx + r] = True
    fp[r, r] = True
    return fp


def _combine(mask: np.ndarray, radius: int) -> np.ndarray:
    """Morphological closing with dt's semantics: dilate(radius) — the
    reference's dilate applies its 3x3 stage even for radius 0/1 — and
    erode only for radius > 3, with radius-3 (small radii only merge).
    The working border is zeroed before and after, as in the reference."""
    from scipy.ndimage import binary_dilation, binary_erosion

    def zero_border(m):
        m[:_SEG_BORDER, :] = m[-_SEG_BORDER:, :] = False
        m[:, :_SEG_BORDER] = m[:, -_SEG_BORDER:] = False
        return m

    r = int(radius)
    out = zero_border(mask.copy())
    out = binary_dilation(out, structure=_footprint(max(1, r)))
    if r > 3:
        out = binary_erosion(out, structure=_footprint(r - 3),
                             border_value=1)
    return zero_border(out)


def _segmentize(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, list]:
    """4-connected components of `mask` in scan order, minimum size 4.

    Returns (ids, border_ids, segments): `ids` (H, W) int32 with 0 =
    none; `border_ids` — unclipped 4-neighbour cells claimed by the
    lowest-id adjacent segment; segments = list of dicts with bbox
    (incl. border cells, as in the reference) and size.
    """
    from scipy.ndimage import binary_dilation, find_objects, label

    struct4 = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], bool)
    lab, n = label(mask, structure=struct4)
    ids = np.zeros(mask.shape, np.int32)
    border = np.zeros(mask.shape, np.int32)
    segs = []
    next_id = 2
    for raw_id, sl in zip(range(1, n + 1), find_objects(lab), strict=True):
        m = lab[sl] == raw_id
        size = int(m.sum())
        if size < _MIN_SEG_SIZE:
            continue
        ids[sl][m] = next_id
        b = binary_dilation(m, structure=struct4) & ~m
        by, bx = np.nonzero(b)
        by = by + sl[0].start
        bx = bx + sl[1].start
        claim = border[by, bx] == 0
        border[by[claim], bx[claim]] = next_id
        y0, y1 = sl[0].start, sl[0].stop - 1
        x0, x1 = sl[1].start, sl[1].stop - 1
        if len(by):
            y0 = min(y0, by.min())
            y1 = max(y1, by.max())
            x0 = min(x0, bx.min())
            x1 = max(x1, bx.max())
        segs.append({"id": next_id, "size": size,
                     "ymin": y0, "ymax": y1, "xmin": x0, "xmax": x1,
                     "val1": 0.0, "val2": 0.0})
        next_id += 1
    return ids, border, segs


def _local_std(plane: np.ndarray) -> np.ndarray:
    """The reference's 21-tap local deviation (5x5 minus the corners)."""
    from scipy.ndimage import convolve

    fp = np.ones((5, 5), np.float32)
    fp[0, 0] = fp[0, 4] = fp[4, 0] = fp[4, 4] = 0.0
    k = fp / np.float32(21.0)
    av = convolve(plane, k, mode="nearest")
    var = convolve((plane) ** 2, k, mode="nearest") - av * av
    return np.sqrt(np.maximum(var, 0.0))


def _plane_candidates(plane, refavg, ids, border_ids, segs, clipval,
                      badlevel):
    """Best-candidate search per segment (reference step 3)."""
    h, w = plane.shape
    smooth = np.maximum(0.0, 1.0 - 10.0 * np.sqrt(
        np.maximum(_local_std(plane), 0.0)))
    # The reference multiplies by sval = max(1, (min(clip, box3)/clip)^2),
    # which is identically 1 — omitted as arithmetic, noted for fidelity.
    weight = smooth * np.where(border_ids > 0, 1.0, 0.75)
    unclipped = plane < clipval
    lo = _SEG_BORDER + 2
    for s in segs:
        if not (s["ymax"] - s["ymin"] > 2 and s["xmax"] - s["xmin"] > 2):
            continue
        y0 = max(lo, s["ymin"] - 2)
        y1 = min(h - lo, s["ymax"] + 3)
        x0 = max(lo, s["xmin"] - 2)
        x1 = min(w - lo, s["xmax"] + 3)
        if y1 <= y0 or x1 <= x0:
            continue
        sid = s["id"]
        member = (ids[y0:y1, x0:x1] == sid) | (border_ids[y0:y1, x0:x1] == sid)
        sel = member & unclipped[y0:y1, x0:x1]
        if not sel.any():
            continue
        wts = np.where(sel, weight[y0:y1, x0:x1], 0.0)
        best = np.unravel_index(int(np.argmax(wts)), wts.shape)
        testweight = float(wts[best])
        if testweight <= 1.0 - badlevel:
            continue
        ty, tx = best[0] + y0, best[1] + x0
        win = plane[ty - 2:ty + 3, tx - 2:tx + 3]
        ok = win < clipval
        pix = np.maximum(1.0, _BINOMIAL5[ok].sum())
        av = float((win * _BINOMIAL5)[ok].sum() / pix)
        if av > 0.125 * clipval:
            s["val1"] = min(float(clipval), av)
            s["val2"] = float(refavg[ty, tx])


def _scharr_mag(lum: np.ndarray) -> np.ndarray:
    from scipy.ndimage import convolve

    kx = np.array([[47.0, 0.0, -47.0],
                   [162.0, 0.0, -162.0],
                   [47.0, 0.0, -47.0]], np.float32) / 255.0
    gx = convolve(lum, kx, mode="nearest")
    gy = convolve(lum, kx.T, mode="nearest")
    return np.hypot(gx, gy)


def _segment_gradients(distance, gradient, seg, mode, recovery_close):
    """Ring-by-ring gradient propagation + slab box blur (steps 6)."""
    from scipy.ndimage import uniform_filter

    h, w = distance.shape
    y0 = max(seg["ymin"] - 1, _SEG_BORDER)
    y1 = min(seg["ymax"] + 2, h - _SEG_BORDER)
    x0 = max(seg["xmin"] - 1, _SEG_BORDER)
    x1 = min(seg["xmax"] + 2, w - _SEG_BORDER)
    if y1 <= y0 or x1 <= x0:
        return
    maxdist_seg = seg["val1"]
    if mode in ("adapt", "adaptf"):
        attenuate = min(1.7, 0.9 + 3.0 / max(1.0, maxdist_seg))
    else:
        attenuate = _ATTENUATE[mode]
    strength_corr = attenuate - 0.1 * recovery_close

    dist = distance[y0:y1, x0:x1]
    grad = gradient[y0:y1, x0:x1]
    member = seg["member"][y0:y1, x0:x1]

    d = 1.5
    while d < maxdist_seg:
        ring = (dist >= d) & (dist < d + 1.5) & member
        if ring.any():
            src = (dist >= d - 1.5) & (dist < d)
            srcg = np.where(src, grad, 0.0)
            cnt = uniform_filter(src.astype(np.float32), size=5,
                                 mode="constant") * 25.0
            acc = uniform_filter(srcg.astype(np.float32), size=5,
                                 mode="constant") * 25.0
            with np.errstate(divide="ignore", invalid="ignore"):
                avg = np.where(cnt > 0.0, acc / np.maximum(cnt, 1e-9), 0.0)
            boost = 1.0 + 1.0 / np.power(np.maximum(dist, 1e-6), attenuate)
            upd = ring & (cnt > 0.0)
            grad[upd] = np.minimum(1.5, (avg * boost)[upd])
        d += 1.5

    if d > 4.0:
        r = min(int(d), 15)
        blurred = grad.copy()
        for _ in range(2):
            blurred = uniform_filter(blurred, size=2 * r + 1, mode="nearest")
        grad[member] = blurred[member]
    grad[member] *= strength_corr
    gradient[y0:y1, x0:x1] = grad


def reconstruct_mosaic_segbased(
    cfa: np.ndarray, chan: np.ndarray, wb_mul: np.ndarray, *,
    clip: float = 1.0, combine: float = 2.0, candidating: float = 0.4,
    recovery: str = "off", strength: float = 0.0,
) -> np.ndarray:
    """Segmentation-based reconstruction ON the balanced headroom mosaic.

    `cfa` (H, W) float32 BALANCED Bayer mosaic with per-channel headroom
    (no common-white clamp); `chan` (H, W) int CFA channel per site (G2
    folded to 1); `wb_mul` (3,) G-normalised multipliers. `recovery` one
    of RECOVERY_MODES (+ `strength` in [0,1]) enables the all-clipped
    rebuild. Returns the mosaic with clipped sites reconstructed;
    unclipped sites byte-identical.
    """
    from scipy.ndimage import distance_transform_edt, gaussian_filter

    if recovery not in RECOVERY_MODES:
        raise ValueError(f"recovery must be one of {RECOVERY_MODES}")
    h, w = cfa.shape
    clipval = max(0.1, float(_CLIP_MAGIC) * float(clip))
    clips = (clipval * np.asarray(wb_mul, np.float32)).astype(np.float32)
    cube_coeffs = np.cbrt(clips)

    site_clipped = cfa > clips[chan]
    if not site_clipped.any():
        return cfa

    # ---- opposed base layer (the reference's tmpout) -------------------------
    from ._opposed_reconstruct import reconstruct_mosaic_opposed

    base = reconstruct_mosaic_opposed(cfa, chan, wb_mul,
                                      clip_magic=clipval)

    # ---- 1/3-res plane construction (green-centred boxes) -------------------
    # Planes are built from the OPPOSED-modified buffer (the reference
    # reads tmpout); full-res clip tests and refavg use the ORIGINAL.
    # (RGGB-family: FC(0,0) != G -> xshifter 2; centre rows at row%3==1.)
    g00 = int(chan[0, 0] == 1)
    xshift = 1 if g00 else 2
    ny = (h - 2) // 3 + 1
    nx = (w - 2) // 3 + 1
    ph = ny + 2 * _HL_BORDER
    pw = nx + 2 * _HL_BORDER
    B = _HL_BORDER
    plane = np.zeros((3, ph, pw), np.float32)
    refavg = np.zeros((3, ph, pw), np.float32)

    # per-channel sums/counts over each 3x3 box whose centre is at
    # (3k+1, 3m+xshift) — vectorised over the box grid
    cy = np.arange(1, h - 1, 3)
    cx = np.arange(xshift, w - 1, 3)
    cy = cy[cy + 1 < h]
    cx = cx[cx + 1 < w]
    sums = np.zeros((3, len(cy), len(cx)), np.float32)
    cnts = np.zeros((3, len(cy), len(cx)), np.float32)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            vals = base[np.ix_(cy + dy, cx + dx)]
            cc = chan[np.ix_(cy + dy, cx + dx)]
            for c in range(3):
                m = cc == c
                sums[c] += np.where(m, vals, 0.0)
                cnts[c] += m
    means = np.where(cnts > 0, np.cbrt(np.maximum(sums, 0.0)
                                       / np.maximum(cnts, 1.0)), 0.0)
    ra = np.stack([0.5 * (means[1] + means[2]),
                   0.5 * (means[0] + means[2]),
                   0.5 * (means[0] + means[1])])
    oy = B + cy // 3
    ox = B + cx // 3
    plane[:, oy[0]:oy[-1] + 1, ox[0]:ox[-1] + 1] = means
    refavg[:, oy[0]:oy[-1] + 1, ox[0]:ox[-1] + 1] = ra

    clipmask = np.zeros((4, ph, pw), bool)
    for c in range(3):
        clipmask[c, oy[0]:oy[-1] + 1, ox[0]:ox[-1] + 1] = \
            means[c] > cube_coeffs[c]
    clipmask[3] = clipmask[0] & clipmask[1] & clipmask[2]

    if int(clipmask[:3].sum()) < 20:
        return base

    # replicate-extend plane borders (the reference's mask extension)
    for c in range(3):
        core = plane[c, B:ph - B, B:pw - B]
        plane[c] = np.pad(core, B, mode="edge")
        refavg[c] = np.pad(refavg[c, B:ph - B, B:pw - B], B, mode="edge")

    # ---- combine + segmentize + candidates per colour plane -----------------
    out = base.copy()
    plane_ids = []
    for c in range(3):
        m = _combine(clipmask[c], int(combine))
        ids, border_ids, segs = _segmentize(m)
        _plane_candidates(plane[c], refavg[c], ids, border_ids, segs,
                          float(cube_coeffs[c]), float(candidating))
        # full-res lookup resolves BOTH interior and border cells to the
        # segment id (the reference's _get_segment_id strips the border
        # mask bit and accepts either)
        both = np.where(ids > 0, ids, border_ids)
        plane_ids.append((both, {s["id"]: s for s in segs}))

    # ---- candidate write-back at full resolution -----------------------------
    ys, xs = np.nonzero(site_clipped)
    oy_full = B + ys // 3
    ox_full = B + xs // 3
    ch_sites = chan[ys, xs]
    # refavg at the photosite: 3x3 raw window per-channel means (cbrt)
    for c in range(3):
        sel = ch_sites == c
        if not sel.any():
            continue
        ids, segmap = plane_ids[c]
        pid = ids[oy_full[sel], ox_full[sel]]
        val1 = np.array([segmap[p]["val1"] if p in segmap else 0.0
                         for p in pid], np.float32)
        val2 = np.array([segmap[p]["val2"] if p in segmap else 0.0
                         for p in pid], np.float32)
        good = val1 != 0.0
        if not good.any():
            continue
        yy = ys[sel][good]
        xx = xs[sel][good]
        ref_here = _refavg_at(cfa, chan, yy, xx, c)
        oval = (ref_here + val1[good] - val2[good]) ** 3
        rebuilt = np.maximum(cfa[yy, xx], oval.astype(np.float32))
        out[yy, xx] = rebuilt
        # The reference also writes the (LINEAR) rebuilt value back into
        # the cube-root plane — a domain quirk kept verbatim; the value
        # feeds only the rebuild-luminance blur. Raster order = last
        # site in a box wins, as in the reference's sequential loop.
        plane[c][oy_full[sel][good], ox_full[sel][good]] = rebuilt

    # ---- all-clipped rebuild --------------------------------------------------
    if recovery != "off" and strength > 0.0 and clipmask[3].any():
        rc = _RECOVERY_CLOSE[recovery]
        # the reference always runs segments-combine here (its dilate
        # applies the 3x3 stage even at radius 0)
        allmask = _combine(clipmask[3], rc)

        lum = (plane[0] + plane[1] + plane[2]) / 3.0
        lum = np.pad(lum[_SEG_BORDER:-_SEG_BORDER, _SEG_BORDER:-_SEG_BORDER],
                     _SEG_BORDER, mode="edge")
        luminance = np.clip(gaussian_filter(lum, 1.2), 0.0, 20.0)
        distance = distance_transform_edt(allmask).astype(np.float32)

        if distance.max() > 3.0:
            ids_all, _border_all, segs_all = _segmentize(allmask)
            rim = (distance > 0.0) & (distance < 2.0)
            recout = np.where(rim, 4.0 * _scharr_mag(luminance),
                              0.0).astype(np.float32)
            recout = np.pad(
                recout[_SEG_BORDER:-_SEG_BORDER, _SEG_BORDER:-_SEG_BORDER],
                _SEG_BORDER, mode="edge")
            for s in segs_all:
                s["member"] = ids_all == s["id"]
                s["val1"] = float(np.where(s["member"], distance, 0.0).max())
                if s["val1"] > 2.0:
                    _segment_gradients(distance, recout, s, recovery, rc)
            gradient = np.clip(gaussian_filter(recout, 1.2), 0.0, 20.0)

            dshift = 2.0 + rc
            eff = float(strength) / (1.0 + np.exp(
                -(distance[oy_full, ox_full] - dshift)))
            add = np.maximum(0.0, gradient[oy_full, ox_full] * eff)
            out[ys, xs] = out[ys, xs] + add.astype(np.float32)

    return np.maximum(out, 0.0).astype(np.float32, copy=False)


def _refavg_at(cfa, chan, ys, xs, color):
    """Cube-root opponent-mean refavg at full-res photosites (3x3)."""
    h, w = cfa.shape
    sums = np.zeros((3, len(ys)), np.float32)
    cnts = np.zeros((3, len(ys)), np.float32)
    for dy in (-1, 0, 1):
        yy = np.clip(ys + dy, 0, h - 1)
        inb_y = (ys + dy >= 0) & (ys + dy <= h - 1)
        for dx in (-1, 0, 1):
            xx = np.clip(xs + dx, 0, w - 1)
            inb = inb_y & (xs + dx >= 0) & (xs + dx <= w - 1)
            vals = np.maximum(cfa[yy, xx], 0.0)
            cc = chan[yy, xx]
            for c in range(3):
                m = (cc == c) & inb
                sums[c] += np.where(m, vals, 0.0)
                cnts[c] += m
    means = np.where(cnts > 0, np.cbrt(sums / np.maximum(cnts, 1.0)), 0.0)
    opp = {0: (1, 2), 1: (0, 2), 2: (0, 1)}[color]
    return 0.5 * (means[opp[0]] + means[opp[1]])
