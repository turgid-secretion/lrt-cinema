#!/usr/bin/env python3
"""Test different highlight handling modes."""
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
target = np.array(Image.open(LRT).convert("RGB"))

modes = {
    "Clip": rawpy.HighlightMode.Clip,
    "Ignore": rawpy.HighlightMode.Ignore,
    "Blend": rawpy.HighlightMode.Blend,
    "ReconstructDefault": rawpy.HighlightMode.ReconstructDefault,
}

with rawpy.imread(str(NEF)) as raw:
    as_shot = np.array(raw.camera_whitebalance[:3], dtype=np.float32)
    asn = 1.0 / as_shot
    asn = asn / asn[1]

ap.APPLY_LOOKTABLE = True
ap.APPLY_TONECURVE = True

print(f"{'HighlightMode':<24} {'mean':>6} {'P50':>6} {'P95':>6} {'<2%':>6} {'>10%':>6}")
print("-" * 64)
for name, mode in modes.items():
    with rawpy.imread(str(NEF)) as raw:
        rgb = raw.postprocess(
            output_bps=16, gamma=(1, 1), no_auto_bright=True,
            use_camera_wb=False, use_auto_wb=False,
            user_wb=[1.0, 1.0, 1.0, 1.0],
            output_color=rawpy.ColorSpace.raw,
            demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
            highlight_mode=mode,
        )
    camera_rgb = rgb.astype(np.float32) / 65535.0
    prophoto = ap.apply_adobe_pipeline(camera_rgb, profile, asn, 5500.0)
    srgb = ap.prophoto_to_srgb(prophoto)
    ours_resized = np.array(Image.fromarray(srgb).resize((target.shape[1], target.shape[0]), Image.BILINEAR))
    r = ap.measure_de(ours_resized, target)
    lt2 = r["buckets"]["<1"] + r["buckets"]["1-2"]
    gt10 = r["buckets"][">=10"]
    print(f"{name:<24} {r['mean']:>6.2f} {r['P50']:>6.2f} {r['P95']:>6.2f} {lt2:>5.1f}% {gt10:>5.1f}%")
