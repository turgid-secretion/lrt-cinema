# Demosaic test fixtures — external survey + synthetic-fixture plan

**Question (verbatim brief):** find a robust, comprehensive, ideally externally-sourced
set of test fixtures to *exhaustively verify correct implementation* and *guarantee the
"world-class" claim* of our clean-room demosaic (AMaZE/RCD-class, Nikon D750 RGGB) and the
render pipeline generally. Demonstrate either (a) such a set exists and is satisfied, or
(b) it does not — and if (b), return a complete plan to build our own synthetic fixtures.

**Verdict: (b), with nuance.** No off-the-shelf public fixture set can *exhaustively*
verify a *world-class raw-linear Bayer demosaic*. External sets exist and should be
ingested as a **regression / competitiveness floor**, but every one of them fails on at
least one of: ground-truth circularity, wrong colour domain (display-sRGB vs scene-linear),
band-limited reference, restrictive licence, or measuring *port fidelity* instead of
*quality*. The honest, complete answer is a **layered fixture battery we build ourselves**,
anchored to primary-sourced metrics and to published performance numbers so the
"world-class" claim becomes **falsifiable**. The plan is in §8.

> Method note: every technical spec, dataset fact, metric definition and performance number
> below is attributed to a peer-reviewed paper, an ISO standard, or a primary author/tooling
> source, gathered by four parallel research passes that cross-referenced for agreement and
> disagreement and ran adversarial analysis at each stage. Items that could not be pinned to
> a primary are flagged in §10 and never stated as fact.

---

## 1. What "exhaustive world-class verification" actually requires

Three orthogonal axes — the same structure as the project's existing colour-validation triad
([docs/VALIDATION.md](../VALIDATION.md)), re-derived for the spatial/demosaic problem. **The
single most important framing point (advisor + Agent 4): do not conflate them.**

1. **Axis-D1 — Implementation correctness ("did we build it right").**
   Reconstruction must be self-consistent: flat patches reconstruct bit-exactly, all four
   Bayer phases agree, output is finite/non-negative, highlights pass through, and the method
   beats a trivial baseline on aliasing-stress content. **Oracle = our own synthesised
   ground truth + an independent bilinear reference.** *This is the only axis the sibling's
   `tests/test_rcd_demosaic.py` currently covers* (§3).

2. **Axis-D2 — World-class quality ("is it actually good").**
   Reconstruction error vs **true full-RGB ground truth**, measured as CPSNR + S-CIELAB ΔE,
   compared against **published numbers** for the SOTA classical and CNN demosaicers.
   **Oracle = true ground truth — *never another demosaicer*** (see "the trap" below).

3. **Axis-D3 — Artifact suppression ("does it fail gracefully").**
   The named failure modes — **zipper, false colour, moiré/maze, texture over-smoothing,
   resolution loss** — each measured by the metric the literature pairs with it, on
   targets designed to provoke it.

Plus a fourth axis the still-image literature will not give us:

4. **Axis-D4 — Temporal stability (timelapse-specific).** Demosaic artifacts that are static
   within a frame but **flicker frame-to-frame** under sub-pixel scene motion or the
   Holy-Grail exposure ramp. World-class-on-stills ≠ temporally stable. No external benchmark
   addresses this; it ties to the Phase-C `E_warp` gate ([pipeline-overhaul-plan.md](pipeline-overhaul-plan.md) §C).

### The trap (adversarial spine — Agent 4, confirmed by advisor)

> Benchmarking our output **against AMaZE/RCD/DCB output** measures **port fidelity** — "did
> we clone it" — whose ceiling *is the algorithm we cloned*. It says **nothing** about
> world-class. World-class is Axis-D2 only: error vs **true scene ground truth**, compared to
> the published field. **AMaZE, RCD and DCB have no peer-reviewed CPSNR** (Agent 4, confirmed
> by negative search) — they are software-project algorithms, never benchmarked against
> ground truth in an academic venue. So matching AMaZE's *output* cannot tell you whether
> AMaZE is world-class; only the Axis-D2 ground-truth protocol can. Port-fidelity-vs-binary-
> oracle is still useful — as a **regression test** (Axis-D1), kept strictly separate from
> the quality claim.

---

## 2. The external-dataset survey (Agent 1)

The universal benchmarking paradigm: take a full-colour image, **sub-sample it to a Bayer
CFA**, reconstruct, and compare to the "original" [Li, Gunturk, Zhang, *Image demosaicing: a
systematic survey*, Proc. SPIE 6822 (VCIP 2008), 68221J]. The quality of the "original" is
therefore the quality ceiling of the whole exercise.

| Dataset | Ground truth | Domain | Size | Public-repo licence | Fatal flaw for a raw-linear demosaic |
|---|---|---|---|---|---|
| **Kodak-24** (r0k.us) | re-mosaiced **film scan** | **8-bit sRGB** | 24 × 768×512 | ambiguous ("unrestricted" per maintainer, no formal grant) | wrong domain + circular GT; content "too smooth, low-saturation, high spectral correlation" |
| **McMaster/IMAX-18** (Zhang et al. 2011) | re-mosaiced **film scan** | **8-bit sRGB** | 18 × 500×500 (from 8 × 2310×1814) | no licence; citation-only; password-gated | wrong domain + circular GT (harder *chroma* content, same flaw) |
| **MSR / MDD** (Khashabi et al. 2014) | **real raw**, *down-sampled* reference | **linear, 16-bit** | 200/100/200; Bayer + X-Trans | **unverified** (likely MSR-LA, restricted) | reference is band-limited (down-sampled from the mosaic) → cannot verify native-resolution detail; licence unverified |
| **Gharbi/MIT** (SIGGRAPH Asia 2016) | re-mosaiced web JPEG (÷4) | **gamma sRGB** | 2.6 M patches (~80 GB) | code MIT; **data terms unstated** | wrong domain + circular GT (JPEG'd, unknown ISPs); excellent moiré stress only |
| **Waterloo (WED)** (Ma et al. 2017) | re-mosaiced pristine sRGB | display sRGB | 4,744 pristine | **NO — research-only, non-redistributable** | not a demosaicing set; circular; licence forbids check-in |
| **DIV2K / Urban100** | re-mosaiced SR sRGB | 8-bit sRGB | borrowed | research-only / academic | maximally circular (curated/super-resolved content) |

**Cross-source consensus (high):** (1) the simulation paradigm is universal; (2) Kodak is
unrepresentative — low saturation, high R-G-B spectral correlation, small chromatic gradients
[Zhang et al. 2011; Li et al. 2008; Tan/DMCNN 2018; Kokkinos & Lefkimmiatis 2018]; (3)
McMaster is harder than Kodak (no dissent found); (4) **MSR is the only domain-correct
(real-raw, linear) option**, and the field's own answer to the sRGB-domain critique
[Khashabi 2014]; (5) Gharbi/MIT is gamma-sRGB and re-mosaiced (Gharbi 2016 *and* Kokkinos
2018 agree).

**The disqualifier, stated once:** demosaicing runs on the **linear CFA mosaic, pre-WB,
pre-colour-matrix**. Five of the six sets are **display-encoded sRGB that has already been
through a demosaic + ISP**, so using them means either testing in the wrong domain (masking
exactly the linear-light interpolation behaviour we care about) or inverse-EOTF'ing a "truth"
that was never linear — and scoring our demosaic against *another demosaicer's* output. Only
**MSR** is in the right domain, and its reference is band-limited and its licence unverified.
**No public set verifies a world-class raw-linear demosaic on its own terms.**

---

## 3. What we already have — `tests/test_rcd_demosaic.py` (sibling chip)

The RCD clean-room is already partly landed: `src/lrt_cinema/_rcd_demosaic.py` exists, with a
**fixture-free correctness gate** in `tests/test_rcd_demosaic.py`. It is good work and the
right foundation. It synthesises ground-truth RGB in-code (slanted edge, radial-chirp zone
plate, luminance-correlated colour bars, smooth/natural texture, plus an adversarial
saturated anti-correlated bar set), mosaics to a CFA, reconstructs, and scores.

**What it covers (Axis-D1, solidly):**
- Flat-patch bit-exact interior reconstruction, parametrised over all four Bayer phases.
- Per-phase PSNR consistency (smoking-gun guard for a flip/transpose/R↔B-swap bug).
- Finiteness / non-negativity / dtype preservation / input validation.
- Highlight pass-through (no [0,1] crush — preserves headroom for downstream recovery).
- A **relative** quality bar: RCD must beat an independent inline bilinear by a per-image dB
  floor on band-limited, OLPF'd, luminance-correlated content.

**What it does NOT cover — the gaps this plan fills:**

| Gap | Why it matters | Axis |
|---|---|---|
| **Bar is "beats bilinear"** (~+4 dB on an edge) | Bilinear is the *floor* (~32.9 dB Kodak). "+4 dB over bilinear" is consistent with a merely *mediocre* (AHD-grade) demosaicer. **The world-class claim is unfalsifiable** without a SOTA-anchored bar. | D2 |
| **Metric = PSNR only** | PSNR is colour-pooling-blind, **rewards blur**, and is near-blind to localized **zipper** and to **false colour** (a pure-chroma edge artifact barely moves whole-image PSNR). | D2/D3 |
| **No false-colour / zipper / texture metric** | The named failure modes are invisible to the current harness. | D3 |
| **No true-GT natural scene** | Synthetic-only; no contact with the published Kodak/McMaster bar. | D2 |
| **Not in the camera-raw-linear pipeline domain** | Tests pure interpolation on [0,1] RGB; SPIE 7876 (§4) shows the demosaic ranking *changes* between sRGB and camera-raw domains. | D2 |
| **No temporal leg** | Timelapse flicker of artifacts is unmeasured. | D4 |

> **Coordination:** the plan below **adds new files and augments**, never rewrites, the
> sibling's harness. `_rcd_demosaic.py` and `test_rcd_demosaic.py` stay owned by the demosaic
> chip; fixtures/metrics land in new modules (§8).

---

## 4. The true-ground-truth routes (Agent 2)

To escape circularity you need a full-resolution 3-channel image with **no prior demosaicing**.
Three routes exist:

**(a) Analytic synthetic charts — fully ownable, true GT by construction.** Zone plate,
slanted edge, dead-leaves, Siemens star, colour edges: rendered directly as known 3-channel
linear RGB at arbitrary resolution and frequency, then mosaiced. No licence, no SSF, no
band-limit we did not choose. The cost: not "natural" content. **This is the spine of the
plan** (§8) and the only route that gives true GT *and* full above-Nyquist frequency content
(every band-limited route under-stresses the aliasing regime where directional demosaicers
earn their keep).

**(b) Hyperspectral × camera SSF — honest but easy.** Integrate
`reflectance × illuminant × SSF` → camera-raw linear RGB → mosaic [the ISET formulation:
Farrell, Catrysse, Wandell, *Digital camera simulation*, Applied Optics 51(4):A80, 2012].
Datasets: **CAVE** (32 scenes, 400–700 nm/10 nm/31 bands, 512²; Yasuma et al., IEEE TIP 2010),
**ICVL** (200 imgs, CC BY-NC-ND 4.0; Arad & Ben-Shahar, ECCV 2016), **Harvard**
(Chakrabarti & Zickler, CVPR 2011), **TokyoTech** (Monno et al.). The methodology is
**peer-accepted** — and one paper validates our exact constraint: *Evaluation of a
Hyperspectral Image Database for Demosaicking Purposes*, Proc. SPIE 7876, DOI
10.1117/12.876764, shows demosaic should be evaluated on **camera-raw linear values**, and
that the algorithm *ranking changes* between the raw and rendered-sRGB domains. **But** the
cubes are spatially **band-limited by the capture instrument** (LCTF / tunable filter /
push-broom), so re-mosaicing them produces *less* aliasing than a real Bayer sensor → an
**honest but easy** test. Complementary to (a), not a replacement.

**(c) Physically-based render (ISET3d + PBRT).** Ray-trace a spectral 3D scene, render the
un-mosaiced full-RGB sensor image *and* its mosaic from the same scene with a chosen optical
MTF + CFA + SSF [Liu et al., IS&T EI 2019; foundation Farrell et al. 2012]. The only route
that escapes circularity *and* the instrument-MTF gap. Capability is real; standing it up is
heavy; not the community standard. A stretch goal, not a near-term dependency.

**D750 SSF — not published anywhere (Agent 2, verified locally).** `camspec` (Jiang et al.,
WACV 2013) ships D3/D3X/D40/D50/D200/D300s/D5100/D700/D80/D90 — **no D750** (it shipped late
2014, after the set). Confirmed in this repo: `colour.MSDS_CAMERA_SENSITIVITIES` contains
exactly `['Nikon 5100 (NPL)']` — D5100 present, D750 absent. **This justifies the repo's
existing D5100 substitution** (`tests/synthetic_chart.py`). For demosaic *spatial* quality
and ranking the substitution is sound (demosaic exploits spatial channel-correlation + CFA
geometry, not the absolute SSF); absolute colour accuracy is Axis-2's job, not the demosaic's.

---

## 5. Metrics & targets (Agent 3) — the battery, primary-sourced

**Full-reference (need GT):**

- **CPSNR** = `10·log10(MAX² / CMSE)`, `CMSE = mean over {R,G,B}×pixels of (ref−test)²`
  (a *single* pooled colour MSE, not three per-channel PSNRs). MAX = 1.0 (float) or 255
  (8-bit). **Border crop is NOT standardised** — survey/CNNCDM/ARI use **10 px**; others 6
  or 0. *Always report the crop and the dataset, or numbers are not comparable.* Blind spot:
  **rewards blur** (over-smoothing lowers squared error while destroying detail) and is
  near-blind to localized zipper.
- **S-CIELAB ΔE** [Zhang & Wandell, *A spatial extension of CIELAB for digital color-image
  reproduction*, J. SID 5(1):61–63, 1997]. Opponent transform (1 luminance + 2 chroma) →
  per-channel Gaussian spatial filter scaled to that channel's contrast sensitivity (chroma
  channels low-pass relative to luminance) → back to XYZ → CIELAB → ΔE pixelwise → mean. The
  **correct false-colour metric**: it down-weights fine high-frequency colour error the eye
  cannot resolve, and flags broad/strong casts. **Viewing-distance-dependent** — fix and
  report samples-per-degree.
- **SSIM / MS-SSIM** [Wang et al., IEEE TIP 13(4):600–612, 2004; Wang et al., Asilomar 2003].
  **Standard SSIM is luminance-only → structurally near-blind to false colour.** *Do not use
  luma-SSIM as a false-colour gate* (the sharpest "SSIM misleads" case for demosaicing).
- **Zipper-effect % + region-split** [Lu & Tan, *Color Filter Array Demosaicking: New Method
  and Performance Measures*, IEEE TIP 12(10):1194–1210, 2003]. Zipper: for each reference
  pixel find its nearest-neighbour-in-Lab in a local window; if the demosaiced P↔Pn colour
  difference deviates from the reference difference beyond a threshold, count the pixel;
  report the %. Region-split: report CPSNR + ΔE **separately on edge vs smooth regions**
  (artifacts concentrate at edges; whole-image means hide them).
- **"FCIR" (False Color Interpolation Ratio) DOES NOT EXIST** as a defined, citable metric
  (Agent 3 negative search). The real false-colour handles are edge-region ΔE (Lu & Tan) and
  the chroma component of S-CIELAB. *Treat any "FCIR formula" elsewhere with suspicion.*

**No-reference / chart-based (real raw, no GT):**

- **ISO 12233 slanted-edge e-SFR → MTF** [ISO 12233:2023 (cat. 79169) / :2024 (cat. 88626)].
  Super-resolved ESF → differentiate to LSF → FFT → MTF. **Report MTF50P (50 % of *peak*),
  not MTF50** — MTF50 *rewards sharpening*; MTF50P is sharpening-robust [Imatest]. Edge slant
  ~5°, must be >±2° from 0/45/90°. Blind spots: high-contrast stimulus does **not** predict
  fine-texture rendering; says nothing about colour/aliasing.
- **Dead-leaves ("spilled coins") texture MTF / acutance** [Cao, Guichard, Hornung, *Dead
  leaves model for measuring texture quality on a digital camera*, Proc. SPIE 7537, 75370E,
  2010; McElvain & Gish, Proc. SPIE 7537, 2010; Kirk et al. (Image Engineering) ~2014;
  standardised in **ISO/TS 19567-2:2019**]. Catches **demosaic+NR over-smoothing of
  low-contrast fine texture** that the slanted edge misses. **Methodological split:** the
  *direct* (DXOMARK) estimator is noise-biased (noise masquerades as preserved texture and
  inflates the score); the *cross-correlation / intrinsic* estimator (Kirk; Imatest;
  ISO/TS 19567-2) is noise-robust and full-reference. **For a synthetic chart we *know* the
  exact target, so use the cross-correlation estimator** (it becomes truly full-reference).
- **Siemens star (s-SFR)** [ISO 12233; ISO/TS 19567-1:2016] — frequency sweep, aliasing onset,
  harder to game with sharpening than the edge.
- **Zone plate / hyperbolic wedge** — the best **moiré / false-colour visualiser**; *not
  quantitative* [Imatest]. Bayer colour aliasing is special: R/B Nyquist is half the sensor's,
  and aliasing is **strongly demosaic-dependent** (bilinear = severe) — i.e. the demosaicer is
  the dominant lever on colour moiré, which is exactly the world-class differentiator.

**Conclusion: use a battery, never one number.** No single metric captures moiré *appearance*
or integrated false-colour *appearance*; PSNR rewards blur; luma-SSIM misses colour.

---

## 6. The world-class bar (Agent 4) — published numbers make the claim falsifiable

Cross-paper dB are only **loosely** comparable (sRGB vs linRGB; Kodak-12 vs Kodak-24 subset;
border crop; Bayer phase) — the same classical method spreads **±1.5–2 dB** across papers.
The cleanest within-protocol anchor is **CNNCDM Table 1** [Xu/Zhang et al., *Color Image
Demosaicking via Deep Residual Learning*, IEEE ICME 2017] (sRGB, 10-px border, Kodak-24 +
McMaster-18, column = CPSNR):

| Method | Kodak CPSNR | McMaster CPSNR |
|---|---|---|
| AHD | 37.96 | 34.62 |
| GBTF | 40.62 | 34.38 |
| DLMMSE (Zhang-Wu 2005) | 40.11 | 34.47 |
| LDI-NAT (Zhang 2011) | 37.69 | 36.20 |
| RI (Kiku, ICIP 2013) | 38.56 | 36.48 |
| MLRI (Kiku, SPIE 2014) | 40.86 | 36.77 |
| ARI (Monno, ICIP 2015) | 39.79 | 37.52 |
| **CNN (CNNCDM)** | **42.04** | **38.98** |

Corroboration: Monno et al. (*Sensors* 17(12):2787, 2017, Kodak-12) gives ARI 41.47 / 37.60;
the 2008 survey adds **S-CIELAB** (Kodak best ≈ **0.66**, IMAX best ≈ **1.4–1.5**); bilinear
floor ≈ **32.9 / 32.5** (linRGB) [Kokkinos & Lefkimmiatis 2018]; modern CNNs reach **~41–42
Kodak / ~39 McMaster** linRGB [Gharbi 2016 via Kokkinos 2018].

> **The falsifiable world-class bar.** Read it **per dataset** — the leaderboard reorders
> between the easy (Kodak) and hard, high-chroma (McMaster) sets, and **McMaster is the one
> that matters for real cameras**. To call a *classical* demosaicer competitive: clear
> **≈40.5–42 CPSNR on Kodak AND ≈36.5–37.6 on McMaster** (and S-CIELAB ≈0.66 / ≈1.4–1.5). To
> reach the deep-learning tier: **≈42 / ≈39**. A fixture set that cannot *falsify* the claim
> cannot *guarantee* it — this table is what makes "world-class" a testable proposition.

**Algorithm provenance + licence (Agent 4, verified at source):** AMaZE (Emil Martinec) =
**GPLv3**; RCD (Luis Sanz Rodríguez) = **GPLv3** (both the original repo *and* the RawTherapee
port — an early "MIT" claim was wrong); DCB (**Jacek Gozdz**, not "Górny") = **3-clause BSD**.
None have a peer-reviewed CPSNR. **Repo-doc bug:** [pipeline-overhaul-plan.md](pipeline-overhaul-plan.md)
line 16 calls DCB "LGPL" — DCB's *file* is BSD-3 (LGPL is the LibRaw *library*); this
*loosens* the clean-room constraint (DCB source is readable with attribution; AMaZE/RCD stay
black-box). Fix that line.

---

## 7. Verdict — why (b) holds even with the sibling's harness

Synthesising the four passes (each cross-referenced and adversarially checked):

1. **No external set is domain-correct + non-circular + native-resolution + redistributable.**
   Five public sets are circular display-sRGB; MSR is the lone real-raw/linear one but is
   band-limited and licence-unverified (§2). The published *numbers* are usable as a bar, but
   the *images* cannot certify our linear pipeline.
2. **The existing harness certifies Axis-D1 only.** "Beats bilinear by N dB on PSNR" cannot
   distinguish world-class from mediocre, and is blind to false colour, zipper and texture
   loss (§3). The world-class claim is presently **unfalsifiable**.
3. **The trap makes a shortcut impossible.** AMaZE/RCD/DCB have no published quality number,
   so cloning them buys only port fidelity; quality must be earned against true GT, by us
   (§1, §6).
4. **"Exhaustive" is itself unachievable literally** — no finite fixture set proves
   world-class for all inputs. The achievable, defensible target is **full documented-failure-
   mode coverage with falsifiable, primary-sourced thresholds**, which is what §8 builds.

Therefore: build our own layered battery, ingest the external sets only where each is valid,
and pin every threshold to a citation.

---

## 8. THE PLAN — a layered, primary-sourced fixture battery

Design rules: **true GT or it does not count toward Axis-D2**; **camera-raw-linear domain via
the real pipeline** for the quality leg (SPIE 7876); **a metric battery, never one number**;
**every threshold cites a primary**; **saturated colour is first-class** (CLAUDE.md: "neutrals
passing ≠ correct"); **extend, never rewrite, the sibling's harness**.

### Layer A — Analytic synthetic charts (true GT, fully ownable) → Axes D1, D3

New module `tests/demosaic_fixtures.py` (chart generators, returning float64 linear RGB +
their analytic GT) and `tests/test_demosaic_quality.py` (metrics + thresholds). Each chart is
rendered, optionally OLPF'd, mosaiced via the existing `_PHASE_CHANNEL` map, reconstructed,
scored. Charts:

| Chart | Provokes | Metric (cite) | Threshold source |
|---|---|---|---|
| **Slanted edge** (~5°, both neutral *and* saturated-colour edges) | resolution, zipper, false colour at edges | **MTF50P** (ISO 12233); edge-region ΔE + zipper% (Lu & Tan) | relative-to-bilinear + absolute MTF50P floor |
| **Radial zone plate** (hard chirp, neutral + tinted) | aliasing, moiré, colour moiré | aliasing-onset radius; **chroma energy where GT-chroma≈0** = false-colour map; S-CIELAB | beats-bilinear (sibling has this on PSNR) + add chroma-energy ceiling |
| **Dead-leaves** (r⁻³ disks, grey + chromatic) | **texture over-smoothing** | **texture acutance, cross-correlation estimator** (ISO/TS 19567-2; Kirk; Cao 2010) | acutance ≥ bilinear and ≥ a fixed floor at stated viewing condition |
| **Siemens star** | s-SFR, aliasing onset | s-SFR (ISO 12233) | absolute + relative |
| **Saturated isoluminant colour edge** | the documented failure boundary | edge ΔE, zipper% | *characterisation, not pass/fail* (mirrors sibling's `_saturated_bars` honesty) |

Layer A is the spine: true GT by construction, arbitrary resolution/frequency, no licence, no
SSF dependency, and it carries the chart-based metrics (MTF, acutance, aliasing onset) the
synthetic-RGB harness cannot.

### Layer B — Camera-raw-linear pipeline fixtures (true GT, real domain) → Axis D2

Extend `tests/synthetic_dng.py`. Today its `build_cfa` paints **flat** patches into an
uncompressed D750 DNG clone (RGGB, honouring Black/WhiteLevel). Add a **spatial** path:
write a Layer-A chart (or a hyperspectral-rendered scene, Layer C) into the mosaic instead of
flat patches, so the chart flows through the **real pipeline at the correct linear tap** —
the demosaic operating on camera-raw linear values, pre-WB, pre-matrix (SPIE 7876). This is
where Axis-D2 quality is measured *in our actual domain*, not on detached [0,1] RGB. Requires
a real D750 DNG to clone (present at `/tmp/dng_out/DSC_4053.dng`) + `dnglab` (skip-gate when
absent, like the existing harness).

### Layer C — True-GT natural scenes (non-circular realism) → Axis D2

`tools/demosaic_bench/` (offline, not in the unit suite). Render **CAVE or ICVL**
hyperspectral cubes through the **D5100 SSF** (reuse the `reflectance×illuminant×SSF`
integration already in `tests/synthetic_chart.py`), stopping at the three linear channels,
then mosaic → reconstruct → CPSNR + S-CIELAB. Non-circular, linear-domain, realistic content.
Honest-but-easy (band-limited, §4) → realism complement to Layer A's hard aliasing, not a
substitute. Licence: ICVL CC BY-NC-ND (cite, don't redistribute); CAVE research-use.

### Layer D — Published-bar competitiveness (falsifiability) → Axis D2

`tools/demosaic_bench/` also runs **Kodak-24 + McMaster-18** in the **published sRGB protocol**
(10-px border, full counts) → CPSNR + S-CIELAB, compared to the §6 table. Caveats baked in:
this is the *sRGB-domain* protocol (matches the literature, **not** our linear production
path), so it is a **competitiveness sanity check**, not the production verification (which is
Layers A/B/C). Datasets **downloaded on demand, not checked in** (licences §2); skip-gate when
absent.

### Layer E — Port-fidelity regression (Axis D1, strictly separate from D2)

Optional. `our_DCB` vs **libraw `dcraw_emu`** and `our_RCD` vs **`rawtherapee-cli`** as
**binary oracles** (binary use ≠ GPL contamination — clean-room-safe). A regression tripwire
("our DCB still matches libraw DCB"), **never** a quality measure. Extracting demosaic-only
*linear* output from full-pipeline tools is fiddly (Agent 4) — document the exact flags.

### Layer F — Temporal stability (timelapse) → Axis D4

`tests/test_demosaic_temporal.py`. Translate a Layer-A chart by sub-pixel increments (and/or
ramp illumination), demosaic each frame, and measure the **temporal variance of the artifact
metrics** in regions that should be constant after motion compensation (e.g. std of per-pixel
chroma across the sequence). No external metric exists → we define it and tie it to the
Phase-C `E_warp` gate. This is a genuine contribution the still-image literature cannot supply.

### Metric module

`src/lrt_cinema/metrics_demosaic.py` (or `tools/demosaic_bench/metrics.py`): CPSNR,
S-CIELAB ΔE (fixed, documented viewing condition), Lu & Tan zipper% + region-split, slanted-
edge SFR/MTF50P, dead-leaves cross-correlation acutance, zone-plate aliasing-onset + false-
colour chroma-energy map. Reuse `colour-science` for Lab/ΔE; the S-CIELAB front-end follows
the Zhang & Wandell reference implementation; **each function carries its primary citation in
the docstring** (project convention).

### Suggested phasing

1. **A + metric module** — biggest correctness/quality gain, zero external deps, no licence,
   runs in CI. Upgrades the sibling's PSNR-only bar to the full battery on true-GT charts.
2. **B** — wire charts through the real linear pipeline (needs the local DNG + dnglab).
3. **D** — published-bar competitiveness (offline; makes "world-class" falsifiable).
4. **C** — hyperspectral realism (offline; licence-aware).
5. **F** — temporal (ties to Phase-C `E_warp`).
6. **E** — port-fidelity regression (optional).

---

## 9. Adversarial review of *this plan*

- **"Synthetic charts aren't natural scenes."** True — Layer A is hard-but-unnatural; Layer C
  is natural-but-easy; Layer D is natural-but-circular-sRGB. **No single layer is sufficient;
  the battery is the argument.** Each covers another's blind spot; that is the design, and it
  is stated, not hidden.
- **"D5100 ≠ D750."** The D750 SSF is unpublished (§4); the substitution is sound for spatial
  quality/ranking (demosaic exploits spatial correlation + CFA geometry, not absolute SSF),
  and absolute colour is Axis-2's job. Flagged as a reasoning-based call (Agent 2, med-high),
  not a cited certainty.
- **"Layer D's sRGB protocol isn't our domain."** Correct, and *that is why it is labelled a
  competitiveness check, not the production verification.* The production claim rests on
  Layers A/B/C (linear, true GT). Conflating them would be the §1 trap in a new guise.
- **"Beating bilinear is too weak"** — exactly why Layer D anchors to the §6 SOTA table and
  Layer A adds absolute MTF50P/acutance floors, not just relative-to-bilinear gates.
- **Unpinned thresholds** (Lu & Tan zipper ΔE constant; per-edition ISO 12233 wording;
  S-CIELAB filter coefficients) — see §10; the plan uses relative + absolute-floor gates that
  do not depend on the exact unpinned constants until they are sourced.
- **"Exhaustive" is a promise we cannot literally keep** — restated as full documented-
  failure-mode coverage with falsifiable thresholds, which is the honest achievable target.

---

## 10. What could not be verified (carried from all four passes)

- **MSR/MDD exact licence** and **down-sampling factor** — page has no licence text; treat
  redistribution as restricted, band-limit severity as unquantified.
- **SPIE 7876 author line** — title/venue/DOI/methodology confirmed; authors not pinned
  (HTTP 429). Pin before formal citation.
- **Lu & Tan exact zipper threshold constant** (~2.3 ΔE cited secondhand) and the edge/smooth
  classifier operator+threshold — algorithm structure verified; numeric constants not (PDF
  mirrors 403/refused).
- **ISO 12233 per-edition attribution** (2023 #79169 vs 2024 #88626 both exist; which-
  supersedes and which edition introduced slanted-star/Tukey/5th-order fit not disambiguated).
- **CPSNR 10-px border / S-CIELAB filter coefficients** — textbook/ref-impl confirmed, not
  read from the survey primary (scanned PDF).
- **DCB acronym expansion** — no source formally defines it.
- **D750 SSF "absent everywhere"** — verified absent from camspec + this repo's colour-science;
  high but not exhaustive across all private/commercial measurements.
- **Cross-paper dB comparability** — Bayer phase rarely stated; sRGB-vs-linRGB inferred per
  paper; ±1.5–2 dB same-method spread is the comparability ceiling.

---

## 11. Primary sources

**Datasets:** Li, Gunturk, Zhang, *Image demosaicing: a systematic survey*, Proc. SPIE 6822
(VCIP 2008) 68221J · Zhang, Wu, Buades, Li, *Color demosaicking by local directional
interpolation and nonlocal adaptive thresholding*, J. Electronic Imaging 20(2):023016, 2011,
DOI 10.1117/1.3600632 · Khashabi, Nowozin, Jancsary, Fitzgibbon, *Joint Demosaicing and
Denoising via Learned Nonparametric Random Fields*, IEEE TIP 23(12):4968–4981, 2014, DOI
10.1109/TIP.2014.2359774 · Gharbi, Chaurasia, Paris, Durand, *Deep Joint Demosaicking and
Denoising*, ACM TOG 35(6):191, SIGGRAPH Asia 2016, DOI 10.1145/2980179.2982399 · Ma et al.,
*Waterloo Exploration Database*, IEEE TIP 26(2):1004–1016, 2017 · Kokkinos & Lefkimmiatis,
arXiv:1803.05215 / 1807.06403 · Tan et al. (DMCNN), arXiv:1802.03769 · Kodak suite, r0k.us.

**True-GT / simulation:** Farrell, Catrysse, Wandell, *Digital camera simulation*, Applied
Optics 51(4):A80, 2012 · *Evaluation of a Hyperspectral Image Database for Demosaicking
Purposes*, Proc. SPIE 7876, DOI 10.1117/12.876764 · Yasuma, Mitsunaga, Iso, Nayar
(CAVE), IEEE TIP 19(9):2241–2253, 2010 · Chakrabarti & Zickler (Harvard), CVPR 2011 · Arad
& Ben-Shahar (ICVL), ECCV 2016 · Monno et al. (TokyoTech) · Jiang et al. (camspec),
*What is the space of spectral sensitivity functions for digital color cameras?*, IEEE WACV
2013 · Liu et al. (ISET3d), IS&T Electronic Imaging 2019.

**Metrics / targets:** Zhang & Wandell, *A spatial extension of CIELAB for digital color-image
reproduction*, J. SID 5(1):61–63, 1997 · Wang, Bovik, Sheikh, Simoncelli (SSIM), IEEE TIP
13(4):600–612, 2004 · Wang, Simoncelli, Bovik (MS-SSIM), Asilomar 2003 · Lu & Tan, *Color
Filter Array Demosaicking: New Method and Performance Measures*, IEEE TIP 12(10):1194–1210,
2003 · ISO 12233:2023 (cat. 79169) / :2024 (cat. 88626) · Cao, Guichard, Hornung, *Dead
leaves model for measuring texture quality on a digital camera*, Proc. SPIE 7537, 75370E,
2010, DOI 10.1117/12.838902 · McElvain & Gish, Proc. SPIE 7537, 2010 · Kirk et al. (Image
Engineering), ~2014 · ISO/TS 19567-1:2016, ISO/TS 19567-2:2019.

**Performance / provenance:** Xu/Zhang et al. (CNNCDM), *Color Image Demosaicking via Deep
Residual Learning*, IEEE ICME 2017 · Monno, Kiku, Tanaka, Okutomi (ARI), *Sensors*
17(12):2787, 2017 · Kiku et al. (RI), IEEE ICIP 2013 · Kiku et al. (MLRI), Proc. SPIE/IS&T EI
9023, 2014 · Monno et al. (ARI), IEEE ICIP 2015 · Zhang & Wu (DLMMSE), IEEE TIP
14(12):2167–2178, 2005 · Getreuer (DLMMSE reproduction), Image Processing On Line, 2011 ·
RawTherapee/LibRaw source headers (AMaZE GPLv3, RCD GPLv3, DCB BSD-3 / Jacek Gozdz).
