# Color option-space research — 2026-05-26

## Where to start

**The canonical implementation plan is [`11_recommendation.md`](11_recommendation.md).**

All other documents in this directory (`01`–`10`) are research-history
that fed the decision. They are preserved in tree for reference but
are NOT the action plan. If you only have time to read one document,
read `11_recommendation.md`.

## Context

After landing the Phase 2b "Tier 2 DCP-distillation via dt-cli round-trip"
calibration (PR #15), empirical results showed a 12 ΔE2000 mean residual
after a 3×3 channelmixer fit — well above the broadcast tolerance of 3,
and much higher than originally hoped.

Rather than continue building down the calibration tower, this research
pass stepped back to map the full option space across photography,
cinema, academia, and adjacent imaging fields. Multiple iteration
rounds with the user refined the framing and converged on a canonical
recommendation: **refined Shape α** (camera-agnostic Adobe-Standard
transform + Resolve workflow documentation, ~3.5–4 engineer-weeks).
This is captured in `11_recommendation.md`.

## Canonical recommendation

**[`11_recommendation.md`](11_recommendation.md)** — the action plan for v0.6.
Ships in ~3.5–4 engineer-weeks:

1. **A' (camera-agnostic Adobe-Standard transform)** as the
   non-linear residual stage in the render pipeline.
2. **Resolve workflow documentation** for the two standard color-
   management paths (DaVinci YRGB CM + ACES).

Deferred to follow-up research / v0.7+:

- Metadata-passthrough emission mode (open research task).
- G2 parallel viewer (deferred unless A' validation reveals it's
  needed).
- Shape γ (full LRT replacement) on the horizon for v1.0 if
  long-term Adobe-free posture is committed to with sustainable
  maintenance bandwidth.

## Research history

The full iteration trail is preserved as `01`–`10` in this directory.
The intent is to keep a record of how the recommendation was reached
for future maintainers, not to drive current implementation. If
recommendation revisions are needed, future work should branch off
`11`, not edit the historical trail.

### First research pass (option-space survey)

| File | Contents | Status |
|---|---|---|
| `01_raw_software_landscape.md` | RawTherapee, dcamprof, darktable's official stance, Capture One, DxO, LibRaw, ART, vkdt, rawtoaces, ArgyllCMS | Research input — still valid factual reference |
| `02_academic_research.md` | ISO 17321, CIE TC 8-15, AMPAS P-2013-001, Luther/Maxwell-Ives, Finlayson 2015 root-poly, Kucuk 2022, spectral sensitivity datasets, ML status | Research input — still valid factual reference |
| `03_cinema_broadcast.md` | ACES IDT theory, vendor IDTs, OCIO, Resolve Color Match, Filmlight, Pomfort, FilmConvert CineMatch, multi-cam workflows | Research input — still valid factual reference |
| `04_adjacent_fields.md` | Astronomy photometric calibration, microscopy stain normalization, DICOM, remote sensing HLS, display ICC profiles, audio room correction, FFCC, stain GAN | Research input — still valid factual reference |
| `05_synthesis.md` | Cross-cutting findings; the Luther/Maxwell-Ives theoretical floor; the 3 math paths (root-poly, SSF-IDT, HSV residual catcher) | Research input — math primitives still valid |

### Framing-shift (mid-pass reframe)

| File | Contents | Status |
|---|---|---|
| `06_framing_shift.md` | User's reframing: the problem isn't matching LR's output, it's the control-loop mismatch between LRT-preview-rooted grading decisions vs dt-rendered deliverable | Research input — framing background |

### First decision-doc attempts (superseded)

| File | Contents | Status |
|---|---|---|
| `07_decision.md` | Intermediate decision document. Empirical LRT preview-cache behavior test (forecloses preview substitution); correction to the ACES analogy; surface of TIFF baked-in semantics. Two revision rounds. | **Superseded by `11`** for recommendation. Cache-test record + ACES correction remain valid factual inputs. |

### Clean-sheet reframing + analysis phase

| File | Contents | Status |
|---|---|---|
| `08_search_framing.md` | Clean-sheet redefinition of the problem at a layer of abstraction that admits solutions from unrelated domains. Surveys solution patterns across adjacent fields (ICC soft-proofing, parallel-display, room-correction transforms, BIM, in-engine cinematics). Identifies the hard-no constraints, with user answers annotated inline. | Research input — framing methodology |
| `09_dcp_variance.md` | Empirical Q1 measurement: cross-camera variance in Adobe DCP catalog fields across 480 cameras / 52 manufacturers. Result: BaselineExposure (zero), ProfileToneCurve (absent in 97%), LookTable (low variance) are approximately camera-agnostic in Adobe Standard. **A' viable on the low-variance branch.** | Research input — load-bearing measurement |
| `09a_adobe_match.md` | Cluster feasibility study: A (per-camera) + A' (shared transform). A is 5–7 wks; A' is 3 wks on the low-variance branch. Q1 selects Configuration 3 (A' default, A opt-in enrichment). | Research input — A/A' details |
| `09b_display_transform.md` | Cluster feasibility study: G/H (display-transform correction). G/H proper non-viable on Linux and macOS-fragile. Salvageable: G2 (T-corrected parallel viewer) and H1 (OCIO config). H1 ultimately reframed in `11` as documentation, not engineering. | Research input — display-transform analysis |
| `09c_parallel_viewer.md` | Cluster feasibility study: F (parallel viewer) + I (JIT preview). F is 2–6 wks; the discriminator is dt-cli per-frame latency. I-a (OCIO LUT-bake) is 4–5 wks; I-c (vkdt) is 3–5 wks. | Research input — parallel-viewer analysis |
| `09d_lrt_replacement.md` | Cluster feasibility study: D (full LRT replacement). 31–42 engineer-weeks for v1 + 3–6 months/year ongoing maintenance. PySide6 is the UI framework. Half of D's non-UI surface already implemented. | Research input — Shape γ details |
| `09e_a_prime_ceiling.md` | **Empirical A' ΔE2000 ceiling measurement.** 40-camera evaluation panel × 214 spectral patches. A' achieves ~1.5 mean ΔE on modern HSM-equipped target cameras (Apple/Fujifilm/Google/Nikon Z/Panasonic), ~3.60 mean across full catalog. Cascade (median HSM + median LookTable) does real work. 33³ and 65³ uncompressed cubes perform equivalently; per-camera tuning variance is the binding constraint. | Research input — A' load-bearing measurement |
| `09f_metadata_passthrough.md` | **Metadata-passthrough emission feasibility (v0.7 candidate).** Verdict: DROP, not defer. Resolve does not read XMP develop intent on RAW; Resolve's Camera Raw is BMD's independent YRGB implementation (not Adobe Camera Raw); image-sequence imports apply one Camera Raw decode per clip. Engineering scope ~3 wks would produce code Resolve ignores. | Research input — closes a v0.7 candidate |
| `10_synthesis.md` | Synthesis weighing all candidates with consistent criteria. Three viable shapes (α, β, γ). | **Superseded by `11`** for recommendation. The shape-α refinements per user feedback are in `11`, not here. |

### Canonical recommendation (current)

| File | Contents | Status |
|---|---|---|
| `11_recommendation.md` | **The action plan for v0.6.** Refined Shape α: A' + Resolve workflow documentation. PR chain disposition. Action list. Follow-up tasks. | **CURRENT** |

## Status

Refined Shape α (per `11_recommendation.md`) is the committed
direction. Awaiting user sign-off on the canonical document; once
approved, implementation begins per the action list in `11`.

Push posture: the iteration trail (07 → 08 → 09 → 10 → 11) has
accumulated as multiple commits on the `docs/color-option-space-research`
branch. Per user direction 2026-05-26 ("hold push until implementation
begins"), the branch stays local until v0.6 implementation lands.
