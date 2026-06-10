"""Per-frame interpolation of develop ops from keyframes.

Given an `LRTSequence` with keyframes at sparse frame indices, produce
the `DevelopOps` that should apply at any frame in the sequence.

Piecewise linear only. An earlier Catmull-Rom 'smooth' mode was deleted
in the 2026-05-24 audit cleanup — it was never validated against LRT's
own spline shape, and SCOPE.md's stated posture is to defer interpolation
to LRT's Auto-Transition (we exact-match LRT's per-frame values when
they are present).

Constant-extrapolation policy at the endpoints: frames before the first
keyframe inherit the first keyframe's ops; frames after the last
keyframe inherit the last. This matches the LRT-default behavior of
holding a value steady outside the keyframed range.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import replace

from lrt_cinema.ir import DevelopOps, LRTSequence


def interpolate(seq: LRTSequence, frame_index: int) -> DevelopOps:
    """Return the linearly-interpolated DevelopOps for `frame_index` in `seq`.

    Raises IndexError if `frame_index` is outside [0, frame_count).
    Returns a default DevelopOps if the sequence has no keyframes.
    """
    if frame_index < 0 or frame_index >= seq.frame_count():
        raise IndexError(
            f"frame_index {frame_index} outside [0, {seq.frame_count()})"
        )

    keyframes = sorted(seq.keyframes, key=lambda k: k.frame_index)
    if not keyframes:
        return DevelopOps()
    if len(keyframes) == 1:
        return replace(keyframes[0].ops)

    indices = [k.frame_index for k in keyframes]

    if frame_index <= indices[0]:
        return replace(keyframes[0].ops)
    if frame_index >= indices[-1]:
        return replace(keyframes[-1].ops)

    pos = bisect_left(indices, frame_index)
    if pos < len(indices) and indices[pos] == frame_index:
        return replace(keyframes[pos].ops)

    right = bisect_right(indices, frame_index)
    left = right - 1
    a = keyframes[left]
    b = keyframes[right]
    span = b.frame_index - a.frame_index
    if span <= 0:
        return replace(a.ops)
    t = (frame_index - a.frame_index) / span
    return a.ops.blend(b.ops, t)


def materialize_all_frames(seq: LRTSequence) -> list[DevelopOps]:
    """Compute the interpolated DevelopOps for every frame in `seq`.

    Convenience wrapper for the renderer when it wants to drive the
    full sequence without re-finding bracketing keyframes per frame.
    """
    return [interpolate(seq, i) for i in range(seq.frame_count())]


# Lightroom serializes local/mask exposure (crs:LocalExposure2012) as EV/4 —
# the local slider spans ±4 EV — and applies 2^(4·serialized) as a linear gain
# UPSTREAM of the DCP tone pipeline. Calibrated 2026-06-10 on owner-exported
# single-variable CAL frames: k* = 3.992 ± 0.027 across two EV levels, sharp
# minimum, ΔE 0.20/0.44 at exactly 4.0 (tools/cal_deflicker_factor.py;
# CLAIMS.md "Exact mask-exposure factor"). History: the order audit's F3
# (2026-06-04) had flagged the ~3× under-delivery; the B2 audit wrongly
# refuted it; the LR-arbiter + CAL experiments re-confirmed and sharpened it.
LR_LOCAL_EXPOSURE_SCALE = 4.0


def apply_deflicker(
    per_frame_ops: list[DevelopOps], seq: LRTSequence, scale: float = 1.0,
) -> list[DevelopOps]:
    """Apply LRT-written per-frame deflicker exposure deltas in place.

    Each `DeflickerOffset(frame_index, exposure_delta_ev)` carries a
    serialized `LocalExposure2012` value; the true correction is
    `LR_LOCAL_EXPOSURE_SCALE ×` that, applied **scene-referred** — so it is
    added to that frame's `scene_exposure_ev` (a pre-Stage-2 linear gain in
    `render_frame`), NOT the post-tone-curve `exposure_ev`. Both the ×4 and
    the domain are measured, not assumed: the post-curve domain cannot match
    Lightroom at HG-ramp magnitudes for any factor (CLAIMS.md "Exact
    mask-exposure factor"; evidence
    `tests/fixtures/evidence/cal_deflicker_factor_2026-06-10.json`).

    `scale` multiplies the **corrected** delta (an owner trim knob on top of
    the calibrated baseline). Default 1.0 = the calibrated Lightroom-faithful
    application; 0.0 disables deflicker entirely.

    Returns the mutated list (also mutates `per_frame_ops` for in-place callers).
    """
    delta_by_frame = {d.frame_index: d.exposure_delta_ev for d in seq.deflicker_offsets}
    for i, ops in enumerate(per_frame_ops):
        delta = delta_by_frame.get(i, 0.0) * LR_LOCAL_EXPOSURE_SCALE * scale
        if delta != 0.0:
            per_frame_ops[i] = replace(
                ops, scene_exposure_ev=ops.scene_exposure_ev + delta,
            )
    return per_frame_ops


def apply_lrt_mask_offsets(
    per_frame_ops: list[DevelopOps], seq: LRTSequence,
    kinds: tuple[str, ...] = ("hg", "deflicker", "global"),
    deflicker_scale: float = 1.0,
) -> list[DevelopOps]:
    """Apply real-LRT mask-correction per-frame exposure deltas in place.

    Real LRT 7.5.3 emits Holy Grail / Visual Deflicker / Global per-frame
    EV deltas inside `crs:MaskGroupBasedCorrections` (serialized
    `LocalExposure2012`, i.e. EV/4 — see `LR_LOCAL_EXPOSURE_SCALE`). Parser
    extracts those as `LRTMaskOffset(frame_index, kind, exposure_delta_ev)`
    on `seq.lrt_mask_offsets`. This function sums all requested kinds per
    frame, scales by `LR_LOCAL_EXPOSURE_SCALE`, and adds to that frame's
    `scene_exposure_ev` — the scene-referred pre-Stage-2 gain, matching
    where Lightroom applies local exposure. All three kinds are the same
    serialization, so all three get the ×4 (sequence-validated for the
    deflicker kind; mechanism-derived for HG/Global, which are zero in the
    current production sequence).

    `kinds` selects which sources to apply; default is all three. Pass
    `("deflicker",)` to apply only Deflicker corrections, etc. Matches
    the CLI's per-source toggle semantics.

    `deflicker_scale` multiplies ONLY the **deflicker**-kind corrected delta
    (HG/Global untouched) — an owner trim knob on the calibrated baseline.
    Default 1.0 = the calibrated Lightroom-faithful application.

    Mutates `per_frame_ops` in place and returns it. See
    ADVERSARIAL_AUDIT_2026-05-23 HIGH-2 for context.
    """
    kinds_set = set(kinds)
    sum_by_frame: dict[int, float] = {}
    for off in seq.lrt_mask_offsets:
        if off.kind not in kinds_set:
            continue
        ev = off.exposure_delta_ev * LR_LOCAL_EXPOSURE_SCALE
        if off.kind == "deflicker":
            ev *= deflicker_scale
        sum_by_frame[off.frame_index] = sum_by_frame.get(off.frame_index, 0.0) + ev
    for i, ops in enumerate(per_frame_ops):
        delta = sum_by_frame.get(i, 0.0)
        if delta != 0.0:
            per_frame_ops[i] = replace(
                ops, scene_exposure_ev=ops.scene_exposure_ev + delta,
            )
    return per_frame_ops


__all__ = [
    "LR_LOCAL_EXPOSURE_SCALE",
    "apply_deflicker",
    "apply_lrt_mask_offsets",
    "interpolate",
    "materialize_all_frames",
]
