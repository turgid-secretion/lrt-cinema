#!/usr/bin/env python3
"""Take dng_validate's stage3 (post-HSM/LookTable, pre-TC linear ProPhoto)
and replay the rest of Adobe's pipeline. If we can match the final,
we've nailed the algorithm."""
import sys, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/.audit_tmp")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/src")
import numpy as np
import tifffile, colour
from PIL import Image
import importlib, adobe_pipeline as ap; importlib.reload(ap)
from lrt_cinema.dcp import parse_dcp
from scipy.interpolate import PchipInterpolator

D65_xy = np.array([0.31270, 0.32900])
def to_lab_16bit(arr):
    linear = colour.models.eotf_sRGB(arr.astype(np.float64) / 65535.0)
    xyz = colour.RGB_to_XYZ(linear, 'sRGB', apply_cctf_decoding=False)
    return colour.XYZ_to_Lab(xyz, illuminant=D65_xy)

stage3 = tifffile.imread('/tmp/dng_out/DSC_4053_stage3.tif').astype(np.float64) / 65535.0
final  = tifffile.imread('/tmp/dng_out/DSC_4053_dngvalidate.tif')
print(f'stage3: {stage3.shape} min={stage3.min()} max={stage3.max()} mean={stage3.mean():.3f}')

# Crop to final.
oh, ow = stage3.shape[:2]; th, tw = final.shape[:2]
cy = (oh-th)//2; cx = (ow-tw)//2
s3 = stage3[cy:cy+th, cx:cx+tw]

# Apply BaselineExposure (Stage3Gain = 2^0.1 already in stage3? Let me try with and without)
profile = parse_dcp('/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Nikon D750/Nikon D750 Camera Standard.dcp')
curve = profile.profile_tone_curve
pchip = PchipInterpolator(curve[:, 0], curve[:, 1], extrapolate=True)

for label, scale in [('stage3 raw', 1.0), ('stage3 ×2^0.1', 2.0**0.1), ('stage3 ×2^0.2', 2.0**0.2), ('stage3 ×2^-0.1', 2.0**-0.1)]:
    s3_scaled = np.clip(s3 * scale, 0, 1)
    # Apply per-channel TC
    s3_tc = np.zeros_like(s3_scaled)
    for ch in range(3):
        s3_tc[..., ch] = np.clip(pchip(s3_scaled[..., ch]), 0, 1)
    srgb = ap.prophoto_to_srgb(s3_tc.astype(np.float32), bit_depth=16)
    de = colour.delta_E(to_lab_16bit(srgb), to_lab_16bit(final), method='CIE 2000')
    print(f'  {label:<25} mean ΔE={de.mean():.3f} P50={np.percentile(de,50):.3f} <1%={(de<1).mean()*100:.1f}%')

# Also: maybe stage3 already has TC applied. Try without our TC.
print()
print('Without applying TC (stage3 might already be post-TC):')
for label, scale in [('stage3 raw', 1.0), ('stage3 ×2^0.1', 2.0**0.1)]:
    s3_scaled = np.clip(s3 * scale, 0, 1).astype(np.float32)
    srgb = ap.prophoto_to_srgb(s3_scaled, bit_depth=16)
    de = colour.delta_E(to_lab_16bit(srgb), to_lab_16bit(final), method='CIE 2000')
    print(f'  {label:<25} mean ΔE={de.mean():.3f} P50={np.percentile(de,50):.3f} <1%={(de<1).mean()*100:.1f}%')
