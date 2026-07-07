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
the four anchors (mean ΔE2000, block-4 grid, v5): H −50 **0.352**, H −100
**0.527**, S +50 **0.343**, S +100 **0.866** — every anchor beats the best
GLOBAL fit arm (0.549/0.865/0.472/2.269), against a ~0.2 base-look floor.
Owner-verdict axes across versions (`tools/hlsh_v2_diagnostics.py` +
`tools/hlsh_artifact_suite.py`): v1 guided = flat + toe patches; v2
LLF/L6 = flat fixed but POOLS/HALOS (wall glow +0.32 S / +6.5 H L*);
v3 = L4 residual: pools fixed but mid-band (32–128 px) contrast fell to
0.67–0.76 ("flattens and blurs", owner round-3); v4 = gate2
two-scale map: averaged metrics green but the owner's round-4 audit
caught the Goodhart hack — per-pixel P95 (5.2 vs v2's 4.2 L*) and the
seam swing (2.55 st) confirmed the double-halo his eyes saw; v5 = the
BILATERAL-GRID TONE DRIVER (mode `bilat`): out = log_l + Δ(M), ONE
edge-aware map (spatial σ 96 px, range σ 1.8 st) — the range kernel
separates regions by VALUE, so structure below 1.8 st keeps its
contrast (Δ constant across it), boundaries stay sharp with monotone
transitions (no seams, no oscillating halos: swing 0.00), blobs read
their own luminance (interior 0.006 st). All floors simultaneously +
the best anchors of any architecture. dt tone-eq's architecture with a
bilateral grid (HDRNet-style slicing) as the mask producer.
Measured signs: Highlights NEGATIVE (recovery), Shadows POSITIVE (lift) —
the owner-exported anchors. The opposite signs use the mirrored tables and
are flagged extrapolated (no anchor exports yet).

**Byte-exact identity contract:** both sliders 0 → the literal input
array is returned (the ship-gate / production contract — H/S are zero in
the production sequence).
"""

from __future__ import annotations

import numpy as np

from lrt_cinema._fast_llf import bilateral_grid_map, llf_apply_tone
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
_LLF_ABS_MODE = "bilat"
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

# "paris" mode: per-side edge-compression anchors — (|slider|, beta).
# beta < 1 compresses that side's inter-region edge amplitude across ALL
# pyramid levels (Shadows: dark-below-context rises; Highlights:
# bright-above-context descends); interp linearly through beta=1 at 0.
# THE structural/locality knob, fit to LR's measured context dependence.
_BETA_LO_ANCHORS = ((50.0, 0.85), (100.0, 0.70))   # Shadows side
_BETA_HI_ANCHORS = ((50.0, 0.85), (100.0, 0.70))   # Highlights side

# "bilat" mode: bilateral-grid tone driver — M = edge-aware smooth map
# (spatial sigma px, range sigma stops), out = log_l + delta(M). The
# range kernel is the edge-awareness: structure smaller than the range
# sigma is averaged out of M (folds keep their contrast — delta constant
# across them), boundaries larger stay sharp (no pools, no gate seams).
_BILAT_SIGMA_S = 96.0
_BILAT_SIGMA_RANGE = 1.8

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
# (v5 = bilateral-grid tone driver, 2026-07-08): armS LUT deltas
# refined through this op vs the owner LR exports; final validation
# dE 0.352/0.527/0.343/0.866 at the four anchors — the best anchor
# set of any architecture (evidence cal_hlsh_fit_v2_2026-07-08.json).
_H_D50 = np.array([0.1351, 0.1365, 0.1025, 0.0349, -0.0083, -0.0123, -0.0216, -0.0347, -0.0424, -0.0484, -0.0623, -0.0674, -0.0711, -0.076, -0.0729, -0.0639, -0.0993, -0.156, -0.1485, -0.3642, -0.5009, -0.5526, -0.6036, -0.6918, -0.7953, -0.863, -0.9032, -1.0105, -1.1763, -1.3443, -1.4417, -1.3985, -1.3769])
_H_D100 = np.array([0.1258, 0.1279, 0.0986, 0.0268, -0.0271, -0.0428, -0.0546, -0.077, -0.0911, -0.1023, -0.1238, -0.135, -0.1452, -0.1546, -0.149, -0.1354, -0.1939, -0.3098, -0.3874, -0.6927, -1.0498, -1.1397, -1.3044, -1.4315, -1.6018, -1.7784, -1.8669, -2.1098, -2.3734, -2.6435, -2.8157, -2.9106, -2.9466])
_S_D50 = np.array([1.0176, 1.0539, 1.2421, 1.4241, 1.556, 1.6257, 1.5719, 1.6075, 1.5997, 1.5029, 1.3835, 1.2299, 1.022, 0.7917, 0.805, 0.4345, 0.2007, 0.1374, 0.1276, 0.1044, 0.0904, 0.0821, 0.0728, 0.0672, 0.0606, 0.0537, 0.0484, 0.0455, 0.0247, -0.0222, -0.1085, -0.2065, -0.246])
_S_D100 = np.array([2.3204, 2.3509, 2.6866, 2.9909, 3.1042, 3.1823, 3.1496, 3.1728, 3.1707, 3.1047, 2.6105, 2.1546, 1.7287, 1.8603, 1.6838, 1.3392, 0.9665, 0.2161, 0.1015, 0.2028, 0.1911, 0.1623, 0.1462, 0.1353, 0.1225, 0.1087, 0.0951, 0.0823, 0.0541, 0.0006, -0.0961, -0.2114, -0.258])


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

    def _beta(slider_abs: float,
              anchors: tuple[tuple[float, float], ...]) -> float:
        xs = [0.0] + [a for a, _ in anchors]
        ys = [1.0] + [v for _, v in anchors]
        return float(np.interp(min(slider_abs, 100.0), xs, ys))

    beta_lo = _beta(abs(shadows), _BETA_LO_ANCHORS) if shadows > 0 else 1.0
    beta_hi = (_beta(abs(highlights), _BETA_HI_ANCHORS)
               if highlights < 0 else 1.0)

    if _LLF_ABS_MODE == "bilat":
        # ONE-MAP architecture (owner round-4: fundamental fix, no
        # patch-stack): out = log_l + delta(M), M = bilateral-grid map.
        # No pyramid at all — detail and every structure below the range
        # sigma is untouched by construction (delta constant across
        # them); boundaries above it stay sharp in M (no pools/seams).
        m = bilateral_grid_map(log_l, _BILAT_SIGMA_S, _BILAT_SIGMA_RANGE)
        log_out = log_l + _hlsh_delta(m, highlights,
                                      shadows).astype(np.float32)
        return _reapply(camera_rgb, lum, log_l, log_out)

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
        gate_hi=_LLF_GATE_HI, beta_lo=beta_lo, beta_hi=beta_hi)
    if _LLF_ABS_MODE == "paris":
        # the ABSOLUTE calibrated component: a POINTWISE finisher on the
        # collapsed output (the Paris §5.3 display-renorm slot) — no
        # spatial structure, so it cannot pool, halo, or blur. The beta
        # pass above carries ALL the local behaviour (owner round-4:
        # no patch-on-patch; one coherent operator).
        log_out = log_out + _hlsh_delta(log_out, highlights,
                                        shadows).astype(np.float32)
    return _reapply(camera_rgb, lum, log_l, log_out)


def _reapply(camera_rgb: np.ndarray, lum: np.ndarray, log_l: np.ndarray,
             log_out: np.ndarray) -> np.ndarray:
    """Hue-preserving luminance reapply with the toe-stable ratio and the
    progressive near-black chroma roll (shared by all op modes)."""
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
