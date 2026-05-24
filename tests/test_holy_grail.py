"""Holy Grail exposure-ramp interpolation + parser tests."""

from pathlib import Path

import pytest

from lrt_cinema.interpolation import apply_holy_grail_ramps, materialize_all_frames
from lrt_cinema.ir import DevelopOps, HolyGrailRamp, Keyframe, LRTSequence
from lrt_cinema.xmp_parser import parse_xmp_file

FIXTURES = Path(__file__).parent / "fixtures"


def _seq_with_ramps(n: int, ramps: list[HolyGrailRamp]) -> LRTSequence:
    return LRTSequence(
        source_frames=[f"f{i}.CR3" for i in range(n)],
        holy_grail_ramps=ramps,
    )


def _flat_per_frame(n: int, base_ev: float = 0.0) -> list[DevelopOps]:
    return [DevelopOps(exposure_ev=base_ev) for _ in range(n)]


def test_single_ramp_hits_endpoints_exactly():
    seq = _seq_with_ramps(101, [HolyGrailRamp(
        start_frame=0, end_frame=100,
        start_exposure_ev=0.0, end_exposure_ev=2.0,
        smoothness=1.0,
    )])
    frames = _flat_per_frame(101)
    apply_holy_grail_ramps(frames, seq)
    assert frames[0].exposure_ev == pytest.approx(0.0)
    assert frames[100].exposure_ev == pytest.approx(2.0)


def test_smoothness_zero_matches_linear():
    seq = _seq_with_ramps(101, [HolyGrailRamp(
        start_frame=0, end_frame=100,
        start_exposure_ev=0.0, end_exposure_ev=2.0,
        smoothness=0.0,
    )])
    frames = _flat_per_frame(101)
    apply_holy_grail_ramps(frames, seq)
    assert frames[25].exposure_ev == pytest.approx(0.5)
    assert frames[50].exposure_ev == pytest.approx(1.0)
    assert frames[75].exposure_ev == pytest.approx(1.5)


def test_smoothness_one_matches_smoothstep():
    # smoothstep(0.25) = 0.0625 * (3 - 0.5) = 0.15625
    # smoothstep(0.75) = 0.5625 * (3 - 1.5) = 0.84375
    seq = _seq_with_ramps(101, [HolyGrailRamp(
        start_frame=0, end_frame=100,
        start_exposure_ev=0.0, end_exposure_ev=2.0,
        smoothness=1.0,
    )])
    frames = _flat_per_frame(101)
    apply_holy_grail_ramps(frames, seq)
    assert frames[25].exposure_ev == pytest.approx(2.0 * 0.15625)
    assert frames[50].exposure_ev == pytest.approx(1.0)
    assert frames[75].exposure_ev == pytest.approx(2.0 * 0.84375)


def test_three_segment_ramp_chains_correctly():
    # Joined ramps with matching boundary exposures form one continuous
    # day-to-night curve. The "last-wins" overlap policy means the shared
    # frame at each joint takes the later ramp's start value — which equals
    # the prior ramp's end by construction, so no double-counting.
    seq = _seq_with_ramps(601, [
        HolyGrailRamp(start_frame=0, end_frame=200,
                      start_exposure_ev=0.0, end_exposure_ev=3.0,
                      smoothness=0.0),
        HolyGrailRamp(start_frame=200, end_frame=400,
                      start_exposure_ev=3.0, end_exposure_ev=5.0,
                      smoothness=0.0),
        HolyGrailRamp(start_frame=400, end_frame=600,
                      start_exposure_ev=5.0, end_exposure_ev=6.0,
                      smoothness=0.0),
    ])
    frames = _flat_per_frame(601)
    apply_holy_grail_ramps(frames, seq)
    assert frames[0].exposure_ev == pytest.approx(0.0)
    assert frames[100].exposure_ev == pytest.approx(1.5)  # mid of segment 1
    assert frames[200].exposure_ev == pytest.approx(3.0)  # joint A→B (B wins, same val)
    assert frames[300].exposure_ev == pytest.approx(4.0)  # mid of segment 2
    assert frames[400].exposure_ev == pytest.approx(5.0)  # joint B→C
    assert frames[500].exposure_ev == pytest.approx(5.5)  # mid of segment 3
    assert frames[600].exposure_ev == pytest.approx(6.0)


def test_joined_ramps_do_not_double_count_at_boundary():
    # If we naively summed both ramps' contributions at frame 200 the result
    # would be 6.0 (3.0 from A's end + 3.0 from B's start). The last-wins
    # overlap policy is what keeps the joint correct.
    seq = _seq_with_ramps(401, [
        HolyGrailRamp(start_frame=0, end_frame=200,
                      start_exposure_ev=0.0, end_exposure_ev=3.0,
                      smoothness=1.0),
        HolyGrailRamp(start_frame=200, end_frame=400,
                      start_exposure_ev=3.0, end_exposure_ev=5.0,
                      smoothness=1.0),
    ])
    frames = _flat_per_frame(401, base_ev=0.5)
    apply_holy_grail_ramps(frames, seq)
    assert frames[200].exposure_ev == pytest.approx(0.5 + 3.0)


def test_overlapping_ramps_last_wins():
    # Two ramps that overlap on [50, 100] with different curves: the later
    # ramp's value should be the one applied at the overlap.
    seq = _seq_with_ramps(151, [
        HolyGrailRamp(start_frame=0, end_frame=100,
                      start_exposure_ev=0.0, end_exposure_ev=10.0,
                      smoothness=0.0),
        HolyGrailRamp(start_frame=50, end_frame=150,
                      start_exposure_ev=100.0, end_exposure_ev=200.0,
                      smoothness=0.0),
    ])
    frames = _flat_per_frame(151)
    apply_holy_grail_ramps(frames, seq)
    # Frame 75 is mid of the second ramp (overlap region); last-wins → ramp B.
    # Ramp B at t = (75-50)/100 = 0.25 (linear): 100 + 100*0.25 = 125.
    assert frames[75].exposure_ev == pytest.approx(125.0)
    # Frame 25 is in ramp A only; linear interp gives 2.5.
    assert frames[25].exposure_ev == pytest.approx(2.5)


def test_ramp_overlays_on_keyframe_base():
    # Confirms apply_holy_grail_ramps adds to whatever exposure_ev is already
    # in per_frame_ops (consistent with apply_deflicker).
    seq = _seq_with_ramps(11, [HolyGrailRamp(
        start_frame=0, end_frame=10,
        start_exposure_ev=1.0, end_exposure_ev=3.0,
        smoothness=0.0,
    )])
    frames = _flat_per_frame(11, base_ev=0.25)
    apply_holy_grail_ramps(frames, seq)
    assert frames[0].exposure_ev == pytest.approx(0.25 + 1.0)
    assert frames[5].exposure_ev == pytest.approx(0.25 + 2.0)
    assert frames[10].exposure_ev == pytest.approx(0.25 + 3.0)


def test_ramp_mutates_in_place_and_returns_same_list():
    seq = _seq_with_ramps(5, [HolyGrailRamp(
        start_frame=0, end_frame=4,
        start_exposure_ev=0.0, end_exposure_ev=1.0,
        smoothness=0.0,
    )])
    frames = _flat_per_frame(5)
    returned = apply_holy_grail_ramps(frames, seq)
    assert returned is frames
    assert frames[4].exposure_ev == pytest.approx(1.0)


def test_ramp_clamps_to_sequence_bounds():
    # Ramp window extends past the end of the sequence — frames beyond n-1
    # are silently ignored, not raised.
    seq = _seq_with_ramps(11, [HolyGrailRamp(
        start_frame=0, end_frame=100,
        start_exposure_ev=0.0, end_exposure_ev=10.0,
        smoothness=0.0,
    )])
    frames = _flat_per_frame(11)
    apply_holy_grail_ramps(frames, seq)
    # At frame 10 inside the ramp, t = 10/100 = 0.1, delta = 1.0.
    assert frames[10].exposure_ev == pytest.approx(1.0)


def test_ramp_zero_or_negative_span_raises():
    seq = _seq_with_ramps(11, [HolyGrailRamp(
        start_frame=5, end_frame=5,
        start_exposure_ev=0.0, end_exposure_ev=1.0,
    )])
    frames = _flat_per_frame(11)
    with pytest.raises(ValueError):
        apply_holy_grail_ramps(frames, seq)


def test_ramp_smoothness_out_of_range_raises():
    seq = _seq_with_ramps(11, [HolyGrailRamp(
        start_frame=0, end_frame=10,
        start_exposure_ev=0.0, end_exposure_ev=1.0,
        smoothness=1.5,
    )])
    frames = _flat_per_frame(11)
    with pytest.raises(ValueError):
        apply_holy_grail_ramps(frames, seq)


def test_empty_ramp_list_noop():
    seq = _seq_with_ramps(5, [])
    frames = _flat_per_frame(5, base_ev=2.0)
    apply_holy_grail_ramps(frames, seq)
    for f in frames:
        assert f.exposure_ev == 2.0


def test_apply_ramps_overlays_on_materialized_keyframes():
    # Full pipeline shape: materialize_all_frames → apply_holy_grail_ramps.
    seq = LRTSequence(
        source_frames=[f"f{i}.CR3" for i in range(11)],
        keyframes=[
            Keyframe(frame_index=0, ops=DevelopOps(exposure_ev=0.5)),
            Keyframe(frame_index=10, ops=DevelopOps(exposure_ev=0.5)),
        ],
        holy_grail_ramps=[HolyGrailRamp(
            start_frame=0, end_frame=10,
            start_exposure_ev=0.0, end_exposure_ev=2.0,
            smoothness=0.0,
        )],
    )
    frames = materialize_all_frames(seq)
    apply_holy_grail_ramps(frames, seq)
    assert frames[5].exposure_ev == pytest.approx(0.5 + 1.0)
    assert frames[10].exposure_ev == pytest.approx(0.5 + 2.0)


def test_parser_extracts_holy_grail_ramps_from_fixture():
    _ops, _is_kf, _delta, ramps, _rating, _mask = parse_xmp_file(FIXTURES / "synthetic_holy_grail.xmp")
    assert len(ramps) == 2
    a, b = ramps
    assert (a.start_frame, a.end_frame) == (0, 200)
    assert a.start_exposure_ev == pytest.approx(0.0)
    assert a.end_exposure_ev == pytest.approx(3.0)
    assert a.smoothness == pytest.approx(1.0)
    assert (b.start_frame, b.end_frame) == (200, 400)
    assert b.smoothness == pytest.approx(0.5)
