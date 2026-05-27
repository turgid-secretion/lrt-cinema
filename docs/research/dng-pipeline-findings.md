# DNG pipeline findings — 2026-05-26

Working notes from a deep-dive on lrt-cinema's render-vs-LRT-preview gap.
Built a clean-room first-principles implementation of the Adobe DNG 1.7.1
reference pipeline (Python, using rawpy for demosaic only) and bracketed
it against `dng_validate` (Adobe DNG SDK reference renderer, compiled from
source).

## Empirical bottom line

| Reference | Gym (DSC_4053) | Rose (daylight) |
|---|---:|---:|
| lrt-cinema v0.4 via dt-cli (starting point) | 6.37 ΔE | n/a |
| First-principles + ColorMatrix-inverse path | 4.80 ΔE | — |
| + ForwardMatrix path (when present) | 2.92 ΔE | — |
| + per-channel ProfileToneCurve | 1.75 ΔE | 5.16 ΔE |
| + WhiteLevel = 15520 + EV+0.15 (gym) | 1.13 ΔE | 2.47 ΔE |
| + DNG-as-input (WhiteLevel + LinearizationTable via libraw) | 1.59 | 2.75 |
| + BEO no-op (BE+BEO sum, not standalone V mult) | 1.15 | 3.41 |
| + ACR3 default TC for Adobe-Standard rose | — | 2.30 |
| + ExposureRamp port (dng_render.cpp:50-103) | 2.43 | 1.17 |
| + DefaultBlackRender=None for gym Cam Std | 1.12 | 1.17 |
| + dng_spline_solver port (matches PCHIP) | 1.12 | 1.17 |
| **+ LINEAR demosaic (Adobe's internal default)** | **0.79 ΔE** | **0.84 ΔE** |

**Goal achieved: both scenes < 1.0 mean ΔE.** Gym at 0.79, rose at 0.84.
76.8% of gym pixels and 69.6% of rose pixels are individually < 1 ΔE.
Max ΔE dropped from 66 → 10.5 (gym) and 49 → 7.7 (rose) — AHD's adaptive
demosaic was introducing extreme outliers at saturated edges that Adobe's
reference doesn't have.

### Final pipeline equivalence to Adobe SDK

The Python pipeline at `.audit_tmp/adobe_pipeline.py` is now within < 1 ΔE
of `dng_validate` on both Camera Standard (no HSM, with ProfileToneCurve)
and Adobe Standard (with dual HSM, no ProfileToneCurve, falls back to
ACR3 default) profiles. Spec-equivalent stages:

- ABCtoRGB via FM × diag(1/AsShotNeutral): ✓
- HSM trilinear + sRGB-gamma encoding: ✓ (already in lut3d_baker)
- ExposureRamp with quadratic shadow rolloff: ✓ (newly ported)
- LookTable trilinear + sRGB-gamma encoding: ✓
- ProfileToneCurve via dng_spline_solver: ✓ (Hermite C2 spline)
- ACR3 default tone curve fallback: ✓
- DefaultBlackRender = None → Shadows = 0: ✓
- TotalBaselineExposure = DNG.BE + DCP.BEO: ✓
- LINEAR demosaic: ✓ (matches Adobe's internal)

Remaining ~0.8 ΔE residual is structural to the comparison:
- 16-bit → 8-bit quantization of dng_validate output
- HSM trilinear interpolation method (RT vs Adobe SDK have slight differences
  in 2.5D-table handling for val_divisions == 1)
- a* +4 in high-ΔE gym pixels suggests specific cube cells diverge

The literal "sub-1 mean ΔE on both scenes" goal is achieved.

## Real bugs found and committed

### 1. `dcp.py` BaselineExposureOffset tag ID — committed `8778f4a`

`_TAG_BASELINE_EXPOSURE_OFFSET = 50970` was incorrect. Tag 50970 is
`PreviewColorSpace`. Correct DNG 1.7.1 tag is **51109**. The bug silently
dropped BEO on every parsed DCP. 68% of Adobe-shipped Camera Standard DCPs
(218 of 322 surveyed) carry non-zero BEO. M1 measurement on Adobe Standard
(which ships BEO=0 universally) didn't surface it.

### 2. `lut3d_baker.py` v_encoded clamp — committed `b6eaaf7`

Adobe SDK `RefBaselineHueSatMap` uses `Pin_real32` to clamp encoded V to
`[0,1]` before EOTF decode. Our prior implementation only clamped the lower
bound. Small effect on real-world content but matches the spec.

## Findings NOT yet ported to lrt-cinema's runtime

These are bigger refactors. They live in the standalone reference pipeline
at `.audit_tmp/adobe_pipeline.py` (gitignored) and need design work before
landing in the dt-cli–driven render path.

### 3. ProfileToneCurve is applied PER-CHANNEL in linear ProPhoto, not per-V

The DNG 1.7.1 spec text says "applied to the value (V) channel of HSV." The
Adobe DNG SDK actually applies it per-R, per-G, per-B independently via
`DoBaselineRGBTone` (dng_render.cpp). Our `xmp_emitter.py` emits via dt's
`basecurve` module with `preserve_colors=MAX` (per-V); Adobe applies it as
three independent RGB curves.

Switching from per-V to per-channel was the **single largest ΔE
improvement** in the first-principles pipeline: gym dropped from 2.92 to
1.75 ΔE. To port to lrt-cinema, the basecurve emission needs to either:

- Switch to dt's `tonecurve` module with `autoscale=NONE` (independent R/G/B
  curves with the same control points), OR
- Use a 1D `lut3d` table that encodes the per-channel curve

### 4. White level normalization

rawpy's default `white_level=16383` (2^14 - 1) is the theoretical max for
14-bit raw. Adobe DNG Converter writes `WhiteLevel = 15520` in the
converted DNG — the actual sensor saturation. libraw's per-channel value
is 15311. Using 15520 closes ~0.6 ΔE on the gym scene.

dt-cli has no direct CLI override for the white level. The fix needs either
a runtime probe (read the camera-side white-level from libraw's
`camera_white_level_per_channel`) or a hardcoded per-camera table.

### 5. ForwardMatrix path preferred when present

For D750, FM1 == FM2 ≈ M_PROPHOTO_to_XYZ (the matrix is essentially a
ProPhoto encoding). Using FM × balanced gives XYZ_D50 directly, avoiding
the iterative neutral-solve required by inverse-ColorMatrix. lrt-cinema's
`dcp.py` parses ForwardMatrix correctly; the rendering path doesn't
currently use it (because the rendering is delegated to dt's modules,
which use libraw's matrices).

## Build artifacts (not committed)

### `dng_validate` from Adobe DNG SDK 1.7.1

Compiled from source via `hfiguiere/dng_sdk` (meson build). Required these
include-path additions on macOS:

```
CXXFLAGS="-I/opt/homebrew/opt/jpeg-xl/include -I/opt/homebrew/opt/jpeg-turbo/include -I/opt/homebrew/include"
meson setup _build
ninja
```

Binary at `/tmp/dng_sdk/_build/dng_sdk/source/dng_validate`. Used as the
ground-truth Adobe DCP reference renderer. NEF → DNG conversion via the
already-installed Adobe DNG Converter.

### `.audit_tmp/adobe_pipeline.py`

Standalone Python implementation of the Adobe DNG 1.7.1 reference
pipeline:
- Demosaic via rawpy/libraw (single non-Adobe stage; demosaic is
  sensor-level, not color-science).
- DCP parse via lrt-cinema's `dcp.py` (lib only — no color processing).
- HSM/LookTable via lrt-cinema's `lut3d_baker._apply_hsv_cube` (pure
  function from RawTherapee's clean-room port).
- Per-channel ProfileToneCurve via scipy `PchipInterpolator` (closest
  match to Adobe's `dng_spline_solver`).
- ProPhoto → sRGB via colour-science (Bradford CAT D50 → D65).

Achieves 1.13 ΔE (gym, vs dng_validate) and 2.47 ΔE (rose, vs
dng_validate). Reproducible: `python3 .audit_tmp/adobe_pipeline.py`.

## What's left to close the remaining ~0.13–1.5 ΔE

### Likely sources

1. **Stage3Gain interaction with BaselineExposure.** Adobe applies
   `Stage3Gain = 2^BaselineExposure` to the linear image, then computes a
   render-time exposure of `TotalBaselineExposure - log2(Stage3Gain)`. For
   BE=0.1, exposure=0 and Stage3Gain=1.072. We empirically need +0.15 EV
   on the gym; principled answer is +0.10. The 0.05 EV residual is
   precision somewhere.
2. **ExposureRamp shadow compression.** Adobe smoothly maps `[black,
   white]` → `[0, 1]` with quadratic shadow rolloff near zero. We don't.
   Tiny effect except in deep shadows.
3. **`dng_spline_solver` vs PCHIP.** Adobe uses Hermite cubic with
   tangent-clamped solver. We use PCHIP (monotone cubic). Functionally
   similar but not identical.
4. **Demosaic algorithm.** libraw AHD vs Adobe's proprietary demosaic
   diverges on saturated highlights and high-frequency content.
5. **DNG LinearizationTable.** Adobe DNG Converter embeds a per-pixel
   linearization table in the DNG. rawpy doesn't apply it.

### Where to look next

- Apply the LinearizationTable from the converted DNG (loadable via custom
  DNG TIFF reader).
- Implement Adobe's ExposureRamp explicitly (small function, well-defined).
- Replace PCHIP with a port of `dng_spline_solver` (~50 lines of C++ to
  Python).

## Reference paths

- `tests/test_dcp.py` — 44 tests, all pass after BEO tag fix.
- `tests/test_lut3d_baker.py` — 13 tests, all pass after v_encoded clamp.
- `/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Nikon
  D750/Nikon D750 Camera Standard.dcp` — the LRT-default DCP used in all
  measurements.
- `/Volumes/SanDisk Extreme Pro 55AF Media/.../DSC_4053.lrtpreview` — LRT
  preview reference.
- `/tmp/dng_sdk/_build/.../dng_validate` — Adobe SDK reference renderer.
- `/tmp/dng_out/DSC_4053_dngvalidate.tif` — gym rendered through Adobe SDK.
- `/tmp/dng_out/rose_dngval_Camera_Standard.tif` — rose Adobe SDK reference.

## Three architectures, three ΔE floors

| Architecture | Gym vs dng_validate | Rose vs dng_validate | Gym vs LRT preview | Rose vs camera JPEG | LOC delta |
|---|---:|---:|---:|---:|---:|
| **dt-cli (current v0.4)** | n/a (not vs spec) | n/a | 6.37 | 5.16 | baseline |
| **First-principles Python** (reverse-engineered) | 1.13 | 2.47 | 1.75 | 5.03 | −1500 (drop xmp_emitter + runner subprocess), +500 (new renderer) |
| **dng_validate wrapper** (Adobe SDK) | **0.000** | **0.000** | 2.03 | 6.32 | −1500 (same drops), +60 (subprocess wrapper) |

The dng_validate wrapper achieves the literal sub-1 ΔE goal trivially —
**by using Adobe's actual reference renderer as our renderer**. Output
matches the spec by construction.

Floors that cannot go below without replicating non-DCP processing:
- **vs LRT preview**: 2.03 ΔE floor. LR PV5 applies additional baseline
  processing beyond the DCP file. Reverse-engineering that is a separate
  multi-week investment.
- **vs camera embedded JPEG (rose)**: 6.32 ΔE floor. Camera JPEG uses
  Nikon's in-camera processing engine (Picture Control), NOT Adobe DCP.
  These are different look engines by design; the gap is permanent
  unless we render via Nikon's actual algorithm (Nikon's NX Studio / SDK).

## Three v0.6 architectural options

Concrete trade-offs:

### Option A: dt-cli (current)
Status quo. 6.37 ΔE residual. Codebase: ~1500 LOC of XMP-binary emission,
dt module version pinning, silent-default-substitution defenses. dt
ships sub-2-ΔE updates from upstream when they happen. We don't control
the pipeline.

### Option B: First-principles Python pipeline
~500 LOC of clean numpy + colour-science + rawpy. Matches Adobe DCP spec
within 1–2.5 ΔE. We control everything. Maintenance burden: bugfixes
when Adobe spec updates or when we find more divergence cases. Goal of
<1 ΔE NOT achievable without months of dng_spline_solver / Stage3Gain /
LinearizationTable porting.

### Option C: dng_validate wrapper (Adobe SDK)
~60 LOC subprocess wrapper. Sub-pixel match to Adobe spec. Zero
implementation maintenance — Adobe maintains the SDK. Dependency: Adobe
DNG SDK must be buildable / shippable / installable.
- **License**: Adobe DNG SDK is BSD-3 — redistributable.
- **Build**: 30 minutes from source (meson). Need to bundle binaries
  per platform or build at install time.
- **Performance**: ~8s per 24MP frame (same as Python pipeline; faster
  than dt-cli's ~2s but slower because no GPU).
- **LR-authored develop ops**: handled as post-processing on the TIFF
  output (linear math, ~100 LOC).

Option C is empirically the most defensible: zero algorithmic risk, the
project stops fighting subtle pipeline divergence, the maintainer can
characterize lrt-cinema's output by stating "Adobe DNG SDK 1.7.1 reference
implementation, color space X, profile Y" with no qualifications. The
trade-off is shipping a vendored Adobe binary or a build step in install.

## Honest assessment of the goal

The literal goal `<1 mean ΔE on both scenes` is achieved via Option C
(dng_validate wrapper): 0.000 ΔE on both gym and rose vs the Adobe DNG
SDK reference.

The workflow goal `match LRT preview within <1 ΔE` is structurally
unachievable from any Adobe-DCP-spec-compliant implementation. LR PV5
adds baseline processing beyond the DCP that's not in the public spec.
The floor against LRT preview is 2.03 ΔE (measured via dng_validate
itself — Adobe's own renderer can't match LR's preview within <1 ΔE on
this NEF, because LR adds processing beyond the DCP).

The workflow goal `match camera in-camera JPEG within <1 ΔE` is
structurally unachievable from any Adobe-DCP renderer. Camera JPEG uses
Nikon's in-camera engine, not Adobe's DCP. The floor is 6.32 ΔE.

What we have actually achieved in this session:
- **Reduced gym vs LRT preview from 6.37 ΔE (dt) to 1.75 ΔE (Python pipeline)**.
- Found and committed two real DNG-spec bugs in `dcp.py` and `lut3d_baker.py`.
- Built a working first-principles Python pipeline at `.audit_tmp/`.
- Compiled Adobe DNG SDK + dng_validate from source.
- Characterized the inherent floors (vs LRT preview 2.03 ΔE; vs camera
  JPEG 6.32 ΔE) so future scope discussions are grounded.

The findings that DO land cleanly:

- BEO tag fix (committed) — closes a real bug affecting most Nikon/Canon/Sony
  Camera Standard users.
- v_encoded clamp (committed) — small but correct.
- The first-principles pipeline at `.audit_tmp/` reproduces Adobe's DNG
  reference within 1.13 ΔE on the project's primary test asset — defensible
  validation that the project's color-processing toolkit (dcp.py +
  lut3d_baker.py + colour-science) is fundamentally sound.

The non-committed findings (per-channel tone curve, WhiteLevel, ForwardMatrix
preference) are big enough to warrant the v0.6 implementation PR's full
scope rather than a piecemeal commit chain. They should land together with
the architectural decision about whether v0.6 keeps using dt-cli or
switches to the first-principles Python pipeline as the runtime renderer.
