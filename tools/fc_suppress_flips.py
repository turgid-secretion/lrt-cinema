"""Slot-6 owner-verdict flip — FC suppression on/off, real frame, native res.

Renders DSC_4053 through the FULL production chain (XMP intent, menon)
with the slot-6 false-colour suppression off vs the recommended setting
(3 passes + RT-style chroma blur — the slot-6 sweep's best arm), full
frames at native resolution, zero scaling.

Out: ~/lrt-cinema-fixtures/verify-2026-06-11/fc-flip/ + README.txt
Run: python3 tools/fc_suppress_flips.py
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

FIX = Path.home() / "lrt-cinema-fixtures"
DNG = FIX / "DSC_4053.dng"
XMP = FIX / "production" / "xmp" / "DSC_4053.xmp"
DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"
)
OUT = FIX / "verify-2026-06-11" / "fc-flip"
B = 8


def main() -> int:
    from PIL import Image

    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.develop_ops import apply_develop_ops
    from lrt_cinema.interpolation import LR_LOCAL_EXPOSURE_SCALE
    from lrt_cinema.ir import RenderIntent
    from lrt_cinema.output import _prophoto_to_display
    from lrt_cinema.pipeline import render_frame
    from lrt_cinema.xmp_parser import parse_xmp_file

    OUT.mkdir(parents=True, exist_ok=True)
    profile = parse_dcp(DCP)
    ops, _kf, _dfk, _rating, mask_offsets = parse_xmp_file(XMP)
    scene_ev = LR_LOCAL_EXPOSURE_SCALE * sum(ev for _k, ev in mask_offsets)
    ops = replace(ops, scene_exposure_ev=scene_ev)

    for name, n_passes in (("A-fc-off-shipped", 0), ("B-fc-p3blur", 3)):
        res = render_frame(
            DNG, profile, dcp_path=DCP, develop_ops=ops,
            demosaic="menon", fc_suppress=n_passes,
        )
        pp = apply_develop_ops(res.prophoto, ops, RenderIntent.FAITHFUL,
                               master_look="bake", capture_sharpen="off")
        srgb8 = (np.clip(_prophoto_to_display(pp, "srgb"), 0, 1)
                 * 255 + 0.5).astype(np.uint8)[B:-B, B:-B]
        Image.fromarray(srgb8).save(OUT / f"DSC_4053_{name}.png")
        print(f"wrote {name} ({srgb8.shape[1]}x{srgb8.shape[0]})")

    (OUT / "README.txt").write_text(
        "Slot-6 owner verdict — false-colour suppression on/off\n"
        "=======================================================\n"
        "DSC_4053, FULL production intent (develop WB 4034K/+20, mask EVs "
        "x4,\nmenon demosaic), native resolution 4016x6016, zero scaling. "
        "Flip:\n\n"
        "  DSC_4053_A-fc-off-shipped.png  shipped default (no suppression)\n"
        "  DSC_4053_B-fc-p3blur.png       slot-6 suppression, recommended "
        "setting\n                                 (3 passes, chroma-"
        "difference median + RT-style\n                                 "
        "chroma blur; --fc-suppress 3)\n\n"
        "WHAT TO JUDGE:\n"
        "  - Does B reduce residual colour shimmer/fringing on fine detail\n"
        "    (blinds, window frames, grain) vs A?\n"
        "  - Any visible softening or desaturation of real colour detail "
        "in B?\n    (The suite guards say no: slantededge 0.004-class "
        "unchanged, bars\n    falsecolor IMPROVES 1.15->1.11, G/luma never "
        "touched.)\n\n"
        "Measured (fc_suppress_slot6_2026-06-11.json, menon arms):\n"
        "  noisebars falsecolor 7.98 -> 4.36 (LR-product anchor ~4.25)\n"
        "  diagbars  falsecolor 34.2 -> 23.6 (LR 13.6 - remainder is the\n"
        "            demosaic's diagonal handling, slot-4 scope note)\n"
        "  zoneplate falsecolor 0.41 -> 0.39 (INERT - this scheme class\n"
        "            cannot reach ACR's 0.02; demosaic-internal mechanism)\n"
        "  Off by default; owner decides whether/where to enable.\n"
    )
    print(f"README + arms -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
