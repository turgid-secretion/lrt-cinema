# ColorChecker test-harness fixtures

This directory holds (or *will* hold) the assets the ColorChecker
ΔE2000 test harness (`tests/test_colorimetric.py`) uses to validate
colorimetric accuracy of `lrt-cinema`'s `cinema-linear` preset against
published X-Rite ColorChecker Classic reference values under D55.

Methodology and the why-of-each-piece live in
[docs/archive/VALIDATION.md](../../../docs/archive/VALIDATION.md). This file is the
operational drop-in instructions.

## What ships here today

| File | Purpose | Already present? |
|---|---|---|
| `chart_reference.json` | 24-patch CIE Lab values under D50 *and* D55 (CAT02-adapted), full provenance, the threshold convention this harness gates on. | **YES** — derived from `colour-science`'s post-2014 BabelColor revision; do not edit by hand. Regenerate via the script noted inline if upstream values move. |
| `README.md` (this file) | Drop-in instructions. | YES |
| `chart.<RAW-ext>` | Your ColorChecker shot. | **NO** — user supplies. |
| `chart.<RAW-ext>.xmp` | Identity LRT XMP for the shot (no creative grade). | **NO** — user supplies. |

The test harness self-tests its math machinery against a synthesized
chart image without these files, so CI stays green on a fresh clone.
The threshold-gate leg of the test is `pytest.skip()`-ped until both
RAW and XMP are present in this directory.

## How to add your chart shot

1. **Shoot.** X-Rite ColorChecker Classic (24-patch). Controlled D55
   illuminant (a daylight LED panel calibrated to ~5500 K + 95+ CRI,
   or sunlight through a north-facing window mid-day, or a calibrated
   D55 lightbox). Camera locked to the same body + firmware as the
   timelapse footage being graded — WB multipliers are camera-specific.
   Exposure: chart white patch around middle-grey + 1 EV (~70 % on a
   linear scale), well clear of clipping.

2. **Drop the RAW.** Copy your file to
   `tests/fixtures/colorchecker/chart.<ext>` — `.NEF`, `.CR3`, `.ARW`,
   `.RAF`, `.DNG`, etc. The harness auto-detects the extension by
   globbing `chart.*` and skipping the `.xmp` / `.json` / `.md`
   companions.

3. **Author the identity XMP.** The companion file must be named
   `chart.<RAW-ext>.xmp` (same stem + suffix as the RAW, plus `.xmp`).
   *Identity* here means: as-shot WB, exposure = 0, every tone /
   colour develop op = its LR default. Critically, the XMP must
   carry `lrt:keyframe="1"` (or `xmp:Rating="1"`) so the
   `lrt-cinema` parser registers it as a keyframe — otherwise the
   render skips the frame and the test fails with a misleading
   "no keyframes parsed" message rather than a colorimetric one.

   Minimum viable identity XMP:

   ```xml
   <?xml version="1.0" encoding="UTF-8"?>
   <x:xmpmeta xmlns:x="adobe:ns:meta/">
    <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
     <rdf:Description rdf:about=""
       xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
       xmlns:lrt="http://lrtimelapse.com/ns/1.0/"
       crs:Exposure2012="0.00"
       lrt:keyframe="1"/>
    </rdf:RDF>
   </x:xmpmeta>
   ```

   Save next to the RAW. The harness copies both into a temp dir
   before rendering so this directory is never mutated.

4. **Run the tests.** From the repo root:

   ```bash
   pip install -e '.[dev,detect]'      # detect extra adds colour-checker-detection
   pytest tests/test_colorimetric.py -v
   ```

   You will see:
   - `test_colorimetric_self_test_with_synthetic_chart` → passes (no fixture needed).
   - `test_colorimetric_real_chart_through_cinema_linear` → runs.
     Pass criterion: mean ΔE2000 < 2.0 AND max ΔE2000 < 4.0 over the
     24 patches. **Expected to FAIL on today's pipeline** — see
     [V03_PLAN.md](../../../docs/V03_PLAN.md) Track A; the emitter
     drops 9 of 12 develop ops and uses neutral WB multipliers. The
     harness's job is to quantify the gap, not to hide it.

## `chart_reference.json` format

```json
{
  "_provenance": { ... where the numbers come from, how D55 was derived, the threshold convention ... },
  "illuminant_xy_D55": [0.33243, 0.34744],
  "patches": [
    { "name": "dark skin", "Lab_D50": [...], "Lab_D55": [...] },
    ... 24 entries total, row-major (4 rows × 6 columns) ...
  ]
}
```

Both `Lab_D50` (the X-Rite published value) and `Lab_D55` (the
CAT02-adapted value the harness actually compares against) are
included. Use D50 for cross-checking against any other tool that
reports against the published reference; the harness uses D55.

## Why no chart shot is checked in by default

`lrt-cinema` is Apache-2.0; we will not ship copyrighted RAW samples
for cameras we don't own. The test harness is structured so a real
chart drop-in is the only thing needed to flip the threshold-gate
from `skip` to `pass` (or `fail` — see step 4 caveat).

## Re-generating `chart_reference.json`

If `colour-science` updates its published BabelColor revision, or you
need a different reference illuminant (D50 / D65), regenerate the
JSON. The exact snippet that produced today's file:

```python
import json, numpy as np, colour
from colour.adaptation import chromatic_adaptation_VonKries

ccs = colour.CCS_COLOURCHECKERS['ColorChecker24 - After November 2014']
D50 = colour.CCS_ILLUMINANTS['CIE 1931 2 Degree Standard Observer']['D50']
D55 = colour.CCS_ILLUMINANTS['CIE 1931 2 Degree Standard Observer']['D55']
patches = list(ccs.data.keys())
xyY = np.array([ccs.data[p] for p in patches])
XYZ_d50 = colour.xyY_to_XYZ(xyY)
XYZ_d55 = chromatic_adaptation_VonKries(
    XYZ_d50, colour.xy_to_XYZ(D50), colour.xy_to_XYZ(D55), transform='CAT02'
)
# ... see chart_reference.json _provenance for the rest
```
