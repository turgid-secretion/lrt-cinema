# The tension: LRT keyframe preservation vs highlight/shadow recovery

**Status:** Design analysis, 2026-05-27. Refines the
[v07-emission-criteria-reframed.md](v07-emission-criteria-reframed.md)
recommendation with the user's additional constraint.
**Parent:** [v07-emission-format.md](v07-emission-format.md) (current
SPEC, now under revision).

---

## 1. The user's nuanced criterion (stated 2026-05-27)

> I want to be able to recover highlights, shadows, as well as other
> parameters. But simply dropping tone/sat/vib/contrast to solve this
> creates another problem — now if I want to *keyframe* those
> parameters in LRT, that gets dropped too. So this is a balancing
> act. I want to recover as much as possible, **and** have maximum
> grading flexibility (meaning keep maximum color and luminance data
> available) **while not losing the keyframed parameters** — which is
> the whole point of the changes one makes during the LRT stage.

Restated as three constraints that must hold simultaneously:

1. **Recoverable:** highlights above 1.0 and shadows near 0 stay
   retrievable downstream — no clipping into baked pixels.
2. **Maximum colour + luminance data preserved** for downstream
   grading flexibility.
3. **LRT keyframed parameters survive** end-to-end. If LRT authored a
   tone-curve ramp from frame 0 to frame 1000, frame 500 must reflect
   the interpolated curve.

This is a genuine tension. Each of α (CDNG), β (Stage 9 EXR), γ (Stage
13 EXR) fails one of the three.

---

## 2. Why each option fails one constraint

### α (CDNG, current SPEC)

- ✓ Recoverable to sensor's 14-bit DR
- ✓ Maximum colour + luminance preserved (raw Bayer)
- ✗ **LRT tone / sat / vib / contrast keyframes DROPPED** (Q1.0 T3, T4 spike: Resolve ignores file-level ProfileToneCurve + OpcodeList3)

### β (Stage 9 EXR DWAB, pre-tone-curve)

- ✓ Recoverable (half-float, full HDR)
- ✓ Maximum colour + luminance preserved
- ✗ **LRT tone / sat / vib / contrast keyframes DROPPED** (we skip stages 10–12)

### γ (Stage 13 EXR DWAB, all LRT applied)

- ✗ **NOT recoverable** — tone curve already clipped highlights
- ✗ Limited colour + luminance for downstream (tone-shaped data,
  reduced DR)
- ✓ LRT keyframes preserved (baked into pixels)

The tension is real. **No single-emission-point + single-format
combination satisfies all three.**

---

## 3. The mathematical structure of the tension

The LRT keyframed parameters fall into two classes by their effect on
data range:

### Class A: invertible monotonic, never clip in floating-point

| LRT op | Op type | Why preserves range |
|---|---|---|
| `Temperature` / `Tint` / Holy Grail K | per-channel multiplication | linear; floats preserve overranges |
| `Exposure2012` | global gain (×2^EV) | linear; floats preserve overranges |
| `Saturation` | HSV-space S multiplication | preserves luminance |
| `Vibrance` | HSV-space S multiplication, selective | preserves luminance |

These ops can be baked into pixels at emit time and still leave
recoverable data, as long as we use float storage. **The keyframes
survive AND the data stays recoverable.** No tension.

### Class B: tonal-shape ops, can clip / crush

| LRT op | Op type | Why clips |
|---|---|---|
| `ToneCurvePV2012` | nonlinear curve, anchored at (1, 1) | flat shoulder near 1.0 → overranges saturate |
| `Contrast2012` | S-curve | extreme values crushed at both ends |
| `Blacks2012` | shadow-region tone-curve | can crush values < threshold to 0 |
| `Highlights2012` / `Shadows2012` / `Whites2012` | parametric tone | dropped by v0.6 already |
| `ProfileToneCurve` (DCP-embedded) | per-channel tone curve | flat shoulder |

These ops, applied in pixels, **destroy data** that was above their
operating range. Once a value > 1.0 has been mapped to 0.99 by the
tone curve, the original value is gone — float storage can preserve
0.99 but cannot reconstruct the lost overranges.

**The tension is entirely about Class B.** Class A keyframes preserve
recoverability for free.

---

## 4. The three architectural answers

There are exactly three ways to resolve the Class B tension:

### Solution I — Apply Class B; lose recoverability (option γ)

Bake the tone-shape ops into pixels. Keyframes preserved. Recovery
sacrificed. This is what v0.6 does today.

### Solution II — Skip Class B; lose keyframes (option β)

Emit pre-tone-curve. Class B keyframes go unapplied at emit; user
re-authors in Resolve. This is what current v0.7 CDNG also does, plus
the reframed-criteria recommendation.

### Solution III — Carry Class B as a *sidecar* alongside the pixels

The cinema-VFX answer to this exact problem for decades. Pixels stay
scene-referred (Class A applied, Class B skipped). LRT Class B intent
travels as a non-destructive grade description that Resolve applies on
top of the pixels by default. User can disable, edit, or re-author the
grade without losing the underlying data.

This is the "two-stream" model: **scene-referred master + grade
metadata**. Cinema VFX uses this universally — EXR scene-referred
plates + CDL grade lists + OFX node trees + LUT delivery layers.

**This is the architectural answer that satisfies all three of the
user's constraints simultaneously.**

---

## 5. The sidecar grade — what format?

Resolve has several mechanisms for non-destructive grade descriptions.
Ranked by fit:

### 5.1 ASC CDL (Color Decision List) — per-clip

Format: small XML (.cdl, .ccc) carrying Slope / Offset / Power per
channel + a single Saturation value.

| LRT op | CDL mapping | Fidelity |
|---|---|---|
| Exposure2012 | Slope (R = G = B) | exact |
| WB / Holy Grail K | Slope per channel | exact |
| Blacks2012 | Offset (lifts shadow point) | approximate |
| Contrast2012 | Power (approximates curve shape) | **lossy** — Power isn't an S-curve |
| ToneCurvePV2012 (custom curve) | Power | **lossy** — loses curve detail |
| Saturation | Saturation | exact |
| Vibrance | (not expressible) | **dropped** |

Pros:
- Universal — every NLE reads ASC CDL.
- Tiny files (~200 bytes each).
- Non-destructive in Resolve.

Cons:
- Per-clip, not per-frame. For a 1000-frame sequence imported as one
  clip, CDL gives a single static grade — keyframes flatten to an
  average.
- Cannot represent custom tone-curve shapes faithfully.

### 5.2 Resolve XML project (`.drp` / `.drxml`) — per-frame keyframable

Format: Resolve's native project description language. Carries
per-clip grade with keyframes on every gradeable parameter.

| LRT op | Resolve grade-page parameter | Fidelity |
|---|---|---|
| Exposure2012 | Gain (R = G = B, keyframed) | exact |
| WB / Holy Grail K | Gain per channel (keyframed) | exact |
| Blacks2012 | Lift (keyframed) | approximate |
| Contrast2012 | Contrast (keyframed) + Pivot | exact for v0.6's contrast model |
| ToneCurvePV2012 (custom curve) | Custom Curves (RGB) (keyframed) | **exact** — Resolve's RGB curves match LRT's primitive |
| Saturation | Saturation (keyframed) | exact |
| Vibrance | Color Boost (keyframed) | exact |
| LRT mask deltas | Power Window grade nodes (per-region, keyframed) | exact |

Pros:
- **Exact per-frame keyframe preservation.** Every LRT-authored ramp
  travels intact.
- Resolve auto-applies on project open.
- User can edit / disable / re-author freely without touching pixels.

Cons:
- Resolve-specific (other NLEs ignore the .drp).
- More implementation work — needs a project generator (~500–1500 LOC).
- User has to import the .drp alongside the EXR sequence; mild
  workflow friction.

### 5.3 DCTL with per-frame parameter source

Custom DaVinci shader that reads per-frame parameters from a sidecar
file (e.g., a JSON or CSV indexed by frame number).

Pros:
- Maximum flexibility (any math expressible).
- Single DCTL install, then any lrt-cinema sequence works.

Cons:
- Requires user to install the DCTL once.
- Less native than Resolve project XML.
- Harder to debug.

### 5.4 Embedded EXR metadata + grade-page macro

EXR supports arbitrary metadata attributes per file. A Fusion node or
DCTL could read per-frame attributes and apply tone shaping.

Pros:
- Single file per frame (no separate sidecar to track).
- Self-describing.

Cons:
- Requires Fusion macro or DCTL install.
- EXR attributes are not natively visible in Resolve's grade UI.

### 5.5 Per-frame LUT (1D / 3D)

Bake each frame's Class B intent into a .cube file. Resolve applies
LUT per-clip or per-frame via OFX node.

Pros:
- LUTs are universal — any NLE.

Cons:
- 1D LUT can capture tone-curve shape but not saturation / vibrance
  cleanly.
- 3D LUT captures both but file size adds up (a 33³ LUT is ~36 KB ×
  1000 frames = 36 MiB sidecar, manageable).
- Resolve applies LUTs as a static per-clip operation — keyframed
  per-frame LUT loading requires DCTL or scripting.

---

## 6. Concrete v0.7 architectures that satisfy all three constraints

Each architecture combines a pixel emission (β-style) with a grade
sidecar (§5). Ordered by implementation effort.

### Architecture **β-CDL** — minimum viable Solution III

- Pixels: Stage 9 EXR half-float DWAB. Class A applied, Class B
  skipped.
- Sidecar: per-sequence CDL capturing the *average* Class B intent
  (or the value at the sequence's middle frame).
- Resolve: import EXR + apply CDL via Color page.

Trade-offs:
- ✓ Recoverable (β-grade EXR is scene-referred half-float)
- ✓ Maximum colour + luminance preserved
- ◐ LRT keyframes **partially** preserved — static look survives;
  per-frame keyframed ramps flatten to an average. **The user's
  constraint #3 is only partially satisfied.**
- Cost: 1–2 weeks.

### Architecture **β-XML** — full Solution III

- Pixels: Stage 9 EXR half-float DWAB.
- Sidecar: Resolve XML project with per-frame keyframes on every
  LRT-authored parameter (Exposure, WB, Blacks, Contrast, custom
  curve, Saturation, Vibrance, mask deltas as power windows).
- Resolve: import EXR sequence and import the .drp → grade page
  auto-populates with per-frame keyframes.

Trade-offs:
- ✓ Recoverable
- ✓ Maximum colour + luminance preserved
- ✓ **LRT keyframes preserved exactly** as Resolve grade-page
  keyframes; user can edit any of them or disable the entire grade
- ✗ Resolve-only (other NLEs ignore the .drp; they'd see only the
  scene-referred linear EXR sequence with no grade applied)
- Cost: 4–8 weeks (writer needs to map LRT → Resolve grade primitives
  + generate valid Resolve XML; needs verification that Resolve
  ingests our generated XML cleanly).

### Architecture **β-LUT** — universal middle ground

- Pixels: Stage 9 EXR half-float DWAB.
- Sidecar: per-frame 3D LUT files (one .cube per frame) baked from
  Class B intent at that frame.
- Resolve: import EXR sequence, install a DCTL that reads the
  per-frame LUT by frame number from a sidecar directory.

Trade-offs:
- ✓ Recoverable, ✓ max data, ✓ LRT keyframes preserved (LUT is
  per-frame so any LRT-authored value travels)
- ◐ Cross-NLE viability: LUTs are universal but per-frame LUT loading
  requires custom DCTL/OFX in each tool. Resolve = clean. Premiere =
  requires keyframable LUT plugin. FCP = requires Motion. Avid =
  hard.
- Cost: 3–5 weeks (DCTL + per-frame LUT generator).

### Architecture **γ + β** — dual emission

- Pixels α: Stage 13 EXR DWAB (the "graded master") with all LRT
  intent baked.
- Pixels β: Stage 9 EXR DWAB (the "raw master") with Class A only.
- No sidecar — both versions of each frame coexist.
- Resolve: user opens whichever they need. Workflow: start with γ
  for the LRT-authored look; if highlights need recovery, switch to
  β for that frame range, re-grade by hand.

Trade-offs:
- ✓ Recoverable (β file path) ✓ max data (β) ✓ LRT keyframes preserved (γ file path)
- ✗ 2× storage cost (~30 MiB / frame total — still 9× smaller than
  v0.6 cinema-aces)
- ✗ Workflow friction — user manages two copies of each frame
- Cost: 1 week (just a preset that calls both writers)
- Net: cheapest path to "all three constraints satisfied" but at the
  cost of operator complexity

---

## 7. The honest matrix

| Architecture | Recover | Max data | LRT keyframes | Effort | Cross-NLE | Resolve UX |
|---|:---:|:---:|:---:|---|:---:|:---:|
| α CDNG (current SPEC) | sensor 14-bit | 14-bit Bayer | WB+Exp only | 4–6 wk | ◐ | Camera Raw |
| β EXR-only (reframed-doc winner) | ✓ full HDR | ✓ | ✗ | 1 wk | ✓ | grade page |
| γ EXR DWAB at Stage 13 | ✗ | ✗ | ✓ all (baked) | 1 day | ✓ | grade page |
| **β-CDL** | ✓ | ✓ | partial (average only) | 1–2 wk | ✓ | grade page + CDL import |
| **β-XML** | ✓ | ✓ | **✓ all (Resolve keyframes)** | 4–8 wk | ✗ (Resolve-only XML) | auto-applied |
| **β-LUT** | ✓ | ✓ | ✓ all (DCTL applies) | 3–5 wk | ◐ (DCTL per-tool) | install DCTL once |
| **γ + β dual** | ✓ (β file) | ✓ (β file) | ✓ (γ file) | 1 wk | ✓ | switch files |
| **α + D++ Resolve plugin** | sensor 14-bit | 14-bit Bayer | ✓ all (plugin reads file-level) | 4–6 wk + ~3 mo plugin | ✗ | Camera Raw |

The two architectures that hit all three of the user's constraints
*and* preserve cross-NLE compatibility:

- **β-LUT** — universal (per-frame LUT + small DCTL), preserves
  keyframes via the sidecar. Mid effort. Workflow friction = DCTL
  install once.
- **γ + β dual emission** — dual storage, no sidecar. Lowest effort.
  Workflow friction = user manages two file paths.

The Resolve-optimised path:

- **β-XML** — exact LRT keyframes as Resolve grade primitives. Higher
  effort, Resolve-only output, but the cleanest UX once the .drp
  imports cleanly.

---

## 8. Recommendation

Given:

- The user's three constraints (recovery + max data + keyframes)
- v0.7's deliverability target (a SPEC + implementation plan)
- The Resolve-primary workflow

**Recommended v0.7 product:**

Ship a **two-preset combination** that covers the three constraints
across two use cases:

### Preset 1: `cinema-linear-master` (β-XML target)

- Emit Stage 9 EXR half-float DWAB per frame (the recoverable master).
- Emit a `_grade.drxml` sidecar per sequence containing per-frame
  keyframes for every LRT-authored parameter mapped to Resolve grade
  primitives.
- Resolve user imports both → scene-referred linear with the
  LRT-authored grade auto-applied.
- User can disable the .drxml grade for full raw access, edit any
  keyframe, or re-author entirely.

### Preset 2: `cinema-linear-finished` (γ target)

- Emit Stage 13 EXR half-float DWAB per frame (the LRT-baked master).
- No sidecar — pixels carry the look.
- Resolve user imports → grade-ready, all LRT intent already applied.
- Highlights / shadows above the tone curve are not recoverable —
  user accepts this for the look-locked simplicity.

### Phased ship

- **v0.7.0:** ship Preset 2 (γ) first — 1-week change (PIZ → DWAB
  swap with optional half-float promotion). Immediate 10–18× size win
  vs v0.6 cinema-aces. All LRT intent preserved. Doesn't address
  recovery — but ships fast.
- **v0.7.x (4–8 weeks later):** ship Preset 1 (β-XML) with the
  Resolve-project generator. Addresses recovery + keyframes
  simultaneously.
- **v0.8:** deprecate v0.6 `cinema-linear` / `cinema-aces`.

This sequence is honest about effort. Phase 1 ships value immediately.
Phase 2 is the deeper architectural answer to the full three-constraint
brief.

### What v0.7 does NOT need to ship

- **α (CDNG)** can wait or be dropped. Once β-XML exists, CDNG's
  Camera-Raw-knob value-add is less compelling (the user has Resolve
  grade-page keyframes that cover the same use case, with more
  ops covered and more flexibility).
- **CineForm / ProRes / etc.** stay as alternative-format research,
  not v0.7 work.

---

## 9. Open questions for the user

Before locking the SPEC revision:

1. **Are you OK with Resolve-only output for Preset 1 (β-XML)?**
   Cross-NLE users would import the EXRs without the Resolve grade.
   Premiere / FCP / Avid users would need to re-grade by hand. If
   cross-NLE is critical, β-LUT becomes the better fit but at higher
   workflow friction (DCTL install).

2. **Does v0.7.0 shipping `cinema-linear-finished` (γ) alone satisfy
   you while β-XML cooks?** Or do you need β-XML to land before any
   v0.7 ship?

3. **Are there LRT-authored ops not on my list that need preserving?**
   I covered Exposure, WB, Blacks, Contrast, ToneCurve, Saturation,
   Vibrance, mask deltas. If LRT has other keyframable parameters
   (e.g., Texture, Clarity, Dehaze, HSL adjustments), the mapping
   table in §5.2 needs to extend.

4. **What's the per-frame metadata storage budget?** A Resolve .drxml
   with full keyframes for a 5000-frame sequence is ~1–5 MiB total
   (negligible). Per-frame LUTs (β-LUT) are ~150 MiB for 5000 frames
   at 33³ — manageable but not free. Per-frame CDL is ~1 MiB.

---

## 10. The truthful one-liner

**No single existing format simultaneously satisfies all three of your
constraints.** The cinema industry solved this 25 years ago by
splitting "data" (scene-referred linear pixels) from "grade"
(non-destructive metadata). That split is what β-XML implements; that
split is what BRAW partially implements (via its Camera Raw decode
parameters); that split is what CDNG would implement if Resolve
honoured file-level develop metadata (Q1.0 spike showed it doesn't,
mostly).

The right v0.7 answer is to **embrace the split** — scene-referred
master + per-frame Resolve grade sidecar — rather than chase a single
file format that does both. The cost is the project-file generator;
the gain is that all three constraints land at once, with no operator
juggling and no LRT-authored intent lost.
