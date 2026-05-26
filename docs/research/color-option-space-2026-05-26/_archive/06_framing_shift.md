# Framing shift: the problem is control-loop mismatch, not output fidelity

*The reframing that emerged from the user's response to the synthesis
(2026-05-26). Captured here verbatim as the mission for the spawned
chip, plus my adversarial analysis of the framing.*

## The user's reframing

> "We're not trying to 'match Lightroom'. We're not trying to give 'users
> who want LR match' a LR look. The problem is that grading decisions in
> LRT are done based on what the user sees in LRT previews. What the
> user sees in LRT previews is based on Adobe color science, for better
> or for worse. At this point, given the rising engineering complexity,
> a fully-dt color path (ignoring Adobe color) seems preferable, if
> only for the lower friction. However, trying to grade dt color
> through an Adobe color lens is like trying to fly a plane without any
> instruments — or worse, with instruments that give random,
> unpredictable feedback to control inputs.
>
> It's possible that this framing doesn't change any of the underlying
> technical realities, however it may change how we frame the search
> for research; it probably *should* change how we frame the potential
> solution sets (for example, if we can get LRT to simply stop
> generating previews via Adobe color pipeline, that would be a
> potential exit from the current bind as well); and it could change
> how we frame the solutions themselves."

## What the reframing changes

Before: "close the ΔE2000 gap between our render and the Adobe-DCP-equivalent reference."

After: "ensure the **control loop** between the grader's perceptual
input (what they see in LRT preview) and the rendered output (what we
deliver) is stable, predictable, and closed. The grader makes
decisions; those decisions should produce the intended effect in the
deliverable. Whether the absolute color matches LR is the WRONG
question."

Three potential exits from the bind:

1. **Match Adobe (current path)**: render output via LR-equivalent
   color science. Closes the loop because input-side (LRT preview) and
   output-side (our render) share color science. **Engineering cost
   keeps climbing.** Even academic-best closes only to ~2 ΔE without
   spectral data, and that requires the full root-poly + SSF + HSV
   stack outlined in the synthesis.

2. **Move the input side**: get LRT to generate previews via dt's color
   pipeline (or via ours) instead of Adobe's. Probably not directly
   possible (LRT is closed-source), but a **related exit exists**: we
   become LRT's preview-generator. LRT's "Visual Workflow" uses JPEGs
   in `.lrt/visual/`; if we render those JPEGs first (with dt color
   science), the user grades against OUR pipeline. Loop closed via the
   input side.

3. **Move the output side**: full dt-color path. Drop the Adobe color
   science machinery. User learns to grade in dt's perceptual model
   (the LR sliders still translate via dt's `lightroom.c`-style import
   path for shape, but the dt pipeline renders them in dt's color
   science). Trades workflow disruption (LR mental model → dt mental
   model) for engineering simplicity. **What darktable's project lead
   would tell you to do.**

## My adversarial pass on the reframing

### Where it tightens to bedrock

1. **The control mismatch IS the fundamental problem.** A `+5
   saturation` slider in LRT means different things in Adobe vs dt
   color science. Magnitude of the mismatch is scene-dependent — that's
   the "unpredictable feedback" failure mode the user named. This
   survives adversarial analysis.

2. **The full-dt path is structurally cleaner** than any DCP-distillation
   tower. We've been bolting Adobe primitives onto a system that
   explicitly rejected them (dt issue #4165, "DCP support — not
   planned"). The internally-consistent system is dt-native.

### Where it could tighten further

1. **Cinema already grades in one space and renders in others.** ACES
   pipeline: grade in ACEScg, preview through Rec.709 ODT, deliver via
   P3 / Rec.2020 ODTs. Different render targets are canonical, not
   broken. So "grade-vs-render mismatch" isn't universally bad — it
   works when the mismatch is a **known, well-defined, bijective
   transform between two color spaces**. The lrt-cinema problem isn't
   that the spaces differ; **the transform is non-bijective and
   non-stationary across scenes** (the DCP's LookTable applies
   different corrections for different hues, sats, vals). **The framing
   should be "the transform is unstable," not "they're different."**

2. **Magnitude of the practical problem depends on grading style.**
   Timelapse grading is mostly exposure/WB/transitions — these
   translate cleanly across color science (a 1-stop EV change is
   1-stop EV in either pipeline; WB temperature shifts are well-defined
   chromatic adaptations). Fine HSL or per-color-channel adjustments
   are where the mismatch bites hardest. For Holy Grail workflows (the
   primary use case per the codebase), the practical magnitude may be
   smaller than the principle suggests. **The "broken instruments"
   metaphor is correct in principle but might overstate the in-practice
   pain for the actual grading style used.**

3. **The third exit ("WE become LRT's preview source") deserves
   explicit investigation.** LRT's visual workflow uses preview JPEGs.
   If lrt-cinema can produce those JPEGs first (with dt color science)
   and feed them to LRT's `.lrt/visual/` directory, the user grades
   against OUR color pipeline. The loop closes at the input side rather
   than the output side. This is potentially the cheapest engineering
   exit, but requires reverse-engineering LRT's preview format +
   workflow.

4. **The full-dt path has a workflow cost** worth surfacing. User must
   learn dt's controls (filmic / sigmoid / agx + dt's saturation/contrast
   modules) instead of LR's. For someone fluent in LR's slider
   semantics, "everything is +30 saturation now but the result looks
   different" is a translation burden. Not an objection to the path —
   just a cost to acknowledge.

5. **"Predictability" isn't binary.** LR's own slider math is non-linear
   and scene-dependent — even within Adobe color, a "+5 saturation"
   produces different absolute changes per scene. The question is
   whether the cross-pipeline non-linearity is meaningfully WORSE than
   the within-pipeline non-linearity. Worth probing empirically rather
   than assumed.

### Net

The reframing is correct at the principle level. The technical realities
(Luther floor, root-poly upgrade math, SSF path, HSV residual catcher)
don't change. What changes is which **objective function** we're
optimizing.

- **Optimize for ΔE2000 vs Adobe-DCP**: full root-poly + SSF + HSV stack
  per the synthesis. Engineering cost: months. Best-achievable: ~2 ΔE.
- **Optimize for control-loop closure**: pick ONE of three exits (match
  Adobe, match LRT preview to ours, or full dt-native).

## The mission for the spawned chip

The next-phase chip's job:

1. **Adversarially examine the third exit** (we become LRT's preview
   source). Investigate LRT's preview format, where `.lrt/visual/`
   JPEGs come from, whether LRT will accept externally-generated
   previews, and whether a "lrt-cinema preview" subcommand is a
   tractable engineering target. The doc references in
   `docs/reference/lrtimelapse/` are a starting point.

2. **Map the workflow cost of the full-dt path** concretely. What does
   the user's existing LR-slider mental model translate to in dt-native
   modules? Where are the irreducible workflow disruptions? Is there
   an "LR import" stage in lrt-cinema today (per
   `src/lrt_cinema/xmp_emitter.py` — yes, the `lr2dt_*` helpers) that
   covers most of the gap?

3. **Decide which exit lrt-cinema should commit to.** Surface for user
   decision with concrete cost / risk / scope estimates per exit.

4. **Reframe the open PR chain** (#11–#15 inclusive) given the chosen
   exit. PRs #11/12/13/14 are likely still useful regardless (audit
   fixes + Phase 2a infrastructure). PR #15 (Phase 2b plain 3×3 fit)
   may need to be retired, reframed as a baseline, or replaced with a
   root-poly variant.

5. **Implement** along the chosen exit, with the research synthesis
   from `05_synthesis.md` as the technical reference (the math options
   don't disappear — root-poly, SSF-IDT, HSV residual catcher are the
   primitives regardless of which exit is chosen).

The chip starts with FRESH context. The full research is on disk; the
chip reads it. The chip's first deliverable is the decision document
for the user, not implementation.
