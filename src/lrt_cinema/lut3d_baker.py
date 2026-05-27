"""DCP HueSatMap / LookTable application primitives.

Pure-numpy helpers reused by `lrt_cinema.pipeline` at HSM (stage 5) and
LookTable (stage 8). Apply cubes in HSV space, with Adobe's hexcone
variant (hue in `[0, 6)`, not degrees) and the sRGB-gamma round-trip on V
when the cube carries `srgb_gamma=True`.

Verified against the RawTherapee `rtengine/dcp.cc` reference port (GPL,
used as algorithmic reference only — no code copied) and Adobe DNG SDK
1.7.1 `RefBaselineHueSatMap`.
"""

from __future__ import annotations

import numpy as np

from lrt_cinema.dcp import HsvCube

# ---------------------------------------------------------------------------
# Standard sRGB transfer functions (used when cube.srgb_gamma is True)
# ---------------------------------------------------------------------------

def _srgb_oetf(x: np.ndarray) -> np.ndarray:
    """Linear → perceptual (encoded). IEC 61966-2-1 piecewise."""
    return np.where(
        x <= 0.0031308,
        x * 12.92,
        1.055 * np.power(np.maximum(x, 0.0), 1.0 / 2.4) - 0.055,
    )


def _srgb_eotf(x: np.ndarray) -> np.ndarray:
    """Perceptual (encoded) → linear. IEC 61966-2-1 piecewise inverse."""
    return np.where(
        x <= 0.04045,
        x / 12.92,
        np.power(np.maximum((x + 0.055) / 1.055, 0.0), 2.4),
    )


# ---------------------------------------------------------------------------
# Vectorized RGB ↔ Adobe-DCP hexcone HSV
# ---------------------------------------------------------------------------
#
# Adobe's HueSatMap / LookTable use a hexcone HSV variant with hue in
# `[0, 6)` (sixths of a turn), NOT degrees. From RT color.h#L393-L513.

def _rgb_to_hsv_dcp(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Vectorized linear-ProPhoto RGB → (h, s, v, valid_mask).

    `rgb` shape: (..., 3). Returns four arrays of the leading shape.
    `valid_mask`: True for pixels with all-nonnegative RGB. Negative-component
    pixels (out-of-gamut samples from working-space → ProPhoto conversion at
    cube bake time) fall back to matrix-only passthrough per RT's
    `apply()` at dcp.cc#L1492-L1509.
    """
    r = rgb[..., 0]
    g = rgb[..., 1]
    b = rgb[..., 2]
    var_min = np.minimum(np.minimum(r, g), b)
    var_max = np.maximum(np.maximum(r, g), b)
    delta = var_max - var_min

    valid_mask = var_min >= 0.0
    v = var_max

    safe_max = np.where(var_max > 0.0, var_max, 1.0)
    s = np.where(var_max > 0.0, delta / safe_max, 0.0)

    # Hue: 6-segment hexcone. Handle delta=0 by setting h=0.
    safe_delta = np.where(np.abs(delta) > 1e-10, delta, 1.0)
    h_r = (g - b) / safe_delta
    h_g = 2.0 + (b - r) / safe_delta
    h_b = 4.0 + (r - g) / safe_delta

    # Pick the right sector per pixel based on which channel == max.
    h = np.where(r == var_max, h_r, np.where(g == var_max, h_g, h_b))
    h = np.where(np.abs(delta) > 1e-10, h, 0.0)

    # Wrap to [0, 6).
    h = np.where(h < 0.0, h + 6.0, h)
    h = np.where(h >= 6.0, h - 6.0, h)

    return h, s, v, valid_mask


def _hsv_to_rgb_dcp(h: np.ndarray, s: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Inverse: hexcone HSV → linear-ProPhoto RGB. h MUST be in [0, 6)."""
    # Wrap h into [0, 6) before sectoring.
    h_wrapped = np.where(h < 0.0, h + 6.0, h)
    h_wrapped = np.where(h_wrapped >= 6.0, h_wrapped - 6.0, h_wrapped)

    sector = np.floor(h_wrapped).astype(np.int32)
    f = h_wrapped - sector
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)

    # Per-sector RGB tuple — assemble via stack-and-pick.
    rgb = np.zeros(h.shape + (3,), dtype=h.dtype)
    sector_clamped = np.clip(sector, 0, 5)

    # Vectorized: build a stack of 6 candidate RGB outputs, gather by sector.
    cand = np.stack([
        np.stack([v, t, p], axis=-1),   # sector 0
        np.stack([q, v, p], axis=-1),   # sector 1
        np.stack([p, v, t], axis=-1),   # sector 2
        np.stack([p, q, v], axis=-1),   # sector 3
        np.stack([t, p, v], axis=-1),   # sector 4
        np.stack([v, p, q], axis=-1),   # sector 5
    ], axis=0)
    # Gather: cand has shape (6, ..., 3); for each pixel index by sector.
    idx = sector_clamped[None, ..., None]
    rgb = np.take_along_axis(cand, np.broadcast_to(idx, (1,) + h.shape + (3,)), axis=0)[0]
    return rgb


# ---------------------------------------------------------------------------
# HSV cube application (matches RT dcp.cc hsdApply, vectorized)
# ---------------------------------------------------------------------------

def _apply_hsv_cube(
    h: np.ndarray,
    s: np.ndarray,
    v: np.ndarray,
    cube: np.ndarray,
    cube_meta: HsvCube,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Trilinear-sample an HSV cube at every (h, s, v) and apply.

    `cube` shape: (V, H, S, 3) — already mired-blended if needed.
    `cube_meta` carries dim counts and `srgb_gamma`. Output (h, s, v)
    arrays are the post-cube values.

    Matches RawTherapee `dcp.cc::hsdApply` at L2013-L2133 — trilinear
    over hue×sat×val with hue WRAPPING (hue=max → wraps to hue=0) and
    sat/val CLAMPING.
    """
    h_div, s_div, v_div = cube_meta.hue_divisions, cube_meta.sat_divisions, cube_meta.val_divisions

    h_scale = h_div / 6.0 if h_div >= 2 else 0.0
    s_scale = float(s_div - 1)
    v_scale = float(v_div - 1)
    max_h_index0 = h_div - 1
    max_s_index0 = s_div - 2 if s_div >= 2 else 0
    max_v_index0 = v_div - 2 if v_div >= 2 else 0

    h_scaled = h * h_scale
    s_scaled = s * s_scale
    v_encoded = _srgb_oetf(np.clip(v, 0.0, None)) if cube_meta.srgb_gamma else v
    v_scaled = v_encoded * v_scale

    h_index0 = np.floor(h_scaled).astype(np.int32)
    s_index0 = np.clip(np.floor(s_scaled).astype(np.int32), 0, max_s_index0)
    v_index0 = np.clip(np.floor(v_scaled).astype(np.int32), 0, max_v_index0)

    # Hue wraps; sat/val clamp via the index1 = index0 + 1 with
    # index1 wrap-to-0 at the hue ceiling.
    h_index0 = np.clip(h_index0, 0, max_h_index0)
    h_index1 = np.where(h_index0 >= max_h_index0, 0, h_index0 + 1)
    s_index1 = np.minimum(s_index0 + 1, s_div - 1)
    v_index1 = np.minimum(v_index0 + 1, v_div - 1)

    h_f1 = np.clip(h_scaled - h_index0, 0.0, 1.0)
    s_f1 = np.clip(s_scaled - s_index0, 0.0, 1.0)
    v_f1 = np.clip(v_scaled - v_index0, 0.0, 1.0)
    h_f0 = 1.0 - h_f1
    s_f0 = 1.0 - s_f1
    v_f0 = 1.0 - v_f1

    # Gather the 8 trilinear cell corners. Cube is (V, H, S, 3) — index via
    # advanced indexing along the spatial axes; the trailing 3-tuple comes
    # along automatically.
    c000 = cube[v_index0, h_index0, s_index0]
    c001 = cube[v_index0, h_index0, s_index1]
    c010 = cube[v_index0, h_index1, s_index0]
    c011 = cube[v_index0, h_index1, s_index1]
    c100 = cube[v_index1, h_index0, s_index0]
    c101 = cube[v_index1, h_index0, s_index1]
    c110 = cube[v_index1, h_index1, s_index0]
    c111 = cube[v_index1, h_index1, s_index1]

    # Trilinear in V (outer), H (middle), S (inner).
    w000 = (v_f0 * h_f0 * s_f0)[..., None]
    w001 = (v_f0 * h_f0 * s_f1)[..., None]
    w010 = (v_f0 * h_f1 * s_f0)[..., None]
    w011 = (v_f0 * h_f1 * s_f1)[..., None]
    w100 = (v_f1 * h_f0 * s_f0)[..., None]
    w101 = (v_f1 * h_f0 * s_f1)[..., None]
    w110 = (v_f1 * h_f1 * s_f0)[..., None]
    w111 = (v_f1 * h_f1 * s_f1)[..., None]

    sampled = (
        w000 * c000 + w001 * c001 + w010 * c010 + w011 * c011
        + w100 * c100 + w101 * c101 + w110 * c110 + w111 * c111
    )

    hue_shift_deg = sampled[..., 0]
    sat_scale_arr = sampled[..., 1]
    val_scale_arr = sampled[..., 2]

    h_out = h + hue_shift_deg * (6.0 / 360.0)
    # Re-wrap hue into [0, 6).
    h_out = np.where(h_out < 0.0, h_out + 6.0, h_out)
    h_out = np.where(h_out >= 6.0, h_out - 6.0, h_out)
    s_out = np.clip(s * sat_scale_arr, 0.0, 1.0)

    if cube_meta.srgb_gamma:
        # Adobe SDK Pin_real32: clamp encoded V to [0, 1] before EOTF decode.
        # (RefBaselineHueSatMap in dng_reference.cpp.)
        v_encoded_out = np.clip(v_encoded * val_scale_arr, 0.0, 1.0)
        v_out = _srgb_eotf(v_encoded_out)
    else:
        v_out = v * val_scale_arr

    return h_out, s_out, v_out


