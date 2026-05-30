# Pipeline validation

How to objectively test what comes out of `lrt-cinema`, what the
project can and cannot prove under automated test, and the
first-class references behind the methodology.

## The three-way ambiguity in "matches an external reference"

"Verify against an external known reference" splits into three
distinct goals. They have different feasibility, and the difference
matters for what the project can defensibly claim.

### (a) "Matches Adobe Lightroom Classic / Camera Raw"

Tests **LRT-intent fidelity** — does our render look like the
render an LRT user would have produced under the canonical workflow
(LRT → Lightroom)? This is the gold standard for *intent
preservation* because the LRT XMP schema's semantics are defined by
what Adobe Camera Raw does with each `crs:*` field.

**Feasibility:** infeasible to automate. Lightroom Classic has no
headless / scriptable rendering surface, and Camera Raw's processing
is closed-source. The only honest verification is a manual frame-pair
regression: render the same RAW + XMP through LR by hand, render
through `lrt-cinema`, compare. This makes "matches LR" a manual
acceptance test, not a CI gate.

There is no shortcut here. The dtLapse project (prior art) made the
same trade-off.

### (b) "Colorimetrically accurate scene-referred RAW conversion"

Tests whether the pipeline produces correct CIE XYZ for known input
colors. This is the bulletproof automated test, and what
"cinema-grade" can be made to mean under CI.

**Feasibility:** fully automatable. Methodology in the next section.

### (c) "Matches another cinema RAW pipeline (ARRI / RED / ACES IDT)"

Tests cross-pipeline equivalence: does our scene-linear output match
what an ACES-compliant IDT would have produced from the same RAW?
Useful when the downstream is an ACES timeline and another department
wants byte-for-byte alignment with a different ingest tool.

**Feasibility:** automatable per camera, but only for cameras with a
published IDT (most cinema-class cameras: ARRI ALEXA, RED, Sony
Venice). Cameras without one (most stills bodies used for timelapse —
Canon EOS R5, Nikon Z9, Fuji GFX series) fall back to (b), since
ACES-via-matrix-from-DCP is an approximation, not a reference.

## The bulletproof automated test (option b)

**One-sentence procedure:** photograph an X-Rite ColorChecker
Classic under controlled illumination, render through `lrt-cinema`,
extract per-patch mean RGB, convert measured RGB to CIE XYZ via the
output color space's known matrix, compute ΔE2000 against the
ColorChecker's published patch values under the same illuminant.

### Step-by-step

1. **Shoot the chart.**
   - X-Rite ColorChecker Classic, 24-patch (standard) or Digital SG
     (140-patch, finer test).
   - Controlled illuminant — D55 daylight is the conventional choice
     (matches the patches' published reference values without
     chromatic-adaptation drift).
   - Camera exposed so the white patch sits around middle grey + 1 EV
     (≈ 70% on a linear scale), well away from clipping.
   - Same camera model + firmware as the timelapse footage being
     graded — temperature/tint multipliers are camera-specific.

2. **Establish ground truth.** The X-Rite published reference values
   are CIE L\*a\*b\* under D50/2°. Convert to your reference
   illuminant (D55 if shot under D55) using a chromatic adaptation
   transform (Bradford is the conventional default; CAT02 is the
   modern preference).

3. **Render through the pipeline.** Run `lrt-cinema render --preset
   cinema-linear` against the RAW + a hand-authored XMP that sets:
   - Exposure: as shot
   - White balance: as-shot camera temperature
   - Everything else: identity (no contrast, no tone curve, etc.)

   This isolates the RAW → linear Rec.2020 path from any creative
   develop ops. Re-run with `cinema-aces` and `stills-finished` to
   cover the other two presets.

4. **Sample the patches.** Use `colour-science.colour_checker_detection`
   (Python) to auto-detect the chart and extract per-patch mean RGB,
   or sample by hand in a known-pixel-coordinate workflow.

5. **Convert measured RGB → XYZ.** Multiply by the known
   Rec.2020-to-XYZ matrix (ITU-R BT.2020 §3.3) or for the ACES preset,
   AP0-to-XYZ. Apply the same chromatic adaptation as step 2.

6. **Compute ΔE2000 per patch** against the reference XYZ (Sharma /
   Wu / Dalal 2005). `colour-science.colour.delta_E()` implements
   the CIE 159:2004 formula.

7. **Interpret.**

   | ΔE2000 | Conventional interpretation |
   |---|---|
   | < 1.0 | Imperceptible to a trained observer |
   | 1–2 | Perceptible by side-by-side comparison only |
   | 2–3 | Acceptable for broadcast / commercial work |
   | 3–5 | Visible; acceptable only for low-stakes outputs |
   | > 5 | Failed — visible color shift |

   Cinema-grade ColorChecker workflows typically aim for **mean ΔE2000
   < 2.0** across the 24 patches with **max ΔE2000 < 4.0**. Anything
   worse than that under controlled illuminant means a known defect
   in the pipeline (matrix wrong, white balance wrong, tone curve
   leaking in, gamut compression artifacts).

### What this test cannot tell you

- Whether the *creative grade* matches LR (see option a).
- Whether the *highlight rolloff* matches your favorite cinema look —
  ΔE on chart patches is mid-tone-weighted, not extreme-luminance-
  weighted. Add a separate gradient-target test for highlight/shadow
  behavior if it matters.
- Whether *temporal consistency* is preserved across frames — that
  needs a deflicker-residual test on a full sequence.

## Reference catalog

First-class sources only. Each entry: author / standard body, year if
relevant, URL or document number.

### Standards — color encoding

- **ITU-R BT.709** — HDTV / Rec.709 color encoding. <https://www.itu.int/rec/R-REC-BT.709>
- **ITU-R BT.2020** — UHDTV / Rec.2020 color encoding. <https://www.itu.int/rec/R-REC-BT.2020>
- **ITU-R BT.2100** — HDR encoding (PQ + HLG). <https://www.itu.int/rec/R-REC-BT.2100>
- **SMPTE ST 2065-1** — Academy Color Encoding Specification (ACES2065-1, the AP0 / linear-EXR archival space). <https://pub.smpte.org/doc/st2065-1/>
- **SMPTE ST 2065-4** — ACES Image Container File Layout (the OpenEXR subset ACES requires). <https://pub.smpte.org/doc/st2065-4/>
- **SMPTE ST 2084** — PQ EOTF for HDR (Dolby Vision / HDR10 base). <https://pub.smpte.org/doc/st2084/>
- **ISO 22028-1 / 22028-2 / 22028-3** — Extended colour encodings for digital image storage. Foundational for scene-referred vs output-referred semantics. <https://www.iso.org/standard/68761.html>
- **CIE 015:2018** — Colorimetry, 4th edition. The reference for CIE XYZ, illuminants, observer functions. <https://cie.co.at/publications/colorimetry-4th-edition>
- **CIE 159:2004** — Recommendation on the ΔE2000 formula. <https://cie.co.at/publications/colour-difference-formula-based-cie-1976-l-u-v-colour-space>

### Standards — cinema delivery

- **DCI DCSS** — Digital Cinema System Specification (DCDM, DCP). <https://www.dcimovies.com/specification/>
- **SMPTE ST 428-1** — D-Cinema distribution master image characteristics (X′Y′Z′ 12-bit). <https://pub.smpte.org/doc/st428-1/>
- **SMPTE RP 431-2** — D-Cinema reference projector + environment. <https://pub.smpte.org/doc/rp431-2/>
- **SMPTE ST 268** — DPX file format (cinema-native intermediate; relevant if a future preset emits DPX). <https://pub.smpte.org/doc/st268-1/>

### ACES — Academy Color Encoding System

- **ACES Central** — entry point for all ACES technical documents. <https://acescentral.com/>
- **ACES TB-2014-002** — Academy IIF (the foundational ACES tech bulletin). <https://acescentral.com/knowledge-base-2/technical-bulletins/>
- **Academy CTL repository** — the reference Color Transform Language implementations of IDTs, the RRT, and ODTs. *This is the source of truth for what ACES says is correct.* <https://github.com/ampas/aces-dev>
- **ACES IDT list** — published IDTs per camera; check whether the source camera has one before claiming ACES compliance. <https://github.com/ampas/aces-dev/tree/master/transforms/ctl/idt>
- **ACES test images** — Academy-supplied golden images for IDT/RRT/ODT validation. <https://github.com/ampas/aces-dev/tree/master/images>
- **ctlrender** — reference renderer for CTL transforms; ground-truth executor of the AMPAS CTL code. <https://github.com/ampas/CTL>

### Color management

- **OpenColorIO v2 specification** — the de-facto color management library for film/VFX pipelines. <https://opencolorio.readthedocs.io/>
- **OCIO Configs repository** — official ACES Studio and CG configs. <https://github.com/AcademySoftwareFoundation/OpenColorIO-Configs>
- **ociochecklut / ocioconvert** — OCIO's CLI tools for round-tripping known input through a config and verifying the output. <https://opencolorio.readthedocs.io/en/latest/guides/contributing/architecture.html#command-line-tools>
- **DNG specification, Adobe** — DCP profile format, used by `dcraw`, RawTherapee, ART, and others as the camera color-matrix source. <https://helpx.adobe.com/camera-raw/digital-negative.html>

### Reference targets

- **X-Rite ColorChecker Classic** — 24-patch standard with published spectral and CIE Lab values under D50. <https://www.xrite.com/categories/calibration-profiling/colorchecker-classic-family>
- **X-Rite ColorChecker Digital SG** — 140-patch chart for finer characterization. <https://www.xrite.com/categories/calibration-profiling/colorchecker-digital-sg>
- **ColorChecker spectral data, BabelColor** — independent published spectral measurements + chromaticities, widely cited as cross-reference. <https://www.babelcolor.com/colorchecker-2.htm>
- **ISO 12640-3** — Standard color characterization images (SCID), for full-image-context validation beyond patch targets. <https://www.iso.org/standard/33293.html>
- **DSC Labs Cambelles / OneShot** — broadcast-cinema calibration charts (commercial, no public reference values, but standard on set). <https://dsclabs.com/>

### Tools — open source, automatable

- **colour-science (Python)** — academic-grade reference implementation of CIE colorimetry, color appearance models, ΔE formulas, color-rendition chart detection. *The single most important dependency for this kind of validation.* <https://www.colour-science.org/> · <https://github.com/colour-science/colour>
- **colour-science / colour-checker-detection** — auto-detects ColorChecker patches in an image and extracts per-patch values. <https://github.com/colour-science/colour-checker-detection>
- **rawtoaces** — Academy's tool for camera-RAW → ACES2065-1 using either spectral sensitivities or published IDTs. Useful cross-pipeline reference. <https://github.com/ampas/rawtoaces>
- **OpenImageIO `oiiotool`** — image diff, metadata inspect, color conversion via OCIO. The standard scriptable image-comparison tool in VFX pipelines. <https://openimageio.readthedocs.io/en/latest/oiiotool.html>
- **ImageMagick `compare`** — produces an absolute-difference image + per-pixel ΔE metric (cruder than `colour-science`, but in every Linux distro). <https://imagemagick.org/script/compare.php>

### Methodology — primary literature

- **Sharma, G., Wu, W., Dalal, E.N. (2005)** — *The CIEDE2000 color-difference formula: implementation notes, supplementary test data, and mathematical observations.* Color Research & Application 30(1), 21–30. The reference paper for ΔE2000. <https://hajim.rochester.edu/ece/sites/gsharma/papers/CIEDE2000CRNAFeb05.pdf>
- **Hunt, R.W.G. (2004)** — *The Reproduction of Colour, 6th edition.* The standard reference on photographic color reproduction; cite for background, not testing methodology.
- **Fairchild, M.D. (2013)** — *Color Appearance Models, 3rd edition.* Modern color appearance theory; relevant for tone-mapping comparison (sigmoid, AgX, ACES RRT).
- **Pascale, D. (2003)** — *A review of RGB color spaces… from xyY to R'G'B'.* BabelColor. Practical reference for the RGB-XYZ matrices we multiply by. <https://www.babelcolor.com/index_htm_files/A%20review%20of%20RGB%20color%20spaces.pdf>
- **Stone, M.C. (2003)** — *A Field Guide to Digital Color.* AK Peters. Widely-cited practical reference.

### Existing test infrastructure to reuse, not reinvent

- **darktable's `src/tests/`** — darktable's own regression tests; relevant because `lrt-cinema` is a thin wrapper around `darktable-cli` and shouldn't re-test what darktable already covers. <https://github.com/darktable-org/darktable/tree/master/src/tests>
- **OCIO's `tests/`** — OCIO's color-transform round-trip tests cover the linear↔gamma↔log conversions we depend on. <https://github.com/AcademySoftwareFoundation/OpenColorIO/tree/main/tests>
- **AMPAS aces-dev `images/`** — golden images for end-to-end IDT/RRT/ODT pipeline tests; running these through `darktable-cli` with the ACES preset and diffing against the published ODT output is the cleanest "end-to-end" sanity check available. <https://github.com/ampas/aces-dev/tree/master/images>

## Honest assessment

Under automated test, `lrt-cinema` can credibly claim **option (b):
colorimetrically-accurate scene-referred RAW conversion within a
documented ΔE2000 envelope on the ColorChecker target.** That claim
is bulletproof, reproducible, and runnable in CI on a checked-in
reference RAW + chart-shot dataset.

`lrt-cinema` *cannot* credibly claim under automated test:

- **"Matches Lightroom"** — option (a) requires Lightroom in the
  loop. Manual frame-pair regression is the only honest verification.
- **"Matches a colorist's grade"** — perceptual judgments by definition
  fall outside automated test. The closest proxy is option (b) plus a
  reviewed-once-per-release frame-pair check by a real DP / colorist.

The "cinema-grade color" wording in `README.md` should be tightened
to specify the test envelope ("colorimetrically accurate to within
ΔE2000 < X on ColorChecker Classic under D55") once the test exists.
Until then it's an aspiration, not a claim.

## What's blocking this test today

The ColorChecker ΔE test harness is implemented in
[`tests/test_colorimetric.py`](../tests/test_colorimetric.py) with
two legs:

1. **Self-test** (runs in CI on every commit) — synthesizes a
   perfect 24-patch chart in linear Rec.2020, round-trips it through
   the harness's Lab(D55) ↔ XYZ ↔ Rec.2020 conversion + ΔE2000 path,
   and asserts the harness machinery (patch sampling, ΔE math,
   reference loader) is wired correctly. A second self-test
   cross-checks `colour-science`'s BT.2020 matrix against a hand-rolled
   matrix from ITU-R BT.2020-2 §3.3 so a transposed / wrong matrix
   anywhere in the harness wiring is detected without needing a real
   chart shot.

2. **Real-chart threshold gate** — skipped unless a chart RAW +
   identity LRT XMP are checked into
   [`tests/fixtures/colorchecker/`](../tests/fixtures/colorchecker/)
   (see that directory's `README.md` for drop-in instructions). When
   present, it renders the chart through `lrt-cinema render --preset
   cinema-linear`, auto-detects patches via the optional
   `colour-checker-detection` dependency (`pip install -e
   '.[detect]'`), and asserts mean ΔE2000 < 2.0 and max < 4.0 against
   the 24-patch D55-adapted reference shipped at
   `tests/fixtures/colorchecker/chart_reference.json`.

The reference JSON is derived from `colour-science`'s
`CCS_COLOURCHECKERS['ColorChecker24 - After November 2014']` (the
post-2014 BabelColor revision), CAT02-adapted from the published D50
values to a D55 working illuminant; full provenance lives in the
JSON's `_provenance` block. We cannot legally ship sample RAWs for
cameras we don't own — the user supplies the chart shot.

What this harness cannot do until the calibration items in
[SCOPE.md](../SCOPE.md) ship (temperature multipliers, tone curve emit
path, bundled `.style` files) is **pass the real-chart leg**. Today's
pipeline drops 9 of 12 develop ops between parse and emit and uses
neutral white-balance multipliers regardless of source kelvin. The
self-test passes today; the real-chart gate is the fast-failing CI
signal that documents how far the calibration work in [v0.3](V03_PLAN.md)
Track A needs to go — which is precisely its value pre-calibration.

## Methodology — comparing two renders of the same scene

The "ColorChecker ΔE2000" methodology above is for **absolute colorimetric
correctness against a known reference target**. Different question from
**"does our render match LRT's preview?"** — that's a two-render comparison
on arbitrary scene content with no ground truth target.

There is no recognized single-number metric for "do these two renders of
the same scene look the same." Mean L\*a\*b\* is **NOT** the right yardstick —
it collapses all spatial information into one scalar, is dragged by
outliers, and conflates uniform shifts with localized defects (e.g. mean
hides a highlight rolloff difference as long as shadows compensate).

The recognized methodology stack for two-render comparison:

| Technique | What it answers | Limitation |
|---|---|---|
| **Per-pixel ΔE2000 distribution** (percentiles + bucket histogram) | What fraction of pixels are off, by how much | Doesn't say WHERE |
| **Spatial ΔE heatmap** | Where in the image the divergence concentrates | Visual only |
| **Affine-fit decomposition** (per-channel gain + offset that minimizes residual ΔE) | "Is the gap a single grading transform, or structural?" | Linear model only |
| **Per-channel L\*a\*b\* percentile distribution** (P5/P50/P95 per channel) | Tonal/chromatic localization (shadows vs highlights, warm vs cool cast) | Aggregates spatial info |
| **Vectorscope + waveform plots** | Industry-standard for colorist visual review | Visual only |
| **SSIM / DSSIM** | Perceptual structural similarity (gradient/edge preservation) | Not Lab-aware |
| **Colorist visual review on calibrated monitor** | Final-deliverable grade-fitness | Subjective, not automatable |

The first four are implemented in [`tools/diagnose_vs_lrt_preview.py`](../tools/diagnose_vs_lrt_preview.py)
and are the project's primary colorimetric-divergence diagnostic for
the "vs LRT preview" question. Run:

```sh
python3 tools/diagnose_vs_lrt_preview.py path/to/ours.tif path/to/preview.jpg [output_dir]
```

### Affine-fit interpretation

The decomposition tells you what *class* of fix would close the gap:

- **Post-fit ΔE < 3.0**: gap is **broadcast-acceptable after a single grade**. A simple per-channel gain+offset transforms our render to within "acceptable broadcast cinema" of the target. Means the divergence is essentially a one-knob color correction the user (or a future per-camera baseline) can apply.
- **Post-fit ≪ Pre-fit but ≥ 3.0**: gap is **mostly a grading transform with a small structural residual**. Per-channel grade closes most of it; the residual is non-linear (tone curve shape, HueSatMap, etc.).
- **Post-fit ≈ Pre-fit**: gap is **structural**. No simple grade closes it. Different camera matrix, non-linear tone curve, hue rotation.

If the per-channel gain ratios diverge by >5% (e.g. green-gain ≠ R-gain), that's a white-balance or camera-matrix divergence flag — not just an exposure offset.

### Validation axes — never conflate them

| Axis | Question | Ground truth | Expected |
|---|---|---|---|
| **Implementation correctness** | Faithful to our own defined maths? | independent reimpl of our matrices/curves, hardcoded — NOT via colour-science (`tests/test_color_oracle.py`) | **~0** (rounding floor). The bug-finder; the only axis that validates a new render-math op with certitude. |
| **Absolute colorimetric accuracy** | How close to CIE truth? | CIE XYZ/Lab from spectra (ISO 17321-1) | **nonzero floor** — real sensors violate the Luther condition, so the DCP matrix is a least-squares fit. Report the floor; never call it bug magnitude. |
| **Appearance vs LRT preview** | Match what the colorist saw? | LRT `.lrtpreview` JPEG (`tools/diagnose_vs_lrt_preview.py`) | **nonzero floor** — LR's closed-source PV5 look + 8-bit JPEG. |

Absolute-accuracy and preview ΔE must be taken at a **colorimetric tap**
(post-ForwardMatrix linear ProPhoto/XYZ, *before* HSM / ExposureRamp / LookTable
/ ProfileToneCurve). Comparing the *rendered* image to Lab measures the DCP tone
curve, not pipeline error.

### Empirical finding 2026-05-30 (v0.8 head) — current state; SUPERSEDES the 2026-05-23 finding below

Head pipeline (in-process Python DNG). Gym DSC_4053 + rose, Adobe-converted DNGs
vs `dng_validate`; v0.8 `lrtimelapse` sRGB default vs LRT 7.5.3 preview.

**vs `dng_validate` (Adobe's own DNG reference renderer — the north-star):**

| Scene | mean ΔE | P50 (all) | P95 | max | <1 ΔE |
|---|---:|---:|---:|---:|---:|
| Gym (Camera Standard) | **0.789** | 0.198 | 4.19 | 10.55 | 76.8% |
| Rose (Adobe Standard) | **0.844** | 0.803 | 1.70 | 7.72 | 69.6% |

Decomposition (gym): **flat non-edge pixels match Adobe EXACTLY — median ΔE
0.000 over 94% of pixels.** The colour maths (matrix + tone curve + HueSatMap +
LookTable) bit-match the open-spec reference. The mean is dragged by **edge
pixels (1.62 ΔE, ~6% — a real demosaic-algorithm difference, libraw LINEAR vs
Adobe; crop misalignment ruled out by an offset sweep, center-crop is optimal)**
and a small flat-region colour tail (~5% of flat px above 4 ΔE). There is **no
theoretical floor on the colour maths**; the real-scene mean floor is the
demosaic choice (the DNG spec does not mandate a demosaic algorithm). A
synthetic flat-patch chart (the planned Axis-2/3 harness) measures the pure
colour-math agreement without edge/demosaic noise and can be driven toward ~0.

**vs LRT preview (v0.8 sRGB default, gym, identity develop):**
- RAW: mean 2.92, P50 2.55, P95 7.36.
- AFFINE residual (per-channel gain 0.83/0.84/0.84 ≈ a benign ~0.3 EV global
  offset, not a defect): mean ~2.18, P50 1.71.
- This ~2 is the **closed-source PV5 look + 8-bit JPEG floor**, not pipeline
  error: **preview gap ≈ 0.79 (our-vs-Adobe-DNG — the part we own and can tune)
  + ~2 (PV5 + JPEG — the reference's own look).**

**North-star for the open-tool transition (Adobe purge):** keep `dng_validate`
as a test-only oracle and tune open-DCP renders back toward the **proven 0.789**
(median 0.000 — the maths already bit-match). The preview is the human-facing
end-to-end check, reported as raw + affine-residual with the PV5 floor named so
it is never mistaken for our error or chased past what is closed-source.

### Synthetic-chart harness (Axes 2 & 3) — landed 2026-05-30

The deterministic flat-patch harness foreshadowed above now exists. A
**colorimetric tap** (`pipeline.apply_adobe_pipeline(..., stop_after_stage=3|4)`
→ XYZ(D50) / linear ProPhoto(D50), post-ForwardMatrix, pre-HSM) is the
measurement point for both nonzero-floor axes.

- **Axis 2 — absolute accuracy** (`tests/test_colorimetric.py`, supersedes the
  old Rec.2020/Lab self-test). ISO 17321-1 (+ grey wedge) spectra → CIE truth
  (Bradford→D50) and → synthetic Nikon-D5100 camera RGB (its SSF; the D750's is
  unpublished). At the tap: a synthetic white-constrained FM gives mean ΔE2000
  **0.70–0.74** and the shipped **Adobe Standard** D5100 DCP **0.77–0.86**, both
  sitting on the **independently computed SSF Luther floor 0.81–0.84**
  (`ssf_lstsq_floor`) — accuracy = profile fit, not bug. (Camera-*Standard*
  profiles are NOT colorimetric: their ForwardMatrix is the ProPhoto→XYZ
  passthrough; the colour work is in the LookTable. Use Adobe Standard for
  absolute accuracy.)
- **Axis 3 — implementation vs `dng_validate`** (`tests/test_synthetic_dng.py`,
  D750). A flat-patch synthetic DNG (dnglab uncompressed clone + raw-strip
  byte-patch honouring BlackLevel 600 / WhiteLevel 15520) rendered both sides
  with Camera Standard. **Neutral wedge: median ΔE 0.000** across the tonal
  range (incl. bright levels up to ProPhoto 0.65) — the clean isolation of the
  bit-match, free of the demosaic-edge tail. **Chromatic flats diverge ~4–8 ΔE**,
  confirmed in wide-gamut ProPhoto (so NOT sRGB-gamut clipping). Localised to the
  **LookTable** by elimination: the per-channel ExposureRamp + ProfileToneCurve
  are exonerated because the neutral wedge — pure per-channel inputs — matches at
  every level *above* the chromatic patches' max per-channel value, leaving the
  (h,s,v)-joint LookTable as the only chromatic-affecting stage neutrals (sat=0)
  can't exercise. This IS the gym's documented "~5% flat-region colour tail",
  now surfaced cleanly (real-scene colours are too desaturated to probe these
  cells) and the concrete **drive-toward-0 target** for the open-DCP transition.

### Empirical finding 2026-05-23 — lrt-cinema vs LRT 7.5.3 preview (SUPERSEDED — darktable-era, pre-v0.6 Python pipeline; kept for history)

Test frame: DSC_4053 (neutral keyframe, EV=0 per user's LRT XMP). After Visual Preview re-render in LRT.

**Pre-fit (raw vs LRT preview):**
- ΔE2000 mean 7.11, median 5.17, P95 17.97
- 51.5% of pixels at ΔE ≥ 5 (visible defect)
- L\* gap concentrated in highlights: ΔP95 = −20.2 (ours much darker in highlights, shadows match)
- b\* gap concentrated in highlights: ΔP95 = −16.3 (ours much less yellow in highlights)

**Affine fit:**
- Best per-channel gain R/G/B = 2.077 / 2.276 / 2.017
- Best per-channel offset = small (−0.03 each)
- **Post-fit mean ΔE drops to 2.48** (broadcast-acceptable)

**Diagnosis:** The gap is mostly a **per-channel gain (≈ +1 EV) with subtle green skew** plus a small structural residual (~2.5 ΔE). Translation in cinematography terms:
- LR/LRT applies its DCP profile's BaselineExposure (~+0.5–1.0 EV bias)
- LR/LRT applies its DCP color matrix (≈ 10% green-channel gain vs dt's libraw-derived matrix)
- Highlight rolloff + warm-shaped HueSatMap account for the ~2.5 ΔE residual

**Gap-closing path:** the affine fit shows the bulk is fixable via DCP-aware processing (chip #2 — kelvin→multipliers research already has the foundational work). Without DCP, a per-camera `--exposure-bias` + manual WB tweak gets within broadcast-acceptable; full DCP processing closes the rest.

## LRT interpolation passthrough model

`lrt-cinema` is a faithful executor of LRTimelapse intent for darktable. There are two LRT workflow modes the parser must handle, and the same code path serves both:

**Mode 1 — Auto Transition NOT run (sparse keyframes only).** LRT has written XMP sidecars but only the rating-flagged keyframes carry creative values; intermediate frames have `crs:Exposure2012="0.000000"` (the LR default). Our parser picks up just the keyframes; `interpolate()` fills gaps using our own linear or Catmull-Rom math. This is the path most useful for LRT-skipping pipelines and the only path where lrt-cinema's own interpolation math actually fires.

**Mode 2 — Auto Transition run (LRT has interpolated).** Every per-frame XMP now carries an LRT-computed `crs:Exposure2012` (and any other animated property). Our parser ingests every frame whose ops are non-default — `is_kf or _has_meaningful_ops(ops)` — so the IR ends up with one `Keyframe` per source frame. `interpolate()` exact-matches each frame and returns LRT's per-frame value verbatim. Our linear / Catmull-Rom code does NOT run for these frames; we honor LRT's intent.

The single code path is `parse_sequence` + `interpolate`. The `_has_meaningful_ops` heuristic distinguishes LR/LRT default-written-but-unedited frames (skipped) from frames carrying real intent (ingested). The two LR defaults that must be excluded are `crs:Sharpness="25"` (LR's out-of-camera sharpness default) and `<crs:ToneCurvePV2012>` containing only `[0,0]` → `[255,255]` (LR's identity tone curve). Both are excluded by `_is_identity_tone_curve` and the omitted-sharpness check.

Verification: render the same frame range twice — once before Auto Transition, once after — and compare the TIFFs. Frames at keyframe positions are identical in both renders (same input value); intermediate frames may differ if our gap-fill interpolation diverges from LRT's. Identical render proves LRT-intent fidelity; divergence quantifies the gap-fill difference in EV terms.

### Empirical comparison (2026-05-22, LRT Pro 7.5.3, dt nightly 5.5.0+1375)

Test sequence: 5033-frame Nikon D750 timelapse, 6 LRT keyframes set at intervals of ~1006 frames. Two EV changes (0.0 → −0.5 → −1.0 → 0.0) over the first three keyframe pairs. 21 frames rendered around the −0.5 EV keyframe at frame 1006, both modes:

| Frame | Our linear interp | LRT's interp (Auto Transition) | ΔEV |
|---|---|---|---|
| 996 | −0.4950 | −0.494380 | +0.00062 |
| 1001 | −0.4980 | −0.497195 | +0.00081 |
| 1006 | −0.5000 | −0.500000 | 0.00000 (keyframe) |
| 1007 | −0.5005 | −0.500599 | −0.00010 |
| 1016 | −0.5050 | −0.505786 | −0.00079 |

LRT's interpolation is asymmetric around the keyframe (less negative approaching, more negative leaving), consistent with a smooth spline (Catmull-Rom or Hermite) rather than linear.

**TIFF pixel data byte-identical** across all 21 frame pairs. The maximum ~0.0008 EV divergence falls below darktable's 16-bit linear TIFF quantization at the scene's midtone levels (~0.224 mean), so the numerical difference does not propagate to visible pixel difference at this keyframe spacing.

**What this test actually proved (be precise):** two lrt-cinema code paths (our linear interp on sparse keyframes, vs passthrough of LRT's per-frame Auto-Transition values) collapse to the same TIFFs *through the same darktable*. That is internal pipeline consistency, not output fidelity in the cinema sense. The byte-identical result was overdetermined: darktable's 16-bit linear TIFF quantization eats sub-LSB differences, so the test could not have failed even if our interp diverged more strongly from LRT's.

**What this test did NOT prove:** that our rendered TIFFs match what an LRT + Adobe Camera Raw / Lightroom workflow would produce. We are entirely dependent on darktable's choices — demosaic algorithm (RCD/AMaZE vs ACR proprietary), camera color matrix application (LCMS+DCP vs ACR profile HSL tables), default modules applied, gamma encoding nuances, highlight reconstruction algorithm — for the actual pixel output. Equivalence to ACR is unverified and likely false in measurable ways.

**For a defensible "cinema-grade output" claim, see the [ColorChecker ΔE2000 methodology](#the-bulletproof-automated-test-option-b) above.** That test compares lrt-cinema's output against published colorimetric reference values, not against LRT or ACR. It is the only currently-feasible automated path to a colorimetric-correctness claim.

**Practical conclusion for the interpolation question:** for typical timelapse keyframe spacing (≥1000 frames per EV stop), `--interpolation linear` (lrt-cinema's default) and `--interpolation smooth` produce indistinguishable output through darktable, both indistinguishable from LRT's own Auto Transition output through darktable. At aggressive keyframe spacing (e.g., +3 EV across 200 frames) the smooth/linear divergence would become visible; `--interpolation smooth` is closer to LRT's spline behavior and is recommended for such cases. None of this constitutes evidence that lrt-cinema's output matches ACR's — only that our pipeline is internally consistent.

## Known environment issues (not lrt-cinema bugs)

### darktable.app cask on macOS arm64 — SQLite/ICU mismatch (5.4.1 release)

Symptom: `darktable-cli` aborts at ~250 ms with `[dt_init] ERROR: can't init develop system, aborting.` regardless of input file, with no preceding diagnostic. Reproduces on a fresh `~/.config/darktable`. Reboot does not fix it.

Cause (confirmed on darktable 5.4.1 cask, macOS arm64, 2026-05-22): the bundled `darktable-cli` binary at `/Applications/darktable.app/Contents/MacOS/darktable-cli` links to the system SQLite at `/usr/lib/libsqlite3.dylib` (which does NOT include the ICU extension), but darktable's startup SQL runs `SELECT icu_load_collation('en_US', 'english')`. The system SQLite fails with "no such function: icu_load_collation", darktable interprets that as a develop-system init failure, and aborts. The ICU dynamic libraries ARE shipped in the bundle (`libicui18n.78.dylib`, etc.) but the linked SQLite isn't ICU-enabled. This is upstream cask packaging; the cask is also flagged "deprecated, will be disabled 2026-09-01" by Homebrew due to a separate macOS Gatekeeper signing issue.

Diagnostic: `/usr/bin/log show --predicate 'process == "darktable-cli"' --last 5m` will surface the two SQLite errors that the darktable terminal output suppresses.

**Working workaround (validated 2026-05-22): darktable nightly .dmg** — version `5.5.0+1375.g9402c65275` and later fixes the SQLite/ICU init path. Download from <https://github.com/darktable-org/darktable/releases/tag/nightly>, install over the cask version. With one caveat:

The nightly .dmg has a plugin-path packaging bug — `darktable-cli` looks for plugins at `Contents/lib/darktable/plugins/` but the bundle ships them at `Contents/Resources/lib/darktable/plugins/`. Symptom: dt fails with "cannot find disk storage module" on first run. One-line symlink fix:
```
rmdir /Applications/darktable.app/Contents/lib/darktable
ln -s ../Resources/lib/darktable /Applications/darktable.app/Contents/lib/darktable
```

Other workarounds, in order of effort:
- **MacPorts install** — `sudo port install darktable` builds from source and links to MacPorts' SQLite (ICU-enabled).
- **Build from source** — clone `darktable-org/darktable`, build against Homebrew's SQLite (`brew install sqlite` then point CMAKE_PREFIX_PATH at `/opt/homebrew/opt/sqlite`).
- **Different RAW renderer** — out of project scope today, but `rawpy` (libraw bindings) + a separate color-management step would be a structural alternative if darktable proves persistently unusable on macOS.

### darktable XMP schema version compatibility

darktable 5.5 nightly accepts `darktable:xmp_version="1"` only; values >=6 (which dt 4.x / 5.4.x wrote) are rejected with "XMP schema version N in '...' not supported". The version field appears to track a backward-incompatible bump rather than a monotonic schema generation — newer dt does NOT accept higher numbers, it requires the new lower number. The emitter is pinned to `DT_XMP_VERSION = "1"` for dt 5.5+ compatibility. If you need to render through an older dt (4.x / 5.0–5.4), bump it back to `"6"` or whatever that release expects; sweep with `lrt-cinema render --dry-run` until dt-cli stops rejecting the sidecar.

The misleading error message — `error: can't open XMP file` instead of `XMP schema version N not supported` — is logged only when dt is run with `--core -d imageio`. Without that flag, the version rejection is invisible.

## Related project documents

- [SCOPE.md](../SCOPE.md) — per-feature implementation status; the
  test described here gates the "cinema-grade" wording.
- [src/lrt_cinema/presets/CALIBRATION.md](../src/lrt_cinema/presets/CALIBRATION.md) —
  why the bundled `.style` files are placeholders and what the
  calibration pass must do.
