# Implementation Scope (v0.6)

Honest per-feature status of the v0.6 pre-alpha.

## What works

| Capability | Status | Notes |
|---|---|---|
| LRT XMP parser (full Camera Raw Settings field set) | shipped | `xmp_parser.py` |
| Per-frame keyframe interpolation (linear) | shipped | `interpolation.py` |
| Holy Grail kelvin override | shipped | `DevelopOps.temperature_k` honored per frame |
| LRT mask-correction per-frame deltas (HG / Deflicker / Global) | shipped | `--apply-lrt-offsets` default |
| Adobe DNG 1.7.1 render pipeline (stages 1–10) | shipped | `pipeline.py`, ΔE < 1 vs `dng_validate` |
| Per-camera DCP profile loading + ColorMatrix interpolation | shipped | `dcp.py`, auto-detect from Adobe DNG Converter install or `$LRT_CINEMA_PROFILES` |
| LR Exposure2012, Blacks2012, ToneCurvePV2012, Saturation, Vibrance, Contrast2012 | shipped | `develop_ops.py` (greenfield from public LR formulas) |
| NEF→DNG preprocessing | shipped | `dng_convert.py` wraps Adobe DNG Converter; mtime+size cache |
| `cinema-linear` output (16-bit linear Rec.2020 TIFF) | shipped | `output.py` via `tifffile` |
| `cinema-aces` output (32-bit float linear Rec.2020 PIZ EXR) | shipped | `output.py` via `OpenEXR` ASWF binding |
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

| Preset | Container | Color space | Library |
|---|---|---|---|
| `cinema-linear` | 16-bit int TIFF | linear Rec.2020 | `tifffile` |
| `cinema-aces` | 32-bit float EXR (PIZ) | linear Rec.2020 | `OpenEXR` (capital-O ASWF PyPI binding) |
| `stills-finished` | 16-bit int TIFF | Rec.2020 + AgX | NotImplemented (v0.6.x) |

## Validation

End-to-end ship gate: `tests/test_pipeline.py` renders the project's
test scenes through `pipeline.render_frame` and asserts mean ΔE2000
< 1.0 against Adobe `dng_validate` (their own DNG SDK reference
renderer).

Current measurements (v0.6 tip):

| Scene | DCP | Mean ΔE | P50 | < 1 ΔE pixels |
|---|---|---:|---:|---:|
| Gym (DSC_4053.NEF, D750) | Camera Standard | **0.79** | 0.20 | 76.8% |
| Rose (d750_sample.NEF) | Adobe Standard | **0.84** | — | 69.6% |

## Floors

Quantified maxima beyond which open-source Adobe-DCP-spec compliance
cannot reach (per `docs/research/dng-pipeline-findings.md`):

- vs LRT preview JPEG: **2.03 ΔE** (LR PV5 adds processing beyond the
  public DCP spec).
- vs in-camera JPEG: **6.32 ΔE** (camera uses Nikon Picture Control,
  not Adobe DCP).

Both are characterized, not ship-gating.

## CLI surface (9 flags)

```
lrt-cinema render
  --input PATH               (required)  source RAW + LRT XMP folder
  --output PATH              (required)  destination folder
  --preset NAME              (required)  cinema-linear | cinema-aces | stills-finished
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

- [docs/research/v06-architecture.md](docs/research/v06-architecture.md)
  — the v0.6 architecture spec this build implements.
- [docs/research/dng-pipeline-findings.md](docs/research/dng-pipeline-findings.md)
  — the empirical journey from 6.37 ΔE (dt-cli) to 0.79 ΔE
  (in-process Python).
- [CHANGELOG.md](CHANGELOG.md) — release notes.
