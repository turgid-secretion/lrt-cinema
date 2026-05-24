"""darktable XMP emitter tests."""

import struct
import xml.etree.ElementTree as ET

from lrt_cinema.ir import DevelopOps
from lrt_cinema.xmp_emitter import DT_NS, RDF_NS, emit_darktable_xmp


def _parse(path):
    return ET.parse(path).getroot()


def test_emitter_writes_well_formed_xml(tmp_path):
    out = tmp_path / "frame.xmp"
    emit_darktable_xmp(DevelopOps(exposure_ev=1.0), out)
    root = _parse(out)
    assert root.tag.endswith("xmpmeta")


def test_emitter_wraps_in_xpacket_for_exiv2_compat(tmp_path):
    # darktable's XMP reader (Exiv2) requires the standard XMP packet
    # wrapper. Without "<?xpacket begin=...?>" / "<?xpacket end=...?>"
    # dt rejects the sidecar with the misleading "can't open XMP file"
    # error. The W5M0Mp... id is the canonical Adobe packet marker.
    out = tmp_path / "frame.xmp"
    emit_darktable_xmp(DevelopOps(exposure_ev=0.5), out)
    raw = out.read_bytes()
    assert raw.startswith(b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>')
    assert raw.rstrip().endswith(b'<?xpacket end="w"?>')


def test_emitter_writes_required_dt5_description_attrs(tmp_path):
    # dt master xmp_version=5 reader requires four attributes on the
    # rdf:Description element to treat the sidecar as fully-specified
    # (src/common/exif.cc#L4065 xmp_version, L4094 auto_presets_applied,
    # L4119-4134 iop_order_version, history_end at L5191-5202 writer).
    # Absent auto_presets_applied, dt re-runs its workflow auto-apply
    # machinery on every render (develop.c#L1822-2106), making output
    # non-deterministic against the runtime workflow conf. See
    # docs/research/DT_WORKFLOW_EXPOSURE_INTERACTION.md.
    out = tmp_path / "frame.xmp"
    emit_darktable_xmp(DevelopOps(exposure_ev=0.0), out)
    root = _parse(out)
    desc = next(root.iter(f"{{{RDF_NS}}}Description"))
    assert desc.get(f"{{{DT_NS}}}xmp_version") == "5"
    assert desc.get(f"{{{DT_NS}}}iop_order_version") == "4"  # V50 RAW
    assert desc.get(f"{{{DT_NS}}}auto_presets_applied") == "1"
    assert desc.get(f"{{{DT_NS}}}history_end") == "1"


def test_emitter_includes_exposure_operation(tmp_path):
    out = tmp_path / "frame.xmp"
    emit_darktable_xmp(DevelopOps(exposure_ev=1.25), out)
    root = _parse(out)
    operations = [
        li.get(f"{{{DT_NS}}}operation")
        for li in root.iter(f"{{{RDF_NS}}}li")
    ]
    assert "exposure" in operations


def test_emitter_does_not_emit_blendop_attrs(tmp_path):
    # ADVERSARIAL_AUDIT_2026-05-23 HIGH-1: emitter's prior
    # blendop_version="11" + 64-byte zero blendop_params was rejected by dt
    # 5.5's reader (logged "blendop v. 11: version WRONG params WRONG") and
    # silently substituted with module->default_blendop_params. We now omit
    # blendop_* attrs entirely, which takes the same dt code path with no
    # version-WRONG warning. Guard against regression.
    out = tmp_path / "frame.xmp"
    emit_darktable_xmp(DevelopOps(exposure_ev=1.0), out)
    root = _parse(out)
    for li in root.iter(f"{{{RDF_NS}}}li"):
        assert li.get(f"{{{DT_NS}}}blendop_version") is None, (
            "blendop_version must not be emitted — see audit HIGH-1"
        )
        assert li.get(f"{{{DT_NS}}}blendop_params") is None, (
            "blendop_params must not be emitted — see audit HIGH-1"
        )


def test_emitter_does_not_emit_temperature_module_pre_calibration(tmp_path):
    # Pre-calibration we deliberately do NOT emit the temperature module
    # even when temperature_k is set, because neutral 1.0 multipliers
    # produce a green cast (the as-shot AWB darktable would otherwise
    # apply is correct). When the DCP-driven kelvin→multiplier
    # calibration ships, this test flips to assert emission.
    for kelvin in (None, 5500):
        out = tmp_path / f"frame_{kelvin}.xmp"
        emit_darktable_xmp(DevelopOps(temperature_k=kelvin), out)
        root = _parse(out)
        operations = [
            li.get(f"{{{DT_NS}}}operation")
            for li in root.iter(f"{{{RDF_NS}}}li")
        ]
        assert "temperature" not in operations


def test_exposure_params_roundtrip(tmp_path):
    # dt master exposure modversion 7 struct: mode int + 4 floats + 2 gbooleans
    # (4 bytes each). Total 28 bytes. compensate_hilite_pres added in v7 with
    # default TRUE. See docs/reference/darktable/MODULES.md.
    #
    # ENCODING: hex ASCII (lowercase 0-9a-f). dt's XMP reader at
    # src/common/exif.cc#L3252-3270 runs strspn against [0-9a-f] and silently
    # falls back to module defaults on any other encoding (e.g. base64).
    # This test guards against that regression.
    out = tmp_path / "frame.xmp"
    emit_darktable_xmp(DevelopOps(exposure_ev=2.5), out)
    root = _parse(out)
    for li in root.iter(f"{{{RDF_NS}}}li"):
        if li.get(f"{{{DT_NS}}}operation") == "exposure":
            params_hex = li.get(f"{{{DT_NS}}}params")
            # Must be pure hex (dt's strspn check).
            assert all(c in "0123456789abcdef" for c in params_hex), (
                f"params must be hex-encoded, got {params_hex!r}"
            )
            decoded = bytes.fromhex(params_hex)
            assert len(decoded) == 28, f"expected 28-byte v7 struct, got {len(decoded)}"
            mode, black, exposure, perc, target, comp_bias, comp_hilite = (
                struct.unpack("<iffffii", decoded)
            )
            assert mode == 0
            assert black == 0.0
            assert exposure == 2.5
            assert perc == 50.0
            assert target == -4.0
            assert comp_bias == 0   # default FALSE
            assert comp_hilite == 1  # default TRUE per v7 introduction
            # And modversion should be "7" on the rdf:li
            assert li.get(f"{{{DT_NS}}}modversion") == "7"
            break
    else:
        raise AssertionError("exposure entry missing")
