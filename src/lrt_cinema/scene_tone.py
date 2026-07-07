"""Scene-referred LOCAL Highlights/Shadows operator (LR PV2012 translation).

The round-2 CAL probes (owner LR Classic exports of DSC_4053 with single
sliders set; `tools/cal_domain_round2.py`, evidence
`tests/fixtures/evidence/cal_domain_round2_2026-07-07.json`) measured, for
`Highlights2012` / `Shadows2012`:

* the application domain is SCENE-REFERRED — the global scene-domain arm
  beats the display-domain arm on ΔE and above all on chroma (Highlights
  −100: ΔC 0.26 scene vs 0.91 display; the CALEXP exposure-domain
  precedent's discriminator), and
* the operator is LOCAL — after the best-fit GLOBAL curve is removed, the
  residual correlates with neighbourhood luminance (Shadows: pooled
  within-bin context r ≈ −0.6 vs the global-slider yardstick ≈ −0.14;
  Shadows +100 defeats every global arm outright, armD 2.27 / armS 4.24),
  concordant with Adobe's own local-adaptive documentation (CLAIMS
  "Exact ACR match is achievable → REFUTED(by construction)").

So this op (v2, owner round-1 verdicts folded in 2026-07-08): the
calibrated tone delta is applied THROUGH a fast Local-Laplacian pyramid
(`_fast_llf.llf_apply_tone`) on toe-floored log2 scene luminance — detail
preserved verbatim, edges remapped at the tone map's own slope, absolute
response carried at the pyramid residual's regional scale — then a
hue-preserving ratio reapply with a PROGRESSIVE near-black chroma roll.
Round 1 used a guided base/detail split; the owner flips refuted it on
two counts: the affected region went FLAT (edge-tracking base ⇒ per-pixel
curve), and Shadows +100 grew boundary artifacts at the crushed-black
interface (log-toe noise + a binary chroma gate). v2 kills both by
construction; the LLF-as-base defer (v10c) does not apply — this is the
integrated tone-map form its §3 exempted. Runs on BALANCED scene-linear
camera RGB inside `render_frame`, immediately after the slot-7
scene-exposure gain — the same point the calibration arms were fitted at.

**This is a CALIBRATED TRANSLATION, not Adobe's math** (which is
closed-source Local-Laplacian-class). The anchor tables + local-scale
constants are fitted so single-slider renders land near the LR Classic
exports (`tools/cal_hlsh_fit.py` regenerates). Measured residual vs LR at
the four anchors (mean ΔE2000, block-4 grid, v3): H −50 **0.394**, H −100
**0.605**, S +50 **0.386**, S +100 **1.157** — every anchor beats the best
GLOBAL fit arm (0.549/0.865/0.472/2.269), against a ~0.2 base-look floor.
Owner-verdict axes across versions (`tools/hlsh_v2_diagnostics.py` +
`tools/hlsh_artifact_suite.py`): v1 guided = flat (retention 0.77–0.91)
+ toe patches; v2 LLF/L6 = flat fixed but POOLS/HALOS (blob interior
2.0 st, real wall glow +0.32 S / +6.5 H L*); v3 = L4 residual (the
halo-campaign winner): interior 0.57/0.64 st, glow +0.10 S / +1.9 H,
flatness 0.97–1.00 — the crop1 wall glow visually gone.
Measured signs: Highlights NEGATIVE (recovery), Shadows POSITIVE (lift) —
the owner-exported anchors. The opposite signs use the mirrored tables and
are flagged extrapolated (no anchor exports yet).

**Byte-exact identity contract:** both sliders 0 → the literal input
array is returned (the ship-gate / production contract — H/S are zero in
the production sequence).
"""

from __future__ import annotations

import numpy as np

from lrt_cinema._fast_llf import llf_apply_tone
from lrt_cinema.develop_ops import _smoothstep

# --- v2 core: fast Local-Laplacian tone application (owner round-1 verdict
# fixes, 2026-07-08). Round-1 shipped a guided base/detail split; the owner
# flips exposed two defects: (a) the affected region went FLAT (the guided
# self-filter is edge-tracking in high-variance content → base ≈ signal →
# the curve compressed per-pixel local contrast), and (b) Shadows +100 grew
# boundary artifacts at the crushed-black↔lifted interface (log-toe noise
# blowups + a binary near-black chroma gate). v2: the calibrated delta is
# applied THROUGH the fast-LLF pyramid (`_fast_llf.llf_apply_tone` — detail
# arm preserved verbatim, edges remapped at the tone map's own slope,
# absolute response at the pyramid residual's regional scale), on a
# TOE-FLOORED log channel, with a progressive chroma roll. No clarity/
# detail boost (owner: Adobe's sharpened recovered highlights look fake).

# LLF intensity-zone half-width, log2 stops: |i − context| <= sigma_r is
# DETAIL (preserved verbatim); beyond is EDGE (remapped at the curve's
# local slope). The archived proto's pin log2(2.5) ~= 1.32 stops.
_LLF_SIGMA_R = 2.0

# Fixed gamma discretization (NEVER per-frame statistics — a per-frame grid
# flickers in a timelapse). Spacing ~= sigma_r per the proto pin; the v10c
# measurement showed FINER gamma grids worsen edge overshoot, so coarse is
# correct, not merely cheap.
_LLF_GAMMA_LO = -13.5
_LLF_GAMMA_HI = 0.5
_LLF_N_GAMMA = 11

# Pyramid depth: the residual carries the ABSOLUTE calibrated tone at a
# 2**last_level px regional scale. L=4 (16 px) is the halo-campaign
# winner (owner round-2 verdicts → tools/hlsh_artifact_suite.py): vs the
# L=6 v2 baseline it halves-to-quarters every pool/halo metric (blob
# interior 2.0→0.50 st, pool 0.83→0.37, S-glow +0.32→+0.13, H-glow
# +6.5→−0.22 L* on the real crop1-class articles) at flatness 0.97.
_LLF_LAST_LEVEL = 4

# Absolute-response mode + knobs (`_fast_llf.llf_apply_tone` docstring;
# the round-2 halo campaign's arm axis — tools/hlsh_artifact_suite.py).
_LLF_ABS_MODE = "gauss"
_LLF_GUIDED_DOWN = 8
_LLF_GUIDED_RADIUS = 24
_LLF_GUIDED_EPS = 8.0
_LLF_GLOBAL_BLEND = 0.0
_LLF_GUARD_TEMP = 1.0
_LLF_GUARD_FINE_LEVEL = 0  # 0 = per-pixel; N = protect features >= 2^N px
_LLF_MULTI_LEVELS = (3, 4, 5)

# Log-toe floor (scene-linear luminance). The log2 channel is computed on
# max(lum, floor): bounds the toe against near-zero noise blowups (the
# round-1 boundary-artifact driver — log2(lum + 1e-6) swung 7+ stops on
# CFA noise) and makes the reapply ratio CONTINUOUS across the
# black-clip↔lifted boundary (pixels below the floor share their region's
# ratio instead of exploding individually). ~2^-13 of diffuse white.
_TOE_FLOOR = 1.2e-4

# Progressive near-black chroma roll: hue-preserving reapply blends to the
# achromatic pixel over a smoothstep window ending at this scene
# luminance (was a hard-ish gate at 2e-4 in round 1 — the visible chroma
# seam). LR's own deep-toe chroma is small in absolute terms there.
_CHROMA_ROLL_LUM = 6e-4

# ---------------------------------------------------------------------------
# Calibrated anchor TABLES (log2 scene domain, additive deltas on the BASE)
#
# Each table is the measured LR tone response at one owner-exported anchor
# (CALHIM50/100, CALSH50/100): the round-2 harness's best-fit global scene
# LUT delta, refined through THIS op by cal_hlsh_fit.py (the refinement
# absorbs the base-vs-pixel application bias of the local split). A
# parametric family (smoothstep windows) was tried first and REJECTED: its
# ~0.2-0.4-stop shape residual at the +/-100 anchors rendered as multi-dE
# luminance error (the tables are the honest calibration — measured shape,
# no invented form). Knot X grid is shared; deltas in log2 stops; flat
# extension outside the measured range.
#
# Between anchors the delta interpolates PER-KNOT, linearly in the slider
# value through 0 (identity at slider 0). The unmeasured opposite signs
# (positive Highlights, negative Shadows) mirror the measured curve —
# flagged EXTRAPOLATED until anchors exist.
#
# Regenerate: python3 tools/cal_hlsh_fit.py --emit-tables
# ---------------------------------------------------------------------------

_KNOT_X = np.array([-12.144, -11.7712, -11.3985, -11.0258, -10.653, -10.2802, -9.9075, -9.5347, -9.162, -8.7892, -8.4165, -8.0438, -7.671, -7.2982, -6.9255, -6.5527, -6.18, -5.8072, -5.4345, -5.0618, -4.689, -4.3162, -3.9435, -3.5708, -3.198, -2.8252, -2.4525, -2.0797, -1.707, -1.3342, -0.9615, -0.5887, -0.216])

# pinned by `python3 tools/cal_hlsh_fit.py --sweep-radius --emit-tables`
# (v3 = v2 LLF core at the halo-campaign L4 residual, 2026-07-08):
# armS LUT deltas refined through this op vs the owner LR exports;
# final validation dE 0.394/0.605/0.386/1.157 at the four anchors
# (evidence cal_hlsh_fit_v2_2026-07-08.json).
_H_D50 = np.array([0.0945, 0.0964, 0.0676, 0.02, -0.0089, -0.0121, -0.023, -0.0355, -0.0427, -0.0491, -0.0618, -0.0666, -0.0699, -0.075, -0.0788, -0.0799, -0.11, -0.1571, -0.1932, -0.4261, -0.5421, -0.5511, -0.5928, -0.6885, -0.7918, -0.8526, -0.8928, -0.9924, -1.1496, -1.3067, -1.3859, -1.3883, -1.3879])
_H_D100 = np.array([0.0828, 0.085, 0.0575, 0.0082, -0.0282, -0.0418, -0.0566, -0.0781, -0.0917, -0.1035, -0.1229, -0.1344, -0.1429, -0.1504, -0.1543, -0.1612, -0.2036, -0.2655, -0.2922, -0.721, -1.0954, -1.1026, -1.2809, -1.444, -1.6131, -1.7528, -1.7973, -2.0004, -2.2766, -2.5852, -2.7529, -2.8658, -2.9101])
_S_D50 = np.array([1.2318, 1.2619, 1.3816, 1.4791, 1.5571, 1.6198, 1.5709, 1.6114, 1.5984, 1.4873, 1.3704, 1.2245, 1.0752, 0.9108, 0.7408, 0.3597, 0.1673, 0.1469, 0.1117, 0.09, 0.0832, 0.0818, 0.0739, 0.0673, 0.0606, 0.055, 0.0513, 0.0484, 0.0301, -0.0076, -0.0706, -0.152, -0.185])
_S_D100 = np.array([2.514, 2.537, 2.7907, 3.0242, 3.101, 3.1818, 3.1482, 3.1709, 3.1604, 3.08, 2.595, 2.1076, 1.9565, 2.3356, 1.8827, 1.3392, 0.9665, 0.5937, 0.0597, 0.2387, 0.1941, 0.1684, 0.1503, 0.1369, 0.1234, 0.1109, 0.101, 0.0919, 0.0658, 0.0192, -0.0516, -0.1479, -0.187])


def _anchor_delta(base_log2: np.ndarray, slider_abs: float,
                  d50: np.ndarray, d100: np.ndarray) -> np.ndarray:
    """Per-knot linear interpolation in the slider value: 0 -> identity,
    50/100 -> the measured anchor tables, >100 clamped (slider range)."""
    a = min(slider_abs, 100.0)
    table = (d50 * (a / 50.0) if a <= 50.0
             else d50 + (d100 - d50) * ((a - 50.0) / 50.0))
    return np.interp(base_log2, _KNOT_X, table)


def _hlsh_delta(base_log2: np.ndarray, highlights: float,
                shadows: float) -> np.ndarray:
    """Additive log2-domain tone delta for the BASE layer. Measured signs:
    negative Highlights (recovery), positive Shadows (lift). The opposite
    signs mirror the measured curves (EXTRAPOLATED — no owner anchors)."""
    delta = np.zeros(base_log2.shape, dtype=np.float64)
    if highlights != 0.0:
        d = _anchor_delta(base_log2, abs(highlights), _H_D50, _H_D100)
        delta += d if highlights < 0.0 else -d
    if shadows != 0.0:
        d = _anchor_delta(base_log2, abs(shadows), _S_D50, _S_D100)
        delta += d if shadows > 0.0 else -d
    return delta


def apply_scene_hlsh(camera_rgb: np.ndarray, highlights: float,
                     shadows: float) -> np.ndarray:
    """Apply the calibrated scene-referred local Highlights/Shadows
    translation to balanced scene-linear camera RGB (H×W×3 float32).

    Returns the input array UNTOUCHED (byte-exact) when both sliders are
    zero. Otherwise returns a new float32 array; overrange (>1) survives
    (scene-referred contract — display clipping happens downstream)."""
    if highlights == 0.0 and shadows == 0.0:
        return camera_rgb

    lum = camera_rgb.mean(axis=-1)          # the ARM-S fit's luminance
    # Toe-floored log channel: bounds the deep-toe noise (round-1 boundary
    # artifact driver) and keeps the reapply ratio continuous across the
    # black-clip boundary — sub-floor pixels inherit their REGION's ratio.
    lum_f = np.maximum(lum, _TOE_FLOOR)
    log_l = np.log2(lum_f)

    def delta_fn(b: np.ndarray) -> np.ndarray:
        return _hlsh_delta(b, highlights, shadows)

    def delta_h(b: np.ndarray) -> np.ndarray:
        return _hlsh_delta(b, highlights, 0.0)

    def delta_s(b: np.ndarray) -> np.ndarray:
        return _hlsh_delta(b, 0.0, shadows)

    log_out = llf_apply_tone(
        log_l, delta_fn, _LLF_SIGMA_R, _LLF_N_GAMMA,
        _LLF_GAMMA_LO, _LLF_GAMMA_HI, _LLF_LAST_LEVEL,
        absolute_mode=_LLF_ABS_MODE, guided_down=_LLF_GUIDED_DOWN,
        guided_radius=_LLF_GUIDED_RADIUS, guided_eps=_LLF_GUIDED_EPS,
        global_blend=_LLF_GLOBAL_BLEND, delta_split=(delta_h, delta_s),
        guard_temp=_LLF_GUARD_TEMP,
        guard_fine_level=_LLF_GUARD_FINE_LEVEL or None,
        multi_levels=tuple(_LLF_MULTI_LEVELS))

    # Ratio from the FLOORED channel (bounded at the toe by construction);
    # true luminance scales by it, so the toe stays pinned near black and
    # the lifted↔clipped boundary is seamless.
    ratio = np.exp2(log_out - log_l)[..., None]
    hue_preserving = camera_rgb * ratio

    # Progressive near-black chroma roll toward the achromatic pixel of the
    # same output luminance (mean-based luminance => [lum_out]*3 exactly).
    lum_out = (lum * ratio[..., 0])
    g = _smoothstep(lum / _CHROMA_ROLL_LUM)[..., None]
    achromatic = np.broadcast_to(lum_out[..., None], hue_preserving.shape)
    out = g * hue_preserving + (1.0 - g) * achromatic
    return np.maximum(out, 0.0).astype(np.float32)
