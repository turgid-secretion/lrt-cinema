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

## Validation gap (the "cinema-grade" claim)

The README's "cinema-grade color" wording is currently aspirational, not measured. The bulletproof automated test for that claim is a ColorChecker ΔE2000 regression against published patch reference values; the methodology, first-class references (ACES TC, OCIO, ITU/SMPTE, X-Rite, `colour-science`), and an honest assessment of what is and is not automatable live in [docs/VALIDATION.md](docs/VALIDATION.md). Today the test would fast-fail because the emitter drops 9 of 12 parsed develop ops and the temperature module emits neutral multipliers regardless of source kelvin — see the "Emitted vs parsed DevelopOps" table above. The test is mechanical to implement; pre-calibration it serves as the CI gate that quantifies the gap.

## Calibration items (schema TBR against real LRT samples)

The following parser schemas are the project's current contract but have NOT been validated against XMP emitted by a real LRTimelapse demo build. The synthetic fixtures under `tests/fixtures/` define the schema; when real samples land, both the parser constants (`xmp_parser.LRT_NS_HINTS`) AND the fixtures need updating in lockstep.

- LRT keyframe marker attribute (`lrt:keyframe`)
- LRT deflicker offset attribute (`lrt:deflickerExposure`)
- LRT Holy Grail ramp container (`<lrt:HolyGrailRamps>` with `<rdf:Seq><rdf:li lrt:startFrame= lrt:endFrame= lrt:startExposure= lrt:endExposure= lrt:smoothness=/></rdf:Seq>`)

Smooth interpolation uses uniform Catmull-Rom: keyframe spacing (in frame indices) is normalized to t ∈ [0, 1] per segment, so non-uniform spacing yields uniform-CR's known velocity-discontinuity behavior at keyframes. Centripetal CR (alpha-parameterized) is the natural upgrade once real LRT sequences expose a preference.

## Emitted vs parsed DevelopOps

The parser reads the full Camera Raw Settings field set the LRTimelapse XMP carries. The emitter, at v0.1, only writes a subset into the darktable history stack:

| Field | Parsed | Interpolated | Emitted to darktable XMP |
|---|---|---|---|
| `exposure_ev` | yes | yes | yes |
| `temperature_k` | yes | yes | yes (module enabled, but params are neutral 1.0 multipliers — kelvin→multipliers needs the camera's DCP profile, calibration item) |
| `tint` | yes | yes | dropped (depends on temperature calibration) |
| `contrast` | yes | yes | dropped |
| `highlights` | yes | yes | dropped |
| `shadows` | yes | yes | dropped |
| `whites` | yes | yes | dropped |
| `blacks` | yes | yes | dropped |
| `saturation` | yes | yes | dropped |
| `vibrance` | yes | yes | dropped |
| `sharpness` | yes | yes | dropped |
| `tone_curve` | yes | yes | dropped |

Dropped fields are honored once `presets/*.style` files carry calibrated `op_params` (see `CALIBRATION.md`) AND a per-frame style-emission path replaces the current "one bundled style + per-frame XMP override" split. v0.1 emits only the modules whose binary params layout we know is stable: `exposure` (well-known 6-field struct).

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
