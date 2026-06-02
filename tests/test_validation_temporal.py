"""Temporal coherence (anti-flicker) of a graded sequence.

A timelapse is judged frame-to-frame: an op that passes every *single-frame*
invariant (finite, in-gamut, neutral-preserving — assertions A–H) can still
FLICKER if it crosses a MODE boundary non-smoothly across frames. The two
boundaries a perceptual grade ramp sweeps through are the OKLCh Luminance
chroma-gate (``_OKLCH_LUM_CHROMA_GATE`` = 0.04) and the ACES RGC engage threshold
(~0.8) — both are designed C1-smooth (smoothstep gate; smooth RGC roll), so a
linear grade ramp must yield a temporally SMOOTH emission (bounded per-pixel
second difference). A regression that made either boundary a hard switch would
read as a one-frame jump here while each frame still passed A–H.

The per-frame ops are built through the real ``interpolation.interpolate`` /
``DevelopOps.blend`` path (so this also guards the interpolation seam under a
render). Content is well-behaved (neutral wedge + moderate-saturation patches at
real luminance); the pathological near-zero-LUMINANCE saturated single-channel
patches are excluded — they have legitimately high, content-driven gain that is
not flicker (covered structurally in test_validation_interpolation).
"""

from __future__ import annotations

import numpy as np
import pytest

from lrt_cinema.develop_ops import _PROPHOTO_LUMINANCE, apply_develop_ops
from lrt_cinema.interpolation import interpolate
from lrt_cinema.ir import (
    ColorGrade,
    DevelopOps,
    HslBands,
    Keyframe,
    LRTSequence,
    RenderIntent,
)
from tests import validation_lattice as vl

# A smooth grade ramp yields max |2nd difference| ~0.1 on this content (the
# exposure ramp's exponential curvature); a one-frame flicker spikes it to ~5
# (measured). 0.6 sits an order of magnitude below the flicker and ~6× above the
# smooth curvature — the teeth leg below proves it discriminates.
_FLICKER_BOUND = 0.6
_N = 21


def _well_behaved_chart() -> np.ndarray:
    """Neutral wedge + moderate-saturation patches at REAL luminance > 0.03 —
    excludes the near-zero-luminance saturated single-channel patches whose
    content-driven gain is not flicker."""
    pats = [
        p for p in vl.build_lattice()
        if (p.is_neutral and 0.02 < p.luma < 3.0)
        or (p.group == "grid" and p.sat in (0.25, 0.5)
            and float(np.array(p.rgb) @ _PROPHOTO_LUMINANCE) > 0.03)
    ]
    return vl.pack(pats).astype(np.float32)


def _ramp_ops() -> list[DevelopOps]:
    """Per-frame ops for an identity→grade ramp, built via the REAL interpolate()
    path over a 2-keyframe sequence. The grade crosses both mode boundaries:
    +exposure pushes bright pixels out of AP1 (RGC engages) and +Saturation/HSL
    grows chroma past the OKLCh c-gate."""
    grade = DevelopOps(
        exposure_ev=1.2, contrast=30.0, saturation=30.0, highlights=40.0,
        shadows=30.0, hsl=HslBands(saturation=(50.0,) * 8),
        color_grade=ColorGrade(shadow_hue=240, shadow_sat=30))
    seq = LRTSequence(
        source_frames=[f"f{i:03d}.dng" for i in range(_N)],
        keyframes=[Keyframe(0, DevelopOps(), True), Keyframe(_N - 1, grade, True)],
    )
    return [interpolate(seq, i) for i in range(_N)]


def _emit_sequence(per_frame: list[DevelopOps]) -> np.ndarray:
    chart = _well_behaved_chart()
    return np.stack([
        vl.emit_acescg(apply_develop_ops(chart, ops, RenderIntent.PERCEPTUAL))
        for ops in per_frame
    ])


def test_grade_ramp_is_temporally_smooth_no_flicker():
    """The headline temporal invariant: a linear grade ramp crossing the c-gate
    and RGC threshold emits a SMOOTH sequence — the per-pixel second difference
    (frame-to-frame acceleration) stays bounded, so no frame jumps relative to its
    neighbours. Each frame also passes the single-frame A-invariants (finite,
    non-negative) — flicker is a PURELY temporal failure A-H cannot see."""
    frames = _emit_sequence(_ramp_ops())
    assert np.isfinite(frames).all() and frames.min() >= 0.0   # per-frame A
    d2 = np.abs(np.diff(frames, axis=0, n=2))
    assert d2.max() < _FLICKER_BOUND, (
        f"temporal flicker: max |2nd-difference| {d2.max():.4f} ≥ {_FLICKER_BOUND}")


def test_flicker_metric_has_teeth_injected_one_frame_jump():
    """Teeth leg (mirrors the halo test's naive-USM leg): injecting a single-frame
    +0.25 EV jump into the otherwise-smooth ramp spikes the same second-difference
    metric WELL past the bound the smooth ramp clears — proving the bound actually
    discriminates flicker, not just that smooth content happens to pass."""
    per_frame = _ramp_ops()
    mid = per_frame[_N // 2]
    from dataclasses import replace
    per_frame[_N // 2] = replace(mid, exposure_ev=mid.exposure_ev + 0.25)
    d2 = np.abs(np.diff(_emit_sequence(per_frame), axis=0, n=2))
    assert d2.max() > _FLICKER_BOUND, (
        f"flicker metric is toothless: injected jump only reached {d2.max():.4f}")


def test_exposure_ramp_emission_valid_every_frame():
    """Each frame of a wide exposure ramp (−2…+3 EV, sweeping deep shadow → clipped
    highlight, crossing the RGC threshold) is individually valid — finite, no
    negative AP1 — so the per-frame contract holds across the whole dynamic sweep,
    not just at one exposure."""
    chart = _well_behaved_chart()
    for i in range(_N):
        ev = -2.0 + 5.0 * i / (_N - 1)
        ace = vl.emit_acescg(
            apply_develop_ops(chart, DevelopOps(exposure_ev=ev), RenderIntent.PERCEPTUAL))
        assert np.isfinite(ace).all(), f"EV={ev:.2f}: non-finite"
        assert ace.min() >= 0.0, f"EV={ev:.2f}: negative AP1"


@pytest.mark.parametrize("intent", list(RenderIntent), ids=lambda i: i.value)
def test_neutral_wedge_has_no_temporal_chroma_flicker(intent):
    """A neutral wedge under the grade ramp must stay neutral EVERY frame (no
    frame where a mode transition briefly tints it) — temporal neutral stability,
    both intents. The per-frame neutral invariant, asserted across time."""
    pats = [p for p in vl.build_lattice() if p.is_neutral and 0.02 < p.luma < 3.0]
    chart = vl.pack(pats).astype(np.float32)
    # ColorGrade-free ramp (ColorGrade tints neutrals by design); luminance + HSL.
    grade = DevelopOps(exposure_ev=1.0, contrast=30.0, saturation=40.0,
                       highlights=40.0, shadows=40.0,
                       hsl=HslBands(saturation=(60.0,) * 8))
    for i in range(_N):
        ops = DevelopOps().blend(grade, i / (_N - 1))
        ace = vl.emit_acescg(apply_develop_ops(chart, ops, intent))
        col = vl.chroma_over_luma(ace.reshape(-1, 3))
        assert col.max() < 3e-3, f"frame {i}: neutral tinted, chroma/luma={col.max():.2e}"
