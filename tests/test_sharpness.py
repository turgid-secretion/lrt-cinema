"""Capture-sharpening (`develop_ops.apply_sharpness`) — D2.

A clean-room ACR/LR capture-sharpening luminance USM (Amount + Radius; Detail /
Masking are a follow-up increment). Tests are split into the three validation axes
the develop-op playbook uses:

* **Implementation correctness** (Axis 1) — an INDEPENDENT reimplementation of the
  USM composition (`_oracle_sharpness`) vs the production op, expected ~0. The
  Gaussian blur is treated as a shared primitive (like a colour matrix): the oracle
  reimplements everything *around* it (sRGB OETF/EOTF from the IEC formula, the
  high-pass, the Amount scale, the luminance-ratio reapply) without calling any
  production helper.
* **Self-properties** — identity at Amount 0 (byte-exact), monotonicity, flat-patch
  no-op, luminance-only (hue preserved), and the load-bearing **headroom-survives**
  invariant (a >1 highlight is not clamped — `highlight_recovery` depends on it).
* **Wiring** — `apply_develop_ops` gating: off = byte-exact, faithful bakes,
  the perceptual master never sharpens (defers detail to the grade).
"""

from __future__ import annotations

import numpy as np
import pytest

from lrt_cinema.develop_ops import (
    _ACR_DEFAULT_AMOUNT,
    _ACR_DEFAULT_RADIUS,
    _PROPHOTO_LUMINANCE,
    _resolve_capture_sharpen,
    apply_develop_ops,
    apply_sharpness,
)
from lrt_cinema.ir import DevelopOps, RenderIntent


# ---------------------------------------------------------------------------
# Axis-1 independent reimplementation oracle
# ---------------------------------------------------------------------------
def _oracle_sharpness(prophoto: np.ndarray, amount: float, radius: float) -> np.ndarray:
    """Independent USM reimpl. Same Gaussian primitive; everything else from
    scratch. Valid where luminance > the near-black floor (gate == 1.0), which all
    callers below respect (inputs ≥ 0.05)."""
    if amount == 0.0:
        return prophoto.copy()
    from scipy.ndimage import gaussian_filter

    pp = prophoto.astype(np.float64)
    lum = pp @ _PROPHOTO_LUMINANCE  # luminance is a definition (shared primitive)

    def oetf(x: np.ndarray) -> np.ndarray:  # IEC 61966-2-1, no clip (extends >1)
        x = np.maximum(x, 0.0)
        return np.where(x <= 0.0031308, x * 12.92, 1.055 * x ** (1.0 / 2.4) - 0.055)

    def eotf(x: np.ndarray) -> np.ndarray:
        return np.where(
            x <= 0.04045, x / 12.92, np.maximum((x + 0.055) / 1.055, 0.0) ** 2.4,
        )

    perceptual = oetf(lum)
    blurred = gaussian_filter(perceptual, sigma=max(float(radius), 0.0), mode="reflect")
    perceptual_sharp = perceptual + (amount / 100.0) * (perceptual - blurred)
    lum_out = eotf(perceptual_sharp)
    ratio = lum_out / np.maximum(lum, 1e-12)
    return np.maximum(pp * ratio[..., None], 0.0)


def _edgy(seed: int = 0, lo: float = 0.15, hi: float = 0.55) -> np.ndarray:
    """An above-near-black ProPhoto frame with a vertical edge + texture."""
    rng = np.random.default_rng(seed)
    img = np.full((24, 40, 3), lo, np.float32)
    img[:, 20:, :] = hi
    img += (rng.random((24, 40, 3)).astype(np.float32) - 0.5) * 0.04
    return np.clip(img, 0.05, None)


# ---------------------------------------------------------------------------
# Axis 1 — implementation correctness
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("amount", [20.0, 40.0, 100.0, 150.0])
@pytest.mark.parametrize("radius", [0.5, 1.0, 2.5])
def test_sharpness_matches_independent_oracle(amount, radius):
    img = _edgy(amount and int(amount))
    got = apply_sharpness(img, amount, radius)
    ref = _oracle_sharpness(img, amount, radius)
    assert np.max(np.abs(got.astype(np.float64) - ref)) < 1e-6


# ---------------------------------------------------------------------------
# Self-properties
# ---------------------------------------------------------------------------
def test_identity_at_zero_amount_is_the_same_object():
    img = _edgy(1)
    assert apply_sharpness(img, 0.0) is img            # byte-exact short-circuit
    assert apply_sharpness(img, 0.0, 2.0) is img


def test_flat_patch_is_a_noop():
    flat = np.full((16, 16, 3), 0.42, np.float32)
    out = apply_sharpness(flat, 100.0, 1.0)
    assert np.allclose(out, flat, atol=1e-6)           # high-pass ≡ 0 on flat input


def test_sharpens_edge_with_overshoot_and_undershoot():
    edge = np.full((8, 32, 3), 0.2, np.float32)
    edge[:, 16:, :] = 0.6
    out = apply_sharpness(edge, 60.0, 1.0)
    assert out[:, 15, 0].mean() < 0.2                  # dark side undershoots
    assert out[:, 16, 0].mean() > 0.6                  # bright side overshoots
    assert np.isfinite(out).all() and (out >= 0).all()


def test_monotonic_in_amount():
    edge = np.full((8, 32, 3), 0.25, np.float32)
    edge[:, 16:, :] = 0.65
    contrast = [
        float(apply_sharpness(edge, a, 1.0)[:, 16, 0].mean()
              - apply_sharpness(edge, a, 1.0)[:, 15, 0].mean())
        for a in (0.0, 25.0, 50.0, 100.0)
    ]
    assert contrast == sorted(contrast)


def test_preserves_highlight_headroom():
    """A >1 highlight (Tier-1 recovery) must survive — no top clamp."""
    hdr = np.full((8, 32, 3), 0.5, np.float32)
    hdr[:, 16:, :] = 1.4
    out = apply_sharpness(hdr, 80.0, 1.0)
    assert out.max() > 1.0


def test_luminance_only_preserves_hue():
    """A coloured edge keeps its channel ratios (sharpening is luminance-only)."""
    col = np.empty((8, 32, 3), np.float32)
    col[:, :, :] = [0.4, 0.1, 0.1]
    col[:, 16:, :] = [0.8, 0.2, 0.2]                   # same 4:1:1 hue, brighter
    out = apply_sharpness(col, 60.0, 1.0)
    mid = out[:, 4, :].mean(0)                          # away from the edge ring
    assert mid[0] / mid[1] == pytest.approx(4.0, abs=0.02)


def test_negative_radius_is_safe():
    img = _edgy(2)
    out = apply_sharpness(img, 40.0, -1.0)             # clamped to σ≥0 → no-op blur
    assert np.isfinite(out).all() and (out >= 0).all()


# ---------------------------------------------------------------------------
# Mode resolver
# ---------------------------------------------------------------------------
def test_resolve_capture_sharpen_modes():
    silent = DevelopOps()                               # no Amount in the XMP
    explicit = DevelopOps(sharpness=85.0, sharpen_radius=2.0)
    assert _resolve_capture_sharpen(explicit, "off") == (0.0, explicit.sharpen_radius)
    assert _resolve_capture_sharpen(silent, "acr") == (_ACR_DEFAULT_AMOUNT, _ACR_DEFAULT_RADIUS)
    assert _resolve_capture_sharpen(explicit, "acr") == (85.0, 2.0)   # honour XMP Amount
    assert _resolve_capture_sharpen(explicit, "xmp") == (85.0, 2.0)
    assert _resolve_capture_sharpen(silent, "xmp") == (0.0, 1.0)      # Amount 0 → no-op


# ---------------------------------------------------------------------------
# Wiring — apply_develop_ops gating
# ---------------------------------------------------------------------------
def test_default_off_is_byte_exact():
    img = _edgy(3)
    ops = DevelopOps(sharpness=50.0)
    base = apply_develop_ops(img, ops, RenderIntent.FAITHFUL, capture_sharpen="off")
    assert np.array_equal(apply_develop_ops(img, ops, RenderIntent.FAITHFUL), base)  # default arg
    # and equals the same render with no Amount at all (off truly skips sharpening)
    assert np.array_equal(
        base, apply_develop_ops(img, DevelopOps(), RenderIntent.FAITHFUL, capture_sharpen="off"),
    )


def test_faithful_acr_and_xmp_actually_sharpen():
    img = _edgy(4)
    off = apply_develop_ops(img, DevelopOps(), RenderIntent.FAITHFUL, capture_sharpen="off")
    acr = apply_develop_ops(img, DevelopOps(), RenderIntent.FAITHFUL, capture_sharpen="acr")
    xmp = apply_develop_ops(img, DevelopOps(sharpness=60.0), RenderIntent.FAITHFUL,
                            capture_sharpen="xmp")
    assert not np.allclose(acr, off)
    assert not np.allclose(xmp, off)


def test_perceptual_master_never_sharpens():
    """The perceptual master defers detail to the grade — capture_sharpen is inert."""
    img = _edgy(5)
    ops = DevelopOps(sharpness=120.0)
    off = apply_develop_ops(img, ops, RenderIntent.PERCEPTUAL, master_look="bake",
                            capture_sharpen="off")
    acr = apply_develop_ops(img, ops, RenderIntent.PERCEPTUAL, master_look="bake",
                            capture_sharpen="acr")
    assert np.array_equal(off, acr)
