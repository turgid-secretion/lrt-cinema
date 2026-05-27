"""LR-authored develop ops applied AFTER the DCP shaping stages.

Stages 11–12 of the pipeline (per `docs/research/v06-architecture.md`
§"Pipeline stage order"). These run on linear ProPhoto(D50) output from
`pipeline.apply_adobe_pipeline`.

Stage 11 (linear domain — apply on raw linear ProPhoto):
  - Exposure2012: scalar EV multiplier (`x · 2^EV`).
  - Blacks2012: linear black-point lift / crush.

Stage 12 (perceptual domain — apply in HSV where appropriate):
  - ToneCurvePV2012: per-channel parametric tone curve in [0, 1].
  - Saturation: HSV S-channel multiplier.
  - Vibrance: non-linear S boost (low-saturation pixels gain more than
    already-saturated ones).
  - Contrast2012: midtone-pivot S-curve in linear domain.
  - Sharpness: 3x3 USM (deferred; v0.6 no-op stub, see docstring).

The math here is NOT a 1:1 port of Adobe Lightroom's PV5 PV2012 internals
— that pipeline is closed-source. These are defensible best-effort
implementations matching dt's mappings and the public LR documentation.
The ΔE-vs-LR-preview floor (2.03 mean per `dng-pipeline-findings`) bounds
how close any open-source implementation can get without LR's PV5 source.

For Holy Grail / Deflicker timelapse use, the Exposure2012 op carries the
LRT-emitted per-frame exposure deltas — that path is exercised every
render. The slider ops below (Sat/Vib/Contrast/Sharp) fire only when the
LRT keyframe carries non-zero values, which is rare on cinema-linear
output (those operations belong in the grade, not the render).
"""

from __future__ import annotations

import numpy as np

from lrt_cinema.ir import DevelopOps, TonePoint
from lrt_cinema.pipeline import DngSplineSolver

# ---------------------------------------------------------------------------
# Stage 11 — linear-domain ops
# ---------------------------------------------------------------------------


def apply_exposure_2012(prophoto: np.ndarray, ev: float) -> np.ndarray:
    """LR Exposure2012: scalar EV multiplier in linear domain.

    `out = in × 2^EV`. EV ∈ [-5, +5] per the LR slider convention; we do
    not clamp — the spec is silent and the seed pipeline likewise doesn't,
    letting overrange data flow through to the next stage.
    """
    if ev == 0.0:
        return prophoto
    return prophoto * np.float32(2.0 ** ev)


def apply_blacks_2012(prophoto: np.ndarray, blacks: float) -> np.ndarray:
    """LR Blacks2012: linear black-point shift.

    Positive blacks (0..+100) lift shadows; negative (-100..0) crush.
    LR's UI normalizes to ±100 around a pivot; we map to a small additive
    bias scaled by 0.0005 per slider unit (5% lift / crush at ±100, matching
    dt's colorbalancergb mapping). Clamped at 0 from below so we never
    invert linear data.
    """
    if blacks == 0.0:
        return prophoto
    bias = np.float32(blacks * 0.0005)
    return np.maximum(prophoto + bias, np.float32(0.0))


def apply_stage_11_linear(prophoto: np.ndarray, ops: DevelopOps) -> np.ndarray:
    """Apply all stage-11 linear-domain ops in order: Exposure then Blacks."""
    out = apply_exposure_2012(prophoto, ops.exposure_ev)
    return apply_blacks_2012(out, ops.blacks)


# ---------------------------------------------------------------------------
# Stage 12 — perceptual-domain ops (HSV where appropriate)
# ---------------------------------------------------------------------------


def apply_tone_curve_pv2012(
    prophoto: np.ndarray, points: list[TonePoint],
) -> np.ndarray:
    """LR ToneCurvePV2012: parametric tone curve applied per-channel via
    Adobe SDK Hermite C2 spline (same solver as DCP ProfileToneCurve).

    Empty / single-point list = no-op. Identity curve (two points at
    (0,0)→(1,1)) also no-ops out cheaply. Clamps to [0, 1] before/after
    the spline since the curve is defined on that domain.
    """
    if len(points) < 2:
        return prophoto
    xs = np.array([p.x for p in points], dtype=np.float64)
    ys = np.array([p.y for p in points], dtype=np.float64)
    if np.allclose(xs, ys, atol=1e-6) and xs[0] == 0.0 and xs[-1] == 1.0:
        return prophoto
    solver = DngSplineSolver(xs, ys)
    clipped = np.clip(prophoto, 0.0, 1.0)
    out = np.empty_like(prophoto)
    for ch in range(3):
        out[..., ch] = np.clip(
            solver.evaluate(clipped[..., ch]), 0.0, 1.0,
        ).astype(prophoto.dtype)
    return out


def apply_saturation(prophoto: np.ndarray, sat: float) -> np.ndarray:
    """LR Saturation: -100..+100 slider, HSV S-channel multiplier.

    Mapping: `mult = 1 + sat/100`. So +100 doubles saturation, -100
    desaturates fully. Implemented in HSV; gamut-clamped on recompose.
    """
    if sat == 0.0:
        return prophoto
    mult = 1.0 + sat / 100.0
    return _scale_hsv_saturation(prophoto, lambda s: s * mult)


def apply_vibrance(prophoto: np.ndarray, vib: float) -> np.ndarray:
    """LR Vibrance: -100..+100 slider, non-linear S boost. Already-saturated
    pixels gain less than near-grey ones. Mapping: `out_s = s + (vib/100) *
    s * (1 - s)`. Peak boost at s=0.5; no effect at s=0 or s=1."""
    if vib == 0.0:
        return prophoto
    k = vib / 100.0
    return _scale_hsv_saturation(prophoto, lambda s: np.clip(s + k * s * (1.0 - s), 0.0, 1.0))


def apply_contrast_2012(prophoto: np.ndarray, contrast: float) -> np.ndarray:
    """LR Contrast2012: -100..+100 S-curve around midtone pivot (0.18 linear,
    matching scene-linear midgray). Mapping: pivot-anchored gain
    `out = pivot + (in - pivot) * gain` where `gain = 1 + contrast/100`.

    Linear-domain operation — applied here rather than in HSV because
    contrast is a per-channel scalar and HSV-domain contrast would tint
    saturated pixels.
    """
    if contrast == 0.0:
        return prophoto
    pivot = np.float32(0.18)
    gain = np.float32(1.0 + contrast / 100.0)
    return np.maximum(pivot + (prophoto - pivot) * gain, np.float32(0.0))


def apply_sharpness(prophoto: np.ndarray, sharpness: float) -> np.ndarray:
    """LR Sharpness: 0..150 slider, USM-style. NO-OP in v0.6.

    Sharpening for cinema-linear output is conventionally applied at the
    grade stage, not the render stage — cinema timelines downstream
    (Resolve, AE) carry their own sharpening primitives that compose with
    the timelapse motion. Carrying LR's USM amount through to a 16-bit
    linear TIFF / 32-bit float EXR would bake a non-reversible operation
    into the deliverable. Returning the input unmodified preserves the
    grade-stage decision.

    v0.6.x may wire this in if user feedback says otherwise.
    """
    return prophoto


# ---------------------------------------------------------------------------
# Top-level dispatchers
# ---------------------------------------------------------------------------


def apply_stage_12_perceptual(
    prophoto: np.ndarray, ops: DevelopOps,
) -> np.ndarray:
    """Apply all stage-12 perceptual-domain ops in order:
    ToneCurve → Saturation → Vibrance → Contrast → Sharpness."""
    out = apply_tone_curve_pv2012(prophoto, ops.tone_curve)
    out = apply_saturation(out, ops.saturation)
    out = apply_vibrance(out, ops.vibrance)
    out = apply_contrast_2012(out, ops.contrast)
    out = apply_sharpness(out, ops.sharpness)
    return out


def apply_develop_ops(
    prophoto: np.ndarray, ops: DevelopOps,
) -> np.ndarray:
    """Entry point: apply all develop ops (stages 11 + 12) to linear
    ProPhoto. Returns linear ProPhoto post-LR-ops, ready for stage 13
    (color-space conversion + output encoding in `output.py`).
    """
    out = apply_stage_11_linear(prophoto, ops)
    return apply_stage_12_perceptual(out, ops)


# ---------------------------------------------------------------------------
# Internal — HSV helpers
# ---------------------------------------------------------------------------


def _scale_hsv_saturation(prophoto: np.ndarray, s_map) -> np.ndarray:
    """Convert ProPhoto → HSV (Adobe hex sector model from `lut3d_baker`),
    apply `s_map` to the S channel, recompose. Pixels outside HSV's valid
    region are passed through unchanged (matches DCP HSM behavior)."""
    from lrt_cinema.lut3d_baker import (
        _hsv_to_rgb_dcp,
        _rgb_to_hsv_dcp,
    )

    h_arr, s_arr, v_arr, valid = _rgb_to_hsv_dcp(prophoto)
    s_out = s_map(s_arr)
    rgb_post = _hsv_to_rgb_dcp(h_arr, s_out, v_arr)
    return np.where(valid[..., None], rgb_post, prophoto).astype(prophoto.dtype)
