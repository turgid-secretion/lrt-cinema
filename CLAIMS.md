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
| B2 audit: "deflicker scale 1.0 confirmed correct; the '~3×' was a scalar-gain conflation artifact" | **REFUTED 2026-06-10** | Three independent measurements: (1) per-frame gain vs deflicker EV across 250 frames: **r = +1.000**, slope +1.66 output-domain (vs r=0.75 for scene brightness — kills the "it's the tone curve" alternative); (2) three-point arms on frame 250 (EV +0.072): off→gain 1.124, 1.0×→1.081, **3.1×→1.006 (flat)**, ΔE 2.73/2.30/**1.49**; (3) full-sequence 3.1× confirmation: **gain flat 1.005→1.007 across all 250 frames (was 0.941→1.082), sequence mean ΔE 1.20→0.89, worst frame 2.45→1.58, zero color cast**. The dead sprint's original "~3× under-application" was approximately RIGHT; the audit's conflation critique was methodologically fair but killed a true signal. Evidence: `tests/fixtures/evidence/seq_lrt_compare_scale{1.0,3.1}_2026-06-10.json` |
| **Lightroom ITSELF applies ≈3–4× the serialized `LocalExposure2012` — our mask-exposure emulation has a REAL BUG (not a look choice)** | **VERIFIED 2026-06-10 (LR-arbiter test, owner exports)** | Fresh LR Classic 14.5.1 16-bit exports of DSC_4053 (EV −0.048) + DSC_4302 (EV +0.072): LR lands on our 3.1×-arm at BOTH frames incl. the sign flip (gain vs our 1×: 0.937 / 1.082; vs our 3.1×: 1.004 / 1.008). ΔE keeps improving to 4.0–4.5× (clip-biased LSQ gain understates; perceptual optimum ≥4.0). Leading mechanism: XMP stores EV/4 (local slider spans ±4 EV; normalization publicly undocumented). **Exact factor: calibration experiment prepared** (`~/lrt-cinema-fixtures/production/calibration/CAL{025,050}_4053.*` — known 0.25/0.50 values, owner exports, brightness ratio = the multiplier). Default stays 1.0 until the factor is exact; then this becomes a code fix, not a knob |
| LRT-internal engine ≈ Lightroom/ACR on this sequence — the "which look" dilemma dissolves | **VERIFIED 2026-06-10** | LRT-JPG vs fresh LR TIFF: gain 1.002/0.999, ΔE 0.585/0.508 on the two test frames — within ~the 8-bit JPEG floor + sharpening/CNR differences. The owner-approved look IS (near enough) the Adobe look; our 16-bit chain at corrected deflicker sits 0.54–0.97 ΔE from LR |
| North-star sequence gap (mean ΔE 1.20, drift 0.94→1.08, worst 2.4 at sequence end) is dominated by the deflicker channel; residual base-look floor ≈0.7 ΔE at matched brightness | **VERIFIED 2026-06-10** | seq compare at scale 1.0: ΔE minimum 0.68 exactly where gain crosses 1.0 (frame ~125). Remaining floor = LRT-internal base look + Sharpness 25 + CNR 25 + JPEG, at 6× downsample |
| Current-vs-old-export render difference: mean ΔE 3.66, **constant chromatic** (gain R 0.94 / G 1.01 / B 0.87, stable f1→f250) | **MEASURED 2026-06-10, UNEXPLAINED** | Old drive TIFFs carry the same stale version string ("0.7.1a0") — undatable, flags unknown. Exposure-class ruled out (G≈1); it's a WB/color-class difference. Root-cause alongside H1–H4 (same "what changed when" family). Process lesson: the never-bumped version number makes artifacts undatable |
| LRT ingests our `LRT_*.tif` 16-bit sequence (round-trip premise) | **VERIFIED 2026-06-10 (owner-run)** | Owner rendered the 250-frame lrt-cinema TIFF sequence through LRTimelapse Pro 7.5.3 → `tif_H265-444_Rec.709L_OriRes_59.94_UHQ.mov` on the drive. The product's existential premise is demonstrated (TIFFs were a pre-repair build; re-confirm after any emission change) |
| Owner's RT refuting experiment parameters | **VERIFIED 2026-06-10** | `~/lrt-cinema-fixtures/production/rt-experiment/DSC_4053.NEF.pp3`: Temperature=4039 Green=1.057, demosaic Method=rcdvng4 — RT at the cool develop WB with RCD-class demosaic, no cyan. Anchor observation for H1–H4 |
| Frame mapping LRT_00001 ↔ DSC_4053 | **VERIFIED 2026-06-10** | Identical EXIF DateTimeOriginal (2026:05:20 08:46:53) on both |

## The cyan/blinds artifact

| Claim | Status | Regenerate / evidence |
|---|---|---|
| "Fundamental demosaic false-color floor, amplified by cool WB; engine swap wouldn't help" | **REFUTED(owner experiment, 2026-06-10)** | Owner ran RawTherapee at the cool develop WB, multiple algorithms incl. bilinear + RCD, same raw: **no artifacts**. The repo's claim was extrapolated from OUR menon, never tested in RT (see header on `docs/archive/research/alt-raw-engine-feasibility.md`) |
| **H1 CONFIRMED: the cyan root cause is demosaicing the UN-white-balanced CFA** (we pass unit WB to libraw and feed raw mosaic to rcd/menon; Stage 2 scales after — RT/LR scale first) | **VERIFIED 2026-06-10** | Single-variable A/B (`tools/h1_wb_demosaic_ab.py`, develop WB 4034K/+20, artifact region auto-located by cyanness(ours)−cyanness(LR)): saturated-cyan tail (P99.5×1000) rcd 187.7→**87.2**, menon 197.8→**94.5**, vs **LR-Classic 84.5** — WB-before-demosaic lands us ON Adobe's level (mean 10.98 vs LR 10.93). Evidence: `tests/fixtures/evidence/h1/` |
| H2: our clean-room demosaic ports are buggy | **REFUTED as cause** | The independent BSD menon shows the identical artifact and identical H1 response — input conditioning, not implementations |
| H3: post-demosaic stage amplifies / H4: input-path difference | **MOOT** | Artifact forms at demosaic (H1); cool WB amplifies as understood, downstream stages and input path are not the cause |
| "Fundamental demosaic false-colour floor — all engines have it; switching wouldn't help" (dead-sprint narrative) | **DEAD** | The floor exists, but our artifact sat 2.2× above it due to the H1 ordering bug; correctly conditioned we sit at ACR's floor. Owner's RT instinct was right |
| Architecture gate input: bespoke raw front-end viability | **BESPOKE SURVIVES, STRENGTHENED** | Root cause = discrete fixable ordering defect, not a diffuse quality gap. Phase-3 fix: pre-scale CFA by WB at demosaic (all paths incl. libraw user_wb), re-pin gates after |
| Chroma-median mitigation (~40% reduction) + B1 module | **OBSOLETE pending H1 fix** | Built for the misdiagnosis; delete after the WB-ordering fix ships and the blinds crop is owner-verified |

## Environment & strategy facts

| Claim | Status | Regenerate / evidence |
|---|---|---|
| "No local validation possible: no dng_validate, no DCP, no LR" (old memory) | **REFUTED 2026-06-10** | All three present: `~/lrt-cinema-fixtures/dng_validate`, `/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Nikon D750/`, `/Applications/Adobe Lightroom Classic` |
| "Perceptual ops still alias faithful" (old memory + cli.py docstring) | **REFUTED** | `develop_ops.py`: `_apply_hsl_perceptual` (OKLCh), `_apply_color_grade_perceptual` (ACEScct CDL), `apply_dr_compression`, `apply_texture_clarity` are real distinct implementations |
| "LRT only provides deflicker" | **REFUTED(owner)** | LRT = per-frame keyframed editing of the full develop-parameter set; no downstream substitute (Resolve keyframes its own grade, not raw develop intent) |
| "No engine reads Adobe crs: intent" | **OVERSTATED** | LRT's own internal export applies simple edits (8-bit JPG, documented inferior — exactly this project's niche) |
| `apply_dr_compression` Whites slider matches LR's direction | **REFUTED(code)** | `develop_ops.py` docstring: "+Whites darkens — the inverse of Lightroom"; constants self-described as "best-effort tuning" → Phase-1c calibration required before any decomposition use |
| ~1.0s/frame render perf (numba) | **VERIFIED 2026-06-10: 1.31 s/frame** | 250 production frames in 5:27 wall (numba, ~7 cores, while other analysis ran). Perf stays on the kill list |
| "RawTherapee macOS CLI is sandbox-broken" (old memory) | **CORROBORATED IN EFFECT 2026-06-10 (mechanism unknown)** | Bundled `rawtherapee-cli` exits 0 emitting nothing and writing nothing (even `--help`/no-args); a signature-stripped user-space copy gets SIGKILLed by Gatekeeper (and triggered the "damaged" dialog; copy deleted, original untouched — its imperfect codesign seal is likely pre-existing, common for RT community builds). Our quarantine hypothesis: REFUTED. RT replication path = GUI session with owner (pp3 params captured) |
| Clean-room RCD provenance (no GPL contamination) | **UNVERIFIED (self-attested)** | One-time spot-check vs RawTherapee `rcd.cc` constants |
| Repo redistributes extracted Adobe profile data (`tests/fixtures/dcp_data/*.npz`) | **VERIFIED — live legal exposure** | Phase-4: purge from git history (owner confirms), replace with synthetic DCP fixture |
| Owner repeatedly requested an externally-anchored reference pipeline model; **no such artifact was ever produced** | **VERIFIED 2026-06-10** | Grep across all live + archived docs: zero matches for reference-model/ISP-canon/scale_colors content. The order audit that exists (`pipeline-order-audit.md`) is real but **internally framed** (no cross-engine column; demosaic row descriptive only) — and its F3 correctly flagged the ~3× deflicker under-delivery, which the later B2 audit then destroyed. Deliverable queued: `docs/REFERENCE_PIPELINE.md`, cross-engine comparative (dcraw/libraw, RT, darktable, Adobe SDK, ISP literature), every divergence JUSTIFIED/SUSPECT/BUG |
| Agreement with a single reference (incl. Adobe dng_validate) = correctness | **REFUTED — the trap that hid the WB-ordering bug** | The gym gate certified demosaic-before-WB because Adobe's reference demosaic is insensitive to it (bilinear commutes with per-channel WB; directional algorithms don't). Anti-drift rule added to CLAUDE.md |
| EXR tap-7 master is demonstrably more gradable than the 16-bit TIFF | **UNVERIFIED** | Phase-1f Resolve capability gate; owner judges |
