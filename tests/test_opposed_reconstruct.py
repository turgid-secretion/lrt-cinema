"""Opposed reconstruction (slot 5b candidate) — synthetic unit contracts (CI).

Algorithm contracts for `lrt_cinema._opposed_reconstruct` (clean-room
dt/RT "opposed", one algorithm, two placements):
  - strict no-op (same object) when nothing clips;
  - clipped sites/channels are lifted to ≥ their clipped value (never
    decreased);
  - a clipped channel on a NEUTRAL scene reconstructs toward the surviving
    channels' level (the opposed estimate is the mean of the other two);
  - RGB variant writes ONLY masked channels;
  - output finite, ≥ 0.
"""

from __future__ import annotations

import numpy as np

from lrt_cinema._opposed_reconstruct import (
    reconstruct_mosaic_opposed,
    reconstruct_rgb_opposed,
)

_WB = np.array([2.0, 1.0, 1.3], dtype=np.float32)  # G-normalised multipliers


def _rggb_chan(h: int, w: int) -> np.ndarray:
    """RGGB channel map (G2 folded to 1) for a synthetic mosaic."""
    chan = np.empty((h, w), dtype=np.int64)
    chan[0::2, 0::2] = 0
    chan[0::2, 1::2] = 1
    chan[1::2, 0::2] = 1
    chan[1::2, 1::2] = 2
    return chan


def _balanced_mosaic(scene_rgb: np.ndarray, chan: np.ndarray) -> np.ndarray:
    """Mosaic a (H, W, 3) BALANCED scene onto the CFA grid."""
    h, w = chan.shape
    return np.take_along_axis(
        scene_rgb[:h, :w], chan[..., None], axis=-1)[..., 0].astype(np.float32)


# --- mosaic (pre-demosaic, dt placement) --------------------------------------


def test_mosaic_no_clip_is_same_object():
    chan = _rggb_chan(32, 32)
    scene = np.full((32, 32, 3), 0.4, dtype=np.float32)
    cfa = _balanced_mosaic(scene, chan)
    assert reconstruct_mosaic_opposed(cfa, chan, _WB) is cfa


def test_mosaic_clipped_neutral_region_reconstructs_to_neighbour_level():
    """The physically-real partial clip: in BALANCED units channel c
    saturates at wb_mul[c], so G (multiplier 1.0) blows FIRST. A bright
    neutral blob at 1.15 clips G to its 1.0 plateau while R/B survive at
    1.15 — the opposed estimate for G = mean(R, B cube-root means) must
    lift the G plateau toward 1.15. The blob gets a bright SKIRT (the
    global-chrominance annulus is designed for real highlights, which roll
    off — a hard synthetic edge would feed it background instead and skew
    the offset)."""
    chan = _rggb_chan(64, 64)
    scene = np.full((64, 64, 3), 0.4, dtype=np.float32)
    scene[20:44, 20:44] = 0.9          # bright neutral skirt (below all clips)
    scene[24:40, 24:40] = 1.15         # blob core: G clips at 1.0, R/B survive
    cfa = _balanced_mosaic(scene, chan)
    # Sensor clip in balanced units: channel c saturates at wb_mul[c].
    clip_per_site = _WB[chan]
    cfa_clipped = np.minimum(cfa, clip_per_site)
    out = reconstruct_mosaic_opposed(cfa_clipped, chan, _WB)
    yy = np.arange(64)[:, None]
    xx = np.arange(64)[None, :]
    g_core = (chan == 1) & (yy >= 26) & (yy < 38) & (xx >= 26) & (xx < 38)
    # G was clamped to 1.0; R/B neighbours carry 1.15 → reconstruct toward it.
    assert out[g_core].min() >= 1.0 - 1e-6
    assert out[g_core].mean() > 1.08, (
        f"G blob not lifted: mean {out[g_core].mean():.3f}")
    # Unclipped background byte-identical.
    np.testing.assert_array_equal(out[:18], cfa_clipped[:18])


def test_mosaic_never_decreases_clipped_sites():
    chan = _rggb_chan(48, 48)
    rng = np.random.default_rng(3)
    scene = (0.3 + rng.random((48, 48, 3)) * 2.0).astype(np.float32)
    cfa = np.minimum(_balanced_mosaic(scene, chan), _WB[chan])
    out = reconstruct_mosaic_opposed(cfa, chan, _WB)
    assert (out >= cfa - 1e-6).all()
    assert np.isfinite(out).all() and (out >= 0).all()


# --- RGB (post-demosaic, RT placement) ----------------------------------------


def test_rgb_no_mask_is_same_object():
    rgb = np.full((16, 16, 3), 0.5, dtype=np.float32)
    mask = np.zeros((16, 16, 3), dtype=bool)
    assert reconstruct_rgb_opposed(rgb, mask, _WB) is rgb


def test_rgb_only_masked_channels_written():
    rng = np.random.default_rng(4)
    rgb = (0.2 + rng.random((40, 40, 3)) * 0.5).astype(np.float32)
    # Blow R in a blob: plateau at wb_mul[0]·magic-ish, mask it.
    rgb[16:24, 16:24, 0] = 2.0
    mask = np.zeros(rgb.shape, dtype=bool)
    mask[16:24, 16:24, 0] = True
    out = reconstruct_rgb_opposed(rgb, mask, _WB)
    np.testing.assert_array_equal(out[~mask], rgb[~mask])
    assert (out[mask] >= rgb[mask] - 1e-6).all()


def test_rgb_neutral_blob_reconstructs_toward_survivors():
    """Neutral bright blob, R masked at its 2.0 plateau, G/B carry 2.4:
    opposed estimate (mean of G,B) lifts R toward 2.4. Bright skirt around
    the blob for the chrominance annulus (see the mosaic twin test)."""
    rgb = np.full((64, 64, 3), 0.5, dtype=np.float32)
    rgb[20:44, 20:44] = 1.6             # neutral skirt below all clip levels
    rgb[24:40, 24:40] = 2.4
    rgb[24:40, 24:40, 0] = 2.0          # R plateaued (headroom decode)
    mask = np.zeros(rgb.shape, dtype=bool)
    mask[24:40, 24:40, 0] = True
    out = reconstruct_rgb_opposed(rgb, mask, _WB)
    blob_r = out[26:38, 26:38, 0]
    assert blob_r.mean() > 2.25, f"R not lifted toward survivors: {blob_r.mean():.3f}"
    assert np.isfinite(out).all() and (out >= 0).all()
