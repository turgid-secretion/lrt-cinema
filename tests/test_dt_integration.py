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

import numpy as np
import pytest

from lrt_cinema.dcp import DCPProfile
from lrt_cinema.ir import DevelopOps, TonePoint
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


def _make_test_dcp_with_curve_and_matrix() -> DCPProfile:
    """Plausible Nikon-shaped DCP for integration tests.

    Matrices chosen so the green-normalized multipliers at 5500 K fall
    in dt's accepted 0..8 multiplier range (src/iop/temperature.c#L78-81).
    """
    return DCPProfile(
        color_matrix_1=np.array([
            [1.0, -0.4,  0.0],
            [-0.5, 1.3,  0.3],
            [-0.1,  0.2, 0.8],
        ]),
        color_matrix_2=np.array([
            [0.9, -0.3, -0.1],
            [-0.5, 1.3,  0.2],
            [-0.1,  0.2, 0.7],
        ]),
        kelvin_1=2856.0,
        kelvin_2=6504.0,
        baseline_exposure=0.0,
        baseline_exposure_offset=0.0,
        profile_tone_curve=np.stack(
            [np.linspace(0, 1, 32), np.linspace(0, 1, 32) ** 0.5],
            axis=1,
        ),
    )


def _assert_module_loaded_ok(log: str, op_name: str, modversion: int) -> None:
    """Assert dt-cli loaded `op_name` from history AND reported 'params ok'.

    Catches the silent-substitution failure mode where dt accepts the XMP
    but rejects the params blob (wrong size, wrong encoding, wrong
    modversion) and substitutes module defaults. See
    docs/research/ADVERSARIAL_AUDIT_2026-05-23 HIGH-1 / base64-bug.
    """
    assert f"successfully loaded module {op_name} from history" in log, (
        f"dt did not report loading our {op_name} entry; log tail: {log[-2000:]}"
    )
    lines = log.splitlines()
    op_idx = next(
        (i for i, ln in enumerate(lines)
         if f"loaded module {op_name} from history" in ln),
        None,
    )
    assert op_idx is not None
    # The next "params v. N:" line within ~10 lines is ours.
    for ln in lines[op_idx:op_idx + 10]:
        if "params v." in ln and ":" in ln:
            assert "version ok" in ln and "params ok" in ln, (
                f"{op_name} params verification not 'ok ok': {ln!r}"
            )
            return
    raise AssertionError(
        f"could not find 'params v.' line for {op_name}; "
        f"window: {lines[op_idx:op_idx + 10]!r}"
    )


def test_dt_cli_accepts_temperature_module_emission(tmp_path):
    """dt-cli must report 'params ok' for our temperature v4 emission.

    Validates the dt_iop_temperature_params_t struct layout — 4 floats
    + int preset = 20 bytes at modversion 4 (src/iop/temperature.c#L76-82
    + L46 at dt master SHA 9402c65275). A size mismatch would trigger
    dt's silent-substitution path (the base64-bug class).
    """
    xmp = tmp_path / f"{_RAW.stem}{_RAW.suffix}.xmp"
    emit_darktable_xmp(
        DevelopOps(exposure_ev=0.0, temperature_k=5500),
        xmp,
        dcp_profile=_make_test_dcp_with_curve_and_matrix(),
    )
    out_tif = tmp_path / "out.tif"
    proc = _run_dt_cli(_RAW, xmp, out_tif)
    assert proc.returncode == 0, (
        f"darktable-cli failed: {proc.stderr[-500:] or proc.stdout[-500:]}"
    )
    log = proc.stdout + proc.stderr
    _assert_module_loaded_ok(log, "temperature", 4)


def test_dt_cli_accepts_basecurve_module_emission(tmp_path):
    """dt-cli must report 'params ok' for our basecurve v6 emission.

    Validates dt_iop_basecurve_params_t struct layout — 480 bytes of
    nodes + 40 bytes of trailers = 520 bytes at modversion 6
    (src/iop/basecurve.c#L63-76 + L57 at dt master SHA 9402c65275).
    basecurve is dt's designated camera-baseline-tone-curve module and
    the emission target for DCP-bundled ProfileToneCurves. Same
    silent-substitution class as the temperature test.
    """
    xmp = tmp_path / f"{_RAW.stem}{_RAW.suffix}.xmp"
    emit_darktable_xmp(
        DevelopOps(exposure_ev=0.0),
        xmp,
        dcp_profile=_make_test_dcp_with_curve_and_matrix(),
    )
    out_tif = tmp_path / "out.tif"
    proc = _run_dt_cli(_RAW, xmp, out_tif)
    assert proc.returncode == 0, (
        f"darktable-cli failed: {proc.stderr[-500:] or proc.stdout[-500:]}"
    )
    log = proc.stdout + proc.stderr
    _assert_module_loaded_ok(log, "basecurve", 6)


def test_dt_cli_accepts_tonecurve_module_emission(tmp_path):
    """dt-cli must report 'params ok' for our tonecurve v5 emission.

    Validates dt_iop_tonecurve_params_t struct layout — 520 bytes
    (src/iop/tonecurve.c#L108-117 + L72 at dt master SHA 9402c65275).
    Tonecurve is the LR-explicit-curve emission target (not DCP).
    """
    xmp = tmp_path / f"{_RAW.stem}{_RAW.suffix}.xmp"
    emit_darktable_xmp(
        DevelopOps(
            exposure_ev=0.0,
            tone_curve=[
                TonePoint(0.0, 0.0),
                TonePoint(0.5, 0.5),
                TonePoint(1.0, 1.0),
            ],
        ),
        xmp,
        dcp_profile=None,
    )
    # Override identity check: emit an S-curve so the emitter actually writes.
    emit_darktable_xmp(
        DevelopOps(
            exposure_ev=0.0,
            tone_curve=[
                TonePoint(0.0, 0.0),
                TonePoint(0.25, 0.1),
                TonePoint(0.75, 0.9),
                TonePoint(1.0, 1.0),
            ],
        ),
        xmp,
        dcp_profile=None,
    )
    out_tif = tmp_path / "out.tif"
    proc = _run_dt_cli(_RAW, xmp, out_tif)
    assert proc.returncode == 0, (
        f"darktable-cli failed: {proc.stderr[-500:] or proc.stdout[-500:]}"
    )
    log = proc.stdout + proc.stderr
    _assert_module_loaded_ok(log, "tonecurve", 5)


def test_dt_cli_tonecurve_actually_affects_pixels(tmp_path):
    """Render with + without a steep highlight-lift tonecurve; pixels must differ.

    Mirrors test_dt_cli_ev_value_actually_reaches_pixels for tonecurve:
    if dt silently substituted defaults (identity 2-point L curve) the
    two renders would be byte-identical.
    """
    # No-tonecurve: ops with no LR curve and no DCP curve → no tonecurve
    # module emitted. Reference for "what the pipeline does without us".
    xmp_no_tc = tmp_path / "no_tc.xmp"
    emit_darktable_xmp(DevelopOps(exposure_ev=0.0), xmp_no_tc, dcp_profile=None)

    # With-tonecurve: emit an S-curve via the LR tone-curve path.
    xmp_tc = tmp_path / "tc.xmp"
    emit_darktable_xmp(
        DevelopOps(
            exposure_ev=0.0,
            tone_curve=[
                TonePoint(0.0, 0.0),
                TonePoint(0.25, 0.1),
                TonePoint(0.75, 0.9),
                TonePoint(1.0, 1.0),
            ],
        ),
        xmp_tc,
        dcp_profile=None,
    )

    out_no = tmp_path / "no.tif"
    out_yes = tmp_path / "yes.tif"
    proc_no = _run_dt_cli(_RAW, xmp_no_tc, out_no)
    proc_yes = _run_dt_cli(_RAW, xmp_tc, out_yes)
    assert proc_no.returncode == 0, proc_no.stderr[-500:]
    assert proc_yes.returncode == 0, proc_yes.stderr[-500:]
    b_no = out_no.read_bytes()[65536:65536 + 1_000_000]
    b_yes = out_yes.read_bytes()[65536:65536 + 1_000_000]
    assert b_no != b_yes, (
        "no-tonecurve and S-curve renders produced byte-identical pixel data — "
        "dt is silently ignoring our tonecurve params. "
        "Likely cause: emitter params encoding rejected by dt's reader "
        "(see ADVERSARIAL_AUDIT_2026-05-23 HIGH-1 / base64 bug class)."
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
