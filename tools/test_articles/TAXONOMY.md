# Failure-mode taxonomy — what the article suite must cover to be "comprehensive"

Owner directive (2026-06-10): "All possible failure modes / mutations for the
entire pipeline … product-grade edge behavior is exactly what we want to be
able to test for, as completely as is practical — ideally, deterministically
and autonomously."

This file is the coverage ledger. An article suite is comprehensive when every
row has an article (or an explicit justification for why not). Status:
✅ covered (article exists + baseline pinned) · 🔶 article exists, anchor weak
· ⬜ designed, not built · ✖ out of scope (justified).

## Anchor types (strongest available per row)

- **CT** construction truth (analytic scene; mosaic externally verifiable)
- **ENG** independent engines on the same file: dng_validate (colour math;
  bilinear reference), libraw (own pipeline), darktable-cli (product-grade,
  scriptable — INSTALLED, wiring queued), LR-product (owner one-batch export
  of the article DNGs — frozen product anchors; staged, awaiting owner)
- **INV** truth-anchored invariants (no shared colour math): chroma where
  scene is neutral; clip-zone chroma; brightness-tracking exactness
- **LR-CAL** single-variable owner exports (the CAL/CALEXP harness pattern)

## 1. Front-end (sensor → camera RGB)

| Failure mode | Article | Status |
|---|---|---|
| Demosaic false colour, axis-aligned detail | bars (freq sweep, ISO-12233-class) | ✅ baseline: linear 18.9 / menon 1.15 / libraw 0.88 |
| Demosaic false colour, dense/radial frequencies | zoneplate (Fresnel) | ✅ 1.04 / 0.41 / 0.28 |
| Demosaic false colour, diagonal detail | diagbars (45°) | ⬜ v2 |
| Zipper / luminance structure at edges | bars + ΔL split; zipper-ratio metric port from demosaic_bench | 🔶 ΔL pinned; dedicated zipper metric pending |
| Resolution / MTF | slantededge (ISO 12233:2017 eSFR, 5°) | ⬜ v2 (method exists in demosaic_bench for algorithm domain) |
| Clip × fine detail (the production blinds class) | clipbars | ✅ THE headline: ours 17.5 vs libraw 0.88 → clip-to-common-white fallback shipped |
| Partial-clip smooth gradients (magenta-band class) | clipramp | ✅ ours 2.85 vs libraw 12.12 (reversal) |
| Large blown region + bloom edge (window/sun) | clipfield (gaussian blob, peak ≫ clip) | ⬜ v2 |
| WB-conditioning under develop override (H1 regression) | clipbars_coolwb (same mosaic, 4034K/+20 develop WB) | ⬜ v2 |
| Deep-shadow / black-level / quantisation | shadowwedge (log-spaced near-black patches) | ⬜ v2 |
| Demosaic on noise (false colour from grain) | noisebars (seeded Gaussian, deterministic) | ⬜ v2 |
| Lateral CA robustness (real-lens input) | ca_shifted (per-channel radial shift, standard optics model) | ⬜ v3 — also feeds any future CA op |
| Hot/dead pixels | ✖ for now — LRT/owner workflow has not surfaced it; revisit if production shows it |
| Non-Bayer sensors (X-Trans) | ✖ — D750 project; CFA path raises cleanly |

## 2. Colour math (camera RGB → display)

| Failure mode | Article / gate | Status |
|---|---|---|
| Matrix/HSM/LookTable/curve errors | flatpatches + existing gym/synthetic gates vs dng_validate | ✅ 0.15–0.18 ≈ Adobe-ref |
| Absolute patch colour (published values) | flatpatches upgrade → X-Rite CC24 published values | ⬜ v2.1 |
| WB kelvin/tint mapping | covered by LR-CAL pattern + flatpatches under develop_wb | 🔶 |
| Out-of-gamut handling | dedicated saturated-patch article | ⬜ v3 (known comparison-space caveat documented in tests/synthetic_dng.py) |

## 3. Develop / intent layer (the crown jewel)

| Failure mode | Article / gate | Status |
|---|---|---|
| Mask-EV magnitude + domain (deflicker) | LR-CAL (done: ×4 scene-referred) + articles: deflicker-tracking sequence (same scene, EV ramp; output brightness must track 2^(4·EV) exactly — INV, no LR needed) | 🔶 LR-CAL done; tracking article ⬜ v2.1 |
| Global Exposure2012 domain | CALEXP probe (files staged, owner exports) | 🔶 awaiting owner |
| Other ops' domains (Contrast/ToneCurve/HSL/CG) | post-TARGET-lock: per-op articles, expected = op applied in DECLARED domain | ⬜ gated on lock |
| Keyframe interpolation | unit-tested; sequence article with keyframed ops | ⬜ v3 |
| Temporal stability (flicker) | sequence article: constant scene, jittered deflicker EVs → frame-to-frame brightness must be exactly monotone with EV | ⬜ v2.1 |

## 4. Output / encoding

| Failure mode | Gate | Status |
|---|---|---|
| Display transform / ICC / quantisation | existing colour-oracle + emission tests | ✅ |
| Tap-7 headroom | stage-7 overrange guard (headroom mode) | ✅ |

## Product-grade edge behaviour — the anchor plan

dng_validate's reference demosaic is bilinear → it CANNOT anchor product
edge behaviour. The product-grade anchors, in autonomy order:

1. **darktable-cli** (INSTALLED at /usr/local/bin/darktable-cli): scriptable,
   deterministic, a shipping raw developer's full pipeline (its own demosaic,
   FC suppression, pre-demosaic highlight reconstruction). Wire as an
   invariant-scored engine arm. [next session]
2. **libraw engine arm** — wired ✅.
3. **LR-product one-batch export** — ✅ DONE 2026-06-10 (`lr-anchors/*.tif`,
   scored by `score_lr_anchors.py` into the evidence JSON). Re-export only
   when the article set changes. Found: ACR ≈eliminates zoneplate false
   colour (0.02), wins diagbars (13.6) + smooth-clip reconstruction (1.07);
   WE beat the product on bars (1.15 vs 2.03) and clipbars (1.12 vs 3.34,
   post-fallback).
4. RawTherapee: GUI-only on macOS (CLI broken, CLAIMS) — owner-session only,
   low priority while dt-cli covers the product class.
