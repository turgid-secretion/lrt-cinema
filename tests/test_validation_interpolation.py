"""Validation of the keyframe-INTERPOLATION path (the suite had ZERO coverage).

Per-frame ``DevelopOps`` are produced by ``interpolation.interpolate`` →
``DevelopOps.blend`` → ``HslBands.blend`` / ``ColorGrade.blend``. None of the
358-test suite drove a *blended* op through a render, so an interpolation bug
(a field that overshoots its endpoints, or a blended grade that emits an invalid
frame) would ship invisibly. This drives that path and asserts the pure
invariants on the rendered/emitted result.

The headline case is the **near-identity-but-ENGAGED** band: at ``t ≈ 0.001`` a
blend from identity to a grade is JUST past ``is_identity()`` — so the byte-exact
short-circuit does NOT fire and the real (lossy) OKLCh/ACEScct/guided path runs
on a vanishingly small grade. That tiny-but-engaged regime is where a
discontinuity at the identity boundary would surface, and it is otherwise never
exercised.
"""

from __future__ import annotations

import numpy as np
import pytest

from lrt_cinema.develop_ops import apply_develop_ops
from lrt_cinema.interpolation import interpolate, materialize_all_frames
from lrt_cinema.ir import (
    ColorGrade,
    DevelopOps,
    HslBands,
    Keyframe,
    LRTSequence,
    RenderIntent,
)
from tests import validation_lattice as vl

_INTENTS = list(RenderIntent)


def _graded_ops() -> DevelopOps:
    """A multi-op grade endpoint (every interpolated channel engaged)."""
    return DevelopOps(
        exposure_ev=1.0, contrast=40.0, blacks=-15.0, saturation=30.0,
        highlights=40.0, shadows=30.0, whites=20.0, texture=40.0, clarity=30.0,
        hsl=HslBands(hue=(10, -10, 0, 20, 0, -15, 0, 5),
                     saturation=(30, 0, -20, 10, 0, 15, 0, 0),
                     luminance=(0, 10, -10, 0, 6, -6, 0, 0)),
        color_grade=ColorGrade(shadow_hue=220, shadow_sat=30, highlight_hue=45,
                               highlight_sat=25, balance=-10),
    )


# --- DevelopOps.blend field semantics --------------------------------------


def test_blend_endpoints_are_exact_and_fields_stay_between():
    """t=0 → self, t=1 → other (byte-exact), and every interpolated scalar stays
    BETWEEN its endpoints (a linear blend never overshoots — a sign/lerp bug
    that produced an out-of-range field would surface as an over-deflected grade)."""
    a, b = DevelopOps(), _graded_ops()
    assert interpolate_pair(a, b, 0.0) == a
    assert interpolate_pair(a, b, 1.0) == b
    mid = interpolate_pair(a, b, 0.5)
    for f in ("exposure_ev", "contrast", "saturation", "highlights", "texture"):
        lo, hi = sorted((getattr(a, f), getattr(b, f)))
        assert lo - 1e-9 <= getattr(mid, f) <= hi + 1e-9
    # Sub-object blends stay in range too (HSL band 0 saturation 0 → 30).
    assert 0.0 <= mid.hsl.saturation[0] <= 30.0
    assert 0.0 <= mid.color_grade.shadow_sat <= 30.0


def interpolate_pair(a: DevelopOps, b: DevelopOps, t: float) -> DevelopOps:
    return a.blend(b, t)


@pytest.mark.parametrize("intent", _INTENTS, ids=lambda i: i.value)
def test_blended_frames_render_valid_across_the_ramp(intent):
    """Every interpolated frame along an identity→grade ramp renders to a FINITE,
    non-negative ProPhoto under both intents — a blended grade can't emit an
    invalid frame mid-transition."""
    a, b = DevelopOps(), _graded_ops()
    chart = vl.pack(vl.build_lattice()).astype(np.float32)
    for t in np.linspace(0.0, 1.0, 9):
        out = apply_develop_ops(chart, a.blend(b, t), intent)
        assert np.isfinite(out).all(), f"t={t}: non-finite"
        assert out.min() >= 0.0, f"t={t}: negative ProPhoto"


@pytest.mark.parametrize("intent", _INTENTS, ids=lambda i: i.value)
def test_near_identity_engaged_band_is_valid_and_continuous(intent):
    """The headline: at t=0.001 the blend is JUST past is_identity() — the real
    OKLCh/ACEScct/guided path runs on a near-zero grade (the short-circuit does
    NOT fire). The result must be (a) VALID and (b) CONTINUOUS with identity — as
    t→0 the change must VANISH (the defining property that distinguishes a smooth
    boundary from a short-circuit JUMP, where leaving identity would snap to a
    finite change independent of t). Continuity is tested as proportionality, NOT
    a fixed absolute bound: some patches legitimately have high gain (a saturated
    ProPhoto-blue has near-zero LUMINANCE — B-weight 8.6e-5 — so a tiny luminance
    grade rescales its bright channel a lot), but that change still scales with t."""
    a, b = DevelopOps(), _graded_ops()
    tiny = a.blend(b, 1e-3)
    # It really is engaged (escaped the byte-exact short-circuit), not identity.
    assert not (tiny.hsl.is_identity() and tiny.color_grade.is_identity())

    # ABOVE-floor luminance only. The near-black guard (fix branch) DELIBERATELY
    # introduces a discontinuity at the identity boundary for sub-floor-LUMINANCE
    # pixels — it neutralises them the instant an op engages, regardless of grade
    # size (a near-zero-luminance saturated ProPhoto-blue is rolled to neutral).
    # Continuity is only a property the pipeline owes ABOVE the floor (where the
    # guard's gate is exactly 1.0); testing it below would assert something the
    # guard correctly does NOT deliver.
    above = [p for p in vl.build_lattice()
             if float(np.array(p.rgb) @ vl._PROPHOTO_LUMINANCE) > 0.02]
    chart = vl.pack(above).astype(np.float32)
    base = apply_develop_ops(chart, a, intent)            # exact identity output
    assert np.isfinite(apply_develop_ops(chart, tiny, intent)).all()

    deltas = [float(np.max(np.abs(apply_develop_ops(chart, a.blend(b, t), intent) - base)))
              for t in (1e-5, 1e-4, 1e-3)]
    # Vanishes as t→0 (no boundary jump — a jump would leave delta(1e-5) finite).
    assert deltas[0] < 0.02, f"discontinuity at the identity boundary: {deltas[0]:.4f}"
    # …and shrinks monotonically toward 0 with t (continuous, ~proportional).
    assert deltas[0] <= deltas[1] <= deltas[2], f"non-monotone near identity: {deltas}"
    assert deltas[0] < 0.25 * deltas[2] + 1e-6, f"change does not scale with t: {deltas}"


# --- the sub-object blends (HslBands / ColorGrade) -------------------------


def test_hslbands_blend_is_per_band_convex():
    """HslBands.blend interpolates each band independently and convexly — band i
    of the blend lies between the two endpoints' band i (no cross-band leakage)."""
    a = HslBands(saturation=(0, 10, -20, 0, 0, 0, 0, 0))
    b = HslBands(saturation=(40, -10, 20, 0, 0, 0, 0, 0))
    mid = a.blend(b, 0.25)
    for i in range(8):
        lo, hi = sorted((a.saturation[i], b.saturation[i]))
        assert lo - 1e-9 <= mid.saturation[i] <= hi + 1e-9


def test_colorgrade_blend_keeps_wheels_valid():
    """ColorGrade.blend interpolates all 14 fields; a blended wheel stays a valid
    grade (Saturation non-negative, the engaged output finite under both intents)."""
    a = ColorGrade()
    b = ColorGrade(shadow_hue=200, shadow_sat=80, highlight_sat=60, balance=40)
    mid = a.blend(b, 0.5)
    assert 0.0 <= mid.shadow_sat <= 80.0 and 0.0 <= mid.highlight_sat <= 60.0
    chart = vl.pack(vl.build_lattice()).astype(np.float32)
    for intent in _INTENTS:
        out = apply_develop_ops(chart, DevelopOps(color_grade=mid), intent)
        assert np.isfinite(out).all() and out.min() >= 0.0


# --- interpolate() over a real keyframe sequence ---------------------------


def test_interpolate_sequence_materializes_valid_per_frame_ops():
    """A sparse-keyframe sequence (identity → grade → identity) materialised to
    per-frame ops and rendered frame-by-frame stays valid throughout — exercises
    interpolate()'s bracketing + DevelopOps.blend over a real LRTSequence."""
    seq = LRTSequence(
        source_frames=[f"f{i:03d}.dng" for i in range(21)],
        keyframes=[
            Keyframe(0, DevelopOps(), is_lrt_keyframe=True),
            Keyframe(10, _graded_ops(), is_lrt_keyframe=True),
            Keyframe(20, DevelopOps(), is_lrt_keyframe=True),
        ],
    )
    per_frame = materialize_all_frames(seq)
    assert len(per_frame) == 21
    # Keyframe passthrough is exact.
    assert per_frame[0] == DevelopOps() and per_frame[10] == _graded_ops()
    chart = vl.pack(vl.build_lattice()).astype(np.float32)
    for i, ops in enumerate(per_frame):
        out = apply_develop_ops(chart, ops, RenderIntent.PERCEPTUAL)
        assert np.isfinite(out).all() and out.min() >= 0.0, f"frame {i} invalid"
    # interpolate() and materialize agree (same single code path).
    assert interpolate(seq, 5) == per_frame[5]
