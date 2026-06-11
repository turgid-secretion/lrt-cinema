# grading-sweep — validating the Stage-12 develop ops across their full range

The Stage-12 grading ops (`develop_ops.apply_hsl`, `apply_color_grade`) are
clean-room **best-public-approximations** of Adobe's HSL and Color-Grade panels —
Adobe's exact math is closed-source, so each carries free constants (band
centres, hue-rotation magnitude, tint strengths, the Blending/Balance mask
response). This harness exists to (a) catch structural regressions for free in
CI and (b) measure — and ultimately *fit* — those constants against Adobe.

## Why Adobe (ACR) is the only fidelity oracle

`crs:HueAdjustment*` / `crs:ColorGrade*` are **Camera Raw develop settings**, not
DNG-render parameters. So the tools we already trust as oracles **do not apply
them**:

- `dng_validate` (our north-star, gym 0.026) renders the DNG **DCP pipeline only
  (Stages 1-9)** — no Camera Raw develop engine. This is exactly why the ΔE ship
  gate is develop-ops-free and orthogonal to these ops.
- `darktable` / `RawTherapee` read a raw + **their own** sidecar params; they
  ignore Adobe's `crs:*` HSL/Color-Grade entirely and reimplement with different
  math.

So no open tool can check HSL/Color-Grade *fidelity*. The only Adobe truth is
**Adobe Camera Raw / Lightroom itself** (consistent with `docs/archive/VALIDATION.md`
§(a)). That splits the work into two tiers.

## Tier 0 — structural sanity (free, in CI)

`tests/test_grading_sweep.py` drives every HSL band and every Color-Grade wheel
across the **full slider range** on a synthetic linear-ProPhoto chart
(`chart.py`) and asserts the properties any correct knob must have: monotonicity,
hue-band / tonal-zone locality, neutral protection, identity-is-byte-exact, and
no invalid (negative / non-finite) channels. No external renderer; runs in CI.

This is the integration-level net **above** the Axis-1 oracle
(`tests/test_color_oracle.py`, which proves the defined math on chosen pixels) —
it catches a lever wired to the wrong field, a sign error that only shows at
range, or a dropped interpolation field.

## Tier 1 — Adobe fidelity (one-time, needs a licensed Photoshop/ACR)

The same XMP set drives both sides, so the comparison is apples-to-apples.

1. **Generate the sweep sidecars** (153 levers; full range of every band/wheel +
   Blending/Balance):

   ```sh
   python3 tools/grading_sweep/build_sweep_xmps.py --out /tmp/grading_sweep_xmps
   ```

2. **Render OURS** over a chart raw through the full pipeline (Stages 1-9 +
   develop_ops), sampling per-patch means:

   ```sh
   python3 tools/grading_sweep/run_sweep.py \
       --xmp-dir /tmp/grading_sweep_xmps --dcp <profile.dcp|.npz> \
       [--raw <chart.dng>] --out /tmp/grading_sweep_work/ours.json
   ```

   With no `--raw` it builds a synthetic flat-patch chart DNG from the test
   fixtures (needs `/tmp/dng_out/DSC_4053.dng` + `dnglab`). For a real run, shoot
   or synthesise a chart with broad hue/saturation/value coverage (a ColorChecker
   plus a hue ramp) and supply patch coordinates.

   > **PV caveat:** the sidecars are written `crs:ProcessVersion="11.0"` (PV2012 —
   > what LRT actually emits, and the era HSL belongs to). The Color-Grade panel
   > is PV4+; if ACR declines to apply the `crs:ColorGrade*` levers under PV2012,
   > bump those sidecars to `"15.0"` (PV5) for the Tier-1 run. (Real LRT output is
   > PV2012-locked, so ColorGrade reaches LRT only from an upstream LR edit.)

3. **Render ADOBE** — batch the *same* chart raw + each XMP through ACR. ACR is a
   Photoshop plugin and Photoshop is scriptable (ExtendScript / UXP): place
   `chart.<ext>` + `<lever>.xmp` together, `app.open()` the raw (ACR reads the
   sidecar), export `<lever>.tif`. Save the TIFFs once and check them in as a
   golden set — the same model as the checked-in `dng_validate` fixtures
   (Adobe-free *runtime*; Adobe-as-test-oracle).

4. **Compare** — re-run `run_sweep.py --adobe-dir <golden_tifs>`; it writes a
   per-lever mean/max ΔE2000 `compare.csv`.

## The payoff: fit our constants, don't just grade them

Tier 1 isn't pass/fail — it's **calibration data**. Each op has free constants:

| Op | Free constants |
|---|---|
| HSL | `_HSL_BAND_CENTERS_HEX`, `_HSL_HUE_MAX_HEX`, `_HSL_LUM_SAT_GATE` |
| Color Grade | `_CG_CHROMA_STRENGTH`, `_CG_LUM_STRENGTH`, the Blending→`p` map, the Balance→γ map |

Least-squares-fit them to minimise per-lever ΔE vs the ACR golden set — turning
"documented guess" into "fitted approximation" (the same move as the DCP matrix
being a Luther-floor least-squares fit).

**Honest ceiling:** fitting only tunes *our model's* free parameters. Adobe's
functional *form* is closed and differs, so a residual floor remains (like the
SSF Luther floor for the colour matrix). The fit shrinks the gap; it will not
zero it. Whether to instead adopt a *different, better* primitive (perceptual
spaces, CDL) is the separate question scoped in
`docs/research/v09-perceptual-grading-frontier.md`.
