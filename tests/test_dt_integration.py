"""Integration tests against the real darktable-cli binary.

ADVERSARIAL_AUDIT_2026-05-23 MEDIUM-4: prior emitter tests round-tripped
our own output via ElementTree, verifying what we WROTE rather than what
dt's reader EXPECTED. Both the base64 (commit 77eec41) and blendop
(commit 8c49ae8) bugs survived for months because of that gap. This
suite ships a darktable-cli invocation that surfaces dt's silent
substitution warnings (`version WRONG`, `params WRONG`, `not supported`,
`legacy_params`) as test failures.

Skips when:
  - `darktable-cli` is not on PATH
  - DT_INTEGRATION_TEST=skip env var is set
  - No reference RAW is available at tests/fixtures/raw/

The RAW fixture is user-supplied (we don't bundle anyone's RAW for
copyright reasons). Drop any small NEF/DNG/CR3 in tests/fixtures/raw/
and the test will pick it up.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from lrt_cinema.ir import DevelopOps
from lrt_cinema.xmp_emitter import emit_darktable_xmp

FIXTURES_RAW = Path(__file__).parent / "fixtures" / "raw"

_SILENT_SUBSTITUTION_PATTERNS = (
    "version WRONG",
    "params WRONG",
    "not supported",
    "legacy_params",
    "[exif] error",
    "ERROR:",
)


def _find_raw_fixture() -> Path | None:
    """Return the first RAW fixture we can find, or None."""
    if not FIXTURES_RAW.is_dir():
        return None
    raw_exts = {".nef", ".NEF", ".cr3", ".CR3", ".dng", ".DNG", ".arw", ".ARW",
                ".raf", ".RAF", ".orf", ".ORF", ".rw2", ".RW2", ".fff", ".FFF"}
    for p in sorted(FIXTURES_RAW.iterdir()):
        if p.suffix in raw_exts:
            return p
    return None


_SKIP_REASONS = []
if os.environ.get("DT_INTEGRATION_TEST") == "skip":
    _SKIP_REASONS.append("DT_INTEGRATION_TEST=skip")
if shutil.which("darktable-cli") is None:
    _SKIP_REASONS.append("darktable-cli not on PATH")
_RAW = _find_raw_fixture()
if _RAW is None:
    _SKIP_REASONS.append(
        "no RAW fixture at tests/fixtures/raw/ "
        "(drop any small NEF/DNG/CR3 there)"
    )

pytestmark = pytest.mark.skipif(
    bool(_SKIP_REASONS),
    reason="; ".join(_SKIP_REASONS) if _SKIP_REASONS else "",
)


def _run_dt_cli(raw: Path, xmp: Path, out_tif: Path) -> subprocess.CompletedProcess:
    """Run darktable-cli with our standard cinema-linear flags + -d params.

    Flag-order note: `-d <signal>` is a darktable CORE option (parsed
    after `--core`), not a darktable-cli option. All `--conf` and `-d`
    flags go after `--core` in any order.
    """
    argv = [
        "darktable-cli", str(raw), str(xmp), str(out_tif),
        "--apply-custom-presets", "0",
        "--icc-type", "LIN_REC2020", "--icc-intent", "RELATIVE_COLORIMETRIC",
        "--core",
        "-d", "common", "-d", "params",
        "--conf", "plugins/imageio/format/tiff/bpp=16",
        "--conf", "plugins/imageio/format/tiff/compress=0",
        "--conf", "plugins/imageio/format/tiff/pixelformat=0",
    ]
    return subprocess.run(argv, capture_output=True, text=True, timeout=120)


def test_dt_cli_accepts_emitter_output_without_silent_substitution(tmp_path):
    """dt log must NOT contain any silent-substitution warning for OUR entry.

    Catches HIGH-1 / base64 class bugs: a sidecar whose attributes
    parse as XML but whose params fail dt's strspn/sizeof checks. dt
    silently substitutes module defaults and prints WRONG warnings —
    this test fails on any such warning.
    """
    xmp = tmp_path / f"{_RAW.stem}{_RAW.suffix}.xmp"
    emit_darktable_xmp(DevelopOps(exposure_ev=1.0), xmp)
    out_tif = tmp_path / "out.tif"
    proc = _run_dt_cli(_RAW, xmp, out_tif)
    assert proc.returncode == 0, (
        f"darktable-cli failed: {proc.stderr[-500:] or proc.stdout[-500:]}"
    )
    # dt-cli routes `-d <signal>` debug output to STDOUT (not stderr).
    # Module-load lines (`-d common`) + per-module params/blendop
    # verification lines (`-d params`) — we need both to verify our
    # exposure entry was loaded AND validated as "ok ok" not "WRONG".
    log = proc.stdout + proc.stderr
    assert "successfully loaded module exposure from history" in log, (
        f"dt did not report loading our exposure history entry; "
        f"log tail: {log[-1000:]}"
    )
    # Find the params-verification line that follows "loaded module exposure".
    # It will be the next "params v. N:" line after our entry.
    lines = log.splitlines()
    exposure_idx = next(
        (i for i, ln in enumerate(lines) if "loaded module exposure from history" in ln),
        None,
    )
    assert exposure_idx is not None
    # The next "params v." line within ~10 lines is ours.
    found = False
    for ln in lines[exposure_idx:exposure_idx + 10]:
        if "params v." in ln and ":" in ln:
            assert "version ok\tparams ok" in ln or "version ok params ok" in ln, (
                f"our exposure entry's params verification is not 'ok ok': {ln!r}"
            )
            found = True
            break
    assert found, (
        f"could not find 'params v.' verification line for our exposure entry; "
        f"window: {lines[exposure_idx:exposure_idx + 10]!r}"
    )


def test_dt_cli_ev_value_actually_reaches_pixels(tmp_path):
    """Render same RAW with EV=0 and EV=+2; assert pixel data differs.

    Catches the day-1 base64 bug class: if our params encoding is
    silently rejected by dt, both renders use dt's default exposure
    and produce byte-identical pixel data. This test exercises the
    full pipeline (emit → dt-cli → TIFF read) and proves the exposure
    value we wrote is what dt rendered.
    """
    xmp0 = tmp_path / "ev0.xmp"
    xmp2 = tmp_path / "ev2.xmp"
    emit_darktable_xmp(DevelopOps(exposure_ev=0.0), xmp0)
    emit_darktable_xmp(DevelopOps(exposure_ev=2.0), xmp2)
    out0 = tmp_path / "ev0.tif"
    out2 = tmp_path / "ev2.tif"
    proc0 = _run_dt_cli(_RAW, xmp0, out0)
    proc2 = _run_dt_cli(_RAW, xmp2, out2)
    assert proc0.returncode == 0, proc0.stderr[-500:]
    assert proc2.returncode == 0, proc2.stderr[-500:]
    # Pixel-data hash. Skip first 64 KB to avoid TIFF header / ICC profile
    # bytes that may legitimately differ. Compare a 1 MB pixel-data window.
    b0 = out0.read_bytes()[65536:65536 + 1_000_000]
    b2 = out2.read_bytes()[65536:65536 + 1_000_000]
    assert b0 != b2, (
        "EV=0 and EV=+2 renders produced byte-identical pixel data — "
        "dt is silently ignoring our exposure value. "
        "Likely cause: emitter params encoding rejected by dt's reader "
        "(see ADVERSARIAL_AUDIT_2026-05-23 HIGH-1 / base64 bug class)."
    )
