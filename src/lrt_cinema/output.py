"""Output stage: linear ProPhoto(D50) → final delivery format.

Stage 13 of the pipeline (per `docs/research/v06-architecture.md` §"Pipeline
stage order"). Three presets, three containers:

  cinema-linear     → 32-bit float TIFF, linear Rec.2020 — Resolve linear input
  cinema-aces       → 32-bit float EXR (PIZ), linear Rec.2020 — ACES IDT clean 3×3
  stills-finished   → 16-bit int TIFF, Rec.2020 (gamma) + AgX  — DEFERRED to v0.6.x

Color conversion math:
  ProPhoto(D50) → XYZ(D50) → [Bradford CAT] → XYZ(D65) → Rec.2020 linear

Library boundary:
  - tifffile for TIFF (battle-tested, ~300 ms for 24 MP 16-bit write).
  - OpenEXR (capital-O, the ASWF PyPI binding) for EXR. Not pyexr, not
    imageio's EXR plugin — the spec is explicit. PIZ compression at
    default level.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

# --- Color-space transforms (D50→D65, ProPhoto→Rec.2020) -------------------


def _prophoto_to_rec2020(prophoto_d50: np.ndarray) -> np.ndarray:
    """Linear ProPhoto(D50) → linear Rec.2020(D65). Bradford CAT for D50→D65
    chromatic adaptation, then ProPhoto→Rec.2020 via XYZ.

    Computed via `colour.RGB_to_RGB` with an explicit Bradford CAT.
    """
    import colour

    h, w, _ = prophoto_d50.shape
    out = colour.RGB_to_RGB(
        prophoto_d50.reshape(-1, 3).astype(np.float64),
        input_colourspace="ProPhoto RGB",
        output_colourspace="ITU-R BT.2020",
        chromatic_adaptation_transform="Bradford",
        apply_cctf_decoding=False,
        apply_cctf_encoding=False,
    )
    return out.reshape(h, w, 3).astype(np.float32)


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
        # Tag clip as Rec.2020 input downstream (Resolve color-management),
        # not embedded ICC — Resolve's interpretation comes from the project
        # config, not the file metadata.
    )
    return dst


# --- EXR writer ------------------------------------------------------------


def write_exr_linear_rec2020(
    prophoto: np.ndarray, dst: Path | str,
) -> Path:
    """Write 32-bit float EXR, linear Rec.2020(D65), PIZ-compressed.

    Resolve reads PIZ; ZIP1 is also supported but PIZ matches the cinema
    ingest default. No tone mapping — the data is HDR-clean linear, the
    grading happens downstream.
    """
    import OpenEXR

    rec2020 = _prophoto_to_rec2020(prophoto).astype(np.float32)
    h, w, _ = rec2020.shape
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    # OpenEXR (ASWF binding) write API:
    #   header = {"compression": OpenEXR.PIZ_COMPRESSION,
    #             "type": OpenEXR.scanlineimage}
    #   channels = {"R": np.float32 (H, W), "G": ..., "B": ...}
    #   with OpenEXR.File(header, channels) as f: f.write(dst)
    #
    # Channels MUST be C-contiguous arrays. Passing strided views of the
    # interleaved (H, W, 3) source (e.g. `rec2020[..., 0]`) silently
    # produces garbled per-channel data on real-sized renders — the binding
    # reads with a tight stride assumption. `np.ascontiguousarray` forces
    # a per-channel copy.
    header = {
        "compression": OpenEXR.PIZ_COMPRESSION,
        "type": OpenEXR.scanlineimage,
    }
    channels = {
        "R": np.ascontiguousarray(rec2020[..., 0]),
        "G": np.ascontiguousarray(rec2020[..., 1]),
        "B": np.ascontiguousarray(rec2020[..., 2]),
    }
    with OpenEXR.File(header, channels) as exr:
        exr.write(str(dst))
    return dst


# --- Preset dispatch -------------------------------------------------------


def write_preset_output(
    prophoto: np.ndarray, dst_stem: Path | str, preset: str,
) -> Path:
    """Dispatch to the right writer based on preset name.

    `dst_stem` is the path WITHOUT extension; the writer appends .tif / .exr.
    Returns the final written path.

    `stills-finished` raises NotImplementedError in v0.6 — AgX port is
    a v0.6.x follow-up per the architecture spec.
    """
    dst_stem = Path(dst_stem)
    if preset == "cinema-linear":
        return write_tiff_linear_rec2020(prophoto, dst_stem.with_suffix(".tif"))
    if preset == "cinema-aces":
        return write_exr_linear_rec2020(prophoto, dst_stem.with_suffix(".exr"))
    if preset == "stills-finished":
        raise NotImplementedError(
            "stills-finished preset (Rec.2020 + AgX) is deferred to v0.6.x. "
            "Use cinema-linear or cinema-aces in v0.6."
        )
    raise ValueError(
        f"Unknown preset {preset!r}. Valid: cinema-linear, cinema-aces, "
        f"stills-finished (v0.6.x)."
    )
