#!/usr/bin/env python3
"""Sweep AsShotNeutral source - camera vs daylight vs sweep."""
import sys, warnings
from pathlib import Path
import numpy as np
warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/.audit_tmp")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/src")
import rawpy
from PIL import Image
import adobe_pipeline as ap
from lrt_cinema.dcp import parse_dcp

NEF = Path("/tmp/v04_test_input/DSC_4053.NEF")
DCP = Path("/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Nikon D750/Nikon D750 Camera Standard.dcp")
LRT = Path("/Volumes/SanDisk Extreme Pro 55AF Media/Projects/2026 international faire timelapse/.lrt/visual/DSC_4053.lrtpreview")

profile = parse_dcp(DCP)
camera_rgb = ap.demosaic_camera_rgb(NEF)
target = np.array(Image.open(LRT).convert("RGB"))

with rawpy.imread(str(NEF)) as raw:
    cam_wb = np.array(raw.camera_whitebalance[:3])
    day_wb = np.array(raw.daylight_whitebalance[:3])

ap.APPLY_LOOKTABLE = True
ap.APPLY_TONECURVE = True

print(f"camera_wb mults: {cam_wb}")
print(f"daylight_wb mults: {day_wb}")
print()
print(f"{'name':<28} {'asn (R, G, B)':<32} {'mean':>6} {'P50':>6} {'P95':>6} {'<2%':>6}")
print("-" * 90)

candidates = [
    ("camera_wb (default)", 1.0/cam_wb / (1.0/cam_wb)[1]),
    ("daylight_wb", 1.0/day_wb / (1.0/day_wb)[1]),
]

# Also: try varying B multiplier between camera and daylight ranges.
for b_mul in [1.0, 1.1, 1.143, 1.15, 1.2, 1.25, 1.289]:
    cm = np.array([2.0, 1.0, b_mul])  # vary B only
    asn = 1.0/cm / (1.0/cm)[1]
    candidates.append((f"B_mul={b_mul}", asn))

# And varying R mul
for r_mul in [1.8, 1.9, 2.0, 2.05, 2.1, 2.15]:
    cm = np.array([r_mul, 1.0, 1.289])
    asn = 1.0/cm / (1.0/cm)[1]
    candidates.append((f"R_mul={r_mul}", asn))

# Joint
for r_mul, b_mul in [(2.0, 1.143), (2.04, 1.143), (1.9, 1.143), (1.95, 1.15)]:
    cm = np.array([r_mul, 1.0, b_mul])
    asn = 1.0/cm / (1.0/cm)[1]
    candidates.append((f"R={r_mul} B={b_mul}", asn))

for name, asn in candidates:
    prophoto = ap.apply_adobe_pipeline(camera_rgb, profile, asn.astype(np.float32), 5500.0)
    srgb = ap.prophoto_to_srgb(prophoto)
    ours_resized = np.array(Image.fromarray(srgb).resize((target.shape[1], target.shape[0]), Image.BILINEAR))
    r = ap.measure_de(ours_resized, target)
    lt2 = r["buckets"]["<1"] + r["buckets"]["1-2"]
    asn_str = f"[{asn[0]:.3f}, {asn[1]:.3f}, {asn[2]:.3f}]"
    print(f"{name:<28} {asn_str:<32} {r['mean']:>6.2f} {r['P50']:>6.2f} {r['P95']:>6.2f} {lt2:>5.1f}%")
