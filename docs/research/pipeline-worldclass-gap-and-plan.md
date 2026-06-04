# World-class pipeline spec, gap analysis, and gated plan — incl. an adversarial meta-critique of the order/domain audit

**This doc answers: what did the order/domain audit
([pipeline-order-audit.md](pipeline-order-audit.md)) MISS, what does a
world-class pipeline actually look like for *this* project's two purposes, and
what is the prioritized, validation-gated plan to close the gap.** Built from 7
parallel sub-investigations (5 external-research, 2 code), each primary-sourced.

> **Status:** 2026-06-03. Analysis only; no code changed. Supersedes nothing —
> *extends* the order/domain audit and **corrects three of its claims** (§4).
> Every recommendation ends in a **validation gate**, not a proof: the SDK source
> is absent locally and no render fixtures are present, so nothing here was *run*.

---

## 0. Scrutiny of the method (the prompt's own assumptions)

The "build a world-class spec, compare, extrapolate" method is sound **only with
three guards**, each of which a naive application would violate:

1. **The spec MUST be bifurcated by objective — a single "world-class ideal"
   produces false findings for the faithful path.** Path A (sRGB TIFF) exists to
   **bit-reproduce Adobe PV2012's display-referred render** (it round-trips back
   into LRTimelapse; fidelity to Adobe *is* correctness). A scene-referred ideal
   (AgX/filmic/"keep everything linear until one display transform") would make it
   **worse** at that job. A sub-agent with sources confirmed this: the project's
   mid-pipeline tone curve is *not* an anti-pattern for path A — it is a faithful
   copy of Adobe's own architecture, **empirically proven by the 0.026 ΔE match
   with the clamp in place** (darktable/RawTherapee likewise ship a mid-pipeline
   tone curve). So path A is measured against *"correctly reproduce Adobe,"* path B
   (ACEScg master) against *"world-class scene-referred."*
2. **"Complete solution / don't stop until done" is a footgun here.** This repo has
   been bitten twice by confident-but-unverified claims (CLAUDE.md). Two of the
   highest-leverage items below cannot be "solved" by analysis: ACR's PV2012
   develop-op order is **closed-source** (§2.A), and whether the perceptual master
   should carry creative ops at all is a **product decision, not a technical one**
   (§3.B fork). A world-class answer ends in gates + a decision the owner must make.
3. **The crux is ACR's op order — now PARTIALLY resolved.** The audit's F1/F4 and
   DECISIONS §10 hinged on "Adobe applies Basic tone scene-linear before the tone
   curve." That is now **primary-sourced for the DNG *profile* chain** but
   **remains closed for ACR's PV2012 *develop* panel** (§2.A). Half the reorders
   stay gated proposals — by evidence, not timidity.

---

## 1. The headline answer — what the order/domain audit MISSED

The order/domain lens is **structurally blind to four whole categories**. These are
the rocks, ranked by impact on the project's real goals:

| # | Missed category | Why the audit couldn't see it | Severity |
|---|---|---|---|
| **M1** | **Entire missing op-subsystems** — local/masked adjustments, lens corrections (DNG OpcodeList3), noise reduction, sharpening (silent no-op), crop/geometry, Dehaze | An order/domain audit reviews *ops that exist*; it cannot flag *ops that were never implemented*. | **HIGH** — incl. **silent** drops (honesty-invariant breach) |
| **M2** | **Demosaic = bilinear (`LINEAR`)** — a deliberate `dng_validate`-match that caps image quality ≥5.5 dB below SOTA and is a hidden source of the north-star **edge** residual | "Order/domain" doesn't audit *algorithm quality*; bilinear is correctly-placed but low-quality. | **HIGH** for the north-star |
| **M3** | **Highlight recovery runs POST-demosaic** (SOTA = on the mosaic) | The audit checked the *colour domain* (pre-WB — correct) but not the *demosaic timing* (wrong). | MEDIUM |
| **M4** | **Temporal coherence** — temporal NR, chromatic deflicker, flicker metrics — for a *timelapse* tool the audit examined frames in isolation | Per-frame order/domain has no temporal axis. | **HIGH** on the master; backstopped on the TIFF |
| **M5** | **The perceptual master's *whole-stack* coherence** — RGC placement non-canonical (*verify, not defect* — §3.B.3), creative ops baked into interchange data, ACEScg-vs-ACES2065 | The audit verified each perceptual op was internally correct (ProPhoto-in/out, no-top-clamp) but never asked *should these be baked into a master at all*. | **HIGH** (challenges v0.9 scope; mostly opinion → verify) |
| **M6** | **Zero external grounding** — no raw-pipeline/colour-science literature | The audit was 100% internal (code + project docs). | process |

These are detailed as gaps in §3 and turned into plan items in §5.

---

## 2. The bifurcated world-class spec (grounded, cited)

### 2.A Path A reference — *"reproduce Adobe PV2012"* — and the closed-source ceiling

**Primary-sourced (the DNG *profile* chain).** The Adobe DNG SDK `dng_render.cpp`
and the DNG 1.6/1.7 spec pin the profile-stage order exactly, matching this repo's
Stages 2–9:

- Spec, ProfileLookTableData: the LookTable *"should be applied later in the
  processing pipe, **after any exposure compensation and/or fill light stages, but
  before any tone curve stage**."* → **exposure is before the tone curve** [SPEC].
- Spec, HueSatMap: applied *"after the camera colors have been converted to XYZ
  (D50)"* [SPEC].
- `dng_render.cpp::ProcessArea` execution order: **WB → HueSatMap → Exposure(ramp)
  → LookTable → ProfileToneCurve → encode** [SDK] — identical to our Stage 2→9.
  *Caveat:* the public mirror is a ~DNG-1.4-era SDK (the spec text cited is DNG 1.6);
  "this order has been stable since the LookTable shipped in DNG 1.3" is a *reasonable
  inference*, not verified against the 1.7.1 SDK the project targets.
- **New detail we never modelled:** in the reference renderer **positive** exposure
  is a ramp with a *rounded highlight shoulder + black intercept*; **negative**
  exposure is **folded into the tone curve** (`dng_1d_concatenate`), *not* a linear
  multiply. So even Adobe's reference "exposure" is not a clean scene-linear gain.
  Sources: [DNG 1.6 spec PDF](https://paulbourke.net/dataformats/dng/dng_spec_1_6_0_0.pdf),
  [dng_render.cpp mirror](https://github.com/aizvorski/dng_sdk/blob/master/source/dng_render.cpp),
  [McGuffog, "What are DNG Camera Profiles?"](https://mcguffogco.freshdesk.com/support/solutions/articles/8000049771-what-are-dng-camera-profiles-).

**Closed / unverifiable (ACR's PV2012 *develop* panel).** The reference renderer
has **no** Contrast/Highlights/Whites/Blacks PV2012 ops — only the legacy
Exposure/Shadows baseline params. The **position of the PV2012 Basic sliders, the
point Tone Curve, HSL, Color Grading, and Clarity/Texture/Dehaze relative to each
other is NOT in any primary source** — community lore only. **Verdict:** "Exposure
is scene-linear pre-tone-curve" is *established for the dng_validate path*,
*unverifiable for ACR's develop engine*. **Path A's spec therefore has a hard
ceiling: we can faithfully reproduce the profile chain, but the develop-op order is
a reverse-engineering target, never a primary-sourced one.** This is the
evidence that keeps the headroom reorder (§10) a gated proposal.

**The rest of a world-class path-A render that Adobe does and we don't (M1/M2):**
high-quality demosaic, default capture sharpening, luminance+colour NR, lens
corrections (vignette/distortion/CA), local masked adjustments, crop. The LRT JPG
north-star contains *all* of these.

### 2.B Path B reference — *"world-class scene-referred master"*

Consensus, primary-sourced:

- **Keep everything scene-linear; defer the display-rendering-transform (DRT) to
  the very end / to the colorist.** A delivered **master must NOT bake a view
  transform** — the colorist applies the RRT/ODT/DRT per target in their timeline
  ([Brejon ACES](https://chrisbrejon.com/cg-cinematography/chapter-1-5-academy-color-encoding-system-aces/),
  [acescentral output-transforms](https://docs.acescentral.com/system-components/output-transforms/)).
  The project already ships DRT-free — **correct**.
- **A true interchange master is ACES2065-1 (AP0) + `acesImageContainerFlag`**, not
  ACEScg (ACEScg is the *working/render* space). The allowlist supports aces2065 but
  the masters default to ACEScg — a defensible-but-non-canonical choice (Brejon).
- **Modern gamut mapping is increasingly *integrated into the DRT* (ACES 2.0's JMh
  chroma-compression / path-to-white), rather than a separate LMT.** ACES RGC's
  *designed placement* is **input-side** — *"first in the list of any LMTs"* right
  after the IDT, healing camera→ACES excursions
  ([RGC spec](https://docs.acescentral.com/rgc/specification/)). **Important
  calibration (corrected after over-claiming):** "input-side is the *recommended*
  placement" is **not** the same as "downstream use is *invalid*." RGC is a
  gamut-compression operator (compress channel-distances beyond threshold toward
  achromatic); the math is **provenance-agnostic** — it works on out-of-AP1 values
  whether they came from an IDT or a grade op. So a *terminal* RGC catching grade-op
  excursions is **non-canonical but coherent**, not a spec violation (see §3.B.3 for
  the real open question). **ACES 2.0 has shipped** (OCIO 2.5.0, configs 4.0.0,
  Resolve 20 beta;
  [acescentral](https://acescentral.com/exciting-news-aces-2-0-release-for-end-users/)).
- **Scene-referred creative-op consensus:** per-channel tone curves cause the
  "notorious six" hue skew; hue-preserving ratio ops are correct
  ([Brejon OCIO](https://chrisbrejon.com/articles/ocio-display-transforms-and-misconceptions/),
  [AgX](https://avidandrew.com/agx-color.html)) — the project's perceptual ops already
  follow this.

### 2.C Timelapse reference (the axis the audit lacked)

- **Deflicker** = global per-frame brightness correction with a temporal smoothing
  window; LRT's is multi-pass, lossless, **luminance-only**
  ([LRTimelapse workflow](https://lrtimelapse.com/workflow/visual-workflow/),
  [Flicker Free](https://digitalanarchy.com/flicker-free/)). A global EV delta is the
  industry-standard *core* but is insufficient alone for **chromatic** flicker and
  residual **local** flicker.
- **Temporal NR** (motion-compensated, multi-frame) is the defining timelapse
  subsystem; naive per-pixel temporal averaging blurs motion → needs optical-flow
  compensation ([Rubinstein, MIT, *Motion Denoising for Time-lapse*](https://people.csail.mit.edu/mrub/timelapse/)).
- **Validation metric:** warping error **E_warp** (optical-flow warp Oₜ→Oₜ₋₁ +
  occlusion mask) is the academic standard that separates flicker from real motion
  ([Lai/Huang ECCV 2018](https://openaccess.thecvf.com/content_ECCV_2018/papers/Wei-Sheng_Lai_Real-Time_Blind_Video_ECCV_2018_paper.pdf),
  [Lei *Deep Video Prior*](https://arxiv.org/pdf/2010.11838)).

---

## 3. Gap analysis — current vs spec, per path

### 3.A Path A (faithful sRGB TIFF) gaps vs "reproduce Adobe + match the LRT JPG"

Ranked by leverage on the north-star residual (median ΔE ~1.7, edges ~4):

1. **Deflicker units (already F3).** `LocalExposure2012` applied 1:1; measured ~3×
   under-delivery (units, not domain). The dominant per-frame brightness gap.
2. **Demosaic = bilinear (M2, NEW).** `DemosaicAlgorithm.LINEAR`
   ([pipeline.py:422](../../src/lrt_cinema/pipeline.py:422)) chosen to match
   `dng_validate` — the *retired* target. The north-star JPG used ACR's proprietary
   high-quality demosaic. Bilinear's edge softening + false colour is a **plausible,
   unmeasured contributor to the "edge residual" the order/audit blamed solely on
   sharpening** — a double-miss. RawPedia rates bilinear "low quality… not
   recommended"; Malvar reports ~5.5 dB over bilinear for even gradient-corrected
   linear ([RawPedia Demosaicing](https://rawpedia.rawtherapee.com/Demosaicing),
   [Malvar ICASSP'04](https://web.stanford.edu/class/ee367/reading/Demosaicing_ICASSP04.pdf)).
3. **Sharpening = silent no-op (M1, NEW).** `apply_sharpness` returns input
   unchanged ([develop_ops.py:766](../../src/lrt_cinema/develop_ops.py:766)),
   parsed+threaded but **not in `_warn_dropped_ops`** — the cleanest dangerous silent
   drop. ACR bakes default capture sharpening into the JPG; its absence is the *other*
   half of the edge residual.
4. **PV2012 tone-shape op (§11)** — the static shoulder/toe; now known to be a
   look-match only (§2.A: PV2012 math closed).
5. **Local/masked adjustments (M1, NEW, big).** The parser extracts **only** the
   HG/Deflicker/Global internal corrections' `LocalExposure2012` as a **global**
   scalar EV; **mask geometry is discarded**, **user masks ("LRT Mask 1-6") skipped**,
   and `LocalContrast2012 / LocalClarity / LocalDehaze / LocalTemperature / LocalTint`
   on every correction are **never read**
   ([xmp_parser.py:309-360](../../src/lrt_cinema/xmp_parser.py:309)) — silently.
   Real LRT *animates* user masks; this is a core LR subsystem fully absent.
6. **Lens corrections / DNG OpcodeList3 (M1, NEW).** The render reads **no**
   OpcodeList (vignette GainMap, WarpRectilinear distortion, CA) — grep finds zero
   opcode refs in the render path. Vignette/distortion mismatch vs any real LR render.
   *Open verification:* whether dnglab/libraw bakes any opcode at *decode* is
   unconfirmed (a quick check item).
7. **Noise reduction — absent (M1).** No `LuminanceSmoothing`/`ColorNoiseReduction`;
   high-ISO night frames render noisier than the LR preview.
8. **Crop/geometry, Dehaze, post-crop vignette, grain — never parsed (M1).** Dehaze
   isn't even warned (unlike H/S/W).

### 3.B Path B (ACEScg master) gaps vs "world-class scene-referred master"

> **Confidence label (added after the advisor flagged asymmetric skepticism).** Unlike
> §2.A (primary-sourced from the DNG spec + SDK), most of §3.B rests on **sub-agent
> research synthesis + best-practice *advocacy*** — it must be **verified before
> acting**, not treated as established fact. The minimalist "a master should carry no
> baked creative ops" position in particular is a *defensible opinion that directly
> contradicts the project's deliberate §7 purpose* (demonstrate better-than-Adobe
> primitives). Only **F2b** (item 1) is a code-verified defect at this confidence; the
> rest are open questions framed as the fork D1.

1. **F2b (already found, code-verified) — `cinema-linear-finished` is tap-9 yet PERCEPTUAL-default**
   → scene-referred ops on tone-curved/clamped data. Established defect.
2. **THE FORK (M5, NEW, decision-grade).** R4's minimalist argument: *a world-class
   master is CLEAN scene-linear ACES2065-1 with **no baked creative ops** — every
   perceptual op (DR-compression's fixed-0.18 global tonemap, OKLCh HSL, ASC-CDL,
   Texture/Clarity) pre-empts decisions Resolve's ACES 2.0 DRT + colorist make
   better, reversibly, in-context.* This **challenges the entire v0.9 perceptual
   investment**. BUT the project's stated §7 purpose is to *demonstrate what an Adobe
   dependence leaves on the table* — i.e. the ops are an **argument/demo**, not
   necessarily a colorist's preferred master. **These are two different products; the
   owner must choose** (§5 decision D1). Whichever: the ops being "internally correct"
   (the order/audit's verdict) does not make baking them into interchange data
   correct.
3. **RGC placement is non-canonical — VERIFY, don't assume defect (M5; *corrected
   down* from "off-spec").** RGC is applied at the **end** to compress the project's
   **own grade-op** out-of-AP1 excursions; RGC's *recommended* placement is input-side.
   But the operator's compress-toward-achromatic math is **provenance-agnostic**, and
   PIPELINE.md §7 shows the project chose the terminal placement **deliberately**
   ("general gamut safety, not intent-gated; gated on actual out-of-AP1 content") — so
   this is a **coherent, documented choice, NOT a proven defect** (the original
   "off-spec → remove" framing was a sub-agent opinion over-promoted; corrected). **The
   real open question:** does ACES 2.0's *integrated* DRT gamut-mapping (path-to-white /
   chroma-compression) make a terminal RGC in the master **redundant or double-
   compressing** when the colorist's timeline is ACES 2.0? **Verify that empirically
   (render a graded master, round-trip through an ACES 2.0 timeline, inspect for
   double-compression) before changing anything.** Re-examine §7 only if the test shows
   harm.
4. **Master encoding (M5).** Prefer **ACES2065-1 (AP0)** for the interchange master.
5. **Temporal NR (M4, NEW) — the #1 pixel-domain gap on the master** (no safety net;
   the TIFF path is backstopped by LRT's re-deflicker + Motion-Blur frame-blending).
6. **Highlight recovery → mosaic + Tier-2 (M3).** The master is where recovery pays
   off (tap-7 preserves headroom); doing it on the mosaic (pre-demosaic) avoids the
   bilinear clip-smear the code currently compensates for with a ~1% threshold drop.
7. **Chromatic deflicker (M4)** — LRT deflicker is luminance-only; colour shimmer goes
   live on the master.

### 3.C Cross-cutting

- **Silent drops breach the project's own honesty invariant** (DECISIONS §5/§9:
  "surfaced, never silent"). Sharpening, local masks, opcodes, NR, crop, Dehaze are
  all dropped with **no render-time warning** (`_warn_dropped_ops` covers only
  H/S/W/Texture/Clarity, [cli.py:384-437](../../src/lrt_cinema/cli.py:384)). The
  **cheapest high-value fix in the whole doc** is to widen the warning surface.
- **Validation framework gaps (M6):** no temporal-coherence metric (E_warp), no
  perceptual/observer check for the look, no gamut-volume metric for the master.
- **Precision:** float32 working space is fine; the **16-bit sRGB-TIFF quantize after
  the OETF** is the only banding exposure for heavily-lifted day↔night shadows — the
  float16 EXR master is the safer deep-shadow target.

---

## 4. Adversarial meta-critique — where the prior order/domain audit was WRONG or imprecise

Ruthless self-assessment (not just "what it missed" — where it *erred*):

1. **Over-flagged F8 (`scene_kelvin=5500`).** Traced precisely now: the production
   XMP sets `crs:Temperature=4034` → `temperature_k` → `scene_kelvin=4034`, ASN
   re-derived (**coupled**); and for **D750 Camera Standard** scene_kelvin affects
   **nothing** (FM1==FM2 passthrough → blend skipped; no HueSatMap). So F8 is **inert
   on the actual production profile** and moot whenever Temperature is authored. It is
   real only for the Adobe-Standard/rose path with no Temperature. F8 should be
   downgraded from "finding" to "latent, profile-specific footnote."
2. **The "display-referred-in-the-middle = the disease" framing (my pre-commit
   hypothesis) is WRONG for path A.** A sourced sub-investigation showed path A's
   mid-pipeline tone curve is **correct-by-design** (it mirrors Adobe; the 0.026 ΔE
   *with* the clamp proves it), not an anti-pattern. The clamp findings (F1/F2/F2b)
   are real, but the *root cause* is **path B contamination by path A's clamps** and
   the **closed develop-op order**, not a universal architectural defect. My instinct
   to generalize "go scene-referred" was the exact category error §0.1 warns against.
3. **F1/F4 "ACR order UNVERIFIED" is now too coarse.** Sharpen to: the **profile
   chain** (HSM→Exposure→LookTable→ToneCurve) is **primary-sourced [SPEC/SDK]** — so
   the *direction* of F1 (exposure belongs before the tone curve) is **validated for
   the reference renderer**; but ACR's **PV2012 develop-panel** order remains closed,
   so the *develop-op* reorder (§10) stays gated. The audit treated both as equally
   unverified; they are not.
4. **Missed the highlight-recovery POST-demosaic timing defect (M3).** The audit
   called Stage 1.5 "correctly placed pre-WB" — correct on the *colour* axis, but it
   never asked whether *post-demosaic* is the right *spatial* stage. SOTA says recover
   on the mosaic; the code's own threshold-lowering comment is self-evidence.
5. **Verified the perceptual ops at the op level, missed the master level (M5).** The
   audit's "all perceptual ops CONFIRMED-CORRECT (ProPhoto-in/out, no-top-clamp)" is
   true *and* incomplete: it never questioned whether a master should carry them, or
   that RGC is used off-spec.
6. **Structurally internal (M6).** Every audit citation was code or project docs;
   none was the DNG spec, the SDK, or the raw-pipeline literature — which is why the
   ACR-order question sat "unverified" when the spec actually answers half of it.

**What held up:** F1 (the clamp chain — extended to 3 clamps), F2/F2b (perceptual
overrange/tap-9), **F3 (deflicker = units not domain — fully reconfirmed)**, the
bifurcation principle, and the highlight-recovery *colour-domain* (pre-WB,
no-magenta) verdict (SOTA-confirmed).

---

## 5. Critique of the comparison itself + the decisions it forces

Weaknesses on the **spec side** (where "world-class" is wrong or under-determined
for *this* project):

- **Generic scene-referred ideal mis-condemns path A** (caught in §0.1/§4.2). The
  spec must stay objective-relative.
- **The minimalist-master argument vs the §7 demo purpose is a genuine fork, not a
  technical verdict (D1).** "Strip the perceptual ops" is right *if* the master is a
  clean interchange file; wrong *if* its purpose is to demonstrate better-than-Adobe
  primitives. Analysis cannot resolve this — the owner must.
- **The demosaic upgrade moves path A *away* from the `dng_validate` 0.026
  tripwire.** That is *allowed* (the tripwire is a regression sentinel, not the
  north-star — DECISIONS §9) but requires **re-baselining the tripwire** and judging
  the A/B against the **LRT-JPG north-star**, not dng_validate. Don't let the 0.026
  footgun veto a real quality win.
- **Nothing here was run.** No SDK, no render fixtures locally. Every item is a
  hypothesis with a named gate.

**Decisions the owner must make (cannot be analysed away):**
- **D1 — Master identity:** clean minimal ACES2065-1 master (strip/optional perceptual
  ops, fix RGC) **OR** "better-primitives demo" master (keep ops, re-frame honestly,
  fix RGC misuse). Drives most of §3.B.
- **D2 — Sharpening policy:** DECISIONS says sharpening "belongs at the grade," but the
  north-star JPG *has* ACR sharpening baked. To *match the look* on path A you must
  bake some; to stay a clean intermediate you must not. Pick per-path (likely: bake on
  the sRGB TIFF round-trip, none on the EXR master).
- **D3 — Local-mask scope:** full spatial local-adjustment subsystem is a large build;
  decide between (a) implement, (b) warn-and-drop honestly now + implement later.

---

## 6. Prioritized, validation-gated implementation plan

Ordered by (impact on the real goal ÷ cost), with the gate that must pass.

### Tier 0 — honesty + cheap, no algorithm risk (do first)
1. **Widen `_warn_dropped_ops` to every silent drop** (sharpening, user/local masks +
   mask geometry, OpcodeList lens corrections, NR, crop, Dehaze). *Gate:* inspect a
   production XMP; every unapplied authored op is now warned. Restores the §5/§9
   honesty invariant. **Cheapest high-value item in the doc.**
2. **Downgrade F8** in the order/audit to a profile-specific footnote (§4.1).

### Tier 1 — path A north-star levers (match the LRT JPG)
3. **Deflicker units fix (F3).** Calibrate the `LocalExposure2012→exposure_ev` scale
   (~3×); **pin the factor with a cited LrC units basis — do not hard-code an inferred
   number.** *Gate:* flat per-frame gain across the 5-frame held-out set
   (`tools/diagnose_vs_lrt_preview.py`).
4. **Demosaic A/B (M2).** Add an `RCD`/`AMaZE`-class demosaic for the *delivery* path;
   keep `LINEAR` for the `dng_validate` regression tap only. *Gate:* north-star
   smooth+**edge** residual drops vs LINEAR on aligned held-out frames; re-baseline
   the dng_validate tripwire (deliberate divergence, DECISIONS §9).
5. **Un-stub sharpening for path A (D2).** Implement a capture-sharpening pass mapped
   from `crs:Sharpness`. *Gate:* edge residual drops toward the JPEG floor without
   halo regressions; **path B stays no-sharpen**.
6. **PV2012 tone-shape op (§11),** placed per the now-known profile order. *Gate:*
   held-out-frame smooth residual → ~0.85; no regression on a second aligned frame.

### Tier 2 — path B master correctness (after D1)
7. **Fix F2b** — default `cinema-linear-finished` to FAITHFUL, *or* force tap-7 for any
   PERCEPTUAL render so scene-referred ops never see tone-curved input. *Gate:* the
   0.18 anchor sits at scene midgray on the rendered master.
8. **Resolve D1; if "clean master":** make perceptual creative ops opt-in (off by
   default for the master) and switch the interchange master to **ACES2065-1**. On RGC:
   **verify before removing** — render a graded master, round-trip through an ACES 2.0
   timeline, and remove the terminal RGC **only if** double-compression vs the DRT's
   integrated gamut mapping is measured (it is a deliberate, documented choice, not a
   defect — §3.B.3). *Gate:* the round-trip shows no double-compression / no hard-clip
   regression.
9. **Highlight recovery → mosaic (M3)** + Tier-2 Poisson on un-smeared boundaries.
   *Gate:* the existing fixture-free recovery tests + a mosaic-vs-post-demosaic A/B on
   a clipped frame.

### Tier 3 — temporal subsystem (the missing dimension, master-first)
10. **Motion-compensated temporal NR (M4)** for the EXR master (the unbackstopped
    path). *Gate:* **E_warp** drops on a static-region night clip without motion
    blur; per-frame ΔE unchanged on moving regions.
11. **Chromatic deflicker (M4)** if colour shimmer is measured on the master.

### Cross-cutting
12. **Validation framework:** add an **E_warp** temporal gate and a gamut-volume check
    for the master; keep the dng_validate tripwire + LRT-JPG north-star.
13. **Track ACES 2.0** (shipped) — it changes the "what DRT does the colorist apply"
    assumption behind D1/§3.B.

**Sequencing logic:** Tier 0 is free honesty. Tier 1 attacks the *measured* north-star
gap (deflicker > demosaic ≈ sharpening > tone-shape). Tier 2 waits on the **D1
product decision**. Tier 3 is the genuinely new subsystem, scoped master-first because
the TIFF path is backstopped by LRT.

---

## 7. Sources (primary, by domain)

- **Architecture / scene-referred:** [darktable pixelpipe & module order](https://docs.darktable.org/usermanual/4.8/en/darkroom/pixelpipe/the-pixelpipe-and-module-order/) · [Aurélien Pierre — filmic/HDR](https://eng.aurelienpierre.com/2018/11/filmic-darktable-and-the-quest-of-the-hdr-tone-mapping/) · [Brejon — OCIO display transforms](https://chrisbrejon.com/articles/ocio-display-transforms-and-misconceptions/) · [AgX mechanism](https://avidandrew.com/agx-color.html) · [RawPedia Toolchain](https://rawpedia.rawtherapee.com/Toolchain_Pipeline)
- **Adobe/DNG order:** [DNG 1.6 spec PDF](https://paulbourke.net/dataformats/dng/dng_spec_1_6_0_0.pdf) · [dng_render.cpp mirror](https://github.com/aizvorski/dng_sdk/blob/master/source/dng_render.cpp) · [McGuffog — DNG camera profiles](https://mcguffogco.freshdesk.com/support/solutions/articles/8000049771-what-are-dng-camera-profiles-) · [Adobe community — internal adjustment order](https://community.adobe.com/t5/lightroom-classic-discussions/internal-order-of-adjustments-for-raw-development/td-p/14006382)
- **Demosaic / highlight recon:** [RawPedia Demosaicing](https://rawpedia.rawtherapee.com/Demosaicing) · [RawPedia Exposure / HL recovery](https://rawpedia.rawtherapee.com/Exposure) · [darktable highlight reconstruction](https://docs.darktable.org/usermanual/4.6/en/module-reference/processing-modules/highlight-reconstruction/) · [Malvar ICASSP'04 (~5.5 dB)](https://web.stanford.edu/class/ee367/reading/Demosaicing_ICASSP04.pdf) · [Demosaicing survey (PolyU)](https://www4.comp.polyu.edu.hk/~cslzhang/paper/conf/demosaicing_survey.pdf) · [Eilertsen 2017](https://arxiv.org/abs/1710.07480)
- **ACES master / gamut / DRT:** [ACES RGC spec](https://docs.acescentral.com/rgc/specification/) · [ACES 2.0 output transforms](https://docs.acescentral.com/system-components/output-transforms/) · [ACES 2.0 release](https://acescentral.com/exciting-news-aces-2-0-release-for-end-users/) · [Brejon — ACES (masters = ACES2065-1)](https://chrisbrejon.com/cg-cinematography/chapter-1-5-academy-color-encoding-system-aces/)
- **Timelapse / temporal:** [LRTimelapse visual workflow](https://lrtimelapse.com/workflow/visual-workflow/) · [Flicker Free](https://digitalanarchy.com/flicker-free/) · [Rubinstein — Motion Denoising for Time-lapse (MIT)](https://people.csail.mit.edu/mrub/timelapse/) · [Lai/Huang — blind video temporal consistency, ECCV'18](https://openaccess.thecvf.com/content_ECCV_2018/papers/Wei-Sheng_Lai_Real-Time_Blind_Video_ECCV_2018_paper.pdf) · [Lei — Deep Video Prior](https://arxiv.org/pdf/2010.11838)

---

## 8. Honesty notes

- **Nothing was run** (no SDK source, no render/ΔE fixtures locally). Every
  recommendation is a hypothesis + a named gate.
- **Closed-source ceilings, stated:** ACR's PV2012 develop-op order and demosaic
  algorithm are proprietary; path-A fidelity beyond the profile chain is
  reverse-engineering, validated by ΔE against the LRT JPG, never by Adobe internals.
- **Open verification items:** (a) does dnglab/libraw bake any DNG OpcodeList at
  decode (lens-correction gap depends on it); (b) the deflicker ~3× factor's exact
  LrC units basis; (c) the demosaic-vs-sharpening share of the edge residual (the A/B
  resolves it). Each is a one-experiment question, not an inference to settle on paper.
