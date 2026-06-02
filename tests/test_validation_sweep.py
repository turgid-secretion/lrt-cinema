"""Invariant-based validation net for the Stage-12 perceptual grade + emission.

This is the targeted net the 358-test suite lacked: it drives the **enumerated
near-black lattice** (``tests/validation_lattice.py`` — log luma down to 1e-5,
NOT ``np.random`` whose median ~0.5 never reaches the failure region) through
every Stage-12 transform under BOTH render intents and asserts on the **DECODED
emission** (ACEScg / AP0 / display) the pure invariants any correct grade must
satisfy — invariants that hold *regardless of whether the chosen OKLCh/ACEScct
math is right*. It NEVER reimplements that math as an oracle (that would rebuild
the very trap the perceptual ops fell into); the independent oracle for transfer
curves is ``colour-science`` (pinned), used for display round-trips only.

The headline regression — the perceptual ops casting near-black NEUTRALS to a
saturated red/blue + emitting negative AP1 channels in the scene-linear ACEScg
EXR, while faithful renders the same grade clean — is caught here as an
xfail(strict) catcher that **flips automatically when the fix lands** (the
``BUG_PRESENT`` sentinel below). Faithful is the validity cross-check: both
intents must be colour-valid (they may legitimately diverge on *look*).

See ``test_validation_spatial.py`` (halo/ringing on real 2-D fields),
``test_validation_temporal.py`` (flicker), ``test_validation_interpolation.py``
(the keyframe-blend path), and the near-black extensions in
``test_accel_kernels.py`` / ``test_accel_mlx.py``.
"""

from __future__ import annotations

import numpy as np
import pytest

from lrt_cinema.develop_ops import (
    _apply_color_grade_perceptual,
    _apply_contrast_perceptual,
    _apply_hsl_perceptual,
    apply_color_grade,
    apply_contrast_2012,
    apply_develop_ops,
    apply_dr_compression,
    apply_hsl,
    apply_saturation,
    apply_texture_clarity,
    apply_vibrance,
)
from lrt_cinema.ir import ColorGrade, DevelopOps, HslBands, RenderIntent
from tests import validation_lattice as vl
from tests.validation_lattice import NB_CHROMA as _NB_CHROMA
from tests.validation_lattice import nearblack_xfail

# The auto-flipping near-black bug sentinel lives in `validation_lattice` (shared
# with test_grading_sweep). `nearblack_xfail()` builds a conditional xfail(strict)
# that catches the bug on buggy main and flips to live+passing when the
# `_nearblack_gate` fix lands. See validation_lattice.BUG_PRESENT for the why.
_NEARBLACK_XFAIL = nearblack_xfail()

# A near-black engaged grade exercises BOTH documented mechanism arms:
#  * Blacks(-10) floors a dark slightly-chromatic pixel's small channels to 0 →
#    degenerate single-channel pixel; then a shadow-LIFT (Contrast<0 / +Shadows)
#    amplifies it via the lum_out/lum ratio (DECISIONS §7 amendment);
#  * an ACEScct-log CDL shadow Saturation injects a near-black cast through the toe.
_NEARBLACK_GRADES = {
    "contrast_lift": DevelopOps(blacks=-10.0, contrast=-20.0),
    "dr_shadows":    DevelopOps(blacks=-10.0, shadows=80.0),
    "cdl_shadow":    DevelopOps(blacks=-10.0,
                                color_grade=ColorGrade(shadow_hue=240.0, shadow_sat=90.0)),
}


# ===========================================================================
# RED CATCHERS — near-black neutral-preservation + sign on the DECODED master.
# (Assertion B = neutral-preservation; C = no negatives. Both fail on buggy
# main; both must hold once the guard lands. "finite" is NOT here — it already
# passes on main, so it is a green invariant below, never a strict catcher.)
# ===========================================================================


@_NEARBLACK_XFAIL
@pytest.mark.parametrize("grade", list(_NEARBLACK_GRADES), ids=list(_NEARBLACK_GRADES))
def test_perceptual_nearblack_neutral_preserved_in_emitted_acescg(grade):
    """A near-black, near-neutral field graded under PERCEPTUAL must emit a
    near-NEUTRAL, NON-NEGATIVE ACEScg master — never a saturated false cast. The
    enumerated field (no RNG) straddles the Blacks(-10) bias so the floor leaves
    the degenerate single-channel pixels the lift/toe amplify. On buggy main the
    cast reaches chroma O(0.1–40) (catcher B); the guard rolls it to neutral."""
    x = vl.nearblack_chromatic_field()
    ops = _NEARBLACK_GRADES[grade]
    ace = vl.emit_acescg(apply_develop_ops(x, ops, RenderIntent.PERCEPTUAL))
    assert ace.min() >= 0.0, f"emitted ACEScg has negatives: min={ace.min():.6f}"   # C
    assert vl.max_abs_chroma(ace).max() < _NB_CHROMA, (                              # B
        f"near-black false cast: max chroma {vl.max_abs_chroma(ace).max():.4f}")


@_NEARBLACK_XFAIL
def test_perceptual_nearblack_clean_through_real_exr_roundtrip(tmp_path):
    """The task's mandated end-to-end leg: grade → write a REAL lossless EXR
    (zip/half — NOT the DWAB default, whose DCT can quantise a flat cast away) →
    read it back → assert neutral + non-negative. This proves the invariant on
    the bytes that actually ship, not just an in-memory array."""
    pytest.importorskip("OpenEXR")
    x = vl.nearblack_chromatic_field()
    graded = apply_develop_ops(x, _NEARBLACK_GRADES["contrast_lift"],
                               RenderIntent.PERCEPTUAL)
    dec = vl.roundtrip_exr(graded, tmp_path / "nearblack.exr",
                           compression="zip", bit_depth="half")
    assert dec.min() >= 0.0, f"shipped EXR has negative AP1: min={dec.min():.6f}"
    assert vl.max_abs_chroma(dec).max() < _NB_CHROMA, (
        f"shipped EXR near-black cast: {vl.max_abs_chroma(dec).max():.4f}")


@_NEARBLACK_XFAIL
def test_perceptual_nearblack_proven_production_repro():
    """The documented production mechanism (DECISIONS §7 near-black amendment),
    reproduced as a deterministic seeded field straddling the Blacks bias — the
    same construction the fix-branch regression uses. This leg additionally
    exercises the NEGATIVE-AP1 population (the '0.62% → 0.000%' symptom) that the
    RGC cannot rescue at near-black, alongside the cast."""
    rng = np.random.default_rng(0)  # seeded → deterministic; reaches L≈0.005
    base = (0.0045 + 0.001 * rng.random((48, 48, 1))).astype(np.float32)
    x = (base * (1.0 + 0.06 * (rng.random((48, 48, 3)) - 0.5))).astype(np.float32)
    ops = DevelopOps(blacks=-10.0, contrast=-20.0)
    pe = vl.emit_acescg(apply_develop_ops(x, ops, RenderIntent.PERCEPTUAL))
    fa = vl.emit_acescg(apply_develop_ops(x, ops, RenderIntent.FAITHFUL))
    assert pe.min() >= 0.0, f"emitted negatives: min={pe.min():.6f}"          # C
    assert vl.max_abs_chroma(pe).max() < _NB_CHROMA                            # B
    assert np.max(np.abs(pe - fa)) < _NB_CHROMA   # H: guarded perceptual ≈ faithful neutral


@_NEARBLACK_XFAIL
def test_perceptual_nearblack_degenerate_pixels_no_cast():
    """The exact degenerate single-channel near-black pixels DECISIONS.md cites
    (`[0,0,2.6e-6]` pure-blue, `[1.9e-6,0,0]` pure-red — the shape `apply_blacks_
    2012` leaves) must not be amplified into a saturated cast by a perceptual
    shadow-lift. Drives the mechanism without needing Blacks itself."""
    deg = np.array([
        [[0.0, 0.0, 2.6e-6]], [[1.9e-6, 0.0, 0.0]], [[0.0, 3.0e-6, 0.0]],
        [[2.6e-6, 0.0, 1.0e-6]],
    ], dtype=np.float32)
    ace = vl.emit_acescg(_apply_contrast_perceptual(deg, -20.0))
    assert ace.min() >= 0.0
    assert vl.max_abs_chroma(ace).max() < _NB_CHROMA, (
        f"degenerate near-black amplified to chroma {vl.max_abs_chroma(ace).max():.4f}")


# ===========================================================================
# GREEN COMPANIONS — pass on buggy main; lock the contract for the fixed state.
# ===========================================================================


# Faithful is the clean baseline ONLY for the luminance-arm grades: per-channel
# Contrast2012 lifts every channel toward the 0.18 pivot, so a near-black pixel
# goes neutral regardless of imbalance. The CDL grade is EXCLUDED — faithful's
# split-tone ColorGrade *intentionally tints* near-black shadows (a far stronger,
# correct tint than the perceptual toe cast); "faithful is clean" is false there
# by design, so it is not a cross-intent baseline for the CDL arm.
@pytest.mark.parametrize("grade", ["contrast_lift", "dr_shadows"])
def test_faithful_nearblack_is_clean_same_input(grade):
    """The SAME near-black LUMINANCE grade through FAITHFUL emits clean (neutral,
    no negatives) — faithful is immune for free (per-channel pivot lift →
    neutral). This is what the perceptual guard restores; faithful never needed
    it. (DR-compression is dropped on faithful, so dr_shadows is a pure
    pass-through there — trivially neutral on the near-neutral field.)"""
    x = vl.nearblack_chromatic_field()
    ace = vl.emit_acescg(apply_develop_ops(x, _NEARBLACK_GRADES[grade],
                                           RenderIntent.FAITHFUL))
    assert ace.min() >= 0.0
    assert vl.max_abs_chroma(ace).max() < _NB_CHROMA


@pytest.mark.parametrize("intent", list(RenderIntent), ids=lambda i: i.value)
@pytest.mark.parametrize("grade", list(_NEARBLACK_GRADES), ids=list(_NEARBLACK_GRADES))
def test_nearblack_emission_is_finite_both_intents(intent, grade):
    """A finite (no NaN/Inf) emission — the floor invariant that holds on buggy
    main too (the cast is finite). Kept GREEN, separate from the strict catchers,
    so a future NaN regression is caught without coupling to the cast bound."""
    x = vl.nearblack_chromatic_field()
    ace = vl.emit_acescg(apply_develop_ops(x, _NEARBLACK_GRADES[grade], intent))
    assert np.isfinite(ace).all()


def test_pure_black_and_pure_neutral_stay_neutral_both_intents():
    """Positive controls: a PURE neutral (incl. true black) cannot cast under any
    op — all channels are equal, so the Blacks floor and every ratio reapply act
    on them identically. Passes on main AND fixed; guards a regression that would
    break the most basic neutral. (Pure black is why the task's `(0.0008)³`
    suggestion can't reproduce the bug — the cast needs an IMBALANCE.)"""
    neutrals = np.array([
        [[0.0, 0.0, 0.0]], [[1e-4, 1e-4, 1e-4]], [[5e-3, 5e-3, 5e-3]],
        [[0.18, 0.18, 0.18]],
    ], dtype=np.float32)
    for intent in RenderIntent:
        # An aggressive luminance-domain grade (no ColorGrade — that tints
        # neutrals by design); neutrals must survive it neutral.
        ops = DevelopOps(blacks=-10.0, contrast=-30.0, shadows=70.0, texture=60.0,
                         saturation=80.0)
        ace = vl.emit_acescg(apply_develop_ops(neutrals, ops, intent))
        assert ace.min() >= 0.0
        # A true neutral emits the Bradford-floor chroma only (≪ _NB_CHROMA).
        assert vl.max_abs_chroma(ace).max() < _NB_CHROMA


# ===========================================================================
# A — finiteness across the whole lattice × transforms × intents × emissions.
# ===========================================================================

_LATTICE = vl.build_lattice()
_CHART = vl.pack(_LATTICE)  # (N,1,3) — non-spatial ops only (see lattice docstring)

# Each transform: (callable taking a packed chart, label, intents it is valid in).
_TRANSFORMS = {
    "saturation": lambda c: apply_saturation(c, 60.0),
    "vibrance": lambda c: apply_vibrance(c, 60.0),
    "hsl_faithful": lambda c: apply_hsl(c, HslBands(saturation=(50.0,) * 8)),
    "color_grade_faithful": lambda c: apply_color_grade(
        c, ColorGrade(global_hue=120.0, global_sat=60.0)),
    "contrast_faithful": lambda c: apply_contrast_2012(c, 50.0),
    "hsl_perceptual": lambda c: _apply_hsl_perceptual(c, HslBands(saturation=(50.0,) * 8)),
    "cdl_perceptual": lambda c: _apply_color_grade_perceptual(
        c, ColorGrade(global_hue=120.0, global_sat=60.0)),
    "contrast_perceptual": lambda c: _apply_contrast_perceptual(c, 50.0),
    "dr_compression": lambda c: apply_dr_compression(c, 60.0, 40.0, 30.0),
    "texture_clarity": lambda c: apply_texture_clarity(c, 60.0, 40.0),
    "develop_faithful": lambda c: apply_develop_ops(
        c, DevelopOps(contrast=20.0, saturation=15.0,
                      hsl=HslBands(saturation=(30.0,) * 8)), RenderIntent.FAITHFUL),
    "develop_perceptual": lambda c: apply_develop_ops(
        c, DevelopOps(contrast=20.0, saturation=15.0, highlights=30.0, shadows=20.0,
                      hsl=HslBands(saturation=(30.0,) * 8)), RenderIntent.PERCEPTUAL),
}


@pytest.mark.parametrize("name", list(_TRANSFORMS))
def test_transform_output_is_finite_and_floored(name):
    """Every transform on the full lattice emits finite ProPhoto floored at 0 (no
    negative ProPhoto channel reaches output.py's colour matrix — the
    apply_saturation lesson, swept across the near-black-inclusive lattice)."""
    out = _TRANSFORMS[name](_CHART)
    assert np.isfinite(out).all(), f"{name}: non-finite ProPhoto"
    assert out.min() >= 0.0, f"{name}: negative ProPhoto channel ({out.min():.4f})"


@pytest.mark.parametrize("name", list(_TRANSFORMS))
def test_transform_emitted_acescg_is_finite(name):
    """Every transform's output, taken all the way to the DECODED ACEScg master,
    is finite (the NaN-scrub is asserted separately; here the maths must not
    PRODUCE NaN/Inf on any lattice patch incl. the overrange/out-of-AP1 ones)."""
    out = _TRANSFORMS[name](_CHART.astype(np.float32))
    ace = vl.emit_acescg(out)
    assert np.isfinite(ace).all(), f"{name}: non-finite emitted ACEScg"


# ===========================================================================
# C / sign discrimination on the DECODED master.
# ===========================================================================


def test_rgc_compresses_out_of_gamut_keeping_the_achromatic_peak():
    """Sign discrimination (assertion C) — RGC is a smooth COMPRESSION, not a
    hard clip. A legit out-of-AP1 colour (a ProPhoto primary, which has genuine
    NEGATIVE AP1 channels pre-RGC) emerges with: (a) its negative channels rolled
    UP toward the achromatic axis (min raised), and (b) the MAX (achromatic) chan
    EXACTLY preserved — RGC's defining invariant (distance 0 on the max channel),
    so the luminance peak never darkens and grey→grey. Distinct from the
    near-black cast (the RGC pass CANNOT fix that — it is the upstream guard's job
    — because its correction scales by |ach|≈0)."""
    from lrt_cinema import output
    prim = np.array([[[1.0, 0.0, 0.0]], [[0.0, 1.0, 0.0]], [[0.0, 0.0, 1.0]]],
                    dtype=np.float32)
    raw = output._prophoto_to_linear(prim, "acescg")     # pre-RGC
    ace = vl.emit_acescg(prim)                            # post-RGC
    assert np.isfinite(ace).all()
    flat_raw, flat_ace = raw.reshape(-1, 3), ace.reshape(-1, 3)
    for raw_px, ace_px in zip(flat_raw, flat_ace, strict=True):
        assert raw_px.min() < 0.0, "primary should be out-of-AP1 (negative) pre-RGC"
        assert ace_px.min() > raw_px.min(), "RGC did not compress the negative channel"
        # The max (achromatic-distance-0) channel is invariant under RGC.
        np.testing.assert_allclose(ace_px.max(), raw_px.max(), rtol=1e-5)


def test_rgc_leaves_a_bounded_compressed_residual_beyond_the_limit():
    """An excursion BEYOND the per-channel RGC limit stays compressed-but-bounded
    by design (asymptote threshold+scale ≈ 1.03–1.14, never collapsing to the
    boundary), so RGC is compression — never a clamp that would posterise. The
    residual is finite and bounded, not run-away."""
    # A strongly out-of-AP1 saturated colour (overrange primary push).
    far = np.array([[[6.0, 0.0, 0.0]], [[0.0, 0.0, 6.0]]], dtype=np.float32)
    ace = vl.emit_acescg(far)
    assert np.isfinite(ace).all()
    assert ace.min() > -2.0, f"RGC residual unbounded: {ace.min():.4f}"


def test_display_emission_is_clipped_to_unit_range():
    """Display targets (sRGB TIFF) MUST clip to [0,1] — the out-of-gamut
    excursions the perceptual ops leave are resolved by the display encoder's own
    clip, NOT RGC (PIPELINE.md §7: a perceptual render to a display TIFF gets no
    RGC, and that is correct)."""
    pytest.importorskip("tifffile")
    over = np.array([[[4.0, 0.5, 0.1]], [[1.0, 0.0, 0.0]], [[0.0, 0.0, 0.0]]],
                    dtype=np.float32)
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        dec = vl.roundtrip_tiff(over, f"{d}/o.tif", colorspace="srgb", bit_depth=16)
    assert dec.min() >= 0.0 and dec.max() <= 1.0


# ===========================================================================
# E / F — monotone luma & no hue-flip under a luminance grade.
# ===========================================================================


def test_perceptual_contrast_is_luma_monotone_on_neutral_wedge():
    """E: a positive perceptual Contrast is monotone in input luminance on the
    neutral wedge — brighter in → brighter (or equal) out, no tone inversion."""
    wedge = np.array([[[v, v, v]] for v in np.geomspace(1e-4, 4.0, 40)],
                     dtype=np.float64)
    out_lum = (_apply_contrast_perceptual(wedge, 60.0) @ vl._PROPHOTO_LUMINANCE).reshape(-1)
    assert np.all(np.diff(out_lum) >= -1e-9), "perceptual contrast inverted tone"


@pytest.mark.parametrize("name,op", [
    ("contrast_perceptual", lambda c: _apply_contrast_perceptual(c, -50.0)),
    ("dr_compression", lambda c: apply_dr_compression(c, 60.0, 50.0, 0.0)),
    ("texture_clarity", lambda c: apply_texture_clarity(c, 80.0, 60.0)),
])
def test_ratio_reapply_ops_never_flip_hue(name, op):
    """F (no hue-flip): the §0 luminance-domain perceptual ops reapply by the
    out/in luminance RATIO — a per-pixel POSITIVE scalar — so a saturated pixel's
    full channel ORDER (and thus its hue) is preserved EXACTLY, even under an
    aggressive shadow-lift across the whole saturated lattice. A per-channel op
    (the faithful Contrast2012) would reorder channels on saturated colour; these
    cannot. (The OKLCh-HSL Luminance band preserves OKLCh *hue* by construction
    but legitimately reshuffles the RGB representation near gamut edges, so RGB
    channel order is the wrong probe THERE — it is the right probe for the
    ratio-reapply ops, whose contract IS exact RGB-ratio preservation.)"""
    sat = vl.pack([p for p in _LATTICE if p.group == "grid" and p.sat == 1.0
                   and 1e-3 < p.luma < 2.0]).astype(np.float64)
    order_in = np.argsort(sat.reshape(-1, 3), axis=-1)
    order_out = np.argsort(op(sat).reshape(-1, 3), axis=-1)
    np.testing.assert_array_equal(order_in, order_out)


# ===========================================================================
# B — neutral-preservation for the achromatic-preserving ops, both intents.
# (ColorGrade/CDL are EXCLUDED: they tint neutrals by design — split-tone.)
# ===========================================================================

_NEUTRAL_PRESERVING = {
    "saturation": lambda c: apply_saturation(c, 80.0),
    "vibrance": lambda c: apply_vibrance(c, 80.0),
    "hsl_faithful": lambda c: apply_hsl(c, HslBands(saturation=(80.0,) * 8,
                                                    luminance=(50.0,) * 8)),
    "hsl_perceptual": lambda c: _apply_hsl_perceptual(
        c, HslBands(saturation=(80.0,) * 8, luminance=(50.0,) * 8)),
    "contrast_perceptual": lambda c: _apply_contrast_perceptual(c, 50.0),
    "dr_compression": lambda c: apply_dr_compression(c, 50.0, 40.0, 30.0),
    "texture_clarity": lambda c: apply_texture_clarity(c, 60.0, 40.0),
}


@pytest.mark.parametrize("name", list(_NEUTRAL_PRESERVING))
def test_neutral_wedge_stays_neutral_in_emission(name):
    """B (headline): a neutral wedge (sat=0, every luma incl. near-black) stays
    neutral through each achromatic-preserving op, measured on the DECODED ACEScg
    (chroma/luma vs the Bradford floor — luma-invariant, so it is not fooled by
    the bright end's larger absolute chroma). HSL/Sat/Vib must protect neutrals
    (s_gate/c_gate); the luminance ops are achromatic by construction."""
    idx = vl.neutral_indices(_LATTICE)
    out = _NEUTRAL_PRESERVING[name](_CHART.astype(np.float32))
    ace = vl.emit_acescg(out).reshape(-1, 3)[idx]
    col = vl.chroma_over_luma(ace)
    # Bradford neutral floor is ≈3.1e-4; allow 10× for op float error. A cast
    # would be O(1)+ here.
    assert col.max() < 3e-3, f"{name}: neutral cast, chroma/luma={col.max():.2e}"
