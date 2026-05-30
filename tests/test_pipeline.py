"""End-to-end ΔE2000 regression vs Adobe `dng_validate` ground truth.

This is the v0.6 ship gate. Both gym + rose MUST land mean ΔE < 1.0
against `dng_validate`-rendered TIFFs on their respective DNGs.

Per `docs/research/v06-architecture.md` §"Ship gate (v0.6)":
  gym ≤ 1.0 ΔE mean AND rose ≤ 1.0 ΔE mean vs dng_validate.

Fixtures live outside the repo (DNG + 145 MB ground-truth TIFF per scene
is too large to commit). The dev box exposes them via /tmp paths populated
by hand (see fixture paths below); CI skips the tests when the fixtures
are absent. The harness logic also runs as `.audit_tmp/diff_vs_dngvalidate.py`
on `research/python-pipeline-seed` if you need to reproduce by hand.

Adobe DCP catalog (per-camera) is read from the system Adobe install at
`/Library/Application Support/Adobe/CameraRaw/CameraProfiles/`. Same skip
rule applies.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

# Skip the entire module gracefully if optional render-time deps are absent
# (CI without rawpy / colour-science installed).
rawpy = pytest.importorskip("rawpy")
colour = pytest.importorskip("colour")
tifffile = pytest.importorskip("tifffile")

from lrt_cinema.dcp import parse_dcp  # noqa: E402
from lrt_cinema.pipeline import render_frame  # noqa: E402

# --- fixture paths ---------------------------------------------------------

_GYM_DNG = Path("/tmp/dng_out/DSC_4053.dng")
_GYM_GT_TIF = Path("/tmp/dng_out/DSC_4053_dngvalidate.tif")
_GYM_DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"
)

_ROSE_DNG = Path("/tmp/dng_out/rose.dng")
_ROSE_GT_TIF = Path("/tmp/dng_out/rose_dngval_Camera_Standard.tif")
_ROSE_DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Adobe Standard/Nikon D750 Adobe Standard.dcp"
)

_SHIP_GATE_DE_MEAN = 1.0


# --- helpers ---------------------------------------------------------------

_D65_xy = np.array([0.31270, 0.32900])


def _prophoto_to_srgb_8bit(prophoto: np.ndarray) -> np.ndarray:
    """Linear ProPhoto(D50) → sRGB gamma-encoded uint8. For ΔE comparison
    against `dng_validate`'s sRGB output ONLY — production renders write
    linear Rec.2020 via `output.py`."""
    m_prophoto_to_xyz_d50 = colour.RGB_COLOURSPACES["ProPhoto RGB"].matrix_RGB_to_XYZ
    m_xyz_d65_to_srgb = colour.RGB_COLOURSPACES["sRGB"].matrix_XYZ_to_RGB
    m_bradford = colour.adaptation.matrix_chromatic_adaptation_VonKries(
        np.array([0.96422, 1.0, 0.82521]),
        np.array([0.95047, 1.0, 1.08883]),
        transform="Bradford",
    )
    h, w, _ = prophoto.shape
    xyz_d50 = prophoto.reshape(-1, 3) @ m_prophoto_to_xyz_d50.T
    xyz_d65 = xyz_d50 @ m_bradford.T
    linear_srgb = xyz_d65 @ m_xyz_d65_to_srgb.T
    linear_srgb = np.clip(linear_srgb, 0.0, 1.0).reshape(h, w, 3)
    a = 0.055
    encoded = np.where(
        linear_srgb <= 0.0031308,
        linear_srgb * 12.92,
        (1 + a) * np.power(np.maximum(linear_srgb, 0), 1 / 2.4) - a,
    )
    return (encoded * 255).astype(np.uint8)


def _to_lab_d65(srgb_uint8: np.ndarray) -> np.ndarray:
    linear = colour.models.eotf_sRGB(srgb_uint8.astype(np.float64) / 255.0)
    xyz = colour.RGB_to_XYZ(linear, "sRGB", apply_cctf_decoding=False)
    return colour.XYZ_to_Lab(xyz, illuminant=_D65_xy)


def _measure_de_vs_ground_truth(
    dng_path: Path, gt_tif_path: Path, dcp_path: Path,
) -> dict:
    """Render via `pipeline.render_frame`, ΔE2000 vs the dng_validate TIFF.

    Returns {mean, P50, P95, max, pct_lt_1}."""
    profile = parse_dcp(dcp_path)
    result = render_frame(dng_path, profile, dcp_path=dcp_path)
    srgb = _prophoto_to_srgb_8bit(result.prophoto)

    gt_uint16 = tifffile.imread(str(gt_tif_path))
    gt_uint8 = (gt_uint16.astype(np.float32) / 65535.0 * 255).astype(np.uint8)

    # dng_validate crops 16 px / 8 px (sensor crop region); center-crop ours
    # to match before per-pixel ΔE.
    oh, ow, _ = srgb.shape
    th, tw, _ = gt_uint8.shape
    cy = (oh - th) // 2
    cx = (ow - tw) // 2
    ours_cropped = srgb[cy : cy + th, cx : cx + tw]

    de = colour.delta_E(
        _to_lab_d65(ours_cropped),
        _to_lab_d65(gt_uint8),
        method="CIE 2000",
    )
    return {
        "mean": float(de.mean()),
        "P50": float(np.percentile(de, 50)),
        "P95": float(np.percentile(de, 95)),
        "max": float(de.max()),
        "pct_lt_1": float((de < 1.0).mean() * 100),
    }


def _fixture_available(*paths: Path) -> bool:
    return all(p.exists() for p in paths)


# --- ship-gate tests -------------------------------------------------------


@pytest.mark.skipif(
    not _fixture_available(_GYM_DNG, _GYM_GT_TIF, _GYM_DCP),
    reason=(
        "Gym render fixture missing — needs DSC_4053.dng, dng_validate TIFF, "
        "and the system Adobe DCP. See test_pipeline.py header."
    ),
)
def test_ship_gate_gym_de_under_1():
    """Gym (DSC_4053, D750 Camera Standard) ≤ 1.0 mean ΔE2000 vs dng_validate."""
    m = _measure_de_vs_ground_truth(_GYM_DNG, _GYM_GT_TIF, _GYM_DCP)
    assert m["mean"] < _SHIP_GATE_DE_MEAN, (
        f"Gym mean ΔE {m['mean']:.3f} exceeds ship gate {_SHIP_GATE_DE_MEAN}. "
        f"Detail: P50={m['P50']:.3f} P95={m['P95']:.3f} max={m['max']:.3f} "
        f"<1ΔE pixels={m['pct_lt_1']:.1f}%. "
        "Baseline (research/python-pipeline-seed): 0.79 mean."
    )


@pytest.mark.skipif(
    not _fixture_available(_ROSE_DNG, _ROSE_GT_TIF, _ROSE_DCP),
    reason=(
        "Rose render fixture missing — needs rose.dng, dng_validate TIFF, "
        "and the system Adobe Standard DCP. See test_pipeline.py header."
    ),
)
def test_ship_gate_rose_de_under_1():
    """Rose (D750 Adobe Standard, no ProfileToneCurve → ACR3 fallback path)
    ≤ 1.0 mean ΔE2000 vs dng_validate."""
    m = _measure_de_vs_ground_truth(_ROSE_DNG, _ROSE_GT_TIF, _ROSE_DCP)
    assert m["mean"] < _SHIP_GATE_DE_MEAN, (
        f"Rose mean ΔE {m['mean']:.3f} exceeds ship gate {_SHIP_GATE_DE_MEAN}. "
        f"Detail: P50={m['P50']:.3f} P95={m['P95']:.3f} max={m['max']:.3f} "
        f"<1ΔE pixels={m['pct_lt_1']:.1f}%. "
        "Baseline (research/python-pipeline-seed): 0.84 mean."
    )


# --- Holy Grail kelvin override path ---------------------------------------


@pytest.mark.skipif(
    not _fixture_available(_GYM_DNG, _GYM_DCP),
    reason="Gym DNG + DCP required for Holy Grail kelvin override test.",
)
def test_holy_grail_kelvin_override_changes_render():
    """When DevelopOps.temperature_k is set, the AsShotNeutral is overridden
    via kelvin_to_neutral. Rendering at K=3500 (tungsten) vs K=8000 (shade)
    must produce materially different RGB output — confirming the override
    is wired into render_frame, not silently dropped."""
    from lrt_cinema.ir import DevelopOps

    profile = parse_dcp(_GYM_DCP)
    tungsten = render_frame(
        _GYM_DNG, profile, dcp_path=_GYM_DCP,
        develop_ops=DevelopOps(temperature_k=3500),
    )
    shade = render_frame(
        _GYM_DNG, profile, dcp_path=_GYM_DCP,
        develop_ops=DevelopOps(temperature_k=8000),
    )
    diff = float(np.abs(tungsten.prophoto - shade.prophoto).mean())
    assert diff > 0.01, (
        f"Kelvin override produced near-identical renders (mean abs diff "
        f"{diff:.4f}); override is not affecting the pipeline. "
        f"tungsten.scene_kelvin={tungsten.scene_kelvin} "
        f"shade.scene_kelvin={shade.scene_kelvin}"
    )
    assert tungsten.scene_kelvin == 3500.0
    assert shade.scene_kelvin == 8000.0


# --- Stage 7 early-exit (v0.7.1 cinema-linear-master) ----------------------


def test_stage_7_emission_rejects_other_stops():
    """`stop_after_stage` accepts only 3, 4, 7 or 9 — any other value is a
    bug in the caller and must fail fast at the validation check, before any
    matrix math runs."""
    from lrt_cinema.dcp import DCPProfile
    from lrt_cinema.pipeline import apply_adobe_pipeline

    camera_rgb = np.zeros((4, 4, 3), dtype=np.float32)
    asn = np.array([0.5, 1.0, 0.7], dtype=np.float32)
    with pytest.raises(ValueError, match="stop_after_stage"):
        apply_adobe_pipeline(
            camera_rgb, DCPProfile(), asn, 5500.0, stop_after_stage=11,
        )
    # 5 lands mid-HueSatMap — also invalid, must fail at the same guard.
    with pytest.raises(ValueError, match="stop_after_stage"):
        apply_adobe_pipeline(
            camera_rgb, DCPProfile(), asn, 5500.0, stop_after_stage=5,
        )


# --- Colorimetric tap: Stage 3 (XYZ-D50) / Stage 4 (linear ProPhoto-D50) ---
# Fixture-free unit tests for the v0.8 absolute-accuracy / preview tap. They
# prove the tap sits exactly post-ForwardMatrix and BEFORE Stage-5 HueSatMap
# (the first shaping op). The real-DCP proof that the tap precedes the
# LookTable too lives in tests/test_colorimetric.py (the D5100/D750 profiles
# carry a LookTable but no HueSatMap, so a constructed HSM is needed here).


def _identity_fm_profile():
    """Profile whose ForwardMatrix is ProPhoto(D50)→XYZ(D50). Then the Stage-4
    tap (XYZ→ProPhoto) returns the WB-balanced camera RGB unchanged, so the
    tap value is analytically predictable."""
    from lrt_cinema.dcp import DCPProfile

    prof = DCPProfile()
    prof.forward_matrix_1 = colour.RGB_COLOURSPACES["ProPhoto RGB"].matrix_RGB_to_XYZ
    return prof


def test_colorimetric_tap_stage4_is_post_forwardmatrix_pre_hsm():
    """stop_after_stage=4 returns linear ProPhoto(D50) taken post-ForwardMatrix
    and BEFORE Stage-5 HueSatMap.

    Proof: give the profile a deliberately non-identity HueSatMap (+60° hue,
    1.5× sat everywhere). The Stage-4 tap must still equal the pure
    WB→FM→ProPhoto recompute (no hue shift); if the early-return were
    mis-placed after Stage 5, the saturated test pixel would return
    hue-shifted and this assertion would fail."""
    from lrt_cinema.dcp import HsvCube
    from lrt_cinema.pipeline import apply_adobe_pipeline

    prof = _identity_fm_profile()
    cube = np.zeros((1, 6, 2, 3), dtype=np.float32)
    cube[..., 0] = 60.0   # hueShift degrees
    cube[..., 1] = 1.5    # satScale
    cube[..., 2] = 1.0    # valScale
    prof.hue_sat_map = HsvCube(
        hue_divisions=6, sat_divisions=2, val_divisions=1,
        srgb_gamma=False, data_1=cube,
    )

    cam = np.array([[[0.6, 0.3, 0.2]]], dtype=np.float32)  # chromatic
    asn = np.array([1.0, 1.0, 1.0], dtype=np.float32)      # WB identity

    tap4 = apply_adobe_pipeline(cam, prof, asn, 5500.0, stop_after_stage=4)

    m_pp_to_xyz = colour.RGB_COLOURSPACES["ProPhoto RGB"].matrix_RGB_to_XYZ
    m_xyz_to_pp = colour.RGB_COLOURSPACES["ProPhoto RGB"].matrix_XYZ_to_RGB
    xyz_expect = cam.reshape(-1, 3) @ m_pp_to_xyz.T          # WB identity
    pp_expect = (xyz_expect @ m_xyz_to_pp.T).reshape(1, 1, 3)
    np.testing.assert_allclose(tap4, pp_expect, atol=1e-5)

    # The pixel is genuinely chromatic, so a correctly-placed HSM (+ramp+curve)
    # WOULD change it: the full render must differ from the tap.
    full = apply_adobe_pipeline(cam, prof, asn, 5500.0, stop_after_stage=9)
    assert np.abs(full - tap4).max() > 1e-3


def test_colorimetric_tap_stage3_xyz_is_one_matrix_from_stage4():
    """Stage-3 (XYZ-D50) and Stage-4 (linear ProPhoto-D50) taps are one fixed
    matrix apart — confirms Stage 4 is exactly the XYZ→ProPhoto step on the
    Stage-3 output and nothing more (e.g. no WB applied twice)."""
    from lrt_cinema.pipeline import apply_adobe_pipeline

    prof = _identity_fm_profile()
    cam = np.array([[[0.6, 0.3, 0.2], [0.1, 0.2, 0.4]]], dtype=np.float32)
    asn = np.array([0.8, 1.0, 0.7], dtype=np.float32)

    xyz = apply_adobe_pipeline(cam, prof, asn, 5500.0, stop_after_stage=3)
    pp = apply_adobe_pipeline(cam, prof, asn, 5500.0, stop_after_stage=4)
    m_xyz_to_pp = colour.RGB_COLOURSPACES["ProPhoto RGB"].matrix_XYZ_to_RGB
    np.testing.assert_allclose(
        pp.reshape(-1, 3), xyz.reshape(-1, 3) @ m_xyz_to_pp.T, atol=1e-5,
    )


@pytest.mark.skipif(
    not _fixture_available(_GYM_DNG, _GYM_DCP),
    reason="Gym DNG + DCP required for Stage 7 emission test.",
)
def test_stage_7_emission_preserves_more_overrange_than_stage_9():
    """Stage 7 (cinema-linear-master) emits scene-referred data with the
    ExposureRamp's overrange (>1.0) preserved, so half-float EXR carries
    recoverable highlight headroom. Stage 9 (cinema-linear-finished) feeds
    a ProfileToneCurve that clamps to [0, 1], discarding that headroom.
    This is the load-bearing recovery claim of cinema-linear-master — and
    the one the shipped v0.7.1 quietly failed (the ExposureRamp clamped at
    1.0 before the Stage-7 emission point). See pipeline.py support_overrange
    and tools/verify_emission_format.py check C3."""
    profile = parse_dcp(_GYM_DCP)
    stage9 = render_frame(_GYM_DNG, profile, dcp_path=_GYM_DCP)
    stage7 = render_frame(
        _GYM_DNG, profile, dcp_path=_GYM_DCP, stop_after_stage=7,
    )
    # Stage 9 clamps inside the tone-curve solver — no overrange survives.
    assert stage9.prophoto.max() <= 1.0 + 1e-3
    assert float((stage9.prophoto > 1.0).mean()) < 1e-4
    # Stage 7 MUST preserve real overrange highlights (gym frame: ~1 stop,
    # max ≈ 2.0). A regression to support_overrange=False shows up here as
    # max == 1.0 and 0% overrange — the exact v0.7.1 defect.
    assert stage7.prophoto.max() > 1.05, (
        f"Stage 7 lost its overrange highlights (max {stage7.prophoto.max():.3f}); "
        f"the ExposureRamp support_overrange flag is off for the stage-7 path "
        f"— recovery is defeated."
    )
    assert float((stage7.prophoto > 1.0).mean()) > float((stage9.prophoto > 1.0).mean())
    # And Stage 7 must still be materially different from Stage 9 in-gamut
    # (it skips DCP LookTable + ProfileToneCurve).
    diff = float(np.abs(np.clip(stage7.prophoto, 0, 1) - stage9.prophoto).mean())
    assert diff > 0.005, (
        f"Stage 7 and Stage 9 outputs are nearly identical in-gamut (mean abs "
        f"diff {diff:.5f}); the tone curve / LookTable did not change the render."
    )
