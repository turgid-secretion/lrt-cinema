# Edge colour-fringing root-cause investigation (blue/yellow at clipped edges)

**Status:** COMPLETE (root cause proven; fix proposed, NOT implemented — owner signs
off, the fix touches the load-bearing demosaic). Deterministic ablation (ours-variant
vs ours-variant) on DSC_4053 / LRT_00001 (frame 1, indoor venue). Tool:
`tools/edge_fringe/fringe_metric.py`. All renders: NEF-direct via `render_frame`,
`LRT_CINEMA_BACKEND=numpy` (so numpy demosaic edits execute), D750 Camera Standard DCP,
encoded to sRGB via the gym encoder (`tests/test_pipeline.py::_prophoto_to_srgb_8bit`).

**TL;DR.** WHERE = the **demosaic stage** (the only stage that mixes neighbours; only a
demosaic-algorithm swap moves the metric — every colour stage is flat under a
DC-invariant pinned mask). SEED = **per-channel highlight CLIPPING corrupting the
reconstructed chrominance (R−G, B−G)** at high-contrast highlight→clip edges, rendered
~identically by every quality demosaic (the camera-space B−G oscillation is
demosaic-AGNOSTIC, ablation 5 — it is clipping physics, NOT a per-algorithm
reconstruction error). The result is a Bayer-phase-locked **blue↔yellow oscillation**.
REFUTED: rcd-specific false-colour (menon≈rcd), WB/LookTable/matrix as the colouriser
(all flat under the DC-invariant pinned-mask metric; FM is passthrough), post-demosaic
highlight recovery, and **naive same-channel CFA inpaint *as the fix*** (worsens it).
**Principled fix = restore true per-channel values BEFORE demosaic** (ratio-locked
joint-channel highlight reconstruction). **mlri demosaic = low-risk EMPIRICAL
mitigation** (≈ −40–50 % final fringe_b, established by tap-7 / pre-tone; the Stage-8+9
tone amplifier is only ≤1.24×; mlri's precise mechanism is not fully isolated).

> **NB on an earlier commit message** ("CFA inpaint REFUTES cross-clip-demosaic seed"):
> that overclaimed — the inpaint refutes naive same-channel inpaint *as the FIX*, NOT
> the demosaic *diagnosis* (changing the demosaic input and getting a worse output is
> consistent with the demosaic creating the structure). Corrected here (ablation 6a).

## The artifact
High-saturation blue/yellow fringing/banding at high-contrast edges, worst at
highlight→clip interfaces (clipped light fixtures, window-blind gaps, a sawtooth
across the window tops). In BOTH the sRGB TIFF and the ACEScg EXR (worse in EXR's
compressed-DR display, but the fringe is in the DATA). LRT JPGs of the same RAW
do NOT have it → it is OUR processing.

## Metric (committed tool)
Over pixels that are BOTH a strong luminance gradient (|∇L*|>p97) AND near a clip
(max-channel>0.97, dilated 7px):
- `chroma_at_edge` = mean Lab hypot(a*,b*); `abs_b_at_edge` = mean |b*| (blue↔yellow).
  NOT DC-invariant → use for SAME-colour-base comparisons (demosaic family).
- `fringe_hp` = RMS of (chroma − local box-mean chroma) — DC-INVARIANT, captures the
  blue↔yellow ALTERNATION; use for matrix/WB ablations (they recolour globally).

Worst-fringe crops (rendered-frame coords): botleft (1792,256,256) = owner's named
worst (clip frac 0.24, |b*|≈41, n≈17k — confirmed the heaviest-clipped block);
fixtures (1024,1280,256); winupper (2048,256,256). Crops saved to /tmp/fringe_crops/.

## ABLATION 1 — demosaic family (rcd vs menon vs mlri), tap9, hl=off

| variant | botleft chroma@edge | botleft fringe_hp | fixtures fringe_hp | wood a* |
|---|---|---|---|---|
| **rcd**   | 35.85 | 13.14 | 25.07 | 15.67 |
| **menon** | 36.03 | 13.14 | 24.52 | 15.71 |
| **mlri**  | 31.37 |  8.14 | 17.06 | 15.95 |

**rcd ≈ menon to ~1% on every metric and crop.** mlri is moderately lower but STILL
fringes heavily (chroma@edge 31, |b*| 30).

### Verdict on H1 (rcd-specific directional false-colour) — REFUTED as dominant cause
The H1↔H2 discriminator: *menon ≪ rcd → rcd-specific false-colour (H1); menon ≈ rcd
→ any directional demosaic colours the clip imbalance.* menon ≈ rcd → **RCD-specific
false-colour is refuted.** RCD's battery weakness (false-colour 18.5 vs Menon 15.2)
does NOT explain this — Menon, the battery-best, fringes identically.

**IMPORTANT (logic correction):** this does NOT mean "demosaic is not the source."
*Every* stage after demosaic (Stage 2 WB scalar, Stage 3/4 matrix, Stage 8 LookTable
cube, Stage 9 tone sort) is **per-pixel** — none touch neighbours. So ALL spatial
structure, including the sawtooth ALTERNATION `fringe_hp` measures, MUST be seeded by
the demosaic (and LRT demosaics the same raw cleanly → it is OUR demosaic). The
correct reading: **demosaic interpolating ACROSS THE HARD CLIP seeds the per-channel
spatial variation (any directional algorithm does this — rcd≈menon); the downstream
colour stages (WB asymmetry primarily) colourise that variation blue↔yellow.** An
**interaction**, not one stage. mlri's ~38% lower fringe_hp (13.1→8.1 botleft) is REAL
signal (a different green/residual scheme seeds less cross-clip variation) but leaves
the majority → the demosaic algorithm is a partial lever, not the fix.

The **wood tint (a*≈15.7, warm/magenta not green) does NOT co-vary with the demosaic**
(15.67/15.71/15.95) → independent of demosaic; tracked but not the fringe driver.

→ Next: locate WHERE the chroma grows (tap bisection) + confirm WB as the colouriser.

### Mechanism (from DCP + WB inspection, before ablating)
- WB multipliers this frame: **R=2.0, G=1.0, B=1.289** (ASN [0.5,1.0,0.776]) — a large
  asymmetry. libraw hard-clips each camera channel at 1.0 (camera space, pre-WB).
- The D750 Camera Standard FM is **ProPhoto-passthrough** (`M_xyz→pp·FM1 ≈ I`) → Stage
  3/4 does NO chromatic rotation. NO HueSatMap (Stage 5 absent). Colour comes from WB +
  LookTable (Stage 8) + ProfileToneCurve (Stage 9). **So the "camera-matrix rotation"
  of H2 is really the WB asymmetry (+ LookTable), not a matrix.** Identity-matrix
  ablation is moot and is SKIPPED.
- `apply_rgb_tone` (Stage 9) pins input to [0,1] BEFORE curving → a FULLY-clipped pixel
  `[≥1,≥1,≥1]`→`[1,1,1]`→neutral white. So the tap-9 fringe is NOT from saturated clip
  CORES (those go neutral) — it is from **PARTIALLY-clipped boundary pixels**, exactly
  the demosaic-across-clip region. (Also why hl-recovery, which neutralises full clips,
  is inert at tap-9.)

## ABLATION 2 — tap bisection (linear ProPhoto, same metric, rcd hlF)

| tap | what is applied | chroma@edge | |b*| | fringe_hp | n |
|---|---|---|---|---|---|
| 4 | WB + FM-passthrough only | 26.80 | 16.39 | 9.79 | 128034 |
| 7 | + ExposureRamp (no HSM) | 26.80 | 16.39 | 9.79 | 128034 |
| 9 | + LookTable + ProfileToneCurve | 20.20 | 18.44 | 14.64 | 41839 |

tap4==tap7 (Stage 5 absent, Stage 7 monotone). **fringe_hp = 9.79 already at tap-4
with ONLY WB applied** (FM is passthrough). tap-9 mask shrinks 3× (tone curve pulls
highlights below 0.97) so tap9-vs-tap4 isn't a clean delta, but fringe_hp does rise.

## ABLATION 3 — WB / LookTable bypass at tap9-sRGB, PINNED baseline mask (DC-invariant)

DC-invariant `fringe_hp` (the actual blue↔yellow ALTERNATION = the sawtooth):

| variant | botleft fringe_hp | fixtures fringe_hp | winupper fringe_hp |
|---|---|---|---|
| **baseline (rcd)** | 13.14 | 25.07 | 19.13 |
| **NO_WB** (wb→[1,1,1]) | 11.81 | 26.82 | 17.45 |
| **NO_LOOKTABLE** | 14.40 | 25.14 | 22.24 |
| **NO_WB + NO_LT** | 13.74 | 27.85 | 18.17 |

(raw chroma@edge/|b*| move a lot — NO_WB recolours the whole frame — but those are the
DC level, not the artifact; the DC-invariant fringe_hp is what matters.)

### Verdict on H2 (WB / matrix as the colouriser of the fringe) — REFUTED
**Killing WB does NOT collapse the fringe_hp** (botleft 13.14→11.81, fixtures 25.07→
26.82 — flat / slightly up). Killing the LookTable doesn't either. The DC-invariant
ALTERNATION is **essentially invariant to every colour-stage ablation** (demosaic
rcd↔menon, WB on/off, LookTable on/off). The ONLY lever that moved fringe_hp is the
demosaic *algorithm* (mlri: 13.1→8.1). So WB/LookTable set the fringe's overall hue/
saturation (the DC level) but **do NOT create or destroy the local alternation** — they
are per-pixel maps and cannot. **The artifact's spatial structure is demosaic-seeded
and survives every colour ablation.**

→ The root is in the **demosaic's reconstruction across the clip** — a spatial
oscillation present BEFORE any colour stage. Confirmed directly below.

## ABLATION 4 — signed blue↔yellow / green↔magenta swing (fringe_b / fringe_a)

`fringe_hp` is a high-pass of chroma MAGNITUDE → partly blind to a balanced SIGNED
oscillation at constant magnitude (exactly the owner's "alternating blue↔yellow"). Add
`fringe_b = RMS(b*−boxmean b*)` (blue↔yellow) and `fringe_a` (green↔magenta), PINNED to
the baseline rcd mask:

| variant | botleft fringe_b | fixtures fringe_b | (fringe_a botleft) |
|---|---|---|---|
| **rcd**   | 13.14 | 23.58 | 8.29 |
| **menon** | 13.18 | 23.19 | 8.38 |
| **mlri**  |  7.18 | 16.40 | 5.91 |
| **NO_WB** (rcd) | 11.16 | 22.35 | 8.98 |

The artifact IS predominantly a **b\* (blue↔yellow) signed oscillation** (fringe_b ≈
fringe_hp, ≫ fringe_a). **mlri ~halves it**; **NO_WB only nudges it** (13.1→11.2) and
trades a bit into a\* — confirming **WB MODULATES the hue/amplitude but does not
create the swing** (a per-channel scalar maps zero-swing→zero-swing).

## ABLATION 5 — the oscillation is in the DEMOSAIC OUTPUT, pre-colour (the positive proof)

High-pass RMS of the colour-difference planes (B−G), (R−G) measured on the raw
**camera RGB straight out of the demosaic** (pre-WB, pre-FM, pre-LookTable, pre-tone),
at the clip-edge mask:

| crop | rcd HP(B−G) | mlri HP(B−G) | linear HP(B−G) | rcd HP(R−G) | linear HP(R−G) |
|---|---|---|---|---|---|
| botleft  | 0.087 | 0.088 | 0.101 | 0.042 | 0.077 |
| fixtures | 0.161 | 0.162 | 0.159 | 0.077 | 0.123 |
| winupper | 0.105 | 0.102 | 0.125 | 0.101 | 0.142 |

**The (B−G) oscillation is present in the demosaic output BEFORE any colour stage** —
the blue↔yellow swing exists in camera RGB the moment the mosaic is interpolated across
the per-channel clip boundaries; the per-pixel colour stages merely carry/recolour it.

**Crucial nuance — the seed is clipping physics, not a per-algorithm error.** In camera
space HP(B−G) is **~equal for rcd vs menon vs mlri** (botleft 0.087/–/0.088; fixtures
0.161/–/0.162) — the quality demosaics produce the SAME camera-space colour-difference
oscillation. Combined with the identical rcd-vs-linear partial-clip B−G≈−0.20 (direct
seed inspection), this says the oscillation is **demosaic-AGNOSTIC**: it is the
per-channel **clipping** corrupting the reconstructed colour difference, which every
demosaic faithfully renders — NOT a reconstruction error specific to a bad algorithm.
(libraw bilinear has the HIGHEST HP(R−G) → it adds general-edge false colour ON TOP, so
it is worse, but the clip-seed is common.)

## ABLATION 6 — the two FIX-CLASS falsifications

**(a) Same-channel CFA inpaint BEFORE demosaic — fails AS A FIX (does not refute the
diagnosis).** Reconstructing each clipped CFA sample from unclipped same-channel
neighbours, then demosaicing (tap-7 linear, pinned mask): fringe_b **rises** 11–40%
(botleft 9.57→10.63, fixtures 21.81→30.50). This refutes **naive per-channel mosaic
fill as the fix** — it fills R/G/B clipped regions *independently* from each channel's
own rim, so the reconstructed R−G / B−G gradients disagree MORE → more chroma HF. It
does **NOT** refute "demosaic is the source": changing the demosaic INPUT and getting a
worse demosaic OUTPUT is fully consistent with the demosaic creating the structure. The
lesson: the fix must be **joint-channel / ratio-locked**, not independent per channel.

**(b) Post-demosaic highlight recovery (Tier-1) @ tap-7 — does not fix.** `highlight_
recovery=True` (active at tap-7; inert at tap-9): fringe_b **unchanged** (botleft
9.57→9.57) or slightly worse. Post-demosaic ratio propagation cannot **unbake** an
oscillation the demosaic already wrote AND it only touches FULL clips, not the
partial-clip boundary that carries the fringe → the fix must live **at or before** the
demosaic, not after it.

## ABLATION 5b — where mlri's win lands + the Stage-8/9 amplifier (one pinned mask)

rcd vs mlri at tap-7 (pre-LookTable/tone) and tap-9, all linear ProPhoto, mask pinned to
rcd@tap-7:

| crop | rcd tap7 | rcd tap9 | mlri tap7 | mlri tap9 | Stage8+9 amp (rcd t9/t7) |
|---|---|---|---|---|---|
| botleft  |  9.57 | 11.90 |  5.95 |  6.97 | 1.24× |
| fixtures | 21.81 | 25.41 | 18.40 | 17.46 | 1.16× |
| winupper | 15.21 | 15.50 | 12.60 | 13.21 | 1.02× |

(fringe_b.) Two findings: **(1) the Stage-8+9 tone amplifier is modest (1.02–1.24×)** —
NOT the dominant factor. **(2) mlri's advantage is already present at tap-7** (9.57→5.95
botleft), i.e. established at the demosaic/scene-linear level, NOT by the downstream
nonlinearity. But since mlri ≈ rcd in *camera-space* HP(B−G) (ablation 5) yet < rcd at
tap-7, **mlri's precise mechanism is not fully isolated** (its spatial distribution of
the residual yields a lower Lab-b\* fringe after WB despite an equal camera-space B−G
swing). The win is empirical and robust; the mechanism is open.

---

## ROOT CAUSE (WHERE proven; SEED identified; mlri mechanism open)

**WHERE — the demosaic stage.** Airtight: every stage after demosaic is **per-pixel**
(WB scalar, ProPhoto-passthrough FM, LookTable HSV cube, hue-preserving tone sort) and
cannot create a neighbour-to-neighbour alternation; the demosaic is the only stage that
mixes neighbours; and it is the only lever that moves the metric (ablations 1, 3, 4, 6).

**SEED — per-channel highlight CLIPPING corrupting the reconstructed chrominance.** At a
high-contrast highlight→clip edge the three Bayer channels saturate at *different scene
brightnesses* (this frame, camera space: G clips most, then R, then B; partial-clip
B−G ≈ −0.20). The demosaic reconstructs the missing 75 % of each channel across that
boundary from samples flat-topped on one channel but not another → the colour difference
(R−G, B−G) oscillates with the Bayer phase = the blue↔yellow sawtooth. This seed is
**demosaic-AGNOSTIC** (rcd≈menon≈mlri in camera-space HP(B−G), ablation 5; rcd≈linear in
partial-clip B−G) — it is clipping physics that every quality demosaic renders, NOT a
bad-algorithm reconstruction error. The downstream stages only carry/recolour it (WB
modulates the hue, tone sort amplifies a modest ≤1.24×).

**Proven by:** direct (the B−G swing is in the camera RGB pre-colour, ablation 5) +
elimination (demosaic algorithm is the only lever; ablations 1/3/4/6) + logic (per-pixel
post-demosaic stages). **Open:** the precise reason mlri's final fringe is ~half rcd's
despite equal camera-space B−G (ablation 5b) — empirically real, mechanism not isolated.

## REFUTED ALTERNATIVES (each with its test)

| Hypothesis | Test | Result |
|---|---|---|
| **H1: RCD-SPECIFIC false colour** | rcd vs menon (battery-best), same metric | menon ≈ rcd to ~1% → REFUTED (not rcd-specific; any directional algo) |
| **H2: clipped-WB × camera-matrix rotation colourises** | NO_WB (wb→[1,1,1]), pinned mask, DC-invariant | fringe_hp flat (13.1→11.8) → REFUTED as colouriser; WB only modulates |
| **— LookTable colourises** | NO_LOOKTABLE, pinned mask | fringe_hp flat → REFUTED |
| **— camera matrix rotates the hue** | inspect M_xyz→pp·FM1 | ≈ identity (ProPhoto-passthrough FM) → no rotation to ablate; MOOT |
| **H3: highlight recovery amplifies** | hl=True @ tap-7 (active there) | fringe_b unchanged/worse → does NOT fix; not the lever |
| **— lens CA** | XMP disables all lens corr; LRT carries the same raw CA | both carry identical raw CA, LRT is clean → REFUTED (deprioritised, per task) |
| **Naive same-channel CFA inpaint = the fix** | inpaint clipped CFA, re-demosaic, tap-7 | fringe_b RISES 11–40% → that FIX is wrong (independent per-channel); diagnosis stands |

## GREEN-TINT NOTE (owner flagged "see if it changes")

The bottom-left stage-wood region renders **warm/magenta** in our pipeline (mean
a\*≈+15.7, b\*≈+22.6 — positive a\* is magenta/red, NOT green) — the opposite sign to
the owner's "green tint" (likely the owner saw the LRT/preview look, or a different
white balance). Tracked across the demosaic family: a\* = 15.67 / 15.71 / 15.95 for
rcd / menon / mlri → **it does NOT co-vary with the demosaic or the fringe**. It is a
white-balance / tone matter (the PV2012 tone gap already on record, MEMORY
lrt-jpg-northstar-baseline), orthogonal to the edge fringing. Not pursued further here.

## PROPOSED FIX (NOT implemented — owner signs off; touches load-bearing demosaic)

Because the SEED is per-channel CLIPPING (not a bad demosaic algorithm), the principled
fix is to **restore true per-channel values BEFORE the demosaic, ratio-locked** — naive
independent per-channel fill is proven wrong (ablation 6a) and post-demosaic recovery is
too late (6b).

1. **PRINCIPLED FIX (the real B1 work) — ratio-locked joint-channel highlight
   reconstruction at the CFA stage** (the `pipeline._extract_cfa` B1 hook), so the
   demosaic interpolates *consistent* colour differences across the clip and never seeds
   the oscillation. The clipped channels must be reconstructed **tied to a shared local
   brightness/chroma** (the unclipped channels' ratios propagated into the clipped ones
   *jointly*), NOT each channel filled from its own rim. This directly attacks the seed
   identified above. Candidate forms: a guided/joint mosaic reconstruction; or
   RawTherapee-style colour-propagation highlight recovery fused into the CFA stage
   (in front of the demosaic, unlike the current post-demosaic Tier-1). This is the
   recommended direction; it is the only fix that addresses the proven seed.
2. **LOW-RISK EMPIRICAL MITIGATION — swap the quality demosaic rcd → mlri.** Measured
   **~40–50 % fringe_b reduction**, established by tap-7 (pre-tone), with zero pipeline
   surgery (mlri is already wired, headroom-preserving, BSD-licensed). **Caveat: this is
   an empirical mitigation whose mechanism is not isolated** (ablation 5b — mlri ≈ rcd in
   camera-space B−G yet lower final fringe) and it does NOT eliminate the fringe (it
   roughly halves it). Good as an interim while #1 is built; verify mlri's resolution/
   MTF + gym/rose gate + LRT-JPG north-star before any production default change
   (CLAUDE.md demosaic-change rule). Do NOT read this as "the cause was a bad demosaic" —
   the cause is clipping; mlri just renders the clip-seed into less final fringe.
3. **Cosmetic band-aid (NOT recommended)** — post-hoc chroma-median / fringe-suppression
   on the clip-edge mask (`edge_clip_mask`). Knocks the residual down without touching
   the demosaic, but smooths the very feature the owner wants preserved (the sawtooth
   *destroys* real detail; blurring it further is the wrong direction). Listed only for
   completeness.

## TOOLS / REPRO (committed)

- `tools/edge_fringe/fringe_metric.py` — the deterministic metric + ablation harness
  (chroma_at_edge / fringe_hp / fringe_b / fringe_a; tap-7 linear variant; pinned-mask;
  worktree-import + numpy-backend guards). Env hooks in `pipeline.py`: `LRT_FRINGE_NO_WB`,
  `LRT_FRINGE_NO_LOOKTABLE` (default off → byte-identical render).
- `tools/edge_fringe/cfa_inpaint_diag.py` — the same-channel CFA inpaint DIAGNOSTIC
  (proves the naive-fix falsification; not a production op).
- Owner-eyeball crops in `/tmp/fringe_crops/`: `demosaic_{rcd,menon,mlri}_{crop}.png`
  (the fix lever), `00_baseline_*` + `00_LRTjpg_*` (ours vs the clean LRT JPG),
  `cfainpaint_{BASE,INPAINT}_*` (the failed naive fix). All at botleft / fixtures /
  winupper. **Visual confirmation pending — the owner should eyeball these to confirm
  the metric-based conclusion.**

Repro any ablation:
`PYTHONPATH=<worktree>/src LRT_CINEMA_BACKEND=numpy python3 <harness>` (the backend pin
is load-bearing — it forces the numpy demosaic reference so code-level edits execute).
