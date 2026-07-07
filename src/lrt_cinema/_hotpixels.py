"""Clean-room hot-pixel suppression (darktable's `hotpixels` stage).

ALGORITHM (dt `src/iop/hotpixels.c`, Bayer variant `_process_bayer` —
read-to-learn during the 2026-07-07 port session; no GPL code copied,
anti-drift rule 6). The canon's mosaic-domain impulse filter, dt pipe
position `hotpixels@6.0` — BETWEEN `cacorrect@5.0` and `demosaic@8.0`
(TARGET slot 2.5; note the canon SPLITS on ordering: RT interpolates
bad/hot pixels in preprocess BEFORE its raw-CA step — we follow dt).

Per site (all CFA colours; interior only, 2-px border untouched):
  a pixel with value v > `threshold` is HOT when at least 4 (3 with
  `permissive`, which catches adjacent hot-pixel PAIRS) of its four
  same-channel cardinal neighbours (±2 px along row/col — the same-colour
  lattice for every Bayer site, including G, in dt's formulation) lie
  below v · `strength`/2; it is replaced by the MAXIMUM of those dark
  neighbours (dt's own rationale: max replacement does the least damage
  when a non-hot pixel is caught).

Defaults are dt's shipped parameter defaults (strength 0.25 → multiplier
0.125, threshold 0.05, permissive off). dt's `markfixed` debug overlay is
not ported. Values are consumed in the caller's units — pass `threshold`
in the same (balanced-mosaic) scale as the input.

Why this stage exists here (owner-driven, 2026-07-07): the segbased+CA
flips show isolated LUMA impulses ("random hot pixels") that survive
fc-suppress (a chroma median that never touches G); the CA-vs-recon
ORDER probe refuted reordering as the fix. This is the canon's dedicated
tool for that artifact class. Opt-in (`--hotpixels S`), owner-gated.

Validation: tests/test_hotpixels.py (impulse removal, pair handling with
permissive, threshold gates, border/neighbour invariance);
tools/ca_order_probe.py --hotpix arm (gym segb census + flips).
"""

from __future__ import annotations

import numpy as np


def fix_hot_pixels(cfa: np.ndarray, *, strength: float = 0.25,
                   threshold: float = 0.05,
                   permissive: bool = False) -> tuple[np.ndarray, int]:
    """Suppress isolated hot pixels on a Bayer mosaic. Returns
    (corrected copy, number of pixels fixed). See module docstring."""
    if cfa.ndim != 2:
        raise ValueError("fix_hot_pixels needs a 2-D Bayer mosaic")
    if not 0.0 <= strength <= 1.0:
        raise ValueError("strength must be in [0, 1]")
    cfa = np.ascontiguousarray(cfa, dtype=np.float32)
    out = cfa.copy()
    if strength == 0.0:
        return out, 0
    multiplier = np.float32(strength / 2.0)
    thresh = np.float32(threshold)
    min_neighbours = 3 if permissive else 4

    v = cfa[2:-2, 2:-2]
    mid = v * multiplier
    count = np.zeros(v.shape, dtype=np.uint8)
    maxin = np.zeros_like(v)
    for dy, dx in ((0, -2), (-2, 0), (0, 2), (2, 0)):
        other = cfa[2 + dy: cfa.shape[0] - 2 + dy,
                    2 + dx: cfa.shape[1] - 2 + dx]
        cond = mid > other
        count += cond
        maxin = np.where(cond & (other > maxin), other, maxin)
    fix = (v > thresh) & (count >= min_neighbours)
    out[2:-2, 2:-2] = np.where(fix, maxin, v)
    return out, int(fix.sum())
