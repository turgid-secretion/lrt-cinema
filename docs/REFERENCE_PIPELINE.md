# REFERENCE_PIPELINE.md — the cross-engine raw-pipeline canon

Owner-approved: 2026-06-10

**Purpose (anti-drift rule 8):** pipeline-STRUCTURE questions (what order, what
domain, what units) are answered against the cross-engine canon — never against
a single reference. The gym gate certified our demosaic-before-WB ordering for
weeks because Adobe's bilinear reference demosaic is insensitive to it
(bilinear commutes with per-channel WB; directional algorithms don't). Single-
reference agreement ≠ correctness.

**Status: SEED (2026-06-10).** Two rows are filled from this repair campaign's
evidence; the rest carry the column skeleton and await the source-reading
build-out (next session's headline). Reading GPL sources (RawTherapee,
darktable, dcraw/libraw) to LEARN ordering/semantics is allowed and
encouraged; **vendoring their code is not** (CLAUDE.md rule 6).

**Verdict legend** (per OUR-column divergence from canon):
- **JUSTIFIED(evidence)** — deliberate divergence, written rationale + artifact.
- **SUSPECT** — divergence noticed, not yet root-caused/measured.
- **BUG** — divergence measured wrong. Stays listed after fixing, marked
  `BUG→fixed(date)`, so the failure class remains visible.

## Stage-order table

Columns: dcraw/libraw · RawTherapee · darktable · Adobe DNG SDK ·
ISP literature (Karaimer & Brown 2016, "A Software Platform for Manipulating
the Camera Imaging Pipeline") · **OURS** · verdict.

| Stage | dcraw/libraw | RawTherapee | darktable | Adobe DNG SDK | ISP literature | OURS | Verdict |
|---|---|---|---|---|---|---|---|
| Black level | TODO (read `subtract`/`scale_colors`) | TODO | TODO (`rawprepare`) | TODO (`dng_linearization_info`) | pre-demosaic, sensor-referred | per-channel black subtract in `_extract_cfa` / libraw | TODO |
| Linearization (LUT) | TODO | TODO | TODO | LinearizationTable before everything | pre-demosaic | libraw honours DNG LinearizationTable (DNG input preferred for exactly this) | TODO |
| **White balance** | **`scale_colors` BEFORE `*_interpolate`** (verified in source layout + empirically via `user_wb`) | scale before demosaic (owner's RT experiment: no cyan at cool WB, any algo) | TODO (`temperature` before `demosaic` in default pipe order) | WB folded pre/at-demosaic (reference renders show no ordering artifact) | WB before demosaic | ~~demosaic raw mosaic, WB after (Stage 2)~~ → **pre-scale mosaic by Stage-2 multipliers, divide back after** (all paths incl. libraw `user_wb`) | **BUG→fixed(2026-06-10)** — the cyan-blinds root cause (H1). Evidence: `tools/h1_wb_demosaic_ab.py`, `tests/fixtures/evidence/h1/` (P99.5 cyan 188→87 vs LR 84.5; pipeline arm ≡ target arm). CLAIMS.md "WB-before-demosaic fix SHIPPED" |
| Highlight handling | clip-to-white at scaled max (highlight=0 default); reconstruct modes optional | TODO (several reconstruction modes) | TODO (`highlights` module pre-demosaic) | reconstructs partially-clipped px (gym evidence: dng_validate keeps detail where we clip) | recovery pre/at-demosaic | libraw path: canonical scale-then-clip (post-fix); CFA paths: float headroom + optional Tier-1 `highlight_recovery` | **SUSPECT** — partial-clip divergence vs Adobe = the gym max-ΔE 13.6 at 0.006% px; recovery clip-detection degraded on libraw algos (CLAIMS.md). Decide after owner eyeballs blown windows (task D) |
| CA correction | TODO (`cacorrect`) | TODO (pre-demosaic raw CA) | TODO | TODO | pre-demosaic | none | TODO (likely JUSTIFIED-absent for v1; measure on production glass) |
| **Demosaic** | after scale_colors | after WB scaling | after WB | insensitive-by-construction (bilinear-class reference) | after WB | rcd/mlri/menon on WB-conditioned CFA (post-fix); libraw algos via `user_wb` | **BUG→fixed(2026-06-10)** — same H1 row as WB; kept separate so the demosaic-quality question (which algo) stays distinct from the conditioning question (what input) |
| Noise reduction | TODO (post-demosaic wavelet in dcraw `-n`) | TODO (capture NR placement) | TODO (`denoiseprofile` placement) | ACR: NR before/within develop (ColorNR 25 active in production XMPs) | varies; chroma NR often post-demosaic | none (production XMPs carry ColorNR 25 — we drop it) | **SUSPECT** — part of the residual ~0.2–0.5 ΔE base-look floor vs LR; quantify in the look-gap decomposition (1d) |
| Color transform (camera→XYZ→working) | TODO | TODO | TODO | ForwardMatrix(+HSM/LookTable) — implemented | matrix post-demosaic | DNG 1.7.1 stages 2–9 vs dng_validate at 0.023 mean ΔE | verified vs Adobe; cross-engine read TODO |
| **Tone / exposure unit semantics** | n/a (no develop layer) | TODO (pp3 exposure semantics) | TODO (`exposure` scene-referred module) | **local/mask exposure: serialized `LocalExposure2012` = EV/4, applied ×4 scene-referred (pre-curve)** — measured on LR itself | exposure scene-referred, pre-tone-curve | ~~mask EV ×1 into post-ProfileToneCurve `exposure_ev`~~ → **×4.0 onto `scene_exposure_ev`, camera-RGB gain pre-Stage-2** | **BUG→fixed(2026-06-10)** — both magnitude AND domain were wrong; post-curve domain cannot match LR at ±1–2 EV for any factor. Evidence: `tools/cal_deflicker_factor.py`, `tests/fixtures/evidence/cal_deflicker_factor_2026-06-10.json` (k\*=3.992±0.027, ΔE@4.0 = 0.20/0.44). CLAIMS.md "Exact mask-exposure factor". NOTE the GLOBAL `Exposure2012` slider still applies post-curve (Stage 11) — zero in production, but the same domain question is open → SUSPECT sub-row |
| Sharpening | n/a | TODO (capture sharpening placement) | TODO | ACR capture sharpen on luminance, develop-stage (Sharpness 25 active in production) | post-demosaic, late | clean-room USM, faithful path, `--capture-sharpen` (default off) | TODO — placement/params unvalidated vs ACR; part of the base-look floor |

## Open SUSPECT ledger (work queue for the build-out)

1. **Global `Exposure2012` domain** — we apply post-ProfileToneCurve (Stage 11);
   LR applies scene-referred. Zero on this production sequence, so latent.
   Same experiment design as the CAL deflicker calibration would settle it
   (single-variable XMPs at Exposure ±1/±2, owner exports).
2. **Partial-clip highlight handling** vs Adobe reconstruction (gym max-ΔE row).
3. **NR + sharpening placement/params** — the residual base-look floor.
4. **`highlight_recovery` clip detection on libraw algos post-fix** (CLAIMS.md
   SUSPECT row): per-channel clip points moved off 1.0.

## Sources for the build-out (read-to-learn; NO vendoring)

- dcraw.c / LibRaw: `scale_colors`, `pre_interpolate`, `*_interpolate`,
  `blend_highlights` — the C reference for stage order.
- RawTherapee: `rtengine/` (`rawimagesource.cc` — `scaleColors`, demosaic
  dispatch); the owner's pp3 (`production/rt-experiment/`) pins the verified
  no-cyan configuration (Temperature=4039, Green=1.057, rcdvng4).
- darktable: default pixelpipe module order (`src/common/iop_order.c`).
- Adobe: DNG 1.7.1 spec §"Mapping Camera Color Space" (implemented) + dng_sdk
  `dng_render.cpp` for highlight/exposure semantics.
- Karaimer & Brown 2016 (ISP stage taxonomy); Adobe PV2012 local-adjustment
  docs for the ±4 EV local-exposure slider span (the EV/4 serialization).
