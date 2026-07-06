"""Luminance-led NEUTRAL recovery — deciding experiment (survey #1).

THE QUESTION: does `reconstruct_mosaic_neutral` (opposed's luminance
estimate, channel-consistent target, NO chrominance tint — the
Adobe-documented behaviour class) recover highlight structure WITHOUT the
chroma artifacts that killed opposed in the owner's eyes?

ARMS (menon demosaic held constant): clip (shipped 5a baseline) ·
neutral (the candidate). Opposed's numbers are already on file
(hl_reconstruct_5b / hl_truth_harness) for three-way comparison.

PRE-REGISTERED PREDICTIONS (2026-06-12, before first run):
  N1: clipramp clip-zone chroma DROPS from the clip baseline 3.03 toward
      the LR-product 1.07 (neutral-pull = ACR's rolloff style; opposed
      moved it the WRONG way, 3.03→3.52).
  N2: clipbars falsecolor stays ≤ 2 (channel-consistent targets add no
      detail-scale chroma; opposed exploded it to 17.2).
  N3: truth harness rel_mae lands BETWEEN clamp and opposed (recovers
      luminance; deliberately forgoes chroma fidelity on tinted truth).
  N4: real-frame flips: windows stay neutral with recovered rolloff
      (owner eyes decide).

Run:  python3 tools/hl_neutral_experiment.py
Out:  tests/fixtures/evidence/hl_neutral_2026-06-12.json
      ~/lrt-cinema-fixtures/verify-2026-06-12/neutral-flip/  (owner arms)
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
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
EVIDENCE = REPO / "tests/fixtures/evidence/hl_neutral_2026-06-12.json"
FLIPDIR = FIX / "verify-2026-06-12" / "neutral-flip"
SYN_WHITE = 0.6


def _decode_arm(raw, arm: str, wb_mul):
    """Balanced menon RGB for an arm: clip baseline or neutral recovery."""
    from colour_demosaicing import demosaicing_CFA_Bayer_Menon2007

    from lrt_cinema._opposed_reconstruct import reconstruct_mosaic_neutral
    from lrt_cinema.pipeline import _extract_cfa

    cfa, pattern = _extract_cfa(raw)
    colors = raw.raw_colors_visible
    h, w = cfa.shape
    chan = np.where(colors[:h, :w] == 3, 1, colors[:h, :w])
    cfa = cfa * wb_mul[chan].astype(np.float32)
    if arm == "clip":
        cfa = np.minimum(cfa, np.float32(wb_mul.min()))
    elif arm == "neutral":
        # v2 (the v1 lesson): CLAMP FIRST at the common white — every 5a
        # guarantee holds — then lift clipped LOCATIONS channel-consistently
        # above the plateau (mask computed on the pre-clamp mosaic).
        clips = np.float32(0.995) * wb_mul[chan].astype(np.float32)
        pre_mask = cfa >= clips
        cfa = np.minimum(cfa, np.float32(wb_mul.min()))
        cfa = reconstruct_mosaic_neutral(cfa, chan, wb_mul, clipped=pre_mask)
    else:
        raise ValueError(arm)
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
        "design": "luminance-led neutral recovery vs clip baseline (menon)",
        "predictions": "N1 clipramp clip-zone -> toward 1.07; N2 clipbars "
                       "falsecolor <= 2; N3 truth rel_mae between clamp and "
                       "opposed; N4 owner flips",
        "articles": {}, "truth_harness": {},
    }

    # ---- article arms -------------------------------------------------------
    for name in ARTICLES:
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
        for arm in ("clip", "neutral"):
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
            print(f"{name:13s} {arm:8s} falsecolor={s['falsecolor_mean']:.3f}"
                  f"  clipzone={s.get('clipzone_chroma_mean', float('nan')):.3f}"
                  f"  de={s['de_mean']:.3f}")
        results["articles"][name] = row

    # ---- truth-harness arm (real-frame band-clip, W=0.6) -------------------
    from lrt_cinema._opposed_reconstruct import reconstruct_mosaic_neutral
    with rawpy.imread(str(GYM_DNG)) as raw:
        cfa_norm, pattern = _extract_cfa(raw)
        colors = raw.raw_colors_visible
        h, w = cfa_norm.shape
        chan = np.where(colors[:h, :w] == 3, 1, colors[:h, :w])
        wb = np.array(raw.camera_whitebalance[:3], dtype=np.float32)
        asn = 1.0 / wb
        asn = asn / asn[1]
    wb_mul = _wb_mul_from_asn(asn)
    truth_b = (cfa_norm / SYN_WHITE) * wb_mul[chan]
    clamped_b = np.minimum(cfa_norm / SYN_WHITE, 1.0) * wb_mul[chan]
    band = (cfa_norm >= SYN_WHITE) & (cfa_norm < 0.99)
    pre_mask_t = (cfa_norm / SYN_WHITE) * wb_mul[chan] >= 0.995 * wb_mul[chan]
    rec = reconstruct_mosaic_neutral(clamped_b, chan, wb_mul, clipped=pre_mask_t)
    for nm, arr in (("clamp", clamped_b), ("neutral", rec)):
        err = (arr - truth_b) / np.maximum(truth_b, 1e-6)
        results["truth_harness"][nm] = {
            "rel_mae": float(np.abs(err[band]).mean()),
            "bias": float(err[band].mean()),
        }
        print(f"truth {nm:8s} rel_mae={results['truth_harness'][nm]['rel_mae']:.4f}")

    # ---- owner flips (real frame, full production intent) -------------------
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
    for arm in ("clip", "neutral"):
        with rawpy.imread(str(GYM_DNG)) as raw:
            cam = _decode_arm(raw, arm, render_wb)
        total_ev = ops.scene_exposure_ev + ops.exposure_ev
        if total_ev != 0.0:
            cam = cam * np.float32(2.0 ** total_ev)
        pp = apply_adobe_pipeline(
            camera_rgb=cam, profile=profile, as_shot_neutral=render_asn,
            scene_kelvin=scene_kelvin, dng_baseline_exposure=dng_be,
            default_black_render=dbr, stop_after_stage=9)
        pp = apply_develop_ops(pp, ops, RenderIntent.FAITHFUL,
                               master_look="bake", capture_sharpen="off")
        srgb8 = (np.clip(_prophoto_to_display(pp, "srgb"), 0, 1)
                 * 255 + 0.5).astype(np.uint8)[8:-8, 8:-8]
        tag = "A-clip-shipped" if arm == "clip" else "B-neutral-recovery"
        Image.fromarray(srgb8).save(FLIPDIR / f"DSC_4053_{tag}.png")
        print(f"flip: wrote {tag}")

    (FLIPDIR / "README.txt").write_text(
        "Luminance-led NEUTRAL recovery — owner flip (survey shortlist #1)\n"
        "==================================================================\n"
        "DSC_4053, full production intent, menon, native res, zero scaling.\n\n"
        "  DSC_4053_A-clip-shipped.png      the shipped clip default\n"
        "  DSC_4053_B-neutral-recovery.png  luminance-led neutral recovery:\n"
        "      opposed's luminance estimate, channel-consistent target, NO\n"
        "      chrominance tint (Adobe's documented recovery style).\n\n"
        "JUDGE: does B recover believable window rolloff/structure while\n"
        "staying NEUTRAL (the artifact class you rejected in hl-flip B/C\n"
        "must be absent)?\n"
    )
    EVIDENCE.write_text(json.dumps(results, indent=1))
    print(f"\nevidence → {EVIDENCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
