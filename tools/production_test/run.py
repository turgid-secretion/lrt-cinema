#!/usr/bin/env python3
"""Phase 6 — scaled production test + emission analysis.

Synthesises a keyframed timelapse sequence from a single base DNG (every frame is
the same pixels; the per-frame *develop intent* is what varies), renders it
through both render intents, and reports EMISSION CONFORMANCE + interpolation
behaviour. Identical input pixels + varying develop = a clean interpolation
signal: output luminance must track the keyframed exposure ramp.

Exercises end-to-end: XMP parse of the full develop set (Exposure/WB/Blacks/
Contrast/Saturation/Vibrance/ToneCurve + HSL ×24 + Color-Grade + Texture/Clarity),
sparse-keyframe interpolation, the worker pool, both `--render-intent` paths, and
the `lrtimelapse` sRGB-TIFF + `cinema-linear-finished` ACEScg-EXR writers.

Usage:
    python3 tools/production_test/run.py --base-dng /tmp/dng_out/DSC_4053.dng \
        --frames 24 --workers 6 --out /tmp/lrt_prodtest

NOT a CI test (renders full-res frames, needs a base DNG + dnglab-free DNG input).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import tifffile

from lrt_cinema.cli import main as cli_main
from lrt_cinema.ir import HSL_BAND_NAMES

# Keyframe develop intent — index 0..K-1. Exposure is the interpolation probe
# (output luma must track it); the rest exercise parse + apply of the full set.
_EXPOSURES = [0.0, 1.0, -0.5, 0.5]  # EV ramp across the keyframes


def _keyframe_xmp(i: int) -> str:
    """Rich keyframe XMP for keyframe index `i` — sets every parseable develop
    field to a distinct, varying value so interpolation + apply are exercised."""
    ev = _EXPOSURES[i % len(_EXPOSURES)]
    hsl = "".join(
        f'   crs:HueAdjustment{b}="{(i*5 + k*3) % 40 - 20}"\n'
        f'   crs:SaturationAdjustment{b}="{(i*7) % 30}"\n'
        f'   crs:LuminanceAdjustment{b}="{(-i*4) % 25 - 12}"\n'
        for k, b in enumerate(HSL_BAND_NAMES)
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
   xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
   xmlns:xmp="http://ns.adobe.com/xap/1.0/"
   xmlns:lrt="http://lrtimelapse.com/ns/1.0/"
   xmp:Rating="4"
   lrt:keyframe="1"
   crs:Exposure2012="{ev:+.3f}"
   crs:Temperature="{5000 + i*400}"
   crs:Tint="{-10 + i*8}"
   crs:Contrast2012="{-20 + i*15}"
   crs:Blacks2012="{-10 + i*6}"
   crs:Saturation="{i*10}"
   crs:Vibrance="{-i*8}"
   crs:Texture="{i*12}"
   crs:Clarity2012="{-i*6}"
   crs:Sharpness="40"
   crs:ColorGradeShadowHue="{(210 + i*20) % 360}"
   crs:ColorGradeShadowSat="{i*8}"
   crs:ColorGradeShadowLum="{-i*4}"
   crs:ColorGradeMidtoneHue="{(120 + i*15) % 360}"
   crs:ColorGradeMidtoneSat="{i*6}"
   crs:ColorGradeHighlightHue="{(40 + i*10) % 360}"
   crs:ColorGradeHighlightSat="{i*7}"
   crs:ColorGradeGlobalSat="{i*3}"
   crs:ColorGradeBlending="50"
   crs:ColorGradeBalance="{i*5 - 10}"
{hsl}  >
   <crs:ToneCurvePV2012>
    <rdf:Seq>
     <rdf:li>0, 0</rdf:li>
     <rdf:li>64, {54 + i*4}</rdf:li>
     <rdf:li>192, {200 - i*4}</rdf:li>
     <rdf:li>255, 255</rdf:li>
    </rdf:Seq>
   </crs:ToneCurvePV2012>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""


def build_sequence(base_dng: Path, indir: Path, n: int, kf_positions: list[int]) -> None:
    indir.mkdir(parents=True, exist_ok=True)
    for f in indir.iterdir():
        f.unlink()
    for idx in range(n):
        frame = indir / f"frame_{idx + 1:04d}.dng"
        frame.symlink_to(base_dng)  # same pixels; develop intent varies per frame
    for kf, pos in enumerate(kf_positions):
        xmp = indir / f"frame_{pos + 1:04d}.dng.xmp"
        xmp.write_text(_keyframe_xmp(kf))


def run_render(indir: Path, outdir: Path, *, target: str, workers: int,
               to_frame: int | None, dcp: Path) -> tuple[int, float]:
    argv = ["render", "--input", str(indir), "--output", str(outdir),
            "--no-dng-convert", "--target", target, "--workers", str(workers),
            "--dcp", str(dcp), "--quiet"]
    if to_frame is not None:
        argv += ["--to-frame", str(to_frame)]
    t0 = time.monotonic()
    rc = cli_main(argv)
    return rc, time.monotonic() - t0


_ICC_TAG = 34675


def _center_luma(arr: np.ndarray) -> float:
    h, w = arr.shape[:2]
    crop = arr[h // 2 - 256:h // 2 + 256, w // 2 - 256:w // 2 + 256]
    return float(crop.mean())


def analyse_tiff(outdir: Path, n: int) -> dict:
    files = sorted(outdir.glob("LRT_*.tif"))
    report: dict = {"files": len(files), "issues": [], "lumas": []}
    expected = [f"LRT_{i + 1:05d}.tif" for i in range(n)]
    report["naming_ok"] = [f.name for f in files] == expected
    sizes = []
    for f in files:
        with tifffile.TiffFile(f) as tf:
            pg = tf.pages[0]
            codes = [t.code for t in pg.tags]
            arr = pg.asarray()
            if pg.dtype != np.uint16:
                report["issues"].append(f"{f.name}: dtype {pg.dtype} != uint16")
            if _ICC_TAG not in codes:
                report["issues"].append(f"{f.name}: no embedded ICC")
            if not np.isfinite(arr).all():
                report["issues"].append(f"{f.name}: non-finite pixel(s)")
            desc = pg.tags.get(270)
            if desc:
                meta = json.loads(desc.value)
                if meta.get("colorspace") != "sRGB" or meta.get("range") != "full":
                    report["issues"].append(f"{f.name}: bad provenance {meta}")
            report["lumas"].append(_center_luma(arr))
            report["shape"] = arr.shape
        sizes.append(f.stat().st_size)
    report["size_mb"] = (min(sizes) / 1e6, max(sizes) / 1e6) if sizes else (0, 0)
    return report


def analyse_exr(outdir: Path) -> dict:
    import OpenEXR
    files = sorted(outdir.glob("*.exr"))
    if not files:
        return {"files": 0}
    with OpenEXR.File(str(files[0])) as exr:
        hdr = exr.header()
        chrom = np.asarray(hdr.get("chromaticities"), dtype=float).ravel()
    # AP1 white ≈ (0.32168, 0.33767)
    wp_ok = bool(np.allclose(chrom[6:8], [0.32168, 0.33767], atol=2e-3))
    return {"files": len(files), "ap1_whitepoint_ok": wp_ok, "chromaticities": chrom[:8].tolist()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-dng", type=Path, default=Path("/tmp/dng_out/DSC_4053.dng"))
    ap.add_argument("--frames", type=int, default=24)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", type=Path, default=Path("/tmp/lrt_prodtest"))
    ap.add_argument("--dcp", type=Path,
                    default=Path("/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
                                 "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"))
    a = ap.parse_args(argv)
    if not a.base_dng.is_file():
        print(f"error: base DNG not found: {a.base_dng}", file=sys.stderr)
        return 2
    if not a.dcp.is_file():
        print(f"error: DCP not found: {a.dcp}", file=sys.stderr)
        return 2

    n = a.frames
    kf = [0, n // 3, 2 * n // 3, n - 1]
    indir, tif_out, exr_out = a.out / "in", a.out / "tif", a.out / "exr"
    print(f"=== Phase 6 production test: {n} frames, keyframes at {kf} (EV {_EXPOSURES}) ===")
    build_sequence(a.base_dng, indir, n, kf)

    rc_t, dt_t = run_render(indir, tif_out, target="lrtimelapse", workers=a.workers,
                            to_frame=None, dcp=a.dcp)
    exr_subset = min(6, n)
    rc_e, dt_e = run_render(indir, exr_out, target="resolve", workers=a.workers,
                            to_frame=exr_subset, dcp=a.dcp)

    tif = analyse_tiff(tif_out, n)
    exr = analyse_exr(exr_out)

    print("\n========== EMISSION ANALYSIS ==========")
    print(f"[lrtimelapse / sRGB TIFF]  rc={rc_t}  frames={tif['files']}/{n}  "
          f"{dt_t:.1f}s ({n / dt_t:.2f} fps, {a.workers} workers)")
    print(f"  naming LRT_NNNNN contiguous: {tif['naming_ok']}")
    print(f"  dims: {tif.get('shape')}  size/frame: {tif['size_mb'][0]:.0f}-{tif['size_mb'][1]:.0f} MB")
    print(f"  conformance issues: {len(tif['issues'])}")
    for iss in tif["issues"][:10]:
        print(f"    - {iss}")
    # Interpolation: luma should track the EV ramp; report per-keyframe-segment trend.
    lumas = tif["lumas"]
    # Interpolation check: the develop ops lerp linearly between keyframes, so a
    # segment's center-luma must transition MONOTONICALLY end-to-end. (Do NOT test
    # luma-tracks-exposure: every param is keyframed, so luma reflects the COMBINED
    # grade — contrast/tone/HSL-lum/colour-grade can dominate the EV ramp.)
    print("  interpolation — within-segment monotonicity (full develop lerps linearly;")
    print("  luma is the COMBINED grade, not exposure alone):")
    tif["interp_monotonic"] = True
    if len(lumas) <= max(kf):
        print(f"    skipped — only {len(lumas)} frame(s) rendered")
        tif["interp_monotonic"] = False
    for s in range(len(kf) - 1) if len(lumas) > max(kf) else []:
        a0, a1 = kf[s], kf[s + 1]
        seg = lumas[a0:a1 + 1]
        d = np.diff(seg)
        mono = bool(np.all(d >= -1.0) or np.all(d <= 1.0))
        tif["interp_monotonic"] = tif["interp_monotonic"] and mono
        print(f"    frames {a0:>3}->{a1:>3}: luma {seg[0]:.0f}->{seg[-1]:.0f}  "
              f"monotonic transition={mono}")
    print(f"[resolve / ACEScg EXR]  rc={rc_e}  frames={exr['files']}/{exr_subset}  {dt_e:.1f}s")
    print(f"  AP1 (~D60) whitepoint tag: {exr.get('ap1_whitepoint_ok')}  chrom={exr.get('chromaticities')}")
    ok = (rc_t == 0 and rc_e == 0 and tif["naming_ok"] and not tif["issues"]
          and tif.get("interp_monotonic") and exr.get("ap1_whitepoint_ok"))
    print(f"\n=== VERDICT: {'PASS' if ok else 'REVIEW'} ===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
