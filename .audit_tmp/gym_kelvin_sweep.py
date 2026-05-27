#!/usr/bin/env python3
"""Sweep scene kelvin for gym to find best ΔE vs dng_validate."""
import sys, warnings
import numpy as np
warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/src")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/.audit_tmp")
import rawpy, tifffile, colour
import adobe_pipeline as ap
from lrt_cinema.dcp import parse_dcp

D65_xy = np.array([0.31270, 0.32900])

def to_lab(arr):
    linear = colour.models.eotf_sRGB(arr.astype(np.float64) / 255.0)
    xyz = colour.RGB_to_XYZ(linear, "sRGB", apply_cctf_decoding=False)
    return colour.XYZ_to_Lab(xyz, illuminant=D65_xy)

RAW = '/tmp/dng_out/DSC_4053.dng'
DCP = '/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Nikon D750/Nikon D750 Camera Standard.dcp'
REF = '/tmp/dng_out/DSC_4053_dngvalidate.tif'

dng_val = tifffile.imread(REF)
dng_val_8 = (dng_val.astype(np.float32) / 65535.0 * 255).astype(np.uint8)
dng_lab = to_lab(dng_val_8)

profile = parse_dcp(DCP)
with rawpy.imread(RAW) as raw:
    asn = 1.0 / np.array(raw.camera_whitebalance[:3], dtype=np.float32); asn = asn / asn[1]
camera_rgb = ap.demosaic_camera_rgb(RAW)
dng_be = ap.read_dng_baseline_exposure(RAW)
dbr = ap.read_dcp_default_black_render(DCP)

ap.APPLY_LOOKTABLE = True; ap.APPLY_TONECURVE = True

print(f"{'Kelvin':>8} {'mean':>6} {'P50':>6} {'dL*':>6} {'da*':>6} {'db*':>6}")
print("-" * 55)
for k in [2856, 3500, 4000, 4500, 5000, 5500, 6500, 7000]:
    prophoto = ap.apply_adobe_pipeline(camera_rgb, profile, asn, float(k),
                                       dng_baseline_exposure=dng_be,
                                       default_black_render=dbr)
    srgb = ap.prophoto_to_srgb(prophoto)
    oh, ow = srgb.shape[:2]
    th, tw = dng_val.shape[:2]
    cy = (oh - th) // 2; cx = (ow - tw) // 2
    cropped = srgb[cy:cy+th, cx:cx+tw]
    ours_lab = to_lab(cropped)
    de = colour.delta_E(ours_lab, dng_lab, method='CIE 2000')
    dL = (ours_lab[..., 0] - dng_lab[..., 0]).mean()
    da = (ours_lab[..., 1] - dng_lab[..., 1]).mean()
    db = (ours_lab[..., 2] - dng_lab[..., 2]).mean()
    print(f"{k:>8} {de.mean():>6.2f} {np.percentile(de, 50):>6.2f} {dL:>+6.2f} {da:>+6.2f} {db:>+6.2f}")
