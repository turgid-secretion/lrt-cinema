"""Slot-6 false-colour suppression — synthetic unit contracts (CI).

The scheme (canon chroma-difference median, `lrt_cinema._fc_suppress`):
per pass, for c ∈ {R, B}: c ← max(0, median3×3(c − G) + G). Contracts:
strict no-op at 0 passes; G never modified; flat fields invariant; isolated
chroma outliers (the demosaic-false-colour model) removed; luma edges
shared by all channels survive (they cancel in the difference domain).
"""

from __future__ import annotations

import numpy as np
import pytest

from lrt_cinema._fc_suppress import MAX_PASSES, suppress_false_colour


def test_zero_passes_is_same_object_noop():
    rgb = np.random.default_rng(0).random((16, 16, 3)).astype(np.float32)
    assert suppress_false_colour(rgb, passes=0) is rgb


def test_too_many_passes_raises():
    rgb = np.zeros((8, 8, 3), dtype=np.float32)
    with pytest.raises(ValueError, match="passes"):
        suppress_false_colour(rgb, passes=MAX_PASSES + 1)


def test_flat_field_is_invariant():
    rgb = np.full((24, 24, 3), 0.25, dtype=np.float32)
    rgb[..., 0] = 0.4  # chromatic but FLAT — constant difference medians to itself
    out = suppress_false_colour(rgb, passes=3)
    np.testing.assert_allclose(out, rgb, atol=1e-6)


def test_green_channel_is_never_modified():
    rgb = np.random.default_rng(1).random((20, 20, 3)).astype(np.float32)
    out = suppress_false_colour(rgb, passes=2)
    np.testing.assert_array_equal(out[..., 1], rgb[..., 1])


def test_isolated_chroma_outlier_is_removed():
    """The demosaic-false-colour model: a lone pixel whose R deviates from
    the local R−G relation (invented colour) is pulled back to it."""
    rgb = np.full((21, 21, 3), 0.3, dtype=np.float32)
    rgb[10, 10, 0] = 0.9  # isolated invented-red pixel
    out = suppress_false_colour(rgb, passes=1)
    assert abs(out[10, 10, 0] - 0.3) < 1e-6, "outlier should median away"
    # neighbours untouched
    np.testing.assert_allclose(out[:9], rgb[:9], atol=1e-6)


def test_shared_luma_edge_survives():
    """A step edge present in ALL channels cancels in the difference domain
    and must pass through unchanged (the no-resolution-cost property)."""
    rgb = np.full((20, 20, 3), 0.1, dtype=np.float32)
    rgb[:, 10:] = 0.8  # neutral step edge
    out = suppress_false_colour(rgb, passes=3)
    np.testing.assert_allclose(out, rgb, atol=1e-6)


def test_output_nonnegative_and_finite():
    rng = np.random.default_rng(2)
    rgb = (rng.random((32, 32, 3)) * 1.5).astype(np.float32)  # incl. >1 headroom
    out = suppress_false_colour(rgb, passes=2)
    assert np.isfinite(out).all() and (out >= 0).all()


def test_blur_variant_contracts_hold():
    """The RT-style blur refinement keeps every structural contract: G
    untouched, flat fields invariant, shared luma edges intact, output
    sane. (The blur acts only on the chroma differences.)"""
    rgb = np.full((20, 20, 3), 0.1, dtype=np.float32)
    rgb[:, 10:] = 0.8                       # neutral step edge
    out = suppress_false_colour(rgb, passes=3, blur=True)
    np.testing.assert_allclose(out, rgb, atol=1e-6)

    rng = np.random.default_rng(5)
    rnd = (rng.random((24, 24, 3)) * 1.4).astype(np.float32)
    out2 = suppress_false_colour(rnd, passes=2, blur=True)
    np.testing.assert_array_equal(out2[..., 1], rnd[..., 1])
    assert np.isfinite(out2).all() and (out2 >= 0).all()
