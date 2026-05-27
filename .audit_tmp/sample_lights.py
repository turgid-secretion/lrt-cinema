#!/usr/bin/env python3
"""Sample the ceiling light pixels in LRT preview vs our render."""
import sys
import warnings
from pathlib import Path
import numpy as np
warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/src")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/.audit_tmp")
import rawpy
from PIL import Image
import adobe_pipeline as ap
from lrt_cinema.dcp import parse_dcp

LRT = Path("/Volumes/SanDisk Extreme Pro 55AF Media/Projects/2026 international faire timelapse/.lrt/visual/DSC_4053.lrtpreview")
OURS_FULL_LT = Path("/tmp/adobe_pipeline_dsc4053_downsized.jpg")
NEF = Path("/tmp/v04_test_input/DSC_4053.NEF")
DCP = Path("/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Nikon D750/Nikon D750 Camera Standard.dcp")

target = np.array(Image.open(LRT).convert("RGB"))
ours = np.array(Image.open(OURS_FULL_LT).convert("RGB"))

print(f"image shape: {target.shape}")

# Ceiling light bands are in the top portion of the image, left half.
# From the heatmap, hot cells were at (row 0, col 0-2). Let's sample
# from a known light pixel location.
# Looking at the rendered image: the ceiling lights are around y=130-180
# (a band), x ~ 50-450 (left half).
light_y = slice(130, 180)
light_x = slice(50, 450)

ours_sample = ours[light_y, light_x]
tgt_sample = target[light_y, light_x]
print(f"\nCeiling-light region sample ({ours_sample.shape}):")
print(f"  ours  mean RGB: {ours_sample.reshape(-1, 3).mean(axis=0)}")
print(f"  tgt   mean RGB: {tgt_sample.reshape(-1, 3).mean(axis=0)}")
print(f"  ours  max RGB:  {ours_sample.reshape(-1, 3).max(axis=0)}")
print(f"  tgt   max RGB:  {tgt_sample.reshape(-1, 3).max(axis=0)}")

# Pixels that are highest-value in OURS — what does target show?
ours_L = (0.2126*ours_sample[..., 0] + 0.7152*ours_sample[..., 1] + 0.0722*ours_sample[..., 2]).astype(np.float32)
top_pixels_idx = np.unravel_index(np.argsort(ours_L.flatten())[-200:], ours_L.shape)
print(f"\nTop-200 brightest pixels in OURS (saturated lights):")
print(f"  ours  RGB:  {ours_sample[top_pixels_idx].mean(axis=0)}")
print(f"  tgt   RGB:  {tgt_sample[top_pixels_idx].mean(axis=0)}")

# Render WITHOUT LookTable to see what the bright pixels look like at that stage.
profile = parse_dcp(DCP)
with rawpy.imread(str(NEF)) as raw:
    asn = 1.0 / np.array(raw.camera_whitebalance[:3], dtype=np.float32)
    asn = asn / asn[1]
camera_rgb = ap.demosaic_camera_rgb(NEF)
ap.APPLY_LOOKTABLE = False
ap.APPLY_TONECURVE = True
prophoto_nolt = ap.apply_adobe_pipeline(camera_rgb, profile, asn, 5500.0)
srgb_nolt = ap.prophoto_to_srgb(prophoto_nolt)
ours_nolt = np.array(Image.fromarray(srgb_nolt).resize((target.shape[1], target.shape[0]), Image.BILINEAR))
print(f"\nSame region in OURS-WITHOUT-LookTable:")
print(f"  no-LT mean: {ours_nolt[light_y, light_x].reshape(-1, 3).mean(axis=0)}")
print(f"  no-LT top-200 brightest: {ours_nolt[light_y, light_x][top_pixels_idx].mean(axis=0)}")

# And no-tone-curve, no-LT (pure matrix only).
ap.APPLY_TONECURVE = False
prophoto_min = ap.apply_adobe_pipeline(camera_rgb, profile, asn, 5500.0)
srgb_min = ap.prophoto_to_srgb(prophoto_min)
ours_min = np.array(Image.fromarray(srgb_min).resize((target.shape[1], target.shape[0]), Image.BILINEAR))
print(f"\nSame region in OURS-MATRIX-ONLY (no LT, no TC):")
print(f"  matrix-only mean: {ours_min[light_y, light_x].reshape(-1, 3).mean(axis=0)}")
print(f"  matrix-only top-200 brightest: {ours_min[light_y, light_x][top_pixels_idx].mean(axis=0)}")
