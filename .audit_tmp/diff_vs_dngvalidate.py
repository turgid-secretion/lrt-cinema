#!/usr/bin/env python3
"""Diff our pipeline vs dng_validate (Adobe's own reference). The gap
between these two MUST close to <1 ΔE since both implement the same
DNG spec. Identify which pipeline stage diverges."""
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

# Load dng_validate output (full-res 6016x4016 sRGB 16-bit).
dng_val = tifffile.imread('/tmp/dng_out/DSC_4053_dngvalidate.tif')
dng_val_8 = (dng_val.astype(np.float32) / 65535.0 * 255).astype(np.uint8)
print(f'dng_validate output: {dng_val.shape} dtype={dng_val.dtype}')

# Render via our pipeline at full-res (same shape).
profile = parse_dcp('/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Nikon D750/Nikon D750 Camera Standard.dcp')
with rawpy.imread('/tmp/v04_test_input/DSC_4053.NEF') as raw:
    asn = 1.0 / np.array(raw.camera_whitebalance[:3], dtype=np.float32); asn = asn / asn[1]
DNG = '/tmp/dng_out/DSC_4053.dng'
DCP = '/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Nikon D750/Nikon D750 Camera Standard.dcp'
camera_rgb = ap.demosaic_camera_rgb(DNG)
ap.APPLY_LOOKTABLE = True; ap.APPLY_TONECURVE = True
dng_be = ap.read_dng_baseline_exposure(DNG)
dbr = ap.read_dcp_default_black_render(DCP)
print(f'DNG.BaselineExposure = {dng_be}, DCP.DefaultBlackRender = {dbr}')
prophoto = ap.apply_adobe_pipeline(camera_rgb, profile, asn, 5500.0,
                                    dng_baseline_exposure=dng_be,
                                    default_black_render=dbr)
srgb = ap.prophoto_to_srgb(prophoto)
print(f'ours: {srgb.shape}')

# Crop ours to match dng_validate (which is 6016x4016, ours is 6032x4032).
oh, ow = srgb.shape[:2]
th, tw = dng_val.shape[:2]
cy = (oh - th) // 2; cx = (ow - tw) // 2
ours_cropped = srgb[cy:cy+th, cx:cx+tw]
print(f'ours cropped: {ours_cropped.shape}')

# Compute ΔE at full-res — no downsampling.
ours_lab = to_lab(ours_cropped)
dng_lab = to_lab(dng_val_8)
de = colour.delta_E(ours_lab, dng_lab, method='CIE 2000')
print(f'\nOurs vs dng_validate (full-res, no downsample):')
print(f'  mean={de.mean():.2f}  P50={np.percentile(de, 50):.2f}  P95={np.percentile(de, 95):.2f}  max={de.max():.2f}')
for lo, hi, lbl in [(0,1,'<1'),(1,2,'1-2'),(2,3,'2-3'),(3,5,'3-5'),(5,10,'5-10'),(10,1e9,'>=10')]:
    pct = ((de.flatten() >= lo) & (de.flatten() < hi)).mean() * 100
    print(f'    {lbl}: {pct:.1f}%')

# Per-channel Lab differences at high-ΔE pixels.
hi_mask = de > 3
print(f'\nHigh-ΔE pixels ({hi_mask.sum():,} = {100*hi_mask.mean():.1f}%):')
print(f'  mean L* ours-dngvalidate = {(ours_lab[..., 0] - dng_lab[..., 0])[hi_mask].mean():+.2f}')
print(f'  mean a* ours-dngvalidate = {(ours_lab[..., 1] - dng_lab[..., 1])[hi_mask].mean():+.2f}')
print(f'  mean b* ours-dngvalidate = {(ours_lab[..., 2] - dng_lab[..., 2])[hi_mask].mean():+.2f}')

# Average across the whole image (signed).
print(f'\nWhole image mean Lab delta (ours - dngvalidate):')
print(f'  L*: {(ours_lab[..., 0] - dng_lab[..., 0]).mean():+.2f}')
print(f'  a*: {(ours_lab[..., 1] - dng_lab[..., 1]).mean():+.2f}')
print(f'  b*: {(ours_lab[..., 2] - dng_lab[..., 2]).mean():+.2f}')

# Heatmap.
hm = np.clip(de, 0, 30) / 30 * 255
heat = np.stack([np.clip(hm * 1.0, 0, 255), np.clip(255 - hm * 0.8, 0, 255), np.clip(255 - hm, 0, 255)], axis=2).astype(np.uint8)
Image.fromarray(heat).save('/tmp/our_vs_dngvalidate_heatmap.jpg', quality=92)

# Per-luminance band ΔE.
print(f'\nΔE by target L* band:')
for lo, hi in [(0, 10), (10, 25), (25, 50), (50, 75), (75, 90), (90, 100)]:
    mask = (dng_lab[..., 0] >= lo) & (dng_lab[..., 0] < hi)
    if mask.sum() == 0: continue
    print(f'  L* {lo}-{hi}: count={mask.sum():>9,}  mean ΔE={de[mask].mean():.2f}  >5%={100*(de[mask] > 5).mean():.1f}%')

# Save side-by-side for visual.
Image.fromarray(ours_cropped).save('/tmp/ours_full.jpg', quality=92)
Image.fromarray(dng_val_8).save('/tmp/dngvalidate_full.jpg', quality=92)
print(f'\nsaved: ours, dng_validate, heatmap to /tmp/')
