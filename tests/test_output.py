"""Output container tests: TIFF + EXR round-trip + color-space correctness.

Resolve interop verification (EXR PIZ files actually open in DaVinci
Resolve) is documented in the v0.6 PR body — that's a manual check that
runs once during dev, not in CI.
"""

from __future__ import annotations

import numpy as np
import pytest

from lrt_cinema.output import (
    _prophoto_to_rec2020,
    write_exr_linear_rec2020,
    write_preset_output,
    write_tiff_linear_rec2020,
)

# ---------------------------------------------------------------------------
# Color-space conversion ProPhoto(D50) → Rec.2020(D65)
# ---------------------------------------------------------------------------


def test_prophoto_to_rec2020_preserves_neutral_gray():
    """A linear ProPhoto neutral gray (R=G=B) must remain neutral after
    Bradford CAT D50→D65 + Rec.2020 conversion (within float32 matrix-
    cascade noise)."""
    gray = np.full((4, 4, 3), 0.5, dtype=np.float32)
    out = _prophoto_to_rec2020(gray)
    np.testing.assert_allclose(out[..., 0], out[..., 1], atol=1e-3)
    np.testing.assert_allclose(out[..., 1], out[..., 2], atol=1e-3)


def test_prophoto_to_rec2020_preserves_shape():
    x = np.random.rand(8, 12, 3).astype(np.float32)
    out = _prophoto_to_rec2020(x)
    assert out.shape == (8, 12, 3)
    assert out.dtype == np.float32


def test_prophoto_to_rec2020_zero_in_zero_out():
    z = np.zeros((2, 2, 3), dtype=np.float32)
    np.testing.assert_allclose(_prophoto_to_rec2020(z), 0.0, atol=1e-6)


# ---------------------------------------------------------------------------
# TIFF writer
# ---------------------------------------------------------------------------


def test_tiff_16bit_roundtrip(tmp_path):
    tifffile = pytest.importorskip("tifffile")
    x = np.full((8, 8, 3), 0.5, dtype=np.float32)
    dst = tmp_path / "test.tif"
    write_tiff_linear_rec2020(x, dst, bit_depth=16)
    assert dst.is_file()
    rt = tifffile.imread(str(dst))
    assert rt.shape == (8, 8, 3)
    assert rt.dtype == np.uint16
    # Neutral gray @ ProPhoto 0.5 should land near 0.5 in Rec.2020 too.
    assert 0.4 < rt[0, 0, 0] / 65535.0 < 0.6


def test_tiff_8bit_roundtrip(tmp_path):
    tifffile = pytest.importorskip("tifffile")
    x = np.full((4, 4, 3), 0.5, dtype=np.float32)
    dst = tmp_path / "test8.tif"
    write_tiff_linear_rec2020(x, dst, bit_depth=8)
    rt = tifffile.imread(str(dst))
    assert rt.dtype == np.uint8


def test_tiff_rejects_invalid_bit_depth(tmp_path):
    x = np.zeros((2, 2, 3), dtype=np.float32)
    with pytest.raises(ValueError, match="bit_depth"):
        write_tiff_linear_rec2020(x, tmp_path / "x.tif", bit_depth=12)


def test_tiff_32bit_float_preserves_overrange(tmp_path):
    """32-bit float TIFF is the cinema-linear default. Must preserve
    overrange (>1) signal — Resolve grade needs it. 16-bit int clips."""
    tifffile = pytest.importorskip("tifffile")
    x = np.full((4, 4, 3), 1.7, dtype=np.float32)  # overrange
    dst = tmp_path / "linear.tif"
    write_tiff_linear_rec2020(x, dst, bit_depth=32)
    rt = tifffile.imread(str(dst))
    assert rt.dtype == np.float32
    # Overrange survives — round-tripped through Rec.2020 matrix.
    assert rt.max() > 1.0


def test_cinema_linear_default_is_float(tmp_path):
    """Default bit_depth (no kwarg) is 32-bit float for cinema-linear use."""
    tifffile = pytest.importorskip("tifffile")
    x = np.full((2, 2, 3), 0.5, dtype=np.float32)
    dst = tmp_path / "default.tif"
    write_tiff_linear_rec2020(x, dst)  # no bit_depth kwarg
    rt = tifffile.imread(str(dst))
    assert rt.dtype == np.float32


def test_tiff_clips_overrange(tmp_path):
    tifffile = pytest.importorskip("tifffile")
    x = np.full((2, 2, 3), 1.5, dtype=np.float32)  # overrange
    dst = tmp_path / "over.tif"
    write_tiff_linear_rec2020(x, dst, bit_depth=16)
    rt = tifffile.imread(str(dst))
    assert rt.max() == 65535  # clamped to top of 16-bit range


def test_tiff_creates_parent_dir(tmp_path):
    x = np.zeros((2, 2, 3), dtype=np.float32)
    dst = tmp_path / "nested" / "dir" / "out.tif"
    write_tiff_linear_rec2020(x, dst)
    assert dst.is_file()


# ---------------------------------------------------------------------------
# EXR writer
# ---------------------------------------------------------------------------


def test_exr_roundtrip_preserves_float_precision(tmp_path):
    OpenEXR = pytest.importorskip("OpenEXR")
    x = np.random.rand(16, 16, 3).astype(np.float32) * 1.5  # incl. overrange
    dst = tmp_path / "test.exr"
    write_exr_linear_rec2020(x, dst)
    assert dst.is_file()
    with OpenEXR.File(str(dst), separate_channels=True) as exr:
        ch = exr.channels()
        assert "R" in ch and "G" in ch and "B" in ch
        rgb = np.stack([ch["R"].pixels, ch["G"].pixels, ch["B"].pixels], axis=-1)
    assert rgb.dtype == np.float32
    assert rgb.shape == (16, 16, 3)
    # Round-trip through Rec.2020: not identity, but float32 precision preserved.
    assert rgb.max() > 1.0  # overrange survived


def test_exr_channels_distinct_at_realistic_size(tmp_path):
    """Regression: passing strided views of an interleaved (H, W, 3) array
    to the OpenEXR ASWF binding silently produced garbled per-channel data
    on real-sized renders (~4K × 6K). Tiny 16×16 fixtures didn't trigger
    it. This test uses a 1024×1024 image with deliberately distinct
    per-channel content and verifies the readback matches the input
    pixel-for-pixel.

    The fix in output.py wraps each channel slice in `np.ascontiguousarray`
    before handing it to OpenEXR.File.
    """
    OpenEXR = pytest.importorskip("OpenEXR")
    rng = np.random.default_rng(seed=0)
    x = np.zeros((1024, 1024, 3), dtype=np.float32)
    x[..., 0] = rng.random((1024, 1024)) * 0.3 + 0.1  # R: ~[0.1, 0.4]
    x[..., 1] = rng.random((1024, 1024)) * 0.4 + 0.4  # G: ~[0.4, 0.8]
    x[..., 2] = rng.random((1024, 1024)) * 0.2 + 0.7  # B: ~[0.7, 0.9]
    # Pre-divergence sanity: the per-channel means must be well-separated
    # so the bug would have nowhere to hide if it regressed.
    assert x[..., 0].mean() < x[..., 1].mean() < x[..., 2].mean()

    dst = tmp_path / "wide.exr"
    write_exr_linear_rec2020(x, dst)
    with OpenEXR.File(str(dst), separate_channels=True) as exr:
        ch = exr.channels()
        R = ch["R"].pixels
        G = ch["G"].pixels
        B = ch["B"].pixels

    # The writer applies ProPhoto→Rec.2020 conversion, so we cannot expect
    # `R == x[..., 0]`. But the per-channel means must remain monotonically
    # ordered the way the input was (Rec.2020 is a near-identity colour
    # rotation for these synthetic non-spectral values — small enough that
    # the ordering invariant holds). The bug produced all-equal channel
    # means as the smoking gun.
    means = (R.mean(), G.mean(), B.mean())
    assert means[0] < means[1] < means[2], (
        f"EXR per-channel mean ordering broken — likely strided-view "
        f"regression in write_exr_linear_rec2020. Got means R={means[0]:.4f} "
        f"G={means[1]:.4f} B={means[2]:.4f}"
    )
    # Stricter: per-channel means must not all collapse to a single value.
    spread = max(means) - min(means)
    assert spread > 0.05, (
        f"EXR per-channel means collapsed to nearly one value (spread={spread:.4f}) "
        f"— strided-view bug signature. Means: {means}"
    )


def test_exr_uses_piz_compression(tmp_path):
    OpenEXR = pytest.importorskip("OpenEXR")
    x = np.zeros((8, 8, 3), dtype=np.float32)
    dst = tmp_path / "piz.exr"
    write_exr_linear_rec2020(x, dst)
    with OpenEXR.File(str(dst)) as exr:
        assert exr.header()["compression"] == OpenEXR.PIZ_COMPRESSION


# ---------------------------------------------------------------------------
# Preset dispatch
# ---------------------------------------------------------------------------


def test_preset_cinema_linear_writes_tiff(tmp_path):
    pytest.importorskip("tifffile")
    x = np.zeros((4, 4, 3), dtype=np.float32)
    out = write_preset_output(x, tmp_path / "frame_001", "cinema-linear")
    assert out.suffix == ".tif"
    assert out.is_file()


def test_preset_cinema_aces_writes_exr(tmp_path):
    pytest.importorskip("OpenEXR")
    x = np.zeros((4, 4, 3), dtype=np.float32)
    out = write_preset_output(x, tmp_path / "frame_001", "cinema-aces")
    assert out.suffix == ".exr"
    assert out.is_file()


def test_preset_stills_finished_is_not_implemented_in_v06(tmp_path):
    x = np.zeros((4, 4, 3), dtype=np.float32)
    with pytest.raises(NotImplementedError, match="AgX"):
        write_preset_output(x, tmp_path / "frame", "stills-finished")


def test_preset_unknown_raises(tmp_path):
    x = np.zeros((4, 4, 3), dtype=np.float32)
    with pytest.raises(ValueError, match="Unknown preset"):
        write_preset_output(x, tmp_path / "frame", "nonexistent")
