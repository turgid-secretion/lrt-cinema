#!/usr/bin/env python3
"""Bulk-extract `.dcp` camera profiles into lrt-cinema's `.npz` format.

Usage:
    python3 tools/extract_dcp_library.py <source_root> [output_dir]

`<source_root>` (required) is the directory scanned **recursively** for
`.dcp` files. lrt-cinema is Adobe-free at runtime, so there is no hardcoded
Adobe install path — point this at whichever DCP source you have, e.g.:

  - a user's Adobe CameraRaw profiles dir (if Adobe is installed), e.g.
    macOS:   /Library/Application Support/Adobe/CameraRaw/CameraProfiles
    Windows: %ProgramData%/Adobe/CameraRaw/CameraProfiles
  - a directory of profiles built with dcamprof or exported by RawTherapee
  - any folder of `.dcp` files you are licensed to use

When `output_dir` is omitted, the extracted profiles are written to the
per-user lrt-cinema profile cache:

    macOS / Linux: ~/.config/lrt-cinema/profiles/
    Windows:       %APPDATA%/lrt-cinema/profiles/

(Or `$XDG_CONFIG_HOME/lrt-cinema/profiles/` when that env var is set.)
After extraction, `lrt-cinema render` auto-detects the matching profile for
any RAW it processes — no Adobe install needed at render time. To build the
sister `lrt-cinema-profiles` data repo instead, pass that repo's root as
`output_dir`.

For each `.dcp` found, writes a corresponding `.npz` under `output_dir` (flat
naming: `<Make> <Model> <variant>.npz`, e.g. `Nikon D750 Camera
Standard.npz`). The matrix/tone/cube data is project-defined and losslessly
re-encodable; the source `.dcp` file format and bundled non-data metadata
(UniqueCameraModel, ProfileCopyright, etc.) are NOT preserved. See
docs/research/KELVIN_MULTIPLIERS_RESEARCH.md for the licensing context that
motivates storing extracted *data* rather than redistributing `.dcp` files.

Skips any profile the parser can't read (typically because a field we don't
yet handle is present); logs a one-line warning per skip.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from lrt_cinema.dcp import parse_dcp, save_profile

_USAGE = (
    "usage: python3 tools/extract_dcp_library.py <source_root> [output_dir]\n"
    "  <source_root>  directory scanned recursively for .dcp files\n"
    "  [output_dir]   destination for .npz files "
    "(default: per-user lrt-cinema profile cache)"
)


def _default_output_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else (Path.home() / ".config")
    return base / "lrt-cinema" / "profiles"


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] in ("-h", "--help"):
        print(__doc__, file=sys.stderr)
        return 0
    if len(argv) < 2 or len(argv) > 3:
        print(_USAGE, file=sys.stderr)
        return 2

    source_root = Path(argv[1])
    if not source_root.is_dir():
        print(
            f"error: source_root is not a directory: {source_root}\n{_USAGE}",
            file=sys.stderr,
        )
        return 1

    out_dir = Path(argv[2]) if len(argv) == 3 else _default_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Source root:      {source_root}")
    print(f"Output directory: {out_dir}")
    print()

    total_in = 0
    total_out = 0
    n_ok = 0
    n_skip = 0
    seen_outputs: set[str] = set()
    for dcp_path in source_root.rglob("*.dcp"):
        try:
            profile = parse_dcp(dcp_path)
        except (ValueError, OSError) as exc:
            print(f"  SKIP {dcp_path.name}: {exc}", file=sys.stderr)
            n_skip += 1
            continue
        # Use the source filename (sans .dcp) as the output basename. DCP
        # filenames already encode the "<Make> <Model> <variant>" convention
        # lrt-cinema's auto-detect expects.
        out_name = dcp_path.stem + ".npz"
        if out_name in seen_outputs:
            # Same camera+variant found twice under the source tree (e.g. a
            # converter and an editor ship duplicates). First write wins.
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
    elif n_ok == 0 and n_skip == 0:
        print(f"No .dcp files found under {source_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
