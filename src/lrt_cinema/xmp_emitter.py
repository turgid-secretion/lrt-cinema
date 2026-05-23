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

DT_XMP_VERSION = "5"
"""dt sidecar xmp_version. dt master writes "5" (src/common/exif.cc#L81 at SHA
635c0c55b6...); accepts 0..5, rejects >=6 (see docs/reference/darktable/
XMP_FORMAT.md). Our prior value "1" worked because dt's legacy reader accepts
lower versions, but is forward-incompatible with future bumps."""

DT_IOP_ORDER_VERSION = "4"
"""dt v50 (RAW) iop_order table identifier. Per src/common/iop_order.h#L128-140
at dt master: DT_IOP_ORDER_V50 = 4. Required by dt's XMP reader when
xmp_version is 4 or 5 (src/common/exif.cc#L4119-4134); if absent, dt falls
back to the LEGACY table — workable today but semantically wrong for sidecars
authored against dt 5.x module versions.

Pinning to V50 (not V50_JPG=5) because lrt-cinema is exclusively a RAW
workflow."""

DT_AUTO_PRESETS_APPLIED = "1"
"""Tells dt "I have already run the workflow/camera/lens auto-apply preset
injection — don't re-run it." Read at src/common/exif.cc#L4584-4591: if
absent, dt CLEARS the in-memory flag and `_dev_auto_apply_presets` runs full
body on every render (src/develop/develop.c#L1822-2106), which (a) is
non-deterministic across user dt installations with different workflow
settings, and (b) interacts with `--core --conf workflow=none` in
poorly-understood ways (see docs/research/DT_WORKFLOW_EXPOSURE_INTERACTION.md).

Setting this to "1" is the right behavior for a "render exactly what I asked,
no workflow injection" sidecar."""

EXPOSURE_MODVERSION = "7"
"""dt exposure module current modversion (src/iop/exposure.c#L47 in dt master).
Bumped from 6 to 7 in dt 5.x when `compensate_hilite_pres` gboolean was added
to the params struct. dt's legacy_params migration would handle v6→v7 on read,
but emitting the canonical current modversion is preferred."""

TEMPERATURE_MODVERSION = "3"
# Note: dt master is at modversion 4 (added int preset field). We don't emit
# the temperature module pre-calibration so this constant is unused. When the
# kelvin→multipliers calibration ships, bump to "4" and add the preset int.

BLENDOP_VERSION = "11"
EMPTY_BLENDOP_PARAMS = "00" * 64


def _encode_exposure_params(exposure_ev: float) -> str:
    """Encode darktable exposure module params (modversion 7) as HEX ASCII.

    Struct layout from src/iop/exposure.c#L66-75 at dt master SHA
    635c0c55b6... (DT_MODULE_INTROSPECTION(7, dt_iop_exposure_params_t)):

        dt_iop_exposure_mode_t mode      // enum = int32, default MANUAL=0
        float black                      // -1.0..1.0, default 0.0
        float exposure                   // -18.0..18.0 EV, default 0.0
        float deflicker_percentile       // 0..100, default 50.0
        float deflicker_target_level     // -18..18, default -4.0
        gboolean compensate_exposure_bias// = gint = int32, default FALSE=0
        gboolean compensate_hilite_pres  // ADDED in v7, default TRUE=1

    Total: 7 fields * 4 bytes = 28 bytes (no struct padding; all 4-aligned).

    ENCODING: hexadecimal ASCII (lowercase 0-9a-f), not base64. dt's XMP
    reader at src/common/exif.cc#L3252-3270 runs
    `strspn(input, "0123456789abcdef")` and rejects anything that fails.
    Base64 fails immediately because of `+`, `/`, `=` characters →
    dt silently substitutes `module->default_params` →
    pipe renders with dt's default exposure (0.7 in scene-referred
    workflows, 0.0 in workflow=none) regardless of what we wrote.
    This was a project-wide silent regression from the day-1 emitter;
    fixed 2026-05-23. See docs/research/DT_WORKFLOW_EXPOSURE_INTERACTION.md.
    """
    payload = struct.pack(
        "<iffffii",
        0,             # mode = manual
        0.0,           # black
        float(exposure_ev),
        50.0,          # deflicker_percentile
        -4.0,          # deflicker_target_level
        0,             # compensate_exposure_bias = FALSE (default)
        1,             # compensate_hilite_pres = TRUE (default per v7)
    )
    return payload.hex()


def _encode_temperature_params(kelvin: int, tint: int) -> str:
    """Encode darktable temperature module params as HEX ASCII.

    Layout (modversion 3): four float channel multipliers (R, G1, B, G2).
    A full kelvin→multipliers conversion needs the camera's input
    matrix from a DCP profile. Until that calibration ships (see
    SCOPE.md / src/lrt_cinema/presets/CALIBRATION.md) we do not emit
    the temperature module at all — see `emit_darktable_xmp` — so this
    helper is kept only for the calibrated path.
    """
    payload = struct.pack("<ffff", 1.0, 1.0, 1.0, 1.0)
    return payload.hex()


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
    desc.set(f"{{{DT_NS}}}iop_order_version", DT_IOP_ORDER_VERSION)
    desc.set(f"{{{DT_NS}}}auto_presets_applied", DT_AUTO_PRESETS_APPLIED)
    desc.set(f"{{{DT_NS}}}history_end", "1")

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
