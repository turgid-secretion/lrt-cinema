# RAW software camera-profile landscape

*Research agent output, 2026-05-26. Verbatim except for formatting normalization.*

## Comparison table

| Tool / Library | Profile format | Calibration source | Math | Acknowledged limits |
|---|---|---|---|---|
| **RawTherapee** | Adobe DCP (ColorMatrix + ForwardMatrix + HueSatMap + LookTable + ToneCurve), plus ICC matrix or cLUT | User-shot chart -> dcamprof; or vendor DCP; or `colprof` ICC | Linear LS for matrix; spline-smoothed 2.5D LUT for residuals | "LUT smoothed to prioritize smoothness over accuracy"; matrix-only insufficient for saturated/blue regions |
| **ART (Another RawTherapee)** | DCP + ICC (same as RT), plus **CTL scripts** and **CLF / OCIO LUTs** for arbitrary look transforms | Same as RT + ACES IDTs | Same as RT + arbitrary script-based | None published; inherits dcamprof/Argyll limits |
| **darktable** | **3x3 matrix only** in `colorin` (standard / enhanced / vendor); ICC file accepted but matrix-only path is the norm. **No HueSatMap / LookTable / ProfileToneCurve from DCP.** | LibRaw / rawspeed adobe_coeff; user can drop ICC | 3x3 linear; chromatic adaptation moved to `color calibration` (CAT16 default) | DCP support **"closed as not planned"** (issue #4165). Tone/look intentionally pushed to scene-referred `filmic` / `sigmoid` / `agx` |
| **Capture One / Phase One** | Proprietary per-camera ICC with embedded LUT + "hue twists" (Phase One's term) | In-house lab; user can save Color Editor adjustments as ICC | Proprietary; chart-based plus subjective tuning | Phase One openly states their ICCs contain a subjective look ("shadow saturation increase" etc.) |
| **DxO PhotoLab** | "Color Profile" tied to camera/lens "Module" (proprietary binary) | Lab-measured under DxO Spectralight reference illuminant; per-camera-per-lens grid | Multi-dimensional model conditioned on ISO/aperture/focal/distance | Only ships for cameras DxO has physically tested |
| **ON1 Photo RAW** | ICC; defers to X-Rite ColorChecker Camera Calibration app for custom profiles | User chart shot | X-Rite's algorithm | Nothing notable; thin wrapper around XR workflow |
| **Affinity Photo** | LibRaw matrix; user-supplied ICC | None bundled; relies on LibRaw defaults | 3x3 | Minimal color science; treats RAW as a decode problem |
| **LibRaw / dcraw** | Embedded matrix from DNG, Adobe coeff table (compiled-in `adobe_coeff[]`), or user-supplied 3x3 | Adobe's published DNG matrices; vendor matrices for some bodies | Linear only; no LUT | "LR does not utilize ICC for camera calibration. It only accepts DCP." LibRaw exposes no DCP LUT path |
| **dcamprof** (Torger) | DCP or ICC; ColorMatrix + ForwardMatrix + 2.5D HueSatMap; optional 3D LookTable; optional tone-reproduction operator | (a) chart shot via Argyll `scanin`, or (b) **SSF JSON (no chart)** | Stage 1 linear LS for 3x3; stage 2 spline-fit 2.5D LUT in HSV; tone operator preserves hue under contrast | "No real camera is colorimetric." Refuses to publish dE numbers for curved profiles ("once you apply a tone curve you can no longer do normal automatic dE comparisons") |
| **ArgyllCMS `colprof`** | ICC shaper-matrix or cLUT (XYZ or Lab PCS) | Chart shot (recommends Wolf Faust IT8, 288 patches) | Least-squares; cLUT via tetrahedral interpolation | Author explicitly recommends **matrix-only** for general photography; LUTs only for repro studio with custom pigment-matched charts |
| **vkdt (`mkssf` + `mkclut`)** | CLUT (constrained to spectral locus) | SSF estimated from a DCP / DNG or from two ColorChecker shots under A + D65 | PCA + Gaussian mixture model to recover SSF; then synthesizes target patches at any illuminant | New (J. Hanika); no peer-reviewed dE numbers; advantage is **physical** color-space safety |
| **rawtoaces** (ACES IDT) | ACES IDT (3x3 to AP0) | Published SSF if available; else metadata fallback | Closed-form fit of SSF integrated against AP0 primaries under chosen illuminant | "Most accurate" when SSF data present; metadata-only path is "dependent on the camera manufacturer writing correct metadata" |
| **Recent ML** (e.g. "Beyond Calibration", JOSAA 2025) | Learned NN; some hybrid physically-informed | Multi-camera image pairs | NN loss in XYZ; physically-informed regularization | All published work is multi-camera raw-to-raw; no production tool ships this |

## Key insights

**1. Darktable is the outlier — by design.** dt deliberately rejected DCP (issue #4165 closed "not planned"). The official position is that the *colorimetric* part of color rendering belongs in a 3x3 matrix in `colorin`, the *chromatic adaptation* part belongs in `color calibration` (CAT16), and the *look* (tone curve + hue/sat compression) belongs in `filmic rgb`, `sigmoid`, or `agx`. Adobe's DCP architecture bundles all three into one file; dt's pipeline unbundles them. **This means matching LR via dt is structurally an impedance mismatch, not a bug.** You will never close the gap with a single 3x3 in `colorin`, because LR's gap is partly the LookTable (Adobe's per-camera lab-measured hue twists) and partly the ProfileToneCurve (a non-linear contrast/saturation shape).

**2. The 3x3 ceiling Torger publishes matches yours exactly.** dcamprof's own worked example (CC24, Solux 4700K) hits ~1.2 dE median matrix-only and ~1.7 dE with LUT relaxation on the *training* patches. On out-of-training colors (glossy 210-patch target) it climbs to 2.16 dE median matrix-only. Glenn Butcher's IT8 / Argyll profile for a Pentax K-5 II reports **2.6 dE average / 14.6 dE peak with matrix only.** Your ~12 dE2000 residual after a 3x3 fit is consistent with the literature — the linear approximation simply runs out of degrees of freedom in saturated chromas, especially **blues**, which Torger repeatedly calls out as the hardest case ("the human eye sees pure blue as dark, cameras generally do not").

**3. There is exactly one path to <2.0 dE2000 without a chart shot: SSF-based profiling.** Two open-source projects do this today:

- **rawtoaces** uses published SSF data (when available) to compute an IDT in closed form — no chart, no optimization. Accuracy is "most accurate" by their docs because the math is essentially direct integration of the sensor's spectral response against AP0 primaries under a chosen illuminant.
- **vkdt's `mkssf`** can *estimate* SSF from a DCP file you already have (or from two ColorChecker shots), then synthesize an arbitrary-illuminant CLUT. Hanika's design constrains the CLUT to never leave the spectral locus — a class of artifact that 3x3 fits can produce.

The catch: SSF data exists for a finite set of cameras. The community-curated `butcherg/ssf-data` repo and the `camspec` database (Jiang et al. 2013, 28 cameras) cover most Canon/Nikon/Sony bodies but not all. **D750 is in `butcherg/ssf-data`** (confirmed by the academic agent).

**4. The "tone curve in linear vs perceptual" question has a settled answer in dcamprof's design.** Torger's "neutral tone reproduction operator" deliberately does *not* apply the tone curve in linear RGB — that gives the well-known desaturation-with-contrast problem. Instead it mixes an RGB-HSV curve (for low-saturation tones, preserving HSV-luminance) with a pure luminance curve (for high-saturation, preserving hue). darktable's `filmic` and `sigmoid` modules implement the same insight but under different names (`preserve chrominance`, `hue preservation`). **This is why a DCP ProfileToneCurve applied naively in linear space looks wrong**: it was designed to be applied *after* the hue-preserving step Adobe does internally.

**5. The dcpTool "hue twist" decomposition gives you a back-door option.** dcpTool can "untwist" a LookTable by collapsing its V-dependency to a single skin-tone slice and copying the result into the HueSatMap. The remaining table is then *invariant in luminance* — exactly the shape that a 2D HSV transform module can apply. This is potentially the cheapest way to bring the non-linear part of an Adobe profile into a tool that doesn't natively read LookTables: precompute a 90x16 HueSatDelta from the DCP at distillation time, then apply it inside darktable's `color look up table` module (which is Lab-space but accepts a similar grid).

**6. Capture One privately admits what dt publicly forbids.** Phase One's ICC profiles "contain a subjective look with hue twists, for example saturation in the shadows is increased." The Capture One Color Editor lets users *save an arbitrary adjustment as an ICC profile*. C1 has no qualms about baking a Look into the input profile. ART (the RT fork) reaches the same place via CTL scripts and ACES CLF LUTs. **The "matrix is enough" position is essentially unique to Argyll's Elle Stone and to darktable's Aurélien.**

## Concrete options for lrt-cinema's gap

Ranked by cost vs. payoff:

- **(A) SSF-based fit when SSF exists.** Adapt vkdt's `mkssf`/`mkclut` math (or call rawtoaces) to produce a per-camera CLUT applied in a dt-compatible module. <2.0 dE2000 plausible without any user action when the SSF is on file. Falls back to chart for unsupported bodies.
- **(B) Distill the Adobe LookTable into a 2D HueSatMap via dcpTool's "untwist".** Apply the resulting 90x16 grid in dt's existing `color look up table` module (Lab-space). Captures most of the non-linear residual without needing dt to learn DCP natively. Loses the V-dependency, but Torger considers this an acceptable simplification for variable lighting.
- **(C) Stop trying to match LR.** Adopt dt's filmic/sigmoid + CAT16 + your fitted 3x3 as the *target* look. Document the difference. This is what the dt project itself recommends.
- **(D) Run dcamprof in algorithmic mode at distillation time.** Feed it the same SSF data and a synthesized CC24 under the LRT scene's measured illuminant. Outputs a per-shoot DCP that you then apply via your existing DCP engine. Cuts dependency on Adobe's distillation entirely.

The ~12 dE residual is real and it is non-linear by construction. Any 3x3-only solution caps out around where you are now.

## Sources

- [DCamProf - torger.se](https://torger.se/anders/dcamprof.html)
- [Making a camera profile with DCamProf - torger.se](https://torger.se/anders/photography/camera-profiling.html)
- [DCamProf News Archive - torger.se](https://torger.se/anders/dcamprof-old-news.html)
- [How to create DCP color profiles - RawPedia](https://rawpedia.rawtherapee.com/How_to_create_DCP_color_profiles)
- [Camera input profile types compared - ninedegreesbelow](https://ninedegreesbelow.com/photography/camera-profiles-applied.html)
- [How to Make a Better Custom Camera Input Profile - ninedegreesbelow](https://ninedegreesbelow.com/photography/well-behaved-camera-profile.html)
- [ArgyllCMS colprof](https://www.argyllcms.com/doc/colprof.html)
- [darktable input color profile (development)](https://docs.darktable.org/usermanual/development/en/module-reference/processing-modules/input-color-profile/)
- [darktable color calibration (4.8)](https://docs.darktable.org/usermanual/4.8/en/module-reference/processing-modules/color-calibration/)
- [darktable sigmoid module](https://docs.darktable.org/usermanual/development/en/module-reference/processing-modules/sigmoid/)
- [Issue #4165: Support .dcp color input profiles - darktable](https://github.com/darktable-org/darktable/issues/4165)
- [Better support for DCP LookTable - RawTherapee #2721](https://github.com/Beep6581/RawTherapee/issues/2721)
- [LibRaw forum: ICC and DCP profile](https://www.libraw.org/node/2656)
- [Hue Twists - dcpTool](https://dcptool.sourceforge.net/Hue%20Twists.html)
- [vkdt: utilities to create input device transforms](https://jo.dreggn.org/vkdt/src/tools/clut/readme.html)
- [The Quest for Good Color - SSFs and Camera Profiles - discuss.pixls.us](https://discuss.pixls.us/t/the-quest-for-good-color-1-spectral-sensitivity-functions-ssfs-and-camera-profiles/18002)
- [butcherg/ssf-data on GitHub](https://github.com/butcherg/ssf-data)
- [rawtoaces wiki - AcademySoftwareFoundation](https://github.com/AcademySoftwareFoundation/rawtoaces/wiki/usage)
- [ART raw image processor](https://artraweditor.github.io/)
- [Capture One: How can I create a custom camera profile?](https://support.captureone.com/hc/en-us/articles/360002862017-How-can-I-create-a-custom-camera-profile)
- [Capture One: Saving a color scheme as an ICC profile](https://support.captureone.com/hc/en-us/articles/360002602258-Saving-a-color-scheme-as-an-ICC-profile)
- [DxO PhotoLab technologies](https://www.dxo.com/dxo-photolab/technology/)
- [Lumariver Profile Designer manual](https://www.lumariver.com/lrpd-manual/)
- [ON1: Custom Camera Profile with X-Rite (PDF)](https://ononesoft.cachefly.net/content/ON1-X-Rite-Color-Profile-Guide.pdf)
- [Beyond Calibration: Physically Informed Learning for Raw-to-Raw Mapping (arXiv 2506.08650)](https://arxiv.org/html/2506.08650v1)
