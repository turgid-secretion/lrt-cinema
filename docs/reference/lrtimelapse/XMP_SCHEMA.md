# LRTimelapse XMP schema

> Scope: the XMP attributes and elements LRTimelapse writes to sequence
> XMP sidecars. Adobe-namespace fields (`crs:*`, `xmp:*`, `xmpMM:*`,
> `exif:*`) are LR-shaped and out of scope here; this file documents
> only the LRT-namespace fields and the LRT-specific *conventions*
> imposed on shared-namespace fields.
>
> Sources: forum post by Gunther Wegner (cited verbatim below for the
> mask-correction roles) and empirical inspection of the user's LRT
> 7.5.3 keyframe XMP `.lrt/proxy/DSC_5059.xmp` on 2026-05-22 (SHA-256
> `9219ec3971e713b80abbf599d81f3e76105b3c56c97326180e4b442ba491b29e`).

## LRT namespace

```
xmlns:lrt="http://lrtimelapse.com/"
```

Note: **no trailing `ns/1.0/`**, no version segment in the URI. This
was a calibration item for our parser; earlier synthetic fixtures used
the longer form and were wrong. The bare base URI is what real LRT
7.5.3 writes (validated and locked into the parser in commit
`2ae63da`, see `src/lrt_cinema/xmp_parser.py:44`).

## `lrt:*` attributes on `rdf:Description`

`OBSERVED 2026-05-22` in the user's sequence:

| Attribute | Example value | Source / Meaning |
|---|---|---|
| `lrt:Aperture` | `"13.0"` | extracted from EXIF aperture |
| `lrt:Iso` | `"100"` | extracted from EXIF ISO |
| `lrt:ShutterSpeed` | `"0.5"` | extracted from EXIF shutter (seconds) |
| `lrt:Width` | `"6032"` | original raw width in pixels |
| `lrt:Height` | `"4032"` | original raw height in pixels |
| `lrt:Quality` | `"RAW"` | source-image quality classification |
| `lrt:ShootingMode` | `"Manual"` | from EXIF |
| `lrt:IsMergedHDR` | `"false"` | HDR-merge flag, set by HDR workflow |

These are frozen-EXIF metadata, not develop intent. LRT stores them as
namespace attributes because they need to survive XMP-only handoff to
the renderer (the renderer should not have to re-open the raw to learn
the shutter speed). Other `lrt:*` attributes may exist in
HDR-merged-sequence XMPs or in long-term-timelapse XMPs; we have not
observed them in this sample. `STATUS: UNKNOWN for the full
`lrt:*` attribute set.`

## `xmp:Rating` semantics (LRT convention on a shared field)

`xmp:Rating` is a standard Adobe attribute, but LRT overloads it as the
keyframe-flag mechanism. The convention `OBSERVED 2026-05-22` and
encoded in our parser (`src/lrt_cinema/xmp_parser.py:52-57`):

| Rating | Meaning |
|---|---|
| `0` | not a keyframe; ordinary interpolated frame |
| `4` | "Creative" keyframe — set by the Keyframes Wizard |
| `1`, `2`, `3`, `5` | other keyframe roles per LRT UI conventions; our parser treats any `Rating >= 1` as "some kind of keyframe" |

Wegner's forum / tutorial materials confirm `Rating="4"` as the
Keyframes-Wizard creative-keyframe value but do not exhaustively
enumerate the other rating values. `STATUS: PARTIALLY DOCUMENTED` —
the binary "keyframe vs not" semantics are reliable; the fine-grained
sub-types behind 1/2/3/5 are observable in the LRT UI but not
formally specced.

## `xmlns:xmpMM` history record

LRT writes one `xmpMM:History` `rdf:Seq` entry per significant write,
stamping the software agent with the full license string. `OBSERVED`
in the user's sequence:

```xml
<xmpMM:History>
  <rdf:Seq>
    <rdf:li stEvt:action="saved"
            stEvt:changed="/metadata"
            stEvt:instanceID="xmp.iid:e23d9eb9-9a97-4f42-ac42-596825b72993"
            stEvt:softwareAgent="LRTimelapse Pro 7.5.3 (Mac/ARM) - licensed to Dylan Johnston, "
            stEvt:when="2026-05-22T20:57:28-0700"/>
  </rdf:Seq>
</xmpMM:History>
```

The trailing comma+space in the license string is a literal `OBSERVED`
artifact — there is presumably a CSV-shaped license payload where a
secondary field is empty. Worth not relying on the exact format.

## `crs:MaskGroupBasedCorrections` — the LRT mask convention

This is the most important LRT-specific schema convention in the
entire XMP. The user's keyframe XMP carries 9 named corrections in
the `crs:MaskGroupBasedCorrections` rdf:Seq:

```
LRT Mask 1
LRT Mask 2
LRT Mask 3
LRT Mask 4
#LRT internal use (HG)
#LRT internal use (Deflicker)
#LRT internal use (Global)
LRT Mask 5
LRT Mask 6
```

`OBSERVED 2026-05-22` in `.lrt/proxy/DSC_5059.xmp`. All 9 entries are
written by LRT into every keyframe XMP, regardless of whether the user
has actually edited any of the masks.

Wegner's role-of-each-correction explanation on the LRT forum thread
"useless masks when I drag and drop photos from LRT into Lightroom"
(https://forum.lrtimelapse.com/Thread-useless-masks-when-i-drag-and-drop-photos-from-lrt-into-lightroom):

> *"The Masks marked as 'for internal use' are needed internally by
> LRTimelapse do do Deflicker, Holy Grail Wizard etc."*
>
> *"The other 6 masks are Masks that you can use to keyframe masks
> animations."*
>
> *"Please don't [delete them], otherwise LRTimelapse won't work as
> expected. Those masks get initialized by LRTimelapse."*

So:

- `#LRT internal use (HG)` carries the Holy Grail wizard's per-frame
  exposure-compensation contribution.
- `#LRT internal use (Deflicker)` carries the Visual Deflicker's
  per-frame exposure-correction contribution.
- `#LRT internal use (Global)` carries a global per-frame exposure
  contribution (purpose less clear; possibly the Auto Transition's
  exposure delta in a normalized form). `STATUS: PARTIALLY DOCUMENTED`
  — Wegner names HG and Deflicker explicitly but not "Global".
- `LRT Mask 1`–`LRT Mask 6` are user-available, mask-animatable
  corrections. Most users never touch them; LRT writes them as
  initialized placeholders.

## Per-correction attributes (each rdf:li under MaskGroupBasedCorrections)

Each correction carries the full LR local-correction attribute set
(`crs:LocalExposure2012`, `crs:LocalContrast2012`, `crs:LocalClarity`,
`crs:LocalDehaze`, `crs:LocalTemperature`, `crs:LocalTint`, etc.) plus
the inner `<crs:CorrectionMasks>` group with a `Mask/CircularGradient`
shape and per-mask geometry. The per-frame "exposure delta" payload
that we care about lives at `crs:LocalExposure2012` on the
internal-use correction.

`OBSERVED`: in this sample, every internal-use correction has
`crs:LocalExposure2012="0"` because the user has not run Visual
Deflicker or used Holy Grail on this sequence. To observe non-zero
values we would need a deflickered sample, which we do not yet have.

## Other LRT-relevant CRS conventions

- `crs:Sharpness="25"` is the LR out-of-camera default and is written
  into every frame by both LR and LRT, regardless of user intent. Our
  parser's `_has_meaningful_ops` heuristic excludes it.
- `<crs:ToneCurvePV2012>` with `[(0,0), (255,255)]` is the LR identity
  tone curve, also written by default. Same heuristic excludes it.
- `crs:ProcessVersion="11.0"` is the standard Adobe PV2012 (process
  version 11). `OBSERVED` — LRT does not write `crs:ProcessVersion=
  "15.0"` (PV5) even in 7.5.3, so the schema is locked to the PV2012
  era field set. This matters because PV5 added `crs:*PV2015` field
  variants that LRT-emitted XMPs do not carry.

## Provenance summary

| Claim | Source | Tag |
|---|---|---|
| Namespace URI `http://lrtimelapse.com/` | parser fixture vs real | VALIDATED 2026-05-22 |
| `lrt:Aperture/Iso/ShutterSpeed/Width/Height/Quality/ShootingMode/IsMergedHDR` attribute set | exiftool dump | OBSERVED 2026-05-22 |
| `xmp:Rating="4"` as Creative keyframe | LRT UI + parser validation | VALIDATED |
| `xmp:Rating="0"` as non-keyframe | parser validation | VALIDATED |
| `xmp:Rating` 1/2/3/5 sub-types | LRT UI only | UNDER-DOCUMENTED |
| 9-element MaskGroupBasedCorrections list | proxy XMP inspection | OBSERVED 2026-05-22 |
| Roles of `#LRT internal use (HG)`, `(Deflicker)` | Wegner forum quote | DOCUMENTED |
| Role of `#LRT internal use (Global)` | not Wegner-quoted | INFERRED |
| LRT Mask 1–6 user-animatable | Wegner forum quote | DOCUMENTED |
| LRT writes PV2012-era CRS fields, not PV5 | exiftool dump | OBSERVED 2026-05-22 |
