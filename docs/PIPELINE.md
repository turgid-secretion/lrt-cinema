# PIPELINE.md — canonical render-engine reference (as-built)

**This is the single source of truth for the lrt-cinema render engine.** It is
the *as-built* map: every render stage, the file/function that owns it, the
colour space in→out, what is load-bearing, the known gotchas, and where tests
tap in. CLAUDE.md is the index of invariants; this is the engine itself.

> **Status:** repo-truth as of **2026-05-30**. This is the canonical as-built
> engine reference; the 2026-05-27 pre-implementation spec it supersedes
> (`v06-architecture.md`, now-wrong on Stage 9) was archived in the Phase-4 doc
> reduction — see [§11](#11-document-map--what-supersedes-what).

---

## 0. The contract (read before you touch the engine)

Any LLM or human changing the render engine — `src/lrt_cinema/{pipeline,dcp,lut3d_baker,develop_ops,output}.py`
— **must read this file first**, then:

1. **Preserve every documented invariant** unless you have *primary-source
   evidence* it is wrong. Primary source = the Adobe DNG SDK 1.7.1 at
   `/private/tmp/dng_sdk/dng_sdk/source/` (the `dng_validate` oracle is built
   from it), a reproducible ΔE measurement vs `dng_validate`, or the DNG 1.7.1
   spec. "It looks cleaner" / "a self-test passes" is **not** evidence.
2. **If new evidence overturns an invariant**, update *this doc* AND the guarding
   test in the same change, citing the evidence (SDK `file:line`, or the
   measurement). Do not silently diverge from repo-truth.
3. **Neutrals passing ≠ correct.** This is the single most expensive trap in this
   codebase (it hid a ~0.8 ΔE error for months). A grey patch (`r=g=b`, `sat=0`)
   is **blind** to: the tone-curve application *mode* (per-channel vs
   hue-preserving — identical on neutrals), and the camera-matrix *chromatic
   rotation* (any white-preserving matrix maps neutral→neutral). Only **saturated
   colour** exercises those. When you change Stage 3, 5, 8, or 9, verify against
   **chromatic** patches/pixels, never a grey wedge alone.
4. **Run the gates.** `python3 -m pytest -q` (render/ΔE tests skip without
   `/tmp/dng_out` fixtures) and `ruff check .`. The Axis-1 oracle
   (`test_color_oracle.py`, no external deps) is the certitude check for new
   render-math ops — add one when you add an op.

---

## 1. End-to-end flow

```
RAW sequence + LRT XMP sidecars
        │
        │  cli.py::_cmd_render
        ▼
┌─────────────────────────────────────────────────────────────────┐
│ INTENT INGEST (pre-render, no pixels)                            │
│   xmp_parser.parse_sequence   → LRTSequence (keyframes, offsets) │   ir.py = the IR
│   interpolation.materialize_all_frames → per-frame DevelopOps    │
│   + apply_lrt_mask_offsets + apply_deflicker (EV deltas)         │
└─────────────────────────────────────────────────────────────────┘
        │  one _RenderJob per frame  (cli.py::_render_one_frame, ProcessPool)
        ▼
   dng_convert.resolve_render_input :  NEF ──dnglab──▶ DNG (cached)   [Stage 1 input]
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│ pipeline.render_frame → apply_adobe_pipeline   (STAGES 1–9)      │   pipeline.py
│   linear camera RGB ─▶ … ─▶ linear ProPhoto(D50)                 │   lut3d_baker.py
│   stop_after_stage ∈ {3,4,7,9}  ← the taps                       │   dcp.py
└─────────────────────────────────────────────────────────────────┘
        │  linear ProPhoto(D50)
        ▼
   develop_ops.apply_develop_ops          (STAGES 11–12)              develop_ops.py
        │  linear ProPhoto(D50)  (LR PV2012 ops baked on top)
        ▼
   output.write_preset_output             (STAGE 13)                  output.py
        │
        ▼
   LRT_00001.tif (sRGB) | <stem>.exr (ACEScg) | …
```

Stage **10** is intentionally gone (it was a standalone `2^TotalBE` scalar;
TotalBaselineExposure is now folded into Stage 7's ExposureRamp).

---

## 2. Stage-by-stage reference

Spaces: **cam** = linear camera RGB; **XYZ** = CIE XYZ(D50); **PP** = linear
ProPhoto RGB(D50); **HSV** = Adobe hexcone HSV (hue ∈ [0,6)).

| # | Stage | File · function | In→Out | Critical / invariant | SDK ref |
|---|---|---|---|---|---|
| 1 | Demosaic | `pipeline.demosaic_camera_rgb` | DNG→cam | rawpy/libraw **LINEAR** demosaic on the **Adobe-converted DNG** (gets embedded LinearizationTable + WhiteLevel 15520, not NEF's 15311). `output_color=raw`, `user_wb=[1,1,1]`, `no_auto_bright`. | — |
| 2 | White balance | `apply_adobe_pipeline` Stage 2 | cam→cam | `balanced = cam · (1/AsShotNeutral)`, normalized G=1. ASN from libraw `camera_whitebalance`; **Holy-Grail override**: if `DevelopOps.temperature_k` set, ASN ← `kelvin_to_neutral(profile, K)`. | — |
| 3 | Camera→XYZ | `apply_adobe_pipeline` Stage 3 | cam→XYZ | **Two paths — see [§3](#3-the-camera→prophoto-colour-transform-stage-3-4).** FM present → `XYZ = FM · balanced` (mired-blended FM1/FM2 by `scene_kelvin`). **No FM** → `dcp.colormatrix_camera_to_pcs` (ColorMatrix + **MapWhiteMatrix** D50 Bradford). **This is the colorimetric tap (`stop_after_stage=3`).** | `dng_color_spec::SetWhiteXY` |
| 4 | XYZ→ProPhoto | `apply_adobe_pipeline` Stage 4 | XYZ→PP | Fixed matrix `colour ProPhoto RGB matrix_XYZ_to_RGB`. **Colorimetric tap (`stop_after_stage=4`)** — the Axis-2 measurement point. | — |
| 5 | HueSatMap | `lut3d_baker._apply_hsv_cube` (via `_rgb_to_hsv_dcp`/`_hsv_to_rgb_dcp`) | PP→PP (HSV) | Mired-blended HSM cube, applied in HSV. **Absent on D750 Camera Standard** (`hue_sat_map=None`). Trilinear over hue×sat×val, hue WRAPS, sat/val CLAMP. | `RefBaselineHueSatMap` |
| 6 | BaselineExposure | folded into Stage 7 | — | `TotalBE = DNG.BaselineExposure + DCP.BaselineExposureOffset` (NOT DCP.BaselineExposure — Adobe never emits it). Fed as Stage-7 ramp `exposure`. | `dng_negative.cpp:2588-2606` |
| 7 | ExposureRamp | `pipeline.make_exposure_ramp` | PP→PP | Per-channel 3-region piecewise. `shadows=0` when `DefaultBlackRender==1` (None), else 5.0. **`stop_after_stage=7`** = `cinema-linear-master` emission (overrange preserved). | `dng_render.cpp:50-103` |
| 8 | LookTable | `lut3d_baker._apply_hsv_cube` | PP→PP (HSV) | Single cube (no Data2), HSV. D750 Camera Standard's is 90×16×16, `srgb_gamma=True` (V axis sRGB-OETF-encoded before indexing). **Verified equal to the SDK to machine precision — do NOT re-suspect it ([§4](#4-the-looktable--hsm-hsv-cube-stage-5-8)).** | `RefBaselineHueSatMap` |
| 9 | ProfileToneCurve | `pipeline.apply_rgb_tone` (curve = `DngSplineSolver` of ProfileToneCurve, else `apply_acr3_default`) | PP→PP | **HUE/SAT-PRESERVING `RefBaselineRGBTone`, NOT per-channel ([§5](#5-the-tone-curve-stage-9--load-bearing)).** Curve max+min, interpolate mid. `stop_after_stage=9` = default full shaping. | `dng_reference.cpp:1871` |
| 11 | Develop — linear | `develop_ops.apply_stage_11_linear` | PP→PP | LR PV2012, order **Exposure2012 → Blacks2012**. Exposure is the hot path (LRT per-frame EV). No overrange clamp on exposure. | — |
| 12 | Develop — perceptual | `develop_ops.apply_stage_12_perceptual` | PP→PP | Order **ToneCurvePV2012 → Saturation → Vibrance → HSL → ColorGrade → Contrast → Sharpness**. Contrast is **linear-domain pivot 0.18** (not HSV); Sat/Vib/**HSL** are HSV; **ColorGrade** is a linear-domain additive overlay; **Sharpness is a no-op stub**; tone curve here is **per-channel** (this is LR's user curve, a different op from Stage 9). **HSL** = 8 hue bands × {Hue, Sat, Lum}, smooth triangular **partition-of-unity** band weights (`apply_hsl`, `_hsl_band_weights`); luminance **saturation-gated** so neutrals stay neutral. **ColorGrade** = Shadow/Midtone/Highlight/Global wheels (`apply_color_grade`), luminance-masked zero-sum chroma tint + uniform-luminance offset; zone masks are a **partition of unity** (`_color_grade_zone_weights`) shaped by Blending/Balance, taken on a perceptual (sRGB-OETF) luminance; output clamped ≥0. Both **identity short-circuit byte-exact**. Band centres / tint strengths / mask shape are a documented public approximation (LR's are closed). | — |
| 13 | Output | `output.write_preset_output` | PP→delivery | Colour-space convert + encode + container. **Hard allowlist ([§7](#7-emission--the-colour-space-allowlist-stage-13)).** | — |

---

## 3. The camera→ProPhoto colour transform (Stage 3–4)

This is the most regression-prone area; it caused the v0.8 synthetic divergence.

**Two render paths, chosen by whether the profile has a ForwardMatrix:**

- **ForwardMatrix path** (`profile.forward_matrix_1 is not None`):
  `XYZ = FM · diag(1/ASN) · cam`, FM mired-blended between FM1/FM2 by `scene_kelvin`.
- **ColorMatrix path** (no FM) — `dcp.colormatrix_camera_to_pcs`, a port of
  `dng_color_spec::SetWhiteXY` (no-FM branch):
  `whiteXY = NeutralToXY(ASN)` → `CM = interp ColorMatrix at that CCT` →
  `PCStoCamera = CM · MapWhiteMatrix(D50, whiteXY)` (linearized Bradford,
  `dcp.map_white_matrix`, `dng_color_spec.cpp:22`) → normalize so PCS white
  reaches on 1st-channel saturation → `CameraToPCS = inv(PCStoCamera)`.
  **The naive `inv(ColorMatrix)` shortcut is WRONG** — it maps neutral to the
  *scene* white, not D50, tinting every neutral (~7 ΔE). Use `colormatrix_camera_to_pcs`.

**Adobe Camera-Matching profiles ship a ProPhoto-passthrough ForwardMatrix.**
Verified across the D750 family: Camera Standard / Vivid / Neutral all have
`FM == ProPhoto RGB→XYZ` (so `M_xyz→pp · FM = I`, i.e. the FM does NO colour
work — the colour is in the ColorMatrix + LookTable). **Adobe Standard** ships a
*real* FM. So for Camera-Matching profiles the FM path collapses to
white-balance-only colour; that is genuinely what Adobe renders **when the FM is
present** (the LookTable was authored on that base).

**The dnglab FM-strip (harness-critical).** `dnglab convert` **strips the
ForwardMatrix** when it builds an uncompressed clone. So a dnglab-cloned DNG's
embedded profile is FM-less → `dng_validate` renders it via the ColorMatrix +
MapWhiteMatrix path, **a different colour base** than the FM-passthrough path.
`tests/test_synthetic_dng.py` strips the FM from the profile it feeds our
pipeline so "same profile both sides" holds. This is why the synthetic harness
exercises the **ColorMatrix path** while production D750 Camera Standard renders
exercise the **FM-passthrough path** — both are covered, by different tests
([§6](#6-validation-taps--teststage-map)). Full trace:
`docs/research/v08-synthetic-chromatic-rootcause.md`.

**Kelvin.** `scene_kelvin` defaults to 5500 K (`DEFAULT_SCENE_KELVIN`). The
ColorMatrix path does NOT use it — it interpolates CM at `NeutralToXY(ASN)`'s CCT
(~5647 K for the gym ASN). With FM1==FM2 the FM-path kelvin is irrelevant; with
CM1≠CM2 the CM-path kelvin matters, so the ASN-derived white is mandatory.

---

## 4. The LookTable / HSM (HSV cube, Stage 5, 8)

`lut3d_baker._apply_hsv_cube` is a **verified-faithful** port of Adobe
`RefBaselineHueSatMap` (`dng_reference.cpp:1508`). A scalar reimplementation
matches it to machine zero; `_rgb_to_hsv_dcp`/`_hsv_to_rgb_dcp` match
`DNG_RGBtoHSV`/`DNG_HSVtoRGB` (`dng_utils.h`); the sRGB V-axis encode/decode is
the exact `dng_function_GammaEncode_sRGB` (4096-entry `dng_1d_table` ⇒ ~1e-8).
Cube parse order is val-major/hue-mid/sat-minor (`dcp._build_hsv_cube` matches
`ReadHueSatMap`), `skipSat0=false` for the D750 (full plane stored).

**Do not re-diagnose chromatic divergence as "the LookTable."** The v0.8
investigation proved by elimination (identity-cube both sides → divergence
persists) that the LookTable was never the cause. If you suspect it, first
reproduce against a scalar `RefBaselineHueSatMap` reimpl.

---

## 5. The tone curve (Stage 9) — load-bearing

`pipeline.apply_rgb_tone` ports Adobe's **hue/saturation-preserving**
`RefBaselineRGBTone` (`dng_reference.cpp:1871`):

```
sort channels → (max, mid, min)
max_out = curve(max);  min_out = curve(min)
mid_out = min_out + (max_out - min_out) · (mid - min)/(max - min)   # NOT curve(mid)
```

It is **NOT** per-channel. Per-channel rotates hue/saturation on every pixel
where channels differ (edges + saturated colour) and is invisible on neutrals.
Switching from per-channel to this was the v0.8 fix that took **gym 0.789 →
0.026** mean ΔE2000 vs `dng_validate`. The curve *shape* (`DngSplineSolver`,
Hermite C2) already matched Adobe to 1e-4; only the *application mode* was wrong.
Axis-1 oracle: `test_color_oracle.py::test_rgb_tone_matches_independent_refbaseline_oracle`
(independent 7-case port) + `…is_not_per_channel_but_preserves_neutrals`.

Note: Stage 9 (DCP ProfileToneCurve, hue-preserving) and Stage 12
`ToneCurvePV2012` (LR's user curve, per-channel) are **different ops** — do not
unify them.

---

## 6. Validation taps & test→stage map

**Taps** (`apply_adobe_pipeline(..., stop_after_stage=N)`):

| Tap | Returns | Purpose | Used by |
|---|---|---|---|
| 3 | XYZ(D50), post-FM, pre-HSM | colorimetric tap (Axis 2) | `test_colorimetric.py`, `test_pipeline.py` |
| 4 | linear ProPhoto(D50), pre-HSM | colorimetric tap (Axis 2) — the canonical one | `test_colorimetric.py`, `test_pipeline.py` |
| 7 | linear ProPhoto(D50), post-ExposureRamp, overrange kept | `cinema-linear-master` β emission | `test_pipeline.py`, presets |
| 9 | linear ProPhoto(D50), post-ProfileToneCurve | default full DCP shaping | everything else |

**Three validation axes** (deep detail in `docs/VALIDATION.md` §"Validation axes
— never conflate them"; do not duplicate it here):

- **Axis 1 — implementation correctness** (`test_color_oracle.py`): vs an
  independent hardcoded reimpl of our own maths. Expected **~0**. The bug-finder
  and the only axis that certifies a new render-math op. No external fixtures.
- **Axis 2 — absolute colorimetric accuracy** (`test_colorimetric.py`): vs CIE
  truth from ISO-17321-1 spectra, measured at the **colorimetric tap (3/4)**.
  **Nonzero Luther floor** (DCP matrix = least-squares fit; SSF floor 0.81–0.84).
- **Axis 3 — vs `dng_validate`** (`test_pipeline.py` real scenes;
  `test_synthetic_dng.py` flat patches): Adobe's own DNG reference renderer as a
  test-only oracle. Ship gate **mean ΔE2000 < 1.0**.

**Per-test coverage:**

| Test | Covers | Axis | Gated on |
|---|---|---|---|
| `test_pipeline.py` | Stages 1–13 end-to-end; taps 3/4/7/9 | 3 | `/tmp/dng_out` fixtures, `dng_validate`, system DCP |
| `test_synthetic_dng.py` | Stages 1–9 on flat patches (ColorMatrix path) | 3 | + `dnglab` |
| `test_colorimetric.py` | tap 4 (camera→ProPhoto) | 2 | partial (real-DCP subtests need system D5100 DCP) |
| `test_color_oracle.py` | tone/ramp/HSV-cube/matrix/RGB-tone ops | 1 | none (CI) |
| `test_dcp.py` | DCP parse, CM interp, kelvin math, save/load | unit | partial (system DCP subtests) |
| `test_lut3d_baker.py` | sRGB OETF/EOTF, HSV-DCP, `_apply_hsv_cube` | 1/unit | none |
| `test_develop_ops.py` | Stages 11–12 | 1/unit | none |
| `test_output.py` | Stage 13 writers + allowlist | unit | dep-gate (tifffile/OpenEXR) |
| `test_xmp_parser.py`/`test_ir.py`/`test_interpolation.py` | intent ingest + keyframe interp | unit | none |
| `test_dng_convert.py` | NEF→DNG wrapper (mostly mocked) | unit | one real-`dnglab` smoke |
| `test_cli.py` | arg parser / dry-run / inspect | smoke | none |

---

## 7. Emission & the colour-space allowlist (Stage 13)

Presets (`presets/__init__.py`; dispatch `output.write_preset_output`):

| Preset | Space / transfer | Container | Tap | Filename |
|---|---|---|---|---|
| **`lrtimelapse`** (DEFAULT) | **sRGB** (Rec.709 prim, D65, sRGB OETF), 16-bit, embedded sRGB ICC | TIFF | 9 | `LRT_{n+1:05d}.tif` |
| `cinema-linear-finished` | **ACEScg** (AP1, ~D60, linear) | EXR half, DWAB | 9 | `<stem>.exr` |
| `cinema-linear-master` (β) | ACEScg (same writer) | EXR half, DWAB | **7** | `<stem>.exr` |
| `stills-finished` | Rec.2020 + AgX | — | — | **DEFERRED** (`NotImplementedError`) |

The allowlist is enforced in code: scene-linear = `output.EXR_COLORSPACES`
(`acescg`, `aces2065` only); display = `output.DISPLAY_COLORSPACES` (`srgb`,
`adobergb`, `prophoto`, `rec2020`). `write_tiff_display` **refuses a non-sRGB
display target without an explicit ICC**. EXR path Bradford-adapts D50→~D60 and
writes the `chromaticities` attribute (Nuke/OIIO honour it; **Resolve ignores
it** — gamut comes from the clip's Input Color Space). **Linear Rec.2020 is
deliberately absent** (the "Franken-gamut" error). Full allowlist rationale:
CLAUDE.md §"Colour-space allowlist" + `docs/research/v08-linear-exr-gamut-resolve-nuke.md`.

The default `lrtimelapse` TIFF is the only emission LRT's video renderer
re-ingests (LRT → Render from Intermediate → Motion Blur); see
`docs/LRT_ROUNDTRIP.md`.

---

## 8. Intent ingest (pre-render)

- **`xmp_parser.parse_sequence`** → `LRTSequence`. Reads `crs:` develop fields
  into `ir.DevelopOps`. Keyframe authority: `xmp:Rating ≥ 1` (when present) >
  synthetic `lrt:keyframe` > `_has_meaningful_ops`. Honours LRT's
  Auto-Transition (every per-frame XMP carries interpolated values → ingested as
  keyframes → exact-match passthrough). `defusedxml`-hardened; NaN/Inf scrubbed
  to defaults (prevents black frames).
- **`interpolation.materialize_all_frames`** → per-frame `DevelopOps`,
  **piecewise-linear only** (Catmull-Rom deleted, 2026-05-24), constant
  extrapolation at ends. `apply_deflicker` + `apply_lrt_mask_offsets` add EV
  deltas (Holy-Grail/Deflicker/Global).
- **`dng_convert`**: NEF→DNG via **dnglab** — the sole, Adobe-free converter
  (the Adobe DNG Converter binary discovery + fallback were removed in the
  Phase-3 Adobe purge). It does no tag manipulation itself — but **dnglab strips
  the ForwardMatrix** (see [§3](#3-the-camera→prophoto-colour-transform-stage-3-4)).

---

## 9. Dropped / out-of-scope (surfaced, never silent)

- **PV5 basic tone — Highlights / Shadows / Whites**: parsed into `DevelopOps`
  but **not applied** (closed-source PV5 math). Surfaced by `cli.py inspect`
  over `_DROPPED_AT_EMIT_FIELDS = ("highlights","shadows","whites")`, counting
  non-zero keyframes. (NB: `pipeline.py`'s `shadows` param is the DCP black-render
  scalar, unrelated.)
- **Dehaze**: not even an IR field.
- **Sharpness**: `apply_sharpness` is a deliberate no-op stub (sharpening belongs
  at grade, not baked into a deliverable).
- **Smooth/Catmull-Rom interpolation**: deleted; defer to LRT Auto-Transition.

---

## 10. Repo-truth numbers (2026-05-30 head)

| Metric | Value | Source |
|---|---|---|
| Gym (D750 Camera Standard, real) mean ΔE2000 vs `dng_validate` | **0.026** (P50 0.000, 100% px <1) | `test_pipeline.py` |
| Rose (D750 Adobe Standard, real) mean ΔE2000 | **0.545** | `test_pipeline.py` |
| Synthetic flat patches — neutral median / chromatic mean | **0.000 / 0.052** | `test_synthetic_dng.py` |
| Absolute colorimetric (Axis 2) | 0.70–0.86 on the 0.81–0.84 SSF Luther floor | `test_colorimetric.py` |
| vs LRT preview (affine residual) | ~2.0 (closed-source PV5 + 8-bit JPEG floor) | `tools/diagnose_vs_lrt_preview.py` |

History: gym/rose were 0.789/0.844 before the 2026-05-30 hue-preserving-tone fix.

---

## 11. Document map / what supersedes what

- **This file (`docs/PIPELINE.md`)** — canonical as-built engine reference.
- **`docs/VALIDATION.md`** — canonical *validation* reference (the three axes,
  the colorimetric tap rule, current numbers). Up to date.
- **`docs/LRT_ROUNDTRIP.md`** — the default-emission (sRGB TIFF) round-trip
  contract. Up to date.
- **`docs/DECISIONS.md`** — the binding decisions log (emission format, Adobe
  purge, CDNG/β-XML/GUI dead-ends, dropped ops). Up to date.
- **`docs/research/v08-synthetic-chromatic-rootcause.md`** — full trace of the
  v0.8 colour fix (why it wasn't the LookTable). Kept live authority.
- **`docs/research/v08-linear-exr-gamut-resolve-nuke.md`** — the colour-space
  allowlist authority (on-box Resolve verification). Kept live authority.
- **Archived under git tag `phase4-research-archive`** (Phase-4 doc reduction):
  the `v06`/`v07`/`v08`/`v09` research series, the `color-option-space` set, and
  the superseded emission records (`EMISSION_FORMAT_VERDICT.md`,
  `EMISSION_FORMAT_VERIFIED.md`, `EXR_VERIFICATION.md`). `v06-architecture.md`
  was STALE/WRONG on Stage 9 (per-channel vs hue-preserving `RefBaselineRGBTone`
  — [§5](#5-the-tone-curve-stage-9--load-bearing)); its gym/rose numbers
  (0.79/0.84) are pre-fix (now 0.026/0.545). All binding conclusions live in
  `docs/DECISIONS.md`; recover any archived file with
  `git show phase4-research-archive:<path>`.
