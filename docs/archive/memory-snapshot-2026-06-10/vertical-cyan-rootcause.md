---
name: vertical-cyan-rootcause
description: "The vertical SATURATED CYAN artifact (DSC_4053 window blinds) root-caused + fix tested: a REAL ours-defect (NOT in LRT, NOT intent) = DEMOSAIC DIRECTIONAL COLOR ERROR at steep edges (rcd 8x linear), AMPLIFIED by cool develop WB (4034K). Clip is a MARKER (steepest edges, 6.2x coincidence) NOT the root — on-mosaic highlight RECONSTRUCTION was tested + REFUTED (3 variants, 2 worsened). PARTIAL fix = pre-WB chroma-difference median (survey technique, ~40% cut, detail-safe) + linear demosaic (halves rcd); best linear+median cyan 0.006 vs LRT 0.003 — close, not fully there. Env-gated scaffolding shipped (LRT_CINEMA_CHROMA_MED / _B1, default off = byte-exact). SAME class as the horizontal-slat aliasing (both demosaic chroma error)."
metadata:
  node_type: memory
  type: project
  originSessionId: 738f75a0-a635-4b3e-98fe-2c840a01f21e
---

**Origin:** owner (the COLORIST) flagged weird saturated cyan/blue artifacts at window-blind
vertical edges in our renders — "highly saturated, odd hue, stand out," NOT in LRT's previews
or renders → NOT intent, a real defect. My scalar metrics misled me TWICE: (1) `chroma_hf`
(mean local chroma-HF) missed the vertical cyan entirely; (2) `cyan-index=(G+B)/2−R` (red-
deficiency) scored ours≈LRT because it conflates a mild cool WHITE cast with a saturated
artifact. The RIGHT metric is **Lab C\* (chroma/saturation) + local C\*-standout + cyan-hue
(a\*<0 & b\*<0)** — that matches the eye: ours C\*max 62, cyan 1.8%, standout-p99 11.8 vs LRT
C\*max 22, cyan 0.3%, standout 5.1. **LESSON: an artifact is high SATURATION + local hue-
standout, not red-deficiency; measure chroma, and LOOK (don't say "visually conclusive" off a
thumbnail — owner overruled me on the image).**

**ROOT CAUSE (verified empirically, not narrated — advisor stopped me twice from the tidy
"demosaic false-color → chroma-suppression" story):**
1. **Born in stages 1–9, triggered by the cool develop WB.** Bisect: stage9+coolWB (NO
   stage-12) = cyan 0.010; +stage-12 grading = 0.010 (stage 12 adds NOTHING); stage9 +
   **as-shot WB = 0.001 (clean)**. Frame-0 ops: `temperature_k=4034, tint=+20` (owner cooled
   a warm backlit window).
2. **CLIP-COINCIDENCE (a MARKER, not the root — reconstructing clips didn't help; see
   corrected MECHANISM below).** Raw at the window clips **green 12.5% + blue 7.7%,
   red 0%** (white_level 16383). Cyan pixels are **6.2× enriched within 3px of a clipped
   G/B photosite; 58% of cyan is clip-adjacent** (clip_vs_cyan.png overlay confirms). Blown
   G/B + cool WB (B↑/R↓) → R-deficient = cyan at the partially-clipped edge.
3. **Demosaic AMPLIFIES it (so demosaic is NOT exonerated).** At cool WB: linear 0.0004 vs
   **rcd 0.0032 saturated-cyan (8×)**. My earlier "linear==rcd clean" was at AS-SHOT WB (no
   cyan to see) — invalid for this artifact (advisor caught it). The demosaic interpolates
   ACROSS the clip boundary (clipped G/B + unclipped R) → color error; a directional demosaic
   (rcd, and the testrun's quality demosaic → 0.018 vs my linear 0.008) makes more.
4. **EXONERATED (measured, no change):** stage-12 grading; capture sharpening (acr==default);
   gamut compression (perceptual==faithful); highlight-recovery on==off.
5. **Why our highlight recovery doesn't help:** `reconstruct_highlights` runs **POST-demosaic**
   (code-confirmed: render_frame Stage 1.5, on camera_rgb AFTER _decode_raw demosaics) → it's
   TOO LATE; the demosaic already created the cyan from clipped data. (Matches the known
   meta-audit gap "HL-recovery is post-demosaic not on-mosaic.")

**MECHANISM (CORRECTED after testing — clip is a MARKER, not the root):** **demosaic
directional COLOR error at steep edges** (rcd 8× linear is the tell — highlight reconstruction
can neither explain nor fix an 8× demosaic-dependence) → the small per-edge color error stays
balanced under as-shot WB but the **cool develop WB rotates it into saturated cyan**. Clips are
just the steepest edges (→ the 6.2× clip-coincidence) but NOT causal: 42% of cyan isn't near a
clip, and reconstructing the clips didn't help. Fits EVERY fact (stages 1–9, needs-cool-WB,
6.2×, the 42%, the 8×); clip-reconstruction fit all but the 8×.

**FIX — TESTED (env-gated scaffolding shipped, default off = byte-exact):**
- **REFUTED: on-mosaic highlight reconstruction** (`_b1_highlight.b1_reconstruct`, `LRT_CINEMA_B1`).
  3 variants (tier1 clipped-only ratio-prop; survivor-neutral; brightest-neutral) — tier1
  no-ops, the other two WORSEN (whole-region overwrite lifts blown-core G 0.9→1.43 while R
  stays → a fresh COLORED cliff at the region edge the demosaic re-fringes). Clip-value
  reconstruction is the WRONG lever (advisor called it).
- **PARTIAL WIN: pre-WB chroma-difference (R−G,B−G) median** (`chroma_diff_median`,
  `LRT_CINEMA_CHROMA_MED=N`, applied in `_demosaic_rgb` → covers rcd AND linear). The survey's
  universal technique; WB-agnostic; G (luma) untouched so slats survive (HF preserved, verified).
  rcd cyan 0.019→0.012 (~40%), linear 0.010→0.006; **best = linear demosaic + median (0.006)**
  vs LRT 0.003 — close, visibly reduced, but does NOT fully reach LRT (3×3 plateaus over passes;
  wider kernel risks smear). Practical mitigation for this footage: **--demosaic linear + a
  chroma-median pass**; WB-coolness is the dial.
- UNIFIES the investigation: the vertical cyan AND the horizontal slats are the SAME class
  (demosaic chroma error), both partially suppressed by the chroma median LRT/ACR have + we
  lacked. LRT's suppression is more effective (0.003) than our 3×3 (0.006–0.012).
- Owner's ORIGINAL demosaic worry was RIGHT: it's a demosaic problem; linear beats rcd here.

**DISTINCT from the horizontal-slat false color** (blinds-false-color-survey.md): that is
fundamental near-Nyquist demosaic aliasing (both ours AND Adobe have it); THIS vertical cyan is
a clip+WB+demosaic defect ours-only. Two different artifacts, two different fixes. Don't
conflate (I did, initially, with one scalar).

**ORACLE DISCIPLINE (re-confirmed):** dng_validate is the WRONG oracle for intent-driven looks
(it ignores develop intent: as-shot WB, Adobe Standard → warm window, no cyan). The north-star
is the **LRT JPG/preview** (applies the owner's WB and is clean here). Reusable local repro:
`python3 -m lrt_cinema render --input <seq> --output <dir> --dcp "<D750 Camera Standard.dcp>"
--from-frame 0 --to-frame 1 [--demosaic … --capture-sharpen … --render-intent …]`; bisect with
`render_frame(develop_ops=materialize_all_frames(parse_sequence(IN))[0], stop_after_stage=9)`.
Real DNG via Adobe DNG Converter; dng_validate at /private/tmp/dng_sdk/_build/.../dng_validate.
