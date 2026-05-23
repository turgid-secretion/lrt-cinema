# Kelvin → RGGB Multipliers — Research Findings

Background for Track A5, [V03_PLAN.md](../V03_PLAN.md). Emitter skips dt's `temperature` module ([xmp_emitter.py:144](../../src/lrt_cinema/xmp_emitter.py)) because LR's `crs:Temperature`+`crs:Tint` can't be honored without per-camera characterization.

## 1. DCP sources

- **Adobe DNG Converter bundle.** macOS: `/Library/Application Support/Adobe/CameraRaw/CameraProfiles/`. Windows: `C:\ProgramData\Adobe\CameraRaw\CameraProfiles\`. Per-user override at `~/Library/...` / `%APPDATA%\...` ([X-Rite KB](https://www.xrite.com/service-support/where_dng_profiles_are_stored_in_the_computer)). D750 included.
- **No clean-licensed GitHub bundle of Nikon DCPs.** `abpy/FujifilmCameraProfiles` is CC-BY-NC-SA (NC blocks us). Wolf3d D750 profiles in [DPReview](https://www.dpreview.com/forums/thread/4012529), unlicensed. Lensfun ships LCPs.
- **rawtoaces does NOT consume DCP.** `--mat-method=metadata` reads matrices from a DNG, not `.dcp` ([README](https://github.com/AcademySoftwareFoundation/rawtoaces)).
- **Legal.** DNG SDK is permissive; DNG Converter EULA does not grant redistribution of bundled `CameraProfiles/*.dcp`. **Ship no DCPs.** Require user-side install; read from local path.

## 2. DCP parsing in Python

- `dcp_decoder` on PyPI: **does not exist**. PyPI `profi-dcp` is PROFINET, unrelated.
- `colour-science`: **no DCP support** ([discussion #1126](https://github.com/colour-science/colour/discussions/1126)).
- `rawpy` exposes `camera_whitebalance`, `color_matrix`, `rgb_xyz_matrix` from LibRaw's `adobe_coeff` tables — **not** external DCP ([API](https://letmaik.github.io/rawpy/api/rawpy.RawPy.html)).
- darktable: **no DCP parser to port.** Issue [#4165](https://github.com/darktable-org/darktable/issues/4165) closed not-planned (2020).
- RawTherapee `rtengine/dcp.cc` is the C++ reference — **GPLv3**, clean-room only.

**Cleanest Python path:** clean-room ~150-line DCP IFD reader against the public [DNG 1.7.1.0 spec](https://helpx.adobe.com/camera-raw/digital-negative.html). DCP is TIFF-IFD; `struct.unpack` over `ColorMatrix1/2`, `ForwardMatrix1/2`, `CalibrationIlluminant1/2`. Algorithm side already in [`colour-hdri.models.dng`](https://colour-hdri.readthedocs.io/en/develop/_modules/colour_hdri/models/dng.html) (`xy_to_camera_neutral`, `matrix_interpolated`) — BSD-3, importable.

## 3. Algorithm

DNG 1.7.1.0 spec, **Ch. 6 "Mapping Camera Color Space to CIE XYZ Space"**:

1. `(K, tint) → xy` chromaticity. Planckian locus for K<4000K, CIE daylight above (darktable convention); tint = perpendicular offset on CIE 1960 uv locus.
2. `xy → XYZ` with Y=1.
3. **Mired-space blend** α from `1e6/CCT(xy)` between `1e6/CCT1` and `1e6/CCT2`, clamped [0,1].
4. `XYZtoCamera = AnalogBalance · CameraCalibration(α) · ColorMatrix(α)`, with `ColorMatrix(α) = α·CM1 + (1−α)·CM2`.
5. `n = XYZtoCamera · XYZ_illuminant`.
6. **Iterate** 1–5 (α depends on xy depends on n).
7. **Multipliers** = `(1/n_R, 1/n_G, 1/n_B)` normalized green=1; Bayer `G2=G1`. Pack `<ffff`.

`ForwardMatrix` is **not** used for WB (downstream camera-RGB → XYZ(D50) only).

## 4. Verification recipe

- LR-rendered TIFF at `crs:Temperature=5500 crs:Tint=0` = ground truth.
- Same NEF via `darktable-cli in.NEF sidecar.xmp out.tif` with computed multipliers in per-image XMP history (not `--style`).
- **ΔE2000** in CIE Lab. Threshold: **mean < 2.0, p95 < 3.5** ([ColorFYI](https://colorfyi.com/blog/what-is-delta-e/)). Mean catches drift; p95 catches localized failures (speculars, deep shadows).

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
| `(K, tint) → xy` mapping | **2–4 days** | **BOTTLENECK** |
| Iterative neutral solver (port `colour-hdri`) | ~0.5 day | LOW |
| IR/emitter plumbing + `--dcp-dir` flag | ~0.5 day | LOW |
| Verification harness vs LR | ~1 day (post-C1) | MEDIUM |

**Bottleneck: LR's (K, tint) → xy curve is undocumented**, reverse-engineered only ([exiftool forum](https://exiftool.org/forum/index.php?topic=11258.0)). darktable's `_temperature_to_XYZ` ([temperature.c](https://github.com/darktable-org/darktable/blob/master/src/iop/temperature.c)) approximates but is not LR-compatible. Closing the gap needs empirical calibration vs ACR's `AsShotNeutral` for a swept (K, tint) grid — half-day shoot + curve-fit, or accept the dt approximation and document residual delta.

**Total: ~5–7 dev-days.** As-shot fallback (§5) is ~half a day and covers the dominant exposure-ramp case. **Recommend shipping fallback in v0.3, gating full DCP path on a calibration milestone in v0.3.x / v0.4** per [V03_PLAN.md:71](../V03_PLAN.md).
