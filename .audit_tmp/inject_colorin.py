#!/usr/bin/env python3
"""TEMPORARY audit/research script (gitignored). Injects a darktable
colorin history entry into an existing dt XMP sidecar, forcing colorin
type = DT_COLORSPACE_STANDARD_MATRIX (=11) instead of the default
ENHANCED_MATRIX (=12). Then re-runs dt-cli. Measures whether dt's
"standard" matrix (dcraw-derived from Adobe coeffs) closes the
matrix-stage gap vs the default enhanced matrix.

Not for committing; used to size the colorin-override fix before
deciding the v0.6 implementation.
"""
import struct
import sys
from pathlib import Path
import subprocess
import re

XMP = Path("/tmp/v04_test_output/DSC_4053.NEF.dt.xmp")
SRC = Path("/tmp/v04_test_input/DSC_4053.NEF")
OUT = Path("/tmp/v04_test_output/DSC_4053.tif")
CUBE_DIR = Path("/tmp/v04_test_output")

# dt_iop_colorin_params_t layout from colorin.c#L71-81:
#   dt_colorspaces_color_profile_type_t type    // 4
#   char filename[512]                           // 512
#   dt_iop_color_intent_t intent                 // 4
#   dt_iop_color_normalize_t normalize           // 4
#   gboolean blue_mapping                        // 4 (gboolean = gint = int32)
#   dt_colorspaces_color_profile_type_t type_work// 4
#   char filename_work[512]                      // 512
# Total: 1044 bytes
def encode_colorin_params(type_enum, intent=0, normalize=0, blue_mapping=0, type_work=4):
    filename = b"\x00" * 512
    filename_work = b"\x00" * 512
    payload = (
        struct.pack("<i", type_enum)
        + filename
        + struct.pack("<iii", intent, normalize, blue_mapping)
        + struct.pack("<i", type_work)
        + filename_work
    )
    print(f"colorin params size: {len(payload)} bytes")
    return payload.hex()

text = XMP.read_text()
nums = [int(n) for n in re.findall(r'darktable:num="(\d+)"', text)]
next_num = max(nums) + 1 if nums else 0
print(f"existing history nums: {sorted(set(nums))}, injecting at {next_num}")

# Force colorin to STANDARD_MATRIX (=11). All other fields at default.
params = encode_colorin_params(type_enum=11)
li = (
    f'<rdf:li darktable:num="{next_num}" '
    f'darktable:operation="colorin" '
    f'darktable:enabled="1" '
    f'darktable:modversion="7" '
    f'darktable:params="{params}" '
    f'darktable:multi_name="" '
    f'darktable:multi_priority="0"/>'
)
new_text = text.replace("</rdf:Seq>", li + "</rdf:Seq>", 1)
new_text = re.sub(r'darktable:history_end="\d+"', f'darktable:history_end="{next_num + 1}"', new_text)
XMP.write_text(new_text)
print(f"wrote injected XMP: {XMP}")

argv = [
    "darktable-cli",
    str(SRC.resolve()),
    str(XMP.resolve()),
    str(OUT.resolve()),
    "--apply-custom-presets", "0",
    "--icc-type", "LIN_REC2020", "--icc-intent", "RELATIVE_COLORIMETRIC",
    "--core",
    "--conf", "plugins/imageio/format/tiff/bpp=16",
    "--conf", "plugins/imageio/format/tiff/compress=0",
    "--conf", "plugins/imageio/format/tiff/pixelformat=0",
    "--conf", f"plugins/darkroom/lut3d/def_path={CUBE_DIR.resolve()}",
]
result = subprocess.run(argv, capture_output=True, text=True, timeout=600)
print(f"dt-cli rc: {result.returncode}")
if result.returncode != 0:
    print("stderr:", result.stderr[-1500:])
    sys.exit(1)
print(f"rendered: {OUT} ({OUT.stat().st_size} bytes)")
