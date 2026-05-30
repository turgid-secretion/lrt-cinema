# vkdt-fork + LLM-built UI strategy

**Status:** Research / decision spike, 2026-05-29. Deep evaluation of the
**fork-vkdt** path for the standalone, open-source, Adobe-free LRTimelapse
replacement (see [v09-standalone-app-build-vs-not.md](v09-standalone-app-build-vs-not.md)
and [v08 §1.5](v08-timelapse-emission-survey.md)), with a specific focus on
**how a Lightroom-class UI/UX is created on top of vkdt's engine when the build
is done MOSTLY BY LLMs (Claude).**

**Evidence tags:** **[verified]** = confirmed against a primary source (source
code in a fresh clone of `github.com/hanatos/vkdt`, vendor docs, official
spec/repo). **[doc]** = stated in official documentation, not independently
re-confirmed. **[claim]** = community/forum/inference/single-source/reasoning.
Method: cloned vkdt and read the source for Q1/licence; ran a multi-agent
research+adversarial-verification pass for Q2–Q4 (4 load-bearing claims each
assigned a skeptic instructed to refute). Sources listed at the end.

---

## TL;DR — the verdict in five sentences

1. **Forking vkdt's engine is sound and recommended.** Its node-graph GPU
   develop pipeline is cleanly decoupled from its nuklear GUI (proven by its own
   headless CLI binary) and is BSD-2; a new UI is just a third front-end.
   **[verified]**
2. The whole decision reduces to **one fork-in-the-road**, because the two hard
   requirements pull in opposite directions: **"approach Lightroom, built mostly
   by Claude" pushes hard toward a web UI (React/Tailwind/shadcn)**, while
   **"keep vkdt's zero-copy GPU-resident preview" pushes toward a Vulkan-native
   UI (Qt/QML)** — and you largely cannot have both. **[verified]**
3. **Two paths are genuinely viable, and it is closer to a toss-up than a clear
   winner — because each puts the LLM's difficulty in a different place.**
   **Web (Tauri/Electron + React/Tailwind/shadcn)** gives Claude the best possible
   flywheel for the *chrome* (~90% of the UI) but makes it build the *viewport*
   bridge — bespoke external-memory/IPC/readback glue — in its **weakest** regime,
   and the viewport *is* the product for a raw tool. **Qt/QML** inverts that:
   the viewport is **trivial documented wiring** (`fromDeviceObjects` +
   `QRhiTexture::createFrom`) that **preserves vkdt's zero-copy preview**, but the
   chrome is built without any of Claude's web-only aesthetic tooling. **[verified]**
4. **The tie breaks on ONE empirical question, not on paper: does a high-bit-depth
   proxy-readback viewport give acceptable *interactive* latency (slider-drag
   feedback on one frame, and proxy scrub)?** Build that Phase-1 prototype first.
   **If yes → web** (and prefer **Electron over Tauri**, the only web stack with a
   real GPU-interop upgrade path); **if no → Qt/QML.** The honest decision ladder
   is three rungs: **tier-c Tauri → tier-b Electron → tier-a/b Qt.** **[verified]**
5. **From-scratch (Qt/Vulkan or Rust/wgpu) is not better:** it has the *same*
   web-interop limitations and throws away vkdt's banked GPU pipeline; the cobble
   path stays rejected. **[verified]**

---

## 0. Corrections to the prior research (load-bearing)

Three claims in the prior docs were refined or overturned by direct inspection:

- **"vkdt has no Windows support" → partly false.** vkdt **already ships a
  pre-alpha Windows build**: the Makefile has a `Windows_NT` branch +
  `bin/config.mk.defaults.w64` (MSYS2/UCRT64), and CI's `WindowsRawler` job
  builds `vkdt.exe`. Upstream labels it "pre-alpha, known issues" (#103), but the
  breakage is concentrated in **GUI/DB/peripheral conveniences** (symlink-based
  tags, drive letters, console colours, v4l2/ALSA) — **not** the core Vulkan
  compute engine. Since a fork replaces the UI anyway, **replacing it *reduces*
  net Windows effort.** **[verified — Makefile, nightly.yml, issue #103]**
- **"vkdt is predominantly BSD-2 with a few isolated GPLv3 files to excise" →
  understated.** The GPL is **not** confined to disposable leaf modules: the
  **foundational Vulkan bootstrap layer `src/qvk/` (~1,254 LOC) is GPLv2-or-later**
  (derived from Christoph Schied's Q2RTX), and `shared/oetf.glsl` (GPLv3) is
  `#include`d by the **core `colour` module**. The engine *architecture* is still
  BSD-2, but the de-GPL task is **bounded engineering, not "delete a few files."**
  **[verified — file headers]**
- **vkdt's RAW decoder is `rawloader` (Rust, WTFPL-permissive), not GPL
  rawspeed.** This is a positive the prior docs missed: raw ingestion — the
  foundation of the tool — is already permissive. **[verified — i-raw/main.c,
  Cargo.toml]**

---

## 1. Engine/GUI decoupling (Q1) — VERIFIED clean

**Rating: clean (decoupling enforced by the build graph, not just convention).**

**Proof-by-construction.** `src/Makefile` builds two binaries from the *same*
engine objects: the GUI `bin/vkdt` = `GUI + QVK + CORE + SND + PIPE + DB`, and
the headless `bin/vkdt-cli` = `CLI + QVK + CORE + PIPE + DB` — **the CLI links
zero GUI objects** and runs a full animation export to disk without them
**[verified — Makefile:113-120]**. The GUI (`src/gui`, nuklear) and CLI
(`src/cli`) are two independent front-ends over the engine
(`src/pipe` + `src/qvk` + `src/core` + `src/db`). A new UI is a **third
front-end**.

**The headless engine API a foreign UI would drive** (mirrors `src/cli/main.c`)
**[verified]**:

```
dt_pipe_global_init()            // scan + dlopen modules, parse params/connectors → registry
qvk_init(name,id, window=0,…)    // create VkInstance/PhysicalDevice/Device + queues; window=0 = headless
dt_graph_init(&graph, s_queue_compute)
   // build graph: dt_module_add(dt_token(class),dt_token(inst)) + dt_module_connect(...)
   //   OR load a flat-text .cfg (module:/connect:/param:/keyframe: lines)
   // set params: write packed binary blob at module->param + offset (matched BY OFFSET)
   // keyframes: dt_keyframe_t + dt_graph_apply_keyframes(graph) for the current graph->frame
dt_graph_run(&graph, run_bitmask) // INTERACTIVE: bitmask re-runs only what changed
   //   (param-only change skips node re-creation → fast slider feedback)
   //   OR dt_graph_export(&graph, &export_params) // one-shot render whole animation to disk
dt_graph_get_display(graph, dt_token("main"))  // → node whose dset[frame] is sampler-ready
dt_graph_cleanup / threads_global_cleanup / qvk_cleanup
```

Everything is addressed by `dt_token_t` (a packed 8-char int). The interactive
`dt_graph_run` with a run-phase bitmask (`s_graph_run_record_cmd`, etc.) is
exactly what an editor needs: drag a slider → re-run only the command-record/
submit phases, not node allocation. **[verified — graph.h, modules/api.h,
render_darkroom.c:1380]**

**GPU-resident display path (the "killer feature").** A graph contains a sink
module of class `display`; after `dt_graph_run`, that node's output connector has
a descriptor set `node->dset[frame]` (`VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER`
over the output `VkImage`'s view, layout `SHADER_READ_ONLY_OPTIMAL`). The GUI
hands that handle straight to nuklear — `nk_image_ptr(out->dset[display_frame])`
— and nuklear's Vulkan backend binds it as descriptor **set=1**:
`vkCmdBindDescriptorSets(..., set 1, &dset, ...)`. **No CPU readback, no copy —
the engine's output image is sampled directly by the UI's fragment shader because
both share `qvk.device`.** Double-buffered (`dset[graph->double_buffer]`) with two
timeline semaphores (`semaphore_display`, `semaphore_process`). **[verified —
graph-run-nodes-allocate.h:154-266, render_darkroom.c:589, nuklear_glfw_vulkan.h:1288]**

**Foreign-UI contract — the decisive split:**

- **(i) Another Vulkan UI on the SAME device** (Qt-on-Vulkan, a custom Vulkan
  renderer, ImGui): **nothing must be added to the memory plumbing.** The
  replacement UI (a) shares the `VkDevice` qvk created (or qvk is refactored to
  accept an externally-created device), (b) declares a `set=1` layout compatible
  with the single `COMBINED_IMAGE_SAMPLER`, (c) honours the `double_buffer` index
  and the two timeline semaphores, (d) reads aspect from
  `out->connector[0].roi`. "That is the entire contract — the engine already
  produces a sampler-ready dset." nuklear is living proof. **[verified]**
- **(ii) A different GPU API/device** (Metal, WebGPU, D3D, or a browser
  compositor): must add **both** `VkExportMemoryAllocateInfo` +
  `VK_KHR_external_memory_*` (so the output image yields an importable
  FD/IOSurface/NT-handle) **and** `VK_KHR_external_semaphore_*` (export the
  timeline semaphores so the consumer waits for completion — omitting this
  races/tears). vkdt requests **none** of these today (grep of `src/qvk`+`src/pipe`
  for `VK_KHR_external*`/`GetMemoryFd`/`MTLTexture` is empty), so this is **engine
  surgery on the allocator + sync code.** **[verified — qvk.c:411-433, grep]**

This split *is* the crux of the whole report: a same-device Vulkan UI keeps the
killer feature for free; everything else pays an interop tax.

**Extensibility (very LLM-friendly).** A new processing node is a directory of
small text/GLSL files, auto-discovered by a Makefile glob — **no central
registration list to edit.** The minimal pure-shader module (template:
`exposure/`) is 5 tiny files: `connectors` (2 lines), `params` (1 line/param),
`params.ui` (1 line/param, drives the auto-generated widget), `main.comp` (the
GLSL compute kernel), `readme.md` (tooltips auto-extracted). Clean-room colour
science = pure-shader modules with your own math; deflicker = a feedback
connector (`s_conn_feedback`, already exists) reading frame N-1; Holy-Grail ramp
= keyframed params (substrate built in). **"An LLM can add a new colour/
deflicker/ramp node by copying the exposure directory and editing 4 short files
+ one GLSL kernel."** **[verified — exposure/, denoise/, modules/api.h]**

---

## 2. The central collision (the crux behind Q2 + Q3)

Every UI candidate must be scored on **one row** because the axes trade off:

> **The most LLM-buildable, most beautiful UI stack (web/React) is the *worst*
> GPU-interop case. The stack that *preserves* vkdt's zero-copy preview (Qt/QML,
> Vulkan-native) is the *weakest* LLM-buildability case. Flutter loses on both.**

The interop axis is a **3-tier ladder**, not a binary:

| Tier | What it is | Latency | Who can reach it for vkdt output |
|---|---|---|---|
| **(a)** | Zero-copy, **same Vulkan device** — sample the output `VkImage` in place | sub-ms (nuklear today) | nuklear; **Qt/QML** (adopts vkdt's `VkDevice`); a custom Vulkan UI |
| **(b)** | One **GPU→GPU interop copy** across an API boundary (Vulkan→Metal, Vulkan→WebGPU shared texture) — no CPU readback | ~sub-ms–few ms, *if it works* | **experimental everywhere**: MoltenVK export, Electron `sharedTexture`, Qt-on-macOS (MoltenVK→Metal) |
| **(c)** | **CPU readback → re-upload** per displayed frame | tens of ms @4K (≈67 MB RGBA16F) | the default for any web/WebView UI; mature but slow |

**The four load-bearing interop claims were each adversarially tested and all
four were REFUTED as stated** (full reasoning in §3/§5; sources at end):

- *"A web UI (Tauri/Electron) can preserve vkdt's zero-copy preview on all 3
  OSes."* **Refuted, high confidence.** Tauri **cannot** display a Vulkan frame
  in the web layer at all (system WebViews have no offscreen-compositing path;
  maintainer: "not supported and probably won't ever be," and the transparent-
  overlay hack works "not really on linux"). Electron *now* has an **experimental**
  `sharedTexture.importSharedTexture` (imports as a media `VideoFrame`, per-OS
  handles, Linux gated by a conditional flag) — but it still requires forking
  vkdt to export external memory + semaphores. **[verified — tauri#13740,
  WebView2Feedback#547, electron#46779/#46811]**
- *"MoltenVK reliably exposes a `VkImage` as an `MTLTexture` (zero-copy) for a
  Metal/WebGPU UI, production-ready 2026."* **Refuted, medium confidence.** The
  mechanism is real (`VK_EXT_metal_objects`/`vkExportMetalObjectsEXT`) but
  requires the image be **created up-front** with the export flag, may return
  **NULL** for buffer-backed/linear images, and the path had severe recent bugs
  (#2151 ARC crash, #1631 sync). No production deployments found. **[verified —
  Khronos proposal, MoltenVK #2151/#1631/#2209]**
- *"Native WebGPU (wgpu/Dawn) can import an external Vulkan image, production-
  ready 2026."* **Refuted, high confidence.** No standard API (W3C WebGPU's only
  external import is `importExternalTexture` from `HTMLVideoElement`); wgpu offers
  only the unstable internal `create_texture_from_hal` (RFC #3145/#2320 open for
  years); Dawn's `SharedTextureMemory*` are non-standard, WIP-documented, and used
  in production only for platform-native producer surfaces (IOSurface/dma-buf/
  AHardwareBuffer), not arbitrary `VkImage`s. A March-2026 writeup attempting this
  abandoned it and kept CPU readback. **[verified — W3C WebGPU, wgpu#2320/#3145,
  Dawn docs, ginokent 2026]**
- *"Mostly-Claude development can approach Lightroom-class aesthetics."*
  **Refuted as a conjunction, medium confidence — with a decisive nuance:** it is
  **far more defensible for a web UI than for a native one.** See §3. **[verified
  — Anthropic Frontend Design SKILL.md, Anthropic harness-design blog]**

---

## 3. UI-stack comparison (Q2) + the LLM-UI-quality question (Q3)

### 3.1 Unified scoring table

| Candidate | Interop tier for vkdt output | Latency @4K | LLM-buildability | Aesthetic ceiling | Licence | Net |
|---|---|---|---|---|---|---|
| **Tauri + React/Tailwind/shadcn** | **(c)** default; (b) only via fragile transparent-overlay hack (macOS-only, "not really on linux") | tens of ms (c); proxy/RGB8 readback (~25 MB) cuts it materially | **High** (densest training data; shadcn/Radix; screenshot-feedback via browser/preview MCP) — *but the GPU bridge is LLM-hostile* | **High** (full CSS; Linear/Figma-class) | MIT/Apache | **Best build × aesthetic; worst interop** |
| **Electron + React** | **(b)** via experimental `sharedTexture` import; else (c) | (b) near-native *if it works*; (c) otherwise | **High** (same web stack) | **High** | MIT | Like Tauri but heavier; the one web path with a real (experimental) tier-(b) |
| **Qt 6 / QML** | **(a)** on Linux/Windows (adopts vkdt's `VkInstance`/`VkDevice`); **(b)** on macOS (MoltenVK→Metal) | sub-ms (a) / low-ms (b) — **never forced to (c)** | **Moderate** (75–86% on Qt's QML100; a Claude-tested QML skill exists) — *but no web aesthetic tooling applies* | **High** (Resolve proves *Qt's* ceiling — likely Widgets, not QML; **QML-specific:** Substance 3D, Filmulator) | LGPLv3 / commercial | **Only stack that preserves zero-copy; weak on LLM-aesthetic** |
| **Flutter (Dart)** | **(c)** today; tier-(a) impossible (own device); tier-(b) is an open **P3** issue | readback today | **Low-moderate** (Dart < React; no web aesthetic tooling; no live-preview flywheel) | Medium-high (Rive proves it, but bespoke) | BSD-3 | **Dominated on both axes** |
| *nuklear / egui (baseline)* | (a) native | sub-ms | n/a (Claude can't make it look good) | **Low — "tool look," below the bar** | — | The thing being replaced |

### 3.2 How each binds to the engine + displays GPU frames

- **Qt 6 / QML — tier (a), the interop winner.** Qt 6's RHI can render its QML
  scene graph on an **externally-created** Vulkan device and wrap vkdt's image
  zero-copy: `QVulkanInstance::setVkInstance()` adopts an existing `VkInstance`;
  `QQuickGraphicsDevice::fromDeviceObjects(physDev, dev, queueFamilyIdx)` +
  `QQuickWindow::setGraphicsDevice()` makes the scene graph "use the existing
  device objects … instead of creating new ones … sharing resources … between Qt
  Quick and native rendering engines"; `QRhiTexture::createFrom({VkImage,layout})`
  wraps the existing texture non-owningly; `QSGRenderNode` draws it inline,
  "avoiding an additional render target." On Linux/Windows this is genuine
  tier-(a) — the same thing nuklear does. **macOS caveat (load-bearing):** Qt's
  RHI **defaults to Metal** on Apple, and running it on Vulkan/MoltenVK to match
  the engine is "supported on a best-effort basis only" with "reported stability
  issues" — so on macOS you either ship a best-effort Vulkan-on-MoltenVK Qt build
  or accept a tier-(b) MoltenVK→Metal hop (still GPU-resident). **[verified — Qt
  docs: QVulkanInstance, QQuickGraphicsDevice, QRhiTexture, QSGRenderNode; Qt blog
  "Qt Quick on Vulkan/Metal/D3D pt.2"]**
- **Tauri + React — tier (c) by default.** The system WebView owns its own
  compositor/GPU process; there is **no API to hand a foreign `VkImage` into the
  DOM**, and "Safari 26 ships WebGPU" does **not** help — WebGPU's
  `importExternalTexture` accepts only `HTMLVideoElement`/`VideoFrame`, never a
  native handle. So the idiomatic path is: engine renders headless → **GPU→CPU
  readback** → IPC/shared-mem → upload to an `HTMLCanvas`/WebGPU texture. The only
  GPU-resident escape is to composite a **transparent WebView over vkdt's own
  MoltenVK swapchain** — demonstrated only as a macOS *game overlay*, never an
  editor viewport, Windows/Linux unproven. **[verified — tauri#8246/#11944/#13740,
  MDN/W3C WebGPU, qwook/tauri-plugin-steam-overlay]**
- **Electron + React — tier (b), experimentally.** `sharedTexture.importSharedTexture`
  (merged, **experimental**) imports an external shared texture (Win `ntHandle`/
  mac `ioSurface`/Linux `nativePixmap`) as a `VideoFrame` — a real cross-context
  zero-copy path, but `VideoFrame`-shaped (format/colour constraints for HDR), the
  Linux path conditional, single-contributor, and still presupposes vkdt exports
  the handle. **[verified — electron#46779, PR#46811, shared-texture docs]**
- **Flutter — tier (c), worst-in-field.** Flutter owns its own renderer/device
  (Skia today on Windows/Linux/macOS; Impeller-on-desktop is a *2026 roadmap*
  item, not shipped), so tier-(a) is architecturally impossible; importing an
  external `VkImage` is an **open, unassigned P3 issue** (flutter#117937). Today =
  CPU readback. **[verified — docs.flutter.dev/perf/impeller, flutter#117937/
  #183495]**

### 3.3 The LLM-UI-quality question (Q3) — concrete and opinionated

This is the user's central concern, so be blunt about it.

**Where Claude genuinely excels (build here):**
- **Component code from a spec or a screenshot, in React+Tailwind+shadcn
  specifically.** This is the densest region of Claude's training data and where
  *all* the tooling lives. In `screenshot-to-code`'s own eval Claude scored
  70.31% vs GPT-4V's 65.10% and was "much less lazy" (generated full content, not
  placeholders). **[verified — abi/screenshot-to-code eval]**
- **Theming/token plumbing** (wiring shadcn's CSS-variable contract, restyling
  whole component sets) — mechanical, pattern-dense, reliable. **[verified]**
- **Iterating against visual feedback *when given eyes*** — the loop "write →
  render in a real browser → screenshot → inspect → fix" closes with Playwright/
  Claude-in-Chrome/Claude-Preview MCP tools; reports cite Opus converging in ~4
  iterations vs 10+ for weaker models. **This loop is best-in-class on the web
  and documented-weakest on native desktop** (noisy/absent accessibility trees,
  100–500 ms state staleness). **[doc/claim — community + TechRxiv]**
- **Layout/shell assembly** — collapsible panel stacks, slider rows, filmstrips,
  nav/inspector shells (v0's documented sweet spot). The Lightroom-style develop
  panel *shell* falls here. **[verified — v0 reviews]**
- **Integrating off-the-shelf complex widgets** it could not originate — e.g.
  `react-timeline-editor`, Theatre.js, Twick for the keyframe timeline/curve
  editor. **[verified repos]**

**Where Claude struggles (do not rely on it):**
- **Originating a novel visual identity.** Anthropic's own Frontend Design plugin
  names the failure: unguided output converges on "generic system fonts,
  predictable purple gradients, cookie-cutter components" because "the model
  predicts tokens based on statistical patterns in its training data." **Taste
  must be supplied, not requested.** **[verified — claude.com/plugins/frontend-design]**
- **Colour fidelity from a reference image** — "Claude gets background and text
  colors wrong quite often." For a colour-critical tool, panel palettes must come
  from **explicit tokens**, never inferred. **[verified — screenshot-to-code eval]**
- **Bespoke direct-manipulation canvas widgets** — a custom curve editor with
  hit-testing/draggable control points, a deflicker/ramp graph editor. This is
  the **highest-risk UI surface** and the reason to *integrate* a timeline lib,
  not hand-roll one. **[doc/claim]**
- **Motion "feel" and the last-20% pixel polish** — the widely-cited "70%
  problem"; v0 "hits an upper limit of how much it can adjust things." Lightroom/
  Linear-grade density and alignment live entirely in that fraction. **Budget
  human-steered iteration for it.** **[verified/claim — Addy Osmani; v0 reviews]**

**The decisive wedge (why this pushes to web).** The single biggest documented
lever for Claude-built class-leading UI — Anthropic's **Frontend Design plugin**
(and `web-artifacts-builder`, v0, screenshot-to-code, Artifacts) — is
**exclusively web/HTML/CSS** (Tailwind, CSS variables, the Motion library). It
makes **zero** reference to Qt/QML, nuklear, or Vulkan UIs. Every Anthropic
"good design" success story is a React/Vite/Tailwind web app. **So for the
*chrome*, a "mostly Claude" build that must "approach Lightroom aesthetics" is
structurally advantaged on web and disadvantaged on native** — this is the
**strongest argument for Path W.** It is *not* decisive on its own, because it
weighs only the chrome; the opposing force (the viewport is the product, and the
web viewport bridge is what Claude builds *worst*) is weighed against it in §5.2.
**[verified — Anthropic SKILL.md + harness-design blog]**

**The concrete workflow (how you actually get Lightroom-class web UI from Claude):**
1. **Lock a design-token contract FIRST** — semantic tokens (colour roles,
   spacing, radius, type scale, motion) as a strict in-repo contract (shadcn CSS
   vars / Tailwind `@theme`; Linear's discipline: ~3 LCH tokens). This converts
   "originate taste" (fails) into "execute against tokens" (excels) and kills the
   generic-default and drifting-spacing failure modes. **[verified — Linear
   redesign writeup; freedesignmd]**
2. **Inject an aesthetic direction** — load the Frontend Design plugin (forces a
   stated direction before coding; the "permission slip to be interesting").
   **[verified]**
3. **Reference-image-to-code per surface** — feed Lightroom develop panel /
   DaVinci Resolve color page / Capture One / Linear / Figma screenshots for the
   develop panel, timeline, and sequence browser — but supply exact colours via
   tokens (colour inference is the weak spot). **[verified]**
4. **Build components in isolation (Storybook)**; back the few state-heavy widgets
   (combobox, the timeline) with state-machine primitives (Ark UI/XState) where
   LLM-written ad-hoc state bugs cluster. **[verified — ark-ui]**
5. **Close the visual loop (give Claude eyes)** — render in a real browser,
   screenshot, have Claude inspect its own output + console (Playwright/
   Claude-in-Chrome/Claude-Preview). **[doc/claim]**
6. **Screenshot-diff / visual-regression gate in CI** — pin reference shots, diff
   every change; without this gate the model silently re-introduces generic
   patterns and drops a11y/contrast. **[doc]**
7. **Integrate (don't author) the hard widgets** — wire `react-timeline-editor` /
   Theatre.js (studio is **AGPL-3.0 — licence-check against the permissive-core
   stance**) / Twick for the keyframe timeline + interpolation curves; have Claude
   do integration + theming. **[verified repos]**

**Design systems that encode taste (LLM-fit, best→):** **shadcn/ui** (copy-in
source on Radix/Tailwind, the de-facto Claude default, in Artifacts by default —
*highest fit*); **Radix** (headless a11y primitives, huge training presence);
**Base UI** (MUI's Radix successor, v1.0 Dec 2025, forward-looking); **Tailwind**
(constrained tokenised scale = guardrail). Mine **Material 3** for token
*methodology* only — its visual skin reads "Google app," fighting the pro-tool
target. **[verified]**

**Reference targets:** Lightroom Develop module (the explicit bar); DaVinci
Resolve color page (node graph + scrubbable timeline + density — directly
relevant to the keyframe timeline); Capture One (denser pro layout); **Linear**
(high density done tastefully — copy the token discipline); Figma (the multi-
panel creative-app shell archetype). **[verified — Linear/Resolve/Adobe sources]**

---

## 4. Constraints & mitigations (Q4)

**Vulkan-only + MoltenVK (macOS).** *Severity: moderate.* MoltenVK 1.4 (Aug 2025)
is "nearly conformant" Vulkan 1.4; LunarG (Jan 2026): "missing functionality is
minor." vkdt already targets mac (portability-subset, runtime feature fallback,
CI builds a universal `.app` bundling `libMoltenVK.dylib`). Gaps that matter for
*this* pipeline: **cooperative_matrix not supported** (only the kpn-t neural
denoiser uses it — avoid it; it's a leaf); **`shader_atomic_float`** is supported
on current MoltenVK (Metal 3) but **`hdrmerge` (Holy-Grail HDR) uses it — must be
validated on real Metal hardware** or given a non-atomic fallback (vkdt's runtime
check prevents a crash); ray tracing not supported (irrelevant). No published
MoltenVK-vs-native-Metal *compute* benchmark exists — **an evidence gap; budget
empirical M-series validation (~1–2 weeks).** Forward option: LunarG/Google
**KosmicKrisp** reached Vulkan 1.3 conformance on Apple Silicon (late-2025) — an
ICD swap with no app-code change later. **[verified — MoltenVK UserGuide, LunarG
Jan-2026 + KosmicKrisp, vkdt config.mk.defaults.osx + nightly.yml]**

**Windows (premise corrected).** *Severity: moderate.* vkdt **already builds a
pre-alpha `vkdt.exe`** (MSYS2/UCRT64, CI `WindowsRawler`). On Windows you use
**native conformant ICDs — no MoltenVK risk at all.** The real costs: the
hand-rolled Makefile assumes a POSIX shell/symlinks → **migrate to CMake** (the
single biggest structural win, ~1 week, independent of the UI), and the symlink-
based DB → sidecar index. Most #103 breakage lives in the nuklear/GLFW/symlink-DB
layer that a fork **replaces anyway**, so **replacing the UI reduces net Windows
effort.** Realistic: ~2–4 weeks to a solid Windows build of the develop+render
core. **[verified — Makefile w64, _WIN32 shims, nightly.yml, issue #103]**

**Linux.** *Severity: minor.* Primary, best-supported target (default config,
AppImage + Nix flake, conformant native drivers). Only universal caveat: a
Vulkan-capable GPU is a hard runtime requirement, and upstream CI can't execute
the pipeline (GitHub runners lack a usable GPU) — **real-GPU integration tests
are the developer's responsibility on all three OSes.** **[verified — nightly.yml,
flake.nix]**

**One-person upstream (bus factor).** *Severity: moderate, well-mitigated.*
Hanika authored **97.3%** of commits (5,105; next contributor: 26); co-creator
houz has 1 commit; external PRs are often closed-not-merged (opinionated
gatekeeper) → **upstreaming our changes is unlikely.** **But:** cadence is healthy
(2024 peak 1,037; 2025 = 754; nightly tagged 3 days before this analysis), 1.0.0
shipped Dec 2025, and Hanika is the **darktable founder + SIGGRAPH rendering
researcher** (strong design-quality signal). Crucially, the most cadence-
sensitive dependency — **new-camera RAW support — flows through rawspeed
(darktable-org, multi-contributor) / rawloader (Rust, vendored), NOT through
Hanika's bus factor.** *Mitigation:* **snapshot-fork** at 1.0.0/a vetted nightly;
own the ~1,254 LOC qvk + engine surface in-house; bump rawspeed/rawloader on your
own schedule; keep a thin patch-set for opportunistic engine cherry-picks.
**[verified — GitHub contributors/commits/releases APIs, i-raw/flat.mk]**

**Licence audit (BSD-2 core + isolated GPL) for a possibly-commercial product.**
*Severity: moderate, bounded — feasible.* The engine **architecture** (graph,
modules, connectors, scheduling, keyframes, allocation: `graph.c`, `module.h`,
`node.h`, `connector.h`, `params.h`, `modules/api.h`) carries **no per-file
header → root LICENCE = BSD-2.** The GPL is concentrated and replaceable:

| File(s) | Licence | Role | Replace effort | Note |
|---|---|---|---|---|
| `src/qvk/qvk.c`+`qvk.h` | GPLv2-or-later (Q2RTX) | **foundational** | **substantial** | ~777 LOC Vulkan bootstrap; pervasively referenced via global `qvk` struct. **The one big item** — clean-room reimplement preserving the `qvk.h` interface (or swap for MIT volk + vk-bootstrap + VMA). |
| `src/qvk/qvk_util.c`/`.h` | GPLv2+ | foundational | bounded | ~477 LOC mechanical Vulkan helpers. |
| `src/core/gaussian_elimination.h` | GPLv3+ (darktable) | core-helper | trivial | Textbook solver; rewrite in minutes. |
| `src/core/sig.h` | GPLv3+ (darktable) | core-helper | trivial | Signal-handler boilerplate. |
| `src/pipe/modules/shared/oetf.glsl` | GPLv3 (OpenDRT) | **core-helper (NOT a leaf)** | bounded | **`#include`d by the core `colour` module + filmcurv** — taints otherwise-BSD modules. Standard transfer curves; **rewrite your own (you have clean-room colour science anyway).** |
| `i-raw/exif.h`, `dng_opcode_decode.c` | GPLv3+ (darktable) | core-helper | bounded | EXIF/DNG-opcode helpers **actually used** by the raw path; swap for exiv2/libexif (the decoder itself, rawloader, is WTFPL). |
| `OpenDRT/`, `rt/`, `i-mlv/`, `i-jpg`/`o-jpg` (ICC snippet), `hdrmerge` | GPLv2/v3 | leaf-module | drop/bounded | All **optional for a timelapse tool** — omitting the module drops the taint. |

**Net:** **1 substantial item (rewrite qvk) + a short list of bounded rewrites
(exif, OETF curves, optional DNG opcodes) + delete the irrelevant GPL leaves. No
GPL is woven into the engine architecture.** A closed-source/commercial fork is
feasible. **[verified — per-file headers, LICENCE, Cargo.toml]**

---

## 5. Verdict + phased plan (Q5)

### 5.1 Is "fork vkdt engine + LLM-built modern UI" viable and best?

**Yes, viable; yes, best — versus the alternatives:**

- **vs from-scratch (Qt/Vulkan or Rust/wgpu):** from-scratch has **no interop
  advantage** — wgpu's Vulkan-image import to a web UI is just as unstable
  (Skeptic 3), and a from-scratch Qt/Vulkan app faces the *same* tier-(a)/(b)
  question Qt-over-vkdt does — while **throwing away vkdt's banked GPU develop
  pipeline + runtime + raw ingestion + module system** (the ~3-year-solo /
  12–18-mo-team core). Forking the engine strictly dominates. **[verified
  reasoning]**
- **vs cobble (darktable + dtlapse + ffmpeg):** stays rejected (dead keystone,
  GPL throughout, fails the UX bar). **[prior doc]**

The only real question is **the UI fork-in-the-road**, and it is decided by the
user's own hard requirements pulling in opposite directions: *exceed LRT /
approach Lightroom* **and** *built mostly by Claude*. The naïve reading ("Claude's
aesthetics are best on web → go web") is incomplete, because it weighs only the
chrome and ignores that the **viewport is the product** for a raw develop tool —
and the viewport is exactly where the web path puts Claude's *weakest* work.

### 5.2 The recommendation — two viable paths, one empirical tiebreaker

This is genuinely close. State it symmetrically:

- **Path W — vkdt engine + web UI (Electron preferred, or Tauri) + React/Tailwind/
  shadcn; viewport via GPU-proxy readback backed by a scrub-proxy cache.** You keep
  vkdt's zero-copy *compute* (the develop pipeline stays entirely on GPU) and give
  up only the zero-copy *display*. **Strength:** the chrome (~90% of the UI —
  panels, filmstrip, develop sliders, on-image crop/mask/curve overlays) is built
  in the one regime where Claude reaches Lightroom-class polish (Frontend Design
  plugin, shadcn, v0, screenshot-to-code, the browser screenshot loop). **Weakness:
  the viewport bridge — external-memory/semaphore export (if tier-b) or readback/
  IPC/proxy-cache (tier-c) — is bespoke native glue, the single thing an LLM builds
  *worst*, and it is the most important widget in the app.**
- **Path Q — vkdt engine + Qt/QML.** **Strength:** the viewport is **trivial,
  documented wiring** (`QQuickGraphicsDevice::fromDeviceObjects` +
  `QRhiTexture::createFrom` + `QSGRenderNode`) that **preserves the zero-copy
  preview** (tier-a Linux/Win, tier-b mac) — the entire reason to fork vkdt — and
  Qt's aesthetic ceiling is proven by shipping pro tools. **Weakness:** the chrome
  is built without *any* of Claude's web-only aesthetic tooling; QML lands at
  ~75–86% functional correctness with a Claude-tested QML skill, but the
  taste/polish flywheel is web-only, so chrome polish is more human-steered.

**In one line: Path W concentrates the LLM's difficulty in the critical viewport;
Path Q concentrates it in the chrome.** Under "mostly Claude," that is a real
trade, not an obvious win for either side — which is why it must be decided by
measurement, not preference.

**The tiebreaker (build it in Phase 1, before committing the stack):** *does a
high-bit-depth proxy-readback viewport give acceptable **interactive** latency?*
Note this is **two** latency-sensitive paths, not one — and the prior "a timelapse
tool needs a cache anyway" argument only covers the second:
1. **Slider-drag feedback on the current frame** — Lightroom's signature; per-drag
   GPU→CPU readback at full res hurts here. Mitigation to test: **proxy-resolution
   readback *during* the drag, full-res on release.**
2. **Whole-sequence scrub** — covered by a pre-rendered GPU-resident proxy cache
   (needed regardless, since you can't demosaic 24 MP at scrub framerate even with
   zero-copy).

**If both feel right at the target quality → Path W** (start tier-c on Tauri or
Electron; keep Electron's experimental `sharedTexture` as the tier-b upgrade path —
Tauri has none). **If slider feedback or scrub stays sticky despite proxies →
Path Q**, accepting the chrome-polish hit. Decision ladder: **tier-c Tauri →
tier-b Electron → tier-a/b Qt.**

### 5.3 Rough effort

Calibrating against the prior v1.0 estimate (~36–40 eng-mo solo from scratch): a
vkdt-engine fork still removes the GPU-develop-pipeline + pipeline-runtime + raw-
ingest + module-system months (the bulk of the highest-risk third). The web path
**adds back** a new line item the prior estimate didn't have — the **GPU→CPU
proxy-readback/IPC viewport bridge + scrub-proxy cache** (~1.5–3 eng-mo, and the
LLM-hostile part of the build) — and a **de-GPL pass** (~1–1.5 eng-mo, dominated
by the qvk rewrite). The web UI chrome is *faster* with Claude than a native UI
would be. **Net intuition: the fork keeps roughly the prior doc's ~25–35% saving
on the engine, minus the new bridge cost; MVP plausibly ~9–13 eng-mo solo / ~4–6
mo with 2–3 engineers.** Ranges, not points — the bridge latency spike and the
MoltenVK compute validation are the two estimate-movers. **[claim — reasoned]**

### 5.4 Phased plan

**Phases 0–1 are common to both paths and contain the tiebreaker; Phases 2–3 are
written for Path W (web) — under Path Q they become "build the QML shell + adopt
vkdt's `VkDevice` via `fromDeviceObjects`" and "build/integrate the QML timeline/
curve widgets," and the viewport bridge of Phase 1 collapses to documented wiring.**

- **Phase 0 — Licence-clean snapshot fork (~1–1.5 mo).** Fork at 1.0.0. Rewrite
  qvk (volk + vk-bootstrap + VMA, MIT) preserving the `qvk.h` interface; clean-
  room `gaussian_elimination`/`sig`/`oetf` curves; swap `exif.h` for exiv2; drop
  GPL leaf modules; pin rawloader. **Migrate Makefile → CMake** (unlocks Windows).
- **Phase 1 — Headless engine harness + the viewport-latency spike (~1.5–2.5 mo).**
  Drive `dt_graph_run` in an interactive loop; build the GPU-render → downscaled-
  proxy readback → local-surface transport; **measure BOTH interactive paths at
  4K on all three OSes (native Vulkan Win/Linux, MoltenVK mac): (1) slider-drag
  feedback on one frame (test proxy-during-drag, full-res-on-release) and (2)
  proxy scrub.** *This is the go/no-go for Path W vs Path Q.* Validate `hdrmerge`
  atomic-float on real Apple Silicon.
- **Phase 2 — Web UI shell (~2–3 mo).** Tauri + React/Tailwind/shadcn. **Lock
  design tokens first** (Linear-discipline LCH); Frontend Design plugin; build
  develop panel + filmstrip browser + viewport canvas against Lightroom/Resolve/
  Capture One references; stand up the Storybook + screenshot-diff regression gate.
- **Phase 3 — The hard widgets (~1.5–2.5 mo).** **Integrate** a timeline/curve lib
  (`react-timeline-editor` / Theatre.js [AGPL — check] / Twick) for the keyframe
  timeline + editable interpolation curves; build deflicker/ramp **graph** views.
- **Phase 4 — Clean-room colour-science + ramp + deflicker nodes (~2–3 mo).** Add
  as vkdt pure-shader modules (copy `exposure/`): linearise → gamut → tone → OETF;
  an `autoexp`-style Holy-Grail ramp; a feedback-connector deflicker.
- **Phase 5 — Proxy/cache for smooth scrub + export (~1.5–2.5 mo).** Multi-tier
  GPU-resident proxy cache; high-bit EXR/TIFF export (reuse the sister repo's
  format work).
- **Phase 6 — Polish + packaging (ongoing).** The human-steered last-20%; 3-OS
  packaging, code-signing/notarization.

### 5.5 Decision triggers (kill/switch criteria)

- **Phase-1 latency spike fails** (slider-drag feedback or proxy scrub
  unacceptable even with proxies / full-res-on-release) → take **Path Q (Qt/QML)**
  for tier-a/b zero-copy, accepting the chrome-polish hit.
- **MoltenVK compute validation fails** for the develop graph on Apple Silicon →
  evaluate KosmicKrisp ICD, or descope macOS to v1.x.
- **qvk rewrite balloons** beyond ~1.5 mo → reconsider whether an open-source
  (BSD-2 + GPL-acknowledged) release defers the de-GPL work until a commercial
  decision is actually made.

---

## Sources

**vkdt source (fresh clone, `github.com/hanatos/vkdt` @ nightly):** `src/Makefile`
(two-binary proof); `src/cli/main.c`; `src/pipe/{graph,module,node,connector,
params,global}.h`, `graph-export.h`, `graph-run-nodes-allocate.h`; `src/qvk/qvk.{c,h}`
(+ GPLv2 header); `src/gui/render_darkroom.c`, `nuklear_glfw_vulkan.h`;
`src/core/{gaussian_elimination,sig}.h`; `src/pipe/modules/{exposure,shared/oetf.glsl,
i-raw,OpenDRT,i-mlv,o-jpg}/…`; `LICENCE` (BSD-2); `bin/config.mk.defaults.{osx,w64}`;
`.github/workflows/nightly.yml`; issue #103. **[verified]**

**Interop / UI stacks:** Qt docs — QVulkanInstance, QQuickGraphicsDevice
(`fromDeviceObjects`), QRhiTexture (`createFrom`), QSGRenderNode; Qt blog "Qt Quick
on Vulkan/Metal/Direct3D pt.2"; Qt "QML Coding Skill" blog + TheQtCompanyRnD/
agent-skills. Tauri #8246/#11944/#13740; WebView2Feedback #547; qwook/tauri-plugin-
steam-overlay. Electron #46779, PR #46811, shared-texture docs. W3C WebGPU +
MDN `importExternalTexture`; gfx-rs/wgpu #2320/#3145; Dawn `shared_texture_memory`
docs; ginokent 2026 wgpu-video writeup; wcandillon/react-native-webgpu. Khronos
`VK_EXT_metal_objects` proposal; MoltenVK #2151/#1631/#2209/#962/#830 + UserGuide;
LunarG "State of Vulkan on Apple (Jan 2026)" + KosmicKrisp. Flutter
docs/perf/impeller, flutter#117937/#137639/#144613/#183495, pub.dev/minigpu_view,
flutter.dev/showcase/rive. **[verified/doc/claim as tagged inline]**

**LLM-UI quality:** Anthropic Frontend Design plugin
(`claude.com/plugins/frontend-design` + `claude-code/.../frontend-design/SKILL.md`);
Anthropic "harness design for long-running apps" blog; Claude Artifacts/
web-artifacts-builder; abi/screenshot-to-code eval; v0.app docs; Victor Dibia
"How good is Claude Design"; shadcn/ui, Radix, Base UI, Tailwind, Ark UI; Linear
redesign writeup; DaVinci Resolve color page; Adobe Lightroom GPU FAQ; freedesignmd.
**[verified/doc/claim as tagged inline]**

**Upstream health:** GitHub contributors/commits/releases/pulls/issues APIs for
`hanatos/vkdt`; `darktable-org/rawspeed`; Linux Magazine + librearts (Hanika/
darktable); SIGGRAPH history. **[verified]**
