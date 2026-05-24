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


def apply_deflicker(
    per_frame_ops: list[DevelopOps], seq: LRTSequence,
) -> list[DevelopOps]:
    """Apply LRT-written per-frame deflicker exposure deltas in place.

    Each `DeflickerOffset(frame_index, exposure_delta_ev)` adds its
    delta onto that frame's exposure_ev. Frames without a recorded
    offset are unchanged.

    Returns the mutated list (also mutates `per_frame_ops` for
    in-place callers). The compute-the-deltas pass — `darktable-cli`
    export → measure luminance via OIIO → write deltas back — is
    out of scope for v0.1 (see SCOPE.md). This function applies
    deltas the LRT user already authored.
    """
    delta_by_frame = {d.frame_index: d.exposure_delta_ev for d in seq.deflicker_offsets}
    for i, ops in enumerate(per_frame_ops):
        delta = delta_by_frame.get(i, 0.0)
        if delta != 0.0:
            per_frame_ops[i] = replace(ops, exposure_ev=ops.exposure_ev + delta)
    return per_frame_ops


def apply_lrt_mask_offsets(
    per_frame_ops: list[DevelopOps], seq: LRTSequence,
    kinds: tuple[str, ...] = ("hg", "deflicker", "global"),
) -> list[DevelopOps]:
    """Apply real-LRT mask-correction per-frame exposure deltas in place.

    Real LRT 7.5.3 emits Holy Grail / Visual Deflicker / Global per-frame
    EV deltas inside `crs:MaskGroupBasedCorrections`. Parser extracts
    those as `LRTMaskOffset(frame_index, kind, exposure_delta_ev)` and
    stores them on `seq.lrt_mask_offsets`. This function sums all
    requested kinds per frame and adds to that frame's `exposure_ev`.

    `kinds` selects which sources to apply; default is all three. Pass
    `("deflicker",)` to apply only Deflicker corrections, etc. Matches
    the CLI's per-source toggle semantics.

    Mutates `per_frame_ops` in place and returns it. See
    ADVERSARIAL_AUDIT_2026-05-23 HIGH-2 for context.
    """
    kinds_set = set(kinds)
    sum_by_frame: dict[int, float] = {}
    for off in seq.lrt_mask_offsets:
        if off.kind not in kinds_set:
            continue
        sum_by_frame[off.frame_index] = (
            sum_by_frame.get(off.frame_index, 0.0) + off.exposure_delta_ev
        )
    for i, ops in enumerate(per_frame_ops):
        delta = sum_by_frame.get(i, 0.0)
        if delta != 0.0:
            per_frame_ops[i] = replace(ops, exposure_ev=ops.exposure_ev + delta)
    return per_frame_ops


__all__ = [
    "apply_deflicker",
    "apply_lrt_mask_offsets",
    "interpolate",
    "materialize_all_frames",
]
