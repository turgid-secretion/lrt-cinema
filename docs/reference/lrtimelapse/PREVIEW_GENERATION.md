# `.lrtpreview` file format and color-space details

> Scope: precise format of the JPEG files LRTimelapse writes to
> `.lrt/previews/` and `.lrt/visual/`, what color space and tone curve
> they encode, what (if any) develop settings the preview pipeline
> honors.
>
> Sources: empirical inspection of LRT 7.5.3 output on 2026-05-22.
> The official LRT documentation does not describe the preview file
> format beyond calling it a "preview". This file is therefore mostly
> `OBSERVED, NOT DOCUMENTED`.

## File format (observed)

`identify` and `exiftool` agree on the format of every `.lrtpreview`
file in the user's sample sequence:

```
File Type             : JPEG
JFIF Version          : 1.02
Encoding Process      : Baseline DCT, Huffman coding
Bits Per Sample       : 8
Color Components      : 3
Y Cb Cr Sub Sampling  : YCbCr4:2:0 (2 2)
```

There are no other JPEG segments. No APP1 (Exif). No APP2 (ICC
profile). No APP13 (Photoshop / IPTC). No XMP packet appended.

The file is purely a JFIF-wrapped baseline JPEG with chroma
subsampling. SHA-256 of the user's `.lrt/previews/DSC_4053.lrtpreview`
on 2026-05-22 is
`8a59e28cf8e300fb401bcf23de988d4cb4a0ee0ff7f38376a8fde8f6070a149b`.

`OBSERVED 2026-05-22, LRT 7.5.3.` Not documented by LRT.

## Geometry

- `.lrt/previews/*.lrtpreview`: 640×424 in this sample (Nikon D750
  aspect 3:2, scaled). Exact dimensions presumably depend on source
  aspect; `OBSERVED, NOT DOCUMENTED` for non-3:2 sensors.
- `.lrt/visual/*.lrtpreview`: 1024×684, same aspect.

## Color space

No ICC profile is embedded. JPEG without an embedded profile is
conventionally interpreted as sRGB IEC 61966-2.1 by downstream
software (this is what ImageMagick reports when it says
`Colorspace: sRGB`). The LRT documentation does not state which color
space these previews are encoded in, but the *internal export*
documentation establishes that LRT's whole 8-bit pipeline is sRGB:
*"the internal export will always create 8 bit sRGB intermediary JPG
files"* (https://lrtimelapse.com/workflow/internal-workflow/). It is
consistent to infer the previews follow the same convention, but the
inference is undocumented for the preview path specifically.

`STATUS: UNKNOWN whether the preview pipeline applies a Lightroom-
equivalent base tone curve before JPEG-encoding, or whether the curve
shape encoded into the JPEG is sRGB transfer only.` Disambiguating
would require rendering the same DNG proxy through ACR with all
settings zeroed and comparing to LRT's preview — out of scope for
this reference.

## JPEG quality

`exiftool` does not surface the quantization tables directly; the
file size (~18 KB at 640×424) suggests medium quality. Approximate
JPEG-quality estimation tools (e.g. `magick identify -format "%Q"`)
yield single-image estimates that vary by content and should not be
treated as the LRT-encoder setting. `STATUS: UNKNOWN` for the
LRT-side JPEG quality factor; the LRT documentation does not state
it.

## Does the preview honor develop settings?

The LRT internal workflow tutorial states *"in the Visual Previews
phase, undeveloped camera previews are displayed in blue, developed
visual previews are displayed in pink"*. The "developed" previews
post-Visual-Previews are LRT-rendered with the per-frame XMP applied.
This is unambiguous: yes, the preview file changes when the user
edits a keyframe and re-runs Visual Previews.

What is *less* clear:

- **`crs:LensProfileEnable`.** Not addressed in LRT documentation. Our
  sample's keyframe XMPs all carry `crs:LensProfileEnable="0"` so the
  sample cannot discriminate. `STATUS: UNKNOWN.` The earlier project
  assumption that "LRT preview probably ignores LR's lens correction"
  is *not* sourced — it should be treated as conjecture until tested.
- **`crs:HasCrop`.** Same situation. All keyframe XMPs in the sample
  carry `crs:HasCrop="False"`. `STATUS: UNKNOWN.`
- **`crs:ToneCurvePV2012`.** The keyframe XMPs all carry the LR
  identity tone curve `[(0,0), (255,255)]`. `STATUS: UNKNOWN` whether
  a non-identity tone curve in a keyframe XMP would be reflected in
  the preview pipeline.
- **`crs:Exposure2012`, `crs:Contrast2012`, white balance, basic
  panel sliders.** These are the fields the LRT *Internal Editor*
  exposes, so they are by construction honored by the preview pipeline
  — that is the point of the editor. `DOCUMENTED implicitly` by the
  workflow tutorial; not a formal spec.

These gaps are *useful future-work*. The cleanest experiment: take a
single keyframe, set `crs:LensProfileEnable="1"` plus a known-distorted
lens profile, regenerate Visual Previews, compare the `.lrt/visual/`
JPEG against the lens-corrected reference rendered with the matching
Adobe LCP profile. Repeat for `HasCrop`, `ToneCurvePV2012`. None of
this is in our project's current scope.

## Why this matters for our project

We do not consume `.lrtpreview` files at runtime. They are a debugging
reference for "what LRT thinks this frame looks like." When our
darktable-rendered TIFF diverges visibly from LRT's preview, the
preview-pipeline behaviors above become candidates for the divergence
source — which is why this reference exists. We previously inferred
behaviors that may not hold; that inference is now flagged explicitly
rather than buried in code comments.

## Provenance summary for this section

| Claim | Source | Tag |
|---|---|---|
| 640×424 / 1024×684 JPEG/JFIF baseline | exiftool, identify | OBSERVED 2026-05-22 |
| No ICC, EXIF, or XMP segments | exiftool | OBSERVED 2026-05-22 |
| YCbCr 4:2:0 8-bit | exiftool | OBSERVED 2026-05-22 |
| sRGB encoding | not documented for preview; documented for internal export | INFERRED, plausible |
| JPEG quality factor | not retrievable from file | UNKNOWN |
| Preview honors basic-panel develop settings | LRT internal workflow tutorial | DOCUMENTED implicitly |
| Preview honors `crs:LensProfileEnable` | not addressed | UNKNOWN |
| Preview honors `crs:HasCrop` | not addressed | UNKNOWN |
| Preview honors `crs:ToneCurvePV2012` | not addressed | UNKNOWN |
