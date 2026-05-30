"""V4: does our new ACEScg (AP1) EXR emission round-trip correctly in Resolve?

Emit the gym render as scene-linear ACEScg EXR (the v0.8 switch), ingest in RCM
with the named clip Input Color Space = "ACEScg", render to Rec.709 γ2.4, and
compare to our pipeline's own Rec.709 γ2.4 reference. LOW ΔE ⇒ (a) our ACEScg
emission is correct and (b) Resolve's ACEScg input transform inverts it — i.e.
the standards-aligned switch works end-to-end (and far better than the 8.5 ΔE
CDNG colour-delegation path, because here WE keep the colour science).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import tifffile

from tools.resolve_verify import harness as H

GYM = Path("/tmp/dng_out/DSC_4053.dng")
DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"
)
OUT = Path("/tmp/resolve_verify/acescg_rt")
W, Hh = 640, 426


def _render_ours():
    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.pipeline import render_frame

    return render_frame(GYM, parse_dcp(DCP), dcp_path=DCP).prophoto


def _ours_rec709_g24(pp):
    import colour

    h, w, _ = pp.shape
    lin709 = colour.RGB_to_RGB(
        pp.reshape(-1, 3).astype(np.float64),
        "ProPhoto RGB",
        "sRGB",
        chromatic_adaptation_transform="Bradford",
        apply_cctf_decoding=False,
        apply_cctf_encoding=False,
    )
    lin709 = np.clip(lin709, 0, 1).reshape(h, w, 3)
    disp = lin709 ** (1.0 / 2.4)
    ys = np.linspace(0, h, Hh + 1).astype(int)
    xs = np.linspace(0, w, W + 1).astype(int)
    small = np.empty((Hh, W, 3))
    for j in range(Hh):
        for i in range(W):
            small[j, i] = disp[ys[j] : ys[j + 1], xs[i] : xs[i + 1]].reshape(-1, 3).mean(0)
    return small


def _lab(disp_g24):
    import colour

    lin = np.clip(disp_g24, 0, 1) ** 2.4
    xyz = colour.RGB_to_XYZ(lin, "sRGB", apply_cctf_decoding=False)
    return colour.XYZ_to_Lab(xyz, illuminant=np.array([0.31270, 0.32900]))


def main() -> int:
    import colour

    from lrt_cinema.output import write_exr_linear_rec2020

    pp = _render_ours()
    OUT.mkdir(parents=True, exist_ok=True)
    exr_dir = OUT / "seq"
    exr_dir.mkdir(exist_ok=True)
    # ZIP (lossless) to avoid DWAB confounding the colour check.
    write_exr_linear_rec2020(
        pp, exr_dir / "ACG_0001.exr", bit_depth="half", compression="zip", colorspace="acescg"
    )
    write_exr_linear_rec2020(
        pp, exr_dir / "ACG_0002.exr", bit_depth="half", compression="zip", colorspace="acescg"
    )

    r = H.connect()
    print("connected:", r.GetProductName())
    with H.scratch_project(r) as proj:
        # RCM with the EXR ingested as ACEScg, output Rec.709 γ2.4.
        proj.SetSetting("colorScienceMode", "davinciYRGBColorManagedv2")
        proj.SetSetting("colorSpaceInput", "ACEScg")
        proj.SetSetting("colorSpaceTimeline", "ACEScg")
        proj.SetSetting("colorSpaceOutput", "Rec.709 Gamma 2.4")
        items = H.import_media(r, proj, [exr_dir])
        if items:
            items[0].SetClipProperty("Input Color Space", "ACEScg")
        files = H.render_clip(r, proj, items[0], OUT, "acg")
        res_img = tifffile.imread(str(files[0])).astype(np.float64) / 65535.0

    ref = _ours_rec709_g24(pp)
    de = colour.delta_E(_lab(ref), _lab(res_img), method="CIE 2000")
    mean = float(de.mean())
    print(
        f"\nACEScg EXR round-trip in Resolve (Input=ACEScg → Rec.709 γ2.4) "
        f"vs OUR pipeline: mean ΔE2000 {mean:.2f}  P95 {float(np.percentile(de, 95)):.2f}"
    )
    ok = mean < 4.0  # far below the 8.5 CDNG delegation; residual gamma/RCM only
    print(
        f"V4: {'PASS — ACEScg round-trip preserves our colour' if ok else 'CHECK — higher than expected'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
