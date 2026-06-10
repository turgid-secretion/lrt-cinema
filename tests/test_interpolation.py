"""Keyframe interpolation tests."""

import pytest

from lrt_cinema.interpolation import (
    apply_deflicker,
    apply_lrt_mask_offsets,
    interpolate,
    materialize_all_frames,
)
from lrt_cinema.ir import (
    ColorGrade,
    DeflickerOffset,
    DevelopOps,
    HslBands,
    Keyframe,
    LRTMaskOffset,
    LRTSequence,
)


def _seq(
    num_frames: int,
    keyframes: list[Keyframe],
    deflicker=None,
) -> LRTSequence:
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


def test_interpolate_threads_hsl_through_blend():
    """HSL must survive per-frame interpolation — a field dropped from
    DevelopOps.blend() would silently zero here. Keyframe A has Red-band sliders;
    B is default; the midpoint frame must carry ~half of each."""
    a = DevelopOps(hsl=HslBands(
        hue=(40.0, 0, 0, 0, 0, 0, 0, 0),
        saturation=(60.0, 0, 0, 0, 0, 0, 0, 0),
        luminance=(-20.0, 0, 0, 0, 0, 0, 0, 0),
    ))
    seq = _seq(11, [
        Keyframe(frame_index=0, ops=a),
        Keyframe(frame_index=10, ops=DevelopOps()),
    ])
    mid = interpolate(seq, 5).hsl
    assert mid.hue[0] == pytest.approx(20.0)
    assert mid.saturation[0] == pytest.approx(30.0)
    assert mid.luminance[0] == pytest.approx(-10.0)
    # Endpoints exact.
    assert interpolate(seq, 0).hsl.saturation[0] == pytest.approx(60.0)
    assert interpolate(seq, 10).hsl.is_identity()


def test_interpolate_threads_texture_clarity_through_blend():
    """Texture/Clarity must survive per-frame interpolation — a field dropped from
    DevelopOps.blend() would silently zero here. Keyframe A sets both; B is default;
    the midpoint frame must carry ~half of each."""
    a = DevelopOps(texture=60.0, clarity=-40.0)
    seq = _seq(11, [
        Keyframe(frame_index=0, ops=a),
        Keyframe(frame_index=10, ops=DevelopOps()),  # default: texture 0, clarity 0
    ])
    mid = interpolate(seq, 5)
    assert mid.texture == pytest.approx(30.0)
    assert mid.clarity == pytest.approx(-20.0)
    # Endpoints exact.
    assert interpolate(seq, 0).texture == pytest.approx(60.0)
    assert interpolate(seq, 10).texture == pytest.approx(0.0)
    assert interpolate(seq, 10).clarity == pytest.approx(0.0)


def test_interpolate_threads_color_grade_through_blend():
    """Color Grade must survive per-frame interpolation — a field dropped from
    DevelopOps.blend()/ColorGrade.blend() would silently zero here."""
    a = DevelopOps(color_grade=ColorGrade(
        shadow_hue=240.0, shadow_sat=80.0, highlight_lum=40.0,
        blending=80.0, balance=-40.0,
    ))
    seq = _seq(11, [
        Keyframe(frame_index=0, ops=a),
        Keyframe(frame_index=10, ops=DevelopOps()),  # default: blending 50, balance 0
    ])
    mid = interpolate(seq, 5).color_grade
    assert mid.shadow_hue == pytest.approx(120.0)
    assert mid.shadow_sat == pytest.approx(40.0)
    assert mid.highlight_lum == pytest.approx(20.0)
    assert mid.blending == pytest.approx(65.0)   # (80+50)/2
    assert mid.balance == pytest.approx(-20.0)   # (-40+0)/2
    assert interpolate(seq, 10).color_grade.is_identity()


def test_interpolate_threads_sharpen_fields_through_blend():
    """The D2 sharpen fields (radius/detail/masking) must survive per-frame
    interpolation — any field dropped from DevelopOps.blend() would silently snap to
    a default here."""
    seq = _seq(11, [
        Keyframe(frame_index=0, ops=DevelopOps(
            sharpness=80.0, sharpen_radius=3.0, sharpen_detail=75.0,
            sharpen_edge_masking=60.0)),
        # default endpoint: sharpness 0, radius 1.0, detail 25.0, masking 0.0
        Keyframe(frame_index=10, ops=DevelopOps()),
    ])
    mid = interpolate(seq, 5)
    assert mid.sharpness == pytest.approx(40.0)
    assert mid.sharpen_radius == pytest.approx(2.0)            # (3.0 + 1.0)/2
    assert mid.sharpen_detail == pytest.approx(50.0)          # (75.0 + 25.0)/2
    assert mid.sharpen_edge_masking == pytest.approx(30.0)    # (60.0 + 0.0)/2
    assert interpolate(seq, 0).sharpen_radius == pytest.approx(3.0)
    assert interpolate(seq, 10).sharpen_detail == pytest.approx(25.0)


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
    # Serialized LocalExposure2012 is EV/4: the applied correction is ×4
    # (LR_LOCAL_EXPOSURE_SCALE), landing on the SCENE-REFERRED channel —
    # the post-curve exposure_ev must stay untouched (CLAIMS.md "Exact
    # mask-exposure factor").
    seq = _seq(4, [
        Keyframe(frame_index=0, ops=DevelopOps(exposure_ev=1.0)),
        Keyframe(frame_index=3, ops=DevelopOps(exposure_ev=1.0)),
    ], deflicker=[
        DeflickerOffset(frame_index=1, exposure_delta_ev=0.1),
        DeflickerOffset(frame_index=2, exposure_delta_ev=-0.05),
    ])
    frames = materialize_all_frames(seq)
    deflickered = apply_deflicker(frames, seq)
    assert deflickered[0].scene_exposure_ev == 0.0
    assert deflickered[1].scene_exposure_ev == pytest.approx(0.4)    # 4 × 0.1
    assert deflickered[2].scene_exposure_ev == pytest.approx(-0.2)   # 4 × -0.05
    assert deflickered[3].scene_exposure_ev == 0.0
    assert all(f.exposure_ev == 1.0 for f in deflickered)            # untouched


def test_apply_lrt_mask_offsets_sums_kinds_per_frame():
    # ADVERSARIAL_AUDIT_2026-05-23 HIGH-2: real-LRT mask corrections
    # (HG/Deflicker/Global) sum additively per frame — onto the
    # scene-referred channel, ×4 (same LocalExposure2012 serialization).
    seq = _seq(4, [
        Keyframe(frame_index=0, ops=DevelopOps(exposure_ev=1.0)),
        Keyframe(frame_index=3, ops=DevelopOps(exposure_ev=1.0)),
    ])
    seq.lrt_mask_offsets = [
        LRTMaskOffset(frame_index=1, kind="hg",        exposure_delta_ev=0.30),
        LRTMaskOffset(frame_index=1, kind="deflicker", exposure_delta_ev=-0.05),
        LRTMaskOffset(frame_index=1, kind="global",    exposure_delta_ev=0.10),
        LRTMaskOffset(frame_index=2, kind="deflicker", exposure_delta_ev=0.20),
    ]
    frames = materialize_all_frames(seq)
    applied = apply_lrt_mask_offsets(frames, seq)
    # Frame 1: 4 × (0.30 - 0.05 + 0.10) = 1.40
    assert applied[1].scene_exposure_ev == pytest.approx(1.40)
    # Frame 2: only deflicker, 4 × 0.20
    assert applied[2].scene_exposure_ev == pytest.approx(0.80)
    # Frames with no offsets untouched; exposure_ev never touched
    assert applied[0].scene_exposure_ev == 0.0
    assert applied[3].scene_exposure_ev == 0.0
    assert all(f.exposure_ev == 1.0 for f in applied)


def test_deflicker_scale_trims_corrected_baseline():
    """deflicker_scale is an owner trim on the CALIBRATED (×4) deflicker
    correction, deflicker kind only (HG/Global untouched). Default 1.0 =
    the Lightroom-faithful baseline."""
    # apply_deflicker (synthetic offsets): scale multiplies the corrected delta.
    seq = _seq(3, [Keyframe(frame_index=0, ops=DevelopOps(exposure_ev=1.0))],
               deflicker=[DeflickerOffset(frame_index=1, exposure_delta_ev=0.1)])
    assert apply_deflicker(materialize_all_frames(seq), seq, scale=1.0)[1] \
        .scene_exposure_ev == pytest.approx(0.4)    # calibrated baseline 4×0.1
    assert apply_deflicker(materialize_all_frames(seq), seq, scale=3.0)[1] \
        .scene_exposure_ev == pytest.approx(1.2)    # trim ×3 on the baseline
    assert apply_deflicker(materialize_all_frames(seq), seq, scale=0.0)[1] \
        .scene_exposure_ev == 0.0                   # 0.0 disables deflicker

    # apply_lrt_mask_offsets: deflicker_scale hits ONLY the deflicker kind.
    seq2 = _seq(2, [Keyframe(frame_index=0, ops=DevelopOps(exposure_ev=0.0))])
    seq2.lrt_mask_offsets = [
        LRTMaskOffset(frame_index=1, kind="hg",        exposure_delta_ev=0.30),
        LRTMaskOffset(frame_index=1, kind="deflicker", exposure_delta_ev=0.10),
        LRTMaskOffset(frame_index=1, kind="global",    exposure_delta_ev=0.20),
    ]
    # default 1.0: 4 × (0.30 + 0.10 + 0.20) = 2.40
    assert apply_lrt_mask_offsets(
        materialize_all_frames(seq2), seq2)[1].scene_exposure_ev \
        == pytest.approx(2.40)
    # scale 3.0: 4×0.30 + 4×0.10×3 + 4×0.20 = 3.20 (only deflicker trimmed)
    assert apply_lrt_mask_offsets(
        materialize_all_frames(seq2), seq2, deflicker_scale=3.0)[1] \
        .scene_exposure_ev == pytest.approx(3.20)


def test_apply_lrt_mask_offsets_kinds_filter():
    # Only the requested kinds apply (×4 calibrated scale on each).
    seq = _seq(2, [Keyframe(frame_index=0, ops=DevelopOps(exposure_ev=0.0))])
    seq.lrt_mask_offsets = [
        LRTMaskOffset(frame_index=0, kind="hg",        exposure_delta_ev=1.0),
        LRTMaskOffset(frame_index=0, kind="deflicker", exposure_delta_ev=2.0),
        LRTMaskOffset(frame_index=0, kind="global",    exposure_delta_ev=4.0),
    ]
    frames = materialize_all_frames(seq)
    only_hg = apply_lrt_mask_offsets(frames[:], seq, kinds=("hg",))
    assert only_hg[0].scene_exposure_ev == pytest.approx(4.0)
    frames2 = materialize_all_frames(seq)
    only_dfk = apply_lrt_mask_offsets(frames2, seq, kinds=("deflicker",))
    assert only_dfk[0].scene_exposure_ev == pytest.approx(8.0)
