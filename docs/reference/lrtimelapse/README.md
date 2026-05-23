# LRTimelapse reference

Authoritative reference for the LRTimelapse behaviors lrt-cinema
depends on. Source-anchored to LRTimelapse Pro 7.5.3 (Mac/ARM,
build 1053), validated 2026-05-22 against the user's sample
sequence at
`/Volumes/SanDisk Extreme Pro 55AF Media/Projects/2026 international
faire timelapse/`.

LRT is closed-source commercial software. Our source mix differs from
the darktable reference (`docs/reference/darktable/`) accordingly:

- **First**: official LRT manual + tutorials + Wegner's posts (the
  closest thing to a primary spec).
- **Second**: empirical observation of LRT's on-disk artifacts.
- **Third**: explicit `STATUS: UNKNOWN` markers wherever neither
  source path resolves a question. Do not infer; do not extrapolate
  from related systems.

Every claim in any other repo doc that describes LRT behavior should
be sourceable to one of the files below. If a claim cannot be traced,
the claim is provisional pending source verification or empirical
audit.

## Contents

- [`PIPELINE.md`](PIPELINE.md) — LRT's two-path architecture
  (Internal vs Lightroom render), the LRT↔LR communication channel
  (XMP + drag-and-drop, *not* programmatic), the preview-file
  pipeline, and the bombshell observation that the `.lrt/proxy/*.proxy`
  files are produced by *bundled Adobe DNG Converter 18.2.2*, not by
  LRT-native RAW code.
- [`DISK_LAYOUT.md`](DISK_LAYOUT.md) — every file LRT writes into a
  sequence folder, with SHA-256 hashes for the user's sample
  artifacts. Includes the `lrtsequence.json` field table and the
  observed `.lrt/proxy/` mixed-content directory.
- [`PREVIEW_GENERATION.md`](PREVIEW_GENERATION.md) — exact JPEG
  format of `.lrtpreview` files: JFIF baseline, no EXIF, no ICC, no
  XMP, YCbCr 4:2:0 8-bit, sRGB-by-convention. Documents which
  develop-pipeline settings the preview honors (and which are
  `UNKNOWN`).
- [`XMP_SCHEMA.md`](XMP_SCHEMA.md) — LRT namespace URI
  `http://lrtimelapse.com/` (no `ns/1.0/` suffix), the observed
  `lrt:*` attribute set, `xmp:Rating` keyframe convention, and the
  9-element `crs:MaskGroupBasedCorrections` rdf:Seq with Wegner's
  forum-quoted explanation of `#LRT internal use (HG)` /
  `(Deflicker)` / `(Global)`.
- [`AUTO_TRANSITION.md`](AUTO_TRANSITION.md) — what Auto Transition
  writes to disk (per-frame XMPs with interpolated values), the
  observed asymmetric-around-keyframe interpolation curve shape, and
  the genuine `STATUS: UNKNOWN` for the specific spline algorithm.
- [`VISUAL_WORKFLOW.md`](VISUAL_WORKFLOW.md) — Visual Previews,
  Visual Deflicker, Holy Grail Wizard — pre-conditions, disk outputs,
  and step ordering. Confirms the "Visual *" branding means
  *LRT-internal, no-LR-required*.
- [`EXPORT_PATHS.md`](EXPORT_PATHS.md) — "Export & Render (internal)"
  is 8-bit sRGB JPG, always. "Export & Render (Lightroom)" hands off
  via the LRTExport plugin. Neither path has documented headless /
  CLI surface area.
- [`VERSION_HISTORY.md`](VERSION_HISTORY.md) — LRT 7.0 / 7.4.1 / 7.5
  release-notes deltas relevant to our pipeline. Includes the
  "Adobe DNG Converter Dock Icon Patch Tool" item that incidentally
  confirms DNG Converter is bundled.

## Project context

lrt-cinema is a CLI that translates LRT-written XMP sidecars into a
darktable history stack and renders via `darktable-cli`. Our value-add
is high-quality cinema-format intermediates from an LRT workflow
*without Adobe products on the critical path*. We depend on LRT for:

- XMP-authoring: keyframe rating, Auto Transition interpolation,
  Visual Deflicker exposure-delta computation, Holy Grail compensation.
- Sequence-state management: `.lrt/lrtsequence.json`.

We deliberately do not depend on:

- LRT's preview pipeline (we are not the consumer; we substitute
  darktable's).
- LRT's internal render path (we replace it with `darktable-cli` for
  the high-quality intermediate sequence).
- LRT's bundled Adobe DNG Converter (we read raws directly via dt's
  RawSpeed / LibRaw).

## Notable refutations and closed gaps from prior project assumptions

This reference was created in part to verify claims the lrt-cinema
codebase had been operating on. Several do not survive source +
empirical inspection:

1. **"LRT's preview pipeline almost certainly applies LR's lens
   correction."** Unsupported. LRT documentation is silent on whether
   the preview pipeline reads `crs:LensProfileEnable`. Our sample's
   keyframes all carry `LensProfileEnable="0"`, so the sample cannot
   empirically discriminate either way. Treat as `STATUS: UNKNOWN`
   (PREVIEW_GENERATION.md), not as "yes, applied."

2. **"The `.proxy` files are TIFFs."** Wrong format identification.
   They are DNG 1.4 files written by Adobe DNG Converter 18.2.2
   (Macintosh), with the `.proxy` extension. Discovered by `exiftool`
   reporting `File Type: DNG`. See DISK_LAYOUT.md and PIPELINE.md
   — this changes our understanding of LRT's architecture (it is not
   homebrew demosaic; it bundles Adobe code).

3. **"LRT's Auto Transition uses a specific named spline."** Not
   sourced. The LRT documentation does not name the algorithm. Our
   empirical observation (commit `bf89107`) establishes only that the
   interpolation is non-linear and asymmetric around keyframes —
   consistent with several spline families. Calling it "Catmull-Rom"
   or "Hermite" specifically is `STATUS: UNKNOWN` (AUTO_TRANSITION.md).

4. **"The `lrt:*` namespace URI is `http://lrtimelapse.com/ns/1.0/`."**
   Wrong. The real URI is the bare base `http://lrtimelapse.com/` (no
   version path), validated against LRT 7.5.3 output and locked into
   the parser in commit `2ae63da`. Our earliest synthetic fixtures
   used the wrong form (XMP_SCHEMA.md).

5. **"LRT writes a `lrt:HolyGrailRamps` element."** Wrong. The Holy
   Grail compensation is encoded as a named entry inside
   `crs:MaskGroupBasedCorrections` with `CorrectionName="#LRT internal
   use (HG)"`, carrying `crs:LocalExposure2012` deltas. Documented by
   Wegner on the LRT forum ("masks marked as 'for internal use' are
   needed internally by LRTimelapse"). Our parser does not yet
   consume the mask-encoded form — calibration item (SCOPE.md).

## Genuine documentation gaps remaining open

These are flagged for future work or for direct outreach to Wegner if
the project decides to engage him:

- Specific spline algorithm used by Auto Transition.
- Whether LRT's preview pipeline honors `crs:LensProfileEnable`,
  `crs:HasCrop`, and varying `crs:ToneCurvePV2012` between keyframes.
- Whether LRT's "Export & Render (internal)" path uses the bundled
  Adobe DNG Converter, or runs a separate RAW decode (the bundle is
  used preview-side, OBSERVED; render-side behavior is unobserved).
- Cache invalidation rules for `.lrt/` (which user actions trigger
  regeneration of which artifact).
- JPEG quality factor used by LRT's preview encoder.
- Behavior of Auto Transition when adjacent keyframes have different
  categorical `crs:*` values (e.g. different `CameraProfile` strings).
- Headless / CLI / scripting surface area of LRT.
- Free vs Private vs Pro tier XMP schema differences (if any).
- Pre-LRT-7 XMP schema differences (if any).

## Source-priority discipline applied

In writing this reference we used the following order, only consulting
a lower-priority source when higher-priority sources were silent:

1. https://lrtimelapse.com/manual/ — landed 404 on our fetch; the
   site appears to have restructured the manual landing into the
   tutorial / workflow pages below.
2. https://lrtimelapse.com/workflow/visual-workflow/
3. https://lrtimelapse.com/workflow/internal-workflow/
4. https://lrtimelapse.com/tutorial/basic/complete/
5. https://lrtimelapse.com/tutorial/basic/internal/
6. https://lrtimelapse.com/tutorial/expert/
7. https://lrtimelapse.com/news/ (release announcement archive)
8. https://lrtimelapse.com/news/lrtimelapse-7/
9. https://lrtimelapse.com/news/lrtimelapse-7-4-1-available/
10. https://lrtimelapse.com/updates/lrtimelapse-7-5/
11. Wegner forum posts on
    https://forum.lrtimelapse.com (specifically the "useless masks
    when I drag and drop" thread for the mask-correction explanation).
12. Empirical observation of disk artifacts under
    `/Volumes/SanDisk Extreme Pro 55AF Media/Projects/2026 international
    faire timelapse/.lrt/` and `<sequence>/*.xmp`, using `exiftool`,
    `identify -verbose`, `file`, and `shasum -a 256`.
13. NOT used as primary sources: third-party tutorials, Reddit,
    general photography blogs.
