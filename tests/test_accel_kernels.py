"""Backend equivalence: numba kernels vs the numpy reference (Axis-1-style).

The numba backend's contract is that it is colour-identical to the numpy
reference (which is what the ΔE ship gate measures). This is the fixture-free
guard for that contract: it builds adversarial synthetic pixels — random,
overrange (>1), out-of-gamut (negative → the cube's passthrough branch), and
exact channel ties (the tone curve's sort/scatter edge) — and asserts the
numba kernel matches the numpy twin to far below the ΔE floor. No `/tmp/dng_out`
fixtures, no system DCP; runs in CI wherever numba is installed (skips cleanly
when it is not).

The end-to-end ΔE2000-on-a-real-frame proof lives in
`tools/perf/bench_render.py verify`; this isolates the kernels.
"""

from __future__ import annotations

import numpy as np
import pytest

from lrt_cinema import accel

pytestmark = pytest.mark.skipif(
    not accel.numba_available(), reason="numba not installed (numpy-only build)",
)

# Linear-ProPhoto [0,1] values; a kernel diff this small is ~1e-4 of a 16-bit
# code unit — orders below the 1.0 ΔE2000 ship gate.
_TOL = 1e-4


def _adversarial_pixels(seed: int = 0) -> np.ndarray:
    """(N, 3) float32 covering the branches the kernels must get right.

    Includes a NEAR-BLACK block (log-spaced neutrals down to 1e-5 + near-black
    chromatic slivers) — the L<0.01 region a uniform ``rand`` (median ~0.5) never
    samples, and exactly the region the perf kernels were NOT exercised in
    (synthetic-pixel-tested only). The faithful kernels carry no near-black bug
    (that is the perceptual path), but they MUST stay numpy-identical and
    neutral-preserving down here too — this block makes the equivalence + the
    near-black neutral-preservation tests below cover it."""
    rng = np.random.default_rng(seed)
    nb_neutral = np.geomspace(1e-5, 1e-2, 30, dtype=np.float32)[:, None] * np.ones(3, np.float32)
    nb_chromatic = nb_neutral[:20] * np.array([1.0, 0.6, 0.3], np.float32)  # near-black slivers
    blocks = [
        rng.random((4000, 3), dtype=np.float32),                 # in-range
        rng.random((1000, 3), dtype=np.float32) * 1.6,           # overrange (>1)
        rng.random((1000, 3), dtype=np.float32) * 1.3 - 0.3,     # some negatives
        np.zeros((10, 3), dtype=np.float32),                     # black
        np.ones((10, 3), dtype=np.float32),                      # white (clipped)
        np.full((10, 3), 0.5, dtype=np.float32),                 # neutral grey (r==g==b)
        nb_neutral,                                              # near-black neutrals
        nb_chromatic,                                            # near-black chromatic
    ]
    px = np.concatenate(blocks, axis=0)
    # Force exact two-channel ties (the argsort/scatter edge in the tone curve).
    px[:200, 1] = px[:200, 0]              # r == g
    px[200:400, 2] = px[200:400, 0]        # r == b
    px[400:600, 2] = px[400:600, 1]        # g == b
    return np.ascontiguousarray(px, dtype=np.float32)


def _nearblack_neutral_wedge() -> np.ndarray:
    """A pure-neutral wedge (r==g==b) from deep near-black to mid-tone — for the
    near-black neutral-PRESERVATION invariant (distinct from raw equivalence)."""
    v = np.geomspace(1e-5, 0.5, 48, dtype=np.float32)
    return np.ascontiguousarray(np.stack([v, v, v], axis=-1))


def _synthetic_cube(srgb_gamma: bool, seed: int = 1):
    """A random but well-formed HSV cube + meta: channel 0 = hue shift (deg),
    1 = sat scale (~1), 2 = val scale (~1). Dims deliberately differ per axis."""
    from lrt_cinema.dcp import HsvCube

    rng = np.random.default_rng(seed)
    v_div, h_div, s_div = 8, 12, 6
    data = np.empty((v_div, h_div, s_div, 3), dtype=np.float32)
    data[..., 0] = rng.uniform(-40.0, 40.0, (v_div, h_div, s_div))   # hue shift°
    data[..., 1] = rng.uniform(0.6, 1.4, (v_div, h_div, s_div))      # sat scale
    data[..., 2] = rng.uniform(0.6, 1.4, (v_div, h_div, s_div))      # val scale
    meta = HsvCube(
        hue_divisions=h_div, sat_divisions=s_div, val_divisions=v_div,
        srgb_gamma=srgb_gamma, data_1=data,
    )
    return data, meta


# --- HSV cube (Stage 5 / 8) -------------------------------------------------


@pytest.mark.parametrize("srgb_gamma", [True, False])
def test_hsv_cube_numba_matches_numpy(srgb_gamma):
    px = _adversarial_pixels()
    cube, meta = _synthetic_cube(srgb_gamma)
    ref = accel.apply_hsv_cube_rgb(px, cube, meta, backend="numpy")
    got = accel.apply_hsv_cube_rgb(px, cube, meta, backend="numba")
    assert ref.shape == got.shape == px.shape
    assert np.isfinite(got).all()
    max_diff = float(np.max(np.abs(ref.astype(np.float64) - got.astype(np.float64))))
    assert max_diff < _TOL, f"HSV cube kernel diverges: max |Δ| = {max_diff:.2e}"


def test_hsv_cube_negative_pixels_passthrough_both_backends():
    """A pixel with any negative component is returned unchanged (matrix-only
    fallback) on BOTH backends — the reference's `valid_mask` semantics."""
    px = np.array([[-0.1, 0.5, 0.5], [0.2, -0.3, 0.4]], dtype=np.float32)
    cube, meta = _synthetic_cube(srgb_gamma=True)
    for be in ("numpy", "numba"):
        out = accel.apply_hsv_cube_rgb(px, cube, meta, backend=be)
        np.testing.assert_array_equal(out, px, err_msg=f"backend={be}")


def test_hsv_cube_numpy_branch_is_the_reference_composition():
    """accel's numpy branch == the literal Stage-8 composition (no drift)."""
    from lrt_cinema.lut3d_baker import (
        _apply_hsv_cube,
        _hsv_to_rgb_dcp,
        _rgb_to_hsv_dcp,
    )
    px = _adversarial_pixels()
    cube, meta = _synthetic_cube(srgb_gamma=True)
    h, s, v, valid = _rgb_to_hsv_dcp(px)
    h, s, v = _apply_hsv_cube(h, s, v, cube, meta)
    manual = np.where(valid[..., None], _hsv_to_rgb_dcp(h, s, v), px)
    via_accel = accel.apply_hsv_cube_rgb(px, cube, meta, backend="numpy")
    np.testing.assert_array_equal(via_accel, manual)


# --- tone curve (Stage 9) ---------------------------------------------------


def _solver():
    from lrt_cinema.pipeline import DngSplineSolver
    xs = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
    ys = np.array([0.0, 0.18, 0.45, 0.82, 1.0])   # a plausible S-ish tone curve
    return DngSplineSolver(xs, ys)


def test_rgb_tone_numba_matches_numpy():
    px = _adversarial_pixels(seed=7)
    solver = _solver()
    ref = accel.apply_rgb_tone(px, solver, backend="numpy")
    got = accel.apply_rgb_tone(px, solver, backend="numba")
    assert ref.shape == got.shape == px.shape
    assert got.dtype == np.float32
    max_diff = float(np.max(np.abs(ref.astype(np.float64) - got.astype(np.float64))))
    assert max_diff < _TOL, f"tone kernel diverges: max |Δ| = {max_diff:.2e}"


def test_rgb_tone_numpy_branch_is_the_reference():
    """accel's numpy tone branch == pipeline.apply_rgb_tone(rgb, solver.evaluate)."""
    from lrt_cinema.pipeline import apply_rgb_tone
    px = _adversarial_pixels(seed=7)
    solver = _solver()
    np.testing.assert_array_equal(
        accel.apply_rgb_tone(px, solver, backend="numpy"),
        apply_rgb_tone(px, solver.evaluate),
    )


def test_rgb_tone_preserves_neutrals_on_numba():
    """Neutral pixels (r==g==b) map to curve(v) on all channels (no hue/sat)."""
    v = np.linspace(0.0, 1.0, 64, dtype=np.float32)
    px = np.stack([v, v, v], axis=-1)
    solver = _solver()
    out = accel.apply_rgb_tone(px, solver, backend="numba")
    assert np.allclose(out[:, 0], out[:, 1]) and np.allclose(out[:, 1], out[:, 2])
    expected = np.clip(solver.evaluate(v), 0, 1).astype(np.float32)
    assert np.max(np.abs(out[:, 0].astype(np.float64) - expected)) < _TOL


# --- real LookTable cube (committed fixture) when present -------------------


def test_hsv_cube_matches_on_real_looktable():
    """Equivalence on the real D750 LookTable (90×16×16, srgb_gamma) if the
    committed profile fixture is present — exercises the production cube dims."""
    from pathlib import Path
    npz = Path("tests/fixtures/dcp_data/Nikon D750 Camera Standard.npz")
    if not npz.is_file():
        pytest.skip("D750 profile fixture absent")
    from lrt_cinema.dcp import load_profile
    lt = load_profile(npz).look_table
    if lt is None:
        pytest.skip("profile has no LookTable")
    px = _adversarial_pixels(seed=3)
    ref = accel.apply_hsv_cube_rgb(px, lt.data_1, lt, backend="numpy")
    got = accel.apply_hsv_cube_rgb(px, lt.data_1, lt, backend="numba")
    max_diff = float(np.max(np.abs(ref.astype(np.float64) - got.astype(np.float64))))
    assert max_diff < _TOL, f"real LookTable kernel diverges: {max_diff:.2e}"


# --- dispatcher API ---------------------------------------------------------


def test_resolve_backend_rules():
    assert accel.resolve_backend("numpy") == "numpy"
    assert accel.resolve_backend("numba") == "numba"   # numba is available here
    assert accel.resolve_backend("auto") == "numba"
    with pytest.raises(ValueError):
        accel.resolve_backend("cuda")


def test_set_threads_is_safe():
    accel.set_threads(1)
    accel.set_threads(9999)   # clamped to the launch maximum, no raise


# --- Stage-12 faithful grade ops (numba vs numpy) --------------------------


def _hsl_bands_op():
    from lrt_cinema.ir import HslBands
    return HslBands(hue=(5, -5, 0, 10, 0, -8, 0, 3),
                    saturation=(10, 0, -10, 5, 0, 8, 0, 0),
                    luminance=(0, 5, -5, 0, 3, -3, 0, 0))


def _color_grade_op():
    from lrt_cinema.ir import ColorGrade
    return ColorGrade(shadow_hue=220, shadow_sat=15, highlight_hue=45,
                      highlight_sat=12, midtone_sat=6, balance=-10, blending=50)


def test_saturation_numba_matches_numpy():
    px = _adversarial_pixels(seed=11)
    a = accel.apply_saturation(px, 15.0, backend="numpy")
    b = accel.apply_saturation(px, 15.0, backend="numba")
    assert np.max(np.abs(a.astype(np.float64) - b.astype(np.float64))) < _TOL


def test_vibrance_numba_matches_numpy():
    px = _adversarial_pixels(seed=12)
    a = accel.apply_vibrance(px, 10.0, backend="numpy")
    b = accel.apply_vibrance(px, 10.0, backend="numba")
    assert np.max(np.abs(a.astype(np.float64) - b.astype(np.float64))) < _TOL


def test_hsl_numba_matches_numpy():
    px = _adversarial_pixels(seed=13)
    hsl = _hsl_bands_op()
    a = accel.apply_hsl(px, hsl, backend="numpy")
    b = accel.apply_hsl(px, hsl, backend="numba")
    assert np.max(np.abs(a.astype(np.float64) - b.astype(np.float64))) < _TOL


def test_color_grade_numba_matches_numpy():
    px = _adversarial_pixels(seed=14)
    cg = _color_grade_op()
    a = accel.apply_color_grade(px, cg, backend="numpy")
    b = accel.apply_color_grade(px, cg, backend="numba")
    assert np.max(np.abs(a.astype(np.float64) - b.astype(np.float64))) < _TOL


def test_hsl_preserves_neutrals_on_numba():
    """Neutral pixels (r==g==b) must stay neutral on numba — the s_gate
    luminance gating (CLAUDE.md §0: a grey wedge must stay grey)."""
    v = np.linspace(0.0, 1.0, 64, dtype=np.float32)
    px = np.stack([v, v, v], axis=-1)
    out = accel.apply_hsl(px, _hsl_bands_op(), backend="numba")
    assert np.allclose(out[:, 0], out[:, 1]) and np.allclose(out[:, 1], out[:, 2])


# --- near-black invariants on the accel FAITHFUL ops (perf kernels are new + ---
# synthetic-pixel-tested only; the L<0.01 region was the suite's blind spot). ---


@pytest.mark.parametrize("op,arg", [
    ("apply_saturation", 80.0), ("apply_vibrance", 80.0),
])
def test_faithful_scalar_op_preserves_nearblack_neutrals_on_numba(op, arg):
    """B/D at near-black: a near-black NEUTRAL wedge stays neutral (r==g==b) under
    the numba Saturation/Vibrance kernels — a saturation multiplier on a
    zero-chroma pixel is a no-op at EVERY luma, incl. the L<1e-3 tail the perf
    kernel was never tested at. Guards a near-black neutral-cast regression in the
    new kernels (matches the numpy reference identically)."""
    wedge = _nearblack_neutral_wedge()
    out = getattr(accel, op)(wedge, arg, backend="numba")
    ref = getattr(accel, op)(wedge, arg, backend="numpy")
    assert np.allclose(out[:, 0], out[:, 1]) and np.allclose(out[:, 1], out[:, 2]), \
        "near-black neutral cast on numba"
    assert np.max(np.abs(out.astype(np.float64) - ref.astype(np.float64))) < _TOL


def test_faithful_hsl_preserves_nearblack_neutrals_on_numba():
    """B/D at near-black for the HSL kernel: the s_gate must protect near-black
    neutrals (ill-defined hue) at every luma down to 1e-5 — they stay neutral
    under an engaged 8-band HSL, on numba, matching numpy."""
    wedge = _nearblack_neutral_wedge()
    out = accel.apply_hsl(wedge, _hsl_bands_op(), backend="numba")
    ref = accel.apply_hsl(wedge, _hsl_bands_op(), backend="numpy")
    assert np.allclose(out[:, 0], out[:, 1]) and np.allclose(out[:, 1], out[:, 2])
    assert np.max(np.abs(out.astype(np.float64) - ref.astype(np.float64))) < _TOL


@pytest.mark.parametrize("op,mk", [
    ("apply_saturation", lambda: 80.0),
    ("apply_vibrance", lambda: 80.0),
    ("apply_hsl", _hsl_bands_op),
    ("apply_color_grade", _color_grade_op),
])
def test_faithful_accel_ops_no_negatives_on_nearblack_chromatic_numba(op, mk):
    """C at near-black: the faithful numba kernels emit NO negative ProPhoto
    channel on near-black chromatic slivers (incl. the degenerate single-channel
    shape) — the apply_saturation [0,1]-clamp lesson must hold on the kernel path
    too, at near-black, where it was never exercised. (ColorGrade legitimately
    TINTS neutrals, so it is checked for no-negatives, not neutral-preservation.)"""
    v = np.geomspace(1e-5, 5e-3, 24, dtype=np.float32)[:, None]
    nb = np.ascontiguousarray(
        np.concatenate([
            v * np.array([1.0, 0.05, 0.02], np.float32),   # near-black saturated red
            v * np.array([0.02, 1.0, 0.05], np.float32),   # green
            v * np.array([0.02, 0.05, 1.0], np.float32),   # blue
        ], axis=0))
    out = getattr(accel, op)(nb, mk(), backend="numba")
    assert np.isfinite(out).all()
    assert out.min() >= 0.0, f"{op}: negative ProPhoto at near-black: {out.min():.2e}"
