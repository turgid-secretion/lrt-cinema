"""Output stage: linear ProPhoto(D50) → final delivery format.

Stage 13 of the pipeline (per `docs/research/v06-architecture.md` §"Pipeline
stage order" and `docs/research/v07-spec-revision-plan.md`).

v0.7.0 presets:

  cinema-linear-finished → 16-bit half EXR (DWAB), linear Rec.2020 — γ; v0.7
                           default; 10-18× smaller than v0.6 cinema-aces
                           with full LRT intent baked.
  cinema-linear          → 32-bit float TIFF, linear Rec.2020 — v0.6
                           back-compat; uncompressed reference master.
  cinema-aces            → 32-bit float EXR (PIZ), linear Rec.2020 — v0.6
                           back-compat; one-time DeprecationWarning suggests
                           cinema-linear-finished.
  stills-finished        → 16-bit int TIFF, Rec.2020 (gamma) + AgX  —
                           DEFERRED to v0.6.x.

Color conversion math:
  ProPhoto(D50) → XYZ(D50) → [Bradford CAT] → XYZ(D65) → Rec.2020 linear

Library boundary:
  - tifffile for TIFF (battle-tested, ~300 ms for 24 MP 16-bit write).
  - OpenEXR (capital-O, the ASWF PyPI binding) for EXR. PIZ (lossless,
    v0.6 cinema-aces) and DWAB (visually-lossless cinema scene-referred
    standard, v0.7 cinema-linear-finished default) both supported.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Literal

import numpy as np

# --- Color-space transforms (ProPhoto(D50) → scene-linear target) ----------

# Emission colourspaces. The live cinema masters (cinema-linear-finished /
# -master) now target ACEScg (AP1) — the standards-aligned scene-referred
# grading space: Resolve exposes a named "ACEScg" Input Color Space (one clean
# click), whereas "linear Rec.2020" is a delivery gamut with NO matching Resolve
# Input entry (only the gamut-agnostic "Linear", which inherits the timeline
# gamut). See docs/research/v08-linear-exr-gamut-resolve-nuke.md. AP1/AP0 are
# ~D60-white; Rec.2020 is D65 — the Bradford CAT adapts to each target.
_COLOURSPACE_NAMES = {
    "rec2020": "ITU-R BT.2020",   # D65 — delivery gamut, v0.6/back-compat
    "acescg": "ACEScg",           # AP1, ~D60 — scene-referred grading default
    "aces2065": "ACES2065-1",     # AP0, ~D60 — archival / interchange
}
EXR_COLORSPACES = tuple(_COLOURSPACE_NAMES)


def _prophoto_to_linear(
    prophoto_d50: np.ndarray, colorspace: str = "rec2020",
) -> np.ndarray:
    """Linear ProPhoto(D50) → scene-linear `colorspace`, Bradford-adapted to
    that space's whitepoint (D65 for Rec.2020; ~D60 for ACEScg/ACES2065-1).

    `colorspace` ∈ EXR_COLORSPACES. Computed via `colour.RGB_to_RGB`.
    """
    import colour

    if colorspace not in _COLOURSPACE_NAMES:
        raise ValueError(
            f"colorspace must be one of {EXR_COLORSPACES}, got {colorspace!r}",
        )
    h, w, _ = prophoto_d50.shape
    out = colour.RGB_to_RGB(
        prophoto_d50.reshape(-1, 3).astype(np.float64),
        input_colourspace="ProPhoto RGB",
        output_colourspace=_COLOURSPACE_NAMES[colorspace],
        chromatic_adaptation_transform="Bradford",
        apply_cctf_decoding=False,
        apply_cctf_encoding=False,
    )
    return out.reshape(h, w, 3).astype(np.float32)


def _prophoto_to_rec2020(prophoto_d50: np.ndarray) -> np.ndarray:
    """Back-compat alias: ProPhoto(D50) → linear Rec.2020(D65)."""
    return _prophoto_to_linear(prophoto_d50, "rec2020")


def _exr_chromaticities(colorspace: str) -> tuple[float, ...]:
    """OpenEXR `chromaticities` attribute (Rx Ry Gx Gy Bx By Wx Wy) for
    `colorspace`, taken from colour-science so primaries/whitepoint are exact.

    Note: Resolve does NOT auto-read this attribute (verified — gamut comes from
    the clip's Input Color Space). It is written for standards-correct
    interchange (Nuke/OIIO/archival) and self-documentation."""
    import colour

    cs = colour.RGB_COLOURSPACES[_COLOURSPACE_NAMES[colorspace]]
    p, wp = cs.primaries, cs.whitepoint
    return (
        float(p[0, 0]), float(p[0, 1]), float(p[1, 0]), float(p[1, 1]),
        float(p[2, 0]), float(p[2, 1]), float(wp[0]), float(wp[1]),
    )


# --- TIFF writer -----------------------------------------------------------


def write_tiff_linear_rec2020(
    prophoto: np.ndarray, dst: Path | str, bit_depth: int = 32,
) -> Path:
    """Convert linear ProPhoto(D50) → linear Rec.2020(D65) and write a TIFF.

    `bit_depth=32` (default) writes 32-bit float — required for linear
    scene-referred data; 16-bit linear has ~6 bits of precision in the
    bottom stop, insufficient for Resolve grade. Float preserves the
    overrange (>1) signal.

    `bit_depth=16` writes 16-bit unsigned int, clipping to [0, 1] —
    suitable only for already-graded display-referred content.

    `bit_depth=8` writes 8-bit unsigned int — preview/contact-sheet use
    only.
    """
    import tifffile

    if bit_depth not in (8, 16, 32):
        raise ValueError(f"bit_depth must be 8, 16, or 32, got {bit_depth}")
    rec2020 = _prophoto_to_rec2020(prophoto)
    if bit_depth == 32:
        pixels = rec2020.astype(np.float32)
    elif bit_depth == 16:
        clipped = np.clip(rec2020, 0.0, 1.0)
        pixels = (clipped * 65535.0 + 0.5).astype(np.uint16)
    else:
        clipped = np.clip(rec2020, 0.0, 1.0)
        pixels = (clipped * 255.0 + 0.5).astype(np.uint8)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(
        str(dst),
        pixels,
        photometric="rgb",
    )
    return dst


# --- EXR writer ------------------------------------------------------------

_EXR_BIT_DEPTHS = ("half", "float")
_EXR_COMPRESSIONS = ("piz", "zip", "dwab")


def write_exr_linear_rec2020(
    prophoto: np.ndarray,
    dst: Path | str,
    bit_depth: Literal["half", "float"] = "half",
    compression: Literal["piz", "zip", "dwab"] = "dwab",
    colorspace: Literal["rec2020", "acescg", "aces2065"] = "rec2020",
) -> Path:
    """Write a scene-linear OpenEXR.

    `colorspace` (default `"rec2020"` for back-compat; the live cinema presets
    pass `"acescg"`): emission gamut/whitepoint, one of `EXR_COLORSPACES`. The
    matching `chromaticities` attribute is written to the header (and
    `acesImageContainerFlag=1` for `"aces2065"`). Resolve ignores these tags
    (gamut = the clip's Input Color Space) but Nuke/OIIO/archival honor them.

    `bit_depth`:
      - `"half"` (default; v0.7 cinema-linear-finished) — 16-bit float.
        Carries ~30 stops of headroom; cinema scene-referred standard.
      - `"float"` (v0.6 cinema-aces back-compat) — 32-bit float. 2× the
        bytes; recoverable past half's denormal floor (rare).

    `compression`:
      - `"dwab"` (default; v0.7) — DCT-based, visually-lossless lossy.
        10-18× smaller than PIZ at half precision. Cinema scene-referred
        compressed-intermediate default.
      - `"piz"` (v0.6 cinema-aces back-compat) — wavelet, lossless.
      - `"zip"` — deflate, lossless. Mid-pack size/speed.

    Channels MUST be C-contiguous arrays. Passing strided views of the
    interleaved (H, W, 3) source (e.g. `rec2020[..., 0]`) silently
    produces garbled per-channel data on real-sized renders — the binding
    reads with a tight stride assumption. `np.ascontiguousarray` forces
    a per-channel copy.
    """
    import OpenEXR

    if bit_depth not in _EXR_BIT_DEPTHS:
        raise ValueError(
            f"bit_depth must be one of {_EXR_BIT_DEPTHS}, got {bit_depth!r}",
        )
    if compression not in _EXR_COMPRESSIONS:
        raise ValueError(
            f"compression must be one of {_EXR_COMPRESSIONS}, got {compression!r}",
        )

    pixels = _prophoto_to_linear(prophoto, colorspace).astype(np.float32)
    if bit_depth == "half":
        pixels = pixels.astype(np.float16)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    compression_const = {
        "piz": OpenEXR.PIZ_COMPRESSION,
        "zip": OpenEXR.ZIP_COMPRESSION,
        "dwab": OpenEXR.DWAB_COMPRESSION,
    }[compression]
    header = {
        "compression": compression_const,
        "type": OpenEXR.scanlineimage,
        # Standards-correct gamut tag (Resolve ignores it; Nuke/OIIO honor it).
        "chromaticities": _exr_chromaticities(colorspace),
    }
    if colorspace == "aces2065":
        header["acesImageContainerFlag"] = 1
    channels = {
        "R": np.ascontiguousarray(pixels[..., 0]),
        "G": np.ascontiguousarray(pixels[..., 1]),
        "B": np.ascontiguousarray(pixels[..., 2]),
    }
    with OpenEXR.File(header, channels) as exr:
        exr.write(str(dst))
    return dst


# --- Preset dispatch -------------------------------------------------------

_CINEMA_ACES_DEPRECATION_WARNED = False


def _warn_cinema_aces_once() -> None:
    """Emit the cinema-aces DeprecationWarning at most once per process.

    Per `docs/research/v07-spec-revision-plan.md` §"Phase 1 — ship
    cinema-linear-finished (γ)", the v0.6 cinema-aces preset continues to
    work for one release cycle with a deprecation pointer to the v0.7
    default. Module-level guard so a 5000-frame render emits one warning,
    not 5000."""
    global _CINEMA_ACES_DEPRECATION_WARNED
    if _CINEMA_ACES_DEPRECATION_WARNED:
        return
    _CINEMA_ACES_DEPRECATION_WARNED = True
    warnings.warn(
        "preset 'cinema-aces' is deprecated; use 'cinema-linear-finished' "
        "(half-float DWAB EXR — same color science, 10-18× smaller). "
        "cinema-aces will be removed in v0.8.",
        DeprecationWarning,
        stacklevel=3,
    )


def write_preset_output(
    prophoto: np.ndarray, dst_stem: Path | str, preset: str,
) -> Path:
    """Dispatch to the right writer based on preset name.

    `dst_stem` is the path WITHOUT extension; the writer appends .tif / .exr.
    Returns the final written path.

    `stills-finished` raises NotImplementedError in v0.7 — AgX port is
    still v0.6.x scope.
    """
    dst_stem = Path(dst_stem)
    if preset in ("cinema-linear-finished", "cinema-linear-master"):
        # Identical writer: both presets ship half-float DWAB EXR. The
        # difference is upstream — γ (cinema-linear-finished) emits
        # Stage-13 ProPhoto (full DCP shape baked); β
        # (cinema-linear-master) emits Stage-7 ProPhoto (LookTable +
        # ProfileToneCurve skipped). CLI worker decides which by setting
        # `stop_after_stage` on render_frame.
        # Emit scene-linear ACEScg (AP1) — the standards-aligned grading space
        # (named Resolve Input entry; linear Rec.2020 has none). Tag is written
        # for interchange; Resolve picks gamut from Input Color Space = ACEScg.
        return write_exr_linear_rec2020(
            prophoto, dst_stem.with_suffix(".exr"),
            bit_depth="half", compression="dwab", colorspace="acescg",
        )
    if preset == "cinema-linear":
        return write_tiff_linear_rec2020(prophoto, dst_stem.with_suffix(".tif"))
    if preset == "cinema-aces":
        _warn_cinema_aces_once()
        return write_exr_linear_rec2020(
            prophoto, dst_stem.with_suffix(".exr"),
            bit_depth="float", compression="piz",
        )
    if preset == "stills-finished":
        raise NotImplementedError(
            "stills-finished preset (Rec.2020 + AgX) is deferred to v0.6.x. "
            "Use cinema-linear-finished, cinema-linear-master, cinema-linear, "
            "or cinema-aces."
        )
    raise ValueError(
        f"Unknown preset {preset!r}. Valid: cinema-linear-finished, "
        f"cinema-linear-master, cinema-linear, cinema-aces, "
        f"stills-finished (v0.6.x)."
    )
