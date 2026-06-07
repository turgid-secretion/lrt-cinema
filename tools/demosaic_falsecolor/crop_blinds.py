"""Save 1:1 NATIVE crops of the venetian-blind ROI for each demosaic option + the
ACR-NR-off reference, so the owner can eyeball the false colour at full resolution.

NO DOWNSAMPLING — a prior crop set was ruined by it. Each crop is the exact ROI
pixels straight from the production-encoded sRGB TIFF (encode rule #1: we only READ
the real output.py TIFFs, never re-encode). The ACR reference is shifted by the
measured -8,-8 alignment so its crop covers the same scene region.

Run: PYTHONPATH=<worktree>/src python3 tools/demosaic_falsecolor/crop_blinds.py <out_dir>
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image

# ROI in OUR render coords (4032x6032); the ACR ref aligns at off=-8 (verified).
Y0, Y1, X0, X1 = 1350, 1660, 150, 1400
ACR_OFF = -8

# label -> (tiff path, roi offset). Our renders: off 0. ACR ref: off -8.
SOURCES = {
    "rcd": ("/tmp/fc_renders/rcd_numpy/LRT_00001.tif", 0),
    "mlri": ("/tmp/fc_renders/mlri/LRT_00001.tif", 0),
    "dcb": ("/tmp/fc_renders/dcb/LRT_00001.tif", 0),
    "menon": ("/tmp/fc_renders/menon/LRT_00001.tif", 0),
    "rcd+med3i1": ("/tmp/fc_renders/med3i1/LRT_00001.tif", 0),
    "rcd+med5i1": ("/tmp/fc_renders/med5i1/LRT_00001.tif", 0),
    "rcd+med5i2": ("/tmp/fc_renders/med5i2/LRT_00001.tif", 0),
    "ACR-NR-off": (
        "/Volumes/SanDisk Extreme Pro 55AF Media/Projects/lrt-export/NR-off/DSC_4053.tif",
        ACR_OFF,
    ),
}


def _crop(path: str, off: int) -> np.ndarray:
    a = tifffile.imread(path)
    sub = a[Y0 + off:Y1 + off, X0 + off:X1 + off]
    # 16-bit -> 8-bit for an eyeball PNG (>>8, no resample, no tone change).
    if sub.dtype == np.uint16:
        sub = (sub >> 8).astype(np.uint8)
    return sub


def main(out_dir: str) -> int:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for label, (path, off) in SOURCES.items():
        if not Path(path).exists():
            print(f"  SKIP {label}: missing {path}")
            continue
        crop = _crop(path, off)
        fp = out / f"blinds_{label.replace('+', '_')}.png"
        Image.fromarray(crop).save(fp)
        print(f"  {label:12s} -> {fp}  {crop.shape}")
    # A zoomed 4x sub-tile (nearest, NO smoothing) of the worst corner, to make the
    # 1-2px false-colour stripes legible without any resampling artefact.
    for label in ("rcd", "rcd+med5i1", "mlri", "ACR-NR-off"):
        path, off = SOURCES[label]
        if not Path(path).exists():
            continue
        crop = _crop(path, off)
        tile = crop[40:140, 0:200]  # a slat-dense corner
        big = np.kron(tile, np.ones((4, 4, 1), dtype=np.uint8))  # 4x nearest zoom
        fp = out / f"zoom4x_{label.replace('+', '_')}.png"
        Image.fromarray(big).save(fp)
        print(f"  zoom4x {label:12s} -> {fp}  {big.shape}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/fc_crops"))
