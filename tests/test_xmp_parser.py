"""LRT XMP parser tests against synthetic fixtures."""

from pathlib import Path

import pytest

from lrt_cinema.xmp_parser import parse_sequence, parse_xmp_file

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_keyframe_a_fields():
    ops, is_kf, deflicker, _ramps, _rating = parse_xmp_file(FIXTURES / "synthetic_keyframe_a.xmp")
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
    ops, is_kf, deflicker, _ramps, _rating = parse_xmp_file(FIXTURES / "synthetic_keyframe_b.xmp")
    assert is_kf is True
    assert deflicker == pytest.approx(0.12)
    assert ops.exposure_ev == 2.5
    assert ops.temperature_k == 7500


def test_parse_tone_curve_from_seq():
    ops, _, _, _, _ = parse_xmp_file(FIXTURES / "synthetic_with_tone_curve.xmp")
    assert len(ops.tone_curve) == 5
    assert ops.tone_curve[0].x == 0.0
    assert ops.tone_curve[0].y == 0.0
    assert ops.tone_curve[2].x == pytest.approx(128 / 255)
    assert ops.tone_curve[2].y == pytest.approx(128 / 255)
    assert ops.tone_curve[-1].x == 1.0
    assert ops.tone_curve[-1].y == 1.0


def test_parse_multi_description_merges_intent():
    ops, is_kf, deflicker, _ramps, _rating = parse_xmp_file(
        FIXTURES / "synthetic_multi_description.xmp"
    )
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
        ops, _, _, _, _ = parse_xmp_file(path)
        assert ops.temperature_k == 5500
    finally:
        path.unlink()


def test_parse_real_lrt_fixture_uses_xmp_rating():
    ops, is_kf, deflicker, _ramps, rating = parse_xmp_file(
        FIXTURES / "synthetic_real_lrt_keyframe.xmp",
    )
    assert rating == 4
    assert is_kf is True
    assert deflicker is None
    assert ops.exposure_ev == pytest.approx(-0.5)
    # crs:Sharpness=25 is LR's default and should be parsed but should
    # not by itself make _has_meaningful_ops return True (verified by
    # the test below that uses a rating=0 frame).
    assert ops.sharpness == 25.0
    # Identity tone curve should be parsed but recognized as non-meaningful.
    assert len(ops.tone_curve) == 2


def test_parse_sequence_skips_non_keyframe_rated_frames(tmp_path):
    # Simulates real LRT: every frame has a sidecar, only rating-tagged
    # ones are keyframes. Without xmp:Rating-aware gating, all 3 frames
    # would be misclassified as keyframes because each carries identical
    # LR-default crs:Sharpness=25 and identity tone curves.
    raw_files = [
        tmp_path / f"DSC_{i:04d}.NEF" for i in (4053, 5059, 6065)
    ]
    for raw in raw_files:
        raw.write_bytes(b"raw-stub")

    real_kf = (FIXTURES / "synthetic_real_lrt_keyframe.xmp").read_text()
    # Non-keyframe: rating=0 + same LR defaults LRT writes everywhere.
    non_kf = real_kf.replace(
        'xmp:Rating="4"', 'xmp:Rating="0"',
    ).replace(
        'crs:Exposure2012="-0.500000"', 'crs:Exposure2012="0.000000"',
    )
    (tmp_path / "DSC_4053.NEF.xmp").write_text(real_kf)
    (tmp_path / "DSC_5059.NEF.xmp").write_text(non_kf)
    (tmp_path / "DSC_6065.NEF.xmp").write_text(real_kf)

    seq = parse_sequence(tmp_path)
    assert seq.source_frames == ["DSC_4053.NEF", "DSC_5059.NEF", "DSC_6065.NEF"]
    # Two keyframes (frames 0 and 2), middle frame is rating=0 and skipped.
    assert seq.keyframe_indices() == [0, 2]


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
