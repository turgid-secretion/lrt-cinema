"""Segbased hot-pixel investigation — forensics + remedy arms (owner-
directed 2026-07-07).

FORENSIC FINDING (this probe, direct evidence): the owner's "random hot
pixels" in the segb arms are INPUT-LEVEL specs, not reconstruction
inventions — at every one of the top-15 interior luma impulses in the D
render, the reconstructed mosaic equals the INPUT mosaic (the candidate
write-back's max(in, ·) floor keeps pre-existing spec values). Two
sub-classes: (a) true sensor hot pixels in unclipped areas (e.g. 0.78
among ~0.14 neighbours), mostly adjacent PAIRS — which is why dt's
hotpixels stage in strict mode can never fire (pairs are what its
`permissive` toggle exists for) and why running it AFTER CA also fails
(CA's bilinear resampling smears single-site specs across neighbours;
RT's canon order — bad pixels BEFORE CA — is the working placement);
(b) isolated per-channel-ceiling sites in partial-clip zones, which the
CLIP arm hides by clamping to common white and headroom paths expose.

METRIC HONESTY: the interior luma-impulse census (|L − med3×3| over the
dilated clip region) does NOT resolve this artifact — it counts all
recovered fine structure, and every remedy arm measured within ±25 % of
the baseline (median-replacement guard measurably WORSE: 48→129 — it
created dark divots; the shipped guard is therefore a CLAMP). The
deciding instrument is the owner flip; the numbers recorded here are
the spot-coverage forensics + the safety guards.

ARMS (gym, full production intent, menon + fc3, flips D/G/H):
  D  segb → CA                                   — baseline (shipped arm)
  G  segb(site_guard=2.0) → CA                   — reconstruction guard
  H  hotpix(1.0, permissive) → segb(guard) → CA  — input-spec remedy,
                                                   RT placement (pre-CA)

SAFETY GATES (pre-registered):
  G3: truth-harness rel_mae (band-clip, W=0.6) regression < 2 % relative
      for every arm — remedies must not undo real recovery.
  H2: hotpix(1.0, permissive) false-positive census on the CA-free
      bars/zoneplate article mosaics stays ~0.

Run:  python3 tools/segbased_guard_probe.py
Out:  tests/fixtures/evidence/segbased_guard_<today>.json
      ~/lrt-cinema-fixtures/verify-2026-07-07/ca-flip/ (G + H arms)
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

FIX = Path.home() / "lrt-cinema-fixtures"
GYM_DNG = FIX / "DSC_4053.dng"
GYM_XMP = FIX / "production" / "xmp" / "DSC_4053.xmp"
DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"
)
EVIDENCE = REPO / ("tests/fixtures/evidence/"
                   f"segbased_guard_{_dt.date.today().isoformat()}.json")
FLIPDIR = FIX / "verify-2026-07-07" / "ca-flip"
GUARD = 2.0
HOTPIX = dict(strength=1.0, permissive=True)
SYN_WHITE = 0.6


def main() -> int:
    import rawpy
    from PIL import Image
    from scipy.ndimage import binary_dilation, median_filter

    from lrt_cinema._ca_correct import ca_correct_mosaic
    from lrt_cinema._fc_suppress import suppress_false_colour
    from lrt_cinema._hotpixels import fix_hot_pixels
    from lrt_cinema._segbased_reconstruct import (
        _CLIP_MAGIC,
        _suppress_isolated_sites,
        reconstruct_mosaic_segbased,
    )
    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.develop_ops import apply_develop_ops
    from lrt_cinema.interpolation import LR_LOCAL_EXPOSURE_SCALE
    from lrt_cinema.ir import RenderIntent
    from lrt_cinema.output import _prophoto_to_display
    from lrt_cinema.pipeline import (
        _extract_cfa,
        _mosaic_clip_mask,
        _wb_mul_from_asn,
        apply_adobe_pipeline,
        kelvin_to_neutral,
        read_dcp_default_black_render,
        read_dng_baseline_exposure,
    )
    from lrt_cinema.xmp_parser import parse_xmp_file

    profile = parse_dcp(DCP)
    ops, _kf, _dfk, _rating, mask_offsets = parse_xmp_file(GYM_XMP)
    ops = replace(ops, scene_exposure_ev=LR_LOCAL_EXPOSURE_SCALE
                  * sum(ev for _k, ev in mask_offsets))
    kelvin = float(ops.temperature_k)
    asn = kelvin_to_neutral(profile, kelvin, float(ops.tint or 0.0))
    wb = _wb_mul_from_asn(asn)
    dng_be = read_dng_baseline_exposure(GYM_DNG)
    dbr = read_dcp_default_black_render(DCP)

    with rawpy.imread(str(GYM_DNG)) as raw:
        cfa, pattern = _extract_cfa(raw)
        colors = raw.raw_colors_visible
        clip_mask = _mosaic_clip_mask(raw)
    h, w = cfa.shape
    chan = np.where(colors[:h, :w] == 3, 1, colors[:h, :w])
    scaled = cfa * wb[chan].astype(np.float32)
    anyclip = clip_mask.any(axis=-1)
    interior = binary_dilation(anyclip, iterations=2)[8:-8, 8:-8]

    results: dict = {
        "design": "input-spec forensics + remedy arms D/G/H (docstring)",
        "guard_limit": GUARD, "hotpix": HOTPIX,
        "census_grounding": {"clipped_sites": 95664, "ratio_p50": 1.0,
                             "tail_gt2x": 647, "ratio_max": 5.79},
    }

    def render(m: np.ndarray) -> np.ndarray:
        from colour_demosaicing import demosaicing_CFA_Bayer_Menon2007
        m = ca_correct_mosaic(m, pattern, iterations=2, avoid_shift=True)
        cam = np.maximum(np.asarray(
            demosaicing_CFA_Bayer_Menon2007(m, pattern), np.float32), 0.0)
        cam = suppress_false_colour(cam, passes=3, blur=True)
        ev = ops.scene_exposure_ev + ops.exposure_ev
        if ev:
            cam = cam * np.float32(2.0 ** ev)
        pp = apply_adobe_pipeline(cam, profile, asn, kelvin, dng_be, dbr, 9)
        pp = apply_develop_ops(pp, ops, RenderIntent.FAITHFUL,
                               master_look="bake", capture_sharpen="off")
        return (np.clip(_prophoto_to_display(pp, "srgb"), 0, 1)
                * 255 + 0.5).astype(np.uint8)[8:-8, 8:-8]

    def luma_impulse(s8: np.ndarray) -> np.ndarray:
        import colour
        rgb = s8.astype(np.float64) / 255.0
        lum = colour.XYZ_to_Lab(
            colour.RGB_to_XYZ(rgb, colourspace="sRGB",
                              apply_cctf_decoding=True),
            illuminant=np.array([0.3127, 0.3290]))[..., 0]
        return np.abs(lum - median_filter(lum, size=3))

    # ---- D baseline + forensics ----------------------------------------------
    recon_plain = reconstruct_mosaic_segbased(scaled, chan, wb)
    d8 = render(recon_plain)
    imp = luma_impulse(d8)
    ii = interior[:imp.shape[0], :imp.shape[1]]
    top = np.argsort(np.where(ii, imp, 0.0).ravel())[::-1][:15]
    spots = [tuple(int(v) + 8 for v in np.unravel_index(f, imp.shape))
             for f in top]
    forensic = []
    for my, mx in spots:
        win = np.s_[max(0, my - 2):my + 3, max(0, mx - 2):mx + 3]
        forensic.append({
            "mosaic_yx": [my, mx],
            "recon_eq_input": bool(np.allclose(recon_plain[win],
                                               scaled[win])),
            "input_max": round(float(scaled[win].max()), 3),
            "anyclip": bool(anyclip[my, mx]),
        })
    results["forensics_top15"] = forensic
    n_input_level = sum(f["recon_eq_input"] for f in forensic)
    print(f"forensics: {n_input_level}/15 top impulses are INPUT-level "
          f"(recon == input in the 5x5)")

    # hotpix spot coverage at the working placement (pre-recon)
    hp, n_hp = fix_hot_pixels(scaled, **HOTPIX)
    changed = hp != scaled
    cover = sum(bool(changed[max(0, y - 2):y + 3, max(0, x - 2):x + 3].any())
                for y, x in spots)
    results["hotpix_pre_recon"] = {"fires": n_hp, "spot_coverage_of_15": cover}
    print(f"hotpix(1.0, permissive) pre-recon: {n_hp} fires, "
          f"covers {cover}/15 impulse spots")

    # guard fire count (G1-class)
    clips = (float(_CLIP_MAGIC) * wb).astype(np.float32)
    site_clipped = scaled > clips[chan]
    _g, n_guard = _suppress_isolated_sites(recon_plain, site_clipped, GUARD)
    results["guard_fires"] = n_guard

    # ---- safety gates ---------------------------------------------------------
    truth_b = (cfa / SYN_WHITE) * wb[chan]
    clamped_b = (np.minimum(cfa / SYN_WHITE, 1.0) * wb[chan]).astype(np.float32)
    band = (cfa >= SYN_WHITE) & (cfa < 0.99)
    th = {}
    for nm, pre_hp, kw in (("segb", False, {}),
                           ("segb_guard", False, {"site_guard": GUARD}),
                           ("hotpix_segb_guard", True, {"site_guard": GUARD})):
        src = fix_hot_pixels(clamped_b, **HOTPIX)[0] if pre_hp else clamped_b
        rec = reconstruct_mosaic_segbased(src, chan, wb, **kw)
        err = (rec - truth_b) / np.maximum(truth_b, 1e-6)
        th[nm] = {"rel_mae": float(np.abs(err[band]).mean())}
        print(f"truth {nm:18s}: rel_mae={th[nm]['rel_mae']:.4f}")
    results["truth_harness"] = th

    art = FIX / "test-articles"
    fp = {}
    for name in ("bars", "zoneplate"):
        with rawpy.imread(str(art / f"{name}.dng")) as r:
            acfa, _p = _extract_cfa(r)
            acolors = r.raw_colors_visible
        ah, aw = acfa.shape
        achan = np.where(acolors[:ah, :aw] == 3, 1, acolors[:ah, :aw])
        cond = np.minimum(acfa * wb[achan].astype(np.float32),
                          np.float32(wb.min()))
        fp[name] = fix_hot_pixels(cond, **HOTPIX)[1]
        print(f"hotpix(1.0, permissive) false positives {name}: {fp[name]}")
    results["hotpix_false_positive_census"] = fp

    # ---- remedy arms + flips (census recorded, flips decide) ------------------
    FLIPDIR.mkdir(parents=True, exist_ok=True)
    arms = {
        "D-segb-ca-on": d8,
        "G-segb-guarded": render(reconstruct_mosaic_segbased(
            scaled, chan, wb, site_guard=GUARD)),
        "H-segb-hotpix-guard": render(reconstruct_mosaic_segbased(
            hp, chan, wb, site_guard=GUARD)),
    }
    for tag, s8 in arms.items():
        m = luma_impulse(s8)
        mi = interior[:m.shape[0], :m.shape[1]]
        results.setdefault("interior_census_nonresolving", {})[tag] = {
            "gt5": int((m[mi] > 5).sum()), "gt8": int((m[mi] > 8).sum()),
            "gt12": int((m[mi] > 12).sum())}
        print(f"{tag}: {results['interior_census_nonresolving'][tag]}")
        Image.fromarray(s8).save(FLIPDIR / f"DSC_4053_intent_fc3_{tag}.png")
    with (FLIPDIR / "README.txt").open("a") as f:
        f.write(
            "\nG-segb-guarded: D + the isolated-site guard (site_guard 2.0,\n"
            "a CLAMP to 2x the same-channel ring median; only clipped —\n"
            "reconstruction-owned — sites can be touched).\n"
            "H-segb-hotpix-guard: dt-hotpixels (strength 1.0, permissive —\n"
            "pairs) run BEFORE reconstruction/CA (RT's bad-pixels-first\n"
            "order; running it after CA fails: CA smears the specs), then\n"
            "segbased with the guard. Forensics: the specs are INPUT-level\n"
            "(sensor hot pixels + partial-clip singles) that the clip arm\n"
            "hides by clamping — H is the remedy candidate for them.\n"
            "JUDGE D vs G vs H: which kills the random hot pixels without\n"
            "damaging recovered structure? (The impulse census cannot\n"
            "resolve this — your eyes decide.)\n")
    EVIDENCE.write_text(json.dumps(results, indent=1))
    print(f"evidence -> {EVIDENCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
