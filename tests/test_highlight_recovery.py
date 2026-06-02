"""Tier-1 raw highlight reconstruction — synthetic, fixture-free (CI).

Builds camera-RGB fields with clipped highlights of KNOWN pre-clip colour, each
embedding recoverable same-chromaticity neighbours (ratio propagation has nothing
to learn from an isolated uniform clip), and asserts:

  * single- and double-channel clips are reconstructed with the LOCAL CHANNEL
    RATIO restored (recovered chromaticity ≈ the true pre-clip chromaticity),
    lifted well above the libraw clip;
  * fully-blown (no surviving channel) pixels get a NEUTRAL interim — neutral
    *after* the asymmetric white-balance multiply, i.e. NOT magenta;
  * a spatial step edge is reconstructed without halo overshoot;
  * no negatives / NaN / Inf, output stays sane;
  * a no-clip field is a byte-identical no-op (returns the input object) — the
    guarantee that keeps the ΔE ship gate unmoved on unclipped content.

The post-WB checks use the gym-realistic AsShotNeutral ([0.5, 1.0, 0.776] ↔ WB
multipliers [2.0, 1.0, 1.289]) so "neutral after WB" is a real asymmetric test.
"""

from __future__ import annotations

import numpy as np

from lrt_cinema.highlight_recovery import (
    DEFAULT_CLIP_LEVEL,
    _tier1_ratio_propagation,
    clip_mask,
    reconstruct_highlights,
)

# Gym-realistic camera neutral: WB = 1/ASN normalised to G = [2.0, 1.0, 1.289].
_ASN = np.array([0.5, 1.0, 0.7759], dtype=np.float32)
_WB_MUL = (1.0 / _ASN) / (1.0 / _ASN)[1]  # Stage-2 multiplier, G-normalised


def _post_wb(cam: np.ndarray) -> np.ndarray:
    """Apply the Stage-2 white-balance the pipeline applies right after this
    stage — so "neutral / chromaticity after WB" can be checked directly."""
    return cam * _WB_MUL[None, None, :]


def _chroma(rgb: np.ndarray) -> np.ndarray:
    """Unit-sum chromaticity (drops brightness), for ratio comparison."""
    return rgb / np.maximum(rgb.sum(axis=-1, keepdims=True), 1e-9)


def _uniform_field(color, shape=(96, 96)) -> np.ndarray:
    f = np.empty((*shape, 3), dtype=np.float32)
    f[:] = np.asarray(color, dtype=np.float32)
    return f


def _embed(field: np.ndarray, color, y0, y1, x0, x1) -> None:
    field[y0:y1, x0:x1, :] = np.asarray(color, dtype=np.float32)


# --- single-channel clip: R clips, G+B survive --------------------------------


def test_single_channel_clip_restores_ratio():
    """True colour [1.6, 0.8, 0.4] (R clips at 1.0); surrounded by unclipped
    same-chroma background. Recovery must lift R back toward 1.6 (ratio R:G:B
    restored), not leave it at the clipped 1.0."""
    true_hi = np.array([1.6, 0.8, 0.4], dtype=np.float32)  # ratio 2:1:0.5
    bg_lo = np.array([0.4, 0.2, 0.1], dtype=np.float32)    # SAME chroma, unclipped
    field = _uniform_field(bg_lo)
    _embed(field, true_hi, 47, 49, 47, 49)  # 2x2 highlight, background-dominated

    clipped = np.minimum(field, 1.0).astype(np.float32)  # simulate sensor clip
    assert clip_mask(clipped)[48, 48, 0]          # R flagged clipped
    assert not clip_mask(clipped)[48, 48, 1]      # G survives

    out = reconstruct_highlights(clipped, _ASN)
    px = out[48, 48]
    assert px[0] > 1.4, f"R not recovered (got {px[0]:.3f}, clipped was 1.0)"
    # surviving channels untouched
    np.testing.assert_allclose(px[1:], true_hi[1:], rtol=1e-5)
    # chromaticity restored to the true pre-clip colour
    np.testing.assert_allclose(_chroma(px), _chroma(true_hi), atol=0.02)


# --- double-channel clip: R+G clip, B survives --------------------------------


def test_double_channel_clip_restores_ratio_via_single_survivor():
    """True [1.6, 1.2, 0.5] (R+G clip); the lone surviving channel B anchors BOTH
    clipped channels via the local ratio. (Per the task/advisor: 2-clip is
    ratio-recovered, NOT dumped into the neutral branch.)"""
    true_hi = np.array([1.6, 1.2, 0.5], dtype=np.float32)   # ratio 3.2:2.4:1
    bg_lo = np.array([0.64, 0.48, 0.2], dtype=np.float32)   # SAME chroma, unclipped
    field = _uniform_field(bg_lo)
    _embed(field, true_hi, 47, 49, 47, 49)  # 2x2, strongly background-dominated

    clipped = np.minimum(field, 1.0).astype(np.float32)
    m = clip_mask(clipped)[48, 48]
    assert m[0] and m[1] and not m[2]  # R,G clipped; B survives

    out = reconstruct_highlights(clipped, _ASN)
    px = out[48, 48]
    assert px[0] > 1.4 and px[1] > 1.05, f"R/G not recovered: {px}"
    np.testing.assert_allclose(px[2], true_hi[2], rtol=1e-5)  # B untouched
    np.testing.assert_allclose(_chroma(px), _chroma(true_hi), atol=0.03)


# --- fully blown: no survivor → neutral interim (NOT magenta) ------------------


def test_fully_blown_is_neutral_after_wb_not_magenta():
    """All three channels clipped → the Tier-1 interim sets the pixel ∝ ASN, so
    it is NEUTRAL after the asymmetric WB. The bug this guards: leaving camera
    [1,1,1] → post-WB [2, 1, 1.289] = the warm/magenta cast."""
    bg_lo = np.array([0.3, 0.3, 0.3], dtype=np.float32)
    field = _uniform_field(bg_lo)
    _embed(field, [3.0, 3.0, 3.0], 44, 52, 44, 52)  # all-clipped blob

    clipped = np.minimum(field, 1.0).astype(np.float32)
    assert np.all(clip_mask(clipped)[48, 48]), "patch should be fully clipped"
    out = reconstruct_highlights(clipped, _ASN)

    # interim ∝ ASN at the clip level → neutral after WB
    wb = _post_wb(out[None, 48:49, 48:49])[0, 0, 0]
    assert wb.max() - wb.min() < 0.02 * wb.mean(), f"post-WB not neutral: {wb}"
    # explicitly NOT the camera-[1,1,1] magenta failure (post-WB R>>G)
    cam_one_wb = _post_wb(np.ones((1, 1, 3), np.float32))[0, 0]
    assert (cam_one_wb[0] - cam_one_wb[1]) > 0.5  # the failure we avoid is real


# --- spatial edge: no halo overshoot ------------------------------------------


def test_edge_recovers_without_overshoot_halo():
    """A clipped bright square (R clips) against an unclipped same-chroma field.
    The defining halo failure of naive (high-pass) recovery is OVERSHOOT above
    the true bright level at the boundary ring; the ratio method cannot overshoot
    because the reconstructed value is bounded by the local ratio. So: recovery
    fires (the patch lifts off the clip) and NO pixel anywhere exceeds the true
    level — neither interior nor the edge where a halo would appear."""
    true_hi = np.array([1.6, 0.8, 0.4], dtype=np.float32)
    bg_lo = np.array([0.4, 0.2, 0.1], dtype=np.float32)   # SAME chroma
    field = _uniform_field(bg_lo)
    _embed(field, true_hi, 45, 51, 45, 51)  # 6x6 patch (has an interior + edge)

    clipped = np.minimum(field, 1.0).astype(np.float32)
    out = reconstruct_highlights(clipped, _ASN)

    r = out[..., 0]
    assert r.max() <= true_hi[0] * 1.05, f"halo overshoot above true ({r.max():.3f})"
    assert out[48, 48, 0] > 1.1, "patch interior R under-recovered (no-op)"
    # the unclipped field well outside the patch is untouched (byte-identical)
    np.testing.assert_array_equal(out[:36], clipped[:36])


# --- guards: finite, non-negative, in-gamut-feasible --------------------------


def test_no_negatives_no_nan_on_mixed_field():
    rng = np.random.default_rng(0)
    field = (rng.random((48, 48, 3), dtype=np.float32) * 1.4).astype(np.float32)
    out = reconstruct_highlights(field, _ASN)
    assert np.all(np.isfinite(out)), "non-finite output"
    assert np.all(out >= 0.0), "negative output"
    assert out.dtype == np.float32


# --- strict no-op on unclipped content (the gate-safety guarantee) ------------


def test_no_clip_field_is_byte_identical_noop():
    rng = np.random.default_rng(1)
    field = (rng.random((40, 40, 3), dtype=np.float32) * 0.9).astype(np.float32)
    assert field.max() < DEFAULT_CLIP_LEVEL
    out = reconstruct_highlights(field, _ASN)
    assert out is field, "no-clip field must return the SAME object (no-op)"


def test_disabled_is_byte_identical_noop():
    field = _uniform_field([1.5, 1.5, 1.5], shape=(16, 16)).astype(np.float32)
    out = reconstruct_highlights(field, _ASN, enable=False)
    assert out is field, "enable=False must be a strict no-op"


def test_tier2_mask_flags_fully_blown_not_well_recovered():
    """Tier-2 hand-off contract: the residual mask flags fully-blown pixels (no
    survivor → neutral interim) AND under-recovered clips, but NOT a cleanly
    ratio-recovered partial clip. Phase 2 (Poisson) consumes this mask."""
    # SAME-chroma sparse single-clip (R) → cleanly recovers above the clip;
    # plus a fully-blown all-channel blob → neutral interim, Tier 2's job.
    bg = np.array([0.4, 0.2, 0.1], dtype=np.float32)        # chroma 2:1:0.5
    field = _uniform_field(bg)
    _embed(field, [1.6, 0.8, 0.4], 20, 22, 20, 22)         # same chroma, R clips
    _embed(field, [3.0, 3.0, 3.0], 60, 68, 60, 68)         # fully-blown blob
    clipped = np.minimum(field, 1.0).astype(np.float32)

    out, tier2 = _tier1_ratio_propagation(
        clipped, clip_mask(clipped), _ASN, clip_level=DEFAULT_CLIP_LEVEL, radius=8,
    )
    assert tier2[64, 64], "fully-blown blob must be flagged for Tier 2"
    assert out[21, 21, 0] > 1.0, "single-clip R should lift above the clip"
    assert not tier2[21, 21], "cleanly recovered partial clip must NOT be flagged"
    # mask is a subset of clipped pixels
    assert not tier2[~clip_mask(clipped).any(-1)].any()


def test_unclipped_channels_are_preserved_exactly():
    """Even on a clipped field, channels that did NOT clip are byte-identical —
    recovery only ever writes clipped channels."""
    true_hi = np.array([1.6, 0.8, 0.4], dtype=np.float32)
    bg_lo = np.array([0.4, 0.2, 0.1], dtype=np.float32)
    field = _uniform_field(bg_lo)
    _embed(field, true_hi, 46, 50, 46, 50)
    clipped = np.minimum(field, 1.0).astype(np.float32)

    out = reconstruct_highlights(clipped, _ASN)
    unclipped = ~clip_mask(clipped)
    np.testing.assert_array_equal(out[unclipped], clipped[unclipped])
