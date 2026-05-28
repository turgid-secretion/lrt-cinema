"""Adobe DNG Converter subprocess wrapper tests.

Pure mock-based — CI does not require Adobe DNG Converter to be installed.
Real-binary smoke test (skipped when binary is absent) is included so dev
runs catch breakage if the converter CLI surface changes.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from lrt_cinema.dng_convert import (
    DngConverterNotFound,
    cached_dng_path,
    convert_nef_to_dng,
    find_dng_converter,
    resolve_render_input,
)

# ---------------------------------------------------------------------------
# Cache key + path
# ---------------------------------------------------------------------------


def test_cache_key_stable_for_same_file(tmp_path):
    src = tmp_path / "frame.nef"
    src.write_bytes(b"x" * 1024)
    p1 = cached_dng_path(src, tmp_path / "cache")
    p2 = cached_dng_path(src, tmp_path / "cache")
    assert p1 == p2


def test_cache_key_changes_when_file_changes(tmp_path):
    src = tmp_path / "frame.nef"
    src.write_bytes(b"x" * 1024)
    p1 = cached_dng_path(src, tmp_path / "cache")
    # Modify file → mtime changes → cache key changes.
    src.write_bytes(b"y" * 2048)
    os.utime(src, (src.stat().st_atime + 10, src.stat().st_mtime + 10))
    p2 = cached_dng_path(src, tmp_path / "cache")
    assert p1 != p2


def test_cache_key_distinct_per_nef(tmp_path):
    a = tmp_path / "a.nef"
    a.write_bytes(b"a" * 1024)
    b = tmp_path / "b.nef"
    b.write_bytes(b"b" * 1024)
    assert cached_dng_path(a, tmp_path) != cached_dng_path(b, tmp_path)


# ---------------------------------------------------------------------------
# find_dng_converter
# ---------------------------------------------------------------------------


def test_find_raises_when_no_binary_available(tmp_path, monkeypatch):
    """Force find to fail by clearing env + pointing at impossible paths."""
    monkeypatch.delenv("LRT_CINEMA_DNG_CONVERTER", raising=False)
    with (
        patch("lrt_cinema.dng_convert._DNG_CONVERTER_PATHS", (str(tmp_path / "nope"),)),
        pytest.raises(DngConverterNotFound, match="not found"),
    ):
        find_dng_converter()


def test_find_respects_env_var(tmp_path, monkeypatch):
    fake = tmp_path / "fakedngc"
    fake.write_text("fake")
    monkeypatch.setenv("LRT_CINEMA_DNG_CONVERTER", str(fake))
    assert find_dng_converter() == fake


# ---------------------------------------------------------------------------
# resolve_render_input dispatch
# ---------------------------------------------------------------------------


def test_resolve_passes_through_dng_unchanged(tmp_path):
    dng = tmp_path / "frame.dng"
    dng.write_bytes(b"dummy")
    out = resolve_render_input(dng, tmp_path / "cache")
    assert out == dng


def test_resolve_passes_through_nef_when_no_convert(tmp_path):
    nef = tmp_path / "frame.nef"
    nef.write_bytes(b"dummy")
    out = resolve_render_input(nef, tmp_path / "cache", no_convert=True)
    assert out == nef


# ---------------------------------------------------------------------------
# convert_nef_to_dng (mocked subprocess)
# ---------------------------------------------------------------------------


def _fake_converter(stem: str, cache_dir: Path) -> None:
    """Emulate Adobe DNG Converter's output side-effect."""
    (cache_dir / f"{stem}.dng").write_bytes(b"\x49\x49\x2a\x00" + b"\x00" * 1000)


def test_convert_runs_subprocess_and_caches(tmp_path):
    nef = tmp_path / "frame.nef"
    nef.write_bytes(b"raw bytes")
    cache_dir = tmp_path / "cache"

    fake_binary = tmp_path / "fake_dngc"
    fake_binary.write_text("#!/bin/sh\nexit 0\n")
    fake_binary.chmod(0o755)

    def fake_run(cmd, capture_output, timeout):
        _fake_converter("frame", Path(cmd[-2]))  # -d <cache_dir> position
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    with patch("lrt_cinema.dng_convert.subprocess.run", side_effect=fake_run):
        r1 = convert_nef_to_dng(nef, cache_dir, converter_binary=fake_binary)
        assert r1.dng_path.is_file()
        assert r1.from_cache is False
        # Second call hits the cache — no subprocess.
        with patch("lrt_cinema.dng_convert.subprocess.run") as run2:
            r2 = convert_nef_to_dng(nef, cache_dir, converter_binary=fake_binary)
            run2.assert_not_called()
            assert r2.dng_path == r1.dng_path
            assert r2.from_cache is True


def test_convert_raises_on_subprocess_failure(tmp_path):
    nef = tmp_path / "frame.nef"
    nef.write_bytes(b"raw")
    cache_dir = tmp_path / "cache"
    fake_binary = tmp_path / "fakedngc"
    fake_binary.write_text("")
    fake_binary.chmod(0o755)

    with patch(
        "lrt_cinema.dng_convert.subprocess.run",
        return_value=subprocess.CompletedProcess([], 1, b"", b"bang"),
    ), pytest.raises(RuntimeError, match="Adobe DNG Converter failed"):
        convert_nef_to_dng(nef, cache_dir, converter_binary=fake_binary)


def test_convert_raises_when_no_output_produced(tmp_path):
    nef = tmp_path / "frame.nef"
    nef.write_bytes(b"raw")
    cache_dir = tmp_path / "cache"
    fake_binary = tmp_path / "fakedngc"
    fake_binary.write_text("")
    fake_binary.chmod(0o755)

    with patch(
        "lrt_cinema.dng_convert.subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, b"", b""),
    ), pytest.raises(RuntimeError, match="produced no file"):
        convert_nef_to_dng(nef, cache_dir, converter_binary=fake_binary)


# ---------------------------------------------------------------------------
# Parallel-conversion regression (race against Adobe's fixed-name output)
# ---------------------------------------------------------------------------


def test_convert_parallel_same_nef_serializes_on_lock(tmp_path):
    """Four workers converting the same NEF in parallel must serialize on
    the file lock: exactly one runs the subprocess, the rest see a populated
    cache, and the cached DNG is intact. Without the lock, two workers can
    both pass dst.exists(), race on Adobe's fixed `<stem>.dng` output, and
    one fails ENOENT during rename — that's the bug this guards."""
    import threading
    import time as _time
    from concurrent.futures import ThreadPoolExecutor

    nef = tmp_path / "frame.nef"
    nef.write_bytes(b"raw bytes")
    cache_dir = tmp_path / "cache"
    fake_binary = tmp_path / "fake_dngc"
    fake_binary.write_text("#!/bin/sh\nexit 0\n")
    fake_binary.chmod(0o755)

    n_workers = 4
    invocations: list[list[str]] = []
    inv_lock = threading.Lock()
    barrier = threading.Barrier(n_workers)

    def fake_run(cmd, capture_output, timeout):
        with inv_lock:
            invocations.append(list(cmd))
        # Sleep widens the overlap window — without the file lock, all four
        # workers would be inside the subprocess concurrently here.
        _time.sleep(0.05)
        _fake_converter(Path(cmd[-1]).stem, Path(cmd[-2]))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    def worker(_):
        barrier.wait()
        return convert_nef_to_dng(nef, cache_dir, converter_binary=fake_binary)

    with patch("lrt_cinema.dng_convert.subprocess.run", side_effect=fake_run):
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            results = list(pool.map(worker, range(n_workers)))

    assert len(invocations) == 1, (
        f"expected exactly 1 subprocess call under lock; got {len(invocations)}"
    )
    paths = {r.dng_path for r in results}
    assert len(paths) == 1, f"workers disagreed on dst path: {paths}"
    fresh = [r for r in results if not r.from_cache]
    assert len(fresh) == 1, f"expected exactly one fresh convert, got {len(fresh)}"
    dst = results[0].dng_path
    assert dst.is_file()
    assert dst.read_bytes()[:4] == b"\x49\x49\x2a\x00"


def test_convert_parallel_same_stem_different_paths_no_clobber(tmp_path):
    """Behavioural check: two NEFs sharing a stem in different folders
    convert in parallel without corrupting each other's cache slot. They
    have different cache keys (different locks) so subprocesses can run
    concurrently — passes trivially under the worker-private temp dir
    design, and would fail loudly if a future change re-introduces a
    shared output directory."""
    import threading
    import time as _time
    from concurrent.futures import ThreadPoolExecutor

    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    nef_a = dir_a / "frame.nef"
    nef_b = dir_b / "frame.nef"
    nef_a.write_bytes(b"NEF_A_PAYLOAD")
    nef_b.write_bytes(b"NEF_B_PAYLOAD_DISTINCT")
    cache_dir = tmp_path / "cache"
    fake_binary = tmp_path / "fake_dngc"
    fake_binary.write_text("#!/bin/sh\nexit 0\n")
    fake_binary.chmod(0o755)

    barrier = threading.Barrier(2)

    def fake_run(cmd, capture_output, timeout):
        # Tag each fake DNG with its input bytes so we can prove the right
        # body ended up in the right cache slot.
        in_path = Path(cmd[-1])
        out_dir = Path(cmd[-2])
        tag = in_path.read_bytes()
        _time.sleep(0.05)
        (out_dir / f"{in_path.stem}.dng").write_bytes(
            b"\x49\x49\x2a\x00" + tag + b"\x00" * 100
        )
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    def worker(nef):
        barrier.wait()
        return convert_nef_to_dng(nef, cache_dir, converter_binary=fake_binary)

    with patch("lrt_cinema.dng_convert.subprocess.run", side_effect=fake_run):
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_a = pool.submit(worker, nef_a)
            fut_b = pool.submit(worker, nef_b)
            res_a = fut_a.result()
            res_b = fut_b.result()

    assert res_a.dng_path != res_b.dng_path
    assert res_a.dng_path.is_file()
    assert res_b.dng_path.is_file()
    assert b"NEF_A_PAYLOAD" in res_a.dng_path.read_bytes()
    assert b"NEF_B_PAYLOAD_DISTINCT" in res_b.dng_path.read_bytes()


# ---------------------------------------------------------------------------
# Optional: real-binary smoke (only when Adobe DNG Converter is installed)
# ---------------------------------------------------------------------------


_REAL_BINARY = Path(
    "/Applications/Adobe DNG Converter.app/Contents/MacOS/Adobe DNG Converter",
)
_REAL_NEF = Path("/tmp/v04_test_input/DSC_4053.NEF")


@pytest.mark.skipif(
    not (_REAL_BINARY.is_file() and _REAL_NEF.is_file()),
    reason="Adobe DNG Converter binary or test NEF unavailable",
)
def test_real_binary_smoke(tmp_path):
    """Convert a real NEF → DNG via the real Adobe DNG Converter. Checks
    the subprocess invocation actually works on the dev box."""
    r = convert_nef_to_dng(_REAL_NEF, tmp_path)
    assert r.dng_path.is_file()
    # Adobe DNG Converter produces ~17 MB DNGs for D750 (sensor size).
    size_mb = r.dng_path.stat().st_size / (1024 * 1024)
    assert 10 < size_mb < 25, f"unexpected DNG size: {size_mb:.1f} MB"
    # TIFF header magic — DNG is TIFF-shaped.
    assert r.dng_path.read_bytes()[:4] in (b"II*\x00", b"MM\x00*")
