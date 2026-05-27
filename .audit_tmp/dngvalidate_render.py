#!/usr/bin/env python3
"""Render NEF via Adobe DNG Converter + dng_validate (Adobe DNG SDK 1.7.1).

This is the v0.6 architecture proposal: instead of reverse-engineering
Adobe's DCP pipeline in Python (which lands at ~1.13 ΔE for the gym scene
vs Adobe's reference), or fighting dt-cli's mid-pipeline divergence (which
lands at ~6.37 ΔE), we ship Adobe's actual reference renderer.

Output matches Adobe's spec by construction.

Stages:
  1. NEF → DNG via Adobe DNG Converter (~3s, single subprocess)
  2. DNG → TIFF via dng_validate (~8s, single subprocess)
     - Profile: configurable (Camera Standard / Camera Neutral / etc.)
     - Color space: configurable (sRGB / Adobe RGB / ProPhoto / Rec.2020)
     - Bit depth: 16
  3. (Optional, future): apply LR-authored develop ops (Exposure2012,
     Blacks2012, ToneCurvePV2012, etc.) as post-processing on the TIFF.

For the goal here: render gym + rose, measure vs dng_validate's own output
(trivially 0 ΔE — we ARE dng_validate).
"""
import subprocess
import sys
import warnings
from pathlib import Path

import numpy as np
import tifffile
import colour
from PIL import Image

warnings.filterwarnings("ignore")

DNG_CONVERTER = "/Applications/Adobe DNG Converter.app/Contents/MacOS/Adobe DNG Converter"
DNG_VALIDATE = "/tmp/dng_sdk/_build/dng_sdk/source/dng_validate"

D65_xy = np.array([0.31270, 0.32900])


def render_nef(nef_path: Path, out_dir: Path, profile_name: str = "Camera Standard",
                colorspace: str = "sRGB") -> Path:
    """Render NEF through Adobe DNG Converter + dng_validate. Returns TIFF path."""
    nef_path = Path(nef_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    dng_path = out_dir / (nef_path.stem + ".dng")
    if not dng_path.exists():
        subprocess.run([DNG_CONVERTER, "-c", "-d", str(out_dir),
                        "-o", dng_path.name, str(nef_path)],
                       capture_output=True, check=True, timeout=180)

    tif_base = out_dir / (nef_path.stem + "_render")
    cs_map = {"sRGB": "-cs1", "Adobe RGB": "-cs2", "ProPhoto RGB": "-cs3",
              "Rec.2020": "-cs2020"}
    cs_flag = cs_map.get(colorspace, "-cs1")
    result = subprocess.run([DNG_VALIDATE, "-16", cs_flag,
                             "-profile", profile_name,
                             "-tif", str(tif_base), str(dng_path)],
                            capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"dng_validate failed: {result.stderr[-500:]}")
    return Path(str(tif_base) + ".tif")


def to_lab_16bit(arr_uint16):
    linear = colour.models.eotf_sRGB(arr_uint16.astype(np.float64) / 65535.0)
    xyz = colour.RGB_to_XYZ(linear, "sRGB", apply_cctf_decoding=False)
    return colour.XYZ_to_Lab(xyz, illuminant=D65_xy)


def measure(ours_uint16, target_uint16) -> dict:
    ours_lab = to_lab_16bit(ours_uint16)
    tgt_lab = to_lab_16bit(target_uint16)
    de = colour.delta_E(ours_lab, tgt_lab, method="CIE 2000")
    return {
        "mean": float(de.mean()), "P50": float(np.percentile(de, 50)),
        "P95": float(np.percentile(de, 95)), "max": float(de.max()),
        "lt1_pct": float((de < 1).mean() * 100),
    }


if __name__ == "__main__":
    out = Path("/tmp/lrt_cinema_dngvalidate")
    print("=== gym (DSC_4053) ===")
    gym_tif = render_nef("/tmp/v04_test_input/DSC_4053.NEF", out, "Camera Standard")
    print(f"  rendered: {gym_tif}")
    gym_ref = tifffile.imread("/tmp/dng_out/DSC_4053_dngvalidate.tif")
    gym_ours = tifffile.imread(gym_tif)
    # Crop ours to match (dng_validate output dims may differ slightly between runs)
    oh, ow = gym_ours.shape[:2]; th, tw = gym_ref.shape[:2]
    cy = max(0, (oh-th)//2); cx = max(0, (ow-tw)//2)
    gym_ours_c = gym_ours[cy:cy+th, cx:cx+tw]
    if gym_ours_c.shape != gym_ref.shape:
        # Reverse: crop ref to match ours
        oh, ow = gym_ref.shape[:2]; th, tw = gym_ours.shape[:2]
        cy = max(0, (oh-th)//2); cx = max(0, (ow-tw)//2)
        gym_ref_c = gym_ref[cy:cy+th, cx:cx+tw]
        r = measure(gym_ours[:th, :tw], gym_ref_c)
    else:
        r = measure(gym_ours_c, gym_ref)
    print(f"  vs dng_validate reference: mean={r['mean']:.3f} P50={r['P50']:.3f} <1%={r['lt1_pct']:.1f}%")

    print()
    print("=== rose (d750_sample) ===")
    rose_tif = render_nef("/tmp/d750_sample.NEF", out, "Camera Standard")
    print(f"  rendered: {rose_tif}")
    rose_ref = tifffile.imread("/tmp/dng_out/rose_dngval_Camera_Standard.tif")
    rose_ours = tifffile.imread(rose_tif)
    oh, ow = rose_ours.shape[:2]; th, tw = rose_ref.shape[:2]
    cy = max(0, (oh-th)//2); cx = max(0, (ow-tw)//2)
    if rose_ours.shape != rose_ref.shape:
        # Same shape attempt or crop one to other
        min_h = min(oh, th); min_w = min(ow, tw)
        r = measure(rose_ours[:min_h, :min_w], rose_ref[:min_h, :min_w])
    else:
        r = measure(rose_ours, rose_ref)
    print(f"  vs dng_validate reference: mean={r['mean']:.3f} P50={r['P50']:.3f} <1%={r['lt1_pct']:.1f}%")
