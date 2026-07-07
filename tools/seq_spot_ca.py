"""Production spot re-pin for the ca-correct DEFAULT decision (owner-
approved 2026-07-07 on the rank-1 flip verdict "CA-on is better in all
cases").

PRE-REGISTERED CRITERIA (2026-07-07, before first run):
  D1: ca0 on frame 1 reproduces the pinned amaze+fc3 0.582-class ΔE
      (calibrates this construction against seq_spot_amaze_2026-07-06 —
      the render goes through the REAL CLI, all production defaults).
  D2: |ca2 − ca0| ΔE ≤ 0.02 per frame. The 6×-block-mean metric is blind
      to pixel-scale fringe fixes, AND the LRT-JPG reference itself
      carries UNcorrected lens CA (production XMPs have CA off), so
      near-equality is expected; the rank-1 owner verdict, not this
      number, is the quality authority.
  D3: wall-time per frame recorded (perf context for the default: numpy
      CA stage, no numba twin yet — task-18 standard applies later).

Frames: only every-10th NEF is local → 1/121/241 (frame 1 = the
directly-comparable calibration point).

Run:  python3 tools/seq_spot_ca.py
Out:  tests/fixtures/evidence/seq_spot_ca_<today>.json
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
FRAMES = {1: "DSC_4053", 121: "DSC_4173", 241: "DSC_4293"}
SCRATCH = Path("/tmp/claude-501") if Path("/tmp/claude-501").exists() else Path("/tmp")
WORK = SCRATCH / "ca_spot"
EVIDENCE = REPO / ("tests/fixtures/evidence/"
                   f"seq_spot_ca_{_dt.date.today().isoformat()}.json")
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
    curd = _block_down(cur[CROP:-CROP, CROP:-CROP], DOWN)
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


def _render_arm(arm: str, ca_n: int, indir: Path) -> tuple[Path, float]:
    from lrt_cinema.cli import main as cli_main

    out = WORK / f"out_{arm}"
    if out.exists():
        shutil.rmtree(out)
    t0 = time.perf_counter()
    cli_main(["render", "--input", str(indir), "--output", str(out),
              "--ca-correct", str(ca_n), "--dcp", DCP,
              "--workers", "1", "--quiet"])
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
                     "D1 ca0 frame-1 ~ pinned 0.582 (amaze+fc3); "
                     "D2 |ca2-ca0| dE <= 0.02/frame; D3 wall recorded",
                     "arms": {}}
    for arm, ca_n in (("ca0", 0), ("ca2", 2)):
        outdir, wall = _render_arm(arm, ca_n, indir)
        rows = {}
        for k, (n, _stem) in enumerate(sorted(FRAMES.items()), start=1):
            rows[str(n)] = _compare(outdir / f"LRT_{k:05d}.tif",
                                    JPG / f"LRT_{n:05d}.jpg")
            print(f"{arm:4s} frame {n:3d}: ΔE {rows[str(n)]['de_lrt']:.4f} "
                  f"(P95 {rows[str(n)]['p95']:.2f}) gain "
                  f"{[f'{g:.3f}' for g in rows[str(n)]['gain']]}")
        results["arms"][arm] = {
            "frames": rows,
            "wall_s_total": round(wall, 2),
            "wall_s_per_frame": round(wall / len(FRAMES), 2)}
        print(f"{arm:4s} wall: {wall:.1f}s total, {wall/len(FRAMES):.1f}s/frame")

    EVIDENCE.write_text(json.dumps(results, indent=1))
    print(f"evidence -> {EVIDENCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
