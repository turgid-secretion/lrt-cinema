"""Owner-eyeball flips for the Highlights/Shadows translation — FULL NATIVE
RESOLUTION, no scaling, ever (owner rule 2026-06-10: pixel-scale defects,
downscales produce false "looks fine" verdicts; flip-stacks beat
side-by-side).

Per anchor probe (Highlights −50/−100, Shadows +50/+100) this emits, on
the LR 4016×6016 grid (ours cropped 8 px — a crop, NOT a resize):

    <probe>_A-lr-classic.png   the owner's LR Classic export (ground truth)
    <probe>_B-ours-hlsh.png    our render with the calibrated scene
                               translation applied (final pinned constants)
    BASE_zero-sliders.png      the shared zero-slider base render (context:
                               what both arms started from)

Open the folder, select a probe's A/B pair, arrow-key to flip. A = what
Lightroom did with the slider; B = what our translation does with the
same XMP. The verdict question: does B read as the same *grade decision*
(which tones moved, halo behaviour, shadow colour), not bit-identity —
the measured residual is in CLAIMS (evidence cal_hlsh_fit_2026-07-07).

Run:  python3 tools/hlsh_flips.py   (renders must exist in
      round2/.cal-renders — run tools/cal_hlsh_fit.py first)
Out:  ~/lrt-cinema-fixtures/verify-2026-07-07/hlsh-flip/ (+ README.txt)
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np

FIX = Path.home() / "lrt-cinema-fixtures"
ROUND2 = FIX / "production/calibration/round2"
RENDERS = ROUND2 / ".cal-renders"
OUT = FIX / "verify-2026-07-08/hlsh-flip-v4"
EVIDENCE = Path(__file__).resolve().parent.parent / (
    "tests/fixtures/evidence/cal_hlsh_fit_v2_2026-07-08.json"
)

PROBES = {
    "CALHIM50": "Highlights2012 = -50",
    "CALHIM100": "Highlights2012 = -100",
    "CALSH50": "Shadows2012 = +50",
    "CALSH100": "Shadows2012 = +100",
}

README = """H/S translation v4 (LLF core + amplitude-gated two-scale tone map) — owner flips (2026-07-08)
===============================================================
All images 4016x6016 NATIVE pixels (ours carry an 8 px alignment CROP,
never a resize). Select a probe's A/B pair and flip:

  <probe>_A-lr-classic.png  what Lightroom Classic did with the slider
                            (your export = ground truth)
  <probe>_B-ours-hlsh.png   what our calibrated scene-referred translation
                            does with the SAME NEF+XMP
  BASE_zero-sliders.png     the shared zero-slider base (both arms'
                            starting point)

Probes: CALHIM50/100 = Highlights -50/-100; CALSH50/100 = Shadows +50/+100.

Verdict question: does B make the same GRADE DECISION as A (which tones
moved, halo behaviour at window edges, shadow colour after big lifts)?
Known v4 residual to check: a faint outline can appear along the very
brightest fold ridges in lifted shadows (the two-scale gate crossing
over) — flag it if it reads as an artifact at native res.
Bit-identity is not claimed — Adobe's local-adaptive math is closed; the
measured residual per anchor is in CLAIMS.md (round-2 rows) and
tests/fixtures/evidence/cal_hlsh_fit_2026-07-07.json.
"""


def _png_from_tif16(tif: Path, dst: Path, crop_border: int) -> None:
    import tifffile
    from PIL import Image
    a = tifffile.imread(tif)
    if crop_border:
        a = a[crop_border:-crop_border, crop_border:-crop_border]
    Image.fromarray(
        (a.astype(np.float32) / 65535.0 * 255.0 + 0.5).astype(np.uint8)
    ).save(dst)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    # the final-validation renders carry the pinned-constants hash; find
    # them via the evidence file's final tag list
    ev = json.loads(EVIDENCE.read_text())
    if "final_render_tags" not in ev:
        raise SystemExit(
            "evidence lacks final_render_tags — re-run tools/cal_hlsh_fit.py")
    for probe, tag in ev["final_render_tags"].items():
        _png_from_tif16(ROUND2 / f"{probe}_4053.tif",
                        OUT / f"{probe}_A-lr-classic.png", 0)
        _png_from_tif16(RENDERS / f"{tag}.tif",
                        OUT / f"{probe}_B-ours-hlsh.png", 8)
        print(f"{probe}: A/B written  ({PROBES[probe]})")
    _png_from_tif16(RENDERS / "BASE.tif", OUT / "BASE_zero-sliders.png", 8)
    shutil.copy2(EVIDENCE, OUT / "evidence.json")
    (OUT / "README.txt").write_text(README)
    print(f"\nflip-stack -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
