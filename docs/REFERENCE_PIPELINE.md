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

### RawTherapee **[PEND source read; EMP anchor]**

Empirical anchor (owner-run, 2026-06-10): RT at the cool develop WB
(4034 K), multiple demosaics including RCD and bilinear, shows **no cyan
artifact** on the production raw — consistent with WB-before-demosaic.
The pp3 is preserved at `production/rt-experiment/DSC_4053.NEF.pp3`.
Reading `rtengine/rawimagesource.cc` for the absolute chain is
next-session work.

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
| Global `Exposure2012` (13) | scene-referred (expected: same machinery class as local) | **SUSPECT** [PEND] — zero in production, latent. Probe: CAL-style single-variable XMPs at Exposure ±1/±2, owner exports, same harness |
| `Blacks2012` (13) | unknown | [PEND] — same harness |
| `Contrast2012`, ToneCurve (14) | tone-domain by nature; LR's pivot/space unknown | [PEND] — these may be *correctly* post-curve; needs the probe before touching |
| HSL / ColorGrade / Sat / Vib (14) | color-domain; LR space unknown | [PEND] — lower risk (zero or constant in production), audit after exposure class |
| Sharpness / NR | ACR detail stage | [PEND] — part of the ~0.6 base-look floor |
| Highlight reconstruction (5) | Adobe reconstructs; darktable does it PRE-demosaic on the mosaic; dcraw post-demosaic | **BLOCKED on this lock** (owner): placement decides implementation |

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

## Next session (the architecture lock, in order)

1. RawTherapee + LibRaw source pass → upgrade [PEND]/[LIT] tags to [SRC];
   fill the RT flowchart.
2. The global-`Exposure2012` domain probe (CAL harness, owner exports).
3. Declare the TARGET architecture: one flowchart, every op with an
   absolute slot and a domain, each slot justified by canon or by a
   measured LR-match.
4. Migration plan for the ⚠ region + highlight-reconstruction placement.
5. Only then: re-enable/raise anything gated on architecture (highlight
   recovery default, EXR tap policy, demosaic default).
