"""Ballpark: how far does Resolve's CDNG decode (bundled DCP) sit from our
Adobe-dng_validate-validated pipeline?

CDNG color is delegated to Resolve's bundled per-camera profile, NOT the
embedded one — so the gym, validated to 0.79 ΔE vs Adobe dng_validate in our
pipeline, may decode differently in Resolve. This quantifies that delegation
gap (ballpark: cross-pipeline color-management differences inflate it, so read
it as an upper bound, not a precise figure).

Render the gym CDNG through Resolve (YRGB default → Rec.709 display) and
compare to our pipeline output taken to the same display space.
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
OUT = Path("/tmp/resolve_verify/cdng_color")
W, Hh = 640, 426


def _ours_srgb_small() -> np.ndarray:
    """Our pipeline gym render → sRGB8 → downscaled to (Hh, W)."""
    import colour

    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.pipeline import render_frame

    pp = render_frame(GYM, parse_dcp(DCP), dcp_path=DCP).prophoto
    m_pp = colour.RGB_COLOURSPACES["ProPhoto RGB"].matrix_RGB_to_XYZ
    m_sr = colour.RGB_COLOURSPACES["sRGB"].matrix_XYZ_to_RGB
    bf = colour.adaptation.matrix_chromatic_adaptation_VonKries(
        np.array([0.96422, 1.0, 0.82521]), np.array([0.95047, 1.0, 1.08883]), "Bradford"
    )
    h, w, _ = pp.shape
    xyz = pp.reshape(-1, 3) @ m_pp.T @ bf.T
    lin = np.clip((xyz @ m_sr.T), 0, 1).reshape(h, w, 3)
    a = 0.055
    srgb = np.where(
        lin <= 0.0031308, lin * 12.92, (1 + a) * np.power(np.maximum(lin, 0), 1 / 2.4) - a
    )
    # downscale by block-mean to (Hh, W)
    ys = np.linspace(0, h, Hh + 1).astype(int)
    xs = np.linspace(0, w, W + 1).astype(int)
    small = np.empty((Hh, W, 3))
    for j in range(Hh):
        for i in range(W):
            small[j, i] = srgb[ys[j] : ys[j + 1], xs[i] : xs[i + 1]].reshape(-1, 3).mean(0)
    return small


def _lab(srgb01):
    import colour

    lin = colour.models.eotf_sRGB(np.clip(srgb01, 0, 1))
    xyz = colour.RGB_to_XYZ(lin, "sRGB", apply_cctf_decoding=False)
    return colour.XYZ_to_Lab(xyz, illuminant=np.array([0.31270, 0.32900]))


def main() -> int:
    import colour

    resolve = H.connect()
    print("connected:", resolve.GetProductName())
    with H.scratch_project(resolve) as proj:
        proj.SetSetting("colorScienceMode", "davinciYRGB")
        items = H.import_media(resolve, proj, [GYM])
        files = H.render_clip(resolve, proj, items[0], OUT, "cdngcolor", w=W, h=Hh)
        res = tifffile.imread(str(files[0])).astype(np.float64) / 65535.0

    ours = _ours_srgb_small()
    # Both are display-referred sRGB/Rec.709-ish; align center region.
    de = colour.delta_E(_lab(ours), _lab(res), method="CIE 2000")
    print(f"\nResolve CDNG decode vs our pipeline (display sRGB/Rec.709, {W}x{Hh}):")
    print(
        f"  mean ΔE2000={float(de.mean()):.2f}  P50={float(np.percentile(de, 50)):.2f}  "
        f"P95={float(np.percentile(de, 95)):.2f}"
    )
    print("  (ballpark/upper-bound — cross-pipeline gamma+CM differences included)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
