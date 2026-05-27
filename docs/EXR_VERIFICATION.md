# EXR verification procedure (v0.6)

Manual acceptance test for the `cinema-aces` and `cinema-linear` output
presets. Run once after a v0.6 install, after any change to `output.py`
or `pipeline.py`, and as part of v0.7+ release sign-off. Not in CI —
requires DaVinci Resolve.

The goal is to confirm three things:

1. The render path produces a syntactically valid EXR / float-TIFF that
   Resolve will ingest without warnings.
2. The color interpretation in Resolve matches the colorimetric intent
   declared by `lrt-cinema` (linear Rec.2020 scene-referred).
3. The image matches the `dng_validate` reference within the published
   ΔE bound (see `docs/research/v06-architecture.md` ship gate).

## Prerequisites

- v0.6 install (`pipx install lrt-cinema` or local dev install).
- Adobe DNG Converter installed (`/Applications/Adobe DNG Converter.app`
  on macOS, or set `LRT_CINEMA_DNG_CONVERTER` env to the binary path).
- A test NEF + matching Adobe DCP. The project's reference scene is
  `DSC_4053.NEF` shot on a Nikon D750, paired with
  `Nikon D750 Camera Standard.dcp` from Adobe's Camera Raw profile
  bundle (`/Library/Application Support/Adobe/CameraRaw/CameraProfiles/`
  on macOS).
- DaVinci Resolve 18 or later.
- (Optional) `dng_validate` built from the Adobe DNG SDK for the
  numerical regression — see `docs/research/v06-architecture.md` for
  build references.

## Step 1 — render a single frame to both formats

`--to-frame` is exclusive — `--from-frame 0 --to-frame 1` renders one
frame.

```bash
# EXR output via cinema-aces. Pre-converts NEF → DNG once; cached after.
lrt-cinema render \
  --input /path/to/sequence/ \
  --output /tmp/exr-verify \
  --preset cinema-aces \
  --dcp "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Nikon D750/Nikon D750 Camera Standard.dcp" \
  --from-frame 0 --to-frame 1

# Float-TIFF output via cinema-linear.
lrt-cinema render \
  --input /path/to/sequence/ \
  --output /tmp/exr-verify \
  --preset cinema-linear \
  --dcp "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Nikon D750/Nikon D750 Camera Standard.dcp" \
  --from-frame 0 --to-frame 1
```

Expected: `/tmp/exr-verify/` contains one `.exr` and one `.tif` per
output preset; both decode without errors.

## Step 2 — verify file headers programmatically

```bash
python - <<'PY'
import numpy as np, OpenEXR, tifffile

import pathlib
exr_path = next(pathlib.Path("/tmp/exr-verify").glob("*.exr"))
with OpenEXR.File(exr_path) as f:
    h = f.header()
    print("EXR compression:", h["compression"])  # expect PIZ_COMPRESSION
    print("EXR channels:", sorted(f.channels().keys()))  # expect R, G, B
    rgb = np.stack([f.channels()[c].pixels for c in ("R", "G", "B")], -1)
print("EXR shape/dtype:", rgb.shape, rgb.dtype)  # expect (H, W, 3) float32
print("EXR has overrange:", rgb.max() > 1.0)
print("EXR has negatives:", rgb.min() < 0.0)  # may be True for OOG samples

tif = tifffile.imread(str(next(pathlib.Path("/tmp/exr-verify").glob("*.tif"))))
print("TIFF dtype:", tif.dtype)  # expect float32 (NOT uint16)
print("TIFF shape:", tif.shape)
print("TIFF overrange survived:", float(tif.max()) > 1.0)
PY
```

**Failure signals:**
- EXR `compression` ≠ `PIZ_COMPRESSION` → wrong writer config.
- TIFF dtype is `uint16` → `output.py` regressed back to 16-bit int
  (pre-v0.6 behavior; see PR #19 spec rationale).
- TIFF `overrange survived` is `False` and the source NEF has
  highlights above middle gray → 16-bit int clipping happened.

## Step 3 — DaVinci Resolve project setup

1. New project. **Settings → Color Management:**
   - Color science: **DaVinci YRGB Color Managed**
   - Color processing mode: **HDR DaVinci Wide Gamut Intermediate**
     (or **SDR Rec.709** for a quick visual check; the linearity test
     in step 4 works under either).
   - Input color space: **Bypass** (per-clip override below).
   - Output color space: per your target.
2. Media Pool → import the `.exr` and `.tif` files from
   `/tmp/exr-verify/`.
3. Right-click each clip → **Input Color Space:**
   - For both: **Linear** (gamma) + **Rec.2020** (gamut), i.e.
     **Linear / Rec.2020** if listed, otherwise the closest match
     ("Linear", "Rec.2020 ST.2084" with Linear gamma override).
4. Drop both clips on the timeline.

## Step 4 — visual + numerical checks in Resolve

**A. Linearity check (must pass).** With the EXR clip selected, scrub
the **Lift / Gamma / Gain** wheels in Color page. A `+1.0` stop of Gain
should double the apparent brightness of midtones without raising the
black point. If midtones double AND blacks crush together, the clip is
being interpreted as gamma-encoded — Input Color Space is wrong.

**B. Round-trip check (must pass).** Apply a node with `Color Space
Transform`: from **Linear Rec.2020** → **DaVinci Intermediate** →
**Linear Rec.2020**. The image should be visually identical to the
unprocessed clip. If there's a gamma shift, the input space is
mis-tagged.

**C. ACES IDT check (must pass for `cinema-aces`).** Add a node:
`Color Space Transform` from **Linear Rec.2020** → **ACES2065-1**.
Color picker on a known neutral patch (sky, gray card) should land
on chromaticity within ±0.005 of the D65 white point. A drift > 0.02
suggests a CAT or matrix bug.

**D. Reference comparison (recommended, requires `dng_validate`).**
- Render the same NEF through `dng_validate -16 -stage3 raw.dng out.tif`
  for an Adobe-reference 16-bit stage-3 TIFF.
- Import into Resolve as **Linear / sRGB**.
- Side-by-side with the `cinema-aces` EXR converted to Linear sRGB via
  CST. Use **Difference** blend mode on a layer. Anywhere the difference
  isn't near-black indicates pipeline divergence.
- Architecture spec ship gate: **≤ 1.0 mean ΔE2000 vs `dng_validate`**
  on both reference scenes.

## Step 5 — render a 24-frame burst

```bash
lrt-cinema render \
  --input /path/to/sequence/ \
  --output /tmp/exr-verify-burst \
  --preset cinema-aces \
  --dcp "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Nikon D750/Nikon D750 Camera Standard.dcp" \
  --from-frame 0 --to-frame 24 \
  --workers 4
```

**Failure signals:**
- `dng_convert.py` race condition: any frame fails with
  `RuntimeError: Adobe DNG Converter exited 0 but produced no file`
  during parallel conversion. Known follow-up; tracked separately.
- Frame-to-frame chromaticity drift unrelated to scene change: WB
  override path (`crs:Temperature`) wired incorrectly.

Import the burst to Resolve as an image sequence and scrub. Expect
smooth temporal stability under unchanged WB.

## Known limitations to verify, not regress

Per `docs/research/v06-architecture.md` § "Color science scope":

- `scene_kelvin` is hardcoded at 5500K. Holy Grail kelvin shifts will
  use the per-frame `crs:Temperature` override path only; the implicit
  default does not adapt.
- `stills-finished` preset raises `NotImplementedError` (AgX port is
  v0.6.x).
- Structural residual ~0.8 ΔE vs `dng_validate` on the reference
  scenes is bounded by 16-bit→8-bit quantization of dng_validate's
  output, edge-case HSM trilinear interpolation, and ~+4 a* in
  high-ΔE gym pixels. Closeable in v0.7+; not ship-gating.

## Reporting

If any step fails, capture:
- The render command exact arguments.
- `python -c "from lrt_cinema import __version__; print(__version__)"`.
- The file from step 2's header dump.
- A screenshot from Resolve showing the failing check.
- The input NEF + DCP file paths (or hashes if redacting).

Open an issue at https://github.com/turgid-secretion/lrt-cinema/issues
with the above attached. Tag with `output:exr` or `output:tiff` and
the failing step number.
