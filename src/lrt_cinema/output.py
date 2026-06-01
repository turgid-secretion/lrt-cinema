"""Output stage: linear ProPhoto(D50) → final delivery format.

Stage 13 of the pipeline (per `docs/research/v06-architecture.md` §"Pipeline
stage order" and `docs/research/v07-spec-revision-plan.md`).

Presets (only standards-aligned colour spaces — see CLAUDE.md allowlist):

  lrtimelapse            → 16-bit sRGB display TIFF (embedded ICC) — v0.8
                           DEFAULT. Display-referred, full LRT look baked;
                           the only emission LRT's video renderer re-ingests
                           (LRT → Render from Intermediate → Motion Blur).
  cinema-linear-finished → 16-bit half EXR (DWAB), scene-linear ACEScg (AP1) —
                           γ; master for DaVinci Resolve / ACES (bypasses LRT);
                           full LRT intent baked.
  cinema-linear-master   → 16-bit half EXR (DWAB), scene-linear ACEScg (AP1) —
                           β; Stage-7 emit for HDR headroom.
  stills-finished        → display Rec.2020 (gamma) + AgX — DEFERRED.

(Removed: cinema-linear / cinema-aces — both emitted *linear Rec.2020*, a
delivery gamut misused as scene-referred. ACEScg/ACES2065-1 are the only
standards-aligned scene-linear gamuts; see CLAUDE.md.)

Color conversion math (scene-linear EXR):
  ProPhoto(D50) → XYZ(D50) → [Bradford CAT D50→~D60] → ACEScg (AP1) linear

Library boundary:
  - tifffile for TIFF (battle-tested, ~300 ms for 24 MP 16-bit write).
  - OpenEXR (capital-O, the ASWF PyPI binding) for EXR. DWAB (visually-lossless
    cinema scene-referred default) + PIZ/ZIP (lossless) supported.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Literal

import numpy as np

from lrt_cinema import __version__

# --- Color-space transforms (ProPhoto(D50) → scene-linear target) ----------

# Scene-referred (linear) emission colourspaces — the standards-aligned set ONLY.
# Linear Rec.2020 is deliberately ABSENT: Rec.2020 (ITU-R BT.2020) is a
# delivery/display gamut, and using it linear/scene-referred is a colour-science
# error ("Franken-gamut") with no matching Resolve Input entry — see CLAUDE.md
# §"Colour-space allowlist" and docs/research/v08-linear-exr-gamut-resolve-nuke.md.
# AP1/AP0 are ~D60-white; the Bradford CAT adapts ProPhoto(D50)→target.
_COLOURSPACE_NAMES = {
    "acescg": "ACEScg",           # AP1, ~D60 — scene-referred grading DEFAULT
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
    prophoto_d50: np.ndarray, colorspace: str = "acescg",
) -> np.ndarray:
    """Linear ProPhoto(D50) → scene-linear `colorspace`, Bradford-adapted to
    that space's whitepoint (~D60 for ACEScg/ACES2065-1).

    `colorspace` ∈ EXR_COLORSPACES (acescg | aces2065). Computed via
    `colour.RGB_to_RGB`.
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


# --- ACES Reference Gamut Compression (RGC) — AP1 gamut safety -------------
#
# The single, gated gamut-safety pass for the scene-linear ACEScg (AP1) EXR
# path (DECISIONS.md §7 contract 2; v10 research §3.5). The perceptual develop
# ops (DR-compression — shipped; OKLCh HSL / ASC-CDL grade — coming) can push
# pixels OUTSIDE AP1; after the ProPhoto(D50)→AP1 Bradford in
# `_prophoto_to_linear` an out-of-AP1 colour presents as one or more **negative
# AP1 channels**. Without compression those hard-clip at the float→half encode
# (posterised, hue-shifted speculars). RGC rolls them smoothly back toward the
# achromatic axis BEFORE the encode.
#
# This is the canonical Academy 1.3 transform (`urn:ampas:aces:transformId:
# v1.5:LMT.Academy.GamutCompress.a1.3.0`), hand-coded from the spec
# (https://docs.acescentral.com/rgc/specification/, Equations 2–4) and the
# aces-dev reference DCTL `LMT.Academy.ReferenceGamutCompress` — `colour` 0.4.x
# has NO general gamut compression. The reference constants are exact (NOT
# tuning): they are the published Academy defaults, verified against both the
# spec and the DCTL this session.
#
# Per-channel maximum distances (the AP1↔AP0 boundary distances, in the order
# the achromatic-distance channels are indexed: R-distance↔Cyan, G↔Magenta,
# B↔Yellow). At `distance == limit` the channel reconstructs to exactly the
# gamut boundary (0); beyond the limit it stays compressed-but-negative by
# design (the asymptote is `threshold + scale ≈ 1.14 / 1.09 / 1.03`, never 1.0)
# — RGC is gamut *compression*, not a hard clamp, so we deliberately do NOT
# clip residual negatives afterwards.
_RGC_LIMIT = np.array([1.147, 1.264, 1.312], dtype=np.float64)   # Cyan/Mag/Yel
_RGC_THRESHOLD = np.array([0.815, 0.803, 0.880], dtype=np.float64)
_RGC_POWER = 1.2


def _rgc_scale(threshold: np.ndarray, limit: np.ndarray, power: float) -> np.ndarray:
    """Per-channel scale `s` that makes the compression curve pass through
    `(distance=limit) → 1.0` (the spec's defining property — Eq. 4):

        s = (l − t) / ( ((1 − t)/(l − t))**(−p) − 1 )**(1/p)
    """
    return (limit - threshold) / (
        ((1.0 - threshold) / (limit - threshold)) ** (-power) - 1.0
    ) ** (1.0 / power)


def _rgc_compress_distance(dist: np.ndarray) -> np.ndarray:
    """The ACES RGC forward compression curve applied per achromatic-distance
    channel (spec Eq. 4 / DCTL `compress`):

        d_c = d                                          if d < t   (identity)
        d_c = t + s · nd / (1 + nd**p)**(1/p)            otherwise

    with `nd = (d − t)/s`. `dist` is (..., 3); thresholds/limits broadcast over
    the last axis. The identity branch is preserved exactly by `np.where`; the
    `max(d − t, 0)` floor keeps `nd**p` finite (a raw negative base would NaN
    and trip the writer's NaN scrub)."""
    dist = np.asarray(dist, dtype=np.float64)
    scl = _rgc_scale(_RGC_THRESHOLD, _RGC_LIMIT, _RGC_POWER)
    nd = np.maximum(dist - _RGC_THRESHOLD, 0.0) / scl
    rolled = _RGC_THRESHOLD + scl * nd / (1.0 + nd ** _RGC_POWER) ** (1.0 / _RGC_POWER)
    return np.where(dist < _RGC_THRESHOLD, dist, rolled)


def _aces_rgc_compress_ap1(ap1: np.ndarray) -> np.ndarray:
    """Apply the gated ACES Reference Gamut Compression to AP1-linear pixels.

    `ap1` is (H, W, 3) ACEScg (AP1) linear. Returns AP1 with out-of-AP1
    excursions (the negative channels of colours outside AP1) compressed toward
    the achromatic axis.

    **Gated:** if NO channel of any pixel reaches its compression threshold the
    function returns the **input array unchanged** (the literal object — no copy
    or cast), so an in-gamut / low-saturation render is **byte-exact identity**
    and the gym/rose ΔE ship gate path (which never goes out of AP1) is
    untouched. NB the threshold (~0.8) sits *inside* the gamut boundary, so
    deeply-saturated but still-in-gamut colours in `[threshold, 1]` are also
    compressed — that is correct RGC, not a defect.

    Algorithm (spec Eq. 2–4):
      ach  = max(R, G, B)                       (the achromatic component)
      d    = (ach − rgb) / |ach|                (per-channel distance; 0 if ach==0)
      d'   = compress(d)                        (identity below threshold)
      rgb' = ach − d' · |ach|                   (reconstruct)

    The MAX channel always has d=0 → identity, so RGC never darkens the
    achromatic/luminance peak; it only pulls the trailing (low/negative)
    channels in. Achromatic (grey) pixels have d=0 on all channels → exact
    identity per pixel."""
    ach = np.max(ap1, axis=-1, keepdims=True)
    abs_ach = np.abs(ach)
    # Distance is undefined where ach == 0 (pure black); the spec sets d=0 there
    # (→ identity), so guard the division and force those pixels to passthrough.
    safe = abs_ach > 0.0
    dist = np.where(safe, (ach - ap1) / np.where(safe, abs_ach, 1.0), 0.0)

    # Gate: nothing reaches threshold → byte-exact no-op (return the input).
    if not np.any(dist >= _RGC_THRESHOLD):
        return ap1

    dist_c = _rgc_compress_distance(dist)
    return (ach - dist_c * abs_ach).astype(ap1.dtype, copy=False)


# --- Display-referred TIFF writer (LRTimelapse round-trip) -----------------


def _srgb_icc_bytes() -> bytes:
    """Standard sRGB ICC profile bytes (embedded; no Pillow runtime dependency).

    Embedding this in the output TIFF is what makes the LRT round-trip robust:
    Gunther Wegner's documented gamma/contrast shifts come from viewers/LRT
    *misinterpreting* an untagged or wide-gamut file. A correct sRGB ICC removes
    that ambiguity. Source bytes live in `lrt_cinema._srgb_icc`."""
    from lrt_cinema._srgb_icc import srgb_icc_bytes

    return srgb_icc_bytes()


# Cache: ProPhoto(D50)→display-primaries linear matrix (Bradford CAT baked in),
# float32, keyed by display colourspace. Built once from colour-science so it is
# the SAME composed transform `colour.RGB_to_RGB` applies — see `_prophoto_to_display`.
_DISPLAY_MATRIX_CACHE: dict[str, np.ndarray] = {}


def _display_matrix(colorspace: str) -> np.ndarray:
    """Composed ProPhoto(D50)→`colorspace`-linear 3×3 (Bradford), float32, cached.

    `colour.matrix_RGB_to_RGB` returns exactly the matrix `colour.RGB_to_RGB`
    composes internally (ProPhoto→XYZ, Bradford D50→target white, XYZ→target),
    so applying it then the target CCTF is equivalent-by-construction to the old
    `colour.RGB_to_RGB(...)` call — only float32-vs-float64 differs, which is
    sub-16-bit-quantisation (verified max 0.12 code units on random ±overrange
    input). Built once per colourspace; the matmul is then plain numpy."""
    if colorspace not in _DISPLAY_MATRIX_CACHE:
        import colour
        m = colour.matrix_RGB_to_RGB(
            colour.RGB_COLOURSPACES["ProPhoto RGB"],
            colour.RGB_COLOURSPACES[_DISPLAY_COLOURSPACE_NAMES[colorspace]],
            chromatic_adaptation_transform="Bradford",
        )
        _DISPLAY_MATRIX_CACHE[colorspace] = np.asarray(m, dtype=np.float32)
    return _DISPLAY_MATRIX_CACHE[colorspace]


def _srgb_oetf(x: np.ndarray) -> np.ndarray:
    """Linear → sRGB-encoded (IEC 61966-2-1 piecewise OETF).

    Bit-identical to `colour.RGB_COLOURSPACES["sRGB"].cctf_encoding` (verified
    max diff 0.0); duplicated here so the hot display encode is a single numpy
    pass with no float64 round-trip. Negatives take the linear segment (stay
    negative) and are clipped downstream by the writer, matching colour."""
    return np.where(
        x <= 0.0031308,
        x * 12.92,
        1.055 * np.power(np.maximum(x, 0.0), 1.0 / 2.4) - 0.055,
    )


def _prophoto_to_display(
    prophoto_d50: np.ndarray, colorspace: str = "srgb",
) -> np.ndarray:
    """Linear ProPhoto(D50) → display-referred `colorspace`, gamma-encoded.

    Bradford-adapts D50→the target whitepoint (D65 for sRGB) and applies the
    target's encoding CCTF (sRGB OETF for `srgb`). `colorspace` ∈
    DISPLAY_COLORSPACES. Output is nominally [0, 1] before clipping.

    The `srgb` default (the LRT round-trip / measured hot path) takes a fast
    numpy path: a cached float32 composed matrix (`_display_matrix`) + the
    in-module sRGB OETF, replacing a per-frame float64 `colour.RGB_to_RGB`
    (~1.7 s/frame at 24 MP). Equivalent-by-construction to the old call (see
    `_display_matrix`). Other display targets (rare; ICC-gated, not hot) keep
    the reference `colour.RGB_to_RGB` so each target's exact CCTF is used."""
    if colorspace not in _DISPLAY_COLOURSPACE_NAMES:
        raise ValueError(
            f"colorspace must be one of {DISPLAY_COLORSPACES}, got {colorspace!r}",
        )
    h, w, _ = prophoto_d50.shape
    if colorspace == "srgb":
        lin = prophoto_d50.reshape(-1, 3).astype(np.float32) @ _display_matrix("srgb").T
        return _srgb_oetf(lin).reshape(h, w, 3)
    import colour
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
    pre_encoded: bool = False,
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

    `pre_encoded`: when True, `prophoto` is ALREADY the display-encoded
    `colorspace` float [0, 1] (e.g. from the MLX GPU path, which encodes
    on-device) — skip the ProPhoto→display conversion and quantise directly.
    The ICC / provenance / NaN-scrub / clip path is otherwise identical, so the
    emitted file is indistinguishable from the numpy-encoded one.
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

    encoded = prophoto if pre_encoded else _prophoto_to_display(prophoto, colorspace)
    # np.clip does NOT sanitize NaN (nan→clip→nan→uint cast→0): a non-finite
    # pixel would silently render solid black with no diagnostic. Scrub + warn
    # so upstream corruption is visible, never a silent black/white frame.
    n_nonfinite = int(np.count_nonzero(~np.isfinite(encoded)))
    if n_nonfinite:
        warnings.warn(
            f"{n_nonfinite} non-finite (NaN/Inf) pixel value(s) scrubbed before "
            f"quantising {Path(dst).name} — upstream produced invalid pixels; "
            f"investigate rather than ship a silently-corrupt frame.",
            stacklevel=2,
        )
        encoded = np.nan_to_num(encoded, nan=0.0, posinf=1.0, neginf=0.0)
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


def write_exr_scene_linear(
    prophoto: np.ndarray,
    dst: Path | str,
    bit_depth: Literal["half", "float"] = "half",
    compression: Literal["piz", "zip", "dwab"] = "dwab",
    colorspace: Literal["acescg", "aces2065"] = "acescg",
) -> Path:
    """Write a scene-linear OpenEXR in a standards-aligned gamut.

    `colorspace` (default `"acescg"`): emission gamut/whitepoint, one of
    `EXR_COLORSPACES` (`acescg` = AP1 grading; `aces2065` = AP0 archival). The
    matching `chromaticities` attribute is written to the header (and
    `acesImageContainerFlag=1` for `"aces2065"`). Resolve ignores these tags
    (gamut = the clip's Input Color Space) but Nuke/OIIO/archival honor them.
    (Linear Rec.2020 is deliberately NOT an option — see CLAUDE.md allowlist.)

    `bit_depth`:
      - `"half"` (default) — 16-bit float. ~30 stops of headroom; cinema
        scene-referred standard.
      - `"float"` — 32-bit float. 2× the bytes; recoverable past half's
        denormal floor (rare).

    `compression`:
      - `"dwab"` (default) — DCT-based, visually-lossless lossy. 10-18× smaller
        than PIZ at half precision. Cinema scene-referred intermediate default.
      - `"piz"` — wavelet, lossless.
      - `"zip"` — deflate, lossless. Mid-pack size/speed.

    Channels MUST be C-contiguous arrays. Passing strided views of the
    interleaved (H, W, 3) source silently produces garbled per-channel data on
    real-sized renders — the binding reads with a tight stride assumption.
    `np.ascontiguousarray` forces a per-channel copy.
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
    # Scene-linear keeps intended overrange (>1), but NaN/Inf are corruption —
    # scrub (NaN→0, +Inf→half-max, -Inf→0) and warn so it is never silent.
    n_nonfinite = int(np.count_nonzero(~np.isfinite(pixels)))
    if n_nonfinite:
        warnings.warn(
            f"{n_nonfinite} non-finite (NaN/Inf) pixel value(s) scrubbed before "
            f"writing {Path(dst).name} — upstream produced invalid pixels.",
            stacklevel=2,
        )
        pixels = np.nan_to_num(pixels, nan=0.0, posinf=65504.0, neginf=0.0)
    # ACES Reference Gamut Compression — the single, gated AP1 gamut-safety pass
    # (DECISIONS.md §7 contract 2). Perceptual develop ops can push pixels out of
    # AP1, presenting as negative AP1 channels here; RGC rolls them smoothly back
    # toward the achromatic axis instead of letting them hard-clip at the encode.
    # ACEScg (AP1) ONLY — the reference limits are AP1-specific; AP0 (aces2065)
    # is wider and is not compressed. Gated: a no-op (byte-exact) when nothing is
    # near/out of gamut, so the in-gamut ship-gate path is untouched.
    if colorspace == "acescg":
        pixels = _aces_rgc_compress_ap1(pixels)
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
        return write_exr_scene_linear(
            prophoto, dst_stem.with_suffix(".exr"),
            bit_depth="half", compression="dwab", colorspace="acescg",
        )
    if preset == "stills-finished":
        raise NotImplementedError(
            "stills-finished preset (display Rec.2020 + AgX) is deferred. "
            "Use lrtimelapse (default), cinema-linear-finished, or "
            "cinema-linear-master."
        )
    raise ValueError(
        f"Unknown preset {preset!r}. Valid: lrtimelapse, "
        f"cinema-linear-finished, cinema-linear-master, "
        f"stills-finished (deferred)."
    )
