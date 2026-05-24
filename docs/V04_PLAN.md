# v0.4 Plan — Full LRT-keyframe develop-op competency

## Scope statement

v0.4 closes the per-op emission gap left after v0.3 shipped the DCP-aware
basecurve + DCP-derived temperature paths. By v0.4 every one of the twelve
LRT-keyframe-able develop ops carries authored intent through to a real
darktable module instead of silently dropping at emit — modulo two
documented carve-outs where dt itself drops a field (PV2012
Highlights2012 / Shadows2012 / Whites2012, which dt's own LR-import has
declined to map for 12 years).

## What ships

| Item | dt module | Source of mapping |
|---|---|---|
| **DCP auto-detect** from RAW EXIF Make/Model | n/a (CLI plumbing) | env-var `LRT_CINEMA_PROFILES` → user-config `~/.config/lrt-cinema/profiles/` → Adobe DNG Converter install-path (fallback only) |
| **`.npz` extracted-profile format** + `save_profile`/`load_profile` + `tools/extract_dcp.py` + `tools/extract_dcp_library.py` | n/a (parser/tools) | Project-defined; lossless serialization of the DCP fields the renderer consumes; ~70% of source `.dcp` byte size after zlib compression |
| Exposure2012 (already) | `exposure.exposure` | 1:1 |
| Blacks2012 (NEW) | `exposure.black` | Verbatim from dt's `lr2dt_blacks_table` (src/develop/lightroom.c#L279-L285 SHA 9402c65275) |
| Temperature_k + Tint (already) | `temperature` | DCP color matrices + Robertson kelvin↔xy + DNG SDK iterative neutral solver |
| ToneCurvePV2012 (already, verified) | `tonecurve` | LR (x, y) ∈ 0..255 pairs → dt 20-node tonecurve, AUTOMATIC_RGB autoscale |
| DCP ProfileToneCurve (already, v0.3) | `basecurve` | DCP TIFF tag 50940 → dt 20-node basecurve, preserve_colors=MAX |
| Saturation (NEW) | `colorbalancergb.saturation_global` | Linear scale (÷100) |
| Vibrance (NEW) | `colorbalancergb.vibrance` | Linear scale (÷100) |
| Contrast2012 (NEW) | `colorbalancergb.contrast` | Linear scale (÷100); approximate — LR's PV2012 contrast is closed-source |
| Sharpness (NEW) | `sharpen.amount` | Linear scale, default-aligned (LR 25 → dt 0.5) |
| Dropped-warning false-positive fix | n/a (CLI) | Excludes LR defaults (Sharpness=25, identity ToneCurvePV2012) from the count |

## What does NOT ship

- **AsShotNeutral → temperature for "As Shot" case.** Reading
  AsShotNeutral from RAW EXIF for Nikon NEFs requires either the
  MakerNote (fragile, per-firmware) or a `rawpy`/`exiftool` runtime
  dependency. Out of scope for v0.4; deferred to v0.4.x once the
  dependency-cost decision is made (the only DCP-format we definitively
  handle today is DNG, which embeds AsShotNeutral as tag 50728).
- **Pan-camera bundled profile data in the main repo.** Per the
  size/coverage/in-repo trilemma (full LookTable cubes are 270 KB per
  camera × 4304 Adobe-supported cameras = 1.2 GB), the main repo
  ships only the parser + extractor + one test fixture
  (`tests/fixtures/dcp_data/Nikon D750 Camera Standard.npz`). Users
  wanting bundled pan-camera coverage either run
  `tools/extract_dcp_library.py` against their Adobe install or
  clone a sister `lrt-cinema-profiles` data repo and set
  `$LRT_CINEMA_PROFILES`. The sister repo is a v0.4.x deliverable.
- **Algorithmic alternative pipeline (libraw matrices + colour-science
  chromatic adaptation, no DCP dependency).** Substantial new engine;
  separate PR after the DCP-localization work lands.
- **PV2012 Contrast2012 / Highlights2012 / Shadows2012 / Whites2012 to
  per-tone modules.** dt's own LR-import drops Contrast/Highlights/
  Shadows/Whites and has done so since 2013 — the underlying PV2012
  parametric tone math is closed-source and there is no published
  dt-module mapping that is anything other than an arbitrary
  approximation. The v0.4 Contrast2012 routing through
  `colorbalancergb.contrast` is explicitly best-effort and not
  pixel-match-LR; the other three drops remain TBR in
  [SCOPE.md](../SCOPE.md).
- **Pixel-match to ACR / Lightroom output.** Same caveat as v0.3:
  PV2012 tone math is Adobe-proprietary and we have no published
  per-op response calibration. "Cinema-grade color" remains gated on
  the ColorChecker ΔE2000 envelope, not on LR-equivalence claims.

## What ships pending HSM (Stage 3 — research-gated)

- **DCP HueSatMap (HSM) interpolation.** The lrt-cinema vs LRT-preview
  diagnostic on the user's test sequence flagged "DCP-style HueSatMap
  or tone-aware warmth adjustment" as the remaining structural
  ΔE2000 residual after the affine fit closes the per-channel gain
  delta (DSC_4053 post-fit 2.24, primarily b\* divergence in
  highlights). HSM is the only mechanism in the DCP spec that can
  close that residual; its emission target in dt 5.5+ is one of
  `colorbalancergb` per-region knobs or `lut3d` 3D cube interpolation.
  The research deliverable (HSM binary format, application algorithm,
  two-illuminant blend, dt module recommendation, struct layout,
  validation plan, risk flags) is the gating decision; implementation
  follows the spec.

## Acceptance gate

End-to-end on the user's 5033-frame Nikon D750 sequence:

1. `lrt-cinema inspect` reports zero "DROPPED at emit" warnings on the
   user's neutral creative keyframes (passes today via the
   false-positive fix).
2. `lrt-cinema render --from-frame 0 --to-frame 50` succeeds with
   auto-detected DCP (no `--dcp` argument needed; passes today).
3. `tools/diagnose_vs_lrt_preview.py` on at least 3 frames vs LRT
   `/visual/` previews:
   - **Target: mean pre-fit ΔE2000 < 4.0; mean post-fit < 2.0** across
     all sampled frames.
   - Current baseline (DSC_4053, Stage 1+2 work, neutral keyframe): pre-fit
     6.05 / post-fit 2.24 — broadcast-acceptable after grade but ABOVE
     the post-fit acceptance target. Closing the remaining 0.24 ΔE
     post-fit requires HSM landing.

## Dependencies, blockers, ordering

- Stage 1+2 (auto-detect + dropped-warning + competency emits) is
  independent and committed.
- Stage 3 (HSM) is research-gated; implementation lands once the spec
  resolves the dt-module target question (`colorbalancergb` vs
  `lut3d`) and the application-point question (before or after
  ProfileToneCurve).
- AsShotNeutral path is dep-gated on the rawpy / exiftool decision;
  out of scope for v0.4 by explicit prioritization.

## Process

- Branch: `feat/v0.4-full-keyframe-competency`. Multiple commits
  (Stage 1 / 2-A / 2-B / docs).
- PR opens with the Stage 1+2 changes plus the empirical ΔE
  before/after numbers from the diagnostic. HSM ships as a follow-up
  commit on the same branch when the spec resolves, or as a separate
  v0.4.x PR if HSM stalls — per the staged-fallback approach.
- Do not merge until user reviews.

## What lrt-cinema does NOT promise post-v0.4

- Pixel-match to Adobe Camera Raw / Lightroom Classic (PV2012 math
  is proprietary).
- LR-equivalent rendering for unmapped PV2012 fields (Highlights2012,
  Shadows2012, Whites2012). These remain dropped at emit, consistent
  with dt's own LR-import behavior.
- Headless LR-driven calibration loop (the "LR sweep trap" — out of
  project scope; would require Adobe LR in the calibration path,
  contradicting the Adobe-free runtime goal).
