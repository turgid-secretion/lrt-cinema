"""Does DaVinci Resolve read the OpenEXR `chromaticities` (and
`acesImageContainerFlag`) header attribute to determine an imported linear
EXR's GAMUT/primaries?

This is the empirical half of the "linear-EXR gamut" question (task #15).
The web answer ("Resolve does not auto-read EXR chromaticities; gamut comes
from the Input Color Space assignment") is a [claim] until tested against the
real product. This test makes it [here].

Method
------
Write the SAME linear pixels three ways and import all three into one
DaVinci-YRGB-Color-Managed project with a fixed timeline color space and the
SAME Input Color Space assigned to every clip:

  none    — no chromaticities attribute at all
  rec709  — chromaticities = Rec.709 primaries + D65
  aces    — chromaticities = ACES AP0 primaries + acesImageContainerFlag=1

If Resolve READ the attribute, the three would decode to different colors
(the patch's primaries would be interpreted differently). If Resolve IGNORES
it, all three render identically — the gamut is governed solely by the Input
Color Space dropdown, not the file header.

A saturated patch (most of its energy in one primary) maximizes the gamut
sensitivity: a Rec.709-vs-AP0 reinterpretation of the same code values moves
a saturated pixel a lot, a neutral pixel barely.

Low-res, relative pixel comparison only. Restores the user's project on exit.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import numpy as np
import OpenEXR
import tifffile

from tools.resolve_verify import harness as H

ASSETS = Path("/tmp/resolve_verify/chroma_assets")
RENDERS = Path("/tmp/resolve_verify/chroma_renders")
W, Hh = 256, 256

# CIE (x,y) primaries + white, in OpenEXR chromaticities order:
# Rx Ry Gx Gy Bx By Wx Wy
REC709 = (0.640, 0.330, 0.300, 0.600, 0.150, 0.060, 0.3127, 0.3290)
AP0 = (0.7347, 0.2653, 0.0000, 1.0000, 0.0001, -0.0770, 0.32168, 0.33767)


def _make_image() -> np.ndarray:
    """(H,W,3) float32 linear. Top half: saturated patch heavy in R+G (a
    color whose interpretation swings hard between Rec.709 and AP0). Bottom
    half: a neutral grey. Values < 1.0 so nothing clips on a plain ingest."""
    img = np.zeros((Hh, W, 3), np.float32)
    img[: Hh // 2] = (0.80, 0.60, 0.05)  # saturated warm
    img[Hh // 2 :] = (0.40, 0.40, 0.40)  # neutral
    return img


def _write_exr(path: Path, rgb: np.ndarray, chroma=None, aces_flag=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "compression": OpenEXR.ZIP_COMPRESSION,
        "type": OpenEXR.scanlineimage,
    }
    if chroma is not None:
        header["chromaticities"] = tuple(float(x) for x in chroma)
    if aces_flag:
        header["acesImageContainerFlag"] = 1
    channels = {
        "R": np.ascontiguousarray(rgb[..., 0].astype(np.float16)),
        "G": np.ascontiguousarray(rgb[..., 1].astype(np.float16)),
        "B": np.ascontiguousarray(rgb[..., 2].astype(np.float16)),
    }
    with OpenEXR.File(header, channels) as f:
        f.write(str(path))


def _make_assets() -> None:
    if ASSETS.exists():
        shutil.rmtree(ASSETS)
    img = _make_image()
    for i in (1, 2):  # 2-frame sequence per variant
        _write_exr(ASSETS / "none" / f"C_none_{i:04d}.exr", img, chroma=None)
        _write_exr(ASSETS / "rec709" / f"C_rec709_{i:04d}.exr", img, chroma=REC709)
        _write_exr(ASSETS / "aces" / f"C_aces_{i:04d}.exr", img, chroma=AP0, aces_flag=True)
    # Confirm the attributes actually landed on disk (rule out a silent no-op).
    for variant, _want in (
        ("none", None),
        ("rec709", "chromaticities"),
        ("aces", "acesImageContainerFlag"),
    ):
        p = ASSETS / variant / f"C_{variant}_0001.exr"
        with OpenEXR.File(str(p)) as f:
            hk = f.header()
            has_chroma = "chromaticities" in hk
            has_flag = "acesImageContainerFlag" in hk
        print(f"  {variant}: chromaticities={has_chroma} acesFlag={has_flag}")


def _render(resolve, proj, item, name, input_cs: str | None = None) -> np.ndarray:
    mp = proj.GetMediaPool()
    tl = mp.CreateTimelineFromClips(name, [item])
    proj.SetCurrentTimeline(tl)
    proj.SetSetting("timelineResolutionWidth", str(W))
    proj.SetSetting("timelineResolutionHeight", str(Hh))
    if input_cs is not None:
        # Assign the clip's input color space explicitly (RCM).
        ok = item.SetClipProperty("Input Color Space", input_cs)
        print(f"    SetClipProperty('Input Color Space','{input_cs}') -> {ok}")
    out = RENDERS / name
    if out.exists():
        shutil.rmtree(out)
    fmt, codec = H.pick_format_codec(proj, "tif", "16")
    proj.SetCurrentRenderFormatAndCodec(fmt, codec)
    proj.SetCurrentRenderMode(1)
    proj.SetRenderSettings(
        {
            "SelectAllFrames": True,
            "TargetDir": str(out),
            "CustomName": name,
            "UniqueFilenameStyle": 1,
        }
    )
    resolve.OpenPage("deliver")
    time.sleep(1.5)
    job = ""
    for _ in range(5):
        job = proj.AddRenderJob()
        if job:
            break
        time.sleep(1.0)
    if not job:
        raise RuntimeError(f"AddRenderJob failed for {name!r}")
    proj.StartRendering(job)
    for _ in range(180):
        time.sleep(1.0)
        if not proj.IsRenderingInProgress() and list(out.glob(f"{name}*.tif")):
            break
    files = sorted(out.glob(f"{name}*.tif"))
    if not files:
        raise RuntimeError(f"render {name!r} produced no files")
    return tifffile.imread(str(files[0])).astype(np.float64) / 65535.0


def _patch_means(img: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (saturated-patch mean RGB, neutral-patch mean RGB)."""
    h = img.shape[0]
    sat = img[: h // 2].reshape(-1, img.shape[-1]).mean(0)
    neu = img[h // 2 :].reshape(-1, img.shape[-1]).mean(0)
    return sat, neu


def main() -> int:
    print("building chromaticities test assets...")
    _make_assets()

    resolve = H.connect()
    print("connected:", resolve.GetProductName(), resolve.GetVersionString())
    results = {}
    with H.scratch_project(resolve) as proj:
        # DaVinci YRGB Color Managed (RCM) — the mode where Input Color Space
        # governs gamut interpretation.
        proj.SetSetting("colorScienceMode", "davinciYRGBColorManagedv2")
        # Fixed, known timeline + a simple input default so the only variable
        # is the EXR header attribute.
        proj.SetSetting("colorSpaceTimeline", "Rec.709 Gamma 2.4")
        proj.SetSetting("colorSpaceInput", "Rec.709 Gamma 2.4")
        print("color science:", proj.GetSetting("colorScienceMode"))
        print("timeline cs   :", proj.GetSetting("colorSpaceTimeline"))
        print("input cs dflt :", proj.GetSetting("colorSpaceInput"))

        none_i = H.import_media(resolve, proj, [ASSETS / "none"])
        r709_i = H.import_media(resolve, proj, [ASSETS / "rec709"])
        aces_i = H.import_media(resolve, proj, [ASSETS / "aces"])
        print(f"imported: none={len(none_i)} rec709={len(r709_i)} aces={len(aces_i)}")
        if not (none_i and r709_i and aces_i):
            print("FAIL: import incomplete")
            return 1

        # Assign the SAME Input Color Space to all three so the dropdown is a
        # constant; only the file header differs.
        IN_CS = "Linear"
        none_img = _render(resolve, proj, none_i[0], "chroma_none", input_cs=IN_CS)
        r709_img = _render(resolve, proj, r709_i[0], "chroma_rec709", input_cs=IN_CS)
        aces_img = _render(resolve, proj, aces_i[0], "chroma_aces", input_cs=IN_CS)

        for tag, im in (("none", none_img), ("rec709", r709_img), ("aces", aces_img)):
            s, n = _patch_means(im)
            print(f"  [{tag}] sat-patch RGB={s.round(4)}  neutral RGB={n.round(4)}")

        d_709 = float(np.abs(none_img - r709_img).mean())
        d_aces = float(np.abs(none_img - aces_img).mean())
        d_709_aces = float(np.abs(r709_img - aces_img).mean())
        print(f"\n[Δ] none vs rec709-tagged : {d_709:.5f}")
        print(f"[Δ] none vs aces-tagged   : {d_aces:.5f}")
        print(f"[Δ] rec709 vs aces tagged : {d_709_aces:.5f}")

        # Threshold: 16-bit TIFF readback noise is ~1e-4. Anything < 1e-3 mean
        # abs diff across the whole frame = "identical decode" = attribute
        # IGNORED. A real Rec.709-vs-AP0 reinterpretation of a saturated patch
        # would move means by >0.05.
        ignored = max(d_709, d_aces, d_709_aces) < 1e-3
        results["chromaticities_ignored_by_resolve"] = ignored
        print(
            f"\n[VERDICT] Resolve {'IGNORES' if ignored else 'READS'} the EXR "
            f"chromaticities/acesImageContainerFlag attribute "
            f"(max Δ = {max(d_709, d_aces, d_709_aces):.5f})"
        )

    print("\n=== chromaticities test summary ===")
    for k, v in results.items():
        print(f"  {'PASS' if v else 'FAIL/UNCLEAR'}  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
