"""Contracts for `scene_tone.apply_scene_hlsh` — the probe-calibrated
scene-referred LOCAL Highlights/Shadows translation (pipeline slot-7b).

Constants are calibrated against the round-2 owner LR exports
(`tools/cal_hlsh_fit.py`); these tests pin the CONTRACTS (identity,
directions, hue preservation, near-black guard, overrange survival,
locality), not the calibration numbers — the evidence JSONs pin those.
"""

from __future__ import annotations

import numpy as np

from lrt_cinema.scene_tone import _hlsh_delta, apply_scene_hlsh


def _gradient_field(h=96, w=96):
    """Scene-linear neutral field sweeping ~13 stops of luminance."""
    lum = np.geomspace(1e-4, 1.2, h * w).reshape(h, w).astype(np.float32)
    return np.repeat(lum[..., None], 3, axis=-1)


def test_identity_at_zero_sliders_is_the_input_object():
    """Both sliders 0 → the LITERAL input array (byte-exact ship-gate /
    production contract; H/S are zero in the production sequence)."""
    x = _gradient_field()
    assert apply_scene_hlsh(x, 0.0, 0.0) is x


def test_delta_family_is_zero_at_zero_sliders():
    b = np.linspace(-18.0, 2.0, 101)
    np.testing.assert_array_equal(_hlsh_delta(b, 0.0, 0.0), np.zeros_like(b))


def test_negative_highlights_darkens_brights_spares_deep_shadows():
    x = _gradient_field()
    out = apply_scene_hlsh(x, -50.0, 0.0)
    lum_in = x.mean(-1)
    lum_out = out.mean(-1)
    bright = lum_in > 0.5
    dark = lum_in < 1e-3
    assert (lum_out[bright] < lum_in[bright]).all()          # pulled down
    ratio_dark = lum_out[dark] / lum_in[dark]
    assert np.abs(np.log2(ratio_dark)).max() < 0.2           # darks ~untouched


def test_positive_shadows_lifts_dark_regions_spares_bright_regions():
    """Regional contract (the LLF core applies tone at the context scale —
    the measured LR fingerprint): a DARK region lifts, a BRIGHT region
    stays put. A global smooth ramp is deliberately NOT the test article:
    on one, the regional operator legitimately shifts brights toward the
    regional mean's delta (same behaviour class the round-2 locality
    statistic measured in Lightroom itself)."""
    h, w = 128, 256
    x = np.full((h, w), 0.6, dtype=np.float32)
    x[:, : w // 2] = 1.5e-3                                   # dark half
    rgb = np.repeat(x[..., None], 3, axis=-1)
    out = apply_scene_hlsh(rgb, 0.0, 50.0)
    lum_out = out.mean(-1)
    dark_mid = lum_out[h // 2, w // 8]
    bright_mid = lum_out[h // 2, 5 * w // 8]
    assert dark_mid > 1.5e-3 * 1.15                           # lifted strongly
    # bright region: nudged at most mildly (this synthetic field has an
    # extreme 8.6-stop bimodal gap inside one residual neighbourhood — the
    # regional edge arm legitimately compresses a little of it; the dark
    # side must still receive SEVERAL times the bright side's move)
    bright_move = abs(np.log2(bright_mid / 0.6))
    dark_move = np.log2(dark_mid / 1.5e-3)
    assert bright_move < 0.35
    assert dark_move > 3.0 * bright_move


def test_stronger_slider_moves_more():
    b = np.linspace(-14.0, 0.0, 200)
    d50 = _hlsh_delta(b, -50.0, 0.0)
    d100 = _hlsh_delta(b, -100.0, 0.0)
    assert d100.min() < d50.min() < 0.0                       # deeper pull at -100
    s50 = _hlsh_delta(b, 0.0, 50.0)
    s100 = _hlsh_delta(b, 0.0, 100.0)
    assert s100.max() > s50.max() > 0.0                       # bigger lift at +100


def test_hue_preserved_above_nearblack_floor():
    """The reapply is a per-pixel luminance ratio: RGB channel ratios survive
    exactly for pixels above the near-black floor (§0 hue discipline)."""
    rng = np.random.default_rng(7)
    x = (rng.uniform(0.05, 0.8, (32, 32, 3))).astype(np.float32)
    out = apply_scene_hlsh(x, -40.0, 30.0)
    rin = x[..., 0] / x[..., 1]
    rout = out[..., 0] / out[..., 1]
    np.testing.assert_allclose(rout, rin, rtol=2e-3)


def test_nearblack_chroma_rolls_to_neutral():
    """A degenerate single-channel near-black pixel must NOT be amplified into
    a saturated cast by a big Shadows lift (the measured armS Shadows+100
    chroma-explosion failure mode) — below the scene floor the output rolls
    to the achromatic pixel of the same luminance."""
    x = np.full((33, 33, 3), 0.3, dtype=np.float32)
    x[16, 16] = (4e-6, 0.0, 0.0)  # degenerate near-black, pure red
    out = apply_scene_hlsh(x, 0.0, 100.0)
    px = out[16, 16]
    chroma = px.max() - px.min()
    assert chroma < 1e-4, f"near-black cast survived: {px}"


def test_overrange_survives_scene_contract():
    """Scene-referred contract: >1 highlights are NOT clamped by the op (the
    display transfer clips downstream; EXR masters keep them)."""
    x = np.full((48, 48, 3), 1.8, dtype=np.float32)
    out = apply_scene_hlsh(x, 0.0, 60.0)   # shadows op: brights ~untouched
    assert out.max() > 1.5


def test_local_adaptation_same_luminance_different_context():
    """The LOCAL contract (the probe fingerprint): a pixel of the SAME scene
    luminance embedded in a BRIGHT region is pulled down more by -Highlights
    than one embedded in a DARK region (base/detail split at the guided
    radius; a purely global curve would move both identically)."""
    h, w = 96, 192
    x = np.full((h, w), 0.02, dtype=np.float32)
    x[:, w // 2:] = 0.7                    # right half: bright region
    x[h // 2, w // 4] = 0.25               # probe pixel in DARK context
    x[h // 2, 3 * w // 4] = 0.25           # probe pixel in BRIGHT context
    rgb = np.repeat(x[..., None], 3, axis=-1)
    out = apply_scene_hlsh(rgb, -100.0, 0.0).mean(-1)
    drop_dark_ctx = np.log2(out[h // 2, w // 4] / 0.25)
    drop_bright_ctx = np.log2(out[h // 2, 3 * w // 4] / 0.25)
    assert drop_bright_ctx < drop_dark_ctx - 0.1, (
        f"no local adaptation: bright-ctx {drop_bright_ctx:.3f} vs "
        f"dark-ctx {drop_dark_ctx:.3f}")


def test_output_dtype_and_nonnegative():
    x = _gradient_field()
    out = apply_scene_hlsh(x, -25.0, 25.0)
    assert out.dtype == np.float32
    assert (out >= 0.0).all()
