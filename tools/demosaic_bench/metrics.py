"""Demosaic-quality metric battery (docs/research/demosaic-test-fixtures.md §5).

A demosaic-algorithm decision must NOT rest on CPSNR alone: CPSNR rewards blur
and is near-blind to localized zipper and to pure-chroma false colour (§3/§5).
This module is the primary-sourced battery that reveals what CPSNR hides —
each function's docstring cites the paper/standard it implements (§5):

  cpsnr                     pooled-colour PSNR            (survey; CNNCDM 10-px)
  s_cielab_de               false-colour ΔE               (Zhang & Wandell 1997)
  zipper_ratio              zipper-effect %               (Lu & Tan 2003)
  region_split              CPSNR/ΔE on edge vs smooth    (Lu & Tan 2003)
  mtf50p                    resolution, sharpening-robust (ISO 12233 eSFR)
  falsecolor_chroma_energy  chroma where GT chroma≈0      (zone-plate moiré)

`cpsnr`, `mosaic_rggb`, `bilinear_rggb` are imported (not copied) from the
already-validated `kodak_cpsnr.py` — single source of truth.

DOMAIN: S-CIELAB / zipper / false-colour operate in CIE XYZ (then CIELAB). The
two source domains convert differently and conflating them is a silent bug:
  - Kodak PNGs are sRGB-ENCODED  -> sRGB OETF decode + primaries  (xyz_from_srgb)
  - the synthetic charts are LINEAR RGB -> primaries only, no decode (xyz_from_linear_rgb)
These metrics therefore take XYZ; callers convert with the right helper.

CLIP POLICY (fairness — audit/advisor): demosaicers ring outside [0,1] (Malvar
seen at [-0.185, 1.327]); negatives -> bogus huge ΔE that differentially punishes
whichever method rings. For every Lab-domain metric (S-CIELAB, zipper, region ΔE,
false-colour) callers clip BOTH ref and test to [0,1] identically before XYZ
conversion. CPSNR stays UNCLIPPED (error is error; matches the published protocol).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# Reuse the validated primitives (bilinear is bit-exact vs colour_demosaicing).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from kodak_cpsnr import bilinear_rggb, cpsnr, mosaic_rggb  # noqa: E402,F401

# ----------------------------------------------------------------------------
# Colour-space front-ends (XYZ is the metric domain; choose by source encoding).
# ----------------------------------------------------------------------------

# sRGB linear primaries -> CIE XYZ (D65), IEC 61966-2-1. Same matrix `colour`
# carries as RGB_COLOURSPACE_sRGB.matrix_RGB_to_XYZ; inlined so the metric module
# has no hard import-time dependency on colour for the linear path.
_SRGB_RGB_TO_XYZ = np.array(
    [
        [0.41239080, 0.35758434, 0.18048079],
        [0.21263901, 0.71516868, 0.07219232],
        [0.01933082, 0.11919478, 0.95053215],
    ],
    dtype=np.float64,
)
# CIELAB normalising white. Derived FROM the RGB->XYZ matrix (= matrix @ [1,1,1])
# rather than a hand-typed D65 triple, so an exactly-neutral RGB lands at exactly
# a*=b*=0 (a hand-typed white off by ~2e-4 in Z makes grey read b*≈-0.01 and
# breaks the perfect-neutral false-colour oracle). This is the colourspace's own
# white, which is what CIELAB normalisation requires.
_D65_XYZ = _SRGB_RGB_TO_XYZ @ np.ones(3, dtype=np.float64)


def _srgb_eotf(v: np.ndarray) -> np.ndarray:
    """sRGB OETF^-1 (IEC 61966-2-1): display sRGB code value -> linear light."""
    v = np.asarray(v, dtype=np.float64)
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def xyz_from_linear_rgb(rgb: np.ndarray) -> np.ndarray:
    """LINEAR sRGB/Rec.709-primaried RGB -> CIE XYZ (for the synthetic charts).

    No OETF decode — the charts are already scene-linear. Use this, NOT the sRGB
    path, on Layer-A charts (feeding linear values through an sRGB decode is the
    silent domain bug the advisor flagged).
    """
    return np.asarray(rgb, dtype=np.float64) @ _SRGB_RGB_TO_XYZ.T


def xyz_from_srgb(rgb: np.ndarray) -> np.ndarray:
    """sRGB-ENCODED RGB in [0,1] -> CIE XYZ (for Kodak PNGs): OETF decode then
    the linear-primary matrix. S-CIELAB on Kodak follows the published sRGB
    protocol via this path."""
    return xyz_from_linear_rgb(_srgb_eotf(rgb))


def _xyz_to_lab(xyz: np.ndarray, white: np.ndarray = _D65_XYZ) -> np.ndarray:
    """CIE XYZ -> CIELAB (CIE 15; D65 white). Vectorised over an (H,W,3) image."""
    xr = xyz / white
    eps = 216.0 / 24389.0
    kappa = 24389.0 / 27.0
    fx = np.where(xr > eps, np.cbrt(xr), (kappa * xr + 16.0) / 116.0)
    fX, fY, fZ = fx[..., 0], fx[..., 1], fx[..., 2]
    lab = np.empty_like(xyz)
    lab[..., 0] = 116.0 * fY - 16.0
    lab[..., 1] = 500.0 * (fX - fY)
    lab[..., 2] = 200.0 * (fY - fZ)
    return lab


# ----------------------------------------------------------------------------
# S-CIELAB — the correct false-colour metric (Zhang & Wandell 1997).
# ----------------------------------------------------------------------------

# Opponent transform (XYZ -> {luminance, red-green, blue-yellow}) and the spatial
# CSF filters are the Zhang & Wandell reference implementation, read verbatim from
# the canonical MATLAB toolbox `wandell/SCIELAB-1996` (cmatrix.m, separableFilters.m,
# gauss.m, sumGauss.m). Numbers are quoted, not reconstructed from memory.

# cmatrix('xyz2opp', 2)  — CIE 1931 2-degree, divided by 1000.
_XYZ2OPP = (
    np.array(
        [
            [278.7336, 721.8031, -106.5520],
            [-448.7736, 289.8056, 77.1569],
            [85.9513, -589.9859, 501.1089],
        ],
        dtype=np.float64,
    )
    / 1000.0
)
_OPP2XYZ = np.linalg.inv(_XYZ2OPP)

# separableFilters.m, weight/halfwidth pairs in DEGREES of visual angle.
# Format [halfwidth1, weight1, halfwidth2, weight2, ...]; weights per channel sum
# to ~1 so each filter is DC-preserving (verified: uniform field -> ΔE 0). The
# luminance channel's negative third term is a real difference-of-Gaussians lobe.
_OPP_FILTERS_DEG = (
    [0.05, 1.00327, 0.225, 0.114416, 7.0, -0.117686],  # luminance
    [0.0685, 0.616725, 0.826, 0.383275],               # red-green
    [0.0920, 0.567885, 0.6451, 0.432115],              # blue-yellow
)

# Fixed, documented viewing condition (S-CIELAB is viewing-distance dependent; §5
# says fix + report it). 47 samples/degree ~ a 24" 96-dpi display at ~50 cm; the
# absolute ΔE shifts with this value but the METHOD RANKING does not.
S_CIELAB_SAMPLES_PER_DEGREE = 47.0


def _gauss_1d(halfwidth_px: float, width: int) -> np.ndarray:
    """One unit-sum Gaussian (gauss.m). `halfwidth_px` is FWHM in pixels;
    alpha = 2*sqrt(ln2)/(halfwidth-1), g = exp(-alpha^2 x^2), normalised to sum 1.
    """
    alpha = 2.0 * np.sqrt(np.log(2.0)) / (halfwidth_px - 1.0)
    x = np.arange(1, width + 1) - round(width / 2)
    g = np.exp(-(alpha * alpha) * x * x)
    return g / g.sum()


def _sum_gauss_1d(pairs_deg: list[float], width: int, spd: float) -> np.ndarray:
    """Weighted sum of unit-sum Gaussians (sumGauss.m); halfwidths deg->px via spd."""
    g = np.zeros(width, dtype=np.float64)
    for i in range(0, len(pairs_deg), 2):
        hw_px = pairs_deg[i] * spd
        g = g + pairs_deg[i + 1] * _gauss_1d(hw_px, width)
    return g


def _scielab_kernels(spd: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The three separable 1-D opponent CSF kernels at `spd` samples/degree.

    Width = 1 degree of support rounded to an odd length (matches the toolbox
    convention of a ~1-deg filter). Each kernel is applied separably (rows then
    cols) to its opponent plane.
    """
    width = int(np.ceil(spd / 2.0)) * 2 - 1  # odd, ~1 deg
    return tuple(_sum_gauss_1d(list(p), width, spd) for p in _OPP_FILTERS_DEG)


def _filter_separable(plane: np.ndarray, k: np.ndarray) -> np.ndarray:
    """Apply a 1-D unit-sum kernel `k` separably (rows then cols), edge-reflected.

    Reflection keeps a uniform field uniform (so DC/ΔE-0 invariants hold at the
    border too). Pure numpy — no scipy dependency for this hot path.
    """
    r = k.size // 2
    p = np.pad(plane, ((r, r), (0, 0)), mode="reflect")
    out = np.zeros_like(plane)
    for i, w in enumerate(k):
        out += w * p[i:i + plane.shape[0], :]
    p = np.pad(out, ((0, 0), (r, r)), mode="reflect")
    out = np.zeros_like(plane)
    for i, w in enumerate(k):
        out += w * p[:, i:i + plane.shape[1]]
    return out


def scielab_filter_xyz(xyz: np.ndarray, spd: float = S_CIELAB_SAMPLES_PER_DEGREE) -> np.ndarray:
    """Spatially pre-filter an XYZ image through the opponent CSF (the S-CIELAB
    front end): XYZ -> opponent -> per-channel CSF filter -> XYZ. Exposed for the
    metric oracle (lets a test inspect the filtered field directly)."""
    opp = xyz @ _XYZ2OPP.T
    k1, k2, k3 = _scielab_kernels(spd)
    opp_f = np.empty_like(opp)
    opp_f[..., 0] = _filter_separable(opp[..., 0], k1)
    opp_f[..., 1] = _filter_separable(opp[..., 1], k2)
    opp_f[..., 2] = _filter_separable(opp[..., 2], k3)
    return opp_f @ _OPP2XYZ.T


def s_cielab_de(
    ref_xyz: np.ndarray,
    test_xyz: np.ndarray,
    spd: float = S_CIELAB_SAMPLES_PER_DEGREE,
) -> float:
    """Mean S-CIELAB ΔEab between two XYZ images (Zhang & Wandell, *A spatial
    extension of CIELAB for digital color-image reproduction*, J. SID 5(1):61, 1997).

    Pipeline: opponent transform (1 luminance + 2 chroma) -> per-channel spatial
    CSF filter (chroma low-passed harder than luminance, so high-frequency colour
    error the eye cannot resolve is discounted) -> back to XYZ -> CIELAB -> ΔEab
    (CIE 1976) pixelwise -> mean. This is the *correct false-colour metric*: it
    down-weights fine chroma noise yet flags broad/strong casts (§5). ΔE76 is the
    classic S-CIELAB pairing (documented; ΔE2000 is a separate choice).

    Inputs are CIE XYZ (use `xyz_from_srgb` for Kodak, `xyz_from_linear_rgb` for
    charts). Callers clip RGB to [0,1] identically before conversion (clip policy).
    """
    lab_r = _xyz_to_lab(scielab_filter_xyz(ref_xyz, spd))
    lab_t = _xyz_to_lab(scielab_filter_xyz(test_xyz, spd))
    de = np.sqrt(np.sum((lab_r - lab_t) ** 2, axis=-1))
    return float(np.mean(de))


# ----------------------------------------------------------------------------
# Zipper effect + region split (Lu & Tan 2003).
# ----------------------------------------------------------------------------

# Lu & Tan, *Color Filter Array Demosaicking: New Method and Performance
# Measures*, IEEE TIP 12(10):1194, 2003. The exact ΔE threshold (~2.3) and the
# edge classifier are NOT pinned to the primary (PDF refused; §10); the structure
# is faithful, the constants are documented choices, and the metric is leaned on
# only via its flat-field=0 oracle + the relative ranking (NOT its absolute value).
_ZIPPER_DE_THRESHOLD = 2.3  # ΔEab; documented choice, cited secondhand (§10).


def _lab_from_xyz_clipped(xyz: np.ndarray) -> np.ndarray:
    return _xyz_to_lab(xyz)


def zipper_ratio(
    ref_xyz: np.ndarray,
    test_xyz: np.ndarray,
    threshold: float = _ZIPPER_DE_THRESHOLD,
) -> float:
    """Zipper-effect ratio in PERCENT (Lu & Tan 2003, performance measure §V).

    For every pixel find, in the *reference*, its most-similar 8-neighbour in Lab
    (the nearest-neighbour-in-colour). A pixel is a "zipper" pixel if the
    reference P<->Pn ΔE and the demosaiced P<->Pn ΔE disagree by more than
    `threshold` — i.e. the demosaic introduced a local colour discontinuity that
    is not in the ground truth (the on/off ribbon of a zipper). Reports the % of
    such pixels (interior only; the 1-px frame is excluded).

    Flat field -> 0 (every neighbour difference is 0 in both ref and test) — the
    metric's own oracle. Absolute % depends on `threshold` (§10); rely on the
    RANKING. Inputs XYZ, clipped to [0,1] before conversion (clip policy).
    """
    lab_r = _lab_from_xyz_clipped(ref_xyz)
    lab_t = _lab_from_xyz_clipped(test_xyz)
    h, w, _ = lab_r.shape
    shifts = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    best_de_r = np.full((h, w), np.inf)
    de_t_at_best = np.zeros((h, w))
    for dy, dx in shifts:
        rr = np.roll(np.roll(lab_r, dy, axis=0), dx, axis=1)
        tt = np.roll(np.roll(lab_t, dy, axis=0), dx, axis=1)
        de_r = np.sqrt(np.sum((lab_r - rr) ** 2, axis=-1))
        de_t = np.sqrt(np.sum((lab_t - tt) ** 2, axis=-1))
        upd = de_r < best_de_r
        best_de_r = np.where(upd, de_r, best_de_r)
        de_t_at_best = np.where(upd, de_t, de_t_at_best)
    zipper = np.abs(best_de_r - de_t_at_best) > threshold
    interior = zipper[1:-1, 1:-1]
    return 100.0 * float(np.mean(interior))


def _edge_mask(ref_xyz: np.ndarray, percentile: float = 75.0) -> np.ndarray:
    """Boolean edge mask from the luminance-gradient magnitude of the reference.

    Sobel-like central differences on L*; pixels ABOVE `percentile` of the
    gradient are "edge" (artifacts concentrate there), the rest "smooth". The
    classifier operator/threshold is a documented choice (Lu & Tan's exact one is
    unpinned, §10); the split is meaningful for the RELATIVE edge-vs-smooth gap.

    Uses a STRICT `>` (not `>=`): on a sparse-edge chart most pixels are flat
    (gradient exactly 0) so the percentile itself is 0, and `>= 0` would select
    the whole image. `>` excludes the flat zeros so the mask is the true edge set.
    """
    lab = _xyz_to_lab(ref_xyz)
    lum = lab[..., 0]
    gy = np.zeros_like(lum)
    gx = np.zeros_like(lum)
    gy[1:-1, :] = lum[2:, :] - lum[:-2, :]
    gx[:, 1:-1] = lum[:, 2:] - lum[:, :-2]
    grad = np.hypot(gx, gy)
    thr = np.percentile(grad, percentile)
    return grad > thr


def _chroma_edge_mask(ref_xyz: np.ndarray, percentile: float = 97.0) -> np.ndarray:
    """Boolean transition-band mask from the CHROMA (a*,b*) gradient of the
    reference — the correct localiser for an ISOLUMINANT colour edge, whose
    transition lives in chrominance, not L* (an L* mask would see ~no signal).

    Strict `>` over a high percentile keeps only the thin slanted transition band
    (~a few % of pixels), so a fringe-sensitive statistic measured over it
    reflects the edge, not the flat colour fields on either side.
    """
    lab = _xyz_to_lab(ref_xyz)
    a, b = lab[..., 1], lab[..., 2]
    gy = np.zeros_like(a)
    gx = np.zeros_like(a)
    gy[1:-1, :] = np.hypot(a[2:, :] - a[:-2, :], b[2:, :] - b[:-2, :])
    gx[:, 1:-1] = np.hypot(a[:, 2:] - a[:, :-2], b[:, 2:] - b[:, :-2])
    grad = np.hypot(gx, gy)
    thr = np.percentile(grad, percentile)
    return grad > thr


def region_split(
    ref_xyz: np.ndarray,
    test_xyz: np.ndarray,
    ref_rgb: np.ndarray,
    test_rgb: np.ndarray,
    border: int = 10,
) -> dict[str, float]:
    """CPSNR + mean ΔEab reported SEPARATELY on edge vs smooth regions (Lu & Tan
    2003 §V region-split): whole-image means hide artifacts that concentrate at
    edges. Returns {cpsnr_edge, cpsnr_smooth, de_edge, de_smooth}.

    CPSNR uses the UNCLIPPED rgb (error is error); ΔE uses the clipped XYZ. Edges
    are the top-quartile L*-gradient pixels of the reference (`_edge_mask`).
    """
    edge = _edge_mask(ref_xyz)
    if border:
        keep = np.zeros_like(edge)
        keep[border:-border, border:-border] = True
        edge = edge & keep
        smooth = (~edge) & keep
    else:
        smooth = ~edge

    def _cpsnr_masked(rgb_r: np.ndarray, rgb_t: np.ndarray, m: np.ndarray) -> float:
        diff = (rgb_r - rgb_t)[m]
        cmse = float(np.mean(diff ** 2)) if diff.size else 0.0
        return float("inf") if cmse <= 0 else 10.0 * np.log10(1.0 / cmse)

    lab_r = _xyz_to_lab(ref_xyz)
    lab_t = _xyz_to_lab(test_xyz)
    de = np.sqrt(np.sum((lab_r - lab_t) ** 2, axis=-1))
    return {
        "cpsnr_edge": _cpsnr_masked(ref_rgb, test_rgb, edge),
        "cpsnr_smooth": _cpsnr_masked(ref_rgb, test_rgb, smooth),
        "de_edge": float(np.mean(de[edge])) if edge.any() else 0.0,
        "de_smooth": float(np.mean(de[smooth])) if smooth.any() else 0.0,
    }


# ----------------------------------------------------------------------------
# False colour from a chart with a known-neutral region (zone plate).
# ----------------------------------------------------------------------------


def chroma_amplitude_recovery(
    test_rgb: np.ndarray,
    gt_rgb: np.ndarray,
    *,
    border: int = 8,
) -> float:
    """Fraction of a colour grating's TRUE chroma modulation a demosaic recovered
    (docs/research/demosaic-false-color-test.md — the adversarial real-colour test).

    For the isoluminant near-Nyquist colour grating (`charts.isoluminant_color_grating`):
    the ground truth alternates between two fixed CIELab chroma points, so its chroma
    signal has a known modulation amplitude. A faithful demosaic reproduces that
    modulation; a chroma-killer (blur / aggressive smoothing) flattens it. This
    returns ``std(chroma_test) / std(chroma_gt)`` over the interior — 1.0 = full
    chroma recovered, →0 = the colour grating smeared to a flat field.

    Measured in the (a*, b*) chroma PLANE and PROJECTED onto the ground-truth chroma
    modulation, so it captures the chroma that is actually ALIGNED with the real
    grating and REJECTS orthogonal aliasing chroma (a raw std would conflate the two
    and read >1 from aliasing inflation). Concretely: zero-mean the test and GT (a*,
    b*) signals over the interior, then recovery = <test, gt> / <gt, gt> — the
    least-squares scale of GT present in the reconstruction. 1.0 = the real colour
    modulation fully survived; →0 = smeared to a flat field; the projection discards
    the false-colour component that does not co-vary with the true pattern.

    INTERPRETATION (sampling limit): near Nyquist every Bayer demosaic attenuates the
    real grating, so read this RELATIVE to the baseline (rcd): a method recovering
    SUBSTANTIALLY LESS than rcd is smearing real colour (the falsifier). Inputs are
    LINEAR chart RGB."""
    lab_t = _xyz_to_lab(xyz_from_linear_rgb(np.clip(test_rgb, 0.0, 1.0)))
    lab_g = _xyz_to_lab(xyz_from_linear_rgb(np.clip(gt_rgb, 0.0, 1.0)))
    b = border
    # (a*, b*) vectors over the interior, mean-removed so we measure MODULATION.
    t = lab_t[b:-b, b:-b, 1:3].reshape(-1, 2)
    g = lab_g[b:-b, b:-b, 1:3].reshape(-1, 2)
    t = t - t.mean(axis=0)
    g = g - g.mean(axis=0)
    gg = float(np.sum(g * g))
    if gg <= 1e-9:
        return 0.0
    # Least-squares projection of the true chroma modulation onto the reconstruction:
    # the scalar a minimising ||t - a*g||, = <t,g>/<g,g>. Rejects aliasing orthogonal
    # to the true pattern (which a raw-std ratio would wrongly count as "recovery").
    return float(np.sum(t * g) / gg)


def falsecolor_chroma_energy(
    test_rgb: np.ndarray,
    neutral_mask: np.ndarray,
) -> float:
    """Mean CIELAB chroma sqrt(a*^2+b*^2) of the reconstruction inside a region
    that is NEUTRAL in the ground truth (docs §5 / §8: "chroma energy where
    GT-chroma ≈ 0 = false-colour map").

    A perfect demosaic of a neutral (grey/achromatic) region produces zero chroma;
    any chroma there is demosaic-introduced FALSE COLOUR — the exact moiré/colour-
    aliasing artifact CPSNR is blind to. Lower is better; a perfect-neutral
    reconstruction -> ~0 (the oracle). `test_rgb` is LINEAR chart RGB (clipped to
    [0,1]); `neutral_mask` is True where GT is achromatic.
    """
    xyz = xyz_from_linear_rgb(np.clip(test_rgb, 0.0, 1.0))
    lab = _xyz_to_lab(xyz)
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    return float(np.mean(chroma[neutral_mask])) if neutral_mask.any() else 0.0


# ----------------------------------------------------------------------------
# ISO 12233 slanted-edge eSFR -> MTF50P (sharpening-robust).
# ----------------------------------------------------------------------------


def mtf50p(
    image: np.ndarray,
    *,
    channel: str = "luma",
    oversample: int = 4,
) -> float:
    """MTF50P (cycles/pixel) from a single near-vertical slanted edge via the
    ISO 12233 slanted-edge (e-SFR) method.

    Steps (ISO 12233:2023, slanted-edge SFR): take the edge-channel plane; per
    row, locate the sub-pixel edge centroid from the derivative; bin all rows'
    samples onto a common `oversample`x-finer axis by their distance from a
    fitted edge line -> a super-resolved Edge Spread Function; differentiate ->
    Line Spread Function; window (Hann) and FFT -> MTF; correct for the binning
    aperture. Return the frequency where MTF falls to **50% of its PEAK** (not of
    DC): MTF50 rewards sharpening (peaking lifts MTF[0]'s neighbourhood), MTF50P
    is sharpening-robust (Imatest; §5). Edge slant must be ~5deg and >2deg off
    0/45/90 for valid binning.

    `image` is (H,W,3) in [0,1] (or 2-D); a near-vertical dark|light edge with a
    few px margin. Returns cycles/pixel; higher = sharper/more resolution.
    """
    img = np.asarray(image, dtype=np.float64)
    if img.ndim == 3:
        if channel == "luma":
            plane = 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]
        else:
            plane = img[..., {"R": 0, "G": 1, "B": 2}[channel]]
    else:
        plane = img
    h, w = plane.shape

    # Per-row sub-pixel edge location = centroid of |d/dx| (derivative weighting).
    dxp = np.diff(plane, axis=1)
    xs = np.arange(dxp.shape[1]) + 0.5
    wgt = np.abs(dxp)
    wsum = wgt.sum(axis=1)
    valid = wsum > 1e-9
    centroids = np.full(h, np.nan)
    centroids[valid] = (wgt[valid] * xs).sum(axis=1) / wsum[valid]
    rows = np.arange(h)[valid]
    cen = centroids[valid]
    if rows.size < 4:
        return float("nan")
    # Fit edge line x = a*row + b (the slant); project every pixel onto distance
    # from this line, then bin onto an oversampled axis (the eSFR super-resolve).
    a, b = np.polyfit(rows.astype(np.float64), cen, 1)

    yy, xx = np.indices((h, w))
    dist = xx - (a * yy + b)  # signed distance from the edge, in px
    dmin, dmax = dist.min(), dist.max()
    nbins = int(np.ceil((dmax - dmin) * oversample))
    if nbins < 8:
        return float("nan")
    idx = np.clip(((dist - dmin) * oversample).astype(int), 0, nbins - 1)
    flat_idx = idx.ravel()
    flat_val = plane.ravel()
    esf_sum = np.bincount(flat_idx, weights=flat_val, minlength=nbins)
    esf_cnt = np.bincount(flat_idx, minlength=nbins).astype(np.float64)
    good = esf_cnt > 0
    if good.sum() < 8:
        return float("nan")
    esf = np.interp(np.arange(nbins), np.arange(nbins)[good], esf_sum[good] / esf_cnt[good])

    # ESF -> LSF (derivative) -> window -> MTF (|FFT|), normalised to its own peak.
    lsf = np.diff(esf)
    lsf = lsf - lsf.mean()
    win = np.hanning(lsf.size)
    mtf = np.abs(np.fft.rfft(lsf * win))
    if mtf.max() <= 0:
        return float("nan")
    # Aperture correction for the finite bin width (sinc); freq axis in cyc/px.
    freq = np.fft.rfftfreq(lsf.size, d=1.0 / oversample)
    with np.errstate(divide="ignore", invalid="ignore"):
        sinc = np.sinc(freq / oversample)
        mtf = np.where(sinc > 1e-6, mtf / sinc, mtf)
    peak = mtf.max()
    half = 0.5 * peak
    # First downward crossing of 50%-of-peak, after the peak location.
    pk = int(np.argmax(mtf))
    below = np.where(mtf[pk:] <= half)[0]
    if below.size == 0:
        return float(freq[-1])
    j = pk + below[0]
    if j == 0:
        return float(freq[0])
    f0, f1 = freq[j - 1], freq[j]
    m0, m1 = mtf[j - 1], mtf[j]
    if m0 == m1:
        return float(f1)
    return float(f0 + (half - m0) * (f1 - f0) / (m1 - m0))
