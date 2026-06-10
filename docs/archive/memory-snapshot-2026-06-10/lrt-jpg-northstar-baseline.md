---
name: lrt-jpg-northstar-baseline
description: "First LRT-JPG north-star baseline measurement (DSC_4053↔LRT_00001) + the root cause of the gap — a PV2012 tone-curve-SHAPE difference, NOT color/bug/BaselineExposure"
metadata: 
  node_type: memory
  type: project
  originSessionId: c033d948-2981-4ec4-ae88-f352a0850ff8
---

First measurement against the new north-star (LRT JPG look, not dng_validate — see [[highlight-recovery-tier1-and-stage9-clamp]] §VALIDATION REFRAME). Frame DSC_4053 ↔ LRT_00001.jpg (the one confirmed name-aligned pair), production-faithful faithful-sRGB render (temp 4034/tint 20, recovery off). Tool: `tools/diagnose_vs_lrt_preview.py` (ΔE dist + spatial heatmap + affine-fit + per-band L*a*b*).

**Baseline numbers (ours vs LRT JPG, CENTRE-CROP aligned — not the tool's default resize, which inflates edge ΔE):** raw mean ΔE 2.43 (smooth-70% 1.57, edges-30% 4.04). Monotonic tone-transfer closes the smooth-region gap: **luminance-only (hue-preserving) → ~1.11**, **per-channel → 0.85** (≈8-bit-JPEG floor). So gap = mostly luma-tone + a small ~0.26 per-channel/color-tone component. Post-tone edge residual ~2.07 (sharpening + sub-pixel/JPEG artifact). Affine gain 0.83 uniform R/G/B, no cast. (Earlier resize-based numbers — raw 2.66, smooth 0.88, edge 2.94 — were edge-inflated by the LANCZOS resize; center-crop supersedes.)

**ROOT CAUSE = PV2012 tone-curve-SHAPE difference (NOT a bug, NOT color, NOT BaselineExposure).** Center-crop luma: highlights P95 ours 0.723 ≈ Adobe-baseline 0.726 > LRT 0.668; mids/shadows P50 ours 0.004 < LRT 0.016 < Adobe 0.025. So **LRT = darker highlights (shoulder rolloff) + lifted shadows (toe) = lower-contrast filmic PV2012 tone**; ours = bare DCP-baseline ProfileToneCurve (brighter highs, crushed shadows). The 0.83 linear gain = the highlight rolloff (0.925 display ≈ 0.84 linear). `final.tif` (`/tmp/dng_out/`, Software=Adobe DNG Converter, 4016×6016) is the Adobe-baseline ref.

**This CONFIRMS the validation reframe with data:** ours ≈ dng_validate (established 0.026) ≈ Adobe-baseline, but **≠ LRT in the highlights**. The 0.026 gate anchored our highlight tone to Adobe's *baseline*, not the colorist's PV2012 look. Closing the gap REQUIRES diverging from dng_validate (now authorized).

**Confirmed NOT the gap:** highlight reconstruction (windows cyan/matching), color, BaselineExposure (+0.10 EV, applied by both → cancels).

**Secondary gap:** high-contrast EDGES (heatmap red) = ACR default sharpening (our `apply_sharpness` is a no-op stub) + partly measurement artifact (LANCZOS 4032→4016 resize misreg + 8-bit JPEG ringing).

**Lever to match the look = emulate the PV2012 tone response** (highlight shoulder + shadow toe), as a develop-stage tone op that intentionally diverges from the DCP baseline. Mostly a tone-curve-shape change in the 0.3–0.95 range (largely orthogonal to the Stage-9 clamp / highlight-recovery, which only touches the truly-blown small %). Sharpening is the secondary lever.

**Caveats:** single aligned frame (multi-frame needs LRT output↔source map in `.lrt/lrtsequence.json`; sequence is constant-grade so likely representative). `final.tif` used a DIFFERENT WB than ours (Adobe default vs 4034K) → color not comparable, only desaturated-highlight luma is WB-robust; a same-WB dng_validate render would clean up the shadow comparison (no dng_validate binary locally). Diag outputs were in /tmp/hr_baseline (ephemeral).

## VALIDATION-SET UPDATE (2026-06-02) — single-frame baseline was MISLEADING; §11 static-tone-op demoted

Exact LRT↔source map via EXIF `DateTimeOriginal`: **LRT_000NN ↔ DSC_(4052+N)**, N=1..250 → DSC_4053..DSC_4302 (the export is the first 250 consecutive frames, ~08:46–09:03 morning; `holyGrail:0`, `deflickerPasses:2`). Map saved /tmp/lrt_map.json; build it by matching exiftool DateTimeOriginal (NEFs are contiguous DSC_4053..9085).

5-frame validation set (N=1,63,125,188,250) revealed the **per-frame gap GROWS** (smooth ΔE 1.39→3.26) and **frame-1's tone curve makes held-out frames WORSE** (held-out raw 2.26→2.41) → **a single static tone op does NOT generalize; §11-as-fixed-curve is the WRONG primary lever.** Root cause is a per-frame **GLOBAL BRIGHTNESS DRIFT**: affine gain **0.93 (frame1, ours brighter) → 1.14 (frame250, ours darker)**, NOT a static tone-shape, NOT the windows (frame-250 window highlights match). Driven by the **brightening morning** (raw mean 0.0365→0.0421, ~+0.2 stops) + **LRT 2-pass deflicker**.

Our parser DOES extract the per-frame deflicker delta as `mask_offsets=[('deflicker', X)]` (from `crs:LocalExposure2012`): −0.047→+0.072 across the set. **My measurement omitted it** (render_frame-direct, no `apply_lrt_mask_offsets`) → unfair baseline. Applying it helps but only ~30-40%: gain 0.93→0.96 (f1), 1.14→1.10 (f250); smooth ΔE 1.39→1.34, 3.26→3.03. **A residual ~0.14-stop drift (growing) remains** — likely the 2nd deflicker pass / a correction not in the field we read.

**Corrected plan (supersedes "build §11 next"):** (1) render the validation set via the FULL deflicker path (`apply_lrt_mask_offsets`/CLI `--apply-lrt-offsets`) for a FAIR baseline; (2) find + apply LRT's residual deflicker correction (2nd pass / source TBD) — the #1 lever for the dominant growing gap; (3) THEN re-assess the small static tone-shape residual (§11) on the deflicker-matched baseline. The validation set did its job: caught that frame-1-alone (ΔE 1.39, smallest deflicker) was misleadingly good.

## RECONCILED (2026-06-02, fair baseline WITH deflicker applied)

`apply_lrt_mask_offsets` adds the deflicker delta to `exposure_ev` (confirmed) → my `exposure_ev=delta` render is production-exact. Fair baseline (5 frames, deflicker applied):

| frame | gain | raw smooth ΔE | **per-channel self-fit smooth** |
|---|---|---|---|
| 1 | 0.961 | 1.34 | **0.85** |
| 63 | 0.993 | 1.41 | **0.87** |
| 125 | 1.025 | 1.89 | **0.84** |
| 188 | 1.057 | 2.36 | **0.85** |
| 250 | 1.099 | 3.03 | **0.85** |

**THE reconciling fact: a per-frame global tone curve flattens the gap to a ROCK-FLAT ~0.85 across the whole sequence** (vs raw 1.34→3.03). So the ENTIRE growing gap is **per-frame GLOBAL TONE** — fully closeable per-frame to the ~0.85 JPEG/edge floor. It decomposes into: **(a) a per-frame BRIGHTNESS drift** (gain 0.96→1.10, monotonic through 1.0 — LRT deflicker: we apply the parsed LocalExposure2012 part, residual ~0.14-stop@250 is LRT-internal 2-pass deflicker, the "compute deltas" pass our parser scopes OUT) **+ (b) a tone-SHAPE component** (shoulder/toe).

**§11 is RE-VALIDATED but must be per-frame-aware:** the earlier "frame-1 curve hurts held-out" was because a full bundled curve bakes frame-1's BRIGHTNESS. Decompose into **static shape (§11 shoulder/toe) + per-frame exposure (deflicker)** → reaches 0.85 everywhere.

## ROOT CAUSE FOUND (2026-06-02) — we under-apply `LocalExposure2012` ~3× (RETRACTS the "LRT-internal computation" claim)

**I was WRONG that the residual was an "LRT-internal, out-of-scope deflicker computation."** Owner's correct logic: the PRIMARY workflow is **LrC reads the LRT-modified XMP and exports** — LrC has NO access to any LRT-internal computation, so ALL adjustments MUST be in the XMP. Complete `diff` of DSC_4053.xmp vs DSC_4302.xmp confirms the ONLY differing develop field is **`crs:LocalExposure2012`** (−0.047 vs +0.072), on the `#LRT internal use (Deflicker)` mask (near-full-image gradient, MaskValue 1.0). So the data is all there — **we MISHANDLE it.**

`XMP_SCHEMA.md` admits the parser's `LocalExposure2012→EV` handling was **NEVER calibrated** ("to observe non-zero values we would need a deflickered sample, which we do not yet have"). This deflickered sequence is that missing calibration data. Empirical calibration vs the LRT JPGs (frames 1 & 250): applying **k×LocalExposure2012**, the per-frame gain ramp (k=1: 0.961→1.099) **flattens at k≈3** (1.026→1.029, a flat static residual). So `LocalExposure2012` is **under-applied ~3×**.

**Root cause of the 3× = a UNITS/SCALE mismatch, NOT a domain/order bug.** I hypothesized it was the post-tone-curve application domain (tying it to §10) — **TESTED and REFUTED**: applying the deflicker EV pre-tone-curve (scene-linear, before LookTable+ProfileToneCurve) gives the SAME ramp (PRE k=1 ramp 1.169 ≈ POST k=1 1.144); only the ~3× scale flattens it, **regardless of domain**. So `LocalExposure2012` simply needs ~3× scaling; the deflicker fix is **independent of the §10 reorder** (§10 still stands on its own merit — highlight headroom for graded sequences — but is NOT the deflicker fix). Basis of the 3× (LrC local-vs-global Exposure2012 units convention) is TBD/uncited — confirm before hard-coding; do NOT infer it.

**~3× VALIDATED across all 5 held-out frames:** k=3 gives flat gain ~1.02 (1.026/1.020/1.012/1.016/1.029 for frames 1/63/125/188/250) vs k=1's 0.96→1.10 ramp. Robust calibration, not a 2-frame artifact.

**After the brightness fix, ΔE STILL grows** (k=3: 1.31→2.60 across frames 1→250, at flat gain) = the **static §11 tone-SHAPE**, exercised more by brighter-frame histograms (NOT per-frame, NOT exposure). Two clean, SEPARATE levers: **(1) scale LocalExposure2012 ~3× (units calibration, own small PR — pin exact factor + test + find basis)** — closes the per-frame brightness ramp; **(2) §11 static shape op** — closes the residual growing ΔE. Per-frame curve floor stays ~0.85 (8-bit-JPEG limit). NB: I proposed 3 mechanisms (LRT-internal compute / domain bug / =§10) the data each refuted — LEAD WITH MEASUREMENT. A spawned chip is doing a full adversarial pipeline order/domain audit (→ docs/research/pipeline-order-audit.md).
