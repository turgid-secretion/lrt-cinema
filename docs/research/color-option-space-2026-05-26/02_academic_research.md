# Camera Color Characterization: Academic + Standards Survey

*Research agent output, 2026-05-26. Verbatim except for formatting normalization.*

## The 3×3 ceiling, explained: Luther/Maxwell-Ives failure

The ~12 ΔE2000 residual you see after a least-squares 3×3 channelmixer is structural, not algorithmic. A camera RGB-to-XYZ map is **exactly invertible** only when the camera's three spectral sensitivity functions (SSFs) are a linear combination of the CIE 1931 color matching functions — the **Luther condition** (Maxwell-Ives criterion). Production Bayer cameras with on-sensor dye filters and IR-cut glass never satisfy it; their SSFs are too sharp and too overlapped relative to the CMFs, so for any chosen matrix there exist spectra (metamers under the camera but not the observer, and vice versa) that the matrix *cannot* place correctly. The matrix can minimize average error but cannot eliminate it. Discussion: [Strolls With My Dog – Perfect Color Filter Array](https://www.strollswithmydog.com/perfect-color-filter-array/); Finlayson et al., *Designing Color Filters that Make Cameras More Colorimetric*, [arXiv:2003.12645](https://arxiv.org/abs/2003.12645); Finlayson et al., *The Luther Condition for All*, 2022.

This means the gap closes on **only two axes**: either (a) make the fit non-linear so it can place metamers individually, or (b) bypass chart fitting entirely by integrating the camera's measured SSF against the target primaries (the rawtoaces / P-2013-001 path). The Adobe DCP HueSatMap+LookTable is option (a); rawtoaces is option (b).

## Matrix-only best-achievable (the floor your engine hit)

- **Vazquez-Corral, Connah, Bertalmío 2014** ("Perceptual Color Characterization of Cameras", *Sensors* 14(12)) — on **37 cameras** (28 from Jiang's camspec + 9 from Image Engineering), tested against 203,490 reflectance×illuminant pairs, the best 3×3 matrix achieved **1.83 ΔE2000 mean** with spherical sampling vs. 1.83 with least squares (spherical 0.4% better). They prove "characterization error is guaranteed zero only when sensor responses exactly match XYZ CMFs — a condition rarely met in practice." [PMC4299059](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4299059/)
- **Anders Torger (dcamprof)** reports comparable ~1.7 ΔE on training patches, 2–3 on unseen, peaks ~14 — consistent with Vazquez-Corral's theoretical floor when measured on real test data. [torger.se/anders/dcamprof.html](https://torger.se/anders/dcamprof.html)

Both figures are **on the ColorChecker reflectance space**. The lrt-cinema ~12 ΔE on D750 is plausible if you're including saturated cinema test patches well outside that gamut (skin under tungsten, deep red/blue costuming, fluorescent gels) — those are exactly the spectra where camera–observer metamerism diverges most.

## Non-linear extensions (option a)

**Root-polynomial regression — Finlayson, Mackiewicz, Hurlbert 2015** (*IEEE TIP* 24(5), [10.1109/TIP.2015.2405336](https://dl.acm.org/doi/10.1109/TIP.2015.2405336); preprint [eprints.ncl.ac.uk](https://eprints.ncl.ac.uk/file_store/production/211896/56A5026C-F3B9-4CB9-9A51-10F304877B45.pdf)) — takes each k-order term in a polynomial expansion and applies its kth root, producing a transform that is **exposure-invariant** (a×R → a×corrected, so the fit doesn't break when scene brightness changes). Standard polynomial PCC is *not* exposure-invariant. Order-2 root-poly has 6 terms; order-3 has 13. Reported to substantially improve over 3×3 without overfitting in cross-validation. **This is the single highest-EV addition to a cinema engine** — it's a closed-form regression, ships exposure invariance for free, and trivially round-trips through DCP (encode the higher-order terms as a 2.5D LookTable approximation if needed).

**Kucuk, Finlayson, Mantiuk, Ashraf 2022** ("An Exposure Invariant Neural Network for Colour Correction", CIC30) — most decisive recent finding: **root-polynomial outperforms small CNNs** for camera color correction on test data, and **NN models fail catastrophically when exposure changes** at test time. They eventually patch this with a chromaticity-correcting net + separate brightness predictor — but the bottom line is *don't replace root-poly with a CNN*. [PMC10607821](https://pmc.ncbi.nlm.nih.gov/articles/PMC10607821/), [library.imaging.org/lim/articles/3/1/17](https://library.imaging.org/lim/articles/3/1/17)

**Hong, Luo, Rhodes 2001** — foundational polynomial PCC for digital cameras, Color Research & Application 26(1):76–84, [10.1002/1520-6378(200102)26:1<76::AID-COL8>3.0.CO;2-3](https://onlinelibrary.wiley.com/doi/10.1002/1520-6378(200102)26:1%3C76::AID-COL8%3E3.0.CO;2-3). Showed polynomial term selection matters more than degree; orders >3 overfit on a 24-patch ColorChecker.

**Karaimer & Brown CVPR 2018** ("Improving Color Reproduction Accuracy on Cameras", [karaimer.github.io/camera-color/](https://karaimer.github.io/camera-color/)) — **30% improvement on DSLRs, 59% on phones** vs. single-illuminant 3×3, by interpolating per-illuminant matrices in chromaticity space. This is essentially what DNG's ColorMatrix1/ColorMatrix2 already does, but rigorously evaluated. Confirms multi-illuminant interpolation is necessary for >2.5 ΔE accuracy across mixed lighting.

**3D LUT / 2.5D LookTable** — Adobe DCP, dcamprof, and the Hung 2001 / Kasson 1993 (SPIE 1909:127) tetrahedral interpolation tradition. The Adobe LookTable is fundamentally a 2.5D LUT indexed in HSV chromaticity (90×16×16 — *exactly what your fit is trying to recover*). dcamprof's spline-smoothed 2.5D LUT is the open-source reference implementation. Empirically catches the residual but introduces gradient artifacts ("broken gradients") if the LUT is over-fitted to a small training set; this is the fundamental tradeoff documented at length on [torger.se](https://torger.se/anders/photography/camera-profiling.html). The TPS3D / Smooth-TPS3D thin-plate-spline approach ([arXiv:2409.05159](https://arxiv.org/pdf/2409.05159)) is a refined replacement worth knowing about.

## Spectral path (option b) — closing the gap structurally

If you can recover the D750's SSF, you can compute the IDT in **closed form** by integrating SSF × illuminant against ACES AP0 (or against XYZ D50 for a DCP ForwardMatrix). No chart, no training, no overfit. The AMPAS procedure is **P-2013-001** ("Recommended Procedures for the Creation and Use of Digital Camera System Input Device Transforms"), implemented in [AcademySoftwareFoundation/rawtoaces](https://github.com/AcademySoftwareFoundation/rawtoaces) and in [colour-science Python](https://colour.readthedocs.io/en/latest/generated/colour.matrix_idt.html) (`colour.matrix_idt`, `optimisation_factory_rawtoaces_v1`). Both projects use Ceres for the constrained non-linear least squares.

**Critical finding for lrt-cinema**: The Nikon D750 SSF is available in [butcherg/ssf-data](https://github.com/butcherg/ssf-data) under `Nikon/D750/`. Not in Jiang's camspec (28 cameras) but Butcher measured it. License: **CC BY-NC-SA 4.0** — fine for engine development, requires share-alike on derivative profile data.

**SSF databases**:

- **Jiang, Liu, Gu, Süsstrunk 2013** "What is the space of spectral sensitivity functions for digital color cameras?" — IEEE WACV ([ieeexplore](https://ieeexplore.ieee.org/abstract/document/6475015/), data [Zenodo 3245883](https://zenodo.org/records/3245883)). 28 cameras; the camspec PCA basis is 2-dimensional convex hull. This is the basis any SSF-from-chart estimator uses as prior.
- **Darrodi, Finlayson, Goodman, Mackiewicz 2015** "Reference data set for camera spectral sensitivity estimation" — JOSA A 32(3):381 — NPL-grade ground-truth for Nikon D5100 + Sigma SD1 Merrill with per-wavelength uncertainty bands. The reference for evaluating any estimator.
- **butcherg/ssf-data** — DIY spectroscope, ~15 cameras incl. several Nikons, Arri, Blackmagic, Phase One, RED-ish coverage.
- **Image Engineering camSPECS** — commercial hardware, not free data.

**SSF recovery from a single image** — for cameras with no published SSF:

- **Jiang et al. 2013** themselves give the chart-based recovery method using the camspec PCA prior.
- **Practical Camera Sensor Spectral Response and Uncertainty Estimation** (2020) [PMC8321145](https://pmc.ncbi.nlm.nih.gov/articles/PMC8321145/) extends with uncertainty estimation.
- **Makabe, Santo, Okura, Brown, Matsushita ICCV 2025** "Spectral Sensitivity Estimation with an Uncalibrated Diffraction Grating" ([arXiv:2508.00330](https://arxiv.org/abs/2508.00330), code [GitHub](https://github.com/lilika-makabe/camera-sensitivity-estimation-with-grating)) — current SOTA, requires only a $5 off-the-shelf diffraction grating, beats chart-based methods. Closed-form solution.
- **vkdt's mkssf** estimates SSF *from the camera's DNG ColorMatrix* (already noted by the RAW agent) — useful when you have nothing but the DCP.

**Wueller 2017** "Sensitivity analysis applied to ISO recommended camera color calibration methods" ([library.imaging.org/ei/articles/29/15/art00006](https://library.imaging.org/ei/articles/29/15/art00006)) — direct ISO-A (spectral) vs. ISO-B (chart) comparison: spectral wins when illuminant is known, chart-based wins when training-illuminant matches scene illuminant exactly. Cinema is in the "illuminant unknown / mixed" regime, so spectral path is preferred.

## Standards bodies

- **ISO 17321-1:2012** — defines two methods: **Method A (spectral)** using monochromator stimuli, **Method B (target)** using a calibrated chart. The colorimetric matrix is the deliverable in both. No accuracy threshold mandated. [Preview](https://webstore.ansi.org/preview-pages/ISO/preview_ISO+17321-1-2012.pdf)
- **CIE TC 8-15** (Colour Imaging for Digital Preservation) — emphasizes capture as a "use- and institution-neutral starting point"; recommends Method A spectral characterization explicitly, accepts that 3×3 is the standard mapping but acknowledges its limits.
- **AMPAS P-2013-001** — Academy procedure for IDT creation, the canonical SSF-integration spec used by rawtoaces. Cinema-grade authority.
- **SMPTE ST 2065-1 (ACES)** — defines AP0 primaries; the math underneath every IDT.

**No standard mandates ΔE2000 < N for cinema characterization.** Industry rule-of-thumb (from ImageEngineering, DXO, and the cinematography community) is ΔE2000 < 3.0 for "professionally acceptable", < 2.0 for "broadcast/cinema reference", < 1.0 for "imperceptible". Adobe targets ~2 on their reference patches.

## Patent / license landscape

- **Adobe DNG specification** — royalty-free patent license under conditions: prominently attribute, no patent counter-suit against Adobe ([adobe-dng-spec-patent on ScanCode LicenseDB](https://scancode-licensedb.aboutcode.org/adobe-dng-spec-patent.html)). Reading + writing DCP profiles is *covered*.
- **ACES (P-2013-001, ST 2065-1)** — open standard, no royalty.
- **rawtoaces** — Apache 2.0 (AcademySoftwareFoundation project).
- **colour-science Python** — BSD-3.
- **dcamprof** — GPL-3.
- **camspec / butcherg SSF data** — CC BY-NC-SA 4.0; **non-commercial** clause matters if lrt-cinema ships profiles built from it.
- **Hung 1993 tetrahedral interpolation patents** (US5581376) — long expired (>20 years).
- No known patent blockers for root-polynomial PCC.

## ML status (territory 6)

- **Kucuk 2022** (above) — *root-poly beats small NNs on test data; NNs are exposure-fragile*. This is the load-bearing finding.
- **Karaimer & Brown CVPR 2018** — multi-illuminant matrix interpolation is the production winner.
- **ISPDiffuser 2025** ([arXiv:2503.19283](https://arxiv.org/html/2503.19283v1)) — diffusion-model RAW-to-sRGB. Aesthetic mapping, not characterization; doesn't solve metamerism.
- **CCMNet 2025** ([arXiv:2504.07959](https://arxiv.org/html/2504.07959v1)) — cross-camera color constancy via calibrated CCM matrices; *uses* matrices, doesn't replace them.
- **AISP / mv-lab** ([github.com/mv-lab/AISP](https://github.com/mv-lab/AISP)) — NTIRE/AIM challenge implementations, the reference repo for learned-ISP work.
- **No production-shipped ML camera-characterization tool** that beats DCP. The market is occupied by dcamprof, X-Rite ColorChecker Camera Calibration, and rawtoaces.

## Spectral sharpening (Finlayson & Drew 1994, JOSA A 11(5):1553)

[Spectral sharpening](https://www.semanticscholar.org/paper/Spectral-sharpening:-sensor-transformations-for-Finlayson-Drew/8f376ea67936dda2d084fc740d61d1cade4be091) is a sensor basis transform that makes channels less overlapped, making *chromatic adaptation* (von Kries / Bradford) work better diagonally. **It does not close the colorimetric characterization gap.** It's useful at the white-balance stage (where dt's CAT16 module sits) but is orthogonal to the matrix/LUT residual you're trying to close. CAT16 and the Bradford transform already encode the practical benefits.

## What an academic-best lrt-cinema implementation looks like vs. what was built

| Stage | Current lrt-cinema | Academic best | Gap |
|---|---|---|---|
| White balance | dt's CAT16 (presumed) | CAT16 / Bradford-sharpened | None |
| Linear correction | LS 3×3 channelmixer v3 | Root-polynomial PCC (order 2–3), or closed-form SSF×illuminant IDT | **Highest EV** — root-poly closes a substantial fraction of the non-linear gap as a drop-in replacement; SSF path closes more if D750 SSF (in butcherg/ssf-data) is exploited |
| Non-linear residual catcher | None | 2.5D HSV LUT (dcamprof spline-smoothed), or thin-plate-spline | Necessary for the last few ΔE; what DCP's LookTable expresses |
| Tone curve | dt's filmic/sigmoid | Same | None |

**Recommended order of operations**:

1. Implement **root-polynomial PCC** (Finlayson 2015) as the linear-correction primitive — exposure-invariant, closed-form, no overfit risk at order ≤3. Likely closes a large fraction of the 12 ΔE residual immediately.
2. Pull **D750 SSF from butcherg/ssf-data** (CC BY-NC-SA — note for licensing if you ship profiles) and add a Tier-4 "compute IDT/ForwardMatrix from SSF × illuminant" path via colour-science's `matrix_idt`. This is the rawtoaces approach and should beat Adobe's own ColorMatrix1/2 fit (Adobe doesn't have measured D750 SSF either).
3. For the irreducible non-linear remainder, fit a **2.5D HSV LookTable** with dcamprof-style spline smoothing — DCP-round-trippable, what darktable explicitly *refuses* to do but cinema needs.
4. Validate with **CIEDE2000 on a held-out test set** that includes saturated patches (SG140, IT8.7, not just CC24) to expose metameric corners.

This stack matches the AMPAS P-2013-001 + Adobe DNG references and uses no patent-encumbered math.

## Additional sources

- [Comparing ACES IDTs for Canon 5D Mark III (Vrhel/Pankanin et al.)](https://community.acescentral.com/uploads/default/original/2X/0/044f1c81300387c2487aa58e31bd672a347f9b9e.pdf)
- [DNG 1.4 Specification PDF](https://www.kronometric.org/phot/processing/DNG/dng_spec_1.4.0.0.pdf)
- [colour-science aces_it.py source](https://github.com/colour-science/colour/blob/develop/colour/characterisation/aces_it.py)
- [Spectral Sensitivity Estimation with Diffraction Grating (Makabe 2025)](https://lilika-makabe.github.io/camera-sensitivity-estimation-with-grating-site/)
