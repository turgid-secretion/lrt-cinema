# Implementation Scope (v0.7)

Honest per-feature status. v0.7.0 ships the γ preset
(`cinema-linear-finished`, half-float DWAB EXR). β-XML
(`cinema-linear-master`, Stage-7 EXR + Resolve project sidecar) lands
in v0.7.1; the §2.B free-upgrade increments (X1–X6) extend β-XML
coverage in subsequent v0.7.x releases.

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
| NEF→DNG preprocessing | shipped | `dng_convert.py` wraps **dnglab** (open-source, LGPL-2.1; Adobe-free); mtime+size cache |
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
  [docs/DECISIONS.md](docs/DECISIONS.md) §6.
- [docs/PIPELINE.md](docs/PIPELINE.md) — the canonical as-built pipeline reference.
- [CHANGELOG.md](CHANGELOG.md) — the empirical journey from 6.37 ΔE (dt-cli) to
  the in-process Python pipeline.
- [CHANGELOG.md](CHANGELOG.md) — release notes.
