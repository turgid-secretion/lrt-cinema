# Darktable pixel pipeline

Authoritative reference for the order in which darktable applies image
operations (modules), and the scene-referred / display-referred split
that pipeline encodes.

All source citations pin to darktable commit
`635c0c55b64331481dffe30f937ba3fe72f83857` (master, fetched 2026-05).
Permalinks use that SHA; replace with a newer SHA when refreshing.

## The pipeline is iop-order, not history-stack order

The user manual occasionally implies that "the module order in the
darkroom panel is the order of pixel processing." That is misleading.
The actual execution order is governed by an integer `iop_order`
assigned to each module from a table per-image at history-read time.
The history-stack order in the XMP only controls the order of edits in
the undo timeline; the pipeline reorders edits by `iop_order` before
running them.

The current default table is `v50_order`, declared at
[`src/common/iop_order.c#L298-L415`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/common/iop_order.c#L298-L415).
It became the default for new RAW edits in darktable 5.0 (released
2024-12-21; release notes:
<https://github.com/darktable-org/darktable/releases/tag/release-5.0.0>).
Prior history versions still apply per-image:

| Constant | Value | Applies to |
|---|---|---|
| `DT_IOP_ORDER_LEGACY` | 1 | edits authored up to dt 2.6.3 |
| `DT_IOP_ORDER_V30` | 2 | edits starting dt 3.0 (RAW) |
| `DT_IOP_ORDER_V30_JPG` | 3 | edits starting dt 3.0 (non-linear LDR) |
| `DT_IOP_ORDER_V50` | 4 | **default for new RAW edits since dt 5.0** |
| `DT_IOP_ORDER_V50_JPG` | 5 | **default for new LDR edits since dt 5.0** |

Defined at [`src/common/iop_order.h#L128-L140`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/common/iop_order.h#L128-L140).
The constant `DT_IOP_ORDER_VERSION = 5` at
[`src/common/iop_order.c#L31`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/common/iop_order.c#L31)
is the table-count, not a history-format version.

When darktable reads an XMP it picks the table from
`Xmp.darktable.iop_order_version`; if absent it falls back to
`DT_IOP_ORDER_LEGACY`. Per-image custom order is also supported (a
serialized `iop_order_list` string stored in
`Xmp.darktable.iop_order_list`) — relevant for sidecars hand-edited by
power users, not for our emitter.

## The canonical execution order (v50, scene-referred)

Reading the table directly (operation, fractional order):

```
1.0  rawprepare        sensor unpack, black level, white level
3.0  temperature       channel multipliers (camera WB)
4.0  highlights        highlight reconstruction
5.0  cacorrect         CA correction on raw (pre-demosaic)
8.0  demosaic          Bayer/X-Trans -> RGB
13.0 lens              lensfun geometric/vignetting correction
13.5 cacorrectrgb      post-lens CA cleanup
15.0 ashift            perspective correction
17.0 clipping          crop + rotate
21.0 exposure          EV multiplier (the lrt-cinema entry point)
24.0 toneequal         tone equalizer (zone-based exposure)
28.0 colorin           input -> working profile (Lin Rec2020 by default)
28.5 channelmixerrgb   color calibration (since dt 3.6)
35.0 sharpen           USM-style sharpening
41.5 colorbalancergb   scene-referred color grading (lift/gamma/gain)
44.0 basecurve         display-referred curve (legacy)
45.3 sigmoid           display transform (current default)
45.5 agx               display transform (since dt 5.4)
46.0 filmicrgb         display transform (Aurelien Pierre)
48.0 tonecurve         display-referred contrast curve (legacy)
70.0 colorout          working -> output profile (the export ICC)
77.0 watermark         output overlay
78.0 gamma             final encoding (for display, not for export)
```

Full table at the permalink above. The `0.1` and `0.5` fractional
suffixes encode module-pair ordering rules; module priority is integer
in the runtime pipe.

## Scene-referred vs display-referred

In darktable terminology these are workflow modes, set via the conf
key `plugins/darkroom/workflow` (enum defined in
`data/darktableconfig.xml.in`):

- `scene-referred (sigmoid)` — **default in dt 5.0+**
- `scene-referred (filmic)`
- `scene-referred (AgX)` — added in dt 5.4
- `display-referred (legacy)`
- `none`

The split is by where in the pipeline the display transform sits. Up
to and including `colorout` (priority 70.0), values are **linear,
scene-referred** — they represent relative scene luminance, can exceed
1.0, and color is expressed in the working profile (Lin Rec2020 by
default; see [`src/iop/colorin.c#L71-L81`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/colorin.c#L71-L81)
where the colorin default workspace is `DT_COLORSPACE_LIN_REC2020`).

The display transform — one of `sigmoid` / `filmicrgb` / `agx` /
`basecurve` — maps that open-ended scene-referred signal to the
bounded `[0, 1]` of an output medium. Modules **after** the display
transform are **display-referred**: their math assumes clamped,
gamma-encoded data and is incorrect on scene-linear input. Hence the
ordering `colorbalancergb` (41.5, scene-referred) appears before
`sigmoid` (45.3) but `tonecurve` (48.0, display-referred) appears
after. Comments in the iop_order table label the transition explicitly
at [`src/common/iop_order.c#L374-L380`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/common/iop_order.c#L374-L380).

For cinema-native intermediate output, the right answer is to either
disable all display transforms (linear scene-referred Rec.2020 TIFF,
the `cinema-linear` preset) or to enable exactly one and pick its
display target deliberately.

## Default-active modules

A module is added to the history stack at image load if its
`default_enabled` flag is `TRUE`. The full inventory (grep across
`src/iop/*.{c,cc}` for `self->default_enabled =`) for darktable
master:

| Module | Default-enabled when | Source |
|---|---|---|
| `rawprepare` | raw image, not pre-normalized | rawprepare.c |
| `temperature` | raw image (AWB) | [temperature.c#L1184](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/temperature.c#L1184) |
| `highlights` | raw, non-monochrome | highlights.c |
| `demosaic` | raw or mono-sRAW | [demosaic.c#L1484](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/demosaic.c#L1484) |
| `colorin` | always | [colorin.c#L1690](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/colorin.c#L1690) |
| `colorout` | always | [colorout.c#L811](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/colorout.c#L811) |
| `flip` | always (honors EXIF orientation) | flip.c |
| `gamma` | always (display encoding) | gamma.c |
| `finalscale` | always | finalscale.c |
| `overexposed` | always (display-only guide) | overexposed.c |
| `rawoverexposed` | always (display-only guide) | rawoverexposed.c |

Workflow-conditional auto-applied modules (via `data.presets` table at
darktable startup, see [`develop.c#L1822-L2106`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/develop/develop.c#L1822-L2106)):

- Under `scene-referred (sigmoid)`: `sigmoid` preset "scene-referred
  default" auto-applies. Source: [`sigmoid.c#L227-L246`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/sigmoid.c#L227-L246).
- Under `scene-referred (filmic)`: `filmicrgb` analogous preset.
  Source: [`filmicrgb.c#L3179-L3199`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/filmicrgb.c#L3179-L3199).
- Under `display-referred (legacy)`: `basecurve` joins the auto-apply
  query (see `is_display_referred ? "" : "basecurve"` at
  [`develop.c#L1991`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/develop/develop.c#L1991)).

**Not** default-enabled under any workflow: `lens`, `cacorrect`,
`ashift`, `clipping`, `sharpen`, `colorbalancergb`, `tonecurve`,
`channelmixerrgb`, `toneequal`. These activate only via explicit XMP
history, a `.style`, or a user-saved auto-apply preset matching the
camera/lens.

## Implications for lrt-cinema

1. Our emitter must write modules in the priority order shown above.
   The `darktable:num` index in the XMP history is the history-stack
   order (display in undo), not the pipe order; getting them out of
   sequence still renders correctly because dt reorders by iop_order
   at history-read time, but matching the canonical sequence is good
   form.
2. A bare exposure-only XMP, with no `.style`, will render through
   the default-active list above plus whatever sigmoid preset
   auto-apply added at first edit. That preset is part of the dt
   user's environment, not our XMP — meaning two different darktable
   installations can render our sidecar differently if their
   `plugins/darkroom/workflow` differs.
3. To produce reproducible cinema-linear output, the runner needs
   to either bypass workflow auto-apply (`--apply-custom-presets 0`)
   or to over-write the sigmoid module with an explicit
   `enabled=false` history entry. See `EXPORT.md`.
