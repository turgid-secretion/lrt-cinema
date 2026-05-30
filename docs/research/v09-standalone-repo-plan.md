# Standalone app — separate repository (scope boundary)

**Decision (2026-05-29):** the **standalone timelapse application** (a full
LRTimelapse *replacement* — own authoring, own colour science, own UI) is a
**separate product and will live in its own repository.** It is **not** part of
`lrt-cinema` and will not be built here.

## Why separate
| | lrt-cinema (this repo) | standalone app (future repo) |
|---|---|---|
| Product | LRT **companion** renderer (reads LRT XMP → cinema intermediate) | LRT **replacement** (ingest + author + render its own timelines) |
| Colour science | clean-room Adobe-DNG pipeline, validated < 1 ΔE vs `dng_validate` | **its own** (not required to match Adobe) |
| Stack | Python | C++/Vulkan (vkdt fork) or Rust/wgpu + Qt-QML / web UI |
| Licence | Apache-2.0 | **not** Apache — vkdt is BSD-2 (+ isolated GPLv3 to audit); Qt is LGPL |
| Lifecycle | stable renderer | new app, multi-year build |

Different product, stack, and licence ⇒ different repo.

## Relationship — siblings, not parent/child
- `lrt-cinema` remains the **validated colour-science reference**: its DCP /
  ProfileToneCurve / ExposureRamp / develop-op math (0.79 ΔE gym, 0.84 rose) is
  the ground truth the standalone can **port to GPU and validate against**.
- The standalone **does not depend on** lrt-cinema as a library (different
  language). lrt-cinema does not depend on the standalone.
- Shared only by reference (the math) and by these research docs until spin-out.

## Spin-out trigger
Create the new repo **only when the vkdt-fork feasibility + UI strategy passes
scrutiny** (see `v09-vkdt-fork-ui-strategy.md` + the spawned R&D task). No empty
repo before there is a GO — that's premature fragmentation.

## What moves at spin-out
- `docs/research/v09-standalone-app-build-vs-not.md` → new repo (R&D seed).
- `docs/research/v09-vkdt-fork-ui-strategy.md` → new repo.
- This file → updated to point at the new repo URL.
- `lrt-cinema` keeps the **survey** (`v08-timelapse-emission-survey.md`) with an
  outward pointer; §1.5 "standalone" stays as the decision record of *why* it
  was spun out.

Until then these v09 docs live here as **decision-space research** (they answer
"what should this effort be" — a legitimate lrt-cinema strategic question). No
standalone **code** lands in lrt-cinema.

## Provisional identity (TBD — user's call)
- **Name:** must be LRT-independent (it's a replacement, not a companion), so
  **not** `lrt-*`. Placeholder names to react to: *LapseForge*, *Chronoframe*,
  *Aperture Lapse*. Final name is the user's decision.
- **Licence:** permissive, chosen to keep a commercial option open and to fit a
  vkdt (BSD-2) base — decided at repo creation.
