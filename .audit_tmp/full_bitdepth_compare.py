#!/usr/bin/env python3
"""Compare ours vs dng_validate at full 16-bit depth, full resolution.
Removes downsample + 8-bit quantization from the measurement."""
import sys, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/.audit_tmp")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/src")
import numpy as np
import rawpy, tifffile, colour
from PIL import Image
import importlib, adobe_pipeline as ap; importlib.reload(ap)
from lrt_cinema.dcp import parse_dcp

D65_xy = np.array([0.31270, 0.32900])

def to_lab_16bit(arr_uint16):
    linear = colour.models.eotf_sRGB(arr_uint16.astype(np.float64) / 65535.0)
    xyz = colour.RGB_to_XYZ(linear, "sRGB", apply_cctf_decoding=False)
    return colour.XYZ_to_Lab(xyz, illuminant=D65_xy)

# Render ours at full 16-bit
profile = parse_dcp('/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Nikon D750/Nikon D750 Camera Standard.dcp')
with rawpy.imread('/tmp/v04_test_input/DSC_4053.NEF') as raw:
    asn = 1.0 / np.array(raw.camera_whitebalance[:3], dtype=np.float32); asn = asn / asn[1]
camera_rgb = ap.demosaic_camera_rgb('/tmp/v04_test_input/DSC_4053.NEF')
ap.APPLY_LOOKTABLE = True; ap.APPLY_TONECURVE = True
prophoto = ap.apply_adobe_pipeline(camera_rgb, profile, asn, 5500.0)
ours_16 = ap.prophoto_to_srgb(prophoto, bit_depth=16)
print(f'ours 16-bit: {ours_16.shape}')

# dng_validate output
dng_val = tifffile.imread('/tmp/dng_out/DSC_4053_dngvalidate.tif')
print(f'dng_validate: {dng_val.shape}')

# Crop ours to match
oh, ow = ours_16.shape[:2]; th, tw = dng_val.shape[:2]
cy = (oh-th)//2; cx = (ow-tw)//2
ours_c = ours_16[cy:cy+th, cx:cx+tw]

ours_lab = to_lab_16bit(ours_c)
dng_lab = to_lab_16bit(dng_val)
de = colour.delta_E(ours_lab, dng_lab, method='CIE 2000')

print(f'\nFull 16-bit full-res vs dng_validate:')
print(f'  mean = {de.mean():.3f}')
print(f'  P50  = {np.percentile(de, 50):.3f}')
print(f'  P75  = {np.percentile(de, 75):.3f}')
print(f'  P95  = {np.percentile(de, 95):.3f}')
print(f'  P99  = {np.percentile(de, 99):.3f}')
print(f'  max  = {de.max():.3f}')
print(f'Bucket distribution:')
for lo, hi, lbl in [(0,1,'<1'),(1,2,'1-2'),(2,3,'2-3'),(3,5,'3-5'),(5,10,'5-10'),(10,1e9,'>=10')]:
    pct = ((de.flatten() >= lo) & (de.flatten() < hi)).mean() * 100
    print(f'  {lbl}: {pct:.1f}%')

# Per-L* band
print(f'\nMean ΔE by target L* band:')
for lo, hi in [(0,10),(10,25),(25,50),(50,75),(75,90),(90,100)]:
    mask = (dng_lab[..., 0] >= lo) & (dng_lab[..., 0] < hi)
    if mask.sum() == 0: continue
    print(f'  L* {lo}-{hi}: count={mask.sum():>10,}  mean ΔE={de[mask].mean():.3f}')

# Signed Lab delta
print(f'\nSigned Lab delta (ours - dng_validate):')
print(f'  L*: {(ours_lab[..., 0] - dng_lab[..., 0]).mean():+.3f}')
print(f'  a*: {(ours_lab[..., 1] - dng_lab[..., 1]).mean():+.3f}')
print(f'  b*: {(ours_lab[..., 2] - dng_lab[..., 2]).mean():+.3f}')
