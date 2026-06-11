# lrt-cinema

Self-contained Python implementation of the Adobe DNG 1.7.1 render pipeline,
driven by [LRTimelapse](https://lrtimelapse.com/) XMP develop intent. By default
it emits an **LRTimelapse-ready 16-bit sRGB TIFF sequence** (`LRT_00001.tif…`,
embedded sRGB ICC) — the only format LRT's video renderer re-ingests, so you
take the frames straight back into LRT for video + **Motion Blur** — without
shelling out to Lightroom or any other RAW pipeline. Scene-linear ACEScg OpenEXR
(for DaVinci Resolve / ACES) is an opt-in target. See
[docs/archive/LRT_ROUNDTRIP.md](docs/archive/LRT_ROUNDTRIP.md).

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
[docs/archive/PIPELINE.md](docs/archive/PIPELINE.md) for the full as-built pipeline reference
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
  --render-intent {faithful,perceptual} \
  --demosaic {linear,menon,rcd,mlri,dcb,ahd,...} \
  --capture-sharpen {off,xmp,acr} \
  --master-look {bake,defer} \
  --deflicker-scale 1.0 \
  --highlight-recovery \
  --no-apply-lrt-offsets \
  --no-dng-convert \
  --dry-run
```

See `lrt-cinema render --help` for the full flag surface.

### Speed: `--backend` and `--preview-scale`

The per-pixel colour maths is pure-numpy by default (the colour-exact reference
the ΔE gate measures). Two optional accelerated backends — colour-identical to
numpy (well under the 1.0 ΔE ship gate), gated behind optional deps:

- **`numba`** (`pip install "lrt-cinema[fast]"`) — fused multi-core **CPU** JIT
  kernels for the DCP-render hot stages; covers every preset/intent; the `auto`
  default when installed.
- **`mlx`** (`pip install "lrt-cinema[gpu]"`, **Apple Silicon**) — runs the
  WHOLE faithful sRGB render on the **Metal GPU**, including the Stage-12 grade,
  so it wins biggest on graded frames. Faithful sRGB only (falls back to
  numba/numpy for EXR/perceptual/unsupported profiles).

```bash
lrt-cinema render --input ... --output ... --backend mlx     # Metal GPU
lrt-cinema render --input ... --output ... --backend numba   # multi-core CPU
# default --backend auto = numba if installed, else numpy
```

Measured on an Apple M1 Max (10 cores, D750 Camera Standard, full-res 24 MP):

| Path | numpy | numba (CPU) | mlx (GPU) |
|---|---|---|---|
| DCP-render only (no grade), 1 frame | 16.9 s | **2.5 s (6.6×)** | 1.16 s (2.1×) |
| └ the cube + tone stages alone | 12.7 s | 0.27 s (**~48×**) | — |
| **Heavily-graded frame** (HSL + ColorGrade + …) | ~26 s | **3.0 s (8.8×)** | **1.54 s (9.1×)** |
| **Graded sequence throughput** | — | ~3 s/frame | **1.0 s/frame (7.9×, 3–4 workers)** |

**Why both:** both accelerate the full faithful path *including the Stage-12
grade*, so both are fast on graded frames — numba ~8.8× (CPU, every platform,
bit-tight: max ΔE 1.6e-4), mlx ~9.1× (Apple GPU, max ΔE ~3e-3). Per-kernel the
GPU only *ties* the CPU (the LookTable gather is memory-bandwidth-bound and the
M1's CPU+GPU share one bus); mlx pulls slightly ahead by keeping everything
on-device, and scales better in a pool (CPU demosaics while the GPU renders). A
CPU-pool + GPU-lane *split-frame* scheduler was measured and **rejected**
(counterproductive). See [docs/archive/PIPELINE.md](docs/archive/PIPELINE.md) §11.

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
- LRT XMP develop ops: Exposure2012, Blacks2012, ToneCurvePV2012, Saturation,
  Vibrance, Contrast2012, HSL (8 bands), Color Grading / Split Toning — plus,
  on the perceptual intent, Highlights/Shadows/Whites (scene-referred
  DR-compression approximation) and Texture/Clarity (edge-aware).
- Capture sharpening (clean-room ACR-style USM, `--capture-sharpen {off,xmp,acr}`,
  default off; tuning constants not yet validated against Lightroom output).
- Holy Grail kelvin override + LRT mask-correction per-frame deltas +
  `--deflicker-scale`.
- Output presets (above); demosaic selection via `--demosaic`
  (linear default; menon/rcd/mlri + libraw algorithms opt-in).

**Out of scope (current):**
- Exact Lightroom PV5 parametric tone math (Highlights/Shadows/Whites are
  local-adaptive and closed source — bit-matching them is impossible by
  construction; the faithful intent drops them with a warning, the perceptual
  intent approximates them).
- Noise reduction (ColorNoiseReduction / LuminanceSmoothing — not parsed yet),
  lens corrections, local masks (geometry), Dehaze.
- AgX display transform (`stills-finished` preset — deferred).
- CinemaDNG, ProRes, image-sequence-to-movie muxing.

See [SCOPE.md](SCOPE.md) for per-feature implementation status.

## Validation

End-to-end gate: `tests/test_pipeline.py` renders the project's test
scenes through the pipeline and asserts mean ΔE2000 < 1.0 against
Adobe's own `dng_validate` reference renderer.

Latest measurement (gym re-verified 2026-06-10 against a freshly regenerated
`dng_validate` reference; rose last measured 2026-05-30 and currently
**unreproducible** — its fixture is missing, see [CLAIMS.md](CLAIMS.md)):

| Scene | Mean ΔE | P50 | P95 | < 1 ΔE pixels |
|---|---:|---:|---:|---:|
| Gym (D750 Camera Standard) | **0.026** | 0.000 | 0.32 | 99.99% |
| Rose (D750 Adobe Standard) | 0.545 (stale) | 0.577 | 0.90 | 97.8% |

Gym is now an effective bit-match — P50 0.000, 100% of pixels under 1 ΔE. The
drop from the pre-fix 0.789 was a single change: Stage 9 now applies the DCP
ProfileToneCurve as Adobe's hue/saturation-preserving `RefBaselineRGBTone`
(curve the max and min channel, interpolate the middle) instead of per-channel.
The old per-channel tone error fired wherever channels differ (edges and
saturated colour) and was invisible on neutrals, which is why the flat-pixel
median was already 0.000 before the fix. See
[docs/archive/VALIDATION.md](docs/archive/VALIDATION.md).

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

Bug reports and PRs welcome via GitHub issues. PRs are merged only with
explicit owner sign-off, and contributions must be the contributor's own work
(no copied GPL code) — this keeps the copyright clean so the project retains
the option to relicense or dual-license later.
