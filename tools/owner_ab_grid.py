"""Owner-eyeball A/B grid — the corrected pipeline vs the approved look.

Builds one PNG: rows = frames {1, 125, 250} of the production sequence,
columns = {OURS (fixed pipeline, /tmp/seq_current) | LRT JPG (the
owner-approved north star) | OLD pipeline TIF (pre-overhaul drive export)}.
Two crops per frame: the cyan-blinds hotspot (from the h1 metrics, frame-1
coords — the artifact region) and a blown-window region (clip-to-white
behaviour change, CLAIMS.md "WB-before-demosaic fix SHIPPED" side-effect 1).

Ground-truth ranking applies: the owner's eyes on this grid outrank every
number in the session (anti-drift rule 3).

Run:  python3 tools/owner_ab_grid.py
Out:  ~/lrt-cinema-fixtures/verify-2026-06-10/owner_ab_grid.png
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

FIX = Path.home() / "lrt-cinema-fixtures"
CUR = Path("/tmp/seq_current")
LRT = Path("/Volumes/SanDisk Extreme Pro 55AF Media/Projects/lrt-export/"
           "LRT_2026_international_faire_timelapse")
OLD = Path("/Volumes/SanDisk Extreme Pro 55AF Media/Projects/"
           "2026 international faire timelapse — lrt-cinema export/tif")
OUT = FIX / "verify-2026-06-10"
FRAMES = (1, 125, 250)
CROP = 512


def _load(path: Path) -> np.ndarray:
    import imageio.v3 as iio
    import tifffile
    if path.suffix == ".tif":
        return (tifffile.imread(path).astype(np.float32) / 65535.0 * 255).astype(np.uint8)
    return iio.imread(path)


def main() -> int:
    from PIL import Image, ImageDraw

    OUT.mkdir(parents=True, exist_ok=True)
    h1_meta = json.loads((FIX / "h1/h1_metrics.json").read_text())
    by, bx = h1_meta["crop_yx"]          # blinds hotspot, OURS grid (4032×6032)

    # Crop windows (ours-grid coords): the blinds hotspot + a fixed second
    # window for general look (center). LRT/OLD grids: LRT is 4016×6016
    # (ours − 8 px border); OLD is ours-grid.
    windows = {"blinds": (by, bx), "center": (1760, 2760)}

    rows = []
    for n in FRAMES:
        cur = _load(CUR / f"LRT_{n:05d}.tif")
        lrt = _load(LRT / f"LRT_{n:05d}.jpg")
        old_p = OLD / f"LRT_{n:05d}.tif"
        old = _load(old_p) if old_p.exists() else None
        for wname, (y, x) in windows.items():
            panels = [("ours-FIXED", cur[y:y + CROP, x:x + CROP])]
            panels.append(("LRT-approved", lrt[y - 8:y - 8 + CROP, x - 8:x - 8 + CROP]))
            if old is not None:
                panels.append(("old-pipeline", old[y:y + CROP, x:x + CROP]))
            rows.append((f"f{n}-{wname}", panels))

    n_cols = max(len(p) for _, p in rows)
    grid = Image.new("RGB", (CROP * n_cols, (CROP + 20) * len(rows)), "black")
    draw = ImageDraw.Draw(grid)
    for r, (rname, panels) in enumerate(rows):
        y0 = r * (CROP + 20)
        for c, (pname, img) in enumerate(panels):
            grid.paste(Image.fromarray(img), (c * CROP, y0 + 20))
            draw.text((c * CROP + 4, y0 + 4), f"{rname}  {pname}", fill="white")
    out = OUT / "owner_ab_grid.png"
    grid.save(out)
    print(f"grid -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
