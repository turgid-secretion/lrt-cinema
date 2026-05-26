# Adobe-match cluster feasibility study (A + A')

*Feasibility input for the candidate-cluster comparison. Companion docs:
`08_search_framing.md` (constraint statement), `05_synthesis.md` (math
primitives), `01_raw_software_landscape.md` (tool survey), `07_decision.md`
(cache-test, ACES analogy), `09_dcp_variance.md` (Q1 measurement output;
absent at time of writing — A' subsections flag the dependency).*

## Cluster summary

Both candidates pursue Pattern 6 (procedural encoding) with an inverted
Pattern 3 (corrective transform from B toward A). They differ only in
whether the correction is per-camera (A) or camera-agnostic (A'). The
cluster's structural privilege over workflow-side candidates (F, G, I,
D): it requires no change to LRT, Resolve, or the grader's daily
experience.

Critical scope clarification: this cluster matches Adobe's **camera-
response stage** (matrix + DCP-style hue twist + tone curve) — *not*
Adobe's PV2012 develop slider math (Exposure2012, ToneCurvePV2012,
etc.), which is closed in acr.dll per `DNG_SDK_FEASIBILITY.md`. The
slider math is translated separately by `src/lrt_cinema/xmp_emitter.py`
into dt-native modules. Adobe-match closes the *base profile* gap; the
develop-intent translation is a discrete second axis.

The current codebase has substantially more A-shaped infrastructure
landed than the prior research passes acknowledged: PR #14 (calibration
`.npz` storage) and v0.4's `lut3d_baker.py` ship the HueSatMap +
LookTable trilinear-sample stage (Path 3 from `05_synthesis.md`)
including RT-compatible hexcone HSV, sRGB OETF/EOTF on V,
BaselineExposureOffset positioning, and dt `lut3d`-module integration.
The DSC_4053 post-affine ΔE 2.24 residual in `07_decision.md` is what
remains *after* this stage has fired. The work-item costing reflects it.

## Candidate A: Adobe-match per-camera (full calibration tower)

### Implementation surface

A is the union of three transform layers, each per-camera-data-dependent:

1. **Linear matrix (ColorMatrix / ForwardMatrix)** — camera RGB → XYZ(D50),
   per-illuminant mired-blend. Today: `dcp.py::parse_dcp` reads from DCP;
   passed to dt's `colorin` module. The remaining A work is improving
   beyond plain 3×3.

2. **Non-linear HSV cube (HueSatMap + LookTable)** — per-cell hue/sat/val
   transform via Adobe's hexcone HSV with sRGB-encoded V axis. Today:
   `lut3d_baker.py::bake_dcp_cubes_to_resolve_cube` emits a Resolve `.cube`
   alongside the XMP, loaded by dt's `lut3d` module. Math matches RT's
   `dcp.cc::hsdApply` reference. **Path 3 from `05_synthesis.md` is
   shipped, not pending.**

3. **Per-channel root-polynomial regression (Finlayson 2015)** — 3×N
   matrix on √, ∛ root terms of (R,G,B). Order 2 = 6 terms; order 3 = 13.
   Exposure-invariant by construction. Not implemented. `colour-science`
   provides `polynomial_expansion_Finlayson2015` and
   `optimisation_factory_Oklab_15`. Replaces layer 1's plain 3×3 when
   per-camera SSF is unavailable.

4. **SSF-integrated IDT** (optional layer 1 alternative) — closed-form
   integration of camera SSF × illuminant against target primaries.
   `colour.matrix_idt` provides the math; `butcherg/ssf-data` provides
   the database (CC BY-NC-SA 4.0; user computes locally). When SSF
   available, replaces layer 1 matrix; otherwise root-poly takes over.

The dataflow for a fully-fitted A render of a single frame:

```
RAW → parse EXIF Make/Model → look up per-camera calibration data:
                              ├─ ForwardMatrix1/2 + illuminants (always — DCP)
                              ├─ HueSatMap / LookTable cubes (when DCP carries)
                              ├─ ProfileToneCurve (when DCP carries)
                              ├─ BaselineExposure / Offset (always — DCP)
                              ├─ Root-poly fit coefficients (compute or cached)
                              └─ SSF-IDT matrix (when SSF available)

emit XMP → darktable-cli:
  colorin (custom matrix: root-poly or SSF-IDT or ForwardMatrix-blend)
  → temperature (kelvin → multipliers via dcp.kelvin_tint_to_dt_multipliers)
  → CRS-translated modules (xmp_emitter output: exposure, channelmixer, etc.)
  → lut3d (HSM + LookTable + BaselineExposureOffset bake from lut3d_baker)
  → tonecurve (ProfileToneCurve from DCP)
  → output linear Rec.2020 TIFF
```

Files affected:

- `src/lrt_cinema/dcp.py` — add root-poly fit code path; SSF reader; new
  function `fit_root_poly_from_dcp` (synthesizes target patches from the
  DCP's ColorMatrix + illuminant, fits a 3×N polynomial against them).
- `src/lrt_cinema/calibration.py` (per #14) — store root-poly
  coefficients in the extracted-profile `.npz` alongside the existing
  matrices. New optional field; load_profile reads it when present.
- `src/lrt_cinema/runner.py` — wire root-poly / SSF-IDT into the matrix
  passed to dt's `colorin` module. Engine choice (`--engine dcp` today)
  expands to `--engine dcp-rootpoly` / `--engine dcp-ssf` flavors, or
  keeps the same flag and auto-selects from available calibration data.
- `tools/build_calibration.py` (new or extension of
  `tools/extract_dcp_library.py`) — at calibration-build time, for each
  camera, optionally also compute root-poly fit and SSF-IDT and store
  them in the per-camera `.npz`.
- `tests/test_root_poly.py`, `tests/test_ssf_idt.py` — synthetic and
  real-fixture coverage.

### Dependencies (with license)

| Dependency | License | Shipping vs install-only | Notes |
|---|---|---|---|
| `colour-science >= 0.4.7` | BSD-3 | Shipping (already a dev extra; promote to base for runtime use) | Provides `polynomial_expansion_Finlayson2015`, `optimisation_factory_Oklab_15`, `matrix_idt`. Compatible with Apache-2.0. |
| Adobe DCP files (per-camera) | Adobe DNG Spec patent license — royalty-free for reading + writing | Install-only — extracted via `tools/extract_dcp_library.py` to `~/.config/lrt-cinema/profiles/`. Repo ships extracted-data `.npz`, not Adobe `.dcp`. | Apache-2.0 compatible per `KELVIN_MULTIPLIERS_RESEARCH.md`. User must have Adobe DNG Converter (free) installed to extract; alternatively, the project could ship `.npz` derivations as a sister repo. |
| `butcherg/ssf-data` | CC BY-NC-SA 4.0 | **User computes locally** — repo cannot ship derivative profiles. | Coverage: Arri, Baumer, Blackmagic, Canon, Hasselblad, Nikon, Nokia, Olympus, Pentax, Phase One, Point Grey, Sony. Tens of cameras, not hundreds. D750 is present (confirmed in `01_raw_software_landscape.md`). The license bites: lrt-cinema cannot ship pre-computed SSF-IDT matrices in its release artifacts. User-side opt-in derivation only. |
| `dcamprof` (Torger) | GPL-3 | Shell-out only at build time, never linked | Useful as a reference implementation for the spline-smoothed 2.5D LookTable that the dcpTool "untwist" path uses. Optional. |
| `dcpTool` | Freeware | Shell-out only at build time, optional | Provides "untwist" of Adobe LookTable to 2D HueSatDelta. Now redundant given `lut3d_baker.py` handles the full V-dependent LookTable directly; useful only as a cross-check oracle. |
| Adobe DNG SDK | Adobe DNG SDK License (royalty-free derivative works permitted, distribution permitted with commercial-distribution indemnification) | Optional at install/build time, **also viable at runtime as an alternate pipeline** under user's hard constraint | Distinction from PV2012: SDK does demosaic + matrix + DCP HSM/LookTable/ToneCurve/BaselineExposure correctly, but **does not implement** the LR Develop ops (Exposure2012, Contrast2012, ToneCurvePV2012, etc.). For A's purpose — reproducing Adobe's *base profile rendering* — it works. For PV2012 develop math it does not (closed in acr.dll). |
| Adobe DNG Converter | Adobe EULA, free | Install-only on user's machine | Source of DCP files at `/Library/Application Support/Adobe/CameraRaw/CameraProfiles/`. Required on macOS and Windows for the `extract_dcp_library.py` workflow. **No Linux build exists** — see Linux portability below. |

The "Adobe at runtime as an alternate pipeline" path is worth surfacing.
The user's hard constraint permits Adobe-at-runtime as long as it's an
*alternate* pipeline, not the only one. The DNG SDK's CMake builds on
macOS arm64 clean (`DNG_SDK_FEASIBILITY.md` §4). A `--engine adobe-dng-sdk`
flag coexisting with `--engine dcp` (Adobe-free) gives a bit-exact
reference for DCP-application math — useful for QA cross-check. **Does
not** unlock PV2012 develop math (which still lives in closed acr.dll).

### Engineering cost breakdown (engineer-weeks)

Calendar time, single engineer, including review and validation.
Estimates reflect the v0.4 codebase (post-#14 calibration storage,
post-#10 HSV cube baking, post-#15 channelmixer-rgb fit landing
in-progress).

| Work item | Weeks | Notes |
|---|---:|---|
| Root-polynomial fit code (Finlayson 2015, order 2) | 0.5 | `colour-science` provides the math; wrapper in `dcp.py` synthesises target patches from the DCP and fits via `optimisation_factory_Oklab_15`. |
| Root-poly storage in calibration `.npz` | 0.25 | Additive field on the `_PROFILE_NPZ_FIELDS` set; load_profile reads when present. |
| Root-poly runtime application | 0.5 | dt's `colorin` accepts a 3×3 matrix only; root-poly requires expansion to 6 or 13 terms then per-frame multiply. Either bake into a 3D LUT (use existing `lut3d_baker` infrastructure) or apply via a pre-stage Python step before dt-cli. Lower-risk variant: bake to 3D LUT and emit alongside the HSV cube; combined cube becomes the full non-linear-residual stage. |
| SSF-IDT integration | 1.0 | colour-science's `matrix_idt` is a one-call API. The work is reading butcherg's SSF data format (CSV, normalized SSFs at 5nm intervals), plumbing camera-Make/Model → SSF lookup, illuminant selection (per-shoot or D55 default), and per-camera `.npz` storage of the resulting matrix. |
| Per-camera SSF coverage / fallback policy | 0.5 | Decision logic: SSF present? Use SSF-IDT. Else root-poly. Else fall back to current `colorin matrix` ForwardMatrix-blend. Three-tier degradation, all sharing the downstream HSV cube and tone curve stages. |
| Calibration build pipeline | 1.0 | Extension of `tools/extract_dcp_library.py` to also compute root-poly + SSF-IDT during one-shot user-run extraction. Or a separate `tools/build_calibration.py`. CLI ergonomics: progress bar, skip-existing, force-rebuild. |
| Validation harness | 1.5 | ColorChecker Classic 24 + AMPAS 190-reflectance training set rendered through (a) Adobe `dng_validate` for ground truth and (b) lrt-cinema's render. Per-patch ΔE2000 in Oklab and CIE Lab. Comparison against the industry-tier table from `05_synthesis.md`. Per-camera regression test grid; CI gate. Hooks already partly exist (`docs/VALIDATION.md`); the new work is wiring the root-poly / SSF-IDT paths into the harness. |
| Documentation | 0.5 | User-facing: "how to build per-camera calibration"; "what cameras get SSF-IDT vs root-poly vs matrix-only"; "expected ΔE per tier". Internal: the data-flow diagram above; the three-tier fallback decision tree. |
| Acceptance gate + release | 0.5 | The current v0.4 acceptance gate (DSC_4053 post-affine 2.24 ΔE per `V04_PLAN.md`) is camera-specific. Generalise to "P95 ΔE2000 across N supported cameras × M illuminants ≤ T_target", where T_target derives from the tier the camera-data falls into. |
| **Subtotal (full stack)** | **5.75** | Per-camera mean ΔE2000 ≤ 2 on SSF-data cameras; ≤ 4-6 on root-poly-only; ≤ 6-12 on matrix-only fallback. |
| Optional: Adobe DNG SDK alternate-pipeline engine | +2.0 | If the user wants the Adobe-rendered reference at runtime. Build SDK from source with the existing macOS arm64 patch (`DNG_SDK_FEASIBILITY.md` §4), wire as `--engine adobe-dng-sdk`. Adds a runtime dep on the SDK binary; ships under the SDK's distribution terms. |
| **Subtotal (with Adobe SDK option)** | **7.75** | |

Compare against `08_search_framing.md`'s prior 6-8 wk estimate: that
estimate predates #14 + #10 + the in-flight #15. The HSV catcher being
shipped, the calibration storage being merged, and the linear-baseline
3×3 fit being in PR moves the total down by ~2 weeks. Realistic v0.5
target window: **5-7 calendar weeks for a single engineer focused on
this stack**.

### Risk register

**Luther floor — partially binding.** Vazquez-Corral 2014 measured
~1.83 ΔE2000 mean as the 3×3 ceiling; dcamprof publishes 1.7-3 ΔE
training/unseen. Root-poly halves it on chart-relevant chromaticities;
SSF-IDT gets sub-2 ΔE when SSF available. The HSV residual catcher
addresses the non-linear residual that 3×3 fundamentally cannot
represent. Combined stack achieves cinema-reference tolerance on
SSF-equipped cameras; degrades gracefully otherwise. Worst case
(matrix-only fallback + saturated content like blue-sky timelapse):
3-4 ΔE residual — broadcast-acceptable, not cinema-acceptable.
Mitigation: per-camera tier classification documented per camera.

**Adobe DCP version churn.** ACR releases multiple times per year
(18.3 May 2026, 18.2 Feb 2026, 17.x late 2025); some DCPs revise per
release. Calibration `.npz` already has `_PROFILE_FORMAT_VERSION` for
versioning; extend with extraction-time ACR-version metadata + a
"re-extract" prompt when installed ACR is newer. **Risk: low.**

**Adobe DCP license interpretation.** DNG spec patent grant is
royalty-free for reading/writing. Project `.npz` extraction stores
profile *data*, not Adobe's `.dcp` format, sidestepping "Adobe format
redistribution" interpretation issues. **Risk: low.**

**Adobe DNG SDK license at runtime.** If the optional `--engine
adobe-dng-sdk` ships, the SDK's commercial-distribution indemnification
clause applies. For Apache-2.0 open source with no corporate entity,
this is a contract risk on individual maintainers if Adobe asserts.
Mitigation: opt-in via local-build flag, not bundled in release
artifacts. **Risk: moderate**, mitigable to low by build-policy.

**SSF data license (CC BY-NC-SA 4.0).** Non-commercial-only.
User-computes-locally posture (`05_synthesis.md`) means lrt-cinema
ships no SSF-derived data; the NC obligation lands on users who use
the project commercially, not on the project. **Risk: low for project;
user owns the NC obligation.**

**Maintenance burden over time.** Adobe adds 50-100 cameras/year to
DCP catalog; project coverage grows via user-run extraction
automatically. SSF coverage grows organically through butcherg's repo;
should be marketed as opportunistic enrichment, not coverage guarantee.

### Validation plan

**Primary test**: per-camera ColorChecker Classic 24 + AMPAS 190-patch
reflectance set, computed at two illuminants (D65 and a measured-scene
illuminant from the user's working sequence, e.g. ~4900 K from the
DSC_4053 sequence's EXIF/XMP).

**Ground truth**: Adobe `dng_validate -cs2020 -32 [dng_path]` rendering
of the same patches through Adobe's reference DCP application. This is
the salvage value of the DNG SDK per `DNG_SDK_FEASIBILITY.md` §6 — the
SDK reproduces Adobe's *base profile* rendering correctly (matrix +
HSM + LookTable + ToneCurve + BaselineExposure), which is precisely what
A is trying to reproduce. Not the user-develop-op stage.

**Metric**: ΔE2000 in Oklab and CIELab, reported as mean + P95 + max.
Pass criterion per-camera per-tier:

| Tier | Calibration data available | Target mean ΔE2000 | Target P95 ΔE2000 |
|---|---|---:|---:|
| 1 | SSF + DCP | < 1.5 | < 3.0 |
| 2 | DCP-only with root-poly | < 3.0 | < 5.0 |
| 3 | DCP-only matrix fallback | < 6.0 | < 12.0 |

**Test cases**: ColorChecker patches (controlled), AMPAS 190 patches
(uncontrolled saturation coverage), a saturated-blue synthetic
spectrum (the case `01_raw_software_landscape.md` flagged as hardest),
a skin-tone synthetic spectrum (perceptually-load-bearing).

**Regression**: per-camera regression test grid in CI. New camera
support PR includes the ΔE measurements in the PR body; existing
cameras have CI-asserted tolerances.

**What the test does not measure**: the LRT-preview → lrt-cinema gap
in *user-develop intent* (Exposure2012, ToneCurve, etc.). That's
addressed by the separate emitter-side validation harness already in
`tests/test_xmp_emitter.py`, which is unaffected by this work.

### Linux portability

Linux-as-primary-alternate is a user-stated constraint
(`08_search_framing.md`). The Adobe-match stack is mostly portable, with
one data-source restriction.

| Component | Linux status |
|---|---|
| `colour-science`, NumPy, Python core | Portable — pip-installable everywhere. |
| `src/lrt_cinema/dcp.py` parser | Pure Python; works everywhere. |
| `src/lrt_cinema/lut3d_baker.py` | Pure NumPy; works everywhere. |
| `tools/extract_dcp_library.py` workflow | Requires Adobe DNG Converter installed locally to source `.dcp` files. **No Linux build of DNG Converter exists.** Adobe ships DNG Converter only for macOS and Windows. |
| Adobe DNG SDK build (optional alternate engine) | Builds on Linux per `emmcb/adobe-dng-sdk`. The macOS-framework patch from `DNG_SDK_FEASIBILITY.md` §4 is not needed on Linux. |
| `butcherg/ssf-data` SSF acquisition | Portable — repo is plain CSV / JSON. |
| `dng_validate` reference rendering for validation | Builds on Linux. |

**Linux user path**: extract calibration `.npz` files on a macOS/Windows
machine with Adobe DNG Converter installed, copy them to the Linux
machine under `~/.config/lrt-cinema/profiles/` (or set
`LRT_CINEMA_PROFILES`). The existing extracted-profile lookup path in
`dcp.py::auto_detect_profile` already supports this. Document the cross-
platform recipe. Alternative: project ships pre-extracted `.npz` files
for the top-N supported cameras under a separate repo (license-safe per
the `extract` posture, since `.npz` is the project's own format derived
from a royalty-free spec).

The Adobe DNG Converter macOS/Windows-only restriction is the only
non-portable dependency, and it applies at calibration-build time, not
runtime. Once `.npz` profiles exist, the Linux render path is identical
to macOS.

## Candidate A': Adobe-match camera-agnostic (shared transform)

### Implementation surface

A' replaces per-camera calibration with a single shared transform
approximating "the Adobe look." Q1 from `08_search_framing.md` asks
whether per-camera variance in LookTable / ProfileToneCurve /
BaselineExposure is low enough that one shared transform captures the
perceptual character.

Two ways A' could be defined, and the choice matters:

**A'(a) — Adobe Standard intent.** Adobe Standard is *designed*
camera-agnostic per Adobe's documentation: "delivers a consistent
unified look across all cameras." Distill its design intent across the
catalog and ship as one transform; the linear matrix stays per-camera.

**A'(b) — Median Camera Standard.** Camera Standard varies per-camera
*by design* (manufacturer-specific intent). A median would be a
synthetic look that no real profile produces. LRT's default is
`crs:CameraProfile="Camera Standard"` (per `dcp.py::find_dcp_for_camera`),
so the grader's perceptual reference is per-camera Camera Standard.
A'(b) breaks that reference for every camera.

The viable A' is **A'(a)** — match Adobe Standard intent — with
documented workflow caveat: A'(a) matches what the grader would see if
they switched LRT to Adobe Standard, not what they see today (Camera
Standard default). Workflow-fit risk, not technical blocker.

The dataflow:

```
RAW → parse EXIF Make/Model → look up per-camera ForwardMatrix only
                              (the linear stage stays per-camera, since
                               the sensor → XYZ mapping is camera-
                               physical, not aesthetic).

emit XMP → darktable-cli:
  colorin (per-camera ForwardMatrix; same as today's --engine dcp)
  → temperature, etc. (unchanged)
  → lut3d (single Adobe-Standard-distilled HSV cube; SHARED across cameras)
  → tonecurve (single Adobe-Standard-distilled tone curve; SHARED)
  → output linear Rec.2020 TIFF
```

Files affected:

- `src/lrt_cinema/dcp.py` — same `parse_dcp` reads the per-camera DCP
  for the matrix only.
- `src/lrt_cinema/presets/` — ship a single `adobe_standard.npz` (or
  `adobe_standard.cube` directly) containing the distilled shared
  transform. License: the data is Adobe-derived; same Adobe-DCP-data
  status as the per-camera `.npz` files.
- `src/lrt_cinema/runner.py` — `--engine dcp-shared` or similar flag
  that bypasses the per-camera HSV cube and tone curve, substituting
  the shared transform.
- No new SSF dependency. No new root-poly dependency (unless A' is
  pursued *with* the per-camera matrix improvement from A; see "How A
  and A' interact" below).

### Dependencies (with license)

| Dependency | License | Shipping vs install-only | Notes |
|---|---|---|---|
| Adobe DCP files (one or many "Adobe Standard" profiles, for distillation) | Adobe DNG spec patent license | Install-only at *distillation* time; the distilled output is project-derived data the project can ship under its own Apache-2.0 license. | The distillation happens once, by the project maintainer (or by a one-shot build script); resulting transform ships in `presets/`. No per-user extraction needed. |
| `colour-science` | BSD-3 | Shipping (for cube interpolation, ΔE measurement). | Same as A. |
| `butcherg/ssf-data` | N/A — A' doesn't use SSF | — | A' does not depend on per-camera SSF data. |
| `dcamprof`, `dcpTool` | N/A | — | A' does not require them. |
| Adobe DNG SDK | N/A | — | A' does not require Adobe runtime. |
| Adobe DNG Converter | Required once for catalog enumeration / distillation | One-shot at distillation time on the maintainer's machine; users do not need it. | Significant relief: A' users do NOT need Adobe DNG Converter installed. Linux portability becomes trivial. |

A' is materially cheaper on the dependency side. The shipping-time
artifact is a single `.cube` or `.npz` file in `presets/`. The
distillation tool is run-once by the project; users consume the result.

### Engineering cost breakdown (engineer-weeks)

**Conditional on Q1 outcome.** Q1 (`09_dcp_variance.md`, expected to
land in parallel with this study) determines whether A' is even viable.
Three branches of the cost model:

**Branch 1 — Q1 returns low variance (cross-camera mean ΔE < ~2).**
A' is viable. The cost is the distillation tool + the runtime path +
validation:

| Work item | Weeks | Notes |
|---|---:|---|
| Distillation tool (Adobe-Standard catalog → shared transform) | 1.0 | Iterate the Adobe Standard catalog (~4000 DCPs), parse each, compute a median or weighted-average HSV cube + tone curve, validate convergence stability. Use `colour-science` for ΔE-weighted averaging. |
| Shipping artifact (presets/adobe_standard.npz + lookup wiring) | 0.5 | Drop the file in `presets/`, point the runtime at it via a new engine flag. |
| Runtime integration | 0.5 | Plumb the shared transform through `runner.py` / `lut3d_baker.py` (the latter already handles cube application; reuse). |
| Validation harness | 1.0 | Compare lrt-cinema's A' render against per-camera Adobe Standard render via `dng_validate`. Pass criterion per camera; aggregate across all supported cameras. |
| Documentation | 0.25 | "What A' is and when it applies"; "how A' differs from A"; "supported camera list with Tier classification." |
| **Subtotal (Branch 1)** | **3.25** | Per-camera mean ΔE2000 ≤ 2-3 across the catalog. |

**Branch 2 — Q1 returns medium variance (~2-5).** A' partially viable
as per-family transforms (e.g., one transform per manufacturer or per
sensor generation). Cost expands:

| Work item | Weeks | Notes |
|---|---:|---|
| Family clustering algorithm | 0.5 | Cluster cameras into families by similarity of LookTable + ProfileToneCurve; manual review of clustering. |
| Per-family distillation tool | 1.0 | Generate K shared transforms instead of one. |
| Family-lookup runtime | 0.5 | Camera → family resolver in `dcp.py`. |
| Shipping artifacts (K presets) | 0.5 | One `.cube` per family. |
| Validation | 1.5 | More expansive than Branch 1 (K families × N cameras-per-family). |
| Documentation | 0.5 | Family taxonomy explicit. |
| **Subtotal (Branch 2)** | **4.5** | Per-camera mean ΔE2000 ≤ 3 within family; ≤ 4-5 cross-family. |

**Branch 3 — Q1 returns high variance (> ~5).** A' not viable. Cost: 0
(A' is abandoned, A absorbs the work).

### Risk register

**Q1 result risk — primary.** A' is conditional on Q1. High variance →
candidate evaporates. Cannot commit until Q1 lands.

**Workflow-fit risk — secondary.** A' targets Adobe Standard intent;
the grader's LRT preview default is Camera Standard. A' matches a
sibling profile, not the one the grader sees today. Mitigation: ship
A' alongside A; let workflow choose; or instruct graders to switch LRT
to Adobe Standard (workflow imposition many will refuse).

**Adobe Standard catalog drift over time.** Each ACR release revises
some profiles; Adobe Color (2018) was a material rework of prior Adobe
Standard. Snapshot transforms go stale. Mitigation: re-distill against
new ACR releases; ship versioned preset files; user selects.

**Luther floor — same as A.** Per-camera ForwardMatrix still hits the
3×3 ceiling. Root-poly / SSF-IDT can layer onto A' (see "How A and A'
interact").

**License risk — lower than A.** No SSF, no Adobe SDK at runtime.
Shared transform is project-derived data from royalty-free DNG spec.

### Validation plan

**Primary test**: same ColorChecker + AMPAS 190 patches as A.
**Ground truth**: per-camera Adobe Standard render via `dng_validate`
(NOT Camera Standard — A' targets Adobe Standard intent).

**Metric**: ΔE2000 mean + P95 + max per camera; aggregate distribution
across the supported camera set.

**Pass criterion**: depends on the Q1 result.

| Q1 result | Branch | Per-camera mean target | Per-camera P95 target |
|---|---|---:|---:|
| Low | 1 | < 2.5 | < 5.0 |
| Medium | 2 | < 3.5 within family; < 5.0 cross | < 6.0 / 8.0 |
| High | 3 | A' not pursued | — |

**Validation surfaces a tier 2 criterion**: A' should *also* be
validated against the grader's *actual* LRT reference (Camera Standard
in the default LRT config). The expected outcome — A' renders are
visibly different from Camera Standard renders — is the workflow-fit
risk made concrete. Surfacing this comparison in the validation report
helps the synthesis weigh whether A' is workflow-acceptable.

**What the test does not measure**: same caveat as A. The emitter-side
user-develop-intent translation is orthogonal.

### Linux portability

Materially better than A. A' ships a single (or few) `.npz`/`.cube`
preset file(s) with the project. No per-user Adobe install required.
No per-user SSF data required. Linux render is identical to macOS,
out of the box.

The only macOS/Windows constraint is at *distillation time*, on the
maintainer's machine, which has nothing to do with end-user platform.

## How A and A' interact (can both ship? what's the relationship?)

A and A' are not mutually exclusive. They share the linear matrix
stage (per-camera ForwardMatrix or improved root-poly / SSF-IDT) and
differ only in the non-linear residual stage (per-camera cubes + tone
curve for A; shared Adobe-Standard-distilled transform for A').

Three shipping configurations the project could commit to:

- **Config 1 — A only.** ~5.75 wk. Tier-mixed coverage.
- **Config 2 — A + A' co-shipped.** ~9-10 wk. Both engines available.
- **Config 3 — A' default, A opt-in enrichment.** ~6-7 wk total.
  Inverts typical "ship the expensive thing first" failure mode. A'
  carries most users with a cheap shared transform; A is opt-in for
  per-camera fidelity. Root-poly / SSF-IDT enrichment shares between
  engines, so combined cost is sub-additive.

Configuration mapping by Q1 result:

| Q1 outcome | Best config | Reasoning |
|---|---|---|
| Low variance (mean < ~2) | Config 3 | A' is sufficient; A is enrichment |
| Medium variance (~2-5) | Config 2 | Per-family A' + per-camera A both useful |
| High variance (> ~5) | Config 1 | A' not viable; A only |

Q1 thus drives not just A' viability but how A is positioned in the
cluster's product shape.

## Open questions

1. **Q1 (DCP variance).** Pending; A' viability and cluster config
   both hinge on it. `tools/measure_dcp_variance.py` is ready to run.
2. **Adobe Standard vs Camera Standard for A'.** Recommended position:
   A'(a), with explicit doc of the workflow shift to the user.
3. **Root-poly numerical conditioning** on production cameras —
   `07_decision.md` known unknown #3. Verify before commitment.
4. **SSF coverage strategy.** Encourage user contribution to butcherg
   (spectroscope home build is documented), or treat as opportunistic?
   Affects Tier 1 marketing.
5. **Validation reference**: `dng_validate` baseline-DCP render is
   Adobe-runtime-free and reproducible in CI; LR Classic frame export
   adds PV2012-default coverage but Adobe-at-validation-time fragility.
   Recommendation: pin to `dng_validate`; LR sample comparison only at
   release time.
6. **Engine flag taxonomy.** Auto-select (`--engine dcp`) vs explicit
   tier variants (`--engine dcp-ssf` / `--engine dcp-rootpoly` /
   `--engine dcp-matrix`). Synthesis should pick.
7. **Configuration 3 UX surfacing.** If A' is default + A is opt-in,
   what's the flag shape? `--engine adobe-shared` vs `--engine
   adobe-camera`?
8. **Temporal validation.** `V04_PLAN.md`'s DSC_4053 acceptance gate
   is single-frame. Verify temporal stability across a Holy Grail
   sequence under exposure ramps + WB drift.
9. **Adobe DNG SDK at runtime — skip.** +2 weeks for a runtime engine
   that duplicates what `dng_validate` provides in the validation
   harness. Marginal value not worth the SDK contract-risk surface.

---

**Cluster verdict (one paragraph)**: A is buildable in ~5-7
engineer-weeks; most of the non-linear stage is already shipped
(`lut3d_baker.py`). Achievable ΔE2000 tiers: sub-2 (SSF cameras),
3-5 (root-poly fallback), 6-12 (matrix-only). Linux portability fine
modulo cross-platform calibration extraction. A' is conditional on
Q1 — if low variance, A' is ~3 weeks and supersedes A as the default
(Config 3); if high variance, A' is dropped (Config 1 only). The
cluster's structural privilege — no user workflow change — is its
primary trade against F/G/I/D. Q1 is the load-bearing input for the
cluster's product shape, not just for A' viability.
