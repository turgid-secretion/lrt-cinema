# DECISIONS.md — binding decisions log

The hard-won decisions that shaped lrt-cinema, each with a one-paragraph
rationale, so they are **not re-litigated**. This is a decisions *log*, not a
research dump. The raw research that produced these verdicts (the `v06`/`v07`/
`v08`/`v09` series, the `color-option-space` set, the superseded emission
records) was archived and removed in the Phase-4 doc reduction; recover any of it
from git tag **`phase4-research-archive`** (e.g. `git show phase4-research-archive:<path>`).

Two research docs survived the cull as **live authorities** and are cited below:
- [`research/v08-linear-exr-gamut-resolve-nuke.md`](research/v08-linear-exr-gamut-resolve-nuke.md)
  — the colour-space allowlist authority (on-box Resolve verification).
- [`research/v08-synthetic-chromatic-rootcause.md`](research/v08-synthetic-chromatic-rootcause.md)
  — the 2026-05-30 chromatic-fix trace.

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
