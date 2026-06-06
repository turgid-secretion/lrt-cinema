"""TIFF-trunk vs LRT-JPG comparison — LINEAR + tone-aware.

Supersedes the *scalar-gain* part of `seq_lrt_compare.py`, which fits a single
no-offset gain `LRT≈g·ours` that **conflates exposure with tone-shape** (see
`docs/research/deflicker-rootcause-audit.md` — that's how a fixed tone-curve
difference got mis-read as a fake per-frame deflicker factor). Here, per frame, in
**linear** (sRGB EOTF):

  * **ΔE2000** (CIE 2000, Lab) mean / P95 — the north-star headline (domain-correct).
  * **tone transfer summary** — `LRT_lin / ours_lin` at shadow / mid / highlight
    luminance bins. This is the transfer *curve*, not a scalar: the **mid** ratio is
    the exposure level; **shadow-vs-highlight spread** is the tone-curve-shape tilt
    (the real PV2012 gap). Within-frame, one scene → no cross-frame confound.
  * **colour cast** — per-channel R/G/B mid-luminance ratio (hue).
  * **sharpness** — high-frequency energy ratio `ours / LRT` on a FULL-RES centre
    crop (downsampling would hide it). >1 ⇒ ours sharper; this is whether the D2
    `--capture-sharpen acr` pass closed the LRT edge gap.

Then temporal trends across the set. Usage:
    python3 tools/seq_lrt_compare_linear.py [OURS_DIR] [LRT_DIR] [N] [FROM]
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import colour
import imageio.v3 as iio
import numpy as np
import tifffile
from scipy.ndimage import gaussian_filter

warnings.filterwarnings("ignore")

_OURS = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    "/Volumes/SanDisk Extreme Pro 55AF Media/Projects/lrt-cinema-testrun/tiff_faithful")
_LRT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(
    "/Volumes/SanDisk Extreme Pro 55AF Media/Projects/lrt-export/"
    "LRT_2026_international_faire_timelapse")
_N = int(sys.argv[3]) if len(sys.argv) > 3 else 250
_FROM = int(sys.argv[4]) if len(sys.argv) > 4 else 1
_DOWN = 6           # ΔE / tone: block-mean (robust to it)
_CROP = 8           # ours(4032)→LRT(4016) centre-crop border per axis
_HFCROP = 768       # sharpness: full-res centre crop side (NO downsample)
_D65 = np.array([0.3127, 0.3290])
_W709 = np.array([0.2126, 0.7152, 0.0722])
_BINS = {"shadow": (0.02, 0.09), "mid": (0.12, 0.26), "highlight": (0.38, 0.60)}


def _eotf(x):
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def _block_down(a, k):
    h, w = a.shape[:2]
    h2, w2 = (h // k) * k, (w // k) * k
    return a[:h2, :w2].reshape(h2 // k, k, w2 // k, k, -1).mean(axis=(1, 3))


def _lab(arr01):
    xyz = colour.RGB_to_XYZ(_eotf(arr01), "sRGB", apply_cctf_decoding=False)
    return colour.XYZ_to_Lab(xyz, illuminant=_D65)


def _tone_ratios(ours_lin_lum, lrt_lin_lum):
    """LRT/ours linear-luminance ratio in shadow/mid/highlight ours-luminance bins."""
    out = {}
    for name, (lo, hi) in _BINS.items():
        m = (ours_lin_lum >= lo) & (ours_lin_lum < hi)
        out[name] = float(lrt_lin_lum[m].mean() / ours_lin_lum[m].mean()) if m.sum() > 200 else float("nan")
    return out


def _centre_crop(a, side):
    h, w = a.shape[:2]
    return a[(h - side) // 2:(h + side) // 2, (w - side) // 2:(w + side) // 2]


def _hf_energy(rgb01):
    lum = _eotf(rgb01) @ _W709
    return float(np.var(lum - gaussian_filter(lum, 1.5)))


def main() -> int:
    rows = []
    for n in range(_FROM, _FROM + _N):
        op = _OURS / f"LRT_{n:05d}.tif"
        lp = _LRT / f"LRT_{n:05d}.jpg"
        if not op.exists() or not lp.exists():
            print(f"  stop at {n - 1} (missing {op.name if not op.exists() else lp.name})",
                  file=sys.stderr)
            break
        ours = tifffile.imread(op).astype(np.float32) / 65535.0
        lrt = iio.imread(lp).astype(np.float32) / 255.0
        ours_c = ours[_CROP:-_CROP, _CROP:-_CROP]
        od, ld = _block_down(ours_c, _DOWN), _block_down(lrt, _DOWN)
        de = colour.delta_E(_lab(od), _lab(ld), method="CIE 2000")
        tone = _tone_ratios(_eotf(od) @ _W709, _eotf(ld) @ _W709)
        ol, ll = _eotf(od), _eotf(ld)
        mid = (od @ _W709 >= 0.12) & (od @ _W709 < 0.26)
        cast = [float(ll[..., c][mid].mean() / ol[..., c][mid].mean()) for c in range(3)]
        hf_ours = _hf_energy(_centre_crop(ours_c, _HFCROP))
        hf_lrt = _hf_energy(_centre_crop(lrt, _HFCROP))
        rows.append({"n": n, "de": float(de.mean()), "de95": float(np.percentile(de, 95)),
                     **tone, "castR": cast[0], "castB": cast[2],
                     "hf_ratio": hf_ours / hf_lrt if hf_lrt > 0 else float("nan")})
        if n % 25 == 0 or n < _FROM + 3:
            r = rows[-1]
            print(f"  {n:3d}  ΔE {r['de']:.2f} (P95 {r['de95']:.1f})  tone S/M/H "
                  f"{r['shadow']:.2f}/{r['mid']:.2f}/{r['highlight']:.2f}  "
                  f"sharp ours/LRT {r['hf_ratio']:.2f}")

    if not rows:
        print("no frames compared", file=sys.stderr)
        return 1

    def col(k):
        return np.array([r[k] for r in rows], float)

    de, hf = col("de"), col("hf_ratio")
    print("\n" + "=" * 72)
    print(f"TIFF trunk vs LRT JPG — {len(rows)} frames, LINEAR + tone-aware")
    print("=" * 72)
    print(f"ΔE2000        : mean {de.mean():.2f}  P50 {np.median(de):.2f}  "
          f"P95 {np.percentile(de, 95):.2f}  (trend {de[:10].mean():.2f}→{de[-10:].mean():.2f})")
    print("  vs prior baseline (linear demosaic, NO sharpening): findings mean ΔE 1.20")
    print(f"tone ratio    : shadow {col('shadow').mean():.3f}  mid {col('mid').mean():.3f}  "
          f"highlight {col('highlight').mean():.3f}")
    print("  (mid = exposure level; shadow≠highlight = tone-curve-SHAPE tilt = the §11 gap)")
    print(f"  tilt (H−S)  : {(col('highlight') - col('shadow')).mean():+.3f}   "
          f"(LRT relative to ours: {'darker shadows / brighter highlights' if (col('highlight')-col('shadow')).mean()>0 else 'lifted shadows / pulled highlights'})")
    print(f"colour cast   : R {col('castR').mean():.3f}  B {col('castB').mean():.3f}  "
          f"(Δ {abs(col('castR').mean()-col('castB').mean()):.3f}; ~1.0 = neutral)")
    print(f"sharpness     : HF ours/LRT mean {hf.mean():.2f}  "
          f"({'ours SHARPER' if hf.mean() > 1.05 else 'LRT sharper' if hf.mean() < 0.95 else '~matched'}; "
          f"capture-sharpen acr vs the 8-bit JPEG)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
