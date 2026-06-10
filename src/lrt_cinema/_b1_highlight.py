"""B1 — on-mosaic (pre-demosaic) highlight reconstruction.

The slot documented in `pipeline._extract_cfa`/`_cfa_demosaic`:
    extract CFA → [B1] → demosaic
B1 operates on the linearised Bayer CFA (white→1.0, NO top clip) BEFORE the
demosaic, so the demosaic never interpolates *across* a clip boundary (clipped
G/B photosites mixed with an unclipped R) — which, under a cool develop WB, is
what produces the saturated cyan edge artifact at blown windows
(docs memory: vertical-cyan-rootcause). Our existing `highlight_recovery`
reconstruction is POST-demosaic — too late; the demosaic already made the cyan.

ENV-GATED, default off (byte-exact preserved). Methods:
  * "tier1"  — reuse `highlight_recovery.reconstruct_highlights` on a rough
               bilinear demosaic, write the reconstructed values back to the
               CLIPPED photosites only. Lifts clipped channels (never decreases);
               does NOT touch the unclipped channel — may not cure the cyan.
  * "neutral"— reconstruct each clipped photosite to the AS-SHOT-NEUTRAL ratio
               at the brightness carried by the SURVIVING channel(s). Allows the
               clipped channel to come DOWN to the neutral ratio (the cyan fix),
               while the surviving channel — untouched — carries the slat detail.

This module is a confirmation harness for the root-cause fix, not yet the
production wiring (that needs the gym ship-gate-frame audit — see DECISIONS).
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import binary_dilation, convolve, median_filter

from lrt_cinema.highlight_recovery import DEFAULT_CLIP_LEVEL, reconstruct_highlights


def chroma_diff_median(rgb: np.ndarray, passes: int = 1) -> np.ndarray:
    """Post-demosaic chroma-difference median (dcraw `-m` / darktable color-smoothing).

    Median-filters the colour differences R−G and B−G (3×3, `passes` iterations);
    G — the luma-carrying channel — is left UNTOUCHED. This removes demosaic
    directional colour error (false colour) at steep edges while preserving
    luminance detail (the blind slats are a luminance structure). Operates on
    linear camera RGB BEFORE white balance, so it is WB-agnostic — it smooths the
    small per-edge colour error before the cool WB can rotate it into saturated
    cyan. The universal suppression technique from the false-colour survey."""
    out = np.asarray(rgb, dtype=np.float32).copy()
    for _ in range(max(1, passes)):
        g = out[..., 1]
        out[..., 0] = median_filter(out[..., 0] - g, size=(3, 3)) + g
        out[..., 2] = median_filter(out[..., 2] - g, size=(3, 3)) + g
    return out

# Bayer bilinear interpolation kernel (R/B: 1/4 density; G: 1/2). Convolving
# (value·mask) and (mask) then dividing = density-correct bilinear at all pixels.
_BILIN = np.array([[1, 2, 1], [2, 4, 2], [1, 2, 1]], dtype=np.float32)


def _channel_offsets(pattern: str) -> dict[str, list[tuple[int, int]]]:
    """Bayer phase string (row-major 2×2, e.g. 'RGGB') → per-channel (row,col)
    offsets within the 2×2 tile."""
    offs: dict[str, list[tuple[int, int]]] = {"R": [], "G": [], "B": []}
    for k, ch in enumerate(pattern):
        offs[ch].append((k // 2, k % 2))
    return offs


def _rough_planes(cfa: np.ndarray, offs: dict) -> np.ndarray:
    """Rough full-res (H,W,3) bilinear demosaic — for clip detection + the
    reconstruction brightness/ratio estimate ONLY (not the delivery demosaic)."""
    h, w = cfa.shape
    planes = []
    for ch in "RGB":
        val = np.zeros((h, w), np.float32)
        msk = np.zeros((h, w), np.float32)
        for (i, j) in offs[ch]:
            val[i::2, j::2] = cfa[i::2, j::2]
            msk[i::2, j::2] = 1.0
        num = convolve(val, _BILIN, mode="reflect")
        den = convolve(msk, _BILIN, mode="reflect")
        planes.append(np.where(den > 0, num / den, 0.0))
    return np.stack(planes, axis=-1).astype(np.float32)


def b1_reconstruct(
    cfa: np.ndarray,
    pattern: str,
    as_shot_neutral: np.ndarray,
    method: str = "neutral",
    *,
    clip_level: float = DEFAULT_CLIP_LEVEL,
    dilate: int = 2,
) -> np.ndarray:
    """On-mosaic highlight reconstruction. `cfa` is the normalised Bayer (white→1,
    floor 0, no top clip). Returns a new CFA with clipped photosites reconstructed;
    unclipped photosites byte-identical. Strict no-op if nothing clips."""
    clip_cfa = cfa >= clip_level
    if not clip_cfa.any():
        return cfa
    offs = _channel_offsets(pattern)
    rough = _rough_planes(cfa, offs)
    asn = np.asarray(as_shot_neutral, dtype=np.float32).reshape(3)
    out = cfa.copy()

    if method == "tier1":
        recon = reconstruct_highlights(rough, asn, clip_level=clip_level)
        for ci, ch in enumerate("RGB"):
            for (i, j) in offs[ch]:
                sub = cfa[i::2, j::2]
                out[i::2, j::2] = np.where(sub >= clip_level, recon[i::2, j::2, ci], sub)
        return out

    if method == "neutral":
        clip = rough >= clip_level                 # (H,W,3) per-channel (demosaic-spread)
        valid = ~clip
        # AS-SHOT-NEUTRAL reconstruction at the BRIGHTEST-channel luminance: a blown
        # highlight is bright, and the brightest channel (a clipped one, pinned at the
        # clip) carries the true high level — anchoring on the dim *surviving* channel
        # reconstructs too dark and leaves the cyan. s = max_c(rough_c / asn_c) sets the
        # neutral level from whichever channel implies the most light; clipped channels
        # are then LIFTED to their true over-white value (headroom preserved, no clip).
        del valid
        s = np.max(rough / asn[None, None, :], axis=-1)
        target = s[..., None] * asn[None, None, :]  # coherent ASN-neutral field (H,W,3)
        # Reconstruct the WHOLE dilated blown region coherently (writing only the
        # clipped photosites leaves a clipped/unclipped checkerboard → demosaic rings).
        blown = binary_dilation(clip.any(axis=-1), iterations=dilate)
        for ci, ch in enumerate("RGB"):
            for (i, j) in offs[ch]:
                reg = blown[i::2, j::2]
                out[i::2, j::2] = np.where(reg, target[i::2, j::2, ci], cfa[i::2, j::2])
        return out

    raise ValueError(f"unknown B1 method {method!r}")
