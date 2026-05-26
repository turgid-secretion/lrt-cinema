#!/usr/bin/env python3
"""Measure Adobe DCP catalog variance to inform A' viability (Q1 from 08_search_framing.md).

Hypothesis: a meaningful fraction of "Adobe color" is *not* per-camera-specific
— the per-camera variance in HueSatMap / LookTable / ProfileToneCurve /
BaselineExposure is small enough that a single shared transform captures most
of the perceptual character.

Methodology:
    1. Enumerate Adobe Standard DCPs in /Library/Application Support/Adobe/
       CameraRaw/CameraProfiles/Adobe Standard/.
    2. Sample N cameras (random or stratified by manufacturer).
    3. For each DCP, parse via src/lrt_cinema/dcp.py.
    4. Compute cross-camera variance metrics on:
        - BaselineExposure / BaselineExposureOffset (scalars)
        - ProfileToneCurve (resampled to common N nodes)
        - HueSatMap (binned by cube dimensions; per-dimension cross-camera
          ΔE-equivalent on the (hue_shift, sat_scale, val_scale) channels)
        - LookTable (same)
    5. Output: distribution stats + classification thresholds.

Thresholds (from 08_search_framing.md):
    - Low variance (mean cross-camera ΔE < ~2): A' viable
    - Medium (~2–5): A' partially viable (per-camera-family)
    - High (> ~5): A' not viable; per-camera essential

Output: text report on stdout + JSON details to argv[1] if given.
"""

from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

# Add src/ to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lrt_cinema.dcp import DCPProfile, parse_dcp  # noqa: E402

ADOBE_STD_DIR = Path("/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Adobe Standard")

# Sampling: limit to N cameras for speed + clarity. Stratified by manufacturer.
SAMPLE_PER_MAKE_CAP = 20  # cameras per manufacturer
TONE_CURVE_RESAMPLE_N = 32  # nodes


def enumerate_dcps() -> dict[str, list[Path]]:
    """Map manufacturer → list of DCP paths."""
    by_make: dict[str, list[Path]] = defaultdict(list)
    if not ADOBE_STD_DIR.is_dir():
        sys.exit(f"error: {ADOBE_STD_DIR} not found")
    for dcp in sorted(ADOBE_STD_DIR.glob("*.dcp")):
        # Filename format: "{Make} {Model} Adobe Standard.dcp"
        # Manufacturer is everything before the first space.
        name = dcp.name.removesuffix(" Adobe Standard.dcp")
        parts = name.split(" ", 1)
        make = parts[0]
        by_make[make].append(dcp)
    return by_make


def stratified_sample(by_make: dict[str, list[Path]], cap: int, seed: int = 0) -> list[Path]:
    """Sample up to `cap` DCPs per manufacturer."""
    rng = random.Random(seed)
    sampled: list[Path] = []
    for make, paths in sorted(by_make.items()):
        if len(paths) <= cap:
            sampled.extend(paths)
        else:
            sampled.extend(rng.sample(paths, cap))
    return sampled


def resample_tone_curve(curve: np.ndarray | None, n: int) -> np.ndarray | None:
    """Resample a tone curve to N x-positions, returning the y-values."""
    if curve is None or len(curve) < 2:
        return None
    xs = curve[:, 0]
    ys = curve[:, 1]
    # Resample at uniform 0..1 x.
    target_x = np.linspace(0, 1, n)
    return np.interp(target_x, xs, ys)


def cube_signature(profile: DCPProfile, kind: str) -> tuple[tuple[int, int, int], np.ndarray] | None:
    """Get (dimensions, flat data) for HSM or LookTable. None if missing."""
    cube = profile.hue_sat_map if kind == "hsm" else profile.look_table
    if cube is None:
        return None
    return ((cube.hue_divisions, cube.sat_divisions, cube.val_divisions), cube.data_1)


def measure_baseline_exposure_variance(profiles: list[DCPProfile]) -> dict:
    bes = np.array([p.baseline_exposure for p in profiles])
    beos = np.array([p.baseline_exposure_offset for p in profiles])
    return {
        "n": len(profiles),
        "baseline_exposure": {
            "min": float(bes.min()), "max": float(bes.max()),
            "mean": float(bes.mean()), "std": float(bes.std()),
            "P5": float(np.percentile(bes, 5)), "P50": float(np.percentile(bes, 50)),
            "P95": float(np.percentile(bes, 95)),
        },
        "baseline_exposure_offset": {
            "min": float(beos.min()), "max": float(beos.max()),
            "mean": float(beos.mean()), "std": float(beos.std()),
        },
    }


def measure_tone_curve_variance(profiles: list[DCPProfile], n: int) -> dict:
    curves = []
    for p in profiles:
        rs = resample_tone_curve(p.profile_tone_curve, n)
        if rs is not None:
            curves.append(rs)
    if not curves:
        return {"n": 0, "note": "no ProfileToneCurve in any sampled DCP"}
    M = np.stack(curves)  # (n_cameras, n)
    mean_curve = M.mean(axis=0)
    rmse_per_camera = np.sqrt(((M - mean_curve[None, :]) ** 2).mean(axis=1))
    return {
        "n_curves": len(curves),
        "n_missing": len(profiles) - len(curves),
        "mean_curve_rmse_around_mean": {
            "min": float(rmse_per_camera.min()),
            "max": float(rmse_per_camera.max()),
            "mean": float(rmse_per_camera.mean()),
            "P50": float(np.percentile(rmse_per_camera, 50)),
            "P95": float(np.percentile(rmse_per_camera, 95)),
        },
        # An RMSE of 0.01 in [0,1] tone-curve space is ~2.5 ΔL* on a mid-gray pixel; very small.
        # RMSE of 0.05 is ~12 ΔL*; visibly different curves.
    }


def measure_cube_variance(profiles: list[DCPProfile], kind: str) -> dict:
    """Group profiles by cube dimensions, then compute cross-camera variance
    on the data array per group.

    Cubes encode (hue_shift_degrees, sat_scale, val_scale) per cell. We
    compute the per-cell standard deviation across cameras to get a sense
    of how much the cube varies.
    """
    by_dim: dict[tuple[int, int, int], list[np.ndarray]] = defaultdict(list)
    n_missing = 0
    for p in profiles:
        sig = cube_signature(p, kind)
        if sig is None:
            n_missing += 1
            continue
        dims, data = sig
        by_dim[dims].append(data)

    out: dict = {"kind": kind, "n_total": len(profiles), "n_missing": n_missing, "groups": {}}
    for dims, datas in sorted(by_dim.items(), key=lambda kv: -len(kv[1])):
        if len(datas) < 2:
            out["groups"][str(dims)] = {"n_cameras": len(datas), "note": "single-camera group"}
            continue
        M = np.stack(datas)  # (n_cameras, n_cells*3)
        # Reshape to per-channel for interpretable stats.
        # Each cell has 3 channels: hue_shift_deg, sat_scale, val_scale.
        try:
            M3 = M.reshape(len(datas), -1, 3)
        except ValueError:
            out["groups"][str(dims)] = {"n_cameras": len(datas), "note": "data shape mismatch"}
            continue
        # Cross-camera std per cell per channel.
        per_cell_std = M3.std(axis=0)  # (n_cells, 3)
        out["groups"][str(dims)] = {
            "n_cameras": len(datas),
            "hue_shift_deg_std": {
                "mean": float(per_cell_std[:, 0].mean()),
                "P50": float(np.percentile(per_cell_std[:, 0], 50)),
                "P95": float(np.percentile(per_cell_std[:, 0], 95)),
                "max": float(per_cell_std[:, 0].max()),
            },
            "sat_scale_std": {
                "mean": float(per_cell_std[:, 1].mean()),
                "P50": float(np.percentile(per_cell_std[:, 1], 50)),
                "P95": float(np.percentile(per_cell_std[:, 1], 95)),
                "max": float(per_cell_std[:, 1].max()),
            },
            "val_scale_std": {
                "mean": float(per_cell_std[:, 2].mean()),
                "P50": float(np.percentile(per_cell_std[:, 2], 50)),
                "P95": float(np.percentile(per_cell_std[:, 2], 95)),
                "max": float(per_cell_std[:, 2].max()),
            },
        }
    return out


def main() -> int:
    by_make = enumerate_dcps()
    print(f"Adobe Standard DCP catalog: {sum(len(v) for v in by_make.values())} profiles across "
          f"{len(by_make)} manufacturers")
    for make, paths in sorted(by_make.items(), key=lambda kv: -len(kv[1])):
        print(f"  {make:30s} {len(paths):4d}")
    print()

    sampled = stratified_sample(by_make, cap=SAMPLE_PER_MAKE_CAP)
    print(f"Stratified sample: {len(sampled)} DCPs (cap {SAMPLE_PER_MAKE_CAP} per manufacturer)")
    print()

    profiles: list[DCPProfile] = []
    n_fail = 0
    for path in sampled:
        try:
            profiles.append(parse_dcp(path))
        except Exception as e:  # noqa: BLE001
            n_fail += 1
            print(f"  PARSE FAIL: {path.name}: {type(e).__name__}: {e}", file=sys.stderr)
    print(f"Parsed {len(profiles)} DCPs successfully ({n_fail} failures)")
    print()

    print("=" * 70)
    print("Baseline exposure variance")
    print("=" * 70)
    be = measure_baseline_exposure_variance(profiles)
    print(json.dumps(be, indent=2))
    print()

    print("=" * 70)
    print(f"ProfileToneCurve variance (resampled to {TONE_CURVE_RESAMPLE_N} nodes)")
    print("=" * 70)
    tc = measure_tone_curve_variance(profiles, TONE_CURVE_RESAMPLE_N)
    print(json.dumps(tc, indent=2))
    print()

    print("=" * 70)
    print("HueSatMap variance (grouped by cube dimensions)")
    print("=" * 70)
    hsm = measure_cube_variance(profiles, "hsm")
    print(json.dumps(hsm, indent=2))
    print()

    print("=" * 70)
    print("LookTable variance (grouped by cube dimensions)")
    print("=" * 70)
    lt = measure_cube_variance(profiles, "look_table")
    print(json.dumps(lt, indent=2))
    print()

    # Optional: write full JSON output to argv[1]
    if len(sys.argv) > 1:
        result = {
            "n_sampled": len(sampled),
            "n_parsed": len(profiles),
            "n_failed": n_fail,
            "baseline_exposure": be,
            "tone_curve": tc,
            "hsm": hsm,
            "look_table": lt,
        }
        Path(sys.argv[1]).write_text(json.dumps(result, indent=2))
        print(f"Full JSON written to {sys.argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
