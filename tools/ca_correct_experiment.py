"""Slot-2 raw CA correction (Martinec port) — deciding experiment.

THE QUESTION (owner directive 2026-07-06, task 17): does a pre-demosaic
raw-CA stage (the cross-engine canon stage we lacked) measurably correct
lateral CA WITHOUT costing resolution on CA-free content, and does it
reduce the clip-edge fringe chroma that boundary-trusting highlight
reconstruction ingests (the owner-observed segbased saturation boost)?

ARMS:
  synthetic   radial-magnification CA (the physical model), known truth
  articles    amaze + clip, ca0 vs ca2 — articles carry NO lens CA by
              construction, so this is the no-regression half
  gym         DSC_4053 clip-edge fringe ring, four arms:
              clip+ca0 / clip+ca2 (amaze, the shipped path) and
              segb+ca0 / segb+ca2 (menon, reconstruction-then-CA — dt's
              highlights@4 → cacorrect@5 order)

PRE-REGISTERED PREDICTIONS (2026-07-07, before first run):
  C1: synthetic R/B misalignment error cut >= 50 % at production-plausible
      CA magnitudes (alpha ~0.002).
  C2: articles do NOT regress — de/dl/falsecolor for ca2 within noise of
      ca0 on every article (bars + slantededge dl_mean especially: the
      correction must not soften G-carried resolution; G is untouched by
      construction, so any dl movement comes from R/B resampling only).
  C3: gym fringe ring chroma (2-out-3-dilated mosaic clip-mask boundary):
      ca2 <= ca0 for BOTH the clip arm and the segbased arm; if the
      owner-observed segbased boost is fringe-ingestion, the segbased arm
      should benefit MORE (its boundary sample gets cleaned).

Run:  python3 tools/ca_correct_experiment.py [--flips-only] [--no-flips]
Out:  tests/fixtures/evidence/ca_correct_<today>.json
      ~/lrt-cinema-fixtures/verify-2026-07-07/ca-flip/  (owner arms)
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
ARTICLES = ("bars", "slantededge", "diagbars", "clipbars", "noisebars",
            "zoneplate")
EVIDENCE = REPO / ("tests/fixtures/evidence/"
                   f"ca_correct_{_dt.date.today().isoformat()}.json")
FLIPDIR = FIX / f"verify-{_dt.date.today().isoformat()}" / "ca-flip"
CA_ITER = 2  # the dt default iteration count


# ---------------------------------------------------------------------------
# synthetic half — known radial CA, measurable recovery
# ---------------------------------------------------------------------------

def synthetic_block() -> dict:
    from scipy.ndimage import gaussian_filter, map_coordinates

    from lrt_cinema._ca_correct import ca_correct_mosaic

    rng = np.random.default_rng(42)
    h = w = 2048
    base = gaussian_filter(rng.random((h, w)), 3.0)
    base = ((base - base.min()) / (base.max() - base.min()) * 0.85 + 0.05
            ).astype(np.float32)
    cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)

    def magnified(alpha: float) -> np.ndarray:
        return map_coordinates(
            base, [yy - alpha * (yy - cy), xx - alpha * (xx - cx)],
            order=3, mode="reflect").astype(np.float32)

    out: dict = {}
    for alpha_r, alpha_b in ((0.0025, -0.0020), (0.0008, -0.0006)):
        mosaic = base.copy()
        mosaic[0::2, 0::2] = magnified(alpha_r)[0::2, 0::2]
        mosaic[1::2, 1::2] = magnified(alpha_b)[1::2, 1::2]
        corr = ca_correct_mosaic(mosaic, "RGGB", iterations=CA_ITER)
        inner = np.s_[16:-16, 16:-16]
        row = {}
        for ch, (pr, qc) in (("R", (0, 0)), ("B", (1, 1))):
            before = float(np.abs(mosaic[inner][pr::2, qc::2]
                                  - base[inner][pr::2, qc::2]).mean())
            after = float(np.abs(corr[inner][pr::2, qc::2]
                                 - base[inner][pr::2, qc::2]).mean())
            row[ch] = {"mae_before": before, "mae_after": after,
                       "cut_pct": (1 - after / before) * 100.0}
            print(f"synthetic a=({alpha_r:+.4f},{alpha_b:+.4f}) {ch}: "
                  f"{before:.5f} -> {after:.5f} "
                  f"({row[ch]['cut_pct']:.1f} % cut)")
        out[f"alpha_{alpha_r:+.4f}_{alpha_b:+.4f}"] = row
    return out


# ---------------------------------------------------------------------------
# articles half — no-regression on CA-free constructions
# ---------------------------------------------------------------------------

def articles_block() -> dict:
    import rawpy
    from run_pressure import _score

    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.output import _prophoto_to_display
    from lrt_cinema.pipeline import (
        _asn_from_wb,
        _demosaic_rgb,
        _wb_mul_from_asn,
        apply_adobe_pipeline,
        read_dcp_default_black_render,
        read_dng_baseline_exposure,
    )

    manifest = json.loads((ART / "manifest.json").read_text())
    art_asn = np.asarray(manifest["asn"], np.float32)
    profile = parse_dcp(DCP)
    profile = type(profile)(**{**profile.__dict__,
                               "forward_matrix_1": None,
                               "forward_matrix_2": None})
    dbr = read_dcp_default_black_render(DCP)

    def to8(pp):
        return (np.clip(_prophoto_to_display(pp, "srgb"), 0, 1)
                * 255 + 0.5).astype(np.uint8)

    out: dict = {}
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
            profile=profile, as_shot_neutral=art_asn, scene_kelvin=5500.0,
            dng_baseline_exposure=dng_be, default_black_render=dbr,
            stop_after_stage=9))
        nclip = (unbal >= 1.0).sum(axis=-1)
        partial = (nclip > 0) & (nclip < 3)
        row: dict = {}
        for ca_n in (0, CA_ITER):
            with rawpy.imread(str(dng)) as raw:
                file_asn = _asn_from_wb(raw.camera_whitebalance)
                rgb = _demosaic_rgb(raw, rawpy, False, "amaze",
                                    _wb_mul_from_asn(file_asn), "clip", ca_n)
            pp = apply_adobe_pipeline(
                camera_rgb=rgb, profile=profile, as_shot_neutral=file_asn,
                scene_kelvin=5500.0, dng_baseline_exposure=dng_be,
                default_black_render=dbr, stop_after_stage=9)
            o8 = to8(pp)
            oh, ow = o8.shape[:2]
            zone = partial[:oh, :ow]
            s = _score(o8, exp8[:oh, :ow], True, zone if zone.any() else None)
            row[f"ca{ca_n}"] = s
            print(f"{name:12s} ca{ca_n}: de={s['de_mean']:.3f} "
                  f"dl={s['dl_mean']:.3f} fc={s['falsecolor_mean']:.3f}")
        out[name] = row
    return out


# ---------------------------------------------------------------------------
# gym half — clip-edge fringe ring, clip vs segbased, CA on/off
# ---------------------------------------------------------------------------

def _gym_context():
    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.interpolation import LR_LOCAL_EXPOSURE_SCALE
    from lrt_cinema.pipeline import (
        _wb_mul_from_asn,
        kelvin_to_neutral,
        read_dcp_default_black_render,
        read_dng_baseline_exposure,
    )
    from lrt_cinema.xmp_parser import parse_xmp_file

    profile = parse_dcp(DCP)
    ops, _kf, _dfk, _rating, mask_offsets = parse_xmp_file(GYM_XMP)
    ops = replace(ops, scene_exposure_ev=LR_LOCAL_EXPOSURE_SCALE
                  * sum(ev for _k, ev in mask_offsets))
    scene_kelvin = float(ops.temperature_k)
    render_asn = kelvin_to_neutral(profile, scene_kelvin,
                                   float(ops.tint or 0.0))
    return dict(profile=profile, ops=ops, scene_kelvin=scene_kelvin,
                render_asn=render_asn,
                render_wb=_wb_mul_from_asn(render_asn),
                dng_be=read_dng_baseline_exposure(GYM_DNG),
                dbr=read_dcp_default_black_render(DCP))


def _gym_decode(arm: str, ca_n: int, render_wb: np.ndarray) -> np.ndarray:
    """Balanced camera RGB for one gym arm.

    clip: the shipped path (amaze, clip-to-common-white, optional slot-2 CA
    inside `_demosaic_rgb`). segb: dt-class segmentation reconstruction on
    the scaled mosaic, then CA (dt order: highlights@4 -> cacorrect@5),
    then menon (the reconstruction arms' comparability demosaic)."""
    import rawpy

    from lrt_cinema.pipeline import _demosaic_rgb, _extract_cfa

    with rawpy.imread(str(GYM_DNG)) as raw:
        if arm == "clip":
            return _demosaic_rgb(raw, rawpy, False, "amaze", render_wb,
                                 "clip", ca_n)
        from colour_demosaicing import demosaicing_CFA_Bayer_Menon2007

        from lrt_cinema._ca_correct import ca_correct_mosaic
        from lrt_cinema._segbased_reconstruct import reconstruct_mosaic_segbased

        cfa, pattern = _extract_cfa(raw)
        colors = raw.raw_colors_visible
        h, w = cfa.shape
        chan = np.where(colors[:h, :w] == 3, 1, colors[:h, :w])
        scaled = cfa * render_wb[chan].astype(np.float32)
        recon = reconstruct_mosaic_segbased(scaled, chan, render_wb)
        if ca_n > 0:
            recon = ca_correct_mosaic(recon, pattern, iterations=ca_n)
        return np.maximum(np.asarray(
            demosaicing_CFA_Bayer_Menon2007(recon, pattern), np.float32), 0.0)


def _fringe_ring() -> np.ndarray:
    """The owner's clip-edge fringe location: pixels within 3 px OUTSIDE the
    2-dilated mosaic clip mask (any channel) — the boundary band where lens
    CA rims blown highlights."""
    import rawpy
    from scipy.ndimage import binary_dilation

    from lrt_cinema.pipeline import _mosaic_clip_mask

    with rawpy.imread(str(GYM_DNG)) as raw:
        mask = _mosaic_clip_mask(raw)
    anyclip = mask.any(axis=-1)
    return binary_dilation(anyclip, iterations=3) & ~anyclip


def _ring_chroma(srgb8: np.ndarray, ring: np.ndarray) -> dict:
    import colour
    rgb = srgb8.astype(np.float64) / 255.0
    lab = colour.XYZ_to_Lab(
        colour.RGB_to_XYZ(rgb, colourspace="sRGB",
                          apply_cctf_decoding=True),
        illuminant=np.array([0.3127, 0.3290]))
    ring = ring[:lab.shape[0], :lab.shape[1]]
    ch = np.hypot(lab[..., 1], lab[..., 2])[ring]
    return {"ring_px": int(ring.sum()),
            "ring_chroma_mean": float(ch.mean()),
            "ring_chroma_p99": float(np.percentile(ch, 99))}


def gym_block(write_flips: bool, metrics: bool) -> dict:
    from PIL import Image

    from lrt_cinema._fc_suppress import suppress_false_colour
    from lrt_cinema.develop_ops import apply_develop_ops
    from lrt_cinema.ir import RenderIntent
    from lrt_cinema.output import _prophoto_to_display
    from lrt_cinema.pipeline import apply_adobe_pipeline

    ctx = _gym_context()
    ring = _fringe_ring() if metrics else None
    out: dict = {}
    if write_flips:
        FLIPDIR.mkdir(parents=True, exist_ok=True)
    tags = {("clip", 0): "A-clip-ca-off", ("clip", CA_ITER): "B-clip-ca-on",
            ("segb", 0): "C-segb-ca-off", ("segb", CA_ITER): "D-segb-ca-on"}
    for arm, ca_n in tags:
        cam = _gym_decode(arm, ca_n, ctx["render_wb"])
        # production display path: fc-suppress 3 after the decode
        cam = suppress_false_colour(cam, passes=3, blur=True)
        ops = ctx["ops"]
        total_ev = ops.scene_exposure_ev + ops.exposure_ev
        if total_ev != 0.0:
            cam = cam * np.float32(2.0 ** total_ev)
        pp = apply_adobe_pipeline(
            camera_rgb=cam, profile=ctx["profile"],
            as_shot_neutral=ctx["render_asn"],
            scene_kelvin=ctx["scene_kelvin"],
            dng_baseline_exposure=ctx["dng_be"],
            default_black_render=ctx["dbr"], stop_after_stage=9)
        pp = apply_develop_ops(pp, ops, RenderIntent.FAITHFUL,
                               master_look="bake", capture_sharpen="off")
        srgb8 = (np.clip(_prophoto_to_display(pp, "srgb"), 0, 1)
                 * 255 + 0.5).astype(np.uint8)
        if metrics:
            out[tags[(arm, ca_n)]] = _ring_chroma(srgb8, ring)
            print(f"gym {tags[(arm, ca_n)]}: "
                  f"{out[tags[(arm, ca_n)]]}")
        if write_flips:
            Image.fromarray(srgb8[8:-8, 8:-8]).save(
                FLIPDIR / f"DSC_4053_intent_fc3_{tags[(arm, ca_n)]}.png")
            print(f"flip: wrote {tags[(arm, ca_n)]}")
    if write_flips:
        (FLIPDIR / "README.txt").write_text(
            "Slot-2 raw CA correction (Martinec port) — owner flip\n"
            "======================================================\n"
            "DSC_4053, native res, FULL production display intent on every\n"
            "arm (deflicker mask EV, develop WB 4034K/+20, fc-suppress 3).\n\n"
            "  A-clip-ca-off   the shipped production path (amaze + clip),\n"
            "                  CA correction OFF — today's default output\n"
            "  B-clip-ca-on    same + --ca-correct 2 (pre-demosaic Martinec\n"
            "                  raw CA correction, the dt/RT canon stage)\n"
            "  C-segb-ca-off   segbased HL reconstruction (menon), CA off —\n"
            "                  the arm whose clip-edge saturation boost you\n"
            "                  flagged on 2026-07-06\n"
            "  D-segb-ca-on    segbased + CA correction between the\n"
            "                  reconstruction and the demosaic (dt order)\n\n"
            "JUDGE: A vs B — do the coloured rims on clip-edge/high-contrast\n"
            "edges (window frames, specular edges) shrink, with NO softening\n"
            "of fine detail elsewhere? C vs D — does CA correction reduce\n"
            "the saturation boost the reconstruction paints into clip\n"
            "boundaries? A/B use amaze (production); C/D use menon (the\n"
            "reconstruction arms' comparability demosaic) — judge A-vs-B\n"
            "and C-vs-D, not A-vs-C.\n")
    return out


def main() -> int:
    flips_only = "--flips-only" in sys.argv
    no_flips = "--no-flips" in sys.argv
    results: dict = {
        "design": "slot-2 Martinec raw CA correction: synthetic recovery + "
                  "article no-regression + gym fringe interaction",
        "predictions": "C1 synthetic cut >=50%; C2 articles no regression "
                       "(dl/falsecolor within noise); C3 gym ring chroma "
                       "ca2 <= ca0 on both arms",
        "ca_iterations": CA_ITER,
    }
    if not flips_only:
        results["synthetic"] = synthetic_block()
        results["articles"] = articles_block()
    results["gym_fringe"] = gym_block(write_flips=not no_flips,
                                      metrics=not flips_only)
    if not flips_only:
        EVIDENCE.write_text(json.dumps(results, indent=1))
        print(f"\nevidence -> {EVIDENCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
