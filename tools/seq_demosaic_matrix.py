"""Demosaic A/B across the 250-frame sequence — linear vs rcd vs menon, each vs the
LRT JPG (north-star) AND vs each other AND vs the old pipeline.

The demosaic's effect is concentrated at EDGES (smooth regions are tone-dominated and
demosaic-blind), so per variant-vs-LRT it splits ΔE2000 into edge vs smooth regions
(edge = top-30% luma-gradient pixels of the LRT frame — the memory's 30% edge split).
Cross-variant means show how far apart the demosaics actually are.

Variants (our output 4032×6032; LRT JPG 4016×6016 → our frames center-cropped 8 px):
  linear  /tmp/seq_current   rcd  /tmp/seq_rcd   menon  /tmp/seq_menon
  old     external tif        LRT  external jpg

Downsample factor 3 (preserves edges far better than the ÷6 tone run) for 250-frame
throughput. Run: python3 tools/seq_demosaic_matrix.py
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import colour
import imageio.v3 as iio
import numpy as np
import tifffile

warnings.filterwarnings("ignore")

_LRT = Path("/Volumes/SanDisk Extreme Pro 55AF Media/Projects/lrt-export/"
            "LRT_2026_international_faire_timelapse")
_OLD = Path("/Volumes/SanDisk Extreme Pro 55AF Media/Projects/"
            "2026 international faire timelapse — lrt-cinema export/tif")
_VARIANTS = {"linear": Path("/tmp/seq_current"), "rcd": Path("/tmp/seq_rcd"),
             "menon": Path("/tmp/seq_menon")}
_N = 250
_DOWN = 3
_CROP = 8
_D65 = np.array([0.3127, 0.3290])


def _down(a: np.ndarray, k: int) -> np.ndarray:
    h, w = a.shape[:2]
    h2, w2 = (h // k) * k, (w // k) * k
    return a[:h2, :w2].reshape(h2 // k, k, w2 // k, k, -1).mean(axis=(1, 3))


def _lab(a01: np.ndarray) -> np.ndarray:
    lin = colour.models.eotf_sRGB(a01)
    xyz = colour.RGB_to_XYZ(lin, "sRGB", apply_cctf_decoding=False)
    return colour.XYZ_to_Lab(xyz, illuminant=_D65)


def _mde(a_lab: np.ndarray, b_lab: np.ndarray) -> float:
    """Mean ΔE2000 between two Lab images."""
    return float(colour.delta_E(a_lab, b_lab, method="CIE 2000").mean())


def _edge_mask(lab_lrt: np.ndarray) -> np.ndarray:
    """Top-30% luma-gradient-magnitude pixels of the LRT frame = the edge region."""
    lum = lab_lrt[..., 0]
    gy, gx = np.gradient(lum)
    g = np.hypot(gy, gx)
    return g >= np.percentile(g, 70)


def main() -> int:
    rows = []
    for n in range(1, _N + 1):
        paths = {k: v / f"LRT_{n:05d}.tif" for k, v in _VARIANTS.items()}
        # Skip frames where any variant is missing OR truncated (<100 MB = a
        # disk-full write failure). The demosaic A/B is frame-invariant, so the
        # good-frame intersection is a valid sample; don't stop, collect all good.
        if not all(p.exists() and p.stat().st_size > 100_000_000 for p in paths.values()):
            continue
        # our variants: 4032 → crop 8 → 4016, downsample. Same grid as LRT.
        var01 = {}
        for k, p in paths.items():
            a = tifffile.imread(p).astype(np.float32) / 65535.0
            var01[k] = _down(a[_CROP:-_CROP, _CROP:-_CROP], _DOWN)
        lrt01 = _down(iio.imread(_LRT / f"LRT_{n:05d}.jpg").astype(np.float32) / 255.0, _DOWN)
        lrt_lab = _lab(lrt01)
        emask = _edge_mask(lrt_lab)
        smask = ~emask
        row = {"n": n}
        # each variant vs LRT: overall / edge / smooth ΔE
        var_lab = {k: _lab(v) for k, v in var01.items()}
        for k, vl in var_lab.items():
            de = colour.delta_E(vl, lrt_lab, method="CIE 2000")
            row[f"{k}_lrt"] = float(de.mean())
            row[f"{k}_lrt_edge"] = float(de[emask].mean())
            row[f"{k}_lrt_smooth"] = float(de[smask].mean())
        # cross-variant (all-region mean): how far apart are the demosaics
        row["rcd_vs_linear"] = _mde(var_lab["rcd"], var_lab["linear"])
        row["menon_vs_linear"] = _mde(var_lab["menon"], var_lab["linear"])
        row["menon_vs_rcd"] = _mde(var_lab["menon"], var_lab["rcd"])
        # vs old (linear-grid, 4032; reuse the downsampled full frame)
        oldp = _OLD / f"LRT_{n:05d}.tif"
        if oldp.exists():
            old01 = _down(tifffile.imread(oldp).astype(np.float32) / 65535.0, _DOWN)
            old_lab = _lab(old01)
            for k in _VARIANTS:
                full = _down(tifffile.imread(paths[k]).astype(np.float32) / 65535.0, _DOWN)
                row[f"{k}_vs_old"] = float(
                    colour.delta_E(_lab(full), old_lab, method="CIE 2000").mean())
        rows.append(row)
        if n % 25 == 0 or n <= 2:
            print(f"  {n:3d}/250  vs LRT[all/edge]: lin {row['linear_lrt']:.2f}/{row['linear_lrt_edge']:.2f}"
                  f"  rcd {row['rcd_lrt']:.2f}/{row['rcd_lrt_edge']:.2f}"
                  f"  menon {row['menon_lrt']:.2f}/{row['menon_lrt_edge']:.2f}"
                  f"  | menon-vs-rcd {row['menon_vs_rcd']:.2f}", flush=True)

    Path("/tmp/seq_matrix.json").write_text(json.dumps(rows))
    _report(rows)
    return 0


def _report(rows: list[dict]) -> None:
    def col(k):
        return np.array([r[k] for r in rows if k in r])
    print("\n" + "=" * 72)
    print(f"DEMOSAIC A/B — {len(rows)} frames, sequence means")
    print("=" * 72)
    print("\n— each demosaic vs LRT JPG (north-star); ΔE2000 mean [edge / smooth] —")
    for k in ("linear", "rcd", "menon"):
        a, e, s = col(f"{k}_lrt"), col(f"{k}_lrt_edge"), col(f"{k}_lrt_smooth")
        print(f"  {k:7s}: all {a.mean():.3f}   edge {e.mean():.3f}   smooth {s.mean():.3f}")
    le, re, me = col("linear_lrt_edge").mean(), col("rcd_lrt_edge").mean(), col("menon_lrt_edge").mean()
    print(f"\n  EDGE-ΔE improvement vs linear:  rcd {le-re:+.3f}   menon {le-me:+.3f}   "
          f"(demosaic's payoff is here; smooth is ~flat = tone-bound)")
    print("\n— cross-variant ΔE2000 (how far apart the demosaics are) —")
    print(f"  rcd↔linear  {col('rcd_vs_linear').mean():.3f}   "
          f"menon↔linear {col('menon_vs_linear').mean():.3f}   "
          f"menon↔rcd {col('menon_vs_rcd').mean():.3f}")
    if any("linear_vs_old" in r for r in rows):
        print("\n— vs OLD pipeline (regression; old used linear + predates tint fix #45) —")
        for k in ("linear", "rcd", "menon"):
            v = col(f"{k}_vs_old")
            if len(v):
                print(f"  {k:7s} vs old: {v.mean():.3f}")
    # which demosaic is closest to LRT, per frame
    best = []
    for r in rows:
        cands = {k: r[f"{k}_lrt"] for k in ("linear", "rcd", "menon")}
        best.append(min(cands, key=cands.get))
    from collections import Counter
    print(f"\n  closest-to-LRT per frame: {dict(Counter(best))}")
    print("\nfull per-frame data → /tmp/seq_matrix.json")


if __name__ == "__main__":
    raise SystemExit(main())
