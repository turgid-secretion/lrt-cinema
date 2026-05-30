"""Resolve tests for the EXR data+grade answer:

  M  Multi-layer EXR: does Resolve expose the 'baked' layer of a single EXR
     selectably? (decides self-contained single-file vs dual-file packaging)
  R  Recovery: does the scene-ref EXR's overrange (>1.0) actually pull back
     through a Resolve grade? (proves the recovery axis is real IN Resolve)

Renders at low res (relative pixel comparisons only) for speed. Restores the
user's project on exit via the harness context manager.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import tifffile

from tools.resolve_verify import harness as H

ASSETS = Path("/tmp/resolve_verify/assets")
RENDERS = Path("/tmp/resolve_verify/renders")
W, Hh = 640, 426


def _mean_frame(files: list[Path]) -> np.ndarray:
    if not files:
        raise RuntimeError("render produced no files")
    a = tifffile.imread(str(files[0])).astype(np.float64) / 65535.0
    return a


def _render_clip(resolve, proj, item, name, cdl=None) -> np.ndarray:
    mp = proj.GetMediaPool()
    tl = mp.CreateTimelineFromClips(name, [item])
    proj.SetCurrentTimeline(tl)
    # AddRenderJob returns '' on an EXR-sequence timeline unless a concrete
    # timeline resolution is set (diagnosed). FormatWidth/Height render-
    # override does NOT substitute; the project timeline resolution does.
    proj.SetSetting("timelineResolutionWidth", str(W))
    proj.SetSetting("timelineResolutionHeight", str(Hh))
    if cdl is not None:
        vid = tl.GetItemListInTrack("video", 1)
        vid[0].SetCDL(cdl)
    out = RENDERS / name
    if out.exists():
        shutil.rmtree(out)
    # Proven sequence (matches harness self-test): format/codec → mode →
    # settings → AddRenderJob. Reordering (format after settings) or calling
    # DeleteAllRenderJobs here makes AddRenderJob return '' (diagnosed).
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
    # AddRenderJob returns '' unless the Deliver page is open AND has settled
    # (OpenPage is async; the first attempt after switching still fails).
    import time

    resolve.OpenPage("deliver")
    time.sleep(1.5)
    job = ""
    for _ in range(5):
        job = proj.AddRenderJob()
        if job:
            break
        time.sleep(1.0)
    if not job:
        raise RuntimeError(f"AddRenderJob failed for '{name}' (deliver page)")
    proj.StartRendering(job)
    # Wait on files-present AND settled — robust to fast 2-frame renders where
    # IsRenderingInProgress() briefly reads False before output exists.
    import time

    for _ in range(180):
        time.sleep(1)
        if not proj.IsRenderingInProgress() and list(out.glob(f"{name}*.tif")):
            break
    files = sorted(out.glob(f"{name}*.tif"))
    if not files:
        raise RuntimeError(f"render '{name}' produced no files")
    return _mean_frame(files)


def main() -> int:
    resolve = H.connect()
    print("connected:", resolve.GetProductName(), resolve.GetVersionString())
    results = {}
    with H.scratch_project(resolve) as proj:
        proj.SetSetting("colorScienceMode", "davinciYRGB")
        scene = H.import_media(resolve, proj, [ASSETS / "scene_ref"])
        baked = H.import_media(resolve, proj, [ASSETS / "baked"])
        ml = H.import_media(resolve, proj, [ASSETS / "multilayer"])
        print(f"imported: scene={len(scene)} baked={len(baked)} multilayer={len(ml)}")

        # ---- probe what Resolve exposes about the multilayer clip ----
        if ml:
            props = ml[0].GetClipProperty()
            keys = {
                k: v
                for k, v in props.items()
                if any(t in k.lower() for t in ("layer", "channel", "format", "name"))
            }
            print("multilayer clip props (layer/channel/format):", keys)

        # ---- M: which layer is the multilayer default? ----
        scene_img = _render_clip(resolve, proj, scene[0], "scene_plain")
        baked_img = _render_clip(resolve, proj, baked[0], "baked_plain")
        ml_img = _render_clip(resolve, proj, ml[0], "ml_plain")
        d_scene = float(np.abs(ml_img - scene_img).mean())
        d_baked = float(np.abs(ml_img - baked_img).mean())
        print(f"\n[M] multilayer-default vs scene={d_scene:.4f}  vs baked={d_baked:.4f}")
        default_layer = "scene-ref" if d_scene < d_baked else "baked"
        print(f"[M] Resolve's default layer for the multilayer EXR = {default_layer}")
        # scene and baked must actually differ, else the comparison is meaningless
        scene_vs_baked = float(np.abs(scene_img - baked_img).mean())
        print(f"[M] (scene vs baked differ by {scene_vs_baked:.4f} — sanity)")
        results["scene_vs_baked_differ"] = scene_vs_baked > 0.02

        # ---- R: recovery — pixels BLOWN in the plain 1.0 view, pulled down ----
        dark = {
            "NodeIndex": "1",
            "Slope": "0.30 0.30 0.30",
            "Offset": "0 0 0",
            "Power": "1 1 1",
            "Saturation": "1",
        }
        scene_dark = _render_clip(resolve, proj, scene[0], "scene_dark", cdl=dark)
        baked_dark = _render_clip(resolve, proj, baked[0], "baked_dark", cdl=dark)
        # Anchor on pixels that are blown (>=0.99) in the plain scene render —
        # guaranteed non-empty because scene-ref carries overrange. If the
        # overrange survived into Resolve's float pipeline, darkening these
        # reveals graded-down detail (mean << 1, real spread). If Resolve had
        # clipped on ingest, they'd stay flat.
        sL = scene_img.mean(-1)
        hot = sL >= 0.99
        n = int(hot.sum())
        print(f"\n[R] blown-in-plain-scene region: {n} px ({100 * hot.mean():.2f}%)")
        if n == 0:
            print("[R] no blown pixels — cannot assess")
            results["recovery_in_resolve"] = False
        else:
            s_mean, s_std = float(scene_dark[hot].mean()), float(scene_dark[hot].std())
            b_mean, b_std = float(baked_dark[hot].mean()), float(baked_dark[hot].std())
            print(f"    scene-ref pulled to slope0.30: mean={s_mean:.3f} std={s_std:.4f}")
            print(f"    baked     pulled to slope0.30: mean={b_mean:.3f} std={b_std:.4f}")
            # Recovery = the blown region resolves to graded detail well below 1.0
            # with real spread, AND scene-ref reveals more detail than the baked
            # look (whose highlights were tone-curve-compressed in-pixel).
            recovered = (s_mean < 0.9) and (s_std > 0.01) and (s_std >= b_std)
            print(
                f"[R] scene-ref overrange recovers through Resolve: {'YES' if recovered else 'NO'}"
            )
            results["recovery_in_resolve"] = recovered

    print("\n=== EXR test summary ===")
    for k, v in results.items():
        print(f"  {'PASS' if v else 'FAIL/UNCLEAR'}  {k}")
    print(f"  default multilayer layer: {default_layer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
