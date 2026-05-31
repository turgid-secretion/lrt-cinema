"""Adobe DNG Camera Profile (DCP) reader + kelvin-to-multipliers math.

Closes the "lrt-cinema's render diverges from LR / LRT preview" gap by
giving us access to the same color-pipeline knobs LR uses internally:
the camera-specific color matrices, the baseline-exposure bias, and
the bundled tone curve.

A DCP file is a standard TIFF/IFD container (Adobe DNG 1.7.1 §"Camera
Profile Format"). It carries:

    ColorMatrix1 / ColorMatrix2          XYZ(D50) → camera RGB,
                                         tabulated at two illuminants.
    ForwardMatrix1 / ForwardMatrix2      camera RGB → XYZ(D50),
                                         tabulated at two illuminants.
    CalibrationIlluminant1/2             EXIF light-source codes (e.g.
                                         17 = Standard A, 21 = D65)
                                         identifying which illuminants
                                         the matrices were calibrated
                                         at.
    BaselineExposureOffset               additive EV bias folded into
                                         TotalBaselineExposure alongside
                                         DNG.BaselineExposure (per Adobe
                                         DNG SDK dng_negative.cpp:2588-2606
                                         — DCP.BaselineExposure tag 50730 is
                                         spec-permitted but Adobe's writer
                                         never emits it, and the SDK's
                                         TotalBE formula does not consume it).
    ProfileToneCurve                     N×2 (x,y) tone-curve LR
                                         applies as part of the
                                         "Camera Standard" / etc.
                                         look.

This module:

    1. Parses a DCP via a small clean-room IFD reader (no Adobe SDK at
       runtime — DCP files are 100-300 KB of plain TIFF; no need for
       a full TIFF lib).
    2. Implements the Robertson (1968) kelvin↔CIE-1960-UV conversion
       used by Adobe's DNG SDK (`dng_temperature.cpp`) to map a target
       kelvin+tint to a white point.
    3. Implements the DNG SDK's iterative interpolation algorithm that
       picks the right blend between ColorMatrix1/2 for a given target
       white point.
    4. Inverts the resulting camera-XYZ matrix at the target white to
       yield the per-channel camera-RGB multipliers darktable's
       `temperature` module wants (R, G1, B, G2 with G1=G2 for Bayer).

References
----------
- DNG 1.7.1 spec, §"Camera Profile Format" and §"Mapping Camera Color
  Space to a Pre-Defined Color Space"
  <https://helpx.adobe.com/camera-raw/digital-negative.html>
- DNG SDK 1.7.1, `dng_temperature.cpp` (Robertson tables + Set_xy_coord /
  Set_Temperature methods). The Robertson table below is reproduced
  from Wyszecki & Stiles, *Color Science*, 2nd ed., Table 5.4(3).
- colour-hdri.models.dng `xy_to_camera_neutral` iterative solver
  <https://github.com/colour-science/colour-hdri>
"""

from __future__ import annotations

import os
import struct
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

import numpy as np

# ---------------------------------------------------------------------------
# TIFF IFD0 tags used by the RAW-side EXIF probe (TIFF 6.0)
# ---------------------------------------------------------------------------

_TAG_MAKE = 271
_TAG_MODEL = 272

# ---------------------------------------------------------------------------
# DCP IFD tags (DNG 1.7.1)
# ---------------------------------------------------------------------------

_TAG_COLOR_MATRIX_1 = 50721
_TAG_COLOR_MATRIX_2 = 50722
_TAG_CAMERA_CALIBRATION_1 = 50723
_TAG_CAMERA_CALIBRATION_2 = 50724
# CalibrationIlluminant1/2 — canonical DNG 1.7.1 tag IDs (verified against
# RawTherapee dcp.cc:856-857, LibRaw tiff.cpp:1427-1431, darktable
# imageio_dng.c#L606 at SHA 9402c65275, and the real Nikon D750
# Camera Standard.dcp on the dev machine). Prior code used 50931/50932 — wrong;
# 50932 on the D750 DCP is an unrelated ASCII metadata string ("com.adobe").
# The mismatch was harmless because Adobe DCPs leave the canonical tags at
# 17 (Standard A) / 21 (D65) and the existing "both zero → A/D65 fallback" at
# parse_dcp coincidentally produced the same kelvin pair (2856/6504); the
# fix lets the parser actually read non-default vendor profiles correctly.
_TAG_CALIBRATION_ILLUMINANT_1 = 50778
_TAG_CALIBRATION_ILLUMINANT_2 = 50779
_TAG_PROFILE_NAME = 50936
_TAG_PROFILE_TONE_CURVE = 50940
_TAG_FORWARD_MATRIX_1 = 50964
_TAG_FORWARD_MATRIX_2 = 50965
_TAG_BASELINE_EXPOSURE_OFFSET = 51109

# DCP HueSatMap (DNG 1.7.1 §"Hue Sat Map"). Applied BEFORE
# BaselineExposureOffset in Adobe's pipeline. Two cubes (per-illuminant); the
# kelvin-driven mired blend selects per-cell between Data1 and Data2.
_TAG_PROFILE_HUE_SAT_MAP_DIMS = 50937
_TAG_PROFILE_HUE_SAT_MAP_DATA_1 = 50938
_TAG_PROFILE_HUE_SAT_MAP_DATA_2 = 50939
_TAG_PROFILE_HUE_SAT_MAP_ENCODING = 51107

# DCP LookTable (DNG 1.7.1 §"Profile Look Table"). Binary-identical algorithm
# to HueSatMap; Adobe applies it AFTER BaselineExposureOffset, BEFORE the
# ProfileToneCurve. Single cube (no per-illuminant variant). For the project's
# test camera (Nikon D750 Camera Standard.dcp) this is the only HSV cube
# present, and is the source of the ΔE post-fit 2.24 structural residual the
# diagnostic flagged on DSC_4053.
_TAG_PROFILE_LOOK_TABLE_DIMS = 50981
_TAG_PROFILE_LOOK_TABLE_DATA = 50982
_TAG_PROFILE_LOOK_TABLE_ENCODING = 51108

# TIFF type IDs (TIFF 6.0)
_TYPE_BYTE = 1
_TYPE_ASCII = 2
_TYPE_SHORT = 3
_TYPE_LONG = 4
_TYPE_RATIONAL = 5
_TYPE_SBYTE = 6
_TYPE_UNDEFINED = 7
_TYPE_SSHORT = 8
_TYPE_SLONG = 9
_TYPE_SRATIONAL = 10
_TYPE_FLOAT = 11
_TYPE_DOUBLE = 12

_TYPE_SIZES = {
    _TYPE_BYTE: 1, _TYPE_ASCII: 1, _TYPE_SHORT: 2, _TYPE_LONG: 4,
    _TYPE_RATIONAL: 8, _TYPE_SBYTE: 1, _TYPE_UNDEFINED: 1,
    _TYPE_SSHORT: 2, _TYPE_SLONG: 4, _TYPE_SRATIONAL: 8,
    _TYPE_FLOAT: 4, _TYPE_DOUBLE: 8,
}


# ---------------------------------------------------------------------------
# EXIF light-source codes → kelvin (EXIF 2.31 §4.6.5 + DNG SDK convention)
# ---------------------------------------------------------------------------
#
# DNG SDK's dng_camera_profile.cpp::IlluminantToTemperature() maps these
# CalibrationIlluminant codes to color temperatures. Codes 0 and >255
# fall back to D55 per the spec.

_ILLUMINANT_TO_KELVIN = {
    1: 5500.0,   # Daylight
    2: 4000.0,   # Fluorescent
    3: 3200.0,   # Tungsten (incandescent)
    4: 5500.0,   # Flash
    9: 5500.0,   # Fine weather
    10: 6500.0,  # Cloudy weather
    11: 7500.0,  # Shade
    12: 4150.0,  # Daylight fluorescent (D, 5700-7100K) -- DNG uses mid
    13: 3800.0,  # Day white fluorescent (N, 4600-5400K)
    14: 3450.0,  # Cool white fluorescent (W, 3900-4500K)
    15: 2900.0,  # White fluorescent (WW, 3200-3700K)
    17: 2856.0,  # Standard light A (incandescent tungsten)
    18: 4874.0,  # Standard light B (noon sunlight)
    19: 6774.0,  # Standard light C (avg daylight)
    20: 5500.0,  # D55
    21: 6504.0,  # D65
    22: 7504.0,  # D75
    23: 5003.0,  # D50
    24: 3200.0,  # ISO studio tungsten
}


def illuminant_code_to_kelvin(code: int) -> float:
    """Map an EXIF CalibrationIlluminant code to its nominal kelvin temperature."""
    return _ILLUMINANT_TO_KELVIN.get(code, 5500.0)


# ---------------------------------------------------------------------------
# DCP HSV-cube dataclass (HueSatMap + LookTable share this layout)
# ---------------------------------------------------------------------------

@dataclass
class HsvCube:
    """A DCP HSV-cube transformation — covers HueSatMap and LookTable.

    Both tags decode to the same shape (hue × sat × val × {hueShift_deg,
    satScale, valScale}) and run through the same trilinear-sampling
    algorithm (RawTherapee `dcp.cc::hsdApply` at L2013-2133 — used for
    both). Differences are pipeline position (Adobe applies HSM before
    BaselineExposureOffset, LookTable after) and Data2 presence (HSM has
    per-illuminant variants; LookTable is single-illuminant).

    Cube array shape: (val_divisions, hue_divisions, sat_divisions, 3) —
    the order matches DNG 1.7.1 §"Hue Sat Map" cell traversal
    (value-major, hue-medium, sat-minor) so reshape from the raw float
    sequence is direct.

    `srgb_gamma=True` means the cube's V axis was authored against an
    sRGB-gamma-ENCODED V (`HueSatMapEncoding`/`LookTableEncoding` = 1
    per DNG 1.7.1 §"Hue Sat Map Encoding"). The baker must OETF-encode
    the linear V into perceptual space before indexing the cube and
    EOTF-decode after applying valScale.
    """

    hue_divisions: int
    sat_divisions: int
    val_divisions: int
    srgb_gamma: bool
    data_1: np.ndarray
    data_2: np.ndarray | None = None

    @property
    def cell_count(self) -> int:
        return self.hue_divisions * self.sat_divisions * self.val_divisions


# ---------------------------------------------------------------------------
# DCP profile dataclass
# ---------------------------------------------------------------------------

@dataclass
class DCPProfile:
    """Parsed Adobe DCP profile.

    Matrices are 3x3 numpy arrays. The `*_1` variant corresponds to
    `calibration_illuminant_1` (usually the cooler of the two — Standard
    A 2856K is typical) and `*_2` to `calibration_illuminant_2` (usually
    D65 6504K). For single-illuminant profiles only `*_1` is populated.

    Color matrices map XYZ → camera RGB. Forward matrices map
    camera RGB → XYZ(D50). Camera calibration matrices are per-camera
    fine-tuning applied to the color matrix (rarely used in
    consumer-grade DCPs); when absent they default to identity.
    """

    profile_name: str = ""

    color_matrix_1: np.ndarray | None = None
    color_matrix_2: np.ndarray | None = None
    forward_matrix_1: np.ndarray | None = None
    forward_matrix_2: np.ndarray | None = None
    camera_calibration_1: np.ndarray | None = None
    camera_calibration_2: np.ndarray | None = None

    calibration_illuminant_1: int = 0
    calibration_illuminant_2: int = 0

    # Per Adobe DNG SDK dng_image_writer.cpp:2658, the DCP writer emits
    # tcBaselineExposureOffset (51109) but never tcBaselineExposure (50730);
    # likewise dng_negative.cpp:2588-2606 computes TotalBaselineExposure as
    # DNG.BaselineExposure + DCP.BaselineExposureOffset — no DCP.BE term.
    # So the DCP `BaselineExposure` field is intentionally absent here.
    baseline_exposure_offset: float = 0.0

    # Tone curve as Nx2 array of (x, y) in 0..1. None if not present.
    profile_tone_curve: np.ndarray | None = None

    # DCP HSV-cube transformations (DNG 1.7.1 §"Hue Sat Map", §"Profile
    # Look Table"). Both are HsvCube instances. HSM has Data2; LookTable
    # never does. For Nikon D750 Camera Standard.dcp on the dev machine,
    # `hue_sat_map` is None and `look_table` is the 90×16×16 cube that
    # produces the ΔE post-fit ~2.24 structural residual.
    hue_sat_map: HsvCube | None = None
    look_table: HsvCube | None = None

    # Cached calibration kelvin (set after parse).
    kelvin_1: float = field(default=0.0)
    kelvin_2: float = field(default=0.0)


# ---------------------------------------------------------------------------
# IFD parsing
# ---------------------------------------------------------------------------

def _read_value(
    f: BinaryIO,
    tag_type: int,
    count: int,
    value_or_offset_bytes: bytes,
    byte_order: str,
) -> bytes:
    """Read raw bytes for an IFD entry's payload.

    TIFF inlines values that fit in 4 bytes; larger values live at the
    offset that occupies those 4 bytes. We always return raw bytes; the
    caller decodes per `tag_type`.

    Rejects oversized counts as malformed: a hostile (or just garbage)
    DCP/RAW can claim count=2^31 for any tag type, causing `f.read(size)`
    to allocate billions of bytes — MemoryError-or-OOM at parse time
    regardless of the file's actual size. 16 MiB is strictly larger than
    any legitimate single-tag payload (DCP HSV cubes top out around
    270 KiB at 90×16×16 floats; LookTables similarly bounded; ASCII
    strings are <64 B). Larger → malformed → ValueError.
    """
    type_size = _TYPE_SIZES.get(tag_type, 1)
    if count < 0:
        raise ValueError(f"malformed TIFF/DCP: negative IFD-entry count {count}")
    if count > _MAX_IFD_ENTRY_COUNT // max(1, type_size):
        raise ValueError(
            f"malformed TIFF/DCP: IFD entry payload {type_size} * {count} "
            f"= {type_size * count} bytes exceeds the {_MAX_IFD_ENTRY_COUNT}-"
            f"byte cap. A real DCP/RAW tag never approaches this size."
        )
    size = type_size * count
    if size <= 4:
        return value_or_offset_bytes[:size]
    offset = struct.unpack(f"{byte_order}I", value_or_offset_bytes)[0]
    pos = f.tell()
    f.seek(offset)
    data = f.read(size)
    f.seek(pos)
    return data


# 16 MiB IFD-entry payload cap. Bounded above any legitimate tag (DCP HSV
# cubes are ~270 KB max; LookTables similar; ASCII strings <64 B). Below
# this, malformed-input parses fail fast with ValueError instead of
# attempting a multi-GB read on a fabricated count field.
_MAX_IFD_ENTRY_COUNT = 16 * 1024 * 1024


def _decode_srational(data: bytes, count: int, byte_order: str) -> list[float]:
    out = []
    for i in range(count):
        num, den = struct.unpack(f"{byte_order}ii", data[i * 8:(i + 1) * 8])
        out.append(num / den if den != 0 else 0.0)
    return out


def _decode_rational(data: bytes, count: int, byte_order: str) -> list[float]:
    out = []
    for i in range(count):
        num, den = struct.unpack(f"{byte_order}II", data[i * 8:(i + 1) * 8])
        out.append(num / den if den != 0 else 0.0)
    return out


def _decode_floats(data: bytes, count: int, byte_order: str) -> list[float]:
    return list(struct.unpack(f"{byte_order}{count}f", data))


def _decode_ascii(data: bytes) -> str:
    return data.rstrip(b"\x00").decode("ascii", errors="replace")


def _decode_short(data: bytes, count: int, byte_order: str) -> list[int]:
    return list(struct.unpack(f"{byte_order}{count}H", data[:count * 2]))


def _decode_long(data: bytes, count: int, byte_order: str) -> list[int]:
    return list(struct.unpack(f"{byte_order}{count}I", data[:count * 4]))


def parse_dcp(path: Path) -> DCPProfile:
    """Parse a DCP file into a DCPProfile.

    DCPs use the TIFF/IFD container format. We read IFD0 only — DCPs
    have a single IFD by spec (DNG 1.7.1 §"Camera Profile Format").

    Raises ValueError on malformed input (bad magic, truncated header,
    short read mid-IFD). A bare struct.error from a truncated DCP would
    surface to callers as an opaque traceback; rewrapping it here keeps
    DCP-error handling uniform on the `(FileNotFoundError, ValueError)`
    contract every callsite already catches.
    """
    path = Path(path)
    try:
        return _parse_dcp_impl(path)
    except struct.error as exc:
        raise ValueError(
            f"{path}: malformed DCP (struct unpack failed: {exc})"
        ) from exc


def _parse_dcp_impl(path: Path) -> DCPProfile:
    with open(path, "rb") as f:
        header = f.read(8)
        if header[:2] == b"II":
            byte_order = "<"
        elif header[:2] == b"MM":
            byte_order = ">"
        else:
            raise ValueError(f"{path}: not a TIFF/DCP (bad byte-order marker)")
        magic = struct.unpack(f"{byte_order}H", header[2:4])[0]
        # Adobe DCP files use magic 0x4352 ("CR" = Camera Raw) instead of
        # the standard TIFF magic 42 (0x002A). Both are valid containers
        # for the DCP IFD structure; we accept either.
        if magic not in (42, 0x4352):
            raise ValueError(f"{path}: bad TIFF/DCP magic ({magic}, expected 42 or 0x4352)")
        ifd0_offset = struct.unpack(f"{byte_order}I", header[4:8])[0]

        f.seek(ifd0_offset)
        n_entries = struct.unpack(f"{byte_order}H", f.read(2))[0]

        prof = DCPProfile()
        # HSV-cube fields are deferred — Dims/Data/Encoding tags may appear
        # in any IFD order, so we collect raw decoded values during the loop
        # and resolve them to HsvCube instances afterward.
        hsm_dims: list[int] | None = None
        hsm_enc: int = 0
        hsm_data1: list[float] | None = None
        hsm_data2: list[float] | None = None
        lt_dims: list[int] | None = None
        lt_enc: int = 0
        lt_data: list[float] | None = None
        for _ in range(n_entries):
            entry = f.read(12)
            tag, tag_type, count = struct.unpack(f"{byte_order}HHI", entry[:8])
            value_or_offset = entry[8:12]
            data = _read_value(f, tag_type, count, value_or_offset, byte_order)

            if tag == _TAG_PROFILE_NAME and tag_type == _TYPE_ASCII:
                prof.profile_name = _decode_ascii(data)
            elif tag == _TAG_COLOR_MATRIX_1 and tag_type == _TYPE_SRATIONAL and count == 9:
                prof.color_matrix_1 = np.asarray(
                    _decode_srational(data, 9, byte_order)
                ).reshape(3, 3)
            elif tag == _TAG_COLOR_MATRIX_2 and tag_type == _TYPE_SRATIONAL and count == 9:
                prof.color_matrix_2 = np.asarray(
                    _decode_srational(data, 9, byte_order)
                ).reshape(3, 3)
            elif tag == _TAG_FORWARD_MATRIX_1 and tag_type == _TYPE_SRATIONAL and count == 9:
                prof.forward_matrix_1 = np.asarray(
                    _decode_srational(data, 9, byte_order)
                ).reshape(3, 3)
            elif tag == _TAG_FORWARD_MATRIX_2 and tag_type == _TYPE_SRATIONAL and count == 9:
                prof.forward_matrix_2 = np.asarray(
                    _decode_srational(data, 9, byte_order)
                ).reshape(3, 3)
            elif tag == _TAG_CAMERA_CALIBRATION_1 and tag_type == _TYPE_SRATIONAL and count == 9:
                prof.camera_calibration_1 = np.asarray(
                    _decode_srational(data, 9, byte_order)
                ).reshape(3, 3)
            elif tag == _TAG_CAMERA_CALIBRATION_2 and tag_type == _TYPE_SRATIONAL and count == 9:
                prof.camera_calibration_2 = np.asarray(
                    _decode_srational(data, 9, byte_order)
                ).reshape(3, 3)
            elif tag == _TAG_CALIBRATION_ILLUMINANT_1 and tag_type == _TYPE_SHORT:
                prof.calibration_illuminant_1 = _decode_short(data, 1, byte_order)[0]
            elif tag == _TAG_CALIBRATION_ILLUMINANT_2 and tag_type == _TYPE_SHORT:
                prof.calibration_illuminant_2 = _decode_short(data, 1, byte_order)[0]
            elif tag == _TAG_BASELINE_EXPOSURE_OFFSET and tag_type == _TYPE_SRATIONAL:
                prof.baseline_exposure_offset = _decode_srational(data, 1, byte_order)[0]
            elif (
                tag == _TAG_PROFILE_TONE_CURVE
                and tag_type == _TYPE_FLOAT
                and count % 2 == 0
            ):
                floats = _decode_floats(data, count, byte_order)
                prof.profile_tone_curve = np.asarray(floats).reshape(count // 2, 2)
            # HueSatMap (two illuminants — same algorithm + layout as
            # LookTable below, just different tag IDs and pipeline position).
            elif tag == _TAG_PROFILE_HUE_SAT_MAP_DIMS and count == 3:
                # DNG spec mandates SHORT (type 3) but accept LONG (type 4)
                # for symmetry with the LookTable-Dims Adobe-non-spec case.
                if tag_type == _TYPE_SHORT:
                    hsm_dims = _decode_short(data, 3, byte_order)
                elif tag_type == _TYPE_LONG:
                    hsm_dims = _decode_long(data, 3, byte_order)
            elif tag == _TAG_PROFILE_HUE_SAT_MAP_ENCODING and tag_type == _TYPE_LONG:
                hsm_enc = _decode_long(data, 1, byte_order)[0]
            elif tag == _TAG_PROFILE_HUE_SAT_MAP_DATA_1 and tag_type == _TYPE_FLOAT:
                hsm_data1 = _decode_floats(data, count, byte_order)
            elif tag == _TAG_PROFILE_HUE_SAT_MAP_DATA_2 and tag_type == _TYPE_FLOAT:
                hsm_data2 = _decode_floats(data, count, byte_order)
            # LookTable (single cube, same algorithm). Adobe's real DCPs
            # emit LookTableDims as type=4 LONG (count=3) — NOT the
            # spec-mandated type=3 SHORT. Verified empirically against the
            # Nikon D750 Camera Standard.dcp (LookTableDims is type=4
            # LONG, value (90, 16, 16)). Accept both forms; fail silent on
            # neither (cube simply not loaded).
            elif tag == _TAG_PROFILE_LOOK_TABLE_DIMS and count == 3:
                if tag_type == _TYPE_LONG:
                    lt_dims = _decode_long(data, 3, byte_order)
                elif tag_type == _TYPE_SHORT:
                    lt_dims = _decode_short(data, 3, byte_order)
            elif tag == _TAG_PROFILE_LOOK_TABLE_ENCODING and tag_type == _TYPE_LONG:
                lt_enc = _decode_long(data, 1, byte_order)[0]
            elif tag == _TAG_PROFILE_LOOK_TABLE_DATA and tag_type == _TYPE_FLOAT:
                lt_data = _decode_floats(data, count, byte_order)

    # Resolve deferred HSV-cube tags now that the IFD walk is complete.
    if hsm_dims is not None and hsm_data1 is not None:
        prof.hue_sat_map = _build_hsv_cube(hsm_dims, hsm_enc, hsm_data1, hsm_data2)
    if lt_dims is not None and lt_data is not None:
        prof.look_table = _build_hsv_cube(lt_dims, lt_enc, lt_data, None)

    if prof.color_matrix_1 is None:
        raise ValueError(f"{path}: missing ColorMatrix1 — not a valid DCP")

    # Adobe-generated DCPs frequently set both CalibrationIlluminant fields
    # to 0 (Unknown) — observed empirically across the Adobe-shipped Nikon
    # D750 profile family. When both are 0 AND both ColorMatrix entries are
    # populated, fall back to Adobe DNG SDK convention: ColorMatrix1 is the
    # warmer-illuminant matrix (Standard Illuminant A, 2856 K), ColorMatrix2
    # is the cooler-illuminant matrix (D65, 6504 K). This matches what
    # `dng_validate -tif` produces against the same DCP and the
    # colour-hdri.models.dng test fixtures.
    if (
        prof.calibration_illuminant_1 == 0
        and prof.calibration_illuminant_2 == 0
        and prof.color_matrix_2 is not None
    ):
        prof.kelvin_1 = 2856.0
        prof.kelvin_2 = 6504.0
    else:
        prof.kelvin_1 = illuminant_code_to_kelvin(prof.calibration_illuminant_1)
        prof.kelvin_2 = illuminant_code_to_kelvin(prof.calibration_illuminant_2)
    return prof


# ---------------------------------------------------------------------------
# Robertson (1968) kelvin ↔ CIE 1960 UCS
# ---------------------------------------------------------------------------
#
# Reproduced from Wyszecki & Stiles, *Color Science*, 2nd ed., Table 5.4(3).
# Tuples are (mired, u, v, slope) where slope is dv/du for the
# perpendicular line at that mired value used for tint offset.
# Identical (within rounding) to the table in DNG SDK's
# `dng_temperature.cpp` (kTempTable).

_ROBERTSON_RUVT: tuple[tuple[float, float, float, float], ...] = (
    (0,   0.18006, 0.26352, -0.24341),
    (10,  0.18066, 0.26589, -0.25479),
    (20,  0.18133, 0.26846, -0.26876),
    (30,  0.18208, 0.27119, -0.28539),
    (40,  0.18293, 0.27407, -0.30470),
    (50,  0.18388, 0.27709, -0.32675),
    (60,  0.18494, 0.28021, -0.35156),
    (70,  0.18611, 0.28342, -0.37915),
    (80,  0.18740, 0.28668, -0.40955),
    (90,  0.18880, 0.28997, -0.44278),
    (100, 0.19032, 0.29326, -0.47888),
    (125, 0.19462, 0.30141, -0.58204),
    (150, 0.19962, 0.30921, -0.70471),
    (175, 0.20525, 0.31647, -0.84901),
    (200, 0.21142, 0.32312, -1.0182),
    (225, 0.21807, 0.32909, -1.2168),
    (250, 0.22511, 0.33439, -1.4512),
    (275, 0.23247, 0.33904, -1.7298),
    (300, 0.24010, 0.34308, -2.0637),
    (325, 0.24702, 0.34655, -2.4681),
    (350, 0.25591, 0.34951, -2.9641),
    (375, 0.26400, 0.35200, -3.5814),
    (400, 0.27218, 0.35407, -4.3633),
    (425, 0.28039, 0.35577, -5.3762),
    (450, 0.28863, 0.35714, -6.7262),
    (475, 0.29685, 0.35823, -8.5955),
    (500, 0.30505, 0.35907, -11.324),
    (525, 0.31320, 0.35968, -15.628),
    (550, 0.32129, 0.36011, -23.325),
    (575, 0.32931, 0.36038, -40.770),
    (600, 0.33724, 0.36051, -116.45),
)


def kelvin_tint_to_xy(kelvin: float, tint: float = 0.0) -> tuple[float, float]:
    """Convert a target kelvin + tint to CIE 1931 (x, y) white point.

    Implements DNG SDK's `dng_temperature::Set_Temperature` (port of
    Robertson 1968). The tint is a perpendicular offset to the
    Planckian locus in the CIE 1960 (u, v) UCS, in DNG SDK's
    tint-axis-scale-by-3000 convention.
    """
    if kelvin < 1666.7 or kelvin > 1e6:
        raise ValueError(f"kelvin {kelvin} outside [1666.7, 1e6]")
    mired = 1e6 / kelvin

    # Find bracketing table indices.
    for i in range(1, len(_ROBERTSON_RUVT)):
        if mired <= _ROBERTSON_RUVT[i][0] or i == len(_ROBERTSON_RUVT) - 1:
            break
    lo = _ROBERTSON_RUVT[i - 1]
    hi = _ROBERTSON_RUVT[i]

    f = (hi[0] - mired) / (hi[0] - lo[0])
    f = max(0.0, min(1.0, f))

    u_locus = f * lo[1] + (1 - f) * hi[1]
    v_locus = f * lo[2] + (1 - f) * hi[2]

    # Tint offset along the perpendicular to the locus at that mired.
    # DNG SDK applies tint after dividing by an "Anti_Tint_Scale" of
    # 3000 — i.e. the user-facing tint=±150 ≈ 5% of locus orthogonal
    # length on the (u, v) plane. We follow the same convention.
    slope_lo = 1.0 / np.sqrt(1.0 + lo[3] * lo[3])
    slope_hi = 1.0 / np.sqrt(1.0 + hi[3] * hi[3])
    du_lo, dv_lo = slope_lo, lo[3] * slope_lo
    du_hi, dv_hi = slope_hi, hi[3] * slope_hi
    du = f * du_lo + (1 - f) * du_hi
    dv = f * dv_lo + (1 - f) * dv_hi
    norm = np.sqrt(du * du + dv * dv)
    du, dv = du / norm, dv / norm

    offset = tint / 3000.0
    u = u_locus + du * offset
    v = v_locus + dv * offset

    # Convert CIE 1960 (u, v) → CIE 1931 (x, y) per the standard
    # identities (see e.g. Wyszecki & Stiles §3.3 or
    # https://en.wikipedia.org/wiki/CIE_1960_color_space#Relation_to_CIE_XYZ).
    denom = 2.0 * u - 8.0 * v + 4.0
    x = 3.0 * u / denom
    y = 2.0 * v / denom
    return float(x), float(y)


def xy_to_uv(x: float, y: float) -> tuple[float, float]:
    """CIE 1931 (x, y) → CIE 1960 (u, v)."""
    denom = -2.0 * x + 12.0 * y + 3.0
    u = 4.0 * x / denom
    v = 6.0 * y / denom
    return u, v


def uv_to_kelvin(u: float, v: float) -> float:
    """CIE 1960 (u, v) → correlated color temperature in kelvin.

    Inverse Robertson: find the (mired, u, v, slope) row whose
    perpendicular line through the locus contains (u, v).
    """
    last_d = 0.0
    for i in range(1, len(_ROBERTSON_RUVT)):
        mired_i, u_i, v_i, slope_i = _ROBERTSON_RUVT[i]
        norm = 1.0 / np.sqrt(1.0 + slope_i * slope_i)
        d = (v - v_i - slope_i * (u - u_i)) * norm
        if i > 1 and d * last_d <= 0.0:
            # Sign change → interpolate between rows i-1 and i.
            mired_prev, u_prev, v_prev, slope_prev = _ROBERTSON_RUVT[i - 1]
            norm_prev = 1.0 / np.sqrt(1.0 + slope_prev * slope_prev)
            d_prev = (v - v_prev - slope_prev * (u - u_prev)) * norm_prev
            f = d_prev / (d_prev - d)
            mired = mired_prev + f * (mired_i - mired_prev)
            return 1e6 / max(mired, 1e-9)
        last_d = d
    # Fallback: extreme cool or hot — return endpoint.
    return 1e6 / _ROBERTSON_RUVT[-1][0]


# ---------------------------------------------------------------------------
# DCP color matrix → camera-neutral multipliers
# ---------------------------------------------------------------------------

def _build_hsv_cube(
    dims: list[int],
    encoding: int,
    data_1_floats: list[float],
    data_2_floats: list[float] | None,
) -> HsvCube:
    """Construct an HsvCube from the raw DNG-IFD payload.

    `dims` is the (hueDivisions, satDivisions, valDivisions) triple as
    decoded from the file. `encoding` is the HueSatMapEncoding /
    LookTableEncoding LONG (0 = linear V axis, 1 = sRGB-gamma-encoded V).
    `data_1_floats` / `data_2_floats` are flat lists of 3 ×
    hueDivisions × satDivisions × valDivisions IEEE floats; the reshape
    order (V, H, S, 3) matches the DNG spec's cell-traversal convention
    (value-major, hue-medium, sat-minor — see RT dcp.cc#L2088-L2091).
    """
    h_div, s_div, v_div = dims
    expected = 3 * h_div * s_div * v_div
    if len(data_1_floats) != expected:
        raise ValueError(
            f"DCP HSV cube size mismatch: dims={dims} → expected "
            f"{expected} floats, got {len(data_1_floats)}"
        )
    arr1 = np.asarray(data_1_floats, dtype=np.float32).reshape(
        v_div, h_div, s_div, 3
    )
    arr2: np.ndarray | None = None
    if data_2_floats is not None:
        if len(data_2_floats) != expected:
            raise ValueError(
                f"DCP HSV cube Data2 size {len(data_2_floats)} does not match "
                f"Data1 size {expected} — invalid DCP"
            )
        arr2 = np.asarray(data_2_floats, dtype=np.float32).reshape(
            v_div, h_div, s_div, 3
        )
    return HsvCube(
        hue_divisions=h_div,
        sat_divisions=s_div,
        val_divisions=v_div,
        srgb_gamma=bool(encoding & 1),
        data_1=arr1,
        data_2=arr2,
    )


def interpolate_hsv_cube(
    cube: HsvCube,
    kelvin: float,
    kelvin_1: float,
    kelvin_2: float,
) -> np.ndarray:
    """Per-cell mired-linear blend of an HsvCube's Data1/Data2 arrays.

    LookTable cubes always have `data_2=None` and return `data_1` unchanged.
    HSM cubes blend per-corresponding-cell between the two calibration
    illuminants — identical math to `interpolate_color_matrix`, just on
    the full (V, H, S, 3) cube instead of a 3×3 matrix. Numpy broadcast
    handles the per-cell linear combination in one allocation.
    """
    # Either kelvin zero → can't compute mireds. Falls back to data_1
    # (matches the existing "no second illuminant" convention). Real DCPs
    # always have positive kelvins via illuminant_code_to_kelvin which
    # supplies 5500.0 as default; this guard catches manual construction
    # with kelvin_1=0 or a corrupted .npz where one side is zero.
    if cube.data_2 is None or kelvin_1 <= 0.0 or kelvin_2 <= 0.0:
        return cube.data_1
    k_lo, k_hi = sorted([kelvin_1, kelvin_2])
    if kelvin_1 <= kelvin_2:
        c_lo, c_hi = cube.data_1, cube.data_2
    else:
        c_lo, c_hi = cube.data_2, cube.data_1
    if kelvin <= k_lo:
        return c_lo
    if kelvin >= k_hi:
        return c_hi
    inv_lo, inv_hi = 1.0 / k_lo, 1.0 / k_hi
    f = (1.0 / kelvin - inv_lo) / (inv_hi - inv_lo)
    return ((1.0 - f) * c_lo + f * c_hi).astype(np.float32)


def interpolate_color_matrix(profile: DCPProfile, kelvin: float) -> np.ndarray:
    """Interpolate ColorMatrix1/2 by kelvin per DNG SDK convention.

    DNG SDK 1.7.1's `dng_color_spec::InterpolateColorMatrix` uses an
    inverse-temperature (mired) blend between the two illuminants.
    For single-illuminant profiles, returns ColorMatrix1 unchanged.
    """
    if profile.color_matrix_2 is None or profile.kelvin_2 == 0.0:
        return profile.color_matrix_1
    k_lo, k_hi = sorted([profile.kelvin_1, profile.kelvin_2])
    if profile.kelvin_1 <= profile.kelvin_2:
        m_lo, m_hi = profile.color_matrix_1, profile.color_matrix_2
    else:
        m_lo, m_hi = profile.color_matrix_2, profile.color_matrix_1
    # Blend in mired (reciprocal temperature) space.
    if kelvin <= k_lo:
        return m_lo
    if kelvin >= k_hi:
        return m_hi
    inv = 1.0 / kelvin
    inv_lo, inv_hi = 1.0 / k_lo, 1.0 / k_hi
    f = (inv - inv_lo) / (inv_hi - inv_lo)
    return (1.0 - f) * m_lo + f * m_hi


def _xy_to_xyz(x: float, y: float) -> np.ndarray:
    """CIE (x, y) chromaticity → XYZ vector with Y = 1."""
    return np.array([x / y, 1.0, (1.0 - x - y) / y])


def xy_to_camera_neutral(profile: DCPProfile, x: float, y: float) -> np.ndarray:
    """Compute camera-RGB neutral for a given target white-point (x, y).

    Algorithm matches colour-hdri.models.dng `xy_to_camera_neutral`
    (BSD-3, Mansencal et al.), which is the canonical Python port of
    DNG SDK 1.7.1 `dng_color_spec::SetWhiteXY`:

        1. (x, y) → CIE 1960 uv → Robertson CCT
        2. interpolate ColorMatrix1/2 at that CCT (mired-linear blend)
        3. camera_neutral = ColorMatrix @ XYZ(x, y)

    Returns the un-normalized camera-RGB neutral vector. Caller normalizes
    (typically by dividing through so the green entry is 1).

    History: a prior version of this function carried a `for _ in
    range(10)` loop labeled "iterative because the color matrix depends
    on kelvin." The loop was dead — kelvin was computed once outside the
    loop and never updated, so np.allclose(matrix, matrix) was always
    True on iteration 1. Empirically the output exactly matches the
    colour-hdri reference (verified in test_xy_to_camera_neutral_matches
    _colour_hdri_reference), confirming the single-pass form is correct
    for the DCP shapes we target. The DNG SDK's iterative form matters
    only for non-Adobe vendor profiles with non-identity CameraCalibration
    matrices or non-unit AnalogBalance — both absent from every Adobe DCP
    shipped to date (including the bundled Nikon D750 Camera Standard).
    If the renderer ever needs to handle a custom profile with these
    fields populated, the iterative form would re-enter; that's a v0.5+
    concern documented as `CameraCalibration` / `AnalogBalance` support.
    """
    u, v = xy_to_uv(x, y)
    kelvin = uv_to_kelvin(u, v)
    matrix = interpolate_color_matrix(profile, kelvin)
    return matrix @ _xy_to_xyz(x, y)


# Linearized Bradford cone-response matrix (DNG SDK dng_color_spec.cpp:28).
_BRADFORD = np.array([
    [0.8951, 0.2664, -0.1614],
    [-0.7502, 1.7135, 0.0367],
    [0.0389, -0.0685, 1.0296],
])


def map_white_matrix(white1_xy: tuple[float, float], white2_xy: tuple[float, float]) -> np.ndarray:
    """Chromatic-adaptation matrix mapping XYZ at `white1` to XYZ at `white2`.

    Direct port of `MapWhiteMatrix` (DNG SDK dng_color_spec.cpp:22-57) — the
    linearized Bradford von-Kries adaptation Adobe uses inside the ColorMatrix
    render path. Per-cone scale ratios are pinned to [0.1, 10].
    """
    w1 = np.maximum(_BRADFORD @ _xy_to_xyz(*white1_xy), 0.0)
    w2 = np.maximum(_BRADFORD @ _xy_to_xyz(*white2_xy), 0.0)
    ratio = np.array([
        min(max(w2[i] / w1[i] if w1[i] > 0.0 else 10.0, 0.1), 10.0) for i in range(3)
    ])
    return np.linalg.inv(_BRADFORD) @ np.diag(ratio) @ _BRADFORD


def neutral_to_xy(profile: DCPProfile, neutral: np.ndarray) -> tuple[float, float]:
    """Camera-RGB neutral (AsShotNeutral) → scene-white CIE (x, y).

    Port of `dng_color_spec::NeutralToXY` (dng_color_spec.cpp:659) — the
    iterative inverse of `xy_to_camera_neutral`: repeatedly interpolate the
    ColorMatrix at the current white estimate and back-solve the chromaticity,
    until convergence. Used by the ColorMatrix (no-ForwardMatrix) render path.
    """
    n = np.asarray(neutral, dtype=np.float64)
    last = np.array([0.34567, 0.35850])  # D50 starting point
    for _ in range(30):
        kelvin = uv_to_kelvin(*xy_to_uv(last[0], last[1]))
        cm = interpolate_color_matrix(profile, kelvin)
        xyz = np.linalg.inv(cm) @ n
        s = xyz.sum()
        nxt = np.array([xyz[0] / s, xyz[1] / s])
        if abs(nxt[0] - last[0]) + abs(nxt[1] - last[1]) < 1e-7:
            return float(nxt[0]), float(nxt[1])
        last = nxt
    return float(last[0]), float(last[1])


def colormatrix_camera_to_pcs(
    profile: DCPProfile,
    neutral: np.ndarray,
    pcs_white_xy: tuple[float, float],
) -> np.ndarray:
    """Camera RGB → XYZ(D50 PCS) matrix via the ColorMatrix path WITH white adaptation.

    Port of the no-ForwardMatrix branch of `dng_color_spec::SetWhiteXY`
    (dng_color_spec.cpp:570-607). Required for Adobe Camera-Matching profiles
    whose ForwardMatrix is a ProPhoto passthrough (no colour information) and
    for any profile that ships only a ColorMatrix — the LookTable that follows
    is authored against THIS colour base, not a white-balance-only base.

        whiteXY      = NeutralToXY(neutral)
        CM           = interpolated ColorMatrix at whiteXY's CCT
        PCStoCamera  = CM @ MapWhiteMatrix(PCS_white, whiteXY)
        PCStoCamera /= max(PCStoCamera @ XYZ(PCS_white))   # white reaches on 1st-channel sat
        CameraToPCS  = inv(PCStoCamera)

    `pcs_white_xy` is the PCS (D50) chromaticity — pass the working-space
    (ProPhoto) white so this composes exactly with the XYZ(D50)→ProPhoto matrix
    used downstream. The naive `inv(ColorMatrix)` shortcut (no MapWhiteMatrix)
    maps neutral to the SCENE white instead of D50 and tints every neutral.
    """
    white_xy = neutral_to_xy(profile, neutral)
    kelvin = uv_to_kelvin(*xy_to_uv(*white_xy))
    cm = interpolate_color_matrix(profile, kelvin)
    pcs_to_camera = cm @ map_white_matrix(pcs_white_xy, white_xy)
    scale = float(np.max(pcs_to_camera @ _xy_to_xyz(*pcs_white_xy)))
    if scale == 0.0:
        raise ValueError("degenerate ColorMatrix: PCStoCamera maps PCS white to zero")
    pcs_to_camera = pcs_to_camera / scale
    return np.linalg.inv(pcs_to_camera)


# ---------------------------------------------------------------------------
# RAW EXIF Make/Model probe — drives the auto-detect path
# ---------------------------------------------------------------------------
#
# All RAW formats this project targets except Canon CR3 (ISO BMFF container) are
# TIFF-shaped (NEF, DNG, ARW, RW2, RAF, ORF, FFF). The TIFF header rules at
# IFD0 are stable across vendors; Make/Model live at the canonical tag IDs
# 271/272 (TIFF 6.0 §8). We use the same struct-based IFD walk we already do
# for DCPs in `parse_dcp` rather than pulling in a heavier dep (exifread,
# rawpy, exiftool).

def read_raw_make_model(raw_path: Path) -> tuple[str, str] | None:
    """Extract (make, model) ASCII strings from a TIFF-shaped RAW's IFD0.

    Returns None when:
      - file is not TIFF-shaped (e.g. Canon CR3 is QuickTime/ISO BMFF; in
        practice the caller falls back to `--dcp` for those formats)
      - file is unreadable or truncated
      - Make/Model tags are absent

    Make/Model are read as raw ASCII strings; Adobe's DCP filename
    convention is a separate normalization step (`adobe_make_for_camera`).
    """
    raw_path = Path(raw_path)
    try:
        with open(raw_path, "rb") as f:
            header = f.read(8)
            if len(header) < 8:
                return None
            if header[:2] == b"II":
                bo = "<"
            elif header[:2] == b"MM":
                bo = ">"
            else:
                return None
            magic = struct.unpack(f"{bo}H", header[2:4])[0]
            # Standard TIFF magic 42 (NEF/DNG/ARW/RW2/RAF/ORF/FFF). DCP's 0x4352
            # is intentionally NOT accepted here — DCPs are not RAW images.
            if magic != 42:
                return None
            ifd0_offset = struct.unpack(f"{bo}I", header[4:8])[0]
            f.seek(ifd0_offset)
            n_entries_data = f.read(2)
            if len(n_entries_data) < 2:
                return None
            n_entries = struct.unpack(f"{bo}H", n_entries_data)[0]

            make: str | None = None
            model: str | None = None
            for _ in range(n_entries):
                entry = f.read(12)
                if len(entry) < 12:
                    return None
                tag, tag_type, count = struct.unpack(f"{bo}HHI", entry[:8])
                if tag not in (_TAG_MAKE, _TAG_MODEL) or tag_type != _TYPE_ASCII:
                    continue
                value_or_offset = entry[8:12]
                data = _read_value(f, tag_type, count, value_or_offset, bo)
                decoded = _decode_ascii(data).strip()
                if tag == _TAG_MAKE:
                    make = decoded
                elif tag == _TAG_MODEL:
                    model = decoded
                if make is not None and model is not None:
                    break
            if make is None or model is None:
                return None
            return make, model
    except (OSError, struct.error):
        return None


# ---------------------------------------------------------------------------
# Camera Make/Model → extracted-profile filename label
# ---------------------------------------------------------------------------
#
# Extracted `.npz` profiles are named `<Make> <Model> <variant>.npz`; the
# convention mirrors Adobe's DCP filenames (e.g. "Nikon D750 Camera
# Standard.npz") so a one-shot extraction from a user's own DCP install yields
# predictable, auto-detectable names. Real LRT XMP carries
# `crs:CameraProfile="Camera Standard"` by default, so
# `find_extracted_profile_for_camera` prefers the Camera Standard variant over
# the Adobe Standard fallback — that matches what LR rendered the LRT preview
# with.

# EXIF Make → filename Make. Vendor strings are normalized from EXIF caps +
# corporate-suffix forms ("NIKON CORPORATION") to the friendly form ("Nikon").
# PENTAX is uppercase to match the established profile-filename convention.
_ADOBE_MAKE_NORMALIZE = {
    "NIKON CORPORATION": "Nikon",
    "NIKON": "Nikon",
    "CANON": "Canon",
    "SONY": "Sony",
    "FUJIFILM": "Fujifilm",
    "PANASONIC": "Panasonic",
    "OLYMPUS CORPORATION": "Olympus",
    "OLYMPUS IMAGING CORP.": "Olympus",
    "OM DIGITAL SOLUTIONS": "OM Digital Solutions",
    "RICOH IMAGING COMPANY, LTD.": "PENTAX",
    "PENTAX CORPORATION": "PENTAX",
    "PENTAX": "PENTAX",
    "LEICA CAMERA AG": "Leica",
    "SAMSUNG": "Samsung",
    "HASSELBLAD": "Hasselblad",
    "PHASE ONE": "Phase One",
    "APPLE": "Apple",
}


def adobe_make_for_camera(make: str) -> str:
    """Map an EXIF Make value to Adobe's DCP-filename Make convention."""
    return _ADOBE_MAKE_NORMALIZE.get(make.strip().upper(), make.strip().title())


def _adobe_camera_label(make: str, model: str) -> str:
    """Build the `<Make> <Model>` label used as the extracted-profile filename
    stem (the convention mirrors Adobe's DCP naming, e.g. "Nikon D750").

    A leading Make is stripped from Model when present. The strip is
    against the NORMALIZED Make (the first word, uppercased) so a NEF
    whose EXIF Make is "NIKON CORPORATION" and Model "NIKON D750" still
    yields label "Nikon D750" (Model's "NIKON" prefix matches the
    "NIKON" core of "NIKON CORPORATION").
    """
    norm_make = adobe_make_for_camera(make)
    m = model.strip()
    # Strip the first-word prefix of Make from Model — handles both
    # "NIKON CORPORATION" + "NIKON D750" and "Canon" + "Canon EOS R5".
    make_head = make.strip().split()[0].upper() if make.strip() else ""
    if make_head and m.upper().startswith(make_head):
        m = m[len(make_head):].strip()
    label = f"{norm_make} {m}".strip()
    # Harden against EXIF→path-traversal (bug #8). This label is interpolated
    # into a filesystem path by `find_extracted_profile_for_camera`
    # (`<root>/<label> <variant>.npz`), and EXIF Make/Model is attacker-
    # controllable. Strip path separators and NUL so a hostile Model like
    # "x/../../../../etc/evil" cannot escape the profile search root: with these
    # removed the label is always a single path segment, and the appended
    # "<variant>.npz" suffix means it can never resolve to "." or "..". A mangled
    # label simply matches no profile → auto-detect returns None.
    return label.translate({ord("/"): None, ord("\\"): None, ord("\x00"): None})


# ---------------------------------------------------------------------------
# Extracted-profile format (.npz) — Adobe-free in-repo path
# ---------------------------------------------------------------------------
#
# Project-defined lossless serialization of the DCP fields the renderer
# actually consumes (matrices, illuminants, baseline-exposure offset, tone
# curve, HSV cubes). Storing extracted *data* — not Adobe's .dcp file
# format — is the project's stance on Adobe DCP redistribution per
# docs/research/KELVIN_MULTIPLIERS_RESEARCH.md.
#
# Format: numpy `.npz` (zip of zlib-compressed `.npy` arrays) with the
# field names listed in `_PROFILE_NPZ_FIELDS`. All numeric arrays are
# float32; calibration illuminant codes are int32; profile_name is a
# 0-d unicode string array. Optional fields are simply absent from the
# archive when the source DCP doesn't carry them.
#
# Version tag (`format_version`) is bumped only on backwards-incompatible
# changes — additive fields land at the next minor without a bump.

_PROFILE_FORMAT_VERSION = 1


def save_profile(profile: DCPProfile, path: Path) -> None:
    """Serialize a DCPProfile to lrt-cinema's `.npz` extracted format.

    Lossless w.r.t. the fields lrt-cinema's renderer consumes. Adobe DCPs
    carry additional metadata (UniqueCameraModel, ProfileCopyright, etc.)
    that the renderer doesn't use; those are NOT preserved by the
    extractor — re-extract from the source .dcp if more fields are ever
    needed.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arrays: dict[str, np.ndarray] = {
        "format_version": np.int32(_PROFILE_FORMAT_VERSION),
        # Coerce None → "" so the .npz roundtrip preserves "no name" as
        # the empty string the dataclass default uses. Without this,
        # np.array(None, dtype="U") saves the literal four-char string
        # "None", which load_profile would faithfully restore as a
        # bogus profile name. Real parse_dcp always sets profile_name
        # to "" or a real string; this catches manual-construction edge
        # cases per caveman-review PR #8 #7.
        "profile_name": np.array(profile.profile_name or "", dtype="U"),
        "calibration_illuminant_1": np.int32(profile.calibration_illuminant_1),
        "calibration_illuminant_2": np.int32(profile.calibration_illuminant_2),
        "kelvin_1": np.float32(profile.kelvin_1),
        "kelvin_2": np.float32(profile.kelvin_2),
        "baseline_exposure_offset": np.float32(profile.baseline_exposure_offset),
    }
    if profile.color_matrix_1 is not None:
        arrays["color_matrix_1"] = profile.color_matrix_1.astype(np.float32)
    if profile.color_matrix_2 is not None:
        arrays["color_matrix_2"] = profile.color_matrix_2.astype(np.float32)
    if profile.forward_matrix_1 is not None:
        arrays["forward_matrix_1"] = profile.forward_matrix_1.astype(np.float32)
    if profile.forward_matrix_2 is not None:
        arrays["forward_matrix_2"] = profile.forward_matrix_2.astype(np.float32)
    if profile.camera_calibration_1 is not None:
        arrays["camera_calibration_1"] = profile.camera_calibration_1.astype(np.float32)
    if profile.camera_calibration_2 is not None:
        arrays["camera_calibration_2"] = profile.camera_calibration_2.astype(np.float32)
    if profile.profile_tone_curve is not None:
        arrays["profile_tone_curve"] = profile.profile_tone_curve.astype(np.float32)
    if profile.hue_sat_map is not None:
        hsm = profile.hue_sat_map
        arrays["hsm_dims"] = np.array(
            [hsm.hue_divisions, hsm.sat_divisions, hsm.val_divisions], dtype=np.int32,
        )
        arrays["hsm_srgb_gamma"] = np.int32(1 if hsm.srgb_gamma else 0)
        arrays["hsm_data_1"] = hsm.data_1.astype(np.float32)
        if hsm.data_2 is not None:
            arrays["hsm_data_2"] = hsm.data_2.astype(np.float32)
    if profile.look_table is not None:
        lt = profile.look_table
        arrays["look_dims"] = np.array(
            [lt.hue_divisions, lt.sat_divisions, lt.val_divisions], dtype=np.int32,
        )
        arrays["look_srgb_gamma"] = np.int32(1 if lt.srgb_gamma else 0)
        arrays["look_data"] = lt.data_1.astype(np.float32)
    # Atomic write — a crashed or concurrent extract must not leave a
    # half-written archive that then crashes every render auto-detecting it
    # (load_profile would raise BadZipFile/KeyError). Write to a temp file in
    # the same dir, then os.replace (atomic on the same filesystem). Pass a file
    # object so np.savez_compressed does not re-append ".npz" to the temp name.
    fd, tmp = tempfile.mkstemp(suffix=".npz.tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            np.savez_compressed(fh, **arrays)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_profile(path: Path) -> DCPProfile:
    """Deserialize a DCPProfile from lrt-cinema's `.npz` extracted format.

    Honors the `(FileNotFoundError, ValueError)` error contract every callsite
    relies on (mirrors `parse_dcp`): a corrupt/incomplete `.npz` — missing key,
    bad zip, truncated archive — is rewrapped as ValueError rather than leaking a
    bare KeyError/BadZipFile that escapes the CLI's `--dcp` preflight. Raises
    ValueError on missing required fields, an unknown format-version, or a
    malformed archive; FileNotFoundError when the path is absent.
    """
    path = Path(path)
    try:
        return _load_profile_npz(path)
    except (FileNotFoundError, ValueError):
        raise  # already the documented contract types, with good messages
    except (KeyError, zipfile.BadZipFile, EOFError, OSError) as exc:
        raise ValueError(
            f"{path}: malformed extracted profile ({type(exc).__name__}: {exc})"
        ) from exc


def _load_profile_npz(path: Path) -> DCPProfile:
    with np.load(path, allow_pickle=False) as data:
        version = int(data["format_version"])
        if version != _PROFILE_FORMAT_VERSION:
            raise ValueError(
                f"{path}: unsupported profile format_version {version} "
                f"(this lrt-cinema build understands "
                f"format_version={_PROFILE_FORMAT_VERSION}). "
                f"Re-extract by running `tools/extract_dcp_library.py` "
                f"with the current version, or upgrade/downgrade "
                f"lrt-cinema to match the file's version."
            )
        if "color_matrix_1" not in data:
            raise ValueError(f"{path}: missing color_matrix_1 — not a valid extracted profile")

        def _opt(name: str) -> np.ndarray | None:
            return data[name].astype(np.float32) if name in data else None

        prof = DCPProfile(
            profile_name=str(data["profile_name"]),
            color_matrix_1=data["color_matrix_1"].astype(np.float32),
            color_matrix_2=_opt("color_matrix_2"),
            forward_matrix_1=_opt("forward_matrix_1"),
            forward_matrix_2=_opt("forward_matrix_2"),
            camera_calibration_1=_opt("camera_calibration_1"),
            camera_calibration_2=_opt("camera_calibration_2"),
            calibration_illuminant_1=int(data["calibration_illuminant_1"]),
            calibration_illuminant_2=int(data["calibration_illuminant_2"]),
            baseline_exposure_offset=float(data["baseline_exposure_offset"]),
            profile_tone_curve=_opt("profile_tone_curve"),
            kelvin_1=float(data["kelvin_1"]),
            kelvin_2=float(data["kelvin_2"]),
        )
        if "hsm_data_1" in data:
            hsm_dims = data["hsm_dims"]
            prof.hue_sat_map = HsvCube(
                hue_divisions=int(hsm_dims[0]),
                sat_divisions=int(hsm_dims[1]),
                val_divisions=int(hsm_dims[2]),
                srgb_gamma=bool(int(data["hsm_srgb_gamma"])),
                data_1=data["hsm_data_1"].astype(np.float32),
                data_2=(
                    data["hsm_data_2"].astype(np.float32)
                    if "hsm_data_2" in data else None
                ),
            )
        if "look_data" in data:
            look_dims = data["look_dims"]
            prof.look_table = HsvCube(
                hue_divisions=int(look_dims[0]),
                sat_divisions=int(look_dims[1]),
                val_divisions=int(look_dims[2]),
                srgb_gamma=bool(int(data["look_srgb_gamma"])),
                data_1=data["look_data"].astype(np.float32),
            )
    return prof


def _extracted_profile_search_roots() -> list[Path]:
    """Where to look for `.npz` extracted-profile files, in lookup order.

    1. `$LRT_CINEMA_PROFILES` env var — explicit user override; takes any
       absolute directory (typically points at a cloned sister
       `lrt-cinema-profiles` repo or a custom local cache).
    2. `~/.config/lrt-cinema/profiles/` — XDG-style per-user config dir,
       written by `tools/extract_dcp_library.py <source_root>` when the user
       runs the one-shot extraction against a `.dcp` source they supply.

    Linux / macOS use the XDG path verbatim; Windows uses the
    `%APPDATA%/lrt-cinema/profiles` equivalent. Honors the
    `XDG_CONFIG_HOME` env var on platforms where it applies.
    """
    import os
    roots: list[Path] = []
    env = os.environ.get("LRT_CINEMA_PROFILES")
    if env:
        p = Path(env)
        if p.is_dir():
            roots.append(p)
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            roots.append(Path(appdata) / "lrt-cinema" / "profiles")
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        config_home = Path(xdg) if xdg else (Path.home() / ".config")
        roots.append(config_home / "lrt-cinema" / "profiles")
    return [r for r in roots if r.is_dir()]


def find_extracted_profile_for_camera(
    make: str,
    model: str,
    extra_roots: list[Path] | None = None,
) -> Path | None:
    """Locate an `.npz` extracted profile for (make, model).

    Filename convention mirrors Adobe's DCP naming:
    `<label> Camera Standard.npz` (preferred) or `<label> Adobe Standard.npz`
    (fallback), where `<label>` is the Adobe-style camera label
    (e.g. "Nikon D750"). Returns the first match across the search roots
    in `_extracted_profile_search_roots()` plus any `extra_roots` passed
    in (used by tests to point at fixture dirs).
    """
    label = _adobe_camera_label(make, model)
    roots = _extracted_profile_search_roots()
    if extra_roots:
        roots.extend(r for r in extra_roots if r.is_dir())
    for root in roots:
        for candidate in (
            root / f"{label} Camera Standard.npz",
            root / f"{label} Adobe Standard.npz",
        ):
            if candidate.is_file():
                return candidate
    return None


def auto_detect_profile(
    raw_path: Path,
    extra_extracted_roots: list[Path] | None = None,
) -> tuple[DCPProfile, Path] | None:
    """End-to-end profile lookup via the Adobe-free extracted `.npz` roots.

    Probes the RAW's EXIF Make/Model, then searches the extracted-profile
    roots (`$LRT_CINEMA_PROFILES`, the per-user config dir, and any
    `extra_extracted_roots`). On a match, loads and returns the DCPProfile —
    no Adobe install required. Returns None when no match is found; the caller
    logs a clear "populate $LRT_CINEMA_PROFILES or pass --dcp" message and can
    still supply a profile explicitly via `--dcp` (a `.npz`, or a
    clean-room-parsed `.dcp`).

    Returns `(profile, source_path)` so callers can log where the profile
    came from.
    """
    info = read_raw_make_model(raw_path)
    if info is None:
        return None
    make, model = info
    extracted_path = find_extracted_profile_for_camera(
        make, model, extra_roots=extra_extracted_roots,
    )
    if extracted_path is not None:
        return load_profile(extracted_path), extracted_path
    return None
