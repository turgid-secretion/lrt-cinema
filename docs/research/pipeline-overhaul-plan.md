# Pipeline overhaul — trunk/branch refactor, world-class capture, bug fixes

**Binding implementation plan.** Realizes the trunk/branch model
([discussion in session]; [pipeline-worldclass-gap-and-plan.md](pipeline-worldclass-gap-and-plan.md))
and fixes the audited bugs ([pipeline-order-audit.md](pipeline-order-audit.md)).

> **Status:** 2026-06-03, in progress. Owner decisions locked (below). Every
> output-changing step is **flag-gated, default = current byte-exact behaviour**;
> the owner validates the real gym/rose ΔE gate + LRT-JPG north-star before any
> default flips (those gates do NOT run on the dev machine).

## Owner decisions (binding)
- **D-master:** per-frame corrections ALWAYS bake into the scene-linear trunk; a
  `--master-look {bake,defer}` flag (**default defer** = clean master) controls
  whether the *static* creative look is also baked. Perceptual ops stay, optional.
- **D-demosaic:** switch delivery to **DCB** (LGPL, available, ~5 dB > bilinear)
  behind a flag; **clean-room RCD** numba port is a Phase-2 follow-up. (AMaZE=GPL3,
  LMMSE=GPL2, RCD=absent in the installed libraw 0.22.1 → blocked.)
- **D-validation:** flag-gated; defaults preserve current bytes; I verify
  byte-exact identity + the 202 Axis-1 oracles + the synthetic-chromatic ΔE
  harness + unit tests locally; **owner runs gym/rose + LRT-JPG before flipping
  defaults.**

## Local validation surface (what CAN / CANNOT be verified here)
- **CAN:** byte-exact identity, `test_color_oracle.py` (202, fixture-free),
  `test_synthetic_dng.py` (flat-patch ΔE vs stored `synth_dngval.tif`),
  `test_colorimetric.py`, all unit tests, `ruff`, and a live render of the real
  `DSC_4053.dng`.
- **CANNOT:** gym/rose real-scene ΔE-vs-`dng_validate` (no binary, no system DCP),
  the LRT-JPG north-star (no aligned JPGs), and **demosaic EDGE quality** (the
  synthetic harness is flat patches — no edges). These gate the owner's validation.

---

## The target architecture (trunk/branch)

```
TRUNK  (shared, scene-linear, data-preserving — the MASTER)
  demosaic[world-class] → highlight-recon[on mosaic] → WB → camera→ProPhoto
  → HueSatMap → ExposureRamp                                   ← tap-7
        │
        ├── MASTER branch  → [per-frame bake] → [static look IF --master-look bake]
        │                  → ACEScg / ACES2065 (PERCEPTUAL ops, scene-referred)
        │
        └── FAITHFUL branch → LookTable → ProfileToneCurve → develop ops (Adobe order)
                            → sRGB TIFF (the Lightroom look) | ACEScg-finished
```

Mapping to today's code: the **trunk already exists** as `stop_after_stage=7`
(`cinema-linear-master`). The refactor is mostly **routing + gating**, not a
rewrite: scene-referred ops must only ever run on the trunk (tap-7); the Adobe
look (LookTable + tone curve + faithful develop ops) is the faithful branch.

**The one irreducible seam:** Adobe's LookTable is authored on `[0,1]` and cannot
carry over-range, and the tone curve clamps — so the faithful branch is lossy *by
mandate* (it reproduces a lossy target). The trunk stays data-preserving; the loss
lives only on the faithful branch. This is correct, not a defect.

---

## Phase A — locally-verifiable, byte-exact-by-default — SHIPPED (commit f65d674)

| # | Change | Files | Default-preserving? | Local gate |
|---|---|---|---|---|
| **A1** | **Surface every silent drop** — widen `_warn_dropped_ops` to sharpening, user/local masks + mask geometry + non-exposure mask fields, lens/OpcodeList, NR, crop, Dehaze. Restores the §5/§9 honesty invariant. | `cli.py` | yes (warnings only) | `test_cli` unit |
| **A2** | **F2b fix** — `cinema-linear-finished` (tap-9, display-shaped) defaults to **FAITHFUL**, so scene-referred PERCEPTUAL ops only ever run on the tap-7 trunk. `--render-intent` still overrides. | `cli.py` | preset-default change (documented); lrtimelapse/gym path untouched | `test_cli` intent-default unit |
| **A3** | **`--master-look {bake,defer}` (default defer)** — perceptual master applies Stage-11 (per-frame corrections) always; Stage-12 static creative look only when `bake`. Byte-identical on the current constant-grade deliverable (Stage-12 ops are zero → no-ops either way). | `cli.py`, `develop_ops.py` | byte-exact when Stage-12 ops identity (the deliverable) | `test_develop_ops` defer==bake-at-identity |
| **A4** | **Demosaic flag** — `--demosaic {linear,dcb,ahd,dht,vng,ppg}` (**default linear** = byte-exact). DCB is the recommended delivery value; unsupported algo → warn + fall back to linear. | `pipeline.py`, `cli.py` | default linear unchanged | byte-exact linear leg; DCB render-runs |

After A1–A4: run `ruff`, the 202 oracles, synthetic harness, unit tests, and a live
`DSC_4053.dng` render (linear default byte-path + a DCB smoke). Confirm byte-exact
on the default path.

## Phase B — larger, flagged, owner-validates
- **B1 — mosaic-domain highlight reconstruction** (the "recon before/during demosaic"
  ask). New CFA path: read `raw.raw_image_visible` + `raw.raw_colors`, detect clipped
  photosites at the raw WhiteLevel (uniform), reconstruct WB-aware on the mosaic,
  write back, then demosaic. Behind `--highlight-recovery=mosaic` (default keeps the
  current post-demosaic Tier-1). Fixture-free CFA synthetic tests. *Not locally
  edge-validatable.*
- **B2 — `--deflicker-scale` tunable** (default **1.0** = current). The measured ~3×
  under-delivery is a units mismatch with an **uncited basis** — do NOT hard-code it;
  expose the knob + the LRT-JPG calibration procedure so the owner pins it with
  evidence.
- **B3 — faithful clamp reorder (F1)** — prototype behind a flag; gated on an
  exposure-ramped LRT-JPG experiment (DECISIONS §10). Must defuse the THREE clamps
  (ramp/LookTable/curve) and solve the LookTable `[0,1]` domain — hard; stays a
  proposal until the experiment runs.
- **B4 — clean-room RCD demosaic — SHIPPED** (commit 947f86d; `--demosaic rcd`).
  CLEAN-ROOM RCD-*family* (Hamilton-Adams directional green + Malvar colour-difference
  R/B from non-GPL papers — RCD's exact internals are GPL-locked, so the defensible
  equivalent shipped, NOT bit-RCD). `_rcd_demosaic.py` (pure numpy) + a fixture-free
  PSNR oracle (+4.7–10.4 dB vs bilinear). Integrated by extracting the linearised
  Bayer CFA from rawpy → normalise → RCD, which **also preserves highlight headroom**
  (the B1 foundation). Verified on DSC_4053: normalisation matches LINEAR within
  0.03%, 3.6× more detail, headroom max 1.296. **numba acceleration** (3.3 s/frame
  pure-numpy) + the **fixture battery** (docs/research/demosaic-test-fixtures.md) are
  the remaining follow-ups. Default stays `linear` (byte-exact) — owner validates edge
  quality vs the LRT-JPG.

## Phase C — new subsystems (specs; scope later)
- Temporal NR (motion-compensated, master-first); chromatic deflicker; E_warp gate.
- Local/masked-adjustment subsystem (currently silent-dropped → A1 warns first).
- Lens corrections (DNG OpcodeList3) — verify whether dnglab/libraw bake any at decode.
- Capture sharpening for the faithful sRGB path (D2 per-path policy).

## Where /agents + workflow fit
- **/agents (Senior Developer / general-purpose):** isolated module builds — the
  clean-room RCD port (B4), the mosaic-recon CFA module (B1), the temporal-NR module
  (C). Each is a self-contained spec with its own fixture-free oracle.
- **Workflow:** not justified for the *implementation* (sequential, dependent edits
  on shared files). It was the right tool for the *research* fan-out (already done).

## Risks / invariants held
- The **ΔE ship gate** (faithful stages 1–9 → sRGB) and **byte-exact identity** stay
  untouched on every default path; all new behaviour is opt-in until the owner
  validates. `numpy` stays the reference; numba/mlx parity must hold (A4's demosaic
  is pre-`accel`, so the kernels are unaffected; A3 gates Stage-12 which mlx bakes —
  mlx master path must honour the same gate or fall back).
