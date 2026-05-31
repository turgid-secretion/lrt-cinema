#!/usr/bin/env python3
"""Render the OURS side of the grading sweep and (optionally) compare to ACR.

For each XMP from `build_sweep_xmps.py`, parse it → DevelopOps, render a chart
raw through the full lrt-cinema pipeline (Stages 1-9 + develop_ops), sample
per-patch means, and write `ours.json`. If `--adobe-dir` (a folder of
ACR-rendered TIFFs named `<lever>.tif`) is supplied, sample the same patches and
emit a per-lever ΔE2000 table — the Tier-1 fidelity comparison + the data to
fit our approximation constants against. See README.md.

Prerequisites: a DCP profile (`--dcp`, a `.dcp` or `.npz`) and a chart raw.
Without `--raw`, a synthetic flat-patch chart DNG is built from the test
fixtures (needs `/tmp/dng_out/DSC_4053.dng` + dnglab). The tool prints exactly
what is missing and exits cleanly — it does not pretend to have rendered.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def _load_profile(dcp: Path):
    from lrt_cinema.dcp import load_profile, parse_dcp
    return load_profile(dcp) if dcp.suffix.lower() == ".npz" else parse_dcp(dcp)


def _synthetic_chart(work: Path):
    """Build a synthetic flat-patch chart DNG from fixtures; return (dng, patches)
    or (None, None) if prerequisites are missing."""
    src = Path("/tmp/dng_out/DSC_4053.dng")
    if not src.is_file():
        return None, None
    from lrt_cinema.pipeline import read_as_shot_neutral
    from tests import synthetic_dng as sd
    uncomp = work / "sweep_chart_uncomp.dng"
    if not sd.ensure_uncompressed_clone(src, uncomp):
        return None, None
    layout = sd.read_raw_layout(uncomp)
    asn = read_as_shot_neutral(uncomp)
    chart = sd.default_chart(asn)
    cfa = sd.build_cfa(layout, chart.patches)
    dng = work / "sweep_chart.dng"
    sd.write_synthetic_dng(uncomp, dng, cfa, layout)
    return dng, chart.patches


def _render_ours(dng: Path, profile, dcp: Path, ops):
    from lrt_cinema.develop_ops import apply_develop_ops
    from lrt_cinema.pipeline import render_frame
    result = render_frame(dng, profile, dcp_path=dcp, develop_ops=ops)
    return apply_develop_ops(result.prophoto, ops)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--xmp-dir", type=Path, default=Path("/tmp/grading_sweep_xmps"))
    ap.add_argument("--dcp", type=Path, required=True, help=".dcp or .npz profile")
    ap.add_argument("--raw", type=Path, help="chart raw/DNG (default: synthetic from fixtures)")
    ap.add_argument("--adobe-dir", type=Path, help="folder of ACR-rendered <lever>.tif for compare")
    ap.add_argument("--work", type=Path, default=Path("/tmp/grading_sweep_work"))
    ap.add_argument("--out", type=Path, default=Path("/tmp/grading_sweep_work/ours.json"))
    args = ap.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)

    xmps = sorted(args.xmp_dir.glob("*.xmp"))
    if not xmps:
        sys.stderr.write(f"no XMPs in {args.xmp_dir} — run build_sweep_xmps.py first\n")
        return 2
    if not args.dcp.is_file():
        sys.stderr.write(f"DCP not found: {args.dcp} (pass --dcp a .dcp or extracted .npz)\n")
        return 2

    from lrt_cinema.xmp_parser import parse_xmp_file
    from tests import synthetic_dng as sd

    if args.raw:
        sys.stderr.write(
            "--raw given: supply patch coordinates for your chart; this scaffold "
            "samples the synthetic fixture chart. Edit _synthetic_chart for a custom chart.\n",
        )
    dng, patches = _synthetic_chart(args.work)
    if dng is None:
        sys.stderr.write(
            "synthetic chart unavailable (need /tmp/dng_out/DSC_4053.dng + dnglab). "
            "Cannot render the ours side here.\n",
        )
        return 2

    profile = _load_profile(args.dcp)
    ours: dict[str, list[list[float]]] = {}
    for xmp in xmps:
        ops, *_ = parse_xmp_file(xmp)
        img = _render_ours(dng, profile, args.dcp, ops)
        ours[xmp.stem] = sd.sample_patch_means(img, patches).tolist()
    args.out.write_text(json.dumps(ours, indent=2))
    print(f"rendered {len(ours)} levers → {args.out}")

    if args.adobe_dir:
        _compare(ours, patches, args.adobe_dir, sd, args.work)
    return 0


def _compare(ours: dict, patches, adobe_dir: Path, sd, work: Path) -> None:
    """Per-lever mean ΔE2000 between ours and ACR-rendered TIFFs."""
    import colour
    import tifffile

    from tests.test_pipeline import _to_lab_d65
    rows = ["lever,mean_dE,max_dE,n_patches"]
    for lever, ours_patch in ours.items():
        tif = adobe_dir / f"{lever}.tif"
        if not tif.is_file():
            continue
        gt = tifffile.imread(str(tif))
        gt8 = (gt.astype(np.float32) / (65535.0 if gt.dtype == np.uint16 else 255.0) * 255).astype(np.uint8)
        gt_means = sd.sample_patch_means(gt8, patches)
        ours8 = np.clip(np.array(ours_patch), 0, 255).astype(np.uint8).reshape(-1, 1, 3)
        de = colour.delta_E(_to_lab_d65(ours8), _to_lab_d65(gt_means.reshape(-1, 1, 3)), method="CIE 2000")
        rows.append(f"{lever},{float(de.mean()):.3f},{float(de.max()):.3f},{len(patches)}")
    out = work / "compare.csv"
    out.write_text("\n".join(rows) + "\n")
    print(f"compare table → {out}  ({len(rows) - 1} levers matched)")


if __name__ == "__main__":
    raise SystemExit(main())
