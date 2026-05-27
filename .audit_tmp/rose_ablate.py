#!/usr/bin/env python3
"""Ablation on rose to isolate where the L* +2 bias comes from."""
import sys, warnings
import numpy as np
warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/src")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/.audit_tmp")
import tifffile, rawpy, colour
from PIL import Image
import adobe_pipeline as ap
from lrt_cinema.dcp import parse_dcp

D65_xy = np.array([0.31270, 0.32900])

def to_lab(arr):
    linear = colour.models.eotf_sRGB(arr.astype(np.float64) / 255.0)
    xyz = colour.RGB_to_XYZ(linear, "sRGB", apply_cctf_decoding=False)
    return colour.XYZ_to_Lab(xyz, illuminant=D65_xy)

DCP_ADOBE = "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Adobe Standard/Nikon D750 Adobe Standard.dcp"
DNG = "/tmp/dng_out/rose.dng"
REF = tifffile.imread("/tmp/dng_out/rose_dngval_Camera_Standard.tif")
REF8 = (REF.astype(np.float32) / 65535.0 * 255).astype(np.uint8)
ref_lab = to_lab(REF8)

with rawpy.imread(DNG) as raw:
    asn = 1.0 / np.array(raw.camera_whitebalance[:3], dtype=np.float32); asn = asn / asn[1]
camera_rgb = ap.demosaic_camera_rgb(DNG)
profile = parse_dcp(DCP_ADOBE)

header = "Setup"
print(f"{header:<40} {'mean':>6} {'dL*':>6} {'da*':>6} {'db*':>6}")
print("-" * 70)
for label, apply_lt, apply_tc in [
    ("FM only (no LT, no TC, HSM on)", False, False),
    ("FM + LT only (no TC, HSM on)", True, False),
    ("FM + TC only (no LT, HSM on)", False, True),
    ("FM + LT + TC + HSM (all)", True, True),
]:
    ap.APPLY_LOOKTABLE = apply_lt
    ap.APPLY_TONECURVE = apply_tc
    pp = ap.apply_adobe_pipeline(camera_rgb, profile, asn, 5500.0)
    srgb = ap.prophoto_to_srgb(pp)
    oh, ow = srgb.shape[:2]; th, tw = REF.shape[:2]
    cy = (oh - th) // 2; cx = (ow - tw) // 2
    crop = srgb[cy:cy+th, cx:cx+tw]
    cur_lab = to_lab(crop)
    de = colour.delta_E(cur_lab, ref_lab, method="CIE 2000")
    dL = (cur_lab[..., 0] - ref_lab[..., 0]).mean()
    da = (cur_lab[..., 1] - ref_lab[..., 1]).mean()
    db = (cur_lab[..., 2] - ref_lab[..., 2]).mean()
    print(f"{label:<40} {de.mean():>6.2f} {dL:>+6.2f} {da:>+6.2f} {db:>+6.2f}")
