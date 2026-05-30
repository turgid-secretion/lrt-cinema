# v0.7 SPEC: Re-developable Compressed Emission (CinemaDNG)

**Status:** Spec (advisory) — 2026-05-27
**Mandate:** Replace v0.6's baked TIFF/EXR emission with a re-developable
compressed raw sequence that is 10–50× smaller than the current output.
**Companion to:** [v06-architecture.md](v06-architecture.md). v0.7 changes
*what we emit*, not the in-process render math; the v0.6 pipeline survives
as the offline reference / validator.

---

## 1. Mandate

v0.6 emissions on the project's 24 MP test scene:

| Preset | Container | Per-frame size | Compression | Re-developable? |
|---|---|---:|---|---|
| `cinema-linear` | 32-bit float TIFF, uncompressed | **~280 MiB** | none | no — pixels are baked through ProPhoto → Rec.2020 |
| `cinema-aces` | 32-bit float EXR, PIZ | **~100–150 MiB** | ~2× lossless | no — same baking |

A 30-frame Holy-Grail ramp emits **~3–8 GiB**. A 1000-frame timelapse
emits **~140–300 GiB**. The user cannot change WB, exposure, or tone
curve downstream — the develop intent is fused into the pixels by
`pipeline.py` Stages 1–9 and `develop_ops.py` Stages 11–12 (per
[v06-architecture.md](v06-architecture.md) §"Pipeline stage order").

v0.7 fixes both:

- **Size goal:** 10–50× reduction vs `cinema-linear` (~5–28 MiB / frame).
  Measured against the `cinema-aces` PIZ baseline (~120 MiB), the same
  CDNG output yields **5–9× lossless** / 15–30× with DNG 1.7 + JXL —
  the headline 10–50× holds vs the TIFF baseline, undershoots the user
  brief on the EXR baseline absent JXL.
- **Re-developability:** downstream tools (Resolve, Adobe Camera Raw,
  RawTherapee) can re-grade exposure / WB / tone curve / color matrix
  *without* re-running `lrt-cinema` — **subject to §8 Q1.0**.

---

## 2. Reframing the constraint

The user's brief says "10–50× compression … with full reversibility."
Read literally, that is **physically impossible** on the v0.6 output:
demosaiced 32-bit float linear Rec.2020 has ~6 bits of entropy per
channel after sensor noise; lossless EXR (PIZ / ZIP / ZIPS) ceilings at
~2–3×. The user's BRAW reference is the tell — **BRAW itself is lossy**
and stores partially-processed sensor data, not bit-exact frames. The
real ask is:

> "Emit a format where develop intent travels as **metadata**, not as
> baked pixels — and the file is small because it stores sensor data
> rather than 32-bit float RGB."

That framing is satisfiable. v0.7 adopts it.

**"Reversible"** in this spec means: the recipient can override every
develop decision the pipeline made (WB, exposure, color matrix, tone
curve) without information loss in the sensor data. It does **not** mean
bit-exact reconstruction of the v0.6 TIFF — that frame is no longer the
artifact; it becomes a validator output.

---

## 3. BRAW rejected — decoder-only

Blackmagic RAW SDK (current: August 2025) is publicly documented and
cross-platform — **for decoding only**. No public encoder exists. BRAW
writes are gated to Blackmagic capture hardware (cameras, decklink
ingest). lrt-cinema is a software renderer on existing NEFs; it cannot
produce BRAW. Rejected.

(For the record: REDCODE R3D and ARRIRAW are similarly gated. ProRes
RAW has a partial SDK but no general write path outside Atomos / Apple
endpoints.)

---

## 4. Candidate evaluation

Compression ratios are vs the v0.6 `cinema-linear` 32-bit float TIFF
baseline (~280 MiB / 24 MP frame).

| Format | Lossless? | Re-developable? | Typical ratio | Resolve ingest | Encoder OSS? | Verdict |
|---|---|---|---:|---|---|---|
| **CinemaDNG, lossless JPEG (DNG ≤ 1.6)** | yes (sensor) | yes | **10–18×** | native (CDNG) | yes — multiple writers | **chosen primary** |
| CinemaDNG, JPEG XL (DNG 1.7) | optional | yes | 20–50× | uncertain May 2026 | partial (libjxl + DNG mux) | **future upgrade path** |
| EXR DWAA/DWAB | no (lossy) | no (still baked) | 10–30× | yes | yes (OpenImageIO) | rejected — not re-developable |
| JPEG XL float (HDR) | optional | no | 15–30× | no native | yes (libjxl) | rejected — Resolve gap + baked |
| ProRes RAW | yes | yes | 5–8× | yes (macOS) | **no public encoder** | rejected — write-gated |
| BRAW | yes | yes | 5–12× | yes | **no public encoder** | rejected — write-gated |
| EXR ZIP/PIZ (status quo) | yes | no | 2–3× | yes | yes | baseline — not target |
| TIFF + ZSTD | yes | no | 2–3× | partial | yes (tifffile 2023+) | rejected — same baking, marginal gain |

CinemaDNG is the only entry on the table that is **lossless on sensor
data**, **re-developable**, **ingested natively by Resolve today**, and
has **open-source writers**.

---

## 5. Chosen format: CinemaDNG sequence with baked develop metadata

### 5.1 The artifact

One `.dng` per frame, lexicographically named to match v0.6 ordering
(`IMG_0001.dng`, `IMG_0002.dng`, …). Each DNG contains:

| Field | Source | Per-frame? | Purpose |
|---|---|---|---|
| `CFAPattern` + `BlackLevel` + `WhiteLevel` + `LinearizationTable` | source DNG (Adobe-converted NEF), `WhiteLevel` may rescale if a mask-correction gain pushed values above source range | yes | reconstructable sensor data |
| Bayer image data, lossless-JPEG compressed; **mask-correction gain pre-applied** | source DNG sensor strip × per-frame LRT mask-correction scalar gain plane | yes | pre-baked per-frame mask deltas |
| `AsShotNeutral` | computed from `DevelopOps` (Holy Grail kelvin override or scene_kelvin default) | yes (Q1.0 spike T1 ✓) | per-frame WB; Resolve honors |
| `BaselineExposure` | `DevelopOps.exposure` × `2^` factor | yes (Q1.0 spike T2 ✓) | per-frame exposure; Resolve honors |
| `ColorMatrix1` / `ColorMatrix2` | from source DNG (DCP-derived for scene K) | sequence-level | color science |
| `ForwardMatrix1` / `ForwardMatrix2` | from source DNG | sequence-level | color science |
| `ProfileToneCurve` | **inherited from source DNG, not modified** (T3 spike: Resolve ignores file-level override; LRT tone curve dropped with warning) | — | not used by Resolve — bundled DCP takes precedence |
| `ProfileLookTableData` | **inherited from source DNG, not modified** (T6 deferred; assumed same Resolve behaviour as T3) | — | not used by Resolve — bundled DCP takes precedence |
| `XMP` block | full `DevelopOps` + LRT-XMP source, as serialised JSON | yes | round-trip auditability |

The DNG-level metadata is what makes the file **re-developable**:
Resolve / ACR / RawTherapee read it as the *default* develop, and the
user can override any field on import.

### 5.2 Compression source

The size win comes from two stacked sources, neither of which is the
JPEG compression itself:

1. **Sensor data vs demosaiced RGB.** Bayer mosaic = 1 channel × 14 bits
   ≈ 1.75 bytes/pixel. Demosaiced 32-bit float RGB = 3 channels × 4
   bytes ≈ 12 bytes/pixel. **6.9× reduction** before any compression.
2. **Lossless JPEG on Bayer.** Industry-measured 1.5–2.8× on typical
   sensor data (1.6× Blackmagic-grade, 2.5× Sony FS). Stacking with
   (1) gives a baseline of **~10–18× vs v0.6 TIFF**.

DNG 1.7 JPEG XL compression (Adobe DCRaw 16+, libjxl) tightens the
floor toward 20–50× but is gated on downstream ingest — see §8.

### 5.3 Why not strip-down the pipeline to just CDNG copy?

LRT authoring carries develop intent that has no clean DNG equivalent.
Empirically (Q1.0 spike), the carriers split into three classes:

- **Per-frame metadata (passes T1+T2 spike):**
  - LR PV2012 `Exposure2012` → `BaselineExposure` per-frame.
  - Holy Grail K + `Temperature` / `Tint` → `AsShotNeutral` per-frame.
- **Per-frame Bayer-bake (T4 spike showed `OpcodeList3.GainMap` not
  honored per-frame):**
  - LRT mask-correction per-frame deltas (HG / Deflicker / Global) →
    apply as a per-frame Bayer-plane multiplicative gain *before*
    lossless-JPEG. Equivalent to applying `OpcodeList3.GainMap` ahead
    of time.
- **Dropped (T3 spike showed `ProfileToneCurve` ignored entirely by
  Resolve — file-level overrides are bypassed in favour of Resolve's
  bundled DCP profile; swap diagnostic confirmed "ignored entirely",
  not "pinned to frame 1"):**
  - LR PV2012 `ToneCurvePV2012` → **dropped with render-time warning**,
    same pattern as v0.6's drop of `Highlights2012` / `Shadows2012` /
    `Whites2012`. Sequence-averaged ProfileToneCurve via a per-sequence
    modified DCP does not work — Resolve uses its bundled DCP, not the
    embedded one. The tone curve shape from LRT is lost.
  - LR `Saturation` / `Vibrance` / `Contrast2012` → **dropped with
    warning**, same reason.

For the LR ops that v0.6 already drops (`Highlights2012`, `Shadows2012`,
`Whites2012` — closed-source parametric tone), v0.7 inherits the drop;
no regression.

The pipeline therefore retains its present compute responsibility:
read LRT XMP, interpolate, derive per-frame `DevelopOps`. What changes
is the **emission**: instead of running Stages 1–13 to produce a baked
frame, it serialises Holy Grail intent into DNG metadata, bakes
mask-correction deltas into the Bayer plane, and copies the result with
lossless-JPEG compression.

---

## 6. Architectural shift

### 6.1 Pipeline as validator, not producer

```
v0.6:  NEF → adobe-convert → DNG → pipeline.render_frame → TIFF / EXR
                                              ↓
                                       (ship to Resolve)

v0.7:  NEF → adobe-convert → DNG → emit_cdng (metadata + sensor copy)
                                              ↓
                                       (ship to Resolve)
        +
       (offline) pipeline.render_frame → reference TIFF → ΔE gate
```

`pipeline.py` and `develop_ops.py` keep producing the reference frame
they always have. The < 1 ΔE gate vs `dng_validate` survives unchanged
(`tests/test_pipeline.py`).

A **second** gate is added: ΔE2000 between *Resolve's decode of the
emitted CDNG* (or `dng_validate`-of-emitted-CDNG as a proxy) and
*`pipeline.render_frame` of the same source*. Both should agree because
they encode the same develop intent. Target < 1 ΔE mean.

### 6.2 Preset rename

`cinema-linear` and `cinema-aces` lose their TIFF/EXR semantics in v0.7.
The replacements:

| Preset (v0.7) | Container | Compression | Notes |
|---|---|---|---|
| `cinema-cdng` | DNG 1.4 sequence | lossless JPEG on Bayer | default; Resolve-ready today |
| `cinema-cdng-jxl` | DNG 1.7 sequence | JPEG XL lossless on Bayer | smaller; gated on Resolve verification — see §8 |
| `cinema-linear` | 32-bit float TIFF | uncompressed | **deprecated, retained for validator parity**; emits a slow warning |
| `cinema-aces` | 32-bit float EXR PIZ | PIZ | **deprecated, retained**; emits a slow warning |
| `stills-finished` | unchanged | — | still `NotImplementedError` (v0.6.x scope, untouched) |

Deprecation flags one major release. Removal scheduled for v0.8.

### 6.3 What lrt-cinema is now

A **CinemaDNG develop-intent baker** for LRT XMP timelapses. Resolve
remains the grade tool; lrt-cinema is the front-end that interprets LRT
authoring and packages it as Resolve-friendly CDNG.

This sharpens the project's pitch: "We don't replace your grade tool;
we make sure LRT's per-frame intent reaches it intact, in a 10× smaller
container, with full override flexibility."

---

## 7. LRT develop intent → DNG mapping

| LRT field | v0.6 location | v0.7 carrier | Empirical fit |
|---|---|---|---|
| `Temperature` / `Tint` / Holy Grail K override | `develop_ops.temperature_k` | `AsShotNeutral` per-frame **(metadata)** | spike T1 ✓ — none lost |
| `Exposure2012` | `develop_ops.exposure` (Stage 11) | `BaselineExposure` per-frame **(metadata)** | spike T2 ✓ — none lost |
| LRT mask-correction (HG / Deflicker / Global) per-frame delta | `develop_ops.mask_corrections` | per-frame Bayer-plane scalar gain, applied *pre-LJPEG* (the `OpcodeList3.GainMap` path the spec originally proposed is unworkable — spike T4 ✗) | none lost in pixel space; "Resolve cannot override" is the trade |
| `Blacks2012` | `develop_ops.blacks` (Stage 11) | absorbed into the Bayer-plane gain envelope (low-region multiplier) | sub-perceptual (TBV) |
| `ToneCurvePV2012` (per-frame or sequence-baseline) | `develop_ops.tone_curve` (Stage 11) | **dropped with render-time warning** — T3 spike + swap diagnostic shows Resolve ignores file-level `ProfileToneCurve` entirely | LRT tone-curve shape lost; pattern matches v0.6's drop of LR PV2012 closed-source parametric tone fields |
| `Saturation` / `Vibrance` / `Contrast2012` | `develop_ops` (Stage 12) | **dropped with warning** — same reason | look intent from LR ops lost in v0.7 emission |
| DCP `ColorMatrix1/2`, `ForwardMatrix1/2`, `HueSatMap`, `LookTable`, `ProfileToneCurve` | `dcp.py` runtime application | **not transported via file-level tags — Resolve bypasses embedded DCP and uses its bundled per-camera profile based on EXIF Make/Model** | sequence-level colour science delegated to Resolve's library; we just propagate EXIF unchanged. v0.6 dcp.py becomes validator-only. |
| `Highlights2012` / `Shadows2012` / `Whites2012` | already dropped (v0.6) | dropped | inherited — closed source |
| `Sharpness` | already no-op (v0.6) | no-op | inherited — sharpening = grade |

**"TBV"** = to be verified empirically in Phase 4 validation, by
computing ΔE2000 between (a) `pipeline.render_frame`-of-NEF (the v0.6
reference) and (b) `dng_validate`-of-emitted-CDNG on gym + rose. Target
mean ΔE < 1.0 for the metadata-only ops (T1+T2 covered); < 2.0 for the
sequence-averaged tone curve approximation; bake-step adjustments if
measured drift exceeds.

---

## 8. Open questions / verification gates

These must resolve before v0.7 implementation commits to the chosen
writer; treat them as the spec's *kill criteria*.

### Q1.0 (blocking — resolved 2026-05-27, partial pass)

Empirically tested via the spike (see
[v07-resolve-cdng-spike-results.md](v07-resolve-cdng-spike-results.md)).
Result: Resolve 20 honors per-frame metadata for **a subset of tags**:

| Tag class | Honoured by Resolve? | LRT op covered |
|---|---|---|
| `AsShotNeutral` | **yes, per-frame** ✓ | Holy Grail kelvin override |
| `BaselineExposure` | **yes, per-frame** ✓ | Per-frame exposure ramp |
| `ProfileToneCurve` (file-level) | **no — ignored entirely** ✗ | LR `ToneCurvePV2012` (per-frame or sequence-baseline) |
| `OpcodeList3.GainMap` | **no — ignored entirely** ✗ | LRT mask-correction deltas |

Resolve's CDNG decoder reads per-frame `AsShotNeutral` and
`BaselineExposure` at debayer time. It bypasses the file-level DCP /
profile fields entirely — almost certainly loads a bundled DCP from
its own library (`Library/Application Support/DaVinci Resolve/Color/
CameraProfiles`) based on the source EXIF Make/Model. Confirmed via
the swap-diagnostic test (T3_swapped, T4_swapped): even when the
mutated tag rides on frame 1, Resolve's decoded clip is
indistinguishable from the un-mutated clip. macOS Quick Look DOES
honour file-level `ProfileToneCurve` overrides, ruling out
"exiftool didn't actually write the tag" as the failure mode.

The two **headline LRT features** (Holy Grail WB + exposure ramps) ride
as per-frame metadata cleanly. The remaining LRT ops fall back to:

- Bayer-bake for **mask-correction deltas** (HG / Deflicker / Global) —
  feasible, the gain plane just gets pre-applied to the Bayer values.
- **Drop with render-time warning** for `ToneCurvePV2012`, LR
  `Saturation`, `Vibrance`, `Contrast2012` — Resolve uses its bundled
  DCP, so neither file-level overrides nor a modified embedded DCP can
  carry these. Users who time-author tone curves in LRT can re-apply
  the curve as a Resolve grade-page node downstream.

The v0.7 product is "mostly full" — neither the original full-metadata
plan nor the pessimistic narrow-bake plan. Section 7 mapping table
below reflects the empirical reality.

Historical-context summary kept for the spec's record:

- Resolve 20.3 Reference Manual (4300 pages, Feb 2026) is silent on
  per-frame behavior. Smallest decode granularity surfaced anywhere is
  "individual clip"; the post-process Deflicker plugin exists for
  "flickering exposure in timelapse clips". These adjacent signals
  pointed toward Q1.0 failing — they don't.
- The Resolve CDNG decoder reads `AsShotNeutral` + `BaselineExposure`
  per-frame at debayer time even though the Camera Raw UI offers only
  clip-level overrides. The post-process Deflicker plugin addresses
  *content-level* flicker (sensor noise, AC mains beat) which DNG
  metadata can't fix; per-frame metadata exposure and the plugin
  coexist non-overlappingly.
- File-level `ProfileToneCurve` and `OpcodeList3.GainMap` either are
  not read or pin to frame 1 — diagnostic swap test exists but does
  not change the v0.7 shape either way.

### v0.7 emission shape (resolved)

Given Q1.0's partial pass, v0.7 emits CinemaDNG with:

1. **Per-frame metadata for Holy Grail intent** — author per-frame
   `AsShotNeutral` (WB) and `BaselineExposure` (exposure) on every
   emitted DNG. Resolve honors these on debayer. Re-developability:
   full — user can override WB picker / exposure slider in Resolve's
   Camera Raw panel.
2. **Bayer-bake for time-varying mask-correction deltas** — LRT's
   per-region HG / Deflicker / Global per-frame scalar deltas apply as
   a multiplicative Bayer-plane gain *before* lossless-JPEG. Embedded
   DCP, ColorMatrix, ForwardMatrix, AsShotNeutral travel unchanged —
   no invented colour science.
3. **Sequence-averaged or dropped LR tone ops** — LR PV2012
   `ToneCurvePV2012` (per-frame) cannot ride as metadata (Q1.0 says
   `ProfileToneCurve` per-frame variation is not honored), cannot
   Bayer-bake (post-demosaic op). Two options ranked by preference:
   - **3a.** Approximate per-frame tone via `BaselineExposure` only;
     leave the LRT tone-curve shape as a sequence-averaged
     `ProfileToneCurve` baked into a per-sequence modified DCP. Drift
     measured against v0.6 pipeline output — gate at < 2 ΔE mean.
   - **3b.** Drop per-frame tone curves with a render-time warning,
     same pattern as v0.6 drops on `Highlights2012` / `Shadows2012` /
     `Whites2012`. Acceptable if 3a's drift exceeds the gate.

The fallback Bayer-bake section below documents method (2) in detail.

**Bayer-bake — applies to mask-correction deltas (and is the only
time-varying op that needs it):**

**Bayer-bake reference path (not invented from scratch):**

| Op | Bake method | Reference |
|---|---|---|
| Per-frame exposure delta | Multiply post-linearization Bayer values by `2^ΔEV`; rescale `WhiteLevel` tag by the same factor | DNG 1.7.1 spec §"Linearization and Black Levels"; precedent in slimRAW's `Set DNG WhiteLevel to this value` option ([slimRAW user guide](https://www.slimraw.com/userguide.html)) |
| Per-frame WB delta (Holy Grail K shift) | Multiply per-CFA-channel by `(AsShotNeutral_target / AsShotNeutral_source)`; preserve `AsShotNeutral` tag at the source value so a re-grade re-balances to neutral correctly | Adobe DNG 1.7.1 spec §"Camera Color Calibration" + RawTherapee `rtengine/rawimage.cc` channel-multiplier path (algorithmic reference, not code copy) |
| Per-frame mask-correction gain (LRT HG / Deflicker / Global) | Render LRT's per-region scalar delta into a per-frame Bayer-aligned gain plane, multiply in-place. Equivalent to applying `OpcodeList3.GainMap` ahead of time. **This is the only time-varying op that Bayer-bakes in v0.7.** | DNG 1.7.1 spec §"Opcode Definitions: GainMap" — we are simply pre-applying what the opcode would |

(Per-frame WB + exposure ride as metadata per Q1.0 verdict — see §"v0.7
emission shape" above; they do not appear in this Bayer-bake table.)
(Per-frame tone-curve / LR ops cannot Bayer-bake (post-demosaic). v0.7
ships option 3a sequence-averaged `ProfileToneCurve` if validation
gate < 2 ΔE; otherwise 3b drop with warning. See §"v0.7 emission
shape" above.)

Color-space fidelity guarantees of this approach:

1. Multiplications happen *post-linearization* (linear sensor space).
   The DNG `LinearizationTable` is **unchanged** — the Bayer-bake step
   sees data already mapped to linear by the source DNG's table.
2. `BlackLevel` and `WhiteLevel` tags are rescaled in lockstep with the
   pixel-level gain so the downstream demosaic / WB normalises to the
   same `[0, 1]` linear range as the source.
3. `ColorMatrix1/2`, `ForwardMatrix1/2`, `AsShotNeutral`, embedded DCP
   profile travel unchanged → Resolve's debayer + color science path is
   identical to its handling of any other CDNG. No invented color math.

What we *are* inventing: a Bayer-aligned per-pixel gain plane writer.
This is ~80 lines of NumPy (resample LRT mask region polygons → CFA
grid → multiply). The math is straightforward; the risk is
implementation bugs, not theoretical fidelity. Validation against the
v0.6 pipeline (which already implements the equivalent transform in
floating point) gives a per-pixel ground truth — gate at < 0.5 ΔE2000
mean on the gym + rose scenes.

Q1.0's partial-pass outcome resolves the spec to the "mostly full"
v0.7 documented above: Holy Grail intent rides as metadata; mask
deltas Bayer-bake; LR per-frame tone falls back to averaged or drop.

### Q1.1. Does Resolve 21 honor the embedded DCP fields on CinemaDNG import?

`AsShotNeutral`, `ColorMatrix1/2`, `ForwardMatrix1/2`,
`ProfileToneCurve`, `ProfileLookTableData` are all standard DNG tags;
Resolve's "Camera Raw" panel reads them as the per-clip default. We
need to confirm:

- Resolve 21 reads `ProfileLookTableData` (not just `HueSatMapData`,
  which is the per-WB-temperature variant) on CinemaDNG.
- Resolve 21 applies `OpcodeList3.GainMap` per-frame (not only at
  Stage 3 / final).
- Resolve 21 honors per-clip-overridden `AsShotNeutral` when set
  differently from the source DNG's value.

**Test path:** write one frame via the v0.7 prototype, ingest in
Resolve 21 with project Color Management = "DaVinci YRGB Color
Managed", set timeline color space = "Rec.2020 Linear", and read off
the Camera Raw decode. Compare to v0.6 pipeline's output frame ΔE2000.
Pass = < 1 ΔE mean.

If Resolve drops any of the above tags, fall back to the
**LookTable-only** strategy: bake everything (tone curve + LR ops +
mask deltas) into a single LookTable per frame, accept the
3D-LUT-sampling drift. Drift should still be sub-1 ΔE; verify before
committing.

### Q1.2. Does Resolve 21 (or 20) ingest DNG 1.7 JPEG XL?

Industry signal as of search date (May 2026): DNG 1.7 JXL is recognised
by Adobe Camera Raw 16+ and Adobe DNG Converter 16+. Resolve support
is **not confirmed**. If Resolve cannot decode JXL DNG, `cinema-cdng-jxl`
ships disabled-by-default and the JXL preset waits.

**Test path:** write one DNG 1.7 + JXL frame (via `pidng` 4.0.9+ if it
gains JXL by then, or via `libjxl` + `tinydng` C library wrapper, or
shell out to Adobe DNG Converter 16+ in CDNG mode), ingest in Resolve,
confirm decode. Pass = correctly decodes; fail = ship `cinema-cdng` only
and revisit JXL in v0.8.

### Q1.3. Writer library choice

None of the surveyed open-source DNG writers covers the full target
field set (DCP profile embed + OpcodeList3 + tone curve + JXL):

| Library | Bayer DNG | OpcodeList3 | Embed DCP | ProfileToneCurve | JXL | License |
|---|---|---|---|---|---|---|
| `pidng` 4.0.9 | yes | no | no | no | no | MIT |
| `dnglab` | yes (LJPEG) | no | no | no | no | LGPL-2.1 |
| `tinydng` | read+write | partial | no | partial | issue#42 open | MIT |
| direct `tifffile` + manual tag injection | possible | possible | possible | possible | yes (with libjxl) | BSD-3 |
| Adobe DNG Converter subprocess | full | yes | yes | yes | yes (DNG 1.7) | proprietary |

**Recommended path:** start from the already-cached Adobe-converted DNG
(it exists; the v0.6 `dng_convert.py` writes it to make pipeline.py's
< 1 ΔE work). It already contains correct `LinearizationTable`,
`WhiteLevel`, `BlackLevel`, `CFAPattern`, embedded `ColorMatrix1/2`,
`ForwardMatrix1/2`. v0.7's writer:

1. Reads the cached DNG via `tifffile` (DNG is TIFF + tags).
2. Injects per-frame tags: `AsShotNeutral`, `BaselineExposure`,
   `ProfileToneCurve`, `ProfileLookTableData`, `OpcodeList3` blob.
3. Re-emits via `tifffile.imwrite` with the sensor strip carried
   through unchanged (or re-compressed lossless-JPEG-92 if the source
   was uncompressed).

This keeps the writer surface small (~300 LOC) and reuses Adobe's own
DNG layout for correctness. License: tifffile is BSD-3, fine.

**Fallback:** if injecting `OpcodeList3` via tifffile proves
unreasonable (the SubIFD packing is non-trivial), shell out to
`exiftool` for the tag write step. exiftool covers all DNG opcode tags
and is widely packaged; license = Perl Artistic / GPL-1 (CLI invocation
only, no linkage).

### Q1.4. Per-frame opcode size

`OpcodeList3.GainMap` is a per-rectangle scalar-gain blob. LRT
mask-correction deltas are tens of bytes per frame. Tone curves are
~2 KB per frame (256 × float). Total per-frame metadata overhead
< 100 KB — negligible at compression ratios on the order of 10 MiB
per frame.

### Q1.5. Color-managed Resolve project setup

The emission format choice constrains the Resolve project setup. v0.7
will ship a `docs/RESOLVE_INGEST.md` covering:

- Project Color Science: DaVinci YRGB Color Managed
- Input Color Space (CDNG clip): Camera Raw (auto from DNG tags)
- Timeline Color Space: Rec.2020 Linear (matches v0.6 `cinema-linear`
  semantics) or ACES AP1 (matches v0.6 `cinema-aces`)
- Output Color Space: project-defined; not v0.7's concern

The user no longer chooses preset = "linear vs ACES" at *render* time
(v0.6 behaviour). The choice moves to Resolve's timeline color space.
This is the right place for it — the CDNG is the same file either way.

---

## 9. Validation gates

v0.7 ships only when all of the following pass:

1. **Sensor round-trip.** Read NEF → emit CDNG → decode via
   `dng_validate` → bit-exact match on the raw Bayer plane vs decoding
   the source DNG. Establishes lossless of the sensor data.
2. **Develop-intent fidelity.** ΔE2000 between
   `pipeline.render_frame(NEF, LRT XMP)` and `dng_validate(emitted CDNG)`
   on gym and rose test scenes. Target: **< 1.0 mean ΔE**. This is the
   bake-vs-metadata equivalence gate.
3. **Resolve ingest.** Same comparison but with Resolve 21 standing in
   for `dng_validate`. Capture via `resolve --headless` (CLI render) or
   manual session. Target: **< 1.5 mean ΔE** (allow 0.5 ΔE drift for
   Resolve's interpolation differences vs Adobe DNG SDK).
4. **Compression ratio.** Measure per-frame size on gym + rose +
   third scene (tungsten / fluorescent — already on the v0.6.x roadmap).
   Target: **≥ 10× vs v0.6 `cinema-linear`** mean across the three
   scenes.
5. **CLI / preset back-compat.** v0.6 `--preset cinema-linear` and
   `--preset cinema-aces` still produce identical output to v0.6 (they
   stay supported, deprecated-with-warning).

---

## 10. Implementation plan (phased)

Each phase is a self-contained PR. Order matters.

### Phase 1 — Verification spike (DONE 2026-05-27)

Spike completed. Results recorded in
[v07-resolve-cdng-spike-results.md](v07-resolve-cdng-spike-results.md).

Outcomes:

- **Q1.0:** partial pass. `AsShotNeutral` + `BaselineExposure` honored
  per-frame (T1, T2 pass). `ProfileToneCurve` + `OpcodeList3.GainMap`
  not honored per-frame (T3, T4 fail). Spec resolved to "mostly full"
  v0.7 shape — see §"v0.7 emission shape" in §8.
- **Q1.1:** subsumed by Q1.0 — `AsShotNeutral` override empirically
  works clip-level too (T1 confirms tag is read).
- **Q1.2 (JXL):** untested — depends on Q1.0 outcome which now dictates
  Phase 2 design. Defer JXL spike to Phase 2 once the writer scaffold
  exists; until then `cinema-cdng-jxl` preset is feature-flagged off.
- **Q1.3:** writer choice resolved as `tifffile` for tag injection +
  `exiftool` shell-out for `OpcodeList3` binary tags. exiftool's
  variable-length float tag write (T6 attempt) is unreliable against
  existing tags; `tifffile`'s direct IFD write is the durable path.

The throwaway spike code lives at `tools/v07_spike/inject_dng_tag.py`
and stays in-repo for documentation purposes; not promoted to `src/`.

### Phase 2 — `cdng_emit.py` writer module

- New module `src/lrt_cinema/cdng_emit.py` (~600–900 LOC with the
  Bayer-bake gain plane added on top of the original metadata writer).
- Inputs: cached Adobe-converted DNG path + per-frame `DevelopOps` +
  loaded `DCPProfile` + the sequence's averaged tone curve (computed
  once over the LRT XMPs).
- Per-frame work:
  1. Load source DNG via `tifffile`.
  2. Compute per-frame Bayer-plane gain plane from LRT mask-correction
     deltas; multiply Bayer values; rescale `WhiteLevel` tag in lockstep.
  3. Write per-frame `AsShotNeutral` and `BaselineExposure` tags
     (validated honoured by Resolve in Q1.0 T1+T2).
  4. Replace embedded DCP fields with the sequence-averaged
     `ProfileToneCurve` (one-shot, computed once outside the per-frame
     loop).
  5. Encode the Bayer strip with lossless JPEG-92.
  6. Write final DNG.
- Outputs: one `.dng` per frame, lexicographically named.
- No CLI surface yet — called from tests / pipeline-internal code.

Unit tests:

- Sensor round-trip with no LRT deltas applied (gate #1: bit-exact match
  on raw Bayer plane via `dng_validate` decode).
- Per-frame `AsShotNeutral` / `BaselineExposure` write produces the
  expected float bytes (read back via `tifffile`).
- Bayer-plane gain bake: synthetic constant-2× gain produces output
  pixels exactly 2× input, with `WhiteLevel` doubled (or clamped to
  the sensor's max-encodable, with overflow flagged).
- Sequence-averaged `ProfileToneCurve` matches the mean of per-frame
  LRT tone curves within 1 LSB.

### Phase 3 — preset wiring

- `output.py` gains a `write_cdng` dispatch branch.
- `presets/definitions.py` adds `cinema-cdng` (feature-flagged
  `cinema-cdng-jxl` deferred until Q1.2 spike runs in Phase 2.5).
- CLI passes through; no new flags.
- `cinema-linear` / `cinema-aces` continue to dispatch through the old
  TIFF/EXR writers (deprecation warning printed once per render).
- Render-time warnings surface the ops v0.7 drops or approximates:
  per-frame `ToneCurvePV2012` baked into sequence-averaged curve
  (warning on first frame of each sequence); LRT mask-correction
  deltas baked into Bayer plane (silent — no information loss in pixel
  space, only loss of Resolve-side override flexibility).

### Phase 4 — Resolve-vs-pipeline ΔE gate

- New test `tests/test_cdng_emit.py` (~150 LOC).
- Two gates:
  - **bake-vs-metadata (§9 gate #2):** runs via `dng_validate`; fully
    CI-able and shipped as a default pytest gate.
  - **ingest-vs-Resolve (§9 gate #3):** Resolve Free has no CLI render
    path — only Resolve **Studio** exposes the Python scripting API for
    headless renders. If Studio is available and licensed, the test
    runs via the scripting API and is marked
    `@pytest.mark.resolve_studio_required` + `skipif` when absent. If
    not, this is a **manual checkpoint** performed once per release by
    a human walking the recipe in `docs/RESOLVE_INGEST.md` and
    recording the measured ΔE in `docs/research/v07-resolve-gate.md`.
    Do not block CI on it.

### Phase 5 — docs + release

- `docs/RESOLVE_INGEST.md` ships the project-setup recipe.
- README + SCOPE.md updated to v0.7 preset table + new compression
  measurements.
- CHANGELOG entry covering the architectural shift (pipeline → validator,
  emission → CDNG).
- Tag `0.7.0a0`.

### Out of scope for v0.7

- AgX / `stills-finished` preset — remains on v0.6.x track.
- Multi-camera test scene coverage beyond gym + rose + the v0.6.x
  tungsten scene.
- Re-architecting `pipeline.py` to remove now-redundant stages. The
  pipeline keeps producing the reference frame for the ΔE gate; tidy-up
  PRs may follow in v0.7.x once the new emission path is proven.

---

## 11. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| ~~Resolve ingests a CDNG sequence with only frame-1's metadata (Q1.0)~~ | **resolved 2026-05-27** | partial: WB+exposure honored per-frame; tone curve + opcodes are clip-level | metadata path for Holy Grail intent; Bayer-bake for mask deltas; sequence-averaged tone curve |
| Resolve's bundled DCP library lacks the source camera (e.g. obscure body) | low for popular Nikon/Sony/Canon; medium for Fuji / Olympus / Phase One | medium — colour science falls back to Resolve's "generic" DCP, ΔE may exceed v0.6 baseline | Phase 4 ΔE gate; if a target camera regresses, document `Camera Raw → Decode Using = Project` workaround with explicit white-balance + color-space override |
| LRT tone curve shape lost in v0.7 emission | confirmed — T3 ignored entirely | medium — users who time-author tone curves get a flat decode | render-time warning; document workaround (apply tone shape as a Resolve power-grade node downstream) |
| `OpcodeList3.GainMap` per-frame not applied by Resolve | medium | high — LRT mask-correction deltas lost on import | spike in Phase 1; fall back to baking the deltas into `ProfileToneCurve` per-frame (lossy on extreme deltas) |
| Adobe DNG Converter path required for source DNG; Linux users excluded | low | low — already true in v0.6 | inherited from v0.6; document `--no-dng-convert` Linux fallback (~0.5 ΔE) |
| User's installed Resolve is < 21 and lacks any of (a) CDNG `ProfileLookTableData` honour, (b) `OpcodeList3.GainMap` honour | medium | medium — recipe degrades | document minimum Resolve version on README; consider emitting a sidecar `.cube` LUT for users on older versions |
| LRT XMP carries a develop op no DNG opcode can represent | low | medium | currently none observed; if encountered, add to the v0.6-style "dropped at render" list with a render-time warning |

---

## 12. Decision log (open)

These are decisions the spec defers to verification rather than fixing:

- **Lossless JPEG variant** — `LJPEG-92` (DNG mainstream) vs newer
  `JPEG-LS` (some writers support). Default: LJPEG-92, matches Adobe
  DNG Converter and 100% of Resolve-tested CDNG sources. Revisit if a
  measurable size or speed win shows up.
- **Per-frame DCP embed vs static** — embed the same DCP in every
  frame (~50 KB overhead each) vs once-per-sequence sidecar. Default:
  embed-every-frame for self-contained DNGs; revisit if the overhead
  matters at extreme frame counts (it does not at 24 MP / 30 frames).
- **Output filename scheme** — `{stem}.dng` preserves v0.6 lexicographic
  ordering. No change anticipated.

---

## 13. Acceptance / "done"

v0.7 is shippable when:

1. All five gates in §9 pass on the gym + rose + tungsten scenes.
2. `docs/RESOLVE_INGEST.md` exists and has been walked through by a
   human on a clean Resolve 21 install.
3. `CHANGELOG.md` records the architectural shift.
4. The next user who renders a 100-frame timelapse with v0.7 sees their
   output directory at 10–18× smaller than v0.6 produced.

Then v0.7.0 ships.

---

## See also

- [v06-architecture.md](v06-architecture.md) — the v0.6 in-process
  pipeline architecture (the validator path in v0.7).
- [dng-pipeline-findings.md](dng-pipeline-findings.md) — empirical
  journey establishing the < 1 ΔE2000 vs `dng_validate` ground-truth.
- [color-option-space-2026-05-26/](color-option-space-2026-05-26/) —
  v0.6.x AgX preset notes (untouched by v0.7).
- Adobe DNG 1.7.1 specification — `ProfileToneCurve`,
  `ProfileLookTableData`, `OpcodeList3.GainMap` reference fields.
- CinemaDNG 1.1 specification — frame sequencing semantics.
