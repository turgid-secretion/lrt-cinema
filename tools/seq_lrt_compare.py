"""Sequence comparison — current pipeline vs the LRT JPG render (north-star) AND
vs the old-pipeline TIF render (regression), across a whole 250-frame sequence,
with temporal trend analysis.

Per frame N it computes:
  * current-vs-LRT mean/P95 ΔE2000 (north-star gap) — current TIF center-cropped
    8 px to the LRT JPG (the validated alignment, NOT a resize; memory
    lrt-jpg-northstar-baseline);
  * affine per-channel gain (LRT ≈ gain·ours) — the brightness/cast drift;
  * current-vs-old mean ΔE2000 (same 4032×6032 grid, direct) — regression: did the
    overhaul change the production render? (expect ≈0 on the default path).
Then it reports temporal trends across N=1..250 (does the gap grow? does the gain
drift — the documented ~0.96→1.10 deflicker under-application? is the regression flat?).

Downsampled (block-mean) for 250-frame throughput; mean ΔE + gain are robust to it.
Run: python3 tools/seq_lrt_compare.py   (paths configured below; edit for another run)
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import colour
import imageio.v3 as iio
import numpy as np
import tifffile

warnings.filterwarnings("ignore")

_CUR = Path("/tmp/seq_current")
_LRT = Path("/Volumes/SanDisk Extreme Pro 55AF Media/Projects/lrt-export/"
            "LRT_2026_international_faire_timelapse")
_OLD = Path("/Volumes/SanDisk Extreme Pro 55AF Media/Projects/"
            "2026 international faire timelapse — lrt-cinema export/tif")
_N = 250
_DOWN = 6          # block-mean downsample factor (speed; means/gain robust to it)
_CROP = 8          # current(4032)→LRT(4016) center-crop border, per axis
_D65 = np.array([0.3127, 0.3290])


def _block_down(a: np.ndarray, k: int) -> np.ndarray:
    h, w = a.shape[:2]
    h2, w2 = (h // k) * k, (w // k) * k
    return a[:h2, :w2].reshape(h2 // k, k, w2 // k, k, -1).mean(axis=(1, 3))


def _srgb01_to_lab(arr01: np.ndarray) -> np.ndarray:
    lin = colour.models.eotf_sRGB(arr01)
    xyz = colour.RGB_to_XYZ(lin, "sRGB", apply_cctf_decoding=False)
    return colour.XYZ_to_Lab(xyz, illuminant=_D65)


def _gain(ours_lin: np.ndarray, tgt_lin: np.ndarray) -> list[float]:
    """Per-channel least-squares gain g s.t. tgt ≈ g·ours (no offset). g<1 ⇒ ours
    brighter than the target; g>1 ⇒ ours darker."""
    out = []
    for c in range(3):
        o = ours_lin[..., c].ravel()
        t = tgt_lin[..., c].ravel()
        out.append(float((o @ t) / (o @ o)))
    return out


def main() -> int:
    rows = []
    for n in range(1, _N + 1):
        cur_p = _CUR / f"LRT_{n:05d}.tif"
        lrt_p = _LRT / f"LRT_{n:05d}.jpg"
        old_p = _OLD / f"LRT_{n:05d}.tif"
        if not cur_p.exists():
            print(f"  missing current {cur_p.name} — stop at {n - 1}", file=sys.stderr)
            break
        cur = tifffile.imread(cur_p).astype(np.float32) / 65535.0      # 4032×6032
        lrt = iio.imread(lrt_p).astype(np.float32) / 255.0             # 4016×6016
        # current→LRT alignment: center-crop 8 px (NOT resize).
        cur_c = cur[_CROP:-_CROP, _CROP:-_CROP]                        # 4016×6016
        curd = _block_down(cur_c, _DOWN)
        lrtd = _block_down(lrt, _DOWN)
        cur_lab, lrt_lab = _srgb01_to_lab(curd), _srgb01_to_lab(lrtd)
        de_lrt = colour.delta_E(cur_lab, lrt_lab, method="CIE 2000")
        g = _gain(colour.models.eotf_sRGB(curd), colour.models.eotf_sRGB(lrtd))

        row = {
            "n": n,
            "de_lrt": float(de_lrt.mean()),
            "de_lrt_p95": float(np.percentile(de_lrt, 95)),
            "gR": g[0], "gG": g[1], "gB": g[2], "gmean": float(np.mean(g)),
            "bright_cur": float(colour.models.eotf_sRGB(curd).mean()),
            "bright_lrt": float(colour.models.eotf_sRGB(lrtd).mean()),
        }
        # current-vs-old regression (same 4032×6032 grid, direct).
        if old_p.exists():
            old = tifffile.imread(old_p).astype(np.float32) / 65535.0
            cd, od = _block_down(cur, _DOWN), _block_down(old, _DOWN)
            de_old = colour.delta_E(_srgb01_to_lab(cd), _srgb01_to_lab(od),
                                    method="CIE 2000")
            row["de_old"] = float(de_old.mean())
            row["de_old_max"] = float(de_old.max())
        rows.append(row)
        if n % 25 == 0 or n <= 3:
            o = row.get("de_old", float("nan"))
            print(f"  {n:3d}/250  de_LRT={row['de_lrt']:.2f} (P95 {row['de_lrt_p95']:.1f})"
                  f"  gain={row['gmean']:.3f}  de_old={o:.3f}")

    Path("/tmp/seq_analysis.json").write_text(json.dumps(rows))
    _report(rows)
    return 0


def _report(rows: list[dict]) -> None:
    n = np.array([r["n"] for r in rows])
    de_lrt = np.array([r["de_lrt"] for r in rows])
    gmean = np.array([r["gmean"] for r in rows])
    gR = np.array([r["gR"] for r in rows])
    gB = np.array([r["gB"] for r in rows])
    has_old = all("de_old" in r for r in rows)
    de_old = np.array([r.get("de_old", np.nan) for r in rows])

    print("\n" + "=" * 70)
    print(f"SEQUENCE ANALYSIS — {len(rows)} frames (current pipeline, default linear demosaic)")
    print("=" * 70)
    print("\n— current vs LRT JPG (north-star) —")
    print(f"  mean ΔE2000 over sequence : {de_lrt.mean():.2f}   (range {de_lrt.min():.2f}–{de_lrt.max():.2f})")
    print(f"  ΔE trend (frame 1 → 250)  : {de_lrt[:5].mean():.2f} → {de_lrt[-5:].mean():.2f}   "
          f"({'GROWS' if de_lrt[-5:].mean() > de_lrt[:5].mean() + 0.2 else 'flat'})")
    print(f"  affine gain (LRT≈g·ours)  : {gmean[:5].mean():.3f} → {gmean[-5:].mean():.3f}   "
          f"(<1 ours brighter, >1 ours darker; crosses 1.0: "
          f"{'YES — brightness drift' if gmean.min() < 1.0 < gmean.max() else 'no'})")
    print(f"  per-channel gain R vs B   : R {gR.mean():.3f}  B {gB.mean():.3f}  "
          f"(Δ {abs(gR.mean()-gB.mean()):.3f} = colour cast)")
    # correlation of ΔE with frame index (temporal trend strength)
    if len(n) > 2:
        cc = float(np.corrcoef(n, de_lrt)[0, 1])
        print(f"  ΔE↔frame correlation      : {cc:+.2f}   (temporal drift {'strong' if abs(cc)>0.6 else 'weak'})")
    if has_old:
        print("\n— current vs OLD pipeline (regression: did the overhaul change production?) —")
        print(f"  mean ΔE2000               : {de_old.mean():.4f}   (max frame {de_old.max():.4f})")
        print(f"  verdict                   : {'CLEAN (default path unchanged)' if de_old.max() < 0.05 else 'CHANGED — investigate'}")
    # outliers (worst north-star frames)
    worst = n[np.argsort(de_lrt)[-5:]][::-1]
    print(f"\n  worst north-star frames   : {list(worst)}  (ΔE {sorted(de_lrt)[-5:][::-1]})")
    print("\nfull per-frame data → /tmp/seq_analysis.json")


if __name__ == "__main__":
    raise SystemExit(main())
