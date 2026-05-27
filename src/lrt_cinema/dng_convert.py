"""NEF → DNG conversion via the Adobe DNG Converter subprocess.

Why: the v0.6 pipeline's < 1 ΔE result depends on libraw seeing the embedded
LinearizationTable and the correct WhiteLevel (15520 for D750, vs libraw's
per-camera 15311 or the theoretical 16383). Both are present in the
Adobe-converted DNG and absent from a NEF read directly. See
`docs/research/dng-pipeline-findings.md` §"Verification of the LINEAR
demosaic finding".

Users invoke `lrt-cinema` with a folder of NEFs; this wrapper transparently
runs the Adobe DNG Converter once per frame (with mtime+size-keyed cache to
make re-runs free) and routes the resulting DNG into the pipeline.

The `--no-dng-convert` CLI flag bypasses this preprocessing for users on
Linux (where Adobe DNG Converter has no official build) or who would rather
trade ~0.5 ΔE for not installing a vendored Adobe binary.

Performance: ~0.5-1.0 s per NEF on M1; bottlenecks are sequential subprocess
spawns rather than CPU. The worker pool in `cli.py` parallelizes across
frames via `ProcessPoolExecutor`.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Adobe DNG Converter install paths (macOS first — only platform with a
# canonical fixed path). Linux users with Wine / a Crossover bottle can
# point at their own install via the LRT_CINEMA_DNG_CONVERTER env var.
_DNG_CONVERTER_PATHS = (
    "/Applications/Adobe DNG Converter.app/Contents/MacOS/Adobe DNG Converter",
    "C:\\Program Files\\Adobe\\Adobe DNG Converter\\Adobe DNG Converter.exe",
    "C:\\Program Files (x86)\\Adobe\\Adobe DNG Converter\\Adobe DNG Converter.exe",
)


class DngConverterNotFound(RuntimeError):
    """Raised when the Adobe DNG Converter binary can't be located.

    Users hit this when they're on Linux (no official build) or haven't
    installed the converter. The CLI catches and either falls back to
    direct-NEF read (if `--no-dng-convert`) or surfaces an actionable
    install hint."""


@dataclass(frozen=True)
class DngConvertResult:
    """Output of `convert_nef_to_dng`."""

    dng_path: Path
    from_cache: bool
    elapsed_seconds: float


def find_dng_converter() -> Path:
    """Locate the Adobe DNG Converter binary. Checks
    $LRT_CINEMA_DNG_CONVERTER first, then platform install paths.

    Raises `DngConverterNotFound` with a platform-specific install hint."""
    env_path = os.environ.get("LRT_CINEMA_DNG_CONVERTER")
    if env_path and Path(env_path).is_file():
        return Path(env_path)
    for candidate in _DNG_CONVERTER_PATHS:
        if Path(candidate).is_file():
            return Path(candidate)
    raise DngConverterNotFound(
        "Adobe DNG Converter not found. Install it from "
        "https://helpx.adobe.com/camera-raw/digital-negative.html, "
        "or set $LRT_CINEMA_DNG_CONVERTER to the binary path, "
        "or pass --no-dng-convert to read NEFs directly "
        "(expect ~0.5 ΔE regression)."
    )


def _cache_key(nef_path: Path) -> str:
    """Stable key for the DNG cache. mtime + size — content-hash would be
    more correct but adds 50-100 ms per frame for a 25 MB NEF; mtime is
    enough since the cache invalidates on the next pipeline rev anyway."""
    st = nef_path.stat()
    src = f"{nef_path.resolve()}|{st.st_mtime_ns}|{st.st_size}"
    return hashlib.sha256(src.encode()).hexdigest()[:16]


def cached_dng_path(nef_path: Path, cache_dir: Path) -> Path:
    """Where the cached DNG for a given NEF lives. Per-NEF stem + content
    hash so two NEFs with the same stem in different folders don't collide."""
    return cache_dir / f"{nef_path.stem}.{_cache_key(nef_path)}.dng"


def convert_nef_to_dng(
    nef_path: Path,
    cache_dir: Path,
    converter_binary: Path | None = None,
    timeout_seconds: float = 60.0,
) -> DngConvertResult:
    """Convert a single NEF (or any libraw-readable RAW) → DNG.

    Caches by NEF mtime+size+path. Re-conversion returns the cached DNG
    in O(stat). Always runs Adobe DNG Converter with `-c` (default
    Lossless JPEG compression, smallest output) and `-d <cache_dir>`.

    Raises:
      DngConverterNotFound — binary missing.
      subprocess.TimeoutExpired — single-frame convert exceeded `timeout_seconds`.
      RuntimeError — converter exit code != 0.
    """
    import time

    if converter_binary is None:
        converter_binary = find_dng_converter()
    cache_dir.mkdir(parents=True, exist_ok=True)
    dst = cached_dng_path(nef_path, cache_dir)
    if dst.exists():
        return DngConvertResult(dng_path=dst, from_cache=True, elapsed_seconds=0.0)

    t0 = time.monotonic()
    proc = subprocess.run(
        [
            str(converter_binary),
            "-c",
            "-d", str(cache_dir),
            str(nef_path),
        ],
        capture_output=True,
        timeout=timeout_seconds,
    )
    elapsed = time.monotonic() - t0
    if proc.returncode != 0:
        raise RuntimeError(
            f"Adobe DNG Converter failed (exit {proc.returncode}) on "
            f"{nef_path}. stderr: {proc.stderr.decode(errors='replace')[:500]}"
        )
    # Adobe DNG Converter writes <stem>.dng in cache_dir; rename to the
    # cache-keyed name so we don't re-convert on next run.
    produced = cache_dir / f"{nef_path.stem}.dng"
    if not produced.is_file():
        raise RuntimeError(
            f"Adobe DNG Converter exited 0 but produced no file at {produced}."
        )
    produced.rename(dst)
    return DngConvertResult(dng_path=dst, from_cache=False, elapsed_seconds=elapsed)


def resolve_render_input(
    raw_path: Path,
    cache_dir: Path,
    no_convert: bool = False,
) -> Path:
    """High-level helper used by `cli.py`. Given a user-supplied RAW path,
    return the path that should be fed to `pipeline.render_frame`.

    `no_convert=True` returns `raw_path` unmodified (NEF direct read).
    `no_convert=False` runs the cached NEF→DNG conversion and returns the
    DNG path. If the input is already a DNG, returns it unmodified
    regardless of `no_convert`.
    """
    if raw_path.suffix.lower() == ".dng":
        return raw_path
    if no_convert:
        return raw_path
    return convert_nef_to_dng(raw_path, cache_dir).dng_path
