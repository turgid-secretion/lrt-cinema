---
name: demosaic-test-fixtures
description: Demosaic test-fixture research — verdict (b) + plan doc; RCD clean-room ALREADY EXISTS (PSNR-only bar); DCB=BSD-3 not LGPL (verified)
metadata: 
  node_type: memory
  type: project
  originSessionId: a19a93a0-395c-4f6d-a480-ad92b3989775
---

Research on test fixtures to verify the clean-room demosaic is "world-class" (branch
`test/demosaic-fixtures`, deliverable `docs/research/demosaic-test-fixtures.md`). Part of
the [[trunk-branch-overhaul]].

**VERDICT = (b): no external set exhaustively verifies a world-class raw-linear Bayer
demosaic.** Every public set (Kodak/McMaster/Gharbi-MIT/WED/DIV2K) is re-mosaiced 8-bit
**sRGB** → circular GT + wrong domain (demosaic runs on the LINEAR mosaic, pre-WB/pre-matrix;
SPIE 7876 proves the ranking flips by domain). MSR is the lone real-raw+linear set but its GT
is band-limited (down-sampled) + licence unverified. So: build our own LAYERED battery,
anchored to the 3 axes (D1 correctness / D2 world-class-vs-TRUE-GT / D3 artifacts / D4
temporal). **THE TRAP (don't lose it):** matching AMaZE/RCD output = *port fidelity*, NOT
world-class — they have NO peer-reviewed CPSNR; quality is only vs true GT. Falsifiable bar
(published, §6 of doc): Kodak ~40.5–42 / McMaster ~36.5–37.6 CPSNR classical; ~42/~39 CNN;
bilinear floor ~32.9. Metric BATTERY not PSNR: CPSNR + S-CIELAB(false colour) + Lu&Tan
zipper%/region-split + MTF50**P** + dead-leaves acutance; "FCIR" metric DOESN'T EXIST;
luma-SSIM near-blind to false colour.

**MOST SURPRISING / expensive-to-rederive:** the **RCD clean-room ALREADY EXISTS** —
`src/lrt_cinema/_rcd_demosaic.py` + `tests/test_rcd_demosaic.py` (sibling chip). It's a solid
*fixture-free Axis-D1 correctness gate* (synthetic edge/zone-plate/bars/texture, all 4 Bayer
phases, flat-patch bit-exact, highlight pass-through) BUT its bar is only "**beat inline
bilinear by N dB on PSNR**" — which cannot distinguish world-class from AHD-grade, and is
blind to false colour/zipper/texture-loss. The world-class claim is currently UNFALSIFIABLE.
The plan EXTENDS this harness (new files: `tests/demosaic_fixtures.py`,
`test_demosaic_quality.py`, `tools/demosaic_bench/`, metric module) — never rewrites it.

**VERIFIED FACTS (first-class, my own read):** (1) DCB = **3-clause BSD, "Jacek Gozdz"**
(LibRaw `dcb_demosaic.cpp` header, Copyright 2010) — NOT "LGPL" (was wrong in
pipeline-overhaul-plan.md line 16, now fixed) and NOT "Górny" (brief's misspelling); BSD →
DCB source is clean-room-readable. AMaZE/RCD = GPLv3 (black-box only). (2) **D750 SSF is
unpublished** anywhere (camspec lacks it; `colour.MSDS_CAMERA_SENSITIVITIES` = only
`Nikon 5100 (NPL)`) → justifies the repo's D5100 substitution for demosaic spatial quality.
