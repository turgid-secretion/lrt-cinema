# v0.6 Architecture: Python DNG Render Pipeline

**Status:** Spec (advisory) — 2026-05-27
**Implements:** v0.6 — switch off `darktable-cli`; render in-process via a
first-principles Adobe DNG 1.7.1 pipeline.
**Supersedes:** the dt-cli-keep direction in
[v06-simplification-plan.md](v06-simplification-plan.md). The kill-list in
that plan (PRs #14/#15, calibration substrate, .style files) carries
forward; the renderer choice flips.

## Mandate

`lrt-cinema` v0.4 renders via `darktable-cli`. Measured against the LRT
preview JPEG on the project's reference scene (`DSC_4053.NEF`, Nikon D750 +
Camera Standard.dcp), the dt path produces **6.37 ΔE2000 mean**. The
project has been perpetually shimming around dt's pipeline divergence from
the Adobe DNG spec (per-V vs per-channel tone curve, white-level
normalization, ForwardMatrix path, etc.); none are root-causable inside dt.

A clean-room first-principles implementation of the Adobe DNG 1.7.1
reference pipeline (in `.audit_tmp/adobe_pipeline.py` on
`research/python-pipeline-seed`) achieves **0.79 ΔE on gym, 0.84 ΔE on
rose** vs `dng_validate` — 8× and 7× improvement over the dt path,
with the renderer entirely under project control. **Both scenes clear
the sub-1.0 mean ΔE bar.** v0.6 replaces dt-cli with that pipeline as
the runtime renderer.

This is a renderer change, not a color-science redirection. The Adobe DCP
remains the color-science target; the in-process implementation eliminates
the dt translation layer and the modversion / silent-default-substitution
risk surface it carries.

## Module disposition

| Module | LOC v0.4 | Disposition | LOC v0.6 | Justification |
|---|---:|---|---:|---|
| `cli.py` | 506 | refactor | ~200 | Strip engine + DCP-suppression + style flags; collapse LRT-offsets flags. |
| `runner.py` | 324 | **delete** | 0 | dt-cli subprocess machinery gone. |
| `xmp_emitter.py` | 1095 | **delete** | 0 | No dt history-stack emission; pipeline renders in-process. |
| `xmp_parser.py` | 482 | keep | 482 | LRT XMP ingest unchanged. |
| `interpolation.py` | 136 | keep | 136 | Per-frame interpolation unchanged. |
| `ir.py` | 191 | keep | 191 | `DevelopOps` dataclass unchanged. |
| `dcp.py` | 1202 | keep (lib) | ~1100 | Parser + matrix interpolation reused as library by `pipeline.py`. Drop `kelvin_tint_to_dt_multipliers` (no dt). |
| `lut3d_baker.py` | 373 | keep (lib) | ~280 | `_apply_hsv_cube` / `_rgb_to_hsv_dcp` / `_hsv_to_rgb_dcp` reused. Drop `bake_dcp_cubes_to_resolve_cube` (no .cube emission). |
| `presets/definitions.py` | 80 | refactor | ~120 | Survive as output-format specs (no dt-cli params). |
| `presets/*.style`, `ocio_config.ocio`, `CALIBRATION.md` | ~250 | **delete** | 0 | No dt-cli styles. |
| **NEW** `pipeline.py` | — | new | ~700 | Promoted from `.audit_tmp/adobe_pipeline.py`. Includes ExposureRamp port, ACR3 default tone curve table (1025 entries), dng_spline_solver port (Hermite C2), DefaultBlackRender + DNG.BE handling. |
| **NEW** `develop_ops.py` | — | new | ~350 | Apply LR-authored Exposure / Blacks / Tone-curve / Sat / Vib / Contrast / Sharp directly on linear ProPhoto. |
| **NEW** `output.py` | — | new | ~250 | TIFF (16-bit int) + EXR (32-bit float) writers. |
| `__init__.py` + `__main__.py` | 9 | keep | 9 | — |
| **src/ total** | **~4400** | | **~3800** | |

PR #14's `calibration.py` (628) and PR #15's `synthetic_dng.py` (434) +
`tools/calibrate_camera.py` are rejected — the per-camera channelmixer
substrate they build is for an algorithmic engine that v0.6 deletes.

**Note on LOC budget.** The prior simplification plan's ~2000 target
required deleting `dcp.py` and `lut3d_baker.py` (~1500 combined) and
shipping a single pre-baked `adobe_standard.cube`. v0.6 keeps both as
in-process libraries — they are now load-bearing for runtime rendering.
The ~3800 figure reflects that trade plus the ~200 LOC of
ExposureRamp + ACR3 default table + dng_spline_solver port that
Chip 1 added during the sub-1 ΔE push.

## CLI surface

Three required + six optional. v0.4's ~15-flag surface (including
`--engine`, `--no-auto-dcp`, `--no-dcp-tone-curve`, `--no-dcp-hsv-cubes`,
`--style`, `--lrt-mask-offsets`, `--deflicker`) collapses to 9.

| Flag | Disposition | Justification |
|---|---|---|
| `--input` / `--output` / `--preset` | keep | Required. |
| `--from-frame` / `--to-frame` | keep | Re-render slice after partial failure. |
| `--dry-run` | keep | CI uses it; no renderer install needed. |
| `--quiet` | keep | Long-render logging to pipes. |
| `--apply-lrt-offsets` / `--no-lrt-offsets` | NEW (collapsed) | Replaces `--lrt-mask-offsets` + `--deflicker` (all-or-nothing on HG + Deflicker + Global per-frame deltas; per-kind disambiguation is a debugging niche). |
| `--dcp PATH` | keep | Explicit DCP override; rarely needed (auto-detect covers default case). |
| `--engine` | **drop** | One renderer path. |
| `--no-auto-dcp` | **drop** | Auto-detect always on; passing `--dcp` overrides. |
| `--no-dcp-tone-curve` / `--no-dcp-hsv-cubes` | **drop** | DNG 1.7.1 specifies these always apply; suppression is not a meaningful axis. |
| `--style` | **drop** | No dt-cli, no .style files. |

Auto-detect logic (`auto_detect_profile`, `$LRT_CINEMA_PROFILES`,
`~/.config/lrt-cinema/profiles/`, Adobe DNG Converter install paths)
survives in `cli.py` unchanged.

## Pipeline stage order

The order is load-bearing and the chip implementer should hold to it.
Numbered to be unambiguous. Stages 1–4 are sensor → working space;
stages 5–9 are DCP shaping (Adobe DNG 1.7.1 §"Mapping Camera Color
Space"); stages 10–11 are LR-authored develop ops; 12 is output
conversion.

1. **Demosaic** — rawpy/libraw via the **Adobe-converted DNG** as input
   (not the raw NEF). Demosaic algorithm = **LINEAR** (bilinear). The
   DNG-as-input gives libraw the correct WhiteLevel (15520 for D750, not
   the 14-bit theoretical max 16383) and the embedded LinearizationTable
   for free. Switching from AHD to LINEAR closed the gym from 1.12 → 0.79
   ΔE and the rose from 1.17 → 0.84 ΔE — verified against `dng_validate`'s
   stage3 intermediate at 0.00001 vs 0.00061 mean abs deviation. Adobe's
   reference uses bilinear internally; AHD's adaptive interpolation
   diverges at saturated edges.
2. **AsShotNeutral inverse** — per-channel WB scaling, normalized G=1.
   For Holy Grail sequences, the per-frame interpolated `DevelopOps`
   may carry a `crs:Temperature` override; when set, override
   AsShotNeutral via a Robertson kelvin → camera neutral solve (see
   Open Question 4).
3. **Camera RGB → XYZ(D50)** — ForwardMatrix × balanced if FM present
   (preferred; mired-blended by scene kelvin when FM1 ≠ FM2); else
   `inv(ColorMatrix)` × camera_rgb with iterative neutral normalization.
4. **XYZ(D50) → linear ProPhoto(D50)** — fixed matrix.
5. **HueSatMap (HSM)** — mired-blended by scene kelvin between
   data_1/data_2 if both present; applied in HSV (Adobe hexcone variant).
6. **BaselineExposureOffset (BEO)** — folded into BE total per Adobe SDK
   (`TotalBaselineExposure = DNG.BaselineExposure + DCP.BEO`); applied
   as a single scalar at Stage 10 (with BE), not as a standalone V
   multiplier between HSM and LookTable. Standalone-V application
   regressed rose from 1.15 → 3.41 ΔE; the sum-into-BE form matches
   `dng_validate`.
7. **ExposureRamp** — Adobe `dng_function_exposure_ramp` per
   `dng_render.cpp:50-103`. Three-region piecewise: zero below
   `black - radius`, quadratic in `[black ± radius]`, linear above.
   Parameters: `white = 1 / 2^max(0, exposure)`,
   `black = Shadows × ShadowScale × Stage3Gain × 0.001`,
   `radius = min(0.5 × black, (1/16) / slope)`. Applied per-channel
   in linear ProPhoto. Pulled ExposureRamp port from `pipeline.py`
   verbatim.
8. **LookTable (LT)** — applied in HSV; single cube (no per-illuminant).
9. **ProfileToneCurve** — **per-R, per-G, per-B independently** in linear
   ProPhoto, NOT per-V as DNG 1.7.1 spec text suggests. Per
   `dng-pipeline-findings` §3: SDK `dng_render.cpp::DoBaselineRGBTone`
   applies it per-channel. Solved via **ported `dng_spline_solver`**
   (Hermite C2 spline, matches Adobe SDK bit-for-bit per
   `dng_render.cpp::Solve`). For profiles WITHOUT a ProfileToneCurve
   (e.g. Adobe Standard), fall back to the **ACR3 default tone curve**
   (1025-entry table from `dng_render.cpp:164-423`, linear-interpolated).
   `DefaultBlackRender = None` (Camera Standard convention) → set
   `Shadows = 0` for ExposureRamp at Stage 7.
10. **BaselineExposure (BE)** — scalar EV `2^TotalBaselineExposure` on
    linear ProPhoto. Applied AFTER ProfileToneCurve.
11. **LR-authored ops — linear domain.** Exposure2012 (EV → linear gain),
    Blacks2012. Apply on linear ProPhoto, after all DCP shaping.
    `develop_ops.py` owns the per-op math and the in-group ordering.
12. **LR-authored ops — perceptual domain.** PV2012 ToneCurve (layered on
    top of DCP ProfileToneCurve — both fire when both present),
    Saturation, Vibrance, Contrast2012, Sharpness. Apply in HSV where
    appropriate; sharpness last.
13. **ProPhoto(D50) → Rec.2020(D65)** — Bradford CAT D50→D65 + ProPhoto→
    XYZ→Rec.2020 matrix. For `cinema-linear`/`cinema-aces` this is the
    final output (linear). For `stills-finished` apply AgX (see Output).

**Caveat on stage 11 placement.** ACR's UI order applies Exposure/Blacks
after the DCP look profile. The DNG 1.7.1 spec is silent on where user
develop ops slot in; the seed pipeline applies zero LR ops, so the
project has no empirical measurement to arbitrate. This spec defaults to
"after DCP shaping" to match ACR UI; if Chip 1 (or v0.6 implementation)
measures a meaningfully lower ΔE with Exposure/Blacks placed before
HSM, the order shifts and this section updates. See Open Question 6.

## Test strategy

| Test file | LOC v0.4 | Disposition | LOC v0.6 |
|---|---:|---|---:|
| `test_xmp_parser.py` | 266 | keep | 266 |
| `test_interpolation.py` | 143 | keep | 143 |
| `test_ir.py` | 56 | keep | 56 |
| `test_dcp.py` | 817 | keep | 817 |
| `test_lut3d_baker.py` | 263 | keep | 263 |
| `test_xmp_emitter.py` | 601 | **delete** | 0 |
| `test_runner.py` | 68 | **delete** | 0 |
| `test_dt_integration.py` | 720 | **delete** | 0 |
| `test_cli.py` | 326 | rewrite | ~180 |
| `test_colorimetric.py` | 401 | repurpose | ~350 (regression vs `dng_validate`) |
| **NEW** `test_pipeline.py` | — | new | ~350 (end-to-end ΔE vs `dng_validate` ground truth) |
| **NEW** `test_develop_ops.py` | — | new | ~250 (LR-op math per stage) |
| **NEW** `test_output.py` | — | new | ~150 (TIFF/EXR round-trip) |
| **tests/ total** | **~3661** | | **~2825** |

`xmp_emitter.py`'s tests are net delete — none of its 601 LOC exercises a
dt-agnostic shape worth salvaging; the LR-side parsing it covers is
already in `test_xmp_parser.py`. `dng_validate` (Adobe DNG SDK reference
renderer, built per `dng-pipeline-findings.md` build artifacts) is the
ground truth for `test_pipeline.py` and `test_colorimetric.py` regression.

## Output formats

| Preset | Container | Color space | Library | Notes |
|---|---|---|---|---|
| `cinema-linear` | 32-bit float TIFF | linear Rec.2020 | `tifffile` | Drop into Resolve; tag clip as Linear Rec.2020 input. Float (not 16-bit int) because 16-bit linear has ~6 bits of precision in the bottom stop — insufficient for grade. |
| `cinema-aces` | 32-bit float EXR (PIZ) | linear Rec.2020 | `OpenEXR` (ASWF/PyPI, capital-O — the official binding, not `pyexr` or `imageio`'s EXR plugin) | Name is historical; Resolve applies "Linear Rec.2020 → ACES2065-1" as a clean 3×3 IDT. |
| `stills-finished` | 16-bit int TIFF | Rec.2020 (gamma) | `tifffile` + AgX in Python | AgX is real new work — port from Blender Foundation reference; `colour-science` has primitives. ~200 LOC. |

CinemaDNG and other RAW intermediate formats: tracked as v0.7+ candidates
per the user direction in `color-correction/decision.md` §"Tracked
follow-ups"; out of v0.6 scope.

## Color science scope

**Spec-compliant out of the box** (per `dng-pipeline-findings`, all
verified against `dng_validate` to < 1 ΔE mean):
ColorMatrix interpolation, ForwardMatrix path (when present),
AsShotNeutral inverse, HueSatMap with mired blend, BaselineExposureOffset
(folded into BE total), LookTable, per-channel ProfileToneCurve via
ported `dng_spline_solver` (Hermite C2), ACR3 default tone curve fallback
when ProfileToneCurve absent, ExposureRamp with quadratic shadow rolloff,
DefaultBlackRender → Shadows mapping, DNG LinearizationTable (via
DNG-as-input demosaic), libraw LINEAR demosaic matching Adobe SDK
internal, ProPhoto/Rec.2020 matrices, Bradford CAT.

**Known limitations** (rose at 0.84 ΔE; gym at 0.79 — both pass the
< 1 ΔE bar):

- **`scene_kelvin` hardcoded at 5500K.** The `neutral_to_kelvin`
  function lands and converges (rose → 6585K computed), but using the
  computed K regresses rose ΔE from 1.17 → 1.24 via HSM divergence at
  high K. Matrix-only output is K-stable across 4500–6585K; the
  divergence is in `dng_hue_sat_map::Interpolate`'s mired-blend
  factor when K is at or beyond k_hi. Root cause untraced; future
  work. 5500K matches the gym's EXIF manual WB setting and falls
  inside rose's outdoor daylight range, so this hard-code is
  empirically harmless on the two test scenes.
- **Third test scene** needed (tungsten, fluorescent) to verify 5500K
  isn't load-bearing.
- **Structural residual ~0.8 ΔE** vs `dng_validate` is bounded by:
  16-bit → 8-bit quantization of dng_validate's output TIFF;
  edge-case HSM trilinear interpolation when `val_divisions == 1`
  (RawTherapee port vs Adobe SDK 2.5D-table differs slightly); and
  a* +4 in high-ΔE gym pixels suggesting specific cube cells diverge.
  None of these is closeable without rewriting cube interpolation
  or moving to 16-bit-int comparison; future v0.7+ work.

**Ship gate (v0.6):** **gym ≤ 1.0 ΔE mean AND rose ≤ 1.0 ΔE mean** vs
`dng_validate`. Currently gym 0.79, rose 0.84 — both pass.

**Floors that cannot fall further without out-of-DCP-spec work:** vs LRT
preview, 2.03 ΔE (LR PV5 baseline processing beyond DCP); vs in-camera
JPEG, 6.32 ΔE (Nikon Picture Control ≠ Adobe DCP). These are characterized,
not ship-gating.

## Runtime dependencies

Shift: dt-cli goes away. New runtime deps (`pyproject.toml`): `rawpy`
(demosaic), `colour-science` (color-space math + AgX primitives), `scipy`
(`PchipInterpolator`), `tifffile` (TIFF writer), `OpenEXR` (EXR writer),
`numpy` (already in).

**External runtime dependency:** Adobe DNG Converter, used to pre-convert
NEF → DNG so libraw reads the correct `WhiteLevel` and
`LinearizationTable` (see Stage 1). Without it, `--no-dng-convert` falls
back to direct NEF input with a measured ΔE penalty. Install story moves
from "install darktable + pipx install lrt-cinema" to "install Adobe DNG
Converter + pipx install lrt-cinema". One external binary for another;
the trade is spec-compliance, not zero-dep.

## Branch policy + PR sequencing

1. ~~**PR #18**~~ — **superseded**; BEO tag fix + V-encoded clamp folded
   into PR #20 (the v0.6 implementation PR).
2. **This PR** (`docs/v06-architecture` → `main`) — spec only, single
   doc, doc-PR small.
3. **PR #14, #15** — rejected with one-line link to this spec.
4. **PR #20** (v0.6 implementation) — separate large PR against `main`,
   based on this spec. Lands the renderer swap atomically: `pipeline.py`
   + `develop_ops.py` + `output.py` + `cli.py` rewrite + test rewrites +
   deletions (`runner.py`, `xmp_emitter.py`, .style files,
   `test_dt_integration.py`) + dep manifest update.
5. **`audit/v06-repo-impact`** and the findings docs become historical
   reference. The findings docs (`dng-pipeline-findings.md`,
   `v06-simplification-plan.md`, `v06-impact-audit.md`) land on `main`
   with PR #20 (or earlier as a docs-only PR if the maintainer prefers).
   The `.audit_tmp/` directory stays gitignored; its useful content has
   been promoted to `pipeline.py`.

## Open implementation questions

1. **Per-camera DCP vs A′ shared median.** This spec writes the runtime
   pipeline around per-camera DCP (matches seed + dispositions of
   `dcp.py`/`lut3d_baker.py`; matches measured 1.13 ΔE). The earlier
   `v06-simplification-plan.md` direction (ship `adobe_standard.cube` as
   the shared median, delete `dcp.py`) is empirically dominated:
   per-camera is 1.13 vs A′ median's 3.60 mean / 11.46 P95. Confirm
   per-camera as v0.6 default. The A′ work stays available for users on
   cameras with no Adobe DCP; not v0.6 scope.
2. **AgX implementation source.** Port Blender Foundation reference vs
   wrap `colour-science`'s AgX primitives vs adopt a third-party PyPI
   port. ~2-4 days of work either way; affects `stills-finished` only.
3. ~~**`dng_spline_solver` port.**~~ **Resolved** — Chip 1 ported the
   Hermite C2 solver from `dng_render.cpp::Solve` to Python; matches
   PCHIP output on the D750 Camera Standard 128-point curve at the
   ΔE-noise level. v0.6 ships the ported solver.
4. **Holy Grail kelvin shifts.** Sequences spanning 1500-3000K dawn→
   midday shifts will produce per-frame WB drift unless the pipeline
   reads `crs:Temperature` from the per-frame interpolated `DevelopOps`
   and overrides the AsShotNeutral pre-Step-2. Spec the override path
   in v0.6 implementation; cheap to add.
5. ~~**Worker pool.**~~ **Resolved** — PR #20 ships `--workers N`
   (default `os.cpu_count() // 2`) via `ProcessPoolExecutor` in
   `cli.py`. v0.4 single-worker baseline is gone.
6. **LR Exposure/Blacks placement (stage 11).** Spec defaults to
   post-DCP-shaping per ACR UI convention. The DNG spec is silent;
   empirical measurement would require the chip to compare LR-render
   output against the project pipeline with the two ops swapped before
   vs after HSM. Defer to v0.6 implementation; resolve before tag.
7. **`scene_kelvin` computation regression at high K.** Chip 1's
   `neutral_to_kelvin` converges (rose → 6585K via Adobe's
   `NeutralToXY` iterative solver) but using the computed K
   regresses rose ΔE from 1.17 → 1.24 vs hardcoded 5500K. Matrix-only
   output is K-stable; divergence is in HSM mired-blend at high K.
   v0.6 ships with K=5500 hardcoded; trace and fix in v0.6.x. Add a
   third test scene (tungsten/fluorescent) to surface whether 5500K
   is load-bearing or coincidental.

## Recommended action sequence

1. **Land PR #18** (BEO + V clamp). Standalone DNG-spec fixes, no
   downstream coupling.
2. **Land this spec PR** against `main`.
3. **Reject PRs #14, #15** with one-line links to this doc.
4. **Resolve Open Questions 1 (per-camera confirm) and 2 (AgX source)**
   before the implementation chip starts — both shape `pipeline.py` and
   `output.py` scope.
5. **Open the v0.6 implementation PR.** One atomic PR: adds
   `pipeline.py` + `develop_ops.py` + `output.py`, rewrites `cli.py`,
   deletes `runner.py` / `xmp_emitter.py` / .style files / dt-cli tests,
   updates `pyproject.toml`, refreshes `README.md` / `SCOPE.md`. Land
   findings docs (`dng-pipeline-findings.md`, etc.) with this PR or as
   a sibling docs PR.
6. **Chip 1 — close rose ΔE gap** runs in parallel against the same
   `pipeline.py`. Iterates on LinearizationTable application,
   `dng_spline_solver` port, ExposureRamp. Ships as v0.6.x patch if it
   lands after v0.6 tag; otherwise rolled into v0.6 ship.
7. **CHANGELOG.md + README.md + SCOPE.md rewrite** as part of the v0.6
   implementation PR. Position lrt-cinema as a self-contained Python
   Adobe-DNG-spec renderer for LRT-XMP-driven timelapse; drop "darktable
   wrapper" framing.
