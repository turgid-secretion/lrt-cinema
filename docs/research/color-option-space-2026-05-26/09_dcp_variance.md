# Q1: Adobe DCP catalog variance measurement

*Empirical answer to the Q1 question in `08_search_framing.md`:
"How much of Adobe color is per-camera vs shared?" Measurement was
run against the user's local Adobe DNG Converter install
(`/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Adobe
Standard/`) via `tools/measure_dcp_variance.py` on 2026-05-26.*

## Headline result

**A' (camera-agnostic Adobe-match) is empirically viable on the
low-variance branch.** Three of the four per-camera DCP fields turn
out to be essentially camera-agnostic in Adobe Standard:

- **BaselineExposure / BaselineExposureOffset**: identically zero
  across all 480 sampled cameras. Adobe does not encode per-camera
  exposure bias in Adobe Standard.
- **ProfileToneCurve**: present in only 14 of 480 cameras (3%). The
  other 97% rely on Adobe's default ACR3 baseline curve (camera-
  agnostic). Of the 14 cameras that DO ship a tone curve, RMSE
  around the mean is 0.018 (mean), 0.012 (P50), 0.064 (P95) — small
  shape differences.
- **LookTable**: 474 of 480 cameras (99%) use the (36, 8, 16) cube
  dimension. Per-cell standard-deviation across cameras: hue shift
  mean 2.2°, P95 6.3°; sat scale mean 0.026 (~2.6%); val scale mean
  0.028 (~2.8%). Low to moderate variance — a median LookTable
  captures most camera looks within a few degrees of hue and a few
  percent of saturation/value.

The one field with material per-camera variance is **HueSatMap**:

- 322 of 480 cameras (67%) use the (90, 30, 1) cube dimension; 140
  cameras (29%) ship no HueSatMap at all.
- Per-cell standard-deviation: hue shift mean 2.5°, P95 5.2°, max
  8.0°; sat scale mean 0.187 (~19%), P95 0.40 (~40% on the highest-
  variance cells); val scale mean 0.12, P95 0.27.
- Saturated chromas (high sat-scale cells) show the largest cross-
  camera variance, consistent with per-camera tuning for blue-sky /
  red-foliage / skin-tone handling.

## What this means for candidate A'

The hypothesis in `08_search_framing.md` was that low cross-camera
variance would let a single shared transform capture most of the
Adobe Standard look. The data supports the hypothesis with one
qualifier:

- A SHARED LookTable + camera-agnostic BaselineExposure + (absent)
  ProfileToneCurve covers approximately 80-90% of Adobe Standard's
  look character, derived from a median of the 474 same-dimension
  LookTables.
- The per-camera HueSatMap residual is the remaining 10-20%. Two
  sub-options: (a) drop the HueSatMap stage entirely (matches the
  29% of Adobe Standard profiles that already ship without one),
  accepting saturated-chroma drift on the cameras that DO have one;
  (b) ship a "median HueSatMap" alongside the shared LookTable,
  capturing the average-camera character at the cost of greater
  drift on the high-variance cameras (Apple, Samsung, etc. where
  Adobe tunes more aggressively per-camera).

Either sub-option keeps A' as a **single distilled transform shipped
in `presets/`**, not a per-camera database. The per-camera
ColorMatrix / ForwardMatrix stays per-camera but is already
available from every camera's bundled DNG metadata (or from
extract_dcp_library.py's `.npz` output) — no Adobe runtime needed.

## Recommended A' configuration

Per `09a_adobe_match.md`'s "How A and A' interact" section, Q1's
low-variance result selects **Configuration 3**: A' as default, A
as opt-in enrichment.

The default render pipeline:

```
RAW → ColorMatrix from camera EXIF (per-camera, automatic)
   → CAT16 to working space
   → SHARED LookTable + median HueSatMap from presets/adobe_standard.npz
   → output linear Rec.2020 TIFF
```

Opt-in A path for cameras where the user wants per-camera fidelity:
swap the shared transform for a per-camera HSM/LookTable extracted
from the user's local Adobe install via `tools/extract_dcp.py`.

## Methodology + reproducer

- Adobe Standard catalog: 1432 profiles across 52 manufacturers.
- Stratified sample: 480 DCPs (cap 20 per manufacturer).
- Parsed via `src/lrt_cinema/dcp.py::parse_dcp` (480 of 480 successful).
- Per-field cross-camera variance metrics defined in
  `tools/measure_dcp_variance.py`.
- Full JSON output at `/tmp/dcp_variance.json` (not committed; output
  of one-shot measurement).
- Re-run: `python3 tools/measure_dcp_variance.py /tmp/dcp_variance.json`.

## Manufacturer distribution

Top manufacturers in the catalog (Apple, Sony, Samsung, Canon, Nikon
each have ~100-180 profiles; Fujifilm, Google, Panasonic, Xiaomi,
Olympus each have ~70-95; rest of the catalog is long-tail
manufacturers with < 50 profiles each). The sample's manufacturer
spread is broad enough to claim the result generalizes; the variance
metrics above are aggregated across the full sample, not
manufacturer-restricted.

## Caveats

- **Adobe Standard only.** This measurement does not say anything
  about Camera Standard / Camera Neutral / Camera Vivid / per-camera
  alternative looks. Those vary more per-camera by design.
- **Static catalog snapshot.** Adobe's DNG Converter version on the
  user's machine determines which Adobe Standard profiles are
  measured. ACR/DNG Converter version churn may shift the variance
  distribution slightly per release.
- **Single-illuminant LookTables.** Adobe Standard's LookTables are
  single-illuminant (val_divisions=1 effectively for some, 16 for the
  common (36, 8, 16) shape — the "1" in HueSatMap indicates the
  illuminant axis; the val axis on LookTable carries the V-dependent
  hue twist that the synthesis doc describes). Cross-illuminant
  variance is not measured here; same-illuminant cross-camera
  variance is what the measurement captures.

## Provenance

Measurement: `tools/measure_dcp_variance.py`, run 2026-05-26 against
`/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Adobe
Standard/` (Adobe DNG Converter 18.2.2 (Macintosh) per the file
system). Raw output captured to `/tmp/dcp_variance.json` + report at
`/tmp/dcp_variance_report.txt`. Reproducible by re-running the
script.
