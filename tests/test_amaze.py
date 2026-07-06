"""Clean-room AMaZE demosaic — synthetic unit contracts (CI).

Validation of record for the port is external (canonical dt-AMaZE anchor +
pressure-suite arms: diagbars 34.2→15.6 raw, 7.2 with fc-suppress 3 —
CLAIMS "AMaZE port"). These tests pin the algorithm's structural
contracts so refactors can't silently break them.
"""

from __future__ import annotations

import numpy as np
import pytest

from lrt_cinema._amaze_demosaic import amaze_demosaic


def _rggb_mosaic(scene: np.ndarray) -> np.ndarray:
    """Mosaic an (H, W, 3) scene onto an RGGB grid."""
    h, w, _ = scene.shape
    chan = np.empty((h, w), np.int64)
    chan[0::2, 0::2] = 0
    chan[0::2, 1::2] = 1
    chan[1::2, 0::2] = 1
    chan[1::2, 1::2] = 2
    return np.take_along_axis(
        scene, chan[..., None], axis=-1)[..., 0].astype(np.float32)


def test_flat_neutral_field_is_exact():
    cfa = np.full((64, 64), 0.4, dtype=np.float32)
    out = amaze_demosaic(cfa, "RGGB")
    assert out.shape == (64, 64, 3)
    np.testing.assert_allclose(out, 0.4, atol=1e-6)


def test_neutral_step_edge_invents_no_chroma():
    """The algorithm's namesake property: a neutral luma edge must not
    produce colour (interior scored; borders excluded)."""
    cfa = np.full((64, 64), 0.1, dtype=np.float32)
    cfa[:, 32:] = 0.8
    out = amaze_demosaic(cfa, "RGGB")
    inner = out[8:-8, 8:-8]
    chroma = (np.abs(inner[..., 0] - inner[..., 1])
              + np.abs(inner[..., 2] - inner[..., 1]))
    assert chroma.max() < 1e-3, f"invented chroma {chroma.max():.5f}"


def test_diagonal_edge_low_chroma():
    """The port's reason to exist: diagonal luma structure with far less
    invented colour than axis-only interpolators produce."""
    yy, xx = np.mgrid[0:96, 0:96]
    scene_v = np.where(((yy + xx) // 6) % 2 == 0, 0.7, 0.1).astype(np.float32)
    cfa = _rggb_mosaic(np.repeat(scene_v[..., None], 3, axis=-1))
    out = amaze_demosaic(cfa, "RGGB")
    inner = out[12:-12, 12:-12]
    chroma = (np.abs(inner[..., 0] - inner[..., 1])
              + np.abs(inner[..., 2] - inner[..., 1]))
    # loose bound: mean invented chroma stays well under the bar height
    assert chroma.mean() < 0.05, f"diagonal chroma mean {chroma.mean():.4f}"


def test_all_phases_agree_on_phase_shifted_input():
    """The four Bayer phases are one flip apart: demosaicing a shifted
    mosaic with the matching phase string must give the shifted result."""
    rng = np.random.default_rng(7)
    scene = rng.random((66, 66, 3)).astype(np.float32) * 0.8
    # smooth the scene so interpolation is well-posed
    from scipy.ndimage import uniform_filter
    for c in range(3):
        scene[..., c] = uniform_filter(scene[..., c], size=5)
    cfa = _rggb_mosaic(scene)
    a = amaze_demosaic(cfa[0:64, 0:64], "RGGB")
    b = amaze_demosaic(cfa[0:64, 1:65], "GRBG")
    # interiors describe the same scene, offset by one column
    np.testing.assert_allclose(a[10:-10, 11:-9], b[10:-10, 10:-10], atol=2e-2)


def test_native_sites_pass_through():
    """Each site's own channel survives demosaicing (G exactly; R/B up to
    the G-refinement the algorithm applies at its own sites)."""
    rng = np.random.default_rng(3)
    scene = rng.random((64, 64, 3)).astype(np.float32) * 0.5 + 0.2
    from scipy.ndimage import uniform_filter
    for c in range(3):
        scene[..., c] = uniform_filter(scene[..., c], size=7)
    cfa = _rggb_mosaic(scene)
    out = amaze_demosaic(cfa, "RGGB")
    yy, xx = np.mgrid[0:64, 0:64]
    g = ((yy + xx) % 2) == 1
    inner = np.zeros_like(g)
    inner[8:-8, 8:-8] = True
    np.testing.assert_allclose(out[..., 1][g & inner], cfa[g & inner], atol=1e-5)


def test_output_bounds_and_validation_errors():
    rng = np.random.default_rng(1)
    cfa = rng.random((32, 32)).astype(np.float32)
    out = amaze_demosaic(cfa, "RGGB")
    assert np.isfinite(out).all()
    assert out.min() >= 0.0 and out.max() <= 1.0
    with pytest.raises(ValueError, match="pattern"):
        amaze_demosaic(cfa, "XTRANS")
    with pytest.raises(ValueError, match="even"):
        amaze_demosaic(cfa[:31], "RGGB")
    with pytest.raises(ValueError, match="2-D"):
        amaze_demosaic(np.zeros((4, 4, 3), np.float32), "RGGB")
