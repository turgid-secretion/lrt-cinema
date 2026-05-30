"""V1: does Resolve honor PER-FRAME metadata on a LINEAR DNG (no re-mosaic)?

A Linear DNG (PhotometricInterpretation = Linear Raw, SamplesPerPixel 3) carries
DEMOSAICED pixels + DNG colour metadata + per-frame AsShotNeutral/BaselineExposure
— so a software renderer can emit it from already-demosaiced output with NO
re-mosaic. The open question (survey §2.4): does Resolve treat it as Camera-Raw
and honor per-frame WB/exposure the way it does on CFA CinemaDNG?

SRC = Adobe-Converter-linearized gym DNG (asset-gen only; the converter is
incidental — we are testing Resolve's behaviour on the Linear DNG).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import tifffile

from tools.resolve_verify import harness as H

SRC = Path("/tmp/lineardng_test/DSC_4053.dng")  # Linear Raw DNG
ASSETS = Path("/tmp/resolve_verify/lineardng_assets")
RENDERS = Path("/tmp/resolve_verify/lineardng_renders")


def _exif(args, f):
    subprocess.run(
        ["exiftool", "-overwrite_original", *args, str(f)],
        check=True,
        capture_output=True,
        text=True,
    )


def _build():
    if ASSETS.exists():
        shutil.rmtree(ASSETS)
    wb = ASSETS / "wb"
    wb.mkdir(parents=True)
    exp = ASSETS / "exp"
    exp.mkdir(parents=True)
    f1, f2 = wb / "LWB_0001.dng", wb / "LWB_0002.dng"
    shutil.copy2(SRC, f1)
    shutil.copy2(SRC, f2)
    _exif(["-AsShotNeutral=0.5 1 0.775758"], f1)
    _exif(["-AsShotNeutral=0.85 1 0.45"], f2)
    e1, e2 = exp / "LEX_0001.dng", exp / "LEX_0002.dng"
    shutil.copy2(SRC, e1)
    shutil.copy2(SRC, e2)
    _exif(["-BaselineExposure=0.0"], e1)
    _exif(["-BaselineExposure=2.0"], e2)
    return wb, exp


def _frames(files):
    return [tifffile.imread(str(f)).astype(np.float64) / 65535.0 for f in files]


def main() -> int:
    if not SRC.is_file():
        print(f"missing linear DNG: {SRC}")
        return 2
    wb, exp = _build()
    print("built Linear-DNG WB + exposure pairs")
    r = H.connect()
    print("connected:", r.GetProductName(), r.GetVersionString())
    res = {}
    with H.scratch_project(r) as proj:
        proj.SetSetting("colorScienceMode", "davinciYRGB")
        wbi = H.import_media(r, proj, [wb])
        exi = H.import_media(r, proj, [exp])
        print(f"imported: wb clip(s)={len(wbi)} exp clip(s)={len(exi)}")
        if not wbi or not exi:
            print("FAIL: Resolve did not import the Linear DNG sequence")
            return 1

        wf = _frames(H.render_clip(r, proj, wbi[0], RENDERS, "lwb"))
        if len(wf) >= 2:
            br0 = wf[0][..., 2].mean() / max(wf[0][..., 0].mean(), 1e-6)
            br1 = wf[1][..., 2].mean() / max(wf[1][..., 0].mean(), 1e-6)
            d = float(np.abs(wf[1] - wf[0]).mean())
            print(f"[WB]  frame0 B/R={br0:.3f}  frame1 B/R={br1:.3f}  |f1-f0|={d:.4f}")
            res["wb_per_frame"] = d > 0.02 and abs(br1 - br0) / max(br0, 1e-6) > 0.15

        ef = _frames(H.render_clip(r, proj, exi[0], RENDERS, "lex"))
        if len(ef) >= 2:
            m0, m1 = float(np.median(ef[0])), float(np.median(ef[1]))
            print(f"[EXP] frame0 med={m0:.4f}  frame1 med={m1:.4f}  ratio={m1 / max(m0, 1e-6):.2f}")
            res["exp_per_frame"] = m1 > m0 * 1.5

    print("\n=== Linear DNG per-frame metadata ===")
    for k, v in res.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    allp = all(res.values()) and len(res) == 2
    print(f"\nLinear DNG per-frame WB+exposure: {'HONORED' if allp else 'NOT honored / partial'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
