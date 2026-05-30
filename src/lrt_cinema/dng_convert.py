"""RAW → DNG conversion. Adobe-free by default via **dnglab**.

Why convert at all: the pipeline's < 1 ΔE result depends on libraw seeing the
embedded LinearizationTable and the correct WhiteLevel (15520 for D750, vs
libraw's per-camera 15311 or the theoretical 16383). Both are present in a
converted DNG and absent from a NEF read directly. See
`docs/research/dng-pipeline-findings.md` §"Verification of the LINEAR demosaic
finding".

Converter: **dnglab** (open-source, LGPL-2.1, https://github.com/dnglab/dnglab)
is the default — it makes the chain Adobe-free, runs on Linux/macOS/Windows, and
is a verified drop-in for the Adobe DNG Converter on this pipeline: rendering the
same NEF through dnglab-DNG vs Adobe-DNG (identical pipeline + DCP) measured
**mean ΔE2000 0.059, 100 % of pixels < 1 ΔE** (tools/resolve_verify, 2026-05-28).
The Adobe DNG Converter is retained only as a fallback when dnglab is absent but
Adobe is installed.

Users invoke `lrt-cinema` with a folder of NEFs; this wrapper transparently runs
the converter once per frame (mtime+size-keyed cache makes re-runs free) and
routes the resulting DNG into the pipeline. `--no-dng-convert` bypasses it
(direct libraw NEF read, ~0.5 ΔE regression — the original Linux fallback, now
largely redundant since dnglab covers Linux).
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

# dnglab — the Adobe-free default. Checked via $LRT_CINEMA_DNGLAB, then PATH,
# then common install locations (Homebrew, cargo, /usr/local).
_DNGLAB_PATHS = (
    "/opt/homebrew/bin/dnglab",
    "/usr/local/bin/dnglab",
    str(Path.home() / ".cargo" / "bin" / "dnglab"),
    "/usr/bin/dnglab",
)

# Adobe DNG Converter — fallback only. macOS fixed path + Windows installs;
# Linux users can point $LRT_CINEMA_DNG_CONVERTER at a Wine/Crossover bottle.
_DNG_CONVERTER_PATHS = (
    "/Applications/Adobe DNG Converter.app/Contents/MacOS/Adobe DNG Converter",
    "C:\\Program Files\\Adobe\\Adobe DNG Converter\\Adobe DNG Converter.exe",
    "C:\\Program Files (x86)\\Adobe\\Adobe DNG Converter\\Adobe DNG Converter.exe",
)


class DngConverterNotFound(RuntimeError):
    """Raised when no RAW→DNG converter (dnglab or Adobe) can be located."""


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


def find_dng_converter() -> Path:
    """Locate the Adobe DNG Converter binary (fallback). Checks
    $LRT_CINEMA_DNG_CONVERTER first, then platform install paths.

    Raises `DngConverterNotFound` if absent."""
    env_path = os.environ.get("LRT_CINEMA_DNG_CONVERTER")
    if env_path and Path(env_path).is_file():
        return Path(env_path)
    for candidate in _DNG_CONVERTER_PATHS:
        if Path(candidate).is_file():
            return Path(candidate)
    raise DngConverterNotFound(
        "Adobe DNG Converter not found. (It is only a fallback — prefer dnglab.)"
    )


def find_converter() -> tuple[Path, str]:
    """Locate a RAW→DNG converter. Returns (binary, kind) where kind is
    `"dnglab"` (preferred, Adobe-free) or `"adobe"` (fallback).

    Raises `DngConverterNotFound` (with a dnglab-first install hint) if neither
    is available."""
    dnglab = find_dnglab()
    if dnglab is not None:
        return dnglab, "dnglab"
    try:
        return find_dng_converter(), "adobe"
    except DngConverterNotFound:
        raise DngConverterNotFound(
            "No RAW→DNG converter found. Install dnglab (open, Adobe-free): "
            "`brew install dnglab` (macOS) or see "
            "https://github.com/dnglab/dnglab; or set $LRT_CINEMA_DNGLAB to the "
            "binary path. (Adobe DNG Converter also works as a fallback via "
            "$LRT_CINEMA_DNG_CONVERTER.) Or pass --no-dng-convert to read NEFs "
            "directly (expect ~0.5 ΔE regression)."
        ) from None


def _cache_key(nef_path: Path) -> str:
    """Stable key for the DNG cache. mtime + size + path."""
    st = nef_path.stat()
    src = f"{nef_path.resolve()}|{st.st_mtime_ns}|{st.st_size}"
    return hashlib.sha256(src.encode()).hexdigest()[:16]


def cached_dng_path(nef_path: Path, cache_dir: Path) -> Path:
    """Where the cached DNG for a given NEF lives (stem + content hash so two
    NEFs with the same stem in different folders don't collide)."""
    return cache_dir / f"{nef_path.stem}.{_cache_key(nef_path)}.dng"


def _converter_cmd(kind: str, binary: Path, nef_path: Path, out_dng: Path) -> list[str]:
    """Build the subprocess argv for the chosen converter. Both write the DNG
    to `out_dng` (dnglab takes an explicit output path; Adobe writes
    `<stem>.dng` into the -d directory, which we point at out_dng's parent and
    name accordingly)."""
    if kind == "dnglab":
        # `dnglab convert <in> <out>` — explicit output, default lossless JPEG.
        return [str(binary), "convert", str(nef_path), str(out_dng)]
    # Adobe DNG Converter: -c lossless JPEG, -d output dir (fixed <stem>.dng).
    return [str(binary), "-c", "-d", str(out_dng.parent), str(nef_path)]


def convert_nef_to_dng(
    nef_path: Path,
    cache_dir: Path,
    converter_binary: Path | None = None,
    converter_kind: str | None = None,
    timeout_seconds: float = 60.0,
) -> DngConvertResult:
    """Convert a single NEF (or any supported RAW) → DNG, Adobe-free via dnglab
    by default. Caches by NEF mtime+size+path; re-conversion is O(stat).

    `converter_binary`/`converter_kind`: override the auto-detected converter
    (kind ∈ {"dnglab","adobe"}). When `converter_binary` is given without a
    kind, dnglab semantics are assumed.

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
            cmd = _converter_cmd(converter_kind, converter_binary, nef_path, produced)
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
