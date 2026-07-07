"""Clean-room AMaZE demosaic (Aliasing Minimization and Zipper Elimination).

ALGORITHM (E. Martinec, 2008-2010; the open-source canon's quality demosaic
for diagonal detail). Clean-room reimplementation from the algorithm
structure extracted off darktable's scalar `src/iop/demosaicing/amaze.cc`
during the 2026-06-12 port session (read-to-learn; no GPL code copied —
anti-drift rule 6; same discipline as the RCD port). Stages:

  1. Cardinal gradient weights (dirwts) + squared-gradient field.
  2. H/V G interpolation, two estimators per direction — Hamilton-Adams
     (neighbor + half Laplacian) and adaptive-ratio (colour-ratio scaled,
     used when the ratio is within `ARTHRESH` of 1) — combined with
     gradient-adaptive direction weights into H/V colour differences
     (vcd/hcd ≈ G−R|B), plus HA-only alternates (vcdalt/hcdalt).
  3. Variance selection between the two estimators (3-sample windowed
     variance) + saturation bounding (ULIM neighbor bounds near/above the
     clip point).
  4. H-vs-V direction weight `hvwt` at R/B sites from directional
     colour-difference variances AND interpolation-fluctuation variances
     (the more decisive of the two when they agree).
  5. Nyquist texture test: quincunx-Gaussian of (vcd−hcd)² against a
     Gaussian of the gradient field; majority-filtered; flagged areas get
     an area-statistics hvwt and, post-G, a curvature-based refinement.
  6. G at R/B sites: hvwt (or the diagonal-neighbour average when more
     decisive) blends vcd/hcd.
  7. Diagonal R/B-at-B/R interpolation (P/Q diagonals, adaptive-ratio or
     HA), diagonal-variance weight `pmwt`, saturation bounding → `rbint`.
  8. Where diagonal discrimination beats H/V (|0.5−pmwt| ≥ |0.5−hvwt|),
     re-derive G from `rbint` (cardinal ratios, bounded).
  9. Chrominance: G−R and G−B propagated to all sites — 4-diagonal
     weighted 1.325/−0.175/−0.075/−0.075 stencil at R/B sites, then a
     4-cardinal hvwt-weighted average at G sites.

INPUT CONTRACT: BALANCED Bayer mosaic in [0, 1] with sensor clip at
`clip_pt` (default 1.0 — the clip-to-common-white decode, slot 5a). AMaZE
assumes a single uniform clip point (darktable runs it after its
highlights module for the same reason). Output: (H, W, 3) float32 in
[0, 1]. NOTE the [0, 1] output clamp is DARKTABLE'S port addition, which
this port faithfully inherited — RawTherapee's original is floor-only
and runs at `clip_pt = 1/initialGain < 1` with real per-channel headroom
above clip_pt flowing through (CLAIMS "Cross-engine canon", 2026-07-07).

`amaze_demosaic_headroom` is the recovery-path entry: dt's RCD/LMMSE
normalize→demosaic→denormalize scaler convention applied to AMaZE, so
mosaic content above `clip_pt` (highlight-reconstruction output, or raw
per-channel headroom) SURVIVES the demosaic. The scaled call runs at
clip_pt/scale < 1 — exactly RawTherapee's fielded amaze regime. The
ported numerics are untouched; on input with no headroom it degenerates
to the direct call bit-exactly.

Validation: canonical anchor `dt_amaze_anchor_2026-06-12.json`
(dt-AMaZE diagbars falsecolor 9.73 vs dt-RCD 20.6); pressure suite arms;
tests/test_amaze.py contracts.
"""

from __future__ import annotations

import numpy as np

# Tolerances (divide-by-zero guards).
_EPS = np.float32(1e-5)
_EPSSQ = np.float32(1e-10)
# Adaptive-ratio threshold: use the colour-ratio estimator only while the
# ratio is within this distance of 1 (i.e. locally smooth chroma).
ARTHRESH = np.float32(0.75)
# Nyquist texture-test threshold (folded into the gradient kernel).
NYQTHRESH = np.float32(0.5)
# Gaussian on 5x5 quincunx, sigma 1.2 (centre, diag1, card2, diag2).
_GAUSSODD = np.array(
    [0.14659727707323927, 0.103592713382435, 0.0732036125103057,
     0.0365543548389495], dtype=np.float32)
# Gaussian on 5x5 (pre-multiplied by NYQTHRESH), ring-ordered:
# centre, card1, diag1, card2, knight, diag2.
_GAUSSGRAD = NYQTHRESH * np.array(
    [0.07384411893421103, 0.06207511968171489, 0.0521818194747806,
     0.03687419286733595, 0.03099732204057846, 0.018413194161458882],
    dtype=np.float32)
# Gaussian on 5x5 alt quincunx, sigma 1.5 (card1 ring, knight ring).
_GAUSSEVEN = np.array(
    [0.13719494435797422, 0.05640252782101291], dtype=np.float32)
# Gaussian on quincunx grid (Nyquist curvature refinement).
_GQUINC = np.array(
    [0.169917, 0.108947, 0.069855, 0.0287182], dtype=np.float32)

_PAD = 18  # ≥16 border the algorithm needs; +2 slack for the widest stencil

_VALID_PATTERNS = ("RGGB", "BGGR", "GRBG", "GBRG")
# Flips that map each phase onto RGGB (rows, cols) — same convention as
# the RCD port.
_PHASE_FLIP = {
    "RGGB": (False, False), "BGGR": (True, True),
    "GRBG": (False, True), "GBRG": (True, False),
}


def _sh(a: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """View of padded array `a` shifted by (dy, dx) for the inner grid.

    `a` has shape (H+2P, W+2P); the result is the (H, W)-aligned window
    centred at (P+dy, P+dx). Pure view — no copy."""
    p = _PAD
    h = a.shape[0] - 2 * p
    w = a.shape[1] - 2 * p
    return a[p + dy: p + dy + h, p + dx: p + dx + w]


def _ulim(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    """Bound x to the closed interval spanned by y and z (order-free)."""
    lo = np.minimum(y, z)
    hi = np.maximum(y, z)
    return np.clip(x, lo, hi)


def _pad(a: np.ndarray) -> np.ndarray:
    return np.pad(a, _PAD, mode="reflect").astype(np.float32)


def _backend() -> str:
    """Resolve the execution backend: 'numba' (production; bit-exact twin,
    ~50x faster) unless numba is unavailable or LRT_CINEMA_AMAZE=numpy."""
    import os

    choice = os.environ.get("LRT_CINEMA_AMAZE", "auto")
    if choice == "numpy":
        return "numpy"
    try:
        from . import _amaze_numba
        if _amaze_numba.NUMBA_OK:
            return "numba"
    except Exception:
        pass
    if choice == "numba":
        raise RuntimeError("LRT_CINEMA_AMAZE=numba but numba is unavailable")
    return "numpy"


def amaze_demosaic(cfa: np.ndarray, pattern: str,
                   clip_pt: float = 1.0) -> np.ndarray:
    """AMaZE Bayer demosaic. `cfa` (H, W) float in [0, ~1] (balanced,
    clip-mode conditioned); `pattern` the 2x2 phase. Returns (H, W, 3)
    float32 in [0, 1]."""
    if pattern not in _VALID_PATTERNS:
        raise ValueError(f"pattern must be one of {_VALID_PATTERNS}, got {pattern!r}")
    cfa = np.asarray(cfa)
    if cfa.ndim != 2:
        raise ValueError(f"cfa must be 2-D (H, W), got shape {cfa.shape}")
    if cfa.shape[0] % 2 or cfa.shape[1] % 2:
        raise ValueError(f"cfa dimensions must be even, got {cfa.shape}")

    flip_r, flip_c = _PHASE_FLIP[pattern]
    work = cfa.astype(np.float32, copy=False)
    if flip_r:
        work = work[::-1, :]
    if flip_c:
        work = work[:, ::-1]

    if _backend() == "numba":
        from ._amaze_numba import _amaze_rggb_fast
        rgb = _amaze_rggb_fast(np.ascontiguousarray(work), np.float32(clip_pt))
    else:
        rgb = _amaze_rggb(work, np.float32(clip_pt))

    if flip_c:
        rgb = rgb[:, ::-1, :]
    if flip_r:
        rgb = rgb[::-1, :, :]
    return np.ascontiguousarray(rgb)


def amaze_demosaic_headroom(cfa: np.ndarray, pattern: str,
                            clip_pt: float = 1.0,
                            scale: float | None = None) -> np.ndarray:
    """AMaZE for mosaics carrying content ABOVE `clip_pt` (reconstruction
    output or per-channel headroom): normalize by `scale` → demosaic at
    `clip_pt/scale` → denormalize. dt's RCD/LMMSE scaler convention
    (rcd.c `scaler`/`revscaler`); the scaled regime (clip_pt < 1, data
    ≤ 1) is RT's amaze as-fielded (`clip_pt = 1/initialGain`), so the
    [0, 1] port clamp lands above all data and never engages.

    `scale=None` → max(cfa.max(), clip_pt). When scale ≤ clip_pt (no
    content above the clip) this IS `amaze_demosaic`, bit-exactly.
    Returns (H, W, 3) float32 in [0, scale]."""
    cfa = np.asarray(cfa)
    if scale is None:
        scale = max(float(cfa.max()), float(clip_pt))
    scale = float(scale)
    if scale <= float(clip_pt):
        return amaze_demosaic(cfa, pattern, clip_pt=float(clip_pt))
    scaled = (cfa.astype(np.float32, copy=False) / np.float32(scale))
    rgb = amaze_demosaic(scaled, pattern, clip_pt=float(clip_pt) / scale)
    return rgb * np.float32(scale)


def _amaze_rggb(cfa: np.ndarray, clip_pt: np.float32) -> np.ndarray:  # noqa: PLR0915
    """Core on an RGGB mosaic. R at (even,even), B at (odd,odd), G else."""
    h, w = cfa.shape
    clip_pt8 = np.float32(0.8) * clip_pt
    c = _pad(cfa)                     # padded mosaic

    yy, xx = np.mgrid[0:h, 0:w]
    g_site = ((yy + xx) % 2) == 1     # G checkerboard (RGGB)
    rb_site = ~g_site                 # R/B checkerboard
    r_site = (yy % 2 == 0) & (xx % 2 == 0)
    b_site = (yy % 2 == 1) & (xx % 2 == 1)

    # ---- stage 1: gradients ------------------------------------------------
    c0 = _sh(c, 0, 0)
    delh = np.abs(_sh(c, 0, 1) - _sh(c, 0, -1))
    delv = np.abs(_sh(c, 1, 0) - _sh(c, -1, 0))
    dirwts0 = _EPS + np.abs(_sh(c, 2, 0) - c0) + np.abs(c0 - _sh(c, -2, 0)) + delv
    dirwts1 = _EPS + np.abs(_sh(c, 0, 2) - c0) + np.abs(c0 - _sh(c, 0, -2)) + delh
    delhvsqsum = delh * delh + delv * delv
    d0 = _pad(dirwts0)
    d1 = _pad(dirwts1)

    # ---- stage 2: H/V colour differences -----------------------------------
    # cardinal colour ratios (gradient-weighted harmonic form)
    cru = _sh(c, -1, 0) * (_sh(d0, -2, 0) + dirwts0) / (
        _sh(d0, -2, 0) * (_EPS + c0) + dirwts0 * (_EPS + _sh(c, -2, 0)))
    crd = _sh(c, 1, 0) * (_sh(d0, 2, 0) + dirwts0) / (
        _sh(d0, 2, 0) * (_EPS + c0) + dirwts0 * (_EPS + _sh(c, 2, 0)))
    crl = _sh(c, 0, -1) * (_sh(d1, 0, -2) + dirwts1) / (
        _sh(d1, 0, -2) * (_EPS + c0) + dirwts1 * (_EPS + _sh(c, 0, -2)))
    crr = _sh(c, 0, 1) * (_sh(d1, 0, 2) + dirwts1) / (
        _sh(d1, 0, 2) * (_EPS + c0) + dirwts1 * (_EPS + _sh(c, 0, 2)))

    # Hamilton-Adams estimates
    guha = _sh(c, -1, 0) + 0.5 * (c0 - _sh(c, -2, 0))
    gdha = _sh(c, 1, 0) + 0.5 * (c0 - _sh(c, 2, 0))
    glha = _sh(c, 0, -1) + 0.5 * (c0 - _sh(c, 0, -2))
    grha = _sh(c, 0, 1) + 0.5 * (c0 - _sh(c, 0, 2))

    # adaptive-ratio estimates (fall back to HA when ratio far from 1)
    guar = np.where(np.abs(1.0 - cru) < ARTHRESH, c0 * cru, guha)
    gdar = np.where(np.abs(1.0 - crd) < ARTHRESH, c0 * crd, gdha)
    glar = np.where(np.abs(1.0 - crl) < ARTHRESH, c0 * crl, glha)
    grar = np.where(np.abs(1.0 - crr) < ARTHRESH, c0 * crr, grha)

    hwt = _sh(d1, 0, -1) / (_sh(d1, 0, -1) + _sh(d1, 0, 1))
    vwt = _sh(d0, -1, 0) / (_sh(d0, 1, 0) + _sh(d0, -1, 0))

    gintv_ha = vwt * gdha + (1.0 - vwt) * guha
    ginth_ha = hwt * grha + (1.0 - hwt) * glha
    gintv_ar = vwt * gdar + (1.0 - vwt) * guar
    ginth_ar = hwt * grar + (1.0 - hwt) * glar

    # near-clip: force the HA estimator (ratios are meaningless at clip)
    clipped_est = (c0 > clip_pt8) | (gintv_ha > clip_pt8) | (ginth_ha > clip_pt8)
    gintv_ar = np.where(clipped_est, gintv_ha, gintv_ar)
    ginth_ar = np.where(clipped_est, ginth_ha, ginth_ar)
    guar_c = np.where(clipped_est, guha, guar)
    gdar_c = np.where(clipped_est, gdha, gdar)
    glar_c = np.where(clipped_est, glha, glar)
    grar_c = np.where(clipped_est, grha, grar)

    # signed colour differences: + (G−R|B) convention via site parity
    sign = np.where(g_site, np.float32(1.0), np.float32(-1.0))
    vcd = sign * (c0 - gintv_ar)
    hcd = sign * (c0 - ginth_ar)
    vcdalt = sign * (c0 - gintv_ha)
    hcdalt = sign * (c0 - ginth_ha)

    dgintv = np.minimum((guha - gdha) ** 2, (guar_c - gdar_c) ** 2)
    dginth = np.minimum((glha - grha) ** 2, (glar_c - grar_c) ** 2)

    # ---- stage 3: estimator variance selection + saturation bounding -------
    hp = _pad(hcd)
    vp = _pad(vcd)
    hap = _pad(hcdalt)
    vap = _pad(vcdalt)

    def _var3(a0, am, ap_):
        return 3.0 * (am * am + a0 * a0 + ap_ * ap_) - (am + a0 + ap_) ** 2

    hcdvar = _var3(hcd, _sh(hp, 0, -2), _sh(hp, 0, 2))
    hcdaltvar = _var3(hcdalt, _sh(hap, 0, -2), _sh(hap, 0, 2))
    vcdvar = _var3(vcd, _sh(vp, -2, 0), _sh(vp, 2, 0))
    vcdaltvar = _var3(vcdalt, _sh(vap, -2, 0), _sh(vap, 2, 0))
    hcd = np.where(hcdaltvar < hcdvar, hcdalt, hcd)
    vcd = np.where(vcdaltvar < vcdvar, vcdalt, vcd)

    cl = _sh(c, 0, -1)
    cr_ = _sh(c, 0, 1)
    cu = _sh(c, -1, 0)
    cd = _sh(c, 1, 0)

    # G sites: colour difference positive means the interpolated R/B is
    # darker; bound overshoots toward neighbour values.
    ginth_g = c0 - hcd          # R|B estimate at G site
    gintv_g = c0 - vcd
    hcd_bnd = np.where(
        3.0 * hcd > (ginth_g + c0),
        -_ulim(ginth_g, cl, cr_) + c0,
        (1.0 - 3.0 * hcd / (_EPS + ginth_g + c0)) * hcd
        + (3.0 * hcd / (_EPS + ginth_g + c0))
        * (-_ulim(ginth_g, cl, cr_) + c0))
    vcd_bnd = np.where(
        3.0 * vcd > (gintv_g + c0),
        -_ulim(gintv_g, cu, cd) + c0,
        (1.0 - 3.0 * vcd / (_EPS + gintv_g + c0)) * vcd
        + (3.0 * vcd / (_EPS + gintv_g + c0))
        * (-_ulim(gintv_g, cu, cd) + c0))
    hcd_g = np.where(hcd > 0, hcd_bnd, hcd)
    vcd_g = np.where(vcd > 0, vcd_bnd, vcd)
    hcd_g = np.where(ginth_g > clip_pt, -_ulim(ginth_g, cl, cr_) + c0, hcd_g)
    vcd_g = np.where(gintv_g > clip_pt, -_ulim(gintv_g, cu, cd) + c0, vcd_g)

    # R/B sites: colour difference negative means interpolated G darker.
    ginth_rb = hcd + c0         # G estimate at R/B site
    gintv_rb = vcd + c0
    hcd_bnd2 = np.where(
        3.0 * hcd < -(ginth_rb + c0),
        _ulim(ginth_rb, cl, cr_) - c0,
        (1.0 + 3.0 * hcd / (_EPS + ginth_rb + c0)) * hcd
        + (-3.0 * hcd / (_EPS + ginth_rb + c0))
        * (_ulim(ginth_rb, cl, cr_) - c0))
    vcd_bnd2 = np.where(
        3.0 * vcd < -(gintv_rb + c0),
        _ulim(gintv_rb, cu, cd) - c0,
        (1.0 + 3.0 * vcd / (_EPS + gintv_rb + c0)) * vcd
        + (-3.0 * vcd / (_EPS + gintv_rb + c0))
        * (_ulim(gintv_rb, cu, cd) - c0))
    hcd_rb = np.where(hcd < 0, hcd_bnd2, hcd)
    vcd_rb = np.where(vcd < 0, vcd_bnd2, vcd)
    hcd_rb = np.where(ginth_rb > clip_pt, _ulim(ginth_rb, cl, cr_) - c0, hcd_rb)
    vcd_rb = np.where(gintv_rb > clip_pt, _ulim(gintv_rb, cu, cd) - c0, vcd_rb)

    hcd = np.where(g_site, hcd_g, hcd_rb).astype(np.float32)
    vcd = np.where(g_site, vcd_g, vcd_rb).astype(np.float32)
    cddiffsq = (vcd - hcd) ** 2       # meaningful at R/B sites

    # ---- stage 4: H/V direction weight at R/B sites ------------------------
    hp = _pad(hcd)
    vp = _pad(vcd)

    uave = vcd + _sh(vp, -1, 0) + _sh(vp, -2, 0) + _sh(vp, -3, 0)
    dave = vcd + _sh(vp, 1, 0) + _sh(vp, 2, 0) + _sh(vp, 3, 0)
    lave = hcd + _sh(hp, 0, -1) + _sh(hp, 0, -2) + _sh(hp, 0, -3)
    rave = hcd + _sh(hp, 0, 1) + _sh(hp, 0, 2) + _sh(hp, 0, 3)

    def _var4(a, sh_list, ave):
        s = (a - ave) ** 2
        for t in sh_list:
            s = s + (t - ave) ** 2
        return s

    dv_u = _var4(vcd, [_sh(vp, -1, 0), _sh(vp, -2, 0), _sh(vp, -3, 0)], uave)
    dv_d = _var4(vcd, [_sh(vp, 1, 0), _sh(vp, 2, 0), _sh(vp, 3, 0)], dave)
    dh_l = _var4(hcd, [_sh(hp, 0, -1), _sh(hp, 0, -2), _sh(hp, 0, -3)], lave)
    dh_r = _var4(hcd, [_sh(hp, 0, 1), _sh(hp, 0, 2), _sh(hp, 0, 3)], rave)

    vcdvar4 = _EPSSQ + vwt * dv_d + (1.0 - vwt) * dv_u
    hcdvar4 = _EPSSQ + hwt * dh_r + (1.0 - hwt) * dh_l

    gp = _pad(dgintv)
    gh = _pad(dginth)
    fv_u = dgintv + _sh(gp, -1, 0) + _sh(gp, -2, 0)
    fv_d = dgintv + _sh(gp, 1, 0) + _sh(gp, 2, 0)
    fh_l = dginth + _sh(gh, 0, -1) + _sh(gh, 0, -2)
    fh_r = dginth + _sh(gh, 0, 1) + _sh(gh, 0, 2)
    vcdvar1 = _EPSSQ + vwt * fv_d + (1.0 - vwt) * fv_u
    hcdvar1 = _EPSSQ + hwt * fh_r + (1.0 - hwt) * fh_l

    varwt = hcdvar4 / (vcdvar4 + hcdvar4)
    diffwt = hcdvar1 / (vcdvar1 + hcdvar1)
    agree = ((0.5 - varwt) * (0.5 - diffwt) > 0) & (
        np.abs(0.5 - diffwt) < np.abs(0.5 - varwt))
    hvwt = np.where(agree, varwt, diffwt).astype(np.float32)   # R/B sites

    # ---- stage 5: Nyquist test ---------------------------------------------
    cp = _pad(cddiffsq)
    sp = _pad(delhvsqsum)
    # quincunx Gaussian of cddiffsq minus Gaussian of gradient energy
    nyqutest = (
        _GAUSSODD[0] * cddiffsq
        + _GAUSSODD[1] * (_sh(cp, -1, -1) + _sh(cp, -1, 1)
                          + _sh(cp, 1, -1) + _sh(cp, 1, 1))
        + _GAUSSODD[2] * (_sh(cp, -2, 0) + _sh(cp, 0, -2)
                          + _sh(cp, 0, 2) + _sh(cp, 2, 0))
        + _GAUSSODD[3] * (_sh(cp, -2, -2) + _sh(cp, -2, 2)
                          + _sh(cp, 2, -2) + _sh(cp, 2, 2))
    ) - (
        _GAUSSGRAD[0] * delhvsqsum
        + _GAUSSGRAD[1] * (_sh(sp, -1, 0) + _sh(sp, 0, -1)
                           + _sh(sp, 0, 1) + _sh(sp, 1, 0))
        + _GAUSSGRAD[2] * (_sh(sp, -1, -1) + _sh(sp, -1, 1)
                           + _sh(sp, 1, -1) + _sh(sp, 1, 1))
        + _GAUSSGRAD[3] * (_sh(sp, -2, 0) + _sh(sp, 0, -2)
                           + _sh(sp, 0, 2) + _sh(sp, 2, 0))
        + _GAUSSGRAD[4] * (_sh(sp, -2, -1) + _sh(sp, -2, 1)
                           + _sh(sp, -1, -2) + _sh(sp, -1, 2)
                           + _sh(sp, 1, -2) + _sh(sp, 1, 2)
                           + _sh(sp, 2, -1) + _sh(sp, 2, 1))
        + _GAUSSGRAD[5] * (_sh(sp, -2, -2) + _sh(sp, -2, 2)
                           + _sh(sp, 2, -2) + _sh(sp, 2, 2)))

    nyq = (nyqutest > 0) & rb_site
    # canonical gate: the flagged set must span ≥2 rows AND ≥2 columns
    do_nyq = nyq.any(axis=1).sum() > 1 and nyq.any(axis=0).sum() > 1
    if do_nyq:
        # majority filter over the 8 same-colour-class neighbours
        np_pad = np.pad(nyq.astype(np.int8), _PAD, mode="constant")
        cnt = (_sh(np_pad, -2, 0) + _sh(np_pad, -1, -1) + _sh(np_pad, -1, 1)
               + _sh(np_pad, 0, -2) + _sh(np_pad, 0, 2) + _sh(np_pad, 1, -1)
               + _sh(np_pad, 1, 1) + _sh(np_pad, 2, 0))
        nyq2 = np.where(cnt > 4, True, np.where(cnt < 4, False, nyq)) & rb_site

        if nyq2.any():
            # area statistics over the 7x7 step-2 window of flagged sites
            n2 = np.pad(nyq2.astype(np.float32), _PAD, mode="constant")
            cfa_n = _pad(cfa)
            sh_h = _pad((_sh(c, 0, -1) + _sh(c, 0, 1)).astype(np.float32))
            sh_v = _pad((_sh(c, -1, 0) + _sh(c, 1, 0)).astype(np.float32))
            sq_h = _pad((c0 - _sh(c, 0, -1)) ** 2 + (c0 - _sh(c, 0, 1)) ** 2)
            sq_v = _pad((c0 - _sh(c, -1, 0)) ** 2 + (c0 - _sh(c, 1, 0)) ** 2)
            sumcfa = np.zeros((h, w), np.float32)
            sumh = np.zeros((h, w), np.float32)
            sumv = np.zeros((h, w), np.float32)
            sumsqh = np.zeros((h, w), np.float32)
            sumsqv = np.zeros((h, w), np.float32)
            areawt = np.zeros((h, w), np.float32)
            for dy in range(-6, 7, 2):
                for dx in range(-6, 7, 2):
                    m = _sh(n2, dy, dx)
                    sumcfa += m * _sh(cfa_n, dy, dx)
                    sumh += m * _sh(sh_h, dy, dx)
                    sumv += m * _sh(sh_v, dy, dx)
                    sumsqh += m * _sh(sq_h, dy, dx)
                    sumsqv += m * _sh(sq_v, dy, dx)
                    areawt += m
            sumh = sumcfa - 0.5 * sumh
            sumv = sumcfa - 0.5 * sumv
            areawt = 0.5 * areawt
            hv_ = _EPSSQ + np.abs(areawt * sumsqh - sumh * sumh)
            vv_ = _EPSSQ + np.abs(areawt * sumsqv - sumv * sumv)
            hvwt = np.where(nyq2, hv_ / (vv_ + hv_), hvwt).astype(np.float32)
    else:
        nyq2 = nyq

    # ---- stage 6: G at R/B sites -------------------------------------------
    hw = _pad(hvwt)
    hvwtalt = 0.25 * (_sh(hw, -1, -1) + _sh(hw, -1, 1)
                      + _sh(hw, 1, -1) + _sh(hw, 1, 1))
    hvwt = np.where(np.abs(0.5 - hvwt) < np.abs(0.5 - hvwtalt),
                    hvwtalt, hvwt).astype(np.float32)

    dgrb0 = (hvwt * vcd + (1.0 - hvwt) * hcd).astype(np.float32)
    green = np.where(rb_site, cfa + dgrb0, cfa).astype(np.float32)

    # Nyquist refinement from local G curvature
    if nyq2.any():
        gpad = _pad(green)
        curv_h = np.where(
            nyq2, (green - 0.5 * (_sh(gpad, 0, -1) + _sh(gpad, 0, 1))) ** 2, 0.0)
        curv_v = np.where(
            nyq2, (green - 0.5 * (_sh(gpad, -1, 0) + _sh(gpad, 1, 0))) ** 2, 0.0)
        chp = _pad(curv_h.astype(np.float32))
        cvp = _pad(curv_v.astype(np.float32))

        def _gq(a0, apad):
            return (_GQUINC[0] * a0
                    + _GQUINC[1] * (_sh(apad, -1, -1) + _sh(apad, -1, 1)
                                    + _sh(apad, 1, -1) + _sh(apad, 1, 1))
                    + _GQUINC[2] * (_sh(apad, -2, 0) + _sh(apad, 0, -2)
                                    + _sh(apad, 0, 2) + _sh(apad, 2, 0))
                    + _GQUINC[3] * (_sh(apad, -2, -2) + _sh(apad, -2, 2)
                                    + _sh(apad, 2, -2) + _sh(apad, 2, 2)))

        gvarh = _EPSSQ + _gq(curv_h, chp)
        gvarv = _EPSSQ + _gq(curv_v, cvp)
        dgrb0 = np.where(nyq2,
                         (hcd * gvarv + vcd * gvarh) / (gvarv + gvarh),
                         dgrb0).astype(np.float32)
        green = np.where(rb_site, cfa + dgrb0, green).astype(np.float32)

    # ---- stage 7: diagonal R/B interpolation at R/B sites -------------------
    # gradients along the two diagonals; the C code computes delp/delm and
    # the diagonal difference-squares on alternating cosets — expressed
    # here directly per site class:
    # delp/delm live on R/B sites (gradients of the OPPOSITE R/B colour),
    # Dgrbsq1p/m live on G sites (squared diagonal colour differences).
    delp_rb = np.abs(_sh(c, -1, 1) - _sh(c, 1, -1))   # P diagonal (NE-SW)
    delm_rb = np.abs(_sh(c, 1, 1) - _sh(c, -1, -1))   # M diagonal (SE-NW)
    dsq_p = (c0 - _sh(c, -1, 1)) ** 2 + (c0 - _sh(c, 1, -1)) ** 2
    dsq_m = (c0 - _sh(c, 1, 1)) ** 2 + (c0 - _sh(c, -1, -1)) ** 2

    crse = 2.0 * _sh(c, 1, 1) / (_EPS + c0 + _sh(c, 2, 2))
    crnw = 2.0 * _sh(c, -1, -1) / (_EPS + c0 + _sh(c, -2, -2))
    crne = 2.0 * _sh(c, -1, 1) / (_EPS + c0 + _sh(c, -2, 2))
    crsw = 2.0 * _sh(c, 1, -1) / (_EPS + c0 + _sh(c, 2, -2))

    rbse = np.where(np.abs(1.0 - crse) < ARTHRESH, c0 * crse,
                    _sh(c, 1, 1) + 0.5 * (c0 - _sh(c, 2, 2)))
    rbnw = np.where(np.abs(1.0 - crnw) < ARTHRESH, c0 * crnw,
                    _sh(c, -1, -1) + 0.5 * (c0 - _sh(c, -2, -2)))
    rbne = np.where(np.abs(1.0 - crne) < ARTHRESH, c0 * crne,
                    _sh(c, -1, 1) + 0.5 * (c0 - _sh(c, -2, 2)))
    rbsw = np.where(np.abs(1.0 - crsw) < ARTHRESH, c0 * crsw,
                    _sh(c, 1, -1) + 0.5 * (c0 - _sh(c, 2, -2)))

    dpp = _pad(delp_rb)
    dmp = _pad(delm_rb)
    wtse = _EPS + delm_rb + _sh(dmp, 1, 1) + _sh(dmp, 2, 2)
    wtnw = _EPS + delm_rb + _sh(dmp, -1, -1) + _sh(dmp, -2, -2)
    wtne = _EPS + delp_rb + _sh(dpp, -1, 1) + _sh(dpp, -2, 2)
    wtsw = _EPS + delp_rb + _sh(dpp, 1, -1) + _sh(dpp, 2, -2)

    rbm = (wtse * rbnw + wtnw * rbse) / (wtse + wtnw)
    rbp = (wtne * rbsw + wtsw * rbne) / (wtne + wtsw)

    qp = _pad(dsq_p)
    qm = _pad(dsq_m)

    def _geven(apad):
        return (_GAUSSEVEN[0] * (_sh(apad, -1, 0) + _sh(apad, 0, -1)
                                 + _sh(apad, 0, 1) + _sh(apad, 1, 0))
                + _GAUSSEVEN[1] * (_sh(apad, -2, -1) + _sh(apad, -2, 1)
                                   + _sh(apad, -1, -2) + _sh(apad, -1, 2)
                                   + _sh(apad, 1, -2) + _sh(apad, 1, 2)
                                   + _sh(apad, 2, -1) + _sh(apad, 2, 1)))

    rbvarm = _EPSSQ + _geven(qm)
    pmwt = (rbvarm / ((_EPSSQ + _geven(qp)) + rbvarm)).astype(np.float32)

    # saturation bounding of the diagonal estimates
    cse = _sh(c, 1, 1)
    cnw = _sh(c, -1, -1)
    cne = _sh(c, -1, 1)
    csw = _sh(c, 1, -1)
    low_p = 2.0 * rbp < c0
    pwt = 2.0 * (c0 - rbp) / (_EPS + rbp + c0)
    rbp = np.where(rbp < c0,
                   np.where(low_p, _ulim(rbp, csw, cne),
                            pwt * rbp + (1.0 - pwt) * _ulim(rbp, csw, cne)),
                   rbp)
    low_m = 2.0 * rbm < c0
    mwt = 2.0 * (c0 - rbm) / (_EPS + rbm + c0)
    rbm = np.where(rbm < c0,
                   np.where(low_m, _ulim(rbm, cnw, cse),
                            mwt * rbm + (1.0 - mwt) * _ulim(rbm, cnw, cse)),
                   rbm)
    rbp = np.where(rbp > clip_pt, _ulim(rbp, csw, cne), rbp)
    rbm = np.where(rbm > clip_pt, _ulim(rbm, cnw, cse), rbm)

    pw = _pad(pmwt)
    pmwtalt = 0.25 * (_sh(pw, -1, -1) + _sh(pw, -1, 1)
                      + _sh(pw, 1, -1) + _sh(pw, 1, 1))
    pmwt = np.where(np.abs(0.5 - pmwt) < np.abs(0.5 - pmwtalt),
                    pmwtalt, pmwt).astype(np.float32)
    rbint = (0.5 * (cfa + rbm * (1.0 - pmwt) + rbp * pmwt)).astype(np.float32)

    # ---- stage 8: diagonal correction of G ----------------------------------
    use_diag = (np.abs(0.5 - pmwt) >= np.abs(0.5 - hvwt)) & rb_site
    if use_diag.any():
        # NB: rbint lives on the R/B coset — its cardinal same-coset
        # neighbours are ±2 px away (the C code's half-index ±v1/±1
        # arithmetic resolves to ±2 pixel offsets).
        rp = _pad(rbint)
        cru2 = _sh(c, -1, 0) * 2.0 / (_EPS + rbint + _sh(rp, -2, 0))
        crd2 = _sh(c, 1, 0) * 2.0 / (_EPS + rbint + _sh(rp, 2, 0))
        crl2 = _sh(c, 0, -1) * 2.0 / (_EPS + rbint + _sh(rp, 0, -2))
        crr2 = _sh(c, 0, 1) * 2.0 / (_EPS + rbint + _sh(rp, 0, 2))
        gu2 = np.where(np.abs(1.0 - cru2) < ARTHRESH, rbint * cru2,
                       _sh(c, -1, 0) + 0.5 * (rbint - _sh(rp, -2, 0)))
        gd2 = np.where(np.abs(1.0 - crd2) < ARTHRESH, rbint * crd2,
                       _sh(c, 1, 0) + 0.5 * (rbint - _sh(rp, 2, 0)))
        gl2 = np.where(np.abs(1.0 - crl2) < ARTHRESH, rbint * crl2,
                       _sh(c, 0, -1) + 0.5 * (rbint - _sh(rp, 0, -2)))
        gr2 = np.where(np.abs(1.0 - crr2) < ARTHRESH, rbint * crr2,
                       _sh(c, 0, 1) + 0.5 * (rbint - _sh(rp, 0, 2)))
        gintv2 = (_sh(d0, -1, 0) * gd2 + _sh(d0, 1, 0) * gu2) / (
            _sh(d0, 1, 0) + _sh(d0, -1, 0))
        ginth2 = (_sh(d1, 0, -1) * gr2 + _sh(d1, 0, 1) * gl2) / (
            _sh(d1, 0, -1) + _sh(d1, 0, 1))

        lowv = 2.0 * gintv2 < rbint
        vwt2 = 2.0 * (rbint - gintv2) / (_EPS + gintv2 + rbint)
        gintv2 = np.where(gintv2 < rbint,
                          np.where(lowv, _ulim(gintv2, cu, cd),
                                   vwt2 * gintv2 + (1.0 - vwt2) * _ulim(gintv2, cu, cd)),
                          gintv2)
        lowh = 2.0 * ginth2 < rbint
        hwt2 = 2.0 * (rbint - ginth2) / (_EPS + ginth2 + rbint)
        ginth2 = np.where(ginth2 < rbint,
                          np.where(lowh, _ulim(ginth2, cl, cr_),
                                   hwt2 * ginth2 + (1.0 - hwt2) * _ulim(ginth2, cl, cr_)),
                          ginth2)
        ginth2 = np.where(ginth2 > clip_pt, _ulim(ginth2, cl, cr_), ginth2)
        gintv2 = np.where(gintv2 > clip_pt, _ulim(gintv2, cu, cd), gintv2)

        gnew = ginth2 * (1.0 - hvwt) + gintv2 * hvwt
        green = np.where(use_diag, gnew, green).astype(np.float32)
        dgrb0 = np.where(use_diag, green - cfa, dgrb0).astype(np.float32)

    # ---- stage 9: chrominance propagation -----------------------------------
    # native chroma: G−R at R sites, G−B at B sites
    dgrb_r = np.where(r_site, dgrb0, 0.0).astype(np.float32)
    dgrb_b = np.where(b_site, dgrb0, 0.0).astype(np.float32)

    def _chroma_stencil(dg: np.ndarray) -> np.ndarray:
        """4-diagonal weighted chroma interpolation onto the opposite
        R/B coset (the canonical 1.325/−0.175/−0.075/−0.075 stencil; each
        diagonal tap flanked by its two ±2-axis neighbours). The weight
        terms reproduce the canonical code EXACTLY, including its two
        mixed m3/p3 cross-diagonal terms in wtsw/wtse — faithfulness to
        the validated engine behaviour outranks apparent symmetry."""
        p = _pad(dg)
        wtnw2 = 1.0 / (_EPS + np.abs(_sh(p, -1, -1) - _sh(p, 1, 1))
                       + np.abs(_sh(p, -1, -1) - _sh(p, -3, -3))
                       + np.abs(_sh(p, 1, 1) - _sh(p, -3, -3)))
        wtne2 = 1.0 / (_EPS + np.abs(_sh(p, -1, 1) - _sh(p, 1, -1))
                       + np.abs(_sh(p, -1, 1) - _sh(p, -3, 3))
                       + np.abs(_sh(p, 1, -1) - _sh(p, -3, 3)))
        wtsw2 = 1.0 / (_EPS + np.abs(_sh(p, 1, -1) - _sh(p, -1, 1))
                       + np.abs(_sh(p, 1, -1) - _sh(p, 3, 3))
                       + np.abs(_sh(p, -1, 1) - _sh(p, 3, -3)))
        wtse2 = 1.0 / (_EPS + np.abs(_sh(p, 1, 1) - _sh(p, -1, -1))
                       + np.abs(_sh(p, 1, 1) - _sh(p, 3, -3))
                       + np.abs(_sh(p, -1, -1) - _sh(p, 3, 3)))
        est = (wtnw2 * (1.325 * _sh(p, -1, -1) - 0.175 * _sh(p, -3, -3)
                        - 0.075 * _sh(p, -1, -3) - 0.075 * _sh(p, -3, -1))
               + wtne2 * (1.325 * _sh(p, -1, 1) - 0.175 * _sh(p, -3, 3)
                          - 0.075 * _sh(p, -1, 3) - 0.075 * _sh(p, 1, 1))
               + wtsw2 * (1.325 * _sh(p, 1, -1) - 0.175 * _sh(p, 3, -3)
                          - 0.075 * _sh(p, 1, -3) - 0.075 * _sh(p, -1, -1))
               + wtse2 * (1.325 * _sh(p, 1, 1) - 0.175 * _sh(p, 3, 3)
                          - 0.075 * _sh(p, 1, 3) - 0.075 * _sh(p, 3, 1))
               ) / (wtnw2 + wtne2 + wtsw2 + wtse2)
        return est.astype(np.float32)

    # G−R interpolated at B sites; G−B interpolated at R sites
    dgrb_r = np.where(b_site, _chroma_stencil(dgrb_r), dgrb_r)
    dgrb_b = np.where(r_site, _chroma_stencil(dgrb_b), dgrb_b)

    # G sites: 4-cardinal hvwt-weighted chroma average (vertical
    # neighbours weighted by their vertical confidence, horizontal by
    # the complement)
    hwp = _pad(hvwt)
    drp = _pad(dgrb_r)
    dbp = _pad(dgrb_b)
    w_u = _sh(hwp, -1, 0)
    w_d = _sh(hwp, 1, 0)
    w_l = 1.0 - _sh(hwp, 0, -1)
    w_r = 1.0 - _sh(hwp, 0, 1)
    wsum = w_u + w_d + w_l + w_r
    r_at_g = green - (w_u * _sh(drp, -1, 0) + w_d * _sh(drp, 1, 0)
                      + w_l * _sh(drp, 0, -1) + w_r * _sh(drp, 0, 1)) / wsum
    b_at_g = green - (w_u * _sh(dbp, -1, 0) + w_d * _sh(dbp, 1, 0)
                      + w_l * _sh(dbp, 0, -1) + w_r * _sh(dbp, 0, 1)) / wsum

    red = np.where(g_site, r_at_g, green - dgrb_r)
    blue = np.where(g_site, b_at_g, green - dgrb_b)

    rgb = np.stack([red, green, blue], axis=-1)
    rgb = np.nan_to_num(rgb, nan=0.5, posinf=1.0, neginf=0.0)
    return np.clip(rgb, 0.0, 1.0).astype(np.float32)
