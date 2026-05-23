"""ColorChecker ΔE2000 test harness.

Implements the methodology in `docs/VALIDATION.md` (option b — the
bulletproof automated test). Two test legs:

1. **Self-test** (always runs, no darktable / no chart shot required).
   Synthesizes a "perfect" 16-bit linear Rec.2020 image of the 24-patch
   ColorChecker and runs it through the same Lab(D55) ↔ XYZ ↔ Rec.2020
   conversion + ΔE2000 path the real test uses, asserting the harness
   machinery is functional. Includes a separate per-patch comparison
   against a hand-rolled BT.2020 →  XYZ matrix (taken verbatim from
   ITU-R BT.2020-2 §3.3) so a transposed or wrong matrix in our
   harness code does not silently round-trip through colour-science.

2. **Real-chart test** (skipped unless a chart RAW + identity XMP are
   dropped into `tests/fixtures/colorchecker/` — see that directory's
   README). Renders the chart through `lrt-cinema render --preset
   cinema-linear`, auto-detects the patches via the optional
   `colour-checker-detection` dependency, computes ΔE2000 per patch
   against the published D55-adapted reference, and asserts the
   broadcast-cinema thresholds (mean < 2.0, max < 4.0).

The real-chart leg is expected to FAIL on today's pipeline (the
emitter drops 9 of 12 develop ops; WB multipliers are neutral). That
failure is by design — see `docs/V03_PLAN.md` Track A. The harness's
job is to quantify the gap, not to hide it.

References
----------
- `docs/VALIDATION.md` for the full methodology and the source list.
- ITU-R BT.2020-2 §3.3 for the Rec.2020 → XYZ matrix used in the
  cross-check.
- Sharma, Wu, Dalal (2005) for ΔE2000 (implemented by
  `colour.delta_E(..., method='CIE 2000')`).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

# colour-science is a hard dev-dep; importorskip keeps the file safe
# to import in a fresh tree that hasn't run `pip install -e .[dev]` yet
# (e.g. someone running just `pytest tests/test_cli.py` for a focused
# iteration).
colour = pytest.importorskip("colour")  # type: ignore[assignment]
from colour.adaptation import chromatic_adaptation_VonKries  # noqa: E402

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "colorchecker"
REFERENCE_JSON = FIXTURE_DIR / "chart_reference.json"

# Reference illuminants (CIE 1931 2-degree observer).
D50_XY = np.array([0.3457, 0.3585])
D55_XY = np.array([0.33243, 0.34744])
D65_XY = np.array([0.3127, 0.3290])

# ITU-R BT.2020-2 §3.3 — linear Rec.2020 → CIE XYZ (D65) matrix.
# Hard-coded so a transposed / wrong matrix anywhere in our
# colour-science wiring is detected by the self-test cross-check.
BT2020_TO_XYZ_D65 = np.array([
    [0.6369580, 0.1446169, 0.1688810],
    [0.2627002, 0.6779981, 0.0593017],
    [0.0000000, 0.0280727, 1.0609851],
])

# Broadcast-cinema thresholds (see docs/VALIDATION.md table).
REAL_CHART_MEAN_DE_THRESHOLD = 2.0
REAL_CHART_MAX_DE_THRESHOLD = 4.0

# Self-test tolerance: round-trip through u16 Rec.2020 then back to
# Lab(D55) should be well under 1 ΔE2000 — measured empirically at
# max ≈ 0.008 on the darkest grey patch. Generous headroom for
# floating-point implementation drift across colour-science versions.
SELF_TEST_MEAN_DE_TOLERANCE = 0.05
SELF_TEST_MAX_DE_TOLERANCE = 0.20


# ---------------------------------------------------------------------------
# Reference loading
# ---------------------------------------------------------------------------


def _load_reference():
    """Load the 24-patch reference. Returns (names, Lab_D55 array of shape (24, 3))."""
    with open(REFERENCE_JSON, encoding="utf-8") as f:
        data = json.load(f)
    patches = data["patches"]
    assert len(patches) == 24, f"reference must have 24 patches, got {len(patches)}"
    names = [p["name"] for p in patches]
    lab_d55 = np.array([p["Lab_D55"] for p in patches], dtype=np.float64)
    return names, lab_d55


# ---------------------------------------------------------------------------
# Colorimetric conversions — single canonical path
# ---------------------------------------------------------------------------


def _linear_rec2020_to_lab_d55(rgb: np.ndarray) -> np.ndarray:
    """linear Rec.2020 RGB → Lab(D55), via BT.2020 native D65 and CAT02 to D55.

    Input: rgb shape (..., 3) in [0, 1] (linear, not gamma-encoded).
    Output: Lab shape (..., 3) under D55, 2° observer.
    """
    xyz_d65 = colour.RGB_to_XYZ(
        rgb,
        colourspace="ITU-R BT.2020",
        illuminant=D65_XY,
        chromatic_adaptation_transform=None,
        apply_cctf_decoding=False,
    )
    xyz_d55 = chromatic_adaptation_VonKries(
        xyz_d65, colour.xy_to_XYZ(D65_XY), colour.xy_to_XYZ(D55_XY), transform="CAT02"
    )
    return colour.XYZ_to_Lab(xyz_d55, illuminant=D55_XY)


def _lab_d55_to_linear_rec2020(lab: np.ndarray) -> np.ndarray:
    """Inverse of `_linear_rec2020_to_lab_d55`. For synthesizing the self-test chart."""
    xyz_d55 = colour.Lab_to_XYZ(lab, illuminant=D55_XY)
    xyz_d65 = chromatic_adaptation_VonKries(
        xyz_d55, colour.xy_to_XYZ(D55_XY), colour.xy_to_XYZ(D65_XY), transform="CAT02"
    )
    return colour.XYZ_to_RGB(
        xyz_d65,
        colourspace="ITU-R BT.2020",
        illuminant=D65_XY,
        chromatic_adaptation_transform=None,
        apply_cctf_encoding=False,
    )


def _linear_rec2020_to_lab_d55_hardcoded_matrix(rgb: np.ndarray) -> np.ndarray:
    """Cross-check path: BT.2020 matrix from ITU-R BT.2020-2 §3.3 by hand.

    Catches a transposed / wrong matrix in the colour-science path. The
    two paths must agree to within floating-point noise for the same
    input — any disagreement is a bug in the harness, not in the data.
    """
    rgb = np.asarray(rgb, dtype=np.float64)
    xyz_d65 = rgb @ BT2020_TO_XYZ_D65.T
    xyz_d55 = chromatic_adaptation_VonKries(
        xyz_d65, colour.xy_to_XYZ(D65_XY), colour.xy_to_XYZ(D55_XY), transform="CAT02"
    )
    return colour.XYZ_to_Lab(xyz_d55, illuminant=D55_XY)


def _delta_e2000(lab_a: np.ndarray, lab_b: np.ndarray) -> np.ndarray:
    """Per-row ΔE2000 between two (N, 3) Lab arrays."""
    return np.asarray(colour.delta_E(lab_a, lab_b, method="CIE 2000"))


# ---------------------------------------------------------------------------
# Self-test: synthesize a perfect 24-patch chart and round-trip
# ---------------------------------------------------------------------------


def _synthesize_perfect_chart_u16(
    patch_size: int = 60,
) -> tuple[np.ndarray, list[tuple[int, int]]]:
    """Build a (4*patch_size, 6*patch_size, 3) u16 array of the 24-patch chart.

    Returns the image and the list of (row_center_y, col_center_x) per
    patch in standard row-major order (4 rows × 6 cols, same order as
    the reference JSON).
    """
    _, lab_d55 = _load_reference()
    rgb = _lab_d55_to_linear_rec2020(lab_d55)
    rgb = np.clip(rgb, 0.0, 1.0)
    u16_per_patch = (rgb * 65535.0 + 0.5).astype(np.uint16)

    rows, cols = 4, 6
    h, w = rows * patch_size, cols * patch_size
    image = np.zeros((h, w, 3), dtype=np.uint16)
    centers: list[tuple[int, int]] = []
    for i in range(24):
        r, c = divmod(i, cols)
        y0, y1 = r * patch_size, (r + 1) * patch_size
        x0, x1 = c * patch_size, (c + 1) * patch_size
        image[y0:y1, x0:x1] = u16_per_patch[i]
        centers.append((y0 + patch_size // 2, x0 + patch_size // 2))
    return image, centers


def _sample_patches_by_centers(
    image: np.ndarray,
    centers: list[tuple[int, int]],
    half_size: int,
) -> np.ndarray:
    """Sample mean linear RGB inside a (2 half_size+1)² box around each center.

    Returns (24, 3) float64 array scaled to [0, 1] from the u16 image.
    """
    assert image.dtype == np.uint16, f"expected u16 image, got {image.dtype}"
    out = np.zeros((len(centers), 3), dtype=np.float64)
    for i, (cy, cx) in enumerate(centers):
        patch = image[
            cy - half_size : cy + half_size + 1,
            cx - half_size : cx + half_size + 1,
        ]
        out[i] = patch.reshape(-1, 3).mean(axis=0)
    return out / 65535.0


def test_colorimetric_self_test_with_synthetic_chart():
    """Harness round-trip: perfect synthetic chart → ΔE2000 ≈ 0.

    This proves the JSON loader, the linear-Rec.2020 ↔ Lab(D55)
    conversion, the patch sampler, and the ΔE2000 computation are all
    wired together correctly. It does NOT prove the colour science
    underneath them is correct — for that, the BT.2020-matrix
    cross-check below is the second leg.
    """
    names, lab_ref = _load_reference()
    assert len(names) == 24

    image, centers = _synthesize_perfect_chart_u16(patch_size=60)
    measured_rgb = _sample_patches_by_centers(image, centers, half_size=20)
    lab_measured = _linear_rec2020_to_lab_d55(measured_rgb)
    de = _delta_e2000(lab_ref, lab_measured)

    assert de.shape == (24,)
    assert np.all(np.isfinite(de)), "ΔE2000 must be finite for all patches"
    assert de.max() < SELF_TEST_MAX_DE_TOLERANCE, (
        f"self-test max ΔE2000 = {de.max():.4f}, expected < "
        f"{SELF_TEST_MAX_DE_TOLERANCE} (u16 quantization should be ~0.01). "
        f"Per-patch: {dict(zip(names, [round(float(x), 4) for x in de], strict=True))}"
    )
    assert de.mean() < SELF_TEST_MEAN_DE_TOLERANCE, (
        f"self-test mean ΔE2000 = {de.mean():.4f}, expected < "
        f"{SELF_TEST_MEAN_DE_TOLERANCE}"
    )


def test_self_test_matrix_cross_check_against_itu_r_bt2020_section_3_3():
    """colour-science path vs. hand-rolled BT.2020 matrix must agree.

    If colour-science changes its internal matrix, or our wiring picks
    the wrong colourspace name / transposes the matrix, the two paths
    disagree by orders of magnitude more than fp noise. The check
    catches transposition / wrong-matrix bugs that the round-trip
    self-test would silently absorb (because both paths use the same
    matrix internally).
    """
    _, lab_ref = _load_reference()
    rgb = _lab_d55_to_linear_rec2020(lab_ref)
    rgb = np.clip(rgb, 0.0, 1.0)

    lab_via_colour_science = _linear_rec2020_to_lab_d55(rgb)
    lab_via_hardcoded = _linear_rec2020_to_lab_d55_hardcoded_matrix(rgb)
    de = _delta_e2000(lab_via_colour_science, lab_via_hardcoded)

    # ITU-R BT.2020 §3.3 lists the matrix to 7 decimal places; modest
    # rounding drift vs colour-science's internal full-precision matrix
    # is expected but should be well below 0.1 ΔE2000.
    assert de.max() < 0.1, (
        f"colour-science and ITU-R BT.2020 §3.3 hand-rolled matrix "
        f"diverge by max ΔE2000 = {de.max():.6f}. This indicates a "
        f"transposed / wrong-colourspace bug in the harness, not a "
        f"data problem. Per-patch: {de}"
    )


# ---------------------------------------------------------------------------
# Real-chart test: lrt-cinema render → ΔE2000 against reference
# ---------------------------------------------------------------------------


def _discover_chart_raw() -> Path | None:
    """Find chart.<RAW-ext> in the fixture directory if present.

    Skips the .xmp / .json / .md / README companions. Returns None if
    nothing usable is present (the test then skips).
    """
    if not FIXTURE_DIR.is_dir():
        return None
    candidates = [
        p
        for p in FIXTURE_DIR.glob("chart.*")
        if p.suffix.lower() not in {".xmp", ".json", ".md", ".txt"}
    ]
    if not candidates:
        return None
    if len(candidates) > 1:
        raise RuntimeError(
            f"more than one chart.* RAW candidate found in {FIXTURE_DIR}: "
            f"{[p.name for p in candidates]}. Keep exactly one."
        )
    return candidates[0]


def test_colorimetric_real_chart_through_cinema_linear(tmp_path):
    """End-to-end ΔE2000 against a real chart shot.

    Skipped unless ALL of the following are present:
      * `tests/fixtures/colorchecker/chart.<RAW-ext>`
      * `tests/fixtures/colorchecker/chart.<RAW-ext>.xmp` (identity)
      * `colour-checker-detection` installed (`pip install -e .[detect]`)
      * `darktable-cli` on PATH

    Pass criterion: mean ΔE2000 < 2.0 AND max ΔE2000 < 4.0 over the 24
    patches. **Expected to fail on today's pipeline** — see
    docs/V03_PLAN.md Track A. The harness quantifies the gap.
    """
    chart_raw = _discover_chart_raw()
    if chart_raw is None:
        pytest.skip(
            "No chart RAW found in tests/fixtures/colorchecker/. "
            "Drop in chart.<ext> + chart.<ext>.xmp to enable the "
            "real-fixture leg — see that directory's README.md."
        )

    chart_xmp = Path(str(chart_raw) + ".xmp")
    if not chart_xmp.is_file():
        pytest.skip(
            f"Found {chart_raw.name} but no companion {chart_xmp.name}. "
            f"See tests/fixtures/colorchecker/README.md for the identity-XMP template."
        )

    ccd = pytest.importorskip(
        "colour_checker_detection",
        reason=(
            "colour-checker-detection not installed. Run "
            "`pip install -e '.[detect]'` to enable the real-chart leg."
        ),
    )

    from lrt_cinema.cli import main as cli_main
    from lrt_cinema.runner import darktable_cli_path

    if darktable_cli_path() is None:
        pytest.skip(
            "darktable-cli not on PATH; cannot render the chart. "
            "Install darktable to enable this test."
        )

    # Stage a fresh input dir so the fixture dir is never mutated by the
    # renderer (it writes a .dt.xmp into the output dir, but we don't
    # want any side-effects on the source-of-truth chart files).
    src = tmp_path / "input"
    src.mkdir()
    shutil.copy(chart_raw, src / chart_raw.name)
    shutil.copy(chart_xmp, src / chart_xmp.name)

    out = tmp_path / "output"

    rc = cli_main([
        "render",
        "--input", str(src),
        "--output", str(out),
        "--preset", "cinema-linear",
        "--quiet",
    ])
    assert rc == 0, f"lrt-cinema render failed with rc={rc}"

    rendered_tiffs = sorted(out.glob("*.tif"))
    assert rendered_tiffs, f"no .tif rendered in {out}"
    assert len(rendered_tiffs) == 1, (
        f"expected exactly one rendered TIFF, got {[p.name for p in rendered_tiffs]}"
    )

    tiff_path = rendered_tiffs[0]
    image = colour.io.read_image(str(tiff_path))
    # read_image returns float in [0, 1] for 16-bit TIFF input.
    assert image.ndim == 3 and image.shape[-1] in (3, 4), (
        f"expected RGB(A) image, got shape {image.shape}"
    )
    if image.shape[-1] == 4:
        image = image[..., :3]

    detected = ccd.detect_colour_checkers_segmentation(image)
    assert len(detected) >= 1, (
        f"colour-checker-detection found no ColorChecker in {tiff_path}. "
        f"Try a tighter chart framing or supply patch coordinates by hand."
    )
    # Convention: first detection is the highest-confidence one.
    swatches = np.asarray(detected[0])
    assert swatches.shape == (24, 3), (
        f"expected (24, 3) swatches, got {swatches.shape}"
    )

    lab_measured = _linear_rec2020_to_lab_d55(swatches)
    names, lab_ref = _load_reference()
    de = _delta_e2000(lab_ref, lab_measured)

    per_patch = {name: round(float(d), 3) for name, d in zip(names, de, strict=True)}

    assert de.mean() < REAL_CHART_MEAN_DE_THRESHOLD, (
        f"mean ΔE2000 = {de.mean():.3f} exceeds threshold "
        f"{REAL_CHART_MEAN_DE_THRESHOLD} (broadcast-cinema convention). "
        f"max = {de.max():.3f}. Per-patch: {per_patch}"
    )
    assert de.max() < REAL_CHART_MAX_DE_THRESHOLD, (
        f"max ΔE2000 = {de.max():.3f} exceeds threshold "
        f"{REAL_CHART_MAX_DE_THRESHOLD}. mean = {de.mean():.3f}. "
        f"Per-patch: {per_patch}"
    )
