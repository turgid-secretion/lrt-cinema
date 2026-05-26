# Color-correction option space: decision input

*Fresh-context chip's deliverable for the bind described in
`06_framing_shift.md`. Two new inputs land here: an empirical
cache-behavior test on the user's working LRT 7.5.3 sequence, and a
revised analysis of where the control loop closes (and does not) in
the multi-stage pipeline. Recommendations are conditional on
workflow target, not collapsed on a single user's stated practice.*

## What the system does, in plain terms

The pipeline has three stages and four artifacts:

```
camera RAW (.NEF/.CR3/...) 
   ├─→ LRTimelapse  ─→  per-frame XMP sidecars (LR-shape CRS schema)
   │                    .lrt/visual/*.lrtpreview JPEGs (LRT's preview cache)
   │
   ├─→ lrt-cinema  ─→   16-bit linear Rec.2020 TIFF (or ACES OpenEXR, or AgX-tonemapped TIFF)
   │
   └─→ colorist (Resolve / Baselight / Nuke / ...)
                ─→   deliverable encode (ProRes / DPX / H.265 / ...)
```

Three reference points exist, each backed by a different rendering
pipeline:

| Reference point | Pipeline that renders it | Stage where the user sees it |
|---|---|---|
| **LRT preview** (`.lrt/visual/*.lrtpreview`) | Adobe DNG Converter (bundled in LRT 7.x) + LRT-internal JPEG-encode | LRT editor pane, during keyframe authoring |
| **lrt-cinema linear TIFF** | darktable (input matrix + CAT16 + the modules emitted from the LRT XMP) | Resolve's clip view, after lrt-cinema render completes |
| **Resolve display** | Resolve's pipeline (configurable: ACES ODT, DaVinci YRGB, Rec.709 working, etc.) applied to the linear TIFF | Resolve's grading viewport |

The "control loop" the framing-shift doc names is this: a grader makes
decisions while looking at one of these reference points; the decisions
are encoded into XMP (LRT-stage) or into Resolve's project (Resolve-
stage); the encoded decisions, when rendered through whichever pipeline
produces the deliverable, must produce the perceptual result the grader
saw at decision time. The loop closes when the grader's visual
reference IS the deliverable view, modulo a known stable transform.

## Where lrt-cinema's pipeline closes the loop, and where it does not

For a grader working *only inside Resolve* (LRT XMP carries identity-or-
near-identity intent; all color decisions live in Resolve's project):
the loop closes. Their grading viewport, their deliverable encode, and
their adjustment history all share Resolve's pipeline and ODT.

For a grader working *only inside LRT* (deliverable is an export of the
LRT-rendered sequence, no Resolve stage): the loop closes if the LRT
preview's pipeline matches the deliverable's pipeline. Today, LRT's
internal-export path produces 8-bit sRGB JPGs from its Adobe-pipeline
preview, so this loop is closed (LRT preview ≈ LRT export). lrt-cinema
does NOT participate in that loop; it is bypassed.

For a grader working in *both* (the cross-stage workflow the user's
framing-shift doc describes — and the workflow where lrt-cinema lives):
the loop is open between stages. The LRT-stage grading reference
(Adobe-pipeline preview) is a different display transform than the
Resolve-stage grading reference (Resolve's ODT applied to the dt-
rendered TIFF). Numeric scene-referred operations (linear exposure
scaling, well-defined chromatic adaptation) preserve cleanly across the
gap; perceptually-targeted operations (saturation, curve shape, per-
hue HSL) do not, because their *perceptual* effect depends on the
display transform under which they were authored.

The framing-shift doc's "broken instruments" metaphor describes this
gap. The metaphor is correct when applied to perceptually-targeted
LRT-stage operations. It overstates the case for mathematically-clean
LRT-stage operations (exposure ramps, WB shifts, transition smoothing
— the timelapse-mechanical use of LRT).

### What ACES does, more carefully

ACES grades in a wide working space (ACEScg) for headroom — analogous
to audio's 32-bit float for headroom. The wide space is not the
grader's perceptual reference. The grader's reference is the ODT view
(Rec.709 / P3 / Rec.2020 ODT — one transform pinned to the
deliverable's display). The colorist makes decisions while looking
through that ODT, and the deliverable carries the same ODT. The loop
closes because the grading reference IS the deliverable view.

The earlier draft of this document used "ACES grades in one space and
delivers in others" to argue that grade-vs-render space mismatch is
normal. That framing was misleading: ACES decouples *headroom* from
*reference*, but the reference is always pinned to the deliverable.
The grade-vs-render mismatch in the LRT → lrt-cinema → Resolve chain
is a mismatch of *references*, which is structurally different.

### What is "baked in" to the lrt-cinema linear TIFF

The TIFF is scene-referred (post-camera-response, post-CAT, before any
display gamma). This is more flexible than a display-encoded TIFF for
downstream operations: linear scaling, chromatic adaptation, and HDR
headroom all work cleanly in scene-referred space.

The TIFF is *not* RAW. The following are baked in irreversibly at the
lrt-cinema stage:

- LR-keyframe-authored intent that lrt-cinema's emitter translates
  (Exposure2012, Temperature2012 + Tint, Blacks2012, ToneCurvePV2012,
  Sharpness, Saturation / Vibrance / Contrast2012 best-effort)
- darktable's input matrix and CAT16 chromatic adaptation
- DCP-derived corrections when `--engine dcp` is in effect (BaselineExposure,
  ProfileToneCurve, HSM/LookTable cube)

Downstream tools can apply additive operations on top of the baked-in
state; they cannot reverse the baked-in operations to recover the
state of an earlier stage. In particular, an LR-stage `+5 saturation`
that bakes into the TIFF via dt's `colorbalancergb` is not recoverable
to "no saturation applied" in Resolve. Resolve can apply a counter-
saturation; that's a different operation with different mathematical
properties.

A `--engine raw-passthrough` mode (or equivalent) that suppresses LR-
intent emission and leaves all grading to Resolve is technically
feasible but does not exist today. Whether it should exist is a
product-positioning question, not a fit-and-finish one.

## Empirical input: the LRT preview-cache behavior test

Test conducted 2026-05-26 on the user's working sequence
(`/Volumes/SanDisk Extreme Pro 55AF Media/Projects/2026 international
faire timelapse/`).

**Setup.** Backed up `.lrt/visual/DSC_4053.lrtpreview` (SHA-256
`3a31bcdb…`). Wrote a 1024×684 baseline JPEG marker (blue field,
"CACHE TEST" text overlay) to the same path (SHA `a8a61b6e…`). LRT was
not running.

**Test 1 — passive navigation.** User opened LRT, navigated to keyframe
DSC_4053, clicked nothing else. Observation: editor pane displayed the
marker. File state post-test: SHA unchanged, mtime unchanged.

**Test 2 — interactive editing.** User moved the Exposure slider +0.5
on the keyframe, then clicked Save Metadata. Observation: the editor
pane updated live to show a brightened scene the moment the slider
moved, replacing the marker in-pane. File state post-test: SHA changed
to `783401ab…`, size 43408 → 40136, mtime advanced ~3 minutes. **LRT
overwrote the marker JPEG with its own Adobe-pipeline render.**

**Conclusion.** LRT live-computes the editor-pane preview from RAW +
XMP via its bundled Adobe DNG Converter the moment the user begins an
edit operation, and writes the result to the on-disk cache as a side
effect. The cache JPEG is the *output* of LRT's preview pipeline, not
the *input* to the editor pane.

**What this forecloses, what it does not.** Externally-placed JPEGs in
`.lrt/visual/` cannot serve as the grading reference during interactive
editing — that reference is hard-wired to Adobe via LRT's bundled DNG
Converter, and there is no documented hook to redirect it without
forking LRT. Externally-placed JPEGs DO control pre-edit timeline
thumbnails, the pixel-luminance basis of Visual Deflicker's analysis,
and the "pink curve" visualization until the next interactive edit
triggers regeneration. These are observable side-channel uses; they do
not close the live-grading control loop.

(`OBSERVED 2026-05-26`. Reproducer: replace any
`.lrt/visual/*.lrtpreview` with a marker JPEG of identical dimensions,
open LRT, touch any develop slider in the editor, watch the file
on disk get rewritten with LRT's Adobe-pipeline render.)

## Option space

The framing-shift doc proposed three exits. With the cache-behavior
test result and the multi-stage workflow analysis above, the
taxonomy is:

| Option | What it does | Closes the cross-stage loop? | Workflow-agnostic viability |
|---|---|---|---|
| **A. Adobe-match calibration tower** | Make lrt-cinema's render approach LR's render (root-poly + SSF-IDT + HSV residual catcher per `05_synthesis.md`) | Closes the LRT-stage → lrt-cinema stage. The Resolve stage's loop closure is then dependent on Resolve's pipeline matching LR's (commonly Yes via Resolve's "ACES IDT for ACR" workflow or similar) | Viable; engineering-heavy |
| **B. Preview substitution** | Place dt-rendered JPGs into `.lrt/visual/` so LRT uses them | — | **Foreclosed by cache test.** LRT regenerates on any edit. |
| **C. dt-native render, LRT for first-pass only** | Current state. lrt-cinema renders LR-keyframe intent into dt's color science. Documented mismatch with LRT preview; user accepts | Open between stages. Magnitude scales with how much LRT-stage authoring is perceptually-targeted (saturation / HSL / curve) vs mathematically-clean (exposure / WB / transitions) | Viable today |
| **D. dt-native render, LRT replaced for grading entirely** | Build keyframe authoring + interpolation + deflicker + Holy Grail into a new lrt-cinema UI; user no longer touches LRT for color decisions | Closes (single-stage grading inside a dt-native UI) | Multi-engineer-month; out of current scope |
| **E. Render mode that leaves LR intent unbaked** | New `--engine raw-passthrough` (or equivalent) that emits the LR XMP intent as an OCIO / CLF / sidecar artifact for Resolve to apply, instead of baking it into the TIFF | Closes by collapsing both stages into Resolve | Multi-engineer-week if XMP→OCIO mapping is well-defined; possibly large if not |

## Per-option cost and risk matrix

Engineer-weeks estimates are rough, single-engineer, calendar time
including code review and validation. License/dep notes are non-
exhaustive; see `05_synthesis.md` for the full provenance.

| Option | Eng cost | Achievable ΔE2000 vs LR | Maintenance | Workflow disruption | License / deps |
|---|---|---|---|---|---|
| A. Adobe-match | 6–8 wks for the full stack (root-poly + SSF-IDT + HSV-untwist) | ~2 mean (D750 with SSF); ~4–6 mean (no SSF, root-poly only) | Per-Adobe-DNG-Converter release | Transparent to user | colour-science (BSD-3); butcherg SSF data CC BY-NC-SA (user computes locally; not redistributable); dcpTool GPL-3 (shell out only) |
| B. Preview substitution | — | — | — | — | — |
| C. dt-native first-pass | 0 wks (today) to ~1 wk for doc reframe | DSC_4053 baseline: 6.05 pre-affine / 2.24 post-affine on a neutral keyframe per V04_PLAN.md. Substantially worse on perceptually-targeted LRT-stage authoring. | Minimal | None for users already in this workflow; explicit "what to expect" doc for others | None |
| D. LRT-replacement UI | 8–16+ wks | Same as C (no color-change; UX change only) | Whatever LRT improves | Large: user retrains on a new UI; loses LRT's mature interpolation/deflicker/HG | Probably GUI framework dep (Qt / Tauri / similar) |
| E. Raw-passthrough mode | 3–6 wks to design + implement XMP→OCIO sidecar emission | N/A — operations are not pre-baked; final ΔE is whatever Resolve produces | Resolve's OCIO behavior, CRS schema changes | Adds a render mode; Resolve project setup required | OCIO (BSD-3); CRS-to-OCIO mapping research |

## Recommendations, conditional on workflow target

The fitness of each option depends on which users the project chooses
to serve. The conditions below state the decision factors plainly so a
maintainer can self-classify.

### If the project targets LRT-as-primary-grader workflows

Description: the user makes their FINAL color decisions inside LRT,
against the LRT preview. The deliverable is then rendered (by lrt-
cinema or by LRT itself) and shipped without further color work, or
with only mechanical Resolve transcoding.

Loop closure requires: the lrt-cinema render's color science must
match LR's color science within deliverable-acceptable tolerance.

Recommended path: **A (Adobe-match tower).** Cost is real (6–8
weeks for the full stack) and ongoing (Adobe DNG Converter version
churn). Achievable ΔE caps at ~2 mean on cameras with SSF data
available; ~4–6 mean on cameras without. The DCP/HSV
LookTable's per-cell hue twists are the structural ceiling for any
3×3-only solution; closing them requires the HSV residual catcher
stage from `05_synthesis.md`. PR #15's plain 3×3 fit (post-fit 12.66
ΔE on synthetic broad-chromaticity coverage) is the linear-baseline
foundation for this stack — keep it merged.

### If the project targets LRT-for-first-pass + Resolve-final workflows

Description: the user makes LRT-stage decisions for timelapse mechanics
(exposure ramps, transitions, deflicker, Holy Grail). Final color
decisions happen in Resolve against the dt-rendered TIFF.

Loop closure between LRT-stage and Resolve-stage cannot be made
mathematically clean by engineering — the two stages reference
different display transforms (LRT-preview ODT-equivalent vs
Resolve-display ODT). What CAN be made clean is the LRT-stage
operations that translate cross-pipeline (linear exposure, chromatic
adaptation, identity-or-near-identity tone curve, transitions); these
preserve numerically across the gap regardless of display transform.

Recommended path: **C (current state)**, with explicit guidance that
LRT-stage authoring should be restricted to clean-translating
operations and final color work should happen in Resolve. The
current acceptance gate (DSC_4053 post-affine 2.24 ΔE) is broadcast-
acceptable for clean-translating operations; the residual is
recoverable in Resolve by a single per-channel grade adjustment.

PR #15's status under this path: optional. The 12.66 ΔE residual it
documents is on synthetic broad-chromaticity coverage relevant to
path A; it doesn't move the needle for path C unless extended with
root-poly + HSV-residual stages. If the project commits to path C
exclusively, retire #15. If the project supports both paths, keep #15
as the Tier 2 baseline for path A.

### If the project targets a single-grading-stage workflow

Description: the user wants all color decisions to live in one stage,
without cross-stage display-transform discontinuity.

Two routes:

1. **Single-stage in LRT.** Same as the LRT-as-primary recommendation
   above; requires path A.
2. **Single-stage in Resolve.** Requires path E (raw-passthrough mode
   so LRT-stage XMP intent doesn't bake into the TIFF). Multi-engineer-
   week to design and implement; the design question is what subset of
   LR CRS intent maps to OCIO-expressible operations.

Path D (LRT replacement) achieves single-stage in lrt-cinema's own
UI but is multi-engineer-month work and inherits a UI maintenance
burden. Not recommended unless other constraints force it.

### What is foreclosed regardless of workflow

- Path B (preview substitution) is dead per the cache test.

## PR chain implications

Per-PR recommendations depend partly on which workflow paths the
project decides to support. The independent baseline is:

| PR | Branch | Recommendation regardless of path | Conditional notes |
|---|---|---|---|
| [#11](https://github.com/turgid-secretion/lrt-cinema/pull/11) | `fix/v0.4-defensive` | **Merge.** | Independent defensive guards. Fate-decoupled from color narrative. |
| [#12](https://github.com/turgid-secretion/lrt-cinema/pull/12) | `fix/xy-camera-neutral-iteration` | **Merge.** | Independent audit cleanup. Fate-decoupled. |
| [#13](https://github.com/turgid-secretion/lrt-cinema/pull/13) | `refactor/cli-resolve-profile` | **Merge.** | Independent refactor. Fate-decoupled. |
| [#14](https://github.com/turgid-secretion/lrt-cinema/pull/14) | `feat/v0.4-calibration-deterministic` | **Merge.** | Calibration storage infrastructure (628 lines). General-purpose; required by any per-camera calibration work (path A, future SSF work, etc.). |
| [#15](https://github.com/turgid-secretion/lrt-cinema/pull/15) | `feat/v0.4-calibration-dt-roundtrip` | **Depends on workflow targets.** | If path A is in scope (either alone or alongside C/E): keep #15 as the Tier 2 linear baseline; document its 12.66 ΔE residual as the linear-only-fit ceiling for the full stack to build on. If only path C is in scope: retire #15; the 3×3 fit is not load-bearing for path-C users. |
| [#16](https://github.com/turgid-secretion/lrt-cinema/pull/16) | `docs/color-option-space-research` | **Merge after this doc lands** (this commit is on the branch). | The research + decision input is self-contained and worth being in the tree under any path. |

## Open follow-up work (not load-bearing on this decision)

1. **Root-polynomial drop-in for the colorimetric matrix.** Replace
   the existing 3×3 channelmixer fit with colour-science's
   `optimisation_factory_Oklab_15`. Halves ColorChecker ΔE on chart-
   only fits without changing the workflow narrative. ~1 wk. Improves
   absolute colorimetric quality of the linear TIFF; orthogonal to
   the cross-stage control-loop conversation. Useful under paths A,
   C, and E.

2. **ColorChecker ΔE2000 CI gate against published patches.** The
   existing acceptance-gate methodology in
   `docs/VALIDATION.md` is designed for this measurement; the v0.4
   gate phrasing in `V04_PLAN.md` ties to LRT-preview-relative ΔE
   instead. Replace with a published-patch-relative number for the
   colorimetric-correctness claim.

3. **Documented restricted-slider claim** for path C. The slider
   subset that translates cleanly cross-pipeline (Exposure, Temperature
   + Tint, identity-or-near-identity ToneCurvePV2012, transition-only
   per-frame deltas) versus the subset that does not (Saturation,
   Vibrance, Highlights2012, Shadows2012, Whites2012, HSL panel,
   per-color curves) is already implicit in the v0.4 emit table in
   SCOPE.md. Surface explicitly as a "what to author in LRT" guidance.

4. **Path E feasibility study.** Map LR CRS schema → OCIO Color
   Transform Language operations. The cleanly-mappable subset
   (exposure as gain, WB as chromatic adaptation, tone curve as 1D
   LUT) is small but well-defined; the per-hue HSL ops have no clean
   OCIO mapping. A scoping doc would identify whether the cleanly-
   mappable subset is large enough to be useful.

## Known unknowns (surfaced for the record)

1. **HSL-heavy grading on a Holy Grail sequence.** No data on disk;
   the user's sample is constant-exposure with default HSL. Structural
   argument (per `05_synthesis.md`) suggests HSL-heavy grading would
   surface materially worse Adobe→dt mismatch than clean-translating
   operations, but the magnitude is not measured.

2. **LRT cache invalidation triggers beyond editor-pane sliders.**
   Tested two: passive navigation (does not regenerate) and slider
   edit (does regenerate, immediately). Not tested: Auto Transition,
   Visual Previews → All Frames, Holy Grail Wizard. If LRT ever
   exposes a "skip regeneration if XMP unchanged" optimization, the
   cache-side-channel uses (Visual Deflicker basis, "pink curve"
   visualization) become more reliable. Path B remains foreclosed.

3. **`colour-science`'s root-poly `optimisation_factory_Oklab_15`
   numerical conditioning** on production Bayer sensor RAW values has
   not been verified for lrt-cinema-relevant cameras. Library code
   so expected to be solid, but worth verifying before path-A
   commitments rely on it.

## Concrete action list on sign-off

The path-independent items are committed regardless of the workflow-
target decision:

1. Merge #11, #12, #13 in CI order (independent audit fixes).
2. Merge #14 after #11–#13 land (calibration storage infrastructure).
3. Add this doc to #16 and merge #16 (this commit is already on the
   branch).

The path-dependent items depend on which workflow paths the project
commits to supporting:

- **If path A is in scope** (alongside or instead of C): keep #15
  merged; open follow-up PRs for the root-poly upgrade (follow-up
  #1) and the HSV residual catcher (per `05_synthesis.md`).
- **If only path C is in scope**: close #15 with a comment pointing
  at this doc; open follow-up PR for the documentation re-anchoring
  (project value proposition, slider restrictions, ColorChecker-
  relative metric per follow-up #2).
- **If path E is in scope**: open a feasibility-study PR for the
  XMP→OCIO mapping per follow-up #4; defer #15's fate until the
  feasibility study lands.

Implementation along the chosen direction follows under separate PRs.
