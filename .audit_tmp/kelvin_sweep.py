#!/usr/bin/env python3
"""Sweep scene kelvin to see which value minimizes ΔE vs LRT preview."""
import sys
import warnings
from pathlib import Path

import numpy as np
warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/src")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/.audit_tmp")

import rawpy
from PIL import Image

import adobe_pipeline as ap
from lrt_cinema.dcp import parse_dcp

NEF = Path("/tmp/v04_test_input/DSC_4053.NEF")
DCP = Path("/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Nikon D750/Nikon D750 Camera Standard.dcp")
LRT = Path("/Volumes/SanDisk Extreme Pro 55AF Media/Projects/2026 international faire timelapse/.lrt/visual/DSC_4053.lrtpreview")

profile = parse_dcp(DCP)
with rawpy.imread(str(NEF)) as raw:
    as_shot = np.array(raw.camera_whitebalance[:3], dtype=np.float32)
    asn = 1.0 / as_shot
    asn = asn / asn[1]

camera_rgb = ap.demosaic_camera_rgb(NEF)
target = np.array(Image.open(LRT).convert("RGB"))

ap.APPLY_LOOKTABLE = True
ap.APPLY_TONECURVE = True

print(f"{'Kelvin':>8} {'mean':>6} {'P50':>6} {'P95':>6} {'<2%':>6}")
print("-" * 40)
for k in [3000, 3500, 4000, 4500, 5000, 5200, 5500, 5800, 6000, 6500]:
    prophoto = ap.apply_adobe_pipeline(camera_rgb, profile, asn, float(k))
    srgb = ap.prophoto_to_srgb(prophoto)
    ours_resized = np.array(Image.fromarray(srgb).resize((target.shape[1], target.shape[0]), Image.BILINEAR))
    r = ap.measure_de(ours_resized, target)
    lt2 = r["buckets"]["<1"] + r["buckets"]["1-2"]
    print(f"{k:>8} {r['mean']:>6.2f} {r['P50']:>6.2f} {r['P95']:>6.2f} {lt2:>5.1f}%")
