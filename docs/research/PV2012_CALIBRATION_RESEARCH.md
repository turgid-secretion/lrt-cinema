# PV2012 Calibration — Prior Art, Module Mapping, Methodology

v0.3 Track A2 research deliverable. No code.

**Why LR Classic and not a headless Adobe binary**: see DNG SDK feasibility doc, commit `f4a60cb` on `research/kelvin-multipliers`. Findings: `dng_validate` strips the `crs:*` XMP namespace before render (`dng_validate.cpp` L581–594), and `dng_render` exposes no PV2012 API. Adobe DNG Converter does NEF→DNG format conversion only, no develop edits. PV2012 math lives in closed-source `acr.dll` / `Camera Raw.plugin`, GUI-host-only (Photoshop/Bridge/LR Classic). No public Adobe binary applies PV2012 headlessly — LR Classic driven by AppleScript or Lua plugin is the only ground-truth path.

## 1. Prior reverse-engineering

Public state is thin; original work required.

- **darktable** has the only FOSS LR-XMP import path ([`src/develop/lightroom.c`](https://github.com/darktable-org/darktable/blob/master/src/develop/lightroom.c)). Maps `Blacks2012` → `exposure.black` via hand-tuned 5-point LUT `{-100: 0.020, -50: 0.005, 0: 0, 50: -0.005, 100: -0.010}` (L520). `Exposure2012` pass-through. **Contrast/Highlights/Shadows/Whites silently dropped.** [2013 announcement](https://www.darktable.org/2013/02/importing-lightroom-development/) disclaims fidelity beyond exposure/blacks.
- **RawTherapee** sliders are **independent implementations**, not RE'd ([RawPedia](https://rawpedia.rawtherapee.com/Exposure)). Not a PV2012 proxy.
- **Algorithm family**: Adobe confirms PV2012 Highlights/Shadows/Clarity = Local Laplacian Filters (Paris et al. SIGGRAPH 2011, [DOI 10.1145/1964921.1964963](https://doi.org/10.1145/1964921.1964963); Aubry et al. ACM TOG 2014). Papers give the function class; **PV2012 parameterization never published**.
- **Patents**: Adobe 2010–2014 (Chan, Hamburg, Knoll) cover generic tone work (US9020243B2, US20150078661A1) — none documents slider math.
- **Empirical sweeps**: none published. CDTobie 2012 sets methodology, no numerics. pixls.us threads qualitative only.

## 2. LR → darktable module mapping

| LR field | dt target | Confidence | Risk |
|---|---|---|---|
| `Contrast2012` | `sigmoid` contrast knob (fallback `tonecurve` S-curve) | MEDIUM | Sigmoid scene-referred; LR contrast may operate display-side. |
| `Highlights2012` | `toneequal` highlights+whites bands | MEDIUM | LLF-family analog. Flat-ramp sweep cannot capture edge-aware spatial behavior. |
| `Shadows2012` | `toneequal` shadows+blacks bands | MEDIUM | Same LLF caveat. |
| `Whites2012` | `tonecurve` whitepoint anchor (fallback `filmic` white relative) | LOW-MEDIUM | LR Whites clips/extends headroom; dt has no dedicated knob. |
| `Blacks2012` | `exposure.black` (fallback `tonecurve` blackpoint) | MEDIUM-HIGH | darktable LUT is hand-tuned, not measured — re-fit empirically. |

Commits to dt 5.x sigmoid/toneequal pipeline (verified back to 5.4 LTS).

## 3. Calibration methodology

**Stimulus**: synthetic linear gray ramp DNG, 4096×512 px, 16-bit linear, 0..1 in 4096 steps. Generate via `dng_validate` or identity-opcode DNG.

**Sweep** per (field, value), field ∈ {Contrast, Highlights, Shadows, Whites, Blacks}, value ∈ {−100, −75, −50, −25, 0, +25, +50, +75, +100}:

1. Inject `crs:<Field>2012="<value>"` into sidecar, others neutral, `ProcessVersion="11.0"`.
2. LR Classic: "Read Metadata from File" → export 16-bit ProPhoto-linear TIFF (AppleScript-driven, or LR SDK Lua plugin).
3. Capture `f_LR(x; value) : [0,1]→[0,1]` along the ramp axis.
4. Render same DNG via `darktable-cli` with candidate dt-param, capture `f_dt(x; param)`.
5. Sweep dt param across native range. **Fit**: minimize `L2(f_LR, f_dt)` via scipy.optimize. `colour-science` (BSD-3) handles transfer-function conversions + ΔE2000 cross-check.

**Output** `pv2012_calibration.json`:

```json
{
  "contrast":   {"module": "sigmoid",    "param": "contrast",
                 "table": {"-100": 0.42, "0": 1.0, "100": 1.78}},
  "highlights": {"module": "toneequal",  "params": ["highlights","whites"],
                 "table": {"-100": {"highlights": -1.2, "whites": -0.8}}}
}
```

Runtime: monotone-cubic interpolation between sampled levels.

## 4. Risk flags

- **Spatial-vs-1D gap**: Highlights/Shadows are LLF, edge-aware. Flat-ramp sweep captures global response only; real images diverge wherever local contrast matters — exactly the regions these sliders target. Validate against face/sky/shadow image; document residual ΔE envelope.
- **Slider non-commutativity**: PV2012 sliders interact non-linearly. Per-slider tables assume separability. Two-axis spot-check required; cross-terms > 5 ΔE on ColorChecker means the per-op-table approach **fails entirely** — multi-slider regression model needed.
- **Camera-profile dependence**: PV2012 may apply different default shaping per DCP. Calibration TIFFs must come from the user's camera, or be normalized via linear-DNG opcodes.
- **Color-space divergence**: LR's "Melissa" (ProPhoto-linear + sRGB tone) vs dt's linear Rec.2020. Fit in a common linear space.
- **LR version drift**: pin LR version in calibration JSON; flag mismatch at runtime.

## 5. Effort estimate

| Step | Hours |
|---|---|
| Ramp DNG + round-trip verify | 4 |
| LR sidecar-inject + export automation (**bottleneck**) | 8–16 |
| darktable-cli sweep harness | 4 |
| Fitting + JSON serialization | 4–8 |
| Validation image (face/sky/shadow) | 8 |
| ColorChecker ΔE cross-check | 4 |
| **Total** | **32–48h (4–6 person-days)** |

**Bottleneck**: LR Classic has no CLI; no headless Adobe binary applies PV2012 math (see intro + commit `f4a60cb`). Sidecar-inject-and-export driven via AppleScript or LR SDK Lua plugin; each export 5–15s, ~45 per ramp pass.

**De-risk order**: Blacks + Contrast first (smallest spatial component). Validate methodology against existing darktable Blacks LUT as ground truth. Commit to Highlights/Shadows/Whites only if ΔE2000 < 4 on validation image.
