"""Clean-room fast Local Laplacian filter — RESEARCH PROTOTYPE (UNWIRED).

NOT imported by the render pipeline. Preserved for the Texture/Clarity task
(v0.9 step 4 detail-boost), where Local Laplacian is used as designed (small-radius
detail manipulation, alpha < 1). For DR-compression it was measured and DEFERRED —
it does not beat the shipping guided filter on halos as a *base* producer; see
docs/research/v10c-local-laplacian-base-deferred.md.

Clean-room numpy reimplementation informed by the MIT-licensed reference
implementation of:

    Paris, Hasinoff, Kautz, "Local Laplacian Filters: Edge-aware Image Processing
    with a Laplacian Pyramid", ACM TOG 30(4) (SIGGRAPH 2011).

and the fast (discretized-intensity) acceleration of:

    Aubry, Paris, Hasinoff, Kautz, Durand, "Fast Local Laplacian Filters: Theory
    and Applications", ACM TOG 33(5), 2014.

The reference MATLAB carries the following notice, retained here as required:

    Copyright (c) 2011 Sam Hasinoff — MIT License.
    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction ... THE SOFTWARE IS PROVIDED "AS IS".

Operates on a single-channel (log-luminance) array. Pins (documented tuning):
  * sigma_r in log2 stops (the channel is log2; default log2(2.5))
  * a FIXED scene-referred gamma grid (anchor +/- a fixed stop span), spaced every
    sigma_r — NEVER per-frame min/max (a per-frame statistic would re-introduce
    temporal flicker; v10 sec 3.7)
  * display tail (percentile renorm + gamma + clip) DISCARDED — pyramid+remap+collapse
    only
  * degenerate-layout escape: return input.copy() when no pyramid fits (so a 1-wide
    array reduces the consumer to its global path bit-for-bit)
"""
from __future__ import annotations

import math

import numpy as np
from scipy.ndimage import correlate1d

# Burt-Adelson 5-tap separable low-pass [.05 .25 .4 .25 .05].
_PYR_KERNEL = np.array([0.05, 0.25, 0.4, 0.25, 0.05], dtype=np.float64)

# Intensity-ratio threshold separating detail from edges, in LOG2 stops.
SIGMA_R = math.log2(2.5)

# Fixed scene-referred gamma-grid bounds (log2 stops, around the 0.18 anchor). A
# generous fixed working range so g0 rarely clamps; clamp (not extend) when it does.
_LOG_ANCHOR = math.log2(0.18)
GAMMA_LO = _LOG_ANCHOR - 10.0
GAMMA_HI = _LOG_ANCHOR + 6.0


def _filter2(img: np.ndarray) -> np.ndarray:
    out = correlate1d(img, _PYR_KERNEL, axis=0, mode="constant", cval=0.0)
    return correlate1d(out, _PYR_KERNEL, axis=1, mode="constant", cval=0.0)


def _downsample(img: np.ndarray) -> np.ndarray:
    filtered = _filter2(img) / _filter2(np.ones(img.shape))  # reweighted borders
    return filtered[0::2, 0::2]


def _upsample(img: np.ndarray, out_shape: tuple[int, int]) -> np.ndarray:
    r, c = out_shape
    up = np.zeros((r, c))
    up[0::2, 0::2] = img
    w = np.zeros((r, c))
    w[0::2, 0::2] = 1.0
    return _filter2(up) / np.maximum(_filter2(w), 1e-12)


def _num_levels(shape: tuple[int, int]) -> int:
    m = min(shape)
    n = 1
    while m > 1:
        n += 1
        m = (m + 1) // 2
    return n


def _gaussian_pyramid(img: np.ndarray, nlev: int) -> list[np.ndarray]:
    pyr = [img]
    for _ in range(nlev - 1):
        img = _downsample(img)
        pyr.append(img)
    return pyr


def _laplacian_pyramid(img: np.ndarray, nlev: int) -> list[np.ndarray]:
    pyr: list[np.ndarray] = []
    j = img
    for _ in range(nlev - 1):
        small = _downsample(j)
        pyr.append(j - _upsample(small, j.shape))
        j = small
    pyr.append(j)  # coarsest = residual
    return pyr


def _collapse(pyr: list[np.ndarray]) -> np.ndarray:
    r = pyr[-1]
    for lev in range(len(pyr) - 2, -1, -1):
        r = pyr[lev] + _upsample(r, pyr[lev].shape)
    return r


def remap(img: np.ndarray, g0: float, sigma_r: float, alpha: float, beta: float) -> np.ndarray:
    """Paris-2011 grayscale pointwise remap r(i; g0). NO gradient term.

    detail (|d| <= sigma_r): g0 + sign(d)*sigma_r*(|d|/sigma_r)^alpha   (fd = d^alpha)
    edge   (|d| >  sigma_r): g0 + sign(d)*(beta*(|d|-sigma_r) + sigma_r) (fe = beta*a)
    """
    d = img - g0
    dnrm = np.abs(d)
    dsgn = np.sign(d)
    rd = g0 + dsgn * sigma_r * ((dnrm / sigma_r) ** alpha)
    re = g0 + dsgn * (beta * (dnrm - sigma_r) + sigma_r)
    return np.where(dnrm > sigma_r, re, rd)


def local_laplacian(
    channel: np.ndarray,
    sigma_r: float = SIGMA_R,
    alpha: float = 1.0,
    beta: float = 1.0,
    gamma_lo: float = GAMMA_LO,
    gamma_hi: float = GAMMA_HI,
    gamma_spacing: float | None = None,
) -> np.ndarray:
    """Fast Local Laplacian filter on a single channel (Aubry-2014 discretization).

    For a BASE producer use alpha>1, beta=1 (smooth detail away, keep edges). For a
    DETAIL boost (Texture/Clarity) use alpha<1, beta=1 at a small radius.
    """
    nlev = _num_levels(channel.shape)
    if nlev < 2:
        return channel.astype(np.float64, copy=True)  # degenerate-layout escape
    spacing = sigma_r if gamma_spacing is None else gamma_spacing
    n_gamma = max(2, int(math.ceil((gamma_hi - gamma_lo) / spacing)) + 1)
    gammas = np.linspace(gamma_lo, gamma_hi, n_gamma)

    gauss = _gaussian_pyramid(channel, nlev)
    lap_pyrs = [_laplacian_pyramid(remap(channel, g, sigma_r, alpha, beta), nlev) for g in gammas]

    out_lap: list[np.ndarray] = []
    for lev in range(nlev - 1):
        g0 = gauss[lev]
        idx = np.clip(np.searchsorted(gammas, g0) - 1, 0, n_gamma - 2)
        gl = gammas[idx]
        gr = gammas[idx + 1]
        w = np.clip((g0 - gl) / (gr - gl), 0.0, 1.0)
        stacked = np.stack([lp[lev] for lp in lap_pyrs], axis=0)  # (n_gamma, r, c)
        rr, cc = np.indices(g0.shape)
        out_lap.append((1.0 - w) * stacked[idx, rr, cc] + w * stacked[idx + 1, rr, cc])
    out_lap.append(gauss[-1])  # residual untouched
    return _collapse(out_lap)
