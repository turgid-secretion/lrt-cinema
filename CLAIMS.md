# CLAIMS.md — the authoritative claim ledger

Owner-approved: 2026-06-10

**This file outranks every other prose surface in this repo** (docs/, memory,
commit messages, docstrings). Those surfaces accumulated confident falsehoods
during the May–June 2026 LLM sprint; the full audit is in the repair plan
(owner's copy) and the failure post-mortem will live in `docs/archive/`.

Rules: a claim enters as VERIFIED only with a regeneration command + artifact;
numbers expire to STALE after 30 days unless re-run; REFUTED claims stay listed
(with their refuting evidence) so they cannot be re-derived from old prose.

Statuses: **VERIFIED(date)** · **UNVERIFIED** (plausible, not yet re-run) ·
**HYPOTHESIS** (proposed explanation, experiment pending) · **BLOCKED(needs X)**
· **REFUTED(evidence)**.

## Render correctness (stages 1–9 vs Adobe ground truth)

| Claim | Status | Regenerate / evidence |
|---|---|---|
| Gym end-to-end mean ΔE2000 = **0.0262** vs dng_validate (P50 0.000, P95 0.320, max 2.06, 99.99% px <1.0) | **VERIFIED 2026-06-10** | `python3 -m pytest tests/test_pipeline.py::test_ship_gate_gym_de_under_1` against `~/lrt-cinema-fixtures/DSC_4053_dngvalidate.tif` (recipe in FIXTURES.md). Gate had been **dormant** (fixture evaporated from /tmp); resurrected today |
| Synthetic flat-patch gate: neutral median ~0, chromatic mean ~0.05 | **VERIFIED 2026-06-10** | `python3 -m pytest tests/test_synthetic_dng.py` (2 passed, 35s) |
| Rose mean ΔE = 0.545 | **BLOCKED(needs rose.dng)** | Fixture missing from this machine; restore `rose.dng` + regen `rose_dngval_Camera_Standard.tif` per FIXTURES.md, or strike the number |
| Full pipeline byte-exact at zero sliders | **UNVERIFIED for stages 1–9** | Stage-12 ops have explicit byte-exact identity tests; stages 1–9 zero-op identity has NO direct test → Phase-1a adds one |
| Test suite: **577 passed, 3 skipped**, ruff clean (post-purge) | **VERIFIED 2026-06-10** | `python3 -m pytest -q && python3 -m ruff check .` (126s). Delta vs the 573/4 pre-purge baseline is fully explained: +3 context-budget guard tests, +1 gym end-to-end gate flipped SKIP→PASS (fixture resurrected). Render behavior unchanged (gym ΔE identical 0.0262). Historical "250 tests green" and "485 tests" both wrong |
| Phase-0/0.5 purge was behavior-neutral | **VERIFIED 2026-06-10** | Gym gate 0.0262 before and after; suite green; changes were prose/fixture-path/guard-test only |
| WIP env-gates (`LRT_CINEMA_B1`, `LRT_CINEMA_CHROMA_MED`) are inert by default | **VERIFIED 2026-06-10** | Both gates default-off; gym gates green with the wiring committed (`9daed8f`) |

## The look gap (vs LRTimelapse / Lightroom output)

| Claim | Status | Regenerate / evidence |
|---|---|---|
| **North-star JPGs were rendered by LRTimelapse Pro 7.5.3's INTERNAL engine — NOT Lightroom/ACR** | **VERIFIED 2026-06-10** | EXIF `Software` tag on `LRT_00001.jpg` (now in `~/lrt-cinema-fixtures/production/lrt-jpg/`). The entire first-sprint "ours≈dng_validate≠ACR mystery" framing had the wrong renderer on the reference side. OPEN OWNER DECISION: target look = LRT-internal (what was approved) or Adobe-canonical? |
| Production XMPs (DSC_4053–4302): **ALL global develop sliders are ZERO** | **VERIFIED 2026-06-10** | Raw-XML census (NOT via our parser): `tests/fixtures/evidence/xmp_census_2026-06-10.json`. H/S/W=0, Exposure/Contrast/Blacks=0, HSL/ColorGrade=0, ToneCurve='Linear'. Active intent: WB 4034K/+20 constant, Sharpness 25, ColorNR 25, deflicker EV ±0.048/+0.076. Colorist masks "LRT Mask 1–5" carry zero adjustments |
| "The look gap is the dropped Highlights/Shadows/Whites sliders" (this repair's own leading hypothesis) | **REFUTED 2026-06-10** | The census: those sliders are zero in production. Pre-registration caught our own tidy narrative |
| Look-gap re-attribution: LRT-internal engine's BASELINE rendering (its own base tone/color treatment + Sharpness 25 + CNR 25 + 8-bit JPEG) differs from the Adobe-canonical baseline we implement | **HYPOTHESIS (now leading)** | Decisive test: fresh LR Classic export of the same XMPs should land near OUR render (we match Adobe at 0.026), both differing from the LRT JPG by the same signature. With all sliders zero + constant WB, the residual should be ONE fixed global transform — fittable from 250 frame pairs, no closed-source reverse-engineering needed |
| Exact ACR match is achievable | **REFUTED(by construction)** | Adobe PV2012 Highlights/Shadows are local-adaptive (Local-Laplacian class, per Adobe docs) — moot for this sequence anyway (sliders zero) |
| Deflicker `--deflicker-scale 1.0` is correct (the "~3×" was a scalar-gain conflation) | **UNVERIFIED (methodology sound)** | Decisive new test queued: correlate per-frame gain (seq compare) against the per-frame deflicker EV delta — the 0.96→1.10 "drift" range ≈ 2^(±deflicker EV) range, suspicious |
| LRT ingests our `LRT_*.tif` 16-bit sequence (round-trip premise) | **VERIFIED 2026-06-10 (owner-run)** | Owner rendered the 250-frame lrt-cinema TIFF sequence through LRTimelapse Pro 7.5.3 → `tif_H265-444_Rec.709L_OriRes_59.94_UHQ.mov` on the drive. The product's existential premise is demonstrated (TIFFs were a pre-repair build; re-confirm after any emission change) |
| Owner's RT refuting experiment parameters | **VERIFIED 2026-06-10** | `~/lrt-cinema-fixtures/production/rt-experiment/DSC_4053.NEF.pp3`: Temperature=4039 Green=1.057, demosaic Method=rcdvng4 — RT at the cool develop WB with RCD-class demosaic, no cyan. Anchor observation for H1–H4 |
| Frame mapping LRT_00001 ↔ DSC_4053 | **VERIFIED 2026-06-10** | Identical EXIF DateTimeOriginal (2026:05:20 08:46:53) on both |

## The cyan/blinds artifact

| Claim | Status | Regenerate / evidence |
|---|---|---|
| "Fundamental demosaic false-color floor, amplified by cool WB; engine swap wouldn't help" | **REFUTED(owner experiment, 2026-06-10)** | Owner ran RawTherapee at the cool develop WB, multiple algorithms incl. bilinear + RCD, same raw: **no artifacts**. The repo's claim was extrapolated from OUR menon, never tested in RT (see header on `docs/archive/research/alt-raw-engine-feasibility.md`) |
| H1: artifact root cause = we demosaic BEFORE WB scaling (RT/LR/libraw scale first); cool WB amplifies blue-channel error | **HYPOTHESIS (leading)** | Phase-1e A/B: pre-scale CFA by develop WB → demosaic → unscale; compare blinds crop |
| H2: our clean-room demosaic ports are buggy (vs algorithm class) | **HYPOTHESIS** | Same pre-scaled CFA → our RCD vs BSD menon vs libraw |
| H3: post-demosaic stage amplifies (HSM/LookTable/WB math/clip) | **HYPOTHESIS** | `stop_after_stage` taps on the blinds crop; find first stage where cyan appears |
| H4: input-path difference (dnglab DNG vs NEF-direct) | **HYPOTHESIS** | Same render, both input paths |
| Chroma-median mitigation (~40% reduction) is the right fix | **SUSPENDED pending root cause** | Built for the refuted diagnosis; fate decided by the H1–H4 verdict |

## Environment & strategy facts

| Claim | Status | Regenerate / evidence |
|---|---|---|
| "No local validation possible: no dng_validate, no DCP, no LR" (old memory) | **REFUTED 2026-06-10** | All three present: `~/lrt-cinema-fixtures/dng_validate`, `/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Nikon D750/`, `/Applications/Adobe Lightroom Classic` |
| "Perceptual ops still alias faithful" (old memory + cli.py docstring) | **REFUTED** | `develop_ops.py`: `_apply_hsl_perceptual` (OKLCh), `_apply_color_grade_perceptual` (ACEScct CDL), `apply_dr_compression`, `apply_texture_clarity` are real distinct implementations |
| "LRT only provides deflicker" | **REFUTED(owner)** | LRT = per-frame keyframed editing of the full develop-parameter set; no downstream substitute (Resolve keyframes its own grade, not raw develop intent) |
| "No engine reads Adobe crs: intent" | **OVERSTATED** | LRT's own internal export applies simple edits (8-bit JPG, documented inferior — exactly this project's niche) |
| `apply_dr_compression` Whites slider matches LR's direction | **REFUTED(code)** | `develop_ops.py` docstring: "+Whites darkens — the inverse of Lightroom"; constants self-described as "best-effort tuning" → Phase-1c calibration required before any decomposition use |
| ~1.0s/frame render perf (numba) | **UNVERIFIED** | One timed full-res graded render |
| Clean-room RCD provenance (no GPL contamination) | **UNVERIFIED (self-attested)** | One-time spot-check vs RawTherapee `rcd.cc` constants |
| Repo redistributes extracted Adobe profile data (`tests/fixtures/dcp_data/*.npz`) | **VERIFIED — live legal exposure** | Phase-4: purge from git history (owner confirms), replace with synthetic DCP fixture |
| EXR tap-7 master is demonstrably more gradable than the 16-bit TIFF | **UNVERIFIED** | Phase-1f Resolve capability gate; owner judges |
