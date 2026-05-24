#!/usr/bin/env python3
"""Extract one Adobe `.dcp` profile into lrt-cinema's lossless `.npz` format.

Usage:
    python3 tools/extract_dcp.py <input.dcp> [output.npz]

When `output.npz` is omitted, the output is written next to the input
with the `.dcp` extension swapped for `.npz`. Re-running on an existing
output is safe; the file is overwritten.

Adobe DCP files have a license that doesn't grant redistribution rights
(see docs/research/KELVIN_MULTIPLIERS_RESEARCH.md), so lrt-cinema's
in-repo strategy is to extract the *data* the renderer consumes — color
matrices, baseline exposure, profile tone curve, HueSatMap / LookTable
cubes — into a project-defined `.npz` format. Users run this extractor
once against an Adobe DCP install they already have (e.g.
`/Library/Application Support/Adobe/CameraRaw/CameraProfiles/` on macOS
after installing Adobe DNG Converter), and lrt-cinema's auto-detect
picks up the result.

For bulk extraction across an entire Adobe DCP install, use
`tools/extract_dcp_library.py` instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

from lrt_cinema.dcp import load_profile, parse_dcp, save_profile


def main(argv: list[str]) -> int:
    if len(argv) < 2 or len(argv) > 3 or argv[1] in ("-h", "--help"):
        print(__doc__, file=sys.stderr)
        return 2
    in_path = Path(argv[1])
    if not in_path.is_file():
        print(f"error: {in_path} not found", file=sys.stderr)
        return 2
    out_path = Path(argv[2]) if len(argv) == 3 else in_path.with_suffix(".npz")

    try:
        profile = parse_dcp(in_path)
    except (ValueError, OSError) as exc:
        print(f"error: parse {in_path}: {exc}", file=sys.stderr)
        return 1

    save_profile(profile, out_path)

    # Round-trip verify — bail loudly if save/load doesn't preserve the data.
    # Cheap insurance; catches format drift before users see it.
    reload = load_profile(out_path)
    if reload.profile_name != profile.profile_name:
        print("error: round-trip verification failed (profile_name mismatch)",
              file=sys.stderr)
        return 3

    in_kb = in_path.stat().st_size / 1024
    out_kb = out_path.stat().st_size / 1024
    print(
        f"{in_path.name} ({in_kb:.1f} KB) → {out_path.name} ({out_kb:.1f} KB, "
        f"{out_kb / in_kb * 100:.0f}% of original)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
