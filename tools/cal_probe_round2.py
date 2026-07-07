"""Generate the round-2 single-variable calibration probes (owner exports).

PURPOSE (owner directive 2026-07-07): pin, per develop slider, which
DOMAIN Lightroom applies it in (scene-linear pre-curve vs display-referred
post-curve) — the same CAL method that proved Exposure2012/mask-EV are
scene-referred (cal_exposure_domain, k*=3.992) — and give the
Highlights/Shadows translation work its ground truth ("the biggest
missing lever").

Each probe = a copy of DSC_4053.NEF + an XMP that is the CALEXP template
with ALL sliders zeroed except ONE. The owner imports the folder into LR
Classic, batch-exports 16-bit sRGB TIFF (no resize, sharpening off, same
recipe as the LR-product anchors), and drops the TIFFs back into the same
folder. `tools/cal_domain_harness.py` (next session) then fits each
export against our pre-curve vs post-curve arms.

Run:  python3 tools/cal_probe_round2.py
Out:  ~/lrt-cinema-fixtures/production/calibration/round2/ + README
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

CAL = Path.home() / "lrt-cinema-fixtures" / "production" / "calibration"
TEMPLATE = CAL / "CALEXP100_4053.xmp"
NEF_SRC = Path.home() / "lrt-cinema-fixtures" / "production" / "nef" / "DSC_4053.NEF"
OUT = CAL / "round2"

# probe -> {crs field: value}; one variable each (Highlights/Shadows get
# two levels — the priority lever needs a linearity check, not just a sign)
PROBES = {
    "CALHIM50": {"Highlights2012": "-50"},
    "CALHIM100": {"Highlights2012": "-100"},
    "CALSH50": {"Shadows2012": "+50"},
    "CALSH100": {"Shadows2012": "+100"},
    "CALCON50": {"Contrast2012": "+50"},
    "CALCONM50": {"Contrast2012": "-50"},
    "CALBL50": {"Blacks2012": "+50"},
    "CALBLM50": {"Blacks2012": "-50"},
    "CALWH50": {"Whites2012": "+50"},
    "CALWHM50": {"Whites2012": "-50"},
    "CALHSLBLU": {"SaturationAdjustmentBlue": "+50"},
    "CALCGSH": {"ColorGradeShadowHue": "240", "SplitToningShadowHue": "240",
                "SplitToningShadowSaturation": "40"},
}

README = """Round-2 domain-calibration probes — owner export instructions
==============================================================
Each NEF+XMP pair is DSC_4053 with exactly ONE develop slider set
(named in the file). Import this folder into Lightroom Classic
(XMPs auto-apply), then batch-export ALL as:

  16-bit TIFF, sRGB, full resolution (no resize), output sharpening OFF,
  filename = same stem as the NEF (LR default).

Drop the exported .tif files back into THIS folder. The harness fits
each against our pre-curve vs post-curve applicator arms and pins the
domain per slider; Highlights/Shadows exports additionally become the
ground truth for the translation work.

Probe map:
"""


def main() -> int:
    tpl = TEMPLATE.read_text()
    OUT.mkdir(parents=True, exist_ok=True)
    lines = []
    for name, fields in PROBES.items():
        xmp = tpl
        for field, value in fields.items():
            pat = re.compile(rf'crs:{field}="[^"]*"')
            repl = f'crs:{field}="{value}"'
            if pat.search(xmp):
                xmp = pat.sub(repl, xmp)
            else:
                # insert after a known-always-present field
                xmp = xmp.replace('crs:AlreadyApplied="False"',
                                  f'crs:AlreadyApplied="False"\n      {repl}')
        # zero the template's own probe variable
        xmp = re.sub(r'crs:Exposure2012="[^"]*"', 'crs:Exposure2012="0.00"', xmp)
        (OUT / f"{name}_4053.xmp").write_text(xmp)
        shutil.copyfile(NEF_SRC, OUT / f"{name}_4053.NEF")
        desc = ", ".join(f"{k}={v}" for k, v in fields.items())
        lines.append(f"  {name}_4053: {desc}")
        print(f"wrote {name}: {desc}")
    (OUT / "README.txt").write_text(README + "\n".join(lines) + "\n")
    print(f"\n{len(PROBES)} probes -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
