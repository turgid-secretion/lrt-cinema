"""fc-suppress x highlight-reconstruction matrix on the PRODUCTION pipeline.

Task 16 (owner directive 2026-07-06, CLAIMS row "OWNER VERDICT ROUND"):
the 07-06 segbased experiment measured {clip, segb, segb_adapt} under
MENON (comparability with the 5b pins). Production is now AMAZE + fc3.
This measures the full matrix {clip, segb, segb_adapt} x {fc0, fc3}
under amaze — the pipeline the owner actually ships.

MECHANISM ON RECORD (drives the predictions): `_amaze_rggb` final-clamps
its output to [0, 1] (`np.clip(rgb, 0.0, 1.0)`, _amaze_demosaic.py) and
ULIM-bounds above-clip interpolants. In balanced G-normalised units the
common white IS 1.0 (wb_mul.min() = G = 1.0), so reconstructed content
above common white CANNOT survive the amaze decode — unlike menon, which
preserves headroom and let opposed's invented chroma reach the display
(clipbars falsecolor 17.16). Above-clip values DO feed the interpolation
of sub-white neighbours before the final clamp (boundary leakage), so
segb arms are near-clip, not byte-identical.

PRE-REGISTERED PREDICTIONS (2026-07-07, before first run):
  M0 (calibration): gym clip+fc3 through this direct construction
      reproduces the pinned CLI amaze+fc3 frame-1 dE 0.582-class
      (seq_spot_amaze_2026-07-06) within ~0.02; clip+fc0 lands worse
      by roughly the suppression margin (menon precedent 0.626 vs
      0.586 -> predict amaze fc0 ~ 0.60-0.64).
  M1 (clamp neutralizes recovery; the synthetic sanity gate): segb /
      segb_adapt article metrics land NEAR clip's — clipbars falsecolor
      segb+amaze ~ 0.01-class, NOT menon's 17.16. GATE: reconstructed
      clipramp through amaze must score falsecolor within 2x of
      clip+amaze; a blow-up = amaze-on-reconstructed-mosaic pathology,
      stop and report.
  M2 (fc3 effect): fc3 improves-or-holds falsecolor on every
      article x arm; largest absolute cut where residual invented
      chroma exists (clipramp family), ~nil on clipbars/clipfield
      (already ~0 under amaze).
  M3 (truth harness): mosaic-level rel_mae re-pins 0.186 / 0.0903 /
      0.0749 (clamp / segb / segb_adapt) EXACTLY — the measurement
      lives at CFA sites pre-demosaic, so the demosaic and fc axes
      collapse by construction (recorded in the JSON; an RGB-level
      truth probe through amaze would only measure the [0,1] clamp).
  M4 (gym spot matrix): fc dominates, reconstruction ~irrelevant:
      |segb - clip| <= 0.005 dE at each fc level (M1 mechanism is
      invisible at 6x block-mean); fc3 arms at the 0.582 pinned class;
      fc0 arms worse by ~0.02-0.05.
  M5 (owner flips): B/C vs A near-indistinguishable at intent AND at
      the -1 EV pull (recovery above common white does not survive the
      amaze decode) — the owner-observed clip-edge saturation boost in
      the 07-06 menon flips should be ABSENT here. If confirmed by
      eyes, the segbased CA-edge concern is MOOT on the production
      display path; recovery stays a headroom-path (menon/EXR)
      capability.

Menon cross-reference (pinned, hl_segbased_2026-07-06.json — NOT re-run):
  clipbars falsecolor clip 1.119 / segb 17.156; truth rel_mae clamp
  0.186 / segb 0.0903 / segb_adapt 0.0749.

Run:  python3 tools/hl_matrix_experiment.py [--stage articles|truth|gym|flips]
      (no --stage = all, in that order; articles carry the M1 gate)
Out:  tests/fixtures/evidence/hl_matrix_<today>.json
      ~/lrt-cinema-fixtures/verify-<today>/matrix-flip/   (owner arms)
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
EVIDENCE = REPO / f"tests/fixtures/evidence/hl_matrix_{TODAY}.json"
FLIPDIR = FIX / f"verify-{TODAY}" / "matrix-flip"
SYN_WHITE = 0.6
ARMS = ("clip", "segb", "segb_adapt")
FC_LEVELS = (0, 3)
DOWN, CROP = 6, 8          # seq_lrt_compare's validated comparison geometry
MENON_CROSSREF = {          # pinned hl_segbased_2026-07-06.json — not re-run
    "clipbars_falsecolor": {"clip": 1.119, "segb": 17.156},
    "truth_rel_mae": {"clamp": 0.186, "segb": 0.0903, "segb_adapt": 0.0749},
}


def _reconstruct(arm: str, cfa, chan, wb_mul):
    from lrt_cinema._segbased_reconstruct import reconstruct_mosaic_segbased

    if arm == "segb":
        return reconstruct_mosaic_segbased(cfa, chan, wb_mul)
    if arm == "segb_adapt":
        return reconstruct_mosaic_segbased(cfa, chan, wb_mul,
                                           recovery="adapt", strength=0.6)
    raise ValueError(arm)


def _decode_arm(raw, arm: str, wb_mul):
    """Balanced AMAZE RGB (production demosaic): clip baseline or
    segmentation reconstruction fed to amaze at the production clip_pt."""
    from lrt_cinema._amaze_demosaic import amaze_demosaic
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
    return amaze_demosaic(cfa, pattern, clip_pt=float(wb_mul.min()))


def _fc(rgb, passes: int):
    """Production fc-suppress (render_frame applies it post-demosaic,
    BEFORE the scene-referred 2^EV gain; the median + blur commute with
    a positive scalar gain, so suppress-once-then-gain is exact)."""
    if passes <= 0:
        return rgb
    from lrt_cinema._fc_suppress import suppress_false_colour
    return suppress_false_colour(rgb, passes=passes, blur=True)


def _block_down(a, k):
    h, w = a.shape[:2]
    h2, w2 = (h // k) * k, (w // k) * k
    return a[:h2, :w2].reshape(h2 // k, k, w2 // k, k, -1).mean(axis=(1, 3))


def _compare_lrt(srgb_float: np.ndarray, lrt_jpg: Path) -> dict:
    """seq_lrt_compare geometry (crop 8, block-mean 6) vs the approved JPG."""
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
    ours_lin = colour.models.eotf_sRGB(curd)
    tgt_lin = colour.models.eotf_sRGB(lrtd)
    gain = [float((ours_lin[..., c].ravel() @ tgt_lin[..., c].ravel())
                  / (ours_lin[..., c].ravel() @ ours_lin[..., c].ravel()))
            for c in range(3)]
    return {"de_lrt": round(float(de.mean()), 4),
            "p95": round(float(np.percentile(de, 95)), 3),
            "gain": [round(g, 4) for g in gain]}


def run_articles(results: dict) -> bool:
    """Article matrix; returns False on the M1 pathology gate."""
    import rawpy
    from run_pressure import _score

    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.output import _prophoto_to_display
    from lrt_cinema.pipeline import (
        _asn_from_wb,
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
        for arm in ARMS:
            with rawpy.imread(str(dng)) as raw:
                file_asn = _asn_from_wb(raw.camera_whitebalance)
                rgb = _decode_arm(raw, arm, _wb_mul_from_asn(file_asn))
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
                print(f"{name:13s} {arm:10s} fc{fc} "
                      f"falsecolor={s['falsecolor_mean']:.3f}  clipzone="
                      f"{s.get('clipzone_chroma_mean', float('nan')):.3f}"
                      f"  de={s['de_mean']:.3f}")
        results["articles"][name] = row
        if name == "clipramp":                       # M1 pathology gate
            base = row["clip_fc0"]["falsecolor_mean"]
            rec = row["segb_fc0"]["falsecolor_mean"]
            if rec > 2.0 * max(base, 0.05):
                print(f"M1 GATE FAILED: clipramp segb+amaze falsecolor "
                      f"{rec:.3f} > 2x clip's {base:.3f} — pathology; "
                      f"aborting before further stages.")
                results["m1_gate"] = {"pass": False, "clip": base, "segb": rec}
                return False
            results["m1_gate"] = {"pass": True, "clip": base, "segb": rec}
    return True


def run_truth(results: dict) -> None:
    """Mosaic-level held-out-truth re-pin (fc/demosaic-invariant)."""
    import rawpy

    from lrt_cinema._segbased_reconstruct import reconstruct_mosaic_segbased
    from lrt_cinema.pipeline import _extract_cfa, _wb_mul_from_asn

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


def _gym_matrix_srgb():
    """Yield (arm, fc, ptag, srgb_float) for the 3x2x2 gym matrix at the
    full production intent through the direct construction (amaze decode,
    production fc placement, scene-referred mask-EV gain, faithful ops)."""
    import rawpy

    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.develop_ops import apply_develop_ops
    from lrt_cinema.interpolation import LR_LOCAL_EXPOSURE_SCALE
    from lrt_cinema.ir import RenderIntent
    from lrt_cinema.output import _prophoto_to_display
    from lrt_cinema.pipeline import (
        _wb_mul_from_asn,
        apply_adobe_pipeline,
        kelvin_to_neutral,
        read_dcp_default_black_render,
        read_dng_baseline_exposure,
    )
    from lrt_cinema.xmp_parser import parse_xmp_file

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
    for arm in ARMS:
        with rawpy.imread(str(GYM_DNG)) as raw:
            cam = _decode_arm(raw, arm, render_wb)
        for fc in FC_LEVELS:
            cam_fc = _fc(cam, fc)
            for pull, ptag in ((0.0, "intent"), (-1.0, "pull1ev")):
                total_ev = ops.scene_exposure_ev + ops.exposure_ev + pull
                cam_g = (cam_fc * np.float32(2.0 ** total_ev)
                         if total_ev != 0.0 else cam_fc)
                pp = apply_adobe_pipeline(
                    camera_rgb=cam_g, profile=profile,
                    as_shot_neutral=render_asn, scene_kelvin=scene_kelvin,
                    dng_baseline_exposure=dng_be, default_black_render=dbr,
                    stop_after_stage=9)
                pp = apply_develop_ops(pp, ops, RenderIntent.FAITHFUL,
                                       master_look="bake",
                                       capture_sharpen="off")
                srgb = np.clip(_prophoto_to_display(pp, "srgb"), 0.0, 1.0)
                yield arm, fc, ptag, srgb


def run_gym_and_flips(results: dict, do_gym: bool, do_flips: bool) -> None:
    from PIL import Image

    if do_flips:
        FLIPDIR.mkdir(parents=True, exist_ok=True)
    tags = {"clip": "A-clip-shipped", "segb": "B-segb-candidates",
            "segb_adapt": "C-segb-rebuild"}
    for arm, fc, ptag, srgb in _gym_matrix_srgb():
        if do_gym and ptag == "intent":
            r = _compare_lrt(srgb, GYM_JPG)
            results["gym_spot"][f"{arm}_fc{fc}"] = r
            print(f"gym {arm:10s} fc{fc}: dE {r['de_lrt']:.4f} "
                  f"(P95 {r['p95']:.2f}) gain "
                  f"{[f'{g:.3f}' for g in r['gain']]}")
        if do_flips:
            u8 = (srgb * 255 + 0.5).astype(np.uint8)[8:-8, 8:-8]
            Image.fromarray(u8).save(
                FLIPDIR / f"DSC_4053_{ptag}_fc{fc}_{tags[arm]}.png")
            print(f"flip: wrote {ptag}_fc{fc}/{tags[arm]}")

    if do_flips:
        (FLIPDIR / "README.txt").write_text(
            "fc-suppress x HL-reconstruction matrix — owner flip (task 16)\n"
            "==============================================================\n"
            "DSC_4053, AMAZE demosaic on ALL arms (the production default;\n"
            "the 07-06 segbased-flip set was menon), native res, zero\n"
            "scaling. 12 files: 3 reconstruction arms x fc0/fc3 x two\n"
            "exposure sets.\n\n"
            "Reconstruction arms:\n"
            "  A-clip-shipped     clip-to-common-white (the shipped default)\n"
            "  B-segb-candidates  dt-class segmentation reconstruction\n"
            "  C-segb-rebuild     + all-clipped rebuild (adapt, strength .6)\n\n"
            "fc arms:\n"
            "  fc0  false-colour suppression OFF\n"
            "  fc3  the production default (3-pass chroma median + blur)\n\n"
            "Exposure sets:\n"
            "  *_intent_*   the production develop intent (unchanged)\n"
            "  *_pull1ev_*  the same intent pulled -1 EV — on the MENON\n"
            "               path recovered highlights became visible here\n\n"
            "JUDGE at 1:1 (window highlights, clip edges):\n"
            "  1. fc0 vs fc3 within each arm — the fc3-helps verdict,\n"
            "     re-checked under amaze with reconstruction on/off.\n"
            "  2. B/C vs A: our amaze port clamps its output at common\n"
            "     white, so recovered structure should NOT survive —\n"
            "     PREDICTION: B/C ~ A in BOTH exposure sets, and the\n"
            "     clip-edge saturation boost you flagged on the menon\n"
            "     flips (07-06) should be ABSENT here. If you see it\n"
            "     anyway, that refutes the clamp-neutralization claim.\n")


def main() -> int:
    import warnings
    warnings.filterwarnings("ignore")

    stage = None
    if "--stage" in sys.argv:
        stage = sys.argv[sys.argv.index("--stage") + 1]

    results: dict = {
        "design": "{clip,segb,segb_adapt} x {fc0,fc3} x AMAZE (production)",
        "predictions": "M0 gym clip+fc3 ~ pinned 0.582; M1 amaze [0,1] clamp "
                       "neutralizes recovery (clipbars segb ~ clip, gate 2x); "
                       "M2 fc3 improves-or-holds everywhere; M3 truth re-pins "
                       "0.186/0.0903/0.0749 (mosaic-level, fc/demosaic-"
                       "invariant); M4 |segb-clip| <= 0.005 dE at 6x block-"
                       "mean; M5 flips: B/C ~ A both exposure sets",
        "menon_crossref_pinned": MENON_CROSSREF,
        "regen": "python3 tools/hl_matrix_experiment.py",
        "articles": {}, "truth_harness": {}, "gym_spot": {},
    }

    if stage in (None, "articles") and not run_articles(results):
        EVIDENCE.write_text(json.dumps(results, indent=1))
        print(f"evidence (gate-fail partial) -> {EVIDENCE}")
        return 1
    if stage in (None, "truth"):
        run_truth(results)
    if stage in (None, "gym", "flips"):
        run_gym_and_flips(results, do_gym=stage in (None, "gym"),
                          do_flips=stage in (None, "flips"))
    if stage is None:
        EVIDENCE.write_text(json.dumps(results, indent=1))
        print(f"\nevidence -> {EVIDENCE}\nflips -> {FLIPDIR}")
    else:
        out = EVIDENCE.with_name(EVIDENCE.stem + f"_{stage}.json")
        out.write_text(json.dumps(results, indent=1))
        print(f"\npartial evidence ({stage}) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
