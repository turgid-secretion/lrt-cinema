# Spike Protocol: Resolve per-frame CDNG metadata honor

**Status:** Pre-spec gate — must complete before any v0.7 code lands.
**Parent:** [v07-emission-format.md](v07-emission-format.md) §8 Q1.0.
**Owner:** TBD — requires DaVinci Resolve 21 install (Free or Studio
both acceptable for this spike; gate test #4 needs Studio if CI-able is
desired).

---

## Why this spike exists

The v0.7 emission-format proposal relies on DaVinci Resolve reading
**per-frame** DNG metadata across a CinemaDNG sequence (different
`AsShotNeutral`, `BaselineExposure`, `ProfileToneCurve`, and
`OpcodeList3.GainMap` per frame). Resolve's manuals are silent on
this. Adjacent products (LRTimelapse CDNG export, slimRAW per-frame
develop) do not ship the feature. Blackmagic forum threads on the
topic are not retrievable via web fetch (403). The behavior is
**unanswerable from public documentation as of 2026-05-27** and must be
verified empirically.

Outcome of the spike decides which v0.7 product ships:

- **Full v0.7** (Q1.0 pass): per-frame develop intent rides as DNG
  metadata; Resolve users get full per-frame override flexibility.
- **Narrow v0.7** (Q1.0 fail): time-varying ops baked into the Bayer
  plane before lossless-JPEG; only static develop overridable in
  Resolve.

---

## Inputs

1. **Source DNG.** A single Adobe-converted DNG produced from one of the
   project's test NEFs (`tests/fixtures/raw/sample.NEF` →
   `dng_convert.py`).
2. **Resolve 21** install (macOS or Windows). Project Color Management:
   "DaVinci YRGB Color Managed"; Timeline Color Space: "Rec.2020
   Linear"; CinemaDNG Decode Using: "Camera Metadata" (default).
3. **Spike writer.** Throwaway Python (under `tools/v07_spike/`),
   ~150 LOC. Reads the source DNG via `tifffile`, mutates one or two
   tags, writes a numbered copy. **No production code at this stage.**

---

## Test matrix

For each test below, generate a 2-frame sequence `frame_0001.dng` /
`frame_0002.dng` where the two frames differ in exactly one tag
class. Ingest into Resolve as a CinemaDNG clip (drag both into the
Media Pool together; Resolve auto-recognises the sequence). Inspect
the Camera Raw decode of each frame:

| Test | Frame 1 | Frame 2 | Pass criterion |
|---|---|---|---|
| **T1: AsShotNeutral** | `AsShotNeutral` = daylight value (e.g. `0.5, 1.0, 0.7`) | `AsShotNeutral` = tungsten value (e.g. `0.4, 1.0, 1.2`) | Frame 1 decodes daylight-balanced, Frame 2 decodes tungsten-balanced |
| **T2: BaselineExposure** | `BaselineExposure` = 0 | `BaselineExposure` = +2.0 | Frame 2 appears 2 stops brighter than Frame 1 (visible histogram shift) |
| **T3: ProfileToneCurve** | linear `[0,0, 1,1]` tone curve | aggressive S-curve via 256-point `ProfileToneCurve` | Frame 2 shows the S-curve contrast; Frame 1 stays linear |
| **T4: OpcodeList3.GainMap** | no opcode | `OpcodeList3.GainMap` = uniform 2× gain blob | Frame 2 shows 2× brightness uniform |
| **T5: ColorMatrix2** | original `ColorMatrix2` from source DNG | `ColorMatrix2` rotated 30° in hue (synthetic) | Frame 2 decodes with shifted color cast |
| **T6: ProfileLookTableData** | no `ProfileLookTableData` | `ProfileLookTableData` = synthetic 6³ HSV cube applying +20 saturation | Frame 2 visibly more saturated |

Each result is a binary pass/fail. Record all six in the spike report.

---

## Tooling

`tools/v07_spike/inject_dng_tag.py` (throwaway, ~50 LOC):

```python
# Pseudocode — actual implementation lives only on the spike branch
import tifffile
with tifffile.TiffFile(src) as f:
    pages = list(f.pages)
# rewrite with mutated tag via tifffile.imwrite or exiftool subprocess
```

For tags `tifffile` does not natively write (e.g. `OpcodeList3` is a
DNG-private binary blob), shell out to `exiftool -OpcodeList3<=blob.bin
out.dng` — `exiftool` covers every DNG tag.

Resolve inspection:

- **Free Resolve:** open the Color page, select each frame, screenshot
  the Camera Raw panel + viewer. Compare visually.
- **Studio Resolve:** drive via the DaVinci scripting API
  (`DaVinciResolveScript`); render each frame to a 16-bit TIFF and
  compare pixel-wise.

Spike concludes when all six tests have pass/fail recorded.

---

## Outputs

A new doc `docs/research/v07-resolve-cdng-spike-results.md` containing:

1. Resolve build number, OS, project settings.
2. Per-test pass/fail + a screenshot per frame.
3. Aggregate verdict: which tag classes are per-frame, which are
   clip-level.
4. Recommendation: full v0.7 vs narrow v0.7, and which tag mapping table
   from [v07-emission-format.md](v07-emission-format.md) §7 is still
   feasible.

The results doc unblocks Phase 2 of the implementation plan.

---

## Pessimistic forecast (writer prep)

Given that the adjacent industry has no shipping per-frame CDNG
develop-intent encoder, the realistic prior on Q1.0 is **maybe 40%**
that Resolve honors per-frame `AsShotNeutral` + `BaselineExposure` +
`OpcodeList3.GainMap` uniformly. Higher prior (~70%) that *at least*
per-frame `BaselineExposure` is honored (since BMD's own deflicker
metadata story implies this). Lower prior (~25%) on `ProfileToneCurve`
and `ProfileLookTableData` honored per-frame.

Therefore: prepare the writer with the assumption that **some** ops bake
into Bayer (narrow v0.7) and **some** ride as metadata (mostly the
static DCP fields). The full v0.7 is the upside case; the narrow v0.7
is the floor product.

---

## See also

- [v07-emission-format.md](v07-emission-format.md) §7 (LRT → DNG
  mapping table) and §8 (open questions).
- DNG 1.7.1 spec, §"OpcodeList3" + §"Camera Color Calibration".
- DaVinci Resolve 18.6 reference manual, "Master Settings → Camera
  RAW" (silent on per-frame but provides the project-level decode
  modes).
- [slimRAW user guide](https://www.slimraw.com/userguide.html) — closest
  shipping precedent for Bayer-side mutations on CDNG.
