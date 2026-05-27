"""Per-op math tests for `lrt_cinema.develop_ops`.

The LR-authored develop ops fire AFTER the DCP shaping stages, on linear
ProPhoto. These tests use synthetic inputs (gradients, gray patches,
saturated colors) with known closed-form outputs — no Adobe binary
required.
"""

from __future__ import annotations

import numpy as np

from lrt_cinema.develop_ops import (
    apply_blacks_2012,
    apply_contrast_2012,
    apply_develop_ops,
    apply_exposure_2012,
    apply_saturation,
    apply_sharpness,
    apply_stage_11_linear,
    apply_stage_12_perceptual,
    apply_tone_curve_pv2012,
    apply_vibrance,
)
from lrt_cinema.ir import DevelopOps, TonePoint

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
