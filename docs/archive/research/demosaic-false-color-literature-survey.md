> **[OWNER DECISION — 2026-06-10] ARCHIVED, reference only.** The literature survey is
> real, but its **applicability to our artifact is UNPROVEN**: an owner-run RawTherapee
> experiment (cool develop WB, multiple algorithms incl. bilinear and RCD, same raw)
> shows mainstream engines do NOT produce our artifact — so "fundamental demosaic
> false-colour floor" likely never applied to this defect. Empirical root cause:
> CLAIMS.md + repair-plan Phase 1e (H1–H4).

# Demosaic false color on near-Nyquist gratings — literature / code / patent survey

**Date:** 2026-06-07
**Question (precise):** Bayer-CFA false color (blue/yellow chroma) on near-Nyquist,
high-contrast, *periodic* luminance gratings — the horizontal venetian-blind-over-
bright-window case, on a Nikon D750. (1) Is it a **known, fully-characterized**
failure mode? (2) Exactly how do other pipelines / papers / patents handle it — and
what does that tell us about Adobe Camera Raw (ACR)?

**Why this exists:** prior sessions repeatedly stated *inferences* about ACR's
mechanism as fact ("ACR does adaptive edge-aware chroma reconstruction in two
layers; the demosaic contributes nothing"). That was unfounded. This survey
replaces inference with primary sources, and explicitly tags every claim
**FACT** (a source says it, quoted) vs **INFERENCE** (reasoning).

## Verification discipline
- **Personally verified locally (highest-risk / load-bearing ACR claims):** the DNG
  chroma-blur mechanism — confirmed `ChromaBlurRadius`, `AntiAliasStrength`, and the
  `dng_mosaic_info` demosaic class are real symbols in Adobe's own compiled reference
  renderer `dng_validate` (`/private/tmp/dng_sdk/_build/dng_sdk/source/dng_validate`,
  `strings`); confirmed the SDK's reference demosaic (`dng_mosaic_info.cpp`) is
  bilinear-only (web mirror).
- **Agent-fetched, verbatim-quoted, URLs live at time of fetch:** the academic paper
  quotes, the open-source code, the patent search, the helpx strings, the D750 OLPF
  quotes, the DNG-spec text. These are the lower-risk citations (real, well-known
  papers; open code read from pinned commits). Where a quote is the sole basis for a
  load-bearing claim it is flagged.

---

## PART 1 — Is it a known, fully-characterized failure mode? **YES (FACT).**

The mechanism is the **luma–chroma frequency-multiplexing model** of the Bayer CFA,
formally established and consensus in the literature.

- **FACT** — false color *is* luminance high-frequency energy aliasing into the
  chrominance pass-band. Alleysson, Süsstrunk & Hérault, "Linear Demosaicing Inspired
  by the Human Visual System," *IEEE TIP* 14(4), 2005 (read in full):
  *"False color appears due to high luminance frequencies in the chrominance signal
  when the spatial high-pass filter is too wide-band."*
- **FACT** — the Bayer chroma carriers sit at the Fourier-plane borders (frequency
  ½): C1 at the corner (0.5,0.5); C2 on the axes (0.5,0) and (0,0.5), in cycles/px.
  Triple-corroborated (Alleysson 2005; Dubois via Leung–Jeon–Dubois LSLCD, *IEEE TIP*
  2011; Menon & Calvagno, "Color image demosaicking: an overview," *Signal Processing:
  Image Communication* 26(8–9), 2011). Alleysson: *"the chrominance energy is located
  on the borders of the Fourier spectrum and luminance in the center."*
- **INFERENCE (high confidence, from the quoted carrier geometry)** — an axis-aligned
  near-Nyquist luminance grating lands **on** a C2 chroma carrier and is demodulated
  as chrominance. A horizontal venetian blind modulates luminance along the *vertical*
  image axis → energy near (0, ~0.5) c/px → coincides with the C2 axis carrier. High
  contrast maximises the aliased-chroma amplitude; periodicity makes it dense and
  locally indistinguishable from genuine near-Nyquist colour. No source states this
  for *this* stimulus verbatim; it is assembled from the carrier facts.
- **FACT (the fundamental limit)** — once luma aliases onto the chroma carrier it is
  unrecoverable. Alleysson 2005 (sampling theorem): *"When aliasing occurs, the
  original signal cannot be reconstructed without errors."* Li, Gunturk & Zhang,
  "Image Demosaicing: A Systematic Survey," 2008: *"cross-channel aliasing in the
  frequency domain could defy any attempt of designing linear filters"* and *"such a
  low-pass filtering operation cannot eliminate the aliasing"* (for the R/B channels).
  The only "cure" named is an **external prior** — an **optical** pre-filter applied
  before sampling.
- **FACT (taxonomy)** — false color / chromatic aliasing / colour moiré is a **CHROMA**
  artifact, distinct from "zipper" (a **LUMA** edge artifact). Both surveys separate
  them explicitly (Menon 2011, Fig. 2 caption: *"(a) false colors due to aliasing and
  (b) zippering"*).

**Conclusion:** horizontal blinds over a bright window is the **textbook worst case**
for chroma false color. Our measured ~0.56 cluster (clean-room RCD ≈ darktable RCD ≈
darktable AMaZE) is at the **information-theoretic floor for the demosaic stage**, not
a sign of weak implementation. This is now *mechanistically* grounded, not just
empirical.

---

## PART 2 — How is it handled?

### 2a. The universal *software* technique: post-demosaic chroma-difference median (FACT, from source)
Read from pinned commits (2026-06-06): LibRaw `master`@`fc0fc04`, darktable
`master`@`c5a816e`, RawTherapee `dev`@`039b9b8`.

- **dcraw / LibRaw** `median_filter()` (`-m N`): 3×3 **median on R−G and B−G** color
  differences (Paeth optimal-9 sort), reconstruct `R = median(R−G) + G`; green
  untouched. Default **`med_passes = 0` (OFF)**.
- **darktable** `color_smoothing()`: a near-line-for-line **float port of dcraw's
  median_filter** (same R−G/B−G median, same sort network). Default
  **`DT_DEMOSAIC_SMOOTH_OFF` (OFF)**.
- **RawTherapee** "False Color Suppression Steps" (`ccSteps`): RGB→**YIQ**, 3×3
  **median on the I/Q chroma channels**, then a 3×3 box-blur of the cleaned chroma,
  YIQ→RGB; luma (Y) untouched. Default **`ccSteps = 0` (OFF)**.

**Two findings that correct prior assumptions:**
1. The post-demosaic **chroma-difference median is the universal primitive** across all
   three pipelines (two identical, one YIQ variant).
2. **All three ship it OFF by default** — the *opposite* of a "mandatory chroma
   cleanup." And **none targets the periodic case**: every implementation medians
   **every pixel** unconditionally — no Nyquist/periodicity/edge gate. They are
   *generic, full-frame chroma smoothers*, so they **smear genuine fine chroma**. That
   is precisely *why* they ship off and are left to per-image user judgement.

### 2b. The literature methods, and the discriminating answer (FACT)
Surveyed (full-text read unless noted): Freeman color-difference median (US Patent
4,724,395, 1988); AHD median post-process (Hirakawa & Parks, *IEEE TIP* 14(3), 2005);
alternating-projections/POCS (Gunturk et al., *IEEE TIP* 11(9), 2002); adaptive
frequency-domain demultiplexing (Dubois; LSLCD 2011); detect-then-localize (Lu & Tan,
*IEEE TIP* 12(10), 2003). A 2023 paper ("Anti-Aliasing and Anti-Color-Artifact
Demosaicing," abstract only) still uses a **color-difference median filter** as its
artifact stage.

Every method leans on the **same prior**: local smoothness/correlation of chrominance
(color-difference smoothness, or R/B-HF ≈ G-HF, or a narrow chroma pass-band). Every
method concedes the cost where that prior fails — i.e. at the fine/high-frequency
detail that is indistinguishable from the aliased chroma:
- **AHD (verbatim):** *"In some cases, the proposed algorithm smoothes out the very
  small details in the image."*
- **Lu & Tan (verbatim):** the assumptions *"fail to hold in the presence of sharp
  edges and fine details, where color values experience abrupt changes"* — so they
  apply the chroma median **only selectively, to flagged edge/detail regions**
  (detect-then-localize).
- **Li survey (verbatim):** high-saturation / varying-hue demosaicing *"remains a
  challenging task."*

**DISCRIMINATING ANSWER (the question I was told not to pre-judge):** **No accessed
method resolves dense, periodic, near-Nyquist chroma false color WITHOUT smearing
genuine fine chroma.** The honest literature treats it as a **fundamental tradeoff**,
mitigated one of two ways: **optically** (OLPF, at capture), or
**detect-region-then-suppress** (apply the chroma median/blur only in flagged
high-frequency regions, paying a chroma cost *there*). Scope: classical methods +
one 2023 paper; learned/CNN joint demosaic-denoise was **not** mined (a CNN could in
principle learn a non-smooth chroma prior — unestablished here, and disqualified for
us on hallucination/temporal-stability grounds per prior decisions).

### 2c. The capture-side half (FACT) — corrects "a better demosaic is futile"
- **FACT** — the **D750 has an OLPF, but a weak one** (it is *not* AA-less like the
  D810). Imaging-Resource: *"while the sensor does have an anti-aliasing filter, it's
  relatively weak, leading to very highly-detailed images, however there can be visible
  moiré with certain subjects"*; con: *"Weak AA-filter can produce moiré with certain
  subjects."* Direct 24MP-vs-24MP comparison vs the stronger-AA Sony A99: the D750 crop
  *"contains aliasing artifacts in the form of a wavy moiré pattern that the A99's crop
  does not contain."*
- **FACT** — false color is worse without a strong OLPF. RawPedia (RawTherapee docs):
  *"False colors are generally more apparent in images from cameras without
  anti-aliasing filters."*
- **FACT — this is the key correction:** demosaic choice is **not** irrelevant.
  RawPedia: *"it is foremost the chosen demosaicing algorithm which is the deciding
  factor in how prominent will be the false color problem"*; *"DCB can be better at
  avoiding false colors especially in images from cameras without anti-aliasing
  filters."* So the honest framing is **BOTH levers together** — demosaic quality AND a
  post-demosaic chroma-suppression stage — **not** "chroma suppression instead of a
  better demosaic."
- **FACT** — D750 reputation matches our artifact exactly. Imaging-Resource: the JPEG
  engine *"does a good job at suppressing aliasing-related false colors"* but
  *"luminance moiré is much more difficult to deal with… especially if you shoot a lot
  of man-made subjects with repeating patterns, such as buildings, fabrics, etc."*

### 2d. ACR specifically — what we now KNOW vs INFER
- **FACT (strong negative)** — **no Adobe patent** was found for the ACR demosaic, for
  an always-on demosaic chroma/false-color cleanup, or for the Color-NR sliders. An
  assignee-filtered Google-Patents conjunction query returned 0; Adobe is absent from
  the 226-result "false color demosaic chrominance" corpus; ~10 snippet-claimed
  "Adobe" patents were each verified to be assigned to **someone else** (Kodak, STMicro,
  Apple, Axis, Conexant/Synaptics, Omnivision). *Hedge: a negative can't be proven;
  one confirmation query was rate-limited (HTTP 503). But this kills the prior
  "ACR patented adaptive chroma reconstruction" claim — there is no such patent to
  cite.*
- **FACT (DNG spec + locally verified in Adobe's binary)** — Adobe's DNG/demosaic
  pipeline applies an **always-on default chroma blur** to mosaic images. DNG Spec
  1.6.0.0, `ChromaBlurRadius` (tag 50737): *"If this tag is omitted, the reader will
  use its default amount of chroma blurring… the amount of chroma blur required for
  mosaic images is highly dependent on the de-mosaic algorithm, in which case the DNG
  reader's default value is likely optimized for its particular de-mosaic algorithm."*
  I personally confirmed `ChromaBlurRadius` + the `dng_mosaic_info` demosaic class are
  real symbols in Adobe's compiled `dng_validate`. Adobe's **reference** demosaic is
  **bilinear** (`dng_mosaic_info.cpp`); the chroma blur is applied **separately in the
  render path** (not in the demosaic file). It is in the **broad class of
  chroma-smoothing priors** (Part 2a/2b) — though note it is a *linear low-pass blur*,
  not the *nonlinear median* the OSS tools use (a median is more edge-preserving; a
  linear blur smears more indiscriminately).
- **FACT — MEASURED (the capstone; this FALSIFIES the obvious inference).** I rendered
  the **real** `DSC_4053.dng` (Adobe DNG Converter from the NEF) through Adobe's own
  reference renderer `dng_validate -16 -tif` (bilinear **+ the default chroma blur**)
  and measured the blinds chroma-HF on the **same metric** as the cluster, structurally
  aligned (luma cross-correlation ncc = **1.00**, offset −8,−8; luminance matched
  within 4%; the metric is a local chroma high-pass, so the WB/profile difference
  between renderers — dng_validate's as-shot-WB/Adobe-Standard yellow vs our LRT intent
  — is DC and removed):

  | render | blinds chroma-HF |
  |---|---|
  | dng_validate (bilinear + **default chroma blur**) | **0.70** |
  | ours (RCD, no chroma blur) | 0.56 |
  | real AMaZE / real RCD | ~0.56–0.61 |
  | **ACR, Color-NR off** | **0.28** |
  | ACR, Color-NR 25 (the LRT JPG) | 0.13 |

  Adobe's **documented** default chroma blur, in Adobe's **own** renderer, lands at
  **0.70 — in the demosaic-floor cluster, 2.5× above ACR's 0.28, and visibly still
  false-colored.** So the documented default chroma blur is **measured insufficient**:
  it does NOT reach ACR's suppression. This also *directly confirms* the Part-2b thesis
  with a measurement — even Adobe's own generic chroma blur fails the dense-periodic
  blinds case.
- **INFERENCE → now partly RESOLVED by the measurement.** Previously I leaned toward
  "ACR's number comes from its always-on default chroma blur." **The measurement
  refutes that** (the default chroma blur = 0.70, not 0.28). What remains: ACR's actual
  0.28 is produced by ACR's **proprietary** pipeline — a better demosaic than bilinear
  and/or chroma processing stronger/smarter than the open default blur — which is
  **neither documented nor measured.** We do **not** have a primary-source mechanism for
  ACR's 0.28, and the one documented candidate is now ruled out. (Bound: `dng_validate`
  is Adobe's **reference renderer, not ACR**; it tests what the *documented default
  chroma blur* achieves — the inference bridge — not ACR's internal pipeline.)
- **FACT (helpx, agent-reported, not personally rendered)** — ACR's user **Color**
  noise-reduction default is **25, not 0**; "Color Detail" = color-noise threshold
  (specking↔bleeding), "Color Smoothness" = colour mottling.
- **FACT (Adobe blog, verbatim)** — "Enhance Details" (2019, an **opt-in** CNN
  demosaic) is claimed by Adobe to yield *"fewer artifacts like false colors and moiré
  patterns,"* and Adobe defines false color as cross-edge mis-interpolation — exactly
  our failure mode. But it is **not the default path**, so it does not explain default-
  ACR's number.

---

## PART 3 — Correcting the earlier overreach, with sources
Prior claim: *"ACR's advantage = adaptive edge-aware chroma reconstruction in two
layers; the demosaic contributes nothing."*
- **"demosaic contributes nothing" → CONTRADICTED.** RawPedia: demosaic choice is the
  *"foremost"* factor. Demosaic quality matters; it merely **plateaus** ~0.56 on this
  artifact and cannot reach ACR's number *alone*.
- **"adaptive edge-aware chroma reconstruction" → UNSUPPORTED as Adobe's mechanism.**
  No patent exists. The only documented Adobe chroma mechanism is a generic **"chroma
  blur"** (a linear chroma low-pass) + the Color-NR sliders — and the chroma blur is now
  **measured insufficient** (0.70, Part 2d). "Edge-aware reconstruction" was my invention
  projected onto a black box; ACR's real mechanism stays unmeasured.
- **"two layers (intrinsic + slider)" → the intrinsic layer is now MEASURED, and it
  doesn't carry the weight I assigned it.** There *is* a documented always-on default
  chroma blur (intrinsic) **plus** the Color-NR slider (default 25) — so the two-layer
  *shape* has a basis. But the intrinsic layer, as the documented default chroma blur,
  **measures 0.70** — it is NOT what gets ACR to 0.28. Whatever closes ACR's gap is
  beyond the documented reference pipeline and remains unknown.

---

## PART 4 — Bottom line for the build decision
1. The artifact is **fundamental** (information-theoretic luma↔chroma overlap) **and
   partly capture-side** (the D750's weak OLPF lets near-Nyquist luma through). Neither
   is fixable by swapping demosaics — confirmed empirically (AMaZE/RCD/ours all ~0.56)
   **and** now explained by theory.
2. Every pipeline that beats it uses **post-demosaic chroma smoothing** — a generic
   chroma low-pass/median that **trades away real fine chroma**. **Nobody has a clean
   periodic-only software solution.** The best-practice way to limit the cost is
   **detect-then-localize**: gate the chroma suppression to flagged high-frequency /
   edge regions (Lu & Tan 2003), instead of a global blur.
3. ACR reaches 0.28 by a **proprietary mechanism that is neither documented nor
   measured.** The one documented Adobe chroma mechanism (the default chroma blur) is
   **measured insufficient** (0.70, Part 2d), and **no patent exists**. So I make **no
   claim** about how ACR does it — it is beyond the open/reference pipeline. (Default
   Color-NR = 25 contributes, but the NR-*off* number is still 0.28, so the slider is
   not the whole story either.)
4. **Our realistic options (kind unchanged; now grounded):**
   - **(a) Accept the residual.** It is a localized, partly-fundamental artifact; even
     Adobe's own reference renderer (0.70) doesn't beat it, and ACR only does so by an
     undocumented mechanism.
   - **(b) Add a post-demosaic chroma-suppression stage — as a LOSSY mitigation, not a
     fix.** The least-bad literature approach is **detect-then-localize** (a
     chroma-difference median/blur gated to high-frequency regions; Lu & Tan 2003),
     since a global chroma blur smears everywhere. **Critically: no surveyed method —
     and not Adobe's own default chroma blur (measured 0.70) — is demonstrated to
     resolve the dense, periodic, near-Nyquist case.** All are validated on *sparse*
     natural-image false color. So the honest expectation is **partial suppression of
     the blinds plus some genuine-chroma loss in the flagged regions — not a clean
     fix.** We have adjacent infrastructure (the smoothstep edge mask in
     `apply_sharpness`; the guided-filter engine), but it is a real, tunable,
     *un-guaranteed* effort.
   - **(c) Optical fix** — unavailable (capture-side; can't retrofit a stronger OLPF).

**Net:** if we build something, it is a **lossy, detect-then-localize chroma
suppressor** — and the literature *plus* the dng_validate measurement say to expect
**partial suppression at a bounded real-chroma cost, not a clean solve of the
dense-periodic worst case** (nobody, including Adobe's reference renderer, has
demonstrated that). It is explicitly **not** "match ACR's mechanism" (undocumented +
unmeasured) and **not** a demosaic swap (ruled out, empirically and by theory). Whether
a partial, lossy mitigation is worth it is the owner's call.

---

## COULD NOT VERIFY / open
- Exact default `ChromaBlurRadius` value/formula — not in the two SDK files checked
  (`dng_mosaic_info.cpp` = bilinear only; `dng_negative.cpp` = stores, doesn't default).
  The *existence* of a default is FACT (spec); the *value* is unconfirmed. (But its
  *effect* IS now measured: whatever the default is, bilinear+it = 0.70 on the blinds —
  Part 2d. A clean follow-up to isolate the blur alone: set `ChromaBlurRadius=0/1` via
  exiftool and re-render blur-on vs blur-off — not done here.)
- Capstone reproducibility: `/private/tmp/dng_out/DSC_4053.dng` (Adobe DNG Converter),
  `dv_4053.tif` (`dng_validate -16 -tif`), `blinds_ours_vs_dngvalidate.png` (1:1 ×4
  side-by-side, LEFT ours / RIGHT dng_validate).
- "No Adobe patent" is a strong negative, not a proof of nonexistence (one query
  rate-limited).
- helpx slider strings via search-surfaced content, not a directly-rendered page.
- CNN/learned demosaic-denoise methods not surveyed (out of scope; disqualified for us).
- Source URLs and cached PDFs are in the agent transcripts for this session.
