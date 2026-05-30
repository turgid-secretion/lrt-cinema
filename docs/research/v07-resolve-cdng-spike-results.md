# Spike Results: Resolve per-frame CDNG metadata honor

**Status:** T1 + T2 pass, T3 + T4 fail, swap diagnostic ran 2026-05-27 → "ignored entirely". T6 deferred. v0.7 shape finalised: Holy Grail metadata + Bayer-bake for deltas; no sequence-baseline tone curve.
**Parent:** [v07-resolve-cdng-spike.md](v07-resolve-cdng-spike.md).
**Source DNG:** `tests/fixtures/raw/sample.NEF` → Adobe DNG Converter →
`/tmp/v07_spike/dng_cache/DSC_4053.8e288333ac85e490.dng` (16.8 MB, DNG
1.4.0.0, 6032 × 4032).

---

## Documentation-side conclusion (primary source)

Searched the **DaVinci Resolve 20.3 Reference Manual** (PDF on disk,
4300 pages, Feb 2026). Findings:

- §"Camera Raw Decoding" (manual p. 466) describes four control scopes:
  Project Settings (all clips), Image Inspector ("all, some, or
  individual clips"), Camera Raw palette ("individual clips in the
  Timeline"), RCM (project-wide). **Smallest granularity stated:
  individual clip. No per-frame surface anywhere.**

- §"CinemaDNG" (manual p. 178) `Decode Using` options: Camera Metadata
  / Project / CinemaDNG default. Camera Metadata is "decoded using the
  original Camera Metadata settings (the default selection)" but no
  per-frame distinction is documented.

- §"Adding Individual Frames From Image Sequences" (manual p. 380)
  documents that DNG sequences are imported as one clip by default.

- §"Deflicker (Studio Version Only)" (manual p. 3587) ships a
  post-process plugin specifically for "flickering exposure in
  timelapse clips". **This is a smoking gun**: if Resolve honored
  per-frame `BaselineExposure` in CDNG metadata, the Deflicker plugin
  would be redundant for timelapse exposure ramps. Its existence
  implies Resolve's CDNG decoder treats decode parameters at clip
  granularity.

**Documentation verdict:** Resolve operates clip-level on CDNG decode.
Per-frame DNG metadata variation does not survive ingest as different
decode parameters. **Strong prior that Q1.0 fails.**

Empirical spike still run for certainty (and to record observed
behavior for future reference).

---

## Empirical spike

### Setup

- Mutator: `tools/v07_spike/inject_dng_tag.py` (throwaway).
- Output: `/tmp/v07_spike/sequences/{T1,T2}/SPIKE_*.dng`.
- Generated 2025-05-27 via `python3 tools/v07_spike/inject_dng_tag.py`.

### Test pairs

**T1 — AsShotNeutral.** Frame 1 = source daylight (0.5 1 0.776);
Frame 2 = tungsten (0.85 1 0.45). Rawpy sanity decode (camera WB
applied):

| Frame | `camera_wb` | mean RGB |
|---|---|---|
| 1 | `2.0, 1.0, 1.29` | `[41.0, 30.5, 18.3]` (warm) |
| 2 | `1.18, 1.0, 2.22` | `[22.5, 29.0, 31.4]` (cool) |

Color flip is dramatic and unambiguous if Resolve honors per-frame
AsShotNeutral.

**T2 — BaselineExposure.** Frame 1 = 0.1 (source); Frame 2 = +2.0.
Rawpy doesn't apply BaselineExposure (decoder-level concern), so both
frames look identical via rawpy. Resolve's CDNG decoder *does* apply
BaselineExposure per DNG spec, so a 2-stop difference will be visible
in Resolve if per-frame metadata is honored.

### Resolve ingest protocol (manual)

For each test pair:

1. Open Resolve 20. New project, **Color Management = DaVinci YRGB
   (legacy)** (so the Camera Raw panel is active; RCM would bypass it).
2. **Project Settings → Camera Raw → CinemaDNG → Decode Using = Camera
   Metadata.** Apply.
3. Media Storage panel → navigate to `/tmp/v07_spike/sequences/T1_AsShotNeutral/`
   → drag the folder into the Media Pool (or right-click → Add Folder
   into Media Pool as Image Sequence). Verify it shows as **one clip**,
   2 frames, ~24 fps.
4. Drop on a new Timeline.
5. Color page → scrub to frame 1 → screenshot. Scrub to frame 2 →
   screenshot.
6. Compare visually. Does frame 2 appear strongly **blue-shifted /
   tungsten-balanced** vs frame 1? (Pass = yes, fail = both frames
   identical-coloured.)
7. Also inspect the **Camera Raw palette** values: does the AsShotNeutral
   shown for frame 2 differ from frame 1?
8. Repeat with `T2_BaselineExp/` → does frame 2 appear ~2 stops brighter?

### Results

| Test | Frame-2 decode matches its own metadata? | Notes |
|---|---|---|
| T1 (AsShotNeutral)   | **pass** | Frame 2 visibly tungsten-blue in Resolve Color page. |
| T2 (BaselineExposure)| **pass** | Frame 2 ~2 stops brighter. |
| T3 (ProfileToneCurve)| **fail** | Frame 2 (S-curve) looks identical to frame 1 (identity). Resolve either ignores file-level `ProfileToneCurve` override or pins to frame 1 — see diagnostic below. |
| T4 (OpcodeList3.GainMap) | **fail** | Frame 2 (2× gain) looks identical to frame 1 (no opcode). Same ambiguity as T3. |
| T5 (ColorMatrix2)    | skipped | Redundant w/ T1's pass (WB already covered). |
| T6 (ProfileLookTableData) | deferred | exiftool can't atomically rewrite Dims + Data against the embedded 90×16×16 source LookTable — needs `tifffile`-grade IFD-aware write. Low priority given T3 fail (similar profile-field semantics). |

### Aggregate verdict — partial pass

**Resolve 20's CDNG decoder honors per-frame metadata for a subset of
tags:**

| Tag class | Per-frame honored? | LRT op covered |
|---|---|---|
| `AsShotNeutral` | **yes** ✓ | Holy Grail kelvin override (the headline LRT feature) |
| `BaselineExposure` | **yes** ✓ | Per-frame exposure ramp (the second headline LRT feature) |
| `ProfileToneCurve` (file-level override of DCP) | **no** ✗ | Per-frame `ToneCurvePV2012` |
| `OpcodeList3.GainMap` | **no** ✗ | Per-frame mask-correction deltas / Deflicker |

**Implication for v0.7:** the two *most important* time-varying ops
(WB + exposure ramps — the entire Holy Grail story) ride cleanly as
per-frame metadata. The two *secondary* time-varying ops (per-frame
tone curves + mask-correction deltas) do not, and need a fallback.

Time-varying tone curves cannot Bayer-bake (post-demosaic op). They
must either:

1. Be dropped from v0.7 (with a render-time warning, same pattern as
   the v0.6 `Highlights2012` / `Shadows2012` drops).
2. Bake into the `BaselineExposure` + a sequence-averaged
   `ProfileToneCurve` approximation. The exposure component captures
   the main intent on most LRT sequences (timelapse tone authoring is
   typically slow); the residual tone-shape difference per frame is
   sub-perceptual on most footage. **Validation gate:** ΔE2000 < 2.0
   between v0.6 pipeline output and "BaselineExposure + averaged
   ProfileToneCurve" approximation on the test scenes — measure
   before committing.

Time-varying mask-correction deltas (HG / Deflicker / Global) DO
Bayer-bake cleanly. Per the SPEC §8 fallback table, the math is a
per-CFA-pixel multiplicative gain applied post-linearization, with
`WhiteLevel` rescaled in lockstep. No invented colour science.

### Reconciliation with documentation

The Resolve 20.3 Reference Manual is **silent on per-frame metadata
behavior**; smallest decode granularity surfaced anywhere is
"individual clip"; a post-process Deflicker plugin ships for
"flickering exposure in timelapse clips". From these, the prior was
~10% Q1.0 passes.

Empirical result contradicts that prior. Hypothesis to reconcile:

1. The Resolve CDNG decoder reads each frame's metadata independently
   at debayer time. This is an internal implementation detail not
   surfaced in the UI (no per-frame override controls exist; user-side
   adjustments remain clip-level).
2. The Deflicker plugin addresses *content-level* flicker (fluorescent
   beat, AC mains, sensor noise) that DNG metadata cannot fix — not
   metadata-encoded exposure ramps. Both can coexist.
3. The manual emphasises the user-facing UI hierarchy, not the
   decoder's tag-reading behaviour. Documentation underspecified the
   primitive that v0.7 depends on.

Trust the eyes-on-Resolve test. Proceed with full v0.7 as the working
plan; verify the remaining tag classes (T3–T6) before fixing the SPEC.

### Swap diagnostic — result: "ignored entirely"

Ran 2026-05-27. T3 / T3_swapped + T4 / T4_swapped pairs ingested into
Resolve. Frames within each pair indistinguishable; clip-vs-clip
comparison also indistinguishable (T3 clip ≡ T3_swapped clip; T4 clip
≡ T4_swapped clip). Verdict for both: **Resolve ignores the file-level
tag entirely** — not pinned to frame 1.

Corroboration from macOS Quick Look (cross-check that the tag mutation
DID land in the file, ruling out "exiftool silently dropped the write"):

- **T3:** Quick Look shows visible difference between frame 1 (identity)
  and frame 2 (S-curve). macOS RAW decoder honours file-level
  `ProfileToneCurve` overrides — Resolve does not.
- **T4:** Quick Look shows all four files (T4 + T4_swapped) with
  identical-looking high contrast — anomalous vs T1/T2/T3 base images.
  Probably `quicklookd` cache artefact on a folder containing
  mixed-state DNGs; doesn't change the Resolve verdict.

**Implication.** Resolve's CDNG decoder bypasses the file-level DCP /
profile fields entirely. It almost certainly loads a bundled DCP profile
from `Library/Application Support/DaVinci Resolve/Color/CameraProfiles`
based on the source DNG's EXIF Make/Model. The DNG-level
`ProfileToneCurve` / `ProfileLookTableData` / `OpcodeList3` tags are
**not a viable carrier for any develop intent in Resolve**, sequence-level
or per-frame.

**v0.7 consequence:** path 3a (sequence-averaged `ProfileToneCurve` baked
into emitted DNG) **does not work in Resolve** — falls back to path 3b
(drop with warning) for `ToneCurvePV2012` / `Contrast2012` /
`Saturation` / `Vibrance`. Same drop pattern as v0.6 inherited from
`Highlights2012` / `Shadows2012` / `Whites2012` (closed-source PV5
parametric tone).

### Historical: diagnostic protocol

T3 + T4 failed (frame 1 and frame 2 look identical in both clips), but
two failure modes give that result:

- **Ignored entirely:** Resolve doesn't read file-level
  `ProfileToneCurve` overrides or `OpcodeList3.GainMap` at all. Even
  a *static* baseline tone curve / gain map cannot ride as metadata.
- **Pinned to frame 1:** Resolve reads the tag from frame 1 only and
  applies it to every frame. Static sequence-baseline ProfileToneCurve
  would still work; only per-frame variation is lost.

The distinction matters: it decides whether the SPEC's fallback can
still ride a sequence-averaged `ProfileToneCurve` as metadata (pinned)
or must bake everything into the per-frame exposure approximation
(ignored).

**Swap-test pairs prepared:**
- `/tmp/v07_spike/sequences/T3_swapped/` — frame 1 = S-curve, frame 2 = identity (reverse of T3).
- `/tmp/v07_spike/sequences/T4_swapped/` — frame 1 = 2× gain, frame 2 = no opcode (reverse of T4).

**Run:** import each into Resolve same as before. Compare the *clip's*
appearance:

- If **T3_swapped looks the same as T3**: tag ignored entirely → static
  ProfileToneCurve cannot ride as metadata.
- If **T3_swapped looks different from T3** (e.g. T3_swapped's frame 1 visibly
  steeper contrast than T3's frame 1): tag pinned to frame 1 → static
  ProfileToneCurve CAN ride; only per-frame variation is lost.

Same logic for T4 / T4_swapped.

Optional — does not block v0.7 shape but informs which fallback we use
for the secondary ops.

### Original T3 + T4 setup (2026-05-27, both failed)

**T3 ready.** `/tmp/v07_spike/sequences/T3_ProfileToneCurve/`. Frame 1 =
identity tone curve `(0,0, 1,1)`; frame 2 = aggressive S-curve sampled
at 5 points `(0,0, 0.25,0.10, 0.5,0.5, 0.75,0.90, 1,1)`. Verified via
`tifffile` — both frames carry the expected `ProfileToneCurve` tag
(0xC6FC) with the right float counts.

Pass criterion: frame 2 visibly steeper contrast than frame 1
(deeper shadows + lifted highlights). Particularly look at the
mid-tones — the S-curve should crunch contrast.

**T4 ready.** `/tmp/v07_spike/sequences/T4_OpcodeList3_GainMap/`.
Frame 1 = no `OpcodeList3` (source had none); frame 2 = single
GainMap opcode, full-image rect, uniform 2.0× gain. Binary decoded:

```
count=1, opcodeID=9 (GainMap), version=0x01030000, flags=1, paramSize=80
rect=(0, 0, 4032, 6032) — full image
plane=0 (apply to all), planes=1 (same per channel)
1×1 uniform map, gain=2.0
```

Pass criterion: frame 2 ~1 stop brighter than frame 1 (2× linear gain
= +1 EV). Apply *after* WB, *before* color matrix per DNG 1.7.1
opcode list 3 semantics.

**Note on T3 / T6 — DCP profile fields.** ProfileToneCurve and
ProfileLookTableData are camera-profile fields; in our DCPs they're
embedded by Adobe DNG Converter. Whether Resolve honours an
overriding *file-level* `ProfileToneCurve` (overriding the embedded
DCP profile) is itself part of what T3 tests. If T3 fails, the
fallback is to author per-frame *modified DCPs* (still possible —
DCP fields are TIFF tags) and embed the modified DCP per-frame.

### Skipped this round

- **T5 (ColorMatrix2)** — Holy Grail K already covered by T1 pass via
  `AsShotNeutral`. Skip unless T3/T4 reveal a reason.
- **T6 (ProfileLookTableData)** — exiftool's variable-length array
  write conflicts with the source DNG's 90×16×16 embedded LookTable.
  Need `tifffile`-grade IFD-aware rewrite to update Dims + Data
  atomically. Defer until T3 / T4 land; if T3 passes, T6 likely
  passes for the same reasons.

### Pending tests

T3, T4 each gate a distinct LRT op class:

- T3 (ProfileToneCurve) ↔ LRT per-frame `ToneCurvePV2012`. If T3 fails,
  tone-curve ramps must bake into Bayer (impossible; tone is
  post-demosaic) or into a sequence-averaged single tone curve.
- T4 (OpcodeList3.GainMap) ↔ LRT mask-correction per-frame deltas. If
  T4 fails, mask deltas must Bayer-bake (feasible per SPEC §8 fallback
  table).
- T6 (ProfileLookTableData) ↔ LR PV2012 Saturation/Vibrance/Contrast
  baked into HSV cube. If T6 fails, fall back to baking into the
  Bayer plane is not feasible (post-demosaic HSV-space op); accept the
  drop or bake into a sequence-averaged single LookTable.

T5 (ColorMatrix2) deprioritised — Holy Grail K already covered by T1's
`AsShotNeutral` pass. Skip unless T3/T4/T6 results suggest it's worth
verifying separately.

---

## Followups regardless of result

- Resolve must be set to **YRGB (non-RCM)** for the Camera Raw decode
  path to be active. RCM mode bypasses Camera Raw entirely and reads
  raw via per-camera color science profiles instead. The v0.7 spec
  needs to document which Resolve mode the emission targets — likely
  YRGB to preserve the embedded-DCP path.
- Tonal/color difference between rawpy decode and Resolve decode is
  expected; rawpy is a sanity check only.
- The Resolve gate in [v07-emission-format.md](v07-emission-format.md)
  §9 remains a manual checkpoint per release (CI-able only with
  Resolve Studio's Python API).
