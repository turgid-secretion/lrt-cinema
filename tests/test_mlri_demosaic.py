"""Fixture-free correctness gate for the MLRI demosaic (`_mlri_demosaic`).

Mirrors `tests/test_rcd_demosaic.py`: no external files — every ground-truth image
is synthesised here with REAL spatial structure, mosaicked to a single-channel
Bayer CFA, reconstructed, and scored against the original. This file gates
**implementation correctness** (Axis-D1, docs/research/demosaic-test-fixtures.md):
flat-patch exactness, all four Bayer phases, finiteness / non-negativity, highlight
pass-through, and a relative quality bar (MLRI must beat a trivial bilinear by a
per-image PSNR margin) on aliasing-stress content. *World-class* quality (Axis-D2)
is NOT this file's job — that is the multi-metric battery (`tools/demosaic_bench/`),
which compares MLRI against RCD/Menon and the published SOTA numbers.

WHY "BEATS BILINEAR" IS GATED ONLY ON LUMINANCE-CORRELATED STIMULI
-----------------------------------------------------------------
MLRI demosaics in the *residual* domain: it estimates R from the green guide by a
local linear model ``a*G+b`` and interpolates the (smooth) residual ``R-(a*G+b)``.
On **luminance-correlated, band-limited** content (the realistic case — real sensors
carry an OLPF and the standard benchmarks are natural) that residual is very smooth,
so MLRI beats bilinear by a wide margin (measured +6..+10 dB on the structured
images below). But on a **neutral** aliased target (a grey zone plate where the
correct model is exactly ``a=1, b=0``) the fitted slope ``a`` drifts off 1 in the
aliasing region and *injects* chroma — there this MLRI is actually a touch WORSE than
bilinear on a pure false-colour metric. That is a measured, documented property of
**this non-directional variant** (which has no a-posteriori chroma decision to
suppress the swing — a directional method like Menon2007 scores far better there),
NOT a bug — so this file asserts "beats bilinear" only where the luminance-
correlation premise holds, exactly as the RCD test does, and never claims a neutral-
chroma win. (The colour bars step in brightness as well as hue, so they are
luminance-correlated and MLRI wins there too — by a large margin, since the residual
collapses to near-constant within each flat bar.) The full standing vs RCD/SOTA is in
the `_mlri_demosaic` module docstring + `tools/demosaic_bench`; this file is the
Axis-D1 correctness gate only.

The bilinear baseline is independent of the implementation under test (each colour
plane interpolated separately by a 3x3 / cross average) — a genuine weaker
reference, not a re-derivation of MLRI.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter

from lrt_cinema._mlri_demosaic import mlri_demosaic

# Border excluded from every metric (MLRI's guided-filter reach is +/-radius; give
# it slack). Matches the RCD test's policy so the two are directly comparable.
_MARGIN = 6

# Optical low-pass-filter emulation (a mild Gaussian before mosaicing), as a real
# sensor's anti-aliasing filter would, and as the Kodak benchmark images are
# effectively band-limited. Without it the synthetic hard steps stay above Nyquist
# and defeat the inter-channel-correlation premise of EVERY such demosaicer.
_OLPF_SIGMA = 0.7

# Asserted MLRI-over-bilinear PSNR floors (dB), per image. Set comfortably below the
# measured gaps printed by `test_psnr_beats_bilinear` (slanted_edge ~+10.6, zone
# ~+6.0, natural ~+7.6, smooth ~+6.3, color_bars ~+51.6). Conservative to stay
# robust across NumPy/BLAS/scipy versions.
_MIN_PSNR_GAIN_DB = {
    "slanted_edge": 6.0,
    "zone_plate": 3.0,
    "color_bars": 8.0,
    "natural_texture": 4.5,
    "smooth_texture": 4.0,
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
    """Diagonal step edge between two **luminance-correlated** colours (one side
    brighter in all three channels) through the OLPF — where per-channel bilinear
    zippers and a residual method follows the edge. Diagonal so neither H nor V
    interpolation is trivially correct."""
    yy, xx = np.indices((n, n))
    side = (xx * 1.0 + yy * 0.35) > (n * 0.6)
    bright = np.array([0.85, 0.62, 0.45])
    dark = np.array([0.22, 0.16, 0.12])
    img = np.where(side[..., None], bright, dark).astype(np.float64)
    return _olpf(img)


def _zone_plate(n: int = 160) -> np.ndarray:
    """Radial chirp cos(k r^2): frequency rises toward the edges, sweeping past
    Nyquist — the canonical aliasing torture test. Kept HARD (no OLPF); a mild
    correlated tint keeps it luminance-coherent so the win reflects real behaviour
    (the tint is constant per channel, so this is still luminance-correlated, unlike
    the *neutral* zone plate the battery uses to probe false colour)."""
    yy, xx = np.indices((n, n)).astype(np.float64)
    cy, cx = (n - 1) / 2.0, (n - 1) / 2.0
    r2 = (yy - cy) ** 2 + (xx - cx) ** 2
    luma = 0.5 + 0.5 * np.cos(0.0016 * r2)
    return np.stack([np.clip(luma * c, 0.0, 1.0) for c in (0.95, 0.80, 0.65)], axis=2)


def _color_bars(n: int = 128) -> np.ndarray:
    """Vertical colour bars that step in **brightness as well as hue** (each bar a
    distinct luma x tint, so luminance-correlated) through the OLPF. MLRI wins by a
    large margin: within each flat bar the residual R-(a*G+b) collapses to near
    constant, so the residual interpolation is near-exact."""
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
    """Fully-saturated, anti-correlated colour bars — the adversarial NON-realistic
    case (no luminance correlation, hard chrominance steps, no OLPF). No
    luminance-correlation demosaicer is expected to beat bilinear here; used only by
    the robustness test to document the method's boundary."""
    bars = np.array(
        [
            [1.0, 1.0, 1.0], [0.9, 0.9, 0.1], [0.1, 0.8, 0.9], [0.1, 0.8, 0.1],
            [0.9, 0.2, 0.85], [0.9, 0.15, 0.15], [0.15, 0.2, 0.9], [0.05, 0.05, 0.05],
        ]
    )
    img = np.empty((n, n, 3), dtype=np.float64)
    bw = n // len(bars)
    for i, c in enumerate(bars):
        img[:, i * bw : (i + 1) * bw, :] = c
    img[:, len(bars) * bw :, :] = bars[-1]
    return img


def _smooth_texture(n: int = 128) -> np.ndarray:
    """Smooth low-frequency luma gradient + a band of fine high-frequency texture,
    tinted (correlated channels) and OLPF-filtered — a natural-ish mix. Most of the
    frame is bilinear-friendly; the texture band is where the residual method
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
    band-limited and luminance-coherent — where directional/residual demosaicing
    wins cleanly."""
    yy, xx = np.indices((n, n)).astype(np.float64)
    base = 0.5 + 0.18 * np.sin(2.0 * np.pi * xx / n) + 0.12 * np.cos(2.0 * np.pi * yy / 96.0)
    detail = 0.12 * np.sin(xx * 0.9) * np.cos(yy * 1.1)
    luma = np.clip(base + detail, 0.0, 1.0)
    img = np.stack([np.clip(luma * c, 0.0, 1.0) for c in (1.0, 0.85, 0.70)], axis=2)
    return _olpf(img)


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
    """Trivial per-plane bilinear demosaic — the weaker reference (independent of the
    implementation under test). Each plane filled by the 3x3 average of same-colour
    neighbours; no cross-channel correlation, so it zippers and moires."""
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
    """MLRI reconstruction PSNR exceeds bilinear by the per-image floor (RGGB).

    Gated only on luminance-correlated, band-limited stimuli (see module docstring):
    those are the realistic case and the one where the residual domain pays off."""
    img = _IMAGES[name]()
    cfa = _mosaic(img, "RGGB")
    mlri = mlri_demosaic(cfa, "RGGB")
    bil = _bilinear_baseline(cfa, "RGGB")

    psnr_mlri = _psnr(img, mlri)
    psnr_bil = _psnr(img, bil)
    gain = psnr_mlri - psnr_bil
    print(
        f"[{name:>14}] MLRI={psnr_mlri:6.2f} dB  bilinear={psnr_bil:6.2f} dB  "
        f"gain={gain:+5.2f} dB  (floor {_MIN_PSNR_GAIN_DB[name]:.1f})"
    )
    assert psnr_mlri > psnr_bil, (
        f"{name}: MLRI ({psnr_mlri:.2f}) <= bilinear ({psnr_bil:.2f})"
    )
    assert gain >= _MIN_PSNR_GAIN_DB[name], (
        f"{name}: MLRI-vs-bilinear gain {gain:.2f} dB below floor "
        f"{_MIN_PSNR_GAIN_DB[name]:.1f} dB"
    )


def test_zone_plate_high_absolute_psnr() -> None:
    """Sanity floor on absolute quality: the zone plate is hard, but MLRI should
    still clear a healthy absolute PSNR (guards a regression that passes the relative
    test only because bilinear also collapsed)."""
    img = _zone_plate()
    cfa = _mosaic(img, "RGGB")
    mlri = mlri_demosaic(cfa, "RGGB")
    psnr_mlri = _psnr(img, mlri)
    print(f"[zone_plate absolute] MLRI={psnr_mlri:6.2f} dB")
    assert psnr_mlri > 20.0


@pytest.mark.parametrize("pattern", ["RGGB", "BGGR", "GRBG", "GBRG"])
def test_all_phases_reconstruct(pattern: str) -> None:
    """Every Bayer phase reconstructs the structured images well above bilinear.

    A wrong phase->flip mapping (or a spurious R<->B swap, or a transpose) would
    collapse PSNR on the broken phase. The four phases differ only by a flip of the
    SAME image, so a correct mapping yields near-identical PSNR across all four."""
    for name in ("slanted_edge", "natural_texture"):
        img = _IMAGES[name]()
        cfa = _mosaic(img, pattern)
        mlri = mlri_demosaic(cfa, pattern)
        bil = _bilinear_baseline(cfa, pattern)
        psnr_mlri = _psnr(img, mlri)
        psnr_bil = _psnr(img, bil)
        assert psnr_mlri > psnr_bil + 3.0, (
            f"phase {pattern} / {name}: MLRI {psnr_mlri:.2f} not clearly above "
            f"bilinear {psnr_bil:.2f} (likely a broken phase mapping)"
        )
        assert psnr_mlri > 35.0, (
            f"phase {pattern} / {name}: low absolute PSNR {psnr_mlri:.2f} "
            f"(likely a broken phase mapping)"
        )


def test_all_phases_psnr_consistent() -> None:
    """The four phases are the same image under a flip, so MLRI PSNR must agree
    across all four to within a hair. A spread is a smoking gun for a phase-specific
    mapping bug (a flip applied to one axis but not its partner)."""
    img = _natural_texture()
    psnrs = {}
    for pattern in ("RGGB", "BGGR", "GRBG", "GBRG"):
        cfa = _mosaic(img, pattern)
        psnrs[pattern] = _psnr(img, mlri_demosaic(cfa, pattern))
    spread = max(psnrs.values()) - min(psnrs.values())
    print(f"[phase consistency] {psnrs}  spread={spread:.3f} dB")
    assert spread < 1.5, f"per-phase PSNR spread {spread:.2f} dB too large: {psnrs}"


def test_saturated_bars_robustness() -> None:
    """Saturated, anti-correlated colour bars are the adversarial NON-realistic case
    (no luminance correlation), where NO luminance-correlation demosaicer is expected
    to beat bilinear. MLRI need only stay SANE here: finite, non-negative, and not
    catastrophically worse than bilinear. Documents the known boundary of the method
    rather than overclaiming — contrast `_color_bars` (realistic, MLRI wins big)."""
    img = _saturated_bars()
    cfa = _mosaic(img, "RGGB")
    mlri = mlri_demosaic(cfa, "RGGB")
    bil = _bilinear_baseline(cfa, "RGGB")
    psnr_mlri = _psnr(img, mlri)
    psnr_bil = _psnr(img, bil)
    print(f"[saturated_bars robustness] MLRI={psnr_mlri:6.2f} dB  bilinear={psnr_bil:6.2f} dB")
    assert np.all(np.isfinite(mlri))
    assert np.all(mlri >= 0.0)
    assert psnr_mlri > psnr_bil - 3.0, (
        f"saturated bars: MLRI {psnr_mlri:.2f} catastrophically below bilinear "
        f"{psnr_bil:.2f} (worse than the expected adversarial-case gap)"
    )


@pytest.mark.parametrize("pattern", ["RGGB", "BGGR", "GRBG", "GBRG"])
def test_flat_patch_exact(pattern: str) -> None:
    """A spatially-flat colour reconstructs bit-exactly in the interior.

    Constant input => zero Laplacians/variances => the MLRI slope ``a -> 0``, the
    intercept ``b -> mean``, so the tentative equals the constant and the residual is
    zero. Any artifact here is a real bug (a leaked Laplacian at the border, an
    unguarded 0/0 in the slope, overwriting known samples). This is the gate that the
    division MLRI reintroduces (vs the division-free RCD) is still finite + exact on
    flat input."""
    color = np.array([0.37, 0.61, 0.84])
    n = 64
    img = np.empty((n, n, 3), dtype=np.float64)
    img[:] = color
    cfa = _mosaic(img, pattern)
    out = mlri_demosaic(cfa, pattern)
    interior = out[_MARGIN:-_MARGIN, _MARGIN:-_MARGIN, :]
    expected = np.broadcast_to(color, interior.shape)
    assert np.allclose(interior, expected, atol=1e-6), (
        f"phase {pattern}: flat patch not reconstructed exactly "
        f"(max abs err {np.max(np.abs(interior - expected)):.2e})"
    )


def test_finite_nonnegative_random() -> None:
    """Random CFA -> output finite, non-negative, right shape/dtype.

    Unlike the division-free RCD, MLRI divides (the guided-filter slope and means),
    so finiteness is EARNED by the explicit regularizer + masked-mean guards, not
    structural — this nails that contract down. Non-negative but NOT upper-capped
    (highlights pass through), so no ``<= 1`` assertion — see
    `test_highlights_pass_through`."""
    rng = np.random.default_rng(20260604)
    cfa = rng.random((96, 80), dtype=np.float64)
    out = mlri_demosaic(cfa, "RGGB")
    assert out.shape == (96, 80, 3)
    assert np.all(np.isfinite(out))
    assert np.all(out >= 0.0)


def test_highlights_pass_through() -> None:
    """Near-white highlights are NOT crushed to 1.0 — MLRI preserves highlight
    headroom for the project's downstream highlight handling (the upper range is
    uncapped on purpose). A blown 1.0 patch on a 0.98 field reconstructs at >= 1.0
    (it rings slightly above 1.0 at the boundary, which a [0, 1] cap would have
    silently swallowed)."""
    n = 64
    img = np.full((n, n, 3), 0.98, dtype=np.float64)
    img[n // 4 : 3 * n // 4, n // 4 : 3 * n // 4, :] = 1.0  # blown patch
    cfa = _mosaic(img, "RGGB")
    out = mlri_demosaic(cfa, "RGGB")
    interior = out[_MARGIN:-_MARGIN, _MARGIN:-_MARGIN, :]
    core = out[n // 4 + 2 : 3 * n // 4 - 2, n // 4 + 2 : 3 * n // 4 - 2, :]
    assert core.min() >= 0.99, f"blown core crushed: min {core.min():.4f}"
    assert interior.max() > 1.0, (
        f"highlights capped at {interior.max():.4f} — the upper clip was not removed"
    )


def test_float32_in_float32_out() -> None:
    """float32 CFA preserves dtype on output (the renderer feeds float32)."""
    img = _color_bars().astype(np.float32)
    cfa = _mosaic(img, "RGGB")
    out = mlri_demosaic(cfa, "RGGB")
    assert out.dtype == np.float32
    assert np.all(np.isfinite(out))


def test_rejects_bad_input() -> None:
    """Guards: unknown pattern, non-2-D, and odd dimensions all raise."""
    cfa = np.zeros((8, 8), dtype=np.float64)
    with pytest.raises(ValueError):
        mlri_demosaic(cfa, "XYZW")
    with pytest.raises(ValueError):
        mlri_demosaic(np.zeros((8, 8, 3)), "RGGB")
    with pytest.raises(ValueError):
        mlri_demosaic(np.zeros((7, 8)), "RGGB")
