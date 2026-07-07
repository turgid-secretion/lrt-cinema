"""Calibrate `scene_tone.apply_scene_hlsh` against the round-2 LR exports.

Stages:

1. **Table extraction (no renders).** The armS scene-domain LUTs from
   `cal_domain_round2_2026-07-07.json` (the best global scene curve per
   anchor probe: H −50/−100, S +50/+100) are resampled onto the op's
   shared knot grid. A parametric smoothstep family was tried first and
   REJECTED — its 0.2–0.4-stop shape residual at the ±100 anchors
   rendered as multi-ΔE luminance error (under-lift below ~0.004 display
   lum, over-lift 0.006–0.02). The measured tables ARE the calibration.

2. **Refinement through the real op (renders).** The LUTs were fitted for
   PIXEL-domain application; the op applies them to the guided BASE with
   detail reinserted, which shifts the effective response (Jensen bias on
   curve curvature + regional evaluation). Per anchor: render via
   `apply_scene_hlsh`, measure the per-scene-bin log2 luminance residual
   vs the LR export, secant-update the table, iterate. Cache tags carry a
   hash of (table, radius, eps) — a tag without the curve hash silently
   false-hits after any refit (the with_suffix/tag-collision lesson,
   round 2).

3. **Local-scale sweep + final validation.** Radius swept at the extreme
   anchors with refined tables; final ΔE per anchor vs the LR exports.
   Success bar: at or below the armS global-arm ΔE per probe (the local
   split should beat global where locality matters).

Run:  python3 tools/cal_hlsh_fit.py [--tables-only] [--sweep-radius]
      [--emit-tables]
Out:  tests/fixtures/evidence/cal_hlsh_fit_2026-07-07.json
      --emit-tables prints the pinned-table literals for scene_tone.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import numpy as np

FIX = Path.home() / "lrt-cinema-fixtures"
ROUND2 = FIX / "production/calibration/round2"
DNG = FIX / "DSC_4053.dng"
DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"
)
RENDERS = ROUND2 / ".cal-renders"
ROUND2_EVIDENCE = Path(__file__).resolve().parent.parent / (
    "tests/fixtures/evidence/cal_domain_round2_2026-07-07.json"
)
EVIDENCE = Path(__file__).resolve().parent.parent / (
    "tests/fixtures/evidence/cal_hlsh_fit_v2_2026-07-08.json"
)

# probe -> (field, slider, table attr) ; H anchors negative, S positive
ANCHORS = {
    "CALHIM50": (-50.0, 0.0, "_H_D50"),
    "CALHIM100": (-100.0, 0.0, "_H_D100"),
    "CALSH50": (0.0, 50.0, "_S_D50"),
    "CALSH100": (0.0, 100.0, "_S_D100"),
}
REFINE_ITERS = 2


def extract_tables(ev: dict, knot_x: np.ndarray) -> dict[str, np.ndarray]:
    """armS LUT deltas resampled to the op's knot grid (stage 1)."""
    tables = {}
    for probe, (_h, _s, attr) in ANCHORS.items():
        arm = ev["probes"][probe]["arm_s"]
        kx = np.array(arm["lut_x_log2"])
        d = np.array(arm["lut_y_log2"]) - kx
        tables[attr] = np.interp(knot_x, kx, d)
    return tables


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables-only", action="store_true",
                    help="stage 1 only (no renders)")
    ap.add_argument("--sweep-radius", action="store_true",
                    help="sweep LLF sigma_r/last_level at the extreme anchors")
    ap.add_argument("--emit-tables", action="store_true",
                    help="print the pinned-table literals for scene_tone.py")
    args = ap.parse_args()

    import lrt_cinema.scene_tone as st

    ev = json.loads(ROUND2_EVIDENCE.read_text())
    knot_x = st._KNOT_X
    tables = extract_tables(ev, knot_x)
    results: dict = {
        "knot_x": knot_x.tolist(),
        "stage1_tables": {k: v.tolist() for k, v in tables.items()},
    }
    for attr, tab in tables.items():
        setattr(st, attr, tab)

    if args.tables_only:
        EVIDENCE.write_text(json.dumps(results, indent=1))
        print(f"stage-1 tables -> {EVIDENCE}")
        return 0

    # ---- render machinery (the production wiring position) ----
    import importlib.util

    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.develop_ops import apply_develop_ops
    from lrt_cinema.interpolation import LR_LOCAL_EXPOSURE_SCALE
    from lrt_cinema.ir import RenderIntent
    from lrt_cinema.output import write_preset_output
    from lrt_cinema.pipeline import (
        _decode_raw,
        apply_adobe_pipeline,
        kelvin_to_neutral,
        read_dcp_default_black_render,
        read_dng_baseline_exposure,
    )
    from lrt_cinema.xmp_parser import parse_xmp_file
    spec = importlib.util.spec_from_file_location(
        "cal_domain_round2",
        Path(__file__).resolve().parent / "cal_domain_round2.py")
    cdr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cdr)

    profile = parse_dcp(DCP)
    dbr = read_dcp_default_black_render(DCP)
    base_be = read_dng_baseline_exposure(DNG)
    ops0, _kf, _dfk, _r, mask_offsets = parse_xmp_file(
        ROUND2 / "CALHIM50_4053.xmp")
    scene_ev = LR_LOCAL_EXPOSURE_SCALE * sum(e for _k, e in mask_offsets)
    kelvin = float(ops0.temperature_k)
    asn = kelvin_to_neutral(profile, kelvin, float(ops0.tint or 0.0))
    cam, _, _ = _decode_raw(DNG, demosaic="linear", wb_asn=asn)
    cam_ev = cam * np.float32(2.0 ** scene_ev)
    base_ops = replace(
        ops0, highlights=0.0, shadows=0.0, whites=0.0, contrast=0.0,
        blacks=0.0, scene_exposure_ev=0.0,
        hsl=type(ops0.hsl)(), color_grade=type(ops0.color_grade)())

    scene_lum = cdr._block_down(
        (cam_ev.mean(axis=-1))[8:-8, 8:-8, None], cdr.DOWN)[..., 0]
    s_edges = cdr._log_bins(scene_lum)
    sidx = np.digitize(scene_lum.ravel(), s_edges) - 1

    def op_hash() -> str:
        blob = b"".join(
            getattr(st, a).tobytes() for a in
            ("_H_D50", "_H_D100", "_S_D50", "_S_D100"))
        blob += (f"sr{st._LLF_SIGMA_R}L{st._LLF_LAST_LEVEL}"
                 f"g{st._LLF_N_GAMMA}t{st._TOE_FLOOR}"
                 f"c{st._CHROMA_ROLL_LUM}v2").encode()
        return hashlib.md5(blob).hexdigest()[:10]

    def render_op(probe: str, h: float, s: float) -> Path:
        tag = f"hlsh_{probe}_{op_hash()}"
        assert "." not in tag, f"dot in render tag breaks caching: {tag}"
        dst = RENDERS / tag
        out = dst.with_suffix(".tif")
        if out.exists():
            return out
        cam2 = st.apply_scene_hlsh(cam_ev, h, s)
        pp = apply_adobe_pipeline(
            camera_rgb=cam2, profile=profile, as_shot_neutral=asn,
            scene_kelvin=kelvin, dng_baseline_exposure=base_be,
            default_black_render=dbr, stop_after_stage=9)
        pp = apply_develop_ops(pp, base_ops, RenderIntent.FAITHFUL,
                               master_look="bake", capture_sharpen="off")
        write_preset_output(pp, dst, "lrtimelapse")
        return out

    lr_cache: dict[str, np.ndarray] = {}

    def lr_lin(probe: str) -> np.ndarray:
        if probe not in lr_cache:
            lr_cache[probe] = cdr._load_display_linear(
                ROUND2 / f"{probe}_4053.tif", crop8=False)
        return lr_cache[probe]

    def measure(probe: str, h: float, s: float, label: str) -> dict:
        tif = render_op(probe, h, s)
        ours = cdr._load_display_linear(tif, crop8=True)
        m = cdr._metrics(ours, lr_lin(probe))
        print(f"  {label} {probe}: ΔE {m['de_mean']:.3f} "
              f"(ΔL {m['dL_mean']:.3f} ΔC {m['dC_mean']:.3f})")
        return m

    def bin_residual(probe: str, h: float, s: float) -> np.ndarray:
        """Per-scene-bin log2 luminance residual (LR − ours) mapped onto
        the knot grid — the stage-2 secant update direction."""
        tif = render_op(probe, h, s)
        ours = cdr._load_display_linear(tif, crop8=True)
        o_lum = (ours @ cdr._LUM_W).ravel()
        l_lum = (lr_lin(probe) @ cdr._LUM_W).ravel()
        med_o = np.full(cdr.N_BINS, np.nan)
        med_l = np.full(cdr.N_BINS, np.nan)
        centers = np.full(cdr.N_BINS, np.nan)
        for b in range(cdr.N_BINS):
            m = sidx == b
            if m.sum() < 200:
                continue
            med_o[b] = np.median(np.log2(np.maximum(o_lum[m], 1e-6)))
            med_l[b] = np.median(np.log2(np.maximum(l_lum[m], 1e-6)))
            centers[b] = np.log2(np.sqrt(s_edges[b] * s_edges[b + 1]))
        ok = ~np.isnan(med_o)
        slope = np.clip(np.gradient(med_o[ok], centers[ok]), 0.2, 5.0)
        err = (med_l[ok] - med_o[ok]) / slope
        return np.interp(knot_x, centers[ok], err)

    # ---- stage 2: refine each anchor table through the real op ----
    refine_log: dict = {}
    for probe, (h, s, attr) in ANCHORS.items():
        hist = []
        for it in range(REFINE_ITERS + 1):
            m = measure(probe, h, s, f"refine{it}")
            hist.append(m["de_mean"])
            if it == REFINE_ITERS:
                break
            upd = np.clip(bin_residual(probe, h, s), -1.0, 1.0)
            setattr(st, attr, getattr(st, attr) + upd)
        refine_log[probe] = hist
    results["stage2_refined_tables"] = {
        a: getattr(st, a).tolist() for _p, (_h, _s, a) in ANCHORS.items()}
    results["stage2_de_history"] = refine_log

    # ---- optional stage 3: LLF sigma_r/last_level sweep (refined tables) ----
    if args.sweep_radius:
        sweep: dict = {}
        best, best_de = (st._LLF_SIGMA_R, st._LLF_LAST_LEVEL), np.inf
        for sigma_r in (0.9, 1.32, 2.0):
            for last_level in (5, 6, 7):
                st._LLF_SIGMA_R, st._LLF_LAST_LEVEL = sigma_r, last_level
                ms = [measure(p, *ANCHORS[p][:2],
                              f"sweep sr{sigma_r} L{last_level}")
                      for p in ("CALHIM100", "CALSH100")]
                de = float(np.mean([m["de_mean"] for m in ms]))
                sweep[f"sr{sigma_r}_L{last_level}"] = de
                if de < best_de:
                    best, best_de = (sigma_r, last_level), de
        st._LLF_SIGMA_R, st._LLF_LAST_LEVEL = best
        results["llf_sweep"] = sweep
        results["llf_best"] = {"sigma_r": best[0], "last_level": best[1]}

    # ---- final validation ----
    results["validation"] = {
        p: measure(p, h, s, "final")
        for p, (h, s, _a) in ANCHORS.items()}
    results["final_render_tags"] = {
        p: f"hlsh_{p}_{op_hash()}" for p in ANCHORS}  # tools/hlsh_flips.py
    results["final_llf_params"] = [st._LLF_SIGMA_R, st._LLF_LAST_LEVEL]
    results["arm_s_reference"] = {
        p: ev["probes"][p]["arm_s"]["de_mean"] for p in ANCHORS}

    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(results, indent=1))
    print(f"\nevidence -> {EVIDENCE}")

    if args.emit_tables:
        print("\n# pinned tables for src/lrt_cinema/scene_tone.py:")
        print(f"_KNOT_X = np.array({np.round(knot_x, 4).tolist()})")
        for _p, (_h, _s, attr) in ANCHORS.items():
            vals = np.round(getattr(st, attr), 4).tolist()
            print(f"{attr} = np.array({vals})")
        print(f"_LLF_SIGMA_R = {st._LLF_SIGMA_R}")
        print(f"_LLF_LAST_LEVEL = {st._LLF_LAST_LEVEL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
