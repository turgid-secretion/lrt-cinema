# Synthesis: option space for per-camera color correction

*Cross-territory synthesis distilled from the 4 research-agent reports
(`01_*.md` through `04_*.md`).*

## The 12 ΔE residual has a name: Luther/Maxwell-Ives failure

A camera RGB → XYZ matrix is exactly invertible only when the camera's
spectral sensitivity functions (SSFs) are a linear combination of the
CIE 1931 color matching functions — the **Luther condition** (also
Maxwell–Ives criterion). Production Bayer sensors with on-sensor dye
filters never satisfy it. For any chosen 3×3, metamers exist that the
matrix cannot place correctly.

Empirical floor from the literature:

- **Vazquez-Corral 2014** (*Sensors* 14(12), 37 cameras × 203,490 reflectance×illuminant pairs): best 3×3 = **1.83 ΔE2000 mean** on ColorChecker patches.
- **dcamprof (Torger)**: ~1.7 ΔE training, 2–3 unseen, 14 peak on saturated patches.
- **lrt-cinema observed**: 12.66 ΔE mean post-fit on D750 + bundled DCP. High end of plausible because the DCP's 90×16×16 LookTable contributes saturated-patch divergence that the chart doesn't expose.

The 12 ΔE is not a fit-quality bug. It's the theoretical floor for a
linear 3×3 + the non-linear content the DCP's LookTable expresses.

## The convergent recommendation across all 4 territories: root-polynomial regression

**Finlayson, Mackiewicz, Hurlbert 2015** (IEEE TIP 24(5)). 3×N matrix on
√, ∛ root terms of (R,G,B). Order 2 = 6 terms; order 3 = 13 terms.
**Exposure-invariant** (a×R → a×corrected — critical for timelapse).
Closed-form regression. No overfit risk at order ≤ 3.

Convergence:

- **Cinema agent**: THE standard "beyond 3×3" upgrade in ACES tooling. AMPAS IDT Calculator ships `optimisation_factory_Oklab_15`. Typically halves residual ΔE on chart-only fits.
- **Academic agent**: Kucuk 2022 (CIC30) — **root-poly beats small CNNs** on camera color correction. NNs are exposure-fragile. Root-poly is the load-bearing finding.
- **Adjacent agent**: matches astronomy's "color term decomposition" pattern (residual proportional to chromaticity).
- **RAW agent**: dcamprof uses related polynomial-style fits as stage-2 enrichment.

**Already in colour-science.** `colour.characterisation.optimisation_factory_Oklab_15`. Zero new code dependency.

## The two structural paths beyond root-poly

**Path A — SSF-integrated IDT** (the "no chart, deterministic" path):

- Sub-2 ΔE achievable without any chart.
- Closed-form integration of camera SSF × illuminant × target primaries.
- AMPAS P-2013-001 procedure; `colour.matrix_idt`; rawtoaces.
- **Nikon D750 SSF is in butcherg/ssf-data** (CC BY-NC-SA 4.0 — non-commercial; users can compute locally, repo cannot ship the derivative profile).

**Path B — Non-linear residual catcher** (the "match Adobe LookTable" path):

- After matrix/poly, fit a 2.5D HSV LookTable as residual stage.
- What Adobe's DCP LookTable encodes; what dcamprof's spline-smoothed 2.5D LUT implements.
- dcpTool's "hue twist untwist" trick: collapse Adobe LookTable's V-dependency to a single skin-tone slice, fit result into a luminance-invariant 2D HueSatMap usable by dt's existing `color look up table` module (Lab grid).

## Other meaningful findings

**Macenko OD-space fit (cheapest experiment)**. Fit the 3×3 in
`-log(linear_RGB + ε)` space. Spectral mismatches behave more linearly
in log space (Beer-Lambert linearity for absorptive processes; weaker for
additive light but still helps). Adjacent-fields agent flagged this as
highest information-per-effort: hours of work, no architectural change.

**Loss space matters**: fit in Oklab or Jzazbz, not Lab. Uniform across
wider gamut + HDR range. Cinema fits in these.

**Whitepoint preservation as hard constraint**: neutral stays neutral.
Unconstrained LS drifts the neutral axis and visibly degrades skin/sky.

**AMPAS 190-reflectance training set > ColorChecker 24**. Available in
colour-datasets. More patches → better fit, particularly on saturated
chromas where 24 undersamples.

**Cinema's per-camera transform is 1D + 3×3 (or root-poly), NOT 3D LUT**.
ARRI LogC4 → ACES is a published 3×3. Same at Sony, RED, BMD. The 3D
LUT mythology is about *output* (looks, ODT, RRT), not input. Lattice
regression with thin-plate smoothness is the math when LUT IS used,
17³ is the sweet spot, tetrahedral interpolation is non-negotiable.

**darktable's official position is that matching LR is structurally an
impedance mismatch, not a bug**. Their architecture: 3×3 colorimetric +
CAT16 adaptation + filmic/sigmoid/agx for look. Aurélien Pierre (dt) and
Anders Torger (dcamprof) hold the "matrix is enough" position; the rest
of the industry (Adobe, Capture One, RawTherapee, ART) ships hybrid
matrix + LookTable.

**FFCC (Barron CVPR 2017)** for per-frame WB temporal smoothing.
Fourier-space illuminant estimation, 13-20% lower error and 250-3000×
faster than prior SOTA. Produces posterior over illuminants. Directly
applicable to timelapse WB drift.

**No production-shipped ML tool beats DCP** for camera characterization
today. Root-poly + SSF + LookTable IS the SOTA.

## Industry ΔE thresholds (for orienting expectations)

| Tier | ΔE2000 | Comment |
|---|---|---|
| Mastering / DI | ≤ 1 | "Imperceptible" |
| Cinema reference | 1 < ΔE ≤ 2 | "Visible only under careful comparison" |
| Broadcast | 2 < ΔE ≤ 3 | "Professionally acceptable" |
| Consumer | 3 < ΔE ≤ 5 | "Slight visible difference" |
| **lrt-cinema 3×3 fit today** | **~12** | **4× broadcast tolerance** |
| Root-poly estimate | 4–6 | Approaching broadcast |
| SSF-IDT (when SSF exists) | < 2 | Cinema reference |
| DCP engine (the full Adobe path) | ~2.2 post-fit on DSC_4053 | Cinema reference |

## License / patent constraints

- Adobe DNG spec: royalty-free patent license (reading + writing DCP profiles is covered).
- ACES, rawtoaces, colour-science: open licenses (Apache 2.0 / BSD-3).
- dcamprof: GPL-3 (cannot link; can shell out).
- camspec / butcherg SSF data: **CC BY-NC-SA 4.0** — non-commercial.
  Users may compute their own profile from this data locally; lrt-cinema
  (Apache 2.0) cannot ship derivative profile files as part of the repo
  without license-compatibility implications.
- Hung 1993 tetrahedral interpolation patents (US5581376): expired (> 20 years).
- No patent blockers for root-polynomial PCC, Macenko OD-fit, FFCC,
  lattice regression.

## Three concrete paths the research surfaces

**Path 1 — Drop-in root-poly upgrade.** Replace the 3×3 channelmixer fit
with Finlayson 15-term root-polynomial via colour-science's
`optimisation_factory_Oklab_15`. No new dependencies. Should close a
meaningful fraction of the 12 ΔE residual. Estimate: 4-6 ΔE post-fit.
Bury the existing channelmixerrgb v3 work or treat it as a baseline.

**Path 2 — SSF-integrated IDT for D750 (and other supported bodies).**
Pull SSF data from butcherg/ssf-data (CC BY-NC-SA — user computes
locally). Use `colour.matrix_idt`. Should achieve < 2 ΔE on D750 with no
chart, no DCP dependency for the linear-portion correction. Doesn't
address the non-linear LookTable residual (that's Path 3).

**Path 3 — Non-linear residual catcher.** Add a 2.5D HSV LookTable after
the matrix/poly stage. Either dcpTool's "untwist" of Adobe's LookTable
(fastest path, dt-compatible via `color look up table` module) or
dcamprof-style spline-smoothed LUT fit from chart data. Closes the
remaining ΔE that matrix/poly fundamentally cannot.

These paths are additive: full stack = root-poly + SSF + HSV residual.
But the framing-shift document (06) reopens whether closing ΔE is even
the right objective.
