"""Unit tests for the demosaic selection/routing helpers in `pipeline` (Phase A4
+ B4 — the clean-room RCD integration). Pure functions, no raw fixtures needed."""

from __future__ import annotations

import numpy as np
import pytest

from lrt_cinema.pipeline import _DEMOSAIC_ALGOS, _bayer_pattern_str


@pytest.mark.parametrize(
    ("pattern", "desc", "want"),
    [
        # Nikon D750: raw_pattern [[0,1],[3,2]], color_desc b"RGBG" → RGGB.
        (np.array([[0, 1], [3, 2]]), b"RGBG", "RGGB"),
        # The four canonical Bayer phases under a plain RGB(G) descriptor.
        (np.array([[0, 1], [1, 2]]), b"RGBG", "RGGB"),
        (np.array([[2, 1], [1, 0]]), b"RGBG", "BGGR"),
        (np.array([[1, 0], [2, 1]]), b"RGBG", "GRBG"),
        (np.array([[1, 2], [0, 1]]), b"RGBG", "GBRG"),
    ],
)
def test_bayer_pattern_str_maps_phases(pattern, desc, want):
    assert _bayer_pattern_str(pattern, desc) == want


def test_bayer_pattern_str_non_bayer_returns_none():
    """A non-2×2 CFA (e.g. Fuji X-Trans 6×6) is not a Bayer phase → None so the
    RCD path falls back to libraw instead of raising."""
    xtrans = np.zeros((6, 6), dtype=int)
    assert _bayer_pattern_str(xtrans, b"RGBG") is None


def test_bayer_pattern_str_accepts_str_desc():
    """color_desc may arrive as str or bytes (libraw returns bytes)."""
    assert _bayer_pattern_str(np.array([[0, 1], [3, 2]]), "RGBG") == "RGGB"


def test_demosaic_algos_table_excludes_rcd():
    """'rcd' is OUR clean-room demosaic, NOT a libraw algorithm — it must be routed
    before `_postprocess_kwargs`/`_resolve_demosaic` (which only know libraw algos),
    so it deliberately is not in the libraw name table."""
    assert "rcd" not in _DEMOSAIC_ALGOS
    assert _DEMOSAIC_ALGOS["linear"] == "LINEAR"
    assert "dcb" in _DEMOSAIC_ALGOS
