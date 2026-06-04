"""Layer-F temporal-stability bench (docs/research/demosaic-test-fixtures.md §1
Axis-D4, §8 Layer F) — the timelapse-specific axis the still-image literature
cannot supply.

THE PROBLEM. A demosaic artifact (false colour, zipper) can be static within a
single frame yet FLICKER frame-to-frame as the scene drifts sub-pixel under the
Holy-Grail motion / exposure ramp. World-class-on-stills ≠ temporally stable: a
directional demosaicer's per-pixel H/V decision can flip as an edge crosses a
photosite, switching a colour-aliasing artifact on and off → visible shimmer in
the final video that no single-frame metric (CPSNR, S-CIELAB, MTF) sees.

THE METRIC (defined here; no external standard exists). Translate a chart by a
sequence of SUB-PIXEL offsets, demosaic each frame, and measure the TEMPORAL
standard deviation, across the sequence, of a quantity that is CONSTANT in the
ground truth after the motion — so any temporal variation is demosaic-induced:

  E_temporal(neutral) = mean over a should-be-NEUTRAL region of
                        std_t( chroma_t(x) )            [CIELAB chroma units]

Design choices that make this clean (advisor):
  * The chart is rendered ANALYTICALLY at shifted coordinates (the generator takes
    a continuous sub-pixel offset), NOT resampled — so there is no interpolation
    artifact of OUR making contaminating the measurement; the only spatial
    resampling in the loop is the demosaic under test.
  * The probed region is NEUTRAL in ground truth at EVERY offset (a grey zone-plate
    region is achromatic regardless of translation), so its true chroma is 0 in
    every frame and the metric is TRANSLATION-INVARIANT — no motion compensation,
    no warp alignment needed. Residual chroma std is pure demosaic false-colour
    flicker.

TIE TO PHASE C (`E_warp`). This is the still-content analogue of the pipeline-
overhaul Phase-C `E_warp` gate (temporal consistency under the Grail ramp): there
the constraint is "warp(frame_i) ≈ frame_{i+1} where the scene is static"; here we
hold the scene static-up-to-known-translation and charge the demosaic for any
temporal colour instability it injects. A demosaicer that wins on stills but has
high E_temporal would shimmer in the timelapse — exactly what Phase C must catch.

This module is TRACTABLE and implemented (not a stub): the analytic shifted chart
+ neutral-region chroma-std is cheap and needs no external data. Run:
  python3 tools/demosaic_bench/temporal.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as M  # noqa: E402

from lrt_cinema._rcd_demosaic import rcd_demosaic  # noqa: E402


def shifted_zone_plate(
    size: int = 256,
    k: float = 0.9,
    *,
    dx: float = 0.0,
    dy: float = 0.0,
    amplitude: float = 0.45,
    bias: float = 0.5,
) -> tuple[np.ndarray, np.ndarray]:
    """Neutral radial zone plate rendered ANALYTICALLY at a continuous sub-pixel
    offset (dx, dy) — the chart centre moves by (dx, dy), no resampling. Returns
    (linear-RGB ground truth, neutral_mask). The chart is grey at every offset, so
    the whole frame is achromatic ground truth (mask all-True): any chroma in the
    demosaiced output is false colour, and its frame-to-frame change is flicker."""
    h = w = size + (size % 2)
    yy, xx = np.indices((h, w), dtype=np.float64)
    r2 = (xx - w / 2.0 - dx) ** 2 + (yy - h / 2.0 - dy) ** 2
    pattern = bias + amplitude * np.cos(k * np.pi * r2 / size)
    rgb = np.clip(np.repeat(pattern[..., None], 3, axis=2), 0.0, 1.0)
    return rgb, np.ones((h, w), dtype=bool)


def temporal_chroma_std(
    demosaic_fn,
    *,
    size: int = 256,
    k: float = 1.1,
    n_frames: int = 16,
    border: int = 12,
) -> float:
    """E_temporal: mean (over a neutral region) temporal std of CIELAB chroma
    across a sub-pixel translation sequence (the metric defined in this module).

    The chart drifts diagonally by `1/n_frames`-pixel steps over one full pixel
    (covers every Bayer sub-phase). Lower = more temporally stable = less
    timelapse shimmer. `border` crops the frame edge (reflect-pad transients).
    """
    offsets = np.linspace(0.0, 1.0, n_frames, endpoint=False)
    chroma_frames = []
    for t in offsets:
        rgb, _ = shifted_zone_plate(size, k, dx=t, dy=t)
        out = demosaic_fn(M.mosaic_rggb(rgb))
        lab = M._xyz_to_lab(M.xyz_from_linear_rgb(np.clip(out, 0.0, 1.0)))
        chroma = np.hypot(lab[..., 1], lab[..., 2])
        chroma_frames.append(chroma)
    stack = np.stack(chroma_frames, axis=0)  # (T, H, W)
    if border:
        stack = stack[:, border:-border, border:-border]
    std_t = stack.std(axis=0)  # per-pixel temporal std
    return float(std_t.mean())


def _methods():
    try:
        from colour_demosaicing import (
            demosaicing_CFA_Bayer_Malvar2004 as malvar,
        )
        from colour_demosaicing import (
            demosaicing_CFA_Bayer_Menon2007 as menon,
        )
    except Exception:
        return None
    return [
        ("bilinear", lambda c: M.bilinear_rggb(c)),
        ("Malvar2004", lambda c: np.asarray(malvar(c, "RGGB"), dtype=np.float64)),
        ("our-RCD", lambda c: rcd_demosaic(c, "RGGB")),
        ("Menon2007", lambda c: np.asarray(menon(c, "RGGB"), dtype=np.float64)),
    ]


def main() -> int:
    methods = _methods()
    if methods is None:
        print("colour_demosaicing not installed — skipping temporal bench.")
        return 0
    print("\n" + "=" * 64)
    print("LAYER-F TEMPORAL STABILITY  (E_temporal — chroma flicker, ↓ better)")
    print("  neutral zone plate, 16 sub-pixel-shifted frames over 1 px")
    print("  metric = mean temporal std of CIELAB chroma in the neutral field")
    print("=" * 64)
    print(f"{'method':<12}{'E_temporal↓':>14}")
    print("-" * 26)
    results = {}
    for name, fn in methods:
        e = temporal_chroma_std(fn)
        results[name] = e
        print(f"{name:<12}{e:>14.4f}")
    print("-" * 26)
    if "bilinear" in results:
        worst = max(results, key=results.get)
        note = ("bilinear flickers most (expected — most false colour)"
                if worst == "bilinear" else
                f"NOTE: {worst} flickers most — directional decisions toggling under motion")
        print(note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
