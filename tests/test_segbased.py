"""Clean-room segmentation-based HL reconstruction — synthetic contracts.

Validation of record is external (dt-cli canonical anchor + truth
harness + owner flips — CLAIMS "dt segmentation port"). These pin the
structural contracts: identity off-clip, candidate recovery of partial
clipping, all-clipped rebuild activation, determinism, validation.
"""

from __future__ import annotations

import numpy as np
import pytest

from lrt_cinema._segbased_reconstruct import (
    RECOVERY_MODES,
    reconstruct_mosaic_segbased,
)

WB = np.array([2.0, 1.0, 1.5], np.float32)


def _rggb_chan(h, w):
    yy, xx = np.mgrid[0:h, 0:w]
    chan = np.ones((h, w), np.int64)
    chan[(yy % 2 == 0) & (xx % 2 == 0)] = 0
    chan[(yy % 2 == 1) & (xx % 2 == 1)] = 2
    return chan


def _mosaic(scene, chan, wb):
    """Balanced headroom mosaic: per-site scene value x wb, clipped at
    the channel's balanced saturation wb[c]."""
    m = np.take_along_axis(scene, chan[..., None], -1)[..., 0]
    return np.minimum(m * wb[chan], wb[chan]).astype(np.float32)


def test_unclipped_identity():
    rng = np.random.default_rng(5)
    h, w = 120, 150
    chan = _rggb_chan(h, w)
    scene = rng.uniform(0.05, 0.5, (h, w, 3)).astype(np.float32)
    cfa = _mosaic(scene, chan, WB)
    out = reconstruct_mosaic_segbased(cfa, chan, WB)
    np.testing.assert_array_equal(out, cfa)


def test_partial_clip_candidates_recover_luminance():
    """A blown disk on a smooth field where G clips but R/B survive:
    candidates must lift the clipped G sites above the plateau, toward
    the scene, without touching unclipped sites."""
    h, w = 180, 210
    chan = _rggb_chan(h, w)
    yy, xx = np.mgrid[0:h, 0:w]
    r2 = (yy - 90.0) ** 2 + (xx - 105.0) ** 2
    lum = 0.45 + 1.2 * np.exp(-r2 / (2 * 22.0 ** 2))
    scene = np.repeat(lum[..., None].astype(np.float32), 3, -1)
    scene[..., 0] *= 0.45     # tinted: R,B stay under their clip
    scene[..., 2] *= 0.55
    cfa = _mosaic(scene, chan, WB)
    clipped = cfa >= 0.987 * WB[chan]
    assert clipped.any(), "fixture must clip"
    assert (chan[clipped] == 1).all(), "fixture: only G clips"

    out = reconstruct_mosaic_segbased(cfa, chan, WB)
    np.testing.assert_array_equal(out[~clipped], cfa[~clipped])
    lifted = out[clipped] - cfa[clipped]
    assert (lifted >= 0).all()
    assert lifted.max() > 0.05, "candidates found nothing to recover"
    # recovered values move toward the true scene: error must shrink
    truth = (scene[..., 1] * WB[1])[clipped]
    assert np.abs(out[clipped] - truth).mean() \
        < np.abs(cfa[clipped] - truth).mean()


def test_allclipped_rebuild_needs_recovery_mode():
    """Fully-blown interior: candidates alone cannot invent data there;
    the rebuild (recovery mode + strength) must add signal."""
    h, w = 240, 240
    chan = _rggb_chan(h, w)
    yy, xx = np.mgrid[0:h, 0:w]
    r2 = (yy - 120.0) ** 2 + (xx - 120.0) ** 2
    lum = 0.4 + 3.0 * np.exp(-r2 / (2 * 34.0 ** 2))
    scene = np.repeat(lum[..., None].astype(np.float32), 3, -1)
    cfa = _mosaic(scene, chan, WB)
    interior = r2 < 20.0 ** 2       # deep inside the blown core
    assert (cfa[interior] >= 0.987 * WB[chan][interior]).all()

    base = reconstruct_mosaic_segbased(cfa, chan, WB, recovery="off")
    reb = reconstruct_mosaic_segbased(cfa, chan, WB,
                                      recovery="adapt", strength=0.6)
    assert reb[interior].sum() > base[interior].sum(), \
        "rebuild added no signal in the all-clipped core"
    unclipped = cfa < 0.987 * WB[chan]
    np.testing.assert_array_equal(reb[unclipped], cfa[unclipped])


def test_deterministic():
    rng = np.random.default_rng(11)
    h, w = 150, 150
    chan = _rggb_chan(h, w)
    scene = rng.uniform(0.2, 1.4, (h, w, 3)).astype(np.float32)
    cfa = _mosaic(scene, chan, WB)
    a = reconstruct_mosaic_segbased(cfa, chan, WB,
                                    recovery="adapt", strength=0.5)
    b = reconstruct_mosaic_segbased(cfa, chan, WB,
                                    recovery="adapt", strength=0.5)
    np.testing.assert_array_equal(a, b)


def test_validation():
    cfa = np.full((40, 40), 0.3, np.float32)
    chan = _rggb_chan(40, 40)
    with pytest.raises(ValueError, match="recovery"):
        reconstruct_mosaic_segbased(cfa, chan, WB, recovery="bogus")
    assert set(RECOVERY_MODES) == {"off", "small", "large", "smallf",
                                   "largef", "adapt", "adaptf"}


def test_site_guard_zero_is_byte_identical():
    """site_guard=0 (the default) must not perturb the shipped behaviour
    — every pinned segbased evidence row stays reproducible."""
    rng = np.random.default_rng(21)
    h, w = 150, 150
    chan = _rggb_chan(h, w)
    scene = rng.uniform(0.2, 1.4, (h, w, 3)).astype(np.float32)
    cfa = _mosaic(scene, chan, WB)
    a = reconstruct_mosaic_segbased(cfa, chan, WB)
    b = reconstruct_mosaic_segbased(cfa, chan, WB, site_guard=0.0)
    np.testing.assert_array_equal(a, b)


def test_site_guard_clamps_isolated_spike_only():
    """The guard helper: an isolated clipped-site spike >> its same-channel
    ring median is CLAMPED to limit x that median (a ceiling, not a median
    replacement — replacement measurably created dark divots); smooth
    reconstructed values and unclipped (sensor) sites are untouched."""
    from lrt_cinema._segbased_reconstruct import _suppress_isolated_sites

    out = np.full((20, 20), 1.1, np.float32)   # smooth reconstructed field
    clipped = np.zeros((20, 20), bool)
    clipped[6:14, 6:14] = True
    out[10, 10] = 4.0                          # isolated invented spike
    out[2, 2] = 4.0                            # spike OUTSIDE the clip mask
    guarded, n = _suppress_isolated_sites(out, clipped, 2.0)
    assert n == 1
    assert guarded[10, 10] == np.float32(2.0 * 1.1)  # clamped to 2x median
    assert guarded[2, 2] == np.float32(4.0)          # sensor data untouched
    untouched = np.ones_like(out, bool)
    untouched[10, 10] = False
    np.testing.assert_array_equal(guarded[untouched], out[untouched])


def test_site_guard_preserves_smooth_recovery():
    """A smooth above-clip recovered ramp (legit reconstruction) must pass
    the guard unchanged — the guard only fires on ring-median OUTLIERS."""
    from lrt_cinema._segbased_reconstruct import _suppress_isolated_sites

    g = np.linspace(1.0, 1.8, 30, dtype=np.float32)
    out = np.repeat(g[None, :], 30, axis=0).copy()
    clipped = np.ones((30, 30), bool)
    guarded, n = _suppress_isolated_sites(out, clipped, 2.0)
    assert n == 0
    np.testing.assert_array_equal(guarded, out)


def test_site_guard_validation():
    cfa = np.full((40, 40), 0.3, np.float32)
    chan = _rggb_chan(40, 40)
    with pytest.raises(ValueError, match="site_guard"):
        reconstruct_mosaic_segbased(cfa, chan, WB, site_guard=0.5)
