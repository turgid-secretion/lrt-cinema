# lrt-cinema

Self-contained Python implementation of the Adobe DNG 1.7.1 render pipeline,
driven by [LRTimelapse](https://lrtimelapse.com/) XMP develop intent. Produces
cinema-native intermediates — linear Rec.2020 TIFF or ACES OpenEXR — without
shelling out to darktable, Lightroom, or any other RAW pipeline.

**Status:** Pre-alpha. Color science gates < 1 ΔE2000 mean against Adobe
`dng_validate` on the project test scenes. Workflow polish, third-camera
calibration coverage, and `stills-finished` AgX preset are v0.6.x scope.

**Companion, not fork.** lrt-cinema does not modify or distribute
LRTimelapse. It reads the XMP sidecars LRT writes and routes the develop
intent through an in-process Python renderer. LRTimelapse is a separate
commercial product by Gunther Wegner; this tool is independent.

## What it does

LRTimelapse writes Lightroom-shaped XMP sidecars alongside RAW frames,
including time-series extensions (Holy Grail exposure ramps, deflicker
offsets, mask-correction per-frame deltas, keyframe markers). The canonical
LRT workflow renders those XMPs through Adobe Lightroom Classic.

`lrt-cinema` is an alternative renderer for the same XMP intent:

1. Parse LRT XMP into an internal `DevelopOps` + keyframes representation.
2. Interpolate per-frame develop values from keyframes.
3. Run each frame through an Adobe DNG 1.7.1 reference pipeline:
   demosaic → AsShotNeutral → ColorMatrix/ForwardMatrix → HueSatMap
   → ExposureRamp (carries TotalBaselineExposure) → LookTable
   → ProfileToneCurve → LR-authored develop ops → ProPhoto → Rec.2020
   → TIFF / EXR.
4. Write a frame sequence ready for DaVinci Resolve / ACES timelines.

Per-frame color is within < 1 ΔE2000 of `dng_validate` (Adobe's own DNG
SDK reference renderer) on the project's test scenes. See
[docs/research/v06-architecture.md](docs/research/v06-architecture.md) for
the full pipeline spec and [docs/research/dng-pipeline-findings.md](docs/research/dng-pipeline-findings.md)
for the empirical journey.

## Output presets

| Preset | Container | Color space | Notes |
|---|---|---|---|
| `cinema-linear` | 16-bit TIFF | Linear Rec.2020 | Drop into Resolve; tag clip as Linear Rec.2020 input. |
| `cinema-aces` | 32-bit float OpenEXR (PIZ) | Linear Rec.2020 | Resolve / OCIO ACES IDT (clean 3×3). |
| `stills-finished` | 16-bit TIFF | Rec.2020 (gamma) + AgX | **v0.6.x** — `NotImplementedError` in v0.6. |

## Requirements

- Python 3.10+
- macOS or Windows with Adobe DNG Converter installed (free, from Adobe)
  — required for the < 1 ΔE result. On Linux pass `--no-dng-convert` to
  read NEFs directly via libraw (expect ~0.5 ΔE regression).
- A per-camera DCP profile. Auto-detected from `$LRT_CINEMA_PROFILES`,
  `~/.config/lrt-cinema/profiles/`, or the system Adobe DNG Converter
  install. Pass `--dcp PATH` to override.
- Source RAW supported by libraw: NEF, DNG, CR3, ARW, RAF, ORF, RW2, FFF.

Install Adobe DNG Converter:
- **macOS:** https://helpx.adobe.com/camera-raw/digital-negative.html
- **Windows:** Same URL.
- **Linux:** Not officially supported by Adobe — use `--no-dng-convert`.

## Install

```bash
pipx install lrt-cinema       # once published to PyPI
# or, from source checkout:
pipx install .
```

Runtime deps: `rawpy`, `colour-science`, `scipy`, `tifffile`, `OpenEXR`,
`numpy`, `defusedxml`. All pulled automatically.

## Usage

```bash
lrt-cinema render \
  --input  /path/to/source-and-xmp-folder \
  --output /path/to/output-tiff-sequence \
  --preset cinema-linear
```

Power-user knobs:

```bash
lrt-cinema render \
  --input ... --output ... --preset cinema-aces \
  --dcp /path/to/camera.dcp \
  --workers 4 \
  --from-frame 0 --to-frame 500 \
  --no-apply-lrt-offsets \
  --no-dng-convert \
  --dry-run
```

See `lrt-cinema render --help` for the full surface (9 flags).

## Scope and non-goals

**In scope:**
- Adobe DNG 1.7.1 reference pipeline within < 1 ΔE2000 of `dng_validate`.
- LRT XMP develop ops with public LR formulas: Exposure2012, Blacks2012,
  ToneCurvePV2012, Saturation, Vibrance, Contrast2012.
- Holy Grail kelvin override + LRT mask-correction per-frame deltas.
- Three output presets (above).

**Out of scope (v0.6):**
- Lightroom PV5 parametric tone math (Highlights/Shadows/Whites — closed
  source). These fields drop at render.
- Sharpening (`sharpness` is a no-op — sharpening belongs in the grade).
- AgX display transform (`stills-finished` preset — v0.6.x).
- CinemaDNG, ProRes, image-sequence-to-movie muxing.

See [SCOPE.md](SCOPE.md) for per-feature implementation status.

## Validation

End-to-end gate: `tests/test_pipeline.py` renders the project's test
scenes through the pipeline and asserts mean ΔE2000 < 1.0 against
Adobe's own `dng_validate` reference renderer.

Latest measurement (gym + rose, vs `dng_validate`):

| Scene | Mean ΔE | P50 | P95 | < 1 ΔE pixels |
|---|---:|---:|---:|---:|
| Gym (D750 Camera Standard) | **0.79** | 0.20 | 4.19 | 76.8% |
| Rose (D750 Adobe Standard) | **0.84** | — | — | 69.6% |

## License

[Apache-2.0](LICENSE).

## Acknowledgements

- The LRTimelapse XMP format is the public output of LRTimelapse; this
  tool reads but does not bundle or modify it.
- Adobe DNG SDK 1.7.1 (BSD-3) is the algorithmic reference; `dng_validate`
  is the ground-truth comparator for the test gate.
- RawTherapee's `rtengine/dcp.cc` (GPL, used as algorithmic reference,
  no code copied) clarified Adobe's HSM/LookTable cube application.
- The Blender Foundation's AgX work is the basis for the planned
  `stills-finished` v0.6.x preset.

## Contributing

Bug reports and PRs welcome via GitHub issues.
