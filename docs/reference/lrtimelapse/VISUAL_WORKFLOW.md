# Visual Workflow: Visual Previews, Visual Deflicker, Holy Grail Wizard

> Scope: what the three "Visual *" workflow buttons do, what files
> appear on disk when each runs, and what each step's pre-conditions
> are.
>
> Sources: LRT *Complete (with Lightroom)* workflow tutorial
> (https://lrtimelapse.com/workflow/visual-workflow/), LRT *Internal
> (no Lightroom)* tutorial (https://lrtimelapse.com/tutorial/basic/internal/),
> the LRT 7 release post for the Visual-Workflow framing, and
> empirical inspection of disk artifacts on 2026-05-22.

## "Visual Workflow" naming

LRT 7 introduces the "Visual Workflow" branding as a unified label
for the workflow buttons that depend on LRT-rendered (not
Lightroom-rendered) preview frames. The relevant quote, from
https://lrtimelapse.com/workflow/visual-workflow/:

> *"For most tasks, the new universal Visual Workflow can be used. The
> workflow involves several key steps including keyframe creation,
> editing, auto-transition calculations, and visual deflickering."*

The "Visual" qualifier means *uses the `.lrt/previews/*.lrtpreview`
JPEGs that LRT renders by itself*. None of these steps requires
Lightroom Classic to be running; all are LRT-internal. This was a
question worth resolving explicitly because the LRT UI also has
"Export & Render (Lightroom)" buttons that *do* require LR — see
EXPORT_PATHS.md for that branch.

## Visual Previews

The Visual Previews button generates the `.lrt/visual/*.lrtpreview`
JPEGs (1024×684, see PREVIEW_GENERATION.md for the format). Two
modes:

- **Keyframes only** (initial state). When first clicked, LRT
  generates a visual preview for each keyframe.
  > *"A visual preview will be generated for the keyframes only."*
- **All Frames** (subsequent state). When clicked again, LRT switches
  to all-frames mode and generates a 1024×684 JPEG for every frame in
  the sequence.
  > *"Visual Previews will now switch to 'All Frames' mode and start
  > developing the previews."*

The luminance progression visualization changes accordingly: blue
curve before Visual Previews (showing the *undeveloped* camera-JPEG
brightnesses), pink curve after Visual Previews (showing
*LRT-developed* brightness curve including all keyframe edits and Auto
Transition interpolation):

> *"Undeveloped camera previews are displayed in blue, developed
> visual previews are displayed in pink."*

The `pink` curve is the LRT-internal renderer's output. This makes
the pink curve the appropriate reference for "what LRT thinks the
sequence will look like after rendering."

`OBSERVED 2026-05-22`: in the user's sample, `.lrt/visual/` contains 6
files corresponding to the 6 keyframes. This is the post-Initialize,
pre-Visual-Previews-All-Frames state. `lrtsequence.json`'s
`workflowVisualPreviews: 0` confirms.

## Visual Deflicker

The Visual Deflicker button reads the LRT-developed pink luminance
curve and computes per-frame exposure-delta corrections to smooth it.
Per the tutorial:

> *"Drag the Smoothing slider until the green smoothed curve looks
> like you would like the pink curve to be."*
>
> *"The green curve should smooth any short term flicker but leave mid
> and long term changes alone."*
>
> *"Multi pass deflicker will do several lossless (!) passes to
> improve the deflicker."*

Output disk artifact: per-frame XMPs get an updated
`crs:LocalExposure2012` value on their `#LRT internal use (Deflicker)`
correction (see XMP_SCHEMA.md). Each multi-pass run adds to the
existing delta; the "Refine" mode uses the previous run as baseline:

> *"How the 'Refine' function uses the last deflicker as a basis and
> adds to it to get results closer to the smoothing line"*
> (paraphrased from multiple forum threads on the LRT forum).

Pre-conditions: Visual Previews → All Frames must have been completed,
otherwise there is no pink curve to deflicker against. `STATUS:
DOCUMENTED`.

The smoothing-factor numeric range is exposed in `lrtsequence.json`:

```
"deflickerSmoothingFactor": 0.125,
"deflickerSmoothingFactorLegacy": 0.125,
"deflickerPasses": 2,
"deflickerAccuracy": 0,
"deflickerIgnoreBlacksWhites": 0,
"deflickerMultipass": false,
"deflickerConstant": false,
"deflickerConstantLegacy": false
```

Values are `OBSERVED 2026-05-22` defaults. The semantic of each is not
formally documented; field names are self-describing.

## Holy Grail Wizard

Per the tutorial:

> *"Holy Grail Wizard can only be enabled for sequences shot according
> to the 'Holy Grail' approach"*  (i.e. manual exposure adjustment
> during shooting, e.g. day-to-night transitions where the user
> increased the ISO/aperture/shutter mid-sequence).
>
> *"An orange compensation curve will be calculated to compensate for
> the Holy Grail camera adjustments."*
>
> *"The orange curve should be as close to the horizontal middle line
> as possible."*

LRT detects exposure-adjustment events by reading EXIF metadata
across the sequence (`lrt:Iso`, `lrt:ShutterSpeed`, `lrt:Aperture` —
see XMP_SCHEMA.md) and looking for step changes that indicate the
user adjusted exposure on-camera. The wizard then computes a
compensating exposure ramp on the digital side.

Output disk artifact: per-frame XMPs get an updated
`crs:LocalExposure2012` value on their `#LRT internal use (HG)`
correction.

Pre-condition: LRT must detect Holy Grail-style exposure shifts. If
not, the wizard button is greyed out (in the user's sample,
`lrtsequence.json` has `workflowHolyGrailWizard: -1`, signifying
"not applicable for this sequence" — the sequence is constant-exposure
manual mode, ISO 100, f/13, 0.5s).

`STATUS: DOCUMENTED conceptually; the exact compensation-curve
calculation algorithm is not specified by LRT.`

## Holy Grail Wizard's "Optimize" feature (LRT 7)

The LRT 7 release post lists:

> *"Holy Grail Wizard includes new 'Optimize' feature for automatic
> slider optimization"*
> (https://lrtimelapse.com/news/lrtimelapse-7/)

Mechanism not specified.

## Step ordering and dependencies

From the LRT Internal tutorial *"Normally, you simply go through them
from left to right, so you always know exactly what to do next"*:

1. Initialize / Keyframes Wizard — assigns `xmp:Rating="4"` to
   creative keyframes.
2. Holy Grail Wizard (optional; only enabled if HG detected).
3. Save Metadata — writes XMP sidecars to disk.
4. Internal Editor — user edits the keyframe XMPs.
5. Auto Transition — interpolates between keyframes; updates per-frame
   XMPs.
6. Visual Previews — generates `.lrt/visual/*.lrtpreview` JPEGs.
7. Visual Deflicker — adjusts per-frame `#LRT internal use (Deflicker)`
   exposure corrections.
8. Export & Render (Internal or Lightroom) — see EXPORT_PATHS.md.

Re-running an earlier step invalidates subsequent steps' output in
principle. The exact invalidation rules are `UNKNOWN` (LRT does not
document them; we have not exhaustively tested).

## What our project depends on

- We *do not* run Visual Previews / Visual Deflicker / Holy Grail
  Wizard ourselves. Our project consumes the XMP-side artifacts of
  whichever workflow steps the user has run in LRT.
- The `#LRT internal use (HG)` and `#LRT internal use (Deflicker)`
  exposure deltas are surfaced by our `xmp_parser` and applied to the
  per-frame `DevelopOps` (see SCOPE.md and `src/lrt_cinema/
  interpolation.py:92` `apply_holy_grail_ramps`).
- The Visual Workflow framing matters because it confirms LRT can
  produce a complete render-pipeline-ready set of per-frame XMPs
  *without* Lightroom Classic in the loop. That is the architectural
  premise our project relies on.
