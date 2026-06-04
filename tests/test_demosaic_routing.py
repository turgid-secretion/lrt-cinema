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


def test_cfa_demosaics_table():
    """The CFA-domain methods (run on our extracted mosaic, not libraw postprocess)
    are rcd/mlri/menon — disjoint from the libraw-algo table, routed via _cfa_demosaic."""
    from lrt_cinema.pipeline import _CFA_DEMOSAICS

    assert _CFA_DEMOSAICS == ("rcd", "mlri", "menon")
    for m in _CFA_DEMOSAICS:
        assert m not in _DEMOSAIC_ALGOS


def test_cfa_demosaics_preserve_overrange():
    """B1-critical regression: every CFA-domain demosaic must PRESERVE recovered >1
    highlights — else the future mosaic-domain highlight-recon (B1), which extracts
    headroom above the clip, would have its work crushed by the demosaic. Verified on
    a CFA with a >1 blob (the shape B1 produces)."""
    from lrt_cinema import accel
    from lrt_cinema._mlri_demosaic import mlri_demosaic

    cfa = np.full((32, 32), 0.3, dtype=np.float64)
    cfa[8:24, 8:24] = 1.6  # a B1-recovered highlight region (>1)
    cases = [("rcd", lambda c: accel.rcd_demosaic(c, "RGGB")),
             ("mlri", lambda c: mlri_demosaic(c, "RGGB"))]
    try:  # menon is the external BSD-3 quality path (optional dep)
        from colour_demosaicing import demosaicing_CFA_Bayer_Menon2007 as _menon
        cases.append(("menon", lambda c: np.asarray(_menon(c, "RGGB"))))
    except ImportError:
        pass
    for name, fn in cases:
        out = fn(cfa.copy())
        assert np.isfinite(out).all(), name
        assert out.min() >= 0.0, name
        assert out[12:20, 12:20].max() > 1.0, f"{name}: >1 highlight did not survive"
