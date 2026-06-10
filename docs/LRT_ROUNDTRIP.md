> **⚠️ Reference only (banner added 2026-06-10).** The round-trip described here
> (LRT ingests our 16-bit `LRT_*.tif` sequence) is the product's existential
> premise and has **never been demonstrated** — owner test pending (CLAIMS.md).

# The LRTimelapse round-trip (default emission)

`lrt-cinema`'s default target is the **LRTimelapse round-trip**: render the
LRT-developed frames to a TIFF sequence LRT can re-ingest, then go **back into
LRTimelapse** to render the video — where LRT's **Motion Blur** and final
deflicker live. This makes `lrt-cinema` a drop-in replacement for the *Lightroom
develop + TIFF-export* step, keeping you inside the LRT ecosystem without Adobe.

## Why TIFF (not EXR/DNG)

LRT's video renderer ("Render from Intermediate") accepts **JPG or TIFF only**
(Pro adds 16-bit/lossless TIFF). **EXR and DNG are rejected.** Our scene-linear
ACEScg EXR master (`cinema-linear-finished`) is for a *different* downstream —
DaVinci Resolve / ACES — and bypasses LRT entirely (no LRT Motion Blur). If you
want LRT's video stage, you need the TIFF. See [DECISIONS.md](DECISIONS.md).

## What the `lrtimelapse` preset emits

| Property | Value | Why |
|---|---|---|
| Container | TIFF | only re-ingestible format for LRT's renderer |
| Bit depth | 16-bit unsigned int | the point of replacing LRT's 8-bit internal path |
| Colour space | **sRGB** (Rec.709 primaries + sRGB OETF) | LRT-safe; Gunther Wegner's recommended display space |
| ICC profile | **embedded sRGB** | removes the colour/gamma-shift ambiguity that bites untagged/wide-gamut TIFFs |
| Range | full | a TIFF is full-range; legal-vs-full is chosen later in LRT's render dialog |
| Naming | `LRT_00001.tif`, `LRT_00002.tif`, … | LRT requires this exact 5-digit, 1-based, `LRT_`-prefixed pattern to recognise the folder |
| Look | full LRT develop baked (Stage 9 + develop ops) | this is the finished deliverable |

Metadata embedded per frame (ImageDescription, JSON): tool/version, colorspace,
primaries, transfer, range, bit_depth, source_frame, frame_index — so the file
is self-describing. No timestamps → byte-reproducible renders.

## Usage

```bash
lrt-cinema render \
  --input  /path/to/source-RAW-and-XMP-folder \
  --output /path/to/lrt-ready-tiff-sequence
# default --preset lrtimelapse. Render the WHOLE sequence (don't use
# --from-frame) so the folder starts at LRT_00001.tif.
```

## Manual LRT acceptance checkpoint (the only true in-bounds proof)

LRTimelapse exposes **no headless/CLI API**, so the automated tests prove only
**emission conformance** (naming, ICC, sRGB encode, 16-bit, dimensions, look).
They **cannot** prove LRT accepts the frames. That requires a human:

1. Render a sequence with the default preset (above). Confirm the output folder
   contains `LRT_00001.tif …` with no gaps.
2. In LRTimelapse, open the sequence's project. Use **Render from Intermediate**
   (the "blue" render dialog) and point it at the `LRT_*.tif` folder.
3. Enable **LRT Motion Blur** (frame-blend) and render to ProRes/H.264.
4. **Verify no colour/gamma shift**: the rendered video should match the look of
   the `lrt-cinema` frames (open one `LRT_*.tif` in a colour-managed viewer).
   If contrast/highlights look wrong, toggle **Full vs Legal Range** in the LRT
   render dialog — that mismatch is the usual culprit (per Wegner's guidance),
   not the TIFF itself.
5. Record the result (LRT version, OS, codec, range setting) in the project's
   emission-analysis notes.

## Sources

- [LRTimelapse — Export and Render](https://lrtimelapse.com/workflow/export-and-render/)
- [Forum: render video in LRT from external frames](https://forum.lrtimelapse.com/Thread-render-video-in-lrt-and-use-motion-blur-options-from-lr-generated-jpegs) — `LRT_00001` naming; JPG/TIFF only
- [Forum: colour/gamma shift, LR TIFFs vs LRT ProRes444](https://forum.lrtimelapse.com/Thread-having-trouble-rendering-accurately-colour-gamma-shift-lr-tiffs-vs-lrt-prores444) — Rec.709 safe; full/legal-range pitfall
