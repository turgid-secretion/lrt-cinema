#!/usr/bin/env python3
"""Compare dng_validate's intermediate stages to our pipeline at each stage."""
import sys, warnings
from pathlib import Path
import numpy as np
warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/.audit_tmp")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/src")
import tifffile
import colour
import rawpy
from PIL import Image
import adobe_pipeline as ap
from lrt_cinema.dcp import parse_dcp

D65_xy = np.array([0.31270, 0.32900])

# Load dng_validate stage outputs
stage3 = tifffile.imread('/tmp/dng_out/DSC_4053_stage3.tif')  # post-DCP linear ProPhoto (per spec)
final = tifffile.imread('/tmp/dng_out/DSC_4053_dngvalidate.tif')  # final sRGB 16-bit
print(f'stage3: {stage3.shape} dtype={stage3.dtype}')
print(f'final:  {final.shape}')

# What's stage3 actually? Let me check by encoding it via our prophoto_to_srgb.
stage3_f = stage3.astype(np.float32) / 65535.0
print(f'stage3 range: [{stage3_f.min():.3f}, {stage3_f.max():.3f}]')

# Encode stage3 (treat as linear ProPhoto D50) via our prophoto_to_srgb.
encoded = ap.prophoto_to_srgb(stage3_f)
print(f'encoded stage3 → sRGB: shape={encoded.shape}')

# Crop encoded to match final (final is 4016x6016, stage3 is 4032x6032).
oh, ow = encoded.shape[:2]; th, tw = final.shape[:2]
cy = (oh - th) // 2; cx = (ow - tw) // 2
encoded_c = encoded[cy:cy+th, cx:cx+tw]

# Final to uint8 for comparison.
final_8 = (final.astype(np.float32) / 65535.0 * 255).astype(np.uint8)

# ΔE comparison.
def to_lab(arr):
    linear = colour.models.eotf_sRGB(arr.astype(np.float64) / 255.0)
    xyz = colour.RGB_to_XYZ(linear, 'sRGB', apply_cctf_decoding=False)
    return colour.XYZ_to_Lab(xyz, illuminant=D65_xy)

de = colour.delta_E(to_lab(encoded_c), to_lab(final_8), method='CIE 2000')
print(f'\nstage3 encoded by US vs dng_validate final:')
print(f'  mean={de.mean():.2f}  P50={np.percentile(de, 50):.2f}  P95={np.percentile(de, 95):.2f}  max={de.max():.2f}')
print(f'  < 1 ΔE: {((de.flatten()) < 1).mean() * 100:.1f}% of pixels')

# Hmm what if stage3 isn't linear ProPhoto. Maybe it's already-tone-mapped?
# Compare stage3 and final luminances directly.
print(f'\nstage3 mean: {stage3_f.mean():.3f}')
print(f'final L*: should be tone-mapped. final.mean over 255 = {final_8.mean()/255:.3f}')

# If stage3 is already tone-mapped linear, then encoding to sRGB should give a tone-mapped output.
# If stage3 is pre-tone-curve linear, then we need to apply the DCP tone curve.

# Let me also try: render OUR pipeline directly with intermediate inspection.
profile = parse_dcp('/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Nikon D750/Nikon D750 Camera Standard.dcp')
with rawpy.imread('/tmp/v04_test_input/DSC_4053.NEF') as raw:
    asn = 1.0 / np.array(raw.camera_whitebalance[:3], dtype=np.float32); asn = asn / asn[1]
camera_rgb = ap.demosaic_camera_rgb('/tmp/v04_test_input/DSC_4053.NEF')

# Our linear ProPhoto BEFORE tone curve.
ap.APPLY_LOOKTABLE = True; ap.APPLY_TONECURVE = False  # no TC
our_prophoto_no_tc = ap.apply_adobe_pipeline(camera_rgb, profile, asn, 5500.0)
print(f'\nour ProPhoto (NO tone curve): mean={our_prophoto_no_tc.mean():.3f}, max={our_prophoto_no_tc.max():.3f}')

# Our linear ProPhoto AFTER tone curve.
ap.APPLY_TONECURVE = True
our_prophoto_tc = ap.apply_adobe_pipeline(camera_rgb, profile, asn, 5500.0)
print(f'our ProPhoto (WITH tone curve): mean={our_prophoto_tc.mean():.3f}, max={our_prophoto_tc.max():.3f}')

# Compare to stage3.
print(f'\ndng_validate stage3 mean={stage3_f.mean():.3f}')
print(f'   matches our (with TC)?    {abs(stage3_f.mean() - our_prophoto_tc.mean()) < 0.05}')
print(f'   matches our (without TC)? {abs(stage3_f.mean() - our_prophoto_no_tc.mean()) < 0.05}')

# Crop to compare
our_no_tc_c = our_prophoto_no_tc[cy:cy+th, cx:cx+tw]
our_tc_c = our_prophoto_tc[cy:cy+th, cx:cx+tw]
print(f'\nMean Lab delta (our - stage3, treating both as linear ProPhoto):')
# Convert via ProPhoto → XYZ_D50 → Lab(D50)
def prophoto_to_lab(arr):
    xyz = arr.reshape(-1, 3) @ ap.M_PROPHOTO_D50_TO_XYZ_D50.T
    return colour.XYZ_to_Lab(xyz, illuminant=np.array([0.34567, 0.35850])).reshape(arr.shape)
stage3_lab = prophoto_to_lab(stage3_f[cy:cy+th, cx:cx+tw])
our_no_tc_lab = prophoto_to_lab(our_no_tc_c)
our_tc_lab = prophoto_to_lab(our_tc_c)

de_no_tc = colour.delta_E(our_no_tc_lab.reshape(-1, 3), stage3_lab.reshape(-1, 3), method='CIE 2000')
de_tc = colour.delta_E(our_tc_lab.reshape(-1, 3), stage3_lab.reshape(-1, 3), method='CIE 2000')
print(f'  ours-NO-TC vs stage3:   mean ΔE={de_no_tc.mean():.2f}, P50={np.percentile(de_no_tc, 50):.2f}')
print(f'  ours-WITH-TC vs stage3: mean ΔE={de_tc.mean():.2f}, P50={np.percentile(de_tc, 50):.2f}')
