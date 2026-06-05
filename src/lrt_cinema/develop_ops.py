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
    # Clamp S to [0, 1] BEFORE recompose. S>1 drives _hsv_to_rgb_dcp to emit
    # negative linear-ProPhoto channels; output.py's ProPhoto→target matrix then
    # MIXES those negatives into the other channels before the [0, 1] clip, so
    # the clip does NOT neutralise it and saturated colour renders wrong (a grey
    # wedge is blind to this — see CLAUDE.md §0). Mirrors apply_vibrance, which
    # already clamps. Guarded by test_color_oracle.py with a pixel past S=1.
    # Backend-dispatched (numpy reference / numba kernel) via accel.
    from lrt_cinema import accel
    return accel.apply_saturation(prophoto, sat)


def apply_vibrance(prophoto: np.ndarray, vib: float) -> np.ndarray:
    """LR Vibrance: -100..+100 slider, non-linear S boost. Already-saturated
    pixels gain less than near-grey ones. Mapping: `out_s = s + (vib/100) *
    s * (1 - s)`. Peak boost at s=0.5; no effect at s=0 or s=1."""
    if vib == 0.0:
        return prophoto
    from lrt_cinema import accel
    return accel.apply_vibrance(prophoto, vib)


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
    from lrt_cinema import accel
    return accel.apply_hsl(prophoto, hsl)


def _hsl_numpy(prophoto: np.ndarray, hsl: HslBands) -> np.ndarray:
    """numpy reference body for `apply_hsl` (post-identity). The backend-agnostic
    maths; `accel.apply_hsl` calls this on the numpy branch and the `hsl_bands`
    kernel on numba. See `apply_hsl` for the algorithm + fidelity caveat."""
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
    from lrt_cinema import accel
    return accel.apply_color_grade(prophoto, cg)


def _color_grade_numpy(prophoto: np.ndarray, cg: ColorGrade) -> np.ndarray:
    """numpy reference body for `apply_color_grade` (post-identity); `accel.
    apply_color_grade` calls this on the numpy branch and the `color_grade`
    kernel on numba. See `apply_color_grade` for the algorithm + caveat."""
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
    """PERCEPTUAL HSL — hue-stable 8-band HSL in **OKLCh** (DECISIONS.md §7 step 3).
    The faithful Adobe-hexcone `apply_hsl` (HSV) is unchanged; this is the
    ACEScg-master path, where OKLCh's perceptual uniformity buys hue constancy
    under a Luminance sweep (no Abney / Bezold–Brücke drift — the measurable §7
    win) that the hexcone HSV cannot give.

    **Working space (contract 1 — ProPhoto(D50)-in / ProPhoto(D50)-out).** Stage 12
    operates on linear ProPhoto(D50); `output.py` does the ProPhoto→ACEScg Bradford
    at Stage 13. So this op converts to OKLCh **internally** and inverts back to
    ProPhoto before return:

        ProPhoto(D50) lin → XYZ(D50) → XYZ(D65) [Bradford] → OKLab → OKLCh
            → adjust → OKLab → XYZ(D65) → XYZ(D50) [Bradford] → ProPhoto(D50) lin

    Ottosson's Oklab is defined on **D65** XYZ, so the D50→D65 Bradford is
    mandatory (`_M_BRADFORD_*` module constants, cross-checked vs colour-science);
    skipping it is the wrong-whitepoint bug the oracle's inverted-Bradford
    sensitivity leg catches. Production uses `colour.XYZ_to_Oklab`/`Oklab_to_Oklch`
    (+ inverses); the Axis-1 oracle hand-rolls Ottosson's M1/M2 + cube-root
    (contract 4).

    **The grade — 8 hue bands × {Hue, Saturation, Luminance}, triangular
    partition-of-unity over the OKLCh hue wheel** (`_oklch_band_weights`, the same
    structure as the faithful `_hsl_band_weights` but in hue *degrees* over
    `_OKLCH_BAND_CENTERS_DEG`). Because the weights sum to 1, all-equal bands
    collapse to a global adjustment and the all-zero default is the identity.
    Per band, with `weights` the per-pixel `(...,8)` unit-sum vector::

        h_out = (h + weights @ (hue/100 · _OKLCH_HUE_MAX_DEG)) mod 360
        c_out = max(c · (weights @ (1 + sat/100)), 0)
        l_out = max(l · (1 + c_gate·(weights @ (1 + lum/100) − 1)), 0)

    **Chroma-gated Luminance** (`c_gate = clip(c / _OKLCH_LUM_CHROMA_GATE, 0, 1)`)
    protects neutrals: a near-grey pixel — whose OKLCh hue is ill-defined and
    falls into whatever band the float noise picks — is left untouched even when a
    colour band's Luminance slider is set (a grey wedge must stay grey; CLAUDE.md
    §0). This is the OKLCh analogue of the faithful path's `s_gate`
    (`_HSL_LUM_SAT_GATE` on HSV saturation).

    **No top clamp (verifier BLOCKER 1).** The PERCEPTUAL path feeds the
    scene-referred ACEScg master, which MUST carry values >1 (PIPELINE.md Stage 7
    "overrange preserved"; `apply_exposure_2012` runs before this with no clamp).
    Faithful `apply_hsl` floors at 0 but never clamps the top — match that: floor
    L and C at 0, **no display ceiling**; floor the final ProPhoto at 0 only.

    **Gamut (verifier BLOCKER 2).** A chroma boost / hue rotation can push pixels
    outside AP1 → handled by the single gated `output._aces_rgc_compress_ap1` pass
    (after the ProPhoto→AP1 Bradford, where out-of-AP1 presents as negative AP1
    channels). This op does **no** inline gamut compression.

    **Byte-exact identity (verifier BLOCKER 3).** `hsl.is_identity()` → ``return
    prophoto`` (the literal input) before any conversion. The OKLab + Bradford
    round-trip is reversible only to float tolerance, so the short-circuit is
    mandatory — it (plus the gated downstream RGC) keeps a zero-HSL render
    byte-exact even on overrange data, so the gym 0.026 / rose 0.545 ΔE ship gate
    (perceptual-only) is untouched.

    **Constants are best-effort TUNING, not LR fidelity** (`_OKLCH_BAND_CENTERS_DEG`,
    `_OKLCH_HUE_MAX_DEG`, `_OKLCH_LUM_CHROMA_GATE`). The Axis-1 oracle validates the
    *defined* OKLCh band math + the Bradford, not appearance — matching the honesty
    discipline of `apply_hsl` / `apply_color_grade` / `apply_dr_compression`.
    """
    if hsl.is_identity():
        return prophoto  # byte-exact identity — short-circuit before any conversion

    import colour

    m_pp_to_xyz, m_xyz_to_pp = _prophoto_xyz_matrices()

    shape = prophoto.shape
    flat = prophoto.reshape(-1, 3).astype(np.float64)

    # ProPhoto(D50) lin → XYZ(D50) → XYZ(D65) [Bradford] → OKLab → OKLCh.
    xyz_d65 = (flat @ m_pp_to_xyz.T) @ _M_BRADFORD_D50_TO_D65.T
    oklch = colour.Oklab_to_Oklch(colour.XYZ_to_Oklab(xyz_d65))
    el = oklch[:, 0]
    c = oklch[:, 1]
    # Wrap hue into [0, 360) before band-weighting. colour returns [0, 360), but a
    # boundary 360.0 or a tiny negative would fall into NO band segment → all-zero
    # weights → c_out = c·0 (a spurious chroma collapse on that pixel). np.mod
    # closes that edge case at zero cost and matches the oracle's `% 360`.
    h = np.mod(oklch[:, 2], 360.0)  # hue in degrees, [0, 360)

    weights = _oklch_band_weights(h)  # (N, 8) partition of unity

    hue_shift_per_band = np.asarray(hsl.hue, dtype=np.float64) / 100.0 * _OKLCH_HUE_MAX_DEG
    sat_factor_per_band = 1.0 + np.asarray(hsl.saturation, dtype=np.float64) / 100.0
    lum_factor_per_band = 1.0 + np.asarray(hsl.luminance, dtype=np.float64) / 100.0

    hue_shift = weights @ hue_shift_per_band
    sat_mult = weights @ sat_factor_per_band
    lum_mult = weights @ lum_factor_per_band

    h_out = np.mod(h + hue_shift, 360.0)
    c_out = np.maximum(c * sat_mult, 0.0)  # floor 0; NO top clamp (overrange survives)

    # Gate Luminance by chroma so near-neutral pixels (ill-defined hue) are not
    # pushed by a colour band's Luminance slider. Above _OKLCH_LUM_CHROMA_GATE the
    # band gets full luminance authority.
    c_gate = np.clip(c / _OKLCH_LUM_CHROMA_GATE, 0.0, 1.0)
    eff_lum_mult = 1.0 + c_gate * (lum_mult - 1.0)
    l_out = np.maximum(el * eff_lum_mult, 0.0)  # floor 0; NO top clamp

    oklch_out = np.stack([l_out, c_out, h_out], axis=-1)

    # OKLCh → OKLab → XYZ(D65) → XYZ(D50) [Bradford] → ProPhoto(D50) lin.
    xyz_d65_out = colour.Oklab_to_XYZ(colour.Oklch_to_Oklab(oklch_out))
    pp_out = (xyz_d65_out @ _M_BRADFORD_D65_TO_D50.T) @ m_xyz_to_pp.T
    out = np.maximum(pp_out.reshape(shape), 0.0)  # floor 0; NO top clamp
    # Near-black guard (shared): the OKLCh cube-root toe gives near-neutral darks
    # an ill-defined, easily-amplified hue; roll the result to neutral in the
    # near-black tail so a colour band cannot inject a cast there. Above the floor
    # this is byte-identical (gate = 1). Measured: HSL's own near-black injection
    # is already negligible (chroma is not divided by luma in OKLCh), so this is
    # belt-and-suspenders that keeps the whole perceptual chain uniformly safe.
    lum_in = (flat @ _PROPHOTO_LUMINANCE).reshape(shape[:-1])
    out = _roll_chroma_to_neutral(out, lum_in)
    return out.astype(prophoto.dtype)


_CG_ACESCG_MATRICES: tuple[np.ndarray, np.ndarray] | None = None


def _cg_acescg_matrices() -> tuple[np.ndarray, np.ndarray]:
    """ProPhoto(D50)↔ACEScg(AP1) 3×3 matrices (Bradford CAT), computed once via
    colour-science and process-cached. Identical to
    `colour.RGB_to_RGB(..., apply_cctf_*=False)` (which is matrix-only) but without
    re-deriving the CAT+matrix product per frame — the CDL grade then converts with
    a matmul. Lazy import keeps `--dry-run` colour-free. Returns
    `(ProPhoto→ACEScg, ACEScg→ProPhoto)`."""
    global _CG_ACESCG_MATRICES
    if _CG_ACESCG_MATRICES is None:
        import colour
        _CG_ACESCG_MATRICES = (
            colour.matrix_RGB_to_RGB(
                "ProPhoto RGB", "ACEScg", chromatic_adaptation_transform="Bradford",
            ),
            colour.matrix_RGB_to_RGB(
                "ACEScg", "ProPhoto RGB", chromatic_adaptation_transform="Bradford",
            ),
        )
    return _CG_ACESCG_MATRICES


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

    # ProPhoto(D50) → ACEScg(AP1): same Bradford as output._prophoto_to_linear,
    # via the cached matrix (matmul, not a per-frame colour.RGB_to_RGB re-derive).
    to_acescg, to_prophoto = _cg_acescg_matrices()
    shape = prophoto.shape
    flat = prophoto.reshape(-1, 3).astype(np.float64)
    acescg = flat @ to_acescg.T

    # ACEScg → ACEScct log (library toe; NOT floored — the linear toe is
    # invertible for the small negatives an out-of-AP1 colour produces).
    # `log_encoding_ACEScct` computes log2 over ALL inputs before np.where-masking
    # in the toe, so true-black / negative-AP1 channels make it warn on a log2
    # value it then DISCARDS — the toe result is correct. Silence that internal
    # divide/invalid so real renders (which always carry some black) stay clean.
    with np.errstate(divide="ignore", invalid="ignore"):
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

    # ACEScct → ACEScg → ProPhoto(D50) (inverse Bradford, cached matmul). No top
    # clamp; floor 0.
    acescg_out = colour.models.log_decoding_ACEScct(log_out)
    pp_out = acescg_out @ to_prophoto.T
    out = np.maximum(pp_out.reshape(shape), 0.0)
    # Near-black guard (shared): the ACEScct log toe makes the zero-sum-in-log
    # chroma offset wildly ASYMMETRIC in linear near black (a shadow/global wheel
    # with Saturation injects a measurable cast on near-neutral darks — e.g.
    # |B-G| 0.0005→0.026 measured). Roll the result to neutral in the near-black
    # tail; above the floor it is byte-identical (gate = 1). Reuse the luminance
    # already computed for the zone weights.
    out = _roll_chroma_to_neutral(out, luminance.reshape(shape[:-1]))
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
    log_l = np.log2(np.maximum(lum, 0.0) + _LOG_EPS)

    # Adaptive box radius: clamp so the (2r+1) window fits the smaller spatial
    # axis. A 1-wide / sub-window array (the oracle's per-pixel layout, a tiny
    # tile) collapses to r=0, where the guided base == log_l exactly and the op is
    # the global law — one code path, no magic size threshold.
    r = min(_DR_GUIDED_RADIUS, (min(log_l.shape) - 1) // 2) if log_l.ndim == 2 else 0

    base_log = _guided_base_log(log_l, r, _DR_GUIDED_EPS)
    detail_log = log_l - base_log
    base_comp = _DR_LOG_ANCHOR + _dr_remap_log(base_log - _DR_LOG_ANCHOR, c_lo, c_hi, c_top)
    log_l_out = base_comp + detail_log
    lum_out = np.maximum(np.exp2(log_l_out) - _LOG_EPS, 0.0)

    # §0 hue-preserving reapply, near-black-safe: a +Shadows lift forms a huge
    # lum_out/lum ratio at near-black that would amplify a degenerate channel
    # imbalance into a false cast + AP1 negatives — the shared guard rolls that
    # tail to neutral (legit colour above the floor is byte-identical).
    out = _reapply_luminance_ratio(prophoto, lum, lum_out)  # floor 0; NO top clamp
    return out.astype(prophoto.dtype)


def apply_texture_clarity(
    prophoto: np.ndarray, texture: float, clarity: float,
) -> np.ndarray:
    """PERCEPTUAL local-contrast boost — the **boost-detail** mode of the shared
    edge-aware base/detail engine (DECISIONS.md §7 step 4; the inverse of
    `apply_dr_compression`, which *attenuates* the base). Makes the LR
    `Texture`/`Clarity` knobs — dropped + warn-only on the faithful path — *do
    something measurable* on the perceptual ACEScg master: add local/perceived
    contrast WITHOUT injecting halos. **Driven entirely by the two existing XMP
    sliders — no new control.**

    **The engine (one two-band guided decomposition, both ops consume it).** On a
    single log2-luminance channel `L = log2(rgb @ _PROPHOTO_LUMINANCE + eps)` two
    edge-preserving guided self-filters (He–Sun–Tang 2013, the SAME
    `_guided_base_log`/`_box_sum` the DR op uses, including the defining
    ``mean_a``/``mean_b`` step) extract a fine and a coarse base at radii
    ``_TC_RADIUS_FINE < _TC_RADIUS_COARSE``::

        texture_band = L − B_fine            # finest detail (small radius)
        clarity_band = B_fine − B_coarse     # mid-scale local contrast (larger radius)
        L_out = B_coarse
              + (1 + Kt·texture/100)·texture_band
              + (1 + Kc·(clarity/100)·midtone_w)·clarity_band

    **Texture** boosts (or, negative, smooths) the **finest** band uniformly;
    **Clarity** boosts the **larger-radius mid-scale** band, **midtone-weighted**
    (`midtone_w`, a C∞ Gaussian bump on log-luminance around the 0.18 anchor —
    `_TC_MIDTONE_SIGMA` stops) so it lands on midtones and tapers in deep
    shadow/bright highlight (Lightroom's Clarity is a midtone-contrast control).
    `Kt`/`Kc` are pinned per-slider gains. The split is **edge-aware**: the guided
    filter's local-linear `a→1` at strong edges zeroes the detail bands across an
    edge, so boosting them does **not** ring (the measured step-edge halo stays
    sub-1% of the plateau range at full sliders, vs a naive single-Gaussian
    high-pass which overshoots catastrophically — the op-family's defining
    failure). NB: like the DR guided base, this is the lightweight first cut and is
    **not** *provably* halo-free (Local Laplacian is) — it is measured-clean for
    this small-radius boost role, where v10c found the guided filter beats LLF.

    **Reduces to a no-op on flat input.** For a spatially flat region (or a
    sub-window / 1-wide array, where both adaptive box radii collapse to 0) both
    bases equal `L`, both bands are 0, and `L_out = L` — so the op is the identity
    on featureless content regardless of slider (only *local contrast* is touched,
    never global tone). This is the limit the Axis-1 oracle's flat legs check.

    **§0 hue/gamut (never per-channel).** The boosted luminance is reapplied by the
    out/in **ratio** ``rgb · L_out/max(L_in, eps)`` — a per-pixel positive scalar
    that preserves hue and chroma ratios exactly (the `apply_hsl` / DR-compression
    luminance pattern). Output is floored at 0 with **NO top clamp**: a >1 specular
    survives (out-of-AP1 excursions are the downstream ACES RGC pass's job in
    `output.py`, never an in-op clamp). Validate on **saturated + overrange**
    pixels; a grey wedge is blind to the per-channel-vs-ratio error.

    **Byte-exact identity.** ``texture == 0 and clarity == 0`` → ``return prophoto``
    (the literal input) before any pyramid/filter math. The guided box-filter
    round-trip is not bit-exact at float32 (slope-1 bands do not telescope to L
    bit-for-bit through log2/exp2), so the short-circuit is mandatory — it keeps the
    gym 0.026 / rose 0.545 ΔE ship gate untouched (perceptual-only; the gate renders
    the faithful stages 1–9).

    **Constants are best-effort TUNING, not Lightroom fidelity** (the module
    ``_TC_*`` constants). The Axis-1 oracle validates the *defined* two-band
    guided-boost math + the ratio reapply — not LR appearance — matching the honesty
    discipline of `apply_hsl` / `apply_color_grade` / `apply_dr_compression`.
    """
    if texture == 0.0 and clarity == 0.0:
        return prophoto  # byte-exact identity — short-circuit before any filter math

    # Luminance in float64 (the matmul against float64 _PROPHOTO_LUMINANCE promotes
    # a float32 frame); the ratio reapply runs on the original array so we never hold
    # a float64 copy of the whole RGB frame (mirrors apply_dr_compression).
    lum = prophoto @ _PROPHOTO_LUMINANCE
    log_l = np.log2(np.maximum(lum, 0.0) + _LOG_EPS)

    # Adaptive box radii: clamp so each (2r+1) window fits the smaller spatial axis.
    # A 1-wide / sub-window array (the oracle's per-pixel layout) collapses both to
    # r=0, where each guided base == log_l, both bands are 0, and the op is the
    # identity — one code path, no magic size threshold.
    if log_l.ndim == 2:
        half = (min(log_l.shape) - 1) // 2
        r_fine = min(_TC_RADIUS_FINE, half)
        r_coarse = min(_TC_RADIUS_COARSE, half)
    else:
        r_fine = r_coarse = 0

    base_fine = _guided_base_log(log_l, r_fine, _TC_GUIDED_EPS)
    base_coarse = _guided_base_log(log_l, r_coarse, _TC_GUIDED_EPS)
    texture_band = log_l - base_fine
    clarity_band = base_fine - base_coarse

    midtone_w = _tc_midtone_weight(log_l)
    texture_gain, clarity_gain = _tc_band_gains(texture, clarity, midtone_w)

    log_l_out = base_coarse + texture_gain * texture_band + clarity_gain * clarity_band
    lum_out = np.maximum(np.exp2(log_l_out) - _LOG_EPS, 0.0)

    # §0 hue-preserving reapply, near-black-safe (shared guard — see
    # `_reapply_luminance_ratio`): a detail boost that lifts a near-black pixel
    # would otherwise amplify a degenerate channel imbalance via the lum_out/lum
    # ratio; the guard rolls that tail to neutral (legit colour byte-identical).
    out = _reapply_luminance_ratio(prophoto, lum, lum_out)  # floor 0; NO top clamp
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


# Capture-sharpening tuning. The ACR/LR Amount→USM-strength and Radius→σ maps are
# closed-source; these are documented public approximations (no Lightroom-fidelity
# claim), owner-tunable against the LRT-JPG north-star. _AMOUNT_K sets the USM gain
# at Amount=100 (highpass added at 1×); _ACR_DEFAULT_* are ACR's raw-default Detail
# panel (Amount 40 / Radius 1.0 — Adobe's default capture sharpening on every raw,
# Amount raised to 40 in ACR 7.3), used by the `--capture-sharpen acr` mode to
# reproduce the sharpening the LRT JPG actually bakes when the XMP is silent.
_SHARPEN_AMOUNT_K = 1.0
_ACR_DEFAULT_AMOUNT = 40.0
_ACR_DEFAULT_RADIUS = 1.0
_ACR_DEFAULT_DETAIL = 25.0       # ACR raw-default Detail
_ACR_DEFAULT_MASKING = 0.0       # ACR raw-default Masking (off)
# Detail / Masking shaping knees (perceptual sRGB units), documented tuning — the
# ACR curves are closed-source. _DETAIL_SOFT is the tanh halo-clip knee; _MASK_EDGE
# is the luminance-gradient magnitude treated as a "full" edge.
_DETAIL_SOFT = 0.08
_MASK_EDGE = 0.06


def _sharpen_detail_limit(highpass: np.ndarray, detail: float) -> np.ndarray:
    """Detail (0..100) — halo suppression on the high-pass. `detail=100` → the raw
    high-pass (full halos / maximal fine detail); `detail→0` → a tanh soft-clip that
    caps the large overshoot/undershoot **rings** (halos) at strong edges while
    passing small high-pass (fine texture) ≈ unchanged. A documented public
    approximation of ACR's closed-source Detail (its deconvolution↔USM blend); no
    fidelity claim, owner-tunable via `_DETAIL_SOFT`. Monotonic in `detail`."""
    if detail >= 100.0:
        return highpass
    d = detail / 100.0
    soft = _DETAIL_SOFT * np.tanh(highpass / _DETAIL_SOFT)  # caps |hp| ≈ _DETAIL_SOFT
    return d * highpass + (1.0 - d) * soft


def _sharpen_edge_mask(perceptual: np.ndarray, masking: float) -> np.ndarray:
    """Masking (0..100) — protect flat areas. `masking=0` → mask ≡ 1 (sharpen
    everywhere); `masking→100` → mask = a smoothstep gate on the luminance gradient
    magnitude (low-gradient/flat regions → 0, strong edges → 1), so smooth areas
    (sky / skin / sensor noise) are spared the USM. A documented public approximation
    of ACR's closed-source edge mask; owner-tunable via `_MASK_EDGE`. Monotonic in
    `masking`; returns values in [0, 1]."""
    if masking <= 0.0:
        return np.ones_like(perceptual)
    gy, gx = np.gradient(perceptual)
    t = np.clip(np.hypot(gx, gy) / _MASK_EDGE, 0.0, 1.0)
    gate = t * t * (3.0 - 2.0 * t)  # smoothstep(0,1)
    return 1.0 - (masking / 100.0) * (1.0 - gate)


def _resolve_capture_sharpen(
    ops: DevelopOps, mode: str,
) -> tuple[float, float, float, float]:
    """Resolve the effective (Amount, Radius, Detail, Masking) from the ops + mode.

    - ``"off"`` → Amount 0 (apply_sharpness short-circuits → byte-exact identity).
    - ``"xmp"`` → the XMP's crs:Sharpness / SharpenRadius / SharpenDetail /
      SharpenEdgeMasking (Amount 0 → no-op).
    - ``"acr"`` → ACR's raw defaults (40 / 1.0 / 25 / 0) when the XMP carries **no**
      Amount, else the XMP's own values — fall back to the default-on capture
      sharpening the LRT JPG bakes, but honour an explicit colorist Amount.
    """
    if mode == "off":
        return 0.0, ops.sharpen_radius, ops.sharpen_detail, ops.sharpen_edge_masking
    if mode == "acr" and ops.sharpness == 0.0:
        return (_ACR_DEFAULT_AMOUNT, _ACR_DEFAULT_RADIUS,
                _ACR_DEFAULT_DETAIL, _ACR_DEFAULT_MASKING)
    return (ops.sharpness, ops.sharpen_radius,
            ops.sharpen_detail, ops.sharpen_edge_masking)


def apply_sharpness(
    prophoto: np.ndarray, amount: float, radius: float = _ACR_DEFAULT_RADIUS,
    detail: float = 100.0, masking: float = 0.0,
) -> np.ndarray:
    """Clean-room ACR/LR **capture sharpening** — a luminance unsharp mask.

    `amount` is the ACR Amount (0..150; **0 = byte-exact identity short-circuit**),
    `radius` the Gaussian radius (ACR 0.5..3.0), `detail` (0..100) halo suppression,
    `masking` (0..100) flat-area protection. The function defaults `detail=100`
    (no halo limit) and `masking=0` (no mask) give the **pure USM**; the pipeline
    passes the XMP / ACR values (ACR's raw defaults are Detail 25 / Masking 0, via
    `_resolve_capture_sharpen`). FAITHFUL-path only (the CLI gates this via
    `--capture-sharpen` and never calls it on the perceptual master, which defers
    detail to the grade — trunk/branch model). A documented public approximation of
    ACR's closed-source Detail-panel USM; **no Lightroom-fidelity claim** (it is a
    §9 / §11 "deliberately exceed the LRT-JPG" enhancement, not a dng_validate match
    — `dng_validate` does no sharpening).

    **Domain — sRGB OETF, by construction.** The op runs at the end of Stage 12 on
    linear ProPhoto, *before* the Stage-13 ProPhoto→sRGB gamut matrix + sRGB OETF.
    A 3×3 gamut matrix and a per-pixel monotonic OETF **do not move edges**, so
    sharpening the **sRGB-encoded** luminance here is spatially equivalent to
    sharpening the display image ACR/LRT actually sharpened — and the perceptual
    encode gives ACR-like (not linear-light, highlight-biased) halos. `_srgb_oetf`
    is applied with **no [0,1] clip**, so it extends monotonically above 1 →
    **highlight headroom (`highlight_recovery` Tier-1, lum > 1) survives**; the
    inverse `_srgb_eotf` floors its base at 0 (no NaN on an undershoot).

    **Luminance-only, chroma-preserving.** USM acts on luminance; the change is
    reapplied to RGB via the §0 hue-preserving `_reapply_luminance_ratio`
    (out/in ratio, near-black-safe, **floor 0, NO top clamp** → overrange survives
    to the downstream encode). So a coloured edge keeps its hue and no chroma
    fringing is introduced. **Detail** soft-clips the high-pass halos
    (`_sharpen_detail_limit`); **Masking** gates the high-pass by the luminance
    gradient to spare flat areas (`_sharpen_edge_mask`) — both isolated, documented,
    owner-tunable approximations of ACR's closed-source curves.
    """
    if amount == 0.0:
        return prophoto  # byte-exact identity — the ship-gate / default-off contract
    from scipy.ndimage import gaussian_filter

    from lrt_cinema.lut3d_baker import _srgb_eotf, _srgb_oetf

    lum = prophoto @ _PROPHOTO_LUMINANCE  # linear ProPhoto luminance (≥0, may be >1)
    # Perceptual domain — sRGB OETF, NO clip (extends >1 → headroom preserved).
    perceptual = _srgb_oetf(np.maximum(lum, 0.0))
    sigma = max(float(radius), 0.0)
    blurred = gaussian_filter(perceptual, sigma=sigma, mode="reflect")
    highpass = perceptual - blurred  # σ=0 → blurred==perceptual → highpass 0 → no-op
    highpass = _sharpen_detail_limit(highpass, detail)        # Detail: halo suppression
    highpass = highpass * _sharpen_edge_mask(perceptual, masking)  # Masking: flat-area gate
    strength = (amount / 100.0) * _SHARPEN_AMOUNT_K
    perceptual_sharp = perceptual + strength * highpass
    lum_out = _srgb_eotf(perceptual_sharp)  # → linear; eotf floors its base ≥0
    out = _reapply_luminance_ratio(prophoto, lum, lum_out)  # floor 0; NO top clamp
    return out.astype(prophoto.dtype, copy=False)


def _apply_contrast_perceptual(prophoto: np.ndarray, contrast: float) -> np.ndarray:
    """PERCEPTUAL Contrast — the Contrast2012 pivot-0.18 gain applied to
    **luminance** with §0 out/in-ratio reapply, so it is **hue-preserving**.

    The faithful `apply_contrast_2012` scales each channel independently around
    0.18 — which on saturated colour changes the channel *ratios* and so rotates
    hue/saturation. That matches Lightroom on the round-trip TIFF, but on the
    perceptual master it would undo the §0 hue-stability every other perceptual op
    (OKLCh HSL, ratio-reapply DR-compression/Texture, zero-sum CDL) maintains. So
    here the same gain law `lum_out = 0.18 + (lum − 0.18)·gain` (`gain =
    1 + contrast/100`) runs on the luminance channel only, reapplied by the
    `lum_out/lum` ratio (the `apply_dr_compression` / `apply_hsl` pattern):
    a per-pixel positive scalar that preserves hue and chroma ratios exactly.
    Floored at 0, **no top clamp** (overrange survives → downstream RGC). Byte-exact
    identity at `contrast == 0`. Pivot/gain are the faithful op's; no LR claim."""
    if contrast == 0.0:
        return prophoto
    gain = 1.0 + contrast / 100.0
    lum = prophoto @ _PROPHOTO_LUMINANCE
    lum_out = np.maximum(0.18 + (lum - 0.18) * gain, 0.0)
    # §0 hue-preserving reapply, near-black-safe (shared guard). A NEGATIVE
    # contrast lifts shadows toward the 0.18 pivot → a huge lum_out/lum ratio at
    # near-black; without the guard that amplifies the degenerate single-channel
    # pixels apply_blacks_2012 can leave (e.g. [0,0,2.6e-6]) into a saturated
    # false cast + AP1 negatives (the original perceptual-near-black bug). The
    # guard rolls that tail to neutral, matching faithful per-channel Contrast2012;
    # legit shadow colour above the floor is byte-identical.
    out = _reapply_luminance_ratio(prophoto, lum, lum_out)  # floor 0; NO top clamp
    return out.astype(prophoto.dtype)


# ---------------------------------------------------------------------------
# Top-level dispatchers
# ---------------------------------------------------------------------------


def apply_stage_12_perceptual(
    prophoto: np.ndarray, ops: DevelopOps,
    intent: RenderIntent = RenderIntent.FAITHFUL,
    capture_sharpen: str = "off",
) -> np.ndarray:
    """Apply all stage-12 perceptual-domain ops; `intent` selects the applicators
    (DECISIONS.md §7). `capture_sharpen` ({off,xmp,acr}, default off → byte-exact)
    gates FAITHFUL-path capture sharpening (`apply_sharpness`); see DECISIONS §5
    amendment.

    **FAITHFUL** (default — the sRGB TIFF / LRT round-trip, the Lightroom look):
    ToneCurve → Saturation → Vibrance → HSL → ColorGrade → Contrast → Sharpness,
    using the Adobe-hexcone `apply_hsl`, additive split-tone `apply_color_grade`,
    and per-channel `apply_contrast_2012`.

    **PERCEPTUAL** (the ACEScg master): ToneCurve → Sat → Vibrance →
    **DR-compression → HSL → ColorGrade → Texture/Clarity → Contrast** → Sharpness.
    Tone first — `apply_dr_compression` sets the dynamic range from the
    Highlights/Shadows/Whites knobs (Lightroom likewise applies Basic tone before
    Color Grading; DECISIONS §5 amendment) — then colour (OKLCh `_apply_hsl_perceptual`,
    offset-only ASC-CDL `_apply_color_grade_perceptual`), then local detail
    (`apply_texture_clarity`), then a **hue-preserving** luminance-domain
    `_apply_contrast_perceptual` (the faithful per-channel Contrast2012 would rotate
    hue on saturated colour). Every perceptual op is §0 hue-stable and
    overrange-preserving (out-of-AP1 → the downstream `output._aces_rgc_compress_ap1`
    pass). DR-compression + Texture/Clarity are PERCEPTUAL-only; on the faithful
    path their knobs (H/S/W + Texture/Clarity) stay dropped + warn-only
    (`cli._warn_dropped_ops`).

    **Identity / ship gate.** Every op short-circuits to a byte-exact no-op when
    its slider(s) are zero, so a render with no perceptual intent is bit-identical
    across intents and the gym 0.026 / rose 0.545 ΔE ship gate (faithful stages
    1-9 → sRGB) is untouched. ToneCurve/Sat/Vibrance are intent-independent;
    **Sharpness is FAITHFUL-only** (the perceptual master defers detail to the
    grade) and gated off by default; HSL, ColorGrade, Contrast, DR-compression,
    Texture/Clarity branch on intent."""
    out = apply_tone_curve_pv2012(prophoto, ops.tone_curve)
    out = apply_saturation(out, ops.saturation)
    out = apply_vibrance(out, ops.vibrance)
    if intent is RenderIntent.PERCEPTUAL:
        # Tone → colour → local detail → contrast, every step hue-preserving (§0).
        # DR-compression (Highlights/Shadows/Whites, DECISIONS §5 amendment) runs
        # FIRST: set the dynamic range, then grade/detail the tamed result —
        # Lightroom likewise applies Basic tone before Color Grading. PERCEPTUAL-
        # only; the faithful path drops H/S/W + Texture/Clarity (warn-only,
        # cli._warn_dropped_ops). Each op is a byte-exact no-op at zero sliders,
        # so the ΔE ship gate (faithful stages 1-9) is untouched.
        out = apply_dr_compression(out, ops.highlights, ops.shadows, ops.whites)
        out = _apply_hsl_perceptual(out, ops.hsl)
        out = _apply_color_grade_perceptual(out, ops.color_grade)
        out = apply_texture_clarity(out, ops.texture, ops.clarity)
        # Hue-preserving (luminance-domain, ratio-reapply) contrast — keeps the
        # perceptual path's §0 hue-stability; the faithful per-channel
        # Contrast2012 (below) would rotate hue/saturation on saturated colour.
        out = _apply_contrast_perceptual(out, ops.contrast)
    else:
        out = apply_hsl(out, ops.hsl)
        out = apply_color_grade(out, ops.color_grade)
        out = apply_contrast_2012(out, ops.contrast)
    # Capture sharpening — FAITHFUL path only (matches the LRT JPG's baked ACR
    # capture sharpening); the perceptual master defers detail to the grade
    # (trunk/branch model). `capture_sharpen=off` (default) → Amount 0 → the
    # apply_sharpness short-circuit → byte-exact. Was a no-op stub on both intents,
    # so dropping it from the perceptual branch is byte-identical.
    if intent is RenderIntent.FAITHFUL:
        amount, radius, detail, masking = _resolve_capture_sharpen(ops, capture_sharpen)
        out = apply_sharpness(out, amount, radius, detail, masking)
    return out


def apply_develop_ops(
    prophoto: np.ndarray, ops: DevelopOps,
    intent: RenderIntent = RenderIntent.FAITHFUL,
    master_look: str = "bake",
    capture_sharpen: str = "off",
) -> np.ndarray:
    """Entry point: apply all develop ops (stages 11 + 12) to linear
    ProPhoto. Returns linear ProPhoto post-LR-ops, ready for stage 13
    (color-space conversion + output encoding in `output.py`).

    `intent` (DECISIONS.md §7) picks the Stage-12 grading applicator — FAITHFUL
    (default, Adobe-hexcone, sRGB TIFF) or PERCEPTUAL (modern primitives, ACEScg
    master). Stage 11 is intent-independent.

    `master_look` (trunk/branch model — docs/research/pipeline-overhaul-plan.md):
      - ``"bake"`` (default): apply Stage 11 + Stage 12 — the full develop chain.
        Every existing caller gets this → byte-exact, unchanged.
      - ``"defer"``: apply **Stage 11 only** (Exposure2012 / Blacks2012 — the
        per-frame, temporally-varying corrections, where the deflicker + Holy-Grail
        exposure ramp ride) and DEFER the **static creative look** (Stage 12) to the
        downstream colorist. Rationale: per-frame intent has **no transport** across
        an NLE handoff (DECISIONS §4 — keyframes don't survive Resolve import), so it
        MUST bake; a static sequence-wide look survives a single clip grade, so it can
        be left out for maximum grading latitude on the scene-linear master. The CLI
        sets ``"defer"`` only on the PERCEPTUAL tap-7 master; faithful always bakes.
        NB: a v1 split — Stage-12 ops keyframed across the sequence are also deferred;
        a per-op "animated?" detector is the documented refinement (use ``"bake"`` if
        the master must carry an animated Stage-12 grade).
    """
    if master_look not in ("bake", "defer"):
        raise ValueError(f"master_look must be 'bake' or 'defer', got {master_look!r}")
    out = apply_stage_11_linear(prophoto, ops)
    if master_look == "defer":
        return out
    return apply_stage_12_perceptual(out, ops, intent, capture_sharpen)


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


def _oklch_band_weights(h: np.ndarray) -> np.ndarray:
    """Triangular partition-of-unity weights over the eight OKLCh band centres.

    `h`: OKLCh hue array in **degrees**, `[0, 360)`. Returns `(..., 8)` weights
    that sum to exactly 1 per pixel — the degrees analogue of `_hsl_band_weights`
    over `_OKLCH_BAND_CENTERS_DEG`. Each hue lies in exactly one segment between
    two adjacent centres and splits its weight linearly between them; the final
    segment wraps Magenta(300°) → Red(0°≡360°). The unit-sum property is what
    makes all-equal bands behave as a single global adjustment and the all-zero
    default an exact identity (so the perceptual HSL no-ops byte-for-byte on the
    short-circuit, and an all-equal-band grade is a clean global rotation/scale)."""
    weights = np.zeros(h.shape + (8,), dtype=np.float64)
    centers = _OKLCH_BAND_CENTERS_DEG
    for j in range(8):
        lo = centers[j]
        hi = centers[j + 1] if j < 7 else 360.0  # wrap: Magenta → Red
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
# Perceptual HSL (OKLCh) — colour-space constants + tuning
# ---------------------------------------------------------------------------
#
# The PERCEPTUAL path's HSL (`_apply_hsl_perceptual`) grades in OKLCh proper —
# the perceptually-uniform, gamut-AGNOSTIC space (Okhsl/Okhsv are sRGB-gamut-
# bound by construction — wrong for wide-gamut ACEScg; DECISIONS.md §7, v09
# frontier §2.1). The transform chain is ProPhoto(D50)-in / ProPhoto(D50)-out
# (contract 1; `output.py` does the ProPhoto→ACEScg Bradford at Stage 13):
#
#   ProPhoto(D50) lin → XYZ(D50) → XYZ(D65) [Bradford] → OKLab → OKLCh
#       → adjust → OKLab → XYZ(D65) → XYZ(D50) [Bradford] → ProPhoto(D50) lin
#
# Ottosson's Oklab is defined on **D65-adapted** XYZ (verified: `colour`'s
# `XYZ_to_Oklab` docstring requires D65 input), so the D50→D65 Bradford is
# mandatory — feeding D50 XYZ straight in is the wrong-whitepoint bug the oracle's
# inverted-Bradford sensitivity leg catches. Production MAY call
# `colour.XYZ_to_Oklab`/`Oklab_to_Oklch` (and inverses); the Axis-1 oracle hand-
# rolls Ottosson's M1/M2 + cube-root instead (contract 4 — using the production
# function on both sides is a tautology that passes a transcription bug).

# Bradford chromatic-adaptation matrices D50↔D65, pinned as float64 module
# constants per the spec, cross-checked against colour-science (the same
# `matrix_chromatic_adaptation_VonKries(..., transform="Bradford")` the rest of
# the pipeline adapts with) by
# test_color_oracle.py::test_oklch_bradford_constants_match_colour_science so they
# cannot silently drift from the CAT `output.py`'s ProPhoto→ACEScg actually uses.
_M_BRADFORD_D50_TO_D65 = np.array([
    [0.9554734, -0.0230985, 0.0632592],
    [-0.0283697, 1.0099954, 0.0210414],
    [0.0123140, -0.0205076, 1.3303659],
], dtype=np.float64)
_M_BRADFORD_D65_TO_D50 = np.array([
    [1.0479298, 0.0229469, -0.0501923],
    [0.0296278, 0.9904344, -0.0170738],
    [-0.0092430, 0.0150552, 0.7518743],
], dtype=np.float64)


def _prophoto_xyz_matrices() -> tuple[np.ndarray, np.ndarray]:
    """ProPhoto(D50)-linear ↔ XYZ(D50) matrices, pulled from colour-science at
    call time so they are EXACTLY the ROMM RGB matrix `output._prophoto_to_linear`
    converts with (no hand-typed transcription to drift). Returns
    `(RGB→XYZ, XYZ→RGB)`."""
    import colour

    cs = colour.RGB_COLOURSPACES["ProPhoto RGB"]
    return (
        np.asarray(cs.matrix_RGB_to_XYZ, dtype=np.float64),
        np.asarray(cs.matrix_XYZ_to_RGB, dtype=np.float64),
    )


# OKLCh band centres, in **hue degrees** on the OKLCh wheel (the named-colour
# layout: Red 0°, Orange 30°, Yellow 60°, Green 120°, Aqua 180°, Blue 240°,
# Purple 270°, Magenta 300°). Same eight named bands as the faithful hexcone
# `_HSL_BAND_CENTERS_HEX`, expressed in OKLCh hue degrees rather than the [0,6)
# hexcone hue. Adobe's exact centres are closed-source; this is the conventional
# reverse-engineered layout (the Axis-1 oracle validates this *defined* math, not
# Lightroom fidelity — VALIDATION.md).
_OKLCH_BAND_CENTERS_DEG = np.array(
    [0.0, 30.0, 60.0, 120.0, 180.0, 240.0, 270.0, 300.0], dtype=np.float64,
)

# Hue-slider magnitude: ±100 → ±30° of OKLCh hue rotation (the conventional
# value, matching the faithful path's ±30° via `_HSL_HUE_MAX_HEX`).
_OKLCH_HUE_MAX_DEG = 30.0

# Below this OKLCh chroma the per-band Luminance effect ramps to zero, so
# near-neutral pixels (ill-defined hue) are protected from a colour band's
# Luminance slider — the OKLCh analogue of the faithful path's `_HSL_LUM_SAT_GATE`
# (an HSV-saturation gate). OKLCh chroma is an absolute (not [0,1]) scale, so this
# is a small chroma threshold, NOT a saturation fraction. Best-effort TUNING (the
# faithful gate is likewise empirical); re-derivable against a ColorChecker later.
_OKLCH_LUM_CHROMA_GATE = 0.04

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

# Log chroma offset per unit Saturation/100 along the (zero-sum) hue direction,
# applied as a per-channel additive log delta (the direction is mean-subtracted →
# zero-sum, carries no net lift). Pinned to **faithful's 3:1 chroma:lum ratio**
# (`_CG_CHROMA_STRENGTH / _CG_LUM_STRENGTH = 0.30/0.10`) expressed log-natively =
# 3·K_lum_log ≈ 0.171 → a full primary wheel (sat=100) is ≈ 2 stops on its
# strongest channel (vs the Luminance lift's 1 stop/wheel). NB this is NOT the
# linear `_CG_CHROMA_STRENGTH=0.30` reused verbatim — 0.30 *code units* would be
# ~3.5 stops/wheel (ACEScct Δcode×17.52 = Δstops); only the RATIO carries across
# the linear→log domain change. Tuning, not LR fidelity.
_CG_CHROMA_LOG_STRENGTH = 3.0 * _CG_LUM_LOG_STRENGTH

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
    log_l = np.log2(np.maximum(luminance, 0.0) + _LOG_EPS)
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

_LOG_EPS = 1e-6          # shared log(0) floor (DR-compression, CDL zone proxy,
                         # Texture/Clarity); survives scene-linear true zeros. Why
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


# ---------------------------------------------------------------------------
# Internal — near-black stability guard (shared across the perceptual ops)
# ---------------------------------------------------------------------------
#
# Why this exists. The perceptual Stage-12 ops are §0 hue-PRESERVING by design
# (out/in-ratio reapply for the luminance ops; OKLCh / ACEScct-log working spaces
# for the colour ops). That hue-preservation is correct for legit colour but is
# DANGEROUS in the near-black tail, for two compounding reasons:
#   * `apply_blacks_2012` (Stage 11, intent-INDEPENDENT) subtracts a uniform bias
#     and floors at 0, so a dark, slightly-chromatic pixel can lose its smaller
#     channels to EXACTLY 0 — leaving a degenerate, maximally-"saturated"
#     single-channel near-black pixel (observed on the D750 gym frame:
#     [0,0,2.6e-6] pure-blue, [1.9e-6,0,0] pure-red); and
#   * a shadow-LIFTING reapply (Contrast2012<0, +Shadows DR-compression, …) forms
#     `ratio = lum_out/lum`, which → ∞ as lum → 0 and multiplies that degenerate
#     imbalance into a bright false cast. After the ProPhoto→AP1 Bradford in
#     `output._prophoto_to_linear` the cast presents as NEGATIVE AP1 channels that
#     the gated ACES RGC cannot rescue at near-black (its correction scales by
#     |ach| ≈ 0). The ACEScct-log ColorGrade toe similarly turns a zero-sum-in-log
#     chroma offset into a real near-black cast.
# The FAITHFUL ops get near-black neutrality for free: per-channel Contrast2012
# lifts EVERY channel toward the 0.18 pivot, so a near-black pixel goes neutral
# regardless of its imbalance (this is exactly why faithful renders the same grade
# with clean shadows + zero negatives). These guards give the perceptual ops the
# same near-black neutrality WITHOUT sacrificing hue-stability on legit colour:
# above `_NEARBLACK_LUM_FLOOR` the gate is EXACTLY 1.0 (smoothstep clamps), so
# legit shadow colour is byte-identical to the raw op; only the near-black tail
# rolls to neutral. The op's zero-slider `is_identity()` short-circuit fires first,
# so a no-grade render never reaches here (the ΔE ship gate is untouched).
#
# Floor choice. `_NEARBLACK_LUM_FLOOR = 0.004` caps the effective ratio
# amplification at ≈ lum_out/floor ≈ 9× (a typical shadow lift reaches ≈0.036).
# Measured on the D750 production frame it clears 100% of the false-cast + AP1-
# negative population (a floor of 0.002 still left the worst pixel at −3.2e-4;
# 0.004 → min +4.9e-4, zero negatives) while leaving every pixel ≥ ~0.4% grey
# byte-untouched. Gated on the op's INPUT luminance — the stable, pre-toe quantity
# — so a post-lift LOW-luminance cast (a lifted pure-blue still has luminance ≈ 0)
# cannot evade the gate. Tuning, not an LR-fidelity claim, like the other `_*`
# perceptual constants; the Axis-1 oracle validates the *defined* blend, not
# appearance.
_NEARBLACK_LUM_FLOOR = 0.004


def _nearblack_gate(lum: np.ndarray) -> np.ndarray:
    """C1 near-black weight: 0 at `lum=0`, smoothly → 1 at `lum ≥
    _NEARBLACK_LUM_FLOOR` (EXACTLY 1.0 above the floor — smoothstep clamps — so the
    guard is byte-identical to the raw op for legit colour). `lum` is a
    scene-linear luminance array `(…,)`."""
    return _smoothstep(lum / _NEARBLACK_LUM_FLOOR)


def _reapply_luminance_ratio(
    prophoto: np.ndarray, lum: np.ndarray, lum_out: np.ndarray,
) -> np.ndarray:
    """Reapply a per-pixel luminance change `lum → lum_out` as the §0 hue-
    preserving out/in ratio `rgb · lum_out/lum`, rolling toward the achromatic
    pixel that carries the SAME luminance (`[lum_out]³` — exact because
    `sum(_PROPHOTO_LUMINANCE) = 1`) as input luminance → 0. The shared near-black-
    safe reapply for the luminance-domain perceptual ops (DR-compression,
    Texture/Clarity, Contrast).

    Above `_NEARBLACK_LUM_FLOOR` the gate is 1.0, so this returns the literal
    `rgb · lum_out/lum` (byte-identical to the pre-guard op — `1·x + 0·y == x`).
    Both blend branches carry luminance `lum_out`, so the guard rolls CHROMA →
    neutral in the near-black tail WITHOUT touching tone. Floored at 0; NO top
    clamp (overrange survives → downstream ACES RGC). The caller recasts to its
    own dtype. See the section comment for the failure mode this prevents."""
    ratio = lum_out / np.maximum(lum, _LOG_EPS)
    hue_preserving = prophoto * ratio[..., None]
    g = _nearblack_gate(lum)[..., None]
    achromatic = np.broadcast_to(lum_out[..., None], hue_preserving.shape)
    out = g * hue_preserving + (1.0 - g) * achromatic
    return np.maximum(out, 0.0)


def _roll_chroma_to_neutral(graded: np.ndarray, lum_in: np.ndarray) -> np.ndarray:
    """Roll a perceptual COLOUR op's output toward neutral (its own luminance,
    zero chroma) as INPUT luminance → 0, so the op injects no colour into the
    near-black tail where its working-space toe (OKLCh cube-root; ACEScct log)
    can turn a tiny / zero-sum imbalance into a spurious cast. Shared by
    `_apply_hsl_perceptual` and `_apply_color_grade_perceptual`.

    Above `_NEARBLACK_LUM_FLOOR` the gate is 1.0, so it returns `graded`
    byte-for-byte (legit colour untouched — the ops' oracle patches all sit above
    the floor). The neutral target is `graded`'s OWN luminance broadcast to three
    channels, so the roll removes only the near-black CHROMA the op added, never
    its tone. `lum_in` is the op's INPUT scene luminance (the stable, pre-toe gate
    quantity)."""
    g = _nearblack_gate(lum_in)[..., None]
    lum_graded = (graded @ _PROPHOTO_LUMINANCE)[..., None]
    achromatic = np.broadcast_to(lum_graded, graded.shape)
    return g * graded + (1.0 - g) * achromatic


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
    u = np.log2(np.maximum(lum, 0.0) + _LOG_EPS) - _DR_LOG_ANCHOR
    g = _dr_remap_log(u, c_lo, c_hi, c_top)
    return np.maximum(_DR_ANCHOR * np.exp2(g) - _LOG_EPS, 0.0)


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


# ---------------------------------------------------------------------------
# Internal — Texture / Clarity (boost-detail mode of the shared guided engine)
# ---------------------------------------------------------------------------
#
# `apply_texture_clarity` reuses the DR op's guided base/detail split (He–Sun–Tang
# 2013, `_guided_base_log`/`_box_sum`) at TWO radii to form a fine + a mid-scale
# band, then BOOSTS them (the inverse of the DR op, which attenuates the base).
# Every constant below is a best-effort TUNING value pinned at implementation time
# (research/v10-local-tone-mapping-dr-compression.md §2.3/§3.2) — the Axis-1 oracle
# validates the *defined* math, NOT Lightroom appearance. They are NOT open theory
# and NOT "auto from image". The guided filter was chosen over the local-Laplacian
# proto on a measured step-edge halo comparison (guided sub-1% vs naive USM ~580%;
# LLF comparable but fragile + non-byte-exact pyramid) — see the docstring +
# docs/research/v10c-local-laplacian-base-deferred.md (the α<1 detail-boost note).

# Guided-filter radii (px) for the two bands, log-luminance domain. Texture = the
# FINE band (small radius → finest detail); Clarity = the larger mid-scale band
# (B_fine − B_coarse). eps ~0.01 (log2² stops), the same conservative value the DR
# base uses (favours edge preservation → the measured-clean halo).
_TC_RADIUS_FINE = 2
_TC_RADIUS_COARSE = 16
_TC_GUIDED_EPS = 0.01

# Per-slider boost gains: band_out = (1 + K·slider/100)·band. K=1.5 ⇒ slider=+100
# multiplies that band's log amplitude by 2.5 (a strong-but-bounded boost), −100
# multiplies by −0.5 (smooths + slight inversion floor). Both stay finite for any
# slider in [−100, 100]; the step-edge halo is sub-1% of the plateau range even at
# the +100 extreme (measured). Tuning, not LR fidelity.
_TC_TEXTURE_GAIN = 1.5
_TC_CLARITY_GAIN = 1.5

# Clarity midtone weight: a C∞ Gaussian bump on log2-luminance centred at the 0.18
# anchor (`_DR_LOG_ANCHOR`), sigma in log2 stops. Clarity's mid-scale boost is
# scaled by this so it lands on midtones and tapers toward deep shadow / bright
# highlight (Lightroom's Clarity is a midtone-contrast control). Texture is NOT
# midtone-weighted (it is a uniform fine-detail boost). 3 stops ≈ a broad midtone
# band (FWHM ~7 stops). Tuning, not LR fidelity.
_TC_MIDTONE_SIGMA = 3.0


def _tc_midtone_weight(log_l: np.ndarray) -> np.ndarray:
    """C∞ Gaussian midtone bump on log2-luminance, peak 1.0 at the 0.18 anchor,
    sigma `_TC_MIDTONE_SIGMA` stops. Scales the Clarity band so its local-contrast
    boost is midtone-weighted (tapers in deep shadow / bright highlight)."""
    return np.exp(-0.5 * ((log_l - _DR_LOG_ANCHOR) / _TC_MIDTONE_SIGMA) ** 2)


def _tc_band_gains(texture, clarity, midtone_w):
    """The per-band detail-boost multipliers, **floored at 0**. Texture scales the
    fine band uniformly; Clarity scales the mid band weighted by `midtone_w` (1.0 at
    the anchor). The floor makes a strong NEGATIVE slider SMOOTH (gain→0) rather than
    phase-INVERT the detail: with `K=1.5` the unfloored gain `1 + K·s/100` crosses 0
    at `s ≈ −67`, and a negative gain flips a bright speckle to a dark one (LR's
    negative Texture/Clarity never inverts). Floored, the gain is monotone non-
    decreasing in the slider and ≥ 0 everywhere; at `s=+100` it is the full
    `1 + K = 2.5` (the floor is inert on the boost arm). `midtone_w` may be a scalar
    or an array (Clarity returns the broadcast result)."""
    texture_gain = max(0.0, 1.0 + _TC_TEXTURE_GAIN * (texture / 100.0))
    clarity_gain = np.maximum(0.0, 1.0 + _TC_CLARITY_GAIN * (clarity / 100.0) * midtone_w)
    return texture_gain, clarity_gain
