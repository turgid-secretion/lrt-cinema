# DCP test fixtures (lrt-cinema `.npz` format)

This directory holds extracted DCP profiles in lrt-cinema's `.npz`
format — used by the unit + integration tests so they can run without
requiring Adobe DNG Converter to be installed on the test machine.

## Format

Lossless serialization of the DCP fields the renderer consumes (color
matrices, baseline exposure, profile tone curve, HueSatMap / LookTable
cubes). Project-defined; not Adobe's `.dcp` file format. See
[src/lrt_cinema/dcp.py](../../../src/lrt_cinema/dcp.py)'s
`save_profile` / `load_profile` for the format definition.

## What's in here

| File | Source | Bytes | Purpose |
|---|---|---|---|
| `Nikon D750 Camera Standard.npz` | Adobe DNG Converter bundled `Nikon D750 Camera Standard.dcp` | 192 KB | Lone test camera. Covers the integration test for `auto_detect_profile` (RAW EXIF Make/Model → bundled `.npz` lookup) without requiring Adobe install. |

## Licensing

Per `docs/research/KELVIN_MULTIPLIERS_RESEARCH.md`, Adobe's DCP file
format has a license that doesn't grant redistribution rights. lrt-cinema
ships extracted *data* — color matrices, tone curve, LookTable cube — in
the project's own `.npz` container rather than the original `.dcp`
files. The extracted data is a derived work of measured camera color
science; copyright status of factual scientific data is unclear and
varies by jurisdiction. The project's stance is that shipping one
camera's extracted data as a test fixture for an open-source RAW
pipeline qualifies as fair use / not Adobe's protected file format.

For full pan-camera coverage, users run
`tools/extract_dcp_library.py` against their own Adobe DNG Converter
install (free download from Adobe), which writes the same `.npz`
format under their per-user config dir. The renderer's auto-detect
picks those up at runtime.

## Regenerating

```sh
# One file:
python3 tools/extract_dcp.py \
  "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Nikon D750/Nikon D750 Camera Standard.dcp" \
  tests/fixtures/dcp_data/"Nikon D750 Camera Standard.npz"

# Whole Adobe library → user config dir:
python3 tools/extract_dcp_library.py
```
