"""CANONICAL AMaZE anchor — darktable-cli with a sidecar-forced demosaic.

THE QUESTION (owner directive 2026-06-12, AMaZE phase 0): before spending
1–2 sessions on a clean-room AMaZE port, measure what CANONICAL AMaZE
actually achieves on our articles. darktable ships AMaZE (GPL stays in
dt's binary, not our repo — rule 6 satisfied); dt-cli is already our
pixel-deterministic engine anchor. Forcing its demosaic method via a
hand-built XMP sidecar isolates the DEMOSAIC variable inside dt's
pipeline:

    dt-AMaZE vs dt-RCD (dt's default)  → the within-engine gain of
                                          AMaZE-class over RCD-class
    dt-RCD   vs our menon              → cross-engine calibration of the
                                          invariant scale

Scoring: truth-anchored INVARIANTS only (falsecolor where the scene is
neutral) — dt's tone/colour intent differs from ours, so absolute ΔE is
meaningless but invented chroma on a neutral scene is engine-independent
(the run_pressure epistemics).

SIDECAR: dt params are little-endian C structs hex-encoded in
darktable:history. dt_iop_demosaic_params_t v6 (introspection, dt 5.5):
  int32 green_eq; float median_thrs; int32 color_smoothing;
  int32 demosaicing_method; int32 lmmse_refine; float dual_thrs;
  float cs_radius; float cs_thrs; float cs_boost; int32 cs_iter;
  float cs_center; int32 cs_enabled
(48 bytes; method: PPG=0, AMAZE=1, VNG4=2, RCD=5, LMMSE=6.) Struct layout
read from the LOCAL dt source (~/src-reading/darktable, read-to-learn).
VALIDATION built in: the method=5 (RCD) sidecar must reproduce dt's
no-sidecar default render pixel-near-exactly, or the sidecar is wrong and
all arms abort.

Run:  python3 tools/dt_amaze_anchor.py
Out:  tests/fixtures/evidence/dt_amaze_anchor_2026-06-12.json
"""

from __future__ import annotations

import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

FIX = Path.home() / "lrt-cinema-fixtures"
ART = FIX / "test-articles"
DT_CLI = "/usr/local/bin/darktable-cli"
ARTICLES = ("diagbars", "zoneplate", "bars", "noisebars", "slantededge")
METHODS = {"rcd": 5, "amaze": 1, "lmmse": 6, "ppg": 0}
EVIDENCE = REPO / "tests/fixtures/evidence/dt_amaze_anchor_2026-06-12.json"

_XMP_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="lrt-cinema dt-anchor">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:darktable="http://darktable.sf.net/"
    darktable:xmp_version="5"
    darktable:history_end="1">
   <darktable:history>
    <rdf:Seq>
     <rdf:li
      darktable:num="0"
      darktable:operation="demosaic"
      darktable:enabled="1"
      darktable:modversion="6"
      darktable:params="{params_hex}"
      darktable:multi_name=""
      darktable:multi_priority="0"/>
    </rdf:Seq>
   </darktable:history>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""


def demosaic_params_hex(method: int) -> str:
    """dt_iop_demosaic_params_t v6 with dt defaults + the given method."""
    blob = struct.pack(
        "<i f i i i f f f f i f i",
        0,            # green_eq         = DT_IOP_GREEN_EQ_NO
        0.0,          # median_thrs
        0,            # color_smoothing  = OFF
        method,       # demosaicing_method
        1,            # lmmse_refine     = DT_LMMSE_REFINE_1
        0.2,          # dual_thrs
        0.0,          # cs_radius
        0.40,         # cs_thrs
        0.0,          # cs_boost
        8,            # cs_iter
        0.0,          # cs_center
        0,            # cs_enabled       = FALSE
    )
    assert len(blob) == 48, len(blob)
    return blob.hex()


def render_dt(dng: Path, out_tif: Path, method: int | None) -> None:
    """darktable-cli render; method None = no sidecar (dt defaults)."""
    args = [DT_CLI, str(dng)]
    xmp_path: Path | None = None
    if method is not None:
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".xmp", delete=False) as f:
            f.write(_XMP_TEMPLATE.format(
                params_hex=demosaic_params_hex(method)))
            xmp_path = Path(f.name)
        args.append(str(xmp_path))
    args += [str(out_tif), "--core", "--disable-opencl"]
    try:
        subprocess.run(args, check=True, capture_output=True, timeout=300)
    finally:
        if xmp_path is not None:
            xmp_path.unlink(missing_ok=True)


def falsecolor(tif: Path) -> dict:
    """Invented-chroma invariant on a neutral-truth article render."""
    import colour
    import tifffile

    raw = tifffile.imread(str(tif))
    img = raw.astype(np.float64)
    if raw.dtype == np.uint8:
        img /= 255.0
    elif raw.dtype == np.uint16:
        img /= 65535.0
    # float TIFF: already display-referred [0, 1]
    lin = colour.models.eotf_sRGB(np.clip(img[..., :3], 0, 1))
    xyz = colour.RGB_to_XYZ(lin, "sRGB", apply_cctf_decoding=False)
    lab = colour.XYZ_to_Lab(xyz, illuminant=np.array([0.3127, 0.3290]))
    chroma = np.hypot(lab[..., 1], lab[..., 2])
    return {"falsecolor_mean": float(chroma.mean()),
            "falsecolor_p99": float(np.percentile(chroma, 99))}


def main() -> int:
    results: dict = {"design": "canonical AMaZE via dt-cli sidecar (phase 0)",
                     "articles": {}}
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        # --- sidecar VALIDATION on one article: method=5 (RCD) must match
        # dt's no-sidecar default render (dt 5.x default = RCD).
        probe = ART / "bars.dng"
        a = tdir / "probe_default.tif"
        b = tdir / "probe_rcd_sidecar.tif"
        render_dt(probe, a, None)
        render_dt(probe, b, METHODS["rcd"])
        import tifffile
        ia = tifffile.imread(str(a)).astype(np.int32)
        ib = tifffile.imread(str(b)).astype(np.int32)
        maxdiff = int(np.abs(ia - ib).max())
        results["sidecar_validation_maxdiff_16bit"] = maxdiff
        print(f"sidecar validation (RCD sidecar vs dt default): "
              f"max |Δ| = {maxdiff} / 65535")
        if maxdiff > 64:   # ~1 8-bit step — anything more means a wrong sidecar
            print("SIDECAR INVALID — aborting before scoring any arm",
                  file=sys.stderr)
            EVIDENCE.write_text(json.dumps(results, indent=1))
            return 1

        for name in ARTICLES:
            dng = ART / f"{name}.dng"
            row: dict = {}
            for mname, mval in METHODS.items():
                out = tdir / f"{name}_{mname}.tif"
                render_dt(dng, out, mval)
                row[f"dt-{mname}"] = falsecolor(out)
                print(f"{name:11s} dt-{mname:6s} "
                      f"falsecolor={row[f'dt-{mname}']['falsecolor_mean']:.3f}")
            results["articles"][name] = row

    EVIDENCE.write_text(json.dumps(results, indent=1))
    print(f"\nevidence → {EVIDENCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
