"""Calibration storage + lookup tests (Phase 2a infrastructure)."""

from __future__ import annotations

import os
import struct
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from lrt_cinema.calibration import (
    Calibration,
    auto_detect_calibration,
    find_calibration_for_camera,
    load_calibration,
    save_calibration,
)


def _identity_calibration(label: str = "Nikon D750") -> Calibration:
    return Calibration(
        camera_label=label,
        matrix=np.eye(3, dtype=np.float32),
        tier=0,
        source="test",
    )


def test_save_load_round_trip_preserves_all_fields(tmp_path):
    cal = Calibration(
        camera_label="Nikon D750",
        matrix=np.array(
            [[1.05, -0.02, -0.01],
             [-0.01, 1.04, -0.03],
             [-0.02, -0.01, 1.10]],
            dtype=np.float32,
        ),
        tier=2,
        source="Camera Standard.dcp",
        delta_e2000_mean=1.7,
        delta_e2000_max=3.4,
    )
    out = tmp_path / "test.npz"
    save_calibration(cal, out)
    loaded = load_calibration(out)
    assert loaded.camera_label == "Nikon D750"
    assert loaded.tier == 2
    assert loaded.source == "Camera Standard.dcp"
    assert loaded.delta_e2000_mean == pytest.approx(1.7, abs=1e-5)
    assert loaded.delta_e2000_max == pytest.approx(3.4, abs=1e-5)
    np.testing.assert_allclose(loaded.matrix, cal.matrix, rtol=1e-6)


def test_save_calibration_rejects_wrong_shape_matrix(tmp_path):
    cal = Calibration(
        camera_label="Test",
        matrix=np.eye(4, dtype=np.float32),  # wrong shape
        tier=0, source="test",
    )
    with pytest.raises(ValueError, match=r"must be \(3, 3\)"):
        save_calibration(cal, tmp_path / "bad.npz")


def test_save_calibration_rejects_invalid_tier(tmp_path):
    cal = Calibration(
        camera_label="Test",
        matrix=np.eye(3, dtype=np.float32),
        tier=99, source="test",
    )
    with pytest.raises(ValueError, match="tier must be one of"):
        save_calibration(cal, tmp_path / "bad.npz")


def test_load_calibration_version_mismatch_is_actionable(tmp_path):
    bad = tmp_path / "stale.npz"
    np.savez_compressed(
        bad,
        format_version=np.int32(999),
        matrix=np.eye(3, dtype=np.float32),
        camera_label=np.array("Test", dtype="U"),
        tier=np.int32(0),
        source=np.array("test", dtype="U"),
        delta_e2000_mean=np.float32(0.0),
        delta_e2000_max=np.float32(0.0),
    )
    with pytest.raises(ValueError, match="calibrate_camera"):
        load_calibration(bad)


def test_save_load_preserves_none_camera_label_as_empty(tmp_path):
    """Defensive: None camera_label → "" round-trip (same pattern as
    dcp.save_profile per caveman-review PR #8 #7)."""
    cal = Calibration(
        camera_label=None,  # type: ignore[arg-type]
        matrix=np.eye(3, dtype=np.float32),
        tier=0, source="",
    )
    out = tmp_path / "noname.npz"
    save_calibration(cal, out)
    loaded = load_calibration(out)
    assert loaded.camera_label == ""


def test_find_calibration_for_camera_matches_adobe_label(tmp_path):
    """Filename convention is `<Adobe label>.npz` — must match the EXIF-
    derived Make/Model normalization in dcp._adobe_camera_label."""
    cal = _identity_calibration("Nikon D750")
    save_calibration(cal, tmp_path / "Nikon D750.npz")
    found = find_calibration_for_camera(
        "NIKON CORPORATION", "NIKON D750", extra_roots=[tmp_path],
    )
    assert found == tmp_path / "Nikon D750.npz"


def test_find_calibration_returns_none_on_miss(tmp_path):
    assert find_calibration_for_camera(
        "UnknownVendor", "X", extra_roots=[tmp_path],
    ) is None


def test_search_roots_honors_env_var(tmp_path):
    """`$LRT_CINEMA_CALIBRATION` is the explicit user override; takes
    precedence over the XDG default."""
    from lrt_cinema.calibration import _calibration_search_roots
    cal_dir = tmp_path / "envroot"
    cal_dir.mkdir()
    with patch.dict(os.environ, {"LRT_CINEMA_CALIBRATION": str(cal_dir)}):
        roots = _calibration_search_roots()
        assert cal_dir in roots


def _build_synthetic_nef(make: str, model: str) -> bytes:
    """Minimal TIFF/NEF with Make + Model in IFD0, for auto-detect tests."""
    make_bytes = (make + "\x00").encode("ascii")
    model_bytes = (model + "\x00").encode("ascii")
    # IFD0: 2 entries (Make=271, Model=272)
    # Each entry: tag(2) + type(2) + count(4) + value_or_offset(4) = 12 bytes
    header = b"II" + struct.pack("<H", 42) + struct.pack("<I", 8)
    n_entries = 2
    ifd_size = 2 + 12 * n_entries + 4
    big_blob_offset = 8 + ifd_size
    ifd = struct.pack("<H", n_entries)
    blob = b""
    cur_off = big_blob_offset
    # Make
    if len(make_bytes) <= 4:
        val = make_bytes + b"\x00" * (4 - len(make_bytes))
    else:
        val = struct.pack("<I", cur_off)
        blob += make_bytes
        cur_off += len(make_bytes)
    ifd += struct.pack("<HHI4s", 271, 2, len(make_bytes), val)
    # Model
    if len(model_bytes) <= 4:
        val = model_bytes + b"\x00" * (4 - len(model_bytes))
    else:
        val = struct.pack("<I", cur_off)
        blob += model_bytes
        cur_off += len(model_bytes)
    ifd += struct.pack("<HHI4s", 272, 2, len(model_bytes), val)
    ifd += struct.pack("<I", 0)  # next IFD = 0
    return header + ifd + blob


def test_auto_detect_calibration_end_to_end_with_synthetic_nef(tmp_path):
    """Plant a calibration .npz + a synthetic NEF, verify auto-detect
    walks RAW EXIF → Adobe label → calibration file."""
    # Plant the calibration.
    cal = _identity_calibration("Nikon D750")
    save_calibration(cal, tmp_path / "Nikon D750.npz")
    # Plant a synthetic NEF that EXIF-decodes to NIKON CORPORATION + NIKON D750.
    nef = tmp_path / "sample.NEF"
    nef.write_bytes(_build_synthetic_nef("NIKON CORPORATION", "NIKON D750"))
    # Auto-detect (extra_roots pointed at our temp dir).
    result = auto_detect_calibration(nef, extra_roots=[tmp_path])
    assert result is not None
    loaded, src = result
    assert src == tmp_path / "Nikon D750.npz"
    assert loaded.camera_label == "Nikon D750"
    np.testing.assert_allclose(loaded.matrix, np.eye(3))


def test_auto_detect_calibration_returns_none_on_no_match(tmp_path):
    nef = tmp_path / "fake.NEF"
    nef.write_bytes(_build_synthetic_nef("ACMECAM", "X9"))
    # No .npz for ACMECAM in tmp_path.
    assert auto_detect_calibration(nef, extra_roots=[tmp_path]) is None


def test_auto_detect_calibration_returns_none_on_non_tiff_raw(tmp_path):
    cr3 = tmp_path / "fake.CR3"
    cr3.write_bytes(b"\x00\x00\x00\x18ftypcrx ")  # ISO BMFF (Canon CR3)
    # CR3 isn't TIFF-shaped → read_raw_make_model returns None → auto-detect None.
    assert auto_detect_calibration(cr3, extra_roots=[tmp_path]) is None


def test_calibrate_camera_tool_writes_npz_with_explicit_matrix(tmp_path):
    """CLI tool: --camera + --matrix should write a .npz under --output."""
    from tools.calibrate_camera import main as tool_main
    out_dir = tmp_path / "cal"
    rc = tool_main([
        "--camera", "Nikon D750",
        "--matrix", "1.05,0,0, 0,1.04,0, 0,0,1.10",
        "--source", "test fixture",
        "--output", str(out_dir),
    ])
    assert rc == 0
    written = out_dir / "Nikon D750.npz"
    assert written.is_file()
    loaded = load_calibration(written)
    assert loaded.tier == 0  # explicit-matrix mode always records tier=0
    assert loaded.source == "test fixture"
    expected = np.array(
        [[1.05, 0, 0], [0, 1.04, 0], [0, 0, 1.10]],
        dtype=np.float32,
    )
    np.testing.assert_allclose(loaded.matrix, expected, rtol=1e-5)


def test_calibrate_camera_tool_rejects_wrong_matrix_size(tmp_path, capsys):
    from tools.calibrate_camera import main as tool_main
    rc = tool_main([
        "--camera", "Test",
        "--matrix", "1,2,3",  # only 3 values, need 9
        "--output", str(tmp_path),
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "9 comma-separated floats" in err


def test_calibrate_camera_tool_camera_required_or_raw_required(capsys):
    from tools.calibrate_camera import main as tool_main
    # Neither --camera nor --raw → argparse error
    with pytest.raises(SystemExit) as exc:
        tool_main(["--matrix", "1,0,0,0,1,0,0,0,1"])
    assert exc.value.code != 0


def test_calibrate_camera_tool_matrix_or_fit_tier_required(capsys):
    from tools.calibrate_camera import main as tool_main
    # --camera supplied but no --matrix or --fit-tier → argparse error
    with pytest.raises(SystemExit) as exc:
        tool_main(["--camera", "Nikon D750"])
    assert exc.value.code != 0


def test_render_logs_calibration_detection_under_algorithmic_engine(tmp_path, capsys):
    """End-to-end: lrt-cinema render --engine algorithmic with a planted
    calibration auto-detects + logs it (but doesn't yet emit — Phase 2b)."""
    from lrt_cinema.cli import main as cli_main

    # Plant a calibration in a temp dir + point env-var at it.
    cal_dir = tmp_path / "calroot"
    cal_dir.mkdir()
    save_calibration(_identity_calibration("Nikon D750"), cal_dir / "Nikon D750.npz")

    # Build a tiny sequence with one synthetic NEF whose EXIF reports D750.
    src = tmp_path / "input"
    src.mkdir()
    nef = src / "DSC_0001.NEF"
    nef.write_bytes(_build_synthetic_nef("NIKON CORPORATION", "NIKON D750"))
    # Need a minimal LRT XMP so parse_sequence picks the frame up as keyframe.
    (src / "DSC_0001.NEF.xmp").write_bytes(
        b'<?xml version="1.0"?>\n'
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        b'<rdf:Description rdf:about=""\n'
        b'  xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"\n'
        b'  xmlns:xmp="http://ns.adobe.com/xap/1.0/"\n'
        b'  crs:Exposure2012="0.5"\n'
        b'  xmp:Rating="4"/>\n'
        b'</rdf:RDF></x:xmpmeta>\n'
    )

    out = tmp_path / "output"
    with patch.dict(os.environ, {"LRT_CINEMA_CALIBRATION": str(cal_dir)}):
        rc = cli_main([
            "render",
            "--input", str(src),
            "--output", str(out),
            "--preset", "cinema-linear",
            "--engine", "algorithmic",
            "--dry-run",
            "--quiet",
        ])
    assert rc == 0
    err = capsys.readouterr().err
    assert "auto-detected calibration for Nikon D750" in err
    assert "emitting channelmixerrgb v3 correction matrix" in err


def test_render_does_NOT_auto_detect_calibration_under_dcp_engine(tmp_path, capsys):
    """Under --engine dcp, the DCP handles per-camera color science.
    Auto-detecting + applying a calibration would double-correct, so
    auto-detect is gated to algorithmic-engine only."""
    from lrt_cinema.cli import main as cli_main

    cal_dir = tmp_path / "calroot"
    cal_dir.mkdir()
    save_calibration(_identity_calibration("Nikon D750"), cal_dir / "Nikon D750.npz")

    src = tmp_path / "input"
    src.mkdir()
    nef = src / "DSC_0001.NEF"
    nef.write_bytes(_build_synthetic_nef("NIKON CORPORATION", "NIKON D750"))
    (src / "DSC_0001.NEF.xmp").write_bytes(
        b'<?xml version="1.0"?>\n'
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        b'<rdf:Description rdf:about=""\n'
        b'  xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"\n'
        b'  xmlns:xmp="http://ns.adobe.com/xap/1.0/"\n'
        b'  crs:Exposure2012="0.5"\n'
        b'  xmp:Rating="4"/>\n'
        b'</rdf:RDF></x:xmpmeta>\n'
    )

    out = tmp_path / "output"
    with patch.dict(os.environ, {"LRT_CINEMA_CALIBRATION": str(cal_dir)}):
        rc = cli_main([
            "render",
            "--input", str(src),
            "--output", str(out),
            "--preset", "cinema-linear",
            # default engine = dcp; auto-detect calibration must NOT fire.
            "--no-auto-dcp",  # silence the DCP-not-found message clutter
            "--dry-run",
            "--quiet",
        ])
    assert rc == 0
    err = capsys.readouterr().err
    assert "auto-detected calibration" not in err


# ---------------------------------------------------------------------------
# Tier 2 math primitives
# ---------------------------------------------------------------------------

def test_colorchecker_xyz_under_d55_returns_24_patches():
    """24 patches in row-major ColorChecker order, all positive XYZ."""
    from lrt_cinema.calibration import (
        COLORCHECKER_PATCH_NAMES,
        colorchecker_xyz_under_illuminant,
    )
    xyz = colorchecker_xyz_under_illuminant("D55")
    assert xyz.shape == (24, 3)
    assert (xyz >= 0).all()
    assert len(COLORCHECKER_PATCH_NAMES) == 24
    # White patch (#18, "white 9.5") must be brightest; black patch (#23)
    # darkest. Canonical ColorChecker ordering check.
    assert xyz[18].sum() > xyz[23].sum()
    # White Y should be near 0.9 (the "white" patch is ~90% reflectance).
    assert 0.7 < xyz[18, 1] < 1.0


def test_colorchecker_xyz_different_illuminants_differ():
    """D50 vs D65 XYZ values must differ — confirms illuminant routing
    isn't dropped."""
    from lrt_cinema.calibration import colorchecker_xyz_under_illuminant
    d50 = colorchecker_xyz_under_illuminant("D50")
    d65 = colorchecker_xyz_under_illuminant("D65")
    assert not np.allclose(d50, d65)


def test_fit_calibration_matrix_recovers_known_matrix():
    """Given measured = known_M @ target, fitter must recover known_M."""
    from lrt_cinema.calibration import fit_calibration_matrix
    rng = np.random.default_rng(42)
    target = rng.uniform(0.05, 0.95, size=(24, 3)).astype(np.float32)
    known_M = np.array(
        [[1.10, -0.05, -0.02],
         [-0.04, 1.05, -0.01],
         [-0.03, -0.02, 1.15]],
        dtype=np.float32,
    )
    measured = (known_M @ target.T).T
    fitted = fit_calibration_matrix(measured, target)
    # Should recover the INVERSE of known_M (fitted @ measured → target).
    expected_inverse = np.linalg.inv(known_M)
    np.testing.assert_allclose(fitted, expected_inverse, atol=1e-4)


def test_fit_calibration_matrix_identity_passes_through():
    """When measured == target, fitted matrix is identity."""
    from lrt_cinema.calibration import fit_calibration_matrix
    rng = np.random.default_rng(42)
    same = rng.uniform(0.05, 0.95, size=(24, 3)).astype(np.float32)
    fitted = fit_calibration_matrix(same, same)
    np.testing.assert_allclose(fitted, np.eye(3), atol=1e-5)


def test_fit_calibration_matrix_rejects_bad_shape():
    from lrt_cinema.calibration import fit_calibration_matrix
    with pytest.raises(ValueError, match="shape mismatch"):
        fit_calibration_matrix(np.zeros((10, 3)), np.zeros((10, 4)))
    with pytest.raises(ValueError, match=r"must be \(N, 3\)"):
        fit_calibration_matrix(np.zeros((10,)), np.zeros((10,)))
    with pytest.raises(ValueError, match="need at least 3 patches"):
        fit_calibration_matrix(np.zeros((2, 3)), np.zeros((2, 3)))


def test_sample_patches_from_tiff_picks_inner_region(tmp_path):
    """Plant a known-color rectangle in a TIFF, verify the sampler
    returns the mean of the inner pixels."""
    try:
        import tifffile
    except ImportError:
        pytest.skip("tifffile not installed")
    from lrt_cinema.calibration import sample_patches_from_tiff
    # 100×100 uint16 RGB image. Patch 1 covers (10,10)-(50,50) filled
    # with (10000, 20000, 30000); rest is zero.
    img = np.zeros((100, 100, 3), dtype=np.uint16)
    img[10:50, 10:50, 0] = 10000
    img[10:50, 10:50, 1] = 20000
    img[10:50, 10:50, 2] = 30000
    tif_path = tmp_path / "test.tif"
    tifffile.imwrite(str(tif_path), img)
    # Sampling with patch_size=40 at origin (10, 10), margin 0.25 →
    # inner region (20, 20)-(40, 40), which is filled with our color.
    samples = sample_patches_from_tiff(
        tif_path, [(10, 10)], patch_size=40, margin_fraction=0.25,
    )
    assert samples.shape == (1, 3)
    # uint16 max=65535; normalized to [0, 1]:
    expected = np.array([10000, 20000, 30000], dtype=np.float32) / 65535.0
    np.testing.assert_allclose(samples[0], expected, atol=1e-4)


# ---------------------------------------------------------------------------
# Tier 2 end-to-end integration via lrt-cinema render subprocess
# Gated on dt-cli + colour-science + bundled D750 .npz being available.
# ---------------------------------------------------------------------------

import shutil  # noqa: E402

_BUNDLED_D750 = (
    Path(__file__).parent / "fixtures" / "dcp_data"
    / "Nikon D750 Camera Standard.npz"
)
_TIER2_SKIP: list[str] = []
if shutil.which("darktable-cli") is None:
    _TIER2_SKIP.append("darktable-cli not on PATH")
if not _BUNDLED_D750.is_file():
    _TIER2_SKIP.append(f"bundled D750 .npz missing at {_BUNDLED_D750}")
try:
    import colour  # noqa: F401
except ImportError:
    _TIER2_SKIP.append("colour-science not installed")
try:
    import tifffile  # noqa: F401
except ImportError:
    _TIER2_SKIP.append("tifffile not installed")
if os.environ.get("DT_INTEGRATION_TEST") == "skip":
    _TIER2_SKIP.append("DT_INTEGRATION_TEST=skip")


@pytest.mark.skipif(bool(_TIER2_SKIP), reason="; ".join(_TIER2_SKIP) or "")
def test_tier2_fit_end_to_end_against_bundled_d750():
    """End-to-end Tier 2 fit: synthesize chart → write DNG → dt-cli ×2
    → sample patches → fit matrix → confirm post-fit ΔE2000 is bounded.

    This is the canonical "does the pipeline actually work" test for
    Phase 2b. Pre-fit ΔE2000 (algorithmic-only output vs DCP-rendered
    target) is typically 5-15 on real cameras; post-fit should be < 4
    on average for a 3×3 matrix fit. The bundled Nikon D750 .npz is
    the dev camera; this test locks in a per-camera expected envelope
    so future regressions on the math (synthesis, dt-cli flags, fit
    solver) are caught."""
    from lrt_cinema.calibration import (
        fit_tier2_via_dt_cli_roundtrip,
        load_calibration,
    )
    from lrt_cinema.dcp import load_profile

    dcp_profile = load_profile(_BUNDLED_D750)
    result = fit_tier2_via_dt_cli_roundtrip(
        dcp_profile,
        camera_make="NIKON CORPORATION",
        camera_model="NIKON D750",
        unique_camera_model="Nikon D750",
        illuminant="D55",
    )
    # Sanity: matrix is 3x3, finite, has reasonable magnitude.
    assert result.matrix.shape == (3, 3)
    assert np.isfinite(result.matrix).all()
    assert (np.abs(result.matrix) < 10).all(), (
        f"matrix has wild magnitudes: {result.matrix}"
    )
    # Envelope check. A 3×3 fit captures ONLY the linear portion of the
    # DCP transformation. The bundled D750 .npz has a 90×16×16 LookTable
    # (non-linear hue/sat/val shifts) plus a ProfileToneCurve plus
    # potential HSM contributions — none of which a 3×3 can recover.
    # Empirically the fit lands around ΔE2000 mean ~13 on this camera
    # (vs ~30+ without any correction). Closing the residual gap requires
    # 3D LUT fitting (v0.5+ work — channelmixerrgb is not the right
    # emission for that).
    #
    # The 20.0 bound is generous-but-meaningful: a regression in the fit
    # math (wrong synthesis, wrong sampling, wrong patch order) typically
    # blows past this; a healthy fit stays well under it.
    assert result.delta_e2000_mean < 20.0, (
        f"Tier 2 post-fit ΔE2000 mean {result.delta_e2000_mean:.2f} > 20.0; "
        f"likely a fit-math regression (synthesis, dt-cli flags, sampling, "
        f"or solver). Baseline expected ~13 on the bundled D750 .npz."
    )
    # Round-trip via save_calibration / load_calibration to confirm the
    # full storage path works on a real fit result.

    import tempfile as _tempfile
    with _tempfile.TemporaryDirectory() as td:
        out = Path(td) / "Nikon D750.npz"
        save_calibration(
            Calibration(
                camera_label="Nikon D750",
                matrix=result.matrix,
                tier=2,
                source=f"Tier 2 fit @ D55, ΔE2000 mean {result.delta_e2000_mean:.2f}",
                delta_e2000_mean=result.delta_e2000_mean,
                delta_e2000_max=result.delta_e2000_max,
            ),
            out,
        )
        reloaded = load_calibration(out)
        np.testing.assert_allclose(reloaded.matrix, result.matrix, rtol=1e-5)
        assert reloaded.tier == 2
