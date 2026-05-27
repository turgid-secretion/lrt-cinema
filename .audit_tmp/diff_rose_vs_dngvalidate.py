#!/usr/bin/env python3
"""Diff our pipeline vs dng_validate (Adobe reference) for the rose scene.
Same logic as diff_vs_dngvalidate.py but for rose."""
import sys, warnings
from pathlib import Path
import numpy as np
warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/.audit_tmp")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/src")
import rawpy
import tifffile
import colour
from PIL import Image
import adobe_pipeline as ap
from lrt_cinema.dcp import parse_dcp

D65_xy = np.array([0.31270, 0.32900])

def to_lab(arr):
    linear = colour.models.eotf_sRGB(arr.astype(np.float64) / 255.0)
    xyz = colour.RGB_to_XYZ(linear, "sRGB", apply_cctf_decoding=False)
    return colour.XYZ_to_Lab(xyz, illuminant=D65_xy)

RAW = '/tmp/dng_out/rose.dng'  # use DNG for LinearizationTable + WhiteLevel
# Rose DNG has Adobe Standard EMBEDDED — dng_validate uses embedded profile
# regardless of -profile flag, so we must use the matching system DCP.
DCP = '/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Adobe Standard/Nikon D750 Adobe Standard.dcp'
REF = '/tmp/dng_out/rose_dngval_Camera_Standard.tif'  # actually rendered with Adobe Std

dng_val = tifffile.imread(REF)
dng_val_8 = (dng_val.astype(np.float32) / 65535.0 * 255).astype(np.uint8)
print(f'dng_validate output: {dng_val.shape} dtype={dng_val.dtype}')

profile = parse_dcp(DCP)
with rawpy.imread(RAW) as raw:
    asn = 1.0 / np.array(raw.camera_whitebalance[:3], dtype=np.float32); asn = asn / asn[1]
camera_rgb = ap.demosaic_camera_rgb(RAW)
dng_be = ap.read_dng_baseline_exposure(RAW)
print(f'DNG.BaselineExposure = {dng_be}')
ap.APPLY_LOOKTABLE = True; ap.APPLY_TONECURVE = True
dbr = ap.read_dcp_default_black_render(DCP)
print(f'DCP.DefaultBlackRender = {dbr}')
prophoto = ap.apply_adobe_pipeline(camera_rgb, profile, asn, 5500.0,
                                    dng_baseline_exposure=dng_be,
                                    default_black_render=dbr)
srgb = ap.prophoto_to_srgb(prophoto)
print(f'ours: {srgb.shape}')

oh, ow = srgb.shape[:2]
th, tw = dng_val.shape[:2]
cy = (oh - th) // 2; cx = (ow - tw) // 2
ours_cropped = srgb[cy:cy+th, cx:cx+tw]

ours_lab = to_lab(ours_cropped)
dng_lab = to_lab(dng_val_8)
de = colour.delta_E(ours_lab, dng_lab, method='CIE 2000')
print(f'\nOurs vs dng_validate (rose, full-res):')
print(f'  mean={de.mean():.2f}  P50={np.percentile(de, 50):.2f}  P95={np.percentile(de, 95):.2f}  max={de.max():.2f}')
for lo, hi, lbl in [(0,1,'<1'),(1,2,'1-2'),(2,3,'2-3'),(3,5,'3-5'),(5,10,'5-10'),(10,1e9,'>=10')]:
    pct = ((de.flatten() >= lo) & (de.flatten() < hi)).mean() * 100
    print(f'    {lbl}: {pct:.1f}%')

print(f'\nWhole image mean Lab delta (ours - dngvalidate):')
print(f'  L*: {(ours_lab[..., 0] - dng_lab[..., 0]).mean():+.2f}')
print(f'  a*: {(ours_lab[..., 1] - dng_lab[..., 1]).mean():+.2f}')
print(f'  b*: {(ours_lab[..., 2] - dng_lab[..., 2]).mean():+.2f}')

print(f'\nΔE by target L* band:')
for lo, hi in [(0, 10), (10, 25), (25, 50), (50, 75), (75, 90), (90, 100)]:
    mask = (dng_lab[..., 0] >= lo) & (dng_lab[..., 0] < hi)
    if mask.sum() == 0: continue
    print(f'  L* {lo}-{hi}: count={mask.sum():>9,}  mean ΔE={de[mask].mean():.2f}  >5%={100*(de[mask] > 5).mean():.1f}%')

Image.fromarray(ours_cropped).save('/tmp/ours_rose_full.jpg', quality=92)
Image.fromarray(dng_val_8).save('/tmp/dngvalidate_rose_full.jpg', quality=92)
print(f'\nsaved: ours_rose, dngvalidate_rose to /tmp/')
