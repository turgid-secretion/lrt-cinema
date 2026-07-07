# REFERENCE_PIPELINE.md — cross-engine pipeline architecture (the canon)

Owner-approved: 2026-06-10

## Why this document exists

Every defect root-caused during the June 2026 repair campaign was an
**architecture error, not a math error**: white balance applied after the
demosaic instead of before it (the cyan blinds); mask exposure applied at ×1
in the wrong domain (post-tone-curve) instead of ×4 scene-referred (the
deflicker drift); the GPU path silently dropping the white-balance tint.
The math inside each stage was fine — the stages were in the wrong places.

Anti-drift rule 8 therefore requires that any question about pipeline
*structure* (what order, what domain, what units) is answered against the
cross-engine canon below — never against a single reference. Our own gym
gate certified the broken WB ordering for weeks because Adobe's reference
demosaic is bilinear, and bilinear commutes with per-channel scaling; the
gate *could not see* the bug class.

**Owner directive (2026-06-10):** pipeline architecture and absolute stage
order must be comprehensively locked before any further feature work,
including enabling highlight reconstruction. This document is where that
lock happens.

## Provenance legend

Every claim below is tagged. Do not silently upgrade a tag.

- **[SRC]** — verified against the engine's actual source this campaign
  (read-to-learn is allowed and encouraged; vendoring GPL code is not).
- **[EMP]** — verified empirically (checked-in experiment + artifact).
- **[LIT]** — from cited literature; re-verify against source before
  load-bearing use.
- **[PEND]** — believed but not yet verified; next-session work.

## The canonical architecture (ISP literature)

Karaimer & Brown 2016 ("A Software Platform for Manipulating the Camera
Imaging Pipeline", ECCV) decompose the in-camera ISP into ordered stages.
**[LIT]** — the stage list below is from that paper's pipeline figure;
re-verify exact naming on next source pass.

```
RAW sensor data
  1. black-level subtraction / linearization
  2. lens shading correction
  3. white balance                    ← pre-demosaic
  4. demosaic
  5. noise reduction
  6. color space transform (CCM, camera → standard)
  7. tone reproduction / gamma
  8. color manipulation (the "look": 3D LUT / hue-sat tables)
  9. output encode (sRGB/JPEG)
```

**The owner's headline, and it is correct:** in the ISP canon, the major
signal-conditioning adjustments — black level, shading, **white balance**,
and (in most hardware ISPs and in darktable) **highlight handling** — happen
**pre-demosaic, in sensor/scene-referred space**. Creative color and tone
come after the colorimetric transform. Exposure-class adjustments are linear
gains in scene-referred space, *upstream of tone curves*, everywhere.

## Engine flowcharts (absolute positions)

Positions are absolute and numbered within each engine. "Scene-referred"
means linear light before any tone curve; "display-referred" means after
tone mapping/encoding.

### dcraw / LibRaw — `dcraw_process()` **[SRC 2026-06-10]**

Verified by reading `LibRaw/src/postprocessing/dcraw_process.cpp` (master).

```
  1. bad_pixels / dark-frame subtract        (optional)
  2. black subtract + adjust_maximum
  3. scale_colors()                          ← WHITE BALANCE, pre-demosaic.
       user/camera multipliers normalised by their MINIMUM when
       highlight==0 [EMP: our user_wb probe — [2,1,1.5,1] scales output
       by exactly ×2/×1/×1.5]; channels clip at 65535 → blown = white
  4. pre_interpolate()
  5. demosaic: lin/vng/ppg/ahd/xtrans/dcb/dht/aahd
  6. mix_green / median_filter               (optional)
  7. blend_highlights() / recover_highlights()  ← highlight RECONSTRUCTION
       is POST-demosaic here (clip-to-white happens in 3)
  8. convert_to_rgb()                        ← camera → output matrix
  9. stretch / gamma at write-out
```

No develop layer: dcraw has no exposure/contrast/curve ops to place.

### darktable — `v30_order`, `src/common/iop_order.c` **[SRC 2026-06-10]**

```
  1. rawprepare        (black level)                       @ 1.0
  2. temperature       ← WHITE BALANCE                     @ 3.0
  3. highlights        ← highlight RECONSTRUCTION,         @ 4.0
                         PRE-demosaic (mosaic domain)
  4. hotpixels / rawdenoise                                @ 6.0–7.0
  5. demosaic                                              @ 8.0
  6. denoiseprofile    (profiled NR, post-demosaic)        @ 9.0
  7. lens correction                                       @ 13.0
  8. exposure          ← linear gain, SCENE-REFERRED,      @ 21.0
                         before the color transform and
                         far before tone
  9. colorin           (camera → working space)            @ 28.0
 10. filmicrgb/sigmoid ← TONE, near the end                @ 46.0
 11. colorout          (working → display)                 @ 70.0
```

darktable is the strongest single corroboration of the ISP canon: WB *and*
highlight reconstruction pre-demosaic; exposure a scene-referred gain far
upstream of tone; creative color between colorin and tone.

### RawTherapee — `rtengine/rawimagesource.cc` **[SRC 2026-06-11, local]**

Local clone read (`~/src-reading/rawtherapee`), upgrading the 2026-06-10
web fetch:

```
  preprocess():
  1. dark frame / flat field      (copyOriginalPixels)
  2. black level + scaling        (scaleColors, rawimagesource.cc:2761)
  3. bad pixel interpolation
  4. raw CHROMATIC ABERRATION     ← CA_correct_RT, PRE-demosaic,
     correction                     on the mosaic
  5. green equilibration / line denoise / vignetting
  demosaic():
  6. demosaic (AMaZE/RCD/VNG4/…)  ← input: camera-WB-balanced rawData
  getImage():
  7. develop-WB DELTA multipliers (new_scale_mul/scale_mul, :797)
  8. highlight recovery           ← HLRecovery_inpaint / _opposed,
                                    POST-demosaic (hilite_recon.cc)
  9. false-colour suppression     ← processFalseColorCorrection
       (ccSteps>0, :2982): per step, 9-median over 3×3 on YIQ's I/Q
       (Y untouched) + 3×3 box blur of I/Q; POST-demosaic
```

**Nuance RESOLVED [SRC 2026-06-11]:** `scaleColors` bakes black subtract +
`scale_mul[c] = (pre_mul[c]/maxpremul) · 65535/(c_white[c]−c_black[c])`
(`calculate_scale_mul`, rawimagesource.cc:628) into `rawData` — `pre_mul`
is the **camera/auto WB**, max-normalised. So RT demosaics
**camera-AsShot-balanced** data; `getImage` applies only the develop-WB
*delta* (`rm = new_scale_mul/scale_mul`, then ×develop multipliers via
`wbMul2Camera`, :885) post-demosaic. RT therefore SPLITS WB across the
demosaic; darktable and dcraw apply the full selected WB pre-demosaic.
Consistent with the owner's no-cyan RT experiment [EMP]: the demosaic
input is balanced (camera WB ≈ develop WB within the ratio directional
algos care about). Clip levels: `clmax[c] = (c_white−c_black)·scale_mul`
computed at scaleColors; `hlmax = clmax·(develop delta)` in getImage.

### Adobe (DNG SDK / Lightroom) **[SRC: DNG 1.7.1 spec + our port; EMP]**

The colorimetric chain is specified in DNG 1.7.1 §"Mapping Camera Color
Space" and is what we implement (gym ΔE 0.023 vs `dng_validate` [EMP]):

```
  1. linearize + black subtract
  2. white balance (AsShotNeutral inverse)   ← spec'd pre-color-transform;
       reference demosaic is bilinear, so the spec is silent-but-
       insensitive on WB-vs-demosaic order [EMP: H1]
  3. demosaic (reference: bilinear)
  4. camera RGB → XYZ(D50) → ProPhoto linear (ForwardMatrix)
  5. HueSatMap (HSV, mired-interpolated)
  6. TotalBaselineExposure → ExposureRamp    ← exposure-class gain with
       soft shoulder, SCENE-REFERRED (pre-LookTable/pre-curve)
  7. LookTable (HSV)
  8. ProfileToneCurve (hue-preserving RGB tone)
  9. output color transform + encode
```

Lightroom's *develop* ops (PV2012) sit on top of this. Their internal
placement is mostly undocumented; what we have **measured** [EMP]:

- **Local/mask exposure (`LocalExposure2012`)**: serialized as EV/4;
  applied as `2^(4·EV)` **scene-referred, upstream of the tone pipeline**
  (CAL experiment: k\*=3.992±0.027, ΔE 0.20/0.44 at exactly 4.0; the
  post-curve domain cannot fit LR at ±1–2 EV for *any* factor —
  `tools/cal_deflicker_factor.py`). The owner's visual check confirms the
  signature: post-curve application lifts highlights mostly; scene-referred
  lifts the whole image, matching LR's renders.
- **Partially-clipped highlights**: `dng_validate` reconstructs them
  (gym max-ΔE census) — Adobe's highlight handling is reconstruction,
  not plain clip.

## OURS — exact chain at HEAD (2026-06-10) **[SRC: this repo]**

```
  0. dnglab NEF → DNG (LinearizationTable + correct WhiteLevel)
  1. decode + black subtract + white normalise
       (_extract_cfa for rcd/mlri/menon; libraw otherwise)
  2. WB pre-scale of the mosaic (Stage-2 multipliers)      [fixed 2026-06-10]
  3. demosaic (libraw linear | rcd | mlri | menon)
  4. divide-back (returns UNBALANCED camera RGB — contract)
  5. highlight_recovery Tier-1 (optional, default off, camera space)
  6. scene_exposure_ev: 2^(4·maskEV + Exposure2012) gain   [fixed 2026-06-10]
  6b. Highlights/Shadows scene-referred LOCAL translation  [added 2026-07-07]
       (scene_tone.apply_scene_hlsh — slot-7b, probe-calibrated)
  7. Stage 2  WB multiply (AsShotNeutral⁻¹ / kelvin override + tint)
  8. Stage 3/4 ForwardMatrix → XYZ(D50) → linear ProPhoto
  9. Stage 5  HueSatMap (HSV)
 10. Stage 6/7 TotalBE → ExposureRamp        ← scene-referred exposure
 11. Stage 8  LookTable (HSV)                ← [tap-7 EXR exits before 11]
 12. Stage 9  ProfileToneCurve               ← end of scene-referred life
 ──────────────────────────────────────────────────────────────────────
 13. Stage 11 Exposure2012 (2^EV), Blacks          ← POST-curve  ⚠
 14. Stage 12 ToneCurve → Saturation → Vibrance → HSL → ColorGrade
      → Contrast → (capture sharpen)               ← POST-curve  ⚠
 15. Stage 13 ProPhoto → sRGB (Bradford) → encode → 16-bit TIFF
```

Steps 2 and 6 are this campaign's fixes; both moved operations *up* the
chain into the canonical domain. ⚠ region: audited below.

## Develop-ops domain audit (ANSWERED 2026-07-07)

The owner's mismatch-class hypothesis (LR applies develop ops in domains
ours didn't) is now measured per-op — exposure class + H/S scene-referred
(fixed/shipped), the globals probed (round-2 CAL):

| Op (ours, step) | LR's placement | Status / next probe |
|---|---|---|
| Mask/local exposure (6) | scene-referred ×4 | **FIXED + verified** [EMP] |
| Global `Exposure2012` | scene-referred | **DONE 2026-06-11** — measured scene-referred, folded into the slot-7 gain (ledger slot 7) |
| `Highlights2012`/`Shadows2012` (6b) | scene-referred LOCAL | **MEASURED + SHIPPED 2026-07-07** — probe-calibrated translation (ledger slot 7b; CLAIMS round-2 rows) |
| `Blacks2012` (13) | global; scene-referred at +50 | **MEASURED 2026-07-07** — our shape wrong (CLAIMS) |
| `Contrast2012`, ToneCurve (14) | global; weak display lean | **MEASURED 2026-07-07** — placement class OK, our LAW wrong (slot 9) |
| HSL / ColorGrade / Sat / Vib (14) | color-domain | **MEASURED 2026-07-07** — HSL validated, ColorGrade refuted (slot 10); Sat/Vib unprobed |
| Sharpness / NR | ACR detail stage | [PEND] — part of the ~0.6 base-look floor |
| Highlight reconstruction (5) | canon SPLITS: darktable PRE-demosaic (mosaic), dcraw & RawTherapee POST-demosaic, Adobe reconstructs in-render | **BLOCKED on this lock** (owner). Our Tier-1 post-demosaic placement is RT/dcraw-consistent; but it is ~no-op on the residual clip-edge fringes (F vs G arms: 199 px > 2/255, max 4) — whatever fixes those is NOT the current Tier-1 |
| Raw CA correction | LR: off in production (census [EMP]). RT `CA_correct_RT` + dt `cacorrect@5.0`: PRE-demosaic [SRC] | **IMPLEMENTED opt-in 2026-07-07** (owner-directed): clean-room Martinec `_ca_correct.py`, dt placement, `--ca-correct N`, default OFF. See TARGET slot 2 |
| Partial-clip hue handling (mechanism A) | Adobe reconstructs partial clips in-render [EMP: gym census + fringe cluster 0] | **CONFIRMED defect class [EMP]** — clip-neutralization driven by a 2-dilated MOSAIC clip mask reproduces LR's cleanliness (fringe-sat 0.408→0.179 ≈ D 0.167). Tier-1's post-demosaic 0.99 threshold structurally misses interpolation-smeared partial clips → the production fix needs the mask from the mosaic. Slot 5 of the TARGET draft; implement post-lock |
| False-colour suppression at/after demosaic (mechanism B) | canon schemes now read [SRC 2026-06-11]: **dt** `color_smoothing` (demosaicing/basics.c:91): per pass, for R and B, 9-median of (chan−G) over 3×3 then chan = max(0, med+G), 1–5 passes; **dcraw/libraw** `median_filter` (postprocessing_aux.cpp:246): identical class — 3×3 9-median on R−G and B−G, `med_passes` iterations, post-demosaic pre-highlight-blend; **RT** `processFalseColorCorrection` (rawimagesource.cc:2982): per step, 9-median on YIQ I/Q + 3×3 box blur, Y untouched; **libraw FBDD** (dcb_demosaic.cpp:814): PRE-demosaic — green spline + impulse clamp + 2×LCh cross-pattern chroma outlier smoothing (ratio<0.85 test) | **CONFIRMED gap [EMP]; scheme canon CONVERGES** — three engines do post-demosaic 3×3 chroma-difference median (keep G/Y, median the chroma signal, N passes). Our insufficient probe medianed the wrong representation. TARGET slot 6; implement post-lock |

## Adjudicated divergences (the ledger)

1. **WB vs demosaic order — BUG → fixed 2026-06-10.** We demosaiced the
   un-balanced mosaic; every engine scales first. Fix: pre-scale + divide-
   back on all paths. Evidence: `tools/h1_wb_demosaic_ab.py`,
   `tests/fixtures/evidence/h1/` (cyan P99.5 188→87 vs LR 84.5; shipped
   pipeline arm ≡ target arm). CLAIMS.md "WB-before-demosaic fix SHIPPED".
2. **Mask-exposure units + domain — BUG → fixed 2026-06-10.** EV/4
   serialization applied ×1 post-curve; correct is ×4 scene-referred.
   Evidence: `tools/cal_deflicker_factor.py`, CLAIMS.md "Exact
   mask-exposure factor". Post-fix sequence: mean ΔE 0.62 vs approved
   JPGs, drift eliminated.
3. **Blown-highlight rendering on the libraw path — divergence, judged
   acceptable-for-now.** Canonical scale-then-clip lands blown pixels at
   neutral white (dcraw behaviour); Adobe *reconstructs* partial clips
   (gym max-ΔE 13.6 at 0.006 % px). Resolution belongs to the highlight-
   reconstruction row above, after the lock.

## TARGET architecture v2 — the justification ledger

**Owner sign-off basis (2026-06-11): the owner signs the LEDGER, not the
diagram.** v1 was refused for cause: no per-slot narrative, no stated
strategy, unjustified structures left in place. Every slot below carries
its references (battle-tested pipelines / specs), its empirical evidence
(our checked-in experiments), a verdict, and — where the verdict is
UNKNOWN — the experiment that would settle it. An unjustified slot is
UNKNOWN, never default-OK (anti-drift rule 9).

### The governing strategy

1. **North star:** owner-judged quality ≥ the Lightroom export on the
   production sequences, with every LRT-keyframed per-frame parameter
   applied, Lightroom-free in production.
2. **Default to the convergent canon.** Where dcraw/libraw, RawTherapee,
   darktable, Adobe, and the ISP literature AGREE on a structure
   (linearize → WB → demosaic → colour transform → tone → output;
   exposure-class gains scene-referred), we adopt it. Convergence across
   independently-evolved, battle-tested engines is the strongest available
   prior.
3. **Diverge only with both a reason and evidence.** A divergence needs a
   product rationale (e.g. the scene-linear tap-7 master) AND a measured
   demonstration it is safe/superior on the pressure suite + real frames.
4. **Where the canon splits, an experiment decides.** Engines disagree on
   highlight-reconstruction placement; no amount of reading settles it —
   prototypes scored against the articles + the LR-product anchor do.
5. **Where we have neither references nor evidence, say UNKNOWN** and
   either run the probe (CAL-pattern single-variable exports; article
   iteration) or state why it can wait.

### The ledger

**Slot 1 — decode, linearization, black subtract (sensor space).**
VERDICT: JUSTIFIED. References: unanimous canon (every engine; DNG 1.7.1
spec; K&B stage 1) [SRC/LIT]. Evidence: gym 0.023 ΔE vs dng_validate;
flatpatches 0.15–0.18 ≈ Adobe-ref [EMP]. Nothing open.

**Slot 2 — raw CA correction (mosaic domain).** VERDICT: IMPLEMENTED,
OPT-IN. References: RT `CA_correct_RT` and dt `cacorrect@5.0`
(highlights@4 → cacorrect@5 → demosaic@8; dt's file IS RT's
vendored) both pre-demosaic [SRC]; dcraw none; LR
off in production (census [EMP]). 2026-06-10 forensics refuted lens CA
for the clusters measured then; the 2026-07-06 owner round re-opened the
slot: boundary-trusting reconstruction ingests clip-edge CA fringe
(CLAIMS rows; `hl_edge_chroma_research_2026-07-06.json`) and the canon
carries a raw-CA stage we lacked. **PORTED 2026-07-07 [SRC+EMP]:
clean-room Martinec** (`_ca_correct.py`; dt-vs-RT divergences adjudicated
in the module docstring), wired at dt's slot on the CFA paths;
`--ca-correct N` (default 0 = OFF, all outputs byte-identical). Evidence:
`ca_correct_<date>.json` (`tools/ca_correct_experiment.py`); contracts
`tests/test_ca_correct.py`; owner flips `verify-2026-07-07/ca-flip/`.
Owner eyes 2026-07-07: "CA-on is better in all cases" (rank-1).

**Slot 3 — white balance, applied ONCE, before demosaic.** VERDICT:
JUSTIFIED; **MIGRATED 2026-06-11 — the wart is gone.** The divide-back
shim and the Stage-2 re-multiply are deleted: the decode returns BALANCED
camera RGB (CFA paths return the scaled-mosaic demosaic directly; the
libraw path rescales by the scalar `wb_mul.min()` to land on the same
G-normalised scale), `apply_adobe_pipeline` consumes balanced input (the
no-FM ColorMatrix branch folds `diag(asn/asn_G)` into its matrix), the
MLX twin drops `diag(wb)` from its fused matrix, and `highlight_recovery`
operates in balanced space (neutral = [1,1,1]) driven by the new
mosaic-derived clip mask (`pipeline._mosaic_clip_mask` — sensor-truth
sites, per-channel, 2-dilated; the fringe-forensics lesson). Re-pinned
after migration: gym gate IDENTICAL (mean 0.0230 / P95 0.1931 / max
13.578 / 99.90 % <1.0 — the shim was mathematically exact, as predicted),
full suite green, pressure suite + production spot-check below.
References: unanimous canon — dcraw `scale_colors` → interpolate [SRC],
darktable temperature@3 → demosaic@8 [SRC], RT scaleColors bakes
camera-WB into rawData pre-demosaic with the develop DELTA post-demosaic
[SRC 2026-06-11 local — nuance resolved, see the RT flowchart]. RT is
thus the one engine that SPLITS WB; we follow dt/dcraw (full render WB
once, pre-demosaic) — justified by H1: our pre-scale uses the exact
render WB, so directional algos see exactly-balanced channels, whereas
a split leaves the develop-vs-camera ratio unbalanced at demosaic time
— precisely the regime our clipbars_coolwb article measures (3.38 vs
1.16 residual when the conditioning WB ≠ render WB) [EMP].
ISP literature [LIT]. Evidence: H1 single-variable
A/B (cyan P99.5 188→87, two independent demosaics; shipped arm ≡ target
arm) [EMP]. **The wart (owner-flagged):** today we scale the mosaic,
demosaic, DIVIDE BACK, and re-multiply at Stage 2 — WB on both sides of
the demosaic. That divide-back is a **migration shim, not architecture**:
it preserved the "unbalanced camera RGB" interface that stages 1.5–2
(highlight recovery, the MLX entry, tests) consume, so the H1 fix could
ship without rewiring them. It is mathematically exact on the default
path (linear ops telescope; the common-white clip commutes through the
divide/re-multiply pair), but it is structurally indefensible as a
target. TARGET: WB applied once at the mosaic; downstream stages consume
BALANCED camera RGB; Stage 2 reduces to identity and is deleted; the
recovery threshold and MLX contract migrate with it (recovery's detection
must move to the mosaic mask anyway — see slot 5). Migration is
mechanical; scheduled with slot-5 work.

**Slot 4 — demosaic (after WB, before colour transform).** VERDICT:
JUSTIFIED (position — unanimous canon [SRC]); **algorithm choice OPEN.**
Evidence: menon/rcd at or near best-engine on bars/zoneplate/slantededge/
clipbars [EMP]; owner eyes: menon ≫ bilinear [EMP]. Open: diagbars 34.2
vs LR-product 13.6 — the algorithm (or a complementary suppression
stage, slot 6) has a product-anchored 2.5× gap. Plan: post-lock
iteration against diagbars; candidates: RCD parameter work, AMaZE-class
port (clean-room), or accepting menon + slot-6 suppression (RT rcd.cc /
amaze.cc now locally readable at ~/src-reading for learn-not-vendor
study [SRC 2026-06-11]). Default-flip decision (linear→menon) is
owner's, post-lock.
**Diagonal-gap measurements 2026-06-11 [EMP]:** slot-6 suppression
closes diagbars 34.2 → 23.6 (3 passes+blur) with ΔL nearly unmoved —
the remainder is interpolation-direction error, not chroma. libraw
alternatives disqualified (AHD: bars ΔL ×10 regression; DCB worse
everywhere — `diagbars_libraw_algos_2026-06-11.json`).
**PORTED 2026-06-12 [EMP+SRC]: clean-room AMaZE**
(`lrt_cinema/_amaze_demosaic.py`, from dt's scalar source,
read-to-learn) — diagbars 34.2 → 15.6 raw → **7.22 with fc-suppress 3,
beating the LR-product anchor (13.6) and canonical dt-AMaZE inside dt
(9.73)**; clipbars 0.01; bars/slantededge ≈0; noisebars 4.15 with fc3
(LR 4.25); zoneplate unchanged (structural — phase 0 proved no
demosaic method touches it; ACR's mechanism remains out of scope).
Gym sanity: amaze 0.433 vs menon 0.509 against dng_validate (closer to
Adobe on the real frame). `--demosaic amaze` = display/clip-path only
(single uniform clip point assumed; headroom master keeps menon).
**CLOSED 2026-07-06 [EMP]: amaze = the CLI demosaic DEFAULT.** Numba
twin landed BIT-EXACT vs the numpy spec (max|Δ|=0 full-frame incl.
borders) at 0.33 s/24 MP — 52.6× the numpy twin, meeting the owner's
1/50th directive (`amaze_numba_2026-07-06.json`). Default decision ran
owner-authorized pre-registered criteria (`seq_spot_amaze_2026-07-06`):
spot ΔE vs the LRT product ≤ menon on every frame (0.582/0.567/0.579
vs 0.586/0.572/0.584), gains identical, render 16.5 vs 25.0 s/frame —
amaze wins every criterion. Native-res flips for owner verification:
`~/lrt-cinema-fixtures/verify-2026-07-06/amaze-flip/`. render_frame
keeps 'linear' (gym gate byte-stable).

**Slot 5 — highlight handling.** Two distinct sub-questions:
- **5a, the fallback (no reconstruction): clip-to-common-white at the
  scaled mosaic. VERDICT: JUSTIFIED, owner-directed.** References: dcraw/
  libraw highlight=0 semantics [SRC+EMP probe]. Evidence: clipbars
  falsecolor 17.5→1.12 (at libraw 0.88, BEATS LR-product 3.34); clipramp
  3.03 — best smooth-clip of all engines tested; no magenta-band
  inheritance; real-frame forensics confirm mechanism-A clusters gone
  [EMP]. The documented cost: >1-multiplier channels lose top highlight
  detail to the clamp (dcraw's own trade) — recovered only by 5b.
- **5b, reconstruction placement: UNRESOLVED — the canon splits, and the
  local source pass sharpened the split [SRC 2026-06-11]:** darktable's
  DEFAULT mode is `opposed` (enum 5, magic 0.995), PRE-demosaic on the
  WB'd mosaic (iop/hlreconstruct/opposed.c); RawTherapee's current
  "Inpaint opposed" is the **same darktable algorithm vendored verbatim**
  (hilite_recon.cc:1232, "taken from darktable") but run POST-demosaic
  on the RGB planes. One algorithm, two engine-shipped placements — the
  deciding experiment can therefore isolate PLACEMENT with the algorithm
  held constant. The algorithm [SRC, both engines]: per-pixel opposed
  estimate = 3×3 neighbourhood per-channel means → cube-root → for
  channel c, refavg = 0.5·(sum of other two channels' cube-root means) →
  cube back to linear; global per-channel chrominance offset = mean of
  (value − refavg) over unclipped near-clip pixels (0.2·clip < v < clip,
  inside a dilated 3×3-superpixel clip mask; min 100 samples);
  reconstruction: out = max(in, refavg + chrominance[c]) at clipped
  sites only. dt's higher-end modes (guided-laplacian, segmentation)
  are rated best-quality in dt's own code but are iterative multi-scale
  machines — scoped OUT of this round (cost ≫ opposed; the production
  failure modes the anchors measure are smooth/large-area clips where
  opposed is the engines' chosen default). dcraw `recover_highlights`
  (post-demosaic coarse ratio-map growth) and legacy RT
  `HLRecovery_inpaint` (multi-scale directional fill) read and recorded;
  neither is any engine's current default. LR: in-render, best measured
  smooth-clip (clipramp clip-zone 1.07) [EMP]. Our Tier-1 (post-demosaic,
  0.99 threshold) is measurably blind to interpolation-smeared partial
  clips [EMP: fringe forensics; G≈F]. DECIDING EXPERIMENT (post-lock,
  autonomous; owner addendum "best-known current methods both sides" =
  opposed, per above): (i) opposed PRE-demosaic on the WB-scaled mosaic
  (dt placement), (ii) opposed POST-demosaic driven by the MOSAIC-derived
  clip mask (RT placement + our mechanism-A mask lesson) — score on
  clipramp/clipfield/clipbars against the LR anchor (targets ≈1.07 /
  ≈0.01 / ≤1.12) + owner eyeball on real blown windows. Until decided,
  5a is the shipped behaviour (the owner's "clean pipeline either way"
  rationale).
  **EXPERIMENT RUN 2026-06-11 (suite half) — pre-registration caught our
  expectations:** P1 REFUTED — pre-opposed WORSENS clipramp clip-zone
  (3.03→3.52; opposed preserves the ramp's tint where ACR rolls toward
  neutral); P2 CONFIRMED — post (21.5) worse than pre (17.2) on clipbars
  falsecolor, and BOTH are catastrophically worse than the 5a clip
  default (1.12): the headroom decode re-exposes channel-disparate clip
  plateaus at detail scale and reconstruction cannot undo
  demosaic-invented chroma; P3 SPLIT — clipfield pre = exact parity
  (0.008), post regresses (0.57). Suite verdict: **5a clip default
  STANDS on every product-anchored target; IF reconstruction ships, the
  mosaic placement (dt) dominates the RGB placement (RT) on every
  measured article.** Metric blind spot recorded: the articles score
  against a sensor-clipped reference, so RECOVERED REAL DETAIL scores as
  error — the owner-eyeball half (native-res flips on the real blown
  windows, `verify-2026-06-11/hl-flip/`) decides whether opposed
  recovers anything worth shipping. Evidence:
  `tests/fixtures/evidence/hl_reconstruct_5b_2026-06-11.json`
  (`tools/hl_reconstruct_experiment.py`); clean-room implementation
  `lrt_cinema/_opposed_reconstruct.py` (unit-tested).
  **OWNER VERDICT IN (2026-06-12, rank-1): no visible gain from either
  placement; both create MORE colour artifacts. 5a clip default
  CONFIRMED from both directions; reconstruction arms stay lab code.**
  **SLOT 5b CLOSED (owner, 2026-06-12): clip-to-common-white is the
  decided highlight handling; `_opposed_reconstruct` is retained as lab
  code (the truth harness proves it recovers linear signal — a future
  method with competent chroma handling may clear the bar). Successor
  work item, owner-commissioned: survey current industry-best
  reconstruction methods + implementation feasibility against this
  project's constraints (clean-room, licence, 24 MP × 250-frame
  runtime, TEMPORAL STABILITY for timelapse, numpy/numba stack).**
  **Survey #1 candidate EXECUTED same day (`reconstruct_mosaic_neutral`,
  clamp-then-lift): zero display-path change by construction (recovered
  luminance sits above the common white; the display transfer clamps
  there — the real-frame flip is byte-identical), while the linear
  domain measurably recovers (truth rel_mae 0.186→0.111). Matches
  ACR's own coupling of recovery to negative-exposure pulls. The 5a
  clip default is OPTIMAL for the current production; neutral recovery
  activates with EXR masters or exposure-pulled sequences.**
  **Survey #2 EXECUTED 2026-07-06 (`_segbased_reconstruct`, dt's
  segmentation class, clean-room): best LINEAR recovery on file —
  truth rel_mae 0.186 → 0.0903 (candidates) → 0.0749 (+adapt
  rebuild), beats opposed's 0.111 — but its opposed BASE inherits
  the detail-scale invented-chroma class (clipbars falsecolor 17.16
  ≈ opposed's 17.2; S2 refuted), so the 5a display default STANDS.
  The designated recovery arm for EXR masters/exposure-pulled
  renders; owner flips incl. a −1 EV pull set at
  `verify-2026-07-06/segbased-flip/`; evidence
  `hl_segbased_2026-07-06.json`.**
  Follow-up shipped same day (owner request): the held-out-truth
  harness (`tools/hl_truth_harness.py`) makes any future reconstruction
  parameter deterministically judgeable — band-clipped real mosaic +
  unclipped analytic fields as hidden truth, linear-domain `rel_mae`
  (pre-opposed measurably recovers: −52 % on real partial clips) with
  the pressure falsecolor metrics as the display-side guard. The two
  families bracket the visual trade; no eyes needed in the loop.

**Slot 6 — false-colour suppression (after demosaic, before colour
transform). NEW SLOT. VERDICT: NEEDED; scheme canon READ and CONVERGENT
[SRC 2026-06-11].** Three engines converge on the same post-demosaic
scheme — preserve the luma-bearing signal, median the chroma signal,
iterate: **dt** `color_smoothing`: for c∈{R,B}, 3×3 9-median of (c−G),
c = max(0, med+G), 1–5 passes (demosaicing/basics.c:91); **dcraw/libraw**
`median_filter`: identical (R−G, B−G, 3×3, `med_passes`)
(postprocessing_aux.cpp:246); **RT** `processFalseColorCorrection`:
9-median on YIQ I/Q + 3×3 box blur per step, Y untouched
(rawimagesource.cc:2982). libraw FBDD is the pre-demosaic outlier
(LCh impulse/chroma clamp coupled to DCB helpers) — not chosen.
ACR internal (inferred: zoneplate 0.02 vs our 0.41) [EMP]. Evidence for
need: zoneplate/noisebars/diagbars gaps are all chroma-dominated [EMP];
our earlier probe medianed the wrong representation (fringe forensics)
[EMP].
**IMPLEMENTED + ITERATED 2026-06-11** (`lrt_cinema/_fc_suppress.py`,
clean-room; passes sweep + RT-blur variant, pre-registered):
noisebars 7.98 → **4.36** at 3 passes+blur (**LR-product ≈4.25 class
reached**; beats dt's own 5.1 anchor); diagbars 34.2 → 23.6 (31 %
closed; ΔL barely moves — the remainder is the demosaic's, slot 4);
bars 1.15 → 1.11 and slantededge 0.004-class IMPROVE (zero resolution
cost — P4 confirmed; G/luma untouched by construction). **P1 REFUTED
honestly, twice**: zoneplate is INERT under both the pure median (0.414
→ 0.410) and the +blur variant (→ 0.393) — its invented chroma is
low-frequency banding, not impulses or alternating-phase texture; ACR's
0.02 is demosaic-internal (frequency-aware) and OUT OF REACH for this
scheme class. Recommended setting: **3 passes + blur** (the measured
Pareto point; pure-median dcraw arm remains in the module). Wired:
`render_frame(fc_suppress=N)` + CLI `--fc-suppress N` — **off by
default, owner-gated**; on/off owner flip at
`verify-2026-06-11/fc-flip/`. Evidence:
`tests/fixtures/evidence/fc_suppress_slot6_2026-06-11.json`.

**Slot 7 — scene-referred exposure block (linear camera RGB, before the
colour transform).** Sub-slots:
- **Mask/local EVs (deflicker/HG/global masks) ×4: JUSTIFIED.** Evidence:
  CAL calibration k\*=3.992±0.027, ΔE 0.20/0.44 at 4.0 exactly; the
  post-curve domain provably cannot fit LR at ±1–2 EV [EMP]. References:
  exposure-class ops are scene-referred in every engine that has them
  (dt exposure@21 [SRC]; ISP literature [LIT]).
- **Global `Exposure2012`: SCENE-REFERRED — measured 2026-06-11.**
  CALEXP single-variable probe (owner exports, Exposure2012 = 1.0/2.0):
  current post-curve arm FAILS (ΔE 2.84/5.85, midtone gain wrong by
  17 %/53 %); scene-pure-gain and ExposureRamp arms are IDENTICAL to
  three decimals and both land at the base-look floor (ΔE 0.187/0.287,
  gain 1.0000/0.9995). Verdict: same domain as the mask EVs; the
  pre-registered B-vs-C highlight distinction did not materialise on
  this content (recorded honestly), so implementation folds
  `Exposure2012` into `scene_exposure_ev` (one machinery). **Migrated
  2026-06-11 with the slot-3 batch**: `render_frame` (and the MLX twin)
  apply `2^(scene_exposure_ev + exposure_ev)` pre-colour-transform;
  Stage 11 = Blacks only. Zero production urgency (Exposure2012 = 0 in
  the sequence). Evidence:
  `tests/fixtures/evidence/cal_exposure_domain_2026-06-11.json`
  (`tools/cal_exposure_domain.py`).

**Slot 8 — colour transform + Adobe shaping chain (ForwardMatrix → HSM →
ExposureRamp → LookTable → ProfileToneCurve).** VERDICT: JUSTIFIED.
References: DNG 1.7.1 §Mapping Camera Color Space (implemented
clause-by-clause); canonical position (colour transform after demosaic,
tone after colour) unanimous [SRC/LIT]. Evidence: gym 0.023; flat-patch
≈0; the entire Phase-0/1 validation lineage [EMP]. The tap-7 EXR master
exits before LookTable/curve — a deliberate, documented divergence
(scene-linear product goal), gated by the Phase-1f Resolve test.

**Slot 7b — scene-referred LOCAL Highlights/Shadows translation
(`scene_tone.apply_scene_hlsh`), after the slot-7 gain.** VERDICT:
MEASURED + CALIBRATED (round-2 CAL probes, 2026-07-07). Scene domain
wins (chroma discriminator); Shadows strongly local, Highlights
moderately; the op = guided base/detail + calibrated curve family +
near-black chroma roll — a translation with a measured residual, not
Adobe fidelity (their math is closed Local-Laplacian class). Both
intents; byte-exact no-op at zero (production H/S = 0). Numbers +
evidence: CLAIMS round-2 rows (`tools/cal_domain_round2.py`,
`tools/cal_hlsh_fit.py`).

**Slot 9 — tone-domain develop ops (Contrast2012, parametric ToneCurve).**
VERDICT (2026-07-07 probes): Contrast2012 GLOBAL, weak display-domain
lean; luminance arms leave ΔC 1.3–1.4 → per-channel class placement
stands, but our LAW is wrong (+50: ΔE 3.26; −50: 17.23) — recalibration
against the CALCON exports is a queued lever. Zero in production.

**Slot 10 — colour-domain develop ops (HSL, ColorGrade, Sat/Vib).**
VERDICT (2026-07-07): split — `apply_hsl` VALIDATED at the base-look
floor (CALHSLBLU 0.181); `apply_color_grade` REFUTED (CALCGSH 17.9,
ΔC 37; queued lever). Sat/Vib unprobed. Zero/constant in production.

**Slot 11 — NR + capture sharpening (detail stage).** VERDICT: placement
plausible (ACR's detail stage is post-colour [LIT]; dt denoiseprofile
post-demosaic [SRC]) — parameters UNVALIDATED. Evidence: production XMPs
carry Sharpness 25 + ColorNR 25; we render neither by default; they are
part of the measured ~0.6 ΔE base-look floor [EMP]. Probe: single-variable
Sharpness/CNR exports + the noisebars article.

**Slot 12 — output transform + encode.** VERDICT: JUSTIFIED. References:
colour-space allowlist code-enforced; sRGB/Rec.709 delivery per LRT
round-trip requirement. Evidence: oracle tests; owner-run LRT ingest
round-trip [EMP].

### Lock state

The TARGET v2 ledger was ACCEPTED by the owner 2026-06-11 (tentative,
evidence-gated — CLAIMS "ARCHITECTURE LOCK" row); every one-time blocker
(slot-7 verdict, 5b design, slot-3 plan) is DONE. Open slots proceed by
their written deciding experiments.

## The iteration substrate: pressure-test articles

`tools/test_articles/` — analytic scenes (ISO-12233-class constructions)
mosaicked onto real D750 DNGs with sensor-accurate clipping. Epistemics
(owner audit 2026-06-10): the scenes are CONSTRUCTION truth (externally
verifiable mosaic contents), the harness "expected" is internal (isolates
the front-end), and external authority comes from independent engines
rendering the same files (dng_validate — NB its reference demosaic is
bilinear; libraw's own pipeline) compared on truth-anchored INVARIANTS
(chroma invented where the scene is neutral; clip-zone chroma). The
`clipbars` article reproduces the production blinds failure in one number —
front-end changes iterate against these before any owner eyeball.

## Post-lock queue (state 2026-07-07)

Items 1–6 of the 2026-06-11 queue are DONE (source pass, Exposure2012
probe, archive audit, slot-3 migration, slot-5b experiment, slot-6
suppression — evidence rows in CLAIMS.md). Owner-gated remainder (never
autonomous): any default flip (reconstruction/CA default-on), the
EXR/Resolve gate. Open levers from the round-2 probes: faithful Contrast
law, ColorGrade law, Blacks shape, Whites calibration (CLAIMS rows).
