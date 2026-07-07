# Implementation Scope (v0.8)

Honest per-feature status. v0.8 makes `lrtimelapse` (16-bit sRGB display
TIFF, for the LRT round-trip) the default emission and keeps the
scene-linear ACEScg EXR masters (`cinema-linear-finished` γ /
`cinema-linear-master` β) as opt-in targets. The earlier β-XML Resolve
project-sidecar plan — and the §2.B X1–X6 increments built around it —
was ruled out: Resolve preserves no per-frame grade keyframes through any
documented import path (see [docs/archive/DECISIONS.md](docs/archive/DECISIONS.md) §4).

See [CHANGELOG.md](CHANGELOG.md) for the release history.

## What works

| Capability | Status | Notes |
|---|---|---|
| LRT XMP parser (full Camera Raw Settings field set) | shipped | `xmp_parser.py` |
| Per-frame keyframe interpolation (linear) | shipped | `interpolation.py` |
| Holy Grail kelvin override | shipped | `DevelopOps.temperature_k` honored per frame |
| LRT mask-correction per-frame deltas (HG / Deflicker / Global) | shipped | `--apply-lrt-offsets` default |
| Adobe DNG 1.7.1 render pipeline (stages 1–9) | shipped | `pipeline.py`, ΔE < 1 vs `dng_validate` |
| Per-camera DCP profile loading + ColorMatrix interpolation | shipped | `dcp.py`; auto-detect from `$LRT_CINEMA_PROFILES` / `~/.config/lrt-cinema/profiles` (open `.npz`); clean-room `.dcp` reader for `--dcp` |
| LR Exposure2012, Blacks2012, ToneCurvePV2012, Saturation, Vibrance, Contrast2012 | shipped | `develop_ops.py` (greenfield from public LR formulas) |
| LR HSL panel (8 hue bands × Hue/Saturation/Luminance) | shipped (dual-mode) | **Faithful** (sRGB TIFF): `develop_ops.apply_hsl`; Adobe-hexcone HSV, smooth partition-of-unity band weights, saturation-gated neutral-safe luminance. **Perceptual** (ACEScg master, v0.9 step 3): `develop_ops._apply_hsl_perceptual`; the **same 8-band partition-of-unity** in **OKLCh proper** (gamut-agnostic; Okhsl/Okhsv are sRGB-bound) — band centres in OKLCh hue degrees, chroma-gated luminance, **no top clamp** (overrange survives; out-of-AP1 → the shared ACES RGC pass). Measurable win: hue constancy under a Luminance sweep (no Abney drift). Band centres / magnitudes / chroma gate are documented tuning (perceptual makes no LR-fidelity claim; LR's exact math is closed) |
| LR Color Grading wheels (Shadows/Midtones/Highlights/Global + Blending/Balance) | shipped (dual-mode) | **Faithful** (sRGB TIFF): `develop_ops.apply_color_grade`; luminance-masked zero-sum chroma tint added in linear ProPhoto, partition-of-unity zone masks. **Perceptual** (ACEScg master, v0.9 step 2): `develop_ops._apply_color_grade_perceptual`; **offset-only ASC-CDL** (slope=power=1) in ACEScct log — a uniform Luminance lift + the same zero-sum chroma direction as an additive log delta, log-domain zone proxy. Parses `crs:ColorGrade*` + legacy `crs:SplitToning*` aliases. Tint strengths / mask shape / CDL constants are documented tuning (perceptual makes no LR-fidelity claim) |
| LR Highlights2012/Shadows2012 → scene-referred LOCAL translation (**both intents**) | **shipped 2026-07-07 (probe-calibrated)** | `scene_tone.apply_scene_hlsh` at pipeline slot-7b (balanced scene-linear camera RGB, post scene-EV gain): guided base/detail split + MEASURED anchor tone tables (calibrated on owner LR Classic exports of DSC_4053 at −50/−100 / +50/+100; `tools/cal_hlsh_fit.py`) + near-black chroma roll. Beats the best global arm at every anchor; residual per anchor in CLAIMS round-2 rows. Byte-exact no-op at zero sliders; opposite slider signs mirrored-EXTRAPOLATED (no anchors yet) |
| LR Whites → scene-referred DR-compression (**PERCEPTUAL only**) | shipped (v0.9); direction measured INVERTED vs LR (2026-07-07) | `develop_ops.apply_dr_compression` (now Whites-only — H/S moved to slot-7b); homomorphic log-domain compression toward the 0.18 anchor + guided base/detail (He 2013). Faithful path drops Whites (render-time warn). CALWH50 probe: LR's +Whites BRIGHTENS the top; this op's c_top compresses (documented tuning; own calibration queued). DECISIONS §5 amendment |
| LR Texture/Clarity → edge-aware local-contrast boost (**PERCEPTUAL only**) | **shipped (v0.9)** | `develop_ops.apply_texture_clarity`; the **boost-detail mode of the same guided base/detail engine** as DR-compression (the inverse: a two-band split boosts detail rather than attenuating the base). Texture = a uniform fine-detail boost; Clarity = a midtone-weighted mid-scale local-contrast boost. Driven by the existing `crs:Texture`/`crs:Clarity2012` knobs — **no new control**. Faithful path drops them with **their own** render-time warning (not the DR-compression story). Edge-aware → step-edge halo sub-1% of the plateau range at full sliders vs a naive USM at ~580% (the guided filter is the measured-clean first cut, not provably halo-free; the LLF proto stays deferred — v10c). Constants are documented tuning; **no LR-fidelity claim**. DECISIONS §7 step 4 |
| NEF→DNG preprocessing | shipped | `dng_convert.py` wraps **dnglab** (open-source, LGPL-2.1; Adobe-free); mtime+size cache |
| `lrtimelapse` output (16-bit sRGB display TIFF, embedded ICC, `LRT_NNNNN`) | **shipped; v0.8 DEFAULT** | `output.py`; the LRT video round-trip emission |
| `cinema-linear-finished` output (16-bit half DWAB EXR, ACEScg; γ) | shipped | `output.py`; scene-linear master for Resolve / ACES |
| `cinema-linear-master` output (16-bit half DWAB EXR at Stage 7, ACEScg; β) | shipped | `output.py` + `pipeline.py` `stop_after_stage=7`; skips DCP LookTable + ProfileToneCurve for HDR headroom |
| Parallel worker pool | shipped | `--workers N`, `ProcessPoolExecutor` |

## Known limitations / deferred

| Item | Owner | Notes |
|---|---|---|
| `stills-finished` preset (Rec.2020 + AgX) | deferred | `NotImplementedError`. AgX port from Blender reference or `colour-science` primitives. |
| `scene_kelvin` computation regression at high K | deferred | Currently hardcoded 5500K. `neutral_to_kelvin` solver lives in `pipeline.py` but converges to values that regress ΔE on rose via HSM mired-blend divergence. |
| `Whites2012` | **shipped on perceptual; dropped on faithful** | Adobe's PV2012 tone math is closed-source → dropped + render-time warning on the faithful/sRGB path (`cli._warn_dropped_ops`). Perceptual path: `apply_dr_compression` (direction measured inverted vs LR — see "What works"). `Highlights2012`/`Shadows2012` are NO LONGER dropped anywhere — probe-calibrated slot-7b translation, both intents (see "What works"). Follow-ups: Whites calibration; local-Laplacian base producer (deferred, v10c). |
| `Sharpness` | no-op | Sharpening belongs in the grade stage; may revisit later. |
| Third test scene (tungsten / fluorescent) | deferred | Surfaces whether 5500K is load-bearing or coincidental on the current gym + rose pair. |
| Smooth (Catmull-Rom) keyframe interpolation | future | Was in v0.2 plan; deferred until real-LRT-sequence preference signal arrives. |
| Linux RAW→DNG | resolved | dnglab ships official Linux builds (Adobe never did) — it is now the sole converter on every platform. `--no-dng-convert` remains a fallback for boxes with no dnglab binary. |

## Output presets

| Preset | Container | Color space | Library | Status |
|---|---|---|---|---|
| `lrtimelapse` | 16-bit sRGB display TIFF (embedded ICC), `LRT_NNNNN` | sRGB (display) | `tifffile` | **v0.8 DEFAULT** |
| `cinema-linear-finished` | 16-bit half EXR (DWAB) at Stage 13 | scene-linear ACEScg (AP1) | `OpenEXR` ASWF | γ (Resolve/ACES) |
| `cinema-linear-master` | 16-bit half EXR (DWAB) at Stage 7 | scene-linear ACEScg (AP1) | `OpenEXR` ASWF | β (HDR headroom) |
| `stills-finished` | display Rec.2020 + AgX | display-referred | n/a | NotImplemented (deferred) |

**Removed in v0.8: `cinema-linear` / `cinema-aces`.** Both emitted *linear
Rec.2020* — a delivery gamut misused as scene-referred (a colour-science error,
no matching Resolve Input entry). ACEScg (AP1) / ACES2065-1 (AP0) are the only
standards-aligned scene-linear gamuts; see CLAUDE.md §"Colour-space allowlist"
and [`docs/research/v08-linear-exr-gamut-resolve-nuke.md`](docs/research/v08-linear-exr-gamut-resolve-nuke.md).

## Validation

End-to-end ship gate: `tests/test_pipeline.py` renders the project's
test scenes through `pipeline.render_frame` and asserts mean ΔE2000
< 1.0 against Adobe `dng_validate` (their own DNG SDK reference
renderer).

Current measurements (v0.8 head, re-run 2026-05-30):

| Scene | DCP | Mean ΔE | P50 | < 1 ΔE pixels |
|---|---|---:|---:|---:|
| Gym (DSC_4053, D750) | Camera Standard | **0.026** | 0.000 | 100% |
| Rose (d750_sample) | Adobe Standard | **0.545** | 0.577 | 97.8% |

Gym is an effective bit-match (P50 0.000, 100% of px under 1 ΔE). The drop from
the pre-fix 0.789 was a single Stage-9 change: the DCP ProfileToneCurve is now
applied as Adobe's hue/saturation-preserving `RefBaselineRGBTone` (curve the
max+min channel, interpolate the middle) instead of per-channel. The old
per-channel tone error fired wherever channels differ (edges + saturated
colour) and was invisible on neutrals (r=g=b), so the flat-pixel median was
already 0.000. See `docs/archive/VALIDATION.md` for the decomposition.

## Floors

Reference-comparison floors (characterized, not ship-gating). Distinguish the
part we *own and can tune* from the reference's own irreducible look:

- **vs `dng_validate` (the north-star): 0.026 gym / 0.545 rose mean** (gym P50
  0.000, 100% of px < 1 ΔE — an effective bit-match). No theoretical floor on
  the colour maths: the pre-fix 0.789 gym was a per-channel ProfileToneCurve
  error at Stage 9, not a demosaic-edge floor; switching to Adobe's
  hue/saturation-preserving `RefBaselineRGBTone` collapsed it. The synthetic
  flat-patch chart confirms it (neutral median 0.000, chromatic mean 0.05).
- **vs LRT preview: ~2 ΔE** post-affine (was mislabelled 2.03 from the
  darktable era; re-measured 2026-05-30: raw 2.92 / affine-residual ~2.18).
  Decomposes as **our-vs-Adobe-DNG (now an effective bit-match, gym 0.026) + ~2
  (LR closed-source PV5 look + 8-bit JPEG — the reference's, not ours)**.
- vs in-camera JPEG: ~6 ΔE (camera uses Nikon Picture Control, not Adobe DCP).

For the Adobe purge, `dng_validate` stays a test-only oracle and the proven
**0.026** (gym, median 0.000) is the target to tune open-DCP renders back toward.

## CLI surface

```
lrt-cinema render
  --input PATH               (required)  source RAW + LRT XMP folder
  --output PATH              (required)  destination folder
  --target {lrtimelapse,resolve,master}  default lrtimelapse; expands to a preset
  --preset NAME              advanced; overrides --target
                             (lrtimelapse | cinema-linear-finished |
                              cinema-linear-master | stills-finished)
  --render-intent {faithful,perceptual}  which grading MATH (DECISIONS.md §7), not
                             a creative control — values come from the XMP knobs.
                             Default per target: sRGB TIFF → faithful (Adobe look);
                             ACEScg EXR → perceptual (our math). Flag overrides.
                             perceptual: Color Grade → ASC-CDL, HSL → OKLCh,
                             DR-compression — all shipped (v0.9 steps 2-3)
  --from-frame N             default 0
  --to-frame N               default = end of sequence
  --dry-run                  print what would render; no I/O
  --quiet                    suppress per-frame progress
  --apply-lrt-offsets        default on; --no-apply-lrt-offsets to disable
  --dcp PATH                 override auto-detect
  --workers N                default os.cpu_count() // 2
  --no-dng-convert           skip NEF→DNG; libraw-direct (Linux fallback)
```

## DCP auto-detect

When `--dcp` is not supplied, the renderer probes the first source
RAW's EXIF Make/Model (TIFF IFD0; handles NEF / DNG / ARW / RW2 / RAF /
ORF / FFF) and searches in this preference order:

1. `$LRT_CINEMA_PROFILES` — typically points at a cloned sister
   `lrt-cinema-profiles` data repo.
2. `~/.config/lrt-cinema/profiles/` (or `%APPDATA%/lrt-cinema/profiles/`
   on Windows) — populated by `tools/extract_dcp_library.py <source_root>`
   from any `.dcp` source you are licensed to use (a dcamprof/RawTherapee
   profile set, or an Adobe CameraProfiles directory if you have one).
3. None → clear actionable error message. The runtime never scans an Adobe
   install; pass `--dcp PATH` to supply a profile explicitly — a `.dcp`
   (read clean-room) or an extracted `.npz`.

Canon CR3 (ISO BMFF) and other non-TIFF RAWs fall through to step 3
regardless; the user must pass `--dcp` explicitly for those bodies.

## Frame ordering

Source RAW frames sort lexicographically by filename. Sequences must
zero-pad frame indices (`IMG_0001.NEF`, not `IMG_1.NEF`).

## Schema sources

The LRTimelapse XMP schema is reverse-engineered from public LRT
documentation + sample XMPs from LRTimelapse Pro 7.5.3. See
`docs/reference/lrtimelapse/XMP_SCHEMA.md` for the calibration record.

## See also

- **Standalone GUI app (LRT *replacement*) — separate future track, currently
  ON HOLD / NO-GO** (needs a Vulkan/native-systems engineer and/or a design
  originator first; not part of this CLI's scope):
  [docs/archive/DECISIONS.md](docs/archive/DECISIONS.md) §6.
- [docs/archive/PIPELINE.md](docs/archive/PIPELINE.md) — the canonical as-built pipeline reference.
- [CHANGELOG.md](CHANGELOG.md) — the empirical journey from 6.37 ΔE (dt-cli) to
  the in-process Python pipeline.
- [CHANGELOG.md](CHANGELOG.md) — release notes.
