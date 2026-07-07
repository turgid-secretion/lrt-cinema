"""Round-2 develop-slider DOMAIN + SHAPE fits (owner probe exports, 2026-07-07).

Ground truth: ~/lrt-cinema-fixtures/production/calibration/round2/ — 12 LR
Classic 16-bit sRGB exports of DSC_4053, each with exactly ONE slider set
(README.txt there). Per probe this harness measures, on the native grid
(ours cropped 8 px to LR's 4016x6016, then 4x block-mean):

  1. ours-as-is vs LR: ΔE2000 (mean/mid), mean |ΔL*| / mean |ΔC*| split,
     per-channel midtone gains — the gap our translation must close;
  2. the empirical tone response: log2 luminance delta (LR vs our base
     render) binned by BASE display-linear luminance — which tones moved
     = the fingerprint of the operator;
  3. ARM-D — best GLOBAL display-domain luminance curve: a monotone LUT
     fitted to the binned response, ratio-reapplied to the base render.
     KEY METHOD FACT: stages 2-9 are per-pixel, so a global curve in ANY
     domain composes into a global display-domain map — ARM-D's fitted
     LUT captures the *luminance* behaviour of every global hypothesis.
     What ARM-D CANNOT capture: (a) chroma treatment (a scene-domain gain
     changes rendered saturation through HSM/Look/RGBTone differently
     from a display-domain ratio — the discriminator that decided
     cal_exposure_domain), and (b) LOCAL operators;
  4. ARM-S — best GLOBAL scene-domain luminance curve: a monotone LUT on
     balanced-camera-RGB luminance (the slot-7 point, where Exposure2012
     and the mask EVs live), fitted iteratively through real pipeline
     renders. ΔE(ARM-S) vs ΔE(ARM-D) — and their ΔC split — is the
     scene-vs-display verdict for global sliders;
  5. LOCALITY: within-bin residual context correlation. After ARM-D
     removes the best global luminance map, a LOCAL operator leaves
     residual that correlates with the NEIGHBOURHOOD (same input
     luminance, different local context, different output); a global
     operator leaves floor-level noise. Statistic: within-bin-centred
     Pearson r between residual log2 luminance and log2 blurred-base
     context, at two context radii (~24 px and ~96 px native). The
     Contrast probes are the empirical GLOBAL yardstick for the floor.

PRE-REGISTERED (2026-07-07, before any render; author: the repair session):
  P1 Highlights -50/-100: LOCAL — armD residual context-corr >= 3x the
     Contrast yardstick and armD ΔE well above the ~0.2 base floor.
     Response concentrated Y >~ 0.4; -100 sublinear (< 2x the -50 delta).
  P2 Shadows +50/+100: LOCAL — same signature; lift concentrated
     Y <~ 0.25; highlights ~untouched.
  P3 Contrast +/-50: GLOBAL (this is the yardstick); our faithful
     apply_contrast_2012 has the right direction and lands within ~2x of
     LR's magnitude.
  P4 Blacks +/-50: GLOBAL, concentrated Y <~ 0.05; our uniform-bias
     apply_blacks_2012 mismatches the SHAPE (it shifts all tones).
  P5 Whites +/-50: GLOBAL or weakly local; +50 BRIGHTENS the top bins
     (Lightroom's direction — decides the REFUTED-direction docstring on
     apply_dr_compression's c_top).
  P6 scene-vs-display per global slider: genuinely open; ARM-S vs ARM-D
     ΔE/ΔC decides (precedent: exposure class is scene-referred).
  P7 HSLBLU / CGSH: our faithful apply_hsl / apply_color_grade land
     within ~2x the base-look floor (ΔE_mean < 0.5).

Run:  python3 tools/cal_domain_round2.py [--probe NAME] [--skip-arm-s]
Out:  tests/fixtures/evidence/cal_domain_round2_2026-07-07.json
      (renders cached in round2/.cal-renders/, safe to delete)
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
EVIDENCE = Path(__file__).resolve().parent.parent / (
    "tests/fixtures/evidence/cal_domain_round2_2026-07-07.json"
)

PROBES = (
    "CALHIM50", "CALHIM100", "CALSH50", "CALSH100",
    "CALCON50", "CALCONM50", "CALBL50", "CALBLM50",
    "CALWH50", "CALWHM50", "CALHSLBLU", "CALCGSH",
)
# Colour-op probes get metrics but no luminance-LUT arms (their signal is
# chromatic; a luminance LUT is the wrong model class).
LUM_PROBES = PROBES[:10]

DOWN = 4
MID_LO, MID_HI = 0.04, 0.60
N_BINS = 24
CTX_RADII = (6, 24)          # in DOWN-blocks: ~24 px and ~96 px native
ARM_S_ITERS = 3
ARM_S_KNOTS = 33
_LUM_W = np.array([0.2126, 0.7152, 0.0722])   # Rec.709/sRGB display luminance


# --------------------------------------------------------------------------
# image loading / stats
# --------------------------------------------------------------------------

def _block_down(a: np.ndarray, k: int) -> np.ndarray:
    h, w = a.shape[:2]
    h2, w2 = (h // k) * k, (w // k) * k
    return a[:h2, :w2].reshape(h2 // k, k, w2 // k, k, -1).mean(axis=(1, 3))


def _load_display_linear(tif: Path, crop8: bool) -> np.ndarray:
    """16-bit sRGB TIFF -> display-linear RGB at the DOWN-block grid."""
    import colour
    import tifffile
    img = tifffile.imread(str(tif)).astype(np.float32) / 65535.0
    if crop8:
        img = img[8:-8, 8:-8]
    return colour.models.eotf_sRGB(_block_down(img, DOWN))


def _lab(display_linear: np.ndarray) -> np.ndarray:
    import colour
    return colour.XYZ_to_Lab(
        colour.RGB_to_XYZ(display_linear, "sRGB", apply_cctf_decoding=False),
        illuminant=np.array([0.3127, 0.3290]),
    )


def _box_blur(a: np.ndarray, r: int) -> np.ndarray:
    from scipy.ndimage import uniform_filter
    return uniform_filter(a, size=2 * r + 1, mode="nearest")


def _metrics(ours_lin: np.ndarray, lr_lin: np.ndarray) -> dict:
    """ΔE2000 + ΔL/ΔC split + midtone per-channel gains (ours -> LR)."""
    import colour
    lo, lt = _lab(ours_lin), _lab(lr_lin)
    de = colour.delta_E(lo, lt, method="CIE 2000")
    c_o = np.hypot(lo[..., 1], lo[..., 2])
    c_t = np.hypot(lt[..., 1], lt[..., 2])
    lum_o = ours_lin @ _LUM_W
    lum_t = lr_lin @ _LUM_W
    mid = (lum_o > MID_LO) & (lum_o < MID_HI) & (lum_t > MID_LO) & (lum_t < MID_HI)
    gains = [
        float((ours_lin[..., c][mid] @ lr_lin[..., c][mid])
              / (ours_lin[..., c][mid] @ ours_lin[..., c][mid]))
        for c in range(3)
    ]
    return {
        "de_mean": float(de.mean()), "de_p95": float(np.percentile(de, 95)),
        "de_mid": float(de[mid].mean()),
        "dL_mean": float(np.abs(lo[..., 0] - lt[..., 0]).mean()),
        "dC_mean": float(np.abs(c_o - c_t).mean()),
        "gain_mid": gains, "midtone_frac": float(mid.mean()),
    }


def _log_bins(base_lum: np.ndarray) -> np.ndarray:
    lo = np.percentile(base_lum, 0.1)
    hi = np.percentile(base_lum, 99.9)
    lo = max(lo, 1e-5)
    return np.geomspace(lo, hi, N_BINS + 1)


def _binned_response(base_lum: np.ndarray, img_lum: np.ndarray,
                     edges: np.ndarray) -> dict:
    """Per base-luminance bin: median log2(img/base) — the tone fingerprint."""
    idx = np.digitize(base_lum.ravel(), edges) - 1
    ratio = np.log2(np.maximum(img_lum, 1e-6) / np.maximum(base_lum, 1e-6)).ravel()
    centers, deltas, counts, bins = [], [], [], []
    for b in range(N_BINS):
        m = idx == b
        n = int(m.sum())
        if n < 50:
            continue
        centers.append(float(np.sqrt(edges[b] * edges[b + 1])))
        deltas.append(float(np.median(ratio[m])))
        counts.append(n)
        bins.append(b)
    return {"bin_center_lum": centers, "median_log2_ratio": deltas,
            "count": counts, "bin_index": bins}


def _locality(base_lum: np.ndarray, residual_log2: np.ndarray,
              edges: np.ndarray, active_bins: set[int] | None = None) -> dict:
    """Within-bin-centred correlation of residual vs blurred-base context.

    residual_log2: log2(LR / armD(base)) luminance — the part no global
    display curve explains. For each context radius: centre both residual
    and log2-context within base-luminance bins, then one pooled Pearson r
    (+ per-bin residual std for the shape). `active_bins` additionally
    pools ONLY the bins the operator measurably moved (|median log2
    response| > 0.15) — a local operator's signature concentrates there;
    reported as ctx_active_r*.
    """
    out: dict = {}
    idx = np.digitize(base_lum.ravel(), edges) - 1
    res = residual_log2.ravel()
    for r in CTX_RADII:
        ctx = np.log2(np.maximum(_box_blur(base_lum, r), 1e-6)).ravel()
        res_c = np.full_like(res, np.nan)
        ctx_c = np.full_like(res, np.nan)
        act = np.zeros(res.shape, dtype=bool)
        stds = []
        for b in range(N_BINS):
            m = idx == b
            if m.sum() < 200:
                continue
            res_c[m] = res[m] - res[m].mean()
            ctx_c[m] = ctx[m] - ctx[m].mean()
            stds.append(float(res[m].std()))
            if active_bins and b in active_bins:
                act[m] = True
        ok = ~np.isnan(res_c)

        def _pooled_r(mask: np.ndarray, res_c=res_c, ctx_c=ctx_c) -> float:
            rr, cc = res_c[mask], ctx_c[mask]
            if rr.size < 1000:
                return 0.0
            denom = rr.std() * cc.std()
            return float((rr * cc).mean() / denom) if denom > 0 else 0.0

        out[f"ctx_r{r}"] = _pooled_r(ok)
        if active_bins is not None:
            out[f"ctx_active_r{r}"] = _pooled_r(ok & act)
        out[f"resid_std_r{r}"] = float(np.mean(stds)) if stds else 0.0
    out["within_bin_resid_std"] = out.pop(f"resid_std_r{CTX_RADII[0]}")
    out.pop(f"resid_std_r{CTX_RADII[1]}", None)
    return out


# --------------------------------------------------------------------------
# LUT arms
# --------------------------------------------------------------------------

def _fit_display_lut(base_lum: np.ndarray, lr_lum: np.ndarray,
                     edges: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Monotone-x piecewise-linear LUT log2(base)->log2(target) from binned
    medians (the best global display-domain curve, non-parametric)."""
    resp = _binned_response(base_lum, lr_lum, edges)
    x = np.log2(np.array(resp["bin_center_lum"]))
    y = x + np.array(resp["median_log2_ratio"])
    return x, y


def _apply_display_lut(rgb_lin: np.ndarray, x: np.ndarray,
                       y: np.ndarray) -> np.ndarray:
    lum = rgb_lin @ _LUM_W
    l2 = np.log2(np.maximum(lum, 1e-6))
    lut_out = np.exp2(np.interp(l2, x, y))
    ratio = lut_out / np.maximum(lum, 1e-6)
    return np.clip(rgb_lin * ratio[..., None], 0.0, 1.0)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", default=None, help="run a single probe")
    ap.add_argument("--skip-arm-s", action="store_true")
    args = ap.parse_args()

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

    RENDERS.mkdir(parents=True, exist_ok=True)
    profile = parse_dcp(DCP)
    dbr = read_dcp_default_black_render(DCP)
    base_be = read_dng_baseline_exposure(DNG)

    # WB + mask EVs are constant across all probes (single-variable XMPs) —
    # decode once at the shared develop WB.
    ops0, _kf, _dfk, _r, mask_offsets = parse_xmp_file(
        ROUND2 / f"{PROBES[0]}_4053.xmp")
    scene_ev = LR_LOCAL_EXPOSURE_SCALE * sum(e for _k, e in mask_offsets)
    kelvin = float(ops0.temperature_k)
    asn = kelvin_to_neutral(profile, kelvin, float(ops0.tint or 0.0))
    cam, _, _ = _decode_raw(DNG, demosaic="linear", wb_asn=asn)

    def render(tag: str, ops_in, cam_in) -> Path:
        dst = RENDERS / tag
        out = dst.with_suffix(".tif")
        if out.exists():
            return out
        pp = apply_adobe_pipeline(
            camera_rgb=cam_in * np.float32(2.0 ** ops_in.scene_exposure_ev),
            profile=profile, as_shot_neutral=asn, scene_kelvin=kelvin,
            dng_baseline_exposure=base_be, default_black_render=dbr,
            stop_after_stage=9)
        pp = apply_develop_ops(pp, ops_in, RenderIntent.FAITHFUL,
                               master_look="bake", capture_sharpen="off")
        write_preset_output(pp, dst, "lrtimelapse")
        return out

    def zeroed(ops):
        return replace(
            ops, highlights=0.0, shadows=0.0, whites=0.0, contrast=0.0,
            blacks=0.0, scene_exposure_ev=scene_ev,
            hsl=type(ops.hsl)(), color_grade=type(ops.color_grade)())

    base_ops = zeroed(ops0)
    base_tif = render("BASE", base_ops, cam)
    base_lin = _load_display_linear(base_tif, crop8=True)
    base_lum = base_lin @ _LUM_W
    edges = _log_bins(base_lum)

    # Scene luminance at the slot-7 point, on the SAME grid as the display
    # arrays (crop 8 native, then DOWN-block).
    scene_lum_native = (cam * np.float32(2.0 ** scene_ev)).mean(axis=-1)
    scene_lum = _block_down(
        scene_lum_native[8:-8, 8:-8, None], DOWN)[..., 0].astype(np.float64)
    del scene_lum_native

    probes = [args.probe] if args.probe else list(PROBES)
    results: dict = {
        "pre_registered": "P1/P2 H+S LOCAL; P3 contrast global yardstick; "
                          "P4 blacks global near-black, our shape wrong; "
                          "P5 whites +50 brightens; P6 open; P7 colour ops "
                          "< 0.5 dE (full text: tools/cal_domain_round2.py)",
        "grid": f"crop8 + block{DOWN}, {N_BINS} log bins",
        "probes": {},
    }
    if EVIDENCE.exists():
        results = json.loads(EVIDENCE.read_text())
        results.setdefault("probes", {})

    for name in probes:
        lr_tif = ROUND2 / f"{name}_4053.tif"
        ops, _, _, _, mo = parse_xmp_file(ROUND2 / f"{name}_4053.xmp")
        assert LR_LOCAL_EXPOSURE_SCALE * sum(e for _k, e in mo) == scene_ev
        ops = replace(ops, scene_exposure_ev=scene_ev)
        lr_lin = _load_display_linear(lr_tif, crop8=False)
        lr_lum = lr_lin @ _LUM_W
        row: dict = {}

        # 1. ours-as-is (current faithful path: H/S/W dropped, others live)
        ours_tif = render(f"{name}_ours", ops, cam)
        ours_lin = _load_display_linear(ours_tif, crop8=True)
        row["ours_vs_lr"] = _metrics(ours_lin, lr_lin)

        # 2. empirical tone response vs base
        row["response_vs_base"] = _binned_response(base_lum, lr_lum, edges)
        row["base_vs_lr_de"] = _metrics(base_lin, lr_lin)["de_mean"]

        if name in LUM_PROBES:
            # 3. ARM-D: best global display-domain curve
            x, y = _fit_display_lut(base_lum, lr_lum, edges)
            armd_lin = _apply_display_lut(base_lin, x, y)
            row["arm_d"] = _metrics(armd_lin, lr_lin)
            row["arm_d"]["lut_x_log2"] = x.tolist()
            row["arm_d"]["lut_y_log2"] = y.tolist()

            # 5. locality on the armD residual, incl. operator-active bins
            resid = np.log2(np.maximum(lr_lum, 1e-6)
                            / np.maximum(armd_lin @ _LUM_W, 1e-6))
            resp = row["response_vs_base"]
            active = {b for b, d in zip(
                resp["bin_index"], resp["median_log2_ratio"], strict=True)
                if abs(d) > 0.15}
            row["locality"] = _locality(base_lum, resid, edges,
                                        active_bins=active)

            # 4. ARM-S: best global scene-domain curve (iterative)
            if not args.skip_arm_s:
                s_edges = _log_bins(scene_lum)
                kx = np.log2(np.geomspace(s_edges[0], s_edges[-1],
                                          ARM_S_KNOTS))
                ky = kx.copy()
                cam_l2_native = np.log2(np.maximum(
                    (cam * np.float32(2.0 ** scene_ev)).mean(axis=-1), 1e-6))
                arm_lin = None
                ky_used = ky
                for it in range(ARM_S_ITERS):
                    ky_used = ky.copy()
                    lut_out = np.exp2(np.interp(cam_l2_native, kx, ky))
                    ratio = (lut_out / np.maximum(
                        np.exp2(cam_l2_native), 1e-6)).astype(np.float32)
                    lut_id = hashlib.md5(ky.tobytes()).hexdigest()[:8]
                    tag = f"{name}_armS_it{it}_{lut_id}"
                    arm_tif = render(tag, base_ops, cam * ratio[..., None])
                    arm_lin = _load_display_linear(arm_tif, crop8=True)
                    arm_lum = arm_lin @ _LUM_W
                    # per scene-bin luminance error + local scene->display slope
                    sidx = np.digitize(scene_lum.ravel(), s_edges) - 1
                    med_arm = np.full(N_BINS, np.nan)
                    med_lr = np.full(N_BINS, np.nan)
                    centers = np.full(N_BINS, np.nan)
                    for b in range(N_BINS):
                        m = sidx == b
                        if m.sum() < 200:
                            continue
                        med_arm[b] = np.median(np.log2(np.maximum(
                            arm_lum.ravel()[m], 1e-6)))
                        med_lr[b] = np.median(np.log2(np.maximum(
                            lr_lum.ravel()[m], 1e-6)))
                        centers[b] = np.log2(
                            np.sqrt(s_edges[b] * s_edges[b + 1]))
                    okb = ~np.isnan(med_arm)
                    if okb.sum() < 4:
                        break
                    slope = np.gradient(med_arm[okb], centers[okb])
                    slope = np.clip(slope, 0.2, 5.0)
                    delta_b = (med_lr[okb] - med_arm[okb]) / slope
                    err = np.interp(kx, centers[okb], delta_b)
                    ky = ky + np.clip(err, -1.0, 1.0)
                    ky = np.maximum.accumulate(ky)  # keep the LUT monotone
                if arm_lin is not None:
                    row["arm_s"] = _metrics(arm_lin, lr_lin)
                    row["arm_s"]["iters"] = ARM_S_ITERS
                    # the LUT that PRODUCED the final rendered arm (ky itself
                    # receives one more un-rendered update after the loop)
                    row["arm_s"]["lut_x_log2"] = kx.tolist()
                    row["arm_s"]["lut_y_log2"] = ky_used.tolist()

        results["probes"][name] = row
        d = row["ours_vs_lr"]
        msg = (f"{name:11s} ours ΔE {d['de_mean']:.3f} "
               f"(ΔL {d['dL_mean']:.3f} ΔC {d['dC_mean']:.3f})")
        if "arm_d" in row:
            msg += f"  armD ΔE {row['arm_d']['de_mean']:.3f}"
        if "arm_s" in row:
            msg += f"  armS ΔE {row['arm_s']['de_mean']:.3f}"
        if "locality" in row:
            loc = row["locality"]
            msg += (f"  ctx r{CTX_RADII[0]}/{CTX_RADII[1]}: "
                    f"{loc[f'ctx_r{CTX_RADII[0]}']:+.3f}/"
                    f"{loc[f'ctx_r{CTX_RADII[1]}']:+.3f}")
        print(msg)

    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(results, indent=1))
    print(f"\nevidence -> {EVIDENCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
