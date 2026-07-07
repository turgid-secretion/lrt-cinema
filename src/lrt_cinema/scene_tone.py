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
the four anchors (mean ΔE2000, block-4 grid, v2): H −50 **0.375**, H −100
**0.581**, S +50 **0.376**, S +100 **1.051** — every anchor beats the best
GLOBAL fit arm (0.549/0.865/0.472/2.269), against a ~0.2 base-look floor.
On the owner-verdict axes the v2 core measures: local-contrast retention
vs LR in the Highlights-affected bins 0.95–1.15 (v1 guided: 0.77–0.91 =
the "flat look"), toe-boundary band chroma 17.04 vs LR 17.14 (v1: 18.61 =
the false-colour patches) — `tools/hlsh_v2_diagnostics.py`.
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
# 2**last_level px regional scale (the round-2 locality fingerprint's
# scale; swept by tools/cal_hlsh_fit.py).
_LLF_LAST_LEVEL = 6

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
# (v2 LLF core, 2026-07-08): armS LUT deltas refined through this op vs
# the owner LR exports; final validation dE 0.375/0.581/0.376/1.051 at
# the four anchors (evidence cal_hlsh_fit_v2_2026-07-08.json).
_H_D50 = np.array([0.1094, 0.1112, 0.0811, 0.0252, -0.0096, -0.0127, -0.0245, -0.0364, -0.0427, -0.0485, -0.062, -0.0633, -0.0607, -0.0565, -0.0385, -0.0544, -0.1, -0.1479, -0.1492, -0.4012, -0.5362, -0.5556, -0.6073, -0.6963, -0.7977, -0.8592, -0.8868, -0.9922, -1.1695, -1.3596, -1.4837, -1.4704, -1.4612])
_H_D100 = np.array([0.0978, 0.1001, 0.0742, 0.0152, -0.0287, -0.0428, -0.0582, -0.0792, -0.0916, -0.1025, -0.123, -0.1312, -0.1301, -0.1201, -0.0938, -0.1236, -0.2017, -0.2975, -0.3406, -0.7636, -1.1258, -1.1213, -1.3002, -1.4269, -1.5842, -1.7347, -1.7826, -2.1263, -2.4314, -2.6896, -2.8642, -2.9709, -3.0122])
_S_D50 = np.array([1.1465, 1.1808, 1.3465, 1.4704, 1.5616, 1.6294, 1.593, 1.6188, 1.5982, 1.4961, 1.3879, 1.2245, 1.0585, 0.8855, 0.7083, 0.3651, 0.1828, 0.1346, 0.0655, 0.0635, 0.074, 0.0781, 0.0708, 0.0676, 0.0617, 0.0552, 0.0533, 0.0611, 0.044, -0.0047, -0.0923, -0.1933, -0.2342])
_S_D100 = np.array([2.4277, 2.4557, 2.7635, 3.0204, 3.1068, 3.1928, 3.1722, 3.1836, 3.1736, 3.1081, 2.6166, 2.1922, 1.5784, 0.9952, 2.3678, 1.3392, 0.9665, 0.2881, -0.003, 0.1256, 0.1791, 0.1746, 0.1565, 0.1452, 0.1295, 0.113, 0.1047, 0.1027, 0.0771, 0.0202, -0.0785, -0.1955, -0.2428])


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

    log_out = llf_apply_tone(
        log_l, delta_fn, _LLF_SIGMA_R, _LLF_N_GAMMA,
        _LLF_GAMMA_LO, _LLF_GAMMA_HI, _LLF_LAST_LEVEL)

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
