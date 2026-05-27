#!/usr/bin/env python3
"""First-principles implementation of Adobe DNG 1.7.1 reference pipeline.

Bypasses darktable and lrt-cinema's emit machinery entirely. Uses:
  * rawpy (libraw) ONLY for the Bayer demosaic + black-level subtract.
    Libraw IS dcraw, which IS the open reference demosaic; demosaic is
    a sensor operation, not a color operation, so this is acceptable.
  * lrt-cinema's dcp.py — pure-Python DCP parser (reads the file format,
    no color processing). Reused as a library.
  * lrt-cinema's lut3d_baker.py — pure-Python HSV cube application math.
    Reused as a library; we apply it directly to numpy arrays.
  * colour-science — color-space math (XYZ ↔ Lab ↔ sRGB, CIE 1931 utilities).
  * numpy — array math.

Implements the pipeline from DNG 1.7.1 §"Mapping Camera Color Space":
  1. Demosaic (rawpy/libraw)
  2. Black-level subtract + white-level normalize
  3. Apply AsShotNeutral inverse (per-channel WB multipliers)
  4. Camera RGB → XYZ(D50) via interpolated ColorMatrix at scene kelvin
       (or via ForwardMatrix * Diag(AsShotNeutral) if ForwardMatrix present)
  5. XYZ(D50) → linear ProPhoto (DCP working space)
  6. HueSatMap (mired-blended by kelvin)
  7. BaselineExposureOffset (multiplicative on V)
  8. LookTable
  9. ProfileToneCurve (on V)
 10. ProPhoto → XYZ(D50) → sRGB(D65 via Bradford CAT) → sRGB gamma encode
 11. Compare to LRT preview JPEG

Output: an sRGB PNG/JPEG that should match the LRT preview within ~1 ΔE
if the pipeline is correctly implemented.
"""
import sys
import warnings
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/src")

import rawpy
import colour
from PIL import Image

from lrt_cinema.dcp import (
    parse_dcp, interpolate_color_matrix, interpolate_hsv_cube,
    kelvin_tint_to_xy, xy_to_uv, uv_to_kelvin,
)
from lrt_cinema.lut3d_baker import (
    _rgb_to_hsv_dcp, _hsv_to_rgb_dcp, _apply_hsv_cube,
)

# Canonical D50 white point (CIE 1931).
D50_XYZ = np.array([0.96422, 1.00000, 0.82521])
D65_XYZ = np.array([0.95047, 1.00000, 1.08883])
D50_xy = np.array([0.34567, 0.35850])
D65_xy = np.array([0.31270, 0.32900])

# ProPhoto RGB (D50) primary matrices, from colour-science.
M_PROPHOTO_D50_TO_XYZ_D50 = colour.RGB_COLOURSPACES["ProPhoto RGB"].matrix_RGB_to_XYZ
M_XYZ_D50_TO_PROPHOTO_D50 = colour.RGB_COLOURSPACES["ProPhoto RGB"].matrix_XYZ_to_RGB

# sRGB matrices (D65).
M_XYZ_D65_TO_SRGB = colour.RGB_COLOURSPACES["sRGB"].matrix_XYZ_to_RGB

# Bradford CAT D50 → D65.
M_BRADFORD_D50_TO_D65 = colour.adaptation.matrix_chromatic_adaptation_VonKries(
    D50_XYZ, D65_XYZ, transform="Bradford",
)


def srgb_oetf(x):
    """sRGB EOTF^-1 (linear → gamma-encoded). IEC 61966-2-1."""
    a = 0.055
    return np.where(x <= 0.0031308, x * 12.92, (1 + a) * np.power(np.maximum(x, 0), 1/2.4) - a)


def demosaic_camera_rgb(nef_path: Path) -> np.ndarray:
    """Demosaic NEF via rawpy/libraw. Returns float32 (H, W, 3) in
    LINEAR camera RGB, normalized [0, 1] after black-level subtract.

    Uses minimal libraw post-processing: no auto-bright, no WB, no gamma,
    no color matrix. Demosaic only.
    """
    with rawpy.imread(str(nef_path)) as raw:
        # Use libraw's postprocess with maximally-neutral settings.
        # output_bps=16: 16-bit per channel output.
        # gamma=(1, 1): no gamma encoding (linear output).
        # no_auto_bright=True: don't apply auto-exposure.
        # use_camera_wb=False, use_auto_wb=False: no WB applied (we'll do it).
        # output_color=rawpy.ColorSpace.raw: no color transform (we'll do it).
        # user_wb=(1,1,1,1): identity WB (we'll multiply manually).
        rgb = raw.postprocess(
            output_bps=16,
            gamma=(1, 1),
            no_auto_bright=True,
            use_camera_wb=False,
            use_auto_wb=False,
            user_wb=[1.0, 1.0, 1.0, 1.0],
            output_color=rawpy.ColorSpace.raw,
            demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,  # libraw default
            half_size=False,
            four_color_rgb=False,
            highlight_mode=rawpy.HighlightMode.Clip,
        )
        # rawpy already does black-level subtract and white-level normalize
        # in postprocess(). Returns 16-bit values in [0, 65535] mapped from
        # [black_level, white_level].
        rgb_f = rgb.astype(np.float32) / 65535.0
        return rgb_f


def apply_adobe_pipeline(
    camera_rgb: np.ndarray,
    profile,
    as_shot_neutral: np.ndarray,
    scene_kelvin: float,
) -> np.ndarray:
    """Apply DNG 1.7.1 reference pipeline. Input: linear camera RGB
    [0, 1]. Output: linear ProPhoto RGB (D50) [0, 1+].

    Steps per DNG 1.7.1 §"Mapping Camera Color Space":
      1. Multiply by per-channel WB scaling (1/as_shot_neutral).
         (AsShotNeutral is in CAMERA-NEUTRAL space; the inverse
          rebalances channels so a neutral subject lands at gray.)
      2. Build camera-RGB → XYZ(D50) matrix.
         If ForwardMatrix present: XYZ(D50) = ForwardMatrix × Diag(AsShotNeutral) × camera_rgb
         Else: XYZ(D50) = inverse(ColorMatrix) × normalized camera_rgb
      3. XYZ(D50) → ProPhoto(D50).
      4. HSV decompose (Adobe hexcone variant — 0..6 sectors).
      5. Apply HueSatMap (mired-blended by scene kelvin).
      6. Apply BaselineExposureOffset (multiplicative on V).
      7. Apply LookTable.
      8. Apply ProfileToneCurve (on V, linear-encoded).
      9. HSV recompose to ProPhoto.
    """
    h, w, _ = camera_rgb.shape

    # --- Step 1: AsShotNeutral inverse → balanced camera RGB ---
    # AsShotNeutral entries are camera-RGB values of neutral. Per DNG:
    # "the multipliers are reciprocals of AsShotNeutral, normalized so
    # that the green channel multiplier = 1." This makes a neutral subject
    # have R=G=B=neutral_val after balancing.
    wb_mul = 1.0 / as_shot_neutral
    wb_mul = wb_mul / wb_mul[1]   # normalize green to 1
    balanced = camera_rgb * wb_mul[None, None, :]

    # --- Step 2: build camera → XYZ(D50) matrix ---
    # DNG 1.7.1 § "Camera to XYZ (D50) Transform". When ForwardMatrix is
    # present (preferred): XYZ_D50 = FM × diag(1/AsShotNeutral) × camera_rgb.
    # Equivalently: XYZ_D50 = FM × balanced  (where balanced = camera_rgb / AsShotNeutral).
    # When ForwardMatrix absent: use inverse-ColorMatrix path with iterative
    # neutral normalization.
    #
    # Forward matrix is calibrated to map balanced-camera-RGB whose
    # neutral = (1, 1, 1) directly to D50 XYZ. The diag(1/AsShotNeutral)
    # rebalances camera RGB into the FM's expected input space.
    if profile.forward_matrix_1 is not None:
        # Interpolate FM by kelvin if FM2 is also present and differs.
        if (profile.forward_matrix_2 is not None
                and not np.allclose(profile.forward_matrix_1, profile.forward_matrix_2)):
            k_lo, k_hi = sorted([profile.kelvin_1, profile.kelvin_2])
            if profile.kelvin_1 <= profile.kelvin_2:
                fm_lo, fm_hi = profile.forward_matrix_1, profile.forward_matrix_2
            else:
                fm_lo, fm_hi = profile.forward_matrix_2, profile.forward_matrix_1
            if scene_kelvin <= k_lo:
                fm = fm_lo
            elif scene_kelvin >= k_hi:
                fm = fm_hi
            else:
                f = (1/scene_kelvin - 1/k_lo) / (1/k_hi - 1/k_lo)
                fm = (1 - f) * fm_lo + f * fm_hi
        else:
            fm = profile.forward_matrix_1
        # XYZ_D50 = FM × balanced.
        xyz = balanced.reshape(-1, 3) @ fm.T
        xyz = xyz.reshape(h, w, 3).astype(np.float32)
    else:
        # Fallback: inverse-ColorMatrix path with neutral normalization.
        cm = interpolate_color_matrix(profile, scene_kelvin)  # XYZ_D50 → camera_RGB
        cm_inv = np.linalg.inv(cm)
        xyz = camera_rgb.reshape(-1, 3) @ cm_inv.T
        n_xyz = cm_inv @ as_shot_neutral
        xyz = xyz / n_xyz[1]
        xyz = xyz.reshape(h, w, 3).astype(np.float32)

    # --- Step 3: XYZ(D50) → linear ProPhoto(D50) ---
    prophoto = xyz.reshape(-1, 3) @ M_XYZ_D50_TO_PROPHOTO_D50.T
    prophoto = prophoto.reshape(h, w, 3).astype(np.float32)

    # --- Step 4: RGB → HSV (Adobe hexcone variant) ---
    h_arr, s_arr, v_arr, valid = _rgb_to_hsv_dcp(prophoto)

    # --- Step 5: Apply HueSatMap if present ---
    if profile.hue_sat_map is not None:
        hsm_blended = interpolate_hsv_cube(
            profile.hue_sat_map, scene_kelvin,
            profile.kelvin_1, profile.kelvin_2,
        )
        h_arr, s_arr, v_arr = _apply_hsv_cube(
            h_arr, s_arr, v_arr, hsm_blended, profile.hue_sat_map,
        )

    # --- Step 6: BaselineExposureOffset on V (multiplicative, EV → linear) ---
    if profile.baseline_exposure_offset != 0.0:
        v_arr = v_arr * (2.0 ** profile.baseline_exposure_offset)

    # --- Step 7: Apply LookTable if present ---
    if profile.look_table is not None and globals().get("APPLY_LOOKTABLE", True):
        h_arr, s_arr, v_arr = _apply_hsv_cube(
            h_arr, s_arr, v_arr, profile.look_table.data_1, profile.look_table,
        )

    # --- Step 8: HSV → RGB (no ProfileToneCurve on V — Adobe SDK applies per-channel later) ---
    prophoto_out = _hsv_to_rgb_dcp(h_arr, s_arr, v_arr)
    # For pixels with negative input (matrix-only fallback), pass through.
    prophoto_out = np.where(valid[..., None], prophoto_out, prophoto)

    # --- Step 9: ProfileToneCurve applied PER-CHANNEL in linear ProPhoto ---
    # Per dng_render.cpp::DoBaselineRGBTone: Adobe SDK applies the
    # ProfileToneCurve per R,G,B channel independently — NOT on V as the
    # DNG 1.7.1 spec text suggests. SDK behavior is the ground truth.
    # The curve is solved as a (monotone) cubic spline before per-channel
    # application (dng_render.cpp::Solve via dng_spline_solver).
    if profile.profile_tone_curve is not None and globals().get("APPLY_TONECURVE", True):
        curve = profile.profile_tone_curve
        from scipy.interpolate import PchipInterpolator
        # PCHIP = monotone cubic Hermite. Closest to dng_spline_solver's
        # tangent-clamped Hermite spline used by Adobe DNG SDK.
        pchip = PchipInterpolator(curve[:, 0], curve[:, 1], extrapolate=True)
        clipped = np.clip(prophoto_out, 0.0, 1.0)
        for ch in range(3):
            prophoto_out[..., ch] = np.clip(pchip(clipped[..., ch]), 0.0, 1.0).astype(np.float32)

    # Apply BaselineExposure (scalar EV, on top of all above).
    if profile.baseline_exposure != 0.0:
        prophoto_out = prophoto_out * (2.0 ** profile.baseline_exposure)

    return prophoto_out


def prophoto_to_srgb(prophoto: np.ndarray, bit_depth: int = 8) -> np.ndarray:
    """Linear ProPhoto(D50) → sRGB(D65) → gamma-encoded.
    bit_depth=8 returns uint8; 16 returns uint16."""
    h, w, _ = prophoto.shape
    xyz_d50 = prophoto.reshape(-1, 3) @ M_PROPHOTO_D50_TO_XYZ_D50.T
    xyz_d65 = xyz_d50 @ M_BRADFORD_D50_TO_D65.T
    linear_srgb = xyz_d65 @ M_XYZ_D65_TO_SRGB.T
    linear_srgb = np.clip(linear_srgb, 0.0, 1.0).reshape(h, w, 3)
    encoded = srgb_oetf(linear_srgb)
    if bit_depth == 16:
        return (encoded * 65535).astype(np.uint16)
    return (encoded * 255).astype(np.uint8)


def measure_de(ours_srgb_uint8: np.ndarray, target_srgb_uint8: np.ndarray) -> dict:
    """ΔE2000 between two sRGB uint8 arrays of the same shape. Returns
    mean, percentiles, distribution buckets."""
    # Decode both via sRGB EOTF → linear → XYZ → Lab(D65).
    def to_lab(arr):
        linear = colour.models.eotf_sRGB(arr.astype(np.float64) / 255.0)
        xyz = colour.RGB_to_XYZ(linear, "sRGB", apply_cctf_decoding=False)
        return colour.XYZ_to_Lab(xyz, illuminant=D65_xy)
    ours_lab = to_lab(ours_srgb_uint8)
    tgt_lab = to_lab(target_srgb_uint8)
    de = colour.delta_E(ours_lab, tgt_lab, method="CIE 2000")
    de_flat = de.flatten()
    buckets = {}
    for lo, hi, label in [
        (0, 1, "<1"), (1, 2, "1-2"), (2, 3, "2-3"),
        (3, 5, "3-5"), (5, 10, "5-10"), (10, 1e9, ">=10"),
    ]:
        buckets[label] = float(((de_flat >= lo) & (de_flat < hi)).mean() * 100)
    return {
        "mean": float(de.mean()),
        "P50": float(np.percentile(de_flat, 50)),
        "P95": float(np.percentile(de_flat, 95)),
        "P99": float(np.percentile(de_flat, 99)),
        "max": float(de_flat.max()),
        "buckets": buckets,
    }


# ============================================================================
# Test on DSC_4053
# ============================================================================
if __name__ == "__main__":
    nef = Path("/tmp/v04_test_input/DSC_4053.NEF")
    dcp = Path("/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Nikon D750/Nikon D750 Camera Standard.dcp")
    lrt_preview = Path("/Volumes/SanDisk Extreme Pro 55AF Media/Projects/2026 international faire timelapse/.lrt/visual/DSC_4053.lrtpreview")

    # Load NEF + extract AsShotNeutral from libraw.
    with rawpy.imread(str(nef)) as raw:
        as_shot = np.array(raw.camera_whitebalance[:3], dtype=np.float32)
        # libraw gives reciprocal-style multipliers; AsShotNeutral is the
        # CAMERA-RGB neutral itself. Need to invert.
        # camera_whitebalance returns the per-channel SCALING needed to
        # achieve neutral. AsShotNeutral = camera RGB value at white,
        # so multipliers = 1/AsShotNeutral (then normalized to green=1).
        # If multipliers are [2.0, 1.0, 1.289], AsShotNeutral = [0.5, 1.0, 0.776].
        as_shot_neutral = 1.0 / as_shot
        as_shot_neutral = as_shot_neutral / as_shot_neutral[1]  # G=1
        print(f"camera_wb multipliers: {as_shot.tolist()}")
        print(f"as_shot_neutral (G=1): {as_shot_neutral.tolist()}")

    profile = parse_dcp(dcp)
    print(f"DCP: {profile.profile_name!r}")
    print(f"  HSM: {None if profile.hue_sat_map is None else (profile.hue_sat_map.hue_divisions, profile.hue_sat_map.sat_divisions, profile.hue_sat_map.val_divisions)}")
    print(f"  LT:  {None if profile.look_table is None else (profile.look_table.hue_divisions, profile.look_table.sat_divisions, profile.look_table.val_divisions)}")
    print(f"  TC:  {None if profile.profile_tone_curve is None else f'{profile.profile_tone_curve.shape[0]} pts'}")
    print(f"  BE:  {profile.baseline_exposure}  BEO: {profile.baseline_exposure_offset}")
    print(f"  illuminants: k1={profile.kelvin_1}, k2={profile.kelvin_2}")

    # Derive scene kelvin from AsShotNeutral (DNG SDK does this iteratively).
    # For now: use the camera's recorded WB. The user said it was set to
    # 5500K (manual), and EXIF confirms. Use 5500.
    scene_kelvin = 5500.0
    print(f"scene kelvin (manual 5500): {scene_kelvin}")

    # Demosaic.
    print("demosaicing...")
    camera_rgb = demosaic_camera_rgb(nef)
    print(f"camera RGB shape: {camera_rgb.shape}, range [{camera_rgb.min():.3f}, {camera_rgb.max():.3f}]")

    # Apply Adobe pipeline.
    print("applying Adobe pipeline (DCP)...")
    prophoto = apply_adobe_pipeline(camera_rgb, profile, as_shot_neutral, scene_kelvin)
    print(f"ProPhoto shape: {prophoto.shape}, range [{prophoto.min():.3f}, {prophoto.max():.3f}]")

    # Encode to sRGB.
    print("encoding to sRGB...")
    srgb = prophoto_to_srgb(prophoto)
    print(f"sRGB shape: {srgb.shape}")

    # Save.
    out_path = Path("/tmp/adobe_pipeline_dsc4053.jpg")
    Image.fromarray(srgb).save(out_path, quality=92)
    print(f"saved: {out_path}")

    # Compare to LRT preview.
    print()
    print("loading LRT preview...")
    target = np.array(Image.open(lrt_preview).convert("RGB"))
    print(f"target shape: {target.shape}")

    # Downsample our render to target resolution for comparison.
    ours_pil = Image.fromarray(srgb)
    ours_resized = np.array(ours_pil.resize((target.shape[1], target.shape[0]), Image.BILINEAR))

    # Save downsized for visual.
    Image.fromarray(ours_resized).save("/tmp/adobe_pipeline_dsc4053_downsized.jpg", quality=92)

    print("computing ΔE2000...")
    result = measure_de(ours_resized, target)
    print()
    print("=" * 60)
    print("First-principles Adobe pipeline vs LRT preview (DSC_4053)")
    print("=" * 60)
    print(f"Mean ΔE: {result['mean']:.2f}")
    print(f"P50:     {result['P50']:.2f}")
    print(f"P95:     {result['P95']:.2f}")
    print(f"P99:     {result['P99']:.2f}")
    print(f"Max:     {result['max']:.2f}")
    print()
    print("Bucket distribution:")
    for bucket, pct in result["buckets"].items():
        print(f"  {bucket:>6}: {pct:5.1f}%")
