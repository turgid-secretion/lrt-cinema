"""Layer 1 — implementation-correctness oracles for the colour/transfer path.

The *certitude engine* of the deterministic validation harness (see
`docs/VALIDATION.md`). Ground truth here is the pipeline's OWN defined maths,
re-implemented independently from published matrices and the sRGB spec — NOT via
`colour-science` (which `output.py` uses). So a wrong colourspace name, a
transposed matrix, the wrong chromatic-adaptation transform, or the wrong
transfer function in `output.py` is caught.

This axis is distinct from *absolute colorimetric accuracy* (Layer 2, vs CIE
truth from spectra), which has an irreducible nonzero floor (the DCP matrix is a
least-squares fit — real sensors violate the Luther condition). DO NOT conflate:
- implementation correctness → expected ~0 (matrix-rounding tolerance);
- absolute accuracy → expected nonzero (profile-fit floor).

Coverage is the full value range *including extremes* (near-black, the sRGB
toe/knee, clip, overrange >1, primaries, out-of-gamut) — a spectral chart under
a normal illuminant can't reach those; deliberate value injection (here) does.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

colour = pytest.importorskip("colour")  # noqa: F841  (output.py needs it; gate import)

import lrt_cinema.develop_ops as _develop_ops  # noqa: E402  (module attrs for the OKLCh Bradford guard)
from lrt_cinema.dcp import DCPProfile, HsvCube, interpolate_color_matrix  # noqa: E402
from lrt_cinema.develop_ops import (  # noqa: E402
    _CG_CHROMA_LOG_STRENGTH,
    _CG_CHROMA_STRENGTH,
    _CG_LUM_LOG_STRENGTH,
    _CG_LUM_STRENGTH,
    _CG_ZONE_PROXY_ANCHOR,
    _CG_ZONE_PROXY_WHITE,
    _DR_ANCHOR,
    _DR_BLEND_HALFWIDTH_ANCHOR,
    _DR_BLEND_HALFWIDTH_BREAK,
    _DR_BREAK_STOPS,
    _DR_EPS,
    _DR_LOG_ANCHOR,
    _DR_SLOPE_GAIN_K,
    _HSL_BAND_CENTERS_HEX,
    _HSL_HUE_MAX_HEX,
    _HSL_LUM_SAT_GATE,
    _OKLCH_BAND_CENTERS_DEG,
    _OKLCH_HUE_MAX_DEG,
    _OKLCH_LUM_CHROMA_GATE,
    _PROPHOTO_LUMINANCE,
    _TC_CLARITY_GAIN,
    _TC_GUIDED_EPS,
    _TC_MIDTONE_SIGMA,
    _TC_RADIUS_COARSE,
    _TC_RADIUS_FINE,
    _TC_TEXTURE_GAIN,
    _apply_color_grade_perceptual,
    _apply_hsl_perceptual,
    _color_grade_zone_weights,
    _dr_compress_luminance,
    apply_color_grade,
    apply_dr_compression,
    apply_hsl,
    apply_texture_clarity,
)
from lrt_cinema.ir import ColorGrade, HslBands  # noqa: E402
from lrt_cinema.lut3d_baker import (  # noqa: E402
    _apply_hsv_cube,
    _hsv_to_rgb_dcp,
    _rgb_to_hsv_dcp,
)
from lrt_cinema.output import (  # noqa: E402
    _RGC_LIMIT,
    _RGC_POWER,
    _RGC_THRESHOLD,
    _aces_rgc_compress_ap1,
    _prophoto_to_display,
    write_tiff_display,
)
from lrt_cinema.pipeline import (  # noqa: E402
    DngSplineSolver,
    apply_rgb_tone,
    make_exposure_ramp,
)

# ---------------------------------------------------------------------------
# Independent, spec-sourced re-implementation (the oracle)
# ---------------------------------------------------------------------------

# ROMM/ProPhoto linear → XYZ(D50).
_M_PP_LIN_TO_XYZ_D50 = np.array([
    [0.7976749, 0.1351917, 0.0313534],
    [0.2880402, 0.7118741, 0.0000857],
    [0.0000000, 0.0000000, 0.8252100],
])
# Bradford chromatic adaptation D50 → D65.
_M_BRADFORD_D50_TO_D65 = np.array([
    [0.9555766, -0.0230393, 0.0631636],
    [-0.0282895, 1.0099416, 0.0210077],
    [0.0122982, -0.0204830, 1.3299098],
])
# XYZ(D65) → linear sRGB (IEC 61966-2-1).
_M_XYZ_D65_TO_SRGB = np.array([
    [3.2404542, -1.5371385, -0.4985314],
    [-0.9692660, 1.8760108, 0.0415560],
    [0.0556434, -0.2040259, 1.0572252],
])

# Agreement floor between these 7-digit matrices and colour-science's
# full-precision path. Measured worst-case 3.6e-4 linear; a real bug
# (transpose / wrong CAT / wrong transfer) is 0.01–0.5, orders above this.
_ORACLE_TOL = 2e-3


def _srgb_oetf(linear: np.ndarray) -> np.ndarray:
    """Analytic sRGB opto-electronic transfer function (IEC 61966-2-1)."""
    linear = np.asarray(linear, dtype=np.float64)
    return np.where(
        linear <= 0.0031308,
        12.92 * linear,
        1.055 * np.power(np.clip(linear, 0.0, None), 1.0 / 2.4) - 0.055,
    )


def _oracle_prophoto_to_srgb_linear(pp: np.ndarray) -> np.ndarray:
    pp = np.asarray(pp, dtype=np.float64)
    xyz_d50 = pp @ _M_PP_LIN_TO_XYZ_D50.T
    xyz_d65 = xyz_d50 @ _M_BRADFORD_D50_TO_D65.T
    return xyz_d65 @ _M_XYZ_D65_TO_SRGB.T


def _oracle_prophoto_to_srgb_encoded(pp: np.ndarray) -> np.ndarray:
    return _srgb_oetf(_oracle_prophoto_to_srgb_linear(pp))


# ---------------------------------------------------------------------------
# Guard the oracle itself (an oracle that is wrong is worse than none)
# ---------------------------------------------------------------------------


def test_oracle_neutral_axis_is_pure_oetf():
    """A linear neutral v (R=G=B) maps to sRGB neutral OETF(v) — both ProPhoto
    and sRGB normalise their own white to 1, so the achromatic axis is
    transfer-only. Independent of the matrices; pins the oracle's correctness."""
    for v in (0.0, 0.0031308, 0.05, 0.18, 0.5, 1.0):
        enc = _oracle_prophoto_to_srgb_encoded(np.array([[[v, v, v]]]))[0, 0]
        np.testing.assert_allclose(enc, _srgb_oetf(v), atol=1.5e-3)
        np.testing.assert_allclose(enc[0], enc[1], atol=1.5e-3)  # stays neutral


def test_oracle_known_srgb_values():
    """Hand-checked: OETF(0.18) ≈ 0.4614, OETF(1.0) = 1.0, OETF(0) = 0."""
    enc = _oracle_prophoto_to_srgb_encoded(
        np.array([[[0.18, 0.18, 0.18], [1.0, 1.0, 1.0], [0.0, 0.0, 0.0]]]),
    )[0]
    np.testing.assert_allclose(enc[0], 0.4614, atol=2e-3)
    np.testing.assert_allclose(enc[1], 1.0, atol=2e-3)
    np.testing.assert_allclose(enc[2], 0.0, atol=1e-6)


# ---------------------------------------------------------------------------
# Implementation correctness: output._prophoto_to_display == oracle (~0)
# ---------------------------------------------------------------------------


def test_display_transform_matches_oracle_in_gamut():
    """The real sRGB display transform must equal the independent oracle across
    in-gamut neutrals + mild colours, to matrix-rounding tolerance."""
    pp = np.array([[
        [0.05, 0.05, 0.05], [0.18, 0.18, 0.18], [0.5, 0.5, 0.5],
        [1.0, 1.0, 1.0], [0.35, 0.22, 0.14], [0.6, 0.55, 0.2],
    ]])
    got = _prophoto_to_display(pp, "srgb")
    want = _oracle_prophoto_to_srgb_encoded(pp)
    np.testing.assert_allclose(got, want, atol=_ORACLE_TOL)


def test_display_transform_near_black_toe():
    """The sRGB toe (linear ≤ 0.0031308, slope 12.92) is where small errors
    amplify — pin it explicitly."""
    pp = np.array([[[1e-4, 1e-4, 1e-4], [2e-3, 2e-3, 2e-3], [3e-3, 3e-3, 3e-3]]])
    np.testing.assert_allclose(
        _prophoto_to_display(pp, "srgb"),
        _oracle_prophoto_to_srgb_encoded(pp),
        atol=_ORACLE_TOL,
    )


def test_write_tiff_display_quantizes_per_oracle(tmp_path):
    """End-to-end through the real writer: the 16-bit integers must match the
    oracle (encoded → clip [0,1] → round).

    Tolerance is the oracle's matrix-rounding floor — the 7-digit published
    matrices here disagree with colour-science's full-precision path by ~1.4e-4
    (≈9 LSB of 65535). That is the price of an *independent* oracle, NOT pipeline
    error; any real transfer/matrix bug is ~1300+ LSB, two orders above this."""
    tifffile = pytest.importorskip("tifffile")
    pp = np.array([[
        [0.18, 0.18, 0.18], [0.5, 0.5, 0.5], [0.02, 0.02, 0.02], [0.9, 0.9, 0.9],
    ]])
    dst = write_tiff_display(pp, tmp_path / "q.tif", colorspace="srgb", bit_depth=16)
    got = tifffile.imread(str(dst)).astype(np.int64)
    want = np.round(np.clip(_oracle_prophoto_to_srgb_encoded(pp), 0, 1) * 65535).astype(np.int64)
    assert np.max(np.abs(got - want)) <= 16, f"max LSB error {np.max(np.abs(got - want))}"


def test_write_tiff_display_extremes_clip(tmp_path):
    """Overrange highlights clip to white; sub-black clips to 0; no NaN/crash."""
    tifffile = pytest.importorskip("tifffile")
    pp = np.array([[[4.0, 4.0, 4.0], [-0.2, -0.2, -0.2], [0.0, 0.0, 0.0]]])
    rt = tifffile.imread(str(write_tiff_display(pp, tmp_path / "x.tif")))
    assert rt[0, 0, 0] == 65535  # +overrange → white
    assert rt[0, 1, 0] == 0      # sub-black → 0
    assert rt[0, 2, 0] == 0


# ---------------------------------------------------------------------------
# Sensitivity: the oracle MUST flag a deliberately injected bug (else it is a
# rubber stamp). Proves discriminating power per the harness design bar.
# ---------------------------------------------------------------------------


def test_oracle_detects_wrong_transfer():
    """If the transfer were a plain 2.2 gamma instead of the sRGB OETF, the
    oracle would diverge far above tolerance — confirms it would catch it."""
    pp = np.array([[[0.18, 0.18, 0.18], [0.5, 0.5, 0.5]]])
    correct = _oracle_prophoto_to_srgb_encoded(pp)
    wrong = np.power(np.clip(_oracle_prophoto_to_srgb_linear(pp), 0, 1), 1 / 2.2)
    assert np.max(np.abs(correct - wrong)) > 5e-3


def test_oracle_detects_transposed_matrix():
    """A transposed XYZ→sRGB matrix (a classic real bug) diverges grossly."""
    pp = np.array([[[0.35, 0.22, 0.14], [0.18, 0.18, 0.18]]])
    correct = _oracle_prophoto_to_srgb_linear(pp)
    xyz_d50 = pp @ _M_PP_LIN_TO_XYZ_D50.T
    xyz_d65 = xyz_d50 @ _M_BRADFORD_D50_TO_D65.T
    wrong = xyz_d65 @ _M_XYZ_D65_TO_SRGB  # NOT transposed → bug
    assert np.max(np.abs(correct - wrong)) > 5e-2


# ===========================================================================
# Layer-1 oracles for the render-math ops (v0.8 extension).
#
# Same philosophy as the sRGB display oracle above: an independent re-derivation
# of what each op MUST do — from the Adobe spec/source or from first principles
# — held against the real implementation (expect ~0), plus a deliberately
# injected bug to prove the check discriminates. These ops feed the rendered
# (Stage-9) path; the colorimetric tap (Stages 3/4) sits upstream of all of
# them, which is why absolute accuracy is measured there (test_colorimetric.py).
# ===========================================================================


# ---------------------------------------------------------------------------
# ExposureRamp (dng_function_exposure_ramp, dng_render.cpp:50-103)
# ---------------------------------------------------------------------------


def _oracle_exposure_ramp(
    x, exposure, shadows=5.0, shadow_scale=1.0, stage3_gain=1.0, support_overrange=False,
):
    """Independent scalar transcription of Adobe's three-region exposure ramp:
    flat 0 below the shadow knee, a quadratic knee, then a linear slope (clamped
    at 1 unless overrange). Looped per-element — a structurally different
    formulation from the vectorised production code, so a transcription typo in
    either surfaces as disagreement."""
    x = np.asarray(x, dtype=np.float64)
    white = 1.0 / (2.0 ** max(0.0, exposure))
    black = min(shadows * shadow_scale * stage3_gain * 0.001, 0.99 * white)
    slope = 1.0 / (white - black) if white > black else 1.0
    radius = min(0.5 * black, (1.0 / 16.0) / slope) if slope > 0 else 0.0
    qscale = slope / (4.0 * radius) if radius > 0.0 else 0.0
    floor_t, ceil_t = black - radius, black + radius
    out = np.empty(x.size, dtype=np.float64)
    for i, xi in enumerate(x.ravel()):
        if xi <= floor_t:
            out[i] = 0.0
        elif xi >= ceil_t:
            lin = (xi - black) * slope
            out[i] = lin if support_overrange else min(lin, 1.0)
        else:
            out[i] = qscale * (xi - floor_t) ** 2
    return out.reshape(x.shape)


def test_exposure_ramp_matches_oracle():
    """The production ramp must equal the independent transcription across the
    whole range, for both DefaultBlackRender modes (shadows 5 = Auto, 0 = None)
    and the overrange flag."""
    x = np.linspace(0.0, 2.0, 401)
    for shadows in (5.0, 0.0):
        for exposure in (0.0, 0.5, 1.0, -0.3):
            for overrange in (False, True):
                got = make_exposure_ramp(
                    exposure=exposure, shadows=shadows, support_overrange=overrange,
                )(x.astype(np.float64))
                want = _oracle_exposure_ramp(
                    x, exposure, shadows=shadows, support_overrange=overrange,
                )
                np.testing.assert_allclose(got, want, atol=1e-12)


def test_exposure_ramp_analytic_pins():
    """First-principles pins (independent of the formula):
      * shadows=0 (no black lift) + exposure=0 → identity on [0, 1];
      * exposure=1 EV → +1 stop = ×2 gain, clamped at 1 (or not, with overrange);
      * exposure=0, shadows=5 (Auto) → white maps to 1, black floor to 0."""
    x = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
    np.testing.assert_allclose(make_exposure_ramp(0.0, shadows=0.0)(x), x, atol=1e-9)

    ramp1 = make_exposure_ramp(1.0, shadows=0.0)
    np.testing.assert_allclose(ramp1(np.array([0.25, 0.5])), [0.5, 1.0], atol=1e-9)
    assert ramp1(np.array([0.6]))[0] == pytest.approx(1.0)        # clamped
    ramp1_over = make_exposure_ramp(1.0, shadows=0.0, support_overrange=True)
    assert ramp1_over(np.array([0.6]))[0] == pytest.approx(1.2)   # overrange kept

    auto = make_exposure_ramp(0.0, shadows=5.0)
    assert auto(np.array([0.0]))[0] == pytest.approx(0.0)
    assert auto(np.array([1.0]))[0] == pytest.approx(1.0, abs=1e-9)


def test_exposure_ramp_oracle_detects_sign_flipped_knee():
    """If the knee thresholds were swapped (black+radius / black-radius flipped,
    a classic transcription bug), the ramp would diverge in the shadow region —
    confirming the oracle would catch it."""
    x = np.linspace(0.0, 0.05, 200)
    correct = _oracle_exposure_ramp(x, 0.0, shadows=5.0)

    # Buggy variant: floor/ceil thresholds swapped.
    white, black = 1.0, 0.005
    slope = 1.0 / (white - black)
    radius = min(0.5 * black, (1.0 / 16.0) / slope)
    qscale = slope / (4.0 * radius)
    floor_t, ceil_t = black + radius, black - radius   # SWAPPED → bug
    buggy = np.where(
        x >= ceil_t, np.minimum((x - black) * slope, 1.0),
        np.where(x <= floor_t, 0.0, qscale * (x - floor_t) ** 2),
    )
    assert np.max(np.abs(correct - buggy)) > 1e-3


# ---------------------------------------------------------------------------
# ProfileToneCurve — Hermite C2 natural cubic spline (dng_spline.cpp port)
# ---------------------------------------------------------------------------


def test_tone_curve_spline_interpolation_and_clamp():
    """Algorithm-independent properties any correct tone-curve spline satisfies:
    it passes through every control point exactly, clamps to the endpoint values
    outside the knot range, and reproduces the identity curve to machine
    precision."""
    x_knots = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
    y_knots = np.array([0.0, 0.18, 0.45, 0.78, 1.0])
    solver = DngSplineSolver(x_knots, y_knots)
    np.testing.assert_allclose(solver.evaluate(x_knots), y_knots, atol=1e-9)
    np.testing.assert_allclose(solver.evaluate(np.array([-1.0, 2.0])), [0.0, 1.0])

    ident = DngSplineSolver(np.array([0.0, 0.5, 1.0]), np.array([0.0, 0.5, 1.0]))
    xs = np.linspace(0.0, 1.0, 64)
    np.testing.assert_allclose(ident.evaluate(xs), xs, atol=1e-9)


def test_tone_curve_spline_matches_independent_scipy_natural_cubic():
    """The DNG spline uses second-derivative-zero (natural) boundaries. SciPy's
    CubicSpline is a wholly independent solver; with bc_type='natural' it must
    agree to ~0. The same curve under 'not-a-knot' boundaries does NOT agree —
    so this cross-check is discriminating, not a tautology (a wrong boundary
    condition in the port would surface as divergence from 'natural')."""
    interp = pytest.importorskip("scipy.interpolate")
    x_knots = np.array([0.0, 0.2, 0.45, 0.7, 0.85, 1.0])
    y_knots = np.array([0.0, 0.10, 0.35, 0.68, 0.86, 1.0])
    solver = DngSplineSolver(x_knots, y_knots)
    xs = np.linspace(0.0, 1.0, 256)

    natural = interp.CubicSpline(x_knots, y_knots, bc_type="natural")
    np.testing.assert_allclose(solver.evaluate(xs), natural(xs), atol=1e-5)

    not_a_knot = interp.CubicSpline(x_knots, y_knots, bc_type="not-a-knot")
    assert np.max(np.abs(solver.evaluate(xs) - not_a_knot(xs))) > 1e-3


# ---------------------------------------------------------------------------
# Hue/saturation-preserving RGB tone (DNG SDK RefBaselineRGBTone)
# ---------------------------------------------------------------------------


def _ref_baseline_rgb_tone(rgb, curve):
    """Independent scalar port of `RefBaselineRGBTone` (dng_reference.cpp:1871),
    written as the explicit 7-case max/mid/min sort — a DIFFERENT code path from
    `pipeline.apply_rgb_tone`'s vectorised argsort, so agreement is a real
    cross-check, not a tautology. Curve the max & min channels; linearly
    interpolate the middle one to preserve its position between them."""
    def c(x):
        return float(np.clip(curve(np.asarray(float(x))), 0.0, 1.0))

    def tone(a, b, d):  # a >= b >= d (max, mid, min)
        aa, dd = c(a), c(d)
        bb = dd + ((aa - dd) * (b - d) / (a - d)) if a > d else c(b)
        return aa, bb, dd

    out = np.empty_like(rgb, dtype=np.float64)
    flat = np.clip(rgb.reshape(-1, 3), 0.0, 1.0)
    of = out.reshape(-1, 3)
    for i in range(flat.shape[0]):
        r, g, b = (float(v) for v in flat[i])
        if r >= g:
            if g > b:            # r >= g > b
                rr, gg, bb = tone(r, g, b)
            elif b > r:          # b > r >= g
                bb, rr, gg = tone(b, r, g)
            elif b > g:          # r >= b > g
                rr, bb, gg = tone(r, b, g)
            else:                # r >= g == b
                rr = c(r)
                gg = c(g)
                bb = gg
        else:
            if r >= b:           # g > r >= b
                gg, rr, bb = tone(g, r, b)
            elif b > g:          # b > g > r
                bb, gg, rr = tone(b, g, r)
            else:                # g >= b > r
                gg, bb, rr = tone(g, b, r)
        of[i] = (rr, gg, bb)
    return out


def test_rgb_tone_matches_independent_refbaseline_oracle():
    """`apply_rgb_tone` must equal the explicit 7-case RefBaselineRGBTone port to
    machine precision across random triples plus the tie/extreme edge cases
    (neutral r==g==b, two-equal r==g>b, clip, near-black)."""
    solver = DngSplineSolver(
        np.array([0.0, 0.2, 0.45, 0.7, 1.0]),
        np.array([0.0, 0.32, 0.62, 0.84, 1.0]),
    )
    rng = np.random.default_rng(20260530)
    rgb = rng.random((4096, 3)).astype(np.float32)
    edge = np.array([
        [0.3, 0.3, 0.3], [0.5, 0.5, 0.2], [0.2, 0.5, 0.5], [0.5, 0.2, 0.5],
        [0.0, 0.0, 0.0], [1.0, 1.0, 1.0], [0.6, 0.4, 0.1], [0.05, 0.5, 0.95],
    ], dtype=np.float32)
    rgb = np.concatenate([rgb, edge], axis=0)

    got = apply_rgb_tone(rgb, solver.evaluate)
    want = _ref_baseline_rgb_tone(rgb, solver.evaluate)
    np.testing.assert_allclose(got, want, atol=1e-6)


def test_rgb_tone_is_not_per_channel_but_preserves_neutrals():
    """Discriminating: the hue-preserving tone must DIFFER from naive per-channel
    on chromatic pixels (else it's the bug we removed), yet be identical on
    neutrals (r==g==b → curve(v) on all three)."""
    solver = DngSplineSolver(
        np.array([0.0, 0.5, 1.0]), np.array([0.0, 0.72, 1.0]),  # strongly convex
    )
    def per_channel(rgb):
        return np.stack(
            [np.clip(solver.evaluate(rgb[..., k]), 0, 1) for k in range(3)], axis=-1,
        )

    chroma = np.array([[0.6, 0.35, 0.1]], dtype=np.float32)
    assert np.max(np.abs(apply_rgb_tone(chroma, solver.evaluate)
                         - per_channel(chroma))) > 1e-2

    neutral = np.array([[0.4, 0.4, 0.4], [0.7, 0.7, 0.7]], dtype=np.float32)
    np.testing.assert_allclose(
        apply_rgb_tone(neutral, solver.evaluate), per_channel(neutral), atol=1e-6,
    )


# ---------------------------------------------------------------------------
# ColorMatrix kelvin interpolation (DNG SDK InterpolateColorMatrix — mired blend)
# ---------------------------------------------------------------------------


def _oracle_mired_blend(m_lo, m_hi, kelvin, k_lo, k_hi):
    """Independent mired (reciprocal-temperature) linear blend, from the DNG
    SDK convention: clamp outside [k_lo, k_hi]; inside, blend by
    f = (1/k − 1/k_lo)/(1/k_hi − 1/k_lo)."""
    if kelvin <= k_lo:
        return m_lo
    if kelvin >= k_hi:
        return m_hi
    f = (1.0 / kelvin - 1.0 / k_lo) / (1.0 / k_hi - 1.0 / k_lo)
    return (1.0 - f) * m_lo + f * m_hi


def _two_illuminant_profile():
    m1 = np.array([[1.0, -0.3, -0.05], [-0.4, 1.2, 0.15], [0.02, -0.2, 1.1]])
    m2 = np.array([[0.9, -0.2, -0.10], [-0.3, 1.1, 0.10], [0.05, -0.1, 1.2]])
    return DCPProfile(
        color_matrix_1=m1, color_matrix_2=m2, kelvin_1=2856.0, kelvin_2=6504.0,
    )


def test_color_matrix_interpolation_matches_oracle():
    """interpolate_color_matrix must equal the independent mired blend at the
    endpoints and an interior kelvin."""
    prof = _two_illuminant_profile()
    m_lo, m_hi = prof.color_matrix_1, prof.color_matrix_2
    for k in (2000.0, 2856.0, 4000.0, 5000.0, 6504.0, 9000.0):
        np.testing.assert_allclose(
            interpolate_color_matrix(prof, k),
            _oracle_mired_blend(m_lo, m_hi, k, 2856.0, 6504.0),
            atol=1e-12,
        )


def test_color_matrix_interpolation_detects_linear_in_kelvin_bug():
    """A linear-in-kelvin blend (the wrong but tempting f = (k−k_lo)/(k_hi−k_lo))
    diverges from the correct mired blend at an interior kelvin — confirms the
    oracle catches it."""
    prof = _two_illuminant_profile()
    k = 4000.0
    mired = _oracle_mired_blend(prof.color_matrix_1, prof.color_matrix_2, k, 2856.0, 6504.0)
    f_lin = (k - 2856.0) / (6504.0 - 2856.0)
    linear = (1.0 - f_lin) * prof.color_matrix_1 + f_lin * prof.color_matrix_2
    assert np.max(np.abs(mired - linear)) > 1e-3


# ---------------------------------------------------------------------------
# HueSatMap / LookTable HSV cube (DNG §"Hue Sat Map", trilinear hsdApply)
# ---------------------------------------------------------------------------


def _uniform_cube(hue_shift_deg, sat_scale, val_scale, dims=(6, 2, 1)):
    h_div, s_div, v_div = dims
    cube = np.zeros((v_div, h_div, s_div, 3), dtype=np.float32)
    cube[..., 0] = hue_shift_deg
    cube[..., 1] = sat_scale
    cube[..., 2] = val_scale
    return HsvCube(
        hue_divisions=h_div, sat_divisions=s_div, val_divisions=v_div,
        srgb_gamma=False, data_1=cube,
    )


def test_hsv_cube_identity_is_a_no_op():
    """A (hueShift 0, satScale 1, valScale 1) cube must leave RGB unchanged
    through the rgb→hsv→cube→rgb round-trip — proves the round-trip itself is
    transparent (any net bias would show here, against the input)."""
    rgb = np.array([[
        [0.6, 0.3, 0.2], [0.2, 0.5, 0.4], [0.3, 0.3, 0.3], [0.05, 0.4, 0.7],
    ]], dtype=np.float32)
    cube = _uniform_cube(0.0, 1.0, 1.0)
    h, s, v, valid = _rgb_to_hsv_dcp(rgb)
    h2, s2, v2 = _apply_hsv_cube(h, s, v, cube.data_1, cube)
    out = _hsv_to_rgb_dcp(h2, s2, v2)
    np.testing.assert_allclose(np.where(valid[..., None], out, rgb), rgb, atol=1e-5)


def test_hsv_cube_uniform_hue_rotation_is_exact():
    """A uniform +120° hue-shift cube must rotate hue by exactly 120° (= 2.0 in
    the DNG [0,6) hue space) for chromatic pixels, and leave a neutral grey a
    neutral grey (hue is moot when sat=0). Pins the cube's hue-shift semantics
    independently of the trilinear weights (uniform cube → weights irrelevant)."""
    rgb = np.array([[[0.6, 0.3, 0.2], [0.3, 0.3, 0.3]]], dtype=np.float32)
    cube = _uniform_cube(120.0, 1.0, 1.0)
    h, s, v, _ = _rgb_to_hsv_dcp(rgb)
    h2, s2, v2 = _apply_hsv_cube(h, s, v, cube.data_1, cube)

    expected_h = (h[0, 0] + 120.0 * (6.0 / 360.0)) % 6.0   # chromatic pixel
    assert h2[0, 0] == pytest.approx(expected_h, abs=1e-5)
    # Neutral pixel: sat≈0, so it must come back out neutral after hsv→rgb.
    out = _hsv_to_rgb_dcp(h2, s2, v2)
    np.testing.assert_allclose(out[0, 1], [0.3, 0.3, 0.3], atol=1e-5)


def test_hsv_cube_rotation_detects_wrong_degree_scale():
    """Sensitivity leg for the HSV cube: the hue shift converts degrees→[0,6)
    via 6/360. A wrong scale (e.g. 6/180, a doubled rotation — a plausible
    transcription bug) lands at a clearly different hue, so the real op's exact
    match to the 6/360 expectation is discriminating, not a rubber stamp."""
    rgb = np.array([[[0.6, 0.3, 0.2]]], dtype=np.float32)
    cube = _uniform_cube(120.0, 1.0, 1.0)
    h, s, v, _ = _rgb_to_hsv_dcp(rgb)
    h2, _, _ = _apply_hsv_cube(h, s, v, cube.data_1, cube)
    correct = (h[0, 0] + 120.0 * (6.0 / 360.0)) % 6.0
    wrong = (h[0, 0] + 120.0 * (6.0 / 180.0)) % 6.0
    assert abs(correct - wrong) > 0.5, "the two degree scales must be distinguishable"
    assert abs(h2[0, 0] - correct) < 1e-5      # real op uses the correct scale
    assert abs(h2[0, 0] - wrong) > 0.5         # ... and is NOT the buggy scale


# ---------------------------------------------------------------------------
# HSL panel (develop_ops.apply_hsl) — 8 hue bands × {Hue, Saturation, Luminance}
#
# Axis-1 ground truth is an INDEPENDENT scalar reimplementation of apply_hsl's
# *defined* math: a hand-coded hexcone HSV round-trip + an explicit per-pixel
# band-segment search — a wholly different code path from the production
# vectorised `_rgb_to_hsv_dcp` / matmul-weighted form, so agreement is a real
# cross-check, not a tautology. (LR's exact band centres / slider magnitudes
# are closed-source; this validates our defined spec, NOT Lightroom fidelity —
# see VALIDATION.md "Validation axes".) `centers` / `hue_max` are injectable so
# the sensitivity legs can prove the check discriminates a wrong layout.
# ---------------------------------------------------------------------------


def _oracle_hsl(rgb, hsl, centers=None, hue_max=None):
    centers = list(_HSL_BAND_CENTERS_HEX) if centers is None else list(centers)
    hue_max = _HSL_HUE_MAX_HEX if hue_max is None else hue_max
    gate = _HSL_LUM_SAT_GATE
    hue_adj = [x / 100.0 * hue_max for x in hsl.hue]
    sat_fac = [1.0 + x / 100.0 for x in hsl.saturation]
    lum_fac = [1.0 + x / 100.0 for x in hsl.luminance]

    flat = rgb.reshape(-1, 3).astype(np.float64)
    out = np.empty_like(flat)
    for i in range(flat.shape[0]):
        r, g, b = (float(c) for c in flat[i])
        mx, mn = max(r, g, b), min(r, g, b)
        if mn < 0.0:                       # invalid (negative) pixel → passthrough
            out[i] = (r, g, b)
            continue
        v = mx
        delta = mx - mn
        s = 0.0 if mx <= 0.0 else delta / mx
        if delta <= 1e-10:
            h = 0.0
        elif r == mx:
            h = (g - b) / delta
        elif g == mx:
            h = 2.0 + (b - r) / delta
        else:
            h = 4.0 + (r - g) / delta
        h = h + 6.0 if h < 0.0 else h
        h = h - 6.0 if h >= 6.0 else h

        # Triangular band weights via an explicit per-segment search.
        w = [0.0] * 8
        for j in range(8):
            lo = centers[j]
            hi = centers[j + 1] if j < 7 else 6.0
            if lo <= h < hi:
                frac = (h - lo) / (hi - lo)
                w[j] += 1.0 - frac
                w[(j + 1) % 8] += frac

        hue_shift = sum(w[k] * hue_adj[k] for k in range(8))
        sat_mult = sum(w[k] * sat_fac[k] for k in range(8))
        lum_mult = sum(w[k] * lum_fac[k] for k in range(8))

        h2 = h + hue_shift
        h2 = h2 + 6.0 if h2 < 0.0 else h2
        h2 = h2 - 6.0 if h2 >= 6.0 else h2
        s2 = min(max(s * sat_mult, 0.0), 1.0)
        s_gate = min(max(s / gate, 0.0), 1.0)
        v2 = max(v * (1.0 + s_gate * (lum_mult - 1.0)), 0.0)

        sector = min(max(int(np.floor(h2)), 0), 5)
        f = h2 - np.floor(h2)
        p = v2 * (1.0 - s2)
        q = v2 * (1.0 - f * s2)
        t = v2 * (1.0 - (1.0 - f) * s2)
        out[i] = [
            (v2, t, p), (q, v2, p), (p, v2, t),
            (p, q, v2), (t, p, v2), (v2, p, q),
        ][sector]
    return out.reshape(rgb.shape)


def test_hsl_matches_independent_oracle():
    """apply_hsl must equal the independent scalar oracle to ~0 over random
    pixels + saturated / past-gamut-edge / neutral edge cases, with a non-trivial
    mix of Hue/Sat/Lum sliders set across several bands."""
    rng = np.random.default_rng(20260531)
    rgb = rng.random((2048, 3)).astype(np.float64).reshape(-1, 1, 3)
    edge = np.array([
        [1.0, 0.0, 0.0],   # primary red, on the gamut edge
        [0.0, 1.0, 0.0],   # primary green
        [0.05, 0.4, 0.95],  # saturated blue-ish
        [0.4, 0.4, 0.4],   # neutral (hue undefined)
        [0.95, 0.92, 0.05],  # saturated yellow
        [1.2, 0.1, 0.3],   # overrange + saturated (past the [0,1] box)
    ], dtype=np.float64).reshape(-1, 1, 3)
    rgb = np.concatenate([rgb, edge], axis=0)

    hsl = HslBands(
        hue=(20.0, -10.0, 0.0, 15.0, 0.0, -25.0, 0.0, 5.0),
        saturation=(40.0, 0.0, -30.0, 60.0, 0.0, 50.0, 0.0, -20.0),
        luminance=(-35.0, 0.0, 25.0, 0.0, 0.0, -40.0, 0.0, 30.0),
    )
    got = apply_hsl(rgb, hsl)
    want = _oracle_hsl(rgb, hsl)
    np.testing.assert_allclose(got, want, atol=1e-6)


def test_hsl_oracle_detects_wrong_band_centers():
    """Sensitivity: the band centres are load-bearing. A buggy oracle using
    evenly-spaced (45°) centres instead of the named-colour layout assigns a
    clearly different band weight, diverging on a band-targeted Luminance
    adjustment (chosen over Saturation so the [0,1] sat clamp can't mask the
    gap) — so the exact-match in the agreement test is discriminating."""
    rgb = np.array([[[0.15, 0.8, 0.1]]], dtype=np.float64)  # a saturated green pixel
    hsl = HslBands(luminance=(0.0, 0.0, 0.0, -50.0, 0.0, 0.0, 0.0, 0.0))  # Green Lum −50
    correct = _oracle_hsl(rgb, hsl)
    even = np.linspace(0.0, 6.0, 8, endpoint=False)  # 0,0.75,1.5,… — wrong layout
    wrong = _oracle_hsl(rgb, hsl, centers=even)
    assert np.max(np.abs(correct - wrong)) > 1e-2
    np.testing.assert_allclose(apply_hsl(rgb, hsl), correct, atol=1e-6)


def test_hsl_oracle_detects_wrong_hue_magnitude():
    """Sensitivity: a doubled Hue-slider magnitude (a plausible unit-scale bug)
    rotates a band's hue twice as far — the real op matches the defined
    magnitude and not the doubled one."""
    rgb = np.array([[[0.9, 0.2, 0.2]]], dtype=np.float64)  # a red pixel
    hsl = HslBands(hue=(100.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0))  # Red hue +100
    correct = _oracle_hsl(rgb, hsl)
    wrong = _oracle_hsl(rgb, hsl, hue_max=2.0 * _HSL_HUE_MAX_HEX)
    assert np.max(np.abs(correct - wrong)) > 1e-2
    np.testing.assert_allclose(apply_hsl(rgb, hsl), correct, atol=1e-6)


def test_hsl_identity_is_byte_exact_no_op():
    """The all-zero HslBands default must be a BYTE-exact no-op (the short-circuit
    returns the input before any lossy HSV round-trip). This is the structural
    guarantee that the ΔE ship gate is unaffected when no HSL is authored."""
    rgb = np.random.default_rng(1).random((16, 16, 3)).astype(np.float32)
    np.testing.assert_array_equal(apply_hsl(rgb, HslBands()), rgb)
    # An explicitly-all-zero (non-default-object) HslBands is identity too.
    np.testing.assert_array_equal(
        apply_hsl(rgb, HslBands(hue=(0.0,) * 8, saturation=(0.0,) * 8, luminance=(0.0,) * 8)),
        rgb,
    )


# ---------------------------------------------------------------------------
# PERCEPTUAL HSL (develop_ops._apply_hsl_perceptual) — hue-stable 8-band HSL in
# OKLCh on the ACEScg-master path (DECISIONS.md §7 step 3).
#
# Axis-1 ground truth is an INDEPENDENT scalar reimplementation of the defined
# math. Per contract 4 (test charter, lines 1-19) it HAND-ROLLS Ottosson's
# M1/M2 matrices + the signed cube-root and a hand-rolled D50↔D65 Bradford — it
# MUST NOT call `colour.XYZ_to_Oklab`/`Oklab_to_Oklch`, which is exactly what the
# production op uses (validating it against itself is a tautology that passes a
# transcription bug straight through). The oracle's Bradford uses the file's own
# 7-digit `_M_BRADFORD_D50_TO_D65` (a slightly different whitepoint precision than
# the production module constant) and `np.linalg.inv` for every inverse, so
# agreement with production is a real cross-check across two independent matrix
# sets, not a rubber stamp. `centers` / `hue_max` / `invert_bradford` are
# injectable so the sensitivity legs prove the check discriminates the
# load-bearing bugs (wrong band layout, doubled hue magnitude, inverted CAT).
# (LR's exact centres / slider magnitudes / the gate are closed-source; this
# validates our defined OKLCh spec, NOT Lightroom fidelity — see VALIDATION.md.)
# ---------------------------------------------------------------------------

# Ottosson's Oklab matrices (cite: Ottosson 2020). M1: XYZ(D65) → LMS; M2:
# nonlinear l'm's' → Lab. Hand-typed from the published values — the oracle does
# NOT import colour's MATRIX_1/MATRIX_2, so a transcription error in colour (or
# in production's use of it) cannot hide here.
_OKLAB_M1_XYZ_TO_LMS = np.array([
    [0.8189330101, 0.3618667424, -0.1288597137],
    [0.0329845436, 0.9293118715, 0.0361456387],
    [0.0482003018, 0.2643662691, 0.6338517070],
])
_OKLAB_M2_LMS_TO_LAB = np.array([
    [0.2104542553, 0.7936177850, -0.0040720468],
    [1.9779984951, -2.4285922050, 0.4505937099],
    [0.0259040371, 0.7827717662, -0.8086757660],
])


def _oracle_oklch_band_adjust(
    rgb, hsl, centers=None, hue_max=None, invert_bradford=False,
):
    """Independent scalar reimpl of `_apply_hsl_perceptual` (hand-rolled
    Ottosson + Bradford; NEVER colour's Oklab functions — contract 4)."""
    centers = list(_OKLCH_BAND_CENTERS_DEG) if centers is None else list(centers)
    hue_max = _OKLCH_HUE_MAX_DEG if hue_max is None else hue_max
    gate = _OKLCH_LUM_CHROMA_GATE
    hue_adj = [x / 100.0 * hue_max for x in hsl.hue]
    sat_fac = [1.0 + x / 100.0 for x in hsl.saturation]
    lum_fac = [1.0 + x / 100.0 for x in hsl.luminance]

    m_xyz50_to_pp = np.linalg.inv(_M_PP_LIN_TO_XYZ_D50)
    m_bradford_65_to_50 = np.linalg.inv(_M_BRADFORD_D50_TO_D65)
    m1_inv = np.linalg.inv(_OKLAB_M1_XYZ_TO_LMS)
    m2_inv = np.linalg.inv(_OKLAB_M2_LMS_TO_LAB)
    # invert_bradford swaps the CAT direction on BOTH legs (the wrong-whitepoint
    # bug: feeding D50 XYZ to Oklab as if D65, then "un-adapting" the wrong way).
    m_fwd = m_bradford_65_to_50 if invert_bradford else _M_BRADFORD_D50_TO_D65
    m_back = _M_BRADFORD_D50_TO_D65 if invert_bradford else m_bradford_65_to_50

    def cbrt(x):
        return math.copysign(abs(x) ** (1.0 / 3.0), x)

    flat = rgb.reshape(-1, 3).astype(np.float64)
    out = np.empty_like(flat)
    for i in range(flat.shape[0]):
        xyz50 = _M_PP_LIN_TO_XYZ_D50 @ flat[i]
        xyz65 = m_fwd @ xyz50
        lms = _OKLAB_M1_XYZ_TO_LMS @ xyz65
        lms_p = np.array([cbrt(v) for v in lms])
        lab = _OKLAB_M2_LMS_TO_LAB @ lms_p
        el, a, b = float(lab[0]), float(lab[1]), float(lab[2])
        c = math.hypot(a, b)
        h = math.degrees(math.atan2(b, a)) % 360.0

        w = [0.0] * 8
        for j in range(8):
            lo = centers[j]
            hi = centers[j + 1] if j < 7 else 360.0
            if lo <= h < hi:
                frac = (h - lo) / (hi - lo)
                w[j] += 1.0 - frac
                w[(j + 1) % 8] += frac

        hue_shift = sum(w[k] * hue_adj[k] for k in range(8))
        sat_mult = sum(w[k] * sat_fac[k] for k in range(8))
        lum_mult = sum(w[k] * lum_fac[k] for k in range(8))

        h2 = (h + hue_shift) % 360.0
        c2 = max(c * sat_mult, 0.0)
        c_gate = min(max(c / gate, 0.0), 1.0)
        l2 = max(el * (1.0 + c_gate * (lum_mult - 1.0)), 0.0)

        a2 = c2 * math.cos(math.radians(h2))
        b2 = c2 * math.sin(math.radians(h2))
        lms_p2 = m2_inv @ np.array([l2, a2, b2])
        lms2 = lms_p2 ** 3
        xyz65b = m1_inv @ lms2
        xyz50b = m_back @ xyz65b
        ppb = m_xyz50_to_pp @ xyz50b
        out[i] = np.maximum(ppb, 0.0)
    return out.reshape(rgb.shape)


# Saturated ProPhoto patches spanning the bands + neutrals + overrange — a grey
# wedge is blind to the camera-matrix chromatic rotation and the hue-gate (§0).
def _oklch_edge_patches():
    return np.array([
        [1.0, 0.0, 0.0],    # primary red (gamut edge)
        [0.0, 1.0, 0.0],    # primary green
        [0.0, 0.0, 1.0],    # primary blue
        [0.05, 0.4, 0.95],  # saturated blue
        [0.95, 0.92, 0.05],  # saturated yellow
        [0.6, 0.45, 0.4],   # pale skin (low chroma, a real colour)
        [0.4, 0.4, 0.4],    # neutral (hue undefined)
        [0.18, 0.18, 0.18],  # mid-grey neutral
        [1.2, 0.1, 0.3],    # overrange + saturated (past the [0,1] box)
    ], dtype=np.float64).reshape(-1, 1, 3)


def test_oklch_hsl_matches_oracle():
    """`_apply_hsl_perceptual` must equal the independent hand-rolled OKLCh oracle
    to ~1e-2 over random + saturated/neutral/overrange patches, with a non-trivial
    mix of Hue/Sat/Lum sliders across several bands. The floor is the two
    independent matrix sets (file 7-digit Bradford + np.linalg.inv vs production's
    colour-science path), NOT a bug."""
    rng = np.random.default_rng(20260531)
    rgb = (rng.random((2048, 3)) * 1.2).astype(np.float64).reshape(-1, 1, 3)
    rgb = np.concatenate([rgb, _oklch_edge_patches()], axis=0)
    hsl = HslBands(
        hue=(20.0, -10.0, 0.0, 15.0, 0.0, -25.0, 0.0, 5.0),
        saturation=(40.0, 0.0, -30.0, 60.0, 0.0, 50.0, 0.0, -20.0),
        luminance=(-35.0, 0.0, 25.0, 0.0, 0.0, -40.0, 0.0, 30.0),
    )
    got = _apply_hsl_perceptual(rgb, hsl)
    want = _oracle_oklch_band_adjust(rgb, hsl)
    np.testing.assert_allclose(got, want, atol=1e-2)


def test_oklch_oracle_detects_wrong_bradford_direction():
    """Sensitivity: the D50↔D65 Bradford direction is load-bearing (Ottosson's
    Oklab is D65-defined). Inverting the CAT diverges >5e-2 from the correct
    oracle — so production's match to the correct direction is discriminating, not
    a rubber stamp on a transposed/mis-directed matrix."""
    rgb = _oklch_edge_patches()
    hsl = HslBands(
        hue=(20.0, 0.0, 0.0, 15.0, 0.0, -25.0, 0.0, 0.0),
        saturation=(40.0, 0.0, -30.0, 60.0, 0.0, 50.0, 0.0, 0.0),
    )
    correct = _oracle_oklch_band_adjust(rgb, hsl)
    wrong = _oracle_oklch_band_adjust(rgb, hsl, invert_bradford=True)
    assert np.max(np.abs(correct - wrong)) > 5e-2
    np.testing.assert_allclose(_apply_hsl_perceptual(rgb, hsl), correct, atol=1e-2)


def test_oklch_oracle_detects_wrong_band_centers():
    """Sensitivity: the OKLCh band centres are load-bearing. A buggy oracle using
    evenly-spaced (45°) centres instead of the named-colour layout assigns a
    different band weight, diverging on a band-targeted Luminance adjustment
    (chosen over Saturation so a chroma floor cannot mask the gap)."""
    rgb = np.array([[[0.05, 0.4, 0.95]]], dtype=np.float64)  # saturated blue (~209° OKLCh)
    hsl = HslBands(luminance=(0.0, 0.0, 0.0, 0.0, 0.0, -50.0, 0.0, 0.0))  # Blue Lum −50
    correct = _oracle_oklch_band_adjust(rgb, hsl)
    even = np.linspace(0.0, 360.0, 8, endpoint=False)  # 0,45,90,… — wrong layout
    wrong = _oracle_oklch_band_adjust(rgb, hsl, centers=even)
    assert np.max(np.abs(correct - wrong)) > 1e-2
    np.testing.assert_allclose(_apply_hsl_perceptual(rgb, hsl), correct, atol=1e-2)


def test_oklch_oracle_detects_wrong_hue_magnitude():
    """Sensitivity: a doubled Hue-slider magnitude (a plausible unit-scale bug)
    rotates a band's hue twice as far — the real op matches the defined ±30°
    magnitude and not the doubled one."""
    rgb = np.array([[[0.9, 0.2, 0.2]]], dtype=np.float64)  # a red pixel
    hsl = HslBands(hue=(100.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0))  # Red hue +100
    correct = _oracle_oklch_band_adjust(rgb, hsl)
    wrong = _oracle_oklch_band_adjust(rgb, hsl, hue_max=2.0 * _OKLCH_HUE_MAX_DEG)
    assert np.max(np.abs(correct - wrong)) > 1e-2
    np.testing.assert_allclose(_apply_hsl_perceptual(rgb, hsl), correct, atol=1e-2)


def test_oklch_hsl_identity_byte_exact():
    """The all-zero HslBands default must be a BYTE-exact no-op under the
    perceptual applicator (the `is_identity()` short-circuit returns the input
    before any lossy OKLab round-trip). With the gated downstream RGC pass this is
    what keeps a zero-HSL perceptual render byte-exact — the ΔE ship-gate
    guarantee (BLOCKER 3)."""
    rgb = np.random.default_rng(1).random((16, 16, 3)).astype(np.float32)
    np.testing.assert_array_equal(_apply_hsl_perceptual(rgb, HslBands()), rgb)
    np.testing.assert_array_equal(
        _apply_hsl_perceptual(
            rgb, HslBands(hue=(0.0,) * 8, saturation=(0.0,) * 8, luminance=(0.0,) * 8),
        ),
        rgb,
    )


def test_oklch_no_top_clamp_preserves_overrange():
    """BLOCKER 1: the perceptual path feeds the scene-referred ACEScg master, so a
    >1 highlight MUST survive `_apply_hsl_perceptual` un-truncated. Faithful
    `apply_hsl` floors at 0 but never clamps the top; match that. A bright
    saturated specular stays >1 even with a Saturation+Luminance boost on its
    band."""
    spec = np.array([[[6.0, 3.0, 2.0]], [[4.0, 0.2, 0.1]]], dtype=np.float64)
    hsl = HslBands(
        saturation=(60.0, 0, 0, 0, 0, 0, 0, 0),
        luminance=(40.0, 0, 0, 0, 0, 0, 0, 0),
    )
    out = _apply_hsl_perceptual(spec, hsl)
    assert out.max() > 1.0, "overrange highlight was clamped — RGC must be downstream, not in-op"


def test_oklch_hue_constancy_under_lum_sweep():
    """A Luminance grade must act on OKLCh L alone and NOT drift hue (the structural
    basis of the §7 Abney/Bezold–Brücke advantage). A fixed-(hue, chroma) input
    swept across L, with band Luminance engaged, comes out at a near-constant OKLCh
    hue (span < ~1°). This guards that the Luminance op stays in OKLCh L — a wrong
    space / per-channel lift would rotate hue here — not that the band math itself
    is correct (the oracle + sensitivity legs carry that); with the Hue sliders 0,
    `h_out = h` by construction, so this isolates the L-only / hue-preservation
    property."""
    pp = colour.RGB_COLOURSPACES["ProPhoto RGB"]
    m_rgb_to_xyz = np.asarray(pp.matrix_RGB_to_XYZ)
    m_xyz_to_rgb = np.asarray(pp.matrix_XYZ_to_RGB)
    m_b65to50 = np.linalg.inv(_develop_ops._M_BRADFORD_D50_TO_D65)

    def oklch_to_pp(el, c, h_deg):
        a = c * math.cos(math.radians(h_deg))
        b = c * math.sin(math.radians(h_deg))
        xyz65 = colour.Oklab_to_XYZ(np.array([el, a, b]))
        return (xyz65 @ m_b65to50.T) @ m_xyz_to_rgb.T

    def out_hue(rgb):
        xyz65 = (rgb.reshape(-1, 3) @ m_rgb_to_xyz.T) @ _develop_ops._M_BRADFORD_D50_TO_D65.T
        return colour.Oklab_to_Oklch(colour.XYZ_to_Oklab(xyz65))[:, 2]

    h0, c0 = 40.0, 0.12  # a fixed orange-ish hue/chroma (lands in Orange/Yellow bands)
    levels = np.linspace(0.2, 0.9, 15)
    inp = np.array([oklch_to_pp(el, c0, h0) for el in levels]).reshape(-1, 1, 3)
    hsl = HslBands(luminance=(0.0, 80.0, 80.0, 0.0, 0.0, 0.0, 0.0, 0.0))  # Orange+Yellow Lum +80
    out_hues = out_hue(_apply_hsl_perceptual(inp, hsl))
    assert out_hues.max() - out_hues.min() < 1.0, (
        f"hue drifted {out_hues.max() - out_hues.min():.3f}° under a Luminance sweep"
    )


def test_oklch_neutrals_unaffected_by_lum_gate():
    """§0 / the chroma gate: a near-grey pixel (ill-defined hue) must NOT be pushed
    by a Luminance slider, while a saturated pixel under the SAME sliders IS lifted.
    That contrast is the gate's whole purpose (the OKLCh analogue of the faithful
    `s_gate`).

    **Luminance is engaged on ALL eight bands** (`lum_mult = 1.6` independent of
    hue), so the chroma gate `c_gate = clip(c/0.04, 0, 1)` is the ONLY thing that
    can hold neutrals: a broken gate (`c_gate ≡ 1`) would lift greys by ~2.2 in
    ProPhoto, which this test would catch. (A single-band slider would NOT exercise
    the gate — pure greys land at OKLCh hue ≈228°, so a Red-band slider has ~zero
    weight on them and they would be unchanged whether or not the gate works.)"""
    grey = np.array([
        [[0.05, 0.05, 0.05]], [[0.4, 0.4, 0.4]], [[0.7, 0.7, 0.7]], [[0.95, 0.95, 0.95]],
    ], dtype=np.float64)
    hsl = HslBands(luminance=(60.0,) * 8)  # lum_mult = 1.6 for ANY hue
    grey_out = _apply_hsl_perceptual(grey, hsl)
    # Pure greys have c≈1e-4 → c_gate≈0.0025 → a ~0.15% residual lift (the gate
    # ramp, not a real lift); ~4.5e-3 in ProPhoto, three orders below a broken
    # gate's ~2.2. The bound is the ramp, NOT the OKLab float floor.
    np.testing.assert_allclose(grey_out, grey, atol=1e-2)

    # Discriminator: a SATURATED pixel under the SAME all-band sliders IS lifted
    # (c_gate=1 → full L×1.6). Neutral barely moves, saturated moves a lot — proof
    # the gate gates on chroma rather than zeroing the Luminance op outright.
    sat = np.array([[[0.05, 0.4, 0.95]]], dtype=np.float64)  # c≈0.37 → c_gate=1
    sat_out = _apply_hsl_perceptual(sat, hsl)
    assert np.max(np.abs(sat_out - sat)) > 0.1


def test_oklch_bradford_constants_match_colour_science():
    """The pinned module Bradford D50↔D65 matrices must equal colour-science's
    Bradford CAT (the same `matrix_chromatic_adaptation_VonKries` the rest of the
    pipeline adapts with), so the perceptual HSL working transform cannot silently
    drift from the CAT `output.py`'s ProPhoto→ACEScg uses. Mirrors the
    `_PROPHOTO_LUMINANCE` guard."""
    from colour.adaptation import matrix_chromatic_adaptation_VonKries
    xyz_d50 = colour.xy_to_XYZ(colour.RGB_COLOURSPACES["ProPhoto RGB"].whitepoint)
    xyz_d65 = colour.xy_to_XYZ(
        colour.CCS_ILLUMINANTS["CIE 1931 2 Degree Standard Observer"]["D65"],
    )
    m_50_to_65 = matrix_chromatic_adaptation_VonKries(xyz_d50, xyz_d65, transform="Bradford")
    m_65_to_50 = matrix_chromatic_adaptation_VonKries(xyz_d65, xyz_d50, transform="Bradford")
    np.testing.assert_allclose(_develop_ops._M_BRADFORD_D50_TO_D65, m_50_to_65, atol=1e-6)
    np.testing.assert_allclose(_develop_ops._M_BRADFORD_D65_TO_D50, m_65_to_50, atol=1e-6)
    # And the two pinned matrices are mutual inverses (round-trip is clean).
    np.testing.assert_allclose(
        _develop_ops._M_BRADFORD_D50_TO_D65 @ _develop_ops._M_BRADFORD_D65_TO_D50, np.eye(3), atol=1e-5,
    )


# ---------------------------------------------------------------------------
# Color Grading wheels (develop_ops.apply_color_grade) — Shadows / Midtones /
# Highlights / Global, tonal-zone-weighted additive tint.
#
# Axis-1 ground truth is an INDEPENDENT scalar reimplementation of the defined
# math: a hand-coded perceptual-luminance + zone partition + per-wheel zero-sum
# hue direction, looped per pixel — a different code path from the vectorised
# matmul/broadcast impl. `zero_sum` / `swap_zones` are injectable so the
# sensitivity legs prove the check discriminates a wrong tint model. (LR's exact
# strengths / mask are closed-source; this validates our defined spec.)
# ---------------------------------------------------------------------------


def _oracle_color_grade(prophoto, cg, zero_sum=True, swap_zones=False):
    y_w = list(_PROPHOTO_LUMINANCE)

    def srgb_oetf(x):
        return 12.92 * x if x <= 0.0031308 else 1.055 * (x ** (1.0 / 2.4)) - 0.055

    def hue_dir(hue_deg):
        h = (hue_deg % 360.0) * (6.0 / 360.0)
        sector = int(np.floor(h)) % 6
        f = h - np.floor(h)
        rgb = np.array([
            (1.0, f, 0.0), (1.0 - f, 1.0, 0.0), (0.0, 1.0, f),
            (0.0, 1.0 - f, 1.0), (f, 0.0, 1.0), (1.0, 0.0, 1.0 - f),
        ][sector], dtype=np.float64)
        return rgb - rgb.mean() if zero_sum else rgb

    def tint(hue, sat, lum):
        return (_CG_CHROMA_STRENGTH * (sat / 100.0) * hue_dir(hue)
                + _CG_LUM_STRENGTH * (lum / 100.0))

    t_sh = tint(cg.shadow_hue, cg.shadow_sat, cg.shadow_lum)
    t_mid = tint(cg.midtone_hue, cg.midtone_sat, cg.midtone_lum)
    t_hi = tint(cg.highlight_hue, cg.highlight_sat, cg.highlight_lum)
    t_gl = tint(cg.global_hue, cg.global_sat, cg.global_lum)
    gamma_b = 2.0 ** (-cg.balance / 100.0)
    p = 1.0 + 2.0 * (1.0 - min(max(cg.blending, 0.0), 100.0) / 100.0)

    flat = prophoto.reshape(-1, 3).astype(np.float64)
    out = np.empty_like(flat)
    for i in range(flat.shape[0]):
        r, g, b = (float(c) for c in flat[i])
        lum = r * y_w[0] + g * y_w[1] + b * y_w[2]
        ll = srgb_oetf(min(max(lum, 0.0), 1.0))
        tt = min(max(ll, 0.0), 1.0) ** gamma_b
        sh = (1.0 - tt) ** p
        hi = tt ** p
        mid = 1.0 - sh - hi
        if swap_zones:
            sh, hi = hi, sh
        px = flat[i] + sh * t_sh + mid * t_mid + hi * t_hi + t_gl
        out[i] = np.maximum(px, 0.0)
    return out.reshape(prophoto.shape)


def test_prophoto_luminance_constant_matches_colour_science():
    """The hardcoded _PROPHOTO_LUMINANCE row must equal colour-science's ProPhoto
    RGB→XYZ Y row (the matrix output.py actually converts with), so the Color-
    Grade zone mask cannot silently drift from the real luminance."""
    y_row = colour.RGB_COLOURSPACES["ProPhoto RGB"].matrix_RGB_to_XYZ[1]
    np.testing.assert_allclose(_PROPHOTO_LUMINANCE, y_row, atol=1e-3)


def test_color_grade_matches_independent_oracle():
    """apply_color_grade must equal the independent scalar oracle to ~0 over
    random pixels + a dark/bright/neutral/saturated/overrange edge set, with all
    four wheels + non-default blending & balance engaged."""
    rng = np.random.default_rng(20260531)
    rgb = rng.random((2048, 3)).astype(np.float64).reshape(-1, 1, 3)
    edge = np.array([
        [0.01, 0.01, 0.01], [0.5, 0.5, 0.5], [0.98, 0.98, 0.98],  # dark/mid/bright neutral
        [0.8, 0.05, 0.02], [0.05, 0.6, 0.1], [1.4, 0.2, 0.3],      # saturated + overrange
    ], dtype=np.float64).reshape(-1, 1, 3)
    rgb = np.concatenate([rgb, edge], axis=0)

    cg = ColorGrade(
        shadow_hue=220.0, shadow_sat=70.0, shadow_lum=-20.0,
        midtone_hue=120.0, midtone_sat=40.0, midtone_lum=10.0,
        highlight_hue=40.0, highlight_sat=60.0, highlight_lum=25.0,
        global_hue=300.0, global_sat=20.0, global_lum=-5.0,
        blending=65.0, balance=-30.0,
    )
    got = apply_color_grade(rgb, cg)
    want = _oracle_color_grade(rgb, cg)
    np.testing.assert_allclose(got, want, atol=1e-9)


def test_color_grade_oracle_detects_non_zero_sum_tint():
    """Sensitivity: the chroma direction is zero-sum (Hue carries no net
    luminance). A buggy oracle using the raw saturated RGB instead injects a
    brightness shift that diverges from the real op on a Saturation-only grade."""
    rgb = np.array([[[0.2, 0.2, 0.2]]], dtype=np.float64)  # neutral → tint is all there is
    cg = ColorGrade(global_hue=240.0, global_sat=100.0)     # pure blue, no luminance
    correct = _oracle_color_grade(rgb, cg, zero_sum=True)
    wrong = _oracle_color_grade(rgb, cg, zero_sum=False)
    assert np.max(np.abs(correct - wrong)) > 1e-2
    np.testing.assert_allclose(apply_color_grade(rgb, cg), correct, atol=1e-9)


def test_color_grade_oracle_detects_swapped_zones():
    """Sensitivity: shadow vs highlight masks are distinct. Swapping them sends
    the shadow tint to the highlights — diverging on a dark pixel where the two
    wheels carry different colours."""
    rgb = np.array([[[0.03, 0.03, 0.03]]], dtype=np.float64)  # a deep shadow
    cg = ColorGrade(
        shadow_hue=240.0, shadow_sat=100.0,   # blue shadows
        highlight_hue=40.0, highlight_sat=100.0,  # orange highlights
    )
    correct = _oracle_color_grade(rgb, cg, swap_zones=False)
    wrong = _oracle_color_grade(rgb, cg, swap_zones=True)
    assert np.max(np.abs(correct - wrong)) > 1e-2
    np.testing.assert_allclose(apply_color_grade(rgb, cg), correct, atol=1e-9)


def test_color_grade_identity_is_byte_exact_no_op():
    """All wheels at zero Saturation+Luminance → byte-exact passthrough even with
    Blending/Balance/Hue set (they are inert without a tint). The structural
    guarantee that the ΔE ship gate is unaffected when no grade is authored."""
    rgb = np.random.default_rng(2).random((16, 16, 3)).astype(np.float32)
    np.testing.assert_array_equal(apply_color_grade(rgb, ColorGrade()), rgb)
    np.testing.assert_array_equal(
        apply_color_grade(rgb, ColorGrade(blending=80.0, balance=-50.0, shadow_hue=210.0)),
        rgb,
    )


def test_color_grade_zone_weights_are_partition_of_unity():
    """The shadow/midtone/highlight masks sum to 1 with a non-negative midtone
    across the Blending and Balance ranges — so an all-zero grade's additive
    overlay is exactly zero everywhere (no spurious tint)."""
    luminance = np.linspace(0.0, 1.0, 256)
    for blending in (0.0, 50.0, 100.0):
        for balance in (-100.0, 0.0, 100.0):
            sh, mid, hi = _color_grade_zone_weights(luminance, blending, balance)
            np.testing.assert_allclose(sh + mid + hi, 1.0, atol=1e-12)
            assert mid.min() >= -1e-12


# ---------------------------------------------------------------------------
# DR-compression (develop_ops.apply_dr_compression) — scene-referred local
# dynamic-range compression driven by Highlights / Shadows / Whites.
#
# Axis-1 ground truth is an INDEPENDENT scalar reimplementation of the defined
# math: the asymmetric 3-slope piecewise-log curve (with its OWN hand-coded
# smoothstep blends — NOT calling _dr_remap_log) + the out/in luminance-RATIO
# reapply, looped per pixel. A wholly different code path from the production
# vectorised log/exp + cumsum form, so agreement is a real cross-check.
#
# Feeding an (N, 1, 3) array drives the production op's r=0 path (the box radius
# collapses on a 1-wide axis), which IS the global pointwise law the oracle
# mirrors — so this validates the real production function, not a test-only path.
# The local guided-filter base/detail behaviour is covered by property tests in
# test_develop_ops.py (it is not exactly invertible and has no closed oracle).
#
# Constants (k, anchor, breakpoint, blend half-widths) are a documented TUNING
# choice; this validates the *defined* curve, NOT Lightroom fidelity (the
# perceptual path makes no fidelity claim). `per_channel` / `flip_sign` /
# `drop_blend` / `anchor` are injectable so the sensitivity legs prove the check
# discriminates the four load-bearing bugs.
# ---------------------------------------------------------------------------


def _oracle_dr_compress(
    rgb, highlights, shadows, whites,
    per_channel=False, flip_sign=False, drop_blend=False, anchor=None,
):
    anchor = _DR_ANCHOR if anchor is None else anchor
    log_anchor = math.log2(anchor)
    k = _DR_SLOPE_GAIN_K
    ha, hb, ub = _DR_BLEND_HALFWIDTH_ANCHOR, _DR_BLEND_HALFWIDTH_BREAK, _DR_BREAK_STOPS
    eps = _DR_EPS
    yw = list(_PROPHOTO_LUMINANCE)
    sign = 1.0 if flip_sign else -1.0  # correct law uses −k
    c_lo = 2.0 ** (sign * k * shadows / 100.0)
    c_hi = 2.0 ** (sign * k * highlights / 100.0)
    c_top = 2.0 ** (sign * k * whites / 100.0)

    def smoothstep(x):
        x = min(max(x, 0.0), 1.0)
        return x * x * (3.0 - 2.0 * x)

    def remap(u):
        g_lo, g_hi = c_lo * u, c_hi * u
        g_top = c_hi * ub + c_top * (u - ub)
        if drop_blend:  # hard piecewise — no C1 blend (the kink bug)
            if u < 0.0:
                return g_lo
            return g_hi if u < ub else g_top
        wa = smoothstep((u + ha) / (2.0 * ha))
        wb = smoothstep((u - (ub - hb)) / (2.0 * hb))
        return (1.0 - wb) * ((1.0 - wa) * g_lo + wa * g_hi) + wb * g_top

    def law(lum):
        u = math.log2(max(lum, 0.0) + eps) - log_anchor
        return max(anchor * 2.0 ** remap(u) - eps, 0.0)

    flat = rgb.reshape(-1, 3).astype(np.float64)
    out = np.empty_like(flat)
    for i in range(flat.shape[0]):
        r, g, b = (float(c) for c in flat[i])
        if per_channel:  # the §0 hue-rotation bug — law applied per channel
            out[i] = (law(r), law(g), law(b))
        else:
            lum = r * yw[0] + g * yw[1] + b * yw[2]
            ratio = law(lum) / max(lum, eps)
            out[i] = (max(r * ratio, 0.0), max(g * ratio, 0.0), max(b * ratio, 0.0))
    return out.reshape(rgb.shape)


# A fixed pixel set spanning the regions the curve must get right: deep shadow,
# below/at/above anchor, the two blend windows, near-white, and SATURATED +
# OVERRANGE (a grey wedge is blind to the per-channel-vs-ratio error). Shaped
# (N, 1, 3) so the production op runs the global (r=0) law the oracle mirrors.
def _dr_edge_pixels():
    rng = np.random.default_rng(20260531)
    rand = rng.random((1500, 3)) * 1.5  # some overrange
    edge = np.array([
        [0.001, 0.001, 0.001],   # near-black neutral
        [0.05, 0.05, 0.05],      # below anchor
        [0.18, 0.18, 0.18],      # the anchor
        [0.30, 0.30, 0.30],      # anchor blend window
        [0.72, 0.72, 0.72],      # the high breakpoint (0.18·4)
        [0.95, 0.92, 0.05],      # saturated yellow, upper-mid
        [1.4, 0.2, 0.3],         # OVERRANGE + saturated
        [3.0, 0.1, 0.05],        # far overrange + saturated red specular
        [0.8, 0.05, 0.02],       # saturated red, mid
    ])
    return np.concatenate([rand, edge], axis=0).reshape(-1, 1, 3)


def test_dr_compression_matches_independent_oracle():
    """apply_dr_compression (global r=0 path via the (N,1,3) layout) must equal
    the independent scalar oracle to ~0 over random + saturated + overrange
    pixels, with all three sliders engaged at a non-trivial asymmetric setting."""
    rgb = _dr_edge_pixels()
    hi, sh, wh = 45.0, -30.0, 60.0
    got = apply_dr_compression(rgb, hi, sh, wh)
    want = _oracle_dr_compress(rgb, hi, sh, wh)
    np.testing.assert_allclose(got, want, atol=1e-9)


def test_dr_compression_oracle_detects_per_channel_bug():
    """Sensitivity leg 1 — the §0 hue-rotation bug. Applying the law per-channel
    instead of via the out/in luminance ratio shifts hue on saturated pixels;
    the real op matches the ratio oracle and NOT the per-channel one."""
    rgb = np.array([[[1.4, 0.2, 0.3]], [[0.8, 0.05, 0.02]]])  # saturated + overrange
    hi, sh, wh = 50.0, 0.0, 50.0
    correct = _oracle_dr_compress(rgb, hi, sh, wh)
    per_ch = _oracle_dr_compress(rgb, hi, sh, wh, per_channel=True)
    assert np.max(np.abs(correct - per_ch)) > 1e-2
    np.testing.assert_allclose(apply_dr_compression(rgb, hi, sh, wh), correct, atol=1e-9)


def test_dr_compression_oracle_detects_flipped_slope_sign():
    """Sensitivity leg 2 — slope = 2**(−k·s/100). A flipped sign (+k) turns
    compression into expansion (and vice-versa); the real op matches −k."""
    rgb = _dr_edge_pixels()
    hi, sh, wh = 60.0, 40.0, 60.0
    correct = _oracle_dr_compress(rgb, hi, sh, wh)
    flipped = _oracle_dr_compress(rgb, hi, sh, wh, flip_sign=True)
    assert np.max(np.abs(correct - flipped)) > 1e-2
    np.testing.assert_allclose(apply_dr_compression(rgb, hi, sh, wh), correct, atol=1e-9)


def test_dr_compression_oracle_detects_dropped_c1_blend():
    """Sensitivity leg 3 — the C1 blend is load-bearing (without it an asymmetric
    setting kinks at a join). A pixel whose log-distance lands INSIDE a blend
    window diverges between the smooth and hard-piecewise curves; the real op
    matches the blended oracle."""
    # log-distances (stops above the 0.18 anchor): 0.15→−0.26 and 0.21→+0.22 sit
    # inside the anchor window [−0.5,+0.5] (c_lo↔c_hi); 0.60→+1.74 sits inside the
    # breakpoint window [1.5,2.5] (c_hi↔c_top) — both joins, where a kink shows.
    rgb = np.array([[[0.15, 0.15, 0.15]], [[0.21, 0.21, 0.21]], [[0.60, 0.60, 0.60]]])
    hi, sh, wh = 80.0, -80.0, 70.0  # strongly asymmetric arms → a sharp kink if unblended
    correct = _oracle_dr_compress(rgb, hi, sh, wh)
    kinked = _oracle_dr_compress(rgb, hi, sh, wh, drop_blend=True)
    assert np.max(np.abs(correct - kinked)) > 1e-3
    np.testing.assert_allclose(apply_dr_compression(rgb, hi, sh, wh), correct, atol=1e-9)


def test_dr_compression_oracle_detects_wrong_anchor():
    """Sensitivity leg 4 — the anchor is the curve's fixed point. A wrong anchor
    (0.20 vs 0.18) shifts the whole curve; the real op matches 0.18."""
    rgb = _dr_edge_pixels()
    hi, sh, wh = 50.0, 30.0, 40.0
    correct = _oracle_dr_compress(rgb, hi, sh, wh)
    wrong = _oracle_dr_compress(rgb, hi, sh, wh, anchor=0.20)
    assert np.max(np.abs(correct - wrong)) > 1e-3
    np.testing.assert_allclose(apply_dr_compression(rgb, hi, sh, wh), correct, atol=1e-9)


def test_dr_compression_identity_is_byte_exact_no_op():
    """All three sliders 0 → BYTE-exact passthrough (short-circuit before any
    log/exp). slope=1 is NOT numerically identity (the eps pair does not cancel
    bit-exactly), so this guards the ΔE ship gate on the perceptual path."""
    rgb = np.random.default_rng(3).random((16, 16, 3)).astype(np.float32)
    np.testing.assert_array_equal(apply_dr_compression(rgb, 0.0, 0.0, 0.0), rgb)


def test_dr_compression_preserves_hue_and_chroma_on_saturated():
    """§0: the ratio reapply scales all three channels by ONE positive scalar, so
    a saturated pixel's channel ratios (its hue/chroma direction) are preserved
    exactly — including on an overrange pixel."""
    for px in ([1.4, 0.2, 0.3], [3.0, 0.1, 0.05], [0.8, 0.05, 0.02]):
        rgb = np.array([[px]])
        out = apply_dr_compression(rgb, 55.0, -25.0, 45.0)[0, 0]
        scale = out / np.array(px)
        np.testing.assert_allclose(scale, scale[0], rtol=1e-9)  # one common ratio
        # ratios between channels (the hue/chroma direction) are unchanged
        np.testing.assert_allclose(out / out.sum(), np.array(px) / np.sum(px), rtol=1e-9)


def test_dr_compression_break_even_overrange_survives():
    """Correct overrange behaviour (NOT a violation): the law pulls sub-threshold
    overrange below 1 and keeps only bright-enough speculars >1. Verified fixed
    points: a single slope 0.5 has break-even L≈5.556 (=0.18^-1), slope 0.7
    L≈2.085. Below the point → ≤1; above → >1, unclamped."""
    for slope, break_even in ((0.5, _DR_ANCHOR ** (1 - 1 / 0.5)),
                              (0.7, _DR_ANCHOR ** (1 - 1 / 0.7))):
        below = _dr_compress_luminance(np.array([break_even * 0.98]), slope, slope, slope)
        above = _dr_compress_luminance(np.array([break_even * 1.02]), slope, slope, slope)
        assert below[0] < 1.0 < above[0]
    np.testing.assert_allclose(_DR_ANCHOR ** (1 - 1 / 0.5), 5.5556, atol=1e-3)
    np.testing.assert_allclose(_DR_ANCHOR ** (1 - 1 / 0.7), 2.0853, atol=1e-3)


def test_dr_compression_no_in_op_clamp():
    """The op must NOT clip overrange down to a display ceiling — out-of-AP1 is a
    SEPARATE downstream RGC pass. A bright specular stays >1 even under maximal
    Highlights+Whites compression."""
    spec = np.full((4, 4, 3), 8.0)  # ~5.5 stops over the anchor
    out = apply_dr_compression(spec, 100.0, 0.0, 100.0)
    assert out.min() > 1.0, "overrange specular was clamped — RGC must be downstream, not in-op"


def test_dr_compression_monotone_on_sorted_ramp_extremes():
    """No gradient inversions on a sorted luminance ramp, INCLUDING the worst case
    for the C1 blend: opposite-sign adjacent arms (Shadows +100 → c_lo 0.5,
    Highlights −100 → c_hi 2.0) — a mild setting cannot expose a kink."""
    lum = np.linspace(0.0, 60.0, 400_000)
    for hi, sh, wh in [(45.0, -30.0, 60.0), (-100.0, 100.0, -100.0), (100.0, -100.0, 100.0)]:
        from lrt_cinema.develop_ops import _dr_slopes
        c_lo, c_hi, c_top = _dr_slopes(hi, sh, wh)
        out = _dr_compress_luminance(lum, c_lo, c_hi, c_top)
        assert np.all(np.diff(out) >= -1e-12), f"non-monotone at hi={hi},sh={sh},wh={wh}"


def test_dr_compression_invertible_single_slope_round_trip():
    """The bare single-slope law (all sliders equal → one slope everywhere) is
    exactly invertible: forward with +s, inverse with −s (slope → 1/slope), the
    ±eps invert as a matched pair. Round-trips to ~0 (eps-level)."""
    rgb = _dr_edge_pixels()
    fwd = apply_dr_compression(rgb, 50.0, 50.0, 50.0)        # all arms slope 0.5
    back = apply_dr_compression(fwd, -50.0, -50.0, -50.0)    # all arms slope 2.0
    np.testing.assert_allclose(back, rgb, atol=1e-5, rtol=1e-4)


def test_dr_compression_invertible_three_slope_numerically():
    """The driven asymmetric curve is globally invertible because every segment
    and blend window is strictly monotone — so the inverse is PIECEWISE (no single
    1/slope closed form). Verify via a numerical inverse of the monotone forward
    map on a luminance ramp."""
    from lrt_cinema.develop_ops import _dr_slopes
    c_lo, c_hi, c_top = _dr_slopes(60.0, -40.0, 50.0)
    lum = np.linspace(1e-4, 40.0, 200_000)
    fwd = _dr_compress_luminance(lum, c_lo, c_hi, c_top)
    assert np.all(np.diff(fwd) > 0)  # strictly monotone → invertible
    sample = np.array([0.02, 0.18, 0.5, 1.0, 3.0, 12.0])
    fwd_s = _dr_compress_luminance(sample, c_lo, c_hi, c_top)
    recovered = np.interp(fwd_s, fwd, lum)  # invert through the monotone curve
    np.testing.assert_allclose(recovered, sample, rtol=2e-3)


# ---------------------------------------------------------------------------
# Texture / Clarity (develop_ops.apply_texture_clarity) — PERCEPTUAL local-contrast
# boost (the boost-detail mode of the shared guided base/detail engine).
#
# Axis-1 ground truth is an INDEPENDENT reimplementation of the defined math: the
# two-band guided base/detail split + the per-band boost (fine = uniform Texture,
# mid = midtone-weighted Clarity) + the out/in luminance-RATIO reapply. Crucially
# the guided self-filter is reimplemented with scipy.ndimage.uniform_filter (a
# moving-average), NOT the production _box_sum/_guided_base_log (cumsum) — a wholly
# different code path, so agreement is a real cross-check, NOT a tautology (contract
# 4). uniform_filter(mode="nearest") only equals the production shrinking-window box
# in the DEEP INTERIOR: the guided filter box-averages TWICE (mean_i/mean_ii then
# mean_a/mean_b), so output at p depends on log_l across [p−2r, p+2r]; the two agree
# only where that whole support is interior → compare at >= 2·r_coarse from every
# border (mirrors test_box_sum_matches_scipy_uniform_filter_interior, interior-only).
#
# The op is run on a 2D IMAGE WITH REAL STRUCTURE (a detail boost is identically 0 on
# the (N,1,3) r=0 layout the DR oracle uses — it would exercise none of the boost
# math), spanning saturated + overrange pixels (a grey wedge is blind to the
# per-channel-vs-ratio error). Constants (radii, eps, gains, midtone sigma, anchor)
# are a documented TUNING choice; this validates the *defined* math, NOT Lightroom
# fidelity. `per_channel` / `swap_radii` / `drop_midtone` are injectable so the
# sensitivity legs prove the check discriminates the load-bearing bugs.
# ---------------------------------------------------------------------------


def _oracle_guided_uniform(log_l, r, eps_gf):
    """Independent guided self-filter via scipy uniform_filter (NOT the production
    _box_sum cumsum). Matches _guided_base_log only in the >= 2r interior."""
    if r <= 0:
        return log_l.copy()
    from scipy.ndimage import uniform_filter
    sz = 2 * r + 1
    mean_i = uniform_filter(log_l, size=sz, mode="nearest")
    mean_ii = uniform_filter(log_l * log_l, size=sz, mode="nearest")
    var_i = np.maximum(mean_ii - mean_i * mean_i, 0.0)
    a = var_i / (var_i + eps_gf)
    b = mean_i - a * mean_i
    mean_a = uniform_filter(a, size=sz, mode="nearest")
    mean_b = uniform_filter(b, size=sz, mode="nearest")
    return mean_a * log_l + mean_b


def _oracle_texture_clarity(
    rgb, texture, clarity, per_channel=False, swap_radii=False, drop_midtone=False,
):
    eps = _DR_EPS
    yw = np.asarray(_PROPHOTO_LUMINANCE)
    r_fine, r_coarse = _TC_RADIUS_FINE, _TC_RADIUS_COARSE
    if swap_radii:  # the band-scale bug — Texture on the coarse band, Clarity on fine
        r_fine, r_coarse = r_coarse, r_fine
    lum = rgb @ yw
    log_l = np.log2(np.maximum(lum, 0.0) + eps)
    base_fine = _oracle_guided_uniform(log_l, r_fine, _TC_GUIDED_EPS)
    base_coarse = _oracle_guided_uniform(log_l, r_coarse, _TC_GUIDED_EPS)
    texture_band = log_l - base_fine
    clarity_band = base_fine - base_coarse
    if drop_midtone:  # the midtone-weight bug — Clarity applied globally (mw≡1)
        midtone_w = np.ones_like(log_l)
    else:
        midtone_w = np.exp(-0.5 * ((log_l - _DR_LOG_ANCHOR) / _TC_MIDTONE_SIGMA) ** 2)
    texture_gain = 1.0 + _TC_TEXTURE_GAIN * (texture / 100.0)
    clarity_gain = 1.0 + _TC_CLARITY_GAIN * (clarity / 100.0) * midtone_w
    log_l_out = base_coarse + texture_gain * texture_band + clarity_gain * clarity_band
    lum_out = np.maximum(np.exp2(log_l_out) - eps, 0.0)
    if per_channel:  # the §0 hue-rotation bug — boost re-derived per channel, no ratio
        out = np.empty_like(rgb, dtype=np.float64)
        for ch in range(3):
            log_c = np.log2(np.maximum(rgb[..., ch], 0.0) + eps)
            bf = _oracle_guided_uniform(log_c, r_fine, _TC_GUIDED_EPS)
            bc = _oracle_guided_uniform(log_c, r_coarse, _TC_GUIDED_EPS)
            lo = bc + texture_gain * (log_c - bf) + clarity_gain * (bf - bc)
            out[..., ch] = np.maximum(np.exp2(lo) - eps, 0.0)
        return out
    ratio = lum_out / np.maximum(lum, eps)
    return np.maximum(rgb * ratio[..., None], 0.0)


def _tc_oracle_image():
    """A 128×128 ProPhoto image with a fine + coarse detail layer AND saturated /
    overrange hue variation. 128 wide so the >= 2·_TC_RADIUS_COARSE (=32) interior
    [32:-32] is non-trivial (the only region the independent uniform_filter guided
    reimpl matches the production shrinking-window box)."""
    rng = np.random.default_rng(20260531)
    yy, xx = np.mgrid[0:128, 0:128]
    fine = 0.12 * np.sin(xx * 2.0) * np.sin(yy * 2.0)
    coarse = 0.45 * np.sin(xx * 0.3) * np.sin(yy * 0.3)
    base_stops = -2.0 + 4.0 * (xx / 127.0)  # a tonal ramp spanning shadow→overrange
    lum = 0.18 * 2.0 ** (base_stops + fine + coarse)
    # Saturated, spatially-varying hue (NOT a grey wedge) + some overrange.
    chroma = 0.6 + 0.8 * rng.random((128, 128, 3))
    return (lum[..., None] * chroma).astype(np.float64)


def _tc_interior(arr):
    r = 2 * _TC_RADIUS_COARSE  # guided box-filters twice → support is +/- 2r
    return arr[r:-r, r:-r]


def test_texture_clarity_matches_independent_oracle():
    """apply_texture_clarity must equal the independent uniform_filter-based oracle
    to ~0 in the deep interior, over a structured + saturated + overrange image, with
    both sliders engaged at a non-trivial asymmetric setting."""
    img = _tc_oracle_image()
    tx, cl = 60.0, -40.0
    got = apply_texture_clarity(img, tx, cl)
    want = _oracle_texture_clarity(img, tx, cl)
    np.testing.assert_allclose(_tc_interior(got), _tc_interior(want), atol=1e-9)


def test_texture_clarity_oracle_detects_per_channel_bug():
    """Sensitivity leg 1 — the §0 hue-rotation bug. Re-deriving the boost per channel
    (instead of the out/in luminance ratio) shifts hue on saturated pixels; the real
    op matches the ratio oracle and NOT the per-channel one."""
    img = _tc_oracle_image()
    tx, cl = 80.0, 60.0
    correct = _oracle_texture_clarity(img, tx, cl)
    per_ch = _oracle_texture_clarity(img, tx, cl, per_channel=True)
    assert np.max(np.abs(_tc_interior(correct) - _tc_interior(per_ch))) > 1e-2
    np.testing.assert_allclose(
        _tc_interior(apply_texture_clarity(img, tx, cl)), _tc_interior(correct), atol=1e-9,
    )


def test_texture_clarity_oracle_detects_swapped_radii():
    """Sensitivity leg 2 — the band-scale bug. Texture must act on the FINE band and
    Clarity on the larger mid band; swapping the two radii changes which scale each
    slider touches. The real op matches the correctly-ordered radii."""
    img = _tc_oracle_image()
    tx, cl = 80.0, 70.0
    correct = _oracle_texture_clarity(img, tx, cl)
    swapped = _oracle_texture_clarity(img, tx, cl, swap_radii=True)
    assert np.max(np.abs(_tc_interior(correct) - _tc_interior(swapped))) > 1e-2
    np.testing.assert_allclose(
        _tc_interior(apply_texture_clarity(img, tx, cl)), _tc_interior(correct), atol=1e-9,
    )


def test_texture_clarity_oracle_detects_dropped_midtone_weight():
    """Sensitivity leg 3 — Clarity is midtone-weighted (a Gaussian bump on
    log-luminance). Dropping the weight (applying Clarity globally) over-boosts
    shadows/highlights; a Clarity-only edit on a wide tonal range diverges. The real
    op matches the midtone-weighted oracle."""
    img = _tc_oracle_image()
    tx, cl = 0.0, 100.0  # Clarity-only, so the midtone weight is the whole difference
    correct = _oracle_texture_clarity(img, tx, cl)
    flat_mw = _oracle_texture_clarity(img, tx, cl, drop_midtone=True)
    assert np.max(np.abs(_tc_interior(correct) - _tc_interior(flat_mw))) > 1e-3
    np.testing.assert_allclose(
        _tc_interior(apply_texture_clarity(img, tx, cl)), _tc_interior(correct), atol=1e-9,
    )


def test_texture_clarity_identity_is_byte_exact_no_op():
    """Both sliders 0 → BYTE-exact passthrough (short-circuit before any filter
    math). slope-1 bands do NOT telescope to L bit-for-bit through log2/exp2, so this
    guards the ΔE ship gate on the perceptual path."""
    img = np.random.default_rng(44).random((16, 16, 3)).astype(np.float32)
    np.testing.assert_array_equal(apply_texture_clarity(img, 0.0, 0.0), img)


# ---------------------------------------------------------------------------
# ACES Reference Gamut Compression (output._aces_rgc_compress_ap1) — the single
# gated AP1 gamut-safety pass before the ACEScg EXR encode.
#
# Axis-1 ground truth is an INDEPENDENT, per-pixel scalar reimplementation of
# the canonical Academy 1.3 RGC (spec Eq. 2–4 / aces-dev reference DCTL): the
# achromatic max, the per-channel achromatic distance, the threshold/power/limit
# compression curve, and the reconstruct — looped per pixel and per channel, a
# wholly different code path from the production vectorised np.where form, so
# agreement is a real cross-check (not a tautology — production does NOT call
# any colour-science gamut function; `colour` 0.4.x has none).
#
# Constants (threshold/limit/power) are the EXACT published Academy reference
# defaults, NOT tuning — verified against the spec + DCTL. `disable` /
# `wrong_threshold` / `skip_ach_norm` are injectable so the sensitivity legs
# prove the check discriminates the three load-bearing bugs the task names:
# (1) compression disabled, (2) wrong threshold, (3) missing /ach normalization.
# A consistent channel↔limit (Cyan/Magenta/Yellow) swap would live in BOTH the
# oracle and production (same spec read) → it is closed independently by the
# OCIO cross-check below (skipif OCIO absent).
# ---------------------------------------------------------------------------


def _oracle_rgc(rgb, disable=False, wrong_threshold=False, skip_ach_norm=False):
    """Independent scalar ACES RGC. `rgb` is (..., 3) AP1-linear; returns the
    same shape. Per pixel: ach = max; d = (ach − c)/|ach| (or /1 to inject the
    missing-normalization bug); compress d above threshold; rebuild c = ach −
    d'·|ach|."""
    thr = list(_RGC_THRESHOLD)
    lim = list(_RGC_LIMIT)
    pwr = _RGC_POWER
    if wrong_threshold:
        thr = [0.5, 0.5, 0.5]  # a clearly different (lower) threshold
    scl = [
        (lim[i] - thr[i])
        / (((1.0 - thr[i]) / (lim[i] - thr[i])) ** (-pwr) - 1.0) ** (1.0 / pwr)
        for i in range(3)
    ]

    def compress(d, i):
        if disable or d < thr[i]:
            return d
        nd = (d - thr[i]) / scl[i]
        return thr[i] + scl[i] * nd / (1.0 + nd ** pwr) ** (1.0 / pwr)

    flat = rgb.reshape(-1, 3).astype(np.float64)
    out = np.empty_like(flat)
    for p in range(flat.shape[0]):
        c = [float(v) for v in flat[p]]
        ach = max(c)
        aa = abs(ach)
        for i in range(3):
            if aa == 0.0:
                d = 0.0
            elif skip_ach_norm:
                d = ach - c[i]               # the missing /|ach| bug
            else:
                d = (ach - c[i]) / aa
            out[p, i] = ach - compress(d, i) * aa
    return out.reshape(rgb.shape)


# A fixed pixel set spanning the regions RGC must get right: in-gamut neutral &
# saturated, in-gamut-but-past-threshold, mild out-of-AP1 (one negative channel,
# distance ≤ limit), and EXTREME out-of-AP1 (distance ≫ limit, stays negative by
# design). Out-of-AP1 == one or more NEGATIVE AP1 channels (CLAUDE.md §0: a grey
# wedge cannot exercise this op at all — its distances are 0).
def _rgc_edge_pixels():
    rng = np.random.default_rng(20260531)
    # Random AP1 with deliberate negatives (standard_normal → ~half negative).
    rand = rng.standard_normal((1500, 3)) * 0.8 + 0.3
    edge = np.array([
        [0.18, 0.18, 0.18],      # neutral mid — d=0, must be identity
        [0.0, 0.0, 0.0],         # black — ach=0, d=0 guard
        [0.8, 0.2, 0.1],         # in-gamut saturated, trailing chans past thr
        [1.0, 0.05, 0.2],        # mild out-of-AP1-ish (small trailing channel)
        [1.0, -0.05, 0.2],       # mild out-of-AP1 (one negative, d ≤ limit)
        [0.6, -0.02, 0.3],       # mild negative, lower achromatic
        [1.0, -2.0, -1.0],       # EXTREME out-of-AP1 (d ≫ limit)
        [2.0, -0.5, -0.3],       # bright saturated out-of-AP1
        [-0.3, 1.0, -0.1],       # negative on R and B, green-dominant
    ])
    return np.concatenate([rand, edge], axis=0).reshape(-1, 1, 3)


def test_rgc_matches_independent_oracle():
    """_aces_rgc_compress_ap1 must equal the independent per-pixel scalar oracle
    to ~0 over random + saturated + out-of-AP1 (negative-channel) pixels. The
    gate is forced on by the out-of-AP1 content."""
    rgb = _rgc_edge_pixels()
    got = _aces_rgc_compress_ap1(rgb.astype(np.float32))
    want = _oracle_rgc(rgb)
    np.testing.assert_allclose(got, want, atol=1e-6)


def test_rgc_oracle_detects_disabled_compression():
    """Sensitivity leg 1 — compression OFF. With the curve disabled an
    out-of-AP1 (negative) channel keeps its full distance; the real op compresses
    it. The two diverge on an out-of-AP1 pixel, so the agreement test is
    discriminating."""
    rgb = np.array([[[1.0, -0.05, 0.2]], [[2.0, -0.5, -0.3]]])  # out-of-AP1
    correct = _oracle_rgc(rgb)
    disabled = _oracle_rgc(rgb, disable=True)
    assert np.max(np.abs(correct - disabled)) > 1e-2
    np.testing.assert_allclose(
        _aces_rgc_compress_ap1(rgb.astype(np.float32)), correct, atol=1e-6,
    )


def test_rgc_oracle_detects_wrong_threshold():
    """Sensitivity leg 2 — the threshold is load-bearing (it sets where the
    rolloff begins). A wrong (lower) threshold compresses values the correct
    curve leaves alone; the real op matches the reference threshold and not the
    wrong one."""
    rgb = _rgc_edge_pixels()
    correct = _oracle_rgc(rgb)
    wrong = _oracle_rgc(rgb, wrong_threshold=True)
    assert np.max(np.abs(correct - wrong)) > 1e-2
    np.testing.assert_allclose(
        _aces_rgc_compress_ap1(rgb.astype(np.float32)), correct, atol=1e-6,
    )


def test_rgc_oracle_detects_missing_ach_normalization():
    """Sensitivity leg 3 — the distance MUST be normalized by |ach| (spec Eq. 3).
    Dropping the /|ach| makes the distance scale with absolute brightness, so a
    bright out-of-AP1 pixel is compressed by a totally different amount; the real
    op matches the normalized oracle."""
    rgb = np.array([[[2.0, -0.5, -0.3]], [[1.0, -0.05, 0.2]]])
    correct = _oracle_rgc(rgb)
    no_norm = _oracle_rgc(rgb, skip_ach_norm=True)
    assert np.max(np.abs(correct - no_norm)) > 1e-2
    np.testing.assert_allclose(
        _aces_rgc_compress_ap1(rgb.astype(np.float32)), correct, atol=1e-6,
    )


def test_rgc_gated_in_gamut_is_byte_exact_no_op():
    """GATED: an image whose every channel-distance is below threshold (in-gamut,
    low-to-moderate saturation) returns the INPUT array unchanged (literal
    object) — byte-exact identity. This is the structural guarantee that the
    ACEScg EXR path of an in-gamut render is bit-identical to the pre-RGC build,
    so the gym/rose ΔE ship gate is untouched."""
    rng = np.random.default_rng(7)
    # All-positive, modest saturation → max channel-distance stays < ~0.8.
    rgb = (rng.random((16, 16, 3)).astype(np.float32) * 0.5 + 0.25)
    out = _aces_rgc_compress_ap1(rgb)
    assert out is rgb, "gate must return the literal input (no copy/cast)"
    np.testing.assert_array_equal(out, rgb)


def test_rgc_grey_stays_grey_even_when_gate_fires():
    """A grey (achromatic) pixel has distance 0 on every channel → exact
    identity, even inside an image where other pixels ARE out of AP1 (so the gate
    fires). Stronger than an all-grey no-op: it proves the per-pixel achromatic
    axis is fixed. (CLAUDE.md §0 — validate the achromatic invariant alongside
    saturated content, not a grey wedge alone.)"""
    img = np.random.default_rng(1).random((4, 4, 3)).astype(np.float32)
    img[0, 0] = [2.0, -0.5, -0.3]   # out-of-AP1 → forces the gate ON
    greys = [(1, 1, 0.4), (2, 2, 0.05), (3, 3, 0.9)]
    for y, x, v in greys:
        img[y, x] = [v, v, v]
    out = _aces_rgc_compress_ap1(img)
    assert out is not img, "gate should have fired (out-of-AP1 pixel present)"
    for y, x, _v in greys:
        np.testing.assert_array_equal(out[y, x], img[y, x])


def test_rgc_pulls_out_of_ap1_back_toward_gamut():
    """A mildly out-of-AP1 pixel (one negative channel, distance ≤ its limit) is
    pulled back to in-gamut: NO negative channel remains, the achromatic (max)
    channel is preserved exactly, and the negative channel increased toward 0.
    Validated on SATURATED / out-of-gamut content (the only content this op
    touches)."""
    px = np.array([[[1.0, -0.05, 0.2]]], dtype=np.float32)
    out = _aces_rgc_compress_ap1(px)[0, 0]
    assert np.all(out >= -1e-7), f"negative channel survived (d≤limit): {out}"
    np.testing.assert_allclose(out[0], 1.0, atol=1e-6)   # max channel preserved
    assert out[1] > px[0, 0, 1]                           # negative pulled up


def test_rgc_extreme_out_of_ap1_reduced_not_eliminated():
    """Correct (NOT a violation): a pixel whose AP1 distance EXCEEDS the limit
    stays compressed-but-negative — the RGC asymptote is `threshold + scale ≈
    1.03–1.14`, never 1.0, so reconstruction can land below 0. We deliberately do
    NOT clip it (that would reintroduce the hard clip RGC replaces). Assert the
    magnitude is reduced + finite, not eliminated."""
    px = np.array([[[1.0, -2.0, -1.0]]], dtype=np.float32)
    out = _aces_rgc_compress_ap1(px)[0, 0]
    assert np.all(np.isfinite(out))
    assert abs(out[1]) < abs(px[0, 0, 1])    # |neg| reduced
    assert abs(out[2]) < abs(px[0, 0, 2])
    np.testing.assert_allclose(out[0], 1.0, atol=1e-6)  # max still preserved


def test_rgc_max_channel_and_dtype_preserved_no_nan():
    """The achromatic (max) channel is invariant under RGC (its distance is 0),
    the output dtype matches the input, and no NaN/Inf is produced even on a
    heavily-negative batch (the np.where identity branch must not leak a NaN from
    nd**power on a negative base — guarded by the max(d−t,0) floor)."""
    rng = np.random.default_rng(2)
    big = (rng.standard_normal((64, 64, 3)) * 1.5).astype(np.float32)
    out = _aces_rgc_compress_ap1(big)
    assert out.dtype == big.dtype
    assert np.all(np.isfinite(out)), "RGC produced a non-finite value"
    # Max channel per pixel is unchanged (d=0 → identity on the achromatic peak).
    np.testing.assert_allclose(
        np.max(out, axis=-1), np.max(big, axis=-1), atol=1e-6,
    )


def test_rgc_compress_curve_hits_boundary_at_limit():
    """The defining property (spec Eq. 4): a channel at exactly its limit
    distance reconstructs to the gamut boundary (channel value 0), i.e.
    compress(limit) == 1.0 per channel. Below threshold the curve is the
    identity. This pins the curve's anchor independently of the per-pixel
    machinery."""
    for i in range(3):
        # A pixel where channel i sits exactly at its limit distance from ach=1.
        rgb = np.ones((1, 1, 3))
        rgb[0, 0, i] = 1.0 - _RGC_LIMIT[i]      # d_i = (1 − (1−lim))/1 = lim
        out = _aces_rgc_compress_ap1(rgb.astype(np.float64))[0, 0]
        np.testing.assert_allclose(out[i], 0.0, atol=1e-6)  # → boundary
    # Below threshold → identity (a mildly-saturated in-gamut pixel).
    mild = np.array([[[1.0, 0.9, 0.95]]])  # all distances < 0.8
    np.testing.assert_array_equal(
        _aces_rgc_compress_ap1(mild.astype(np.float64)), mild,
    )


def test_rgc_monotone_in_distance_no_inversion():
    """Sweeping a single trailing channel from very negative (far out of AP1) up
    to the achromatic value yields a monotone-non-decreasing reconstructed
    channel — no gradient inversion across the threshold knee (the rolloff is C0
    and monotone)."""
    ach = 1.0
    trailing = np.linspace(-3.0, ach, 4000)
    rgb = np.stack(
        [np.full_like(trailing, ach), trailing, np.full_like(trailing, ach)],
        axis=-1,
    ).reshape(-1, 1, 3)
    out = _aces_rgc_compress_ap1(rgb)[:, 0, 1]
    assert np.all(np.diff(out) >= -1e-7), "RGC reconstruction is non-monotone"


def test_rgc_cross_check_against_ocio_if_available():
    """Independent cross-check against OCIO's built-in 'ACES 1.3 Reference Gamut
    Compression' — closes the one blind spot the hand-rolled oracle shares with
    production (a consistent Cyan/Magenta/Yellow channel↔limit mapping would be
    in BOTH, from the same spec read). Skipped when OCIO is not installed (it is
    NOT a runtime dependency — production stays hand-coded for controllable
    gating)."""
    ocio = pytest.importorskip("PyOpenColorIO")
    try:
        cfg = ocio.Config.CreateFromBuiltinConfig(
            "cg-config-v1.0.0_aces-v1.3_ocio-v2.1",
        )
        # The built-in ACES CG config exposes RGC as a named colour space whose
        # to_reference is the gamut compression (working space = ACEScg).
        proc = cfg.getProcessor("ACEScg", "ACEScg - Reference Gamut Compression")
    except Exception:  # noqa: BLE001 — config/name availability varies across OCIO builds
        pytest.skip("OCIO build lacks the ACES-1.3 RGC named transform")
    cpu = proc.getDefaultCPUProcessor()

    rgb = np.array([
        [1.0, -0.05, 0.2], [2.0, -0.5, -0.3], [0.8, 0.2, 0.1],
        [0.18, 0.18, 0.18], [-0.3, 1.0, -0.1],
    ], dtype=np.float32)
    ours = _aces_rgc_compress_ap1(rgb.reshape(-1, 1, 3))[:, 0, :]
    ref = rgb.copy()
    for i in range(ref.shape[0]):
        ref[i] = cpu.applyRGB(list(ref[i]))
    np.testing.assert_allclose(ours, ref, atol=2e-3)


# ---------------------------------------------------------------------------
# PERCEPTUAL ColorGrade — offset-only ASC-CDL in ACEScct log
# (develop_ops._apply_color_grade_perceptual). v0.9 dual-mode step 2.
#
# Axis-1 ground truth is a wholly INDEPENDENT scalar reimplementation of the
# full chain — hand-rolled ProPhoto<->ACEScg Bradford (7-digit matrices below,
# guarded against colour-science), hand-rolled ACEScct from the published AMPAS
# constants, hand-rolled offset SOP — NOT calling `colour.RGB_to_RGB` /
# `colour.models.log_encoding_ACEScct` (which production uses; using them both
# sides is the tautology contract 4 forbids). Agreement is a real cross-check.
#
# The CDL is OFFSET-ONLY (slope = power = 1): per-channel log offset = a uniform
# Luminance lift + a zero-sum chroma direction, zone-weighted. Constants
# (_CG_*_LOG_STRENGTH, _CG_ZONE_PROXY_*) are TUNING; the oracle validates the
# *defined* math, not LR appearance. Injectable bugs (`flip_toe`,
# `wrong_log_base`, `per_channel`, `swap_zones`) drive the discriminating legs.
# ---------------------------------------------------------------------------

# ProPhoto-RGB(D50) linear -> ACEScg(AP1) linear, Bradford D50->~D60. 7-digit,
# guarded against colour.matrix_RGB_to_RGB(...,"Bradford") by the test below so
# it cannot silently drift from the matrix output.py / the op converts with.
_M_PP_LIN_TO_ACESCG = np.array([
    [1.1690967, -0.0351612, -0.1340422],
    [-0.0670773, 1.0756683, -0.0085294],
    [0.0094233, -0.0133536, 1.0036796],
])
# ACEScg(AP1) linear -> ProPhoto-RGB(D50) linear (the inverse Bradford).
_M_ACESCG_TO_PP_LIN = np.array([
    [0.8562045, 0.0294258, 0.1145489],
    [0.0532754, 0.9316365, 0.0150474],
    [-0.0073287, 0.0121186, 0.9954843],
])

# Published AMPAS ACEScct constants (CONSTANTS_ACES_CCT; cited by colour 0.4.6).
# The toe is the LINEAR segment A*x+B below X_BRK (A,B > 0 — positive slope; the
# raw v09 spec sign-flipped this). Above X_BRK it is the log segment.
_ACESCCT_X_BRK = 0.0078125            # 2**-7
_ACESCCT_Y_BRK = 0.155251141552511
_ACESCCT_A = 10.5402377416545
_ACESCCT_B = 0.0729055341958355
_ACESCCT_LOG_DENOM = 17.52            # (log2(x) + 9.72) / 17.52
_ACESCCT_LOG_OFFSET = 9.72


def _oracle_acescct_encode(x, wrong_log_base=False):
    """Hand-rolled ACEScct log encode (scalar). `wrong_log_base` injects a
    natural-log base instead of log2 in the log segment (the wrong-base bug)."""
    if x <= _ACESCCT_X_BRK:
        return _ACESCCT_A * x + _ACESCCT_B
    if wrong_log_base:
        return (math.log(x) + _ACESCCT_LOG_OFFSET) / _ACESCCT_LOG_DENOM
    return (math.log2(x) + _ACESCCT_LOG_OFFSET) / _ACESCCT_LOG_DENOM


def _oracle_acescct_decode(y, wrong_log_base=False):
    """Hand-rolled ACEScct log decode (scalar), the exact inverse of encode."""
    if y <= _ACESCCT_Y_BRK:
        return (y - _ACESCCT_B) / _ACESCCT_A
    if wrong_log_base:
        return math.exp(y * _ACESCCT_LOG_DENOM - _ACESCCT_LOG_OFFSET)
    return 2.0 ** (y * _ACESCCT_LOG_DENOM - _ACESCCT_LOG_OFFSET)


def _oracle_hue_dir(hue_deg, zero_sum=True):
    """Saturated RGB for `hue_deg` via the hexcone sector formula (matches
    `_hsv_to_rgb_dcp` at S=V=1), mean-subtracted to a zero-sum chroma direction.
    `zero_sum=False` keeps the raw RGB (the non-zero-sum chroma bug)."""
    h = (hue_deg % 360.0) * (6.0 / 360.0)
    sector = int(np.floor(h)) % 6
    f = h - np.floor(h)
    rgb = np.array([
        (1.0, f, 0.0), (1.0 - f, 1.0, 0.0), (0.0, 1.0, f),
        (0.0, 1.0 - f, 1.0), (f, 0.0, 1.0), (1.0, 0.0, 1.0 - f),
    ][sector], dtype=np.float64)
    return rgb - rgb.mean() if zero_sum else rgb


def _oracle_cdl_perceptual(
    prophoto, cg,
    per_channel=False, swap_zones=False, flip_toe=False, wrong_log_base=False,
):
    """Independent scalar reimpl of the perceptual offset-only ASC-CDL chain.

    ProPhoto(D50) -> ACEScg [Bradford] -> ACEScct log -> +offset_lum
    +offset_chroma[c] -> ACEScct decode -> ProPhoto [inverse Bradford], floor 0.

    Bug legs: `flip_toe` (toe slope sign-flipped → discontinuous join),
    `wrong_log_base` (ln instead of log2), `per_channel` (chroma NOT zero-sum →
    net lift), `swap_zones` (shadow/highlight masks swapped)."""
    yw = list(_PROPHOTO_LUMINANCE)
    half_span = math.log2(_CG_ZONE_PROXY_WHITE / _CG_ZONE_PROXY_ANCHOR)
    log_anchor = math.log2(_CG_ZONE_PROXY_ANCHOR)
    gamma_b = 2.0 ** (-cg.balance / 100.0)
    p = 1.0 + 2.0 * (1.0 - min(max(cg.blending, 0.0), 100.0) / 100.0)

    def toe_encode(x):
        if flip_toe and x <= _ACESCCT_X_BRK:
            return -_ACESCCT_A * x + _ACESCCT_B  # sign-flipped toe slope
        return _oracle_acescct_encode(x, wrong_log_base=wrong_log_base)

    chroma = {
        name: (_CG_CHROMA_LOG_STRENGTH * (sat / 100.0)
               * _oracle_hue_dir(hue, zero_sum=not per_channel))
        for name, (hue, sat) in {
            "sh": (cg.shadow_hue, cg.shadow_sat),
            "mid": (cg.midtone_hue, cg.midtone_sat),
            "hi": (cg.highlight_hue, cg.highlight_sat),
            "gl": (cg.global_hue, cg.global_sat),
        }.items()
    }

    flat = prophoto.reshape(-1, 3).astype(np.float64)
    out = np.empty_like(flat)
    for i in range(flat.shape[0]):
        px = flat[i]
        acescg = _M_PP_LIN_TO_ACESCG @ px
        log_in = np.array([toe_encode(float(c)) for c in acescg])

        lum = float(px @ np.array(yw))
        proxy = min(max(0.5 + (math.log2(max(lum, 0.0) + _DR_EPS) - log_anchor)
                        / (2.0 * half_span), 0.0), 1.0)
        tt = proxy ** gamma_b
        sh = (1.0 - tt) ** p
        hi = tt ** p
        mid = 1.0 - sh - hi
        if swap_zones:
            sh, hi = hi, sh

        offset_lum = ((sh * cg.shadow_lum / 100.0 + mid * cg.midtone_lum / 100.0
                       + hi * cg.highlight_lum / 100.0 + cg.global_lum / 100.0)
                      * _CG_LUM_LOG_STRENGTH)
        offset_chroma = (sh * chroma["sh"] + mid * chroma["mid"]
                         + hi * chroma["hi"] + chroma["gl"])

        log_out = log_in + offset_lum + offset_chroma
        acescg_out = np.array(
            [_oracle_acescct_decode(float(c), wrong_log_base=wrong_log_base)
             for c in log_out])
        pp_out = _M_ACESCG_TO_PP_LIN @ acescg_out
        out[i] = np.maximum(pp_out, 0.0)
    return out.reshape(prophoto.shape)


def _cdl_edge_pixels():
    """Pixels spanning the regions the CDL chain must get right: dark/mid/bright
    neutral, saturated primaries, and OVERRANGE + saturated (a grey wedge is
    blind to the per-channel-vs-zero-sum chroma error and the overrange path)."""
    rng = np.random.default_rng(20260531)
    rand = rng.random((1024, 3)) * 1.4  # some overrange
    edge = np.array([
        [0.01, 0.01, 0.01], [0.18, 0.18, 0.18], [0.5, 0.5, 0.5], [0.98, 0.98, 0.98],
        [0.8, 0.05, 0.02], [0.05, 0.6, 0.1], [0.1, 0.1, 0.7],   # saturated R/G/B
        [1.4, 0.2, 0.3], [3.0, 0.1, 0.05],                      # overrange + saturated
    ])
    return np.concatenate([rand, edge], axis=0).reshape(-1, 1, 3)


_CDL_WHEELS = ColorGrade(
    shadow_hue=220.0, shadow_sat=70.0, shadow_lum=-20.0,
    midtone_hue=120.0, midtone_sat=40.0, midtone_lum=10.0,
    highlight_hue=40.0, highlight_sat=60.0, highlight_lum=25.0,
    global_hue=300.0, global_sat=20.0, global_lum=-5.0,
    blending=65.0, balance=-30.0,
)


def test_cdl_perceptual_bradford_matrices_match_colour_science():
    """The hardcoded 7-digit ProPhoto<->ACEScg Bradford matrices the oracle uses
    must equal colour-science's (the op converts with `colour.RGB_to_RGB`,
    Bradford), so the oracle can't silently drift from production — while still
    NOT calling that production conversion itself (contract 4)."""
    fwd = colour.matrix_RGB_to_RGB(
        colour.RGB_COLOURSPACES["ProPhoto RGB"], colour.RGB_COLOURSPACES["ACEScg"],
        chromatic_adaptation_transform="Bradford",
    )
    inv = colour.matrix_RGB_to_RGB(
        colour.RGB_COLOURSPACES["ACEScg"], colour.RGB_COLOURSPACES["ProPhoto RGB"],
        chromatic_adaptation_transform="Bradford",
    )
    np.testing.assert_allclose(_M_PP_LIN_TO_ACESCG, fwd, atol=1e-6)
    np.testing.assert_allclose(_M_ACESCG_TO_PP_LIN, inv, atol=1e-6)


def test_acescct_roundtrip_matches_library_constants():
    """The hand-rolled ACEScct (correct X_BRK / A / B / log2) must agree with
    colour.models.log_encoding_ACEScct at black / mid-grey(0.18 -> 0.413588) /
    white / primaries, and round-trip to <1e-6 — pinning the published anchors
    the oracle's toe relies on."""
    from colour.models import log_decoding_ACEScct, log_encoding_ACEScct

    for x in [0.0, 1e-4, _ACESCCT_X_BRK, 0.18, 0.5, 1.0, 4.0]:
        assert abs(_oracle_acescct_encode(x) - float(log_encoding_ACEScct(x))) < 1e-6
    assert abs(_oracle_acescct_encode(0.18) - 0.413588) < 1e-5  # published anchor
    for y in np.linspace(-0.4, 1.0, 50):
        assert abs(_oracle_acescct_decode(float(y))
                   - float(log_decoding_ACEScct(y))) < 1e-6
    for x in [-0.05, 0.0, 0.001, 0.18, 0.7, 2.0]:  # incl. negatives (linear toe)
        assert abs(_oracle_acescct_decode(_oracle_acescct_encode(x)) - x) < 1e-6


def test_cdl_perceptual_matches_independent_oracle():
    """_apply_color_grade_perceptual must equal the independent scalar oracle to
    ~0 over random + saturated + overrange pixels, with all four wheels +
    non-default blending & balance engaged."""
    rgb = _cdl_edge_pixels()
    got = _apply_color_grade_perceptual(rgb.astype(np.float32), _CDL_WHEELS)
    want = _oracle_cdl_perceptual(rgb, _CDL_WHEELS)
    np.testing.assert_allclose(got, want, atol=1e-5)


def test_cdl_perceptual_oracle_detects_wrong_log_base():
    """Sensitivity: the ACEScct log segment is log2. A natural-log base shifts
    every above-toe code value; the real op matches the log2 oracle and NOT the
    ln one."""
    rgb = _cdl_edge_pixels()
    correct = _oracle_cdl_perceptual(rgb, _CDL_WHEELS)
    wrong = _oracle_cdl_perceptual(rgb, _CDL_WHEELS, wrong_log_base=True)
    assert np.max(np.abs(correct - wrong)) > 1e-2
    np.testing.assert_allclose(
        _apply_color_grade_perceptual(rgb.astype(np.float32), _CDL_WHEELS),
        correct, atol=1e-5,
    )


def test_cdl_perceptual_oracle_detects_sign_flipped_toe():
    """Sensitivity: the ACEScct toe slope A is POSITIVE (the v09 spec sign-flipped
    it). A flipped toe diverges on the near-black / saturated channels that fall
    in the linear-toe region; the real op matches the +A oracle."""
    rgb = _cdl_edge_pixels()
    correct = _oracle_cdl_perceptual(rgb, _CDL_WHEELS)
    flipped = _oracle_cdl_perceptual(rgb, _CDL_WHEELS, flip_toe=True)
    assert np.max(np.abs(correct - flipped)) > 1e-2
    np.testing.assert_allclose(
        _apply_color_grade_perceptual(rgb.astype(np.float32), _CDL_WHEELS),
        correct, atol=1e-5,
    )


def test_cdl_perceptual_oracle_detects_non_zero_sum_chroma():
    """Sensitivity (the §0 hue/lift error): the chroma offset is zero-sum (a pure
    Hue+Sat carries no net log lift). A buggy oracle using the raw saturated RGB
    instead injects a brightness shift; the real op matches the zero-sum oracle."""
    rgb = np.array([[[0.18, 0.18, 0.18]]], dtype=np.float64)  # neutral → chroma is all there is
    cg = ColorGrade(global_hue=240.0, global_sat=100.0)        # pure blue, no lum
    correct = _oracle_cdl_perceptual(rgb, cg, per_channel=False)
    wrong = _oracle_cdl_perceptual(rgb, cg, per_channel=True)
    assert np.max(np.abs(correct - wrong)) > 1e-2
    np.testing.assert_allclose(
        _apply_color_grade_perceptual(rgb.astype(np.float32), cg), correct, atol=1e-5,
    )


def test_cdl_perceptual_oracle_detects_swapped_zones():
    """Sensitivity: shadow vs highlight masks are distinct. Swapping them sends
    the shadow tint to the highlights — diverging on a dark pixel where the two
    wheels carry different colours."""
    rgb = np.array([[[0.03, 0.03, 0.03]]], dtype=np.float64)  # a deep shadow
    cg = ColorGrade(
        shadow_hue=240.0, shadow_sat=100.0, shadow_lum=-40.0,    # blue, dark shadows
        highlight_hue=40.0, highlight_sat=100.0, highlight_lum=40.0,  # orange, bright highs
    )
    correct = _oracle_cdl_perceptual(rgb, cg, swap_zones=False)
    wrong = _oracle_cdl_perceptual(rgb, cg, swap_zones=True)
    assert np.max(np.abs(correct - wrong)) > 1e-2
    np.testing.assert_allclose(
        _apply_color_grade_perceptual(rgb.astype(np.float32), cg), correct, atol=1e-5,
    )


def test_cdl_perceptual_identity_byte_exact():
    """A zero grade (no wheel Sat/Lum) returns the input byte-exact — neutral +
    saturated + overrange + near-black — so PERCEPTUAL stays bit-identical to
    FAITHFUL on a no-grade render (the ship-gate guarantee). is_identity()
    short-circuits before the ACEScct + Bradford round-trip (reversible only to
    float tolerance, so the short-circuit is mandatory)."""
    rng = np.random.default_rng(7)
    rgb = (rng.random((64, 3)) * 1.5).astype(np.float32).reshape(-1, 1, 3)
    edge = np.array([
        [0.0, 0.0, 0.0], [0.001, 0.001, 0.001], [0.8, 0.05, 0.02], [1.4, 0.2, 0.3],
    ], dtype=np.float32).reshape(-1, 1, 3)
    rgb = np.concatenate([rgb, edge], axis=0)
    out = _apply_color_grade_perceptual(rgb, ColorGrade())  # all-zero → identity
    assert np.array_equal(out, rgb)
    assert out is rgb  # the literal input object, not a round-tripped copy


def test_cdl_perceptual_global_lum_uniform_log_offset():
    """+Global Luminance adds the SAME log offset to all three ACEScct channels
    (a uniform tonal lift, not a per-channel tint). Verified by encoding the
    op's in/out to ACEScct and checking the per-channel delta is uniform and
    equals global_lum/100 * K_lum_log."""
    neutral = np.array([[[0.18, 0.18, 0.18]]], dtype=np.float64)
    cg = ColorGrade(global_lum=50.0)
    out = _apply_color_grade_perceptual(neutral.astype(np.float32), cg)

    def to_acescct(pp):
        acescg = (_M_PP_LIN_TO_ACESCG @ pp.reshape(-1, 3).T).T
        return np.array([[_oracle_acescct_encode(float(c)) for c in row]
                         for row in acescg])

    delta = to_acescct(np.asarray(out, dtype=np.float64)) - to_acescct(neutral)
    expected = 0.5 * _CG_LUM_LOG_STRENGTH  # global_lum/100 * K_lum_log
    np.testing.assert_allclose(delta.ravel(), expected, atol=1e-4)
    assert delta.std() < 1e-5  # uniform across channels


def test_cdl_perceptual_shadow_lum_lifts_log_shadows():
    """+Shadow Luminance lifts log shadows measurably while leaving highlights
    essentially untouched (the zone mask is real, not global). A sensitivity
    proof: the dark-pixel log lift is > 1e-3 and far exceeds the bright pixel's."""
    sh = ColorGrade(shadow_lum=80.0)
    dark = np.array([[[0.02, 0.02, 0.02]]], dtype=np.float32)
    bright = np.array([[[0.9, 0.9, 0.9]]], dtype=np.float32)
    out_dark = _apply_color_grade_perceptual(dark, sh)
    out_bright = _apply_color_grade_perceptual(bright, sh)
    lift_dark = float(out_dark.ravel()[0]) - float(dark.ravel()[0])
    lift_bright = abs(float(out_bright.ravel()[0]) - float(bright.ravel()[0]))
    assert lift_dark > 1e-3                 # shadows lifted measurably
    assert lift_dark > 20.0 * lift_bright   # highlights ~untouched (zone-gated)


def test_cdl_perceptual_highlight_lum_dominated_by_highlight_wheel():
    """A near-white pixel must be moved DOMINANTLY by the Highlight wheel, not the
    Midtone wheel — the guard that the log-domain zone proxy is placed correctly
    (0.18 -> 0.5, white -> 1.0). The mirror of the shadow-lift test; without the
    explicit proxy normalization a raw ACEScct proxy would read white as ~half
    midtone and this would fail."""
    near_white = np.array([[[0.95, 0.95, 0.95]]], dtype=np.float32)
    only_hi = _apply_color_grade_perceptual(near_white, ColorGrade(highlight_lum=60.0))
    only_mid = _apply_color_grade_perceptual(near_white, ColorGrade(midtone_lum=60.0))
    move_hi = abs(float(only_hi.ravel()[0]) - 0.95)
    move_mid = abs(float(only_mid.ravel()[0]) - 0.95)
    assert move_hi > 3.0 * move_mid  # highlight wheel dominates the near-white pixel


def test_cdl_perceptual_no_top_clamp_preserves_overrange():
    """An overrange (>1) input highlight survives the CDL chain un-truncated —
    scene-referred, no display ceiling. Out-of-AP1 is the gated RGC pass's job in
    output.py, NOT this op (which must not hard-clip)."""
    over = np.array([[[3.0, 0.1, 0.05]]], dtype=np.float32)
    out = _apply_color_grade_perceptual(over, ColorGrade(highlight_sat=50.0, highlight_hue=40.0))
    assert float(out.max()) > 1.0  # overrange preserved
