# Emission format — verified functional (replaces unverified v0.7.x)

> **Framing superseded 2026-05-28** by
> [EMISSION_FORMAT_VERDICT.md](EMISSION_FORMAT_VERDICT.md). This doc assumed
> Resolve was unavailable and so excluded CDNG and treated baked half-float
> EXR as *the* answer. Resolve Studio 21 turned out to be available and
> scriptable; the corrected objective (represent **all** LRT intent **+**
> max recovery, verified in Resolve) lands on a **dual master** (baked look
> stream + scene-ref recovery stream), with CDNG as a max-recovery substrate
> option. The C1–C5 writer/compression/recovery evidence below is still
> valid and is cited by the verdict; only the single-format conclusion is
> superseded.

**Status:** VERIFIED 2026-05-28 by running code on real fixtures.
**Verdict:** the emission format is **16-bit half-float OpenEXR**, in two
profiles (γ look-locked deliverable, β scene-referred recovery master).
**Reproduce:** `python tools/verify_emission_format.py` (headless; no
DaVinci Resolve required).

This document records *measured* results, not a plan. It supersedes the
unverified claims in the `docs/research/v07-*` series and the
manual-only `docs/EXR_VERIFICATION.md` procedure.

---

## 1. Why the v0.7.x series was "failed"

It shipped on claims that were never executed:

- The acceptance gate was a **manual DaVinci Resolve checkpoint** that in
  practice never ran — the procedure itself (`docs/EXR_VERIFICATION.md`)
  errored on its first real execution (commit `73db120`).
- The EXR writer **silently garbled per-channel data** on real-sized
  (~4K×6K) renders by handing strided NumPy views to the OpenEXR binding;
  fixed in commit `8fcd6fd` (`np.ascontiguousarray` per channel). Tiny
  16×16 unit fixtures never triggered it.
- The β preset (`cinema-linear-master`) advertised "preserves HDR
  headroom for recovery" but its ExposureRamp clamped to 1.0 *before* the
  Stage-7 emission point — **zero headroom survived**. The test that
  claimed to guard this was quietly weakened to only assert the outputs
  "differ" (its own comment admitted the clamp). Found and fixed here —
  see §4.

The word the goal stresses is **verified**. The replacement is proven by
the headless battery below, on the real gym scene (Nikon D750,
`DSC_4053`) against the Adobe `dng_validate` reference render.

## 2. Why half-float EXR, and why not CinemaDNG

CinemaDNG's entire value proposition is *"Resolve honors per-frame
develop metadata on debayer."* That claim **cannot be verified without
Resolve** — the exact gap that sank v0.7.x. So the goal's own constraint
(verified-functional, no Resolve available) rules CDNG out on principle,
not convenience.

EXR's correctness is verifiable headlessly: write → read back → compare
to the in-process reference. Every claim below is checked by running
code. (CDNG, CineForm RAW, BRAW/PRR remain characterised and rejected in
`docs/research/v07-proprietary-raw-codec-feasibility.md`; nothing there
changes — they simply aren't *verifiable here*.)

## 3. Measured evidence (gym `DSC_4053`, real 24 MP)

| # | Check | Result | Gate | Pass |
|---|---|---|---|:--:|
| C1 | Writer channel-correctness, real 4016×6016 non-square | **bit-exact** (maxerr 0.0) per channel for float/half × PIZ/ZIP; shear-roll corr 0.35–0.72 ≪ 1.0; channel-swap corr ≤ 0.98 < true | per-pixel round-trip, no swap/shear | ✅ |
| C2 | Compression vs v0.6 `cinema-linear` 32-bit float TIFF (291.85 MB) | half-DWAB **14.94 MB = 19.5×**; half-PIZ 3.61×; half-ZIP 3.34×; float-PIZ (`cinema-aces`) 1.26× | ≥ 10× | ✅ |
| C3 | Stage-7 (β) recovery vs Stage-13 (γ) clip | γ max 1.000 / 0 % over; β max **2.000 / 0.33 % over = 1.00 stop** recoverable headroom (half holds ~30 stops) | β > γ overrange | ✅ |
| C4 | DWAB visually lossless vs lossless ZIP, **real** content | ΔE2000 mean **0.251**, P95 0.82, max 5.86 | mean < 0.5 | ✅ |
| C5 | Color fidelity vs Adobe `dng_validate` (end-to-end) | gym mean ΔE2000 **0.789**, 76.8 % pixels < 1 ΔE | < 1.0 | ✅ |
| C6 | Full CLI integration, both presets, rich keyframes (sat/vib/contrast/tone-curve/mask deltas) over the new overrange→`develop_ops` path | both presets render 3-frame sequence; monotonic interpolation; β differs from γ on every frame; no crash on V>1.0 HSV recompose | both pass | ✅ |

End-to-end recovery confirmed in the *actual emitted file*: the full β
path (render → `apply_develop_ops` → half EXR on disk) yields max 2.27,
0.35 % pixels > 1.0. C6 (`tools/v07_fullstack/run_test.py`) further
confirms the develop ops handle overrange β data cleanly through the real
CLI subprocess — the regime the Stage-7 fix newly created.

## 4. The two verified profiles

| Profile | Preset | Emission | Compression | Use |
|---|---|---|---|---|
| **γ** | `cinema-linear-finished` | Stage-13, full DCP shape baked | half-DWAB, **19.5×** | look-locked deliverable |
| **β** | `cinema-linear-master` | Stage-7, scene-referred, overrange preserved | half-DWAB, **19.5×** | recovery master |

"Reversibility," reframed to what is verifiable: **HDR-recovery
headroom** — overrange highlights (>1.0) that γ's tone curve discards but
β preserves in half-float. This is the cinema "data + grade" split,
measured (C3), not the CDNG "Resolve re-develops the metadata" promise
(unverifiable here, dropped).

**Live defect fixed (this change):** `pipeline.py` now sets the
ExposureRamp `support_overrange=True` on the Stage-7 path only. Stage-9
(γ) is untouched — its ProfileToneCurve clamps to [0,1] regardless — so
the C5 ship gate is bit-identical to the validated v0.6 reference (0.789,
unchanged). `tests/test_pipeline.py::test_stage_7_emission_preserves_more_overrange_than_stage_9`
now asserts real overrange (would catch a regression to the v0.7.1 bug).

## 5. Honest limitations (verified, not hidden)

- **DWAB is lossy.** Mean ΔE 0.25 is visually lossless, but a few outlier
  pixels reach ΔE ~5.9 (DCT ringing in deep shadow/edges). For an
  archival/lossless master, half-**ZIP** (3.34×) or half-**PIZ** (3.61×)
  are the verified lossless options — smaller than v0.6 but far short of
  DWAB's 19.5×. Pick per use: DWAB for delivery, ZIP/PIZ for archival.
- **A keyframed LR `ToneCurvePV2012` re-clips β to [0,1]** in
  `develop_ops.apply_tone_curve_pv2012` — the one develop op that
  destroys overrange. This is the documented Class-B drop (same pattern
  as v0.6's Highlights/Shadows/Whites). The Holy-Grail core
  (exposure/WB/blacks/contrast/sat/vibrance) keeps the headroom.
- **Tool decode beyond Adobe's reference is not asserted here.** C1–C5
  prove the file is correct, small, recoverable, and colour-accurate
  against `dng_validate`. EXR is a universal cinema container (Resolve,
  Nuke, Fusion, Houdini, OIIO all read it), so this is low-risk — but it
  is verification we *can* do headless, unlike CDNG-in-Resolve.

## 6. Reproduce

```bash
python tools/verify_emission_format.py     # C1–C5, prints PASS/FAIL verdict
python tools/v07_fullstack/run_test.py      # C6: full CLI, both presets
python -m pytest tests/test_pipeline.py tests/test_output.py -q
```

Fixtures (dev box): `/tmp/dng_out/DSC_4053.dng`,
`/tmp/dng_out/DSC_4053_dngvalidate.tif`, and the system Adobe
`Nikon D750 Camera Standard.dcp`. Each check skips cleanly if a fixture
is absent; the writer verdict (C1) does not depend on the DCP.
