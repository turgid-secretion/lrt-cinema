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

## Not yet implemented (stubbed)

- Holy Grail exposure ramp logic (multi-segment smooth interpolation)
- Deflicker pass — measurement loop (export-and-measure-luminance with exposure delta writeback). The application path that reads LRT-authored deflicker deltas from XMP IS implemented.
- Smooth (cubic) keyframe interpolation
- Parallel worker pool (currently single-worker only)
- Bundled darktable `.style` files emit operations as structural placeholders only — their `op_params` are intentionally empty pending the calibration pass (`src/lrt_cinema/presets/CALIBRATION.md`). They are not yet loadable as-is.
- Real-world DP review loop (preset tuning against real timelapse footage)

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
