"""Calibration storage + lookup tests (Phase 2a infrastructure)."""

from __future__ import annotations

import os
import struct
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
    """CLI tool stub: --camera + --matrix should write a .npz under --output."""
    from tools.calibrate_camera import main as tool_main
    out_dir = tmp_path / "cal"
    rc = tool_main([
        "--camera", "Nikon D750",
        "--matrix", "1.05,0,0, 0,1.04,0, 0,0,1.10",
        "--tier", "0",
        "--source", "test fixture",
        "--output", str(out_dir),
    ])
    assert rc == 0
    written = out_dir / "Nikon D750.npz"
    assert written.is_file()
    loaded = load_calibration(written)
    assert loaded.tier == 0
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
