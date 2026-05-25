#!/usr/bin/env python3
"""Fit and store a per-camera channelmixer correction matrix.

The algorithmic engine (`lrt-cinema render --engine algorithmic`) renders
without DCP-derived modules. A per-camera calibration matrix closes the
color-rendition gap vs the DCP-driven engine. This tool fits and stores
that matrix.

Three tiers (resolution priority: Tier 2 → Tier 1 → Tier 3):

  Tier 2 — DCP distillation (preferred when an Adobe DCP exists)
    Synthesizes a 24-patch ColorChecker DNG, renders through both
    --engine algorithmic and --engine dcp via dt-cli, samples patches,
    fits the 3×3 channelmixer that transforms algorithmic → DCP output.
    Deterministic. Implemented.

  Tier 1 — SSF synthesis (fallback when no DCP, camera SSF available)
    Pure spectral integration via colour-science.
    NOT YET IMPLEMENTED (Phase 2c).

  Tier 3 — Physical chart (last resort)
    User-supplied ColorChecker RAW. Less reliable.
    NOT YET IMPLEMENTED (Phase 2d).

Explicit-matrix mode (--matrix) is the Phase 2a stub for one-off
experiments; it bypasses tier-specific fitting and stores the user-
supplied 3×3 directly.

Usage:

    # Tier 2: auto-detect camera from RAW, find DCP, fit matrix.
    python3 tools/calibrate_camera.py --raw <some.NEF> --fit-tier 2

    # Tier 2: explicit camera label (must have a DCP installed).
    python3 tools/calibrate_camera.py --camera "Nikon D750" --fit-tier 2

    # Tier 2: bundled .npz oracle (tests / first-run on the dev camera)
    python3 tools/calibrate_camera.py --camera "Nikon D750" --fit-tier 2 \\
        --oracle-dcp tests/fixtures/dcp_data/"Nikon D750 Camera Standard.npz"

    # Explicit matrix (Phase 2a behavior):
    python3 tools/calibrate_camera.py --camera "Nikon D750" \\
        --matrix "1.05,-0.02,-0.01, -0.01,1.04,-0.03, -0.02,-0.01,1.10"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from lrt_cinema.calibration import (
    Calibration,
    _default_user_calibration_dir,
    fit_tier2_via_dt_cli_roundtrip,
    save_calibration,
)
from lrt_cinema.dcp import (
    _adobe_camera_label,
    adobe_make_for_camera,
    auto_detect_profile,
    find_dcp_for_camera,
    find_extracted_profile_for_camera,
    load_profile,
    parse_dcp,
    read_raw_make_model,
)


def _parse_matrix_csv(text: str) -> np.ndarray:
    tokens = [t.strip() for t in text.replace("\n", ",").split(",") if t.strip()]
    if len(tokens) != 9:
        raise ValueError(
            f"--matrix expects 9 comma-separated floats (3x3 row-major); "
            f"got {len(tokens)}"
        )
    try:
        floats = [float(t) for t in tokens]
    except ValueError as exc:
        raise ValueError(f"--matrix unparseable: {exc}") from exc
    return np.array(floats, dtype=np.float32).reshape(3, 3)


def _resolve_camera_label(args: argparse.Namespace) -> tuple[str, tuple[str, str] | None]:
    """Returns (label, raw_make_model_or_None)."""
    if args.camera is not None:
        return args.camera.strip(), None
    info = read_raw_make_model(args.raw)
    if info is None:
        raise SystemExit(
            f"error: cannot read EXIF Make/Model from {args.raw}. "
            f"Pass --camera explicitly or supply a TIFF-shaped RAW "
            f"(NEF/DNG/ARW/...).",
        )
    return _adobe_camera_label(*info), info


def _resolve_oracle_dcp(args: argparse.Namespace, label: str, raw_info: tuple[str, str] | None):
    """Find a DCPProfile for the camera. Order:
        1. --oracle-dcp explicit path (Adobe .dcp or .npz)
        2. From RAW if --raw was given (auto_detect_profile)
        3. find_dcp_for_camera / find_extracted_profile_for_camera by label
    """
    if args.oracle_dcp is not None:
        path = args.oracle_dcp
        if path.suffix.lower() == ".npz":
            return load_profile(path), path
        return parse_dcp(path), path
    if args.raw is not None:
        result = auto_detect_profile(args.raw)
        if result is not None:
            return result
    if raw_info is None:
        make = adobe_make_for_camera(label.split()[0] if " " in label else label)
        model = " ".join(label.split()[1:]) if " " in label else label
    else:
        make, model = raw_info
    extracted = find_extracted_profile_for_camera(make, model)
    if extracted is not None:
        return load_profile(extracted), extracted
    dcp_path = find_dcp_for_camera(make, model)
    if dcp_path is not None:
        return parse_dcp(dcp_path), dcp_path
    raise SystemExit(
        f"error: no DCP found for camera {label!r}. "
        f"Tier 2 requires an oracle DCP. Either:\n"
        f"  * run tools/extract_dcp_library.py against an Adobe DNG Converter\n"
        f"    install to populate ~/.config/lrt-cinema/profiles/, or\n"
        f"  * pass --oracle-dcp <path> explicitly."
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="calibrate_camera",
        description=(
            "Fit and store a per-camera channelmixer correction matrix "
            "for the lrt-cinema algorithmic engine. Tier 2 (DCP "
            "distillation) is implemented. Tier 1 (SSF) and Tier 3 "
            "(physical chart) land in subsequent PRs."
        ),
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--raw", type=Path,
        help="Path to a RAW file from the camera. EXIF Make/Model is "
             "read to derive the Adobe-style camera label.",
    )
    target.add_argument(
        "--camera",
        help="Explicit Adobe-style camera label (e.g. 'Nikon D750').",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--fit-tier", type=int, choices=[2],
        help="Run tier-specific automatic fitting. Currently only "
             "Tier 2 (DCP distillation) is supported. Requires a DCP "
             "for the camera (auto-detected from --raw, or via --oracle-dcp).",
    )
    mode.add_argument(
        "--matrix",
        help="Explicit 3×3 channelmixer matrix as 9 comma-separated floats "
             "in row-major order. Bypasses tier-specific fitting; the "
             "user is responsible for the correctness of the matrix. "
             "Recorded with tier=0 (explicit).",
    )
    parser.add_argument(
        "--oracle-dcp", type=Path, default=None,
        help="Explicit path to the DCP used as the oracle in Tier 2 "
             "fitting. Accepts Adobe .dcp or lrt-cinema .npz. When omitted, "
             "auto-detected via the standard DCP search paths.",
    )
    parser.add_argument(
        "--illuminant", default="D55", choices=["D50", "D55", "D65", "D75", "A", "E"],
        help="Spectral illuminant used to synthesize patch XYZ values "
             "for Tier 2 fitting. D55 (default) is the typical daylight "
             "WB users shoot under and matches Adobe's ColorChecker D55 "
             "reference. D50 / D65 alternatives for studio / overcast work.",
    )
    parser.add_argument(
        "--source",
        help="Provenance string recorded in the .npz. Defaults to a "
             "tier-appropriate auto-generated string.",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Output directory. Default: per-user XDG calibration dir.",
    )

    args = parser.parse_args(argv)

    # Resolve camera label + (optionally) raw EXIF info for downstream
    # DCP resolution.
    try:
        camera_label, raw_info = _resolve_camera_label(args)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2

    # Fit or accept the matrix.
    if args.fit_tier == 2:
        try:
            dcp_profile, dcp_source = _resolve_oracle_dcp(args, camera_label, raw_info)
        except SystemExit as exc:
            print(exc, file=sys.stderr)
            return 2
        print(f"Tier 2: distilling matrix from DCP at {dcp_source}", file=sys.stderr)
        print(f"        camera label: {camera_label}", file=sys.stderr)
        print(f"        illuminant:   {args.illuminant}", file=sys.stderr)
        if raw_info is not None:
            make, model = raw_info
        else:
            make = adobe_make_for_camera(camera_label.split()[0])
            model = " ".join(camera_label.split()[1:])
        try:
            result = fit_tier2_via_dt_cli_roundtrip(
                dcp_profile,
                camera_make=make,
                camera_model=model,
                unique_camera_model=camera_label,
                illuminant=args.illuminant,
            )
        except RuntimeError as exc:
            print(f"error: Tier 2 fit failed: {exc}", file=sys.stderr)
            return 3
        matrix = result.matrix
        tier = 2
        source = args.source or (
            f"Tier 2 dt-cli round-trip vs {dcp_source.name} @ {args.illuminant}"
        )
        print(
            f"        post-fit ΔE2000 mean = {result.delta_e2000_mean:.3f}",
            file=sys.stderr,
        )
        print(
            f"        post-fit ΔE2000 max  = {result.delta_e2000_max:.3f}",
            file=sys.stderr,
        )
        de_mean = result.delta_e2000_mean
        de_max = result.delta_e2000_max
    else:
        # Explicit --matrix path
        try:
            matrix = _parse_matrix_csv(args.matrix)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        tier = 0
        source = args.source or "explicit user-supplied"
        de_mean = 0.0
        de_max = 0.0

    calibration = Calibration(
        camera_label=camera_label,
        matrix=matrix,
        tier=tier,
        source=source,
        delta_e2000_mean=de_mean,
        delta_e2000_max=de_max,
    )

    out_dir = args.output if args.output is not None else _default_user_calibration_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{camera_label}.npz"
    save_calibration(calibration, out_path)

    print(f"wrote: {out_path}")
    print(f"  camera_label: {camera_label}")
    print(f"  tier:         {tier} ({_tier_name(tier)})")
    print(f"  source:       {source}")
    if de_mean > 0:
        print(f"  ΔE2000:       mean={de_mean:.3f} max={de_max:.3f}")
    print("  matrix:")
    for row in matrix:
        print(f"    [{row[0]:+.4f}, {row[1]:+.4f}, {row[2]:+.4f}]")
    return 0


def _tier_name(tier: int) -> str:
    return {
        0: "explicit user-supplied",
        1: "SSF synthesis (Phase 2c, not yet wired)",
        2: "DCP distillation (dt-cli round-trip)",
        3: "physical chart (Phase 2d, not yet wired)",
    }.get(tier, "unknown")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
