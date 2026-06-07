#!/usr/bin/env python3
"""Reference-measurement harness: does REAL AMaZE (darktable's battle-tested port,
built standalone outside the package) resolve the venetian-blind false colour, or
is it fundamental across demosaics?

ISOLATION CONTRACT (apples-to-apples): the ONLY thing that varies between the
control (our RCD), the hand-off gate, and AMaZE is the demosaic. Everything else
— the CFA we extract, Stages 3-9, develop_ops, the production sRGB encode — is the
exact same code path. We achieve this by monkeypatching the single shared demosaic
chokepoint `pipeline._demosaic_rgb` to return a precomputed camera-RGB.

AMaZE is GPL; like dng_validate / ACR it is a REFERENCE oracle only. Its source +
binary live OUTSIDE the package (/tmp/amaze_work); nothing GPL is added to src/.

Metric: horizontal chroma-HF over the blinds crop (the streaks), per the session
spec. our-RCD ~0.56, ACR-NR-off ~0.28.
"""
from __future__ import annotations

import struct
import subprocess
import sys
from pathlib import Path

import numpy as np

# --- paths ------------------------------------------------------------------
WORKTREE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKTREE / "src"))  # editable-install gotcha: force worktree src

NEF = Path(
    "/Volumes/SanDisk Extreme Pro 55AF Media/Projects/2026 international faire timelapse/DSC_4053.NEF"
)
DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/"
    "Nikon D750/Nikon D750 Camera Standard.dcp"
)
AMAZE_BIN = Path("/tmp/amaze_work/amaze")
AMAZE_CFA = Path("/tmp/amaze_work/cfa.bin")
AMAZE_RGB = Path("/tmp/amaze_work/rgb.bin")

RCD_TESTRUN = Path(
    "/Volumes/SanDisk Extreme Pro 55AF Media/Projects/lrt-cinema-testrun/"
    "_method_rcd/LRT_00001.tif"
)
ACR_NR_OFF = Path(
    "/Volumes/SanDisk Extreme Pro 55AF Media/Projects/lrt-export/NR-off/DSC_4053.tif"
)
CROP_DIR = WORKTREE / "docs" / "research" / "amaze-crops"

# --- the metric (verbatim from the session spec) ----------------------------
import colour  # noqa: E402
from scipy.ndimage import uniform_filter  # noqa: E402


def chroma_hf(srgb01: np.ndarray, off: int = 0) -> float:
    reg = srgb01[1350 + off:1660 + off, 150 + off:1400 + off]
    def eotf(x):
        return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
    L = colour.XYZ_to_Lab(
        colour.RGB_to_XYZ(eotf(reg), "sRGB", apply_cctf_decoding=False),
        np.array([0.3127, 0.3290]),
    )
    ch = np.hypot(L[..., 1], L[..., 2])
    return float(np.abs(ch - uniform_filter(ch, (1, 5))).mean())


# --- dcraw `filters` bitmask from a phase string ----------------------------
# darktable FC(row,col,filters) = filters >> (((row<<1 & 14)+(col&1))<<1) & 3
# with RED=0 GREEN=1 BLUE=2. Build the 32-bit mask so FC reproduces the pattern.
_COLOR_CODE = {"R": 0, "G": 1, "B": 2}


def filters_from_pattern(pattern: str) -> int:
    """2x2 phase string (e.g. 'RGGB', row-major) -> dcraw filters bitmask."""
    p = [[_COLOR_CODE[pattern[0]], _COLOR_CODE[pattern[1]]],
         [_COLOR_CODE[pattern[2]], _COLOR_CODE[pattern[3]]]]
    filters = 0
    # dcraw mask tiles the 2x2 over a 16-pixel period; FC only reads (row&1? no:
    # (row<<1 & 14) spans rows 0..7). We must set the same 2x2 colour for every
    # row pair. Easiest: set each of the 32 bit-pairs from the 2x2 via the SAME
    # FC index formula, then verify by round-trip below.
    for row in range(8):
        for col in range(2):
            shift = (((row << 1) & 14) + (col & 1)) << 1
            filters |= (p[row & 1][col & 1] & 3) << shift
    return filters & 0xFFFFFFFF


def _fc(row: int, col: int, filters: int) -> int:
    return (filters >> ((((row << 1) & 14) + (col & 1)) << 1)) & 3


def verify_filters(pattern: str, filters: int) -> None:
    p = [[_COLOR_CODE[pattern[0]], _COLOR_CODE[pattern[1]]],
         [_COLOR_CODE[pattern[2]], _COLOR_CODE[pattern[3]]]]
    for r in range(4):
        for c in range(4):
            got = _fc(r, c, filters)
            want = p[r & 1][c & 1]
            assert got == want, f"FC({r},{c})={got} != {want} (pattern {pattern})"


# --- CFA extraction (the EXACT bytes our pipeline feeds its demosaic) --------
def extract_cfa():
    import rawpy
    from lrt_cinema.pipeline import _extract_cfa
    with rawpy.imread(str(NEF)) as raw:
        cfa, pattern = _extract_cfa(raw)
    return np.ascontiguousarray(cfa, dtype=np.float32), pattern


# --- run real AMaZE on the CFA ---------------------------------------------
def run_amaze(cfa: np.ndarray, pattern: str, clip_pt: float = 1.0) -> np.ndarray:
    h, w = cfa.shape
    filters = filters_from_pattern(pattern)
    verify_filters(pattern, filters)
    with open(AMAZE_CFA, "wb") as f:
        f.write(struct.pack("<iiIf", w, h, filters, clip_pt))
        f.write(cfa.tobytes())
    subprocess.run([str(AMAZE_BIN), str(AMAZE_CFA), str(AMAZE_RGB)], check=True)
    rgb = np.fromfile(AMAZE_RGB, dtype=np.float32).reshape(h, w, 3)
    return np.ascontiguousarray(rgb)


# --- inject a precomputed RGB into render_frame via the shared chokepoint ----
def render_with_injected_rgb(camera_rgb: np.ndarray):
    """Run the FULL production render (Stages 1-9 + develop_ops + sRGB encode)
    but with `camera_rgb` substituted for the demosaic output. Returns a
    16-bit-equivalent float [0,1] sRGB image (4032x6032)."""
    import lrt_cinema.pipeline as P
    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.output import _prophoto_to_display

    rgb32 = np.ascontiguousarray(camera_rgb, dtype=np.float32)

    orig = P._demosaic_rgb

    def patched(raw, rawpy_mod, half_size, demosaic):  # noqa: ARG001
        # Same shape/scale/dtype the real demosaic returns; identity otherwise.
        return rgb32

    P._demosaic_rgb = patched
    try:
        profile = parse_dcp(DCP)
        # demosaic="linear" is irrelevant now (patched), but keep it explicit.
        result = P.render_frame(NEF, profile, dcp_path=DCP, demosaic="linear")
    finally:
        P._demosaic_rgb = orig

    # Production sRGB encode (output.py), matching write_tiff_display's path.
    encoded = _prophoto_to_display(result.prophoto, "srgb")
    return np.clip(encoded, 0.0, 1.0)


def our_rcd_camera_rgb(cfa: np.ndarray, pattern: str) -> np.ndarray:
    from lrt_cinema import accel
    return np.ascontiguousarray(accel.rcd_demosaic(cfa, pattern), dtype=np.float32)


def save_crop(srgb01: np.ndarray, name: str, off: int = 0) -> None:
    import tifffile
    CROP_DIR.mkdir(parents=True, exist_ok=True)
    reg = srgb01[1350 + off:1660 + off, 150 + off:1400 + off]
    tifffile.imwrite(str(CROP_DIR / name), (reg * 65535 + 0.5).astype(np.uint16))


def main() -> int:
    import tifffile

    print("== extracting CFA our pipeline feeds the demosaic ==")
    cfa, pattern = extract_cfa()
    print(f"   cfa {cfa.shape} {cfa.dtype}  pattern={pattern}  "
          f"range=[{cfa.min():.4f},{cfa.max():.4f}]")

    # ---- HAND-OFF GATE: our RCD through the inject path must match the normal
    #      _method_rcd render at the blinds (proves the mechanism). ----
    print("\n== HAND-OFF GATE: our RCD through the inject-and-finish path ==")
    rcd_rgb = our_rcd_camera_rgb(cfa, pattern)
    rcd_srgb = render_with_injected_rgb(rcd_rgb)
    rcd_inject_hf = chroma_hf(rcd_srgb, 0)
    print(f"   our-RCD-injected blinds chroma-HF = {rcd_inject_hf:.4f}  (must be ~0.56)")

    rcd_disk = tifffile.imread(str(RCD_TESTRUN)).astype(np.float32) / 65535.0
    rcd_disk_hf = chroma_hf(rcd_disk, 0)
    print(f"   on-disk _method_rcd blinds chroma-HF = {rcd_disk_hf:.4f}")

    # ---- AMaZE ----
    print("\n== REAL AMaZE on the same CFA ==")
    amaze_rgb = run_amaze(cfa, pattern, clip_pt=1.0)
    print(f"   amaze rgb {amaze_rgb.shape} range=[{amaze_rgb.min():.4f},"
          f"{amaze_rgb.max():.4f}]")
    amaze_srgb = render_with_injected_rgb(amaze_rgb)
    amaze_hf = chroma_hf(amaze_srgb, 0)
    print(f"   AMaZE-injected blinds chroma-HF = {amaze_hf:.4f}")

    # ---- references ----
    acr = tifffile.imread(str(ACR_NR_OFF)).astype(np.float32) / 65535.0
    acr_hf = chroma_hf(acr, -8)

    # ---- crops ----
    save_crop(rcd_srgb, "our_rcd_blinds.tif", 0)
    save_crop(amaze_srgb, "amaze_blinds.tif", 0)
    save_crop(acr, "acr_nr_off_blinds.tif", -8)

    print("\n================ SUMMARY (blinds chroma-HF) ================")
    print(f"  ACR-NR-off (TARGET)        : {acr_hf:.4f}")
    print(f"  our RCD (baseline, on-disk): {rcd_disk_hf:.4f}")
    print(f"  our RCD (injected, gate)   : {rcd_inject_hf:.4f}")
    print(f"  REAL AMaZE (injected)      : {amaze_hf:.4f}")
    print("===========================================================")
    gate_ok = abs(rcd_inject_hf - rcd_disk_hf) < 0.03
    print(f"  hand-off gate {'PASS' if gate_ok else 'FAIL'} "
          f"(|inject - disk| = {abs(rcd_inject_hf - rcd_disk_hf):.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
