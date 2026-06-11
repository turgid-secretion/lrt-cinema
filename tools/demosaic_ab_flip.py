"""Single-variable demosaic-conditioning A/B at FULL NATIVE RESOLUTION.

The owner's flip-stack review (2026-06-10) confirmed the fixed renders are
clean but noted the old-pipeline arm is NOT a valid "before" (it was rendered
with different/undatable WB + flags, so it neither shows the cyan bug nor
isolates the fix). This tool produces the missing pair — identical code,
identical develop intent, ONE variable:

    E-menon-prefixbug.png   demosaic the RAW (unbalanced) mosaic — the
                            pre-2026-06-10 conditioning (wb_mul=None)
    F-menon-fixed.png       demosaic the WB-conditioned mosaic — the
                            shipped H1 fix (production code path)

Both: menon (the directional quality demosaic — bilinear 'linear' barely
manifests the bug, which is exactly why the Adobe gate missed it), the
production XMP intent for DSC_4053 (WB 4034K/+20, deflicker mask EV ×4
scene-referred), stages 2–9, develop ops, 16-bit-accurate 8-bit PNG view,
cropped 8 px to the 4016×6016 flip-stack grid. Drop them in the flip folder
and arrow between E and F: the cyan blinds edges are the difference.

Also prints the h1 cyanness metric on the artifact window for both arms so
the pair is numerically pinned to the h1 evidence lineage.

Run:  python3 tools/demosaic_ab_flip.py
Out:  ~/lrt-cinema-fixtures/verify-2026-06-10/flip/LRT_00001_{E,F}-*.png
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np

FIX = Path.home() / "lrt-cinema-fixtures"
DNG = FIX / "DSC_4053.dng"
XMP = FIX / "production/xmp/DSC_4053.xmp"
DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"
)
OUT = FIX / "verify-2026-06-10/flip"
H1_META = FIX / "h1/h1_metrics.json"
B = 8                    # alignment crop to the 4016×6016 flip grid
CROP = 512               # metric window (matches the h1 harness)


def _cyanness(srgb8: np.ndarray) -> np.ndarray:
    import colour
    lin = colour.models.eotf_sRGB(srgb8.astype(np.float64) / 255.0)
    return np.maximum(np.minimum(lin[..., 1], lin[..., 2]) - lin[..., 0], 0.0)


def main() -> int:
    import rawpy
    from PIL import Image

    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.develop_ops import apply_develop_ops
    from lrt_cinema.interpolation import LR_LOCAL_EXPOSURE_SCALE
    from lrt_cinema.ir import RenderIntent
    from lrt_cinema.output import _prophoto_to_display
    from lrt_cinema.pipeline import (
        _demosaic_rgb,
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
    # parse_xmp_file returns mask offsets as (kind, exposure_delta_ev) tuples
    scene_ev = LR_LOCAL_EXPOSURE_SCALE * sum(ev for _kind, ev in mask_offsets)
    ops = replace(ops, scene_exposure_ev=scene_ev)
    scene_kelvin = float(ops.temperature_k)
    asn = kelvin_to_neutral(profile, scene_kelvin, float(ops.tint or 0.0))
    wb_mul = _wb_mul_from_asn(asn)
    dng_be = read_dng_baseline_exposure(DNG)
    dbr = read_dcp_default_black_render(DCP)

    arms = {"E-menon-prefixbug": None, "F-menon-fixed": wb_mul}
    rendered: dict[str, np.ndarray] = {}
    for name, mul in arms.items():
        with rawpy.imread(str(DNG)) as raw:
            cam = _demosaic_rgb(raw, rawpy, False, "menon", mul)
        if ops.scene_exposure_ev != 0.0:
            cam = cam * np.float32(2.0 ** ops.scene_exposure_ev)
        pp = apply_adobe_pipeline(
            camera_rgb=cam, profile=profile, as_shot_neutral=asn,
            scene_kelvin=scene_kelvin, dng_baseline_exposure=dng_be,
            default_black_render=dbr, stop_after_stage=9,
        )
        pp = apply_develop_ops(pp, ops, RenderIntent.FAITHFUL,
                               master_look="bake", capture_sharpen="off")
        srgb8 = (np.clip(_prophoto_to_display(pp, "srgb"), 0.0, 1.0)
                 * 255.0 + 0.5).astype(np.uint8)
        srgb8 = srgb8[B:-B, B:-B]
        rendered[name] = srgb8
        Image.fromarray(srgb8).save(OUT / f"LRT_00001_{name}.png")
        print(f"{name}: written")

    cy0, cx0 = json.loads(H1_META.read_text())["crop_yx"]
    cy0, cx0 = cy0 - B, cx0 - B   # h1 coords are on the ours/uncropped grid
    print("\ncyanness on the h1 artifact window (P99.5 ×1000):")
    for name, img in rendered.items():
        c = _cyanness(img[cy0:cy0 + CROP, cx0:cx0 + CROP])
        print(f"  {name}: {np.percentile(c, 99.5) * 1000:.1f}")
    print(f"\nflip pair -> {OUT} (4016×6016 native, zero scaling)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
