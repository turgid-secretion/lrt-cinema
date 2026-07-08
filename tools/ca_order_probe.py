"""CA-vs-reconstruction ORDER probe (owner question 2026-07-07).

THE QUESTION: the owner sees random "hot" pixels in the segb+CA arms in
areas where CA is otherwise suppressed. The shipped/tested order is
RECONSTRUCTION → CA (dt's canon: highlights@4 → cacorrect@5). Does the
reversed order — CA on the raw mosaic FIRST, reconstruction after —
remove the hot pixels (CA never touches synthetic reconstructed content;
the reconstruction samples CA-cleaned boundaries), and at what cost (the
CA diagnostic then sees UNCLIPPED channel-disparate clip plateaus — the
exact regime dt's order avoids)?

ARMS (gym frame, segbased + menon + fc3, full production intent):
  ca_after         recon → CA(avoid-shift)  — the shipped experiment arm (D)
  ca_first         CA(avoid-shift) → recon  — the owner-hypothesised order
  ca_after_hotpix  recon → CA → dt-hotpixels — slot-2.5 remedy (F).
                   MEASURED before the flip run: at dt defaults (0.25,
                   strict) the stage fires 0× on this mosaic (the
                   detector needs bright-over-dark single-site impulses;
                   near clip boundaries everything is bright), so the F
                   flip uses the stage's MAXIMUM reach — strength 1.0 +
                   permissive (234 sites) — to test whether ANY of its
                   envelope touches the owner's artifact.

PRE-REGISTERED PREDICTIONS (2026-07-07, before first run):
  O1: ca_first cuts the hot-pixel census (isolated chroma impulses,
      |C - median3x3(C)| > 8 within the clip-edge ring) vs ca_after.
  O2: ca_first ring chroma <= ca_after (boundary ingestion addressed at
      the source).
  O3: risk — ca_first must not blow up the CA fit on the unclipped
      plateaus (watch the warnings + whole-ring metrics).
  [O1/O2 RESOLVED first run: O1 split, O2 refuted — CLAIMS 2026-07-07.]
  HOTPIXELS ARM (added after the owner's D-vs-E verdict, before its
  first run):
  H1: ca_after_hotpix cuts the ring impulse census vs ca_after (the
      stage targets exactly this class).
  H2: false-positive guard — fix_hot_pixels fires ~0 times on the
      CA-free article mosaics (bars/zoneplate: hard edges + dense
      frequency, the worst plausible false-positive content) and only
      O(10-100) times on the 24 MP real frame.

Run:  python3 tools/ca_order_probe.py
Out:  tests/fixtures/evidence/ca_order_probe_<today>.json
      ~/lrt-cinema-fixtures/verify-2026-07-07/ca-flip/ (E + F arms added)

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

FIX = Path.home() / "lrt-cinema-fixtures"
GYM_DNG = FIX / "DSC_4053.dng"
GYM_XMP = FIX / "production" / "xmp" / "DSC_4053.xmp"
DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"
)
EVIDENCE = REPO / ("tests/fixtures/evidence/"
                   f"ca_order_probe_{_dt.date.today().isoformat()}.json")
FLIPDIR = FIX / "verify-2026-07-07" / "ca-flip"


def main() -> int:
    import rawpy
    from PIL import Image
    from scipy.ndimage import binary_dilation, median_filter

    from lrt_cinema._ca_correct import ca_correct_mosaic
    from lrt_cinema._fc_suppress import suppress_false_colour
    from lrt_cinema._segbased_reconstruct import reconstruct_mosaic_segbased
    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.develop_ops import apply_develop_ops
    from lrt_cinema.interpolation import LR_LOCAL_EXPOSURE_SCALE
    from lrt_cinema.ir import RenderIntent
    from lrt_cinema.output import _prophoto_to_display
    from lrt_cinema.pipeline import (
        _extract_cfa,
        _mosaic_clip_mask,
        _wb_mul_from_asn,
        apply_adobe_pipeline,
        kelvin_to_neutral,
        read_dcp_default_black_render,
        read_dng_baseline_exposure,
    )
    from lrt_cinema.xmp_parser import parse_xmp_file

    profile = parse_dcp(DCP)
    ops, _kf, _dfk, _rating, mask_offsets = parse_xmp_file(GYM_XMP)
    ops = replace(ops, scene_exposure_ev=LR_LOCAL_EXPOSURE_SCALE
                  * sum(ev for _k, ev in mask_offsets))
    kelvin = float(ops.temperature_k)
    asn = kelvin_to_neutral(profile, kelvin, float(ops.tint or 0.0))
    wb = _wb_mul_from_asn(asn)
    dng_be = read_dng_baseline_exposure(GYM_DNG)
    dbr = read_dcp_default_black_render(DCP)

    with rawpy.imread(str(GYM_DNG)) as raw:
        cfa, pattern = _extract_cfa(raw)
        colors = raw.raw_colors_visible
        clip_mask = _mosaic_clip_mask(raw)
    h, w = cfa.shape
    chan = np.where(colors[:h, :w] == 3, 1, colors[:h, :w])
    scaled = cfa * wb[chan].astype(np.float32)
    anyclip = clip_mask.any(axis=-1)
    ring = binary_dilation(anyclip, iterations=3) & ~anyclip

    n_fixed_gym = {}

    def decode(order: str) -> np.ndarray:
        from colour_demosaicing import demosaicing_CFA_Bayer_Menon2007

        from lrt_cinema._hotpixels import fix_hot_pixels
        if order.startswith("ca_after"):
            m = reconstruct_mosaic_segbased(scaled, chan, wb, site_guard=2.0)
            m = ca_correct_mosaic(m, pattern, iterations=2, avoid_shift=True)
            if order == "ca_after_hotpix":
                # maximum-reach parameterization (see docstring): dt
                # defaults fire 0x on this mosaic — measured pre-flip
                m, n_fixed_gym[order] = fix_hot_pixels(
                    m, strength=1.0, permissive=True)
        else:  # ca_first — CA on the raw (unclipped) scaled mosaic
            m = ca_correct_mosaic(scaled, pattern, iterations=2,
                                  avoid_shift=True, scale=float(wb.max()))
            m = reconstruct_mosaic_segbased(m, chan, wb, site_guard=2.0)
        return np.maximum(np.asarray(
            demosaicing_CFA_Bayer_Menon2007(m, pattern), np.float32), 0.0)

    def render(cam: np.ndarray) -> np.ndarray:
        cam = suppress_false_colour(cam, passes=3, blur=True)
        ev = ops.scene_exposure_ev + ops.exposure_ev
        if ev:
            cam = cam * np.float32(2.0 ** ev)
        pp = apply_adobe_pipeline(cam, profile, asn, kelvin, dng_be, dbr, 9)
        pp = apply_develop_ops(pp, ops, RenderIntent.FAITHFUL,
                               master_look="bake", capture_sharpen="off")
        return (np.clip(_prophoto_to_display(pp, "srgb"), 0, 1)
                * 255 + 0.5).astype(np.uint8)

    def metrics(s8: np.ndarray) -> dict:
        import colour
        rgb = s8.astype(np.float64) / 255.0
        lab = colour.XYZ_to_Lab(
            colour.RGB_to_XYZ(rgb, colourspace="sRGB",
                              apply_cctf_decoding=True),
            illuminant=np.array([0.3127, 0.3290]))
        chroma = np.hypot(lab[..., 1], lab[..., 2])
        rr = ring[:chroma.shape[0], :chroma.shape[1]]
        impulse = np.abs(chroma - median_filter(chroma, size=3))
        return {
            "ring_chroma_mean": float(chroma[rr].mean()),
            "ring_chroma_p99": float(np.percentile(chroma[rr], 99)),
            "hot_px_ring_gt8": int((impulse[rr] > 8.0).sum()),
            "hot_px_ring_gt15": int((impulse[rr] > 15.0).sum()),
        }

    FLIPDIR.mkdir(parents=True, exist_ok=True)
    results: dict = {
        "design": "segb+CA order probe: recon->CA (shipped D) vs CA->recon",
        "predictions": "O1 ca_first cuts hot-pixel census; O2 ring chroma "
                       "<=; O3 no CA-fit blowup on unclipped plateaus",
        "arms": {},
    }
    tags = {"ca_after": "D-segb-ca-on",       # identical to the flip set's D
            "ca_first": "E-segb-ca-first",
            "ca_after_hotpix": "F-segb-ca-hotpix"}
    for order in ("ca_after", "ca_first", "ca_after_hotpix"):
        s8 = render(decode(order))
        results["arms"][order] = metrics(s8)
        if order in n_fixed_gym:
            results["arms"][order]["n_fixed_mosaic"] = n_fixed_gym[order]
        print(f"{order:15s}: {results['arms'][order]}")
        Image.fromarray(s8[8:-8, 8:-8]).save(
            FLIPDIR / f"DSC_4053_intent_fc3_{tags[order]}.png")

    # ---- hotpixels false-positive guard (H2): CA-free article mosaics ----
    from lrt_cinema._hotpixels import fix_hot_pixels

    art = FIX / "test-articles"
    fp: dict = {}
    for name in ("bars", "zoneplate"):
        with rawpy.imread(str(art / f"{name}.dng")) as r:
            acfa, apat = _extract_cfa(r)
            acolors = r.raw_colors_visible
        ah, aw = acfa.shape
        achan = np.where(acolors[:ah, :aw] == 3, 1, acolors[:ah, :aw])
        awb = wb  # any sane multipliers; conditioning mirrors the clip path
        cond = np.minimum(acfa * awb[achan].astype(np.float32),
                          np.float32(awb.min()))
        _fixed_m, n = fix_hot_pixels(cond, strength=0.25)
        fp[name] = n
        print(f"hotpix false-positive census {name}: {n}")
    # real-frame clip-path census (production conditioning, no recon)
    clip_cond = np.minimum(scaled, np.float32(wb.min()))
    _m, n_real = fix_hot_pixels(clip_cond, strength=0.25)
    fp["gym_clip_path"] = n_real
    print(f"hotpix census gym clip path: {n_real}")
    results["hotpix_false_positive_census"] = fp

    with (FLIPDIR / "README.txt").open("a") as f:
        f.write(
            "\nE-segb-ca-first: the ORDER probe arm — CA correction runs\n"
            "BEFORE the segbased reconstruction (the owner-hypothesised\n"
            "order; shipped D runs reconstruction first, per dt's canon).\n"
            "JUDGE D vs E: do the random hot pixels in CA-suppressed areas\n"
            "disappear in E, with no new artifacts?\n"
            "\nF-segb-ca-hotpix: D + the dt hotpixels stage (slot 2.5,\n"
            "between CA and demosaic) at its MAXIMUM reach (strength 1.0 +\n"
            "permissive; 234 sites fixed — dt defaults fire ZERO times on\n"
            "this mosaic, so F at defaults would be identical to D).\n"
            "JUDGE D vs F: do the random hot pixels disappear? If D and F\n"
            "look the same, the artifact is NOT a mosaic-domain impulse\n"
            "and the remedy moves into the segbased reconstruction itself.\n")
    EVIDENCE.write_text(json.dumps(results, indent=1))
    print(f"evidence -> {EVIDENCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
