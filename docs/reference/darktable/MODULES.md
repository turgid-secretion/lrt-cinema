# Darktable modules — per-module spec

For each module lrt-cinema reads from or emits to, this file pins:
introspection version (the `modversion` darktable writes in XMP), the
parameter struct at master, the LR-equivalent (if any), and notable
default values. Pulled from darktable master at SHA
`635c0c55b64331481dffe30f937ba3fe72f83857`.

Every module's modversion is declared via
`DT_MODULE_INTROSPECTION(N, dt_iop_<op>_params_t)`. When `N` bumps,
older XMP blobs of that op are routed through the module's
`legacy_params()` migration; if no migration exists for the gap, the
history entry is dropped at read time.

## exposure — EV multiplier

- Op name: `exposure`
- Source: [`src/iop/exposure.c`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/exposure.c)
- Current modversion: **7** ([`exposure.c#L47`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/exposure.c#L47))
- Params struct: [`exposure.c#L66-L75`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/exposure.c#L66-L75)
  - `dt_iop_exposure_mode_t mode` (int32, default MANUAL=0)
  - `float black` (-1.0..1.0, default 0.0)
  - `float exposure` (-18.0..18.0 EV, default 0.0)
  - `float deflicker_percentile` (default 50.0)
  - `float deflicker_target_level` (default -4.0)
  - `gboolean compensate_exposure_bias` (default FALSE)
  - `gboolean compensate_hilite_pres` (default TRUE) — **added in v7**
- LR equivalent: `crs:Exposure2012`
- Pipeline position: 21.0 (scene-referred)
- Note: the codebase's `xmp_emitter.py` writes `modversion="6"` and
  a 24-byte params blob. dt master v7 expects 26 bytes
  (`compensate_hilite_pres` added). dt's `legacy_params` migration
  will pad the 6-byte gap on read, but emitting the correct version
  is preferred. See `XMP_FORMAT.md`.

## temperature — channel multipliers (white balance)

- Op name: `temperature`
- Source: [`src/iop/temperature.c`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/temperature.c)
- Current modversion: **4** ([`temperature.c#L46`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/temperature.c#L46))
- Params struct ([`temperature.c#L68-L75`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/temperature.c#L68-L75)):
  - `float red` (0..8)
  - `float green` (0..8)
  - `float blue` (0..8)
  - `float various` (0..8) — fourth multiplier, X-Trans G2 / EOS-R RGGB
  - `int preset` (since v4) — preset index for GUI; ignored by pipe
- LR equivalent: `crs:Temperature` + `crs:Tint` (kelvin + green-magenta)
- Default-enabled: TRUE on raw images, FALSE on LDR
  ([`temperature.c#L1184`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/temperature.c#L1184))
- The conversion kelvin -> RGB multipliers requires either (a) the
  camera's DCP-style color matrix, or (b) the as-shot multipliers
  written by the camera EXIF. dt's GUI reads camera EXIF "AsShotWB";
  for kelvin sliders it uses a planckian-locus model + the camera
  matrix. The legacy_params migrations in temperature.c are
  particularly long (~600 lines) — the modversion has bumped 3 times,
  fields and semantics shifted each bump.

## colorin — input color profile

- Op name: `colorin`
- Source: [`src/iop/colorin.c`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/colorin.c)
- Current modversion: **7** ([`colorin.c#L58`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/colorin.c#L58))
- Params struct ([`colorin.c#L71-L81`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/colorin.c#L71-L81)):
  - `dt_colorspaces_color_profile_type_t type` (enum, default ENHANCED_MATRIX = 12)
  - `char filename[512]` — fixed buffer (`DT_IOP_COLOR_ICC_LEN` at colorin.c#L54)
  - `dt_iop_color_intent_t intent` (default PERCEPTUAL)
  - `dt_iop_color_normalize_t normalize` (default OFF)
  - `gboolean blue_mapping`
  - `dt_colorspaces_color_profile_type_t type_work` (default `DT_COLORSPACE_LIN_REC2020` = 4)
  - `char filename_work[512]`
- LR equivalent: implicit; LR uses its own profile selection bound
  to the camera, not a per-image XMP field.
- Notes: the historical 100-byte `filename` (`DT_IOP_COLOR_ICC_LEN_V5`,
  see [`colorin.c#L209`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/colorin.c#L209))
  was bumped to 512 in v6. Mis-sizing the struct silently truncates
  the ICC filename and falls back to the type-enum default.

## colorout — output color profile

- Op name: `colorout`
- Source: [`src/iop/colorout.c`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/colorout.c)
- Current modversion: **5** ([`colorout.c#L46`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/colorout.c#L46))
- Params struct ([`colorout.c#L63-L68`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/colorout.c#L63-L68)):
  - `dt_colorspaces_color_profile_type_t type` (enum; default SRGB=1)
  - `char filename[512]`
  - `dt_iop_color_intent_t intent` (default PERCEPTUAL)
- Set `type = DT_COLORSPACE_LIN_REC2020` (=4) for the cinema-linear
  preset; leave `filename` empty. See `EXPORT.md` for the
  type-enum-to-CLI-arg mapping and the alternative `--icc-type`
  override.

## demosaic — Bayer / X-Trans to RGB

- Op name: `demosaic`
- Source: [`src/iop/demosaic.c`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/demosaic.c)
- Current modversion: **6** ([`demosaic.c#L50`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/demosaic.c#L50))
- Default method: `DT_IOP_DEMOSAIC_RCD`
  ([`demosaic.c#L143-L157`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/demosaic.c#L143-L157)).
  RCD (Ratios Corrected Demosaicing) was made the default after dt 4.0;
  earlier defaults were AMaZE (Pierre's CFA) and PPG.
- Capture-sharpen sub-feature lives inside this module since dt 5.0
  (`cs_radius`, `cs_thrs`, `cs_boost`, `cs_iter`, `cs_center`,
  `cs_enabled` fields); explicit invocation is independent of the
  `sharpen` module.

## filmicrgb — display transform (Pierre)

- Op name: `filmicrgb` (NOT `filmic` — the latter is the deprecated
  Lab-space predecessor)
- Source: [`src/iop/filmicrgb.c`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/filmicrgb.c)
- Current modversion: **6** ([`filmicrgb.c#L66`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/filmicrgb.c#L66))
- Author: Aurelien Pierre. Introduced dt 3.0 as `filmic`, renamed
  `filmicrgb` in dt 3.2.
- Params struct: [`filmicrgb.c#L167-L198`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/filmicrgb.c#L167-L198) (29 fields, ~120 bytes)
- Default-enabled: FALSE ([`filmicrgb.c#L3159`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/filmicrgb.c#L3159));
  auto-applied only when workflow == `scene-referred (filmic)`.
- LR equivalent: none. ACR's parametric tone math (Highlights / Shadows
  / Whites / Blacks 2012) is closer to a tonecurve + locally adaptive
  tone equalizer than to a filmic curve.

## sigmoid — display transform (current default)

- Op name: `sigmoid`
- Source: [`src/iop/sigmoid.c`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/sigmoid.c)
- Current modversion: **3** ([`sigmoid.c#L34`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/sigmoid.c#L34))
- Author: jandren (Jakob Andrén); introduced dt 4.4. Original
  development thread: <https://discuss.pixls.us/t/new-sigmoid-scene-to-display-mapping/22635>
- Params struct: [`sigmoid.c#L57-L73`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/sigmoid.c#L57-L73) (14 floats + 2 enums)
- Default-enabled: FALSE; auto-applied only when workflow ==
  `scene-referred (sigmoid)` (the dt 5.5 master default; see
  [`sigmoid.c#L227-L246`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/sigmoid.c#L227-L246)).
- See `FILMIC_VS_SIGMOID.md` for the design trade-off.

## tonecurve — display-referred contrast curve

- Op name: `tonecurve`
- Source: [`src/iop/tonecurve.c`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/tonecurve.c)
- Current modversion: **5** ([`tonecurve.c#L48`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/tonecurve.c#L48))
- Params struct: [`tonecurve.c#L96-L106`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/tonecurve.c#L96-L106)
  - Three independent curves L/a/b (`tonecurve[3][DT_IOP_TONECURVE_MAXNODES]`)
  - Per-curve node count, interpolation type (default MONOTONE_HERMITE)
  - `tonecurve_autoscale_ab` (default `DT_S_SCALE_AUTOMATIC_RGB`)
  - `preserve_colors` (default AVERAGE norm)
- LR equivalent: `crs:ToneCurvePV2012` — sequence of `(x, y)` integer
  pairs in 0..255. The LR import path at
  [`lightroom.c#L965-L981`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/develop/lightroom.c#L965-L981)
  reads them into `data->curve_pts` then maps to dt tonecurve nodes.

## toneequal — tone equalizer

- Op name: `toneequal`
- Source: [`src/iop/toneequal.c`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/toneequal.c)
- Current modversion: **2** ([`toneequal.c#L127`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/toneequal.c#L127))
- Per-band EV multipliers across 9 bands from -8 EV to 0 EV; pipeline
  position 24.0 (scene-referred, applied before colorin).
- LR equivalent: closest approximation to LR's parametric Shadows /
  Highlights / Whites / Blacks 2012 controls; see `LR_IMPORT.md` for
  why dt's own LR importer does NOT use this mapping (it drops those
  LR fields).

## colorbalancergb — scene-referred color grading

- Op name: `colorbalancergb`
- Source: [`src/iop/colorbalancergb.c`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/colorbalancergb.c)
- Current modversion: **5** ([`colorbalancergb.c#L52`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/colorbalancergb.c#L52))
- Params struct: [`colorbalancergb.c#L60-L106`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/colorbalancergb.c#L60-L106).
  v1 + v2 (brilliance) + v3 (mask_grey_fulcrum) + v4 (vibrance,
  grey_fulcrum, contrast) + v5 (`saturation_formula` enum).
- LR equivalent: `crs:Saturation` + `crs:Vibrance` map closest to the
  `saturation_global` and `vibrance` channels. The shadows/midtones/
  highlights lift/gamma/gain controls have no LR analog (LR's color
  grading uses a separate `crs:Color*` field set this module doesn't
  match 1:1).

## sharpen — unsharp mask

- Op name: `sharpen`
- Source: [`src/iop/sharpen.c`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/sharpen.c)
- Current modversion: **1** ([`sharpen.c#L39`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/sharpen.c#L39)) —
  shipped at modversion 1 since pre-2.0 and never bumped.
- Params: three floats — `radius` (0..99, default 2.0), `amount`
  (0..2.0, default 0.5), `threshold` (0..100, default 0.5). 12 bytes.
- LR equivalent: `crs:Sharpness` (master amount only; LR has
  sub-knobs for radius / detail / masking that don't map 1:1).
- Pipeline position: 35.0 (scene-referred, after the working color
  conversion).

## diffuse — diffuse-or-sharpen

- Op name: `diffuse`
- Source: [`src/iop/diffuse.c`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/diffuse.c)
- Current modversion: **2** ([`diffuse.c#L46`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/diffuse.c#L46))
- Introduced dt 3.8 as a PDE-based contrast / denoise / capture-sharpen
  alternative to `sharpen`. Pipeline position 28.5, sits before
  channelmixerrgb in the canonical order.
- LR equivalent: partial overlap with LR's Texture / Clarity /
  Dehaze; dt's `lightroom.c` does not map any of those to `diffuse`.

## cacorrect — pre-demosaic CA correction

- Op name: `cacorrect`
- Source: [`src/iop/cacorrect.c`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/cacorrect.c)
- Current modversion: **2** ([`cacorrect.c#L40`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/cacorrect.c#L40))
- Default-enabled: FALSE (twice asserted, at
  [`cacorrect.c#L1256`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/cacorrect.c#L1256)
  and [`cacorrect.c#L1291`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/cacorrect.c#L1291)).
  Activation is user-opt-in even with full EXIF.
- Pipeline position: 5.0 (pre-demosaic).

## lens — lensfun geometric / vignetting

- Op name: `lens`
- Source: [`src/iop/lens.cc`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/lens.cc)
- Current modversion: **10** ([`lens.cc#L68`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/lens.cc#L68)) —
  dt's most-revved op, reflecting lensfun API churn.
- Default-enabled: **never automatically enabled**. The file contains
  no `self->default_enabled = TRUE`; grep confirms.
- `reload_defaults` ([`lens.cc#L3435-L3534`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/lens.cc#L3435-L3534))
  populates the GUI controls from EXIF + lensfun DB lookup, but never
  flips `default_enabled`. See `LENS_CORRECTION.md`.
- LR equivalent: `crs:LensProfileEnable` etc. — dt's `lightroom.c`
  does NOT match `LensProfileEnable`; the import path leaves dt's
  lens module disabled regardless of the LR sidecar's settings.

## ashift — perspective correction

- Op name: `ashift`
- Source: [`src/iop/ashift.c`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/ashift.c)
- Current modversion: **5** ([`ashift.c#L108`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/ashift.c#L108))
- Default-enabled: FALSE
  ([`ashift.c#L5662`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/iop/ashift.c#L5662)).

## Cross-cutting facts

- The "version" attribute of an XMP `<op_params>` is the per-module
  introspection version (the `DT_MODULE_INTROSPECTION` N), NOT a
  global schema version. Each module's history of bumps is in its own
  `legacy_params()` function.
- The `params` blob is base64 in dt 5.x XMPs (since the move to
  Exiv2-managed XMP), hex in older sidecars produced by `dt_exif_*`.
  Read path tolerates both ([`exif.cc#L3635-L3642`](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/common/exif.cc#L3635-L3642)).
- Modules without a current legacy_params for a given old N will
  return NULL from `legacy_params`, which the loader treats as "drop
  the history entry, warn." Recovering the history loses that edit
  silently — see [`exif.cc` history-entry handling around L4250](https://github.com/darktable-org/darktable/blob/635c0c55b64331481dffe30f937ba3fe72f83857/src/common/exif.cc#L4250).

## Implications for lrt-cinema

- The emitter currently hardcodes `EXPOSURE_MODVERSION="6"` and a
  24-byte payload. dt master expects v7 + 26-byte payload (two extra
  gbooleans). dt's legacy_params can read the v6 blob but the emitter
  should be updated to match the running darktable's actual version.
- Same for `TEMPERATURE_MODVERSION="3"` (master is v4). dt's
  legacy_params chain handles v3, but our emitter writes neutral
  multipliers regardless of source kelvin — see `LR_IMPORT.md` for
  the proper kelvin-to-multipliers source.
- The `BLENDOP_VERSION = "11"` constant is plausible-correct
  (blendop is independent of any iop). Verify against
  `src/develop/blend.h`'s `DT_DEVELOP_BLEND_VERSION`.
