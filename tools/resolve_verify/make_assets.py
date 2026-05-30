"""Generate the verification assets for the emission-format re-survey.

Produces, from the real gym DNG:
  scene_ref/RV_scene_0001.exr, _0002.exr  — Stage-7, overrange preserved,
      NO clipping LR ops. The RECOVERY layer (max latitude).
  baked/RV_baked_0001.exr, _0002.exr      — Stage-13 + full LR develop ops
      incl. an S-curve tone curve. The REPRESENT-ALL look layer (LOCKED).
  multilayer/RV_ml_0001.exr, _0002.exr    — one EXR per frame carrying BOTH:
      default RGB = scene-ref, "baked" layer RGB = baked look. Tests the
      self-contained single-file packaging.

Then reads the multilayer EXR back to prove the dual-layer write survives
locally (before asking Resolve to expose the layers).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import OpenEXR

from lrt_cinema.dcp import parse_dcp
from lrt_cinema.develop_ops import apply_develop_ops
from lrt_cinema.ir import DevelopOps, TonePoint
from lrt_cinema.output import _prophoto_to_rec2020
from lrt_cinema.pipeline import render_frame

GYM = Path("/tmp/dng_out/DSC_4053.dng")
DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"
)
OUT = Path("/tmp/resolve_verify/assets")

# A representative LRT look: exposure lift + contrast + sat + an S-curve tone
# curve (the curve is the Class-B op that clips — i.e. the headroom-spender).
LOOK = DevelopOps(
    exposure_ev=0.4,
    contrast=25,
    saturation=12,
    vibrance=8,
    blacks=-6,
    tone_curve=[TonePoint(0, 0), TonePoint(0.25, 0.16), TonePoint(0.75, 0.86), TonePoint(1.0, 1.0)],
)


def _write_layers(path: Path, layers: dict[str, np.ndarray], half: bool = True):
    """Write a multi-layer EXR. `layers` maps layer-name -> (H,W,3) float array;
    layer 'RGB' becomes the default R/G/B channels, others become 'name.R' etc."""
    chans = {}
    pix = (lambda a: a.astype(np.float16)) if half else (lambda a: a.astype(np.float32))
    for lname, arr in layers.items():
        arr = np.ascontiguousarray(arr)
        if lname == "RGB":
            chans["R"], chans["G"], chans["B"] = (
                pix(np.ascontiguousarray(arr[..., 0])),
                pix(np.ascontiguousarray(arr[..., 1])),
                pix(np.ascontiguousarray(arr[..., 2])),
            )
        else:
            chans[f"{lname}.R"] = pix(np.ascontiguousarray(arr[..., 0]))
            chans[f"{lname}.G"] = pix(np.ascontiguousarray(arr[..., 1]))
            chans[f"{lname}.B"] = pix(np.ascontiguousarray(arr[..., 2]))
    header = {"compression": OpenEXR.ZIP_COMPRESSION, "type": OpenEXR.scanlineimage}
    path.parent.mkdir(parents=True, exist_ok=True)
    with OpenEXR.File(header, chans) as f:
        f.write(str(path))


def _write_rgb(path: Path, rgb: np.ndarray, half=True, comp=OpenEXR.ZIP_COMPRESSION):
    _write_layers(path, {"RGB": rgb}, half=half)


def main() -> int:
    if not GYM.is_file() or not DCP.is_file():
        print("missing fixtures")
        return 2
    if OUT.exists():
        shutil.rmtree(OUT)

    profile = parse_dcp(DCP)
    print("rendering scene-ref (Stage-7, overrange) + baked (Stage-13 + look)...")
    scene7 = render_frame(GYM, profile, dcp_path=DCP, stop_after_stage=7).prophoto
    stage13 = render_frame(GYM, profile, dcp_path=DCP).prophoto
    baked = apply_develop_ops(stage13, LOOK)

    scene_rec = _prophoto_to_rec2020(scene7)
    baked_rec = _prophoto_to_rec2020(baked)
    print(
        f"  scene-ref Rec2020: max={scene_rec.max():.3f}  >1.0={100 * (scene_rec > 1).mean():.3f}%"
    )
    print(
        f"  baked     Rec2020: max={baked_rec.max():.3f}  >1.0={100 * (baked_rec > 1).mean():.3f}%"
    )

    # 2-frame sequences (Resolve treats numbered files as one moving clip).
    for i in (1, 2):
        _write_rgb(OUT / "scene_ref" / f"RV_scene_{i:04d}.exr", scene_rec)
        _write_rgb(OUT / "baked" / f"RV_baked_{i:04d}.exr", baked_rec)
        _write_layers(
            OUT / "multilayer" / f"RV_ml_{i:04d}.exr", {"RGB": scene_rec, "baked": baked_rec}
        )

    # Local round-trip: confirm both layers survive in the multilayer EXR.
    ml = OUT / "multilayer" / "RV_ml_0001.exr"
    with OpenEXR.File(str(ml), separate_channels=True) as f:
        chans = sorted(f.channels().keys())
    print(f"\nmultilayer EXR channels on disk: {chans}")
    has_default = {"R", "G", "B"}.issubset(chans)
    has_baked = {"baked.R", "baked.G", "baked.B"}.issubset(chans)
    ok = has_default and has_baked
    print(
        f"local multi-layer round-trip: {'PASS' if ok else 'FAIL'} "
        f"(default RGB={has_default}, baked layer={has_baked})"
    )
    print(f"\nassets in {OUT}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
