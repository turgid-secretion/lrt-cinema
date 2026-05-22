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
- Single-worker `darktable-cli` subprocess scheduler.
- Three output preset definitions: `cinema-linear`, `cinema-aces`, `stills-finished`.
- GitHub Actions CI for macOS arm64 + Linux x86_64 + Linux aarch64.
- Synthetic XMP test fixtures + unit tests for parser/emitter/interpolation.

### Known gaps (see SCOPE.md)
- Holy Grail exposure ramp logic
- Deflicker pass
- Smooth (cubic) keyframe interpolation
- Parallel worker pool
- Bundled OCIO config + darktable cinema-linear `.style`
- Real LRT XMP test fixtures (only synthetic samples today)
