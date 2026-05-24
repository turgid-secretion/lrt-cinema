# Darktable reference

Authoritative reference for the darktable behaviors lrt-cinema
depends on. Citations are source-anchored to the dt master commit
`9402c65275bebebc4649c6dc91d3798d4bd63a0f` (refresh the SHA when
significant changes land; current snapshot fetched 2026-05).

Every claim in any other repo doc that describes darktable behavior
should be sourceable to one of these files. If a claim cannot be
traced here, the claim is provisional pending source verification.

## Contents

- [`PIPELINE.md`](PIPELINE.md) — module execution order, the v50
  iop_order table that became default in dt 5.0, the scene-referred
  vs display-referred split, and which modules darktable auto-enables
  on a fresh raw image.
- [`MODULES.md`](MODULES.md) — per-module spec for the 15 modules
  lrt-cinema reads from, emits to, or considers: introspection
  version (`modversion`) at master, parameter struct layout, source
  file + line range, and Lightroom equivalent (if any).
- [`XMP_FORMAT.md`](XMP_FORMAT.md) — sidecar schema: xpacket
  wrapper, `darktable:xmp_version` semantics (a project assumption
  is refuted here), history `rdf:Seq` shape, per-entry attribute
  table, masks layout, and the `auto_presets_applied` flag.
- [`STYLES.md`](STYLES.md) — `.style` XML file format, version
  compatibility, and the interaction between `--style` /
  `--style-overwrite` and per-frame XMP sidecars.
- [`EXPORT.md`](EXPORT.md) — `darktable-cli` flag table; the
  unsupported `--bpp` flag (with the source confirmation that the
  default TIFF output is 8-bit, which explains an empirical finding
  in the lrt-cinema task brief); the recipe for 16-bit linear
  Rec.2020 TIFF output without a `.style`.
- [`LR_IMPORT.md`](LR_IMPORT.md) — the field-by-field mapping
  decisions encoded in `src/develop/lightroom.c`. The complete list
  of LR XMP fields dt honors, drops silently, or reinterprets. Useful
  as the dt-authors' canonical answer to "what is the right
  LR-to-dt translation."
- [`LENS_CORRECTION.md`](LENS_CORRECTION.md) — `lens`, `cacorrect`,
  `cacorrectrgb`, and `ashift`. A second project assumption is
  refuted: dt does **not** auto-enable any of these. Diagnoses the
  empirical "geometry differs from LRT preview" finding.
- [`FILMIC_VS_SIGMOID.md`](FILMIC_VS_SIGMOID.md) — the three current
  scene-to-display tone-mapping modules (filmic, sigmoid, agx),
  their authors, when each is auto-applied, and which is closest
  to Lightroom's tone mapping (answer: none).

## Project context

lrt-cinema depends on darktable for the actual pixel pipeline. The
project's value-add is the LRTimelapse XMP parser and the per-frame
dt-XMP emitter; the rendering itself is delegated to `darktable-cli`.
Every dt-related fact we cite elsewhere in this repo — in
`SCOPE.md`, `docs/VALIDATION.md`, `docs/V03_PLAN.md`,
`src/lrt_cinema/presets/CALIBRATION.md`, source-file comments — should
trace back to one of the files in this directory.

## Notable refutations of prior project assumptions

This reference was created in part to verify claims the lrt-cinema
codebase had been making about darktable. Four of those claims do
not survive source inspection:

1. **"dt 5.5 nightly accepts `darktable:xmp_version="1"` only."**
   False. dt 5.5 master accepts values 0..5 (legacy reader for 0/1,
   modern reader for 2-5), rejects ≥6. The dt-master-written value
   is `DT_XMP_EXIF_VERSION = 5`. See XMP_FORMAT.md.

2. **"Exposure module is at modversion 6, temperature at 3."** Both
   wrong at master. Exposure is at 7 (added two gbooleans in v7);
   temperature is at 4 (added a `preset` int in v4). dt's
   `legacy_params` migration chain reads the old blobs but the
   emitter should match the running dt. See MODULES.md.

3. **"dt auto-enables lens correction when it detects a known
   camera body."** False. `lens.cc`, `cacorrect.c`, and `ashift.c`
   all leave `default_enabled = FALSE`. The geometric divergence
   from LRT's preview is caused by LR-side processing (LRT's preview
   is LR-rendered) honoring `crs:LensProfileEnable` and
   `crs:HasCrop`, which neither dt's LR importer nor lrt-cinema's
   emitter touches. See LENS_CORRECTION.md.

4. **"`darktable-cli --bpp` works but isn't documented."** Wrong.
   `--bpp` parses its arg, prints a "TODO: sorry" message, and
   discards the value. The default TIFF bit depth is 8 (not 16),
   set via `plugins/imageio/format/tiff/bpp`. The codebase's
   render-time output is 8-bit sRGB until `--core --conf` overrides
   are added to the argv. See EXPORT.md.

These four are documented refutations, not fix specifications. The
emitter / runner code changes implied by each are out of scope for
this reference PR — they belong in separate code-fix PRs, with
this reference as their justification.

## How to refresh

When darktable lands a release significant enough to invalidate any
table here:

1. `cd /tmp/dt-src && git fetch && git checkout <new-sha>`.
2. `git rev-parse HEAD` and substitute the SHA in every doc.
3. Re-run the `grep` checks documented inline (e.g., the
   `default_enabled` inventory in PIPELINE.md, the
   `DT_MODULE_INTROSPECTION` modversions in MODULES.md).
4. Update version-conditional language (e.g. "default in dt 5.5") if
   the default has changed.
5. Confirm `DT_XMP_EXIF_VERSION` and `DT_IOP_ORDER_VERSION` numbers
   haven't bumped; if they have, document the new accepted ranges in
   XMP_FORMAT.md and PIPELINE.md.

dt's master moves quickly. Treat this reference as accurate as of
the cited SHA; do not assume current-master behavior without
re-grep.
