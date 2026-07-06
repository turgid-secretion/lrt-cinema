"""Pressure harness — render the test articles, score against analytic truth.

For every article DNG and every front-end arm this renders through the REAL
production entry (`pipeline.render_frame`, stages 1–9, no develop ops) and
scores in display sRGB/Lab against the PERFECT-FRONT-END EXPECTED render:
the analytic unbalanced field (with its known sensor clipping) pushed through
the IDENTICAL stage 2–9 maths. Any divergence is therefore front-end
behaviour (demosaic + conditioning + clip handling) in isolation — the colour
maths cancels.

External anchors (epistemic status per the owner's 2026-06-10 audit):
- `dng_validate` renders the SAME article files. CAVEAT: its reference
  demosaic is BILINEAR, so this column is authoritative for the colour math
  and the SDK-reference front-end — NOT for what ACR-the-product does at
  edges (its column ≈ our 'linear' arm by construction).
- `libraw-engine`: libraw's OWN full default render (independent engine, no
  shared code with our stages). Because its tone/colour intent differs,
  it is scored ONLY on truth-anchored INVARIANTS that need no shared colour
  math: chroma invented where the scene is neutral, and colour error inside
  the analytically-known clip zone. Cross-engine agreement on invariants is
  rule 8 applied to fixtures.
- The harness "expected" is INTERNAL (our stages 2–9 on the construction
  truth) — it isolates the front-end; it is not itself an authority.

Metrics per article × arm (the structure/colour split exists because a mean
chroma metric let a desaturated-but-structured artifact masquerade as fixed —
owner catch, 2026-06-10):

    de_mean / de_p95 / de_max   — ΔE2000 vs expected
    dl_mean                     — |ΔL*| component (STRUCTURE error)
    dc_mean                     — chroma (a,b) error component (COLOUR error)
    falsecolor_mean/_p99        — chroma magnitude where truth is NEUTRAL
                                  (bars/zoneplate/clipbars: any chroma at all
                                  is invented)
    clipzone_dc_mean            — colour error inside the analytically-known
                                  partial-clip zone (clipramp/clipbars)

Arms: linear (libraw bilinear) · rcd · menon — the production candidates.

Run:  python3 tools/test_articles/run_pressure.py [--crops]
Out:  tests/fixtures/evidence/pressure_<date>.json
      ~/lrt-cinema-fixtures/test-articles/renders/  (1:1 crops + README.txt)
"""

from __future__ import annotations

import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fields import scene_field  # noqa: E402

FIX = Path.home() / "lrt-cinema-fixtures"
ART = FIX / "test-articles"
RENDERS = ART / "renders"
DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"
)
DNG_VALIDATE = FIX / "dng_validate"
EVIDENCE = REPO / f"tests/fixtures/evidence/pressure_{_dt.date.today().isoformat()}.json"

ARMS = ("linear", "rcd", "menon", "amaze")
NEUTRAL_TRUTH = {"bars", "clipbars", "zoneplate", "diagbars", "noisebars",
                 "clipfield", "shadowwedge", "slantededge"}
CROP = 384


def _lab(srgb8: np.ndarray) -> np.ndarray:
    import colour
    lin = colour.models.eotf_sRGB(srgb8.astype(np.float64) / 255.0)
    xyz = colour.RGB_to_XYZ(lin, "sRGB", apply_cctf_decoding=False)
    return colour.XYZ_to_Lab(xyz, illuminant=np.array([0.3127, 0.3290]))


def _invariants(img8: np.ndarray, neutral_truth: bool,
                clipzone: np.ndarray | None) -> dict:
    """Truth-anchored metrics needing NO shared colour math — valid for any
    engine's render of the article: chroma invented where the scene is
    neutral; chroma spread inside the known partial-clip zone."""
    lab = _lab(img8)
    out: dict = {}
    if neutral_truth:
        chroma = np.hypot(lab[..., 1], lab[..., 2])
        out["falsecolor_mean"] = float(chroma.mean())
        out["falsecolor_p99"] = float(np.percentile(chroma, 99))
    if clipzone is not None and clipzone.any():
        ch = np.hypot(lab[..., 1], lab[..., 2])[clipzone]
        out["clipzone_chroma_mean"] = float(ch.mean())
        out["clipzone_chroma_p99"] = float(np.percentile(ch, 99))
    return out


def _score(ours8: np.ndarray, exp8: np.ndarray, neutral_truth: bool,
           clipzone: np.ndarray | None) -> dict:
    import colour
    lo, le = _lab(ours8), _lab(exp8)
    de = colour.delta_E(lo, le, method="CIE 2000")
    dl = np.abs(lo[..., 0] - le[..., 0])
    dc = np.hypot(lo[..., 1] - le[..., 1], lo[..., 2] - le[..., 2])
    out = {
        "de_mean": float(de.mean()), "de_p95": float(np.percentile(de, 95)),
        "de_max": float(de.max()),
        "dl_mean": float(dl.mean()), "dc_mean": float(dc.mean()),
    }
    if clipzone is not None and clipzone.any():
        out["clipzone_dc_mean"] = float(dc[clipzone].mean())
        out["clipzone_de_mean"] = float(de[clipzone].mean())
    out.update(_invariants(ours8, neutral_truth, clipzone))
    return out


def main() -> int:
    import rawpy
    import tifffile
    from PIL import Image

    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.output import _prophoto_to_display
    from lrt_cinema.pipeline import (
        apply_adobe_pipeline,
        read_dcp_default_black_render,
        read_dng_baseline_exposure,
        render_frame,
    )

    make_crops = "--crops" in sys.argv
    manifest = json.loads((ART / "manifest.json").read_text())
    asn = np.asarray(manifest["asn"], np.float32)
    # Synthetic DNGs carry a ColorMatrix-only embedded profile (dnglab strips
    # the ForwardMatrix); hand our pipeline the same FM-stripped profile so
    # ours / expected / dng_validate all use one colour path
    # (tests/test_synthetic_dng.py, "Profile subtlety").
    profile = parse_dcp(DCP)
    profile = type(profile)(**{**profile.__dict__,
                               "forward_matrix_1": None, "forward_matrix_2": None})

    def to8(prophoto: np.ndarray) -> np.ndarray:
        return (np.clip(_prophoto_to_display(prophoto, "srgb"), 0, 1)
                * 255 + 0.5).astype(np.uint8)

    results: dict = {"arms": ARMS, "articles": {}}
    RENDERS.mkdir(parents=True, exist_ok=True)
    readme = ["Test-article renders — all images 1:1 native pixels.",
              "Per article: expected_* = analytic truth through stages 2-9;",
              "arm_<demosaic>_* = the real pipeline; adobe_* = dng_validate.", ""]

    for name, meta in manifest["articles"].items():
        dng = ART / f"{name}.dng"
        dng_be = read_dng_baseline_exposure(dng)
        dbr = read_dcp_default_black_render(DCP)
        with rawpy.imread(str(dng)) as r:
            h, w = r.raw_image_visible.shape
        h -= h % 2
        w -= w % 2
        spec = meta["spec"]
        scene = scene_field(spec, h, w)
        unbal = scene * asn[None, None, :]
        expected_unbal = np.minimum(unbal, 1.0)    # perfect front-end + sensor clip
        # develop-WB articles (e.g. clipbars_coolwb): the mosaic is the same
        # physical scene; Stage 2 uses the OVERRIDE neutral — and the
        # neutral-truth invariants are INVALID (the override casts the whole
        # frame by design). External engine arms are skipped (they render at
        # as-shot WB → duplicates of the base article's rows).
        dev_ops = None
        render_asn, render_kelvin = asn, 5500.0
        if "develop_wb" in spec:
            from lrt_cinema.ir import DevelopOps
            from lrt_cinema.pipeline import kelvin_to_neutral
            k, tint = spec["develop_wb"]
            dev_ops = DevelopOps(temperature_k=int(k), tint=int(tint))
            render_kelvin = float(k)
            render_asn = kelvin_to_neutral(profile, render_kelvin, float(tint))
        # The pipeline consumes BALANCED camera RGB (slot-3 WB-once): balance
        # the analytic expectation with the same G-normalised multipliers the
        # decode derives from the render neutral. Identical to the old
        # Stage-2 multiply on the same data — the reference is unchanged.
        wb_mul = (1.0 / render_asn) / (1.0 / render_asn)[1]
        exp_pp = apply_adobe_pipeline(
            camera_rgb=(expected_unbal * wb_mul).astype(np.float32),
            profile=profile,
            as_shot_neutral=render_asn, scene_kelvin=render_kelvin,
            dng_baseline_exposure=dng_be, default_black_render=dbr,
            stop_after_stage=9)
        exp8 = to8(exp_pp)
        # partial-clip zone: some-but-not-all channels clipped
        nclip = (unbal >= 1.0).sum(axis=-1)
        partial = (nclip > 0) & (nclip < 3)
        neutral = name in NEUTRAL_TRUTH and dev_ops is None
        row: dict = {"clip_frac": meta["clip_frac"], "arms": {}}

        for arm in ARMS:
            res = render_frame(dng, profile, dcp_path=DCP, demosaic=arm,
                               develop_ops=dev_ops)
            ours8 = to8(res.prophoto)
            oh, ow = ours8.shape[:2]
            row["arms"][arm] = _score(
                ours8, exp8[:oh, :ow], neutral,
                partial[:oh, :ow] if partial.any() else None)
            if make_crops:
                cy, cx = oh // 2 - CROP // 2, ow // 2 - CROP // 2
                Image.fromarray(ours8[cy:cy + CROP, cx:cx + CROP]).save(
                    RENDERS / f"{name}_arm-{arm}.png")
        if dev_ops is not None:
            results["articles"][name] = row
            a = row["arms"]
            print(f"{name:12s} " + "  ".join(
                f"{arm}: ΔE {a[arm]['de_mean']:.3f} (L {a[arm]['dl_mean']:.3f}"
                f"/C {a[arm]['dc_mean']:.3f})" for arm in ARMS))
            continue
        # libraw-engine arm: an INDEPENDENT renderer's answer on the same
        # file (its own WB/colour/tone path, zero shared code with our
        # stages) — scored on invariants only.
        with rawpy.imread(str(dng)) as r:
            lr8 = r.postprocess(
                use_camera_wb=True, no_auto_bright=True, output_bps=8,
                demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD)
        lh, lw = lr8.shape[:2]
        row["libraw_engine_invariants"] = _invariants(
            lr8, neutral, partial[:lh, :lw] if partial.any() else None)
        if make_crops:
            cy, cx = lh // 2 - CROP // 2, lw // 2 - CROP // 2
            Image.fromarray(lr8[cy:cy + CROP, cx:cx + CROP]).save(
                RENDERS / f"{name}_engine-libraw-ahd.png")

        # darktable-cli engine arm: a SHIPPING raw developer's full default
        # pipeline (own demosaic, false-colour suppression, pre-demosaic
        # highlight reconstruction) — the product-grade anchor dng_validate
        # cannot be (its reference demosaic is bilinear). Pixel-deterministic
        # (verified; file hashes differ only by embedded timestamps).
        dt_tif = RENDERS / f"{name}_engine-dt.tif"
        try:
            subprocess.run(
                ["darktable-cli", str(dng), str(dt_tif),
                 "--core", "--disable-opencl"],
                check=True, capture_output=True, timeout=600)
            dt8 = tifffile.imread(str(dt_tif))
            if dt8.dtype != np.uint8:
                dt8 = (dt8.astype(np.float32) / np.iinfo(dt8.dtype).max
                       * 255 + 0.5).astype(np.uint8)
            dh, dw = dt8.shape[:2]
            row["dt_engine_invariants"] = _invariants(
                dt8, neutral, partial[:dh, :dw] if partial.any() else None)
            if make_crops:
                cy, cx = dh // 2 - CROP // 2, dw // 2 - CROP // 2
                Image.fromarray(dt8[cy:cy + CROP, cx:cx + CROP]).save(
                    RENDERS / f"{name}_engine-dt.png")
            dt_tif.unlink()
        except Exception as exc:  # noqa: BLE001 — anchor optional
            row["dt_engine_invariants"] = {"error": str(exc)[:200]}

        # Adobe anchor on the same file
        stem = RENDERS / f"{name}_adobe"
        try:
            subprocess.run(
                [str(DNG_VALIDATE), "-profile", "Camera Standard", "-16",
                 "-tif", str(stem), str(dng)],
                check=True, capture_output=True, timeout=600)
            gt16 = tifffile.imread(str(stem) + ".tif")
            gt8 = (gt16.astype(np.float32) / 65535 * 255 + 0.5).astype(np.uint8)
            gh, gw = gt8.shape[:2]
            oy, ox = (h - gh) // 2, (w - gw) // 2
            row["adobe_vs_expected"] = _score(
                gt8, exp8[oy:oy + gh, ox:ox + gw], neutral,
                partial[oy:oy + gh, ox:ox + gw] if partial.any() else None)
            if make_crops:
                cy, cx = gh // 2 - CROP // 2, gw // 2 - CROP // 2
                Image.fromarray(gt8[cy:cy + CROP, cx:cx + CROP]).save(
                    RENDERS / f"{name}_adobe.png")
            (Path(str(stem) + ".tif")).unlink()    # keep crops, not 150MB tifs
        except Exception as exc:  # noqa: BLE001 — anchor optional
            row["adobe_vs_expected"] = {"error": str(exc)[:200]}
        if make_crops:
            cy, cx = h // 2 - CROP // 2, w // 2 - CROP // 2
            Image.fromarray(exp8[cy:cy + CROP, cx:cx + CROP]).save(
                RENDERS / f"{name}_expected.png")
            readme.append(f"{name}: clip_frac {meta['clip_frac']:.3f}")
        results["articles"][name] = row
        a = row["arms"]
        print(f"{name:12s} " + "  ".join(
            f"{arm}: ΔE {a[arm]['de_mean']:.3f} (L {a[arm]['dl_mean']:.3f}"
            f"/C {a[arm]['dc_mean']:.3f})" for arm in ARMS))

    (RENDERS / "README.txt").write_text("\n".join(readme))
    EVIDENCE.write_text(json.dumps(results, indent=1))
    print(f"\nevidence -> {EVIDENCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
