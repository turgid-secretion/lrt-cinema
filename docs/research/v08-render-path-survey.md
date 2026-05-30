# v0.8 Render-path survey — ranked by represent-all-first

**Status:** Survey / decision-space map, 2026-05-28. Commissioned to stop
collapsing onto a single solution and instead lay out the **full space of
paths × carriers**, ranked against a newly-ranked requirement set.

> **Correction (user, 2026-05-28):** the repo is **Adobe-free by principle.**
> The Adobe-engine paths (P0/P2/P6) are therefore **rejected on principle**,
> not feasibility — the §5 "use Lightroom" recommendation does **not** apply.
> Two reframes follow: (1) **"represent-all" for an Adobe-free tool cannot
> mean pixel-matching ACR** (its closed/patented ops are unreachable) — it can
> only mean *honoring each LRT parameter with our own clean-room colour
> science*; the live question is the **patent-free clean-room ceiling/effort**.
> (2) The **8-bit JPEG is LRT's *internal-export output*** and is **irrelevant
> to lrt-cinema**, which renders from LRT's **XMP intent**, not LRT's pixels —
> so §5's "hook/replace LRT render" framing was a wrong turn. The standalone
> app (own colour science, no represent-all) is a separate track.
>
> **SUPERSEDED** by the Adobe-free two-tier rewrite:
> [v08-timelapse-emission-survey.md](v08-timelapse-emission-survey.md). This
> file is retained only for the P0–P6 path-partition reasoning; use the rewrite
> as the authoritative survey.

Evidence tags: **[here]** verified on this machine · **[doc]** vendor/primary
doc or source code · **[claim]** community/forum/inference. Web findings come
from four parallel research agents (LRTimelapse, Adobe automation+licensing,
ACR reverse-engineering, adjacent engines+carriers); sources at end.

---

## 0. The requirement ranking (the thing we hadn't pinned)

1. **Represent ALL LRT-keyframable parameters.** *Baked* definition: the
   output pixels reflect every parameter as Lightroom/ACR renders it. **Hard
   gate** — everything below is an optimisation beneath it.
2. Recovery latitude (highlights/shadows/colour).
3. Colour fidelity vs the LRT/Adobe reference.
4. Carrier quality (bit depth, banding, gamut) — **compression released**
   ("TIFF is fine"), so size does not decide.
5. Effort / feasibility.
6. Licensing & Adobe-subscription dependency (a real strategic cost).
7. Self-contained file · cross-NLE · Resolve ingest.
8. Longevity / maintenance.

MVP framing (user): a **drop-in** that yields *identical emissions to the
current LRT render* but in a high-bitrate / effectively-lossless intermediate
instead of 8-bit JPEG.

---

## 1. The gate partitions the space (this is structuring, not collapsing)

Represent-all, under the baked definition, means the look must come from
**Adobe's ACR engine** — nothing else reproduces PV2012/PV5 tone, Dehaze,
Texture, Clarity, the HSL/Color-Grade math. Verified **three independent
ways**:

- **A full clean-room ACR is infeasible.** [doc] Global tone + DCP + point
  curve are matchable (this repo already does <1 ΔE). But Highlights/Shadows/
  Whites2012 (adaptive recovery), Dehaze (**patent-encumbered** — US9842382 +
  thicket), Texture, Clarity, Color-Grade wheels are spatially-adaptive +
  proprietary; no open tool matches them, and average-ΔE hides the local
  failure. Effort to truly match = engineer-*years* per op, no convergence
  guarantee, patent exposure. (Agent C)
- **No non-Adobe engine renders `crs:` faithfully.** [doc] Of Capture One,
  RawTherapee, ART, darktable, DxO, ON1, Exposure — only darktable even
  *reads* Lightroom `crs:` develop, as a lossy one-time subset ("never
  identical"); the rest ignore it and use their own model. (Agent D)
- **The redistributable Adobe component can't do it.** [doc] The DNG SDK /
  `dng_validate` renders the **DCP profile only** and literally
  `RemoveProperties(XMP_NS_CRS)` — it strips develop settings. (Agent B)

**⟹ Only paths that route through Adobe ACR/Lightroom can pass the gate.**
The live decision is therefore narrow: **how do we invoke ACR, and what
carrier comes out** — not six co-equal paths.

---

## 2. Grounding — what exists on this machine [here]

| Component | State |
|---|---|
| Adobe Lightroom Classic | **14.5.1, installed** |
| LRTimelapse 7 | **installed** |
| Adobe DNG Converter | installed |
| Adobe Camera Raw plugin | present (`…/CC/File Formats/Camera Raw.plugin`) |
| Adobe Photoshop | ambiguous — support dirs (2021–2024) present, app not in `/Applications` |
| Creative Cloud | installed (subscription path available) |
| Capture One / RawTherapee / darktable | not installed |

The Adobe-engine path is **real and testable here** — the obvious next
empirical step (not done this round; we are mapping, not building).

---

## 3. How LRTimelapse actually emits (Agent A) [doc]

- LRT **"export"** (develop pass → on-disk image sequence) is **separate**
  from **"render"** (encode that sequence to video). The intermediate
  sequence on disk is a stable contract.
- **LRT-internal** export = **8-bit sRGB JPEG only.**
- **16-bit TIFF, Rec.2020, lossless, HDR** = the **Lightroom path** (LRT's
  `LRTExport` Lightroom-SDK plugin drives LR Classic's export). LR renders
  with ACR → **represent-all** at **16-bit ProPhoto/Rec.2020**.
- **No LRT plugin/SDK/hook API exists** (EULA bars reverse-engineering).
  The only integration seams are (a) the **on-disk intermediate sequence**
  and (b) LRT's configurable **"External Programs"** (it shells out to
  ffmpeg / exiftool / dnglab / DNG Converter).

**This is the pivotal finding:** the represent-all, high-bitrate intermediate
the MVP wants **already exists** as LRT's Lightroom-path 16-bit TIFF. The
8-bit JPEG ceiling is specific to the *internal* (no-Lightroom) workflow.

---

## 4. The ranked path map

Gate column first; ✅ passes represent-all, ❌ fails, ⚠️ caps below.

| # | Path | ① Represent-all | ② Recovery | ③ Fidelity | ⑤ Effort | ⑥ Adobe dep. | ⑦ Self-contained / NLE | Verdict |
|---|---|:--:|:--:|:--:|---|---|---|---|
| **P0** | **LRT → Lightroom (LRTExport) → 16-bit TIFF** (exists today) | ✅ ACR | ◐ 16-bit baked (no raw latitude) | reference (ACR itself) | **~0 (ships)** | LR subscription | TIFF universal; Resolve ✓ | **the MVP, already real** |
| **P2** | Our own automation of **LR SDK** (Lua export) or **ACR-via-Photoshop** | ✅ ACR | ◐ 16-bit baked | reference | med (GUI-bound, not headless) | LR/PS subscription | choose TIFF/EXR | **viable; thin value over P0** |
| **P6** | **Hybrid**: LRT/LR renders → our tool owns the **carrier + Resolve packaging** at the on-disk seam | ✅ ACR | ◐ (or dual w/ raw) | reference | low–med | LR subscription | our choice (EXR/TIFF, multi-stream) | **best fit for a real tool** |
| **P1** | lrt-cinema extends its **own ACR reimpl** (current repo) | ⚠️ core only; PV5/Dehaze/§2.B **impossible** | ✓ (scene-ref) | <1 ΔE on core | very high, then stuck | **none** | EXR/TIFF | **fails #1 — structural** |
| **P4** | **Reverse-engineer ACR** fully | ⚠️ caps below "identical"; patents | ✓ | partial | engineer-**years**, patent risk | none | any | **fails #1 — infeasible** |
| **P5** | **Standalone tool**, own render | ⚠️ same cap as P1 (own engine can't match ACR) | ✓ | partial | very high | none | any | **fails #1** unless it just *is* P2/P6 |
| **P3** | **Hook LRT's internal render** to inject our format | ✅ (LRT uses ACR) **but** | — | reference | **blocked** | — | — | **dead — no LRT API; internal = 8-bit JPEG** |

Reading the table: **every gate-passing row routes through Adobe (P0/P2/P6).**
P1/P4/P5 are the "do our own render" family and all fail #1 for the same
root reason (the closed, partly-patented adaptive ops). P3 is dead on API
grounds.

---

## 5. The MVP, called out distinctly

**The represent-all + high-bitrate MVP is P0: drive LRT's Lightroom workflow
so LR/ACR emits the 16-bit TIFF Rec.2020 intermediate, instead of the
internal 8-bit JPEG.** It already exists in LRT Pro. A "tool" here is thin:
configure/automate the LRTExport path, and optionally **own the carrier +
NLE-packaging step** at the on-disk seam (P6) — e.g. convert/repackage the
16-bit TIFF sequence to EXR for Resolve/Nuke, attach metadata, manage
multi-stream output.

**Corollary the user must weigh:** if the workflow is committed to LRT's
*internal* (no-Lightroom) render, represent-all-at-high-bitrate is **blocked**
— 8-bit JPEG ceiling, no hook API, and ACR is unreplicable. Represent-all +
high bitrate **requires accepting the Adobe-Lightroom (subscription) path.**

---

## 6. Identity question — what does lrt-cinema *add*? (surfaced, not buried)

Under the new #1 ranking, the from-scratch renderer (`pipeline.py` /
`develop_ops.py`) is on the **wrong side of the gate** — it can never
represent-all. Honest options for the project's identity:

1. **Become the carrier/packaging layer around LR export (P6).** Value =
   "LRT/LR gives you the represent-all 16-bit develop; we give you the
   Resolve/Nuke-ready master (EXR, multi-stream, recovery sidecar, metadata)."
2. **Own the *recovery* niche Adobe export can't serve (secondary goal §7).**
   The validated <1 ΔE pipeline + CDNG/EXR recovery work (verified earlier
   this session) is genuinely *additive* — it gives latitude that a baked
   ACR TIFF cannot. But it does **not** represent-all. So it is a *companion*
   recovery stream, not the primary deliverable.
3. **Stay an open, no-Adobe renderer** and explicitly **redefine the goal**
   away from represent-all (accept core-set coverage). Contradicts the new
   ranking; only viable if the user re-prioritises.

This is the strategic fork the ranking forces. It is the user's call.

---

## 7. Secondary goal — recovery (only *after* the gate)

Once represent-all is met by a baked ACR render, that render is
display/output-referred: **recovery is limited to the container's headroom**
(16-bit int = none above white; 32-bit float EXR = carries over-range only if
the source had it — an ACR TIFF export generally won't). True highlight/raw
latitude needs **scene-referred or raw** pixels — which **cannot also carry
the baked ACR look** (CinemaDNG is decode-only in Resolve and strips `crs:`).

So recovery vs represent-all is the **same tension, now correctly ranked
second**: the only way to get both is a **dual master** — baked ACR look
stream (TIFF/EXR) **+** a raw/scene-ref recovery stream (CinemaDNG or Stage-7
EXR, both *verified this session*). Two streams; recovery stream re-graded by
hand. This is where the earlier EXR/CDNG verification belongs: as the
**secondary** optimisation, not the headline.

---

## 8. Carrier sub-survey (axis ④, compression released) (Agent D) [doc]

| Carrier | Max depth | Resolve ingest | Notes |
|---|---|---|---|
| **OpenEXR** half/float | 16-bit half / **32-bit float** | **read+write** (RGB/RGBA half&float) | only format combining float precision + clean Resolve + native Nuke; PIZ/ZIP lossless |
| **16-bit TIFF** | 16-bit int | read+write (RGB/RGBA/XYZ 16) | banding-free display-referred; **what LRTExport already emits**; Premiere/FCP weak on TIFF seq [claim] |
| 32-bit float TIFF | 32-bit float | **not in Resolve codec list** (Nuke ✓) | more bits buy nothing for display-referred; Resolve support unconfirmed |
| CinemaDNG | raw | **decode-only** | can't write a baked frame; **rule out for baked** (good only as recovery stream) |
| JPEG-XL | 16/float | **absent from Resolve lists** | rule out |
| ProRes 4444 XQ | 12-bit, DCT-lossy | read+write | convenience mezzanine for Premiere/FCP; not zero-compromise |
| CineForm RGB | 12-bit wavelet | read+write (16-bit wrap) | legacy visually-lossless intermediate |

**Carrier verdict:** **16-bit TIFF** is the natural represent-all baked
carrier (it's literally LRTExport's output, banding-free, universal).
**OpenEXR** is the better master if a scene-referred/recovery stream or Nuke
hand-off is in play, and the only route to 32-bit-float with clean Resolve
ingest. Both beat 8-bit JPEG by the whole point of this exercise.

---

## 9. Licensing & dependency (axis ⑥) (Agent B) [doc/claim]

- LR Classic / PS are **subscription-only** (CC named-user); the leading
  path makes the user **Adobe-dependent** — a real strategic cost to score,
  not hide. [doc]
- Adobe's redistribution ban means a tool must require the user's own
  installed, licensed Adobe app. [doc]
- The verified prohibition is **service-bureau / hosted / on-behalf-of-third-
  party** use; **automating your own licensed seat on your own content is a
  different matter** and is not clearly barred. A blanket "no automation /
  no server" clause was **not confirmed** in current Adobe General Terms —
  check live terms + product license before shipping any automation. [claim]
- ACR reverse-engineering: clean-room defeats *copyright* (Sega/Connectix; EU
  Software Directive Art. 5) but **not patents** — and Dehaze at minimum is
  patented. [doc]

---

## 10. Open decisions for the user (no premature collapse)

1. **Accept the Adobe-Lightroom path for represent-all?** (Required for #1 at
   high bitrate. If no → represent-all is unreachable and #1 must be relaxed.)
2. **Tool identity** (§6): carrier/packaging layer (P6) vs recovery-companion
   vs open-renderer-with-relaxed-goal.
3. **Carrier:** 16-bit TIFF (matches LRTExport, simplest) vs OpenEXR (master
   + recovery + Nuke).
4. **Dual master?** Pair the baked stream with a raw/scene-ref recovery
   stream (secondary goal ②), or accept baked-only.

Recommended next *verification* (when a path is chosen, before building):
empirically drive LR Classic 14.5.1 + LRTExport on this box to confirm the
16-bit TIFF Rec.2020 represent-all output end-to-end — the same "verify, don't
assert" standard applied to Resolve this session.

---

## Sources (web findings — treat per evidence tag)
- LRTimelapse: export-and-render, internal-workflow, visual-workflow, EULA,
  install pages; forum (Wegner) on bit depth. (Agent A)
- Adobe: Lightroom SDK (`LrExportSession`, `applyDevelopPreset`), ACR
  scripting (`CameraRAWOpenOptions`/`SELECTEDIMAGE`), DNG SDK
  (`dng_render.cpp`, `dng_validate.cpp RemoveProperties(XMP_NS_CRS)`), DNG
  spec 1.6, CC licensing / General Terms. (Agent B)
- Reverse-eng: darktable `lightroom.c` map, pixls.us, Adobe ACR-team posts,
  Sega/Connectix, EU Software Directive, Adobe dehaze patents. (Agent C)
- Engines/carriers: darktable/Capture One/DxO/ON1/Exposure/RawTherapee/ART
  docs; Resolve 18/20 codec lists; Nuke Write docs; Apple ProRes white paper.
  (Agent D)
