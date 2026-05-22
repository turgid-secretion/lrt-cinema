"""darktable XMP emitter tests."""

import base64
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


def test_emitter_includes_exposure_operation(tmp_path):
    out = tmp_path / "frame.xmp"
    emit_darktable_xmp(DevelopOps(exposure_ev=1.25), out)
    root = _parse(out)
    operations = [
        li.get(f"{{{DT_NS}}}operation")
        for li in root.iter(f"{{{RDF_NS}}}li")
    ]
    assert "exposure" in operations


def test_emitter_includes_temperature_when_kelvin_set(tmp_path):
    out = tmp_path / "frame.xmp"
    emit_darktable_xmp(DevelopOps(temperature_k=5500), out)
    root = _parse(out)
    operations = [
        li.get(f"{{{DT_NS}}}operation")
        for li in root.iter(f"{{{RDF_NS}}}li")
    ]
    assert "temperature" in operations


def test_emitter_omits_temperature_when_kelvin_none(tmp_path):
    out = tmp_path / "frame.xmp"
    emit_darktable_xmp(DevelopOps(temperature_k=None), out)
    root = _parse(out)
    operations = [
        li.get(f"{{{DT_NS}}}operation")
        for li in root.iter(f"{{{RDF_NS}}}li")
    ]
    assert "temperature" not in operations


def test_exposure_params_roundtrip(tmp_path):
    out = tmp_path / "frame.xmp"
    emit_darktable_xmp(DevelopOps(exposure_ev=2.5), out)
    root = _parse(out)
    for li in root.iter(f"{{{RDF_NS}}}li"):
        if li.get(f"{{{DT_NS}}}operation") == "exposure":
            params_b64 = li.get(f"{{{DT_NS}}}params")
            decoded = base64.b64decode(params_b64)
            mode, black, exposure, perc, target, comp = struct.unpack("<iffffi", decoded)
            assert mode == 0
            assert black == 0.0
            assert exposure == 2.5
            assert perc == 50.0
            assert target == -4.0
            assert comp == 0
            break
    else:
        raise AssertionError("exposure entry missing")
