# Deflicker "~3× under-application" — root-cause audit (B2)

**Verdict: there is no deflicker under-application. Keep `--deflicker-scale 1.0`.**
The "~3×" came from a **scalar-gain conflation**: a single no-offset per-frame gain
`LRT ≈ g·ours` conflates *exposure* with *tone-shape*, so a fixed tone-curve
difference back-solves into a fake per-frame deflicker factor. The deflicker is
correct at 1:1, on three independent grounds.

> **Earlier framing was wrong (corrected 2026-06-05).** A first draft of this audit
> blamed a "gamma-domain measurement artifact" (gain fit on 8-bit JPEGs). That is
> **empirically false**: the comparison tool linearizes (`eotf_sRGB`) *before* the
> gain fit, and a direct measurement gives **linear gain ≈ gamma gain to ~1%**
> (1.0019→1.1391 linear vs 1.0101→1.1324 gamma). The gain table is already linear;
> the defect is the *scalar no-offset gain*, not the bit depth.

## What was claimed
`sequence-comparison-findings.md` §1/§5: the LRT-vs-ours per-frame gain drifts
~0.94→1.08 (linear) across the first 250 frames, attributed to the deflicker being
applied "~3× too weak" (the #1 north-star lever); `--deflicker-scale` was added to
dial in ~2.7×.

## What the code actually does (verified — no bug)
- **Parser** reads `crs:LocalExposure2012` off each `#LRT internal use
  (HG/Deflicker/Global)` correction correctly.
- The deflicker **mask is effectively full-frame**: a "Mask/Gradient" with
  `CorrectionAmount="1.0"`, `MaskValue="1.0"`, positioned **off-frame**
  (`FullY=-0.3`, `ZeroY=-0.31`, both above y=0, named "LRT: don't alter!") — LRT's
  trick to apply a *global* exposure through the local-correction mechanism.
- **CLI** applies it 1:1: `apply_lrt_mask_offsets(..., deflicker_scale=1.0)` →
  `exposure_ev += LocalExposure2012` → `apply_exposure_2012` = `×2^EV`.
- In the first-250 window **the deflicker is the only non-zero op** — global
  `Exposure2012`, `Contrast`, `Highlights`, `Shadows`, and the **HG/Global** mask
  components are all 0.0 across all 250 frames.

Parse and application are correct. The question is whether the *magnitude* (1:1) is
right.

## Why the "~3×" is not real — three independent legs
1. **It's a scalar-gain conflation.** `g = (o·t)/(o·o)` (no offset) is a single
   multiplier per frame. A *tone-shape* difference (a luminance-dependent curve) has
   no single correct gain — the best-fit `g` drifts as the scene moves along the
   curve. Back-solving that drift into a deflicker EV factor (`g = 2^((k−1)d)`)
   manufactures `k≈2.7` out of a difference that isn't an exposure at all. This is
   why the gain *cannot* distinguish exposure from tone-shape — and why the
   correlation with the deflicker ramp never proved causation (the ramp tracks scene
   brightness *by construction*: Visual Deflicker is computed from the developed
   luminance curve).
2. **The deflicker is short-term BY DESIGN.** LRT's own docs: Visual Deflicker
   "smooths short-term flicker but leaves mid- and long-term changes alone"
   (`docs/reference/lrtimelapse/VISUAL_WORKFLOW.md`). The 250-frame gain drift is a
   **long-term** ramp — which the deflicker is *deliberately not meant to correct*.
   So attributing the long-term drift to deflicker under-application is wrong twice
   over. (Domain-independent — relies on no test.)
3. **The deflicker is ~1:1 in linear (preview test).** `.lrt/visual/*.lrtpreview`
   are LRT's pre-deflicker developed-luminance JPEGs (proven pre-deflicker: their
   *short-term* flicker is cancelled by `d` at the right sign). Finding the `k` that
   best flattens the **high-frequency** (short-term) flicker of `log2(P_i)+k·d_i` in
   **linear** luminance: `k* ≤ 1` and scaling up `≥2×` **monotonically worsens**
   flicker for *every* high-pass window (w∈{5,7,11,15,21}). So LRT applies the
   deflicker ~1:1; ours matches; for small `d` it ~cancels in the LRT/ours ratio.

## What the drift actually is — INFERENCE (owner/§11 confirms)
The residual long-term drift is **consistent with the documented PV2012
tone-curve-shape gap** (ours≈dng_validate≠LRT; `lrt-jpg-northstar-baseline`,
DECISIONS §11). Support:
- **Within-frame tone tilt (the clean signal).** A single frame's per-pixel
  LRT-vs-ours linear ratio is luminance-dependent — e.g. frame 1: ratio
  0.66 (shadows) → 0.93 (highlights). One scene, no cross-frame confound: a
  monotonic shadow→highlight ratio is a genuine tone-shape signature (still
  tone+colour mixed, but same content).
- The deflicker is short-term-by-design (above), so the long-term drift is
  necessarily something else — and tone is the documented candidate.

**Not proven here.** The cross-frame **frame-independence** test (do the per-frame
transfer curves overlap?) came back **inconclusive — it is scene-confounded**: a
250-frame day→evening timelapse renders *different scenes*, so a luminance-binned
per-pixel mapping mixes different content and colour at each bin and cannot isolate a
fixed tone curve. The result neither confirmed nor refuted frame-independence; do not
read the explanation as support. **Scalar-gain conflation explains why the gain
*can't* separate exposure from tone-shape — it does not by itself *prove* tone-shape.**
§11 (PV2012 tone emulation) extracts the actual curve directly and is where this gets
settled.

## Conclusion — two confidence levels
- **ESTABLISHED:** scaling the deflicker is refuted (three independent legs);
  **keep `deflicker_scale = 1.0`** (the LRT-authored value). The B2 knob stays only
  as an owner escape hatch.
- **INFERENCE (pending §11's direct extraction):** the residual long-term LRT-vs-ours
  drift is the PV2012 tone-curve-shape gap, not the deflicker. **The real #1
  north-star lever is PV2012 tone emulation (§11), not the deflicker.**

## The durable lesson (beyond B2)
**A scalar, no-offset per-frame gain conflates exposure with tone-shape.** A fixed
tone-curve difference therefore reads as a drifting per-frame gain and back-solves
into a fake deflicker factor. Don't summarise an LRT-vs-ours comparison with a scalar
gain when the suspected difference is a *curve*; use a transfer curve (and remember a
timelapse can't isolate it cross-frame — different scenes). The whole U-shaped gain
table in `sequence-comparison-findings.md` is a scalar-gain summary and should not
drive design decisions about tone.
