"""Fixture-free correctness gate for the RCD-family demosaic (`_rcd_demosaic`).

No external files: every ground-truth image is synthesized here with REAL spatial
structure, mosaicked down to a single-channel Bayer CFA, reconstructed, and scored
against the original. The reconstruction quality bar is **relative**: RCD must beat
a trivial inline bilinear demosaic by a clear PSNR margin on the aliasing-stress
images (a slanted edge, a radial-chirp zone plate, and a natural-ish texture mix),
where directional, color-difference-aware interpolation is exactly what bilinear
lacks. Margins are set below the *measured* gaps (printed by the PSNR test) so the
assertions are tight but not flaky.

REALISM MATTERS (why the structured images look the way they do)
----------------------------------------------------------------
Every luminance-correlation demosaicer (Hamilton-Adams, Malvar, RCD, ...) assumes
inter-channel high-frequency *correlation*: it injects the center channel's
Laplacian into green and reconstructs R/B as G + (R-G). A **hard, isoluminant,
anti-correlated** edge (e.g. saturated yellow abut saturated cyan at equal
luminance) violates that assumption — the correction points the wrong way and
overshoots, and PSNR then *penalizes* the directional method relative to a bounded
bilinear blur. That is a property of the assumption, not a bug (verified: a pure
luminance edge gives RCD +7 dB; the green channel is bit-exact on vertical bars).

Real sensors carry an optical low-pass (anti-aliasing) filter, and the standard
demosaic benchmarks (Kodak) are natural, band-limited, luminance-correlated images.
So the PSNR-gated images here are built to that reality: **luminance-correlated**
(one side genuinely brighter in all three channels / a tinted luma chirp / bars
that step in brightness as well as hue) and passed through a **mild Gaussian OLPF**
(sigma ~0.7 px) before mosaicing. The zone plate keeps its hard radial chirp (the
real aliasing torture test) with only a mild correlated tint. RCD beats bilinear on
all of these (the bar chart by a smaller margin, since bars are still mostly hard
chrominance steps). A separate **fully-saturated, anti-correlated** bar image is
exercised by a robustness test ONLY (finite / sane / not catastrophically worse) —
it is NOT a beats-bilinear case, documenting the boundary where the
luminance-correlation assumption breaks down.

Also gated: flat-patch exactness (a constant color must reconstruct bit-exactly
in the interior), all four Bayer phases (parametrized), and finiteness +
non-negativity on a random CFA. All comparisons exclude a border margin so border
handling never dominates the score.

The bilinear baseline here is independent of the implementation under test (it
interpolates each color plane separately by a 3x3 / cross average) — a genuine
weaker reference, not a re-derivation of RCD.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from lrt_cinema._rcd_demosaic import rcd_demosaic

# Border excluded from every metric (RCD's reach is +/-2; give it slack).
_MARGIN = 6

# Optical low-pass-filter emulation: a mild Gaussian applied before mosaicing, as
# a real sensor's anti-aliasing filter would (and as the Kodak benchmark images
# are effectively band-limited). Without it the synthetic hard steps stay above
# Nyquist and defeat the inter-channel-correlation assumption of EVERY such
# demosaicer (see module docstring).
_OLPF_SIGMA = 0.7

# Asserted RCD-over-bilinear PSNR floors (dB), per image. Set comfortably below
# the measured gaps printed by `test_psnr_beats_bilinear`. The directional /
# color-difference machinery pays off most on the structured, band-limited
# content (edge, zone plate, natural texture); thresholds are conservative to stay
# robust across NumPy/BLAS versions.
_MIN_PSNR_GAIN_DB = {
    "slanted_edge": 4.0,
    "zone_plate": 2.5,
    "color_bars": 0.4,
    "natural_texture": 4.0,
    "smooth_texture": 6.0,
}


def _olpf(img: np.ndarray) -> np.ndarray:
    """Per-channel mild Gaussian blur — emulates the sensor anti-aliasing filter."""
    return np.stack(
        [gaussian_filter(img[..., c], _OLPF_SIGMA) for c in range(img.shape[2])],
        axis=2,
    )


# ---------------------------------------------------------------------------
# Synthetic ground-truth RGB images (all even-sized; values in [0, 1])
# ---------------------------------------------------------------------------

def _slanted_edge(n: int = 128) -> np.ndarray:
    """A diagonal step edge between two **luminance-correlated** colors (one side
    brighter in all three channels) passed through the OLPF — the demosaic case
    where per-channel bilinear smears the edge (zippering) while a directional,
    color-difference method follows it. Diagonal so neither H nor V interpolation
    is trivially correct."""
    yy, xx = np.indices((n, n))
    side = (xx * 1.0 + yy * 0.35) > (n * 0.6)
    bright = np.array([0.85, 0.62, 0.45])  # warm, all channels high
    dark = np.array([0.22, 0.16, 0.12])    # warm, all channels low (correlated)
    img = np.where(side[..., None], bright, dark).astype(np.float64)
    return _olpf(img)


def _zone_plate(n: int = 160) -> np.ndarray:
    """Radial chirp cos(k r^2): spatial frequency rises toward the edges, sweeping
    past Nyquist — the canonical demosaic aliasing torture test. Kept HARD (no
    OLPF) since aliasing is the point; a mild per-channel tint (correlated, NOT a
    phase offset) keeps it luminance-coherent so the win reflects real behavior."""
    yy, xx = np.indices((n, n)).astype(np.float64)
    cy, cx = (n - 1) / 2.0, (n - 1) / 2.0
    r2 = (yy - cy) ** 2 + (xx - cx) ** 2
    luma = 0.5 + 0.5 * np.cos(0.0016 * r2)
    img = np.stack([np.clip(luma * c, 0.0, 1.0) for c in (0.95, 0.80, 0.65)], axis=2)
    return img


def _color_bars(n: int = 128) -> np.ndarray:
    """Vertical color bars that step in **brightness as well as hue** (each bar a
    distinct luma x tint, so they are luminance-correlated) passed through the
    OLPF — a realistic bar chart. RCD wins here, though by a smaller margin than on
    the edge/texture (bars are still mostly hard chrominance steps). Contrast with
    `_saturated_bars`, the adversarial anti-correlated version RCD does NOT win."""
    lumas = [0.95, 0.78, 0.62, 0.50, 0.40, 0.30, 0.20, 0.10]
    tints = [
        (1.00, 0.95, 0.90), (1.00, 0.85, 0.55), (0.60, 0.95, 1.00), (0.55, 0.95, 0.60),
        (1.00, 0.70, 0.95), (1.00, 0.55, 0.55), (0.60, 0.65, 1.00), (0.90, 0.90, 0.90),
    ]
    img = np.empty((n, n, 3), dtype=np.float64)
    bw = n // len(lumas)
    for i, (lum, tint) in enumerate(zip(lumas, tints, strict=True)):
        img[:, i * bw : (i + 1) * bw, :] = np.clip(lum * np.array(tint), 0.0, 1.0)
    img[:, len(lumas) * bw :, :] = img[:, len(lumas) * bw - 1, :][:, None, :]
    return _olpf(img)


def _saturated_bars(n: int = 128) -> np.ndarray:
    """Fully-saturated, anti-correlated color bars — the adversarial NON-realistic
    case (no luminance correlation, hard chrominance steps, no OLPF). No
    luminance-correlation demosaicer is expected to beat bilinear here; used only
    by the robustness test to document the method's boundary (see module
    docstring)."""
    bars = np.array(
        [
            [1.0, 1.0, 1.0],
            [0.9, 0.9, 0.1],
            [0.1, 0.8, 0.9],
            [0.1, 0.8, 0.1],
            [0.9, 0.2, 0.85],
            [0.9, 0.15, 0.15],
            [0.15, 0.2, 0.9],
            [0.05, 0.05, 0.05],
        ]
    )
    img = np.empty((n, n, 3), dtype=np.float64)
    bw = n // len(bars)
    for i, c in enumerate(bars):
        img[:, i * bw : (i + 1) * bw, :] = c
    img[:, len(bars) * bw :, :] = bars[-1]
    return img


def _smooth_texture(n: int = 128) -> np.ndarray:
    """A smooth low-frequency luma gradient + a band of fine high-frequency texture,
    tinted (correlated channels) and OLPF-filtered — a natural-ish mix. Most of the
    frame is bilinear-friendly; the texture band is where directional interpolation
    separates from bilinear."""
    yy, xx = np.indices((n, n)).astype(np.float64)
    luma = 0.5 + 0.32 * np.sin(2.0 * np.pi * xx / n) + 0.12 * np.cos(2.0 * np.pi * yy / n)
    band = slice(n // 3, 2 * n // 3)
    tex = 0.16 * np.sin(xx * 1.7) * np.cos(yy * 1.9)
    luma = luma.copy()
    luma[band, :] += tex[band, :]
    luma = np.clip(luma, 0.0, 1.0)
    img = np.stack([np.clip(luma * c, 0.0, 1.0) for c in (1.0, 0.85, 0.70)], axis=2)
    return _olpf(img)


def _natural_texture(n: int = 128) -> np.ndarray:
    """A natural-ish image: a low-frequency tinted luma field plus correlated fine
    detail everywhere, OLPF-filtered. Stands in for a Kodak-style benchmark frame —
    band-limited and luminance-coherent — where directional demosaicing wins
    cleanly."""
    yy, xx = np.indices((n, n)).astype(np.float64)
    base = 0.5 + 0.18 * np.sin(2.0 * np.pi * xx / n) + 0.12 * np.cos(2.0 * np.pi * yy / 96.0)
    detail = 0.12 * np.sin(xx * 0.9) * np.cos(yy * 1.1)
    luma = np.clip(base + detail, 0.0, 1.0)
    img = np.stack([np.clip(luma * c, 0.0, 1.0) for c in (1.0, 0.85, 0.70)], axis=2)
    return _olpf(img)


# PSNR-gated images (RCD must beat bilinear). The realistic (luminance-correlated,
# OLPF'd) color bars are included; the saturated anti-correlated bars are the
# adversarial boundary case, exercised separately by the robustness test.
_IMAGES = {
    "slanted_edge": _slanted_edge,
    "zone_plate": _zone_plate,
    "color_bars": _color_bars,
    "natural_texture": _natural_texture,
    "smooth_texture": _smooth_texture,
}


# ---------------------------------------------------------------------------
# Bayer mosaic + inline bilinear baseline
# ---------------------------------------------------------------------------

# Per phase: the channel index (0=R, 1=G, 2=B) selected at each of the 4 sites
# in the 2x2 tile, in (row, col) sub-positions (0,0) (0,1) (1,0) (1,1).
_PHASE_CHANNEL = {
    "RGGB": (0, 1, 1, 2),
    "BGGR": (2, 1, 1, 0),
    "GRBG": (1, 0, 2, 1),
    "GBRG": (1, 2, 0, 1),
}


def _mosaic(img: np.ndarray, pattern: str) -> np.ndarray:
    """Sample one channel per pixel per the Bayer ``pattern`` -> 2-D CFA."""
    h, w, _ = img.shape
    cfa = np.empty((h, w), dtype=img.dtype)
    (c00, c01, c10, c11) = _PHASE_CHANNEL[pattern]
    cfa[0::2, 0::2] = img[0::2, 0::2, c00]
    cfa[0::2, 1::2] = img[0::2, 1::2, c01]
    cfa[1::2, 0::2] = img[1::2, 0::2, c10]
    cfa[1::2, 1::2] = img[1::2, 1::2, c11]
    return cfa


def _bilinear_baseline(cfa: np.ndarray, pattern: str) -> np.ndarray:
    """Trivial per-plane bilinear demosaic — the weaker reference.

    Each color plane is filled independently: known samples kept, unknowns set to
    the average of the same-color neighbors within a 3x3 window (cross for G,
    cardinal+diagonal as available for R/B). Deliberately naive: no cross-channel
    correlation, so it zippers on edges and moires on the zone plate.
    """
    h, w = cfa.shape
    (c00, c01, c10, c11) = _PHASE_CHANNEL[pattern]
    site = np.full((h, w), -1, dtype=np.int8)
    site[0::2, 0::2] = c00
    site[0::2, 1::2] = c01
    site[1::2, 0::2] = c10
    site[1::2, 1::2] = c11

    out = np.zeros((h, w, 3), dtype=np.float64)
    cfa64 = cfa.astype(np.float64)
    for ch in range(3):
        known = site == ch
        vals = np.where(known, cfa64, 0.0)
        cnt = known.astype(np.float64)
        # 3x3 neighborhood average over known same-color pixels (incl. self).
        num = np.zeros_like(vals)
        den = np.zeros_like(cnt)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                num += _np_shift(vals, dy, dx)
                den += _np_shift(cnt, dy, dx)
        avg = num / np.maximum(den, 1.0)
        out[..., ch] = np.where(known, cfa64, avg)
    return np.clip(out, 0.0, 1.0)


def _np_shift(a: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """Zero-filled integer shift (test-local; mirrors the impl's edge policy)."""
    out = np.zeros_like(a)
    ys = slice(max(dy, 0), a.shape[0] + min(dy, 0))
    yd = slice(max(-dy, 0), a.shape[0] + min(-dy, 0))
    xs = slice(max(dx, 0), a.shape[1] + min(dx, 0))
    xd = slice(max(-dx, 0), a.shape[1] + min(-dx, 0))
    out[yd, xd] = a[ys, xs]
    return out


def _psnr(ref: np.ndarray, test: np.ndarray) -> float:
    """PSNR in dB over the interior (peak = 1.0). inf if identical."""
    r = ref[_MARGIN:-_MARGIN, _MARGIN:-_MARGIN, :]
    t = test[_MARGIN:-_MARGIN, _MARGIN:-_MARGIN, :]
    mse = float(np.mean((r - t) ** 2))
    if mse <= 0.0:
        return float("inf")
    return 10.0 * np.log10(1.0 / mse)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", list(_IMAGES))
def test_psnr_beats_bilinear(name: str) -> None:
    """RCD reconstruction PSNR exceeds bilinear by the per-image floor (RGGB)."""
    img = _IMAGES[name]()
    cfa = _mosaic(img, "RGGB")
    rcd = rcd_demosaic(cfa, "RGGB")
    bil = _bilinear_baseline(cfa, "RGGB")

    psnr_rcd = _psnr(img, rcd)
    psnr_bil = _psnr(img, bil)
    gain = psnr_rcd - psnr_bil
    print(
        f"[{name:>14}] RCD={psnr_rcd:6.2f} dB  bilinear={psnr_bil:6.2f} dB  "
        f"gain={gain:+5.2f} dB  (floor {_MIN_PSNR_GAIN_DB[name]:.1f})"
    )
    assert psnr_rcd > psnr_bil, f"{name}: RCD ({psnr_rcd:.2f}) <= bilinear ({psnr_bil:.2f})"
    assert gain >= _MIN_PSNR_GAIN_DB[name], (
        f"{name}: RCD-vs-bilinear gain {gain:.2f} dB below floor "
        f"{_MIN_PSNR_GAIN_DB[name]:.1f} dB"
    )


def test_zone_plate_high_absolute_psnr() -> None:
    """Sanity floor on absolute quality: the zone plate is hard, but RCD should
    still clear a healthy absolute PSNR (guards a regression that passes the
    relative test only because bilinear also collapsed)."""
    img = _zone_plate()
    cfa = _mosaic(img, "RGGB")
    rcd = rcd_demosaic(cfa, "RGGB")
    psnr_rcd = _psnr(img, rcd)
    print(f"[zone_plate absolute] RCD={psnr_rcd:6.2f} dB")
    assert psnr_rcd > 20.0


@pytest.mark.parametrize("pattern", ["RGGB", "BGGR", "GRBG", "GBRG"])
def test_all_phases_reconstruct(pattern: str) -> None:
    """Every Bayer phase reconstructs the structured images well above bilinear.

    A wrong phase->flip mapping (or a spurious R<->B swap, or a transpose) would
    collapse PSNR on the broken phase, so this is the phase-mapping guard. The four
    phases differ only by a flip of the SAME image, so a correct mapping yields
    near-identical PSNR across all four; a broken one tanks the affected phase.
    Checked on the band-limited edge + natural texture (where directional
    interpolation has a real, unambiguous advantage)."""
    for name in ("slanted_edge", "natural_texture"):
        img = _IMAGES[name]()
        cfa = _mosaic(img, pattern)
        rcd = rcd_demosaic(cfa, pattern)
        bil = _bilinear_baseline(cfa, pattern)
        psnr_rcd = _psnr(img, rcd)
        psnr_bil = _psnr(img, bil)
        assert psnr_rcd > psnr_bil + 3.0, (
            f"phase {pattern} / {name}: RCD {psnr_rcd:.2f} not clearly above "
            f"bilinear {psnr_bil:.2f} (likely a broken phase mapping)"
        )
        assert psnr_rcd > 35.0, (
            f"phase {pattern} / {name}: low absolute PSNR {psnr_rcd:.2f} "
            f"(likely a broken phase mapping)"
        )


def test_all_phases_psnr_consistent() -> None:
    """The four phases are the same image under a flip, so RCD PSNR must agree
    across all four to within a hair. A spread here is a smoking gun for a
    phase-specific mapping bug (a flip applied to one axis but not its partner)."""
    img = _natural_texture()
    psnrs = {}
    for pattern in ("RGGB", "BGGR", "GRBG", "GBRG"):
        cfa = _mosaic(img, pattern)
        psnrs[pattern] = _psnr(img, rcd_demosaic(cfa, pattern))
    spread = max(psnrs.values()) - min(psnrs.values())
    print(f"[phase consistency] {psnrs}  spread={spread:.3f} dB")
    assert spread < 1.5, f"per-phase PSNR spread {spread:.2f} dB too large: {psnrs}"


def test_saturated_bars_robustness() -> None:
    """Saturated, anti-correlated color bars are the adversarial NON-realistic case
    (no luminance correlation), where NO luminance-correlation demosaicer is
    expected to beat bilinear (measured: RCD ~-1.5 dB). RCD need only stay SANE
    here: finite, non-negative, and not catastrophically worse than bilinear
    (within a few dB). Documents the known boundary of the method (the assumption
    it rests on) rather than overclaiming — contrast `_color_bars`, the realistic
    luminance-correlated bars, which RCD does win."""
    img = _saturated_bars()
    cfa = _mosaic(img, "RGGB")
    rcd = rcd_demosaic(cfa, "RGGB")
    bil = _bilinear_baseline(cfa, "RGGB")
    psnr_rcd = _psnr(img, rcd)
    psnr_bil = _psnr(img, bil)
    print(f"[saturated_bars robustness] RCD={psnr_rcd:6.2f} dB  bilinear={psnr_bil:6.2f} dB")
    assert np.all(np.isfinite(rcd))
    assert np.all(rcd >= 0.0)
    assert psnr_rcd > psnr_bil - 3.0, (
        f"saturated bars: RCD {psnr_rcd:.2f} catastrophically below bilinear "
        f"{psnr_bil:.2f} (worse than the expected adversarial-case gap)"
    )


@pytest.mark.parametrize("pattern", ["RGGB", "BGGR", "GRBG", "GBRG"])
def test_flat_patch_exact(pattern: str) -> None:
    """A spatially-flat color reconstructs bit-exactly in the interior.

    Constant input => zero gradients/Laplacians => the green estimate equals the
    neighbor average (== the constant), and the color differences are constant =>
    R and B are exact. Any artifact here is a real bug (overwriting known samples,
    a stray gain, an unguarded divide)."""
    color = np.array([0.37, 0.61, 0.84])
    n = 64
    img = np.empty((n, n, 3), dtype=np.float64)
    img[:] = color
    cfa = _mosaic(img, pattern)
    out = rcd_demosaic(cfa, pattern)
    interior = out[_MARGIN:-_MARGIN, _MARGIN:-_MARGIN, :]
    expected = np.broadcast_to(color, interior.shape)
    assert np.allclose(interior, expected, atol=1e-6), (
        f"phase {pattern}: flat patch not reconstructed exactly "
        f"(max abs err {np.max(np.abs(interior - expected)):.2e})"
    )


def test_finite_nonnegative_random() -> None:
    """Random CFA -> output is finite, non-negative, right shape/dtype.

    The discriminator is a comparison and chrominance is subtraction (no divide on
    data), so NaN/Inf is structurally impossible; this nails that contract down.
    The output is non-negative but NOT upper-capped (highlights pass through), so
    no ``<= 1`` assertion — see `test_highlights_pass_through`."""
    rng = np.random.default_rng(20260603)
    cfa = rng.random((96, 80), dtype=np.float64)
    out = rcd_demosaic(cfa, "RGGB")
    assert out.shape == (96, 80, 3)
    assert np.all(np.isfinite(out))
    assert np.all(out >= 0.0)


def test_highlights_pass_through() -> None:
    """Near-white highlights are NOT crushed to 1.0 — the demosaic preserves
    highlight headroom for the project's downstream highlight handling (the upper
    range is uncapped on purpose). A blown 1.0 patch on a 0.98 field reconstructs
    at >= 1.0 (it even rings slightly above 1.0 at the boundary, which a [0, 1] cap
    would have silently swallowed — exactly the crush we are avoiding)."""
    n = 64
    img = np.full((n, n, 3), 0.98, dtype=np.float64)
    img[n // 4 : 3 * n // 4, n // 4 : 3 * n // 4, :] = 1.0  # blown patch
    cfa = _mosaic(img, "RGGB")
    out = rcd_demosaic(cfa, "RGGB")
    interior = out[_MARGIN:-_MARGIN, _MARGIN:-_MARGIN, :]
    # The blown core reconstructs at full 1.0 (not crushed); the boundary overshoot
    # proves the upper cap is gone (a [0, 1] clip would force max == 1.0 exactly).
    core = out[n // 4 + 2 : 3 * n // 4 - 2, n // 4 + 2 : 3 * n // 4 - 2, :]
    assert core.min() >= 0.99, f"blown core crushed: min {core.min():.4f}"
    assert interior.max() > 1.0, (
        f"highlights capped at {interior.max():.4f} — the upper clip was not removed"
    )


def test_float32_in_float32_out() -> None:
    """float32 CFA preserves dtype on output (the renderer feeds float32)."""
    img = _color_bars().astype(np.float32)
    cfa = _mosaic(img, "RGGB")
    out = rcd_demosaic(cfa, "RGGB")
    assert out.dtype == np.float32
    assert np.all(np.isfinite(out))


def test_rejects_bad_input() -> None:
    """Guards: unknown pattern, non-2-D, and odd dimensions all raise."""
    cfa = np.zeros((8, 8), dtype=np.float64)
    with pytest.raises(ValueError):
        rcd_demosaic(cfa, "XYZW")
    with pytest.raises(ValueError):
        rcd_demosaic(np.zeros((8, 8, 3)), "RGGB")
    with pytest.raises(ValueError):
        rcd_demosaic(np.zeros((7, 8)), "RGGB")
