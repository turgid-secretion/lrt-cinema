# v0.6 Impact Audit: Repo state assessment after refined Shape α decision

> **SUPERSEDED 2026-05-26 by
> [v06-simplification-plan.md](v06-simplification-plan.md).** The first
> pass below preserved too much per-camera complexity and read the v0.6
> decision as a renaming exercise rather than a simplification mandate.
> Three load-bearing findings from this pass remain valid and are carried
> forward into the simplification plan: (1) `dcp.load_profile` rejects
> A′-shaped `.npz`; (2) `--apply-custom-presets 0` may suppress dt's
> basecurve ACR3 baseline that A′'s ceiling implicitly assumes; (3) the
> decision's "ColorMatrix from EXIF" claim is only true for DNG.
> Everything else in this doc is overtaken by the simplification plan.

**Status:** Audit (advisory) — 2026-05-26
**Audits against:** [docs/research/color-correction/decision.md](color-correction/decision.md)
**Scope:** every committed file in `src/`, `tools/`, `tests/`, `docs/`,
plus root-level `README.md`, `SCOPE.md`, `CHANGELOG.md`, and the open
PR chain (#11–#16). Surfaces what was built assuming a different
direction than the v0.6 commitment.

## Summary

The v0.6 decision ships A′ — a single shared HueSatMap + LookTable
distilled from the Adobe Standard catalog at
`src/lrt_cinema/presets/adobe_standard.npz` — applied as the
non-linear residual stage on top of a per-camera ColorMatrix (sourced
from RAW EXIF or `tools/extract_dcp.py`'s per-camera `.npz`). The
project's defensible color claim moves from "matches LR / LRT
preview" to "ColorChecker ΔE2000 against published patches" (mean
~1.5 across modern target cameras; 3.60 mean / 11.46 P95 across the
full 40-camera evaluation panel).

The substrate the prior milestones built is largely re-usable. The
DCP parser, `.npz` extracted-profile format (PR #14), HSV-cube baker,
lut3d emission path, runner orchestration, and the dt-side wiring all
survive intact: A′ is a *different* cube travelling through the *same*
machinery. The PR #15 per-camera channelmixer fitter (Tier 2) is no
longer load-bearing for the default path but remains the right
companion for the opt-in algorithmic engine.

The friction is concentrated in three places: (1) the engine-flag
surface (`--engine {dcp, algorithmic}` doesn't accommodate `--engine
adobe-shared`); (2) the user-facing positioning docs (`SCOPE.md`,
`README.md`, `docs/V03_PLAN.md`, `docs/V04_PLAN.md`,
`docs/VALIDATION.md`) which carry "matches LR / LRT preview" framing
that the v0.6 reframing explicitly retires; (3) the `cinema-aces`
preset description (which the decision deliberately keeps named-as-is
but the description still misleads). No data needs migrating; no
existing artifact contradicts the new artifact.

## Per-file changes required

### Critical (blocks v0.6 implementation)

**[src/lrt_cinema/cli.py:116-130](../../src/lrt_cinema/cli.py:116)** — `--engine`
flag accepts only `{dcp, algorithmic}`. The decision specifies a
third path: A′ (the default), shipped as
`presets/adobe_standard.npz`. Implementation must add `adobe-shared`
(or rename per the decision's Open Question 1), and decide whether
the existing `dcp` value becomes `adobe-camera` or auto-selects on
presence of per-camera DCPs. Either choice ripples to test
assertions ([tests/test_cli.py:215-330](../../tests/test_cli.py:215)).
Without this change, the v0.6 default path is unreachable from the CLI.

**[src/lrt_cinema/cli.py:358-431](../../src/lrt_cinema/cli.py:358)** — engine
selection in `_cmd_render`. Current branch structure:

```
if args.engine == "algorithmic": dcp_profile = None
elif args.dcp is not None: load explicit
elif args.auto_dcp: probe Adobe install paths
```

A′ requires loading `adobe_standard.npz` from the package
data-resources path and merging its HSM+LookTable with a per-camera
ColorMatrix (EXIF for DNG; extracted `.npz` for NEF/CR3/ARW — see
Open Question 2 below). The merge can produce a single `DCPProfile`
instance, but the *selection* logic is new. No graceful path through
the existing branch tree.

**[src/lrt_cinema/cli.py:168-198](../../src/lrt_cinema/cli.py:168)** —
`_emit_dropped_field_warnings` gates the tint/temperature_k drop on
the two-state `dcp_loaded` boolean. A′ adds a third state: "shared
transform loaded, but no per-camera ColorMatrix → kelvin→multipliers
math can't fire → tint/temperature_k still drop." The boolean
becomes three-state (no profile / camera-matrix only / full profile);
the warning text needs to reflect which arm is active.

**[src/lrt_cinema/dcp.py:1063-1064](../../src/lrt_cinema/dcp.py:1063)** —
`load_profile` hard-requires `color_matrix_1` in the `.npz` archive:

```python
if "color_matrix_1" not in data:
    raise ValueError(f"{path}: missing color_matrix_1 — not a valid extracted profile")
```

`save_profile` writes `color_matrix_1` conditionally
([dcp.py:1011-1014](../../src/lrt_cinema/dcp.py:1011)), so the round-
trip is asymmetric. An `adobe_standard.npz` containing only HSM +
LookTable + baseline-exposure (no ColorMatrix — the whole point of
A′ being camera-agnostic) cannot pass this loader.

Three resolution options:
- (a) Relax the check (split `load_profile` into
  `load_camera_profile` / `load_shared_transform`).
- (b) Ship `adobe_standard.npz` with an identity ColorMatrix1 as a
  placeholder (semantically wrong; loader-shape silently misleads).
- (c) Add a new loader entry-point for shared transforms; keep the
  existing one for per-camera profiles.

Recommendation: (c) — separate concerns. The shape-merging logic at
the cli.py engine-selection layer can compose the two.

This is a code change required for v0.6 implementation; the audit
originally listed dcp.py under "No change needed" but the asymmetry
was missed. Corrected in this revision.

**[docs/V04_PLAN.md:64-77](../V04_PLAN.md:64)** — "Stage 3 — HSM" is
the v0.4 research-gated section. v0.6 supersedes it: HSM ships as
part of A′ (shared, not per-camera), under a different acceptance
gate. The whole "Stage 3 pending" framing is obsolete; the plan
needs a status note pointing forward to the v0.6 milestone.

**[docs/V04_PLAN.md:80-95](../V04_PLAN.md:80)** — v0.4 acceptance gate
phrasing: "Target: mean pre-fit ΔE2000 < 4.0; mean post-fit < 2.0 …
on at least 3 frames vs LRT `/visual/` previews." Decision
explicitly reframes the workflow-relevant metric: ColorChecker
ΔE2000 against published patches (mean < 2.5, P95 < 5 on the modern
target class) replaces LRT-preview-relative as the colorimetric-
correctness claim. The diagnostic tool stays useful but its acceptance
authority changes. The gate text needs to clarify which question
each metric answers (UX validation vs colorimetric correctness).

**[SCOPE.md:42-44](../../SCOPE.md:42)** — "Validation gap (the
'cinema-grade' claim)" section. The "the test is mechanical to
implement" framing is now overtaken: v0.6 specifies the per-make
regression panel and the A′ reproducer
([decision.md:165-176](color-correction/decision.md:165)) as the
validation methodology. Section needs rewriting to point at the
canonical recommendation and to drop the implied "ColorChecker is
the only gap-closer."

### High priority (misaligned but not blocking)

**[README.md:5](../../README.md:5)** — "cinema-grade color" wording
+ link to `docs/VALIDATION.md`. The v0.6 reframing positions
lrt-cinema as the first-pass renderer in a three-stage workflow
(LRT-authoring → lrt-cinema → Resolve), not a final-grade tool.
"Cinema-grade color" is defensible only with the ColorChecker ΔE
envelope that v0.6 makes the canonical claim. Recommend rewriting
to: "ColorChecker ΔE2000 < 2.5 mean / 5.0 P95 on modern target
cameras; intended as the first-pass renderer for cinema-finish
workflows (e.g. into DaVinci Resolve)."

**[README.md:25-27](../../README.md:25)** — Preset table description
for `cinema-aces`: "ACES timelines (bundled OCIO config)." Per
[decision.md:286-294](color-correction/decision.md:286), the preset
emits 32-bit float linear Rec.2020 EXR, NOT ACES2065-1 EXR — the
data is equivalent up to a 3×3 matrix Resolve applies. Recommend
adding a note: "Output is linear Rec.2020 EXR; Resolve's ACES IDT
'Linear Rec.2020 → ACES2065-1' is a clean matrix Resolve auto-applies
on ACES timelines. The preset name is historical." Decision says
conservative answer is "document rather than rename."

**[README.md:90-93](../../README.md:90)** — "Out of scope: Replicating
Adobe Camera Raw's parametric tone-curve look." With A′ shipping the
shared HSM + LookTable, the project DOES replicate the bulk of
Adobe's color-rendition character (per
[decision.md:113-122](color-correction/decision.md:113), ~1.5 ΔE2000
mean against the per-camera Adobe Standard rendering on modern
targets). "Out of scope: pixel-match to PV2012 parametric tone math"
is the accurate post-v0.6 phrasing.

**[SCOPE.md:96-97](../../SCOPE.md:96)** — Engine-path description
covers `{dcp, algorithmic}` only. Needs an `adobe-shared` entry.

**[SCOPE.md:38-40](../../SCOPE.md:38)** — Points at `docs/V03_PLAN.md`
as the v0.3 milestone description; v0.3 has shipped, and the
"project pivot: stop replicating what LRT does well" framing is now
two milestones stale. Recommend pointing forward to the v0.6 plan
once it exists.

**[docs/V03_PLAN.md:8-10](../V03_PLAN.md:8)** — "A v0.3 release does
NOT promise pixel-match to ACR / Lightroom output" — still accurate.
But the "defensible 'cinema-grade color' claim depends on the
ColorChecker ΔE2000 automated test" framing now sits inside a
broader v0.6 framework that explicitly distinguishes "matches LR
preview" (out of scope) from "ColorChecker-correct" (in scope) from
"colorist-graded deliverable" (out of scope; downstream). The plan
document is historical (v0.3 shipped); a one-line "superseded by
v0.6 — see [color-correction/decision.md](color-correction/decision.md)"
banner at the top would be honest.

**[docs/VALIDATION.md:118-126](../VALIDATION.md:118)** — "What this
test cannot tell you" section. Lists what ColorChecker ΔE doesn't
prove. With v0.6 the project's workflow positioning becomes
explicit: ColorChecker ΔE proves *colorimetric correctness against
the published patches*, NOT "matches LR." The "matches LR" question
is structurally retired (Resolve doesn't consume XMP develop intent;
LRT preview cache is regenerated on every interaction —
[decision.md:188-193](color-correction/decision.md:188)). The
methodology section is correct as-is, but the surrounding text
implying that LR-equivalence remains a "manual verification"
deliverable needs softening.

**[docs/VALIDATION.md:265-360](../VALIDATION.md:265)** —
"Methodology — comparing two renders of the same scene" + "Empirical
finding 2026-05-23" + "LRT interpolation passthrough model." Detailed
diagnostic methodology against LRT preview JPEGs. Still
methodologically sound (the diagnostic is a useful UX-validation
tool), but its framing as "the project's primary colorimetric-
divergence diagnostic" needs adjustment: post-v0.6 it's the secondary
metric, with ColorChecker ΔE primary.

**[src/lrt_cinema/presets/definitions.py:56-66](../../src/lrt_cinema/presets/definitions.py:56)** —
`cinema-aces` preset description: "For ACES / OCIO timelines." The
description doesn't mislead, but it doesn't address the naming
mismatch the decision explicitly calls out. Recommend a sentence
clarifying that the output is linear Rec.2020 EXR (consumed by ACES
timelines via Resolve's "Linear Rec.2020 → ACES2065-1" input
transform — a clean matrix), not ACES2065-1 directly.

**[src/lrt_cinema/presets/CALIBRATION.md](../../src/lrt_cinema/presets/CALIBRATION.md)** —
Describes the procedure for calibrating darktable `.style` files
manually. The "Why not just ship guessed params" section is correct.
Two updates needed: (a) the dt module-modversion encoder shipped in
v0.4 (no longer "best-guess") and the `.style` calibration is no
longer the only path; (b) v0.6 ships `adobe_standard.npz` as an
in-tree shipped artifact, which is a different kind of calibration
asset than per-style binary blobs. Recommend a v0.6 update.

**[src/lrt_cinema/presets/README.md](../../src/lrt_cinema/presets/README.md)** —
Treats `.style` files as the primary calibration unit. Post-v0.6
`adobe_standard.npz` is the headline asset under this directory.
Table should be extended to list it once shipped.

**[CHANGELOG.md:8-22](../../CHANGELOG.md:8)** — Unreleased section
ends at "Three output preset definitions: `cinema-linear`,
`cinema-aces`, `stills-finished`." Doesn't reflect anything from
v0.3 or v0.4 forward. Stale; pre-v0.6 cleanup is worth doing.

**[docs/V04_PLAN.md:118-127](../V04_PLAN.md:118)** — "What
lrt-cinema does NOT promise post-v0.4." Pixel-match to ACR remains a
non-goal (correct). LR-equivalent rendering for Highlights/Shadows/
Whites remains a documented gap (correct). But the "headless LR-driven
calibration loop" rejection is now reinforced by
[decision.md:188-193](color-correction/decision.md:188)'s explicit
foreclosure of metadata-passthrough.

### Low priority (works but worth updating)

**[src/lrt_cinema/dcp.py:1-7](../../src/lrt_cinema/dcp.py:1)** —
Module docstring: "Closes the 'lrt-cinema's render diverges from LR
/ LRT preview' gap by giving us access to the same color-pipeline
knobs LR uses internally." Per v0.6, the gap-closing claim is
ColorChecker-relative, not LR-preview-relative. Suggest softening
to: "Read access to Adobe DCP profile fields — color matrices,
baseline exposure, HSV cubes — used by the render pipeline to
approximate Adobe Standard color rendition (per A′ shared transform;
see decision.md)."

**[src/lrt_cinema/xmp_emitter.py:158-164](../../src/lrt_cinema/xmp_emitter.py:158)** —
`BASECURVE_MODVERSION` docstring cites empirical ΔE post-fit residual
against the LRT preview reference (2.25). Number is still correct,
but the framing ("LR preview") needs softening once v0.6 reframes
the metric.

**[src/lrt_cinema/xmp_emitter.py:171-182](../../src/lrt_cinema/xmp_emitter.py:171)** —
Same pattern: comments cite "DSC_4053 vs LRT preview." Accurate but
framing should reference the new metric hierarchy.

**[src/lrt_cinema/dcp.py:106-113](../../src/lrt_cinema/dcp.py:106)** —
Comment on `_TAG_PROFILE_LOOK_TABLE_DATA`: "this is the only HSV cube
present, and is the source of the ΔE post-fit 2.24 structural
residual the diagnostic flagged on DSC_4053." Still factually true
for the per-camera path; redundant context once the median LookTable
ships in `adobe_standard.npz`. Worth a one-line note pointing at the
A′ artifact.

**[src/lrt_cinema/dcp.py:243-256](../../src/lrt_cinema/dcp.py:243)** —
`DCPProfile` dataclass comment on `hue_sat_map` mentions the Nikon
D750 case specifically. Once A′ ships, the "no HSM, only LookTable"
characterization stops being load-bearing for the default path
(though it remains true for per-camera profiles).

**[src/lrt_cinema/dcp.py:858-861](../../src/lrt_cinema/dcp.py:858)** —
"prefers the Camera Standard variant over the Adobe Standard
fallback — that matches what LR rendered the LRT preview with." Post-
v0.6 the preference is mostly inverted at the policy level: A′ is
distilled FROM Adobe Standard, so the auto-detect default-preference
ordering becomes a per-engine concern. Not blocking; worth updating
when implementing the engine flags.

**[tools/diagnose_vs_lrt_preview.py](../../tools/diagnose_vs_lrt_preview.py)** —
The tool stays valuable; its module docstring positions it as the
"primary colorimetric-divergence diagnostic." Post-v0.6 it becomes
the secondary metric (UX validation, "does the render visibly differ
from what the user sees in LRT?"). One-line update to the docstring
positioning is sufficient.

**[tools/README.md:7-30](../../tools/README.md:7)** — Describes
diagnose_vs_lrt_preview.py as the project's primary diagnostic.
Same positioning update; recommend linking to
[color-correction/decision.md](color-correction/decision.md) for the
two-metric framing.

**[tests/test_cli.py:215-256](../../tests/test_cli.py:215)** —
`test_engine_algorithmic_suppresses_dcp_modules` and
`test_engine_dcp_default_unchanged`. Lock in the current two-engine
model. Will need updating (not breaking) when `adobe-shared` lands;
either rename the default-engine assertion or add a third test for
the new default. Not blocking.

### No change needed

**[src/lrt_cinema/dcp.py — IFD parsing + DCPProfile + HSV cube
handling](../../src/lrt_cinema/dcp.py)** — A′ uses the same DCP shape
(HSM + LookTable as HsvCube; ColorMatrix1/2; calibration illuminants;
baseline exposure). The `DCPProfile` dataclass holds optional fields
correctly; storage shape supports A′ at the dataclass level.

EXCEPTION: `load_profile` hard-requires `color_matrix_1` even though
`save_profile` writes it conditionally — see Critical section above.
A loader change is required to read an A′ shared-transform `.npz`.

**[src/lrt_cinema/lut3d_baker.py](../../src/lrt_cinema/lut3d_baker.py)** —
Verified: handles A′'s 90×30×1 HSM and 36×8×16 LookTable correctly.
Trace of `_apply_hsv_cube` at val_divisions=1: `v_scale = 0`,
`max_v_index0 = 0`, both v-corners collapse to index 0, weights
degenerate to bilinear (H, S) only — mathematically correct (HSM
with `val_divisions=1` carries no value-axis variation). LookTable
shape works at the existing path. No code change.

**[src/lrt_cinema/xmp_emitter.py — lut3d emission, content hashing,
cube file path resolution](../../src/lrt_cinema/xmp_emitter.py)** —
Path supports A′ verbatim. Content-hashed cube dedup keys off
profile contents — A′'s identical cube across all frames will
deduplicate to one file per sequence (which is the right outcome).

**[src/lrt_cinema/runner.py](../../src/lrt_cinema/runner.py)** — Shape-
agnostic to DCPProfile contents. The dt-cli invocation only needs
`def_path` pointed at the per-frame output directory, which is
unchanged. No engine awareness required at the runner layer.

**[src/lrt_cinema/calibration.py (PR #14/#15)](../../src/lrt_cinema/calibration.py)** —
Schema is per-camera (`camera_label`, 3×3 matrix) and explicitly fits
the algorithmic-engine output to the DCP-engine output. NOT
load-bearing for A′ (the A′ artifact is HSM+LookTable cubes, a
different shape). Calibration.py is the right substrate for the
opt-in `--engine adobe-camera` enrichment path and the long-term
algorithmic-engine calibration. No schema extension needed for A′.
Recommend a one-line docstring note at the top of the module
clarifying scope: "Tier 2 calibration is the companion to the
algorithmic engine, NOT the storage layer for A′ (which lives in
`presets/adobe_standard.npz` and re-uses the DCPProfile shape from
[dcp.py](dcp.py))."

**[src/lrt_cinema/synthetic_dng.py (PR #15)](../../src/lrt_cinema/synthetic_dng.py)** —
Builds a synthetic Bayer-mosaic DNG for the Tier 2 calibration round-
trip. Per-camera by purpose (encodes Make/Model). Stays scoped to
algorithmic-engine calibration; not load-bearing for A′. No change.

**[tools/extract_dcp.py](../../tools/extract_dcp.py),
[tools/extract_dcp_library.py](../../tools/extract_dcp_library.py)** —
Per-camera DCP extractors. Still needed for `--engine adobe-camera`
and (per Open Question 2 below) potentially also for the A′ default
path on non-DNG cameras. No change.

**[tools/calibrate_camera.py](../../tools/calibrate_camera.py)** —
Per-camera channelmixer fitter. Stays scoped to the algorithmic
engine. Docstring already names it Tier 2 baseline foundation;
matches the decision's positioning.

**[tools/measure_dcp_variance.py](../../tools/measure_dcp_variance.py),
[tools/measure_a_prime_ceiling.py](../../tools/measure_a_prime_ceiling.py)** —
M1 and M2 measurement scripts. Cited as the reproducers in
[color-correction/measurements.md](color-correction/measurements.md);
maintained by reference. No change.

**[docs/reference/lrtimelapse/](../reference/lrtimelapse/),
[docs/reference/darktable/](../reference/darktable/)** — Factual
reference docs about LRT and darktable behavior. Unchanged by the
v0.6 decision; remain valid as-is.

**[docs/research/KELVIN_MULTIPLIERS_RESEARCH.md](KELVIN_MULTIPLIERS_RESEARCH.md),
[docs/research/DNG_SDK_FEASIBILITY.md](DNG_SDK_FEASIBILITY.md)** —
Research-history docs feeding the decision. Cited by name in
[decision.md:300-307](color-correction/decision.md:300). No change.

**[docs/research/color-option-space-2026-05-26/](color-option-space-2026-05-26/)** —
Archived iteration trail; the README already directs readers to the
canonical `color-correction/` docs. No change.

**[docs/research/color-option-space-2026-05-26/_archive/](color-option-space-2026-05-26/_archive/)** —
Archived per the README; not load-bearing.

**[src/lrt_cinema/xmp_parser.py](../../src/lrt_cinema/xmp_parser.py),
[src/lrt_cinema/interpolation.py](../../src/lrt_cinema/interpolation.py),
[src/lrt_cinema/ir.py](../../src/lrt_cinema/ir.py)** — Parser, interp
engine, IR. Unaffected by the color-engine decision; develop-op
emission is a separate axis from camera-response correction
([decision.md:178-184](color-correction/decision.md:178)).

**[tests/test_xmp_parser.py](../../tests/test_xmp_parser.py),
[tests/test_xmp_emitter.py](../../tests/test_xmp_emitter.py),
[tests/test_interpolation.py](../../tests/test_interpolation.py),
[tests/test_ir.py](../../tests/test_ir.py)** — Develop-op-side tests.
Unaffected.

**[tests/test_dcp.py](../../tests/test_dcp.py),
[tests/test_lut3d_baker.py](../../tests/test_lut3d_baker.py)** — DCP
parser + cube baker tests. The existing fixtures use
`val_divisions=2` (well-formed multi-V cubes); none assert against
the A′-specific shapes. A v0.6 test adding a `val_divisions=1`
HSM fixture would close the verification gap, but the existing tests
remain correct.

**[tests/test_synthetic_dng.py (PR #15)](../../tests/test_synthetic_dng.py),
[tests/test_dt_integration.py (PR #15)](../../tests/test_dt_integration.py),
[tests/test_calibration.py (PR #14/#15)](../../tests/test_calibration.py)** —
Per-camera calibration tests; stay scoped to the algorithmic engine.
No change.

**[tests/test_colorimetric.py](../../tests/test_colorimetric.py)** —
ColorChecker ΔE2000 harness; methodology aligns directly with the
v0.6 acceptance gate. No change.

**[src/lrt_cinema/presets/ocio_config.ocio](../../src/lrt_cinema/presets/ocio_config.ocio)** —
Minimal OCIO config; A′ doesn't change the output color space (still
linear Rec.2020). Note: per [decision.md:100-102](color-correction/decision.md:100),
"no OCIO config emission" — the existing config is documented as a
v0.1 scaffold and remains harmless for users who don't reference it,
but the decision's "OCIO is not the right surface" framing means it
shouldn't be promoted in v0.6 docs.

## Per-PR disposition refinements

| PR | Decision verdict | This audit's refinement |
|---|---|---|
| #11–#13 | merge as-is | no change |
| #14 (calibration storage) | merge | The PR title/description should clarify that this `.npz` infrastructure is used by both the per-camera DCP extracts (existing) AND by the Tier 2 algorithmic-engine calibrations. The decision says "underpins A′'s shipped artifact" — true only via shared file-format conventions (numpy `.npz` + naming) ; the *DCPProfile* save/load path in [dcp.py](../../src/lrt_cinema/dcp.py) is the actual A′ storage layer, not the Calibration save/load path in [calibration.py](../../src/lrt_cinema/calibration.py). Worth a clarifying line in the PR description so the reader doesn't expect schema overlap. |
| #15 (calibration dt-roundtrip) | keep as Tier 2 baseline foundation | PR description should be updated to acknowledge: the per-camera 3×3 fit is the algorithmic-engine enrichment path, not part of the v0.6 default. The 12.66 ΔE2000 mean post-fit cited as the Tier 2 baseline ([color-correction/option-space.md:182](color-correction/option-space.md:182)) is the FLOOR for plain-3×3-channelmixer-only; useful as an honest baseline for the opt-in path's value-add measurement. |
| #16 (research docs) | merge | The `docs/research/color-option-space-2026-05-26/` directory has been restructured to point at canonical `docs/research/color-correction/`. The current branch has consolidation commits (e.g. 1bc8337); merge as the new canonical doc set. |

## Hidden mistakes uncovered

**1. The decision's "ColorMatrix from camera EXIF" claim is partially
inaccurate for non-DNG RAWs.**
[decision.md:67-69](color-correction/decision.md:67) says: "Per-camera
ColorMatrix continues to come from camera EXIF (TIFF IFD0 for
NEF/DNG/ARW; ISO BMFF for CR3)." In practice only DNG carries an
Adobe-shape ColorMatrix1/2 in IFD0. NEF/CR3/ARW/RAF/ORF/RW2 store
sensor-vendor-specific WB metadata (in MakerNote for NEF, in CR3 ISO
BMFF metadata-atoms for CR3) but NOT a published ColorMatrix1/2 in a
canonical location lrt-cinema can read without a vendor-specific
MakerNote walker. The dt/libraw approach is to bundle per-camera
matrices in adapter.c; Adobe's DCPs ARE the canonical source for
those matrices on non-DNG RAWs.

This means `tools/extract_dcp.py`'s per-camera `.npz` output is STILL
load-bearing for the A′ default path on non-DNG cameras (which is
the vast majority of consumer cameras). The decision's framing that
the per-camera DCP becomes opt-in enrichment via `--engine
adobe-camera` is true for HSM+LookTable but NOT for the underlying
ColorMatrix.

Two ways to resolve:
- (a) A′ on non-DNG falls back to libraw's bundled camera matrices
  (dt does this today). The "Adobe Standard distillation" claim
  weakens slightly because dt's libraw matrices differ from Adobe's
  ColorMatrix1/2.
- (b) A′ on non-DNG requires per-camera `.npz` (extracted via
  `tools/extract_dcp.py`). The "default" path silently requires a
  one-time extraction or a sister `lrt-cinema-profiles` repo clone.

The decision doesn't pick. This is the load-bearing question for
how A′'s "no per-camera HSM/LookTable database is needed" claim
generalizes to non-DNG cameras. Flagged in Open Questions.

**2. `_emit_dropped_field_warnings` two-state boolean.**
Documented above under cli.py Critical. The current code treats
"DCP loaded" as a binary. A′ creates a third state: "shared
HSM+LookTable loaded, but no per-camera ColorMatrix to drive
kelvin→multipliers." In that state, tint/temperature_k still drop
even though the user might think DCP-derived processing is active.
The warning text needs the third arm.

**3. cube-content-hash collision risk under A′.**
[xmp_emitter.py:776-818](../../src/lrt_cinema/xmp_emitter.py:776),
`_cube_content_hash` keys off cube_size + target_k +
baseline_exposure_offset + cube contents. If A′ ships at
`baseline_exposure_offset=0` and all users render at the same
target_k, ALL renders across all cameras hash to the same cube.
That's the right outcome (one cube file per sequence). But it means
the existing per-frame `.cube` file proliferation problem
(documented in xmp_emitter.py:973-978) is even more aggressively
deduplicated than today. No bug — just a robustness check worth
verifying: confirm that two sequences in the same output directory
referencing the same A′ cube share the file safely (atomic write +
content-hash filename means yes; worth an integration test).

**4. `--no-dcp-tone-curve` / `--no-dcp-hsv-cubes` semantics under
A′.**
[cli.py:96-115](../../src/lrt_cinema/cli.py:96) lets the user
suppress DCP-tone-curve and DCP-HSV-cube emission for a "cleaner
truly-linear" output. Under A′:
- `--no-dcp-tone-curve` is a no-op because A′ has no
  ProfileToneCurve ([decision.md:62-66](color-correction/decision.md:62)).
  The flag should either become an A′ no-op (silently) or warn.
- `--no-dcp-hsv-cubes` suppresses HSM+LookTable. Under A′, this
  reduces to the algorithmic engine's behavior (libraw default
  matrix + LR-authored ops). Semantics survive, but the flag name
  becomes misleading.

Either rename the flags (e.g. `--no-shared-transform`) or document
that they apply to per-camera DCP enrichment only.

**5. `--apply-custom-presets 0` may suppress dt's basecurve ACR3
baseline that A′'s ΔE ceiling assumes is active.**
[decision.md:65-66](color-correction/decision.md:65) says: "No
ProfileToneCurve (97% of Adobe Standard ships without one; the dt
basecurve module's ACR3 baseline applies in absence)." The lrt-cinema
runner invokes dt with `--apply-custom-presets 0`
([runner.py:190](../../src/lrt_cinema/runner.py:190)) to disable
workflow auto-injection (filmic/sigmoid prepend) for deterministic
output. That flag also disables dt's camera-specific basecurve
auto-apply presets — meaning basecurve is NOT in the active pipeline
by default under lrt-cinema's invocation, even though it's at iop_order
position 44.0.

If A′'s empirical ΔE ceiling (1.5 mean on modern targets) was
measured with the standard dt workflow active (basecurve auto-applied),
the in-pipeline result under lrt-cinema may diverge. Two paths to
resolve:
- (a) A′ explicitly emits the ACR3 basecurve as part of the shared
  transform (turn the basecurve ACR3 default into a no-op since the
  HSM/LookTable already shaped tone).
- (b) Re-measure A′'s ΔE ceiling with the lrt-cinema invocation flags
  (`--apply-custom-presets 0`) to confirm the cited 1.5 ΔE still
  holds.

Flagged as Open Question 6 below; cited by reference to
[DT_WORKFLOW_EXPOSURE_INTERACTION.md](DT_WORKFLOW_EXPOSURE_INTERACTION.md)
which already shows the project's prior burn on workflow-injection
interactions.

**6. The `OutputColorSpace` advertised in `cinema-aces` preset
description is `lin_rec2020`, matching what darktable emits. The
"ACES" naming on the preset doesn't surface in any test fixture
that bakes in the (incorrect) assumption of ACES2065-1 output. Audit
positive: the naming gap is purely a documentation issue, not a
silent semantic mismatch.**

## Open questions for the maintainer

**1. On non-DNG cameras, does A′ default to libraw's bundled
ColorMatrix or require `tools/extract_dcp.py`'s per-camera `.npz`?**
The decision implies the matrix lives in EXIF for "TIFF IFD0 for
NEF/DNG/ARW" but that's only true for DNG. Decision needs to clarify
whether the default-path UX is "works out of the box on any RAW dt
supports" (libraw matrices) or "requires a one-time per-camera
extraction or a sister-repo clone" (Adobe matrices). Affects whether
[tools/extract_dcp.py](../../tools/extract_dcp.py) remains
load-bearing for the default path.

**2. `--engine` flag naming (decision's Open Question 1).** Three
options:
- (a) `--engine adobe-shared` / `--engine adobe-camera` /
  `--engine algorithmic` — explicit, no backwards-compat.
- (b) Keep `--engine dcp` (auto-select based on presence of
  per-camera DCP) + `--engine algorithmic` only.
- (c) Add `--engine adobe-shared` as the new default, keep
  `--engine dcp` as alias for `--engine adobe-camera`.

The audit can't pick. Affects [tests/test_cli.py](../../tests/test_cli.py)
and the documentation surface.

**3. v0.4 acceptance gate (DSC_4053 post-fit 2.24 ΔE) under A′.**
The decision predicts A′ should "improve marginally" but doesn't
re-measure. Need an actual measurement before v0.6 ships to set the
v0.6 acceptance gate at a defensible number. Cheap to run:
`tools/diagnose_vs_lrt_preview.py` against DSC_4053 with the A′
transform in place.

**4. Should `docs/V03_PLAN.md` and `docs/V04_PLAN.md` get a
"superseded by v0.6" banner, or be moved to a `docs/historical/`
subdirectory?** Both are accurate as historical milestones; both
also surface in repo browsing as load-bearing forward-looking docs.
The audit defaults to "add a banner, leave in place"; the maintainer
may prefer relocation.

**5. `--apply-custom-presets 0` vs A′'s basecurve-ACR3 assumption.**
The decision says "the dt basecurve module's ACR3 baseline applies
in absence" of a ProfileToneCurve, but the lrt-cinema runner
explicitly disables custom-preset auto-apply for deterministic
output. Need to confirm whether the basecurve ACR3 default fires
under `--apply-custom-presets 0` or whether A′ needs to emit the
basecurve explicitly. See Hidden Mistake 5 above. Cheap to verify:
render the same DCP-free RAW with vs without `--apply-custom-presets 0`
and diff the basecurve module's enabled flag in dt's history (or
just diff the output pixels).

**6. Validation: the "modern target cameras" panel in
[decision.md:113-122](color-correction/decision.md:113) shows
Samsung at 3.12 mean ΔE and Sony at 3.50 mean ΔE — both ABOVE the
"cinema-reference ΔE ≤ 2" line. Decision presents this as "holds for
the project's primary target class" but Sony at least is a common
consumer body. Worth clarifying which cameras are explicitly in vs
out of the cinema-reference tier so the README's positioning matches.**

## Recommended action sequence

1. **Merge PR #16** (consolidated research docs including the
   canonical decision.md). Unblocks all downstream references.
2. **Merge PR #11–#13** (independent audit fixes). Order doesn't
   matter.
3. **Merge PR #14** (calibration storage `.npz` infrastructure).
   Adds the `.npz` file-format conventions A′'s shipped artifact
   reuses (via the DCPProfile shape in dcp.py, not via
   calibration.py's schema — see Hidden Mistake 1).
4. **Merge PR #15** (Tier 2 algorithmic-engine calibration), with
   PR description updated to acknowledge it's the algorithmic-engine
   enrichment, not v0.6 default. (Update before merge per PR
   disposition refinement above.)
5. **Resolve Open Question 1** (ColorMatrix source for non-DNG)
   before opening the v0.6 implementation PR — it affects what the
   default-path UX looks like.
6. **Resolve Open Question 2** (engine flag naming) before writing
   the v0.6 implementation PR — it affects the CLI surface and
   test assertions.
7. **Open the v0.6 implementation PR.** Per the decision:
   - `tools/distill_adobe_standard.py` (new) — generates
     `presets/adobe_standard.npz` from the Adobe Standard catalog.
   - `src/lrt_cinema/presets/adobe_standard.npz` (new) — shipped
     artifact.
   - `src/lrt_cinema/cli.py` (modified per Critical above) — new
     engine flag(s), three-state dropped-warning helper.
   - `src/lrt_cinema/runner.py` / `xmp_emitter.py` — only if the
     engine selection demands changes; per "No change needed" the
     renderer plumbing is shape-agnostic.
   - `tests/test_adobe_shared_engine.py` (new) — fixture + integration
     test.
   - `docs/V06_PLAN.md` (new) — replaces V04_PLAN's Stage 3 framing.
   - `docs/RESOLVE_WORKFLOW.md` (new) — per decision.md §2.
   - `README.md`, `SCOPE.md` — re-anchor per High Priority above.
   - `docs/V04_PLAN.md` — add superseded banner (Open Question 4).
   - `src/lrt_cinema/presets/definitions.py` — update `cinema-aces`
     preset description.
8. **Re-measure DSC_4053** post-A′ to set the v0.6 gate
   (Open Question 3). Cheap and unblocks the v0.6 acceptance text.
9. **Apply Low Priority updates** opportunistically in the v0.6 PR
   or a follow-up cleanup PR (docstrings, comment framing).
10. **`CHANGELOG.md`** — populate with everything from v0.2 →
    v0.6 in one pass during the v0.6 release prep.

The substrate is in good shape. The audit surfaces no architectural
contradictions; the v0.6 PR ships the new artifact + thin selection
logic without rewriting the engine.
