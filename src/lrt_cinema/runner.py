"""Drive `darktable-cli` per frame.

Single-worker scheduler for v0.1. The N-parallel worker pool noted in
SCOPE.md is a straightforward `concurrent.futures.ProcessPoolExecutor`
wrap of `render_frame()` once we have real benchmark data to size the
pool against.

This module is intentionally thin: it composes XMP-emitter +
preset-definitions + a subprocess call. Anything that requires real
darktable I/O is gated behind `dry_run` so the test suite can exercise
the orchestration without darktable installed.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from lrt_cinema.ir import DevelopOps
from lrt_cinema.presets import Preset
from lrt_cinema.xmp_emitter import emit_darktable_xmp


class DarktableCliNotFound(RuntimeError):
    """Raised when `darktable-cli` is not on PATH and dry_run is False."""


@dataclass
class FrameResult:
    frame_index: int
    source_path: Path
    output_path: Path
    returncode: int
    skipped: bool = False
    error: str | None = None


def darktable_cli_path() -> str | None:
    """Return the resolved path to `darktable-cli`, or None if not found."""
    return shutil.which("darktable-cli")


DEFAULT_PER_FRAME_TIMEOUT_S = 600


def _refuse_dash_prefix(path: Path, label: str) -> None:
    """Reject paths whose basename starts with '-' so darktable-cli does not parse them as flags."""
    if path.name.startswith("-"):
        raise ValueError(
            f"{label} basename begins with '-': {path}. darktable-cli would parse this as a flag."
        )


def render_frame(
    frame_index: int,
    source_path: Path,
    output_dir: Path,
    ops: DevelopOps,
    preset: Preset,
    bundled_style_dir: Path | None = None,
    custom_style: Path | None = None,
    dry_run: bool = False,
    timeout_s: float | None = DEFAULT_PER_FRAME_TIMEOUT_S,
) -> FrameResult:
    """Render one frame.

    Steps:
      1. Emit a per-frame darktable XMP sidecar into output_dir
         (NOT next to the source — that file is the user's LRT XMP).
      2. Build the `darktable-cli` argv from the preset.
      3. Run darktable-cli (or skip if dry_run).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    source_path = source_path.resolve()
    output_dir = output_dir.resolve()
    _refuse_dash_prefix(source_path, "source RAW")

    output_path = output_dir / f"{source_path.stem}{preset.output_extension}"
    xmp_path = output_dir / f"{source_path.stem}{source_path.suffix}.dt.xmp"
    emit_darktable_xmp(ops, xmp_path)

    style_path: Path | None = None
    if custom_style is not None:
        style_path = custom_style.resolve()
    elif bundled_style_dir is not None and preset.style_filename:
        candidate = (bundled_style_dir / preset.style_filename).resolve()
        if candidate.exists():
            style_path = candidate

    # NOTE: darktable-cli's --bpp flag is documented "unsupported" in dt 5.x
    # (`darktable-cli --help` lists it that way). The TIFF bit depth and EXR
    # encoding are governed by darktable's export module config, which the
    # bundled `.style` files will pin once calibration ships. For v0.2 we
    # accept darktable's defaults (typically 16-bit TIFF, half-float EXR).
    argv = [
        "darktable-cli",
        str(source_path),
        str(xmp_path),
        str(output_path),
    ]
    if style_path is not None:
        argv += ["--style", str(style_path), "--style-overwrite"]

    if dry_run:
        return FrameResult(
            frame_index=frame_index,
            source_path=source_path,
            output_path=output_path,
            returncode=0,
            skipped=True,
        )

    if darktable_cli_path() is None:
        raise DarktableCliNotFound(
            "darktable-cli not on PATH. Install darktable: "
            "macOS: `brew install --cask darktable` ; "
            "Debian/Ubuntu: `sudo apt install darktable` ; "
            "Fedora: `sudo dnf install darktable` ; "
            "Arch: `sudo pacman -S darktable`"
        )

    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, check=False, timeout=timeout_s,
        )
        return FrameResult(
            frame_index=frame_index,
            source_path=source_path,
            output_path=output_path,
            returncode=proc.returncode,
            error=(proc.stderr if proc.returncode != 0 else None),
        )
    except subprocess.TimeoutExpired as exc:
        return FrameResult(
            frame_index=frame_index,
            source_path=source_path,
            output_path=output_path,
            returncode=-1,
            error=f"darktable-cli timeout after {exc.timeout}s",
        )
    except OSError as exc:
        return FrameResult(
            frame_index=frame_index,
            source_path=source_path,
            output_path=output_path,
            returncode=-1,
            error=str(exc),
        )


def render_sequence(
    source_dir: Path,
    output_dir: Path,
    per_frame_ops: list[DevelopOps],
    preset: Preset,
    source_frames: list[str],
    from_frame: int = 0,
    to_frame: int | None = None,
    bundled_style_dir: Path | None = None,
    custom_style: Path | None = None,
    dry_run: bool = False,
    timeout_s: float | None = DEFAULT_PER_FRAME_TIMEOUT_S,
) -> list[FrameResult]:
    """Render a frame range. Single-worker for v0.1."""
    end = len(source_frames) if to_frame is None else min(to_frame, len(source_frames))
    results: list[FrameResult] = []
    for i in range(from_frame, end):
        source_path = source_dir / source_frames[i]
        ops = per_frame_ops[i]
        result = render_frame(
            frame_index=i,
            source_path=source_path,
            output_dir=output_dir,
            ops=ops,
            preset=preset,
            bundled_style_dir=bundled_style_dir,
            custom_style=custom_style,
            dry_run=dry_run,
            timeout_s=timeout_s,
        )
        results.append(result)
    return results
