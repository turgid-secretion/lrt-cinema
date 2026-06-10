# Linear-EXR gamut: how Resolve & Nuke interpret it, and the standards-aligned tag

**Status:** Research + on-box verification, 2026-05-28. Answers task #15
("measure emission gamut + align to Resolve/scene-referred standards"). The
two load-bearing facts (does Resolve read EXR chromaticities; what gamut does
bare "Linear" assume) are **verified on this machine against DaVinci Resolve
Studio 21.0.0b.33**, not asserted.

**Evidence tags:** **[here]** reproduced on this box this session ·
**[doc]** vendor/primary doc or source · **[claim]** web finding not reproduced.

**Reproduce:** `tools/resolve_verify/test_chromaticities.py` and
`tools/resolve_verify/test_linear_gamut.py` (both connect to a running Resolve
Studio, work in a throwaway project, restore yours on exit).

---

## TL;DR

1. **Resolve does NOT read the OpenEXR `chromaticities` / `acesImageContainerFlag`
   header to determine an imported clip's gamut** — verified [here] in **both**
   DaVinci YRGB Color Managed (RCM) **and** ACES modes. The same linear pixels
   written untagged, Rec.709-tagged, and AP0+ACES-flag-tagged decode
   **byte-identical** (max Δ = 0.00000). Gamut comes from the **Input Color
   Space / Input Transform** assignment, full stop.
2. **Input Color Space = "Linear" means linear-transfer + *inherit the timeline
   working gamut*** (gamut-agnostic; no input-side gamut transform). Verified
   [here] and colorimetrically (matches a hand-computed Rec.2020→Rec.709 chain
   to 4 decimals). That is *why* the Input list has a bare "Linear" but no
   "Linear/Rec.2020": "Linear" deliberately carries no primaries.
3. **Standards-aligned scene-referred working gamut is ACEScg (AP1) linear**,
   not linear Rec.2020. Rec.2020 is a delivery/display gamut (ITU-R BT.2020,
   UHDTV) — the user's suspicion is correct. ACES *archival/interchange* output
   is ACES2065-1 (AP0) per SMPTE ST 2065-1/-4.
4. **Single recommendation for an Adobe-free scene-referred linear EXR master:
   emit ACEScg (AP1), linear, and TAG `chromaticities` = AP1 (+ a sensible white).**
   Colorist picks the named **"ACEScg"** Input entry (verified accepted in
   Resolve RCM, and a real distinct transform) → correct with one obvious
   standard click and zero guesswork. Nuke: set the Read node to ACEScg. The
   chromaticities tag is for OIIO/Nuke/human interchange and self-documentation;
   neither NLE auto-reads it, but writing it is free and standards-correct.

---

## Q1 — Why Resolve's Input list has "Linear" but not "Linear/Rec.2020"; how Resolve assigns a linear EXR's gamut

### Verified Input Color Space list (clip-level, RCM = DaVinci YRGB Color Managed v2) [here]
`SetClipProperty("Input Color Space", …)` ACCEPTS: `Linear`, `Rec.709 Gamma 2.4`,
`Rec.2020 Gamma 2.4`, `ACEScg`, `ACEScct`. REJECTS: `Linear/Rec.2020`,
`Linear/Rec.709`, `ACES2065-1`, `Same as Timeline`, `Bypass`.

So the premise is real: the **Input** side offers bare `Linear` and named scene-
referred spaces (`ACEScg`), but **no gamut-qualified linear-Rec.2020**. The
Timeline/Output lists *do* carry gamut-qualified linear entries (e.g.
"Rec.2020 Linear") because there the gamut is the explicit working/output gamut.

### Does Resolve read the EXR header to pick gamut? NO. [here]
`test_chromaticities.py`: identical linear pixels, three variants —

| Variant | `chromaticities` | `acesImageContainerFlag` | sat-patch decode | neutral |
|---|---|---|---|---|
| none | absent | absent | [0.8949, 0.7744, 0.1864] | 0.6286 |
| rec709 | Rec.709 prim. | absent | [0.8949, 0.7744, 0.1864] | 0.6286 |
| aces | AP0 prim. | 1 | [0.8949, 0.7744, 0.1864] | 0.6286 |

max full-frame Δ between any pair = **0.00000**. The attributes were confirmed
on disk (round-tripped via the OpenEXR 3.4 binding) — this is "Resolve ignores
them," not "they weren't written." **Resolve does not read EXR `chromaticities`
or `acesImageContainerFlag` to assign input color space.** (Camera-RAW formats
and some video metadata are honored — DNG, etc. — but not EXR primaries.) [here]

**Verified in BOTH color-managed modes** [here]. The test above is RCM
(DaVinci YRGB Color Managed). `test_aces_mode_flag.py` repeats it with
`colorScienceMode = acescct` (ACES) and **no manual IDT**: untagged vs
AP0+`acesImageContainerFlag=1` again decode identically (Δ = 0.00000). Even
though the flagged clip's auto input-transform reads back as "ACES" (vs
"Project" for the untagged one), the decode is unchanged — Resolve does **not**
derive primaries from the flag in ACES mode either. (Contrast Autodesk Flame,
which *does* auto-assign ACES2065-1 from the flag in "From File or Rules" mode
[claim].)

### What "Linear" assumes for gamut: the TIMELINE working gamut. [here]
`test_linear_gamut.py`: one untagged linear EXR (saturated red 0.80/0.10/0.05),
Input = `Linear`, **Output pinned Rec.709 Gamma 2.4** in both renders, only the
Timeline gamut varied —

| Input | Timeline | Output | sat-patch decode | neutral |
|---|---|---|---|---|
| Linear | Rec.709 Gamma 2.4 | Rec.709 Gamma 2.4 | [0.9111, 0.3831, 0.2870] | 0.6826 |
| Linear | Rec.2020 Gamma 2.4 | Rec.709 Gamma 2.4 | [1.0, 0.1649, 0.2363] | 0.6826 |

- Saturated patch **moves** (Δ_sat = 0.119); neutral patch **invariant**
  (Δ_neu = 0.00000). Moves-saturated + invariant-neutral = the signature of a
  **gamut reinterpretation** (neutral is gamut-independent).
- Colorimetric confirmation [here]: model the chain as *pixels assigned the
  TIMELINE primaries → Timeline→Output (Rec.2020→Rec.709) matrix → Rec.709
  gamma-2.4 OETF*. Predicted Rec.2020-timeline patch = **[1.0, 0.1649, 0.2363]**
  (= measured to 4 dp); Rec.709-timeline = **[0.9112, 0.3831, 0.2870]**
  (= measured). Exact match.

**Interpretation:** Input = "Linear" applies **no input-side gamut transform** —
the clip *inherits the timeline working primaries* and only the transfer
function is set to linear. The visible shift is the **downstream timeline→output
conversion** acting on values now treated as Rec.2020 reds. Hence: a bare-"Linear"
EXR is correct **iff** the timeline working gamut equals the gamut the pixels are
actually in. There is no "read the header / default to Rec.709" fallback for the
*gamut* of a Linear-tagged EXR — it is gamut-agnostic = timeline gamut.

### The three Resolve modes (don't conflate)
- **DaVinci YRGB** (unmanaged): no input transform; pixels enter the timeline
  as-is. No "Input Color Space" gamut assignment happens; whatever you fed it is
  treated as the timeline space. [doc]
- **DaVinci YRGB Color Managed (RCM)**: the Input→Timeline→Output triple above.
  Input Color Space assigns gamut+transfer; "Linear" = timeline-gamut + linear.
  Auto-detect works for camera-RAW/known video metadata but **not EXR
  chromaticities** [here]; unidentified clips fall to the project's default
  Input Color Space. [doc + here]
- **ACES (ACEScct/ACEScc)**: clips get an **IDT/Input Transform**, not an "Input
  Color Space." An EXR Input Transform list includes "ACES2065-1"/"ACEScg"
  choices; again the *user* selects it — Resolve doesn't infer AP0/AP1 from the
  header. Resolve transforms the working space back to ACES2065-1 (linear AP0)
  before any Output Transform (Nick Shaw, ACEScentral). [doc/claim]

---

## Q2 — What real scene-referred EXR pipelines use and TAG

- **ACEScg (AP1), scene-linear** is the de-facto VFX/CG **working/rendering**
  space. AP1 primaries are close to (slightly larger than) Rec.2020, **all
  positive**, energy-conserving — designed exactly so rendering/compositing math
  behaves. ACEScg is "transitory, existing only within a compositing or
  rendering system." [doc: ACEScentral, Chris Brejon, antlerpost]
- **ACES2065-1 (AP0), scene-linear** is the **interchange/archival** encoding.
  AP0 spans all visible color (some imaginary primaries; negative-capable) — bad
  to grade/render in (breaks energy conservation; "never render in ACES2065-1"),
  good for lossless exchange. **"ACES has always said that if an image is
  written to a file, it should be AP0 (ST 2065-1)"** — Jim Houston, ACEScentral.
  [doc]
- **ACEScct / ACEScc are LOG** working spaces — irrelevant to a *linear* EXR
  master (don't tag a linear file as ACEScct). [doc]
- **Linear Rec.2020 as a scene-referred intermediate:** legitimate as a *container
  of values* (float EXR holds out-of-2020 color as negatives), but BT.2020 is an
  ITU-R **delivery/display** gamut (UHDTV) — it has no IDT, no ACES role, and no
  matching Input entry in Resolve's clip list. Using it as the *named* scene-
  referred working space is the gamut-misuse the user suspected: it works only by
  the "Linear = timeline gamut" coincidence, and self-documents nothing. [doc +
  here]

**What pipelines TAG:** ACES deliverables tag `chromaticities` = AP0 +
`acesImageContainerFlag = 1` (SMPTE ST 2065-4). VFX *working* EXRs are commonly
ACEScg and are frequently tagged `chromaticities` = AP1 — though, per Foundry's
Derek and Deke Kincaid on ACEScentral, **"there is nothing preventing someone
from writing it in a different color primaries,"** so productions confirm spaces
by convention, not by trusting the header. [doc/claim]

---

## Q3 — Are `chromaticities` / `acesImageContainerFlag` respected? Canonical tagging.

- **OpenEXR/ASWF:** `chromaticities` is an **optional** standard attribute — "for
  RGB images, specifies the CIE (x,y) chromaticities of the primaries and the
  white point." The spec assigns **no default and no mandated reader behavior**;
  by long-standing convention a *consumer that chooses to honor it* assumes
  Rec.709 primaries when it is absent, but honoring it at all is optional. [doc:
  openexr.com StandardAttributes]
- **SMPTE ST 2065-4 (ACES container):** for strict ACES compliance
  `acesImageContainerFlag` **"shall be of type int and shall contain the value 1
  … This attribute is required,"** and `chromaticities` **"must specify the ACES
  RGB primaries and the ACES neutral as specified in SMPTE ST 2065-1"** (AP0).
  In practice many facilities call an EXR an "ACES file" loosely even when
  compressed or when the flag is absent. [doc: openexr.com exr2aces, ST 2065-4:2022]
- **Resolve:** **does NOT read either attribute** to assign gamut — verified
  [here] in both RCM and ACES modes. (Some apps do — e.g. Autodesk Flame
  auto-assigns ACES2065-1 from `acesImageContainerFlag = 1` in "From File or
  Rules" mode [claim].)
- **Nuke:** the **Read node colorspace / Input Transform (OCIO)** decides
  interpretation; Nuke determines the *LUT/transfer* from the data type + header
  ("default" → linear for float EXR) but **does not read EXR `chromaticities` to
  pick primaries** — the user sets the Read's colorspace. Foundry's Derek: "the
  read node input transform is set to the color space that the file is in and
  then will convert from that color space into the working space." [doc/claim:
  Foundry Read docs, ACEScentral]

**Canonical way to tag a linear EXR so a colorist ingests it correctly:**
1. Write the pixels in a **named, standard scene-referred space** (ACEScg/AP1, or
   AP0 for archival) so the one manual assignment is an obvious standard entry.
2. Write `chromaticities` = the matching primaries (AP1 for ACEScg; AP0 for
   2065-1) for interchange/OIIO/Nuke/self-documentation. Add
   `acesImageContainerFlag = 1` **only** for a true AP0 ST 2065-4 deliverable.
3. Don't rely on the tag for auto-assignment in Resolve/Nuke — it isn't read.
   The tag is correctness insurance and documentation, not a magic switch.

(Implementation note: the in-repo OpenEXR 3.4 Python binding writes both via the
header dict — `header["chromaticities"] = (Rx,Ry,Gx,Gy,Bx,By,Wx,Wy)` and
`header["acesImageContainerFlag"] = 1`; round-trip verified [here]. `output.py`
currently writes neither.)

AP1 / AP0 / Rec.2020 / Rec.709 chromaticities (EXR order Rx Ry Gx Gy Bx By Wx Wy)
— verified against the `colour-science` library's SMPTE-encoded values [here]:
- **AP1**: 0.713 0.293 / 0.165 0.830 / 0.128 0.044 / 0.32168 0.33767 (ACES white ~D60)
- **AP0**: 0.7347 0.2653 / 0.0 1.0 / 0.0001 −0.0770 / 0.32168 0.33767
- **Rec.2020**: 0.708 0.292 / 0.170 0.797 / 0.131 0.046 / 0.3127 0.3290 (D65)
- **Rec.709**: 0.640 0.330 / 0.300 0.600 / 0.150 0.060 / 0.3127 0.3290 (D65)

### Measured gamut footprint of the real render (gym, Stage-7 scene-ref) [here]
Converting the actual render into Rec.2020 / AP1 / AP0 and counting pixels with
a channel below a real threshold: at any threshold ≥ 1e-3, **0.0000% in all
three gamuts** (per-gamut mins all ≈ −6e-5 — i.e. CAT/quantization noise, not
true gamut excursion). **This warm gym scene barely exercises wide gamut**, so
it does *not* empirically discriminate AP1 from Rec.2020. The positive-values
argument for AP1 (wide-gamut color stored as positive numbers rather than
negatives) is sound in principle and the standard rationale; this particular
scene just doesn't exhibit enough saturation to demonstrate it. A saturated
test scene (neon, deep sky) would. (An earlier draft reported ~0.03–0.11% here;
that was sub-1e-4 rounding noise, including a spurious AP0 figure from the
D65→~D60 adaptation — disregard it.)

---

## Q4 — Single standards-aligned recommendation

**Emit ACEScg (AP1), scene-linear, in OpenEXR (half-float fine), and tag
`chromaticities` = AP1.** Rationale, tied to the verified facts:

- **Resolve, no fiddle:** "ACEScg" is a **named, accepted** Input Color Space
  entry (verified [here]) and applying it produces a **distinct, well-formed**
  result vs "Linear" on the same pixels (verified [here] — a real, plumbed
  transform, not a no-op; this verifies the entry exists and is live, not a
  full known-values colorimetric round-trip). The colorist clicks one
  obviously-correct standard entry.
  By contrast, linear-Rec.2020 has **no matching Input entry** — they'd have to
  pick "Linear" (right only if the timeline is Rec.2020) or the wrong-transfer
  "Rec.2020 Gamma 2.4." The absence is itself the argument.
- **Nuke, no fiddle:** set the Read node to "ACEScg" — a first-class role in
  every ACES OCIO config.
- **Standards:** AP1 is the designated scene-referred working/rendering gamut;
  positive primaries, energy-conserving, ⊃ Rec.2020. [doc]
- **Tag for interchange:** `chromaticities` = AP1 documents the file for OIIO/
  Nuke/humans even though the NLEs won't auto-read it. Don't set
  `acesImageContainerFlag` unless you switch to AP0.
- **White point — implementation gotcha:** ACEScg (AP1) uses the **ACES white
  (~D60)**, NOT D65. `output.py` today does ProPhoto(D50)→Rec.2020(**D65**).
  Emitting ACEScg means ProPhoto(D50)→AP1 with a **D50→~D60** chromatic
  adaptation (Bradford), and tagging the AP1 white (0.32168, 0.33767) — *not*
  D65. Shipping D65 pixels under an AP1/ACES tag is a (subtle) white-point
  mismatch. The footprint script above already used a D65→D60 CAT, so the
  matrices are known; just don't tag D65 as AP1.

**The fork to surface to the user (don't pick silently):**
- **If "master" = grading-bound mezzanine** (the stated use: grade in Resolve/
  Nuke): **ACEScg (AP1)** — the recommendation above.
- **If "master" = archival/interchange** (hand off to *any* ACES facility,
  long-term store): **ACES2065-1 (AP0)**, linear, `chromaticities` = AP0 +
  `acesImageContainerFlag = 1` — the SMPTE ST 2065-1/-4 deliverable. Grade-side
  it's then transformed to a working space (Resolve → AP1/DWG) automatically.

Either is standards-correct; **linear Rec.2020 is not the standards-aligned
scene-referred choice** and should be dropped as the emission gamut. If the
project wants exactly one, choose **ACEScg** for a grading-first tool, and offer
AP0 as an "archival" variant.

---

## Sources

On-box (this session): `tools/resolve_verify/test_chromaticities.py`,
`tools/resolve_verify/test_linear_gamut.py` — DaVinci Resolve Studio 21.0.0b.33.

- DaVinci Resolve manual (Color Management / Input-Timeline-Output Color Space):
  steakunderwater VFXPedia mirror parts 285 & 294.
- OpenEXR / ASWF: openexr.com StandardAttributes (`chromaticities`); openexr.com
  exr2aces (ACES file requirements).
- SMPTE ST 2065-4:2022 (ACES Image Container) — `acesImageContainerFlag`
  required = 1; `chromaticities` = ST 2065-1 AP0.
- ACEScentral: "What am I rendering AP1 or AP0?" (Jim Houston — files = AP0;
  Nick Shaw — Resolve returns to AP0 before ODT); "nuke exr read file aces ap0
  or ap1?" (Foundry Derek; Deke Kincaid; Walter Arrighetti — Nuke uses the Read
  node setting, not header primaries).
- Chris Brejon "ACES" chapter; antlerpost ACEScg; Wikipedia ACES (AP0/AP1 roles).
- Foundry Learn: Nuke Read node + OCIO color management docs.
- ITU-R BT.2020 (Rec.2020) — UHDTV delivery/display gamut.
