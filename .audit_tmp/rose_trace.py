#!/usr/bin/env python3
"""Trace rose pipeline at specific pixels to find brightness bias source."""
import sys, warnings
import numpy as np
warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/src")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/.audit_tmp")
import tifffile, rawpy, colour
from PIL import Image
import adobe_pipeline as ap
from lrt_cinema.dcp import parse_dcp

DCP = "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Adobe Standard/Nikon D750 Adobe Standard.dcp"
DNG = "/tmp/dng_out/rose.dng"
REF = tifffile.imread("/tmp/dng_out/rose_dngval_Camera_Standard.tif")

# Sample mid-luminance gray patches. Use coordinates 2000, 5000 (background)
ys = [800, 1500, 2500, 3000]
xs = [800, 1800, 3500, 5200]

# Run our pipeline
with rawpy.imread(DNG) as raw:
    asn = 1.0 / np.array(raw.camera_whitebalance[:3], dtype=np.float32); asn = asn / asn[1]
camera_rgb = ap.demosaic_camera_rgb(DNG)
profile = parse_dcp(DCP)
ap.APPLY_LOOKTABLE = True
ap.APPLY_TONECURVE = True
pp = ap.apply_adobe_pipeline(camera_rgb, profile, asn, 5500.0)
srgb_ours = ap.prophoto_to_srgb(pp)

# Crop to match REF
oh, ow = srgb_ours.shape[:2]; th, tw = REF.shape[:2]
cy = (oh - th) // 2; cx = (ow - tw) // 2
srgb_ours = srgb_ours[cy:cy+th, cx:cx+tw]

print(f"{'pixel':<14} {'cam_rgb':<22} {'ours_sRGB':<14} {'dng_val_sRGB':<14} {'L_ratio':>6}")
print("-" * 80)
for y, x in zip(ys, xs):
    cam = camera_rgb[y, x]
    ours = srgb_ours[y, x]
    ref8 = (REF[y, x].astype(np.float32) / 65535.0 * 255).astype(np.uint8)
    # Linear sRGB
    ours_lin = (ours / 255.0) ** 2.4
    ref_lin = ((ref8 / 255.0)) ** 2.4
    l_ratio = ours_lin.mean() / max(ref_lin.mean(), 1e-6)
    print(f"({y:>5},{x:>5})  {cam.tolist()!r:<22} {ours.tolist()!r:<14} {ref8.tolist()!r:<14} {l_ratio:>6.3f}")
