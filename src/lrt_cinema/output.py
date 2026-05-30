"""Output stage: linear ProPhoto(D50) → final delivery format.

Stage 13 of the pipeline (per `docs/research/v06-architecture.md` §"Pipeline
stage order" and `docs/research/v07-spec-revision-plan.md`).

Presets:

  lrtimelapse            → 16-bit sRGB display TIFF (embedded ICC) — v0.8
                           DEFAULT. Display-referred, full LRT look baked;
                           the only emission LRT's video renderer re-ingests
                           (LRT → Render from Intermediate → Motion Blur).
  cinema-linear-finished → 16-bit half EXR (DWAB), ACEScg — γ; scene-linear
                           master for DaVinci Resolve / ACES (bypasses LRT);
                           full LRT intent baked.
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

import json
import warnings
from pathlib import Path
from typing import Literal

import numpy as np

from lrt_cinema import __version__

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

# Display-referred (gamma-encoded) emission colourspaces for the LRTimelapse
# round-trip and other display deliverables. Unlike the scene-linear EXR path,
# these apply the output space's encoding CCTF (e.g. the sRGB OETF) so the file
# is display-referred — exactly what LRT's video renderer expects. `srgb`
# (Rec.709 primaries) is the LRT-safe default per Gunther Wegner's guidance;
# wider gamuts round-trip into LRT only with a correct embedded ICC, so the
# writer refuses a non-sRGB target unless an ICC profile is supplied.
_DISPLAY_COLOURSPACE_NAMES = {
    "srgb": "sRGB",                  # Rec.709 primaries + sRGB OETF — LRT default
    "adobergb": "Adobe RGB (1998)",  # wider gamut; needs bundled ICC
    "prophoto": "ProPhoto RGB",      # widest; needs bundled ICC
    "rec2020": "ITU-R BT.2020",      # HDR-ish display; needs bundled ICC
}
DISPLAY_COLORSPACES = tuple(_DISPLAY_COLOURSPACE_NAMES)

# Human/machine-readable colour facts embedded in every display TIFF so the
# file is self-describing (missing colour info is the root cause of LRT's
# documented gamma/contrast shifts).
_DISPLAY_PRIMARIES = {
    "srgb": "Rec.709", "adobergb": "Adobe RGB",
    "prophoto": "ProPhoto", "rec2020": "Rec.2020",
}
_DISPLAY_TRANSFER = {
    "srgb": "sRGB", "adobergb": "gamma2.2",
    "prophoto": "gamma1.8", "rec2020": "BT.2020",
}

# TIFF tag 34675 = ICC Profile ("InterColorProfile"); TIFF datatype 7 = UNDEFINED.
_ICC_PROFILE_TAG = 34675
_TIFF_TYPE_UNDEFINED = 7


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


# --- Display-referred TIFF writer (LRTimelapse round-trip) -----------------


def _srgb_icc_bytes() -> bytes:
    """Standard sRGB ICC profile bytes (built via littleCMS through Pillow).

    Embedding this in the output TIFF is what makes the LRT round-trip robust:
    Gunther Wegner's documented gamma/contrast shifts come from viewers/LRT
    *misinterpreting* an untagged or wide-gamut file. A correct sRGB ICC removes
    that ambiguity."""
    from PIL import ImageCms

    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _prophoto_to_display(
    prophoto_d50: np.ndarray, colorspace: str = "srgb",
) -> np.ndarray:
    """Linear ProPhoto(D50) → display-referred `colorspace`, gamma-encoded.

    Bradford-adapts D50→the target whitepoint (D65 for sRGB) and applies the
    target's encoding CCTF (sRGB OETF for `srgb`). `colorspace` ∈
    DISPLAY_COLORSPACES. Output is nominally [0, 1] before clipping."""
    import colour

    if colorspace not in _DISPLAY_COLOURSPACE_NAMES:
        raise ValueError(
            f"colorspace must be one of {DISPLAY_COLORSPACES}, got {colorspace!r}",
        )
    h, w, _ = prophoto_d50.shape
    out = colour.RGB_to_RGB(
        prophoto_d50.reshape(-1, 3).astype(np.float64),
        input_colourspace="ProPhoto RGB",
        output_colourspace=_DISPLAY_COLOURSPACE_NAMES[colorspace],
        chromatic_adaptation_transform="Bradford",
        apply_cctf_decoding=False,   # our data is already linear
        apply_cctf_encoding=True,    # encode to the display transfer function
    )
    return out.reshape(h, w, 3)


def write_tiff_display(
    prophoto: np.ndarray,
    dst: Path | str,
    colorspace: str = "srgb",
    bit_depth: Literal[8, 16] = 16,
    icc_profile: bytes | None = None,
    provenance: dict | str | None = None,
) -> Path:
    """Write a display-referred, gamma-encoded integer TIFF for the LRT
    round-trip (LRT → Render from Intermediate → Motion Blur).

    Pipeline: linear ProPhoto(D50) → `colorspace` (Bradford CAT) → encoding CCTF
    → clip [0, 1] → integer quantise → TIFF with an **embedded ICC profile** and
    provenance metadata.

    `colorspace` (default `"srgb"`, the LRT-safe Rec.709/sRGB target). For any
    non-sRGB display target an `icc_profile` MUST be supplied — emitting a
    wide-gamut TIFF *without* a profile is the exact footgun behind LRT's
    documented gamma/colour shifts, so the writer refuses it rather than guess.

    `bit_depth` 16 (default) or 8. 16-bit is the point of replacing LRT's 8-bit
    internal path; 8-bit matches LRT's internal-render equivalent.

    `provenance` is embedded in ImageDescription (JSON if a dict). It carries
    colour space / transfer / range so downstream tools self-describe; no
    timestamps, so renders are byte-reproducible.
    """
    import tifffile

    if bit_depth not in (8, 16):
        raise ValueError(f"display TIFF bit_depth must be 8 or 16, got {bit_depth}")
    if colorspace not in _DISPLAY_COLOURSPACE_NAMES:
        raise ValueError(
            f"colorspace must be one of {DISPLAY_COLORSPACES}, got {colorspace!r}",
        )
    if icc_profile is None:
        if colorspace == "srgb":
            icc_profile = _srgb_icc_bytes()
        else:
            raise ValueError(
                f"an ICC profile is required for non-sRGB display colourspace "
                f"{colorspace!r}; pass icc_profile=<bytes> (emitting a wide-gamut "
                f"TIFF without a profile causes LRT colour/gamma shifts).",
            )

    encoded = _prophoto_to_display(prophoto, colorspace)
    clipped = np.clip(encoded, 0.0, 1.0)
    if bit_depth == 16:
        pixels = (clipped * 65535.0 + 0.5).astype(np.uint16)
    else:
        pixels = (clipped * 255.0 + 0.5).astype(np.uint8)

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    if isinstance(provenance, str):
        description = provenance
    else:
        # Always self-describe the colour encoding; merge any caller context.
        meta = {
            "tool": f"lrt-cinema {__version__}",
            "colorspace": _DISPLAY_COLOURSPACE_NAMES[colorspace],
            "primaries": _DISPLAY_PRIMARIES[colorspace],
            "transfer": _DISPLAY_TRANSFER[colorspace],
            "range": "full",
            "bit_depth": bit_depth,
        }
        if provenance:
            meta.update(provenance)
        description = json.dumps(meta, sort_keys=True)

    tifffile.imwrite(
        str(dst),
        pixels,
        photometric="rgb",
        software=f"lrt-cinema {__version__}",
        description=description,
        extratags=[(
            _ICC_PROFILE_TAG, _TIFF_TYPE_UNDEFINED, len(icc_profile),
            icc_profile, True,
        )],
        metadata=None,  # suppress tifffile's own JSON in ImageDescription
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
    prophoto: np.ndarray,
    dst_stem: Path | str,
    preset: str,
    provenance: dict | None = None,
) -> Path:
    """Dispatch to the right writer based on preset name.

    `dst_stem` is the path WITHOUT extension; the writer appends .tif / .exr.
    Returns the final written path.

    `provenance` (optional) is per-frame context (source frame, frame index)
    merged into the emitted file metadata for the display-TIFF target.

    `stills-finished` raises NotImplementedError in v0.7 — AgX port is
    still v0.6.x scope.
    """
    dst_stem = Path(dst_stem)
    if preset == "lrtimelapse":
        # Default target: display-referred 16-bit sRGB TIFF for the LRTimelapse
        # round-trip (LRT → Render from Intermediate → Motion Blur). Full LRT
        # look baked at Stage 9 + develop ops; sRGB ICC embedded. The writer
        # self-describes the colour encoding; we add per-frame context.
        meta = {"preset": preset}
        if provenance:
            meta.update(provenance)
        return write_tiff_display(
            prophoto, dst_stem.with_suffix(".tif"),
            colorspace="srgb", bit_depth=16, provenance=meta,
        )
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
