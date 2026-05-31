# Synthetic-DNG chromatic divergence — root cause (2026-05-30)

`_apply_hsv_cube` / the LookTable is **NOT** the bug. Proven by reading the DNG SDK
at /private/tmp/dng_sdk and by forcing the LookTable to identity on both sides
(chromatic dL2 still ~0.046). Two real causes, both UPSTREAM of the LookTable:

## 1. TONE CURVE APPLICATION  ← PRIMARY, big win
Stage 9 applies the ProfileToneCurve **per-channel**. Adobe applies the
**hue/saturation-preserving** `RefBaselineRGBTone` (dng_reference.cpp:1871):
sort RGB; curve max & min; `mid = min_o + (max_o-min_o)*(mid-min)/(max-min)`.
- Neutrals (r=g=b) identical ⇒ invisible to the neutral gate.
- PROVEN: `rgbtone(adobe_pre) == adobe_post` exactly (all chromatic patches).
- The curve itself (DngSplineSolver) already matches Adobe to 1e-4; only the
  *application* was wrong.
- **Real-scene impact (apples-to-apples, FM path both sides):**
  - gym  0.816 → **0.055** mean ΔE2000 vs dng_validate
  - rose 0.844 → **0.546**
  The documented gym "0.79 demosaic-edge tail" was mostly this: per-channel tone
  diverges from Adobe's hue-preserving tone exactly at demosaic edges (R≠G≠B).

## 2. SYNTHETIC HARNESS ARTIFACT (the ~6 ΔE chromatic number specifically)
- **dnglab strips the ForwardMatrix** when it makes the uncompressed clone.
  DSC_4053.dng embeds FM (= ProPhoto passthrough); DSC_4053_uncomp.dng /
  _synth.dng have **no FM**.
- `dng_validate -profile "Camera Standard"` uses the **embedded** profile. On the
  synth that profile has no FM ⇒ dng_validate renders via the **ColorMatrix-inverse
  path** (real color). Our pipeline uses the **system DCP** whose FM is the ProPhoto
  passthrough ⇒ **WB-only** color (`M_pp@FM=I`). Different bases on saturated colors.
- Adobe's Camera-Matching profiles (Standard/Vivid/Neutral) ALL ship FM == ProPhoto
  passthrough; Adobe Standard ships a real FM. So FM-passthrough → WB base + LookTable
  IS Adobe's intended render for these profiles (gym GT used it ⇒ our FM path = 0.055).
- The synth test therefore compares our-FM-render vs dng_validate-ColorMatrix-render
  = apples-to-oranges. To make it valid, BOTH sides must use the same profile: strip
  the FM from the profile our pipeline renders (matching the FM-stripped synth that
  dng_validate sees), and fix our ColorMatrix path.

## 3. ColorMatrix path is independently broken (latent)
Our Stage-3 no-FM branch (pipeline.py:476-482) lacks Adobe's `MapWhiteMatrix` D50
Bradford adaptation, so it maps neutral→scene-white not D50 (neut 7.467 when used).
Adobe `dng_color_spec::SetWhiteXY` no-FM branch:
  whiteXY = NeutralToXY(asn);  CM = interpColorMatrix(kelvin(whiteXY))   # ~5647K, not 5500
  PCStoCamera = CM @ MapWhiteMatrix(D50_xy, whiteXY);  PCStoCamera /= max(PCStoCamera @ XYtoXYZ(D50_xy))
  CameraToPCS = inv(PCStoCamera);  xyz = CameraToPCS @ camera
MapWhiteMatrix = linearized Bradford (dng_color_spec.cpp:22).

## VALIDATED end-to-end (synth, FM stripped so both sides use ColorMatrix):
ColorMatrix-adaptation path + hue-preserving tone → chroma mean **0.052**, max 0.209,
neutral 0.000 (was 6.114 / 8.032). Remaining 0.05 = 1d_table quantization floor.

## Fixes to ship
1. pipeline.py Stage 9 → hue-preserving RefBaselineRGBTone (PRIMARY).
2. dcp.py → MapWhiteMatrix + ColorMatrix CameraToPCS helper; pipeline.py Stage-3
   no-FM branch → use it (+ asn-derived kelvin).
3. test_synthetic_dng.py → strip FM from the profile (match dnglab/dng_validate) so
   the comparison is like-for-like; correct the "LookTable is the suspect" docstring.
Gate check: gym 0.055, rose 0.546 — both well under the 1.0 ship gate.
