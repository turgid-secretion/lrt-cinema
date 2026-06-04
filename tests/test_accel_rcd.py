"""Backend equivalence: the numba RCD demosaic kernel vs the numpy reference.

The numba RCD core (`accel._numba_kernels.rcd_rggb`, dispatched by
`accel.rcd_demosaic`) is a fused float64 port of `_rcd_demosaic._rcd_rggb`. Its
contract is that it is **colour-identical to the numpy reference** (which is the
Stage-1 demosaic the rest of the pipeline + the ΔE ship gate assume). This is the
fixture-free guard for that contract: it mosaics synthetic structure (a slanted
edge, a radial chirp, a luma gradient) AND random CFAs to a single-channel Bayer
plane for every one of the 4 phases, demosaics on both backends, and asserts a
near-exact match (float64 → float64, so `max|Δ| < 1e-9`), plus the reference's
finite / non-negative / shape contract. No `/tmp/dng_out` fixtures, no system DCP;
skips cleanly when numba is absent.

The reconstruction-QUALITY proof (RCD beats bilinear by a PSNR margin) lives in
`tests/test_rcd_demosaic.py`; this isolates the numpy↔numba equivalence.
"""

from __future__ import annotations

import numpy as np
import pytest

from lrt_cinema import accel

pytestmark = pytest.mark.skipif(
    not accel.numba_available(), reason="numba not installed (numpy-only build)",
)

# float64 reference → float64 kernel: the only divergence is FP reassociation,
# which `fastmath=False` + matched operation order keeps near machine epsilon.
_TOL = 1e-9

_PHASES = ("RGGB", "BGGR", "GRBG", "GBRG")

# Per phase: the channel (0=R,1=G,2=B) sampled at the 4 sub-positions (0,0) (0,1)
# (1,0) (1,1) of the 2×2 tile — same convention as tests/test_rcd_demosaic.py.
_PHASE_CHANNEL = {
    "RGGB": (0, 1, 1, 2),
    "BGGR": (2, 1, 1, 0),
    "GRBG": (1, 0, 2, 1),
    "GBRG": (1, 2, 0, 1),
}


def _mosaic(img: np.ndarray, pattern: str) -> np.ndarray:
    """Sample one channel per pixel per the Bayer ``pattern`` → 2-D CFA."""
    h, w, _ = img.shape
    cfa = np.empty((h, w), dtype=img.dtype)
    c00, c01, c10, c11 = _PHASE_CHANNEL[pattern]
    cfa[0::2, 0::2] = img[0::2, 0::2, c00]
    cfa[0::2, 1::2] = img[0::2, 1::2, c01]
    cfa[1::2, 0::2] = img[1::2, 0::2, c10]
    cfa[1::2, 1::2] = img[1::2, 1::2, c11]
    return cfa


def _structured_rgb(n: int = 96) -> np.ndarray:
    """A synthetic RGB image with real spatial structure (a diagonal edge, a
    radial chirp, and a luma gradient, tinted per channel) — exercises the
    directional green decision (H/V/diagonal branches) and the color-difference
    fill on edges, not just the flat interior a constant patch would test."""
    yy, xx = np.indices((n, n)).astype(np.float64)
    cy, cx = (n - 1) / 2.0, (n - 1) / 2.0
    r2 = (yy - cy) ** 2 + (xx - cx) ** 2
    luma = 0.45 + 0.25 * np.cos(0.0016 * r2)            # radial chirp
    luma += 0.18 * ((xx * 1.0 + yy * 0.35) > (n * 0.55))  # diagonal step edge
    luma += 0.10 * np.sin(2.0 * np.pi * xx / n)         # smooth gradient
    luma = np.clip(luma, 0.0, 1.0)
    return np.stack([np.clip(luma * c, 0.0, 1.0) for c in (1.0, 0.82, 0.6)], axis=2)


def _assert_parity(cfa: np.ndarray, pattern: str) -> float:
    ref = accel.rcd_demosaic(cfa, pattern, backend="numpy")
    got = accel.rcd_demosaic(cfa, pattern, backend="numba")
    assert got.shape == ref.shape == (cfa.shape[0], cfa.shape[1], 3)
    assert got.dtype == ref.dtype
    assert np.isfinite(got).all()
    assert (got >= 0.0).all()
    max_diff = float(np.max(np.abs(ref.astype(np.float64) - got.astype(np.float64))))
    assert max_diff < _TOL, f"RCD kernel diverges ({pattern}): max |Δ| = {max_diff:.2e}"
    return max_diff


@pytest.mark.parametrize("pattern", _PHASES)
def test_rcd_numba_matches_numpy_random(pattern: str) -> None:
    """Random CFA → numba RCD == numpy reference (all 4 phases)."""
    rng = np.random.default_rng(20260603 + hash(pattern) % 1000)
    cfa = rng.random((96, 80), dtype=np.float64)
    _assert_parity(cfa, pattern)


@pytest.mark.parametrize("pattern", _PHASES)
def test_rcd_numba_matches_numpy_structured(pattern: str) -> None:
    """Structured CFA (edge + chirp + gradient) → numba RCD == numpy reference.

    The structured content is what makes the green-direction branches and the
    color-difference fill actually fire (a flat patch leaves them on the
    degenerate path), so this is the load-bearing equivalence case."""
    img = _structured_rgb()
    cfa = _mosaic(img, pattern)
    _assert_parity(cfa, pattern)


def test_rcd_numba_matches_numpy_overrange() -> None:
    """Highlights (>1.0) survive identically on both backends — the demosaic does
    not cap the top, and the numba port must preserve that headroom bit-for-bit."""
    rng = np.random.default_rng(7)
    cfa = rng.random((64, 64), dtype=np.float64) * 1.4   # values up to ~1.4
    md = _assert_parity(cfa, "RGGB")
    got = accel.rcd_demosaic(cfa, "RGGB", backend="numba")
    assert got.max() > 1.0, "overrange highlights were crushed by the kernel"
    assert md < _TOL


def test_rcd_numba_float32_in_float32_out() -> None:
    """float32 CFA → float32 out on numba (the renderer feeds float32), matching
    the numpy reference's dtype family and values within the float32 round."""
    img = _structured_rgb().astype(np.float32)
    cfa = _mosaic(img, "RGGB")
    ref = accel.rcd_demosaic(cfa, "RGGB", backend="numpy")
    got = accel.rcd_demosaic(cfa, "RGGB", backend="numba")
    assert got.dtype == np.float32 == ref.dtype
    assert np.isfinite(got).all() and (got >= 0.0).all()
    # Both cast the same float64 result to float32 → identical bits.
    np.testing.assert_array_equal(got, ref)


def test_rcd_numpy_branch_is_the_reference() -> None:
    """accel.rcd_demosaic(..., backend='numpy') == the literal
    `_rcd_demosaic.rcd_demosaic` reference (no drift in the dispatch wrapper)."""
    from lrt_cinema._rcd_demosaic import rcd_demosaic as ref_fn
    img = _structured_rgb()
    for pattern in _PHASES:
        cfa = _mosaic(img, pattern)
        np.testing.assert_array_equal(
            accel.rcd_demosaic(cfa, pattern, backend="numpy"),
            ref_fn(cfa, pattern),
            err_msg=f"numpy dispatch != reference for {pattern}",
        )


def test_rcd_dispatch_rejects_bad_input_on_numba() -> None:
    """The numba dispatch reuses the reference's guards (bad pattern / non-2-D /
    odd dims all raise) — a malformed CFA never reaches the kernel."""
    with pytest.raises(ValueError):
        accel.rcd_demosaic(np.zeros((8, 8)), "XYZW", backend="numba")
    with pytest.raises(ValueError):
        accel.rcd_demosaic(np.zeros((8, 8, 3)), "RGGB", backend="numba")
    with pytest.raises(ValueError):
        accel.rcd_demosaic(np.zeros((7, 8)), "RGGB", backend="numba")
