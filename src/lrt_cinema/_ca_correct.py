"""Clean-room raw chromatic-aberration correction (Martinec CA_correct).

ALGORITHM (E. Martinec 2008-2010; iterated correction + avoid-colourshift
I. Weyrich 2018 — the dt/RT shared canon: darktable's `iop/cacorrect.c` IS
RawTherapee's `CA_correct_RT.cc` vendored, per its own header). Clean-room
reimplementation from the algorithm structure extracted off BOTH local
sources during the 2026-07-07 port session (read-to-learn; no GPL code
copied — anti-drift rule 6; same discipline as the AMaZE/RCD/opposed/
segbased ports). Operates on the BALANCED Bayer mosaic PRE-demosaic
(TARGET slot 2; dt pipe order: temperature@3 → highlights@4 →
**cacorrect@5** → demosaic@8).

Model: lateral CA displaces the R and B optical images relative to G by a
smooth field ≤ ~4 px. Per iteration:

  DIAGNOSTIC PASS (global; per 112×112 block):
  1. G is interpolated to every R/B site by a gradient-weighted 4-neighbour
     directional average (weights from G gradients + same-channel 2-px
     gradients).
  2. Per R/B site, high-pass (rbhpf) and low-pass (rblpf/grblpf) 1-D
     filters of the G−C colour difference / G,C means, vertical and
     horizontal, on the same-channel 2-px/4-px lattice.
  3. Colour differences under linear interpolation are quadratic in the
     interpolation position: per block, accumulate the gradient-weighted
     moments (Σw·Δ², Σw·gΔ, Σw·g²) of deltgrb = C−G against the directional
     G derivative gdiff; the per-block CA shift = Σw·gΔ / Σw·g² (the
     variance-minimising position). Block weight = Σw·g² / (eps + Σw·Δ²).
  4. Blocks are 3×3-median-filtered, variance-gated
     (shift² ≤ caautostrength·blockvar), and a weighted 4×4-order 2-D
     polynomial (bilinear fallback when < 32 blocks; abort when < 10) is
     LSQ-fitted to the shift field per (colour, direction) — Gaussian
     elimination with partial pivoting, ported exactly (including the
     reference's signed-pivot-candidate quirk).
  CORRECTION PASS (per 128-px tile, 112-px stride, 8-px overlap borders):
  5. Per tile, evaluate the fitted shift (clamped ±3.99), bilinearly sample
     G at each R/B site's optical location (Gint), form the shifted colour
     difference grbdiff = Gint − C, interpolate it back to the grid between
     adjacent same-colour sites (site spacing 2 → frac/2), and correct
     C = G − grbdiffint, guarded: only when it SHRINKS the colour
     difference; a gradient-weighted 4-tap fallback when the direct
     estimate deviates > 25 % from C; average-desaturate on sign overshoot.

  AVOID-COLOURSHIFT (optional, after all iterations): per-site old/new
  factors clamped [0.5, 2], Gaussian-blurred (σ=30) at half resolution,
  multiplied back — cancels the mean R/B level shift CA correction causes.

DOCUMENTED DIVERGENCES between the two references (adjudicated here):
  - caautostrength: dt 4.0 vs RT 8.0 → we take dt's 4.0 (primary source;
    tighter outlier gate).
  - dt declares the block statistics, polyord and fitparams OUTSIDE its
    iterations loop (they accumulate/persist across iterations — a
    vendoring artifact of where dt's parallel region sits); RT resets them
    per iteration → we take RT's per-iteration reset (the original).
  - write-back clamp: RT clamps corrected values ≥ 0, dt does not → we
    clamp (preserves the mosaic ≥ 0 invariant `_extract_cfa` establishes).
  - avoid-shift guard: RT forces factor = 1 where old/new ≤ 1/65535 (its
    raw units); dt divides unguarded (NaN on 0/0) → we take RT's guard,
    translated to normalised units.
  - avoid-shift blur: both use their own IIR Gaussian (σ=30); we use
    scipy's FIR `gaussian_filter` (mode="nearest") — an approximation at
    image borders only.
  - avoid-shift apply range: dt multiplies rows [2, H−2), cols [·, W−2);
    RT multiplies everything. We follow dt (primary).
  - avoid-shift placement: dt runs it ONCE after all iterations; RT runs
    it inside the iteration loop (re-pulling levels every pass). We follow
    dt (primary).
  - precision: the per-block statistics accumulate in float64 here (both
    references use float32 — counts are exact and sums are bounded, so the
    difference is sub-rounding); the correction-pass shift evaluation is
    float64, following RT (dt evaluates in float32).
  - `_lin_eq_solve` aborts on a zero LAST pivot (the references divide
    unconditionally in back-substitution → silent inf/nan into fitparams);
    aborting fails safe to no-correction.

EQUIVALENCE NOTE (tile borders → global arrays): the references fill each
tile's 8-px borders by mirror-reflection at image edges (virtual row −k =
row +k about row 0; row H−1+k = row H−1−k), which is exactly
`np.pad(..., mode="reflect")`, phase-preserving (even border). The pass-1
G interpolation is computed here once, globally, on the reflect-padded
mosaic; the references recompute it per tile, but every consumed value
(filters are only read ≥ 6 px inside a tile; corrections ≥ 8 px) lies in
the region where tile-local and global computation see identical inputs,
and the direction-summed interpolation is mirror-symmetric, so reflected
G values equal G values of reflected inputs.

INPUT CONTRACT: BALANCED Bayer mosaic (slot-3 world: WB applied at the
mosaic, G2 folded to G), float32, EVEN dims, values ≥ 0 with white at
`scale` (clip mode: `wb_mul.min()`; headroom mode: `wb_mul.max()`). The
mosaic is normalised by `scale` internally (the references normalise to
[0, 1] the same way) and G sites are returned BYTE-IDENTICAL. Runtime is
numpy-only, ~10-20 s at 24 MP — acceptable for an opt-in stage; a numba
twin is future perf-campaign work (task 18 pattern).

Validation: tests/test_ca_correct.py (synthetic known-shift recovery,
zero-shift stability, G invariance); tools/ca_correct_experiment.py
(articles no-regression + gym clip-edge fringe interaction, evidence
JSON); owner flips at ~/lrt-cinema-fixtures/verify-2026-07-07/ca-flip/.
"""

from __future__ import annotations

import sys

import numpy as np

_TS = 128            # tile size
_BORDER = 8          # tile overlap border
_STRIDE = _TS - 2 * _BORDER  # 112: tile stride == owned (non-overlapping) region
_CAAUTOSTRENGTH = 4.0        # block variance gate (dt; RT uses 8.0)
_BSLIM = 3.99                # max allowed CA shift, px
_EPS = np.float32(1e-5)
_EPS2 = np.float32(1e-10)
_AVOIDSHIFT_GUARD = np.float32(1.0 / 65535.0)  # RT's ≤1 raw-unit factor guard
_AVOIDSHIFT_SIGMA = 30.0

_VALID_PATTERNS = ("RGGB", "BGGR", "GRBG", "GBRG")


def _color_parities(pattern: str) -> dict[int, tuple[int, int]]:
    """Per colour c ∈ {0 (R), 2 (B)}: the (row, col) parity of its sites."""
    grid = {}
    for i, ch in enumerate(pattern):
        c = {"R": 0, "G": 1, "B": 2}[ch]
        if c != 1:
            grid[c] = (i // 2, i % 2)
    return grid


def _interp_g_to_nong(pad: np.ndarray,
                      parities: dict[int, tuple[int, int]]) -> np.ndarray:
    """Full-image G plane: raw G at G sites, gradient-weighted directional
    4-neighbour interpolation at R/B sites (diagnostic-pass step 1).

    `pad` is the reflect-8-padded mosaic; result is IMAGE-domain (h, w)."""
    hp, wp = pad.shape
    gtmp = pad[8:hp - 8, 8:wp - 8].copy()
    for _c, (pr, qc) in parities.items():
        # image-domain sites of this colour, in padded coords [8, hp-8)
        r0, c0 = 8 + pr, 8 + qc

        def v(dy: int, dx: int, r0=r0, c0=c0) -> np.ndarray:
            return pad[r0 + dy: hp - 8 + dy: 2, c0 + dx: wp - 8 + dx: 2]

        ctr = v(0, 0)
        gu, gd_ = v(-1, 0), v(1, 0)
        gl, gr = v(0, -1), v(0, 1)
        wtu = 1.0 / np.square(_EPS + np.abs(gd_ - gu)
                              + np.abs(ctr - v(-2, 0)) + np.abs(gu - v(-3, 0)))
        wtd = 1.0 / np.square(_EPS + np.abs(gu - gd_)
                              + np.abs(ctr - v(2, 0)) + np.abs(gd_ - v(3, 0)))
        wtl = 1.0 / np.square(_EPS + np.abs(gr - gl)
                              + np.abs(ctr - v(0, -2)) + np.abs(gl - v(0, -3)))
        wtr = 1.0 / np.square(_EPS + np.abs(gl - gr)
                              + np.abs(ctr - v(0, 2)) + np.abs(gr - v(0, 3)))
        gtmp[pr::2, qc::2] = ((wtu * gu + wtd * gd_ + wtl * gl + wtr * gr)
                              / (wtu + wtd + wtl + wtr)).astype(np.float32)
    return gtmp


def _lin_eq_solve(n: int, a: list[float], b: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting on a flat row-major n×n
    system — exact port of the references' LinEqSolve, INCLUDING the quirk
    that the running pivot-candidate magnitude is updated with the SIGNED
    element (affects pivot choice only when a later candidate is negative
    with larger magnitude than a still-later positive one). Mutates copies.
    Returns the solution, or None when the system is singular."""
    a = list(a)
    b = list(b)
    for k in range(n - 1):
        fmax = abs(a[k * n + k])
        m = k
        for i in range(k + 1, n):
            if fmax < abs(a[i * n + k]):
                fmax = a[i * n + k]  # signed — reference behaviour preserved
                m = i
        if m != k:
            for i in range(k, n):
                a[k * n + i], a[m * n + i] = a[m * n + i], a[k * n + i]
            b[k], b[m] = b[m], b[k]
        if a[k * n + k] == 0.0:
            return None
        for j in range(k + 1, n):
            f = -a[j * n + k] / a[k * n + k]
            for i in range(k, n):
                a[j * n + i] += f * a[k * n + i]
            b[j] += f * b[k]
    x = [0.0] * n
    for k in range(n - 1, -1, -1):
        if a[k * n + k] == 0.0:  # last pivot unchecked in the reference (→ inf)
            return None
        x[k] = b[k]
        for i in range(k + 1, n):
            x[k] -= a[k * n + i] * x[i]
        x[k] /= a[k * n + k]
    return x


def _block_reduce(term: np.ndarray, row_pad: np.ndarray, col_pad: np.ndarray,
                  nv: int, nh: int) -> np.ndarray:
    """Sum a per-site term array into its (nv, nh) block grid.

    `row_pad`/`col_pad` are the padded coordinates of the term array's rows/
    cols; block k owns padded coords [8 + 112(k−1), 8 + 112k)."""
    n_r, n_c = term.shape
    if n_r == 0 or n_c == 0:
        return np.zeros((nv, nh), dtype=np.float32)
    r_edges = np.searchsorted(row_pad, 8 + _STRIDE * np.arange(nv + 1))
    c_edges = np.searchsorted(col_pad, 8 + _STRIDE * np.arange(nh + 1))
    # reduceat needs in-range, effectively-monotonic indices; an edge at/past
    # the end (or a non-increasing pair) marks an EMPTY block — clip its
    # start and zero its output explicitly.
    r_empty = r_edges[1:] <= r_edges[:-1]
    c_empty = c_edges[1:] <= c_edges[:-1]
    rows = np.add.reduceat(term, np.minimum(r_edges[:-1], n_r - 1), axis=0)
    rows[r_empty] = 0.0
    blocks = np.add.reduceat(rows, np.minimum(c_edges[:-1], n_c - 1), axis=1)
    blocks[:, c_empty] = 0.0
    return blocks.astype(np.float32)


def ca_correct_mosaic(cfa: np.ndarray, pattern: str, *, iterations: int = 2,
                      avoid_shift: bool = False,
                      scale: float | None = None) -> np.ndarray:
    """Correct lateral CA on a balanced Bayer mosaic. Returns a new array;
    G sites are byte-identical to the input. See module docstring."""
    if pattern not in _VALID_PATTERNS:
        raise ValueError(f"unsupported Bayer pattern {pattern!r}")
    if cfa.ndim != 2 or (cfa.shape[0] % 2) or (cfa.shape[1] % 2):
        raise ValueError("ca_correct_mosaic needs an even-dimensioned 2-D mosaic")
    if not 1 <= iterations <= 5:
        raise ValueError("iterations must be in [1, 5]")
    cfa = np.ascontiguousarray(cfa, dtype=np.float32)
    h, w = cfa.shape
    if scale is None:
        scale = max(1.0, float(cfa.max()))
    scale = float(scale)

    parities = _color_parities(pattern)
    work = (cfa / np.float32(scale)).astype(np.float32)
    oldraw = {c: work[pr::2, qc::2].copy()
              for c, (pr, qc) in parities.items()} if avoid_shift else None

    hp, wp = h + 16, w + 16
    # real tile grid (vblock/hblock 1-based) + the references' padded array
    # size (which can exceed the real grid; excess blocks stay zero and
    # enter the fit with weight 0 — semantics preserved)
    nv = len(range(-_BORDER, h, _STRIDE))
    nh = len(range(-_BORDER, w, _STRIDE))
    vz1 = 1 if (h + 16) % _STRIDE == 0 else 0
    hz1 = 1 if (w + 16) % _STRIDE == 0 else 0
    vblsz = int(np.ceil((h + 16) / _STRIDE + 2 + vz1))
    hblsz = int(np.ceil((w + 16) / _STRIDE + 2 + hz1))

    ok = True
    for _it in range(iterations):
        if not ok:
            break
        pad = np.pad(work, _BORDER, mode="reflect")
        gtmp = _interp_g_to_nong(pad, parities)
        gpad = np.pad(gtmp, _BORDER, mode="reflect")

        # ---- diagnostic pass: per-block shift measurement -----------------
        # per (colour, dir) block moment sums; index c>>1 ∈ {0 (R), 1 (B)}
        coeff = np.zeros((2, 3, 2, nv, nh), dtype=np.float32)  # [dir][k][c]
        for c, (pr, qc) in parities.items():
            cidx = c >> 1
            # filter lattice: sites within padded [6, hp-6) — one 2-px ring
            # wider than the coeff window so its ±2 neighbours exist
            fr0, fc0 = 6 + pr, 6 + qc

            def f(arr: np.ndarray, dy: int, dx: int,
                  fr0=fr0, fc0=fc0) -> np.ndarray:
                return arr[fr0 + dy: hp - 6 + dy: 2, fc0 + dx: wp - 6 + dx: 2]

            d0 = f(gpad, 0, 0) - f(pad, 0, 0)
            dvm = f(gpad, -4, 0) - f(pad, -4, 0)
            dvp = f(gpad, 4, 0) - f(pad, 4, 0)
            dhm = f(gpad, 0, -4) - f(pad, 0, -4)
            dhp = f(gpad, 0, 4) - f(pad, 0, 4)
            rbhpfv = np.abs(np.abs(d0 - dvp) + np.abs(dvm - d0)
                            - np.abs(dvm - dvp))
            rbhpfh = np.abs(np.abs(d0 - dhp) + np.abs(dhm - d0)
                            - np.abs(dhm - dhp))
            del dvm, dvp, dhm, dhp
            glpfv = 0.25 * (2.0 * f(gpad, 0, 0) + f(gpad, 2, 0) + f(gpad, -2, 0))
            glpfh = 0.25 * (2.0 * f(gpad, 0, 0) + f(gpad, 0, 2) + f(gpad, 0, -2))
            clpfv = 0.25 * (2.0 * f(pad, 0, 0) + f(pad, 2, 0) + f(pad, -2, 0))
            clpfh = 0.25 * (2.0 * f(pad, 0, 0) + f(pad, 0, 2) + f(pad, 0, -2))
            rblpfv = (_EPS + np.abs(glpfv - clpfv)).astype(np.float32)
            rblpfh = (_EPS + np.abs(glpfh - clpfh)).astype(np.float32)
            grblpfv = (glpfv + clpfv).astype(np.float32)
            grblpfh = (glpfh + clpfh).astype(np.float32)
            del glpfv, glpfh, clpfv, clpfh

            # coeff lattice: sites within padded [8, hp-8) — the union of
            # the tiles' interior regions, offset (1, 1) into the filter grid
            cr0, cc0 = 8 + pr, 8 + qc

            def g(arr: np.ndarray, dy: int, dx: int,
                  cr0=cr0, cc0=cc0) -> np.ndarray:
                return arr[cr0 + dy: hp - 8 + dy: 2, cc0 + dx: wp - 8 + dx: 2]

            # coeff-lattice site counts (rows/cols within padded [8, ·-8))
            nr_c = (hp - 16 - pr + 1) // 2
            nc_c = (wp - 16 - qc + 1) // 2

            def fl(arr: np.ndarray, di: int, dj: int,
                   nr_c=nr_c, nc_c=nc_c) -> np.ndarray:
                # filter-lattice neighbour: (di, dj) in 2-px site steps;
                # the coeff lattice sits at offset (1, 1) inside it
                return arr[1 + di: 1 + di + nr_c, 1 + dj: 1 + dj + nc_c]

            deltgrb = g(pad, 0, 0) - g(gpad, 0, 0)
            gdiff_v = (0.3125 * (g(gpad, 1, 0) - g(gpad, -1, 0))
                       + 0.09375 * (g(gpad, 1, 1) - g(gpad, -1, 1)
                                    + g(gpad, 1, -1) - g(gpad, -1, -1))
                       ).astype(np.float32)
            gdiff_h = (0.3125 * (g(gpad, 0, 1) - g(gpad, 0, -1))
                       + 0.09375 * (g(gpad, 1, 1) - g(gpad, 1, -1)
                                    + g(gpad, -1, 1) - g(gpad, -1, -1))
                       ).astype(np.float32)
            grbv_s = fl(grblpfv, -1, 0) + fl(grblpfv, 1, 0)
            gradwt_v = (np.abs(0.25 * fl(rbhpfv, 0, 0)
                               + 0.125 * (fl(rbhpfv, 0, 1) + fl(rbhpfv, 0, -1)))
                        * grbv_s / (_EPS + 0.1 * grbv_s
                                    + fl(rblpfv, -1, 0) + fl(rblpfv, 1, 0))
                        ).astype(np.float32)
            grbh_s = fl(grblpfh, 0, -1) + fl(grblpfh, 0, 1)
            gradwt_h = (np.abs(0.25 * fl(rbhpfh, 0, 0)
                               + 0.125 * (fl(rbhpfh, 1, 0) + fl(rbhpfh, -1, 0)))
                        * grbh_s / (_EPS + 0.1 * grbh_s
                                    + fl(rblpfh, 0, -1) + fl(rblpfh, 0, 1))
                        ).astype(np.float32)
            del rbhpfv, rbhpfh, rblpfv, rblpfh, grblpfv, grblpfh
            del grbv_s, grbh_s

            row_pad = np.arange(cr0, hp - 8, 2)
            col_pad = np.arange(cc0, wp - 8, 2)
            for dir_, gw, gd in ((0, gradwt_v, gdiff_v), (1, gradwt_h, gdiff_h)):
                coeff[dir_, 0, cidx] = _block_reduce(
                    gw * deltgrb * deltgrb, row_pad, col_pad, nv, nh)
                coeff[dir_, 1, cidx] = _block_reduce(
                    gw * gd * deltgrb, row_pad, col_pad, nv, nh)
                coeff[dir_, 2, cidx] = _block_reduce(
                    gw * gd * gd, row_pad, col_pad, nv, nh)

        # per-block shifts + weights (the references' padded block arrays;
        # real tiles at [1..nv]×[1..nh], the rest zero)
        blockshifts = np.zeros((vblsz, hblsz, 2, 2), dtype=np.float32)
        blockwt = np.zeros((vblsz, hblsz), dtype=np.float32)
        blockdenom = np.zeros((2, 2), dtype=np.float64)
        blockave = np.zeros((2, 2), dtype=np.float64)
        blocksqave = np.zeros((2, 2), dtype=np.float64)
        for cidx in range(2):
            for dir_ in range(2):
                c2 = coeff[dir_, 2, cidx]
                valid = c2 > _EPS2
                shifts = np.full((nv, nh), 17.0, dtype=np.float32)
                np.divide(coeff[dir_, 1, cidx], c2, out=shifts, where=valid)
                blockshifts[1:nv + 1, 1:nh + 1, cidx, dir_] = shifts
                sel = np.abs(shifts) < 2.0
                blockdenom[dir_, cidx] = int(sel.sum())
                blockave[dir_, cidx] = float(shifts[sel].sum())
                blocksqave[dir_, cidx] = float(np.square(shifts[sel]).sum())
        # blockwt: the references overwrite it per (c, dir) — the surviving
        # value is the LAST write, c=1 (blue) / dir=1 (horizontal)
        c2b = coeff[1, 2, 1]
        wt = np.zeros((nv, nh), dtype=np.float32)
        np.divide(c2b, _EPS + coeff[1, 0, 1], out=wt, where=c2b > _EPS2)
        blockwt[1:nv + 1, 1:nh + 1] = wt

        if (blockdenom == 0).any():
            sys.stderr.write("warning: ca-correct blockdenom vanishes; "
                             "correction skipped this iteration onward.\n")
            ok = False
            break
        blockvar = blocksqave / blockdenom - np.square(blockave / blockdenom)

        # border blocks of the shift array (edge rows/cols ← 2 inside)
        blockshifts[1:vblsz - 1, 0] = blockshifts[1:vblsz - 1, 2]
        blockshifts[1:vblsz - 1, hblsz - 1] = blockshifts[1:vblsz - 1, hblsz - 3]
        blockshifts[0, :] = blockshifts[2, :]
        blockshifts[vblsz - 1, :] = blockshifts[vblsz - 3, :]

        # 3×3 median + variance gate + weighted polynomial LSQ fit
        polyord, numpar = 4, 16
        polymat = np.zeros((2, 2, 256), dtype=np.float64)
        shiftmat = np.zeros((2, 2, 16), dtype=np.float64)
        numblox = [0, 0]
        idx4 = np.arange(4, dtype=np.float64)
        for vb in range(1, vblsz - 1):
            for hb in range(1, hblsz - 1):
                for cidx in range(2):
                    bstemp = [
                        float(np.median(
                            blockshifts[vb - 1:vb + 2, hb - 1:hb + 2,
                                        cidx, dir_]))
                        for dir_ in range(2)
                    ]
                    if (bstemp[0] ** 2 > _CAAUTOSTRENGTH * blockvar[0, cidx]
                            or bstemp[1] ** 2
                            > _CAAUTOSTRENGTH * blockvar[1, cidx]):
                        continue
                    numblox[cidx] += 1
                    bw = float(blockwt[vb, hb])
                    mono = np.outer(float(vb) ** idx4,
                                    float(hb) ** idx4).ravel()  # [4i+j]=v^i h^j
                    quad = bw * np.outer(mono, mono).ravel()
                    polymat[cidx, 0] += quad
                    polymat[cidx, 1] += quad
                    shiftmat[cidx, 0] += bw * bstemp[0] * mono
                    shiftmat[cidx, 1] += bw * bstemp[1] * mono

        numblox[1] = min(numblox[0], numblox[1])
        if numblox[1] < 32:
            polyord, numpar = 2, 4  # flat-buffer reinterpretation, as reference
            if numblox[1] < 10:
                sys.stderr.write(f"warning: ca-correct too few usable blocks "
                                 f"({numblox[1]}); correction skipped.\n")
                ok = False
                break
        fitparams = np.zeros((2, 2, 16), dtype=np.float64)
        for cidx in range(2):
            for dir_ in range(2):
                sol = _lin_eq_solve(numpar, polymat[cidx, dir_].tolist(),
                                    shiftmat[cidx, dir_].tolist())
                if sol is None:
                    sys.stderr.write("warning: ca-correct fit singular; "
                                     "correction skipped.\n")
                    ok = False
                if ok:
                    fitparams[cidx, dir_, :numpar] = sol
        if not ok:
            break

        # ---- correction pass: per tile, piecewise-constant fitted shift ----
        new_work = work.copy()
        for vb in range(1, nv + 1):
            ptop = _STRIDE * (vb - 1)
            rr1 = min(_TS, hp - ptop)
            for hb in range(1, nh + 1):
                pleft = _STRIDE * (hb - 1)
                cc1 = min(_TS, wp - pleft)
                # evaluate the fitted shift polynomial at this block
                lshift = np.zeros((2, 2), dtype=np.float64)
                powv = 1.0
                for i in range(polyord):
                    powh = powv
                    for j in range(polyord):
                        lshift += powh * fitparams[:, :, polyord * i + j]
                        powh *= hb
                    powv *= vb
                lshift = np.clip(lshift, -_BSLIM, _BSLIM)

                for c, (pr, qc) in parities.items():
                    cidx = c >> 1
                    sv = float(lshift[cidx, 0])
                    sh = float(lshift[cidx, 1])
                    svf, svc = int(np.floor(sv)), int(np.ceil(sv))
                    if sv < 0.0:
                        svf, svc = svc, svf
                    svfrac = np.float32(abs(sv - svf))
                    shf, shc = int(np.floor(sh)), int(np.ceil(sh))
                    if sh < 0.0:
                        shf, shc = shc, shf
                    shfrac = np.float32(abs(sh - shf))
                    dir0 = 2 if sv > 0.0 else -2  # GRBdir (row, col)
                    dir1 = 2 if sh > 0.0 else -2

                    # grbdiff/gshift lattice: tile rows/cols [4, rr1-4)
                    gr_lo, gr_hi = 4 + pr, rr1 - 4
                    gc_lo, gc_hi = 4 + qc, cc1 - 4
                    if gr_hi <= gr_lo or gc_hi <= gc_lo:
                        continue

                    def t(arr: np.ndarray, dy: int, dx: int, ptop=ptop,
                          pleft=pleft, gr_lo=gr_lo, gr_hi=gr_hi,
                          gc_lo=gc_lo, gc_hi=gc_hi) -> np.ndarray:
                        return arr[ptop + gr_lo + dy: ptop + gr_hi + dy: 2,
                                   pleft + gc_lo + dx: pleft + gc_hi + dx: 2]

                    gff = t(gpad, svf, shf)
                    gfc = t(gpad, svf, shc)
                    gcf = t(gpad, svc, shf)
                    gcc_ = t(gpad, svc, shc)
                    ginthfloor = shfrac * gfc + (np.float32(1) - shfrac) * gff
                    ginthceil = shfrac * gcc_ + (np.float32(1) - shfrac) * gcf
                    gint = svfrac * ginthceil + (np.float32(1) - svfrac) * ginthfloor
                    grbdiff = gint - t(pad, 0, 0)
                    gshift = gint

                    # correction lattice: tile rows/cols [8, rr1-8); offset
                    # (2, 2) site-steps into the grbdiff lattice
                    kr_lo, kr_hi = 8 + pr, rr1 - 8
                    kc_lo, kc_hi = 8 + qc, cc1 - 8
                    if kr_hi <= kr_lo or kc_hi <= kc_lo:
                        continue
                    n_r = len(range(kr_lo, kr_hi, 2))
                    n_c = len(range(kc_lo, kc_hi, 2))

                    def k(arr: np.ndarray, di: int, dj: int,
                          n_r=n_r, n_c=n_c) -> np.ndarray:
                        # grbdiff-lattice window at site-step offset (di, dj)
                        return arr[2 + di: 2 + di + n_r, 2 + dj: 2 + dj + n_c]

                    def p(arr: np.ndarray, dy: int, dx: int, ptop=ptop,
                          pleft=pleft, kr_lo=kr_lo, kr_hi=kr_hi,
                          kc_lo=kc_lo, kc_hi=kc_hi) -> np.ndarray:
                        return arr[ptop + kr_lo + dy: ptop + kr_hi + dy: 2,
                                   pleft + kc_lo + dx: pleft + kc_hi + dx: 2]

                    sh2 = shfrac / np.float32(2)
                    sv2 = svfrac / np.float32(2)
                    dj = -dir1 // 2   # column site-step of (cc − GRBdir1)
                    di = -dir0 // 2   # row site-step of (rr − GRBdir0)
                    g_site = p(gpad, 0, 0)
                    c_site = p(pad, 0, 0)
                    grbdiffold = g_site - c_site
                    hfloor = sh2 * k(grbdiff, 0, dj) \
                        + (np.float32(1) - sh2) * k(grbdiff, 0, 0)
                    hceil = sh2 * k(grbdiff, di, dj) \
                        + (np.float32(1) - sh2) * k(grbdiff, di, 0)
                    grbdiffint = sv2 * hceil + (np.float32(1) - sv2) * hfloor
                    rbint = g_site - grbdiffint
                    cond = np.abs(rbint - c_site) < 0.25 * (rbint + c_site)
                    # gradient-weighted fallback where the direct estimate
                    # deviates too far from the current value
                    p0 = 1.0 / (_EPS + np.abs(g_site - k(gshift, 0, 0)))
                    p1 = 1.0 / (_EPS + np.abs(g_site - k(gshift, 0, dj)))
                    p2 = 1.0 / (_EPS + np.abs(g_site - k(gshift, di, 0)))
                    p3 = 1.0 / (_EPS + np.abs(g_site - k(gshift, di, dj)))
                    alt = ((p0 * k(grbdiff, 0, 0) + p1 * k(grbdiff, 0, dj)
                            + p2 * k(grbdiff, di, 0) + p3 * k(grbdiff, di, dj))
                           / (p0 + p1 + p2 + p3)).astype(np.float32)
                    gint_final = np.where(cond, grbdiffint, alt)
                    newc = np.where(np.abs(grbdiffold) > np.abs(gint_final),
                                    g_site - gint_final, c_site)
                    overshoot = grbdiffold * gint_final < 0
                    newc = np.where(overshoot,
                                    g_site - np.float32(0.5)
                                    * (grbdiffold + gint_final), newc)
                    # scatter into the owned (image-domain) region, ≥0 (RT)
                    img_r = ptop + kr_lo - 8
                    img_c = pleft + kc_lo - 8
                    new_work[img_r: ptop + kr_hi - 8: 2,
                             img_c: pleft + kc_hi - 8: 2] = \
                        np.maximum(newc, np.float32(0)).astype(np.float32)
        work = new_work

    if avoid_shift and ok:
        from scipy.ndimage import gaussian_filter
        for c, (pr, qc) in parities.items():
            old = oldraw[c]
            new = work[pr::2, qc::2]
            factor = np.ones_like(old)
            good = (old > _AVOIDSHIFT_GUARD) & (new > _AVOIDSHIFT_GUARD)
            np.divide(old, new, out=factor, where=good)
            factor = np.clip(factor, 0.5, 2.0, out=factor)
            factor = gaussian_filter(factor, sigma=_AVOIDSHIFT_SIGMA,
                                     mode="nearest").astype(np.float32)
            # dt apply range: rows [2, h-2), cols [·, w-2)
            rows = np.arange(pr, h, 2)
            cols = np.arange(qc, w, 2)
            rsel = (rows >= 2) & (rows < h - 2)
            csel = cols < w - 2
            sub = work[pr::2, qc::2]
            sub[np.ix_(rsel, csel)] *= factor[np.ix_(rsel, csel)]

    out = cfa.copy()
    for _c, (pr, qc) in parities.items():
        out[pr::2, qc::2] = work[pr::2, qc::2] * np.float32(scale)
    return out
