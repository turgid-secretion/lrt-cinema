"""RAW→DNG converter wrapper tests (dnglab — the sole, Adobe-free converter).

Pure mock-based for the subprocess paths — CI needs no converter installed.
Real-binary smoke tests (skipped when the binary/NEF are absent) catch CLI-
surface breakage on dev boxes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from lrt_cinema.dng_convert import (
    DngConverterNotFound,
    cached_dng_path,
    convert_nef_to_dng,
    find_converter,
    find_dnglab,
    resolve_render_input,
)

# ---------------------------------------------------------------------------
# Cache key + path
# ---------------------------------------------------------------------------


def test_cache_key_stable_for_same_file(tmp_path):
    src = tmp_path / "frame.nef"
    src.write_bytes(b"x" * 1024)
    assert cached_dng_path(src, tmp_path / "cache") == cached_dng_path(src, tmp_path / "cache")


def test_cache_key_changes_when_file_changes(tmp_path):
    src = tmp_path / "frame.nef"
    src.write_bytes(b"x" * 1024)
    p1 = cached_dng_path(src, tmp_path / "cache")
    src.write_bytes(b"y" * 2048)
    os.utime(src, (src.stat().st_atime + 10, src.stat().st_mtime + 10))
    assert p1 != cached_dng_path(src, tmp_path / "cache")


def test_cache_key_distinct_per_nef(tmp_path):
    a = tmp_path / "a.nef"
    a.write_bytes(b"a" * 1024)
    b = tmp_path / "b.nef"
    b.write_bytes(b"b" * 1024)
    assert cached_dng_path(a, tmp_path) != cached_dng_path(b, tmp_path)


# ---------------------------------------------------------------------------
# Converter discovery
# ---------------------------------------------------------------------------


def test_find_dnglab_respects_env_var(tmp_path, monkeypatch):
    fake = tmp_path / "dnglab"
    fake.write_text("fake")
    fake.chmod(0o755)
    monkeypatch.setenv("LRT_CINEMA_DNGLAB", str(fake))
    assert find_dnglab() == fake


def test_find_converter_prefers_dnglab(tmp_path, monkeypatch):
    fake = tmp_path / "dnglab"
    fake.write_text("fake")
    fake.chmod(0o755)
    monkeypatch.setenv("LRT_CINEMA_DNGLAB", str(fake))
    binary, kind = find_converter()
    assert kind == "dnglab" and binary == fake


def test_find_converter_raises_when_no_dnglab(monkeypatch):
    """dnglab is the only converter — its absence raises with an install hint."""
    monkeypatch.delenv("LRT_CINEMA_DNGLAB", raising=False)
    with (
        patch("lrt_cinema.dng_convert.find_dnglab", return_value=None),
        pytest.raises(DngConverterNotFound, match="dnglab not found"),
    ):
        find_converter()


# ---------------------------------------------------------------------------
# resolve_render_input dispatch
# ---------------------------------------------------------------------------


def test_resolve_passes_through_dng_unchanged(tmp_path):
    dng = tmp_path / "frame.dng"
    dng.write_bytes(b"dummy")
    assert resolve_render_input(dng, tmp_path / "cache") == dng


def test_resolve_passes_through_nef_when_no_convert(tmp_path):
    nef = tmp_path / "frame.nef"
    nef.write_bytes(b"dummy")
    assert resolve_render_input(nef, tmp_path / "cache", no_convert=True) == nef


# ---------------------------------------------------------------------------
# convert_nef_to_dng (mocked subprocess; dnglab `convert <in> <out>` shape)
# ---------------------------------------------------------------------------


def test_convert_runs_subprocess_and_caches(tmp_path):
    nef = tmp_path / "frame.nef"
    nef.write_bytes(b"raw bytes")
    cache_dir = tmp_path / "cache"
    fake_binary = tmp_path / "dnglab"
    fake_binary.write_text("#!/bin/sh\nexit 0\n")
    fake_binary.chmod(0o755)

    def fake_run(cmd, capture_output, timeout):
        Path(cmd[-1]).write_bytes(b"\x49\x49\x2a\x00" + b"\x00" * 1000)  # dnglab: explicit out
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    with patch("lrt_cinema.dng_convert.subprocess.run", side_effect=fake_run):
        r1 = convert_nef_to_dng(nef, cache_dir, converter_binary=fake_binary)
        assert r1.dng_path.is_file() and r1.from_cache is False
        assert r1.converter_kind == "dnglab"
        with patch("lrt_cinema.dng_convert.subprocess.run") as run2:
            r2 = convert_nef_to_dng(nef, cache_dir, converter_binary=fake_binary)
            run2.assert_not_called()
            assert r2.dng_path == r1.dng_path and r2.from_cache is True


def test_convert_raises_on_subprocess_failure(tmp_path):
    nef = tmp_path / "frame.nef"
    nef.write_bytes(b"raw")
    fake = tmp_path / "dnglab"
    fake.write_text("")
    fake.chmod(0o755)
    with (
        patch(
            "lrt_cinema.dng_convert.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, b"", b"bang"),
        ),
        pytest.raises(RuntimeError, match="conversion failed"),
    ):
        convert_nef_to_dng(nef, tmp_path / "cache", converter_binary=fake)


def test_convert_raises_when_no_output_produced(tmp_path):
    nef = tmp_path / "frame.nef"
    nef.write_bytes(b"raw")
    fake = tmp_path / "dnglab"
    fake.write_text("")
    fake.chmod(0o755)
    with (
        patch(
            "lrt_cinema.dng_convert.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, b"", b""),
        ),
        pytest.raises(RuntimeError, match="produced no file"),
    ):
        convert_nef_to_dng(nef, tmp_path / "cache", converter_binary=fake)


def test_convert_parallel_same_nef_serializes_on_lock(tmp_path):
    """Four workers converting the same NEF must serialize on the file lock:
    exactly one runs the subprocess; the rest see a populated cache."""
    import threading
    import time as _time
    from concurrent.futures import ThreadPoolExecutor

    nef = tmp_path / "frame.nef"
    nef.write_bytes(b"raw bytes")
    cache_dir = tmp_path / "cache"
    fake = tmp_path / "dnglab"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)

    invocations: list[list[str]] = []
    inv_lock = threading.Lock()
    barrier = threading.Barrier(4)

    def fake_run(cmd, capture_output, timeout):
        with inv_lock:
            invocations.append(list(cmd))
        _time.sleep(0.05)
        Path(cmd[-1]).write_bytes(b"\x49\x49\x2a\x00" + b"\x00" * 1000)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    def worker(_):
        barrier.wait()
        return convert_nef_to_dng(nef, cache_dir, converter_binary=fake)

    with (
        patch("lrt_cinema.dng_convert.subprocess.run", side_effect=fake_run),
        ThreadPoolExecutor(max_workers=4) as pool,
    ):
        results = list(pool.map(worker, range(4)))

    assert len(invocations) == 1
    assert len({r.dng_path for r in results}) == 1
    assert len([r for r in results if not r.from_cache]) == 1
    assert results[0].dng_path.read_bytes()[:4] == b"\x49\x49\x2a\x00"


def test_convert_parallel_same_stem_different_paths_no_clobber(tmp_path):
    """Two NEFs sharing a stem in different folders convert in parallel without
    corrupting each other's cache slot (distinct keys → distinct locks)."""
    import threading
    import time as _time
    from concurrent.futures import ThreadPoolExecutor

    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    nef_a = dir_a / "frame.nef"
    nef_a.write_bytes(b"NEF_A_PAYLOAD")
    nef_b = dir_b / "frame.nef"
    nef_b.write_bytes(b"NEF_B_PAYLOAD_DISTINCT")
    cache_dir = tmp_path / "cache"
    fake = tmp_path / "dnglab"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    barrier = threading.Barrier(2)

    def fake_run(cmd, capture_output, timeout):
        in_path = Path(cmd[-2])
        out = Path(cmd[-1])  # dnglab: convert <in> <out>
        tag = in_path.read_bytes()
        _time.sleep(0.05)
        out.write_bytes(b"\x49\x49\x2a\x00" + tag + b"\x00" * 100)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    def worker(nef):
        barrier.wait()
        return convert_nef_to_dng(nef, cache_dir, converter_binary=fake)

    with (
        patch("lrt_cinema.dng_convert.subprocess.run", side_effect=fake_run),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        fut_a = pool.submit(worker, nef_a)
        fut_b = pool.submit(worker, nef_b)
        res_a = fut_a.result()
        res_b = fut_b.result()

    assert res_a.dng_path != res_b.dng_path
    assert b"NEF_A_PAYLOAD" in res_a.dng_path.read_bytes()
    assert b"NEF_B_PAYLOAD_DISTINCT" in res_b.dng_path.read_bytes()


# ---------------------------------------------------------------------------
# Real-binary smoke (runs only when dnglab + a test NEF are present)
# ---------------------------------------------------------------------------

_REAL_NEF = Path(__file__).parent / "fixtures" / "raw" / "sample.NEF"


@pytest.mark.skipif(
    not (shutil.which("dnglab") and _REAL_NEF.exists()),
    reason="dnglab binary or test NEF unavailable",
)
def test_real_dnglab_smoke(tmp_path):
    """Convert a real NEF → DNG via the real dnglab binary (Adobe-free path)."""
    r = convert_nef_to_dng(_REAL_NEF, tmp_path)
    assert r.converter_kind == "dnglab"
    assert r.dng_path.is_file()
    assert r.dng_path.read_bytes()[:4] in (b"II*\x00", b"MM\x00*")
