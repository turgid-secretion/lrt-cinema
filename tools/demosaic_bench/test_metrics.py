"""Metric oracles — each metric self-tested vs an ANALYTIC known answer.

This is the foundation of the whole battery: a silently-wrong metric poisons the
demosaic verdict, so every metric here is checked against a case whose correct
answer is known by construction (NOT against another metric). A buggy metric must
FAIL here. (docs/research/demosaic-test-fixtures.md §5; advisor: "metric + oracle
together".)

Oracles:
  cpsnr                    cpsnr(x,x)=inf; uniform offset d -> -20log10(d)
  s_cielab_de              s(x,x)=0; UNIFORM field -> 0 (DC preserved, stronger);
                           high-freq chroma the eye can't resolve << raw ΔE
  zipper_ratio             flat field -> 0
  region_split             identical -> inf / 0; smooth-region CPSNR finite on noise
  falsecolor_chroma_energy perfect-neutral reconstruction -> ~0; tinted -> large
  mtf50p                   Gaussian-blur edge sigma -> MTF50 ≈ 0.1874/sigma (analytic)

Run: python3 -m pytest -q tools/demosaic_bench/test_metrics.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import charts  # noqa: E402
from metrics import (  # noqa: E402
    cpsnr,
    falsecolor_chroma_energy,
    mtf50p,
    region_split,
    s_cielab_de,
    scielab_filter_xyz,
    xyz_from_linear_rgb,
    zipper_ratio,
)


# --------------------------------------------------------------------------- cpsnr
def test_cpsnr_identical_is_inf():
    x = np.random.RandomState(0).rand(40, 40, 3)
    assert cpsnr(x, x) == float("inf")


def test_cpsnr_uniform_offset_matches_formula():
    # CMSE = d^2  ->  CPSNR = 10 log10(1/d^2) = -20 log10(d).
    x = np.random.RandomState(1).rand(48, 48, 3)
    for d in (0.05, 0.1, 0.2):
        got = cpsnr(x, x + d)
        assert abs(got - (-20.0 * np.log10(d))) < 1e-9, (d, got)


# ----------------------------------------------------------------------- s_cielab
def test_scielab_identical_is_zero():
    rng = np.random.RandomState(2)
    xyz = xyz_from_linear_rgb(rng.rand(64, 64, 3))
    assert s_cielab_de(xyz, xyz) < 1e-9


def test_scielab_uniform_field_is_zero_dc_preserved():
    # Stronger than s(x,x)=0: a NON-trivial uniform field must come through the
    # opponent CSF unchanged (filters are DC-preserving, weights sum to 1), so a
    # uniform ref vs a DIFFERENT-but-also-filtered uniform test differ only by
    # their true colour. Here: filter a uniform field, it must equal itself.
    flat = xyz_from_linear_rgb(np.full((40, 40, 3), 0.42))
    filtered = scielab_filter_xyz(flat)
    assert np.allclose(filtered, flat, atol=1e-6), "DC not preserved by S-CIELAB filter"


def test_scielab_discounts_unresolvable_chroma():
    # The faithful false-colour scenario: GROUND TRUTH is a uniform neutral grey;
    # the "demosaic" introduces a 1-px (Nyquist) PURE-CHROMA checkerboard about
    # that grey. The chroma CSF cannot resolve it -> S-CIELAB collapses the test
    # back toward its neutral mean -> S-CIELAB << naive ΔEab. That down-weighting
    # IS the false-colour metric (Zhang & Wandell). Construct the two colours in
    # CIELAB so L* is identical BY CONSTRUCTION (opponent-luminance-constant is
    # NOT L*-constant: the maps are nonlinear, and a luminance checkerboard would
    # also be low-passed at 1px, confounding the test). Residual is provably chroma.
    import colour
    from metrics import _D65_XYZ, _xyz_to_lab

    n = 96
    yy, xx = np.indices((n, n))
    chk = ((xx + yy) % 2).astype(np.float64)  # 1-px checkerboard = Nyquist freq
    L0, A, B = 65.0, 15.0, 15.0  # equal L*, opposite chroma -> raw ΔE ~ 2*sqrt(A^2+B^2)
    wp = colour.XYZ_to_xy(_D65_XYZ)
    ca = colour.Lab_to_XYZ([L0, +A, +B], wp)
    cb = colour.Lab_to_XYZ([L0, -A, -B], wp)
    neutral = colour.Lab_to_XYZ([L0, 0.0, 0.0], wp)  # the checkerboard's mean
    ref_xyz = np.empty((n, n, 3))
    ref_xyz[:] = neutral  # ground truth: uniform neutral grey
    test_xyz = np.empty((n, n, 3))
    test_xyz[chk == 0] = ca
    test_xyz[chk == 1] = cb  # introduced high-freq chroma (the "false colour")
    s = s_cielab_de(ref_xyz, test_xyz)
    raw = float(
        np.mean(np.sqrt(np.sum((_xyz_to_lab(ref_xyz) - _xyz_to_lab(test_xyz)) ** 2, axis=-1)))
    )
    assert raw > 1.0, "test pattern should carry real raw chroma ΔE"
    assert s < 0.1 * raw, f"S-CIELAB ({s:.3f}) did not discount unresolvable chroma vs raw ({raw:.3f})"


# ------------------------------------------------------------------------- zipper
def test_zipper_flat_field_is_zero():
    flat = xyz_from_linear_rgb(np.full((50, 50, 3), 0.3))
    assert zipper_ratio(flat, flat) == 0.0


def test_zipper_increases_with_introduced_discontinuity():
    # Oracle design (advisor): the reference must have a UNIQUE nearest neighbour,
    # and the jitter must run ALONG that neighbour's direction. A horizontal ramp
    # (varies with column x, constant DOWN each column) has a uniquely VERTICAL
    # nearest neighbour (ΔE 0 down the column; horizontal/diagonal = ramp step >0).
    # Putting the jitter on alternating ROWS makes that vertical neighbour pair
    # jump by 0.15 in the test -> |0 - large| > threshold -> fires. (Ramp and
    # jitter-stripes on PERPENDICULAR axes; parallel axes give 0, the earlier bug.)
    n = 60
    ramp = np.linspace(0.2, 0.8, n)  # along x
    base = np.repeat(np.repeat(ramp[None, :, None], n, axis=0), 3, axis=2)
    jitter = np.zeros((n, n, 3))
    jitter[::2, :, :] = 0.15  # alternating ROWS -> perpendicular to the ramp
    ref_xyz = xyz_from_linear_rgb(base)
    test_xyz = xyz_from_linear_rgb(base + jitter)
    z_clean = zipper_ratio(ref_xyz, ref_xyz)
    z_dirty = zipper_ratio(ref_xyz, test_xyz)
    assert z_clean == 0.0
    assert z_dirty > 5.0, z_dirty


# ------------------------------------------------------------------- region_split
def test_region_split_identical():
    rng = np.random.RandomState(3)
    rgb = rng.rand(80, 80, 3)
    xyz = xyz_from_linear_rgb(rgb)
    out = region_split(xyz, xyz, rgb, rgb)
    assert out["cpsnr_edge"] == float("inf")
    assert out["cpsnr_smooth"] == float("inf")
    assert out["de_edge"] < 1e-9 and out["de_smooth"] < 1e-9


def test_region_split_finite_on_error():
    rng = np.random.RandomState(4)
    rgb = charts.dead_leaves(80, 400, seed=1)
    noisy = np.clip(rgb + rng.normal(0, 0.02, rgb.shape), 0, 1)
    out = region_split(xyz_from_linear_rgb(rgb), xyz_from_linear_rgb(noisy), rgb, noisy)
    assert np.isfinite(out["cpsnr_edge"]) and np.isfinite(out["cpsnr_smooth"])
    assert out["de_edge"] > 0 and out["de_smooth"] > 0


# --------------------------------------------------------------- falsecolor energy
def test_falsecolor_perfect_neutral_is_zero():
    # A perfectly neutral (grey) reconstruction over a neutral mask -> ~0 chroma.
    grey = np.full((40, 40, 3), 0.5)
    mask = np.ones((40, 40), dtype=bool)
    assert falsecolor_chroma_energy(grey, mask) < 1e-6


def test_falsecolor_flags_introduced_tint():
    # A coloured reconstruction over a should-be-neutral mask -> large chroma.
    tinted = np.zeros((40, 40, 3))
    tinted[..., 0] = 0.6
    tinted[..., 1] = 0.4
    tinted[..., 2] = 0.4
    mask = np.ones((40, 40), dtype=bool)
    assert falsecolor_chroma_energy(tinted, mask) > 5.0


# -------------------------------------------------------------------------- mtf50p
@pytest.mark.parametrize("sigma", [1.0, 1.5, 2.0])
def test_mtf50p_matches_gaussian_blur_analytic(sigma):
    # A Gaussian PSF of std sigma has MTF = exp(-2 pi^2 sigma^2 u^2); MTF=0.5 at
    # u = sqrt(ln2/(2 pi^2)) / sigma = 0.1874/sigma cyc/px. Blur a sharp slanted
    # edge by sigma (>> grid sampling) and recover that frequency within slack.
    from scipy.ndimage import gaussian_filter

    edge = charts.slanted_edge(192, angle_deg=5.0, softness=0.15)  # near-ideal edge
    blurred = np.empty_like(edge)
    for c in range(3):
        blurred[..., c] = gaussian_filter(edge[..., c], sigma, mode="reflect")
    got = mtf50p(blurred, channel="luma")
    expected = 0.1874 / sigma
    assert abs(got - expected) / expected < 0.15, (sigma, got, expected)


def test_mtf50p_sharper_edge_has_higher_mtf50p():
    # Monotonicity: less blur -> higher MTF50P (resolution ordering sanity).
    from scipy.ndimage import gaussian_filter

    edge = charts.slanted_edge(192, angle_deg=5.0, softness=0.15)
    soft = np.empty_like(edge)
    sharp = np.empty_like(edge)
    for c in range(3):
        soft[..., c] = gaussian_filter(edge[..., c], 2.0, mode="reflect")
        sharp[..., c] = gaussian_filter(edge[..., c], 1.0, mode="reflect")
    assert mtf50p(sharp) > mtf50p(soft)
