# DECISIONS.md — binding decisions log

The hard-won decisions that shaped lrt-cinema, each with a one-paragraph
rationale, so they are **not re-litigated**. This is a decisions *log*, not a
research dump. The raw research that produced these verdicts (the `v06`/`v07`/
`v08`/`v09` series, the `color-option-space` set, the superseded emission
records) was archived and removed in the Phase-4 doc reduction; recover any of it
from git tag **`phase4-research-archive`** (e.g. `git show phase4-research-archive:<path>`).

These research docs are **live authorities** and are cited below (the `v06`–`v08`
series was otherwise archived in the Phase-4 cull):
- [`research/v08-linear-exr-gamut-resolve-nuke.md`](research/v08-linear-exr-gamut-resolve-nuke.md)
  — the colour-space allowlist authority (on-box Resolve verification).
- [`research/v08-synthetic-chromatic-rootcause.md`](research/v08-synthetic-chromatic-rootcause.md)
  — the 2026-05-30 chromatic-fix trace.
- [`research/v09-perceptual-grading-frontier.md`](research/v09-perceptual-grading-frontier.md)
  — the dual-mode grading authority (§7): perceptual-space candidates + the
  measurable-"better" axes.

Canonical companions: [`PIPELINE.md`](PIPELINE.md) (the as-built engine),
[`VALIDATION.md`](VALIDATION.md) (the three validation axes),
[`LRT_ROUNDTRIP.md`](LRT_ROUNDTRIP.md) (the default-emission contract),
[`../CLAUDE.md`](../CLAUDE.md) (the index of invariants).

---

## 1. Emission format — sRGB TIFF default; ACEScg EXR opt-in; linear Rec.2020 forbidden

**Decision.** The default emission is an **LRT-ingestible 16-bit sRGB display
TIFF** (`LRT_00001.tif…`, embedded sRGB ICC, full LRT look baked) for the
LRTimelapse round-trip. **Scene-linear ACEScg (AP1) OpenEXR** is the opt-in
master for the DaVinci Resolve / ACES path. **Linear Rec.2020 is forbidden.**

**Why.** The binding constraint is LRT's video renderer: *Render from
Intermediate* accepts **JPG or TIFF only — EXR and DNG are rejected**. To stay
inside the LRT ecosystem (back into LRTimelapse for its **Motion Blur** + final
deflicker), the deliverable must be a TIFF; 16-bit (vs LRT's 8-bit-JPEG internal
export) is the whole point of replacing the internal path; the embedded sRGB ICC
removes the colour/gamma-shift ambiguity that bites untagged/wide-gamut TIFFs;
and the exact `LRT_NNNNN` 5-digit, 1-based naming is what LRT requires to
recognise the folder. ACEScg EXR is **not** the default because it targets a
*different* downstream (Resolve/ACES, no LRT Motion Blur) and LRT rejects EXR on
ingest anyway — but it is the correct Resolve master because it carries the
project's validated clean-room colour science (round-trip verified in Resolve
Studio 21 at mean ΔE2000 0.64 via the named "ACEScg" Input). The two streams are
a deliberate split, not a ranking. **Linear Rec.2020** is excluded because
Rec.2020 (ITU-R BT.2020) is a *delivery/display* gamut; using it scene-referred
is the "Franken-gamut" misuse — Resolve has **no** matching clip Input entry for
"Linear/Rec.2020" (only the gamut-agnostic "Linear", which inherits the timeline
gamut), so it works only by coincidence and self-documents nothing. ACEScg (AP1)
is the standards-aligned scene-referred working gamut (positive primaries,
energy-conserving, ⊃ Rec.2020); ACES2065-1 (AP0) is the archival variant. Full
gamut/tagging evidence (Resolve does **not** read the EXR `chromaticities`
header; that tag is documentation-only) in the kept authority
[`research/v08-linear-exr-gamut-resolve-nuke.md`](research/v08-linear-exr-gamut-resolve-nuke.md);
the round-trip contract in [`LRT_ROUNDTRIP.md`](LRT_ROUNDTRIP.md).

**Rejected alternatives** (one line each):
- **8-bit sRGB JPEG** (LRT's internal export) — the thing being replaced; 8-bit
  ceiling, gradient banding.
- **CinemaDNG / Linear DNG as default** — colour delegated to Resolve's bundled
  DCP, not our validated science (see §3).
- **Linear Rec.2020 EXR/TIFF** — Franken-gamut; no Resolve Input entry (the
  removed `cinema-linear` / `cinema-aces` mistake).
- **Adobe Lightroom / ACR-routed emission** — rejected on principle; the repo is
  Adobe-free (see §2).
- **ProRes RAW / Blackmagic RAW** — no open encoder (BRAW also EULA-barred).
- **JPEG-XL / AVIF / HEIF** — no DaVinci Resolve ingest.

---

## 2. Adobe purge — dnglab-sole converter; open-DCP only; dng_validate is a test-only oracle

**Decision.** The runtime is **fully Adobe-free**. **dnglab** (open-source,
LGPL-2.1) is the **sole** RAW→DNG converter. Profiles resolve only from open
`.npz`/`.dcp` roots — the runtime **never scans an Adobe install**. Adobe's
`dng_validate` reference renderer and any system `.dcp` profiles are **test-only
oracles**, never a runtime dependency.

**Why.** dnglab is a verified drop-in for the Adobe DNG Converter (dnglab-DNG vs
Adobe-DNG on the same pipeline+DCP = mean ΔE 0.059, 100 % < 1 ΔE) and ships
Linux/macOS/Windows builds (Adobe never shipped Linux), so nothing of value is
lost by removing the Adobe binary discovery and the `$LRT_CINEMA_DNG_CONVERTER`
fallback. The `dng_validate` north-star (mean ΔE2000 < 1.0) is preserved by
keeping it as a *comparison oracle in the test suite* — it gates colour-science
correctness without putting Adobe on the user's critical path. `--no-dng-convert`
remains a libraw-direct fallback for hosts with no dnglab binary (≈0.5 ΔE
regression). Caveat that downstream colour code must honour: **dnglab strips the
ForwardMatrix** when it builds its uncompressed clone — see
[`PIPELINE.md`](PIPELINE.md) §3 and
[`research/v08-synthetic-chromatic-rootcause.md`](research/v08-synthetic-chromatic-rootcause.md).

---

## 3. CinemaDNG as the default emission — REJECTED

**Decision.** CinemaDNG (and Linear DNG) are **not** the default emission.
Resolve's CDNG decoder forfeits our colour pipeline to its own bundled DCP. CFA
CinemaDNG may return later only as an *optional* max-recovery preset (needs a
`cdng_emit` writer + per-camera colour characterisation), never the default.

**Why.** The empirical spike (Resolve 20.3, 2026-05-27) found that only
`AsShotNeutral` and `BaselineExposure` survive as per-frame DNG metadata; the
colour-defining DCP fields do **not**. Resolve "ignores the file-level tag
entirely" — `ProfileToneCurve`, `ProfileLookTableData`, and `OpcodeList3.GainMap`
are not a viable carrier for develop intent. The decoder loads a *bundled* DCP
from its own `CameraProfiles` directory keyed on the source DNG's EXIF
Make/Model, bypassing the emitted colour pipeline (cross-checked: macOS Quick
Look honoured the file-level `ProfileToneCurve`, Resolve did not). Adopting CDNG
as default would therefore drop the per-frame tone/saturation ops
(`ToneCurvePV2012`, `Contrast2012`, `Saturation`, `Vibrance`) — the same loss as
the closed-source PV5 ops (§5). ACEScg EXR (§1) carries our colour science
intact and is smaller, so it dominates CDNG/Linear-DNG for the grading master.

---

## 4. β-XML Resolve sidecar (per-frame keyframe carrier) — DEAD-END

**Decision.** Emitting a DaVinci Resolve project/XML sidecar to carry LRT's
per-frame develop keyframes (Holy-Grail exposure ramps etc.) into Resolve is a
**dead-end**. Re-open only if a genuinely new carrier format surfaces.

**Why.** Verified against the DaVinci Resolve 20 Reference Manual and the Resolve
Studio scripting API (2026-05-28): **per-frame grade keyframes do not survive any
documented Resolve project-import path.** The only route that imports colour data
is FCPXML, but imported corrections land in the Color page "as primary
corrections" — a single static node per clip, with the FCPXML keyframe time-track
flattened to the first-frame value. The scripting API exposes only static grade
writes (`SetCDL`, `SetLUT`); there is **no** `SetKeyframe(param, frame, value)`
setter (the sole keyframe-capable call, `ApplyGradeFromDRX`, needs the binary,
undocumented `.drx` format). A sub-clip split can fake stepped grades but never
reaches Resolve's smooth per-frame interpolation — unacceptable at LRT's typical
keyframe density. The pragmatic substitute that *did* ship is the Stage-7 EXR
emission point (`cinema-linear-master`, HDR headroom) **without** the sidecar.

---

## 5. Dropped develop ops — PV5 basic tone + Dehaze (warn-only); Sharpness no-op

**Decision.** Lightroom **PV5 basic-tone** ops (`Highlights`, `Shadows`,
`Whites`) and **Dehaze** are **permanently dropped** at render and surfaced as
**render-time warnings** (never silently hidden). **Sharpness** is a deliberate
no-op stub. Smooth/Catmull-Rom keyframe interpolation was deleted (defer to LRT
Auto-Transition).

**Why.** The PV5 parametric-tone math and Dehaze are **closed-source** — there is
no public formula to clean-room, and guessing would inject uncontrolled colour
error into a pipeline whose whole value is < 1 ΔE fidelity. Surfacing them as
warnings (`cli.py inspect` counts non-zero keyframes over
`_DROPPED_AT_EMIT_FIELDS`) keeps the omission honest rather than producing a
silently-wrong render. Sharpening belongs at the grade stage on the colorist's
calibrated monitor, not baked into a deliverable intermediate, so `apply_sharpness`
is intentionally a no-op. (Note: `pipeline.py`'s `shadows` parameter is the DCP
black-render scalar — unrelated to PV5 `Shadows`.) See [`PIPELINE.md`](PIPELINE.md)
§9.

**Amendment (2026-05-31) — Highlights/Shadows/Whites reopened on the PERCEPTUAL
path; the FAITHFUL path is unchanged.** The *capability* these knobs gesture at
— **surgically compress a large dynamic range while retaining local/perceived
contrast** (the user's most-used tool, esp. day↔night timelapses) — is reopened
on the **perceptual** render-intent (§7), where no Adobe fidelity is owed and a
measurably-better open operator can ship. It is implemented as a **scene-referred
local DR-compression op driven by the *existing* `crs:Highlights2012` /
`crs:Shadows2012` / `crs:Whites2012` XMP knobs** — **no new control, no CLI
grade; all creative values come from the LR/LRT sliders the user already sets** —
built as the base-attenuation mode of the **shared edge-aware base/detail engine**
(Local Laplacian fast variant / guided filter; the same engine as Texture/Clarity,
§7). On the **faithful** path these ops stay **dropped + warn-only**: Adobe's math
is closed and **un-fittable** from the flat-patch grading-sweep harness (Door B
rejected — see [`research/v10-local-tone-mapping-dr-compression.md`](research/v10-local-tone-mapping-dr-compression.md)
§4). The drop is now surfaced at **render time** (`cli._warn_dropped_ops` — per
field + frame count, with a pointer to better-math), not only via `cli inspect`.
"Better" is the **measurable** set only (DR-compression, local-contrast retention,
halo/gradient-reversal, temporal coherence) — **not** an aesthetic claim (no
observer panel). This does **not** re-introduce Adobe's closed math; it ships an
independent open operator. The central open derivation (the scene-referred
base-attenuation law around a fixed log anchor, no display clamp) precedes the op
— full method/ranking/sources in the v10 research doc.

**Resolved + shipped (2026-05-31) — the law is settled and the op landed
(`develop_ops.apply_dr_compression`, PERCEPTUAL-only).** The open derivation is
closed by [`research/v10b-scene-referred-compression-law.md`](research/v10b-scene-referred-compression-law.md):
a homomorphic **log-domain** compression of luminance toward the fixed scene-linear
**0.18 anchor** (the log sibling of `apply_contrast_2012`, same pivot, same
floor-at-0, **no ceiling**). The three sliders force an asymmetric **3-slope**
curve — Shadows→below-anchor `c_lo`, Highlights→upper-mid `c_hi`, Whites→extreme-top
`c_top` (`slope = 2**(−k·s/100)`; `c_top` a third log slope, **never a clipping
shoulder**, so overrange survives every Whites setting) — **C1**-blended (smoothstep)
at the anchor join and the high breakpoint. Applied **locally** (guided-filter
base/detail split, He 2013 — the lightweight first cut; local Laplacian is the
quality follow-up) so local contrast is retained; §0-safe via luminance + **out/in
luminance-ratio** reapply (never per-channel), floored at 0 with **no top clamp**.
**Byte-exact identity** when all three knobs are 0 → the gym 0.026 / rose 0.545 ΔE
ship gate (faithful, stages 1–9) is untouched. An Axis-1 oracle holds the defined
piecewise-log math + ratio reapply to ~0 (per-channel / flipped-sign / dropped-blend
/ wrong-anchor sensitivity legs). Pinned tuning constants (`k=1`, breakpoint 2 stops,
blend half-widths 0.5 stops, guided r≈8 / ε≈0.01, eps 1e-6) are best-effort, **not**
Lightroom fidelity — the perceptual path makes **no fidelity claim** (notably Whites
*compresses* the top, the inverse of LR). **Remaining follow-ups (out of this op's
scope):** ~~the downstream ACES **RGC** gamut pass in `output.py` for out-of-AP1
excursions~~ (**SHIPPED** — see §7 amendment below); ~~the **local-Laplacian**
halo-free base-producer upgrade~~ (**DEFERRED — escape hatch fired, 2026-05-31**, see
below); ~~**Texture/Clarity** (the boost-detail mode of the same shared engine)~~
(**SHIPPED** — see §7 amendment below).

**Amendment (2026-05-31) — the local-Laplacian base-producer upgrade is DEFERRED; the
guided filter STAYS.** v0.9 step 4 (v10 §3.2) proposed swapping the DR op's
guided-filter base producer for a **fast Local Laplacian filter** (Aubry 2014). A
*correct* clean-room fast LLF was built (real Paris-2011 remap `fd=d^α`/`fe=β·a`, NO
gradient term; base-extractor config α>1, β=1; σr in **log2**; fixed scene-referred γ
grid every σr — no per-frame statistic; display tail discarded) and **verified
faithful** (α=β=1 → identity to 9e-16; edge preserved). Measured on the v10 §1.3
halo protocol (textured step edge through the full op) it **does not beat the shipping
guided filter** (eps=0.01): refining the fast γ grid toward exact makes edge overshoot
**worse, monotonically** (σr→0.060, σr/2→0.088, σr/4→0.098 vs guided's 0.068), and the
**exact** O(N²) LLF is worst (0.112). The coarse-γ "win" is a fast-approximation
artifact, not real. **Two findings:** (a) LLF's halo-free guarantee is a property of
its *integrated tone-map output*, **not** transferable to an LLF *base* feeding an
external compression law — the v10b architecture (fixed log law on a separately
extracted base) severs it; (b) at the shipping eps=0.01 the guided halo is **flat in
radius** (r=8→0.0685, r=16→0.0646), contradicting the v10 premise that motivated the
upgrade. Per the task's escape hatch, **a proven defer beats an unproven base
producer** — the DR op is untouched (`apply_dr_compression`, `_guided_base_log`, the
law). The clean-room LLF prototype is preserved
(`docs/research/_proto_local_laplacian.py`, unwired) for **Texture/Clarity**, where
LLF is used as designed (small-radius detail boost, α<1). Full method + measurements +
honesty caveat (synthetic + exact-method evidence, not real day/night frames):
[`research/v10c-local-laplacian-base-deferred.md`](research/v10c-local-laplacian-base-deferred.md).
**Postscript (2026-05-31, Texture/Clarity shipped):** when Texture/Clarity was built
(§7 step 4 below), the **guided filter beat the LLF proto on the same halo metric for
the boost role too** — a guided two-band detail boost rings sub-1% of the plateau range
at full sliders vs a naive single-Gaussian USM at ~580%, while the LLF proto is
comparable but fragile and costs a non-byte-exact pyramid + its own oracle. So the LLF
proto remains **unwired** (measured-only); Texture/Clarity ships on the guided engine.

---

## 6. Standalone GUI app (LRT replacement) & vkdt engine fork — NO-GO as currently staffed

**Decision.** Building a standalone desktop app to *replace* LRTimelapse — and
the strongest engine path for it, **forking vkdt** (the GPU raw processor) — is
**ON HOLD / NO-GO as currently staffed** (2026-05-29). This is a separate product
(own colour science, C++/Vulkan or Rust/wgpu stack, non-Apache licence) that
would live in its **own repo**, never inside lrt-cinema. The engine fork is *not*
technically refuted — it remains the recommended path **conditional on staffing**.

**Why.** An adversarial sanity-check found the build cannot clear its own bar as
staffed (a non-engineer lead + Claude), because its two hardest parts are exactly
what that pairing cannot reliably deliver: (1) **native-systems engineering** —
vkdt's GPLv2 `src/qvk/` Vulkan bootstrap (~1,254 LOC) **gates every UI path**
(nothing boots, web or Qt, until it is rewritten), plus the GPU↔UI viewport
bridge, MoltenVK validation, and proxy/cache; and (2) **originating** a
class-leading aesthetic. "Mostly Claude" covers only non-critical-path chrome;
the native spine is the majority and has **no screenshot-feedback loop** (a
`VK_KHR_external_semaphore` race *hangs*; a MoltenVK `shader_atomic_float` gap
*silently corrupts HDR* — neither screenshots), so that work is liable to block
outright. Web doesn't rescue it: web reduces design *execution*, not
*origination*, and critique-only steering converges on the model's generic mean.
vkdt is the right engine (node-graph GPU pipeline cleanly decoupled from its
nuklear GUI, BSD-2, proven by its headless CLI), and forking banks the two
highest-risk subsystems — but the de-GPL work is bounded *engineering*, so **a
competent Vulkan/native-systems engineer is required regardless of UI stack.**
From-scratch (Qt/Vulkan or Rust/wgpu) is strictly worse — same web-interop
limits, and it throws away vkdt's banked engine.

**Revisit if** (both gates pass): (1) **Team** — a competent Vulkan/native-systems
engineer is secured (non-negotiable, not Claude-substitutable) **and** design
*origination* is arranged (a 2–4-week designer engagement, or a decision to clone
a proven pro aesthetic wholesale); **and** (2) **Evidence** — the Phase-1
viewport-latency spike has actually been *run*, not assumed. The web-vs-Qt UI
choice is then decided empirically by that spike (web/Electron if proxy-readback
latency is acceptable; Qt/QML zero-copy if not), starting from a licence-clean
snapshot fork of vkdt at 1.0.0 (rewrite qvk behind its `qvk.h` interface;
Makefile → CMake for Windows).

---

## 7. Dual-mode grading — `--render-intent {faithful, perceptual}`; faithful default; modern primitives on the master

**Decision.** The Stage-12 grading ops (HSL, Color Grade, and future
Texture/Clarity) become **dual-mode**, selected by a `--render-intent` switch:
- **`faithful` (default)** — today's Adobe-hexcone HSL + additive split-tone
  Color Grade. Feeds the **sRGB display TIFF** (the LRT round-trip). Its job is
  to reproduce the **Lightroom look** the LRT user authored.
- **`perceptual`** — modern primitives: **OKLCh** HSL (gamut-agnostic,
  D50/~D60→D65 adapted — *not* Okhsl, which is sRGB-gamut-bound), **ASC CDL
  (SOP+saturation)** Color Grade in a log domain, **local-Laplacian /
  guided-filter** Texture/Clarity. Feeds the **ACEScg EXR master**
  (Resolve/ACES).

The op IR dataclasses (`HslBands`, `ColorGrade`) are **shared**; only the
*applicator* branches on intent. Both modes preserve the hard invariants:
zero-slider **byte-exact identity** (the ΔE ship gate is untouched —
[`PIPELINE.md`](PIPELINE.md), [`VALIDATION.md`](VALIDATION.md)); the
**colour-space allowlist** (perceptual spaces are internal *working* transforms —
emission stays sRGB / ACEScg, no new gamut, §1); and **neutrals-passing ≠
correct** (validate the perceptual ops against *saturated* colour). The master's
perceptual ops may exceed AP1 → apply **ACES Reference Gamut Compression** before
the AP1 encode (`colour` 0.4.x has no general gamut compression; use the ACES RGC
CTL or OCIO ≥ 2.1).

**Why.** This maps 1:1 onto the project's **two-fold purpose**. (1) The
sRGB-TIFF path proves an Adobe-free workflow is *feasible inside LRT's current
paradigm* — same look, straight back into LRTimelapse for Motion Blur — so
faithfulness **is** correctness there and the path must not drift from Adobe.
(2) The ACEScg-master path demonstrates what an Adobe dependence *leaves on the
table* — hue-stable HSL (no Abney / Bezold–Brücke drift), standards-native CDL
that round-trips losslessly into a colorist's first node, halo-free local
contrast — the "advantageous, not merely feasible" argument aimed at getting on
the LRTimelapse creator's radar. A single mode cannot serve both: an
all-perceptual pipeline breaks TIFF round-trip fidelity; an all-faithful pipeline
forfeits the Mode-2 advantage that is half the reason this repo exists. "Better"
here is the **measurable** set only (perceptual-uniformity, hue-constancy, gamut,
halos) — not an aesthetic claim (that needs an observer panel we do not have).
Full per-op candidates, metrics, and primary sources in the live authority
[`research/v09-perceptual-grading-frontier.md`](research/v09-perceptual-grading-frontier.md).

**Faithful-path improvement policy** (the one nuance). Research improvements
**do** land on the faithful/TIFF path when they are **compliance-safe** — i.e.
they remove a defect *ours* has that Adobe does **not** (negative ProPhoto
channels, NaN, gamut clipping / posterization on saturated boosts), which moves
the TIFF *toward* the Adobe look, never away. But a **working-domain switch** on
the TIFF (e.g. HSV-hexcone → OKLCh) is **not** made speculatively — it is **gated
on Tier-1 ACR golden-set evidence** (the grading-sweep harness,
`tools/grading_sweep/`) showing the modern primitive is *also more faithful*
(lower ΔE vs ACR across the lever sweep). Until that data exists the TIFF stays
Adobe-hexcone. This keeps the feasibility claim evidence-backed and turns the
fidelity question into a measurement, not a guess.

**Sequencing.** (1) dual-mode scaffold (shared dataclass + intent dispatch
through `cli`/`pipeline`/`develop_ops`); (2) Color Grade → CDL on the master
(lowest risk, native ACES interchange); (3) HSL → OKLCh on the master (+ ACES RGC
pass); (4) Texture/Clarity (local Laplacian) only on demand; (5) TIFF ops stay
faithful and untouched pending Tier-1 ACR data.

**Amendment (2026-05-31) — XMP-driven principle, per-target defaults, render-time
drop policy, and DR-first re-sequencing.**
- **Everything is XMP-knob-driven; `--render-intent` is the *only* mode switch
  and carries NO creative values.** All creative values come from the LR/LRT
  develop sliders in the XMP. Intent selects *which math* implements a knob
  (Adobe-matching vs our better math) — render-wide, set once, like an
  output-quality setting. There is **no CLI grade and no second editing stage**
  (an explicitly rejected design): the user edits in LR/LRT, the renderer reads
  and applies the knobs.
- **Default intent is per emission target** (`cli._default_intent_for_preset`):
  the **sRGB display TIFF (lrtimelapse) → `faithful`** (the LRT round-trip wants
  the Lightroom look); the **ACEScg EXR masters (`cinema-linear-*`) →
  `perceptual`** (no Adobe-fidelity obligation; the path where DR-compression /
  OKLCh / CDL live). `--render-intent` overrides. **Revisit the EXR→perceptual
  default only if** a control-loop mismatch in the LR-edit→render→review loop
  proves untameable.
- **A perceptual-only op (an op with *no* Adobe-matching math: today
  Highlights/Shadows/Whites; later Texture/Clarity) is DROPPED under `faithful`
  with an actionable, per-field, frame-counted RENDER-TIME warning**
  (`cli._warn_dropped_ops`) — never a silent drop, and naming better-math as the
  place it is applied. The "always apply Highlights/Shadows even under faithful"
  hybrid is **rejected**: it would silently make "match Lightroom" *not* match
  Lightroom.
- **Re-sequencing (DR-compression pulled forward — it is the user's #1 need and
  the guaranteed-win demo).** The original step order above optimised for risk;
  the priority order is now: **the shared base/detail engine + the single gated
  RGC pass first, then the DR-compression op** (driven by the Highlights/Shadows
  XMP knobs, §5 amendment), **then** the CDL / OKLCh upgrades and Texture/Clarity
  detail. CDL's RGC pass and the Texture engine are shared infra the DR op
  consumes, so they still land first as *infrastructure* — but the DR op, not the
  HSL/grade upgrades, is the headline deliverable they unblock. **Precondition
  (open, do first):** derive the scene-referred base-attenuation law (§5
  amendment; v10 research §3.4/§6).

**Amendment (2026-05-31) — step 4 Texture/Clarity SHIPPED on the perceptual path
(the last v0.9 dual-mode op).** `develop_ops.apply_texture_clarity`, driven by the
existing `crs:Texture`/`crs:Clarity2012` knobs (no new control), is the **boost-detail
mode of the SAME guided base/detail engine** the DR op uses (the inverse: a two-band
guided split *boosts* detail rather than *attenuating* the base). Texture = a uniform
fine-detail boost (`L−B_fine`); Clarity = a midtone-weighted mid-scale local-contrast
boost (`B_fine−B_coarse`, weighted by a C∞ Gaussian bump around the 0.18 log-anchor).
PERCEPTUAL-only; on faithful it joins the dropped + warn-only set with **its own**
intent-aware wording (`_DROPPED_TEXTURE_CLARITY_FIELDS`, pointing at the local-contrast
op — NOT the DR-compression/closed-PV5 story). §0-safe (luminance + out/in-ratio
reapply, never per-channel), floor 0, **no top clamp** (overrange → the shared gated
ACES RGC pass); byte-exact identity at both sliders 0 (the guided round-trip is not
bit-exact) → the gym/rose ΔE ship gate is untouched. **Engine choice — guided, not the
LLF proto** (the §7-step-4 "local Laplacian" candidate): on the step-edge halo protocol
the guided two-band boost rings **sub-1% of the plateau range at +100/+100** vs a naive
single-Gaussian USM at **~580%** (the op-family's defining failure); the LLF proto is
comparable but fragile + costs a non-byte-exact pyramid and its own oracle, so per the
escape hatch the proven guided engine ships and the proto stays unwired (same direction
as v10c's base-role defer). The guided filter is the **measured-clean first cut, NOT
provably halo-free** (only LLF is); this bounds the measured ring + discriminates the
naive USM, it is not a real-content halo-freedom claim. Constants (`_TC_*`) are
documented tuning; the Axis-1 oracle hand-rolls the two-band guided split + boost +
ratio via `scipy.ndimage.uniform_filter` (a different code path from the production
cumsum box; interior-only, ≥2·r_coarse from borders) — validating the *defined* math,
not LR appearance. **All v0.9 dual-mode steps (1–5) are now complete.**

**Rejected alternatives** (one line each):
- **Single perceptual pipeline** — breaks the LRT round-trip (the TIFF must
  *match* the Lightroom look, not "improve" on it).
- **Single faithful pipeline** — forfeits the Mode-2 "what Adobe costs you"
  demonstration; defeats half the project's purpose.
- **Speculative TIFF domain-swap to OKLCh** — risks moving the TIFF *away* from
  the Adobe look with no evidence; gated on the ACR golden set instead.
- **Okhsl / Okhsv on the master** — sRGB-gamut-bound by construction; wrong for
  wide-gamut ACEScg (use OKLCh proper).

**Amendment (2026-05-31) — the single gated ACES RGC pass SHIPPED (contract 2).**
The shared AP1 gamut-safety pass is implemented as `output._aces_rgc_compress_ap1`,
applied in `write_exr_scene_linear` on the **ACEScg (AP1) EXR path only**, after
the ProPhoto→AP1 Bradford + NaN scrub, before the float→half encode. It is the
canonical **Academy 1.3 Reference Gamut Compression** (`LMT.Academy.GamutCompress`),
hand-coded from the spec (`docs.acescentral.com/rgc/specification/`, Eq. 2–4) and
the aces-dev reference DCTL — `colour` 0.4.x has **no** general gamut compression —
with the **exact published reference constants**: per-channel threshold
`[0.815, 0.803, 0.880]`, limit `[1.147, 1.264, 1.312]`, power `1.2` (these are
Academy defaults, **not** tuning). It rolls out-of-AP1 excursions (the **negative
AP1 channels** that the perceptual ops — DR-compression + ASC-CDL ColorGrade +
OKLCh HSL, all shipped — produce) smoothly back toward the achromatic axis
instead of hard-clipping at the
encode. **Always-on for ACEScg** (general gamut safety, not intent-gated) but
**gated on actual out-of-AP1 content** → byte-exact no-op (returns the literal
input) when nothing reaches threshold, so an in-gamut EXR is bit-identical to the
pre-RGC build and the gym 0.026 / rose 0.545 ΔE ship gate (stages 1–9 → sRGB) is
wholly untouched (an EXR-path change). The max/achromatic channel is invariant
(distance 0 → grey→grey, no luminance-peak darkening); an excursion **beyond** the
per-channel limit stays compressed-but-negative by design (asymptote
`threshold+scale ≈ 1.03–1.14`, never 1.0 — RGC is *compression*, not a clamp, so
residual negatives are NOT re-clipped). **`aces2065` (AP0) is not compressed** (AP0
is wider; the limits are AP1-specific). Axis-1 oracle: an independent per-pixel
reimpl held to ~0 + disabled / wrong-threshold / missing-`/ach` sensitivity legs +
an OCIO cross-check (skipif absent) that closes the channel↔limit-mapping blind
spot. The hand-rolled algorithm is kept (controllable gating, no OCIO runtime
dependency). Method/params authority:
[`research/v10-local-tone-mapping-dr-compression.md`](research/v10-local-tone-mapping-dr-compression.md)
§3.5; [`PIPELINE.md`](PIPELINE.md) §7. **Out of scope (still follow-ups):** OKLCh
HSL + ASC-CDL grade (the other perceptual-op consumers of this pass),
local-Laplacian, Texture/Clarity.

**Amendment (2026-05-31) — step 2 (Color Grade → ASC CDL) SHIPPED.**
`_apply_color_grade_perceptual` is implemented as an **offset-only ASC-CDL** grade
(slope = power = 1) in **ACEScct log**, behind the existing PERCEPTUAL branch; the
faithful split-tone `apply_color_grade` (additive-in-linear-ProPhoto) is unchanged.
Chain (contract 1, ProPhoto-in/out): ProPhoto→ACEScg (Bradford, **same params as
`output._prophoto_to_linear`** — the op does **not** claim ACEScg in/out, which
would double-transform via `output.py`, the §0 trap) → `colour.models.
log_encoding_ACEScct` (library toe — the raw v09 spec's toe was sign-flipped/wrong;
`log_encoding_ACEScct(0.18) → 0.413588`) → per-channel offset → `log_decoding_ACEScct`
→ inverse Bradford → ProPhoto, **floor 0, no top clamp** (out-of-AP1 → the shared
RGC pass above). The offset is `out_log[c] = log_in[c] + offset_lum + offset_chroma[c]`:
**Luminance is a log lift** (uniform per-channel offset, `K_lum_log = 1/17.52` = one
stop per slider unit-of-100; global + per-wheel share one scale) and **Hue+Sat** is
the **same zero-sum chroma direction** as faithful `_color_grade_wheel_tint`, applied
as a per-channel additive log delta scaled by sat/100, zone-weighted by
`_color_grade_zone_weights` on a **log-domain** luminance proxy (0.18→0.5, white→1.0;
Resolve Log-wheel placement). **Two verifier corrections folded in (not relayed):**
(a) the invented multiplicative **"slope" heuristic is DROPPED** — ColorGrade has no
control mapping to a CDL slope, Luminance is a *lift* = an offset in log; offset-only
is **the decision** (not "confirm later"), valid ASC-CDL v1.2 that round-trips
losslessly into a colorist's first Resolve node. (b) the spurious **unified 10th
ASC-CDL saturation number is DROPPED** — four per-wheel Saturations, no global one,
no IR source. Constants (`_CG_*_LOG_STRENGTH`, `_CG_ZONE_PROXY_*`) are documented
**tuning, not LR fidelity** — the perceptual intent targets the ACES master.
Byte-exact identity (`cg.is_identity()` → literal input) keeps both intents
bit-identical on a no-grade render (ship gate untouched). Axis-1 oracle: an
independent scalar reimpl (hand-rolled Bradford + ACEScct + offset SOP, **not** the
production `colour` calls — contract 4) held to atol 1e-5 + wrong-log-base /
sign-flipped-toe / non-zero-sum-chroma / swapped-zone sensitivity legs + global-lum
uniform-offset + shadow-lift + highlight-wheel-dominance + no-top-clamp +
identity-byte-exact. Authority:
[`research/v09-dualmode-impl-plan.md`](research/v09-dualmode-impl-plan.md) Step 2;
[`PIPELINE.md`](PIPELINE.md) §Stage 12. **Out of scope (still follow-ups):** step 3
**OKLCh HSL** (shipped — see the amendment below), local-Laplacian,
Texture/Clarity.

**Amendment (2026-05-31) — step 3 (HSL → OKLCh) SHIPPED.**
`_apply_hsl_perceptual` is implemented as hue-stable 8-band HSL in **OKLCh proper**
(the perceptually-uniform, gamut-agnostic space — **not** Okhsl/Okhsv, which are
sRGB-gamut-bound by construction and wrong for wide-gamut ACEScg), behind the
existing PERCEPTUAL branch; the faithful Adobe-hexcone `apply_hsl` (HSV) is
unchanged. Chain (contract 1, ProPhoto-in/out): ProPhoto(D50) lin → XYZ(D50) →
XYZ(D65) **[Bradford, pinned `_M_BRADFORD_*` module constants cross-checked vs
colour-science — Ottosson's Oklab is D65-defined, so the D50→D65 adaptation is
mandatory]** → OKLab → OKLCh → 8-band partition-of-unity adjust → OKLab →
XYZ(D65) → XYZ(D50) [Bradford] → ProPhoto, **floor L/C/ProPhoto at 0, no top
clamp** (out-of-AP1 → the shared RGC pass above). Band centres at OKLCh hue
**degrees** `[0,30,60,120,180,240,270,300]` (`_oklch_band_weights`, the degrees
analogue of the faithful `_hsl_band_weights`); per band
`h_out=(h+w@(hue/100·30°)) mod 360`, `c_out=max(c·w@(1+sat/100),0)`,
`l_out=max(l·(1+c_gate·(w@(1+lum/100)−1)),0)`, `c_gate=clip(c/0.04,0,1)` protecting
neutrals (the faithful `s_gate` analogue, on OKLCh chroma). **Three verifier
BLOCKER corrections folded in (not relayed):** (1) **no top clamp** — the
scene-referred ACEScg master must carry values >1 (faithful floors at 0 but never
clamps the top); (2) **gamut is the downstream gated `output._aces_rgc_compress_ap1`
pass, NOT inline** — the raw spec's inline "ACES RGC" was the wrong algorithm
(triggered on overrange brightness, never on the negative-AP1 channels real RGC
compresses); (3) **byte-exact identity via the `hsl.is_identity()` short-circuit**
(plus the gated downstream RGC) keeps a zero-HSL render byte-exact even on overrange
data, so both intents stay bit-identical on a no-grade render (ship gate untouched).
Production uses `colour.XYZ_to_Oklab`/`Oklab_to_Oklch`; the Axis-1 oracle hand-rolls
Ottosson's M1/M2 + signed cube-root + a hand-rolled Bradford (**not** the production
`colour` calls — contract 4), held to ~4e-3 on saturated/neutral/overrange ProPhoto
patches + inverted-Bradford (>5e-2) / wrong-band-layout / doubled-hue sensitivity
legs + identity-byte-exact + no-top-clamp + **hue-constancy-under-Luminance-sweep**
(output hue span <0.01° — the measurable Abney/Bezold–Brücke win the hexcone cannot
give) + neutral-gate + a Bradford-constant cross-check. Constants
(`_OKLCH_BAND_CENTERS_DEG`, `_OKLCH_HUE_MAX_DEG=30`, `_OKLCH_LUM_CHROMA_GATE=0.04`)
are documented **tuning, not an LR-fidelity claim**. Authority:
[`research/v09-dualmode-impl-plan.md`](research/v09-dualmode-impl-plan.md) Step 3;
[`PIPELINE.md`](PIPELINE.md) §Stage 12. **Out of scope (still follow-ups):**
local-Laplacian, Texture/Clarity (the remaining v0.9 step 4 op).

**Amendment (2026-06-01) — perceptual review-fix pass (two decisions changed).**
A `/caveman-review` of the shipped perceptual master surfaced two ordering/contract
errors (the other two findings — a CDL matrix cache and the `_DR_EPS`→`_LOG_EPS`
rename — are pure impl, see CHANGELOG):
1. **Perceptual Contrast must be hue-preserving.** The PERCEPTUAL branch was falling
   through to the faithful **per-channel** `apply_contrast_2012`, which rotates
   hue/saturation on saturated colour — directly contradicting §0 on a path whose
   thesis is hue stability. Decision: PERCEPTUAL gets its own `_apply_contrast_perceptual`
   (scale **luminance** about the 0.18 pivot, reapply as an out/in **ratio**; floor 0,
   no top clamp), the same §0 discipline as the other perceptual ops. Faithful keeps
   `apply_contrast_2012` (per-channel is part of the Lightroom look it matches).
2. **DR-compression sequences FIRST** on the perceptual branch (was after ColorGrade):
   `DR-compression → HSL → ColorGrade → Texture/Clarity → Contrast`. Tone sets the
   dynamic range, *then* colour/detail work the tamed result — consistent with the §5
   amendment (Lightroom applies Basic tone before Color Grading). Both ops remain
   byte-exact no-ops at zero sliders, so the ship gate is still untouched.

**Amendment (2026-06-01) — perceptual NEAR-BLACK stability guard (a fix-class, not a
new op).** The shipped perceptual master turned near-black NEUTRAL pixels into a
saturated red/blue cast (and ~0.35% negative AP1 channels) in the ACEScg EXR; faithful
rendered the identical grade with neutral shadows + zero negatives. **Root cause (proven
by per-op isolation on the D750 gym frame, NOT the originally-hypothesised OKLCh/ACEScct
toe explosion):** an interaction between two intent-INDEPENDENT-then-perceptual stages —
(1) `apply_blacks_2012` (Stage 11, shared by both intents) subtracts a uniform bias and
floors at 0, so a dark slightly-chromatic pixel loses its smaller channels to *exactly* 0,
leaving a degenerate single-channel near-black pixel (e.g. `[0,0,2.6e-6]`); (2) a
shadow-LIFTING perceptual reapply (Contrast<0; +Shadows DR-compression; Texture) forms
`ratio = lum_out/lum`, which → ∞ as lum → 0 and multiplies that degeneracy into a bright
false cast — which the ProPhoto→AP1 Bradford then renders as negative AP1 channels the
gated RGC cannot rescue at near-black (its correction scales by `|ach| ≈ 0`). Faithful is
immune for free: per-channel `apply_contrast_2012` lifts every channel toward the 0.18
pivot, so near-black goes neutral regardless of imbalance. **Decision — fix as a CLASS,
upstream, at the perceptual ops (not in `output.py`):** a shared near-black gate
(`_nearblack_gate`, `_NEARBLACK_LUM_FLOOR = 0.004`, the floor that caps the effective
ratio amplification at ≈9× and clears 100% of the false-cast + negative population on the
production frame) drives two reapply helpers — `_reapply_luminance_ratio` (rolls the
hue-preserving out/in ratio toward an achromatic lift `[lum_out]³` near black; used by
DR-compression, Texture/Clarity, Contrast) and `_roll_chroma_to_neutral` (rolls the
output toward its own-luminance neutral near black; used by the OKLCh-HSL and ACEScct-CDL
colour ops). **Above the floor the gate is exactly 1.0** (smoothstep clamps), so legit
shadow colour is byte-identical to the raw op — the guard touches ONLY the near-black tail
and does NOT blanket-desaturate (a 1%-grey saturated pixel is untouched). The zero-slider
`is_identity()` short-circuit fires first, so the ΔE ship gate (faithful stages 1–9) and
every byte-exact-identity invariant are untouched; the FAITHFUL applicators are unchanged
(already correct). **Negatives — fixed at birth, NO `output.py`/RGC change:** the
negatives are born in `output.py`'s ProPhoto→AP1 Bradford from *saturated* near-black
ProPhoto, so a ProPhoto-non-negative floor is necessary-but-insufficient; preventing the
*saturation* upstream (near-black neutrals stay neutral → in-gamut AP1) eliminates them at
source (measured 0.62% → **0.000%** on the production frame). RGC keeps its role for
*legit* out-of-AP1 saturated colour (residual-compressed-but-negative *by design* beyond
the per-channel limit — §7 RGC amendment), which near-black neutrals no longer reach; a
hard near-black AP1 clamp was rejected (it would break RGC's smooth roll + the byte-exact
in-gamut no-op). The OKLCh cube-root toe was measured NOT to explode on a clean imbalance
(chroma is not divided by luma in OKLCh); the ACEScct-log CDL toe *does* inject a near-black
cast under a shadow/global-wheel Saturation, so its guard is load-bearing, not insurance.
Regression: `tests/test_develop_ops.py::test_perceptual_nearblack_*` /
`::test_nearblack_guard_*` (a near-neutral-darks-straddling-the-Blacks-bias field that
reproduces ≈0.5% negatives + 8× casts with the guard removed, fixed with it; + a
legit-colour-preserved byte-identity leg + an all-five-ops class leg); the OKLCh Axis-1
oracle (`test_color_oracle.py`) reimplements the guard independently. Constants are
TUNING, not an LR-fidelity claim. Authority: `PIPELINE.md` §Stage 12 / §7.

---

## 8. Highlight recovery — Tier-1 cross-channel ratio propagation (camera-space, pre-WB)

**Decision.** Reconstruct blown highlights the demosaic's libraw hard clip
(`HighlightMode.Clip`) discards, as a **camera-RGB pre-stage** inserted
POST-demosaic / BEFORE Stage-2 white balance (`pipeline.render_frame` →
`highlight_recovery.reconstruct_highlights`). Phase 1 ships **Tier 1**:
cross-channel ratio propagation — for a clipped channel, restore the LOCAL
channel ratio anchored by the channels that still carry signal (a box-window
mean over unclipped same-channel neighbours; per-pixel brightness
`s = Σ_survivors cam / Σ_survivors m`; clipped_c ← `max(cam_c, s·m_c)`). Handles
1- and 2-channel clips (≥1 survivor). Fully-blown pixels (0 survivors) get a
neutral interim and are flagged for **Tier 2** (gradient-domain Poisson, Phase 2)
via the returned `tier2_mask`. Clean-room reimplementation — librtprocess /
RawTherapee / darktable are GPL and were used as algorithm references only; no
GPL code, deterministic (no learned methods), numpy.

**The load-bearing invariant (prevents magenta).** Reconstruct in camera space,
before WB, where the per-channel clip point is **uniform** (libraw normalises the
raw WhiteLevel → 1.0 under unit WB). Survivors anchor the result; the asymmetric
Stage-2 WB multiply applies afterwards, unchanged, so the reconstruction inherits
the correct WB-aware clip asymmetry for free. A fully-blown pixel is set
**∝ AsShotNeutral** (post-WB neutral), NOT camera `[1,1,1]` — the latter maps to
the warm/magenta cast after WB (`[1,1,1]·wb_mul`). RawTherapee's in-engine
Color-Propagation contract: WB-agnostic data, WB-aware clip points.

**Gating + auto-default.** The stage is a **strict byte-identical no-op when no
channel clips** (returns the input object), and off by default at the
`render_frame` level — so every existing caller (incl. the gym/rose ΔE ship gate,
whose gym frame *is* the clipped DSC_4053 window frame) stays byte-identical with
zero audit. CLI flag `--highlight-recovery / --no-highlight-recovery` defaults
**AUTO**: ON for `cinema-linear-master` (the scene-linear tap-7 EXR, where the
recovered over-white headroom survives), OFF for every tone-curve (tap-9) path —
see the finding below. Explicit flag overrides; `--no-highlight-recovery` also
re-enables the MLX GPU fast path (which does not run this camera-space pre-stage).

**Why it diverges from `dng_validate` (intentional).** `dng_validate` (the gym/rose
oracle, Adobe's reference renderer) does **no** highlight reconstruction — it
clips. So in clipped regions this stage **deliberately diverges** from
`dng_validate`: we match the ACR/LRT reality (ACR recovers partial clips), not
Adobe's reference clip. The ship gate is kept unmoved by rendering it with
recovery **disabled** (default-off at `render_frame`) so the validated gym 0.026 /
rose 0.545 baselines are byte-identical; the divergence lives only on the
opt-in recovery path. (Had it been on, the gym frame's ~0.48 %-clipped pixels
would have moved the baseline.)

**Load-bearing FINDING — inert in the faithful path; benefit is scene-linear.**
Adobe's Stage-9 **ProfileToneCurve clamps ≥1.0 → 1.0** (verified on the real
128-pt D750 Camera Standard curve: `curve(1.1)=curve(1.6)=curve(2.0)=1.0`), and
Stage 9 runs **before** every LR user-tone op (Stage 11 Exposure/Blacks, Stage 12
grade — PIPELINE.md flow). So for highlights reaching ProPhoto ≥ 1.0 the recovered
over-white headroom is erased at Stage 9 on **every tap-9 path** (the default
`lrtimelapse` sRGB AND `cinema-linear-finished`), and an LR exposure/highlights
pulldown — which is downstream of Stage 9 — cannot bring it back. Measured
**0.000 %** of sRGB pixels changed on DSC_4053 (whose blown windows already render
~[240] neutral ≈ the LRT JPG's ~[238] — no dark/warm cast to fix in this path).
Two caveats keep this from being a universal "structurally zero" claim (it is
*negligible / sub-LSB*, not provably nil for all frames): the hue-preserving curve
clamps the max channel but its mid-channel interpolation lets a **sub-LSB residual**
through on multi-channel clips (the Stage-9 ProPhoto Δ was 0.003, not 0), and a
**dim saturated single-channel clip** can map to ProPhoto < 1.0, where the curve
does not clamp at all. The 0 % figure is one frame (DSC_4053), measured with a
hand-built `DevelopOps`, not the production XMP→materialize path. **This is NOT
"correct, not a gap" (an earlier framing, now retracted).** Matching `dng_validate`'s
clip is correct only for the *static baseline / a constant-neutral grade*; in a
**transform** pipeline it is a real limitation. The faithful path applies the
clamping ProfileToneCurve at Stage 9 **before** its own develop transforms (Stage 11
`apply_exposure_2012`, Stage 12), so an exposure/highlights pulldown — which would
pull recovered/over-range highlights back into the sRGB range — has nothing left to
act on. **Airtight proof:** faithful recovery ON == OFF **byte-identical even under a
synthetic −3 EV `apply_exposure_2012` pulldown** on DSC_4053 — the Stage-9 clamp had
already discarded the headroom before Stage 11 ran. This is a **pipeline-ordering
defect for graded sequences**, surfaced (not hidden), and the reason `dng_validate`
is a regression tripwire, **not** the north-star (see §"Validation hierarchy" + the
reorder proposal §"Headroom-through-develop-ops"). (NB the production sequence here
is *constant-neutral grade* — `crs:Exposure2012=0`, only ±0.05-stop deflicker — so
this defect is **not exercised by the current deliverable**; the windows already
match the JPG. It bites exposure-ramped sequences.) The recovery's real benefit is the
**`cinema-linear-master` tap-7 scene-linear EXR** (no ProfileToneCurve): there it
turns warm/magenta blown highlights **neutral** — measured on DSC_4053, mean
ProPhoto on changed pixels `[1.74, 1.07, 1.81]` (R,B high, G low) → `[1.22, 1.15,
1.23]`; channel spread (0 = neutral) 0.489 → 0.075; a representative pixel
`[1.81, 1.07, 1.89] → [1.06, 1.06, 1.06]`. **Faithful-sRGB highlight handling that
survives the byte-exact Adobe tone curve is a separate, unsolved decision** —
recorded as a follow-up, NOT delivered here.

**Phase-2 scope flag (Tier 2 inherits this).** Tier 2 (gradient-domain Poisson)
plugs into the **same** Stage-1.5 camera-space insertion point, so its
reconstructed output also passes through the Stage-9 clamp → it likewise advances
**only the tap-7 `cinema-linear-master`** scene-linear path, NOT the default LRT
sRGB round-trip. Surfacing recovered highlight detail in the *faithful sRGB*
default would require highlight handling **at/after Stage 9** — which then also
collides with the **dropped** PV5 Highlights/Shadows/Whites (§5), the other half
of why the faithful path can't surface recovered highlight detail. That is a
distinct line of work to weigh before investing further in the pre-WB tiers.

**Validation.** Synthetic CI (`tests/test_highlight_recovery.py`, fixture-free,
the binding correctness proof): single- and double-channel clips reconstructed
with the ratio restored, fully-blown → neutral-after-WB (not magenta), step-edge
no overshoot, finite / non-negative, byte-identical no-op on a no-clip field, and
the `tier2_mask` hand-off contract. Real-frame reference (drive-gated): the
DSC_4053 measurements above. Authority: `PIPELINE.md` §"Stage 1.5".

---

## 9. Validation hierarchy — LRT JPG is the north-star; `dng_validate` ΔE is a regression tripwire

**Decision (what "success" means).** The real goal is to **match the LRTimelapse JPG
outputs** the colorist signed off on (Axis 3, `tools/diagnose_vs_lrt_preview.py`) —
and then, in targeted areas, **deliberately exceed** them (highlight/shadow
reconstruction, sharpening, noise reduction — enhancements `dng_validate` and an
8-bit JPEG do not have). The **mean ΔE2000 < 1.0 vs `dng_validate`** ship gate
(gym 0.026 / rose 0.545) is **demoted to a regression tripwire** for the baseline
colour science of stages 1–9: it was the sanity check during the darktable→in-house
model switch-out, and it still catches *accidental* colour drift, but it **has no
veto over intentional, goal-directed divergences**.

**Rationale.** `dng_validate` is Adobe's *baseline* DNG renderer — no develop ops,
no highlight reconstruction, no sharpening, no NR. It clips blown highlights to
white. Treating "byte-identical to dng_validate" as the success criterion is an
**active footgun** the moment we add value beyond the baseline: it would forbid
exactly the reconstruction/enhancement work that *is* the point (e.g. "keep
highlights byte-identical to a renderer that throws highlight data away"). The
0.026 number is a floor we don't want to *regress*, not a target that should *drive*
design against the real goal.

**Operational.** Keep gym/rose green for accidental-drift detection. Promote the
LRT-JPG residual to the headline progress metric (closed-source PV5 + 8-bit-JPEG
floor ~2.0 affine-residual; the goal is "match the look," not bit-equality). When a
feature *intentionally* diverges from `dng_validate` (highlight recovery already
does — §8), that is expected and documented, not a regression. Authority: CLAUDE.md
§"Validation invariants" (north-star reframed), `docs/VALIDATION.md`.

---

## 10. Headroom-through-develop-ops re-ordering — PROPOSAL (go/no-go gated, NOT decided, NOT implemented)

**Problem (established, §8).** The faithful path applies the **clamping** DCP
ProfileToneCurve at Stage 9 **before** its own develop transforms (Stage 11
`apply_exposure_2012`; Stage 12). So recovered / sensor over-range highlights are
discarded at Stage 9 before any exposure/highlights pulldown — which would pull them
back into sRGB range — can use them. Proven: faithful recovery ON==OFF byte-identical
even under a −3 EV pulldown. A pipeline-ordering defect for **graded** sequences.

**Proposed fix.** Apply the scene-linear-appropriate develop tone ops (Exposure2012;
a future faithful Highlights/Whites) **before** the DCP ProfileToneCurve, and apply
the ProfileToneCurve as the display tonemap **last** (clamp only at the display
encode). Mirrors ACR's layering (exposure is scene-linear; the profile tone renders
to display). **Identity-case constraint:** with no develop ops the deferred curve
reproduces `dng_validate` exactly, so the gym/rose tripwire stays green — and the
gate renders **stages 1–9 with no develop ops**, so the Stage-9↔11 reorder is
**orthogonal to the gate entirely** (the gate is not the obstacle; this is §9 in
practice).

**Why it is a PROPOSAL, not a decision (what must be earned first).**
1. `tone(x·2^EV) ≠ tone(x)·2^EV` for `EV≠0` — the reorder changes **every pixel** on
   a graded frame, not just highlights. It could move graded frames **closer to OR
   further from** the LRT JPG.
2. The fix assumes ACR applies exposure **scene-linear (pre profile-tone)** — this is
   **UNVERIFIED** (DNG SDK / ACR order not yet confirmed).
3. **Go/no-go = a graded-frame-vs-LRT-JPG experiment:** render both orderings on a
   frame with a real exposure grade, compare each to its `LRT_000NN.jpg`. Reduces
   residual → adopt (and it is a match-the-look win, high priority). Worsens → the
   ACR-order model is wrong; rethink before committing.

**Blocker for the experiment (current data).** The production sequence is
**constant-neutral grade** (`crs:Exposure2012=0` on all 5033 frames; only ±0.05-stop
`LocalExposure2012` deflicker; constant Temp 4034). So `tone(x·2^EV) ≈ tone(x)` and
the reorder is a **near-no-op on this entire deliverable** — it cannot be validated
against these JPGs, and it does **not** change the current job (whose windows already
match the JPG, §8). Validating the reorder needs an **exposure-ramped sequence**
(real Holy-Grail day↔night, or a project that grades highlights). Until then this
stays an unimplemented proposal; it must not become the new anchor that drives
decisions (the §9 footgun in a different coat).

---

## 11. PV2012 tone-emulation op — PROPOSAL (the lever to match the LRT-JPG look)

**Problem (measured, VALIDATION.md 2026-06-02).** On the north-star (LRT JPG), the
gap is a tone-curve **shape** difference: LRT = darker highlights (shoulder) + lifted
shadows (toe) = the lower-contrast PV2012 look; ours = the bare DCP-baseline
ProfileToneCurve (brighter highs, crushed shadows). Confirmed closeable: a monotonic
tone-transfer collapses the **smooth-region** residual from 1.59 → **0.88 mean ΔE**
(≈ the 8-bit-JPEG floor). Not color (a*/b* match), not BaselineExposure (+0.10,
applied by both), not highlight reconstruction (windows already match).

**Proposed op.** A **parametric, luminance-domain (hue/sat-preserving) shoulder+toe
tone op** in **Stage 12 (faithful develop)**, applied on top of the DCP baseline, with
a few knobs (pivot, highlight-shoulder strength, shadow-toe lift) **fit to minimise
the smooth-region LRT-JPG residual**. Hue-preserving like Stage-9 `RefBaselineRGBTone`
(the gap is tonal; color already matches). Parametric, **not** a baked per-frame LUT —
the measured transfer overfits the frame/JPEG; a parametric curve generalises across
the (constant-grade) sequence and is JPEG-noise-robust.

**Why this is clean:**
- **Orthogonal to the gym gate.** The gate renders **stages 1–9 with no develop ops**;
  a Stage-12 op never touches it → gym 0.026 / rose 0.545 stay green by construction.
  This is §9 in action: we diverge from `dng_validate` toward the LRT look **without**
  moving the regression tripwire.
- **Decoupled from the reorder (§10).** The gap lives in **non-clipped** highlights
  (~0.3–0.95, present in both renders); the Stage-9 clamp only affects the truly-blown
  small %. So this op closes the bulk of the look gap **without** needing the
  headroom-reorder. (The reorder remains relevant only for the blown remnant + graded
  sequences.)

**Caveats / non-claims.** It is a **look-match**, not PV2012-math fidelity (PV2012 is
closed-source — the documented PV5 floor, §5; this op matches the *observable* tone
response, it is **not** the dropped per-slider Highlights/Shadows/Whites math). Knobs
are tuned to **this** project's look (this DCP, temp 4034); other looks/DCPs need
per-look tuning or a learned default. Validate across **multiple** aligned frames
(needs the LRT output↔source map in `.lrt/lrtsequence.json`); the 0.88 confirmation is
one frame. The residual it **cannot** close (edges, ΔE 2.94 post-tone) is sharpening
(our `apply_sharpness` no-op stub — a separate "push past" item) + 8-bit-JPEG/resize
measurement artifact.

**Status: recommended next feature.** Go signal (the 0.88 smooth-region collapse) is
earned. Implementation = the Stage-12 op + a fit harness against
`tools/diagnose_vs_lrt_preview.py`'s smooth-region residual.

---

## Also settled — do not re-explore (pointers, not re-derivations)

- **darktable render path** — removed in v0.6 (in-process Python DNG pipeline
  replaced `darktable-cli`; gym ΔE2000 6.37 → 0.79 → 0.026). The
  `docs/reference/darktable/` notes were archived under
  `phase4-research-archive`.
- **Per-channel ProfileToneCurve at Stage 9** — WRONG; reversed 2026-05-30 to the
  hue/saturation-preserving `RefBaselineRGBTone`. Do not reintroduce. See
  [`PIPELINE.md`](PIPELINE.md) §5.
- **The LookTable as the chromatic-divergence suspect** — ruled out by
  elimination; verified equal to Adobe's `RefBaselineHueSatMap` to machine
  precision. See
  [`research/v08-synthetic-chromatic-rootcause.md`](research/v08-synthetic-chromatic-rootcause.md).
