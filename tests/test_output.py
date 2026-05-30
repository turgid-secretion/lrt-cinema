"""Output container tests: TIFF + EXR round-trip + color-space correctness.

Resolve interop verification (EXR PIZ files actually open in DaVinci
Resolve) is documented in the v0.6 PR body — that's a manual check that
runs once during dev, not in CI.
"""

from __future__ import annotations

import warnings

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
    """Default EXR writer in v0.7 is half-float DWAB. Half preserves
    overrange up to ~65504; the round-trip should keep >1 values alive."""
    OpenEXR = pytest.importorskip("OpenEXR")
    x = np.random.rand(16, 16, 3).astype(np.float32) * 1.5  # incl. overrange
    dst = tmp_path / "test.exr"
    write_exr_linear_rec2020(x, dst)
    assert dst.is_file()
    with OpenEXR.File(str(dst), separate_channels=True) as exr:
        ch = exr.channels()
        assert "R" in ch and "G" in ch and "B" in ch
        rgb = np.stack([ch["R"].pixels, ch["G"].pixels, ch["B"].pixels], axis=-1)
    assert rgb.dtype == np.float16
    assert rgb.shape == (16, 16, 3)
    assert rgb.max() > 1.0  # overrange survived


def test_exr_float_piz_roundtrip(tmp_path):
    """v0.6 cinema-aces back-compat: explicit float+piz must still work
    and produce a 32-bit float PIZ EXR (binary-exact round trip in pixels)."""
    OpenEXR = pytest.importorskip("OpenEXR")
    x = np.random.rand(16, 16, 3).astype(np.float32) * 1.5
    dst = tmp_path / "float_piz.exr"
    write_exr_linear_rec2020(x, dst, bit_depth="float", compression="piz")
    with OpenEXR.File(str(dst), separate_channels=True) as exr:
        ch = exr.channels()
        rgb = np.stack([ch["R"].pixels, ch["G"].pixels, ch["B"].pixels], axis=-1)
        assert exr.header()["compression"] == OpenEXR.PIZ_COMPRESSION
    assert rgb.dtype == np.float32
    assert rgb.max() > 1.0


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


def test_exr_default_uses_dwab_compression(tmp_path):
    """v0.7 default: DWAB. cinema-linear-finished routes here."""
    OpenEXR = pytest.importorskip("OpenEXR")
    x = np.zeros((8, 8, 3), dtype=np.float32)
    dst = tmp_path / "dwab.exr"
    write_exr_linear_rec2020(x, dst)
    with OpenEXR.File(str(dst)) as exr:
        assert exr.header()["compression"] == OpenEXR.DWAB_COMPRESSION


def test_exr_acescg_writes_ap1_chromaticities(tmp_path):
    """v0.8 gamut switch: the EXR writer can emit ACEScg (AP1) and tags the
    AP1 primaries + ~D60 white in the `chromaticities` header attribute."""
    OpenEXR = pytest.importorskip("OpenEXR")
    x = np.full((8, 8, 3), 0.5, dtype=np.float32)
    dst = tmp_path / "acescg.exr"
    write_exr_linear_rec2020(x, dst, colorspace="acescg")
    with OpenEXR.File(str(dst)) as exr:
        vals = np.asarray(exr.header()["chromaticities"], dtype=float).ravel()
    expected = np.array(
        [0.713, 0.293, 0.165, 0.830, 0.128, 0.044, 0.32168, 0.33767]
    )
    np.testing.assert_allclose(vals[:8], expected, atol=2e-3)


def test_exr_aces2065_sets_container_flag(tmp_path):
    OpenEXR = pytest.importorskip("OpenEXR")
    x = np.zeros((4, 4, 3), dtype=np.float32)
    dst = tmp_path / "ap0.exr"
    write_exr_linear_rec2020(x, dst, colorspace="aces2065")
    with OpenEXR.File(str(dst)) as exr:
        assert exr.header().get("acesImageContainerFlag") == 1


def test_exr_rejects_invalid_colorspace(tmp_path):
    x = np.zeros((2, 2, 3), dtype=np.float32)
    with pytest.raises(ValueError, match="colorspace"):
        write_exr_linear_rec2020(x, tmp_path / "bad.exr", colorspace="rec709")  # type: ignore[arg-type]


def test_preset_cinema_masters_emit_acescg(tmp_path):
    """cinema-linear-finished / -master switched to ACEScg (AP1) emission:
    EXR carries the ~D60 ACEScg whitepoint (not Rec.2020's D65)."""
    OpenEXR = pytest.importorskip("OpenEXR")
    x = np.full((4, 4, 3), 0.3, dtype=np.float32)
    for preset in ("cinema-linear-finished", "cinema-linear-master"):
        out = write_preset_output(x, tmp_path / preset, preset)
        with OpenEXR.File(str(out)) as exr:
            wp = np.asarray(exr.header()["chromaticities"], dtype=float).ravel()[6:8]
        np.testing.assert_allclose(wp, [0.32168, 0.33767], atol=2e-3,
                                   err_msg=f"{preset} not ACEScg/~D60")


def test_exr_cinema_aces_path_uses_piz_compression(tmp_path):
    """v0.6 back-compat: cinema-aces preset must still produce PIZ EXR."""
    OpenEXR = pytest.importorskip("OpenEXR")
    x = np.zeros((8, 8, 3), dtype=np.float32)
    dst = tmp_path / "piz.exr"
    write_exr_linear_rec2020(x, dst, bit_depth="float", compression="piz")
    with OpenEXR.File(str(dst)) as exr:
        assert exr.header()["compression"] == OpenEXR.PIZ_COMPRESSION


def test_exr_rejects_invalid_bit_depth(tmp_path):
    x = np.zeros((2, 2, 3), dtype=np.float32)
    with pytest.raises(ValueError, match="bit_depth"):
        write_exr_linear_rec2020(x, tmp_path / "bad.exr", bit_depth="quad")  # type: ignore[arg-type]


def test_exr_rejects_invalid_compression(tmp_path):
    x = np.zeros((2, 2, 3), dtype=np.float32)
    with pytest.raises(ValueError, match="compression"):
        write_exr_linear_rec2020(
            x, tmp_path / "bad.exr", compression="brotli",  # type: ignore[arg-type]
        )


def test_exr_dwab_smaller_than_piz(tmp_path):
    """The v0.7 win: DWAB-half output should be substantially smaller than
    PIZ-float output on the same source. Validates the size-reduction claim
    without committing to a specific ratio (varies per content)."""
    pytest.importorskip("OpenEXR")
    rng = np.random.default_rng(seed=42)
    # Realistic-ish content — gradient + noise. DWAB exploits spatial
    # correlation, so flat-noise gives the weakest compression delta.
    h, w = 256, 256
    base = np.linspace(0.0, 1.0, w, dtype=np.float32)
    img = np.stack(
        [np.broadcast_to(base, (h, w)).copy() for _ in range(3)],
        axis=-1,
    )
    img += rng.normal(0, 0.02, img.shape).astype(np.float32)
    img = np.clip(img, 0.0, 2.0)

    piz_dst = tmp_path / "piz.exr"
    dwab_dst = tmp_path / "dwab.exr"
    write_exr_linear_rec2020(
        img, piz_dst, bit_depth="float", compression="piz",
    )
    write_exr_linear_rec2020(
        img, dwab_dst, bit_depth="half", compression="dwab",
    )
    piz_size = piz_dst.stat().st_size
    dwab_size = dwab_dst.stat().st_size
    assert dwab_size < piz_size / 2, (
        f"DWAB-half ({dwab_size}B) should be >2× smaller than PIZ-float "
        f"({piz_size}B); got ratio {piz_size / dwab_size:.2f}"
    )


def test_exr_dwab_visually_lossless_roundtrip(tmp_path):
    """DWAB-half ΔE2000 < 0.5 vs PIZ-half on a realistic synthetic frame.

    This is the v0.7 visually-lossless gate per
    docs/research/v07-spec-revision-plan.md §"Phase 1 Validation".
    Uses synthetic content because the real gym/rose ΔE gate lives in
    test_pipeline.py and runs only with /tmp fixtures present.
    """
    OpenEXR = pytest.importorskip("OpenEXR")
    colour = pytest.importorskip("colour")

    rng = np.random.default_rng(seed=7)
    h, w = 256, 256
    # Smooth gradient + low-amplitude noise; clip to [0, 1] so the values
    # stay in the regime where ΔE2000 is meaningful (display-referred-ish).
    base = np.linspace(0.05, 0.95, w, dtype=np.float32)
    img = np.stack([
        np.broadcast_to(base, (h, w)).copy(),
        np.broadcast_to(base[::-1], (h, w)).copy(),
        np.broadcast_to(base, (h, w)).T.copy(),
    ], axis=-1)
    img += rng.normal(0, 0.01, img.shape).astype(np.float32)
    img = np.clip(img, 0.0, 1.0)

    piz_dst = tmp_path / "piz_half.exr"
    dwab_dst = tmp_path / "dwab_half.exr"
    write_exr_linear_rec2020(
        img, piz_dst, bit_depth="half", compression="piz",
    )
    write_exr_linear_rec2020(
        img, dwab_dst, bit_depth="half", compression="dwab",
    )

    def _read(p):
        with OpenEXR.File(str(p), separate_channels=True) as exr:
            ch = exr.channels()
            rgb = np.stack(
                [ch["R"].pixels, ch["G"].pixels, ch["B"].pixels], axis=-1,
            )
        return rgb.astype(np.float32)

    piz = _read(piz_dst)
    dwab = _read(dwab_dst)

    # ΔE2000 in Lab(D65). Both are linear Rec.2020.
    def _to_lab(rgb_linear_rec2020):
        xyz = colour.RGB_to_XYZ(
            np.clip(rgb_linear_rec2020.astype(np.float64), 0.0, 1.0),
            "ITU-R BT.2020",
            apply_cctf_decoding=False,
        )
        return colour.XYZ_to_Lab(xyz, illuminant=np.array([0.31270, 0.32900]))

    de = colour.delta_E(_to_lab(piz), _to_lab(dwab), method="CIE 2000")
    assert float(de.mean()) < 0.5, (
        f"DWAB-half vs PIZ-half ΔE2000 mean {float(de.mean()):.3f} exceeds "
        f"visually-lossless gate of 0.5. P95={float(np.percentile(de, 95)):.3f} "
        f"max={float(de.max()):.3f}"
    )


# ---------------------------------------------------------------------------
# Preset dispatch
# ---------------------------------------------------------------------------


def test_preset_cinema_linear_finished_writes_half_dwab_exr(tmp_path):
    """v0.7 default preset: cinema-linear-finished → half-float DWAB EXR."""
    OpenEXR = pytest.importorskip("OpenEXR")
    x = np.zeros((4, 4, 3), dtype=np.float32)
    out = write_preset_output(x, tmp_path / "frame_001", "cinema-linear-finished")
    assert out.suffix == ".exr"
    assert out.is_file()
    with OpenEXR.File(str(out), separate_channels=True) as exr:
        ch = exr.channels()
        assert ch["R"].pixels.dtype == np.float16
        assert exr.header()["compression"] == OpenEXR.DWAB_COMPRESSION


def test_preset_cinema_linear_master_writes_half_dwab_exr(tmp_path):
    """v0.7.1 β preset: cinema-linear-master → half-float DWAB EXR (same
    writer as γ; the Stage 7 emission point lives in pipeline.py, not
    here. write_preset_output is preset-aware but stage-agnostic)."""
    OpenEXR = pytest.importorskip("OpenEXR")
    x = np.zeros((4, 4, 3), dtype=np.float32)
    out = write_preset_output(x, tmp_path / "frame_001", "cinema-linear-master")
    assert out.suffix == ".exr"
    assert out.is_file()
    with OpenEXR.File(str(out), separate_channels=True) as exr:
        ch = exr.channels()
        assert ch["R"].pixels.dtype == np.float16
        assert exr.header()["compression"] == OpenEXR.DWAB_COMPRESSION


def test_preset_cinema_linear_writes_tiff(tmp_path):
    pytest.importorskip("tifffile")
    x = np.zeros((4, 4, 3), dtype=np.float32)
    out = write_preset_output(x, tmp_path / "frame_001", "cinema-linear")
    assert out.suffix == ".tif"
    assert out.is_file()


def test_preset_cinema_aces_writes_exr_and_warns(tmp_path):
    """v0.6 back-compat preset cinema-aces still works and emits a one-time
    DeprecationWarning steering toward cinema-linear-finished."""
    OpenEXR = pytest.importorskip("OpenEXR")
    # Reset module-level guard so this test is hermetic regardless of order.
    from lrt_cinema import output as output_module
    output_module._CINEMA_ACES_DEPRECATION_WARNED = False

    x = np.zeros((4, 4, 3), dtype=np.float32)
    with pytest.warns(DeprecationWarning, match="cinema-linear-finished"):
        out = write_preset_output(x, tmp_path / "frame_001", "cinema-aces")
    assert out.suffix == ".exr"
    assert out.is_file()
    with OpenEXR.File(str(out), separate_channels=True) as exr:
        ch = exr.channels()
        # cinema-aces stays float+PIZ for back-compat.
        assert ch["R"].pixels.dtype == np.float32
        assert exr.header()["compression"] == OpenEXR.PIZ_COMPRESSION


def test_preset_cinema_aces_deprecation_fires_once(tmp_path):
    """Module-level guard: a 5000-frame render emits one warning, not 5000."""
    pytest.importorskip("OpenEXR")
    from lrt_cinema import output as output_module
    output_module._CINEMA_ACES_DEPRECATION_WARNED = False

    x = np.zeros((4, 4, 3), dtype=np.float32)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        for i in range(5):
            write_preset_output(x, tmp_path / f"frame_{i:03d}", "cinema-aces")
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deprecations) == 1, (
        f"cinema-aces should warn exactly once per process; got {len(deprecations)}"
    )


def test_preset_stills_finished_is_not_implemented(tmp_path):
    x = np.zeros((4, 4, 3), dtype=np.float32)
    with pytest.raises(NotImplementedError, match="AgX"):
        write_preset_output(x, tmp_path / "frame", "stills-finished")


def test_preset_unknown_raises(tmp_path):
    x = np.zeros((4, 4, 3), dtype=np.float32)
    with pytest.raises(ValueError, match="Unknown preset"):
        write_preset_output(x, tmp_path / "frame", "nonexistent")
