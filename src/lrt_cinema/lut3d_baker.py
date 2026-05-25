"""Bake DCP HueSatMap + LookTable cubes into a Resolve-format .cube LUT.

darktable's `lut3d` module reads Resolve `.cube` files via an on-disk
`filepath` in the params struct (per `src/iop/lut3d.c#L1178-L1196` SHA
9402c65275). The path is resolved against a `def_path` config key. We
emit the cube next to the per-frame XMP sidecar and point dt at that
directory via `--conf plugins/darkroom/lut3d/def_path=...`.

Algorithm summary (verified against RawTherapee `rtengine/dcp.cc`:
clean-room HSM/LookTable reference port, GPL — used here strictly as the
reference for matching Adobe's behavior, no code copied):

  1. For each sample point on the Resolve cube's input grid (R, G, B
     ∈ [0, 1]), interpret the triple as **linear ProPhoto RGB**. dt's
     `lut3d` module converts working-space ↔ ProPhoto automatically at
     pipeline entry/exit (verified at `src/iop/lut3d.c#L1085-L1110`), so
     the cube's data lives in ProPhoto throughout.
  2. Decompose to HSV via Adobe's hexcone variant (hue in `[0, 6)`, not
     degrees). For pixels with any negative ProPhoto component, fall
     back to the matrix-only passthrough (no HSM enrichment) — RT's
     `apply()` at `dcp.cc#L1492-L1509`.
  3. (HSM if present) trilinear-sample the HSM cube → (hueShift_deg,
     satScale, valScale). Apply: `h += hueShift_deg × 6/360`, `s ×=
     satScale`, `v ×= valScale` (with sRGB OETF/EOTF round-trip on V
     when `srgb_gamma=True`).
  4. (BaselineExposureOffset) scalar `v *= 2^offset` between HSM and
     LookTable, matching Adobe's pipeline position.
  5. (LookTable if present) same trilinear-sample-and-apply as step 3.
  6. Recompose RGB from (h, s, v). Clamp to [0, 1] (dt's `_calculate_clut_cube`
     warns but accepts out-of-range; pre-clamping is more predictable).
  7. Emit Resolve `.cube` with R-fastest ordering (matches dt's
     `_calculate_clut` indexing at `src/iop/lut3d.c#L263`: index = R + N×G + N²×B).

Cube size default 33: the Adobe/Resolve standard, sufficient for
visually-lossless representation of a 90×16×16 source HSV cube; 48 or 64
available as headroom if saturated-gamut artifacts surface in practice
(per spec Section 9.4).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from lrt_cinema.dcp import HsvCube

# Recommended Resolve cube size for HSV-cube baking. 33 is the Adobe /
# Resolve / OCIO standard; 48 or 64 give more headroom against saturated-
# gamut interpolation artifacts at the cost of larger cube files and a
# slower dt load. dt's max is 256 (src/iop/lut3d.c#L813); going above 64
# is rarely useful for HSV-derived cubes — the source HSV cube's own
# density (max ~32K cells) bottlenecks meaningful detail recovery.
RECOMMENDED_CUBE_SIZE = 33


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
        v_encoded_out = v_encoded * val_scale_arr
        v_out = _srgb_eotf(np.clip(v_encoded_out, 0.0, None))
    else:
        v_out = v * val_scale_arr

    return h_out, s_out, v_out


# ---------------------------------------------------------------------------
# Main entry point — bake one (or both) DCP HSV cubes into a Resolve .cube
# ---------------------------------------------------------------------------

def bake_dcp_cubes_to_resolve_cube(
    out_path: Path,
    cube_size: int,
    hsm_blended: np.ndarray | None,
    hsm_meta: HsvCube | None,
    look_blended: np.ndarray | None,
    look_meta: HsvCube | None,
    baseline_exposure_offset_ev: float = 0.0,
) -> None:
    """Write a Resolve-format `.cube` file approximating the DCP HSV cubes.

    Pipeline-order semantics mirror Adobe's:
      1. HSM applied (if present)
      2. BaselineExposureOffset scalar lift on V
      3. LookTable applied (if present)

    Steps 2 and 3 fire even when step 1's HSM is None — the BaselineExposureOffset
    must still apply BEFORE the LookTable per spec § "Camera Profile
    Encoding." Caller is responsible for blending HSM Data1/Data2 by target
    kelvin via `dcp.interpolate_hsv_cube` BEFORE passing in `hsm_blended`;
    LookTable has no per-illuminant blend.

    Both cubes (when both present) must have shape `(V, H, S, 3)`. cube_meta
    arguments carry the dim counts + srgb_gamma flag the application
    algorithm needs.

    The output Resolve cube is ProPhoto-in / ProPhoto-out — dt's `lut3d`
    module handles the working-space ↔ ProPhoto conversion at module
    entry/exit (verified at src/iop/lut3d.c#L1085-L1110). The cube
    `colorspace` field in the emitted dt params must be
    `DT_IOP_LIN_PROPHOTO = 5`.
    """
    if hsm_blended is None and look_blended is None:
        raise ValueError("at least one of hsm_blended / look_blended must be supplied")

    n = int(cube_size)
    if n < 2 or n > 256:
        raise ValueError(f"cube_size {n} outside [2, 256] (dt's max is 256)")

    # Resolve-cube sample grid in linear ProPhoto. Iteration order matches
    # dt's `_calculate_clut_cube` indexing (R-fastest): index = R + N*G + N²*B
    # at src/iop/lut3d.c#L263. We compute all N³ samples in a single numpy
    # operation, then iterate the write in (B, G, R) order.
    axis = np.linspace(0.0, 1.0, n, dtype=np.float64)
    # meshgrid with indexing='ij' so the leading axis is R.
    R, G, B = np.meshgrid(axis, axis, axis, indexing="ij")
    rgb_in = np.stack([R, G, B], axis=-1)  # shape (N, N, N, 3)

    # 1. RGB → HSV (Adobe hexcone variant). valid_mask is False for any
    #    negative-component samples — those bypass the cube per RT's
    #    apply() contract.
    h, s, v, valid_mask = _rgb_to_hsv_dcp(rgb_in)

    # 2. HSM (if present).
    if hsm_blended is not None:
        h, s, v = _apply_hsv_cube(h, s, v, hsm_blended, hsm_meta)

    # 3. BaselineExposureOffset between cubes (Adobe pipeline position).
    if baseline_exposure_offset_ev != 0.0:
        scale = float(2.0 ** baseline_exposure_offset_ev)
        v = v * scale

    # 4. LookTable (if present).
    if look_blended is not None:
        h, s, v = _apply_hsv_cube(h, s, v, look_blended, look_meta)

    # 5. HSV → RGB; restore matrix-only passthrough for invalid samples.
    rgb_out = _hsv_to_rgb_dcp(h, s, v)
    rgb_out = np.where(valid_mask[..., None], rgb_out, rgb_in)

    # 6. Clamp to [0, 1] — dt accepts but warns on out-of-range cube cells;
    #    pre-clamping is more predictable. Saturated colors at gamut
    #    boundary will be soft-clipped, which matches Adobe's behavior.
    rgb_out = np.clip(rgb_out, 0.0, 1.0)

    # 7. Write Resolve .cube. Iteration order: B-outer, G-middle, R-inner →
    #    matches dt's indexing.
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# lrt-cinema DCP HSV-cube bake, size={n}\n")
        f.write(f"LUT_3D_SIZE {n}\n")
        f.write("DOMAIN_MIN 0.0 0.0 0.0\n")
        f.write("DOMAIN_MAX 1.0 1.0 1.0\n")
        for bi in range(n):
            for gi in range(n):
                for ri in range(n):
                    px = rgb_out[ri, gi, bi]
                    f.write(f"{px[0]:.6f} {px[1]:.6f} {px[2]:.6f}\n")
