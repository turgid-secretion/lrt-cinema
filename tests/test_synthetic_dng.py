"""Axis 3 — implementation check vs Adobe `dng_validate` on FLAT patches (D750).

Complements the real-scene ship gate (test_pipeline.py, gym 0.789 mean ΔE vs
dng_validate). That mean is dragged by demosaic-edge differences (libraw LINEAR
vs Adobe) — its flat-pixel median is already 0.000. This test isolates the pure
colour-math agreement: a synthetic DNG of flat known-value patches (no demosaic
edges), rendered by BOTH our pipeline and dng_validate with the SAME DCP (so the
Luther floor cancels), compared on patch interiors. Expected: ~0.

Same camera (Nikon D750) + same Adobe DCP both sides, so this is a true
implementation-correctness check (Axis 1/3), NOT absolute accuracy (Axis 2,
test_colorimetric.py). The synthetic chart, byte-patch writer and patch sampler
live in tests/synthetic_dng.py.

Skip-gated: needs the D750 DNG fixture, the `dng_validate` binary, `dnglab` (to
make the uncompressed clone), and the system Adobe D750 Camera Standard DCP.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

rawpy = pytest.importorskip("rawpy")  # noqa: F841
colour = pytest.importorskip("colour")
tifffile = pytest.importorskip("tifffile")

from lrt_cinema.dcp import parse_dcp  # noqa: E402
from lrt_cinema.pipeline import read_as_shot_neutral, render_frame  # noqa: E402
from tests import synthetic_dng as sd  # noqa: E402
from tests.test_pipeline import _prophoto_to_srgb_8bit, _to_lab_d65  # noqa: E402

_DNG_VALIDATE = Path("/private/tmp/dng_sdk/_build/dng_sdk/source/dng_validate")
_SRC_DNG = Path("/tmp/dng_out/DSC_4053.dng")
_D750_CAMSTD = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"
)
_WORK = Path("/tmp/dng_out")


def _fixtures_present() -> bool:
    return (
        _SRC_DNG.is_file()
        and _DNG_VALIDATE.is_file()
        and shutil.which("dnglab") is not None
        and _D750_CAMSTD.is_file()
    )


@pytest.mark.skipif(
    not _fixtures_present(),
    reason="needs DSC_4053.dng + dng_validate binary + dnglab + system D750 Camera Standard DCP.",
)
def test_flat_patches_match_dng_validate():
    """Render a flat-patch synthetic D750 DNG through our pipeline and through
    dng_validate (Camera Standard, both sides), compare patch interiors in
    Lab(D65)/ΔE2000. On flat patches the colour maths bit-match the open spec,
    so the median ΔE is ~0 — the demosaic-edge tail that lifts the real-scene
    mean to 0.789 is absent here."""
    uncomp = _WORK / "DSC_4053_uncomp.dng"
    assert sd.ensure_uncompressed_clone(_SRC_DNG, uncomp), "dnglab uncompressed clone failed"

    layout = sd.read_raw_layout(uncomp)
    assert layout.black == 600.0 and layout.white == 15520.0  # guard the linearization anchors
    asn = read_as_shot_neutral(_SRC_DNG)
    chart = sd.default_chart(asn)
    cfa = sd.build_cfa(layout, chart.patches)
    synth = _WORK / "DSC_4053_synth.dng"
    sd.write_synthetic_dng(uncomp, synth, cfa, layout)

    # --- Adobe dng_validate render (16-bit sRGB, Camera Standard profile) -----
    stem = _WORK / "synth_dngval"
    subprocess.run(
        [str(_DNG_VALIDATE), "-profile", "Camera Standard", "-16", "-tif", str(stem), str(synth)],
        check=True, capture_output=True, timeout=120,
    )
    gt16 = tifffile.imread(str(stem) + ".tif")
    gt8 = (gt16.astype(np.float32) / 65535.0 * 255.0).astype(np.uint8)

    # --- our pipeline render (full DCP shaping) -------------------------------
    profile = parse_dcp(_D750_CAMSTD)
    result = render_frame(synth, profile, dcp_path=_D750_CAMSTD)
    ours8 = _prophoto_to_srgb_8bit(result.prophoto)

    # dng_validate crops to DefaultCrop (origin 8,8); center-crop ours to match.
    oh, ow, _ = ours8.shape
    th, tw, _ = gt8.shape
    cy, cx = (oh - th) // 2, (ow - tw) // 2
    ours8 = ours8[cy: cy + th, cx: cx + tw]

    # --- per-patch ΔE2000 over eroded interiors -------------------------------
    mean_ours = sd.sample_patch_means(ours8, chart.patches)
    mean_gt = sd.sample_patch_means(gt8, chart.patches)
    lab_ours = _to_lab_d65(mean_ours)
    lab_gt = _to_lab_d65(mean_gt)
    de = np.asarray(colour.delta_E(lab_ours, lab_gt, method="CIE 2000"))

    report = {p.name: round(float(d), 3) for p, d in zip(chart.patches, de, strict=True)}
    is_neutral = np.array([p.is_neutral for p in chart.patches])
    de_neutral, de_colour = de[is_neutral], de[~is_neutral]
    print(
        f"\nflat-patch ΔE vs dng_validate: neutral median {np.median(de_neutral):.3f} "
        f"max {de_neutral.max():.3f} | chromatic mean {de_colour.mean():.3f} "
        f"max {de_colour.max():.3f}\n{report}"
    )

    # NEUTRAL wedge — the load-bearing claim. No demosaic edges, in-gamut at
    # every level, same DCP both sides (Luther floor cancels) → the colour maths
    # bit-match the open spec at ΔE 0.000. This is exactly docs/VALIDATION.md's
    # "flat-pixel median 0.000", isolated from the demosaic-edge tail that lifts
    # the real-scene mean to 0.789. Strict gate.
    assert np.median(de_neutral) < 0.05, (
        f"neutral flat-patch median ΔE {np.median(de_neutral):.3f} — the colour "
        f"maths now diverge from dng_validate on edge-free neutral patches "
        f"(a real regression in matrix/WB/tone-curve)."
    )
    assert de_neutral.max() < 0.5, f"neutral flat-patch max ΔE {de_neutral.max():.3f}"

    # CHROMATIC patches isolate a real, known divergence: ~4-8 ΔE, confirmed in
    # wide-gamut ProPhoto too (NOT sRGB-gamut clipping), on the only stage that
    # treats chromatic ≠ neutral — the LookTable (HSV cube). This is the source
    # of the gym's documented "~5% flat-region colour tail" (CLAUDE.md / docs/
    # VALIDATION.md). The synthetic chart surfaces it cleanly because the gym's
    # real-scene colours are too desaturated to probe these LookTable cells.
    # Upper-bound only (a future LookTable fix that drives this toward 0 must NOT
    # break the test); the headline neutral 0.000 stands regardless.
    assert de_colour.max() < 12.0, (
        f"chromatic flat-patch max ΔE {de_colour.max():.3f} — the LookTable "
        f"divergence WORSENED beyond the known ~8 ΔE tail; investigate _apply_hsv_cube."
    )


@pytest.mark.skipif(
    not _fixtures_present(),
    reason="needs DSC_4053.dng + dnglab + tifffile.",
)
def test_synthetic_dng_linearizes_to_intended_values():
    """Sanity on the writer itself: a neutral patch built proportional to
    AsShotNeutral must read back through libraw at the intended balanced level
    (honouring BlackLevel/WhiteLevel), i.e. WB → ~[L,L,L]. Guards against a
    black/white or byte-order slip that would make every downstream ΔE bogus."""
    uncomp = _WORK / "DSC_4053_uncomp.dng"
    assert sd.ensure_uncompressed_clone(_SRC_DNG, uncomp)
    layout = sd.read_raw_layout(uncomp)
    asn = read_as_shot_neutral(_SRC_DNG)

    level = 0.25
    patch = sd.Patch("n", 0.0, 1.0, 0.0, 1.0, tuple(level * asn.astype(np.float64)), True)
    cfa = sd.build_cfa(layout, [patch])
    synth = _WORK / "DSC_4053_synth_uniform.dng"
    sd.write_synthetic_dng(uncomp, synth, cfa, layout)

    with rawpy.imread(str(synth)) as raw:
        rv = raw.raw_image_visible.astype(np.float64)
    cam_norm = (rv - layout.black) / (layout.white - layout.black)
    # The three CFA channels carry L·ASN_c; after dividing the green sites we get
    # the balanced neutral level back. Green sites: expect ≈ level.
    g_sites = cam_norm[0::2, 1::2]  # one of the two green positions in RGGB
    assert abs(g_sites.mean() - level) < 1e-2, f"green linearized to {g_sites.mean():.4f}, want {level}"
