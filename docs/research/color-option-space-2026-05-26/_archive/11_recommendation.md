# Canonical recommendation: refined Shape α

*This is the action plan for v0.6. Supersedes the recommendation
portion of `07_decision.md`. Draws on the empirical inputs in
`09_dcp_variance.md` (Q1: cross-camera DCP-field variance) +
`09e_a_prime_ceiling.md` (A' empirical ΔE ceiling) +
`09f_metadata_passthrough.md` (Resolve XMP handling characterization),
the four cluster feasibility studies (`09a`–`09d`), and the
option-space synthesis in `10_synthesis.md`. The full research is
preserved in tree as `01`–`10`; this doc is the implementation
contract.*

## TL;DR

Ship **refined Shape α** in ~3.5–4 engineer-weeks for v0.6:

1. **A' (camera-agnostic Adobe-Standard transform).** ~3 wks. A
   single distilled transform shipped in
   `src/lrt_cinema/presets/adobe_standard.npz`, applied as the
   non-linear residual stage in the render pipeline. Per-camera
   ColorMatrix continues to come from camera EXIF or `extract_dcp.py`'s
   output. No per-camera HSV/LookTable database needed.
2. **Resolve workflow documentation.** ~0.5–1 wk. Document the two
   standard Resolve color-management paths for our existing presets:
   - **DaVinci YRGB Color Managed** with `cinema-linear` (16-bit
     linear Rec.2020 TIFF). Input = "Linear Rec.2020"; output =
     deliverable.
   - **ACES** with `cinema-aces` (32-bit float linear OpenEXR). Use
     ACES input transform "Linear Rec.2020 → ACES2065-1" (a clean
     linear matrix; Resolve handles it).
   No engineering on the presets themselves; no OCIO config emission.

**Empirical confirmation (2026-05-26)**:

`09e_a_prime_ceiling.md` measured A' against a 40-camera evaluation
panel with 214 spectral patches under D55. Headline results:

- **For modern HSM-equipped DSLR/mirrorless/mobile (the project's
  primary target): A' achieves ~1.5 ΔE2000 mean.** Apple 0.39, Nikon
  Z f 0.64, Fujifilm 0.92, Google 1.02, Panasonic 1.14, Samsung 3.12,
  Sony 3.50. Inside cinema-reference tolerance for the primary target
  camera class.
- For the full catalog including legacy bodies (Olympus SP-500UZ,
  Nikon Coolpix 5400, Canon EOS 450D, etc.): A' achieves ~3.60 mean /
  11.46 P95 ΔE — broadcast-acceptable but not cinema.
- **The cascade (median HSM + median LookTable) does real work**:
  median-HSM-only is 4.83 mean; median-LookTable-only is 6.05; both
  together is 3.60. Ship both stages in `adobe_standard.npz`.
- 33³ and 65³ uncompressed direct-RGB cubes perform equivalently
  (4.11 vs 4.15 mean) — the user's "uncompressed gains fidelity"
  hypothesis does NOT hold. Per-camera tuning variance is the binding
  constraint, not representation compression.

**Deferred to follow-up research, not v0.6:**

- **G2 (parallel viewer)**: dropped unless empirical pilot shows A'
  alone leaves a residual that breaks the workflow. The
  ~1.5 ΔE mean on target modern cameras suggests G2 is unnecessary
  for the primary user base.
- **Shape γ (full LRT replacement)**: held on the horizon for a
  future v1.0 if the project commits to long-term Adobe-free posture
  with sustainable maintenance bandwidth.

**DROPPED (not deferred) per `09f_metadata_passthrough.md`:**

- **Metadata-passthrough emission mode**: ruled out by Resolve's
  documented behavior. Three independent failure grounds, each
  sufficient: (1) Resolve does NOT read XMP develop intent on RAW
  (Wegner-confirmed; Resolve 21 Photo-page docs explicit); (2)
  Resolve's "Camera Raw" is BMD's independent YRGB implementation,
  not Adobe Camera Raw; (3) image-sequence imports apply one Camera
  Raw decode per clip, not per-frame XMP. Engineering scope (~3 wks)
  would produce code Resolve ignores.

**Foreclosed by analysis:**

- Candidate B (cache substitution; dead per `07_decision.md` cache test).
- Candidates G and H proper (per `09b_display_transform.md`: macOS-
  permission fragility and cross-app contamination respectively;
  no Linux story).
- Candidate I-b (full GLSL/Metal port of dt; maintenance burden
  unsupportable per `09c`).
- Candidate E (raw-passthrough as a render mode that escapes the
  problem; per user note 2026-05-26 it does not escape because
  temporal-baked LRT-stage operations still need a stable referent
  at LRT-authoring time).
- Candidate D as v0.6 scope (held on the horizon per above).

## Implementation: A' transform

### What we ship

A new file `src/lrt_cinema/presets/adobe_standard.npz` (project-
derived from Adobe Standard DCP catalog enumeration; ships under
Apache 2.0 as our own format derived from the royalty-free DNG
specification).

The file contains:

- A distilled "shared LookTable" cube: median across the 474 same-
  dimension (36, 8, 16) Adobe Standard LookTables.
- A distilled "shared HueSatMap" cube: median across the 322 same-
  dimension (90, 30, 1) Adobe Standard HueSatMaps. **Ship it.**
  Per `09e_a_prime_ceiling.md`, the cascade (HSM + LookTable) is
  3.60 ΔE mean across the catalog; HSM-only is 4.83; LookTable-only
  is 6.05. The cascade does real work.
- A camera-agnostic baseline-exposure offset of 0.0 (per Q1: Adobe
  Standard ships BaselineExposure=0 for all cameras).
- No ProfileToneCurve (per Q1: 97% of Adobe Standard DCPs ship
  without one; we follow the same convention. Adobe's default ACR3
  baseline curve is applied by dt's basecurve module when invoked
  without a per-camera curve).

### How the runtime path applies it

When `--engine adobe-shared` (new flag), the runner's render path
becomes:

```
RAW → ColorMatrix from camera EXIF (per-camera; bundled in NEF/CR3/DNG)
   → temperature (dt's per-camera kelvin → multiplier math)
   → CAT16 to working space (lin_rec2020)
   → SHARED LookTable + (optional) HSM from presets/adobe_standard.npz
   → emitted via dt's lut3d module
   → CRS-translated user develop ops (existing xmp_emitter path)
   → output (per preset)
```

When `--engine adobe-camera` (existing `--engine dcp` flag, possibly
renamed), the per-camera DCP-distilled HSM + LookTable + ProfileToneCurve
are used instead of the shared transform. This is the opt-in
enrichment path A from the synthesis.

When `--engine algorithmic` (existing), neither A' nor A applies; the
algorithmic pipeline (libraw matrix + dt CAT16) emits without the
Adobe-flavored non-linear stage.

### Files affected

- `src/lrt_cinema/presets/` — new `adobe_standard.npz` shipped artifact.
- `src/lrt_cinema/cli.py` — new `--engine adobe-shared` flag (or a
  policy of `--engine dcp` auto-selecting shared vs per-camera based on
  whether per-camera DCPs are extracted).
- `src/lrt_cinema/runner.py` — wire the shared transform into the
  render pipeline.
- `src/lrt_cinema/lut3d_baker.py` — extend `bake_dcp_cubes_to_resolve_cube`
  to accept a shared-transform variant (or call it the same way with
  the loaded shared transform's HSV cube as input).
- `tools/distill_adobe_standard.py` — new one-shot maintainer-side
  tool that runs `parse_dcp` over the Adobe Standard catalog and
  produces the shared transform `.npz`. Output committed to
  `src/lrt_cinema/presets/`.
- `tests/test_adobe_shared_engine.py` — fixture + integration test
  for the shared-transform path.
- `docs/V06_PLAN.md` — new milestone plan referencing this doc.
- `README.md`, `SCOPE.md` — reword the project's positioning around
  the multi-stage workflow.

### Validation

Per-camera ΔE2000 measurement on the user's existing `tools/diagnose_vs_lrt_preview.py`
harness, run against:

- DSC_4053 baseline (Nikon D750 neutral keyframe; current v0.4
  acceptance gate is 2.24 mean ΔE post-affine — should improve
  marginally with the shared transform's HSV/LookTable layer).
- A small camera-diversity panel: Nikon (D750, Z6), Canon (R5, 5D
  Mark IV), Sony (A7 IV), Fujifilm (X-T5). Borrowed reference RAWs
  + LRT preview JPGs as test fixtures, where available; otherwise
  validation is single-camera baseline.

Acceptance (per `09e_a_prime_ceiling.md` measured panel):

- **Modern-camera class (primary target)**: mean ΔE < 2.5, P95 < 5
  across {Nikon Z 6 / Z f, Canon R5, Sony A7 IV, Fujifilm X-T5,
  Panasonic GH6, Apple iPhone 14+ Pro, Google Pixel 7+}. Measured
  result on the construction set: 1.5 mean across this class.
- **Full-catalog class (extended support)**: mean ΔE < 4, P95 < 12
  across legacy bodies. Measured result: 3.60 mean.

Above broadcast tolerance is acceptable on the full-catalog class
(the workflow has Resolve doing final color). Cinema-reference is
the achievable bar on modern target cameras.

Median HSM decision: SHIP IT. Per `09e_a_prime_ceiling.md`'s
measurement, the median-HSM-plus-median-LookTable cascade outperforms
either alone (3.60 vs 4.83 vs 6.05 mean ΔE). Both stages do real
work. The earlier "drop HSM" hypothesis is empirically falsified.

### Cost

| Work item | Eng-weeks |
|---|---:|
| `tools/distill_adobe_standard.py` — catalog enumeration + median computation | 0.5 |
| Shared transform `.npz` storage + loading | 0.5 |
| Runtime path wiring (`runner.py`, `lut3d_baker.py`) | 0.5 |
| CLI flag + engine selection logic | 0.5 |
| Tests (unit + integration) | 0.5 |
| Validation harness extension | 0.5 |
| Documentation | 0.25 |
| **Subtotal — A'** | **~3.25** |

### Dependencies

- Existing: `colour-science`, `numpy`, dt-cli, the existing DCP
  parser. No new runtime deps.
- Install-time only (maintainer-side): Adobe DNG Converter for the
  one-shot catalog enumeration (already a project dep for
  `tools/extract_dcp.py`).
- Apache 2.0 compatible throughout.

## Implementation: Resolve workflow documentation

### What we document

A new section in `README.md` or `docs/RESOLVE_WORKFLOW.md` that
characterizes the two standard Resolve color-management paths users
will choose between:

#### Path 1: DaVinci YRGB Color Managed (most common)

For users delivering Rec.709 / SDR / standard broadcast:

1. lrt-cinema render: `lrt-cinema render --preset cinema-linear ...`
   (default; 16-bit linear Rec.2020 TIFF).
2. Resolve project setup:
   - Settings → Color Management → Color Science = "DaVinci YRGB
     Color Managed" (or "Color Managed Adv" for finer control).
   - Input Color Space (per-clip or per-project) = "Linear" /
     "Rec.2020 ST 2084" / select "Linear" specifically.
   - Output Color Space = deliverable: "Rec.709 Gamma 2.4" for
     HD/SDR; "Rec.2020 ST 2084" for HDR; etc.
   - Timeline Working Color Space = DaVinci Wide Gamut (default;
     wider than Rec.2020 for grading headroom).
3. Resolve viewport now shows the colorist's grading reference in
   the deliverable display space.
4. Grade with all standard Resolve tools (color wheels, curves,
   HSL, qualifiers).

#### Path 2: ACES (cinema standard)

For users delivering to a cinema ACES pipeline:

1. lrt-cinema render: `lrt-cinema render --preset cinema-aces ...`
   (32-bit float linear OpenEXR).

   *Note*: the `cinema-aces` preset name is slightly misleading —
   it emits 32-bit float linear Rec.2020 EXR, not ACES2065-1 EXR.
   The data is mathematically equivalent up to a 3×3 linear matrix
   transform, which Resolve applies via its ACES input transform.
   The naming may be cleaned up in a future preset refresh.
2. Resolve project setup:
   - Settings → Color Management → Color Science = "ACEScct" (most
     common) or "ACEScc".
   - ACES Input Transform (per-clip) = "Linear Rec.2020" (a clean
     linear matrix conversion to ACES2065-1).
   - Output Device Transform = "Rec.709", "P3-DCI", "Rec.2020 ST 2084
     HDR", etc.
3. Resolve viewport shows the ACES-managed grading view.
4. Grade with all standard Resolve tools.

#### Why not OCIO config emission?

`07_decision.md` v2 and `09b_display_transform.md` (H1 sub-candidate)
flagged "OCIO config emission" as a workflow component. Closer
investigation: Resolve's native CMS handles both Path 1 and Path 2
above without needing a custom OCIO config. Resolve's OCIO support
exists but is for projects that already use OCIO across their
toolchain; it's not the standard color-management path for an
incoming linear-Rec.2020 TIFF/EXR. The user's instinct that "OCIO
isn't a normal grading process in Resolve" is approximately correct:
OCIO is a third color-science option in Resolve's Color Management
settings, used by cinema pipelines that have OCIO configs from
prior steps. lrt-cinema's output works fine in Resolve's native
CMS (Path 1) or ACES (Path 2); no custom OCIO config needed.

If a project demands OCIO interop (e.g., the colorist has an
established OCIO config for the project), the colorist can author a
custom OCIO config that includes "Linear Rec.2020" as an input role.
lrt-cinema does not need to emit anything special.

### Cost

| Work item | Eng-weeks |
|---|---:|
| Write `docs/RESOLVE_WORKFLOW.md` with both paths + screenshots | 0.5 |
| Update `README.md` workflow section | 0.25 |
| **Subtotal — Resolve docs** | **~0.75** |

## Implementation: out-of-scope for v0.6 (follow-up tasks)

These are explicitly NOT in v0.6 scope but are tracked for future
research / implementation:

### Metadata-passthrough mode — DROPPED

Investigated in `09f_metadata_passthrough.md`. Verdict: **drop**, not
defer. Three independent failure grounds:

1. Resolve does NOT read XMP develop intent on RAW (LRTimelapse
   author Wegner confirmed; Resolve 21 Photo-page launch docs
   explicit: "develop settings do NOT transfer").
2. Resolve's "Camera Raw" is BMD's independent YRGB implementation,
   not Adobe Camera Raw. Colorist reviews flag color-accuracy
   problems especially on Fujifilm + iPhone ProRAW.
3. Resolve treats image sequences as a single clip with one Camera
   Raw decode — no per-frame XMP application surface. The LRT-
   authored per-frame Deflicker / HG `crs:LocalExposure2012` deltas
   have no place to land.

Engineering scope (~3 wks) would produce code Resolve ignores.

The metadata-passthrough candidate is closed for the lrt-cinema
project's lifetime unless Resolve introduces XMP-develop-intent
reading on RAW imports (no announced plans).

### CinemaDNG emission mode (v0.7+ candidate)

Per user direction 2026-05-26 ("Add CinemaDNG as a v0.7 candidate"):
characterize whether emitting per-frame CinemaDNG (linear sensor RGB
+ per-frame metadata) gives the colorist meaningfully more flexibility
than the existing cinema-aces OpenEXR preset.

Open questions:

- Can Resolve's CinemaDNG decoder honor per-frame metadata
  (BaselineExposure delta, ProfileToneCurve, white-balance) reliably?
  Empirical testing needed; documented behavior is uncertain.
- Does encoding LRT-stage temporal operations (deflicker + Holy Grail
  exposure deltas) into per-frame CinemaDNG metadata produce the
  same downstream effect as baking them into cinema-aces EXR?
- Does Resolve's CinemaDNG RAW decoder produce visibly different
  results from its OpenEXR / TIFF decoder? Is there a color-science
  reason a colorist would prefer one over the other?
- What's the lrt-cinema engineering scope? darktable does not emit
  DNG natively; a custom DNG writer (rawpy + tifffile, or Adobe DNG
  SDK at install time) would be required. Per-frame metadata
  encoding adds complexity.
- Coverage: does emitting CinemaDNG provide value for non-Bayer
  sensors (Fujifilm X-Trans, Sigma Foveon)?

A scoping document `docs/research/cinema_dng_scoping.md` (or similar
in a future research pass) would close these questions. Rough
estimate: ~1-2 wks of research before scope is clear; if committed,
~2-3 wks to implement. Track as v0.7+ candidate; not in v0.6 scope.

### G2 parallel viewer (deferred)

If post-A' validation reveals the cross-stage gap is materially
larger than ~3 ΔE mean — or if the user's grading workflow shifts
toward HSL-heavy / per-color decisions in LRT — G2 becomes worth
shipping as a v0.7 enhancement. Cost from `09b`: 3–5 wks for the
lrt-cinema-owned viewer window + file watcher + T-correction shader.

### Shape γ (LRT replacement)

Held on the horizon. If the project commits to a long-term Adobe-
free posture with sustainable maintenance bandwidth (per
`09d_lrt_replacement.md`'s realistic 3–6 months/year ongoing
maintenance), Shape γ proceeds as a v1.0 parallel track over 12–18
months. Refined α's artifacts compose forward into γ; nothing in
v0.6 is thrown away if γ later ships.

## PR chain disposition

Following Shape α, the open PRs land as follows:

| PR | Branch | Disposition |
|---|---|---|
| [#11](https://github.com/turgid-secretion/lrt-cinema/pull/11) | `fix/v0.4-defensive` | **Merge.** Independent. |
| [#12](https://github.com/turgid-secretion/lrt-cinema/pull/12) | `fix/xy-camera-neutral-iteration` | **Merge.** Independent. |
| [#13](https://github.com/turgid-secretion/lrt-cinema/pull/13) | `refactor/cli-resolve-profile` | **Merge.** Independent. |
| [#14](https://github.com/turgid-secretion/lrt-cinema/pull/14) | `feat/v0.4-calibration-deterministic` | **Merge.** General-purpose infrastructure; the `.npz` storage layer is exactly what A' ships in `presets/`. |
| [#15](https://github.com/turgid-secretion/lrt-cinema/pull/15) | `feat/v0.4-calibration-dt-roundtrip` | **Keep as Tier 2 baseline foundation.** Documents the per-camera linear fit; useful when A enrichment ships for users opting in to `--engine adobe-camera`. Not load-bearing for A', but not retired. |
| [#16](https://github.com/turgid-secretion/lrt-cinema/pull/16) | `docs/color-option-space-research` | **Merge with this doc + the research history.** All research-pass artifacts (01–10) plus this canonical recommendation (11) land together. |

### Sequencing

1. Merge #11, #12, #13 (independent audit fixes; any order).
2. Merge #14 (calibration storage; underpins A' artifact in `presets/`).
3. Merge #15 (Tier 2 baseline foundation; opt-in enrichment path).
4. Add this doc to #16; merge #16.
5. Open new PR for refined α implementation: A' distillation tool +
   `presets/adobe_standard.npz` shipped artifact + runtime wiring +
   tests + V06_PLAN.md + Resolve workflow documentation.
6. After refined α ships as v0.6: open follow-up research PR for
   metadata-passthrough scoping.

## Action list

If this canonical recommendation is approved as-stated:

1. Merge #11, #12, #13 in CI order.
2. Merge #14 (calibration storage infrastructure).
3. Merge #15 (Tier 2 baseline foundation).
4. Merge #16 with the full research history + this doc.
5. Open the v0.6 implementation PR (refined α components).
6. Schedule metadata-passthrough research as a follow-up task.

If the recommendation needs adjustment, specifics on a per-section
basis go through one more discussion round before any commits land.

## What this recommendation does NOT include

- Cost estimates for v0.7+ work. The metadata-passthrough,
  G2-parallel-viewer, and Shape γ items have estimates in their
  source feasibility studies (`09b`, `09c`, `09d` respectively).
- A schedule. Engineer-weeks is implementation effort; calendar
  schedule depends on the project's stewardship bandwidth.
- Detailed UX / API design. Implementation work surfaces design
  questions as it lands; the recommendation is the contract on
  scope and direction.
- Marketing / community-building strategy. If the user base shifts
  meaningfully under refined α (e.g., users asking for Shape γ
  features), a separate doc surfaces.

## Open questions for user input

1. **`--engine` flag naming.** Use `--engine adobe-shared` for A'
   and `--engine adobe-camera` for A (renaming current `--engine
   dcp` to be more explicit)? Or auto-select based on whether
   per-camera DCPs are extracted (`--engine dcp` stays; the engine
   resolver checks for per-camera DCPs and falls back to shared)?
2. **Median HSM in `adobe_standard.npz`.** Ship it (capturing average
   per-camera HSM character) or drop it (matching the 29% of Adobe
   Standard DCPs without HSM)? Resolve during validation.
3. **`cinema-aces` preset misnaming.** Rename to `cinema-exr` or
   `cinema-linear-32f`, or keep + improve docs? Both work; renaming
   is cleaner but creates a preset-name churn for any users with
   existing scripts. Conservative answer: keep + document.
4. **Validation panel cameras.** Which cameras do we want in the
   acceptance-gate panel beyond Nikon D750? Depends on available
   test data.

These can resolve as v0.6 implementation lands; they don't block the
approval.
