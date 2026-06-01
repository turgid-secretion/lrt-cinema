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

import math

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
    """PERCEPTUAL Color Grade — ASC-CDL idiom, **offset-only**, in ACEScct log
    (DECISIONS.md §7 step 2). The faithful split-tone `apply_color_grade`
    (additive-in-linear-ProPhoto) is unchanged; this is the ACEScg-master path.

    **Working space (contract 1 — ProPhoto(D50)-in / ProPhoto(D50)-out).** Stage
    12 operates on linear ProPhoto(D50); `output.py` does the ProPhoto→ACEScg
    Bradford at Stage 13. So this op converts ProPhoto→ACEScg **internally**
    (`colour.RGB_to_RGB`, the SAME params as `output._prophoto_to_linear`:
    Bradford, `apply_cctf_*=False`), grades in ACEScct log, then inverts back to
    ProPhoto before return. It must NOT claim "ACEScg in/out" — that would have
    `output.py` re-convert the result *as if it were ProPhoto*, double-transforming
    the primaries and corrupting saturated colour (the CLAUDE.md §0 trap). The
    redundant ProPhoto↔ACEScg round-trip vs the eventual EXR encode is an
    idempotent Bradford (reversible to float tolerance) — a future optimization,
    not a seam to relax.

    **The grade — offset-only ASC-CDL (slope = power = 1).** Per channel in
    ACEScct log: ``out_log[c] = log_in[c] + offset_lum + offset_chroma[c]``.
      * **Luminance** is a log *lift* — a uniform per-channel offset (NOT a
        multiplicative gain; ColorGrade has no control mapping to slope). Each
        wheel's Luminance/100 scaled by `_CG_LUM_LOG_STRENGTH` (one stop per
        unit-of-100): the S/M/H wheels zone-weighted, Global everywhere::

            offset_lum = (Σ_zone w_zone·lum_zone/100 + global_lum/100)·K_lum_log

      * **Hue+Saturation** per wheel → the **zero-sum chroma direction** (the
        SAME `_hsv_to_rgb_dcp` hue→RGB construction as faithful
        `_color_grade_wheel_tint`, mean-subtracted) applied as a per-channel
        additive **log** delta scaled by sat/100·`_CG_CHROMA_LOG_STRENGTH`,
        zone-weighted as today. Zero-sum → chroma-only (no net lift).

    Zone weights come from `_color_grade_zone_weights` (so Blending/Balance
    behave identically to faithful) on a **log-domain** luminance proxy
    (`_cg_zone_proxy_log`) — "midtones" at perceptual mid, matching Resolve's Log
    wheels. The unified "10th ASC-CDL saturation number" is intentionally DROPPED:
    ColorGrade carries four per-wheel Saturations and no global one, so the
    per-wheel chroma offsets are the whole saturation story (no IR source for a
    single post-SOP scalar).

    **ACEScct toe via the library.** `colour.models.log_encoding_ACEScct` /
    `log_decoding_ACEScct` (mid-grey 0.18 → 0.413588). The toe is NOT hand-rolled
    — the sub-breakpoint branch is the *linear* segment `A·in+B` (A,B > 0,
    `X_BRK=2^-7`), finite and invertible even for the small NEGATIVE ACEScg
    channels that an in-ProPhoto-but-out-of-AP1 colour produces. So we do NOT
    floor ACEScg before the encode (that would hard-clip those colours to the AP1
    boundary inside this op, pre-empting the gamut pass).

    **Gamut / clamp (§0).** No top clamp — scene-referred, overrange survives.
    Out-of-AP1 excursions are the job of the single gated ACES RGC pass in
    `output.py` (`_aces_rgc_compress_ap1`), NOT this op. We floor the final
    ProPhoto at 0 only (the faithful / DR-compression convention — no negative
    ProPhoto channel reaches `output.py`'s colour matrix).

    **Byte-exact identity.** `cg.is_identity()` (all wheel Sat+Lum zero) →
    ``return prophoto`` (the literal input) before any conversion. The ACEScct +
    Bradford round-trip is reversible only to float tolerance, so the
    short-circuit is mandatory — it keeps both intents bit-identical on a no-grade
    render and the gym 0.026 / rose 0.545 ΔE ship gate untouched (perceptual-only).

    **Constants are best-effort TUNING, not LR fidelity** (`_CG_*_LOG_STRENGTH`,
    `_CG_ZONE_PROXY_*`). The Axis-1 oracle validates the *defined* offset-SOP math
    + the Bradford + ACEScct, not appearance — matching the honesty discipline of
    `apply_color_grade` / `apply_dr_compression`.
    """
    if cg.is_identity():
        return prophoto  # byte-exact identity — short-circuit before any conversion

    import colour

    # ProPhoto(D50) → ACEScg(AP1): same Bradford as output._prophoto_to_linear.
    shape = prophoto.shape
    flat = prophoto.reshape(-1, 3).astype(np.float64)
    acescg = colour.RGB_to_RGB(
        flat,
        input_colourspace="ProPhoto RGB",
        output_colourspace="ACEScg",
        chromatic_adaptation_transform="Bradford",
        apply_cctf_decoding=False,
        apply_cctf_encoding=False,
    )

    # ACEScg → ACEScct log (library toe; NOT floored — the linear toe is
    # invertible for the small negatives an out-of-AP1 colour produces).
    log_in = colour.models.log_encoding_ACEScct(acescg)

    # Zone weights on a log-domain luminance proxy (AP1 luminance is the ACEScg
    # green-dominant Y; reuse the ProPhoto luminance row on the ProPhoto pixels —
    # identical zone mask as faithful, just log-placed).
    luminance = flat @ _PROPHOTO_LUMINANCE
    proxy = _cg_zone_proxy_log(luminance)
    shadow_w, midtone_w, highlight_w = _color_grade_zone_weights(
        proxy, cg.blending, cg.balance,
    )

    # Luminance lift — uniform per-channel log offset (a tonal lift, not a gain).
    lum_zone = (
        shadow_w * (cg.shadow_lum / 100.0)
        + midtone_w * (cg.midtone_lum / 100.0)
        + highlight_w * (cg.highlight_lum / 100.0)
        + (cg.global_lum / 100.0)
    )
    offset_lum = (lum_zone * _CG_LUM_LOG_STRENGTH)[..., None]  # (..., 1) broadcast

    # Chroma — zero-sum hue direction per wheel, additive in log, zone-weighted.
    k = _CG_CHROMA_LOG_STRENGTH
    chroma_sh = k * (cg.shadow_sat / 100.0) * _color_grade_chroma_dir(cg.shadow_hue)
    chroma_mid = k * (cg.midtone_sat / 100.0) * _color_grade_chroma_dir(cg.midtone_hue)
    chroma_hi = k * (cg.highlight_sat / 100.0) * _color_grade_chroma_dir(cg.highlight_hue)
    chroma_gl = k * (cg.global_sat / 100.0) * _color_grade_chroma_dir(cg.global_hue)
    offset_chroma = (
        shadow_w[..., None] * chroma_sh
        + midtone_w[..., None] * chroma_mid
        + highlight_w[..., None] * chroma_hi
        + chroma_gl
    )

    log_out = log_in + offset_lum + offset_chroma

    # ACEScct → ACEScg → ProPhoto(D50) (inverse Bradford). No top clamp; floor 0.
    acescg_out = colour.models.log_decoding_ACEScct(log_out)
    pp_out = colour.RGB_to_RGB(
        acescg_out,
        input_colourspace="ACEScg",
        output_colourspace="ProPhoto RGB",
        chromatic_adaptation_transform="Bradford",
        apply_cctf_decoding=False,
        apply_cctf_encoding=False,
    )
    out = np.maximum(pp_out.reshape(shape), 0.0)
    return out.astype(prophoto.dtype)


def apply_dr_compression(
    prophoto: np.ndarray, highlights: float, shadows: float, whites: float,
) -> np.ndarray:
    """PERCEPTUAL scene-referred dynamic-range compression (DECISIONS.md §5
    amendment; the resolved law in `research/v10b-scene-referred-compression-law.md`).

    Makes the LR `Highlights`/`Shadows`/`Whites` knobs — dropped + warn-only on
    the faithful path (closed-source PV5 math) — *do something measurable* on the
    perceptual ACEScg master: surgically compress a large dynamic range while
    retaining local/perceived contrast. **Driven entirely by those three existing
    XMP sliders — no new control.**

    **The law (homomorphic / Stockham log-domain compression toward a fixed
    scene-linear anchor — the log sibling of `apply_contrast_2012`).** Working on a
    single luminance channel `L = rgb @ _PROPHOTO_LUMINANCE`, with
    `u = log2(L+eps) − log2(0.18)` the log-distance from the 0.18 anchor::

        L_out = max(0, 0.18 · 2**g(u) − eps)        # eps = 1e-6; floor 0, NO ceiling

    `g(u)` is a **3-slope** piecewise-linear remap (the three sliders force the
    asymmetry — a single symmetric slope cannot drive three knobs):
      * below the anchor (`u < 0`): slope ``c_lo``  ← **Shadows**;
      * anchor → high breakpoint:   slope ``c_hi``  ← **Highlights** (upper-mid);
      * above the breakpoint:        slope ``c_top`` ← **Whites** (extreme top).
    Each slope is ``2**(−k·s/100)`` (`s=0 → 1 → identity arm`). ``c_top`` is a
    third log-log **slope**, never a ceiling/shoulder — so **overrange survives at
    every Whites setting** (a clipping shoulder would destroy the defining
    scene-referred constraint). The two joins (anchor, breakpoint) are smoothed
    with a C1 ``smoothstep`` window so no asymmetric setting kinks at mid-grey.

    **Sign convention (NOT Lightroom-faithful — this path makes no fidelity
    claim).** Positive slider → ``slope < 1`` → that arm *compresses* toward 0.18.
    So +Shadows lifts darks and +Highlights recovers brights (both align with LR's
    direction), but **+Whites *darkens*/compresses the extreme top — the inverse of
    Lightroom's brighten-whites.** This is the law's uniform `2**(−k·s/100)` mapping
    (`research/v10b…` §2.5), deliberate, and validated only as *defined* math.

    **Applied LOCALLY (this is what retains dynamism).** Luminance is split into a
    smooth **base** + a **detail** layer with a guided self-filter (He–Sun–Tang
    2013) on log-luminance — including the defining ``mean_a``/``mean_b``
    box-average step. Only the **base** is compressed by the law; the detail is
    reinserted at unity gain, so local micro-contrast survives the global crush.
    For a flat region (or a sub-window-size / 1-wide array, where the adaptive box
    radius collapses to 0) the split is a no-op and the op reduces *exactly* to the
    global law — the limiting case the Axis-1 oracle validates.

    **§0 hue/gamut (never per-channel).** The compressed luminance is reapplied by
    the out/in **ratio** ``rgb · L_out/max(L_in, eps)`` — a per-pixel positive
    scalar that preserves hue and chroma ratios exactly (the `apply_hsl`
    luminance pattern, `develop_ops.py`). Output is floored at 0 with **no top
    clamp**: overrange `>1` is preserved (out-of-AP1 excursions are handled by a
    downstream ACES RGC pass in `output.py` — a *separate* follow-up, never an
    in-op clamp). Validate on **saturated + overrange** pixels; a grey wedge is
    blind to both the per-channel-vs-ratio error and the overrange behaviour.

    **Byte-exact identity.** All three sliders 0 → ``return prophoto`` (the literal
    input). ``slope = 1`` is *not* numerically identity (the eps pair does not
    cancel bit-exactly through ``log2``/``exp2``), so the short-circuit is
    mandatory — it keeps the gym 0.026 / rose 0.545 ΔE ship gate untouched
    (perceptual-only; the gate renders the faithful stages 1–9).

    **Constants are best-effort TUNING, not Lightroom fidelity** (see the module
    ``_DR_*`` constants). The Axis-1 oracle validates the *defined* piecewise-log
    math and the ratio reapply — not LR appearance — matching the honesty
    discipline of `apply_hsl` / `apply_color_grade`. The guided filter is the
    lightweight first cut; a halo-free local-Laplacian base producer is the quality
    follow-up (`research/v10-local-tone-mapping-dr-compression.md` §3).
    """
    if highlights == 0.0 and shadows == 0.0 and whites == 0.0:
        return prophoto  # byte-exact identity — short-circuit before any log math

    c_lo, c_hi, c_top = _dr_slopes(highlights, shadows, whites)
    # Luminance is computed in float64 (the matmul against the float64
    # _PROPHOTO_LUMINANCE promotes a float32 frame), so the log/exp law keeps full
    # precision — but we never hold a float64 copy of the whole RGB frame: the
    # ratio reapply runs on the original array (the multiply promotes per-line and
    # is recast back). Avoids a ~2× full-frame allocation per worker.
    lum = prophoto @ _PROPHOTO_LUMINANCE
    log_l = np.log2(np.maximum(lum, 0.0) + _DR_EPS)

    # Adaptive box radius: clamp so the (2r+1) window fits the smaller spatial
    # axis. A 1-wide / sub-window array (the oracle's per-pixel layout, a tiny
    # tile) collapses to r=0, where the guided base == log_l exactly and the op is
    # the global law — one code path, no magic size threshold.
    r = min(_DR_GUIDED_RADIUS, (min(log_l.shape) - 1) // 2) if log_l.ndim == 2 else 0

    base_log = _guided_base_log(log_l, r, _DR_GUIDED_EPS)
    detail_log = log_l - base_log
    base_comp = _DR_LOG_ANCHOR + _dr_remap_log(base_log - _DR_LOG_ANCHOR, c_lo, c_hi, c_top)
    log_l_out = base_comp + detail_log
    lum_out = np.maximum(np.exp2(log_l_out) - _DR_EPS, 0.0)

    ratio = lum_out / np.maximum(lum, _DR_EPS)
    out = np.maximum(prophoto * ratio[..., None], 0.0)  # floor 0; NO top clamp (overrange survives)
    return out.astype(prophoto.dtype)


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
    ToneCurve → Saturation → Vibrance → HSL → ColorGrade → [DR-compression] →
    Contrast → Sharpness.

    HSL then Color Grading are placed after the global Saturation/Vibrance and
    before Contrast, matching Lightroom's panel order (HSL precedes Color
    Grading; both are colour-treatment ops after Basic presence and before the
    final tone shaping). Identity ops short-circuit to a byte-exact no-op, so a
    render with no HSL / Color-Grade intent is bit-identical to the prior
    pipeline (the ΔE ship gate is unaffected).

    **DR-compression** (`apply_dr_compression`, driven by the Highlights/Shadows/
    Whites knobs) runs **only under PERCEPTUAL**, inside that branch after Color
    Grading and before Contrast. On the faithful path those knobs stay dropped +
    warn-only (`cli._warn_dropped_ops`); with all three at 0 the op is a byte-exact
    no-op, so both intents stay bit-identical when no DR intent is authored.

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
        # Scene-referred local DR-compression driven by the Highlights/Shadows/
        # Whites XMP knobs (DECISIONS.md §5 amendment). PERCEPTUAL-only — on the
        # faithful path these stay dropped + warn-only (cli._warn_dropped_ops).
        # Byte-exact identity when all three are 0, so the ΔE ship gate is
        # untouched.
        out = apply_dr_compression(out, ops.highlights, ops.shadows, ops.whites)
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

# ---------------------------------------------------------------------------
# Perceptual ColorGrade (ASC-CDL idiom, offset-only) — tuning constants
# ---------------------------------------------------------------------------
#
# The PERCEPTUAL path's ColorGrade (`_apply_color_grade_perceptual`) emits an
# ASC-CDL-idiom grade — a per-channel **offset** in ACEScct log — onto the
# ACEScg master. CDL is OFFSET-ONLY here (slope = power = 1): ColorGrade carries
# no control that maps to a multiplicative slope (Luminance is a tonal *lift*,
# which is natively an offset in log, not a gain). slope=1 is valid ASC-CDL v1.2
# and round-trips losslessly into a colorist's first Resolve node. The offsets
# are defined NATIVELY in log (a stop is a fixed log step) — NOT as a difference
# of two nonlinear encodes (`encode(linear_tint) − log_in`, which is not the
# log-delta of a tint; see research/v09-dualmode-impl-plan.md Step 2 §4).
#
# Like the faithful `_CG_*` strengths these are best-effort TUNING values, NOT a
# Lightroom-fidelity claim (the perceptual intent targets the ACES master, not
# the LRT round-trip — DECISIONS.md §7). The Axis-1 oracle validates the
# *defined* offset-SOP math, not LR appearance.

# Log lift per unit Luminance/100. One full Luminance wheel (±100) = ±1/17.52 in
# normalized ACEScct = exactly **one stop** (17.52 is the ACEScct log-segment
# denominator, `(log2(L)+9.72)/17.52`, so 1/17.52 of the code value is a factor
# of 2 in scene-linear). Global and per-wheel Luminance share this one scale.
_CG_LUM_LOG_STRENGTH = 1.0 / 17.52  # K_lum_log — a stop per slider unit-of-100

# Log chroma offset per unit Saturation/100 along the (zero-sum) hue direction.
# Matched to the faithful `_CG_CHROMA_STRENGTH` magnitude so the two intents
# carry comparable grade authority; applied as a per-channel additive log delta
# (the direction is mean-subtracted → zero-sum, carries no net lift).
_CG_CHROMA_LOG_STRENGTH = 0.30

# Black / white anchors (scene-linear) for the log-domain zone proxy fed to
# `_color_grade_zone_weights`. The proxy is `0.5 + (log2(L) − log2(0.18)) /
# (2·log2(white/0.18))`, clipped to [0,1]: it places 0.18 mid-grey at proxy 0.5
# and diffuse white at proxy 1.0, so "midtones" land at perceptual mid and
# diffuse highlights reach the Highlight wheel (matches Resolve's Log wheels —
# a documented *placement* choice). NB: feeding the raw ACEScct code value here
# would skew the proxy (ACEScct over [0,1] only spans ≈[0.07, 0.55], so white
# would read as ~half midtone); the explicit normalization fixes that.
_CG_ZONE_PROXY_WHITE = 1.0    # scene-linear luminance mapped to proxy = 1.0
_CG_ZONE_PROXY_ANCHOR = 0.18  # scene-linear mid-grey mapped to proxy = 0.5


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


def _color_grade_chroma_dir(hue_deg: float) -> np.ndarray:
    """Zero-sum chroma direction for `hue_deg`: the fully-saturated RGB for that
    hue (the SAME `_hsv_to_rgb_dcp` construction as faithful `_color_grade_wheel_tint`)
    mean-subtracted so it carries no net luminance. Returns float64 `(3,)`."""
    from lrt_cinema.lut3d_baker import _hsv_to_rgb_dcp

    h_hex = np.array([(hue_deg % 360.0) * (6.0 / 360.0)], dtype=np.float64)
    rgb = _hsv_to_rgb_dcp(h_hex, np.array([1.0]), np.array([1.0]))[0]  # (3,)
    return rgb - rgb.mean()


def _cg_zone_proxy_log(luminance: np.ndarray) -> np.ndarray:
    """Log-domain [0,1] zone proxy for `_color_grade_zone_weights` on the
    perceptual path: `0.5 + (log2(L) − log2(anchor)) / (2·log2(white/anchor))`,
    clipped to [0,1]. Places 0.18 mid-grey at 0.5 and diffuse white (1.0) at 1.0
    so the Highlight wheel reaches diffuse highlights (Resolve Log-wheel placement;
    see `_CG_ZONE_PROXY_*`). Overrange clips to 1.0 → fully a highlight."""
    half_span = math.log2(_CG_ZONE_PROXY_WHITE / _CG_ZONE_PROXY_ANCHOR)
    log_l = np.log2(np.maximum(luminance, 0.0) + _DR_EPS)
    proxy = 0.5 + (log_l - math.log2(_CG_ZONE_PROXY_ANCHOR)) / (2.0 * half_span)
    return np.clip(proxy, 0.0, 1.0)


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


# ---------------------------------------------------------------------------
# Internal — DR-compression (scene-referred piecewise-log law + guided base/detail)
# ---------------------------------------------------------------------------
#
# Every constant below is a best-effort TUNING value pinned at implementation
# time (research/v10b-scene-referred-compression-law.md §5) — the Axis-1 oracle
# validates the *defined* math, not Lightroom appearance. They are NOT open
# theory and NOT "auto from image".

_DR_EPS = 1e-6           # log(0) floor; survives scene-linear true zeros. Why
                         # slope=1 is not bit-exact identity (→ short-circuit).
_DR_ANCHOR = 0.18        # scene-linear mid-grey fixed point — the SAME pivot
                         # apply_contrast_2012 uses (repo-grounded, not assumed).
_DR_LOG_ANCHOR = math.log2(_DR_ANCHOR)

# Per-slider slope gain: slope = 2**(−k·s/100). k=1 ⇒ s=+100 halves that arm's
# log-log slope (slope 0.5 — compresses ~2 stops of scene range into ~1 around
# the arm), s=−100 doubles it (slope 2.0). Range [0.5, 2.0] keeps the C1-blended
# curve strictly monotone (min g' ≈ 0.19 > 0; proved for these slopes).
_DR_SLOPE_GAIN_K = 1.0

# High breakpoint separating Highlights' c_hi from Whites' c_top, in log2 stops
# above the anchor: 2 stops = 0.18·4 = 0.72 linear (Highlights ≈ upper-mid,
# Whites ≈ near-white and above, incl. overrange speculars).
_DR_BREAK_STOPS = 2.0

# C1 smoothstep blend half-widths (log2 stops) at the two joins. Disjoint windows
# (anchor [−0.5, 0.5]; breakpoint [1.5, 2.5]) so the blends never interfere.
_DR_BLEND_HALFWIDTH_ANCHOR = 0.5
_DR_BLEND_HALFWIDTH_BREAK = 0.5

# Guided-filter base/detail split (He–Sun–Tang 2013), log-luminance domain.
# Radius ~8 px (the large base radius DR-compression wants); eps ~0.01 (log2²
# stops) — conservative, favours edge preservation. The local-Laplacian upgrade
# is the quality follow-up (v10 §3); this guided filter is the first cut.
_DR_GUIDED_RADIUS = 8
_DR_GUIDED_EPS = 0.01


def _smoothstep(x: np.ndarray) -> np.ndarray:
    """Classic cubic smoothstep `3x²−2x³` on `[0,1]` (clamped). `S'(0)=S'(1)=0`,
    which is exactly what makes the slope-blend C1 at each window edge."""
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def _dr_slopes(highlights: float, shadows: float, whites: float) -> tuple[float, float, float]:
    """Map the three sliders to the three log-log arm slopes (`s=0 → slope 1`):
    Shadows → c_lo (below anchor), Highlights → c_hi (anchor→breakpoint),
    Whites → c_top (above breakpoint). `slope = 2**(−k·s/100)`."""
    c_lo = 2.0 ** (-_DR_SLOPE_GAIN_K * shadows / 100.0)
    c_hi = 2.0 ** (-_DR_SLOPE_GAIN_K * highlights / 100.0)
    c_top = 2.0 ** (-_DR_SLOPE_GAIN_K * whites / 100.0)
    return c_lo, c_hi, c_top


def _dr_remap_log(
    log_dist: np.ndarray, c_lo: float, c_hi: float, c_top: float,
) -> np.ndarray:
    """The 3-slope, C1-blended remap of the log-distance-from-anchor `u`.

    Returns `g(u)`: piecewise-linear with slopes `c_lo` (`u<0`), `c_hi`
    (`0<u<break`), `c_top` (`u>break`), smoothed across both joins. The anchor
    join sits at `u=0` (all arms pass through `g(0)=0` — the anchor is a fixed
    point), the breakpoint at `u=break`. Both blend windows are disjoint, so each
    is a smoothstep blend of the two lines that cross at its centre → C1
    everywhere, and strictly monotone for slopes in `[0.5, 2.0]`."""
    u = np.asarray(log_dist, dtype=np.float64)
    ub = _DR_BREAK_STOPS
    ha = _DR_BLEND_HALFWIDTH_ANCHOR
    hb = _DR_BLEND_HALFWIDTH_BREAK
    w_anchor = _smoothstep((u + ha) / (2.0 * ha))           # 0 below −ha, 1 above +ha
    w_break = _smoothstep((u - (ub - hb)) / (2.0 * hb))     # 0 below break−hb, 1 above +hb
    g_lo = c_lo * u
    g_hi = c_hi * u
    g_top = c_hi * ub + c_top * (u - ub)
    g_after_anchor = (1.0 - w_anchor) * g_lo + w_anchor * g_hi
    return (1.0 - w_break) * g_after_anchor + w_break * g_top


def _dr_compress_luminance(
    lum: np.ndarray, c_lo: float, c_hi: float, c_top: float,
) -> np.ndarray:
    """The pointwise (global) law on a luminance array: `0.18·2**g(u) − eps`,
    floored at 0, where `u = log2(L+eps) − log2(0.18)`. This is the limit the
    local op reduces to when base == luminance (flat region / r=0)."""
    lum = np.asarray(lum, dtype=np.float64)
    u = np.log2(np.maximum(lum, 0.0) + _DR_EPS) - _DR_LOG_ANCHOR
    g = _dr_remap_log(u, c_lo, c_hi, c_top)
    return np.maximum(_DR_ANCHOR * np.exp2(g) - _DR_EPS, 0.0)


def _box_sum(img: np.ndarray, r: int) -> np.ndarray:
    """Sum over a `(2r+1)×(2r+1)` window with shrinking edge windows — He et al.'s
    `boxfilter.m`, vectorised via separable cumulative sums (O(N), radius-free).
    `img` is 2-D; requires `min(shape) ≥ 2r+1`. `r=0` returns `img` unchanged."""
    if r <= 0:
        return img.astype(np.float64, copy=True)
    a = img.astype(np.float64)
    h, w = a.shape
    if 2 * r + 1 > min(h, w):
        raise ValueError(f"box radius {r} too large for image shape {a.shape}")
    cum = np.cumsum(a, axis=0)
    out = np.empty_like(cum)
    out[0:r + 1, :] = cum[r:2 * r + 1, :]
    out[r + 1:h - r, :] = cum[2 * r + 1:h, :] - cum[0:h - 2 * r - 1, :]
    out[h - r:h, :] = cum[h - 1:h, :] - cum[h - 2 * r - 1:h - r - 1, :]
    cum = np.cumsum(out, axis=1)
    res = np.empty_like(cum)
    res[:, 0:r + 1] = cum[:, r:2 * r + 1]
    res[:, r + 1:w - r] = cum[:, 2 * r + 1:w] - cum[:, 0:w - 2 * r - 1]
    res[:, w - r:w] = cum[:, w - 1:w] - cum[:, w - 2 * r - 1:w - r - 1]
    return res


def _guided_base_log(log_l: np.ndarray, r: int, eps_gf: float) -> np.ndarray:
    """Edge-preserving smooth **base** of `log_l` via the guided self-filter
    (He–Sun–Tang 2013, guide = signal = log_l). Includes the defining
    `mean_a`/`mean_b` box-average step. `r=0` returns `log_l` (→ global law)."""
    if r <= 0:
        return log_l.astype(np.float64, copy=True)
    n = _box_sum(np.ones_like(log_l, dtype=np.float64), r)
    mean_i = _box_sum(log_l, r) / n
    mean_ii = _box_sum(log_l * log_l, r) / n
    var_i = np.maximum(mean_ii - mean_i * mean_i, 0.0)
    a = var_i / (var_i + eps_gf)
    b = mean_i - a * mean_i
    mean_a = _box_sum(a, r) / n        # the defining mean_a / mean_b step
    mean_b = _box_sum(b, r) / n
    return mean_a * log_l + mean_b
