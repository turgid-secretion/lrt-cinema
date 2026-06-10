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
| Test suite: **573 passed, 4 skipped**, ruff clean (dirty tree, pre-purge) | **VERIFIED 2026-06-10** | `python3 -m pytest -q && python3 -m ruff check .` (101s). Historical "250 tests green" and "485 tests" both wrong |
| WIP env-gates (`LRT_CINEMA_B1`, `LRT_CINEMA_CHROMA_MED`) are inert by default | **VERIFIED 2026-06-10** | Both gates default-off; gym gates green with the wiring committed (`9daed8f`) |

## The look gap (vs LRTimelapse / Lightroom output)

| Claim | Status | Regenerate / evidence |
|---|---|---|
| North-star gap vs colorist JPGs: median ΔE 1.71, "PV2012 tone-curve-shape mystery" | **UNVERIFIED + framing CHALLENGED** | Inputs on unmounted SanDisk. Counter-analysis: faithful mode **drops the colorist's Highlights/Shadows/Whites sliders** (signature = the measured shoulder+toe) and LR bakes default Sharpening=40 + ColorNR=25 that we don't apply; historical metric is 6×-downsampled (blind to sharpening) vs lossy 8-bit JPEG → Phase-1d decomposition experiment |
| Exact ACR match is achievable | **REFUTED(by construction)** | Adobe PV2012 Highlights/Shadows are local-adaptive (Local-Laplacian class, per Adobe docs) — a global curve cannot bit-match them. Target redefined: owner's eyes vs fresh LR renders |
| Deflicker `--deflicker-scale 1.0` is correct (the "~3×" was a scalar-gain conflation) | **UNVERIFIED (methodology sound)** | B2 audit reasoning holds up; re-confirmed implicitly if the Phase-1d temporal arm collapses the 0.96→1.10 gain drift |
| LRT ingests our `LRT_*.tif` 16-bit sequence (round-trip premise) | **UNVERIFIED — never demonstrated** | 15-minute owner test: 10 frames → LRT → 1s video |

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
