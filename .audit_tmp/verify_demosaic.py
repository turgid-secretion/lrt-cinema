#!/usr/bin/env python3
"""Verify whether LINEAR or AHD demosaic matches dng_validate's actual
post-demosaic stage3 output. The advisor flagged that LINEAR may be
coincidentally masking another bug rather than being Adobe's actual
internal demosaic."""
import sys, warnings
import numpy as np
warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/src")
import rawpy, tifffile

STAGE3 = '/tmp/dng_out/DSC_4053_stage3.tif'
DNG = '/tmp/dng_out/DSC_4053.dng'

# dng_validate's stage3 = post-demosaic, post-white-level-normalize image.
# It's the linear camera RGB BEFORE camera→XYZ matrix is applied.
stage3 = tifffile.imread(STAGE3)
print(f"stage3 dtype: {stage3.dtype}, shape: {stage3.shape}")
print(f"stage3 range: [{stage3.min()}, {stage3.max()}]")

if stage3.dtype == np.uint16:
    stage3_f = stage3.astype(np.float32) / 65535.0
elif stage3.dtype == np.float32:
    stage3_f = stage3.astype(np.float32)
else:
    stage3_f = stage3.astype(np.float32) / np.iinfo(stage3.dtype).max
print(f"stage3 norm range: [{stage3_f.min():.4f}, {stage3_f.max():.4f}]")

def render(algo, label):
    with rawpy.imread(DNG) as raw:
        rgb = raw.postprocess(
            output_bps=16, gamma=(1, 1), no_auto_bright=True,
            use_camera_wb=False, use_auto_wb=False,
            user_wb=[1.0, 1.0, 1.0, 1.0],
            output_color=rawpy.ColorSpace.raw,
            demosaic_algorithm=algo,
            half_size=False, four_color_rgb=False,
            highlight_mode=rawpy.HighlightMode.Clip,
        )
    ours = rgb.astype(np.float32) / 65535.0
    return ours, label

ahd, _ = render(rawpy.DemosaicAlgorithm.AHD, "AHD")
lin, _ = render(rawpy.DemosaicAlgorithm.LINEAR, "LINEAR")

print(f"\nour shape: {ahd.shape} vs stage3 {stage3_f.shape}")

# Match shapes — crop ours to stage3.
oh, ow = ahd.shape[:2]; th, tw = stage3_f.shape[:2]
cy = (oh - th) // 2; cx = (ow - tw) // 2
ahd_c = ahd[cy:cy+th, cx:cx+tw]
lin_c = lin[cy:cy+th, cx:cx+tw]

# Stage3 might be in CAMERA-NEUTRAL space (post-WB) or post-matrix.
# If stage3 is camera-RGB before WB, it's directly comparable.
# If post-WB, we need to scale by AsShotNeutral.
# Check by sampling
print(f"\nstage3 sample pixel (1000,1000): {stage3_f[1000,1000]}")
print(f"AHD     sample pixel (1000,1000): {ahd_c[1000,1000]}")
print(f"LINEAR  sample pixel (1000,1000): {lin_c[1000,1000]}")

def diff_stats(a, b, label):
    d = np.abs(a - b)
    print(f"  {label}: mean|d|={d.mean():.5f}, max|d|={d.max():.5f}, P95={np.percentile(d, 95):.5f}")

print(f"\nDiff vs stage3 (raw demosaic only — no WB, no matrix):")
diff_stats(ahd_c, stage3_f, "AHD vs stage3")
diff_stats(lin_c, stage3_f, "LINEAR vs stage3")

# Also check the per-channel diff to see if it's a global scale or per-channel.
print(f"\nPer-channel mean diff:")
for ch, name in enumerate("RGB"):
    print(f"  AHD[{name}] - stage3[{name}]: {(ahd_c[..., ch] - stage3_f[..., ch]).mean():+.5f}")
    print(f"  LIN[{name}] - stage3[{name}]: {(lin_c[..., ch] - stage3_f[..., ch]).mean():+.5f}")
