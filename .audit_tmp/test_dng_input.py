#!/usr/bin/env python3
"""Test whether rawpy/libraw applies DNG-specific corrections (LinearizationTable,
WhiteLevel=15520) when given the DNG vs the NEF. If yes, switching to DNG-as-input
gets us LinearizationTable for free. If no, fall back to manual tag-50712 LUT.

Render rose via both NEF and DNG paths with identical settings.  Diff in linear
balanced camera RGB space (before any DCP / color processing)."""
import sys, warnings
import numpy as np
warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/src")
import rawpy

NEF = '/tmp/d750_sample.NEF'
DNG = '/tmp/dng_out/rose.dng'

print("Testing rawpy on rose NEF vs DNG (identical postprocess args)...")
def load(path):
    with rawpy.imread(path) as raw:
        rgb = raw.postprocess(
            output_bps=16, gamma=(1, 1), no_auto_bright=True,
            use_camera_wb=False, use_auto_wb=False,
            user_wb=[1.0, 1.0, 1.0, 1.0],
            output_color=rawpy.ColorSpace.raw,
            demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
            half_size=False, four_color_rgb=False,
            highlight_mode=rawpy.HighlightMode.Clip,
        )
        as_shot = np.array(raw.camera_whitebalance[:3], dtype=np.float32)
        white = raw.white_level if hasattr(raw, 'white_level') else 'unknown'
        cam_wl = list(raw.camera_white_level_per_channel) if hasattr(raw, 'camera_white_level_per_channel') else 'unknown'
        black = list(raw.black_level_per_channel) if hasattr(raw, 'black_level_per_channel') else 'unknown'
        return rgb.astype(np.float32) / 65535.0, as_shot, white, cam_wl, black

nef_rgb, nef_asn, nef_wl, nef_cwl, nef_bl = load(NEF)
dng_rgb, dng_asn, dng_wl, dng_cwl, dng_bl = load(DNG)

print(f"\nNEF: white_level={nef_wl}, cam_wl_per_ch={nef_cwl}, black_lvl_per_ch={nef_bl}, asn={nef_asn.tolist()}")
print(f"DNG: white_level={dng_wl}, cam_wl_per_ch={dng_cwl}, black_lvl_per_ch={dng_bl}, asn={dng_asn.tolist()}")
print(f"NEF shape: {nef_rgb.shape}, range [{nef_rgb.min():.4f}, {nef_rgb.max():.4f}]")
print(f"DNG shape: {dng_rgb.shape}, range [{dng_rgb.min():.4f}, {dng_rgb.max():.4f}]")

if nef_rgb.shape == dng_rgb.shape:
    diff = nef_rgb - dng_rgb
    print(f"\nDiff (NEF - DNG) in linear cam RGB:")
    print(f"  mean: {diff.mean():+.5f}")
    print(f"  max abs: {np.abs(diff).max():.5f}")
    print(f"  fraction |diff| > 0.001: {(np.abs(diff) > 0.001).mean()*100:.1f}%")
    print(f"  per-channel mean diff: R={diff[..., 0].mean():+.5f}, G={diff[..., 1].mean():+.5f}, B={diff[..., 2].mean():+.5f}")
    # Sample 5 random pixels
    np.random.seed(42)
    ys = np.random.randint(0, nef_rgb.shape[0], 5)
    xs = np.random.randint(0, nef_rgb.shape[1], 5)
    print(f"\nSample pixels (NEF vs DNG, ratio):")
    for y, x in zip(ys, xs):
        nef_p = nef_rgb[y, x]
        dng_p = dng_rgb[y, x]
        ratio = nef_p / np.where(dng_p > 1e-6, dng_p, 1.0)
        print(f"  ({y:>5},{x:>5}): NEF={nef_p.tolist()} DNG={dng_p.tolist()} ratio={ratio.tolist()}")
else:
    print(f"shape mismatch — can't diff directly")
