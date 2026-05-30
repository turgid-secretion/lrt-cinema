"""v0.7 Q1.0 spike — throwaway DNG-tag mutator.

NOT PRODUCTION CODE. Used once to answer: does DaVinci Resolve honor
per-frame DNG metadata across a CinemaDNG sequence, or apply frame-1's
metadata to the whole clip?

Builds two 2-frame DNG sequences from one source DNG:

  T1_AsShotNeutral/  SPIKE_T1_{0001,0002}.dng — frames differ only in WB.
  T2_BaselineExp/    SPIKE_T2_{0001,0002}.dng — frames differ only in BaselineExposure.

User then drags each pair into Resolve as a clip and inspects whether
frame 2 decodes with its own metadata or with frame 1's.

Delete this directory after the spike completes — see
`docs/research/v07-resolve-cdng-spike.md`.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
from pathlib import Path

SRC = Path("/tmp/v07_spike/dng_cache/DSC_4053.8e288333ac85e490.dng")
OUT = Path("/tmp/v07_spike/sequences")


def run(cmd: list[str]) -> None:
    """Run exiftool, raising on non-zero exit."""
    res = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if res.returncode != 0:
        print("exiftool stderr:", res.stderr, file=sys.stderr)
        raise RuntimeError(f"exiftool failed: {' '.join(cmd)}")


def make_pair(
    test_name: str,
    frame2_tag_args: list[str],
    frame1_tag_args: list[str] | None = None,
) -> Path:
    """Copy SRC to two sequentially numbered DNGs; apply tag overrides per frame.

    `frame1_tag_args` lets the caller author an explicit baseline on frame 1
    (e.g. an identity ProfileToneCurve) so the two frames differ only in the
    tested tag.
    """
    pair_dir = OUT / test_name
    pair_dir.mkdir(parents=True, exist_ok=True)
    f1 = pair_dir / f"SPIKE_{test_name}_0001.dng"
    f2 = pair_dir / f"SPIKE_{test_name}_0002.dng"
    shutil.copy2(SRC, f1)
    shutil.copy2(SRC, f2)
    if frame1_tag_args:
        run(["exiftool", "-overwrite_original", *frame1_tag_args, str(f1)])
    run(["exiftool", "-overwrite_original", *frame2_tag_args, str(f2)])
    return pair_dir


# --- T4 GainMap binary builder ------------------------------------------------


def build_gainmap_opcode_list3(
    image_width: int,
    image_height: int,
    gain: float,
) -> bytes:
    """Build a DNG OpcodeList3 binary containing one GainMap opcode that applies
    a uniform multiplicative gain to the whole image.

    Format per Adobe DNG 1.4.0.0 specification, "Camera Profiles" §6.1
    OpcodeList3 + §7.3 GainMap. All values big-endian.

    Layout:
      OpcodeList3 header:
        uint32  number of opcodes (= 1)
      Per-opcode header:
        uint32  OpcodeID            = 9 (GainMap)
        uint32  DNG version         = 0x01030000 (DNG 1.3+)
        uint32  Flags               = 1 (optional)
        uint32  ParameterSize       = 80 (bytes of data following — header
                                          fields are 4*4 + 6*4 + 4*8 + 4 + 4
                                          for a 1×1 uniform map with 1 plane)
      GainMap data (80 bytes total for 1×1 single-plane uniform map):
        uint32 ×4  Rectangle (top, left, bottom, right)
        uint32     Plane      (= 0 — apply to all planes)
        uint32     Planes     (= 1 — same gain across channels)
        uint32     RowPitch   (= 1)
        uint32     ColPitch   (= 1)
        uint32     MapPointsV (= 1 — 1×1 uniform map)
        uint32     MapPointsH (= 1)
        double     MapSpacingV
        double     MapSpacingH
        double     MapOriginV
        double     MapOriginH
        uint32     MapPlanes  (= 1)
        float ×N   gain values   (= 1 entry of `gain`)
    """
    opcode_id = 9
    dng_version = 0x01030000
    flags = 1
    # Rectangle covers whole image.
    rect = struct.pack(">4i", 0, 0, image_height, image_width)
    plane_fields = struct.pack(">6i", 0, 1, 1, 1, 1, 1)  # plane, planes, rp, cp, mpV, mpH
    spacing_origin = struct.pack(">4d", 1.0, 1.0, 0.0, 0.0)
    map_planes = struct.pack(">i", 1)
    gain_floats = struct.pack(">f", gain)
    gainmap_data = rect + plane_fields + spacing_origin + map_planes + gain_floats
    assert len(gainmap_data) == 80, f"Expected 80 bytes of GainMap data, got {len(gainmap_data)}"
    opcode_header = struct.pack(
        ">4I",
        opcode_id,
        dng_version,
        flags,
        len(gainmap_data),
    )
    return struct.pack(">I", 1) + opcode_header + gainmap_data


# --- T6 ProfileLookTable builder ----------------------------------------------


def build_looktable_identity(divs: tuple[int, int, int]) -> tuple[bytes, str]:
    """Identity HSV LookTable: zero deltas. Returns (binary blob, dim string)."""
    v, h, ll = divs
    floats = struct.pack(f"{v * h * ll * 3}f", *([0.0] * (v * h * ll * 3)))
    return floats, f"{v} {h} {ll}"


def build_looktable_satboost(divs: tuple[int, int, int], sat_delta: float) -> tuple[bytes, str]:
    """HSV LookTable applying a uniform saturation scale: output_sat = input_sat × (1 + sat_delta).

    Each entry is (hue_shift, sat_scale_minus_1, val_scale_minus_1); we only set
    the second channel to `sat_delta` (so 1.0 = +100% saturation = 2× sat).
    """
    v, h, ll = divs
    n = v * h * ll
    triples: list[float] = []
    for _ in range(n):
        triples.extend([0.0, sat_delta, 0.0])
    floats = struct.pack(f"{n * 3}f", *triples)
    return floats, f"{v} {h} {ll}"


def main() -> None:
    if not SRC.exists():
        sys.exit(f"source DNG missing: {SRC}")
    OUT.mkdir(parents=True, exist_ok=True)

    # T1 — AsShotNeutral. Source is daylight (0.5, 1, 0.776). Make frame 2 tungsten-warm.
    #    Tungsten WB has the red channel less dim and blue more dim (camera "white" is yellow).
    t1_dir = make_pair(
        "T1_AsShotNeutral",
        ["-AsShotNeutral=0.85 1 0.45"],
    )
    print(f"T1 (AsShotNeutral)    : {t1_dir}")
    print("  frame 1 = daylight (source)  0.50 1 0.78")
    print("  frame 2 = tungsten            0.85 1 0.45")

    # T2 — BaselineExposure. Source is 0.1. Make frame 2 +2.0.
    t2_dir = make_pair(
        "T2_BaselineExp",
        ["-BaselineExposure=2.0"],
    )
    print(f"T2 (BaselineExposure) : {t2_dir}")
    print("  frame 1 = source BE 0.1")
    print("  frame 2 = +2.0 EV")

    # T3 — ProfileToneCurve. Identity tone curve on frame 1; aggressive S-curve
    # on frame 2. ProfileToneCurve = list of (input, output) pairs in [0, 1].
    # S-curve sampled at 0, 0.25, 0.5, 0.75, 1 -> 0, 0.10, 0.5, 0.90, 1.
    identity_tc = "0 0 1 1"
    s_curve_tc = "0 0 0.25 0.10 0.5 0.5 0.75 0.90 1 1"
    t3_dir = make_pair(
        "T3_ProfileToneCurve",
        [f"-ProfileToneCurve={s_curve_tc}"],
        frame1_tag_args=[f"-ProfileToneCurve={identity_tc}"],
    )
    print(f"T3 (ProfileToneCurve) : {t3_dir}")
    print("  frame 1 = identity tone curve")
    print("  frame 2 = aggressive S-curve (deeper shadows, lifted highlights)")

    # T4 — OpcodeList3 GainMap. No opcode on frame 1; uniform 2× gain on frame 2.
    # We build the binary blob and feed it via exiftool's `<=` (file) syntax.
    gainmap_blob_dir = Path("/tmp/v07_spike/blobs")
    gainmap_blob_dir.mkdir(parents=True, exist_ok=True)
    gainmap_blob_path = gainmap_blob_dir / "gainmap_2x.bin"
    gainmap_blob_path.write_bytes(
        build_gainmap_opcode_list3(image_width=6032, image_height=4032, gain=2.0),
    )
    print(f"  built {gainmap_blob_path} ({gainmap_blob_path.stat().st_size} bytes)")
    # `#` after the tag name disables exiftool's PrintConv (binary tag,
    # value is raw bytes). `SubIFD:` qualifier targets the raw IFD where
    # OpcodeList3 lives in a DNG.
    t4_dir = make_pair(
        "T4_OpcodeList3_GainMap",
        [f"-SubIFD:OpcodeList3#<={gainmap_blob_path}"],
    )
    print(f"T4 (OpcodeList3.GainMap) : {t4_dir}")
    print("  frame 1 = no opcode (source)")
    print("  frame 2 = uniform 2× GainMap across full image")

    # T6 — ProfileLookTableData. Identity HSV cube on frame 1; +1.0 saturation
    # delta (i.e. 2× saturation) on frame 2. Use a 6×6×6 cube.
    lut_blob_dir = Path("/tmp/v07_spike/blobs")
    lut_blob_dir.mkdir(parents=True, exist_ok=True)
    identity_blob_path = lut_blob_dir / "looktable_identity_6x6x6.bin"
    sat_blob_path = lut_blob_dir / "looktable_sat2x_6x6x6.bin"
    identity_blob, dims = build_looktable_identity((6, 6, 6))
    sat_blob, _ = build_looktable_satboost((6, 6, 6), sat_delta=1.0)
    identity_blob_path.write_bytes(identity_blob)
    sat_blob_path.write_bytes(sat_blob)
    t6_dir = make_pair(
        "T6_ProfileLookTableData",
        [
            f"-ProfileLookTableDims={dims}",
            f"-ProfileLookTableData#<={sat_blob_path}",
        ],
        frame1_tag_args=[
            f"-ProfileLookTableDims={dims}",
            f"-ProfileLookTableData#<={identity_blob_path}",
        ],
    )
    print(f"T6 (ProfileLookTableData) : {t6_dir}")
    print("  frame 1 = 6×6×6 identity LookTable (zero deltas)")
    print("  frame 2 = 6×6×6 uniform +1.0 saturation delta (2× sat)")

    # Sanity dump.
    tags_by_test = {
        "T1_AsShotNeutral": ["-AsShotNeutral", "-BaselineExposure"],
        "T2_BaselineExp": ["-AsShotNeutral", "-BaselineExposure"],
        "T3_ProfileToneCurve": ["-ProfileToneCurve"],
        "T4_OpcodeList3_GainMap": ["-OpcodeList3"],
        "T6_ProfileLookTableData": ["-ProfileLookTableDims", "-ProfileLookTableData#"],
    }
    print()
    print("Verification — frame 1 vs frame 2 metadata:")
    for test_name, tags in tags_by_test.items():
        for n in (1, 2):
            p = OUT / test_name / f"SPIKE_{test_name}_{n:04d}.dng"
            if not p.exists():
                continue
            res = subprocess.run(
                ["exiftool", *tags, str(p)],
                check=True,
                capture_output=True,
                text=True,
            )
            print(f"  {p.name}")
            for line in res.stdout.strip().splitlines():
                # Truncate long binary dumps.
                if len(line) > 160:
                    line = line[:160] + " …(truncated)"
                print(f"      {line}")


if __name__ == "__main__":
    main()
