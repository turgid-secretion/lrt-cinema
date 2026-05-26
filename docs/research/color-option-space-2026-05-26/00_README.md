# Color option-space research — 2026-05-26

## Context

After landing the Phase 2b "Tier 2 DCP-distillation via dt-cli round-trip"
calibration (PR #15), empirical results showed a **12 ΔE2000 mean residual**
after a 3×3 channelmixer fit — well above the broadcast tolerance of 3, and
much higher than originally hoped.

Rather than continue building down the calibration tower, this research pass
stepped back to map the full option space across photography, cinema,
academia, and adjacent imaging fields. **Four parallel research agents** were
dispatched, each covering a distinct territory. This directory captures their
reports verbatim plus a synthesis + the framing-shift document that emerged
from the user's response.

## Documents

| File | Contents |
|---|---|
| `01_raw_software_landscape.md` | RawTherapee, dcamprof, darktable's official stance, Capture One, DxO, LibRaw, ART, vkdt, rawtoaces, ArgyllCMS |
| `02_academic_research.md` | ISO 17321, CIE TC 8-15, AMPAS P-2013-001, Luther/Maxwell-Ives, Finlayson 2015 root-poly, Kucuk 2022 (NNs fail at color), spectral sensitivity datasets, ML status |
| `03_cinema_broadcast.md` | ACES IDT theory, vendor IDTs (ARRI/Sony/RED/BMD), OCIO, Resolve Color Match, Filmlight, Pomfort, FilmConvert CineMatch, multi-cam workflows |
| `04_adjacent_fields.md` | Astronomy photometric calibration, microscopy stain normalization (Macenko/Reinhard/Vahadane), DICOM, remote sensing HLS, display ICC profiles, audio room correction (Dirac), FFCC, stain GAN |
| `05_synthesis.md` | Cross-cutting findings, what an "academic-best" lrt-cinema would look like, the Luther/Maxwell-Ives theoretical floor explanation, the 3 paths forward (root-poly, SSF-IDT, HSV residual catcher) |
| `06_framing_shift.md` | The user-supplied reframing that emerged from reviewing the synthesis: the problem isn't matching LR's output, it's the **control mismatch** between grade-in-LRT-preview vs render-in-dt. Includes the spawned chip's mission. |
| `07_decision.md` | Intermediate analysis. Empirical LRT preview-cache behavior test (forecloses preview substitution); correction to the framing-shift doc's ACES analogy; surface of TIFF baked-in semantics. The doc's option taxonomy and PR-fate recommendations are **superseded by `08_search_framing.md`** at the recommendation level; the cache-test record and ACES correction remain valid factual inputs. |
| `08_search_framing.md` | Clean-sheet redefinition of the problem at a layer of abstraction that admits solutions from unrelated domains. Surfaces the over-determination present in `01`–`07`, runs a solution-pattern survey across adjacent fields (ICC soft-proofing, parallel-display, room-correction transforms, BIM, in-engine cinematics), and identifies the hard-no constraints that need to be elicited before the next research pass can converge. User constraints landed inline 2026-05-26. |
| `09_dcp_variance.md` | Empirical answer to Q1: cross-camera variance in Adobe DCP catalog fields. Measured 480 DCPs across 52 manufacturers. Result: BaselineExposure (zero everywhere), ProfileToneCurve (absent in 97% of cameras), and LookTable (low variance: hue P95 6°, sat/val ~3% mean) are approximately camera-agnostic in Adobe Standard. HueSatMap has moderate variance. **A' (camera-agnostic Adobe match) is viable on the low-variance branch.** |
| `09a_adobe_match.md` | Feasibility study for the Adobe-match cluster (A + A'). A is 5–7 wks (cheaper than `08` estimated because `lut3d_baker.py` already ships); A' is 3 wks (Branch 1 if Q1 low-variance). Q1 result picks Configuration 3 (A' default, A opt-in enrichment). |
| `09b_display_transform.md` | Feasibility study for the display-transform cluster (G + H + variants). G and H proper are **non-viable** on Linux and macOS-fragile. Salvageable sub-candidates: G2 (T-corrected parallel viewer in lrt-cinema's own window) and H1 (OCIO config for downstream Resolve). |
| `09c_parallel_viewer.md` | Feasibility study for the parallel-viewer cluster (F + I). F is 2–6 wks; the discriminator is dt-cli per-frame latency. I-a (OCIO LUT-bake) is 4–5 wks; I-c (vkdt borrow) is 3–5 wks; I-b (GLSL port) rejected on maintenance grounds. Cognitive-ergonomics field test is load-bearing. |
| `09d_lrt_replacement.md` | Feasibility study for the LRT-replacement cluster (D). Honest estimate: 31–42 engineer-weeks for v1 + 3–6 months/year ongoing maintenance. PySide6 is the UI framework. Half of D's non-UI surface is already implemented. Single-developer maintenance is the structural risk; Linux peer-platform is the structural win. |
| `10_synthesis.md` | Synthesis weighing all candidates with consistent criteria. Three viable shapes: α (A' + G2 + H1, ~6–9 wks), β (α + A enrichment + F-daemon, ~10–15 wks), γ (D clean-slate, 31–42 wks v1 + ongoing). **Default recommendation: Shape α.** Conditional alternatives surfaced for β / γ / K. |

## Status

The research inputs `01`–`05` ran a first pass over the option space.
The framing-shift document `06` reframed the user's problem from
"close the ΔE2000 gap" to "close the control loop." The first
decision-doc draft (`07` v1, v2) attempted to produce a recommendation
off that framing; both pre-determined the solution space too narrowly.

`08_search_framing.md` redefined the search at a layer of abstraction
admitting solutions from unrelated domains. User constraints landed
inline. The analysis phase (`09_dcp_variance.md`, four feasibility
studies `09a`–`09d`) ran in parallel and produced apples-to-apples
cost/risk estimates. `10_synthesis.md` weighs all candidates with
consistent criteria.

**Current state: synthesis is complete and awaiting user sign-off on
the recommended shape (α, β, γ, or K) before implementation begins.**
