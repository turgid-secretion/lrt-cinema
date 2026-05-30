"""Axis 2 — absolute colorimetric accuracy at the colorimetric tap.

The middle axis of the validation harness (docs/VALIDATION.md §"Validation
axes — never conflate them"):

  Axis 1  implementation correctness   vs our own maths     → ~0 (test_color_oracle.py)
  Axis 2  ABSOLUTE accuracy            vs CIE truth          → nonzero FLOOR  (here)
  Axis 3  appearance vs LRT preview    vs the .lrtpreview    → nonzero floor (tools/…)

"Absolute accuracy" asks how close the pipeline's colour gets to CIE truth
computed from spectra (ISO 17321-1). It has an **irreducible nonzero floor**:
a real camera SSF violates the Luther condition, so no 3×3 (Adobe's
ForwardMatrix included) reproduces CIE XYZ exactly — the residual is the
profile fit, NOT a bug. Every ΔE here is reported against an *independently
computed* floor (`synthetic_chart.ssf_lstsq_floor`, a pure-SSF least-squares
solve), so the floor can never silently absorb a pipeline error.

Measurement happens at the **colorimetric tap** — Stage-4 linear ProPhoto(D50)
(or Stage-3 XYZ(D50)), post-ForwardMatrix and BEFORE HueSatMap / ExposureRamp
/ LookTable / ProfileToneCurve. Measuring the rendered image instead would
measure Adobe's pictorial tone curve + look, not pipeline colour error.

The chart, ground truth and synthetic camera RGB all come from
`tests/synthetic_chart.py` (colour-science spectral data — deterministic, no
RAW fixture, no network). Two end-to-end legs:

  * **autonomous** — a white-constrained ForwardMatrix fit to the D5100 SSF is
    injected as a synthetic profile; runs everywhere colour-science is present.
  * **real Adobe DCP** — the shipped Nikon D5100 *Adobe Standard* profile
    (skip-gated on a macOS Adobe install). NB: the *Camera Standard* profiles
    are NOT colorimetric (their ForwardMatrix is the ProPhoto→XYZ passthrough —
    the colour look lives in the LookTable/ToneCurve); see
    `test_camera_standard_forward_matrix_is_not_colorimetric`.

(Supersedes the v0.6 self-test legs that round-tripped a synthetic linear
Rec.2020 chart through a Lab(D55) harness and gated a since-removed
`cinema-linear` preset — that compared at the wrong tap and used a delivery
gamut as a working space. The reference fixture under fixtures/colorchecker/
is now unused by tests; kept for the documented real-chart drop-in workflow.)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

colour = pytest.importorskip("colour")  # noqa: F841  (used throughout; gate import)

from lrt_cinema.dcp import DCPProfile, parse_dcp  # noqa: E402
from lrt_cinema.pipeline import apply_adobe_pipeline  # noqa: E402
from tests import synthetic_chart as sc  # noqa: E402

# Independent ROMM/ProPhoto-linear → XYZ(D50) matrix (ISO 22028-2 / ROMM RGB).
# Kept LOCAL — identical to test_color_oracle._M_PP_LIN_TO_XYZ_D50 — so the
# measurement-chain self-test below stands on a matrix NOT sourced from
# colour-science (the same path the pipeline + the rest of this file use).
_M_PP_LIN_TO_XYZ_D50 = np.array([
    [0.7976749, 0.1351917, 0.0313534],
    [0.2880402, 0.7118741, 0.0000857],
    [0.0000000, 0.0000000, 0.8252100],
])

_M_PP_TO_XYZ = colour.RGB_COLOURSPACES["ProPhoto RGB"].matrix_RGB_to_XYZ

_ILLUMINANTS = ("A", "D50", "D65")

_ADOBE_STANDARD_D5100 = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Adobe Standard/Nikon D5100 Adobe Standard.dcp"
)
_CAMERA_STANDARD_D5100 = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D5100/Nikon D5100 Camera Standard.dcp"
)


# ---------------------------------------------------------------------------
# Measurement helpers
# ---------------------------------------------------------------------------


def _tap_to_lab_d50(prophoto: np.ndarray) -> np.ndarray:
    """Stage-4 tap (linear ProPhoto-D50) → Lab(D50) — the measurement chain
    used by every end-to-end ΔE below. Validated non-circularly by the
    `test_measurement_chain_*` tests before any pipeline number is trusted."""
    xyz = prophoto.reshape(-1, 3) @ _M_PP_TO_XYZ.T
    return colour.XYZ_to_Lab(xyz, illuminant=sc.D50_XY)


def _pipeline_tap_lab(profile: DCPProfile, reflectances: np.ndarray, illuminant: str) -> np.ndarray:
    """Run the synthetic chart through stages 2–4 of the real pipeline and
    return per-patch Lab(D50) at the Stage-4 tap."""
    syn = sc.synthesize_camera_rgb(reflectances, illuminant)
    cam = syn["camera_rgb"][None]                       # (1, n, 3) flat patches
    tap = apply_adobe_pipeline(
        cam, profile, syn["as_shot_neutral"], sc.ILLUMINANT_CCT[illuminant],
        stop_after_stage=4,
    )
    return _tap_to_lab_d50(tap)


def _delta_e(lab_a: np.ndarray, lab_b: np.ndarray) -> np.ndarray:
    return np.asarray(colour.delta_E(lab_a, lab_b, method="CIE 2000"))


# ---------------------------------------------------------------------------
# (i) Measurement-chain self-test — must be NON-circular (advisor note).
# Injecting ProPhoto = M·XYZ and reading XYZ = M⁻¹·ProPhoto via the same
# colour-science call round-trips to ~0 regardless of whether M is right.
# So the chain is pinned two independent ways: analytic neutral L*, and a
# hardcoded ROMM matrix. Only then can a nonzero (ii) be trusted as profile fit.
# ---------------------------------------------------------------------------


def _lab_L_from_relative_Y(y: np.ndarray) -> np.ndarray:
    """Textbook CIE L* from Y/Yn (Yn=1), by hand — not via colour-science."""
    eps, kappa = 216.0 / 24389.0, 24389.0 / 27.0
    fy = np.where(y > eps, np.cbrt(y), (kappa * y + 16.0) / 116.0)
    return 116.0 * fy - 16.0


def test_measurement_chain_neutral_pins_analytic_Lstar():
    """Inject neutral ProPhoto [v,v,v]; the tap→Lab chain must return a neutral
    (a*≈b*≈0) with the analytic L* = 116·f(v)−16. Independent of the matrix's
    chromatic entries — pins the white point of the chain (a wrong output white
    would tilt the neutral axis off a*=b*=0)."""
    levels = np.array(sc.GREY_WEDGE_LEVELS)
    pp = np.repeat(levels[:, None], 3, axis=1).astype(np.float64)[None]  # (1,k,3)
    lab = _tap_to_lab_d50(pp)
    L_expected = _lab_L_from_relative_Y(levels)
    np.testing.assert_allclose(lab[:, 0], L_expected, atol=0.3)
    assert np.max(np.abs(lab[:, 1])) < 0.5, f"a* not neutral: {lab[:,1]}"
    assert np.max(np.abs(lab[:, 2])) < 0.5, f"b* not neutral: {lab[:,2]}"


def test_measurement_chain_matches_independent_romm_matrix():
    """Read injected chromatic ProPhoto (chart patches + pure primaries &
    secondaries) through BOTH colour-science's ProPhoto matrix and the hardcoded
    ROMM matrix. Agreement to a rounding floor proves the chain has no
    transpose / wrong-matrix bug; the two matrices are independent sources, so a
    real bug (which would corrupt both injection and read consistently under one
    matrix) shows up as divergence here."""
    _, refl = sc.chart_patches()
    gt = sc.ground_truth(refl, "D65")
    # Saturated stressors via direct injection (no spectral source needed):
    # ProPhoto primaries + secondaries, the most demanding case for a transpose.
    prims = np.array([
        [1, 0, 0], [0, 1, 0], [0, 0, 1], [0, 1, 1], [1, 0, 1], [1, 1, 0],
    ], dtype=np.float64) * 0.7
    pp = np.vstack([gt["XYZ_D50"] @ colour.RGB_COLOURSPACES["ProPhoto RGB"].matrix_XYZ_to_RGB.T,
                    prims])[None]

    lab_colour = _tap_to_lab_d50(pp)
    xyz_hard = pp.reshape(-1, 3) @ _M_PP_LIN_TO_XYZ_D50.T
    lab_hard = colour.XYZ_to_Lab(xyz_hard, illuminant=sc.D50_XY)
    de = _delta_e(lab_colour, lab_hard)
    assert de.max() < 0.1, (
        f"measurement chain diverges from the independent ROMM matrix by "
        f"max ΔE {de.max():.4f} — transpose / wrong-matrix bug in the chain, "
        f"not a data problem."
    )


# ---------------------------------------------------------------------------
# Independent floor — pure SSF physics, no pipeline involved.
# ---------------------------------------------------------------------------


def test_ssf_lstsq_floor_is_nonzero_and_bounded():
    """The Luther floor exists and is sane: a nonzero, sub-3 mean ΔE for the
    D5100 SSF on the ISO chart, at every illuminant. Documents the number every
    pipeline ΔE below is reported against, and guards the helper itself
    (a zero floor would mean the SSF satisfies Luther — it doesn't)."""
    _, refl = sc.chart_patches()
    for illum in _ILLUMINANTS:
        floor = sc.ssf_lstsq_floor(refl, illum)
        assert 0.3 < floor["mean"] < 3.0, f"{illum}: implausible floor {floor['mean']}"
        assert floor["max"] < 8.0, f"{illum}: implausible floor max {floor['max']}"


# ---------------------------------------------------------------------------
# (ii-a) Autonomous end-to-end: synthetic white-constrained FM → pipeline.
# Proves the pipeline (a) applies WB→FM→ProPhoto correctly [point-4 oracle, ~0]
# and (b) lands at the independent Luther floor [accuracy = profile fit].
# ---------------------------------------------------------------------------


def test_pipeline_camera_to_tap_matches_independent_oracle():
    """IMPLEMENTATION CORRECTNESS for the camera→tap path. The Stage-4 tap must
    equal an independent WB→FM→ProPhoto recompute to ~0. This is what lets the
    nonzero ΔE in the accuracy test be attributed to SSF physics, not code: the
    code is proven exact here, the residual is proven physics by the floor."""
    _, refl = sc.chart_patches()
    for illum in _ILLUMINANTS:
        fm = sc.white_constrained_forward_matrix(refl, illum)
        prof = DCPProfile(forward_matrix_1=fm)
        syn = sc.synthesize_camera_rgb(refl, illum)
        cam = syn["camera_rgb"][None].astype(np.float64)
        asn = syn["as_shot_neutral"].astype(np.float64)

        tap = apply_adobe_pipeline(
            cam.astype(np.float32), prof, asn.astype(np.float32),
            sc.ILLUMINANT_CCT[illum], stop_after_stage=4,
        )
        # Independent recompute: WB (G-normalised) → FM → ProPhoto.
        wb = (1.0 / asn) / ((1.0 / asn)[1])
        balanced = cam[0] * wb
        xyz = balanced @ fm.T
        prophoto = xyz @ colour.RGB_COLOURSPACES["ProPhoto RGB"].matrix_XYZ_to_RGB.T
        np.testing.assert_allclose(tap.reshape(-1, 3), prophoto, atol=2e-5)


def test_absolute_accuracy_autonomous_at_floor():
    """End-to-end absolute accuracy with a synthetic-FM profile (no Adobe
    install). Pipeline mean ΔE must sit at the independent SSF floor (it adds no
    error of its own), reported as accuracy WITH its named floor."""
    _, refl = sc.chart_patches()
    report = {}
    for illum in _ILLUMINANTS:
        floor = sc.ssf_lstsq_floor(refl, illum)
        fm = sc.white_constrained_forward_matrix(refl, illum)
        lab_tap = _pipeline_tap_lab(DCPProfile(forward_matrix_1=fm), refl, illum)
        de = _delta_e(lab_tap, sc.ground_truth(refl, illum)["Lab_D50"])
        report[illum] = (de.mean(), floor["mean"])
        # At or below the unconstrained XYZ-lstsq floor (a white-constrained fit
        # can edge below it in ΔE terms); never far above (bug guard).
        assert de.mean() < floor["mean"] + 0.5, (
            f"{illum}: accuracy {de.mean():.3f} far exceeds floor "
            f"{floor['mean']:.3f} — pipeline colour error, not profile fit."
        )
        assert de.mean() > 0.2, f"{illum}: ΔE {de.mean():.3f} implausibly low"
    print("autonomous accuracy (mean ΔE / floor):",
          {k: (round(v[0], 3), round(v[1], 3)) for k, v in report.items()})


# ---------------------------------------------------------------------------
# (ii-b) End-to-end through the SHIPPED Adobe DCP (the task's literal leg).
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _ADOBE_STANDARD_D5100.is_file(),
    reason="Nikon D5100 Adobe Standard.dcp not installed (system Adobe CameraRaw).",
)
def test_absolute_accuracy_adobe_standard_dcp_at_floor():
    """The shipped Nikon D5100 *Adobe Standard* profile, fed synthetic D5100-SSF
    camera RGB, must reproduce CIE truth at the tap to within the independent
    Luther floor. The Adobe ForwardMatrix is a real colorimetric fit, so it
    lands essentially ON the floor — confirming both the profile and our
    application of it are colorimetrically sound."""
    profile = parse_dcp(_ADOBE_STANDARD_D5100)
    _, refl = sc.chart_patches()
    report = {}
    for illum in _ILLUMINANTS:
        floor = sc.ssf_lstsq_floor(refl, illum)
        lab_tap = _pipeline_tap_lab(profile, refl, illum)
        de = _delta_e(lab_tap, sc.ground_truth(refl, illum)["Lab_D50"])
        report[illum] = (de.mean(), floor["mean"])
        assert de.mean() < floor["mean"] + 1.0, (
            f"{illum}: Adobe-Standard accuracy {de.mean():.3f} exceeds floor "
            f"{floor['mean']:.3f} + 1.0 — investigate (FM mis-applied, wrong "
            f"kelvin blend, or chart/illuminant mismatch)."
        )
    print("Adobe Standard accuracy (mean ΔE / floor):",
          {k: (round(v[0], 3), round(v[1], 3)) for k, v in report.items()})


@pytest.mark.skipif(
    not _CAMERA_STANDARD_D5100.is_file(),
    reason="Nikon D5100 Camera Standard.dcp not installed.",
)
def test_camera_standard_forward_matrix_is_not_colorimetric():
    """Documents a footgun, as a guard. Adobe *Camera Standard* profiles set
    ForwardMatrix to the ProPhoto→XYZ passthrough (the colour look lives in the
    LookTable/ToneCurve), so using one for absolute accuracy gives a ΔE FAR
    above the floor. If a future parser change made these look like real
    colorimetric matrices, this test would start failing — flagging that the
    accuracy harness must keep using Adobe Standard, not Camera Standard."""
    profile = parse_dcp(_CAMERA_STANDARD_D5100)
    _, refl = sc.chart_patches()
    floor = sc.ssf_lstsq_floor(refl, "D65")
    lab_tap = _pipeline_tap_lab(profile, refl, "D65")
    de = _delta_e(lab_tap, sc.ground_truth(refl, "D65")["Lab_D50"])
    assert de.mean() > 3.0 * floor["mean"], (
        f"Camera Standard ΔE {de.mean():.3f} unexpectedly near the floor "
        f"{floor['mean']:.3f} — its ForwardMatrix may no longer be the "
        f"ProPhoto passthrough; re-confirm which profile is colorimetric."
    )


# ---------------------------------------------------------------------------
# Sensitivity — the accuracy axis MUST catch a real colour-pipeline bug.
# ---------------------------------------------------------------------------


def test_absolute_accuracy_detects_transposed_forward_matrix():
    """A transposed ForwardMatrix (a classic real bug) must push accuracy far
    above the floor — proves the axis discriminates, isn't a rubber stamp."""
    _, refl = sc.chart_patches()
    fm = sc.white_constrained_forward_matrix(refl, "D65")
    good = _pipeline_tap_lab(DCPProfile(forward_matrix_1=fm), refl, "D65")
    bad = _pipeline_tap_lab(DCPProfile(forward_matrix_1=fm.T.copy()), refl, "D65")
    gt = sc.ground_truth(refl, "D65")["Lab_D50"]
    floor = sc.ssf_lstsq_floor(refl, "D65")["mean"]
    assert _delta_e(good, gt).mean() < floor + 0.5         # known-good at floor
    assert _delta_e(bad, gt).mean() > 5.0 * floor          # bug detected


# ---------------------------------------------------------------------------
# Extremes — overrange / near-black / clip come from INJECTION, not the chart
# (a spectral chart under a normal illuminant is always in-range). Mapped to
# the layer that delivers them: the tap is linear + unclamped, so it passes
# extremes through verbatim; clipping is a Stage-9 / output concern.
# ---------------------------------------------------------------------------


def test_tap_preserves_injected_extremes_unclamped():
    """Inject near-black, overrange and pure-channel camera RGB; the Stage-4 tap
    must carry them through linearly (no clamp). Absolute-accuracy ΔE is NOT
    computed for these — they have no in-gamut CIE truth; the point is that the
    tap is the right place to inject such extremes for downstream range tests."""
    fm = colour.RGB_COLOURSPACES["ProPhoto RGB"].matrix_RGB_to_XYZ  # ProPhoto→XYZ
    prof = DCPProfile(forward_matrix_1=fm)
    asn = np.array([1.0, 1.0, 1.0], dtype=np.float32)
    cam = np.array([[
        [1e-4, 1e-4, 1e-4],   # near-black
        [4.0, 4.0, 4.0],      # overrange (>1)
        [3.0, 0.0, 0.0],      # saturated overrange red
        [0.0, 0.0, 0.0],      # true black
    ]], dtype=np.float32)
    tap = apply_adobe_pipeline(cam, prof, asn, 6504.0, stop_after_stage=4)
    # With FM = ProPhoto→XYZ and WB identity, the tap returns the camera RGB
    # (a float32 XYZ round-trip, so ~1e-4 relative — the point is "no clamp").
    np.testing.assert_allclose(tap[0], cam[0], rtol=2e-3, atol=1e-3)
    assert tap.max() > 3.5, "overrange highlight was clamped at the tap"
    # No lower clamp either — a saturated channel round-trips slightly negative
    # (float noise) and the tap must NOT floor it to 0 (clipping is Stage-9 only).
    assert np.isfinite(tap).all()
    assert tap[0, 0, 0] > 1e-5, "near-black was floored to 0 at the tap"
