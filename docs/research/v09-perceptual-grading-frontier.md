# Perceptual-grading frontier: are there objectively *better* ways to bake HSL / Color-Grade / Texture?

**Status:** Research scoping, 2026-05-31. Feeds a binding entry in
[docs/DECISIONS.md](../DECISIONS.md). Strategic question: we have just shipped
clean-room best-public-approximations of Adobe's **HSL panel** and **Color
Grading wheels** (`src/lrt_cinema/develop_ops.py`, in the Adobe-hexcone HSV /
additive-linear-ProPhoto domains). Adobe's exact math is closed-source, so ours
are *defensible approximations, not ports*. Since we are forced off Adobe's
runtime regardless, the question is no longer "how do we match Adobe?" but **"are
there objectively BETTER (more perceptually correct, fewer artifacts,
frontier-quality) ways to implement these creative knobs — and how is 'better'
quantified?"**

**Evidence tags** (mirroring [v08-linear-exr-gamut-resolve-nuke.md] house style):
**[std]** official standard (CIE / SMPTE / ITU / ASC) · **[paper]** peer-reviewed
or archival paper with authors/year/venue · **[blog]** self-published, *not*
peer-reviewed (state maturity) · **[lib]** canonical library/source doc ·
**[claim]** secondary finding, not primary-verified.

Every URL below was fetched and made to resolve during this research pass
(2026-05-31); anything that did not resolve was dropped rather than guessed. All
`colour`-library function names were checked against the live 0.4.x docs / source.

---

## TL;DR (the binding recommendations)

1. **"Better" is *measurable*, not purely aesthetic, for the parts that matter
   here.** The objective axes are: (a) **perceptual uniformity** of a slider's
   sweep — even ΔE2000 / ΔE‑ITP / CAM16‑UCS steps per slider unit; (b) **hue
   constancy** — how little the hue *angle* drifts when you change only
   lightness or only chroma (the failure the **Abney effect** and
   **Bezold–Brücke shift** name and quantify); (c) **gamut behavior** —
   out-of-gamut %, clipping, posterization; (d) for local-contrast ops,
   **halo / gradient-reversal** magnitude and feature-preservation (SSIM). The
   *residual* subjective part (does this grade look pleasing?) is gold-standard
   only via **observer panels** and is **out of scope** here (§1).

2. **Our HSV-hexcone HSL is the weakest link, and it is fixable with a
   first-class space.** HSV value/lightness is **not** perceived lightness — at
   constant `V`, perceived lightness varies strongly with hue, which is exactly
   the artifact a per-hue **Luminance** slider exposes. **OKLCh** (Ottosson
   2020) or **CAM16‑UCS** (Li et al. 2017) decouple lightness/chroma/hue far
   better. **Recommendation is dual-mode** (§4): for the **sRGB display TIFF**
   (the LRT round-trip), **Okhsl/Okhsv** (Ottosson 2020) is the strongest
   modern fit *because it is sRGB-gamut-bound by design* — but that same binding
   makes it **wrong** for the wide-gamut master; there use **OKLCh proper** (a
   gamut-agnostic transform of XYZ).

3. **Color Grading should adopt cinema-native primitives on the ACES path.** Our
   master already targets DaVinci Resolve / ACES ([v08-linear-exr-…]). **ASC CDL**
   (slope/offset/power + saturation) **[std]** and **lift/gamma/gain** wheels are
   the *native, interchange-standard* grade primitives downstream — emitting in
   that idiom is arguably **more correct** than reverse-engineering Adobe's opaque
   Blending/Balance overlay, and it round-trips losslessly into Resolve. Keep the
   Adobe-faithful split-tone overlay for the TIFF path.

4. **Texture/Clarity (deferred) must use an edge-aware filter, never naive USM.**
   The frontier is **local Laplacian filters** (Paris–Hasinoff–Kautz 2011;
   Aubry et al. 2014) — provably halo-free at the artifact that defines this
   op-family — with the **guided filter** (He–Sun–Tang 2013) as the
   cheap-and-good alternative. Naive unsharp-mask *guarantees* halos.

5. **`colour-science` (already a dependency) supplies the spaces and the
   metrics**, but **NOT general gamut mapping/compression** in 0.4.x — that lives
   in **ACES RGC (ampas/aces-dev CTL)** and **OpenColorIO ≥ 2.1**. Do not assume
   `colour` will gamut-compress for you (§3 — explicit error-prevention note).

6. **Architecture: a `--render-intent` switch.** `faithful` (default) =
   today's Adobe-hexcone ops → the sRGB TIFF for LRT round-trip fidelity.
   `perceptual` = OKLCh/CAM16 HSL + CDL-idiom grade → the ACEScg EXR master.
   This is not a compromise; it maps 1:1 onto the two emission targets already in
   [CLAUDE.md] and lets each target use the *correct* primitive (§4–5).

---

## §1 — How "better" is quantified (the objective metrics)

A creative knob is "better" if, holding the *intent* fixed, it (i) moves
perception evenly per slider unit, (ii) does not drift attributes the user did
not touch, (iii) stays in gamut without clipping/banding, and (iv) for spatial
ops, adds no halos. Each is measurable.

### 1.1 Perceptual uniformity of a slider sweep

The defining property of a *uniform color space* (UCS) is that Euclidean
distance ≈ perceived difference. Sample a slider at *N* positions, convert each
result to a UCS, and a *good* knob produces **near-constant ΔE between adjacent
steps** (a perceptual ramp), not bunching at one end.

- **ΔE2000 / CIEDE2000** — the CIE-recommended small-difference metric. Its
  many implementation pitfalls (hue-angle wraparound, the discontinuities in
  the rotation/`G` terms) are documented in **Sharma, Wu & Dalal (2005),
  "The CIEDE2000 color-difference formula: implementation notes, supplementary
  test data, and mathematical observations," *Color Research & Application*
  30(1):21–30, DOI 10.1002/col.20070** [paper]
  (<https://onlinelibrary.wiley.com/doi/10.1002/col.20070>; test data at
  <https://hajim.rochester.edu/ece/sites/gsharma/papers/CIEDE2000CRNAFeb05.pdf>).
  Tuned for **threshold** differences on *surface colors* under reference
  conditions — it is the right tool for the sRGB TIFF, a known D65 display space.
- **ΔE‑ITP** — `ΔEITP = 720·√(ΔI² + ΔCt² + ΔCp²)` over ICtCp, ratified as
  **Recommendation ITU‑R BT.2124‑0 (01/2019), "Objective metric for the
  assessment of the potential visibility of colour differences in television"**
  [std] (<https://www.itu.int/rec/R-REC-BT.2124-0-201901-I/en>;
  PDF <https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.2124-0-201901-I!!PDF-E.pdf>).
  **NB:** BT.2124 (the *metric*) is distinct from BT.2100 (which *defines*
  ICtCp). ΔE‑ITP is the right tool for **HDR / wide-gamut**, where ΔE2000 is
  known to mispredict; "a value of 1 ≈ one JND in the most critical adaptation
  state" [std].
- **CAM16‑UCS ΔE** — Euclidean distance in CAM16‑UCS (Li et al. 2017, below),
  the most modern appearance-model-grounded UCS; appropriate when viewing
  conditions are explicitly modeled.

**Reproducible protocol (the defensible test, not just a metric name):**

```
for slider_value in linspace(min, max, N):     # e.g. N = 33
    img  = apply_op(test_patch, slider_value)   # one op, one patch set
    ucs  = XYZ_to_<UCS>(to_XYZ(img))            # OKLab / CAM16-UCS / ICtCp
steps      = diff(ucs, axis=slider)             # adjacent-step vectors
uniformity = std(norm(steps)) / mean(norm(steps))   # ↓ = more uniform
hue_drift  = std(hue_angle(ucs))                # see §1.2
```

Run on a ColorChecker-like spread of saturated patches (a grey wedge is *blind*
to hue drift and tone-mode errors — [CLAUDE.md §0]). `uniformity` → 0 means a
perceptual ramp; `hue_drift` quantifies §1.2. `colour` provides every converter
and `delta_E_*` used here (§3).

### 1.2 Hue constancy / hue-linearity (the sharp argument against HSV)

The human-vision facts that make HSV-hexcone wrong for a Luminance/Lightness
knob:

- **Abney effect** — adding white light (lowering colorimetric purity) **shifts
  perceived hue** even though dominant wavelength is unchanged. Original:
  **W. de W. Abney (1909/1910), "On the Change in Hue of Spectrum Colours by
  Dilution with White Light," *Proc. R. Soc. Lond. A* 83(560):120–127** [paper].
  Modern measurement: **Kurtenbach, Sternheim & Spillmann (1984), "Change in hue
  of spectral colors by dilution with white light (Abney effect)," *JOSA A*
  1(4):365–372, DOI 10.1364/JOSAA.1.000365** [paper]
  (<https://opg.optica.org/josaa/abstract.cfm?uri=josaa-1-4-365>). I.e. changing
  **Saturation** at constant hue *also* shifts perceived hue.
- **Bezold–Brücke shift** — perceived hue changes with **luminance/intensity** at
  constant spectral composition (blues/yellows shift toward longer wavelengths
  with higher luminance; only ~3–4 "invariant" wavelengths hold). Noted by
  Brücke (1866); first published experiments **W. von Bezold (1873)**; canonical
  measurement **D. McL. Purdy (1931), "Spectral Hue as a Function of Intensity,"
  *Am. J. Psychol.* 43(4):541–559** [paper]
  (<https://en.wikipedia.org/wiki/Bezold%E2%80%93Br%C3%BCcke_shift>). I.e.
  changing **Lightness/Value** at constant hue *also* shifts perceived hue.

These are *exactly* the cross-talk a per-hue-band panel must avoid: a Luminance
slider should not rotate hue; a Saturation slider should not rotate hue. HSV-
hexcone has **no** mechanism to resist either — its `H` is a fixed geometric
angle, but constant-`H` is *not* constant-perceived-hue. The metric: hold
H/S fixed, sweep V; measure **hue-angle variance in a UCS** (`hue_drift` above).
A lower value is objectively better; Ottosson's own data (§2.1) shows OKLab
roughly halves CIELAB's hue error and CIELAB already beats HSV.

### 1.3 Gamut behavior

- **% out-of-gamut**: convert the op output to the target gamut (sRGB / AP1) and
  count pixels with any channel < 0 or > 1 (display) at a real threshold; our
  own footprint method in [v08-linear-exr-…] §"Measured gamut footprint" is the
  template (and warns that sub-1e-4 is CAT/quantization noise, not excursion).
- **Clipping / posterization**: a knob that pushes values past the gamut hull
  and then hard-clips loses chroma and can band in 16-bit; a *gamut-compressed*
  result (§3, ACES RGC) rolls off smoothly. Posterization is measurable as
  histogram-comb spacing after quantization.

### 1.4 Local-contrast ops (Texture/Clarity)

- **Halo / gradient reversal**: the signature artifact of multi-scale contrast.
  Quantify as overshoot/undershoot amplitude in the 1-D edge-response, or count
  **gradient-sign reversals** introduced relative to the input. Paris et al.
  (2011) frame the entire local-Laplacian contribution as *halo-free* multi-scale
  manipulation — that *is* the metric the field optimizes (§2.3).
- **Feature preservation**: **SSIM** (structural similarity) of output vs input
  in flat regions confirms the op added local contrast without destroying
  structure or injecting ringing.

### 1.5 Where only observer studies suffice (out of scope)

"Which grade is more *pleasing*", "does this skin tone look *right*", and the
*magnitude* of an acceptable hue rotation are **preference** questions. The gold
standard is a **psychophysical observer panel** (paired comparison / category
scaling under controlled viewing) — the same methodology that produced the
Abney/Bezold data and that underlies every UCS fit. We have **no panel**, so we
explicitly **do not** claim aesthetic superiority. We claim only the *measurable*
axes above, and we flag the rest as subjective — matching the project's
"[here]/[doc]/[claim]" honesty discipline.

---

## §2 — Per-op-family frontier candidates (with open implementations)

### 2.1 HSL (per-hue Hue / Saturation / Luminance)

Our current op (`apply_hsl`) works in the **Adobe-hexcone HSV** domain — same
space as the DCP HueSatMap — with a triangular partition-of-unity over 8 bands.
Its documented caveat is honest: band centres, hue-rotation magnitude, and the
HSL-Luminance↔HSV-Value mapping are all closed-source guesses. The deeper issue
is the *domain*, not the band layout.

**Candidate spaces, with white-point / luminance fit against our inputs**
(ProPhoto **D50** working space; ACEScg **~D60**; sRGB **D65**):

| Space | Source | Anchor white | Luminance assumption | Hue constancy | Fit for *scene-linear wide-gamut* input |
|---|---|---|---|---|---|
| **HSV-hexcone** (today) | n/a | inherits RGB | none (V ≠ perceived L) | **poor** — H is geometric, fails Abney+B–B | works numerically but perceptually wrong |
| **OKLab / OKLCh** | Ottosson 2020 [blog] | **D65** (`L=1,a=0,b=0` at D65) | relative (`Y=1` ref white) | **strong** (RMS hue 0.49 vs CIELAB 0.69; L 0.20 vs 1.70, author's data) | gamut-agnostic transform of XYZ → **OK**, but needs **D50/~D60→D65 Bradford** adapt first |
| **Okhsl / Okhsv** | Ottosson 2020 [blog] | D65, **sRGB-gamut-bound** | reference white `Y=1` | strong (inherits OKLab) | **NO** — S/L are normalized to the *sRGB cusp*; wide-gamut values mis-map/clip. **sRGB-only by construction** |
| **CAM16‑UCS** | Li et al. 2017 [paper] | adapting white = a **parameter** | needs **adapting luminance + surround** params you must fix | strong (full appearance model) | **OK** if you commit viewing-condition params; heaviest |
| **Jzazbz / JzCzhz** | Safdar et al. 2017 [paper] | D65 | **absolute cd/m², Jz=100% @ 10000 nits, PQ-based** | strong, hue-linear by design | **catch:** scene-linear values are *relative*; feeding them needs an **assumed absolute-luminance scaling** (pick a diffuse-white nit level). Built for HDR. |
| **IPT / ICtCp** | Ebner–Fairchild 1998 [paper]; BT.2100 [std] | D65 | IPT relative; ICtCp **PQ/HLG absolute** | **IPT designed for hue linearity**; ICtCp inherits it | IPT OK (relative); ICtCp is **HDR-display-referred** (PQ), not scene-linear |

Primary sources:

- **OKLab/OKLCh** — **Björn Ottosson, "A perceptual color space for image
  processing" (Dec 2020)** [blog, *self-published, not peer-reviewed*]
  (<https://bottosson.github.io/posts/oklab/>). Anchored to **D65** ("Oklab uses
  a D65 whitepoint, since this is what sRGB and other common color spaces use");
  input is **linear sRGB / XYZ**; the author reports OKLab RMS **hue** error
  **0.49 vs CIELAB 0.69** and **lightness** **0.20 vs 1.70**, and that CIELAB's
  "largest issue is [its] inability to predict hue … blue hues are predicted
  badly." Maturity caveat is load-bearing for a binding doc: it is a blog post,
  *but* it has been adopted into **CSS Color 4/5**, Photoshop's default gradient
  interpolation, and game engines [claim] — broad production uptake, not formal
  CIE standing.
- **Okhsv / Okhsl** — **Björn Ottosson, "Two new color spaces for color picking —
  Okhsv and Okhsl" (2020)** [blog]
  (<https://bottosson.github.io/posts/colorpicker/>). Built on OKLCh, they
  "map the sRGB gamut to a cylinder" — i.e. **S and L are normalized relative to
  the sRGB gamut cusp**; the author scopes the work to "picking colors in the
  sRGB gamut" and *explicitly defers* wide-gamut/HDR "for future research." **This
  is why Okhsl belongs only on the sRGB TIFF path.**
- **CAM16 / CAM16‑UCS** — **C. Li, Z. Li, Z. Wang, Y. Xu, M. R. Luo, G. Cui,
  M. Melgosa, M. H. Brill, M. Pointer (2017), "Comprehensive color solutions:
  CAM16, CAT16, and CAM16‑UCS," *Color Research & Application* 42(6):703–718,
  DOI 10.1002/col.22131** [paper]
  (<https://onlinelibrary.wiley.com/doi/10.1002/col.22131>). There is **no
  free-standing CIE document number** for CAM16‑UCS — cite the paper. Requires
  viewing-condition inputs (adapting field luminance `L_A`, background `Yb`,
  surround) — power, but parameters we'd have to fix and document.
- **Jzazbz** — **M. Safdar, G. Cui, Y. J. Kim, M. R. Luo (2017),
  "Perceptually uniform color space for image signals including high dynamic
  range and wide gamut," *Optics Express* 25(13):15131–15151,
  DOI 10.1364/OE.25.015131** [paper]
  (<https://opg.optica.org/oe/fulltext.cfm?uri=oe-25-13-15131&id=368272>).
  **Absolute-luminance** design: the `Jz` channel's transfer is a PQ-derived
  curve with **Jz=100% ⇔ 10000 cd/m²** [claim, Wikipedia summary of the paper] —
  excellent for HDR/wide-gamut *if* you assign an absolute white level, a
  decision scene-linear-relative data does not carry.
- **IPT** — **F. Ebner & M. D. Fairchild (1998), "Development and Testing of a
  Color Space (IPT) with Improved Hue Uniformity," *Proc. 6th Color Imaging
  Conference (CIC6)*, IS&T** [paper]
  (<https://library.imaging.org/cic/articles/6/1/art00003>). Built specifically
  to make **constant-perceived-hue lines straight** — the property an HSL panel
  most needs.
- **ICtCp** — **Recommendation ITU‑R BT.2100** (latest BT.2100‑3, 02/2025) [std]
  (<https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.2100-3-202502-I!!PDF-E.pdf>);
  derived by Dolby from IPT for HDR, **PQ/HLG** nonlinearity, "constant
  intensity" hue-preservation on the neutral axis.

**Verdict (HSL):** the single best **hue constancy + lightness-without-hue-drift**
for a display-referred sRGB knob is **OKLCh** (or its picker form Okhsl). For the
**wide-gamut/scene-linear** master, **OKLCh proper** (D50/~D60→D65 adapted) or
**CAM16‑UCS** (if we commit viewing params); **Okhsl/Okhsv and ICtCp are
disqualified** for that path (sRGB-bound and HDR-display-PQ respectively).
Jzazbz is viable on the master *only* with an assumed nit scaling — extra
ceremony for marginal gain over OKLCh here.

### 2.2 Color Grading (shadow / mid / highlight tonal tints)

Our `apply_color_grade` is a luminance-masked additive split-tone in linear
ProPhoto with a perceptual-luminance zone mask and a partition-of-unity over
shadow/mid/highlight + global, with `blending`/`balance` shaping the masks. It is
a *reasonable* model of Adobe's panel, but Adobe's tint strengths and
Blending/Balance response are closed-source guesses.

**Cinema-native primitives (interchange standards, not reverse-engineering):**

- **ASC CDL** — **American Society of Cinematographers Color Decision List**
  [std]. The math is **Slope/Offset/Power (SOP)** per RGB channel —
  `out = (in·slope + offset)^power` — plus a **tenth** number, **Saturation**
  (added in **v1.2**), applied to all channels *after* SOP. Nine SOP numbers +
  saturation = one "color decision," exchanged via `.cdl`/`.ccc`/`.cc`
  (<https://en.wikipedia.org/wiki/ASC_CDL>; primary spec is "ASC Color Decision
  List (ASC CDL) Transfer Functions and Interchange Syntax," ASC Technology
  Committee — distributed by the ASC, not a public URL). **Why it may be MORE
  correct downstream:** our master targets Resolve/ACES; CDL is the *lingua
  franca* grade primitive there, applies cleanly in log or linear, and round-
  trips losslessly into the colorist's first node.
- **Lift / Gamma / Gain** wheels (DaVinci-style) — shadows/mids/highlights with
  **overlapping** ranges (Lift = black pedestal, Gamma = midtone power, Gain =
  white scale), plus an **Offset** wheel (whole-image, log/printer-light idiom).
  Resolve's **Log** wheels are the *zero-overlap* variant for precise zone
  isolation [claim] (<https://www.blackmagicdesign.com/products/davinciresolve/color>;
  <https://jayaretv.com/color/difference-between-the-3-types-of-color-wheels/>).
  This is structurally close to what Adobe's Color Grade *is* (a 3-zone wheel
  set), but expressed in the standard tonal-operator math.
- **Log-domain tonal masks** — performing the zone split in a log encoding
  (ACEScct-like) gives perceptually even shadow/highlight separation, which is
  what Resolve's Log wheels and the colorist expect; our current sRGB-OETF mask
  is a display-domain proxy for the same idea.

vs **Adobe's opaque blending/balance overlay**: Adobe's panel is a black box; we
can only approximate it. On the **TIFF (LRT round-trip)** path, the *approximation
is the point* — fidelity to what the LRT user saw. On the **ACES master**, an
**SOP+sat (CDL)** or **LGG** emission is a documented standard the downstream tool
implements identically, so it is reproducible and arguably more correct.

**Verdict (Color Grade):** keep the Adobe-faithful split-tone for the TIFF;
on the master, prefer **CDL (SOP+saturation)** as the interchange-correct
primitive (optionally LGG wheels), applied in a log domain.

### 2.3 Texture / Clarity (deferred future op-family)

Not yet implemented (Sharpness is a documented no-op). When added, the artifact
that defines success is the **halo**. Frontier, open-published methods:

- **Local Laplacian Filters** — **S. Paris, S. W. Hasinoff, J. Kautz (2011),
  "Local Laplacian Filters: Edge-aware Image Processing with a Laplacian
  Pyramid," *ACM TOG (Proc. SIGGRAPH 2011)*, DOI 10.1145/1964921.1964963**
  [paper] (<https://dl.acm.org/doi/10.1145/1964921.1964963>;
  author PDF <https://people.csail.mit.edu/sparis/publi/2011/siggraph/>; reissued
  as **CACM 58(3), 2015, DOI 10.1145/2723694**). The reference method for
  **halo-free** multi-scale detail/contrast — *the* algorithm behind modern
  Clarity-type controls. Accelerated form: **M. Aubry, S. Paris, S. W. Hasinoff,
  J. Kautz, F. Durand (2014), "Fast Local Laplacian Filters: Theory and
  Applications," *ACM TOG* 33(5):167** [paper]
  (<https://hal.science/hal-01063419>) — ~50× speed-up, important for sequences.
- **Guided Filter** — **K. He, J. Sun, X. Tang, "Guided Image Filtering,"
  ECCV 2010 (DOI 10.1007/978-3-642-15549-9_1) and *IEEE TPAMI* 35:1397–1409,
  2013** [paper]
  (<https://people.csail.mit.edu/kaiming/publications/pami12guidedfilter.pdf>).
  Edge-preserving smoothing with **O(N)**, non-iterative cost and **better
  near-edge behavior than the bilateral filter** — the cheap, robust base for a
  local-contrast/Clarity op.
- **Bilateral Grid** — **J. Chen, S. Paris, F. Durand, "Real-time Edge-Aware
  Image Processing with the Bilateral Grid," *ACM TOG (Proc. SIGGRAPH 2007)*,
  DOI 10.1145/1275808.1276506** [paper]
  (<https://groups.csail.mit.edu/graphics/bilagrid/>). GPU-real-time edge-aware
  base; relevant if local tone work ever needs to scale.

vs **naive unsharp-mask** (a single Gaussian high-pass added back): **guarantees
halos** at high-contrast edges (the overshoot/undershoot of §1.4) and has no edge
awareness. It is the baseline these papers were written to beat.

**Verdict (Texture/Clarity):** if/when implemented, **local Laplacian** (fast
variant) for quality, **guided filter** for a lightweight first cut; **never**
plain USM. This op is also the strongest candidate to *stay grade-side* (our
current rationale) — but if baked, bake it edge-aware.

---

## §3 — Open implementations / libraries (license + maturity)

| Capability | Provider | Where | License | Maturity |
|---|---|---|---|---|
| OKLab / OKLCh | `colour-science` | `XYZ_to_Oklab`, `Oklab_to_XYZ`, `Oklab_to_Oklch`, `Oklch_to_Oklab` | BSD-3 | stable 0.4.x [lib] |
| CAM16 / CAM16‑UCS | `colour-science` | `XYZ_to_CAM16UCS`, `CAM16UCS_to_XYZ`, `JMh_CAM16_to_CAM16UCS` | BSD-3 | stable [lib] |
| Jzazbz / JzCzhz | `colour-science` | `XYZ_to_Jzazbz`, `Jzazbz_to_XYZ`, `Jzazbz_to_JzCHab` | BSD-3 | stable [lib] |
| IPT | `colour-science` | `XYZ_to_IPT`, `IPT_to_XYZ`, `IPT_to_ICH` | BSD-3 | stable [lib] |
| ICtCp | `colour-science` | `RGB_to_ICtCp`, `ICtCp_to_RGB` (`colour/models/rgb/ictcp.py`), `ICtCp_to_ICHtp` | BSD-3 | stable [lib] |
| ΔE2000 / ΔE‑ITP / ΔE CAM16‑UCS | `colour-science` | `colour.difference.delta_E_CIE2000`, `delta_E_ITP`, `delta_E_CAM16UCS` | BSD-3 | stable [lib] |
| **General gamut mapping / compression** | **— not in `colour` 0.4.x —** | only **Pointer's-gamut volume** utils (`colour/volume/pointer_gamut.py`); a separate experimental `colour-science/gamut-mapping-ramblings` repo exists but is **not** the shipped library | BSD-3 | **absent / experimental** [lib] |
| ACES Reference Gamut Compression (RGC) | AMPAS | `aces-dev` CTL `transforms/ctl/lmt/LMT.Academy.GamutCompress.ctl` | AMPAS license (permissive) | **ACES 1.3, production** [std/lib] |
| Color management / transforms | OpenColorIO (ASWF) | `AcademySoftwareFoundation/OpenColorIO`; **RGC native in OCIO ≥ 2.1** | BSD-3 (ASWF) | **v2 production**, native in Nuke/Resolve/etc. [lib] |

Notes / honesty:

- **`colour-science` function names verified against the live 0.4.x docs/source
  this session** [lib]
  (<https://colour.readthedocs.io/en/develop/colour.models.html>,
  `colour.difference`, and the `ictcp.py` source). Inputs are **CIE XYZ** (so we
  go ProPhoto→XYZ via our existing matrices), and the white-point caveats of §2.1
  apply *before* the call — `colour` will not guess an adaptation for us.
- **The gamut-mapping gap is an error-prevention point, stated plainly:**
  `colour` 0.4.x has **no** general source→destination gamut *compression*. If a
  perceptual HSL/grade pushes values out of AP1/sRGB, **we** must apply
  compression — the ACES **RGC** (an RGB-ratio compression toward the neutral
  axis, with a `threshold` controlling how much of the outer gamut is affected)
  via its CTL or via **OCIO ≥ 2.1**
  (<https://docs.acescentral.com/rgc/specification/>;
  <https://github.com/ampas/aces-dev>;
  <https://github.com/AcademySoftwareFoundation/OpenColorIO>). Do **not** ship a
  doc/implementation that assumes `colour` does this.
- **Papers-with-code:** local Laplacian (authors' MATLAB/C at
  csail.mit.edu/sparis; multiple GitHub reimplementations [claim]); guided filter
  (authors' code + OpenCV `ximgproc::guidedFilter` [claim]); bilateral grid
  (CSAIL project page). License of third-party reimplementations varies — vet
  before vendoring; the *algorithms* are unencumbered (published research).

---

## §4 — The faithful-vs-better tension, mapped to the two emission targets

The two goals genuinely conflict in part:

- **"Match Adobe"** — LRTimelapse users author intent in Lightroom/ACR and expect
  the **Lightroom look** to come back when our TIFF re-enters LRT for the video +
  Motion Blur. Here, an OKLCh HSL that is *perceptually better* would be **wrong**
  if it deviates visibly from what the colorist saw. Fidelity = correctness on
  this path. (And per [CLAUDE.md], byte-exact identity / the ΔE ship-gate is a
  hard invariant — a new HSL domain must still no-op to identity when sliders are
  zero.)
- **"Be better"** — the **scene-linear ACEScg EXR master** is graded fresh in
  Resolve/Nuke. Nobody is matching a Lightroom preview against it; here the
  *better primitive* (perceptual HSL, CDL-idiom grade, edge-aware Clarity) is
  strictly preferable, and Adobe-faithfulness has **no** claim.

These do not have to be reconciled in one code path — they map onto the **two
emission targets already defined in [CLAUDE.md]**:

| | **sRGB display TIFF** (LRT round-trip, default) | **ACEScg EXR master** (Resolve/ACES, opt-in) |
|---|---|---|
| Goal | **Adobe-faithful** (Lightroom look) | **Frontier-quality** (best primitive) |
| HSL domain | today's Adobe-hexcone HSV *(or **Okhsl** — sRGB-bound, perceptual, still faithful-ish)* | **OKLCh** (D50/~D60→D65 adapted) or **CAM16‑UCS** |
| Color Grade | Adobe-faithful luminance-masked split-tone | **CDL (SOP+sat)** / LGG wheels, log-domain |
| Texture/Clarity | match Adobe Clarity *(if ever baked)* | **local Laplacian** / guided filter |
| Gamut safety | sRGB clip (display target) | **ACES RGC** before AP1 encode |
| Metric to gate it | ΔE2000 vs LRT preview / Adobe (existing axes) | ΔE‑ITP / CAM16‑UCS uniformity + OOG % |

**Recommended architecture: a `--render-intent {faithful, perceptual}` switch
(default `faithful`).** `faithful` = today's ops, feeding the TIFF, preserving
the LRT round-trip and the existing ship-gate. `perceptual` = the modern
primitives, feeding the master. The op dataclasses (`HslBands`, `ColorGrade`) are
shared; only the *applicator* differs by intent. This respects the project's
"neutrals passing ≠ correct" rule (validate `perceptual` ops against **saturated**
colour, [CLAUDE.md §0]) and keeps the two axes from contaminating each other.

---

## §5 — Per-op recommendation + rough effort / risk

Effort: **S** ≈ days, **M** ≈ 1–2 weeks, **L** ≈ multi-week. Risk reflects
both implementation difficulty and **the chance of getting the colour science
wrong** (the expensive failure for this project).

| Op-family | TIFF (faithful) | Master (perceptual) | Effort | Risk | Specific method |
|---|---|---|---|---|---|
| **HSL** | **stay Adobe-faithful** (keep `apply_hsl` hexcone) | **adopt modern: OKLCh** per-band Hue/Chroma/Lightness; CAM16‑UCS if viewing params accepted | **M** | **Med** — OKLCh transform is trivial via `colour`; the risk is the **D50/~D60→D65 Bradford** step and re-deriving the 8 band weights in a hue-uniform space + an OOG/gamut-compress pass | OKLCh (`XYZ_to_Oklab`→`Oklab_to_Oklch`); per-band weights on OKLCh hue; **NOT** Okhsl on this path |
| **Color Grade** | **stay Adobe-faithful** (keep split-tone overlay) | **adopt modern: ASC CDL (SOP+saturation)** as the interchange primitive; optional LGG wheels | **S–M** | **Low–Med** — SOP math is trivial and standardized; risk is mapping Adobe's wheel UI (Hue/Sat/Lum ×4) onto SOP faithfully and choosing the log domain | ASC CDL v1.2 SOP+sat, applied in a log encoding; LGG as alt |
| **Texture/Clarity** | match Adobe Clarity *(only if baked)* | **adopt modern: local Laplacian (fast variant)**; guided filter for a light cut | **L** | **Med–High** — local Laplacian is non-trivial to implement correctly and per-frame cost is real (use Aubry 2014); **lowest-priority** — currently and defensibly grade-side | Fast Local Laplacian (Aubry 2014) for quality; Guided Filter (He 2013) for speed; **never** USM |

**Sequencing recommendation:** (1) land the **dual-mode switch** scaffolding (the
shared-dataclass / intent-dispatch split) — small, unblocks everything; (2)
**Color Grade → CDL** on the master path first (lowest risk, highest downstream
correctness payoff, our master *already* targets ACES); (3) **HSL → OKLCh** on
the master path (medium risk, needs the gamut-compress pass); (4) leave **TIFF
ops Adobe-faithful and untouched** (protects the LRT round-trip + ship-gate); (5)
**Texture/Clarity** only if user demand justifies the local-Laplacian cost.

**Honest uncertainty:** without an observer panel we cannot prove the perceptual
ops are *aesthetically* preferred — only that they are more perceptually uniform
and hue-stable by the §1 metrics. The *magnitude* of, e.g., a "good" per-band hue
rotation is a preference. And the **faithful** path's value is by definition tied
to a closed-source target (PV5), so its ceiling is the existing ΔE-vs-preview
floor, not zero ([CLAUDE.md], [VALIDATION.md]). The recommendations above are
defensible on the measurable axes and on standards-conformance; they are **not** a
claim that "modern always looks better" — they are a claim that the *master* path
should speak the *standard, measurable* idiom and the *TIFF* path should stay
faithful.

---

## Sources (primary, fetched 2026-05-31)

**Color spaces / appearance models**
- Ottosson, B. (2020). *A perceptual color space for image processing* (Oklab).
  Self-published blog (not peer-reviewed). <https://bottosson.github.io/posts/oklab/>
- Ottosson, B. (2020). *Two new color spaces for color picking — Okhsv and
  Okhsl*. Self-published. <https://bottosson.github.io/posts/colorpicker/>
- Li, C., et al. (2017). *Comprehensive color solutions: CAM16, CAT16, and
  CAM16‑UCS.* Color Research & Application 42(6):703–718. DOI 10.1002/col.22131.
  <https://onlinelibrary.wiley.com/doi/10.1002/col.22131>
- Safdar, M., Cui, G., Kim, Y. J., Luo, M. R. (2017). *Perceptually uniform color
  space for image signals including high dynamic range and wide gamut* (Jzazbz).
  Optics Express 25(13):15131–15151. DOI 10.1364/OE.25.015131.
  <https://opg.optica.org/oe/fulltext.cfm?uri=oe-25-13-15131&id=368272>
- Ebner, F., Fairchild, M. D. (1998). *Development and Testing of a Color Space
  (IPT) with Improved Hue Uniformity.* Proc. CIC6, IS&T.
  <https://library.imaging.org/cic/articles/6/1/art00003>

**Metrics / standards**
- Sharma, G., Wu, W., Dalal, E. N. (2005). *The CIEDE2000 color-difference
  formula: implementation notes, supplementary test data, and mathematical
  observations.* Color Research & Application 30(1):21–30. DOI 10.1002/col.20070.
  <https://onlinelibrary.wiley.com/doi/10.1002/col.20070>
- ITU‑R Recommendation BT.2124‑0 (2019). *Objective metric for the assessment of
  the potential visibility of colour differences in television* (ΔE‑ITP).
  <https://www.itu.int/rec/R-REC-BT.2124-0-201901-I/en>
- ITU‑R Recommendation BT.2100‑3 (2025). *Image parameter values for high dynamic
  range television…* (defines ICtCp).
  <https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.2100-3-202502-I!!PDF-E.pdf>

**Perceptual hue effects**
- Abney, W. de W. (1909). *On the Change in Hue of Spectrum Colours by Dilution
  with White Light.* Proc. R. Soc. Lond. A 83(560):120–127.
- Kurtenbach, W., Sternheim, C. E., Spillmann, L. (1984). *Change in hue of
  spectral colors by dilution with white light (Abney effect).* JOSA A
  1(4):365–372. DOI 10.1364/JOSAA.1.000365.
  <https://opg.optica.org/josaa/abstract.cfm?uri=josaa-1-4-365>
- Bezold, W. von (1873); Purdy, D. McL. (1931). *Spectral Hue as a Function of
  Intensity.* Am. J. Psychol. 43(4):541–559.
  <https://en.wikipedia.org/wiki/Bezold%E2%80%93Br%C3%BCcke_shift>

**Grading primitives**
- ASC Technology Committee. *ASC Color Decision List (ASC CDL) Transfer Functions
  and Interchange Syntax* (SOP + Saturation, v1.2). Summary:
  <https://en.wikipedia.org/wiki/ASC_CDL>
- Blackmagic Design. *DaVinci Resolve — Color* (Lift/Gamma/Gain/Offset, Log
  wheels). <https://www.blackmagicdesign.com/products/davinciresolve/color>

**Local-contrast filters**
- Paris, S., Hasinoff, S. W., Kautz, J. (2011). *Local Laplacian Filters: Edge-
  aware Image Processing with a Laplacian Pyramid.* ACM TOG (SIGGRAPH) 2011.
  DOI 10.1145/1964921.1964963. <https://dl.acm.org/doi/10.1145/1964921.1964963>
  (CACM reissue 2015, DOI 10.1145/2723694).
- Aubry, M., Paris, S., Hasinoff, S. W., Kautz, J., Durand, F. (2014). *Fast Local
  Laplacian Filters: Theory and Applications.* ACM TOG 33(5):167.
  <https://hal.science/hal-01063419>
- He, K., Sun, J., Tang, X. (2010/2013). *Guided Image Filtering.* ECCV 2010
  (DOI 10.1007/978-3-642-15549-9_1); IEEE TPAMI 35:1397–1409 (2013).
  <https://people.csail.mit.edu/kaiming/publications/pami12guidedfilter.pdf>
- Chen, J., Paris, S., Durand, F. (2007). *Real-time Edge-Aware Image Processing
  with the Bilateral Grid.* ACM TOG (SIGGRAPH) 2007. DOI 10.1145/1275808.1276506.
  <https://groups.csail.mit.edu/graphics/bilagrid/>

**Libraries / color management**
- colour-science (BSD-3). Models + difference + ICtCp source verified this
  session. <https://colour.readthedocs.io/en/develop/colour.models.html>
- ACES Reference Gamut Compression (AMPAS, ACES 1.3).
  <https://docs.acescentral.com/rgc/specification/>; <https://github.com/ampas/aces-dev>
- OpenColorIO (ASWF, BSD-3; RGC native in ≥ 2.1).
  <https://github.com/AcademySoftwareFoundation/OpenColorIO>
