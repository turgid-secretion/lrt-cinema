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


def _box_blur_f32(a: np.ndarray, r: int) -> np.ndarray:
    from scipy.ndimage import uniform_filter
    return uniform_filter(a, size=2 * r + 1, mode="nearest")


def _guided_self(a: np.ndarray, r: int, eps: float) -> np.ndarray:
    """He-2010 guided SELF-filter (guide == signal), float32, ndimage box
    means. Regional-map producer for the `guided` absolute-response mode:
    with eps between the within-region texture variance and the
    between-region edge variance, the map smooths texture but follows the
    strong region boundaries (the wall/curtain class)."""
    mean_i = _box_blur_f32(a, r)
    var_i = np.maximum(_box_blur_f32(a * a, r) - mean_i * mean_i, 0.0)
    w = var_i / (var_i + np.float32(eps))
    b = mean_i - w * mean_i
    mean_w = _box_blur_f32(w, r)
    mean_b = _box_blur_f32(b, r)
    return mean_w * a + mean_b


def _guided_joint(coarse: np.ndarray, guide_c: np.ndarray,
                  guide_f: np.ndarray, r: int, eps: float) -> np.ndarray:
    """Guided (joint) UPSAMPLING (Kopf 2007 / He 2010 §4): fit the local
    linear model coarse ~ a*guide_c + b at the coarse scale, upsample a,b
    bilinearly, evaluate against the FULL-res guide — the gain map follows
    full-res edges instead of bleeding across them."""
    from scipy.ndimage import zoom
    mean_g = _box_blur_f32(guide_c, r)
    mean_x = _box_blur_f32(coarse, r)
    cov = _box_blur_f32(coarse * guide_c, r) - mean_x * mean_g
    var = np.maximum(_box_blur_f32(guide_c * guide_c, r) - mean_g * mean_g,
                     0.0)
    a = cov / (var + np.float32(eps))
    b = mean_x - a * mean_g
    zoom_f = (guide_f.shape[0] / coarse.shape[0],
              guide_f.shape[1] / coarse.shape[1])
    a_f = zoom(a, zoom_f, order=1, mode="nearest", grid_mode=True)
    b_f = zoom(b, zoom_f, order=1, mode="nearest", grid_mode=True)
    return a_f * guide_f + b_f


def llf_apply_tone(
    channel: np.ndarray,
    delta_fn: Callable[[np.ndarray], np.ndarray],
    sigma_r: float,
    n_gamma: int,
    gamma_lo: float,
    gamma_hi: float,
    last_level: int,
    absolute_mode: str = "gauss",
    guided_down: int = 8,
    guided_radius: int = 24,
    guided_eps: float = 8.0,
    global_blend: float = 0.0,
    delta_split: tuple[Callable[[np.ndarray], np.ndarray],
                       Callable[[np.ndarray], np.ndarray]] | None = None,
    guard_temp: float = 1.0,
    guard_fine_level: int | None = None,
    multi_levels: tuple[int, ...] = (3, 4, 5),
) -> np.ndarray:
    """Apply the additive log2 tone delta `delta_fn` to `channel`
    (log2-luminance, 2-D float) with detail preservation.

    `gamma_lo/hi`: FIXED scene-referred discretization bounds (never
    per-frame statistics — a per-frame grid would flicker in a timelapse;
    the archived proto's pin, kept). `last_level` bounds the pyramid depth.

    `absolute_mode` — where the ABSOLUTE calibrated response lives (the
    round-2 halo campaign's arm axis; evidence hlsh_artifact_suite):
      * "gauss"  — v2 baseline: residual := G_L + delta(G_L). The Gaussian
        residual mixes regions across strong edges → the measured
        pool/halo class (owner round-2 verdict).
      * "guided" — residual untouched; the absolute response is
        delta(M) where M = guided SELF-filtered regional map computed at
        ÷`guided_down`, upsampled EDGE-AWARELY against the full-res
        channel (joint guided upsampling) — region boundaries stay crisp,
        texture stays out of the map (eps between texture and edge
        variance).
      * "none"   — residual untouched, no absolute term (dt-style: only
        the edge-arm slopes act; the global response then comes from
        `global_blend`).
      * "gauss_guard" — like "gauss" but POLARITY-GUARDED per slider
        (requires `delta_split = (delta_highlights, delta_shadows)`):
        the shadows-lift delta is evaluated at smooth-max(region, pixel)
        and the highlights-recovery delta at smooth-min(region, pixel)
        (region = the upsampled residual Gaussian; `guard_temp` = the
        smooth-max softness in stops). A bright feature inside a lifted
        dark region reads its OWN luminance, never inherits the region's
        lift (the measured crop1 wall-glow / 16-px-blob class); dark
        texture within the region still reads the regional value, so
        flatness is preserved. Residual itself stays untouched; the
        absolute response is added at full resolution.
    `global_blend` (tau): mix tau of the PER-PIXEL global map
    `delta(channel)` into the absolute response (1.0 = pure global curve:
    halo-free but flat; 0.0 = fully regional).

    Degenerate layouts (too small for one reduce) fall back to the GLOBAL
    per-pixel map `channel + delta(channel)` — the zero-locality limit.
    """
    if absolute_mode == "gauss" and global_blend > 0.0:
        raise ValueError("global_blend composes with 'guided'/'none' modes "
                         "only — 'gauss' already carries a full absolute "
                         "response in the residual")
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

    # ---- the ABSOLUTE calibrated response (arm axis; see docstring) ----
    res = gauss[-1]
    if absolute_mode == "gauss":
        out = (res + delta_fn(res)).astype(np.float32)
    else:
        out = res.astype(np.float32, copy=True)   # residual untouched
    for lev in range(nlev - 2, -1, -1):
        out = out_lap[lev] + _upsample(out, out_lap[lev].shape)

    absolute = None
    if absolute_mode == "gauss_multi":
        # residual untouched; the absolute response is the MEAN of the
        # calibrated delta evaluated at several Gaussian scales — each
        # scale's region-bleed pool is smaller and they don't align, so
        # the composite pool edge is softer than any single scale's.
        acc = None
        for lev in multi_levels:
            fl = min(lev, nlev - 1)
            m = gauss[fl]
            for lv in range(fl - 1, -1, -1):
                m = _upsample(m, gauss[lv].shape)
            d = delta_fn(m).astype(np.float32)
            acc = d if acc is None else acc + d
        absolute = acc / np.float32(len(multi_levels))
    if absolute_mode == "gauss_guard":
        if delta_split is None:
            raise ValueError("gauss_guard needs delta_split=(dH, dS)")
        d_h, d_s = delta_split
        region = res
        for lev in range(nlev - 2, -1, -1):
            region = _upsample(region, gauss[lev].shape)
        # the feature signal the guard compares against: per-pixel, or a
        # FINE Gaussian level (guard_fine_level) so sub-2^fine texture
        # stays flat-immune (only feature-scale structure is protected)
        if guard_fine_level is None or guard_fine_level <= 0:
            fine = ch
        else:
            fl = min(guard_fine_level, nlev - 1)
            fine = gauss[fl]
            for lev in range(fl - 1, -1, -1):
                fine = _upsample(fine, gauss[lev].shape)
        t = np.float32(guard_temp)
        half = np.float32(0.5)
        diff = region - fine
        mag = np.sqrt(diff * diff + t * t)
        smooth_max = half * (region + fine + mag)  # >= max(region, fine)
        smooth_min = half * (region + fine - mag)  # <= min(region, fine)
        absolute = (d_s(smooth_max) + d_h(smooth_min)).astype(np.float32)
    if absolute_mode == "guided":
        # regional map at reduced res (speed), edge-aware everywhere:
        # guided self-filter for the map, joint guided upsample back.
        small = ch[::guided_down, ::guided_down]
        m = _guided_self(small, guided_radius, guided_eps)
        dm = delta_fn(m).astype(np.float32)
        absolute = _guided_joint(dm, small, ch, guided_radius, guided_eps)
    if global_blend > 0.0:
        g = delta_fn(ch).astype(np.float32) * np.float32(global_blend)
        absolute = g if absolute is None else (
            np.float32(1.0 - global_blend) * absolute + g)
    if absolute is not None:
        out = out + absolute
    return out
