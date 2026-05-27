#!/usr/bin/env python3
"""Compare full luminance and per-channel histograms of our render vs target.
Look for distribution differences that could explain the gap."""
import sys
import warnings
from pathlib import Path
import numpy as np
warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/src")
import colour
from PIL import Image

D65_xy = np.array([0.31270, 0.32900])

def to_lab(arr):
    linear = colour.models.eotf_sRGB(arr.astype(np.float64) / 255.0)
    xyz = colour.RGB_to_XYZ(linear, "sRGB", apply_cctf_decoding=False)
    return colour.XYZ_to_Lab(xyz, illuminant=D65_xy)

ours = np.array(Image.open("/tmp/adobe_pipeline_dsc4053_downsized.jpg").convert("RGB"))
tgt = np.array(Image.open("/Volumes/SanDisk Extreme Pro 55AF Media/Projects/2026 international faire timelapse/.lrt/visual/DSC_4053.lrtpreview").convert("RGB"))

ours_lab = to_lab(ours).reshape(-1, 3)
tgt_lab = to_lab(tgt).reshape(-1, 3)

print(f"{'channel':<8} {'P5':>8} {'P25':>8} {'P50':>8} {'P75':>8} {'P95':>8} {'P99':>8}")
for ch_i, ch in enumerate(["L*", "a*", "b*"]):
    print(f"\n{ch}")
    for label, lab in [("ours", ours_lab), ("tgt ", tgt_lab)]:
        vals = lab[:, ch_i]
        p = [np.percentile(vals, q) for q in [5, 25, 50, 75, 95, 99]]
        print(f"  {label}: " + "  ".join(f"{v:+7.2f}" for v in p))
    diff = [np.percentile(ours_lab[:, ch_i], q) - np.percentile(tgt_lab[:, ch_i], q) for q in [5, 25, 50, 75, 95, 99]]
    print(f"  Δ:    " + "  ".join(f"{v:+7.2f}" for v in diff))

# Per-RGB percentile
print(f"\n{'channel':<8} {'P5':>6} {'P25':>6} {'P50':>6} {'P75':>6} {'P95':>6} {'P99':>6} {'max':>6}")
for ch_i, ch in enumerate(["R", "G", "B"]):
    print(f"\n{ch}")
    for label, arr in [("ours", ours), ("tgt", tgt)]:
        vals = arr[..., ch_i].flatten()
        p = [np.percentile(vals, q) for q in [5, 25, 50, 75, 95, 99]]
        print(f"  {label}: " + "  ".join(f"{int(v):>5}" for v in p) + f"  max={int(vals.max())}")

# Pixel counts in luminance bands.
print("\nLuminance band populations:")
print(f"{'L* band':<12} {'ours count':>12} {'tgt count':>12} {'delta':>10}")
ours_L = ours_lab[:, 0]
tgt_L = tgt_lab[:, 0]
for lo, hi in [(0, 10), (10, 25), (25, 50), (50, 75), (75, 90), (90, 95), (95, 99), (99, 101)]:
    oc = ((ours_L >= lo) & (ours_L < hi)).sum()
    tc = ((tgt_L >= lo) & (tgt_L < hi)).sum()
    print(f"  {f'{lo}-{hi}':<12} {oc:>12,} {tc:>12,} {tc - oc:>+10,}")

# How many pixels are clipped (R=255, G=255, B=255 separately)?
print(f"\nClipping in target: R=255: {(tgt[..., 0]==255).sum():,}  G=255: {(tgt[..., 1]==255).sum():,}  B=255: {(tgt[..., 2]==255).sum():,}")
print(f"Clipping in ours:   R=255: {(ours[..., 0]==255).sum():,}  G=255: {(ours[..., 1]==255).sum():,}  B=255: {(ours[..., 2]==255).sum():,}")
print(f"Total pixels: {ours.shape[0] * ours.shape[1]:,}")
