# Color-correction option space

Technical survey of the candidates evaluated for lrt-cinema's color
pipeline, with foreclosure reasoning where applicable. Companion documents:

- [decision.md](decision.md) — what v0.6 ships.
- [measurements.md](measurements.md) — empirical inputs.
- [background.md](background.md) — math primitives (root-polynomial,
  SSF-IDT, HSV residual catcher), industry context (cinema-broadcast
  pipeline, photography RAW software, academic standards), license +
  patent landscape, and primary-source references.

The candidate evaluation below uses the math vocabulary defined in
[background.md §1](background.md#1-math-primitives) and the industry tier
table in [background.md §2.1](background.md#21-cinema-broadcast-color-science).

## 1. Problem decomposition

lrt-cinema's color pipeline has three composable stages, each with an
independent solution surface:

| Stage | What it does | Solution surface in v0.6 |
|---|---|---|
| **Camera response** | Sensor RGB → working-space tristimulus | Per-camera ColorMatrix from EXIF + shared Adobe-Standard HueSatMap/LookTable (A′) |
| **User develop intent** | LRT-authored CRS slider values → working-space transform | `xmp_emitter.py` maps CRS to dt modules per the v0.4 table in `SCOPE.md` |
| **Output encoding** | Working-space → deliverable container | Three presets: `cinema-linear` (16-bit linear Rec.2020 TIFF), `cinema-aces` (32-bit float EXR), `stills-finished` (AgX-baked Rec.2020) |

The camera-response stage carries the structural problem the research was
commissioned to investigate. It has both a measurable colorimetric target
(camera RGB → known tristimulus on calibration patches) and a workflow
target (perceptual match against the Adobe-rooted LRT preview, which is
the grader's reference). The two targets are coupled but distinct: a
perfect colorimetric match still differs perceptually from the LRT preview
to the extent that LRT's preview pipeline is itself non-colorimetric (it
applies a stylized look character on top of the colorimetric stage).

The user-develop-intent stage is essentially settled at v0.4: each LRT
CRS slider has been mapped to either a dt module (the cases where one
exists) or explicitly dropped with a stderr warning (the cases where dt
itself has declined to map the field — PV2012 Highlights2012, Shadows2012,
Whites2012 are dropped by dt's own LR importer because the PV2012 math is
closed-source). This stage is out of scope for the option-space work.

The output-encoding stage is also settled. The three existing presets
cover the cinema workflow (`cinema-linear` for DaVinci YRGB; `cinema-aces`
for ACES pipelines; `stills-finished` for finished-delivery). The
remaining work is colorist-facing documentation, addressed in v0.6 per
the decision.

The option space therefore centers on the camera-response stage and the
**cross-stage control loop** — the question of where in the
authoring-rendering chain a grader can make perceptual decisions whose
results survive translation through to the deliverable.

## 2. Terminology

| Term | Definition |
|---|---|
| **CRS** | Camera Raw Settings — Adobe's XMP namespace for develop intent. LRT writes per-frame CRS sidecars. |
| **DCP** | DNG Color Profile — Adobe's per-camera color characterization format (ColorMatrix1/2, ForwardMatrix1/2, HueSatMap, LookTable, ProfileToneCurve, BaselineExposure). |
| **HSM / HueSatMap** | A 3D LUT indexed by HSV chromaticity that applies per-hue/saturation/value deltas. Adobe ships HSM in 90×30×1 (90 hues, 30 saturations, 1 value-axis sample) for ~67% of catalog cameras. |
| **LookTable** | A second 3D LUT in the DCP applied after HSM. Adobe ships LookTable in 36×8×16 for 99% of same-dimension catalog cameras. |
| **ProfileToneCurve** | A 1D tone curve on luminance (V in HSV). Present in only 3% of Adobe Standard catalog. |
| **BaselineExposure** | A scalar EV offset applied at decode. Identically zero in Adobe Standard across all 480 sampled cameras. |
| **Luther condition (Maxwell-Ives)** | The sufficient condition for exact RGB↔XYZ matrix invertibility: the camera's three SSFs must be a linear combination of the CIE 1931 color matching functions. Production Bayer sensors with dye filters never satisfy it. |
| **SSF** | Spectral Sensitivity Function — the camera's response curve per wavelength. Measurable via monochromator or estimable from charts; the basis for closed-form IDT computation. |
| **IDT** | Input Device Transform — ACES's per-camera matrix mapping camera RGB → ACES2065-1 (AP0). |
| **PV2012** | Process Version 2012 — Adobe's parametric tone-curve / contrast / highlights / shadows / whites math, closed-source in `acr.dll` / `Camera Raw.plugin`. |
| **Working space** | The intermediate color space lrt-cinema renders into: linear Rec.2020 for `cinema-linear`, linear Rec.2020 (32-bit float) for `cinema-aces`. |
| **Adobe Standard** | One of several Adobe DCP profile lines. Designed camera-agnostic — "delivers a consistent unified look across all cameras" per Adobe documentation. |
| **Camera Standard / Camera Vivid / Camera Neutral** | Per-camera Adobe profile lines that *do* vary by camera by design (manufacturer-specific intent). Not the target of A or A′. |

## 3. Constraints

The user-stated constraints (2026-05-26) that bound the candidate set:

| Constraint | Stated answer |
|---|---|
| Adobe code in runtime | Acceptable if it cleanly solves the problem, but preferred none-at-runtime. If present, must be an alternate pipeline, not the only pipeline. Adobe at install/build time is fine. |
| Platform | macOS-first acceptable; Linux is the primary alternate platform; Windows welcomed if it falls out of design but not a constraint. |
| License envelope | Apache-2.0 strict for the project; GPL-compatible deps acceptable by subprocess (darktable-cli already does this); CC BY-NC-SA data acceptable for user-side opt-in (not redistributable). |
| Engineering budget | Open-ended; pick candidate first, then commit budget. |
| UI surface | Full GUI authoring on the table (Shape γ stays in scope). |

These constraints rule in: any candidate that runs Adobe-free at render
time (or uses Adobe only as an alternate engine), works on Linux, and
stays within Apache-2.0-compatible licensing. They rule out: candidates
that require Adobe Camera Raw / Lightroom Classic at runtime; candidates
that need closed proprietary calibration data lrt-cinema cannot ship.

## 4. Solution patterns from adjacent fields

The cross-pipeline authoring-consistency problem appears in many domains.
Five structural patterns recur; lrt-cinema's candidates are derived from
these.

| Pattern | Domain example | lrt-cinema candidates |
|---|---|---|
| Calibrate the author's display to the deliverable | ICC soft-proofing, print preview | H (custom monitor ICC) |
| Parallel display of both pipelines | Cinema previs, broadcast dual-monitor | F (exact parallel render), G2 (T-corrected parallel viewer), I (JIT approximate viewer) |
| Quantitative metric + corrective transform on upstream side | Dirac audio room correction | G (LRT preview LUT correction via screen capture) |
| Standardized intermediate | ACES (ACES2065-1 exchange), audio 24-bit/96kHz masters | A (per-camera Adobe-match), A′ (camera-agnostic Adobe-match) — both target Adobe's profile space |
| Procedural authoring inside the deliverable pipeline | In-engine game cinematics, DAW mastering | D (full LRT replacement) |

Two additional patterns inform parts of the search:

- **Round-trip validation at sample points** (TDD, music session playbacks) corresponds to C (current state: author iterates by render-and-review) and K (constrained-author + Resolve-downstream with documented operation restrictions).
- **Procedural encoding instead of baked rendering** (music notation, BIM) corresponds to E (raw-passthrough emitting modified XMP for Resolve) — the candidate the metadata-passthrough investigation closed.

## 5. Candidate catalog

Each candidate appears with: a one-line summary, the engineering cost (per
the per-cluster feasibility studies in the iteration trail), the
achievable ΔE2000 ceiling where measured or estimated, and the verdict.

### A — Adobe-match per-camera (full calibration tower)

Per-camera ColorMatrix + per-camera HueSatMap/LookTable from extracted
DCP `.npz`, optionally enriched with root-polynomial regression
(Finlayson 2015) or SSF-integrated IDT (AMPAS P-2013-001) when SSF data
is available.

Engineering scope: 5–7 wks. The non-linear stage is already shipped via
`src/lrt_cinema/lut3d_baker.py`; the new work is the optional root-poly
and SSF enrichment layers plus a per-camera validation harness.

Achievable ΔE2000 by camera tier:

| Tier | Calibration data | Mean ΔE | P95 |
|---|---|---:|---:|
| 1 | SSF available (butcherg/ssf-data) | < 1.5 | < 3.0 |
| 2 | DCP + root-polynomial | < 3.0 | < 5.0 |
| 3 | DCP matrix only | < 6.0 | < 12.0 |

**Verdict: viable.** Status in v0.6: shipped as the opt-in `--engine
adobe-camera` enrichment path. The Tier-2 baseline (per PR #15) is the
linear foundation; the Tier-1 SSF enrichment is opportunistic for cameras
where SSF data exists (Nikon D750 is in butcherg/ssf-data).

### A′ — Adobe-match camera-agnostic (shared transform)

Single shared transform (median HueSatMap + median LookTable across the
Adobe Standard catalog) shipped in `src/lrt_cinema/presets/adobe_standard.npz`.

Engineering scope: 3 wks. See [measurements.md](measurements.md) §1
(Q1 catalog-variance) and §2 (A′ empirical ceiling) for the load-bearing
evidence.

Achievable ΔE2000 (measured 2026-05-26 against per-camera Adobe Standard
ground truth, 214-patch reference panel):

- Modern HSM-equipped cameras: 0.4–1.5 mean (cinema-reference).
- Full 40-camera panel including legacy bodies: 3.60 mean / 11.46 P95
  (broadcast-acceptable; not cinema-reference on legacy bodies).

**Verdict: viable. Shipped as v0.6 default.** The data on cross-camera
variance in Adobe Standard supports the hypothesis that a single shared
transform captures most of the look character; per-camera enrichment (A)
is available for users on bodies where the residual matters.

### B — LRT preview-cache substitution

Replace `.lrt/visual/*.lrtpreview` JPEGs with externally-rendered ones so
LRT displays our pipeline's output as the grader's reference.

**Verdict: foreclosed.** Empirically tested 2026-05-26: LRT regenerates
the cache JPEG via its bundled Adobe DNG Converter on every slider
interaction in the editor pane, clobbering external writes before they
reach the grader's eye. The cache JPEG is the *output* of LRT's preview
pipeline, not the *input* to the editor pane. See [measurements.md §3](measurements.md#m3-lrt-preview-cache-behavior).

External writes do control pre-edit timeline thumbnails, the
pixel-luminance basis of Visual Deflicker analysis, and the "pink curve"
visualization until the next interactive edit. These are side-channel
uses; they don't close the live-grading loop.

### C — Current state (dt-native render, accept residual)

Render LRT-keyframe intent through darktable's color science; the grader
accepts the cross-stage gap. Cost: 0 wks (today) to 1 wk (doc reframe).

Achievable ΔE2000: DSC_4053 reference frame measures 6.05 mean ΔE pre-
affine; 2.24 mean post-affine on a neutral keyframe. Worse on
perceptually-targeted LRT-stage authoring (saturation, HSL, curve shape).

**Verdict: partially viable as a fallback.** The constrained-author
variant (K, below) sharpens C into a documented operation subset.

### D — LRT replacement (clean-slate authoring UI)

Replace LRTimelapse as the timelapse-grading authoring tool. New PySide6
GUI whose preview pane is rendered by lrt-cinema's own pipeline. No Adobe
DNG Converter anywhere. The grader's reference IS the deliverable's color
science by construction.

Engineering scope: **31–42 engineer-weeks (~8–10 months single-developer)**
for v1 with Tier-1 feature parity. Plus 3–6 months/year ongoing
maintenance indefinitely. The unbounded item is Visual Deflicker (3–4 wks
for serviceable TLDF-equivalent; +4–6 wks for multi-pass + EXIF-aware
masking that approaches LRT quality on real footage).

Components:

| Component | Eng-weeks | State |
|---|---:|---|
| UI shell (PySide6: window, timeline strip, panels, menus) | 6–8 | New |
| Fast preview path (cached-proxy + delta-apply with GPU shader) | 4–6 | New |
| Visual Deflicker (single-pass + multi-pass + masking) | 8–12 | New (the load-bearing unknown) |
| Holy Grail Wizard (EXIF detection + compensation curve + Optimize) | 2–3 | New |
| Sequence ingestion / project state | 2 | Partial (parser exists; project state new) |
| Render trigger / progress | 1 | Partial (runner exists; UI shell new) |
| LRT read-only migration | 0.5–1 | Mostly done (parser validated against LRT Pro 7.5.3) |
| Catmull-Rom smooth interp re-implementation | 1 | Was in v0.2; deleted in 2026-05-24 audit |
| Testing + packaging + docs | 7–10 | New |

UI framework: **PySide6** wins by constraint analysis (single Python
developer, cross-platform mac + Linux, free LGPL-3.0 license with
dynamic-link non-infection of Apache-2.0 application code). Tauri/Iced/
Slint add a Rust language boundary; Electron's binary overhead and Node
+ Python double maintenance is worse; ImGui is wrong shape; native
AppKit + GTK is two codebases.

Roughly half of D's non-UI surface is already implemented in lrt-cinema
(parser, emitter, interpolation, DCP, runner; ~2000+ lines of test code).

Structural risk: single-developer maintenance with no funding model.
Wegner sustains LRTimelapse on a Pro-tier paid product; an Apache-2.0
free clone has no equivalent. Realistic posture: "perpetually behind LRT
on features, acceptable to a small open-tool-preferring user base."

**Verdict: held on the horizon for v1.0.** Not in v0.6 scope. If
selected as a future direction, α's artifacts compose forward into γ;
nothing in α is thrown away.

### E — Raw-passthrough / metadata-passthrough emission

A new render mode that emits source RAW + modified LR-shape XMP instead
of rendering TIFF/EXR. The colorist's downstream tool consumes the RAW
and applies the XMP develop intent.

**Verdict: foreclosed.** Three independent failure grounds, any one
sufficient:

1. **Resolve does not read XMP develop intent on RAW imports.** Confirmed
   by LRTimelapse author (Gunther Wegner): *"Davinci does cannot develop
   RAW files based on Adobes XMP files."* Confirmed by multiple
   third-party colorist accounts. Confirmed by Blackmagic's own Resolve 21
   Photo-page launch documentation: the new Lightroom-catalog import
   transfers album structure and metadata but explicitly *not* develop
   settings.
2. **Resolve's "Camera Raw" is not Adobe Camera Raw.** It's BMD's
   independent YRGB pipeline, sharing only the name. The PV2012 math
   lives in closed `acr.dll` / `Camera Raw.plugin` (see
   [DNG_SDK_FEASIBILITY](../DNG_SDK_FEASIBILITY.md)); Resolve ships its
   own debayer + decode and has color-accuracy issues especially on
   Fujifilm and iPhone ProRAW.
3. **Image-sequence imports apply one Camera Raw decode per clip, not
   per-frame XMP.** LRT's per-frame deflicker / Holy Grail
   `crs:LocalExposure2012` deltas have no per-frame metadata channel in
   Resolve's import semantics.

Engineering scope estimate (moot): ~3 wks to implement the LR-shape XMP
emitter and per-frame mask-correction flattening. Would produce code the
destination tool ignores.

Narrow exception: Lightroom-as-renderer between LRT and Resolve closes
the loop because LR consumes its own XMP. This is the workflow lrt-cinema
was built to replace; returning to it re-introduces the LR runtime
dependency the project chose to exit.

### F — Parallel exact-render viewer

When LRT writes an XMP, lrt-cinema re-renders that frame through the
production `darktable-cli` pipeline and shows the result in a viewer
window. Grader cross-references LRT's preview and the viewer during
authoring.

Engineering scope: 2 wks (daemon-only, writes JPEGs to a folder for any
image viewer) to 6 wks (polished PySide6 window with zoom, frame strip,
A/B toggle).

The discriminator is **dt-cli per-frame latency**. dt-cli has no daemon
mode (1–2s startup tax per invocation); estimated D750 render latency is
2–10s per frame on CPU, 1–3s with OpenCL. At 5–10s end-to-end, F is too
slow for interactive grading; at 1–2s end-to-end it's plausible.

**Verdict: feasibility-pilot candidate.** Not in v0.6 scope. If A′
validation reveals the workflow needs a parallel reference, the
daemon-only sub-candidate is the cheapest pilot (~2 wks) and doubles as
the dt-cli latency benchmark vehicle.

### G — LRT preview LUT correction (screen-capture overlay)

Capture LRT's window pixels via ScreenCaptureKit, apply a T-correction
in a Metal shader, render into a borderless transparent click-through
overlay window. The grader sees a corrected LRT preview without LRT or
lrt-cinema modifying each other.

**Verdict: foreclosed at production quality.** Multiple HIGH-severity
risks:

- macOS Sequoia (15.x) tightened ongoing-capture consent; the trajectory
  through macOS 13 → 14 → 15 has been monotonically more restrictive,
  with weekly or per-login re-consent prompts. macOS 16 plausibly closes
  the surface entirely.
- 8-bit JPEG quantization in LRT's preview amplifies banding in low-
  saturation regions (skin, skies) under any LUT correction.
- Z-order tracking against LRT modals requires Accessibility API
  permissions and is fragile across LRT version updates.
- No Linux portability: Wayland color management requires app opt-in
  (LRT doesn't), and X11 has no per-window gamma surface.

### G2 — T-corrected parallel viewer (lrt-cinema-owned window)

Salvageable form of G. Instead of correcting LRT's window, render the
corrected preview into a separate lrt-cinema-owned viewer window
alongside LRT. The grader cross-references during authoring. The viewer
sets its own colorspace via the documented `NSColorSpace` API (no
screen-capture, no overlay).

Engineering scope: 3–5 wks for the viewer (file-watcher on
`.lrt/visual/*.lrtpreview`, GPU shader for T-correction, PySide6 window).
Inherits A or A′'s tower upstream for the correction's source.

**Verdict: contingent v0.7 candidate.** Per the decision, A′'s ~1.5 ΔE
mean on target modern cameras suggests G2 is unnecessary for the primary
user base. If post-A′ validation reveals the cross-stage gap is larger
than ~3 ΔE mean — or if the grading workflow shifts toward HSL-heavy /
per-color decisions — G2 ships as a v0.7 enhancement.

### H — Custom monitor ICC profile (Color Sync system-wide)

Define an ICC profile that, when assigned as the display's monitor
profile, applies a T-correction to every pixel rendered on that display.
LRT's window is treated as nominally sRGB and pushed through the profile.

**Verdict: foreclosed by OS design.** With the corrective ICC set as
monitor profile, *every* app — Resolve, Photoshop, browser, every
reference image — renders through the correction. The colorist who later
opens Resolve grades against a contaminated reference, adjusts to
compensate, exports — and the deliverable is wrong by the LUT. The
contamination is deterministic, not a bug; it's the consequence of
attaching an app-specific transform to a system-wide surface.

Mitigations (manual profile switching, AppleScript launch/quit hooks,
dedicated secondary monitor) require user discipline as the last line of
defense. The OS does not guarantee the order of foreground / quit
notifications, so profile-switch automation has a non-zero
stuck-in-wrong-profile rate.

No Linux story: Linux ICC display profiles are honored only by
participating apps; the display server (Xorg, Wayland compositors below
KDE 6.1) does not apply ICC globally.

### H1 — OCIO config emission for Resolve

Different problem from H. Emit an OCIO config that maps lrt-cinema's
rendered TIFF working space to a downstream tool's deliverable view. The
colorist loads the TIFF into Resolve under that OCIO config; Resolve's
viewport view matches what lrt-cinema rendered.

**Verdict: subsumed into Resolve workflow documentation.** Closer
investigation: Resolve's native CMS handles both `cinema-linear` (DaVinci
YRGB Color Managed with input space "Linear Rec.2020") and `cinema-aces`
(ACES with input transform "Linear Rec.2020 → ACES2065-1") without
needing a custom OCIO config. OCIO is the right surface only for
projects that already use OCIO across the toolchain. The v0.6
deliverable is documentation (`docs/RESOLVE_WORKFLOW.md`), not OCIO
emission.

### I — JIT preview-quality approximate viewer

Like F but fast and approximate. Three sub-flavors:

- **I-a: OCIO LUT-baked viewer.** Precompute the dt pipeline at fixed
  parameters as a 33³ or 65³ LUT. Per-frame dynamic ops (exposure, WB,
  mask deltas) applied separately. Cost: 4–5 wks.
- **I-b: GLSL/Metal shader reimplementation of dt modules.** Highest
  fidelity, highest maintenance burden (every dt module change requires
  a shader update). Cost: 8–12 wks. **Foreclosed on maintenance grounds.**
- **I-c: vkdt borrow.** vkdt is Johannes Hanika's (dt founder) Vulkan-
  based replacement, real-time on GPU, native timelapse support. If
  vkdt's pipeline matches dt closely enough, this gives I-a fidelity at
  I-b speed. Cost: 3–5 wks if vkdt's fidelity holds; substantially more
  if module-by-module behavior matching is required.

**Verdict: held with G2.** Not in v0.6 scope. If F/G2 evaluation reveals
the latency or fidelity tradeoff favors approximation, I-a or I-c become
candidates.

### J — Reference-track A/B

The grader authors a small reference sequence (a few frames) through
both pipelines and uses it as a perceptual calibration anchor for the
larger sequence. Workflow, not code; cost ~0.

**Verdict: trivially compatible with any candidate.** Not a candidate
per se; a workflow technique colorists already use that lrt-cinema can
document in `docs/RESOLVE_WORKFLOW.md`.

### K — Constrained-author + Resolve-downstream

Sharpened form of C: the grader restricts LRT-stage authoring to
operations whose Adobe→darktable translation is mathematically well-
defined (linear exposure, chromatic adaptation, identity-or-near-identity
tone curve, transitions); all perceptually-targeted operations defer to
Resolve. Cost: 1 wk documentation.

The restricted slider subset (already implicit in `SCOPE.md`'s v0.4 emit
table):

| Slider | Translates cleanly across pipelines? |
|---|---|
| Exposure2012 | Yes (1 EV = 1 EV) |
| Temperature2012 + Tint | Yes (well-defined chromatic adaptation) |
| ToneCurvePV2012 (identity / near-identity) | Yes |
| Blacks2012 | Yes (verbatim dt mapping) |
| Sharpness | Yes (linear-scale to dt sharpen.amount) |
| Saturation / Vibrance | **No** (perceptually-targeted, scene-dependent) |
| Contrast2012 / Highlights2012 / Shadows2012 / Whites2012 | **No** (PV2012-specific tone math; dt itself declines to map these) |
| HSL panel | **No** (per-hue HSL has no clean cross-pipeline mapping) |

**Verdict: complementary to A′.** K is a usage convention, not a code
shape; documenting it accompanies the A′ + Resolve-workflow ship in v0.6.
