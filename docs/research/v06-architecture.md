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
`research/python-pipeline-seed`) achieves **1.13 ΔE on gym, 2.47 ΔE on
rose** — 5.6× and 2.0× improvement, with the renderer entirely under
project control. v0.6 replaces dt-cli with that pipeline as the runtime
renderer.

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
| **NEW** `pipeline.py` | — | new | ~500 | Promoted from `.audit_tmp/adobe_pipeline.py`. |
| **NEW** `develop_ops.py` | — | new | ~350 | Apply LR-authored Exposure / Blacks / Tone-curve / Sat / Vib / Contrast / Sharp directly on linear ProPhoto. |
| **NEW** `output.py` | — | new | ~250 | TIFF (16-bit int) + EXR (32-bit float) writers. |
| `__init__.py` + `__main__.py` | 9 | keep | 9 | — |
| **src/ total** | **~4400** | | **~3600** | |

PR #14's `calibration.py` (628) and PR #15's `synthetic_dng.py` (434) +
`tools/calibrate_camera.py` are rejected — the per-camera channelmixer
substrate they build is for an algorithmic engine that v0.6 deletes.

**Note on LOC budget.** The prior simplification plan's ~2000 target
required deleting `dcp.py` and `lut3d_baker.py` (~1500 combined) and
shipping a single pre-baked `adobe_standard.cube`. v0.6 keeps both as
in-process libraries — they are now load-bearing for runtime rendering.
The ~3600 figure reflects that trade.

## CLI surface

Three required + six optional. Surface shrinks from 12 flags to 9.

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

1. **Demosaic** — rawpy/libraw (AHD); float32, normalized `[0, 1]` after
   black-level subtract + WhiteLevel normalize. Per `dng-pipeline-findings`
   §4, use the per-camera WhiteLevel from libraw's
   `camera_white_level_per_channel` (D750: 15520) rather than rawpy's
   default 16383; closes ~0.6 ΔE.
2. **AsShotNeutral inverse** — per-channel WB scaling, normalized G=1.
3. **Camera RGB → XYZ(D50)** — ForwardMatrix × balanced if FM present
   (preferred; mired-blended by scene kelvin when FM1 ≠ FM2); else
   `inv(ColorMatrix)` × camera_rgb with iterative neutral normalization.
4. **XYZ(D50) → linear ProPhoto(D50)** — fixed matrix.
5. **HueSatMap (HSM)** — mired-blended by scene kelvin between
   data_1/data_2 if both present; applied in HSV (Adobe hexcone variant).
6. **BaselineExposureOffset (BEO)** — multiplicative on V; `2^BEO`.
7. **LookTable (LT)** — applied in HSV; single cube (no per-illuminant).
8. **ProfileToneCurve** — **per-R, per-G, per-B independently** in linear
   ProPhoto, NOT per-V as DNG 1.7.1 spec text suggests. Per
   `dng-pipeline-findings` §3: SDK `dng_render.cpp::DoBaselineRGBTone`
   applies it per-channel; switching from per-V to per-channel was the
   single largest ΔE improvement (2.92 → 1.75 on gym). Solve via PCHIP
   (closest to Adobe's `dng_spline_solver`; see Open Question 3).
9. **BaselineExposure (BE)** — scalar EV on linear ProPhoto.
10. **LR-authored ops — linear domain.** Exposure2012 (EV → linear gain),
    Blacks2012. Apply on linear ProPhoto, after all DCP shaping.
    `develop_ops.py` owns the per-op math and the in-group ordering.
11. **LR-authored ops — perceptual domain.** PV2012 ToneCurve (layered on
    top of DCP ProfileToneCurve — both fire when both present),
    Saturation, Vibrance, Contrast2012, Sharpness. Apply in HSV where
    appropriate; sharpness last.
12. **ProPhoto(D50) → Rec.2020(D65)** — Bradford CAT D50→D65 + ProPhoto→
    XYZ→Rec.2020 matrix. For `cinema-linear`/`cinema-aces` this is the
    final output (linear). For `stills-finished` apply AgX (see Output).

**Caveat on stage 10 placement.** ACR's UI order applies Exposure/Blacks
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
| `cinema-linear` | 16-bit int TIFF | linear Rec.2020 | `tifffile` | Drop into Resolve; tag clip as Linear Rec.2020 input. |
| `cinema-aces` | 32-bit float EXR (PIZ) | linear Rec.2020 | `OpenEXR` (ASWF/PyPI, capital-O — the official binding, not `pyexr` or `imageio`'s EXR plugin) | Name is historical; Resolve applies "Linear Rec.2020 → ACES2065-1" as a clean 3×3 IDT. |
| `stills-finished` | 16-bit int TIFF | Rec.2020 (gamma) | `tifffile` + AgX in Python | AgX is real new work — port from Blender Foundation reference; `colour-science` has primitives. ~200 LOC. |

CinemaDNG and other RAW intermediate formats: tracked as v0.7+ candidates
per the user direction in `color-correction/decision.md` §"Tracked
follow-ups"; out of v0.6 scope.

## Color science scope

**Spec-compliant out of the box** (per `dng-pipeline-findings`):
ColorMatrix interpolation, ForwardMatrix path (when present),
AsShotNeutral inverse, HueSatMap with mired blend, BaselineExposureOffset,
LookTable, per-channel ProfileToneCurve, BaselineExposure, ProPhoto/Rec.2020
matrices, Bradford CAT.

**Known-imperfect** (rose at 2.47 ΔE vs `dng_validate`; gym at 1.13):
DNG `LinearizationTable` not yet applied (rawpy doesn't surface it);
`dng_spline_solver` replaced by scipy `PchipInterpolator` (functionally
similar, not bit-identical); libraw AHD demosaic vs Adobe's proprietary
demosaic; Adobe `ExposureRamp` shadow rolloff not implemented; Stage3Gain
interaction with BaselineExposure has a measured 0.05 EV residual on gym
(principled answer +0.10; empirical need +0.15).

**Ship gate (v0.6):** gym ≤ **2.0 ΔE mean** vs `dng_validate` (currently
1.13 — passes). Rose is Chip 1 work-in-progress and **not** a ship-gating
metric for v0.6. Target stretches to ≤ 1.0 ΔE pending Chip 1 outcome;
TBD pre-tag.

**Floors that cannot fall further without out-of-DCP-spec work:** vs LRT
preview, 2.03 ΔE (LR PV5 baseline processing beyond DCP); vs in-camera
JPEG, 6.32 ΔE (Nikon Picture Control ≠ Adobe DCP). These are characterized,
not ship-gating.

## Runtime dependencies

Shift: dt-cli goes away. New runtime deps (`pyproject.toml`): `rawpy`
(demosaic), `colour-science` (color-space math + AgX primitives), `scipy`
(`PchipInterpolator`), `tifffile` (TIFF writer), `OpenEXR` (EXR writer),
`numpy` (already in). Install story moves from "install darktable + pipx
install lrt-cinema" to "pipx install lrt-cinema" (self-contained, no
external renderer).

## Branch policy + PR sequencing

1. **PR #18** (BEO tag fix + V-encoded clamp; commits `8778f4a` +
   `b6eaaf7`) — lands independently against `main` first.
2. **This PR** (`docs/v06-architecture` → `main`) — spec only, single
   doc, doc-PR small.
3. **PR #14, #15** — rejected with one-line link to this spec.
4. **v0.6 implementation PR** — separate large PR against `main`, based
   on this spec. Lands the renderer swap atomically: `pipeline.py` +
   `develop_ops.py` + `output.py` + `cli.py` rewrite + test rewrites +
   deletions (`runner.py`, `xmp_emitter.py`, .style files,
   `test_dt_integration.py`) + dep manifest update.
5. **`audit/v06-repo-impact`** and the findings docs become historical
   reference. The findings docs (`dng-pipeline-findings.md`,
   `v06-simplification-plan.md`, `v06-impact-audit.md`) land on `main`
   with the v0.6 implementation PR (or earlier as a docs-only PR if the
   maintainer prefers). The `.audit_tmp/` directory stays gitignored;
   its useful content has been promoted to `pipeline.py`.

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
3. **`dng_spline_solver` port.** Use scipy PCHIP (current; small ΔE
   residual) vs port the ~50-line Hermite solver from Adobe SDK
   (`dng_render.cpp::Solve`). PCHIP is a known-imperfect substitute;
   porting the solver is straightforward.
4. **Holy Grail kelvin shifts.** Sequences spanning 1500-3000K dawn→
   midday shifts will produce per-frame WB drift unless the pipeline
   reads `crs:Temperature` from the per-frame interpolated `DevelopOps`
   and overrides the AsShotNeutral pre-Step-2. Spec the override path
   in v0.6 implementation; cheap to add.
5. **Worker pool.** v0.4 is single-worker. In-process Python at ~8s/24MP
   frame on a 5000-frame Holy Grail is ~11 hours. Add a
   `ProcessPoolExecutor` worker pool to `cli.py` driver, controlled by
   `--workers N` (default = `os.cpu_count() // 2`). 1-day addition; gate
   on whether the chip wants it in v0.6 or v0.6.x.
6. **LR Exposure/Blacks placement (stage 10).** Spec defaults to
   post-DCP-shaping per ACR UI convention. The DNG spec is silent;
   empirical measurement would require the chip to compare LR-render
   output against the project pipeline with the two ops swapped before
   vs after HSM. Defer to v0.6 implementation; resolve before tag.

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
