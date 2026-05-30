"""Does Resolve honor `acesImageContainerFlag` / `chromaticities` in ACES mode?

test_chromaticities.py proved Resolve ignores the EXR primaries attributes in
DaVinci YRGB Color Managed (RCM). ACES mode is the OTHER place they might be
auto-honored — Autodesk Flame auto-assigns ACES2065-1 from
`acesImageContainerFlag=1` in "From File or Rules" mode, so ACES-aware ingest
COULD read the flag. This closes that gap.

Method: colorScienceMode = ACES. Import the AP0+flag=1 variant and the untagged
variant WITHOUT manually setting an IDT/Input Transform. Render both, compare.
  differ    -> Resolve auto-honors the ACES flag in ACES mode (corrects the
               RCM-scoped claim).
  identical -> the "Resolve ignores the attribute" finding holds across modes.

Reuses the assets written by test_chromaticities.py (none / aces variants).
Restores the user's project on exit.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import numpy as np
import tifffile

from tools.resolve_verify import harness as H
from tools.resolve_verify.test_chromaticities import ASSETS, _make_assets, _patch_means

RENDERS = Path("/tmp/resolve_verify/aces_renders")
W, Hh = 256, 256


def _render(resolve, proj, item, name) -> np.ndarray:
    mp = proj.GetMediaPool()
    tl = mp.CreateTimelineFromClips(name, [item])
    proj.SetCurrentTimeline(tl)
    proj.SetSetting("timelineResolutionWidth", str(W))
    proj.SetSetting("timelineResolutionHeight", str(Hh))
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


def main() -> int:
    if not (ASSETS / "aces" / "C_aces_0001.exr").is_file():
        print("building assets (none/aces variants)...")
        _make_assets()

    resolve = H.connect()
    print("connected:", resolve.GetProductName(), resolve.GetVersionString())
    results = {}
    with H.scratch_project(resolve) as proj:
        # Switch the project to ACES. Try the known mode strings; report which sticks.
        applied = None
        for mode in ("acescct", "acescc", "aces"):
            proj.SetSetting("colorScienceMode", mode)
            got = proj.GetSetting("colorScienceMode")
            if got and "aces" in got.lower():
                applied = got
                break
        print("colorScienceMode now:", proj.GetSetting("colorScienceMode"), "(requested aces*)")
        if applied is None:
            print("WARN: could not switch to an ACES mode; result is inconclusive")

        none_i = H.import_media(resolve, proj, [ASSETS / "none"])
        aces_i = H.import_media(resolve, proj, [ASSETS / "aces"])
        print(f"imported: none={len(none_i)} aces={len(aces_i)}")
        if not (none_i and aces_i):
            print("FAIL: import incomplete")
            return 1

        # Deliberately DO NOT set an IDT/Input Transform — we want to see if the
        # flag auto-assigns one. Report the clips' auto-assigned input transform.
        for tag, it in (("none", none_i[0]), ("aces", aces_i[0])):
            with __import__("contextlib").suppress(Exception):
                p = it.GetClipProperty("ACES Transform ID") or it.GetClipProperty(
                    "Input Color Space"
                )
                print(f"  [{tag}] auto input transform/space readback: {p!r}")

        none_img = _render(resolve, proj, none_i[0], "aces_none")
        aces_img = _render(resolve, proj, aces_i[0], "aces_flagged")
        for tag, im in (("untagged", none_img), ("AP0+flag", aces_img)):
            s, n = _patch_means(im)
            print(f"  [{tag}] sat={s.round(4)}  neutral={n.round(4)}")
        d = float(np.abs(none_img - aces_img).mean())
        print(f"\n[Δ] untagged vs AP0+flag in ACES mode: {d:.5f}")
        honored = d > 1e-3
        results["aces_flag_auto_honored_in_aces_mode"] = honored
        print(
            f"[VERDICT] ACES mode {'AUTO-HONORS' if honored else 'does NOT auto-honor'} "
            f"acesImageContainerFlag/chromaticities (Δ={d:.5f})"
        )

    print("\n=== ACES-mode flag test summary ===")
    for k, v in results.items():
        print(f"  {v!s:6}  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
