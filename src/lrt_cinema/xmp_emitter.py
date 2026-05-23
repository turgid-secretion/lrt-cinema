"""Emit darktable XMP history-stack sidecars from IR.

darktable reads a `<RAW>.xmp` sidecar to apply per-image edits and
respects it when invoked headlessly via `darktable-cli`. The history
stack is an rdf:Seq under `darktable:history`, with each `rdf:li`
carrying:

  - `darktable:operation` — module name (e.g. "exposure", "temperature")
  - `darktable:enabled` — "1" or "0"
  - `darktable:modversion` — module schema version (per-module, per-dt-release)
  - `darktable:params` — base64-encoded C struct, layout governed by
    operation + modversion. THIS IS THE CALIBRATION GAP. Version-tolerant
    emission requires either:
      (a) a per-darktable-version params encoder generated from headers
      (b) round-tripping through `darktable-cli --bpp ... --style` with a
          bundled `.style` file that darktable's importer normalizes
  - `darktable:blendop_*` — blend-op metadata (default = no blend)
  - `darktable:multi_*` — instance disambiguation (default = singleton)
  - `darktable:num` — execution order

Scaffold approach (v0.1):

  Emit a well-formed XMP that lists the operations our preset needs,
  with conservative modversions known to be stable across darktable 4.6
  through 5.4. Params for `exposure` and `temperature` modules are
  encoded with their known simple layouts (float + zeros, kelvin int +
  fine-tune). Complex modules (sigmoid, color balance rgb, tone curve)
  are emitted as DISABLED placeholders; the bundled preset .style file
  carries their actual values and is applied via `darktable-cli --style`.

  This is the "scaffold ships the shape, calibration ships the bytes"
  split called out in SCOPE.md.
"""

from __future__ import annotations

import base64
import io
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

from lrt_cinema.ir import DevelopOps

DT_NS = "http://darktable.sf.net/"
X_NS = "adobe:ns:meta/"
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
XMP_NS = "http://ns.adobe.com/xap/1.0/"
XMPMM_NS = "http://ns.adobe.com/xap/1.0/mm/"

DT_XMP_VERSION = "1"

EXPOSURE_MODVERSION = "6"
TEMPERATURE_MODVERSION = "3"

BLENDOP_VERSION = "11"
EMPTY_BLENDOP_PARAMS = "00" * 64


def _encode_exposure_params(exposure_ev: float) -> str:
    """Encode darktable exposure module params.

    Layout (modversion 6 — stable since darktable 3.0):
        mode: int32      (0 = manual)
        black: float     (0.0)
        exposure: float  (EV stops; +1.0 = +1 EV)
        deflicker_percentile: float (50.0 = default)
        deflicker_target_level: float (-4.0 = default)
        compensate_exposure_bias: int32 (0)
    """
    payload = struct.pack(
        "<iffffi",
        0,             # mode = manual
        0.0,           # black
        float(exposure_ev),
        50.0,          # deflicker_percentile
        -4.0,          # deflicker_target_level
        0,             # compensate_exposure_bias
    )
    return base64.b64encode(payload).decode("ascii")


def _encode_temperature_params(kelvin: int, tint: int) -> str:
    """Encode darktable temperature module params.

    Layout (modversion 3): four float channel multipliers (R, G1, B, G2).
    A full kelvin→multipliers conversion needs the camera's input
    matrix from a DCP profile. Until that calibration ships (see
    SCOPE.md / src/lrt_cinema/presets/CALIBRATION.md) we do not emit
    the temperature module at all — see `emit_darktable_xmp` — so this
    helper is kept only for the calibrated path.
    """
    payload = struct.pack("<ffff", 1.0, 1.0, 1.0, 1.0)
    return base64.b64encode(payload).decode("ascii")


def _make_history_entry(
    parent: ET.Element,
    num: int,
    operation: str,
    enabled: bool,
    modversion: str,
    params_b64: str,
) -> None:
    li = ET.SubElement(parent, f"{{{RDF_NS}}}li")
    li.set(f"{{{DT_NS}}}num", str(num))
    li.set(f"{{{DT_NS}}}operation", operation)
    li.set(f"{{{DT_NS}}}enabled", "1" if enabled else "0")
    li.set(f"{{{DT_NS}}}modversion", modversion)
    li.set(f"{{{DT_NS}}}params", params_b64)
    li.set(f"{{{DT_NS}}}multi_name", "")
    li.set(f"{{{DT_NS}}}multi_priority", "0")
    li.set(f"{{{DT_NS}}}blendop_version", BLENDOP_VERSION)
    li.set(f"{{{DT_NS}}}blendop_params", EMPTY_BLENDOP_PARAMS)


def emit_darktable_xmp(ops: DevelopOps, output_path: Path) -> None:
    """Emit a darktable XMP sidecar for `ops` to `output_path`.

    The sidecar is intended to live next to its RAW file
    (`<RAW>.xmp`) so `darktable-cli` picks it up automatically when
    processing that RAW.
    """
    ET.register_namespace("x", X_NS)
    ET.register_namespace("rdf", RDF_NS)
    ET.register_namespace("darktable", DT_NS)
    ET.register_namespace("xmp", XMP_NS)
    ET.register_namespace("xmpMM", XMPMM_NS)

    root = ET.Element(f"{{{X_NS}}}xmpmeta", {f"{{{X_NS}}}xmptk": "lrt-cinema"})
    rdf = ET.SubElement(root, f"{{{RDF_NS}}}RDF")
    desc = ET.SubElement(rdf, f"{{{RDF_NS}}}Description", {f"{{{RDF_NS}}}about": ""})
    desc.set(f"{{{DT_NS}}}xmp_version", DT_XMP_VERSION)

    history = ET.SubElement(desc, f"{{{DT_NS}}}history")
    seq = ET.SubElement(history, f"{{{RDF_NS}}}Seq")

    _make_history_entry(
        seq, num=0, operation="exposure", enabled=True,
        modversion=EXPOSURE_MODVERSION,
        params_b64=_encode_exposure_params(ops.exposure_ev),
    )

    # The temperature module is intentionally NOT emitted while the
    # kelvin → channel-multipliers calibration is outstanding (see
    # SCOPE.md / src/lrt_cinema/presets/CALIBRATION.md). Emitting
    # enabled neutral 1.0 multipliers — the only thing we could write
    # without a DCP profile — overrides darktable's correct as-shot
    # AWB and produces a green-cast render. Skipping the module lets
    # darktable's "white balance from camera" default apply, which is
    # the right behavior for a user setting exposure-only keyframes.
    # The parsed `temperature_k` value is therefore deliberately
    # dropped at emit; `lrt-cinema inspect` surfaces this as a warning.

    tree = ET.ElementTree(root)
    ET.indent(tree, space=" ", level=0)
    # darktable parses sidecars via Exiv2, which requires the standard
    # XMP packet wrapper ("<?xpacket ...?>") around the rdf:RDF document
    # to recognize it as a valid XMP. Without it, dt logs the misleading
    # "can't open XMP file" and the sidecar is ignored. The W5M0Mp...
    # id and "begin" BOM-byte are the canonical Adobe XMP packet markers
    # (see ISO 16684-1 §6.1, "XMP Packet Wrapper"). The trailing "w" in
    # the end marker indicates a writable packet (vs "r" read-only).
    _XMP_PACKET_BEGIN = b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    _XMP_PACKET_END = b'\n<?xpacket end="w"?>\n'
    buf = io.BytesIO()
    tree.write(buf, xml_declaration=False, encoding="utf-8")
    with open(output_path, "wb") as f:
        f.write(_XMP_PACKET_BEGIN)
        f.write(buf.getvalue())
        f.write(_XMP_PACKET_END)
