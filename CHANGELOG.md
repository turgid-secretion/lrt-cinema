# Changelog

All notable changes to this project will be documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.0a0] — 2026-05-27

### Changed
- **Renderer is now an in-process Python Adobe DNG 1.7.1 pipeline.** The
  `darktable-cli` subprocess path is gone. End-to-end gym ΔE2000 drops
  from 6.37 (dt) to 0.79 (vs Adobe `dng_validate`); rose 0.84 ΔE on
  Adobe Standard. Both pass the < 1 ΔE ship gate.
- Pipeline stages: LINEAR demosaic (rawpy/libraw, Adobe-internal default)
  → AsShotNeutral inverse with optional Holy Grail kelvin override
  → ForwardMatrix or inv-ColorMatrix to XYZ(D50) → linear ProPhoto → HSM
  (mired-blended) → ExposureRamp (Adobe `dng_function_exposure_ramp`,
  carrying TotalBaselineExposure = DNG.BaselineExposure +
  DCP.BaselineExposureOffset per SDK `dng_negative.cpp:2588-2606`)
  → LookTable → per-channel ProfileToneCurve via ported `dng_spline_solver`
  (Hermite C2) with ACR3 default-table fallback → LR-authored develop ops
  (Exposure2012, Blacks2012, ToneCurvePV2012, Saturation, Vibrance,
  Contrast2012) → ProPhoto(D50) → Rec.2020(D65) Bradford CAT → TIFF/EXR
  output.
- CLI surface trimmed from 12 flags to 9. Dropped: `--engine`,
  `--no-auto-dcp`, `--no-dcp-tone-curve`, `--no-dcp-hsv-cubes`, `--style`,
  `--deflicker`, `--lrt-mask-offsets`. Added: `--workers N` (parallel
  `ProcessPoolExecutor` render pool, default `os.cpu_count() // 2`),
  `--no-dng-convert` (skip Adobe DNG Converter preprocessing on Linux /
  binary-less hosts at the cost of ~0.5 ΔE).
- Default preprocessing: NEF→DNG via Adobe DNG Converter subprocess
  (`lrt_cinema.dng_convert`). Required for the < 1 ΔE result — libraw
  needs the DNG's embedded LinearizationTable + correct WhiteLevel.
  Cached per-NEF by mtime+size.
- Holy Grail kelvin override: `DevelopOps.temperature_k` is honored
  per-frame; overrides AsShotNeutral via
  `pipeline.kelvin_to_neutral` (Adobe SDK `SetWhiteXY` solve port).

### Added
- `src/lrt_cinema/pipeline.py` — Adobe DNG 1.7.1 render pipeline.
- `src/lrt_cinema/develop_ops.py` — LR-authored develop ops (Stages 11+12).
- `src/lrt_cinema/output.py` — TIFF (16-bit int linear Rec.2020) + EXR
  (32-bit float linear Rec.2020 PIZ) writers.
- `src/lrt_cinema/dng_convert.py` — Adobe DNG Converter subprocess wrapper
  with mtime+size-keyed cache.
- `src/lrt_cinema/_acr3_curve.py` — Embedded 1025-entry ACR3 default
  tone curve (was an external JSON in the research seed).
- `tests/test_pipeline.py` — ΔE2000 ship gate vs `dng_validate`.
- `tests/test_develop_ops.py` — Per-op LR math tests.
- `tests/test_output.py` — TIFF + EXR round-trip + color-space tests.
- `tests/test_dng_convert.py` — Subprocess wrapper tests (mock-based).
- BEO tag fix (50970 → 51109 per DNG 1.7.1) + V-clamp on encoded HSV V
  per Adobe SDK `RefBaselineHueSatMap` (subsumes PR #18).

### Removed
- `src/lrt_cinema/runner.py` (dt-cli subprocess machinery).
- `src/lrt_cinema/xmp_emitter.py` (no dt history-stack emission).
- `src/lrt_cinema/presets/*.style` + `ocio_config.ocio` + `CALIBRATION.md`
  + `definitions.py` (no dt-cli styles).
- `dcp.kelvin_tint_to_dt_multipliers`, `lut3d_baker.bake_dcp_cubes_to_resolve_cube`.
- `tests/test_xmp_emitter.py`, `tests/test_runner.py`,
  `tests/test_dt_integration.py`.
- The `darktable-cli` runtime dependency.

### Known limitations
- `scene_kelvin` hardcoded at 5500K. Computed via `neutral_to_kelvin`
  works but regresses rose at high K (HSM mired-blend divergence,
  untraced). v0.6.x.
- `stills-finished` preset returns `NotImplementedError` — AgX port is
  v0.6.x scope.
- `Sharpness` is a no-op in v0.6 (sharpening conventionally belongs in
  the grade stage, not the linear render).
- `Highlights2012`, `Shadows2012`, `Whites2012` remain dropped at
  render — LR PV2012 parametric tone math is closed-source.

## [Unreleased] — pre-0.6

Earlier dt-cli–driven prototype. See git history.
