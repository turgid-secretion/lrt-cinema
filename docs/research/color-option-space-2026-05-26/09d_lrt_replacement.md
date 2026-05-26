# LRT replacement cluster feasibility study (D)

*Cluster D from `08_search_framing.md`. The grader's authoring tool
becomes a clean-slate UI built on top of lrt-cinema's existing
rendering pipeline. The preview the grader sees IS the deliverable's
color science — single-stage, loop closes, no Adobe in the runtime
at any point.*

## Cluster summary

**Core proposition.** Replace LRTimelapse as the timelapse-grading
authoring tool. The grader works in a new GUI whose preview pane is
rendered by lrt-cinema's own pipeline (darktable, or a fast
proxy thereof). The XMP-shaped IR remains the authoring contract —
keyframes, deflicker offsets, Holy Grail ramps — but the
visual feedback comes from the same renderer that will produce the
final TIFF/EXR. There is no Adobe DNG Converter, no LR plugin,
no closed-source PV2012 anywhere in the loop.

**Structural argument for D.** Every workaround candidate (A,
A′, F, G, H, I) embeds a permanent Adobe-match calibration tower as
maintenance burden: per-camera DCPs, per-LR-version validation,
display-LUT plumbing fragility, parallel-viewer drift. **D
eliminates that burden entirely.** The author and the deliverable
are color-equivalent by construction. Linux portability falls out
for free (no DNG Converter dependency anywhere). The "Adobe-tied
authoring vs darktable-tied delivery" mismatch — the entire reason
this option-space study exists — ceases to be a problem.

**Honest spread of the engineering estimate.** The
`08_search_framing.md` table's "~16–18 engineer-weeks" was a
back-of-envelope rough estimate. This study sharpens it.
The honest v1 floor is **28–36 engineer-weeks (~7–9 months,
single developer)** for a usable LRT alternative that survives real
production timelapse work. The unbounded item is Visual Deflicker:
4 weeks for a serviceable single-pass version that passes a synthetic
test, 8–12 weeks for the multi-pass / EXIF-aware / tune-by-feel
behavior that approaches LRT's quality on real footage. The Visual
Previews real-time render path is the second-largest unknown:
darktable-cli is 2–10s per frame full-res, which is too slow for
keyframe editing feedback; a fast proxy renderer is itself a
4–6-week subproject.

**Structural risk D introduces.** Single-developer maintenance with
no business model. Gunther Wegner sustains LRTimelapse on a
Pro-tier paid product across 10+ years; an Apache-2.0 free clone
has no funding model for full-time maintenance. The choices are
Patreon-style donations (low conversion, won't fund full-time),
narrow-scope acceptance ("we don't promise LRT parity, ever"),
or eventually adding a paid tier (changes licensing posture).
The synthesis pass should weight this against the
workarounds-with-Adobe-burden trade-off explicitly.

## What we are replacing — LRT's feature surface

LRTimelapse has accumulated capability over a decade. v1 of a
replacement will not match it. The honest enumeration of every
LRT user-facing feature, with deliverable-relevance ranking:

### Tier 1: must-have for v1 (timelapse grading would be broken without these)

1. **Sequence ingestion.** Open a folder of RAWs, detect framerate
   intent, sort lexicographically, surface EXIF metadata (shutter,
   ISO, aperture, capture time). Multiple folders for multi-camera or
   multi-segment timelapses.
2. **Keyframe authoring UI.** Mark frames as keyframes (Rating
   convention or equivalent), select a keyframe, edit its develop
   sliders (exposure, white balance, contrast, highlights, shadows,
   whites, blacks, saturation, vibrance, sharpness, tone curve),
   commit. The full LR basic-panel surface in PV2012 semantics.
3. **Visual feedback per keyframe.** The preview pane shows the
   currently-edited keyframe rendered by lrt-cinema's pipeline.
   Updates within ~500 ms of slider drag-stop. This is the core
   D win — what you see is what you ship.
4. **Auto Transition equivalent.** Interpolate per-frame develop
   ops between keyframes. Linear (already implemented). Smooth
   (Catmull-Rom or similar; needs re-implementation — see
   "What we already have" below).
5. **Visual Deflicker.** Read LRT-rendered (now lrt-cinema-rendered)
   per-frame luminance, compute smoothed target curve, write per-frame
   exposure deltas back to the XMP-shaped IR. Multi-pass capability.
   Smoothing-factor slider. **This is the deepest closed-source
   capability in LRT.**
6. **Holy Grail Wizard.** Detect EXIF exposure step-changes
   (ISO/aperture/shutter shifts within sequence), compute a
   compensation exposure ramp, write per-frame deltas. Optional
   "Optimize" feature in LRT 7.0+ that auto-tunes the ramp.
7. **Save / load project state.** Equivalent of `lrtsequence.json` —
   workflow flags, smoothing parameters, project metadata, keyframe
   selection — persisted alongside the XMPs.
8. **Render trigger.** Render the sequence to TIFF/EXR via the
   lrt-cinema renderer. Progress reporting, error surfacing,
   parallel-worker scheduling.

### Tier 2: should-have for v1.x (LRT users will miss these)

9. **Holy Grail Optimize.** Auto-tunes the HG compensation curve to
   minimize residual flicker after combined HG+deflicker pass.
   LRT 7.0 feature.
10. **HDR merge.** Bracketed exposure ingestion, alignment, merge,
    write the merged sequence back as a virtual RAW source. LRT
    added this in 7.0; many timelapse photographers rely on it
    for high-dynamic-range scenes (day-to-night transitions
    cross the sensor's dynamic range).
11. **JPG-sequence support.** Some users shoot JPG (older cameras,
    aspirin-the-pain conversion of unsupported RAW formats);
    LRT 7.0+ treats JPG sequences the same way as RAW.
12. **Reference monitor / viewing transform selection.** Display
    the preview through different output transforms (sRGB,
    Rec.709, Rec.2020 PQ, AgX, ACES) without committing to one
    until render time. This is a feature LRT does NOT have well —
    a clean-slate replacement should leapfrog here.
13. **Visual Drag (legacy).** Drag-style fine-tuning on the
    preview pane (originally LRT 6.x feature, less central in 7.x).
14. **Workflow-step indicators.** LRT's status-bar indicators for
    which step the user has completed (Initialize → Keyframes →
    Auto Transition → Visual Previews → Visual Deflicker → Holy
    Grail → Export). Not strictly necessary but provides workflow
    guardrails.

### Tier 3: nice-to-have (LRT has these; v1 can defer)

15. **Hasselblad .fff RAW** (LRT 7.5+). Niche; most timelapse
    photographers use Nikon/Canon/Sony/Fuji. Falls out of
    LibRaw / darktable's RAW support coverage.
16. **ProRes hardware encoding on Apple Silicon** (LRT 7.5+).
    Out-of-scope for an authoring tool; ffmpeg shellout at the
    final-encode stage is sufficient.
17. **Internal JPG direct rendering** (LRT 7.0+). The whole point
    of D is to skip 8-bit sRGB JPG intermediate; this feature
    doesn't translate.
18. **Lightroom export plugin** (LRTExport plugin). Hands off to
    LR. D doesn't talk to LR; this feature is by definition
    absent.
19. **LRT Sync AI Tools** (LRT 7.5+). LR-side preset for AI
    masking. Not in D's scope; AI masking would be a separate
    feature route in dt or external.
20. **Patch Tool for Adobe DNG Converter Dock Icon** (LRT 7.4.1).
    D has no Adobe DNG Converter, so this is moot.
21. **Visual cropping** (LRT 7.0+). Shift-drag a crop rectangle
    on the preview pane. Useful but easy to defer.

### Tier 4: differences D is licensed to be different on

22. **Different keyboard shortcuts.** A user re-training cost.
    D shouldn't slavishly clone LRT shortcuts; it can use
    modern conventions (Cmd-S for save, etc.). The retraining
    cost is real and lives in the "challenges" register below.
23. **Different sequence file layout.** LRT writes `.lrt/`
    sidecar directory; D doesn't need to. The XMP-as-IR
    contract is the only authoring artifact D needs.
24. **No `.lrtpreview` JPGs.** D renders previews on demand from
    the actual pipeline; doesn't cache 8-bit sRGB JPGs.

The Tier 1 list — 8 must-haves — is **the v1 minimum**. Tier 2
adds another 4 weeks each minimum. Tier 3 is post-v1.

## What we already have in lrt-cinema

Roughly half of D's non-UI surface is already implemented. This is
the most important fact for the engineering estimate:

| Component | File(s) | State |
|---|---|---|
| **XMP parser** (LRT XMP → IR) | `src/lrt_cinema/xmp_parser.py` | Validated against real LRT Pro 7.5.3. Parses all parsed `crs:*` fields, `xmp:Rating` keyframes, `crs:MaskGroupBasedCorrections` for HG/Deflicker/Global per-frame deltas. ~480 lines. |
| **IR types** | `src/lrt_cinema/ir.py` | `DevelopOps`, `Keyframe`, `LRTMaskOffset`, `LRTSequence`. Frozen dataclasses with `blend()` for interpolation. ~190 lines. |
| **Interpolation engine (linear)** | `src/lrt_cinema/interpolation.py` | Piecewise linear with constant-extrapolation at endpoints. Catmull-Rom smooth mode **was deleted in the 2026-05-24 audit cleanup** (see file header) — needs re-implementation for LRT-compatible smooth interp. ~135 lines. |
| **Holy Grail ramp application** | `src/lrt_cinema/interpolation.py` `apply_lrt_mask_offsets()` | Applies HG/Deflicker/Global per-frame deltas onto interpolated base. Smoothstep blending math for ramps existed in v0.2 but was simplified to delta application in current shape. |
| **darktable XMP emitter** | `src/lrt_cinema/xmp_emitter.py` | Verified against dt 5.5.0+1375. Emits exposure, temperature, basecurve, lut3d, tonecurve, colorbalancergb, sharpen. Encodes hex-ASCII params blobs from C struct layouts. ~1100 lines. |
| **DCP parser + cube baker** | `src/lrt_cinema/dcp.py`, `src/lrt_cinema/lut3d_baker.py` | Parses Adobe DCPs to extract color matrices, HSV maps, look tables, baseline exposure, tone curve. Bakes HSV cubes to Resolve .cube format. |
| **darktable-cli runner** | `src/lrt_cinema/runner.py` | Single-worker subprocess scheduler. Builds argv per `EXPORT.md`. ~325 lines. |
| **CLI entry** | `src/lrt_cinema/cli.py` | `lrt-cinema inspect` and `lrt-cinema render` commands. Full argument surface for DCP / engine / deflicker / mask-offsets selection. ~500 lines. |
| **Output presets** | `src/lrt_cinema/presets/` | `cinema-linear` (16-bit linear Rec.2020 TIFF), `cinema-aces` (ACES OpenEXR), `stills-finished` (display-transformed). |
| **Test suite** | `tests/` | Unit + integration coverage for parser/emitter/interpolation/runner. ~2000+ lines of test code. |

**What this means.** Tier 1 items 1, 4, 7, 8 are essentially done.
Item 2 (keyframe authoring UI) needs the UI shell on top of
`DevelopOps` + `Keyframe`. Item 3 (visual feedback) needs the
fast preview path. Items 5 + 6 (Visual Deflicker, Holy Grail
Wizard with detection) are the big new ones.

## What we need to build new

### A. UI shell — keyframe authoring (Tier 1 item 2)

The application window. A sequence-browser timeline strip
(thumbnails of the source RAWs, marker overlays for keyframes,
scrub bar). A preview pane (the active frame's rendered output).
A keyframe-editor panel (sliders for every `DevelopOps` field).
A file-tree / folder-open command. A project-save command.
A menu bar. Keyboard shortcuts for navigation
(left/right, jump to keyframe, mark/unmark keyframe).

Engineering effort: **6–8 weeks** for a clean PySide6 application.
This includes wiring the sliders to the IR types, debouncing slider
drags to render queue, and the timeline-strip widget (which is the
hardest custom widget — thousands of frame thumbnails with scrolling
and selection).

### B. Visual feedback — fast preview render path (Tier 1 item 3)

dt-cli at full-res takes 2–10s per frame. Sub-second feedback
requires one of:

1. **GPU darktable** (libdarktable-gpu, OpenCL path). dt has OpenCL
   support but it's a heavy dep and not all module paths are GPU.
   Plus, embedding libdarktable as a library (vs CLI shellout) is
   not a supported / documented integration mode.
2. **Custom fast-path Python renderer.** rawpy (libraw) for
   decode + demosaic, numpy for the DevelopOps transforms (PV2012
   approximations), colour-science for the OETF/color transforms,
   output to a numpy array displayed in the GUI's image widget.
   Approximate; will not exactly match the final dt render but
   "close enough for authoring decisions" can be plausible if the
   approximation is careful.
3. **Cached-proxy + delta-apply.** Render every frame once at
   1024-wide via dt-cli in the background (analogous to LRT's
   Visual Previews step), cache. When the user edits a keyframe,
   apply the delta (exposure / WB / contrast) on the cached proxy
   via a fast GPU shader. Re-render the cached proxy only when
   structural changes (new keyframe, tone curve edit) happen.
4. **OCIO + rawpy fast path.** Use OCIO for the color transform
   chain, rawpy for decode, custom GLSL for the per-frame ops.
   Aligns with the cinema-pipeline workflow but is a sizeable
   ecosystem rewrite.

Realistically, option 3 (cached-proxy + delta-apply) is the lowest-
risk choice and the closest to what LRT actually does. It's not
"the preview IS the deliverable" in the strict sense — there's an
approximation step — but the approximation gap is small (exposure
+ WB linear deltas + a basecurve apply) and well-bounded. The cached
proxy is rendered by lrt-cinema's actual renderer, so the proxy
itself is faithful to the deliverable; only the live-edit overlay
is approximated.

Engineering effort: **4–6 weeks** for option 3, including the
background-render-queue, on-disk proxy cache management, and the
GPU shader for live overlay.

### C. Visual Deflicker (Tier 1 item 5)

This is the unbounded item. LRT's Visual Deflicker is closed
source. The published literature and open implementations:

- **TLDF (TimeLapse DeFlicker, Java)**. CLI-only deflicker tool by
  Marwan Razouk, github.com/MartinDecker/TLDF. Single-pass moving-
  average luminance smoothing with blacks/whites masking. Apache
  2.0. Last commit 2017. The algorithm: compute mean luminance per
  frame (excluding extreme blacks/whites), apply moving-average
  smoothing with configurable window, derive per-frame exposure
  delta = log2(smoothed/raw). This works for short-period flicker
  on roughly-stationary scenes; it's the lowest-bar deflicker.
- **Lefebvre's smoothness slider** (in his timelapse video
  tutorials). Bezier-shape smoothing of the luminance curve;
  user-tunable smoothness controls window width. Closed
  algorithm; same general shape as TLDF.
- **Multi-pass deflicker** (Wegner's documented framing in LRT
  forum threads). Each pass uses the previous pass's output as
  baseline. Converges toward the smoothing target without
  over-correcting at high-amplitude flicker. The math is
  iterative smoothing; the engineering is in the stopping
  criterion and the masking of blacks/whites (so a blown-out
  highlight or a black sky doesn't bias the luminance estimate).
- **LRT 7.5's "improved high accuracy Deflicker"**. Specifics not
  documented. The "improvement" presumably tunes the masking,
  smoothing kernel, or multi-pass convergence.
- **Academic literature**: Wang & Dong 2016 "Removing Flicker in
  Time-lapse Videos" (multi-scale luminance smoothing);
  Delbracio et al. 2014 "Hand-held Multi-image Super-resolution"
  (touches on temporal coherence). Both are academic, not
  user-facing tools.

The honest framing: **building a deflicker that LRT users find
acceptable on real footage is a research project disguised as an
engineering item.** "Acceptable" is a user-perceptual criterion, not
a numerical-residual criterion. Wegner has iterated 10+ years.

Engineering effort split:

- **Single-pass moving-average + blacks/whites masking** (TLDF-
  equivalent): **3–4 weeks**. Mechanical engineering. Probably
  workable for cleanly-lit sequences with mild flicker.
- **Multi-pass + EXIF-aware masking + tune-by-feel UI**: **+4–6
  weeks** more. Brings the result into the range users
  accustomed to LRT will find acceptable.
- **Holy-grail-aware deflicker** (suppress correction during
  intentional exposure ramps, otherwise it fights the HG ramp
  and creates oscillations): **+1–2 weeks**.

Total honest range: **8–12 weeks** for "comparable to LRT on the
range of scenes LRT users actually shoot." A v1 ship at the 4-week
TLDF-equivalent floor is plausible; expect a year of iterative
tuning on real footage before users-coming-from-LRT report
satisfaction.

### D. Holy Grail Wizard with detection (Tier 1 item 6)

Detection: read EXIF (shutter, ISO, aperture) for every frame in
the sequence. Detect step-changes (a frame's exposure-equivalent
EV differs from the running median by >0.3 EV is the conventional
threshold). Mark the step-change frames.

Compensation: for each step-change, compute the EV jump. The
compensation curve is the negative of the cumulative jump, smoothed
across N frames around the step (smoothstep, sigmoid, or
piecewise-linear with user-tunable smoothness).

Optimize (LRT 7.0 feature): auto-tune the per-step smoothness
parameter to minimize residual luminance derivative across the
step. This is a single-variable optimization per step; trivially
implementable once detection works.

Engineering effort: **2–3 weeks** including EXIF read, step
detection, smoothing curve UI, Optimize mode.

### E. Sequence ingestion / project state (Tier 1 items 1, 7)

Folder-open dialog. Recursive RAW-file scan. Sort + index.
EXIF parse for per-frame metadata. Persistent project state
(JSON sidecar analogous to `lrtsequence.json`): workflow flags,
deflicker parameters, HG parameters, last-opened-keyframe.
Auto-save and recover-from-crash.

Engineering effort: **2 weeks** including the folder-open UI,
EXIF integration, project-state schema, save/load.

### F. Render trigger / progress reporting (Tier 1 item 8)

Wire the existing `runner.render_sequence` to a background process.
Progress bar. Per-frame error surfacing. Cancel button. The
underlying renderer is already in lrt-cinema; this is purely the
UI shell on top.

Engineering effort: **1 week**.

### G. Migration support — read LRT sequences (read-only)

Open an existing LRT `.lrt/` sequence; parse the LRT XMPs (already
done); display in the new UI. The user can keep editing in the
new tool.

Engineering effort: **0.5–1 week** (mostly UI glue; parser is done).

### H. Migration support — write LRT-compatible XMP (round-trip)

If we commit to letting users round-trip between LRT and the new
tool during transition (some users want to switch but keep LRT
available for collaboration), we need to emit LRT-shaped XMP
(not just dt-shaped XMP). This means writing `crs:*` fields,
mask-correction encoding for HG/Deflicker/Global, the LRT
namespace attributes (`lrt:Aperture`, `lrt:ShutterSpeed`, etc.).

Engineering effort: **2–3 weeks** for full round-trip.

**Recommendation**: ship v1 with read-only LRT migration (G) and
emit only the new tool's native XMP. Round-trip (H) is post-v1.
Surfaced explicitly as a design choice; the synthesis pass may
disagree.

### I. Tier 2 items not in v1 scope

HDR merge (Tier 2 item 10): 4–6 weeks. Bracketed exposure
ingestion, alignment (cv2 ORB + homography refinement, or rawpy
post-demosaic alignment), merge (Mertens fusion or radiance-map
merge), virtual-RAW writeback. This is a large standalone
component; v1.x.

Reference-monitor transform selection (Tier 2 item 12): 1 week.
A toggle in the preview pane between sRGB / Rec.709 / AgX / ACES.
The transforms already exist in lrt-cinema's preset definitions;
this is purely UI surface.

Workflow-step indicators (Tier 2 item 14): 1 week. Status-bar
widget. Trivial once the project-state schema is in place.

## UI framework comparison

The framework choice is determined by the constraint set:
single Python developer, cross-platform (mac + Linux primary),
free / Apache-2.0 license envelope.

### PySide6 (Qt 6) — recommended

LGPL-3.0 (LGPL doesn't infect Apache-2.0 application code when
PySide6 is dynamically linked, which is the standard usage).
Python-native via Qt for Python. Mature on macOS, Linux, Windows.
Single-codebase. The Qt ecosystem includes high-quality widgets
for everything D needs: image view (`QGraphicsView` /
`QQuickItem`), timeline strip (`QListView` with custom delegate),
sliders, file dialogs.

Real-time preview integration: PySide6 supports OpenGL widgets
(`QOpenGLWidget`) and Vulkan integration via Qt RHI. Both are
viable for a custom shader-based fast preview path.

Distribution: PySide6 + PyInstaller produces a single
distributable bundle on each platform. macOS app-bundle, Linux
AppImage, Windows installer.

**Why it wins.** Same-language as the existing codebase. No
language boundary. One developer can productively maintain Python
+ PySide6 indefinitely. The framework has been around since 2014
(Qt for Python project); active development; well-documented.

### Tauri (Rust + web frontend)

Apache-2.0 / MIT. Cross-platform (mac, Linux, Windows). Mature
(1.0 released 2022; 2.0 in 2024). Smaller binary than Electron.
Good Linux story.

Python integration: subprocess. The Tauri frontend talks to a
Python backend over IPC (stdin/stdout JSON, named pipes, or HTTP
on localhost). The IPC layer is the engineering tax.

**Why it loses.** Adds a Rust + TypeScript/JavaScript codebase
on top of the existing Python. A single developer maintaining
Python + darktable + Adobe DCP parser + LR XMP schema cannot
also productively maintain Rust + a JS frontend in parallel.
The "modern" appeal doesn't compensate. Skip.

### Iced (Rust-native immediate-mode-ish)

MIT. Pure Rust. Cross-platform but the Linux story is immature
(Wayland support partial); ecosystem still pre-1.0.

Python integration: same subprocess-IPC tax as Tauri, plus the
ecosystem is less mature.

**Why it loses.** Same Rust-language-tax as Tauri, plus less
maturity. Skip.

### Electron (Node.js + web frontend)

MIT. Cross-platform. Very mature (10+ years). Large ecosystem.

Python integration: subprocess to a Python backend, same as
Tauri.

**Why it loses.** ~200 MB minimum binary size vs PySide6's
~80 MB. Slow startup. The Electron tax is real. Add the JS +
Node + Python maintenance to a single dev and the burden is
worse than PySide6. Skip.

### Slint (Rust + QML-inspired DSL)

GPL-3.0 / Royalty Free Desktop / Commercial. Cross-platform.
Growing. The .slint DSL is approachable.

Python integration: subprocess or Python bindings (the bindings
are pre-1.0).

**Why it loses.** GPL-3.0 strictness on the free tier is a
concern; the Royalty-Free Desktop license is acceptable but
introduces a tier ambiguity. Plus the Rust ecosystem tax.
Skip.

### Dear ImGui (immediate-mode)

MIT. Cross-platform. C++ with Python bindings (pyimgui or
DearPyGui). Very fast.

**Why it loses.** Immediate-mode is wrong for a production
authoring app with persistent state, undo/redo, file dialogs,
complex layouts. ImGui shines for dev tools and game-engine
debug UIs; not for end-user creative apps. Skip.

### Native AppKit (mac) + GTK/Qt (Linux)

Free. Native look on each platform. Best UX per-platform.

**Why it loses.** Two completely separate UI codebases. The
maintenance burden is 2-3x a single cross-platform codebase.
For a single developer this is unsustainable. Skip.

### Web-based (HTTP server + browser UI)

Free. Cross-platform trivially. The frontend can be vanilla HTML
+ JS, or React, or htmx, or anything web.

**Why it loses.** The real-time preview pane is the hard
component. WebSocket frame-by-frame image streaming is doable
but the latency budget for slider-drag → updated preview is
tight. No file-system access without the browser's permission
flow (or a custom-protocol scheme via a localhost server).
File-open dialogs are limited. Distribution is awkward ("here's
a Python process you have to run + open a URL"). Acceptable
for a v0.1 prototype; not acceptable for an LRT replacement.
Skip.

### Conclusion

**PySide6 is the answer.** The comparison's purpose was to make
the choice defensible, not to actually weigh alternatives. The
constraint set (single Python developer, cross-platform mac +
Linux, free license) picks PySide6 cleanly.

Other notes:

- License: LGPL-3.0 with dynamic linking does not infect Apache
  2.0 application code. This is well-tested legal ground (PyQt5
  applications have shipped Apache-2.0/MIT-licensed for years).
  Some commercial users may prefer to pay Qt commercial license
  for liability cover, but the free path is clean.
- Versioning: target PySide6 6.5+ (current LTS line). Avoid
  PySide6 6.0–6.4 which had a few cross-platform paint issues.

## Engineering work breakdown

Per-component engineer-week estimates assuming a single capable
developer with the existing lrt-cinema codebase as a starting
point. PySide6 chosen as the UI framework.

| Component | Engineer-weeks | Notes |
|---|---|---|
| **A. UI shell** (window, timeline, panels, menus) | 6–8 | Timeline-strip custom widget is the hardest piece. |
| **B. Fast preview path** (cached-proxy + delta-apply) | 4–6 | Background render queue, proxy cache, GPU shader for live overlay. |
| **C. Visual Deflicker** | 8–12 | Range: 3–4 weeks for serviceable single-pass; +4–6 weeks for multi-pass + masking + tune-by-feel. Adopt high end for honest planning. |
| **D. Holy Grail Wizard with detection** | 2–3 | EXIF reading + step detection + smoothing curve + Optimize mode. |
| **E. Sequence ingestion / project state** | 2 | Folder open, EXIF parse, project-state JSON, save/load. |
| **F. Render trigger / progress** | 1 | Background `runner.render_sequence` + progress widget. |
| **G. LRT migration read-only** | 0.5–1 | Mostly parser-wiring (parser already done). |
| **Re-implement Catmull-Rom smooth interp** | 1 | Was deleted in 2026-05-24 audit; need it back for parity with LRT's auto-transition. |
| **Testing** | 3–4 | Unit tests + integration tests + a Real-Footage Test Sequence (a published reference timelapse the user can manually verify against). |
| **Packaging + distribution** | 2 | PyInstaller bundles per platform; macOS notarization workflow; Linux AppImage; Windows installer (if shipping Windows). |
| **Documentation + user onboarding** | 2 | User guide, video walkthrough, migration-from-LRT guide. |
| **Total v1** | **31.5–42 engineer-weeks** | **~8–10 months single-developer.** |

Reconciling against `08_search_framing.md`'s ~16–18 weeks rough
estimate:

- The 08 estimate had "Auto Transition spline interpolation: 0
  wks (existing)" — wrong; Catmull-Rom was deleted in audit
  cleanup. +1 wk.
- The 08 estimate had "Visual Previews render path: ~2 wks" — too
  low; sub-second feedback requires the cached-proxy fast path
  outlined in component B. +2–4 wks.
- The 08 estimate had Visual Deflicker at "~3–4 wks (algorithm +
  UI + tuning)" — too low for parity-level. +4–8 wks for
  multi-pass and EXIF-aware masking.
- The 08 estimate had testing/docs at 2 wks — too low for a
  user-facing GUI app with multi-platform packaging. +3–4 wks.
- The 08 estimate didn't include the keyframe-authoring UI shell
  as a distinct line item (folded into "Keyframe authoring UI
  ~3–4 wks"); the realistic UI-shell scope is 6–8 wks.

The honest sharpened estimate is therefore **~31–42 engineer-
weeks** vs the 08 placeholder of ~16–18. Roughly 2x more
expensive than the back-of-envelope, before contingency for
unknowns.

Contingency: real-world software engineering against a
research-grade target (Visual Deflicker quality on real footage)
underruns 50% of the time. A 1.5x multiplier on the deflicker
budget is responsible planning; this would push the upper bound
toward 50 weeks.

**Realistic ship-date for v1**: 9–12 months single-developer.
v1 here is "usable replacement for the timelapse-grading workflow
on a Tier-1-only feature set, with v1.1 acknowledged as the
quality-iteration phase."

## Single-developer maintenance reality

Wegner sustains LRTimelapse on a Pro-tier paid product across
~10+ years. The licensing model is one-time payment per major
version, $99 / $399 tiers, presumably ~thousands of customers.
Indie developer; closed-source.

D as Apache-2.0 free software absorbs the maintenance burden
without the funding model. Honest considerations:

### What ongoing maintenance looks like

**Year 1**: build v1. Ship Tier 1. Fix the high-impact bugs that
real footage surfaces. Patch deflicker tuning iteratively as
user reports come in. Budget: 9–12 months as estimated above,
plus 3–4 months of post-ship iteration.

**Year 2**: Ship Tier 2 items (HDR merge, reference-monitor
transform selection, workflow-step indicators). Add the platform
coverage gaps users complain about (Hasselblad RAW if a user
hits it; Linux distro packaging variations). Budget: ~4-6 months
of new-feature work + ongoing bug fixes.

**Year 3**: dt 6.x ships with new module versions; all of the
emitter's modversion constants need re-validation. New cameras
ship (every year, dozens of new RAW formats); rawpy/libraw
coverage must follow. Apple ships macOS 17 and Qt 6.x updates;
distribution pipeline must follow. Budget: ~3-4 months of
keeping-up + new-feature.

**Years 4-5**: the maintenance tail. Bug fixes, dt version
churn, new camera support, occasional larger features (3D LUT
export, Avid integration, what-have-you depending on user base).
Budget: ~2-3 months/year.

Realistic ongoing maintenance: **~3-6 months of engineer-time per
year**, indefinitely. At a back-of-envelope rate of $150k/year for
a senior developer, that's $45k-$75k/year just to maintain D
without going backward.

### Funding model options

The free / Apache-2.0 / hobby-project options:

1. **Pure donations (Patreon / GitHub Sponsors).** Open-source
   creative tools rarely generate enough to fund full-time.
   Krita, Blender, etc. fund through corporate sponsorship or
   foundation grants; a single-developer color tool would
   struggle. Likely outcome: a few hundred dollars/month.
   Insufficient.
2. **Narrow scope acceptance.** Don't promise feature parity
   with LRT, ever. v1 ships, the developer maintains it on
   weekends, features add slowly. The user community accepts
   "stuck behind LRT" as the trade for free / open-source /
   not-Adobe-tied. Sustainable if the user base is small enough
   to be patient.
3. **Foundation grant.** ACES TC, GSoC, NLnet, or similar.
   Possible for one-time bursts (six-month grants happen) but
   not for sustained funding.
4. **Project accepts being perpetually behind LRT.** v1 ships
   with Tier 1; the gap to LRT 7.5 stays open; the user accepts
   "the cheap-and-open alternative" framing. This is the
   honest, achievable posture.

The dishonest options (avoided):

5. **Add a paid tier.** Changes licensing posture. Apache-2.0
   forecloses this unless the dual-licensed model
   (free-for-some-uses, paid-for-others) is set up day one. Not
   compatible with the current stewardship's stated framing.
6. **Pretend the maintenance burden is small.** It's not. LRT's
   user base is small but vocal, the deflicker tuning is
   research-grade, and platform churn is real.

### What the synthesis pass should weigh

The Adobe-match workaround candidates (A, A′, F, G, H, I) carry
a permanent calibration-tower maintenance burden (per-camera
DCPs, per-LR-version validation, ICC/LUT plumbing fragility).
D replaces that burden with a different one: keeping a
full-GUI Python app alive across platform / dependency churn,
plus the deflicker-quality tuning loop.

Both burdens are real. D's burden is larger in absolute
engineer-time but more bounded (no Adobe / LR version
surprises). The Adobe-match burden is smaller in absolute time
but unbounded — every LR / DCP update is a potential breakage,
and the project has no leverage to fix it.

## Risk register

### Technical risks

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| **Visual Deflicker doesn't match LRT quality on real footage** | High | High | Set Tier 1 acceptance at "TLDF-equivalent quality + multi-pass"; explicitly document "below LRT quality on hard cases" in user guide; iterate on user-reported real-footage. |
| **dt-cli proves unsuitable as the render backend** (silent default-substitution, version churn, etc.) | Medium | High | Already-existing modversion-pinning + SHA warnings in `runner.py`; investigate libdarktable embedding as a fallback. |
| **PySide6 distribution issues** (macOS notarization, Linux AppImage edge cases) | Medium | Medium | Use PyInstaller + py2app + PyOxidizer evaluation; budget 2 weeks for distribution work explicitly. |
| **Real-time preview path is slower than promised** | Medium | High | The cached-proxy approach is the lowest-risk; budget 4-6 weeks; if it fails, fall back to "render-on-demand at 2-5s/frame" which is still better than LRT's deflicker iteration loop. |
| **dt module modversion bumps break the emitter** | High (annually) | Medium | Existing dt-SHA warning catches this; document the upgrade procedure; pin the dt version users are expected to install. |
| **RAW format coverage gaps** (a user shoots a new camera, libraw doesn't have it yet) | Medium | Low | Same gap LRT has; libraw is generally fast to add new formats; document the supported camera matrix explicitly. |
| **Holy Grail detection false positives / negatives** | Medium | Medium | Test against a Holy Grail test sequence (day-to-night timelapse with known step changes); tune thresholds; surface false positives to the user with one-click reject. |

### Design risks

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| **User retraining cost** (LRT users have years of muscle memory) | High | Medium | Don't slavishly clone LRT shortcuts; use modern conventions; produce a migration guide. Some users will not move. |
| **Workflow shape doesn't match real timelapse photographer needs** | Medium | High | Iterate with a small beta-user cohort during v0.x; ship v1 with explicit "we want your feedback" framing. |
| **Color science choices** (default output preset, default DCP-or-not, AgX-or-not) **don't match user expectation** | Medium | Medium | Provide multiple output presets out of the box; default to a sensible "looks like LRT default" preset for v1; document the alternatives. |

### Maintenance risks

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| **Single-developer burnout** (10+ years' worth of LRT-clone work) | High (over 5 years) | Critical | Document the funding-model honestly; set expectations; accept the "perpetually behind LRT" posture explicitly; consider co-maintainers from day one. |
| **dt's project direction shifts unfavorably** (e.g., the dt team decides to drop sidecar XMP format) | Low | Critical | dt's XMP-sidecar contract has been stable for 10+ years; risk is low but tail-risk is total project loss. Plan: maintain a fork if necessary. |
| **PySide6 deprecation by Qt company** | Very Low | High | LGPL Qt has community backstop; unlikely to disappear. Migration to PySide7 / Qt7 will happen but is incremental. |

### Adoption risks

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| **Users don't switch from LRT** because it's familiar and supported | High | Critical (project loses purpose) | Tier 2 reference-monitor-transform-selection is the leapfrog feature LRT doesn't have well; lead with it. Linux-availability is the second leapfrog. |
| **User community doesn't form around the project** | Medium | High | Engage early in timelapse photographer forums; documentation must be exemplary; respond to user reports within days, not weeks. |
| **The "best" outcome is a small but stable user base** that uses the tool for hobby work but professional users stay on LRT | High | Medium | Accept this. v1 doesn't need to capture the LRT user base; it needs to be a viable open alternative for users who want one. |

## Dependencies (with license)

| Dependency | License | Purpose | Linux story |
|---|---|---|---|
| **Python 3.11+** | PSF (BSD-compatible) | Language runtime | Universal |
| **PySide6** | LGPL-3.0 | UI framework | Excellent; pip-installable |
| **darktable-cli** | GPL-3.0 (separate binary, called via subprocess) | Render backend | Native |
| **libraw / rawpy** | LGPL / CDDL-style | RAW decode for proxy renderer (if option 3 used for component B) | Native |
| **numpy** | BSD-3-Clause | Numerical primitives | Universal |
| **defusedxml** | PSF | Safe XMP parsing | Universal |
| **OpenImageIO (OIIO)** | BSD-3-Clause | TIFF/EXR read for preview pipeline | Native; widely packaged |
| **OpenColorIO (OCIO)** | BSD-3-Clause | Color transforms in the preview path | Native; ACES uses this |
| **colour-science** | BSD-3-Clause | Color-space math, tone curves | pip-installable |
| **PyInstaller** | GPL-2.0 (with linking exception) | App bundling | Cross-platform |
| **exifread / piexif** | BSD / MIT | EXIF parsing | Universal |

The license envelope is clean: Apache-2.0 application code can
incorporate LGPL (PySide6), BSD-licensed scientific libraries,
GPL-3.0 subprocess (darktable-cli, no link-time GPL contamination).
The dt-cli subprocess boundary is the GPL firewall; this is a
well-established pattern.

**Note on libdarktable embedding**: a future optimization is to
embed libdarktable as a shared library (in-process) for faster
preview rendering than dt-cli subprocess. **This crosses the GPL
firewall** — the application would become GPL-3.0. Not
incompatible with Apache-2.0 (Apache-2.0 + GPL-3.0 → effective
GPL-3.0 for the combined work). Surface this as a v2+ design
choice if performance demands it.

## Validation plan

### v1 acceptance criteria

1. **Real-footage test**: a 5000-frame Nikon D750 or equivalent
   sequence with 5–10 keyframes, a Holy Grail exposure ramp at
   ~frame 2500, and ~0.3 EV flicker. The user can:
   - Open the sequence in the new tool.
   - Edit each keyframe to a target appearance.
   - Run Auto Transition equivalent (interpolate).
   - Run Holy Grail Wizard equivalent (detect + compensate).
   - Run Visual Deflicker equivalent (smooth flicker).
   - Trigger a render to 16-bit linear Rec.2020 TIFF.
   - Result is comparable to LRT's output on the same sequence
     by visual inspection.
   "Comparable" is the operative word — not "identical." The
   author's intent is preserved; the deliverable matches the
   preview.
2. **Round-trip test**: open an LRT-authored sequence (the
   parser already does this); export the new tool's project
   state; close and re-open; verify keyframes, deflicker
   offsets, HG ramps are preserved.
3. **Cross-platform test**: build on macOS and Linux; smoke-test
   on both. Run the real-footage test on both.
4. **Performance test**: preview-pane updates within 500ms of
   slider drag-stop on a representative laptop (16 GB RAM,
   integrated GPU on macOS; 16 GB RAM, mid-tier discrete GPU
   on Linux).
5. **Visual Deflicker test**: a synthetic flicker sequence with
   known luminance perturbation; verify deflicker reduces
   residual luminance variance by >70%. (Numerical, not
   user-perceptual; the user-perceptual test is part of (1).)

### v1.1 (post-ship) iteration

Real-footage feedback loop. Bug reports from early users; tune
deflicker; tune Holy Grail; smooth out UI rough edges. Expect
this phase to take 3–6 months on its own.

## Linux portability

Per-component Linux portability:

| Component | Linux story |
|---|---|
| **Python + PySide6 UI** | Excellent. PySide6 is well-supported across major distributions; pip-installable. |
| **darktable-cli backend** | Excellent. darktable is Linux-native; available in all major distro repos. |
| **libraw / rawpy** | Excellent. Linux-native. |
| **OCIO / OIIO** | Excellent. Built daily on Linux as part of the ACES TC stack. |
| **Fast preview path (option 3: cached proxy + GPU shader)** | Good. PySide6 OpenGL widget works on Linux (Wayland and X11); shader code is GLSL, portable. |
| **EXIF parsing** | Excellent. exifread / piexif are pure Python. |
| **Packaging — AppImage** | Recommended primary Linux distribution format. PyInstaller can build AppImage-compatible bundles. |
| **Packaging — Flatpak** | Nice-to-have; more work than AppImage; cross-distro. |
| **Packaging — .deb / .rpm** | Per-distro; nice-to-have for distro maintainers but not strictly necessary. |
| **Auto-updater** | Linux convention is "use the package manager"; AppImage has its own update spec. Lower priority than macOS. |
| **HiDPI** | PySide6 handles HiDPI cleanly via Qt's scaling. Linux's HiDPI story has improved substantially; verify on a 4K display. |
| **File-dialog conventions** | PySide6 uses the platform's native dialogs (Wayland: xdg-desktop-portal; X11: GTK/Qt). |

**Conclusion**: Linux is well-supported by the component stack.
The only Linux-specific work is packaging (AppImage primary, 1–2
weeks of work) and verification on a representative distro matrix
(Ubuntu, Fedora, Arch; ~1 week of testing).

D's biggest structural win is here: **no Adobe DNG Converter
dependency means Linux is a peer platform, not an afterthought.**
LRT users have been asking for Linux support for 10+ years; LRT
can't deliver because Adobe DNG Converter doesn't run on Linux
(no LibRaw inside; the proxy generation path breaks).
D is Linux-native from day one.

## Migration path for existing LRT users

### Read path (v1)

A user with an existing LRT-authored sequence opens it in the new
tool. The parser ingests:

- LRT-shaped XMP per-frame sidecars (already done).
- `xmp:Rating`-flagged keyframes (already done).
- Mask-correction encoding for HG/Deflicker/Global per-frame
  deltas (already done).
- LRT namespace attributes (`lrt:Aperture`, etc., for EXIF-
  derived metadata; already done).

The user can keep editing in the new tool. The new tool emits
its own project-state JSON sidecar plus dt-shaped XMP for the
render pipeline. **The LRT `.lrt/` cache directory is ignored**
— D doesn't need LRT's proxy JPGs or proxy DNGs.

Engineering effort: **~0.5 week**. The parser is done; this is
purely the UI shell wiring.

### Write path (post-v1 — design choice)

Should the new tool emit LRT-compatible XMP so users can
round-trip between LRT and the new tool during transition? Two
postures:

- **One-way ETL**: the new tool reads LRT XMP, but emits only
  its own format. Users who switch can't go back. **Recommended
  for v1.** Simpler. Forces commitment.
- **Round-trip**: the new tool emits both formats. Users can
  collaborate (one author in LRT, another in the new tool, the
  sequence round-trips). **2–3 weeks of work** to implement
  LR/LRT-shaped XMP emission. Recommended for v1.1 or v1.2.

The synthesis pass should weigh the round-trip decision. The
project's stated posture is "lrt-cinema as a clean alternative,"
which is compatible with one-way ETL; users who want to keep
using LRT can keep using LRT, and users who switch commit.

### Workflow retraining

LRT users have ~years of muscle memory in LRT's UI:

- Specific keyboard shortcuts (left/right arrow for navigation,
  K for keyframe-mark, etc.).
- Specific workflow ordering (Initialize → Keyframes → Auto
  Transition → Visual Previews → Visual Deflicker → Render).
- Specific deflicker tuning vocabulary (smoothing factor,
  multi-pass, refine mode).

The new tool should:

- Borrow LRT's workflow-step ordering (it's well-established;
  no reason to differ).
- Use conventional shortcuts (arrow navigation; modifier-key
  scrolling; standard Cmd/Ctrl combos).
- Provide a migration guide that maps LRT terminology to the
  new tool's vocabulary explicitly.

The retraining cost is real and unrecoverable. Some users will
not switch.

## Open questions

1. **Should D ship with built-in deflicker, or defer to LRT-
   compatibility-via-mask-corrections only?** The latter sidesteps
   the deflicker-algorithm risk entirely: D becomes a keyframe
   authoring tool that consumes LRT's deflicker output (already
   parsed via the mask-correction path). Users who want deflicker
   keep LRT alongside D. This shrinks the v1 scope by ~8–12
   weeks but makes D a partial-replacement rather than full-
   replacement. **Recommendation**: ship Tier 1 deflicker
   (single-pass TLDF-equivalent) in v1; keep the mask-correction
   read path for LRT-deflickered sequences; iterate on
   deflicker quality post-v1.

2. **Should D commit to PV2012 semantics**, or design its own
   develop-op model? LRT users expect PV2012 behavior because
   that's what they've authored against. But PV2012 is closed-
   source and we don't actually implement it — we approximate
   via dt modules. Designing a new develop-op model (more
   alignment with dt-native semantics; fewer LR-compatibility
   compromises) might produce a better authoring experience but
   widens the LRT-retraining gap. **Recommendation**: PV2012-
   shaped IR for v1 (compatible with the existing parser);
   layer dt-native ops as additional features later if needed.

3. **Real-time preview at 4K vs 1024-wide proxy.** The cached-
   proxy approach renders at 1024-wide (LRT's Visual Previews
   resolution). Users authoring a 4K sequence may want full-res
   feedback at some review stage. The "render-current-frame-at-
   full-res-on-demand" option costs 2–10s per frame but is
   acceptable as a manual command. **Recommendation**: ship
   1024-wide proxy as the live preview; add "full-res render
   this frame" as an explicit user action.

4. **What's the v1 default output preset?** lrt-cinema currently
   ships three presets (cinema-linear, cinema-aces,
   stills-finished). D should ship the same set. What's the
   default for new sequences? **Recommendation**: cinema-linear
   (16-bit linear Rec.2020 TIFF) as the cinema-target default;
   stills-finished (display-transformed AgX or similar) as the
   stills-target default; the user picks at project-create time.

5. **Should D bundle a darktable build, or require user-install
   of darktable?** Bundling: predictable; users can ship-it-and-
   forget. Requires-user-install: respects platform conventions;
   keeps the install size small. **Recommendation**: require
   user-install of darktable (document the install steps per
   platform); D's PyInstaller bundle is much smaller and the
   "darktable not on PATH" error is already well-handled in
   `runner.py`.

6. **Co-maintainer recruitment.** Single-developer maintenance
   is the highest structural risk. From day one, should the
   project actively recruit co-maintainers? **Recommendation**:
   yes; document the codebase exemplarily; respond to
   contributions with low friction; explicitly invite
   collaboration. This doesn't change the engineering estimate
   but it materially affects the 5-year survival outlook.

7. **Validation against LRT output during v1 development.**
   Should D's render output be validated for ΔE against LRT's
   output during development, to demonstrate "yes the look
   matches"? Or is it explicitly off the table (D's deliberate
   posture: the preview IS the deliverable; we don't owe LRT
   parity)? **Recommendation**: don't validate against LRT
   output. D's value proposition is escaping the Adobe-pipeline-
   match treadmill. Validate against the user's authored intent
   in the new tool's preview pane.

## What this study deliberately does NOT cover

- The downstream colorist's Resolve/Baselight workflow. D ships
  TIFF/EXR intermediates; what the colorist does with them is
  out of scope.
- Comparison of v1 cost vs lifecycle cost. Engineer-weeks is
  v1-implementation only; the maintenance-tail discussion
  surfaces lifecycle considerations qualitatively.
- Marketing / community-building strategy. Adoption-risk
  surface is acknowledged; the "how to actually grow a user
  base" question is for a separate document if D is selected.
- Specific design language / visual style. PySide6 chosen;
  the actual appearance of the app is downstream of
  selection.

## Provenance

This study draws on:

- `docs/research/color-option-space-2026-05-26/08_search_framing.md`
  — problem framing and D's rough estimate.
- `docs/reference/lrtimelapse/*.md` — LRT feature surface
  documentation (the user did this research in the previous
  pass; it's foundational to the "what we replace" analysis).
- `src/lrt_cinema/*.py` — the existing implementation surface.
  Roughly half of D's non-UI surface is already implemented.
- Public sources on UI frameworks (PySide6, Tauri, Iced,
  Electron, Slint, ImGui documentation and recent comparisons).
- Published deflicker algorithm references (TLDF, Lefebvre's
  approach, Wegner's forum posts, Wang & Dong 2016, Delbracio
  et al. 2014).
- Linux portability assessment based on the dependencies'
  documented Linux support.

Quantitative cost estimates are first-pass engineering judgment
informed by the existing codebase scope and the LRT feature
surface; they are not validated by an external review. The
synthesis pass may adjust.
