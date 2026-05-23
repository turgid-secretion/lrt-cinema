# Export & Render: Internal vs Lightroom paths

> Scope: the two "Export & Render" UI buttons, what each produces,
> what their bit-depth / color profile / format constraints are, and
> whether either is headless-invocable for our project's automation.
>
> Sources: LRT internal workflow tutorial, complete workflow tutorial,
> LRT 7 release post, LRT 7.5 release post, LRT 7.4.1 release notes,
> and search of the LRT forum for headless / CLI / scripting threads.

## "Export & Render (internal)"

LRT's all-in-one path: develop the intermediate frames inside LRT,
then assemble them into a video — all without launching Lightroom.

Documented constraints from
https://lrtimelapse.com/workflow/internal-workflow/:

> *"The internal export will always create 8 bit sRGB intermediary JPG
> files."*
>
> *"The quality will be inferior to the quality provided by the
> Lightroom Export via the LRTExport plugin."*

So the internal path is:

- **Intermediate sequence**: 8-bit sRGB JPG, always. No 16-bit option,
  no wider gamut, no linear-light option.
- **Final encoding**: video, codec/container selectable in the render
  dialog. The LRT 7 release post lists "*JPG Direct Rendering*" as a
  new option for skipping the intermediate-sequence step entirely
  (rendering video directly from the in-memory developed JPGs).
- **Speed**: faster than the LR path because no app-to-app handoff;
  the LRT 7 release post claims "*Proxy Buffering for Visual Previews
  → Speed increase up to 50% for deflicker and VP updates after the
  first generation.*"

## "Export & Render (Lightroom)"

Two-stage process bridging LRT → Lightroom Classic → LRT:

1. **LR side: develop and export intermediates.** User imports the
   sequence into LR (drag-and-drop the LRT icon to LR's Library), runs
   *Metadata → Read Metadata from Files* so LR ingests the LRT-written
   XMPs, then runs the bundled LRT export preset (e.g. "JPG (4k)") via
   the LRTExport plugin. From the LRT Complete tutorial: *"Select one
   of the presets, e.g. 'JPG (4k)', to create intermediate sequences."*
   The output is a folder of `LRT_*` prefixed images. With the right
   preset they can be 16-bit TIFFs at any size/profile LR supports
   (typical practice: 16-bit ProPhoto/AdobeRGB TIFF at 4K).
2. **LRT side: assemble video.** Per the tutorial: *"Lightroom
   automatically opens the render dialog in LRTimelapse after the
   export"* with the *Render pre-exported intermediate sequence* option
   pre-selected. LRT then encodes the LR-rendered frames into video.

The bit-depth and color profile of the intermediate sequence is the LR
plugin preset's responsibility; LRT does not constrain them on this
path.

## Comparison

| Property | Internal path | Lightroom path |
|---|---|---|
| Intermediate format | 8-bit sRGB JPG (fixed) | preset-dependent (typically 16-bit TIFF) |
| Color gamut | sRGB | preset-dependent (ProPhoto, AdobeRGB, etc.) |
| RAW pipeline | unspecified by LRT docs (see note below) | Adobe Camera Raw (LR's pipeline) |
| Adobe products required | bundled Adobe DNG Converter is present (preview-side, OBSERVED); whether the internal render also uses it is UNKNOWN | Lightroom Classic + LRTExport plugin |
| Headless / CLI | not documented | not documented (LR plugin is GUI-driven) |
| Speed | faster | slower (LR launch + LR develop time) |
| Quality (per Wegner) | "inferior" | "superior" |

Note on "Adobe code in the internal path": LRT bundles Adobe DNG
Converter and uses it to produce the `.lrt/proxy/*.proxy` files
(`OBSERVED 2026-05-22`, see PIPELINE.md and DISK_LAYOUT.md; the LRT
7.4.1 release notes' "Patch Tool to remove the Dock Icon from the
Adobe DNG Converter" item independently corroborates the bundling).
Whether the internal *render* path also routes raws through DNG
Converter, reads the existing proxies, or runs a separate decode is
not documented by LRT and not observable from our sample.
`STATUS: UNKNOWN.`

## Why our project replaces both paths

The project's goal (per SCOPE.md and README.md) is "high-quality
exports from LRTimelapse without Adobe products in the loop." The
two LRT-native paths' Adobe dependencies:

- **Internal path**: LRT bundles Adobe DNG Converter, which definitely
  runs as part of the preview-side workflow. Whether it runs on the
  render path is `STATUS: UNKNOWN`; either way, the bundle is shipped.
- **Lightroom path**: requires Lightroom Classic outright (Adobe
  Camera Raw on the critical path).

`lrt-cinema` substitutes `darktable-cli` for both. It reads LRT-written
XMP sidecars (the LRT-rendered nothing, only XMP-authored) and renders
the intermediate sequence with darktable's RCD/AMaZE demosaic +
darktable color pipeline. The final video encode step is out of scope
for v0.x (a `ffmpeg` shell is the obvious downstream).

## Is "Export & Render (Internal)" CLI / headless invocable?

`STATUS: UNKNOWN.` We searched the LRT forum and documentation for
"CLI", "AppleScript", "headless", "command-line", "automation". No
official surface area is documented. The LRTExport plugin's
plugin-side API (within LR) is open via standard Lightroom plugin
hooks, but LRT itself does not appear to expose an external API.

This matters because it removes one potential v0.4+ alternative for
our project: we cannot drive LRT's render pipeline from a script and
take its 8-bit JPG output as a baseline reference. Render references
would have to be captured manually with the LRT GUI in the loop.

## Format support evolution (release notes)

From the release announcements (cross-reference VERSION_HISTORY.md):

- LRT 7.0: "*JPG Direct Rendering option available*"; "*HDR support
  added in export plugin and rendering*"; "*JPGs supported the same
  way as Raw files, including Visual Previews and Visual Deflicker for
  JPG sequences*".
- LRT 7.5: "*Hardware-enabled ProRes encoding on Mac Silicon*";
  "*Native ffmpeg ARM encoder for Windows on ARM*"; "*Hasselblad
  *.fff raw file support added*"; "*Render from Intermediate and
  Create Composition buttons added to toolbar.*"

The ProRes-on-Apple-Silicon support is notable for cinema users: it
means LRT 7.5+ on M-series Macs can encode to a true 10-bit
4:2:2 / 4:4:4 codec at the video-encode stage, even though the
intermediate sequence on the internal path is still 8-bit sRGB JPG.
The intermediate is the bottleneck, not the codec.

## Provenance summary

| Claim | Source | Tag |
|---|---|---|
| Internal export always 8-bit sRGB JPG | LRT internal workflow tutorial | DOCUMENTED |
| Internal export quality "inferior" to LR | LRT internal workflow tutorial | DOCUMENTED (Wegner's words) |
| LR path uses Adobe Camera Raw pipeline via LRTExport plugin | LRT complete tutorial | DOCUMENTED |
| Drag-and-drop import for LR handoff | LRT complete tutorial | DOCUMENTED |
| LR automatically reopens LRT render dialog after export | LRT complete tutorial | DOCUMENTED |
| LRT 7 added JPG Direct Rendering | LRT 7 release post | DOCUMENTED |
| LRT 7 added HDR in export plugin | LRT 7 release post | DOCUMENTED |
| LRT 7.5 added ProRes hardware on Mac Silicon | LRT 7.5 release post | DOCUMENTED |
| LRT 7.5 added Hasselblad raw support | LRT 7.5 release post | DOCUMENTED |
| Headless / CLI / AppleScript external API | none documented | UNKNOWN — no observable evidence |
