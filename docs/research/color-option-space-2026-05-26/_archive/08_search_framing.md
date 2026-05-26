# Clean-sheet search framing for the color-correction bind

*Starts over from the problem in maximally-general form. Past searches
(documented in `01`–`05`) and the first decision-doc draft (`07_decision`,
v1) pre-determined the solution space to "match Adobe via a calibration
tower." That framing closed the search before it began. This doc
defines the search at a layer of abstraction that admits solutions
from unrelated domains and surfaces the constraints needed to bound
the next research pass.*

## Problem statement, maximally general

A human author makes decisions through a feedback system that uses
Rendering Pipeline A. A downstream system produces the deliverable
using Rendering Pipeline B. The author's decisions must be perceptually
consistent with the deliverable, or the decisions are mis-calibrated.

That is the problem to solve.

## Domain instantiation

In lrt-cinema's specific case:

- **Pipeline A** = LRTimelapse 7.x's preview pipeline = Adobe DNG
  Converter (bundled) + LRT's internal JPEG-encode. Closed-source,
  Adobe-rooted, version-churning.
- **Pipeline B** = the deliverable pipeline = darktable (current) +
  the colorist's downstream tool (Resolve / Baselight / Nuke).
  Open-source up to the colorist's tool.
- **Author's decisions** = LRT keyframe authoring AND LRT temporal
  operations (deflicker, Holy Grail, transitions, exposure ramps).
- **Perceptual consistency** = what the author sees in LRT's editor
  pane corresponds to what the deliverable looks like at the
  colorist's monitor.

### Properties unique to this instantiation

These properties constrain the search but do not by themselves
determine the answer:

1. **LRT's temporal operations have no clean substitute downstream.**
   Deflickering smooths long-form luminance fluctuations across
   thousands of frames; Holy Grail compensates for shooter-side
   exposure changes; long-form keyframe interpolation produces
   continuous-over-time develop deltas. Resolve and other NLEs treat
   color decisions as clip-attached, not continuous-over-time. The
   temporal authoring HAS to happen in LRT (or a tool with equivalent
   temporal capabilities; none documented).

2. **Pipeline A's preview rendering is closed and uncontrollable.**
   LRT bundles Adobe DNG Converter for proxy DNG generation; the
   preview JPEGs derive from those proxies via an internal LRT path
   we cannot redirect, monkey-patch, or substitute (per the
   `07_decision.md` cache-behavior test).

3. **Pipeline A's underlying color science (Adobe PV2012) is also
   closed.** Per `docs/research/DNG_SDK_FEASIBILITY.md`, the
   PV2012 math lives in the closed `Camera Raw.plugin` / `acr.dll`;
   no headless executor exists. Adobe DNG SDK's `dng_validate`
   strips XMP CRS before render and lacks PV2012 entry points.

4. **The author is a human making perceptual decisions.**
   Numerical metrics (ΔE2000, ΔE2000-CIEDE) are proxies for
   perceptual equivalence; they correlate but do not equal it.
   "The deliverable matches the author's intent" is the actual
   success criterion.

5. **Temporal-scale changes are not recoverable downstream.**
   Deflicker amplitudes and Holy Grail ramp shapes are baked into
   the temporal sequence at the LRT-authoring stage. Resolve can
   add per-frame adjustments on top but cannot reverse-engineer
   the LRT-stage temporal decisions and re-apply them differently.
   (User noted 2026-05-26: "It is *not* easy or even completely
   possible to unwind the kinds of changes that LRT is designed
   to make.")

## What past searches pre-determined

The previous research passes (`01`–`05`) and the initial decision-doc
draft (`07`, v1) anchored on three assumptions that closed the search:

1. **Pipeline B is darktable.** The search examined alternatives to
   match Adobe with dt's modules, but did not seriously question
   whether dt is the right rendering target.

2. **The success metric is ΔE2000 vs LRT preview.** The search
   optimized for closing this gap rather than asking what the gap
   represents in terms of the author's experience.

3. **The grader's reference is LRT preview at the LRT-authoring
   stage.** The search took this as a fixed property of the
   workflow, not as a design choice that admits alternatives.

These three assumptions are not wrong; they have been observably
useful. They are, however, restrictive. The novel-solution search
must hold them loosely.

## Solution-pattern survey from adjacent fields

The cross-pipeline authoring-consistency problem appears in many
domains. The patterns below are the structural ones that recur. Each
is listed in the abstract first, then translated to our domain.

### Pattern 1: Calibrate the author's display to the deliverable

*Abstract:* the author's display is calibrated, via an ICC profile or
hardware LUT, to render values the same way the deliverable does. The
author sees what the deliverable will show. Used in: ICC soft-
proofing, print preview, calibrated grading suites.

*Translated:* the author sees LRT preview through a display transform
that maps LRT's Adobe-pipeline output to lrt-cinema's deliverable
appearance. Implementation surfaces include OS-level per-app color
management, monitor LUT loaders (DisplayCAL, Calman, Blackmagic),
and per-window color rendering hooks.

### Pattern 2: Parallel display of both pipelines

*Abstract:* the author looks at two displays side by side. One shows
the authoring pipeline's view; the other shows the deliverable's
view of the same content. Used in: cinema previs, AAA game cinematic
review, broadcast-vs-archive monitoring.

*Translated:* a separate viewer (window, second monitor) renders
lrt-cinema's deliverable output for the same XMP the author is
editing in LRT. The author cross-references both views while making
decisions.

### Pattern 3: Quantitative metric + corrective transform on the
*upstream* side

*Abstract:* compute a transform from A to B once; apply the inverse
on the author's display so what they see matches what B produces.
Used in: room-correction audio (Dirac), reference loudspeaker
correction.

*Translated:* compute the Adobe→dt transform once; apply it to the
LRT preview before display. Differs from Pattern 1 in that the
transform is content-aware (depending on the LRT preview's actual
pixel values), not display-attached.

### Pattern 4: Standardized intermediate representation

*Abstract:* both sides agree on a reference representation; each
pipeline converts to and from it through known, stable transforms.
Used in: ACES (ACES2065-1 exchange), audio mastering (24-bit/96kHz
masters), film (D65 reference printing).

*Translated:* lrt-cinema and LRT could both be made to convert to
some standard representation. But LRT's pipeline is closed; this
direction is only available if a reference representation can be
derived from LRT's output without modifying LRT.

### Pattern 5: Round-trip validation at sample points

*Abstract:* during authoring, periodically commit and review the
deliverable on representative samples; iterate. Used in: software
TDD, music recording session "playbacks," prototype testing.

*Translated:* the current state. Author iterates by editing in LRT,
rendering via lrt-cinema, reviewing. Slow loop; cognitively expensive
(author must mentally fuse references).

### Pattern 6: Procedural encoding instead of baked rendering

*Abstract:* the author encodes intent as a parametric description;
the deliverable system realizes the intent in its own pipeline. Used
in: music notation, BIM, parametric design.

*Translated:* lrt-cinema's XMP-in / TIFF-out is exactly this shape
already. The author's intent is encoded; we realize it. The wrinkle
is that the author's *intent* is itself authored against an
Adobe-rooted preview, so "encoding intent" doesn't escape the
authoring-stage reference mismatch.

### Pattern 7: Procedural authoring inside the deliverable pipeline

*Abstract:* combine authoring and delivery into one pipeline. The
author works directly in the deliverable's color science. Used in:
in-engine game cinematics (Unreal sequencer), DAW mastering.

*Translated:* an authoring UI that's part of lrt-cinema (or
deliverable-pipeline-native), supplanting LRT entirely. Path D in
the `07_decision.md` taxonomy. Engineering cost is large; loses
LRT's mature temporal toolkit.

## Solution candidates the survey suggests

The cross-product of patterns × our domain produces a list of
candidates. Some have been examined (the Adobe-match tower lives in
Pattern 6 with a partial Pattern 3 correction baked into the inverse
direction); some have not. Listed here without ranking:

- **A. Adobe-match tower (per-camera)** (Pattern 6, Pattern 3
  inverted). Make Pipeline B → Pipeline A in our render. Examined in
  `05_synthesis.md`. Requires per-camera calibration data (DCP
  distillation, SSF, or chart shot). Engineering cost dominated by
  the calibration tower plus per-camera coverage.
- **A′. Adobe-match transform (camera-agnostic)** (Pattern 6,
  Pattern 3 inverted). Hypothesize that a meaningful fraction of the
  "Adobe look" is *not* per-camera-specific — that the per-camera
  variance in Adobe DCPs is small enough that a single shared
  transform captures most of the perceptual character. If true,
  drastically reduces the calibration-data burden and may close the
  loop for the LRT-as-primary-grader workflow without per-camera
  database. Open empirical question — needs measurement of DCP
  LookTable / ProfileToneCurve / BaselineExposure variance across
  the Adobe camera catalog. See "Measurement targets" below.
- **B. LRT cache substitution** (Pattern 2 in a degenerate form).
  Foreclosed empirically per cache-test (`07_decision.md`).
- **C. dt-native render + accept residual** (Pattern 5). Current
  state. Author lives with the slow iteration loop.
- **D. LRT replacement** (Pattern 7). Costly. Quantified below at
  apples-to-apples scope; surfaced here because the user has
  explicitly asked for D to be considered alongside workarounds
  rather than dismissed.
- **E. Raw-passthrough render** (Pattern 6 with deferred
  application). Per user note 2026-05-26, does not escape because
  temporal authoring still happens against LRT-rooted reference at
  LRT stage.
- **F. Parallel viewer** (Pattern 2). Lrt-cinema runs a side window
  showing dt-rendered output for the keyframe the user is editing
  in LRT.
- **G. LRT preview LUT correction** (Pattern 3). Apply Adobe→dt
  transform to LRT preview JPGs before they reach the user's eye.
  Implementation surfaces: ICC display profile (cross-app
  contamination), per-app ICC on macOS (fiddly but real), Blackmagic
  display LUT loader (hardware-attached), custom window overlay
  (macOS frame buffer interception).
- **H. Display calibration via macOS Color Sync** (Pattern 1).
  Define a custom ICC profile for the user's monitor that maps
  LRT-output values to dt-output appearance. Same cross-app problem
  as G but applied as a system-level color profile rather than per-
  app.
- **I. JIT preview-quality dt render** (Pattern 5 accelerated).
  Lightweight shader or fast Python pipeline that approximates dt's
  output in real-time, displayed alongside LRT. Different from F in
  that the render is fast but approximate, not slow and exact.
- **J. Reference-track A/B** (Pattern 5 with stable anchor). The
  author authors a small reference sequence (a few frames) through
  both pipelines and uses it as a perceptual calibration anchor for
  the larger sequence. Doesn't close the loop but provides a stable
  reference point for translation training.
- **K. Constrained-author workflow** (Pattern 5 + Pattern 6). The
  author restricts LRT-stage authoring to operations whose Adobe→dt
  translation is mathematically well-defined (linear exposure,
  chromatic adaptation, identity-or-near-identity tone curve); all
  perceptually-targeted operations defer to Resolve. Path C in the
  `07_decision.md` taxonomy, sharpened with explicit operation
  restrictions.

## Measurement targets for the analysis phase

Two empirical questions, surfaced explicitly here so the next research
pass can answer them before recommending or eliminating candidates.

### Q1: How much of Adobe color is per-camera vs shared?

The Adobe DCP format encodes per-camera content in five places:
ColorMatrix (per-illuminant), ForwardMatrix (per-illuminant),
HueSatMap (per-illuminant), LookTable (per-illuminant), and
ProfileToneCurve, plus the scalar BaselineExposure /
BaselineExposureOffset. The first two are necessarily per-camera (they
encode sensor SSF → reference-space). The remaining three could vary
widely across cameras, vary little, or fall somewhere in between.

The variance is directly measurable. Adobe DNG Converter ships
profiles for ~4000+ cameras; the files are public, well-documented
TIFF/IFDs (per `src/lrt_cinema/dcp.py`), and parseable. The
measurement asks: for a sample of N cameras, what fraction of the
perceptual look-character lives in the per-camera-variable HueSatMap /
LookTable / ProfileToneCurve, vs in the camera-agnostic remainder?
The methodology is well-defined: render a fixed reference scene
through each camera's DCP with all develop ops zeroed, compute the
pairwise ΔE2000 distribution, and separately the ΔE2000 distribution
against a hypothesized "median" DCP.

Outcomes determine whether A′ (camera-agnostic Adobe match) is a
viable candidate:

- **Low variance** (cross-camera mean ΔE < ~2): the per-camera
  content is largely aesthetic noise; A′ is viable. The calibration
  tower's per-camera scope reduces to "extract the ColorMatrix from
  the camera's bundled DNG metadata" — a one-line operation, no
  database needed.
- **Medium variance** (~2–5): A′ partially viable for some camera
  classes (e.g., one DCP per camera family); per-camera-family
  database needed instead of per-individual-camera.
- **High variance** (> ~5): A′ not viable; per-camera calibration
  is essential. Falls back to A.

Cost to answer Q1: rough estimate, ~1–2 engineer-weeks. The DCP
parser and a measurement harness already exist (`src/lrt_cinema/dcp.py`,
`tools/extract_dcp_library.py`). Adding a variance-measurement script
is mechanical; the bottleneck is access to a sample of Adobe DCPs
representative of the full catalog (user's local Adobe DNG Converter
install provides this).

### Q2: What is the full engineering cost of D (LRT replacement)?

The previous analyses dismissed D as "multi-engineer-month, out of
scope" without enumerating the work. Per user request 2026-05-26,
the analysis phase should quantify D at apples-to-apples comparable
scope to the workaround candidates (F, G, I), so the question "is a
workaround actually cheaper than a clean rebuild?" can be answered
with comparable numbers, not assumed.

Concrete work-item enumeration for D-v1:

| Component | Existing in lrt-cinema? | New work (rough estimate) |
|---|---|---|
| Sequence import (read RAWs, sort, surface metadata) | Partial — parser+runner read XMP, no sequence index | ~1 wk |
| Keyframe authoring UI (select frames, edit per-frame values, save) | None | ~3–4 wks (GUI framework choice dependent) |
| Auto Transition spline interpolation | Yes — linear + Catmull-Rom | 0 wks (existing) |
| Visual Previews render path (real-time enough for UI) | Partial — render pipeline exists but is dt-cli-bound and slow | ~2 wks for a fast preview-quality path |
| Visual Deflicker (luminance analysis + smoothing + writeback) | None — applied path only | ~3–4 wks (algorithm + UI + tuning) |
| Holy Grail Wizard (EXIF-driven exposure-change detection + compensation curve) | None | ~2 wks |
| XMP save (LRT-compatible mask-correction encoding) | Partial — emitter writes dt-shape XMP, not LRT-shape | ~1 wk |
| GUI framework integration + packaging + cross-platform | None | ~2 wks |
| Testing + documentation + user-onboarding | Existing CLI test infrastructure | ~2 wks |
| **Total v1 estimate** | | **~16–18 engineer-weeks (~4 months)** |

This is rough — the GUI choice (Tauri / Qt / Electron / Iced /
native AppKit) significantly affects the GUI-component estimates;
the Visual Deflicker algorithm choice (TLDF-style, Gunther's
proprietary smoothing, or our own) significantly affects estimate
quality; and the UI fidelity target (proof-of-concept that does the
job vs. polished alternative to LRT) bounds the spread.

For comparison with the workaround candidates the analysis phase
should produce comparable estimates:

| Candidate | Rough estimate (engineer-weeks) | What's in the estimate |
|---|---|---|
| A. Adobe-match tower (per-camera, full stack) | ~6–8 | Root-poly + SSF-IDT + HSV residual catcher per `05_synthesis.md` |
| A′. Adobe-match (camera-agnostic) if Q1 says viable | ~2–4 | One shared transform, no calibration tower |
| C. dt-native + doc reframe | ~1 | Documentation work only |
| D. LRT replacement | ~16–18 | All of the above |
| F. Parallel viewer | ~2–4 | File-watcher + fast render + viewer window |
| G. LRT preview LUT correction | ~1–3 + ongoing fragility | Display-LUT plumbing; high risk of cross-app contamination or version churn |
| H. Display calibration via Color Sync | ~1–2 + ongoing fragility | Custom ICC; same fragility risks as G |
| I. JIT preview-quality dt render | ~3–5 | Fast approximate dt pipeline + viewer integration |
| J. Reference-track A/B | ~0 (workflow, not code) | A handful of test renders the user views as anchor |
| K. Constrained-author + Resolve-downstream | ~1 (doc) | Same as C with explicit op restrictions |

These numbers are first-pass; the analysis phase should sharpen them.
The user's point lands: F's ~2–4 weeks against D's ~16–18 weeks is a
~4–6× ratio, not a 10–20× ratio. A combined F+G+I "polished
parallel-display story" could approach 8–12 weeks, which is starting
to be in shouting distance of D's full cost — at which point D's
appeal as a clean-slate solution increases. The analysis phase
should make these tradeoffs explicit rather than dismissing D
preemptively.

### Why "clean-slate" engenders its own challenges

Per user note 2026-05-26, the clean-slate path (D) should be
considered alongside other candidates *with its own challenges
captured and quantified.* The challenges include:

- **User retraining cost.** LRT users have years of muscle memory in
  LRT's UI; a new UI imposes a re-learning cost not visible in the
  engineering estimate above.
- **LRT feature parity.** LRT has accumulated capability over a
  decade (HDR merge, multi-pass deflicker, Optimize-feature,
  comprehensive EXIF handling). v1 of a replacement will not match.
- **Compatibility loss.** LRT-authored sequences from collaborators
  who use LRT won't roundtrip cleanly; users with legacy LRT
  projects can't easily migrate.
- **Single-developer maintenance risk.** LRT is maintained by
  Gunther Wegner with a paid-product business model. A clean-slate
  replacement absorbs full maintenance burden onto whatever team
  builds it.
- **Workflow lock-in choice.** D replaces "LRT's Adobe-tied
  workflow" with "lrt-cinema's dt-tied workflow." A different
  flavor of lock-in, but lock-in nonetheless.

These should be in the analysis phase's per-candidate matrix
alongside engineering cost, not buried in narrative text.

## Hard-no constraints (need user input)

These constraints would prune solution candidates. The previous
research pass did not surface them explicitly; getting them on the
record now lets the next pass converge faster.

| Constraint | Why it matters | Question for the user |
|---|---|---|
| Adobe code in runtime | The Adobe DNG SDK is usable but does not implement PV2012. Adobe Camera Raw plugin requires GUI / LR. Hard-no on Adobe-code-in-runtime forecloses C / E variants that route through Adobe; relaxable opens "use Adobe to render via LR Lua SDK overnight" or "ship DNG SDK as a build dep." | Is Adobe code in lrt-cinema's runtime acceptable? **User answer (2026-05-26):** Acceptable if it cleanly solves the problem, **with preference for none-at-runtime**. If Adobe is in the runtime, it must be an **alternate pipeline**, not the only pipeline. Adobe at install/build time is fine (e.g., the existing `tools/extract_dcp.py` shells out at user's option). |
| Platform requirement | Some Pattern 1 / Pattern 3 implementations are macOS-only (per-app ICC, CoreGraphics hooks). Cross-platform requires a different implementation surface. | Must the solution work on Windows / Linux, or is macOS-only acceptable? **User answer (2026-05-26):** macOS-first acceptable; **Linux is the primary alternate platform**; Windows compatibility welcomed if it falls out of the design but not a constraint. |
| License preference | Apache 2.0 strict (current) limits dependency choices. GPL-acceptable opens dcamprof; proprietary-acceptable opens commercial display-LUT loaders and similar. | What license envelope is acceptable? |
| Engineering budget | Pattern 7 (LRT replacement) is multi-month; Pattern 1 (display LUT) might be 1–2 weeks. | What's the rough order-of-magnitude engineering budget for the next phase? |
| UI surface | CLI tool only (current shape) constrains Pattern 1 / 2 / 3; a GUI component or daemon process opens them. | Is lrt-cinema allowed to introduce a GUI component or daemon, or must it stay CLI? **User answer (2026-05-26):** Full GUI authoring is on the table — D (LRT replacement) stays in the candidate set. |
| Engineering budget | Pattern 7 (LRT replacement) is multi-month; Pattern 1 (display LUT) might be 1–2 weeks. | What's the rough order-of-magnitude engineering budget for the next phase? **User answer (2026-05-26):** Open-ended; depends on what the analysis recommends. Decide on candidate first, then commit budget appropriately. |
| Real-time-during-LRT-authoring requirement | Parallel viewer (F) and JIT render (I) only useful if they update fast enough to track the author's editing pace. | Is a sub-second-feedback parallel preview a requirement, or is a few-seconds-per-update viewer acceptable? |
| Deliverable format flexibility | Currently emits 16-bit linear Rec.2020 TIFF / ACES OpenEXR / AgX Rec.2020 TIFF. Some candidates require alternative formats (RAW-passthrough, OCIO sidecars, etc.). | Is the deliverable format fixed, or could it diversify? |

## What the search produces, downstream of user input

Once the constraints land, the next research pass:

1. Prunes candidates that violate hard-no constraints.
2. For the surviving candidates, expands each into a feasibility
   study: implementation surface, dependency map, engineering
   cost estimate, validation plan.
3. Re-ranks by fit-to-workflow under the user's stated constraints.
4. Surfaces the top 2–3 as feasibility-pilot proposals.

This pass DOES NOT produce a single recommendation. The user has
indicated explicit interest in surveying the option space without
the search collapsing prematurely. The output is a feasibility-
study set, not a verdict.

## What this doc deliberately does NOT contain

- A recommendation. The recommendation depends on constraints not
  yet elicited.
- A workflow-stage allocation. Whether the user grades all-in-LRT
  or all-in-Resolve or in-both is a property of the workflow this
  doc is searching to support, not an input to the search.
- A PR fate determination. PR fates depend on which candidates the
  feasibility study surfaces; that surfacing has not happened yet.

## Provenance

The cache-behavior test result in `07_decision.md` v2 is the only
empirical input this doc draws on; all other content is structural
analysis. The patterns survey draws on:

- ICC color management literature (soft-proofing, calibrated
  monitor workflow).
- ACES IDT/ODT documentation (standardized intermediate as cross-
  tool exchange).
- Audio mastering / room-correction literature (Dirac, Trinnov;
  Pattern 3 corrective transforms).
- AAA game development cross-pipeline review patterns (engine
  rendering vs cinematic vs broadcast view).
- BIM and parametric design (Pattern 6).
- DAW in-the-box mastering (Pattern 7).

These are not exhaustive. The next research pass, if commissioned,
should re-survey at the depth `01`–`04` did.
