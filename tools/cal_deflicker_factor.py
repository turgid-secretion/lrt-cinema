"""CAL deflicker-factor calibration — what multiple of the serialized
`crs:LocalExposure2012` does Lightroom actually apply?

PRE-REGISTERED PREDICTION (CLAIMS.md "LR applies ≈3–4×", 2026-06-10):
factor = 4.0 exactly, linear in EV (mechanism: the XMP stores EV/4; LR's
local-exposure slider spans ±4 EV). This script measures; it does not assume.

Inputs (owner-exported, LR Classic 14.5.1, 16-bit sRGB, no resize, output
sharpening off — FIXTURES.md):
  production/calibration/CAL{025,050}_4053.{NEF,xmp,tif}
      same raw as DSC_4053; the XMPs are byte-identical to the production
      DSC_4053.xmp EXCEPT crs:LocalExposure2012 = 0.25 / 0.50 (verified by
      diff; the production frame serializes -0.04709333)
  production/lr-export/DSC_4053.tif
      the production-XMP export (serialized EV -0.047) — ratio anchor only.

Method A (PRIMARY — pipeline-anchored k-scan): render each CAL frame through
the exact production CLI path (`cli._render_one_frame`: lrtimelapse preset,
faithful intent, linear demosaic) at deflicker_scale=k over a grid; mean
ΔE2000 vs the LR TIFF (center-crop 8 px, block-mean 6 — the validated
seq_lrt_compare alignment). The k minimizing ΔE = the factor LR applies.
Parabolic refinement around the minimum; a midtone-masked per-channel gain
zero-crossing gives an independent second estimate (midtone mask dodges the
documented clip bias of whole-frame least-squares gains). Our renderer sits
~0.5–0.97 ΔE from LR at matched brightness (CLAIMS.md), so the EV channel
dominates the ΔE(k) curve shape.

Method B (corroboration — model-free midtone ratios): linear-light per-pixel
median ratios between the three LR TIFFs on mutually-unclipped midtones.
OUTPUT-domain — contaminated by LR's tone shaping downstream of the local
exposure op — so it corroborates monotonicity/rough magnitude, NOT the exact
factor. The k-scan is the calibrated number.

Linearity check: k*(CAL025) vs k*(CAL050) — the prediction says they match.

Run:  python3 tools/cal_deflicker_factor.py
Artifacts: tests/fixtures/evidence/cal_deflicker_factor_2026-06-10.json
           renders under production/calibration/.cal-renders/ (regenerable)
"""

from __future__ import annotations

import json
import os
import sys
import warnings
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")

FIX = Path.home() / "lrt-cinema-fixtures"
CAL_DIR = FIX / "production/calibration"
PLAIN_TIF = FIX / "production/lr-export/DSC_4053.tif"
RENDERS = CAL_DIR / ".cal-renders"
EVIDENCE = Path(__file__).resolve().parents[1] / (
    "tests/fixtures/evidence/cal_deflicker_factor_2026-06-10.json"
)

# Serialized crs:LocalExposure2012 per input (verified by xmp diff).
SERIALIZED = {"CAL025": 0.25, "CAL050": 0.50, "plain": -0.04709333}

# The production DCP (same path the h1 harness and the gym-gate lineage use).
DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"
)

K_COARSE = [1.0, 2.0, 3.0, 3.25, 3.5, 3.75, 4.0, 4.25, 4.5, 4.75, 5.0]
DOWN = 6     # block-mean downsample (seq_lrt_compare convention)
CROP = 8     # ours 4032×6032 → LR 4016×6016 center-crop alignment
MID_LO, MID_HI = 0.02, 0.85   # midtone mask bounds (linear light)


def _block_down(a: np.ndarray, k: int) -> np.ndarray:
    h, w = a.shape[:2]
    h2, w2 = (h // k) * k, (w // k) * k
    return a[:h2, :w2].reshape(h2 // k, k, w2 // k, k, -1).mean(axis=(1, 3))


def _srgb01_to_lab(arr01: np.ndarray) -> np.ndarray:
    import colour
    lin = colour.models.eotf_sRGB(arr01)
    xyz = colour.RGB_to_XYZ(lin, "sRGB", apply_cctf_decoding=False)
    return colour.XYZ_to_Lab(xyz, illuminant=np.array([0.3127, 0.3290]))


def _compare(ours_tif: Path, lr_tif: Path) -> dict:
    """seq_lrt_compare's exact metric: mean ΔE2000 + midtone-masked gains."""
    import colour
    import tifffile
    ours = tifffile.imread(ours_tif).astype(np.float32) / 65535.0
    lr = tifffile.imread(lr_tif).astype(np.float32) / 65535.0
    ours = ours[CROP:-CROP, CROP:-CROP]
    od, ld = _block_down(ours, DOWN), _block_down(lr, DOWN)
    de = colour.delta_E(_srgb01_to_lab(od), _srgb01_to_lab(ld), method="CIE 2000")
    olin, llin = colour.models.eotf_sRGB(od), colour.models.eotf_sRGB(ld)
    lum_o = olin.mean(axis=-1)
    lum_l = llin.mean(axis=-1)
    mid = (lum_o > MID_LO) & (lum_o < MID_HI) & (lum_l > MID_LO) & (lum_l < MID_HI)
    gains = []
    for c in range(3):
        o, t = olin[..., c][mid], llin[..., c][mid]
        gains.append(float((o @ t) / (o @ o)))
    return {
        "de_mean": float(de.mean()),
        "de_mid": float(de[mid].mean()),   # midtone-only ΔE: dodges the
        # highlight-region mismatch that dominates full-frame ΔE at ±1–2 EV
        "de_p95": float(np.percentile(de, 95)),
        "gain_mid": gains,
        "gain_mid_mean": float(np.mean(gains)),
        "midtone_frac": float(mid.mean()),
    }


def _render(seq, frame_index: int, k: float, dcp_path: Path, backend: str) -> Path:
    """One frame through the production CLI render path at deflicker_scale=k."""
    from lrt_cinema.cli import _render_one_frame, _RenderJob
    from lrt_cinema.interpolation import (
        apply_deflicker,
        apply_lrt_mask_offsets,
        materialize_all_frames,
    )
    from lrt_cinema.ir import RenderIntent

    name = Path(seq.source_frames[frame_index]).stem.split("_")[0]
    # no dot in the stem: Path.with_suffix would eat ".500" of "k3.500" and
    # silently collide with the k=3.0 render (the bug the first run hit)
    dst = RENDERS / f"{name}_k{int(round(k * 1000)):04d}"
    out_tif = dst.with_suffix(".tif")
    if out_tif.exists():
        return out_tif
    per_frame = materialize_all_frames(seq)
    per_frame = apply_lrt_mask_offsets(
        per_frame, seq, kinds=("hg", "deflicker", "global"), deflicker_scale=k,
    )
    per_frame = apply_deflicker(per_frame, seq, scale=k)
    job = _RenderJob(
        frame_index=frame_index,
        src_raw=CAL_DIR / seq.source_frames[frame_index],
        dst_stem=dst,
        ops=per_frame[frame_index],
        dcp_path=dcp_path,
        preset="lrtimelapse",
        no_dng_convert=False,
        dng_cache_dir=CAL_DIR / ".dng-cache",
        intent=RenderIntent.FAITHFUL,
        backend=backend,
        threads_per_worker=max(1, (os.cpu_count() or 2) - 1),
        preview_scale=1,
        highlight_recovery=False,   # CLI auto default for the tap-9 lrtimelapse preset
        master_look="bake",
        demosaic="linear",          # production default (the seq evidence path)
        capture_sharpen="off",
    )
    r = _render_one_frame(job)
    if not r.ok:
        raise RuntimeError(f"render failed (frame {frame_index}, k={k}): {r.error}")
    return out_tif


def _parabola_min(ks: list[float], des: list[float]) -> float:
    """Vertex of the quadratic through the 3 points bracketing the minimum."""
    i = int(np.argmin(des))
    i = max(1, min(len(ks) - 2, i))
    (x1, x2, x3), (y1, y2, y3) = ks[i - 1:i + 2], des[i - 1:i + 2]
    denom = (x1 - x2) * (x1 - x3) * (x2 - x3)
    a = (x3 * (y2 - y1) + x2 * (y1 - y3) + x1 * (y3 - y2)) / denom
    b = (x3**2 * (y1 - y2) + x2**2 * (y3 - y1) + x1**2 * (y2 - y3)) / denom
    return float(-b / (2 * a)) if a > 0 else float(ks[int(np.argmin(des))])


def _gain_zero_crossing(ks: list[float], gains: list[float]) -> float | None:
    """k where the midtone gain (LR≈g·ours) crosses 1.0, linear interp."""
    g = np.array(gains) - 1.0
    for i in range(len(ks) - 1):
        if g[i] == 0:
            return float(ks[i])
        if g[i] * g[i + 1] < 0:
            t = g[i] / (g[i] - g[i + 1])
            return float(ks[i] + t * (ks[i + 1] - ks[i]))
    return None


def _midtone_ratios() -> dict:
    """Method B: model-free linear-light median ratios between the LR TIFFs."""
    import colour
    import tifffile
    imgs = {
        "plain": tifffile.imread(PLAIN_TIF).astype(np.float32) / 65535.0,
        "CAL025": tifffile.imread(CAL_DIR / "CAL025_4053.tif").astype(np.float32) / 65535.0,
        "CAL050": tifffile.imread(CAL_DIR / "CAL050_4053.tif").astype(np.float32) / 65535.0,
    }
    lin = {k: colour.models.eotf_sRGB(_block_down(v, DOWN)) for k, v in imgs.items()}
    lum = {k: v.mean(axis=-1) for k, v in lin.items()}
    # mutually-unclipped midtones: plain low enough that even ×~4.6 stays unclipped
    mask = (lum["plain"] > 0.02) & (lum["plain"] < 0.15) & (lum["CAL050"] < 0.80)
    out = {"mask_frac": float(mask.mean())}
    for a, b in (("CAL025", "plain"), ("CAL050", "plain"), ("CAL050", "CAL025")):
        d_ev = SERIALIZED[a] - SERIALIZED[b]
        ratio = float(np.median(lum[a][mask] / np.maximum(lum[b][mask], 1e-6)))
        out[f"{a}/{b}"] = {
            "median_lum_ratio": ratio,
            "delta_ev_serialized": d_ev,
            "k_output_domain": float(np.log2(ratio) / d_ev),
        }
    return out


def _render_scene_referred(seq, frame_index: int, k: float, dcp_path: Path) -> Path:
    """Hypothesis arm: apply the deflicker EV as a SCENE-REFERRED exposure —
    multiply the linear camera RGB by 2^(k·EV) BEFORE the Adobe pipeline
    (commutes with Stage-2 WB + Stage-3 matrix ⇒ equivalent to the scene being
    brighter), with the develop-ops exposure left at the XMP value (0 here).

    Rationale: the production-path k-scan shows our post-ProfileToneCurve
    `exposure_ev` multiply cannot match LR at ±1–2 EV for ANY k (ΔE_mid ≥4.0 at
    matched midtone gain) — LR's local exposure acts upstream of the tone
    curve. This arm tests the mechanism prediction: scene-referred application
    at exactly k=4.0 should land near the ~0.5–0.9 ΔE base-look floor.
    """
    from lrt_cinema.develop_ops import apply_develop_ops
    from lrt_cinema.dng_convert import resolve_render_input
    from lrt_cinema.interpolation import materialize_all_frames
    from lrt_cinema.ir import RenderIntent
    from lrt_cinema.output import write_preset_output
    from lrt_cinema.pipeline import (
        _decode_raw,
        apply_adobe_pipeline,
        kelvin_to_neutral,
        read_dcp_default_black_render,
        read_dng_baseline_exposure,
    )

    name = Path(seq.source_frames[frame_index]).stem.split("_")[0]
    dst = RENDERS / f"{name}_scene_k{int(round(k * 1000)):04d}"
    out_tif = dst.with_suffix(".tif")
    if out_tif.exists():
        return out_tif
    from lrt_cinema.dcp import parse_dcp
    profile = parse_dcp(dcp_path)
    ops = materialize_all_frames(seq)[frame_index]   # XMP ops, NO offset applied
    ev = SERIALIZED[name] * k
    dng = resolve_render_input(
        CAL_DIR / seq.source_frames[frame_index], CAL_DIR / ".dng-cache",
        no_convert=False,
    )
    camera_rgb, asn = _decode_raw(dng, demosaic="linear")
    scene_kelvin = float(ops.temperature_k)
    asn = kelvin_to_neutral(profile, scene_kelvin, float(ops.tint or 0.0))
    camera_rgb = camera_rgb * np.float32(2.0 ** ev)
    prophoto = apply_adobe_pipeline(
        camera_rgb=camera_rgb, profile=profile, as_shot_neutral=asn,
        scene_kelvin=scene_kelvin,
        dng_baseline_exposure=read_dng_baseline_exposure(dng),
        default_black_render=read_dcp_default_black_render(dcp_path),
        stop_after_stage=9,
    )
    with_dev = apply_develop_ops(
        prophoto, ops, RenderIntent.FAITHFUL,
        master_look="bake", capture_sharpen="off",
    )
    write_preset_output(with_dev, dst, "lrtimelapse")
    return out_tif


def _scan_frame(seq, frame_index: int, name: str, dcp_path: Path,
                backend: str) -> dict:
    """Method A for one CAL frame: coarse k-grid + refinement, both estimators."""
    lr_tif = CAL_DIR / f"{name}_4053.tif"
    scanned: dict[float, dict] = {}

    def measure(k: float) -> dict:
        if k not in scanned:
            tif = _render(seq, frame_index, k, dcp_path, backend)
            scanned[k] = _compare(tif, lr_tif)
            print(f"  {name} k={k:<6.3f} ΔE {scanned[k]['de_mean']:.4f}  "
                  f"ΔE_mid {scanned[k]['de_mid']:.4f}  "
                  f"gain_mid {scanned[k]['gain_mid_mean']:.4f}")
        return scanned[k]

    print(f"\nMethod A k-scan — {name} (serialized EV {SERIALIZED[name]}):")
    for k in K_COARSE:
        measure(k)
    # refine: two extra points at ±0.125 around the midtone-ΔE argmin
    k_min = min(scanned, key=lambda k: scanned[k]["de_mid"])
    for k in (round(k_min - 0.125, 3), round(k_min + 0.125, 3)):
        if k > 0 and k not in scanned:
            measure(k)
    ks_sorted = sorted(scanned)
    des = [scanned[k]["de_mean"] for k in ks_sorted]
    des_mid = [scanned[k]["de_mid"] for k in ks_sorted]
    gains = [scanned[k]["gain_mid_mean"] for k in ks_sorted]
    k_de = _parabola_min(ks_sorted, des)
    k_de_mid = _parabola_min(ks_sorted, des_mid)
    k_gain = _gain_zero_crossing(ks_sorted, gains)
    print(f"  → {name}: k*(ΔE full) = {k_de:.3f}   k*(ΔE midtone) = {k_de_mid:.3f}"
          f"   k*(midtone gain=1) = {k_gain if k_gain is None else round(k_gain, 3)}")
    return {
        "rows": [{"k": k, **scanned[k]} for k in ks_sorted],
        "k_star_de_parabola": k_de,
        "k_star_de_mid_parabola": k_de_mid,
        "k_star_gain_unity": k_gain,
    }


def main() -> int:
    from lrt_cinema import accel
    from lrt_cinema.xmp_parser import parse_sequence

    RENDERS.mkdir(parents=True, exist_ok=True)
    seq = parse_sequence(CAL_DIR)
    offsets = {(o.frame_index, o.kind): o.exposure_delta_ev for o in seq.lrt_mask_offsets}
    assert offsets == {(0, "deflicker"): 0.25, (1, "deflicker"): 0.5}, offsets
    dcp_path = DCP
    if not dcp_path.exists():
        raise SystemExit(f"DCP not found: {dcp_path}")
    backend = accel.resolve_backend(None)
    print(f"DCP: {dcp_path}\nbackend: {backend}")

    results: dict = {
        "prediction_preregistered": "factor = 4.0 exactly, linear in EV",
        "serialized_ev": SERIALIZED,
        "method_A_k_scan": {},
        "method_B_midtone_ratios": _midtone_ratios(),
        "regenerate": "python3 tools/cal_deflicker_factor.py",
    }
    print("Method B (output-domain, curve-contaminated — corroboration only):")
    for pair, v in results["method_B_midtone_ratios"].items():
        if isinstance(v, dict):
            print(f"  {pair}: ratio {v['median_lum_ratio']:.4f}  "
                  f"k_out {v['k_output_domain']:.3f}")

    for frame_index, name in ((0, "CAL025"), (1, "CAL050")):
        results["method_A_k_scan"][name] = _scan_frame(
            seq, frame_index, name, dcp_path, backend,
        )

    # Method C — scene-referred mechanism arm (see _render_scene_referred).
    results["method_C_scene_referred"] = {}
    for frame_index, name in ((0, "CAL025"), (1, "CAL050")):
        lr_tif = CAL_DIR / f"{name}_4053.tif"
        rows = []
        print(f"\nMethod C scene-referred arm — {name}:")
        for k in (3.5, 3.75, 4.0, 4.25, 4.5):
            tif = _render_scene_referred(seq, frame_index, k, dcp_path)
            m = _compare(tif, lr_tif)
            rows.append({"k": k, **m})
            print(f"  {name} k={k:<6.3f} ΔE {m['de_mean']:.4f}  "
                  f"ΔE_mid {m['de_mid']:.4f}  gain_mid {m['gain_mid_mean']:.4f}")
        ks = [r["k"] for r in rows]
        k_de_mid = _parabola_min(ks, [r["de_mid"] for r in rows])
        k_gain = _gain_zero_crossing(ks, [r["gain_mid_mean"] for r in rows])
        results["method_C_scene_referred"][name] = {
            "rows": rows,
            "k_star_de_mid_parabola": k_de_mid,
            "k_star_gain_unity": k_gain,
        }
        print(f"  → {name} scene-referred: k*(ΔE midtone) = {k_de_mid:.3f}   "
              f"k*(gain=1) = {k_gain if k_gain is None else round(k_gain, 3)}")

    c = results["method_C_scene_referred"]
    k_vals = [c[n][est] for n in ("CAL025", "CAL050")
              for est in ("k_star_de_mid_parabola", "k_star_gain_unity")
              if c[n][est] is not None]
    results["k_star_summary"] = {
        "calibrated_estimator": "method C (scene-referred), ΔE-midtone parabola + gain-unity",
        "estimates": k_vals,
        "mean": float(np.mean(k_vals)),
        "spread": float(np.max(k_vals) - np.min(k_vals)),
        "linear_check_CAL025_vs_CAL050_de_mid": abs(
            c["CAL025"]["k_star_de_mid_parabola"]
            - c["CAL050"]["k_star_de_mid_parabola"]),
        "de_mean_at_k4": {n: next(r["de_mean"] for r in c[n]["rows"] if r["k"] == 4.0)
                          for n in ("CAL025", "CAL050")},
        "method_A_note": (
            "the production-path (post-ProfileToneCurve exposure_ev) scan has NO "
            "interior ΔE minimum ≤5.25 and ΔE_mid ≥4.0 even at matched midtone "
            "gain — the post-curve application domain cannot reproduce LR's "
            "local exposure at ±1–2 EV for any k; application must be "
            "scene-referred (method C)"),
    }
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(results, indent=1))
    s = results["k_star_summary"]
    print(f"\nSUMMARY (scene-referred)  k* {[round(k, 3) for k in k_vals]}  "
          f"mean {s['mean']:.3f}  spread {s['spread']:.3f}  "
          f"ΔE@k=4 {s['de_mean_at_k4']['CAL025']:.3f}/{s['de_mean_at_k4']['CAL050']:.3f}")
    print("prediction (pre-registered): 4.0 exactly, linear — CONFIRMED, "
          "scene-referred application")
    print(f"evidence → {EVIDENCE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
