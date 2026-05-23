"""Keyframe interpolation tests."""

import pytest

from lrt_cinema.interpolation import (
    apply_deflicker,
    interpolate,
    materialize_all_frames,
)
from lrt_cinema.ir import (
    DeflickerOffset,
    DevelopOps,
    InterpolationMode,
    Keyframe,
    LRTSequence,
    TonePoint,
)


def _seq(
    num_frames: int,
    keyframes: list[Keyframe],
    deflicker=None,
    mode: InterpolationMode = InterpolationMode.linear,
) -> LRTSequence:
    return LRTSequence(
        source_frames=[f"f{i}.CR3" for i in range(num_frames)],
        keyframes=keyframes,
        deflicker_offsets=deflicker or [],
        interpolation_mode=mode,
    )


def test_interpolate_constant_before_first_keyframe():
    seq = _seq(10, [Keyframe(frame_index=4, ops=DevelopOps(exposure_ev=2.0))])
    assert interpolate(seq, 0).exposure_ev == 2.0
    assert interpolate(seq, 3).exposure_ev == 2.0


def test_interpolate_constant_after_last_keyframe():
    seq = _seq(10, [Keyframe(frame_index=4, ops=DevelopOps(exposure_ev=2.0))])
    assert interpolate(seq, 9).exposure_ev == 2.0


def test_interpolate_linear_between_two_keyframes():
    seq = _seq(11, [
        Keyframe(frame_index=0, ops=DevelopOps(exposure_ev=0.0)),
        Keyframe(frame_index=10, ops=DevelopOps(exposure_ev=10.0)),
    ])
    assert interpolate(seq, 0).exposure_ev == 0.0
    assert interpolate(seq, 5).exposure_ev == 5.0
    assert interpolate(seq, 10).exposure_ev == 10.0


def test_interpolate_exactly_at_keyframe_returns_keyframe_ops():
    seq = _seq(11, [
        Keyframe(frame_index=0, ops=DevelopOps(exposure_ev=0.0)),
        Keyframe(frame_index=5, ops=DevelopOps(exposure_ev=2.5, contrast=15.0)),
        Keyframe(frame_index=10, ops=DevelopOps(exposure_ev=10.0)),
    ])
    mid = interpolate(seq, 5)
    assert mid.exposure_ev == 2.5
    assert mid.contrast == 15.0


def test_interpolate_no_keyframes_returns_default():
    seq = _seq(5, [])
    assert interpolate(seq, 2) == DevelopOps()


def test_interpolate_out_of_range_raises():
    seq = _seq(5, [Keyframe(frame_index=0, ops=DevelopOps())])
    with pytest.raises(IndexError):
        interpolate(seq, -1)
    with pytest.raises(IndexError):
        interpolate(seq, 5)


def test_materialize_all_frames_covers_full_sequence():
    seq = _seq(4, [
        Keyframe(frame_index=0, ops=DevelopOps(exposure_ev=0.0)),
        Keyframe(frame_index=3, ops=DevelopOps(exposure_ev=3.0)),
    ])
    frames = materialize_all_frames(seq)
    assert len(frames) == 4
    assert frames[0].exposure_ev == 0.0
    assert frames[1].exposure_ev == 1.0
    assert frames[2].exposure_ev == 2.0
    assert frames[3].exposure_ev == 3.0


def test_smooth_two_keyframes_degenerates_to_linear():
    # Catmull-Rom with mirror-extrapolated phantoms collapses to an exact
    # line when there are only two keyframes (cubic + quadratic coefficients
    # vanish). Asserting parity end-to-end is the cleanest contract check.
    keyframes = [
        Keyframe(frame_index=0, ops=DevelopOps(exposure_ev=0.0, contrast=-20.0)),
        Keyframe(frame_index=10, ops=DevelopOps(exposure_ev=4.0, contrast=20.0)),
    ]
    smooth = _seq(11, list(keyframes), mode=InterpolationMode.smooth)
    linear = _seq(11, list(keyframes), mode=InterpolationMode.linear)
    for i in range(11):
        s = interpolate(smooth, i)
        lin = interpolate(linear, i)
        assert s.exposure_ev == pytest.approx(lin.exposure_ev)
        assert s.contrast == pytest.approx(lin.contrast)


def test_smooth_differs_from_linear_for_non_monotonic_keyframes():
    # Three keyframes at frames 0/50/100 with values 0/10/0. At the midpoint
    # of the first segment (frame 25), linear gives 5.0; uniform Catmull-Rom
    # with phantom p0 = 2*0 - 10 = -10 and real p3 = 0 gives 6.25.
    seq_smooth = _seq(101, [
        Keyframe(frame_index=0, ops=DevelopOps(exposure_ev=0.0)),
        Keyframe(frame_index=50, ops=DevelopOps(exposure_ev=10.0)),
        Keyframe(frame_index=100, ops=DevelopOps(exposure_ev=0.0)),
    ], mode=InterpolationMode.smooth)
    seq_linear = _seq(101, [
        Keyframe(frame_index=0, ops=DevelopOps(exposure_ev=0.0)),
        Keyframe(frame_index=50, ops=DevelopOps(exposure_ev=10.0)),
        Keyframe(frame_index=100, ops=DevelopOps(exposure_ev=0.0)),
    ], mode=InterpolationMode.linear)
    assert interpolate(seq_linear, 25).exposure_ev == pytest.approx(5.0)
    assert interpolate(seq_smooth, 25).exposure_ev == pytest.approx(6.25)


def test_smooth_hits_keyframes_exactly():
    seq = _seq(101, [
        Keyframe(frame_index=0, ops=DevelopOps(exposure_ev=0.0)),
        Keyframe(frame_index=50, ops=DevelopOps(exposure_ev=10.0)),
        Keyframe(frame_index=100, ops=DevelopOps(exposure_ev=0.0)),
    ], mode=InterpolationMode.smooth)
    assert interpolate(seq, 0).exposure_ev == 0.0
    assert interpolate(seq, 50).exposure_ev == 10.0
    assert interpolate(seq, 100).exposure_ev == 0.0


def test_smooth_optional_int_rounds_and_smooths():
    seq = _seq(101, [
        Keyframe(frame_index=0, ops=DevelopOps(temperature_k=3000)),
        Keyframe(frame_index=50, ops=DevelopOps(temperature_k=6000)),
        Keyframe(frame_index=100, ops=DevelopOps(temperature_k=4000)),
    ], mode=InterpolationMode.smooth)
    val = interpolate(seq, 25).temperature_k
    assert isinstance(val, int)
    # Linear midpoint would be 4500; smooth bows toward the velocity profile.
    assert val != 4500


def test_smooth_optional_int_one_sided_falls_back():
    seq = _seq(11, [
        Keyframe(frame_index=0, ops=DevelopOps(temperature_k=None)),
        Keyframe(frame_index=10, ops=DevelopOps(temperature_k=5500)),
    ], mode=InterpolationMode.smooth)
    # Bracketing pair has one None — same single-side-wins policy as linear.
    assert interpolate(seq, 5).temperature_k == 5500


def test_smooth_tone_curve_cardinality_mismatch_falls_back_to_self():
    seq = _seq(11, [
        Keyframe(frame_index=0, ops=DevelopOps(
            tone_curve=[TonePoint(0.0, 0.0), TonePoint(1.0, 1.0)],
        )),
        Keyframe(frame_index=10, ops=DevelopOps(
            tone_curve=[TonePoint(0.5, 0.5)],
        )),
    ], mode=InterpolationMode.smooth)
    mid = interpolate(seq, 5)
    assert mid.tone_curve == [TonePoint(0.0, 0.0), TonePoint(1.0, 1.0)]


def test_smooth_tone_curve_smooths_pointwise():
    # Three keyframes with matched cardinality; smooth interp should produce
    # values that differ from linear at a non-monotonic point.
    seq_smooth = _seq(101, [
        Keyframe(frame_index=0, ops=DevelopOps(
            tone_curve=[TonePoint(0.0, 0.0), TonePoint(1.0, 0.0)],
        )),
        Keyframe(frame_index=50, ops=DevelopOps(
            tone_curve=[TonePoint(0.0, 0.0), TonePoint(1.0, 1.0)],
        )),
        Keyframe(frame_index=100, ops=DevelopOps(
            tone_curve=[TonePoint(0.0, 0.0), TonePoint(1.0, 0.0)],
        )),
    ], mode=InterpolationMode.smooth)
    p = interpolate(seq_smooth, 25).tone_curve[1]
    assert p.x == 1.0  # x coordinate is static across keyframes
    assert p.y == pytest.approx(0.625)  # same math as scalar 0/10/0 test, scaled


def test_apply_deflicker_adds_per_frame_delta():
    seq = _seq(4, [
        Keyframe(frame_index=0, ops=DevelopOps(exposure_ev=1.0)),
        Keyframe(frame_index=3, ops=DevelopOps(exposure_ev=1.0)),
    ], deflicker=[
        DeflickerOffset(frame_index=1, exposure_delta_ev=0.1),
        DeflickerOffset(frame_index=2, exposure_delta_ev=-0.05),
    ])
    frames = materialize_all_frames(seq)
    deflickered = apply_deflicker(frames, seq)
    assert deflickered[0].exposure_ev == 1.0
    assert deflickered[1].exposure_ev == pytest.approx(1.1)
    assert deflickered[2].exposure_ev == pytest.approx(0.95)
    assert deflickered[3].exposure_ev == 1.0
