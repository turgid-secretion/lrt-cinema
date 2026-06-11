"""Slot-5b DECIDING EXPERIMENT — opposed reconstruction, both placements.

THE QUESTION (docs/REFERENCE_PIPELINE.md TARGET slot 5b; owner-accepted
design + addendum "best-known current methods both sides"): where does
highlight reconstruction live — PRE-demosaic on the mosaic (darktable's
placement of its default `opposed` mode) or POST-demosaic on RGB (RT's
placement of the *identical, vendored* algorithm)? One algorithm
(`lrt_cinema._opposed_reconstruct`, clean-room), placement isolated.

ARMS (all on the menon quality demosaic — one mechanism per experiment;
the demosaic is held constant so placement is the only variable):
  clip          — the shipped default: clip-to-common-white at the mosaic
                  (5a fallback; the baseline standings).
  pre-opposed   — headroom decode (NO common-white clamp) → opposed
                  reconstruction ON the WB-scaled mosaic → demosaic.
  post-opposed  — headroom decode → demosaic (channel-disparate clip
                  plateaus!) → opposed reconstruction on RGB, driven by the
                  MOSAIC-derived per-channel clip mask.

PRE-REGISTERED PREDICTIONS (2026-06-11, before first run):
  P1: pre-opposed improves clipramp clip-zone chroma toward the LR-product
      anchor (3.03 → toward ≈1.07): reconstruction replaces the clamp's
      plateau with a plausible rolloff.
  P2: post-opposed UNDERPERFORMS pre-opposed on clipbars falsecolor: in the
      headroom decode the directional demosaic sees channel-disparate clip
      plateaus and invents chroma BEFORE reconstruction can run (the
      measured 17.5-class mechanism); reconstruction-after cannot fully
      undo invented detail-scale chroma.
  P3: clipfield (large uniform blob) lands near-parity across arms (any
      reasonable treatment of a big blown blob ends neutral-white).

SCORING: identical metrics + expected-render machinery as run_pressure
(ΔE/ΔL/ΔC vs the analytic stage-2-9 expectation; falsecolor + clip-zone
invariants), on the three clip articles. LR-product anchor targets:
clipramp clip-zone ≈1.07 · clipfield ≈0.01 · clipbars falsecolor ≤1.12
(beat-the-product baseline is ours at 1.12).

Run:  python3 tools/hl_reconstruct_experiment.py
Out:  tests/fixtures/evidence/hl_reconstruct_5b_<date>.json
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
ARTICLES = ("clipramp", "clipfield", "clipbars")
ARMS = ("clip", "pre-opposed", "post-opposed")
EVIDENCE = REPO / "tests/fixtures/evidence/hl_reconstruct_5b_2026-06-11.json"
# LR-product anchor targets (CLAIMS "LR-PRODUCT anchors scored").
TARGETS = {"clipramp": ("clipzone_chroma_mean", 1.07),
           "clipfield": ("clipzone_chroma_mean", 0.01),
           "clipbars": ("falsecolor_mean", 1.12)}


def _render_arm(dng: Path, arm: str, profile, dbr: int, dng_be: float):
    """Render one arm: decode/conditioning per the arm, stages 3-9 after."""
    import rawpy

    from lrt_cinema._opposed_reconstruct import (
        reconstruct_mosaic_opposed,
        reconstruct_rgb_opposed,
    )
    from lrt_cinema.pipeline import (
        _asn_from_wb,
        _cfa_demosaic,
        _extract_cfa,
        _mosaic_clip_mask,
        _wb_mul_from_asn,
        apply_adobe_pipeline,
    )

    with rawpy.imread(str(dng)) as raw:
        asn = _asn_from_wb(raw.camera_whitebalance)
        wb_mul = _wb_mul_from_asn(asn)
        if arm == "clip":
            rgb = _cfa_demosaic(raw, "menon", wb_mul, highlights="clip")
        elif arm == "pre-opposed":
            # Headroom decode + mosaic-domain reconstruction, then demosaic.
            cfa, pattern = _extract_cfa(raw)
            colors = raw.raw_colors_visible
            h, w = cfa.shape
            chan = np.where(colors[:h, :w] == 3, 1, colors[:h, :w])
            cfa = cfa * wb_mul[chan].astype(np.float32)
            cfa = reconstruct_mosaic_opposed(cfa, chan, wb_mul)
            from colour_demosaicing import demosaicing_CFA_Bayer_Menon2007
            rgb = np.maximum(np.asarray(
                demosaicing_CFA_Bayer_Menon2007(cfa, pattern), np.float32), 0.0)
        elif arm == "post-opposed":
            rgb = _cfa_demosaic(raw, "menon", wb_mul, highlights="headroom")
            mask = _mosaic_clip_mask(raw)[: rgb.shape[0], : rgb.shape[1]]
            rgb = reconstruct_rgb_opposed(rgb, mask, wb_mul)
        else:
            raise ValueError(arm)

    return apply_adobe_pipeline(
        camera_rgb=rgb, profile=profile, as_shot_neutral=asn,
        scene_kelvin=5500.0, dng_baseline_exposure=dng_be,
        default_black_render=dbr, stop_after_stage=9)


def main() -> int:
    import rawpy

    sys.path.insert(0, str(REPO / "tools" / "test_articles"))
    from run_pressure import _score  # reuse the exact pressure metrics

    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.output import _prophoto_to_display
    from lrt_cinema.pipeline import (
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

    results: dict = {"design": "slot-5b opposed both placements (menon)",
                     "predictions": "P1 pre wins clipramp; P2 post worse on "
                                    "clipbars falsecolor; P3 clipfield parity",
                     "articles": {}}
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
        wb_mul = (1.0 / asn) / (1.0 / asn)[1]
        exp_pp_in = (np.minimum(unbal, 1.0) * wb_mul).astype(np.float32)
        from lrt_cinema.pipeline import apply_adobe_pipeline
        exp8 = to8(apply_adobe_pipeline(
            camera_rgb=exp_pp_in, profile=profile, as_shot_neutral=asn,
            scene_kelvin=5500.0, dng_baseline_exposure=dng_be,
            default_black_render=dbr, stop_after_stage=9))
        nclip = (unbal >= 1.0).sum(axis=-1)
        partial = (nclip > 0) & (nclip < 3)
        anyclip = nclip > 0

        row: dict = {"arms": {}}
        for arm in ARMS:
            pp = _render_arm(dng, arm, profile, dbr, dng_be)
            ours8 = to8(pp)
            oh, ow = ours8.shape[:2]
            zone = (partial if partial.any() else anyclip)[:oh, :ow]
            row["arms"][arm] = _score(ours8, exp8[:oh, :ow], True,
                                      zone if zone.any() else None)
            mkey, tval = TARGETS[name]
            got = row["arms"][arm].get(mkey)
            print(f"{name:10s} {arm:13s} {mkey}={got:.3f}"
                  f"  (LR-product target ≈{tval})"
                  + "".join(f"  {k}={v:.3f}" for k, v in row["arms"][arm].items()
                            if k in ("de_mean", "dc_mean", "falsecolor_mean",
                                     "clipzone_chroma_mean")))
        results["articles"][name] = row

    EVIDENCE.write_text(json.dumps(results, indent=1))
    print(f"\nevidence → {EVIDENCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
