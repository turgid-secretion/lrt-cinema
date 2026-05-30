# Implementation Scope (v0.7)

Honest per-feature status. v0.7.0 ships the γ preset
(`cinema-linear-finished`, half-float DWAB EXR). β-XML
(`cinema-linear-master`, Stage-7 EXR + Resolve project sidecar) lands
in v0.7.1; the §2.B free-upgrade increments (X1–X6) extend β-XML
coverage in subsequent v0.7.x releases.

See [`docs/research/v07-spec-revision-plan.md`](docs/research/v07-spec-revision-plan.md)
for the authoritative roadmap.

## What works

| Capability | Status | Notes |
|---|---|---|
| LRT XMP parser (full Camera Raw Settings field set) | shipped | `xmp_parser.py` |
| Per-frame keyframe interpolation (linear) | shipped | `interpolation.py` |
| Holy Grail kelvin override | shipped | `DevelopOps.temperature_k` honored per frame |
| LRT mask-correction per-frame deltas (HG / Deflicker / Global) | shipped | `--apply-lrt-offsets` default |
| Adobe DNG 1.7.1 render pipeline (stages 1–9) | shipped | `pipeline.py`, ΔE < 1 vs `dng_validate` |
| Per-camera DCP profile loading + ColorMatrix interpolation | shipped | `dcp.py`, auto-detect from Adobe DNG Converter install or `$LRT_CINEMA_PROFILES` |
| LR Exposure2012, Blacks2012, ToneCurvePV2012, Saturation, Vibrance, Contrast2012 | shipped | `develop_ops.py` (greenfield from public LR formulas) |
| NEF→DNG preprocessing | shipped | `dng_convert.py` wraps Adobe DNG Converter; mtime+size cache |
| `cinema-linear-finished` output (16-bit half DWAB EXR; γ) | **shipped v0.7.0** | `output.py`; v0.7 default; 10–18× smaller than `cinema-aces` |
| `cinema-linear-master` output (16-bit half DWAB EXR at Stage 7; β) | **shipped v0.7.1** | `output.py` + `pipeline.py` `stop_after_stage=7`; skips DCP LookTable + ProfileToneCurve for HDR headroom |
| `cinema-linear` output (32-bit float linear Rec.2020 TIFF) | shipped | `output.py` via `tifffile`; v0.6 back-compat |
| `cinema-aces` output (32-bit float linear Rec.2020 PIZ EXR) | **deprecated** | `output.py`; one-time `DeprecationWarning`; removal in v0.8 |
| Parallel worker pool | shipped | `--workers N`, `ProcessPoolExecutor` |

## Known limitations / deferred

| Item | Owner | Notes |
|---|---|---|
| `stills-finished` preset (Rec.2020 + AgX) | v0.6.x | `NotImplementedError` in v0.6. AgX port from Blender reference or `colour-science` primitives. |
| `scene_kelvin` computation regression at high K | v0.6.x | Currently hardcoded 5500K. `neutral_to_kelvin` solver lives in `pipeline.py` but converges to values that regress ΔE on rose via HSM mired-blend divergence. |
| `Highlights2012`, `Shadows2012`, `Whites2012` | dropped | LR PV2012 parametric tone math is closed-source. Render-time warning surfaces them. |
| `Sharpness` | no-op | Sharpening belongs in the grade stage. v0.6.x may revisit. |
| Third test scene (tungsten / fluorescent) | v0.6.x | Surfaces whether 5500K is load-bearing or coincidental on the current gym + rose pair. |
| Smooth (Catmull-Rom) keyframe interpolation | future | Was in v0.2 plan; deferred until real-LRT-sequence preference signal arrives. |
| Linux Adobe DNG Converter | not feasible | Adobe ships no Linux build; use `--no-dng-convert` (NEF direct, ~0.5 ΔE regression). |

## Output presets

| Preset | Container | Color space | Library | Status |
|---|---|---|---|---|
| `cinema-linear-finished` | 16-bit half EXR (DWAB) at Stage 13 | linear Rec.2020 | `OpenEXR` ASWF | **v0.7 default (γ)** |
| `cinema-linear-master` | 16-bit half EXR (DWAB) at Stage 7 | linear Rec.2020 | `OpenEXR` ASWF | **v0.7.1 (β, Option B)** |
| `cinema-linear` | 32-bit float TIFF | linear Rec.2020 | `tifffile` | back-compat |
| `cinema-aces` | 32-bit float EXR (PIZ) | linear Rec.2020 | `OpenEXR` ASWF | deprecated (v0.8 removal) |
| `stills-finished` | 16-bit int TIFF | Rec.2020 + AgX | n/a | NotImplemented (v0.6.x) |

**β-XML deferred to v0.8.** The originally-planned `cinema-linear-master`
sidecar variant (per-sequence Resolve project XML carrying LRT keyframes)
proved infeasible — Resolve does not preserve per-frame grade keyframes
through any documented import path. See
[`docs/research/v07-beta-xml-deadend.md`](docs/research/v07-beta-xml-deadend.md).
The v0.7.1 `cinema-linear-master` preset is the Option B pivot: Stage 7
pixel bake without sidecar.

## Validation

End-to-end ship gate: `tests/test_pipeline.py` renders the project's
test scenes through `pipeline.render_frame` and asserts mean ΔE2000
< 1.0 against Adobe `dng_validate` (their own DNG SDK reference
renderer).

Current measurements (v0.8 head, re-run 2026-05-30):

| Scene | DCP | Mean ΔE | P50 | < 1 ΔE pixels |
|---|---|---:|---:|---:|
| Gym (DSC_4053, D750) | Camera Standard | **0.789** | 0.198 | 76.8% |
| Rose (d750_sample) | Adobe Standard | **0.844** | 0.803 | 69.6% |

The gym mean is dragged by demosaic-edge pixels; **flat non-edge pixels match
`dng_validate` exactly (median ΔE 0.000 over 94% of px)** — the colour maths
bit-match the open-spec reference. See `docs/VALIDATION.md` for the decomposition.

## Floors

Reference-comparison floors (characterized, not ship-gating). Distinguish the
part we *own and can tune* from the reference's own irreducible look:

- **vs `dng_validate` (the north-star): 0.789 gym / 0.844 rose mean — but
  median 0.000.** No theoretical floor on the colour maths; the real-scene mean
  floor is the **demosaic-algorithm choice** (libraw LINEAR vs Adobe; the DNG
  spec mandates no demosaic) at edges (~1.6 ΔE, ~6% of px). A synthetic
  flat-patch chart can drive the measured colour-math gap toward ~0.
- **vs LRT preview: ~2 ΔE** post-affine (was mislabelled 2.03 from the
  darktable era; re-measured 2026-05-30: raw 2.92 / affine-residual ~2.18).
  Decomposes as **0.79 (our-vs-Adobe-DNG, closeable) + ~2 (LR closed-source PV5
  look + 8-bit JPEG — the reference's, not ours)**.
- vs in-camera JPEG: ~6 ΔE (camera uses Nikon Picture Control, not Adobe DCP).

For the Adobe purge, `dng_validate` stays a test-only oracle and the proven
**0.789** is the target to tune open-DCP renders back toward.

## CLI surface (9 flags)

```
lrt-cinema render
  --input PATH               (required)  source RAW + LRT XMP folder
  --output PATH              (required)  destination folder
  --preset NAME              default cinema-linear-finished
                             (cinema-linear-finished | cinema-linear |
                              cinema-aces | stills-finished)
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
   on Windows) — populated by `tools/extract_dcp_library.py` against an
   Adobe DNG Converter install.
3. Adobe DNG Converter install (`/Library/Application Support/Adobe/CameraRaw/
   CameraProfiles/` on macOS; `%PROGRAMDATA%/Adobe/CameraRaw/CameraProfiles/`
   on Windows).
4. None → clear actionable error message.

Canon CR3 (ISO BMFF) and other non-TIFF RAWs fall through to step 4
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
  [docs/research/v09-standalone-repo-plan.md](docs/research/v09-standalone-repo-plan.md).
- [docs/research/v06-architecture.md](docs/research/v06-architecture.md)
  — the v0.6 architecture spec this build implements.
- [docs/research/dng-pipeline-findings.md](docs/research/dng-pipeline-findings.md)
  — the empirical journey from 6.37 ΔE (dt-cli) to 0.79 ΔE
  (in-process Python).
- [CHANGELOG.md](CHANGELOG.md) — release notes.
