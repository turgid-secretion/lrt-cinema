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

import numpy as np
import pytest

colour = pytest.importorskip("colour")  # noqa: F841  (output.py needs it; gate import)

from lrt_cinema.dcp import DCPProfile, HsvCube, interpolate_color_matrix  # noqa: E402
from lrt_cinema.lut3d_baker import (  # noqa: E402
    _apply_hsv_cube,
    _hsv_to_rgb_dcp,
    _rgb_to_hsv_dcp,
)
from lrt_cinema.output import _prophoto_to_display, write_tiff_display  # noqa: E402
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
