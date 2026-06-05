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


# ---------------------------------------------------------------------------
# Stage-12 FAITHFUL grade ops (reusable scalar HSV helpers + kernels)
# ---------------------------------------------------------------------------
#
# These mirror develop_ops.{apply_saturation,apply_vibrance,apply_hsl,
# apply_color_grade} exactly, including the numpy reference's float PRECISION:
# Saturation/Vibrance run float32 (float32 array × weak python float); HSL's
# per-band weighted sums and Color-Grade run float64 (numpy promotes there) —
# matched here so the kernels stay bit-tight to numpy (verified by
# tests/test_accel_kernels.py).


@njit(cache=True, fastmath=False, inline="always")
def _rgb2hsv(r, g, b):
    """Scalar Adobe-hexcone RGB→HSV. Returns (h in [0,6), s, v, valid)."""
    vmin = min(r, g, b)
    vmax = max(r, g, b)
    delta = vmax - vmin
    valid = vmin >= 0.0
    v = vmax
    s = (delta / vmax) if vmax > 0.0 else 0.0 * vmax
    if delta > 1e-10 or delta < -1e-10:
        if r >= vmax:
            h = (g - b) / delta
        elif g >= vmax:
            h = 2.0 + (b - r) / delta
        else:
            h = 4.0 + (r - g) / delta
        if h < 0.0:
            h += 6.0
        elif h >= 6.0:
            h -= 6.0
    else:
        h = 0.0 * vmax
    return h, s, v, valid


@njit(cache=True, fastmath=False, inline="always")
def _hsv2rgb(h, s, v):
    """Scalar Adobe-hexcone HSV→RGB (h in [0,6)). Returns (r, g, b)."""
    if h < 0.0:
        h += 6.0
    elif h >= 6.0:
        h -= 6.0
    sector = int(np.floor(h))
    f = h - np.floor(h)
    if sector < 0:
        sector = 0
    elif sector > 5:
        sector = 5
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    if sector == 0:
        return v, t, p
    if sector == 1:
        return q, v, p
    if sector == 2:
        return p, v, t
    if sector == 3:
        return p, q, v
    if sector == 4:
        return t, p, v
    return v, p, q


@njit(parallel=True, cache=True, fastmath=False)
def saturation_hsv(rgb, mult):
    """LR Saturation: S *= mult, clipped [0,1] (float32). Matches apply_saturation
    via _scale_hsv_saturation; negative-component pixels pass through."""
    n = rgb.shape[0]
    out = np.empty_like(rgb)
    m = np.float32(mult)
    z = np.float32(0.0)
    one = np.float32(1.0)
    for i in prange(n):
        r = rgb[i, 0]
        g = rgb[i, 1]
        b = rgb[i, 2]
        h, s, v, valid = _rgb2hsv(r, g, b)
        if not valid:
            out[i, 0] = r
            out[i, 1] = g
            out[i, 2] = b
            continue
        s_out = s * m
        s_out = z if s_out < z else (one if s_out > one else s_out)
        rr, gg, bb = _hsv2rgb(h, s_out, v)
        out[i, 0] = rr
        out[i, 1] = gg
        out[i, 2] = bb
    return out


@njit(parallel=True, cache=True, fastmath=False)
def vibrance_hsv(rgb, k):
    """LR Vibrance: S += k·S·(1−S), clipped [0,1] (float32). Matches apply_vibrance."""
    n = rgb.shape[0]
    out = np.empty_like(rgb)
    kk = np.float32(k)
    z = np.float32(0.0)
    one = np.float32(1.0)
    for i in prange(n):
        r = rgb[i, 0]
        g = rgb[i, 1]
        b = rgb[i, 2]
        h, s, v, valid = _rgb2hsv(r, g, b)
        if not valid:
            out[i, 0] = r
            out[i, 1] = g
            out[i, 2] = b
            continue
        s_out = s + kk * s * (one - s)
        s_out = z if s_out < z else (one if s_out > one else s_out)
        rr, gg, bb = _hsv2rgb(h, s_out, v)
        out[i, 0] = rr
        out[i, 1] = gg
        out[i, 2] = bb
    return out


@njit(parallel=True, cache=True, fastmath=False)
def hsl_bands(rgb, hue_pb, sat_pb, lum_pb, centers_lo, centers_hi, nxt_idx, lum_gate):
    """LR HSL: 8-band partition-of-unity in hexcone HSV (matches apply_hsl).

    `*_pb` length-8 float64 per-band arrays (hue shift hex / sat factor / lum
    factor); `centers_lo/hi`/`nxt_idx` the 8 segments. Band math runs **float64**
    (numpy promotes via `weights @ per_band`); luminance is saturation-gated so
    neutrals stay put. Negative-component pixels pass through."""
    n = rgb.shape[0]
    out = np.empty_like(rgb)
    for i in prange(n):
        r = rgb[i, 0]
        g = rgb[i, 1]
        b = rgb[i, 2]
        h, s, v, valid = _rgb2hsv(r, g, b)
        if not valid:
            out[i, 0] = r
            out[i, 1] = g
            out[i, 2] = b
            continue
        # segment index j: #(centers_lo <= h) - 1, clamped (h in [lo[j], hi[j]))
        j = 0
        for c in range(8):
            if h >= centers_lo[c]:
                j = c
        lo = centers_lo[j]
        hi = centers_hi[j]
        nj = nxt_idx[j]
        frac = (h - lo) / (hi - lo)
        wj = 1.0 - frac
        hue_shift = wj * hue_pb[j] + frac * hue_pb[nj]
        sat_mult = wj * sat_pb[j] + frac * sat_pb[nj]
        lum_mult = wj * lum_pb[j] + frac * lum_pb[nj]
        h_out = h + hue_shift
        if h_out < 0.0:
            h_out += 6.0
        elif h_out >= 6.0:
            h_out -= 6.0
        s_out = s * sat_mult
        s_out = 0.0 if s_out < 0.0 else (1.0 if s_out > 1.0 else s_out)
        s_gate = s / lum_gate
        s_gate = 0.0 if s_gate < 0.0 else (1.0 if s_gate > 1.0 else s_gate)
        eff_lum = 1.0 + s_gate * (lum_mult - 1.0)
        v_out = v * eff_lum
        if v_out < 0.0:
            v_out = 0.0
        rr, gg, bb = _hsv2rgb(h_out, s_out, v_out)
        out[i, 0] = rr
        out[i, 1] = gg
        out[i, 2] = bb
    return out


@njit(parallel=True, cache=True, fastmath=False)
def color_grade(rgb, tint_sh, tint_mid, tint_hi, tint_glob, lum_row,
                gamma_balance, p):
    """LR Color Grade: luminance-masked additive split-tone (matches
    apply_color_grade). Runs **float64** (numpy does `prophoto.astype(float64)`);
    `tint_*` are precomputed float64 (3,) wheel tints, `lum_row` the ProPhoto
    luminance row. Output floored at 0."""
    n = rgb.shape[0]
    out = np.empty_like(rgb)
    for i in prange(n):
        r = rgb[i, 0]
        g = rgb[i, 1]
        b = rgb[i, 2]
        lum = r * lum_row[0] + g * lum_row[1] + b * lum_row[2]
        lc = 0.0 if lum < 0.0 else (1.0 if lum > 1.0 else lum)
        perceptual = _oetf(lc)
        tt = 0.0 if perceptual < 0.0 else (1.0 if perceptual > 1.0 else perceptual)
        t = tt ** gamma_balance
        shadow_w = (1.0 - t) ** p
        highlight_w = t ** p
        midtone_w = 1.0 - shadow_w - highlight_w
        or_ = r + shadow_w * tint_sh[0] + midtone_w * tint_mid[0] + highlight_w * tint_hi[0] + tint_glob[0]
        og = g + shadow_w * tint_sh[1] + midtone_w * tint_mid[1] + highlight_w * tint_hi[1] + tint_glob[1]
        ob = b + shadow_w * tint_sh[2] + midtone_w * tint_mid[2] + highlight_w * tint_hi[2] + tint_glob[2]
        out[i, 0] = max(or_, 0.0)
        out[i, 1] = max(og, 0.0)
        out[i, 2] = max(ob, 0.0)
    return out


# ---------------------------------------------------------------------------
# RCD green + Menon directional R/B + chroma-gated refining (current core)
# ---------------------------------------------------------------------------
#
# Bit-faithful float64 port of the CURRENT `_rcd_demosaic._rcd_rggb` (RCD green +
# Menon directional R/B reconstruction + the iterated chroma-gated a-posteriori
# refining — the quality path that carries the demosaic battery's 39.03 CPSNR).
# Replaces the stale `rcd_rggb` above (a port of the pre-refining core).
#
# DESIGN — why m_dir is passed in (the parity-critical decision)
# --------------------------------------------------------------
# The whole pipeline has exactly TWO discrete branches on computed floats:
#   (1) the green H/V/diagonal pick (`grad_h` vs `grad_v`) — pure per-pixel
#       arithmetic, no convolution, so it is matched bit-for-bit by keeping the
#       reference's operand order (Pass A below, identical to `_green_directional`);
#   (2) the a-posteriori direction `M = (dd_v >= dd_h)`, which is convolution-fed.
#       Its CONTINUOUS d-planes ARE ported (the 1-D symmetric folds, `menon_dplanes`
#       above), but its discrete `>=` rides scipy's 5x5 homogeneity `convolve`, whose
#       SIMD-vectorised FP reduction is not reproducible bit-for-bit in a scalar
#       kernel — and a single flipped `M` bit selects the *other* (H vs V)
#       reconstruction, a large battery-moving divergence. So `m_dir` is computed
#       OUTSIDE this kernel (in `accel.rcd_demosaic`: numba d-planes → the shared
#       scipy `_menon_decide`) and passed in as a padded bool plane: bit-identical to
#       the numpy reference by construction, and no branch in this kernel can flip.
#       It runs once per frame (not in the refine loop).
#
# Everything ELSE this kernel computes is continuous: the R/B reconstruction and
# the refining are contractive averaging FIRs (`_KB3`, `_FIR3`) plus the
# chroma-gate box mean (`uniform_filter`, size 5). Their summation order only
# perturbs the result by ~1e-15 and that perturbation stays ~1e-15 (it never
# crosses a discrete branch), so a faithful — not necessarily scipy-bit-exact —
# tap order is sufficient. End-to-end parity vs the numpy reference is therefore
# ~1e-14 (well under the 1e-9 test tolerance), with `m_dir` and green exact.
#
# The convolutions are applied to FULL padded planes with the reference's
# boundary modes (`convolve1d mode="mirror"` == reflect-no-edge-repeat;
# `uniform_filter mode="reflect"` == reflect-WITH-edge-repeat), then the 2-px ring
# is cropped by the caller. Each reconstruction / refining sub-step re-reads the
# fully-updated plane the previous sub-step wrote (the numpy `np.where` chain has
# this exact data dependency), so the kernel is a SEQUENCE of full-plane passes,
# not one fused per-pixel loop. Each pass is embarrassingly parallel over rows.
#
# Mask note: on the padded RGGB grid (even pad → padded parity == real parity)
# the reference's `r_row/b_row/r_col/b_col` reduce to pure parity — a red row is
# every even row (`y % 2 == 0`), a blue row every odd row, etc. — so they are
# tested inline as parity rather than materialised.


@njit(cache=True, fastmath=False, inline="always")
def _mir(i, n):
    """scipy `mode='mirror'` / `np.pad(mode='reflect')` index map (reflect WITHOUT
    repeating the edge sample): -1→1, n→n-2, …"""
    if n == 1:
        return 0
    while i < 0 or i >= n:
        if i < 0:
            i = -i
        if i >= n:
            i = 2 * (n - 1) - i
    return i


@njit(cache=True, fastmath=False, inline="always")
def _refl(i, n):
    """scipy `mode='reflect'` index map (reflect WITH the edge sample repeated, as
    `uniform_filter` defaults to): -1→0, n→n-1, …"""
    if n == 1:
        return 0
    while i < 0 or i >= n:
        if i < 0:
            i = -i - 1
        if i >= n:
            i = 2 * n - 1 - i
    return i


@njit(parallel=True, cache=True, fastmath=False)
def _conv_kb3_h(a):
    """Horizontal `convolve1d(a, [0.5, 0, 0.5], mode='mirror')` (the `_KB3`
    directional fill). Zero centre tap → order-exact regardless of summation."""
    h, w = a.shape
    out = np.empty((h, w), dtype=np.float64)
    for y in prange(h):
        for x in range(w):
            out[y, x] = 0.5 * a[y, _mir(x + 1, w)] + 0.5 * a[y, _mir(x - 1, w)]
    return out


@njit(parallel=True, cache=True, fastmath=False)
def _conv_kb3_v(a):
    """Vertical `_KB3` mirror convolution."""
    h, w = a.shape
    out = np.empty((h, w), dtype=np.float64)
    for y in prange(h):
        for x in range(w):
            out[y, x] = 0.5 * a[_mir(y + 1, h), x] + 0.5 * a[_mir(y - 1, h), x]
    return out


@njit(parallel=True, cache=True, fastmath=False)
def _conv_fir3_h(a):
    """Horizontal `convolve1d(a, [1,1,1]/3, mode='mirror')` (the `_FIR3` colour-
    difference low-pass of the refining step)."""
    h, w = a.shape
    out = np.empty((h, w), dtype=np.float64)
    third = 1.0 / 3.0
    for y in prange(h):
        for x in range(w):
            out[y, x] = (a[y, _mir(x - 1, w)] + a[y, x] + a[y, _mir(x + 1, w)]) * third
    return out


@njit(parallel=True, cache=True, fastmath=False)
def _conv_fir3_v(a):
    """Vertical `_FIR3` mirror convolution."""
    h, w = a.shape
    out = np.empty((h, w), dtype=np.float64)
    third = 1.0 / 3.0
    for y in prange(h):
        for x in range(w):
            out[y, x] = (a[_mir(y - 1, h), x] + a[y, x] + a[_mir(y + 1, h), x]) * third
    return out


@njit(parallel=True, cache=True, fastmath=False)
def _uniform5(a):
    """`uniform_filter(a, size=5, mode='reflect')` — separable 5-box mean. Done as
    two passes (rows then cols), each a length-5 mean with the reflect-edge-repeat
    boundary, matching scipy's separable `uniform_filter1d` composition."""
    h, w = a.shape
    tmp = np.empty((h, w), dtype=np.float64)
    fifth = 1.0 / 5.0
    for y in prange(h):
        for x in range(w):
            tmp[y, x] = (
                a[y, _refl(x - 2, w)] + a[y, _refl(x - 1, w)] + a[y, x]
                + a[y, _refl(x + 1, w)] + a[y, _refl(x + 2, w)]
            ) * fifth
    out = np.empty((h, w), dtype=np.float64)
    for y in prange(h):
        for x in range(w):
            out[y, x] = (
                tmp[_refl(y - 2, h), x] + tmp[_refl(y - 1, h), x] + tmp[y, x]
                + tmp[_refl(y + 1, h), x] + tmp[_refl(y + 2, h), x]
            ) * fifth
    return out


# ---------------------------------------------------------------------------
# Menon a-posteriori direction — d-planes (the bit-exact front half)
# ---------------------------------------------------------------------------
#
# `_rcd_demosaic._menon_direction` splits into a CONTINUOUS front half
# (`_menon_dplanes`: the colour-difference directional gradients d_h/d_v) and ONE
# discrete branch (`_menon_decide`: the 5x5 homogeneity convolve + `dd_v >= dd_h`).
# ONLY the front half is ported here. The decision stays in scipy on both backends:
# scipy's n-D `convolve` is SIMD-FP-reduced (not a scalar fold), so no per-pixel
# summation order reproduces it bit-for-bit, and a 1-ULP flip of the `>=` selects the
# opposite H/V reconstruction (battery-moving). `menon_dplanes` is bit-identical to
# the numpy `_menon_dplanes`, so routing its output through the SHARED scipy
# `_menon_decide` yields a bit-identical `m_dir` by construction.
#
# Bit-exactness of the green hypotheses: the reference forms g_h/g_v as
# `convolve1d(f,_H0,'mirror') + convolve1d(f,_H1,'mirror')` — TWO separate symmetric
# folds, then added. scipy's symmetric-fold accumulation for an odd len-5 palindrome
# is `centre*w2 + (x[+1]+x[-1])*w3 + (x[+2]+x[-2])*w4`, so with the zero taps elided:
#   A (=_H0) = 0.5*(x[+1]+x[-1])            # w2=0, w3=0.5, w4=0
#   B (=_H1) = 0.5*x[0] + (-0.25)*(x[+2]+x[-2])   # w2=0.5, w3=0, w4=-0.25
# and g = A + B. Keeping A and B as separate sub-expressions (NOT a merged 5-tap)
# reproduces the reference's float grouping exactly (verified bit-for-bit). `_mir` is
# scipy's `mode='mirror'` index map; the d-shift's `np.pad(mode='reflect')` is the
# same map. `fastmath=False` ⇒ no FMA contraction (which would perturb the bits).


@njit(parallel=True, cache=True, fastmath=False)
def _menon_cdiff(f):
    """Colour difference under the H/V green hypotheses (c_h, c_v): `f - g` at R/B
    sites, 0 at green sites — the first stage of `_menon_dplanes`. Bit-identical to
    `np.where(rb, f - (conv1d(f,_H0)+conv1d(f,_H1)), 0.0)` for each axis."""
    h, w = f.shape
    c_h = np.empty((h, w), dtype=np.float64)
    c_v = np.empty((h, w), dtype=np.float64)
    for y in prange(h):
        for x in range(w):
            is_r = (y % 2 == 0) and (x % 2 == 0)
            is_b = (y % 2 == 1) and (x % 2 == 1)
            if not (is_r or is_b):
                c_h[y, x] = 0.0
                c_v[y, x] = 0.0
                continue
            fc = f[y, x]
            # horizontal green hypothesis g_h = A_h + B_h (scipy fold grouping)
            a_h = 0.5 * (f[y, _mir(x + 1, w)] + f[y, _mir(x - 1, w)])
            b_h = 0.5 * fc + (-0.25) * (f[y, _mir(x + 2, w)] + f[y, _mir(x - 2, w)])
            c_h[y, x] = fc - (a_h + b_h)
            # vertical green hypothesis g_v = A_v + B_v
            a_v = 0.5 * (f[_mir(y + 1, h), x] + f[_mir(y - 1, h), x])
            b_v = 0.5 * fc + (-0.25) * (f[_mir(y + 2, h), x] + f[_mir(y - 2, h), x])
            c_v[y, x] = fc - (a_v + b_v)
    return c_h, c_v


@njit(parallel=True, cache=True, fastmath=False)
def _menon_dgrad(c_h, c_v):
    """Directional colour-difference gradient |c - c(+2)| along each axis (d_h, d_v) —
    the second stage of `_menon_dplanes` (the reference's reflect-shift-and-abs, with
    the +2 shift via `_mir`). Bit-identical to the numpy reference."""
    h, w = c_h.shape
    d_h = np.empty((h, w), dtype=np.float64)
    d_v = np.empty((h, w), dtype=np.float64)
    for y in prange(h):
        for x in range(w):
            d_h[y, x] = abs(c_h[y, x] - c_h[y, _mir(x + 2, w)])
            d_v[y, x] = abs(c_v[y, x] - c_v[_mir(y + 2, h), x])
    return d_h, d_v


def menon_dplanes(f):
    """Padded float64 CFA → (d_h, d_v): the bit-exact numba twin of
    `_rcd_demosaic._menon_dplanes` (colour-difference directional gradients). The
    discrete `dd_v >= dd_h` homogeneity decision is deliberately NOT here — it stays
    in scipy (`_rcd_demosaic._menon_decide`) on both backends (see the section note
    above). Two `prange` passes (per-pixel green hypotheses → c_h/c_v, then the +2
    gradient → d_h/d_v); not `@njit` itself (just chains the jitted helpers)."""
    c_h, c_v = _menon_cdiff(f)
    return _menon_dgrad(c_h, c_v)


@njit(parallel=True, cache=True, fastmath=False)
def _rcd_green_refined(cfa):
    """Pass A — Hamilton-Adams directional green at every R/B site, known greens
    kept exactly (a 1:1 port of `_green_directional` on the padded RGGB grid).
    Returns the full padded green plane. Bit-identical to the reference (pure
    per-pixel arithmetic; the H/V/diagonal branch keeps the reference's order)."""
    h, w = cfa.shape
    green = np.empty((h, w), dtype=np.float64)
    for y in prange(h):
        for x in range(w):
            is_r = (y % 2 == 0) and (x % 2 == 0)
            is_b = (y % 2 == 1) and (x % 2 == 1)
            if not (is_r or is_b):
                green[y, x] = cfa[y, x]
                continue
            c = cfa[y, x]
            # _shift fills 0 past the edge; the reference reads the zero-padded
            # shift, so out-of-range neighbours are 0.0 here too. (On the kept
            # interior every neighbour is in range; the ring is cropped.)
            g_l = cfa[y, x + 1] if x + 1 < w else 0.0
            g_r = cfa[y, x - 1] if x - 1 >= 0 else 0.0
            g_u = cfa[y + 1, x] if y + 1 < h else 0.0
            g_d = cfa[y - 1, x] if y - 1 >= 0 else 0.0
            c_l2 = cfa[y, x + 2] if x + 2 < w else 0.0
            c_r2 = cfa[y, x - 2] if x - 2 >= 0 else 0.0
            c_u2 = cfa[y + 2, x] if y + 2 < h else 0.0
            c_d2 = cfa[y - 2, x] if y - 2 >= 0 else 0.0
            lap_h = 2.0 * c - c_l2 - c_r2
            lap_v = 2.0 * c - c_u2 - c_d2
            grad_h = abs(g_l - g_r) + abs(lap_h)
            grad_v = abs(g_u - g_d) + abs(lap_v)
            if grad_h > grad_v:
                green[y, x] = 0.5 * (g_u + g_d) + 0.25 * lap_v
            elif grad_h < grad_v:
                green[y, x] = 0.5 * (g_l + g_r) + 0.25 * lap_h
            else:
                green[y, x] = 0.25 * (g_u + g_d + g_l + g_r) + 0.125 * (lap_h + lap_v)
    return green


@njit(parallel=True, cache=True, fastmath=False)
def _apply_reconstruct_green_sites(r, b, g, ch_r, cv_r, ch_g, cv_g, ch_b, cv_b):
    """`_reconstruct_rb` lines for the GREEN sites.

    Mirrors, in order:
        r = where(g & r_row, g + ch(r) - ch(g), r)   # red row  → horiz
        r = where(g & b_row, g + cv(r) - cv(g), r)    # blue row → vert
        b = where(g & b_row, g + ch(b) - ch(g), b)
        b = where(g & r_row, g + cv(b) - cv(g), b)
    where r_row == (y even), b_row == (y odd) on the RGGB grid. `ch_*/cv_*` are the
    convolutions of the planes AS THEY WERE AT FUNCTION ENTRY — the reference
    re-reads the *updated* r between the r-lines, but the two r-updates use disjoint
    masks (green&even vs green&odd) and never overlap, so the entry-snapshot
    convolutions are correct for these masks. (Verified by the per-stage harness.)
    Mutates r, b in place at green sites."""
    h, w = r.shape
    for y in prange(h):
        for x in range(w):
            is_r = (y % 2 == 0) and (x % 2 == 0)
            is_b = (y % 2 == 1) and (x % 2 == 1)
            if is_r or is_b:
                continue
            if y % 2 == 0:                      # red row
                r[y, x] = g[y, x] + ch_r[y, x] - ch_g[y, x]
                b[y, x] = g[y, x] + cv_b[y, x] - cv_g[y, x]
            else:                               # blue row
                r[y, x] = g[y, x] + cv_r[y, x] - cv_g[y, x]
                b[y, x] = g[y, x] + ch_b[y, x] - ch_g[y, x]


@njit(parallel=True, cache=True, fastmath=False)
def _apply_reconstruct_opp_sites(r, b, m_dir, ch_r, cv_r, ch_b, cv_b):
    """`_reconstruct_rb` lines for the OPPOSITE-colour sites (R at blue, B at red),
    directional by `m_dir`:
        r = where(b_row & b_m, where(M, b+ch(r)-ch(b), b+cv(r)-cv(b)), r)
        b = where(r_row & r_m, where(M, r+ch(b)-ch(r), r+cv(b)-cv(r)), b)
    `ch_r/cv_r` are conv of r AFTER the green-site updates; `ch_b/cv_b` of b after.
    r at blue sites and b at red sites are disjoint, so order is immaterial."""
    h, w = r.shape
    for y in prange(h):
        for x in range(w):
            is_r = (y % 2 == 0) and (x % 2 == 0)
            is_b = (y % 2 == 1) and (x % 2 == 1)
            if is_b:                            # R at blue site
                if m_dir[y, x]:
                    r[y, x] = b[y, x] + ch_r[y, x] - ch_b[y, x]
                else:
                    r[y, x] = b[y, x] + cv_r[y, x] - cv_b[y, x]
            elif is_r:                          # B at red site
                if m_dir[y, x]:
                    b[y, x] = r[y, x] + ch_b[y, x] - ch_r[y, x]
                else:
                    b[y, x] = r[y, x] + cv_b[y, x] - cv_r[y, x]


@njit(parallel=True, cache=True, fastmath=False)
def _apply_refine_green(r, g, b, m_dir, alpha, ch_bg, cv_bg, ch_rg, cv_rg):
    """`_refine_once` chroma-gated green update:
        b_g_m = where(b_m, where(M, ch(b_g), cv(b_g)), 0)
        r_g_m = where(r_m, where(M, ch(r_g), cv(r_g)), 0)
        g = where(r_m, (1-α)·g + α·(r - r_g_m), g)
        g = where(b_m, (1-α)·g + α·(b - b_g_m), g)
    `ch_rg/cv_rg` are conv of (r-g); `ch_bg/cv_bg` of (b-g), computed on entry."""
    h, w = g.shape
    for y in prange(h):
        for x in range(w):
            is_r = (y % 2 == 0) and (x % 2 == 0)
            is_b = (y % 2 == 1) and (x % 2 == 1)
            a = alpha[y, x]
            if is_r:
                rgm = ch_rg[y, x] if m_dir[y, x] else cv_rg[y, x]
                g[y, x] = (1.0 - a) * g[y, x] + a * (r[y, x] - rgm)
            elif is_b:
                bgm = ch_bg[y, x] if m_dir[y, x] else cv_bg[y, x]
                g[y, x] = (1.0 - a) * g[y, x] + a * (b[y, x] - bgm)


@njit(parallel=True, cache=True, fastmath=False)
def _apply_refine_rb_green_sites(r, b, g, cvk_rg, chk_rg, cvk_bg, chk_bg):
    """`_refine_once` R/B at the GREEN sites (directional by row/column geometry):
        r = where(g & b_row, g + cvk(r_g), r);  r = where(g & b_col, g + chk(r_g), r)
        b = where(g & r_row, g + cvk(b_g), b);  b = where(g & r_col, g + chk(b_g), b)
    b_row==(y odd), b_col==(x odd), r_row==(y even), r_col==(x even). At a green
    site (one of y/x even, the other odd) exactly one of the row/col tests fires
    per channel, so the two sequential `np.where`s reduce to one assignment each.
    `*_rg`=conv of (r-g), `*_bg`=conv of (b-g), both on the POST-green-update r-g/
    b-g (the reference recomputes r_g/b_g right before this block)."""
    h, w = g.shape
    for y in prange(h):
        for x in range(w):
            is_r = (y % 2 == 0) and (x % 2 == 0)
            is_b = (y % 2 == 1) and (x % 2 == 1)
            if is_r or is_b:
                continue
            # green site: y, x parities differ → exactly one row + one col test hits.
            if y % 2 == 1:                      # b_row → R vertical KB3
                r[y, x] = g[y, x] + cvk_rg[y, x]
            if x % 2 == 1:                      # b_col → R horizontal KB3
                r[y, x] = g[y, x] + chk_rg[y, x]
            if y % 2 == 0:                      # r_row → B vertical KB3
                b[y, x] = g[y, x] + cvk_bg[y, x]
            if x % 2 == 0:                      # r_col → B horizontal KB3
                b[y, x] = g[y, x] + chk_bg[y, x]


@njit(parallel=True, cache=True, fastmath=False)
def _apply_refine_opp_sites(r, b, m_dir, ch_rb, cv_rb):
    """`_refine_once` R at blue / B at red (directional by M), the final block:
        r_b = r - b
        r = where(b_m, b + (M? ch(r_b):cv(r_b)), r)
        b = where(r_m, r - (M? ch(r_b):cv(r_b)), b)
    Both read the SAME `r_b = r - b` snapshot (conv computed once on entry). r at
    blue and b at red are disjoint; the `b` update reads the *updated* r — but only
    at red sites, where r is NOT touched by this block (r changes at blue sites),
    so the entry r equals the updated r there. Hence one pass suffices."""
    h, w = r.shape
    for y in prange(h):
        for x in range(w):
            is_r = (y % 2 == 0) and (x % 2 == 0)
            is_b = (y % 2 == 1) and (x % 2 == 1)
            if is_b:
                r[y, x] = b[y, x] + (ch_rb[y, x] if m_dir[y, x] else cv_rb[y, x])
            elif is_r:
                b[y, x] = r[y, x] - (ch_rb[y, x] if m_dir[y, x] else cv_rb[y, x])


@njit(cache=True, fastmath=False)
def _sub(a, b):
    """Element-wise a - b into a fresh float64 plane (njit helper for the staged
    colour-difference inputs)."""
    h, w = a.shape
    out = np.empty((h, w), dtype=np.float64)
    for y in range(h):
        for x in range(w):
            out[y, x] = a[y, x] - b[y, x]
    return out


@njit(cache=True, fastmath=False)
def _abs_chroma(r, g, b):
    """|r-g| + |b-g| into a fresh plane (the chroma-gate magnitude input)."""
    h, w = r.shape
    out = np.empty((h, w), dtype=np.float64)
    for y in range(h):
        for x in range(w):
            out[y, x] = abs(r[y, x] - g[y, x]) + abs(b[y, x] - g[y, x])
    return out


@njit(parallel=True, cache=True, fastmath=False)
def _assemble_rcd_refined(cfa, r, g, b):
    """Restore the exact CFA sample at its own site (red at red, green at green,
    blue at blue — the reference's post-loop `np.where(*_site, f, …)`) and stack
    into a padded (H, W, 3) plane. R/B at their own sites and G at green sites are
    pinned to `cfa`; everything else keeps the reconstructed value."""
    h, w = cfa.shape
    out = np.empty((h, w, 3), dtype=np.float64)
    for y in prange(h):
        for x in range(w):
            is_r = (y % 2 == 0) and (x % 2 == 0)
            is_b = (y % 2 == 1) and (x % 2 == 1)
            is_g = not (is_r or is_b)
            out[y, x, 0] = cfa[y, x] if is_r else r[y, x]
            out[y, x, 1] = cfa[y, x] if is_g else g[y, x]
            out[y, x, 2] = cfa[y, x] if is_b else b[y, x]
    return out


def rcd_rggb_refined(cfa, m_dir, refine_iters, chroma_thr, chroma_soft):
    """Padded float64 RGGB CFA + padded bool `m_dir` → padded (H, W, 3) RGB.

    The bit-faithful numba twin of `_rcd_demosaic._rcd_rggb`. `m_dir` is the
    a-posteriori direction plane from the numpy reference `_menon_direction` (so
    the only convolution-fed branch is exact by construction); `refine_iters`,
    `chroma_thr`, `chroma_soft` are the reference tunables (`_REFINE_ITERS`,
    `_CHROMA_THR`, `_CHROMA_SOFT`). Orchestrates the staged passes; each conv /
    apply helper is itself `prange`-parallel. Not `@njit` itself (it allocates the
    `np.where`/`np.clip` masks in numpy — cheap, once-per-frame); the per-pixel
    work is all in the jitted helpers it calls."""
    h, w = cfa.shape
    yy, xx = np.indices((h, w))
    r_site = (yy % 2 == 0) & (xx % 2 == 0)
    b_site = (yy % 2 == 1) & (xx % 2 == 1)

    # --- Pass A: directional green (exact) ---
    green = _rcd_green_refined(cfa)

    # --- _reconstruct_rb ---
    r = np.where(r_site, cfa, 0.0)
    b = np.where(b_site, cfa, 0.0)
    # green-site updates read entry-snapshot convolutions of r, b, g.
    ch_r = _conv_kb3_h(r)
    cv_r = _conv_kb3_v(r)
    ch_g = _conv_kb3_h(green)
    cv_g = _conv_kb3_v(green)
    ch_b = _conv_kb3_h(b)
    cv_b = _conv_kb3_v(b)
    _apply_reconstruct_green_sites(r, b, green, ch_r, cv_r, ch_g, cv_g, ch_b, cv_b)
    # opposite-colour sites read convolutions of the post-green-update r, b.
    ch_r = _conv_kb3_h(r)
    cv_r = _conv_kb3_v(r)
    ch_b = _conv_kb3_h(b)
    cv_b = _conv_kb3_v(b)
    _apply_reconstruct_opp_sites(r, b, m_dir, ch_r, cv_r, ch_b, cv_b)

    # --- chroma-gated refining, `refine_iters` times ---
    for _ in range(int(refine_iters)):
        # chroma gate α (uniform_filter box mean of |r-g|+|b-g|).
        chroma = _uniform5(_abs_chroma(r, green, b))
        alpha = np.clip((chroma - chroma_thr) / chroma_soft, 0.0, 1.0)
        # green update: convolutions of (r-g) and (b-g) on entry.
        r_g = _sub(r, green)
        b_g = _sub(b, green)
        ch_rg = _conv_fir3_h(r_g)
        cv_rg = _conv_fir3_v(r_g)
        ch_bg = _conv_fir3_h(b_g)
        cv_bg = _conv_fir3_v(b_g)
        _apply_refine_green(r, green, b, m_dir, alpha, ch_bg, cv_bg, ch_rg, cv_rg)
        # R/B at green sites: recompute (r-g)/(b-g) AFTER the green update, KB3.
        r_g = _sub(r, green)
        b_g = _sub(b, green)
        cvk_rg = _conv_kb3_v(r_g)
        chk_rg = _conv_kb3_h(r_g)
        cvk_bg = _conv_kb3_v(b_g)
        chk_bg = _conv_kb3_h(b_g)
        _apply_refine_rb_green_sites(r, b, green, cvk_rg, chk_rg, cvk_bg, chk_bg)
        # R at blue / B at red: FIR3 of (r-b) snapshot.
        r_b = _sub(r, b)
        ch_rb = _conv_fir3_h(r_b)
        cv_rb = _conv_fir3_v(r_b)
        _apply_refine_opp_sites(r, b, m_dir, ch_rb, cv_rb)

    # --- restore exact known CFA samples, assemble RGB ---
    return _assemble_rcd_refined(cfa, r, green, b)
