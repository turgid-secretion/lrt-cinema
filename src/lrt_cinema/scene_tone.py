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
the four anchors (mean ΔE2000, block-4 grid, v4): H −50 **0.370**, H −100
**0.552**, S +50 **0.381**, S +100 **1.062** — every anchor beats the best
GLOBAL fit arm (0.549/0.865/0.472/2.269), against a ~0.2 base-look floor.
Owner-verdict axes across versions (`tools/hlsh_v2_diagnostics.py` +
`tools/hlsh_artifact_suite.py`): v1 guided = flat + toe patches; v2
LLF/L6 = flat fixed but POOLS/HALOS (wall glow +0.32 S / +6.5 H L*);
v3 = L4 residual: pools fixed but mid-band (32–128 px) contrast fell to
0.67–0.76 ("flattens and blurs", owner round-3); v4 = the
AMPLITUDE-GATED TWO-SCALE map (mode `gate2`): tone follows the coarse
map where fine(8 px)/coarse(64 px) agree within 0.4 st (folds keep
contrast) and the fine map beyond 1.2 st (boundaries stay pool-free) —
real stage-crop mid-bands 0.90–0.94 of LR's, wall glow +0.00 S /
−0.06 H, flatness 1.00, all simultaneously.
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
# local slope). In the full-depth architecture (round-3) the slopes CARRY
# the tone response, so sigma_r is small (dt's mid-tone-range analogue).
_LLF_SIGMA_R = 2.0

# Fixed gamma discretization (NEVER per-frame statistics — a per-frame grid
# flickers in a timelapse). Spacing ~= sigma_r per the proto pin; the v10c
# measurement showed FINER gamma grids worsen edge overshoot, so coarse is
# correct, not merely cheap.
_LLF_GAMMA_LO = -13.5
_LLF_GAMMA_HI = 0.5
_LLF_N_GAMMA = 11

# Pyramid depth. Round-3 (owner: v3's adjusted regions "flattened and
# lack contrast… flattens and blurs" — worst on the gym stage/curtain):
# ANY single-scale absolute map trades pools (large L, round-2) against
# mid-band flattening/blur (small L, round-3) — the per-band suite
# metrics showed shipped-L4 at 0.67–0.76 of input contrast in the
# 32–128 px bands while FULL depth reads 0.97–0.99 synthetic and
# 0.95–1.07 of LR's own band contrast on the stage crop. 99 = full
# pyramid (residual ≈ global mean; Δ(residual) ≈ a constant — no
# intermediate absolute map exists to pool, blur, or flatten): the
# canonical dt/LLF architecture, where the per-γ edge-arm slopes carry
# the tone at every level (Aubry 2014: halos vanish at full depth).
_LLF_LAST_LEVEL = 6

# Absolute-response mode + knobs (`_fast_llf.llf_apply_tone` docstring;
# the round-2 halo campaign's arm axis — tools/hlsh_artifact_suite.py).
_LLF_ABS_MODE = "gate2"
_LLF_GUIDED_DOWN = 8
_LLF_GUIDED_RADIUS = 24
_LLF_GUIDED_EPS = 8.0
_LLF_GLOBAL_BLEND = 0.0
_LLF_GUARD_TEMP = 1.0
_LLF_GUARD_FINE_LEVEL = 0  # 0 = per-pixel; N = protect features >= 2^N px
_LLF_MULTI_LEVELS = (3, 4, 5)
_LLF_GATE_FINE = 3         # gate2: fine-map level (2^N px)
_LLF_GATE_LO = 0.4         # gate2: below this fine/coarse deviation (st),
_LLF_GATE_HI = 1.2         #        tone follows the coarse map; above, fine

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
# (v4 = gate2 amplitude-gated two-scale map, 2026-07-08): armS LUT
# deltas refined through this op vs the owner LR exports; final
# validation dE 0.370/0.552/0.381/1.062 at the four anchors — the
# best anchor set of any version (evidence
# cal_hlsh_fit_v2_2026-07-08.json).
_H_D50 = np.array([0.1076, 0.1094, 0.0795, 0.0251, -0.0087, -0.0124, -0.0257, -0.0367, -0.0428, -0.0497, -0.0618, -0.0683, -0.0728, -0.079, -0.0894, -0.0766, -0.1015, -0.1546, -0.1904, -0.4247, -0.5398, -0.549, -0.6032, -0.6968, -0.7975, -0.8559, -0.8886, -0.9939, -1.1595, -1.3224, -1.3971, -1.3535, -1.3327])
_H_D100 = np.array([0.0952, 0.0974, 0.0705, 0.0143, -0.0277, -0.0425, -0.0608, -0.0801, -0.092, -0.1046, -0.1232, -0.1363, -0.1476, -0.1592, -0.1724, -0.1536, -0.1876, -0.2655, -0.3085, -0.732, -1.0883, -1.0798, -1.2852, -1.4367, -1.601, -1.7483, -1.8078, -2.098, -2.367, -2.6095, -2.7727, -2.8493, -2.8775])
_S_D50 = np.array([1.1505, 1.1841, 1.3417, 1.4648, 1.5571, 1.6233, 1.5673, 1.6078, 1.5965, 1.4885, 1.3835, 1.2262, 1.0452, 0.8557, 0.7801, 0.4144, 0.1955, 0.1337, 0.1069, 0.0911, 0.0859, 0.0826, 0.073, 0.0668, 0.0606, 0.0548, 0.05, 0.0496, 0.0315, -0.0108, -0.0865, -0.1638, -0.1945])
_S_D100 = np.array([2.4274, 2.4542, 2.7489, 3.0116, 3.1023, 3.1817, 3.1467, 3.1703, 3.1656, 3.095, 2.6105, 2.1736, 1.782, 1.9314, 1.6838, 1.3392, 0.9665, 0.4239, 0.1077, 0.212, 0.2016, 0.1654, 0.1465, 0.1348, 0.1226, 0.1105, 0.0991, 0.0883, 0.0635, 0.0158, -0.0709, -0.1603, -0.1956])


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
        multi_levels=tuple(_LLF_MULTI_LEVELS),
        gate_fine=_LLF_GATE_FINE, gate_lo=_LLF_GATE_LO,
        gate_hi=_LLF_GATE_HI)

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
