"""V3: isolate the CDNG colour delta (gamma-matched), and compare CFA-CDNG vs
Linear-DNG vs our validated pipeline.

The earlier ~9.5 ΔE ballpark conflated DCP-science delta + sRGB-vs-2.4 gamma +
Resolve default tone. Here all three renders are taken to the SAME display
transfer (Rec.709 / γ2.4), so:
  - ΔE(CFA-CDNG, ours) and ΔE(Linear-DNG, ours) isolate DCP-science + debayer,
    not gamma.
  - ΔE(CFA-CDNG, Linear-DNG) is gamma-confound-FREE (both rendered identically
    in Resolve): if ~0, Resolve uses the same bundled DCP for both → Linear DNG
    gives no colour advantage; if Linear-DNG is closer to ours, it honours the
    embedded ColorMatrix → it keeps our colour science.

Resolve: plain YRGB, default CinemaDNG decode (Camera Metadata), no grade.
Renders at 640×426; ours block-mean-downscaled to match.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import tifffile

from tools.resolve_verify import harness as H

GYM_CFA = Path("/tmp/dng_out/DSC_4053.dng")  # CFA mosaic
GYM_LIN = Path("/tmp/lineardng_test/DSC_4053.dng")  # Linear Raw
DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"
)
OUT = Path("/tmp/resolve_verify/cdng_color_v2")
W, Hh = 640, 426


def _ours_rec709_g24() -> np.ndarray:
    """Our pipeline gym → Rec.709 primaries, pure γ2.4, downscaled to (Hh,W)."""
    import colour

    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.pipeline import render_frame

    pp = render_frame(GYM_CFA, parse_dcp(DCP), dcp_path=DCP).prophoto
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
    """Treat input as Rec.709/γ2.4 display; linearize (2.4) → XYZ(Rec.709) → Lab.
    Same conversion applied to every image, so relative ΔE is clean."""
    import colour

    lin = np.clip(disp_g24, 0, 1) ** 2.4
    xyz = colour.RGB_to_XYZ(lin, "sRGB", apply_cctf_decoding=False)
    return colour.XYZ_to_Lab(xyz, illuminant=np.array([0.31270, 0.32900]))


def main() -> int:
    import colour

    for p in (GYM_CFA, GYM_LIN):
        if not p.is_file():
            print(f"missing {p}")
            return 2
    r = H.connect()
    print("connected:", r.GetProductName())
    with H.scratch_project(r) as proj:
        proj.SetSetting("colorScienceMode", "davinciYRGB")
        cfa = H.import_media(r, proj, [GYM_CFA])
        lin = H.import_media(r, proj, [GYM_LIN])
        cfa_img = (
            tifffile.imread(str(H.render_clip(r, proj, cfa[0], OUT, "cfa")[0])).astype(np.float64)
            / 65535.0
        )
        lin_img = (
            tifffile.imread(str(H.render_clip(r, proj, lin[0], OUT, "lin")[0])).astype(np.float64)
            / 65535.0
        )
    ours = _ours_rec709_g24()

    de_cfa = colour.delta_E(_lab(ours), _lab(cfa_img), method="CIE 2000")
    de_lin = colour.delta_E(_lab(ours), _lab(lin_img), method="CIE 2000")
    de_cl = colour.delta_E(_lab(cfa_img), _lab(lin_img), method="CIE 2000")
    print("\n=== gamma-matched colour deltas (Rec.709 γ2.4) ===")
    print(
        f"  CFA-CDNG vs OUR pipeline : mean ΔE {float(de_cfa.mean()):.2f}  P95 {float(np.percentile(de_cfa, 95)):.2f}"
    )
    print(
        f"  Linear-DNG vs OUR pipeline: mean ΔE {float(de_lin.mean()):.2f}  P95 {float(np.percentile(de_lin, 95)):.2f}"
    )
    print(
        f"  CFA-CDNG vs Linear-DNG    : mean ΔE {float(de_cl.mean()):.2f}  (≈0 ⇒ same bundled DCP for both)"
    )
    print(
        "\n(residual gamma uncertainty remains vs the ~9.5 ballpark; the "
        "CFA-vs-Linear delta is gamma-confound-free.)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
