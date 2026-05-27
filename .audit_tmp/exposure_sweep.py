#!/usr/bin/env python3
"""Sweep exposure scale + Profile look options."""
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
    asn = 1.0 / np.array(raw.camera_whitebalance[:3], dtype=np.float32)
    asn = asn / asn[1]
camera_rgb = ap.demosaic_camera_rgb(NEF)
target = np.array(Image.open(LRT).convert("RGB"))

ap.APPLY_LOOKTABLE = True
ap.APPLY_TONECURVE = True

print(f"{'EV scale':>10} {'mean':>6} {'P50':>6} {'P95':>6} {'<2%':>6} {'>5%':>6} {'>10%':>6}")
print("-" * 60)
for ev in [-0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5]:
    scale = 2.0 ** ev
    rgb_scaled = np.clip(camera_rgb * scale, 0, 1)
    prophoto = ap.apply_adobe_pipeline(rgb_scaled, profile, asn, 5500.0)
    srgb = ap.prophoto_to_srgb(prophoto)
    ours_resized = np.array(Image.fromarray(srgb).resize((target.shape[1], target.shape[0]), Image.BILINEAR))
    r = ap.measure_de(ours_resized, target)
    lt2 = r["buckets"]["<1"] + r["buckets"]["1-2"]
    gt5 = r["buckets"]["5-10"] + r["buckets"][">=10"]
    gt10 = r["buckets"][">=10"]
    print(f"{ev:>+6.2f}    {r['mean']:>6.2f} {r['P50']:>6.2f} {r['P95']:>6.2f} {lt2:>5.1f}% {gt5:>5.1f}% {gt10:>5.1f}%")
