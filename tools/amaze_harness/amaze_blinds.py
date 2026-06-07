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
# GPL reference binary lives in the gitignored tools/external/amaze (built by its
# build.sh); falls back to the ephemeral /tmp build if the local one is absent.
AMAZE_BIN = WORKTREE / "tools" / "external" / "amaze" / "amaze"
if not AMAZE_BIN.exists():
    AMAZE_BIN = Path("/tmp/amaze_work/amaze")
AMAZE_CFA = Path("/tmp/amaze_cfa.bin")
AMAZE_RGB = Path("/tmp/amaze_rgb.bin")

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


# --- the EXACT production lrtimelapse finish (cli.py 432-443 + encode) -------
# Defaults for the lrtimelapse preset / faithful sRGB TIFF (cli.py):
#   highlight_recovery=True, intent=FAITHFUL, master_look="bake",
#   capture_sharpen="off", stop_after_stage=9.
def _load_ops():
    """DevelopOps parsed from the frame's LRT XMP sidecar (the develop intent the
    on-disk _method_rcd render carried). Applied IDENTICALLY to every demosaic, so
    it cannot bias the demosaic-vs-demosaic delta — but it IS needed to land on the
    same develop scale as the 0.56 / 0.28 references (both fully-developed)."""
    from lrt_cinema.xmp_parser import parse_xmp_file
    xmp = NEF.with_suffix(".xmp")
    ops = parse_xmp_file(xmp)[0]
    return ops


def _finish_to_srgb(prophoto, ops):
    """linear ProPhoto (post Stage-9) -> production faithful sRGB float [0,1]."""
    from lrt_cinema.develop_ops import apply_develop_ops
    from lrt_cinema.ir import RenderIntent
    from lrt_cinema.output import _prophoto_to_display
    graded = apply_develop_ops(
        prophoto, ops, RenderIntent.FAITHFUL, master_look="bake", capture_sharpen="off",
    )
    return np.clip(_prophoto_to_display(graded, "srgb"), 0.0, 1.0)


def render_with_injected_rgb(camera_rgb: np.ndarray, ops):
    """FULL production lrtimelapse render with `camera_rgb` substituted for the
    demosaic output (the ONLY thing that varies). Stages 1-9 (incl. HL-recovery)
    + develop_ops + production sRGB encode. Returns float [0,1] sRGB (4032x6032)."""
    import lrt_cinema.pipeline as P
    from lrt_cinema.dcp import parse_dcp

    rgb32 = np.ascontiguousarray(camera_rgb, dtype=np.float32)
    orig = P._demosaic_rgb

    def patched(raw, rawpy_mod, half_size, demosaic):  # noqa: ARG001
        return rgb32

    P._demosaic_rgb = patched
    try:
        profile = parse_dcp(DCP)
        result = P.render_frame(
            NEF, profile, dcp_path=DCP, develop_ops=ops,
            highlight_recovery=True, demosaic="linear",  # patched; name irrelevant
        )
    finally:
        P._demosaic_rgb = orig
    return _finish_to_srgb(result.prophoto, ops)


def render_native_rcd(ops):
    """A CURRENT production RCD render (no monkeypatch) through the identical
    finish — the version-proof hand-off gate. Inject-RCD must match this."""
    import lrt_cinema.pipeline as P
    from lrt_cinema.dcp import parse_dcp
    profile = parse_dcp(DCP)
    result = P.render_frame(
        NEF, profile, dcp_path=DCP, develop_ops=ops,
        highlight_recovery=True, demosaic="rcd",
    )
    return _finish_to_srgb(result.prophoto, ops)


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
    ops = _load_ops()

    # ---- HAND-OFF GATE (version-proof internal consistency): our RCD through the
    #      inject path must match a CURRENT native RCD production render. Both carry
    #      the same develop_ops + HL-recovery, so this proves ONLY the demosaic
    #      varies. (We do NOT gate against the stale 0.7.1a0 on-disk TIFF.) ----
    print("\n== HAND-OFF GATE: inject-RCD vs native production-RCD (current code) ==")
    rcd_rgb = our_rcd_camera_rgb(cfa, pattern)
    rcd_inject = render_with_injected_rgb(rcd_rgb, ops)
    rcd_native = render_native_rcd(ops)
    rcd_inject_hf = chroma_hf(rcd_inject, 0)
    rcd_native_hf = chroma_hf(rcd_native, 0)
    # Byte-level identity of the two RGBs at the blinds (the strongest gate).
    a = (rcd_inject[1350:1660, 150:1400] * 65535).round()
    b = (rcd_native[1350:1660, 150:1400] * 65535).round()
    max_cu = float(np.abs(a - b).max())
    print(f"   inject-RCD blinds chroma-HF = {rcd_inject_hf:.4f}")
    print(f"   native-RCD blinds chroma-HF = {rcd_native_hf:.4f}")
    print(f"   max |inject-native| at blinds = {max_cu:.1f} code units (16-bit)")

    rcd_disk = tifffile.imread(str(RCD_TESTRUN)).astype(np.float32) / 65535.0
    rcd_disk_hf = chroma_hf(rcd_disk, 0)
    print(f"   (FYI) stale on-disk _method_rcd 0.7.1a0 = {rcd_disk_hf:.4f}")

    # ---- AMaZE (same CFA, same finish) ----
    print("\n== REAL AMaZE on the same CFA, same finish ==")
    amaze_rgb = run_amaze(cfa, pattern, clip_pt=1.0)
    print(f"   amaze rgb {amaze_rgb.shape} range=[{amaze_rgb.min():.4f},"
          f"{amaze_rgb.max():.4f}]")
    amaze_srgb = render_with_injected_rgb(amaze_rgb, ops)
    amaze_hf = chroma_hf(amaze_srgb, 0)
    print(f"   AMaZE-injected blinds chroma-HF = {amaze_hf:.4f}")

    # ---- references ----
    acr = tifffile.imread(str(ACR_NR_OFF)).astype(np.float32) / 65535.0
    acr_hf = chroma_hf(acr, -8)

    # ---- 1:1 crops ----
    save_crop(rcd_native, "our_rcd_blinds.tif", 0)
    save_crop(amaze_srgb, "amaze_blinds.tif", 0)
    save_crop(acr, "acr_nr_off_blinds.tif", -8)

    gate_ok = max_cu <= 1.0  # ≤1 code unit = byte-identical (FP rounding only)
    delta = amaze_hf - rcd_native_hf
    print("\n================ SUMMARY (blinds chroma-HF) ================")
    print(f"  ACR-NR-off (TARGET)           : {acr_hf:.4f}")
    print(f"  our RCD  (native, current)    : {rcd_native_hf:.4f}")
    print(f"  our RCD  (injected, gate)     : {rcd_inject_hf:.4f}")
    print(f"  REAL AMaZE (injected)         : {amaze_hf:.4f}")
    print(f"  AMaZE - RCD delta             : {delta:+.4f}")
    print("===========================================================")
    print(f"  HAND-OFF GATE {'PASS' if gate_ok else 'FAIL'} "
          f"(max {max_cu:.1f} cu inject-vs-native at blinds)")
    verdict = ("AMaZE REACHES ACR (~0.28) -> adopt AMaZE-class demosaic"
               if amaze_hf < 0.40
               else "FUNDAMENTAL -> AMaZE ~= RCD, no demosaic swap reaches ACR's 0.28")
    print(f"  VERDICT: {verdict}")
    print(f"  crops: {CROP_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
