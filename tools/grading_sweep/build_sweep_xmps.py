#!/usr/bin/env python3
"""Generate a parameter-sweep set of Adobe Camera-Raw XMP sidecars.

One XMP per (lever, value), covering the full range of every HSL band and every
Color-Grade wheel that `lrt_cinema.develop_ops` bakes. These are valid ACR
sidecars (the same `crs:*` schema our parser reads), so the SAME files drive
both sides of the fidelity comparison:

  * ours  — `run_sweep.py` parses each XMP → DevelopOps → renders the chart;
  * Adobe — batch the chart raw + each XMP through ACR/Photoshop → TIFF.

See README.md for the comparison and constant-fitting procedure. Pure string
generation — no dependencies, runs anywhere.

Usage:
    python3 tools/grading_sweep/build_sweep_xmps.py --out /tmp/grading_sweep_xmps
"""

from __future__ import annotations

import argparse
from pathlib import Path

_XMP_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
    crs:ProcessVersion="11.0"
{attrs}/>
 </rdf:RDF>
</x:xmpmeta>
"""

_HSL_BANDS = ("Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta")
_HSL_CHANNELS = (("HueAdjustment", "hue"), ("SaturationAdjustment", "sat"),
                 ("LuminanceAdjustment", "lum"))
_SLIDER_STEPS = (-100, -50, 50, 100)          # full-range, skip identity 0
_CG_WHEELS = ("Shadow", "Midtone", "Highlight", "Global")
_CG_SAT_STEPS = (25, 50, 75, 100)
_CG_LUM_STEPS = (-100, -50, 50, 100)
_CG_HUE_STEPS = (0, 90, 180, 270)             # swept only with the wheel engaged (sat=100)


def _emit(out_dir: Path, name: str, attrs: dict[str, str | int]) -> None:
    body = "\n".join(f'    crs:{k}="{v}"' for k, v in attrs.items())
    (out_dir / f"{name}.xmp").write_text(_XMP_TEMPLATE.format(attrs=body))


def build_sweep(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0

    # Baseline (identity) — the reference frame both sides must reproduce.
    _emit(out_dir, "baseline", {})
    n += 1

    # HSL: every band × every channel × full slider range.
    for band in _HSL_BANDS:
        for tag, short in _HSL_CHANNELS:
            for v in _SLIDER_STEPS:
                _emit(out_dir, f"hsl_{short}_{band.lower()}_{v:+d}", {f"{tag}{band}": v})
                n += 1

    # Color Grade: per wheel — Saturation @ blue hue, Luminance, and Hue @ full sat.
    for wheel in _CG_WHEELS:
        for v in _CG_SAT_STEPS:
            _emit(out_dir, f"cg_{wheel.lower()}_sat_{v}",
                  {f"ColorGrade{wheel}Hue": 210, f"ColorGrade{wheel}Sat": v})
            n += 1
        for v in _CG_LUM_STEPS:
            _emit(out_dir, f"cg_{wheel.lower()}_lum_{v:+d}", {f"ColorGrade{wheel}Lum": v})
            n += 1
        for v in _CG_HUE_STEPS:
            _emit(out_dir, f"cg_{wheel.lower()}_hue_{v}",
                  {f"ColorGrade{wheel}Hue": v, f"ColorGrade{wheel}Sat": 100})
            n += 1

    # Blending / Balance: shape a fixed split-tone (blue shadows / orange highlights).
    split = {"ColorGradeShadowHue": 240, "ColorGradeShadowSat": 60,
             "ColorGradeHighlightHue": 45, "ColorGradeHighlightSat": 60}
    for v in (0, 25, 75, 100):
        _emit(out_dir, f"cg_blending_{v}", {**split, "ColorGradeBlending": v})
        n += 1
    for v in (-100, -50, 50, 100):
        _emit(out_dir, f"cg_balance_{v:+d}", {**split, "ColorGradeBalance": v})
        n += 1

    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("/tmp/grading_sweep_xmps"),
                    help="output directory for the XMP set")
    args = ap.parse_args()
    count = build_sweep(args.out)
    print(f"wrote {count} sweep XMPs to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
