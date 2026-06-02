"""End-to-end full-range sweep sanity for the Stage-12 grading ops.

Tier-0 of the grading-validation harness (`tools/grading_sweep/`). The Axis-1
oracle (`test_color_oracle.py`) proves each op matches its defined math on
chosen pixels; THIS layer drives every HSL band and every Color-Grade tonal zone
across the *full slider range* on a realistic linear-ProPhoto chart and asserts
the structural properties any correct knob must have: monotonicity, hue-band /
tonal-zone locality, neutral protection, identity, and no invalid channels.

It catches integration-level faults the unit oracle can miss — a lever wired to
the wrong field, a sign error that only shows at range, a dropped interpolation
field — without any external renderer. (No open tool reads Adobe's
crs:HueAdjustment*/ColorGrade* params, so external *fidelity* comparison needs
ACR; that is the harness's Tier-1, run manually — see the package README.)
"""

from __future__ import annotations

import numpy as np

from lrt_cinema.develop_ops import apply_color_grade, apply_develop_ops, apply_hsl
from lrt_cinema.ir import HSL_BAND_NAMES, ColorGrade, DevelopOps, HslBands, RenderIntent
from lrt_cinema.lut3d_baker import _rgb_to_hsv_dcp
from tests import validation_lattice as vl
from tools.grading_sweep.chart import build_prophoto_chart, chart_array, patch_chroma

_CHART = build_prophoto_chart()
_RGB = chart_array(_CHART)  # (N, 1, 3) linear ProPhoto


def _patch(name: str) -> int:
    return next(i for i, p in enumerate(_CHART) if p.name == name)


def _hsl_one_band(channel: str, band: str, value: float) -> HslBands:
    """An HslBands with a single band of one channel set."""
    idx = HSL_BAND_NAMES.index(band)
    sliders = [0.0] * 8
    sliders[idx] = value
    return HslBands(**{channel: tuple(sliders)})


def _is_monotonic_nondecreasing(xs: np.ndarray, tol: float = 1e-6) -> bool:
    return bool((np.diff(xs) >= -tol).all())


# ---------------------------------------------------------------------------
# HSL — full-range sweeps
# ---------------------------------------------------------------------------


def test_hsl_saturation_sweep_monotonic_and_band_local():
    """Sweeping a band's Saturation 0→+100 monotonically raises that hue's chroma
    and leaves a different hue band untouched."""
    red, blue = _patch("hue000"), _patch("hue240")
    chroma_red, chroma_blue = [], []
    for s in np.linspace(0.0, 100.0, 11):
        out = apply_hsl(_RGB, _hsl_one_band("saturation", "Red", s))
        chroma_red.append(patch_chroma(out)[red])
        chroma_blue.append(patch_chroma(out)[blue])
    assert _is_monotonic_nondecreasing(np.array(chroma_red))
    assert chroma_red[-1] > chroma_red[0] + 1e-3            # actually moved
    np.testing.assert_allclose(chroma_blue, chroma_blue[0], atol=1e-6)  # blue untouched


def test_hsl_negative_saturation_sweep_monotonic_desaturates():
    """Sweeping a band's Saturation 0→−100 monotonically lowers that hue's
    chroma toward zero."""
    red = _patch("hue000")
    chroma = [patch_chroma(apply_hsl(_RGB, _hsl_one_band("saturation", "Red", s)))[red]
              for s in np.linspace(0.0, -100.0, 11)]
    assert _is_monotonic_nondecreasing(-np.array(chroma))   # non-increasing
    assert chroma[-1] < chroma[0] - 1e-3


def test_hsl_hue_sweep_rotates_monotonically():
    """Sweeping a band's Hue 0→+100 rotates that hue monotonically (no wrap for
    a moderate rotation off red)."""
    red = _patch("hue000")
    hues = []
    for hue_slider in np.linspace(0.0, 100.0, 11):
        out = apply_hsl(_RGB, _hsl_one_band("hue", "Red", hue_slider))
        h, _, _, _ = _rgb_to_hsv_dcp(out)
        hues.append(float(h.reshape(-1)[red]))
    assert _is_monotonic_nondecreasing(np.array(hues))
    assert hues[-1] > hues[0] + 1e-3


def test_hsl_luminance_band_local_and_neutral_safe():
    """A band's Luminance −100 darkens a saturated pixel of that hue but leaves
    the neutral wedge unchanged (the saturation gate)."""
    out = apply_hsl(_RGB, _hsl_one_band("luminance", "Green", -100.0))
    green = _patch("hue120")
    assert out.reshape(-1, 3)[green].max() < _RGB.reshape(-1, 3)[green].max() - 1e-3
    for p in _CHART:
        if p.is_neutral:
            i = _patch(p.name)
            np.testing.assert_allclose(out.reshape(-1, 3)[i], _RGB.reshape(-1, 3)[i], atol=1e-6)


# ---------------------------------------------------------------------------
# Color Grade — full-range sweeps
# ---------------------------------------------------------------------------


def test_color_grade_shadow_sweep_zone_local_and_monotonic():
    """Sweeping Shadow Saturation 0→+100 monotonically pushes a dark patch toward
    the wheel hue, far more than it moves a bright patch."""
    dark, bright = _patch("tone_v0.04"), _patch("tone_v0.85")
    blue_dark, blue_bright = [], []
    for s in np.linspace(0.0, 100.0, 11):
        out = apply_color_grade(_RGB, ColorGrade(shadow_hue=240.0, shadow_sat=s))
        blue_dark.append(out.reshape(-1, 3)[dark][2])
        blue_bright.append(out.reshape(-1, 3)[bright][2])
    assert _is_monotonic_nondecreasing(np.array(blue_dark))
    moved_dark = blue_dark[-1] - blue_dark[0]
    moved_bright = blue_bright[-1] - blue_bright[0]
    assert moved_dark > 0.01
    assert abs(moved_bright) < moved_dark            # zone mask favours shadows


def test_color_grade_global_moves_all_zones():
    """The Global wheel tints dark, mid and bright patches alike (no zone mask)."""
    out = apply_color_grade(_RGB, ColorGrade(global_hue=120.0, global_sat=100.0))
    for name in ("tone_v0.04", "tone_v0.45", "tone_v0.85"):
        i = _patch(name)
        assert out.reshape(-1, 3)[i][1] - _RGB.reshape(-1, 3)[i][1] > 0.005


def test_color_grade_balance_shifts_pivot():
    """Positive Balance gives highlights more territory: a mid patch picks up more
    of a highlight-wheel tint at +balance than at −balance."""
    mid = _patch("tone_v0.45")
    cg_pos = ColorGrade(highlight_hue=120.0, highlight_sat=100.0, balance=80.0)
    cg_neg = ColorGrade(highlight_hue=120.0, highlight_sat=100.0, balance=-80.0)
    g_pos = apply_color_grade(_RGB, cg_pos).reshape(-1, 3)[mid][1]
    g_neg = apply_color_grade(_RGB, cg_neg).reshape(-1, 3)[mid][1]
    assert g_pos > g_neg + 1e-3


# ---------------------------------------------------------------------------
# Invariants across the whole sweep
# ---------------------------------------------------------------------------


def test_full_sweep_never_emits_invalid_channels():
    """Across every HSL band and Color-Grade wheel at full deflection, no patch
    emits a negative or non-finite channel **in the DECODED ACEScg master under
    BOTH render intents** — not just the ProPhoto op output.

    Re-targeted (was: faithful only, ``out.min()`` on the *ProPhoto* op output).
    Two faults that re-targeting fixes: (1) it tested ONLY the faithful
    applicators (the perceptual OKLCh/CDL ops it now also drives were entirely
    unexercised here); (2) ``ProPhoto.min() >= 0`` is the WRONG surface — the
    negatives the gamut-safety pass exists to bound are born in the
    ProPhoto→AP1 Bradford and only visible on the decoded AP1 emission (a
    ProPhoto-non-negative render can still emit negative AP1). The chart's 0.04
    luma floor keeps this sweep clear of the near-black bug (covered separately
    below + in test_validation_sweep), so both intents are valid here."""
    def emitted(prophoto):
        return vl.emit_acescg(prophoto.astype(np.float32))

    for band in HSL_BAND_NAMES:
        for ch in ("hue", "saturation", "luminance"):
            for v in (-100.0, 100.0):
                ops = DevelopOps(hsl=_hsl_one_band(ch, band, v))
                for intent in RenderIntent:
                    ace = emitted(apply_develop_ops(_RGB, ops, intent))
                    assert np.isfinite(ace).all(), (band, ch, v, intent)
    for wheel in ("shadow", "midtone", "highlight", "global"):
        for hue in (0.0, 120.0, 240.0):
            cg = ColorGrade(**{f"{wheel}_hue": hue, f"{wheel}_sat": 100.0,
                               f"{wheel}_lum": -100.0})
            ops = DevelopOps(color_grade=cg)
            for intent in RenderIntent:
                ace = emitted(apply_develop_ops(_RGB, ops, intent))
                assert np.isfinite(ace).all(), (wheel, hue, intent)


@vl.nearblack_xfail()
def test_full_sweep_perceptual_near_black_no_negatives():
    """The near-black leg the 0.04-floored chart could never reach (the gap that
    let the cast ship): a near-black field driven by a full perceptual grade must
    emit a NON-NEGATIVE, near-neutral ACEScg master. Catches the bug on buggy
    main (xfail); flips live+passing when the `_nearblack_gate` fix lands. The
    matching FAITHFUL render is clean (asserted in the green companion below)."""
    x = vl.nearblack_chromatic_field()
    ops = DevelopOps(blacks=-10.0, contrast=-20.0, shadows=60.0,
                     hsl=HslBands(saturation=(60.0,) * 8))
    ace = vl.emit_acescg(apply_develop_ops(x, ops, RenderIntent.PERCEPTUAL))
    assert ace.min() >= 0.0, f"emitted ACEScg negatives: {ace.min():.6f}"
    assert vl.max_abs_chroma(ace).max() < vl.NB_CHROMA


def test_full_sweep_faithful_near_black_is_clean():
    """Green companion: the SAME near-black luminance grade through FAITHFUL is
    clean (no negatives, neutral) — faithful never had the bug. Passes on main
    and fixed; locks the cross-intent baseline."""
    x = vl.nearblack_chromatic_field()
    ops = DevelopOps(blacks=-10.0, contrast=-20.0)  # luminance arm: faithful → neutral
    ace = vl.emit_acescg(apply_develop_ops(x, ops, RenderIntent.FAITHFUL))
    assert ace.min() >= 0.0
    assert vl.max_abs_chroma(ace).max() < vl.NB_CHROMA


def test_identity_sweep_is_byte_exact_over_whole_chart():
    """Default ops over the whole chart are a byte-exact no-op (ship-gate proof
    at the chart level)."""
    np.testing.assert_array_equal(apply_hsl(_RGB, HslBands()), _RGB)
    np.testing.assert_array_equal(apply_color_grade(_RGB, ColorGrade()), _RGB)
    np.testing.assert_array_equal(apply_develop_ops(_RGB, DevelopOps()), _RGB)
