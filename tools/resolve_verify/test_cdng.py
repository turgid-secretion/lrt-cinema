"""Resolve test: does Resolve honor PER-FRAME CinemaDNG decode metadata?

This re-runs, for real and headless, the v0.7 'α' spike whose "T1/T2 pass"
was an unrun manual checkpoint. CDNG is the only candidate substrate that
gives full-sensor (re-debayerable) recovery — the user's top priority — so
its per-frame metadata claim must be verified before "back to CDNG" can be
on the table.

Builds two 2-frame CinemaDNG sequences from the real gym DNG (exiftool):
  cdng_wb/  WB_0001 (daylight ASN) , WB_0002 (tungsten ASN)  — differ in WB
  cdng_exp/ EXP_0001 (BE 0.0)      , EXP_0002 (BE +2.0)       — differ in exposure
Ingests each as ONE CinemaDNG clip, renders both frames, and checks whether
frame 2 decodes with ITS OWN metadata (per-frame honored) or with frame 1's
(clip-level only).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import tifffile

from tools.resolve_verify import harness as H

SRC = Path("/tmp/dng_out/DSC_4053.dng")
ASSETS = Path("/tmp/resolve_verify/cdng_assets")
RENDERS = Path("/tmp/resolve_verify/cdng_renders")


def _exif(args: list[str], f: Path):
    subprocess.run(
        ["exiftool", "-overwrite_original", *args, str(f)],
        check=True,
        capture_output=True,
        text=True,
    )


def _build_pairs():
    if ASSETS.exists():
        shutil.rmtree(ASSETS)
    wb = ASSETS / "cdng_wb"
    wb.mkdir(parents=True)
    exp = ASSETS / "cdng_exp"
    exp.mkdir(parents=True)
    # WB pair
    f1, f2 = wb / "WB_0001.dng", wb / "WB_0002.dng"
    shutil.copy2(SRC, f1)
    shutil.copy2(SRC, f2)
    _exif(["-AsShotNeutral=0.5 1 0.775758"], f1)  # daylight (source)
    _exif(["-AsShotNeutral=0.85 1 0.45"], f2)  # tungsten — strong shift
    # Exposure pair
    e1, e2 = exp / "EXP_0001.dng", exp / "EXP_0002.dng"
    shutil.copy2(SRC, e1)
    shutil.copy2(SRC, e2)
    _exif(["-BaselineExposure=0.0"], e1)
    _exif(["-BaselineExposure=2.0"], e2)  # +2 stops
    return wb, exp


def _frames(files: list[Path]) -> list[np.ndarray]:
    return [tifffile.imread(str(f)).astype(np.float64) / 65535.0 for f in files]


def main() -> int:
    if not SRC.is_file():
        print(f"missing {SRC}")
        return 2
    wb_dir, exp_dir = _build_pairs()
    print("built CDNG pairs (WB, exposure)")

    resolve = H.connect()
    print("connected:", resolve.GetProductName(), resolve.GetVersionString())
    results = {}
    with H.scratch_project(resolve) as proj:
        proj.SetSetting("colorScienceMode", "davinciYRGB")
        # CinemaDNG decode uses per-file camera metadata (Resolve default).
        wb_items = H.import_media(resolve, proj, [wb_dir])
        exp_items = H.import_media(resolve, proj, [exp_dir])
        print(f"imported WB clip(s)={len(wb_items)} EXP clip(s)={len(exp_items)}")

        # ---- WB per-frame test ----
        wb_files = H.render_clip(resolve, proj, wb_items[0], RENDERS, "wb")
        print(f"WB rendered {len(wb_files)} frames")
        wf = _frames(wb_files)
        if len(wf) >= 2:
            f0, f1 = wf[0], wf[1]
            # blue/red ratio per frame — tungsten ASN should make frame 2 far cooler
            br0 = float(f0[..., 2].mean() / max(f0[..., 0].mean(), 1e-6))
            br1 = float(f1[..., 2].mean() / max(f1[..., 0].mean(), 1e-6))
            overall = float(np.abs(f1 - f0).mean())
            print(f"[WB] frame0 B/R={br0:.3f}  frame1 B/R={br1:.3f}  |f1-f0|mean={overall:.4f}")
            wb_perframe = (overall > 0.02) and (abs(br1 - br0) / max(br0, 1e-6) > 0.15)
            print(f"[WB] per-frame WB honored: {'YES' if wb_perframe else 'NO'}")
            results["wb_per_frame"] = wb_perframe

        # ---- Exposure per-frame test ----
        exp_files = H.render_clip(resolve, proj, exp_items[0], RENDERS, "exp")
        print(f"EXP rendered {len(exp_files)} frames")
        ef = _frames(exp_files)
        if len(ef) >= 2:
            f0, f1 = ef[0], ef[1]
            m0, m1 = float(f0.mean()), float(f1.mean())
            # use median to resist clipping
            med0, med1 = float(np.median(f0)), float(np.median(f1))
            print(
                f"[EXP] frame0 mean={m0:.4f} med={med0:.4f}  "
                f"frame1 mean={m1:.4f} med={med1:.4f}  ratio(med)={med1 / max(med0, 1e-6):.2f}"
            )
            exp_perframe = med1 > med0 * 1.5  # +2 stops -> markedly brighter
            print(f"[EXP] per-frame exposure honored: {'YES' if exp_perframe else 'NO'}")
            results["exp_per_frame"] = exp_perframe

    print("\n=== CDNG per-frame metadata summary ===")
    for k, v in results.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    allp = all(results.values()) and len(results) == 2
    print(f"\nCDNG per-frame WB+exposure: {'VERIFIED' if allp else 'NOT honored / partial'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
