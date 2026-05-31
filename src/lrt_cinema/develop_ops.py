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

from lrt_cinema.ir import ColorGrade, DevelopOps, HslBands, RenderIntent, TonePoint
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
    desaturates fully. Implemented in HSV; S is clamped to [0, 1] before
    recompose.
    """
    if sat == 0.0:
        return prophoto
    mult = 1.0 + sat / 100.0
    # Clamp S to [0, 1] BEFORE recompose. S>1 drives _hsv_to_rgb_dcp to emit
    # negative linear-ProPhoto channels; output.py's ProPhoto→target matrix then
    # MIXES those negatives into the other channels before the [0, 1] clip, so
    # the clip does NOT neutralise it and saturated colour renders wrong (a grey
    # wedge is blind to this — see CLAUDE.md §0). Mirrors apply_vibrance, which
    # already clamps. Guarded by test_color_oracle.py with a pixel past S=1.
    return _scale_hsv_saturation(prophoto, lambda s: np.clip(s * mult, 0.0, 1.0))


def apply_vibrance(prophoto: np.ndarray, vib: float) -> np.ndarray:
    """LR Vibrance: -100..+100 slider, non-linear S boost. Already-saturated
    pixels gain less than near-grey ones. Mapping: `out_s = s + (vib/100) *
    s * (1 - s)`. Peak boost at s=0.5; no effect at s=0 or s=1."""
    if vib == 0.0:
        return prophoto
    k = vib / 100.0
    return _scale_hsv_saturation(prophoto, lambda s: np.clip(s + k * s * (1.0 - s), 0.0, 1.0))


def apply_hsl(prophoto: np.ndarray, hsl: HslBands) -> np.ndarray:
    """LR HSL / Color panel: 8 hue bands × {Hue, Saturation, Luminance}.

    Applied in the Adobe hexcone HSV domain (`lut3d_baker` helpers, the same
    space as Saturation/Vibrance and the DCP HueSatMap). For each pixel a
    smooth, overlapping triangular **partition of unity** over the eight band
    centres (`_HSL_BAND_CENTERS_HEX`) blends the per-band adjustments:

      * Hue:        ``h_out = h + Σ wᵢ · (hueᵢ/100)·HUE_MAX``   (rotation)
      * Saturation: ``s_out = clip(s · Σ wᵢ·(1 + satᵢ/100), 0, 1)``
      * Luminance:  ``v_out = v · (1 + s_gate·(Σ wᵢ·(1 + lumᵢ/100) − 1))``

    Because the weights sum to 1, all-equal bands collapse to a global
    adjustment and the all-zero default is the identity. **Saturation gates the
    luminance term** (`s_gate`) so a neutral pixel — whose hue is undefined and
    defaults to the Red band — is left untouched even when a band's Luminance
    slider is set (a grey wedge must stay grey; see CLAUDE.md §0). S is clamped
    to [0,1] before recompose for the same reason `apply_saturation` clamps:
    an S>1 would drive `_hsv_to_rgb_dcp` to emit negative ProPhoto channels
    that `output.py`'s colour matrix then mixes in before the [0,1] clip.

    **Fidelity caveat:** Adobe's exact band centres, the Hue-slider→rotation
    magnitude (`_HSL_HUE_MAX_HEX`), and the HSL-Luminance vs HSV-Value mapping
    are closed-source. These are the best public approximation — the band
    layout matches the named-colour hue wheel and the ±100→±30° hue rotation
    is the conventional reverse-engineered value. The Axis-1 oracle validates
    this *defined* math, not absolute Lightroom fidelity (see VALIDATION.md).
    """
    if hsl.is_identity():
        return prophoto

    from lrt_cinema.lut3d_baker import _hsv_to_rgb_dcp, _rgb_to_hsv_dcp

    h, s, v, valid = _rgb_to_hsv_dcp(prophoto)
    weights = _hsl_band_weights(h)  # (..., 8) partition of unity

    hue_shift_per_band = np.asarray(hsl.hue, dtype=np.float64) / 100.0 * _HSL_HUE_MAX_HEX
    sat_factor_per_band = 1.0 + np.asarray(hsl.saturation, dtype=np.float64) / 100.0
    lum_factor_per_band = 1.0 + np.asarray(hsl.luminance, dtype=np.float64) / 100.0

    hue_shift = weights @ hue_shift_per_band
    sat_mult = weights @ sat_factor_per_band
    lum_mult = weights @ lum_factor_per_band

    h_out = h + hue_shift
    h_out = np.where(h_out < 0.0, h_out + 6.0, h_out)
    h_out = np.where(h_out >= 6.0, h_out - 6.0, h_out)

    s_out = np.clip(s * sat_mult, 0.0, 1.0)

    # Gate luminance by saturation so near-neutral pixels (ill-defined hue) are
    # not pushed by a colour band's Luminance slider. Above _HSL_LUM_SAT_GATE
    # the band gets full luminance authority.
    s_gate = np.clip(s / _HSL_LUM_SAT_GATE, 0.0, 1.0)
    eff_lum_mult = 1.0 + s_gate * (lum_mult - 1.0)
    v_out = np.maximum(v * eff_lum_mult, 0.0)

    rgb_post = _hsv_to_rgb_dcp(h_out, s_out, v_out)
    return np.where(valid[..., None], rgb_post, prophoto).astype(prophoto.dtype)


def apply_color_grade(prophoto: np.ndarray, cg: ColorGrade) -> np.ndarray:
    """LR Color Grading wheels: Shadows / Midtones / Highlights / Global.

    A tonal-zone-weighted colour overlay applied additively in linear ProPhoto.
    Each wheel contributes a tint = a **zero-sum chroma direction** (the
    fully-saturated RGB for its Hue, mean-subtracted so Hue and Luminance stay
    orthogonal) scaled by Saturation, plus a uniform Luminance offset. The
    Shadow/Midtone/Highlight tints are masked by a luminance-driven
    **partition-of-unity** weighting (`_color_grade_zone_weights`); the Global
    tint applies everywhere. `ColorGradeBlending` sets the zone overlap and
    `ColorGradeBalance` shifts the shadow↔highlight pivot.

    The zone mask is taken on a perceptual luminance proxy — the sRGB OETF of
    the (clamped) ProPhoto relative luminance — so "midtones" land near
    perceptual mid rather than linear mid. The tint itself is added in linear
    light; the output is clamped to ≥0 so no negative ProPhoto channel reaches
    `output.py`'s colour matrix (the `apply_saturation` lesson, generalised).

    **Fidelity caveat:** Lightroom's exact tint strengths, the zone-mask shape
    and its working domain, and the Blending/Balance response are closed-source.
    This is the best public approximation — a luminance-masked split-tone, the
    well-understood model the Color Grade panel succeeds. The Axis-1 oracle
    validates this *defined* math, not absolute Lightroom fidelity.
    """
    if cg.is_identity():
        return prophoto

    from lrt_cinema.lut3d_baker import _srgb_oetf

    tint_shadow = _color_grade_wheel_tint(cg.shadow_hue, cg.shadow_sat, cg.shadow_lum)
    tint_midtone = _color_grade_wheel_tint(cg.midtone_hue, cg.midtone_sat, cg.midtone_lum)
    tint_highlight = _color_grade_wheel_tint(
        cg.highlight_hue, cg.highlight_sat, cg.highlight_lum,
    )
    tint_global = _color_grade_wheel_tint(cg.global_hue, cg.global_sat, cg.global_lum)

    pp = prophoto.astype(np.float64)
    luminance = pp @ _PROPHOTO_LUMINANCE
    perceptual = _srgb_oetf(np.clip(luminance, 0.0, 1.0))
    shadow_w, midtone_w, highlight_w = _color_grade_zone_weights(
        perceptual, cg.blending, cg.balance,
    )

    out = (
        pp
        + shadow_w[..., None] * tint_shadow
        + midtone_w[..., None] * tint_midtone
        + highlight_w[..., None] * tint_highlight
        + tint_global
    )
    return np.maximum(out, 0.0).astype(prophoto.dtype)


def _apply_hsl_perceptual(prophoto: np.ndarray, hsl: HslBands) -> np.ndarray:
    """PERCEPTUAL HSL — OKLCh (DECISIONS.md §7 step 3). **Not yet implemented**:
    aliases the faithful Adobe-hexcone `apply_hsl` so the dual-mode switch is
    byte-identical until the OKLCh applicator (+ ACES RGC gamut pass) lands."""
    return apply_hsl(prophoto, hsl)


def _apply_color_grade_perceptual(prophoto: np.ndarray, cg: ColorGrade) -> np.ndarray:
    """PERCEPTUAL Color Grade — ASC CDL (SOP+sat) (DECISIONS.md §7 step 2).
    **Not yet implemented**: aliases the faithful split-tone `apply_color_grade`
    until the CDL applicator lands."""
    return apply_color_grade(prophoto, cg)


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
    intent: RenderIntent = RenderIntent.FAITHFUL,
) -> np.ndarray:
    """Apply all stage-12 perceptual-domain ops in order:
    ToneCurve → Saturation → Vibrance → HSL → ColorGrade → Contrast → Sharpness.

    HSL then Color Grading are placed after the global Saturation/Vibrance and
    before Contrast, matching Lightroom's panel order (HSL precedes Color
    Grading; both are colour-treatment ops after Basic presence and before the
    final tone shaping). Identity ops short-circuit to a byte-exact no-op, so a
    render with no HSL / Color-Grade intent is bit-identical to the prior
    pipeline (the ΔE ship gate is unaffected).

    `intent` selects the HSL + Color-Grade applicator (DECISIONS.md §7):
    **FAITHFUL** (default) uses the Adobe-hexcone ops — the sRGB TIFF / LRT
    round-trip path; **PERCEPTUAL** uses the modern primitives for the ACEScg
    master. The perceptual applicators currently alias the faithful ones (steps
    2-3 fill them), so the two intents are byte-identical today — the seam is in
    place without changing any output. Only the HSL/ColorGrade applicators
    branch; ToneCurve/Sat/Vib/Contrast/Sharpness are intent-independent."""
    out = apply_tone_curve_pv2012(prophoto, ops.tone_curve)
    out = apply_saturation(out, ops.saturation)
    out = apply_vibrance(out, ops.vibrance)
    if intent is RenderIntent.PERCEPTUAL:
        out = _apply_hsl_perceptual(out, ops.hsl)
        out = _apply_color_grade_perceptual(out, ops.color_grade)
    else:
        out = apply_hsl(out, ops.hsl)
        out = apply_color_grade(out, ops.color_grade)
    out = apply_contrast_2012(out, ops.contrast)
    out = apply_sharpness(out, ops.sharpness)
    return out


def apply_develop_ops(
    prophoto: np.ndarray, ops: DevelopOps,
    intent: RenderIntent = RenderIntent.FAITHFUL,
) -> np.ndarray:
    """Entry point: apply all develop ops (stages 11 + 12) to linear
    ProPhoto. Returns linear ProPhoto post-LR-ops, ready for stage 13
    (color-space conversion + output encoding in `output.py`).

    `intent` (DECISIONS.md §7) picks the Stage-12 grading applicator — FAITHFUL
    (default, Adobe-hexcone, sRGB TIFF) or PERCEPTUAL (modern primitives, ACEScg
    master). Stage 11 is intent-independent.
    """
    out = apply_stage_11_linear(prophoto, ops)
    return apply_stage_12_perceptual(out, ops, intent)


# ---------------------------------------------------------------------------
# Internal — HSL band-weighting (hexcone hue space, [0, 6))
# ---------------------------------------------------------------------------
#
# Adobe's eight HSL hue bands, expressed as centres in the hexcone hue space
# (`[0, 6)`, sixths-of-a-turn — the same space `lut3d_baker` uses, NOT degrees).
# Degrees → hexcone is ×6/360. The named-colour layout (Red 0°, Orange 30°,
# Yellow 60°, Green 120°, Aqua 180°, Blue 240°, Purple 270°, Magenta 300°)
# matches the perceptual hue wheel; the precise centres Adobe ships are
# closed-source, so these are the conventional reverse-engineered values.
_HSL_BAND_CENTERS_HEX = np.array(
    [0.0, 0.5, 1.0, 2.0, 3.0, 4.0, 4.5, 5.0], dtype=np.float64,
)  # Red, Orange, Yellow, Green, Aqua, Blue, Purple, Magenta

# Hue-slider magnitude: ±100 → ±30° of hue rotation (the conventional value).
_HSL_HUE_MAX_HEX = 30.0 * (6.0 / 360.0)

# Below this HSV saturation the per-band Luminance effect ramps to zero, so
# near-neutral pixels (undefined hue) are protected from colour-band luminance.
_HSL_LUM_SAT_GATE = 0.1


def _hsl_band_weights(h: np.ndarray) -> np.ndarray:
    """Triangular partition-of-unity weights over the eight HSL band centres.

    `h`: hexcone hue array in `[0, 6)`. Returns `(..., 8)` weights that sum to
    exactly 1 per pixel — each hue lies in exactly one segment between two
    adjacent centres and splits its weight linearly between them (the final
    segment wraps Magenta→Red across 5.0→6.0≡0.0). The unit-sum property is
    what makes all-equal bands behave as a single global adjustment and the
    all-zero default an exact identity.
    """
    weights = np.zeros(h.shape + (8,), dtype=np.float64)
    centers = _HSL_BAND_CENTERS_HEX
    for j in range(8):
        lo = centers[j]
        hi = centers[j + 1] if j < 7 else 6.0  # wrap: Magenta → Red
        nxt = (j + 1) % 8
        in_seg = (h >= lo) & (h < hi)
        frac = np.where(in_seg, (h - lo) / (hi - lo), 0.0)
        weights[..., j] += np.where(in_seg, 1.0 - frac, 0.0)
        weights[..., nxt] += frac
    return weights


# ---------------------------------------------------------------------------
# Internal — Color Grading (tonal-zone-weighted colour overlay)
# ---------------------------------------------------------------------------
#
# ProPhoto-RGB(D50) relative-luminance row (ROMM RGB → XYZ, the Y row). Used
# to drive the Color-Grade tonal zone mask. Cross-checked against
# colour-science's ProPhoto matrix by
# test_color_oracle.py::test_prophoto_luminance_constant_matches_colour_science
# so it cannot silently drift from the matrix output.py actually converts with.
_PROPHOTO_LUMINANCE = np.array([0.2880402, 0.7118741, 0.0000857], dtype=np.float64)

# Tint strengths at full slider. Documented approximations — Lightroom's exact
# magnitudes are closed-source; these give a strong-but-bounded grade.
_CG_CHROMA_STRENGTH = 0.30  # per unit Saturation/100 along the (zero-sum) hue dir
_CG_LUM_STRENGTH = 0.10     # per unit Luminance/100, uniform across channels


def _color_grade_wheel_tint(hue_deg: float, sat: float, lum: float) -> np.ndarray:
    """One wheel's additive tint: a zero-sum chroma direction (the saturated RGB
    for `hue_deg`, mean-subtracted so it carries no net luminance) scaled by
    Saturation, plus a uniform Luminance offset. Returns a float64 `(3,)`."""
    from lrt_cinema.lut3d_baker import _hsv_to_rgb_dcp

    h_hex = np.array([(hue_deg % 360.0) * (6.0 / 360.0)], dtype=np.float64)
    rgb = _hsv_to_rgb_dcp(h_hex, np.array([1.0]), np.array([1.0]))[0]  # (3,)
    chroma_dir = rgb - rgb.mean()
    return _CG_CHROMA_STRENGTH * (sat / 100.0) * chroma_dir + _CG_LUM_STRENGTH * (lum / 100.0)


def _color_grade_zone_weights(
    perceptual: np.ndarray, blending: float, balance: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Shadow / Midtone / Highlight masks from a perceptual luminance.

    A partition of unity: ``shadow=(1−t)ᵖ``, ``highlight=tᵖ``,
    ``midtone=1−shadow−highlight`` (≥0 for p≥1). `blending` sets the exponent
    `p∈[1,3]` (higher Blending → p→1 → shadows/highlights crossfade with more
    overlap); `balance` remaps `t` via a power curve to shift the pivot
    (positive Balance → highlights claim more territory). Sums to 1 per pixel,
    so an all-zero grade leaves the additive overlay at zero everywhere."""
    gamma_balance = 2.0 ** (-balance / 100.0)
    t = np.clip(perceptual, 0.0, 1.0) ** gamma_balance
    p = 1.0 + 2.0 * (1.0 - float(np.clip(blending, 0.0, 100.0)) / 100.0)
    shadow_w = (1.0 - t) ** p
    highlight_w = t ** p
    midtone_w = 1.0 - shadow_w - highlight_w
    return shadow_w, midtone_w, highlight_w


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
