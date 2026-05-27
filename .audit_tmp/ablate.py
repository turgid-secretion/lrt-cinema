#!/usr/bin/env python3
"""Ablation: turn each pipeline stage on/off, report ΔE on DSC_4053."""
import sys
import warnings
from pathlib import Path

import numpy as np
warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/src")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/.audit_tmp")

import rawpy
import colour
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

variants = [
    ("FM + LT + TC",  True,  True),
    ("FM + LT (no TC)", True,  False),
    ("FM + TC (no LT)", False, True),
    ("FM only (no LT, no TC)", False, False),
]

print(f"{'Variant':<28} {'mean':>6} {'P50':>6} {'P95':>6} {'<2%':>6} {'>5%':>6}")
print("-" * 60)
for name, apply_lt, apply_tc in variants:
    ap.APPLY_LOOKTABLE = apply_lt
    ap.APPLY_TONECURVE = apply_tc
    prophoto = ap.apply_adobe_pipeline(camera_rgb, profile, asn, 5500.0)
    srgb = ap.prophoto_to_srgb(prophoto)
    ours_resized = np.array(Image.fromarray(srgb).resize((target.shape[1], target.shape[0]), Image.BILINEAR))
    r = ap.measure_de(ours_resized, target)
    lt2 = r["buckets"]["<1"] + r["buckets"]["1-2"]
    gt5 = r["buckets"]["5-10"] + r["buckets"][">=10"]
    print(f"{name:<28} {r['mean']:>6.2f} {r['P50']:>6.2f} {r['P95']:>6.2f} {lt2:>5.1f}% {gt5:>5.1f}%")
