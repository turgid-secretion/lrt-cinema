# LRTimelapse on-disk layout

> Scope: every file LRTimelapse writes into a sequence folder, what
> each contains, when each is generated and invalidated.
>
> Sources: empirical inspection of the user's LRT 7.5.3 sequence
> `/Volumes/SanDisk Extreme Pro 55AF Media/Projects/2026 international
> faire timelapse/` on 2026-05-22, plus the LRT tutorial pages that
> mention disk artifacts. File hashes recorded for reproducibility;
> claims tagged `OBSERVED` are auditable against those hashes.

## Sequence folder root

LRT does not relocate the source files. The sequence folder remains
under the user's control and contains the raw frames plus their
sidecars:

```
<sequence>/
  DSC_XXXX.NEF              # raw, untouched by LRT
  DSC_XXXX.xmp              # Adobe-shaped sidecar, written by LRT
  .lrt/                     # LRT cache directory (auto-created)
  .DS_Store                 # macOS Finder, not LRT
```

The per-frame `.xmp` files in the sequence root contain the *currently
active* develop state for each frame — what Lightroom (or any RAW
developer reading the sidecar) would pick up when the raw is
opened. LRT writes these whenever the user runs "Save Metadata" (an
explicit workflow step) or whenever LRT needs
to commit metadata as part of a workflow step (e.g. Auto Transition).
On a freshly initialized but not-yet-saved sequence, all per-frame
sidecars carry LR defaults plus `xmp:Rating="0"`. After Auto Transition,
all sidecars carry interpolated per-frame `crs:Exposure2012` (and any
other animated field). `OBSERVED 2026-05-22`: in this sample the
sequence-root XMPs carry `History stEvt:softwareAgent="LRTimelapse Pro
7.5.3 (Mac/ARM) - licensed to Dylan Johnston, "`.

## `.lrt/` cache directory

Top-level layout `OBSERVED 2026-05-22`:

```
.lrt/
  lrtsequence.json          # 955 bytes, sequence-wide state
  previews/                 # one .lrtpreview per source frame
  visual/                   # one .lrtpreview per keyframe (+ all frames after Visual Previews→All)
  proxy/                    # one .proxy per keyframe (+ a paired .xmp for some)
  .DS_Store
```

### `lrtsequence.json`

Plain-text JSON, the per-sequence project state. The user's file
(SHA-256 `891e3be51b32bee7d797f7ea357abfba99e099406bf7ff2e55bad19b86693a11`)
encodes workflow state flags, deflicker tuning, license string, image
count, and the absolute `sequencePath`. Representative fields:

| Field | User sample value | Meaning |
|---|---|---|
| `lrtVersion` / `lrtBuild` | `"7.5.3"` / `1053` | LRT version + numeric build |
| `imageCount` | `5033` | frames in the sequence |
| `workflowInitialized` | `1` | sequence has been initialized |
| `workflowKeyframes` | `1` | Keyframes Wizard has been run |
| `workflowAutoTransition` | `1` | Auto Transition has been run |
| `workflowVisualPreviews` | `0` | Visual Previews not yet generated for all frames |
| `workflowVisualDeflicker` | `0` | Visual Deflicker not yet run |
| `workflowHolyGrailWizard` | `-1` | Holy Grail Wizard not applicable for this sequence |
| `workflowFinished` | `0` | sequence not finalized |
| `deflickerSmoothingFactor` | `0.125` | smoothing for the deflicker algorithm |
| `deflickerPasses` | `2` | multi-pass deflicker count |
| `deflickerAccuracy` | `0` | accuracy threshold |
| `holyGrailReduceDeflicker` | `0.0` | HG-related deflicker dampening |
| `proxies` | `0` | proxies generated flag (note: `.lrt/proxy/` exists despite flag=0 in this sample) |
| `rating` | `0` | display-filter rating threshold |
| `license` | `"LRTimelapse Pro 7.5.3 (Mac/ARM) - licensed to Dylan Johnston, "` | embedded license string |
| `sequencePath` | absolute path | LRT cannot resolve the sequence under a different mount point without editing this |

`OBSERVED 2026-05-22; not documented anywhere in the LRT manual.` The
field names are self-describing; semantics are inferred from defaults
and from the LRT UI.

### `.lrt/previews/*.lrtpreview`

JPEG/JFIF baseline images, 640×424, YCbCr 4:2:0, 8-bit, no EXIF, no
ICC profile, no XMP packet. One file per source frame. The user's
sample folder contains 5033 of them (matching `imageCount`).

`OBSERVED 2026-05-22`: `DSC_4053.lrtpreview` is SHA-256
`8a59e28cf8e300fb401bcf23de988d4cb4a0ee0ff7f38376a8fde8f6070a149b`,
18 KB. File regenerated when the underlying XMP changes; full directory
is re-created on Initialize. See PREVIEW_GENERATION.md for the
color-space discussion.

### `.lrt/visual/*.lrtpreview`

Same JPEG/JFIF format as `previews/` but 1024×684 (~2.5× the area).
One file per keyframe initially; expanded to all frames after the
Visual Previews → All Frames workflow step. The user's sample contains
6 files (DSC_4053, 5059, 6065, 7071, 8077, 9085) corresponding to the
6 LRT keyframes.

`OBSERVED 2026-05-22`: `DSC_4053.lrtpreview` (visual) is SHA-256
`bd830efe1ef632b7575787d2c7feca8b9883196b47df7ee416f15a6cadcc4cd6`,
38 KB. Same encoding profile as the 640-wide previews — no metadata.

### `.lrt/proxy/`

Mixed-content directory. On the user's sample:

```
.lrt/proxy/
  DSC_4053.proxy    # DNG, 452 KB
  DSC_5059.proxy    # DNG, 555 KB
  DSC_5059.xmp      # 22460 bytes
  DSC_6065.proxy    # DNG, 575 KB
  DSC_6065.xmp      # 22460 bytes
  DSC_7071.proxy    # DNG, 581 KB
  DSC_8077.proxy    # DNG, 604 KB
  DSC_9085.proxy    # DNG, 565 KB
```

Two surprises here, both `OBSERVED, NOT DOCUMENTED`:

1. **The `.proxy` files are DNG 1.4 with `Software` EXIF tag set to
   "Adobe DNG Converter 18.2.2 (Macintosh)".** LRT bundles Adobe DNG
   Converter and uses it to render the linear-raw proxies, embedding a
   1024×684 preview and a 256×171 thumbnail. See PIPELINE.md for the
   architectural implication.
2. **Only 3 of 6 proxy files have a paired `.xmp` sidecar** (5059,
   6065, 7071) despite all 6 keyframes having `.proxy`. Plausible
   explanations: the paired XMPs are written only when LRT performs a
   write-back operation against the keyframe, or only the most recently
   touched keyframes carry sidecars. `STATUS: UNKNOWN, not
   documented.` The two paired XMPs that exist are byte-identical in
   size (22460 bytes) and carry identical `lrt:*` field sets.

`OBSERVED 2026-05-22`: `DSC_4053.proxy` is SHA-256
`36acc417f009671c7bfc1b02ad1b51f742880fa88771143a5436b8f227c29d48`,
452 KB. `DSC_5059.xmp` is SHA-256
`9219ec3971e713b80abbf599d81f3e76105b3c56c97326180e4b442ba491b29e`.

### What does `.proxy/*.xmp` carry that the sequence-root XMPs don't?

The `.lrt/proxy/*.xmp` files are the LRT-side authoritative develop
state for the keyframes — what gets edited when the user works on a
keyframe in LRT's internal editor. They carry:

- Full Adobe CRS schema (every `crs:*` field LR would write).
- Full `lrt:*` namespace (Aperture, Iso, ShutterSpeed, Width, Height,
  Quality, ShootingMode, IsMergedHDR — extracted from EXIF and frozen
  for downstream reference).
- The complete `crs:MaskGroupBasedCorrections` rdf:Seq with **9
  CorrectionName entries**: `LRT Mask 1`–`LRT Mask 4`, then the three
  internal-use names (`#LRT internal use (HG)`,
  `#LRT internal use (Deflicker)`, `#LRT internal use (Global)`), then
  `LRT Mask 5`–`LRT Mask 6`. See XMP_SCHEMA.md for the per-correction
  attribute table.

The sequence-root XMPs (`<sequence>/DSC_XXXX.xmp`) are the LR-facing
authoritative state — they go in the drag-and-drop bundle to
Lightroom. The two diverge until the user runs "Save Metadata", which
synchronizes them.

## Cache invalidation: what triggers regeneration

`STATUS: UNKNOWN.` The LRT documentation does not enumerate cache
invalidation triggers. `OBSERVED` from the sample: regenerating Visual
Previews appears to update `.lrt/visual/` mtimes; running Auto
Transition appears to leave `.lrt/previews/` untouched (Visual
Previews must be re-run separately to refresh them). Anything beyond
this would be inference; we do not have a controlled-experiment record
to cite.

## What our project depends on from this layout

- We read `<sequence>/*.xmp` (sequence-root, LR-facing) as our XMP
  parser input. We do *not* read `.lrt/proxy/*.xmp`.
- We never write to `.lrt/`, and we never modify the source sequence
  folder. Our rendered output (the TIFF/EXR sequence) goes in a
  separate output directory by design; the in-process renderer emits no
  `.xmp` sidecars of its own.
- The `lrtsequence.json` license / version string is the canonical
  signal of which LRT generated the sequence. We could surface this in
  `lrt-cinema inspect` as a future enhancement; we do not currently.
