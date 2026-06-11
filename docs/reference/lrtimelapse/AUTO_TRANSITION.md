# Auto Transition: interpolation between keyframes

> Scope: what LRT's Auto Transition step computes, what it writes to
> disk, and what its interpolation model is.
>
> Sources: LRT internal workflow tutorial, complete workflow tutorial,
> a brief description from the LRTimelapse 7.4.1 release notes
> ("Auto Transition with 2 Keyframes functionality corrected"), and
> this project's own empirical pre-vs-post-Auto-Transition render
> comparison (commit `bf89107`, `docs/archive/VALIDATION.md` §
> "Empirical comparison"). LRT's spline algorithm is *not* documented
> by Wegner in any forum or tutorial we found.

## What Auto Transition does at the disk level

Per the LRT documentation:

> *"Auto Transition reloads the metadata for the keyframes and
> calculates the transitions for all images in between the keyframes."*
> — https://lrtimelapse.com/workflow/visual-workflow/
>
> *"Auto Transition calculates the developments for all images between
> the keyframes."*
> — https://lrtimelapse.com/tutorial/basic/internal/

Concretely, when the user clicks Auto Transition, LRT writes a new XMP
sidecar at every frame position (or updates the existing one)
containing per-frame interpolated values for every animated `crs:*`
field. After Auto Transition, every per-frame XMP carries a
non-default `crs:Exposure2012` (and any other field the user has
varied between keyframes). Before Auto Transition, only the
keyframe-position XMPs carry the user's edits; in-between frames carry
LR defaults.

This is the workflow split our parser handles (see VALIDATION.md "LRT
interpolation passthrough model"):

- **Auto Transition not run.** Parser ingests only the keyframes
  (Rating ≥ 1); `interpolate()` fills the gaps using our own linear or
  Catmull-Rom math.
- **Auto Transition run.** Parser ingests every per-frame XMP whose
  `crs:*` carries non-LR-default values; `interpolate()` returns the
  per-frame value verbatim (`_has_meaningful_ops` heuristic in
  `src/lrt_cinema/xmp_parser.py`).

## What fields does Auto Transition interpolate?

The LRT documentation does not enumerate the field set. From the
description ("calculates the developments for all images"), the
natural reading is that *every* `crs:*` field that varies between
adjacent keyframes is animated.

`OBSERVED 2026-05-22` in this project's empirical comparison: at
minimum, `crs:Exposure2012` was interpolated (the test set EV
changes between keyframes and observed asymmetric per-frame values
written to the in-between XMPs). We did not exhaustively check every
`crs:*` field for animation. `STATUS: PARTIALLY OBSERVED for the
exposure field, UNKNOWN whether all crs:* fields are interpolated or
only the "animatable" subset.`

## What interpolation curve does Auto Transition use?

This is the question Wegner's tutorial materials answer least
specifically. From a forum search by content phrase, multiple Wegner
posts allude to *"the nature of such curves [...] to be smooth over
to later keyframes"* — consistent with spline-shaped interpolation
rather than piecewise-linear, but not a formal algorithm name.

`OBSERVED 2026-05-22` (this project, commit `bf89107` /
`docs/archive/VALIDATION.md` Empirical comparison):

> "Test sequence: 5033-frame Nikon D750 timelapse, 6 LRT keyframes set
> at intervals of ~1006 frames. Two EV changes (0.0 → −0.5 → −1.0 →
> 0.0) over the first three keyframe pairs. 21 frames rendered around
> the −0.5 EV keyframe at frame 1006, both modes:
>
> | Frame | Our linear interp | LRT's interp (Auto Transition) | ΔEV |
> | ... | ... | ... | ... |
>
> LRT's interpolation is asymmetric around the keyframe (less negative
> approaching, more negative leaving), consistent with a smooth spline
> (Catmull-Rom or Hermite) rather than linear."

The observed asymmetry is the only empirical signal we have. It is
consistent with several spline families (uniform Catmull-Rom,
centripetal Catmull-Rom, monotonic cubic Hermite, smoothing spline)
and discriminating among them empirically would require denser
keyframe placements and more frames per segment.

`STATUS: PARTIALLY DOCUMENTED + PARTIALLY OBSERVED.` LRT's docs do
not name the spline. Our observation establishes that it is non-linear
and asymmetric around keyframes. The exact algorithm is `UNKNOWN`.

## What about non-numeric / categorical fields?

Some `crs:*` fields are not interpolatable in any well-defined sense:
`crs:WhiteBalance="As Shot"` vs `"Custom"`, `crs:CameraProfile`
strings, `crs:ToneCurveName2012` (`"Linear"`, `"Medium Contrast"`,
etc.).

The LRT documentation does not state what happens when adjacent
keyframes have different values for these fields. Plausible behaviors:
hold-from-prior, snap-at-midpoint, error-on-mismatch. `STATUS:
UNKNOWN.` Our parser currently uses hold-from-prior semantics
(`src/lrt_cinema/interpolation.py:74` `interpolate()` dispatch on
`InterpolationMode`), which has not been validated against an LRT
sample with mismatched categorical values between keyframes.

## What about the curve interpolation `crs:ToneCurvePV2012`?

This field is a point list (`<rdf:Seq>` of `(x, y)` pairs). How LRT
interpolates a varying tone curve between keyframes is not
documented. Possible models: point-wise interpolation of matched
points; resample-to-common-x-axis then interpolate y; cross-fade
between two LUT-shaped curves. `STATUS: UNKNOWN.` Our renderer *does*
apply a per-frame `crs:ToneCurvePV2012` (Stage 12 of the in-process
pipeline, per-channel), and our keyframe interpolation point-wise-lerps
the curve when adjacent keyframes carry matched point counts (otherwise
holds-from-prior; `ir.DevelopOps.blend`). But because LRT's own model
for interpolating a *varying* curve is undocumented, ours is a
best-effort guess rather than a verified match — validating it against
an LRT sample remains future-work.

## What does `Auto Transition with 2 Keyframes corrected` (LRT 7.4.1) mean?

The LRT 7.4.1 release notes (2025-08-29,
https://lrtimelapse.com/news/lrtimelapse-7-4-1-available/) list:

> *"Auto Transition with 2 Keyframes functionality corrected"*

This implies the previous behavior with exactly 2 keyframes had a bug.
The fix's specifics are not described. `STATUS: NOTED, NOT EXPLAINED`.
Practical implication: avoid the project's older-than-7.4.1 sequence
samples for any 2-keyframe interpolation analysis.

## Provenance summary

| Claim | Source | Tag |
|---|---|---|
| Auto Transition writes per-frame XMPs | LRT visual-workflow + internal-workflow tutorials | DOCUMENTED |
| All `crs:*` animated fields are interpolated | inferred from tutorial wording | PARTIALLY DOCUMENTED |
| Interpolation is non-linear / asymmetric around keyframes | this project's comparison (commit `bf89107`) | OBSERVED |
| Specific spline algorithm (uniform CR, centripetal CR, Hermite, …) | not documented anywhere | UNKNOWN |
| Behavior for non-interpolatable categorical fields | not documented | UNKNOWN |
| Behavior for varying `crs:ToneCurvePV2012` between keyframes | not documented | UNKNOWN |
| 2-keyframe edge case fixed in 7.4.1 | release notes | NOTED |
