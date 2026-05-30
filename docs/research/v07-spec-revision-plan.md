# v0.7 SPEC Revision Plan

**Status:** **SUPERSEDED 2026-05-28** by
[v07-beta-xml-deadend.md](v07-beta-xml-deadend.md) for Phase 2 onward.
v0.7.0 (Phase 1 / γ — `cinema-linear-finished`) ships as planned;
Phase 2 (β-XML / `cinema-linear-master`) and the §5.5 v0.7.x
free-upgrade roadmap (X1–X6: HSL, Color Grading wheels, parametric
tone, user masks, Texture, Clarity) are **deferred to v0.8** pending a
new carrier format. Empirical verification proved Resolve does not
preserve per-frame grade keyframes through any documented import path
— see the dead-end doc for the audit log.

Read this document for the Phase 1 (γ) implementation that shipped.
Treat §3 (Stage 7 emission), §4 (Resolve XML schema), §5 Phase 2, and
§5.5 increments as **historical context** for the v0.8 re-investigation,
not as a v0.7 plan.

**Prior status:** Locked 2026-05-28. Supersedes the CDNG-as-headliner
plan in [v07-emission-format.md](v07-emission-format.md).
**Parent docs:**
[v07-emission-criteria-reframed.md](v07-emission-criteria-reframed.md) +
[v07-emission-keyframe-vs-recovery.md](v07-emission-keyframe-vs-recovery.md).

User-locked decisions (2026-05-28):

1. **Resolve-only sidecar output is OK** — cross-NLE users get the
   scene-referred EXR sequence without the auto-applied grade and
   re-grade by hand.
2. **Ship the simple change first.** γ (EXR DWAB at Stage 13) ships
   in v0.7.0 as a one-week pivot. β-XML (Stage 9 EXR + Resolve project
   sidecar) ships in v0.7.x.
3. **Q3 was the wrong framing.** This repo's mandate is to preserve
   what LRT can keyframe — full stop. Not what one user happens to
   keyframe. The keyframable set inventory in §2 below is authoritative.
4. **Per-frame LUT sidecar is rejected** on storage grounds. 1000
   frames × 33³ LUT ≈ 36 MiB on top of the EXR sequence; 5000 frames ≈
   180 MiB. Re-inflates 1000-frame sequences toward TiB-range when
   combined with the EXR plates. Resolve XML sidecar is the chosen
   carrier — per-frame keyframe storage is ~1 KiB per parameter per
   sequence (negligible).

---

## 1. The two presets

### Preset A — `cinema-linear-finished` (γ target, v0.7.0)

The 1-week pivot from v0.6 cinema-aces. Same pipeline output as today,
different compression.

- **Pixels:** Stage 13 output. ProPhoto(D50) → Rec.2020(D65) Bradford
  CAT applied. All LRT-authored keyframed parameters baked into
  pixels exactly as v0.6 does.
- **Container:** OpenEXR.
- **Channels:** 16-bit half-float RGB.
- **Compression:** DWAB (lossy, visually lossless cinema-standard).
- **No sidecar.**
- **Code change:** `output.py` write_exr_linear_rec2020 — swap PIZ
  for DWAB, change channel type from float to half. ~5–10 LOC.
- **Recovery story:** none above the tone curve's clip point. Honest
  positioning: "look-locked cinema deliverable, smaller than v0.6
  cinema-aces."
- **LRT keyframe preservation:** complete (everything v0.6 currently
  handles, baked into pixels).
- **Validation:** new gate — ΔE2000 < 0.5 between PIZ-half and
  DWAB-half outputs on gym + rose (the lossy-but-visually-lossless
  gate).
- **CLI:** `--preset cinema-linear-finished` (default).
- **Deprecation:** `cinema-aces` continues to work for one release
  cycle, prints a one-time deprecation warning suggesting
  `cinema-linear-finished`.

### Preset B — `cinema-linear-master` (β-XML target, v0.7.x)

The recoverability-and-keyframes answer.

- **Pixels:** new emission point at **end of Stage 7** (after Adobe
  ExposureRamp + LookTable, before any tonal-shape ops). See §3 for
  why Stage 7 not Stage 9.
- **Container:** OpenEXR.
- **Channels:** 16-bit half-float RGB.
- **Compression:** DWAB.
- **Sidecar:** one Resolve project file per sequence (`*.drxml`)
  containing per-frame keyframes for the modeled LRT keyframable
  parameters (§2.A below).
- **Code change:** new emission function emitting Stage 7 output +
  new `resolve_xml.py` module that maps `DevelopOps` keyframe series
  → Resolve grade-page keyframe tracks. ~500–1000 LOC.
- **Recovery story:** full half-float HDR (30 stops of headroom).
  Highlights and shadows recoverable to half-float precision.
- **LRT keyframe preservation:** §2.A set carried as Resolve grade
  keyframes; §2.B set carried only as text comments / XMP block in
  the project file (not auto-applied, but documented).
- **Validation:** new gate — ΔE2000 < 1.0 between Resolve's decode
  of (EXR + .drxml) and v0.6 cinema-linear reference on gym + rose.
- **CLI:** `--preset cinema-linear-master`.

---

## 2. The LRT keyframable parameter inventory

What LRT can keyframe (per LRT's own documentation + per the repo's
[XMP_SCHEMA.md](../reference/lrtimelapse/XMP_SCHEMA.md)):

### §2.A — Modeled by lrt-cinema v0.6 today

| LRT field | DevelopOps attr | v0.6 status | Stage 7 emission carries? | β-XML Resolve target |
|---|---|---|---|---|
| `crs:Exposure2012` | `exposure_ev` | applied | folded into BaselineExposure → applied at Stage 7 | Gain (RGB equal), keyframed |
| `crs:Contrast2012` | `contrast` | applied | not yet at Stage 7 → carry as keyframe | Contrast + Pivot, keyframed |
| `crs:Temperature` | `temperature_k` | applied via AsShotNeutral | applied at Stage 2 → in pixels | Color Temp, keyframed (Camera Raw or Gain per channel) |
| `crs:Tint` | `tint` | applied | applied at Stage 2 → in pixels | Tint, keyframed |
| `crs:Blacks2012` | `blacks` | applied (Stage 11) | not yet at Stage 7 → carry as keyframe | Lift, keyframed |
| `crs:Saturation` | `saturation` | applied (Stage 12) | not yet at Stage 7 → carry as keyframe | Saturation, keyframed |
| `crs:Vibrance` | `vibrance` | applied (Stage 12) | not yet at Stage 7 → carry as keyframe | Color Boost, keyframed |
| `crs:ToneCurvePV2012` (point) | `tone_curve` | applied (Stage 11) | not yet at Stage 7 → carry as keyframe | Custom Curves > RGB, keyframed |
| `crs:Sharpness` | `sharpness` | no-op | no-op | n/a (no Resolve grade primitive — drop) |
| `crs:Highlights2012` | `highlights` | dropped (closed-source PV5) | drop (inherited) | drop |
| `crs:Shadows2012` | `shadows` | dropped | drop | drop |
| `crs:Whites2012` | `whites` | dropped | drop | drop |
| `#LRT internal use (HG)` mask | `mask_corrections.hg` | applied (Stage 2, per-frame exposure delta) | applied at Stage 7 → in pixels | n/a (already baked) |
| `#LRT internal use (Deflicker)` mask | `mask_corrections.deflicker` | applied (Stage 2) | applied at Stage 7 → in pixels | n/a (already baked) |
| `#LRT internal use (Global)` mask | `mask_corrections.global` | applied (Stage 2) | applied at Stage 7 → in pixels | n/a (already baked) |

This is the **v0.7 scope.** Everything currently-modeled either rides
in Stage 7 pixels (Class A) or rides as Resolve grade keyframes in the
.drxml (Class B). No data loss vs v0.6 for the modeled set.

### §2.B — Keyframable by LRT but NOT modeled by lrt-cinema today

Per LRT's documentation, LRT can keyframe the full Lightroom develop
module (PV2012 era).

**Architectural distinction surfaced by user (2026-05-28):**

LRT itself doesn't reimplement Adobe's develop math. External
workflow delegates to Lightroom Classic; internal workflow **bundles
the Adobe Camera Raw engine** (per LRT's own docs: "the internal
editor uses the same tools as Lightroom and Adobe Camera Raw"; "uses
the Adobe RAW engine in the backend"). Wegner's actual implementation
surface is sequence-level — keyframing UI, Holy Grail wizard, Visual
Deflicker, the 9-mask convention. The per-frame develop math is
100% Adobe's.

lrt-cinema cannot do the same. Adobe Camera Raw SDK is not
redistributable as Apache-2.0 software, so v0.6 had to reimplement
Adobe's documented DNG 1.7.1 pipeline from scratch (`pipeline.py` +
`develop_ops.py` + `dcp.py`). Proprietary ops Adobe does not document
(PV5 parametric tone, Clarity, Dehaze, Texture, Color Grading wheels'
exact math) are structurally unreachable for us via the pipeline.

**The β-XML preset routes around this constraint.** β-XML does not
need lrt-cinema to *render* §2.B params — only to *translate* LRT XMP
values into Resolve grade-page keyframes. Resolve applies the math.
The limit becomes "is there a clean Resolve primitive the LRT param
maps to?", not "do we have Adobe's render code?".

Resolve-mapping audit:

| LRT field | Resolve grade primitive | Mapping fidelity | β-XML treatment |
|---|---|---|---|
| `crs:HueAdjustment*` ×8 | HSL Curves > Hue vs Hue | **exact** | v0.7.x free upgrade (parser + XML mapping; no render math) |
| `crs:SaturationAdjustment*` ×8 | HSL Curves > Hue vs Sat | **exact** | v0.7.x free upgrade |
| `crs:LuminanceAdjustment*` ×8 | HSL Curves > Hue vs Lum | **exact** | v0.7.x free upgrade |
| `crs:ColorGrade{Mid/Shadow/Highlight/Global}{Hue/Sat/Lum}` ×12 | Color Wheels (Lift/Gamma/Gain/Offset) | **exact** | v0.7.x free upgrade |
| Tone Curve parametric (`crs:Parametric*Split` + `crs:Parametric{Shadows/Darks/Lights/Highlights}`) | Custom Curves with knee points | **exact** | v0.7.x free upgrade |
| `crs:Texture` | Texture node (Resolve 18+) | approximate (different math) | v0.7.x best-effort with caveat |
| `crs:Clarity2012` | Midtone Detail | approximate | v0.7.x best-effort with caveat |
| `crs:Dehaze` | (no direct primitive) | **none** | drop with warning permanently |
| `crs:Highlights2012` / `Shadows2012` / `Whites2012` (LR PV5 parametric tone) | (Custom Curves is lossy approximation; doesn't match LR) | poor | drop with warning permanently (same as v0.6) |
| `LRT Mask 1`–`LRT Mask 6` (user-animatable masks) | Power Window + per-window grade | exact (geometry parse non-trivial) | v0.7.x scope decision (Increment X6) |
| Per-mask `crs:Local*` ops on user masks | grade per Power Window node | inherits user-mask decision | as above |
| Lens corrections, vignetting (post-crop), grain, B&W conversion, calibration | intentionally not part of develop scope | n/a | drop permanently |
| Noise reduction | not keyframed in typical timelapse | n/a | drop |

**Free upgrades** (exact Resolve mapping, no render math): HSL grading
(24 fields), Color Grading wheels (12 fields), parametric tone curve
(7 fields). **43 keyframable params** that ride to Resolve without
adding any render-pipeline work — only XMP parser + Resolve XML
mapping extensions.

**Best-effort upgrades** (approximate Resolve mapping): Clarity,
Texture. Ship with documented fidelity caveats.

**Permanent drops** (no Resolve primitive): Dehaze, LR PV5 tone math
(Highlights/Shadows/Whites). Same status as v0.6 — render-time
warning.

The v0.7 SPEC's **Phase 1 + Phase 2 scope remains §2.A only** — ship
the headliner first. The expanded §2.B coverage ships as v0.7.x
increments detailed in §5.5 below.

The β-XML format is **additive** — each v0.7.x increment extends the
Resolve XML schema with new keyframe tracks without changing existing
ones. This is a design constraint on `resolve_xml.py`, not a runtime
requirement.

### §2.C — Render-time warnings v0.7 prints

When the LRT XMP carries any of these fields with non-default values,
v0.7 surfaces a one-line warning (same pattern as v0.6's
Highlights2012/Shadows2012/Whites2012 dropping):

```
[lrt-cinema] LRT-authored param 'crs:Clarity2012=20' on frame DSC_4053.NEF
  is not yet modeled by lrt-cinema; dropped at render. Add a node on the
  Resolve grade page if you need it.
```

This keeps users honest about what travels and what doesn't.

---

## 3. Why Stage 7 emission, not Stage 9

The earlier doc suggested Stage 9 (post-LookTable, pre-ProfileToneCurve)
for β. Reconsidered against the LRT keyframable inventory:

| Stage | What's applied | What's NOT applied | Class A LRT params still keyframable in sidecar |
|---|---|---|---|
| Stage 9 | demosaic, AsShotNeutral, CCM, ProPhoto, HSM, ExposureRamp, LookTable | ProfileToneCurve, LR PV2012 ops | Contrast, Saturation, Vibrance, ToneCurve, Blacks (all 5) |
| **Stage 7** | demosaic, AsShotNeutral, CCM, ProPhoto, HSM, ExposureRamp | LookTable, ProfileToneCurve, LR PV2012 ops | LookTable contribution + the Class A set |

Stage 7 keeps the LookTable application out of pixels. LRT's "Look"
field (when keyframed) maps to the DCP LookTable. By emitting before
LookTable application, we preserve the option for downstream tools to
load a different DCP / different LookTable per frame if they want.

But — LRT users rarely keyframe the DCP look itself; the LookTable
is sequence-static (one DCP per camera body). So Stage 9 vs Stage 7
makes essentially no difference for keyframe coverage; the choice is
between "extra recoverability headroom" (Stage 7) and "simpler emission
math" (Stage 9).

**Recommendation: Stage 7.** The extra recoverability is free —
LookTable is a sequence-level operation, applied once on import via
Resolve's input transform if needed. The downstream user benefits from
seeing the data pre-LookTable.

If Stage 7 turns out to introduce a regression vs v0.6's < 1 ΔE
reference (because LookTable contributes meaningfully to the Adobe
DCP color science), fall back to Stage 9. Phase-2 validation gate
checks this.

---

## 4. Resolve project XML schema (.drxml)

Each sequence emits ONE Resolve project file alongside the EXR
sequence. The file:

- References the EXR sequence by relative path
- Sets project Color Management to YRGB (NOT RCM — RCM would bypass
  the Camera Raw decode, which we don't need here since pixels are
  half-float scene-referred)
- Sets Timeline color space to Rec.2020 Linear
- Imports the EXR sequence as one clip
- Adds keyframes on the **grade page** for the §2.A Class B parameters

Resolve grade-page keyframes are accessed via the `dynamic` parameter
within a node. Per-frame interpolation between keyframes is Resolve's
native job once we set the values at each keyframe time. We don't
need to write per-frame entries — only LRT-authored keyframes (which
are sparse in time).

Concrete XML shape (sketched; final schema verified against Resolve
20.3's XML import):

```xml
<DaVinciResolveProject>
  <Timeline name="lrt-cinema-{sequence_id}">
    <Clip>
      <Source href="frames/IMG_%05d.exr"/>
      <ColorSpace>Rec.2020 Linear</ColorSpace>
      <Grade>
        <Node id="0">
          <DynamicGain frameOffset="0" rgb="1.000"/>
          <DynamicGain frameOffset="42" rgb="1.250"/>  <!-- LRT keyframe @ frame 42, Exposure2012=+0.32 EV -->
          <DynamicGain frameOffset="156" rgb="0.890"/>
          ...
          <DynamicContrast frameOffset="0" amount="0.0" pivot="0.5"/>
          <DynamicContrast frameOffset="42" amount="0.12" pivot="0.5"/>
          ...
          <DynamicCustomCurve channel="rgb" frameOffset="42">
            <Point x="0.0" y="0.0"/>
            <Point x="0.25" y="0.18"/>
            ...
          </DynamicCustomCurve>
        </Node>
      </Grade>
    </Clip>
  </Timeline>
</DaVinciResolveProject>
```

(Schema is illustrative — real Resolve XML uses its own conventions
which Phase 2 implementation verifies via round-trip: write, import
into Resolve, export, diff.)

**The XML keyframe density** = LRT keyframe density. For a typical
sequence with 5–10 LRT keyframes, each carrying ~9 modelled parameters,
the XML is ~3–20 KiB. Negligible storage.

---

## 5. Implementation plan

### Phase 1 (v0.7.0) — ship `cinema-linear-finished` (γ)

1. **Modify `output.py`:**
   - Add `bit_depth` argument to `write_exr_linear_rec2020` accepting
     `"float"` or `"half"` (default flips to `"half"`).
   - Add `compression` argument accepting `"piz"`, `"zip"`, `"dwab"`
     (default `"dwab"`).
   - Verify the OpenEXR ASWF binding supports DWAB write (it does —
     `OpenEXR.DWAB_COMPRESSION` is the constant).
2. **Add preset:** `cinema-linear-finished` dispatches through
   `write_exr_linear_rec2020(half, dwab)`. The existing `cinema-aces`
   continues to dispatch through `(float, piz)` for one release cycle
   with a deprecation warning.
3. **CLI:** `--preset cinema-linear-finished` becomes the documented
   default in README. `--preset cinema-aces` still works.
4. **Tests:**
   - `test_output.py` — ΔE2000 < 0.5 between DWAB-half and PIZ-float
     outputs on gym + rose.
   - Round-trip: write DWAB-half, read back, compare to source ProPhoto
     within DWAB's visually-lossless tolerance.
5. **Docs:** README, CHANGELOG, SCOPE.md updates.

**Effort:** 1 week. **New deps:** none. **New LOC:** ~30.

### Phase 2 (v0.7.x) — ship `cinema-linear-master` (β-XML)

1. **Pipeline branch:** add an emission point at the end of Stage 7.
   This means either:
   - A new `render_frame_partial(stage=7)` entry point that runs only
     stages 1–7 and returns the linear ProPhoto frame.
   - Or a flag on the existing `render_frame` that short-circuits.
2. **`resolve_xml.py`:** new module. Inputs = per-sequence list of
   LRT keyframes (the parsed `Keyframe` objects). Outputs = a single
   `.drxml` file with keyframed grade-page nodes per §4.
3. **Preset:** `cinema-linear-master` orchestrates the two emissions
   (EXR per-frame, .drxml per-sequence).
4. **Validation:**
   - Round-trip schema test: emit .drxml, import into Resolve via
     scripting API (Studio-only) or manual checkpoint, verify
     keyframes land at the right frame offsets with the right values.
   - ΔE2000 gate: Resolve's decode of (EXR + .drxml) on the gym +
     rose scenes vs v0.6 cinema-linear reference. Target < 1.0 mean.
5. **Docs:** new `docs/RESOLVE_INGEST_MASTER.md` walking the recipe.

**Effort:** 4–8 weeks. **New deps:** none (Resolve XML is just text
output). **New LOC:** ~500–1000 (most in `resolve_xml.py`).

### Phase 3 (v0.8) — deprecate v0.6 presets

- Remove `cinema-linear` and `cinema-aces` preset code.
- Keep `output.py`'s legacy writers as private API in case future
  research re-needs them.

---

## 5.5 v0.7.x roadmap — §2.B free-upgrade increments

The architectural insight from §2.B is that β-XML can preserve LRT
params *without* requiring lrt-cinema to render them, by translating
to Resolve grade-page primitives. Each increment below extends
β-XML coverage incrementally.

Each increment is a self-contained PR:
- `xmp_parser.py` extension to ingest the new LRT field set
- `ir.py` / `DevelopOps` extension to hold the parsed values
- `interpolation.py` keyframe blend extension (most fields are
  scalar-lerpable; HSL curves and Color Grading wheels are
  point-by-point lerp)
- `resolve_xml.py` mapping extension to write new keyframe tracks

The v0.6 pipeline (`pipeline.py` / `develop_ops.py`) needs **zero
changes** — these params never travel through pixel render.

### Increment X1 — HSL grading (24 fields)

| LRT fields | Resolve target |
|---|---|
| `crs:HueAdjustmentRed/Orange/Yellow/Green/Aqua/Blue/Purple/Magenta` | HSL Curves > Hue vs Hue keyframes |
| `crs:SaturationAdjustment...` ×8 | HSL Curves > Hue vs Sat keyframes |
| `crs:LuminanceAdjustment...` ×8 | HSL Curves > Hue vs Lum keyframes |

Effort: 1–2 weeks. Adds 24 keyframable fields. **Highest-leverage
free upgrade** — HSL is widely used in colour-graded timelapses.

### Increment X2 — Color Grading wheels (12 fields)

| LRT fields | Resolve target |
|---|---|
| `crs:ColorGradeMidtoneHue/Sat/Lum` | Color Wheels > Gamma (Mid) |
| `crs:ColorGradeShadowHue/Sat/Lum` | Color Wheels > Lift (Shadow) |
| `crs:ColorGradeHighlightHue/Sat/Lum` | Color Wheels > Gain (Highlight) |
| `crs:ColorGradeGlobalHue/Sat/Lum` | Color Wheels > Offset (Global) |

Effort: 1 week. Adds 12 keyframable fields. Modern look-grading
workflow.

### Increment X3 — Parametric tone curve

| LRT fields | Resolve target |
|---|---|
| `crs:ParametricShadowSplit`, `ParametricMidtoneSplit`, `ParametricHighlightSplit`, `ParametricShadows`, `ParametricDarks`, `ParametricLights`, `ParametricHighlights` | Custom Curves with knee points at the split values |

Effort: 1 week. Adds 7 keyframable fields. Complements the existing
point tone curve.

### Increment X4 — Texture (best-effort)

| LRT field | Resolve target | Caveat |
|---|---|---|
| `crs:Texture` | Texture sharpening (Resolve 18+) | different math — output won't match Lightroom 1:1, document in user-facing warning |

Effort: 3–5 days. Adds 1 keyframable field with documented fidelity
caveat.

### Increment X5 — Clarity (best-effort)

| LRT field | Resolve target | Caveat |
|---|---|---|
| `crs:Clarity2012` | Midtone Detail | different math, LRT itself recommends using with care on timelapses |

Effort: 3–5 days. Adds 1 keyframable field with caveat.

### Increment X6 — LRT user masks (`LRT Mask 1`–`LRT Mask 6`)

| LRT fields | Resolve target |
|---|---|
| Per-mask `crs:CorrectionMasks` geometry (CircularGradient, RadialGradient, LinearGradient, BrushStroke) | Power Window primitives |
| Per-mask `crs:LocalExposure2012`, `LocalContrast2012`, `LocalSaturation` etc. | Per-Power-Window grade nodes (keyframed) |

Effort: **2–4 weeks** (significantly heavier — geometry parser +
Power Window primitive mapping + per-mask grade tree). The
"non-trivial" upgrade in the list.

Decision needed: ship now (Phase 2 included) or defer to Increment X6?
Per the user-locked decision at §8 below, **defer to X6** — keep
Phase 2 tight.

### Drops (no β-XML rescue)

| LRT field | Why no rescue | Status after v0.7.x |
|---|---|---|
| `crs:Dehaze` | no clean Resolve primitive | drop with warning permanently |
| `crs:Highlights2012` / `Shadows2012` / `Whites2012` (LR PV5 parametric tone) | no clean Resolve primitive; Custom Curves is lossy approximation that doesn't survive comparison to Lightroom | drop with warning permanently (same as v0.6) |
| Lens corrections, vignetting post-crop, grain, B&W conversion, calibration | intentionally out of develop scope | permanent drop |

### Ordering

Recommended order: **X1 → X2 → X3 → X6 → X4 → X5.**

- X1 (HSL) first — highest user value, cleanest mapping.
- X2 (Color Grading) second — modern grading workflow, exact mapping.
- X3 (parametric tone) third — complements existing tone curve coverage.
- X6 (user masks) fourth — bigger lift, but the last "exact" mapping
  that significantly expands coverage.
- X4 (Texture) and X5 (Clarity) last — approximate mappings with
  caveats; lower priority because their fidelity gaps are visible.

---

## 6. What we explicitly are NOT doing in v0.7

- **CDNG emission** (`cinema-cdng` preset). Drops out of v0.7's scope.
  CDNG was solving for "Camera Raw panel knobs" which the user has
  de-prioritised. If interest returns, ship in v0.7.x or v0.8 as a
  sibling preset.
- **CineForm RGB / ProRes 4444 / etc.** Not v0.7's target. Research
  available in
  [v07-proprietary-raw-codec-feasibility.md](v07-proprietary-raw-codec-feasibility.md).
- **JXL emission.** Pending Resolve ingest catching up; defer.
- **Expanding the LRT-modelled parameter set into v0.7's *Phase 2*
  (β-XML)** beyond §2.A. The §2.B free-upgrade roadmap (HSL, Color
  Grading, parametric tone, user masks, Texture, Clarity) lives in
  the v0.7.x increments at §5.5. v0.7's Phase 1 + Phase 2 ship the
  §2.A core; each §2.B increment lands afterwards as a self-contained
  PR. v0.7's emission architecture is designed to absorb each new
  parameter additively without re-architecture.
- **D++ Resolve plugin** (originally suggested for the CDNG path).
  Not needed once β-XML ships.

---

## 7. The decision table, final

| Constraint | γ (v0.7.0) | β-XML (v0.7.x) | When user picks each |
|---|:---:|:---:|---|
| Small size (10–30× vs v0.6 TIFF) | ✓ | ✓ | both |
| Fast encode | ✓ | ✓ | both |
| Highlight / shadow recovery | ✗ | ✓ | β when needed |
| Maximum colour + luminance data | ◐ (Stage 13, tone-shaped) | ✓ (Stage 7, scene-referred) | β when needed |
| LRT modelled-keyframes preserved | ✓ (baked) | ✓ (sidecar) | both |
| Resolve UX | grade page from scratch | grade page auto-populated with LRT keyframes | both work |
| Cross-NLE | ✓ (EXR universal) | ◐ (EXRs work everywhere; .drxml is Resolve-only) | γ for cross-NLE |
| Implementation effort | 1 week | 4–8 weeks | γ ships first |

The two presets cover the design space. γ is the look-locked
deliverable for users who want a single file per frame with the
LRT-authored look baked in. β-XML is the recoverable master for users
who want to keep grading flexibility *and* preserve the LRT-authored
keyframed intent.

---

## 8. User-mask handling (decision locked 2026-05-28)

**Resolved: defer to Increment X6.**

LRT user masks (`LRT Mask 1` … `LRT Mask 6`) — keyframable but not
modelled in v0.6 — do NOT ship in v0.7's Phase 2. Phase 2's β-XML
ships with only the §2.A core (Exposure, Contrast, Temp/Tint, Blacks,
Saturation, Vibrance, Tone Curve point form, + internal-use masks
HG/Deflicker/Global folded into Stage 7 pixels).

User-mask preservation lands as Increment X6 (§5.5), positioned
fourth in the v0.7.x roadmap order — after HSL (X1), Color Grading
wheels (X2), and parametric tone curve (X3). X6 is the heaviest
increment (~2–4 weeks) because it needs the LRT XMP geometry parser
(CircularGradient / RadialGradient / LinearGradient / BrushStroke
shape data) mapped to Resolve Power Window primitives plus per-mask
grade tree.

While X6 is pending, render-time warning surfaces non-default user
masks (same pattern as v0.6's Highlights/Shadows/Whites drops):

```
[lrt-cinema] LRT-authored mask 'LRT Mask 3' on frame DSC_4053.NEF
  carries non-default Local* values that v0.7 does not yet preserve.
  Drops at render. Increment X6 (planned) will preserve these as
  Resolve Power Window keyframes.
```

This keeps Phase 2 tight (~4–8 weeks scope vs +2–4 weeks if user
masks shipped together) and aligns with the user's "ship simple
change first" directive.

The .drxml schema reserves space for user-mask nodes additively so
X6 can land later without breaking Phase 2 outputs.

---

## 9. Goal-satisfaction check (against `/goal` directive)

The session-scoped goal:

> Current emissions are huge, and don't even allow full reversibility.
> Find a modern raw format that we can use which allows 10–50× compression
> of current emissions. BRAW would be an ideal candidate. Goal is satisfied
> when we have a SPEC/implementation plan to transition repo to emissions
> in said format.

This SPEC revision plan satisfies the directive on each criterion:

| Goal criterion | This SPEC's answer |
|---|---|
| **Modern format** | OpenEXR (16-bit half-float, DWAB compression) — cinema-industry standard for scene-referred compressed intermediate |
| **10–50× compression of current emissions** | DWAB at half-float gives 12–30× vs v0.6 32-bit float TIFF (cinema-linear); ~5–9× vs v0.6 EXR PIZ (cinema-aces). γ preset hits 10–18× vs cinema-linear at Stage 13; β preset same range at Stage 7. |
| **"Full reversibility"** (reframed by user → recoverability + LRT keyframe preservation) | γ preserves all v0.6-modelled LRT keyframes in pixels (no recoverability above the tone curve); β-XML preserves full HDR recoverability AND LRT keyframes as Resolve grade sidecar. The architectural split (data + grade) resolves the three-constraint tension. |
| **BRAW as ideal candidate** | BRAW is decoder-only — characterised in [v07-proprietary-raw-codec-feasibility.md](v07-proprietary-raw-codec-feasibility.md) §1.2 and rejected. EXR-half-DWAB-plus-Resolve-XML achieves BRAW's value prop (smart intermediate + per-frame metadata for develop intent) via an open-standard route. |
| **SPEC + implementation plan** | This document. §5 phased plan, §5.5 v0.7.x roadmap, §8 user-mask decision, all dependencies and validation gates enumerated. |

**Phase 1 ships in ~1 week** as the immediate value drop. Phase 2 +
v0.7.x increments are scheduled, scoped, and architecturally
positioned.

The SPEC's body is complete. v0.7 implementation can begin.
