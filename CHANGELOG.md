# Changelog

All notable changes to this project will be documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial repo scaffold (Apache-2.0).
- `lrt-cinema render` CLI skeleton with argparse.
- Internal IR: `DevelopOps`, `LRTKeyframes`.
- LRT XMP parser for supported develop ops.
- darktable history-stack XMP emitter for supported develop ops.
- Linear keyframe interpolation engine.
- Smooth (uniform Catmull-Rom) keyframe interpolation, `--interpolation smooth`. Endpoint segments use mirror-extrapolated phantom tangents; 2-keyframe sequences degenerate to linear.
- Holy Grail exposure ramps: `HolyGrailRamp` IR + `apply_holy_grail_ramps()` with smoothstep-blended segments + `--holy-grail apply-lrt-ramps` CLI flag. XMP parser hook against the `<lrt:HolyGrailRamps>` schema defined in `tests/fixtures/synthetic_holy_grail.xmp` (schema TBR pending real LRT samples — see SCOPE.md).
- Single-worker `darktable-cli` subprocess scheduler.
- Three output preset definitions: `cinema-linear`, `cinema-aces`, `stills-finished`.
- GitHub Actions CI for macOS arm64 + Linux x86_64 + Linux aarch64.
- Synthetic XMP test fixtures + unit tests for parser/emitter/interpolation/Holy Grail.

### Known gaps (see SCOPE.md)
- Deflicker pass — measurement loop
- Parallel worker pool
- Bundled OCIO config + darktable cinema-linear `.style`
- Real LRT XMP test fixtures (only synthetic samples today)
- Real-LRT-sample calibration of the Holy Grail / keyframe / deflicker XMP schemas
