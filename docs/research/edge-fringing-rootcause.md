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
The H1↔H2 discriminator: *menon ≪ rcd → rcd-specific false-colour (H1); menon ≈ rcd
→ any directional demosaic colours the clip imbalance.* menon ≈ rcd → **RCD-specific
false-colour is refuted.** RCD's battery weakness (false-colour 18.5 vs Menon 15.2)
does NOT explain this — Menon, the battery-best, fringes identically.

**IMPORTANT (logic correction):** this does NOT mean "demosaic is not the source."
*Every* stage after demosaic (Stage 2 WB scalar, Stage 3/4 matrix, Stage 8 LookTable
cube, Stage 9 tone sort) is **per-pixel** — none touch neighbours. So ALL spatial
structure, including the sawtooth ALTERNATION `fringe_hp` measures, MUST be seeded by
the demosaic (and LRT demosaics the same raw cleanly → it is OUR demosaic). The
correct reading: **demosaic interpolating ACROSS THE HARD CLIP seeds the per-channel
spatial variation (any directional algorithm does this — rcd≈menon); the downstream
colour stages (WB asymmetry primarily) colourise that variation blue↔yellow.** An
**interaction**, not one stage. mlri's ~38% lower fringe_hp (13.1→8.1 botleft) is REAL
signal (a different green/residual scheme seeds less cross-clip variation) but leaves
the majority → the demosaic algorithm is a partial lever, not the fix.

The **wood tint (a*≈15.7, warm/magenta not green) does NOT co-vary with the demosaic**
(15.67/15.71/15.95) → independent of demosaic; tracked but not the fringe driver.

→ Next: locate WHERE the chroma grows (tap bisection) + confirm WB as the colouriser.

### Mechanism (from DCP + WB inspection, before ablating)
- WB multipliers this frame: **R=2.0, G=1.0, B=1.289** (ASN [0.5,1.0,0.776]) — a large
  asymmetry. libraw hard-clips each camera channel at 1.0 (camera space, pre-WB).
- The D750 Camera Standard FM is **ProPhoto-passthrough** (`M_xyz→pp·FM1 ≈ I`) → Stage
  3/4 does NO chromatic rotation. NO HueSatMap (Stage 5 absent). Colour comes from WB +
  LookTable (Stage 8) + ProfileToneCurve (Stage 9). **So the "camera-matrix rotation"
  of H2 is really the WB asymmetry (+ LookTable), not a matrix.** Identity-matrix
  ablation is moot and is SKIPPED.
- `apply_rgb_tone` (Stage 9) pins input to [0,1] BEFORE curving → a FULLY-clipped pixel
  `[≥1,≥1,≥1]`→`[1,1,1]`→neutral white. So the tap-9 fringe is NOT from saturated clip
  CORES (those go neutral) — it is from **PARTIALLY-clipped boundary pixels**, exactly
  the demosaic-across-clip region. (Also why hl-recovery, which neutralises full clips,
  is inert at tap-9.)

## ABLATION 2 — tap bisection (linear ProPhoto, same metric, rcd hlF)

| tap | what is applied | chroma@edge | |b*| | fringe_hp | n |
|---|---|---|---|---|---|
| 4 | WB + FM-passthrough only | 26.80 | 16.39 | 9.79 | 128034 |
| 7 | + ExposureRamp (no HSM) | 26.80 | 16.39 | 9.79 | 128034 |
| 9 | + LookTable + ProfileToneCurve | 20.20 | 18.44 | 14.64 | 41839 |

tap4==tap7 (Stage 5 absent, Stage 7 monotone). **fringe_hp = 9.79 already at tap-4
with ONLY WB applied** (FM is passthrough). tap-9 mask shrinks 3× (tone curve pulls
highlights below 0.97) so tap9-vs-tap4 isn't a clean delta, but fringe_hp does rise.

## ABLATION 3 — WB / LookTable bypass at tap9-sRGB, PINNED baseline mask (DC-invariant)

DC-invariant `fringe_hp` (the actual blue↔yellow ALTERNATION = the sawtooth):

| variant | botleft fringe_hp | fixtures fringe_hp | winupper fringe_hp |
|---|---|---|---|
| **baseline (rcd)** | 13.14 | 25.07 | 19.13 |
| **NO_WB** (wb→[1,1,1]) | 11.81 | 26.82 | 17.45 |
| **NO_LOOKTABLE** | 14.40 | 25.14 | 22.24 |
| **NO_WB + NO_LT** | 13.74 | 27.85 | 18.17 |

(raw chroma@edge/|b*| move a lot — NO_WB recolours the whole frame — but those are the
DC level, not the artifact; the DC-invariant fringe_hp is what matters.)

### Verdict on H2 (WB / matrix as the colouriser of the fringe) — REFUTED
**Killing WB does NOT collapse the fringe_hp** (botleft 13.14→11.81, fixtures 25.07→
26.82 — flat / slightly up). Killing the LookTable doesn't either. The DC-invariant
ALTERNATION is **essentially invariant to every colour-stage ablation** (demosaic
rcd↔menon, WB on/off, LookTable on/off). The ONLY lever that moved fringe_hp is the
demosaic *algorithm* (mlri: 13.1→8.1). So WB/LookTable set the fringe's overall hue/
saturation (the DC level) but **do NOT create or destroy the local alternation** — they
are per-pixel maps and cannot. **The artifact's spatial structure is demosaic-seeded
and survives every colour ablation.**

→ The root is the **demosaic's per-channel reconstruction across the hard clip** — a
spatial per-channel imbalance present BEFORE any colour stage. Confirmed next by
measuring the seed directly on balanced camera RGB, pre-colour.
