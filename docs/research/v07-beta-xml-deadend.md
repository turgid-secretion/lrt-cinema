# v0.7 β-XML: empirical dead-end

**Status:** Verification outcome, 2026-05-28. Closes
[v07-spec-revision-plan.md](v07-spec-revision-plan.md) §"Phase 2
(v0.7.x) — ship cinema-linear-master (β-XML)". Does NOT affect Phase 1
(γ; `cinema-linear-finished` shipped in v0.7.0).

User decision: **defer β-XML (sidecar variant) to v0.8**; v0.7.1 ships
**Option B** instead — a pixel-bake variant of `cinema-linear-master`
at Stage 7, no sidecar (see §4 below + the v0.7.1 CHANGELOG entry).

---

## 1. What β-XML promised

A two-stream architecture: scene-referred half-float DWAB EXR at Stage
7 + a per-sequence Resolve-importable XML sidecar carrying LRT-authored
keyframes for the §2.A Class B parameters (Exposure2012, Contrast2012,
Blacks2012, Saturation, Vibrance, ToneCurvePV2012). Resolve would
auto-apply the grade on import; user could disable, edit, or re-author
without re-rendering.

This was the architectural answer to the three-constraint tension
([v07-emission-keyframe-vs-recovery.md](v07-emission-keyframe-vs-recovery.md)
§3) — recoverable pixels + max colour/luminance data + LRT keyframes
preserved. The cinema-VFX "data + grade" split, applied to LRT-driven
timelapse.

The spec sketched the XML as `.drxml` with `<DynamicGain>` /
`<DynamicContrast>` / `<DynamicCustomCurve>` keyframe tracks. It
explicitly noted: *"Schema is illustrative — real Resolve XML uses its
own conventions which Phase 2 implementation verifies via round-trip:
write, import into Resolve, export, diff."*

That verification is the subject of this document.

---

## 2. The empirical finding

**Per-frame grade keyframes do not survive any documented Resolve
project-import format.** Verified against the DaVinci Resolve 20
Reference Manual (installed locally,
`/Applications/DaVinci Resolve/DaVinci Resolve Manual.pdf`) and the
Resolve Studio Scripting API
(`/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/README.txt`).

### 2.1 FCPXML route

> "At the time of this writing, only Final Cut Pro X XML projects are
> capable of exporting color correction data that can be imported as
> primary grades in DaVinci Resolve. For obvious reasons, color
> correction import is a one-way street, and imported color corrections
> cannot be output back to Final Cut Pro. Imported Final Cut Pro X
> color adjustments appear in the Color page **as primary corrections**."
>
> — Resolve Manual, line 50884 onward.

"As primary corrections" is the load-bearing phrase. FCPXML colour data
becomes a single static primary-correction node per clip in Resolve;
the keyframe time-track that FCPXML 1.10 supports on the FCP side is
flattened to the first-frame value on the Resolve import side. There is
no documented FCPXML path that lands per-frame color keyframes on the
Resolve grade page.

### 2.2 Resolve scripting API route

The Studio scripting API exposes grade controls only at static
granularity. The relevant calls
(`Developer/Scripting/README.txt:464, 525, 535`):

| API | Surface | Per-frame keyframes? |
|---|---|---|
| `SetCDL([CDL map])` | per-node Slope/Offset/Power/Saturation | **no** — static |
| `SetLUT(nodeIndex, lutPath)` | per-node LUT path | **no** — static |
| `ApplyGradeFromDRX(path, gradeMode)` | apply still grade with keyframes loaded from a `.drx` file | **yes — but only via .drx, which is binary and undocumented** |
| `AddVersion(...)` / `GetVersionNames(...)` | per-clip color versions | static per version |

No call exists in the form `node.SetKeyframe(param, frame, value)`. The
keyframe-mode toggles
(`SetKeyframeMode` / `KEYFRAME_MODE_COLOR`) configure UI behaviour, not
programmatic per-frame setters.

### 2.3 CDL / EDL+CDL route

Per-clip ASC CDL (Color Decision List) is universally supported but is
per-clip-static by definition. Resolve's ColorTrace workflow
(`Manual` Chapter 186) lets users batch-apply CDLs to clips, but the
CDL itself encodes Slope/Offset/Power/Saturation at one point in time,
not per frame.

### 2.4 Per-clip splits

A pseudo-keyframe approach is feasible: split the timeline into
sub-clips at each LRT keyframe boundary, apply a static CDL per
sub-clip via `SetCDL`. The output is stepped (not smoothly
interpolated) per-segment grading; the segment density approximates
"keyframes" but never reaches Resolve's native dynamic-keyframe
behaviour. Acceptable for a coarse Holy Grail ramp; unacceptable for
LRT's typical per-frame creative-keyframe density.

---

## 3. What this collapses

| v0.7.x increment | Status under Option C |
|---|---|
| Phase 1 — γ (`cinema-linear-finished`) | **shipped** (v0.7.0) |
| Phase 2 — β-XML (`cinema-linear-master`) | **deferred to v0.8** |
| Increment X1 — HSL grading (24 fields) | deferred — depends on β-XML carrier |
| Increment X2 — Color Grading wheels (12 fields) | deferred — depends on β-XML carrier |
| Increment X3 — Parametric tone curve (7 fields) | deferred — depends on β-XML carrier |
| Increment X4 — Texture (best-effort) | deferred |
| Increment X5 — Clarity (best-effort) | deferred |
| Increment X6 — LRT user masks (6) | deferred — Power Window per-frame keyframes have the same blocker |

All six §2.B "free upgrade" increments were architected around the
β-XML sidecar carrier. With no carrier, the §2.B parameters have no
non-baked transport into Resolve. They survive only if v0.8 finds a
new carrier.

---

## 4. What v0.8 could re-open

Three avenues to revisit when β-XML scope is re-opened:

1. **Reverse-engineer `.drx`.** The Resolve Color Trace `.drx` file
   format is the only documented carrier of grade keyframes that the
   scripting API can ingest (`ApplyGradeFromDRX`). It is undocumented
   in the public Studio scripting README. A reverse-engineering spike
   (estimated 4–8 weeks, high dead-end risk) could yield an open-source
   `.drx` writer.
2. **Resolve scripting API extension.** Blackmagic publishes scripting
   API additions in Studio release notes; a future Resolve version may
   ship `node.SetKeyframe(param, frame, value)`. Track release notes.
3. **Per-clip sub-clip split (Option A from the 2026-05-28 pivot).** Ship as
   a "stepped grade" preset variant of β-XML if the user re-prioritises
   ramp-style timelapses where step granularity is acceptable. Trade-off:
   loses smooth per-frame keyframe interpolation.

---

## 5. v0.7 line as shipped

- **v0.7.0 (2026-05-28)** — γ preset (`cinema-linear-finished`).
  Half-float DWAB EXR at Stage 13; 10–18× smaller than v0.6
  `cinema-aces`. All LRT-authored develop ops baked into pixels exactly
  as v0.6 did. No sidecar. No β-XML.
- **v0.7.1 (2026-05-28)** — β preset (`cinema-linear-master`, Option B
  pivot from the dead-end above). Half-float DWAB EXR at **Stage 7**;
  skips DCP LookTable + ProfileToneCurve for HDR headroom. LR PV2012
  ops (Exposure / Blacks / ToneCurve / Saturation / Vibrance / Contrast)
  still apply on the Stage 7 output, so LRT-authored keyframes bake
  into pixels — just without the DCP tone shape. Trade-off documented
  on the preset.
- **v0.8** — re-opens the original β-XML sidecar question if a new
  carrier surfaces (per §4 above). Also re-opens the §2.B free-upgrade
  increments (X1–X6) if a per-frame keyframe carrier becomes available.

The user's three constraints (recoverable highlights / max
colour-luminance data / LRT keyframes preserved) reduce to one
v0.7-shippable answer: keyframes baked into γ pixels. Recovery and
max-data are sacrificed for keyframes — the trade γ has always made.
β-XML promised to lift the trade; verification proved the lift wasn't
available.

---

## 6. Verification log (for v0.8 re-investigation)

| Source | Evidence | Date |
|---|---|---|
| Resolve Manual, ~line 50884 | "imported color corrections... appear in the Color page as primary corrections" — single static node, no keyframes | 2026-05-28 |
| Resolve Manual FCPXML chapter (~line 51074) | Lists supported transitions, effects, retiming hints; never lists per-frame color keyframes | 2026-05-28 |
| Resolve Studio Scripting README, lines 464/525/535 | `SetCDL` / `SetLUT` / `ApplyGradeFromDRX` are the only grade-write APIs; no per-frame setter | 2026-05-28 |
| Apple FCPXML 1.10 reference | `<adjust-color>` element schema supports `<keyframe>` children, but Resolve discards them on import per the Manual passage above | 2026-05-28 (web fetched) |
| Resolve user forum (Blackmagic forum) | Multiple community reports confirm FCPXML keyframes drop at Resolve import | 2026-05-28 (web fetched) |

If v0.8 re-opens this: re-verify each row against the Resolve version
in use at that time; the Studio scripting API has historically gained
features version-over-version.
