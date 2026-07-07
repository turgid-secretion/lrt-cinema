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

So this op: guided-filter base/detail split on log2 scene luminance (the
He–Sun–Tang machinery `develop_ops._guided_base_log`, the repo's measured
low-halo base producer), a calibrated slider-driven tone curve applied to
the BASE only (detail reinserted at unity — the local part), hue-preserving
ratio reapply with a near-black chroma roll (the Shadows +100 scene-lift
chroma explosion measured on the armS fit is exactly the failure this
guard prevents). Runs on BALANCED scene-linear camera RGB inside
`render_frame`, immediately after the slot-7 scene-exposure gain — the
same point the calibration arms were fitted at.

**This is a CALIBRATED TRANSLATION, not Adobe's math** (which is
closed-source Local-Laplacian-class). The anchor tables + local-scale
constants are fitted so single-slider renders land near the LR Classic
exports (`tools/cal_hlsh_fit.py` regenerates). Measured residual vs LR at
the four anchors (mean ΔE2000, block-4 grid): H −50 **0.355**, H −100
**0.497**, S +50 **0.346**, S +100 **0.980** — every anchor beats the best
GLOBAL fit arm (0.549/0.865/0.472/2.269), against a ~0.2 base-look floor.
Measured signs: Highlights NEGATIVE (recovery), Shadows POSITIVE (lift) —
the owner-exported anchors. The opposite signs use the mirrored tables and
are flagged extrapolated (no anchor exports yet).

**Byte-exact identity contract:** both sliders 0 → the literal input
array is returned (the ship-gate / production contract — H/S are zero in
the production sequence).
"""

from __future__ import annotations

import numpy as np

from lrt_cinema.develop_ops import _guided_base_log, _smoothstep

# Log-luminance eps: keeps log2 finite on true zeros. Same role as
# develop_ops._LOG_EPS; scene camera RGB is typically ~1e-4..1 linear.
_EPS = 1e-6

# Guided-filter base/detail split (He–Sun–Tang 2013) on log2 scene
# luminance. Radius in px at native res — the LOCAL adaptation scale the
# context-correlation fingerprint demands (calibrated: see
# tools/cal_hlsh_fit.py sweep; production frames are ~4000 px tall).
_HLSH_RADIUS = 192
_HLSH_GUIDED_EPS = 1.0

# Near-black chroma roll floor (scene-linear luminance). Below this the
# hue-preserving ratio blends to the achromatic pixel of the same output
# luminance — prevents a big Shadows lift from amplifying a degenerate
# near-black channel imbalance into a false cast (the measured armS
# Shadows+100 failure mode). Same construction as
# develop_ops._NEARBLACK_LUM_FLOOR but in SCENE units (pre-tone-curve):
# 2e-4 ≈ a deep shadow ~12 stops below diffuse white on the D750 frames.
_NEARBLACK_SCENE_FLOOR = 2e-4

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
# (2026-07-07): armS LUT deltas refined through this op vs the owner LR
# exports; final validation dE 0.355/0.497/0.346/0.980 at the four
# anchors (evidence cal_hlsh_fit_2026-07-07.json).
_H_D50 = np.array([-0.0029, 0.0028, 0.0156, 0.002, -0.0088, -0.0116, -0.023, -0.0354, -0.0428, -0.0495, -0.0617, -0.0676, -0.0727, -0.081, -0.0931, -0.0832, -0.1095, -0.1628, -0.2008, -0.4019, -0.5158, -0.5492, -0.5912, -0.6829, -0.7868, -0.8557, -0.9136, -1.0274, -1.1671, -1.2877, -1.3456, -1.3587, -1.3639])
_H_D100 = np.array([-0.0147, -0.0087, 0.0055, -0.0093, -0.0277, -0.0416, -0.0571, -0.0783, -0.0919, -0.1044, -0.123, -0.1355, -0.1479, -0.1628, -0.1807, -0.1669, -0.1972, -0.2659, -0.3146, -0.6791, -1.0561, -1.1226, -1.2876, -1.4445, -1.6169, -1.7726, -1.8542, -2.1067, -2.3438, -2.5611, -2.6897, -2.8013, -2.8465])
_S_D50 = np.array([1.1197, 1.1545, 1.3252, 1.4556, 1.5515, 1.6197, 1.5696, 1.6114, 1.597, 1.482, 1.3699, 1.2281, 1.0726, 0.8958, 0.7389, 0.3932, 0.1949, 0.1368, 0.1253, 0.1013, 0.0884, 0.0823, 0.074, 0.0675, 0.0607, 0.0542, 0.0489, 0.0412, 0.0207, -0.0138, -0.0631, -0.1263, -0.1516])
_S_D100 = np.array([2.4012, 2.4271, 2.7121, 2.9895, 3.0984, 3.1809, 3.1463, 3.1675, 3.1522, 3.0665, 2.5909, 2.1261, 2.0327, 2.615, 1.6838, 1.3392, 0.9665, 0.5937, 0.1347, 0.2657, 0.1905, 0.1623, 0.1479, 0.1356, 0.1224, 0.1095, 0.0972, 0.0815, 0.054, 0.0131, -0.0433, -0.116, -0.145])


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
    log_l = np.log2(np.maximum(lum, 0.0) + _EPS)

    r = (min(_HLSH_RADIUS, (min(log_l.shape) - 1) // 2)
         if log_l.ndim == 2 else 0)
    base = _guided_base_log(log_l, r, _HLSH_GUIDED_EPS)
    detail = log_l - base

    base_out = base + _hlsh_delta(base, highlights, shadows)
    lum_out = np.maximum(np.exp2(base_out + detail) - _EPS, 0.0)

    # Hue-preserving ratio reapply with the near-black chroma roll (scene
    # units). Above the floor: literal rgb * lum_out/lum. Below: blend to
    # the achromatic pixel with the same output luminance (mean-based
    # luminance => achromatic = [lum_out]*3 exactly).
    ratio = (lum_out / np.maximum(lum, _EPS))[..., None]
    hue_preserving = camera_rgb * ratio
    g = _smoothstep(lum / _NEARBLACK_SCENE_FLOOR)[..., None]
    achromatic = np.broadcast_to(lum_out[..., None], hue_preserving.shape)
    out = g * hue_preserving + (1.0 - g) * achromatic
    return np.maximum(out, 0.0).astype(np.float32)
