# Standalone timelapse authoring+render app — build-vs-not research

> ⛔ **DECISION (2026-05-29): standalone app ON HOLD / NO-GO for now.** An
> adversarial sanity-check of the chosen path concluded the build is not viable as
> staffed (a non-engineer/non-designer lead + Claude) — it needs a Vulkan/
> native-systems engineer **and/or** a design originator first. Governance:
> [`v09-standalone-repo-plan.md`](v09-standalone-repo-plan.md); reasoning:
> [`v09-vkdt-fork-ui-strategy.md`](v09-vkdt-fork-ui-strategy.md) §6. Below is
> research input, **not a build authorization.**

**Status:** Research / decision input, 2026-05-28. Scopes an OPEN-SOURCE,
Adobe-free, STANDALONE timelapse authoring + render application — a full
LRTimelapse replacement that ingests RAW sequences, authors keyframed develop
(+ motion), runs Holy-Grail ramp + visual deflicker, and renders high-bit-depth
recoverable output with its OWN clean-room colour science. UX bar: exceed
LRTimelapse, approach Lightroom in aesthetics/speed.

Evidence tags: **[verified]** = confirmed against a primary source (vendor doc,
source code, official docs); **[claim]** = community/forum/inference/single
source. Two brief assumptions were CORRECTED by primary sources and are flagged
inline (vkdt licence+GUI; darktable origin year).

---

## Settled constraints (given; not re-litigated)

- Own clean-room colour science; NO represent-all / no ACR pixel-match (Dehaze
  patent-encumbered, PV2012 adaptive ops closed). The sister CPU repo's <1 ΔE
  DNG/DCP core is a **validated algorithmic spec, not reusable code** (CPU
  NumPy; GPU-shader port is a rewrite).
- Adobe-free is a hard principle. **Upside:** removes the Linux limitation of
  the current CLI (no Adobe DNG Converter on Linux) → clean cross-platform.

---

## Part 1 — UX/UI critique + target

### What dates/limits LRTimelapse

- **Develop is outsourced to Lightroom.** The high-quality path *is* the
  Lightroom path: LRT's `LRTExport` plugin drives LR Classic to emit 16-bit
  TIFF. [verified — lrtimelapse.com/workflow]
- **Its own "internal" develop is an 8-bit dead end.** LRT docs: *"the internal
  export will always create 8 bit sRGB intermediary JPG files… The quality will
  be inferior to the quality provided by the Lightroom Export."* Internal
  editing *"cannot fully replace the power of Lightroom, but for simple edits it
  works fine."* [verified — lrtimelapse.com/workflow/internal-workflow]
- **The native UI is a filmstrip + keyframe table + a few graphs.** You set
  keyframe count with a slider; LRT marks frames; everything between is
  interpolated. Preview build is **low-res 8-bit JPEG Rec.709**, so gradients
  can band. [verified — LRT docs + Bryan Snider review]
- Net: two-app workflow (LRT ↔ Lightroom round-trip via XMP), a develop surface
  that is either Lightroom-or-nothing, and a preview that does not represent
  final colour. The keyframe table is functional but spreadsheet-like, not a
  direct-manipulation timeline.

### What makes Lightroom develop strong/fast

- **GPU-accelerated interactive preview.** "Use GPU for image processing" speeds
  Develop calculations so slider drags update fast; GPU also drives Library/
  Loupe/Filmstrip and AI masking (Select Subject/Sky), Denoise, Lens Blur.
  [verified — helpx.adobe.com/lightroom-classic/kb/lightroom-gpu-faq]
- Strong develop-panel ergonomics, before/after, local/AI masking, fast
  filmstrip navigation. The interaction is **direct + immediate** — the thing
  LRT's table model lacks.

### Key UI surfaces a superior timelapse tool needs

1. **RAW sequence / filmstrip browser** — thumbnail strip + grid, fast scrub,
   keyframe markers inline.
2. **Keyframe TIMELINE with interpolation curves** — a real timeline (not a
   table): per-parameter tracks, draggable keyframes, editable interpolation
   (linear/Bézier/Catmull-Rom) shown as curves. This is the single biggest UX
   win over LRT.
3. **Develop panel** — Lightroom-grade ergonomics on the clean-room ops the tool
   actually owns (exposure, tone curve, WB, sat/vibrance/contrast, HSL, masks).
4. **Holy-Grail ramp + deflicker VISUALIZATION** — the ramp/deflicker math made
   visible as graphs (luminance over the sequence, applied correction curve,
   residual flicker). LRT already does a primitive version: "Visual Deflicker"
   draws an orange correction curve in the viewer driven by a user-dragged
   sky rectangle. [verified — PetaPixel/Matjoez/LRT]
5. **Real-time GPU preview + whole-sequence scrubbing** — scrub the *developed,
   deflickered* sequence at framerate, not a low-res JPEG proxy of stills.
6. **Masking** — at minimum linear/radial/brush; whole-sequence-consistent.
7. **Motion (optional)** — keyframed pan/zoom/crop over the sequence, and
   stabilization/deshake; lives on the same timeline as develop keyframes.

### Interaction model that beats LRT

One app, no Lightroom round-trip. Direct-manipulation timeline replaces the
keyframe table. Edits to a keyframe propagate live down the interpolated
sequence with a GPU preview that shows *final* colour (high-bit-depth, correct
gamut) — eliminating LRT's "preview ≠ output" gap. Deflicker/ramp are
first-class graph editors, not opaque buttons.

---

## Part 2 — Tech stack

### GUI frameworks

| Framework | Aesthetic ceiling | Cross-platform | Licence | Verdict for this app |
|---|---|---|---|---|
| **Qt/QML** | High — can approach Lightroom (GPU-composited scene graph, custom styling) | Win/mac/Linux first-class | **LGPLv3** (keep app source closed if you dynamically link + comply) or commercial | **Top pick.** Proven for creative raw apps (Filmulator). [verified — qt.io licensing; Filmulator README Qt 5.15+] |
| **Tauri/web (React)** | High — full CSS/HTML control | Win/mac/Linux (system WebView) | MIT/Apache | **Viable.** Best if web-dev velocity matters; **IPC boundary is not type-safe**, and GPU compute lives in a separate native sidecar (the WebView won't run your raw pipeline). [verified — boringcactus survey; libhunt] |
| **Rust egui** | **Low — "developer tools and debug overlays rather than polished consumer applications."** Immediate-mode, limited theming/layout, no native IME. | Excellent | MIT/Apache | **Fails the aesthetic bar.** Great for the *debug HUD*, not the product UI. [verified — boringcactus; libhunt] |
| **Dear ImGui** | Low — "ugly as sin"; a debug/tools UI by design | Excellent | MIT | **Fails the aesthetic bar** (this is what vkdt-adjacent tools look like; see Part 4b). [claim — libhunt community] |
| **JUCE** | Medium; audio-plugin heritage, not photo-UI ergonomics | Win/mac/Linux | GPL or commercial | Wrong domain; no advantage here. |
| **Slint** | Medium-High, improving fast; DSL-driven | Good | GPL/royalty/commercial | Promising but younger ecosystem; weaker raw-app precedent than Qt. [verified — boringcactus] |
| **Native SwiftUI/Cocoa** | High on macOS | **macOS-only** | — | Disqualified by cross-platform requirement. |

**Aesthetic discriminator (load-bearing):** the "approach Lightroom" bar
**disqualifies GTK** (darktable/RawTherapee look dated) **and immediate-mode**
(egui/ImGui look like tools). The realistic routes to a Lightroom-class look are
**Qt/QML** or **web/Tauri**. Don't let raw-performance arguments smuggle GTK/
ImGui past the aesthetic gate.

### GPU compute/render backends

| Backend | Cross-platform | Notes |
|---|---|---|
| **wgpu (Rust)** | **One API over Vulkan/Metal/D3D12/GL + WebGPU.** | "Explicit control comparable to Vulkan/Metal without footguns (manual sync/allocators)." MIT/Apache. WebGPU spec still "Working Draft" so some churn. **Best cross-platform/effort tradeoff for a new app.** [verified — github.com/gfx-rs/wgpu; wgpu.rs] |
| **Vulkan** | Win/Linux/Android native; macOS via **MoltenVK** | Max control + performance; verbose; what vkdt uses. macOS is translated, not native. [verified — Khronos; vkdt] |
| **Metal** | **Apple-only** | Best on Apple Silicon (unified memory). Not portable. [verified] |
| **OpenGL** | Everywhere but **deprecated on macOS** | Legacy; avoid for new compute-heavy work. |
| **OpenCL** | Broad | What darktable uses for compute; aging, uneven driver quality; not a renderer. [verified — darktable docs] |
| **CUDA** | **NVIDIA-only** | Disqualified by cross-platform + AMD/Apple users. |

**Pick:** **wgpu** if the stack is Rust (single backend, all three OSes incl.
clean macOS via Metal); **Vulkan + MoltenVK** if C++/Qt and you want maximum
control and to mirror vkdt's proven design. Either clears cross-platform; both
beat the OpenCL/OpenGL incumbents.

### Licence propagation (stack constraint, not a footnote)

- **darktable: GPL-3.0-or-later. RawTherapee: GPLv3.** [verified — Wikipedia/RT
  site] Forking/linking either makes the product GPL. Referencing their
  algorithms (clean-room, as the sister repo already does) is fine.
- **vkdt: 2-clause BSD** for its own code (bundled deps differ — rawspeed LGPLv2,
  ffmpeg LGPLv2, nuklear public-domain). [verified — vkdt README] **This is
  permissive** — see Part 4b; it overturns the brief's "vkdt is GPL" assumption.
- **Qt: LGPLv3** lets a closed-source app ship if it dynamically links and meets
  LGPL terms; commercial licence removes those obligations. [verified — qt.io]
- For an **OSS-now-maybe-commercial-later** tool: a permissive core (Qt-LGPL or
  Rust/wgpu MIT/Apache, + BSD vkdt if forked) keeps options open; a GPL
  fork (darktable/RT) closes the commercial door.

---

## Part 3 — Engineering breakdown + effort (engineer-months)

### Comparables (real dev histories) [verified]

- **darktable** — first release **April 2009** (NB: the "2012" copyright string
  is when OpenCL landed, not project start [verified — Wikipedia]); Hanika +
  large contributor base; GTK + OpenCL; **five distinct pixelpipe types** (full,
  preview, second-window, export, thumbnail) — i.e. multi-pipeline preview/cache
  is a first-class subsystem. 15+ years, many engineers. [verified — Wikipedia;
  DeepWiki pixelpipe]
- **RawTherapee** — started **2004** solo (Gábor Horváth); OSS 2010; **~28
  developers in a 2-year window (2022)**; **20+ years**, still CPU-only. Shows
  how long even a CPU raw engine takes to mature with a team. [verified — RT
  site; Wikipedia]
- **Filmulator** — **solo** project (CarVac), Qt 5.15+/QML, LibRaw, CPU film-sim
  pipeline. Demonstrates one engineer *can* ship a focused raw editor — with a
  deliberately narrow feature set. [verified — README; GPU not claimed → CPU by
  absence, [claim]]
- **vkdt** — essentially **Hanika solo**, **5,249 commits** to reach **1.0.0
  (Dec 2025)**; Vulkan/GLSL node graph. The closest existing thing to the
  target; see Part 4b. [verified — GitHub]

Read-through: a *focused* raw editor is a solo-feasible multi-year effort
(Filmulator, early RT, vkdt); a *broad* one is a decade+ team effort (darktable,
mature RT). This app is **narrow in scope (timelapse) but deep in hard
subsystems (GPU pipeline, sequence cache, colour science)**.

### MVP vs v1.0 (1 senior engineer; ranges, not points)

| Component | MVP | v1.0 | Hard parts / notes |
|---|---:|---:|---|
| RAW decode/ingest (LibRaw) | 1.0 | 2.0 | LibRaw is the easy 80%; per-camera WhiteLevel/black/CFA edge-cases + sequence metadata are the long tail. [verified — Filmulator/vkdt both lean on LibRaw/rawspeed] |
| **GPU develop pipeline** | 3.0 | 8.0 | **Hard.** Rewrite the validated NumPy colour science as GPU compute shaders (demosaic → WB → matrix → tone → gamut). The spec exists; the GPU impl + numerical parity does not. |
| **Real-time preview / scrubbing** | 3.0 | 7.0 | **Hardest, and under-budgeted by intuition.** The cost sink is the **proxy/cache architecture**: you cannot decode+demosaic 24MP RAW at scrub framerate, so you need pre-decoded, GPU-resident proxies + a multi-tier cache. darktable needing *five* pipeline types is the evidence this is a subsystem, not a feature. [verified — DeepWiki] |
| Keyframe model + interpolation | 1.0 | 2.5 | Data model is easy; the **timeline UI with editable curves** is most of the cost. dtlapse proves the math (linear/quad/cubic + Savitzky-Golay) is small. [verified — dtlapse] |
| Deflicker | 0.5 | 1.5 | Algorithm is modest (weighted-luminance, region-based "visual" variant); robustness across sequences is the work. [verified — LRT/PetaPixel] |
| Holy-Grail ramp solver | 0.5 | 1.5 | Wegner-style histogram-weighted luminance ramp; detection of exposure jumps. [verified — Dynamic Perception/foolography] |
| Export (EXR/TIFF high-bit) | 1.0 | 2.0 | Mostly solved by the sister repo's format work (OpenEXR/tifffile); re-impl in app language. |
| Project/catalog management | 1.0 | 3.0 | DB, sidecars, undo/history (darktable's "history stack" is non-trivial). [verified — darktable docs] |
| Cross-platform packaging | 1.0 | 3.0 | 3 OSes, GPU driver matrix, code-signing/notarization (mac), installers. Real, recurring cost. |
| UI shell / develop panels / polish | 2.0 | 6.0 | The "approach Lightroom" bar lives here — most of the *aesthetic* budget. |
| **Total (1 eng)** | **~14–16 eng-mo** | **~36–40 eng-mo** | MVP ≈ **12–16 months solo / ~6 with 2–3 eng**; v1.0 ≈ **3+ years solo / ~12–18 mo with a small team.** |

**Honesty checks baked in:** (i) the colour-science repo is **not** banked as
done — it's a spec feeding a from-scratch GPU rewrite; (ii) preview is dominated
by **cache/proxy**, not the develop shader. The two GPU-adjacent rows (develop
pipeline + preview/cache) are ~**40% of v1.0** and carry the schedule risk.

---

## Part 4 — Alternative paths (genuine build-vs-not options)

### 4a — Cobble: darktable + dtlapse + ffmpeg

- **dtlapse is dormant.** Last release **1.0.1, Aug 2020** (~6 years stale);
  author Jochen Keil; **GPLv3+**; Python ≥3 + scipy; operates on darktable XMP
  sidecars (keyframe interpolation: linear/quad/cubic + Savitzky-Golay smoothing
  + a `--plot` curve view; supports ~14 dt modules incl. exposure/temperature/
  filmicrgb). [verified — PyPI]
- **Workflow:** edit keyframes in **darktable** (GTK, dated UI) → tag them → run
  **dtlapse** CLI to interpolate XMPs → batch-export via darktable-cli →
  **ffmpeg** to encode. Three tools, two of them CLI, no integrated timeline, no
  live whole-sequence GPU preview, no in-app deflicker visualization.
- **Verdict: hobbyist patchwork, not a product.** It works for a technical user
  today, but (1) the keystone (dtlapse) is unmaintained, (2) the UX is three
  disjoint tools with darktable's dated GTK front, (3) it **fails the "exceed
  LRT / approach Lightroom" bar outright** — there is no unified authoring app,
  no real timeline, no live developed-sequence scrub. **GPL throughout** (dt +
  dtlapse) closes any commercial door. As a *product* it is not viable; as a
  *personal pipeline* it is serviceable.

### 4b — Fork/extend vkdt (a first-class fifth path)

This is the closest existing thing to the target and deserves first-class
weight. Two brief assumptions were **wrong** and matter:

- **Architecture [verified — README]:** generic **node graph (DAG)**, multi-
  input/multi-output, **all processing in GLSL/Vulkan**, GPU-resident textures
  the GUI can display *while still on GPU* (exactly the real-time-preview
  property Part 3 calls hardest — vkdt already has it). Explicit support for
  **timelapses, raw video (Magic Lantern MLV, MotionCam), animation/iteration
  via feedback connectors**. Built by **darktable's own author** for raw stills
  **and video**.
- **Maturity [verified — GitHub]:** **5,249 commits**, **1.0.0 Dec 2025**, ~534
  stars, active nightly CI. Not the "experimental toy" the framing implied —
  it's a 1.0 with a reduced (vs darktable) but real feature set.
- **Platform [verified — README]:** native support is **Linux + macOS (Intel &
  Apple Silicon, via brew + LunarG Vulkan SDK)**. **Windows is NOT mentioned** —
  see the Windows risk below.
- **GUI [verified — source tree]:** uses **nuklear (public-domain immediate-
  mode)** — confirmed by `src/gui/nuklear.h` + `src/gui/nuklear_glfw_vulkan.h`;
  **no Dear ImGui in the tree**, correcting the brief's assumption.
  Aesthetically this lands the same place as the Part-2 immediate-mode verdict:
  a *tool* look, **below the Lightroom bar**. A fork would likely **replace the
  GUI layer** (Qt/QML or web) while keeping the Vulkan node-graph engine —
  feasible because engine and GUI are decoupled (GUI just displays GPU textures).
- **Licence [verified — GitHub licensee = BSD-2-Clause + README]:** vkdt's own
  code is **2-clause BSD** (permissive), BUT the README warns it
  *"contain[s] a bit of viral GPLv3… handle with care if you're afraid of the
  GPL"* (plus LGPL/public-domain bundled deps). So: **predominantly permissive,
  with isolated GPLv3 files to excise/replace in a closed-source fork.** This
  still **overturns the brief's "vkdt is GPL" assumption** and is far better
  than darktable/RT's wholesale GPL — but it is *not* unqualified BSD; a
  commercial fork must audit and strip the GPLv3 bits.

**Verdict:** **the strongest "don't fully build" option.** Forking vkdt gives
you, for free, the two highest-risk subsystems from Part 3 — the **GPU node-
graph develop pipeline** and **GPU-resident real-time preview** — plus native
timelapse/raw-video intent and a permissive licence. The work becomes: (1) a
**new Lightroom-class GUI** (Qt/QML or web) over the engine, (2) your **clean-
room colour-science nodes**, (3) the **keyframe-timeline + deflicker/ramp**
authoring layer. That plausibly removes ~30–40% of the v1.0 build (the
preview/cache + pipeline-runtime months) — *if* vkdt's engine is extensible
enough and you accept tracking a one-maintainer upstream. Risks: (1) **no
official Windows support** — a vkdt fork inherits a Windows-porting cost
(Vulkan SDK + nuklear/GLFW are portable, but it's unproven and eats into the
"30–40% saved"); this directly tensions the app's cross-platform requirement.
(2) bus-factor of one upstream. (3) **GLSL/Vulkan-only** (no wgpu portability).
(4) the GUI-replacement effort is itself non-trivial. (5) GPLv3-tainted source
files must be audited/stripped for a commercial fork.

---

## Recommendation

1. **Don't cobble (4a).** dtlapse is dead, the UX can't clear the bar, and it's
   GPL end-to-end. Fine as a personal pipeline, not a product.
2. **Seriously evaluate forking vkdt (4b)** before committing to from-scratch.
   It already solves the two hardest, longest subsystems (GPU node-graph
   pipeline + GPU-resident live preview), is **predominantly BSD-2** (audit out
   a few GPLv3 files for a commercial fork), runs on **mac+Linux (no official
   Windows)**, and was built by darktable's author *for timelapse/raw-video*.
   The honest from-scratch v1.0 is **~3 years solo / ~12–18 months for a small
   team**; a vkdt fork could cut the highest-risk third of that — at the cost of
   a one-person upstream, Vulkan-only portability, **and a Windows port**.
3. **If building fresh:** **Qt/QML + Vulkan(+MoltenVK)** or **Rust + wgpu**, with
   egui/ImGui used (if at all) only for debug HUDs. Budget the **proxy/cache**
   and **GPU pipeline** as the schedule drivers, not the develop shader.
4. **Either way:** the clean-room colour science is a *spec to re-implement on
   GPU*, not a finished core; and Adobe-free genuinely buys clean Linux support
   the current CLI can't have.

---

## Sources

- LRTimelapse workflow + internal-workflow docs (8-bit JPEG ceiling; filmstrip/
  keyframe model); Bryan Snider LRT 6 review. [verified]
- Adobe Lightroom Classic GPU FAQ (helpx) — GPU in Develop/Library/masking.
  [verified]
- vkdt — github.com/hanatos/vkdt: README + GitHub licensee API (**BSD-2-Clause**,
  README warns of "a bit of viral GPLv3") + source tree (`src/gui/nuklear.h` →
  **nuklear**, no ImGui); node-graph GLSL/Vulkan, timelapse/raw-video, 5,249
  commits, 1.0.0 Dec 2025, **Linux+macOS only (no Windows)**. [verified — source]
- dtlapse — pypi.org/project/dtlapse (1.0.1 Aug 2020, GPLv3+, scipy, dt XMP).
  [verified]
- darktable — Wikipedia (April 2009, Hanika, GTK, OpenCL, GPL-3.0-or-later);
  DeepWiki pixelpipe (five pipeline types). [verified]
- RawTherapee — RT site + Wikipedia (2004 solo start, OSS 2010, ~28 devs/2yr,
  GPLv3, CPU). [verified]
- Filmulator — github.com/CarVac/filmulator-gui README (Qt 5.15+/QML, LibRaw;
  GPU not claimed → CPU [claim]); itsfoss/PetaPixel/DPReview. [verified/claim]
- Rust GUI survey — boringcactus.com 2025 survey; libhunt egui (egui = dev-tools/
  debug, not polished consumer; MIT/Apache; immediate-mode limits). [verified/claim]
- wgpu — github.com/gfx-rs/wgpu + wgpu.rs (one API over Vulkan/Metal/D3D12/GL/
  WebGPU, MIT/Apache). [verified]
- Qt licensing — qt.io (LGPLv3 keeps app source closed if compliant; commercial
  alt). [verified]
- Holy-Grail/deflicker — PetaPixel, Dynamic Perception, foolography, Matjoez
  (Wegner histogram-weighted luminance ramp; region-based Visual Deflicker).
  [verified across multiple]
