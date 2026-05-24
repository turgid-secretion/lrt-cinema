# Filmic RGB vs Sigmoid (and AgX)

Darktable's three current scene-to-display tone-mapping modules,
their attributions, when they're auto-applied, and the design
trade-offs as the modules' authors describe them. Source citations to
commit `9402c65275bebebc4649c6dc91d3798d4bd63a0f`.

## The three modules

### filmic / filmicrgb

- Op name: `filmicrgb`. (The op `filmic` is the deprecated Lab-space
  predecessor; do not use.)
- Author: **Aurelien Pierre**.
- Introduced: dt 3.0 (December 2019) as `filmic`; renamed `filmicrgb`
  in dt 3.2 (June 2020) for the RGB-space rewrite.
- Source: [`src/iop/filmicrgb.c`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/iop/filmicrgb.c)
- Current modversion: 6 ([`filmicrgb.c#L66`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/iop/filmicrgb.c#L66))
- Design: filmic curve (S-curve based on a scaled logistic) with
  user-settable black-relative-exposure, white-relative-exposure,
  middle-grey, dynamic-range latitude, and per-region (shadows /
  highlights) contrast hardness. 29 parameters in the struct.
- Auto-apply: only under workflow `scene-referred (filmic)`
  ([`filmicrgb.c#L3179-L3199`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/iop/filmicrgb.c#L3179-L3199)).

### sigmoid

- Op name: `sigmoid`.
- Author: **jandren (Jakob Andrén)**. Introduced in the discussion
  thread at <https://discuss.pixls.us/t/new-sigmoid-scene-to-display-mapping/22635>
  on 2021-01-12.
- Introduced in dt: 4.4 (released 2023-06-21; verified via
  `gh api repos/darktable-org/darktable/releases`).
- Source: [`src/iop/sigmoid.c`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/iop/sigmoid.c)
- Current modversion: 3 ([`sigmoid.c#L34`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/iop/sigmoid.c#L34))
- Design: a "modified generalized log-logistic curve" with only two
  primary user-facing controls (contrast and skew), plus per-primary
  inset / rotation for color rendition shaping. 14 parameters.
- Auto-apply: only under workflow `scene-referred (sigmoid)`
  ([`sigmoid.c#L227-L246`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/iop/sigmoid.c#L227-L246)).
- Original dev quote, from jandren's first post in the thread above:
  > "tone mapping / look transform / scene to display conversion ...
  > based on a sigmoid function instead of a cubic spline like the
  > base curve."

### agx

- Op name: `agx`.
- Authors: **Blender Foundation** (original AgX OCIO config by Troy
  Sobotka, integrated into Blender 4.0); the dt port is community
  work, see commit history.
- Introduced in dt: **5.4** (released 2025-12-21; verified via
  `gh api repos/darktable-org/darktable/releases`). See release notes:
  <https://github.com/darktable-org/darktable/releases/tag/release-5.4.0>
  ("a new tone mapper based on Blender's AgX display transform was
  added, offering more extensive controls than Sigmoid with explicit
  exposure white/black points similar to Filmic RGB").
- Source: [`src/iop/agx.c`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/iop/agx.c)
- Auto-apply: only under workflow `scene-referred (AgX)`.

## The default in dt 5.5 master

The conf default is set in `data/darktableconfig.xml.in`:

```
<dtconfig prefs="processing" section="general">
  <name>plugins/darkroom/workflow</name>
  <type><enum>
    <option>scene-referred (sigmoid)</option>
    <option>scene-referred (filmic)</option>
    <option>scene-referred (AgX)</option>
    <option>display-referred (legacy)</option>
    <option>none</option>
  </enum></type>
  <default>scene-referred (sigmoid)</default>
  ...
```

dt 5.5 master defaults to **sigmoid** (per the conf default above).
The switch from filmic-default to sigmoid-default happened in the
dt 5.x line; the precise minor release should be cross-checked
against release notes if it matters for a downstream feature.
dt 4.4-4.8 defaulted to filmic.

## How they differ — design intent

The longest dev-attributed comparison is in the discussion thread
"Filmic vs Sigmoid — when to use which"
(<https://discuss.pixls.us/t/filmic-vs-sigmoid-when-to-use-which/41507>),
which has multiple pages of community + occasional dev replies. No
single dev (jandren, Aurelien Pierre, Hanika) has posted a definitive
"use X when Y, Z when W" statement that's not under active
revision. What the source code + module documentation jointly say:

- **filmic** exposes the photographer's mental model: explicit
  black-relative-exposure, white-relative-exposure, middle-grey,
  per-region contrast. It's a more controllable tone mapper.
  Recommended for difficult dynamic-range scenes (golden-hour
  sunsets, high-contrast forests) and for colorists who want
  parametric control.
- **sigmoid** is opinion-light: a single contrast + skew shapes the
  whole curve. Color rendition under saturation extremes is
  noticeably different (sigmoid preserves chrominance more
  aggressively under highlight clipping). Recommended for snapshot /
  high-volume work and for users who prefer a "good enough by
  default" tone mapper.
- **agx** is sigmoid-like in spirit (single curve shape, three-axis
  control) but with explicit black/white relative exposure points
  borrowed from filmic. Recommended for VFX / Blender-compatible
  workflows; less established in pure photo work.

From the dt user manual on sigmoid
(<https://docs.darktable.org/usermanual/development/en/module-reference/processing-modules/sigmoid/>):

> "The sigmoid module remaps tonal range using a modified
> generalized log-logistic curve to expand or contract dynamic range
> for display. ... Unlike filmic rgb or AgX, sigmoid should never be
> used alongside other display transform modules."

This holds for all three: exactly one display transform should be
active in a scene-referred pipeline. The iop_order table places them
adjacent (sigmoid 45.3, agx 45.5, filmicrgb 46.0) so the user can
swap by enabling one and disabling the others.

## Which is closer to Lightroom's tone mapping

A common request. Honest answer: **none of them** maps directly to
LR's PV2012 parametric tone math, which combines a global tone curve
with separate parametric Highlights / Shadows / Whites / Blacks
sliders modulating a region-adaptive contrast curve. The closest
mapping in dt's module set is:

1. `toneequal` (per-band exposure, 9-zone) for the Highlights /
   Shadows behavior. Pipeline position 24.0 (scene-referred,
   before colorin).
2. `tonecurve` (with parametric channel) for the master curve.
3. Either filmic or sigmoid as the display transform.

This is what V03_PLAN.md Track A2 calls "the PV2012 calibration
problem." It requires per-op response-curve fitting, not a textual
lookup; see the V03 plan for the proposed methodology.

## Empirical relevance to lrt-cinema's tone divergence

The task brief reports "dt looks linear/under-mapped vs LRT's
tone-mapped preview" with ΔL = -8. Two contributing factors:

1. **LRT's preview is LR-rendered**, which means LR's PV2012 tone
   math is in effect. Our dt sidecar carries only
   `crs:Exposure2012` (the one field lightroom.c maps and we emit),
   so dt applies no tone mapping beyond what auto-apply added
   (sigmoid with the workflow default). LRT preview has tone-mapped
   highlights and shadows; dt render has neutral.

2. **Whether sigmoid auto-applied to our test render**. If
   `--apply-custom-presets 1` (the dt-cli default) was set, sigmoid
   appears via the workflow preset. If `0`, no display transform
   runs and the render is fully linear from `colorout`. The ΔL = -8
   matches "linear scene-referred output vs sigmoid-mapped output
   on a midtone-heavy scene" — the linear render appears darker /
   flatter in display-encoded comparison.

To produce a render that's closer to LR / LRT preview tone-wise:
disable `--apply-custom-presets`, then either emit a tuned
`tonecurve` + `toneequal` history pair (Track A2 work), OR
deliberately emit a `sigmoid` history entry with the preset our
target output color treatment expects. The `cinema-linear` preset
should NOT do this (it wants flat scene-linear output); the
`stills-finished` preset is where the display transform should be
calibrated and emitted.

## Implications for lrt-cinema

- All three modules are workflow-conditional. Our headless render
  inherits whatever the dt-cli user has set for
  `plugins/darkroom/workflow` — that's a reproducibility hole.
  Always pass `--apply-custom-presets 0` (see EXPORT.md) to disable
  the workflow preset injection.
- For `cinema-linear` and `cinema-aces` (display transform off),
  ensure no sigmoid / filmic / agx / basecurve entries are emitted.
  With `--apply-custom-presets 0` and no display-transform in our
  XMP, the output is pure linear scene-referred from `colorout`.
- For `stills-finished`, pick exactly one display transform and
  emit a calibrated history entry for it. `sigmoid` with the
  "smooth" or "neutral gray" preset is the lowest-effort baseline;
  the preset params are in [`sigmoid.c#L227-L300`](https://github.com/darktable-org/darktable/blob/9402c65275bebebc4649c6dc91d3798d4bd63a0f/src/iop/sigmoid.c#L227-L300).
- LR-tone-match is **not** achievable with any single dt display
  transform. That's a Track A2 calibration problem (PV2012 math),
  not a tone-mapper-selection problem.
