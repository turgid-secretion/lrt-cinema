# Changelog

All notable changes to this project will be documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — v0.8 prep

### Added
- **Dual-mode grading scaffold — `--render-intent {faithful,perceptual}`**
  (DECISIONS.md §7, v0.9 step 1). Threads a `RenderIntent` through
  `cli → _RenderJob → develop_ops.apply_develop_ops / apply_stage_12_perceptual`;
  only the HSL + Color-Grade applicators branch on it. **faithful** (default) =
  today's Adobe-hexcone ops (the sRGB TIFF / LRT round-trip — the Lightroom
  look); **perceptual** = modern primitives (OKLCh HSL, ASC-CDL grade) for the
  ACEScg master. The perceptual applicators (`_apply_hsl_perceptual`,
  `_apply_color_grade_perceptual`) currently **alias the faithful ones**, so the
  switch is wired but **byte-identical** — zero behaviour change, ship gate
  untouched — until v0.9 steps 2-3 fill them. Routing is covered by a
  monkeypatch test that survives those steps; identity stays byte-exact under
  both intents. Shared op IR (`HslBands`, `ColorGrade`) across intents.
- **LR Color Grading wheels baked into the render (Stage 12).** The four wheels
  — Shadows, Midtones, Highlights, Global — each {Hue, Saturation, Luminance},
  plus `ColorGradeBlending` and `ColorGradeBalance` (`crs:ColorGrade*`; the PV4+
  successor to Split Toning) are parsed (`ir.ColorGrade`), interpolated per
  frame, and applied as a tonal-zone-weighted colour overlay
  (`develop_ops.apply_color_grade`). Each wheel adds a **zero-sum chroma
  direction** (Hue carries no net luminance) scaled by Saturation, plus a
  uniform Luminance offset; the Shadow/Midtone/Highlight tints are masked by a
  luminance-driven **partition-of-unity** weighting (shaped by Blending and
  Balance) taken on a perceptual (sRGB-OETF) luminance proxy, while Global
  applies everywhere. Output is clamped ≥0 (no negative ProPhoto channel reaches
  the output matrix). The parser also reads the legacy `crs:SplitToning*`
  aliases (ACR stores the Color-Grade Shadow/Highlight Hue+Sat and Balance
  there, and Split Toning is itself PV2012-era), so a pure Split-Toning edit
  drives the Shadow/Highlight wheels. **Identity short-circuits byte-exact**, so
  Blending/Balance/Hue with no tint — and any no-grade render — is bit-identical
  to the prior pipeline; the ΔE ship gate is unaffected. Axis-1 oracle:
  `test_color_grade_matches_independent_oracle` + non-zero-sum-tint /
  swapped-zone sensitivity legs; `_PROPHOTO_LUMINANCE` is cross-checked against
  colour-science's ProPhoto matrix. **Fidelity caveat:** Lightroom's exact tint
  strengths, zone-mask shape/domain, and Blending/Balance response are
  closed-source — this is the best public approximation (a luminance-masked
  split-tone); the Axis-1 oracle validates that defined math, not absolute
  Lightroom fidelity.
- **LR HSL panel baked into the render (Stage 12).** The 8 hue bands (Red,
  Orange, Yellow, Green, Aqua, Blue, Purple, Magenta) × {Hue, Saturation,
  Luminance} — `crs:HueAdjustment*` / `crs:SaturationAdjustment*` /
  `crs:LuminanceAdjustment*`, a PV2012-era field set that appears in real
  LRT-emitted XMPs — are now parsed (`ir.HslBands`), interpolated per frame,
  and applied in the Adobe hexcone HSV domain (`develop_ops.apply_hsl`). Smooth
  overlapping **triangular partition-of-unity** hue-band weights (bands blend,
  never step; all-equal bands collapse to a global adjustment). Per-band
  Luminance is **saturation-gated** so a neutral pixel — whose hue is undefined
  — is never moved by a colour band (a grey wedge stays grey). HSV S is clamped
  to [0,1] on recompose (the `apply_saturation` negative-channel lesson).
  **Identity (all-zero sliders) short-circuits to a byte-exact no-op**, so a
  render with no HSL intent is bit-identical to the prior pipeline and the ΔE
  ship gate (gym 0.026 / rose 0.545 vs `dng_validate`) is provably unaffected.
  Axis-1 oracle: `test_color_oracle.py::test_hsl_matches_independent_oracle`
  (independent scalar reimpl) + wrong-band-centre / wrong-hue-magnitude
  sensitivity legs. **Fidelity caveat:** Adobe's exact band centres, the
  Hue-slider→rotation magnitude, and the HSL-Luminance↔HSV-Value mapping are
  closed-source; these are the best public approximation. The Axis-1 oracle
  validates that *defined* math, not absolute Lightroom fidelity.

### Changed
- **New default emission: `lrtimelapse` — a 16-bit sRGB display TIFF for the
  LRTimelapse round-trip.** This is the format LRT's video renderer re-ingests
  ("Render from Intermediate"), so frames go straight back into LRT for video +
  Motion Blur — the canonical LRT workflow. Display-referred sRGB (Rec.709
  primaries + sRGB OETF, Bradford D50→D65), **embedded sRGB ICC**, strict
  `LRT_00001.tif…` naming, full LRT look baked, self-describing provenance
  metadata. `DEFAULT_PRESET` is now `lrtimelapse`. The scene-linear ACEScg EXR
  masters (`cinema-linear-finished` / `-master`) remain as opt-in targets for
  DaVinci Resolve / ACES (which bypass LRT — no LRT Motion Blur). New writer
  `output.write_tiff_display(colorspace=…)`; refuses a non-sRGB target without an
  ICC to avoid LRT colour/gamma shifts. See `docs/LRT_ROUNDTRIP.md`.
- **Cinema masters now emit scene-linear ACEScg (AP1), not linear Rec.2020.**
  `cinema-linear-finished` / `cinema-linear-master` write half-float DWAB EXR in
  ACEScg (AP1 primaries, ~D60 white) with the OpenEXR `chromaticities` header
  attribute. Rationale: linear Rec.2020 is a *delivery* gamut misused as
  scene-referred and has **no** matching DaVinci Resolve clip Input Color Space
  (only the gamut-agnostic "Linear", which inherits the timeline gamut); ACEScg
  is the standards-aligned scene-referred grading space with a named Resolve
  Input entry. `write_exr_linear_rec2020(colorspace=…)` accepts `"rec2020"`
  (default, back-compat) / `"acescg"` / `"aces2065"`; `"aces2065"` also sets
  `acesImageContainerFlag`. The < 1 ΔE pipeline ship gate is unaffected (output
  colourspace is independent of the validated render). See
  `docs/research/v08-linear-exr-gamut-resolve-nuke.md`.
- **The runtime is now fully Adobe-free (Phase 3).** dnglab (open-source,
  LGPL-2.1) is the **sole** RAW→DNG converter — discovery is
  `$LRT_CINEMA_DNGLAB` → PATH → common installs. The Adobe DNG Converter binary
  discovery and the `$LRT_CINEMA_DNG_CONVERTER` fallback are **removed**
  (`find_dng_converter`, `_DNG_CONVERTER_PATHS`). dnglab is a verified drop-in
  (dnglab-DNG vs Adobe-DNG on the same pipeline+DCP = mean ΔE 0.059, 100 % < 1
  ΔE) and ships Linux/macOS/Windows builds; `--no-dng-convert` remains the
  libraw-direct fallback for boxes with no dnglab binary. DCP auto-detect no
  longer scans an Adobe install directory (`find_dcp_for_camera`,
  `_adobe_dcp_search_roots` removed) — profiles resolve only from the open
  `.npz` roots (`$LRT_CINEMA_PROFILES`, `~/.config/lrt-cinema/profiles/`).
  `--dcp` still accepts a `.dcp` (read by the clean-room `parse_dcp` reader, a
  file-format reader — not an Adobe dependency) or an extracted `.npz`.
  `tools/extract_dcp_library.py` now takes an **explicit** `<source_root>`
  argument instead of hardcoding the Adobe install path. The `dng_validate`
  reference renderer and system `.dcp` profiles remain **test-only** oracles
  (the ΔE ship gate is unchanged). See `docs/PIPELINE.md` §8.

### Security
- **Fixed an EXIF→path-traversal in profile auto-detect (bug #8).** Camera
  Make/Model read from untrusted RAW EXIF is interpolated into the
  extracted-profile filename, so a hostile `Model` (e.g. `x/../../etc/evil`)
  could make `find_extracted_profile_for_camera` probe a path outside the
  profile search root. `_adobe_camera_label` now strips path separators and
  NUL, keeping the label a single contained path segment. (Removing the
  Adobe-install `.dcp` scan closed the sibling sink in the same class — the
  original framing of bug #8.) Regression tests:
  `test_camera_label_strips_path_separators_bug8`,
  `test_find_extracted_profile_no_exif_path_traversal_bug8`.

### Verified (DaVinci Resolve Studio 21, headless — tools/resolve_verify/)
- **ACEScg round-trip:** our ACEScg EXR, ingested via the named "ACEScg" Input
  Color Space → Rec.709 γ2.4, matches our pipeline at **mean ΔE2000 0.64** — the
  switch preserves our validated colour science end-to-end.
- **dnglab** (open, LGPL) is an Adobe-DNG-Converter drop-in: same pipeline+DCP,
  dnglab-DNG vs Adobe-DNG = **mean ΔE 0.059, 100 % < 1 ΔE** → render chain is
  Adobe-free end-to-end (Adobe DNG Converter no longer required).
- **CinemaDNG** honors per-frame `AsShotNeutral`/`BaselineExposure` (genuine
  Bayer mosaic, **no re-mosaic**) but **delegates colour to Resolve's bundled
  DCP** (materially divergent from our 0.79-ΔE science). **Linear DNG** also
  honors per-frame WB/exposure (no re-mosaic) but is dominated by ACEScg-EXR
  (our colour, smaller) and CFA-CDNG (full-sensor raw); **not adopted**.

### Emission verdict
**Do not switch to CDNG/Linear DNG.** ACEScg EXR is the colour-accurate master
(our science; recovery = half-float + Stage-7 overrange). CFA CinemaDNG is the
only full-sensor-raw option but trades away our colour science → offer later as
an *optional* max-recovery preset (needs a `cdng_emit` writer + per-camera
colour characterisation), not a default. See
[`docs/DECISIONS.md`](docs/DECISIONS.md) §3.

## [0.7.1a0] — 2026-05-28

### Added
- **`cinema-linear-master` preset (β; Option B).** Emits half-float
  DWAB EXR at **Stage 7** (post-ExposureRamp), skipping the DCP
  LookTable (Stage 8) + ProfileToneCurve (Stage 9). Preserves the
  HDR headroom that the DCP tone curve otherwise consumes. LR PV2012
  ops (Exposure, Blacks, ToneCurve, Saturation, Vibrance, Contrast)
  still apply on the Stage 7 output, so LRT-authored keyframes bake
  into pixels exactly as γ does — just without the DCP shape applied.
- `apply_adobe_pipeline(stop_after_stage=)` + `render_frame(stop_after_stage=)`
  kwargs accept `7` (β) or `9` (default; γ behaviour). Other values
  raise `ValueError`.
- `STAGE_7_PRESETS` constant exported from `lrt_cinema.presets`.
- Tests: `test_stage_7_emission_rejects_other_stops`,
  `test_stage_7_emission_preserves_more_overrange_than_stage_9` (fixture-
  gated), `test_preset_cinema_linear_master_writes_half_dwab_exr`.
- `tools/v07_fullstack/run_test.py` extended to verify both γ and β
  end-to-end: monotonic per-frame R-mean interpolation under each
  preset, β output materially differs from γ on every frame.

### Fixed
- **β recovery was a no-op.** `cinema-linear-master` advertised "preserves
  HDR headroom for recovery", but the Stage-7 ExposureRamp ran with
  `support_overrange=False`, hard-clamping to 1.0 *before* the emission
  point — zero overrange survived (gym frame: max 1.000, 0 % pixels > 1).
  The pipeline now sets `support_overrange=(stop_after_stage == 7)`, so β
  preserves real recoverable highlights (gym: max 2.0 = +1 stop; the
  half-float container holds ~30 stops). Stage-9 (γ) is unchanged — its
  ProfileToneCurve clamps to [0,1] regardless — so the < 1 ΔE ship gate
  stays bit-identical (gym 0.789, unchanged).
- `test_stage_7_emission_preserves_more_overrange_than_stage_9` now
  asserts actual overrange survival; it previously only checked that the
  outputs "differ", masking the clamp above.

### Verified
- **Emission format is now verified functional headlessly**, replacing the
  manual-Resolve checkpoint that never ran. `tools/verify_emission_format.py`
  proves on the real gym DNG (vs Adobe `dng_validate`): writer is
  bit-exact per channel on 4016×6016 non-square content (kills the
  strided-view garble class on real data, not 16×16 fixtures); half-DWAB
  is 19.5× vs float TIFF; DWAB is visually lossless (mean ΔE 0.25) on real
  content; Stage-7 preserves +1 stop of recovery; end-to-end colour is
  0.789 ΔE vs `dng_validate`.

### Why this exists
The v0.7 spec's Phase 2 (β-XML; Stage 7 EXR + Resolve project sidecar
carrying LRT-authored keyframes) was deferred to v0.8 — Resolve does
not preserve per-frame grade keyframes through any documented import
path (see [`docs/DECISIONS.md`](docs/DECISIONS.md) §4). Option B is the
pragmatic intermediate: the Stage 7 emission point (HDR headroom win)
without the sidecar (which doesn't work). Users who want the v0.6 DCP
shape stay on γ (`cinema-linear-finished`); users who want maximum
recoverability above the tone curve switch to β. Both preserve LRT
keyframes-in-pixels.

## [0.7.0a0] — 2026-05-28

### Added
- **`cinema-linear-finished` preset (γ; new v0.7 default).** Writes
  16-bit half-float OpenEXR with DWAB compression — the cinema
  scene-referred compressed-intermediate standard. 10–18× smaller than
  v0.6 `cinema-aces` PIZ float EXR; same pipeline output (all LRT-
  authored develop ops baked into pixels exactly as v0.6 does).
- `write_exr_linear_rec2020(bit_depth=, compression=)` arguments —
  accepts `"half" | "float"` and `"piz" | "zip" | "dwab"` respectively.
  Default flips to `("half", "dwab")` for v0.7.
- `DEFAULT_PRESET` constant exported from `lrt_cinema.presets`.
- `cinema-linear-finished` becomes the CLI default; `--preset` is now
  optional. Existing `--preset cinema-linear-finished` invocations
  continue to work.
- Test gate: ΔE2000 < 0.5 between DWAB-half and PIZ-half outputs on a
  synthetic gradient+noise fixture (the visually-lossless gate).

### Changed
- `cinema-aces` preset now emits a one-time `DeprecationWarning` per
  process steering users to `cinema-linear-finished`. The preset
  continues to work for one release cycle; planned removal in v0.8.
- Version bumped from `0.6.0a0` to `0.7.0a0`.

### Why this exists
v0.6's emissions were
huge (~292 MiB / frame for `cinema-linear` 32-bit float TIFF, ~100 MiB
for `cinema-aces` PIZ-float EXR). Cinema scene-referred workflows ship
half-float DWAB EXR because it's the size/quality/decode-speed Pareto
front. v0.7.0 swaps to that without changing the upstream render
pipeline.

### What's NOT in v0.7 (β-XML deferred to v0.8)
The spec's Phase 2 — `cinema-linear-master` preset shipping a Stage-7
EXR + per-sequence Resolve XML sidecar carrying LRT-authored keyframes
— is **deferred to v0.8** pending a new carrier format. Empirical
verification (2026-05-28) found Resolve's documented import paths do
not preserve per-frame grade keyframes: FCPXML colour data lands as
static primary corrections only (Manual ~line 50884); Studio scripting
API exposes `SetCDL` / `SetLUT` / `ApplyGradeFromDRX` only, with no
per-frame setter. See [`docs/DECISIONS.md`](docs/DECISIONS.md) §4 for the
finding and what could re-open it. The v0.7.x §2.B free-upgrade
roadmap (X1–X6: HSL, Color Grading wheels, parametric tone, user
masks, Texture, Clarity) is correspondingly deferred — those
increments were architected around the β-XML carrier.

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
