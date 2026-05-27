#!/usr/bin/env python3
"""Spatial diagnostic of where the ΔE is concentrated."""
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
tgt  = np.array(Image.open("/Volumes/SanDisk Extreme Pro 55AF Media/Projects/2026 international faire timelapse/.lrt/visual/DSC_4053.lrtpreview").convert("RGB"))

ours_lab = to_lab(ours)
tgt_lab = to_lab(tgt)
de = colour.delta_E(ours_lab, tgt_lab, method="CIE 2000")
h, w = de.shape

# Mask high-ΔE pixels.
hi_mask = de > 5.0
print(f"high-ΔE pixels (>5): {hi_mask.sum():,} ({100*hi_mask.mean():.1f}%)")

# Where are they spatially? Split image into 4×6 grid of 24 cells; report mean ΔE per cell.
cell_h = h // 4
cell_w = w // 6
print()
print("Spatial ΔE map (4×6 grid, mean ΔE per cell):")
print("     " + " ".join(f"col{c}".rjust(6) for c in range(6)))
for r in range(4):
    row = [f"row{r}"]
    for c in range(6):
        y0, y1 = r*cell_h, (r+1)*cell_h
        x0, x1 = c*cell_w, (c+1)*cell_w
        row.append(f"{de[y0:y1, x0:x1].mean():6.2f}")
    print("     " + " ".join(row))

print()
print("Spatial high-ΔE-pixel-fraction map (% pixels > 5 ΔE per cell):")
print("     " + " ".join(f"col{c}".rjust(6) for c in range(6)))
for r in range(4):
    row = [f"row{r}"]
    for c in range(6):
        y0, y1 = r*cell_h, (r+1)*cell_h
        x0, x1 = c*cell_w, (c+1)*cell_w
        row.append(f"{100*(de[y0:y1, x0:x1] > 5).mean():5.1f}%")
    print("     " + " ".join(row))

# Spatial heatmap.
hm = np.clip(de, 0, 30) / 30 * 255
heat = np.stack([
    np.clip(hm * 1.0, 0, 255),
    np.clip(255 - hm * 0.8, 0, 255),
    np.clip(255 - hm, 0, 255),
], axis=2).astype(np.uint8)
Image.fromarray(heat).save("/tmp/adobe_de_heatmap.jpg", quality=92)
print(f"\nheatmap: /tmp/adobe_de_heatmap.jpg")

# What's the relationship between source luminance and ΔE?
# (Are bad pixels in shadows? highlights? mid-tones?)
ours_L = ours_lab[..., 0]
tgt_L = tgt_lab[..., 0]
print()
print("ΔE binned by target L* (luminance band):")
print(f"  {'L* band':<15} {'count':>10} {'mean ΔE':>10} {'% > 5 ΔE':>10}")
for lo, hi in [(0, 10), (10, 25), (25, 50), (50, 75), (75, 90), (90, 100)]:
    mask = (tgt_L >= lo) & (tgt_L < hi)
    if mask.sum() == 0:
        continue
    print(f"  {f'{lo}-{hi}':<15} {mask.sum():>10,} {de[mask].mean():>10.2f} {100*(de[mask] > 5).mean():>9.1f}%")

# What's the per-channel cast on high-ΔE pixels?
print()
print("Per-channel L*a*b* of HIGH-ΔE pixels vs LOW-ΔE pixels:")
print(f"{'group':<12} {'L* ours-tgt':>15} {'a* ours-tgt':>15} {'b* ours-tgt':>15}")
for label, mask in [("low (<2)", de < 2), ("high (>5)", de > 5)]:
    if mask.sum() == 0:
        continue
    dL = (ours_lab[..., 0] - tgt_lab[..., 0])[mask].mean()
    da = (ours_lab[..., 1] - tgt_lab[..., 1])[mask].mean()
    db = (ours_lab[..., 2] - tgt_lab[..., 2])[mask].mean()
    print(f"{label:<12} {dL:>+15.2f} {da:>+15.2f} {db:>+15.2f}")
