# Pipeline order-of-operations & per-stage domain audit

**Adversarial audit of every render stage's colour space / domain and its
ordering vs the Adobe DNG SDK / Lightroom Classic PV2012.** Goal: find every
place a transform runs in the wrong domain, in the wrong order, or — the most
expensive class — where an **early stage clamps / quantizes / mutates data a
later stage needs**.

> **Status:** 2026-06-03. Analysis only; no code changed. Findings ranked by
> data-loss risk. Cross-references DECISIONS §8/§9/§10/§11, PIPELINE.md §2/§2.5/§5/§7.
> Companion to PIPELINE.md (as-built engine) — this doc audits the *ordering*
> PIPELINE.md documents.
>
> **EXTENDED + CORRECTED by [pipeline-worldclass-gap-and-plan.md](pipeline-worldclass-gap-and-plan.md)
> (2026-06-03):** that doc adds the categories this order/domain audit was
> structurally blind to (missing subsystems, demosaic quality, highlight-recovery
> *timing*, temporal coherence, the master's whole-stack coherence) and **corrects
> three claims here** — (1) **F8 over-flagged** (`scene_kelvin=5500` is inert on the
> production profile: Temp=4034 couples it, and D750 Camera Standard FM1==FM2/no-HSM
> → kelvin affects nothing); (2) the **ACR-order "UNVERIFIED"** label is now too
> coarse — the DNG *profile* chain (HSM→Exposure→LookTable→ToneCurve, exposure
> before the tone curve) is **primary-sourced** via the public DNG spec + the
> `dng_render.cpp` mirror, though ACR's PV2012 *develop-panel* order stays closed, so
> the §10 reorder stays gated; (3) the implicit "display-referred-in-the-middle is a
> defect" reading is **wrong for the faithful path A** (it correctly mirrors Adobe —
> the 0.026 ΔE with the clamp proves it). Read both together.

---

## 0. Two blockers that bound this audit's evidence

1. **The Adobe DNG SDK source is NOT present.** `/private/tmp/dng_sdk/` exists but
   `find … -name '*.cpp' -o -name '*.h'` returns **0 files**. Every "SDK
   `file:line`" reference below is therefore **second-hand** — quoted from
   PIPELINE.md / code docstrings that were written when the SDK *was* present, not
   freshly re-verified. Primary evidence here = **code `file:line`** + the
   project's own ΔE measurements. Where a claim rests only on a now-unverifiable
   SDK cite, it is marked **[SDK-cite, unverified]**. Re-checkout the SDK to close
   these.

2. **The deflicker "3× / domain" premise is corrected by measurement (see F3).**
   The motivating brief attributed the ~3× deflicker under-delivery to the
   *post-tone-curve domain*. The project's own test (memory
   `lrt-jpg-northstar-baseline`) **refuted that**: applying the deflicker EV
   pre- vs post-tone-curve gives the *same* gain ramp; only a ~3× *scale* flattens
   it. So the dominant deflicker error is **units**, not domain. Lead with that.

---

## 1. Master order & per-stage domain (as-built, default faithful sRGB)

| # | Stage · file:fn | Domain in→out | Clamp / mutation | Tap |
|---|---|---|---|---|
| 1 | demosaic · `pipeline.demosaic_camera_rgb` | DNG→camera RGB | libraw normalises WhiteLevel→1.0 and **hard-clips** highlights | — |
| 1.5 | highlight recovery · `highlight_recovery.reconstruct_highlights` | camera→camera (pre-WB) | byte-exact no-op if no clip; rebuilds clipped channels | opt-in |
| 2 | white balance · `pipeline.py:554-557` | camera→camera | `camera·(1/ASN)`, G=1; no clamp | — |
| 3 | cam→XYZ(D50) · `pipeline.py:560-596` / `dcp.colormatrix_camera_to_pcs` | camera→XYZ(D50) | matmul only; negatives pass through | 3 |
| 4 | XYZ→ProPhoto · `pipeline.py:607-609` | XYZ→PP(D50) lin | matmul only | 4 |
| 5 | HueSatMap · `lut3d_baker._apply_hsv_cube` | PP(HSV, **linear V**) | hue wrap, sat/val clamp at cube edge; **no V-clamp on linear branch** | — |
| 7 | ExposureRamp · `pipeline.make_exposure_ramp` | PP→PP lin | **CLAMP `min(linear,1.0)` when `support_overrange=False`** (`pipeline.py:179-180`) | 7 |
| 8 | LookTable · `lut3d_baker._apply_hsv_cube` | PP(HSV, **sRGB-gamma V**) | **CLAMP `clip(v·scale,0,1)`** in the srgb_gamma branch (`lut3d_baker.py:211`) | — |
| 9 | ProfileToneCurve · `pipeline.apply_rgb_tone` | PP→PP lin | **CLAMP [0,1] in *and* out** (`pipeline.py:124,148`); hue-preserving | 9 |
| 11 | Exposure2012→Blacks2012 · `develop_ops.apply_stage_11_linear` | PP→PP lin | exposure no clamp; **Blacks floors at 0** (`develop_ops.py:70`) | — |
| 12 | ToneCurvePV2012→Sat→Vib→HSL→ColorGrade→Contrast→Sharpness · `apply_stage_12_perceptual` | PP / HSV / log | **ToneCurvePV2012 CLAMP [0,1]** (`develop_ops.py:101,104`); Sharpness no-op | — |
| 13 | output · `output.write_preset_output` | PP→delivery | TIFF: per-channel clip [0,1]; EXR: gated RGC then f16 | — |

**Adobe / ACR reference order (for the ordering verdicts):** WB → **Basic tone
(Exposure, Contrast, Highlights, Shadows, Whites, Blacks — together, scene-linear)**
→ Presence (Texture/Clarity/Dehaze) → **Tone Curve** → **HSL** → **Color Grading**
→ Detail. The load-bearing contrast with our build: **Adobe applies Exposure +
Contrast scene-linear *before* the profile/tone render; we apply them at
Stage 11/12 *after* the clamping Stage-9 DCP tone curve.** That inversion is the
root of the top findings.

---

## 2. Findings — DEFECTS / RISKS, ranked by data-loss

### F1 — [HIGHEST] Highlight headroom is clamped at Stage 7 **and** Stage 9, before Stage-11 Basic tone can use it
*Known defect class (DECISIONS §8/§10), extended here with a new Stage-7 finding.*

- **Current order/domain.** Stage 7 ExposureRamp clamps `linear = min(linear, 1.0)`
  whenever `support_overrange=False` (`pipeline.py:179-180`) — which is **every
  tap-9 path** (`support_overrange=(stop_after_stage==7)`, `pipeline.py:650`).
  Stage 9 then re-clamps `[0,1]` on input and output (`pipeline.py:124,148`). Both
  run **before** Stage-11 `apply_exposure_2012` (`x·2^EV`, `develop_ops.py:55`) and
  the rest of Basic tone.
- **Why wrong.** ACR applies Exposure/Highlights scene-linear *before* the profile
  tone render; here a +EV or highlights pulldown at Stage 11/12 has nothing left to
  recover — the over-range data was discarded two stages earlier. **Proven:**
  faithful highlight-recovery ON==OFF byte-identical even under a synthetic −3 EV
  pulldown (DECISIONS §8) — the clamp fired first.
- **NEW (extends §10) — there are THREE serial clamps, not one; §10 names only Stage 9.**
  Between Stage 7 and the develop ops sit **three** headroom-destroying clamps, in order:
  (i) **Stage 7** ramp `min(linear,1.0)` (`pipeline.py:179-180`); (ii) **Stage 8**
  LookTable `np.clip(v_encoded·val_scale, 0, 1)` in its srgb_gamma branch
  (`lut3d_baker.py:211` — see F6); (iii) **Stage 9** ProfileToneCurve `[0,1]`
  (`pipeline.py:124,148`). Today **Stage 7 is the binding (first) clamp**, so 8 and 9
  are masked — but the §10 reorder defers Stage 9 and enables Stage-7 overrange, at
  which point **Stage 8 immediately becomes the binding clamp.** A reorder that
  defuses only Stage 7+9 (the F1-original list) still loses headroom at Stage 8.
  **Data-loss risk: HIGH** (full highlight headroom) on exposure-ramped / graded
  sequences.
- **Recommended reorder (described) — and a real feasibility obstacle §10 hasn't priced
  in.** Adopt §10's "Basic-tone-before-profile-tone" layering, **and** set
  `support_overrange=True` on the Stage-7 ramp on the faithful path, **and** address the
  Stage-8 LookTable. The Stage-8 piece is the hard part: the LookTable is not just a
  clamp at `lut3d_baker.py:211` — the **cube itself is authored in a `[0,1]`
  sRGB-encoded domain** (its V axis is sRGB-OETF-encoded and indexed on `[0,1]`,
  `lut3d_baker.py:150`), so it **saturates at its top cube entry** for any V≥1. Removing
  the line-211 clamp is necessary but *not sufficient*: preserving overrange through the
  LookTable requires **defining out-of-cube extrapolation** for a cube Adobe authored on
  `[0,1]` (or bypassing the LookTable for over-range pixels). That is a genuine obstacle
  — it **strengthens "§10 is a gated proposal, not a slam-dunk."** Then apply the DCP
  ProfileToneCurve as the display tonemap **last** (clamp only at the display encode).
  Identity case (no develop ops) still reproduces `dng_validate` → gym/rose tripwire
  stays green (the gate renders stages 1–9 with no develop ops — orthogonal, DECISIONS
  §10/§11). **Not exercised by the current constant-neutral deliverable**
  (`crs:Exposure2012=0`); validate on a Holy-Grail day↔night sequence (§10 go/no-go).

### F2 — [HIGH, EXR master] ToneCurvePV2012 is intent-independent and clamps [0,1] → it destroys the perceptual-EXR overrange the whole perceptual path preserves
*New finding.*

- **Current order/domain.** `apply_stage_12_perceptual` applies `apply_tone_curve_pv2012`
  **first, for both intents** (`develop_ops.py:850`, before the `if PERCEPTUAL`
  branch at :853). That op clamps input *and* output to `[0,1]`
  (`develop_ops.py:101,104`).
- **Why wrong.** The `cinema-linear-master` (tap-7) EXR exists to carry scene-referred
  **over-range** headroom; every perceptual op below it (`apply_dr_compression`,
  `apply_texture_clarity`, `_apply_contrast_perceptual`) is deliberately "floor 0, **no
  top clamp**," and `output._aces_rgc_compress_ap1` cleans the wide-gamut result. A
  non-identity `crs:ToneCurvePV2012` **clamps the tap-7 over-range to 1.0 before
  DR-compression runs**, silently defeating the master's purpose. Secondary: the op is
  **per-channel** (`develop_ops.py:103-106`), which rotates hue/saturation on saturated
  colour — directly contradicting the perceptual path's §0 hue-stability thesis (the
  exact reason `_apply_contrast_perceptual` was split out, DECISIONS §7 2026-06-01).
- **Data-loss risk: HIGH on the EXR master *iff* a user tone curve is present**
  (byte-exact no-op on the identity curve — `develop_ops.py:98 — and the current
  deliverable has none). Latent, not currently exercised.
- **Recommended fix (described).** On the PERCEPTUAL branch, replace the `[0,1]`
  per-channel `apply_tone_curve_pv2012` with a hue-preserving, overrange-safe tone
  application (luminance-domain ratio reapply, no top clamp — the `_apply_contrast_perceptual`
  pattern), or move it after RGC at display encode. Keep faithful unchanged (the
  `[0,1]` per-channel curve is part of the sRGB Lightroom look it matches).

### F2b — [HIGH, latent] `cinema-linear-finished` is **tap-9** yet defaults to **PERCEPTUAL** → scene-referred perceptual ops run on tone-curved, [0,1]-clamped data
*New finding (broader than F2 — the whole Stage-9 DCP tone curve precedes the ops, not just ToneCurvePV2012).*

- **Current order/domain (code-confirmed, not inferred).** `STAGE_7_PRESETS =
  {"cinema-linear-master"}` only (`presets/__init__.py:34`); the worker sets
  `stop_after_stage = 7 if preset in STAGE_7_PRESETS else 9` (`cli.py:351`) →
  **`cinema-linear-finished` renders at tap-9** (Stage-9 ProfileToneCurve applied +
  `[0,1]`-clamped). Yet `cinema-linear-finished` ∈ `_PERCEPTUAL_DEFAULT_PRESETS`
  (`cli.py:68`) → its Stage-12 runs the **PERCEPTUAL** applicators.
- **Why wrong (domain mismatch).** The perceptual ops are **scene-referred by
  construction**: `apply_dr_compression` pivots its 3-slope law around a **fixed
  scene-linear 0.18 log anchor** (`develop_ops.py:562,635`), and DR/Texture/
  Contrast-perceptual are "floor 0, **no top clamp**, overrange → RGC." On tap-9 input both
  assumptions are violated: (a) the Stage-9 DCP tone curve has already remapped scene-0.18
  to some higher display value, so the DR-compression anchor **no longer sits at scene
  midgray** — the compression pivots around the wrong luminance; (b) Stage 9 already
  clamped to `[0,1]`, so "preserve overrange for RGC" is **moot** (no over-range left; RGC
  has nothing to compress but what the perceptual ops re-emit). The perceptual master's
  scene-referred thesis is realised **only on `cinema-linear-master` (tap-7)**;
  `cinema-linear-finished` feeds the same ops display-shaped data.
- **Data-loss risk:** none directly; **HIGH conceptual/quality risk** when any
  perceptual-only slider (Highlights/Shadows/Whites/Texture/Clarity) or OKLCh/CDL grade is
  non-zero (all short-circuit to no-ops at zero → **latent on the constant-neutral
  deliverable**).
- **Recommended action (a preset-default decision, not a mechanical fix).** Resolve the
  intent: either (1) default `cinema-linear-finished` to **FAITHFUL** (a display-shaped
  "finished" EXR — the faithful grade ops are domain-appropriate for tap-9), or (2) if
  PERCEPTUAL on tap-9 is intended, **gate the scene-referred ops** (DR-compression's
  0.18-anchor law especially) off the tap-9 path or re-anchor them post-tone-curve. Confirm
  intent with the project owner; this audit flags the mismatch only.

### F3 — [HIGH user-visible, but UNITS not domain] Deflicker `LocalExposure2012` is applied 1:1 → ~3× under-delivered; the post-tone-curve domain is only a 2nd-order contributor
*Corrects the motivating premise.*

- **Current order/domain.** `crs:LocalExposure2012` is parsed raw (`xmp_parser.py:353,356`),
  summed **1:1, no scale** onto `exposure_ev` (`interpolation.py:121-122,127`; synthetic
  path :91-93), then applied at **Stage 11** as `x·2^EV` (`develop_ops.py:55`), i.e.
  *after* the Stage-9 DCP tone curve. Exhaustive grep: the only multiplicative use of
  `exposure_ev` anywhere is the `2^EV` gain itself — **no ~3× or any coefficient exists**
  (verified across `xmp_parser`/`interpolation`/`develop_ops`/`_mlx_kernels.py:377`).
- **Verdict on the brief's premise (post-tone-curve domain → 3× under-delivery):
  REFUTED by measurement.** The project tested deflicker EV pre- vs post-tone-curve and
  got the *same* gain ramp (PRE k=1 ≈ POST k=1); only a ~3× *scale* flattens the
  per-frame gain ramp (0.96→1.10) to flat (~1.02) across the held-out validation set
  (memory `lrt-jpg-northstar-baseline`). So the dominant error is a **units/scale
  mismatch** (LR local-mask Exposure vs global Exposure2012 units), **not** the domain.
  The post-tone-curve domain *is* the F1 class, but the deflicker EV is ±0.05–0.07 stop
  — so small that the locally-near-linear midtone curve makes `tone(x·2^ε)≈tone(x)·2^ε`;
  domain contributes ~2%, units contributes 3×.
- **Data-loss risk:** none (no data destroyed); but **high user-visible impact** — it is
  the dominant component of the LRT-JPG north-star gap on this deliverable (residual
  brightness ramp / flicker).
- **Recommended fix (described).** Scale `LocalExposure2012`→`exposure_ev` by the
  empirically-calibrated factor (~3×) **as its own small PR**, *independent of the F1
  reorder*. **Pin the exact factor with a basis** (LrC local-vs-global Exposure2012 units
  convention — currently uncited/TBD; do NOT hard-code an inferred number) + a test +
  held-out-frame validation. This is a units bug in the *ingest*, not a pipeline reorder.

### F4 — [MEDIUM, faithful look-match] Contrast2012 runs **last** (after ColorGrade), split from the rest of Basic tone
*New finding; medium confidence (ACR closed-source).*

- **Current order/domain.** Faithful Stage 12 order is `ToneCurve → Sat → Vib → HSL →
  ColorGrade → Contrast2012 → Sharpness` (`develop_ops.py:850-873`). `apply_contrast_2012`
  is a per-channel pivot-0.18 linear gain (`develop_ops.py:750-763`).
- **Why suspicious (candidate, not established).** On the *assumed* ACR PV2012 order —
  **Contrast is a Basic-panel slider applied scene-linear *with* Exposure, *before* the
  tone curve and *before* HSL/Color Grading** — our placement diverges twice: Contrast is
  (a) separated from Exposure/Blacks (Stage 11) and (b) placed **after** ColorGrade, so it
  contrast-stretches the colour-grade tints about 0.18, which that ACR order would not.
  **But this ACR order is exactly the assumption DECISIONS §10 flags UNVERIFIED** (DNG
  SDK / ACR processing order not confirmed) — so treat this as a *candidate* order issue
  against an *assumed* reference, not an established divergence.
- **Evidence/caveat.** No primary cite (SDK absent, ACR closed) — reasoned from the
  conventional (unverified) ACR Basic-panel grouping. The slider ops "fire only when the
  LRT keyframe carries non-zero values" (`develop_ops.py` module docstring), so it is
  **not exercised by the constant-neutral deliverable**. **Data-loss: none**; look-fidelity
  risk on graded faithful renders.
- **Recommended action.** Move Contrast2012 into Stage-11 Basic tone (alongside Exposure,
  before Stage-9 if F1 is adopted; otherwise immediately after Exposure2012, before
  ToneCurvePV2012/HSL/ColorGrade). **Gate on grading-sweep ACR evidence** (`tools/grading_sweep/`,
  Tier-1) before changing the faithful order — that is the project's tool for exactly this
  "is the modern/old order more faithful" question (DECISIONS §7 faithful-improvement policy).

### F5 — [MEDIUM, mitigated] Blacks2012 floors to exactly 0 → degenerate near-black pixel → perceptual false-cast
*Known + already fixed; documented for completeness.*

- **Order/domain.** `apply_blacks_2012` (Stage 11, **intent-independent**) does
  `max(prophoto + bias, 0)` (`develop_ops.py:70`), so a dark slightly-chromatic pixel
  loses its smaller channels to *exactly* 0 → degenerate single-channel near-black. A
  downstream shadow-*lifting* perceptual reapply (`lum_out/lum`→∞) then amplifies it into
  a saturated false cast + negative AP1 (rendered in `output.py`'s ProPhoto→AP1 Bradford).
- **Status: FIXED upstream** by the shared near-black guard (`_nearblack_gate`,
  `_reapply_luminance_ratio`, `_roll_chroma_to_neutral`; DECISIONS §7 2026-06-01). The
  guard is byte-identical above `_NEARBLACK_LUM_FLOOR=0.004` and gated on **input**
  luminance (verified: `develop_ops.py:1267-1272,1291`). Faithful is immune (per-channel
  pivot lift → neutral). **Data-loss: none post-fix.** Root cause is the **ordering**
  (a `[0,1]`-flooring op feeding a ratio-reapply op) — recorded so the class isn't
  reintroduced.

### F6 — [LOW, latent] LookTable srgb_gamma V-clamp is overrange-destroying but unguarded
*New finding; latent on the current profile.*

- **Order/domain.** The `srgb_gamma` cube branch clamps output V `clip(v_encoded·val_scale,
  0, 1)` (`lut3d_baker.py:211`), genuinely destroying over-range; the linear branch (HSM)
  has **no** V-clamp (`lut3d_baker.py:214`). Benign **today** only because: (a) the D750
  Camera Standard ships **no HueSatMap** (so the clamp never runs at Stage 5), and (b) the
  LookTable runs at Stage 8 — *after* the tap-7 EXR return (`pipeline.py:663`).
- **Risk.** Unguarded fragility: if the EXR tap ever moved below Stage 8, or any HSM cube
  were `srgb_gamma=True`, line 211 would silently eat headroom **pre-ExposureRamp**. **Data-loss:
  none now; latent.** Recommend an assertion/test that the srgb_gamma clamp never executes
  on an overrange-preserving (tap-7) path.
- **Connects to F1 (not independent).** This is the **Stage-8 clamp F1's reorder must also
  defuse**: the moment F1 enables Stage-7 overrange and defers Stage 9, this line-211 clamp
  becomes the **binding** highlight-headroom clamp — and beyond the clamp, the LookTable
  cube's `[0,1]` sRGB-encoded V domain saturates at its top entry for V≥1, so over-range
  cannot pass the LookTable without out-of-cube extrapolation. F1 and F6 are one obstacle.

### F7 — [LOW, EXR master] float16 cast can overflow a finite highlight to +inf
*New finding; minor.*

- **Order/domain.** `write_exr_scene_linear` scrubs `posinf→65504` (`output.py:488`)
  **before** the float16 cast (`output.py:499`) and never re-scrubs after. A *finite*
  float32 ≥ ~65520 therefore becomes **+inf** in the half EXR (defeating the writer's own
  anti-inf intent); worse, `np.float16(65504.0)` itself rounds to 65500, marginally above
  the true half-max. Same on the `aces2065` path.
- **Risk.** Only bites legitimate data > ~18.7 stops over 0.18 grey; RGC cannot cause it
  (max channel invariant). **Data-loss: minor, edge-case.** Fix: `np.minimum(pixels, 65504.0)`
  *after* the cast (or scrub the half array), or document `bit_depth="float"` as the escape.

### F8 — [LOW, documented] Stage-3 FM mired-blend driven by a hardcoded `scene_kelvin=5500`, decoupled from per-frame AsShotNeutral CCT
*Known/intentional; flagged as in-scope domain caveat.*

- **Order/domain.** Default `scene_kelvin = DEFAULT_SCENE_KELVIN = 5500.0`
  (`pipeline.py:74,776`); `neutral_to_kelvin` (the ASN-derived CCT) is deliberately *not*
  called (`pipeline.py:356-358` — computed-K regressed rose ΔE). So Stage-2 WB (from ASN)
  and the Stage-3 FM mired-blend (from the 5500 constant) can run at **different
  temperatures**. The Holy-Grail override re-derives ASN *from* `scene_kelvin`, so they
  stay coupled there.
- **Scope.** No-op for D750 Camera-Matching profiles (FM1==FM2 passthrough); bites only the
  real-FM path (Adobe Standard / rose). Documented/intentional, **not** an accidental bug.
  **Data-loss: none.** Listed because it is squarely an order/domain coupling.

---

## 3. Findings — CONFIRMED-CORRECT (with reason)

| Stage | Domain/order | Why correct | Evidence |
|---|---|---|---|
| **2 WB** | camera space, pre-matrix | per-channel WB must precede the colour matrix; G-normalised | `pipeline.py:554-557` |
| **3 dual-path** | FM on WB-balanced; ColorMatrix on **raw** camera RGB | ColorMatrix folds white-adaptation via MapWhiteMatrix → applying to raw is correct; **no double/missing WB**, `M·ASN`→D50 neutral | `dcp.py:865-873`; agent-verified empirically |
| **3 CAT** | linearized Bradford → D50 (not D65) | matches DNG MapWhiteMatrix | `dcp.py:797-801` |
| **3 mutation** | matmul + astype only; negatives pass through | nothing a later stage needs is clipped at the colorimetric tap | `pipeline.py:579-580,595-596` |
| **1.5 highlight recovery** | camera space, **pre-WB** | uniform clip point pre-WB; fully-blown ∝ASN → neutral after WB on **both** Stage-3 paths (no magenta) | `highlight_recovery.py:224-239`; DECISIONS §8 |
| **5/8 HSV-cube** | flag-driven V domain (HSM linear, LookTable sRGB-gamma); negatives passthrough via `valid` mask | the asymmetry is a per-cube DCP flag, not a constant; matches Adobe RefBaselineHueSatMap [SDK-cite, unverified] | `dcp.py:690`; `lut3d_baker.py:150,212,214` |
| **5→7→8 order** | HSM before ExposureRamp, LookTable after | matches Adobe (HSM pre-BaselineExposureOffset, LookTable post) [SDK-cite, unverified] | `dcp.py:192-194` |
| **9 tone curve** | hue/saturation-preserving `RefBaselineRGBTone`, not per-channel | per-channel rotates hue on chromatic pixels; this fix took gym 0.79→0.026 | `pipeline.py:101-148`; PIPELINE.md §5 |
| **13 TIFF OETF/clip** | matrix→OETF→nan_scrub→clip[0,1]; OETF floors negatives (`np.maximum`) | OETF monotonic ⇒ clip-after ≡ clip-in-linear; no NaN leak on negatives | `output.py:281-285,375,387-388` |
| **13 TIFF per-channel clip** | hard clip to sRGB gamut (hue shift on out-of-sRGB saturated colour) | **by design** — display delivery *must* clip to its gamut; a gamut compression would diverge from Adobe sRGB export (the north-star). RGC is correctly EXR-only | `output.py:388,496-497`; PIPELINE.md §7 |
| **13 EXR RGC** | after ProPhoto→AP1 Bradford + scrub, before f16; gated; AP0 not compressed | smooth gamut roll on the wide master; byte-exact no-op in-gamut | `output.py:478,496-499` |
| **12 perceptual ops** | all ProPhoto-in/out, single CAT, **floor 0 / no top clamp** | overrange preserved for RGC; OKLCh D50↔D65 Bradford mandatory + present; CDL ProPhoto→ACEScg uses the *same* Bradford as `output._prophoto_to_linear` (no double-transform) | `develop_ops.py:342,376,488,535`; agent-verified `colour` match 3e-18 |
| **12 near-black guard** | activates only below floor, gated on input luminance, rolls chroma not tone | legit shadow colour byte-identical; kills the false-cast→AP1-negative chain at source | `develop_ops.py:1267-1272,1291` |
| **deflicker offsets** | no double-apply | `apply_deflicker` (synthetic `deflickerExposure`) & `apply_lrt_mask_offsets` (`MaskGroupBasedCorrections`) read **disjoint, mutually-exclusive** sources; HG/Deflicker/Global summed once each | `interpolation.py:89,118`; `xmp_parser.py:432-439,529` |
| **intent dispatch** | sRGB/lrtimelapse→FAITHFUL; ACEScg EXR→PERCEPTUAL | per-target default; `--render-intent` overrides | `cli.py:71-73,618-619` |

### By-design observations (not defects, but easy to misread)
- **CDL "zero-sum chroma" is zero-sum in *log*, not linear.** A sat=100 push lifts linear
  luminance (0.18→0.269) because the ACEScct decode is nonlinear. This is standard ASC-CDL
  offset semantics and matches the docstring's "no net lift" *in log* — deliberate, not a
  bug (`develop_ops.py:519-528`).
- **Faithful Sat/Vib/HSL operate in Adobe-hexcone HSV of *linear* ProPhoto**, and faithful
  ColorGrade adds its tint in **linear** with a zone mask on **sRGB-OETF** luminance (mixed
  domain). LR's HSL/grading are perceptual/log. These are **documented public
  approximations with no fidelity claim** (`develop_ops.py:140-265`); the *perceptual* path
  uses the more-correct domains (OKLCh, ACEScct CDL). Flagged as a domain *consideration*,
  not a defect — but a candidate for the grading-sweep ACR comparison (DECISIONS §7).

---

## 4. Cross-reference to the known defects (DECISIONS §8/§10/§11)

- **§8 (highlight recovery inert in faithful) + §10 (headroom-through-develop-ops reorder)**
  → **F1 + F6**. This audit *extends* §10 in two ways. (1) §10 names only the Stage-9
  ProfileToneCurve, but there are **three** serial headroom clamps — Stage 7 ramp
  `min(linear,1.0)`, Stage 8 LookTable srgb_gamma `clip(…,0,1)`, Stage 9 curve — and the
  binding one shifts from Stage 7 (today) to Stage 8 the moment the reorder fires. (2) The
  Stage-8 piece is a real **feasibility obstacle §10 has not priced in**: the LookTable cube
  is authored in a `[0,1]` sRGB-encoded V domain and saturates at its top entry, so
  preserving over-range needs out-of-cube extrapolation, not just clamp removal. **This
  strengthens "§10 is a gated proposal, not a slam-dunk."**
- **§11 (PV2012 tone-emulation op)** → orthogonal to F1 (the look gap lives in *non-clipped*
  0.3–0.95 highlights). But note: §11 is a *new* Stage-12 luminance tone op; if added, place
  it in the **Basic-tone group** (with F1's reordered Exposure/Contrast), hue-preserving,
  overrange-safe — do **not** add another `[0,1]`-clamping per-channel curve (the F2 trap).
- **DECISIONS §9 (validation hierarchy)** governs all of the above: every reorder here keeps
  the no-develop-ops baseline byte-matching `dng_validate` (the gate renders stages 1–9 with
  no develop ops → orthogonal), so the gym 0.026 / rose 0.545 tripwire is *not* the obstacle.

---

## 5. Prioritized fix list (highest data-loss / impact first)

1. **F3 — scale `LocalExposure2012` (~3×, units).** Biggest *user-visible* win on the
   current deliverable (the dominant LRT-JPG north-star gap), and the cheapest: a units fix
   in ingest, independent of any reorder. **Blocker:** pin the exact factor with a cited
   basis (LrC units convention) + held-out-frame validation — do not hard-code an inferred
   number.
2. **F1 (+F6) — headroom-through-Basic-tone reorder.** Highest *data-loss* risk. Must defuse
   **all three** clamps (Stage 7 ramp, Stage 8 LookTable, Stage 9 curve) — and the Stage-8
   LookTable `[0,1]` cube domain is a genuine feasibility obstacle (out-of-cube extrapolation),
   not a one-line clamp removal. Gated go/no-go needs an **exposure-ramped** sequence (the
   constant-neutral deliverable can't validate it).
3. **F2b — `cinema-linear-finished` tap-9 + PERCEPTUAL mismatch.** A preset-default decision
   (default it FAITHFUL, or gate the scene-referred ops off tap-9). Cheap to resolve; affects
   every graded `cinema-linear-finished` EXR. Confirm intent with the owner.
4. **F2 — perceptual-path ToneCurvePV2012 overrange/hue fix.** Protects the EXR *master*
   (tap-7) reason-to-exist; latent until a user sets a tone curve on a perceptual render.
5. **F4 — Contrast2012 ordering on the faithful path.** Look-fidelity; **gate on
   grading-sweep ACR evidence** before moving it (the ACR order is itself UNVERIFIED — §10).
6. **F6 — guard the LookTable srgb_gamma V-clamp** against ever running on an
   overrange-preserving path (latent fragility, add a test) — and see F1 (it is the Stage-8
   clamp the reorder must defuse).
7. **F7 — clamp the float16 cast** (`np.minimum(pixels,65504.0)` after cast) on the EXR path.
8. **F8 — revisit `scene_kelvin` coupling** only if the Adobe-Standard/rose FM-blend path is
   prioritised (documented/intentional today).

**Already resolved:** F5 (near-black) — fixed by the DECISIONS §7 guard; documented so the
ordering class isn't reintroduced.

---

## 6. Method & honesty notes

- Evidence = code `file:line` (primary) + the project's ΔE/north-star measurements; **SDK
  cites are unverifiable** (§0.1) and marked. No claim rests on inference alone — the two
  prior bites (per-channel tone curve; "correct, not a gap") came from unverified inference,
  so suspicious claims are labelled MEDIUM/LOW confidence with the verification path named.
- Audit covered stages 1–13 + intent ingest (`xmp_parser`/`interpolation`/`ir`) + the
  deflicker/mask-offset application. Five parallel sub-audits (Stage 3, Stage 5/8, Stage 13,
  perceptual ops, deflicker/intent flow) each returned cited per-question verdicts.
- **Not separately re-verified:** the numba/mlx accelerated kernels (asserted colour-identical
  to numpy, max ΔE 6.4e-5; PIPELINE.md §11) — the numpy reference path was audited; the
  kernels are out of order/domain scope but inherit the same op order.
