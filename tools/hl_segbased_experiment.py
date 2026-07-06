"""Segmentation-based reconstruction (survey #2) — deciding experiment.

THE QUESTION: does the dt-class segmentation reconstruction (opposed
base + per-segment candidates + optional all-clipped rebuild) recover
highlights competently WITHOUT the artifact classes that killed plain
opposed (owner verdict + clipbars falsecolor 17.2), and does the rebuild
recover LARGE fully-blown areas that nothing else touches?

ARMS (menon demosaic held constant for comparability with the 5b pins):
  clip        the shipped 5a baseline
  segb        segmentation, candidates only (recovery off)
  segb_adapt  segmentation + rebuild (recovery=adapt, strength 0.6)

PRE-REGISTERED PREDICTIONS (2026-07-06, before first run):
  S1: truth-harness rel_mae for segb BEATS plain opposed (0.111-0.186
      band) — candidates use real measured references where opposed
      guesses; at worst it matches (opposed is the base layer).
  S2: clipbars falsecolor for segb LANDS FAR UNDER opposed's 17.2 —
      the detail-scale invented chroma came from opposed's per-pixel
      estimates; segment-level candidates are locally constant.
      (The 5a clip default's 1.12 remains the display bar.)
  S3: segb_adapt adds signal ONLY inside all-clipped regions (clipfield
      interior), leaves partial-clip metrics ~equal to segb.
  S4: owner flips: at production intent A≈B≈C on the display path
      (recovery lives above common white); at the -1 EV pull, B/C show
      recovered window structure that A clips flat.

Run:  python3 tools/hl_segbased_experiment.py
Out:  tests/fixtures/evidence/hl_segbased_<today>.json
      ~/lrt-cinema-fixtures/verify-2026-07-06/segbased-flip/  (owner arms)
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools" / "test_articles"))

from fields import scene_field  # noqa: E402

FIX = Path.home() / "lrt-cinema-fixtures"
ART = FIX / "test-articles"
GYM_DNG = FIX / "DSC_4053.dng"
GYM_XMP = FIX / "production" / "xmp" / "DSC_4053.xmp"
DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"
)
ARTICLES = ("clipramp", "clipramp_deep", "clipfield", "clipbars")
EVIDENCE = REPO / ("tests/fixtures/evidence/"
                   f"hl_segbased_{_dt.date.today().isoformat()}.json")
FLIPDIR = FIX / "verify-2026-07-06" / "segbased-flip"
SYN_WHITE = 0.6
ARMS = ("clip", "segb", "segb_adapt")


def _reconstruct(arm: str, cfa, chan, wb_mul):
    from lrt_cinema._segbased_reconstruct import reconstruct_mosaic_segbased

    if arm == "segb":
        return reconstruct_mosaic_segbased(cfa, chan, wb_mul)
    if arm == "segb_adapt":
        return reconstruct_mosaic_segbased(cfa, chan, wb_mul,
                                           recovery="adapt", strength=0.6)
    raise ValueError(arm)


def _decode_arm(raw, arm: str, wb_mul):
    """Balanced menon RGB: clip baseline or segmentation reconstruction."""
    from colour_demosaicing import demosaicing_CFA_Bayer_Menon2007

    from lrt_cinema.pipeline import _extract_cfa

    cfa, pattern = _extract_cfa(raw)
    colors = raw.raw_colors_visible
    h, w = cfa.shape
    chan = np.where(colors[:h, :w] == 3, 1, colors[:h, :w])
    cfa = cfa * wb_mul[chan].astype(np.float32)
    if arm == "clip":
        cfa = np.minimum(cfa, np.float32(wb_mul.min()))
    else:
        cfa = _reconstruct(arm, cfa, chan, wb_mul)
    return np.maximum(np.asarray(
        demosaicing_CFA_Bayer_Menon2007(cfa, pattern), np.float32), 0.0)


def main() -> int:
    import rawpy
    from run_pressure import _score

    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.output import _prophoto_to_display
    from lrt_cinema.pipeline import (
        _asn_from_wb,
        _extract_cfa,
        _wb_mul_from_asn,
        apply_adobe_pipeline,
        read_dcp_default_black_render,
        read_dng_baseline_exposure,
    )

    flips_only = "--flips-only" in sys.argv

    manifest = json.loads((ART / "manifest.json").read_text())
    art_asn = np.asarray(manifest["asn"], np.float32)
    profile_art = parse_dcp(DCP)
    profile_art = type(profile_art)(**{**profile_art.__dict__,
                                       "forward_matrix_1": None,
                                       "forward_matrix_2": None})
    dbr = read_dcp_default_black_render(DCP)

    def to8(pp):
        return (np.clip(_prophoto_to_display(pp, "srgb"), 0, 1)
                * 255 + 0.5).astype(np.uint8)

    results: dict = {
        "design": "segmentation reconstruction vs clip baseline (menon)",
        "predictions": "S1 truth rel_mae <= opposed; S2 clipbars fc << 17.2; "
                       "S3 adapt adds only in all-clipped; S4 owner flips",
        "articles": {}, "truth_harness": {},
    }

    # ---- article arms -------------------------------------------------------
    for name in ARTICLES if not flips_only else ():
        dng = ART / f"{name}.dng"
        meta = manifest["articles"][name]
        dng_be = read_dng_baseline_exposure(dng)
        with rawpy.imread(str(dng)) as r:
            h, w = r.raw_image_visible.shape
        h -= h % 2
        w -= w % 2
        scene = scene_field(meta["spec"], h, w)
        unbal = scene * art_asn[None, None, :]
        wbm = (1.0 / art_asn) / (1.0 / art_asn)[1]
        exp8 = to8(apply_adobe_pipeline(
            camera_rgb=(np.minimum(unbal, 1.0) * wbm).astype(np.float32),
            profile=profile_art, as_shot_neutral=art_asn, scene_kelvin=5500.0,
            dng_baseline_exposure=dng_be, default_black_render=dbr,
            stop_after_stage=9))
        nclip = (unbal >= 1.0).sum(axis=-1)
        partial = (nclip > 0) & (nclip < 3)
        anyclip = nclip > 0
        row: dict = {}
        for arm in ARMS:
            with rawpy.imread(str(dng)) as raw:
                file_asn = _asn_from_wb(raw.camera_whitebalance)
                rgb = _decode_arm(raw, arm, _wb_mul_from_asn(file_asn))
            pp = apply_adobe_pipeline(
                camera_rgb=rgb, profile=profile_art, as_shot_neutral=file_asn,
                scene_kelvin=5500.0, dng_baseline_exposure=dng_be,
                default_black_render=dbr, stop_after_stage=9)
            o8 = to8(pp)
            oh, ow = o8.shape[:2]
            zone = (partial if partial.any() else anyclip)[:oh, :ow]
            s = _score(o8, exp8[:oh, :ow], True, zone if zone.any() else None)
            row[arm] = s
            print(f"{name:13s} {arm:10s} falsecolor={s['falsecolor_mean']:.3f}"
                  f"  clipzone={s.get('clipzone_chroma_mean', float('nan')):.3f}"
                  f"  de={s['de_mean']:.3f}")
        results["articles"][name] = row

    # ---- truth-harness arm (real-frame band-clip, W=0.6) --------------------
    from lrt_cinema._segbased_reconstruct import reconstruct_mosaic_segbased

    with rawpy.imread(str(GYM_DNG)) as raw:
        cfa_norm, pattern = _extract_cfa(raw)
        colors = raw.raw_colors_visible
        h, w = cfa_norm.shape
        chan = np.where(colors[:h, :w] == 3, 1, colors[:h, :w])
        wb = np.array(raw.camera_whitebalance[:3], dtype=np.float32)
        asn = 1.0 / wb
        asn = asn / asn[1]
    wb_mul = _wb_mul_from_asn(asn)
    if not flips_only:
        truth_b = (cfa_norm / SYN_WHITE) * wb_mul[chan]
        clamped_b = (np.minimum(cfa_norm / SYN_WHITE, 1.0)
                     * wb_mul[chan]).astype(np.float32)
        band = (cfa_norm >= SYN_WHITE) & (cfa_norm < 0.99)
        for nm, arr in (
            ("clamp", clamped_b),
            ("segb", reconstruct_mosaic_segbased(clamped_b, chan, wb_mul)),
            ("segb_adapt", reconstruct_mosaic_segbased(
                clamped_b, chan, wb_mul, recovery="adapt", strength=0.6)),
        ):
            err = (arr - truth_b) / np.maximum(truth_b, 1e-6)
            results["truth_harness"][nm] = {
                "rel_mae": float(np.abs(err[band]).mean()),
                "bias": float(err[band].mean()),
            }
            print(f"truth {nm:10s} "
                  f"rel_mae={results['truth_harness'][nm]['rel_mae']:.4f}")

    # ---- owner flips (real frame; production intent AND a -1 EV pull) -------
    from PIL import Image

    from lrt_cinema.develop_ops import apply_develop_ops
    from lrt_cinema.interpolation import LR_LOCAL_EXPOSURE_SCALE
    from lrt_cinema.ir import RenderIntent
    from lrt_cinema.pipeline import kelvin_to_neutral
    from lrt_cinema.xmp_parser import parse_xmp_file

    FLIPDIR.mkdir(parents=True, exist_ok=True)
    profile = parse_dcp(DCP)
    ops, _kf, _dfk, _rating, mask_offsets = parse_xmp_file(GYM_XMP)
    ops = replace(ops, scene_exposure_ev=LR_LOCAL_EXPOSURE_SCALE
                  * sum(ev for _k, ev in mask_offsets))
    scene_kelvin = float(ops.temperature_k)
    render_asn = kelvin_to_neutral(profile, scene_kelvin, float(ops.tint or 0.0))
    render_wb = _wb_mul_from_asn(render_asn)
    dng_be = read_dng_baseline_exposure(GYM_DNG)
    tags = {"clip": "A-clip-shipped", "segb": "B-segb-candidates",
            "segb_adapt": "C-segb-rebuild"}
    from lrt_cinema._fc_suppress import suppress_false_colour
    for arm in ARMS:
        with rawpy.imread(str(GYM_DNG)) as raw:
            cam = _decode_arm(raw, arm, render_wb)
        # production display path applies fc-suppress 3 after the decode —
        # the fc3 set is the production-accurate comparison (the plain set
        # isolates the raw reconstruction behaviour)
        for fc_tag, cam_v in (("", cam),
                              ("_fc3", suppress_false_colour(
                                  cam, passes=3, blur=True))):
            for pull, ptag in ((0.0, "intent"), (-1.0, "pull1ev")):
                total_ev = ops.scene_exposure_ev + ops.exposure_ev + pull
                cam_g = (cam_v * np.float32(2.0 ** total_ev)
                         if total_ev != 0.0 else cam_v)
                pp = apply_adobe_pipeline(
                    camera_rgb=cam_g, profile=profile,
                    as_shot_neutral=render_asn,
                    scene_kelvin=scene_kelvin, dng_baseline_exposure=dng_be,
                    default_black_render=dbr, stop_after_stage=9)
                pp = apply_develop_ops(pp, ops, RenderIntent.FAITHFUL,
                                       master_look="bake",
                                       capture_sharpen="off")
                srgb8 = (np.clip(_prophoto_to_display(pp, "srgb"), 0, 1)
                         * 255 + 0.5).astype(np.uint8)[8:-8, 8:-8]
                Image.fromarray(srgb8).save(
                    FLIPDIR / f"DSC_4053_{ptag}{fc_tag}_{tags[arm]}.png")
                print(f"flip: wrote {ptag}{fc_tag}/{tags[arm]}")

    (FLIPDIR / "README.txt").write_text(
        "Segmentation-based reconstruction — owner flip (survey #2)\n"
        "===========================================================\n"
        "DSC_4053, menon on ALL arms (isolates reconstruction; the\n"
        "production amaze default is clip-path-only), native res.\n\n"
        "Two exposure sets:\n"
        "  *_intent_*   the production develop intent (unchanged)\n"
        "  *_pull1ev_*  the same intent pulled -1 EV — recovered\n"
        "               highlight data BECOMES VISIBLE here\n\n"
        "  A-clip-shipped     the shipped clip-to-common-white default\n"
        "  B-segb-candidates  dt-class segmentation, candidates only\n"
        "  C-segb-rebuild     + all-clipped rebuild (adapt, strength .6)\n\n"
        "JUDGE: at intent, A/B/C should be near-identical (recovery\n"
        "lives above display white). At -1 EV: do B/C recover credible\n"
        "window structure/rolloff that A renders as flat grey? Any\n"
        "colour artifacts in B/C (the class that killed opposed)?\n\n"
        "*_fc3_* set: the same arms WITH the production fc-suppress 3\n"
        "(3-pass chroma-median + blur) applied — the production-accurate\n"
        "comparison. The owner-observed clip-edge saturation boost in\n"
        "B/C should be judged on THIS set; the plain set isolates the\n"
        "raw reconstruction behaviour.\n")
    if not flips_only:
        EVIDENCE.write_text(json.dumps(results, indent=1))
        print(f"\nevidence -> {EVIDENCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
