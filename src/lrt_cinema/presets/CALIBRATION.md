# Style file calibration

The three `.style` files in this directory (`cinema_linear.style`,
`cinema_aces.style`, `stills_finished.style`) are **placeholders**.
They contain the structural XML skeleton and documented intent but
**will not load cleanly into `darktable-cli` as shipped**.

This file documents exactly what the week-5 calibration pass must do
to turn the placeholders into shippable styles. Holding back the
calibration is a deliberate choice — see "Why not just ship guessed
params" below.

## What the styles need to do

| Preset | Operations to enable | Operations to disable | Output ICC |
|---|---|---|---|
| `cinema-linear` | colorout (lin_rec2020), basic capture sharpening | sigmoid, filmic*, basecurve, tonecurve, agx* | `lin_rec2020` |
| `cinema-aces`   | colorout (lin_rec2020), basic capture sharpening | sigmoid, filmic*, basecurve, tonecurve, agx* | `lin_rec2020` |
| `stills-finished` | colorout (rec2020 gamma), AgX display transform, capture sharpening | sigmoid, filmic*, basecurve, tonecurve | `rec2020` |

Note: `cinema-linear` and `cinema-aces` use the same darktable side
treatment — the OpenEXR vs TIFF split happens in the runner via the
output extension, not in the style.

## Why not just ship guessed params

A darktable `.style` file is XML, but each enabled module carries an
`<op_params>` binary blob (hex-encoded in current darktable) whose
layout is version-keyed. The blob encodes the module's full parameter
struct. You cannot synthesize one from text descriptions alone:

- Field order, padding, and sizes are determined by the C struct in
  the running darktable binary.
- Some fields (e.g. ICC filename in `colorout`) are null-terminated
  fixed-width strings; getting the length wrong silently drops the
  setting or worse, loads garbage.
- Module-version migration paths exist (`<op_params version="N">`)
  and a blob for version N will be rejected or auto-migrated when
  loaded by a darktable expecting version M.

A "best-guess" blob will load, silently set the wrong params, and
ship visually wrong renders. That failure mode is worse than no
style at all (in which case the runner's per-frame XMP plus
`--style` omission still produces a defined output, just without
the preset's discriminator). The runner today is already written
to fall back gracefully if a style file is missing or unloadable.

## Calibration procedure (week-5 deliverable)

1. **Bracket darktable versions.** Calibrate against the two anchors
   we support: the 4.6 LTS-ish line and the current 5.x line (5.4 at
   time of writing, where AgX landed). For each version:

2. **Generate a reference style by hand in the darktable GUI.**
   - Open a representative RAW (one from the project test fixtures).
   - For each preset, set up the desired module state exactly:
     disable sigmoid / filmic / basecurve / tonecurve; enable
     `colorout` with the correct output profile; for `stills-finished`
     enable the AgX display transform module.
   - Right-click the history stack → "create style" → save with the
     target filename.
   - Export the saved style file from darktable's config dir
     (`~/.config/darktable/styles/`).

3. **Diff the two version-anchored styles.** For each preset, diff
   the 4.6 export against the 5.x export. Three outcomes:
   - **Identical blobs.** Ship one file, supports both versions.
   - **Differ only in `version=` attribute, blob bytes identical.**
     Ship the lower-version blob; darktable will accept it on both.
   - **Blobs genuinely differ.** Ship the 5.x file and document a
     minimum-version requirement in the preset metadata. (4.6
     coverage would need an emitter that branches on detected dt
     version — out of scope for v0.1.)

4. **Verify by round-trip.** Run `darktable-cli` with each style
   file against a known RAW, compare the output pixel-for-pixel to
   the GUI-rendered reference. Document a hash in this file.

5. **Pin module names.** The placeholders use my best guess for
   module names; confirm during calibration:
   - `sigmoid` — added in dt 4.4, op-name expected stable through
     5.x but verify.
   - `agx_tone_mapping` — name on the 5.4 release notes I have. The
     module may have shipped under the shorter alias `agx`; both
     names should be tested.
   - `colorout` — long stable; the field name for the output ICC
     was `iccfilename` historically. Verify whether 5.x has shifted
     to a token enum.
   - `sharpen` vs `diffuse` for capture sharpening — pick one; the
     placeholder leaves the choice to the calibration step.

6. **Replace the placeholder files** in this directory and add a
   line to `CHANGELOG.md` noting the calibrated darktable versions.

7. **Update `SCOPE.md`** to move "Bundled darktable `.style` files"
   out of the Not-yet-implemented list.

## Acceptance test (runner side)

The runner's preset-loading path needs a test that asserts: when a
calibrated style exists for the active preset and darktable version
combination, `darktable-cli` is invoked with `--style PRESET.style
--style-overwrite`; otherwise the flag is omitted and a warning is
logged. That test belongs in `tests/test_runner.py` and is part of
the same calibration ticket.
