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

The ColorChecker ΔE test is mechanical to implement. Adding it to
the project means:

1. A checked-in reference RAW + ColorChecker shot (one per supported
   camera model). The user supplies these; we cannot legally ship
   sample RAWs for cameras we don't own.
2. A `tests/test_colorimetric.py` that runs the full pipeline against
   each reference RAW + identity XMP and computes ΔE2000 per patch.
3. A dependency on `colour-science` (BSD-3) — license-compatible with
   our Apache-2.0.
4. A documented threshold in `pyproject.toml` test config (e.g. mean
   ΔE < 2.0, max ΔE < 4.0). CI fails if regression exceeds threshold.

What it cannot do until the calibration items in [SCOPE.md](../SCOPE.md)
ship (temperature multipliers, tone curve emit path, bundled
`.style` files) is **pass**. Today's pipeline drops 9 of 12 develop
ops between parse and emit and uses neutral white-balance multipliers
regardless of source kelvin. The test would be a fast-failing CI
gate that documents exactly how far the calibration work needs to
go — which is precisely its value pre-calibration.

## Related project documents

- [SCOPE.md](../SCOPE.md) — per-feature implementation status; the
  test described here gates the "cinema-grade" wording.
- [src/lrt_cinema/presets/CALIBRATION.md](../src/lrt_cinema/presets/CALIBRATION.md) —
  why the bundled `.style` files are placeholders and what the
  calibration pass must do.
