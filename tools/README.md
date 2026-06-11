# lrt-cinema diagnostic tools

Out-of-band scripts for inspection / validation / debugging that aren't
part of the shipped CLI surface. Run from project root with
`python3 tools/<name>.py …`.

## `diagnose_vs_lrt_preview.py`

The project's primary colorimetric-divergence diagnostic. Compares a
TIFF rendered by `lrt-cinema` against an LRT-generated preview JPEG.
Produces four reports per the methodology in
[`docs/archive/VALIDATION.md`](../docs/archive/VALIDATION.md):

1. ΔE2000 per-pixel distribution (percentiles + bucket histogram)
2. Spatial ΔE heatmap (JPG — locates where divergence concentrates)
3. Affine-fit decomposition (per-channel gain+offset that minimizes residual ΔE — distinguishes "grading transform" gaps from structural gaps)
4. Per-channel L\*a\*b\* percentile distribution (shadows vs highlights, channel bias)

Usage:

```sh
python3 tools/diagnose_vs_lrt_preview.py <our.tif> <lrt_preview.jpg> [output_dir]
```

Why these four and **not** mean L\*a\*b\* — see `docs/archive/VALIDATION.md`
"Methodology — comparing two renders of the same scene." Mean is a
misleading scalar; the four above are the recognized stack for
two-render comparison on arbitrary scene content.

Dependencies (dev install): `pip install --user numpy tifffile Pillow colour-science scipy`.
