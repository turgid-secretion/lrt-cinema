# Canonical recommendation: refined Shape α

*This is the action plan for v0.6. Supersedes the recommendation
portion of `07_decision.md`. Draws on the empirical inputs in
`09_dcp_variance.md`, the four cluster feasibility studies
(`09a`–`09d`), and the option-space synthesis in `10_synthesis.md`.
The full research is preserved in tree as `01`–`10`; this doc is the
implementation contract.*

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

**Deferred to follow-up research, not v0.6:**

- **G2 (parallel viewer)**: dropped unless empirical pilot shows A'
  alone leaves a residual that breaks the workflow. Per user's
  challenge, A' closing to ~2–3 ΔE mean on clean-translating LRT-
  stage operations should bring decisions inside broadcast tolerance.
- **Metadata-passthrough emission mode**: characterize further before
  committing. Open research task per user direction
  ("investigate more before deciding").
- **Shape γ (full LRT replacement)**: held on the horizon for a
  future v1.0 if the project commits to long-term Adobe-free posture
  with sustainable maintenance bandwidth.

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
  dimension (90, 30, 1) Adobe Standard HueSatMaps. Optional —
  consider dropping in v0.6 and only adding if validation shows
  saturated-chroma drift on per-camera comparison.
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

Acceptance: mean ΔE < 3 mean / < 6 P95 across the panel. Above
broadcast tolerance is acceptable (the workflow has Resolve doing
final color). Stretched goal: mean ΔE < 2 across the panel.

Open question (resolve during validation): drop or ship the median
HueSatMap. Q1's cross-camera variance on HSM is moderate (sat
scale std ~19% per cell mean, ~40% P95); 29% of Adobe Standard
profiles already ship without HSM. If validation shows the median
HSM helps on cameras Adobe tunes aggressively (Apple, Samsung) and
doesn't hurt on others, ship it. If it produces worse results than
no HSM on most cameras, drop it.

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

### Metadata-passthrough mode

User direction (2026-05-26): "investigate more before deciding."
Open research questions:

- Does Resolve's Camera Raw module read LR-shape XMP for the user's
  primary camera (Nikon NEF)? What level of fidelity to ACR's
  PV2012 develop does Resolve's Camera Raw provide on NEF input?
- How does Resolve handle LRT's mask-based deflicker / HG encoding
  (`#LRT internal use (Deflicker)` / `(HG)` corrections inside
  `crs:MaskGroupBasedCorrections`)? Does it apply them at all?
- What's the colorist's workflow if lrt-cinema emits modified LR-
  shape XMP (with deflicker/HG baked into the `crs:Exposure2012`
  field per-frame, rather than via mask-correction)? Does Resolve
  read them correctly?
- Are there cameras where the Camera Raw path produces a visibly
  different / better result than lrt-cinema's dt-rendered TIFF?

A scoping document at `docs/research/metadata_passthrough_scoping.md`
(or similar) would characterize the workflow before committing to
implementation. Estimate: ~0.5–1 wk of research before scope is
clear; if committed, ~2–3 wks to implement.

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
