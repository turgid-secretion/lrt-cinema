#!/usr/bin/env python3
"""Test whether a global blue-channel scale closes the gap.
If yes → the gap is global cast (some pipeline-stage mismatch we haven't isolated).
If no → it's spatial/local (cube application differences)."""
import sys, warnings
from pathlib import Path
import numpy as np
warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/.audit_tmp")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/src")
import colour
from PIL import Image
import adobe_pipeline as ap

LRT = "/Volumes/SanDisk Extreme Pro 55AF Media/Projects/2026 international faire timelapse/.lrt/visual/DSC_4053.lrtpreview"
OURS = "/tmp/adobe_pipeline_dsc4053_downsized.jpg"

target = np.array(Image.open(LRT).convert("RGB")).astype(np.float64) / 255.0
ours = np.array(Image.open(OURS).convert("RGB")).astype(np.float64) / 255.0

# Work in linear sRGB so scaling is meaningful.
tgt_linear = colour.models.eotf_sRGB(target)
ours_linear = colour.models.eotf_sRGB(ours)

print(f"{'R scale':>8} {'G scale':>8} {'B scale':>8} {'mean ΔE':>9} {'P50':>6} {'P95':>6} {'<2%':>6}")
print("-" * 60)
for r_s, g_s, b_s in [
    (1.0, 1.0, 1.0),  # baseline
    (1.0, 1.0, 0.95),
    (1.0, 1.0, 0.90),
    (1.0, 1.0, 0.85),
    (1.0, 1.0, 0.80),
    (1.0, 1.0, 0.75),
    (1.0, 1.0, 0.70),
    (1.0, 1.0, 0.60),
    (1.05, 1.0, 0.80),
    (1.0, 1.05, 0.80),
    (1.05, 1.05, 0.80),
]:
    adj_linear = np.clip(ours_linear * np.array([r_s, g_s, b_s])[None, None, :], 0, 1)
    adj_srgb_u8 = (colour.models.eotf_inverse_sRGB(adj_linear) * 255).astype(np.uint8)
    r = ap.measure_de(adj_srgb_u8, (target * 255).astype(np.uint8))
    lt2 = r["buckets"]["<1"] + r["buckets"]["1-2"]
    print(f"{r_s:>8.2f} {g_s:>8.2f} {b_s:>8.2f} {r['mean']:>9.2f} {r['P50']:>6.2f} {r['P95']:>6.2f} {lt2:>5.1f}%")
