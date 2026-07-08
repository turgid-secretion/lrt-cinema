"""Scaler-wrapped AMaZE (`amaze_demosaic_headroom`) — deciding experiment.

THE QUESTION (owner GO 2026-07-07, after the cross-engine canon read):
does the dt-convention normalize→demosaic→denormalize wrapper carry
highlight-reconstruction headroom through OUR amaze losslessly, at what
detail-scale cost, and does pre-demosaic raw-CA correction reduce the
clip-edge chroma that reconstruction ingests (the owner-flagged
saturation boost)?

ARMS
  truth:    {clamp, segb, segb_adapt} x {direct, wrapped} amaze — RGB-level
            recovery error at the band sites with KNOWN truth (the task-16
            harness geometry, SYN_WHITE 0.6)
  articles: {clip, segb} x {fc0, fc3} through WRAPPED amaze on the four
            clip articles (clip arm degenerates to the direct call by
            construction — its numbers must reproduce the task-16 pins)
  flips:    gym frame, wrapped amaze, production fc3:
            A-clip / B-segb-rebuild / C-segb-rebuild-CA2 x {intent, -1 EV}
            (CA order: ca_correct BEFORE reconstruction — mechanism-
            targeted, diverges from dt's highlights@4→cacorrect@5 slot
            order; recorded as an open ordering question for wiring)

PRE-REGISTERED PREDICTIONS (2026-07-07, before first run):
  W1 (lossless carry): wrapped-amaze RGB truth rel_mae lands within 0.01
      of the mosaic-level pins — clamp 0.186 / segb 0.090 / segb_adapt
      0.0749 — while DIRECT amaze on the recon arms sits >= 0.25
      (recovery destroyed at the [0,1] port clamp; smoke: 0.2676).
  W2 (detail-scale cost): clipbars wrapped segb fc0 lands in the
      headroom-reconstruction class 10-25 (menon-segb 17.16, clamped-
      amaze 17.71 — the explosion is reconstruction-inherent, not
      clamp-suppressed); fc3 cuts it >= 50 %; the clip arm stays
      <= 0.05 (degenerate wrapper = the pinned 0.006-class).
  W3 (smooth clips): wrapped segb on clipramp/clipramp_deep/clipfield
      lands in the menon-segb class (clipramp clip-zone chroma ~3.5-
      class; task-16 clamp-identity NO LONGER holds — that identity was
      the clamp's doing); falsecolor <= 1.2x the menon-segb pins (no
      new amaze-specific pathology).
  W4 (CA interaction): on the real frame, CA2-before-reconstruction
      REDUCES clip-boundary chroma vs no-CA (proxy: mean boundary-band
      saturation on the pull1ev fc3 renders, C < B). Owner flips carry
      the rank-1 verdict.
  W5 (recovery visible): pull1ev wrapped-amaze B/C diverge from A at
      >= 50k px >8/255 (task-16 clamped-amaze: 13k; menon: 112k) —
      recovered window structure is display-visible through amaze for
      the first time.
  W6 (intent sanity): gym intent fc3 spot dE vs the approved JPG stays
      0.570-class on ALL arms (recovery lives above display white at
      production intent; the wrapper must not move the north star).

Menon / task-16 cross-references (pinned, NOT re-run): truth mosaic
0.186/0.0903/0.0749; clipbars segb fc0 menon 17.156, clamped-amaze
17.707, fc3 3.345; gym clip+fc3 0.5707; flip divergence px>8/255
clamped-amaze 13k / menon 112k.

Run:  python3 tools/hl_amaze_wrapper_experiment.py [--stage truth|articles|flips]
Out:  tests/fixtures/evidence/hl_amaze_wrapper_<today>.json
      ~/lrt-cinema-fixtures/verify-<today>/amaze-wrapper-flip/

NOTE (2026-07-07, owner-verdicted): segbased arms pass site_guard=2.0
(the isolated-site guard recipe — CLAIMS D/G/H verdict). Evidence rows
pinned BEFORE the guard regenerate with site_guard=0.
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
GYM_JPG = FIX / "production" / "lrt-jpg" / "LRT_00001.jpg"
DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"
)
ARTICLES = ("clipramp", "clipramp_deep", "clipfield", "clipbars")
TODAY = _dt.date.today().isoformat()
EVIDENCE = REPO / f"tests/fixtures/evidence/hl_amaze_wrapper_{TODAY}.json"
FLIPDIR = FIX / f"verify-{TODAY}" / "amaze-wrapper-flip"
SYN_WHITE = 0.6
FC_LEVELS = (0, 3)
DOWN, CROP = 6, 8
PINNED = {
    "truth_mosaic": {"clamp": 0.186, "segb": 0.0903, "segb_adapt": 0.0749},
    "clipbars_segb_fc0": {"menon": 17.156, "amaze_clamped": 17.707},
    "gym_clip_fc3": 0.5707,
    "flip_px_gt8": {"amaze_clamped": 13029, "menon": 111924},
}


def _recon(arm: str, cfa, chan, wb_mul):
    from lrt_cinema._segbased_reconstruct import reconstruct_mosaic_segbased

    if arm == "segb":
        return reconstruct_mosaic_segbased(cfa, chan, wb_mul, site_guard=2.0)
    if arm.startswith("segb_adapt"):
        return reconstruct_mosaic_segbased(cfa, chan, wb_mul, site_guard=2.0,
                                           recovery="adapt", strength=0.6)
    raise ValueError(arm)


def _demosaic(cfa, pattern, wb_mul, wrapped: bool):
    from lrt_cinema._amaze_demosaic import (
        amaze_demosaic,
        amaze_demosaic_headroom,
    )

    clip_pt = float(wb_mul.min())
    if wrapped:
        return amaze_demosaic_headroom(cfa, pattern, clip_pt=clip_pt)
    return amaze_demosaic(cfa, pattern, clip_pt=clip_pt)


def _fc(rgb, passes: int):
    if passes <= 0:
        return rgb
    from lrt_cinema._fc_suppress import suppress_false_colour
    return suppress_false_colour(rgb, passes=passes, blur=True)


def _block_down(a, k):
    h, w = a.shape[:2]
    h2, w2 = (h // k) * k, (w // k) * k
    return a[:h2, :w2].reshape(h2 // k, k, w2 // k, k, -1).mean(axis=(1, 3))


def _compare_lrt(srgb_float: np.ndarray, lrt_jpg: Path) -> dict:
    import colour
    import imageio.v3 as iio

    d65 = np.array([0.3127, 0.3290])
    lrt = iio.imread(lrt_jpg).astype(np.float32) / 255.0
    curd = _block_down(srgb_float[CROP:-CROP, CROP:-CROP], DOWN)
    lrtd = _block_down(lrt, DOWN)

    def lab(a):
        lin = colour.models.eotf_sRGB(a)
        return colour.XYZ_to_Lab(
            colour.RGB_to_XYZ(lin, "sRGB", apply_cctf_decoding=False),
            illuminant=d65)

    de = colour.delta_E(lab(curd), lab(lrtd), method="CIE 2000")
    return {"de_lrt": round(float(de.mean()), 4),
            "p95": round(float(np.percentile(de, 95)), 3)}


def _gym_mosaic():
    import rawpy

    from lrt_cinema.pipeline import _extract_cfa, _wb_mul_from_asn

    with rawpy.imread(str(GYM_DNG)) as raw:
        cfa, pattern = _extract_cfa(raw)
        colors = raw.raw_colors_visible
        h, w = cfa.shape
        chan = np.where(colors[:h, :w] == 3, 1, colors[:h, :w])
        wb = np.array(raw.camera_whitebalance[:3], np.float32)
        asn = 1.0 / wb
        asn = asn / asn[1]
    return cfa, pattern, chan, _wb_mul_from_asn(asn)


def run_truth(results: dict) -> None:
    """W1: RGB-level recovery error, direct vs wrapped amaze."""
    cfa_norm, pattern, chan, wb_mul = _gym_mosaic()
    truth_b = (cfa_norm / SYN_WHITE) * wb_mul[chan]
    clamped = (np.minimum(cfa_norm / SYN_WHITE, 1.0)
               * wb_mul[chan]).astype(np.float32)
    band = (cfa_norm >= SYN_WHITE) & (cfa_norm < 0.99)
    ys, xs = np.nonzero(band)
    tv = truth_b[ys, xs]
    cch = chan[ys, xs]
    mosaics = {
        "clamp": clamped,
        "segb": _recon("segb", clamped, chan, wb_mul),
        "segb_adapt": _recon("segb_adapt", clamped, chan, wb_mul),
    }
    for nm, mos in mosaics.items():
        row = {}
        for tag, wrapped in (("direct", False), ("wrapped", True)):
            rgb = _demosaic(mos, pattern, wb_mul, wrapped)
            got = rgb[ys, xs, cch]
            rel = np.abs(got - tv) / np.maximum(tv, 1e-6)
            row[tag] = {"rel_mae": round(float(rel.mean()), 4),
                        "out_max": round(float(rgb.max()), 3)}
            print(f"truth {nm:10s} {tag:8s} rel_mae={row[tag]['rel_mae']:.4f}"
                  f"  out_max={row[tag]['out_max']:.3f}")
        results["truth_rgb"][nm] = row


def run_articles(results: dict) -> None:
    """W2/W3: article matrix {clip, segb} x {fc0, fc3}, wrapped amaze."""
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
        for arm in ("clip", "segb"):
            with rawpy.imread(str(dng)) as raw:
                file_asn = _asn_from_wb(raw.camera_whitebalance)
                wbm_f = _wb_mul_from_asn(file_asn)
                cfa, pattern = _extract_cfa(raw)
                colors = raw.raw_colors_visible
                hh, ww = cfa.shape
                chan = np.where(colors[:hh, :ww] == 3, 1, colors[:hh, :ww])
                cfa = cfa * wbm_f[chan].astype(np.float32)
                if arm == "clip":
                    cfa = np.minimum(cfa, np.float32(wbm_f.min()))
                else:
                    cfa = _recon("segb", cfa, chan, wbm_f)
            rgb = _demosaic(cfa, pattern, wbm_f, wrapped=True)
            for fc in FC_LEVELS:
                pp = apply_adobe_pipeline(
                    camera_rgb=_fc(rgb, fc), profile=profile_art,
                    as_shot_neutral=file_asn, scene_kelvin=5500.0,
                    dng_baseline_exposure=dng_be,
                    default_black_render=dbr, stop_after_stage=9)
                o8 = to8(pp)
                oh, ow = o8.shape[:2]
                zone = (partial if partial.any() else anyclip)[:oh, :ow]
                s = _score(o8, exp8[:oh, :ow], True,
                           zone if zone.any() else None)
                row[f"{arm}_fc{fc}"] = s
                print(f"{name:13s} {arm:5s} fc{fc} "
                      f"falsecolor={s['falsecolor_mean']:.3f}  clipzone="
                      f"{s.get('clipzone_chroma_mean', float('nan')):.3f}"
                      f"  de={s['de_mean']:.3f}")
        results["articles"][name] = row


def run_flips(results: dict) -> None:
    """W4/W5/W6: gym flips (wrapped amaze, fc3) + proxies + intent spot."""
    import rawpy
    from PIL import Image

    from lrt_cinema._ca_correct import ca_correct_mosaic
    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.develop_ops import apply_develop_ops
    from lrt_cinema.interpolation import LR_LOCAL_EXPOSURE_SCALE
    from lrt_cinema.ir import RenderIntent
    from lrt_cinema.output import _prophoto_to_display
    from lrt_cinema.pipeline import (
        _extract_cfa,
        _wb_mul_from_asn,
        apply_adobe_pipeline,
        kelvin_to_neutral,
        read_dcp_default_black_render,
        read_dng_baseline_exposure,
    )
    from lrt_cinema.xmp_parser import parse_xmp_file

    FLIPDIR.mkdir(parents=True, exist_ok=True)
    profile = parse_dcp(DCP)
    dbr = read_dcp_default_black_render(DCP)
    ops, _kf, _dfk, _rating, mask_offsets = parse_xmp_file(GYM_XMP)
    ops = replace(ops, scene_exposure_ev=LR_LOCAL_EXPOSURE_SCALE
                  * sum(ev for _k, ev in mask_offsets))
    scene_kelvin = float(ops.temperature_k)
    render_asn = kelvin_to_neutral(profile, scene_kelvin,
                                   float(ops.tint or 0.0))
    render_wb = _wb_mul_from_asn(render_asn)
    dng_be = read_dng_baseline_exposure(GYM_DNG)

    with rawpy.imread(str(GYM_DNG)) as raw:
        cfa0, pattern = _extract_cfa(raw)
        colors = raw.raw_colors_visible
        h, w = cfa0.shape
        chan = np.where(colors[:h, :w] == 3, 1, colors[:h, :w])
    bal = cfa0 * render_wb[chan].astype(np.float32)

    def arm_mosaic(arm: str):
        m = bal
        if arm.endswith("ca2"):
            # CA BEFORE reconstruction (mechanism-targeted: realigned
            # channels -> cleaner near-clip boundary chroma for the
            # reconstruction to ingest). dt's slot order is highlights@4
            # -> cacorrect@5; the divergence is recorded as an open
            # ordering question for the wiring session.
            m = ca_correct_mosaic(m, pattern, iterations=2,
                                  scale=float(render_wb.max()))
        if arm == "clip":
            return np.minimum(m, np.float32(render_wb.min()))
        return _recon("segb_adapt", m, chan, render_wb)

    tags = {"clip": "A-clip-shipped",
            "segb_adapt": "B-segb-rebuild",
            "segb_adapt_ca2": "C-segb-rebuild-CA2"}
    boundary = None
    pull_u8 = {}
    for arm, tag in tags.items():
        mos = arm_mosaic(arm)
        cam = _fc(_demosaic(mos, pattern, render_wb, wrapped=True), 3)
        for pull, ptag in ((0.0, "intent"), (-1.0, "pull1ev")):
            total_ev = ops.scene_exposure_ev + ops.exposure_ev + pull
            cam_g = (cam * np.float32(2.0 ** total_ev)
                     if total_ev != 0.0 else cam)
            pp = apply_adobe_pipeline(
                camera_rgb=cam_g, profile=profile,
                as_shot_neutral=render_asn, scene_kelvin=scene_kelvin,
                dng_baseline_exposure=dng_be, default_black_render=dbr,
                stop_after_stage=9)
            pp = apply_develop_ops(pp, ops, RenderIntent.FAITHFUL,
                                   master_look="bake",
                                   capture_sharpen="off")
            srgb = np.clip(_prophoto_to_display(pp, "srgb"), 0.0, 1.0)
            u8 = (srgb * 255 + 0.5).astype(np.uint8)[8:-8, 8:-8]
            Image.fromarray(u8).save(FLIPDIR / f"DSC_4053_{ptag}_{tag}.png")
            print(f"flip: wrote {ptag}/{tag}")
            if ptag == "intent":
                results["gym_spot"][arm] = _compare_lrt(srgb, GYM_JPG)
                print(f"  intent spot dE {results['gym_spot'][arm]['de_lrt']}")
            else:
                pull_u8[arm] = u8

    # W4 proxy: mean saturation in the dilated clip-boundary band (pull set)
    from scipy.ndimage import binary_dilation
    clipped_any = (bal >= 0.99 * render_wb[chan]).astype(np.uint8)
    bnd = binary_dilation(clipped_any, iterations=3) & ~binary_dilation(
        clipped_any, iterations=1).astype(bool)
    bnd = bnd[8:-8, 8:-8]
    if boundary is None:
        boundary = bnd
    for arm, u8 in pull_u8.items():
        f = u8.astype(np.float32) / 255.0
        mx = f.max(axis=-1)
        mn = f.min(axis=-1)
        sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
        results["w4_boundary_sat"][arm] = round(float(sat[boundary].mean()), 5)
    # W5 proxy: pull-set divergence from the A arm
    a = pull_u8["clip"].astype(np.int16)
    for arm in ("segb_adapt", "segb_adapt_ca2"):
        d = np.abs(pull_u8[arm].astype(np.int16) - a).max(axis=-1)
        results["w5_pull_divergence"][arm] = {
            "px_gt0": int((d > 0).sum()), "px_gt8": int((d > 8).sum()),
            "max": int(d.max())}
    print("w4 boundary sat:", results["w4_boundary_sat"])
    print("w5 divergence:", results["w5_pull_divergence"])

    (FLIPDIR / "README.txt").write_text(
        "Scaler-wrapped AMaZE + segbased recovery — owner flip (wrapper GO)\n"
        "===================================================================\n"
        "DSC_4053, WRAPPED amaze (headroom survives the demosaic now),\n"
        "production fc3, native res, zero scaling. 6 files.\n\n"
        "Arms:\n"
        "  A-clip-shipped       clip-to-common-white (production today)\n"
        "  B-segb-rebuild       segmentation recovery (adapt 0.6)\n"
        "  C-segb-rebuild-CA2   + raw CA correction (2 iter) BEFORE the\n"
        "                       reconstruction — targets the clip-edge\n"
        "                       saturation boost you flagged 07-06 (the\n"
        "                       recovery ingests boundary chroma; CA\n"
        "                       realignment cleans that boundary)\n\n"
        "Exposure sets:\n"
        "  *_intent_*   production intent — A/B/C should look identical\n"
        "               (recovery lives above display white)\n"
        "  *_pull1ev_*  -1 EV — RECOVERY IS NOW VISIBLE through amaze\n"
        "               (unlike the 07-07 matrix-flip set, where the\n"
        "               port clamp destroyed it)\n\n"
        "JUDGE at 1:1 on the blown windows (pull set):\n"
        "  1. B vs A: credible recovered structure/rolloff vs flat grey?\n"
        "  2. B vs C: does CA-before-recovery reduce the coloured rims\n"
        "     at clip edges? (deterministic proxy in the evidence JSON)\n"
        "  3. Any new artifact class vs the 07-06 menon segbased flips?\n")


def main() -> int:
    import warnings
    warnings.filterwarnings("ignore")

    stage = None
    if "--stage" in sys.argv:
        stage = sys.argv[sys.argv.index("--stage") + 1]

    results: dict = {
        "design": "scaler-wrapped amaze: truth {3 recon x direct/wrapped}; "
                  "articles {clip,segb}x{fc0,fc3}; gym flips A/B/C-CA2",
        "predictions": "W1 wrapped rel_mae ~ mosaic pins, direct >= 0.25; "
                       "W2 clipbars segb fc0 in 10-25, fc3 -50%, clip <= "
                       "0.05; W3 smooth clips ~ menon-segb class; W4 CA2 "
                       "boundary sat < no-CA; W5 pull divergence >= 50k "
                       "px>8; W6 intent spot 0.570-class all arms",
        "pinned_crossrefs": PINNED,
        "regen": "python3 tools/hl_amaze_wrapper_experiment.py",
        "truth_rgb": {}, "articles": {}, "gym_spot": {},
        "w4_boundary_sat": {}, "w5_pull_divergence": {},
    }
    if stage in (None, "truth"):
        run_truth(results)
    if stage in (None, "articles"):
        run_articles(results)
    if stage in (None, "flips"):
        run_flips(results)
    out = EVIDENCE if stage is None else EVIDENCE.with_name(
        EVIDENCE.stem + f"_{stage}.json")
    out.write_text(json.dumps(results, indent=1))
    print(f"\nevidence -> {out}\nflips -> {FLIPDIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
