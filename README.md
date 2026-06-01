# lrt-cinema

Self-contained Python implementation of the Adobe DNG 1.7.1 render pipeline,
driven by [LRTimelapse](https://lrtimelapse.com/) XMP develop intent. By default
it emits an **LRTimelapse-ready 16-bit sRGB TIFF sequence** (`LRT_00001.tif…`,
embedded sRGB ICC) — the only format LRT's video renderer re-ingests, so you
take the frames straight back into LRT for video + **Motion Blur** — without
shelling out to Lightroom or any other RAW pipeline. Scene-linear ACEScg OpenEXR
(for DaVinci Resolve / ACES) is an opt-in target. See
[docs/LRT_ROUNDTRIP.md](docs/LRT_ROUNDTRIP.md).

**Status:** Pre-alpha. Color science gates < 1 ΔE2000 mean against Adobe
`dng_validate` on the project test scenes. Workflow polish, third-camera
calibration coverage, and the `stills-finished` AgX preset remain deferred.

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
   → ProfileToneCurve → LR-authored develop ops → ProPhoto → display sRGB
   (default) / scene-linear ACEScg.
4. Write an **LRT-ready 16-bit sRGB TIFF sequence** (`LRT_00001.tif…`, embedded
   sRGB ICC) you load straight back into LRTimelapse for video + Motion Blur.
   (Scene-linear ACEScg EXR for DaVinci Resolve / ACES is an opt-in `--preset`.)

Per-frame color is within < 1 ΔE2000 of `dng_validate` (Adobe's own DNG
SDK reference renderer) on the project's test scenes. See
[docs/PIPELINE.md](docs/PIPELINE.md) for the full as-built pipeline reference
and [CHANGELOG.md](CHANGELOG.md) for the empirical journey from 6.37 ΔE
(darktable) to the in-process Python pipeline.

## Output presets

| Preset | Container | Color space | Notes |
|---|---|---|---|
| `lrtimelapse` | 16-bit sRGB TIFF (embedded ICC), `LRT_NNNNN` naming | sRGB (Rec.709 + sRGB OETF), display-referred | **v0.8 DEFAULT.** The only emission LRT's video renderer re-ingests — take it back into LRT for video + Motion Blur. Full LRT look baked. |
| `cinema-linear-finished` | 16-bit half OpenEXR (DWAB) at Stage 13 | scene-linear ACEScg (AP1) | Scene-linear master for DaVinci Resolve / ACES (bypasses LRT). Full DCP shape baked. |
| `cinema-linear-master` | 16-bit half OpenEXR (DWAB) at Stage 7 | scene-linear ACEScg (AP1) | β. Skips DCP LookTable + ProfileToneCurve for HDR headroom. LR PV2012 keyframes still bake into pixels. Pick this when highlight recovery matters more than the canned DCP look. |
| `stills-finished` | display Rec.2020 (gamma) + AgX | display-referred | **Deferred** — `NotImplementedError`. |

> **Removed in v0.8:** `cinema-linear` / `cinema-aces` — both emitted *linear
> Rec.2020*, a delivery gamut misused as scene-referred (a colour-science error).
> ACEScg (AP1) / ACES2065-1 (AP0) are the only standards-aligned scene-linear
> gamuts; see [CLAUDE.md](CLAUDE.md) §"Colour-space allowlist".

## Requirements

- Python 3.10+
- **dnglab** (open-source, LGPL-2.1) — the RAW→DNG converter, required for the
  < 1 ΔE result. No Adobe software is needed. Install with `brew install
  dnglab` (macOS), `cargo install dnglab`, or grab a Linux/macOS/Windows build
  from https://github.com/dnglab/dnglab. Point `$LRT_CINEMA_DNGLAB` at the
  binary if it isn't on `PATH`. To skip conversion entirely, pass
  `--no-dng-convert` (reads NEFs directly via libraw; expect ~0.5 ΔE
  regression).
- A per-camera DCP profile. Auto-detected from `$LRT_CINEMA_PROFILES` or
  `~/.config/lrt-cinema/profiles/`; pass `--dcp PATH` to supply one explicitly
  (a `.dcp`, read clean-room, or an extracted `.npz`). Populate the profile
  cache from any `.dcp` source you are licensed to use — an Adobe
  CameraProfiles directory if you happen to have one, or profiles built with
  [dcamprof](https://torger.se/anders/dcamprof.html) / RawTherapee — via
  `python3 tools/extract_dcp_library.py <source_root>`.
- Source RAW supported by libraw: NEF, DNG, CR3, ARW, RAF, ORF, RW2, FFF.

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
  --output /path/to/lrt-ready-tiff-sequence
# defaults to --preset lrtimelapse (16-bit sRGB TIFF, LRT_00001.tif…).
# Take the output folder back into LRTimelapse → Render from Intermediate.
```

Power-user knobs:

```bash
lrt-cinema render \
  --input ... --output ... --target resolve \
  --dcp /path/to/camera.dcp \
  --workers 4 \
  --from-frame 0 --to-frame 500 \
  --no-apply-lrt-offsets \
  --no-dng-convert \
  --dry-run
```

See `lrt-cinema render --help` for the full flag surface.

### Speed: `--backend` and `--preview-scale`

The per-pixel colour maths is pure-numpy by default (the colour-exact reference
the ΔE gate measures). Install the optional `numba` extra for **fused multi-core
JIT kernels** on the hot stages (LookTable / HueSatMap cube + tone curve), which
are colour-identical to numpy (max ΔE2000 < 1e-4, far below the 1.0 ship gate):

```bash
pip install "lrt-cinema[fast]"      # or: pip install numba

lrt-cinema render --input ... --output ... \
  --backend numba                   # default 'auto' = numba if installed, else numpy
```

Measured on an Apple M1 Max (10 cores, D750 Camera Standard, full-res 24 MP):

| Path | numpy | numba | speed-up |
|---|---|---|---|
| DCP-render stages (1–9), no grade | 16.9 s | 2.5 s | **6.6×** |
| └ the cube + tone stages alone | 12.7 s | 0.27 s | **~48×** |
| Throughput, 10-frame pool (DCP-render) | 6.9 s/frame | **0.97 s/frame** | **7.1×** (10 workers vs 6) |

**Scope — what's accelerated:** the numba backend covers the **DCP-render
stages (1–9)** + the output encode. The **Stage-12 faithful grade ops**
(Saturation / Vibrance / HSL / Color-Grade) are **not yet accelerated** — they
add the same cost on both backends, so a heavily-graded frame's full-res
speed-up shrinks (e.g. a render with HSL + Color-Grade set: ~26 s → ~14.5 s,
**1.8×**). Accelerating those (same backend, same approach) is the next step;
see [docs/PIPELINE.md](docs/PIPELINE.md) §11.

For rapid grade/sequence iteration the **preview path is the answer** — and
because it downsamples *before* the colour math, it shrinks Stage-12 grading
too, so it stays fast even on heavily-graded frames. Add `--preview-scale
{2,4,8}` (fast 2×2-bin demosaic + downsample): a heavily-graded frame renders
**~18× (scale 4) to ~34× (scale 8)** faster. **Preview output is not
colour-exact** (exempt from the ΔE gate) — for visual iteration, not the LRT
round-trip or final delivery:

```bash
lrt-cinema render --input ... --output ... --preview-scale 4   # ~1/4 res
```

## Scope and non-goals

**In scope:**
- Adobe DNG 1.7.1 reference pipeline within < 1 ΔE2000 of `dng_validate`.
- LRT XMP develop ops with public LR formulas: Exposure2012, Blacks2012,
  ToneCurvePV2012, Saturation, Vibrance, Contrast2012.
- Holy Grail kelvin override + LRT mask-correction per-frame deltas.
- Three output presets (above).

**Out of scope:**
- Lightroom PV5 parametric tone math (Highlights/Shadows/Whites — closed
  source). These fields drop at render.
- Sharpening (`sharpness` is a no-op — sharpening belongs in the grade).
- AgX display transform (`stills-finished` preset — deferred).
- CinemaDNG, ProRes, image-sequence-to-movie muxing.

See [SCOPE.md](SCOPE.md) for per-feature implementation status.

## Validation

End-to-end gate: `tests/test_pipeline.py` renders the project's test
scenes through the pipeline and asserts mean ΔE2000 < 1.0 against
Adobe's own `dng_validate` reference renderer.

Latest measurement (v0.8 head, re-run 2026-05-30, gym + rose vs `dng_validate`):

| Scene | Mean ΔE | P50 | P95 | < 1 ΔE pixels |
|---|---:|---:|---:|---:|
| Gym (D750 Camera Standard) | **0.026** | 0.000 | 0.32 | 100% |
| Rose (D750 Adobe Standard) | **0.545** | 0.577 | 0.90 | 97.8% |

Gym is now an effective bit-match — P50 0.000, 100% of pixels under 1 ΔE. The
drop from the pre-fix 0.789 was a single change: Stage 9 now applies the DCP
ProfileToneCurve as Adobe's hue/saturation-preserving `RefBaselineRGBTone`
(curve the max and min channel, interpolate the middle) instead of per-channel.
The old per-channel tone error fired wherever channels differ (edges and
saturated colour) and was invisible on neutrals, which is why the flat-pixel
median was already 0.000 before the fix. See
[docs/VALIDATION.md](docs/VALIDATION.md).

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
