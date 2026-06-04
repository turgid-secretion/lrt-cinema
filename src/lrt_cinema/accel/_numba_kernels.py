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
# RCD-family directional color-difference demosaic (Stage 1 core)
# ---------------------------------------------------------------------------
#
# Fused float64 port of `_rcd_demosaic._rcd_rggb` (the RGGB core; the cheap
# validate/flip/pad/crop/unflip/clip wrapper stays in numpy — see
# `accel.rcd_demosaic`). The reference materialises ~30 full-frame float64
# `_shift`/`np.where` temporaries; this keeps each pixel's scratch in registers
# and writes the padded (H, W, 3) result once.
#
# PRECISION: float64 (the reference does `cfa.astype(np.float64)`); no fastmath
# (reassociation would change the masked-average reduction order). The reference
# is a *comparison* + *subtraction* method (no divide on pixel data), so it is
# structurally finite — preserved here.
#
# The color-difference fill has a data dependency (`_bilinear_fill_diff` pass 2
# reads pass 1's output), so it is split into sequential `prange` passes:
#   A — directional green at every pixel (Hamilton-Adams), kept at green sites.
#   B — color-difference K_R/K_B at the *opposite-color* sites (K_R at blue,
#       K_B at red) from the 4 *diagonal* same-difference neighbours.
#   C — K_R/K_B at the *green* sites from the 4 *cardinal* neighbours (all known
#       after B). Then R = G + K_R, B = G + K_B, with known CFA samples exact.
# Each pass writes a full padded buffer that the next reads, so the per-pass
# `prange` over pixels is embarrassingly parallel with no cross-pixel races.
#
# Masked-average semantics (`diag_cnt`/`card_cnt` in the reference) are matched
# exactly: a neighbour contributes only when it carries a real difference, and
# the average divides by the *count* of contributors. On the interior (the only
# region that survives the 2-px crop) every interior site has the full 4
# neighbours, so this is a plain /4; the count logic only differs on the outer
# pad ring, which is cropped away. Replicated anyway for an exact match.


@njit(cache=True, fastmath=False, inline="always")
def _diag_diff_avg(diff, filled, y, x, h, w):
    """Masked average of a difference plane over the 4 *diagonal* neighbours of
    (y, x) that carry a real value (`filled`). Returns (have, value): `have` is
    True iff ≥1 diagonal neighbour was known (the reference's `diag_cnt > 0`)."""
    s = 0.0
    cnt = 0.0
    if y - 1 >= 0 and x - 1 >= 0 and filled[y - 1, x - 1]:
        s += diff[y - 1, x - 1]
        cnt += 1.0
    if y - 1 >= 0 and x + 1 < w and filled[y - 1, x + 1]:
        s += diff[y - 1, x + 1]
        cnt += 1.0
    if y + 1 < h and x - 1 >= 0 and filled[y + 1, x - 1]:
        s += diff[y + 1, x - 1]
        cnt += 1.0
    if y + 1 < h and x + 1 < w and filled[y + 1, x + 1]:
        s += diff[y + 1, x + 1]
        cnt += 1.0
    if cnt > 0.0:
        return True, s / cnt
    return False, 0.0


@njit(cache=True, fastmath=False, inline="always")
def _card_diff_avg(diff, filled, y, x, h, w):
    """Masked average of a difference plane over the 4 *cardinal* neighbours of
    (y, x) that carry a real value (`filled`). Returns (have, value)."""
    s = 0.0
    cnt = 0.0
    if x + 1 < w and filled[y, x + 1]:
        s += diff[y, x + 1]
        cnt += 1.0
    if x - 1 >= 0 and filled[y, x - 1]:
        s += diff[y, x - 1]
        cnt += 1.0
    if y + 1 < h and filled[y + 1, x]:
        s += diff[y + 1, x]
        cnt += 1.0
    if y - 1 >= 0 and filled[y - 1, x]:
        s += diff[y - 1, x]
        cnt += 1.0
    if cnt > 0.0:
        return True, s / cnt
    return False, 0.0


@njit(parallel=True, cache=True, fastmath=False)
def rcd_rggb(cfa):
    """Fused RGGB-phase RCD core: padded float64 CFA (H, W) → padded (H, W, 3).

    1:1 port of `_rcd_demosaic._rcd_rggb`. `cfa` is a reflect-padded 2-D mosaic
    whose interior top-left pixel (``[2, 2]``) is RED (RGGB phase). Returns the
    padded float64 RGB; the caller crops the 2-px ring and unflips. Same maths,
    same operation order, float64, no `fastmath`."""
    h, w = cfa.shape
    green = np.empty((h, w), dtype=np.float64)
    kr = np.zeros((h, w), dtype=np.float64)   # K_R = R - G plane (built in A→C)
    kb = np.zeros((h, w), dtype=np.float64)   # K_B = B - G plane
    # `filled_*` track which sites carry a real difference at each stage — the
    # reference's `known` (pass 1) then `filled = known | diag_known` (pass 2)
    # masks. Tracking them makes the masked averages an EXACT match even on the
    # outer pad ring (cropped away), not just the interior.
    f_kr = np.zeros((h, w), dtype=np.bool_)   # K_R known: red sites, then + blue
    f_kb = np.zeros((h, w), dtype=np.bool_)   # K_B known: blue sites, then + red
    out = np.empty((h, w, 3), dtype=np.float64)

    # --- Pass A: Hamilton-Adams directional green at every site ---------------
    # On the RGGB padded grid (pad is even, so padded parity == original parity):
    #   R at (even, even); B at (odd, odd); G at (even, odd) and (odd, even).
    # Green neighbours are cardinal ±1; same-channel neighbours ±2. The reference
    # reads zero-filled shifts past the true edge; the bounds guards below do the
    # same (out-of-range → 0.0), so the cropped interior is identical. K_R/K_B at
    # the known R/B sites (= f − green) are recorded here so pass B can read them.
    for y in prange(h):
        for x in range(w):
            is_r = (y % 2 == 0) and (x % 2 == 0)
            is_b = (y % 2 == 1) and (x % 2 == 1)
            if not (is_r or is_b):
                green[y, x] = cfa[y, x]          # known green: keep exactly
                continue
            c = cfa[y, x]
            # green neighbours (cardinal ±1), zero past the array edge
            g_l = cfa[y, x + 1] if x + 1 < w else 0.0   # col +1 (ref _shift(f,0,-1))
            g_r = cfa[y, x - 1] if x - 1 >= 0 else 0.0   # col -1
            g_u = cfa[y + 1, x] if y + 1 < h else 0.0    # row +1
            g_d = cfa[y - 1, x] if y - 1 >= 0 else 0.0   # row -1
            # same-channel neighbours (±2)
            c_l2 = cfa[y, x + 2] if x + 2 < w else 0.0
            c_r2 = cfa[y, x - 2] if x - 2 >= 0 else 0.0
            c_u2 = cfa[y + 2, x] if y + 2 < h else 0.0
            c_d2 = cfa[y - 2, x] if y - 2 >= 0 else 0.0

            lap_h = 2.0 * c - c_l2 - c_r2
            lap_v = 2.0 * c - c_u2 - c_d2
            grad_h = abs(g_l - g_r) + abs(lap_h)
            grad_v = abs(g_u - g_d) + abs(lap_v)

            if grad_h > grad_v:
                g_est = 0.5 * (g_u + g_d) + 0.25 * lap_v        # interp vertical
            elif grad_h < grad_v:
                g_est = 0.5 * (g_l + g_r) + 0.25 * lap_h        # interp horizontal
            else:
                g_est = 0.25 * (g_u + g_d + g_l + g_r) + 0.125 * (lap_h + lap_v)
            green[y, x] = g_est
            if is_r:
                kr[y, x] = c - g_est            # K_R real at red site
                f_kr[y, x] = True
            else:
                kb[y, x] = c - g_est            # K_B real at blue site
                f_kb[y, x] = True

    # --- Pass B: diagonal fill of BOTH difference planes ----------------------
    # `_bilinear_fill_diff` pass 1, transcribed literally and independently for
    # K_R and K_B: at every site NOT already known, average the (up to 4) diagonal
    # neighbours that ARE known; mark it filled where any existed. K_R becomes
    # known at blue sites (diagonal red neighbours), K_B at red sites — and, on the
    # outer pad ring, wherever a diagonal neighbour happens to be in range. Reads
    # only pass-A `f_*`, writes only the freshly-diag-known sites, so the two
    # planes never read each other → race-free under `prange`.
    for y in prange(h):
        for x in range(w):
            if not f_kr[y, x]:
                have, val = _diag_diff_avg(kr, f_kr, y, x, h, w)
                if have:
                    kr[y, x] = val
                    f_kr[y, x] = True
            if not f_kb[y, x]:
                have, val = _diag_diff_avg(kb, f_kb, y, x, h, w)
                if have:
                    kb[y, x] = val
                    f_kb[y, x] = True

    # --- Pass C: cardinal fill of the remaining (green) sites -----------------
    # `_bilinear_fill_diff` pass 2: at every site STILL not filled after B (the
    # green sites; on the pad ring, any straggler), average the cardinal neighbours
    # that are filled. After this every site carries both differences. Reads the
    # post-B `f_*`; the `_was` snapshot makes the "filled after B" test independent
    # of this pass's own writes (so a freshly cardinal-filled site is NOT treated
    # as a source for its neighbour in the same pass — matching the reference,
    # which computes `card_known` from the pre-pass-2 `filled` mask).
    f_kr_was = f_kr.copy()
    f_kb_was = f_kb.copy()
    for y in prange(h):
        for x in range(w):
            if not f_kr_was[y, x]:
                have, val = _card_diff_avg(kr, f_kr_was, y, x, h, w)
                if have:
                    kr[y, x] = val
                    f_kr[y, x] = True
            if not f_kb_was[y, x]:
                have, val = _card_diff_avg(kb, f_kb_was, y, x, h, w)
                if have:
                    kb[y, x] = val
                    f_kb[y, x] = True

    # --- Pass D: assemble RGB (known CFA samples exact) -----------------------
    # red = green + K_R, blue = green + K_B, then restore the exact CFA sample at
    # its own site (reference `np.where(r_site, f, red)` / `np.where(b_site, f,
    # blue)`). Green is already exact at green sites (pass A) / the estimate
    # elsewhere.
    for y in prange(h):
        for x in range(w):
            is_r = (y % 2 == 0) and (x % 2 == 0)
            is_b = (y % 2 == 1) and (x % 2 == 1)
            g = green[y, x]
            r = cfa[y, x] if is_r else g + kr[y, x]
            b = cfa[y, x] if is_b else g + kb[y, x]
            out[y, x, 0] = r
            out[y, x, 1] = g
            out[y, x, 2] = b
    return out
