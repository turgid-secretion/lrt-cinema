"""Clean-room "opposed" highlight reconstruction — both placements.

THE ALGORITHM (one algorithm, two engine-shipped placements)
------------------------------------------------------------
The current best-regarded simple reconstruction in the open-source canon:
darktable's default `highlights` mode ("inpaint opposed", enum 5) — which
RawTherapee 5.9+ also ships, vendored from dt, as its "Inpaint opposed"
method. darktable runs it PRE-demosaic on the WB'd Bayer mosaic; RT runs the
same maths POST-demosaic on the RGB planes. That split is exactly the
slot-5b deciding experiment (docs/REFERENCE_PIPELINE.md §TARGET v2):
`reconstruct_mosaic_opposed` is the dt placement, `reconstruct_rgb_opposed`
the RT placement, and the algorithm is held constant so the experiment
isolates PLACEMENT.

Per pixel with a clipped channel c:
  1. **Opposed estimate.** Take the 3×3 neighbourhood's per-channel means
     (on the mosaic: means over each channel's CFA sites in the window; on
     RGB: plain 3×3 box means). Cube-root the means; the opposed estimate
     for channel c is the mean of the OTHER TWO channels' cube-rooted means,
     cubed back to linear. (Cube-root ≈ a robust, luma-like compression that
     keeps the estimate stable across magnitudes — both engines use exponent
     3.0.)
  2. **Global chrominance offset.** Over UNCLIPPED pixels very close to
     clipped regions (value in (0.2·clip, clip), inside a dilated clip
     mask), accumulate (value − opposed_estimate) per channel; the mean is a
     global per-channel offset that corrects the estimate's colour cast.
     Falls back to 0 with fewer than `MIN_CHROMA_SAMPLES` samples.
  3. **Reconstruct.** Clipped channel ← max(value, estimate + offset) —
     never decrease a channel that was, by definition, at/above clip.

This file is a CLEAN-ROOM implementation from the algorithm description
extracted during the 2026-06-11 source pass (read-to-learn; no GPL code
copied — anti-drift rule 6). Engine references, file/line citations and the
deciding experiment: docs/REFERENCE_PIPELINE.md TARGET slot 5b; CLAIMS
"Local source pass COMPLETE".

DOMAIN CONTRACT (slot 3): all inputs are BALANCED — the mosaic is WB-scaled
(the decode's pre-demosaic conditioning), RGB is balanced camera RGB. Clip
levels are therefore per-channel `wb_mul[c] × sensor_white(=1.0)`: in
"headroom" decode mode each channel saturates at its own multiplier.
"""

from __future__ import annotations

import numpy as np

# Fraction of the clip level a near-clip pixel must exceed to contribute to
# the global chrominance estimate (both engines: 0.2).
_LO_CLIP_FRAC = 0.2
# Clip-detection headroom factor on the saturation level (dt's "magic" for
# the opposed mode is 0.995 — sites within 0.5 % of saturation count).
_CLIP_MAGIC = 0.995
# Minimum near-clip samples for a usable per-channel chrominance offset
# (dt: 100 for the Bayer path, 30 for RGB; we keep the conservative 100).
MIN_CHROMA_SAMPLES = 100
# Dilation (pixels) of the clip mask defining the near-clip annulus.
_ANNULUS_DILATE = 3


def _box_mean_masked(values: np.ndarray, mask: np.ndarray, size: int = 3,
                     ) -> tuple[np.ndarray, np.ndarray]:
    """Windowed mean of `values` over True `mask` cells; (mean, count>0)."""
    from scipy.ndimage import uniform_filter

    m = mask.astype(np.float32)
    s = uniform_filter(values.astype(np.float32) * m, size=size, mode="nearest")
    c = uniform_filter(m, size=size, mode="nearest")
    ok = c > 1e-6
    mean = np.zeros_like(s)
    np.divide(s, c, out=mean, where=ok)
    return mean, ok


def _opposed_from_channel_means(means: list[np.ndarray]) -> list[np.ndarray]:
    """Cube-root opposed estimates per channel from per-channel mean maps."""
    cr = [np.cbrt(np.maximum(m, 0.0)) for m in means]
    return [
        (0.5 * (cr[1] + cr[2])) ** 3,
        (0.5 * (cr[0] + cr[2])) ** 3,
        (0.5 * (cr[0] + cr[1])) ** 3,
    ]


def reconstruct_mosaic_opposed(
    cfa: np.ndarray, chan: np.ndarray, wb_mul: np.ndarray,
    clip_magic: float = float(_CLIP_MAGIC),
) -> np.ndarray:
    """darktable-placement opposed reconstruction ON the WB-scaled mosaic.

    `cfa` (H, W) float32 — the BALANCED (WB-scaled) Bayer mosaic, headroom
    preserved (no common-white clamp). `chan` (H, W) int — CFA channel index
    per site (G2 already folded to 1). `wb_mul` (3,) — the G-normalised
    multipliers; per-channel clip level is `clip_magic × wb_mul[c]`
    (default 0.995, dt's opposed magic; the segmentation arm passes its
    own 0.987 — dt hands the segments clip to its opposed base layer).
    Returns the mosaic with clipped sites reconstructed (float32, ≥ 0);
    unclipped sites byte-identical. Runs BEFORE the demosaic, so the
    interpolator sees plausible (non-plateau) highlight structure.
    """
    from scipy.ndimage import binary_dilation

    clips = (np.float32(clip_magic)
             * np.asarray(wb_mul, np.float32)).astype(np.float32)
    site_clip = clips[chan]
    clipped = cfa >= site_clip
    if not clipped.any():
        return cfa

    # Per-channel 3×3-window means over each channel's own CFA sites.
    means = []
    for c in range(3):
        mean_c, _ = _box_mean_masked(cfa, chan == c, size=3)
        means.append(mean_c)
    opposed = _opposed_from_channel_means(means)
    ref = np.choose(chan, opposed)  # opposed estimate for each site's channel

    # Global chrominance: unclipped near-clip annulus per channel.
    annulus = binary_dilation(clipped, iterations=_ANNULUS_DILATE) & ~clipped
    chrominance = np.zeros(3, dtype=np.float32)
    for c in range(3):
        sel = annulus & (chan == c) & (cfa > _LO_CLIP_FRAC * clips[c])
        n = int(sel.sum())
        if n >= MIN_CHROMA_SAMPLES:
            chrominance[c] = float((cfa[sel] - ref[sel]).mean())

    out = cfa.copy()
    rec = np.maximum(cfa, ref + chrominance[chan])
    out[clipped] = rec[clipped]
    return np.maximum(out, 0.0).astype(np.float32, copy=False)


def reconstruct_mosaic_neutral(
    cfa: np.ndarray, chan: np.ndarray, wb_mul: np.ndarray,
    clipped: np.ndarray | None = None,
) -> np.ndarray:
    """LUMINANCE-LED NEUTRAL recovery — the survey shortlist #1 (2026-06-12).

    Adobe's own documentation describes ACR's recovery as returning
    surviving-channel data as LUMINANCE, not colour (docs/HL_RECON_SURVEY.md
    [PRIMARY]); the owner's rank-1 verdicts select for exactly that (clean
    neutral windows, no invented colour). This variant keeps the opposed
    machinery's PROVEN luminance estimate (truth harness: −49…−64 %
    recovery error) but makes the reconstruction target CHANNEL-CONSISTENT:

      per LOCATION, target T = cube( mean of cube-rooted 3×3 means of the
      locally UNCLIPPED channels );   every clipped channel ← max(value, T)

    One T per location ⇒ reconstructed values carry no channel disparity ⇒
    the demosaic sees locally neutral structure with recovered brightness.
    Compared to `reconstruct_mosaic_opposed`: the per-channel global
    chrominance offset (the tint-preserving term the owner's eyes rejected)
    is REMOVED, and the estimate uses unclipped channels only. Fully-blown
    interiors (no unclipped channel in the window) stay at the plateau —
    uniform, hence neutral, by construction; the partial-clip skirt carries
    the recovered luminance rolloff.

    CALL CONVENTION (the v1→v2 lesson, hl_neutral evidence): pass the
    COMMON-WHITE-CLAMPED mosaic as `cfa` and the pre-clamp per-channel clip
    mask as `clipped` — clamp first, THEN lift. v1 ran on the unclamped
    mosaic and the untouched channel-disparate headroom sites reproduced
    the clipbars falsecolor explosion (17.2) that the 5a clamp exists to
    prevent. Clamp-then-lift keeps every 5a guarantee and adds only
    channel-consistent luminance above the plateau. `clipped=None` falls
    back to value-threshold detection on `cfa` (headroom inputs only).
    """
    clips = (_CLIP_MAGIC * np.asarray(wb_mul, np.float32)).astype(np.float32)
    if clipped is None:
        clipped = cfa >= clips[chan]
    if not clipped.any():
        return cfa

    # Per-channel 3×3 means over UNCLIPPED sites of that channel only.
    croot_sum = np.zeros(cfa.shape, dtype=np.float32)
    croot_cnt = np.zeros(cfa.shape, dtype=np.float32)
    for c in range(3):
        mean_c, ok = _box_mean_masked(cfa, (chan == c) & ~clipped, size=3)
        croot_sum += np.where(ok, np.cbrt(np.maximum(mean_c, 0.0)), 0.0)
        croot_cnt += ok.astype(np.float32)
    have = croot_cnt > 0
    target = np.zeros(cfa.shape, dtype=np.float32)
    np.divide(croot_sum, croot_cnt, out=target, where=have)
    target = target ** 3

    out = cfa.copy()
    rec = np.where(have, np.maximum(cfa, target), cfa)
    out[clipped] = rec[clipped]
    return np.maximum(out, 0.0).astype(np.float32, copy=False)


def reconstruct_rgb_opposed(
    rgb: np.ndarray, clip_mask: np.ndarray, wb_mul: np.ndarray,
) -> np.ndarray:
    """RT-placement opposed reconstruction on demosaiced BALANCED RGB.

    `rgb` (H, W, 3) float32 balanced camera RGB, headroom preserved (the
    demosaic ran on the unclamped scaled mosaic, so blown channels sit at
    channel-disparate plateaus). `clip_mask` (H, W, 3) bool — the
    MOSAIC-derived per-channel clip mask (`pipeline._mosaic_clip_mask`):
    sensor truth, catches interpolation-smeared partial clips the value
    threshold misses (the fringe-forensics lesson — this is the one
    deliberate improvement over RT, which thresholds values). `wb_mul` as in
    the mosaic variant. Returns reconstructed RGB; unmasked channels
    byte-identical.
    """
    from scipy.ndimage import binary_dilation

    if not clip_mask.any():
        return rgb

    clips = (_CLIP_MAGIC * np.asarray(wb_mul, np.float32)).astype(np.float32)
    means = []
    for c in range(3):
        # Means over UNCLIPPED pixels of channel c (mask excludes clipped).
        mean_c, _ = _box_mean_masked(rgb[..., c], ~clip_mask[..., c], size=3)
        means.append(mean_c)
    opposed = _opposed_from_channel_means(means)

    any_clip = clip_mask.any(axis=-1)
    annulus = binary_dilation(any_clip, iterations=_ANNULUS_DILATE) & ~any_clip
    chrominance = np.zeros(3, dtype=np.float32)
    for c in range(3):
        v = rgb[..., c]
        sel = annulus & ~clip_mask[..., c] & (v > _LO_CLIP_FRAC * clips[c]) \
            & (v < clips[c])
        n = int(sel.sum())
        if n >= MIN_CHROMA_SAMPLES:
            chrominance[c] = float((v[sel] - opposed[c][sel]).mean())

    out = rgb.copy()
    for c in range(3):
        rec = np.maximum(rgb[..., c], opposed[c] + chrominance[c])
        out[..., c] = np.where(clip_mask[..., c], rec, rgb[..., c])
    return np.maximum(out, 0.0).astype(np.float32, copy=False)
