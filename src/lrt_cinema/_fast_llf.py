"""Fast Local-Laplacian tone application for the H/S translation (v2 core).

Applies an ARBITRARY calibrated tone delta Δ(g) (log2-domain, additive) to a
log2-luminance channel through the fast Local-Laplacian machinery (Aubry
2014 discretization of Paris/Hasinoff/Kautz 2011) so that:

  * per-pixel DETAIL is preserved verbatim (remap detail arm, slope 1 for
    |i − g| ≤ sigma_r) — the affected region keeps its local contrast (the
    owner-rejected "flat look" of a per-pixel curve / edge-tracking base);
  * EDGES (|i − g| > sigma_r) are remapped with the tone map's own local
    slope `1 + Δ'(g)` — inter-region transitions compress consistently
    with the curve, which is LLF's halo-distribution mechanism (the
    integrated form whose halo behaviour v10c explicitly exempted from its
    LLF-as-base defer; darktable's local-contrast local-laplacian mode is
    the shipping precedent, read-to-learn 2026-07-08);
  * the ABSOLUTE calibrated response is carried by the pyramid RESIDUAL:
    G_L + Δ(G_L) at a REGIONAL scale (last level ~2^L px), not per-pixel.
    On smooth content the collapse telescopes back to ≈ i + Δ(i); the
    residual-vs-slope approximation bias is absorbed EMPIRICALLY by the
    calibration refinement loop (tools/cal_hlsh_fit.py fits the tables
    THROUGH this operator against the owner LR exports).

No clarity/detail-boost term on purpose: the owner judged Adobe's
sharpened/"clarity-enhanced" recovered highlights as odd/fake; detail is
preserved at unity, never boosted (darktable exposes such a term; we pin
it to zero by construction).

Pyramid / remap provenance: clean-room numpy, structure from the
MIT-licensed reference implementation of Paris, Hasinoff, Kautz, "Local
Laplacian Filters" (ACM TOG 30(4), 2011) and the discretized-intensity
acceleration of Aubry, Paris, Hasinoff, Kautz, Durand, "Fast Local
Laplacian Filters" (ACM TOG 33(5), 2014), via the archived prototype
`docs/archive/research/_proto_local_laplacian.py`. The reference MATLAB
carries this notice, retained as required:

    Copyright (c) 2011 Sam Hasinoff — MIT License.
    Permission is hereby granted, free of charge, to any person obtaining
    a copy of this software and associated documentation files (the
    "Software"), to deal in the Software without restriction ... THE
    SOFTWARE IS PROVIDED "AS IS".
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from scipy.ndimage import correlate1d

# Burt–Adelson 5-tap separable low-pass [.05 .25 .4 .25 .05].
# float32 throughout: the channel is log2 luminance (tone, not colorimetric
# accuracy); f32 keeps ~1e-7 relative precision and halves time + memory.
_PYR_KERNEL = np.array([0.05, 0.25, 0.4, 0.25, 0.05], dtype=np.float32)

# Border-reweight denominators are shape-dependent constants — cache them
# (one per pyramid level shape; a handful of small arrays).
_norm_cache: dict[tuple[int, int], np.ndarray] = {}
_upnorm_cache: dict[tuple[int, int], np.ndarray] = {}


def _filter2(img: np.ndarray) -> np.ndarray:
    out = correlate1d(img, _PYR_KERNEL, axis=0, mode="constant", cval=0.0)
    return correlate1d(out, _PYR_KERNEL, axis=1, mode="constant", cval=0.0)


def _downsample(img: np.ndarray) -> np.ndarray:
    key = img.shape
    norm = _norm_cache.get(key)
    if norm is None:
        norm = _filter2(np.ones(key, dtype=np.float32))
        _norm_cache[key] = norm
    return (_filter2(img) / norm)[0::2, 0::2]


def _upsample(img: np.ndarray, out_shape: tuple[int, int]) -> np.ndarray:
    r, c = out_shape
    up = np.zeros((r, c), dtype=np.float32)
    up[0::2, 0::2] = img
    norm = _upnorm_cache.get(out_shape)
    if norm is None:
        w = np.zeros((r, c), dtype=np.float32)
        w[0::2, 0::2] = 1.0
        norm = np.maximum(_filter2(w), np.float32(1e-12))
        _upnorm_cache[out_shape] = norm
    return _filter2(up) / norm


def _gaussian_pyramid(img: np.ndarray, nlev: int) -> list[np.ndarray]:
    pyr = [img]
    for _ in range(nlev - 1):
        img = _downsample(img)
        pyr.append(img)
    return pyr


def _remap(img: np.ndarray, g0: float, delta_fn: Callable[[np.ndarray], np.ndarray],
           sigma_r: float, slope_eps: float = 0.25) -> np.ndarray:
    """Tone remap around the discretization point `g0`.

    detail (|d| <= sigma_r): g0 + d                      (slope 1 — preserved)
    edge   (|d| >  sigma_r): g0 + sign(d)*(sigma_r + s(g0)*(|d| - sigma_r))
        with s(g0) = 1 + Δ'(g0) (finite difference over sigma_r), clamped to
        [slope_eps, 4] — the tone map's own local slope, never a collapse
        to zero (keeps the remap strictly monotone).
    """
    d = img - np.float32(g0)
    dnrm = np.abs(d)
    dp = float(delta_fn(np.array([g0 + 0.5 * sigma_r]))[0]
               - delta_fn(np.array([g0 - 0.5 * sigma_r]))[0]) / sigma_r
    s = np.float32(np.clip(1.0 + dp, slope_eps, 4.0))
    detail = np.float32(g0) + d
    edge = np.float32(g0) + np.sign(d) * (
        np.float32(sigma_r) + s * (dnrm - np.float32(sigma_r)))
    return np.where(dnrm > np.float32(sigma_r), edge, detail)


def llf_apply_tone(
    channel: np.ndarray,
    delta_fn: Callable[[np.ndarray], np.ndarray],
    sigma_r: float,
    n_gamma: int,
    gamma_lo: float,
    gamma_hi: float,
    last_level: int,
) -> np.ndarray:
    """Apply the additive log2 tone delta `delta_fn` to `channel`
    (log2-luminance, 2-D float) with detail preservation.

    `gamma_lo/hi`: FIXED scene-referred discretization bounds (never
    per-frame statistics — a per-frame grid would flicker in a timelapse;
    the archived proto's pin, kept). `last_level` bounds the pyramid depth:
    the residual carries `G_L + delta(G_L)`, so 2**last_level px is the
    regional scale of the absolute tone map.

    Degenerate layouts (too small for one reduce) fall back to the GLOBAL
    per-pixel map `channel + delta(channel)` — the zero-locality limit.
    """
    ch = channel.astype(np.float32, copy=False)
    nlev = min(last_level + 1,
               int(np.floor(np.log2(max(min(ch.shape), 1)))) + 1)
    if ch.ndim != 2 or nlev < 2:
        return (ch + delta_fn(ch)).astype(np.float32)

    gammas = np.linspace(gamma_lo, gamma_hi, n_gamma)
    gauss = _gaussian_pyramid(ch, nlev)

    # Laplacian pyramids of each remapped image, built level-by-level in
    # lockstep (memory: n_gamma buffers at the current level, not n_gamma
    # full pyramids). Per-coefficient interpolation via take_along_axis on
    # the gamma axis (no full index grids).
    remapped = [_remap(ch, g, delta_fn, sigma_r) for g in gammas]
    out_lap: list[np.ndarray] = []
    for lev in range(nlev - 1):
        g0 = gauss[lev]
        idx = np.clip(np.searchsorted(gammas, g0) - 1, 0, n_gamma - 2)
        gl = gammas[idx]
        gr = gammas[idx + 1]
        w = ((np.clip((g0 - gl) / (gr - gl), 0.0, 1.0))
             .astype(np.float32))
        laps = []
        nexts = []
        for k in range(n_gamma):
            small = _downsample(remapped[k])
            laps.append(remapped[k] - _upsample(small, remapped[k].shape))
            nexts.append(small)
        remapped = nexts
        stacked = np.stack(laps, axis=-1)          # (r, c, n_gamma)
        lo = np.take_along_axis(stacked, idx[..., None], axis=-1)[..., 0]
        hi = np.take_along_axis(stacked, (idx + 1)[..., None], axis=-1)[..., 0]
        out_lap.append((1.0 - w) * lo + w * hi)

    # Residual: the ABSOLUTE calibrated tone applied at the regional scale.
    res = gauss[-1]
    out = (res + delta_fn(res)).astype(np.float32)
    for lev in range(nlev - 2, -1, -1):
        out = out_lap[lev] + _upsample(out, out_lap[lev].shape)
    return out
