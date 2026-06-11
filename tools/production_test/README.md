# Phase 6 — scaled production test + emission analysis

`run.py` synthesises a keyframed timelapse from a single base DNG (same pixels,
**varying per-frame develop intent**) and reports emission conformance +
interpolation behaviour across the full develop set and both render intents.

## Run
```bash
python3 tools/production_test/run.py \
  --base-dng /tmp/dng_out/DSC_4053.dng --frames 24 --workers 6
```
Needs a base DNG + the D750 `.npz` DCP (`tests/fixtures/dcp_data/`, auto-default).
Renders full-res (~146 MB/frame); **not a CI test** (heavy + needs external data).

## Input provenance (read before trusting the numbers)
The base frame is the **real `DSC_4053.NEF`** (Nikon D750 capture) converted to a
CFA-mosaic DNG by **dnglab** — the *product* RAW→DNG converter — so this run
exercises the real shipping pipeline. It is **not** a camera-native file (D750
saves NEF) and **not** the Adobe DNG Converter output; an earlier run used the
Adobe-converted DNG, which differs only in baseline exposure (below).

**Baseline-exposure provenance — faithful net = 0.0 EV.** Adobe computes
`TotalBaselineExposure = DNG.BaselineExposure + DCP.BaselineExposureOffset`. For the
D750 Camera Standard profile the terms cancel: the Adobe DNG carries
`BaselineExposure +0.1`, the `.dcp` carries `BaselineExposureOffset −0.1` → **net
0.0**. This run (dnglab DNG `BaselineExposure 0.0` + `.npz` profile) also nets 0.0,
so its brightness matches the faithful Adobe target. Two things to know:
- The in-repo `.npz` fixture is **stale**: it carries `baseline_exposure_offset =
  0.0`, yet `parse_dcp` on the real `.dcp` returns −0.1 and `save_profile`→
  `load_profile` preserves it — a *fresh* extract would yield −0.1. Harmless on the
  dnglab/NEF path (`0.0 + 0.0` nets 0.0) but feeding an *Adobe*-converted DNG
  (`+0.1`) through this stale `.npz` nets **+0.1 EV (too bright)**. See bottom.
- The gym ΔE ship-gate is **truthful but guards a different path**: it
  `parse_dcp()`s the system `.dcp` (offset −0.1) on the Adobe DNG → net 0.0 →
  matches `dng_validate` (0.026). It never reads the `.npz`, so it does **not**
  guard the shipping (dnglab + `.npz`) baseline — nothing does.

## Recorded run — 2026-06-01, 24 frames, real NEF→dnglab, `main`
**VERDICT: PASS.**

- **lrtimelapse / sRGB TIFF (faithful):** 24/24 frames; naming `LRT_00001.tif…`
  contiguous; 4032×6032 uint16; embedded sRGB ICC + correct provenance
  (`colorspace=sRGB`, `range=full`); no non-finite pixels — **0 conformance
  issues**. ~14 s/frame (6 workers, each re-decoding the same 146 MB DNG — a
  test artifact; real sequences decode distinct, smaller RAWs once each via the
  dnglab cache).
- **resolve / ACEScg EXR (perceptual):** 6/6 frames; `chromaticities` tag =
  AP1 primaries + ACES ~D60 white (0.32168, 0.33767) — **allowlist-compliant
  scene-linear**, half-float.
- **Full develop set exercised:** Exposure/WB/Blacks/Contrast/Saturation/
  Vibrance/ToneCurve + HSL ×24 + Color-Grade wheels + Texture/Clarity — parsed,
  interpolated, applied; per-frame center-luma swings with the combined grade.
- **Interpolation:** every keyframe segment transitions **monotonically**
  (13837→7291→190→373 across the keyframes, smooth at every intermediate frame) —
  the linear per-frame lerp is sound. (Luma reflects the *combined* grade, not
  exposure alone — do not read it as an EV probe.)
- **Texture/Clarity under faithful:** correctly **dropped + warned** (DECISIONS
  §7 — Adobe's edge-aware versions are closed-source), the warning pointing to
  the perceptual path. Invariant working; never silently hidden.

## NOT covered here — human-gated (the remaining true proofs)
- The real **~5000-frame** sequence — needs the source drive mounted
  (`tests/fixtures/raw/sample.NEF` is a symlink to it).
- The **manual LRT acceptance check** — load `LRT_*.tif` into LRTimelapse →
  *Render from Intermediate* → Motion Blur → confirm no colour/gamma shift. The
  only true "in-bounds" proof (LRT has no headless API). See
  [docs/archive/LRT_ROUNDTRIP.md](../../docs/archive/LRT_ROUNDTRIP.md).

## Latent issue surfaced by this run (not yet fixed)
**Root cause: a stale in-repo `.npz` fixture, not a code bug.** `parse_dcp` reads
the real `.dcp`'s `BaselineExposureOffset = −0.1` correctly and `save_profile`→
`load_profile` preserves it — but the committed
`tests/fixtures/dcp_data/Nikon D750 Camera Standard.npz` carries `0.0` (extracted
before the offset was parsed). Consequences:
- **Shipping path (dnglab + `.npz`) is *accidentally* correct:** `0.0 (dnglab) +
  0.0 (stale .npz) = net 0.0` = the faithful target. Two omissions cancel.
- **Adobe-DNG + `.npz` is +0.1 EV too bright:** `+0.1 + 0.0`.
- **The trap:** naively re-extracting the `.npz` (→ −0.1) pushes the dnglab path to
  `0.0 + (−0.1) = −0.1` (too dark), because dnglab omits the +0.1 camera
  `BaselineExposure` Adobe writes. The correct fix is **baseline-source-aware** —
  supply the per-camera baseline when the DNG lacks one — *then* the `.npz` can
  carry the true −0.1.
- **Coverage gap:** the gym gate exercises `.dcp` + Adobe-DNG, not the `.npz` +
  dnglab shipping path; **no test asserts the shipping net baseline**, so the gate
  stays green through a `.npz` regression. Closing this needs a **new `.npz`/dnglab
  net-baseline assertion**, not the existing gate.
