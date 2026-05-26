# v0.6 Simplification Plan: A′-only, single path, minimum surface

**Status:** Plan (advisory) — 2026-05-26
**Supersedes:** [v06-impact-audit.md](v06-impact-audit.md) (first-pass
audit that preserved too much complexity)
**Implements:** the v0.6 milestone in radically simplified form, per
maintainer directive 2026-05-26

## Mandate

> "There is no 'new flags' or 'default flags'. Assess all current program
> paths, and get rid of any not strongly justified. We should just have
> one path, or at least organize around Alpha as the default. Any
> remaining flags or paths must be meaningfully justified. Keeping/
> maintaining a giant library of per-camera profiles must be meaningfully
> justified, and have no other simpler alternatives. Do not keep
> overcomplicated machinery that is no longer justified. Simplify as
> much as possible around our core goal. We want a radical simplification,
> codebase reduction, document reduction."

Metric: minimize ΔE2000 between LRT-preview JPEGs and our OpenEXR
emission. The shared Adobe-Standard-distilled transform (A′) captures
the load-bearing portion of that correction in a single
camera-agnostic asset; the project re-organizes around it.

## Two framing corrections against the prior audit

**1. The cube is invariant. Ship it pre-baked, not as `.npz`.**
A′ has `baseline_exposure_offset = 0` and no per-illuminant variant
([decision.md:60-66](color-correction/decision.md:60)).
[dcp.py:674](../../src/lrt_cinema/dcp.py:674) immediately returns
`data_1` when `data_2 is None`, and `bake_dcp_cubes_to_resolve_cube`
has no other per-render free parameters. The runtime cube is **identical
across every frame, every sequence, every camera**. Pre-bake at
distillation time; ship `presets/adobe_standard.cube` (~700 KB at
cube_size=33). This kills the entire runtime baker.

**2. The metric is LRT-preview ΔE, not ColorChecker ΔE.** The maintainer
named it; the decision's pivot to ColorChecker-relative as primary is
overridden. `tools/diagnose_vs_lrt_preview.py` is the v0.6 primary
acceptance diagnostic. `tests/test_colorimetric.py` (ColorChecker
harness) survives as a secondary deterministic CI smoke test, not as
acceptance authority.

## The single happy path

```
$ lrt-cinema render --input <dir> --output <dir> --preset cinema-linear
```

1. `parse_sequence(input_dir)` — LRT XMP → `LRTSequence`.
2. `materialize_all_frames(seq)` — keyframes → per-frame `DevelopOps`.
3. `apply_lrt_mask_offsets()` + `apply_deflicker()` — overlay per-frame
   exposure deltas from LRT's HG / Deflicker / Global masks.
4. Per frame: `emit_darktable_xmp(ops, xmp_path)` writes dt history:
   - `exposure` (Exposure2012 + Blacks2012 + per-frame deltas)
   - `tonecurve` (when LR-authored non-identity ToneCurvePV2012)
   - `colorbalancergb` (when sat/vib/contrast non-zero)
   - `sharpen` (when non-default)
   - `lut3d` pointing at the bundled `adobe_standard.cube`
5. `darktable-cli <raw> <xmp> <out>.exr --apply-custom-presets 0
   --icc-type LIN_REC2020 --core --conf
   plugins/imageio/format/exr/bpp=32 --conf
   plugins/darkroom/lut3d/def_path=<presets-dir>`. dt's libraw
   supplies as-shot WB + camera→working-space matrix; A′ applies after
   CAT16 as the lut3d cube.

That is the entire runtime path. No engine flag. No DCP discovery. No
per-camera anything. No runtime cube baking.

## CLI surface

Drops from 12 flags to 7. Each survivor justified.

**Required:**
- `--input`, `--output`, `--preset {cinema-linear, cinema-aces, stills-finished}`

**Optional (justified):**
- `--from-frame` / `--to-frame` — re-render slices after a partial failure
  without redoing the whole sequence. Production batches are long.
- `--dry-run` — exercise XMP emission without dt installed; CI uses it.
- `--quiet` — long renders flood the terminal; piping to logs needs it.
- `--apply-lrt-offsets` / `--no-lrt-offsets` (single boolean; replaces
  `--lrt-mask-offsets` + `--deflicker`) — all-or-nothing on per-frame
  LRT exposure deltas (HG + Deflicker + Global). The per-kind
  disambiguation today is a debugging niche.

**Dropped:**
- `--engine {dcp, algorithmic}` — one engine.
- `--dcp` — no DCP loading anywhere.
- `--no-auto-dcp` — no auto-detect to suppress.
- `--no-dcp-tone-curve` — A′ has no ProfileToneCurve.
- `--no-dcp-hsv-cubes` — equivalent to "render without color science."
- `--style` — A′ IS the look. Stacking a custom .style over it is
  undefined territory not measured anywhere; users wanting darktable
  styles drive `darktable-cli` directly.
- `--lrt-mask-offsets`, `--deflicker` — folded into `--apply-lrt-offsets`.

## What dies entirely

### Source files deleted

- **`src/lrt_cinema/dcp.py`** (1202 LOC). Every consumer goes:
  no .dcp / .npz parser, no `kelvin_tint_to_dt_multipliers` (no WB
  emission), no `interpolate_color_matrix` / `interpolate_hsv_cube`
  (pre-baked cube has no per-frame interpolation), no `read_raw_make_model`
  / `find_dcp_for_camera` / `auto_detect_profile` (no per-camera
  lookup), no Adobe DCP install path discovery, no `HsvCube` /
  `DCPProfile` dataclasses.
- **`src/lrt_cinema/lut3d_baker.py`** (371 LOC). The baker MOVES to
  `tools/distill_adobe_standard.py` (maintainer-side, runs once per
  Adobe DNG Converter catalog refresh).

### PR #14/#15 files rejected (never landing on main)

- **`src/lrt_cinema/calibration.py`** (628 LOC, PR #14/#15) — per-camera
  3×3 channelmixer storage layer for an opt-in engine that no longer
  exists.
- **`src/lrt_cinema/synthetic_dng.py`** (434 LOC, PR #15) — built
  synthetic DNGs for the Tier 2 dt-cli round-trip fit.
- **`tools/calibrate_camera.py`** (PR #15) — Tier 2 fitter.
- **`tests/test_calibration.py`**, **`tests/test_synthetic_dng.py`**,
  **`tests/test_dt_integration.py`** (PR #14/#15 tests).

### Existing files deleted

- **`tools/extract_dcp.py`** (69 LOC) — extracted per-camera DCPs to
  `.npz`. Replaced by maintainer-side `tools/distill_adobe_standard.py`
  (different tool: samples the catalog, produces the shared cube).
- **`tools/extract_dcp_library.py`** (120 LOC) — batch wrapper.
- **`tools/measure_dcp_variance.py`** (267 LOC) — M1 variance study;
  one-shot research, served its purpose, lives in the research docs.
- **`tests/test_dcp.py`** (817 LOC) — DCP parser + matrix math; both gone.
- **`tests/test_lut3d_baker.py`** (263 LOC) — runtime baker is gone;
  if any smoke coverage remains, it lives next to the maintainer-side
  distillation tool.
- **`src/lrt_cinema/presets/CALIBRATION.md`** — described per-style
  manual calibration; superseded by "we ship one cube."
- **`src/lrt_cinema/presets/README.md`** — folded into the top-level
  README and `definitions.py` docstrings.

### Docs moved to `docs/historical/`

- **`docs/V03_PLAN.md`** — v0.3 shipped; framing is two milestones stale.
  Banner: "Superseded by v0.6 — see decision.md."
- **`docs/V04_PLAN.md`** — Stage 3 HSM was per-camera-enrichment; A′
  ships HSM shared. Same banner.

## What survives in `src/lrt_cinema/`

| Module | Before | After | Notes |
|---|---:|---:|---|
| `cli.py` | 506 | ~200 | strip engine selection + 4 DCP flags + 2 collapsed flags; `_emit_dropped_field_warnings` simplifies to unconditional `highlights/shadows/whites` + a one-line WB-dropped notice |
| `runner.py` | 324 | ~200 | strip `dcp_profile` / `apply_dcp_*` / `cube_will_emit` parameters; the `def_path` config goes unconditional pointing at the bundled cube dir |
| `xmp_emitter.py` | 1095 | ~400 | drop `_encode_temperature_params`, `_encode_basecurve_params`, the cube-content-hash machinery, the entire `cube_will_emit` branch (lines 886-999); lut3d emission becomes unconditional |
| `xmp_parser.py` | 482 | 482 | unchanged |
| `interpolation.py` | 136 | 136 | unchanged |
| `ir.py` | 191 | 191 | unchanged |
| `dcp.py` | 1202 | **0** | deleted |
| `lut3d_baker.py` | 371 | **0** in src | moved to tools/ |
| `presets/definitions.py` | ~80 | ~80 | `cinema-aces` description gets a sentence on the Resolve IDT path |
| `presets/*.style`, `ocio_config.ocio` | ~120 | ~120 | unchanged |
| `presets/adobe_standard.cube` | — | **NEW (~700 KB)** | shipped artifact |
| **src/ total** | **~4402** | **~1704** | **~39% survives** |

## What survives in `tools/`

- **`tools/distill_adobe_standard.py`** (NEW, ~400 LOC) — maintainer-side
  only. Absorbs `lut3d_baker.py`. Produces `presets/adobe_standard.cube`
  from a one-time pass over an Adobe DNG Converter install. Re-run on
  each Adobe catalog refresh.
- **`tools/measure_a_prime_ceiling.py`** (kept) — cited in
  [decision.md:170-176](color-correction/decision.md:170) as the
  validation reproducer; needed to re-measure after the simplification
  (see Open Implementation Items).
- **`tools/diagnose_vs_lrt_preview.py`** (kept, **promoted**) — the v0.6
  primary acceptance diagnostic. Docstring positioning updated.

## What survives in `tests/`

- `tests/test_xmp_parser.py` (266 LOC) — unaffected.
- `tests/test_xmp_emitter.py` (601 LOC) — needs ~30% rewrite to drop
  DCP-branch assertions; structural emit-shape tests survive.
- `tests/test_interpolation.py` (143 LOC) — unaffected.
- `tests/test_ir.py` (56 LOC) — unaffected.
- `tests/test_runner.py` (68 LOC) — drop DCP parameter assertions; keep
  dt-cli argv assertions.
- `tests/test_cli.py` (326 LOC) — strip engine-flag tests; add coverage
  for `--apply-lrt-offsets`.
- `tests/test_colorimetric.py` (401 LOC) — kept as secondary objective
  metric.
- **NEW** `tests/test_adobe_shared.py` — bundled `adobe_standard.cube`
  is non-degenerate (middle-grey neutral round-trips within ε); lut3d
  emission references the right cube path.

Tests: ~3300 LOC current → ~1500 LOC after. ~45% survives.

## Policy decisions (load-bearing)

### WB emission: drop or per-make table — decide BEFORE ship

dt's `temperature` module wants RGGB multipliers. Computing those from
a user-authored kelvin requires either per-camera Adobe ColorMatrix
math (the path we're killing), per-camera MakerNote walking (vendor-
specific and fragile), or shipping our own bundled per-camera matrix
library (the maintainer's "no giant library of per-camera profiles"
explicitly rejects this).

**Working hypothesis: drop kelvin emission.** dt's libraw-derived
as-shot WB stands; users do WB in Resolve downstream.

**The Holy Grail problem (blocks ship until measured).** Holy Grail
sequences — the project's bread-and-butter — typically span authored
kelvin shifts of 1500–3000 K across a sunrise/sunset (e.g.
3500K dawn → 5500K midday). LRT renders the preview with those kelvin
changes applied via LR/ACR. If lrt-cinema drops kelvin emission, the
deliverable carries a constant as-shot cast where the LRT preview
shows correctly-balanced output — easily 3–5 ΔE on frames far from
the as-shot kelvin. That residual would dominate the v0.6 acceptance
gate against the LRT-preview-relative metric the maintainer named.

This is **Open Implementation Item 0** (promoted from "later"; see
below). Either:
- (a) Drop confirmed: kelvin-drop costs ≤ ~1 ΔE incremental on the
  reference Holy Grail sequence (frames where authored kelvin diverges
  from as-shot). v0.6 ships as planned.
- (b) Drop rejected: smallest reintroduction is a per-make ColorMatrix
  dict baked into code (~50 entries, ~3 KB total — NOT a per-camera
  profile library, NOT a per-camera install). Kelvin→multipliers math
  survives but reads a small table instead of parsing per-camera DCPs.
  Still a major simplification vs the current code; just not
  "kelvin-drop simple."

The decision blocks the v0.6 implementation PR opening. Cheap to
measure: the user's existing 5033-frame DSC sequence has authored
kelvin (per V04_PLAN.md context). `diagnose_vs_lrt_preview.py` on a
handful of frames at extreme kelvin gives the answer.

### ColorMatrix source for A′: dt's libraw bundled matrices

A′ is the post-CAT16 non-linear residual; the pre-CAT16 ColorMatrix is
dt's responsibility. dt's libraw-bundled per-camera matrices are good
enough for the project's target class (decision.md's per-camera
measurements are vs Adobe ColorMatrix; the libraw-vs-Adobe gap is a
known small term, typically 0.5–1.5 ΔE). Adding per-camera ColorMatrix
.npz reintroduces the library the maintainer rejected.

Re-measurement of A′'s ceiling under dt-libraw ColorMatrix (instead of
Adobe per-camera) is an open implementation item; cited numbers in
decision.md were measured against Adobe per-camera matrices.

## PR chain disposition (overrides the prior audit)

| PR | Branch | Verdict | Justification |
|---|---|---|---|
| #11 | `fix/v0.4-defensive` | **Reject** | dcp.py defensive fixes; module about to be deleted. Salvage any standalone tests if they exercise non-DCP behavior. |
| #12 | `fix/xy-camera-neutral-iteration` | **Reject** | `xy_to_camera_neutral` simplification in dcp.py — entire function gone with the module. |
| #13 | `refactor/cli-resolve-profile` | **Reject; rewrite cli.py from scratch under §"What survives"** | Touches cli.py (87 lines) + dcp.py (127 lines, dies). Hunk-by-hunk cherry-picking through an in-flight refactor against a target surface that itself diverges is more work than rewriting cli.py against this plan. Reject the PR; rewrite cli.py against the simplified surface. |
| #14 | `feat/v0.4-calibration-deterministic` | **Reject** | `calibration.py` storage layer for an engine that no longer exists. |
| #15 | `feat/v0.4-calibration-dt-roundtrip` | **Reject** | Tier 2 fit + synthetic DNG + per-camera channelmixerrgb emission. Algorithmic engine dies; whole stack goes. |
| #16 | `docs/color-option-space-research` | **Merge** | Research consolidation; this plan depends on it. |

The prior audit's "merge all of #11–#16" is overridden. The simplification
mandate kills the substrate #14 and #15 build, and most of the dcp.py
fixes in #11/#12/#13 land in a module that's about to be deleted.

## Hidden mistakes carried forward from the prior audit

These three findings remain load-bearing under the simplification:

**1. `dcp.load_profile` asymmetry.** Becomes a non-issue under the
simplification (`dcp.py` is deleted; the shared cube is shipped
pre-baked, no runtime `.npz` load). But the underlying lesson — that
save/load round-trips need symmetric optional-field handling — applies
to any future shipped data asset.

**2. `--apply-custom-presets 0` may suppress dt's basecurve ACR3
default.** Still load-bearing. A′'s ΔE ceiling assumes ACR3 basecurve
is active. The lrt-cinema runner disables custom-preset auto-apply for
deterministic output. Open implementation item below.

**3. "ColorMatrix from camera EXIF" claim is partial.** Resolved by
the simplification: we use dt's libraw matrices for every camera; no
per-RAW ColorMatrix read needed at runtime.

## Open implementation items

0. **MEASURE WB-drop residual on Holy Grail before opening the v0.6
   implementation PR.** Render the user's 5033-frame DSC reference
   sequence with as-shot WB only, vs LRT preview, on frames where the
   authored kelvin diverges from as-shot. If incremental ΔE ≤ ~1,
   kelvin-drop ships. If higher, per-make ColorMatrix dict lands in
   v0.6 itself (not as a "reversible later" item — the user's primary
   workflow is Holy Grail, the metric the maintainer named is
   LRT-preview ΔE). **Blocks ship.**

1. **Verify `--apply-custom-presets 0` doesn't suppress basecurve ACR3.**
   Cheap: render the same DCP-free RAW with vs without
   `--apply-custom-presets 0`, diff the basecurve module's enabled flag
   in dt's history. If suppressed, A′ either re-measures under the new
   condition or emits an explicit basecurve as part of the shared
   transform (which can be pre-baked into the cube).

2. **Re-measure A′'s ΔE ceiling under dt-libraw ColorMatrix.** Current
   decision.md numbers (1.5 mean on modern targets; 3.60 mean / 11.46
   P95 full panel) were vs Adobe per-camera ColorMatrix renders. The
   v0.6 acceptance gate text waits for the libraw-relative measurement.

3. **Confirm A′'s ΔE ceiling holds for the LRT-preview-relative
   metric.** The two metrics (LRT-preview vs ColorChecker) measure
   different things. The maintainer's stated metric is LRT-preview
   ΔE. Re-measure on DSC_4053 (Nikon D750) before fixing the v0.6
   acceptance number.

4. **Cube-size forever commitment.** Shipping a pre-baked
   `adobe_standard.cube` locks `cube_size=33` for that release. Decision.md
   M2 measured 33³ and 65³ performing equivalently (4.11 vs 4.15 mean
   ΔE — [decision.md:139-142](color-correction/decision.md:139)), which
   justifies 33. But "we can't recover 65³ headroom without shipping a
   new cube" is the trade. Documented; acceptable.

5. **`test_colorimetric.py` survival.** The ColorChecker harness is a
   methodology the maintainer didn't ask for. The self-test leg
   (synthetic-chart round-trip, no chart shot needed) is cheap and
   guards against numerical regressions in the colour-science
   integration the offline `distill_adobe_standard.py` depends on.
   Plan default: **keep self-test leg, drop real-chart leg** (which
   depended on the user shooting a ColorChecker; not aligned with the
   LRT-preview metric).

6. **Doc-archive decision.** `docs/V03_PLAN.md` and `docs/V04_PLAN.md`:
   move to `docs/historical/` with banners, or delete outright? Plan
   defaults to "move with banner."

## Recommended action sequence

0. **Resolve Open Implementation Item 0 (WB-drop on Holy Grail).**
   Two-day measurement at most. The result decides whether v0.6 ships
   kelvin-drop or with the per-make ColorMatrix dict. Everything
   downstream waits.
1. **Merge PR #16** (research docs consolidation). Unblocks references.
2. **Open v0.6 cleanup PR — deletion-only changes.** Remove the
   dying surface in one commit: `dcp.py`, `lut3d_baker.py` (moved to
   tools), `extract_dcp*.py`, `measure_dcp_variance.py`, `test_dcp.py`,
   `test_lut3d_baker.py`, `presets/CALIBRATION.md`, `presets/README.md`,
   `test_colorimetric.py` real-chart leg.
3. **Open v0.6 simplification PR — surface refactor.** Strip
   `cli.py` / `runner.py` / `xmp_emitter.py` to the A′-only shapes
   above. Drop the 8 retired flags. Add the new `--apply-lrt-offsets`
   collapsed flag.
4. **Open v0.6 artifact PR.** Add `tools/distill_adobe_standard.py`
   (maintainer-side). Distill `adobe_standard.cube` from the Adobe
   catalog. Ship the cube in `src/lrt_cinema/presets/`. Add
   `tests/test_adobe_shared.py`.
5. **Resolve Open Implementation Item 1** (basecurve ACR3) before
   closing the cube. The pre-baked cube may need to fold in an explicit
   ACR3 basecurve component.
6. **Resolve Open Implementation Item 2** (re-measure under libraw
   ColorMatrix). Sets the v0.6 acceptance number.
7. **Doc rewrite PR.** README, SCOPE, CHANGELOG, V06_PLAN, RESOLVE_WORKFLOW.
   Move V03/V04 to `docs/historical/`.
8. **Reject PRs #11, #12, #14, #15.** Close with a one-line reference
   to this plan. Cherry-pick PR #13's cli.py portion if standalone-useful.

## What this plan does NOT do

- Does not commit a specific ΔE number for the v0.6 acceptance gate
  (depends on Open Implementation Item 2).
- Does not solve the WB-residual problem (Open Implementation Item 4
  decides whether kelvin-dropped is acceptable).
- Does not write the v0.6 implementation. The maintainer reviews this
  plan, then opens the deletion + refactor + artifact PRs.

## What this plan replaces

The prior audit ([v06-impact-audit.md](v06-impact-audit.md)) framed v0.6
as a renaming/extension exercise — new engine flag, new loader split,
schema clarifications, preserved per-camera substrate. That framing
matches the decision.md text but not the maintainer's intent. The
maintainer's intent is: **one path, ship the cube, delete the rest**.
This plan operationalizes that.

The decision.md text itself is consistent with the simplification —
the per-camera enrichment (`--engine adobe-camera`) was a courtesy
fallback for users on legacy cameras. The simplification answers
"do we need that courtesy at v0.6 ship time?" with **no**. The
substrate to add it back later (per-make ColorMatrix dict, or
re-extracted per-camera `.npz`) is small and reversible.
