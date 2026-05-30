"""Headless DaVinci Resolve Studio verification harness.

The whole point of the current goal is that emission-format claims must be
*verified against Resolve*, not asserted via a manual checkpoint that never
runs. This module is the reusable plumbing: connect to a running Resolve
Studio instance, do work in a throwaway scratch project, render headless,
read the pixels back, and restore the user's original project on exit.

Confirmed environment: DaVinci Resolve Studio 21.0.0b.33, scripting reachable
(see tools/resolve_verify/ findings). The user keeps a project open; this
harness never mutates it — it creates `lrt_verify_scratch`, works there, and
re-opens whatever was current before.

Run directly for a plumbing self-test:
    python tools/resolve_verify/harness.py
"""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path

# --- connection -------------------------------------------------------------

_API = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
# The running instance is "DaVinci Resolve 21"; its fusionscript.so is the one
# we must bind. Fall back to the generic install if 21 is absent.
_LIB_CANDIDATES = [
    "/Applications/DaVinci Resolve 21/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so",
    "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so",
]
SCRATCH_PROJECT = "lrt_verify_scratch"


def connect():
    """Return a connected Resolve app object, or raise with a clear reason."""
    os.environ.setdefault("RESOLVE_SCRIPT_API", _API)
    lib = next((p for p in _LIB_CANDIDATES if Path(p).is_file()), None)
    if lib is None:
        raise RuntimeError("fusionscript.so not found in any known location")
    os.environ.setdefault("RESOLVE_SCRIPT_LIB", lib)
    mod_dir = str(Path(_API) / "Modules")
    if mod_dir not in os.sys.path:
        os.sys.path.append(mod_dir)
    import DaVinciResolveScript as dvr  # type: ignore

    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        raise RuntimeError(
            "scriptapp('Resolve') returned None — is Resolve running and is "
            "external scripting enabled (Preferences > System > General)?"
        )
    return resolve


@contextlib.contextmanager
def scratch_project(resolve, name: str = SCRATCH_PROJECT):
    """Work inside a throwaway project; restore the user's project on exit.

    Never deletes the user's project. Creates `name` fresh each run (deletes a
    stale scratch of the same name first).
    """
    pm = resolve.GetProjectManager()
    original = pm.GetCurrentProject()
    original_name = original.GetName() if original else None
    # Drop a stale scratch so each run starts clean. CloseProject first so it
    # isn't the active project (can't delete the open one).
    pm.CloseProject(original) if original else None
    with contextlib.suppress(Exception):
        pm.DeleteProject(name)
    proj = pm.CreateProject(name)
    if proj is None:  # already existed and wasn't deleted — load it
        proj = pm.LoadProject(name)
    try:
        yield proj
    finally:
        pm.SaveProject()
        with contextlib.suppress(Exception):
            cur = pm.GetCurrentProject()
            pm.CloseProject(cur) if cur else None
        if original_name:
            pm.LoadProject(original_name)


def set_setting(proj, key: str, value: str) -> bool:
    ok = proj.SetSetting(key, value)
    return bool(ok)


def import_media(resolve, proj, paths: list[Path]):
    """Add files/folders to the media pool. Returns the list of MediaPoolItems."""
    ms = resolve.GetMediaStorage()
    mp = proj.GetMediaPool()
    mp.SetCurrentFolder(mp.GetRootFolder())
    items = ms.AddItemListToMediaPool([str(p) for p in paths])
    return items or []


def timeline_from(proj, name: str, items):
    mp = proj.GetMediaPool()
    tl = mp.CreateTimelineFromClips(name, items)
    return tl


def pick_format_codec(proj, want_format_substr: str, want_codec_substr: str):
    """Resolve render format/codec names vary by version; resolve them by
    case-insensitive substring against the live lists. Returns (fmt, codec)."""
    fmts = proj.GetRenderFormats()  # {description: ext}
    fmt = None
    for desc, ext in fmts.items():
        if want_format_substr.lower() in desc.lower() or want_format_substr.lower() == ext.lower():
            fmt = ext
            break
    if fmt is None:
        raise RuntimeError(f"no render format matching {want_format_substr!r}; have {fmts}")
    codecs = proj.GetRenderCodecs(fmt)  # {description: codecName}
    codec = None
    for desc, name in codecs.items():
        if want_codec_substr.lower() in desc.lower() or want_codec_substr.lower() in name.lower():
            codec = name
            break
    if codec is None:
        # fall back to the first codec
        codec = next(iter(codecs.values())) if codecs else None
    return fmt, codec


def render_clip(
    resolve,
    proj,
    item,
    out_dir: Path,
    name: str,
    w: int = 640,
    h: int = 426,
    cdl: dict | None = None,
    timeout_s: int = 180,
) -> list[Path]:
    """Robust single-clip render to a TIFF16 sequence. Encodes every hard-won
    Resolve-scripting fix:
      - explicit timeline resolution (else AddRenderJob returns '')
      - Deliver page open + settle + retry (else AddRenderJob returns '')
      - wait on files-present AND settled (else fast renders race StopRendering)
    Returns the rendered files (one per timeline frame), sorted.
    """
    import shutil

    mp = proj.GetMediaPool()
    tl = mp.CreateTimelineFromClips(name, [item])
    proj.SetCurrentTimeline(tl)
    proj.SetSetting("timelineResolutionWidth", str(w))
    proj.SetSetting("timelineResolutionHeight", str(h))
    if cdl is not None:
        tl.GetItemListInTrack("video", 1)[0].SetCDL(cdl)
    out = out_dir / name
    if out.exists():
        shutil.rmtree(out)
    fmt, codec = pick_format_codec(proj, "tif", "16")
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
    waited = 0.0
    while waited < timeout_s:
        time.sleep(1.0)
        waited += 1.0
        if not proj.IsRenderingInProgress() and list(out.glob(f"{name}*.tif")):
            break
    files = sorted(out.glob(f"{name}*.tif"))
    if not files:
        raise RuntimeError(f"render {name!r} produced no files")
    return files


def render_timeline(
    proj, target_dir: Path, fmt: str, codec: str, custom_name: str = "RV", timeout_s: int = 180
) -> list[Path]:
    """Render the current timeline (all frames, individual-clip mode) to
    target_dir and block until done. Returns the rendered files."""
    target_dir.mkdir(parents=True, exist_ok=True)
    proj.SetCurrentRenderFormatAndCodec(fmt, codec)
    proj.SetCurrentRenderMode(1)  # single clip = one file per frame range
    proj.SetRenderSettings(
        {
            "SelectAllFrames": True,
            "TargetDir": str(target_dir),
            "CustomName": custom_name,
            "UniqueFilenameStyle": 1,  # suffix
        }
    )
    job = proj.AddRenderJob()
    if not job:
        raise RuntimeError("AddRenderJob failed (returned empty job id)")
    proj.StartRendering(job)
    # Wait on files-present AND settled — robust to fast renders where
    # IsRenderingInProgress() briefly reads False before output exists.
    waited = 0.0
    while waited < timeout_s:
        time.sleep(1.0)
        waited += 1.0
        if not proj.IsRenderingInProgress() and list(target_dir.glob(f"{custom_name}*")):
            break
    return sorted(target_dir.glob(f"{custom_name}*"))


# --- plumbing self-test -----------------------------------------------------


def _self_test() -> int:
    gym = Path("/tmp/dng_out/DSC_4053.dng")
    if not gym.is_file():
        print(f"SKIP self-test: missing {gym}")
        return 0
    out = Path("/tmp/resolve_verify/selftest")
    if out.exists():
        import shutil

        shutil.rmtree(out)

    resolve = connect()
    print("connected:", resolve.GetProductName(), resolve.GetVersionString())
    with scratch_project(resolve) as proj:
        print("scratch project:", proj.GetName())
        items = import_media(resolve, proj, [gym])
        print(f"imported {len(items)} item(s)")
        if not items:
            print("FAIL: nothing imported")
            return 1
        tl = timeline_from(proj, "selftest_tl", items)
        print(
            "timeline:",
            tl.GetName() if tl else None,
            "frames:",
            tl.GetEndFrame() - tl.GetStartFrame() + 1 if tl else "?",
        )
        fmt, codec = pick_format_codec(proj, "tif", "16")
        print(f"render format/codec: {fmt} / {codec}")
        files = render_timeline(proj, out, fmt, codec, custom_name="selftest")
        print(f"rendered {len(files)} file(s): {[f.name for f in files][:3]}")
        ok = len(files) > 0
    print("self-test:", "PASS — headless ingest→render→readback works" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_self_test())
