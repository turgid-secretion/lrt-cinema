"""DCP HSV-cube baker tests.

The baker pipeline (HSV decompose → cube apply → HSV recompose → Resolve
.cube emit) is verifiable WITHOUT requiring a real LR-rendered reference:

  - Identity cube → bit-near-passthrough on the Resolve grid sample points
  - Rotation cube → predictable hue shift on a pure-color sample point

Both tests run in seconds and require no dt-cli; the dt-cli leg is the
integration test in tests/test_dt_integration.py.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from lrt_cinema.dcp import HsvCube
from lrt_cinema.lut3d_baker import (
    RECOMMENDED_CUBE_SIZE,
    _apply_hsv_cube,
    _hsv_to_rgb_dcp,
    _rgb_to_hsv_dcp,
    _srgb_eotf,
    _srgb_oetf,
    bake_dcp_cubes_to_resolve_cube,
)


def _read_resolve_cube(path: Path) -> tuple[int, np.ndarray]:
    """Parse a Resolve `.cube` file → (N, array of shape (N, N, N, 3)).

    Resolve iteration order: R varies fastest, then G, then B. Returns the
    cube in (R, G, B) indexing — i.e. cube[r, g, b] = the cube cell at
    (R_i, G_j, B_k).
    """
    n = None
    rows: list[tuple[float, float, float]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("LUT_3D_SIZE"):
            n = int(line.split()[1])
            continue
        if line.startswith("DOMAIN_"):
            continue
        parts = line.split()
        if len(parts) == 3:
            rows.append((float(parts[0]), float(parts[1]), float(parts[2])))
    if n is None:
        raise ValueError(f"{path}: no LUT_3D_SIZE directive")
    arr = np.zeros((n, n, n, 3), dtype=np.float64)
    idx = 0
    for bi in range(n):
        for gi in range(n):
            for ri in range(n):
                arr[ri, gi, bi] = rows[idx]
                idx += 1
    if idx != n * n * n:
        raise ValueError(f"{path}: expected {n**3} rows, got {idx}")
    return n, arr


# ---------------------------------------------------------------------------
# sRGB transfer-function round-trip
# ---------------------------------------------------------------------------

def test_srgb_transfer_functions_roundtrip():
    # OETF(EOTF(x)) == x within float epsilon, on a sample grid.
    x = np.linspace(0.0, 1.0, 100)
    round_trip = _srgb_oetf(_srgb_eotf(x))
    np.testing.assert_allclose(round_trip, x, atol=1e-6)
    round_trip2 = _srgb_eotf(_srgb_oetf(x))
    np.testing.assert_allclose(round_trip2, x, atol=1e-6)


# ---------------------------------------------------------------------------
# RGB <-> Adobe-DCP HSV
# ---------------------------------------------------------------------------

def test_rgb_to_hsv_dcp_pure_red():
    rgb = np.array([[1.0, 0.0, 0.0]])
    h, s, v, valid = _rgb_to_hsv_dcp(rgb)
    assert h[0] == pytest.approx(0.0, abs=1e-6)
    assert s[0] == pytest.approx(1.0, abs=1e-6)
    assert v[0] == pytest.approx(1.0, abs=1e-6)
    assert valid[0]


def test_rgb_to_hsv_dcp_pure_green_lands_at_sector_2():
    # Hexcone: green = h=2 (sectors 0=R, 1=R→Y, 2=Y→G, ..., R=0/6 wrap).
    rgb = np.array([[0.0, 1.0, 0.0]])
    h, s, v, valid = _rgb_to_hsv_dcp(rgb)
    assert h[0] == pytest.approx(2.0, abs=1e-6)
    assert valid[0]


def test_rgb_to_hsv_dcp_invalid_on_negative():
    # Out-of-gamut sample (one negative component) → valid_mask False.
    rgb = np.array([[1.0, -0.1, 0.5]])
    _, _, _, valid = _rgb_to_hsv_dcp(rgb)
    assert not valid[0]


def test_rgb_hsv_dcp_round_trip_neutral_gray():
    # Gray (R=G=B) → h=0, s=0, v=R; recompose to same RGB.
    rgb = np.array([[0.5, 0.5, 0.5]])
    h, s, v, _ = _rgb_to_hsv_dcp(rgb)
    out = _hsv_to_rgb_dcp(h, s, v)
    np.testing.assert_allclose(out, rgb, atol=1e-6)


def test_rgb_hsv_dcp_round_trip_grid():
    # Sample on a 5×5×5 grid; round-trip must be bit-near-equal everywhere.
    axis = np.linspace(0.0, 1.0, 5)
    R, G, B = np.meshgrid(axis, axis, axis, indexing="ij")
    rgb = np.stack([R, G, B], axis=-1)
    h, s, v, valid = _rgb_to_hsv_dcp(rgb)
    out = _hsv_to_rgb_dcp(h, s, v)
    # All grid points have nonneg components → all valid.
    assert valid.all()
    np.testing.assert_allclose(out, rgb, atol=1e-6)


# ---------------------------------------------------------------------------
# Cube application — identity + rotation
# ---------------------------------------------------------------------------

def test_apply_identity_cube_is_passthrough():
    # Identity cell: hueShift=0, satScale=1, valScale=1. Cube application
    # must leave (h, s, v) unchanged at any sample.
    cube = np.tile(
        np.array([0.0, 1.0, 1.0], dtype=np.float32),
        (2, 6, 2, 1),
    )
    meta = HsvCube(
        hue_divisions=6, sat_divisions=2, val_divisions=2,
        srgb_gamma=False, data_1=cube,
    )
    h_in = np.array([1.5, 3.7, 0.0])
    s_in = np.array([0.5, 0.8, 0.0])
    v_in = np.array([0.5, 0.3, 1.0])
    h_out, s_out, v_out = _apply_hsv_cube(h_in, s_in, v_in, cube, meta)
    np.testing.assert_allclose(h_out, h_in, atol=1e-5)
    np.testing.assert_allclose(s_out, s_in, atol=1e-5)
    np.testing.assert_allclose(v_out, v_in, atol=1e-5)


def test_apply_30deg_hue_rotation_shifts_hue_uniformly():
    # 30° hue shift everywhere. Adobe units: 30/360 × 6 = 0.5 in hexcone.
    cube = np.tile(
        np.array([30.0, 1.0, 1.0], dtype=np.float32),
        (2, 6, 2, 1),
    )
    meta = HsvCube(
        hue_divisions=6, sat_divisions=2, val_divisions=2,
        srgb_gamma=False, data_1=cube,
    )
    h_in = np.array([0.0, 1.5, 3.0])
    s_in = np.array([0.7, 0.7, 0.7])
    v_in = np.array([0.6, 0.6, 0.6])
    h_out, _, _ = _apply_hsv_cube(h_in, s_in, v_in, cube, meta)
    np.testing.assert_allclose(h_out, h_in + 0.5, atol=1e-5)


# ---------------------------------------------------------------------------
# End-to-end baker — identity cube → near-passthrough .cube
# ---------------------------------------------------------------------------

def test_bake_identity_cube_is_near_passthrough(tmp_path):
    """Identity HSM cube must bake to a Resolve cube that is ~identity on
    the Resolve sample grid.

    Trilinear sampling + RGB↔HSV round-trip introduce floating-point
    noise, but a true identity cube must round-trip within ΔE ≪ 1.0 at
    every Resolve cell.
    """
    identity = HsvCube(
        hue_divisions=6, sat_divisions=2, val_divisions=2,
        srgb_gamma=False,
        data_1=np.tile(
            np.array([0.0, 1.0, 1.0], dtype=np.float32),
            (2, 6, 2, 1),
        ),
    )
    out = tmp_path / "identity.cube"
    bake_dcp_cubes_to_resolve_cube(
        out, cube_size=9,
        hsm_blended=identity.data_1, hsm_meta=identity,
        look_blended=None, look_meta=None,
    )
    n, cube = _read_resolve_cube(out)
    assert n == 9
    # Identity expectation: at sample (R, G, B), cube cell = (R, G, B).
    axis = np.linspace(0.0, 1.0, n)
    R, G, B = np.meshgrid(axis, axis, axis, indexing="ij")
    expected = np.stack([R, G, B], axis=-1)
    # Max abs error in any cell of any channel must be tiny — the
    # round-trip RGB→HSV→RGB on a Resolve grid samples + trilinear of an
    # identity cube must net-zero.
    np.testing.assert_allclose(cube, expected, atol=1e-5)


def test_bake_writes_resolve_header(tmp_path):
    identity = HsvCube(
        hue_divisions=6, sat_divisions=2, val_divisions=2,
        srgb_gamma=False,
        data_1=np.tile(
            np.array([0.0, 1.0, 1.0], dtype=np.float32),
            (2, 6, 2, 1),
        ),
    )
    out = tmp_path / "header.cube"
    bake_dcp_cubes_to_resolve_cube(
        out, cube_size=5,
        hsm_blended=identity.data_1, hsm_meta=identity,
        look_blended=None, look_meta=None,
    )
    text = out.read_text()
    assert "LUT_3D_SIZE 5" in text
    assert "DOMAIN_MIN 0.0 0.0 0.0" in text
    assert "DOMAIN_MAX 1.0 1.0 1.0" in text


def test_bake_rejects_empty_inputs(tmp_path):
    with pytest.raises(ValueError, match="at least one of"):
        bake_dcp_cubes_to_resolve_cube(
            tmp_path / "x.cube", cube_size=9,
            hsm_blended=None, hsm_meta=None,
            look_blended=None, look_meta=None,
        )


def test_bake_rejects_out_of_range_size(tmp_path):
    identity = HsvCube(
        hue_divisions=6, sat_divisions=2, val_divisions=2,
        srgb_gamma=False,
        data_1=np.tile(
            np.array([0.0, 1.0, 1.0], dtype=np.float32),
            (2, 6, 2, 1),
        ),
    )
    with pytest.raises(ValueError, match="cube_size"):
        bake_dcp_cubes_to_resolve_cube(
            tmp_path / "x.cube", cube_size=1,
            hsm_blended=identity.data_1, hsm_meta=identity,
            look_blended=None, look_meta=None,
        )
    with pytest.raises(ValueError, match="cube_size"):
        bake_dcp_cubes_to_resolve_cube(
            tmp_path / "x.cube", cube_size=300,
            hsm_blended=identity.data_1, hsm_meta=identity,
            look_blended=None, look_meta=None,
        )


def test_recommended_cube_size_is_resolve_standard():
    # Adobe / Resolve / OCIO standard is 33 — sufficient for visually-
    # lossless representation of a 90×16×16 source HSV cube.
    assert RECOMMENDED_CUBE_SIZE == 33
