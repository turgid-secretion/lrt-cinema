"""False-colour suppression — TARGET slot 6 (after demosaic, before the
colour transform).

THE CONVERGENT CANON SCHEME (source pass 2026-06-11 — CLAIMS "Local source
pass COMPLETE"; docs/REFERENCE_PIPELINE.md slot 6): three engines suppress
demosaic false colour the same way — keep the luma-bearing signal, median
the CHROMA-DIFFERENCE signal, iterate:

  - darktable `color_smoothing` (demosaicing/basics.c): per pass, for
    c ∈ {R, B}: 3×3 9-median of (c − G), then c ← max(0, median + G);
    1–5 passes.
  - dcraw / libraw `median_filter` (`-m`, postprocessing_aux.cpp): the
    identical operation (R−G, B−G, 3×3, `med_passes`).
  - RawTherapee `processFalseColorCorrection`: same idea in YIQ (median +
    box-blur the I/Q chroma planes, Y untouched).

WHY THE DIFFERENCE DOMAIN: G carries most luminance; medianing (c − G)
removes chroma outliers (invented colour at edges/zone-plates/grain) while
edges shared by both channels cancel in the difference and survive intact.
Our earlier 3×3 "chroma-median" probe medianed the wrong representation and
was measurably insufficient (fringe forensics) — this is the canon's actual
scheme, clean-room (read-to-learn; no GPL code copied — rule 6).

DOMAIN CONTRACT: balanced camera RGB (slot-3), post-demosaic, scene-linear.
Headroom-safe: values >1 pass through the median like any other value.

GATING: off by default (owner eyeballs before any default flips). The CLI
exposes `--fc-suppress N` (0 = off); preset wiring proposal in the slot-6
ledger entry.
"""

from __future__ import annotations

import numpy as np

# The canon's pass range (dt enumerates 1–5; dcraw takes any count, typical
# use 1–3). Guard rail only — callers pass an explicit count.
MAX_PASSES = 5

try:
    from numba import njit, prange
    _NUMBA = True
except Exception:  # pragma: no cover - exercised only without numba
    _NUMBA = False


if _NUMBA:
    @njit(cache=True, parallel=True)
    def _median3x3_nearest(src, dst, h, w):
        """3×3 median with edge-replicate (scipy mode='nearest') — BIT-EXACT
        vs scipy.ndimage.median_filter(size=3): a median is value selection,
        no arithmetic, so any correct algorithm returns identical bytes.
        The scipy call was 8.6 s/frame at 24 MP (single-threaded rank
        filter) — the top production-profile cost after the AMaZE twin."""
        for y in prange(h):
            win = np.empty(9, np.float32)
            ym = y - 1 if y > 0 else 0
            yp_ = y + 1 if y < h - 1 else h - 1
            for x in range(w):
                xm = x - 1 if x > 0 else 0
                xp = x + 1 if x < w - 1 else w - 1
                win[0] = src[ym, xm]
                win[1] = src[ym, x]
                win[2] = src[ym, xp]
                win[3] = src[y, xm]
                win[4] = src[y, x]
                win[5] = src[y, xp]
                win[6] = src[yp_, xm]
                win[7] = src[yp_, x]
                win[8] = src[yp_, xp]
                # insertion sort, take the middle — value selection only,
                # so bytes match any correct median implementation
                for i in range(1, 9):
                    v = win[i]
                    j = i - 1
                    while j >= 0 and win[j] > v:
                        win[j + 1] = win[j]
                        j -= 1
                    win[j + 1] = v
                dst[y, x] = win[4]


def suppress_false_colour(rgb: np.ndarray, passes: int = 2,
                          blur: bool = False) -> np.ndarray:
    """Chroma-difference median suppression (dt/dcraw class), `passes`×.

    `rgb` (H, W, 3) float32 balanced linear camera RGB. Returns a new array
    (input untouched); `passes <= 0` returns the input object unchanged —
    strict no-op for the off-by-default path.

    Per pass, for c ∈ {R, B}: c ← max(0, median3×3(c − G) + G); G is never
    modified (it carries the luminance/detail the scheme preserves).

    `blur=True` adds RawTherapee's refinement: a 3×3 box blur of the chroma
    difference AFTER the median, per pass (RT's `processFalseColorCorrection`
    medians then box-blurs its YIQ I/Q chroma planes). The blur cancels
    ALTERNATING-PHASE invented chroma (dense-frequency moiré — the zoneplate
    class) that a median is structurally blind to: opposite-sign errors
    average toward zero but each 3×3 window's median just picks one of them.
    Measured on the slot-6 sweep: the pure median is inert on zoneplate.
    """
    if passes <= 0:
        return rgb
    if passes > MAX_PASSES:
        raise ValueError(f"passes must be ≤ {MAX_PASSES}, got {passes}")
    from scipy.ndimage import median_filter, uniform_filter

    out = rgb.astype(np.float32, copy=True)
    h, w = out.shape[:2]
    med_dst = np.empty((h, w), np.float32) if _NUMBA else None
    for _ in range(passes):
        g = out[..., 1]
        for c in (0, 2):
            if _NUMBA:
                _median3x3_nearest(
                    np.ascontiguousarray(out[..., c] - g), med_dst, h, w)
                diff = med_dst
            else:
                diff = median_filter(out[..., c] - g, size=3, mode="nearest")
            if blur:
                diff = uniform_filter(diff, size=3, mode="nearest")
            out[..., c] = np.maximum(diff + g, 0.0)
    return out
