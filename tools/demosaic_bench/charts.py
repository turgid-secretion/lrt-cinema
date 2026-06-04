"""Layer-A analytic synthetic charts (docs/research/demosaic-test-fixtures.md §4a, §8).

True ground truth BY CONSTRUCTION: each chart is rendered directly as known
3-channel LINEAR RGB at arbitrary resolution/frequency, so there is no prior
demosaic, no licence, no instrument band-limit, and full above-Nyquist content
(the aliasing regime where directional demosaicers earn their keep — §4a). This
is the spine of the battery: it escapes the ground-truth circularity that
disqualifies every public sRGB set (§2) and carries the chart-based metrics
(MTF50P, false-colour chroma-energy) that a re-mosaiced natural image cannot.

Every generator returns float64 LINEAR RGB in [0,1], shape (H,W,3), H/W even
(Bayer requires even dims). Mosaic with `metrics.mosaic_rggb`; the returned RGB
*is* the ground truth. An optional mild OLPF (anti-alias blur, modelling a sensor
optical low-pass filter) is available where a band-limited variant is wanted; it
is OFF by default because the point of these charts is to STRESS aliasing.

Charts (§8 Layer-A table):
  slanted_edge          resolution / zipper / false colour   -> MTF50P, edge ΔE
  zone_plate            aliasing / moire / colour moire       -> false-colour map
  dead_leaves           texture over-smoothing                -> acutance/CPSNR
  siemens_star          s-SFR / aliasing onset                -> CPSNR/false colour
  isoluminant_color_edge documented failure boundary          -> edge ΔE, zipper
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter


def _olpf(rgb: np.ndarray, sigma: float) -> np.ndarray:
    """Mild optical-low-pass (anti-alias) blur per channel; models a sensor OLPF.
    OFF by default in every chart (we want aliasing); exposed for band-limited
    variants. sigma in pixels."""
    if sigma <= 0:
        return rgb
    out = np.empty_like(rgb)
    for c in range(3):
        out[..., c] = gaussian_filter(rgb[..., c], sigma, mode="reflect")
    return out


def _even(n: int) -> int:
    return n if n % 2 == 0 else n + 1


def slanted_edge(
    size: int = 256,
    angle_deg: float = 5.0,
    low: tuple[float, float, float] = (0.05, 0.05, 0.05),
    high: tuple[float, float, float] = (0.9, 0.9, 0.9),
    *,
    softness: float = 0.6,
    olpf_sigma: float = 0.0,
) -> np.ndarray:
    """Near-vertical slanted edge (ISO 12233 stimulus). `angle_deg`~5deg from
    vertical (>2deg off 0/45/90 for valid eSFR binning). Sub-pixel-soft transition
    (erf), so the edge is band-limited enough to differentiate but still high-
    frequency. Default neutral; pass coloured `low`/`high` for a saturated edge
    (false-colour stress). Returns linear-RGB ground truth."""
    from scipy.special import erf

    h = w = _even(size)
    yy, xx = np.indices((h, w), dtype=np.float64)
    # Edge line through the centre, tilted by angle from vertical.
    a = np.tan(np.deg2rad(angle_deg))
    cx = w / 2.0
    signed = (xx - cx) - a * (yy - h / 2.0)  # >0 right of the edge
    t = 0.5 * (1.0 + erf(signed / (np.sqrt(2.0) * max(softness, 1e-3))))
    lo = np.asarray(low, dtype=np.float64)
    hi = np.asarray(high, dtype=np.float64)
    rgb = lo[None, None, :] + t[..., None] * (hi - lo)[None, None, :]
    return _olpf(rgb, olpf_sigma)


def zone_plate(
    size: int = 256,
    k: float = 0.9,
    tint: tuple[float, float, float] = (1.0, 1.0, 1.0),
    *,
    amplitude: float = 0.45,
    bias: float = 0.5,
    olpf_sigma: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Radial zone plate (hyperbolic chirp): intensity = bias + A*cos(k*pi*r^2/size).
    Frequency rises with radius, sweeping past Nyquist -> the canonical moire /
    colour-moire visualiser (§5). NEUTRAL when `tint`=(1,1,1) (so any chroma the
    demosaic produces is FALSE colour). Returns (linear-RGB ground truth,
    neutral_mask) — the mask is True where the chart is achromatic (tint grey),
    feeding `falsecolor_chroma_energy`."""
    h = w = _even(size)
    yy, xx = np.indices((h, w), dtype=np.float64)
    r2 = (xx - w / 2.0) ** 2 + (yy - h / 2.0) ** 2
    pattern = bias + amplitude * np.cos(k * np.pi * r2 / size)
    t = np.asarray(tint, dtype=np.float64)
    rgb = np.clip(pattern[..., None] * t[None, None, :], 0.0, 1.0)
    neutral = np.isclose(t[0], t[1]) and np.isclose(t[1], t[2])
    mask = np.ones((h, w), dtype=bool) if neutral else np.zeros((h, w), dtype=bool)
    return _olpf(rgb, olpf_sigma), mask


def dead_leaves(
    size: int = 256,
    n_disks: int = 1600,
    *,
    rmin: float = 2.0,
    rmax: float = 64.0,
    chromatic: bool = True,
    seed: int = 0,
    olpf_sigma: float = 0.0,
) -> np.ndarray:
    """Dead-leaves ("spilled coins") model (Cao et al. 2010): overlapping disks
    with radii ~ r^-3 (scale-invariant power spectrum, like natural texture). The
    target for TEXTURE over-smoothing that the slanted edge misses (§5). `chromatic`
    gives random saturated disk colours (colour texture stress); else greyscale.
    Returns linear-RGB ground truth (full-reference, since we know it exactly)."""
    h = w = _even(size)
    rng = np.random.RandomState(seed)
    img = np.full((h, w, 3), 0.5, dtype=np.float64)
    yy, xx = np.indices((h, w), dtype=np.float64)
    # r^-3 disk-radius distribution via inverse-CDF on 1/r^2.
    u = rng.rand(n_disks)
    inv = 1.0 / rmax ** 2 + u * (1.0 / rmin ** 2 - 1.0 / rmax ** 2)
    radii = 1.0 / np.sqrt(inv)
    cx = rng.rand(n_disks) * w
    cy = rng.rand(n_disks) * h
    if chromatic:
        cols = rng.rand(n_disks, 3)
    else:
        g = rng.rand(n_disks, 1)
        cols = np.repeat(g, 3, axis=1)
    for i in range(n_disks):
        rr = radii[i]
        disk = (xx - cx[i]) ** 2 + (yy - cy[i]) ** 2 <= rr * rr
        img[disk] = cols[i]
    return _olpf(img, olpf_sigma)


def siemens_star(
    size: int = 256,
    n_spokes: int = 72,
    *,
    olpf_sigma: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Siemens star (ISO 12233 s-SFR stimulus): radial spokes whose angular
    frequency is fixed but whose spatial frequency rises toward the centre, so
    aliasing onset is visible as a radius. Neutral -> centre region is a false-
    colour probe. Returns (linear-RGB ground truth, neutral_mask=all-True)."""
    h = w = _even(size)
    yy, xx = np.indices((h, w), dtype=np.float64)
    theta = np.arctan2(yy - h / 2.0, xx - w / 2.0)
    val = 0.5 + 0.45 * np.sign(np.cos(n_spokes * theta / 2.0))
    rgb = np.repeat(val[..., None], 3, axis=2)
    mask = np.ones((h, w), dtype=bool)
    return _olpf(rgb, olpf_sigma), mask


# Two SATURATED colours solved for EQUAL luminance Y=0.2126R+0.7152G+0.0722B in
# linear Rec.709 (a red and a green at Y=0.2752, both inside [0,1]): ΔL*≈0.003,
# raw chroma ΔE≈86. "Isoluminant" here is exact-by-construction, not approximate —
# so the luminance channel carries ~no edge signal and a luminance-first demosaicer
# must reconstruct the transition from chrominance alone (the failure boundary).
_ISO_RED = (0.85, 0.12, 0.12)
_ISO_GREEN = (0.12, 0.337, 0.12)


def isoluminant_color_edge(
    size: int = 256,
    angle_deg: float = 5.0,
    color_a: tuple[float, float, float] = _ISO_RED,
    color_b: tuple[float, float, float] = _ISO_GREEN,
    *,
    softness: float = 0.6,
    olpf_sigma: float = 0.0,
) -> np.ndarray:
    """Slanted edge between two ISOLUMINANT SATURATED colours (the documented
    demosaic failure boundary — a chroma edge with ~zero luminance signal, where
    luminance-first demosaicers have the LEAST guidance and tend to fringe; §8).
    Characterisation target (transition-band ΔE), NOT a pass/fail: on this edge a
    blurring demosaic can legitimately beat a directional one (less colour fringe).
    Defaults are solved for equal Y in linear Rec.709 (`_ISO_RED`/`_ISO_GREEN`).
    Returns linear-RGB ground truth."""
    return slanted_edge(
        size, angle_deg, low=color_a, high=color_b, softness=softness, olpf_sigma=olpf_sigma
    )
