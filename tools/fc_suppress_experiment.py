"""Slot-6 false-colour-suppression iteration — passes sweep vs LR anchors.

THE QUESTION (TARGET slot 6): how far does the canon's chroma-difference
median (`lrt_cinema._fc_suppress`, dt/dcraw class) close the
chroma-dominated gaps, and at what resolution cost?

ARMS: menon demosaic (shipped clip default) + suppression passes 0/1/2/3/5.

TARGETS (LR-product anchors, CLAIMS "LR-PRODUCT anchors scored"):
  zoneplate  falsecolor 0.41 → ≈0.02 (ACR ≈ eliminates it)
  noisebars  falsecolor 8.0  → ≈4   (dt's suppression class lands ≈5)
  diagbars   falsecolor 34   → measure the contribution (slot-4 remainder)
GUARDS (resolution cost — must NOT regress):
  slantededge de_mean stays ≈0.004-class; bars falsecolor must stay
  ≤ LR-product 2.03 (we lead at 1.15 — keep leading).

PRE-REGISTERED PREDICTIONS (2026-06-11, before first run):
  P1: zoneplate falsecolor drops monotonically with passes; ≥2 passes
      reach ≤0.1 (the scheme is built for exactly this dense-frequency
      invented chroma).
  P2: noisebars reaches the dt-class ≈5 by 2–3 passes.
  P3: diagbars improves but does NOT reach 14 — its error is structural
      (ΔL) as much as chroma; the remainder is the demosaic's (slot 4).
  P4: slantededge/bars unchanged at ≤1 pass-level tolerance (G untouched;
      luma edges cancel in the difference domain).

Run:  python3 tools/fc_suppress_experiment.py
Out:  tests/fixtures/evidence/fc_suppress_slot6_2026-06-11.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools" / "test_articles"))

from fields import scene_field  # noqa: E402

FIX = Path.home() / "lrt-cinema-fixtures"
ART = FIX / "test-articles"
DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"
)
ARTICLES = ("zoneplate", "noisebars", "diagbars", "bars", "slantededge")
PASSES = (0, 1, 2, 3, 5)
BLUR_PASSES = (1, 2, 3)   # RT-style median+box-blur arms
EVIDENCE = REPO / "tests/fixtures/evidence/fc_suppress_slot6_2026-06-11.json"


def main() -> int:
    import rawpy
    from run_pressure import _score

    from lrt_cinema._fc_suppress import suppress_false_colour
    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.output import _prophoto_to_display
    from lrt_cinema.pipeline import (
        _asn_from_wb,
        _cfa_demosaic,
        _wb_mul_from_asn,
        apply_adobe_pipeline,
        read_dcp_default_black_render,
        read_dng_baseline_exposure,
    )

    manifest = json.loads((ART / "manifest.json").read_text())
    asn = np.asarray(manifest["asn"], np.float32)
    profile = parse_dcp(DCP)
    profile = type(profile)(**{**profile.__dict__,
                               "forward_matrix_1": None, "forward_matrix_2": None})
    dbr = read_dcp_default_black_render(DCP)

    def to8(pp):
        return (np.clip(_prophoto_to_display(pp, "srgb"), 0, 1)
                * 255 + 0.5).astype(np.uint8)

    results: dict = {
        "design": "slot-6 chroma-difference median (dt/dcraw class), passes sweep on menon",
        "predictions": "P1 zoneplate→≤0.1 by 2 passes; P2 noisebars→≈5; "
                       "P3 diagbars partial (structural remainder); "
                       "P4 slantededge/bars unharmed",
        "articles": {},
    }
    for name in ARTICLES:
        dng = ART / f"{name}.dng"
        meta = manifest["articles"][name]
        dng_be = read_dng_baseline_exposure(dng)
        with rawpy.imread(str(dng)) as r:
            h, w = r.raw_image_visible.shape
        h -= h % 2
        w -= w % 2
        scene = scene_field(meta["spec"], h, w)
        unbal = scene * asn[None, None, :]
        wb_mul3 = (1.0 / asn) / (1.0 / asn)[1]
        exp_in = (np.minimum(unbal, 1.0) * wb_mul3).astype(np.float32)
        exp8 = to8(apply_adobe_pipeline(
            camera_rgb=exp_in, profile=profile, as_shot_neutral=asn,
            scene_kelvin=5500.0, dng_baseline_exposure=dng_be,
            default_black_render=dbr, stop_after_stage=9))

        # ONE decode+demosaic per article; the sweep applies suppression to
        # the same balanced RGB (isolates the suppression variable).
        with rawpy.imread(str(dng)) as raw:
            file_asn = _asn_from_wb(raw.camera_whitebalance)
            rgb0 = _cfa_demosaic(raw, "menon", _wb_mul_from_asn(file_asn),
                                 highlights="clip")

        row: dict = {"arms": {}}
        arm_specs = [(p, False) for p in PASSES] + [(p, True) for p in BLUR_PASSES]
        for p, blur in arm_specs:
            rgb = suppress_false_colour(rgb0, passes=p, blur=blur)
            pp = apply_adobe_pipeline(
                camera_rgb=rgb, profile=profile, as_shot_neutral=file_asn,
                scene_kelvin=5500.0, dng_baseline_exposure=dng_be,
                default_black_render=dbr, stop_after_stage=9)
            ours8 = to8(pp)
            oh, ow = ours8.shape[:2]
            s = _score(ours8, exp8[:oh, :ow], True, None)
            tag = f"p{p}" + ("+blur" if blur else "")
            row["arms"][tag] = s
            print(f"{name:11s} {tag:8s}  falsecolor={s['falsecolor_mean']:.3f}"
                  f"  de={s['de_mean']:.3f}  dl={s['dl_mean']:.3f}"
                  f"  dc={s['dc_mean']:.3f}")
        results["articles"][name] = row

    EVIDENCE.write_text(json.dumps(results, indent=1))
    print(f"\nevidence → {EVIDENCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
