"""Per-camera color-correction matrix storage + auto-detection.

The algorithmic engine (`--engine algorithmic`) renders without DCP-derived
modules and uses darktable's libraw-derived defaults for white-balance and
input-color matrix. This produces a per-camera color rendition gap vs the
DCP-driven engine (typical ΔE2000 mean 5-10 on real footage).

A `Calibration` is a 3×3 channelmixer correction matrix fitted offline that
maps the algorithmic engine's "no DCP" output into the DCP-driven engine's
output (or, in Tier 1, into a known target XYZ derived from spectral data).
The fitted matrix lives at:

    $LRT_CINEMA_CALIBRATION  (env-var override, takes a single directory)
    ~/.config/lrt-cinema/calibration/<camera-label>.npz  (XDG default)
    %APPDATA%/lrt-cinema/calibration/<camera-label>.npz  (Windows default)

mirroring the layout of `~/.config/lrt-cinema/profiles/` for DCP profiles.
Auto-detect at render time matches the same Adobe-style camera label
(see `dcp._adobe_camera_label`).

This module ships the storage + lookup infrastructure (Phase 2a).
The matrix-fitting tools (Tier 2 DCP distillation, Tier 1 SSF synthesis,
Tier 3 physical chart) are separate concerns implemented in
`tools/calibrate_camera.py` (Phase 2b+).

Schema notes
------------
* `format_version` is bumped only on backwards-incompatible changes;
  additive fields land at the next minor without a bump.
* `tier` records which tier produced the matrix (2 = DCP distillation,
  1 = SSF synthesis, 3 = physical chart, 0 = explicit user-supplied).
* `source` is the path / identifier of the calibration source (the
  DCP path for Tier 2, SSF dataset name for Tier 1, chart RAW path for
  Tier 3, or "explicit" for Tier 0). Lossless audit trail.
* `matrix` is the 3×3 channelmixer correction in linear-Rec.2020 working
  space (the algorithmic engine's output space). Emitted as
  `channelmixerrgb` v3 in the algorithmic-engine emit path.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from lrt_cinema.dcp import _adobe_camera_label, read_raw_make_model

_CALIBRATION_FORMAT_VERSION = 1


@dataclass
class Calibration:
    """A fitted per-camera channelmixer correction matrix + provenance.

    `matrix` shape: (3, 3), float32, linear-Rec.2020 working-space
    transform applied to the algorithmic-engine output. Identity = no
    correction (a valid calibration meaning "the algorithmic engine
    matches the target for this camera as-is").
    """

    camera_label: str
    matrix: np.ndarray
    tier: int                 # 0=explicit, 1=SSF, 2=DCP-distill, 3=chart
    source: str               # path / identifier of calibration source
    delta_e2000_mean: float = 0.0     # ΔE2000 mean post-fit (0.0 if not measured)
    delta_e2000_max: float = 0.0      # ΔE2000 max post-fit (0.0 if not measured)


def save_calibration(calibration: Calibration, path: Path) -> None:
    """Serialize a Calibration to `.npz`.

    Schema:
        format_version: int32
        camera_label:   0-d unicode
        matrix:         (3, 3) float32
        tier:           int32 (0..3)
        source:         0-d unicode
        delta_e2000_mean: float32
        delta_e2000_max:  float32
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if calibration.matrix.shape != (3, 3):
        raise ValueError(
            f"calibration matrix must be (3, 3), got {calibration.matrix.shape}"
        )
    if calibration.tier not in (0, 1, 2, 3):
        raise ValueError(
            f"calibration tier must be one of 0/1/2/3, got {calibration.tier}"
        )
    np.savez_compressed(
        path,
        format_version=np.int32(_CALIBRATION_FORMAT_VERSION),
        # Coerce None → "" so the .npz roundtrip doesn't turn None into
        # the literal string "None" (same defensive pattern as
        # dcp.save_profile per caveman-review PR #8 #7).
        camera_label=np.array(calibration.camera_label or "", dtype="U"),
        matrix=calibration.matrix.astype(np.float32),
        tier=np.int32(calibration.tier),
        source=np.array(calibration.source or "", dtype="U"),
        delta_e2000_mean=np.float32(calibration.delta_e2000_mean),
        delta_e2000_max=np.float32(calibration.delta_e2000_max),
    )


def load_calibration(path: Path) -> Calibration:
    """Deserialize a Calibration from `.npz`.

    Raises ValueError on missing required fields or on a format-version
    we don't recognize. Helpful error message points the user to
    re-fit if the version is stale (parallel to dcp.load_profile's
    actionable error).
    """
    path = Path(path)
    with np.load(path, allow_pickle=False) as data:
        version = int(data["format_version"])
        if version != _CALIBRATION_FORMAT_VERSION:
            raise ValueError(
                f"{path}: unsupported calibration format_version {version} "
                f"(this lrt-cinema build understands "
                f"format_version={_CALIBRATION_FORMAT_VERSION}). "
                f"Re-fit by running `tools/calibrate_camera.py` against "
                f"the source camera/DCP, or upgrade/downgrade lrt-cinema "
                f"to match the file's version."
            )
        if "matrix" not in data:
            raise ValueError(f"{path}: missing matrix — not a valid calibration")
        return Calibration(
            camera_label=str(data["camera_label"]),
            matrix=data["matrix"].astype(np.float32),
            tier=int(data["tier"]),
            source=str(data["source"]),
            delta_e2000_mean=float(data["delta_e2000_mean"]),
            delta_e2000_max=float(data["delta_e2000_max"]),
        )


def _calibration_search_roots() -> list[Path]:
    """Where to look for `.npz` calibration files, in lookup order.

    Mirrors `dcp._extracted_profile_search_roots`:
      1. `$LRT_CINEMA_CALIBRATION` env var — explicit user override
      2. `~/.config/lrt-cinema/calibration/` (XDG-style per-user)
      3. `%APPDATA%/lrt-cinema/calibration/` on Windows
    """
    roots: list[Path] = []
    env = os.environ.get("LRT_CINEMA_CALIBRATION")
    if env:
        p = Path(env)
        if p.is_dir():
            roots.append(p)
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            roots.append(Path(appdata) / "lrt-cinema" / "calibration")
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        config_home = Path(xdg) if xdg else (Path.home() / ".config")
        roots.append(config_home / "lrt-cinema" / "calibration")
    return [r for r in roots if r.is_dir()]


def find_calibration_for_camera(
    make: str,
    model: str,
    extra_roots: list[Path] | None = None,
) -> Path | None:
    """Locate a `.npz` calibration for (make, model).

    Filename convention: `<label>.npz` where `<label>` is the Adobe-style
    camera label (e.g. "Nikon D750"). Returns the first match across
    the search roots in `_calibration_search_roots()` plus any
    `extra_roots` passed in (used by tests).
    """
    label = _adobe_camera_label(make, model)
    roots = _calibration_search_roots()
    if extra_roots:
        roots.extend(r for r in extra_roots if r.is_dir())
    for root in roots:
        candidate = root / f"{label}.npz"
        if candidate.is_file():
            return candidate
    return None


def auto_detect_calibration(
    raw_path: Path,
    extra_roots: list[Path] | None = None,
) -> tuple[Calibration, Path] | None:
    """End-to-end calibration lookup from a RAW file.

    Probes the RAW's EXIF Make/Model, searches the calibration roots,
    returns `(calibration, source_path)` or None. Caller logs the
    source for the audit trail. Mirrors `dcp.auto_detect_profile`
    contract.
    """
    info = read_raw_make_model(raw_path)
    if info is None:
        return None
    make, model = info
    cal_path = find_calibration_for_camera(make, model, extra_roots=extra_roots)
    if cal_path is None:
        return None
    return load_calibration(cal_path), cal_path


# ---------------------------------------------------------------------------
# Tier 2 (DCP distillation) math primitives — colour-science integration
# ---------------------------------------------------------------------------

# Standard 24-patch ColorChecker patch name order, matching colour-science's
# `SDS_COLOURCHECKERS["ColorChecker N Ohta"]` keys. Listed here as a
# stable contract so callers can correlate fit-input patches with the
# canonical chart row/column layout regardless of colour-science version.
COLORCHECKER_PATCH_NAMES = (
    "dark skin", "light skin", "blue sky", "foliage",
    "blue flower", "bluish green", "orange", "purplish blue",
    "moderate red", "purple", "yellow green", "orange yellow",
    "blue", "green", "red", "yellow",
    "magenta", "cyan", "white 9.5 (.05 D)", "neutral 8 (.23 D)",
    "neutral 6.5 (.44 D)", "neutral 5 (.70 D)", "neutral 3.5 (1.05 D)",
    "black 2 (1.5 D)",  # matches colour-science's exact key
)


def colorchecker_xyz_under_illuminant(illuminant: str = "D55") -> np.ndarray:
    """Compute the 24-patch ColorChecker XYZ values under the named illuminant.

    Returns: (24, 3) float32 array. Row order matches
    `COLORCHECKER_PATCH_NAMES`. XYZ scaled so Y of a perfect white
    diffuser under the illuminant ≈ 1.0 (colour-science convention is
    Y=100; we divide by 100 to land in the [0, 1] linear-output domain
    that lrt-cinema's pipeline expects).

    Spectral integration via `colour.sd_to_XYZ` covers Wyszecki & Stiles
    Table 1(5.3.1) (CIE 1964 2° observer). Source: colour-science's
    `colour.SDS_COLOURCHECKERS["ColorChecker N Ohta"]` reflectance data
    (Ohta 1997, the original 24-patch dataset Adobe + X-Rite use).

    Deterministic: same illuminant → same XYZ, no shot-to-shot variation,
    no chart-aging concerns. This is the whole point of the Tier 2
    deterministic calibration vs Tier 3 physical chart.
    """
    try:
        import colour
    except ImportError as exc:
        raise ImportError(
            "Tier 2 calibration requires colour-science. "
            "Install: pip install colour-science"
        ) from exc
    chart = colour.SDS_COLOURCHECKERS["ColorChecker N Ohta"]
    illum_sd = colour.SDS_ILLUMINANTS[illuminant]
    patches = np.array(
        [colour.sd_to_XYZ(chart[name], illuminant=illum_sd) / 100.0
         for name in COLORCHECKER_PATCH_NAMES],
        dtype=np.float32,
    )
    return patches


def sample_patches_from_tiff(
    tiff_path: Path,
    patch_origins: list[tuple[int, int]],
    patch_size: int,
    margin_fraction: float = 0.25,
) -> np.ndarray:
    """Sample mean RGB per patch from a rendered TIFF.

    Reads the TIFF, samples the inner (1 - 2*margin_fraction) region of
    each patch's bbox, returns the per-channel mean. Margin avoids
    demosaic edge bleed across patch boundaries.

    Returns: (N, 3) float32 array of mean RGB values, where N is
    `len(patch_origins)`.
    """
    try:
        import tifffile
    except ImportError as exc:
        raise ImportError(
            "Patch sampling requires tifffile. Install: pip install tifffile"
        ) from exc
    arr = tifffile.imread(str(tiff_path))
    if arr.ndim != 3 or arr.shape[-1] < 3:
        raise ValueError(
            f"{tiff_path}: expected (H, W, 3+) RGB TIFF, got shape {arr.shape}"
        )
    arr = arr[..., :3]
    if np.issubdtype(arr.dtype, np.integer):
        # Normalize to [0, 1] floats. The TIFF dtype tells us the range.
        max_val = float(np.iinfo(arr.dtype).max)
        arr = arr.astype(np.float32) / max_val
    else:
        arr = arr.astype(np.float32)
    m = int(patch_size * margin_fraction)
    samples = np.empty((len(patch_origins), 3), dtype=np.float32)
    for i, (y, x) in enumerate(patch_origins):
        inner = arr[y + m : y + patch_size - m, x + m : x + patch_size - m]
        samples[i] = inner.reshape(-1, 3).mean(axis=0)
    return samples


def fit_calibration_matrix(
    measured_rgb: np.ndarray,
    target_rgb: np.ndarray,
    *,
    include_constraint_neutral_white: bool = False,
) -> np.ndarray:
    """Fit a 3×3 channelmixer matrix M minimizing ||M @ measured - target||²
    in least-squares sense.

    Closed-form linear regression: M = target.T @ pinv(measured.T).
    Equivalent to NumPy's `np.linalg.lstsq` per-output-channel; we
    compute it directly for clarity and to handle the degenerate
    rank-deficient case explicitly.

    `measured_rgb`: (N, 3) float — the algorithmic-engine renders.
    `target_rgb`:   (N, 3) float — the DCP-engine renders.
    Returns: (3, 3) float32 channelmixer matrix M such that
        M @ measured_pixel ≈ target_pixel
    for each of the N patches in least-squares optimal sense.

    Note: a linear 3×3 cannot fit any non-linear DCP transformation
    (HSM / LookTable / ProfileToneCurve). Residual after fit quantifies
    how much of the gap is captured by the matrix-only correction. The
    Tier 2 acceptance criterion is "narrow the ΔE2000 gap to < 1.0",
    which 3×3 should hit when HSM/LookTable contribution is modest;
    cameras with strong HSM (highly chroma-shaped looks) may need a
    larger fitter (future v0.5+ work).
    """
    if measured_rgb.shape != target_rgb.shape:
        raise ValueError(
            f"shape mismatch: measured {measured_rgb.shape} vs target {target_rgb.shape}"
        )
    if measured_rgb.ndim != 2 or measured_rgb.shape[1] != 3:
        raise ValueError(
            f"measured/target must be (N, 3); got {measured_rgb.shape}"
        )
    if measured_rgb.shape[0] < 3:
        raise ValueError(
            f"need at least 3 patches to fit a 3x3; got {measured_rgb.shape[0]}"
        )
    # Solve per-output-channel: M[i, :] @ measured.T = target[:, i] for
    # each output channel i. Equivalent to lstsq with measured as A,
    # target as B columns. We use np.linalg.lstsq for the rank-deficient
    # handling it does internally.
    sol, _residuals, _rank, _sv = np.linalg.lstsq(
        measured_rgb.astype(np.float64),
        target_rgb.astype(np.float64),
        rcond=None,
    )
    # sol shape: (3, 3) — rows are input channels (R, G, B), cols are
    # output channels. We want M with rows=output (red[*], green[*],
    # blue[*]) so transpose.
    matrix = sol.T.astype(np.float32)
    return matrix


# ---------------------------------------------------------------------------
# Tier 2 orchestration — dt-cli round-trip fit
# ---------------------------------------------------------------------------

def fit_tier2_via_dt_cli_roundtrip(
    dcp_profile,                                # DCPProfile — the oracle
    *,
    camera_make: str,
    camera_model: str,
    unique_camera_model: str | None = None,
    illuminant: str = "D55",
    work_dir: Path | None = None,
) -> Tier2FitResult:
    """End-to-end Tier 2 calibration via the dt-cli round-trip.

    Steps:
      1. Synthesize 24-patch XYZ via spectral integration under the
         named illuminant (D55 default — chosen to be near the daylight
         camera-WB users actually shoot in).
      2. Convert each patch XYZ to camera RGB via the DCP's color matrix
         interpolated at the illuminant kelvin. This is what a real
         sensor would record under that illuminant if its spectral
         response exactly matched Adobe's published calibration.
      3. Write a synthetic Bayer-mosaic DNG with the patches + the
         camera's EXIF Make/Model (libraw uses these to look up the
         per-camera default input matrix).
      4. Invoke `lrt-cinema render` twice: once with `--engine dcp`
         (the oracle: applies the full DCP pipeline), once with
         `--engine algorithmic` (the calibration target: dt's libraw
         default + LR-authored ops only, no DCP).
      5. Sample the 24 patches from both rendered TIFFs.
      6. Fit M such that M @ algorithmic_patches ≈ dcp_patches via
         least-squares.
      7. Compute ΔE2000 stats post-fit for the audit trail.

    Returns a `Tier2FitResult` with the matrix + diagnostic metadata.
    Caller is expected to wrap into a `Calibration` and persist via
    `save_calibration`.
    """
    try:
        import colour
    except ImportError as exc:
        raise ImportError(
            "Tier 2 calibration requires colour-science. "
            "Install: pip install colour-science"
        ) from exc
    from lrt_cinema.dcp import interpolate_color_matrix
    from lrt_cinema.synthetic_dng import write_calibration_dng

    if work_dir is None:
        import tempfile
        work_dir = Path(tempfile.mkdtemp(prefix="lrt-cinema-tier2-"))
    else:
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: synthesize patch XYZ under the named illuminant.
    patches_xyz = colorchecker_xyz_under_illuminant(illuminant)

    # Step 2: derive camera RGB. Interpolate the DCP's ColorMatrix at
    # the illuminant's nominal kelvin. ColorMatrix is XYZ → camera-RGB
    # at the calibration illuminant; applying it to our target XYZ
    # gives the camera-RGB a real sensor would record.
    illum_kelvin_map = {
        "A": 2856.0, "D50": 5003.0, "D55": 5500.0, "D65": 6504.0,
        "D75": 7504.0, "E": 5454.0,
    }
    target_kelvin = illum_kelvin_map.get(illuminant, 5500.0)
    cm_at_illum = interpolate_color_matrix(dcp_profile, target_kelvin)
    patches_camera_rgb = (cm_at_illum @ patches_xyz.T).T  # (24, 3)

    # Step 3: write the synthetic DNG. Clip + normalize peak to 0.8 so
    # we don't sit at sensor white during render — dt's exposure logic
    # behaves better with headroom.
    patches_camera_rgb = np.clip(patches_camera_rgb, 0.0, None)
    peak = patches_camera_rgb.max()
    if peak > 0:
        patches_camera_rgb = patches_camera_rgb * (0.8 / peak)
    dng_path = work_dir / "synth_chart.dng"
    layout = write_calibration_dng(
        dng_path,
        camera_make=camera_make,
        camera_model=camera_model,
        unique_camera_model=unique_camera_model or camera_model,
        color_matrix_1=dcp_profile.color_matrix_1,
        color_matrix_2=dcp_profile.color_matrix_2,
        calibration_illuminant_1=dcp_profile.calibration_illuminant_1 or 17,
        calibration_illuminant_2=dcp_profile.calibration_illuminant_2 or 21,
        patches_camera_rgb=patches_camera_rgb,
    )

    # Step 4: dt-cli round-trip via lrt-cinema render. Each run produces
    # one TIFF. Use a minimal LRT XMP so per-frame DevelopOps is neutral
    # (no exposure/curve/etc that could mask the channel response).
    input_dir = work_dir / "input"
    input_dir.mkdir(exist_ok=True)
    staged_dng = input_dir / "synth_chart.dng"
    import shutil as _shutil
    _shutil.copy(dng_path, staged_dng)
    (input_dir / "synth_chart.dng.xmp").write_bytes(_NEUTRAL_LRT_XMP)

    algo_dir = work_dir / "out_algo"
    dcp_dir = work_dir / "out_dcp"
    _run_lrt_cinema_render(input_dir, algo_dir, engine="algorithmic")
    _run_lrt_cinema_render(
        input_dir, dcp_dir, engine="dcp",
        dcp_profile_path=_write_dcp_profile_npz(dcp_profile, work_dir),
    )

    algo_tif = next(algo_dir.glob("*.tif"))
    dcp_tif = next(dcp_dir.glob("*.tif"))

    # Step 5: sample patches.
    algo_patches = sample_patches_from_tiff(
        algo_tif, layout.patch_origins, layout.patch_size,
    )
    dcp_patches = sample_patches_from_tiff(
        dcp_tif, layout.patch_origins, layout.patch_size,
    )

    # Step 6: fit.
    matrix = fit_calibration_matrix(algo_patches, dcp_patches)

    # Step 7: post-fit ΔE2000 stats. Convert RGB to Lab for comparison.
    # The TIFFs are linear Rec.2020 per the cinema-linear preset.
    fitted_patches = (matrix @ algo_patches.T).T
    lab_target = _linear_rec2020_to_lab(dcp_patches)
    lab_fitted = _linear_rec2020_to_lab(fitted_patches)
    de_values = colour.delta_E(lab_target, lab_fitted, method="CIE 2000")
    delta_e_mean = float(np.mean(de_values))
    delta_e_max = float(np.max(de_values))

    return Tier2FitResult(
        matrix=matrix,
        delta_e2000_mean=delta_e_mean,
        delta_e2000_max=delta_e_max,
        dng_path=dng_path,
        algo_tif=algo_tif,
        dcp_tif=dcp_tif,
        algo_patches=algo_patches,
        dcp_patches=dcp_patches,
        target_xyz=patches_xyz,
        illuminant=illuminant,
    )


@dataclass
class Tier2FitResult:
    """Diagnostic bundle returned by `fit_tier2_via_dt_cli_roundtrip`.

    `matrix` is the fitted 3×3; the rest is audit metadata. Caller
    typically just consumes `matrix` + the ΔE stats and packages them
    into a `Calibration`.
    """
    matrix: np.ndarray
    delta_e2000_mean: float
    delta_e2000_max: float
    dng_path: Path
    algo_tif: Path
    dcp_tif: Path
    algo_patches: np.ndarray
    dcp_patches: np.ndarray
    target_xyz: np.ndarray
    illuminant: str


# Minimal LRT XMP: xmp:Rating=4 marks the frame as a keyframe; no
# DevelopOps fields set so all v0.4-mapped knobs stay at default.
# Required to land any keyframe at all in `parse_sequence`.
_NEUTRAL_LRT_XMP = (
    b'<?xml version="1.0"?>\n'
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
    b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    b'<rdf:Description rdf:about=""\n'
    b'  xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"\n'
    b'  xmlns:xmp="http://ns.adobe.com/xap/1.0/"\n'
    b'  crs:Exposure2012="0.0"\n'
    b'  xmp:Rating="4"/>\n'
    b'</rdf:RDF></x:xmpmeta>\n'
)


def _write_dcp_profile_npz(dcp_profile, work_dir: Path) -> Path:
    """Stage the DCPProfile as a .npz so `--engine dcp --dcp <path>` can
    consume it without depending on user env config."""
    from lrt_cinema.dcp import save_profile
    npz = work_dir / "oracle_dcp.npz"
    save_profile(dcp_profile, npz)
    return npz


def _run_lrt_cinema_render(
    input_dir: Path,
    output_dir: Path,
    *,
    engine: str,
    dcp_profile_path: Path | None = None,
) -> None:
    """Invoke `lrt-cinema render` via subprocess (in-process import would
    require resetting CLI state between calls). One TIFF expected in
    output_dir on return; raises on non-zero exit.
    """
    import subprocess
    output_dir.mkdir(parents=True, exist_ok=True)
    argv = [
        "lrt-cinema", "render",
        "--input", str(input_dir),
        "--output", str(output_dir),
        "--preset", "cinema-linear",
        "--engine", engine,
        "--no-auto-dcp",   # we either supply --dcp or none; no auto-detect
        "--quiet",
    ]
    if dcp_profile_path is not None:
        argv += ["--dcp", str(dcp_profile_path)]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(
            f"lrt-cinema render --engine {engine} failed "
            f"(returncode {proc.returncode}):\n"
            f"  stderr: {proc.stderr[-1500:]}\n"
            f"  stdout: {proc.stdout[-500:]}"
        )


def _linear_rec2020_to_lab(rgb: np.ndarray) -> np.ndarray:
    """Convert linear Rec.2020 RGB (the cinema-linear preset's output
    space) to CIE Lab(D50) for ΔE2000 computation.

    Rec.2020 → XYZ via ITU-R BT.2020-2 §3.3 matrix. D65→D50 chromatic
    adaptation via colour-science (Bradford CAT). Lab via colour-science.
    """
    import colour
    # ITU-R BT.2020-2 §3.3 matrix (D65 white point):
    m_rec2020_to_xyz_d65 = np.array([
        [0.6369580, 0.1446169, 0.1688810],
        [0.2627002, 0.6779981, 0.0593017],
        [0.0000000, 0.0280727, 1.0609851],
    ])
    xyz_d65 = (m_rec2020_to_xyz_d65 @ rgb.T).T
    # D65 → D50 chromatic adaptation for CIE Lab(D50). Von Kries is
    # the canonical method name in colour-science ≥0.4; Bradford was
    # the pre-0.4 alias. Equivalent matrix-based adaptation.
    xyz_d50 = colour.adaptation.chromatic_adaptation(
        xyz_d65,
        colour.xy_to_XYZ([0.31270, 0.32900]),  # D65
        colour.xy_to_XYZ([0.34570, 0.35850]),  # D50
        method="Von Kries",
    )
    return colour.XYZ_to_Lab(
        xyz_d50,
        illuminant=np.array([0.34570, 0.35850]),  # D50
    )


def _default_user_calibration_dir() -> Path:
    """Where `tools/calibrate_camera.py` writes by default.

    Mirrors the `~/.config/lrt-cinema/profiles/` default in
    `tools/extract_dcp_library.py`. Honors `$XDG_CONFIG_HOME` on
    Unix and `%APPDATA%` on Windows.
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else (Path.home() / ".config")
    return base / "lrt-cinema" / "calibration"
