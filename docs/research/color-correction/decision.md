# Decision: v0.6 color-correction shape

**Status:** Proposed — 2026-05-26 (awaiting maintainer sign-off)
**Supersedes:** none
**Implements:** the v0.6 color-correction milestone

## Context

`lrt-cinema` bridges LRTimelapse XMP sidecars to darktable-rendered TIFF/EXR
output for cinema-grade timelapse sequences. The architecture has three
stages: LRT-stage authoring (Adobe-rooted preview), lrt-cinema render
(darktable), and Resolve-stage final color. Two color-science problems live
in this chain:

1. **Camera-response matching.** lrt-cinema currently emits camera RGB →
   working space via a per-camera DCP ColorMatrix or LibRaw matrix. Without
   the Adobe DCP non-linear stage (HueSatMap + LookTable + ProfileToneCurve),
   the render is not perceptually close to what an Adobe-pipeline tool would
   produce from the same RAW. Measured residual on the project's reference
   keyframe (DSC_4053, Nikon D750) after a per-camera 3×3 channelmixer fit:
   12 ΔE2000 mean — 4× the broadcast tolerance of 3.

2. **Cross-stage control loop.** Grading decisions in LRT are made against
   an Adobe-pipeline preview; the deliverable is rendered through darktable;
   the colorist views the deliverable through Resolve's pipeline. The
   perceptually-targeted LRT-stage operations (saturation, HSL, tone-curve
   shape) do not translate cleanly across these three pipelines.

The first problem has an academically-bounded floor: the Luther/Maxwell-Ives
condition guarantees that a 3×3 matrix cannot exactly invert a Bayer
sensor's response. The published floor for a chart-fit 3×3 is ~1.8 ΔE2000
mean (Vazquez-Corral 2014); the floor for a chart-fit 3×3 plus 2.5D HSV
LookTable is ~1.5 ΔE on the training set. lrt-cinema's 12 ΔE residual is the
combined effect of (a) hitting the 3×3 floor and (b) the missing non-linear
HueSatMap + LookTable stage that the Adobe-pipeline reference does apply.

The second problem is structurally unavoidable once the deliverable carries
baked-in perceptually-targeted ops. The control loop only fully closes when
the grader's reference IS the deliverable (the LRT-replacement path); short
of that, the project must constrain LRT-stage authoring to operations that
translate cross-pipeline, and document the workflow for the colorist.

The v0.6 question is: which combination of these stages does lrt-cinema
ship, and at what cost? See [option-space.md](option-space.md) for the
full candidate survey; [measurements.md](measurements.md) for the empirical
inputs.

## Decision

Ship **refined Shape α** in ~3.5–4 engineer-weeks for v0.6:

### 1. A′ transform: camera-agnostic Adobe-Standard distillation

A single shared transform shipped at `src/lrt_cinema/presets/adobe_standard.npz`,
applied as the non-linear residual stage in the render pipeline. The
transform contains:

- A median HueSatMap cube (90×30×1) computed across the 322 same-dimension
  HueSatMaps in the Adobe Standard catalog.
- A median LookTable cube (36×8×16) computed across the 474 same-dimension
  LookTables.
- BaselineExposure offset = 0 (per measurement: Adobe Standard ships
  BaselineExposure = 0 across all 480 sampled cameras).
- No ProfileToneCurve (97% of Adobe Standard ships without one; the dt
  basecurve module's ACR3 baseline applies in absence).

Per-camera ColorMatrix continues to come from camera EXIF (TIFF IFD0 for
NEF/DNG/ARW; ISO BMFF for CR3) or from `tools/extract_dcp.py`'s per-camera
`.npz` output. No per-camera HueSatMap/LookTable database is needed.

The runtime path when `--engine adobe-shared` is selected:

```
RAW → ColorMatrix from camera EXIF (per-camera; bundled in NEF/CR3/DNG)
    → temperature (dt's per-camera kelvin → multiplier math)
    → CAT16 to working space (lin_rec2020)
    → shared HueSatMap + LookTable from presets/adobe_standard.npz
    → emitted via dt's lut3d module
    → CRS-translated user develop ops (existing xmp_emitter path)
    → output (per preset)
```

The existing `--engine dcp` flag becomes the opt-in per-camera enrichment
path (renamed `--engine adobe-camera`); `--engine algorithmic` continues
to suppress all Adobe-flavored stages and rely on dt's libraw-derived
defaults.

### 2. Resolve workflow documentation

A new `docs/RESOLVE_WORKFLOW.md` characterizes the two standard Resolve
color-management paths for lrt-cinema's existing presets:

- **DaVinci YRGB Color Managed** with `cinema-linear`. Input space =
  "Linear Rec.2020"; output = deliverable (Rec.709 Gamma 2.4 for HD/SDR,
  Rec.2020 ST 2084 for HDR).
- **ACES** with `cinema-aces`. ACES input transform = "Linear Rec.2020 →
  ACES2065-1" (clean linear matrix; Resolve applies it). Output Device
  Transform = deliverable.

No OCIO config emission. Resolve's native CMS and ACES paths both consume
linear Rec.2020 TIFF/EXR without a custom OCIO config; OCIO is the right
surface only for projects that already use OCIO across the toolchain.

## Empirical evidence

Full results in [measurements.md](measurements.md). The load-bearing
findings:

**A′ achieves cinema-reference tolerance on modern HSM-equipped cameras**
(per-camera mean ΔE2000 against per-camera Adobe Standard rendering of the
214-patch reference panel):

| Make | Per-camera mean ΔE2000 |
|---|---:|
| Apple | 0.39 |
| Nikon Z f | 0.64 |
| Fujifilm | 0.92 |
| Google | 1.02 |
| Panasonic | 1.14 |
| Samsung | 3.12 |
| Sony | 3.50 |

The cinema-reference threshold (ΔE ≤ 2) holds for the project's primary
target class.

**Across the full 40-camera evaluation panel** (including legacy bodies
like Olympus SP-500UZ and Canon EOS 450D), A′ achieves 3.60 mean / 11.46
P95 ΔE2000 — broadcast-acceptable, not cinema-reference. This is the
honest ceiling for a single shared transform; per-camera Adobe-match
(candidate A) is the path for users on legacy bodies who need tighter
match.

**The HSM + LookTable cascade is load-bearing.** Median-HSM-only achieves
4.83 mean ΔE; median-LookTable-only 6.05; the cascade 3.60. Both stages
do real work. The earlier "drop HSM" hypothesis is empirically falsified;
both ship in `adobe_standard.npz`.

**Cube resolution is not the binding constraint.** 33³ and 65³
uncompressed direct-RGB cubes perform equivalently (4.11 vs 4.15 mean ΔE).
The user-tested hypothesis that an uncompressed representation gains
material fidelity does not hold; per-camera tuning variance is what binds.

**Adobe Standard is camera-agnostic by design** (Q1 measurement, 480-DCP
sample): BaselineExposure identically zero; ProfileToneCurve absent in 97%;
LookTable per-cell hue std mean 2.2° (low variance); only HueSatMap shows
material per-camera variance (sat scale std mean 19%). A single shared
transform captures most of the look.

**Resolve does not consume XMP develop intent on RAW imports.** Confirmed
by the LRTimelapse author, multiple third-party colorist accounts, and
Blackmagic's own Resolve 21 Photo-page launch documentation. The
metadata-passthrough emission mode (candidate E variant) is foreclosed:
the destination tool ignores the artifact.

## Validation

### Acceptance gate

| Camera class | Cameras | Acceptance | Source |
|---|---|---|---|
| Modern target (primary) | Nikon Z 6 / Z f / D750, Canon R5, Sony A7 IV, Fujifilm X-T5, Panasonic GH6, Apple iPhone 14+ Pro, Google Pixel 7+ | mean ΔE < 2.5, P95 < 5 | A′ measured 1.5 mean across the class |
| Full catalog (extended) | All Adobe-supported bodies | mean ΔE < 4, P95 < 12 | A′ measured 3.60 mean / 11.46 P95 |

### Test harness

- Per-frame ΔE2000 measurement on `tools/diagnose_vs_lrt_preview.py` against
  DSC_4053 baseline (Nikon D750 neutral keyframe). Current v0.4 gate is
  2.24 mean ΔE post-affine; A′ should improve marginally with the
  HueSatMap + LookTable stage active.
- Per-make regression panel using borrowed reference RAWs from Nikon, Canon,
  Sony, Fujifilm where available; ground truth via `dng_validate` rendering
  of the per-camera Adobe Standard profile through DNG SDK's reference
  pipeline. CI gate flags ΔE drift on supported cameras.
- A′ reproducer: `python3 tools/measure_a_prime_ceiling.py --panel-size 40
  --construction-size 200`. Re-run on each Adobe DNG Converter catalog
  refresh.

### What this does not validate

The user-develop-intent emitter side (`src/lrt_cinema/xmp_emitter.py`,
`tests/test_xmp_emitter.py`) is unaffected by A′. CRS slider translation
(Exposure2012, ToneCurvePV2012, etc.) has its own per-module test coverage.
A′ closes the camera-response gap; the develop-op gap is a separate axis.

## Foreclosed options

| Option | Reason |
|---|---|
| **B — LRT preview-cache substitution** | LRT regenerates `.lrt/visual/*.lrtpreview` JPEGs from RAW + XMP on every slider interaction (empirically tested 2026-05-26). External cache writes are clobbered before they reach the grader's eye. |
| **E — metadata-passthrough emission** | Resolve does not read XMP develop intent on RAW imports; Resolve's RAW decoder is BMD's YRGB pipeline (not Adobe Camera Raw); image-sequence imports apply one Camera Raw decode per clip (no per-frame XMP channel). Three independent failure grounds. |
| **G — screen-capture LRT overlay** | Depends on Apple private/permission surfaces that tighten on every macOS release; no Linux story; 8-bit JPEG quantization in LRT's preview amplifies banding under any LUT correction. |
| **H — custom monitor ICC profile** | System-wide LUTs contaminate every other app the colorist uses (Resolve, Photoshop, browsers). The contamination is OS-design, not a bug. |
| **I-b — full GLSL/Metal shader port of darktable** | Every dt module change requires a shader update; unsustainable maintenance burden for the project's stewardship bandwidth. |

## Tracked follow-ups

### CinemaDNG emission (v0.7+ candidate)

Per user direction 2026-05-26, characterize whether emitting per-frame
CinemaDNG (linear sensor RGB + per-frame metadata) gives the colorist
meaningfully more flexibility than the existing `cinema-aces` OpenEXR
preset. Open questions:

- Does Resolve's CinemaDNG decoder honor per-frame BaselineExposure /
  ProfileToneCurve / white-balance reliably? Documented behavior is uncertain.
- Does encoding LRT-stage temporal operations (deflicker + Holy Grail
  exposure deltas) into per-frame CinemaDNG metadata produce the same
  downstream effect as baking them into `cinema-aces` EXR?
- Engineering scope: darktable does not emit DNG natively; a custom writer
  (rawpy + tifffile, or Adobe DNG SDK at install time) would be required.

Rough estimate: ~1–2 wks research before scope is clear; if committed,
~2–3 wks to implement. Not in v0.6 scope.

### G2 parallel viewer (contingent)

If post-A′ validation reveals the cross-stage gap is materially larger than
~3 ΔE mean on the project's target cameras — or if the user's grading
workflow shifts toward HSL-heavy / per-color decisions in LRT — G2 becomes
worth shipping as a v0.7 enhancement.

G2 is a small lrt-cinema-owned viewer window that file-watches
`.lrt/visual/*.lrtpreview` JPEGs, applies a T-correction (Adobe→darktable
transform), and displays in a color-managed window the grader cross-
references during LRT authoring. The viewer is owned by lrt-cinema (so its
colorspace is set properly via documented APIs), avoiding the screen-
capture fragility of G proper. Cost from [option-space.md](option-space.md):
3–5 wks. Not in v0.6 scope; contingent on validation outcome.

### Shape γ (LRT replacement) — horizon

Held on the horizon for v1.0 if the project commits to long-term Adobe-free
posture with sustainable maintenance bandwidth. Cost: 31–42 engineer-weeks
for v1, plus 3–6 months/year ongoing maintenance indefinitely. Linux falls
out as a peer platform; the cross-stage control-loop problem ceases to
exist.

Sequencing if γ is selected: ship α first as v0.6 (3.5–4 weeks); γ proceeds
as a parallel v1.0 track over 12–18 months. Nothing in α is thrown away —
the A′ transform composes forward into γ's render path.

## PR chain disposition

| PR | Branch | Disposition |
|---|---|---|
| [#11](https://github.com/turgid-secretion/lrt-cinema/pull/11) | `fix/v0.4-defensive` | Merge — independent audit fixes |
| [#12](https://github.com/turgid-secretion/lrt-cinema/pull/12) | `fix/xy-camera-neutral-iteration` | Merge — independent |
| [#13](https://github.com/turgid-secretion/lrt-cinema/pull/13) | `refactor/cli-resolve-profile` | Merge — independent |
| [#14](https://github.com/turgid-secretion/lrt-cinema/pull/14) | `feat/v0.4-calibration-deterministic` | Merge — `.npz` storage underpins A′'s shipped artifact |
| [#15](https://github.com/turgid-secretion/lrt-cinema/pull/15) | `feat/v0.4-calibration-dt-roundtrip` | Keep as Tier-2 baseline foundation for the opt-in `--engine adobe-camera` path |
| [#16](https://github.com/turgid-secretion/lrt-cinema/pull/16) | `docs/color-option-space-research` | Merge with this doc + research history |

Sequencing: merge #11/12/13 in any order; #14 (calibration storage); #15
(per-camera baseline); #16 with this doc; open new PR for refined α
implementation.

## Action list

1. Merge #11, #12, #13 in CI order.
2. Merge #14 (calibration storage infrastructure).
3. Merge #15 (Tier-2 baseline foundation).
4. Merge #16 with the consolidated research docs.
5. Open the v0.6 implementation PR: A′ distillation tool +
   `presets/adobe_standard.npz` shipped artifact + runtime wiring + tests +
   `docs/V06_PLAN.md` + `docs/RESOLVE_WORKFLOW.md`.

## Files affected

- `src/lrt_cinema/presets/adobe_standard.npz` — new shipped artifact.
- `src/lrt_cinema/cli.py` — new `--engine adobe-shared` flag (or auto-select
  on `--engine dcp` based on presence of per-camera DCPs).
- `src/lrt_cinema/runner.py` — wire shared transform into render pipeline.
- `src/lrt_cinema/lut3d_baker.py` — extend `bake_dcp_cubes_to_resolve_cube`
  to accept shared-transform variant.
- `tools/distill_adobe_standard.py` — new maintainer-side tool that runs
  `parse_dcp` over the Adobe Standard catalog and produces the shared
  `.npz`.
- `tests/test_adobe_shared_engine.py` — fixture + integration test.
- `docs/V06_PLAN.md` — milestone plan referencing this decision.
- `docs/RESOLVE_WORKFLOW.md` — colorist-facing workflow documentation.
- `README.md`, `SCOPE.md` — re-anchor the project's positioning around the
  multi-stage workflow.

## Open implementation questions

These do not block approval; they resolve during implementation.

1. **`--engine` flag naming.** Use `--engine adobe-shared` / `--engine
   adobe-camera`, or keep `--engine dcp` and auto-select based on whether
   per-camera DCPs are extracted?
2. **`cinema-aces` preset name.** The preset emits 32-bit float linear
   Rec.2020 EXR, not ACES2065-1 EXR (the data is equivalent up to a 3×3
   matrix Resolve applies). Rename or document? Conservative answer:
   document.
3. **Validation panel scope.** Which cameras beyond Nikon D750 enter the
   acceptance gate? Depends on available test data.

## References

- [option-space.md](option-space.md) — full candidate survey + math primitives.
- [measurements.md](measurements.md) — empirical inputs (DCP variance, A′
  ceiling, LRT cache test).
- [DNG SDK feasibility](../DNG_SDK_FEASIBILITY.md) — patent + runtime
  constraints on Adobe DNG SDK.
- [Adobe DNG Specification 1.4](https://www.adobe.com/content/dam/acom/en/products/photoshop/pdfs/dng_spec_1.4.0.0.pdf) — DCP field semantics.
- [Finlayson, Mackiewicz, Hurlbert 2015](https://eprints.ncl.ac.uk/file_store/production/211896/56A5026C-F3B9-4CB9-9A51-10F304877B45.pdf) — root-polynomial regression (cinema-standard 3×3 upgrade; not in α but in option-space).
- [AMPAS P-2013-001](https://docs.acescentral.com/system-components/input-transforms/) — IDT creation procedure.
- [butcherg/ssf-data](https://github.com/butcherg/ssf-data) — Nikon D750 SSF reference (CC BY-NC-SA).
