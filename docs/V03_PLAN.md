# v0.3 Plan — Render-time fidelity to full LRT keyframe intent

## Scope statement

v0.3 delivers a render path that takes ALL twelve LRT-emitted develop ops, not just exposure, and emits darktable XMPs that produce intermediate image sequences honoring the LRT-authored creative intent end-to-end. Project explicitly does NOT replicate LRT's interpolation, keyframing, or XMP-authoring math beyond what is already shipping; LRT does that work well and we lean on its outputs. We compete only on render-time intermediate-sequence fidelity for cinema-native delivery (linear Rec.2020 TIFF, ACES OpenEXR, AgX Rec.2020 TIFF).

## What we cannot claim until further work

A v0.3 release does NOT promise pixel-match to ACR / Lightroom output. The PV2012 tone math (Adobe Camera Raw's Contrast / Highlights / Shadows / Whites / Blacks parametric model) is proprietary and unpublished. Mapping LR's parametric tone values to darktable's tonecurve / toneequal / filmic params requires per-op response calibration, which produces "looks similar," not pixel-equivalent, output. State this plainly in v0.3 release notes.

The defensible "cinema-grade color" claim depends on the ColorChecker ΔE2000 automated test (see [docs/VALIDATION.md](VALIDATION.md)). That test work runs in parallel with v0.3 emitter work and is its acceptance gate, not a precondition.

## Work tracks (risk-ranked, biggest unknowns first)

### Track A — Emitter expansion (the bulk of v0.3)

Map the nine currently-dropped develop ops (see [SCOPE.md](../SCOPE.md) "Emitted vs parsed DevelopOps" table) to darktable modules with calibrated `op_params` blobs.

| Order | LRT field(s) | Target dt module | Risk | Approach |
|---|---|---|---|---|
| A1 | `tone_curve` (parametric points list) | `tonecurve` (modversion ~5) | LOW | Binary layout for parametric curve is well-documented; map LRT's 0..255 `(x, y)` pairs into dt's normalized control points. Round-trip test via known-good GUI-exported sidecar. |
| A2 | `contrast`, `highlights`, `shadows`, `whites`, `blacks` (PV2012 parametric values, -100..100 each) | `tonecurve` parametric channel OR `toneequal` (per-band exposure) OR `filmic` regions | HIGH | PV2012 math is Adobe-proprietary. Approach: sweep each LR value -100..+100 against a fixed grayscale ramp via LR (or `dng_validate`), measure output values, fit dt-param response curve. Calibration heavy. See chip: pv2012-calibration. |
| A3 | `saturation`, `vibrance` (-100..100 each) | `colorbalancergb` master/perceptual saturation channels | MEDIUM | LR's vibrance has selective behavior (less effect on already-saturated colors); dt's colorbalancergb has similar perceptual saturation knob. Calibrate via sweep against LR. |
| A4 | `sharpness` (0..150, default 25) | `sharpen` (USM equivalent) or `diffuse` (newer) | LOW-MEDIUM | LR's sharpness is unsharp mask with radius/detail/masking sub-knobs we don't currently parse. v0.3: ignore the sub-knobs, map the master amount to `sharpen` amount via simple linear scale. |
| A5 | `temperature_k`, `tint` (Kelvin + green-magenta) | `temperature` (modversion 3, four float channel multipliers) | HIGH | Kelvin → R/G1/B/G2 multipliers requires the camera's DCP profile (or LR-equivalent matrix derivation). DCP parsing is its own project; see chip: dcp-kelvin-multipliers. v0.3 may ship with as-shot WB only and document the kelvin-override as deferred. |

### Track B — Parser update

| Order | Item | Risk | Notes |
|---|---|---|---|
| B1 | Real LRT mask-based encoding for Deflicker and Holy Grail | LOW (mechanical) | Real LRT writes deflicker / HG offsets as named entries inside `crs:MaskGroupBasedCorrections` (`CorrectionName="#LRT internal use (Deflicker)"` etc. carrying per-frame `crs:LocalExposure2012`). Currently the parser uses our synthetic `<lrt:HolyGrailRamps>` schema. Refactor to read both, prefer the real mask-based encoding when present. Needs LRT-deflicker'd sample to test against — chip: lrt-mask-parser. |

### Track C — Validation harness

| Order | Item | Risk | Notes |
|---|---|---|---|
| C1 | ColorChecker ΔE2000 CI gate | LOW (mechanical, depends on chart shot) | Implements the methodology in docs/VALIDATION.md. Adds dev-dep on `colour-science` (BSD-3, license-compatible). Threshold: mean ΔE2000 < 2.0, max < 4.0 across 24 patches under D55. Chip: colorchecker-test-harness. Blocked on chart shot from user. |
| C2 | `dng_validate` (Adobe DNG SDK) reference renderer | MEDIUM | DNG SDK ships `dng_validate` which renders DNG → TIFF using ACR's pipeline. NEF → DNG via Adobe DNG Converter, then `dng_validate -tif`. Closest available headless ACR-equivalent. Useful for per-op response calibration in Track A2/A3. Background research chip: dng-sdk-feasibility. |
| C3 | Golden-image regression CI gate | LOW | Single hand-tuned reference TIFF per preset, checked into repo, CI diff against current render. Cheap defense against unintended pipeline drift. Add once Track A2 is calibrated enough to be stable. |

## v0.3 acceptance test (the user-facing milestone)

Render a contiguous test sequence on the user's existing 5033-frame LRT-authored timelapse with ALL twelve develop ops set on the keyframes (user sets a creative grade in LRT — contrast / shadows / highlights / saturation / tone curve / etc.), and confirm:

1. `lrt-cinema inspect` reports zero "DROPPED at emit" warnings — all twelve ops emit successfully.
2. The rendered sequence visually carries the LRT-authored grade (not just exposure animation). Smoke test: open in Resolve, scrub through, compare to the LRT visual preview.
3. ColorChecker ΔE2000 on a reference chart shot through the same pipeline: mean < 2.0, max < 4.0 (acceptance gate for "colorimetrically correct" claim).
4. Optional: pixel-diff against an LR-rendered reference (if feasible via DNG SDK by then). Surface per-op divergence in EV / Lab terms; document where dt's interpretation diverges from ACR. Not a release gate for v0.3.

## What ships in v0.3 — explicit list

- All 12 develop ops emit to dt modules (calibrated to "looks similar to LR" via per-op response curves)
- Parser honors both synthetic and real-LRT mask-based Deflicker / HG encodings
- ColorChecker ΔE2000 test harness wired into CI; current threshold-pass against a reference shot in `tests/fixtures/colorchecker/`
- Bundled `.style` files calibrated against dt 5.4 LTS line and dt nightly (see [presets/CALIBRATION.md](../src/lrt_cinema/presets/CALIBRATION.md))
- Release notes explicitly: "colorimetrically correct within published ΔE2000 envelope on ColorChecker Classic / D55; not pixel-match to Adobe Camera Raw"

## What does NOT ship in v0.3

- Pixel-match to ACR (PV2012 math is closed-source; calibration is "looks similar" only)
- Headless LR Lua SDK integration (R&D path, deferred)
- Parallel worker pool (single-worker stays for v0.3)
- Cross-platform "LRT successor" mode (parked indefinitely; revisit only if LRT becomes unavailable)

## Dependencies, blockers, ordering

- A1 (tone curve) can land first — no external blockers, lowest risk.
- A2 (PV2012) blocked on calibration-data-gathering chip; can sequence after A1.
- A3 (sat/vib) blocked on same calibration data as A2.
- A4 (sharpness) independent, can land in parallel.
- A5 (kelvin) blocked on DCP-sourcing chip; may ship in a later v0.3.x.
- B1 blocked on user running LRT deflicker / HG on a test sequence (small ask).
- C1 blocked on user shooting a ColorChecker reference (medium ask — needs chart + controlled lighting).
- C2 / C3 can run independently as R&D / hardening tracks.

## Out-of-scope / parked

- Replicating LRT math (interpolation, keyframe management, XMP authoring). LRT does this well. Our existing linear / smooth / Holy Grail interp competency stays in tree as a fallback for users who skip Auto Transition, but is no longer the project's claim-of-value.
- Replacing darktable as the renderer. dt is the bet. Risk is mitigated by the ColorChecker ΔE test; alternative-renderer evaluation is a v0.4+ topic if dt proves persistently inadequate.