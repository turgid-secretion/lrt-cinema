# Decision: ship current state, reframe PR #15, document the multi-stage workflow

*Fresh-context chip's decision deliverable for the color-correction bind.
Builds on `05_synthesis.md` (option-space technical math) and
`06_framing_shift.md` (user's control-loop reframing). Two new empirical
inputs land here: the LRT preview-cache behavior test, and the user's
grading-workflow answer.*

## TL;DR

1. **Empirical (cache test):** Exit 2 (substitute our JPEGs into
   `.lrt/visual/` as LRT's preview source) is **dead.** LRT live-computes
   the editor-pane preview from RAW + XMP via its bundled Adobe DNG
   Converter the moment the user touches a slider, and overwrites the
   on-disk cache as a side effect. The on-disk JPEG is the *output* of
   LRT's preview pipeline, not the *input* to the editor pane.

2. **Workflow (user answer):** LRT is the first-pass tool (exposure
   ramps, transitions, deflicker, Holy Grail). Final color decisions
   happen in Resolve against the lrt-cinema-rendered linear TIFF.

3. **Recommendation:** ship the current state. The "broken control loop"
   the framing-shift doc described is real, but it closes one stage
   downstream — at Resolve, not in LRT. Retire PR #15 (plain 3×3
   chasing-Adobe fit). Keep #11/12/13/14. Reframe the project's
   documentation around the actual two-stage workflow.

4. **Engineering output:** ~1 PR for SCOPE/README rewording + retiring
   PR #15. Optional follow-up: a colour-science root-polynomial drop-in
   for ColorChecker-relative correctness (improves the *absolute*
   colorimetric quality of the linear TIFF Resolve consumes; not
   load-bearing for the workflow narrative).

## The cache-behavior test (verbatim record)

**Setup.** Backed up
`/Volumes/SanDisk Extreme Pro 55AF Media/Projects/2026 international faire
timelapse/.lrt/visual/DSC_4053.lrtpreview` (SHA-256 `3a31bcdb…`). Wrote
a 1024×684 baseline JPEG marker (blue field, `CACHE TEST` text overlay)
to the same path (SHA `a8a61b6e…`). LRT not running.

**Test 1: does LRT respect external JPEGs in `.lrt/visual/` during
passive navigation?**

- Action: user opened LRT, navigated to keyframe DSC_4053, did NOT click
  Visual Previews / Save Metadata / any editing button.
- Observation: user saw the blue marker.
- File state post-test: SHA unchanged, mtime unchanged.
- **Conclusion:** LRT does NOT regenerate the cache on passive
  navigation. The editor pane displays the on-disk JPEG when the user
  is not actively editing.

**Test 2: does LRT regenerate the cache when the user edits or saves?**

- Action: user moved the Exposure slider +0.5 on the keyframe, then
  clicked Save Metadata.
- Observation: user reported "Preview updates live to show the
  brightened scene" — the moment the slider moved, the marker was
  replaced in-pane by a live-computed Adobe-pipeline preview of the new
  exposure. User: *"Marker is gone after hitting save — but it was
  already gone after altering exposure."*
- File state post-test: SHA changed to `783401ab…`; size 43408 → 40136;
  mtime advanced ~3 minutes. **LRT overwrote our cached JPEG with its
  own Adobe-pipeline render.**
- **Conclusion:** the moment the user touches the editor, LRT routes
  preview generation through its bundled Adobe DNG Converter pipeline
  and writes the result back to disk. Our substituted JPEG only
  affects the *pre-edit* state — the timeline thumbnails, the
  initial scrub-through, and the pixel luminance LRT's Visual
  Deflicker analyzes.

**Implications:**

| Use the cache JPEG controls? | Yes / No |
|---|---|
| Initial timeline scrub-through (pre-edit) | Yes |
| "Pink curve" luminance visualization | Yes (until next regen) |
| Visual Deflicker input | Yes (until next regen) |
| The live preview the user grades against | **No** |
| The post-edit preview after Save Metadata | No (LRT just overwrote it) |

**The grading control loop is in the live preview, not in the cache
JPEG.** And the live preview is hard-wired to Adobe via LRT's bundled
DNG Converter. There is no documented or empirical hook to redirect
it without forking LRT.

`OBSERVED 2026-05-26.` Reproducer: replace any `.lrt/visual/*.lrtpreview`
with a marker JPEG, open LRT, touch any develop slider, watch the file
on disk get rewritten with LRT's render.

## The workflow discriminator (user answer)

> *"LRT first-pass; Resolve does final color."*

This answer changes the whole framing. The "broken control loop"
framing assumes the user grades final color **in LRT**, against the LRT
preview. If LRT is the grade tool, the loop must close — Exit 1
(chase Adobe) is then the only real path, since Exit 2 is dead.

But the user grades final color **in Resolve**, against the
lrt-cinema-rendered linear TIFF. The loop **closes one stage
downstream**: the user looks at the dt-rendered TIFF in Resolve and
makes color decisions there, against what they will deliver. LRT's
role is timelapse-mechanical (transitions, deflicker, exposure ramps,
Holy Grail) — domains where Adobe→dt translation is well-defined and
the residual ΔE is small (~6 ΔE pre-affine, ~2 ΔE post-affine on the
DSC_4053 baseline per [V04_PLAN.md](../../V04_PLAN.md), broadcast-acceptable
after a single Resolve grade adjustment).

The "instruments giving unpredictable feedback" failure mode the user
named in `06_framing_shift.md` would be acute if the user were grading
HSL panel / per-color saturation / per-hue luminance in LRT and
expecting the dt-render to track. In the actual workflow, those
controls aren't where decisions happen — they happen in Resolve, against
the dt output. The first-pass controls the user DOES use in LRT
(Exposure, WB, basic curve, transitions) translate cleanly across both
pipelines.

## Renaming the option taxonomy

The framing-shift doc's three exits anchor too tightly to the
control-loop metaphor. Replacing with concrete labels that name the
decision rather than the philosophy:

| Old label | Concrete label | Status |
|---|---|---|
| Exit 1 (match Adobe) | **A. Continue Adobe-match calibration tower** | Possible; high cost |
| Exit 2 (preview-source) | **B. Substitute LRT's preview cache** | **DEAD per cache test** |
| Exit 3 (full-dt path, contradictory) | split into:<br>**C₃ₐ. dt-native render, grade in LRT preview, broken loop**<br>**C₃ᵣ. dt-native render, grade in Resolve downstream**<br>**C₃ᵦ. dt-native render, leave LRT for grading entirely** | Depends on workflow |
| — | **D. Hybrid: restricted-slider claim** | Optional positioning play |

The framing doc's Exit 3 conflated C₃ₐ (broken loop) and C₃ᵦ
(loop-closes-by-leaving-LRT) into one option. They are different
products. C₃ᵣ — the "Resolve downstream" path — was not in the framing
doc at all but is what the user actually does.

## Per-option cost / risk matrix

Engineer-weeks estimates are rough, single-engineer, calendar time
including code review and validation. ΔE numbers are mean ΔE2000 on
the DSC_4053 neutral keyframe vs LRT preview unless noted.

| Option | Eng cost | Achievable ΔE | Workflow disruption | Ongoing maintenance | License / deps | Fit to user workflow |
|---|---|---|---|---|---|---|
| **A. Adobe-match tower** | 6–8 wks (root-poly + SSF + HSV-untwist) | ~2 (D750 with SSF); ~4–6 (no SSF) | None (transparent improvement) | Per-Adobe-DNG-Converter-release; bundled-DCP-version churn | colour-science (BSD-3); SSF data CC BY-NC-SA (user computes locally, repo cannot ship); dcpTool GPL-3 (can shell out only) | **Solves a problem the user no longer cares about.** The Adobe match matters only if LRT preview = final color reference. |
| **B. Preview substitution** | — | — | — | — | — | **DEAD.** Empirical: LRT live-computes from RAW during sliders. |
| **C₃ₐ. dt-native, grade in LRT** | 0 wks (current state) | ~6 pre-affine / ~2 post-affine | None | Minimal | None | Loop is broken if final color happens in LRT. User has said it does not. |
| **C₃ᵣ. dt-native, grade in Resolve** | 0 wks (current state); ~1 wk for doc rewrite | ~6 pre-affine / ~2 post-affine for first-pass (broadcast-acceptable); arbitrary for final (in Resolve) | None | Minimal | None | **Matches the user's actual workflow.** Loop closes at Resolve, not in LRT. |
| **C₃ᵦ. dt-native, leave LRT entirely** | 4–6 wks (replace LRT's keyframe + transitions + deflicker + Holy Grail with our own) | Same as C₃ᵣ | **Large.** User loses LRT's interpolation/deflicker/HG infrastructure. | Whatever LRT improves we'd have to backport. | None | Workflow disruption is huge for marginal benefit (Resolve grade closes the loop anyway). Not justified. |
| **D. Restricted-slider claim** | 1 wk (doc + warning enhancements) | Same as C₃ᵣ | None | Minimal | None | Strict subset of C₃ᵣ; useful framing if HSL drift complaints surface. |

## Recommendation

**Ship C₃ᵣ. Retire PR #15. Document the workflow.**

Reasoning:

1. **The cache test forecloses B.** Not opinion; empirical. LRT live-
   computes through Adobe during slider edits and overwrites our cache.

2. **The workflow answer forecloses the need for A.** Closing the
   Adobe gap matters when the user is grading against the Adobe preview
   for final color. They are not. The first-pass operations they DO
   perform in LRT (exposure ramps, WB, transitions, deflicker, Holy
   Grail) translate cleanly across Adobe→dt — exposure shifts and
   chromatic adaptation are single-DOF transforms with no per-scene
   non-stationary content. The 6 ΔE pre-affine / 2 ΔE post-affine
   baseline on a neutral keyframe is broadcast-acceptable for the
   first-pass content.

3. **C₃ₐ vs C₃ᵣ resolves to C₃ᵣ on user input.** Same code, different
   documentation. The user has surfaced the workflow they actually use.

4. **C₃ᵦ is over-engineering.** Replacing LRT's keyframe / transition /
   deflicker / Holy Grail infrastructure to "leave LRT" doesn't close
   any loop the Resolve stage doesn't already close. Multi-engineer-
   weeks for no narrative benefit.

5. **D is positioning, not engineering.** If HSL-drift complaints
   surface later, escalate to the explicit restricted-slider claim. Not
   needed pre-emptively.

The recommendation is therefore: **the calibration tower is solving the
wrong problem.** PR #15's documented numbers (pre-fit mean 16.50 ΔE,
post-fit mean 12.66 ΔE, max 23.08 — measured on synthetic-ColorChecker
patches synthesized via the bundled DCP's ColorMatrix, then fit via the
two-engine round-trip) are the gap between *our algorithmic engine* and
*our DCP engine* on broadly-sampled chromaticity. That measurement
would matter if the project's job were to make the algorithmic engine
match the DCP engine on arbitrary scenes. It is not. Our render's job
is to produce a colorimetrically correct linear TIFF for Resolve to
grade. The ColorChecker-against-published-patches reference is what
matters for that claim, and is what the existing CI-gate ΔE2000 test
methodology in [docs/VALIDATION.md](../../VALIDATION.md) is designed to
measure.

## PR chain reframing

| PR | Branch | Recommendation | Reasoning |
|---|---|---|---|
| [#11](https://github.com/turgid-secretion/lrt-cinema/pull/11) | `fix/v0.4-defensive` | **Merge regardless.** | Independent audit fixes. Fate-decoupled from color work. |
| [#12](https://github.com/turgid-secretion/lrt-cinema/pull/12) | `fix/xy-camera-neutral-iteration` | **Merge regardless.** | Audit cleanup for the existing DCP-iteration code. Fate-decoupled. |
| [#13](https://github.com/turgid-secretion/lrt-cinema/pull/13) | `refactor/cli-resolve-profile` | **Merge regardless.** | CLI extraction refactor (audit #23). Fate-decoupled. |
| [#14](https://github.com/turgid-secretion/lrt-cinema/pull/14) | `feat/v0.4-calibration-deterministic` | **Merge.** | Infrastructure (calibration.py save/load/lookup + synthetic DNG generator). Fate-independent of the Adobe-match narrative — the storage / lookup primitives are general-purpose and would be required by any future per-camera calibration work regardless of the chosen color path. 628 lines of new code is non-trivial maintenance, but the option value is real and there is no downside to landing it. |
| [#15](https://github.com/turgid-secretion/lrt-cinema/pull/15) | `feat/v0.4-calibration-dt-roundtrip` | **Retire.** Close without merging. The plain 3×3 fit chases the Adobe-match objective that the workflow answer says is the wrong target. Specifically, the 12 ΔE residual it documents is on synthetic broad-chromaticity coverage — interesting as a research data point but not load-bearing for the deliverable. | If kept, it would justify itself only as a "baseline number for the V04 acceptance gate" — but the V04 gate (`mean post-fit < 2.0`) is the LRT-preview-relative number that we no longer optimize for. Better to delete it and clarify the metric we DO optimize: ColorChecker ΔE2000 of the linear TIFF against published patches. |
| [#16](https://github.com/turgid-secretion/lrt-cinema/pull/16) | `docs/color-option-space-research` | **Merge after this doc lands.** Add this doc (`07_decision.md`) to the branch. The research + decision is self-contained and worth being in the tree. | The research informed the decision; the decision should sit next to it. |

**Merge order recommendation:**
1. #11, #12, #13 in parallel (or sequentially per CI capacity)
2. #14 after #11–#13 land (so calibration code lands against the
   defensive guards)
3. #16 (this doc + the research) any time
4. #15: close with a comment explaining the retirement

## Documentation re-anchoring (implementation phase)

After the recommendation is approved, [README.md](../../../README.md),
[SCOPE.md](../../../SCOPE.md), and [V04_PLAN.md](../../V04_PLAN.md) need
to be re-anchored around the two-stage workflow: lrt-cinema as
first-pass renderer producing colorimetrically-correct linear TIFFs;
LRT for timelapse mechanics; Resolve (or any color finishing tool) for
final grade. The colorimetric claim moves from "matches LRT preview"
to "ColorChecker ΔE2000 envelope vs published patches" — the figure
dcamprof and Argyll have used for two decades. This is implementation
work that follows sign-off, not part of this decision.

## Open follow-up work (optional, post-decision)

These are NOT load-bearing on the recommendation. Surface for the
user's consideration as separate work after the decision lands:

1. **Root-polynomial drop-in for the colorimetric matrix.** Replace the
   existing 3×3 channelmixer fit (in `colorin` / wherever the matrix
   correction sits today) with
   `colour.characterisation.optimisation_factory_Oklab_15`. Halves
   ColorChecker ΔE on chart-only fits without changing the workflow
   narrative. Zero new dependencies (colour-science is already in dev
   deps). ~1 engineer-week. Improves the *absolute* colorimetric
   quality of the linear TIFF; orthogonal to the LRT-vs-dt
   conversation.

2. **Document the post-affine number as the workflow-relevant figure.**
   Current V04 acceptance gate names mean post-fit < 2.0 vs LRT
   preview, which optimizes for a target the workflow doesn't use.
   Replace with: mean ColorChecker ΔE2000 < N against published patches
   under the renderer's chosen illuminant (the figure dcamprof and
   Argyll have used for decades). The threshold should be 2.0 (cinema
   reference) or 3.0 (broadcast) depending on what we can hit; current
   baselines aren't documented in the form needed to pick a number
   confidently — propose running the existing ColorChecker pipeline on
   the v0.4 codebase once and using the result as the published gate.

3. **Diagnostic tool reframing.** `tools/diagnose_vs_lrt_preview.py`
   currently positions LRT preview as the reference. Keep the tool
   (it's useful for "is my render visibly different from what the user
   sees in LRT?" — a UX question) but reposition it: it diagnoses
   workflow-stage handoff quality, not colorimetric correctness.

## Known unknowns (surfaced for the record)

These would matter if the workflow were different; documented so a
future chip can re-open them:

1. **HSL-heavy grading on a Holy Grail sequence.** No data on disk; the
   user's sample is constant-exposure with default HSL. Structural
   argument suggests HSL-heavy grading would surface materially worse
   Adobe→dt mismatch than first-pass operations, but the magnitude is
   not measured. If the project later targets a user who grades HSL in
   LRT, this becomes a measurable gap that needs the calibration-tower
   path to close.

2. **LRT cache invalidation triggers beyond editor-pane sliders.**
   Tested two: passive navigation (does not regenerate) and slider edit
   (does regenerate, immediately). Not tested: Auto Transition,
   Visual Previews → All Frames, Holy Grail Wizard. If LRT 7.6+ ever
   exposes a "skip regeneration if XMP unchanged" optimization, Exit B
   may reopen.

3. **`colour-science`'s root-poly `optimisation_factory_Oklab_15`
   numerical conditioning** on Nikon D750 RAW values has not been
   verified against published reference profiles. Standard library code
   so expected to be solid, but worth verifying when the optional
   follow-up #1 lands.

## Adversarial-pass observations (from the framing-shift doc)

The framing-shift doc raised two adversarial threads. Both deserve a
brief noted response now that the workflow discriminator is in.

**Thread 1: "Cinema already grades in one space and renders in others
(ACES)."** The framing-shift doc's adversarial #1 argued the relevant
distinction is *transform-stability,* not *spaces-differ.* Confirmed.
Under the C₃ᵣ workflow the user has confirmed, the LRT→dt transform on
the operations the user actually performs in LRT (exposure / WB / curve
/ transitions) IS well-defined and reasonably stable (Robertson kelvin
↔ xy chromatic adaptation, linear exposure shift, scalar tone-curve
remapping). The non-stationary content (DCP per-cell hue-twist
LookTable contributions) only bites on operations the workflow does
NOT use in LRT. Adversarial #1 was correct as a principle; the
workflow-stage discriminator localizes where it actually matters.

**Thread 2: "Magnitude depends on grading style."** Confirmed
structurally. Timelapse-typical operations preserve the same residual
as the neutral keyframe (~6 ΔE pre-affine / ~2 ΔE post-affine on
DSC_4053 per the V04 gate). HSL-heavy operations are where Adobe→dt
becomes structurally non-bijective, and where the "broken instruments"
metaphor would bite hardest. The user's actual workflow avoids the
HSL panel in LRT, so the practical magnitude is dominated by the
clean-translation regime.

## Concrete action list (on user sign-off)

If the recommendation is approved as-stated:

1. **Merge #11, #12, #13** in CI order (independent audit fixes; no
   dependencies between them).
2. **Merge #14** after #11–#13 land (calibration infrastructure;
   stacks cleanly on the audit fixes).
3. **Close #15** with a comment pointing at this decision document
   ([07_decision.md](07_decision.md) §"PR chain reframing").
4. **Add this document to PR #16** (`docs/color-option-space-research`)
   and **merge #16** so the research and the decision live in the tree
   together.
5. **Open a follow-up PR** to re-anchor README / SCOPE / V04_PLAN
   around the two-stage workflow (per "Documentation re-anchoring"
   above). Sequenced after #14 / #16.

If the recommendation needs partial buy-in or course-correction (e.g.
"keep PR #15 as a Tier 2 baseline" or "land the root-poly upgrade
inline with this change"), specifics on a per-PR or per-section basis
go through one more round of discussion before any commits land.
