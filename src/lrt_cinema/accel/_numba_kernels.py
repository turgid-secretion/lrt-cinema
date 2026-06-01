"""Numba-JIT fused kernels for the per-pixel render hotspots.

This module is imported **only** when the numba backend is selected and numba
is importable (see `lrt_cinema.accel`). Every kernel is a fused, single-pass
`prange` port of the pure-numpy reference it replaces — same maths, same
operation order, same float precision *where precision is load-bearing*:

  * the HSV-cube kernel runs in **float32** (the numpy reference is float32:
    float32 RGB × float32 cube), and
  * the tone-curve kernel evaluates the Hermite spline in **float64** (the
    numpy `DngSplineSolver.evaluate` is float64; a float32 evaluate drifts the
    128-pt curve — verified by the kernel-equivalence test).

Fusion is the win, not just threads: the numpy path materialises ~a dozen
24-megapixel temporaries per stage (the HSV cube alone gathers 8 corner arrays);
the fused kernel keeps each pixel's scratch in registers and writes the result
once. `fastmath=False` on every kernel — reassociation would change the
reduction order *and* assumes no NaN/Inf, which collides with the output
stage's NaN scrub. `cache=True` persists the compiled object to disk so each
ProcessPool worker pays ~0.2 s (cache load) not ~0.8 s (recompile).

Correctness owner: `tests/test_accel_kernels.py` (numpy-twin equivalence on
random + saturated + clipped + tied pixels; skipped when numba is absent).
"""

from __future__ import annotations

import numpy as np
from numba import njit, prange

# --- scalar transfer functions (match lut3d_baker._srgb_oetf/_srgb_eotf) ----


@njit(cache=True, fastmath=False, inline="always")
def _oetf(x: float) -> float:
    """sRGB OETF, scalar. Input is pre-clamped >=0 by the caller (matches the
    numpy reference's `np.clip(v, 0.0, None)` before `_srgb_oetf`)."""
    if x <= 0.0031308:
        return x * 12.92
    return 1.055 * x ** (1.0 / 2.4) - 0.055


@njit(cache=True, fastmath=False, inline="always")
def _eotf(x: float) -> float:
    """sRGB EOTF, scalar. Input is clamped to [0, 1] by the caller (matches the
    SDK Pin_real32 before the EOTF decode in `_apply_hsv_cube`)."""
    if x <= 0.04045:
        return x / 12.92
    return ((x + 0.055) / 1.055) ** 2.4


# --- fused HSV-cube kernel (Stage 5 HueSatMap / Stage 8 LookTable) ----------


@njit(parallel=True, cache=True, fastmath=False)
def lut_cube_rgb(rgb, cube, h_div, s_div, v_div, srgb_gamma):
    """Fused RGB→HSV → trilinear cube → HSV→RGB with negative-pixel passthrough.

    `rgb` (N, 3) float32 linear ProPhoto; `cube` (V, H, S, 3) float32. Returns
    (N, 3) float32. One pass replaces `_rgb_to_hsv_dcp` + `_apply_hsv_cube` +
    `_hsv_to_rgb_dcp` + the `np.where(valid, post, pre)` passthrough — the exact
    composed Stage-8 (and Stage-5) numpy reference, byte-for-byte in intent.

    Hue WRAPS (index ceiling → 0) and sat/val CLAMP, matching the reference.
    Pixels with any negative component fall back to the input unchanged (the
    reference's `valid_mask = var_min >= 0` passthrough)."""
    n = rgb.shape[0]
    out = np.empty_like(rgb)

    h_scale = (h_div / 6.0) if h_div >= 2 else 0.0
    s_scale = np.float32(s_div - 1)
    v_scale = np.float32(v_div - 1)
    max_h_i0 = h_div - 1
    max_s_i0 = (s_div - 2) if s_div >= 2 else 0
    max_v_i0 = (v_div - 2) if v_div >= 2 else 0

    for i in prange(n):
        r = rgb[i, 0]
        g = rgb[i, 1]
        b = rgb[i, 2]

        vmin = min(r, g, b)
        # Negative-component pixels: matrix-only passthrough (reference fallback).
        if vmin < np.float32(0.0):
            out[i, 0] = r
            out[i, 1] = g
            out[i, 2] = b
            continue

        vmax = max(r, g, b)
        delta = vmax - vmin
        v = vmax
        s = (delta / vmax) if vmax > np.float32(0.0) else np.float32(0.0)
        if delta > np.float32(1e-10) or delta < np.float32(-1e-10):
            if r >= vmax:          # r == vmax (>= mirrors np.where(r==var_max,...))
                h = (g - b) / delta
            elif g >= vmax:
                h = np.float32(2.0) + (b - r) / delta
            else:
                h = np.float32(4.0) + (r - g) / delta
            if h < np.float32(0.0):
                h += np.float32(6.0)
            elif h >= np.float32(6.0):
                h -= np.float32(6.0)
        else:
            h = np.float32(0.0)

        # --- trilinear sample of the (V, H, S, 3) cube ---
        h_scaled = h * np.float32(h_scale)
        s_scaled = s * s_scale
        if srgb_gamma:
            v_enc = np.float32(_oetf(v if v > np.float32(0.0) else np.float32(0.0)))
        else:
            v_enc = v
        v_scaled = v_enc * v_scale

        h_i0 = int(np.floor(h_scaled))
        if h_i0 < 0:
            h_i0 = 0
        elif h_i0 > max_h_i0:
            h_i0 = max_h_i0
        h_i1 = 0 if h_i0 >= max_h_i0 else h_i0 + 1

        s_i0 = int(np.floor(s_scaled))
        if s_i0 < 0:
            s_i0 = 0
        elif s_i0 > max_s_i0:
            s_i0 = max_s_i0
        s_i1 = s_i0 + 1
        if s_i1 > s_div - 1:
            s_i1 = s_div - 1

        v_i0 = int(np.floor(v_scaled))
        if v_i0 < 0:
            v_i0 = 0
        elif v_i0 > max_v_i0:
            v_i0 = max_v_i0
        v_i1 = v_i0 + 1
        if v_i1 > v_div - 1:
            v_i1 = v_div - 1

        hf1 = h_scaled - h_i0
        hf1 = np.float32(0.0) if hf1 < 0.0 else (np.float32(1.0) if hf1 > 1.0 else hf1)
        sf1 = s_scaled - s_i0
        sf1 = np.float32(0.0) if sf1 < 0.0 else (np.float32(1.0) if sf1 > 1.0 else sf1)
        vf1 = v_scaled - v_i0
        vf1 = np.float32(0.0) if vf1 < 0.0 else (np.float32(1.0) if vf1 > 1.0 else vf1)
        hf0 = np.float32(1.0) - hf1
        sf0 = np.float32(1.0) - sf1
        vf0 = np.float32(1.0) - vf1

        w000 = vf0 * hf0 * sf0
        w001 = vf0 * hf0 * sf1
        w010 = vf0 * hf1 * sf0
        w011 = vf0 * hf1 * sf1
        w100 = vf1 * hf0 * sf0
        w101 = vf1 * hf0 * sf1
        w110 = vf1 * hf1 * sf0
        w111 = vf1 * hf1 * sf1

        # sampled[k] = trilinear blend of the 8 corners, per channel k.
        hue_shift = (
            w000 * cube[v_i0, h_i0, s_i0, 0] + w001 * cube[v_i0, h_i0, s_i1, 0]
            + w010 * cube[v_i0, h_i1, s_i0, 0] + w011 * cube[v_i0, h_i1, s_i1, 0]
            + w100 * cube[v_i1, h_i0, s_i0, 0] + w101 * cube[v_i1, h_i0, s_i1, 0]
            + w110 * cube[v_i1, h_i1, s_i0, 0] + w111 * cube[v_i1, h_i1, s_i1, 0]
        )
        sat_scale = (
            w000 * cube[v_i0, h_i0, s_i0, 1] + w001 * cube[v_i0, h_i0, s_i1, 1]
            + w010 * cube[v_i0, h_i1, s_i0, 1] + w011 * cube[v_i0, h_i1, s_i1, 1]
            + w100 * cube[v_i1, h_i0, s_i0, 1] + w101 * cube[v_i1, h_i0, s_i1, 1]
            + w110 * cube[v_i1, h_i1, s_i0, 1] + w111 * cube[v_i1, h_i1, s_i1, 1]
        )
        val_scale = (
            w000 * cube[v_i0, h_i0, s_i0, 2] + w001 * cube[v_i0, h_i0, s_i1, 2]
            + w010 * cube[v_i0, h_i1, s_i0, 2] + w011 * cube[v_i0, h_i1, s_i1, 2]
            + w100 * cube[v_i1, h_i0, s_i0, 2] + w101 * cube[v_i1, h_i0, s_i1, 2]
            + w110 * cube[v_i1, h_i1, s_i0, 2] + w111 * cube[v_i1, h_i1, s_i1, 2]
        )

        # --- apply the sampled hue shift / sat & val scales ---
        h_out = h + hue_shift * np.float32(6.0 / 360.0)
        if h_out < np.float32(0.0):
            h_out += np.float32(6.0)
        elif h_out >= np.float32(6.0):
            h_out -= np.float32(6.0)
        s_out = s * sat_scale
        s_out = np.float32(0.0) if s_out < 0.0 else (np.float32(1.0) if s_out > 1.0 else s_out)
        if srgb_gamma:
            v_enc_out = v_enc * val_scale
            v_enc_out = (np.float32(0.0) if v_enc_out < 0.0
                         else (np.float32(1.0) if v_enc_out > 1.0 else v_enc_out))
            v_out = np.float32(_eotf(v_enc_out))
        else:
            v_out = v * val_scale

        # --- HSV → RGB (hexcone, matches _hsv_to_rgb_dcp) ---
        sector = int(np.floor(h_out))
        f = h_out - sector
        if sector < 0:
            sector = 0
        elif sector > 5:
            sector = 5
        p = v_out * (np.float32(1.0) - s_out)
        q = v_out * (np.float32(1.0) - f * s_out)
        t = v_out * (np.float32(1.0) - (np.float32(1.0) - f) * s_out)
        if sector == 0:
            rr, gg, bb = v_out, t, p
        elif sector == 1:
            rr, gg, bb = q, v_out, p
        elif sector == 2:
            rr, gg, bb = p, v_out, t
        elif sector == 3:
            rr, gg, bb = p, q, v_out
        elif sector == 4:
            rr, gg, bb = t, p, v_out
        else:
            rr, gg, bb = v_out, p, q
        out[i, 0] = rr
        out[i, 1] = gg
        out[i, 2] = bb
    return out


# --- fused hue/sat-preserving tone curve (Stage 9, RefBaselineRGBTone) ------


@njit(cache=True, fastmath=False, inline="always")
def _spline_eval(x: float, X, Y, S, count: int) -> float:
    """Scalar Hermite-spline evaluate in float64 — a 1:1 port of
    `pipeline.DngSplineSolver.evaluate` (bisect-left + the same D formula)."""
    if x <= X[0]:
        return Y[0]
    if x >= X[count - 1]:
        return Y[count - 1]
    # bisect-left == np.searchsorted(X, x, side="left")
    lo = 0
    hi = count
    while lo < hi:
        mid = (lo + hi) // 2
        if X[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    j = lo
    if j < 1:
        j = 1
    elif j > count - 1:
        j = count - 1
    x0 = X[j - 1]
    y0 = Y[j - 1]
    s0 = S[j - 1]
    x1 = X[j]
    y1 = Y[j]
    s1 = S[j]
    A = x1 - x0
    B = (x - x0) / A
    C = (x1 - x) / A
    return ((y0 * (2.0 - C + B) + (s0 * A * B)) * (C * C)
            + (y1 * (2.0 - B + C) - (s1 * A * C)) * (B * B))


@njit(cache=True, fastmath=False, inline="always")
def _pick(c0, c1, c2, idx: int):
    """Return the channel value at `idx` (0/1/2) — branch-pick of three scalars."""
    if idx == 0:
        return c0
    if idx == 1:
        return c1
    return c2


@njit(parallel=True, cache=True, fastmath=False)
def rgb_tone_spline(rgb, X, Y, S):
    """Fused `apply_rgb_tone` with the spline curve inlined (Stage 9).

    `rgb` (N, 3) float32; `X, Y, S` float64 spline arrays (the solved
    `DngSplineSolver`). Curves the per-pixel max & min channels through the
    spline (float64) and linearly interpolates the middle channel between the
    two curved extremes — Adobe's hue/saturation-preserving `RefBaselineRGBTone`,
    NOT per-channel. Input pinned to [0, 1] in float32 (matching the reference's
    `np.clip(rgb, 0, 1)`), output clamped to [0, 1] float32.

    The reference uses a stable argsort for [min, mid, max]; this uses
    argmin/argmax with the middle index = 3 − imin − imax. Equal channels curve
    to equal outputs, so the two agree on ties (verified by the equivalence
    test) without materialising a sort."""
    n = rgb.shape[0]
    count = X.shape[0]
    out = np.empty_like(rgb)
    z = np.float32(0.0)
    one = np.float32(1.0)
    for i in prange(n):
        # Pin to [0, 1] (Adobe Pin_real32) in float32, like the reference.
        c0 = rgb[i, 0]
        c1 = rgb[i, 1]
        c2 = rgb[i, 2]
        c0 = z if c0 < z else (one if c0 > one else c0)
        c1 = z if c1 < z else (one if c1 > one else c1)
        c2 = z if c2 < z else (one if c2 > one else c2)

        # index of max and min over the 3 channels (first-wins on ties, like
        # np.where(r==max,...) / a stable argsort's label choice).
        if c0 >= c1 and c0 >= c2:
            imax = 0
        elif c1 >= c2:
            imax = 1
        else:
            imax = 2
        if c0 <= c1 and c0 <= c2:
            imin = 0
        elif c1 <= c2:
            imin = 1
        else:
            imin = 2

        mx = _pick(c0, c1, c2, imax)
        mn = _pick(c0, c1, c2, imin)
        mx_o = _spline_eval(mx, X, Y, S, count)
        mx_o = 0.0 if mx_o < 0.0 else (1.0 if mx_o > 1.0 else mx_o)
        mn_o = _spline_eval(mn, X, Y, S, count)
        mn_o = 0.0 if mn_o < 0.0 else (1.0 if mn_o > 1.0 else mn_o)

        if imax == imin:
            # all three equal (neutral): every channel → curve(v).
            out[i, 0] = mx_o
            out[i, 1] = mx_o
            out[i, 2] = mx_o
            continue
        imid = 3 - imax - imin
        md = _pick(c0, c1, c2, imid)
        delta = mx - mn  # float32, like the reference
        if delta > z:
            md_o = mn_o + (mx_o - mn_o) * (md - mn) / delta
        else:
            md_o = _spline_eval(md, X, Y, S, count)
        md_o = 0.0 if md_o < 0.0 else (1.0 if md_o > 1.0 else md_o)

        out[i, imax] = mx_o
        out[i, imin] = mn_o
        out[i, imid] = md_o
    return out
