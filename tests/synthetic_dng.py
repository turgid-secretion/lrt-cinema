"""Synthetic flat-patch DNG builder — the Axis-3 (vs dng_validate) source.

NOT a test module (no `test_` prefix). Builds a D750 DNG whose Bayer mosaic is
overwritten with flat, known-value patches, so our pipeline and Adobe's
`dng_validate` can be compared on regions that have NO demosaic edges. On such
regions the colour maths (matrix + HSM/LookTable + tone curve) are the only
thing in play and — same DCP both sides, so the Luther floor cancels — they
bit-match the open spec; the real-scene mean ΔE vs dng_validate (gym 0.789) is
dragged by demosaic-edge differences (libraw LINEAR vs Adobe), which flat
patches remove. This harness measures the pure colour-math agreement.

Why a rewrite, not generation from scratch: the Adobe DNG's mosaic is
lossless-JPEG + tiled (can't patch in place), and hand-authoring every DNG tag
so BOTH `dng_validate` and libraw accept the file is fragile. Instead:

  1. `dnglab convert -c uncompressed` produces a valid uncompressed clone (the
     raw plane becomes contiguous uint16 row-strips); every colour tag,
     AsShotNeutral, BlackLevel/WhiteLevel and the IFD layout are dnglab's, so
     dng_validate accepts it by construction;
  2. we overwrite ONLY the raw strip pixel bytes with a synthetic mosaic,
     honouring BlackLevel/WhiteLevel (no LinearizationTable on this body) so the
     value the pipeline linearizes to is exactly the value we intend.

Neutral patches are built proportional to AsShotNeutral, so after white balance
they are in-range and render neutral (equal raw across channels would white-
balance to a clipped magenta — useless for a clean comparison).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

_PHOTOMETRIC_CFA = 32803


@dataclass
class RawLayout:
    """The bits of an uncompressed DNG's raw IFD needed to byte-patch pixels."""

    height: int
    width: int
    strip_offsets: list[int]
    strip_bytecounts: list[int]
    rows_per_strip: int
    byteorder: str        # '<' or '>'
    black: float
    white: float
    cfa_pattern: tuple[int, ...]   # e.g. (0,1,1,2) = RGGB


def ensure_uncompressed_clone(src_dng: Path, dst_dng: Path) -> bool:
    """Produce an uncompressed DNG clone of `src_dng` via dnglab (cached).

    Returns False if dnglab is unavailable or the conversion fails — callers
    skip-gate on that. The clone is a fully valid DNG; we only rewrite its
    pixel bytes afterwards.
    """
    src_dng, dst_dng = Path(src_dng), Path(dst_dng)
    if dst_dng.is_file() and dst_dng.stat().st_mtime >= src_dng.stat().st_mtime:
        return True
    if shutil.which("dnglab") is None:
        return False
    try:
        subprocess.run(
            ["dnglab", "convert", "-c", "uncompressed", "-f", str(src_dng), str(dst_dng)],
            check=True, capture_output=True, timeout=120,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return dst_dng.is_file()


def read_raw_layout(uncompressed_dng: Path) -> RawLayout:
    """Extract the raw-IFD layout (strips + black/white + CFA) from an
    uncompressed DNG. Requires the raw plane be strip-stored + uncompressed.

    BlackLevel/WhiteLevel come from libraw (rawpy), which decodes the
    rational/per-channel forms authoritatively and matches what the render
    pipeline linearizes against — parsing the raw TIFF rational by hand is a
    footgun (tifffile returns the num/den pair un-divided)."""
    import rawpy
    import tifffile

    with rawpy.imread(str(uncompressed_dng)) as r:
        black = float(r.black_level_per_channel[0])
        white = float(r.white_level)

    with tifffile.TiffFile(str(uncompressed_dng)) as t:
        raw = next(
            sp for pg in t.pages
            for sp in [pg, *(getattr(pg, "pages", None) or [])]
            if sp.photometric == _PHOTOMETRIC_CFA
        )
        if raw.compression != 1:
            raise ValueError(f"raw plane is compressed ({raw.compression}); need uncompressed")
        h, w = raw.shape
        rps = raw.tags[278].value
        rps = int(rps[0]) if isinstance(rps, (tuple, list)) else int(rps)
        cfa = tuple(int(b) for b in raw.tags[33422].value)
        return RawLayout(
            height=h, width=w,
            strip_offsets=list(raw.tags[273].value),
            strip_bytecounts=list(raw.tags[279].value),
            rows_per_strip=rps,
            byteorder=t.byteorder,
            black=black, white=white, cfa_pattern=cfa,
        )


@dataclass
class Patch:
    """A flat patch: a fractional rectangle of the frame + a camera-RGB triple
    (linear, post-black-subtract, [0,1+]) the patch should linearize to."""

    name: str
    y0: float
    y1: float
    x0: float
    x1: float
    camera_rgb: tuple[float, float, float]
    is_neutral: bool = False


def build_cfa(layout: RawLayout, patches: list[Patch]) -> np.ndarray:
    """Build the full (H, W) uint16 CFA mosaic from flat patches.

    For each patch the per-channel raw value is black + c·(white−black); the R/G/B
    sites are filled by the GLOBAL Bayer parity (so the mosaic stays a coherent
    RGGB field across patch boundaries — the demosaicer sees flat interiors)."""
    h, w = layout.height, layout.width
    cfa = np.zeros((h, w), dtype=np.uint16)
    # CFA pattern (0,1,1,2) over the 2×2 tile: index = (row%2)*2 + (col%2).
    # 0→R, 1→G, 2→B; map each of the 4 tile positions to a channel index.
    pat = layout.cfa_pattern
    chan_of_pos = [{0: 0, 1: 1, 2: 2}[pat[(r % 2) * 2 + (c % 2)]] for r in (0, 1) for c in (0, 1)]

    def raw_val(c: float) -> int:
        return int(round(layout.black + c * (layout.white - layout.black)))

    for p in patches:
        y0, y1 = int(p.y0 * h), int(p.y1 * h)
        x0, x1 = int(p.x0 * w), int(p.x1 * w)
        raws = [raw_val(c) for c in p.camera_rgb]
        sub = cfa[y0:y1, x0:x1]
        yy, xx = np.mgrid[y0:y1, x0:x1]
        for pos, (dr, dc) in enumerate([(0, 0), (0, 1), (1, 0), (1, 1)]):
            mask = ((yy % 2) == dr) & ((xx % 2) == dc)
            sub[mask] = raws[chan_of_pos[pos]]
        cfa[y0:y1, x0:x1] = sub
    return cfa


def write_synthetic_dng(uncompressed_src: Path, dst: Path, cfa: np.ndarray, layout: RawLayout) -> Path:
    """Copy the uncompressed clone and overwrite ONLY the raw strip pixel bytes
    with `cfa`. Every tag / IFD / colour matrix / AsShotNeutral is preserved, so
    the result is the same valid DNG with synthetic pixels."""
    uncompressed_src, dst = Path(uncompressed_src), Path(dst)
    shutil.copyfile(uncompressed_src, dst)
    dt = np.dtype("<u2" if layout.byteorder == "<" else ">u2")
    with open(dst, "r+b") as f:
        for s, (off, cnt) in enumerate(zip(layout.strip_offsets, layout.strip_bytecounts, strict=True)):
            r0 = s * layout.rows_per_strip
            r1 = min(r0 + layout.rows_per_strip, layout.height)
            data = cfa[r0:r1].astype(dt).tobytes()
            if len(data) != cnt:
                raise ValueError(f"strip {s}: {len(data)} bytes != stored {cnt}")
            f.seek(off)
            f.write(data)
    return dst


# Neutral grey step-wedge levels (linear, in *balanced* space — multiplied by
# AsShotNeutral per channel below) + a few in-range colour patches. Spans
# near-black to upper-midtone after the steep Camera-Standard tone curve.
_WEDGE_LEVELS = (0.45, 0.30, 0.18, 0.10, 0.06, 0.03, 0.015, 0.007)
# Colour patches are kept MILD / desaturated (realistic surface colours, like a
# ColorChecker) and given as camera RGB. Highly-saturated synthetic colours that
# render outside the sRGB gamut diverge in the 8-bit sRGB comparison space
# because the two renderers clip the out-of-gamut excursion slightly differently
# — a comparison-space artefact, NOT a colour-pipeline error (the neutral wedge,
# which stays in gamut at every level, bit-matches dng_validate at ΔE 0.000).
# These mild tints stay in gamut so the off-neutral-axis maths are still tested.
_COLOURS = (
    ("warm", (0.12, 0.18, 0.108)),     # balanced ≈ [0.24, 0.18, 0.14]
    ("cool", (0.08, 0.18, 0.186)),     # balanced ≈ [0.16, 0.18, 0.24]
    ("greenish", (0.08, 0.21, 0.124)),  # balanced ≈ [0.16, 0.21, 0.16]
    ("rosy", (0.12, 0.16, 0.155)),     # balanced ≈ [0.24, 0.16, 0.20]
)


@dataclass
class ChartLayout:
    patches: list[Patch] = field(default_factory=list)
    rows: int = 0
    cols: int = 0


def default_chart(as_shot_neutral: np.ndarray, rows: int = 3, cols: int = 4) -> ChartLayout:
    """A `rows`×`cols` flat-patch chart: a neutral wedge (camera_rgb = L·ASN, so
    it white-balances neutral) plus colour patches. Patches are fractional
    rectangles with a margin gutter so interiors are sampled clear of edges."""
    asn = np.asarray(as_shot_neutral, dtype=np.float64)
    specs: list[tuple[str, tuple[float, float, float], bool]] = [
        (f"grey{lv:.3f}", tuple(lv * asn), True) for lv in _WEDGE_LEVELS
    ]
    specs += [(nm, rgb, False) for nm, rgb in _COLOURS]
    patches: list[Patch] = []
    for i, (name, rgb, neutral) in enumerate(specs[: rows * cols]):
        r, c = divmod(i, cols)
        gy, gx = 0.06 / rows, 0.06 / cols   # gutter so patch edges don't touch
        patches.append(Patch(
            name=name,
            y0=(r + gy) / rows, y1=(r + 1 - gy) / rows,
            x0=(c + gx) / cols, x1=(c + 1 - gx) / cols,
            camera_rgb=(float(rgb[0]), float(rgb[1]), float(rgb[2])),
            is_neutral=neutral,
        ))
    return ChartLayout(patches=patches, rows=rows, cols=cols)


def sample_patch_means(image: np.ndarray, patches: list[Patch], erode: float = 0.30) -> np.ndarray:
    """Mean colour over the central `1-2·erode` box of each patch (fractional
    coords of `image`'s own dims). Heavy erosion makes the tiny DefaultCrop
    offset between our render and dng_validate's irrelevant — interiors are
    flat, so the central box is identical content in both."""
    h, w = image.shape[:2]
    out = np.zeros((len(patches), image.shape[2]), dtype=np.float64)
    for i, p in enumerate(patches):
        cy0, cy1 = p.y0 + (p.y1 - p.y0) * erode, p.y1 - (p.y1 - p.y0) * erode
        cx0, cx1 = p.x0 + (p.x1 - p.x0) * erode, p.x1 - (p.x1 - p.x0) * erode
        box = image[int(cy0 * h): int(cy1 * h), int(cx0 * w): int(cx1 * w)]
        out[i] = box.reshape(-1, image.shape[2]).mean(axis=0)
    return out
