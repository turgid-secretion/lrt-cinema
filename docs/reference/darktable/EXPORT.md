# Darktable-cli — export configuration

Authoritative sources: `src/cli/main.c`, `src/imageio/format/tiff.c`,
`src/imageio/format/exr.cc`, `data/darktableconfig.xml.in`. All at
commit `635c0c55b64331481dffe30f937ba3fe72f83857`.

## `darktable-cli` invocation shape

From [`src/cli/main.c#L74-L106`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/cli/main.c#L74-L106):

```
darktable-cli [IMAGE_FILE | IMAGE_FOLDER]
              [XMP_FILE] DIR [OPTIONS]
              [--core DARKTABLE_OPTIONS]
```

`[XMP_FILE]` is positional: if a single file follows the input image,
dt-cli takes it as the XMP sidecar; otherwise the sibling `<RAW>.xmp`
is auto-detected. `[DIR]` is the output destination (a directory, or
a filename if `--out-ext` is also provided).

## The flag list (master)

| Flag | Default | What it does |
|---|---|---|
| `--apply-custom-presets <0|1>` | 1 | Run dt's auto-apply preset injection (filmic/sigmoid workflow, camera-specific custom matrices, etc.). Set to 0 for fully deterministic per-frame output. |
| `--export_masks <0|1>` | 0 | Re-emit mask metadata into output |
| `--width N` | 0 | Max output width in pixels; 0 = full resolution |
| `--height N` | 0 | Max output height; 0 = full resolution |
| `--hq <0|1>` | 1 | High-quality resampling (Lanczos vs faster bilinear) |
| `--upscale <0|1>` | 0 | Allow upscaling beyond source resolution |
| `--style NAME` | (none) | Apply style; default is APPEND to sidecar history |
| `--style-overwrite` | (off) | When `--style` is set, replace sidecar history rather than appending |
| `--out-ext .ext` | (from DIR) | Output extension; selects the format plugin |
| `--import file_or_dir` | (none) | Specify input; can repeat instead of positional |
| `--library path` | (sidecar) | Read history from library DB rather than XMP sidecar |
| `--icc-type TYPE` | NONE | Override colorout type (see list below) |
| `--icc-file file` | NONE | Override colorout ICC filename |
| `--icc-intent intent` | LAST | PERCEPTUAL / RELATIVE_COLORIMETRIC / SATURATION / ABSOLUTE_COLORIMETRIC |
| `--bpp N` | (unsupported) | **PRINTS A NOTICE, DOES NOTHING.** See below |
| `--verbose` | (off) | Per-pixel-pipe logging |
| `--core KEY=VAL ...` | (none) | Forward to darktable core; key is a darktablerc key, value is its setting |

`--core` is the escape hatch for any conf-driven setting (darktablerc).
It is documented as "open-ended" — see `data/darktableconfig.xml.in`
for the full set of keys.

## `--bpp` is unsupported

Source: [`src/cli/main.c#L279-L290`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/cli/main.c#L279-L290):

```c
else if(!strcmp(arg[k], "--bpp") && argc > k + 1)
{
  k++;
  bpp = MAX(atoi(arg[k]), 0);
  fprintf(stderr, "%s %d\n",
          _("TODO: sorry, due to API restrictions we currently "
            "cannot set the BPP to"), bpp);
}
```

The value is parsed into a local `bpp` variable, a notice is printed
to stderr, and the variable is never propagated to the export
pipeline. The source comments confirm: at
[`src/cli/main.c#L21`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/cli/main.c#L21)
`make --bpp work` is listed as a top-of-file TODO, and at
[`src/cli/main.c#L841`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/cli/main.c#L841)
the comment reads "`TODO: add a callback to set the bpp without
going through the config`."

The actual bit-depth comes from the format plugin's conf entry,
loaded at `get_params()` time:

- TIFF: `dt_conf_get_int("plugins/imageio/format/tiff/bpp")` at
  [`tiff.c#L743`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/imageio/format/tiff.c#L743)
- EXR: `dt_conf_get_int("plugins/imageio/format/exr/bpp")` at
  [`exr.cc#L549`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/imageio/format/exr.cc#L549)

## Bit-depth defaults

From `data/darktableconfig.xml.in`:

| Format | Conf key | Default | Allowed values |
|---|---|---|---|
| TIFF | `plugins/imageio/format/tiff/bpp` | **8** | 8, 16, 32 |
| TIFF (16-bit float) | `plugins/imageio/format/tiff/pixelformat` | 0 (int) | 0 (int), 1 (float; requires Imath) |
| EXR | `plugins/imageio/format/exr/bpp` | **32** (full float) | 16 (half), 32 (full) |

**The default TIFF output is 8-bit.** This explains the empirical
finding in the lrt-cinema task brief: a render with no `--bpp` and
no `--core` override produces an 8-bit TIFF. Combined with the
default `colorout` of `DT_COLORSPACE_SRGB` (see MODULES.md), the
default dt-cli output is 8-bit sRGB even when the preset name
claims linear Rec.2020.

## The cinema-linear Rec.2020 16-bit TIFF recipe

To produce 16-bit linear Rec.2020 TIFF from dt-cli with NO `.style`:

```sh
darktable-cli SOURCE.NEF SIDECAR.xmp OUTPUT.tif \
  --apply-custom-presets 0 \
  --icc-type LIN_REC2020 \
  --icc-intent RELATIVE_COLORIMETRIC \
  --core \
    --conf plugins/imageio/format/tiff/bpp=16 \
    --conf plugins/imageio/format/tiff/compress=0 \
    --conf plugins/imageio/format/tiff/pixelformat=0
```

Explanation:
- `--apply-custom-presets 0` suppresses the workflow auto-apply
  (sigmoid / filmic prepend). With this off, dt processes only what
  the sidecar requests.
- `--icc-type LIN_REC2020` is converted by
  [`src/cli/main.c#L154`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/cli/main.c#L154)
  to `DT_COLORSPACE_LIN_REC2020 = 4`, which is passed to
  `storage->store(...)` at
  [`src/cli/main.c#L863`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/cli/main.c#L863).
  That override propagates to the colorout module's params at export
  time, regardless of the sidecar's own colorout history entry.
- `--core --conf ...` sets the TIFF format plugin's params before
  the format is loaded. Multiple `--conf KEY=VAL` pairs after a
  single `--core` are accepted.
- `compress=0` is uncompressed (largest, fastest write). Use `5`
  for LZW or `9` for ZIP if disk space matters.

`--icc-type` valid values, from
[`src/cli/main.c#L115-L144`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/cli/main.c#L115-L144):

```
NONE, FILE, SRGB, ADOBERGB, LIN_REC709, LIN_REC2020, XYZ, LAB,
INFRARED, DISPLAY, EMBEDDED_ICC, EMBEDDED_MATRIX, STANDARD_MATRIX,
ENHANCED_MATRIX, VENDOR_MATRIX, ALTERNATE_MATRIX, BRG, EXPORT,
SOFTPROOF, WORK, DISPLAY2, REC709, PROPHOTO_RGB, PQ_REC2020,
HLG_REC2020, PQ_P3, HLG_P3, DISPLAY_P3
```

`--icc-intent` valid values:
```
PERCEPTUAL, RELATIVE_COLORIMETRIC, SATURATION, ABSOLUTE_COLORIMETRIC
```

## The cinema-ACES (OpenEXR) recipe

```sh
darktable-cli SOURCE.NEF SIDECAR.xmp OUTPUT.exr \
  --apply-custom-presets 0 \
  --icc-type LIN_REC2020 \
  --core \
    --conf plugins/imageio/format/exr/bpp=32 \
    --conf plugins/imageio/format/exr/compression=2
```

Compression values for OpenEXR ([`exr.cc`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/imageio/format/exr.cc)
defines them as enum; 2 is PIZ, the default cinema-friendly lossless
codec).

Note: ACES2065-1 (AP0) is not in dt's `--icc-type` set. Lin Rec.2020
is the closest scene-linear output; the consumer is expected to apply
the Rec.2020 -> AP0 matrix on ingest. If that's not acceptable, the
alternative is to provide an ACES AP0 .icc via `--icc-type FILE
--icc-file /path/to/AP0.icc`.

## What dt-cli does not let you set per-invocation

- Working color space (`colorin` module's `type_work`). Compile-time
  default `DT_COLORSPACE_LIN_REC2020` for raw inputs, overridable
  per-image only through the XMP sidecar's `colorin` history entry.
- Demosaic method. Compile-time default RCD; per-image override via
  XMP sidecar's `demosaic` history entry.
- Highlight reconstruction method. Per-image via XMP.
- Per-format dither / chroma subsampling. JPEG quality has a flag
  (`plugins/imageio/format/jpeg/quality`); TIFF does not, and dt
  always writes 4:4:4.

## Implications for lrt-cinema

- The codebase comment in `runner.py` that "`--bpp` is documented
  unsupported" is correct. The same comment's assumption that "the
  bundled `.style` files will pin TIFF bit depth" is wrong: a
  `.style` only carries module history (colorout, etc.); it cannot
  set the TIFF format plugin's conf. Use `--core --conf` instead.
- The runner argv builder should add the `--core --conf ...` block
  for the cinema-linear preset. The current argv (per `runner.py`)
  produces dt's default 8-bit sRGB.
- `--apply-custom-presets 0` is essential for reproducibility across
  user dt installations with different workflow settings.
- For `--icc-type LIN_REC2020`, the same colorout override happens
  whether the XMP sidecar contains a colorout history entry or not,
  so the emitter can skip writing colorout entirely.
