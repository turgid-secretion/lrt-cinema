"""LRT XMP parser tests against synthetic fixtures."""

from pathlib import Path

import pytest

from lrt_cinema.xmp_parser import parse_sequence, parse_xmp_file

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_keyframe_a_fields():
    ops, is_kf, deflicker = parse_xmp_file(FIXTURES / "synthetic_keyframe_a.xmp")
    assert is_kf is True
    assert deflicker is None
    assert ops.exposure_ev == 0.5
    assert ops.contrast == 15.0
    assert ops.highlights == -30.0
    assert ops.shadows == 25.0
    assert ops.whites == 10.0
    assert ops.blacks == -5.0
    assert ops.temperature_k == 5500
    assert ops.tint == 8
    assert ops.saturation == 5.0
    assert ops.vibrance == 10.0
    assert ops.sharpness == 40.0


def test_parse_keyframe_b_with_deflicker():
    ops, is_kf, deflicker = parse_xmp_file(FIXTURES / "synthetic_keyframe_b.xmp")
    assert is_kf is True
    assert deflicker == pytest.approx(0.12)
    assert ops.exposure_ev == 2.5
    assert ops.temperature_k == 7500


def test_parse_tone_curve_from_seq():
    ops, _, _ = parse_xmp_file(FIXTURES / "synthetic_with_tone_curve.xmp")
    assert len(ops.tone_curve) == 5
    assert ops.tone_curve[0].x == 0.0
    assert ops.tone_curve[0].y == 0.0
    assert ops.tone_curve[2].x == pytest.approx(128 / 255)
    assert ops.tone_curve[2].y == pytest.approx(128 / 255)
    assert ops.tone_curve[-1].x == 1.0
    assert ops.tone_curve[-1].y == 1.0


def test_parse_multi_description_merges_intent():
    ops, is_kf, deflicker = parse_xmp_file(FIXTURES / "synthetic_multi_description.xmp")
    assert is_kf is True
    assert ops.exposure_ev == 1.25
    assert ops.temperature_k == 5800
    assert deflicker == pytest.approx(0.07)


def test_parse_kelvin_as_float_text():
    import tempfile
    xmp = """<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
    crs:Temperature="5500.0"/>
 </rdf:RDF>
</x:xmpmeta>
"""
    with tempfile.NamedTemporaryFile(suffix=".xmp", mode="w", delete=False) as f:
        f.write(xmp)
        path = Path(f.name)
    try:
        ops, _, _ = parse_xmp_file(path)
        assert ops.temperature_k == 5500
    finally:
        path.unlink()


def test_parse_sequence_walks_folder(tmp_path):
    raw_a = tmp_path / "frame_0001.CR3"
    raw_a.write_bytes(b"raw-stub")
    raw_b = tmp_path / "frame_0002.CR3"
    raw_b.write_bytes(b"raw-stub")
    raw_c = tmp_path / "frame_0003.CR3"
    raw_c.write_bytes(b"raw-stub")

    (tmp_path / "frame_0001.CR3.xmp").write_text(
        (FIXTURES / "synthetic_keyframe_a.xmp").read_text()
    )
    (tmp_path / "frame_0003.CR3.xmp").write_text(
        (FIXTURES / "synthetic_keyframe_b.xmp").read_text()
    )

    seq = parse_sequence(tmp_path)
    assert seq.source_frames == ["frame_0001.CR3", "frame_0002.CR3", "frame_0003.CR3"]
    assert seq.keyframe_indices() == [0, 2]
    assert seq.deflicker_offsets[0].frame_index == 2
    assert seq.deflicker_offsets[0].exposure_delta_ev == pytest.approx(0.12)
