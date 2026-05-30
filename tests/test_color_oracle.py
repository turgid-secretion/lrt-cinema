"""Layer 1 — implementation-correctness oracles for the colour/transfer path.

The *certitude engine* of the deterministic validation harness (see
`docs/VALIDATION.md`). Ground truth here is the pipeline's OWN defined maths,
re-implemented independently from published matrices and the sRGB spec — NOT via
`colour-science` (which `output.py` uses). So a wrong colourspace name, a
transposed matrix, the wrong chromatic-adaptation transform, or the wrong
transfer function in `output.py` is caught.

This axis is distinct from *absolute colorimetric accuracy* (Layer 2, vs CIE
truth from spectra), which has an irreducible nonzero floor (the DCP matrix is a
least-squares fit — real sensors violate the Luther condition). DO NOT conflate:
- implementation correctness → expected ~0 (matrix-rounding tolerance);
- absolute accuracy → expected nonzero (profile-fit floor).

Coverage is the full value range *including extremes* (near-black, the sRGB
toe/knee, clip, overrange >1, primaries, out-of-gamut) — a spectral chart under
a normal illuminant can't reach those; deliberate value injection (here) does.
"""

from __future__ import annotations

import numpy as np
import pytest

colour = pytest.importorskip("colour")  # noqa: F841  (output.py needs it; gate import)

from lrt_cinema.output import _prophoto_to_display, write_tiff_display  # noqa: E402

# ---------------------------------------------------------------------------
# Independent, spec-sourced re-implementation (the oracle)
# ---------------------------------------------------------------------------

# ROMM/ProPhoto linear → XYZ(D50).
_M_PP_LIN_TO_XYZ_D50 = np.array([
    [0.7976749, 0.1351917, 0.0313534],
    [0.2880402, 0.7118741, 0.0000857],
    [0.0000000, 0.0000000, 0.8252100],
])
# Bradford chromatic adaptation D50 → D65.
_M_BRADFORD_D50_TO_D65 = np.array([
    [0.9555766, -0.0230393, 0.0631636],
    [-0.0282895, 1.0099416, 0.0210077],
    [0.0122982, -0.0204830, 1.3299098],
])
# XYZ(D65) → linear sRGB (IEC 61966-2-1).
_M_XYZ_D65_TO_SRGB = np.array([
    [3.2404542, -1.5371385, -0.4985314],
    [-0.9692660, 1.8760108, 0.0415560],
    [0.0556434, -0.2040259, 1.0572252],
])

# Agreement floor between these 7-digit matrices and colour-science's
# full-precision path. Measured worst-case 3.6e-4 linear; a real bug
# (transpose / wrong CAT / wrong transfer) is 0.01–0.5, orders above this.
_ORACLE_TOL = 2e-3


def _srgb_oetf(linear: np.ndarray) -> np.ndarray:
    """Analytic sRGB opto-electronic transfer function (IEC 61966-2-1)."""
    linear = np.asarray(linear, dtype=np.float64)
    return np.where(
        linear <= 0.0031308,
        12.92 * linear,
        1.055 * np.power(np.clip(linear, 0.0, None), 1.0 / 2.4) - 0.055,
    )


def _oracle_prophoto_to_srgb_linear(pp: np.ndarray) -> np.ndarray:
    pp = np.asarray(pp, dtype=np.float64)
    xyz_d50 = pp @ _M_PP_LIN_TO_XYZ_D50.T
    xyz_d65 = xyz_d50 @ _M_BRADFORD_D50_TO_D65.T
    return xyz_d65 @ _M_XYZ_D65_TO_SRGB.T


def _oracle_prophoto_to_srgb_encoded(pp: np.ndarray) -> np.ndarray:
    return _srgb_oetf(_oracle_prophoto_to_srgb_linear(pp))


# ---------------------------------------------------------------------------
# Guard the oracle itself (an oracle that is wrong is worse than none)
# ---------------------------------------------------------------------------


def test_oracle_neutral_axis_is_pure_oetf():
    """A linear neutral v (R=G=B) maps to sRGB neutral OETF(v) — both ProPhoto
    and sRGB normalise their own white to 1, so the achromatic axis is
    transfer-only. Independent of the matrices; pins the oracle's correctness."""
    for v in (0.0, 0.0031308, 0.05, 0.18, 0.5, 1.0):
        enc = _oracle_prophoto_to_srgb_encoded(np.array([[[v, v, v]]]))[0, 0]
        np.testing.assert_allclose(enc, _srgb_oetf(v), atol=1.5e-3)
        np.testing.assert_allclose(enc[0], enc[1], atol=1.5e-3)  # stays neutral


def test_oracle_known_srgb_values():
    """Hand-checked: OETF(0.18) ≈ 0.4614, OETF(1.0) = 1.0, OETF(0) = 0."""
    enc = _oracle_prophoto_to_srgb_encoded(
        np.array([[[0.18, 0.18, 0.18], [1.0, 1.0, 1.0], [0.0, 0.0, 0.0]]]),
    )[0]
    np.testing.assert_allclose(enc[0], 0.4614, atol=2e-3)
    np.testing.assert_allclose(enc[1], 1.0, atol=2e-3)
    np.testing.assert_allclose(enc[2], 0.0, atol=1e-6)


# ---------------------------------------------------------------------------
# Implementation correctness: output._prophoto_to_display == oracle (~0)
# ---------------------------------------------------------------------------


def test_display_transform_matches_oracle_in_gamut():
    """The real sRGB display transform must equal the independent oracle across
    in-gamut neutrals + mild colours, to matrix-rounding tolerance."""
    pp = np.array([[
        [0.05, 0.05, 0.05], [0.18, 0.18, 0.18], [0.5, 0.5, 0.5],
        [1.0, 1.0, 1.0], [0.35, 0.22, 0.14], [0.6, 0.55, 0.2],
    ]])
    got = _prophoto_to_display(pp, "srgb")
    want = _oracle_prophoto_to_srgb_encoded(pp)
    np.testing.assert_allclose(got, want, atol=_ORACLE_TOL)


def test_display_transform_near_black_toe():
    """The sRGB toe (linear ≤ 0.0031308, slope 12.92) is where small errors
    amplify — pin it explicitly."""
    pp = np.array([[[1e-4, 1e-4, 1e-4], [2e-3, 2e-3, 2e-3], [3e-3, 3e-3, 3e-3]]])
    np.testing.assert_allclose(
        _prophoto_to_display(pp, "srgb"),
        _oracle_prophoto_to_srgb_encoded(pp),
        atol=_ORACLE_TOL,
    )


def test_write_tiff_display_quantizes_per_oracle(tmp_path):
    """End-to-end through the real writer: the 16-bit integers must match the
    oracle (encoded → clip [0,1] → round).

    Tolerance is the oracle's matrix-rounding floor — the 7-digit published
    matrices here disagree with colour-science's full-precision path by ~1.4e-4
    (≈9 LSB of 65535). That is the price of an *independent* oracle, NOT pipeline
    error; any real transfer/matrix bug is ~1300+ LSB, two orders above this."""
    tifffile = pytest.importorskip("tifffile")
    pp = np.array([[
        [0.18, 0.18, 0.18], [0.5, 0.5, 0.5], [0.02, 0.02, 0.02], [0.9, 0.9, 0.9],
    ]])
    dst = write_tiff_display(pp, tmp_path / "q.tif", colorspace="srgb", bit_depth=16)
    got = tifffile.imread(str(dst)).astype(np.int64)
    want = np.round(np.clip(_oracle_prophoto_to_srgb_encoded(pp), 0, 1) * 65535).astype(np.int64)
    assert np.max(np.abs(got - want)) <= 16, f"max LSB error {np.max(np.abs(got - want))}"


def test_write_tiff_display_extremes_clip(tmp_path):
    """Overrange highlights clip to white; sub-black clips to 0; no NaN/crash."""
    tifffile = pytest.importorskip("tifffile")
    pp = np.array([[[4.0, 4.0, 4.0], [-0.2, -0.2, -0.2], [0.0, 0.0, 0.0]]])
    rt = tifffile.imread(str(write_tiff_display(pp, tmp_path / "x.tif")))
    assert rt[0, 0, 0] == 65535  # +overrange → white
    assert rt[0, 1, 0] == 0      # sub-black → 0
    assert rt[0, 2, 0] == 0


# ---------------------------------------------------------------------------
# Sensitivity: the oracle MUST flag a deliberately injected bug (else it is a
# rubber stamp). Proves discriminating power per the harness design bar.
# ---------------------------------------------------------------------------


def test_oracle_detects_wrong_transfer():
    """If the transfer were a plain 2.2 gamma instead of the sRGB OETF, the
    oracle would diverge far above tolerance — confirms it would catch it."""
    pp = np.array([[[0.18, 0.18, 0.18], [0.5, 0.5, 0.5]]])
    correct = _oracle_prophoto_to_srgb_encoded(pp)
    wrong = np.power(np.clip(_oracle_prophoto_to_srgb_linear(pp), 0, 1), 1 / 2.2)
    assert np.max(np.abs(correct - wrong)) > 5e-3


def test_oracle_detects_transposed_matrix():
    """A transposed XYZ→sRGB matrix (a classic real bug) diverges grossly."""
    pp = np.array([[[0.35, 0.22, 0.14], [0.18, 0.18, 0.18]]])
    correct = _oracle_prophoto_to_srgb_linear(pp)
    xyz_d50 = pp @ _M_PP_LIN_TO_XYZ_D50.T
    xyz_d65 = xyz_d50 @ _M_BRADFORD_D50_TO_D65.T
    wrong = xyz_d65 @ _M_XYZ_D65_TO_SRGB  # NOT transposed → bug
    assert np.max(np.abs(correct - wrong)) > 5e-2
