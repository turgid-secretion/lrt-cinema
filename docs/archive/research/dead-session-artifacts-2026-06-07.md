# Dead-session experiment artifacts — inventory (Jun 5–7, 2026)

Owner-approved: 2026-06-10

The last LLM sessions (after the final commit `fecbe93`, Jun 5) ran ~3 days of
uncommitted experiments. Their image artifacts lived in `/tmp/dng_out` (volatile;
relocated to the stable fixtures dir — see `FIXTURES.md`). What each tested:

- `blinds_ours_vs_dngvalidate.png`, `crop_dv_blinds.tif`, `crop_ours_blinds.tif` — our render vs Adobe dng_validate on the venetian-blinds crop (basis of the "dng_validate measures 0.70 chroma" claim).
- `cyan_diag.png`, `cyan_profile.png`, `cyan_ablation.png`, `clip_vs_cyan.png`, `cyan_ours_LRT_dv.png`, `chroma_map.png`, `chroma_repro.png` — cyan-artifact diagnostics: clip-correlation, hue/chroma profiles, stage ablations, three-way ours/LRT/dng_validate.
- `b1_result.png`, `chromamed_result.png` — candidate mitigations: B1 on-mosaic highlight reconstruction; post-demosaic chroma-difference median (the dcraw `-m` technique).
- `rt_ours_lrt.png`, `altengine_grille_amp.png` — RawTherapee vs ours vs LRT comparison and an alt-engine grille-amplitude test (the engine-swap evaluation; its conclusion is REFUTED — see the header on `alt-raw-engine-feasibility.md`).
- `fix_compare.png`, `despeckle.png`, `real_fix.png`, `base_check.png`, `spikemap.png` — candidate-fix A/Bs from the final session (Jun 7 16:00–18:32).
- `dv_4053.tif` (145MB) — full dng_validate render of DSC_4053 (reusable as the missing `test_pipeline.py` gym reference TIFF). `synth_dngval.tif` — dng_validate render of the synthetic flat-patch DNG (used by the gym synthetic gate).

These are point-in-time outputs of uncommitted code paths; treat their implied
conclusions as HYPOTHESIS unless re-derived (CLAIMS.md is authoritative).
