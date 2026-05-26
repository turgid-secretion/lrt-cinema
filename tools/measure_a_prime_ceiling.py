#!/usr/bin/env python3
"""Measure A' empirical ΔE ceiling across the Adobe Standard catalog.

Companion to `09_dcp_variance.md` (Q1 variance measurement). Where Q1 asked
"how much do per-camera DCPs vary?", this script asks "given that variance,
how close can a SHARED transform come to matching each camera's Adobe
Standard render?"

Three coupled questions (per `docs/research/.../09e_a_prime_ceiling.md`):

  Q-A: Achievable ΔE2000 when replacing per-camera HSM/LookTable/ToneCurve
       with a single shared transform — the A' ceiling.
  Q-B: Does an "uncompressed" representation (direct-RGB 33³ cube baking
       per-camera-output average) give materially lower ΔE than the
       "compressed" median-of-HSV-cubes approach?
  Q-C: Is the median HSM useful, or does LookTable-only / no-HSM perform
       comparably?

Methodology
-----------

Working space: linear ProPhoto RGB (D50 anchor), matching `lut3d_baker.py`'s
HSV-cube application convention and dt's `lut3d` module's `LIN_PROPHOTO=5`
colorspace tag. All cube application happens in ProPhoto; ΔE measured in
CIELab(D50) for consistency with the working-space anchor.

Reference patches: ColorChecker N Ohta (24) + rawtoaces v1 training set
(190) = 214 spectral reflectance distributions, integrated under D55
(cinema reference illuminant) against the CIE 1931 2° observer to yield
reference XYZ(D55) per patch.

For each camera DCP in the panel:

  1. Adapt patch XYZ(D55) → XYZ(D50) via CAT16 (matches dt's CAT convention).
  2. Project to ProPhoto = XYZ(D50) @ M_XYZ_to_PROPHOTO. This is the
     working-space input W. (Same per camera — the per-camera matrix
     stage cancels because both A' and the per-camera-DCP target operate
     on the SAME working-space W. The matrix question lives in candidate A,
     not A'; A' isolates the HSV-residual-stage replacement.)
  3. Apply Adobe pipeline per-camera: HSM (mired-blended at D50) →
     BaselineExposureOffset → LookTable → ProfileToneCurve. Output is the
     per-camera Adobe Standard render in ProPhoto.
  4. Convert per-camera output ProPhoto → XYZ(D50) → Lab(D50).

This yields a (N_cameras, N_patches, 3) tensor of Lab values per camera —
the ground truth.

Then for each A' candidate (described in `_build_a_prime_candidates`):

  5. Apply the same A' transform to all W's, get a (N_patches, 3) Lab.
  6. ΔE2000 per (camera, patch) between A' Lab and ground truth Lab.

The "matrix cancels in the comparison" claim: A' and ground-truth both
take the same working-space W as input. The per-camera matrix produces W
but it's identical for both branches; whatever W is, A' and ground truth
process the same W. The fidelity question is purely about the HSV/cube
stage replacement.

NOTE: A complementary measurement WOULD include per-camera matrices to
ask "how realistic is the working-space input distribution per camera?"
That measurement is held back to keep this script focused on Q-A/Q-B/Q-C.
The per-camera-matrix-realism question maps to "does W from a Samsung S24
look like W from a Nikon D750 for the same scene patch?" — interesting,
but not the A'-ceiling question.

A' candidates measured
----------------------

  identity       : do nothing. Baseline showing the gap if A' ships nothing.
  median-HSV     : median over (90,30,1) HSMs + median over (36,8,16)
                   LookTables. Compressed shape — current recommendation
                   in 11_recommendation.md.
  median-look    : LookTable only (drop HSM). Tests Q-C.
  median-hsm     : HSM only (drop LookTable). Tests Q-C.
  output-avg-33  : 33³ direct-RGB cube; at each grid cell, mean of
                   per-camera Adobe Standard outputs (computed in Lab,
                   converted back to ProPhoto). The "uncompressed" Q-B
                   candidate. Aggregates in output-space, not HSV-cube
                   space.
  output-avg-65  : same as above at 65³, only if 33³ shows promise.

Output
------

Markdown + JSON to `--out` (default `/tmp/a_prime_ceiling.{md,json}`).
Tables: per-camera × per-candidate ΔE2000 mean / P95 / max. Aggregate
distribution across panel.

Reproducer
----------

    python3 tools/measure_a_prime_ceiling.py \
        --out /tmp/a_prime_ceiling \
        --panel-size 40

Defaults to a stratified panel including the load-bearing manufacturers
(Apple, Samsung — where Adobe tunes aggressively per-camera, the worst
case for A') plus the standard timelapse-camera makes (Nikon, Canon,
Sony, Fujifilm).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import warnings
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")

# Add src/ to path for the project's DCP parser.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lrt_cinema.dcp import (  # noqa: E402
    DCPProfile,
    HsvCube,
    interpolate_hsv_cube,
    parse_dcp,
)
from lrt_cinema.lut3d_baker import (  # noqa: E402
    _apply_hsv_cube,
    _hsv_to_rgb_dcp,
    _rgb_to_hsv_dcp,
)

import colour  # noqa: E402

# --------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------

ADOBE_STD_DIR = Path("/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Adobe Standard")

# CIE 1931 2° observer + D55 reference illuminant — cinema convention.
CMFS = colour.MSDS_CMFS["CIE 1931 2 Degree Standard Observer"]
SD_D55 = colour.SDS_ILLUMINANTS["D55"]

# ProPhoto RGB (Kodak ROMM RGB) — D50 anchor. Matches dt's lut3d LIN_PROPHOTO=5
# and `lut3d_baker.py`'s convention.
M_XYZ_D50_TO_PROPHOTO = colour.RGB_COLOURSPACES["ProPhoto RGB"].matrix_XYZ_to_RGB
M_PROPHOTO_TO_XYZ_D50 = colour.RGB_COLOURSPACES["ProPhoto RGB"].matrix_RGB_to_XYZ

# CAT16 D55 → D50 chromatic adaptation matrix (precomputed; identity on Y
# since both are 2° observer same-CMFS, off-diagonal nonzero from
# whitepoint shift).
XYZ_D55 = np.array(colour.xy_to_XYZ(colour.CCS_ILLUMINANTS["CIE 1931 2 Degree Standard Observer"]["D55"]))
XYZ_D50 = np.array(colour.xy_to_XYZ(colour.CCS_ILLUMINANTS["CIE 1931 2 Degree Standard Observer"]["D50"]))
CAT16_D55_TO_D50 = colour.adaptation.matrix_chromatic_adaptation_VonKries(
    XYZ_D55, XYZ_D50, transform="CAT16",
)

# Standard observer chromaticity for D50 (used for Lab conversion).
ILLUMINANT_D50_XY = colour.CCS_ILLUMINANTS["CIE 1931 2 Degree Standard Observer"]["D50"]


# --------------------------------------------------------------------------
# Patch synthesis
# --------------------------------------------------------------------------

def synthesize_reference_patches() -> tuple[np.ndarray, list[str]]:
    """Generate (N_patches, 3) XYZ array under D55 reference illuminant.

    Combines ColorChecker N Ohta (24) + rawtoaces v1 training set (190) for
    214 patches total. The ColorChecker covers controlled chart colors;
    rawtoaces covers a broader natural-reflectance distribution including
    saturated foliage, skin tones, and synthetic targets used in cinema
    IDT training.

    Y normalized to [0, 1] per patch — i.e. the perfect diffuser at D55
    yields Y = 1.0. `colour.sd_to_XYZ` is internally normalised so that
    a constant-1.0 reflectance under the supplied illuminant integrates to
    Y = 100; we divide by 100 to land in the [0, 1] working-space convention
    matching ProPhoto / Rec.2020 / dt's working pipeline. This puts
    ColorChecker patches into the typical 0.04-0.9 Y range observed in
    real renders.
    """
    cc = colour.SDS_COLOURCHECKERS["ColorChecker N Ohta"]
    raw = colour.characterisation.read_training_data_rawtoaces_v1()

    patches = []
    names = []

    for name, sd in cc.items():
        xyz = colour.sd_to_XYZ(sd, cmfs=CMFS, illuminant=SD_D55) / 100.0
        patches.append(xyz)
        names.append(f"cc24/{name}")

    # rawtoaces training data is MultiSpectralDistributions — convert each
    # column to a single SpectralDistribution and integrate. The labels
    # attribute carries the rawtoaces patch names when present.
    rta_labels = (
        raw.labels if hasattr(raw, "labels") and raw.labels
        else [f"rta{j}" for j in range(raw.values.shape[1])]
    )
    for i, col_name in enumerate(rta_labels):
        column_sd = colour.SpectralDistribution(
            dict(zip(raw.wavelengths, raw.values[:, i])),
        )
        xyz = colour.sd_to_XYZ(column_sd, cmfs=CMFS, illuminant=SD_D55) / 100.0
        patches.append(xyz)
        names.append(f"rta190/{col_name}")

    return np.array(patches, dtype=np.float64), names


def patches_to_prophoto(patches_xyz_d55: np.ndarray) -> np.ndarray:
    """Adapt XYZ(D55) to ProPhoto working space (D50).

    Two-step transform: CAT16 D55 → D50, then XYZ(D50) → ProPhoto matrix.
    Result is working-space ProPhoto RGB suitable for HSV-cube application.
    Values may be < 0 if a patch is outside ProPhoto gamut; the cube
    application's negative-component mask handles those.
    """
    xyz_d50 = patches_xyz_d55 @ CAT16_D55_TO_D50.T
    return xyz_d50 @ M_XYZ_D50_TO_PROPHOTO.T


def prophoto_to_lab(rgb: np.ndarray) -> np.ndarray:
    """Inverse: ProPhoto → XYZ(D50) → Lab(D50)."""
    xyz = rgb @ M_PROPHOTO_TO_XYZ_D50.T
    return colour.XYZ_to_Lab(xyz, illuminant=ILLUMINANT_D50_XY)


# --------------------------------------------------------------------------
# Adobe pipeline application (per-camera ground truth)
# --------------------------------------------------------------------------

def apply_tone_curve(v: np.ndarray, curve: np.ndarray | None) -> np.ndarray:
    """Apply a DCP ProfileToneCurve to V values via 1D linear interp.

    Curve is N×2 array of (x, y) tabulated points; we linearly interpolate
    in [0, 1]. Out-of-range V values clamp to curve endpoints (matches
    Adobe convention).
    """
    if curve is None:
        return v
    xs = curve[:, 0]
    ys = curve[:, 1]
    return np.interp(v, xs, ys)


def apply_full_adobe_pipeline(
    profile: DCPProfile,
    prophoto_rgb: np.ndarray,
    target_kelvin: float = 5500.0,
) -> np.ndarray:
    """Apply per-camera DCP HSM/BEO/LookTable/ToneCurve in ProPhoto.

    Input shape: (N, 3) ProPhoto-space samples. Output: (N, 3) post-pipeline
    ProPhoto-space samples — same convention as `lut3d_baker.py`'s cube
    bake but in measurement form.

    Pipeline order per DNG 1.7.1 §"Camera Profile Encoding":
      1. RGB → HSV (Adobe hexcone)
      2. HSM (if present) — trilinear sample, apply hue/sat/val shifts
      3. BaselineExposureOffset — multiplicative V scale
      4. LookTable (if present) — same as HSM
      5. ProfileToneCurve (if present) — 1D curve on V (Adobe applies it
         in luminance space, simplest faithful approximation is V-domain;
         it's mainly a tone-shape effect not chromatic)
      6. HSV → RGB
      7. Restore matrix-only passthrough for negative-component inputs.

    `target_kelvin` selects the HSM mired-blend (LookTable has no
    per-illuminant variant). 5500K is the cinema-D55 reference; profile's
    own calibration illuminants 1/2 are used for the blend endpoints.
    """
    h, s, v, valid = _rgb_to_hsv_dcp(prophoto_rgb)

    # HSM (per-illuminant blend at target_kelvin)
    if profile.hue_sat_map is not None:
        hsm_blended = interpolate_hsv_cube(
            profile.hue_sat_map,
            target_kelvin,
            profile.kelvin_1,
            profile.kelvin_2,
        )
        h, s, v = _apply_hsv_cube(h, s, v, hsm_blended, profile.hue_sat_map)

    # BaselineExposureOffset (multiplicative on V — Adobe applies it
    # between HSM and LookTable).
    if profile.baseline_exposure_offset != 0.0:
        v = v * (2.0 ** profile.baseline_exposure_offset)

    # LookTable (no per-illuminant blend; single cube).
    if profile.look_table is not None:
        h, s, v = _apply_hsv_cube(
            h, s, v, profile.look_table.data_1, profile.look_table,
        )

    # ProfileToneCurve (rarely present — Q1 measured 3% of catalog).
    # Adobe's pipeline applies it in linear-encoded V (after HSV decomp).
    if profile.profile_tone_curve is not None:
        v = apply_tone_curve(v, profile.profile_tone_curve)

    rgb_out = _hsv_to_rgb_dcp(h, s, v)
    # Pixels with negative input components bypass the cube and pass through
    # the matrix-only stage (RT convention; lut3d_baker.py also uses this).
    rgb_out = np.where(valid[..., None], rgb_out, prophoto_rgb)
    return rgb_out


# --------------------------------------------------------------------------
# A' candidate construction
# --------------------------------------------------------------------------

@dataclass
class CandidateApprime:
    name: str
    apply: callable  # (prophoto_rgb: ndarray) -> ndarray


def build_median_hsv_candidate(
    profiles: list[DCPProfile],
    use_hsm: bool,
    use_look: bool,
    name: str,
) -> CandidateApprime:
    """Median-of-cubes A' — the compressed-representation candidate.

    Filters to same-dimension HSV cubes (per Q1's grouping: HSM is (90,30,1)
    for 322 profiles, LookTable is (36,8,16) for 474 profiles). Median per
    cell across the filtered cameras.

    The cube's apply path mirrors `lut3d_baker.py`'s — RGB → HSV → cube →
    HSV → RGB — but skipping BEO entirely since the shared candidate has
    no BEO (Q1: BEO = 0 across all Adobe Standard).
    """
    # Gather same-dim HSMs.
    hsm_cubes = []
    hsm_meta = None
    if use_hsm:
        for p in profiles:
            if p.hue_sat_map is not None and (
                p.hue_sat_map.hue_divisions == 90
                and p.hue_sat_map.sat_divisions == 30
                and p.hue_sat_map.val_divisions == 1
            ):
                # Blend Data1/Data2 at the 5500K reference; this baked-blend
                # is what the shared transform actually applies.
                blended = interpolate_hsv_cube(
                    p.hue_sat_map, 5500.0, p.kelvin_1, p.kelvin_2,
                )
                hsm_cubes.append(blended)
                hsm_meta = p.hue_sat_map  # all same shape/encoding here
        if hsm_cubes:
            hsm_median = np.median(np.stack(hsm_cubes), axis=0).astype(np.float32)
        else:
            hsm_median = None
    else:
        hsm_median = None

    # Gather same-dim LookTables.
    look_cubes = []
    look_meta = None
    if use_look:
        for p in profiles:
            if p.look_table is not None and (
                p.look_table.hue_divisions == 36
                and p.look_table.sat_divisions == 8
                and p.look_table.val_divisions == 16
            ):
                look_cubes.append(p.look_table.data_1)
                look_meta = p.look_table
        if look_cubes:
            look_median = np.median(np.stack(look_cubes), axis=0).astype(np.float32)
        else:
            look_median = None
    else:
        look_median = None

    def apply(prophoto_rgb: np.ndarray) -> np.ndarray:
        h, s, v, valid = _rgb_to_hsv_dcp(prophoto_rgb)
        if hsm_median is not None and hsm_meta is not None:
            h, s, v = _apply_hsv_cube(h, s, v, hsm_median, hsm_meta)
        if look_median is not None and look_meta is not None:
            h, s, v = _apply_hsv_cube(h, s, v, look_median, look_meta)
        out = _hsv_to_rgb_dcp(h, s, v)
        return np.where(valid[..., None], out, prophoto_rgb)

    return CandidateApprime(name=name, apply=apply)


def build_identity_candidate() -> CandidateApprime:
    return CandidateApprime(name="identity", apply=lambda x: x)


def build_output_average_cube_candidate(
    profiles: list[DCPProfile],
    cube_size: int,
    name: str,
) -> CandidateApprime:
    """Output-average A' — bake per-camera Adobe-Standard output mean into a
    direct-RGB cube.

    At each of the cube_size³ grid points G in ProPhoto:
      1. Apply each profile's full Adobe pipeline to G → G' per camera.
      2. Convert all G' to Lab(D50).
      3. Lab mean across cameras → G'_mean_lab.
      4. Lab → ProPhoto → store at G.

    Mean-in-Lab approximates the L2-minimum-ΔE single point per cell. Direct
    RGB cube (R-fast iteration matching dt's `_calculate_clut`) — no HSV
    decomp at apply time; trilinear sampling in ProPhoto.

    Higher cube_size → denser sampling → less interpolation bias. 33 is
    the Adobe/Resolve standard; 65 is the "uncompressed" stretch.

    This is the load-bearing Q-B candidate: aggregation happens in
    OUTPUT space (Lab mean) instead of cube-parameter space (median of
    hue-shift / sat-scale parameters). Captures more cross-camera signal
    by construction.
    """
    n = cube_size
    axis = np.linspace(0.0, 1.0, n, dtype=np.float64)
    R, G, B = np.meshgrid(axis, axis, axis, indexing="ij")
    grid = np.stack([R, G, B], axis=-1).reshape(-1, 3)  # (n³, 3)

    # Apply each camera's pipeline at every grid point.
    n_cams = len(profiles)
    all_outputs_lab = np.zeros((n_cams, grid.shape[0], 3), dtype=np.float64)
    for i, p in enumerate(profiles):
        out_rgb = apply_full_adobe_pipeline(p, grid)
        all_outputs_lab[i] = prophoto_to_lab(out_rgb)

    # Mean across cameras (per-cell Lab mean).
    mean_lab = all_outputs_lab.mean(axis=0)

    # Lab → XYZ(D50) → ProPhoto.
    mean_xyz = colour.Lab_to_XYZ(mean_lab, illuminant=ILLUMINANT_D50_XY)
    cube_rgb = (mean_xyz @ M_XYZ_D50_TO_PROPHOTO.T).reshape(n, n, n, 3)
    cube_rgb = cube_rgb.astype(np.float32)

    def apply(prophoto_rgb: np.ndarray) -> np.ndarray:
        """Trilinear-sample the (n, n, n, 3) RGB cube at every input
        ProPhoto sample. Inputs outside [0, 1] clamp to cube boundary
        (matches Resolve cube DOMAIN_MIN/MAX convention).
        """
        clipped = np.clip(prophoto_rgb, 0.0, 1.0)
        # Convert to (n-1)-scaled indices.
        scaled = clipped * (n - 1)
        i0 = np.floor(scaled).astype(np.int32)
        i1 = np.minimum(i0 + 1, n - 1)
        f = scaled - i0
        # Trilinear weights — clamp f to [0, 1] for safety on boundary.
        f = np.clip(f, 0.0, 1.0)

        # 8 corner samples.
        r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
        r1, g1, b1 = i1[..., 0], i1[..., 1], i1[..., 2]
        c000 = cube_rgb[r0, g0, b0]
        c100 = cube_rgb[r1, g0, b0]
        c010 = cube_rgb[r0, g1, b0]
        c110 = cube_rgb[r1, g1, b0]
        c001 = cube_rgb[r0, g0, b1]
        c101 = cube_rgb[r1, g0, b1]
        c011 = cube_rgb[r0, g1, b1]
        c111 = cube_rgb[r1, g1, b1]

        fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
        c00 = c000 * (1 - fr) + c100 * fr
        c10 = c010 * (1 - fr) + c110 * fr
        c01 = c001 * (1 - fr) + c101 * fr
        c11 = c011 * (1 - fr) + c111 * fr
        c0 = c00 * (1 - fg) + c10 * fg
        c1 = c01 * (1 - fg) + c11 * fg
        return c0 * (1 - fb) + c1 * fb

    return CandidateApprime(name=name, apply=apply)


# --------------------------------------------------------------------------
# Sampling + ΔE measurement
# --------------------------------------------------------------------------

def enumerate_dcps_by_make() -> dict[str, list[Path]]:
    by_make: dict[str, list[Path]] = defaultdict(list)
    if not ADOBE_STD_DIR.is_dir():
        sys.exit(f"error: {ADOBE_STD_DIR} not found")
    for dcp in sorted(ADOBE_STD_DIR.glob("*.dcp")):
        name = dcp.name.removesuffix(" Adobe Standard.dcp")
        # Some Samsung profiles end in "Adobe_Standard.dcp" (underscore).
        name = name.removesuffix(" Adobe_Standard.dcp")
        make = name.split(" ", 1)[0]
        by_make[make].append(dcp)
    return by_make


# Manufacturers we want in the panel — Apple/Samsung are the high-variance
# cases per Q1; Nikon/Canon/Sony/Fujifilm are the primary timelapse camera
# makes; Panasonic/Olympus/Google add long-tail variety.
PANEL_MAKE_WEIGHTS = {
    "Apple": 8,
    "Samsung": 8,
    "Nikon": 6,
    "Canon": 6,
    "Sony": 6,
    "Fujifilm": 4,
    "Panasonic": 4,
    "Olympus": 3,
    "Google": 3,
    "Pentax": 2,
    "Leica": 2,
}


def select_panel(by_make: dict[str, list[Path]], target_size: int, seed: int = 0) -> list[Path]:
    """Stratified sample weighted toward the timelapse-camera + high-variance makes.

    Returns up to `target_size` paths. If the panel weights total more than
    target_size, scales proportionally; if any make is short, falls through
    to the next.
    """
    rng = random.Random(seed)
    total_weight = sum(PANEL_MAKE_WEIGHTS.values())
    selected: list[Path] = []
    for make, weight in PANEL_MAKE_WEIGHTS.items():
        available = by_make.get(make, [])
        if not available:
            continue
        n_take = min(
            len(available),
            max(1, int(round(weight * target_size / total_weight))),
        )
        sampled = rng.sample(available, n_take)
        selected.extend(sampled)
    return selected[:target_size]


def deltaE_2000(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """Vectorized ΔE2000 between two (..., 3) Lab arrays."""
    return colour.delta_E(lab1, lab2, method="CIE 2000")


# --------------------------------------------------------------------------
# Main measurement
# --------------------------------------------------------------------------

def measure(
    profiles: list[DCPProfile],
    profile_names: list[str],
    patches_prophoto: np.ndarray,
    patch_names: list[str],
    candidates: list[CandidateApprime],
) -> dict:
    """For each (profile, candidate), compute ΔE2000 mean/P95/max.

    Returns a dict suitable for JSON output:
      {
        "patches": [name, ...],
        "profiles": [name, ...],
        "candidates": [name, ...],
        "per_camera": {profile_name: {candidate_name: {mean, P50, P95, max}}}
        "aggregate": {candidate_name: {mean, P95, max} across all (cam, patch)}
      }
    """
    # Build per-camera ground truth Lab.
    n_cams = len(profiles)
    n_patches = patches_prophoto.shape[0]
    gt_lab = np.zeros((n_cams, n_patches, 3), dtype=np.float64)
    for i, p in enumerate(profiles):
        rgb = apply_full_adobe_pipeline(p, patches_prophoto)
        gt_lab[i] = prophoto_to_lab(rgb)

    out_per_camera: dict[str, dict[str, dict]] = {}
    out_aggregate: dict[str, dict] = {}

    # Pre-compute candidate-applied Lab (only varies by candidate, not by camera).
    cand_lab = {}
    for cand in candidates:
        cand_rgb = cand.apply(patches_prophoto)
        cand_lab[cand.name] = prophoto_to_lab(cand_rgb)

    for cand in candidates:
        # Per-camera ΔE distributions.
        deltas = np.zeros((n_cams, n_patches), dtype=np.float64)
        for i in range(n_cams):
            d = deltaE_2000(gt_lab[i], cand_lab[cand.name])
            deltas[i] = d
            cam_name = profile_names[i]
            if cam_name not in out_per_camera:
                out_per_camera[cam_name] = {}
            out_per_camera[cam_name][cand.name] = {
                "mean": float(d.mean()),
                "P50": float(np.percentile(d, 50)),
                "P95": float(np.percentile(d, 95)),
                "max": float(d.max()),
            }
        out_aggregate[cand.name] = {
            "n_pairs": int(deltas.size),
            "mean": float(deltas.mean()),
            "P50": float(np.percentile(deltas, 50)),
            "P95": float(np.percentile(deltas, 95)),
            "P99": float(np.percentile(deltas, 99)),
            "max": float(deltas.max()),
            # Per-camera mean distribution — captures cross-camera spread.
            "per_camera_mean_distribution": {
                "mean": float(deltas.mean(axis=1).mean()),
                "P50": float(np.percentile(deltas.mean(axis=1), 50)),
                "P95": float(np.percentile(deltas.mean(axis=1), 95)),
                "max": float(deltas.mean(axis=1).max()),
                "min": float(deltas.mean(axis=1).min()),
            },
        }

    return {
        "n_patches": n_patches,
        "n_cameras": n_cams,
        "patches": patch_names,
        "profiles": profile_names,
        "candidates": [c.name for c in candidates],
        "per_camera": out_per_camera,
        "aggregate": out_aggregate,
    }


def write_markdown(result: dict, profile_makes: dict[str, str], out_path: Path) -> None:
    """Write a readable Markdown summary table to out_path."""
    lines: list[str] = []
    lines.append("# A' empirical ceiling — measurement results")
    lines.append("")
    lines.append(f"Panel: {result['n_cameras']} cameras × {result['n_patches']} patches.")
    lines.append("")
    lines.append("## Aggregate ΔE2000 across the panel")
    lines.append("")
    lines.append("| Candidate | mean | P50 | P95 | P99 | max | per-cam-mean P95 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for cand_name in result["candidates"]:
        a = result["aggregate"][cand_name]
        cm = a["per_camera_mean_distribution"]
        lines.append(
            f"| {cand_name} | {a['mean']:.2f} | {a['P50']:.2f} | "
            f"{a['P95']:.2f} | {a['P99']:.2f} | {a['max']:.2f} | "
            f"{cm['P95']:.2f} |"
        )
    lines.append("")
    lines.append("## Per-camera mean ΔE2000")
    lines.append("")
    header = "| Camera | " + " | ".join(result["candidates"]) + " |"
    sep = "|---|" + "|".join(["---:"] * len(result["candidates"])) + "|"
    lines.append(header)
    lines.append(sep)
    # Sort cameras by make first, then by name.
    cam_names_sorted = sorted(
        result["per_camera"].keys(),
        key=lambda c: (profile_makes.get(c, "ZZZ"), c),
    )
    for cam in cam_names_sorted:
        row = [cam]
        for cand in result["candidates"]:
            row.append(f"{result['per_camera'][cam][cand]['mean']:.2f}")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    lines.append("## Per-make summary (mean of per-camera mean)")
    lines.append("")
    lines.append("| Make | n | " + " | ".join(result["candidates"]) + " |")
    lines.append("|---|---:|" + "|".join(["---:"] * len(result["candidates"])) + "|")
    by_make_data: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    by_make_count: dict[str, int] = defaultdict(int)
    for cam in result["per_camera"]:
        make = profile_makes.get(cam, "Unknown")
        by_make_count[make] += 1
        for cand in result["candidates"]:
            by_make_data[make][cand].append(result["per_camera"][cam][cand]["mean"])
    for make in sorted(by_make_data.keys()):
        n = by_make_count[make]
        row = [make, str(n)]
        for cand in result["candidates"]:
            vals = by_make_data[make][cand]
            row.append(f"{np.mean(vals):.2f}")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    out_path.write_text("\n".join(lines))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def parse_panel(paths: list[Path]) -> tuple[list[DCPProfile], list[str], dict[str, str]]:
    """Parse a list of DCP paths into (profiles, names, makes) tuples.

    Filters out parse failures (rare). Returns the original-order list
    excluding failures.
    """
    profiles: list[DCPProfile] = []
    names: list[str] = []
    makes: dict[str, str] = {}
    for path in paths:
        try:
            p = parse_dcp(path)
            profiles.append(p)
            short = path.stem.removesuffix(" Adobe Standard").removesuffix(" Adobe_Standard")
            names.append(short)
            makes[short] = short.split()[0]
        except Exception as e:  # noqa: BLE001
            print(f"  parse FAIL: {path.name}: {e}", file=sys.stderr)
    return profiles, names, makes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=Path("/tmp/a_prime_ceiling"),
                    help="Output basename — writes <out>.md and <out>.json")
    ap.add_argument("--panel-size", type=int, default=40,
                    help="Target number of cameras in the stratified evaluation panel")
    ap.add_argument("--construction-size", type=int, default=200,
                    help="Number of cameras used to BUILD A' (median + output-average). "
                         "Larger = more representative shared transform. Distinct from "
                         "the evaluation panel.")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--include-65cube", action="store_true",
                    help="Also build the 65³ output-average cube (slow; skip "
                         "unless the 33³ result motivates it)")
    args = ap.parse_args(argv)

    print(f"loading Adobe Standard catalog from {ADOBE_STD_DIR}")
    by_make = enumerate_dcps_by_make()
    total = sum(len(v) for v in by_make.values())
    print(f"catalog: {total} DCPs across {len(by_make)} makes")

    # CONSTRUCTION set: broader sample for building A' candidates. We use the
    # measure_dcp_variance.py-style stratified sample (cap 20/manufacturer)
    # to get full-catalog coverage, then subsample.
    construction_paths: list[Path] = []
    rng = random.Random(args.seed)
    for make, paths in sorted(by_make.items()):
        cap = min(len(paths), 20)
        construction_paths.extend(rng.sample(paths, cap) if len(paths) > cap else paths)
    if args.construction_size < len(construction_paths):
        construction_paths = rng.sample(construction_paths, args.construction_size)
    print(f"construction set: {len(construction_paths)} DCPs (full-catalog stratified)")

    # EVALUATION panel: smaller, weighted toward the load-bearing cameras.
    panel_paths = select_panel(by_make, args.panel_size, args.seed)
    print(f"evaluation panel: {len(panel_paths)} cameras "
          "(weighted: Apple/Samsung + timelapse-camera makes)")

    construction_profiles, _, _ = parse_panel(construction_paths)
    profiles, profile_names, profile_makes = parse_panel(panel_paths)
    print(f"parsed: construction={len(construction_profiles)}, panel={len(profiles)}")

    # Build A' candidates from the CONSTRUCTION set. Evaluation is against
    # the panel set. This separates "shared transform built from the broad
    # catalog" from "fidelity tested against representative cameras."
    #
    # Per-class candidates: ~30% of Adobe Standard ships NO HSM (legacy
    # bodies with universal-default LookTable). Their inclusion in the
    # construction set pulls median-HSV toward identity AND pulls
    # output-avg toward a milder transform. We also build "HSM-filtered"
    # variants from the 67% of cameras with HSM, capturing modern Adobe-
    # tuned look more sharply at the cost of worse fit on legacy bodies.
    construction_with_hsm = [p for p in construction_profiles if p.hue_sat_map is not None]
    print(f"  construction set with HSM: {len(construction_with_hsm)} / {len(construction_profiles)}")

    print("building A' candidates (from construction set)...")
    candidates: list[CandidateApprime] = [
        build_identity_candidate(),
        build_median_hsv_candidate(construction_profiles, use_hsm=True, use_look=True,
                                   name="median-HSV"),
        build_median_hsv_candidate(construction_with_hsm, use_hsm=True, use_look=True,
                                   name="median-HSV-modern"),
        build_median_hsv_candidate(construction_profiles, use_hsm=False, use_look=True,
                                   name="median-look-only"),
        build_median_hsv_candidate(construction_profiles, use_hsm=True, use_look=False,
                                   name="median-hsm-only"),
        build_output_average_cube_candidate(construction_profiles, 33, "output-avg-33"),
        build_output_average_cube_candidate(construction_with_hsm, 33, "output-avg-33-modern"),
    ]
    if args.include_65cube:
        candidates.append(
            build_output_average_cube_candidate(construction_profiles, 65, "output-avg-65"),
        )

    print("synthesizing reference patches...")
    patches_xyz, patch_names = synthesize_reference_patches()
    patches_prophoto = patches_to_prophoto(patches_xyz)
    print(f"patches: {len(patch_names)} (ColorChecker + rawtoaces RICD)")

    # Measurement.
    print("running per-camera × per-candidate ΔE2000 measurement...")
    result = measure(profiles, profile_names, patches_prophoto, patch_names, candidates)

    # Save outputs.
    args.out.parent.mkdir(parents=True, exist_ok=True)
    json_out = args.out.with_suffix(".json")
    md_out = args.out.with_suffix(".md")
    json_out.write_text(json.dumps(result, indent=2))
    write_markdown(result, profile_makes, md_out)
    print(f"\nresults written to:\n  {json_out}\n  {md_out}\n")

    # Print headline aggregate to stdout.
    print("=" * 70)
    print("Aggregate ΔE2000 across panel")
    print("=" * 70)
    print(f"{'Candidate':<25} {'mean':>6} {'P50':>6} {'P95':>6} {'P99':>6} {'max':>6}")
    for cand_name in result["candidates"]:
        a = result["aggregate"][cand_name]
        print(f"{cand_name:<25} {a['mean']:>6.2f} {a['P50']:>6.2f} "
              f"{a['P95']:>6.2f} {a['P99']:>6.2f} {a['max']:>6.2f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
