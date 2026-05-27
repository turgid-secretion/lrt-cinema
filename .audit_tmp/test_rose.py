#!/usr/bin/env python3
"""Test first-principles pipeline on the rose scene."""
import sys, warnings
from pathlib import Path
import numpy as np
warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/.audit_tmp")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/src")
import rawpy
from PIL import Image
import adobe_pipeline as ap
from lrt_cinema.dcp import parse_dcp

NEF = Path("/tmp/d750_sample.NEF")
DCP = Path("/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Nikon D750/Nikon D750 Camera Standard.dcp")
REF = Path("/tmp/rose_camera_jpeg.jpg")  # full-res Nikon Picture Control Neutral

profile = parse_dcp(DCP)
with rawpy.imread(str(NEF)) as raw:
    asn = 1.0 / np.array(raw.camera_whitebalance[:3], dtype=np.float32)
    asn = asn / asn[1]

camera_rgb = ap.demosaic_camera_rgb(NEF)
target = np.array(Image.open(REF).convert("RGB"))

ap.APPLY_LOOKTABLE = True
ap.APPLY_TONECURVE = True

prophoto = ap.apply_adobe_pipeline(camera_rgb, profile, asn, 5500.0)
srgb = ap.prophoto_to_srgb(prophoto)
print(f"our render shape: {srgb.shape}")
print(f"target shape: {target.shape}")

# Full-resolution comparison (no downsample needed; both are full-res).
# Need to handle slight size mismatch from rawpy (6032x4032) vs camera JPEG (6016x4016).
oh, ow = srgb.shape[:2]
th, tw = target.shape[:2]
print(f"size mismatch: ours {ow}x{oh} vs target {tw}x{th}")
# Crop ours to target.
if oh > th or ow > tw:
    cy = (oh - th) // 2
    cx = (ow - tw) // 2
    srgb_cropped = srgb[cy:cy+th, cx:cx+tw]
else:
    srgb_cropped = srgb

print(f"cropped to: {srgb_cropped.shape}")
r = ap.measure_de(srgb_cropped, target)
print(f"\nFirst-principles pipeline vs Nikon Picture Control Neutral (rose):")
print(f"Mean ΔE: {r['mean']:.2f}")
print(f"P50:     {r['P50']:.2f}")
print(f"P95:     {r['P95']:.2f}")
print(f"P99:     {r['P99']:.2f}")
print(f"Max:     {r['max']:.2f}")
print()
print("Bucket distribution:")
for bucket, pct in r["buckets"].items():
    print(f"  {bucket:>6}: {pct:5.1f}%")

# Save for visual.
Image.fromarray(srgb_cropped).save("/tmp/adobe_pipeline_rose.jpg", quality=92)
print(f"\nsaved: /tmp/adobe_pipeline_rose.jpg")
