"""RAW → DNG conversion. Adobe-free: the sole converter is **dnglab**.

Why convert at all: the pipeline's < 1 ΔE result depends on libraw seeing the
embedded LinearizationTable and the correct WhiteLevel (15520 for D750, vs
libraw's per-camera 15311 or the theoretical 16383). Both are present in a
converted DNG and absent from a NEF read directly. See
`docs/research/dng-pipeline-findings.md` §"Verification of the LINEAR demosaic
finding".

Converter: **dnglab** (open-source, LGPL-2.1, https://github.com/dnglab/dnglab)
is the only RAW→DNG path — it makes the chain Adobe-free and runs on
Linux/macOS/Windows. It is a verified drop-in for the (now-removed) Adobe DNG
Converter dependency: rendering the same NEF through dnglab-DNG vs Adobe-DNG
(identical pipeline + DCP) measured **mean ΔE2000 0.059, 100 % of pixels < 1 ΔE**
(tools/resolve_verify, 2026-05-28).

Users invoke `lrt-cinema` with a folder of NEFs; this wrapper transparently runs
dnglab once per frame (mtime+size-keyed cache makes re-runs free) and routes the
resulting DNG into the pipeline. `--no-dng-convert` bypasses it (direct libraw
NEF read, ~0.5 ΔE regression) for environments without a dnglab binary.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from filelock import FileLock

# dnglab — the sole RAW→DNG converter (Adobe-free). Checked via
# $LRT_CINEMA_DNGLAB, then PATH, then common install locations
# (Homebrew, cargo, /usr/local).
_DNGLAB_PATHS = (
    "/opt/homebrew/bin/dnglab",
    "/usr/local/bin/dnglab",
    str(Path.home() / ".cargo" / "bin" / "dnglab"),
    "/usr/bin/dnglab",
)


class DngConverterNotFound(RuntimeError):
    """Raised when the dnglab RAW→DNG converter cannot be located."""


@dataclass(frozen=True)
class DngConvertResult:
    """Output of `convert_nef_to_dng`."""

    dng_path: Path
    from_cache: bool
    elapsed_seconds: float
    converter_kind: str = "dnglab"


def find_dnglab() -> Path | None:
    """Locate the dnglab binary ($LRT_CINEMA_DNGLAB, PATH, common installs)."""
    env_path = os.environ.get("LRT_CINEMA_DNGLAB")
    if env_path and Path(env_path).is_file():
        return Path(env_path)
    on_path = shutil.which("dnglab")
    if on_path:
        return Path(on_path)
    for candidate in _DNGLAB_PATHS:
        if Path(candidate).is_file():
            return Path(candidate)
    return None


def find_converter() -> tuple[Path, str]:
    """Locate the RAW→DNG converter. Returns `(binary, "dnglab")`.

    dnglab is the only converter (the chain is Adobe-free). Raises
    `DngConverterNotFound` (with an install hint) when no dnglab binary is
    available. The `"dnglab"` second element is retained so callers and the
    `DngConvertResult.converter_kind` field have a stable shape."""
    dnglab = find_dnglab()
    if dnglab is None:
        raise DngConverterNotFound(
            "dnglab not found. Install it (open-source, Adobe-free): "
            "`brew install dnglab` (macOS) or see "
            "https://github.com/dnglab/dnglab; or set $LRT_CINEMA_DNGLAB to the "
            "binary path. Or pass --no-dng-convert to read NEFs directly "
            "(expect ~0.5 ΔE regression)."
        )
    return dnglab, "dnglab"


def _cache_key(nef_path: Path) -> str:
    """Stable key for the DNG cache. mtime + size + path."""
    st = nef_path.stat()
    src = f"{nef_path.resolve()}|{st.st_mtime_ns}|{st.st_size}"
    return hashlib.sha256(src.encode()).hexdigest()[:16]


def cached_dng_path(nef_path: Path, cache_dir: Path) -> Path:
    """Where the cached DNG for a given NEF lives (stem + content hash so two
    NEFs with the same stem in different folders don't collide)."""
    return cache_dir / f"{nef_path.stem}.{_cache_key(nef_path)}.dng"


def _converter_cmd(binary: Path, nef_path: Path, out_dng: Path) -> list[str]:
    """Build the dnglab subprocess argv. `dnglab convert <in> <out>` writes the
    DNG to the explicit `out_dng` path (default lossless JPEG compression)."""
    return [str(binary), "convert", str(nef_path), str(out_dng)]


def convert_nef_to_dng(
    nef_path: Path,
    cache_dir: Path,
    converter_binary: Path | None = None,
    converter_kind: str | None = None,
    timeout_seconds: float = 60.0,
) -> DngConvertResult:
    """Convert a single NEF (or any supported RAW) → DNG, Adobe-free via dnglab
    by default. Caches by NEF mtime+size+path; re-conversion is O(stat).

    `converter_binary`/`converter_kind`: override the auto-detected converter.
    dnglab is the only supported kind; `converter_kind` defaults to `"dnglab"`
    and is carried through to the result for a stable API shape.

    Raises: DngConverterNotFound, subprocess.TimeoutExpired, RuntimeError.
    """
    if converter_binary is None:
        converter_binary, converter_kind = find_converter()
    elif converter_kind is None:
        converter_kind = "dnglab"
    cache_dir.mkdir(parents=True, exist_ok=True)
    dst = cached_dng_path(nef_path, cache_dir)

    # Cross-process lock so cli.py's ProcessPoolExecutor (--workers N>1) can't
    # double-convert the same NEF.
    lock_path = cache_dir / f".{dst.name}.lock"
    with FileLock(str(lock_path), timeout=timeout_seconds + 60):
        if dst.exists():
            return DngConvertResult(dng_path=dst, from_cache=True,
                                    elapsed_seconds=0.0, converter_kind=converter_kind)

        # Worker-private temp dir: both converters write "<stem>.dng" here, then
        # we atomically move it into the shared cache slot.
        with tempfile.TemporaryDirectory(prefix="dngc-", dir=cache_dir) as tmpdir:
            produced = Path(tmpdir) / f"{nef_path.stem}.dng"
            cmd = _converter_cmd(converter_binary, nef_path, produced)
            t0 = time.monotonic()
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout_seconds)
            elapsed = time.monotonic() - t0
            if proc.returncode != 0:
                raise RuntimeError(
                    f"{converter_kind} conversion failed (exit {proc.returncode}) "
                    f"on {nef_path}. stderr: "
                    f"{proc.stderr.decode(errors='replace')[:500]}"
                )
            if not produced.is_file():
                raise RuntimeError(
                    f"{converter_kind} exited 0 but produced no file at {produced}."
                )
            produced.replace(dst)
    return DngConvertResult(dng_path=dst, from_cache=False,
                            elapsed_seconds=elapsed, converter_kind=converter_kind)


def resolve_render_input(
    raw_path: Path,
    cache_dir: Path,
    no_convert: bool = False,
) -> Path:
    """High-level helper used by `cli.py`. Given a user-supplied RAW path,
    return the path to feed to `pipeline.render_frame`.

    `no_convert=True` returns `raw_path` unmodified (direct NEF read). A DNG
    input is returned unmodified regardless of `no_convert`.
    """
    if raw_path.suffix.lower() == ".dng":
        return raw_path
    if no_convert:
        return raw_path
    return convert_nef_to_dng(raw_path, cache_dir).dng_path
