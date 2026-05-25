"""Synthesize a minimal Bayer-mosaic DNG that dt's libraw accepts.

The Tier 2 calibration tool needs to render a known-color test image
through dt-cli's algorithmic engine AND dt-cli's DCP engine, then fit
the 3×3 channelmixer matrix that maps algorithmic → DCP output.

This module builds the test image: a synthetic DNG containing 24
ColorChecker patches arranged in the standard 4×6 grid, encoded as a
Bayer mosaic (RGGB) so dt's libraw treats it like any real camera RAW.
The camera-RGB values per patch are derived by inverting the camera's
DCP ColorMatrix1 against known target XYZ values under D55 illuminant.

Design constraints:
  * No new dependencies. Hand-rolled TIFF writer mirrors the IFD-parsing
    style in `dcp.py`. ~250 LOC.
  * Minimal DNG tag set sufficient for libraw camera-ID lookup +
    demosaic. Validated empirically by running dt-cli on the output and
    confirming the rendered TIFF is non-trivial.
  * Per-Adobe-DCP convention: Make + Model + UniqueCameraModel mirror
    the strings libraw matches against (e.g. "NIKON CORPORATION" /
    "NIKON D750" / "Nikon D750"). Without these, libraw falls back to
    a generic input matrix and the calibration becomes meaningless.
  * Bayer pattern: RGGB by default (matches every common consumer
    Bayer Nikon/Canon/Sony/Fuji/Olympus body). Configurable via
    `cfa_pattern` for the rare X-Trans / mirrorless 4G arrangement
    case — those probably won't work without further plumbing.

DNG spec: https://helpx.adobe.com/camera-raw/digital-negative.html
TIFF 6.0: https://www.adobe.io/open/standards/TIFF.html
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# TIFF / DNG tag IDs
# ---------------------------------------------------------------------------

_TAG_NEW_SUBFILE_TYPE = 254
_TAG_IMAGE_WIDTH = 256
_TAG_IMAGE_LENGTH = 257
_TAG_BITS_PER_SAMPLE = 258
_TAG_COMPRESSION = 259
_TAG_PHOTOMETRIC_INTERPRETATION = 262
_TAG_MAKE = 271
_TAG_MODEL = 272
_TAG_STRIP_OFFSETS = 273
_TAG_ORIENTATION = 274
_TAG_SAMPLES_PER_PIXEL = 277
_TAG_ROWS_PER_STRIP = 278
_TAG_STRIP_BYTE_COUNTS = 279
_TAG_PLANAR_CONFIGURATION = 284
_TAG_SOFTWARE = 305
_TAG_CFA_REPEAT_PATTERN_DIM = 33421
_TAG_CFA_PATTERN = 33422
_TAG_DNG_VERSION = 50706
_TAG_DNG_BACKWARD_VERSION = 50707
_TAG_UNIQUE_CAMERA_MODEL = 50708
_TAG_CFA_PLANE_COLOR = 50710
_TAG_CFA_LAYOUT = 50711
_TAG_BLACK_LEVEL = 50714
_TAG_WHITE_LEVEL = 50717
_TAG_AS_SHOT_NEUTRAL = 50728
_TAG_COLOR_MATRIX_1 = 50721
_TAG_COLOR_MATRIX_2 = 50722
_TAG_CALIBRATION_ILLUMINANT_1 = 50778
_TAG_CALIBRATION_ILLUMINANT_2 = 50779

# TIFF type codes (TIFF 6.0)
_TYPE_BYTE = 1
_TYPE_ASCII = 2
_TYPE_SHORT = 3
_TYPE_LONG = 4
_TYPE_RATIONAL = 5
_TYPE_SRATIONAL = 10

_TYPE_SIZES = {
    _TYPE_BYTE: 1, _TYPE_ASCII: 1, _TYPE_SHORT: 2, _TYPE_LONG: 4,
    _TYPE_RATIONAL: 8, _TYPE_SRATIONAL: 8,
}

# Bayer pattern encodings — value per (y, x) tuple. 0=R, 1=G, 2=B.
# CFA arrays read by libraw / dt are 4-byte BYTE arrays in row-major
# 2×2 order. RGGB is the universal consumer-Bayer arrangement.
CFA_RGGB = bytes([0, 1, 1, 2])
CFA_GRBG = bytes([1, 0, 2, 1])
CFA_GBRG = bytes([1, 2, 0, 1])
CFA_BGGR = bytes([2, 1, 1, 0])


@dataclass
class PatchLayout:
    """Position metadata for the synthetic chart's 24 patches.

    Returned by `write_calibration_dng` so the patch-sampling step
    (after dt-cli renders the DNG) knows exactly where to read.
    Coordinates are in IMAGE-PIXEL space (rendered TIFF), top-left
    origin. The Bayer mosaic at input has the same dimensions; dt's
    demosaic does not change them.
    """

    image_width: int
    image_height: int
    patch_size: int
    grid_cols: int
    grid_rows: int
    # Per-patch upper-left corner (y, x) in image pixels. Length 24,
    # row-major (row 0 = top row of the chart, col 0 = left).
    patch_origins: list[tuple[int, int]]

    def patch_bbox(self, index: int) -> tuple[int, int, int, int]:
        """(y0, x0, y1, x1) — half-open bounds of patch `index`."""
        y0, x0 = self.patch_origins[index]
        return y0, x0, y0 + self.patch_size, x0 + self.patch_size

    def patch_inner_bbox(
        self, index: int, margin_fraction: float = 0.25,
    ) -> tuple[int, int, int, int]:
        """Inner bbox with a margin — sampling here avoids edge bleed
        from dt's demosaic interpolating across patch boundaries.

        `margin_fraction=0.25` reserves the inner 50%×50% of each patch.
        """
        y0, x0, y1, x1 = self.patch_bbox(index)
        m = int(self.patch_size * margin_fraction)
        return y0 + m, x0 + m, y1 - m, x1 - m


def _pack_ifd_entry(
    tag: int, ttype: int, count: int, payload: bytes,
    blob_offset_cursor: list[int], blob_out: list[bytes],
) -> bytes:
    """Pack one 12-byte IFD entry; inline if ≤4 bytes, else point to blob.

    `blob_offset_cursor` is a single-element list (mutable int) tracking
    the next available offset in the post-IFD blob region. `blob_out`
    is the running blob byte stream; appended in-place.
    """
    size = _TYPE_SIZES.get(ttype, 1) * count
    if size <= 4:
        value_field = payload + b"\x00" * (4 - size)
    else:
        # Pad payload to even length (TIFF requires 2-byte alignment
        # for offsets; libraw is strict about this).
        if len(payload) % 2 != 0:
            payload = payload + b"\x00"
        value_field = struct.pack("<I", blob_offset_cursor[0])
        blob_out.append(payload)
        blob_offset_cursor[0] += len(payload)
    return struct.pack("<HHI4s", tag, ttype, count, value_field)


def _srational(num: int, den: int) -> bytes:
    return struct.pack("<ii", num, den)


def _rational(num: int, den: int) -> bytes:
    return struct.pack("<II", num, den)


def _encode_matrix_srational(matrix: np.ndarray, scale: int = 10000) -> bytes:
    """3×3 numpy matrix → 9 SRATIONAL entries (72 bytes)."""
    out = b""
    for v in matrix.flatten():
        out += _srational(int(round(v * scale)), scale)
    return out


def _bayer_indices(height: int, width: int, cfa: bytes) -> np.ndarray:
    """Return a (height, width) array of 0/1/2 channel indices per the
    CFA pattern. The 2×2 CFA tile tiles across the image.
    """
    tile = np.array(list(cfa), dtype=np.uint8).reshape(2, 2)
    full = np.tile(tile, (height // 2 + 1, width // 2 + 1))[:height, :width]
    return full


def _build_bayer_image(
    patches_camera_rgb: np.ndarray,  # (24, 3) float in [0, 1]
    layout: PatchLayout,
    cfa: bytes,
    white_level: int,
    bg_camera_rgb: tuple[float, float, float] = (0.5, 0.5, 0.5),
) -> np.ndarray:
    """Construct the (H, W) uint16 Bayer mosaic.

    Each patch fills its rectangle uniformly with the per-channel
    camera RGB value at the corresponding Bayer position. Background
    (the space between patches and the image border) is filled with
    `bg_camera_rgb` so dt's input has nonzero pixels everywhere — a
    fully-black backdrop trips some dt code paths (auto-exposure,
    statistics) in subtle ways.

    Returns: uint16 array, values in [0, white_level].
    """
    height, width = layout.image_height, layout.image_width
    img = np.empty((height, width, 3), dtype=np.float32)
    img[..., 0] = bg_camera_rgb[0]
    img[..., 1] = bg_camera_rgb[1]
    img[..., 2] = bg_camera_rgb[2]
    for i, (y0, x0) in enumerate(layout.patch_origins):
        y1, x1 = y0 + layout.patch_size, x0 + layout.patch_size
        for c in range(3):
            img[y0:y1, x0:x1, c] = patches_camera_rgb[i, c]
    # Per-pixel Bayer pick.
    indices = _bayer_indices(height, width, cfa)
    bayer = np.take_along_axis(img, indices[..., None], axis=2).squeeze(2)
    bayer = np.clip(bayer * white_level, 0.0, float(white_level))
    return bayer.astype(np.uint16)


def _build_patch_layout(
    patch_size: int,
    grid_cols: int,
    grid_rows: int,
    margin: int,
) -> PatchLayout:
    """Lay out a `grid_rows × grid_cols` patch grid with `margin`-pixel
    padding around each patch and around the chart border. Returns the
    full image dimensions + per-patch origins. Patch size and margin
    are forced to be EVEN so the Bayer-tile boundary aligns predictably.
    """
    if patch_size % 2 != 0:
        patch_size += 1
    if margin % 2 != 0:
        margin += 1
    width = margin + grid_cols * (patch_size + margin)
    height = margin + grid_rows * (patch_size + margin)
    patch_origins: list[tuple[int, int]] = []
    for row in range(grid_rows):
        for col in range(grid_cols):
            y = margin + row * (patch_size + margin)
            x = margin + col * (patch_size + margin)
            patch_origins.append((y, x))
    return PatchLayout(
        image_width=width,
        image_height=height,
        patch_size=patch_size,
        grid_cols=grid_cols,
        grid_rows=grid_rows,
        patch_origins=patch_origins,
    )


def write_calibration_dng(
    out_path: Path,
    *,
    camera_make: str,
    camera_model: str,
    unique_camera_model: str,
    color_matrix_1: np.ndarray,                # (3, 3)
    color_matrix_2: np.ndarray | None = None,  # (3, 3)
    calibration_illuminant_1: int = 17,        # 17 = Standard A
    calibration_illuminant_2: int = 21,        # 21 = D65
    as_shot_neutral: tuple[float, float, float] | None = None,
    patches_camera_rgb: np.ndarray | None = None,  # (24, 3) in [0, 1]
    cfa_pattern: bytes = CFA_RGGB,
    patch_size: int = 64,
    grid_cols: int = 6,
    grid_rows: int = 4,
    margin: int = 8,
    white_level: int = 16383,                  # 14-bit Nikon default
    black_level: int = 0,
) -> PatchLayout:
    """Write a synthetic Bayer-mosaic DNG of 24 ColorChecker patches.

    The DNG's camera identity (Make/Model/UniqueCameraModel) must match
    a camera dt's libraw can identify so it picks the correct default
    input matrix — otherwise the algorithmic engine renders against a
    generic fallback and the calibration is meaningless.

    `patches_camera_rgb` is a 24×3 array of pre-Bayer camera-RGB values
    in [0, 1]. The caller is expected to derive these from target XYZ
    via `inv(interpolate_color_matrix(profile, target_kelvin))`. If
    None, an identity ramp is written for testing the writer in
    isolation.

    Returns the `PatchLayout` describing where the 24 patches landed
    in the image, so the post-render patch-sampling step knows the
    coordinates.
    """
    layout = _build_patch_layout(patch_size, grid_cols, grid_rows, margin)
    if patches_camera_rgb is None:
        # Identity ramp — 24 grayscale steps. Useful for testing the
        # writer + dt-cli round-trip without supplying real patch data.
        gray = np.linspace(0.05, 0.95, 24)
        patches_camera_rgb = np.stack([gray, gray, gray], axis=1)
    if patches_camera_rgb.shape != (grid_rows * grid_cols, 3):
        raise ValueError(
            f"patches_camera_rgb shape {patches_camera_rgb.shape} != "
            f"({grid_rows * grid_cols}, 3)"
        )
    bayer = _build_bayer_image(
        patches_camera_rgb=patches_camera_rgb,
        layout=layout,
        cfa=cfa_pattern,
        white_level=white_level,
    )

    image_data = bayer.tobytes()
    height, width = bayer.shape

    # Build ASCII strings with null terminators.
    make_b = (camera_make + "\x00").encode("ascii")
    model_b = (camera_model + "\x00").encode("ascii")
    unique_b = (unique_camera_model + "\x00").encode("ascii")
    software_b = (b"lrt-cinema synthetic calibration DNG\x00")

    # AsShotNeutral default = camera RGB at "white": use the mean of
    # the patches as a proxy for "the scene's white point under the
    # synthesis illuminant." Libraw uses this to derive WB multipliers
    # in its default pipeline.
    if as_shot_neutral is None:
        white_camera_rgb = patches_camera_rgb.mean(axis=0)
        # Normalize so green = 1, then invert (AsShotNeutral encodes
        # the camera's response to neutral — multipliers = 1/asn).
        if white_camera_rgb[1] != 0:
            asn = white_camera_rgb / white_camera_rgb[1]
        else:
            asn = np.ones(3)
        as_shot_neutral = (float(asn[0]), float(asn[1]), float(asn[2]))

    # Pack AsShotNeutral as 3 RATIONAL entries (num/den at 10000 scale).
    asn_bytes = b""
    for v in as_shot_neutral:
        asn_bytes += _rational(int(round(v * 10000)), 10000)

    # Color matrices.
    cm1_bytes = _encode_matrix_srational(np.asarray(color_matrix_1))
    cm2_bytes = (
        _encode_matrix_srational(np.asarray(color_matrix_2))
        if color_matrix_2 is not None else None
    )

    # Build IFD entries. Each entry produces 12 bytes inline; big
    # payloads go into the blob region after the IFD.
    # ENTRIES MUST BE SORTED BY TAG ID (TIFF spec). Build a list of
    # (tag, type, count, payload_bytes) then sort.
    entries_raw: list[tuple[int, int, int, bytes]] = [
        (_TAG_NEW_SUBFILE_TYPE, _TYPE_LONG, 1, struct.pack("<I", 0)),
        (_TAG_IMAGE_WIDTH, _TYPE_LONG, 1, struct.pack("<I", width)),
        (_TAG_IMAGE_LENGTH, _TYPE_LONG, 1, struct.pack("<I", height)),
        (_TAG_BITS_PER_SAMPLE, _TYPE_SHORT, 1, struct.pack("<H", 16)),
        (_TAG_COMPRESSION, _TYPE_SHORT, 1, struct.pack("<H", 1)),  # uncompressed
        (_TAG_PHOTOMETRIC_INTERPRETATION, _TYPE_SHORT, 1, struct.pack("<H", 32803)),  # CFA
        (_TAG_MAKE, _TYPE_ASCII, len(make_b), make_b),
        (_TAG_MODEL, _TYPE_ASCII, len(model_b), model_b),
        # StripOffsets placeholder — patched after we know the post-IFD
        # offset where the image data lives.
        (_TAG_STRIP_OFFSETS, _TYPE_LONG, 1, struct.pack("<I", 0)),
        (_TAG_ORIENTATION, _TYPE_SHORT, 1, struct.pack("<H", 1)),  # top-left
        (_TAG_SAMPLES_PER_PIXEL, _TYPE_SHORT, 1, struct.pack("<H", 1)),
        (_TAG_ROWS_PER_STRIP, _TYPE_LONG, 1, struct.pack("<I", height)),
        (_TAG_STRIP_BYTE_COUNTS, _TYPE_LONG, 1, struct.pack("<I", len(image_data))),
        (_TAG_PLANAR_CONFIGURATION, _TYPE_SHORT, 1, struct.pack("<H", 1)),
        (_TAG_SOFTWARE, _TYPE_ASCII, len(software_b), software_b),
        (_TAG_CFA_REPEAT_PATTERN_DIM, _TYPE_SHORT, 2, struct.pack("<HH", 2, 2)),
        (_TAG_CFA_PATTERN, _TYPE_BYTE, 4, cfa_pattern),
        (_TAG_BLACK_LEVEL, _TYPE_LONG, 1, struct.pack("<I", black_level)),
        (_TAG_WHITE_LEVEL, _TYPE_LONG, 1, struct.pack("<I", white_level)),
        (_TAG_COLOR_MATRIX_1, _TYPE_SRATIONAL, 9, cm1_bytes),
        (_TAG_AS_SHOT_NEUTRAL, _TYPE_RATIONAL, 3, asn_bytes),
        (_TAG_DNG_VERSION, _TYPE_BYTE, 4, bytes([1, 4, 0, 0])),
        (_TAG_DNG_BACKWARD_VERSION, _TYPE_BYTE, 4, bytes([1, 0, 0, 0])),
        (_TAG_UNIQUE_CAMERA_MODEL, _TYPE_ASCII, len(unique_b), unique_b),
        (_TAG_CFA_PLANE_COLOR, _TYPE_BYTE, 3, bytes([0, 1, 2])),  # R, G, B
        (_TAG_CFA_LAYOUT, _TYPE_SHORT, 1, struct.pack("<H", 1)),  # rectangular
        (_TAG_CALIBRATION_ILLUMINANT_1, _TYPE_SHORT, 1, struct.pack("<H", calibration_illuminant_1)),
        (_TAG_CALIBRATION_ILLUMINANT_2, _TYPE_SHORT, 1, struct.pack("<H", calibration_illuminant_2)),
    ]
    if cm2_bytes is not None:
        entries_raw.append(
            (_TAG_COLOR_MATRIX_2, _TYPE_SRATIONAL, 9, cm2_bytes),
        )

    entries_raw.sort(key=lambda e: e[0])
    n_entries = len(entries_raw)

    # Layout:
    #   [0..8)            TIFF header
    #   [8..8+ifd_size)   IFD0 (entries + next-IFD offset)
    #   [post-IFD..)      blob region (big values) + image data
    #
    # The big-value blob offsets are absolute file positions. We compute
    # the post-IFD start, then fill blob payloads, then place image data
    # immediately after the blob — patching the StripOffsets entry's
    # value field with that absolute offset.
    ifd_size = 2 + 12 * n_entries + 4
    post_ifd_offset = 8 + ifd_size
    blob_offset_cursor = [post_ifd_offset]
    blob_chunks: list[bytes] = []

    ifd_bytes = struct.pack("<H", n_entries)
    strip_offsets_entry_index: int | None = None
    for i, (tag, ttype, count, payload) in enumerate(entries_raw):
        if tag == _TAG_STRIP_OFFSETS:
            strip_offsets_entry_index = i
            # Reserve a placeholder; we'll patch the 4-byte value field
            # after the blob region is closed and image-data offset known.
            ifd_bytes += struct.pack("<HHI4s", tag, ttype, count, b"\x00\x00\x00\x00")
        else:
            ifd_bytes += _pack_ifd_entry(
                tag, ttype, count, payload, blob_offset_cursor, blob_chunks,
            )
    ifd_bytes += struct.pack("<I", 0)  # next IFD = 0 (last)

    blob = b"".join(blob_chunks)
    image_data_offset = post_ifd_offset + len(blob)

    # Patch the StripOffsets value field with image_data_offset.
    if strip_offsets_entry_index is None:
        raise RuntimeError("internal: StripOffsets entry index not recorded")
    entry_start = 2 + 12 * strip_offsets_entry_index
    # entry_start..entry_start+12 is the StripOffsets entry; bytes 8..12
    # within the entry are the value field.
    patched = (
        ifd_bytes[: entry_start + 8]
        + struct.pack("<I", image_data_offset)
        + ifd_bytes[entry_start + 12 :]
    )
    ifd_bytes = patched

    header = b"II" + struct.pack("<H", 42) + struct.pack("<I", 8)
    contents = header + ifd_bytes + blob + image_data

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(contents)
    return layout
