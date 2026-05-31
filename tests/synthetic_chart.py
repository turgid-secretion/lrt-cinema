"""Deterministic synthetic spectral chart — the Axis-2 (absolute-accuracy) source.

NOT a test module (no `test_` prefix → pytest does not collect it). Shared by
`tests/test_colorimetric.py` (Axis 2, end-to-end) and the synthetic-DNG check.

Everything here is derived from `colour-science`'s checked-in spectral data, so
the chart is byte-reproducible with no network, no physical chart, and no RAW
fixture:

  * reflectances  → `colour.SDS_COLOURCHECKERS['ISO 17321-1']` (24 patches) +
    a flat-spectrum grey step-wedge (augmentation);
  * illuminants   → `colour.SDS_ILLUMINANTS` (D50 / D55 / D65 / A);
  * CMFs (truth)  → `colour.MSDS_CMFS['CIE 1931 2 Degree Standard Observer']`;
  * camera RGB    → `colour.MSDS_CAMERA_SENSITIVITIES['Nikon 5100 (NPL)']`
    (the D750's SSF is unpublished — D5100 stands in, with its Adobe DCP).

Two products per (patch, illuminant):
  1. **Ground truth** — CIE XYZ from reflectance × illuminant × CMF, Bradford-
     adapted to the D50 profile-connection space, as Lab(D50). This is "CIE
     truth" for the absolute-accuracy axis.
  2. **Synthetic camera RGB** — reflectance × illuminant × SSF, scaled so a
     perfect white reflector maps to the camera's AsShotNeutral (G=1). Fed to
     the pipeline's WB→ForwardMatrix→ProPhoto path; compared at the
     colorimetric tap against (1).

The gap between (2)-through-the-pipeline and (1) has an **irreducible nonzero
floor**: the SSF violates the Luther condition, so no 3×3 reproduces CIE XYZ
exactly. `ssf_lstsq_floor` computes that floor *independently of the pipeline*
(the XYZ-optimal least-squares 3×3 for this SSF) so a measured ΔE can be
reported as "accuracy (floor = profile fit)", never as bug magnitude.

A spectral chart under a normal illuminant only ever yields in-gamut, in-range
values; near-black / overrange / clip / pure-primary extremes are reached by
direct value injection in the tests, not from here.
"""

from __future__ import annotations

import colour
import numpy as np

# One spectral grid for reflectance, illuminant, CMF and SSF. Aligning every
# distribution to this BEFORE integrating is mandatory — mismatched shapes
# integrate silently wrong (the integrals would sample different wavelengths).
SPECTRAL_SHAPE = colour.SpectralShape(380, 780, 5)

# CIE 1931 2° (x, y) white points (match src/lrt_cinema/dcp.py + output.py).
D50_XY = (0.34570, 0.35850)
D65_XY = (0.31270, 0.32900)
D50_XYZ = colour.xy_to_XYZ(D50_XY)

# Nominal CCTs of the calibration illuminants (EXIF light-source convention,
# matching dcp._ILLUMINANT_TO_KELVIN). Passed to the pipeline as `scene_kelvin`
# so the ForwardMatrix1/2 mired blend matches the synthesis illuminant — using
# the 5500 K default instead would pick the wrong matrix for A / D50 patches.
ILLUMINANT_CCT = {"A": 2856.0, "D50": 5003.0, "D55": 5503.0, "D65": 6504.0}

DEFAULT_SSF = "Nikon 5100 (NPL)"

# Flat-spectrum grey step-wedge reflectances (augmentation). Neutral by
# construction (flat ρ → scales the illuminant white), so they exercise the
# tone axis without leaving the in-gamut region. Levels approximate the
# ColorChecker grey ramp plus a darker step.
GREY_WEDGE_LEVELS = (0.90, 0.59, 0.36, 0.19, 0.09, 0.031)


def _aligned(sd: colour.SpectralDistribution) -> colour.SpectralDistribution:
    return sd.copy().align(SPECTRAL_SHAPE)


def iso17321_patches() -> tuple[list[str], np.ndarray]:
    """The 24 ISO 17321-1 ColorChecker reflectances, aligned to SPECTRAL_SHAPE.

    Returns (names, reflectances) with reflectances shape (24, n_wl)."""
    cc = colour.SDS_COLOURCHECKERS["ISO 17321-1"]
    names = list(cc.keys())
    refl = np.array([_aligned(cc[name]).values for name in names])
    return names, refl


def grey_wedge_patches(levels: tuple[float, ...] = GREY_WEDGE_LEVELS) -> tuple[list[str], np.ndarray]:
    """Flat-spectrum grey step-wedge reflectances, shape (len(levels), n_wl)."""
    n_wl = len(SPECTRAL_SHAPE.wavelengths)
    names = [f"grey {lv:.3f}" for lv in levels]
    refl = np.array([np.full(n_wl, lv, dtype=np.float64) for lv in levels])
    return names, refl


def chart_patches(include_grey_wedge: bool = True) -> tuple[list[str], np.ndarray]:
    """Full synthetic chart: 24 ISO patches (+ grey step-wedge augmentation)."""
    names, refl = iso17321_patches()
    if include_grey_wedge:
        g_names, g_refl = grey_wedge_patches()
        names = names + g_names
        refl = np.vstack([refl, g_refl])
    return names, refl


def _illuminant_sd(name: str) -> np.ndarray:
    return _aligned(colour.SDS_ILLUMINANTS[name]).values


def _cmfs() -> np.ndarray:
    return _aligned(colour.MSDS_CMFS["CIE 1931 2 Degree Standard Observer"]).values


def _ssf(name: str = DEFAULT_SSF) -> np.ndarray:
    return _aligned(colour.MSDS_CAMERA_SENSITIVITIES[name]).values


def ground_truth(reflectances: np.ndarray, illuminant: str) -> dict:
    """CIE ground truth for `reflectances` under `illuminant`.

    XYZ is normalised so a perfect white reflector (ρ=1) under the illuminant
    has Y=1 (the dλ factor cancels in the k = 1/∫S·ȳ normalisation, so the
    rectangle sum below is exact). The scene XYZ is then Bradford-adapted to
    the D50 profile-connection space — the same CAT the pipeline's output path
    and ICC PCS use — so it is directly comparable to the Stage-3/4 tap.

    Returns {XYZ_scene, XYZ_D50, Lab_D50, white_xyz_scene}.
    """
    S = _illuminant_sd(illuminant)          # (n_wl,)
    xbar = _cmfs()                          # (n_wl, 3)
    k = 1.0 / np.sum(S * xbar[:, 1])        # perfect reflector → Y = 1
    xyz_scene = k * (reflectances * S) @ xbar           # (n, 3)
    white_xyz_scene = k * (S @ xbar)                    # illuminant white, Y=1

    m_cat = colour.adaptation.matrix_chromatic_adaptation_VonKries(
        white_xyz_scene, D50_XYZ, transform="Bradford",
    )
    xyz_d50 = xyz_scene @ m_cat.T
    lab_d50 = colour.XYZ_to_Lab(xyz_d50, illuminant=D50_XY)
    return {
        "XYZ_scene": xyz_scene,
        "XYZ_D50": xyz_d50,
        "Lab_D50": lab_d50,
        "white_xyz_scene": white_xyz_scene,
    }


def synthesize_camera_rgb(
    reflectances: np.ndarray, illuminant: str, ssf: str = DEFAULT_SSF,
) -> dict:
    """Synthetic linear camera RGB = ∫ ρ(λ)·S(λ)·SSF(λ) dλ, plus AsShotNeutral.

    Scaled so the camera's green response to a perfect white reflector is 1;
    then a perfect reflector maps to AsShotNeutral (G=1) exactly, and the
    pipeline's WB step takes it to balanced [1,1,1] → FM → D50 white. A patch
    of flat reflectance v lands at balanced [v,v,v]. This makes the synthetic
    camera RGB physically consistent with the ground-truth Y scale.

    Returns {camera_rgb (n,3), as_shot_neutral (3,)}.
    """
    S = _illuminant_sd(illuminant)
    cam = _ssf(ssf)                                     # (n_wl, 3)
    k_cam = 1.0 / np.sum(S * cam[:, 1])                 # white green → 1
    camera_rgb = k_cam * (reflectances * S) @ cam       # (n, 3)
    as_shot_neutral = k_cam * (S @ cam)                 # white response, G=1
    return {
        "camera_rgb": camera_rgb.astype(np.float32),
        "as_shot_neutral": as_shot_neutral.astype(np.float32),
    }


def white_constrained_forward_matrix(
    reflectances: np.ndarray, illuminant: str, ssf: str = DEFAULT_SSF,
) -> np.ndarray:
    """A *proper* synthetic ForwardMatrix fit to the SSF, for the autonomous leg.

    Least-squares maps balanced camera RGB (camera_rgb / AsShotNeutral) →
    XYZ(D50) over the patch set, CONSTRAINED so balanced white [1,1,1] → D50
    white — the DNG ForwardMatrix invariant the pipeline relies on. Injected as
    a synthetic profile's `forward_matrix_1`, it drives the pipeline end-to-end
    with NO Adobe install; the resulting tap ΔE vs CIE truth then equals the SSF
    Luther floor (the pipeline contributes no error of its own).

    Returned so the pipeline's `xyz = balanced @ fm.T` reproduces the fit.
    """
    syn = synthesize_camera_rgb(reflectances, illuminant, ssf)
    bal = syn["camera_rgb"].astype(np.float64) / syn["as_shot_neutral"].astype(np.float64)
    xyz = ground_truth(reflectances, illuminant)["XYZ_D50"]
    ones = np.ones((1, 3))
    m = np.zeros((3, 3))
    for j in range(3):  # per XYZ output column: KKT solve with white constraint
        a = bal.T @ bal
        b = bal.T @ xyz[:, j]
        kkt = np.block([[a, ones.T], [ones, np.zeros((1, 1))]])
        sol = np.linalg.solve(kkt, np.concatenate([b, [D50_XYZ[j]]]))
        m[:, j] = sol[:3]
    return m.T


def ssf_lstsq_floor(
    reflectances: np.ndarray, illuminant: str, ssf: str = DEFAULT_SSF,
) -> dict:
    """The Luther/SSF floor — computed WITHOUT the pipeline.

    Fits the XYZ-MSE-optimal 3×3 camera-RGB→XYZ(D50) matrix for this SSF on
    this patch set (one `lstsq`), then reports the residual ΔE2000 vs the CIE
    ground truth. Because the SSF violates the Luther condition, even this
    optimal 3×3 leaves a nonzero residual — that residual is the best any 3×3
    (Adobe's ForwardMatrix included) can do, i.e. the absolute-accuracy floor.

    The Adobe FM is a *constrained* fit (white-preserving, tuned across two
    illuminants), so the pipeline ΔE sits a little ABOVE this unconstrained
    XYZ-optimal floor — never far below it, and never near zero. A pipeline ΔE
    far above it signals a bug, not physics.

    Returns {delta_e (n,), mean, max, matrix (3,3)}.
    """
    cam = synthesize_camera_rgb(reflectances, illuminant, ssf)["camera_rgb"].astype(np.float64)
    gt = ground_truth(reflectances, illuminant)
    m_fit, *_ = np.linalg.lstsq(cam, gt["XYZ_D50"], rcond=None)   # cam @ m_fit ≈ XYZ
    xyz_pred = cam @ m_fit
    de = colour.delta_E(
        colour.XYZ_to_Lab(xyz_pred, illuminant=D50_XY),
        gt["Lab_D50"],
        method="CIE 2000",
    )
    return {
        "delta_e": np.asarray(de),
        "mean": float(np.mean(de)),
        "max": float(np.max(de)),
        "matrix": m_fit,
    }
