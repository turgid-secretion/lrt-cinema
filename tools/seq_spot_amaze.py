"""Production spot re-pin for the amaze DEFAULT decision (owner-authorized).

PRE-REGISTERED CRITERIA (2026-07-06, before first run):
  A1: menon+fc3 on frame 1 reproduces the pinned 0.586-class ΔE (calibrates
      this construction against seq_spot_menon_fc3_2026-06-12 — the render
      here goes through the REAL CLI, all production defaults).
  A2: amaze+fc3 ΔE within ±0.02 of menon+fc3 per frame (block-mean ΔE is
      blind to pixel-scale demosaic differences — menon vs linear measured
      identical; a LARGER delta would mean amaze changes global colour,
      which the bit-exact port evidence says it must not).
  A3: amaze render wall-time per frame ≤ menon's (perf must not regress
      the production render).
  A4: native-res flip artifacts (menon vs amaze, full production intent)
      written for owner verification.

Frames: the SanDisk master holds the pinned 1/125/250; only every-10th NEF
is local, so this uses 1/121/241 (start/middle/end coverage; frame 1 is the
directly-comparable calibration point).

Run:  python3 tools/seq_spot_amaze.py
Out:  tests/fixtures/evidence/seq_spot_amaze_<today>.json
      ~/lrt-cinema-fixtures/verify-2026-07-06/amaze-flip/   (owner arms)
"""

from __future__ import annotations

import datetime as _dt
import json
import shutil
import sys
import time
import warnings
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

FIX = Path.home() / "lrt-cinema-fixtures"
NEF = FIX / "production" / "nef"
XMP = FIX / "production" / "xmp"
JPG = FIX / "production" / "lrt-jpg"
DCP = ("/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
       "Camera/Nikon D750/Nikon D750 Camera Standard.dcp")
# local NEF n -> (source stem, LRT output index)
FRAMES = {1: "DSC_4053", 121: "DSC_4173", 241: "DSC_4293"}
SCRATCH = Path("/tmp/claude-501") if Path("/tmp/claude-501").exists() else Path("/tmp")
WORK = SCRATCH / "amaze_spot"
FLIPDIR = FIX / "verify-2026-07-06" / "amaze-flip"
EVIDENCE = REPO / ("tests/fixtures/evidence/"
                   f"seq_spot_amaze_{_dt.date.today().isoformat()}.json")
DOWN, CROP = 6, 8          # seq_lrt_compare's validated comparison geometry


def _block_down(a, k):
    h, w = a.shape[:2]
    h2, w2 = (h // k) * k, (w // k) * k
    return a[:h2, :w2].reshape(h2 // k, k, w2 // k, k, -1).mean(axis=(1, 3))


def _compare(cur_tif: Path, lrt_jpg: Path) -> dict:
    import colour
    import imageio.v3 as iio
    import tifffile

    d65 = np.array([0.3127, 0.3290])
    cur = tifffile.imread(cur_tif).astype(np.float32) / 65535.0
    lrt = iio.imread(lrt_jpg).astype(np.float32) / 255.0
    cur_c = cur[CROP:-CROP, CROP:-CROP]
    curd = _block_down(cur_c, DOWN)
    lrtd = _block_down(lrt, DOWN)

    def lab(a):
        lin = colour.models.eotf_sRGB(a)
        return colour.XYZ_to_Lab(
            colour.RGB_to_XYZ(lin, "sRGB", apply_cctf_decoding=False),
            illuminant=d65)

    de = colour.delta_E(lab(curd), lab(lrtd), method="CIE 2000")
    ours_lin = colour.models.eotf_sRGB(curd)
    tgt_lin = colour.models.eotf_sRGB(lrtd)
    gain = [float((ours_lin[..., c].ravel() @ tgt_lin[..., c].ravel())
                  / (ours_lin[..., c].ravel() @ ours_lin[..., c].ravel()))
            for c in range(3)]
    return {"de_lrt": round(float(de.mean()), 4),
            "p95": round(float(np.percentile(de, 95)), 3),
            "gain": [round(g, 4) for g in gain]}


def _render_arm(arm: str, indir: Path) -> tuple[Path, float]:
    from lrt_cinema.cli import main as cli_main

    out = WORK / f"out_{arm}"
    if out.exists():
        shutil.rmtree(out)
    t0 = time.perf_counter()
    cli_main(["render", "--input", str(indir), "--output", str(out),
              "--demosaic", arm, "--dcp", DCP, "--workers", "1", "--quiet"])
    return out, time.perf_counter() - t0


def main() -> int:
    indir = WORK / "in"
    if indir.exists():
        shutil.rmtree(indir)
    indir.mkdir(parents=True)
    for stem in FRAMES.values():
        (indir / f"{stem}.NEF").symlink_to(NEF / f"{stem}.NEF")
        (indir / f"{stem}.xmp").symlink_to(XMP / f"{stem}.xmp")

    results: dict = {"frames": FRAMES, "criteria":
                     "A1 menon frame-1 ~ pinned 0.586; A2 |amaze-menon| ΔE "
                     "<= 0.02; A3 amaze wall <= menon; A4 flips written",
                     "arms": {}}
    for arm in ("menon", "amaze"):
        outdir, wall = _render_arm(arm, indir)
        rows = {}
        for k, (n, _stem) in enumerate(sorted(FRAMES.items()), start=1):
            rows[str(n)] = _compare(outdir / f"LRT_{k:05d}.tif",
                                    JPG / f"LRT_{n:05d}.jpg")
            print(f"{arm:6s} frame {n:3d}: ΔE {rows[str(n)]['de_lrt']:.4f} "
                  f"(P95 {rows[str(n)]['p95']:.2f}) gain "
                  f"{[f'{g:.3f}' for g in rows[str(n)]['gain']]}")
        results["arms"][arm] = {
            "frames": rows,
            "wall_s_total": round(wall, 2),
            "wall_s_per_frame": round(wall / len(FRAMES), 2)}
        print(f"{arm:6s} wall: {wall:.1f}s total, {wall/len(FRAMES):.1f}s/frame")

    # ---- owner flips: native res, zero scaling ------------------------------
    import tifffile
    from PIL import Image

    FLIPDIR.mkdir(parents=True, exist_ok=True)
    for k, (n, _stem) in enumerate(sorted(FRAMES.items()), start=1):
        if n not in (1, 241):
            continue
        for arm, tag in (("menon", "A-menon-current-default"),
                         ("amaze", "B-amaze-candidate")):
            t = tifffile.imread(WORK / f"out_{arm}" / f"LRT_{k:05d}.tif")
            Image.fromarray((t // 257).astype(np.uint8)).save(
                FLIPDIR / f"frame{n:03d}_{tag}.png")
    (FLIPDIR / "README.txt").write_text(
        "amaze DEFAULT decision — owner flip (menon vs amaze, fc-suppress 3)\n"
        "====================================================================\n"
        "Full production intent through the real CLI, native res, zero\n"
        "scaling. Frames 1 and 241 of the faire sequence.\n\n"
        "  frameNNN_A-menon-current-default.png\n"
        "  frameNNN_B-amaze-candidate.png       (the numba twin: bit-exact\n"
        "      port evidence amaze_numba_2026-07-06; diagbars falsecolor\n"
        "      34->15.6, clipbars 1.12->0.006 vs menon)\n\n"
        "JUDGE at 1:1: window edges / diagonal railings / fine texture —\n"
        "B should show fewer colour fringes and cleaner diagonals; global\n"
        "colour must be indistinguishable.\n")

    EVIDENCE.write_text(json.dumps(results, indent=1))
    print(f"evidence -> {EVIDENCE}\nflips -> {FLIPDIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
