#!/usr/bin/env python3
"""Extract the transfer function: dng_validate stage3 (linear ProPhoto) → final (sRGB).
If it's purely sRGB encoding + colorspace conversion, we already do that.
If there's additional tone shaping, we'll see it as a non-monotone or non-power curve."""
import sys, warnings
from pathlib import Path
import numpy as np
warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/.audit_tmp")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/src")
import tifffile
import colour
from PIL import Image
import adobe_pipeline as ap

stage3 = tifffile.imread('/tmp/dng_out/DSC_4053_stage3.tif')   # 4032x6032 uint16
final  = tifffile.imread('/tmp/dng_out/DSC_4053_dngvalidate.tif')  # 4016x6016 uint16
print(f'stage3: {stage3.shape}, final: {final.shape}')

# Crop stage3 to match final.
oh, ow = stage3.shape[:2]; th, tw = final.shape[:2]
cy = (oh - th) // 2; cx = (ow - tw) // 2
stage3_c = stage3[cy:cy+th, cx:cx+tw]
print(f'stage3 cropped: {stage3_c.shape}')

# Convert both to float [0, 1].
s3_f = stage3_c.astype(np.float64) / 65535.0
fn_f = final.astype(np.float64) / 65535.0

# Reshape, sample.
N = 100000
rng = np.random.default_rng(0)
flat_s3 = s3_f.reshape(-1, 3)
flat_fn = fn_f.reshape(-1, 3)
idx = rng.choice(flat_s3.shape[0], N, replace=False)
sampled_s3 = flat_s3[idx]
sampled_fn = flat_fn[idx]

# Per-channel sorted scatter: plot stage3_channel vs final_channel.
print('\nPer-channel transfer (stage3 → final) at percentiles:')
print(f'{"input":>6} {"R_in→R_out":>16} {"G_in→G_out":>16} {"B_in→B_out":>16}')
for p in [1, 5, 10, 25, 50, 75, 90, 95, 99]:
    # Sort each channel by input value, take p-th percentile of output
    rin, gin, bin_ = np.percentile(sampled_s3[:, 0], p), np.percentile(sampled_s3[:, 1], p), np.percentile(sampled_s3[:, 2], p)
    # Find pixels near the p-th input percentile and report their output.
    def find_out(ch):
        target_in = np.percentile(sampled_s3[:, ch], p)
        # pixels within ±0.5% of target_in
        mask = np.abs(sampled_s3[:, ch] - target_in) < 0.005
        if mask.sum() < 10:
            mask = np.abs(sampled_s3[:, ch] - target_in) < 0.02
        return target_in, np.median(sampled_fn[mask, ch]) if mask.sum() else float('nan')
    rin_v, rout = find_out(0)
    gin_v, gout = find_out(1)
    bin_v, bout = find_out(2)
    print(f'P{p:<5} {rin_v:.3f}→{rout:.3f}    {gin_v:.3f}→{gout:.3f}    {bin_v:.3f}→{bout:.3f}')

# What would our prophoto_to_srgb do to these values?
# (Treat stage3 as linear ProPhoto; convert to sRGB our way; see what output that produces)
ours_encoded = ap.prophoto_to_srgb(s3_f.astype(np.float32))
ours_encoded_f = ours_encoded.astype(np.float64) / 255.0

print('\nWhat OUR prophoto_to_srgb produces from stage3:')
flat_ours = ours_encoded_f.reshape(-1, 3)[idx]
for p in [1, 5, 25, 50, 75, 95, 99]:
    rin = np.percentile(sampled_s3[:, 0], p)
    target_in = rin
    mask = np.abs(sampled_s3[:, 0] - target_in) < 0.005
    if mask.sum() < 10: mask = np.abs(sampled_s3[:, 0] - target_in) < 0.02
    if mask.sum():
        ours_out = np.median(flat_ours[mask, 0])
        adobe_out = np.median(sampled_fn[mask, 0])
        print(f'  P{p:<3} R: in={rin:.3f}, ours_out={ours_out:.3f}, adobe_out={adobe_out:.3f}, delta={adobe_out-ours_out:+.3f}')
