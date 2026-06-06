# 250-frame sequence comparison — current pipeline vs LRT (north-star) + vs old + demosaic A/B

**Production sequence** (`2026 international faire timelapse`, first 250 frames =
DSC_4053–4302 ↔ LRT_00001–00250), rendered through the current overhaul-branch
pipeline (faithful sRGB, deflicker applied) and compared to the LRT JPG export
(north-star) and the old-pipeline TIFs (regression). Tools:
`tools/seq_lrt_compare.py` (tone/temporal, ÷6 downsample — valid for large-scale),
`tools/seq_demosaic_matrix.py` (cross-variant). Demosaic A/B done **full-res** (see
§3 — downsampling invalidates the demosaic comparison).

> **⚠️ CORRECTION (2026-06-05, B2 root-cause audit — `deflicker-rootcause-audit.md`).**
> The "deflicker ~3× under-application = #1 lever" claim below (§1, §5) is **REFUTED**.
> The defect is a **scalar-gain conflation**: a single no-offset gain `LRT≈g·ours`
> conflates *exposure* with *tone-shape*, so a fixed tone-curve difference drifts the
> gain and back-solves into a fake per-frame deflicker factor. The deflicker is
> correct at **1:1** on three independent grounds — it's short-term *by design* (so
> the long-term drift isn't its job), a linear preview test shows scaling ≥2× *worsens*
> flicker, and for small `d` it ~cancels in the ratio. **Keep `--deflicker-scale 1.0`.**
> The drift is the **PV2012 tone-curve-shape gap** (§11 = the real #1 lever). NB the
> gain table IS already linear (the tool linearizes before the fit; linear≈gamma gain
> to ~1%) — the problem is the *scalar gain* metric, not the bit depth; don't drive
> tone decisions off it.

## 1. North-star: current vs LRT JPG — mean ΔE2000 **1.20** (0.61–2.45)
Colour cast negligible (R/B gain Δ 0.006) → the gap is **brightness/tone, not colour**.
Temporal shape is a **U** (best mid-sequence), driven by the affine gain:

| frame | 1 | 50 | 100 | 150 | 200 | 250 |
|---|---|---|---|---|---|---|
| ΔE vs LRT | 1.45 | 1.04 | **0.68** | 0.82 | 1.45 | 2.30 |
| gain (LRT≈g·ours) | 0.941 | 0.963 | 0.984 | 1.010 | 1.042 | **1.081** |

The gain **crosses 1.0 at ~frame 110** — ours starts *brighter* than LRT, matches
mid-run, ends *darker*. ~~Root cause = deflicker under-application (~3×).~~
**SUPERSEDED — see the correction banner + `deflicker-rootcause-audit.md`.** This
**scalar no-offset gain conflates exposure with tone-shape**, so it back-solves a
fixed tone-curve difference into a fake deflicker factor. The deflicker is correct at
1:1 (short-term *by design*; linear preview test) and the drift is the **PV2012
tone-curve-shape gap** (§11), *correlated with the deflicker ramp by construction*
(Visual Deflicker is computed from the scene-brightness trend) — so the correlation
never proved causation. Keep `--deflicker-scale 1.0`; don't drive tone decisions off
this scalar-gain table.

## 2. Regression: current vs OLD pipeline — ~constant **3.66 ΔE** (NOT an overhaul regression)
~Constant across all 250 (3.46→3.84) with a blue-axis WB shift (B gain 0.904 vs R/G
~1.0). The XMP carries **Tint=20**, and the old renders **predate the merged wb-tint
fix #45** (2026-06-02). The overhaul's default path is **byte-exact** (synthetic
harness) + **gym = 0.026 on real data** → stages 1-9 unchanged. So the delta is the
pipeline having **improved** (it now applies the colorist's Tint=20; old dropped it) —
which moves us *toward* LRT. An improvement, not a regression.

## 3. Demosaic A/B — FULL-RES (linear vs rcd vs menon), 5 frames, edge-region ΔE + detail
Downsampling **invalidates** this comparison (the demosaic differs at the 1-2 px scale a
÷3 area-mean averages away). Full-res:

| demosaic | edge ΔE vs LRT | detail (HF energy) |
|---|---|---|
| linear (bilinear) | 2.530 | 0.0001 |
| **rcd** | 2.365 (**−0.17**) | 0.0004 |
| **menon** | 2.317 (**−0.21**) | 0.0004 |
| *LRT target* | — | *0.0002* |

cross-variant edge ΔE: menon↔rcd 0.78 · rcd↔linear 2.16 · menon↔linear 2.14

- **Demosaic helps edges, but modestly** (rcd −0.17, menon −0.21 on a ~2.5 edge gap),
  consistent across frames. **menon ≈ rcd vs LRT** (−0.05); rcd captures ~80% of the
  benefit at ~free cost.
- **rcd/menon are SHARPER than the LRT JPG; only bilinear is softer** (HF: linear
  0.0001 < LRT 0.0002 < rcd/menon 0.0004 — the 8-bit JPEG degraded LRT's fine detail).
  Our quality demosaics don't lack resolution — they exceed the JPEG reference.
- **The edge gap is mostly NOT the demosaic** — even menon leaves 2.32. The bulk =
  **missing ACR sharpening** (`apply_sharpness` is a no-op) + JPEG/8-bit + sub-pixel
  misalignment. The demosaic difference (rcd↔linear 2.16) is largely *orthogonal* to
  the LRT-gap direction.

## 4. Render timing (numba, 8 workers, cached DNG)
- Full faithful sRGB render: **1.5 s/frame** (incl. dnglab convert, 250 in 388 s).
- Demosaic **sequence throughput**: linear (w/ convert) 1.5 · **rcd 1.05** · **menon
  2.64** s/frame (cache-reused). So menon adds only **~+1.6 s/frame vs rcd** at
  multi-worker throughput — NOT the +7.4 s single-frame latency. rcd is ~free
  (numba, +0.05 s single-frame). menon is pure-numpy DDFAPD (no accel kernel).

## 5. Leverage ranking for matching LRT (CORRECTED 2026-06-05)
**PV2012 tone-shape (§11) ≫ sharpening (edges) > demosaic.** ~~deflicker ≫ …~~ —
the deflicker is NOT a lever (it's correct at 1:1; the "~3×" was a scalar-gain
conflation of exposure with tone-shape — `deflicker-rootcause-audit.md`).
1. **PV2012 tone emulation (§11)** — the U-shaped brightness/tone drift vs LRT is a
   tone-curve-SHAPE difference (ours≈dng_validate≠LRT), NOT the deflicker. The real
   #1 lever. (Confirm the deflicker-vs-tone split with the linear, per-frame
   jitter-vs-smooth test against the LRT JPGs.)
2. **Sharpening (D2 — SHIPPED 2026-06-05)** — the bulk of the *edge* gap;
   `apply_sharpness` is now a clean-room capture USM (`--capture-sharpen`,
   `capture-sharpening-d2`). Owner tunes its constants vs the LRT JPG.
3. **Demosaic (DONE)** — a small spatial refinement (−0.2 edge); real value is
   *absolute* quality (the battery). rcd is the value pick (Menon-tier, 4× numba).

## Caveats
- The demosaic A/B is on 69 intact + 5 full-res frames (I overfilled /tmp rendering
  3×250 full-res TIFs → some rcd/menon frames truncated). The demosaic is
  frame-invariant, so this is a sound sample. /tmp since freed.
- Old-vs-current is confounded by the #45 tint fix (old predates it); the comparison
  is "current is improved," not a clean per-op delta.
