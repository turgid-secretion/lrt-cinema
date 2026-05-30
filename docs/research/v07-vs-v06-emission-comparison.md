# Emission Comparison: v0.7 CinemaDNG vs v0.6 TIFF / EXR

**Status:** Comparison document, 2026-05-27.
**Scope:** Comprehensive side-by-side of the v0.7 CinemaDNG emission
(per [v07-emission-format.md](v07-emission-format.md)) and the existing
v0.6 TIFF / EXR emission (per [v06-architecture.md](v06-architecture.md)).

This doc is the discriminating evidence the SPEC's "transition repo to
emissions in said format" mandate rests on. Either v0.7 is materially
better than v0.6 across the axes that matter, or the SPEC is wrong.

---

## 1. One-line characterisation

| | v0.6 (current) | v0.7 (proposed) |
|---|---|---|
| | **"Baked frame, scene-referred linear, full pipeline applied"** | **"Raw sensor data + per-frame WB/exposure metadata + Bayer-baked mask deltas"** |
| Pipeline stage of emission | Stage 13 (after all develop) | Stage ~1 (after demosaic skip — output is sensor-space Bayer + metadata) |
| Architecturally analogous to | Cinema intermediate (EXR/TIFF master) | "Smart raw" (CDNG / DCP-driven re-developable raw) |

---

## 2. Headline metrics (24 MP frame, gym/rose test scenes)

| Metric | v0.6 `cinema-linear` | v0.6 `cinema-aces` | v0.7 `cinema-cdng` |
|---|---:|---:|---:|
| Per-frame size | **~292 MiB** | **~100–150 MiB** | **~15–25 MiB** |
| Compression vs `cinema-linear` | 1.0× | ~2–3× | **10–18×** |
| 1000-frame seq | 280 GiB | 130 GiB | **20 GiB** |
| 5000-frame seq | 1.4 TiB | 650 GiB | **100 GiB** |
| Lossless on sensor data | yes (32-bit float preserves) | yes (PIZ lossless) | **yes (LJPEG-92 on Bayer)** |
| Bit-exact reversible | yes (TIFF is uncompressed) | yes | yes (sensor data); WB/exposure are decode parameters |

The decisive size win comes from **carrying Bayer instead of demosaiced
RGB**: 1 channel × 14 bits vs 3 channels × 32 bits = ~6.9× before any
compression. Lossless JPEG-92 on the Bayer plane adds ~1.5–2.8× on top.

---

## 3. Where the data lives in the pipeline

```
v0.6 (current):
  NEF → adobe-convert → DNG → pipeline (12 stages: demosaic → CCM → HSM →
  Exposure → LookTable → ToneCurve → LR ops → ProPhoto → Rec.2020)
                                                ↓
                                  emit 32-bit float TIFF / EXR
                                                ↓
                                      Resolve reads pixels
                                                ↓
                                            Grade

v0.7 (proposed):
  NEF → adobe-convert → DNG → (skip pipeline — keep source Bayer)
                            → apply LRT mask-correction gain plane to Bayer
                            → write AsShotNeutral + BaselineExposure tags
                            → lossless-JPEG-92 encode Bayer strip
                            → emit .dng
                                                ↓
                            Resolve reads as CinemaDNG, debayers,
                            applies bundled DCP profile + per-frame metadata
                                                ↓
                            Optional: user overrides WB / exposure in Camera Raw panel
                                                ↓
                                            Grade
```

The fundamental architectural shift: **v0.6 finishes the work and ships
pixels. v0.7 ships the work-order with a partial product.**

---

## 4. LRT develop intent preservation

This is the substantive trade. Each LRT op class lands somewhere different.

| LRT op | v0.6 disposition | v0.7 disposition | Net change |
|---|---|---|---|
| **Holy Grail kelvin override** (`Temperature` / `Tint`) | Applied in pipeline Stage 2 (AsShotNeutral inverse); pixels baked. | Per-frame `AsShotNeutral` tag; Resolve honours per-frame (Q1.0 T1 ✓). | **Better in v0.7** — Resolve user can override |
| **`Exposure2012`** | Applied Stage 11 (Adobe ExposureRamp); pixels baked. | Per-frame `BaselineExposure` tag; Resolve honours per-frame (Q1.0 T2 ✓). | **Better in v0.7** — Resolve user can override |
| **`Blacks2012`** | Applied Stage 11; pixels baked. | Absorbed into per-frame Bayer-plane gain (low-region multiplier). | Parity — applied in pixel space either way; v0.7 user cannot override in Resolve (the gain is pre-baked into Bayer) |
| **LRT mask-correction deltas** (HG / Deflicker / Global) | Applied via DevelopOps per-frame WB adjustment in Stage 2. | Per-frame Bayer-plane scalar gain, applied pre-LJPEG (Q1.0 T4 ✗ blocks metadata path). | Parity in pixel space; v0.7 user cannot override deltas in Resolve |
| **`ToneCurvePV2012`** | Applied Stage 11 via Hermite spline; pixels baked. | **Dropped with render-time warning** (Q1.0 T3 ✗ — file-level `ProfileToneCurve` ignored by Resolve; bundled DCP wins). | **Regression in v0.7** — LRT-authored tone curves lost; user must re-apply as Resolve grade-page node |
| **`Saturation` / `Vibrance` / `Contrast2012`** | Applied Stage 12; pixels baked. | **Dropped with warning** (same reason). | **Regression in v0.7** |
| **DCP colour science** (ColorMatrix, ForwardMatrix, HueSatMap, LookTable, ProfileToneCurve) | Applied in pipeline Stages 3–9 from the loaded DCP (e.g. Camera Standard). Validated to < 1 ΔE vs `dng_validate`. | **Resolve uses its bundled DCP**, not our source-DNG-embedded one. Colour science is delegated to Resolve. | **Indeterminate** — depends on Resolve's bundled DCP quality for the target camera. Gym/rose tested only with Nikon D750 Camera Standard; Resolve's bundle may or may not match |
| `Highlights2012` / `Shadows2012` / `Whites2012` | Already dropped in v0.6 (closed-source LR PV5 parametric tone) | Dropped (inherited) | Parity |
| `Sharpness` | No-op in v0.6 (sharpening = grade) | No-op | Parity |

**Net for the timelapse user:**

- Holy Grail authoring (the headline LRT feature) survives and gets
  *better* — user can dial WB/exposure in Resolve after the fact.
- LRT mask deltas survive in pixel space; lose re-grade flexibility.
- LR PV2012 tone / sat / vib / contrast — **lost.** Need to re-apply
  downstream.
- DCP-driven colour science — handed off to Resolve, which may or may
  not match v0.6's < 1 ΔE result depending on bundled DCP coverage.

---

## 5. Resolve workflow comparison

### v0.6 cinema-linear (32-bit float TIFF)

1. lrt-cinema renders → folder of `.tif` (one per frame).
2. Resolve Media Pool: drag folder → recognised as image sequence
   (treated as one clip).
3. Project Settings → Color Management → DaVinci YRGB Color Managed.
4. Timeline color space → Rec.2020 Linear (match TIFF's encoding).
5. Grade.

### v0.6 cinema-aces (32-bit float EXR PIZ)

1. lrt-cinema renders → folder of `.exr` (one per frame).
2. Resolve Media Pool: drag folder.
3. Project Settings → Color Management → ACES.
4. Input color space (clip) → ACES IDT clean 3×3 (linear Rec.2020 → ACES AP1).
5. Grade.

### v0.7 cinema-cdng

1. lrt-cinema renders → folder of `.dng` (one per frame).
2. Resolve Media Pool: drag folder → auto-recognised as CinemaDNG clip.
3. Project Settings → Camera Raw → CinemaDNG → Decode Using = Camera Metadata (default).
4. Project Settings → Color Management → DaVinci YRGB (NOT RCM — RCM
   bypasses Camera Raw on CDNG; would defeat our per-frame metadata).
5. Per-clip Camera Raw palette: WB and exposure carry our authored
   per-frame values as defaults; user can override.
6. Grade.

The CDNG path adds two new user-side concerns:

- Camera Raw panel exists and is meaningful (gives a re-develop knob).
- LRT tone-curve shape is missing — user may need a manual tone curve
  on the grade page to restore intent.

---

## 6. Encode + decode speed

Measured/estimated for 24 MP (6032 × 4032) on M-series Mac:

| Op | v0.6 cinema-linear | v0.6 cinema-aces | v0.7 cinema-cdng |
|---|---:|---:|---:|
| **Encode** (per frame) | ~300 ms (tifffile float write) | ~400 ms (OpenEXR PIZ) | **~1500–2500 ms** (tifffile tag write + libjpeg LJPEG-92 encode of 24 MP Bayer) |
| **Decode** (per frame, in Resolve) | ~50 ms (TIFF read, no debayer) | ~80 ms (EXR PIZ decode) | **~80 ms** (LJPEG decode + GPU debayer; Resolve is hardware-accelerated for CDNG specifically) |
| **End-to-end render** 1000 frames at 8 workers | ~37 sec | ~50 sec | **~210 sec** (more compute, but I/O reduced ~13×) |

**Trade:** v0.7 encode is **5–8× slower per frame** than v0.6. But:

- Disk write time drops proportional to file size (~13× less data).
  On slow storage (network, USB-3 NAS) the I/O reduction more than
  pays for the CPU encode time.
- Re-render cost is symmetric — v0.7 is slower to re-render but
  faster to *transfer*, and the v0.7 artefact is the master output
  going forward.
- Resolve playback / scrubbing of CDNG is faster than EXR on a per-
  pixel basis because BMD hardware-accelerates the CDNG path
  specifically (the debayer is GPU-bound and very efficient).

For low-frame-count workflows or one-off renders, v0.6 is faster
end-to-end. For multi-thousand-frame timelapses, v0.7's I/O savings
dominate.

---

## 7. Bit depth and dynamic range

| | v0.6 cinema-linear | v0.6 cinema-aces | v0.7 cinema-cdng |
|---|---|---|---|
| Container precision | 32-bit float per RGB channel | 32-bit float per RGB channel | 14-bit int per Bayer sample |
| Effective precision post-decode | ~14 bits ÷ pipeline noise floor (~10–11 effective bits on the bottom stops) | same | **15-bit interpolated** (Bayer demosaic typically gives ~14.5–15 effective bits per RGB channel post-demosaic) |
| Highlight headroom (overrange) | unlimited (float; >1.0 carried explicitly) | unlimited | bounded by `WhiteLevel` tag (typically ~15520 / 2^14 ≈ 0.95) plus gain headroom from `BaselineExposure` |
| HDR support | full HDR linear | full HDR linear | full HDR linear (per-frame `BaselineExposure` carries exposure ramps cleanly; sensor's 14 stops of DR fully preserved) |
| Quantisation noise added by emission | none (float) | none (float, PIZ lossless) | none (LJPEG-92 lossless on Bayer) |

v0.6 has theoretically higher precision (float per channel) but in
practice both formats carry the same sensor information at the same
fidelity. v0.7 ships closer to the sensor; v0.6 ships the same data
after pipeline computation expanded the bit width.

---

## 8. Colour space and gamut

| | v0.6 cinema-linear | v0.6 cinema-aces | v0.7 cinema-cdng |
|---|---|---|---|
| Emitted color space | linear Rec.2020 (D65) | linear Rec.2020 (D65), tagged for ACES IDT | sensor space (camera-native CFA) |
| Whitepoint | D65 | D65 | "As shot" (`AsShotNeutral` per-frame) |
| CIE diagram coverage | Rec.2020 primaries (wide) | Rec.2020 primaries (wide), ACES interprets as AP0 input | sensor's full chromaticity coverage (typically wider than Rec.2020) |
| Colour science correctness | < 1 ΔE2000 vs `dng_validate` (gym 0.79, rose 0.84) | same | **delegated to Resolve's bundled DCP** — needs separate ΔE characterisation |

v0.6's colour science is project-controlled and validated. v0.7
delegates colour to Resolve's bundled DCP library; we lose direct
control of the ColorMatrix / ForwardMatrix path. Whether this matters
depends on whether Resolve's bundled Nikon D750 Camera Standard
matches Adobe's (which we've validated against).

---

## 9. Metadata richness

| | v0.6 cinema-linear | v0.6 cinema-aces | v0.7 cinema-cdng |
|---|---|---|---|
| EXIF (camera, lens, ISO, shutter, aperture) | none | none | **full** (carried from source DNG) |
| Camera profile (DCP) | not embedded | not embedded | **embedded** (from source DNG, though Resolve overrides) |
| Per-frame WB | n/a (baked in pixels) | n/a | **`AsShotNeutral` tag** |
| Per-frame exposure | n/a | n/a | **`BaselineExposure` tag** |
| Per-frame look (tone curve, sat) | n/a (baked) | n/a | not transported (dropped — see §4) |
| LRT source XMP | lost at render | lost | **carried as XMP block in DNG** (round-trip auditability) |
| `DevelopOps` JSON | lost | lost | **carried as XMP block** |
| Preview thumbnail | none (would need separate JPEG) | none | **embedded JPEG preview** (Adobe DNG Converter generates) |

CDNG is metadata-rich by design. v0.6 TIFF/EXR strips all camera /
develop intent at write time. v0.7 keeps the chain of provenance.

This matters for: archival (knowing what camera / lens shot it years
later), reproducibility (replaying the LRT XMP), and tooling
(third-party software can re-develop without lrt-cinema).

---

## 10. Code disposition

### v0.6

| Module | LOC | Status |
|---|---:|---|
| `pipeline.py` | ~700 | Production renderer — Stages 1–9 |
| `develop_ops.py` | ~350 | Production renderer — Stages 11–12 |
| `dcp.py` | ~1100 | DCP loader + matrix interpolation |
| `lut3d_baker.py` | ~280 | HSV cube application |
| `output.py` | ~250 | TIFF + EXR writers |

### v0.7

| Module | LOC | Status |
|---|---:|---|
| `pipeline.py` | ~700 | **Becomes offline validator** — still tested vs `dng_validate`, no longer production |
| `develop_ops.py` | ~350 | Same — validator only |
| `dcp.py` | ~1100 | Same — validator only (Resolve uses its own DCP) |
| `lut3d_baker.py` | ~280 | Same |
| `output.py` | ~250 | TIFF/EXR writers retained (deprecated, removed in v0.8) |
| **`cdng_emit.py`** (new) | ~600–900 | **Production emission path** — Bayer-bake + tag write + LJPEG encode |

Net LOC delta: **+600 to +900**. Production path mostly new; legacy
path stays for the validator + v0.6 fallback during deprecation.

In v0.8 (after v0.7 stabilises), `output.py` legacy writers can be
removed (~250 LOC). `pipeline.py` / `develop_ops.py` / `dcp.py` stay
forever as the colour-science validator.

---

## 11. Dependencies

### v0.6 runtime deps

- `rawpy` (libraw bindings; demosaic) — already required for source DNG read
- `colour-science` (color conversion) — already required
- `scipy` (interpolation helpers) — already required
- `tifffile` (BSD-3) — production TIFF writer
- `OpenEXR` (ASWF binding; BSD-3) — production EXR writer
- `numpy`
- `defusedxml`

### v0.7 runtime deps (delta)

- `tifffile` — repurposed for DNG tag write (DNG is TIFF + tags)
- **NEW: `pylibjpeg-libjpeg`** (MIT) OR direct `libjpeg-turbo` via
  ctypes — for lossless JPEG-92 encode of Bayer strip
- **NEW: `exiftool`** subprocess wrapper — fallback for binary tags
  (OpcodeList3) if any survive into v0.7 (currently none after spike
  shifted to Bayer-bake)
- `OpenEXR` retained during deprecation window (v0.7 → v0.8)

Net: +1 small dep (`pylibjpeg-libjpeg`, ~5 MiB). exiftool is optional /
not on the production path anymore.

---

## 12. Validation surface

### v0.6 gate

`tests/test_pipeline.py` — ΔE2000 < 1.0 mean between
`pipeline.render_frame(NEF, XMP)` and `dng_validate(NEF + XMP)` on
gym + rose. Currently passing (gym 0.79 ΔE, rose 0.84 ΔE).

### v0.7 gates

1. **Sensor round-trip** — `dng_validate(emitted CDNG)` bit-exact
   match on raw Bayer plane vs `dng_validate(source DNG)`. Pure
   integrity check on the LJPEG encoder.
2. **Bake-vs-metadata ΔE** — ΔE2000 between `pipeline.render_frame`
   (the v0.6 reference, retained as validator) and
   `dng_validate(emitted CDNG)` on gym + rose. Covers the per-frame
   metadata path (AsShotNeutral, BaselineExposure) end-to-end.
3. **Resolve ingest ΔE** — ΔE2000 between Resolve's decode of the
   emitted CDNG and `pipeline.render_frame` reference. **Manual
   checkpoint** in CI absent Resolve Studio's scripting API; logged
   per release.
4. **Bayer-bake fidelity** — synthetic test: apply a known per-frame
   gain via `cdng_emit`, decode, compare to the same gain applied in
   pipeline. Bit-exact post-WhiteLevel-rescale.

v0.7 adds three gates on top of v0.6's one. More to maintain; greater
confidence in correctness.

The v0.6 pipeline.py ΔE gate **continues to pass** in v0.7 — it's now
the reference the new emission is validated against. That's an
important continuity property: we don't lose the calibration we earned.

---

## 13. Future-proofing

| | v0.6 TIFF | v0.6 EXR | v0.7 CDNG |
|---|---|---|---|
| Format age | 1986 (TIFF), 1999 (TIFF 6) | 1999 (OpenEXR 1.0), 2024 (3.x) | 2008 (CinemaDNG 1.0), 2024 (CinemaDNG 1.1) |
| Industry adoption | universal | cinema/VFX standard | post-production raw, narrower than EXR |
| Compression upgrade path | `tifffile` ZSTD (~2×) — marginal | DWAB/DWAA (~10–30× lossy) — feasible | DNG 1.7 + JXL (20–50× lossless) — gated on Resolve catching up |
| Deprecation risk | nil | nil | **moderate** — Apple deprecated CDNG decode in macOS post-Mojave; Resolve still supports as of 20; BMD's incentive to maintain depends on their roadmap |
| Reverse engineering risk | nil (open spec) | nil (open spec) | low (DNG is Adobe-published open spec) |

v0.6 is bulletproof on longevity. v0.7 has more deprecation risk but a
stronger compression upgrade path (JXL = 2–3× more compression in the
same workflow).

---

## 14. Failure modes

| Failure type | v0.6 cinema-linear | v0.6 cinema-aces | v0.7 cinema-cdng |
|---|---|---|---|
| Encoder memory OOM | rare (32-bit float ~280 MiB working set) | rare | rare (16-bit Bayer 50 MiB) |
| Colour-space conversion bug | caught by ΔE gate | caught | caught (validator path unchanged); new bug surface: tag write |
| Tag layout error in writer | n/a | n/a | **new: corrupts DNG, Resolve silent reject** — needs strict tag-byte tests |
| LJPEG encode failure | n/a | n/a | **new: libjpeg dependency surface** — needs encode failure tests |
| WhiteLevel rescale overflow | n/a | n/a | **new: per-frame gain can clip** — needs overflow assertions |
| Sensor strip corruption | n/a (no sensor in v0.6 emission) | n/a | possible if LJPEG decode fails; sensor round-trip gate covers |
| Downstream tool drops the file | very rare (TIFF universal) | rare | possible if Resolve loses CDNG support (low but non-zero) |

v0.7 has **more failure surface** than v0.6 — at least three new bug
classes (tag layout, LJPEG, WhiteLevel rescale). Validation gates 1
and 4 (§12) are designed to catch each one.

---

## 15. Use cases that win / lose

### Big wins for v0.7

- **Long timelapses** (1000+ frames). I/O reduction dominates compute cost.
- **Holy Grail ramps with downstream re-grading**. User wants to dial
  WB/exposure differently after seeing the v0.6 baseline. v0.7 lets
  them; v0.6 doesn't.
- **Storage-constrained workflows.** A laptop SSD can hold 5000 v0.7
  frames; only 350 v0.6 frames.
- **Archival.** v0.7 carries provenance (EXIF, source XMP, DCP).
- **Faster Resolve scrubbing** on CDNG-aware GPUs.

### Wins for v0.6

- **One-shot still renders.** v0.6's encode is faster; no re-develop value needed.
- **Sequences with heavy LR PV2012 authoring.** v0.6 preserves
  `ToneCurvePV2012` / `Saturation` / `Vibrance` / `Contrast2012`;
  v0.7 drops them.
- **Workflows that need bit-identical reproducibility across multiple
  decoders.** v0.6 TIFF/EXR is universally decoded; v0.7 CDNG decode
  varies by tool (Resolve's bundled DCP vs Adobe Camera Raw's vs
  RawTherapee's).
- **VFX hand-off to Nuke / Houdini / OCIO pipelines.** v0.6 EXR is
  the standard intermediate; v0.7 CDNG would require ingest plugins
  or re-render to EXR upstream of VFX.

### Where they're equivalent

- Holy Grail kelvin authoring (both work; v0.7 stays editable, v0.6 doesn't)
- Mask-correction deltas (both apply correctly; v0.7 is Bayer-baked, v0.6 is pixel-baked)
- Sharpness handling (no-op in both)
- Highlights2012 / Shadows2012 / Whites2012 (dropped in both)

---

## 16. The discriminating questions

Three questions decide whether v0.7 is worth the swap from v0.6:

1. **Is the size reduction (10–18×) worth losing LR PV2012 tone /
   sat / vib / contrast?** For pure-Holy-Grail timelapse workflows
   (where LRT tone ops are minimal anyway), yes. For users authoring
   heavy tone shaping in LRT/Lightroom, no — until D++ (Resolve
   plugin) ships.

2. **Does Resolve's bundled DCP for the target camera produce
   acceptable colour?** If yes (typical for Nikon/Canon/Sony), v0.7
   is a clean win. If no (some bodies are missing or poorly profiled),
   v0.7 introduces a colour-science regression v0.6 doesn't have.
   Phase 4 ΔE gate (§12) catches this.

3. **Is the re-developability story worth the new complexity (LJPEG,
   tag write, three new validation gates, +600–900 LOC)?** Strategic
   answer: yes if lrt-cinema's positioning shifts from "renderer" to
   "LRT-XMP-to-CDNG baker for Resolve workflows." Tactical answer
   depends on user demand.

---

## 17. Recommendation envelope

The v0.7 SPEC is correct if all three of these hold:

- The user's primary workflow is timelapses, not stills.
- Holy Grail is the LRT feature being authored; LR PV2012 tone ops
  are minor / used as a starting point.
- Storage and Resolve playback performance are bottlenecks today.

If any of those fail, v0.6 should be preserved or v0.7 should
incorporate a Resolve plugin (D++) before declaring v0.6 deprecated.

v0.7's `cinema-linear` / `cinema-aces` deprecation is **conditional**:
keep them as supported presets through v0.8 minimum; possibly
permanently as the "VFX hand-off" preset. The user-facing copy should
position v0.7 cdng as "the cinema timelapse preset" — not "the only
preset."

---

## 18. See also

- [v07-emission-format.md](v07-emission-format.md) — the v0.7 SPEC itself.
- [v06-architecture.md](v06-architecture.md) — the v0.6 pipeline spec.
- [v07-resolve-cdng-spike-results.md](v07-resolve-cdng-spike-results.md) — the
  empirical Q1.0 spike results that fixed v0.7's metadata-vs-bake split.
- [v07-proprietary-raw-codec-feasibility.md](v07-proprietary-raw-codec-feasibility.md) — the
  PRR/BRAW/CineForm alternative-paths characterisation.
- [SCOPE.md](../../SCOPE.md) — v0.6's honest per-feature status table.
