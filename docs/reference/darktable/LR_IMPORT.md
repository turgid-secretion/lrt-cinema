# Lightroom XMP import — what darktable actually understands

Source: [`src/develop/lightroom.c`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/develop/lightroom.c)
at commit `635c0c55b64331481dffe30f937ba3fe72f83857`. The file has
been in tree since 2013 (per the file header copyright); structural
changes have been infrequent.

dt routes a sidecar through `lightroom.c` when the XMP has
**no** `xmlns:darktable=` namespace declaration; the dispatch is at
[`src/common/exif.cc#L4071-L4074`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/common/exif.cc#L4071-L4074).
For dt-authored sidecars this code never runs. For LRT-authored
sidecars (which carry only the Adobe `crs:`/`xmp:`/`exif:` namespaces),
this is the import path dt uses when an LRT-edited image is loaded
into the dt GUI. lrt-cinema does not route through this path — we
write our own dt-native sidecars — but the field-mapping decisions
encoded here are dt's authors' canonical answer to "what is the right
LR->dt translation?", so the table below is the reference for
lrt-cinema's emitter calibration.

## The complete set of field names lightroom.c matches

Extracted by `grep -oE '"[A-Z][a-zA-Z0-9]+2?0?1?2?"'` on
`lightroom.c`, filtered to LR develop fields:

**Crop / orientation:**
- `CropTop`, `CropBottom`, `CropLeft`, `CropRight`, `CropAngle`
- `HasCrop`
- `Orientation` (EXIF rotation passthrough)
- `ImageWidth`, `ImageLength`

**Exposure module (dt `exposure`):**
- `Exposure2012` (mapped float-to-float)
- `Blacks2012` (mapped via [`lr2dt_blacks_table`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/develop/lightroom.c#L279-L284):
  `{-100, 0.020}, {-50, 0.005}, {0, 0}, {50, -0.005}, {100, -0.010}` —
  this is the 5-point LUT lrt-cinema's calibration work referenced)

**Tone curve (dt `tonecurve`):**
- `ToneCurveName2012` (Linear / Medium Contrast / Strong Contrast / Custom)
- `ToneCurvePV2012` (li-list of `(x, y)` integer pairs in 0..255;
  parsed at [`lightroom.c#L965-L981`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/develop/lightroom.c#L965-L981))

**Parametric tone curve:**
- `ParametricShadows`, `ParametricDarks`, `ParametricLights`, `ParametricHighlights`
- `ParametricShadowSplit`, `ParametricMidtoneSplit`, `ParametricHighlightSplit`

**Vignette (dt `vignette`):**
- `PostCropVignetteAmount` (via [`lr2dt_vignette_gain`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/develop/lightroom.c#L287-L292))
- `PostCropVignetteMidpoint`
- `PostCropVignetteStyle` (1 = Highlight Priority -> saturation -0.300; else -0.200)
- `PostCropVignetteFeather`, `PostCropVignetteRoundness`

**HSL adjustments (dt `colorzones`):**
- `SaturationAdjustment{Red,Orange,Yellow,Green,Aqua,Blue,Purple,Magenta}`
- `LuminanceAdjustment{Red,Orange,Yellow,Green,Aqua,Blue,Purple,Magenta}`
- `HueAdjustment{Red,Orange,Yellow,Green,Aqua,Blue,Purple,Magenta}`

Mapping: lightness uses a `lfactor = 4/9` (4 out of 9 colorzones
boxes), hue uses `hfactor = 3/9`, saturation uses 1:1. See
[`lightroom.c#L671-L740`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/develop/lightroom.c#L671-L740).

**Split toning (dt `splittoning`):**
- `SplitToningShadowHue`, `SplitToningShadowSaturation`
- `SplitToningHighlightHue`, `SplitToningHighlightSaturation`
- `SplitToningBalance` (via [`lr2dt_splittoning_balance`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/develop/lightroom.c#L315-L320))

**Grain (dt `grain`):**
- `GrainAmount`, `GrainFrequency` (via piecewise tables)

**Clarity (dt `bilat`):**
- `Clarity2012` -> `lr2dt_clarity` ({-100, -0.65}, {0, 0}, {100, 0.65})

**Retouch (dt `spots`):**
- `RetouchInfo` (li-list of clone source points)

**Misc:**
- `Rating` (xmp:Rating; passthrough)
- `GPSLatitude{,Ref}`, `GPSLongitude{,Ref}`
- `title`, `description`, `creator`, `rights`, `publisher` (DC metadata)

## What dt's LR import DROPS (silently)

The following LR fields appear in real LR sidecars but lightroom.c has
**no matching branch** — verified by exhaustive grep on field names
known from LR XMP samples. dt silently discards them on import:

| LR field | What it controls in LR |
|---|---|
| `Temperature` | White balance kelvin |
| `Tint` | White balance green-magenta |
| `Contrast2012` | Master contrast slider |
| `Highlights2012` | Highlights recovery slider |
| `Shadows2012` | Shadows recovery slider |
| `Whites2012` | Whites pivot slider |
| `Saturation` | Master saturation slider |
| `Vibrance` | Master vibrance slider |
| `Sharpness` | Master sharpening amount |
| `SharpenRadius`, `SharpenDetail`, `SharpenEdgeMasking` | Sharpening sub-knobs |
| `LuminanceSmoothing`, `ColorNoiseReduction*` | NR controls |
| `LensProfileEnable`, `LensProfileName`, `LensManualDistortionAmount` | Lens correction |
| `LensProfileSetup`, `LensProfileVignettingScale` | Lens correction |
| `DefringePurpleAmount`, `DefringeGreenAmount` | CA defringe |
| `ConvertToGreyscale` | B&W conversion |
| `Dehaze` | Dehaze slider |
| `Texture` | Texture slider (LR 8+) |
| `AutoLateralCA` | Lateral CA correction |
| `HDREditMode`, `AutoLateralCA` | LR HDR / lateral |
| `PerspectiveUpright`, `Upright*` | Upright corrections |

This list is the largest single gap. The dt developers' position
(implicit in the file) is that:

- LR's `Temperature`/`Tint` need camera-specific DCP matrix data dt
  doesn't try to replicate.
- LR's PV2012 parametric tone math (`Highlights2012` etc.) is
  Adobe-proprietary and there's no published mapping to filmic /
  sigmoid / tonecurve.
- LR's `Sharpness` family needs the LR-internal radius/detail/masking
  algorithm; dt's `sharpen` (USM) and `diffuse` (PDE) don't have a
  one-to-one mapping.
- LR's lens correction uses Adobe LCP profiles; dt's lens module uses
  lensfun. The data sources are incompatible.

## What dt's LR import REINTERPRETS (different meaning than LR)

A few fields are matched but mapped to a dt module whose semantics
diverge:

- `Blacks2012`: 5-point LUT to dt's `exposure.black` (in units of
  linear-RGB-displacement, range -0.020 to +0.010). LR's
  `Blacks2012` is in 0..100 "Adobe perceptual units." The
  5-point fit is approximate even at the table breakpoints;
  values between breakpoints are linearly interpolated.
- `Clarity2012`: scaled to `bilat.detail` in range -0.65..+0.65.
  dt's `bilat` uses a bilateral filter; LR's clarity uses an
  adaptive midtone-contrast algorithm. Visual similarity is
  rough.
- `PostCropVignetteAmount`: 5-point LUT to dt `vignette.brightness`.
  dt's vignette is a simple radial darkening; LR's PostCrop adds
  midpoint, roundness, feather, and a "highlight priority" mode
  with selective saturation interaction. Some of these are read
  (Midpoint, Feather, Roundness) but the priority-mode behavior
  collapses to a fixed `-0.300` or `-0.200` saturation offset.
- `ParametricShadows` etc.: read into a `ptc_value[]` array, used
  to build a custom `tonecurve` parametric channel. The dt
  parametric channel uses Bezier curves; LR's uses a proprietary
  spline. The match is "looks similar at default split points,"
  not pixel-equivalent.

## Mapping execution

`_lrop()` at [`lightroom.c#L486`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/develop/lightroom.c#L486)
is the dispatch — it's a long chain of `else if` blocks keyed on XML
attribute name. Fields ending in `*Adjustment*` are accumulated into
the `colorzones` data struct; once all 8x3 = 24 hue/sat/lum entries
are read, dt synthesizes a `colorzones` history entry.

`_handle_xpath()` at [`lightroom.c#L1056`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/develop/lightroom.c#L1056)
walks the parsed XML tree with xmlXPath queries; each query points
at a known LR field path. Any path with no XPath registered above is
silently ignored.

`dt_add_hist()` at [`lightroom.c#L329`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/develop/lightroom.c#L329)
takes a built params struct and inserts it as a history entry for
the imgid being imported. Each module's struct used here is a frozen
copy of an older dt params layout — see the file's top comment at
[`lightroom.c#L39-L48`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/develop/lightroom.c#L39-L48):

> // copy here the iop params struct with the actual version. This is so to
> // be as independent as possible of any iop evolutions. Indeed, we create
> // the iop params into the database for a specific version. We then ask
> // for a reload of the history parameter. If the iop has evolved since then
> // the legacy circuitry will be called to convert the parameters.

That is: the LR importer pins to historical struct layouts on
purpose; dt's per-iop legacy_params migration chain handles the
move from those historical versions to current.

## Implications for lrt-cinema

- The "12 LRT-emitted develop ops" we want to honor map to dt as:
  - `exposure_ev` -> dt `exposure.exposure` (exact, 1:1)
  - `temperature_k`, `tint` -> NOT mapped by dt; requires DCP-derived
    multipliers we compute ourselves (see V03_PLAN.md Track A5)
  - `contrast`, `highlights`, `shadows`, `whites` -> NOT mapped by
    dt; Adobe-proprietary PV2012 math, requires our own calibration
    (V03_PLAN.md Track A2)
  - `blacks` -> dt does map via 5-point LUT (`lr2dt_blacks_table`),
    we can reuse that table verbatim
  - `saturation`, `vibrance` -> NOT mapped by dt; we map to
    `colorbalancergb.saturation_global` / `vibrance` (Track A3)
  - `sharpness` -> NOT mapped by dt; we map to `sharpen.amount`
    (Track A4)
  - `tone_curve` -> dt maps via parametric tonecurve from
    `ToneCurvePV2012` li-list (the curve_pts array path); we can
    reuse that mapping (Track A1)
- The lrt-cinema emitter does NOT need to use `lightroom.c`'s
  mapping; it emits dt-native sidecars directly. But for every
  mapping decision lightroom.c made (e.g., the Blacks2012 LUT, the
  hue 3/9 factor), the dt-authors' choice is the
  least-controversial starting point. Diverging from it requires
  evidence that the LR-equivalent dt setting differs measurably
  from what dt's own importer produces.
