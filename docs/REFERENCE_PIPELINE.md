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

### RawTherapee — `rtengine/rawimagesource.cc` **[SRC-fetch 2026-06-10]**

Single-pass web fetch of the dev-branch source (verify with a local read
before load-bearing use — the multiplier nuance below especially):

```
  preprocess():
  1. dark frame / flat field      (copyOriginalPixels)
  2. black level + scaling        (scaleColors)
  3. bad pixel interpolation
  4. raw CHROMATIC ABERRATION     ← CA_correct_RT, PRE-demosaic,
     correction                     on the mosaic
  5. green equilibration / line denoise / vignetting
  demosaic():
  6. demosaic (AMaZE/RCD/VNG4/…)
  getImage():
  7. develop-WB multipliers (rm/gm/bm via calculate_scale_mul)
  8. highlight recovery           ← HLRecovery_inpaint / _opposed,
                                    POST-demosaic
```

**Open nuance [PEND]:** which multiplier set is baked into `rawData` at
demosaic time (camera pre-mul in `scaleColors` vs none) — i.e. whether RT
demosaics camera-balanced or fully-unbalanced data — needs the local
source pass. The owner's experiment (no cyan at the cool develop WB with
RCD; pp3 at `production/rt-experiment/`) [EMP] says RT's demosaic input is
at least approximately balanced, consistent with the canon either way.

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
  6. scene_exposure_ev: 2^(4·maskEV) linear gain           [fixed 2026-06-10]
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
chain into the canonical domain. The ⚠ region is the open question below.

## The open architecture question: the develop-ops domain

Everything below the line (steps 13–14) currently operates on
**post-ProfileToneCurve, display-referred-ish ProPhoto** data. Lightroom
defines the same sliders inside its raw pipeline, where at least the
exposure class is provably scene-referred. The deflicker fix is the first
measured instance of this mismatch class; the owner's working hypothesis —
which the evidence so far supports — is that the same class affects other
ops. The audit is per-op, because the correct placement differs per op:

| Op (ours, step) | LR's placement | Status / next probe |
|---|---|---|
| Mask/local exposure (6) | scene-referred ×4 | **FIXED + verified** [EMP] |
| Global `Exposure2012` (13) | scene-referred (expected: same machinery class as local) | **SUSPECT — probe PREPARED 2026-06-10**: `production/calibration/CALEXP{100,200}_4053.{NEF,xmp}` (single-variable, Exposure2012 = 1.0/2.0, xmp-diff-verified). Owner exports 16-bit sRGB TIFFs (no resize, sharpening off) → same harness as the deflicker CAL |
| `Blacks2012` (13) | unknown | [PEND] — same harness pattern |
| `Contrast2012`, ToneCurve (14) | tone-domain by nature; LR's pivot/space unknown | [PEND] — these may be *correctly* post-curve; needs the probe before touching |
| HSL / ColorGrade / Sat / Vib (14) | color-domain; LR space unknown | [PEND] — lower risk (zero or constant in production), audit after exposure class |
| Sharpness / NR | ACR detail stage | [PEND] — part of the ~0.6 base-look floor |
| Highlight reconstruction (5) | canon SPLITS: darktable PRE-demosaic (mosaic), dcraw & RawTherapee POST-demosaic, Adobe reconstructs in-render | **BLOCKED on this lock** (owner). Our Tier-1 post-demosaic placement is RT/dcraw-consistent; but it is ~no-op on the residual clip-edge fringes (F vs G arms: 199 px > 2/255, max 4) — whatever fixes those is NOT the current Tier-1 |
| Raw CA correction (absent) | LR: available but **ZERO/off in this production sequence** (census [EMP]). RT: `CA_correct_RT` PRE-demosaic [SRC-fetch] | **CLOSED for the observed fringes** — forensics refuted lens CA (bilinear arm kills the saturation; true CA would survive it). A real CA op stays optional future work, pre-demosaic per RT |
| Partial-clip hue handling (mechanism A) | Adobe reconstructs partial clips in-render [EMP: gym census + fringe cluster 0] | **CONFIRMED defect class [EMP]** — clip-neutralization driven by a 2-dilated MOSAIC clip mask reproduces LR's cleanliness (fringe-sat 0.408→0.179 ≈ D 0.167). Tier-1's post-demosaic 0.99 threshold structurally misses interpolation-smeared partial clips → the production fix needs the mask from the mosaic. Slot 5 of the TARGET draft; implement post-lock |
| False-colour suppression at/after demosaic (mechanism B) | canon: dcraw `-m` (median passes), libraw FBDD, RT false-colour-suppression steps, ACR bakes it into its demosaic | **CONFIRMED gap [EMP]** — directional algos (menon ≈ rcd) CREATE saturated fringe at steep edges that bilinear doesn't (0.498 vs 0.231; D between at 0.308); our quick 3×3 chroma-median ×2 probe was insufficient (0.546) — the canon's actual schemes (iteration counts, spaces) need the source read. New TARGET slot between demosaic and highlight handling; implement post-lock |

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

**Slot 2 — raw CA correction (mosaic domain).** VERDICT: ABSENT,
PROVISIONALLY JUSTIFIED. References: canon SPLITS — RT (`CA_correct_RT`)
and darktable correct pre-demosaic; dcraw has none; LR offers it but the
production XMPs have it OFF (census [EMP]). Evidence: fringe forensics
REFUTED lens CA as the residual-artifact cause [EMP]. Plan: stays absent
until a real-lens article (`ca_shifted`, taxonomy v3) demonstrates need;
placement would be pre-demosaic per RT/dt.

**Slot 3 — white balance, applied ONCE, before demosaic.** VERDICT:
JUSTIFIED (position); **current IMPLEMENTATION carries a declared wart.**
References: unanimous canon — dcraw `scale_colors` → interpolate [SRC],
darktable temperature@3 → demosaic@8 [SRC], RT scales before demosaic
[EMP: owner experiment], ISP literature [LIT]. Evidence: H1 single-variable
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
port (clean-room), or accepting menon + slot-6 suppression. Default-flip
decision (linear→menon) is owner's, post-lock.

**Slot 5 — highlight handling.** Two distinct sub-questions:
- **5a, the fallback (no reconstruction): clip-to-common-white at the
  scaled mosaic. VERDICT: JUSTIFIED, owner-directed.** References: dcraw/
  libraw highlight=0 semantics [SRC+EMP probe]. Evidence: clipbars
  falsecolor 17.5→1.12 (at libraw 0.88, BEATS LR-product 3.34); clipramp
  3.03 — best smooth-clip of all engines tested; no magenta-band
  inheritance; real-frame forensics confirm mechanism-A clusters gone
  [EMP]. The documented cost: >1-multiplier channels lose top highlight
  detail to the clamp (dcraw's own trade) — recovered only by 5b.
- **5b, reconstruction placement: UNRESOLVED — the canon splits.**
  darktable: pre-demosaic on the mosaic [SRC]; RT (`HLRecovery_*`) and
  dcraw (`recover/blend_highlights`): post-demosaic [SRC]; LR: in-render,
  and its smooth-clip result (clipramp clip-zone 1.07) is the best
  measured [EMP]. Our current Tier-1 (post-demosaic, 0.99 threshold) is
  measurably blind to interpolation-smeared partial clips [EMP: fringe
  forensics; G≈F]. DECIDING EXPERIMENT (post-lock, autonomous): prototype
  BOTH placements — (i) mosaic-domain reconstruction before demosaic
  (dt-style), (ii) post-demosaic reconstruction driven by a MOSAIC-derived
  clip mask — score on clipramp/clipfield/clipbars against the LR anchor
  (targets ≈1.07 / ≈0.01 / ≤1.12) + owner eyeball on real blown windows.
  Until decided, 5a is the shipped behaviour (the owner's "clean pipeline
  either way" rationale).

**Slot 6 — false-colour suppression (after demosaic, before colour
transform). NEW SLOT. VERDICT: NEEDED, scheme UNKNOWN.** References:
canon-supported everywhere — dcraw `-m` median passes, libraw FBDD, RT
false-colour-suppression steps [SRC-fetch, scheme details pending local
read], ACR internal (inferred: zoneplate 0.02 vs our 0.41 — near-total
suppression) [EMP]. Evidence for need: zoneplate/noisebars/diagbars gaps
are all chroma-dominated [EMP]; our naive 3×3 chroma-median probe was
insufficient (fringe forensics) [EMP]. Plan: read RT/dt suppression
sources (learn, never vendor), implement clean-room, iterate against
zoneplate (→≈0.02), noisebars (→≈4), diagbars (contribution TBD).

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
  `Exposure2012` into `scene_exposure_ev` (one machinery). Migration:
  with the slot-3 batch; zero production urgency (Exposure2012 = 0 in
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

**Slot 9 — tone-domain develop ops (Contrast2012, parametric ToneCurve).**
VERDICT: UNKNOWN. The current post-ProfileToneCurve placement was
validated on LR *TIFF round-trips* — display-referred ops applied to
display-referred input — which says NOTHING about where LR applies them
inside a raw render. References: none solid (LR internal undocumented;
engines differ in tone architecture). Probe (CAL pattern): single-variable
Contrast ±50 owner exports; arms post-curve vs pre-curve application.
Until then these ops are zero in production — latent, not load-bearing.

**Slot 10 — colour-domain develop ops (HSL, ColorGrade, Sat/Vib).**
VERDICT: UNKNOWN, LOW-RISK (zero/constant in production). Same probe
pattern as slot 9, after the exposure/tone classes settle.

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

### What blocks the lock

The lock needs: (a) ~~slot-7 global-exposure verdict~~ DONE 2026-06-11
(scene-referred, measured), (b) slot-5b deciding experiment design
AGREED (not necessarily run — the 5a fallback is shipped and safe),
(c) owner acceptance of the slot-3 migration plan (WB-once), (d) this
ledger's justifications challenged and signed. Slots 9–11 are explicitly
OK-to-lock as UNKNOWN-latent: they are zero in production, carry written
probes, and do not gate other work.

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

## Next session (the architecture lock, in order)

1. LOCAL RawTherapee + LibRaw source pass → upgrade the [SRC-fetch] and
   [EMP] tags to [SRC]; resolve the RT rawData-multiplier nuance.
2. The global-`Exposure2012` domain probe — **files prepared**
   (`CALEXP{100,200}_4053`), needs the owner's two LR exports, then the
   existing harness.
3. Resolve the ? slots above; owner signs the TARGET; migration plan for
   the ⚠ region.
4. Only then: re-enable/raise anything gated on architecture (highlight
   recovery default, EXR tap policy, demosaic default — owner verdict on
   menon vs bilinear is already in: menon, post-lock).
5. Stale-prose archive audit of the four bannered docs (owner-sanctioned)
   to reclaim context budget.
