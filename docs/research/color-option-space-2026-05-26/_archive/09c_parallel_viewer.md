# Parallel viewer + JIT render cluster feasibility study (F + I)

*Pattern 2 from `08_search_framing.md`: don't fight LRT's preview pipeline;
run a separate viewer that renders the same XMP via lrt-cinema's pipeline
and displays it alongside LRT. The grader cross-references the two views
during authoring. Two sub-candidates explored here: F (parallel
exact-render viewer; slow because dt-cli is slow) and I (JIT
approximate-render viewer; fast but approximates dt's output via a
cheaper pipeline). All engineering estimates are first-pass and should
sharpen during a feasibility pilot.*

## Cluster summary

The cluster does not try to make LRT show the deliverable pipeline.
It accepts the LRT-vs-darktable divergence and provides a *second*
surface — a viewer window, a folder of JPEG previews, or a second-
monitor app — where the grader sees "what this XMP looks like
through my deliverable pipeline." The grader switches attention
between LRT (authoring controls + temporal context) and the parallel
viewer (deliverable-side appearance) during decision-making.

Three structural properties shape the design space:

1. **The XMP is the source of truth.** LRT writes per-frame sidecars
   to `<sequence>/DSC_XXXX.xmp` on Save Metadata and during Auto
   Transition; our `xmp_parser.py` already consumes them. The parallel
   viewer's input is whatever LRT just wrote.

2. **The grader does not edit "live" in LRT.** LRT's editor commits
   changes on Save Metadata (per `DISK_LAYOUT.md`), so the parallel
   viewer can be an event-driven re-render keyed off filesystem
   changes, not a continuously streaming preview. This is a much
   easier engineering target than a Resolve-style live link.

3. **The grader's attention is at one or a few keyframes at a time.**
   Auto Transition writes thousands of XMPs at once, but only the
   currently-selected keyframe needs immediate fresh feedback; the
   interpolated frames between keyframes do not need to be re-rendered
   on every edit.

These properties make F and I more tractable than a naive read
suggests — but they also surface real cognitive-ergonomics questions
that the engineering estimates alone do not capture (see "Cognitive
ergonomics of parallel-reference grading," below).

## Candidate F: Parallel exact-render viewer

The exact-render variant: when LRT writes an XMP, lrt-cinema re-renders
that frame through the production darktable-cli pipeline and shows the
result. The grader sees what the deliverable *actually* looks like
under the production pipeline, modulo a render delay.

### Implementation surface

- **File watcher.** Watch `<sequence>/*.xmp` for modifications.
  `watchdog` (Python) covers macOS fsevents + Linux inotify with one
  API. Debounce 200–500ms to collapse Auto Transition's thousands-of-
  XMP burst into a single re-render trigger. (Standard file-watcher
  practice: 50ms handles editor saves, 500ms handles batch ops.)
- **Recently-edited keyframe selector.** Lrt-cinema has no hook into
  LRT's UI state. Best heuristic: "render the XMP(s) whose mtime
  moved most recently." Viewer can show the last N rendered frames as
  a strip to amortize heuristic error. Manual override (user picks
  the keyframe in the viewer) is a needed fallback.
- **Render invocation.** Reuse `runner.render_frame()` essentially
  as-is. Override `preset.output_format` to JPEG/PNG (TIFF is overkill
  for a viewer). Pass `--height`/`--width` to dt-cli for a lower-
  resolution render (1920×1280 suffices on a 4K display; full-res
  6032×4032 is wasted on the human eye and costs render time).
- **Viewer window.** Options by ascending engineering cost:
  - **No window**: write JPEGs to `<sequence>/.lrt-cinema/`; user
    opens that folder in any image viewer. ~2 days.
  - **Browser-based**: localhost HTTP server, browser tab. ~1–2
    weeks. Lower-quality UX (no pixel-perfect color management) but
    cross-platform free.
  - **Cross-platform GUI**: Tauri (Rust+webview) or Qt (PySide6),
    image widget + frame strip + A/B toggle vs LRT preview + zoom.
    2–3 weeks.
  - **macOS-native (AppKit)**: best polish; +1 week for a Linux
    fallback. Drop from serious consideration if Linux symmetry
    matters.

### Dependencies (with license)

| Component | License | Notes |
|---|---|---|
| `watchdog` (Python) | Apache 2.0 | Covers macOS fsevents + Linux inotify |
| Pillow / `simplejpeg` for JPEG decode | PIL / BSD | Optional, for thumbnail strip rendering |
| Qt / PySide6 (if GUI route) | LGPL-3 with exceptions | LGPL-3 dynamic linking acceptable for Apache 2.0 app |
| Tauri (if Tauri route) | MIT / Apache 2.0 dual | Brings Rust toolchain dependency |
| flask / starlette (if browser route) | BSD / BSD | Trivial HTTP serving |
| `darktable-cli` | GPL-3 binary | Already a runtime dep; shelled out, no linking concern |

No new license tensions beyond the existing darktable runtime
dependency.

### Engineering cost breakdown (engineer-weeks)

| Work item | Estimate (eng-weeks) | Notes |
|---|---|---|
| File-watcher + debounce + mtime-based selector | 0.5 | Watchdog covers macOS+Linux; debounce is standard |
| Daemon/runner loop (re-render on event) | 0.5 | Wraps existing `runner.render_frame()` |
| Lower-resolution dt-cli render mode | 0.5 | Add `--height`/`--width` flags; verify dt honors them on TIFF |
| Per-frame XMP-emit ↔ dt-cli ↔ JPEG-write path | 0.5 | Mostly reuse |
| Daemon-only sub-candidate stops here | (sum = 2.0) | Sufficient for "JPEG folder + Preview.app" workflow |
| Minimal Qt/Tauri/web viewer with frame strip + A/B toggle | 2.0 | If the user wants a real window |
| Polished viewer (zoom, pixel-peep, color managed, hotkeys) | 2.0 | Optional |
| Testing + LRT integration shakedown | 0.5 | |
| **Daemon-only total** | **~2 eng-weeks** | |
| **Daemon + minimal viewer total** | **~4 eng-weeks** | |
| **Daemon + polished viewer total** | **~6 eng-weeks** | |

The `08_search_framing.md` table estimated F at 2–4 weeks; this
breakdown lands at the same range, with the upper bound now explicit
(it's "polished viewer," not "minimal").

### Risk register

| Risk | Severity | Notes |
|---|---|---|
| dt-cli per-frame latency too slow for interactive grading | **HIGH** | Central failure mode. See "Performance characteristics" |
| dt-cli startup overhead dominates render time for fast pipelines | HIGH | dt-cli has no daemon mode; each invocation re-parses configs and loads modules |
| Auto Transition batch write floods the watcher | MEDIUM | Debounce + mtime priority handles this, but a 5000-XMP save event needs careful queueing |
| Watcher doesn't know which keyframe LRT user is editing | MEDIUM | mtime heuristic + frame strip mitigates but doesn't solve |
| Power consumption / fan noise on macOS during continuous re-rendering | LOW | Quality-of-life issue; pause-when-idle alleviates |
| dt version churn changes render output between releases | LOW | Same risk lrt-cinema's production pipeline already carries; no new exposure |

### Validation plan

1. **Latency benchmark.** Measure end-to-end "LRT Save Metadata → viewer
   shows updated frame" on the user's Nikon D750 sequence. Three runs,
   median reported. Target: < 5s, ideally < 2s for one keyframe.
2. **Batch behavior test.** Run Auto Transition (5033 XMPs written),
   confirm the watcher does not crash, does not render every frame,
   and prioritizes the most-recently-modified XMPs. Target: viewer
   recovers to interactive-update mode within 30s of batch completion.
3. **Cognitive ergonomics field test.** Grader runs the parallel
   viewer alongside LRT for a full keyframe-authoring pass on a real
   sequence. Subjective report: did the viewer become the effective
   grading reference, or did LRT's preview remain dominant? Where did
   the grader actually look at decision points?
4. **A/B vs current state.** Same grader, same sequence, with and
   without the parallel viewer. Compare final-deliverable subjective
   quality (blinded, if feasible) and grader's reported time-to-
   confident-decision.

### Linux portability

`watchdog` covers inotify on Linux with the same API as macOS fsevents.
The Qt route is fully cross-platform; the Tauri route is fully cross-
platform; the browser route is fully cross-platform; the macOS-native
AppKit route is not. **Recommended path for Linux symmetry:** Tauri or
Qt for the viewer, drop the macOS-native option from serious
consideration. Engineering cost stays in the same range; macOS
integration polish loses slightly.

### Performance characteristics

**The discriminator is dt-cli render latency plus per-invocation
startup tax.** The literature does not give a single authoritative
number for the D750 case. What we can say with confidence:

- dt-cli has **no daemon mode**. Each invocation re-parses configs,
  loads modules, and exits — a fixed startup tax per frame.
- A user-reported 24MP benchmark cited 2.149s and 1.110s total
  pipeline times under different pipeline configurations (camera
  and CPU/GPU split not specified in the cited source).
- A bilateral-filter-heavy pipeline dropped from 3.97s to 1.415s
  with full OpenCL utilization (-63%)
  ([source](https://op-co.de/blog/posts/darktable_opencl_memory/)).
- darktable 5.x continues to improve OpenCL coverage; most color-
  pipeline modules we lean on are GPU-accelerated.

**Estimated D750 latency:** 1–2s startup + 2–10s CPU render, ~1–3s
GPU render. Needs a direct benchmark to sharpen — lrt-cinema's
existing dt-cli plumbing makes this a 1-day measurement task.

**At 5–10s end-to-end, F is too slow for interactive grading.** At
1–2s end-to-end with OpenCL + reduced-resolution render, F is
plausible; the grader adapts to the lag. F's viability hinges on
the empirical benchmark. The F-daemon sub-candidate (~2 eng-weeks)
is cheap enough to build as the benchmark vehicle itself.

**Update frequency capability:** with debounce, the viewer updates
once per Save Metadata. Whether the grader-perceived "save → updated
frame" cadence is 2s or 10s is the workflow-feasibility threshold.

## Candidate I: JIT preview-quality approximate viewer

The approximate-render variant: when LRT writes an XMP, lrt-cinema
runs a *fast, approximate* render — shader-based or OCIO-LUT-based —
and shows the result. Render is sub-second; the result is close to,
but not exactly, what the production darktable-cli pipeline produces.

### Implementation surface

Three sub-flavors, listed in order of fidelity and engineering cost.

**I-a: OCIO LUT-baked viewer.** Precompute the production dt pipeline
for a *fixed parameter setting* as an OCIO display transform (a 3D
LUT, typically 65³ or 17³). At preview time, decode the RAW with a
fast librarian (LibRaw / rawpy), apply per-frame dynamic operations
(exposure, white balance, LRT mask offsets) in linear space, then
apply the baked LUT for the look (DCP HSV cubes, tone curve, filmic /
sigmoid / AgX). **Key constraint:** the LUT can encode only the
*fixed* portion of the pipeline. Anything that changes per-frame
(exposure, WB, mask deltas) must be applied separately at preview
time. This is the central "how approximate is acceptable" issue
(see below).

**I-b: GPU-shader reimplementation.** Hand-port the relevant dt
modules to GLSL/Metal shaders. Apply directly to the decoded RAW.
Highest fidelity, highest engineering cost. Maintenance burden: every
dt module change requires a shader update.

**I-c: vkdt borrow.** vkdt is Johannes Hanika's (dt founder) Vulkan-
based replacement. Real-time on GPU. Native timelapse support. If
vkdt's pipeline can be made to match the dt pipeline closely enough,
it provides the I-a fidelity at the I-b execution speed. The
"approximation" is then bounded by how closely vkdt's port matches
dt's modules — actively maintained, not a one-off implementation
burden on us.
([source: vkdt project](https://github.com/hanatos/vkdt))

### Dependencies (with license)

| Component | License | Notes |
|---|---|---|
| LibRaw (RAW decode) | LGPL-2.1 / CDDL | Already an indirect dep via dt; dynamic-link clean |
| rawpy (Python LibRaw binding) | MIT | If staying in-Python |
| OpenColorIO 2.x | BSD 3-clause | Industry-standard, mature, GPU-shader-capable |
| Pillow / NumPy for pixel ops | PIL / BSD | Standard |
| vkdt (if I-c) | GPL-3 | Same license posture as darktable; shell out, don't link |
| GLSL/Metal shader runtime (if I-b) | varies | Likely Qt's QShader / Tauri's wgpu / direct Metal |

### Engineering cost breakdown (engineer-weeks)

**I-a: OCIO LUT-baked viewer**

| Work item | Estimate (eng-weeks) |
|---|---|
| LUT bake script: render dt pipeline at fixed params, sample to 3D LUT | 0.5 |
| LUT-bake-per-preset variant (one LUT per (camera, preset, look-grade)) | 0.5 |
| RAW decode + per-frame dynamic ops in CPU/GPU pipeline | 1.0 |
| OCIO integration (apply baked LUT) | 0.5 |
| Viewer (reuse F's viewer infrastructure) | 1.0–2.0 |
| Validation: compare I-a output vs production dt-cli output on N test frames | 0.5 |
| **Total** | **~4–5 eng-weeks** |

**I-b: GPU-shader reimplementation** — 8–12 eng-weeks. Likely
over-budget for the value, given that dt modules' implementations are
not stable across releases.

**I-c: vkdt borrow** — 3–5 eng-weeks if vkdt's pipeline maps cleanly
to dt's; substantially more if module-by-module behavior matching is
required. Depends on vkdt's maturity at evaluation time.

The `08_search_framing.md` table estimated I at 3–5 eng-weeks, which
matches I-a and I-c. I-b is out of scope.

### Risk register

| Risk | Severity | Notes |
|---|---|---|
| "Approximate" viewer diverges from production output at the points that matter for grading | **HIGH** | The grader's decisions might be wrong if the approximation systematically misrepresents the look |
| LUT-baked pipeline can't represent dynamic ops cleanly | HIGH | Exposure / WB / deflicker / HG must be applied separately and *commute* correctly with the baked LUT |
| LUT precision loss on highlights / shadows | MEDIUM | 65³ LUT is fine for SDR; for HDR / wide-gamut intermediate, precision matters |
| vkdt fidelity to dt's pipeline | MEDIUM (I-c) | Actively-maintained project but not a guaranteed match |
| Maintenance burden as dt evolves | MEDIUM | I-b is worst; I-a is moderate (re-bake LUT on dt update); I-c is best (vkdt tracks dt) |
| Preview's fidelity becomes the grader's reference, but final dt-cli render diverges | HIGH | Worse than the current state if the grader doesn't notice the divergence |

### Validation plan

1. **Per-frame ΔE2000 vs production dt-cli render**, across N test
   frames spanning the parameter space (low/high exposure, neutral/
   saturated, identity tone curve / aggressive curve). Target: < 2
   ΔE2000 mean (cinema-reference tier — see synthesis doc's tier
   table).
2. **Latency benchmark.** End-to-end save → viewer-show. Target:
   < 500ms.
3. **Dynamic-op commutation test.** Render frame at +1 EV via baked
   path and via full dt-cli path; verify difference is below noise
   floor. Repeat for WB drift, LRT mask offsets. (This is the central
   correctness test for the LUT-bake approach.)
4. **Cognitive ergonomics field test** (same as F's, but with the
   approximate viewer).
5. **Falsification check**: deliberately produce an XMP where the
   approximate viewer and the production render diverge; show the
   grader and ask whether the divergence would have changed their
   authoring decision.

### Linux portability

Same picture as F. OCIO is fully cross-platform. LibRaw is fully
cross-platform. vkdt is Linux-first (the dev environment) and works
on macOS with appropriate Vulkan loaders (MoltenVK). The viewer
infrastructure is shared with F.

### Performance characteristics

Sub-second per-frame is the target. Achievable on I-a (the LUT bake
is a memory lookup; the dynamic ops are simple per-pixel arithmetic
on a 1920×1280 preview ≈ 2.5M pixels, well within real-time on
modern GPU and ~100ms on CPU with NumPy). Achievable on I-c (vkdt is
designed for real-time). I-b would be the fastest if it worked at all
but is rejected for maintenance reasons.

**Update frequency capability:** ~10 frames per second on the LUT-
baked path, sufficient for continuous LRT-side scrubbing if the
file-watcher were augmented with a faster signal (e.g., LRT sending
inotify-visible touches on every slider move — currently it doesn't,
so the practical floor is "one update per Save Metadata click").

### How "approximate" is acceptable for grading-decision feedback?

This is the question that determines whether I is a viable workflow,
not just a viable engineering project. Four sub-questions matter.

1. **Is the approximation systematic or random?** A systematic
   offset (e.g., the viewer is uniformly +0.3 EV brighter than
   production) is workable — the grader recalibrates. A random per-
   frame divergence is not — the grader's decisions become noisy.
   I-a's expected behavior is systematic plus bounded error from LUT
   sampling; I-c's behavior depends on vkdt fidelity.

2. **Does the approximation preserve the *relative* response to
   slider movement?** The grader's primary action is "change a
   slider, see the result, decide if the new value is better." If
   the approximation correctly tracks the sign and magnitude of the
   change (a 0.5 EV exposure shift produces ~0.5 EV of brightening
   in the preview), the grader can reason fluently even if absolute
   values differ.

3. **Does the approximation preserve the *qualitative* tonal
   character?** Filmic vs sigmoid vs agx have very different
   highlight rolloff characters; the grader's response to highlight-
   clipping cues differs by character. A LUT bake of filmic + sigmoid
   captures the character; a LUT bake of one preset and then runtime
   substitution for the other does not.

4. **Does the approximation update when the LRT-side intent moves to
   a regime the LUT wasn't sampled for?** A LUT baked at neutral WB
   for a sequence shot at tungsten WB drifts; the more dynamic ops
   are pulled out of the bake, the less the LUT can stale. The
   tradeoff is precision (bake more) vs flexibility (bake less).

Minimum acceptable fidelity from the synthesis doc's tier table:
broadcast (≤ 3 ΔE2000) usable, cinema reference (≤ 2 ΔE2000) ideal.
Whether I-a hits this depends on dynamic-op factoring and LUT
sampling density — both empirically measurable in a 0.5 eng-week
validation harness.

## Sub-candidates / variations

- **F-daemon (no viewer).** Background daemon writes preview JPEGs
  to a folder; user opens that folder in their preferred viewer
  (macOS Preview, eog, IrfanView, Resolve's media pool). Reduces
  engineering by ~2–3 eng-weeks. **Recommended as the F-pilot.**
- **F-second-monitor.** Viewer auto-positions on the user's second
  monitor and always shows the most-recently-edited keyframe in
  fullscreen. Trades polish for spatial integration — the parallel
  reference becomes a constant the grader's peripheral vision can
  use.
- **F-QuickLook-plugin.** macOS-only. Register a QuickLook plugin
  that renders the deliverable preview when the user hits spacebar
  on the XMP in Finder. Very low cost (~3 days), low discoverability.
- **I-c vkdt borrow.** Worth a 1-week exploratory pilot to check
  whether vkdt's defaults reach the fidelity bar on the user's D750
  + bundled DCP. If yes, the rest of I collapses to "wire vkdt to a
  file watcher" — likely under 2 eng-weeks of new work.
- **F+I hybrid.** Two viewers side by side: I (fast, approximate)
  and F (slow, exact). Grader uses I for the decision and F as the
  ground-truth check. ~6 eng-weeks combined; possibly too many
  reference points cognitively.

## Cognitive ergonomics of parallel-reference grading

Can a human grader fluently use two different visual reference points
during a single decision?

**Static-image side-by-side comparison is well-established.** Soft-
proofing in print (ICC-soft-proofed on-screen preview beside the
calibrated reference); DI dual-monitor reference (primary grading
display beside a broadcast-spec secondary); color-matching booths
(D50 viewing booth beside the proof). These work because the
comparison happens at decision points, not during continuous
manipulation, and the references differ in known characterized ways.

**Moving-image parallel reference is much weaker.** Attention
division during motion costs accuracy on both streams. Cinema review
rooms show one version at a time and A/B between; they do not run
two projectors simultaneously. Audio mixing's reference-track pattern
is similar: engineers A/B between mix and reference *sequentially*,
not simultaneously. The dual-task attention literature confirms two
simultaneous visual streams interfere meaningfully
([source](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC6091269/)).

**Keyframe authoring is closer to the static case than the moving
case.** The grader makes discrete decisions at static frames —
selecting a keyframe, adjusting a slider, committing — not scrubbing
continuously. At decision points the cost of attention shift between
LRT and the parallel viewer is small. The cost only spikes if the
grader tries to use both during continuous slider manipulation,
which the workflow doesn't strictly require.

**Most likely failure mode:** the grader develops muscle memory
against LRT's preview character — what +0.5 EV looks like, where
the highlight rolloff sits. If the parallel viewer shows a different
character, the muscle memory becomes misleading rather than helpful,
and the grader must double-think every slider movement. The
engineering can succeed completely and the workflow can still fail
here. **The cognitive field test in F and I's validation plans is
the load-bearing test.**

## Open questions

- **dt-cli benchmark on the user's specific machine + sequence.**
  Central unknown for F's viability. ~1 day of work; should run
  before committing to F's full engineering scope.
- **Watcher heuristic for "currently-edited keyframe."** mtime-
  based heuristic is best-effort and may be wrong on bulk saves.
  Surface a manual override (user picks the keyframe in the viewer)
  as a fallback.
- **LUT-bake validation methodology.** What error metric, sampled
  at what frames, with what acceptance threshold? Cinema reference
  tier (≤ 2 ΔE2000) is a starting point; finer subjective evaluation
  (skin tones, neutrals, deep shadows) matters more than mean ΔE.
- **vkdt evaluation.** Does vkdt reach the fidelity bar on the D750
  + bundled DCP? Worth a 1-week pilot before committing to I-c.
- **OCIO disambiguation.** Two OCIO angles exist; don't conflate.
  Bake the *production* pipeline into a LUT for the viewer (I-a,
  this cluster) vs apply a *corrective* LUT to LRT's preview (G,
  Pattern 3, separate cluster).
- **Cognitive field test design.** A blinded comparison of grader
  performance with vs without the parallel viewer, with subjective +
  objective (time-to-decision, deliverable subjective quality)
  measures, is the load-bearing workflow test. Design it before
  committing to polished-viewer engineering.
- **Long-run dynamic.** If the parallel viewer becomes the grader's
  primary reference, LRT's preview becomes secondary. That reopens
  whether LRT's other authoring features (Auto Transition, deflicker,
  Holy Grail) are worth keeping over a clean-slate rebuild —
  connects this cluster to candidate D in the framing doc.
