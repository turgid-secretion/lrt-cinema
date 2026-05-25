# Implementation Scope

Honest per-feature status of this pre-alpha scaffold.

## Implemented (scaffold)

- Repo structure, Apache-2.0 license, packaging metadata
- CLI entry (`lrt-cinema render ...`) with argument parsing
- Internal IR (`DevelopOps`, `LRTKeyframes`)
- LRT XMP parser — supported ops only (see below)
- darktable history-stack XMP emitter — supported ops only
- Keyframe interpolation — linear mode
- darktable-cli subprocess scheduler — single-worker
- Three output presets (`cinema-linear`, `cinema-aces`, `stills-finished`) — definitions only

## v0.2 (added)

- Smooth (uniform Catmull-Rom) keyframe interpolation, selectable via `--interpolation linear|smooth`. Endpoint segments use mirror-extrapolated phantom tangents; two-keyframe sequences degenerate to linear. Per-field policy: scalars smooth; optional ints (kelvin, tint) smooth then round, single-side-wins fallback when only one bracketing keyframe carries a value; tone curves smooth `(x, y)` independently when bracketing cardinalities match, else fall back to `p1` (same policy as linear).
- Holy Grail exposure ramps:
    - IR — `HolyGrailRamp(start_frame, end_frame, start_exposure_ev, end_exposure_ev, smoothness)` carried as `LRTSequence.holy_grail_ramps`.
    - Math — `apply_holy_grail_ramps()` applies smoothstep `s(t) = t*t*(3 - 2t)` blended with linear by the per-segment `smoothness` parameter; ramp deltas overlay (add) on top of the keyframe-interpolated base, mirroring `apply_deflicker`.
    - Pipeline ordering — Holy Grail FIRST (it's the base exposure intent), deflicker SECOND (per-frame correction on top).
    - Overlap policy — joined/overlapping ramps resolve via last-wins per-frame (the later ramp's delta overwrites the prior), so joined segments at a shared boundary don't double-count.
    - CLI — `--holy-grail none|apply-lrt-ramps` (default `apply-lrt-ramps`).
    - Parser — extracts the ramp list from `<lrt:HolyGrailRamps>` per the schema in `tests/fixtures/synthetic_holy_grail.xmp`.

## Not yet implemented (stubbed)

- Deflicker pass — measurement loop (export-and-measure-luminance with exposure delta writeback). The application path that reads LRT-authored deflicker deltas from XMP IS implemented.
- Parallel worker pool (currently single-worker only)
- Bundled darktable `.style` files emit operations as structural placeholders only — their `op_params` are intentionally empty pending the calibration pass (`src/lrt_cinema/presets/CALIBRATION.md`). They are not yet loadable as-is.
- Real-world DP review loop (preset tuning against real timelapse footage)

## Known environment issues

- **darktable 5.4.1 cask on macOS arm64 fails at `dt_init`** with the misleading "can't init develop system" error. Root cause: the cask's bundled `darktable-cli` links to macOS's system `/usr/lib/libsqlite3.dylib`, which has no ICU extension, while darktable's startup SQL calls `icu_load_collation()`. The cask is also Homebrew-deprecated for an unrelated Gatekeeper issue (disabled 2026-09-01). Workarounds in [docs/VALIDATION.md](docs/VALIDATION.md). Affects orchestration end-to-end tests on macOS; the rest of the pipeline (parser → IR → interpolation → emitter → dry-run) remains testable.

## v0.3 plan

[docs/V03_PLAN.md](docs/V03_PLAN.md) details the next milestone: render-time fidelity to ALL twelve LRT-emitted develop ops (vs the current exposure-only path), risk-ranked work tracks, acceptance gates, and explicit out-of-scope items. Project pivot: stop replicating what LRT does well (interpolation, XMP authoring) and focus exclusively on render-time intermediate-sequence fidelity.

## Validation gap (the "cinema-grade" claim)

The README's "cinema-grade color" wording is currently aspirational, not measured. The bulletproof automated test for that claim is a ColorChecker ΔE2000 regression against published patch reference values; the methodology, first-class references (ACES TC, OCIO, ITU/SMPTE, X-Rite, `colour-science`), and an honest assessment of what is and is not automatable live in [docs/VALIDATION.md](docs/VALIDATION.md). Today the test would fast-fail because the emitter drops 9 of 12 parsed develop ops and the temperature module emits neutral multipliers regardless of source kelvin — see the "Emitted vs parsed DevelopOps" table above. The test is mechanical to implement; pre-calibration it serves as the CI gate that quantifies the gap.

## Calibration items (LRT schema)

### Validated against LRTimelapse Pro 7.5.3 (Mac) — 2026-05-22

- **LRT keyframe marker.** Real LRT uses the standard Adobe `xmp:Rating` attribute on the `rdf:Description` element. `Rating="4"` flags Creative keyframes (from the Keyframes Wizard); `Rating="0"` flags interpolated / normal frames. Other rating values (1–3, 5) are used by LRT for visual-drag markers and Holy Grail keyframes — our parser treats any `Rating>=1` as a keyframe, which matches the convention. Reference fixture: [tests/fixtures/synthetic_real_lrt_keyframe.xmp](tests/fixtures/synthetic_real_lrt_keyframe.xmp).
- **LRT namespace URI.** `xmlns:lrt="http://lrtimelapse.com/"` (no trailing `ns/1.0/`). The earlier `lrt:keyframe` synthetic-fixture schema remains supported via a fallback path in the parser.

### Schema TBR — next calibration target

- **Deflicker + Holy Grail.** Real LRT does NOT use top-level `lrt:*` attributes or an `<lrt:HolyGrailRamps>` element. It encodes both as named entries inside `crs:MaskGroupBasedCorrections`:
  - `CorrectionName="#LRT internal use (Deflicker)"` carries a per-frame `crs:LocalExposure2012` delta
  - `CorrectionName="#LRT internal use (HG)"` carries a per-frame `crs:LocalExposure2012` delta from the Holy Grail ramp
  - `CorrectionName="#LRT internal use (Global)"` carries a per-frame global offset

  Parsing the mask-correction encoding is the next calibration item. Until it lands, the synthetic `lrt:deflickerExposure` and `<lrt:HolyGrailRamps>` schemas remain supported but produce empty input on real LRT XMP. The interpolation engine and IR for both work; only the parser bridge is pending.

Smooth interpolation uses uniform Catmull-Rom: keyframe spacing (in frame indices) is normalized to t ∈ [0, 1] per segment, so non-uniform spacing yields uniform-CR's known velocity-discontinuity behavior at keyframes. Centripetal CR (alpha-parameterized) is the natural upgrade once real LRT sequences expose a preference.

## Emitted vs parsed DevelopOps

The parser reads the full Camera Raw Settings field set the LRTimelapse XMP carries. v0.4 emits an expanded subset into the darktable history stack:

| Field | Parsed | Interpolated | Emitted to darktable XMP | dt module |
|---|---|---|---|---|
| `exposure_ev` | yes | yes | yes | `exposure` |
| `temperature_k` | yes | yes | yes (when explicit kelvin AND a DCP is supplied; DCP color matrices derive RGGB multipliers) | `temperature` |
| `tint` | yes | yes | yes (rides on the temperature emission via DCP kelvin→xy→multiplier math; emits only when temperature_k is also set) | `temperature` |
| `contrast` | yes | yes | dropped (TBR; dt's own LR-import also drops this — PV2012 contrast math is closed-source and the right dt-module target is unsettled) | — |
| `highlights` | yes | yes | dropped (TBR; same — PV2012 highlights is Local-Laplacian-Filter family, dt has no LR-equivalent module) | — |
| `shadows` | yes | yes | dropped (TBR; same — PV2012 shadows is LLF family) | — |
| `whites` | yes | yes | dropped (TBR; LR's whites pivot has no dt-native equivalent) | — |
| `blacks` | yes | yes | **yes** (5-point LUT verbatim from dt's `lr2dt_blacks_table` at src/develop/lightroom.c#L279-L285, SHA 9402c65275; piggybacks on the exposure module's `black` field) | `exposure.black` |
| `saturation` | yes | yes | dropped (TBR; right target is `colorbalancergb` global saturation, large params struct — pending v0.4.x landing alongside HSM) | — |
| `vibrance` | yes | yes | dropped (TBR; same — `colorbalancergb` global vibrance) | — |
| `sharpness` | yes | yes | **yes** (LR Sharpness → dt sharpen.amount via linear scale with default-alignment; LR 25 → dt 0.5, LR 100 → dt 2.0. LR sub-knobs SharpenRadius/Detail/EdgeMasking ignored, consistent with dt's lightroom.c also dropping them) | `sharpen` |
| `tone_curve` | yes | yes | **yes** (`crs:ToneCurvePV2012` → dt tonecurve module, AUTOMATIC_RGB autoscale; LR-authored non-identity curve wins over any DCP-bundled curve) | `tonecurve` |
| DCP ProfileToneCurve | (via --dcp) | n/a | yes (when --dcp and no LR-authored curve; via basecurve module with preserve_colors=MAX) | `basecurve` |
| DCP BaselineExposure | (via --dcp) | n/a | yes (additive on top of ops.exposure_ev) | `exposure.exposure` |

Dropped fields tagged "TBR" emit nothing today; render-time stderr prints a one-line `warning: dropped at emit` when any keyframe carries non-default-non-LR-default intent on those fields. LR defaults like Sharpness=25 and identity ToneCurvePV2012 [0,0]→[1,1] are correctly excluded — they fire on every XMP regardless of user touch and would otherwise produce a permanent false positive on every neutral keyframe.

DCP path: `--dcp` and `--no-auto-dcp` flags control the optional DCP-driven emission. `--dcp` accepts either Adobe `.dcp` files or lrt-cinema's project-defined `.npz` extracted-profile format. When neither is supplied AND auto-detect is enabled (default), the renderer probes the first source RAW's EXIF Make/Model (TIFF IFD0 reader covers NEF/DNG/ARW/RW2/RAF/ORF/FFF) and searches in this preference order:

1. `$LRT_CINEMA_PROFILES` env var (highest priority — typically points at a cloned sister `lrt-cinema-profiles` data repo)
2. `~/.config/lrt-cinema/profiles/` (or `%APPDATA%/lrt-cinema/profiles/` on Windows; honors `$XDG_CONFIG_HOME`) — populated by `tools/extract_dcp_library.py` against the user's Adobe DNG Converter install
3. Adobe DNG Converter install paths (`/Library/Application Support/Adobe/CameraRaw/CameraProfiles/` on macOS; `%PROGRAMDATA%/Adobe/CameraRaw/CameraProfiles/` on Windows; LR Classic bundle as secondary root on macOS) — fallback for users still relying on Adobe at runtime
4. None → no-DCP path with a clear actionable error message

`.npz` is the project's lossless serialization of the DCP fields the renderer consumes (color matrices, baseline exposure, profile tone curve, HSV cubes). Adobe DCP `.dcp` files are NOT redistributed in-repo per `docs/research/KELVIN_MULTIPLIERS_RESEARCH.md`. Users wanting Adobe-free pan-camera coverage either (a) run `tools/extract_dcp_library.py` once against an Adobe DNG Converter install, populating `~/.config/lrt-cinema/profiles/`, or (b) clone the sister `lrt-cinema-profiles` data repo and set `LRT_CINEMA_PROFILES` to its path. Canon CR3 (ISO BMFF) and other non-TIFF RAW formats fall through to the no-DCP path regardless; the user can still pass `--dcp <path>` explicitly.

Engine path: `--engine {dcp,algorithmic}` selects the color-engine pipeline. `dcp` (default) is the DCP-driven path described above. `algorithmic` is the DCP-free alternative pipeline (Phase 1, v0.4): all DCP-derived module emissions (`temperature`, `basecurve`, `lut3d`) are suppressed; the render relies on darktable's libraw-derived defaults for white-balance and input-color matrix, plus the LR-authored ops only (`exposure`, `exposure.black`, `tonecurve`, `sharpen`, `colorbalancergb`). `--engine algorithmic` overrides `--dcp` and `--no-auto-dcp` and emits an info-line documenting the suppression. Use it as a no-DCP baseline or as the substrate for a per-camera correction matrix fitted separately (Phase 2 — `tools/calibrate_camera.py`, not yet shipped).

## Frame ordering

Source RAW frames are sorted lexicographically by filename. Sequences must zero-pad frame indices (`IMG_0001.CR3`, not `IMG_1.CR3`) so the order matches the temporal order. Mixed-width names will sort wrong; the parser does not natural-sort.

## Calibration

The handoff estimate per `IMPLEMENTATION_HANDOFF.md` in the parent project is **8–12 engineer-weeks with two engineers** for a first shippable beta. This scaffold is the week-4 deliverable: parser + emitter + CLI + CI. Weeks 5–8 cover interpolation, deflicker, presets, OCIO, and first release.

## Schema source of truth

The LRTimelapse XMP schema is reverse-engineered from:
- LRTimelapse public documentation
- Sample XMP files emitted by LRTimelapse demo builds
- Prior dtLapse source on PyPI (read for schema reference; not imported)

Synthetic test fixtures under `tests/fixtures/` are flagged as such; first DP-review-loop pass will replace them with real LRT-emitted samples.
