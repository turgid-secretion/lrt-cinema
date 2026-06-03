# CLAUDE.md — lrt-cinema project context (read first)

Invariants prior sessions established and that are expensive to re-derive. This
file is the index; canonical deep docs are linked.

## What this is
Clean-room Python implementation of the Adobe DNG 1.7.1 render pipeline, driven by
LRTimelapse (LRT) XMP develop intent. **Default emission = an LRT-ingestible 16-bit
sRGB display TIFF** (`lrtimelapse` preset; `LRT_00001.tif…`; embedded sRGB ICC) for
the canonical LRT round-trip: lrt-cinema → TIFF → back into LRT → video + Motion
Blur. Scene-linear ACEScg EXR is the opt-in DaVinci Resolve / ACES path. See
[docs/LRT_ROUNDTRIP.md](docs/LRT_ROUNDTRIP.md).

## Render intent — dual-mode (`--render-intent {faithful, perceptual}`, v0.9)
Stage-12 grading ops share IR (`HslBands`, `ColorGrade`, Texture/Clarity, DR
fields); only the **applicator** branches on intent (DECISIONS.md §7):
- **faithful** — Adobe-matching math (the Lightroom look). DEFAULT for the sRGB
  TIFF (LRT round-trip). HSL / Color-Grade in linear ProPhoto; PV5 basic tone
  (Highlights/Shadows/Whites) + Texture/Clarity stay **dropped + warned** (no
  public Adobe math to match).
- **perceptual** — modern primitives. DEFAULT for the ACEScg EXR masters
  (Resolve/ACES, no Adobe-fidelity obligation): HSL in **OKLCh**, Color-Grade as
  **offset-only ASC-CDL in ACEScct**, **scene-referred DR-compression**
  approximating Highlights/Shadows/Whites, edge-aware **Texture/Clarity**.
Per-target default; `--render-intent` overrides. **Invariants:** every perceptual
applicator is ProPhoto(D50)-in/out (gamut conversion stays at Stage 13); gamut
compression is ONE gated **ACES RGC** pass in `output.py` before the AP1 encode
(no-op in-gamut); both modes are **byte-exact at zero-slider identity** (the ship
gate is unchanged). Ops: `develop_ops.apply_{hsl,color_grade,dr_compression,
texture_clarity}` (+ `_*_perceptual`). Fidelity tracked by `tools/grading_sweep/`.

## Render engine — READ [docs/PIPELINE.md] BEFORE TOUCHING IT
[docs/PIPELINE.md] is the canonical as-built engine reference: every stage
(1–13), the file/function that owns it, colour space in→out, the load-bearing
invariants, where tests tap in, and current repo-truth numbers. **Mandatory
ingest before changing `src/lrt_cinema/{pipeline,dcp,lut3d_baker,develop_ops,
output}.py`.** If a change contradicts a documented invariant, you must preserve
it OR update PIPELINE.md + the guarding test in the same change, citing
primary-source evidence (the DNG SDK at `/private/tmp/dng_sdk`, or a ΔE
measurement vs `dng_validate`). **Neutrals passing ≠ correct** — a grey wedge is
blind to the tone-curve application mode and the camera-matrix chromatic
rotation; verify Stage 3/5/8/9 changes against *saturated* colour.

## Colour-space allowlist — DO NOT emit anything else
A colour space = (primaries, white point, transfer). Authoritative research:
[docs/research/v08-linear-exr-gamut-resolve-nuke.md]. Only these emissions are correct:

**Scene-referred (linear) masters**
- **ACEScg** — AP1 primaries, ACES white **~D60** (0.32168, 0.33767), linear.
  DEFAULT scene-linear master. Tag EXR `chromaticities` = AP1. Resolve Input =
  "ACEScg"; Nuke Read node = ACEScg.
- **ACES2065-1** — AP0 primaries, ~D60, linear. Archival/interchange (SMPTE ST
  2065-1/-4). Tag `chromaticities` = AP0 **and** `acesImageContainerFlag = 1`.

**Display-referred (delivery)**
- **sRGB** — Rec.709 primaries, D65, sRGB OETF. DEFAULT display / LRT round-trip;
  embed sRGB ICC.
- **Rec.709** — BT.709 primaries, D65, gamma 2.4 / BT.1886. SDR video delivery.
- **Rec.2020 with a DISPLAY transfer** — BT.2020 primaries, D65, PQ or gamma 2.4.
  HDR display delivery ONLY (never linear).

**FORBIDDEN — colour-science errors, never emit:**
- **Linear Rec.2020** (BT.2020 primaries + linear transfer, used scene-referred).
  Rec.2020 is a *delivery/display* gamut; linear/scene-referred use is the
  "Franken-gamut" misuse. Resolve REJECTS "Linear/Rec.2020" as an Input. This was
  the removed `cinema-linear` / `cinema-aces` mistake.
- Any **linear + delivery-gamut** combination (linear Rec.709, etc.).
- **D65 pixels tagged as AP1/ACES** — ACEScg/ACES use ~D60. ProPhoto(D50)→AP1 needs
  a D50→~D60 Bradford CAT and the AP1 white tag, NOT D65.

White points: ProPhoto working space = D50; sRGB/Rec.709/Rec.2020 = D65; ACEScg/ACES
= ~D60. Always Bradford-adapt between them.

## Validation invariants
Three axes — never conflate (detail: [docs/VALIDATION.md]):
1. **Implementation correctness** (`tests/test_color_oracle.py`) — vs our own maths
   (independent reimpl). Expected **~0**. The bug-finder + the only axis that
   validates a new render-math op with certitude.
2. **Absolute colorimetric accuracy** — vs CIE truth from spectra (ISO 17321-1).
   **Nonzero floor** (Luther condition → least-squares DCP fit). Measure at the
   **colorimetric tap**: post-ForwardMatrix linear, BEFORE HSM/ExposureRamp/
   LookTable/ProfileToneCurve.
3. **Appearance vs LRT preview** (`tools/diagnose_vs_lrt_preview.py`) — what the
   colorist saw. Report affine-**residual** (structural) + raw; floor = closed-source
   PV5 look + 8-bit JPEG.

**North-star = the LRT JPG look — NOT `dng_validate`.** The real goal is to match
(then, in targeted areas — highlight/shadow reconstruction, sharpening, NR —
deliberately *exceed*) the LRTimelapse JPG outputs the colorist signed off on
(Axis 3, `tools/diagnose_vs_lrt_preview.py`). The mean ΔE2000 < 1.0 vs
`dng_validate` (Adobe's DNG reference renderer; test-only oracle) is a **regression
tripwire for the baseline colour science (stages 1–9)** — it was the sanity check
during the model switch-out and still guards against *accidental* colour drift, but
it has **NO veto over intentional, goal-directed divergences**. dng_validate does no
highlight reconstruction, no sharpening, no NR; matching its *clip* in those areas
is the wrong target. **Do not let the 0.026 number drive decisions against the real
goal** (it is an active footgun when it does — e.g. "keep highlights byte-identical
to a renderer that throws highlight data away"). Baseline-regression head: gym
**0.026**, rose **0.545** mean (was 0.789 / 0.844). The gym near-bit-match landed on 2026-05-30 by
fixing Stage 9 to apply the ProfileToneCurve as Adobe's **hue/saturation-preserving
`RefBaselineRGBTone`** (curve max+min, interpolate the middle channel) instead of
per-channel — the old "demosaic-edge tail" was mostly this per-channel tone error
firing where channels differ (edges + saturated colour). The synthetic flat-patch
harness (`test_synthetic_dng.py`) drove the residual: neutral ΔE 0.000, chromatic
**0.05** (sRGB-quantisation floor). NB: `dnglab` strips the ForwardMatrix from the
synthetic clone, so that path exercises the ColorMatrix + **MapWhiteMatrix** branch
(`dcp.colormatrix_camera_to_pcs`); the real D750 Camera-Matching DCPs ship a
ProPhoto-passthrough ForwardMatrix (LookTable does the colour) — see
[docs/research/v08-synthetic-chromatic-rootcause.md] for the full trace.

## Build / test
- `python3 -m pytest -q` — full suite. Render/ΔE tests skip without `/tmp/dng_out`
  fixtures (Adobe DNG + dng_validate TIFFs, gitignored / external).
- `ruff check .` — must pass.

## Git
- `main` is the consolidated head (v0.8 sweep + v0.9 dual-mode grading, all merged).
  Keep `main` green; one PR per feature/op to `main`; CI runs on PRs to `main`.
- Recovery tags: `pre-reduction-v0.8` (pre-sweep) · `phase4-research-archive` (pre doc-prune).
- Conventional commits; end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Settled — do NOT re-explore
Standalone GUI app, vkdt-fork, β-XML Resolve sidecar, CDNG-as-default — all ruled out
with reasons in [docs/DECISIONS.md](docs/DECISIONS.md). Closed-source PV5 basic tone
(Highlights/Shadows/Whites) + Dehaze have no faithful Adobe math: **faithful** mode
drops them + warns (never hidden); **perceptual** mode ships a defensible
scene-referred DR-compression approximation for H/S/W (v0.9 §7) — Dehaze stays
dropped in both.
