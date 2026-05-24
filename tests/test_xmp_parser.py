"""LRT XMP parser tests against synthetic fixtures."""

from pathlib import Path

import pytest

from lrt_cinema.xmp_parser import parse_sequence, parse_xmp_file

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_keyframe_a_fields():
    ops, is_kf, deflicker, _rating, _mask = parse_xmp_file(FIXTURES / "synthetic_keyframe_a.xmp")
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
    ops, is_kf, deflicker, _rating, _mask = parse_xmp_file(FIXTURES / "synthetic_keyframe_b.xmp")
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
    ops, is_kf, deflicker, _rating, _mask = parse_xmp_file(
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


def test_parse_real_lrt_mask_offsets():
    # ADVERSARIAL_AUDIT_2026-05-23 HIGH-2: real LRT 7.5.3 writes HG /
    # Deflicker / Global per-frame deltas inside crs:MaskGroupBasedCorrections,
    # not as top-level lrt:* attributes. Parser must walk that container,
    # match #LRT internal use ({HG,Deflicker,Global}) names, extract
    # crs:LocalExposure2012, and FILTER ZEROS (initialized-but-unused).
    _ops, _is_kf, _delta, _rating, mask_offsets = parse_xmp_file(
        FIXTURES / "synthetic_real_lrt_mask_offsets.xmp",
    )
    # Fixture has 4 mask entries: HG=0.25 + Deflicker=-0.075 + Global=0.0 +
    # user "LRT Mask 1"=0.5. Expect HG + Deflicker only (Global is 0.0
    # → filtered; user mask isn't a recognized internal-use name).
    kinds = sorted(k for k, _ in mask_offsets)
    assert kinds == ["deflicker", "hg"]
    by_kind = dict(mask_offsets)
    assert by_kind["hg"] == pytest.approx(0.25)
    assert by_kind["deflicker"] == pytest.approx(-0.075)


def test_parse_sequence_collects_lrt_mask_offsets(tmp_path):
    # Verify parse_sequence ingests mask offsets into seq.lrt_mask_offsets
    # with the right frame_index + kind + delta.
    raw = tmp_path / "DSC_5059.NEF"
    raw.write_bytes(b"raw-stub")
    (tmp_path / "DSC_5059.NEF.xmp").write_text(
        (FIXTURES / "synthetic_real_lrt_mask_offsets.xmp").read_text(),
    )
    seq = parse_sequence(tmp_path)
    assert len(seq.lrt_mask_offsets) == 2
    by_kind = {o.kind: o for o in seq.lrt_mask_offsets}
    assert by_kind["hg"].frame_index == 0
    assert by_kind["hg"].exposure_delta_ev == pytest.approx(0.25)
    assert by_kind["deflicker"].frame_index == 0
    assert by_kind["deflicker"].exposure_delta_ev == pytest.approx(-0.075)


def test_parse_real_lrt_fixture_uses_xmp_rating():
    ops, is_kf, deflicker, rating, _mask = parse_xmp_file(
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
    # Pre-Auto-Transition LRT state: keyframes flagged via rating=4 carry
    # creative intent; non-keyframes are rating=0 with crs:Exposure2012=0
    # (the LR/LRT default value before any interpolation has run). Only
    # the rated frames should land in seq.keyframes.
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
    # Two keyframes (frames 0 and 2), middle frame is rating=0 with
    # default EV → meaningful_ops=False → skipped.
    assert seq.keyframe_indices() == [0, 2]


def test_parse_sequence_honors_lrt_auto_transition_per_frame_values(tmp_path):
    # Post-Auto-Transition LRT state: LRT has written its own interpolated
    # values into every per-frame XMP. Rating-4 keyframes flank rating-0
    # interpolated frames, but every frame now carries non-default EV.
    # lrt-cinema MUST ingest all frames as Keyframes in the IR so that
    # interpolate() returns LRT's per-frame value directly — otherwise
    # we'd throw away LRT's interpolation and re-derive a different
    # curve from only the 2 rating-4 frames.
    raw_files = [
        tmp_path / f"DSC_{i:04d}.NEF" for i in (4053, 4054, 4055, 5059)
    ]
    for raw in raw_files:
        raw.write_bytes(b"raw-stub")

    real_kf = (FIXTURES / "synthetic_real_lrt_keyframe.xmp").read_text()
    # LRT-interpolated intermediate frames: rating=0 BUT non-default EV.
    def lrt_interp(ev: str) -> str:
        return real_kf.replace(
            'xmp:Rating="4"', 'xmp:Rating="0"',
        ).replace(
            'crs:Exposure2012="-0.500000"', f'crs:Exposure2012="{ev}"',
        )
    # Keyframe at 4053 (EV 0.0), keyframe at 5059 (EV -0.5), with two
    # LRT-interpolated frames between (linearly between, in a real
    # sequence these would be 1006 frames apart but we use 4 for the test).
    kf_zero = real_kf.replace('crs:Exposure2012="-0.500000"', 'crs:Exposure2012="0.000000"')
    (tmp_path / "DSC_4053.NEF.xmp").write_text(kf_zero)
    (tmp_path / "DSC_4054.NEF.xmp").write_text(lrt_interp("-0.166667"))  # 1/3 of way
    (tmp_path / "DSC_4055.NEF.xmp").write_text(lrt_interp("-0.333333"))  # 2/3 of way
    (tmp_path / "DSC_5059.NEF.xmp").write_text(real_kf)  # rating=4, EV=-0.5

    seq = parse_sequence(tmp_path)
    # All four frames should be ingested — the two rating=0 frames
    # carry LRT-interpolated EV intent that must NOT be discarded.
    assert seq.keyframe_indices() == [0, 1, 2, 3]
    # Only the rating=4 frames are flagged is_lrt_keyframe=True;
    # the interpolated ones are still keyframes in our IR (they
    # carry intent) but not LRT-marked.
    by_idx = {kf.frame_index: kf for kf in seq.keyframes}
    assert by_idx[0].is_lrt_keyframe is True
    assert by_idx[1].is_lrt_keyframe is False
    assert by_idx[2].is_lrt_keyframe is False
    assert by_idx[3].is_lrt_keyframe is True
    # interpolate() must return LRT's per-frame values verbatim (exact
    # match short-circuits any re-interpolation on our side).
    from lrt_cinema.interpolation import interpolate
    assert interpolate(seq, 0).exposure_ev == pytest.approx(0.0)
    assert interpolate(seq, 1).exposure_ev == pytest.approx(-0.166667)
    assert interpolate(seq, 2).exposure_ev == pytest.approx(-0.333333)
    assert interpolate(seq, 3).exposure_ev == pytest.approx(-0.5)


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


def test_parse_float_rejects_nan_and_inf():
    """Hostile/corrupted XMPs may carry NaN/Inf. These must not propagate
    into struct.pack — they render frames solid black with no diagnostic."""
    from lrt_cinema.xmp_parser import _parse_float

    assert _parse_float("NaN") == 0.0
    assert _parse_float("nan") == 0.0
    assert _parse_float("inf") == 0.0
    assert _parse_float("-inf") == 0.0
    assert _parse_float("Infinity") == 0.0
    assert _parse_float("NaN", default=-1.0) == -1.0
    assert _parse_float("+1.5") == 1.5
    assert _parse_float("-0.5") == -0.5


def test_parse_sequence_warns_on_corrupted_xmp(tmp_path, capsys):
    """A corrupted XMP must NOT silently degrade the frame to defaults
    (would produce a flat frame mid-graded-sequence with no diagnostic).
    Surface the skip so the user can investigate."""
    (tmp_path / "frame_0001.CR3").write_bytes(b"raw-stub")
    (tmp_path / "frame_0002.CR3").write_bytes(b"raw-stub")
    (tmp_path / "frame_0001.CR3.xmp").write_text(
        (FIXTURES / "synthetic_keyframe_a.xmp").read_text()
    )
    (tmp_path / "frame_0002.CR3.xmp").write_bytes(
        b"<this is not valid xml: half-written file"
    )

    seq = parse_sequence(tmp_path)
    err = capsys.readouterr().err
    assert "warning: skipping unreadable XMP frame_0002.CR3.xmp" in err
    # The valid frame still loads.
    assert len(seq.keyframes) == 1
