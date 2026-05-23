# LRTimelapse version history relevant to this project

> Scope: LRT release history with emphasis on changes that could
> affect XMP schema, preview pipeline, render output, or this
> project's runtime assumptions.
>
> Sources: LRT news index (https://lrtimelapse.com/news/), individual
> release-post pages for 7.0 / 7.4.1 / 7.5, and search of the LRT
> forum / blog for migration notes. Many LRT release posts are
> short-form user-facing announcements without a structured changelog;
> empirical claims below are tagged accordingly.

## Pro vs Standard tier

LRT is sold in three tiers (Free, Private, Pro). The license string
embedded in `lrtsequence.json` distinguishes them — the user's sample
carries `"LRTimelapse Pro 7.5.3 (Mac/ARM) - licensed to Dylan
Johnston, "`. `STATUS: UNKNOWN whether Free / Private tiers write a
different XMP schema or omit specific `lrt:*` fields.` Our parser
should be considered validated against Pro 7.5.3 only.

## Major-version timeline (in this project's relevance window)

LRT 6.x → 7.0 is the most significant version transition that's
adjacent to our sample. We did not deeply audit pre-7 schemas because
our project targets current LRT.

### LRT 7.0 — release 2023-ish

Quoting the release post
(https://lrtimelapse.com/news/lrtimelapse-7/) verbatim where possible:

- **Visual Workflow framing introduced**: the entire "Visual *" naming
  convention dates from this release.
- **"Proxy Buffering for Visual Previews → Speed increase up to 50%
  for deflicker and VP updates after the first generation."**
- **"JPGs will now be supported the same way as Raw files by
  LRTimelapse, this includes Visual Previews and Visual Deflicker for
  JPG sequences."**
- **HDR support**: "*HDR support added in export plugin and rendering.
  HDR sequences marked with yellow circle in workflow indicator.*"
- **Visual cropping**: "*Shift-Dragging in the preview sets a crop.*"
- **Holy Grail Wizard Optimize**: "*new 'Optimize' feature for
  automatic slider optimization.*"
- **JPG Direct Rendering**: new render-pipeline option (skips
  intermediate sequence on internal path).
- **UI rewrite**: "*Redesigned interface with modern appearance*"; SVG
  icon system replaced raster icons.

No explicit XMP-schema changes are mentioned. The HDR support
introduces the `lrt:IsMergedHDR` field (observed in 7.5.3 sample), but
the release post does not call this out.

### LRT 7.1 — release date not extracted

https://lrtimelapse.com/news/lrtimelapse-7-1-released/ exists on the
news index but we did not fetch the body for this reference. `STATUS:
NOT EXTRACTED — recommended future verification.`

### LRT 7.4.1 — 2025-08-29

Release post: https://lrtimelapse.com/news/lrtimelapse-7-4-1-available/.
Verbatim items relevant to this project:

- **"Auto Transition with 2 Keyframes functionality corrected."** —
  Behavior change in the Auto Transition step for the 2-keyframe edge
  case. See AUTO_TRANSITION.md.
- **"Implemented a Patch Tool to remove the Dock Icon from the Adobe
  DNG Converter."** — Confirms LRT bundles and invokes Adobe DNG
  Converter, supporting the empirical PIPELINE.md observation that
  `.lrt/proxy/*.proxy` files are DNG-Converter output.
- **"Added fallback for Canon CR3 image orientation loading via
  ExifTool."** — Implies LRT uses ExifTool as a fallback metadata
  reader (interesting from a "what binaries does LRT bundle"
  perspective, less directly relevant to our pipeline).
- **"Transitions refactored."** — Vague, no detail on what changed.
  `STATUS: UNKNOWN whether the spline algorithm was modified.`
- **"Parse Exception errors on n/a intervals resolved."** — bug fix.
- **"Image Date/Time formatting issues (2025-07-07 08:59:07 format)
  corrected."** — bug fix.

### LRT 7.5 — 2026-03-08

Release post: https://lrtimelapse.com/updates/lrtimelapse-7-5/.
Verbatim items relevant to this project:

- **"Improved high accuracy Deflicker."** — Behavior change in the
  deflicker algorithm. Specifics not documented. `STATUS: NOTED.`
- **"Faster Visual Preview recalculation after Auto Transition."**
- **"Hardware-enabled ProRes encoding on Mac Silicon."** — final-
  encode codec support, not relevant to XMP schema but relevant to
  EXPORT_PATHS.md.
- **"Native ffmpeg ARM encoder for Windows on ARM."**
- **"Hasselblad *.fff raw file support added."** — raw decoder
  support widened.
- **"LRT Sync AI Tools" preset added for syncing AI tools to
  sequences.** — LR-side preset, not LRT-internal.
- **"Render from Intermediate" and "Create Composition" buttons
  added to toolbar.**
- **Settings option to automatically create evenly-spaced keyframes
  on initialization.**

### LRT 7.5.3 — current (Mac/ARM, validated 2026-05-22)

No standalone release post body extracted; release indicated in
news-page sidebar. This is the version that produced all empirical
observations in this reference. Build number `1053`
(`OBSERVED 2026-05-22` from `lrtsequence.json`).

## What we know does *not* change between versions

`STATUS: ASSUMED CONSTANT` (no observable evidence of change in our
limited cross-version exposure):

- LRT namespace URI `http://lrtimelapse.com/` — has remained the
  bare-base form across 6.x and 7.x per the forum posts we found.
- `xmp:Rating` semantics for keyframes.
- The `crs:MaskGroupBasedCorrections` schema for `#LRT internal use
  (HG)` / `(Deflicker)` / `(Global)`.

## What we know *did* change

`OBSERVED` from cross-referencing forum threads and release notes:

- The mask format changed when Lightroom 11 introduced "Masks 2.0"
  (was a major LR-side architecture change to local corrections). LRT
  followed; its `crs:MaskGroupBasedCorrectionMask` `MaskVersion` field
  is `"2"` in current 7.5.3 output (`OBSERVED 2026-05-22` in the
  proxy XMP). Older LRT-emitted XMPs against the old mask format would
  carry `MaskVersion="1"` or no `MaskVersion` at all. Our parser does
  not currently inspect `MaskVersion`; this could matter if a user
  brings forward an old sequence.
- Visual Workflow naming and Proxy Buffering are 7.0+ features. We
  have no evidence the pre-7 XMP schema differs structurally from the
  7.x schema, but we have not validated against a pre-7 sample.

## What our project assumes about LRT version

`SCOPE.md` flags compatibility with "LRTimelapse Pro 7.5.3" explicitly
in the validated-schema-items section. Earlier 7.x releases (7.0–7.4)
*probably* work with our parser given the apparent schema stability,
but this has not been tested. Pre-7 releases are unverified.

## Provenance summary

| Claim | Source | Tag |
|---|---|---|
| LRT 7.0 added Visual Workflow framing | LRT 7 release post | DOCUMENTED |
| LRT 7.0 added Proxy Buffering, JPG Direct Rendering, HDR support | LRT 7 release post | DOCUMENTED |
| LRT 7.4.1 fixed 2-keyframe Auto Transition edge case | LRT 7.4.1 release notes | DOCUMENTED |
| LRT 7.4.1 confirms Adobe DNG Converter is bundled | LRT 7.4.1 release notes (Patch Tool item) | DOCUMENTED |
| LRT 7.5 improved high-accuracy Deflicker | LRT 7.5 release notes | DOCUMENTED |
| LRT 7.5 added Hasselblad fff support, ProRes hardware encoding, Mac Silicon native encoding | LRT 7.5 release notes | DOCUMENTED |
| `lrt:*` namespace URI unchanged across versions | inferred from forum continuity | INFERRED |
| `crs:MaskGroupBasedCorrections` schema stable in 7.x | inferred | INFERRED |
| Pro vs Standard vs Free XMP differences | not documented | UNKNOWN |
| Pre-7 XMP schema differences | not investigated | UNKNOWN |
