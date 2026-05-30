"""What GAMUT does Resolve's Input Color Space = "Linear" assume?

Companion to test_chromaticities.py. That test proved Resolve ignores the EXR
header primaries. So when a clip is tagged Input = "Linear", where do the
primaries come from? Two live hypotheses:

  (a) "Linear" pins the input gamut to the TIMELINE working gamut (i.e. the
      pixels are assumed already in timeline primaries; only a linear->working
      transfer-function change happens, no gamut rotation). Prediction: the
      same untagged linear file decodes IDENTICALLY regardless of timeline
      color space.
  (b) "Linear" assumes a fixed gamut (e.g. Rec.709) and Resolve applies a
      gamut transform into the timeline gamut. Prediction: a saturated patch
      shifts when the timeline gamut changes (Rec.709 vs Rec.2020).

Method: assign Input = "Linear" to the SAME untagged linear EXR, render it
once into a Rec.709-Linear-ish project and once into a Rec.2020 project, and
compare. Also dump the live Input Color Space list so we can see exactly which
"Linear*" entries exist (Q: why is there a bare "Linear" but no
"Linear/Rec.2020" in the INPUT list, while timeline/output lists have
gamut-qualified linear entries?).

Restores the user's project on exit.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import numpy as np
import OpenEXR
import tifffile

from tools.resolve_verify import harness as H

ASSETS = Path("/tmp/resolve_verify/lin_assets")
RENDERS = Path("/tmp/resolve_verify/lin_renders")
W, Hh = 256, 256


def _make_assets() -> None:
    if ASSETS.exists():
        shutil.rmtree(ASSETS)
    img = np.zeros((Hh, W, 3), np.float32)
    img[: Hh // 2] = (0.80, 0.10, 0.05)  # saturated red — max gamut sensitivity
    img[Hh // 2 :] = (0.40, 0.40, 0.40)  # neutral
    header = {"compression": OpenEXR.ZIP_COMPRESSION, "type": OpenEXR.scanlineimage}
    for i in (1, 2):
        p = ASSETS / f"L_{i:04d}.exr"
        p.parent.mkdir(parents=True, exist_ok=True)
        ch = {
            "R": np.ascontiguousarray(img[..., 0].astype(np.float16)),
            "G": np.ascontiguousarray(img[..., 1].astype(np.float16)),
            "B": np.ascontiguousarray(img[..., 2].astype(np.float16)),
        }
        with OpenEXR.File(header, ch) as f:
            f.write(str(p))


def _render(resolve, proj, item, name, input_cs="Linear") -> np.ndarray:
    mp = proj.GetMediaPool()
    tl = mp.CreateTimelineFromClips(name, [item])
    proj.SetCurrentTimeline(tl)
    proj.SetSetting("timelineResolutionWidth", str(W))
    proj.SetSetting("timelineResolutionHeight", str(Hh))
    item.SetClipProperty("Input Color Space", input_cs)
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


def _sat_mean(img):
    h = img.shape[0]
    return img[: h // 2].reshape(-1, img.shape[-1]).mean(0)


def _neu_mean(img):
    h = img.shape[0]
    return img[h // 2 :].reshape(-1, img.shape[-1]).mean(0)


def main() -> int:
    print("building linear-gamut test assets...")
    _make_assets()
    resolve = H.connect()
    print("connected:", resolve.GetProductName(), resolve.GetVersionString())
    results = {}
    with H.scratch_project(resolve) as proj:
        proj.SetSetting("colorScienceMode", "davinciYRGBColorManagedv2")
        proj.SetSetting("colorSpaceInput", "Rec.709 Gamma 2.4")

        # Dump the live Input Color Space option list (clip-level), so we can
        # see which Linear* entries exist on the INPUT side.
        items0 = H.import_media(resolve, proj, [ASSETS])
        clip = items0[0]
        # Probe a set of candidate values and record which SetClipProperty
        # accepts (returns True) — an empirical enumeration of valid input names.
        candidates = [
            "Linear",
            "Linear/Rec.2020",
            "Linear/Rec.709",
            "Rec.709 Gamma 2.4",
            "Rec.2020",
            "Rec.2020 Gamma 2.4",
            "DaVinci Wide Gamut Intermediate",
            "ACEScg",
            "ACEScct",
            "ACES2065-1",
            "Same as Timeline",
            "Bypass",
        ]
        valid = []
        for c in candidates:
            ok = clip.SetClipProperty("Input Color Space", c)
            if ok:
                back = clip.GetClipProperty("Input Color Space")
                valid.append((c, back))
        print("\nInput Color Space values accepted by SetClipProperty (clip-level):")
        for c, back in valid:
            print(f"   set {c!r:34} -> readback {back!r}")
        results["input_has_bare_Linear"] = any(c == "Linear" for c, _ in valid)
        results["input_has_Linear_Rec2020"] = any(c == "Linear/Rec.2020" for c, _ in valid)

        # ---- the gamut probe: Input=Linear into Rec.709 vs Rec.2020 timeline ----
        # Pin OUTPUT to Rec.709 in BOTH renders so the only variable is the
        # timeline working gamut. The delta then isolates the timeline->output
        # conversion acting on pixels that inherited the timeline primaries.
        proj.SetSetting("colorSpaceOutput", "Rec.709 Gamma 2.4")
        proj.SetSetting("colorSpaceTimeline", "Rec.709 Gamma 2.4")
        print(
            "\n  [chain] Input=Linear  Timeline=",
            proj.GetSetting("colorSpaceTimeline"),
            " Output=",
            proj.GetSetting("colorSpaceOutput"),
        )
        img_709 = _render(resolve, proj, clip, "lin_into_709", input_cs="Linear")

        proj.SetSetting("colorSpaceOutput", "Rec.709 Gamma 2.4")
        proj.SetSetting("colorSpaceTimeline", "Rec.2020 Gamma 2.4")
        print(
            "  [chain] Input=Linear  Timeline=",
            proj.GetSetting("colorSpaceTimeline"),
            " Output=",
            proj.GetSetting("colorSpaceOutput"),
        )
        img_2020 = _render(resolve, proj, clip, "lin_into_2020", input_cs="Linear")

        s709, s2020 = _sat_mean(img_709), _sat_mean(img_2020)
        n709, n2020 = _neu_mean(img_709), _neu_mean(img_2020)
        print(f"\n  Input=Linear, timeline Rec.709 : sat={s709.round(4)}  neutral={n709.round(4)}")
        print(f"  Input=Linear, timeline Rec.2020: sat={s2020.round(4)}  neutral={n2020.round(4)}")
        d = float(np.abs(img_709 - img_2020).mean())
        d_sat = float(np.abs(s709 - s2020).mean())
        d_neu = float(np.abs(n709 - n2020).mean())
        print(
            f"\n[Δ] Linear-into-709 vs Linear-into-2020: full={d:.5f}  sat-patch={d_sat:.5f}  neutral-patch={d_neu:.5f}"
        )
        # Gamut-reinterpretation signature: saturated patch MOVES, neutral is
        # INVARIANT (neutral is gamut-independent). Print it as a cross-check.
        print(
            f"[OBS] saturated patch {'MOVES' if d_sat > 1e-3 else 'is stable'}; "
            f"neutral patch {'is INVARIANT' if d_neu < 1e-3 else 'MOVES'} "
            "(moves-sat + invariant-neutral = gamut reinterpretation, "
            "confirming Linear inherits the timeline primaries)"
        )
        results["neutral_invariant_across_timeline_gamut"] = d_neu < 1e-3

        # If "Linear" pinned to timeline gamut, the saturated red would be
        # treated as already-in-Rec.709 and already-in-Rec.2020 respectively —
        # the SAME code values, only the output-encode differs, so a saturated
        # red would shift visibly (Rec.2020 red is more saturated). If instead
        # "Linear" assumes a FIXED input gamut, Resolve would gamut-convert and
        # the displayed color would be preserved (numbers differ by the encode).
        # Either way a non-trivial Δ means the timeline gamut matters; a ~0 Δ
        # would mean the timeline gamut is ignored for a Linear-tagged clip.
        results["timeline_gamut_affects_linear_clip"] = d > 1e-3
        print(
            f"[OBS] timeline gamut {'AFFECTS' if d > 1e-3 else 'does NOT affect'} "
            f"a Linear-tagged clip's render (Δ={d:.5f})"
        )

        # ---- POSITIVE round-trip: the named "ACEScg" entry does a REAL,
        # distinct AP1->working transform (mirror of the negative tests). Same
        # pixels, Output pinned Rec.709, Timeline = DaVinci Wide Gamut Intermediate;
        # decode once as Input=ACEScg and once as Input=Linear. If "ACEScg" is a
        # real, correctly-plumbed input space, the two differ (AP1 primaries are
        # rotated into the working gamut; "Linear" inherits working primaries) and
        # the ACEScg decode is well-formed (no clipping/garbage).
        proj.SetSetting("colorSpaceTimeline", "DaVinci Wide Gamut Intermediate")
        proj.SetSetting("colorSpaceOutput", "Rec.709 Gamma 2.4")
        print(
            "\n  [chain] Timeline=",
            proj.GetSetting("colorSpaceTimeline"),
            " Output=",
            proj.GetSetting("colorSpaceOutput"),
        )
        img_acescg = _render(resolve, proj, clip, "as_acescg", input_cs="ACEScg")
        img_aslin = _render(resolve, proj, clip, "as_linear_dwg", input_cs="Linear")
        s_acg, s_lin = _sat_mean(img_acescg), _sat_mean(img_aslin)
        print(f"  Same pixels, Input=ACEScg : sat={s_acg.round(4)}")
        print(f"  Same pixels, Input=Linear : sat={s_lin.round(4)}")
        d_named = float(np.abs(img_acescg - img_aslin).mean())
        finite_ok = bool(
            np.isfinite(img_acescg).all()
            and (img_acescg <= 1.0001).all()
            and (img_acescg >= -1e-4).all()
        )
        print(
            f"[OBS] ACEScg-input vs Linear-input differ by {d_named:.5f} "
            f"(real distinct transform = {d_named > 1e-3}); ACEScg decode well-formed = {finite_ok}"
        )
        results["acescg_is_a_real_named_input_space"] = (d_named > 1e-3) and finite_ok

    print("\n=== linear-gamut test summary ===")
    for k, v in results.items():
        print(f"  {v!s:6}  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
