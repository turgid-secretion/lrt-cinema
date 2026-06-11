"""Owner-eyeball flip-stack — FULL NATIVE RESOLUTION, no scaling, ever.

Owner rule (2026-06-10): the defect classes here are pixel-sized or small
clusters; downscaled side-by-sides produce false "looks fine" verdicts.
Verification artifacts are therefore full-resolution files with IDENTICAL
pixel geometry, named so they sort adjacently — open the folder, select all,
and arrow-key/flip between conditions in a viewer; pixel-level differences
pop on the flip.

Per frame {1, 125, 250} this emits, aligned on the LRT 4016×6016 grid
(ours/old are 4032×6032; the 8 px border is CROPPED — a crop, not a resize):

    LRT_<n>_A-ours-fixed.png    current pipeline render (8-bit view of the
                                16-bit TIFF; the TIFF itself is also copied)
    LRT_<n>_B-lrt-approved.png  the owner-approved LRT JPG (re-encoded PNG,
                                pixels untouched)
    LRT_<n>_C-old-pipeline.png  the pre-overhaul drive export
    (frame 1 only)
    LRT_00001_D-lr-classic.png  fresh LR Classic export of the same XMP

16-bit originals: ours_LRT_<n>.tif (uncropped 4032×6032) alongside, for
pixel-peeping beyond 8 bits.

Run:  python3 tools/owner_flip_stack.py
Out:  ~/lrt-cinema-fixtures/verify-2026-06-10/flip/
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np

FIX = Path.home() / "lrt-cinema-fixtures"
CUR = Path("/tmp/seq_current")
LRT = Path("/Volumes/SanDisk Extreme Pro 55AF Media/Projects/lrt-export/"
           "LRT_2026_international_faire_timelapse")
OLD = Path("/Volumes/SanDisk Extreme Pro 55AF Media/Projects/"
           "2026 international faire timelapse — lrt-cinema export/tif")
LR_CLASSIC = FIX / "production/lr-export/DSC_4053.tif"
OUT = FIX / "verify-2026-06-10/flip"
FRAMES = (1, 125, 250)
B = 8  # ours(4032×6032) → LRT(4016×6016) alignment border (crop, NOT resize)


def _png_from_tif16(tif: Path, dst: Path, crop_border: int) -> None:
    import tifffile
    from PIL import Image
    a = tifffile.imread(tif)
    a = a[crop_border:-crop_border, crop_border:-crop_border]
    Image.fromarray((a.astype(np.float32) / 65535.0 * 255.0 + 0.5).astype(np.uint8)).save(dst)


def _png_from_jpg(jpg: Path, dst: Path) -> None:
    from PIL import Image
    Image.open(jpg).save(dst)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for n in FRAMES:
        name = f"LRT_{n:05d}"
        cur = CUR / f"{name}.tif"
        _png_from_tif16(cur, OUT / f"{name}_A-ours-fixed.png", B)
        shutil.copy2(cur, OUT / f"ours_{name}.tif")
        _png_from_jpg(LRT / f"{name}.jpg", OUT / f"{name}_B-lrt-approved.png")
        old = OLD / f"{name}.tif"
        if old.exists():
            _png_from_tif16(old, OUT / f"{name}_C-old-pipeline.png", B)
        print(f"{name}: done")
    if LR_CLASSIC.exists():
        _png_from_tif16(LR_CLASSIC, OUT / "LRT_00001_D-lr-classic.png", B)
        print("LRT_00001_D-lr-classic: done")
    print(f"\nflip-stack -> {OUT}")
    print("All images 4016×6016 native pixels (8 px alignment CROP, no resize).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
