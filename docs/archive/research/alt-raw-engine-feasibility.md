> **[OWNER DECISION — 2026-06-10] ARCHIVED.** The cyan conclusion below is **REFUTED
> by an owner-run experiment**: RawTherapee at the cool develop WB, multiple demosaic
> algorithms (incl. bilinear and RCD), same raw file → **no artifacts**. This doc's
> claim that the artifact "does appear at the cool WB even with an AMaZE-tier
> demosaic" was an extrapolation from OUR menon implementation — RT was never tested
> at the cool WB. The full-engine-swap kill (no crs: reader; keyframes/deflicker live
> in XMP) stands, but a **hybrid** front-end (their raw decode, our XMP-intent layer)
> is REOPENED. Root cause in progress: CLAIMS.md + repair-plan Phase 1e (H1–H4).

# Feasibility — RawTherapee / darktable / RapidRAW as the render+export engine

**Question (owner, 2026-06-07):** can we drop our clean-room render path and use
RawTherapee, darktable, or RapidRAW as the develop/export front-end for timelapse
sequences — *the way LRTimelapse uses Lightroom* — motivated by the observation that
a RawTherapee render of `DSC_4053` is clean (no venetian-blind cyan) and looks like ACR?

**Verdict (short):** **No — not as a drop-in engine swap**, on two independent grounds:

1. **The motivating premise is a white-balance confound.** The "clean" RawTherapee
   render was made at the **warm as-shot WB (5713 K)**, not LRT's intended **cool
   4034 K**. The cyan is a demosaic false-colour *amplified by the cool develop WB*;
   it does **not** appear at as-shot WB in *any* engine (ours included), and it
   **does** appear at the cool WB even with an AMaZE-tier demosaic. Switching engines
   does not fix it. (§1)
2. **None of the three can consume LRT's Adobe-authored develop intent** (proven
   bit-exact for darktable), and a swap **forfeits LRT's deflicker/ramp**, which is
   carried *inside* the Adobe `crs:` XMP. (§3–§4)

**What the revelation is actually worth — stated precisely (two separate facts, do not
blur them):**
- *Observed:* the RawTherapee render was clean because of the **warm WB** — its
  suppression was **off** (`CcSteps=0`). So the cleanliness we saw was **WB, not
  suppression.**
- *Separately true:* a post-demosaic chroma-difference median is the **best bounded
  lever we have** against the cool-WB cyan — but in our pipeline it is **partial
  (~40 %, with a slight magenta residue), not a full solve.** It is **not** demonstrated
  to reproduce RT/ACR's clean-at-cool result, whose mechanism is **unknown** (see
  `blinds-false-color-survey`, which *falsified* the DNG-chroma-blur hypothesis at 0.70).

The portable takeaway is therefore "**tune our suppression — the best bounded lever**,"
**not** "switch engines" and **not** "suppression = RT/ACR cleanliness." (§5–§6)

---

## 1. The revelation, re-examined — it's a white-balance confound (PROVEN)

The three sidecars for `DSC_4053` on the production drive tell the story:

| Source | White balance | Demosaic | False-colour suppression |
|---|---|---|---|
| **LRT / Adobe XMP** (the intent) | Temp **4034**, Tint 20, Custom | — | — |
| **RawTherapee `.pp3`** (the "clean" render) | Temp **5713**, Setting **Camera** (as-shot) | AMaZE | `CcSteps=0` (**off**) |
| **RapidRAW `.rrdata`** | default/as-shot | PPG | — (rating-only edit) |

For this frame **every** LRT develop slider is 0 except WB — so WB is essentially the
*only* variable between the two renders. The RawTherapee render dodged the failure
condition: it never applied the cool WB that blooms the artifact, and it had
suppression *off*, so suppression isn't what saved it either.

**Empirical confirmation** (chroma-amplified 2.2× crops at the worst locus, the
lower-left grille edge — `/tmp/rt_test/grille_amp.png`):

- **cool WB + linear demosaic** → cyan fringe on the bright vertical edge.
- **cool WB + menon (AMaZE/DDFAPD-tier) demosaic** → a **sharp, distinct cyan line** —
  as bad or worse than linear. *A better demosaic does not remove it.*
- **cool WB + chroma-median suppression** → cyan largely gone (slight magenta residue).
- **LRT reference (cool WB)** → clean. This is the real benchmark.
- **RawTherapee export** → strongly **warm/yellow-green** (different WB entirely).

This reproduces and extends the prior root-cause work (`vertical-cyan-rootcause`,
`blinds-false-color-survey`): the cyan is a fundamental demosaic false-colour
(luma/chroma frequency multiplexing on the near-Nyquist slat grating) whose floor is
present even in AMaZE, *amplified* by the cool develop WB. The known software mitigation
is a post-demosaic chroma-difference median. We ship ours **off** by default
(`LRT_CINEMA_CHROMA_MED`). Whether RawTherapee/darktable enable theirs *by default* is
**not verified here** — and is moot for the observed render, whose RT sidecar had
suppression **off** (`CcSteps=0`).

> **Honest limitation (does NOT affect the verdict):** I could **not** render
> RawTherapee *at the cool WB* directly — `rawtherapee-cli` crashes headless on this
> macOS box (§3, sandbox entitlement). "RT would also show cyan at the cool WB" is
> inferred from (a) the sidecar proof that RT was warm, and (b) the menon AMaZE-tier
> proxy at the cool WB — **not** from a direct RT-at-cool render. This gap touches only
> **Ground 1** (the artifact motivation). **Ground 2** (no engine reads Adobe `crs:`
> intent — proven bit-exact for darktable — and a swap forfeits LRT's deflicker, §3–§4)
> kills the wholesale swap on its own, *regardless of what RT does at 4034 K*. So the
> swap verdict is firm even in the low-probability world where RT's AMaZE resists what
> menon doesn't. **Cheap confirmatory step for the owner:** one manual RawTherapee *GUI*
> render of `DSC_4053` at Temp ≈ 4034 K, `CcSteps=0`, and look at the blind edges. If it
> is clean, RT's demosaic genuinely resists it (revises the demosaic story only — not
> the swap verdict); if it shows cyan, Ground 1 is confirmed directly.

**Implication:** the fair comparison was never "RawTherapee vs ours." It is "**LRT/ACR
is clean at the cool WB while ours isn't**," and that gap closes by porting suppression
(§6), not by changing engines.

---

## 2. The three roles a "frontend" must fill

LRTimelapse + Lightroom is **three** roles, not one. A replacement engine fills only
the third; the question is what happens to the first two.

1. **Develop-intent authoring** — LRT drives Lightroom; the colourist's look is stored
   as Adobe `crs:` XMP (PV2012 sliders, HSL, tone curve, WB).
2. **Per-frame interpolation + deflicker** — LRT's signature value: ramp keyframes
   across thousands of frames + Visual/Holy-Grail deflicker, written as one XMP/frame.
3. **Render engine** — *our* `lrt-cinema` reads that per-frame XMP + raw and renders the
   Lightroom look to TIFF. **This is the only role with the artifact.**

`lrt-cinema` already replaces role 3 only. RawTherapee/darktable/RapidRAW cannot read
role 1's output, and none provides role 2 — so "use them like LRT uses LR" actually
means *rebuild roles 1 and 2 too*.

---

## 3. Requirements verification (evidence-backed)

| # | Requirement | RawTherapee | darktable | RapidRAW |
|---|---|---|---|---|
| **R1** | Apply LRT's **Adobe `crs:` develop intent** | **No** (own pp3 only) | **No — PROVEN** | **No** (own JSON) |
| **R2** | **Interpolatable** per-frame sidecar (1000s of frames) | **Yes** — numeric pp3 | **Hostile** — binary blobs | Yes (JSON) — *but see R3* |
| **R3** | **Headless CLI batch** | Yes, *but broken on macOS here* | **Yes — works** | **No CLI** (GUI/Tauri only) |
| **R4** | Reproduce LRT **ramp/deflicker** | No (script it) | No | No |
| **R5** | Actually **fix the artifact** | No — clean render was the WB confound (suppression was off) | No (same) | No (PPG, no suppression) |

**R1 — Adobe intent (the linchpin): PROVEN false for darktable, true in principle for all.**
Rendered `DSC_4053.NEF` through `darktable-cli` (i) with the Adobe LR XMP (Temp=4034) and
(ii) with no sidecar. Result: **0 of 24.3 M pixels differ** (mean RGB identical, only
non-pixel metadata changes the md5). darktable bit-exactly **ignores** the Adobe `crs:`
develop block.
*Positive control (so this isn't "sidecars are inert in this invocation"):* (a) the
`dt_default` output is itself a fully-developed image (demosaiced, camera-WB'd,
base-curve applied) — darktable's develop pipeline is demonstrably **active** in this CLI
mode; the Adobe sidecar simply contributed nothing to it; and (b) darktable parses only
its own `darktable:*` XMP namespace, and a research spike confirmed editing a
darktable-native param (the exposure float at a fixed blob offset) **does** change the
render [Med-confidence: agent-sourced]. None of the three implement Adobe's proprietary
PV2012 develop math — by construction they cannot reproduce the LR/ACR look the project
exists to match.

**R2 — interpolatable sidecars: RawTherapee wins decisively.**
- RawTherapee `.pp3` is INI text with plain numerics and explicit curve point-lists
  (`Compensation=0`, `Curve=4;0;0;0.05;0.0309;…`). An external tool can parse and
  linearly interpolate it across frames trivially. ✓
- darktable XMP stores each module as `darktable:params="000000400000003f…"` (hex-packed
  C structs) and `blendop_params="gz12eJxj…"` (gzip+base64 structs). These are opaque,
  **version-bound binary blobs**. A research spike confirmed individual fields *are*
  decodable (e.g. the exposure float at a fixed byte offset), so external driving is
  *possible* — but only by reverse-engineering the struct layout of every module and
  re-packing per darktable version. Heroic and fragile, not a sidecar contract. ✗
- RapidRAW `.rrdata` is plain JSON (`brightness/curves/highlights/temperature/hsl/…`) —
  interpolatable in principle, but moot without a headless path (R3).

**R3 — headless batch:**
- `darktable-cli` 5.5.0 runs headless here (verified — it did the R1 renders).
- `rawtherapee-cli` exists and is the documented batch tool (`-o -p -b16 -t -Y -c`), **but
  this macOS build crashes on launch** (`EXC_BREAKPOINT` in `_libsecinit_appsandbox`):
  the binary carries a hard `com.apple.security.app-sandbox` entitlement + a `Brave`
  quarantine flag, and `xattr` removal is **blocked by SIP** ("Operation not permitted").
  *Workaround:* a non-sandboxed `rawtherapee-cli` (Homebrew / self-built) — **not**
  currently installed. This is a real robustness cost for RT-on-macOS as a pipeline tool.
- RapidRAW has **no CLI**. It is a Tauri GUI app; its batch-export is internal IPC
  invoked by the JS frontend (`Batch Export:`, `convert_negatives`, `cull_images`), not a
  command line. Automating 1000s of frames would require GUI-driving — not viable. **DQ.**

**R4 — deflicker/ramp:** none of the three has timelapse keyframe interpolation or
deflicker. LRT's deflicker signal is written *into the Adobe XMP* exposure fields —
established by our own prior deflicker audit (`deflicker-rootcause-audit`) and
`LRT_ROUNDTRIP.md`. [Med-confidence, agent-sourced: the live per-frame term in *this*
sequence is `LocalExposure2012`, and LRT writes the full ~401-attr `crs:` develop block
to every interpolated frame, not deltas.] So the deflicker
*numbers* exist only in the Adobe sidecar; an engine that can't read `crs:` gets no
deflicker. You could script-extract `crs:Exposure2012`→pp3 `Compensation` per frame (RT
only), but the deflicker *computation* still requires LR+LRT upstream — it stays
Adobe-coupled. LRT itself has **no CLI/headless API**.

**R5 — fix the artifact:** see §1. No engine fixes it by virtue of being a different
engine; RapidRAW (PPG, no suppression) would likely be **worse**.

---

## 4. Per-tool verdict

- **RawTherapee — the only viable *shape* for a future non-Adobe pipeline, but not now.**
  Numeric interpolatable pp3 (R2 ✓) + a real batch CLI (R3, modulo the macOS sandbox bug)
  + default false-colour suppression. Fatal for the *current* goal: its look ≠ the ACR/LR
  look (R1 ✗), so adopting it means **re-authoring every look and abandoning the
  ACR-fidelity north-star** the project was built to hit, plus rebuilding LRT's ramp (R4).
- **darktable — disqualified for external-driven timelapse.** Can't read Adobe intent
  (R1 ✗, proven), and its binary-blob sidecars are hostile to the per-frame interpolation
  that *is* the timelapse workflow (R2 ✗). Excellent interactive editor; wrong tool to be
  driven by an external ramping process.
- **RapidRAW — disqualified.** No headless CLI (R3 ✗) ends it for a 1000s-frame automated
  pipeline; PPG demosaic + no false-colour suppression means it would show the artifact
  too (R5 ✗); JSON sidecar (R2 ✓) is moot without R3.

---

## 5. What an engine swap would actually cost

Even granting the best case (RawTherapee), "use it like LRT uses LR" means:

1. **Re-author every creative look** in RawTherapee's controls — and accept it will *not*
   match the ACR/LR renders the colourist signed off on. This is precisely the
   Adobe-fidelity problem `lrt-cinema` was written to solve, reintroduced through a second
   engine's (different) math. Translating Adobe XMP→pp3 automatically is **lossy twice
   over** (our math → Adobe's, then Adobe's → RT's).
2. **Rebuild LRT's keyframe interpolation + deflicker** outside LRT (the numbers live in
   the Adobe XMP; the algorithm is LRT's).
3. **Keep LRT anyway** for video assembly + Motion Blur (JPG/TIFF-only ingest, no CLI) —
   that stage is unaffected and stays as-is.

You replace one working role (our renderer) with a worse-fitting one, and inherit two
new build projects (look re-authoring + deflicker), to fix an artifact that the swap
**doesn't actually fix**.

---

## 6. Recommendation

1. **Primary — tune the false-colour suppression we already have.** `LRT_CINEMA_CHROMA_MED`
   (post-demosaic chroma-difference median, ~40 % effective and HF-preserving) is the best
   bounded lever against the cool-WB cyan. **Keep it flag-gated / owner-validated** (per
   the ship-gate discipline — do not silently flip a default); the owner tunes it against
   the LRT/ACR reference at the cool WB and decides whether the residue is acceptable.
   This is a **partial mitigation (~40 %, with a magenta residue), not a full solve** —
   ACR's clean-at-cool mechanism is undocumented (`blinds-false-color-survey` falsified the
   one Adobe mechanism it could test). It keeps colour fidelity + LRT ramp/deflicker
   intact, which the swap does not.
2. **If the cool-WB cyan is the live pain:** it is a bounded suppression-tuning task, not
   an architecture change. Consider a mild WB-aware chroma guard at steep edges.
3. **Optional spike (only if 1–2 stall):** RawTherapee/libraw as a **demosaic-only**
   front-end — clean linear demosaic in, our develop pipeline on top. But we already have
   RCD/menon; the gap is *suppression*, so this is likely overkill versus (1).
4. **Do not:** adopt darktable/RapidRAW as the engine; do not auto-translate the full
   Adobe look into another engine.

> **Decision that is genuinely the owner's (not sunk-cost):** must the deliverable keep
> matching the **ACR/LR-authored look**? If **yes** → engine swap is near-infeasible;
> surgical suppression wins. If the owner is willing to **re-author looks natively** and
> rebuild the ramp → RawTherapee becomes a real long-term product option (and the macOS
> CLI must be replaced with a non-sandboxed build first).

---

## Appendix — evidence & repro

- Sidecars: `…/2026 international faire timelapse/DSC_4053.{xmp,NEF.pp3,NEF.rrdata}`
  (SanDisk). LRT Temp=4034/Tint=20; RT Temp=5713/Camera/CcSteps=0; RapidRAW rating-only.
- darktable R1 proof: `darktable-cli DSC_4053.NEF [DSC_4053.xmp] out.tif --core
  --configdir … --library :memory:` → `dt_adobexmp.tif` vs `dt_default.tif` = **0 px
  differ** of 24.3 M.
- Artifact crops: `/tmp/rt_test/grille_amp.png`, `blinds_amplified.png` (cool/linear,
  cool/menon, cool/median-suppress, LRT-ref, RT-warm). Prior: `/private/tmp/dng_out/
  rt_ours_lrt.png`, `clip_vs_cyan.png`.
- RawTherapee CLI crash: `EXC_BREAKPOINT … _libsecinit_appsandbox`; entitlement
  `com.apple.security.app-sandbox`; `xattr -dr` → SIP "Operation not permitted".
- RapidRAW: Tauri GUI, demosaic = bilinear/ppg/superpixel, no AMaZE/RCD/suppression, no
  CLI; `.rrdata` JSON (`brightness/curves/highlights/temperature/hsl/colorGrading/…`).
- Prior root-cause: `vertical-cyan-rootcause`, `blinds-false-color-survey`,
  `demosaic-false-color-literature-survey.md`.
</content>
