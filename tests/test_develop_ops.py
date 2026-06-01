"""Per-op math tests for `lrt_cinema.develop_ops`.

The LR-authored develop ops fire AFTER the DCP shaping stages, on linear
ProPhoto. These tests use synthetic inputs (gradients, gray patches,
saturated colors) with known closed-form outputs — no Adobe binary
required.
"""

from __future__ import annotations

import numpy as np
import pytest

from lrt_cinema.develop_ops import (
    _DR_GUIDED_RADIUS,
    _PROPHOTO_LUMINANCE,
    _box_sum,
    _dr_compress_luminance,
    _dr_slopes,
    _guided_base_log,
    apply_blacks_2012,
    apply_color_grade,
    apply_contrast_2012,
    apply_develop_ops,
    apply_dr_compression,
    apply_exposure_2012,
    apply_hsl,
    apply_saturation,
    apply_sharpness,
    apply_stage_11_linear,
    apply_stage_12_perceptual,
    apply_tone_curve_pv2012,
    apply_vibrance,
)
from lrt_cinema.ir import ColorGrade, DevelopOps, HslBands, RenderIntent, TonePoint

# ---------------------------------------------------------------------------
# Stage 11 — Exposure2012
# ---------------------------------------------------------------------------


def test_exposure_2012_zero_is_no_op():
    x = np.array([[[0.1, 0.5, 0.9]]], dtype=np.float32)
    np.testing.assert_array_equal(apply_exposure_2012(x, 0.0), x)


def test_exposure_2012_plus_one_ev_doubles():
    x = np.array([[[0.25, 0.5, 0.75]]], dtype=np.float32)
    out = apply_exposure_2012(x, 1.0)
    np.testing.assert_allclose(out, x * 2.0, rtol=1e-6)


def test_exposure_2012_minus_one_ev_halves():
    x = np.array([[[0.5, 1.0, 2.0]]], dtype=np.float32)
    out = apply_exposure_2012(x, -1.0)
    np.testing.assert_allclose(out, x * 0.5, rtol=1e-6)


def test_exposure_2012_preserves_dtype():
    x = np.zeros((4, 4, 3), dtype=np.float32)
    assert apply_exposure_2012(x, 2.0).dtype == np.float32


# ---------------------------------------------------------------------------
# Stage 11 — Blacks2012
# ---------------------------------------------------------------------------


def test_blacks_2012_zero_is_no_op():
    x = np.full((2, 2, 3), 0.5, dtype=np.float32)
    np.testing.assert_array_equal(apply_blacks_2012(x, 0.0), x)


def test_blacks_2012_positive_lifts():
    x = np.array([[[0.0, 0.1, 0.5]]], dtype=np.float32)
    out = apply_blacks_2012(x, 100.0)  # max lift
    assert (out > x).all()
    # 100 × 0.0005 = 0.05 lift; check magnitude.
    np.testing.assert_allclose(out, x + 0.05, rtol=1e-5)


def test_blacks_2012_negative_crushes_clamped_at_zero():
    x = np.array([[[0.0, 0.01, 0.1]]], dtype=np.float32)
    out = apply_blacks_2012(x, -100.0)  # -100 × 0.0005 = -0.05
    # 0.0 - 0.05 = -0.05 → clamped to 0; 0.01 - 0.05 = -0.04 → clamped to 0.
    assert out[0, 0, 0] == 0.0
    assert out[0, 0, 1] == 0.0
    np.testing.assert_allclose(out[0, 0, 2], 0.05, rtol=1e-5)


# ---------------------------------------------------------------------------
# Stage 12 — ToneCurvePV2012
# ---------------------------------------------------------------------------


def test_tone_curve_empty_is_no_op():
    x = np.full((2, 2, 3), 0.5, dtype=np.float32)
    np.testing.assert_array_equal(apply_tone_curve_pv2012(x, []), x)


def test_tone_curve_single_point_is_no_op():
    x = np.full((2, 2, 3), 0.5, dtype=np.float32)
    np.testing.assert_array_equal(
        apply_tone_curve_pv2012(x, [TonePoint(0.5, 0.5)]), x,
    )


def test_tone_curve_identity_passes_through():
    x = np.array([[[0.1, 0.5, 0.9]]], dtype=np.float32)
    out = apply_tone_curve_pv2012(x, [TonePoint(0.0, 0.0), TonePoint(1.0, 1.0)])
    np.testing.assert_array_equal(out, x)


def test_tone_curve_s_curve_pushes_midtones():
    """S-curve: lift midtones by mapping 0.5 → 0.6. Spline interpolates;
    inputs near 0.5 should rise."""
    x = np.full((1, 1, 3), 0.5, dtype=np.float32)
    out = apply_tone_curve_pv2012(
        x,
        [
            TonePoint(0.0, 0.0),
            TonePoint(0.5, 0.6),
            TonePoint(1.0, 1.0),
        ],
    )
    # Adobe Hermite C2 spline maps 0.5 exactly to 0.6 at a control point.
    np.testing.assert_allclose(out[0, 0], 0.6, atol=1e-4)


# ---------------------------------------------------------------------------
# Stage 12 — Saturation
# ---------------------------------------------------------------------------


def test_saturation_zero_is_no_op():
    x = np.array([[[0.6, 0.2, 0.4]]], dtype=np.float32)
    np.testing.assert_array_equal(apply_saturation(x, 0.0), x)


def test_saturation_minus_100_desaturates_to_gray():
    """sat=-100 → mult=0 → no saturation. Output should be grey (R=G=B
    at the original V channel)."""
    x = np.array([[[0.8, 0.2, 0.5]]], dtype=np.float32)
    out = apply_saturation(x, -100.0)
    # All three channels should converge to the value (max of input, by HSV).
    assert abs(out[0, 0, 0] - out[0, 0, 1]) < 1e-5
    assert abs(out[0, 0, 1] - out[0, 0, 2]) < 1e-5


def test_saturation_plus_100_doubles_chroma():
    """sat=+100 → mult=2. Chroma (max-min) doubles relative to original."""
    x = np.array([[[0.6, 0.3, 0.4]]], dtype=np.float32)
    chroma_in = x.max(axis=-1) - x.min(axis=-1)
    out = apply_saturation(x, 100.0)
    chroma_out = out.max(axis=-1) - out.min(axis=-1)
    # Doubling chroma: chroma_out ≈ 2 * chroma_in (modulo HSV gamut clamping).
    np.testing.assert_allclose(chroma_out, 2.0 * chroma_in, rtol=1e-3)


# ---------------------------------------------------------------------------
# Stage 12 — Vibrance
# ---------------------------------------------------------------------------


def test_vibrance_zero_is_no_op():
    x = np.array([[[0.6, 0.2, 0.4]]], dtype=np.float32)
    np.testing.assert_array_equal(apply_vibrance(x, 0.0), x)


def test_vibrance_boosts_low_sat_more_than_high_sat():
    """Vibrance: out_s = s + (vib/100) * s * (1-s). Boost peaks at s=0.5;
    near-zero saturation gains more than near-1 saturation."""
    low_sat = np.array([[[0.55, 0.45, 0.50]]], dtype=np.float32)   # near-grey
    high_sat = np.array([[[1.00, 0.05, 0.05]]], dtype=np.float32)  # near-red
    out_low = apply_vibrance(low_sat, 100.0)
    out_high = apply_vibrance(high_sat, 100.0)
    chroma_low_in = low_sat.max() - low_sat.min()
    chroma_low_out = out_low.max() - out_low.min()
    chroma_high_in = high_sat.max() - high_sat.min()
    chroma_high_out = out_high.max() - out_high.min()
    rel_low = (chroma_low_out - chroma_low_in) / chroma_low_in
    rel_high = (chroma_high_out - chroma_high_in) / max(chroma_high_in, 1e-9)
    assert rel_low > rel_high  # low-sat pixel gains more relative chroma


# ---------------------------------------------------------------------------
# Stage 12 — HSL panel (8 hue bands × {Hue, Saturation, Luminance})
# ---------------------------------------------------------------------------


def test_hsl_default_is_byte_exact_no_op():
    """Default (all-zero) HslBands → byte-exact passthrough (short-circuit before
    the lossy HSV round-trip). Guarantees the ΔE ship gate is unaffected."""
    x = np.random.rand(8, 8, 3).astype(np.float32)
    np.testing.assert_array_equal(apply_hsl(x, HslBands()), x)


def test_hsl_saturation_band_targets_only_its_hue():
    """Red-band +Saturation boosts a red pixel's chroma but leaves a blue pixel
    (a different band) essentially unchanged."""
    red = np.array([[[0.6, 0.2, 0.2]]], dtype=np.float32)
    blue = np.array([[[0.2, 0.2, 0.6]]], dtype=np.float32)
    red_sat = HslBands(saturation=(80.0, 0, 0, 0, 0, 0, 0, 0))

    def chroma(a):
        return float(a.max() - a.min())

    assert chroma(apply_hsl(red, red_sat)) > chroma(red) + 1e-3   # red gains chroma
    np.testing.assert_allclose(apply_hsl(blue, red_sat), blue, atol=1e-6)  # blue untouched


def test_hsl_luminance_leaves_neutrals_unchanged():
    """A neutral grey has no hue → the saturation gate must keep ANY band's
    Luminance slider from moving it (a grey wedge stays grey; CLAUDE.md §0).
    The same slider DOES darken a saturated pixel of that band."""
    grey = np.array([[[0.5, 0.5, 0.5]]], dtype=np.float32)
    red = np.array([[[0.7, 0.1, 0.1]]], dtype=np.float32)
    red_lum_down = HslBands(luminance=(-60.0, 0, 0, 0, 0, 0, 0, 0))
    np.testing.assert_allclose(apply_hsl(grey, red_lum_down), grey, atol=1e-6)
    assert apply_hsl(red, red_lum_down).max() < red.max() - 1e-3  # red darkens


def test_hsl_saturated_past_gamut_emits_no_negative_channels():
    """An already-saturated pixel × a large +Saturation pushes HSV S past 1;
    without the [0,1] clamp on recompose, _hsv_to_rgb_dcp would emit negative
    ProPhoto channels (the apply_saturation lesson). Must stay non-negative."""
    x = np.array([[[0.80, 0.10, 0.05]]], dtype=np.float32)  # S≈0.94
    out = apply_hsl(x, HslBands(saturation=(100.0, 0, 0, 0, 0, 0, 0, 0)))
    assert out.min() >= 0.0, f"negative channel leaked: min={out.min()}"


def test_hsl_invalid_negative_pixel_passes_through():
    """A pixel with a negative channel (out-of-gamut, hue undefined) is passed
    through unchanged — matching the HueSatMap / apply_saturation convention."""
    x = np.array([[[0.6, -0.05, 0.2]]], dtype=np.float32)
    np.testing.assert_array_equal(apply_hsl(x, HslBands(saturation=(50.0,) * 8)), x)


def test_hsl_all_bands_equal_acts_as_global_saturation():
    """Partition-of-unity property: setting all 8 Saturation bands to the same
    value is equivalent to a single global Saturation (weights sum to 1)."""
    x = np.array([[[0.6, 0.3, 0.2], [0.2, 0.5, 0.4], [0.1, 0.2, 0.7]]], dtype=np.float64)
    out_hsl = apply_hsl(x, HslBands(saturation=(50.0,) * 8))
    out_global = apply_saturation(x, 50.0)
    np.testing.assert_allclose(out_hsl, out_global, atol=1e-6)


# ---------------------------------------------------------------------------
# Stage 12 — Color Grading wheels
# ---------------------------------------------------------------------------


def test_color_grade_default_is_byte_exact_no_op():
    """Default ColorGrade (no tint) → byte-exact passthrough (short-circuit).
    Guarantees the ΔE ship gate is unaffected when no grade is authored."""
    x = np.random.rand(8, 8, 3).astype(np.float32)
    np.testing.assert_array_equal(apply_color_grade(x, ColorGrade()), x)


def test_color_grade_shadows_tint_darks_not_brights():
    """A saturated Shadow wheel tints a dark pixel toward its hue far more than
    a bright pixel (the luminance zone mask). Neutrals ARE tinted here — unlike
    HSL, that is the intended split-tone behaviour."""
    dark = np.array([[[0.02, 0.02, 0.02]]], dtype=np.float64)
    bright = np.array([[[0.95, 0.95, 0.95]]], dtype=np.float64)
    cg = ColorGrade(shadow_hue=240.0, shadow_sat=100.0)  # blue shadows
    d_dark = apply_color_grade(dark, cg)[0, 0, 2] - dark[0, 0, 2]
    d_bright = apply_color_grade(bright, cg)[0, 0, 2] - bright[0, 0, 2]
    assert d_dark > 0.01                 # shadows pick up blue
    assert abs(d_bright) < abs(d_dark)   # highlights barely move


def test_color_grade_global_applies_everywhere():
    """The Global wheel tints dark and bright pixels alike (no zone mask)."""
    dark = np.array([[[0.05, 0.05, 0.05]]], dtype=np.float64)
    bright = np.array([[[0.9, 0.9, 0.9]]], dtype=np.float64)
    cg = ColorGrade(global_hue=120.0, global_sat=100.0)  # green everywhere
    assert apply_color_grade(dark, cg)[0, 0, 1] - dark[0, 0, 1] > 0.01
    assert apply_color_grade(bright, cg)[0, 0, 1] - bright[0, 0, 1] > 0.01


def test_color_grade_saturated_pixel_emits_no_negative_channels():
    """A saturated pixel + a strong opposing tint must clamp at 0, never emit a
    negative ProPhoto channel into output.py's colour matrix."""
    sat = np.array([[[0.8, 0.05, 0.02]]], dtype=np.float32)  # saturated red
    out = apply_color_grade(
        sat, ColorGrade(global_hue=180.0, global_sat=100.0, global_lum=-100.0),
    )
    assert out.min() >= 0.0, f"negative channel leaked: min={out.min()}"


def test_color_grade_hue_only_wheel_is_no_op():
    """A wheel with Hue set but Saturation=0 produces no tint (is_identity)."""
    x = np.random.rand(4, 4, 3).astype(np.float32)
    np.testing.assert_array_equal(
        apply_color_grade(x, ColorGrade(shadow_hue=200.0, highlight_hue=40.0)), x,
    )


# ---------------------------------------------------------------------------
# Stage 12 — Contrast2012
# ---------------------------------------------------------------------------


def test_contrast_2012_zero_is_no_op():
    x = np.full((2, 2, 3), 0.5, dtype=np.float32)
    np.testing.assert_array_equal(apply_contrast_2012(x, 0.0), x)


def test_contrast_2012_positive_expands_around_pivot():
    """contrast=+100 → gain=2. Pivot at 0.18. Output = 0.18 + (in - 0.18) * 2."""
    x = np.array([[[0.05, 0.18, 0.4, 0.9]]], dtype=np.float32).reshape(1, 4, 1)
    x = np.broadcast_to(x, (1, 4, 3)).astype(np.float32).copy()
    out = apply_contrast_2012(x, 100.0)
    expected = 0.18 + (x - 0.18) * 2.0
    expected = np.maximum(expected, 0.0)
    np.testing.assert_allclose(out, expected, rtol=1e-5)


def test_contrast_2012_pivot_unchanged():
    pivot = np.full((1, 1, 3), 0.18, dtype=np.float32)
    np.testing.assert_allclose(apply_contrast_2012(pivot, 50.0), pivot, rtol=1e-5)


# ---------------------------------------------------------------------------
# Stage 12 — Sharpness (v0.6 no-op)
# ---------------------------------------------------------------------------


def test_sharpness_is_no_op_in_v06():
    """v0.6 deliberately returns input unchanged — sharpening belongs in
    the grade stage, not the linear-render stage. v0.6.x may revisit."""
    x = np.random.rand(8, 8, 3).astype(np.float32)
    np.testing.assert_array_equal(apply_sharpness(x, 100.0), x)


# ---------------------------------------------------------------------------
# Dispatchers + integration
# ---------------------------------------------------------------------------


def test_apply_develop_ops_all_default_is_no_op():
    x = np.random.rand(4, 4, 3).astype(np.float32)
    np.testing.assert_array_equal(apply_develop_ops(x, DevelopOps()), x)


# ---------------------------------------------------------------------------
# Dual-mode render intent (DECISIONS.md §7) — the Stage-12 applicator seam
# ---------------------------------------------------------------------------


def test_render_intent_default_is_faithful():
    """apply_develop_ops with no intent == explicit FAITHFUL."""
    x = np.random.rand(4, 4, 3).astype(np.float32)
    ops = DevelopOps(hsl=HslBands(saturation=(40.0, 0, 0, 0, 0, 0, 0, 0)))
    np.testing.assert_array_equal(
        apply_develop_ops(x, ops), apply_develop_ops(x, ops, RenderIntent.FAITHFUL),
    )


def test_render_intent_identity_byte_exact_both_modes():
    """Default ops are a byte-exact no-op under BOTH intents — the ship-gate
    guarantee survives the dual-mode seam."""
    x = np.random.rand(8, 8, 3).astype(np.float32)
    for intent in RenderIntent:
        np.testing.assert_array_equal(apply_develop_ops(x, DevelopOps(), intent), x)


def test_render_intent_routes_to_perceptual_applicators(monkeypatch):
    """The seam wiring: PERCEPTUAL routes HSL + Color-Grade through the
    `_apply_*_perceptual` functions (in order); FAITHFUL does not touch them.
    Tested via monkeypatch so it validates ROUTING independent of the currently
    aliased stub bodies — it survives steps 2-3 filling in the real primitives."""
    import lrt_cinema.develop_ops as do

    calls: list[str] = []

    def hsl_p(pp, hsl):
        calls.append("hsl_perceptual")
        return pp

    def cg_p(pp, cg):
        calls.append("cg_perceptual")
        return pp

    monkeypatch.setattr(do, "_apply_hsl_perceptual", hsl_p)
    monkeypatch.setattr(do, "_apply_color_grade_perceptual", cg_p)

    x = np.zeros((2, 2, 3), dtype=np.float32)
    ops = DevelopOps(
        hsl=HslBands(saturation=(10.0, 0, 0, 0, 0, 0, 0, 0)),
        color_grade=ColorGrade(shadow_sat=10.0),
    )
    do.apply_develop_ops(x, ops, RenderIntent.FAITHFUL)
    assert calls == []                                  # faithful: not routed
    do.apply_develop_ops(x, ops, RenderIntent.PERCEPTUAL)
    assert calls == ["hsl_perceptual", "cg_perceptual"]  # perceptual: routed, in order


def test_perceptual_hsl_diverges_from_faithful():
    """Step 3 (OKLCh) LANDED: with an HSL band engaged the PERCEPTUAL applicator
    (hue-stable OKLCh) is intentionally DIFFERENT from the faithful Adobe-hexcone
    HSV — the dual-mode seam now produces two distinct HSL grades. A *zero* HSL
    must still match byte-exact (the identity short-circuit) so the ship gate
    stays green. (This replaces the prior alias assertion, which step 3 flips.)"""
    x = np.random.rand(8, 8, 3).astype(np.float32)

    # Engaged band → the two intents diverge measurably.
    graded = DevelopOps(hsl=HslBands(saturation=(50.0, 0, 0, 0, 0, 0, 0, 0)))
    faithful = apply_develop_ops(x, graded, RenderIntent.FAITHFUL)
    perceptual = apply_develop_ops(x, graded, RenderIntent.PERCEPTUAL)
    assert np.max(np.abs(faithful - perceptual)) > 1e-3

    # Zero HSL → byte-identical under both intents (no-grade ship gate).
    zero = DevelopOps(hsl=HslBands())
    np.testing.assert_array_equal(
        apply_develop_ops(x, zero, RenderIntent.FAITHFUL),
        apply_develop_ops(x, zero, RenderIntent.PERCEPTUAL),
    )


def test_perceptual_color_grade_diverges_from_faithful():
    """Step 2 (CDL) LANDED: with a Color-Grade wheel engaged the PERCEPTUAL
    applicator (offset-only ASC-CDL in ACEScct log) is intentionally DIFFERENT
    from the faithful split-tone (additive-in-linear-ProPhoto) — the dual-mode
    seam now produces two distinct grades. A *zero* Color-Grade must still match
    byte-exact (the identity short-circuit) so the ship gate stays green."""
    x = np.random.rand(8, 8, 3).astype(np.float32)

    # Engaged wheel → the two intents diverge measurably.
    graded = DevelopOps(color_grade=ColorGrade(global_hue=120.0, global_sat=50.0))
    faithful = apply_develop_ops(x, graded, RenderIntent.FAITHFUL)
    perceptual = apply_develop_ops(x, graded, RenderIntent.PERCEPTUAL)
    assert np.max(np.abs(faithful - perceptual)) > 1e-3

    # Zero Color-Grade → byte-identical under both intents (no-grade ship gate).
    zero = DevelopOps(color_grade=ColorGrade())
    np.testing.assert_array_equal(
        apply_develop_ops(x, zero, RenderIntent.FAITHFUL),
        apply_develop_ops(x, zero, RenderIntent.PERCEPTUAL),
    )


def test_apply_develop_ops_chains_in_order():
    """All ops together: Exposure +1 then Blacks +20 then everything else
    default. Expected: x*2 + 0.01 (since 20*0.0005=0.01)."""
    x = np.array([[[0.1, 0.2, 0.3]]], dtype=np.float32)
    ops = DevelopOps(exposure_ev=1.0, blacks=20.0)
    out = apply_develop_ops(x, ops)
    expected = x * 2.0 + 0.01
    np.testing.assert_allclose(out, expected, rtol=1e-5)


def test_apply_stage_11_then_stage_12_matches_full_dispatcher():
    x = np.random.rand(4, 4, 3).astype(np.float32)
    ops = DevelopOps(
        exposure_ev=0.3, blacks=10.0, contrast=20.0,
        saturation=15.0, tone_curve=[
            TonePoint(0.0, 0.0), TonePoint(0.5, 0.55), TonePoint(1.0, 1.0),
        ],
    )
    full = apply_develop_ops(x, ops)
    decomposed = apply_stage_12_perceptual(apply_stage_11_linear(x, ops), ops)
    np.testing.assert_array_equal(full, decomposed)


def test_saturation_past_s1_emits_no_negative_channels():
    """Axis-1 oracle for the apply_saturation HSV-S clamp (the headline bug).

    An already-saturated pixel × a big +sat pushes S*mult > 1; without clamping
    S to [0,1], _hsv_to_rgb_dcp emits NEGATIVE linear-ProPhoto channels, which
    output.py's ProPhoto→target matrix then mixes in BEFORE the [0,1] clip — so
    saturated colour renders wrong (a grey wedge is blind; see CLAUDE.md §0).
    The clamp mirrors apply_vibrance. NB: a pixel sitting ON S=1 cannot detect
    this — S*mult must EXCEED 1, so use a sub-1-S pixel with a large +sat."""
    x = np.array([[[0.80, 0.10, 0.05]]], dtype=np.float32)  # S≈0.94 (< 1)
    out = apply_saturation(x, 80.0)  # mult=1.8 → S*1.8≈1.69 > 1 before clamp
    assert out.min() >= 0.0, f"negative linear-ProPhoto channel leaked: min={out.min()}"


# ---------------------------------------------------------------------------
# Stage 12 — DR-compression (perceptual-only, Highlights/Shadows/Whites)
# ---------------------------------------------------------------------------


def test_dr_compression_default_is_byte_exact_no_op():
    """All three sliders 0 → byte-exact passthrough (short-circuit before any log
    math). Guarantees the ΔE ship gate is unaffected on the perceptual path."""
    x = np.random.rand(8, 8, 3).astype(np.float32)
    np.testing.assert_array_equal(apply_dr_compression(x, 0.0, 0.0, 0.0), x)


def test_dr_compression_preserves_dtype():
    x = np.random.rand(8, 8, 3).astype(np.float32)
    assert apply_dr_compression(x, 30.0, 20.0, 10.0).dtype == np.float32


def test_box_sum_matches_scipy_uniform_filter_interior():
    """The He-et-al. box filter (the guided-filter kernel — the op's highest-risk
    code) must equal an independent moving-average. A constant-input test is BLIND
    to off-by-one bugs (constant in → constant out regardless), so cross-check the
    box MEAN against scipy.ndimage.uniform_filter on NON-constant input, at
    interior pixels (where both use the full (2r+1)² window)."""
    ndi = pytest.importorskip("scipy.ndimage")
    img = np.random.default_rng(11).random((48, 40))
    r = 8
    n = _box_sum(np.ones_like(img), r)
    box_mean = _box_sum(img, r) / n
    ref = ndi.uniform_filter(img, size=2 * r + 1, mode="constant")
    interior = (slice(r, img.shape[0] - r), slice(r, img.shape[1] - r))
    np.testing.assert_allclose(box_mean[interior], ref[interior], atol=1e-12)


def test_box_sum_radius_zero_is_identity():
    """r=0 (a 1-wide / sub-window array) returns the input — the path that makes
    apply_dr_compression collapse to the global pointwise law."""
    img = np.random.default_rng(12).random((5, 7))
    np.testing.assert_allclose(_box_sum(img, 0), img, atol=0.0)


def test_box_sum_rejects_radius_larger_than_image():
    """A radius whose (2r+1) window exceeds the image raises a clear error instead
    of an opaque broadcast failure (the caller clamps r; this guards reuse)."""
    with pytest.raises(ValueError, match="too large"):
        _box_sum(np.zeros((3, 3)), 4)


def test_guided_base_smooths_but_preserves_edge():
    """The guided base must reduce variance in a noisy-flat region (smoothing) yet
    track a strong step edge (edge preservation) — the defining guided-filter
    behaviour, distinguishing it from a plain blur."""
    rng = np.random.default_rng(13)
    flat = 1.0 + 0.05 * rng.standard_normal((40, 40))  # noisy flat (log-domain)
    base = _guided_base_log(flat, _DR_GUIDED_RADIUS, 0.01)
    assert base.var() < flat.var() * 0.5  # noise smoothed

    step = np.zeros((40, 40))
    step[:, 20:] = 5.0  # a 5-stop edge at col 20 — far above sqrt(eps)=0.1
    base_step = _guided_base_log(step, _DR_GUIDED_RADIUS, 0.01)
    # Probe ADJACENT to the edge (cols 18/21): the guided filter holds the step
    # sharp (≈0/5, a→1 across it) where a plain box mean would smear it to ≈2.1/2.9
    # (its window straddles the step). Probing far from the edge would NOT
    # discriminate — a box blur is exact there too, so the old far-probe assertion
    # passed for any local averaging.
    assert base_step[:, 18].mean() < 0.6, "left of edge smeared (not edge-preserving)"
    assert base_step[:, 21].mean() > 4.4, "right of edge smeared (not edge-preserving)"


def _dr_textured_image(h=80, w=80):
    """A neutral image with a large low-freq luminance ramp (the base, ±4 stops)
    plus high-freq micro-texture (the detail)."""
    yy, xx = np.mgrid[0:h, 0:w]
    base_stops = -4.0 + 8.0 * (xx / (w - 1))
    detail = 0.25 * np.sin(xx * 1.3) * np.sin(yy * 1.3)
    lum = 0.18 * 2.0 ** (base_stops + detail)
    return np.repeat(lum[..., None], 3, axis=2)


def _log_detail_rms(img):
    """RMS of the high-pass (log-luminance minus its local box mean)."""
    log_l = np.log2(np.maximum(img @ _PROPHOTO_LUMINANCE, 0.0) + 1e-6)
    return float(np.std(log_l - _box_sum(log_l, 4) / _box_sum(np.ones_like(log_l), 4)))


def test_dr_compression_is_genuinely_local():
    """The headline property: applied as an IMAGE (local guided base/detail split)
    the op differs measurably from the same pixels fed as a 1-wide strip (which
    collapses to the GLOBAL law), and it retains MORE local micro-contrast than the
    global crush. (The guided-filter first cut is conservative — eps=0.01 favours
    edge preservation — so the margin is modest; the local-Laplacian upgrade is the
    quality follow-up. This asserts it is genuinely local, not the magnitude.)"""
    img = _dr_textured_image()
    h, w, _ = img.shape
    hi, sh, wh = 60.0, 40.0, 60.0
    local = apply_dr_compression(img, hi, sh, wh)
    glob = apply_dr_compression(img.reshape(-1, 1, 3), hi, sh, wh).reshape(h, w, 3)

    assert np.max(np.abs(local - glob)) > 1e-3, "local path is indistinguishable from global"
    assert _log_detail_rms(local) > _log_detail_rms(glob), "local did not retain more detail"


def test_dr_compression_flat_image_reduces_to_global_law():
    """On a spatially flat-luminance image the base/detail split is a no-op, so the
    op equals the pointwise law + ratio reapply (the limiting case the oracle
    validates)."""
    rng = np.random.default_rng(14)
    # Random hues, each pixel renormalised to the SAME luminance (0.42) → the
    # guided base equals the (constant) luminance, detail is 0, so the op is the
    # pointwise law + ratio reapply.
    pix = rng.random((24, 24, 3)) + 0.1
    lum_in = pix @ _PROPHOTO_LUMINANCE
    pix = pix * (0.42 / lum_in)[..., None]
    lum_in = pix @ _PROPHOTO_LUMINANCE

    out = apply_dr_compression(pix, 50.0, -30.0, 20.0)
    c_lo, c_hi, c_top = _dr_slopes(50.0, -30.0, 20.0)
    lum_out = _dr_compress_luminance(lum_in, c_lo, c_hi, c_top)
    ratio = lum_out / np.maximum(lum_in, 1e-6)
    np.testing.assert_allclose(out, np.maximum(pix * ratio[..., None], 0.0), atol=1e-6)


def test_dr_compression_no_negative_channels_on_saturated_overrange():
    """A saturated + overrange pixel under strong compression stays non-negative
    (floor at 0; the ratio reapply cannot introduce a negative)."""
    x = np.array([[[3.0, 0.05, 0.02]], [[1.4, 0.2, 0.3]]], dtype=np.float32)
    out = apply_dr_compression(x, 100.0, -100.0, 100.0)
    assert out.min() >= 0.0, f"negative channel leaked: min={out.min()}"


def test_dr_compression_no_halo_overshoot_at_step_edge():
    """A clean step edge driven through the full op must not ring: the guided base
    is edge-preserving (a→1 across the step → detail≈0 there) and the guided filter
    is gradient-reversal-free, so the recombined output stays within the two
    plateaus' compressed levels to <1% of their range — no halo overshoot/
    undershoot (v10 §1.3/§3.8). The guided filter is NOT provably halo-free (halos
    grow with radius); this bounds the first cut's measured ring and catches a
    gross-ringing regression. The halo-free local-Laplacian base is the follow-up."""
    h = w = 32
    lum = np.full((h, w), 0.1)
    lum[:, w // 2:] = 4.0  # a strong step (~5.3 stops), no texture
    img = np.repeat(lum[..., None], 3, axis=2)
    out_lum = apply_dr_compression(img, 60.0, 40.0, 60.0) @ _PROPHOTO_LUMINANCE

    c_lo, c_hi, c_top = _dr_slopes(60.0, 40.0, 60.0)
    levels = _dr_compress_luminance(np.array([0.1, 4.0]), c_lo, c_hi, c_top)
    lo, hi = float(levels[0]), float(levels[1])
    tol = 0.01 * (hi - lo)  # <1% of the plateau range
    assert out_lum.min() >= lo - tol, f"undershoot halo: {lo - out_lum.min():.4f}"
    assert out_lum.max() <= hi + tol, f"overshoot halo: {out_lum.max() - hi:.4f}"


# ---------------------------------------------------------------------------
# Dual-mode: DR-compression is PERCEPTUAL-only (faithful drops it)
# ---------------------------------------------------------------------------


def test_dr_compression_applies_under_perceptual_only():
    """Highlights/Shadows/Whites drive a real change under PERCEPTUAL but are
    DROPPED under FAITHFUL — the §5-amendment contract. So faithful output with
    them set is byte-identical to faithful with them zeroed, while perceptual
    diverges."""
    x = _dr_textured_image(32, 32).astype(np.float32)
    ops_set = DevelopOps(highlights=50.0, shadows=40.0, whites=30.0)
    ops_zero = DevelopOps()

    faithful_set = apply_develop_ops(x, ops_set, RenderIntent.FAITHFUL)
    faithful_zero = apply_develop_ops(x, ops_zero, RenderIntent.FAITHFUL)
    np.testing.assert_array_equal(faithful_set, faithful_zero)  # dropped on faithful

    perceptual_set = apply_develop_ops(x, ops_set, RenderIntent.PERCEPTUAL)
    assert np.max(np.abs(perceptual_set - faithful_set)) > 1e-3  # applied on perceptual


def test_dr_compression_perceptual_identity_still_byte_exact():
    """With H/S/W all 0, PERCEPTUAL stays byte-identical to FAITHFUL even though the
    DR op is wired into the perceptual branch (short-circuit holds the ship gate).

    Decorated only with INTENT-INDEPENDENT ops (global Saturation): an HSL band and
    a Color-Grade wheel can no longer be used here because steps 2-3 (CDL, OKLCh
    HSL) make `_apply_color_grade_perceptual` / `_apply_hsl_perceptual` diverge from
    faithful by design — those divergences are covered by
    `test_perceptual_color_grade_diverges_from_faithful` /
    `test_perceptual_hsl_diverges_from_faithful`. This test isolates the DR op's
    H/S/W=0 no-op."""
    x = np.random.rand(8, 8, 3).astype(np.float32)
    ops = DevelopOps(
        saturation=20.0,
    )  # an intent-independent op set, but highlights/shadows/whites are 0 (no HSL/Color-Grade)
    np.testing.assert_array_equal(
        apply_develop_ops(x, ops, RenderIntent.FAITHFUL),
        apply_develop_ops(x, ops, RenderIntent.PERCEPTUAL),
    )


def test_dr_compression_runs_after_color_grade_before_contrast(monkeypatch):
    """Order check: under PERCEPTUAL the DR op runs after the color-grade applicator
    and before Contrast2012 (the §5-amendment slot, inside the perceptual branch)."""
    import lrt_cinema.develop_ops as do

    calls: list[str] = []
    monkeypatch.setattr(do, "_apply_color_grade_perceptual",
                        lambda pp, cg: (calls.append("color_grade"), pp)[1])
    monkeypatch.setattr(do, "apply_dr_compression",
                        lambda pp, hi, sh, wh: (calls.append("dr"), pp)[1])
    monkeypatch.setattr(do, "apply_contrast_2012",
                        lambda pp, c: (calls.append("contrast"), pp)[1])

    x = np.zeros((2, 2, 3), dtype=np.float32)
    ops = DevelopOps(highlights=10.0, color_grade=ColorGrade(shadow_sat=10.0), contrast=5.0)
    do.apply_stage_12_perceptual(x, ops, RenderIntent.PERCEPTUAL)
    assert calls == ["color_grade", "dr", "contrast"]
