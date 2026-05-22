"""Keyframe interpolation tests."""

import pytest

from lrt_cinema.interpolation import (
    apply_deflicker,
    interpolate,
    materialize_all_frames,
)
from lrt_cinema.ir import DeflickerOffset, DevelopOps, Keyframe, LRTSequence


def _seq(num_frames: int, keyframes: list[Keyframe], deflicker=None) -> LRTSequence:
    return LRTSequence(
        source_frames=[f"f{i}.CR3" for i in range(num_frames)],
        keyframes=keyframes,
        deflicker_offsets=deflicker or [],
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
