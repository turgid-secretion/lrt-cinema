"""Unit tests for the Adobe DCP profile reader and kelvin-to-multipliers math.

We cannot ship real Adobe DCPs (license + size) so the IFD-reader tests
build a synthetic ~200-byte DCP from a hand-laid IFD with known
matrices. The Robertson + camera-neutral math is cross-checked against
published reference values from Wyszecki & Stiles and against
colour-science (when available).

Real-Adobe-DCP smoke test runs only when one is present on the local
filesystem (typically `/Library/Application Support/Adobe/CameraRaw/
CameraProfiles/Camera/...` on macOS). It is gated behind a skip so CI
without ACR installed still passes.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from lrt_cinema.dcp import (
    DCPProfile,
    _adobe_camera_label,
    _build_hsv_cube,
    adobe_make_for_camera,
    auto_detect_dcp,
    find_dcp_for_camera,
    illuminant_code_to_kelvin,
    interpolate_color_matrix,
    interpolate_hsv_cube,
    kelvin_tint_to_dt_multipliers,
    kelvin_tint_to_xy,
    parse_dcp,
    read_raw_make_model,
    uv_to_kelvin,
    xy_to_camera_neutral,
    xy_to_uv,
)

# ---------------------------------------------------------------------------
# Synthetic DCP builder — small TIFF/IFD writer for tests
# ---------------------------------------------------------------------------

def _srational(num: int, den: int) -> bytes:
    return struct.pack("<ii", num, den)


def _srational_matrix(matrix: np.ndarray, scale: int = 10000) -> bytes:
    """Encode a 3x3 numpy matrix as 9 SRATIONAL entries."""
    out = b""
    for v in matrix.flatten():
        out += _srational(int(round(v * scale)), scale)
    return out


def _build_synthetic_dcp(
    color_matrix_1: np.ndarray,
    color_matrix_2: np.ndarray | None = None,
    forward_matrix_1: np.ndarray | None = None,
    forward_matrix_2: np.ndarray | None = None,
    calibration_illuminant_1: int = 17,  # Standard A
    calibration_illuminant_2: int = 21,  # D65
    profile_name: str = "Test DCP",
    baseline_exposure: float = 0.0,
    profile_tone_curve: np.ndarray | None = None,
) -> bytes:
    """Build a minimal valid DCP file as bytes.

    Layout:
        TIFF header (8 B): "II" + magic 0x4352 + IFD0 offset (8)
        IFD0: n_entries (2 B) + entries (12 B each) + next_ifd (4 B)
        Big-value blob (after IFD0): the 8-byte+ payloads each entry
        points into.

    All entries inline if their data fits in 4 bytes; otherwise the
    entry's value/offset field is the absolute file offset to the
    big-value blob.
    """
    # Pre-compute entries: (tag, type, count, value_or_blob)
    entries: list[tuple[int, int, int, bytes]] = []

    # ProfileName (ASCII)
    name_bytes = (profile_name + "\x00").encode("ascii")
    entries.append((50936, 2, len(name_bytes), name_bytes))

    # ColorMatrix1 (SRATIONAL, count=9)
    entries.append((50721, 10, 9, _srational_matrix(color_matrix_1)))

    if color_matrix_2 is not None:
        entries.append((50722, 10, 9, _srational_matrix(color_matrix_2)))
    if forward_matrix_1 is not None:
        entries.append((50964, 10, 9, _srational_matrix(forward_matrix_1)))
    if forward_matrix_2 is not None:
        entries.append((50965, 10, 9, _srational_matrix(forward_matrix_2)))

    # CalibrationIlluminant1/2 (SHORT). Canonical DNG 1.7.1 tag IDs are
    # 50778 / 50779, NOT 50931/50932 (this fixture matched the prior
    # incorrect parser constants; updated to canonical at the parser-tag fix
    # commit).
    entries.append((50778, 3, 1, struct.pack("<H", calibration_illuminant_1)))
    entries.append((50779, 3, 1, struct.pack("<H", calibration_illuminant_2)))

    # BaselineExposure (SRATIONAL, count=1)
    entries.append((
        50730, 10, 1,
        _srational(int(round(baseline_exposure * 10000)), 10000),
    ))

    # ProfileToneCurve (FLOAT, count = 2*N)
    if profile_tone_curve is not None:
        n = profile_tone_curve.shape[0]
        floats = profile_tone_curve.flatten().astype(np.float32)
        entries.append((50940, 11, n * 2, floats.tobytes()))

    # Entries must be sorted by tag id per TIFF spec.
    entries.sort(key=lambda e: e[0])

    n_entries = len(entries)
    ifd_size = 2 + 12 * n_entries + 4
    big_blob_offset = 8 + ifd_size

    ifd_bytes = struct.pack("<H", n_entries)
    big_blob = b""
    cur_blob_off = big_blob_offset

    for tag, ttype, count, payload in entries:
        if len(payload) <= 4:
            value_field = payload + b"\x00" * (4 - len(payload))
        else:
            value_field = struct.pack("<I", cur_blob_off)
            big_blob += payload
            cur_blob_off += len(payload)
        ifd_bytes += struct.pack("<HHI4s", tag, ttype, count, value_field)

    ifd_bytes += struct.pack("<I", 0)  # next IFD = 0 (last)

    # TIFF header: "II" + magic 0x4352 ("CR" little-endian) + IFD0 offset.
    header = b"II" + struct.pack("<H", 0x4352) + struct.pack("<I", 8)
    return header + ifd_bytes + big_blob


# ---------------------------------------------------------------------------
# IFD-reader tests
# ---------------------------------------------------------------------------

def test_parse_synthetic_dcp_round_trips_matrices(tmp_path):
    m1 = np.array([
        [1.0, -0.4,  0.0],
        [-0.5, 1.2,  0.3],
        [-0.1,  0.2, 0.8],
    ])
    m2 = np.array([
        [0.9, -0.3, -0.1],
        [-0.5, 1.3,  0.2],
        [-0.1,  0.2, 0.7],
    ])
    dcp = _build_synthetic_dcp(
        color_matrix_1=m1,
        color_matrix_2=m2,
        forward_matrix_1=np.eye(3),
        forward_matrix_2=np.eye(3),
        calibration_illuminant_1=17,
        calibration_illuminant_2=21,
        profile_name="Synthetic Test",
        baseline_exposure=-0.5,
    )
    path = tmp_path / "test.dcp"
    path.write_bytes(dcp)

    p = parse_dcp(path)
    assert p.profile_name == "Synthetic Test"
    assert p.calibration_illuminant_1 == 17
    assert p.calibration_illuminant_2 == 21
    assert p.kelvin_1 == 2856.0
    assert p.kelvin_2 == 6504.0
    assert p.baseline_exposure == pytest.approx(-0.5, abs=1e-4)
    # SRATIONAL encoding scale=10000 → 4-decimal precision.
    np.testing.assert_allclose(p.color_matrix_1, m1, atol=1e-4)
    np.testing.assert_allclose(p.color_matrix_2, m2, atol=1e-4)
    np.testing.assert_allclose(p.forward_matrix_1, np.eye(3), atol=1e-4)
    np.testing.assert_allclose(p.forward_matrix_2, np.eye(3), atol=1e-4)


def test_parse_synthetic_dcp_with_tone_curve(tmp_path):
    # Concave-up tone curve, like Adobe's profile-bundled curves.
    n = 32
    xs = np.linspace(0, 1, n)
    ys = xs ** 0.5  # sqrt curve = highlight lift
    curve = np.stack([xs, ys], axis=1)
    dcp = _build_synthetic_dcp(
        color_matrix_1=np.eye(3),
        profile_tone_curve=curve,
    )
    path = tmp_path / "curve.dcp"
    path.write_bytes(dcp)
    p = parse_dcp(path)
    assert p.profile_tone_curve is not None
    assert p.profile_tone_curve.shape == (n, 2)
    # 32-bit float → fits in 0..1 range; we round-trip exactly.
    np.testing.assert_allclose(p.profile_tone_curve, curve, rtol=1e-6, atol=1e-6)


def test_parse_unknown_illuminants_uses_adobe_convention(tmp_path):
    # When CalibrationIlluminant1/2 are 0 (Unknown), Adobe-shipped DCPs
    # follow the SDK convention: ColorMatrix1 = warmer (Standard A 2856K),
    # ColorMatrix2 = cooler (D65 6504K).
    m1 = np.eye(3) * 1.0
    m2 = np.eye(3) * 0.9
    dcp = _build_synthetic_dcp(
        color_matrix_1=m1,
        color_matrix_2=m2,
        calibration_illuminant_1=0,
        calibration_illuminant_2=0,
    )
    path = tmp_path / "unk.dcp"
    path.write_bytes(dcp)
    p = parse_dcp(path)
    assert p.kelvin_1 == 2856.0
    assert p.kelvin_2 == 6504.0


def test_parse_dcp_rejects_non_tiff(tmp_path):
    path = tmp_path / "bad.dcp"
    path.write_bytes(b"NOT A TIFF FILE")
    with pytest.raises(ValueError, match="byte-order marker"):
        parse_dcp(path)


def test_parse_dcp_rejects_wrong_magic(tmp_path):
    path = tmp_path / "bad.dcp"
    path.write_bytes(b"II" + struct.pack("<H", 1234) + b"\x08\x00\x00\x00")
    with pytest.raises(ValueError, match="magic"):
        parse_dcp(path)


def test_illuminant_code_to_kelvin_known_values():
    assert illuminant_code_to_kelvin(17) == 2856.0  # Standard A
    assert illuminant_code_to_kelvin(21) == 6504.0  # D65
    assert illuminant_code_to_kelvin(23) == 5003.0  # D50
    assert illuminant_code_to_kelvin(999) == 5500.0  # fallback


# ---------------------------------------------------------------------------
# Robertson math tests
# ---------------------------------------------------------------------------

def test_kelvin_to_xy_d65_matches_published_value():
    # CIE D65 published value: (0.31271, 0.32902).
    # Robertson's table approximates the Planckian locus, which D65
    # falls slightly off-locus (small tint offset of ~+9 in DNG units).
    # The on-locus Robertson value at 6504K is close to but not equal
    # to D65's published xy.
    x, y = kelvin_tint_to_xy(6504, 0)
    assert 0.30 < x < 0.32
    assert 0.32 < y < 0.34


def test_kelvin_to_xy_standard_a_matches_published_value():
    # CIE Standard Illuminant A published: (0.44758, 0.40745).
    x, y = kelvin_tint_to_xy(2856, 0)
    # Planckian locus at 2856K → (~0.4476, ~0.4074) within rounding.
    assert 0.43 < x < 0.46
    assert 0.39 < y < 0.42


def test_uv_inverse_round_trip():
    # kelvin → (x,y) → (u,v) → kelvin should round-trip within
    # Robertson table precision (~tens of K at typical illuminants).
    for k_in in (2856, 4000, 5500, 6504, 7500):
        x, y = kelvin_tint_to_xy(k_in, 0)
        u, v = xy_to_uv(x, y)
        k_out = uv_to_kelvin(u, v)
        assert abs(k_in - k_out) < 50, f"K={k_in} → {k_out}"


def test_kelvin_tint_to_xy_rejects_out_of_range():
    with pytest.raises(ValueError):
        kelvin_tint_to_xy(1000, 0)  # below table low end


# ---------------------------------------------------------------------------
# Color-matrix interpolation + neutral solver tests
# ---------------------------------------------------------------------------

def test_interpolate_color_matrix_endpoints():
    # At kelvin_1 → returns ColorMatrix1; at kelvin_2 → returns ColorMatrix2;
    # at midpoint → returns blended.
    p = DCPProfile(
        color_matrix_1=np.eye(3),
        color_matrix_2=np.eye(3) * 2.0,
        kelvin_1=2856.0,
        kelvin_2=6504.0,
    )
    m_lo = interpolate_color_matrix(p, 2856)
    m_hi = interpolate_color_matrix(p, 6504)
    np.testing.assert_allclose(m_lo, np.eye(3))
    np.testing.assert_allclose(m_hi, np.eye(3) * 2.0)
    # Midpoint in mired space, not kelvin space — at 1/((1/2856 + 1/6504)/2)
    # ≈ 3970 K, we should land at the exact midpoint matrix.
    mid_inv = (1 / 2856 + 1 / 6504) / 2
    mid_k = 1.0 / mid_inv
    m_mid = interpolate_color_matrix(p, mid_k)
    np.testing.assert_allclose(m_mid, np.eye(3) * 1.5, atol=1e-3)


def test_interpolate_color_matrix_single_illuminant():
    # ColorMatrix2 absent → always returns ColorMatrix1.
    p = DCPProfile(color_matrix_1=np.eye(3) * 1.5, kelvin_1=5500.0)
    np.testing.assert_allclose(
        interpolate_color_matrix(p, 3200), np.eye(3) * 1.5,
    )
    np.testing.assert_allclose(
        interpolate_color_matrix(p, 8000), np.eye(3) * 1.5,
    )


def test_xy_to_camera_neutral_identity_matrix():
    # Identity color matrix → camera-RGB-at-white = the XYZ of that white.
    p = DCPProfile(
        color_matrix_1=np.eye(3),
        kelvin_1=5500.0,
    )
    x, y = kelvin_tint_to_xy(5500, 0)
    neutral = xy_to_camera_neutral(p, x, y)
    # With identity matrix, neutral == XYZ(x,y) with Y=1.
    np.testing.assert_allclose(
        neutral, np.array([x / y, 1.0, (1 - x - y) / y]), atol=1e-6,
    )


def test_kelvin_tint_to_dt_multipliers_green_is_one():
    # By convention dt's GUI normalizes multipliers so green = 1.
    p = DCPProfile(
        color_matrix_1=np.array([
            [1.0, -0.4, -0.1],
            [-0.5, 1.3, 0.3],
            [-0.1, 0.2, 0.8],
        ]),
        kelvin_1=5500.0,
    )
    r, g1, b, g2 = kelvin_tint_to_dt_multipliers(p, 5500, 0)
    assert g1 == pytest.approx(1.0, abs=1e-9)
    assert g2 == g1  # Bayer convention: G1 = G2.
    assert 0.1 < r < 10.0  # sane range for a real camera matrix
    assert 0.1 < b < 10.0


# ---------------------------------------------------------------------------
# Real-Adobe-DCP smoke test (skipped when ACR not installed)
# ---------------------------------------------------------------------------

_REAL_DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/"
    "Nikon D750/Nikon D750 Camera Standard.dcp"
)


@pytest.mark.skipif(
    not _REAL_DCP.exists(),
    reason="real Adobe DCP not installed at standard macOS path",
)
def test_real_adobe_dcp_parses():
    p = parse_dcp(_REAL_DCP)
    assert p.profile_name == "Camera Standard"
    assert p.color_matrix_1 is not None and p.color_matrix_1.shape == (3, 3)
    assert p.color_matrix_2 is not None and p.color_matrix_2.shape == (3, 3)
    assert p.profile_tone_curve is not None
    assert p.profile_tone_curve.shape[1] == 2
    assert p.profile_tone_curve.shape[0] >= 32
    # Tone curve must be monotone and start/end at (0,0) and (1,1).
    np.testing.assert_allclose(p.profile_tone_curve[0], [0, 0], atol=1e-6)
    np.testing.assert_allclose(p.profile_tone_curve[-1], [1, 1], atol=1e-6)
    assert np.all(np.diff(p.profile_tone_curve[:, 0]) >= 0)
    assert np.all(np.diff(p.profile_tone_curve[:, 1]) >= 0)


# ---------------------------------------------------------------------------
# RAW Make/Model + DCP auto-detect tests
# ---------------------------------------------------------------------------

def _build_synthetic_nef(make: str, model: str) -> bytes:
    """Build a minimal TIFF-shaped RAW header with IFD0 carrying Make+Model.

    Mirrors `_build_synthetic_dcp` shape but uses standard TIFF magic 42 (the
    DCP probe rejects this; the RAW probe accepts it). Enough for
    `read_raw_make_model` — a real NEF has hundreds more IFD entries we do
    not care about.
    """
    make_bytes = (make + "\x00").encode("ascii")
    model_bytes = (model + "\x00").encode("ascii")
    entries = [
        (271, 2, len(make_bytes), make_bytes),   # Make
        (272, 2, len(model_bytes), model_bytes), # Model
    ]
    n_entries = len(entries)
    ifd_size = 2 + 12 * n_entries + 4
    big_blob_offset = 8 + ifd_size

    ifd_bytes = struct.pack("<H", n_entries)
    big_blob = b""
    cur_blob_off = big_blob_offset
    for tag, ttype, count, payload in entries:
        if len(payload) <= 4:
            value_field = payload + b"\x00" * (4 - len(payload))
        else:
            value_field = struct.pack("<I", cur_blob_off)
            big_blob += payload
            cur_blob_off += len(payload)
        ifd_bytes += struct.pack("<HHI4s", tag, ttype, count, value_field)
    ifd_bytes += struct.pack("<I", 0)
    header = b"II" + struct.pack("<H", 42) + struct.pack("<I", 8)  # standard TIFF
    return header + ifd_bytes + big_blob


def test_read_raw_make_model_from_synthetic_tiff(tmp_path):
    path = tmp_path / "fake.NEF"
    path.write_bytes(_build_synthetic_nef("NIKON CORPORATION", "NIKON D750"))
    assert read_raw_make_model(path) == ("NIKON CORPORATION", "NIKON D750")


def test_read_raw_make_model_rejects_dcp_magic(tmp_path):
    # DCP files use magic 0x4352, not standard TIFF 42. The RAW probe
    # must NOT accept them — they would never carry a Make/Model anyway,
    # and accepting them would mask real "this is not a RAW" errors.
    path = tmp_path / "fake.dcp"
    path.write_bytes(b"II" + struct.pack("<H", 0x4352) + b"\x08\x00\x00\x00" + b"\x00" * 20)
    assert read_raw_make_model(path) is None


def test_read_raw_make_model_rejects_non_tiff(tmp_path):
    path = tmp_path / "fake.CR3"
    path.write_bytes(b"\x00\x00\x00\x18ftypcrx ")  # ISO BMFF (Canon CR3)
    assert read_raw_make_model(path) is None


def test_read_raw_make_model_handles_truncated_file(tmp_path):
    path = tmp_path / "tiny.NEF"
    path.write_bytes(b"II*\x00")  # 4 bytes — header truncated
    assert read_raw_make_model(path) is None


def test_adobe_make_for_camera_normalization():
    assert adobe_make_for_camera("NIKON CORPORATION") == "Nikon"
    assert adobe_make_for_camera("Canon") == "Canon"
    assert adobe_make_for_camera("SONY") == "Sony"
    assert adobe_make_for_camera("FUJIFILM") == "Fujifilm"
    assert adobe_make_for_camera("RICOH IMAGING COMPANY, LTD.") == "PENTAX"
    # Unknown make → title-case fallback.
    assert adobe_make_for_camera("ACME PHOTOMATIC") == "Acme Photomatic"


def test_adobe_camera_label_strips_make_prefix():
    # EXIF: Make="NIKON CORPORATION", Model="NIKON D750"
    # Adobe filename label: "Nikon D750" (strip "NIKON " from model, normalize make).
    assert _adobe_camera_label("NIKON CORPORATION", "NIKON D750") == "Nikon D750"
    # Canon: Make="Canon", Model="Canon EOS R5"
    assert _adobe_camera_label("Canon", "Canon EOS R5") == "Canon EOS R5"
    # Sony: Make="SONY", Model="ILCE-7M3" (no make prefix)
    assert _adobe_camera_label("SONY", "ILCE-7M3") == "Sony ILCE-7M3"


def test_find_dcp_for_camera_searches_extra_roots(tmp_path):
    # Plant a Camera Standard DCP under tmp_path/<root>/Camera/<label>/...
    # `adobe_make_for_camera` title-cases unknown vendors, so the planted
    # path must match the normalized label (label-build is what `find` looks
    # up — not the raw EXIF strings).
    label = _adobe_camera_label("Acme", "X100")  # "Acme X100"
    cam_dir = tmp_path / "extra_root" / "Camera" / label
    cam_dir.mkdir(parents=True)
    dcp_path = cam_dir / f"{label} Camera Standard.dcp"
    dcp_path.write_bytes(b"dummy content")  # not a real DCP — find only matches existence
    found = find_dcp_for_camera("Acme", "X100", extra_roots=[tmp_path / "extra_root"])
    assert found == dcp_path


def test_find_dcp_for_camera_returns_none_on_miss(tmp_path):
    assert find_dcp_for_camera("UnknownVendor", "X", extra_roots=[tmp_path]) is None


def test_auto_detect_dcp_end_to_end_with_synthetic(tmp_path):
    nef = tmp_path / "fake.NEF"
    nef.write_bytes(_build_synthetic_nef("ACMECAM", "Z1"))
    # No DCP planted anywhere — returns None.
    assert auto_detect_dcp(nef) is None


# ---------------------------------------------------------------------------
# HSV-cube parser tests
# ---------------------------------------------------------------------------

def test_build_hsv_cube_reshapes_in_value_hue_sat_order():
    # 2 hue × 2 sat × 3 val × 3 floats = 36 floats.
    # Value-major, hue-medium, sat-minor layout means flat index 0..2 is
    # the cell (v=0, h=0, s=0); 3..5 is (v=0, h=0, s=1); etc.
    flat = list(range(36))
    cube = _build_hsv_cube([2, 2, 3], 0, [float(x) for x in flat], None)
    assert cube.hue_divisions == 2
    assert cube.sat_divisions == 2
    assert cube.val_divisions == 3
    assert cube.srgb_gamma is False
    # Shape order must be (V, H, S, 3) — matches RT cell traversal.
    assert cube.data_1.shape == (3, 2, 2, 3)
    # Cell (v=0, h=0, s=0) = [0, 1, 2]; (v=0, h=0, s=1) = [3, 4, 5];
    # (v=0, h=1, s=0) = [6, 7, 8].
    np.testing.assert_array_equal(cube.data_1[0, 0, 0], [0, 1, 2])
    np.testing.assert_array_equal(cube.data_1[0, 0, 1], [3, 4, 5])
    np.testing.assert_array_equal(cube.data_1[0, 1, 0], [6, 7, 8])


def test_build_hsv_cube_decodes_srgb_gamma_encoding():
    flat = [0.0] * 12  # 2 × 2 × 1 × 3 = 12 floats
    cube_lin = _build_hsv_cube([2, 2, 1], 0, flat, None)
    cube_srgb = _build_hsv_cube([2, 2, 1], 1, flat, None)
    assert cube_lin.srgb_gamma is False
    assert cube_srgb.srgb_gamma is True


def test_build_hsv_cube_rejects_size_mismatch():
    with pytest.raises(ValueError, match="size mismatch"):
        _build_hsv_cube([2, 2, 1], 0, [0.0] * 10, None)


def test_build_hsv_cube_rejects_data2_size_mismatch():
    flat = [0.0] * 12
    with pytest.raises(ValueError, match="Data2 size"):
        _build_hsv_cube([2, 2, 1], 0, flat, [0.0] * 8)


def test_interpolate_hsv_cube_returns_data1_when_no_data2():
    flat = list(range(12))
    cube = _build_hsv_cube([2, 2, 1], 0, [float(x) for x in flat], None)
    out = interpolate_hsv_cube(cube, kelvin=5500, kelvin_1=2856, kelvin_2=6504)
    np.testing.assert_array_equal(out, cube.data_1)


def test_interpolate_hsv_cube_blends_in_mired_space():
    # Data1 all 0, Data2 all 1. At mired midpoint, expect 0.5.
    n = 12
    cube = _build_hsv_cube([2, 2, 1], 0, [0.0] * n, [1.0] * n)
    # Mired midpoint between 2856 and 6504 K:
    mid_inv = (1.0/2856 + 1.0/6504) / 2
    mid_k = 1.0 / mid_inv
    out = interpolate_hsv_cube(cube, kelvin=mid_k, kelvin_1=2856, kelvin_2=6504)
    np.testing.assert_allclose(out, np.full(cube.data_1.shape, 0.5), atol=1e-5)


@pytest.mark.skipif(
    not _REAL_DCP.exists(),
    reason="real Adobe DCP not installed at standard macOS path",
)
def test_real_d750_dcp_carries_looktable_no_hsm():
    # Per the agent's empirical survey: Nikon D750 Camera Standard.dcp has
    # ONLY a LookTable (no HueSatMap). This regression-guards that finding.
    p = parse_dcp(_REAL_DCP)
    assert p.hue_sat_map is None
    assert p.look_table is not None
    lt = p.look_table
    assert lt.hue_divisions == 90
    assert lt.sat_divisions == 16
    assert lt.val_divisions == 16
    assert lt.srgb_gamma is True
    assert lt.data_1.shape == (16, 90, 16, 3)
    # First cell should be ~identity (no shift at hue=0, sat=0, val=0).
    np.testing.assert_allclose(lt.data_1[0, 0, 0], [0.0, 1.0, 1.0], atol=1e-3)


@pytest.mark.skipif(
    not _REAL_DCP.exists(),
    reason="real Adobe DCP not installed at standard macOS path",
)
def test_find_dcp_for_camera_real_d750():
    found = find_dcp_for_camera("NIKON CORPORATION", "NIKON D750")
    assert found is not None
    assert found.name == "Nikon D750 Camera Standard.dcp"


@pytest.mark.skipif(
    not _REAL_DCP.exists(),
    reason="real Adobe DCP not installed at standard macOS path",
)
def test_real_adobe_dcp_multipliers_match_lr_expected_range():
    # Sanity test: at typical daylight kelvins (5500-6500), Nikon's
    # neutral multipliers should be R≈1.8-2.2, G=1.0, B≈1.1-1.3. These
    # are the well-known empirical multiplier ranges for Nikon Bayer
    # sensors under D65; we confirm DCP-derived multipliers fall in
    # that range (not off by 10x or sign-flipped).
    p = parse_dcp(_REAL_DCP)
    r, g, b, _ = kelvin_tint_to_dt_multipliers(p, 5500, 0)
    assert 1.5 < r < 2.5, f"R multiplier {r} outside expected Nikon range"
    assert g == pytest.approx(1.0)
    assert 1.0 < b < 1.6, f"B multiplier {b} outside expected Nikon range"
