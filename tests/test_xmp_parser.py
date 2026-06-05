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
    assert ops.sharpen_radius == 1.5            # crs:SharpenRadius parsed when present
    assert ops.sharpen_detail == 55.0          # crs:SharpenDetail parsed when present
    assert ops.sharpen_edge_masking == 40.0    # crs:SharpenEdgeMasking parsed when present


def test_parse_keyframe_b_with_deflicker():
    ops, is_kf, deflicker, _rating, _mask = parse_xmp_file(FIXTURES / "synthetic_keyframe_b.xmp")
    assert ops.sharpen_radius == 1.0           # absent crs:SharpenRadius → ACR default 1.0
    assert ops.sharpen_detail == 25.0          # absent → ACR default 25
    assert ops.sharpen_edge_masking == 0.0     # absent → ACR default 0
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


def _write_xmp(tmp_path: Path, crs_attrs: str, name: str = "DSC_0001.NEF") -> Path:
    """Write a one-Description XMP carrying `crs_attrs` and its RAW stub."""
    raw = tmp_path / name
    raw.write_bytes(b"raw-stub")
    xmp = f"""<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
    {crs_attrs}/>
 </rdf:RDF>
</x:xmpmeta>
"""
    (tmp_path / f"{name}.xmp").write_text(xmp)
    return raw


def test_parse_hsl_round_trip(tmp_path):
    """The 8-band HSL tags parse into HslBands in named order (Red…Magenta)."""
    _write_xmp(
        tmp_path,
        'crs:SaturationAdjustmentRed="50" crs:SaturationAdjustmentBlue="-30" '
        'crs:HueAdjustmentGreen="12" crs:LuminanceAdjustmentMagenta="-40"',
    )
    seq = parse_sequence(tmp_path)
    assert len(seq.keyframes) == 1          # HSL intent alone flags a keyframe
    hsl = seq.keyframes[0].ops.hsl
    assert hsl.saturation[0] == 50.0        # Red
    assert hsl.saturation[5] == -30.0       # Blue
    assert hsl.hue[3] == 12.0               # Green
    assert hsl.luminance[7] == -40.0        # Magenta
    assert not hsl.is_identity()


def test_hsl_only_frame_is_flagged_meaningful(tmp_path):
    """A frame with only HSL intent (no xmp:Rating) is detected as a keyframe via
    _has_meaningful_ops — proves HSL is threaded into the keyframe heuristic."""
    from lrt_cinema.ir import DevelopOps, HslBands
    from lrt_cinema.xmp_parser import _has_meaningful_ops
    assert _has_meaningful_ops(DevelopOps()) is False
    assert _has_meaningful_ops(
        DevelopOps(hsl=HslBands(saturation=(20.0, 0, 0, 0, 0, 0, 0, 0))),
    ) is True


def test_parse_texture_clarity_round_trip(tmp_path):
    """Texture/Clarity parse from the real ACR tags (crs:Texture is PV-less;
    crs:Clarity2012 is PV2012-suffixed) into DevelopOps, and flag a keyframe."""
    _write_xmp(tmp_path, 'crs:Texture="35" crs:Clarity2012="-20"')
    seq = parse_sequence(tmp_path)
    assert len(seq.keyframes) == 1          # Texture/Clarity intent flags a keyframe
    ops = seq.keyframes[0].ops
    assert ops.texture == 35.0
    assert ops.clarity == -20.0


def test_parse_texture_clarity_2012_alias(tmp_path):
    """A 2012-suffixed Texture tag (the spec-named alias / older serialisation)
    still drives the slider via the fallback — and a PV-less Clarity tag likewise."""
    _write_xmp(tmp_path, 'crs:Texture2012="40" crs:Clarity="15"')
    ops = parse_sequence(tmp_path).keyframes[0].ops
    assert ops.texture == 40.0
    assert ops.clarity == 15.0


def test_texture_clarity_only_frame_is_flagged_meaningful():
    """A frame carrying only Texture or Clarity intent is detected as a keyframe
    via _has_meaningful_ops — proves the fields are threaded into the heuristic (a
    field omitted there is silently dropped per-frame)."""
    from lrt_cinema.ir import DevelopOps
    from lrt_cinema.xmp_parser import _has_meaningful_ops
    assert _has_meaningful_ops(DevelopOps()) is False
    assert _has_meaningful_ops(DevelopOps(texture=10.0)) is True
    assert _has_meaningful_ops(DevelopOps(clarity=-10.0)) is True


def test_parse_color_grade_round_trip(tmp_path):
    """ColorGrade* tags parse into the four wheels; Blending defaults to 50."""
    _write_xmp(
        tmp_path,
        'crs:ColorGradeShadowHue="220" crs:ColorGradeShadowSat="40" '
        'crs:ColorGradeShadowLum="-10" crs:ColorGradeMidtoneSat="25" '
        'crs:ColorGradeHighlightHue="45" crs:ColorGradeHighlightSat="30" '
        'crs:ColorGradeGlobalSat="15" crs:ColorGradeBalance="-20"',
    )
    seq = parse_sequence(tmp_path)
    assert len(seq.keyframes) == 1            # Color-Grade intent flags a keyframe
    cg = seq.keyframes[0].ops.color_grade
    assert cg.shadow_hue == 220.0
    assert cg.shadow_sat == 40.0
    assert cg.shadow_lum == -10.0
    assert cg.midtone_sat == 25.0
    assert cg.highlight_hue == 45.0
    assert cg.global_sat == 15.0
    assert cg.balance == -20.0
    assert cg.blending == 50.0                # default when the tag is absent
    assert not cg.is_identity()


def test_parse_color_grade_splittoning_alias(tmp_path):
    """ACR aliases Shadow/Highlight Hue+Sat and Balance onto the legacy
    crs:SplitToning* tags. A pure Split-Toning XMP (no ColorGrade* tags) must
    therefore still drive the Shadow/Highlight wheels — else a real edit is a
    dead feature (the ColorGrade* tags take precedence when both are present)."""
    _write_xmp(
        tmp_path,
        'crs:SplitToningShadowHue="210" crs:SplitToningShadowSaturation="35" '
        'crs:SplitToningHighlightHue="50" crs:SplitToningHighlightSaturation="20" '
        'crs:SplitToningBalance="15"',
    )
    cg = parse_sequence(tmp_path).keyframes[0].ops.color_grade
    assert cg.shadow_hue == 210.0
    assert cg.shadow_sat == 35.0
    assert cg.highlight_hue == 50.0
    assert cg.highlight_sat == 20.0
    assert cg.balance == 15.0
    assert not cg.is_identity()


def test_color_grade_blending_alone_is_not_meaningful(tmp_path):
    """Blending/Balance/Hue without a Saturation or Luminance produce no tint, so
    they must NOT flag a keyframe — only an actual wheel tint counts."""
    from lrt_cinema.ir import ColorGrade, DevelopOps
    from lrt_cinema.xmp_parser import _has_meaningful_ops
    assert _has_meaningful_ops(DevelopOps(color_grade=ColorGrade(blending=50.0))) is False
    assert _has_meaningful_ops(
        DevelopOps(color_grade=ColorGrade(blending=80.0, balance=-40.0, shadow_hue=210.0)),
    ) is False
    assert _has_meaningful_ops(
        DevelopOps(color_grade=ColorGrade(shadow_sat=20.0)),
    ) is True


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


def test_parse_int_handles_non_finite_without_crashing():
    """crs:Temperature='1e999'/'inf' would make int(round(float(...))) raise
    OverflowError (an ArithmeticError, NOT a ValueError) → escapes
    parse_sequence's skip-and-warn handler → aborts the whole batch. Must
    degrade to None instead."""
    from lrt_cinema.xmp_parser import _parse_int

    assert _parse_int("1e999") is None
    assert _parse_int("inf") is None
    assert _parse_int("nan") is None
    assert _parse_int("5500") == 5500
    assert _parse_int("5500.0") == 5500


def test_try_finite_float_drops_non_finite():
    from lrt_cinema.xmp_parser import _try_finite_float

    assert _try_finite_float("nan") is None
    assert _try_finite_float("inf") is None
    assert _try_finite_float("garbage") is None
    assert _try_finite_float(None) is None
    assert _try_finite_float("+1.5") == 1.5


def test_parse_xmp_temperature_inf_degrades_to_none(tmp_path):
    """End-to-end: a non-finite Temperature degrades to None, not an
    OverflowError that aborts the batch (regression for the widened handler)."""
    xmp = tmp_path / "f.xmp"
    xmp.write_text(
        '<?xml version="1.0"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/" '
        'crs:Temperature="1e999" crs:Exposure2012="0.5"/>'
        "</rdf:RDF></x:xmpmeta>",
    )
    ops, *_ = parse_xmp_file(xmp)
    assert ops.temperature_k is None                 # degraded, not crashed
    assert ops.exposure_ev == pytest.approx(0.5)      # the rest parses fine


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
