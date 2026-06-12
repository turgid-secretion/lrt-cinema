# Highlight reconstruction — industry survey + feasibility (slot-5b successor)

Owner-approved: 2026-06-12 ("a survey of current industry-best methods is
justified, followed by an assessment of implementation feasibility")

**Evidence tiers used below.** [PRIMARY] = read by us from primary material
(engine source code during the 2026-06-11 pass; Adobe's own whitepaper);
[BENCH] = published benchmark/challenge report; [DOC] = project's official
documentation; [PRACT] = practitioner/maintainer reports (failure-mode
signal, not benchmarks); [MKT] = vendor marketing (discounted). Web claims
were gathered by a fan-out research run (24 sources, 207 extracted claims;
adversarial verification was budget-killed at 9/207 — those 9 all
survived; the rest are weighed here by source tier instead. Raw salvage:
`/tmp/dr_salvage.json` lineage recorded in CLAIMS).

## The reframe that falls out of the evidence

**Adobe Camera Raw's documented behaviour is NEUTRAL recovery: where one
or two channels survive, recovered data returns as LUMINANCE, not full
colour** [PRIMARY: adobe.com/digitalimag/pdfs/phscs2ip_hilight.pdf].
Independent empirics corroborate that LR's true reconstruction is shallow —
texture degrades past ~ETTR+⅔ EV even with R/B unclipped, and part of its
reputation is default-rendering headroom, not reconstruction
[blog.kasson.com/z9/lightroom-highlight-recovery]. Typical usable headroom:
1–2 stops [PRIMARY].

Consequence for us: the product bar is **luminance recovery + neutral
chroma + graceful rolloff** — not chroma resynthesis. This matches the
owner's rank-1 verdicts (clean neutral windows; opposed rejected for
invented chroma) and our fringe forensics. "Competent reconstruction" =
recover brightness structure, refuse to invent colour.

## Family 1 — classical, in shipping OSS engines

- **Opposed (dt default since 4.2; RT vendors the same code)** [PRIMARY
  source read + our clean-room port]. dt's own docs name the failure mode:
  clipped areas adjacent to different-colour regions import wrong chroma
  [DOC]. Concordant with our suite (clipbars 17–21 falsecolor) and the
  owner's rejection. Its LUMINANCE estimate is good — our truth harness
  measured −49…−64 % linear recovery error vs clamp.
- **dt segmentation-based (4.2, same author)** — the only classical mode
  documented to rebuild LARGE all-channel-clipped regions (blown windows):
  per-segment candidate analysis + gradient-based rebuild, output framed by
  its own authors as "plausible disguise rather than true repair" [DOC].
  Practitioners rate it good but parameter-heavy [PRACT]. Algorithm
  already extracted from source ([PRIMARY], 2026-06-11 pass: 3×3-superpixel
  segmentation, weighted candidates, distance-transform gradient rebuild,
  opposed fallback).
- **dt guided-laplacian** — author-admitted: does nothing on large
  fully-blown areas, leaves magenta, prohibitive runtime at large
  diameters; speculars only [PRACT/DOC]. DISQUALIFIED for our use case.
- **RT colour propagation / dt legacy reconstruct-color** — documented
  maze-artifact failure mode [DOC]. Superseded in both engines.
- **Cross-cutting prerequisite:** accurate per-camera WHITE LEVEL.
  Multiple dt issues show all reconstruction modes corrupt when camera
  metadata overestimates the clip point [PRACT: dt #12616/#12193]. We
  measure the D750 level empirically and the mosaic mask thresholds at
  sensor truth — a strength to preserve, a risk for other bodies.
- **Temporal stability:** dt's investigated timelapse flicker traced to
  per-frame raw metadata (black/white points, rounded shutter), NOT to
  reconstruction [PRACT: dt #16548]. Deterministic local methods are
  temporally stable when inputs are consistent; our deflicker layer
  already absorbs exposure ripple.

## Family 2 — gradient-domain / inpainting literature

- **Rouf–Lau–Heidrich 2012**: deterministic Poisson/Laplace solves;
  recovers hue+texture only where ≥1 channel survives; all-3-clipped →
  flat hue fill; ~1 min at 10 MP on 2012 hardware → minutes/frame at
  24 MP; authors call it easy to implement [BENCH/paper]. Shared failure:
  hue-intensity-correlated scenes (sunsets).
- **Masood 2009** (spatially-varying ratio prior, sparse solve): ~30 s at
  0.2 MP in 2009 — impractical at 24 MP without serious solver work;
  partial-clip only [paper].
- Verdict: texture continuation for partial clips at high cost; the
  all-3-clipped case (our windows) stays unsolved. NICHE — defer.

## Family 3 — learned methods

- **State of the art (AIM 2025 inverse-tone-mapping challenge, 67 teams)**:
  deterministic feed-forward NAFNet-class CNNs win (34.49 dB PU21-PSNR,
  27 M params, GTX-1080-class inference); diffusion placed 3rd at ~6 s per
  512² on an A100. Even winners fail PRECISELY in saturated regions — the
  challenge's own error analysis [BENCH: arXiv 2508.13479]. Benchmarked on
  512² sRGB crops, not raw, not native res.
- **Temporal stability:** HDRCNN's authors admit per-frame video use
  flickers (they published a separate method to fix it) [DOC]; per-frame
  diffusion is non-deterministic and flickers by construction; mitigations
  are partial and multiply cost [BENCH: arXiv 2510.25420]. LEDiff (2024,
  MPI+Adobe) is explicit generative hallucination on display-referred
  data; no runtime/weights clarity [DOC].
- Verdict vs our hard requirements (deterministic, temporally stable,
  24 MP × 250 frames, clean-room numpy/numba, single developer):
  **DISQUALIFIED for now.** Re-open only if a sequence-aware deterministic
  model with permissive weights appears — not on any current horizon we
  found.

## Family 4 — commercial behaviour

Adobe: neutral luminance recovery, 1–2 stops, shallow true reconstruction
(above). Capture One: claims improved recovery + acknowledges the
canonical failure modes (invented colour, hard clip transitions) in its
own marketing [MKT]. DxO: no substantive public documentation surfaced.

## Feasibility assessment + ranked shortlist

Constraints: owner-clean blown windows (neutral, no invented colour) ·
deterministic · temporally stable · ≲ seconds/frame at 24 MP ·
Apache-clean clean-room in numpy/numba · single developer.

1. **Luminance-led neutral recovery ("Adobe-style"), HIGH feasibility —
   the recommended next experiment.** Reuse the opposed machinery for the
   LUMINANCE estimate only (truth harness: −49…−64 % linear error) and
   REPLACE its chroma estimate with a neutral-pull policy (chroma →
   neutral as values approach/exceed clip; the rolloff the LR anchor
   measures as clipramp ≈1.07). Cheap (reuses mosaic mask + opposed +
   fc-suppress), deterministic, stable, and directly targets what the
   owner's eyes selected for. Validate: truth-harness rel_mae on L,
   falsecolor guards, clipramp clip-zone vs the LR anchor, native-res
   flips.
2. **dt segmentation-based clean-room port, MEDIUM feasibility (~1–2
   sessions, AMaZE-class effort).** The only classical answer to large
   fully-blown regions beyond neutral fill. Algorithm already extracted;
   moderately complex (segmentation + candidates + gradient rebuild).
   Run it through the truth harness + owner flips only if (1) leaves
   visible structure on real windows unrecovered.
3. **Gradient-domain texture continuation (Rouf-style), LOW priority.**
   Partial-clip texture enhancement layered on (1) if ever needed;
   minutes/frame cost as published; defer indefinitely for timelapse.

Killed by constraints: guided-laplacian (large-area no-op), per-frame
neural methods (flicker/determinism/licence/runtime), colour-propagation
legacy modes (maze artifacts), full chroma resynthesis of any kind (the
owner's bar and Adobe's own design both say neutral).
