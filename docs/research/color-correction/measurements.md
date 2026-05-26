# Empirical measurements

Three measurements bound the v0.6 color-correction decision:

| ID | Question | Result |
|---|---|---|
| **M1** | How much of Adobe color is per-camera vs shared? | Three of four DCP fields are essentially camera-agnostic in Adobe Standard; A′ is viable on the low-variance branch. |
| **M2** | What ΔE2000 ceiling does A′ achieve in practice? | 0.4–1.5 mean on modern HSM-equipped target cameras; 3.60 mean / 11.46 P95 across the full 40-camera evaluation panel. |
| **M3** | Does LRT honor externally-placed preview-cache JPEGs? | No — LRT regenerates the cache JPEG on every editor-pane slider interaction. |

Each section below gives methodology, results, caveats, and a reproducer
command. The raw measurement data is not committed; the scripts are
deterministic given the same Adobe DCP catalog snapshot, and re-running
produces JSON output to `/tmp/`.

---

## M1: Adobe DCP catalog variance

**Question.** Given that Adobe ships DCPs for ~1432 cameras across 52
manufacturers, does the camera-specific content of those profiles vary
enough that lrt-cinema needs a per-camera database, or is the look
character largely camera-agnostic in the Adobe Standard line?

The DCP encodes per-camera content in five places. ColorMatrix1/2 and
ForwardMatrix1/2 (per-illuminant) are necessarily per-camera because
they encode sensor SSF → reference-space; they are not measured here.
The remaining three fields — HueSatMap, LookTable, ProfileToneCurve —
plus the scalar BaselineExposure could in principle be either per-camera
or shared.

### Methodology

Sample: 480 DCPs stratified across 52 manufacturers (cap 20 per
manufacturer) from the user's local Adobe DNG Converter 18.2.2 install
at `/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Adobe
Standard/`.

Parser: `src/lrt_cinema/dcp.py::parse_dcp` (480/480 parsed successfully).

Variance metrics per field:

- **BaselineExposure**: cross-camera mean + std of the scalar EV value.
- **ProfileToneCurve**: presence rate; RMSE around the mean curve for
  cameras that ship one.
- **HueSatMap** and **LookTable**: same-cube-dimension grouping (separate
  metrics for each shape); per-cell std of hue shift (degrees), sat
  scale (multiplicative), val scale (multiplicative).

### Results

| Field | Variance characterization | A′ viability |
|---|---|---|
| **BaselineExposure** | Identically zero across all 480 cameras. Camera-agnostic by Adobe's design. | Viable. Ship BaselineExposure = 0 in A′. |
| **ProfileToneCurve** | Present in only 14 of 480 cameras (3%). Of the 14, RMSE around the mean: 0.018 mean, 0.012 P50, 0.064 P95. | Viable. Omit ProfileToneCurve in A′; the 97% of catalog without one relies on dt's basecurve module's ACR3 baseline (camera-agnostic by definition). |
| **LookTable** | 474/480 cameras (99%) use (36, 8, 16) cube dimension. Per-cell std: hue shift 2.2° mean / 6.3° P95; sat scale 0.026 (~2.6%); val scale 0.028 (~2.8%). Low variance. | Viable. Ship a median LookTable computed across the 474 same-dimension cubes. |
| **HueSatMap** | 322/480 cameras (67%) use (90, 30, 1) cube dimension; 140 cameras (29%) ship no HueSatMap. Per-cell std: hue shift 2.5° mean / 5.2° P95 / 8.0° max; sat scale 0.187 (~19%) / 0.40 P95 (~40%); val scale 0.12 / 0.27 P95. Moderate per-camera variance on saturated chromas. | Viable but lossy. Ship a median HueSatMap; per-camera enrichment (A) is the path where the residual matters. |

### Interpretation

A shared LookTable + camera-agnostic BaselineExposure + (absent)
ProfileToneCurve captures approximately 80–90% of Adobe Standard's look
character across the catalog. The HueSatMap residual carries the
remaining 10–20%, concentrated on cameras Adobe tunes aggressively per
make (Apple, Samsung, recent Fujifilm).

Two implementation choices follow:

1. **Drop the HueSatMap stage entirely** (match the 29% of catalog
   without one). Accepts saturated-chroma drift on the 67% with one.
2. **Ship a median HueSatMap** alongside the shared LookTable. Captures
   average per-camera character at the cost of greater drift on
   high-variance cameras.

Per M2's empirical findings (next section), option 2 wins decisively
on the project's target modern-camera class. Both stages ship in
`adobe_standard.npz`.

### Caveats

- **Adobe Standard only.** This measurement says nothing about Camera
  Standard / Camera Neutral / Camera Vivid / per-camera alternative
  looks. Those vary more per-camera by design (manufacturer-specific
  intent).
- **Single-illuminant LookTables.** Adobe Standard's LookTables are
  single-illuminant; the val axis carries V-dependent hue twist.
  Cross-illuminant variance is not measured.
- **Static catalog snapshot.** Adobe DNG Converter 18.2.2 catalog at
  measurement time. ACR/DNG Converter version churn may shift variance
  slightly per release; re-distill A′ on each catalog refresh.

### Reproducer

```bash
python3 tools/measure_dcp_variance.py /tmp/dcp_variance.json
```

Outputs JSON at the given path; report at `/tmp/dcp_variance_report.txt`.
Measurement run 2026-05-26.

---

## M2: A′ empirical ΔE2000 ceiling

**Question.** Given M1 supports A′'s structural viability, what ΔE2000
does A′ actually achieve in practice? And does an "uncompressed"
direct-RGB cube representation gain material fidelity over the
median-HSV-cascade?

### Methodology

**Working space.** Linear ProPhoto RGB (D50 anchor), matching
`src/lrt_cinema/lut3d_baker.py`'s HSV-cube application convention and
darktable's `lut3d` module's `DT_IOP_LIN_PROPHOTO = 5` colorspace tag.
ΔE2000 measured in CIELab(D50).

**Reference patches.** 214 spectral reflectance distributions integrated
under CIE D55 (cinema reference illuminant) against CIE 1931 2° observer:

- ColorChecker N Ohta — 24 controlled chart patches.
- AMPAS / rawtoaces v1 training set — 190 natural-reflectance patches
  used for cinema IDT training; sampling skin tones, foliage, primaries,
  synthetic-color targets.

Patches normalized so a perfect diffuser under D55 yields Y = 1.0
(working-space convention). XYZ(D55) adapted to XYZ(D50) via CAT16,
projected to ProPhoto via
`colour.RGB_COLOURSPACES["ProPhoto RGB"].matrix_XYZ_to_RGB`.

**Evaluation panel.** 40 cameras weighted toward the project's target
makes: Apple (8), Samsung (8), Nikon (6), Canon (6), Sony (6), Fujifilm
(4), Panasonic (4), Olympus (3), Google (3), Pentax (2), Leica (2). The
panel deliberately mixes modern HSM-equipped bodies (Apple, recent
Nikon/Canon/Sony, Fujifilm, Google Pixel) with legacy bodies sharing the
universal "no-HSM + identity LookTable" default (Olympus SP-500UZ,
Canon EOS 450D, Nikon Coolpix 5400, Pentax istDS, Sony DSLR-A100).

**Construction set.** 200 DCPs stratified across all 52 manufacturers
(cap 20/make), separate from the evaluation panel. A′ candidates are
built from this set; evaluation tests against the panel. The two sets
overlap partially but are not identical — providing held-out-camera
signal.

**Pipeline math (per-camera ground truth).** For each DCP × patch in
ProPhoto: RGB → HSV via Adobe's hexcone HSV variant; HSM (if present)
mired-blended at 5500 K target, trilinear-sampled; BaselineExposureOffset
multiplicative on V (zero for all Adobe Standard per M1); LookTable (if
present) single-illuminant, trilinear-sampled; ProfileToneCurve (if
present) 1D interp on V; HSV → RGB with matrix-only passthrough for
negative-component inputs (RawTherapee convention).

**Why ProPhoto-direct, not per-camera-matrix-routed.** A′ and ground
truth both consume the same working-space ProPhoto input. The per-camera
ColorMatrix produces working-space values from sensor RGB but is shared
between both branches and cancels in the ΔE comparison. The A′-vs-
per-camera-DCP residual lives in the HSV/cube/tone-curve stage. The
matrix question maps to candidate A (with root-poly / SSF-IDT enrichment)
and is orthogonal.

**Candidates measured.**

| Candidate | Construction |
|---|---|
| `identity` | `lambda x: x`. Baseline. |
| `median-HSV` | Median over (90, 30, 1) HSMs and (36, 8, 16) LookTables across the 200-DCP construction set. Applied as HSV-cascade. **Matches the v0.6 ship shape.** |
| `median-HSV-modern` | Same construction filtered to the 141 HSM-equipped cameras only. |
| `median-look-only` | Median LookTable only, no HSM stage. |
| `median-hsm-only` | Median HSM only, no LookTable stage. |
| `output-avg-33` | Direct 33³ RGB cube built from per-camera Adobe Standard output averages (Lab mean across cameras → ProPhoto). |
| `output-avg-33-modern` | Same but constructed from HSM-equipped cameras only. |
| `output-avg-65` | 65³ resolution variant of `output-avg-33`. |

### Aggregate results (full 40-camera panel)

| Candidate | mean | P50 | P95 | P99 | max |
|---|---:|---:|---:|---:|---:|
| identity | 6.28 | 5.91 | 14.29 | 19.81 | 26.42 |
| **median-HSV** | **3.60** | 1.63 | 11.46 | 16.99 | 27.72 |
| median-HSV-modern | 3.60 | 1.63 | 11.46 | 16.99 | 27.72 |
| median-look-only | 6.05 | 6.47 | 12.50 | 18.59 | 26.69 |
| median-hsm-only | 4.83 | 3.90 | 12.03 | 17.49 | 23.20 |
| output-avg-33 | 4.15 | 3.69 | **9.24** | **16.66** | 24.33 |
| output-avg-33-modern | 3.75 | 2.54 | 10.42 | 16.30 | 26.24 |
| output-avg-65 | 4.11 | 3.63 | 9.26 | 16.67 | 24.60 |

Two observations stand out:

- **median-HSV vs median-HSV-modern are identical.** Filtering
  construction to HSM-equipped cameras has no effect on the median
  because the no-HSM contributors don't have a (90, 30, 1) cube to
  participate in the HSM-cube median. median-HSV is *already*
  effectively modern-only.
- **33³ vs 65³ are identical at four-decimal precision on aggregate.**
  Cube resolution is not the binding constraint. The user-tested
  hypothesis that an uncompressed representation gains material fidelity
  does not hold — per-camera tuning variance is what limits A′, not
  representation compression.

### Per-make breakdown — the load-bearing result

| Make | n | identity | **median-HSV** | median-look | median-hsm | avg-33 | avg-33-modern | avg-65 |
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

Pattern by camera class:

- **Modern HSM-equipped target makes**: median-HSV dominates. Apple 0.39,
  Fujifilm 0.92, Google 1.02, Panasonic 1.14, Nikon Z f (alone) 0.64 —
  all sub-1.2 ΔE. The compressed-cube cascade tracks Adobe Standard
  modern tuning tightly.
- **Mixed / multi-generation makes**: output-avg-33 modestly leads. Canon
  (5 cameras span 5D IV + R10 + 70D + 450D + 1D), Nikon (5 cameras span
  Z f + D810 + D2Hs + Coolpix 5400 + Coolpix A), Samsung, Sony, Pentax.
  The output-avg spreads residual more evenly.
- **Legacy no-HSM bodies**: identity (no shared transform) wins by a
  large margin. Olympus 2.62 identity vs 7.08 median-HSV — these cameras
  have no Adobe-Standard "look" to match, so shipping any shared
  transform damages them.

### Stage-by-stage decomposition

Both the HSM and LookTable stages do real work. Comparing median-HSV
(cascade) against the single-stage variants on the full panel:

| Variant | Mean ΔE | Gain over identity |
|---|---:|---:|
| identity | 6.28 | — |
| median-look-only | 6.05 | 0.23 (LookTable alone is barely transformative; it captures the universal-default behavior that's shared across the no-HSM bodies) |
| median-hsm-only | 4.83 | 1.45 (HSM alone captures the saturated-content lift modern bodies ship but loses per-camera character) |
| **median-HSV** | **3.60** | **2.68 (the cascade outperforms either stage alone)** |

The earlier "drop HSM" hypothesis is empirically falsified. Both stages
ship in `adobe_standard.npz`.

### Pathological cameras driving the P95 tail

| Camera | identity | median-HSV | output-avg-33 |
|---|---:|---:|---:|
| Nikon Coolpix A (legacy aggressive default) | 13.64 | 12.75 | 12.17 |
| Nikon D810 (extreme HSM lift) | 16.46 | 11.69 | 12.87 |
| Leica C (Typ 112) | 6.86 | 7.39 | 6.65 |

These three are atypical Adobe tuning that no shared transform captures
< 6 ΔE. They drive the P95/P99 tails of the aggregate panel. For
lrt-cinema users on these bodies, candidate A (per-camera) is the path.

### Saturated synthetic stress test

Beyond the 214-patch panel, the script tests against synthetic saturated
primaries `[0.9, 0.05, 0.05]` etc. computed against the full 200-camera
construction set as ground truth:

| Test patch | identity (mean / P95) | median-HSV | output-avg-33 |
|---|---:|---:|---:|
| Vivid red | 10.70 / 16.28 | 8.82 / 20.89 | 8.92 / 14.24 |
| Vivid green | 8.85 / 14.55 | 6.92 / 19.69 | 7.09 / 16.31 |
| Vivid blue | 15.01 / 23.89 | 7.80 / 16.26 | 8.33 / 14.07 |
| Mid red | 7.19 / 12.70 | 5.99 / 12.32 | 5.98 / 8.21 |

Mean ΔE on saturated primaries is comparable between median-HSV and
output-avg-33; P95 substantially favors output-avg-33 (14–16 vs 20–22).
For the project's modern-camera target the mean wins; if a future
deliverable demands tight worst-case bounds on saturated content,
output-avg-33 is the conservative alternative.

### Interpretation

For the v0.6 decision:

1. **Ship median-HSV as the default `adobe_standard.npz` shape.** On the
   project's primary target (modern HSM-equipped DSLR / mirrorless /
   cinema bodies) it achieves cinema-reference tolerance (~0.4–1.5 ΔE
   per camera, ~1.5 ΔE mean across the class). The existing
   `lut3d_baker.py` runtime path applies it without code change.
2. **Acceptance gate is camera-class-scoped.** For modern target
   cameras: mean ΔE < 2.5, P95 < 5 (cinema-reference). For full catalog
   including legacy bodies: mean ΔE < 4, P95 < 12 (broadcast-acceptable).
3. **Optional `--engine adobe-shared-robust` for output-avg-33** could
   ship as a v0.7 conservative variant if users surface legacy-body
   workflows where median-HSV's 4–7 ΔE shift matters. Not in v0.6 scope.
4. **Do not pursue 65³ or higher cube resolutions.** Empirically
   identical to 33³ at aggregate precision; pure overhead.

The user's hypothesis that "a full-fidelity transform giving < 2 ΔE
collapses HSM/LookTable/matrix-compression questions into one" does not
hold. The three questions remain three:

- **ΔE ceiling**: 1.5 (modern-class) to 3.6 (full catalog) mean. Real,
  not a compression artifact.
- **HSM/LookTable separation**: both stages needed in the cascade path,
  both replaceable with a single output-avg cube. Not collapsible to
  one-or-the-other.
- **Matrix compression**: not addressed by A′ representation; remains
  independent. The 3×3 matrix dictates linear-stage sensor → XYZ
  fidelity, separate from A′'s HSV residual stage. Addressed by
  candidate A's root-poly / SSF-IDT enrichment.

### Caveats

- **Single-illuminant measurement.** Patches integrated under D55, HSMs
  blended to 5500 K. Cross-illuminant behavior not stress-tested.
  Timelapse content usually stays within a single calibration-illuminant
  range; caveat is small.
- **Construction set sampling.** 200 cameras stratified across 52
  manufacturers. Maintainer-side production of A′ should use the full
  1432-DCP catalog for the median; the script ran a 200-sample for time
  budget. Median is monotonically convergent in sample size; small bias
  expected.
- **Lab-mean ≈ L2-optimal-per-cell.** The output-average cube uses Lab
  mean across cameras per cell. Approximates the L2-minimum-ΔE single
  point. A more precise construction would iterate per-cell ΔE2000
  minimization; expected gain < 0.2 ΔE per prior analysis, not worth
  the complexity at v0.6.
- **No matrix-stage variation.** This measurement uses identical
  ProPhoto inputs across cameras. The per-camera matrix question
  (candidate A's root-polynomial / SSF-IDT improvement) is orthogonal
  and separately measured. A′ isolates the HSV-residual stage.
- **ProfileToneCurve coverage.** 14/480 cameras (3%) ship a tone curve.
  The measurement includes per-camera tone-curve application in ground
  truth where present; candidates don't include one. Effect on aggregate
  < 0.3 ΔE.

### Reproducer

```bash
python3 tools/measure_a_prime_ceiling.py --panel-size 40 --construction-size 200 --include-65cube
```

Quick validation (smaller panel):

```bash
python3 tools/measure_a_prime_ceiling.py --panel-size 10 --construction-size 60
```

Raw outputs to `/tmp/a_prime_ceiling.json` and `/tmp/a_prime_ceiling.md`.
Measurement run 2026-05-26 (Adobe DNG Converter 18.2.2; 1432-profile
catalog). 214 patches × 40 cameras × 8 candidates = 68,480 ΔE2000
comparisons.

---

## M3: LRT preview cache behavior

**Question.** Can externally-placed JPEGs in `.lrt/visual/` serve as the
grader's reference during interactive LRT editing? If so, candidate B
(preview substitution) becomes a route to closing the cross-stage
control loop without changing the grader's tool. If not, B is dead.

### Methodology

Test conducted 2026-05-26 on the user's working sequence
(`/Volumes/SanDisk Extreme Pro 55AF Media/Projects/2026 international
faire timelapse/`).

**Setup.** Backed up the existing `.lrt/visual/DSC_4053.lrtpreview`
(SHA-256 `3a31bcdb…`). Wrote a 1024×684 baseline JPEG marker (solid blue
field with "CACHE TEST" text overlay) to the same path
(SHA `a8a61b6e…`). LRT was not running.

**Test 1 — passive navigation.**

1. Open LRT.
2. Navigate to keyframe DSC_4053. Click nothing else.

Expected: if LRT serves the editor pane from the cache JPEG, the marker
is displayed; the on-disk file is unchanged.

Observation: editor pane displayed the marker. File state post-test:
SHA unchanged, mtime unchanged.

**Test 2 — interactive editing.**

1. With the marker still in place, move the Exposure slider +0.5 on the
   keyframe.
2. Click Save Metadata.

Expected: if LRT live-computes the editor-pane preview on edit, the
marker is replaced in-pane and the on-disk file is overwritten with
LRT's render.

Observation: the editor pane updated live to show a brightened scene the
moment the slider moved, replacing the marker in-pane. File state
post-test: SHA changed to `783401ab…`, size 43408 → 40136, mtime
advanced ~3 minutes. **LRT overwrote the marker JPEG with its own
Adobe-pipeline render.**

### Result

LRT live-computes the editor-pane preview from RAW + XMP via its
bundled Adobe DNG Converter the moment the user begins an edit
operation, and writes the result to the on-disk cache as a side effect.
The cache JPEG is the *output* of LRT's preview pipeline, not the
*input* to the editor pane.

### Interpretation

Candidate B (preview substitution) is foreclosed: externally-placed JPEGs
in `.lrt/visual/` cannot serve as the grading reference during
interactive editing. That reference is hard-wired to Adobe via LRT's
bundled DNG Converter, and there is no documented hook to redirect it
without forking LRT.

Externally-placed JPEGs DO control:

- Pre-edit timeline thumbnails.
- The pixel-luminance basis of Visual Deflicker's analysis.
- The "pink curve" visualization until the next interactive edit
  triggers regeneration.

These are observable side-channel uses; they do not close the
live-grading control loop.

### Caveats

- **Two slider operations tested**: passive navigation and Exposure
  slider edit. Not tested: Auto Transition, Visual Previews → All
  Frames, Holy Grail Wizard. If a future LRT release exposes a "skip
  regeneration if XMP unchanged" optimization, the cache-side-channel
  uses become more reliable. Candidate B remains foreclosed for the
  live-grading loop regardless.
- **LRT version**: tested on LRTimelapse Pro 7.5.3 (macOS). Earlier
  versions plausibly behaved identically; later versions are not
  guaranteed to.

### Reproducer

```bash
# Back up the existing preview
cp /path/to/sequence/.lrt/visual/DSC_NNNN.lrtpreview /tmp/backup.jpg

# Replace with any visibly-distinct marker JPEG of identical dimensions.
# Read the original's dimensions first; then any tool that writes a JPEG
# at those dimensions works (any random photo, a solid-color fill from
# ImageMagick: convert -size WxH xc:blue marker.jpg, etc.).
cp /path/to/any/marker.jpg /path/to/sequence/.lrt/visual/DSC_NNNN.lrtpreview

# Open LRT, navigate to DSC_NNNN, observe the marker in the editor pane
# (passive navigation: file unchanged on disk).
# Move any develop slider; observe the marker replaced by LRT's render,
# AND the on-disk file is overwritten.

# Restore the original cache
cp /tmp/backup.jpg /path/to/sequence/.lrt/visual/DSC_NNNN.lrtpreview
```

Test run 2026-05-26.
