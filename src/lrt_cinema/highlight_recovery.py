"""Highlight reconstruction — a balanced-camera-RGB pre-stage (post-demosaic)
that recovers blown highlights the at-mosaic clip discards.

WHY THIS EXISTS
---------------
The decode applies WB ONCE at the mosaic (TARGET slot 3) and, on the default
"clip" path, clamps at the common white — blown regions land NEUTRAL but lose
the >1-multiplier channels' top highlight detail (dcraw's own documented
trade). Adobe Camera Raw (the look we round-trip through LRTimelapse)
reconstructs partially-clipped highlights from the channels that still carry
signal. This module closes that gap with the **Tier 1** half of a tiered
hybrid: cross-channel **ratio propagation**, fed by the "headroom" decode
path (no common-white clamp) on masters.

THE LOAD-BEARING INVARIANTS
---------------------------
1. **Balanced space, neutral = [1, 1, 1].** Input is the balanced camera RGB
   the decode now emits — a fully-blown pixel reconstructs to the NEUTRAL
   direction ``[1, 1, 1]`` by construction (the old unbalanced-space code
   had to aim along AsShotNeutral to land neutral after Stage 2; that stage
   is gone — CLAIMS "fringe forensics" recorded the domain-error class this
   convention kills).
2. **Clip detection from the MOSAIC, not the values.** The fringe forensics
   proved the post-demosaic 0.99 value threshold structurally misses
   interpolation-smeared partial clips; `pipeline._mosaic_clip_mask`
   (sensor-saturation sites, per-channel, 2-dilated) is the production mask
   and is passed in by `render_frame`. The value threshold remains only as
   the fallback when no mask is available (previews, direct library calls).

TIER STRUCTURE (Tier 2 / slot-5b plugs in here)
-----------------------------------------------
`reconstruct_highlights` is a strict **no-op (byte-identical) when nothing
clips**. When clips exist it runs `_tier1_ratio_propagation`, which returns
the recovered field **and** a boolean ``tier2_mask`` of pixels it could not
anchor — those get a safe neutral interim now and are the hand-off for
gradient-domain / opposed reconstruction (slot 5b experiment).

GATING
------
Off by default at the `pipeline.render_frame` level (so every existing caller —
incl. the gym/rose ΔE ship gate, whose gym frame *is* the clipped window frame —
stays byte-identical with zero audit), on by default at the CLI/preset layer for
production fidelity. In clipped regions this stage **intentionally diverges from
`dng_validate`** (which clips, doing no reconstruction): we match the ACR/LRT
reality, not Adobe's reference clip. See docs/archive/DECISIONS.md §"Highlight recovery".
"""

from __future__ import annotations

import numpy as np

from lrt_cinema.develop_ops import _box_sum

# Fallback value threshold (used ONLY when no mosaic mask is supplied).
# In balanced space the default "clip" decode plateaus at min(wb_mul) — the
# G-normalised minimum is 1.0 on every real WB measured — and the "headroom"
# decode leaves blown channels at wb_mul[c] ≥ 1. A ≥0.99 threshold therefore
# catches both, plus interpolation-softened near-clips (a saturated photosite
# mixed with an unsaturated neighbour lands just under the plateau). False
# positives are near-white by construction; a ratio-consistent lift there is
# harmless. The mosaic mask (sensor truth) supersedes this wherever available.
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
    """FALLBACK per-channel boolean clip mask from pixel values.

    ``mask[..., c]`` is True where channel ``c`` is at/above ``clip_level``
    (see `DEFAULT_CLIP_LEVEL`). Shape ``(H, W, 3)``. Production passes the
    mosaic-derived mask (`pipeline._mosaic_clip_mask`) instead — sensor
    truth; this value threshold misses interpolation-smeared partial clips
    (CLAIMS "Fringe forensics verdict").
    """
    return camera_rgb >= clip_level


def reconstruct_highlights(
    camera_rgb: np.ndarray,
    clip: np.ndarray | None = None,
    *,
    clip_level: float = DEFAULT_CLIP_LEVEL,
    radius: int = DEFAULT_RADIUS,
    enable: bool = True,
) -> np.ndarray:
    """Tier 1 highlight reconstruction on BALANCED linear camera RGB.

    `camera_rgb`: float (H, W, 3), demosaiced BALANCED camera RGB in [0, 1+]
    (WB applied at the mosaic — the decode's output contract, TARGET slot 3).
    `clip`: optional (H, W, 3) boolean clip mask — pass the mosaic-derived
    mask (`pipeline._mosaic_clip_mask`) for production; None falls back to
    the `clip_level` value threshold.

    Returns a new array with clipped channels reconstructed; unclipped
    channels are byte-identical to the input. **Strict no-op**: when
    ``enable`` is False or nothing clips, the input array is returned
    unchanged (same object) — so unclipped content is byte-identical and the
    ΔE ship gate is unmoved.

    Reconstructed (clipped) channels may exceed 1.0 (recovered over-white
    headroom) — that is the point; the matrix / tone stages carry it and the
    display encoder clips to the delivery gamut. Output is finite and ≥ 0.
    """
    if not enable:
        return camera_rgb
    if clip is None:
        clip = clip_mask(camera_rgb, clip_level)
    if not clip.any():
        return camera_rgb  # STRICT byte-identical no-op (gate-safe)
    out, _tier2_mask = _tier1_ratio_propagation(
        camera_rgb, clip, clip_level=clip_level, radius=radius,
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
    *,
    clip_level: float,
    radius: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Cross-channel ratio propagation (the Tier 1 algorithm), balanced space.

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

    Fallbacks, in order, for clipped channels Tier 1 can't anchor locally
    (input is BALANCED, so the neutral direction is ``[1, 1, 1]``):

      * **Neutral from survivors** — if the pixel still has ≥1 surviving
        channel but no usable local ratio, reconstruct the clipped channel at
        the survivors' mean brightness (``s = Σ_surv cam / #surv``;
        clipped_c ← ``max(cam_c, s)``) — the balanced-space "fade toward
        neutral".
      * **Fully blown** (no surviving channel) — set the whole pixel to
        neutral ``clip_level`` ([1,1,1]·level). These pixels form
        ``tier2_mask``: a safe interim now, the slot-5b reconstruction's job.

    Returns ``(out, tier2_mask)``. ``out`` is float32 (H, W, 3), finite, ≥ 0,
    byte-identical to the input on unclipped channels. ``tier2_mask`` is
    ``(H, W)`` bool marking the fully-blown pixels handed to Tier 2.
    """
    cam = camera_rgb.astype(np.float32, copy=False)
    neutral = np.ones(3, dtype=np.float32)
    valid = ~clip  # surviving (unclipped) channels, per pixel

    mean, has_est = _local_valid_mean(cam, valid, radius)

    # --- primary: local-ratio brightness from survivors with a local estimate
    surv_ok = valid & has_est
    num_loc = np.sum(cam * surv_ok, axis=-1)
    den_loc = np.sum(mean * surv_ok, axis=-1)
    s_loc, s_loc_ok = _anchored_brightness(num_loc, den_loc)
    recon_loc = s_loc[..., None] * mean  # (H, W, 3)

    # --- fallback: neutral-direction brightness from ALL surviving channels
    asn_b = neutral[None, None, :]
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
