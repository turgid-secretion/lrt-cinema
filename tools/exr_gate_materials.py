"""EXR/Resolve capability gate — owner judging materials (Phase 1f).

THE GATE (CLAIMS "EXR tap-7 master is demonstrably more gradable than the
16-bit TIFF — UNVERIFIED; owner judges"): the ACEScg EXR path survives
only if the owner, grading in DaVinci Resolve, finds real headroom /
gradability the display TIFF cannot give. This tool renders the judging
set; the README tells the owner exactly what to do in Resolve.

Arms (three frames: start / mid / end of the faire sequence):
  tiff/    the production 16-bit sRGB TIFF (lrtimelapse target)
  master/  --target master  = ACEScg EXR tapped at Stage 7 (scene-linear,
           highlight headroom preserved — the CFA decode keeps per-channel
           headroom above the common white)

A segbased-reconstruction EXR arm (recovered structure INSIDE the blown
areas, not just preserved partial headroom) rides with the fc3xHR matrix
wiring (task 16) — judge the base gate first.

Run:  python3 tools/exr_gate_materials.py
Out:  ~/lrt-cinema-fixtures/verify-2026-07-06/exr-gate/{tiff,master}/ + README
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

FIX = Path.home() / "lrt-cinema-fixtures"
NEF = FIX / "production" / "nef"
XMP = FIX / "production" / "xmp"
DCP = ("/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
       "Camera/Nikon D750/Nikon D750 Camera Standard.dcp")
FRAMES = ("DSC_4053", "DSC_4173", "DSC_4293")
OUTDIR = FIX / "verify-2026-07-06" / "exr-gate"
SCRATCH = Path("/tmp/exr_gate_in")

README = """EXR/Resolve capability gate — owner judging session (Phase 1f)
================================================================
Three frames (start/mid/end of the faire sequence), two arms each:

  tiff/    LRT_0000N.tif  — the production 16-bit sRGB TIFF
  master/  LRT_0000N.exr  — ACEScg EXR, scene-linear, tapped at Stage 7
                            (highlight headroom preserved)

WHAT TO DO IN RESOLVE
1. Project settings -> Color Management: set 'ACES' color science
   (ACEScct working, output sRGB/Rec.709) OR DaVinci YRGB with input
   color space assigned per-clip: EXR = ACEScg (AP1, linear);
   TIFF = sRGB.
2. Import both arms of the same frame side by side on the timeline.
3. JUDGE 1 (headroom): pull exposure down ~1-2 stops on both. The gym
   windows: does the EXR reveal recovered rolloff/structure where the
   TIFF plateaus at flat grey? That headroom is the EXR's whole case.
4. JUDGE 2 (grade robustness): apply a strong grade you'd actually use
   (contrast + saturation + a warm/cool balance). Does the TIFF band
   or posterize in highlight gradients where the EXR stays smooth?
5. JUDGE 3 (colour fidelity): at a neutral grade, do both arms agree
   with your LRT-approved look? (They should — same pipeline until the
   tap; if the EXR looks WRONG at neutral, the gate FAILS regardless
   of headroom.)

VERDICT TO RECORD (CLAIMS wants your words):
  - EXR gradability vs TIFF: better / same / worse, and where
  - Is the difference worth a second deliverable pipeline? (the EXR
    path only survives this gate)
Note: a segbased-reconstruction EXR arm (structure rebuilt INSIDE fully
blown areas) follows with the fc3xHR matrix — this session judges the
preserved-headroom baseline.
"""


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from lrt_cinema.cli import main as cli_main

    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True)
    for stem in FRAMES:
        (SCRATCH / f"{stem}.NEF").symlink_to(NEF / f"{stem}.NEF")
        (SCRATCH / f"{stem}.xmp").symlink_to(XMP / f"{stem}.xmp")

    for target, sub in (("lrtimelapse", "tiff"), ("master", "master")):
        out = OUTDIR / sub
        if out.exists():
            shutil.rmtree(out)
        cli_main(["render", "--input", str(SCRATCH), "--output", str(out),
                  "--target", target, "--dcp", DCP, "--workers", "1",
                  "--quiet"])
        print(f"{sub}: {sorted(p.name for p in out.iterdir())}")
    (OUTDIR / "README.txt").write_text(README)
    print(f"gate materials -> {OUTDIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
