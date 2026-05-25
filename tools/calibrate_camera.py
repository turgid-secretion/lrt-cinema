#!/usr/bin/env python3
"""Fit and store a per-camera channelmixer correction matrix.

The algorithmic engine (`lrt-cinema render --engine algorithmic`) renders
without DCP-derived modules. A per-camera calibration matrix closes the
color-rendition gap vs the DCP-driven engine. This tool fits and stores
that matrix.

Three tiers, auto-detected (Phase 2a: only --matrix stub + storage are
shipped; the tier-specific fitting math lands in a follow-up PR):

  Tier 2 — DCP-as-oracle distillation (preferred when an Adobe DCP exists)
    Synthesizes a 24-patch ColorChecker; renders through both the DCP
    pipeline (oracle) and the algorithmic pipeline (the engine being
    calibrated); fits the 3×3 channelmixerrgb that transforms
    algorithmic → DCP-rendered. NOT YET IMPLEMENTED (Phase 2b).

  Tier 1 — SSF-based synthetic calibration (fallback when no DCP)
    Looks up the camera in colour.MSDS_CAMERA_SENSITIVITIES; computes
    predicted patch RGB via spectral integration; fits a 3×3 from
    predicted_rgb to target XYZ. NOT YET IMPLEMENTED (Phase 2c).

  Tier 3 — Physical chart (last resort)
    User supplies a ColorChecker RAW shot; we render through both
    engines and fit. Less reliable (shot-specific confounders).
    NOT YET IMPLEMENTED (Phase 2d).

Today (Phase 2a) the tool accepts an explicit 3×3 matrix via --matrix
(9 floats) and stores it under the camera label derived from --raw or
--camera. Use this for one-off experiments with hand-fitted matrices
while the automated fitters are under development.

Usage:
    # Explicit matrix from a RAW for camera-label auto-detection:
    python3 tools/calibrate_camera.py \\
        --raw <some.NEF> \\
        --matrix "1.05,-0.02,-0.01, -0.01,1.04,-0.03, -0.02,-0.01,1.10"

    # Explicit camera label (skips RAW probe):
    python3 tools/calibrate_camera.py \\
        --camera "Nikon D750" \\
        --matrix "..." \\
        --tier 0

    # Output dir defaults to ~/.config/lrt-cinema/calibration/ (XDG).
    # Override with --output for a custom location.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from lrt_cinema.calibration import (
    Calibration,
    _default_user_calibration_dir,
    save_calibration,
)
from lrt_cinema.dcp import _adobe_camera_label, read_raw_make_model


def _parse_matrix_csv(text: str) -> np.ndarray:
    """Parse a 9-value comma-separated matrix string into a (3, 3) np array.

    Whitespace inside the string is allowed; values are floats in row-major
    order. Raises ValueError on wrong count or unparsable tokens.
    """
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


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="calibrate_camera",
        description=(
            "Fit and store a per-camera channelmixer correction matrix "
            "for the lrt-cinema algorithmic engine. Phase 2a ships explicit-"
            "matrix storage only; tier-specific fitting math (Tier 2 / 1 / 3) "
            "lands in subsequent PRs."
        ),
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--raw", type=Path,
        help="Path to a RAW file from the camera. EXIF Make/Model is "
             "read to derive the Adobe-style camera label "
             "(see dcp._adobe_camera_label).",
    )
    target.add_argument(
        "--camera",
        help="Explicit Adobe-style camera label (e.g. 'Nikon D750'). "
             "Skips the RAW EXIF probe. Useful when no RAW is on-hand "
             "but the camera label is known.",
    )
    parser.add_argument(
        "--matrix", required=True,
        help="3x3 channelmixer correction matrix as 9 comma-separated "
             "floats in row-major order. Identity = '1,0,0,0,1,0,0,0,1'.",
    )
    parser.add_argument(
        "--tier", type=int, default=0, choices=[0, 1, 2, 3],
        help="Which tier produced the matrix. 0 (default) = explicit "
             "user-supplied; 1 = SSF synthesis; 2 = DCP distillation; "
             "3 = physical chart. Recorded in the .npz for audit.",
    )
    parser.add_argument(
        "--source", default="explicit (Phase 2a stub)",
        help="Provenance string recorded in the .npz (e.g. the DCP path "
             "for Tier 2, the SSF dataset name for Tier 1, the chart RAW "
             "path for Tier 3). Defaults to 'explicit'.",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Output directory. Default: per-user XDG calibration dir "
             "(~/.config/lrt-cinema/calibration/ on Linux/macOS, "
             "%%APPDATA%%/lrt-cinema/calibration/ on Windows).",
    )

    args = parser.parse_args(argv)

    # Resolve camera label.
    if args.camera is not None:
        camera_label = args.camera.strip()
    else:
        info = read_raw_make_model(args.raw)
        if info is None:
            print(
                f"error: cannot read EXIF Make/Model from {args.raw}. "
                f"Pass --camera explicitly or supply a TIFF-shaped RAW "
                f"(NEF/DNG/ARW/...).",
                file=sys.stderr,
            )
            return 2
        make, model = info
        camera_label = _adobe_camera_label(make, model)

    # Parse the matrix.
    try:
        matrix = _parse_matrix_csv(args.matrix)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    calibration = Calibration(
        camera_label=camera_label,
        matrix=matrix,
        tier=int(args.tier),
        source=args.source,
    )

    out_dir = args.output if args.output is not None else _default_user_calibration_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{camera_label}.npz"
    save_calibration(calibration, out_path)

    print(f"wrote: {out_path}")
    print(f"  camera_label: {camera_label}")
    print(f"  tier:         {args.tier} ({_tier_name(args.tier)})")
    print(f"  source:       {args.source}")
    print("  matrix:")
    for row in matrix:
        print(f"    [{row[0]:+.4f}, {row[1]:+.4f}, {row[2]:+.4f}]")
    return 0


def _tier_name(tier: int) -> str:
    return {
        0: "explicit user-supplied",
        1: "SSF synthesis (Phase 2c, not yet wired)",
        2: "DCP distillation (Phase 2b, not yet wired)",
        3: "physical chart (Phase 2d, not yet wired)",
    }.get(tier, "unknown")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
