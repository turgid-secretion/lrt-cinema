#!/usr/bin/env python3
"""Sample our pipeline output at high-ΔE pixel locations, identify the
(h, s, v) coordinates that landed in those cells, and report what hue/sat/val
shifts the LookTable cube applies at those cells."""
import sys, warnings
from pathlib import Path
import numpy as np
warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/.audit_tmp")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/src")
import rawpy
import colour
from PIL import Image
import adobe_pipeline as ap
from lrt_cinema.dcp import parse_dcp
from lrt_cinema.lut3d_baker import _rgb_to_hsv_dcp, _srgb_oetf

NEF = Path("/tmp/v04_test_input/DSC_4053.NEF")
DCP = Path("/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Nikon D750/Nikon D750 Camera Standard.dcp")
LRT = Path("/Volumes/SanDisk Extreme Pro 55AF Media/Projects/2026 international faire timelapse/.lrt/visual/DSC_4053.lrtpreview")

profile = parse_dcp(DCP)
lt = profile.look_table
print(f"LookTable shape (V, H, S, 3): {lt.data_1.shape}")
print(f"  hue_divisions={lt.hue_divisions}, sat={lt.sat_divisions}, val={lt.val_divisions}, srgb_gamma={lt.srgb_gamma}")

# Look at the cube's hue/sat/val shift statistics by V layer.
cube = lt.data_1  # shape (V, H, S, 3): channel 0=hue shift deg, 1=sat scale, 2=val scale
print()
print("LookTable shifts by V layer (sRGB-encoded V axis if srgb_gamma=True):")
print(f"{'V layer':<10} {'avg hue shift':>14} {'avg sat scale':>14} {'avg val scale':>14}")
for v in range(cube.shape[0]):
    avg_h = cube[v, :, :, 0].mean()
    avg_s = cube[v, :, :, 1].mean()
    avg_v = cube[v, :, :, 2].mean()
    print(f"  V={v:<7} {avg_h:>+14.2f} {avg_s:>+14.3f} {avg_v:>+14.3f}")

# Now find the bad-ΔE pixels and report their HSV.
D65_xy = np.array([0.31270, 0.32900])
def to_lab(arr):
    linear = colour.models.eotf_sRGB(arr.astype(np.float64) / 255.0)
    xyz = colour.RGB_to_XYZ(linear, "sRGB", apply_cctf_decoding=False)
    return colour.XYZ_to_Lab(xyz, illuminant=D65_xy)

ours = np.array(Image.open("/tmp/adobe_pipeline_dsc4053_downsized.jpg").convert("RGB"))
target = np.array(Image.open(LRT).convert("RGB"))
de = colour.delta_E(to_lab(ours), to_lab(target), method="CIE 2000")

# Reload the full prophoto array so we can extract HSV.
with rawpy.imread(str(NEF)) as raw:
    asn = 1.0 / np.array(raw.camera_whitebalance[:3], dtype=np.float32)
    asn = asn / asn[1]
camera_rgb = ap.demosaic_camera_rgb(NEF)
ap.APPLY_LOOKTABLE = False  # so we see the HSV BEFORE LookTable application
ap.APPLY_TONECURVE = True
prophoto_pre_lt = ap.apply_adobe_pipeline(camera_rgb, profile, asn, 5500.0)
# Downsample to match.
h_p, w_p, _ = prophoto_pre_lt.shape
target_h, target_w, _ = target.shape
# We need to downsample the float prophoto, not just the rendered sRGB.
# Quick: use PIL on each channel.
pp_resized = np.stack([
    np.array(Image.fromarray((prophoto_pre_lt[..., c] * 65535).clip(0, 65535).astype(np.uint16), mode="I;16").resize((target_w, target_h), Image.BILINEAR), dtype=np.float32) / 65535.0
    for c in range(3)
], axis=-1)
h_arr, s_arr, v_arr, _ = _rgb_to_hsv_dcp(pp_resized)

# Sample high-ΔE pixels.
hi_mask = de > 5
print(f"\nHigh-ΔE pixels ({hi_mask.sum()} = {100*hi_mask.mean():.1f}%):")
print(f"  hue (sectors 0-6): mean={h_arr[hi_mask].mean():.2f}, median={np.median(h_arr[hi_mask]):.2f}")
print(f"  sat:               mean={s_arr[hi_mask].mean():.3f}, median={np.median(s_arr[hi_mask]):.3f}")
print(f"  val (linear):      mean={v_arr[hi_mask].mean():.3f}, median={np.median(v_arr[hi_mask]):.3f}")
print(f"  val (sRGB-enc):    mean={_srgb_oetf(v_arr[hi_mask]).mean():.3f}, median={np.median(_srgb_oetf(v_arr[hi_mask])):.3f}")

lo_mask = de < 2
print(f"\nLow-ΔE pixels ({lo_mask.sum()} = {100*lo_mask.mean():.1f}%):")
print(f"  hue: mean={h_arr[lo_mask].mean():.2f}, median={np.median(h_arr[lo_mask]):.2f}")
print(f"  sat: mean={s_arr[lo_mask].mean():.3f}, median={np.median(s_arr[lo_mask]):.3f}")
print(f"  val: mean={v_arr[lo_mask].mean():.3f}, median={np.median(v_arr[lo_mask]):.3f}")

# What cube cells (V index) are the bad pixels landing in?
v_div = lt.val_divisions
v_scale = float(v_div - 1)
hi_v_encoded = _srgb_oetf(np.clip(v_arr[hi_mask], 0.0, None))
hi_v_idx_f = hi_v_encoded * v_scale
print(f"\nBad pixels' V-axis cube index (0..{v_div-1}):")
print(f"  mean={hi_v_idx_f.mean():.2f}, median={np.median(hi_v_idx_f):.2f}, P5={np.percentile(hi_v_idx_f, 5):.2f}, P95={np.percentile(hi_v_idx_f, 95):.2f}")
