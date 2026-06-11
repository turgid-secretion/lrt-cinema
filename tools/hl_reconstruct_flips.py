"""Slot-5b owner-verdict flip arms — real production frame, native res.

Renders DSC_4053 (the blown-window production frame) through the FULL
production chain (XMP intent, menon, develop ops) with three highlight
treatments, full frames at native resolution, zero scaling:

  A-clip-shipped     — the shipped default: clip-to-common-white (5a).
  B-pre-opposed      — headroom decode → opposed reconstruction ON the
                       WB-scaled mosaic (darktable placement) → demosaic.
  C-post-opposed     — headroom decode → demosaic → opposed on RGB driven
                       by the mosaic clip mask (RT placement).

WHY OWNER EYES DECIDE (the suite metric's blind spot, recorded in the 5b
evidence): the pressure articles score against a SENSOR-CLIPPED reference,
so an arm that recovers REAL highlight detail scores as error even when it
looks better. The flips answer what the metric cannot: does opposed recover
visible window detail, and does it introduce artifacts?

Out: ~/lrt-cinema-fixtures/verify-2026-06-11/hl-flip/ + README.txt
Run: python3 tools/hl_reconstruct_flips.py
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
OUT = FIX / "verify-2026-06-11" / "hl-flip"
B = 8  # menon border crop (matches the earlier flip stacks)


def main() -> int:
    import rawpy
    from PIL import Image

    from lrt_cinema._opposed_reconstruct import (
        reconstruct_mosaic_opposed,
        reconstruct_rgb_opposed,
    )
    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.develop_ops import apply_develop_ops
    from lrt_cinema.interpolation import LR_LOCAL_EXPOSURE_SCALE
    from lrt_cinema.ir import RenderIntent
    from lrt_cinema.output import _prophoto_to_display
    from lrt_cinema.pipeline import (
        _cfa_demosaic,
        _extract_cfa,
        _mosaic_clip_mask,
        _wb_mul_from_asn,
        apply_adobe_pipeline,
        kelvin_to_neutral,
        read_dcp_default_black_render,
        read_dng_baseline_exposure,
    )
    from lrt_cinema.xmp_parser import parse_xmp_file

    OUT.mkdir(parents=True, exist_ok=True)
    profile = parse_dcp(DCP)
    ops, _kf, _dfk, _rating, mask_offsets = parse_xmp_file(XMP)
    scene_ev = LR_LOCAL_EXPOSURE_SCALE * sum(ev for _k, ev in mask_offsets)
    ops = replace(ops, scene_exposure_ev=scene_ev)
    scene_kelvin = float(ops.temperature_k)
    asn = kelvin_to_neutral(profile, scene_kelvin, float(ops.tint or 0.0))
    wb_mul = _wb_mul_from_asn(asn)
    dng_be = read_dng_baseline_exposure(DNG)
    dbr = read_dcp_default_black_render(DCP)

    def demosaic_menon(cfa, pattern):
        from colour_demosaicing import demosaicing_CFA_Bayer_Menon2007
        return np.maximum(np.asarray(
            demosaicing_CFA_Bayer_Menon2007(cfa, pattern), np.float32), 0.0)

    arms = ("A-clip-shipped", "B-pre-opposed", "C-post-opposed")
    for name in arms:
        with rawpy.imread(str(DNG)) as raw:
            if name == "A-clip-shipped":
                cam = _cfa_demosaic(raw, "menon", wb_mul, highlights="clip")
            elif name == "B-pre-opposed":
                cfa, pattern = _extract_cfa(raw)
                colors = raw.raw_colors_visible
                h, w = cfa.shape
                chan = np.where(colors[:h, :w] == 3, 1, colors[:h, :w])
                cfa = cfa * wb_mul[chan].astype(np.float32)
                cfa = reconstruct_mosaic_opposed(cfa, chan, wb_mul)
                cam = demosaic_menon(cfa, pattern)
            else:
                cam = _cfa_demosaic(raw, "menon", wb_mul, highlights="headroom")
                mask = _mosaic_clip_mask(raw)[: cam.shape[0], : cam.shape[1]]
                cam = reconstruct_rgb_opposed(cam, mask, wb_mul)

        total_ev = ops.scene_exposure_ev + ops.exposure_ev
        if total_ev != 0.0:
            cam = cam * np.float32(2.0 ** total_ev)
        pp = apply_adobe_pipeline(
            camera_rgb=cam, profile=profile, as_shot_neutral=asn,
            scene_kelvin=scene_kelvin, dng_baseline_exposure=dng_be,
            default_black_render=dbr, stop_after_stage=9)
        pp = apply_develop_ops(pp, ops, RenderIntent.FAITHFUL,
                               master_look="bake", capture_sharpen="off")
        srgb8 = (np.clip(_prophoto_to_display(pp, "srgb"), 0, 1)
                 * 255 + 0.5).astype(np.uint8)[B:-B, B:-B]
        Image.fromarray(srgb8).save(OUT / f"DSC_4053_{name}.png")
        print(f"wrote {name} ({srgb8.shape[1]}x{srgb8.shape[0]})")

    (OUT / "README.txt").write_text(
        "Slot-5b owner verdict — highlight reconstruction flip arms\n"
        "===========================================================\n"
        "DSC_4053 (blown windows), FULL production intent (develop WB "
        "4034K/+20,\nmask EVs x4, menon demosaic), native resolution "
        "4016x6016, zero scaling.\nFlip between these three in an image "
        "viewer (sorted = stack order):\n\n"
        "  DSC_4053_A-clip-shipped.png   the SHIPPED default (5a clip-to-"
        "common-white)\n"
        "  DSC_4053_B-pre-opposed.png    dt-placement opposed: reconstruct "
        "ON the mosaic,\n                                then demosaic "
        "(headroom decode)\n"
        "  DSC_4053_C-post-opposed.png   RT-placement opposed: demosaic "
        "(headroom), then\n                                reconstruct on "
        "RGB via the mosaic clip mask\n\n"
        "WHAT TO JUDGE (the pressure-suite metric cannot see this):\n"
        "  - Do B/C recover real, believable window detail vs A's flat "
        "neutral white?\n"
        "  - Do B/C introduce colour casts or edge artifacts at the "
        "highlight borders?\n\n"
        "Suite verdict already measured (hl_reconstruct_5b_2026-06-11.json):"
        "\n  on the construction articles the clip default BEATS both "
        "opposed placements\n  (clipbars falsecolor: clip 1.12 vs pre 17.2 "
        "/ post 21.5; clipramp clip-zone:\n  3.03 vs 3.52 both). If "
        "reconstruction ships at all, pre (mosaic) dominates\n  post on "
        "every measured article. Default remains clip either way "
        "(owner-gated).\n"
    )
    print(f"README + arms -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
