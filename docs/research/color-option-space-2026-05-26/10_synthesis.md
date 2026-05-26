# Synthesis: weighing all candidates with consistent criteria

*Output of the analysis phase commissioned by `08_search_framing.md`.
Draws on Q1 measurement (`09_dcp_variance.md`) plus four cluster
feasibility studies (`09a_adobe_match.md`, `09b_display_transform.md`,
`09c_parallel_viewer.md`, `09d_lrt_replacement.md`). Produces a
recommendation set conditional on the project's stewardship horizon,
not a single verdict.*

## TL;DR

The four feasibility studies + Q1 measurement collapse the 11-
candidate option space into three viable shapes the project can
commit to:

- **Shape α — A' + G2 + H1 (the "tractable workaround stack").**
  6–9 engineer-weeks. Closes the LRT-stage authoring loop via a
  shared Adobe-Standard transform (A'), a T-corrected parallel
  viewer (G2), and OCIO config emission for the colorist's
  downstream tool (H1). Cheapest viable path; relies on Q1's low-
  variance result; carries ongoing per-LR-version maintenance.

- **Shape β — A + G2 + H1 + F-daemon (the "polished workaround
  stack").** 10–15 engineer-weeks. Same as α but adds per-camera A
  enrichment for users who want per-camera fidelity, plus a
  daemon-only render-to-folder for the "open in any viewer" UX. Same
  workflow shape; tighter ΔE on per-camera basis.

- **Shape γ — D (the "clean-slate replacement").** 31–42 engineer-
  weeks (~8–10 months single-developer), plus 3–6 months/year
  ongoing maintenance. Eliminates the Adobe-match maintenance
  burden entirely. Makes Linux a peer platform. Carries
  single-developer-maintenance and user-adoption risks.

**Default recommendation**: Shape α, with an explicit doc-the-
limitations posture on the persistent residual mismatch. The
workaround stack is materially cheaper, lower-risk, and ships an
escape valve for users on the Adobe-tied workflow today.

**Conditional alternative**: Shape γ if the project commits to a
long-term Linux-peer Adobe-free posture AND has access to a co-
maintainer or funding model that can sustain ~3–6 months/year of
engineering. Without those, γ becomes "perpetually behind LRT" by
construction.

**Foreclosed by analysis**: candidates B (cache substitution; dead
per `07_decision.md` cache test), G proper (Apple private/permission
surfaces trending more restrictive every macOS release per 09b), H
proper (cross-app contamination is OS-design failure mode per 09b),
I-b (GLSL/Metal shader hand-port; maintenance burden unsupportable
per 09c), E (raw-passthrough; doesn't escape per user note 2026-05-26
about temporal-scale bakeable operations).

## Q1 result, briefly

`09_dcp_variance.md` measures cross-camera variance in Adobe DCP
fields. Result on a 480-camera stratified sample across 52
manufacturers:

- **BaselineExposure**: identically zero across all 480. Camera-
  agnostic by Adobe's design.
- **ProfileToneCurve**: present in only 14/480 (3%). Camera-agnostic
  by default.
- **LookTable**: 99% of cameras share the (36, 8, 16) cube
  dimension; per-cell hue shift std mean 2.2°; sat scale std mean
  2.6%; val scale std mean 2.8%. Low cross-camera variance.
- **HueSatMap**: moderate variance (sat scale std mean 19%, P95
  40%). 29% of cameras don't ship one at all.

**A' (camera-agnostic Adobe match) is on the low-variance branch.**
Per `09a_adobe_match.md`'s configuration matrix, this selects
**Configuration 3**: A' as default, A as opt-in enrichment.

## Cross-cluster comparison matrix

Engineer-week estimates from the feasibility studies, normalised to
consistent criteria. Costs sum the "cluster-specific" work plus
shared upstream stages (e.g., G2 inherits A's calibration tower at
the upstream cost). The "shared" column indicates which work items
are common across multiple candidates.

| Candidate | Cluster | Eng cost (wk) | Achievable ΔE2000 | Workflow disruption | Ongoing maintenance | Linux portability | Adobe runtime |
|---|---|---:|---|---|---|---|---|
| **A** (Adobe-match per-camera) | Adobe-match | 5–7 | ~2 mean (SSF), 3–5 (root-poly), 6–12 (matrix fallback) | None | Per-DCP-Converter release | Excellent (data extracted once on Mac/Win, used everywhere) | None at runtime |
| **A'** (Adobe-match shared) | Adobe-match | 3 (Branch 1 if Q1 low-variance) | ~2.5 mean across catalog | None | Per-Adobe-Standard catalog snapshot | **Excellent** (single shared transform shipped in `presets/`) | None |
| **B** (cache substitution) | — | — | — | — | — | — | **DEAD** per `07_decision.md` |
| **C** (current state) | Workflow-only | 0 (today) to 1 (doc reframe) | ~6 pre-affine / ~2 post-affine on neutral keyframes | Author lives with the gap | Minimal | Already cross-platform | None |
| **D** (LRT replacement) | LRT-replacement | 31–42, ~8–10 months | N/A — preview IS the deliverable | **Large** — user retrains on a new UI | 3–6 months/yr indefinitely | **Peer platform from day one** | None |
| **E** (raw-passthrough) | Workflow shape | — | — | Doesn't escape (user note 2026-05-26: LRT-stage temporal ops bake) | — | — | **FORECLOSED** |
| **F** (parallel exact-render viewer) | Parallel-viewer | 2 (daemon-only) – 6 (polished) | Exact (dt-cli render) | Add a second viewer surface | Same as lrt-cinema's existing dt deps | Excellent (PySide6/Qt) | None |
| **G** (LRT preview LUT correction, screen-capture) | Display-transform | 12–17 with upstream A | Inherits A's | Add overlay window over LRT | High (ScreenCaptureKit + Apple-private surfaces trending restrictive) | **None** (macOS-only) | None |
| **G2** (T-corrected parallel viewer, lrt-cinema-owned window) | Display-transform | 3–5 (cluster) + A's tower | Inherits A's | Add a second viewer surface | Same as A | Excellent | None |
| **H** (custom monitor ICC) | Display-transform | 10–13 with upstream A | Inherits A's | **CROSS-APP CONTAMINATION** | Cross-app contamination is structural | **None** (Linux has no system-wide ICC) | None |
| **H1** (OCIO config for Resolve) | Display-transform | 1–2 | Closes dt→Resolve handoff (different problem) | Resolve project setup | Minimal (OCIO is industry-standard) | Excellent | None |
| **I-a** (JIT preview via OCIO LUT-bake) | Parallel-viewer | 4–5 | ~2 ΔE if dynamic ops factor cleanly | Add a second viewer surface | Re-bake LUT on dt update | Excellent | None |
| **I-b** (GLSL/Metal shader port of dt) | Parallel-viewer | 8–12 | Approximate; high fidelity if maintained | Add a second viewer surface | **Every dt module change** | Excellent | None |
| **I-c** (vkdt borrow) | Parallel-viewer | 3–5 (if vkdt's fidelity matches) | Bounded by vkdt port | Add a second viewer surface | vkdt project tracks dt | Excellent | None |
| **J** (reference-track A/B) | Workflow-only | ~0 (workflow, not code) | Whatever the reference is | None | None | None | None |
| **K** (constrained-author + Resolve-downstream) | Workflow-only | 1 (doc) | Clean-translating LRT-stage ops only | Author restricts LRT-stage to subset | Minimal | None | None |

A few notes on the table:

- "Achievable ΔE2000" is a stand-in for "perceptual deliverable
  fidelity." For candidates that close the cross-stage loop (D, K),
  the metric is "preview IS deliverable" rather than a numerical
  match.
- "Workflow disruption" is qualitative: None = grader continues
  using LRT today; Add a viewer surface = grader looks at a second
  window during authoring; Large = grader switches to a new UI.
- "Ongoing maintenance" accounts for both engineering time and
  fragility: an Adobe-DCP-Converter release that changes the
  catalog requires re-distilling A'; an Apple macOS release that
  tightens permissions can break G; etc.
- "Adobe runtime" is "None" for all viable candidates per user's
  hard constraint (preference for none; Adobe at install/build time
  is fine). G via screen capture has no Adobe code at runtime but
  has Apple-permission fragility, which is a different axis.

## Cluster-level summaries

### Adobe-match cluster (A, A')

Per `09a_adobe_match.md`. The cluster's structural privilege: no user
workflow change. Q1 result picks Configuration 3 (A' default, A
opt-in enrichment).

A is cheaper than `08_search_framing.md` estimated because PR #14
(calibration storage) and the in-place `lut3d_baker.py` already ship
the HueSatMap + LookTable trilinear-sample stage that `05_synthesis.md`
called Path 3 ("HSV residual catcher"). It's **shipped, not pending**.
The remaining A work is layered onto a substantially-built foundation.

A' is the surprise win from Q1: distilled across the Adobe Standard
catalog, the camera-agnostic look character is large enough that one
shared transform captures most cameras within a few percent of
saturation/value and a few degrees of hue. The per-camera HueSatMap
residual is the qualifier (29% of cameras don't ship one anyway).

### Display-transform cluster (G, H, G2, H1)

Per `09b_display_transform.md`. Cluster verdict: G and H proper are
both non-viable on Linux and have macOS-specific fragility
(permissions / contamination). The cluster's salvageable contribution
is G2 (T-corrected parallel viewer in an lrt-cinema-owned window) and
H1 (OCIO config emission for the colorist's Resolve project). Both
documented public-API surfaces, cross-platform, no contamination.

G2 and H1 should be elevated to first-class candidates; G and H proper
should be retired.

### Parallel-viewer cluster (F, I)

Per `09c_parallel_viewer.md`. F is engineering-tractable; the
**discriminator is dt-cli per-frame latency**. dt-cli has no daemon
mode (~1–2s startup tax per invocation). With OpenCL + reduced-
resolution render, end-to-end "save → updated preview" plausibly
1–2s on a D750; without OpenCL, 5–10s and probably too slow for
interactive grading.

The recommended F pilot is the **daemon-only** sub-candidate (~2
weeks) — write previews to a folder the user opens in any image
viewer. Doubles as the dt-cli latency benchmark vehicle.

I (JIT preview-quality) collapses to I-a (OCIO LUT-bake; 4–5 wks)
or I-c (vkdt borrow; 3–5 wks if fidelity matches). I-b (full GLSL
port) is rejected on maintenance grounds.

The cognitive-ergonomics section in `09c` flags the load-bearing
workflow test: can the grader develop fluency with a parallel
reference, or does LRT's preview character dominate decisions even
with a deliverable-faithful viewer alongside? Static-image side-by-
side comparison works (soft-proofing, DI dual-monitor); moving-image
parallel reference is weaker.

### LRT-replacement cluster (D)

Per `09d_lrt_replacement.md`. The honest estimate is **31–42
engineer-weeks** (~8–10 months single-developer), with **Visual
Deflicker** as the unbounded item (3–4 wks for serviceable, 8–12
wks for parity-with-LRT on real footage). The previous 16–18 wk
estimate underestimated the UI shell, the fast preview path, and
the deflicker quality target.

D's structural argument: **eliminates the Adobe-match maintenance
burden entirely.** Every workaround candidate (A, A′, F, G, H, I)
embeds a permanent calibration tower. D doesn't. Linux falls out as
a peer platform.

D's structural risk: **single-developer maintenance with no funding
model.** Wegner sustains LRT on a Pro-tier paid product; an Apache-
2.0 free clone has no equivalent. The honest posture is
"perpetually behind LRT on features, acceptable to a small open-
tool-preferring user base." Realistic ongoing maintenance: 3–6
months/year indefinitely.

UI framework choice: **PySide6** wins by constraint (single Python
developer, cross-platform mac + Linux, free license). Tauri/Iced/
Slint add a Rust language boundary unsustainable for one developer;
Electron's binary overhead is worse than PySide6; ImGui is wrong
shape; native AppKit + GTK is two codebases.

Roughly **half of D's non-UI surface is already implemented** in the
lrt-cinema codebase (parser, emitter, interpolation, DCP, runner).
The new work is dominated by the GUI shell, fast preview path, and
Visual Deflicker.

## Three viable shapes

The candidates resolve into three shippable configurations the
project can commit to. Each comes with its own engineering cost,
risk profile, and user-experience trade.

### Shape α — A' + G2 + H1 (the tractable workaround stack)

**What it ships.**

- **A'**: a single Adobe-Standard-distilled transform in
  `presets/adobe_standard.npz`, applied at render time. Per-camera
  ColorMatrix comes from the camera's bundled DNG metadata or from
  `tools/extract_dcp.py`'s output; no per-camera HSM/LookTable
  database needed.
- **G2**: a small lrt-cinema viewer window that file-watches
  `.lrt/visual/*.lrtpreview` JPEGs, applies a T-correction
  (Adobe→dt transform), and displays in a color-managed window
  that the grader cross-references during LRT authoring. The
  viewer is owned by lrt-cinema (so its colorspace is set
  properly via documented APIs), avoiding G's screen-capture
  fragility.
- **H1**: OCIO config emission. When lrt-cinema renders a
  sequence, it also emits an OCIO config that maps its
  rendered TIFF's working space to a downstream tool's
  deliverable view. The colorist loads the TIFF into Resolve
  under that OCIO config; Resolve's viewport view matches what
  lrt-cinema rendered.

**Total cost**: 6–9 engineer-weeks (A' 3 + G2 3–5 + H1 1–2). All
three components are independently useful; partial-stack ships are
viable.

**Loop closure**: LRT-stage authoring loop closes approximately
(G2's T-correction depends on A's tower; A's ceiling is ~2 mean ΔE
when SSF available, ~3–5 on root-poly, ~6–12 on matrix fallback —
but for the A' path on a shared transform, the ceiling is ~2–3 mean
ΔE across the catalog). dt→Resolve handoff loop closes via H1.

**Workflow disruption**: minimal. Grader continues using LRT;
glances at the G2 viewer; loads Resolve under the H1 OCIO config.

**Ongoing maintenance**: re-distill A' on each Adobe DNG Converter
catalog refresh (~quarterly). G2 plumbing is stable. H1 OCIO config
is one-shot per release.

**Linux portability**: A' ships a single `.npz`, no per-user
extraction; G2 uses PySide6/Qt for the viewer window (cross-platform);
H1 emits OCIO configs (cross-platform standard). **Excellent** Linux
portability.

**Risks**:
- Q1 result conditioning: low-variance branch holds; if a later re-
  measurement against a new ACR release shows higher variance,
  A' becomes degraded and the A opt-in enrichment becomes more
  important.
- G2's T-correction depends on A's tower being computed; the
  cluster's value scales with the tower's ΔE ceiling.
- Cognitive ergonomics of the parallel viewer (per `09c`'s analysis):
  grader's muscle memory against LRT's preview may dominate even
  with a deliverable-faithful viewer alongside. **Load-bearing
  workflow test before committing to G2 polish.**

### Shape β — α + A enrichment + F-daemon (the polished workaround stack)

Same as α plus:

- **A**: per-camera HSM/LookTable enrichment for cameras where the
  user wants per-camera fidelity. Opt-in via `--engine adobe-camera`.
  Falls back to A' for cameras without per-camera DCP extracted.
- **F-daemon**: a background daemon that renders preview JPEGs to a
  folder the user opens in any image viewer. Duplicates G2's
  function for users who prefer a no-window workflow.

**Total cost**: 10–15 engineer-weeks (α 6–9 + A enrichment 2–4 + F-
daemon 2).

**Trade vs α**: tighter ΔE on per-camera basis (matters for cameras
Adobe tunes aggressively — Apple, Samsung). Adds a daemon-only
workflow for users who want the "JPEGs in a folder" UX.

**Workflow disruption**: same as α; the additions are opt-in.

**Ongoing maintenance**: α's plus per-camera DCP extraction
maintenance (`tools/extract_dcp_library.py` already handles this for
the existing pipeline; the work is wiring the enrichment path).

**Linux portability**: A enrichment requires the user to have access
to a Mac/Windows machine for the one-shot Adobe DNG Converter
extraction; once extracted, the `.npz` files are cross-platform.

### Shape γ — D (the clean-slate replacement)

**What it ships.**

- A new PySide6 application that replaces LRT for timelapse-grading
  authoring. The preview pane is rendered by lrt-cinema's own
  pipeline. No Adobe DNG Converter anywhere. No closed-source
  PV2012 anywhere. Linux is a peer platform from day one.

**Total cost**: 31–42 engineer-weeks for v1 (Tier 1 feature parity),
plus 3–6 months/year ongoing maintenance indefinitely.

**Loop closure**: complete. The author's preview IS the deliverable's
color science. No cross-stage gap.

**Workflow disruption**: **large**. Grader retrains on a new UI.
LRT-specific muscle memory does not carry over completely.

**Ongoing maintenance**: 3–6 months/year. dt module version churn,
RAW format coverage updates, OS/Qt updates, Visual Deflicker quality
iteration on user-reported real footage.

**Linux portability**: peer platform from day one. No Adobe DNG
Converter dependency anywhere.

**Risks**:
- **Single-developer maintenance is the highest structural risk.**
  Without a co-maintainer or funding model, the project becomes
  "perpetually behind LRT" within 2–3 years.
- **User adoption.** LRT users have years of muscle memory; some
  will not switch. The realistic posture is "small but stable user
  base" rather than "displacing LRT."
- **Visual Deflicker quality.** Research-grade problem; LRT-parity
  on real footage is a 12+ month iteration target.

## Default recommendation

**Ship Shape α (A' + G2 + H1) over the next 6–9 engineer-weeks.**

Reasoning:

1. **Q1 enables A'.** The measured low-variance result is the
   biggest single piece of evidence in this whole analysis: a single
   shared transform captures most of Adobe Standard's look. Without
   Q1, the cluster collapses to A (5–7 wks); with Q1, A' (3 wks) is
   sufficient and Linux portability becomes trivial.

2. **G2 is the cluster's salvageable surface.** G and H proper are
   dead per `09b`. G2 in an lrt-cinema-owned window avoids all the
   cross-app, private-API, Linux-portability traps. The grader gets
   a deliverable-faithful reference during LRT authoring without
   fighting LRT's preview pipeline.

3. **H1 closes a separate loop.** Even if the LRT-stage loop is
   imperfect, the dt→Resolve handoff loop closes cleanly via OCIO.
   This is high-value, low-cost work that ships regardless of the
   LRT-stage decision. Worth doing on its own.

4. **The maintenance burden is acceptable.** Per-ACR-release re-
   distillation of A' is mechanical (run a script; new shared
   transform; ship). G2 plumbing is stable engineering. H1 OCIO
   configs are one-shot per release.

5. **The user's hard constraints are satisfied.** No Adobe runtime
   (Adobe at install/build time only, for the one-shot A'
   distillation on the maintainer's machine). Full GUI authoring
   on the table — G2 uses a GUI window. macOS-first, Linux primary
   alternate — all three components are Linux-clean.

6. **Shape α composes with Shape γ if the project later wants D.**
   The A' transform from α becomes the look reference for the
   deliverable's color science under D. G2's viewer architecture
   informs D's preview pane. H1's OCIO config emission carries
   through to D's render output. **None of Shape α's work is
   thrown away if Shape γ ships later.**

The recommendation is conditional on:

- **Q1 result holds.** If a re-measurement against a future ACR
  release shows higher variance, the recommendation degrades from
  α to β (adding the A enrichment).
- **The grader's cognitive ergonomics with a parallel viewer
  works.** This is the load-bearing workflow test (per `09c`). If
  the grader cannot develop fluency with G2 as a reference, G2's
  value drops and Shape α resembles Shape C (current state). Pilot
  G2 as a daemon-only first (cheap; 2 weeks; doubles as the
  workflow test).

## Conditional alternatives

### Choose Shape β if you want per-camera fidelity

If users routinely shoot multiple cameras and want per-camera Adobe-
match fidelity (e.g., on cameras Adobe tunes aggressively like Apple/
Samsung phones), opt in to Shape β. Costs 4–6 more weeks; tightens
ΔE on per-camera basis; preserves α's structural simplicity.

### Choose Shape γ if you commit to a long-term Adobe-free posture

If the project's stewardship can sustain 3–6 months/year of
maintenance indefinitely, AND you accept the "perpetually behind
LRT on features" posture, AND you specifically want Linux as a peer
platform AND no Adobe runtime AT ALL (even at install time), Shape γ
is the path. 31–42 engineer-weeks for v1; ongoing maintenance
indefinitely.

Recommended sequencing if Shape γ is selected: **ship Shape α first**
as v0.6 (6–9 weeks). Then commit to Shape γ as a parallel track
(starting at some point) targeting v1.0 in 12–18 months. Shape α's
artifacts carry forward into γ; nothing is thrown away.

### Choose K (constrained-author + Resolve-downstream) if the project wants minimal engineering

If the project's stewardship cannot commit to even 6–9 engineer-
weeks, the cheapest viable shape is to **document the constraint
explicitly** (K): authors restrict LRT-stage operations to the
clean-translating subset (Exposure, WB, transitions, identity-or-
near-identity tone curve), and final color happens in Resolve. This
is ~1 week of documentation work. Per `07_decision.md`, the residual
ΔE on this subset is ~2 ΔE post-affine — broadcast-acceptable after
a single Resolve grade.

This shape does NOT close the loop; it asks the grader to live with
the gap. Surfaced here because it's a real fallback if engineering
budget collapses.

## PR chain implications, conditional

| PR | Shape α | Shape β | Shape γ |
|---|---|---|---|
| [#11](https://github.com/turgid-secretion/lrt-cinema/pull/11) (defensive) | Merge | Merge | Merge |
| [#12](https://github.com/turgid-secretion/lrt-cinema/pull/12) (xy iteration) | Merge | Merge | Merge |
| [#13](https://github.com/turgid-secretion/lrt-cinema/pull/13) (cli refactor) | Merge | Merge | Merge |
| [#14](https://github.com/turgid-secretion/lrt-cinema/pull/14) (calibration storage) | Merge | Merge | Merge (calibration .npz is the IR that A' ships in `presets/`) |
| [#15](https://github.com/turgid-secretion/lrt-cinema/pull/15) (Tier 2 3×3 fit) | Keep as Tier 2 baseline (A's foundation) | Keep | Retire (D doesn't need Adobe-match calibration) |
| [#16](https://github.com/turgid-secretion/lrt-cinema/pull/16) (research) | Merge after this doc lands | Merge | Merge |

PRs #11–14 merge regardless of shape; the calibration storage
infrastructure (#14) is general-purpose and is the IR that A' ships
its distilled transform in. PR #15's fate depends on whether the
Adobe-match path stays in scope (shapes α/β yes; γ no — D escapes
the Adobe-match maintenance burden entirely).

## Concrete action list

If Shape α is approved:

1. Merge #11, #12, #13, #14 in CI order.
2. Land #15 as the Tier 2 baseline foundation (A's linear stage).
3. Open follow-up PRs for:
   - A' distillation tool + shared `.npz` preset.
   - G2 viewer (PySide6 + file watcher + T-correction shader).
   - H1 OCIO config emission (extends `runner.py` output path).
4. Add this doc + the 09a-09d feasibility studies + this synthesis
   to PR #16; merge #16.
5. Re-anchor README / SCOPE / V04_PLAN around the multi-stage
   workflow with explicit G2 + H1 documentation.

If Shape β is approved:

- Same as α, plus open a follow-up PR for A enrichment after A'
  ships.

If Shape γ is approved:

- Same as α/β for #11–#14 (the infrastructure stays useful).
- Begin γ as v1.0 parallel track; α/β as v0.6 ships in 6–9 weeks
  while γ proceeds on a multi-month horizon.
- Recruit a co-maintainer if γ proceeds; document the funding-model
  posture explicitly.

## Open questions for user input

1. **Which shape (α / β / γ) to commit to?** The recommendation is
   α. β and γ are conditional alternatives surfaced for completeness.
2. **G2 viewer pilot:** start with daemon-only (cheapest) or
   PySide6 window from day one? Recommendation: daemon-only for
   workflow validation, then upgrade to window if the workflow
   test confirms viability.
3. **A' distillation methodology:** median, weighted-mean, or a
   specific "reference camera" (e.g., Nikon D750 as a known-good
   Adobe Standard target)? Recommendation: median across the
   (36, 8, 16) LookTable group for the cube; ACR3 baseline curve
   for the tone curve.
4. **HSM handling in A':** drop entirely (match the 29% of cameras
   without one), ship a median HSM in `presets/`, or per-family HSMs?
   Recommendation: drop in v1; revisit if user reports surface
   saturated-chroma drift.
5. **PR #15 final disposition:** keep as Tier 2 baseline (shapes
   α/β) or retire (shape γ)? Recommendation: keep; the Tier 2 baseline
   is genuinely useful as the A path's linear foundation.

## What this synthesis does NOT do

- Pick a shape unilaterally. The recommendation is α; the user can
  commit to α or redirect to β / γ / K.
- Schedule the work. Engineer-weeks are estimates; calendar-time
  schedules depend on the project's stewardship bandwidth.
- Validate the per-camera A' result against real test footage. The
  Q1 measurement is structural (cross-camera variance); the
  user-perceptual validation happens during α implementation.
- Address marketing / community / adoption strategy. If Shape γ is
  selected, a separate doc on community-building strategy is
  warranted.

## Provenance

This synthesis weighs:

- `09_dcp_variance.md` — empirical Q1 measurement (Adobe DCP catalog
  variance).
- `09a_adobe_match.md` — Adobe-match cluster feasibility (A, A').
- `09b_display_transform.md` — display-transform cluster feasibility
  (G, H, G2, H1).
- `09c_parallel_viewer.md` — parallel-viewer cluster feasibility
  (F, I).
- `09d_lrt_replacement.md` — LRT-replacement cluster feasibility
  (D).
- `08_search_framing.md` — the clean-sheet framing with user
  constraints inline.
- `07_decision.md` — the cache-test result and ACES correction
  (still valid factual inputs).
- `05_synthesis.md` — math primitives (root-poly, SSF-IDT, HSV
  residual catcher).
- User feedback 2026-05-26 across multiple iteration rounds; key
  corrections: Path C/E don't escape temporal-baked LRT operations;
  ACES analogy was misapplied (loop doesn't auto-close at Resolve);
  recommendations must be workflow-conditional not collapsed.
