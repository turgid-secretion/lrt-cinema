# lrt-cinema presets

Bundled darktable style files and an OCIO config for the three v0.1
output presets. Definitions live in `definitions.py`; the runner
references files in this directory by name.

## Files

| File | Purpose | Status |
|---|---|---|
| `definitions.py` | `Preset` dataclass + the three preset entries | shipping |
| `cinema_linear.style` | dt style: scene-linear Rec.2020, display transform off | **placeholder** |
| `cinema_aces.style` | dt style: same as cinema-linear; ACES tagging happens via OCIO + runner-set OpenEXR extension | **placeholder** |
| `stills_finished.style` | dt style: AgX display transform baked in, Rec.2020 gamma output | **placeholder** (requires dt 5.4+) |
| `ocio_config.ocio` | OpenColorIO v2 config; Linear Rec.2020 reference + ACES2065-1 via matrix | shipping |
| `CALIBRATION.md` | Procedure to turn the placeholder styles into shipping styles | reference |

## darktable version assumptions

- `cinema_linear` / `cinema_aces`: dt 4.6 LTS line and dt 5.x (5.4 at
  time of writing). The calibration step (see `CALIBRATION.md`) will
  confirm whether a single style file covers both or whether the
  bundle ships one file per version.
- `stills_finished`: dt 5.4+ only — the AgX module is a 5.4 addition.

## Why the styles are placeholders

darktable `.style` files require per-module binary parameter blobs
whose layout is keyed to the darktable version and (for some modules)
the source RAW. Synthesizing those blobs from documentation alone
would silently produce wrong-looking renders. `CALIBRATION.md`
documents the GUI-export-and-diff procedure that produces shippable
styles in week 5; until then the runner falls back to per-frame XMP
only when a style fails to load.

## OCIO config

`ocio_config.ocio` is a real, minimal v2 config — not a placeholder.
Reference space is Linear Rec.2020 (matches the cinema-* deliveries).
ACES2065-1 is reachable via the published Rec.2020-D65 to AP0-D60
matrix so Resolve auto-tags OpenEXR clips on an ACES timeline. The
AgX view is a placeholder marker — the real AgX render is baked into
the stills-finished output by darktable, not by OCIO.

Validate with `ociocheck` (OCIO >= 2.0) before relying on it in
production.

## Upstream docs

- darktable user manual:
  <https://docs.darktable.org/usermanual/development/en/> — module
  references for `sigmoid`, `filmicrgb`, `colorout`, the AgX page
  (5.4+), and the styles overview.
- darktable release notes:
  <https://www.darktable.org/install/> — confirm the AgX module
  ship version and op-name on the 5.4 (or current 5.x) release page.
- OpenColorIO v2 spec:
  <https://opencolorio.readthedocs.io/en/latest/guides/authoring/authoring.html>
- AgX background (Blender lineage):
  <https://github.com/sobotka/AgX>
