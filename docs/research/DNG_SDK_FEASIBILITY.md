# Adobe DNG SDK / `dng_validate` feasibility for ACR-equivalent reference rendering

Research only, no code committed beyond this doc.
Context: [V03_PLAN.md](../V03_PLAN.md) Track C2; [VALIDATION.md](../VALIDATION.md) "matches Adobe Lightroom" question.

**BLUF:** `dng_validate` is **NOT** an ACR pipeline executor. It cannot serve as ACR-equivalent ground truth for LRT-authored develop ops. The SDK is usable on macOS arm64 (build spike succeeded) and offers a secondary value as a baseline-DCP reference renderer in linear Rec.2020 32-bit float — but that does not address the ACR-equivalence question that motivated the research.

## 1. SDK status

- **Latest:** DNG SDK 1.7.1 (build 2573, May 2026), per <https://helpx.adobe.com/camera-raw/digital-negative.html>.
- **Best CMake mirror:** `emmcb/adobe-dng-sdk` (1.7.0.0). <https://github.com/emmcb/adobe-dng-sdk>.
- **License:** Adobe DNG SDK License — proprietary-free per ScanCode-LicenseDB; permits redistribution, derivative works, sublicensing royalty-free. Not OSI-approved. For lrt-cinema's intended use as a dev/test tool (not shipped), Apache-2.0 compatibility is fine — we do not redistribute. <https://scancode-licensedb.aboutcode.org/adobe-dng-sdk.html>.
- **macOS arm64:** Adobe's `DNG_ReadMe.txt` declares arm64 tested. Build spike confirmed (§4).

## 2. `dng_validate` does NOT honor XMP develop ops — the fatal finding

`dng_sdk/source/dng_validate.cpp` lines 581–594:

```cpp
// Now that Camera Raw supports non-raw formats, we should
// not keep any Camera Raw settings in the XMP around when
// writing rendered files.
if (negative->GetXMP ())
    {
    negative->GetXMP ()->RemoveProperties (XMP_NS_CRS);
    negative->GetXMP ()->RemoveProperties (XMP_NS_CRSS);
    ...
    }
```

The XMP CRS namespace (`crs:Exposure2012`, `Contrast2012`, `ToneCurvePV2012`, etc.) is stripped. And the `dng_render` class never consumed those properties to begin with — its API exposes only `SetExposure`, `SetShadows`, `SetToneCurve`, `SetWhiteXY`, `SetFinalSpace`. PV2012 parametric tone math (Contrast / Highlights / Shadows / Whites / Blacks / Vibrance) has no SDK entry point.

What `dng_render::Render()` *does* apply: demosaic, white-balance multipliers, camera color matrix, DCP profile tone curve / HueSatMap / LookTable, baseline exposure, ACR3 default tone curve. Confirmed by Adobe staff: <https://community.adobe.com/t5/camera-raw-discussions/dng-validate-and-after-stage-3/td-p/10422184>.

**The Camera Raw PV2012 pipeline lives in closed-source `Camera Raw.plugin` / `acr.dll`, not in the DNG SDK.** No headless executor for it exists.

## 3. Workflow brittleness

| Step | Brittleness | Note |
|---|---|---|
| NEF → DNG (Adobe DNG Converter CLI, macOS) | LOW | Mature, headless since 3.2. |
| DNG + LRT XMP → `dng_validate` → TIFF | **FATAL** | XMP CRS stripped; output is baseline-DCP, not ACR-developed. |
| lrt-cinema → TIFF | LOW | Existing path. |
| Pixel diff | LOW | `oiiotool --diff` or `colour-science.delta_E`. |

The pipeline fails at step 2. The resulting diff would measure dt-vs-Adobe-baseline-DCP, NOT dt-vs-ACR.

## 4. macOS arm64 build attempt (30 min)

`git clone emmcb/adobe-dng-sdk` → `cmake -DCMAKE_BUILD_TYPE=Release ..` → configure clean.

First link failed with undefined Core Foundation / Carbon symbols (`CFTimeZoneCopyDefault`, etc.) — emmcb's CMakeLists doesn't link macOS frameworks. Six-line patch:

```cmake
if(APPLE)
    target_link_libraries(${TARGET} PRIVATE "-framework CoreFoundation" "-framework CoreServices")
endif()
```

Rebuilt clean. `file` reports `Mach-O 64-bit executable arm64`. Help output confirms `-cs2020` (Rec.2020) and `-32` (32-bit float TIFF). Note: binary reports `dng_validate, version 1.6` (utility version string lags SDK 1.7.0). Patch is upstreamable as a one-line PR to emmcb.

## 5. Alternatives

- **LR Classic Lua SDK** — plugins run inside LR's GUI process; no headless mode. Scriptable for ColorChecker-scale (24 frames) and per-op slider sweeps overnight; **zero CI compatibility**. <https://developer.adobe.com/lightroom-classic/>.
- **Bridge / Photoshop AppleScript** — same profile, GUI-bound.
- **Camera Raw plugin CLI** — does not exist publicly.
- **Manual LR export** — viable one-shot for 24-patch ColorChecker; unsustainable for sweep- or timelapse-scale.
- **rawpy / libraw** — open, programmatic, but baseline render only — same ACR-gap as `dng_render`.

## 6. Recommendation

**Do not pursue `dng_validate` as ACR ground truth.** CRS strip + missing PV2012 math rule it out.

**Ceiling on "matches ACR":** with DNG SDK alone, zero (different algorithm). With manual Lua-SDK route, plausibly mean ΔE2000 < 0.5 on identical inputs, but GUI-bound and not CI-able. Byte-equivalence is moot — Adobe does not guarantee bit-stable LR output across versions.

**Invest engineer time in:**
1. **ColorChecker ΔE2000 harness (Track C1)** — the bulletproof claim; no ACR in the loop.
2. **Optional: `dng_validate` as a baseline-DCP reference** (not ACR). NEF → DNG → `dng_validate -cs2020 -32` yields a linear Rec.2020 32-bit float TIFF representing Adobe's reference impl of demosaic + matrix + DCP baseline tone. Useful as a cheap sanity cross-check for darktable's color-matrix work (Track A5 / Kelvin).
3. **Manual LR frame-pair regression as a release-time check, not CI** — the path [VALIDATION.md §a](../VALIDATION.md) already documents.

**Implication for V03_PLAN Track C2:** re-scope from "headless ACR-equivalent for Track A2/A3 calibration" to "baseline-DCP reference for Track A5 verification." Track A2 (PV2012 calibration) needs a different data path — manual LR slider sweeps against grayscale ramps, or accepting documented "looks-similar" divergence + ColorChecker ΔE pass as the defensible claim.
