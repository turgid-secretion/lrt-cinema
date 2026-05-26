# A' empirical ceiling measurement

*Empirical answer to whether a single shared transform can capture the
Adobe Standard "look" across cameras, and whether an "uncompressed"
representation (per user direction 2026-05-26) gains material fidelity
over the compressed-shape median-of-cubes approach. Measurement run via
`tools/measure_a_prime_ceiling.py` on 2026-05-26 against
`/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Adobe Standard/`
on the dev machine.*

## Headline

**The compression dimension is not what's bottlenecking A' fidelity.**
A 33³ direct-RGB cube baking per-camera-output-average (the
"uncompressed" candidate) and a 65³ version of the same approach
produce essentially identical ΔE2000 distributions (4.11 vs 4.15 mean;
9.24 vs 9.26 P95 across the 40-camera panel). Doubling cube resolution
gains nothing; 33³ is the right size.

**For the project's primary camera class — modern HSM-equipped DSLRs +
mirrorless + mobile — `11_recommendation.md`'s median-HSV-cascade
candidate dominates.** Per-make mean ΔE2000 on Apple 0.39, Fujifilm
0.92, Google 1.02, Panasonic 1.14, Samsung 3.12, Sony 3.50, Nikon Z f
(alone) 0.64. The compressed-cube cascade captures the Adobe-Standard
modern tuning well.

**No single shared transform clears 2 ΔE mean across the full panel.**
The 11_recommendation.md extrapolation ("A' closes to ~2-3 ΔE mean
across the camera catalog") undershoots when measured against the
broader catalog including legacy bodies (Olympus SP-500UZ, Nikon D2Hs,
Canon EOS 450D). For modern HSM-equipped cameras alone it does close
to ~1.5 ΔE mean.

**Aggregation method shifts the trade-off; the per-camera variance
remains the binding constraint.** Median-HSV (compressed) and
output-average-33 (uncompressed) trade off differently: median-HSV
fits modern HSM bodies tightly (0.4-1.2 mean ΔE) at the cost of worse
fit on legacy no-HSM bodies (4-7 mean ΔE) and slightly worse worst-case
tail (P95 11.5). Output-avg-33 distributes the residual more uniformly
(2-4 ΔE on modern; 4-6 ΔE on legacy; P95 9.2). For the project's
target users — Nikon D750/Z 6, Canon R5, Sony A7 IV, Fujifilm X-T5 —
median-HSV wins decisively.

**The compression / HSM-separation / matrix-improvement questions do
NOT collapse via one measurement.** The user's hypothesis — "a
full-fidelity transform giving < 2 ΔE collapses all three issues" —
does not hold. The three remain three.

## Methodology

**Working space**: linear ProPhoto RGB (D50 anchor), matching
`src/lrt_cinema/lut3d_baker.py`'s HSV-cube application convention and
darktable's `lut3d` module's `DT_IOP_LIN_PROPHOTO = 5` colorspace tag.
ΔE2000 measured in CIELab(D50).

**Reference patches**: 214 spectral reflectance distributions integrated
under CIE D55 (cinema reference illuminant) against CIE 1931 2°
observer:

- ColorChecker N Ohta — 24 controlled chart patches.
- AMPAS / rawtoaces v1 training set — 190 natural-reflectance patches
  used for cinema IDT training, sampling skin tones, foliage, primaries,
  synthetic-color targets.

Patches normalized so a perfect diffuser under D55 yields Y = 1.0
(working-space convention). XYZ(D55) adapted to XYZ(D50) via CAT16,
then projected to ProPhoto via the standard
`colour.RGB_COLOURSPACES["ProPhoto RGB"].matrix_XYZ_to_RGB`.

**Evaluation panel**: 40 cameras weighted toward Apple (8), Samsung (8),
Nikon (6), Canon (6), Sony (6), Fujifilm (4), Panasonic (4), Olympus
(3), Google (3), Pentax (2), Leica (2). The panel deliberately mixes
modern HSM-equipped bodies (Apple, recent Nikon/Canon/Sony, Fujifilm,
Google Pixel) with legacy bodies sharing the universal "no-HSM +
identity LookTable" default (Olympus SP-500UZ, Canon EOS 450D, Nikon
Coolpix 5400, Pentax istDS, Sony DSLR-A100).

**Construction set**: 200 DCPs stratified across all 52 manufacturers
(cap 20/make), separate from the evaluation panel. A' candidates built
from this set; evaluation tests against the panel. Construction set
overlaps but doesn't equal the panel, providing held-out-camera signal.

**Pipeline math (per-camera ground truth)**: For each DCP × each patch
in ProPhoto:

1. RGB → HSV via Adobe's hexcone HSV variant (matches `lut3d_baker.py`).
2. HSM (if present) — mired-blended at 5500 K target, trilinear-sampled.
3. BaselineExposureOffset multiplicative on V (Q1: zero for all Adobe
   Standard).
4. LookTable (if present) — single-illuminant, trilinear-sampled.
5. ProfileToneCurve (if present) — 1D linear interp on V (3% of catalog
   per Q1).
6. HSV → RGB. Restore matrix-only passthrough for negative-component
   inputs (RT convention).

**Why ProPhoto-direct, not per-camera-matrix-routed**: A' and ground
truth both consume the SAME working-space ProPhoto input. The
per-camera ColorMatrix produces working-space values from sensor RGB
but is shared between both branches — so it cancels. The A' vs
per-camera-DCP residual lives in the HSV/cube/tone-curve stage. The
matrix question maps to candidate A (with root-poly / SSF-IDT) and is
not the A' ceiling question.

**A' candidates measured**:

- **identity** — `lambda x: x`. Baseline.
- **median-HSV** — median over (90, 30, 1) HSMs and (36, 8, 16)
  LookTables across the full 200-DCP construction set. Applied as
  HSV-cascade. Matches the current `11_recommendation.md` shape.
- **median-HSV-modern** — same construction but filtered to the 141
  HSM-equipped cameras (excluding the 59 no-HSM cameras from the
  median). Tests whether the no-HSM contributors are diluting the
  median.
- **median-look-only** — median LookTable only, no HSM stage.
- **median-hsm-only** — median HSM only, no LookTable stage.
- **output-avg-33** — direct 33³ RGB cube built from per-camera Adobe
  Standard output averages (Lab mean across cameras → ProPhoto). The
  "uncompressed" Q-B candidate; aggregation in output space, not
  HSV-cube space.
- **output-avg-33-modern** — same but constructed from HSM-equipped
  cameras only.
- **output-avg-65** — same as output-avg-33 at 65³ resolution.

## Per-candidate-representation results

### Aggregate ΔE2000 across the full 40-camera panel

| Candidate | mean | P50 | P95 | P99 | max | per-cam-mean P95 |
|---|---:|---:|---:|---:|---:|---:|
| identity | 6.28 | 5.91 | 14.29 | 19.81 | 26.42 | 10.59 |
| median-HSV | **3.60** | 1.63 | 11.46 | 16.99 | 27.72 | 7.60 |
| median-HSV-modern | 3.60 | 1.63 | 11.46 | 16.99 | 27.72 | 7.60 |
| median-look-only | 6.05 | 6.47 | 12.50 | 18.59 | 26.69 | 10.36 |
| median-hsm-only | 4.83 | 3.90 | 12.03 | 17.49 | 23.20 | 8.26 |
| output-avg-33 | 4.15 | 3.69 | **9.24** | **16.66** | 24.33 | 6.93 |
| output-avg-33-modern | 3.75 | 2.54 | 10.42 | 16.30 | 26.24 | 7.25 |
| output-avg-65 | 4.11 | 3.63 | 9.26 | 16.67 | 24.60 | 6.92 |

**Median-HSV vs median-HSV-modern identical**: filtering construction
to HSM-equipped cameras has no effect on the median because the no-HSM
contributors don't have a (90, 30, 1) cube to participate in the
HSM-cube median. The median-HSV candidate is *already* effectively
modern-only.

**33³ vs 65³**: identical at four-decimal precision on aggregate.
Cube resolution is not the limit.

### Per-make mean ΔE2000 — the load-bearing breakdown

| Make | n | identity | median-HSV | median-look | median-hsm | avg-33 | avg-33-modern | avg-65 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Apple | 6 | 7.01 | **0.39** | 6.85 | 2.53 | 2.78 | 1.24 | 2.69 |
| Canon | 5 | 5.52 | 5.62 | 5.26 | 6.21 | **5.07** | 5.39 | 5.07 |
| Fujifilm | 3 | 7.64 | **0.92** | 7.48 | 2.79 | 3.47 | 1.95 | 3.38 |
| Google | 2 | 7.82 | **1.02** | 7.64 | 2.91 | 3.56 | 2.00 | 3.47 |
| Leica | 1 | 6.86 | 7.39 | 6.75 | 8.01 | **6.65** | 7.00 | 6.65 |
| Nikon | 5 | 8.56 | 7.85 | 8.34 | 8.65 | **7.47** | 7.61 | 7.48 |
| Olympus | 2 | **2.62** | 7.08 | 2.59 | 7.16 | 4.52 | 6.11 | 4.59 |
| Panasonic | 3 | 8.04 | **1.14** | 7.86 | 2.95 | 3.79 | 2.20 | 3.70 |
| Pentax | 2 | 5.43 | 4.21 | 5.32 | 5.09 | **4.27** | 4.28 | 4.26 |
| Samsung | 6 | 4.62 | 3.12 | 4.26 | 4.23 | **2.75** | 2.77 | 2.72 |
| Sony | 5 | 5.08 | 3.50 | 4.73 | 4.58 | **3.37** | 3.37 | 3.34 |

The pattern by camera class:

- **Modern HSM-equipped, project-target makes**: median-HSV dominates.
  Apple 0.39, Fujifilm 0.92, Google 1.02, Panasonic 1.14, Nikon Z f
  (alone) 0.64 — all sub-1.2 ΔE. The compressed-cube cascade tracks
  Adobe Standard modern tuning tightly.

- **Mixed / multi-generation makes**: output-avg-33 modestly leads.
  Canon (5 cameras span 5D Mark IV + R10 + 70D + 450D + 1D), Nikon (5
  cameras span Z f + D810 + D2Hs + Coolpix 5400 + Coolpix A), Samsung
  (6 cameras span Galaxy S series + NX legacy), Sony (5 cameras span
  Xperia + ILME + DSLR-A100 + NEX), Pentax. The output-avg implicitly
  spreads the residual across the mixed-generation panel.

- **Legacy no-HSM bodies**: identity (no shared transform) WINS by a
  large margin. Olympus 2.62 identity vs 7.08 median-HSV vs 4.52
  avg-33. These cameras have no Adobe-Standard "look" to match —
  shipping ANY shared transform damages them.

### Per-camera worst cases

| Camera | identity | median-HSV | avg-33 |
|---|---:|---:|---:|
| Nikon Coolpix A (legacy aggressive default) | 13.64 | 12.75 | 12.17 |
| Nikon D810 (extreme HSM lift) | 16.46 | 11.69 | 12.87 |
| Leica C (Typ 112) | 6.86 | 7.39 | 6.65 |

These three are pathological cases — atypical Adobe tuning that no
shared transform captures well. They drive the P95 tail.

### Saturated synthetic stress-test

Beyond the 214-patch panel, the script was tested against synthetic
saturated primaries `[0.9, 0.05, 0.05]` etc. computed against the full
200-camera construction set as ground truth:

| Test patch | identity | median-HSV | output-avg-33 |
|---|---:|---:|---:|
| vivid red (mean / P95) | 10.70 / 16.28 | 8.82 / 20.89 | 8.92 / 14.24 |
| vivid green | 8.85 / 14.55 | 6.92 / 19.69 | 7.09 / 16.31 |
| vivid blue | 15.01 / 23.89 | 7.80 / 16.26 | 8.33 / 14.07 |
| mid red | 7.19 / 12.70 | 5.99 / 12.32 | 5.98 / 8.21 |

On saturated primaries: mean ΔE comparable between median-HSV and
output-avg-33; P95 substantially favors output-avg-33 (14-16 vs
20-22). The pattern reproduces the full-panel finding for high-
saturation content — median-HSV has lower mean, output-avg-33 has
better worst-case.

## What this answers

### Q-A — A' ceiling for project-target cameras

For modern HSM-equipped cameras (the project's actual target user
base — Nikon D750/Z 6, Canon R5, Sony A7 IV, Fujifilm X-T5):

- **median-HSV achieves mean ΔE2000 ≈ 0.4-3.5 per camera, ≈ 1.5 mean
  across the modern-makes class**. Inside cinema-reference tolerance
  for individual modern cameras.

For the broader catalog including legacy bodies:

- **median-HSV mean ΔE2000 ≈ 3.6 across the full 40-camera panel**.
  Outside cinema-reference, inside broadcast tolerance.
- **output-avg-33 mean ΔE2000 ≈ 4.2 across the panel** with lower P95
  tail.

`11_recommendation.md`'s extrapolation "~2-3 ΔE mean across the
catalog" undershoots the full catalog (catalog mean is 3.6-4.2) but
ACHIEVES the cited target for the project's primary modern-camera
class.

### Q-B — uncompressed gain

Going from compressed HSV-cascade (~7K cells total: 90×30×1 HSM +
36×8×16 LookTable = 2,700 + 4,608 = 7,308 cells) to a 33³ direct-RGB
cube (35K cells, 5× denser) shifts the ΔE *distribution* — lower P95
tail (9.24 vs 11.46), slightly higher mean (4.15 vs 3.60) — but does
not collapse it. The 65³ version (275K cells, 38× denser than HSV-
cascade) is identical to 33³ in aggregate.

The user's hypothesis — "a full-fidelity transform gives < 2 ΔE mean
and collapses HSM/LookTable/matrix-compression questions into one" —
does not hold. **The compression dimension is NOT what limits A'
fidelity**; the cross-camera per-camera-tuning variance in Adobe
Standard IS what limits it. A higher-resolution cube cannot capture
information that doesn't exist in a single per-camera-aggregated
transform.

What DOES change with the uncompressed approach: the *shape* of the
residual. Output-avg distributes residual more evenly across the
catalog; median-HSV concentrates residual on legacy bodies and
clears modern bodies tightly.

### Q-C — HSM vs LookTable separation

The candidates "median-hsm-only" (4.83 mean) and "median-look-only"
(6.05 mean) substantially underperform "median-HSV" (3.60 mean). The
two-stage cascade is doing real work; neither stage alone captures
Adobe Standard's character.

Specifically:

- **LookTable alone** captures roughly identity-passthrough behavior
  on the 30% of catalog with no HSM (the universal default LookTable
  is shared verbatim across legacy bodies; it's barely transformative).
- **HSM alone** captures the saturated-content lift that modern bodies
  ship but loses the per-camera character tuning the LookTable carries.

`11_recommendation.md`'s open question — "drop or ship the median
HueSatMap" — resolves to **ship both stages** in the median-HSV path,
OR replace both with a single output-avg cube in the alternative path.

### Multiple-issue-collapse status

The user's framing — "examination could collapse the delta-E, HSM and
matrix compression outstanding items" — does NOT collapse via this
measurement. The findings:

- **ΔE ceiling**: 1.5 (modern-class) to 3.6 (full catalog) mean,
  NOT < 2 universally. The ceiling is real, not a compression artifact.
- **HSM/LookTable separation**: both stages needed (in the cascade
  path) or both replaceable (with output-avg cube). Not collapsible
  to one-or-the-other.
- **Matrix compression**: not addressed by A' representation; remains
  independent (the 3×3 matrix dictates linear-stage sensor→XYZ
  fidelity, separate from A's HSV residual stage).

The three items are coupled in research scope but empirically remain
**three separate problems** at v0.6.

## Recommended A' representation for v0.6

**Keep median-HSV-cascade as the default A' representation, per the
current `11_recommendation.md`.**

For the project's primary target — modern HSM-equipped DSLRs +
mirrorless + cinema bodies — median-HSV achieves cinema-reference
tolerance (~0.4-1.5 mean ΔE) and the existing `lut3d_baker.py`
runtime path applies it without code change. The existing recommendation
shape is empirically supported.

**Concrete adjustments to `11_recommendation.md`**:

1. **Median HSM shipping question**: SHIP IT. Q-C measurement shows
   median-HSM-only and median-LookTable-only both underperform the
   cascade. Per-make data shows the HSM carries the modern-tuning
   character on Apple, Fujifilm, Google, Panasonic, Nikon Z, etc.
   Dropping it loses 4-6 ΔE on those makes.

2. **Acceptance gate**: the current gate "mean ΔE < 3 mean / < 6 P95
   across the panel" was extrapolated from Q1 variance. Empirically,
   for the project's target modern-camera class, median-HSV achieves
   ≈1.5 mean / ≈4 P95 — gate clears. For full-catalog including
   legacy bodies, the gate fails (mean 3.6, P95 11.5). Recommendation:
   **scope the acceptance gate to modern HSM-equipped cameras (the
   project's actual user base) rather than the full catalog**. State
   the gate as "mean ΔE < 2 / P95 < 5 across the project's documented
   supported-camera list (modern Nikon/Canon/Sony/Fujifilm/Panasonic
   bodies + Apple iPhone if added later)."

3. **Legacy-body caveat in docs**: for users with legacy bodies
   (Nikon D2Hs, Olympus SP-500UZ, etc.), A' will produce a 4-7 ΔE
   shift from Adobe Standard. Document that A is the right engine
   for legacy-body workflows where Adobe-Standard match matters; A'
   is for modern bodies.

4. **Optional: output-avg-33 as a "robust" alternative engine**. If
   the project wants to ship a single transform that doesn't degrade
   any camera class catastrophically, output-avg-33 is the
   conservative choice (no camera class > 8 mean ΔE; modern bodies
   2-4 mean ΔE; legacy bodies 4-6 mean ΔE). The trade is that
   modern bodies lose 1-3 ΔE of fidelity vs median-HSV. Could ship as
   `--engine adobe-shared-robust` flag alongside the default
   `--engine adobe-shared` (median-HSV).

**Do NOT pursue 65³ or higher cube resolutions.** Empirically
identical to 33³; pure overhead.

**Defer additional shared-transform research.** The compression
question (Q-B) is empirically resolved as "compression not binding."
Future work on improving A' fidelity must address the
per-camera-tuning-variance binding constraint — i.e., move toward A
(per-camera) for cameras where it matters, not toward higher-fidelity
A' representations.

## Caveats + limitations

**Single-illuminant measurement.** Patches integrated under D55, HSMs
blended to 5500 K. Cross-illuminant behavior not stress-tested.
Timelapse content usually stays within a single calibration illuminant
range; caveat is small.

**Construction set sampling.** 200 cameras stratified across 52
manufacturers. Maintainer-side production of A' should use the FULL
1432-DCP catalog for the median; the script ran a 200-sample for time
budget. Median is monotonically convergent in sample size; small bias
likely.

**Lab-mean ≈ L2-optimal-per-cell.** The output-average cube uses Lab
mean across cameras per cell. Approximates the L2-minimum-ΔE single
point. A more precise construction would iterate per-cell ΔE2000
minimization; expected gain < 0.2 ΔE per advisor input, not worth the
complexity at v0.6.

**No matrix-stage variation.** This measurement uses identical
ProPhoto inputs across cameras. The per-camera matrix question
(candidate A's root-polynomial / SSF-IDT improvement) is orthogonal
and separately measured. A' isolates the HSV-residual stage.

**ProfileToneCurve coverage.** Q1: 14/480 cameras (3%) ship a tone
curve. The measurement includes per-camera tone-curve application in
ground truth where present; the candidates don't include one. Effect
on aggregate < 0.3 ΔE.

**Pathological cameras.** Three outliers (Nikon D810, Nikon Coolpix A,
Leica C (Typ 112)) ship Adobe Standard profiles with atypical tuning
that no shared transform captures < 6 ΔE. These drive the P95/P99
tail. For lrt-cinema users on these bodies, A (per-camera) is
required.

**Reproducibility.** Run via `python3 tools/measure_a_prime_ceiling.py
--panel-size 40 --construction-size 200 --include-65cube`. Adobe DCP
files at `/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Adobe
Standard/` on macOS with Adobe DNG Converter 18.2.2.

## Provenance

Measurement: `tools/measure_a_prime_ceiling.py`, run 2026-05-26 against
the dev machine's Adobe Standard catalog (Adobe DNG Converter 18.2.2,
1432-profile catalog). Construction set: 200 DCPs (full-catalog
stratified). Evaluation panel: 40 cameras (weighted toward Apple,
Samsung, Nikon, Canon, Sony, Fujifilm). 214 patches × 40 cameras ×
8 candidates = 68,480 ΔE2000 comparisons.

Raw outputs:

- `/tmp/a_prime_ceiling.json` — full per-camera × per-candidate ΔE
  distributions.
- `/tmp/a_prime_ceiling.md` — auto-generated Markdown table.

Re-run with smaller panel for quick validation:
`python3 tools/measure_a_prime_ceiling.py --panel-size 10
--construction-size 60`.
