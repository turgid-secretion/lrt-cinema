"""IR blend math."""

from lrt_cinema.ir import (
    DevelopOps,
    InterpolationMode,
    LRTSequence,
    TonePoint,
)


def test_blend_endpoints_return_copies():
    a = DevelopOps(exposure_ev=1.0)
    b = DevelopOps(exposure_ev=2.0)
    assert a.blend(b, 0.0).exposure_ev == 1.0
    assert a.blend(b, 1.0).exposure_ev == 2.0


def test_blend_midpoint_is_linear():
    a = DevelopOps(exposure_ev=0.0, contrast=-20.0, shadows=10.0)
    b = DevelopOps(exposure_ev=4.0, contrast=+20.0, shadows=50.0)
    mid = a.blend(b, 0.5)
    assert mid.exposure_ev == 2.0
    assert mid.contrast == 0.0
    assert mid.shadows == 30.0


def test_blend_temperature_rounds_to_int():
    a = DevelopOps(temperature_k=4000)
    b = DevelopOps(temperature_k=6000)
    assert a.blend(b, 0.5).temperature_k == 5000


def test_blend_temperature_one_sided_returns_present_value():
    a = DevelopOps(temperature_k=None)
    b = DevelopOps(temperature_k=5500)
    assert a.blend(b, 0.5).temperature_k == 5500


def test_blend_tone_curve_pointwise_when_same_length():
    a = DevelopOps(tone_curve=[TonePoint(0.0, 0.0), TonePoint(1.0, 1.0)])
    b = DevelopOps(tone_curve=[TonePoint(0.0, 0.0), TonePoint(1.0, 0.5)])
    mid = a.blend(b, 0.5)
    assert mid.tone_curve[0] == TonePoint(0.0, 0.0)
    assert mid.tone_curve[1].x == 1.0
    assert mid.tone_curve[1].y == 0.75


def test_blend_tone_curve_falls_back_to_self_when_lengths_differ():
    a = DevelopOps(tone_curve=[TonePoint(0.0, 0.0), TonePoint(1.0, 1.0)])
    b = DevelopOps(tone_curve=[TonePoint(0.5, 0.5)])
    mid = a.blend(b, 0.5)
    assert mid.tone_curve == a.tone_curve


def test_lrt_sequence_defaults():
    seq = LRTSequence()
    assert seq.interpolation_mode == InterpolationMode.linear
    assert seq.frame_count() == 0
