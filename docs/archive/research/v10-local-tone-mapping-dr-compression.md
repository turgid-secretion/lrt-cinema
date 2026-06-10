# Open local tone-mapping / DR-compression for the PERCEPTUAL master: which method, and why

**Status:** Research scoping, 2026-05-31. Companion to
[v09-perceptual-grading-frontier.md](v09-perceptual-grading-frontier.md) (the §1
"measurable-better" house style this mirrors) and
[v09-dualmode-impl-plan.md](v09-dualmode-impl-plan.md) (the five cross-cutting
contracts + PR-A/B/C sequencing this slots into). Feeds a proposed amendment to
[DECISIONS.md](../DECISIONS.md) §5 and a new step in §7.

> **Provenance.** Synthesised by the `v10-local-tonemap-dr-compression` workflow
> (2026-05-31): five candidate-method agents + one Door-B feasibility agent →
> adversarial verify (dynamism claim, temporal-flicker risk, real open impl,
> **citation honesty**, pipeline fit) → synthesis. Verifier corrections are
> folded in (a fabricated `cv2.createTonemapFattal` and a 404'd OpenCV URL were
> struck; all academic primaries verified, primary PDFs read this session).
> Repo line anchors are **indicative** — relocate symbols by name. Feeds a
> proposed DECISIONS.md §5/§7 amendment (§5/§5b below); **not itself binding**.

**Strategic question.** The headline feature of the **PERCEPTUAL** render-intent
(the scene-linear ACEScg EXR master) is to **surgically compress a large dynamic
range while RETAINING the image's dynamism** — its local / perceived contrast.
DECISIONS.md §5 dropped Adobe's closed-source `Highlights`/`Shadows`/`Whites` as
un-replicable; that decision is being **reopened for the perceptual path only**,
because the perceptual master does **not** owe Adobe fidelity — it can ship a
*measurably better, open* operator. This doc picks that operator from five
adversarially-verified candidates.

**Framing this doc does not violate (carried from the task):**
- **General-purpose first.** Every method is judged for general scenes. Day/night
  (extreme-DR) timelapse is **one** demanding use case, not the target to overfit.
  Day/night-specific optimisation is quarantined in §3b as **optional config-flag**
  candidates, promoted to the core *only* if it benefits the majority.
- **"Better" is the MEASURABLE set only:** DR-compression strength, dynamism /
  local-contrast retention, halo / gradient-reversal artifacts, and — first-class
  for a sequence tool — **temporal coherence** (a per-frame local operator whose
  adaptation drifts frame-to-frame *flickers* across a clip; the HDR-*still*
  literature ignores this; it is load-bearing for timelapse and worst under
  day/night brightness sweeps). **Aesthetic preference is out of scope** — it
  needs an observer panel we do not have (§6).

**Evidence tags** (mirroring the frontier doc): **[std]** official standard ·
**[paper]** peer-reviewed / archival with authors-year-venue · **[lib]** canonical
library/source · **[claim]** secondary, not primary-verified · **[repo]** verified
against this repo's source/docs this session.

---

## TL;DR (the binding recommendations)

1. **The op is the base-attenuation MODE of a SINGLE shared edge-aware
   base/detail engine — build once, get two.** DR-compression and Texture/Clarity
   (DECISIONS.md §7 step 4 / [v09-dualmode-impl-plan.md] Step 4 / PR-C) are the
   *same* operation at different layers: split luminance into a smooth **base** +
   a **detail** layer, then either **attenuate the base** (DR-compression) or
   **boost the detail** (Texture/Clarity). Do **not** build a second engine for
   DR-compression. Three of the five assessments call this architecture-load-bearing
   and they are right.

2. **Method = Local Laplacian filters (Aubry et al. 2014 fast variant) as the
   QUALITY primary; guided filter (He–Sun–Tang 2013) as the lightweight first cut
   already sequenced in PR-C.** Local Laplacian is the only `primary-candidate` in
   the set, the only **provably halo-free** member, and the only one rated
   `temporal_risk: low`. The non-obvious payoff: DR-compression wants a **large**
   base radius — exactly the regime where the guided filter's halos grow (its own
   honest caveat + He et al.) and where Texture/Clarity, which runs at *small*
   radius, never goes. **So the DR role is what justifies / drives the
   local-Laplacian upgrade**, more than Texture/Clarity does.

3. **The central open problem applies to EVERY candidate, and is not yet solved:
   the scene-referred reformulation.** Every method reviewed — Durand, Fattal,
   Mertens, *and even Local Laplacian's percentile-renorm tail* — is
   **display-referred as published** (it normalises to a fixed display contrast /
   `[0,1]` ceiling). The PERCEPTUAL path is **scene-referred**: linear
   ProPhoto(D50), overrange `>1` preserved, **no display clamp** (PIPELINE.md
   Stage 7/11; `develop_ops.apply_exposure_2012` "we do not clamp",
   `develop_ops.py:44-53` [repo]). The op only ports correctly after we **discard
   the display tail** and replace the content-adaptive normalisation with a
   **fixed compression law around a fixed scene-linear log anchor** (e.g. 0.18),
   no ceiling. This is the CLAUDE.md §0 trap (neutrals pass, overrange/saturated
   fail) and is the single biggest piece of genuinely-open work (§6).

4. **Temporal coherence is FREE in the general core, and the "anchor-EMA in core"
   claim is resolved against.** The two relevant assessments appear to conflict
   (guided-filter: "anchor-EMA belongs in the general core"; local-Laplacian:
   "fixed everything → coherence is free, EMA is day/night-only"). The conflict is
   an artifact: the guided-filter "EMA in core" position assumes you keep Durand's
   **content-adaptive** compression law (`base_max−base_min` recomputed per frame).
   The scene-referred reformulation in (3) — which we need *anyway* — uses a
   **fixed** law + **fixed** anchor, removing the per-frame global statistic for
   **both** kernels. **Correct general position: the core computes NO per-frame
   global statistic, so output coherence = input coherence (which LRT already
   deflickered) → temporal coherence is free.** Adaptive renorm + its *mandatory*
   temporal EMA is a **day/night FLAG only** (§3b), not core.

5. **Bilateral (Durand–Dorsey 2002) is the conceptual parent, not the engine.** It
   *defines* the base/detail formulation but its kernel is dominated on the exact
   axis we care most about — He et al. (TPAMI 2013, PDF text extracted this
   session) state bilateral gradient reversal "is inherent and cannot be safely
   avoided by tuning parameters" [paper]. **Do not build a second, bilateral
   engine.** Implement DR-compression as the base-attenuation mode of the
   guided/local-Laplacian engine.

6. **Mertens exposure fusion is disqualified for this slot** — display-referred *by
   construction* (well-exposedness is a Gaussian on `0.5` over `[0,1]`; output is a
   `[0,1]` LDR image). Baking it into the scene-linear master = Resolve's ODT
   tone-maps it **again** (the double-tone-map failure). No legitimate
   display-referred target exists on the perceptual path today (§3b, §6).

7. **Door B (fit Adobe's `Highlights`/`Shadows` for the FAITHFUL/TIFF path) =
   not-recommended.** The shipping sliders are spatially adaptive
   (local-Laplacian-*class*); they are **unidentifiable** from the flat-patch
   grading-sweep harness, §7's faithful-path policy **forbids** a speculative
   working-domain change, and an independently-fitted local operator carries a
   *different* temporal-flicker signature than Adobe's closed one — defeating the
   faithful path's only purpose (§4). The capability users want lives on the
   **perceptual** path via the shared engine, **not** on faithful.

---

## §1 — How "compress range while retaining dynamism" is measured (+ temporal coherence)

A DR-compression op is "better" if, holding the *intent* (target range reduction)
fixed, it (i) achieves the **range reduction** asked, (ii) **preserves or amplifies
local contrast** while doing so, (iii) introduces **no halos / gradient reversals**,
and — first-class for a sequence tool — (iv) **does not flicker** when the scene
drifts frame-to-frame. Each is measurable without an observer panel.

### 1.1 Dynamic-range-compression strength

The thing being compressed is the **large-scale (base) luminance ratio**, in stops.
Measure on a single frame:

- **Achieved range ratio** — `log2(P_hi/P_lo)` of a robust luminance min/max
  (e.g. 0.5th / 99.5th percentile) **before** vs **after** the op. The op should
  hit a *commanded* base-range reduction (e.g. 12 stops → 4 stops) without a hard
  knee or display clip.
- **Monotonicity of the base map** — the base→base transfer must be monotone (no
  inversions); verify on a sorted luminance ramp.

NB the *kernel* (guided/bilateral/Laplacian-remap) does **no** range reduction by
itself — the compression is the **base-attenuation law** applied to the extracted
base. Any framing that says "the guided filter compresses range" is wrong; it
supplies the edge-aware base that the attenuation law then compresses.

### 1.2 Dynamism / local-contrast retention

The signature property. With the base compressed, the **detail layer is reinserted
at unity gain** (or boosted), so local micro-contrast survives the global crush.

- **Local-contrast retention ratio** — RMS of a band-pass (Laplacian) response in
  textured regions, output ÷ input. `≈1` = detail preserved; `>1` = boosted
  (the "dynamism" lever); `<1` = flattened (the failure of a global tone curve,
  which *must* steal contrast somewhere to bend the ends).
- **SSIM in flat / textured patches** (output vs input) — confirms detail was
  added without destroying structure or injecting ringing (frontier §1.4 template).
- **Per-scale control (Local Laplacian only)** — because detail (`α`) and edge
  (`β`) are remapped by *separate* functions on the same pass, you can compress the
  coarse component while independently preserving/boosting fine scales; the bilateral
  and gradient-domain methods give a single global detail scale only.

### 1.3 Halo / gradient-reversal artifacts (the axis that defines this op-family)

The signature failure of multi-scale contrast manipulation, and the axis on which
the candidates most differ.

- **1-D edge overshoot/undershoot** — drive a synthetic step edge through the op,
  measure the over/undershoot amplitude flanking the edge (the halo).
- **Gradient-sign-reversal count** — number of pixels whose output gradient sign
  flips relative to input (the bilateral filter's inherent failure, He et al.
  TPAMI 2013 [paper]).
- **Provable vs empirical:** Local Laplacian is *provably* halo-free (a local
  monotonic remapping rebuilt through a standard pyramid — Paris et al. 2011 §5
  "without degrading edges or introducing halos, even at extreme settings"
  [paper]). The guided filter is **gradient-reversal-free but NOT halo-free**, and
  its **halo width scales with the box radius `r`** — which is why the large-radius
  DR role stresses it more than the small-radius Texture/Clarity role.

### 1.4 Temporal coherence (first-class for us; absent from the HDR-still literature)

A per-frame local operator whose **adaptation** is content-driven will *flicker*
across a clip: as the scene's DR drifts (clouds, sun, day→night), a per-frame
**global statistic** (max-log-base, average-gradient, percentile renorm, display-fit
scale/shift) wanders, swinging the compression slope and/or offset frame-to-frame.
This is the axis the HDR-still papers ignore and the timelapse path cannot tolerate.

**Reproducible protocol (mirrors the frontier-doc linspace/UCS style):**

```
# render the SAME op with FIXED params across a real sequence
for f in frames:                       # a real day/night clip, or a brightness ramp
    out[f] = apply_op(frame[f], fixed_params)
Lmean   = [mean(luminance(out[f])) for f in frames]
Lmid    = [percentile(luminance(out[f]), 50) for f in frames]
# flicker = high-frequency component of a statistic that SHOULD track input smoothly
flicker = std(highpass(Lmean)) / mean(Lmean)      # ↓ = more temporally coherent
drift   = std(highpass(Lmid))                     # local-zone breathing
# decisive control: feed a slow linear brightness ramp (no real motion) and assert
# out is itself a slow ramp — any step / pump is operator-induced flicker, not scene.
```

Run on a slow brightness ramp (isolates operator flicker from scene change) **and**
a real day/night clip (the stress case). The two flicker sources to separate:
- **Global-statistic flicker (dominant, cheap):** the per-frame compression anchor
  jumps. **Removed entirely** by the fixed-anchor scene-referred law (§1.5 below,
  TL;DR 3-4) — no per-frame statistic, no flicker.
- **Local-decomposition drift (secondary, harder):** the base split itself can
  "breathe" near moving edges even with stable anchors. This is Aydın et al.
  (2014) / Boitard et al. (2014) territory — a **deferred** spatiotemporal upgrade,
  not solved by anchor stabilisation alone. Do not claim it is.

### 1.5 The cross-cutting constraint that decides everything: scene-referred vs display-referred

Every published TMO in this family was written to map an HDR scene onto a **display**
`[0,1]`. The PERCEPTUAL master is **scene-referred** — overrange `>1` is preserved
and there is **no display ceiling**. So before any method "fits", its
display-normalising tail must be replaced by a **scene-referred base-attenuation
law**: compress base ratios **toward a fixed log anchor** (e.g. scene-linear 0.18),
**no top clamp**, speculars stay `>1`. This is *not* a parameter tweak; the published
defaults are display-tuned (§6, open). It is **common to all candidates** — measure
every method *after* this reformulation, and validate on **saturated** colour, never
a grey wedge (a grey wedge is blind to the per-channel-vs-ratio reapply error and to
overrange clipping — CLAUDE.md §0).

### 1.6 Where only observer studies suffice (out of scope)

"Which compression *looks* most natural", the *magnitude* of an acceptable
local-contrast boost, "does this day→night transition feel right" — all
**preference** questions, gold-standard only via a psychophysical observer panel
under controlled viewing. **We have no panel.** We claim only the measurable axes
above and flag the rest as subjective, per the project's honesty discipline (§6).

---

## §2 — Ranked method comparison (verifier flags folded in)

All five candidates were adversarially verified this session. The verifier's
**must-fix** items (fabricated/unconfirmed API references, sign-flipped constants,
soft attributions) are folded into the table notes and struck where required —
**no candidate's academic primary citations failed**; the corrections are to
*secondary* claims and do not flip any verdict.

| # | Method | DR-compression | Dynamism retention | Halo / grad-reversal | Temporal | Open impl (license) | Compute/frame | Scene-referred fit | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| **1** | **Local Laplacian** (Paris 2011 / **Aubry 2014 fast**) | Strong, directly controllable (`β<1` edge compress, `α≤1` detail keep/boost), in log | **Best** — detail & edge remapped by *separate* functions, per-scale control | **Provably halo-free** (best-in-class) | **Low** — history-free pointwise + intra-frame `g0`; *no* per-frame global stat (display-tail discarded) | MIT refs (psalvaggio, KerouichaReda/fast_llf); clean-room numpy/scipy from 2 papers | Bounded/offline: fast variant ~8.4 s/frame single-core (2014 i7) → ~1 s/frame on modern multicore/GPU at 24 MP; **original ~1 min/MP (~24 min/frame) is disqualifying** → use fast variant | **Good** — discard percentile-renorm tail, fix scene-linear anchor | **primary-candidate** |
| **2** | **Guided filter base/detail** (He 2013 kernel + Durand–Dorsey framework) | Strong (Durand log base-scale on the guided base) | Strong, structurally explicit (detail at unity / boost) | **Gradient-reversal-free, NOT halo-free**; **halos grow with radius `r`** — bites the *large-radius* DR role | **Medium** — kernel deterministic; flicker only from the per-frame anchor (fix: fixed anchor, §1.5) + residual decomposition drift (Aydın, deferred) | **MIT** numpy/scipy refs (lisabug, tody411, pfchai); OpenCV `ximgproc` Apache-2.0; **~15 lines clean-room** | **O(N), independent of `r`** (He, TPAMI verbatim) — the standout budget property; **cheapest** | **Good** | **viable-alternative** (the lightweight first cut) |
| **3** | **Gradient-domain** (Fattal–Lischinski–Werman 2002) | **Strongest raw** (2415:1 → 7.5:1) — per-level gradient attenuation + Poisson solve | **Excellent** (monotone-in-gradient: small gradients survive) | Low halos (single-finest-level), but **non-conservative field → low-freq cupping/spilling**; desaturating `s`-exponent is a built-in colour compromise | **High** — TWO per-frame global stats (`α=0.1·avg‖∇‖` + mandatory display shift+scale); WRA05 needed a **3D Poisson** for time-coherence (airtight Fattal-specific proof) | GPL/unlicensed refs (pfstmo, LuminanceHDR) → **reference-only**; clean-room numpy/scipy (pyramid + sparse/DCT Poisson) | **Global Poisson every frame** — low-single-digit s/frame at 24 MP via DCT, but **cannot tile/parallelise** | **Tension** — display-fit must be removed; defaults are display-tuned (unproven) | **niche-or-flag-only** |
| **4** | **Exposure fusion** (Mertens 2007/2009) from virtual exposures | Strong but **indirect/bounded** (= EV-ladder spread); no monotone curve | Good local; but well-exposedness favours 0.5 → can flatten **global** contrast | Low halos; **Fig-6 cupping grows with ladder EV spread** (low-freq "breathing") | **Low** *if* ladder fixed (stateless, memoryless per-pixel) — but irrelevant given the slot mismatch | OpenCV `MergeMertens` Apache-2.0 (core photo); BSD-2 MATLAB; **~100 lines clean-room** | Moderate, scales with stack size `N` (keep `N=3`) | **POOR** — **display-referred BY CONSTRUCTION** (Gaussian on 0.5 over `[0,1]`); partial-strength is ill-defined; double-tone-map under Resolve ODT | **niche-or-flag-only** (mis-targeted, not flawed) |
| **5** | **Bilateral base/detail** (Durand–Dorsey 2002) | Strong but **display-normalised** (whole base → fixed contrast, e.g. 5) | **Signature strength** (detail unchanged) — but single global scale (no per-scale control) | **Inherent gradient reversal**, parameter-tuning cannot remove (He et al.) — **DOMINATED** by #1/#2 | **Worst-in-class** — content-adaptive per-frame compression slope *and* offset wander | OpenCV `xphoto::TonemapDurand` Apache-2.0 (URL **unconfirmed**, see note); **patent US7146059B1 EXPIRED 2025-02-08** ✓; clean-room numpy/scipy | Fast-bilateral/grid real-time-capable, but it's an **approximation** whose bins can flicker | Partial (same display-tail problem) | **viable-alternative** but **conceptual parent only — do NOT build a 2nd engine** |

**Verifier corrections folded in (the "citations did not verify" items):**

- **Fattal #3 — STRUCK: `cv2.createTonemapFattal` does not exist.** OpenCV's photo
  module ships exactly four tonemappers (Drago, Durand, Mantiuk, Reinhard) and has
  **never** shipped a Fattal operator [claim, verified against OpenCV docs +
  `tonemap.cpp`]. This was a fabricated API reference; it is a *citation fix*, not a
  verdict-flip — Fattal 2002 + Eilertsen 2017 are airtight and the clean-room route
  stands. Also: cite the single DOI `10.1145/566570.566573` (the assessment listed a
  duplicate). The §5.2 content-adaptive-flicker quote is sound *inference* applied to
  Fattal's two global statistics; the **load-bearing** temporal proof is the WRA05
  3D-Poisson entry, which is Fattal-specific and airtight.
- **Bilateral #5 — OpenCV `xphoto::TonemapDurand` URL 404'd this session** → treat
  as an **unconfirmed nice-to-have**, not a confirmed vendorable route. `open_impl`
  rests independently on (a) patent **US7146059B1 EXPIRED 2025-02-08** (confirmed
  twice, MIT-assigned, Durand & Dorsey) and (b) the algorithm being fully described
  in the verified paper → clean-room. GPL impls (HDR_Toolbox, pfstmo) are
  **reference-only** (do not vendor).
- **All five — academic primaries verified, none fabricated.** GPL/unlicensed
  reference impls are flagged reference-only throughout; the clean-room-from-paper
  route is the license-clean path for every method (matches the repo's clean-room
  ethos + the Axis-1 oracle contract 4, which must hand-roll independently anyway).

**Why #1 wins the slot over #2/#3, and why #4/#5 are out:**
- **#1 vs #2:** same engine family (this is the point, §3) — but at the **large base
  radius** DR-compression needs, #2's halos grow and #1 is provably halo-free, and
  #1 is the only `temporal_risk: low` member. #2 is the **right first cut** (cheap,
  O(N), already in PR-C) and the **fallback base producer**; #1 is the **quality
  ceiling** the DR role specifically justifies.
- **#3 (Fattal)** has the strongest raw compression and best dynamism, but: `High`
  temporal risk (two per-frame global stats, published fix is a heavier 3D Poisson),
  **low shared-engine overlap** (a second engine — gradient + Poisson solver —
  undercuts build-once), and a display-tuned calibration whose scene-referred
  re-derivation is unproven. Strong general DR-compressor in the abstract; wrong
  *architecture* for us.
- **#4 (Mertens)** is display-referred *by construction* — disqualified for a
  scene-referred core regardless of quality (TL;DR 6).
- **#5 (bilateral)** is dominated on the artifact axis and worst on temporal —
  conceptual parent only (TL;DR 5).

---

## §3 — RECOMMENDED GENERAL-PURPOSE architecture

**Method: Local Laplacian filters (Aubry et al. 2014 fast variant) as the quality
primary, implemented as the base-attenuation mode of the SHARED edge-aware
base/detail engine; guided filter (He 2013) as the lightweight first cut already
sequenced in PR-C and the cheap fallback base producer.** Chosen for the **majority**
of use cases (portraits, landscapes, architecture, any moderate-DR sequence), **not**
overfit to day/night (§3b).

### 3.1 Why this method (measurable axes only)
- **Dynamism retention (headline):** detail (`α`) and edge (`β`) are remapped by
  *separate* functions on one pass (Paris 2011 Eq.1-2, read verbatim) — compress the
  coarse component (`β<1`) while independently preserving/boosting fine scales
  (`α≤1`); per-level processing further targets which detail frequencies are touched.
  No single global curve forces a detail-vs-range trade.
- **Halos:** **provably halo-free** — the manipulation is a local monotonic remapping
  rebuilt through a standard Laplacian pyramid, *not* a coefficient rescale (which
  Paris identifies as the halo source). This is the axis that defines the op-family,
  and it is the one place the guided filter cannot match at large radius.
- **DR-compression:** `β` multiplicatively attenuates log-domain edge amplitude — it
  compresses **stops**, the correct domain. `σr` (default `log(2.5)`, a fixed
  intensity *ratio*, scene-DR-independent) trades off with `β`.
- **Temporal coherence: `Low`.** History-free, pointwise, anchored to absolute
  intensities + an *intra-frame* Gaussian reference `g0`; computes **no** per-frame
  global statistic once the display-tail is discarded (§3.3). Fixed params → two
  near-identical frames → near-identical output. (The fast variant's intensity
  discretization `{γj}` is C0-continuous in `g` under linear interpolation between
  bracketing pyramids → a region crossing a `γj` boundary under a slow ramp does
  **not** step/pump; it adds a static <30 dB error, not temporal flicker.)
- **Compute:** offline-acceptable. Fast variant ~350 ms/MP (2014 single-core i7) →
  ~8.4 s/frame at 24 MP single-core, dropping to ~1 s/frame or below on modern
  multicore / any GPU (GPU figures: 49 ms/MP, ~116 ms at 4 MP); lrt-cinema is a
  per-frame offline batch renderer, not real-time, so this clears budget either way.
  The **original ~1 min/MP variant (~24 min/frame at 24 MP) is disqualifying for
  sequences** — implement the **Aubry 2014 fast variant**, not the original.

### 3.2 The shared edge-aware engine (build once, get two)
This is the architecture-load-bearing point. Implement **one** base/detail engine; both
op-families consume it, differing only in **recombination**:

| | Engine call | Recombination | Radius regime | Halo stress |
|---|---|---|---|---|
| **Texture/Clarity** (PR-C) | base/detail split | `base + gain·detail` (boost detail) | **small** | negligible |
| **DR-compression** (this op) | base/detail split | `compress(base) + detail` (attenuate base in log) | **large** | **grows** → why local-Laplacian matters here |

- **PR-C ships the guided filter first** ([v09-dualmode-impl-plan.md] Step 4) — `~15`
  lines, O(N), the right lightweight base producer for the small-radius Texture role.
- **DR-compression is the consumer that justifies the local-Laplacian upgrade**,
  because its large base radius is exactly where guided-filter halos grow. Local
  Laplacian can be swapped in as the base producer for the DR op **without disturbing
  Texture/Clarity** (same engine seam, different kernel).
- **Critical caveat (do not overclaim):** shared engine ≠ equal fitness. The Texture
  use runs the kernel where halos are negligible; the DR use runs it where they grow.
  Same code, harder regime — the DR op **owns its own halo + temporal handling**.
- **Minor framing note for the impl plan:** [v09-dualmode-impl-plan.md] Step 4 ships
  guided-filter first and defers local Laplacian; the *quality* preference here is the
  inverse (local Laplacian is the ceiling). These are consistent — **sequence** guided
  first, **target** local Laplacian for the DR role. Do **not** implement the
  gradient-gated lookalike the impl-plan verifier already caught (`gain =
  max(0,1−|∇L|/σ)·L`): that *is* the naive sharpening local Laplacian exists to
  avoid. The real remapping function (Paris Eq.1-3) has **no gradient term**.

### 3.3 Pipeline placement (PERCEPTUAL branch only)
- **Working-space seam (contract 1):** ProPhoto(D50)-in / ProPhoto(D50)-out, like
  the OKLCh/CDL applicators. The ProPhoto(D50)→ACEScg(AP1) Bradford stays **downstream**
  in `output.py::_prophoto_to_linear` at Stage 13. The op never sees AP1.
- **Luminance + ratio colour reapply (CLAUDE.md §0):** compute a luminance channel
  via the repo's `_PROPHOTO_LUMINANCE` row (`develop_ops.py:407` [repo]) — **not** the
  paper's `(20R+40G+B)/61`. Compress luminance; reapply to RGB by the **out/in
  luminance RATIO** (`rgb_out = rgb_in · L_out/L_in`), **never** per-channel. Validate
  on **saturated** patches (a grey wedge is blind to the per-channel-vs-ratio error).
- **log(0):** scene-linear carries true zeros → the log step needs an epsilon offset
  (`log(x+eps)`) or a small floor, or it NaNs.
- **Order:** existing PERCEPTUAL chain, after ColorGrade, in the Texture/Clarity slot
  (replacing the `apply_sharpness` no-op position): ToneCurve → Sat → Vib → HSL →
  ColorGrade → **Texture/Clarity + DR-compression (shared engine)** → (Sharpness no-op).
- **Dispatch:** behind the existing `if intent is RenderIntent.PERCEPTUAL:` branch
  (`develop_ops.py:326-331` [repo]). Faithful path **untouched** (§4, §7 item 5).

### 3.4 The scene-referred base-attenuation law (the open derivation — TL;DR 3)
**Discard** Local Laplacian's published tone-mapping tail (Paris §5: per-frame
99.5/0.5-percentile robust min/max → fixed 100:1 → 1/2.2 gamma). That tail is the
*only* per-frame global statistic and the *only* display clamp; removing it is what
makes the op scene-referred **and** temporally coherent at once. Replace with:
- compress the **base** toward a **fixed scene-linear log anchor** (candidate: 0.18),
  with a **fixed** compression ratio per slider unit — **no top clamp**, speculars
  stay `>1`;
- exponentiate back to **scene-linear** anchored to that constant grey (compress
  around a constant anchor in log), keeping output overrange-preserving.

This law is **not yet derived** and is the central open work (§6). It is common to
*all* candidates (Durand's display-normalisation, Fattal's display-fit, Mertens'
`[0,1]` crush all need the same surgery). Needs a pinned module constant (the anchor)
and an Axis-1 oracle target.

### 3.5 Overrange + RGC handling (do NOT add a second clamp)
- **No inline clip** in the op. Floor at 0 only; **no display ceiling** (matches
  faithful `apply_hsl`'s `np.maximum(..., 0.0)` with no top clamp, `develop_ops.py:192`
  [repo]).
- Local-contrast boost / base compression can push pixels **out of AP1** → handled by
  the **single gated ACES RGC pass** in `output.py` (contract 2), operating on
  **negative-AP1** channels after the Bradford, **shared** with PR-A/B/C. Do **not**
  hand-roll gamut compression in the op, and do **not** trigger on overrange
  *brightness* (the impl-plan verifier's BLOCKER 2 — RGC acts on negative excursions
  in AP1, not bright pixels).

### 3.6 Byte-exact identity (ship-gate requirement)
**Explicit short-circuit returning the literal input array** — never numerical
round-trip. Pyramid build→collapse is **not** byte-exact at float32, and the fast
variant's interpolation adds further sub-30 dB error, so `α=1, β=1,
dr_compress=0` will **not** numerically reproduce the input. Gate on the IR
`is_identity()` (`texture==0 and clarity==0 and dr_compress==0`) and `return prophoto`
before any pyramid math, mirroring `HslBands.is_identity()` / `ColorGrade.is_identity()`
(`develop_ops.py:165,222` [repo]). The single gated RGC pass stays a no-op on in-gamut
data, so a zero-strength DR op under PERCEPTUAL is **bit-identical to faithful** —
preserving the gym 0.026 / rose 0.545 gate (contract 5).

### 3.7 Temporal-coherence strategy (general core)
**Core = fixed everything → temporal coherence is free.** Fixed `σr` (`=log(2.5)`),
fixed `α/β` driven by the slider, fixed scene-linear log anchor for the inverse map →
a pure deterministic per-frame function with **no** per-frame global statistic →
output coherence = input coherence (which LRT's upstream Exposure2012 EV-ramp /
deflicker already levelled). **This resolves the apparent guided-vs-local-Laplacian
conflict** (TL;DR 4): anchor-EMA is *not* needed in the core — it was only needed to
patch a *content-adaptive* law we are discarding anyway. The **residual** to watch:
local-decomposition drift near moving edges under extreme sweeps (Aydın 2014) — a
**deferred** upgrade (§3b/§6), not a core requirement, and **not** something
anchor-EMA fixes.

### 3.8 Effort / risk
- **Effort: L (multi-week)** for a correct clean-room Aubry-2014 fast variant +
  scene-linear-anchored log law + intensity/ratio colour + Axis-1 oracle (independent
  pyramid+remap reimpl) + a step-edge halo sensitivity leg. **But amortised:** the
  same engine discharges the deferred Texture/Clarity quality op, so it is
  **L-once-for-two**, not L-each. Guided-filter first cut is **M** and already
  scoped in PR-C. Risk-reducing scaffolding exists (PERCEPTUAL dispatch branch, the
  planned shared RGC pass, the 4-point playbook threading recipe).
- **Risk: Med-High (correctness), Low (integration).** Guard: (1) implement the
  **real** pointwise remapping, not a gradient-gated/coefficient-rescale lookalike
  (step-edge halo oracle leg); (2) **discard** the per-frame percentile renorm →
  fixed-anchor inverse map (else flicker); (3) `_PROPHOTO_LUMINANCE` + **ratio**
  colour + no inline clip (else saturated-colour corruption, CLAUDE.md §0); (4) sample
  `{γj}` every `σ` (else visible <30 dB error). Determinism/license: **clean**
  (deterministic, MIT refs, no learned/CNN component).

---

## §3b — Day/night-extreme-DR optimisations (QUARANTINED — optional config flags)

Day/night is **one** demanding use case, not the core target. Per §3.7 the core is
"fixed everything"; the levers below re-introduce a per-frame statistic and therefore
require temporal smoothing. **None belong in the general core** — each is an opt-in
flag, and the column states whether it could ever be promoted.

| Lever | What it does | Benefits the majority? | Recommendation |
|---|---|---|---|
| **Adaptive renorm / adaptive `σr`/`β`** to track the brightness sweep | re-introduces a per-frame statistic that follows the 24 h cycle | **No** — general scenes don't sweep; it *trades coherence for sweep-tracking* | **Flag only**, and **mandatory temporal EMA** over the adaptation parameter (else the exact flicker the still-literature ignores) |
| **Wider compression ratio / larger base radius** for the night→day range | more aggressive base crush for extreme DR | **No** — general scenes don't need `>` the default; larger radius costs more halo budget | **Flag only** |
| **Temporal EMA window** over the adaptive anchor | smooths the per-frame statistic across the sequence | **Only if** adaptive renorm is on (it has no job in a fixed-anchor core) | **Flag only**, coupled to the adaptive flag |
| **Couple the anchor track to LRT's emitted Exposure2012 EV-ramp** | the EV-ramp already encodes the brightness sweep — the cleanest day/night anchor signal (vs image-derived) | **Possibly** — if a user runs the adaptive flag, the EV-ramp is a cleaner anchor than image stats; but it has no role in the fixed-anchor core | **Flag only** (the cleanest *form* of the adaptive flag) |
| **Aydın et al. 2014 spatiotemporal base filter** (extend the base/detail split into the temporal domain along motion paths) | fixes residual **local-decomposition drift** under extreme sweeps (the §3.7/§1.4 secondary flicker) | **Marginally** — drift exists generally but is small at fixed anchors; the cost (multi-frame, motion-path) is high | **Deferred** (separate M+ piece); revisit only if measured drift on real footage warrants it |

**Day/night is NOT a clean win for any spatial-engine choice.** A method's adaptive
compression auto-scales to whatever DR the frame presents (attractive for the huge
swing) — but that same auto-adaptation is *precisely* what flickers worst across a
brightness sweep. So the day/night-specific lever worth a flag is a
**temporally-stabilised compression target**, which benefits **any** per-frame local
TMO and should live in a **shared, flag-gated temporal-stabilisation layer**, not in
choosing the spatial engine. Keep the core ("fixed everything") and the day/night
tuning (opt-in adaptive + mandatory EMA) cleanly separated.

---

## §4 — Door B: fit Adobe's Highlights/Shadows for the FAITHFUL/TIFF path?

**Verdict: NOT-RECOMMENDED.** Three independent disqualifiers, plus an honest error-floor.

**What the sliders actually are.** Primary-sourced (Paris/Hasinoff/Kautz 2011 +
Aubry 2014, Sylvain Paris @ Adobe) + Adobe-engineer corroboration (Eric Chan,
Luminous Landscape: Adobe "explored algorithms that used edges to modify various
tones", ~1 min/MP → "almost real time" — the exact 2011→2014 arc) + CreativePro
("The value of a shade changes faster as it approaches its border with another
shade"): the shipping `Highlights`/`Shadows`/`Whites` are **spatially adaptive /
edge-aware (local-Laplacian-CLASS)** — same input value maps to different output
depending on neighbours, plus content-aware auto-masking and multi-channel
blown-highlight reconstruction. **Honesty caveat (do not upgrade):** no fetched
source proves the shipping slider *is verbatim* LLF; the chain is strong
*circumstantial* — state it as "local-Laplacian-class", not "is LLF". An Adobe patent
search (assignee Adobe, inventors Paris/Chan, ~2011-14) could harden it but does not
change the verdict.

**Disqualifier 1 — unidentifiable from the existing harness.** The flat-patch
grading-sweep harness fits **per-pixel position-independent** functions (HSL/CDL band
centres, tint strengths — cleanly identifiable from flat patches). A **spatial**
operator silently degenerates to its pointwise response on flat patches → the part
that *distinguishes* these sliders (the local, edge-conditioned behaviour) is
**not identifiable** from that data. The globally-approximable component **is** just a
tone curve, and the pipeline already expresses one — so the easy-to-fit piece is
redundant and the distinguishing piece is unfittable. (Doc nit, non-load-bearing: the
existing `apply_tone_curve_pv2012` applies the *user's* ToneCurvePV2012, a separate LR
control, and clamps `[0,1]` per-channel — it is not Highlights/Shadows' own global
component; the broader point holds.)

**Disqualifier 2 — §7's faithful-path policy forbids it.** DECISIONS.md §7
"faithful-path improvement policy" + [v09-dualmode-impl-plan.md] contract 3 permit a
working-domain change on the TIFF **only** when it is compliance-safe (removes a defect
ours has that Adobe doesn't, moving *toward* Adobe) **or** is gated on **Tier-1 ACR
golden-set evidence** (`tools/grading_sweep/`, which **does not yet exist**) showing it
is *more* faithful. A fitted local operator does **neither** — it introduces **new**
behaviour that may move the TIFF **away** from the Adobe look with no convergence
evidence. This is exactly the speculative move §7 forbids and the reason §5 dropped
the ops (warn-only). The seam is mechanically solvable (ProPhoto(D50) in/out,
overrange preserved, single gated RGC, byte-exact identity); the **contract**, not the
mechanics, is the fatal mismatch.

**Disqualifier 3 — temporal-signature mismatch (faithful-path-specific).** Any
independently-fitted local operator has a content-dependent per-frame adaptation whose
**flicker signature differs** from Adobe's closed one — unfixable by regularisation,
worst under a day/night sweep, and a *sequence-level* mismatch with the very look Door
B is trying to be faithful TO. Even a fit that scored well on a static ΔE test would
inherit this. (Reasoned, not measured — the HDR-still literature quantifies no
temporal axis — but it is a *second* independent disqualifier, not the sole leg.)

**Error-floor honesty.** The fit cannot reach a faithful floor regardless of effort:
the model is **misspecified** (multi-channel highlight reconstruction + auto-masking
are outside basic LLF) and the distinguishing component is **unidentifiable** from the
available data → a structural residual remains. An edge-rich real-image LR-pair
campaign could fit a biased *partial* approximation at best — still not a "match".
**Open implementability of LLF does NOT rescue Door B:** an open engine gives you the
*engine*, not Adobe's specific remapping curve, `σ` schedule, auto-masking, and
highlight reconstruction — those are the closed parts. The honest-warning status quo
(drop + warn, never silently wrong) is **strictly safer** than a confident guess.

**Where the capability belongs.** The general DR-compression / local-tone *need* is
real — and it is best served on the **PERCEPTUAL** path by the shared engine (§3),
where there is **no Adobe target** so halo-freedom + DR metrics are the only bar
(measurably-better, not match-closed-Adobe). The same engine, on faithful, draws the
opposite verdict because the **correctness criterion differs**. If users want a
Highlights/Shadows-*like* control, expose a clearly-labelled **non-faithful** "local
tone" control **on the perceptual path** that makes **no fidelity claim** — sidesteps
Door B entirely and reuses sunk engine cost. Cheap guardrail worth adopting: document
the flat-patch harness as **INVALID for any spatial/edge-aware op** (it silently
degenerates them to their pointwise response).

---

## §5 — PROPOSED amendment to DECISIONS.md §5 (paste-ready, marked PROPOSAL)

> **PROPOSAL (v09 local-tonemap research, 2026-05-31) — append to §5, reopening the
> dropped tone ops for the PERCEPTUAL path only:**
>
> **§5 amendment — perceptual-path DR-compression reopens the dropped-tone question
> (the FAITHFUL path is unchanged).** The PV5 `Highlights`/`Shadows`/`Whites` ops
> stay **permanently dropped + warn-only on the FAITHFUL/TIFF path** (closed-source,
> un-fittable from the flat-patch harness, and a working-domain change there is
> forbidden by §7's faithful-path policy — see Door B, [research/v10-local-tone-mapping-dr-compression.md] §4).
> However, the *capability* they gesture at — **surgically compress a large dynamic
> range while retaining local/perceived contrast** — is reopened **on the PERCEPTUAL
> path**, where no Adobe fidelity is owed and a measurably-better open operator can
> ship. The chosen operator is a **scene-referred local DR-compression op** built as
> the **base-attenuation mode of the shared edge-aware base/detail engine** (the same
> engine as Texture/Clarity, §7 step 4): **Local Laplacian filters (Aubry 2014 fast
> variant)** as the halo-free quality primary, **guided filter (He 2013)** as the
> lightweight first cut / fallback base producer. It is **PERCEPTUAL-only**; on
> FAITHFUL it joins `_DROPPED_AT_EMIT_FIELDS` (warn-only), exactly like
> Highlights/Shadows/Whites. "Better" is the **measurable** set only (DR-compression
> strength, local-contrast retention, halo/gradient-reversal, temporal coherence) —
> **not** an aesthetic claim (no observer panel). This does **not** re-introduce
> Adobe's closed math; it ships an independent, open, measurable operator.

## §5b — PROPOSED §7 sequencing slot (paste-ready, marked PROPOSAL)

> **PROPOSAL — insert into DECISIONS.md §7 "Sequencing" after step 4 (Texture/Clarity):**
>
> **(4b) DR-compression (perceptual local tone) — rides the Texture/Clarity engine.**
> After PR-C lands the shared edge-aware base/detail engine (guided filter), add the
> **DR-compression op as the base-attenuation mode of that same engine** — PERCEPTUAL
> branch only, faithful path warn-only. Reuses PR-A's single gated RGC pass (contract
> 2); byte-exact identity via `is_identity()` short-circuit (contract 5); ProPhoto(D50)
> in/out (contract 1); Axis-1 oracle hand-rolls the pyramid + remap independently
> (contract 4). **This op is the consumer that justifies upgrading the engine's base
> producer from the guided filter to Local Laplacian** (Aubry 2014 fast variant),
> because DR-compression needs a *large* base radius where guided-filter halos grow and
> local Laplacian is provably halo-free — the Texture/Clarity role (small radius) does
> not stress this. **Precondition (open, do first):** derive the **scene-referred
> base-attenuation law** — discard the published display tail; compress base ratios
> toward a fixed scene-linear log anchor with **no top clamp** (overrange preserved).
> Day/night-extreme-DR tuning (adaptive renorm + mandatory temporal EMA, EV-ramp anchor
> coupling) is an **opt-in flag**, not core (§3b). Effort **L** but amortised with the
> Texture/Clarity quality upgrade (L-once-for-two).

---

## §6 — Honest open questions + the no-observer-panel caveat

**The load-bearing open derivation (do before coding the op):**
1. **Scene-referred base-attenuation law.** What law replaces the display-normalising
   tail so we compress base ratios toward a fixed log anchor (candidate 0.18) **without
   imposing a ceiling** (overrange must survive)? Needs the derivation + a pinned
   module-constant anchor + an Axis-1 oracle target. **Common to all candidates**; the
   single biggest open item. Until done, the op is the CLAUDE.md §0 trap (neutrals
   pass, overrange/saturated fail).

**Engine / method:**
2. **Confirm the build-once architecture** (DR-compression = base-attenuation mode of
   the shared guided/local-Laplacian engine, not a separate bilateral or
   gradient+Poisson engine). This doc recommends it; it is a product/architecture
   decision.
3. **Guided filter vs Local Laplacian as the eventual base producer for the DR role.**
   Quantify the **actual** halo delta at the *large* base radius DR-compression needs,
   on our own saturated, high-contrast day/night frames — confirm the He-et-al.
   dominance holds for our content before finalising. (Sequence guided first per PR-C;
   target local Laplacian.)
4. **Fast-variant intensity sampling `{γj}`** on ProPhoto scene-linear: the >30 dB claim
   is for the paper's test set — verify the static error on overrange HDR inputs; sample
   every `σ`.

**Parameters (must be pinned as exact module constants for the byte-exact oracle):**
5. `σr` (or guided radius/ε), `β` per slider unit, the fixed log anchor, detail gain,
   and (if the day/night flag) the EMA window — all empirical, **not** "auto from image
   size", **not** "≈".
6. Expose `σr` at all, or fix it at `log(2.5)` and expose only `β` (Paris advises
   "keep `σr` fixed, vary `β`") — product decision.

**Temporal (day/night flag, §3b):**
7. Does the residual **local-decomposition drift** under a real day/night sweep warrant
   the deferred Aydın-2014 spatiotemporal base filter, or is fixed-anchor + LRT's
   upstream deflicker enough? Needs a measured flicker metric on real footage.

**Metric:**
8. **Target ΔE-ITP / local-contrast-uniformity bar for the master** — the LRT-fitness
   ΔE does **not** apply to the EXR master ([v09-dualmode-impl-plan.md] flags the same
   gap). Still unspecified; needs a number.

**The no-observer-panel / measurable-not-aesthetic caveat (load-bearing).** Everything
above is defensible on the **measurable** axes (DR-compression strength,
local-contrast retention, halo/gradient-reversal count, per-frame luminance-drift
flicker). We have **no observer panel**, so we **do not** claim the recommended op
*looks* better — only that it is more halo-free, retains local contrast better, and is
more temporally coherent by the §1 metrics. "Which compression is most pleasing", the
*magnitude* of an acceptable local-contrast boost, and "does this day→night transition
feel natural" are **preference** questions, gold-standard only via a psychophysical
panel under controlled viewing — explicitly **out of scope**, matching the project's
honesty discipline and the frontier doc §1.5.

---

## Sources (primary, fetched 2026-05-31; verifier corrections folded in)

**Recommended method**
- Paris, S., Hasinoff, S. W., Kautz, J. (2011). *Local Laplacian Filters: Edge-aware
  Image Processing with a Laplacian Pyramid.* ACM TOG 30(4) (SIGGRAPH). DOI
  10.1145/1964921.1964963 (journal 10.1145/2010324.1964963); CACM 58(3):81-91, 2015,
  DOI 10.1145/2723694. [paper, PDF read this session — §5 recipe + Eq.1-3 verbatim]
- Aubry, M., Paris, S., Hasinoff, S. W., Kautz, J., Durand, F. (2014). *Fast Local
  Laplacian Filters: Theory and Applications.* ACM TOG 33(5):167. DOI 10.1145/2629645.
  HAL hal-01063419. [paper, PDF read this session — §2 acceleration + timings verbatim]
- He, K., Sun, J., Tang, X. (2013). *Guided Image Filtering.* IEEE TPAMI 35(6):1397-1409.
  DOI 10.1109/TPAMI.2012.213; PMID 23599054. ECCV 2010 DOI 10.1007/978-3-642-15549-9_1.
  [paper, PDF text extracted this session — gradient-reversal-inherent quote verbatim]

**Compared / parent methods**
- Durand, F., Dorsey, J. (2002). *Fast Bilateral Filtering for the Display of HDR
  Images.* ACM TOG 21(3):257-266 (SIGGRAPH). DOI 10.1145/566654.566574. [paper]
  Patent US7146059B1 (Durand & Dorsey, MIT), **EXPIRED 2025-02-08** [confirmed twice,
  Google Patents]. OpenCV `xphoto::TonemapDurand` Apache-2.0 — **docs URL 404'd this
  session, treat as unconfirmed**.
- Fattal, R., Lischinski, D., Werman, M. (2002). *Gradient Domain High Dynamic Range
  Compression.* ACM TOG 21(3):249-256 (SIGGRAPH). DOI 10.1145/566570.566573. [paper,
  8 pp read]. **NB: `cv2.createTonemapFattal` does NOT exist — OpenCV never shipped a
  Fattal operator; the assessment's API reference is struck.**
- Eilertsen, G., Mantiuk, R. K., Unger, J. (2017). *A comparative review of tone-mapping
  algorithms for HDR video.* Computer Graphics Forum 36(2) STAR. [paper, 12 pp read —
  §5.2 temporal coherence; §6 [WRA05] Wang–Raskar–Ahuja 2005 3D-Poisson Fattal video
  extension, the airtight Fattal-specific temporal proof]
- Mertens, T., Kautz, J., Van Reeth, F. (2007). *Exposure Fusion.* Pacific Graphics
  2007, IEEE, pp. 382-390. DOI 10.1109/PG.2007.23. Journal: CGF 28(1):161-171, 2009,
  DOI 10.1111/j.1467-8659.2008.01171.x. [paper, conference PDF read]. OpenCV
  `MergeMertens` Apache-2.0 (core photo module); BSD-2 reference MATLAB
  (github.com/Mericam/exposure-fusion).
- Burt, P., Adelson, T. (1983). *The Laplacian Pyramid as a Compact Image Code.* IEEE
  Trans. Communications COM-31:532-540. [paper — the shared pyramid plumbing]

**Temporal coherence**
- Aydın, T. O., Stefanoski, N., Croci, S., Gross, M., Smolic, A. (2014). *Temporally
  Coherent Local Tone Mapping of HDR Video.* ACM TOG 33(6) (SIGGRAPH Asia). DOI
  10.1145/2661229.2661268. [paper — the deferred spatiotemporal base-filter upgrade]
- Boitard, R., Cozot, R., Thoreau, D., Bouatouch, K. (2014). *Zonal brightness coherency
  for video tone mapping.* Signal Processing: Image Communication 29(2):229-246. [paper
  — the zonal-coherency post-stabiliser family motivating the temporal layer]

**Door B (faithful-path Adobe-fit feasibility)**
- Paris 2011 + Aubry 2014 (above) — the local-Laplacian-class algorithm. [paper]
- Chan, E. (Adobe ACR engineer), in C. Cramer, *Tonal Adjustments in the Age of
  Lightroom 4*, Luminous Landscape. [secondary — "edges to modify various tones",
  1 min/MP → "almost real time"]
- *Editing Highlights and Shadows in Adobe Lightroom and Camera Raw*, CreativePro.
  [secondary — "The value of a shade changes faster as it approaches its border"]

**Repo authorities cross-referenced [repo]**
- [DECISIONS.md](../DECISIONS.md) §5 (dropped ops), §7 (dual-mode + faithful-path
  policy + sequencing).
- [v09-perceptual-grading-frontier.md](v09-perceptual-grading-frontier.md) §1
  (measurable-better axes, mirrored here), §2.3 (Texture/Clarity frontier).
- [v09-dualmode-impl-plan.md](v09-dualmode-impl-plan.md) (five contracts; PR-A/B/C;
  Step 4 guided-filter-first / local-Laplacian-deferred; verifier BLOCKERS folded in).
- [PIPELINE.md](../PIPELINE.md) Stage 7/11 (overrange preserved, no clamp).
- `src/lrt_cinema/develop_ops.py` (`_PROPHOTO_LUMINANCE:407`; `apply_exposure_2012`
  "we do not clamp" `:44-53`; `apply_hsl` no-top-clamp `:192`; `is_identity()`
  short-circuits `:165,222`; PERCEPTUAL dispatch `:326-331`).
