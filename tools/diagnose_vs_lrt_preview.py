#!/usr/bin/env python3
"""Diagnostic: compare an lrt-cinema-rendered TIFF against an LRT preview JPG.

Project's primary colorimetric-divergence diagnostic for the
"does our render match LRT's preview?" question. Produces four reports:

1. ΔE2000 percentile distribution + bucket histogram
2. Spatial ΔE heatmap (JPG) — locates where divergence concentrates
3. Affine-fit decomposition — finds the single per-channel
   gain+offset grade that minimizes residual ΔE. Critical for
   distinguishing "our gap is a grading transform" (small residual)
   from "our gap is structural" (large residual).
4. Per-channel L*a*b* percentile distribution comparison — surfaces
   where in tonal/chromatic range the divergence lives (shadows vs
   midtones vs highlights, red/green/blue cast bias).

Why these four and NOT mean L*a*b*: see docs/VALIDATION.md
"Methodology — comparing two renders of the same scene." Mean
collapses all spatial info into one scalar, dragged by outliers,
and conflates uniform shifts with localized defects.

Usage:
    python3 tools/diagnose_vs_lrt_preview.py <our.tif> <lrt_preview.jpg> [output_dir]

Defaults: output_dir = ./diagnostic_output/

Input expectations:
    <our.tif>          16-bit linear Rec.2020 TIFF as emitted by lrt-cinema
                       cinema-linear preset.
    <lrt_preview.jpg>  sRGB JPEG from .lrt/visual/*.lrtpreview or
                       .lrt/previews/*.lrtpreview.

Dependencies: numpy, tifffile, Pillow, colour-science, scipy.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image

try:
    import colour
except ImportError:
    print("error: colour-science not installed (pip install --user colour-science)", file=sys.stderr)
    sys.exit(2)

try:
    from scipy.optimize import minimize
except ImportError:
    print("error: scipy not installed (pip install --user scipy)", file=sys.stderr)
    sys.exit(2)


# ITU-R BT.2020 §3.3 linear Rec.2020 → CIE XYZ (D65 white).
M_REC2020_TO_XYZ = np.array([
    [0.6369580, 0.1446169, 0.1688810],
    [0.2627002, 0.6779981, 0.0593017],
    [0.0000000, 0.0280727, 1.0609851],
])
D65 = colour.CCS_ILLUMINANTS["CIE 1931 2 Degree Standard Observer"]["D65"]


def _load_tif_16bit(path: Path, target_w: int, target_h: int) -> np.ndarray:
    """Read 16-bit uint16 RGB TIFF, downsample to target dims, return float64 0..1."""
    arr = tifffile.imread(str(path))
    if arr.dtype != np.uint16:
        raise SystemExit(
            f"expected 16-bit TIFF at {path}, got dtype={arr.dtype}. "
            "Render via lrt-cinema cinema-linear preset which writes 16-bit linear Rec.2020."
        )
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise SystemExit(f"expected 3-channel RGB TIFF at {path}, got shape={arr.shape}")
    # PIL can't fromarray uint16 RGB directly — resize per channel via I;16 mode.
    channels = []
    for c in range(3):
        ch = Image.fromarray(arr[:, :, c], mode="I;16")
        ch = ch.resize((target_w, target_h), Image.LANCZOS)
        channels.append(np.array(ch))
    return np.stack(channels, axis=2).astype(np.float64) / 65535.0


def _load_lrt_preview(path: Path) -> np.ndarray:
    """Read 8-bit sRGB JPEG, return uint8 array as-is."""
    return np.array(Image.open(path).convert("RGB"))


def _to_lab_d65_from_linear_rec2020(arr: np.ndarray) -> np.ndarray:
    """linear Rec.2020 (float 0..1) → CIE Lab(D65)."""
    xyz = arr @ M_REC2020_TO_XYZ.T
    return colour.XYZ_to_Lab(xyz, illuminant=D65)


def _to_lab_d65_from_srgb_uint8(arr: np.ndarray) -> np.ndarray:
    """sRGB-encoded uint8 → linear sRGB → CIE Lab(D65)."""
    norm = arr.astype(np.float64) / 255.0
    linear = colour.models.eotf_sRGB(norm)
    xyz = colour.RGB_to_XYZ(linear, "sRGB", apply_cctf_decoding=False)
    return colour.XYZ_to_Lab(xyz, illuminant=D65)


def _percentile_report(de: np.ndarray) -> str:
    de_flat = de.flatten()
    lines = ["=== ΔE2000 per-pixel distribution (over the full image) ==="]
    lines.append(f"  total pixels:  {de_flat.size:>9,}")
    lines.append("")
    lines.append("  Bucket           % pixels")
    for lo, hi, label in [
        (0, 1,  "ΔE < 1   (imperceptible)"),
        (1, 2,  "1 ≤ ΔE < 2 (perceptible side-by-side)"),
        (2, 3,  "2 ≤ ΔE < 3 (broadcast-acceptable)"),
        (3, 5,  "3 ≤ ΔE < 5 (visible, low-stakes OK)"),
        (5, 10, "5 ≤ ΔE <10 (clearly different)"),
        (10, 1e9, "ΔE ≥ 10  (drastic divergence)"),
    ]:
        pct = ((de_flat >= lo) & (de_flat < hi)).mean() * 100
        bar = "#" * int(pct / 2)
        lines.append(f"  {label:38s} {pct:5.1f}%  {bar}")
    lines.append("")
    lines.append("  Percentiles:")
    for p in [50, 75, 90, 95, 99, 99.9]:
        lines.append(f"    P{p:<5}: {np.percentile(de_flat, p):>7.2f}")
    lines.append(f"    max:   {de_flat.max():>7.2f}")
    return "\n".join(lines)


def _spatial_heatmap(de2d: np.ndarray, out_path: Path) -> None:
    """Render ΔE field as a heatmap (low ΔE = blue, high = red). Saturate at ΔE=30."""
    hm = np.clip(de2d, 0, 30) / 30 * 255
    heat = np.stack([
        np.clip(hm * 1.0, 0, 255),       # R rises
        np.clip(255 - hm * 0.8, 0, 255), # G falls
        np.clip(255 - hm, 0, 255),       # B falls faster
    ], axis=2).astype(np.uint8)
    Image.fromarray(heat).save(out_path, quality=92)


def _affine_fit(ours_linear_srgb: np.ndarray, target_linear_srgb: np.ndarray,
                n_samples: int = 50000, seed: int = 0) -> tuple[np.ndarray, float, float]:
    """Find per-channel (gain, offset) that minimizes mean ΔE2000.

    Returns (params [gR gG gB oR oG oB], pre_fit_mean_de, post_fit_mean_de).

    Critical interpretation: if post_fit << pre_fit, the gap is a single
    grading transform (per-channel gain/offset) — fixable by a one-shot
    color correction in the renderer or by the user in Resolve. If
    post_fit ≈ pre_fit, the gap is structural (different camera matrix,
    non-linear tone curve, hue rotation, etc.) and needs deeper work.
    """
    np.random.seed(seed)
    flat_ours = ours_linear_srgb.reshape(-1, 3)
    flat_tgt  = target_linear_srgb.reshape(-1, 3)
    n = min(n_samples, flat_ours.shape[0])
    idx = np.random.choice(flat_ours.shape[0], size=n, replace=False)
    ours_s = flat_ours[idx]
    tgt_s  = flat_tgt[idx]

    tgt_xyz = colour.RGB_to_XYZ(tgt_s, "sRGB", apply_cctf_decoding=False)
    tgt_lab = colour.XYZ_to_Lab(tgt_xyz, illuminant=D65)

    def obj(params):
        gain = params[:3]; offset = params[3:]
        tx = np.clip(ours_s * gain + offset, 0, 1)
        tx_xyz = colour.RGB_to_XYZ(tx, "sRGB", apply_cctf_decoding=False)
        tx_lab = colour.XYZ_to_Lab(tx_xyz, illuminant=D65)
        return float(np.mean(colour.delta_E(tx_lab, tgt_lab, method="CIE 2000")))

    x0 = np.array([1.0, 1.0, 1.0, 0.0, 0.0, 0.0])
    pre = obj(x0)
    result = minimize(
        obj, x0, method="Nelder-Mead",
        options={"maxiter": 500, "xatol": 1e-4, "fatol": 1e-3, "adaptive": True},
    )
    return result.x, pre, result.fun


def _affine_report(params: np.ndarray, pre: float, post: float) -> str:
    gR, gG, gB, oR, oG, oB = params
    lines = ["=== Affine-fit decomposition (best per-channel grade) ==="]
    lines.append(f"  gain   R / G / B = {gR:>6.3f} / {gG:>6.3f} / {gB:>6.3f}")
    lines.append(f"  offset R / G / B = {oR:>+7.4f} / {oG:>+7.4f} / {oB:>+7.4f}")
    lines.append("")
    lines.append(f"  Pre-fit  mean ΔE: {pre:>6.2f}")
    lines.append(f"  Post-fit mean ΔE: {post:>6.2f}")
    lines.append(f"  Reduction:        {pre - post:>+6.2f}  ({(1 - post/pre) * 100:.0f}%)")
    lines.append("")
    if post < 3.0:
        lines.append("  → Post-fit < 3.0: gap is BROADCAST-ACCEPTABLE after a single grade.")
        lines.append("    The divergence is one per-channel gain/offset away from target.")
    elif post < pre * 0.5:
        lines.append("  → Post-fit << Pre-fit: gap is MOSTLY a grading transform.")
        lines.append("    Per-channel grade closes most of the gap; small structural")
        lines.append("    residual likely from non-linear tone curve or HueSatMap.")
    else:
        lines.append("  → Post-fit ≈ Pre-fit: gap is STRUCTURAL.")
        lines.append("    No simple grade closes it — camera matrix, tone curve, or")
        lines.append("    DCP-style hue/saturation profile differs.")
    if abs(gR / gG - 1) > 0.05 or abs(gB / gG - 1) > 0.05:
        lines.append("")
        lines.append("  → Per-channel gain ratio differs > 5% — indicates white-balance")
        lines.append("    or camera-matrix divergence, not just exposure.")
    return "\n".join(lines)


def _per_channel_report(ours_lab: np.ndarray, tgt_lab: np.ndarray) -> str:
    lines = ["=== Per-channel L*a*b* percentile distribution ==="]
    lines.append(f"  {'channel':>7s}  {'ours P5/P50/P95':>22s}  {'target P5/P50/P95':>22s}  {'ΔP5/ΔP50/ΔP95':>20s}")
    for idx, name in [(0, "L*"), (1, "a*"), (2, "b*")]:
        op5, op50, op95 = (np.percentile(ours_lab[:, :, idx], q) for q in (5, 50, 95))
        tp5, tp50, tp95 = (np.percentile(tgt_lab[:, :, idx],  q) for q in (5, 50, 95))
        lines.append(
            f"  {name:>7s}  {op5:>+6.1f}/{op50:>+6.1f}/{op95:>+6.1f}  "
            f"{tp5:>+6.1f}/{tp50:>+6.1f}/{tp95:>+6.1f}  "
            f"{op5 - tp5:>+5.1f}/{op50 - tp50:>+5.1f}/{op95 - tp95:>+5.1f}"
        )
    lines.append("")
    lines.append("  Interpretation:")
    dp95_L = np.percentile(ours_lab[:, :, 0], 95) - np.percentile(tgt_lab[:, :, 0], 95)
    dp5_L  = np.percentile(ours_lab[:, :, 0], 5)  - np.percentile(tgt_lab[:, :, 0], 5)
    if abs(dp95_L) > 3 * abs(dp5_L) + 5:
        lines.append("    L* divergence concentrated in HIGHLIGHTS (P95 shift >> P5 shift).")
        lines.append("    Suggests target applies a tone curve that lifts (or compresses) highlights.")
    elif abs(dp5_L) > 3 * abs(dp95_L) + 5:
        lines.append("    L* divergence concentrated in SHADOWS (P5 shift >> P95 shift).")
        lines.append("    Suggests target applies a shadow-lift or black-point shift.")
    dp95_b = np.percentile(ours_lab[:, :, 2], 95) - np.percentile(tgt_lab[:, :, 2], 95)
    dp50_b = np.percentile(ours_lab[:, :, 2], 50) - np.percentile(tgt_lab[:, :, 2], 50)
    if abs(dp95_b) > 5 and abs(dp95_b - dp50_b) > 3:
        lines.append("    b* divergence varies between midtones and highlights — suggests")
        lines.append("    DCP-style HueSatMap or tone-aware warmth adjustment in target.")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) < 3 or len(argv) > 4:
        print(__doc__, file=sys.stderr)
        return 2
    our_tif = Path(argv[1])
    lrt_preview = Path(argv[2])
    outdir = Path(argv[3]) if len(argv) == 4 else Path("./diagnostic_output")
    if not our_tif.is_file():
        print(f"error: {our_tif} not found", file=sys.stderr); return 2
    if not lrt_preview.is_file():
        print(f"error: {lrt_preview} not found", file=sys.stderr); return 2
    outdir.mkdir(parents=True, exist_ok=True)

    preview_arr = _load_lrt_preview(lrt_preview)
    H, W = preview_arr.shape[:2]
    ours_arr = _load_tif_16bit(our_tif, W, H)

    ours_lab = _to_lab_d65_from_linear_rec2020(ours_arr)
    preview_lab = _to_lab_d65_from_srgb_uint8(preview_arr)
    de = colour.delta_E(ours_lab, preview_lab, method="CIE 2000")

    print(f"Comparing:")
    print(f"  ours    {our_tif} ({ours_arr.shape[0]}x{ours_arr.shape[1]}, 16-bit linear Rec.2020)")
    print(f"  target  {lrt_preview} ({preview_arr.shape[0]}x{preview_arr.shape[1]}, 8-bit sRGB)")
    print()
    print(_percentile_report(de))
    print()

    # Convert both into linear-sRGB-encoded for the affine fit
    ours_linear_srgb = colour.RGB_to_RGB(
        ours_arr, "ITU-R BT.2020", "sRGB",
        apply_cctf_decoding=False, apply_cctf_encoding=False,
    )
    preview_linear_srgb = colour.models.eotf_sRGB(preview_arr.astype(np.float64) / 255.0)
    params, pre, post = _affine_fit(ours_linear_srgb, preview_linear_srgb)
    print(_affine_report(params, pre, post))
    print()

    print(_per_channel_report(ours_lab, preview_lab))
    print()

    # Heatmap + side-by-side
    _spatial_heatmap(de, outdir / "de_heatmap.jpg")
    Image.fromarray(preview_arr).save(outdir / "target.jpg", quality=92)
    ours_srgb = (
        colour.models.eotf_inverse_sRGB(np.clip(ours_linear_srgb, 0, 1)) * 255
    ).astype(np.uint8)
    Image.fromarray(ours_srgb).save(outdir / "ours_srgb.jpg", quality=92)
    print(f"Outputs saved to {outdir}/:")
    print(f"  target.jpg     LRT preview reference")
    print(f"  ours_srgb.jpg  our render decoded to sRGB for visual comparison")
    print(f"  de_heatmap.jpg ΔE2000 heatmap (blue=match, red=divergence; saturated at ΔE=30)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
