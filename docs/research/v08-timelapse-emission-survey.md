# v0.8 Timelapse Emission & Workflow Survey (Adobe-free)

**Status:** Survey / decision-space map, 2026-05-28. Rewrite of (and supersedes)
[v08-render-path-survey.md](v08-render-path-survey.md), whose Adobe-engine
recommendation violated the repo's Adobe-free principle. Built from this
session's on-box verification + a 5-dimension parallel research workflow +
a completeness critic.

**Requirement ranking (user, locked):**
1. **Represent all LRT-authorable intent** — the hard goal *for the companion
   (lrt-cinema) tool*. The standalone tool (Tier 1.0) is explicitly **exempt**
   (own colour science).
2. Recovery latitude (luminance + colour).
3. Colour fidelity vs the LRT/Adobe reference.
4. Carrier quality (bit depth, gamut). **Compression released** ("TIFF is fine").
5. Effort/feasibility. 6. Adobe-free + licensing. 7. Self-contained / cross-NLE.
8. Longevity.

**Evidence tags:** **[here]** verified on this machine this session ·
**[doc]** vendor/primary doc or source code · **[claim]** research-agent
web finding, not reproduced here. Confidence caveats collected in §C.

**Structure (two tiers, per user; engine/format duplication collapsed per the
critic):**
- **Tier 1 — the option space for timelapse *generation & editing*** (tools,
  render backends, Adobe-removal, and the standalone-app option).
- **Tier 2 — the option space for *modifying LRT output*** (what this repo
  does today): recoverability approaches + an exhaustive format matrix.
- **§X — the represent-all ceiling** (clean-room + patents) ties the tiers.

---

# TIER 1 — Timelapse generation & editing: the option space

## 1.0 The standalone app — a full LRT replacement (proposed scope)

**Identity:** open-source, self-contained, Adobe-free app that **ingests,
edits, and emits its own timelines through its own workflow** — a true
LRTimelapse replacement. It does **not** read LRT XMP and is **not** bound to
reproduce Adobe's develop math: it uses **its own colour science and parameter
set**. *This is the clean escape from the represent-all ceiling (§X): because
it never promises Adobe's look, the closed/patented ops (Dehaze, PV5 tone…)
simply don't apply.*

**Capability scope** (= LRT's actual, engine-agnostic value, which the research
confirms is keyframe-metadata computation, not pixel rendering):
1. RAW sequence ingest — LibRaw/rawpy (LGPL/CDDL, **linkable**, already in-repo). [here]
2. Own keyframe authoring + interpolation (linear + smooth) — our param space.
3. Holy-Grail exposure-ramp detection + smoothing (dawn↔midday).
4. Visual deflicker (rolling-luminance and/or histogram-matching — two OSS
   reference families exist: cyberang3l GPL / struffel Go). [doc]
5. Own develop/colour pipeline — **render base analysed both ways in §1.2.**
6. High-bit-depth **recoverable** output (Tier 2 matrix) — the EXR/CDNG recovery
   work already verified this session drops straight in.
7. Encode/deflicker via **ffmpeg** shell-out (LGPL-safe; native `deflicker`,
   `minterpolate`, `tmix`). [here, ffmpeg 8.1 on box]

**Deliberately out of scope:** matching Adobe PV2012/PV5/Dehaze (not the goal);
in-app camera tethering (recommend qDslrDashboard/gphoto2 as front-ends).

**Why it's viable white space:** no *live* competitor combines Adobe-free +
true high-bit-depth output + timelapse keyframe authoring (see 1.1).

## 1.1 Competitive / complementary landscape

| Tool | Role | OSS? | Adobe-dep? | Self-contained? | Authoring (keyframe/HG/deflicker)? | Output ceiling | Note |
|---|---|---|---|---|---|---|---|
| **LRTimelapse 7** | incumbent competitor | no | **split** | only in 8-bit mode | yes (all three, best-in-class) | 8-bit JPEG internal / 16-bit TIFF *via Adobe LR* | the tool we replace; [here] installed |
| **Panolapse** | competitor (fading) | no | no | yes | motion KF + RAWBlend + AutoExposure + deflicker | JPEG/PhotoJPEG (~8-bit, inferred [claim]) | own non-ACR develop; low momentum |
| **GBTimelapse/GBDeflicker** | former competitor | no | mixed | yes | yes (AutoRamp) | — | **DISCONTINUED 2022**; Win/Canon only |
| qDslrDashboard | upstream capture | no (freeware) | no | yes | HG *capture* only | n/a | best HG capture; recommend as front-end |
| gphoto2 / Entangle | upstream capture | **yes** (LGPL/GPL) | no | yes | capture/intervalometer | n/a | LGPL gphoto2 = reuse-friendly capture |
| DaVinci Resolve | downstream grade/render target | no (free tier) | no | yes | **no per-frame develop KF** (layered-crossfade hack); deflicker = Studio-only | EXR/DPX/ProRes/TIFF | where our output GOES, not a competitor; [here] installed |
| After Effects / Premiere | downstream | no | **yes** (excluded) | no | via paid plugins (RawRamper, RE:Vision) | — | Adobe-dep → off the table |
| Mobile (Framelapse, etc.) | capture | mostly no | no | yes | minimal | 8/10-bit video | different market |

**Reusable OSS components** (the "what to reuse" answer), gated by the
**Apache-2.0 license** of this repo [here, pyproject.toml]:
- **Linkable** (permissive/LGPL/CDDL): LibRaw/rawpy, RawSpeed, ImageMagick
  (Q16-HDRI), libtiff/tifffile, OpenEXR. → in-process raw decode + image I/O.
- **Shell-out only** (GPL-3 — process boundary, never linked): ffmpeg encode
  (LGPL build is fine), darktable-cli / rawtherapee-cli / ART, dcamprof,
  cyberang3l deflicker. → encode, reference renders, profile-making.
- **Reimplement (don't copy)**: GPL deflicker algorithms (two families).

## 1.2 Render-backend decision — the decoder-vs-engine call (both options, with our lessons)

The single most decisive datum is already in-repo, not in any feature table:
**shelling out to a full engine (darktable-cli) measured 6.37 ΔE vs Adobe
`dng_validate`, while decoding with LibRaw and applying our OWN clean-room DCP
math in-process measured 0.79 ΔE** (gym; 0.84 rose). [doc: SCOPE.md,
DT_WORKFLOW_EXPOSURE_INTERACTION.md] The drawbacks of *both* approaches were
felt during lrt-cinema development (the darktable XMP-injection path is the
source of the encoding bug that started this whole session):

| Render base | Pros (our experience) | Cons (our experience) | License |
|---|---|---|---|
| **(α) Reuse our clean-room pipeline** | we control it; **0.79 ΔE**; Apache-2.0; recovery taps are ours (Stage-7 overrange, verified); for the **standalone**, the represent-all cap is moot (own colour science) | every op is from-scratch math; for the **companion**, PV5/Dehaze/§2.B permanently out (§X) | Apache-2.0 (ours) |
| **(β) Embed a full OSS engine** (darktable / RawTherapee / ART / vkdt) | mature broad colour science out-of-box; DCP support (RT/ART); GPU (vkdt) | **6.37 ΔE shell-out** (their colour model ≠ Adobe ≠ ours); CLI/integration friction; **GPL-3 infects** an Apache-2.0 product if linked → shell-out only; none read LR `crs:` faithfully | GPL-3 |

**Embeddable-backend field** (if β): **vkdt** is the only one purpose-built for
timelapse/raw-video (GPU/Vulkan, float, node-graph per-frame) but experimental
[doc]; **ART** is the best DCP-aware CLI; **RawTherapee** strongest colour
science (CIECAM02) but 16-bit-int CLI ceiling; **darktable** the only one that
reads LR `crs:` (lossy, GUI-only — *not* headless [doc]); **librtprocess**
(GPL-3) offers RT's AMaZE demosaic as a library; **rawtoaces** (BSD!) does
raw→ACES-EXR and is the one permissive engine-grade option, but produces
neutral scene-referred ACES (no look). **CLI float-output reality:** only
darktable-cli emits 32-bit-float EXR; RT-CLI/ART-CLI cap at **16-bit-int** TIFF
[doc]; rawpy on this box caps final output at 16-bit int (decoder-only into our
float writer). [here]

**Verdict for both products:** keep **LibRaw/rawpy as the decoder** (linkable,
already shipping) feeding **our own float pipeline** — it both wins on measured
ΔE and avoids GPL. Reach for an engine only to *borrow algorithms* (librtprocess
demosaic) or for the standalone's look if we'd rather not author colour science
from scratch (then darktable-cli/ART via shell-out).

## 1.3 The Adobe-strip checklist (companion path) — three artifacts, three fixes

lrt-cinema's chain has exactly three Adobe touchpoints. Each has an Adobe-free fix:

| Artifact | Today | Adobe-free fix | Status |
|---|---|---|---|
| **RAW→DNG converter** | Adobe DNG Converter subprocess | **dnglab** (Rust, LGPL, rawler decoder, no Adobe SDK); or **libraw-direct** (`--no-dng-convert`); or **inject WhiteLevel** into the libraw path | dnglab ≈ Adobe at **0.062 ΔE** [claim — agent, not reproduced here]; libraw-direct **0.71 ΔE** [doc: SCOPE/README ~0.5 regression] |
| **Render engine** | our clean-room pipeline (already Adobe-free) | — (it IS the replacement) | **0.79 ΔE** [doc] |
| **DCP colour profile** ← *the true last Adobe artifact* | loads Adobe "Nikon D750 Camera Standard.dcp" | **dcamprof** (GPL, shell-out) makes a CC24-calibrated open DCP; or RawTherapee's bundled DCPs | gives accurate base colour, **not Adobe's canned look** — a fidelity change [doc] |

Key correction (critic): the converter is **not** the hard dependency —
dnglab/libraw-direct both work; the **DCP profile** is the last real Adobe
artifact. Note the dnglab 0.062 number is an agent claim (no dnglab on this
box; no harness in `tools/`); the *feasibility* of Adobe-converter removal is
nonetheless solid because libraw-direct's 0.71 ΔE is independently corroborated.

## 1.4 Three product postures, compared

| Posture | What it is | Represent-all? | Adobe-free? | Authoring | Effort | Identity |
|---|---|---|---|---|---|---|
| **Companion** (lrt-cinema today) | reads LRT XMP, clean-room renders to high-bit-depth | capped by §X (core ✓, PV5/Dehaze/§2.B ✗) | yes (after dnglab + open DCP) | LRT does it | shipped | "high-bit-depth Adobe-free renderer for LRT users" |
| **Fully-open w/ LRT dropped** | darktable + dtlapse + ffmpeg | n/a (own look) | yes | **dtlapse — dormant since 2020, darktable-XMP only** | integrate dormant tools | weakest: authoring half is an unmaintained patchwork |
| **Standalone** (§1.0) | own ingest+edit+emit; own colour science | **n/a — exempt by design** | yes | **ours to build** (the hard, valuable part) | largest (new authoring UI/engine) | "the open LRTimelapse replacement" |

## 1.5 Standalone app — deep scope/spec (note 1) → [v09-standalone-app-build-vs-not.md](v09-standalone-app-build-vs-not.md)

A *creative* tool: it must **exceed LRT** and approach **Lightroom**-class
aesthetics/usability/speed. Full breakdown in v09; headlines:

- **UX target.** LRT's limits: develop outsourced to Lightroom; native internal
  render is an 8-bit-JPEG dead end; UI is a filmstrip + keyframe **table** with a
  low-res 8-bit Rec.709 preview (gradients band). The decisive wins: a
  **direct-manipulation keyframe TIMELINE with editable interpolation curves**
  (vs LRT's spreadsheet table) and **real-time GPU preview/scrubbing of the
  *developed, deflickered* sequence** (closing LRT's "preview ≠ output" gap).
  Surfaces: filmstrip browser · keyframe-curve timeline · develop panel ·
  Holy-Grail-ramp + deflicker **graphs** · masking · optional motion.
- **Stack.** Lightroom-class look ⇒ **Qt/QML (LGPL)** or **Tauri/web**; **egui /
  Dear ImGui fail the aesthetic bar** (tool-look) and **GTK looks dated**
  (darktable/RT). GPU: **wgpu (Rust)** cross-platform, or **Vulkan + MoltenVK**
  (C++/Qt). License gate: darktable/RT are **GPL-3** (fork ⇒ GPL); a permissive
  core (Qt-LGPL / Rust-wgpu) keeps a commercial option open.
- **Effort (1 senior eng).** MVP **~14–16 mo solo** (~6 mo w/ 2–3 eng); v1.0
  **~36–40 mo solo** (~12–18 mo small team). Schedule drivers (~40% of v1.0):
  **real-time preview (proxy/cache architecture)** + **GPU develop pipeline** —
  and the validated colour science is a **spec to re-implement on GPU**, not banked.
- **Don't-fully-build options.** (a) **Cobble** darktable + **dtlapse (dormant
  since 2020)** + ffmpeg → hobbyist patchwork, GPL throughout, fails the UX bar —
  personal pipeline only. (b) **Fork vkdt** — darktable-author Hanika's **Vulkan
  node-graph** processor **built for raw-video/timelapse**, **1.0 (Dec 2025)**,
  **BSD-2** (a few GPLv3 files to strip), GPU-resident **live preview already
  solved** — could cut **~30–40%** off v1.0. Costs: one-person upstream,
  **Vulkan-only**, **no Windows** (port needed), nuklear GUI replaced.
  **Recommendation: seriously evaluate a vkdt fork before from-scratch.**

**Identity consequence:** the standalone is **exempt from the represent-all
ceiling (§X)** — own colour science — the only posture that escapes it entirely.

---

# TIER 2 — Modifying LRT output: recoverability & formats

## 2.1 Two axes, and a load-bearing finding

"Recoverability" splits into two independent axes the requirements conflate:
- **Luminance/highlight headroom** = values >1.0. Already maxed by **Baseline A**
  (Stage-7 float-linear EXR overrange; [here] max 2.0–2.27, +1 stop, recovers
  in Resolve) and by **Baseline B** (CinemaDNG raw; [here] per-frame WB+exposure
  honored in Resolve).
- **Colour/gamut headroom** = chromaticities outside Rec.2020, which in a float
  container live as **negative channel values**.

**Finding (critic-verified):** our EXR writer (`output.py
write_exr_linear_rec2020`) does **NOT clamp negatives** — only the 16/8-bit TIFF
paths clip to [0,1]. So out-of-Rec.2020 colour is **already preserved losslessly**
in the float EXR. The missing piece is therefore *interpretation*, not bits or a
wider container. This collapses most of the "recoverability menu" into "tag and
carry what we already keep."

## 2.2 Format × capability matrix (exhaustive; merged from the format + recoverability research)

Ranked to the triad **recoverable + Resolve-ingest + open-encoder** (compression released).
R-rd / R-wr = Resolve read / write.

| Format | Max depth | Float | Recoverable? | R-rd | R-wr | Open enc? | Container | Verdict |
|---|---|---|---|---|---|---|---|---|
| **CinemaDNG** | 16-bit int (+float DNG1.4+) | (JXL float 1.7) | **raw, full re-develop** | ✅ | ✗ | ✅ PiDNG/dnglab | seq | **#1 recovery**; WB+exp honored per-frame [here]; colour delegated; **genuine mosaic, no re-mosaic (§2.4)** |
| **Linear DNG** | 8–16-bit + float | yes | **raw metadata, NO re-mosaic** | ✅ | ✗ | ✅ PiDNG | single | **key open question §2.4** — demosaiced pixels + DNG WB/exp; untested in Resolve |
| **OpenEXR half/float** | 16/32-bit float | yes | HDR headroom (baked WB) | ✅ | **✅** | ✅ | seq (multipart) | **#2 / our master**; 0.79 ΔE [here]; multipart NOT Resolve-selectable [here] |
| **DPX** | 16-bit (+16-bit-half via Resolve writer) | ltd | baked (log headroom) | ✅ | **✅** | ✅ ffmpeg/OIIO | seq | universal DI; Resolve writes 16-bit-half |
| **16-bit TIFF** | 16-bit int | no | baked, no headroom | ✅ | **✅** | ✅ | seq | banding-free; **what LRT's LR path emits** |
| 32-bit float TIFF | 32-bit float | yes | HDR headroom | ⚠️ unconfirmed | ✗ | ✅ | seq | Nuke/OIIO home; risky for Resolve |
| Cineon | 10-bit log | no | baked log | ✅ | ✅ (10-bit) | ✅ | seq | superseded by DPX |
| PNG-16 | 16-bit int | no | baked | ✅ | ✅ | ✅ | seq | = 16-bit TIFF, weaker metadata |
| JPEG2000/J2K | 16-bit (spec) | no | baked (lossless) | ✅ | ⚠️ 12-bit YUV writer | ✅ OpenJPEG/JPH | seq/MXF | DCP/delivery codec |
| JPEG-XL | 32-bit float | yes | baked | ✗ | ✗ | ✅ libjxl | single | **ruled out** (no Resolve); relevant only as DNG1.7 payload |
| AVIF | 8/10/12-bit | no | baked | ✗ | ✗ | ✅ libavif | single/seq | **ruled out** (no Resolve) |
| HEIF/HEIC | 10/12-bit | no | baked | ⚠️ Studio read | ✗ | ✅ libheif (HEVC patents) | single/seq | **ruled out** as master |
| Adobe DNG (stills) | 8–16-bit+float | yes | raw, re-develop | ✅ | ✗ | ✅ | single | = CinemaDNG; supports Linear DNG |
| ProRes 422/4444/XQ | 12-bit | no | baked, lossy | ✅ | **✅** | ✅ ffmpeg (12-bit caveat) | **single .mov** | NLE mezzanine; not a recoverable master |
| **ProRes RAW** | ~12-bit raw | no | raw | ✅ (R20.2) | ✗ | ✗ **no encoder** | single | **ruled out — no open encoder** [here: ffmpeg decode-only] |
| **Blackmagic RAW** | 12-bit raw | no | raw | ✅ | ✗ | ✗ **no encoder, EULA-barred** | single | **ruled out — uncodeable** |
| CineForm RGB | 16-bit int | no | baked (Active Metadata) | ✅ | **✅** | ✅ Apache SDK + ffmpeg* | **single .mov** | viable baked single-file; *ffmpeg cfhd is **decode-only** [here] — use GoPro SDK |
| CineForm RAW | 12/16-bit Bayer | no | raw | ✗ (dropped, claim) | ✗ | ✅ | single | re-mosaic + Resolve dropped it |

**Top tier:** CinemaDNG (deepest recovery, raw) · OpenEXR (our verified baked
master + scene-ref recovery, Resolve read+write) · DPX / 16-bit TIFF (universal
baked fallbacks). The **dual master** (baked-look EXR + raw/scene-ref recovery
stream) remains the cinema-correct way to satisfy recovery AND look at once;
multipart-EXR can't make it one file in Resolve [here], so it's dual-file.

## 2.3 Recoverability levers, ranked (additive over Baselines A+B)

**Additive (do these):**
1. **Tag EXR chromaticities + colour-managed ingest (OpenColorIO).** Cheap header
   attribute; makes the already-preserved negatives survive instead of being
   clamped downstream. The consuming-side mechanism is **OCIO** (Resolve/Nuke/
   Blender/Natron) — name it; `output.py` writes no chromaticities today. [doc]
2. **Emit a standards gamut (ACEScg/AP1), not linear Rec.2020 — see §2.5.** Our
   data barely exceeds Rec.2020 (measured), so this is a standards/ingest-
   correctness fix (linear Rec.2020 is a misused delivery gamut; ACEScg has a
   named Resolve Input entry, "Linear/Rec.2020" does not), **not** a recovered-
   clipping fix.
3. **Data + grade dual-stream** (scene-ref pixels + look as CDL/LUT). Keeps
   latitude AND look. Per-frame keyframed look is hard: Resolve-XML keyframes are
   a **verified dead end** [here]; CDL is per-clip/average; per-frame LUT+DCTL
   works but needs a DCTL install. Simplest robust form = **dual-file**.
4. **Renderer-side highlight reconstruction** (recover clipped channels at
   demosaic) — adds recoverable *content*, complements the float tap. [claim]

**NOT latitude levers (don't chase):**
- **Log encodings** (Cineon/LogC/S-Log3/V-Log/ACEScct): bit-efficiency only in
  10/12-bit *integer* carriers; add nothing over 16/32-bit float-linear EXR
  (half already ≈30 stops ≫ ~14-stop sensor). [doc]
- **ISO-invariance / dual-gain:** a *capture* property a renderer can't synthesize
  — an argument *for* the raw/CDNG path, not a new technique. [doc]
- **Gain maps / ISO 21496-1 / Adaptive-HDR:** display adaptation (SDR base + map),
  not grading latitude; **no Resolve/NLE ingest**. [doc]
- **PQ/HLG:** delivery transfer functions; baking into an intermediate *caps*
  highlights and spends latitude. Use at delivery only. [doc]
- **32-bit float over 16-bit half:** no recoverable benefit for a ~14-stop sensor;
  doubles storage. [doc]

## 2.4 CDNG stays mosaiced — the "fake raw / re-mosaic" objection was wrong (corrected)

**Correction (verified this session, note 3).** The earlier framing — "emitting
true raw from already-demosaiced pixels needs a lossy re-mosaic ('fake raw')" —
was **wrong for what we actually do.** The CDNG test
(`tools/resolve_verify/test_cdng.py`) **copies the source Bayer-CFA DNG and
mutates only metadata tags**; the source is `PhotometricInterpretation = Color
Filter Array`, `SamplesPerPixel 1` [here]. **No re-mosaic occurred — it is
genuine mosaiced raw.** A real lrt-cinema CDNG emission would do the same:
(dnglab/Adobe)-converted **CFA DNG** → inject per-frame `AsShotNeutral` /
`BaselineExposure` (verified honored [here]) → optionally pre-apply per-frame
mask-correction **gains to the Bayer plane** (multiplicative; still a genuine
mosaic — the v0.7 "Bayer-bake"). **None of that re-mosaics.** Re-mosaic would
only be needed to bake *demosaic-dependent* look ops (tone curve / HSL / colour
matrix) *into* raw — which we never do (the look rides as the baked-EXR stream of
the dual master, or is dropped). So "fake raw" is a **strawman** for this use case.

**Adjacent option — Linear DNG** (`PhotometricInterpretation = LinearRaw`):
carries *demosaiced* pixels **with** DNG metadata + per-frame WB/exposure, also
**no re-mosaic**. **Now verified [here]:** Resolve **honors per-frame
WB/exposure** on a Linear DNG (B/R 0.658→1.505, exposure ×1.92). But it is
**not adopted** — see §2.6: it is dominated by ACEScg-EXR (our colour, smaller,
clean Resolve read) and by CFA-CDNG (full-sensor raw), and its colour is still
delegated to Resolve. (A Linear-DNG-vs-pipeline ΔE was measured but is *not* a
colour verdict: Resolve decodes LinearRaw *flat*, no camera-raw tone curve, vs
our tone-mapped render — different tonal states, not comparable.)

## 2.5 Gamut & transfer — align to standards, don't invent a Franken-gamut (note 2)

**Measured footprint (this session, gym + rose, stage-9).** Our emissions
**barely exceed Rec.2020**: gym **0.00%** out-of-gamut in Rec.2020 / AP1 / AP0;
rose worst-case min channel **−0.0017** (< 0.01% of pixels below −0.001 even in
Rec.2020). The "any-negative %" is sub-0.001 rounding noise. So **there is no
measured gamut clipping on these scenes** — the gamut choice is about *standards
correctness + clean Resolve ingest*, not recovering lost colour. (A saturated
scene — neon, deep sky — would discriminate; gym/rose don't.)

**Standards finding (verified on-box, Resolve Studio 21 — full detail in
[v08-linear-exr-gamut-resolve-nuke.md](v08-linear-exr-gamut-resolve-nuke.md)):**
- Resolve **ignores** the EXR `chromaticities` / `acesImageContainerFlag`
  attributes — untagged / Rec.709-tagged / AP0-tagged decode byte-identical, in
  YRGB-RCM **and** ACES modes [here]. Gamut comes solely from the clip's **Input
  Color Space** assignment.
- Input Color Space **"Linear" = linear transfer + inherit the *timeline* working
  gamut** (no input-side gamut transform) [here] — which is exactly *why* there
  is no "Linear/Rec.2020" entry: "Linear" deliberately carries no primaries. The
  RCM Input list accepts `Linear`, `Rec.709/2020 Gamma 2.4`, **`ACEScg`**,
  `ACEScct`; it **rejects** `Linear/Rec.2020` and `ACES2065-1`.
- **Linear Rec.2020 is a delivery gamut (BT.2020) misused as a scene-referred
  working space** — your suspicion is correct; it has no IDT and no matching
  Resolve Input entry. **ACEScg (AP1)** is the de-facto scene-referred grading/VFX
  space (positive primaries, ⊃ Rec.2020); **ACES2065-1 (AP0)** is the
  archival/interchange encoding (log ACEScct/cc are irrelevant to a *linear* EXR).

**Recommendation:** emit **scene-linear ACEScg (AP1)** and tag
`chromaticities = AP1`. Resolve won't read the tag, but the colorist then picks
the named **"ACEScg"** Input entry — one obvious standard click; emitting "linear
Rec.2020" leaves only the ambiguous "Linear" (inherit-timeline) path. Offer
**ACES2065-1 (AP0) + `acesImageContainerFlag`** as the archival variant.
**Implementation:** change `output.py`'s `ProPhoto(D50)→Rec.2020(D65)` to
`ProPhoto(D50)→AP1` with a **D50→~D60** adaptation (AP1 white ≈ D60, **not D65** —
don't mistag), keep transfer linear, and write the `chromaticities` attribute
(absent today). This is a primaries+whitepoint+tag change, not a tone change.
**Implemented + verified** (mean ΔE 0.64 round-trip, §2.6 V4).

## 2.6 Verification results (the 4 open checks, now closed) + emission verdict

All four open verifications run on this box (DaVinci Resolve Studio 21, headless,
gym scene). [here] = verified this session.

| Check | Result |
|---|---|
| **V1 Linear DNG per-frame WB/exp** | **HONORED** [here] — B/R 0.658→1.505, exposure ×1.92; demosaiced, **no re-mosaic** |
| **V2 dnglab converter** | **drop-in** [here] — dnglab-DNG vs Adobe-DNG (same pipeline+DCP) **mean ΔE 0.059, 100% <1ΔE** |
| **V3 CDNG colour delta (γ-matched)** | CFA-CDNG vs our pipeline **~8.5 ΔE** (residual gamma slop ⇒ "materially divergent"); colour **delegated to Resolve's bundled DCP**, not our science |
| **V4 ACEScg EXR round-trip** | **mean ΔE 0.64** [here] — our ACEScg EXR via the named "ACEScg" Input entry reproduces our colour; the §2.5 switch works end-to-end |

**The trade has two poles — you cannot have both in one stream:**
- **Colour fidelity → ACEScg EXR.** Keeps our validated 0.79-ΔE science
  (round-trip 0.64 ΔE [here]); recovery = half-float + Stage-7 overrange
  (~1 stop+), **not** full sensor. *The master — switch implemented (§2.5).*
- **Full-sensor raw latitude → CFA CinemaDNG** (the only path). Per-frame
  WB/exposure re-developable [here], genuine mosaic, smaller — **but colour is
  delegated** to Resolve's bundled DCP (≠ our science) and the look can't ride.

**Verdict — do NOT switch to CDNG or Linear DNG:**
- **Keep ACEScg EXR** as the emission master (colour-accurate, verified).
- **Reject Linear DNG** — honors per-frame WB/exp but is *dominated on both
  axes*: by ACEScg-EXR-scene-ref (our colour, smaller, clean Resolve read) for
  gradeable scene-referred use, and by CFA-CDNG for full-sensor raw; colour still
  delegated; larger (demosaiced 3-ch vs Bayer 1-ch).
- **Offer CFA CinemaDNG as an OPTIONAL max-recovery preset** for users who want
  full-sensor latitude and will re-grade colour in Resolve anyway. It is **not**
  a drop-in recovery companion to the EXR look stream — its delegated colour
  won't match the ACEScg look in Resolve (grading friction). Needs a `cdng_emit`
  writer + per-camera colour characterisation; **not built this pass**.
- **The colour-consistent dual master is all-EXR**: ACEScg baked-look +
  ACEScg Stage-7 scene-ref (both in our colour science).
- **dnglab** (verified 0.059 ΔE) makes the render chain **Adobe-free end-to-end**
  regardless of emission choice — adopt it in `dng_convert.py`.

---

# §X — The represent-all ceiling (ties both tiers)

For the **companion** posture, represent-all is gated not by format but by
**renderability** (three independent confirmations: clean-room ACR infeasible
for closed/patented ops; no non-Adobe engine renders `crs:` faithfully; DNG SDK
strips `crs:`). Patent-free clean-room reaches: the **core set** (shipped, 0.79
ΔE); **bounded own-implementations** of HSL/wheels/parametric-tone/Clarity/
Texture (won't pixel-match Adobe; OSS refs exist); **patent-walled** = Dehaze
(+ maybe AI Denoise/Enhance) → drop; **won't-match** = PV5 Highlights/Shadows/
Whites (trade-secret adaptive). The **standalone** posture is **exempt** — own
colour science means "represent" = "honor with our look," not "match Adobe."

---

# §C — Confidence caveats (critic-flagged)

- **dnglab 0.062 ΔE [claim, not reproduced here]** — no dnglab binary/harness on
  this box; precision is an agent claim. Feasibility holds via libraw-direct's
  independently-corroborated 0.71 ΔE.
- **ffmpeg `cfhd` is decode-only [here]** — earlier "encoder verified" was wrong;
  use the GoPro cineform-sdk (Apache/MIT) to encode CineForm RGB.
- **Version/date specifics are soft** — several (ART 1.26.5, dnglab 0.7.2,
  DNG→ISO 12234-4:2026, ProRes RAW in R20.2, Panolapse 1.25) post-date the
  knowledge boundary and are suspiciously precise; none flips a verdict.
- **Inferred, tag visibly:** Panolapse 8-bit ceiling; qDslrDashboard license;
  CineForm-RAW "dropped by Resolve."
- **CinemaDNG colour delta ~9.5 ΔE is an upper bound** (conflates DCP-science +
  gamma + Resolve default tone) — magnitude not isolated; per-camera
  characterisation needed before trusting CDNG colour. Caveat rides with every
  CDNG recommendation.
- **32-bit-float TIFF Resolve decode unconfirmed**; **multipart-EXR not
  selectable in Resolve [here]**; **DNG 1.7 JXL/float decode in Resolve
  unconfirmed**.

# Open verifications (next, when a direction is chosen — all headless on this box)
1. **Linear DNG** per-frame WB/exposure in Resolve (§2.4) — extend the CDNG harness.
2. **dnglab** end-to-end on this box (install + ΔE harness) to replace the claimed 0.062.
3. **CinemaDNG colour delta** isolation vs our 0.79 ΔE pipeline (per-camera).
4. **OCIO/ACEScg-tagged EXR** ingest in a colour-managed Resolve project.

# Sources
Per-option source URLs are in the workflow research record
(`tasks/w21gpcybd.output`); primary repo evidence: SCOPE.md, EMISSION_FORMAT_VERDICT.md,
EMISSION_FORMAT_VERIFIED.md, tools/resolve_verify/, tools/verify_emission_format.py.
