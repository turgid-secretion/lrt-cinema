# Cinema & Broadcast Color Management — Per-Camera Correction Survey

*Research agent output, 2026-05-26. Verbatim except for formatting normalization.*

## 1. The cinema pipeline at a glance

Cinema solved this problem by **standardizing the *target* space, not the *source***. Every camera vendor publishes a fixed working space (ARRI AWG4/LogC4, Sony S-Gamut3.Cine/S-Log3, RED REDWideGamutRGB/Log3G10, Blackmagic Gen 5 Wide Gamut). The job of color correction is then well-defined: **camera-native → vendor working space → ACES2065-1 → ACEScg (for grading) → ODT → display**. The "per-camera correction" lives in the first two steps — the **IDT** (Input Device Transform).

Crucially: **the working-space transforms are almost always a 1D curve + 3×3 matrix.** The 3D-LUT mythology around cinema is mostly about *output* (creative looks, display rendering), not *input*.

| Stage | Math | Source |
|---|---|---|
| Sensor RGB → Log (in camera) | Per-channel 1D (log curve) | Encoded by manufacturer; e.g. ARRI LogC4 eq. (2) — log + linear toe |
| Log → Wide Gamut linear | 1D LUT inverse | ARRI eq. (3) decoding function |
| Wide Gamut → ACES AP0 | **3×3 matrix** with CAT02 chromatic adaptation | ARRI LogC4 spec §4.3.2 — `M_ACES` is published to 18 decimals |
| ACEScg ↔ ACES2065-1 | 3×3 matrix | AP1 ↔ AP0 |
| Grade space → display | 3D LUT (RRT + ODT) | ACES Reference Rendering Transform |

The ARRI LogC4 PDF I pulled is unambiguous: **after the log curve, AWG4-to-ACES is a single 3×3 matrix**. No 3D LUT, no polynomial. Same shape at Sony (S-Gamut3.Cine→XYZ is a published 3×3) and RED IPP2. That's because once you're in a vendor-defined working space, the colorist *creates* the look — the IDT just delivers a well-conditioned starting tristimulus.

## 2. How are IDTs actually created?

This is where the problem lives. AMPAS published **P-2013-001 "Recommended Procedures for the Creation and Use of Digital Camera System Input Device Transforms"** ([acescentral.com](https://docs.acescentral.com/system-components/input-transforms/)), which standardizes two approaches:

**Method A — Spectral (preferred, vendor-grade):**
1. Measure the camera's **spectral sensitivities** (a monochromator scans 380–780 nm)
2. Synthesize the camera's response to AMPAS's curated training reflectance database (~190 swatches of natural reflectances, cherry-picked for gamut coverage) under D60/CIE A illuminants
3. Solve a 3×3 matrix that minimizes ΔE in CIE Lab between synthetic camera RGB and ACES AP0 ground truth
4. Constraint: **whitepoint-preserving** (D60 stays neutral)

This is what ARRI, Sony, RED, BMD do internally. Result: ΔE76 ~1–2 average, max ~4–6 on saturated test colors. Vendor IDTs are published as CTL files and shipped in OCIO configs.

**Method B — Chart-based ("prosumer" path):**
The [AMPAS IDT Calculator](https://github.com/ampas/idt-calculator) accepts a stack of ColorChecker exposures + flatfield + gray patch and fits the matrix using [colour-science](https://github.com/colour-science/colour/blob/develop/colour/characterisation/aces_it.py). Three optimisation factories ship:

| Factory | Math | Loss space | Typical ΔE |
|---|---|---|---|
| `rawtoaces_v1` | 3×3 linear matrix | CIE Lab | 3–6 ΔE76 |
| `Jzazbz` | 3×3 linear matrix | Jzazbz | 3–5 ΔE_J |
| `Oklab_15` | **Root-polynomial** (15-term, Finlayson 2015) | Oklab | 1.5–3 ΔE_Ok |

All preserve whitepoint. **Root-polynomial (Finlayson, Mackiewicz, Hurlbert 2015)** is the key advance — it's a 3×N matrix that operates on nonlinear root terms like √(RG), ∛(RGB), R^(2/3), preserving exposure invariance (homogeneous of degree 1) while capturing nonlinearity that a 3×3 cannot. Sources: [IEEE TIP 2015](https://eprints.ncl.ac.uk/file_store/production/211896/56A5026C-F3B9-4CB9-9A51-10F304877B45.pdf); [colour-science discussion](https://github.com/colour-science/colour/discussions/896). **This is the most actionable upgrade path** — a 3×9 or 3×15 root-polynomial typically halves residual ΔE versus 3×3 on a chart-only fit.

## 3. Tool-by-tool comparison

| Tool | Math | Required input | ΔE class | Notes |
|---|---|---|---|---|
| **ACES IDT (vendor)** | 1D + 3×3 | Spectral sensitivity + training reflectances | <2 avg | Gold standard. ALEXA/RED/Sony/BMD have official IDTs in [aces-aswf/aces-core](https://github.com/aces-aswf/aces-core) |
| **AMPAS IDT Calculator** | 1D + 3×3 *or* root-poly (Oklab_15) | ColorChecker stack | 2–6 | Open-source. Wraps colour-science |
| **DaVinci Resolve Color Match** | 3D LUT (proprietary fit) | One chart frame | ~2–4 | "Minimizes distance between captured and reference patches"; works inside DaVinci Wide Gamut Intermediate |
| **DaVinci Color Space Transform** | Pure 1D + 3×3 (matrix lookup) | None — uses built-in matrices | Camera-spec | Vendor working space → DWG, no per-body fit |
| **FilmConvert CineMatch** | Sensor-data profile (lab-measured per body) | None at runtime | Visually neutral | FilmConvert profiles ~200 cameras in their lab. Aligns sensors then re-applies target color science |
| **Colourlab Ai 3** | Neural net, ACES-native, 16-stop | Chart or reference shot | Sub-2 (claimed) | First neural color engine in ACES space |
| **Pomfort LiveGrade** | CDL + 3D LUT applied live | Vendor IDT (CLF) + manual grade | N/A — on-set | Now supports custom CLF as IDT for ACES |
| **FilmLight Baselight Truelight** | Truelight Colour Spaces (math, not LUTs) | Vendor TCS or custom | <2 | TCS is internal, GPU-native, function-based — escapes 3D-LUT precision loss |
| **Steve Yedlin / [camera-match](https://github.com/ethan-ou/camera-match)** | Tetrahedral interpolation, RBF, root-poly | Chart pair | <1.5 reported | Yedlin's "Display Prep" demo — cinema's most-cited match work |
| **3DLUT Creator** | 3D LUT via grid bending | Reference + source chart | 2–5 | Manual fitting UI |

## 4. ΔE envelopes treated as "acceptable"

| Standard | Threshold | Source |
|---|---|---|
| Highest-end (DI, mastering) | ΔE ≤ 1 | [3nh, ViewSonic ColorPro guides](https://www.viewsonic.com/ap/colorpro/articles/detail/deltae2color-accuracy_811) |
| Cinema-acceptable | 1 < ΔE ≤ 2 | "Visible only under careful comparison" |
| Broadcast-acceptable | 2 < ΔE ≤ 3 | General broadcast |
| Consumer/IMAG | 3 < ΔE ≤ 5 | "Slight visible difference" |

**The 12 ΔE2000 on a 3×3 fit is roughly 4× the broadcast tolerance.** Achievable via the Oklab_15 root-polynomial route alone (no spectral data needed). With spectral data, sub-2 is realistic.

## 5. Multi-cam workflow — "make N cameras match"

This is the standard ACES use case. The cinema procedure:
1. Every camera gets its IDT applied **at ingest** — all clips land in AP0 sharing a common neutral.
2. A **Look Modification Transform (LMT)** is layered for creative grade.
3. The single ODT renders to the chosen display.

Practical reinforcement (DIT methods, per Pomfort and the IMAG/church-production guides): chart shots every setup, grey-card white balance to a common Kelvin, vector scope check, and CDL trims to align WB drift. **The IDT is treated as fixed metadata, not a per-shot creative choice.** This is the architecture lrt-cinema should adopt.

## 6. What cinema does that photography pipelines miss

1. **Treat the camera transform as 1D-then-3×3**, not as a single 3D LUT. The log curve is per-channel and *exposure-invariant*; the chromaticity correction is a small linear matrix. Conflating them into a 3D LUT loses precision and introduces float quantization at the cube vertices.
2. **Enforce whitepoint preservation as a hard constraint** in the matrix solve — D60 stays neutral. Unconstrained least-squares 3×3 fits drift the neutral axis and visibly degrade skin/sky.
3. **Use root-polynomial expansion (Finlayson 2015) when a 3×3 is insufficient.** It's the standard upgrade in ACES tooling and preserves exposure invariance. It's a 3×9 or 3×15 matrix on √, ∛ terms of (R,G,B). This is the highest-ROI fix: implementing this should close most of the 12 → 2–4 ΔE gap **without** any spectral data.
4. **Pick the loss space deliberately.** Cinema fits in **Oklab or Jzazbz**, not Lab — these are uniform across the wider gamut and HDR range cameras now capture. Lab is biased toward sRGB-ish chroma.
5. **Don't refit per shot.** Cinema bakes IDT once per body+ISO+WB combo and treats subsequent corrections as creative CDL trims. A timelapse with stable settings should compute its IDT once at calibration time, not per-frame.
6. **Targets matter more than algorithms.** AMPAS uses a curated 190-reflectance training set, not just the 24-patch ColorChecker. The colour-science library exposes these. The [ColorChecker-considered-mostly-harmless](https://www.colour-science.org/posts/the-colorchecker-considered-mostly-harmless/) critique by Thomas Mansencal (colour-science maintainer) is essential reading.
7. **Spectral characterization is the only path below ΔE 2.** Without spectral data, **fundamental metameric ambiguity** caps chart-based fits at ~2 ΔE for any algorithm. If you need cinema-grade match, you need a spectral measurement of the body or a published vendor sensitivity curve.

## 7. Recommendation for lrt-cinema

The Tier 2 DCP-distillation fit is on the right architecture. To close more of the residual:
- **Add a root-polynomial (Finlayson 15-term) emission alongside 3×3**, optimised in Oklab with whitepoint preservation. `colour.characterisation.optimisation_factory_Oklab_15` is a ready reference.
- **Use the AMPAS 190-reflectance training set**, not a 24-patch chart, when fitting against the synthetic DCP target. This is one import from colour-datasets.
- **Architect like ACES**: per-body calibration = 1D curve + matrix/poly, stored once per `(body, ISO, WB)` triple. Resolve, ARRI, Pomfort all share this shape.
- **Long term**: harvest published spectral sensitivities (RIT camspec database, dcraw_emu measurements for Nikon bodies) for sub-2 ΔE without requiring a monochromator.

## Sources

- [ACES Input Transforms documentation](https://docs.acescentral.com/system-components/input-transforms/)
- [AMPAS IDT Calculator (GitHub)](https://github.com/ampas/idt-calculator)
- [ARRI LogC4 Specification (PDF)](https://www.arri.com/resource/blob/278790/bea879ac0d041a925bed27a096ab3ec2/2022-05-arri-logc4-specification-data.pdf) — primary source for matrix-only AWG4→ACES math
- [ARRI REVEAL Color Science explained (frame.io)](https://blog.frame.io/2024/06/10/arri-reveal-color-science-explained/)
- [RED IPP2 Overview](https://support.red.com/hc/en-us/articles/115004913827-IPP2-Overview)
- [Sony S-Gamut3.Cine technical summary (PDF)](https://pro.sony/s3/cms-static-content/uploadfile/06/1237494271406.pdf)
- [colour-science aces_it.py — IDT optimisation factories](https://github.com/colour-science/colour/blob/develop/colour/characterisation/aces_it.py)
- [Finlayson, Mackiewicz, Hurlbert — Color Correction Using Root-Polynomial Regression (IEEE TIP 2015, PDF)](https://eprints.ncl.ac.uk/file_store/production/211896/56A5026C-F3B9-4CB9-9A51-10F304877B45.pdf)
- [Thomas Mansencal — The ColorChecker Considered Mostly Harmless](https://www.colour-science.org/posts/the-colorchecker-considered-mostly-harmless/)
- [FilmLight Truelight Colour Spaces](https://www.filmlight.ltd.uk/products/truelight/overview_tl.php)
- [Pomfort Livegrade — ACES CDL grading mode KB](https://kb.pomfort.com/workflows/aces/aces-grading-mode-in-livegrade/)
- [DaVinci Resolve Color Match — Emerson College guide](https://support.emerson.edu/hc/en-us/articles/21709302725531-Color-Match-in-Resolve)
- [FilmConvert CineMatch — sensor-profile methodology](https://www.filmconvert.com/blog/introducing-cinematch-camera-matching-made-easy/)
- [Colourlab Ai 3 — neural ACES engine](https://colourlab.ai/pro/)
- [ethan-ou/camera-match (Python library, ACES-style)](https://github.com/ethan-ou/camera-match)
- [Perceptual Color Characterization of Cameras (NIH/PMC)](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC4299059/)
- [Delta E thresholds reference (ViewSonic ColorPro)](https://www.viewsonic.com/ap/colorpro/articles/detail/deltae2color-accuracy_811)
- [Comparing IDTs for RED Scarlet-X (academic paper)](https://www.researchgate.net/publication/326779225_Comparing_different_ACES_Input_Device_Transforms_IDTs_for_the_RED_Scarlet-X_Camera)
