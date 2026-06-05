# Deflicker "~3× under-application" — root-cause audit (B2)

**Verdict: there is no deflicker under-application. Keep `--deflicker-scale 1.0`.**
The "~3×" is a **gamma-domain measurement artifact** (per-frame gain read off 8-bit
sRGB JPEGs), compounded by the documented PV2012 tone-curve-shape gap. Scaling the
deflicker up — the proposed B2 fix — **provably worsens** the flicker.

## What was claimed
`sequence-comparison-findings.md` §1/§5: the LRT-vs-ours per-frame gain drifts
0.941→1.081 across the first 250 frames, attributed to the deflicker being applied
"~3× too weak," and ranked the #1 north-star lever. The `--deflicker-scale` knob (B2)
was added to let an owner dial in ~2.5–3×.

## What the code actually does (verified — no bug)
- **Parser** (`xmp_parser._parse_lrt_mask_offsets`) reads `crs:LocalExposure2012` off
  each `#LRT internal use (HG/Deflicker/Global)` correction correctly.
- The deflicker **mask is effectively full-frame**: a "Mask/Gradient" with
  `CorrectionAmount="1.0"`, `MaskValue="1.0"`, positioned **off-frame**
  (`FullY=-0.3`, `ZeroY=-0.31`, both above y=0, named "LRT: don't alter!") — LRT's
  trick to apply a *global* exposure through the local-correction mechanism.
- **CLI** applies it 1:1: `apply_lrt_mask_offsets(..., deflicker_scale=1.0)` →
  `exposure_ev += LocalExposure2012` → `apply_exposure_2012` = `×2^EV`.
- In the first-250 window **the deflicker is the only non-zero op** — global
  `Exposure2012`, `Contrast`, `Highlights`, `Shadows` and the **HG/Global** mask
  components are all 0.0 across all 250 frames. So "ours" applies exactly the
  deflicker EV, nothing else.

So the parse and the application are correct. The question is whether
`LocalExposure2012` applied as a linear `2^EV` is the right *magnitude*.

## The decisive test (no rendering)
`.lrt/visual/DSC_*.lrtpreview` are plain 1024×684 sRGB JPEGs — LRT's **developed,
pre-deflicker** per-frame luminance (the "pink curve" Visual Deflicker is computed to
flatten). For a 400-frame window, find the factor `k` that best flattens the
high-frequency flicker of `log2(P_i) + k·d_i` (`P_i` = preview luminance, `d_i` =
that frame's deflicker EV). `k` is LRT's effective deflicker application factor.

**Validity check (proved, not assumed): the previews are pre-deflicker.** If they
were already deflickered, adding any `k·d` only injects variance → `k*≈0` in *both*
domains. We measure display `k*≈2.1–2.25` (far from 0) → the previews contain flicker
that `d` cancels at the right sign → pre-deflicker. Good.

**Result — domain is everything:**

| measurement domain | k* (best-flattening factor) |
|---|---|
| **display / sRGB-gamma** (how 8-bit JPEGs are measured) | **≈ 2.1–2.25** |
| **linear luminance** (where `2^EV` actually operates) | **≤ 1** (0.75→−0.1 across windows) |

And the decision-relevant, **window-robust** fact (high-pass w ∈ {5,7,11,15,21}):
in the linear domain, **scaling up monotonically worsens the flicker** —
var(k=1) ≈ var(k=0) (indistinguishable, noise), then var rises through k=2 and k=2.7
for *every* window. Example (w=7): 5.6e-4 (k=1) → 7.4e-4 (k=2) → 9.6e-4 (k=2.7).

## Why "~3×" appeared (the artifact)
The deflicker `d_i` is a **linear** exposure (`2^EV`). The original gain table was an
affine fit on **÷6 8-bit sRGB JPEGs** — a *gamma-encoded* quantity. The sRGB OETF
compresses a linear EV change into a smaller display change, so reading the
display-domain gain as a linear-EV factor **inflates it by ~the encoding slope**.
That is exactly the gap between the two rows above (linear ≤1 vs display ≈2.2), and
it is why a *physically impossible* "local exposure 2.7× stronger than global, from
the same masked `Exposure2012` algorithm" appeared at all.

## Conclusion — two confidence levels
- **ESTABLISHED:** scaling the deflicker is refuted; **keep `deflicker_scale = 1.0`**
  (the LRT-authored value, within the supported `k ≤ 1` range; `k ≥ 2` provably
  worse). The B2 knob stays as an owner override, default **1.0**. The point estimate
  of the linear optimum (~0.5) is window-dependent noise — do **not** read it as
  "1:1 is exactly right" nor drift to 0.5; 1.0 is the authored value, keep it.
- **INFERENCE (owner-confirms):** the residual LRT-vs-ours gain drift is *consistent
  with* the documented **PV2012 tone-curve-shape gap** (ours≈dng_validate≠LRT;
  `lrt-jpg-northstar-baseline`, DECISIONS §11) — a baseline render difference present
  at zero develop ops, and correlated with the deflicker ramp *by construction*
  (Visual Deflicker is computed from the scene-brightness trend). This test did
  **not** prove the gap is tone-shape; the decisive jitter-vs-smooth confirmation
  against the per-frame LRT JPGs (now located locally at
  `…/Projects/lrt-export/LRT_2026_international_faire_timelapse/LRT_*.jpg`) remains
  the owner check. **The real #1 lever is PV2012 tone emulation (§11), not the
  deflicker.**

## The bigger fish (beyond B2)
**Per-frame gain measured on 8-bit sRGB JPEGs is a gamma-domain quantity; reading it
as a linear-EV factor inflates it by ~the encoding slope.** This contaminates the
*entire* U-shaped gain table in `sequence-comparison-findings.md` (0.941→1.081), not
just the deflicker line — and any LRT-JPG comparison done in 8-bit. **Those gain
numbers must be re-derived in linear before any of them drive decisions.** The same
artifact inflated the original "~3×."
