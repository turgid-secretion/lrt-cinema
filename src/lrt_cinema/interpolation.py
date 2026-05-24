"""Per-frame interpolation of develop ops from keyframes.

Given an `LRTSequence` with keyframes at sparse frame indices, produce
the `DevelopOps` that should apply at any frame in the sequence.

Two modes are supported: piecewise linear (the default, matching the
LRT scaffold v0.1 behavior) and uniform Catmull-Rom smooth interpolation
with mirror-extrapolated phantom tangents at the sequence endpoints.
With exactly two keyframes the phantoms collapse the cubic to a line,
so `smooth` degenerates to `linear` and the two modes return the same
values (modulo float round-off).

Holy Grail exposure ramps overlay on top of the keyframe-interpolated
base, mirroring the `apply_deflicker` contract: per-frame mutation,
returns the same list for convenience.

Constant-extrapolation policy at the endpoints: frames before the first
keyframe inherit the first keyframe's ops; frames after the last
keyframe inherit the last. This matches the LRT-default behavior of
holding a value steady outside the keyframed range.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import replace

from lrt_cinema.ir import (
    DevelopOps,
    InterpolationMode,
    LRTSequence,
    TonePoint,
)


def interpolate(seq: LRTSequence, frame_index: int) -> DevelopOps:
    """Return the interpolated DevelopOps for `frame_index` in `seq`.

    Dispatches on `seq.interpolation_mode`. Raises IndexError if
    `frame_index` is outside [0, frame_count). Returns a default
    DevelopOps if the sequence has no keyframes.
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

    if seq.interpolation_mode == InterpolationMode.smooth:
        prev_ops = keyframes[left - 1].ops if left - 1 >= 0 else None
        next_ops = keyframes[right + 1].ops if right + 1 < len(keyframes) else None
        return _smooth_blend(prev_ops, a.ops, b.ops, next_ops, t)

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


def _catmull_rom_scalar(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
    """Uniform Catmull-Rom interpolation between p1 and p2 with neighbors p0, p3.

    Standard form: returns p1 at t=0, p2 at t=1, with a cubic interior
    whose tangent at p1 is (p2 - p0)/2 and tangent at p2 is (p3 - p1)/2.
    Uniform parameterization: keyframe spacing is normalized to t ∈ [0, 1]
    inside the segment, irrespective of the integer frame distance between
    keyframes. Non-uniform spacing therefore yields uniform-CR's known
    velocity-discontinuity behavior at keyframes; centripetal CR is left
    as a future calibration choice.
    """
    t2 = t * t
    t3 = t2 * t
    return 0.5 * (
        (2.0 * p1)
        + (-p0 + p2) * t
        + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
        + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3
    )


def _cr_with_phantoms(
    prev_v: float | None, p1: float, p2: float, next_v: float | None, t: float,
) -> float:
    """Catmull-Rom with mirror-extrapolated phantoms when neighbors are missing.

    Phantom convention: p0 = 2*p1 - p2 and p3 = 2*p2 - p1. This makes the
    endpoint tangent match the chord slope, and — critically — collapses
    the cubic to an exact line when both neighbors are phantom (the
    2-keyframe case). See `test_smooth_two_keyframes_degenerates_to_linear`.
    """
    p0 = prev_v if prev_v is not None else 2.0 * p1 - p2
    p3 = next_v if next_v is not None else 2.0 * p2 - p1
    return _catmull_rom_scalar(p0, p1, p2, p3, t)


def _smooth_blend(
    prev_ops: DevelopOps | None,
    p1_ops: DevelopOps,
    p2_ops: DevelopOps,
    next_ops: DevelopOps | None,
    t: float,
) -> DevelopOps:
    """Per-field Catmull-Rom blend between two bracketing keyframes.

    `prev_ops` and `next_ops` are the neighboring keyframes outside the
    bracket; either may be None at sequence ends, in which case the
    `_cr_with_phantoms` mirror-extrapolation applies.

    Per-field policy:
      - Scalar floats: pure Catmull-Rom.
      - Optional ints (kelvin, tint): Catmull-Rom then `round()`. If a
        neighbor's value is None even when the neighbor exists, that
        neighbor is treated as missing and the phantom is mirrored from
        the bracketing pair — yielding chord-slope tangent there. If
        one of the bracketing values is None, fall back to the single
        non-None value (matches linear `lerp_opt_int` policy).
      - Tone curves: if bracketing curves match cardinality, smooth
        each `(x, y)` independently using per-point phantoms (mirrored
        from the bracketing pair when a neighbor's cardinality
        doesn't match). Otherwise fall back to `p1_ops.tone_curve`,
        matching the existing linear-blend policy.
    """
    if t <= 0.0:
        return replace(p1_ops)
    if t >= 1.0:
        return replace(p2_ops)

    def cr_f(name: str) -> float:
        prev_v = getattr(prev_ops, name) if prev_ops is not None else None
        next_v = getattr(next_ops, name) if next_ops is not None else None
        return _cr_with_phantoms(prev_v, getattr(p1_ops, name), getattr(p2_ops, name), next_v, t)

    def cr_opt_int(name: str) -> int | None:
        a1 = getattr(p1_ops, name)
        a2 = getattr(p2_ops, name)
        if a1 is None and a2 is None:
            return None
        if a1 is None:
            return a2
        if a2 is None:
            return a1
        prev_v = getattr(prev_ops, name) if prev_ops is not None else None
        next_v = getattr(next_ops, name) if next_ops is not None else None
        prev_f: float | None = float(prev_v) if prev_v is not None else None
        next_f: float | None = float(next_v) if next_v is not None else None
        return int(round(_cr_with_phantoms(prev_f, float(a1), float(a2), next_f, t)))

    if (
        p1_ops.tone_curve
        and p2_ops.tone_curve
        and len(p1_ops.tone_curve) == len(p2_ops.tone_curve)
    ):
        n = len(p1_ops.tone_curve)
        prev_curve = (
            prev_ops.tone_curve
            if prev_ops is not None and len(prev_ops.tone_curve) == n
            else None
        )
        next_curve = (
            next_ops.tone_curve
            if next_ops is not None and len(next_ops.tone_curve) == n
            else None
        )
        blended_curve: list[TonePoint] = []
        for i in range(n):
            p1pt = p1_ops.tone_curve[i]
            p2pt = p2_ops.tone_curve[i]
            prev_x = prev_curve[i].x if prev_curve is not None else None
            prev_y = prev_curve[i].y if prev_curve is not None else None
            next_x = next_curve[i].x if next_curve is not None else None
            next_y = next_curve[i].y if next_curve is not None else None
            blended_curve.append(TonePoint(
                x=_cr_with_phantoms(prev_x, p1pt.x, p2pt.x, next_x, t),
                y=_cr_with_phantoms(prev_y, p1pt.y, p2pt.y, next_y, t),
            ))
    else:
        blended_curve = list(p1_ops.tone_curve)

    return DevelopOps(
        exposure_ev=cr_f("exposure_ev"),
        contrast=cr_f("contrast"),
        highlights=cr_f("highlights"),
        shadows=cr_f("shadows"),
        whites=cr_f("whites"),
        blacks=cr_f("blacks"),
        temperature_k=cr_opt_int("temperature_k"),
        tint=cr_opt_int("tint"),
        saturation=cr_f("saturation"),
        vibrance=cr_f("vibrance"),
        sharpness=cr_f("sharpness"),
        tone_curve=blended_curve,
    )


__all__ = [
    "apply_deflicker",
    "apply_lrt_mask_offsets",
    "interpolate",
    "materialize_all_frames",
]
