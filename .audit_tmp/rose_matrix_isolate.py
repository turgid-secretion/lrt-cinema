#!/usr/bin/env python3
"""Isolate where the K-dependence comes from for rose. Render with HSM/LT/TC
all disabled at multiple kelvins — if matrix only, should be near-identical
since at K=6585 we use FM2 and at K=4500 we interpolate; the L*/a*/b* delta
tells us how much matrix interpolation matters."""
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

RAW = '/tmp/dng_out/rose.dng'
DCP = '/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Adobe Standard/Nikon D750 Adobe Standard.dcp'
REF = tifffile.imread('/tmp/dng_out/rose_dngval_Camera_Standard.tif')
ref_8 = (REF.astype(np.float32) / 65535.0 * 255).astype(np.uint8)
ref_lab = to_lab(ref_8)

profile = parse_dcp(DCP)
with rawpy.imread(RAW) as raw:
    asn = 1.0 / np.array(raw.camera_whitebalance[:3], dtype=np.float32); asn = asn / asn[1]
camera_rgb = ap.demosaic_camera_rgb(RAW)
dng_be = ap.read_dng_baseline_exposure(RAW)
dbr = ap.read_dcp_default_black_render(DCP)

def run(K, apply_lt, apply_tc, label):
    ap.APPLY_LOOKTABLE = apply_lt
    ap.APPLY_TONECURVE = apply_tc
    pp = ap.apply_adobe_pipeline(camera_rgb, profile, asn, float(K),
                                  dng_baseline_exposure=dng_be,
                                  default_black_render=dbr)
    srgb = ap.prophoto_to_srgb(pp)
    oh, ow = srgb.shape[:2]; th, tw = REF.shape[:2]
    cy = (oh - th) // 2; cx = (ow - tw) // 2
    crop = srgb[cy:cy+th, cx:cx+tw]
    cur_lab = to_lab(crop)
    de = colour.delta_E(cur_lab, ref_lab, method='CIE 2000')
    dL = (cur_lab[..., 0] - ref_lab[..., 0]).mean()
    da = (cur_lab[..., 1] - ref_lab[..., 1]).mean()
    db = (cur_lab[..., 2] - ref_lab[..., 2]).mean()
    print(f"{label:<30} K={K:>5.0f} mean={de.mean():.3f} dL={dL:+.2f} da={da:+.2f} db={db:+.2f}")

print(f"{'config':<30} {'K':>5} {'mean':>5} {'dL':>5} {'da':>5} {'db':>5}")
for K in [4500, 5500, 6585]:
    run(K, False, False, "Matrix only (HSM on)")
    run(K, True, False, "Matrix + LT (HSM on)")
    run(K, True, True, "Matrix + LT + TC (HSM on)")
    print()
