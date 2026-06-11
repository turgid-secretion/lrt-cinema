"""Global-Exposure2012 DOMAIN probe — where does LR apply the exposure slider?

Single-variable owner exports (CALEXP{100,200}_4053: production XMP with ONLY
crs:Exposure2012 = 1.0 / 2.0 changed, xmp-diff-verified; LR Classic 16-bit
sRGB TIFFs) arbitrate between three application domains, each rendered through
our full production chain (develop WB 4034K/+20, deflicker mask EV ×4
scene-referred, master-look bake, sharpening off):

  A  current   — Stage-11 post-ProfileToneCurve multiply (ops.exposure_ev),
                 today's shipped semantics
  B  scene     — pure linear gain on camera RGB upstream of Stage 2
                 (the domain that nailed the LOCAL/mask exposure at ×4)
  C  ramp      — folded into TotalBaselineExposure → Stage-7 ExposureRamp
                 (Adobe's own exposure machinery, soft highlight shoulder)

PRE-REGISTERED (2026-06-11, before any render): A fails at +1/+2 EV with the
same highlight-heavy signature the deflicker calibration exposed; B matches
midtones but deviates in highlights at +2 EV (Adobe documents Exposure2012
with built-in highlight rolloff); C wins at both levels. If C wins, the
TARGET-architecture slot for global exposure is the Stage-7 ramp, with
evidence; if B wins, global == local semantics (pure gain).

Run:  python3 tools/cal_exposure_domain.py
Out:  tests/fixtures/evidence/cal_exposure_domain_2026-06-11.json
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np

FIX = Path.home() / "lrt-cinema-fixtures"
CAL_DIR = FIX / "production/calibration"
DNG = FIX / "DSC_4053.dng"
DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"
)
RENDERS = CAL_DIR / ".calexp-renders"
EVIDENCE = Path(__file__).resolve().parent.parent / (
    "tests/fixtures/evidence/cal_exposure_domain_2026-06-11.json"
)
LEVELS = {"CALEXP100": 1.0, "CALEXP200": 2.0}
MID_LO, MID_HI = 0.04, 0.60
DOWN = 4


def _block_down(a: np.ndarray, k: int) -> np.ndarray:
    h, w = a.shape[:2]
    h2, w2 = (h // k) * k, (w // k) * k
    return a[:h2, :w2].reshape(h2 // k, k, w2 // k, k, -1).mean(axis=(1, 3))


def _compare(ours_tif: Path, lr_tif: Path) -> dict:
    import colour
    import tifffile
    o = tifffile.imread(str(ours_tif)).astype(np.float32) / 65535.0
    t = tifffile.imread(str(lr_tif)).astype(np.float32) / 65535.0
    o = o[8:-8, 8:-8]                      # ours 4032 → LR 4016 grid
    od, td = _block_down(o, DOWN), _block_down(t, DOWN)
    lo = colour.XYZ_to_Lab(colour.RGB_to_XYZ(colour.models.eotf_sRGB(od),
                                             "sRGB", apply_cctf_decoding=False),
                           illuminant=np.array([0.3127, 0.3290]))
    lt = colour.XYZ_to_Lab(colour.RGB_to_XYZ(colour.models.eotf_sRGB(td),
                                             "sRGB", apply_cctf_decoding=False),
                           illuminant=np.array([0.3127, 0.3290]))
    de = colour.delta_E(lo, lt, method="CIE 2000")
    olin, tlin = colour.models.eotf_sRGB(od), colour.models.eotf_sRGB(td)
    lum_o, lum_t = olin.mean(-1), tlin.mean(-1)
    mid = (lum_o > MID_LO) & (lum_o < MID_HI) & (lum_t > MID_LO) & (lum_t < MID_HI)
    gains = [float((olin[..., c][mid] @ tlin[..., c][mid])
                   / (olin[..., c][mid] @ olin[..., c][mid])) for c in range(3)]
    return {"de_mean": float(de.mean()), "de_mid": float(de[mid].mean()),
            "gain_mid_mean": float(np.mean(gains)), "midtone_frac": float(mid.mean())}


def main() -> int:
    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.develop_ops import apply_develop_ops
    from lrt_cinema.interpolation import LR_LOCAL_EXPOSURE_SCALE
    from lrt_cinema.ir import RenderIntent
    from lrt_cinema.output import write_preset_output
    from lrt_cinema.pipeline import (
        _decode_raw,
        apply_adobe_pipeline,
        kelvin_to_neutral,
        read_dcp_default_black_render,
        read_dng_baseline_exposure,
    )
    from lrt_cinema.xmp_parser import parse_xmp_file

    RENDERS.mkdir(parents=True, exist_ok=True)
    profile = parse_dcp(DCP)
    dbr = read_dcp_default_black_render(DCP)
    base_be = read_dng_baseline_exposure(DNG)
    results: dict = {"arms": {}, "pre_registered":
                     "A fails; B midtones-only; C (ramp) wins both levels"}

    for name, ev in LEVELS.items():
        xmp = CAL_DIR / f"{name}_4053.xmp"
        lr_tif = CAL_DIR / f"{name}_4053.tif"
        ops, _kf, _dfk, _r, mask_offsets = parse_xmp_file(xmp)
        assert ops.exposure_ev == ev, (name, ops.exposure_ev)
        scene_ev = LR_LOCAL_EXPOSURE_SCALE * sum(e for _k, e in mask_offsets)
        ops = replace(ops, scene_exposure_ev=scene_ev)
        kelvin = float(ops.temperature_k)
        asn = kelvin_to_neutral(profile, kelvin, float(ops.tint or 0.0))
        cam, _, _ = _decode_raw(DNG, demosaic="linear", wb_asn=asn)

        def render(arm: str, ops_in, cam_in, be: float,
                   frame=name, asn_=asn, kelvin_=kelvin,
                   legacy_postcurve_ev: float = 0.0) -> Path:
            dst = RENDERS / f"{frame}_{arm}"
            out = dst.with_suffix(".tif")
            if out.exists():
                return out
            pp = apply_adobe_pipeline(
                camera_rgb=cam_in * np.float32(2.0 ** ops_in.scene_exposure_ev),
                profile=profile, as_shot_neutral=asn_, scene_kelvin=kelvin_,
                dng_baseline_exposure=be, default_black_render=dbr,
                stop_after_stage=9)
            pp = apply_develop_ops(pp, ops_in, RenderIntent.FAITHFUL,
                                   master_look="bake", capture_sharpen="off")
            if legacy_postcurve_ev != 0.0:
                # The REFUTED arm A re-creates the deleted Stage-11
                # post-curve Exposure2012 multiply (the pipeline now folds
                # exposure_ev scene-referred, so the legacy behaviour must
                # be reconstructed here to keep this experiment
                # regenerable). Exact for these frames: every other
                # Stage-11/12 slider is zero (production XMPs), so the
                # multiply commutes to this position.
                pp = pp * np.float32(2.0 ** legacy_postcurve_ev)
            write_preset_output(pp, dst, "lrtimelapse")
            return out

        zeroed = replace(ops, exposure_ev=0.0)
        arms = {
            "A-current-postcurve": render(
                "A", zeroed, cam, base_be, legacy_postcurve_ev=ev),
            "B-scene-puregain": render(
                "B", zeroed, cam * np.float32(2.0 ** ev), base_be),
            "C-ramp-baselineexp": render("C", zeroed, cam, base_be + ev),
        }
        for arm, tif in arms.items():
            m = _compare(tif, lr_tif)
            results["arms"].setdefault(arm, {})[name] = m
            print(f"{name} {arm:22s} ΔE {m['de_mean']:.3f}  "
                  f"ΔE_mid {m['de_mid']:.3f}  gain_mid {m['gain_mid_mean']:.4f}")

    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(results, indent=1))
    print(f"\nevidence -> {EVIDENCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
