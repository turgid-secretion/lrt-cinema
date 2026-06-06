# Edge-fringing owner-eyeball crops

Visual confirmation set for `docs/research/edge-fringing-rootcause.md`. Crops at three
clipped-fringe sites on DSC_4053 / LRT_00001 (frame 1): **botleft** (1792,256,256,
owner's named worst), **fixtures** (1024,1280,256), **winupper** (2048,256,256).

- `00_baseline_*` — our production render (rcd, hl off). **The fringe.**
- `00_LRTjpg_*` — the LRTimelapse JPG of the same RAW (8 px inset aligned). **Clean.**
- `demosaic_{rcd,menon,mlri}_*` — the demosaic-family ablation. rcd≈menon (refutes
  rcd-specific false colour); **mlri ≈ −40–50 % fringe (the proposed low-risk lever)**.
- `cfainpaint_{BASE,INPAINT}_*` — naive same-channel CFA inpaint BEFORE demosaic. The
  INPAINT is WORSE → proves naive per-channel mosaic fill is NOT the fix.
- `ablate_NO_WB_*` — white balance killed (wb→[1,1,1]). Whole frame mis-coloured, but
  the local blue↔yellow ALTERNATION persists → WB is not the colouriser.

Compare `00_baseline_*` vs `demosaic_mlri_*` (the fix lever) and vs `00_LRTjpg_*` (the
target). The sawtooth across the window tops is most visible in the **fixtures** /
**winupper** crops.
