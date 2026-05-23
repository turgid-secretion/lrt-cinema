# lrt-cinema

Translate [LRTimelapse](https://lrtimelapse.com/) XMP develop instructions into [darktable](https://www.darktable.org/) history-stack XMP sidecars, then render a cinema-native intermediate sequence (linear Rec.2020 TIFF, ACES OpenEXR, or AgX-baked Rec.2020 TIFF) via `darktable-cli`.

**Status:** Pre-alpha scaffold. Not yet usable for production work. See [SCOPE.md](SCOPE.md) for what is and is not implemented.

**Companion, not fork.** lrt-cinema does not modify or distribute LRTimelapse. It reads the XMP sidecars LRTimelapse writes and routes the develop intent through a different RAW pipeline. LRTimelapse is a separate commercial product by Gunther Wegner; this tool is independent.

## What it does

LRTimelapse writes Lightroom-shaped XMP sidecars alongside RAW frames, including time-series extensions (Holy Grail exposure ramps, deflicker offsets, keyframe markers). The canonical LRT workflow renders those XMPs through Adobe Lightroom Classic or Camera Raw.

`lrt-cinema` is an alternative renderer for the same XMP intent:

1. Parse LRT-written XMP into an internal develop-ops + keyframes representation.
2. Interpolate per-frame develop values from keyframes (linear and smooth modes; Holy Grail ramp support).
3. Emit per-frame darktable history-stack XMP that maps the supported develop ops onto darktable modules (exposure, temperature, tone-curve / sigmoid, sharpening, etc.).
4. Invoke `darktable-cli` per frame to produce the output sequence.
5. Optionally apply a deflicker pass (export-and-measure-luminance loop with exposure delta writeback).

## Output presets

| Preset | Container | Color space | Display transform | Intended downstream |
|---|---|---|---|---|
| `cinema-linear` | 16-bit TIFF | Linear Rec.2020 | Disabled | Resolve (tag clip as Linear Rec.2020 input) |
| `cinema-aces` | 32-bit float OpenEXR (PIZ) | Linear Rec.2020 | Disabled | ACES timelines (bundled OCIO config) |
| `stills-finished` | 16-bit TIFF | Rec.2020 (gamma) | AgX baked | Finished delivery without further grading |

## Requirements

- Python 3.10+
- `darktable-cli` 4.6+ available on `PATH` (5.4+ recommended for AgX preset)
- Source RAW files supported by darktable: CR3, NEF, ARW, DNG, RAF, ORF, RW2, FFF

Install darktable:
- **macOS:** `brew install --cask darktable`
- **Debian/Ubuntu:** `sudo apt install darktable`
- **Fedora:** `sudo dnf install darktable`
- **Arch:** `sudo pacman -S darktable`

## Install

```bash
pipx install lrt-cinema       # once published to PyPI
# or, from source checkout:
pipx install .
```

## Usage

```bash
lrt-cinema render \
  --input  /path/to/source-and-xmp-folder \
  --preset cinema-linear \
  --output /path/to/output-tiff-sequence
```

Other presets:

```bash
lrt-cinema render --input ... --preset cinema-aces --output ...
lrt-cinema render --input ... --preset stills-finished --output ...
```

Power-user knobs:

```bash
lrt-cinema render \
  --input ... --output ... --preset cinema-linear \
  --style /path/to/custom-darktable.style \
  --keyframes auto \
  --interpolation smooth \
  --holy-grail apply-lrt-ramps \
  --deflicker none \
  --workers 4 \
  --from-frame 0 --to-frame 500 \
  --dry-run
```

See `lrt-cinema --help` for the full surface.

## Scope and non-goals

**In scope:**
- Translation of LRT-written XMP develop ops that have a credible darktable equivalent.
- Per-frame interpolation of keyframe values.
- Three output presets above.
- Bundled OCIO config naming the output color spaces.

**Out of scope:**
- Replicating Adobe Camera Raw's parametric tone-curve look. AgX or scene-linear with a Resolve LUT downstream is a different rendering target.
- CNN-based detail enhancement (ACR "Enhance Details" equivalent).
- Spectral ACES IDTs from camera characterization. Honor darktable's matrix transforms and document Resolve-side tagging.

See [SCOPE.md](SCOPE.md) for the per-feature implementation status.

## License

[Apache-2.0](LICENSE).

## Acknowledgements

- The LRTimelapse XMP format is the public output of LRTimelapse; this tool reads but does not bundle or modify it.
- darktable is the RAW pipeline; lrt-cinema invokes it via subprocess and ships no darktable code.
- The Blender Foundation's AgX work is the basis for darktable's AgX module.
- Prior art: [dtLapse](https://pypi.org/project/dtlapse/) (GPL-3, last updated 2020) explored a similar translation; lrt-cinema's interpolation is a clean reimplementation rather than a fork.

## Contributing

Bug reports and PRs welcome via GitHub issues. See [CONTRIBUTING.md](CONTRIBUTING.md) (TBD).
