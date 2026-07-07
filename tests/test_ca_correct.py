"""Slot-2 raw CA correction — synthetic unit contracts (CI).

The clean-room Martinec CA_correct (`lrt_cinema._ca_correct`). Contracts:
known RADIAL lateral CA (the physical magnification model the polynomial
fit is built for — note the block variance gate measures deviation from
ZERO, so a spatially-CONSTANT shift is designed-out, not a bug) is
measurably recovered on every Bayer phase; G sites are byte-identical
always; a CA-free mosaic is near-invariant; invalid inputs raise; the
pipeline CFA path routes the flag.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter, map_coordinates

from lrt_cinema._ca_correct import ca_correct_mosaic

_H = _W = 1024
_ALPHA_R = +0.0025   # R magnified: ~1.8 px radial shift at the corner
_ALPHA_B = -0.0020


def _base_field(h: int = _H, w: int = _W) -> np.ndarray:
    rng = np.random.default_rng(42)
    base = gaussian_filter(rng.random((h, w)), 3.0)
    return ((base - base.min()) / (base.max() - base.min()) * 0.85 + 0.05
            ).astype(np.float32)


def _magnified(base: np.ndarray, alpha: float) -> np.ndarray:
    """The lateral-CA model: the channel image is the scene under a
    magnification difference about the optical centre."""
    h, w = base.shape
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    return map_coordinates(
        base, [yy - alpha * (yy - cy), xx - alpha * (xx - cx)],
        order=3, mode="reflect").astype(np.float32)


def _mosaic(base: np.ndarray, r_img: np.ndarray, b_img: np.ndarray,
            pattern: str) -> np.ndarray:
    out = base.copy()
    for i, ch in enumerate(pattern):
        pr, qc = i // 2, i % 2
        if ch == "R":
            out[pr::2, qc::2] = r_img[pr::2, qc::2]
        elif ch == "B":
            out[pr::2, qc::2] = b_img[pr::2, qc::2]
    return out


def _site_mae(m: np.ndarray, truth: np.ndarray, pr: int, qc: int) -> float:
    inner = np.s_[16:-16, 16:-16]
    return float(np.abs(m[inner][pr::2, qc::2]
                        - truth[inner][pr::2, qc::2]).mean())


@pytest.mark.parametrize("pattern", ("RGGB", "BGGR", "GRBG", "GBRG"))
def test_radial_ca_recovery_measurable(pattern):
    """Known radial CA: correction must cut the R/B misalignment error vs
    the aligned scene by >= 50 % (measured ~70-76 % at these parameters)."""
    base = _base_field()
    r_img = _magnified(base, _ALPHA_R)
    b_img = _magnified(base, _ALPHA_B)
    mosaic = _mosaic(base, r_img, b_img, pattern)
    out = ca_correct_mosaic(mosaic, pattern, iterations=2)
    pars = {ch: (i // 2, i % 2) for i, ch in enumerate(pattern)}
    for ch in ("R", "B"):
        pr, qc = pars[ch]
        before = _site_mae(mosaic, base, pr, qc)
        after = _site_mae(out, base, pr, qc)
        assert after < 0.5 * before, (
            f"{pattern}/{ch}: mae {before:.5f} -> {after:.5f} "
            f"(cut {(1 - after / before) * 100:.1f} % < 50 %)")


def test_g_sites_byte_identical():
    base = _base_field()
    mosaic = _mosaic(base, _magnified(base, _ALPHA_R),
                     _magnified(base, _ALPHA_B), "RGGB")
    out = ca_correct_mosaic(mosaic, "RGGB", iterations=2)
    g_mask = np.zeros(mosaic.shape, bool)
    g_mask[0::2, 1::2] = True
    g_mask[1::2, 0::2] = True
    np.testing.assert_array_equal(out[g_mask], mosaic[g_mask])


def test_ca_free_mosaic_is_near_invariant():
    """No CA present -> measured shifts ~0 -> the guarded correction must
    leave the mosaic essentially untouched (float-level perturbation only)."""
    base = _base_field()
    out = ca_correct_mosaic(base.copy(), "RGGB", iterations=1)
    d = np.abs(out - base)
    assert float(d.max()) < 5e-3
    assert float(d.mean()) < 1e-4


def test_avoid_shift_runs_and_preserves_g():
    base = _base_field()
    mosaic = _mosaic(base, _magnified(base, _ALPHA_R),
                     _magnified(base, _ALPHA_B), "RGGB")
    out = ca_correct_mosaic(mosaic, "RGGB", iterations=2, avoid_shift=True)
    g_mask = np.zeros(mosaic.shape, bool)
    g_mask[0::2, 1::2] = True
    g_mask[1::2, 0::2] = True
    np.testing.assert_array_equal(out[g_mask], mosaic[g_mask])
    assert np.isfinite(out).all() and (out >= 0).all()


def test_validation_errors():
    good = np.zeros((64, 64), np.float32)
    with pytest.raises(ValueError, match="pattern"):
        ca_correct_mosaic(good, "XTRANS")
    with pytest.raises(ValueError, match="even-dimensioned"):
        ca_correct_mosaic(np.zeros((63, 64), np.float32), "RGGB")
    with pytest.raises(ValueError, match="even-dimensioned"):
        ca_correct_mosaic(np.zeros((4, 4, 3), np.float32), "RGGB")
    with pytest.raises(ValueError, match="iterations"):
        ca_correct_mosaic(good, "RGGB", iterations=0)
    with pytest.raises(ValueError, match="iterations"):
        ca_correct_mosaic(good, "RGGB", iterations=6)


def test_too_few_blocks_degrades_to_noop(capsys):
    """A mosaic too small for >= 10 usable blocks must warn + return the
    input unchanged (the references' processpasstwo abort), not crash."""
    base = _base_field(128, 128)
    out = ca_correct_mosaic(base.copy(), "RGGB", iterations=2)
    np.testing.assert_array_equal(out, base)


def test_resolve_ca_correct_default_on_display_off_master():
    """Owner-approved 2026-07-07 default policy: AUTO = 2 iterations on
    display presets, 0 on the scene-linear tap-7 master; explicit wins."""
    from lrt_cinema.cli import STAGE_7_PRESETS, resolve_ca_correct

    assert resolve_ca_correct(None, "lrtimelapse") == 2
    for preset in STAGE_7_PRESETS:
        assert resolve_ca_correct(None, preset) == 0
    assert resolve_ca_correct(0, "lrtimelapse") == 0
    assert resolve_ca_correct(3, next(iter(STAGE_7_PRESETS))) == 3


class _FakeRaw:
    """Minimal rawpy stand-in for the pipeline CFA path (RGGB, zero black,
    16383 white)."""

    def __init__(self, mosaic16: np.ndarray):
        h, w = mosaic16.shape
        self.raw_image_visible = mosaic16
        colors = np.empty((h, w), np.uint8)
        colors[0::2, 0::2] = 0
        colors[0::2, 1::2] = 1
        colors[1::2, 0::2] = 3
        colors[1::2, 1::2] = 2
        self.raw_colors_visible = colors
        self.black_level_per_channel = [0, 0, 0, 0]
        self.white_level = 16383
        self.raw_pattern = np.array([[0, 1], [3, 2]], np.uint8)
        self.color_desc = b"RGBG"


def test_pipeline_cfa_path_routes_ca_correct():
    """`_cfa_demosaic(..., ca_correct=N)` runs the correction between the
    WB conditioning and the demosaic; ca_correct=0 stays byte-identical."""
    from lrt_cinema.pipeline import _cfa_demosaic

    base = _base_field(512, 512)
    mosaic = _mosaic(base, _magnified(base, 0.004), _magnified(base, -0.004),
                     "RGGB")
    raw = _FakeRaw((mosaic * 16383.0 + 0.5).astype(np.uint16))
    wb = np.array([1.0, 1.0, 1.0], np.float32)
    off = _cfa_demosaic(raw, "rcd", wb, "clip", ca_correct=0)
    off2 = _cfa_demosaic(raw, "rcd", wb, "clip")
    np.testing.assert_array_equal(off, off2)
    on = _cfa_demosaic(raw, "rcd", wb, "clip", ca_correct=2)
    assert on.shape == off.shape
    assert float(np.abs(on - off).max()) > 1e-4, "CA correction did not engage"
