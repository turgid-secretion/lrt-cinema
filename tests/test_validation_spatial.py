"""Spatial-domain invariants for the edge-aware perceptual ops, on real 2-D
fields (NOT the packed chart).

The guided base/detail engine behind DR-compression + Texture/Clarity is the one
part of Stage 12 that is genuinely spatial — packing it into a 1-pixel-wide chart
collapses it to its global law and hides its defining failure mode (HALOS: a
naive high-pass overshoots a step edge ~580%). test_develop_ops already bounds the
halo of each op in ISOLATION on a step edge; this harness AUGMENTS that with:
  * the FULL perceptual dispatcher (DR + Texture/Clarity composed) — halos can
    stack across ops;
  * IMPULSE (single bright pixel) — ringing the step edge can't show;
  * GRADIENT — banding / staircasing a flat-region test can't show;
all at a realistic 2-D resolution where the guided radii (2, 16) act properly.

Marked ``spatial`` so the slow 2-D legs can be selected/deselected.
"""

from __future__ import annotations

import numpy as np
import pytest

from lrt_cinema.develop_ops import _PROPHOTO_LUMINANCE, apply_develop_ops
from lrt_cinema.ir import DevelopOps, RenderIntent
from tests import validation_lattice as vl

pytestmark = pytest.mark.spatial

_RES = 128  # large enough that the coarse guided radius (16) is interior-dominated


def _lum(prophoto: np.ndarray) -> np.ndarray:
    return prophoto @ _PROPHOTO_LUMINANCE


def test_full_perceptual_dispatcher_no_halo_at_step_edge():
    """A clean step edge (NO texture → the ideal local-contrast boost is a no-op)
    through the FULL perceptual dispatcher (DR-compression + Texture/Clarity +
    Contrast composed) must not ring: any excursion beyond the two emerged plateau
    levels is halo. Combined ops can stack halos, so this is stricter than the
    per-op isolation tests — the edge-aware guided split must keep it sub-1.5%."""
    lum = np.full((_RES, _RES), 0.1, dtype=np.float32)
    lum[:, _RES // 2:] = 4.0  # ~5.3-stop step, no texture
    img = np.repeat(lum[..., None], 3, axis=2)
    ops = DevelopOps(highlights=60.0, shadows=40.0, whites=40.0,
                     texture=80.0, clarity=60.0, contrast=20.0)
    out = _lum(apply_develop_ops(img, ops, RenderIntent.PERCEPTUAL))
    lo = float(out[:, :_RES // 2 - 20].mean())
    hi = float(out[:, _RES // 2 + 20:].mean())
    rng = hi - lo
    over = max(0.0, float(out.max()) - hi) / rng
    under = max(0.0, lo - float(out.min())) / rng
    assert over < 0.015, f"overshoot halo {over * 100:.2f}% (combined dispatcher)"
    assert under < 0.015, f"undershoot halo {under * 100:.2f}%"


def test_impulse_does_not_ring():
    """A single bright impulse on a flat mid field must not produce a ringing halo
    — the field away from the impulse stays at the background, with no undershoot
    ‘dark ring’ (the gradient-reversal artefact of a non-edge-aware sharpener).
    The guided filter classifies the impulse neighbourhood as an edge (a→1) and
    leaves the surround flat."""
    bg = 0.18
    flat = np.full((_RES, _RES), bg, dtype=np.float32)
    flat[_RES // 2, _RES // 2] = 3.0  # bright impulse
    img = np.repeat(flat[..., None], 3, axis=2)
    out = _lum(apply_develop_ops(img, DevelopOps(texture=90.0, clarity=70.0),
                                 RenderIntent.PERCEPTUAL))
    mask = np.ones((_RES, _RES), bool)
    mask[_RES // 2 - 2:_RES // 2 + 3, _RES // 2 - 2:_RES // 2 + 3] = False
    # No dark ring: the surround never dips meaningfully below the background.
    assert out[mask].min() > bg * 0.99, f"undershoot ring: {out[mask].min():.5f} < {bg}"
    assert np.isfinite(out).all()


def test_smooth_gradient_stays_monotone_no_banding():
    """A smooth luminance gradient driven through Contrast + Clarity stays MONOTONE
    — no staircase / banding / gradient-reversal (a local-contrast op that
    over-boosts mid-frequencies would introduce non-monotone wiggles a flat-patch
    test can't see)."""
    grad = (np.linspace(0.02, 2.0, _RES, dtype=np.float32)[None, :]
            * np.ones((_RES, 1), np.float32))
    img = np.repeat(grad[..., None], 3, axis=2)
    out = _lum(apply_develop_ops(img, DevelopOps(contrast=40.0, clarity=70.0),
                                 RenderIntent.PERCEPTUAL))
    row = out[_RES // 2]
    # Monotone non-decreasing (a few-ULP tolerance for float noise).
    assert int((np.diff(row) < -1e-5).sum()) == 0, "gradient banding / reversal"


def test_naive_usm_would_halo_proving_the_step_test_has_teeth():
    """Teeth leg: a NAIVE single-Gaussian unsharp-mask (not edge-aware) driven on
    the SAME step edge overshoots far past the 1.5% bound the guided dispatcher
    clears — so the halo bound discriminates the edge-aware win, it is not merely
    a value every sharpener passes (mirrors the test_develop_ops naive-USM leg, at
    the dispatcher level)."""
    from scipy.ndimage import gaussian_filter
    lum = np.full((_RES, _RES), 0.1, dtype=np.float32)
    lum[:, _RES // 2:] = 4.0
    log_l = np.log2(lum + 1e-6)
    hp = log_l - gaussian_filter(log_l, sigma=16, mode="nearest")  # naive coarse high-pass
    boosted = np.exp2(log_l + 2.5 * hp)  # a strong naive local-contrast boost
    lo, hi = 0.1, 4.0
    over = max(0.0, float(boosted.max()) - hi) / (hi - lo)
    assert over > 0.10, f"naive USM did not halo (toothless): {over * 100:.1f}%"


@vl.nearblack_xfail("perceptual near-black cast in a spatial step-edge context")
def test_nearblack_region_adjacent_to_bright_stays_neutral():
    """Spatial × near-black: a near-black CHROMATIC region sharing an edge with a
    bright region must not cast under a perceptual shadow-lift — the guided
    filter's edge handling must not let the bright neighbour bleed energy that
    worsens the near-black tail. Catches the bug on buggy main (xfail), flips
    live+passing with the `_nearblack_gate` fix. The bright half is unaffected."""
    nb = vl.nearblack_chromatic_field(_RES, _RES // 2)        # near-black, straddles bias
    bright = np.full((_RES, _RES // 2, 3), 0.6, dtype=np.float32)
    img = np.concatenate([nb, bright], axis=1)
    ops = DevelopOps(blacks=-10.0, contrast=-20.0, shadows=60.0)
    ace = vl.emit_acescg(apply_develop_ops(img, ops, RenderIntent.PERCEPTUAL))
    nb_side = ace[:, :_RES // 2]
    assert nb_side.min() >= 0.0, f"near-black side negatives: {nb_side.min():.6f}"
    assert vl.max_abs_chroma(nb_side).max() < vl.NB_CHROMA, (
        f"near-black side cast next to a bright edge: "
        f"{vl.max_abs_chroma(nb_side).max():.4f}")
