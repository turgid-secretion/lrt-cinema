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
    for _ in range(passes):
        g = out[..., 1]
        for c in (0, 2):
            diff = median_filter(out[..., c] - g, size=3, mode="nearest")
            if blur:
                diff = uniform_filter(diff, size=3, mode="nearest")
            out[..., c] = np.maximum(diff + g, 0.0)
    return out
