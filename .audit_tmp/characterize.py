#!/usr/bin/env python3
"""Comprehensive pipeline-stage characterization for v0.4 vs reference.

Goal: stop guessing which stage contributes how much. Render the same
inputs through multiple variants, each isolating ONE stage, and tabulate
ΔE contribution per stage.

Scenes (both rendered for every variant):
  * gym   — DSC_4053.NEF + DSC_4053.xmp (As Shot WB, fluorescent indoor)
            Reference: LRT preview JPEG (Adobe-pipeline render at 1024×684).
  * rose  — d750_sample.NEF + synthetic neutral xmp (Auto1 WB, outdoor daylight)
            Reference: camera-embedded full-res JPEG (Nikon Picture Control Neutral).

Variants:
  V1  v0.4 baseline: DCP loaded, cube + basecurve emit, libraw as-shot WB
  V2  V1 + explicit kelvin emit (5500K for gym; what camera resolved for rose)
  V3  V1 - cube (basecurve only)
  V4  V1 - basecurve (cube only)
  V5  V1 - cube - basecurve (matrix + WB only)
  V6  V1 + colorin override to STANDARD_MATRIX (=11; confirmed dt default)

Per (scene, variant), report:
  - mean / P50 / P95 / max ΔE2000
  - pixel buckets (imperceptible / perceptible / broadcast / visible / clear / drastic)
  - affine fit: pre-fit, post-fit, per-channel gain R/G/B
  - per-channel L* P5/P50/P95 (ours vs target) → contrast-shape signature

Outputs:
  /tmp/characterize/{scene}_{variant}.tif    — rendered TIFF
  /tmp/characterize/{scene}_{variant}.report — full diagnose_vs output
  /tmp/characterize/summary.md               — attribution table

Cropping caveat: LRT preview applies lens corrections; ours doesn't.
The framing mismatch contributes some baseline ΔE we can't eliminate
without implementing lens correction (out of scope).
"""
import json
import re
import shutil
import struct
import subprocess
import sys
import warnings
from collections import OrderedDict
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/src")
sys.path.insert(0, "/Users/dylan/Documents/001_CODE/lrt-cinema/tools")

OUT_DIR = Path("/tmp/characterize")
OUT_DIR.mkdir(exist_ok=True)

DT_BIN = "darktable-cli"
LRT_BIN = [sys.executable, "-m", "lrt_cinema"]

# Scene definitions ---------------------------------------------------------
SCENES = {
    "gym": {
        "raw_src": "/Volumes/SanDisk Extreme Pro 55AF Media/Projects/2026 international faire timelapse/DSC_4053.NEF",
        "xmp_src": "/Volumes/SanDisk Extreme Pro 55AF Media/Projects/2026 international faire timelapse/DSC_4053.xmp",
        "reference": "/Volumes/SanDisk Extreme Pro 55AF Media/Projects/2026 international faire timelapse/.lrt/visual/DSC_4053.lrtpreview",
        "stem": "DSC_4053",
        "explicit_kelvin": 5500,  # camera was set to manual 5500K
    },
    "rose": {
        "raw_src": "/tmp/d750_sample.NEF",
        "xmp_src": None,  # we'll synthesize one
        "reference": "/tmp/rose_camera_jpeg.jpg",
        "stem": "rose",
        "explicit_kelvin": 5500,  # Auto1, we'll pick a daylight default
    },
}

# Neutral XMP template ------------------------------------------------------
def synth_xmp(white_balance="As Shot", temperature=None, tint=0):
    wb = f'crs:WhiteBalance="{white_balance}"'
    temp = f' crs:Temperature="{temperature}" crs:Tint="{tint}"' if temperature else ""
    return f'''<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmp:Rating="4"
    crs:CameraProfile="Camera Standard"
    {wb}{temp}
    crs:Exposure2012="0.00"
    crs:Contrast2012="0"
    crs:Blacks2012="0"
    crs:Vibrance="0"
    crs:Saturation="0"
    crs:Sharpness="25"/>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>
'''

# Variant pipeline ----------------------------------------------------------
def make_input_dir(scene_name, scene_info, variant_kwargs):
    """Set up an input dir with the RAW + an XMP shaped per variant."""
    in_dir = OUT_DIR / f"in_{scene_name}_{variant_kwargs['name']}"
    in_dir.mkdir(exist_ok=True)
    raw_dst = in_dir / Path(scene_info["raw_src"]).name
    if not raw_dst.exists():
        shutil.copy(scene_info["raw_src"], raw_dst)

    # Build the XMP:
    if scene_info["xmp_src"] and not variant_kwargs.get("force_synth"):
        xmp_text = Path(scene_info["xmp_src"]).read_text(errors="replace")
        if variant_kwargs.get("explicit_kelvin"):
            k = scene_info["explicit_kelvin"]
            xmp_text = re.sub(
                r'crs:WhiteBalance="[^"]*"',
                f'crs:WhiteBalance="Custom" crs:Temperature="{k}" crs:Tint="0"',
                xmp_text, count=1,
            )
    else:
        xmp_text = synth_xmp(
            white_balance="Custom" if variant_kwargs.get("explicit_kelvin") else "As Shot",
            temperature=scene_info["explicit_kelvin"] if variant_kwargs.get("explicit_kelvin") else None,
        )
    (in_dir / f"{raw_dst.name}.xmp").write_text(xmp_text)
    return in_dir

def render_via_lrt_cinema(in_dir, out_dir, extra_flags=None):
    out_dir.mkdir(exist_ok=True, parents=True)
    # Clear stale outputs.
    for f in out_dir.glob("*.tif"):
        f.unlink()
    for f in out_dir.glob("*.cube"):
        f.unlink()
    for f in out_dir.glob("*.dt.xmp"):
        f.unlink()
    argv = LRT_BIN + ["render",
        "--input", str(in_dir),
        "--output", str(out_dir),
        "--preset", "cinema-linear",
        "--quiet",
    ]
    if extra_flags:
        argv += extra_flags
    result = subprocess.run(argv, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        print(f"  render failed (rc={result.returncode}):")
        print("  stderr:", result.stderr[-400:])
        return None
    tifs = list(out_dir.glob("*.tif"))
    return tifs[0] if tifs else None

# Colorin-override variant (V6): bypass lrt-cinema's XMP, inject colorin entry.
def render_with_colorin_override(in_dir, out_dir, scene_stem, colorin_type=11):
    out_dir.mkdir(exist_ok=True, parents=True)
    for f in out_dir.glob("*.tif"):
        f.unlink()
    # Re-emit XMP via dry-run.
    argv_dry = LRT_BIN + ["render",
        "--input", str(in_dir),
        "--output", str(out_dir),
        "--preset", "cinema-linear",
        "--dry-run", "--quiet",
    ]
    subprocess.run(argv_dry, capture_output=True, text=True, timeout=600)
    xmp_path = next(out_dir.glob("*.dt.xmp"))

    # Encode colorin params: 1044 bytes.
    filename = b"\x00" * 512
    filename_work = b"\x00" * 512
    params = (
        struct.pack("<i", colorin_type)
        + filename
        + struct.pack("<iii", 0, 0, 0)  # intent, normalize, blue_mapping
        + struct.pack("<i", 4)          # type_work = LIN_REC2020
        + filename_work
    ).hex()

    text = xmp_path.read_text()
    nums = [int(n) for n in re.findall(r'darktable:num="(\d+)"', text)]
    next_num = max(nums) + 1 if nums else 0
    li = (
        f'<rdf:li darktable:num="{next_num}" '
        f'darktable:operation="colorin" darktable:enabled="1" '
        f'darktable:modversion="7" darktable:params="{params}" '
        f'darktable:multi_name="" darktable:multi_priority="0"/>'
    )
    text = text.replace("</rdf:Seq>", li + "</rdf:Seq>", 1)
    text = re.sub(r'darktable:history_end="\d+"', f'darktable:history_end="{next_num + 1}"', text)
    xmp_path.write_text(text)

    out_tif = out_dir / f"{scene_stem}.tif"
    raw = next(in_dir.glob("*.NEF"))
    argv = [
        DT_BIN, str(raw.resolve()), str(xmp_path.resolve()), str(out_tif.resolve()),
        "--apply-custom-presets", "0",
        "--icc-type", "LIN_REC2020", "--icc-intent", "RELATIVE_COLORIMETRIC",
        "--core",
        "--conf", "plugins/imageio/format/tiff/bpp=16",
        "--conf", "plugins/imageio/format/tiff/compress=0",
        "--conf", "plugins/imageio/format/tiff/pixelformat=0",
        "--conf", f"plugins/darkroom/lut3d/def_path={out_dir.resolve()}",
    ]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        print(f"  colorin-override render failed: {result.stderr[-400:]}")
        return None
    return out_tif

# Diagnostic ----------------------------------------------------------------
def diagnose(tif_path, reference_path, report_path):
    result = subprocess.run(
        [sys.executable, "/Users/dylan/Documents/001_CODE/lrt-cinema/tools/diagnose_vs_lrt_preview.py",
         str(tif_path), str(reference_path), str(report_path.parent / report_path.stem)],
        capture_output=True, text=True, timeout=300,
    )
    text = result.stdout + result.stderr
    report_path.write_text(text)
    return text

def parse_report(text):
    """Extract structured metrics from diagnose stdout."""
    out = {}
    # Per-pixel buckets.
    bucket_re = re.compile(r"^\s+(ΔE [^(]+?\(\w[^)]*\))\s+([0-9.]+)%", re.MULTILINE)
    out["buckets"] = {label.strip(): float(pct) for label, pct in bucket_re.findall(text)}
    # Percentiles.
    pct_re = re.compile(r"^\s+(P\d+(?:\.\d+)?)\s*:\s*([0-9.]+)", re.MULTILINE)
    out["percentiles"] = {p: float(v) for p, v in pct_re.findall(text)}
    max_match = re.search(r"^\s+max:\s+([0-9.]+)", text, re.MULTILINE)
    out["max"] = float(max_match.group(1)) if max_match else None
    # Affine fit.
    gain_match = re.search(r"gain\s+R / G / B =\s+([0-9.]+) /\s+([0-9.]+) /\s+([0-9.]+)", text)
    out["gain_rgb"] = [float(g) for g in gain_match.groups()] if gain_match else None
    pre_match = re.search(r"Pre-fit\s+mean ΔE:\s+([0-9.]+)", text)
    post_match = re.search(r"Post-fit mean ΔE:\s+([0-9.]+)", text)
    out["prefit_mean"] = float(pre_match.group(1)) if pre_match else None
    out["postfit_mean"] = float(post_match.group(1)) if post_match else None
    # Per-channel L*a*b* dist
    lab_re = re.compile(r"^\s+(L\*|a\*|b\*)\s+([+-]?[0-9.]+)/\s*([+-]?[0-9.]+)/\s*([+-]?[0-9.]+)\s+([+-]?[0-9.]+)/\s*([+-]?[0-9.]+)/\s*([+-]?[0-9.]+)", re.MULTILINE)
    out["lab_dist"] = {}
    for m in lab_re.finditer(text):
        ch, op5, op50, op95, tp5, tp50, tp95 = m.groups()
        out["lab_dist"][ch] = {
            "ours": [float(op5), float(op50), float(op95)],
            "target": [float(tp5), float(tp50), float(tp95)],
        }
    return out

# Variant table -------------------------------------------------------------
VARIANTS = OrderedDict([
    ("V1_baseline",     {"flags": [], "label": "DCP + cube + basecurve + libraw WB (default)"}),
    ("V2_kelvin",       {"flags": [], "label": "+ explicit kelvin emit (DCP-derived WB)", "explicit_kelvin": True}),
    ("V3_no_cube",      {"flags": ["--no-dcp-hsv-cubes"], "label": "DCP + basecurve only (no cube)"}),
    ("V4_no_basecurve", {"flags": ["--no-dcp-tone-curve"], "label": "DCP + cube only (no basecurve)"}),
    ("V5_matrix_only",  {"flags": ["--no-dcp-hsv-cubes", "--no-dcp-tone-curve"], "label": "matrix + libraw WB only"}),
    ("V6_colorin",      {"flags": [], "label": "V1 + colorin override to STANDARD_MATRIX", "colorin_override": 11}),
])

def main():
    rows = []  # list of dicts for summary table
    for scene_name, scene_info in SCENES.items():
        print(f"\n=== Scene: {scene_name} ===")
        for vname, vinfo in VARIANTS.items():
            print(f"  {vname}: {vinfo['label']}")
            kwargs = {"name": vname}
            if vinfo.get("explicit_kelvin"):
                kwargs["explicit_kelvin"] = True
            in_dir = make_input_dir(scene_name, scene_info, kwargs)
            out_dir = OUT_DIR / f"out_{scene_name}_{vname}"

            if vinfo.get("colorin_override"):
                tif = render_with_colorin_override(in_dir, out_dir, scene_info["stem"], vinfo["colorin_override"])
            else:
                tif = render_via_lrt_cinema(in_dir, out_dir, vinfo["flags"])

            if tif is None:
                print(f"    SKIP — render failed")
                continue

            report_path = OUT_DIR / f"{scene_name}_{vname}.report"
            diag = diagnose(tif, scene_info["reference"], report_path)
            metrics = parse_report(diag)
            row = {
                "scene": scene_name, "variant": vname, "label": vinfo["label"],
                "mean": metrics["prefit_mean"], "postfit": metrics["postfit_mean"],
                "gain_rgb": metrics["gain_rgb"], "max": metrics["max"],
                "percentiles": metrics["percentiles"],
                "buckets": metrics["buckets"],
                "lab_dist": metrics["lab_dist"],
            }
            rows.append(row)
            print(f"    mean ΔE = {row['mean']:.2f}, postfit = {row['postfit']:.2f}, gain = {row['gain_rgb']}")

    # Summary markdown.
    out_md = OUT_DIR / "summary.md"
    lines = ["# Pipeline-stage characterization\n"]
    lines.append("## Per-variant mean ΔE2000\n")
    lines.append("| Scene | Variant | Description | Mean ΔE | Post-fit | Gain R/G/B | Max |")
    lines.append("|---|---|---|---:|---:|:---:|---:|")
    for r in rows:
        gain_str = " / ".join(f"{g:.3f}" for g in r["gain_rgb"]) if r["gain_rgb"] else "—"
        lines.append(f"| {r['scene']} | {r['variant']} | {r['label']} | "
                     f"{r['mean']:.2f} | {r['postfit']:.2f} | {gain_str} | {r['max']:.1f} |")
    lines.append("")

    # Stage-contribution deltas.
    lines.append("## Stage-contribution deltas (ΔE change vs V1 baseline)\n")
    lines.append("| Scene | Variant | Δ mean from V1 | Δ postfit from V1 |")
    lines.append("|---|---|---:|---:|")
    for scene in SCENES:
        v1 = next((r for r in rows if r["scene"] == scene and r["variant"] == "V1_baseline"), None)
        if not v1:
            continue
        for r in rows:
            if r["scene"] != scene or r["variant"] == "V1_baseline":
                continue
            dmean = r["mean"] - v1["mean"]
            dpost = r["postfit"] - v1["postfit"]
            lines.append(f"| {scene} | {r['variant']} | {dmean:+.2f} | {dpost:+.2f} |")
    lines.append("")

    # Per-channel L* contrast signature.
    lines.append("## Tone-curve signature — L\\* spread (P95 − P5) per variant\n")
    lines.append("Higher P95−P5 = more contrast (wider luminance range). Target's L\\* spread is constant per scene.\n")
    lines.append("| Scene | Variant | ours L\\* spread | target L\\* spread | Δ |")
    lines.append("|---|---|---:|---:|---:|")
    for r in rows:
        if "L*" not in r["lab_dist"]:
            continue
        ours_p5, _, ours_p95 = r["lab_dist"]["L*"]["ours"]
        tgt_p5, _, tgt_p95 = r["lab_dist"]["L*"]["target"]
        ours_spread = ours_p95 - ours_p5
        tgt_spread = tgt_p95 - tgt_p5
        lines.append(f"| {r['scene']} | {r['variant']} | {ours_spread:.1f} | {tgt_spread:.1f} | {ours_spread - tgt_spread:+.1f} |")
    lines.append("")

    # Bucket dist.
    lines.append("## Pixel-bucket distribution per variant\n")
    bucket_keys_order = ["ΔE < 1", "1 ≤ ΔE < 2", "2 ≤ ΔE < 3", "3 ≤ ΔE < 5", "5 ≤ ΔE <10", "ΔE ≥ 10"]
    lines.append("| Scene | Variant | <1 | 1-2 | 2-3 | 3-5 | 5-10 | ≥10 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        cells = []
        for k in bucket_keys_order:
            # match by prefix only.
            found = None
            for bk, bv in r["buckets"].items():
                if bk.startswith(k):
                    found = bv
                    break
            cells.append(f"{found:.1f}%" if found is not None else "—")
        lines.append(f"| {r['scene']} | {r['variant']} | " + " | ".join(cells) + " |")

    out_md.write_text("\n".join(lines))
    print(f"\nsummary: {out_md}")

    # JSON for downstream analysis.
    json_path = OUT_DIR / "summary.json"
    json_path.write_text(json.dumps(rows, indent=2))
    print(f"json:    {json_path}")

if __name__ == "__main__":
    main()
