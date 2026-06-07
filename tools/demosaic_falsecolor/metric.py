"""Chroma-high-frequency metric + alignment for the venetian-blind false-colour test.

THE ARTIFACT
------------
lrt-cinema's `rcd` demosaic shows blue/cyan false-colour "sawtooth" streaks on a
fine horizontal venetian blind at a bright window in DSC_4053 (the indoor faire
hall). It is demosaic luma<->chroma aliasing on a near-Nyquist horizontal grating
(the slats undersample R/B vertically; the false colour alternates with Bayer
phase) — NOT highlight clipping (independently established: 79% of the false-colour
pixels are unclipped). See docs/research/demosaic-false-color-test.md.

THE METRIC (exactly as the owner specified; do not "improve" it)
----------------------------------------------------------------
`chroma_hf` measures *horizontal* chroma variation in CIELab over the blinds ROI:
decode sRGB -> linear -> XYZ -> Lab, chroma = hypot(a*, b*), then the mean abs
difference between chroma and its 1x5 horizontal box-mean. The streaks ARE the
horizontal chroma variation, so this lights up exactly on the artifact.

`chroma_hf_v` is the VERTICAL companion (1x5 -> 5x1). It exists to catch gaming: a
horizontal-only chroma smoother would crush `chroma_hf` without fixing the artifact
(and would leave `chroma_hf_v` untouched). A *real* demosaic win drops BOTH. Always
report both.

ALIGNMENT
---------
The ACR/LRT references are 4016x6016; ours is 4032x6032. The prompt says they align
at dy=dx=-8, but `align_offset` CONFIRMS it by luma cross-correlation over a search
window — a 1px error on a near-Nyquist grating corrupts every number. Use the
measured offset, not the assumed one.

ENCODE RULE #1 (a prior repro burned here): NEVER hand-roll a ProPhoto->sRGB encode.
This module only ever READS already-encoded production sRGB TIFFs (ours via the real
output.py CLI; ACR's own export). It never encodes. A naive per-channel
clip(linear_srgb,0,1) creates a false yellow cast on out-of-gamut highlights and
corrupts the chroma measurement.
"""

from __future__ import annotations

import numpy as np
import tifffile
from scipy.ndimage import uniform_filter

# The worst-spot ROI in OUR render coords (4032x6032): the upper-left clerestory
# windows, fine horizontal blinds over a bright window. The owner's spec.
BLINDS = dict(y0=1350, y1=1660, x0=150, x1=1400)


def _srgb_eotf(x: np.ndarray) -> np.ndarray:
    """sRGB encoded -> linear (IEC 61966-2-1)."""
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def _srgb01(tiff_path: str) -> np.ndarray:
    """Read a uint16/uint8 sRGB TIFF -> float [0,1] (NO re-encode; rule #1)."""
    a = tifffile.imread(tiff_path)
    if a.dtype == np.uint16:
        return a.astype(np.float64) / 65535.0
    if a.dtype == np.uint8:
        return a.astype(np.float64) / 255.0
    return a.astype(np.float64)


def _luma(srgb01: np.ndarray) -> np.ndarray:
    """Rec.709 luma of a linearised sRGB image (for alignment cross-correlation)."""
    lin = _srgb_eotf(srgb01)
    return 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]


def chroma_hf(
    srgb01: np.ndarray,
    y0: int = BLINDS["y0"], y1: int = BLINDS["y1"],
    x0: int = BLINDS["x0"], x1: int = BLINDS["x1"],
    off: int = 0,
    axis: str = "h",
) -> float:
    """Mean abs horizontal (axis='h') OR vertical (axis='v') chroma variation in the
    ROI. `off` shifts the ROI for an aligned reference (off=-8 for ACR/LRT)."""
    import colour

    reg = srgb01[y0 + off:y1 + off, x0 + off:x1 + off]
    lin = _srgb_eotf(reg)
    xyz = colour.RGB_to_XYZ(lin, "sRGB", apply_cctf_decoding=False)
    L = colour.XYZ_to_Lab(xyz, np.array([0.3127, 0.3290]))
    ch = np.hypot(L[..., 1], L[..., 2])
    box = (1, 5) if axis == "h" else (5, 1)
    return float(np.abs(ch - uniform_filter(ch, box)).mean())


def chroma_hf_path(tiff_path: str, off: int = 0, axis: str = "h", **roi) -> float:
    return chroma_hf(_srgb01(tiff_path), off=off, axis=axis, **roi)


def align_offset(
    ref_srgb01: np.ndarray, our_srgb01: np.ndarray,
    y0: int = BLINDS["y0"], y1: int = BLINDS["y1"],
    x0: int = BLINDS["x0"], x1: int = BLINDS["x1"],
    search: int = 12,
) -> tuple[int, int, float]:
    """Find the integer (dy, dx) that best aligns `ref` to `our` over the ROI, by
    maximising normalised luma cross-correlation. Returns (dy, dx, peak_ncc).

    `our` is the larger (4032x6032) image indexed at the ROI; `ref` is the smaller
    (4016x6016). A positive returned dy means ref[y+dy] aligns to our[y]; the prompt
    claims (dy,dx)=(-8,-8). We brute-force a +/-search window and report the peak."""
    our_L = _luma(our_srgb01)
    ref_L = _luma(ref_srgb01)
    base = our_L[y0:y1, x0:x1]
    base = base - base.mean()
    best = (0, 0, -2.0)
    for dy in range(-search, search + 1):
        for dx in range(-search, search + 1):
            ys, xs = y0 + dy, x0 + dx
            cand = ref_L[ys:y1 + dy, xs:x1 + dx]
            if cand.shape != base.shape:
                continue
            cand = cand - cand.mean()
            denom = np.sqrt((base * base).sum() * (cand * cand).sum())
            if denom <= 0:
                continue
            ncc = float((base * cand).sum() / denom)
            if ncc > best[2]:
                best = (dy, dx, ncc)
    return best
