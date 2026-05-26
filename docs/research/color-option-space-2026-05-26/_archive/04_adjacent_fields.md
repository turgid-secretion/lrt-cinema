# Cross-Domain Calibration Research: Findings for lrt-cinema

*Research agent output, 2026-05-26. Verbatim except for formatting normalization.*

## TL;DR — Three techniques most likely to unlock the ΔE ceiling

1. **Macenko-style stain normalization (OD-space SVD)** is the most directly transferable. It solves the lrt-cinema problem — non-linear cross-device color mapping — in log space rather than linear RGB, which kills exactly the kind of residual that a 3×3 in linear space can't catch.
2. **Lattice regression with thin-plate regularization** (Garcia & Gupta) is the proven way to fit a 3D LUT from sparse chart data without ringing. This is the LUT-fitting math you want if you go past a matrix.
3. **HSV-delta tables (Adobe DCP HueSatMap)** are already in your stack — and the published evidence is consistent: matrix-only ≈ 65–70% of the gap, and the residual genuinely lives in non-linear hue/sat space. The structure is identical to what Adobe shipped.

---

## 1. Histopathology stain normalization — most direct analog

The setup mirrors yours: same physical scene (slide / your reference) imaged by different sensors (scanners / your cameras) producing systematically different RGB. Three canonical methods:

**Reinhard (2001)** — convert to Lαβ, match mean & std per channel, convert back. Three scalars per channel. Cheap, global, breaks on regions with skewed color distributions. SSIM ≈ 0.968 in the multicenter benchmark.

**Macenko (2009)** — the key insight for you. Convert RGB → optical density: `OD = -log10(I/I0)`. In OD space, stain mixtures become linear. SVD on the OD pixel cloud finds the two stain vectors (the "primaries" of that slide). Normalize concentrations to a reference, reconstruct. This is a 3×3 matrix that operates in **log space**, not linear. Why this matters for lrt-cinema: the residual non-linearity you're seeing in linear-RGB matrix fits often disappears once you fit in OD/log space because dye-mixing physics (Beer-Lambert) is linear there, and sensor spectral mismatches behave more linearly in log space too.

**Vahadane (2016)** — same OD trick but uses Sparse NMF instead of SVD to constrain stain vectors to be structure-preserving and non-negative. Best SSIM (0.989) in benchmarks.

**StainGAN / StainNet (2021)** — CycleGAN learns the mapping unpaired (no chart needed), then distills it into a 1×1 conv net 40× faster than the GAN. Reference-free.

**Insight for lrt-cinema**: Try fitting the 3×3 in OD space (`-log(linear_RGB + ε)`) rather than linear RGB. The 23% capture figure may improve substantially without changing the model order. The Beer-Lambert linearity argument applies to any subtractive/absorptive process; for additive light it's weaker, but the spectral-mismatch component of camera differences does behave more linearly in log space.

Refs: [Macenko PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC6778842/) · [Benchmark](https://arxiv.org/abs/2506.19106) · [StainNet](https://www.frontiersin.org/journals/medicine/articles/10.3389/fmed.2021.746307/full) · [Vahadane](https://albarqouni.github.io/publication/vahadane-2016-structure/)

---

## 2. Astronomy — the "color term" decomposition

Standard astronomical photometric calibration: `m_standard = m_instrumental + ZP + k·X + T·C`, where `ZP` is zero point, `k·X` is airmass extinction, and **`T·C` is the color term** (T is the transformation coefficient, C is the color index like B-V). T captures the residual after the linear scale-and-zero, exactly because the sensor's bandpass doesn't match the standard's bandpass. Achieves ~5 mmag (~0.5%) after correction; Stetson achieves 10–40 mmag raw, 5 mmag corrected.

**Insight**: Astronomers explicitly decompose into (linear scale) + (zero point) + (extinction) + (color term that depends on object color index). The lrt-cinema equivalent: a piecewise correction where the per-channel gain depends on (R-G), (G-B), (R-B) — not just absolute RGB. This is essentially what HueSatMap encodes, but the astronomy framing as "the residual is proportional to chromaticity, not RGB position" is a sharp design rule.

Refs: [USask CCD transforms](https://researchers.usask.ca/gordon-sarty/documents/astronomy/transf.pdf) · [Stetson recalibration](https://arxiv.org/abs/2603.08376) · [AAVSO airmass+color](https://www.aavso.org/extrinsic-color-and-differential-airmass-corrections)

---

## 3. Display & ICC profile science — patch counts and smoothing

Direct, empirical numbers from Argyll / ICC literature:
- **Matrix/shaper profile**: 200–600 patches sufficient. Smooth by construction.
- **LUT profile**: 1000–2000 patches for high quality.
- ColorChecker 24 is undersampled for LUT fitting; that's a hard constraint, not a tuning issue.
- **Lattice regression** (Garcia & Gupta, used in Argyll-class tools) fits the entire LUT jointly under three criteria: low interpolation error, smoothness between adjacent cells, steady functional trend. Thin-plate regularizer is standard. This is the math for fitting a non-overfit LUT from few patches.

**Insight**: If you go LUT, do not naive-fit. Use lattice regression with thin-plate or bilateral smoothness. A 9³ or 17³ LUT fit by lattice regression from 24 patches will outperform a 33³ LUT fit by nearest-neighbor scattering. Cube size: 17³ is the cinema sweet spot; 33³ is overkill for chart-based fits and prone to artifacts in gamut corners.

Tetrahedral interpolation is non-negotiable: same quality as trilinear with ~20–25% smaller LUT, and no banding on smooth gradients. Trilinear's color-fringe artifacts on subtle skin transitions are a known failure mode you'd inherit.

Refs: [Argyll matrix vs LUT](https://argyllcms.freelists.narkive.com/kjq0hinI/camera-profiling-using) · [Lattice regression](https://www.researchgate.net/publication/286154470_Building_accurate_and_smooth_ICC_profiles_by_lattice_regression) · [Tetrahedral analysis](https://coloristfactory.com/2022/08/05/luts-in-davinci-resolve-get-way-better-results-tetrahedral-interpolation-vs-trilinear-interpolation/)

---

## 4. Remote sensing — cross-sensor harmonization at scale

NASA's Harmonized Landsat-Sentinel (HLS) is **exactly** the cross-sensor problem at planetary scale. Pipeline:
1. Atmospheric correction (Sen2Cor: at-sensor radiance → surface reflectance)
2. BRDF correction (Ross-Li 3-kernel model: isotropic + volumetric + geometric)
3. **Bandpass adjustment**: Sentinel-2 spectrally remapped to Landsat-8 OLI as reference using a per-band linear transform derived from spectra of typical surfaces
4. Common grid

Published result: ≤4.2% reflectance difference in red/NIR/SWIR; blue/green slightly worse (atmospheric scattering hardest in short wavelengths).

**Vicarious calibration** uses pseudo-invariant sites (Saharan sand dunes — Libya 4 is canonical) as zero-touch reference targets that are temporally stable; extended PICS (EPICS) generalizes this to land-cover classes.

**Insights**:
- They pick **one sensor as the reference** and remap the other to it via a per-band linear transform learned from physics-modeled scene spectra. Direct analog: pick lrt-cinema's most-used camera as reference, fit per-band linear from the other cameras to it. They don't try to fit "true color" — they fit "look like the reference camera." This is operationally what you want.
- BRDF-style correction (per-position kernel) has no direct analog unless your camera angles vary, but it shows that breaking the problem into separable kernels (each kernel models one physical effect) is the field's escape from monolithic LUT fitting.
- Atmospheric correction is a fully physical model, not a regression — the contrast with stain normalization (purely statistical) suggests **hybrid pipelines** beat either extreme. They use physics where physics is known (Rayleigh, Mie) and statistics for the residual.

Refs: [HLS](https://www.sciencedirect.com/science/article/pii/S0034425718304139) · [Sen2Cor](https://sentiwiki.copernicus.eu/web/s2-processing) · [PICS/EPICS](https://www.mdpi.com/2072-4292/13/8/1545) · [Ross-Li](https://www.mdpi.com/2072-4292/10/3/437)

---

## 5. Audio room correction — anchor frequencies & multipoint averaging

Dirac Live uses **mixed-phase filters** (IIR for efficiency + FIR for the non-causal part of impulse correction). The non-causal piece is what an IIR fundamentally cannot model — direct parallel to: a 3×3 matrix fundamentally cannot model non-linear hue rotations. The fix is the same: add a structurally-different second stage.

**Multipoint measurement**: 9 mic positions, averaged, with weighting toward primary seat. Then correct toward a **target curve** that is deliberately not flat (gentle 2–6 dB roll-off toward 20 kHz to match psychoacoustic expectation).

**Insights**:
- *"Don't correct to flat"*: the target is psychoacoustic, not physical. Photo analog: don't correct to colorimetric truth, correct to **a perceptually pleasing target** that your reference camera happens to already produce. This is why "match the reference camera" beats "match the chart" for cross-camera consistency in practice.
- The IIR+FIR split = matrix + 3D-LUT residual stage. Audio settled here for the same reason photography is converging on it.

Refs: [Dirac filter design PDF](https://www.dirac.com/wp-content/uploads/2021/09/On-equalization-filters.pdf) · [Dirac target curves](https://www.dirac.com/resources/target-curve)

---

## 6. Color constancy (CV) — algorithms without a chart

Hierarchy of chart-less methods:
- Gray World (assume scene avg = gray)
- Max-RGB / White Patch (brightest pixel = white)
- Shades of Gray (L^p norm, p≈6 best empirically — Finlayson & Trezzi)
- Gray Edge (apply same logic to image derivatives)
- **FFCC (Barron, 2017)**: reframe illuminant estimation as 2D localization on a chromaticity torus, solve in Fourier space. 13–20% lower error than SOTA, 250–3000× faster. Produces a full posterior over illuminants, enabling temporal smoothing — relevant for timelapse.

**Insight**: FFCC's framing is the one to remember. White balance ≈ finding a (u, v) chromaticity offset. They make it a convolution problem in Fourier space and get learned, temporally-smooth, near-realtime estimation. For lrt-cinema's timelapse, temporal smoothing of per-frame WB is a known win.

Refs: [FFCC paper](https://openaccess.thecvf.com/content_cvpr_2017/papers/Barron_Fast_Fourier_Color_CVPR_2017_paper.pdf) · [Patch-wise Bright Pixels](https://arxiv.org/pdf/1911.07177) · [Shades of Gray](https://library.imaging.org/admin/apis/public/api/ist/website/downloadArticle/cic/12/1/art00008)

---

## 7. DICOM / pathology color management

DICOM Supplement 100 mandates **ICC Input Device Profiles embedded in every color WSI** — pathology's answer to cross-vendor color matching is "force everyone to ship an ICC profile and trust the consumer's CMS to render it." Per-camera profiles, no enforced common space. The profile is mandatory; the rendering pipeline is the user's problem.

This is the *opposite* of HLS's "pick a reference sensor" — it's a hands-off federation model.

Refs: [DICOM Sup 100 PDF](https://www.dicomstandard.org/News-dir/ftsup/docs/sups/sup100.pdf) · [Color mgmt in path](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4334042/)

---

## Cross-cutting patterns

**Hybrid pipelines dominate**. Every mature field uses a layered approach:
- Astronomy: linear (ZP+gain) + airmass + color term
- Display: 1D LUT (linearize) + 3×3 matrix + 1D LUT (output transfer) — and the LUT-only profile is reserved for cases where this fails
- Audio: IIR (efficient) + FIR (non-causal residual)
- Adobe DCP: ForwardMatrix + HueSatMap + LookTable
- Remote sensing: atmospheric model + BRDF kernel + bandpass linear transform

**Nobody fits one big 3D LUT directly from sparse chart data.** When LUT is used, it's either (a) a small residual on top of an analytical model, or (b) fit by lattice regression with smoothness regularization, or (c) learned from millions of pixels (stainGAN, FFCC).

**Minimum data for "good enough"** varies wildly:
- Matrix/shaper: 24-patch ColorChecker is sufficient
- LUT (lattice-regression-fit): 200–600 patches
- LUT (naive fit): 1000–2000 patches
- Neural (StainGAN, FFCC): millions of unpaired pixels, no chart

**Calibration without a physical reference exists** and is mature: gray-world, FFCC, stainGAN, PICS sites, "match the reference instrument" style.

---

## Recommendations for lrt-cinema (ranked by expected payoff to break the 12 ΔE2000 ceiling)

1. **Fit the 3×3 in optical-density space** (Macenko trick) before adding model complexity. Cheapest experiment, mechanism is well-justified (spectral-mismatch residual is more linear in log space), and could close a chunk of the 77% remaining gap without enlarging the model. Highest information-per-effort.
2. **Add an HSV-delta table as the residual stage** (you're already on this with HueSatMap). Adobe shipped this exact two-stage structure for the same reason the audio world shipped IIR+FIR. The "12 ΔE2000 ceiling" is essentially what this stage was designed to attack.
3. **Reframe the target as "match the reference camera," not "match the chart"** (HLS strategy + Dirac target curve insight). Cross-camera consistency is what timelapses need; colorimetric truth is a different goal. Fit each non-reference camera's profile to map its 24 chart patches onto the reference camera's 24 chart patches in the reference's already-cooked color space.
4. If you go to LUT, use **lattice regression with thin-plate smoothness**, 17³ cube, tetrahedral interpolation. Skip 33³ — the chart undersamples it.
5. For per-frame WB drift in timelapse, **FFCC with temporal smoothing** is the SOTA pattern; the published implementation is open source.
6. Consider a **StainGAN-style unpaired CycleGAN** as a long shot: it would let you train on your existing footage cross-camera without any chart, learning the per-camera signature from data. This is the "no physical reference" escape hatch if chart-based fitting plateaus.

The single highest-leverage experiment is #1 (refit the existing 3×3 in OD space). Hours of work, no architectural change, mechanistically sound. The next-highest is #3 (target = reference camera, not colorimetric truth), which is also nearly free and changes what "12 ΔE2000" even means.
