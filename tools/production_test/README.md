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

## Recorded run — 2026-06-01, 24 frames, D750 gym, `main`
**VERDICT: PASS.**

- **lrtimelapse / sRGB TIFF (faithful):** 24/24 frames; naming `LRT_00001.tif…`
  contiguous; 4032×6032 uint16; embedded sRGB ICC + correct provenance
  (`colorspace=sRGB`, `range=full`); no non-finite pixels — **0 conformance
  issues**. ~15 s/frame (6 workers, each re-decoding the same 146 MB DNG — a
  test artifact; real sequences decode distinct, smaller RAWs once each via the
  dnglab cache).
- **resolve / ACEScg EXR (perceptual):** 6/6 frames; `chromaticities` tag =
  AP1 primaries + ACES ~D60 white (0.32168, 0.33767) — **allowlist-compliant
  scene-linear**, half-float.
- **Full develop set exercised:** Exposure/WB/Blacks/Contrast/Saturation/
  Vibrance/ToneCurve + HSL ×24 + Color-Grade wheels + Texture/Clarity — parsed,
  interpolated, applied; per-frame center-luma swings with the combined grade.
- **Interpolation:** every keyframe segment transitions **monotonically**
  (13860→7368→220→419 across the keyframes, smooth at every intermediate frame) —
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
  [docs/LRT_ROUNDTRIP.md](../../docs/LRT_ROUNDTRIP.md).
