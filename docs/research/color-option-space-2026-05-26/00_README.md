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
| `07_decision.md` | The fresh-context chip's decision deliverable. Empirical cache-behavior test + user workflow answer → recommendation: ship current state, retire PR #15, re-anchor docs around the two-stage workflow. Includes per-PR fate, option-space comparison matrix, and a concrete action list on sign-off. |

## Status

The research input (`01`–`06`) was carried forward into the decision
document `07`. The decision is awaiting user sign-off; the action list
in `07_decision.md` §"Concrete action list" describes what implementation
follows.
