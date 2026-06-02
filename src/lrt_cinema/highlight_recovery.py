"""Raw highlight reconstruction — a camera-RGB pre-stage (post-demosaic,
pre-white-balance) that recovers blown highlights libraw's hard clip discards.

WHY THIS EXISTS
---------------
The demosaic (`pipeline.demosaic_camera_rgb`) runs libraw with
`HighlightMode.Clip`: every channel is hard-clamped at the raw white level
(libraw normalises WhiteLevel → 1.0 under unit WB, so the clip point is a
**uniform 1.0 across channels in camera space**, *before* the asymmetric
Stage-2 white-balance multiply). A blown window therefore renders ~4 % dark and
warm/magenta instead of clean bright white, because the clamped camera value
``[1, 1, 1]`` becomes ``[1, 1, 1]·wb_mul`` = a coloured cast after WB.

Adobe Camera Raw (the look we round-trip through LRTimelapse) reconstructs
partially-clipped highlights from the channels that still carry signal; the open
DNG SDK does no reconstruction at all. This module closes that gap with the
**Tier 1** half of a tiered hybrid (research: deep-research pass, 20 sources):
cross-channel **ratio propagation**.

THE LOAD-BEARING INVARIANT (prevents magenta)
---------------------------------------------
Reconstruct in **camera space, before WB**, where the per-channel clip point is
uniform (1.0). The surviving channels anchor the result; the WB multiply applies
afterwards, unchanged — so the reconstructed highlight inherits the correct
WB-aware asymmetry for free. A fully-blown pixel (no surviving channel) is set
**proportional to AsShotNeutral**, NOT to camera ``[1, 1, 1]``: ``cam ∝ ASN``
maps to a neutral ``[1, 1, 1]`` *after* WB, whereas camera ``[1, 1, 1]`` maps to
the warm cast we are trying to kill. This is RawTherapee's in-engine
"Color"/Color-Propagation contract: WB-agnostic data, WB-aware clip points.

TIER STRUCTURE (Tier 2 plugs in here)
-------------------------------------
`reconstruct_highlights` is a strict **no-op (byte-identical) when no channel
clips**. When clips exist it runs `_tier1_ratio_propagation`, which returns the
recovered field **and** a boolean ``tier2_mask`` of pixels it could not anchor
(fully-blown interiors with no surviving channel and no recoverable neighbour) —
those get a safe neutral interim now and are the hand-off for the gradient-domain
Poisson **Tier 2** (Phase 2). Clip-mask, insertion point and validation harness
are all reused by Tier 2.

GATING
------
Off by default at the `pipeline.render_frame` level (so every existing caller —
incl. the gym/rose ΔE ship gate, whose gym frame *is* the clipped window frame —
stays byte-identical with zero audit), on by default at the CLI/preset layer for
production fidelity. In clipped regions this stage **intentionally diverges from
`dng_validate`** (which clips, doing no reconstruction): we match the ACR/LRT
reality, not Adobe's reference clip. See docs/DECISIONS.md §"Highlight recovery".
"""

from __future__ import annotations

import numpy as np

from lrt_cinema.develop_ops import _box_sum

# libraw normalises the raw WhiteLevel to 1.0 (under unit WB, output_bps=16);
# clip the threshold 1 % below it so demosaic-interpolation-softened clips (a
# saturated photosite bilinearly mixed with an unsaturated neighbour lands just
# under 1.0) are caught, while genuine non-clipped highlights (which would need
# to sit within ~155 ADU of the 15520 white level) are the only false-positives
# — and those are near-white anyway, so a ratio-consistent lift is harmless.
# Measured stable on DSC_4053: any-channel ≥0.99 = 0.478 % of pixels vs the CFA
# ground truth `raw ≥ white_level` = 0.386 % (the demosaic spreads clips to
# neighbours → the threshold mask is a natural superset = free dilation, no
# false-negatives on real clips). Uniform across channels = WB-agnostic.
DEFAULT_CLIP_LEVEL = 0.99

# Box radius for the local channel-ratio estimate. Large enough that a clipped
# pixel at the fringe of a blown region reaches unclipped same-channel
# neighbours; deep interiors (no unclipped neighbour in the window) fall through
# to the neutral interim / Tier 2 by construction. Tunable; not fidelity-claimed.
DEFAULT_RADIUS = 8

_EPS = 1e-6


def clip_mask(
    camera_rgb: np.ndarray, clip_level: float = DEFAULT_CLIP_LEVEL,
) -> np.ndarray:
    """Per-channel boolean clip mask on demosaiced camera RGB.

    ``mask[..., c]`` is True where channel ``c`` is at/above the camera-space
    saturation point (``clip_level``, uniform across channels — see
    `DEFAULT_CLIP_LEVEL`). Shape ``(H, W, 3)``. Swappable: Tier 2 may pass a
    CFA-derived mask instead, but the algorithm only needs this contract.
    """
    return camera_rgb >= clip_level


def reconstruct_highlights(
    camera_rgb: np.ndarray,
    as_shot_neutral: np.ndarray,
    *,
    clip_level: float = DEFAULT_CLIP_LEVEL,
    radius: int = DEFAULT_RADIUS,
    enable: bool = True,
) -> np.ndarray:
    """Tier 1 highlight reconstruction on linear camera RGB (pre-white-balance).

    `camera_rgb`: float (H, W, 3), demosaiced linear camera RGB in [0, 1+],
    black-subtracted, NO white balance applied yet.
    `as_shot_neutral`: the (3,) camera-neutral vector Stage 2 will divide by —
    pass the SAME one `apply_adobe_pipeline` receives (incl. any Holy-Grail
    kelvin override), so the neutral interim lands neutral *after* WB.

    Returns a new array with clipped channels reconstructed; unclipped channels
    are byte-identical to the input. **Strict no-op**: when ``enable`` is False
    or no channel clips, the input array is returned unchanged (same object) —
    so unclipped content is byte-identical and the ΔE ship gate is unmoved.

    Reconstructed (clipped) channels may exceed 1.0 (recovered over-white
    headroom) — that is the point; the WB / matrix / tone stages carry it and
    the display encoder clips to the delivery gamut. Output is finite and ≥ 0.
    """
    if not enable:
        return camera_rgb
    clip = clip_mask(camera_rgb, clip_level)
    if not clip.any():
        return camera_rgb  # STRICT byte-identical no-op (gate-safe)
    out, _tier2_mask = _tier1_ratio_propagation(
        camera_rgb, clip, as_shot_neutral, clip_level=clip_level, radius=radius,
    )
    return out


def _local_valid_mean(
    cam: np.ndarray, valid: np.ndarray, radius: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Per-channel local mean over UNCLIPPED same-channel neighbours.

    Returns ``(mean, has_est)`` both ``(H, W, 3)``: ``mean[..., c]`` is the
    box-window (radius `radius`) average of channel ``c`` taken only over pixels
    where channel ``c`` is unclipped; ``has_est[..., c]`` is True where at least
    one such neighbour exists (i.e. the mean is defined). Where no neighbour
    exists the mean is 0 and ``has_est`` is False (caller routes to a fallback).
    Float64 box sums (matching develop_ops), result cast back to the cam dtype.
    """
    h, w, _ = cam.shape
    mean = np.zeros((h, w, 3), dtype=np.float64)
    has_est = np.zeros((h, w, 3), dtype=bool)
    for c in range(3):
        v = valid[..., c].astype(np.float64)
        cnt = _box_sum(v, radius)
        ssum = _box_sum(cam[..., c].astype(np.float64) * v, radius)
        ok = cnt > 0.5
        has_est[..., c] = ok
        np.divide(ssum, cnt, out=mean[..., c], where=ok)
    return mean, has_est


def _anchored_brightness(
    num: np.ndarray, den: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Per-pixel scalar ``s = num/den`` with a finite/positive guard.

    Returns ``(s, ok)``: ``s`` is the ratio where ``den > _EPS`` (else 0), and
    ``ok`` flags those finite, usable cells. No divide-by-zero warnings (masked
    `np.divide`).
    """
    ok = den > _EPS
    s = np.zeros_like(den)
    np.divide(num, den, out=s, where=ok)
    return s, ok


def _tier1_ratio_propagation(
    camera_rgb: np.ndarray,
    clip: np.ndarray,
    as_shot_neutral: np.ndarray,
    *,
    clip_level: float,
    radius: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Cross-channel ratio propagation (the Tier 1 algorithm).

    For every clipped channel, restore the LOCAL channel ratio anchored by the
    channels that still carry signal:

      * local mean ``m_c`` = average of channel ``c`` over nearby UNCLIPPED
        same-channel pixels (`_local_valid_mean`);
      * per-pixel brightness ``s = Σ_survivors cam / Σ_survivors m`` — how bright
        this pixel is vs its neighbourhood, measured only on surviving channels
        that have a local estimate;
      * reconstruct clipped channel ``c`` ← ``max(cam_c, s · m_c)`` (never
        *decrease* a channel that was, by definition, high). This handles 1- and
        2-channel clips uniformly (≥1 survivor anchors the rest).

    Fallbacks, in order, for clipped channels Tier 1 can't anchor locally:

      * **ASN-neutral from survivors** — if the pixel still has ≥1 surviving
        channel but no usable local ratio, reconstruct the clipped channel along
        the AsShotNeutral direction anchored by the survivors
        (``s_asn = Σ_surv cam / Σ_surv ASN``; clipped_c ← ``max(cam_c, s_asn·ASN_c)``).
        WB-aware, magenta-free (post-WB neutral), the right "fade toward neutral".
      * **Fully blown** (no surviving channel) — set the whole pixel
        ``∝ ASN · clip_level`` (post-WB neutral at the clip level). These pixels
        form ``tier2_mask``: a safe interim now, the gradient-domain Tier 2's job.

    Returns ``(out, tier2_mask)``. ``out`` is float32 (H, W, 3), finite, ≥ 0,
    byte-identical to the input on unclipped channels. ``tier2_mask`` is
    ``(H, W)`` bool marking the fully-blown pixels handed to Tier 2.
    """
    cam = camera_rgb.astype(np.float32, copy=False)
    asn = np.asarray(as_shot_neutral, dtype=np.float32).reshape(3)
    valid = ~clip  # surviving (unclipped) channels, per pixel

    mean, has_est = _local_valid_mean(cam, valid, radius)

    # --- primary: local-ratio brightness from survivors with a local estimate
    surv_ok = valid & has_est
    num_loc = np.sum(cam * surv_ok, axis=-1)
    den_loc = np.sum(mean * surv_ok, axis=-1)
    s_loc, s_loc_ok = _anchored_brightness(num_loc, den_loc)
    recon_loc = s_loc[..., None] * mean  # (H, W, 3)

    # --- fallback: ASN-neutral brightness from ALL surviving channels
    asn_b = asn[None, None, :]
    num_asn = np.sum(cam * valid, axis=-1)
    den_asn = np.sum(asn_b * valid, axis=-1)
    s_asn, s_asn_ok = _anchored_brightness(num_asn, den_asn)
    recon_asn = s_asn[..., None] * asn_b

    # --- interim: fully-blown pixels → neutral at the clip level
    recon_zero = asn_b * float(clip_level)

    out = cam.copy()

    # 1) local ratio where the channel is clipped and the anchor is usable
    use_loc = clip & has_est & (s_loc_ok & (s_loc > 0.0))[..., None]
    out = np.where(use_loc, np.maximum(cam, recon_loc), out).astype(np.float32)

    # 2) ASN-neutral fallback: clipped, not locally anchored, but ≥1 survivor
    use_asn = clip & ~use_loc & s_asn_ok[..., None]
    out = np.where(use_asn, np.maximum(cam, recon_asn), out).astype(np.float32)

    # 3) fully-blown interim: clipped with no usable anchor at all → neutral
    use_zero = clip & ~use_loc & ~use_asn
    out = np.where(use_zero, recon_zero.astype(np.float32), out).astype(np.float32)

    # Guards: finite, non-negative. (cam ≥ 0, means ≥ 0, ratios ≥ 0 → out ≥ 0;
    # clip ≥ 0 enforced defensively so a NaN/neg can never reach the WB stage.)
    np.clip(out, 0.0, None, out=out)
    if not np.all(np.isfinite(out)):  # pragma: no cover - guard, should not fire
        out = np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)

    # Tier-2 hand-off: every pixel Tier 1 left under-recovered — the fully-blown
    # neutral-interim pixels (use_zero) AND partial clips whose ratio estimate
    # fell short so `max(cam, ·)` kept the clip (e.g. large blown-window interiors
    # the box couldn't reach unclipped neighbours for). A clipped channel counts
    # as recovered only if it was lifted ABOVE its clipped value; otherwise it is
    # still effectively at the clip and is the gradient-domain Tier 2's job.
    not_lifted = clip & (out <= cam + _EPS)
    tier2_mask = (use_zero | not_lifted).any(axis=-1)
    return out, tier2_mask
