# Darktable `.style` files

Authoritative source: [`src/common/styles.c`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/common/styles.c)
at commit `9402c65275bebebc4649c6dc91d3798d4bd63a0f`. Citations to
that SHA throughout.

## File format

A `.style` file is XML parsed by GLib's `g_markup` SAX parser. The
parser tags handled, from
[`styles.c#L1433-L1544`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/common/styles.c#L1433-L1544):

Top-level element children (any names, recognized by the text handler):

- `<name>` — style display name (string)
- `<description>` — free-form description (string)
- `<iop_list>` — serialized iop-order list (text format consumed by
  `dt_ioppr_deserialize_text_iop_order_list`, present only if the
  style was saved against a custom iop order)
- `<plugin>` — one per active history entry (see below)

Each `<plugin>` element contains, as text-bearing children:

- `<num>` — int (history-stack position)
- `<module>` — int (the module's introspection version when this
  history entry was created)
- `<operation>` — string (op name, e.g. `exposure`)
- `<op_params>` — text (the binary params struct, encoded; format
  matches the XMP `darktable:params` blob — see XMP_FORMAT.md)
- `<enabled>` — "0" or "1"
- `<blendop_version>` — int
- `<blendop_params>` — text (blendop binary struct)
- `<multi_name>` — string (instance name)
- `<multi_name_hand_edited>` — "0" or "1"
- `<multi_priority>` — int
- `<iop_order>` — float (per-instance pipe order override; only
  present in non-default-order styles)

There is no explicit version attribute on a style file. The encoded
parameters' compatibility is governed by the individual `<module>`
integer for each `<plugin>` (the per-iop introspection version). dt
runs the same `legacy_params()` migration chain on style import as
on XMP import, so an older `<module>` value will be migrated forward
if the running dt has a migration covering the gap.

## How dt loads styles

`dt_styles_import_from_file()` at
[`styles.c#L1619-L1680`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/common/styles.c#L1619-L1680)
streams the file through the SAX parser in 8 KiB chunks, fills a
`StyleData` struct, then `dt_style_save()` inserts:

- one row into `data.styles` (name, description, optional iop_list)
- one row into `data.style_items` per `<plugin>` element

When the style is applied to an image (CLI: `--style NAME
--style-overwrite`), `dt_styles_apply_to_image()` at
[`styles.c#L1003`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/common/styles.c#L1003)
copies each style item into `main.history` for that image, then the
darkroom-reload path runs the per-module `legacy_params()` migration
on each blob.

`--style-overwrite` (vs the default `--style` append) wipes the
existing history before applying. Effect on a per-frame XMP
sidecar: the sidecar's `<darktable:history>` is fully replaced; only
the style's items take effect. Without `--style-overwrite` the style
items are appended after the sidecar's items, and the user's per-frame
XMP edits win for any overlap (because higher num beats lower num for
same-op-same-priority entries).

## `.style` vs per-frame XMP — the interaction

For lrt-cinema's per-frame rendering:

- The `<RAW>.xmp` sidecar next to the source RAW carries the per-frame
  intent (exposure, etc.).
- `--style PRESET.style --style-overwrite` applied via dt-cli would
  **discard** the sidecar's history. That's wrong for our pipeline.
- The right pattern is **no `--style-overwrite`**, plus a style file
  that contains only the modules NOT in our per-frame sidecar. The
  style appends its items; the sidecar's items take precedence
  per-op-per-instance.
- Equivalently: emit all needed modules in the per-frame XMP, ship no
  `.style`. Simpler at the cost of larger sidecars and per-frame
  computation of `colorout` / `sigmoid` / etc. blobs.

dt-cli's argv flag handling for `--style` / `--style-overwrite` is
at [`src/cli/main.c#L334-L342`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/cli/main.c#L334-L342)
and the runtime application at
[`src/cli/main.c#L820-L832`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/cli/main.c#L820-L832).
The CLI's `style[]` field is limited to `DT_MAX_STYLE_NAME_LENGTH`
(128 chars including terminator).

## Per-darktable-version compatibility

The style file format itself is dt-version-agnostic: the field names
have not changed since dt 2.x. Compatibility breaks come from the
per-`<plugin>` `<op_params>` payload. A style saved under dt 5.5 with
`exposure` modversion 7 ships a 26-byte params blob; loaded into dt
4.6 (which has modversion 6, 24 bytes), dt's legacy_params would
need a backward migration, which does not exist (migrations only go
forward). The style appears to load but the `exposure` history entry
either silently drops or applies stale defaults.

## The "version-anchored style" calibration procedure

The procedure described in `src/lrt_cinema/presets/CALIBRATION.md`
is correct as written. Confirmed against `styles.c`:

1. Generate the style by hand in the dt GUI (right-click history -> "create style").
2. Export the saved style via the dt styles preset GUI to a `.style`
   file in `~/.config/darktable/styles/`.
3. The resulting file is the byte-exact encoding that the dt version
   that saved it expects.
4. To cover dt 4.6 LTS and dt 5.x simultaneously without per-version
   blobs, the style must be saved with the **lowest** module
   versions that have matching forward-migrations in the higher
   versions. In practice this means saving in dt 4.6 first, then
   diffing the 5.x export — if the binary blobs differ, ship the
   4.6 version (which dt 5.x can migrate forward) and skip dt 4.6
   coverage entirely if the running 4.6 misses a feature that the
   blob requires.

There is no `.style` field for a "minimum darktable version" — that
must be documented out-of-band.

## Implications for lrt-cinema

- The bundled style approach (one `.style` per preset, applied via
  `--style PRESET --style-overwrite`) is incompatible with per-frame
  sidecar edits because `--style-overwrite` wipes the sidecar's
  history. Use `--style PRESET` (append) and rely on sidecar items
  winning per-op-per-priority. OR drop the `.style` entirely and
  emit everything per-frame.
- Either path requires knowing the per-darktable-version
  introspection number for every module we emit. See MODULES.md.
- Two practical alternatives to the current `.style` plan:
  1. Generate the style at install/first-run from the running
     dt-cli's introspection (parse `--core -d params <op>` output)
     and cache it. Robust but slow at first invocation.
  2. Ship a `--core --conf` block in the dt-cli invocation that
     sets `plugins/imageio/format/tiff/bpp`, output ICC, etc. —
     bypass styles altogether. See EXPORT.md.
