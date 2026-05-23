# Kelvin → RGGB Multipliers — Research Findings

Background for Track A5, [V03_PLAN.md](../V03_PLAN.md). Emitter skips dt's `temperature` module ([xmp_emitter.py:144](../../src/lrt_cinema/xmp_emitter.py)) because LR's `crs:Temperature`+`crs:Tint` can't be honored without per-camera characterization.

## 1. DCP sources

- **Adobe DNG Converter bundle.** macOS: `/Library/Application Support/Adobe/CameraRaw/CameraProfiles/`; Windows: `C:\ProgramData\Adobe\CameraRaw\CameraProfiles\` ([X-Rite KB](https://www.xrite.com/service-support/where_dng_profiles_are_stored_in_the_computer)). D750 included.
- **No clean-licensed GitHub bundle of Nikon DCPs** (Fuji-only at `abpy/FujifilmCameraProfiles` is CC-BY-NC-SA, NC blocks us; Wolf3d D750 in [DPReview](https://www.dpreview.com/forums/thread/4012529) unlicensed; Lensfun is LCPs not DCPs).
- **rawtoaces does NOT consume DCP** — `--mat-method=metadata` reads from a DNG, not `.dcp` ([README](https://github.com/AcademySoftwareFoundation/rawtoaces)).
- **Legal.** DNG SDK is permissive; DNG Converter EULA does not grant redistribution of bundled `CameraProfiles/*.dcp`. **Ship no DCPs.** Require user-side install; read from local path.

## 2. DCP parsing in Python

- No PyPI DCP parser exists (`dcp_decoder` is fiction; `profi-dcp` is PROFINET); `colour-science` has no DCP support ([#1126](https://github.com/colour-science/colour/discussions/1126)); darktable doesn't either ([#4165](https://github.com/darktable-org/darktable/issues/4165), closed not-planned).
- `rawpy` exposes `camera_whitebalance`, `color_matrix`, `rgb_xyz_matrix` from LibRaw's `adobe_coeff` tables — **not** from external DCP ([API](https://letmaik.github.io/rawpy/api/rawpy.RawPy.html)).
- RawTherapee `rtengine/dcp.cc` is the C++ reference — **GPLv3**, clean-room only.

**Cleanest path:** ~150-line clean-room DCP IFD reader against the public [DNG 1.7.1.0 spec](https://helpx.adobe.com/camera-raw/digital-negative.html); `struct.unpack` over `ColorMatrix1/2`, `ForwardMatrix1/2`, `CalibrationIlluminant1/2`. Algorithm side in BSD-3 [`colour-hdri.models.dng`](https://colour-hdri.readthedocs.io/en/develop/_modules/colour_hdri/models/dng.html) — importable.

## 3. Algorithm

LR's `(K, tint) → xy` is **not** reverse-engineered — Adobe ships the source in the BSD-licensed [DNG SDK `dng_temperature.cpp`](https://android.googlesource.com/platform/external/dng_sdk/+/master/source/dng_temperature.cpp): **Robertson 1968** procedure, 31-row `(1e6/T, u, v, slope)` table, bracketed search in CIE 1960 uv, linear interp; `kTintScale = -3000.0` so `Tint = -3000 · Duv` (LR ±150 = Δuv ±0.05). **Pure Planckian** — no Planck/CIE-D crossover (that's darktable's convention, not LR). Port = ~50 lines. Cross-refs: [strollswithmydog](https://www.strollswithmydog.com/white-point-cct-tint/), [`colour.CCT_to_uv_Robertson1968`](https://github.com/colour-science/colour), CIE 015:2018 ([ISO/CIE 11664-2](https://www.iso.org/standard/77215.html)).

Full pipeline per DNG 1.7.1.0 spec **Ch. 6**:

1. `(K, tint) → xy` via DNG SDK Robertson port (above); `xy → XYZ` with Y=1.
2. **Mired blend** α from `1e6/CCT(xy)` between `1e6/CCT1` and `1e6/CCT2`, clamped.
3. `XYZtoCamera = AnalogBalance · CameraCalibration(α) · ColorMatrix(α)`, with `ColorMatrix(α) = α·CM1 + (1−α)·CM2`.
4. `n = XYZtoCamera · XYZ_illuminant`.
5. **Iterate** 1–4 (α depends on xy depends on n).
6. **Multipliers** = `(1/n_R, 1/n_G, 1/n_B)`, green-normalized; Bayer `G2=G1`. Pack `<ffff`.

`ForwardMatrix` is **not** used for WB (camera-RGB → XYZ(D50), downstream).

## 4. Verification recipe

- LR-rendered TIFF at `crs:Temperature=5500 crs:Tint=0` = ground truth.
- Same NEF via `darktable-cli in.NEF sidecar.xmp out.tif` with computed multipliers in per-image XMP history (not `--style`).
- **ΔE2000** in CIE Lab. Threshold: **mean < 2.0, p95 < 3.5** ([ColorFYI](https://colorfyi.com/blog/what-is-delta-e/)) — mean catches drift; p95 catches localized failures.

## 5. Fallback: rawpy as-shot WB

`rawpy.camera_whitebalance` = 4-element as-shot multipliers in CFA order. Green-normalize, pack `<ffff` directly. **Works when** LR `crs:Temperature` ≈ as-shot. **Breaks when:**

- **Keyframed WB animation** (golden→blue hour). Freezes the ramp. Unacceptable.
- **Large manual WB regrade** (3200K shot, 5500K edited) — full hundreds-of-mireds cast.
- **Custom-picker WB** (LR eyedropper).
- **Tint changed substantially** from as-shot (independent axis, unrecoverable).

## 6. Effort estimate

| Step | Effort | Risk |
|---|---|---|
| Clean-room DCP IFD parser | ~1 day | LOW |
| `(K, tint) → xy` port from DNG SDK | ~0.5 day | LOW |
| Iterative neutral solver (port `colour-hdri`) | ~0.5 day | LOW |
| IR/emitter plumbing + `--dcp-dir` flag | ~0.5 day | LOW |
| Verification harness vs LR | ~1 day (post-C1) | MEDIUM |

**No real bottleneck.** Residual risks: (a) runtime DCP sourcing (§1 — user-side install), (b) closing verification vs LR ground truth (depends on Track C1 chart shot).

**Total: ~3.5 dev-days.** As-shot fallback (§5) is ~½ day. **Recommend fallback in v0.3, calibrated DCP path in v0.3.x** post-C1 — now small enough to fit v0.3 if prioritized ([V03_PLAN.md:71](../V03_PLAN.md)).
