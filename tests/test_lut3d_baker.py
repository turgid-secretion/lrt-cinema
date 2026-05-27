"""DCP HSV-cube primitive tests (sRGB OETF/EOTF, RGB↔HSV, _apply_hsv_cube).

Cube application is verifiable WITHOUT a real LR-rendered reference:
identity cube → exact passthrough, rotation cube → predictable hue shift.
"""

from __future__ import annotations

import numpy as np
import pytest

from lrt_cinema.dcp import HsvCube
from lrt_cinema.lut3d_baker import (
    _apply_hsv_cube,
    _hsv_to_rgb_dcp,
    _rgb_to_hsv_dcp,
    _srgb_eotf,
    _srgb_oetf,
)

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


