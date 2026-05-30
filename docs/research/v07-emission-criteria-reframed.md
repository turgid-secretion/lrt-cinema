# Emission criteria reframed: data preservation, not Camera Raw knobs

**Status:** Analysis, 2026-05-27. Re-evaluates the v0.7 format choice
under a user-clarified criterion.
**Parent:** [v07-emission-format.md](v07-emission-format.md) (current
SPEC), [v07-vs-v06-emission-comparison.md](v07-vs-v06-emission-comparison.md)
(comparison doc).

---

## 1. The reframe

The original v0.7 brief was read as "small files + Camera Raw-style
re-developability." The empirical Q1.0 spike landed on a v0.7 product
that gets WB and exposure as Camera Raw knobs but loses the LR PV2012
tone / sat / vib / contrast ops via metadata path. The user's reaction:

> Most parameters end up baked, some lost, only exposure and Kelvin
> adjustability gained. Wash or close lose for CDNG.

The clarified criterion:

> What I actually care about is whether we throw away data in the
> baking process that's unrecoverable. If the Adobe Color curve /
> contrast causes highlights to clip or blacks to be crushed, can I
> recover these post-facto in Resolve? Whether it is RAW / RAW-like /
> lossy / lossless is irrelevant — small size, fast encode, easy
> emission, versatile across NLEs.

**This is a different criterion.** Re-developability via decode-parameter
overrides ≠ data preservation. The two correlate but they're not the
same. The format answer changes accordingly.

Definitions for the rest of this doc:

- **Recoverable data:** the stored representation preserves enough bit
  depth + dynamic-range headroom that a downstream tool can invert /
  undo the pipeline operations that were applied at emit time. If the
  tone curve clipped a linear value of 8.0 to a stored value of 1.0
  with no overrange storage, that pixel is *unrecoverable*. If the
  tone curve mapped 8.0 to a stored value of 0.95 in a float container
  that also preserved the 8.0 above-white-point value, recoverable.
- **Re-developable:** the format carries metadata that the decoder
  reads as decode-time parameters (WB, exposure, tone curve), giving
  the user a UI knob to override them. CDNG-via-Resolve gives WB and
  exposure this way for v0.7; tone curve no.

The two relate but aren't equivalent. A format can be recoverable
without being re-developable (raw float pixels with no decode metadata
= EXR scene-referred linear) and vice versa (BRAW = re-developable but
lossy on highlights past a certain point).

---

## 2. Quick answer on CDNG encode time

The 1.5–2.5 s/frame estimate for `cinema-cdng` was using stock
libjpeg-92 (Adobe DNG Converter ships this). Realistic speedups:

| Encoder | Estimated time per 24 MP Bayer frame | Vs stock libjpeg |
|---|---:|---|
| libjpeg (stock) | 1500–2500 ms | 1.0× |
| libjpeg-turbo (SIMD) | **500–800 ms** | 2–3× |
| `pylibjpeg-libjpeg` (libjpeg-turbo binding) | 500–800 ms | 2–3× |
| GPU (Metal / CUDA, fastvideo / nvjpeg) | <100 ms | >15× |
| DNG 1.7 + JPEG XL (libjxl) | 200–400 ms | 4–8× (also better compression) |

With libjpeg-turbo, v0.7 cdng encode lands at **500–800 ms/frame**,
which is in the same ballpark as v0.6 cinema-aces (~400 ms). Not free
but no longer a deal-breaker. The CDNG encode-speed regression is
**fixable** without changing the format.

That said — the format choice has bigger axes than just speed. Below.

---

## 3. The decoupled decisions

Two independent decisions sit under "what does v0.7 emit":

### Decision A — Where in the pipeline do we emit?

The v0.6 pipeline has 13 stages. We can emit at any of them. Choice
of stage determines what's *baked in pixels* vs *available downstream*:

| Stage | What's applied | What overranges survive | LRT intent captured |
|---|---|---|---|
| 0 (source Bayer) | nothing | sensor's 14-bit raw | only WB choice (via metadata) |
| 1 (post-demosaic) | demosaic only | sensor's 14-bit linear | nothing |
| 4 (post-ProPhoto) | demosaic + WB + CCM + color space | scene-referred float | WB (Holy Grail K) only |
| 7 (post-ExposureRamp) | + HSM + exposure ramp (Adobe + LRT) | mild highlight roll-off applied | + LRT Exposure2012 |
| 9 (post-LookTable, pre-ProfileToneCurve) | + LookTable | overranges still alive | + LookTable |
| 11 (post-LR Exposure/Blacks/ToneCurvePV2012) | + LR PV2012 tonal ops | tone curve may clip highlights | + all LR PV2012 tonal |
| 13 (current v0.6) | + Saturation / Vibrance / Contrast / ProPhoto→Rec.2020 | clipped at tone curve | + everything LRT authored |

Recoverability is a monotonic function: **earlier stage = more
recoverable, less LRT intent baked.**

### Decision B — What container / compression?

Container determines size, speed, NLE coverage, and what numeric
precision survives. Independent of where we emit. Same options apply
to any pipeline stage's output.

The cross-product of A × B is the decision surface.

---

## 4. Exhaustive option scan

All viable containers for emitting Stage-4-through-Stage-13 output.
Sizes assume 24 MP frame (6032 × 4032).

| # | Container | Bit depth | Compression | Lossy? | Size/frame | Encode time | Resolve | Premiere | FCP | Avid | OSS encoder | Notes |
|---|---|---|---|---|---:|---:|---|---|---|---|---|---|
| 1 | **TIFF 32-bit float (uncompressed)** [v0.6 cinema-linear] | 32-bit float | none | no | ~292 MiB | 300 ms | yes | yes | yes (Codec Converter) | partial | tifffile (BSD) | baseline |
| 2 | TIFF 32-bit float ZSTD | 32-bit float | ZSTD | no | ~100–140 MiB | 400 ms | yes (read), partial (write) | yes | partial | partial | tifffile 2023+ (BSD) | recent codec; check Resolve ingest |
| 3 | TIFF 16-bit int | 16-bit int | LZW or deflate | no | ~140 MiB | 250 ms | yes | yes | yes | yes | tifffile | clips overranges |
| 4 | **EXR 32-bit float PIZ** [v0.6 cinema-aces] | 32-bit float | PIZ (lossless) | no | ~100–150 MiB | 400 ms | yes | yes | yes (3rd-party) | yes (via plugin) | OpenEXR (BSD) | baseline |
| 5 | **EXR 16-bit half PIZ** | 16-bit half | PIZ (lossless) | no | ~50–80 MiB | 300 ms | yes | yes | yes (3rd-party) | yes | OpenEXR | drops to half precision |
| 6 | **EXR 16-bit half ZIP** | 16-bit half | ZIP | no | ~40–70 MiB | 350 ms | yes | yes | yes | yes | OpenEXR | standard cinema compress |
| 7 | **EXR 16-bit half ZSTD** (EXR 3.x) | 16-bit half | Zstandard | no | ~30–60 MiB | **250 ms** | check 20.3 | check | check | check | OpenEXR 3.3+ | newer; ingest verify needed |
| 8 | **EXR 16-bit half DWAB** | 16-bit half | DWAB (lossy) | yes (visually lossless) | **~10–25 MiB** | **400–600 ms** | **yes** | yes | yes (3rd-party) | partial | OpenEXR | **cinema scene-referred standard** |
| 9 | **EXR 32-bit float DWAB** | 32-bit float | DWAB (lossy) | yes | ~20–40 MiB | 600–900 ms | yes | yes | yes | partial | OpenEXR | more headroom than half |
| 10 | EXR 16-bit half B44A | 16-bit half | B44A (lossy) | yes | ~50 MiB (fixed 2.3:1) | 300 ms | yes | yes | yes | partial | OpenEXR | older lossy; not as efficient |
| 11 | **CinemaDNG (Bayer + LJPEG)** [v0.7 current] | 14-bit Bayer | LJPEG-92 | no (lossless sensor) | **~15–25 MiB** | **500–800 ms** (with libjpeg-turbo) | yes | yes (with plugin) | partial | no | tifffile + libjpeg-turbo (BSD) | re-developable in Camera Raw |
| 12 | CinemaDNG + JXL (DNG 1.7) | 14-bit Bayer | JPEG XL lossless | no | ~5–15 MiB | 200–400 ms | uncertain May 2026 | uncertain | no | no | libjxl + tifffile | bleeding edge |
| 13 | **ProRes 4444 XQ** | 12-bit RGB int (or 10-bit via FFmpeg) | ProRes wavelet | yes (visually lossless) | ~10–20 MiB | **400–600 ms** | yes | yes | **yes (native)** | **yes (native)** | FFmpeg `prores_ks` (LGPL) | log-encode required to preserve HDR; 12-bit native via Apple, 10-bit via FFmpeg |
| 14 | ProRes 4444 | 10-bit RGB int (FFmpeg) | ProRes wavelet | yes | ~6–12 MiB | 400 ms | yes | yes | yes (native) | yes (native) | FFmpeg | lower bitrate than XQ |
| 15 | **CineForm RGB 16-bit** | 16-bit RGB int | wavelet | yes (visually lossless) | ~30–80 MiB | ~500 ms | yes | yes | partial | yes | GoPro CineForm SDK (Apache/MIT) | log-encode required |
| 16 | DPX 16-bit | 16-bit RGB int | none / RLE | no | ~140 MiB | 200 ms | yes | yes | yes | yes | OpenImageIO | cinema legacy, large |
| 17 | DPX 10-bit log | 10-bit RGB int | none | yes (quantisation) | ~30 MiB | 150 ms | yes | yes | yes | yes | OpenImageIO | classic cinema-DI exchange |
| 18 | DNxHR 444 | 12-bit YUV 4:4:4 | DNxHD wavelet | yes (visually lossless) | ~15–25 MiB | 400 ms | yes (native) | yes | yes (with plugin) | yes (native) | FFmpeg dnxhd | Avid's intermediate |
| 19 | JPEG XL float HDR (single image) | 32-bit float | JXL (lossy or lossless) | optional | 5–25 MiB (lossless) / 1–8 MiB (lossy) | 200–400 ms | not in 20.3 (planned 21?) | no | no | no | libjxl (BSD) | great codec, sparse NLE |
| 20 | AVIF HDR | 12-bit YUV | AV1 (lossy or lossless) | optional | 3–15 MiB lossy | 600 ms | not in 20.3 | no | no | no | libaom | sparse NLE |
| 21 | HEIF HDR | 10/12-bit YUV | HEVC | lossy | 5–20 MiB | 500 ms | partial | partial | yes | partial | libheif | iOS-leaning |

---

## 5. Score against the user's criteria

Criteria (user-stated, May 2026):

1. **Small size** — the CDNG win was a big draw.
2. **Fast encode** — CDNG regression matters.
3. **Recoverable** — highlight + black recovery in Resolve.
4. **Easy emission** — well-documented, open libraries, existing converters.
5. **NLE versatility** — Resolve primary, but also Premiere / FCP / Avid useful.

Scoring matrix (★★★ = excellent fit, ★ = poor):

| Option | Size | Encode speed | Recoverable | Easy emit | NLE versatility | Total |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **8. EXR half DWAB** | ★★★ | ★★★ | ★★★ | ★★★ | ★★★ | **15★** |
| **13. ProRes 4444 XQ** | ★★★ | ★★★ | ★★ | ★★★ | ★★★ | **14★** |
| **11. CinemaDNG (v0.7)** | ★★★ | ★★ | ★★ (sensor DR only) | ★★ | ★★ | **11★** |
| **7. EXR half ZSTD** (lossless) | ★★ | ★★★ | ★★★ | ★★ (Resolve ingest TBV) | ★★★ | **13★** |
| **6. EXR half ZIP** (lossless) | ★★ | ★★ | ★★★ | ★★★ | ★★★ | **13★** |
| **9. EXR float DWAB** | ★★ | ★★ | ★★★ | ★★★ | ★★★ | **13★** |
| **15. CineForm RGB** | ★★ | ★★★ | ★★ (log-encoded) | ★★★ | ★★ | **12★** |
| **18. DNxHR 444** | ★★★ | ★★★ | ★★ | ★★ | ★★ (Avid lean) | **12★** |
| 12. CDNG + JXL | ★★★ | ★★★ | ★★ | ★ (TBV) | ★ (Resolve TBV) | 10★ |
| 2. TIFF float ZSTD | ★ | ★★ | ★★★ | ★★★ | ★★ | 11★ |
| 1. TIFF float (v0.6) | — | ★★ | ★★★ | ★★★ | ★★ | 9★ |
| 4. EXR PIZ (v0.6 aces) | ★ | ★★ | ★★★ | ★★★ | ★★★ | 12★ |

**Top result: EXR half-float DWAB.** Hits ★★★ on every axis except
"recoverable" caveat (half-float is theoretically slightly less
recoverable than 32-bit float at extreme overranges; in practice
half-float carries 30 stops of headroom — more than the 14 stops the
sensor produces). It's the cinema industry's default scene-referred
compressed intermediate.

ProRes 4444 XQ comes very close. Loses on theoretical recoverability
(12-bit log integer vs half-float) but wins on Avid + FCP native
support if those matter.

---

## 6. The pipeline-stage choice

EXR half DWAB at Stage 13 (current v0.6 output) vs Stage 9
(pre-tone-curve) vs Stage 4 (pre-LookTable / pre-exposure-ramp):

| Emit stage | LRT-authored ops preserved | Overranges preserved | Recoverability |
|---|---|---|---|
| Stage 13 (current v0.6) | all (baked) | none — tone curve clipped >1.0 | low — tone curve clipped highlights are gone regardless of bit depth |
| Stage 11 (post LR Exposure/Blacks/ToneCurve) | + LR exposure + blacks + tone curve | mostly clipped | partial |
| Stage 9 (post LookTable, pre tone curve) | + LookTable | full (tone curve not applied) | **full HDR** |
| Stage 7 (post exposure ramp, pre LookTable) | + LRT Exposure2012 | partial (exposure ramp has mild roll-off) | full HDR with mild compression |
| Stage 4 (post ProPhoto) | + Holy Grail K only | full | **maximum HDR** |

The user's stated criterion ("if the tone curve causes highlights to
clip, can I recover them?") implies emitting **before** the tone curve.
Stage 9 is the natural place — DCP colour science applied (good!),
LookTable applied (LRT look intent baked in pixels), no tone curve
yet.

What's lost by emitting at Stage 9 vs Stage 13:

- LR PV2012 `ToneCurvePV2012` — user re-applies in Resolve as a grade
  curve. v0.7 CDNG already drops this.
- LR PV2012 `Saturation` / `Vibrance` / `Contrast2012` — user
  re-applies. v0.7 CDNG drops these.
- LR PV2012 `Exposure2012` — could either ride as metadata sidecar OR
  be baked into the exposure ramp at Stage 7 (preserves overranges
  since exposure ramp uses TotalBaselineExposure with float values).

So Stage 9 emission **loses no more LRT intent than v0.7 CDNG already
does**, and gains the recoverability the user is asking about.

(Where v0.7 CDNG and Stage-9-EXR diverge: CDNG gives Resolve's Camera
Raw panel WB and exposure knobs; EXR doesn't. EXR users adjust WB and
exposure in the colour grade page, which is arguably more powerful
anyway.)

---

## 7. Three concrete v0.7 architecture options

### Option α — **CinemaDNG (current SPEC, accelerated)**

Pipeline: NEF → adobe-convert → DNG → Bayer-bake mask deltas → write
tags → libjpeg-turbo lossless JPEG-92 → emit `.dng`.

- Size: 15–25 MiB / frame
- Encode: 500–800 ms / frame (with libjpeg-turbo)
- Recoverability: 14-bit Bayer DR (~13 stops), no tone curve baked
  (because the file ships pre-pipeline). Resolve applies its bundled
  DCP + ProfileToneCurve.
- Re-developability: WB + exposure overridable in Camera Raw panel.
- LR PV2012 tone / sat / vib / contrast: **dropped** (per Q1.0 spike).
- NLE: Resolve, Premiere (CDNG plugin), partial elsewhere.

### Option β — **EXR half-float DWAB at Stage 9 (pre-tone-curve)**

Pipeline: NEF → … → Stage 9 output (linear ProPhoto, post-LookTable,
pre-ProfileToneCurve) → ProPhoto→Rec.2020 → write EXR half DWAB.

- Size: 10–25 MiB / frame
- Encode: 400–600 ms / frame
- Recoverability: **full HDR float, 30 stops of headroom in half-float,
  no tone curve baked**. User picks tone shape in Resolve grade page.
- Re-developability: no Camera Raw knobs; grade-page knobs only.
- LR PV2012 tone / sat / vib / contrast: **dropped** (same as α).
- NLE: **universal** — Resolve, Premiere, AE, FCP (via Codec Converter
  or Pro Codec plugin), Nuke, Houdini, OCIO pipelines. The cinema
  scene-referred standard.

### Option γ — **EXR half-float DWAB at Stage 13 (current v0.6 output)**

Pipeline: same as v0.6 cinema-aces, just swap PIZ → DWAB.

- Size: 10–25 MiB / frame
- Encode: 400–600 ms / frame
- Recoverability: **same as v0.6 cinema-aces** — half-float preserves
  what the pipeline didn't clip, but tone curve already clipped
  highlights >1.0 before emit. So highlight recovery is limited to
  what was below the tone curve's roll-off.
- Re-developability: no Camera Raw knobs.
- LR PV2012 tone / sat / vib / contrast: **all baked in pixels.**
- NLE: universal.

The 1-line swap (PIZ → DWAB in `output.py`) gives a 10–18× size win
over v0.6 cinema-aces immediately, with full LRT intent preserved. The
only loss is theoretical bit-exactness (PIZ is lossless; DWAB is lossy
visually-lossless). For practical timelapse work, indistinguishable.

---

## 8. The honest comparison: α (CDNG) vs β (EXR-DWAB-at-Stage-9) vs γ (EXR-DWAB-at-Stage-13)

| Criterion | α CDNG | β EXR DWAB / Stage 9 | γ EXR DWAB / Stage 13 |
|---|---|---|---|
| Size | 15–25 MiB | 10–25 MiB | 10–25 MiB |
| Encode time | 500–800 ms (with libjpeg-turbo) | 400–600 ms | 400–600 ms |
| Resolve ingest | native CDNG | native EXR | native EXR |
| Premiere / AE | CDNG plugin | native EXR | native EXR |
| FCP / Avid | partial | Codec Converter / plugin | Codec Converter / plugin |
| Highlight recovery (above tone curve) | full to sensor's 14-bit limit | **full to half-float limit** | **none — tone curve already clipped** |
| Shadow lift (deep blacks) | partial — depends on Resolve's bundled DCP | full | partial |
| WB / Kelvin overridable | **yes (Camera Raw panel)** | grade-page only | grade-page only |
| Exposure ramp overridable | **yes (Camera Raw panel)** | grade-page only | grade-page only |
| LR PV2012 tone curve preserved | no (dropped) | no (dropped) | **yes (baked)** |
| LR PV2012 sat / vib / contrast | no (dropped) | no (dropped) | **yes (baked)** |
| DCP colour science | Resolve's bundled DCP | **ours (validated < 1 ΔE)** | **ours (validated < 1 ΔE)** |
| Code added | ~600–900 LOC (cdng_emit) | ~50 LOC (pipeline branch + EXR DWAB writer arg) | **~5 LOC (PIZ → DWAB swap)** |
| New deps | libjpeg-turbo, exiftool | none (OpenEXR already in tree) | none |
| New failure surface | tag write, LJPEG, WhiteLevel rescale | none | none |

---

## 9. Recommendation against user criteria

Given the user's stated criteria — small size, fast encode, recoverable
data, easy emission, NLE versatility, format type irrelevant — the
ranking changes from v0.7 CDNG to:

1. **β EXR DWAB / Stage 9** wins on the user's stated criteria. Full
   recoverability, similar size, faster encode, universal NLE, tiny
   code change. The only loss vs CDNG is the Camera Raw panel knobs
   (which the user has now explicitly de-prioritised).
2. **γ EXR DWAB / Stage 13** is the fastest possible pivot: a 5-line
   change in `output.py` (PIZ → DWAB, optionally promote to half-float
   if not already). 10–18× compression vs v0.6 cinema-aces, full LRT
   intent preserved, encode is the same speed as v0.6. **Doesn't
   address the "highlight recovery" criterion** but ships everything
   else. Strong v0.7 candidate if the user wants the LR PV2012 ops to
   keep working.
3. **α CDNG** retains its appeal only if the Camera Raw panel knobs
   matter. Same compression as β/γ, slower encode (even with
   libjpeg-turbo), more code, more failure surface.

### Honest split for the user to decide

| If your priority is… | Pick |
|---|---|
| Smallest possible diff from v0.6, preserve all LRT intent | **γ** (EXR DWAB at Stage 13) |
| Highlight / shadow recoverability (the criterion you just stated) | **β** (EXR DWAB at Stage 9) |
| Camera Raw panel WB / exposure knobs in Resolve | **α** (CDNG, accelerated) |
| Future JXL upgrade path | **α** (CDNG + JXL) when Resolve catches up |
| Open all NLEs maximally | **β** or **γ** (EXR is the cinema universal) |

### The hybrid

These options aren't mutually exclusive. lrt-cinema could expose
multiple presets:

- `cinema-linear-master` → β (Stage 9 EXR DWAB) — for cinema masters
  with downstream tone shaping
- `cinema-linear-finished` → γ (Stage 13 EXR DWAB) — for direct-grade
  cinema deliverables
- `cinema-cdng` → α (CDNG) — for users who want Camera Raw knobs

The cost of supporting all three is small once the EXR DWAB writer
exists (≈ 50 LOC for the Stage 9 emission path). v0.7's preset list
expands rather than swaps.

---

## 10. Implementation effort summary

### γ (PIZ → DWAB swap)

- 1 hour. Single CLI flag exposed (`--exr-compression {piz,dwab}`),
  default flipped to DWAB. Update CHANGELOG, README, SCOPE.md.
- New validation gate: ΔE2000 < 0.5 between PIZ and DWAB output on
  the test scenes (visually lossless gate).
- No new dependencies.

### β (Stage 9 EXR emission, new preset)

- 1–2 weeks. New emission point added to `pipeline.py` (or a sibling
  function) returning Stage 9 output as linear ProPhoto → Rec.2020.
  `output.py` gains DWAB writer parameter.
- New preset `cinema-linear-master` wired through CLI.
- New validation gate: round-trip Stage 9 → DWAB → decode → re-apply
  tone curve in test rig → ΔE2000 vs v0.6 reference frame.
- No new dependencies.

### α (CDNG, accelerated)

- 4–6 weeks per current v0.7 plan + libjpeg-turbo integration
  (~3 days additional).
- All previously-identified validation gates.
- New deps: libjpeg-turbo (via `pylibjpeg-libjpeg` or direct ctypes).

### Three-preset combo

- ~3 weeks total if done in parallel. Final preset matrix:

```
cinema-linear         → v0.6 32-bit float TIFF (kept for back-compat)
cinema-aces           → v0.6 32-bit float EXR PIZ (kept for back-compat)
cinema-linear-master  → β Stage 9 EXR half-float DWAB
cinema-aces-master    → β Stage 9 EXR half-float DWAB tagged ACES IDT
cinema-linear-finished → γ Stage 13 EXR half-float DWAB (default; replaces cinema-linear over v0.8)
cinema-cdng           → α CDNG (optional; for Camera Raw workflows)
```

`cinema-linear-finished` becomes the new default. `cinema-cdng` is an
opt-in. Old `cinema-linear` / `cinema-aces` are deprecated through
v0.8 with warning.

---

## 11. Verdict (the user's reframed criteria, applied)

The original v0.7 SPEC (CDNG) was correct under the *Camera Raw knobs*
reading of the goal. Under the *data preservation* reading, **the SPEC
should be revised to make EXR DWAB the primary v0.7 emission** —
either at Stage 9 (β) for the recoverability story, or at Stage 13
(γ) for the minimal-diff path that preserves all LRT intent.

CDNG should ship as a sibling preset rather than the headliner —
addressing the specific Camera Raw use case for users who want it.

The pivot is small: EXR is already in the tree, OpenEXR DWAB is a
one-line compression flag, the v0.6 pipeline already produces
half-float-compatible data. Compared to the ~600–900 LOC cdng_emit.py
the SPEC currently demands, this is a 50–200 LOC change.

---

## 12. Sources

- [OpenEXR Codecs explained — LearnVFX](https://www.learnvfx.com/p/openexr-codecs-explained)
- [Linear EXR workflows and recommended DWA compression — Render Network](https://know.rendernetwork.com/getting-started/how-to-get-started/recommended-dwa-compression)
- [DWA compression in OpenEXR 2.2 — fnord software](http://fnordware.blogspot.com/2014/08/dwa-compression-in-openexr-22.html)
- [EXR: Zstandard compression — Aras Pranckevičius](https://aras-p.info/blog/2021/08/06/EXR-Zstandard-compression/)
- [Lossless Float Image Compression — Aras Pranckevičius](https://aras-p.info/blog/2025/07/08/Lossless-Float-Image-Compression/)
- [libjpeg-turbo lossless mode](https://libjpeg-turbo.org/About/SmartScale-Lossless)
- [Fast CinemaDNG lossless JPEG codec](https://www.fastcinemadng.com/info/jpeg/lossless-jpeg-codec.html)
- [FFmpeg ProRes encoder ticket #4292 (4444 XQ)](https://trac.ffmpeg.org/ticket/4292)
- [Apple ProRes — Wikipedia](https://en.wikipedia.org/wiki/Apple_ProRes)
- [GoPro CineForm SDK](https://github.com/gopro/cineform-sdk)
- DaVinci Resolve 20.3 Reference Manual, on-disk at `/Applications/DaVinci Resolve/DaVinci Resolve Manual.pdf`, pp. 466 (Camera Raw Decoding), pp. 178 (CinemaDNG), pp. ~3500 (Optimised Media / Cache "Preventing Clipping" recommendation).
