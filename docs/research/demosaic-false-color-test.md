# Demosaic false-colour test — venetian blinds (DSC_4053)

**Verdict (honest null):** No clean-room **demosaic-only** option reaches the
ACR-NR-off target (~0.25 chroma-HF at the blinds) without collateral. The two
candidates that move the number do so by the wrong mechanism: `mlri` lowers it by
**chroma-blur** (and fails the explicit battery gate), and the proposed in-demosaic
**chroma-difference median** does **not** fix the dense-periodic blinds (it makes
the metric *worse*) and **fails the adversarial real-colour test**. The median *is*
a small, clean win on **sparse/neutral** false colour (the battery zone-plate), but
that win does not transfer to the dense-periodic blinds. ACR-NR-off apparently uses
a directional/adaptive chroma reconstruction the tested clean-room options lack.

**PROPOSE-not-ship.** Everything below is gated/default-OFF; the owner signs off
before any default change.

---

## 1. The artifact (re-verified, not clipping)

`rcd`-demosaiced lrt-cinema renders show blue/cyan/magenta false-colour "sawtooth"
streaks on the fine horizontal venetian blinds at the bright upper-left clerestory
windows of DSC_4053 (= LRT_00001, the indoor Martinez faire hall). It is demosaic
luma↔chroma aliasing on a **near-Nyquist horizontal grating**: the slats undersample
R/B vertically, so the false colour alternates row-to-row with Bayer phase. This is
**not** highlight clipping (independently established: 79% of the false-colour pixels
are unclipped) — it is a real demosaic-quality gap.

**Worst-spot ROI (our render coords, 4032×6032):** `y 1350–1660, x 150–1400`.

The 4×-zoom crops (`/tmp/fc_crops/zoom4x_*.png`, committed harness regenerates them)
show it directly: `rcd` has a broad cyan/magenta wash over the slats + colored
speckle on the vertical mullion; `ACR-NR-off` is near-neutral with crisp slats.

## 2. Metric + alignment (re-verified)

`tools/demosaic_falsecolor/metric.py` — the owner's chroma-high-frequency metric:
sRGB→linear→XYZ→Lab, chroma = hypot(a\*, b\*), mean |chroma − 1×5 horizontal
box-mean| over the ROI (= the horizontal chroma variation = the streaks). A
**vertical companion** (`axis='v'`, 5×1) is reported alongside to catch gaming (a
horizontal-only smoother would crush H without fixing the artifact). The metric
**only reads production-encoded sRGB TIFFs** — it never re-encodes (encode rule #1).

Alignment was **confirmed empirically**, not assumed: luma cross-correlation over a
±12 px window peaks at **(dy, dx) = (−8, −8), ncc 0.998** — matching the prompt. The
ACR/LRT references (4016×6016) are read at `off=−8`; ours (4032×6032) at `off=0`.

**Zero-render baseline (existing TIFFs):**
- testrun `rcd` (`lrt-cinema-testrun/tiff_faithful/LRT_00001.tif`): H **0.558** (≈0.51 regime ✓)
- ACR-NR-off (`lrt-export/NR-off/DSC_4053.tif`): H **0.277** (≈0.25 target ✓)

**Fresh-render validation:** a fresh `rcd` render via the production CLI reproduces
the testrun (H **0.560**; numpy ≡ numba to 1 code at the ROI). The 1509-code delta
vs the *stale* testrun TIFF is the recent WB-Tint fix (a **global** tone shift, not
the blinds — smooth-patch mean diff 766 codes, per-channel R+251/G−74/B−92), so the
fresh-vs-fresh A/B below is unaffected.

> **Render recipe (all A/B on `--backend numpy`):**
> `python3 -m lrt_cinema render --input "<SEQ>" --output <DIR> --dcp "<D750 Camera Standard.dcp>" --target lrtimelapse --demosaic <X> --capture-sharpen off --from-frame 0 --to-frame 1 --backend numpy`
> The chroma-median runs **only on the numpy reference** (the numba twin
> `rcd_rggb_refined` is unchanged), so the median variants MUST be rendered/measured
> on `--backend numpy`. `PYTHONPATH=<worktree>/src` is mandatory (the editable
> install otherwise resolves `lrt_cinema` to the main checkout).

## 3. Blinds + control spots (fresh renders, numpy)

`chroma_hf` H/V at the blinds and two **bright** control spots (dark spots dropped —
the metric inflates near black where a\*/b\* blow up, exceeding visibility). Controls
chosen to localise the gap: a bright-detailed floor patch and a bright-smooth
background wall.

| method | blinds H/V (lum 0.77) | floor-tiles H/V (0.43) | bg-wall H/V (0.32) |
|---|---|---|---|
| **rcd** (baseline) | 0.560 / 0.896 | 0.589 / 1.030 | 0.382 / 0.408 |
| mlri | **0.327 / 0.444** | 0.326 / 0.480 | 0.316 / 0.307 |
| dcb | 0.629 / 1.030 | 0.686 / 1.118 | 0.510 / 0.529 |
| menon | 0.586 / 0.921 | 0.599 / 1.049 | 0.401 / 0.427 |
| rcd+med3i1 | 0.622 / 0.971 | 0.648 / 1.046 | 0.377 / 0.414 |
| rcd+med5i1 | 0.692 / 1.083 | 0.732 / 1.167 | 0.396 / 0.443 |
| rcd+med5i2 | 0.701 / 1.118 | 0.777 / 1.230 | 0.405 / 0.454 |
| **ACR-NR-off (TARGET)** | **0.277 / 0.374** | 0.321 / 0.493 | 0.311 / 0.301 |

Readings:
- **dcb is WORSE than rcd** (0.629) — its libraw false-colour handling does not help
  this content. Rejected.
- **menon ≈ rcd** (0.586) — expected, our `rcd` already uses Menon's directional
  a-posteriori R/B + refining; that lever is pulled.
- **The chroma-median makes the blinds WORSE** (rcd 0.560 → med5i1 0.692 →
  med5i2 0.701), monotonic in window/iters. It is near-no-op on the smooth bg-wall
  (0.382→0.377–0.405) — it doesn't hurt non-grating content — but it cannot fix the
  dense-periodic grating (mechanism in §5). **No parameter rescues it.**
- **mlri is the only option that lowers the blinds** (0.327), and it tracks
  ACR-NR-off across *every* region (bg-wall 0.316 vs 0.311; floor-tiles 0.326 vs
  0.321). But this is **chroma-blur**, not clean reconstruction (§4).

**Attribution (the within-pipeline control):** rcd/mlri/menon/dcb are the *same NEF
through the same pipeline*, changing only `--demosaic` — a confound-free demosaic
isolation (no tone/WB/encode difference). That mlri(our-pipeline) ≈ ACR(its-pipeline)
across regions of widely different brightness/content is strong evidence the
pipeline-tone difference is negligible **for this metric** and the gap is genuinely
**demosaic**, not a global render difference.

## 4. Battery gate (Kodak-24 + linear charts)

`tools/demosaic_bench/run_battery.py` (24 Kodak + synthetic charts). The winner
"MUST NOT regress the battery."

| method | CPSNR↑ | SCIELAB↓ | zipper%↓ | MTF50P↑ | falseClr↓ | isoΔE↓ | grat2↑ | grat3↑ |
|---|---|---|---|---|---|---|---|---|
| bilinear | 30.25 | 1.698 | 40.33 | 0.2269 | 36.250 | 2.715 | 0.343 | 0.235 |
| Malvar2004 | 35.60 | 1.016 | 24.91 | 0.2915 | 26.993 | 6.544 | 0.123 | 0.041 |
| **our-RCD** | **39.03** | 0.750 | 6.60 | 0.3116 | 18.521 | 4.376 | 0.186 | 0.173 |
| our-RCD+med3i1 | 39.12 | 0.758 | 6.25 | 0.3117 | 16.469 | 4.376 | 0.096 | 0.078 |
| our-RCD+med5i1 | 38.99 | 0.795 | 6.72 | 0.3117 | **14.518** | 4.374 | 0.096 | 0.194 |
| our-RCD+med3i2 | 39.15 | 0.764 | 6.25 | 0.3117 | 15.988 | 4.376 | 0.096 | 0.078 |
| our-RCD+med5i2 | 38.75 | 0.835 | 7.25 | 0.3116 | 13.791 | 4.374 | 0.096 | 0.194 |
| our-MLRI | 37.28 | 0.889 | 11.71 | 0.3114 | **37.112** | **0.286** | 0.891\* | 0.498 |
| Menon2007 | 39.10 | 0.727 | 7.34 | 0.3115 | 15.153 | 4.372 | 0.251 | 0.173 |

- **mlri fails the gate outright:** CPSNR 39.03→37.28, zipper 6.60→**11.71**,
  falseClr 18.5→**37.1 (worst in class, above bilinear's 36.3)**. Its low blinds
  number co-exists with worst-neutral-false-colour because it produces *smooth
  low-horizontal-frequency* false chroma (blobs the H-metric and the zone-plate
  punish differently) — the blur-family signature, corroborated by **isoΔE 0.286**
  (it chroma-blurs a real isoluminant edge *more than bilinear's 2.715*). **mlri
  cannot be the recommended winner.**
- **The median is a genuine *clean* battery win — but only for sparse/neutral false
  colour:** falseClr drops monotonically 18.5 → 16.5 (3i1) → 14.5 (5i1) → 13.8 (5i2),
  beating even Menon (15.15) at 5i1; CPSNR is **held or improved** (3i1 39.12; 5i1
  38.99), zipper held/improved, MTF50P untouched (green is fixed), isoΔE unchanged
  (4.376→4.374 — unlike mlri, the median does NOT chroma-blur the real isoluminant
  edge). This is a real, defensible win for the case where the median works:
  **impulsive false colour on a locally-flat-chroma neighbourhood** (the zone-plate's
  true chroma is zero, so any chroma removed is false).

## 5. Why the median wins the zone-plate but loses the blinds (the crux)

The two results are not contradictory — they measure different content:
- **Zone-plate falseClr** = total chroma over a region whose **true chroma is zero**.
  A median rejects the impulsive false chroma → pulls toward zero → **wins**.
- **Blinds chroma_hf** = local variation over a region with **dense periodic
  near-Nyquist chroma** (real slat structure + the false-colour alternation riding
  on it). A median is **edge/rank-preserving, not low-pass**; on a dense periodic
  pattern (period < window) it preserves — and can sharpen — the alternation →
  variation goes **up**.

The deep reason no clean-room demosaic-only option hits 0.25: at the blinds the
false colour is a dense periodic near-Nyquist chroma pattern **locally
indistinguishable from a real near-Nyquist colour pattern**. Every option faces a
forced choice:
- **edge-preserving (median):** preserves real → preserves false → blinds
  unchanged/worse. Clean, useless on this artifact.
- **low-pass (mlri's bilinear residual fill):** removes false → removes real too →
  blinds drops, but it is blur (isoΔE 0.286 proves it smears real colour).
- **directional a-posteriori (Menon, which rcd already uses):** ≈ rcd. Pulled.

Separating dense-periodic-real from dense-periodic-false needs the **adaptive chroma
step ACR has and this task scoped OUT** (the 0.25→0.13 colour-NR). The owner's
premise — that ACR-NR-off's 0.25 is a pure-demosaic feat — holds, but it apparently
relies on a directional/adaptive chroma reconstruction none of the tested clean-room
options replicate. (Stated without claiming knowledge of ACR's internals.)

## 6. Adversarial real-colour test (the keystone)

The danger of any false-colour suppression is erasing **real** fine colour. Test:
mosaic a known **isoluminant near-Nyquist colour grating** (the blinds' frequency +
geometry: horizontal stripes, chroma varying vertically), demosaic, and measure how
much of the **true** chroma modulation survives —
`metrics.chroma_amplitude_recovery` projects the reconstruction's (a\*,b\*)
modulation onto the ground truth (rejecting orthogonal aliasing). Pure-demosaic
(no pipeline, no encode → rule #1 N/A). Read **relative to baseline rcd** (every
Bayer demosaic attenuates real chroma near Nyquist — the sampling limit, not a
defect).

**Falsifier:** *a method that attenuates the real grating substantially MORE than
rcd is smearing real colour.* Run on rcd / mlri / median:

- **2-colour grating (`grat2`) is a DEGENERACY — discard for mlri.** Every pixel
  lies on one line `R = a·G + b` in (G,R), which is exactly MLRI's linear tentative,
  so MLRI zeroes its residual **by construction** → a false "perfect" 0.891. Verified
  by adding a 3rd off-line isoluminant colour (`grat3`): MLRI drops **0.891 → 0.498**.
  So `grat2` certifies nothing about MLRI; `grat3` is the fair probe.
- **The median FAILS the falsifier:** `grat3` med3i1 **0.078 < rcd 0.173** — it
  attenuates the real periodic chroma *below baseline* and **distorts** it (a
  period-4 slice: GT modulation peak-to-peak 20.1, rcd 53.5, med5 **56.0**,
  mis-aligned). This is the **same mechanism** that worsens the blinds: the median
  cannot tell dense-periodic-real from dense-periodic-false, so it mangles both. The
  adversarial test fired on exactly the operator one might propose.
- **mlri** recovers more than rcd on `grat3` (0.498 vs 0.173) but this is the
  blur-family flattening the aliasing toward the smooth truth — consistent with its
  isoΔE 0.286, not evidence of fidelity.

## 7. 1:1 native crops

`tools/demosaic_falsecolor/crop_blinds.py` → `/tmp/fc_crops/` (regenerable). Exact
ROI pixels straight from each production sRGB TIFF (16→8-bit `>>8`, **no resample,
no tone change**), ACR shifted by the measured −8,−8. `blinds_<opt>.png` (full
310×1250 ROI) + `zoom4x_<opt>.png` (4×-nearest sub-tile of a slat-dense corner).
Eyeball: rcd = broad cyan/magenta wash; med5i1 ≈ rcd (no help); mlri = less wash but
softer; **ACR-NR-off = cleanest, crisp slats + neutral chroma**.

## 8. Recommendation

1. **Do NOT change the default demosaic for the blinds.** No clean-room
   demosaic-only option reaches ~0.25 without collateral:
   - `dcb`, `menon` — no help (worse / ≈ rcd).
   - `mlri` — closest to 0.25 (0.327) but by **chroma-blur**; **fails the battery
     gate** (falseClr 37.1 worst-in-class, CPSNR/zipper regress). Not recommendable.
   - **rcd + chroma-difference median** — **does not fix the blinds** (makes the
     metric worse) and **fails the adversarial real-colour test** (smears a real
     periodic colour grating below baseline). Reject *for this artifact*.
2. **The chroma-median may be worth keeping as an OPT-IN tool for sparse/neutral
   false colour** — it is a clean battery win there (falseClr −4 at 5i1, CPSNR flat,
   MTF50P/isoΔE untouched) and is already env-gated + default-OFF + byte-exact at
   identity (`LRT_RCD_CHROMA_MEDIAN`). It is **not** the answer to the blinds. If
   kept, it should be plumbed/documented as "impulsive/neutral false-colour cleanup,"
   never as a blinds fix, and (if ever defaulted on) ported to the numba twin.
3. **The real lever for the blinds is the scoped-OUT colour-NR / an adaptive
   directional chroma reconstruction** (what carries ACR-NR-off's 0.25→0.13 and,
   apparently, much of its 0.51→0.25). That is a separate work item from
   demosaic-only quality and should be tracked as such.

## 9. Reproduce

```
# baseline + alignment (zero render):
PYTHONPATH=<wt>/src python3 - <<'PY'
import sys; sys.path.insert(0,'tools/demosaic_falsecolor'); import metric as M
ours=M._srgb01('.../lrt-cinema-testrun/tiff_faithful/LRT_00001.tif')
acr =M._srgb01('.../lrt-export/NR-off/DSC_4053.tif')
print('align', M.align_offset(acr, ours))        # -> (-8,-8, 0.998)
print('rcd  H', M.chroma_hf(ours))               # -> ~0.558
print('ACR  H', M.chroma_hf(acr, off=-8))        # -> ~0.277
PY

# render a median variant (numpy!) and measure:
LRT_RCD_CHROMA_MEDIAN=5 LRT_RCD_CHROMA_MEDIAN_ITERS=1 PYTHONPATH=<wt>/src \
  python3 -m lrt_cinema render --input "<SEQ>" --output /tmp/med5i1 \
  --dcp "<D750 Camera Standard.dcp>" --target lrtimelapse --demosaic rcd \
  --capture-sharpen off --from-frame 0 --to-frame 1 --backend numpy

# battery (incl. grat2/grat3 adversarial cols) + crops:
PYTHONPATH=<wt>/src python3 tools/demosaic_bench/run_battery.py /tmp/kodak
PYTHONPATH=<wt>/src python3 tools/demosaic_falsecolor/crop_blinds.py /tmp/fc_crops
```

## 10. Artifacts

- Metric/alignment: `tools/demosaic_falsecolor/metric.py`
- Crops: `tools/demosaic_falsecolor/crop_blinds.py` → `/tmp/fc_crops/`
- Adversarial chart: `tools/demosaic_bench/charts.py`
  (`isoluminant_color_grating`, `isoluminant_color_grating3`)
- Recovery metric: `tools/demosaic_bench/metrics.py` (`chroma_amplitude_recovery`)
- Battery wiring: `tools/demosaic_bench/run_battery.py` (median rows + grat2/grat3)
- The median itself (env-gated, default OFF): `src/lrt_cinema/_rcd_demosaic.py`
  (`_chroma_median`, `LRT_RCD_CHROMA_MEDIAN` / `_ITERS`)
