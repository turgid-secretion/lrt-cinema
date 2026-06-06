# Edge colour-fringing root-cause investigation (blue/yellow at clipped edges)

**Status:** IN PROGRESS. Deterministic ablation (ours-variant vs ours-variant) on
DSC_4053 / LRT_00001 (frame 1, indoor venue). Tool: `tools/edge_fringe/fringe_metric.py`.
All renders: NEF-direct via `render_frame`, `LRT_CINEMA_BACKEND=numpy` (so numpy
demosaic edits execute), D750 Camera Standard DCP, encoded to sRGB via the gym
encoder (`tests/test_pipeline.py::_prophoto_to_srgb_8bit`).

## The artifact
High-saturation blue/yellow fringing/banding at high-contrast edges, worst at
highlight→clip interfaces (clipped light fixtures, window-blind gaps, a sawtooth
across the window tops). In BOTH the sRGB TIFF and the ACEScg EXR (worse in EXR's
compressed-DR display, but the fringe is in the DATA). LRT JPGs of the same RAW
do NOT have it → it is OUR processing.

## Metric (committed tool)
Over pixels that are BOTH a strong luminance gradient (|∇L*|>p97) AND near a clip
(max-channel>0.97, dilated 7px):
- `chroma_at_edge` = mean Lab hypot(a*,b*); `abs_b_at_edge` = mean |b*| (blue↔yellow).
  NOT DC-invariant → use for SAME-colour-base comparisons (demosaic family).
- `fringe_hp` = RMS of (chroma − local box-mean chroma) — DC-INVARIANT, captures the
  blue↔yellow ALTERNATION; use for matrix/WB ablations (they recolour globally).

Worst-fringe crops (rendered-frame coords): botleft (1792,256,256) = owner's named
worst (clip frac 0.24, |b*|≈41, n≈17k — confirmed the heaviest-clipped block);
fixtures (1024,1280,256); winupper (2048,256,256). Crops saved to /tmp/fringe_crops/.

## ABLATION 1 — demosaic family (rcd vs menon vs mlri), tap9, hl=off

| variant | botleft chroma@edge | botleft fringe_hp | fixtures fringe_hp | wood a* |
|---|---|---|---|---|
| **rcd**   | 35.85 | 13.14 | 25.07 | 15.67 |
| **menon** | 36.03 | 13.14 | 24.52 | 15.71 |
| **mlri**  | 31.37 |  8.14 | 17.06 | 15.95 |

**rcd ≈ menon to ~1% on every metric and crop.** mlri is moderately lower but STILL
fringes heavily (chroma@edge 31, |b*| 30).

### Verdict on H1 (rcd-specific directional false-colour) — REFUTED as dominant cause
The advisor's H1↔H2 discriminator: *if menon ≪ rcd → rcd-specific false-colour (H1);
if menon ≈ rcd → any directional demosaic colours the clip imbalance, H2 dominates.*
menon ≈ rcd → **H1 is refuted as the primary cause.** RCD's known battery weakness
(false-colour 18.5 vs Menon 15.2) does NOT explain this fringe — Menon, the
battery-best, fringes identically. The demosaic *algorithm* is at most a SECONDARY
lever (mlri's different green/residual scheme shaves ~1/3 off fringe_hp but leaves
most of it). The sawtooth/false-colour is therefore NOT principally a demosaic
reconstruction error.

The **wood tint (a*≈15.7, actually warm/magenta not green) does NOT co-vary with the
demosaic** (15.67/15.71/15.95) → the tint is upstream of / independent of demosaic.

→ Points at **H2: the clip-imbalance × WB × camera-matrix rotation** colouring the
edge largely independent of which demosaic produced it. Tested next.
