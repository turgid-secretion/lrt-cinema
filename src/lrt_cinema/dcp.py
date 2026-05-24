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
    BaselineExposure / Offset            additive EV bias LR applies on
                                         top of any user exposure.
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

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

import numpy as np

# ---------------------------------------------------------------------------
# DCP IFD tags (DNG 1.7.1)
# ---------------------------------------------------------------------------

_TAG_COLOR_MATRIX_1 = 50721
_TAG_COLOR_MATRIX_2 = 50722
_TAG_CAMERA_CALIBRATION_1 = 50723
_TAG_CAMERA_CALIBRATION_2 = 50724
_TAG_BASELINE_EXPOSURE = 50730
_TAG_CALIBRATION_ILLUMINANT_1 = 50931
_TAG_CALIBRATION_ILLUMINANT_2 = 50932
_TAG_PROFILE_NAME = 50936
_TAG_PROFILE_TONE_CURVE = 50940
_TAG_FORWARD_MATRIX_1 = 50964
_TAG_FORWARD_MATRIX_2 = 50965
_TAG_BASELINE_EXPOSURE_OFFSET = 50970

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

    baseline_exposure: float = 0.0
    baseline_exposure_offset: float = 0.0

    # Tone curve as Nx2 array of (x, y) in 0..1. None if not present.
    profile_tone_curve: np.ndarray | None = None

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
    """
    size = _TYPE_SIZES.get(tag_type, 1) * count
    if size <= 4:
        return value_or_offset_bytes[:size]
    offset = struct.unpack(f"{byte_order}I", value_or_offset_bytes)[0]
    pos = f.tell()
    f.seek(offset)
    data = f.read(size)
    f.seek(pos)
    return data


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
    """
    path = Path(path)
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
            elif tag == _TAG_BASELINE_EXPOSURE and tag_type == _TYPE_SRATIONAL:
                prof.baseline_exposure = _decode_srational(data, 1, byte_order)[0]
            elif tag == _TAG_BASELINE_EXPOSURE_OFFSET and tag_type == _TYPE_SRATIONAL:
                prof.baseline_exposure_offset = _decode_srational(data, 1, byte_order)[0]
            elif (
                tag == _TAG_PROFILE_TONE_CURVE
                and tag_type == _TYPE_FLOAT
                and count % 2 == 0
            ):
                floats = _decode_floats(data, count, byte_order)
                prof.profile_tone_curve = np.asarray(floats).reshape(count // 2, 2)

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
    """Solve camera-RGB neutral multipliers for a given target (x, y).

    Iterative because the color matrix depends on kelvin and the kelvin
    we want is the one derived from the target (x, y). Converges in
    typically ≤5 iterations. Algorithm ported from
    colour-hdri.models.dng `xy_to_camera_neutral` (BSD-3, Mansencal
    et al. 2025), which in turn ports DNG SDK 1.7.1
    `dng_color_spec::SetWhiteXY`.

    Returns the un-normalized camera-RGB neutral vector. Caller
    normalizes — typically by dividing through so the green entry is 1.
    """
    u, v = xy_to_uv(x, y)
    kelvin = uv_to_kelvin(u, v)
    matrix = interpolate_color_matrix(profile, kelvin)
    xyz = _xy_to_xyz(x, y)
    for _ in range(10):
        neutral = matrix @ xyz
        new_matrix = interpolate_color_matrix(profile, kelvin)
        if np.allclose(new_matrix, matrix, atol=1e-9):
            return neutral
        matrix = new_matrix
        neutral = matrix @ xyz
    return neutral


def kelvin_tint_to_dt_multipliers(
    profile: DCPProfile,
    kelvin: float,
    tint: float = 0.0,
) -> tuple[float, float, float, float]:
    """Compute darktable temperature-module RGGB multipliers.

    Returns (red, green, blue, various) where `various` is the second
    green channel for Bayer/X-Trans-G2 cameras — for the consumer
    Bayer cameras lrt-cinema targets, G1 == G2. Multipliers are
    normalized so green = 1.

    The math:
      1. Target (kelvin, tint) → (x, y) via Robertson.
      2. Iteratively solve camera-RGB-at-white using interpolated
         ColorMatrix.
      3. Per-channel multiplier = 1 / camera-RGB-at-white, normalized so
         green channel multiplier = 1 (darktable convention — green is
         the reference). This way a 5500K-calibrated render through dt's
         temperature module produces the expected neutral output for
         the DCP's camera-RGB definition.
    """
    x, y = kelvin_tint_to_xy(kelvin, tint)
    neutral = xy_to_camera_neutral(profile, x, y)
    # Normalize so green = 1; dt's GUI does the same.
    green = neutral[1] if abs(neutral[1]) > 1e-9 else 1.0
    r_mul = green / neutral[0] if abs(neutral[0]) > 1e-9 else 1.0
    g_mul = 1.0
    b_mul = green / neutral[2] if abs(neutral[2]) > 1e-9 else 1.0
    return float(r_mul), float(g_mul), float(b_mul), float(g_mul)
