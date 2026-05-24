#!/usr/bin/env python3
"""Bulk-extract every Adobe `.dcp` profile on the system into lrt-cinema's `.npz` format.

Usage:
    python3 tools/extract_dcp_library.py [output_dir]

When `output_dir` is omitted, the extracted profiles are written to the
per-user lrt-cinema profile cache:

    macOS / Linux: ~/.config/lrt-cinema/profiles/
    Windows:       %APPDATA%/lrt-cinema/profiles/

(Or `$XDG_CONFIG_HOME/lrt-cinema/profiles/` when that env var is set.)
After extraction, `lrt-cinema render` will auto-detect the matching
profile for any RAW it processes — no Adobe DNG Converter needed at
render time. To build the sister `lrt-cinema-profiles` data repo
instead, pass that repo's root as the output_dir.

Walks the standard Adobe install locations:
  macOS:   /Library/Application Support/Adobe/CameraRaw/CameraProfiles/
           /Applications/Adobe Lightroom Classic/.../CameraProfiles/
  Windows: %ProgramData%/Adobe/CameraRaw/CameraProfiles/

For each .dcp found, writes a corresponding .npz under output_dir (flat
naming: `<Make> <Model> <variant>.npz` e.g. `Nikon D750 Camera
Standard.npz`). The matrix-tone-cube data is project-defined and
losslessly re-encodable; Adobe's `.dcp` file format and bundled
non-data metadata (UniqueCameraModel, ProfileCopyright, etc.) are NOT
preserved. See docs/research/KELVIN_MULTIPLIERS_RESEARCH.md for the
licensing context that motivates this format.

Skips any profile the parser can't read (typically because dt drops a
field we don't yet handle); logs a one-line warning per skip.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from lrt_cinema.dcp import _adobe_dcp_search_roots, parse_dcp, save_profile


def _default_output_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else (Path.home() / ".config")
    return base / "lrt-cinema" / "profiles"


def main(argv: list[str]) -> int:
    if len(argv) > 2 or (len(argv) == 2 and argv[1] in ("-h", "--help")):
        print(__doc__, file=sys.stderr)
        return 2
    out_dir = Path(argv[1]) if len(argv) == 2 else _default_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    roots = _adobe_dcp_search_roots()
    if not roots:
        print(
            "error: no Adobe DCP install paths found.\n"
            "Install Adobe DNG Converter (free) from "
            "https://helpx.adobe.com/camera-raw/digital-negative.html#dng-converter "
            "to populate the CameraProfiles directory.",
            file=sys.stderr,
        )
        return 1

    print(f"Output directory: {out_dir}")
    print("Scanning Adobe DCP install roots:")
    for r in roots:
        print(f"  {r}")
    print()

    total_in = 0
    total_out = 0
    n_ok = 0
    n_skip = 0
    seen_outputs: set[str] = set()
    for root in roots:
        for dcp_path in root.rglob("*.dcp"):
            try:
                profile = parse_dcp(dcp_path)
            except (ValueError, OSError) as exc:
                print(f"  SKIP {dcp_path.name}: {exc}", file=sys.stderr)
                n_skip += 1
                continue
            # Use the source filename (sans .dcp) as the output basename.
            # Adobe's filenames already encode the "<Make> <Model> <variant>"
            # convention lrt-cinema's auto-detect expects.
            out_name = dcp_path.stem + ".npz"
            if out_name in seen_outputs:
                # Same camera+variant in multiple Adobe roots (e.g. DNG Converter
                # and LR Classic ship duplicates). First write wins; skip dupes.
                continue
            seen_outputs.add(out_name)
            out_path = out_dir / out_name
            save_profile(profile, out_path)
            total_in += dcp_path.stat().st_size
            total_out += out_path.stat().st_size
            n_ok += 1

    print()
    print(f"Extracted: {n_ok} profiles")
    if n_skip:
        print(f"Skipped:   {n_skip} (parse failures — typically older / variant DCPs)")
    if total_in:
        print(
            f"Total: {total_in / (1024*1024):.1f} MB in → "
            f"{total_out / (1024*1024):.1f} MB out "
            f"({total_out / total_in * 100:.0f}% of source)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
