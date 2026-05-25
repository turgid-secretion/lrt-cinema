"""Synthetic DNG writer — structural tests + optional dt-cli integration.

Structural tests verify the DNG is a well-formed TIFF (parseable, has
the required DNG tags). The integration test runs dt-cli on the synth
DNG and confirms it produces a non-trivial render (gated on dt-cli +
the bundled D750 .npz being available)."""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
from pathlib import Path

import numpy as np
import pytest

from lrt_cinema.dcp import interpolate_color_matrix, load_profile
from lrt_cinema.synthetic_dng import (
    CFA_RGGB,
    PatchLayout,
    write_calibration_dng,
)

_BUNDLED_D750 = (
    Path(__file__).parent / "fixtures" / "dcp_data"
    / "Nikon D750 Camera Standard.npz"
)


def _read_ifd0_tags(path: Path) -> dict[int, tuple[int, int, bytes]]:
    """Minimal IFD0 reader for verifying our DNG writer's output.

    Returns {tag_id: (type, count, payload_bytes)} for every IFD0 entry.
    payload_bytes is the value field (inline ≤4) or the bytes at the
    offset that field points to.
    """
    data = path.read_bytes()
    assert data[:2] == b"II", "expected little-endian TIFF"
    magic = struct.unpack("<H", data[2:4])[0]
    assert magic == 42, f"expected TIFF magic 42, got {magic}"
    ifd0_offset = struct.unpack("<I", data[4:8])[0]
    n_entries = struct.unpack("<H", data[ifd0_offset:ifd0_offset + 2])[0]
    type_sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 10: 8}
    tags: dict[int, tuple[int, int, bytes]] = {}
    cursor = ifd0_offset + 2
    for _ in range(n_entries):
        entry = data[cursor:cursor + 12]
        tag, ttype, count = struct.unpack("<HHI", entry[:8])
        size = type_sizes.get(ttype, 1) * count
        if size <= 4:
            payload = entry[8:8 + size]
        else:
            offset = struct.unpack("<I", entry[8:12])[0]
            payload = data[offset:offset + size]
        tags[tag] = (ttype, count, payload)
        cursor += 12
    return tags


def test_write_dng_produces_parseable_tiff(tmp_path):
    """DNG must be a well-formed TIFF: II magic, 42, IFD0 at offset 8."""
    out = tmp_path / "test.dng"
    write_calibration_dng(
        out,
        camera_make="NIKON CORPORATION",
        camera_model="NIKON D750",
        unique_camera_model="Nikon D750",
        color_matrix_1=np.eye(3),
    )
    data = out.read_bytes()
    assert data[:2] == b"II"
    assert struct.unpack("<H", data[2:4])[0] == 42
    ifd0_offset = struct.unpack("<I", data[4:8])[0]
    assert ifd0_offset == 8


def test_write_dng_carries_required_dng_tags(tmp_path):
    """libraw needs Make/Model + UniqueCameraModel + DNGVersion + CFA tags
    to identify the camera and demosaic. Missing any → libraw falls back
    to generic input and the calibration is meaningless."""
    out = tmp_path / "test.dng"
    write_calibration_dng(
        out,
        camera_make="NIKON CORPORATION",
        camera_model="NIKON D750",
        unique_camera_model="Nikon D750",
        color_matrix_1=np.eye(3),
    )
    tags = _read_ifd0_tags(out)
    # Camera ID
    assert 271 in tags, "Make missing"
    assert 272 in tags, "Model missing"
    assert 50708 in tags, "UniqueCameraModel missing"
    # DNG version markers
    assert 50706 in tags, "DNGVersion missing"
    assert tags[50706][2] == bytes([1, 4, 0, 0])
    assert 50707 in tags, "DNGBackwardVersion missing"
    # CFA / Bayer tags
    assert 33421 in tags, "CFARepeatPatternDim missing"
    assert 33422 in tags, "CFAPattern missing"
    assert tags[33422][2] == CFA_RGGB
    assert 50710 in tags, "CFAPlaneColor missing"
    assert 50711 in tags, "CFALayout missing"
    # Color science
    assert 50721 in tags, "ColorMatrix1 missing"
    assert 50728 in tags, "AsShotNeutral missing"
    # Image data wrapping
    assert 256 in tags, "ImageWidth missing"
    assert 257 in tags, "ImageLength missing"
    assert 258 in tags, "BitsPerSample missing"
    assert 259 in tags, "Compression missing"
    assert tags[262][2] == struct.pack("<H", 32803), "Photometric must be CFA"
    assert 273 in tags, "StripOffsets missing"
    assert 279 in tags, "StripByteCounts missing"


def test_write_dng_encodes_make_model_correctly(tmp_path):
    out = tmp_path / "test.dng"
    write_calibration_dng(
        out,
        camera_make="NIKON CORPORATION",
        camera_model="NIKON D750",
        unique_camera_model="Nikon D750",
        color_matrix_1=np.eye(3),
    )
    tags = _read_ifd0_tags(out)
    assert tags[271][2].rstrip(b"\x00").decode() == "NIKON CORPORATION"
    assert tags[272][2].rstrip(b"\x00").decode() == "NIKON D750"
    assert tags[50708][2].rstrip(b"\x00").decode() == "Nikon D750"


def test_write_dng_strip_offset_points_at_valid_image_data(tmp_path):
    """The StripOffsets value must point at an address within the file
    + the StripByteCounts bytes must be readable from there."""
    out = tmp_path / "test.dng"
    layout = write_calibration_dng(
        out,
        camera_make="NIKON CORPORATION",
        camera_model="NIKON D750",
        unique_camera_model="Nikon D750",
        color_matrix_1=np.eye(3),
        patch_size=32, margin=4,  # smaller for faster test
    )
    data = out.read_bytes()
    tags = _read_ifd0_tags(out)
    strip_offset = struct.unpack("<I", tags[273][2])[0]
    strip_bytes = struct.unpack("<I", tags[279][2])[0]
    expected_bytes = layout.image_width * layout.image_height * 2  # uint16
    assert strip_bytes == expected_bytes
    assert strip_offset + strip_bytes <= len(data), (
        "StripOffsets + StripByteCounts overruns file end"
    )
    # The image data should be non-zero (we filled patches + background).
    image_region = data[strip_offset:strip_offset + strip_bytes]
    assert any(b != 0 for b in image_region[:1024])


def test_write_dng_layout_describes_24_patches_in_grid(tmp_path):
    out = tmp_path / "test.dng"
    layout = write_calibration_dng(
        out,
        camera_make="N", camera_model="M", unique_camera_model="N M",
        color_matrix_1=np.eye(3),
        patch_size=32, margin=4, grid_cols=6, grid_rows=4,
    )
    assert layout.grid_cols == 6
    assert layout.grid_rows == 4
    assert len(layout.patch_origins) == 24
    # Patches must not overlap.
    seen_bboxes: set[tuple[int, int, int, int]] = set()
    for i in range(24):
        bbox = layout.patch_bbox(i)
        assert bbox not in seen_bboxes
        seen_bboxes.add(bbox)
    # Image dims must accommodate the grid + margins.
    assert layout.image_width >= 6 * 32
    assert layout.image_height >= 4 * 32


def test_write_dng_rejects_wrong_patch_count(tmp_path):
    out = tmp_path / "test.dng"
    with pytest.raises(ValueError, match="patches_camera_rgb shape"):
        write_calibration_dng(
            out,
            camera_make="N", camera_model="M", unique_camera_model="N M",
            color_matrix_1=np.eye(3),
            patches_camera_rgb=np.zeros((5, 3)),  # wrong: expected 24
        )


def test_patch_layout_inner_bbox_provides_margin():
    """`patch_inner_bbox` returns a smaller region — sampling here
    avoids demosaic edge bleed across patch boundaries."""
    layout = PatchLayout(
        image_width=100, image_height=100, patch_size=40,
        grid_cols=1, grid_rows=1,
        patch_origins=[(10, 10)],
    )
    outer = layout.patch_bbox(0)
    inner = layout.patch_inner_bbox(0, margin_fraction=0.25)
    assert outer == (10, 10, 50, 50)
    # 25% margin on 40-px patch = 10 px each side → inner (20, 20, 40, 40)
    assert inner == (20, 20, 40, 40)


# ---------------------------------------------------------------------------
# dt-cli integration: render the synthetic DNG, confirm we get a TIFF out.
# Gated on dt-cli + bundled D750 .npz being available.
# ---------------------------------------------------------------------------

_DT_CLI = shutil.which("darktable-cli")
_SKIP_REASONS: list[str] = []
if _DT_CLI is None:
    _SKIP_REASONS.append("darktable-cli not on PATH")
if os.environ.get("DT_INTEGRATION_TEST") == "skip":
    _SKIP_REASONS.append("DT_INTEGRATION_TEST=skip")
if not _BUNDLED_D750.is_file():
    _SKIP_REASONS.append(f"bundled D750 .npz missing at {_BUNDLED_D750}")


@pytest.mark.skipif(bool(_SKIP_REASONS), reason="; ".join(_SKIP_REASONS) or "")
def test_synthetic_dng_renders_through_dt_cli(tmp_path):
    """End-to-end: write synthetic D750 DNG → dt-cli renders to TIFF.

    Validates that the DNG is actually consumable by libraw/dt — the
    structural tests above don't catch a real-world libraw rejection.

    A successful render proves:
      - libraw identified the camera (Nikon D750)
      - libraw demosaiced the Bayer mosaic
      - dt's pipeline ran to completion
      - dt wrote a non-empty output TIFF
    """
    # Build camera-RGB for 24 patches via the bundled D750 DCP.
    # We pick D55-ish target by interpolating ColorMatrix at 5500 K
    # then mapping a simple per-patch XYZ via Lab→XYZ (D55 reference).
    profile = load_profile(_BUNDLED_D750)
    cm_d55 = interpolate_color_matrix(profile, kelvin=5500)
    # 24 patches at quasi-uniform XYZ values (not real ColorChecker —
    # this test just verifies the round-trip, not colorimetric accuracy).
    rng = np.random.default_rng(seed=42)
    patches_xyz = rng.uniform(0.1, 0.9, size=(24, 3)).astype(np.float32)
    patches_camera_rgb = (cm_d55 @ patches_xyz.T).T
    # Clip + normalize so peak is at 0.8 (avoid hitting white).
    patches_camera_rgb = np.clip(patches_camera_rgb, 0.0, None)
    peak = patches_camera_rgb.max()
    if peak > 0:
        patches_camera_rgb = patches_camera_rgb * (0.8 / peak)

    dng = tmp_path / "synthetic.dng"
    write_calibration_dng(
        dng,
        camera_make="NIKON CORPORATION",
        camera_model="NIKON D750",
        unique_camera_model="Nikon D750",
        color_matrix_1=profile.color_matrix_1,
        color_matrix_2=profile.color_matrix_2,
        calibration_illuminant_1=profile.calibration_illuminant_1 or 17,
        calibration_illuminant_2=profile.calibration_illuminant_2 or 21,
        patches_camera_rgb=patches_camera_rgb,
    )

    out_tif = tmp_path / "render.tif"
    # No XMP sidecar — dt-cli renders the DNG with its default pipeline.
    # --apply-custom-presets 0 disables workflow injection so the output
    # is deterministic across user dt configs.
    argv = [
        _DT_CLI, str(dng), str(out_tif),
        "--apply-custom-presets", "0",
        "--icc-type", "LIN_REC2020",
        "--icc-intent", "RELATIVE_COLORIMETRIC",
        "--core",
        "--conf", "plugins/imageio/format/tiff/bpp=16",
        "--conf", "plugins/imageio/format/tiff/compress=0",
        "--conf", "plugins/imageio/format/tiff/pixelformat=0",
    ]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, (
        f"dt-cli rejected the synthetic DNG (returncode {proc.returncode}):\n"
        f"  stderr: {proc.stderr[-1500:]}\n"
        f"  stdout: {proc.stdout[-500:]}"
    )
    assert out_tif.is_file(), "dt-cli reported success but no TIFF written"
    assert out_tif.stat().st_size > 1024, (
        f"output TIFF suspiciously small ({out_tif.stat().st_size} bytes)"
    )
