# Emission-format verdict — verified against DaVinci Resolve

**Status:** VERIFIED 2026-05-28 by running code against **DaVinci Resolve
Studio 21.0.0b.33** (headless scripting), on the real gym scene (Nikon
D750, `DSC_4053`). Supersedes the framing in
[EMISSION_FORMAT_VERIFIED.md](EMISSION_FORMAT_VERIFIED.md), which assumed
Resolve was unavailable and therefore wrongly excluded CDNG. Resolve *is*
available here and was used as the verification target.

**Reproduce:** `tools/resolve_verify/` (connect → scratch project →
ingest → render → read back; restores your project). Plus
`tools/verify_emission_format.py` for the writer/recovery/compression
battery.

---

## 1. The objective (agreed)

One master per sequence for a Resolve colorist (primary; other NLEs
secondary) that **simultaneously**:
- **(A) represents *all* LRT-keyframable intent** — carried+applied where
  a clean per-frame Resolve mechanism exists; **burned into pixels where
  it doesn't**; never dropped/hidden;
- **(B) preserves *maximum* recovery latitude** — exposure / highlights /
  shadows + colour/luminance; **full sensor raw preferred**, accepting
  that no single format may hit both ceilings at once.

Preference: self-contained file > universal > Resolve-only two-stream.

## 2. Three binding axes — and "represent-all" is capped by the third

This is provable **by construction**, not assertion. There are **three**
constraints, not two:

1. **Recoverability (B).** Wants pixels emitted early/raw, unbaked.
2. **Carriability.** The only per-frame develop channel Resolve applies on
   ingest is **CDNG WB + exposure** (verified §4); `SetLUT`/`SetCDL` are
   static per-node, there is no per-frame keyframe setter, DCTL has no
   frame-number input. So the look **cannot** ride per-frame as editable
   grade in Resolve — carrying it means **baking** it into pixels.
3. **Renderability — the one that actually caps (A).** Baking presupposes
   a *render path*. lrt-cinema renders **only the v0.6 core set**
   (`DevelopOps`: exposure, WB/Temp/Tint, blacks, contrast, saturation,
   vibrance, point tone curve, + HG/Deflicker/Global mask deltas). It has
   **no render math** — and `xmp_parser` doesn't even parse — the §2.B
   "grading" set the user put in scope: **HSL (×24), Color-Grade wheels
   (×12), parametric-tone split (×7), Texture, Clarity**. Those were only
   ever "free upgrades" via the **β-XML Resolve-mapping carrier — which is
   dead** (see [v07-beta-xml-deadend.md](research/v07-beta-xml-deadend.md)).
   And **Highlights/Shadows/Whites (PV5) + Dehaze** are closed-source —
   impossible to render at any effort.

**Consequence:** the in-scope §2.B grading set has **neither a bake path
(no render math) nor a live carrier (β-XML dead)** → it is **not
representable today**. So a baked stream represents *all renderable*
intent — the **core set** — not "all LRT intent" at the user's "Core +
grading" scope. Represent-all is gated by renderability, independent of
recovery.

Interactions that remain true: baking a tonal op **spends** recovery (B);
no single container is both fully-recoverable and fully-baked. The cinema
answer for what *is* renderable — and the verified one here — is the
**data + grade split: two representations**. But "all intent" is not on
the table without new §2.B render math, and is permanently off the table
for PV5/Dehaze. This is exactly the "there may be no format that fulfills
this" the user pre-authorised — sharpened to *why*: renderability, not
just the data/grade tension.

## 3. Verdict, mapped to the three options posed

| Option | Verdict |
|---|---|
| **"New format we missed"** | **Ruled out.** No single container resolves the three axes; the frontier is fundamental, not a survey gap. A format change cannot add a render path. |
| **"We already do this, to the greatest extent possible"** | **This is the answer — taken literally.** "Greatest extent possible *today*" = the **core set** baked + a real recovery stream. Going beyond it is **not a format problem** — it needs new §2.B *render math*, not a new container. The repo already produces both streams (after the Stage-7 recovery fix); ship them as one **dual master**. |
| **"Back to CDNG"** | **Partially** — the **maximum-recovery substrate option** (§5), paired with the baked stream. Not standalone: it represents even less of the look (only WB+exp ride; everything else needs the baked EXR) and delegates colour. |

**Recommendation: emit a dual master per sequence —**
1. **`look` stream** — baked EXR (half-float DWAB) carrying every op
   lrt-cinema **can render** (the core set). This is represent-all *to the
   extent the renderer reaches today*; it does **not** include the
   in-scope §2.B grading set (no render path) or PV5/Dehaze (closed-source).
2. **`recovery` stream** — scene-referred pixels with overrange/latitude
   preserved (B). Substrate is a knob, see §5.

The colorist starts from `look`; when a shot needs latitude, they pull
from `recovery`. Both ingest and behave correctly in Resolve (verified).

**To actually reach the user's "Core + grading" represent-all scope**, the
lever is **implementing the §2.B render math** (HSL / Color-Grade wheels /
parametric tone → bake into the look stream) — `xmp_parser` + `DevelopOps`
+ `develop_ops` extensions, no format change. Texture/Clarity are
best-effort (different math); PV5 tone + Dehaze stay permanently dropped
(closed-source) and **must be surfaced as a render-time warning**, not
hidden — per the "don't cripple/hide intent" requirement.

## 4. Verified evidence

### EXR (writer + look stream + scene-ref recovery stream)
| Check | Result | Source |
|---|---|---|
| Writer bit-exact per channel, real 4016×6016 | maxerr 0.0 (float/half × PIZ/ZIP); no swap/shear | `verify_emission_format.py` C1 |
| Compression vs v0.6 float TIFF | half-DWAB **19.5×**; lossless half-ZIP/PIZ 3.3–3.6× | C2 |
| DWAB visually lossless (real content) | mean ΔE2000 **0.25** | C4 |
| Pipeline colour vs Adobe `dng_validate` | **0.79** ΔE (76.8% px <1) | C5 |
| **Stage-7 overrange (recovery), full-res in-file** | max **2.0–2.27**, 0.35% px >1.0 (+1 stop) — *primary recovery proof* | C3 |
| Recovery survives in Resolve (confirmatory) | scene-ref blown px pull back to detail (std 0.061) vs baked flat (0.000) — directional only (4-px sample at 640×426; architecturally guaranteed by float-EXR in YRGB) | `test_exr.py` [R] |

### Single-file packaging (multi-layer EXR)
| Check | Result | Source |
|---|---|---|
| Multi-layer EXR (scene-ref + baked in ONE file) | writes & round-trips locally | `make_assets.py` |
| **Resolve exposes the 2nd layer selectably?** | **NO** — reads only default RGB (exact 0.0000 match); no layer/channel selector in clip props | `test_exr.py` [M] |

→ The self-contained single-file dream via EXR layers **fails in
Resolve** (would need a Fusion node per clip — not colorist-native). The
EXR data+grade answer is therefore **dual-file** (two sequences).

### CDNG (max-recovery substrate option)
| Check | Result | Source |
|---|---|---|
| **Per-frame WB honored** | frame B/R 0.513 (daylight) → 1.288 (tungsten) | `test_cdng.py` |
| **Per-frame exposure honored** | frame median **2.35×** under BE +2 | `test_cdng.py` |
| Look ops (tone/sat/HSL…) as per-frame metadata | **NO** (Resolve uses bundled DCP; prior T3/T4/T6) + can't bake into Bayer | prior spike + architecture |
| **Colour is delegated** to Resolve's bundled DCP (not our validated science) | confirmed delegated; the magnitude is **not yet isolated** — the ~9.5 mean ΔE ballpark conflates the DCP-science delta with sRGB-vs-2.4 gamma + Resolve's default Rec.709 tone, so it is an upper bound only. **Action: per-camera characterise** before trusting CDNG colour. | `test_cdng_color.py` |

The never-actually-run α "T1/T2 pass" is now genuinely verified. But CDNG
**cannot represent the look** and its **colour is delegated** to Resolve
(materially divergent from our validated pipeline).

## 5. The substrate knob (recovery stream)

| | Stage-7 **EXR** scene-ref | **CDNG** raw |
|---|---|---|
| Recovery | ~30 stops half-float, scene-referred (one WB baked) | **full sensor**, re-debayerable, WB-free |
| Per-frame WB/exposure adjustable in Resolve | no (baked) | **yes (verified)** |
| Colour science | **our validated pipeline (0.79 ΔE)** | delegated to Resolve bundled DCP (magnitude not yet isolated; needs per-camera characterisation) |
| Streams / universality | EXR, universal | CDNG (Resolve/ACR/RawTherapee), heterogeneous w/ the look EXR |
| Status | **shipping-ready** (`cinema-linear-master`, after the Stage-7 overrange fix) | **option** — needs a `cdng_emit` writer + colour characterisation |

**Default recommendation:** Stage-7-EXR recovery stream — clean validated
colour, universal, zero new code beyond the dual-emit preset. **Offer
CDNG** as the "ultimate latitude" option for users who will re-grade in
Resolve and accept delegated colour.

## 6. What changed in code (this investigation)
- `pipeline.py`: Stage-7 `support_overrange=True` — made the recovery
  stream *real* (it was clamped to 1.0 before; see
  [EMISSION_FORMAT_VERIFIED.md](EMISSION_FORMAT_VERIFIED.md) §4).
- `tools/resolve_verify/` — the headless Resolve verification harness +
  the EXR / CDNG / CDNG-colour tests. This is the durable replacement for
  the manual-Resolve checkpoint that never ran.

## 7. Open follow-ups (not blocking the verdict)
- A `dual-master` preset that emits both streams in one render pass
  (trivial: γ writer + the Stage-7 writer already exist).
- If CDNG ships: a `cdng_emit` writer (per-frame `AsShotNeutral` +
  `BaselineExposure` from `DevelopOps`) and a per-camera colour
  characterisation of Resolve's bundled DCP vs our pipeline.
- Resolve render-job scripting quirks encoded in `harness.render_clip`
  (Deliver page must be open; timeline resolution must be set; wait on
  files-present) — documented there for reuse.
