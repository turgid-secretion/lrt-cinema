"""Slot-2.5 hot-pixel suppression — synthetic unit contracts (CI).

dt's `hotpixels` Bayer stage (`lrt_cinema._hotpixels.fix_hot_pixels`).
Detection semantics: pixel v (> threshold 0.05) is hot when >= 4 (3 with
permissive) same-channel cardinal neighbours sit BELOW v*strength/2
(default multiplier 0.125) — so test backgrounds must sit under that
mid line. Contracts: impulse replaced by its brightest dark neighbour;
strict/permissive counts gate (incl. the adjacent-pair case permissive
exists for); the threshold floor gates; smooth content, borders and
strength-0 untouched; the pipeline CFA path routes the flag.
"""

from __future__ import annotations

import numpy as np
import pytest

from lrt_cinema._hotpixels import fix_hot_pixels

_BG = 0.08  # under 0.9 * 0.125 = 0.1125 → counts as a dark neighbour


def _flat(v: float = _BG, n: int = 16) -> np.ndarray:
    return np.full((n, n), v, dtype=np.float32)


def test_isolated_impulse_replaced_by_brightest_dark_neighbour():
    m = _flat()
    m[8, 8] = 0.9                      # hot: mid = 0.1125 > 0.08 neighbours
    m[8, 10] = 0.1                     # the brightest same-channel neighbour
    out, fixed = fix_hot_pixels(m)
    assert fixed == 1
    assert out[8, 8] == np.float32(0.1)
    mask = np.ones_like(m, bool)
    mask[8, 8] = False
    np.testing.assert_array_equal(out[mask], m[mask])


def test_below_threshold_is_untouched():
    m = _flat(0.001)
    m[8, 8] = 0.04                     # impulse, but under the 0.05 floor
    out, fixed = fix_hot_pixels(m)
    assert fixed == 0
    np.testing.assert_array_equal(out, m)


def test_three_dark_neighbours_needs_permissive():
    """The adjacent hot-pixel PAIR case: each member sees 3 dark + 1 hot
    same-channel neighbour — strict (4) leaves it, permissive (3) fixes."""
    m = _flat()
    m[8, 8] = 0.9
    m[8, 10] = 0.9                     # its same-channel pair partner
    out, fixed = fix_hot_pixels(m)
    assert fixed == 0
    np.testing.assert_array_equal(out, m)
    out_p, fixed_p = fix_hot_pixels(m, permissive=True)
    assert fixed_p == 2
    assert out_p[8, 8] == np.float32(_BG)
    assert out_p[8, 10] == np.float32(_BG)


def test_neighbours_read_from_input_no_cascade():
    """Corrections must not cascade within a pass (dt reads the input
    buffer): fixing one pixel does not alter its neighbour's decision."""
    m = _flat()
    m[8, 8] = 0.9
    m[12, 8] = 0.9                     # 4 rows apart: shares one neighbour site
    out, fixed = fix_hot_pixels(m)
    assert fixed == 2
    assert out[8, 8] == out[12, 8] == np.float32(_BG)


def test_border_two_px_untouched():
    m = _flat()
    m[1, 8] = 0.9                      # inside the 2-px border → not scanned
    m[8, 1] = 0.9
    out, fixed = fix_hot_pixels(m)
    assert fixed == 0
    np.testing.assert_array_equal(out, m)


def test_smooth_gradient_invariant():
    g = np.linspace(0.05, 0.9, 64, dtype=np.float32)
    m = np.repeat(g[None, :], 64, axis=0).copy()
    out, fixed = fix_hot_pixels(m)
    assert fixed == 0
    np.testing.assert_array_equal(out, m)


def test_strength_zero_is_noop_and_validation():
    m = _flat()
    m[8, 8] = 0.9
    out, fixed = fix_hot_pixels(m, strength=0.0)
    assert fixed == 0
    np.testing.assert_array_equal(out, m)
    with pytest.raises(ValueError, match="2-D"):
        fix_hot_pixels(np.zeros((4, 4, 3), np.float32))
    with pytest.raises(ValueError, match="strength"):
        fix_hot_pixels(m, strength=1.5)


def test_pipeline_cfa_path_routes_hotpixels():
    from test_ca_correct import _FakeRaw

    from lrt_cinema.pipeline import _cfa_demosaic

    rng = np.random.default_rng(7)
    base = (rng.random((256, 256)).astype(np.float32) * 0.02 + 0.05)
    base[100, 100] = 0.95              # isolated impulse over a dark field
    raw = _FakeRaw((base * 16383.0 + 0.5).astype(np.uint16))
    wb = np.array([1.0, 1.0, 1.0], np.float32)
    off = _cfa_demosaic(raw, "rcd", wb, "clip")
    on = _cfa_demosaic(raw, "rcd", wb, "clip", hotpixels=0.25)
    assert on.shape == off.shape
    # the impulse region changes; a far region does not
    assert float(np.abs(on[95:106, 95:106] - off[95:106, 95:106]).max()) > 0.01
    np.testing.assert_array_equal(on[:40, :40], off[:40, :40])
