# Color-correction background

Math primitives, industry context, license landscape, and primary-source
references for the candidates in [option-space.md](option-space.md) and
the decision in [decision.md](decision.md). Read on demand; not a
prerequisite for the action documents.

## 1. Math primitives

The candidates draw on a small vocabulary of math operations. The
primitives that come up across candidates:

### 1.1 3×3 linear matrix (ColorMatrix / ForwardMatrix)

Sensor RGB → XYZ(D50) via a 3×3 linear transform. The DCP encodes two
per-illuminant matrices (ColorMatrix1 at illuminant 1, ColorMatrix2 at
illuminant 2) and lrt-cinema mired-blends between them per scene Kelvin.

The published structural floor for the best 3×3 against the ColorChecker
patches: **1.83 ΔE2000 mean** (Vazquez-Corral, Connah, Bertalmío 2014,
*Sensors* 14(12), 37 cameras × 203,490 reflectance×illuminant pairs).
Real-world test data (saturated patches outside CC24, blue costuming
under tungsten) climbs to 2.16 mean / 14+ peak (Torger / dcamprof).

The floor exists because production Bayer sensors with dye filters never
satisfy the Luther/Maxwell-Ives condition. No 3×3, however well fit,
escapes the floor; chasing it further requires either non-linear
extensions or bypassing chart fitting entirely via SSF integration.

### 1.2 Root-polynomial regression (Finlayson 2015)

A 3×N matrix on root terms of (R, G, B): order 2 = 6 terms (R, G, B,
√(RG), √(GB), √(RB)); order 3 = 13 terms. Each term has its kth root
taken so the fit is **exposure-invariant** (a·R → a·corrected;
homogeneous of degree 1) — critical for timelapse where scene brightness
ramps cross orders of magnitude.

`colour-science` provides `polynomial_expansion_Finlayson2015` and
`optimisation_factory_Oklab_15` (15-term variant). The AMPAS IDT
Calculator ships this as the "Oklab_15" factory. Reported to substantially
improve over 3×3 on chart-only fits without overfit at order ≤ 3.

Most decisive recent finding (Kucuk, Finlayson, Mantiuk, Ashraf 2022,
CIC30): **root-polynomial outperforms small CNNs** on camera color
correction, and NN models fail catastrophically when exposure changes at
test time. The conclusion is unambiguous: root-poly is the load-bearing
non-linear primitive, not learned models.

Status in lrt-cinema: not implemented at v0.6. Available as a Tier-2
enrichment for the opt-in `--engine adobe-camera` path under candidate A.

### 1.3 SSF-integrated IDT (AMPAS P-2013-001)

Closed-form integration of camera SSF × illuminant against target
primaries (ACES AP0 for an IDT; XYZ D50 for a DCP ForwardMatrix). When
the camera's SSF is available, the matrix is computed analytically — no
chart, no training, no overfit.

`colour-science` provides `colour.matrix_idt`,
`optimisation_factory_rawtoaces_v1`. `AcademySoftwareFoundation/rawtoaces`
is the reference C++ implementation. Both use Ceres for the constrained
non-linear least squares.

SSF databases:

- **butcherg/ssf-data** (CC BY-NC-SA 4.0): DIY-spectroscope-measured SSFs
  for ~15 cameras including Nikon D750 (the project's reference camera).
  Non-commercial license: users may compute their own profile locally,
  but lrt-cinema cannot ship the derivative profile data.
- **Jiang, Liu, Gu, Süsstrunk 2013** ([camspec database](https://zenodo.org/records/3245883)):
  28 cameras; the PCA basis any SSF-from-chart estimator uses as prior.
- **Darrodi, Finlayson, Goodman, Mackiewicz 2015** ([JOSA A 32(3):381](https://opg.optica.org/josaa/abstract.cfm?uri=josaa-32-3-381)):
  NPL-grade ground truth for Nikon D5100 + Sigma SD1 Merrill with
  per-wavelength uncertainty bands. The reference for evaluating any
  estimator.

Status in lrt-cinema: not implemented. Tier-1 enrichment for cameras
where SSF data exists under candidate A.

### 1.4 HSV residual catcher (Adobe HueSatMap + LookTable)

Adobe's per-cell hue/saturation/value transform via the hexcone HSV
variant with sRGB-encoded V axis. Each cell maps an input (h, s, v) to an
output (h+Δh, s·Δs, v·Δv). Two stages cascade in the Adobe pipeline:

1. **HueSatMap** (90×30×1 standard dimension; per-illuminant): captures
   the camera's saturated-chroma divergence from colorimetric truth.
2. **LookTable** (36×8×16 standard dimension; single-illuminant): the
   per-camera aesthetic look character on top of HSM.

`src/lrt_cinema/lut3d_baker.py` ships the trilinear-sample math with
RT-compatible hexcone HSV, sRGB OETF/EOTF on V, BaselineExposureOffset
positioning, and dt `lut3d`-module integration. Validated against
RawTherapee's `dcp.cc::hsdApply` reference.

The measured contribution of each stage (across the 40-camera evaluation
panel, full Adobe Standard catalog reference; see
[measurements.md §2](measurements.md#m2-a-empirical-e2000-ceiling)):

| Configuration | Mean ΔE2000 | P95 |
|---|---:|---:|
| Identity (no shared transform) | 6.28 | 14.29 |
| Median HueSatMap only | 4.83 | 12.03 |
| Median LookTable only | 6.05 | 12.50 |
| Median HSM + LookTable cascade | 3.60 | 11.46 |
| Direct 33³ uncompressed cube | 4.15 | 9.24 |

The cascade does real work; either stage alone underperforms.

### 1.5 CAT16 chromatic adaptation

darktable's `color calibration` module's default. CAT16 is a published
improvement on CAT02 (which has a published failure mode where extreme
blue chromaticities can produce negative tristimulus values). It's the
right primitive for the chromatic adaptation stage; no candidate
challenges it.

The Bradford transform (older but still common) is approximately
equivalent for the chromaticity-region cinema content occupies; CAT16
wins at the bluer end.

## 2. Industry context

### 2.1 Cinema-broadcast color science

The cinema pipeline solved the camera-response problem by **standardizing
the target space, not the source**. Every camera vendor publishes a fixed
working space (ARRI AWG4/LogC4, Sony S-Gamut3.Cine/S-Log3, RED
REDWideGamutRGB/Log3G10, Blackmagic Gen 5 Wide Gamut). The IDT (Input
Device Transform) maps camera-native → vendor working space → ACES2065-1
→ ACEScg (for grading) → ODT → display.

Critically: **the working-space transforms are almost always 1D curve +
3×3 matrix.** The 3D LUT mythology around cinema is about *output*
(creative looks, display rendering), not *input*. The ARRI LogC4 spec
publishes the AWG4→ACES matrix to 18 decimals; Sony S-Gamut3.Cine→XYZ is
a published 3×3; RED IPP2 is the same shape.

This is why root-polynomial PCC (Finlayson 2015) is the standard
"beyond 3×3" upgrade in ACES tooling — it preserves exposure invariance
and remains analytically tractable, unlike free-form 3D LUTs.

ΔE2000 envelopes treated as acceptable in cinema:

| Tier | Threshold |
|---|---|
| Mastering / DI | ≤ 1 |
| Cinema reference | 1 < ΔE ≤ 2 |
| Broadcast | 2 < ΔE ≤ 3 |
| Consumer / IMAG | 3 < ΔE ≤ 5 |

Vendor IDTs (ARRI / RED / Sony / BMD official) reach 1–2 mean ΔE on
their reference patches via Method A (spectral). The AMPAS IDT
Calculator's chart-based Method B reaches 2–6 ΔE depending on the loss
space (Lab vs Oklab) and whether root-polynomial is enabled. Steve
Yedlin's published cinema-camera matching work (`ethan-ou/camera-match`)
achieves < 1.5 mean ΔE via tetrahedral interpolation + RBF + root-poly.

### 2.2 Photography RAW software

darktable is the architectural outlier. The dt project's official
position (issue [#4165 "Support .dcp"](https://github.com/darktable-org/darktable/issues/4165),
closed *not planned*): the *colorimetric* part of color rendering belongs
in a 3×3 matrix in `colorin`, *chromatic adaptation* belongs in `color
calibration` (CAT16), and the *look* (tone curve, hue/sat compression)
belongs in `filmic rgb` / `sigmoid` / `agx`. Adobe's DCP architecture
bundles all three into one file; dt's pipeline unbundles them. The
"matrix is enough" position is held by dt (Aurélien Pierre) and Argyll
(Elle Stone); the rest of the industry (Adobe, Capture One, RawTherapee,
ART) ships hybrid matrix + LookTable.

The other tools' formats and approaches:

| Tool | Profile format | Stage 2 |
|---|---|---|
| RawTherapee | DCP (ColorMatrix + ForwardMatrix + HueSatMap + LookTable + ToneCurve) | Spline-smoothed 2.5D HSV LUT via dcamprof |
| ART (Another RawTherapee) | DCP + CTL scripts + CLF / OCIO LUTs | Arbitrary script-based looks |
| Capture One | Proprietary per-camera ICC with embedded LUT + "hue twists" | Subjective look baked in (their explicit posture) |
| DxO PhotoLab | Per-camera-per-lens proprietary binary | Multi-dimensional model conditioned on ISO/aperture/focal/distance |
| dcamprof (Torger) | DCP or ICC; matrix + 2.5D HSM; optional 3D LookTable | Spline-smoothed; tone operator preserves hue under contrast |
| ArgyllCMS | ICC shaper-matrix or cLUT | Recommends matrix-only for general photography |
| vkdt | CLUT (constrained to spectral locus) | PCA + Gaussian mixture model recovers SSF; synthesizes target patches at any illuminant |
| rawtoaces | ACES IDT (3×3 to AP0) | Closed-form SSF integration |

### 2.3 Academic + standards context

- **ISO 17321-1:2012** defines Method A (spectral; monochromator stimuli)
  and Method B (target; calibrated chart). Both deliver a colorimetric
  matrix; no accuracy threshold is mandated by the standard.
- **CIE TC 8-15** (Colour Imaging for Digital Preservation) recommends
  Method A spectral characterization explicitly.
- **AMPAS P-2013-001** is the cinema canonical IDT-creation spec
  (spectral path); `rawtoaces` is its open-source implementation.
- **SMPTE ST 2065-1 (ACES)** defines AP0 primaries.
- **Adobe DNG specification** ships under a royalty-free patent license
  (read + write of DCP profiles is covered). Per `KELVIN_MULTIPLIERS_RESEARCH.md`,
  the project's `.npz` extracted-profile format stores profile *data*,
  not Adobe's `.dcp` format, sidestepping format-redistribution
  interpretation issues.

### 2.4 Adjacent-field findings

Five techniques from non-imaging-color domains surface lrt-cinema-relevant
patterns. Captured here for reference value; none directly displace the
v0.6 candidates but each informs the option-space evaluation:

- **Histopathology stain normalization (Macenko 2009)** — fit the 3×3 in
  optical-density space (`-log(linear_RGB + ε)`) rather than linear RGB.
  Spectral mismatches behave more linearly in log space for absorptive
  processes; the technique is potentially applicable as a cheaper variant
  of the linear stage. Not pursued at v0.6 (A′'s ceiling is set by HSV
  residual variance, not linear-stage residual).
- **Astronomy color-term decomposition** — the residual after a linear
  scale-and-zero is proportional to chromaticity (e.g., B-V color index),
  not to absolute RGB. This is structurally what HueSatMap encodes.
- **Display ICC profiling (Garcia & Gupta lattice regression)** — the
  math for fitting a LUT from sparse chart data without ringing. 17³
  cube is the cinema sweet spot; 33³ is overkill for chart-based fits
  and prone to gamut-corner artifacts. (Confirms the measurement finding
  that 65³ doesn't outperform 33³ on A′.)
- **Remote sensing cross-sensor harmonization (HLS pipeline)** — pick one
  sensor as the reference and remap the other via a per-band linear
  transform from physics-modeled scene spectra. Doesn't try to fit "true
  color"; fits "look like the reference camera." Direct analog: A′
  targets Adobe Standard as the reference, not colorimetric truth.
- **Audio room correction (Dirac)** — IIR (efficient) + FIR (non-causal
  residual). The two-stage structure is exactly Adobe's
  matrix-plus-HueSatMap. Audio settled here for the same structural
  reason photography is converging on it: a 3×3 alone fundamentally
  cannot model the residual non-linearity.

## 3. License + patent landscape

| Component | License | Status for lrt-cinema |
|---|---|---|
| Adobe DNG Specification | Royalty-free patent license under DNG spec (read + write of DCP profiles is covered, with attribution + no-counter-suit conditions) | Apache-2.0 compatible; `.npz` extracted data ships under Apache-2.0 per the project's own format definition |
| ACES (P-2013-001, ST 2065-1) | Open standard, no royalty | Compatible |
| `rawtoaces` | Apache 2.0 (AcademySoftwareFoundation) | Compatible |
| `colour-science` | BSD-3 | Compatible (already a dep) |
| `dcamprof` | GPL-3 | Subprocess shell-out only (already the pattern with `darktable-cli`); no link-time GPL contamination |
| `dcpTool` | Freeware | Optional install-time shell-out |
| `butcherg/ssf-data` | CC BY-NC-SA 4.0 | **Non-commercial.** User computes locally; lrt-cinema cannot redistribute derivative profile data |
| Adobe DNG SDK | Adobe DNG SDK License (royalty-free derivative works; distribution permitted with commercial-distribution indemnification) | Optional at install/build time; runtime engine plausibly OK if shipped as alternate (not primary) pipeline |
| Hung 1993 tetrahedral interpolation patents (US5581376) | Expired (> 20 years) | No blocker |
| Root-polynomial PCC | No known patent | No blocker |

## 4. References

### Standards + procedures
- [ACES Input Transforms documentation](https://docs.acescentral.com/system-components/input-transforms/) — AMPAS P-2013-001 reference.
- [AMPAS IDT Calculator (GitHub)](https://github.com/ampas/idt-calculator) — open-source Method B implementation.
- [ARRI LogC4 Specification](https://www.arri.com/resource/blob/278790/bea879ac0d041a925bed27a096ab3ec2/2022-05-arri-logc4-specification-data.pdf) — vendor IDT shape primary source.
- [Adobe DNG Specification 1.4](https://helpx.adobe.com/camera-raw/digital-negative.html).
- [ISO 17321-1:2012 preview](https://webstore.ansi.org/preview-pages/ISO/preview_ISO+17321-1-2012.pdf).

### Academic primary sources
- Finlayson, Mackiewicz, Hurlbert 2015 — [Color Correction Using Root-Polynomial Regression (IEEE TIP)](https://eprints.ncl.ac.uk/file_store/production/211896/56A5026C-F3B9-4CB9-9A51-10F304877B45.pdf).
- Vazquez-Corral, Connah, Bertalmío 2014 — [Perceptual Color Characterization of Cameras (Sensors 14(12))](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4299059/).
- Kucuk, Finlayson, Mantiuk, Ashraf 2022 — [Exposure Invariant Neural Network for Colour Correction (CIC30)](https://pmc.ncbi.nlm.nih.gov/articles/PMC10607821/).
- Karaimer & Brown 2018 — [Improving Color Reproduction Accuracy on Cameras (CVPR)](https://karaimer.github.io/camera-color/).
- Jiang, Liu, Gu, Süsstrunk 2013 — [What is the space of spectral sensitivity functions for digital color cameras? (WACV)](https://ieeexplore.ieee.org/abstract/document/6475015/) + [camspec data](https://zenodo.org/records/3245883).
- Darrodi, Finlayson, Goodman, Mackiewicz 2015 — [Reference data set for camera spectral sensitivity estimation (JOSA A 32(3):381)](https://opg.optica.org/josaa/abstract.cfm?uri=josaa-32-3-381).
- Hong, Luo, Rhodes 2001 — Polynomial PCC for digital cameras (Color Research & Application 26(1):76–84).

### Tool documentation
- [DCamProf — torger.se](https://torger.se/anders/dcamprof.html) — including [News Archive](https://torger.se/anders/dcamprof-old-news.html) with worked dE numbers.
- [darktable input color profile docs](https://docs.darktable.org/usermanual/development/en/module-reference/processing-modules/input-color-profile/).
- [darktable issue #4165 "Support .dcp" (closed not planned)](https://github.com/darktable-org/darktable/issues/4165).
- [colour-science aces_it.py source](https://github.com/colour-science/colour/blob/develop/colour/characterisation/aces_it.py).
- [vkdt utilities to create input device transforms](https://jo.dreggn.org/vkdt/src/tools/clut/readme.html).
- [butcherg/ssf-data on GitHub](https://github.com/butcherg/ssf-data) — CC BY-NC-SA 4.0.
- [rawtoaces wiki](https://github.com/AcademySoftwareFoundation/rawtoaces/wiki/usage).
- [Hue Twists — dcpTool](https://dcptool.sourceforge.net/Hue%20Twists.html).
- [Mansencal — The ColorChecker Considered Mostly Harmless](https://www.colour-science.org/posts/the-colorchecker-considered-mostly-harmless/).

### Adjacent-field references
- Macenko 2009 stain normalization — [PMC6778842](https://pmc.ncbi.nlm.nih.gov/articles/PMC6778842/).
- HLS (Harmonized Landsat-Sentinel) — [pipeline overview](https://www.sciencedirect.com/science/article/pii/S0034425718304139).
- Garcia & Gupta lattice regression — [Building accurate and smooth ICC profiles by lattice regression](https://www.researchgate.net/publication/286154470_Building_accurate_and_smooth_ICC_profiles_by_lattice_regression).
- Dirac filter design — [On equalization filters (Dirac whitepaper)](https://www.dirac.com/wp-content/uploads/2021/09/On-equalization-filters.pdf).
- FFCC (Barron 2017) — [Fast Fourier Color Constancy (CVPR)](https://openaccess.thecvf.com/content_cvpr_2017/papers/Barron_Fast_Fourier_Color_CVPR_2017_paper.pdf).
