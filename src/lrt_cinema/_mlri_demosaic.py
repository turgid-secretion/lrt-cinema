"""Clean-room Minimized-Laplacian Residual Interpolation (MLRI) demosaic.

WHAT THIS IS
------------
A vectorized, pure-NumPy Bayer demosaicker in the **residual-interpolation** (RI /
MLRI) family. It demosaics in a *residual* domain rather than the color-difference
domain its sibling `_rcd_demosaic.py` uses, and that single change is the whole
point: the residual `R - tentative` is much smoother (less structured) than the
color difference `R - G`, so interpolating it injects far less chroma-aliasing
(false colour / zipper) — exactly the weakness the project's RCD-family demosaic
measures on the bench.

Pipeline (RGGB phase; other phases via flips — see `mlri_demosaic`):

  1. **Green**: directional Hamilton-Adams (the *same* sharp green this project's
     RCD uses — it ties the SOTA resolution leader on MTF50P in our battery, so we
     keep it verbatim and spend the new effort on R/B where the chroma weakness is).
     The horizontal-vs-vertical decision in the RI/MLRI family is made *here*, for
     green (via the HA grad_h/grad_v classifier); R/B below is non-directional.
  2. **R and B** (after green is complete), each reconstructed once:
       a. **Tentative** `tilde_R`: estimate R from the green guide by a local linear
          model `R ~= a*G + b` whose slope `a` is fitted by the **minimized-
          Laplacian** objective (MLRI's contribution) — minimize the Laplacian
          energy of the residual over observed R pixels in a 2-D guided-filter
          window, then box-average the per-window models (the guided-filter mean
          step). RI fits `a` by least-squares on the *values*; MLRI fits it on the
          *Laplacians*, which yields a smoother residual.
       b. **Residual** `res = R - tilde_R` at the observed R sites only.
       c. **Interpolate** the residual (it is smooth) by a 2-D bilinear fill over the
          R quincunx, then **add back**: `R = tilde_R + interp(res)`.
     The window is 2-D, not 1-D-directional: R sites occupy only even rows, so a
     1-D horizontal fit/fill is empty on the odd rows and collapses the estimate
     there. R/B is deliberately the non-directional "easy" step (RI/MLRI: a couple
     of iterations suffice for R/B); a *second*, adaptive H/V decision on R/B is an
     ARI feature, out of scope for MLRI.

Known CFA samples are never overwritten. Output is finite, **non-negative**, and
the upper range is deliberately NOT capped at 1.0 (headroom-preserving — the
project is highlight-sensitive; see `highlight_recovery.py`). Matches
`rcd_demosaic`'s interface, dtype policy, and even-dimension contract exactly.

MEASURED STANDING (this repo's `tools/demosaic_bench`, Kodak-24 sRGB + charts) — a
**correct but simplified MLRI**, honestly scoped. This implementation **ties** our
RCD on natural-image CPSNR (37.28 vs 37.35) and S-CIELAB, is marginally better on
zipper (11.71 vs 12.13), and HOLDS the green's resolution (MTF50P 0.3114 vs 0.3116)
— but it does **not** beat RCD overall and does not reach the classical SOTA band
(§6 lists MLRI at ~40.86 Kodak in this very sRGB 10-px protocol; we are ~3.5 dB
short, i.e. simplified, NOT at the method's ceiling). It is notably WORSE on
*neutral* false colour (zone-plate chroma 37.1 vs RCD's 24.5, even above bilinear's
36.3).

This loss is a property of **this variant**, not of residual interpolation, and the
distinction matters (the missing lever is named so it is not mis-attributed):

  * In the same battery, `Menon2007` scores **15.15** on neutral false colour — far
    BELOW RCD's 24.5 — so 24.5 is emphatically NOT a floor and a demosaicer CAN beat
    RCD here. Menon wins it with a *directional a-posteriori chroma decision*; this
    MLRI has none.
  * Our only deviation from RCD is the tentative: RCD uses the trivial `a=1, b=0`
    (plain colour-difference R-G), we use the fitted `a*G+b`. On *neutral* aliased
    content the correct slope is exactly 1, but the unconstrained fit swings `a!=1`
    in the aliasing region and AMPLIFIES chroma (hence worse than even bilinear).
    With no directional decision to suppress that swing, the variant cannot recover
    it (verified: forcing a=1,b=0 reproduces RCD's 24.5 exactly — the delta is 100%
    in the fit; checked across RI value-slope and MLRI Laplacian-slope, guided-filter
    radii 1..5, eps 1e-9..1e-1; none recover it).

So the honest summary is: a *non-directional, RCD-green-reuse* MLRI ties RCD and
loses neutral false colour; a directional chroma decision (cf. Menon, or the
adaptive ARI successor) is what would beat RCD there — out of scope for plain MLRI.
RI/MLRI's published strength is *chromatic* natural content (McMaster) and rebuilding
R/B edges through a sharp green guide; reusing RCD's already-SOTA green leaves MLRI
no green advantage to exploit, and Kodak's low-chroma content does not surface the
chroma strength. Kept as a correct, documented residual-domain alternative; NOT
promoted to the default demosaic (the pipeline default is unchanged).

CLEAN-ROOM PROVENANCE (license-sensitive — read before editing)
---------------------------------------------------------------
Implemented CLEAN-ROOM from the **papers / open-access algorithm descriptions
only**. The authors' MATLAB reference (Tanaka/Kiku/Monno, "Guided Upsampling and
Residual Interpolation", MathWorks File Exchange #47268) was **NOT opened, read, or
copied**, nor was any GPL demosaic source (RawTherapee, darktable, RCD, AMaZE). The
equations below are taken from these primary / permissively-licensed sources:

  [Kiku2013]  D. Kiku, Y. Monno, M. Tanaka, M. Okutomi, "Residual Interpolation
              for Color Image Demosaicking," Proc. IEEE ICIP 2013. — the RI scheme:
              directional green, guided-filter *tentative* estimate, residual =
              observed - tentative, interpolate residual, add back.
  [Kiku2014]  D. Kiku, Y. Monno, M. Tanaka, M. Okutomi, "Minimized-Laplacian
              Residual Interpolation for Color Image Demosaicking," Proc. SPIE/IS&T
              EI 9023, 2014, DOI 10.1117/12.2038425. — MLRI's contribution: fit the
              tentative model by minimizing the **Laplacian energy of the residual**
              instead of the residual itself (a better, smoother guide than RI's
              guided filter).
  [Monno2017] Y. Monno, D. Kiku, M. Tanaka, M. Okutomi, "Adaptive Residual
              Interpolation for Color and Multispectral Image Demosaicking,"
              *Sensors* 17(12):2787, 2017 (open access, **CC BY 4.0**). — its §2/§3
              re-derive RI and MLRI in full (cost functions, the masked guided-
              filter coefficients, the directional structure, the residual step);
              the detailed equations used here were read from THIS open-access
              review, since the ICIP/SPIE primaries would not fetch. (We implement
              MLRI, *not* the adaptive ARI of this paper — none of ARI's per-pixel
              adaptive iteration selection / 4-way weighting is reproduced.)
  [He2013]    K. He, J. Sun, X. Tang, "Guided Image Filtering," IEEE TPAMI
              35(6):1397, 2013 (orig. ECCV 2010). — the guided-filter closed form
              `a_k = (mean(I p) - mean(I) mean(p)) / (var(I) + eps)`,
              `b_k = mean(p) - a_k mean(I)`, output `q = mean(a) I + mean(b)` (box-
              filter averaging of a, b over overlapping windows). Textbook; not GPL.
  [Buades2011] A. Buades, B. Coll, J.-M. Morel, C. Sbert, "Self-Similarity Driven
              Color Demosaicking," IPOL 2011. — §1.1 states the Hamilton-Adams
              directional green equations verbatim (open / non-GPL); the green here
              is that same formulation (shared with our RCD by design).

THE LAPLACIAN KERNEL (re-derived, not transcribed). MLRI minimizes the energy of
the residual's Laplacian. The residual lives on the *same-color* sublattice (R is
known every 2nd pixel), so the relevant second difference is taken at distance-2
spacing. The directional Laplacian operators are therefore
  horizontal:  [1, 0, -2, 0, 1]   (1x5)
  vertical  :  its transpose       (5x1)
each summing to **0** (a Laplacian of a constant is 0 — weights MUST sum to zero)
and symmetric. (A 5x5 "MLRI kernel" floating around secondhand transcriptions sums
to -1 and is asymmetric — that is a corrupted copy and is NOT used here; the
sum-to-zero / symmetry properties are asserted at import to guard against exactly
that error.)

FINITENESS. Unlike the division-free RCD, the MLRI slope `a = sum(L_R L_G) /
sum(L_G^2)` and the guided-filter means divide by data, so finiteness is now
*earned*, not structural: every denominator carries an explicit regularizer
``_EPS`` (and masked means divide by the mask's own box-filter, never a bare
count). On a flat patch every Laplacian is 0 => `a -> 0`, `b -> mean`, and the
tentative equals the constant, so flat patches still reconstruct bit-exactly.
"""

from __future__ import annotations

import numpy as np

# Guided-filter box half-width (2-D). Larger => smoother tentative (more chroma-
# alias suppression) but softer fit; 4 tracks the RI/MLRI papers' window scale.
_GF_RADIUS = 4
# Reflect-pad width. The HA green stencil and the distance-2 Laplacian both reach
# +/-2; the guided-filter box reaches +/-_GF_RADIUS. Pad to cover the widest reach,
# rounded UP to an EVEN width — load-bearing: ``np.pad(mode="reflect")`` with an
# even margin preserves the 2x2 Bayer phase (an odd margin flips row/col parity and
# silently corrupts the mosaic interpretation).
_PAD = _GF_RADIUS + (_GF_RADIUS % 2)  # even, >= _GF_RADIUS (and >= 2)

_VALID_PATTERNS = ("RGGB", "BGGR", "GRBG", "GBRG")

# Guided-filter / MLRI regularizer. Small relative to [0,1] signal energy so it is
# a no-op on real structure but pins 0/0 to 0 on flat/smooth regions (where every
# Laplacian and variance vanishes). Load-bearing for the finite/flat-patch gates.
_EPS = 1e-9

# Directional second-difference (Laplacian) on the SAME-COLOR sublattice (spacing
# 2). Sum-to-zero + symmetric by construction; asserted below.
_LAP_1D = np.array([1.0, 0.0, -2.0, 0.0, 1.0])
assert _LAP_1D.sum() == 0.0, "MLRI Laplacian must sum to zero"
assert np.array_equal(_LAP_1D, _LAP_1D[::-1]), "MLRI Laplacian must be symmetric"


def _shift(a: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """Shift a 2-D array by (dy, dx) with zero fill at the exposed edge.

    Positive ``dy`` pulls the neighbor from *below* (row + dy), positive ``dx`` from
    the *right* (col + dx). Vacated cells are 0.0 (paired with a parallel count/mask
    shift wherever a masked average is taken, so "no neighbor" contributes nothing).
    """
    out = np.zeros_like(a)
    ys_src = slice(max(dy, 0), a.shape[0] + min(dy, 0))
    ys_dst = slice(max(-dy, 0), a.shape[0] + min(-dy, 0))
    xs_src = slice(max(dx, 0), a.shape[1] + min(dx, 0))
    xs_dst = slice(max(-dx, 0), a.shape[1] + min(-dx, 0))
    out[ys_dst, xs_dst] = a[ys_src, xs_src]
    return out


def _box1d(a: np.ndarray, radius: int, axis: int) -> np.ndarray:
    """1-D box (moving-sum) over the 2*radius+1 window along ``axis``.

    Returns the SUM, not the mean — callers form means as ``box(value)/box(mask)``
    so the normalization is exact at borders (the mask box counts only real
    contributors). O(N) via a cumulative-sum difference, edge-extended by clamping.
    """
    a = np.moveaxis(a, axis, -1)
    n = a.shape[-1]
    p = np.zeros(a.shape[:-1] + (n + 1,), dtype=np.float64)
    np.cumsum(a, axis=-1, out=p[..., 1:])
    hi = np.minimum(np.arange(n) + radius + 1, n)
    lo = np.maximum(np.arange(n) - radius, 0)
    out = p[..., hi] - p[..., lo]
    return np.moveaxis(out, -1, axis)


def _box2d(a: np.ndarray, radius: int) -> np.ndarray:
    """2-D box (moving-sum) over a (2*radius+1)^2 window — 1-D box on each axis."""
    return _box1d(_box1d(a, radius, 0), radius, 1)


def _lap2d(a: np.ndarray) -> np.ndarray:
    """2-D same-color second difference (the MLRI Laplacian) at distance-2 spacing.

    Separable sum of the verified ``_LAP_1D`` ([1,0,-2,0,1]) horizontally and
    vertically: center weight -4, +1 at each of the four +/-2 arms. Sum-to-zero and
    symmetric => zero on any constant (so a flat patch yields slope 0 -> exact).
    """
    return (
        _shift(a, 2, 0) + _shift(a, -2, 0) + _shift(a, 0, 2) + _shift(a, 0, -2)
        - 4.0 * a
    )


def _tentative_mlri(
    guide: np.ndarray, inp: np.ndarray, mask: np.ndarray, radius: int
) -> np.ndarray:
    """MLRI tentative estimate of ``inp`` from the ``guide`` over observed ``mask``.

    The guided-filter linear model ``inp ~= a*guide + b`` with the slope ``a`` fitted
    by MLRI's **minimized-Laplacian** objective [Kiku2014] and the intercept ``b``
    on values [He2013]:

      a = boxSum(Lap(g)*Lap(p)*m_lap) / (boxSum(Lap(g)^2 * m_lap) + eps)    (per win)
      b = mean(p) - a*mean(g)        (masked window means)
      tentative = boxAvg(a)*guide + boxAvg(b)                               (GF mean)

    All windows are **2-D** (radius x radius box) — this is the load-bearing fix:
    a 1-D directional box is empty on the rows/cols that hold no same-color sample
    (R sites live only on even rows), which collapses the tentative there. ``m_lap``
    is the Laplacian-validity mask (center + all four +/-2 arms observed), true at
    interior R/B sites. ``b`` does NOT enter the Laplacian objective (Lap(const)=0),
    so on a flat patch a->0, b->mean, tentative->const, residual->0 (exact). Every
    denominator carries ``_EPS`` so 0/0 on smooth regions resolves to 0, keeping the
    output finite (the division MLRI reintroduces vs the division-free RCD).
    """
    m = mask.astype(np.float64)
    lap_g = _lap2d(guide)
    lap_p = _lap2d(inp)
    # Laplacian valid only where the center and all four distance-2 arms are observed
    # (same-colour lattice). Built from the mask so a partial stencil never leaks.
    m_lap = (
        m * _shift(m, 2, 0) * _shift(m, -2, 0) * _shift(m, 0, 2) * _shift(m, 0, -2)
    )
    num = _box2d(lap_g * lap_p * m_lap, radius)
    den = _box2d(lap_g * lap_g * m_lap, radius)
    a = num / (den + _EPS)

    cnt = _box2d(m, radius)
    cnt_safe = np.maximum(cnt, 1.0)
    mean_g = _box2d(guide * m, radius) / cnt_safe
    mean_p = _box2d(inp * m, radius) / cnt_safe
    b = mean_p - a * mean_g
    empty = cnt < 0.5
    a = np.where(empty, 0.0, a)
    b = np.where(empty, 0.0, b)

    # Guided-filter output step: average the per-window linear models (2-D box) and
    # apply. Normalize a,b by the true 2-D window area (clamped at the borders).
    h, w = a.shape
    len_r = (np.minimum(np.arange(h) + radius + 1, h)
             - np.maximum(np.arange(h) - radius, 0))[:, None]
    len_c = (np.minimum(np.arange(w) + radius + 1, w)
             - np.maximum(np.arange(w) - radius, 0))[None, :]
    area = len_r * len_c
    a_bar = _box2d(a, radius) / area
    b_bar = _box2d(b, radius) / area
    return a_bar * guide + b_bar


def _bilinear_fill_quincunx(res: np.ndarray, known: np.ndarray) -> np.ndarray:
    """Bilinearly fill a plane known only on the R (or B) quincunx [Malvar2004].

    The residual is *smooth* (that is the whole RI/MLRI premise), so it is
    interpolated with an ordinary 2-D bilinear fill — the directionality of MLRI
    lives in the **tentative**, not here. Two passes per [Malvar2004] eq. (1): first
    the opposite-corner sites (e.g. the B site for an R residual) from their 4
    *diagonal* known neighbors, then the green sites from their 4 *cardinal*
    neighbors (all known after pass 1). A purely 1-D directional fill cannot work:
    on an RGGB grid the R sites occupy only even rows, so a horizontal pass leaves
    every odd row empty — the fill MUST be 2-D. Known sites are preserved exactly;
    masked shifts make absent neighbors contribute nothing (interior: always 4).
    """
    d = res.astype(np.float64, copy=True)
    k = known
    vals = np.where(k, d, 0.0)
    cnt = k.astype(np.float64)
    diag_sum = (
        _shift(vals, 1, 1) + _shift(vals, 1, -1)
        + _shift(vals, -1, 1) + _shift(vals, -1, -1)
    )
    diag_cnt = (
        _shift(cnt, 1, 1) + _shift(cnt, 1, -1)
        + _shift(cnt, -1, 1) + _shift(cnt, -1, -1)
    )
    diag_known = (diag_cnt > 0) & (~k)
    d = np.where(diag_known, diag_sum / np.maximum(diag_cnt, 1.0), d)

    filled = k | diag_known
    vals = np.where(filled, d, 0.0)
    cnt = filled.astype(np.float64)
    card_sum = (
        _shift(vals, 0, 1) + _shift(vals, 0, -1)
        + _shift(vals, 1, 0) + _shift(vals, -1, 0)
    )
    card_cnt = (
        _shift(cnt, 0, 1) + _shift(cnt, 0, -1)
        + _shift(cnt, 1, 0) + _shift(cnt, -1, 0)
    )
    card_known = (card_cnt > 0) & (~filled)
    d = np.where(card_known, card_sum / np.maximum(card_cnt, 1.0), d)
    return d


def _reconstruct_channel(
    chan: np.ndarray, green: np.ndarray, site: np.ndarray, radius: int
) -> np.ndarray:
    """MLRI estimate of a full R (or B) plane (non-directional — the easy step).

    ``chan`` holds the observed channel values at ``site`` (else 0); ``green`` is the
    complete green plane. Forms the minimized-Laplacian **tentative** (guided by
    green), takes the smooth **residual** ``chan - tentative`` at observed sites,
    fills it by a 2-D bilinear fill over the quincunx, and adds it back. Observed
    samples are kept exact.

    R/B is deliberately NOT direction-split: the H/V decision in the RI/MLRI family
    is made for *green* (and our green — shared Hamilton-Adams — already makes it
    via grad_h/grad_v); a second directional decision on R/B is an ARI feature, out
    of scope here. This isolates the only delta from the sibling RCD: RCD uses the
    trivial tentative ``G`` (slope 1, offset 0) and interpolates ``R-G``; MLRI uses
    the fitted ``a*G+b`` and interpolates the smaller, smoother ``R-(a*G+b)`` — the
    measurable chroma-aliasing win.
    """
    tent = _tentative_mlri(green, chan, site, radius)
    res = np.where(site, chan - tent, 0.0)
    res_full = _bilinear_fill_quincunx(res, site)
    out = tent + res_full
    return np.where(site, chan, out)


def _mlri_rggb(cfa: np.ndarray) -> np.ndarray:
    """Core MLRI demosaic for the **RGGB** phase. Input/output already padded.

    ``cfa`` is a reflect-padded 2-D mosaic whose interior top-left pixel (at
    ``[_PAD, _PAD]``) is RED (RGGB). Returns padded (H, W, 3).
    """
    f = cfa.astype(np.float64, copy=False)
    h, w = f.shape

    yy, xx = np.indices((h, w))
    r_site = (yy % 2 == 0) & (xx % 2 == 0)
    b_site = (yy % 2 == 1) & (xx % 2 == 1)
    g_site = ~(r_site | b_site)

    # ------------------------------------------------------------------ green
    # Directional Hamilton-Adams green at every R/B site [Buades2011 §1.1] — the
    # SAME formulation our RCD uses (it ties the resolution leader on MTF50P, so we
    # reuse it and spend the new work on R/B). Clean-room from [Buades2011], not
    # imported from `_rcd_demosaic` (that module stays untouched).
    c = f
    g_l, g_r = _shift(f, 0, 1), _shift(f, 0, -1)
    g_u, g_d = _shift(f, 1, 0), _shift(f, -1, 0)
    c_l2, c_r2 = _shift(f, 0, 2), _shift(f, 0, -2)
    c_u2, c_d2 = _shift(f, 2, 0), _shift(f, -2, 0)

    lap_h = 2.0 * c - c_l2 - c_r2
    lap_v = 2.0 * c - c_u2 - c_d2
    grad_h = np.abs(g_l - g_r) + np.abs(lap_h)
    grad_v = np.abs(g_u - g_d) + np.abs(lap_v)
    green_horiz = 0.5 * (g_l + g_r) + 0.25 * lap_h
    green_vert = 0.5 * (g_u + g_d) + 0.25 * lap_v
    green_avg = 0.25 * (g_u + g_d + g_l + g_r) + 0.125 * (lap_h + lap_v)
    green_est = np.where(
        grad_h > grad_v, green_vert,
        np.where(grad_h < grad_v, green_horiz, green_avg),
    )
    green = np.where(g_site, f, green_est)

    # ------------------------------------------------------------- red & blue
    # MLRI minimized-Laplacian residual interpolation, guided by the complete green
    # (non-directional — the easy step; the H/V decision was green's job above).
    r_obs = np.where(r_site, f, 0.0)
    b_obs = np.where(b_site, f, 0.0)
    red = _reconstruct_channel(r_obs, green, r_site, _GF_RADIUS)
    blue = _reconstruct_channel(b_obs, green, b_site, _GF_RADIUS)
    red = np.where(r_site, f, red)    # keep observed samples exact
    blue = np.where(b_site, f, blue)

    out = np.empty((h, w, 3), dtype=np.float64)
    out[..., 0] = red
    out[..., 1] = green
    out[..., 2] = blue
    return out


# Phase -> (flip_rows, flip_cols) that maps the pattern's top-left 2x2 onto RGGB.
# Flips relocate the real red photosite to (0,0); they preserve the H/V axes (so the
# directional green classifier stays valid) and need NO R<->B channel swap
# afterwards. (Transpose is intentionally NOT used: it swaps H<->V and would corrupt
# the green direction discrimination.)
_PHASE_FLIP: dict[str, tuple[bool, bool]] = {
    "RGGB": (False, False),
    "GRBG": (False, True),
    "GBRG": (True, False),
    "BGGR": (True, True),
}


def mlri_demosaic(cfa: np.ndarray, pattern: str) -> np.ndarray:
    """Demosaic a single-channel Bayer mosaic to full RGB (MLRI).

    Parameters
    ----------
    cfa
        2-D array ``(H, W)`` of a Bayer mosaic, values nominally in ``[0, 1]``. ``H``
        and ``W`` must be even (a Bayer mosaic always is; the phase-mapping flips
        only preserve the pattern on even dimensions).
    pattern
        The 2x2 CFA phase of ``cfa[0:2, 0:2]``: one of ``"RGGB"``, ``"BGGR"``,
        ``"GRBG"``, ``"GBRG"`` (row-major).

    Returns
    -------
    np.ndarray
        Float ``(H, W, 3)`` RGB (R, G, B order), finite and **non-negative** (the
        upper range is NOT capped at 1.0 — highlights pass through; see the module
        docstring). float32 in -> float32 out; otherwise float64.

    Notes
    -----
    The RGGB math is written once (`_mlri_rggb`); the other three phases are mapped
    onto it by row/column flips (`_PHASE_FLIP`) and flipped back, a single source of
    truth for the directional green + minimized-Laplacian residual R/B steps.
    """
    if pattern not in _VALID_PATTERNS:
        raise ValueError(
            f"pattern must be one of {_VALID_PATTERNS}, got {pattern!r}"
        )
    cfa = np.asarray(cfa)
    if cfa.ndim != 2:
        raise ValueError(f"cfa must be 2-D (H, W), got shape {cfa.shape}")
    if cfa.shape[0] % 2 or cfa.shape[1] % 2:
        raise ValueError(
            f"cfa dimensions must be even for Bayer phase mapping, got {cfa.shape}"
        )

    out_float32 = cfa.dtype == np.float32
    work = cfa.astype(np.float64, copy=False)

    flip_rows, flip_cols = _PHASE_FLIP[pattern]
    if flip_rows:
        work = work[::-1, :]
    if flip_cols:
        work = work[:, ::-1]

    padded = np.pad(work, _PAD, mode="reflect")
    rgb_padded = _mlri_rggb(padded)
    rgb = rgb_padded[_PAD:-_PAD, _PAD:-_PAD, :]

    if flip_cols:
        rgb = rgb[:, ::-1, :]
    if flip_rows:
        rgb = rgb[::-1, :, :]

    np.clip(rgb, 0.0, None, out=rgb)  # non-negative only (preserve highlights)
    return rgb.astype(np.float32) if out_float32 else rgb
