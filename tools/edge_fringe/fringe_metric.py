"""Deterministic edge-fringing metric + ablation harness for the lrt-cinema render.

The artifact under investigation: high-saturation blue/yellow colour fringing /
banding at high-contrast edges, worst at highlight-to-clipping interfaces (clipped
light fixtures, window-blind gaps, a sawtooth across the window tops). Present in
both the sRGB TIFF and the ACEScg EXR — it is in the DATA, not the display.

This module is the **committed, falsifiable tool** the investigation is built on:

  * `render_variant`    — render DSC_4053 (NEF-direct via rawpy) through a chosen
                          pipeline variant to a comparable output space (linear
                          ProPhoto at a tap, then the gym sRGB encoder).
  * `fringe_metrics`    — two metrics on an sRGB-uint8 image:
        - `chroma_at_edge`  : owner's metric — mean Lab chroma hypot(a*,b*) over
          pixels that are BOTH a strong luminance gradient (|∇L*|>p97) AND near a
          clip (max-channel>0.97 dilated 7px). Plus |b*| (blue↔yellow axis).
          Validated against the owner's 13.0 / 8.3 numbers. Use for SAME-colour-base
          comparisons (e.g. demosaic family) — it is NOT DC-invariant.
        - `fringe_hp`       : DC-INVARIANT metric — RMS of (chroma - local box-mean
          chroma) over the same edge&clip mask. Kills the global-recolour confound
          (an identity-matrix ablation recolours the whole image; the high-pass only
          sees the local blue↔yellow ALTERNATION). Use for matrix/WB ablations.
  * `green_tint`        — mean a* over a flat dark patch (the stage-wood green-tint
                          the owner flagged "see if it changes"), logged per variant.
  * crop PNGs           — saved for owner visual confirmation (we cannot see images).

GOTCHA GUARDS (both fail LOUD):
  * the module asserts `lrt_cinema` imports from the worktree src (a forgotten
    PYTHONPATH would silently run main-checkout code);
  * renders pin `LRT_CINEMA_BACKEND=numpy` (set by the caller) so code-level
    edits to `_rcd_demosaic.py` actually execute (numba twin would bypass them).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# --- worktree import guard (fail loud on a forgotten PYTHONPATH) ------------
import lrt_cinema

_WORKTREE_SRC = "/Users/dylan/Documents/001_CODE/lrt-cinema/.claude/worktrees/agent-a140ffb55f12cd08a/src"
if not lrt_cinema.__file__.startswith(_WORKTREE_SRC):
    raise RuntimeError(
        f"lrt_cinema imported from {lrt_cinema.__file__}, NOT the worktree src "
        f"({_WORKTREE_SRC}). Run with PYTHONPATH={_WORKTREE_SRC}."
    )

import colour  # noqa: E402

from lrt_cinema.dcp import parse_dcp  # noqa: E402
from lrt_cinema.pipeline import apply_adobe_pipeline, render_frame  # noqa: E402

# --- fixed paths (verified this session) -----------------------------------
NEF = "/Volumes/SanDisk Extreme Pro 55AF Media/Projects/2026 international faire timelapse/DSC_4053.NEF"
LRT_JPG = "/Volumes/SanDisk Extreme Pro 55AF Media/Projects/lrt-export/LRT_2026_international_faire_timelapse/LRT_00001.jpg"
DCP = (
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"
)

# Worst-fringe crops (rendered-frame pixel coords, top-left, size). The owner
# named the bottom-left 256^2 (y=1792 x=256); the window-tops sawtooth is the most
# diagnostic for directional false-colour (H/V decision flipping along a horizontal
# clipped edge). These are scanned + saved per variant.
CROPS = {
    # Owner's named worst 256^2 (bottom-left): empirically the heaviest-clipped,
    # highest-|b*| fringe block on the frame (clip frac 0.24, |b*|≈41, n≈17k).
    "botleft": (1792, 256, 256),
    # Upper window/fixture block — clipped light fixtures + window edges (clip 0.21).
    "fixtures": (1024, 1280, 256),
    # Another clipped upper-left block (window tops region).
    "winupper": (2048, 256, 256),
}

# Flat-ish dark patch for the tint scalar (mean a*), inside the bottom-left
# stage-wood. NB in our render this region is warm/magenta (a*>0), not green —
# tracked anyway since the owner flagged "see if it changes".
GREEN_TINT_PATCH = (1900, 300, 64)  # (y, x, size)

_D65_xy = np.array([0.31270, 0.32900])


# --- gym sRGB encoder (byte-identical to tests/test_pipeline.py) -----------


def prophoto_to_srgb_8bit(prophoto: np.ndarray) -> np.ndarray:
    """Linear ProPhoto(D50) → sRGB gamma-encoded uint8. Copied verbatim from
    tests/test_pipeline.py::_prophoto_to_srgb_8bit so the metric matches the gate."""
    m_prophoto_to_xyz_d50 = colour.RGB_COLOURSPACES["ProPhoto RGB"].matrix_RGB_to_XYZ
    m_xyz_d65_to_srgb = colour.RGB_COLOURSPACES["sRGB"].matrix_XYZ_to_RGB
    m_bradford = colour.adaptation.matrix_chromatic_adaptation_VonKries(
        np.array([0.96422, 1.0, 0.82521]),
        np.array([0.95047, 1.0, 1.08883]),
        transform="Bradford",
    )
    h, w, _ = prophoto.shape
    xyz_d50 = prophoto.reshape(-1, 3) @ m_prophoto_to_xyz_d50.T
    xyz_d65 = xyz_d50 @ m_bradford.T
    linear_srgb = xyz_d65 @ m_xyz_d65_to_srgb.T
    linear_srgb = np.clip(linear_srgb, 0.0, 1.0).reshape(h, w, 3)
    a = 0.055
    encoded = np.where(
        linear_srgb <= 0.0031308,
        linear_srgb * 12.92,
        (1 + a) * np.power(np.maximum(linear_srgb, 0), 1 / 2.4) - a,
    )
    return (encoded * 255).astype(np.uint8)


def srgb8_to_lab_d65(srgb_uint8: np.ndarray) -> np.ndarray:
    linear = colour.models.eotf_sRGB(srgb_uint8.astype(np.float64) / 255.0)
    xyz = colour.RGB_to_XYZ(linear, "sRGB", apply_cctf_decoding=False)
    return colour.XYZ_to_Lab(xyz, illuminant=_D65_xy)


# --- rendering variants ----------------------------------------------------


@dataclass
class Variant:
    name: str
    demosaic: str = "rcd"
    highlight_recovery: bool = False
    stop_after_stage: int = 9


def render_variant(v: Variant, profile=None) -> np.ndarray:
    """Render DSC_4053 through the variant → sRGB uint8 (full frame).

    For stop_after_stage 7 (tap-7, overrange-preserved linear ProPhoto) we still
    encode through the SAME sRGB encoder so the metric is comparable; the encoder's
    [0,1] clip is what the tap-9 ProfileToneCurve would otherwise impose, so a
    tap7-vs-tap9 comparison isolates the curve's highlight desaturation."""
    if profile is None:
        profile = parse_dcp(DCP)
    r = render_frame(
        NEF,
        profile,
        dcp_path=DCP,
        demosaic=v.demosaic,
        highlight_recovery=v.highlight_recovery,
        stop_after_stage=v.stop_after_stage,
    )
    return prophoto_to_srgb_8bit(r.prophoto)


def render_prophoto(v: Variant, profile=None) -> np.ndarray:
    """Same as render_variant but return the linear ProPhoto array (for Lab-on-
    linear measurement at tap-7 where overrange matters)."""
    if profile is None:
        profile = parse_dcp(DCP)
    r = render_frame(
        NEF,
        profile,
        dcp_path=DCP,
        demosaic=v.demosaic,
        highlight_recovery=v.highlight_recovery,
        stop_after_stage=v.stop_after_stage,
    )
    return r.prophoto


# --- metrics ---------------------------------------------------------------


def _box_mean(a: np.ndarray, radius: int) -> np.ndarray:
    """Separable box mean (reflect) over a 2-D plane; used for the DC-invariant
    high-pass. radius=r → (2r+1) window."""
    from scipy.ndimage import uniform_filter

    return uniform_filter(a, size=2 * radius + 1, mode="reflect")


def edge_clip_mask(srgb_uint8: np.ndarray, grad_pct: float = 97.0) -> np.ndarray:
    """The owner's mask: pixels that are BOTH a strong luminance gradient
    (|∇L*| > p`grad_pct`) AND near a clip (max-channel>0.97 dilated 7px)."""
    from scipy.ndimage import maximum_filter, sobel

    lab = srgb8_to_lab_d65(srgb_uint8)
    L = lab[..., 0]
    gx = sobel(L, axis=1, mode="reflect")
    gy = sobel(L, axis=0, mode="reflect")
    grad = np.hypot(gx, gy)
    strong = grad > np.percentile(grad, grad_pct)

    maxc = srgb_uint8.max(axis=-1).astype(np.float32) / 255.0
    near_clip = maximum_filter(maxc > 0.97, size=7)
    return strong & near_clip, lab


def fringe_metrics(
    srgb_uint8: np.ndarray, hp_radius: int = 6, pinned_mask: np.ndarray | None = None,
) -> dict:
    """Both fringe metrics on an sRGB-uint8 image.

    `pinned_mask`: when given, use THIS edge&clip mask instead of recomputing — so a
    WB/LookTable ablation (which shifts which pixels pass max>0.97) measures the SAME
    pixels as the baseline. This is mandatory for the DC-invariant matrix/WB verdict
    (a moving mask would confound the delta).

    Returns (see `_lab_fringe_stats`): chroma_at_edge, abs_b_at_edge, fringe_hp
    (DC-invariant magnitude), fringe_b / fringe_a (SIGNED blue↔yellow / green↔magenta
    alternation — what the owner's "alternating blue↔yellow" describes), n_mask.
    """
    if pinned_mask is not None:
        mask = pinned_mask
        lab = srgb8_to_lab_d65(srgb_uint8)
    else:
        mask, lab = edge_clip_mask(srgb_uint8)
    return _lab_fringe_stats(lab, mask, hp_radius)


def _lab_fringe_stats(lab: np.ndarray, mask: np.ndarray, hp_radius: int) -> dict:
    """Shared fringe stats from a Lab image + an edge&clip mask.

    fringe_hp  = RMS high-pass of chroma magnitude hypot(a,b) (DC-invariant).
    fringe_b/a = RMS high-pass of SIGNED b*/a* — the blue↔yellow / green↔magenta
                 ALTERNATION. fringe_hp (magnitude) is partly blind to a balanced
                 signed swing at constant magnitude; fringe_b/a capture exactly the
                 owner's "alternating blue↔yellow" sawtooth.
    """
    a = lab[..., 1]
    b = lab[..., 2]
    chroma = np.hypot(a, b)
    if mask.sum() == 0:
        return dict(
            chroma_at_edge=0.0, abs_b_at_edge=0.0, fringe_hp=0.0,
            fringe_b=0.0, fringe_a=0.0, n_mask=0,
        )
    chroma_hp = chroma - _box_mean(chroma, hp_radius)
    b_hp = b - _box_mean(b, hp_radius)
    a_hp = a - _box_mean(a, hp_radius)
    return dict(
        chroma_at_edge=float(chroma[mask].mean()),
        abs_b_at_edge=float(np.abs(b[mask]).mean()),
        fringe_hp=float(np.sqrt(np.mean(chroma_hp[mask] ** 2))),
        fringe_b=float(np.sqrt(np.mean(b_hp[mask] ** 2))),
        fringe_a=float(np.sqrt(np.mean(a_hp[mask] ** 2))),
        n_mask=int(mask.sum()),
    )


def prophoto_to_lab_d65(prophoto: np.ndarray) -> np.ndarray:
    """Linear ProPhoto(D50) → Lab(D65), NO [0,1] clip on chroma (overrange survives).
    For tap-7 where the fringe lives above 1.0."""
    m_pp_to_xyz = colour.RGB_COLOURSPACES["ProPhoto RGB"].matrix_RGB_to_XYZ
    m_bradford = colour.adaptation.matrix_chromatic_adaptation_VonKries(
        np.array([0.96422, 1.0, 0.82521]),
        np.array([0.95047, 1.0, 1.08883]),
        transform="Bradford",
    )
    h, w, _ = prophoto.shape
    xyz_d50 = prophoto.reshape(-1, 3) @ m_pp_to_xyz.T
    xyz_d65 = (xyz_d50 @ m_bradford.T).reshape(h, w, 3)
    return colour.XYZ_to_Lab(np.clip(xyz_d65, 0, None), illuminant=_D65_xy)


def prophoto_clip_edge_mask(prophoto: np.ndarray, lab: np.ndarray | None = None) -> np.ndarray:
    """Edge&clip mask on LINEAR ProPhoto: strong |∇L*| AND near a linear clip (max
    channel >0.97, dilated 7px). The linear analogue of `edge_clip_mask`."""
    from scipy.ndimage import maximum_filter, sobel

    if lab is None:
        lab = prophoto_to_lab_d65(prophoto)
    L = lab[..., 0]
    grad = np.hypot(sobel(L, axis=1, mode="reflect"), sobel(L, axis=0, mode="reflect"))
    strong = grad > np.percentile(grad, 97.0)
    near_clip = maximum_filter(prophoto.max(axis=-1) > 0.97, size=7)
    return strong & near_clip


def fringe_metrics_prophoto(
    prophoto: np.ndarray, hp_radius: int = 6, pinned_mask: np.ndarray | None = None,
) -> dict:
    """Fringe metrics on LINEAR ProPhoto (overrange survives). For tap-7. `pinned_mask`
    measures the same pixels across ablations."""
    lab = prophoto_to_lab_d65(prophoto)
    mask = pinned_mask if pinned_mask is not None else prophoto_clip_edge_mask(prophoto, lab)
    return _lab_fringe_stats(lab, mask, hp_radius)


def green_tint(srgb_uint8: np.ndarray, patch=GREEN_TINT_PATCH) -> float:
    """Mean a* over a flat dark patch (negative = green). The stage-wood green
    tint the owner flagged. Logged per variant to test co-variation with the fringe."""
    y, x, s = patch
    lab = srgb8_to_lab_d65(srgb_uint8[y : y + s, x : x + s])
    return float(lab[..., 1].mean())


# --- crop saving -----------------------------------------------------------


def save_crop(srgb_uint8: np.ndarray, crop, path: Path) -> None:
    from PIL import Image

    y, x, s = crop
    Image.fromarray(srgb_uint8[y : y + s, x : x + s]).save(str(path))


def metrics_on_crop(srgb_uint8: np.ndarray, crop) -> dict:
    y, x, s = crop
    return fringe_metrics(srgb_uint8[y : y + s, x : x + s])
