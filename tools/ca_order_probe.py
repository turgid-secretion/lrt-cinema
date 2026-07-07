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
  ca_after   recon → CA(avoid-shift)   — the shipped experiment arm (D)
  ca_first   CA(avoid-shift) → recon   — the owner-hypothesised order

PRE-REGISTERED PREDICTIONS (2026-07-07, before first run):
  O1: ca_first cuts the hot-pixel census (isolated chroma impulses,
      |C - median3x3(C)| > 8 within the clip-edge ring) vs ca_after.
  O2: ca_first ring chroma <= ca_after (boundary ingestion addressed at
      the source).
  O3: risk — ca_first must not blow up the CA fit on the unclipped
      plateaus (watch the warnings + whole-ring metrics).

Run:  python3 tools/ca_order_probe.py
Out:  tests/fixtures/evidence/ca_order_probe_<today>.json
      ~/lrt-cinema-fixtures/verify-2026-07-07/ca-flip/ (E arm added)
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

    def decode(order: str) -> np.ndarray:
        from colour_demosaicing import demosaicing_CFA_Bayer_Menon2007
        if order == "ca_after":
            m = reconstruct_mosaic_segbased(scaled, chan, wb)
            m = ca_correct_mosaic(m, pattern, iterations=2, avoid_shift=True)
        else:  # ca_first — CA on the raw (unclipped) scaled mosaic
            m = ca_correct_mosaic(scaled, pattern, iterations=2,
                                  avoid_shift=True, scale=float(wb.max()))
            m = reconstruct_mosaic_segbased(m, chan, wb)
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
            "ca_first": "E-segb-ca-first"}
    for order in ("ca_after", "ca_first"):
        s8 = render(decode(order))
        results["arms"][order] = metrics(s8)
        print(f"{order:9s}: {results['arms'][order]}")
        Image.fromarray(s8[8:-8, 8:-8]).save(
            FLIPDIR / f"DSC_4053_intent_fc3_{tags[order]}.png")
    with (FLIPDIR / "README.txt").open("a") as f:
        f.write(
            "\nE-segb-ca-first: the ORDER probe arm — CA correction runs\n"
            "BEFORE the segbased reconstruction (the owner-hypothesised\n"
            "order; shipped D runs reconstruction first, per dt's canon).\n"
            "JUDGE D vs E: do the random hot pixels in CA-suppressed areas\n"
            "disappear in E, with no new artifacts?\n")
    EVIDENCE.write_text(json.dumps(results, indent=1))
    print(f"evidence -> {EVIDENCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
