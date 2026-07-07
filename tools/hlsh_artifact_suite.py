"""Halo/pool artifact suite for the H/S translation (owner round-2 campaign).

Owner round-2 verdicts on the v2 LLF core: exposure HALOS in zones
neighbouring adjusted shadows; POOLS of adjustment around small bright
regions inside lifted dark regions (worst: the bright wall strip next to
the dark curtain — diag crop1); edge haloing on highlight recovery. This
suite makes each class a NUMBER, at op level (seconds per arm, no full
renders), plus a real-frame crop article rendered through the actual
pipeline against the LR export anchor.

ARTICLES (synthetic = neutral camera-RGB fields in scene-linear units;
construction truth, externally checkable):
  step        two textured plateaus (log2 -9 | -2), vertical edge.
  blob_bright bright discs (-1.5; radii 16/48/128 px) in a dark field
              (-9) — the crop1 "wall strip in curtain" class.
  blob_dark   dark discs in a bright field — the highlight-pool class.
  ramp        smooth 11.5-stop ramp + fine texture — gamma-grid banding.
  real        512px crops of DSC_4053 around the owner-flagged regions,
              rendered through the REAL pipeline stages (per-pixel post
              op, so a crop render equals the full render's crop) vs the
              LR export crop. NB the op itself sees crop context, not
              full-frame context — a bounded bias, noted; the winner is
              re-validated full-frame by cal_hlsh_fit.

METRICS
  halo_over/under  step: max signed deviation of near-edge band deltas
                   (2..64 px) from the same side's far-field delta, in
                   log2 stops (the v10 edge-band protocol, per side).
  pool             blobs: max |annulus-mean delta - far-field delta| over
                   annuli 1.2r..4r (the glow ring), per radius.
  interior_err     blobs: |disc-core mean delta - global-curve delta at
                   the disc's luminance| — a small bright region must be
                   treated as bright content, not inherit its region's
                   lift.
  banding          ramp: std of the column-mean delta's residual around
                   its 51-col smooth — gamma-discretization ripple.
  flatness         plateau texture-contrast retention out/in (the round-1
                   "flat look" guard; want ~1.0).
  real: de_mean vs the LR crop + zone_glow = |mean L* delta vs LR| in the
  bright zone (the wall that must NOT glow).

PRE-REGISTERED (2026-07-08, before any run):
  P1 'guided' absolute mode kills the wall glow (real zone_glow and blob
     pool <= half of 'gauss') at equal flatness (within 0.05).
  P2 'gauss' pools are WORST for blobs smaller than the residual scale
     (r16: interior_err large — the blob inherits the region lift).
  P3 'none'+tau=1 (pure global curve) = ~zero halos/pools and the worst
     flatness (round-1-class) — the reference extremes.
  P4 sigma_r has a secondary halo-vs-flatness trade.
  P5 banding is negligible (<0.02 st) for all arms at n_gamma=11 (the
     v10c coarse-grid finding).

Run:  python3 tools/hlsh_artifact_suite.py [--arms NAME,NAME] [--no-real]
Out:  tests/fixtures/evidence/hlsh_artifact_suite_2026-07-08.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

FIX = Path.home() / "lrt-cinema-fixtures"
ROUND2 = FIX / "production/calibration/round2"
RENDERS = ROUND2 / ".cal-renders"
DNG = FIX / "DSC_4053.dng"
DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"
)
CACHE = FIX / "hlsh-artifact-cache"
EVIDENCE = Path(__file__).resolve().parent.parent / (
    "tests/fixtures/evidence/hlsh_artifact_suite_2026-07-08.json"
)

SIZE = 1024
RNG_SEED = 20260708
BLOB_RADII = (16, 48, 128)
DARK, BRIGHT = -9.0, -1.5
# real-frame crop centres (4016-grid coords): the diag crop1 wall/curtain
# region (shadows case) + a window/interior boundary (highlights case,
# picked from the base render's bright-next-to-dark map).
REAL_CROPS = {"wall_curtain_S100": ((0.0, 100.0), None),
              "window_edge_H100": ((-100.0, 0.0), None),
              "stage_bottom_S100": ((0.0, 100.0), None)}
CROP = 1024


# --------------------------------------------------------------------------
# articles
# --------------------------------------------------------------------------

def _texture(shape: tuple[int, int], rng: np.ndarray, fine=0.25,
             folds=0.75) -> np.ndarray:
    """Log2-domain texture: 4-px-scale grain + low-frequency folds."""
    from scipy.ndimage import gaussian_filter
    g = gaussian_filter(rng.standard_normal(shape), 4.0)
    g *= fine / max(g.std(), 1e-9)
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    f = np.sin(xx / 97.0 + 0.7 * np.sin(yy / 141.0)) * folds
    return (g + f).astype(np.float32)


def _to_rgb(log_lum: np.ndarray) -> np.ndarray:
    lum = np.exp2(log_lum).astype(np.float32)
    return np.repeat(lum[..., None], 3, axis=-1)


def make_articles() -> dict:
    rng = np.random.default_rng(RNG_SEED)
    arts: dict = {}
    tex = _texture((SIZE, SIZE), rng)

    step = np.full((SIZE, SIZE), DARK, dtype=np.float32)
    step[:, SIZE // 2:] = BRIGHT
    arts["step"] = _to_rgb(step + tex)

    for name, lo, hi in (("blob_bright", DARK, BRIGHT),
                         ("blob_dark", BRIGHT, DARK)):
        field = np.full((SIZE, SIZE), lo, dtype=np.float32)
        yy, xx = np.mgrid[0:SIZE, 0:SIZE]
        centers = [(SIZE // 4, SIZE // 4), (SIZE // 2, 3 * SIZE // 4),
                   (3 * SIZE // 4, SIZE // 4)]
        for (cy, cx), r in zip(centers, BLOB_RADII, strict=True):
            field[np.hypot(yy - cy, xx - cx) <= r] = hi
        arts[name] = _to_rgb(field + tex * 0.4)
        arts[f"_{name}_centers"] = centers

    ramp = np.linspace(-12.0, -0.5, SIZE, dtype=np.float32)[None, :]
    arts["ramp"] = _to_rgb(np.broadcast_to(ramp, (SIZE, SIZE)).copy()
                           + tex * 0.15)
    return arts


# --------------------------------------------------------------------------
# metrics (all in log2-stop units on op in/out luminance)
# --------------------------------------------------------------------------

def _delta_map(rgb_in: np.ndarray, rgb_out: np.ndarray) -> np.ndarray:
    li = np.log2(np.maximum(rgb_in.mean(-1), 1e-6))
    lo = np.log2(np.maximum(rgb_out.mean(-1), 1e-6))
    return lo - li


def metric_step(rgb_in, rgb_out) -> dict:
    d = _delta_map(rgb_in, rgb_out)
    e = SIZE // 2
    out = {}
    for side, sl_far, sl_bands in (
            ("dark", np.s_[:, : e - 128], [np.s_[:, e - b - 8: e - b]
                                           for b in (2, 8, 24, 56)]),
            ("bright", np.s_[:, e + 128:], [np.s_[:, e + b: e + b + 8]
                                            for b in (2, 8, 24, 56)])):
        far = float(np.median(d[sl_far]))
        devs = [float(np.median(d[sl]) - far) for sl in sl_bands]
        out[f"halo_over_{side}"] = max(0.0, max(devs))
        out[f"halo_under_{side}"] = min(0.0, min(devs))
    return out


def metric_blob(rgb_in, rgb_out, centers, delta_fn) -> dict:
    d = _delta_map(rgb_in, rgb_out)
    li = np.log2(np.maximum(rgb_in.mean(-1), 1e-6))
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    far_mask = np.ones((SIZE, SIZE), bool)
    for (cy, cx), r in zip(centers, BLOB_RADII, strict=True):
        far_mask &= np.hypot(yy - cy, xx - cx) > 5 * r
    far = float(np.median(d[far_mask]))
    out = {"far_delta": far}
    for (cy, cx), r in zip(centers, BLOB_RADII, strict=True):
        rr = np.hypot(yy - cy, xx - cx)
        ring_devs = []
        for a0, a1 in ((1.2, 1.6), (1.6, 2.2), (2.2, 3.0), (3.0, 4.0)):
            m = (rr >= a0 * r) & (rr < a1 * r)
            ring_devs.append(float(np.median(d[m]) - far))
        out[f"pool_r{r}"] = float(max(abs(v) for v in ring_devs))
        core = rr <= 0.6 * r
        expected = float(np.mean(delta_fn(li[core])))
        out[f"interior_err_r{r}"] = float(abs(np.median(d[core]) - expected))
    return out


def metric_ramp(rgb_in, rgb_out) -> dict:
    from scipy.ndimage import uniform_filter1d
    d = _delta_map(rgb_in, rgb_out)
    col = d.mean(axis=0)
    smooth = uniform_filter1d(col, 51, mode="nearest")
    resid = col - smooth
    dd = np.diff(col)
    return {"banding": float(resid[64:-64].std()),
            "nonmonotone_frac": float((np.abs(dd) > 0.05).mean())}


def metric_flatness(rgb_in, rgb_out) -> dict:
    """Texture retention on the step article's plateau interiors."""
    from scipy.ndimage import uniform_filter
    li = np.log2(np.maximum(rgb_in.mean(-1), 1e-6))
    lo = np.log2(np.maximum(rgb_out.mean(-1), 1e-6))
    out = {}
    e = SIZE // 2
    for side, sl in (("dark", np.s_[128:-128, 128: e - 160]),
                     ("bright", np.s_[128:-128, e + 160: -128])):
        hi_in = (li - uniform_filter(li, 33))[sl]
        hi_out = (lo - uniform_filter(lo, 33))[sl]
        out[f"flatness_{side}"] = float(hi_out.std() / max(hi_in.std(), 1e-9))
    return out


_BAND_EDGES = (4, 8, 16, 32, 64, 128, 256)


def _band_stds(log_lum: np.ndarray, sl) -> list[float]:
    """Per-band structure amplitude: std of (box_s - box_2s) bandpass of
    log2 luminance, bands 4..256 px — the owner round-3 axis ("flattened
    and lacks contrast", "flattens and blurs"): a single-scale absolute
    map preserves fine texture but erases/blurs structure ABOVE the map
    scale; the round-2 flatness guard (33 px highpass) was blind to it."""
    from scipy.ndimage import uniform_filter
    stds = []
    for s in _BAND_EDGES[:-1]:
        band = (uniform_filter(log_lum, s) - uniform_filter(log_lum, 2 * s))
        stds.append(float(band[sl].std()))
    return stds


def metric_multiscale(rgb_in, rgb_out) -> dict:
    """Per-band contrast retention on the step article's DARK plateau (the
    lifted region under S+100 — the owner's curtain/stage complaint zone).
    ~1.0 per band = structure carried; << 1 at mid bands = the round-3
    'flattened + blurred' signature. NB an ideal LOCAL tone op DOES
    compress bands somewhat (the curve's slope) — judge vs the LR anchor
    per-band ratios on the real crops, not vs 1.0 alone."""
    li = np.log2(np.maximum(rgb_in.mean(-1), 1e-6))
    lo = np.log2(np.maximum(rgb_out.mean(-1), 1e-6))
    sl = np.s_[128:-128, 128: SIZE // 2 - 160]
    s_in = _band_stds(li, sl)
    s_out = _band_stds(lo, sl)
    return {f"band{b}px": float(o / max(i, 1e-9))
            for b, i, o in zip(_BAND_EDGES[:-1], s_in, s_out, strict=True)}


# --------------------------------------------------------------------------
# arms
# --------------------------------------------------------------------------

def arm_configs() -> dict[str, dict]:
    base = {}
    arms = {
        # the SHIPPED config exactly as pinned in scene_tone (no overrides)
        "shipped": {},
        "v2-gauss": {**base, "_LLF_ABS_MODE": "gauss", "_LLF_LAST_LEVEL": 6},
        "gauss-L4": {**base, "_LLF_ABS_MODE": "gauss", "_LLF_LAST_LEVEL": 4},
        "global-tau1": {**base, "_LLF_ABS_MODE": "none",
                        "_LLF_GLOBAL_BLEND": 1.0},
        # sigma_r 50 puts EVERYTHING in the identity detail arm -> LLF is a
        # no-op and the output is exactly x + delta(x): the zero-locality,
        # zero-halo reference (P3's true extreme).
        "pure-global": {**base, "_LLF_ABS_MODE": "none",
                        "_LLF_GLOBAL_BLEND": 1.0, "_LLF_SIGMA_R": 50.0},
        "none": {**base, "_LLF_ABS_MODE": "none"},
    }
    for eps in (2.0, 8.0, 32.0):
        for rad in (12, 24, 48):
            arms[f"guided-e{eps:g}-r{rad}"] = {
                **base, "_LLF_ABS_MODE": "guided",
                "_LLF_GUIDED_EPS": eps, "_LLF_GUIDED_RADIUS": rad}
    for tau in (0.25, 0.5):
        arms[f"guided-e8-r24-tau{tau:g}"] = {
            **base, "_LLF_ABS_MODE": "guided", "_LLF_GUIDED_EPS": 8.0,
            "_LLF_GUIDED_RADIUS": 24, "_LLF_GLOBAL_BLEND": tau}
    # round-2 arms: residual-scale sweep + the polarity guard
    for lvl in (2, 3, 5):
        arms[f"gauss-L{lvl}"] = {**base, "_LLF_ABS_MODE": "gauss",
                                 "_LLF_LAST_LEVEL": lvl}
    for lvl in (4, 6):
        for temp in (0.5, 1.0, 2.0):
            arms[f"guard-L{lvl}-t{temp:g}"] = {
                **base, "_LLF_ABS_MODE": "gauss_guard",
                "_LLF_LAST_LEVEL": lvl, "_LLF_GUARD_TEMP": temp}
    # round-3 arms: two-scale guard (features protected at 2^fine px,
    # sub-fine texture flat-immune) + sigma_r interaction at L4
    for fine in (2, 3):
        for temp in (0.5, 1.0):
            arms[f"guard2-L4-f{fine}-t{temp:g}"] = {
                **base, "_LLF_ABS_MODE": "gauss_guard",
                "_LLF_LAST_LEVEL": 4, "_LLF_GUARD_TEMP": temp,
                "_LLF_GUARD_FINE_LEVEL": fine}
    arms["guard2-L6-f2-t1"] = {
        **base, "_LLF_ABS_MODE": "gauss_guard", "_LLF_LAST_LEVEL": 6,
        "_LLF_GUARD_TEMP": 1.0, "_LLF_GUARD_FINE_LEVEL": 2}
    arms["gauss-L4-sr3"] = {**base, "_LLF_ABS_MODE": "gauss",
                            "_LLF_LAST_LEVEL": 4, "_LLF_SIGMA_R": 3.0}
    arms["gauss-L3-sr3"] = {**base, "_LLF_ABS_MODE": "gauss",
                            "_LLF_LAST_LEVEL": 3, "_LLF_SIGMA_R": 3.0}
    # round-4 arms: multi-scale absolute
    arms["multi-34"] = {**base, "_LLF_ABS_MODE": "gauss_multi",
                        "_LLF_MULTI_LEVELS": (3, 4), "_LLF_LAST_LEVEL": 6}
    arms["multi-345"] = {**base, "_LLF_ABS_MODE": "gauss_multi",
                         "_LLF_MULTI_LEVELS": (3, 4, 5),
                         "_LLF_LAST_LEVEL": 6}
    arms["multi-234"] = {**base, "_LLF_ABS_MODE": "gauss_multi",
                         "_LLF_MULTI_LEVELS": (2, 3, 4),
                         "_LLF_LAST_LEVEL": 6}
    # round-5 arms (owner round-3 "flattens AND blurs"): FULL-depth pyramid
    # — the canonical dt/LLF architecture. The residual collapses to a
    # near-global constant (no intermediate absolute map exists to pool,
    # blur, or flatten); the tone response is carried by the per-gamma
    # edge-arm slopes at EVERY level. Small sigma_r is the regime where
    # slopes carry tone (dt uses a small mid-tone window for exactly this).
    # Pre-registered (2026-07-08): F1 full-depth kills the mid-band
    # (16-128 px) contrast loss AND keeps pools/interior at the floor;
    # F2 the aggregate response WEAKENS as sigma_r grows (identity window
    # swallows deviations) — the refit re-steepens tables; F3 fine bands
    # (4-8 px) stay ~1.0 at sigma_r >= 0.5.
    for sr in (0.33, 0.66, 1.0, 2.0):
        arms[f"full-sr{sr:g}"] = {**base, "_LLF_ABS_MODE": "gauss",
                                  "_LLF_LAST_LEVEL": 99, "_LLF_SIGMA_R": sr}
    # round-6 arms: amplitude-gated two-scale map (tone follows the coarse
    # map where fine/coarse agree — folds keep contrast; follows the fine
    # map at strong divergences — no pools across boundaries).
    for fine in (3, 4):
        for lo, hi in ((0.4, 1.2), (0.6, 1.6), (1.0, 2.4)):
            arms[f"gate2-f{fine}-{lo:g}-{hi:g}"] = {
                **base, "_LLF_ABS_MODE": "gate2", "_LLF_LAST_LEVEL": 6,
                "_LLF_GATE_FINE": fine, "_LLF_GATE_LO": lo,
                "_LLF_GATE_HI": hi}
    arms["gate2-f4-L7"] = {
        **base, "_LLF_ABS_MODE": "gate2", "_LLF_LAST_LEVEL": 7,
        "_LLF_GATE_FINE": 4, "_LLF_GATE_LO": 0.6, "_LLF_GATE_HI": 1.6}
    return arms


SLIDERS = {"S100": (0.0, 100.0), "S50": (0.0, 50.0),
           "H100": (-100.0, 0.0), "H50": (-50.0, 0.0)}


# --------------------------------------------------------------------------
# real-frame crop article
# --------------------------------------------------------------------------

def _real_setup():
    """Decode once; cache the exposure-corrected camera RGB + crop coords."""
    import lrt_cinema.scene_tone as st  # noqa: F401 (arm overrides)
    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.interpolation import LR_LOCAL_EXPOSURE_SCALE
    from lrt_cinema.pipeline import (
        _decode_raw,
        kelvin_to_neutral,
        read_dcp_default_black_render,
        read_dng_baseline_exposure,
    )
    from lrt_cinema.xmp_parser import parse_xmp_file
    profile = parse_dcp(DCP)
    dbr = read_dcp_default_black_render(DCP)
    base_be = read_dng_baseline_exposure(DNG)
    ops0, _kf, _dfk, _r, mo = parse_xmp_file(ROUND2 / "CALHIM50_4053.xmp")
    scene_ev = LR_LOCAL_EXPOSURE_SCALE * sum(e for _k, e in mo)
    kelvin = float(ops0.temperature_k)
    asn = kelvin_to_neutral(profile, kelvin, float(ops0.tint or 0.0))
    cam_npy = CACHE / "cam_ev.npy"
    if cam_npy.exists():
        cam_ev = np.load(cam_npy)
    else:
        CACHE.mkdir(parents=True, exist_ok=True)
        cam, _, _ = _decode_raw(DNG, demosaic="linear", wb_asn=asn)
        cam_ev = cam * np.float32(2.0 ** scene_ev)
        np.save(cam_npy, cam_ev)
    return profile, dbr, base_be, ops0, kelvin, asn, cam_ev


def _find_real_crops(cam_ev: np.ndarray) -> dict[str, tuple[int, int]]:
    """Deterministic crop centres (4016-grid coords): the strongest
    dark-next-to-bright neighbourhoods, shadow-case and highlight-case."""
    from scipy.ndimage import uniform_filter
    lum = cam_ev.mean(-1)[8:-8, 8:-8]
    l2 = np.log2(np.maximum(lum, 1e-6))
    dark = (l2 < -8.0).astype(np.float32)
    bright = (l2 > -3.0).astype(np.float32)
    mix = uniform_filter(dark, 257) * uniform_filter(bright, 257)
    picks = {}
    s = mix.copy()
    s[:CROP // 2 + 8, :] = -1
    s[-(CROP // 2 + 8):, :] = -1
    s[:, :CROP // 2 + 8] = -1
    s[:, -(CROP // 2 + 8):] = -1
    j, i = np.unravel_index(np.argmax(s), s.shape)
    picks["wall_curtain_S100"] = (int(j), int(i))
    picks["window_edge_H100"] = (int(j), int(i))  # same geometry, H slider
    # owner round-3: worst shadow artifacts at the BOTTOM of the gym frame
    # (curtain + stage) — pick the strongest dark-dominant neighbourhood in
    # the bottom third.
    s2 = uniform_filter(dark, 257).copy()
    s2[: 2 * s2.shape[0] // 3, :] = -1
    s2[-(CROP // 2 + 8):, :] = -1
    s2[:, :CROP // 2 + 8] = -1
    s2[:, -(CROP // 2 + 8):] = -1
    j2, i2 = np.unravel_index(np.argmax(s2), s2.shape)
    picks["stage_bottom_S100"] = (int(j2), int(i2))
    return picks


def eval_real(arm_over: dict, setup, crops, lr_cache) -> dict:
    import importlib.util

    import lrt_cinema.scene_tone as st
    from lrt_cinema.develop_ops import apply_develop_ops
    from lrt_cinema.ir import RenderIntent
    from lrt_cinema.pipeline import apply_adobe_pipeline
    spec = importlib.util.spec_from_file_location(
        "cal_domain_round2",
        Path(__file__).resolve().parent / "cal_domain_round2.py")
    cdr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cdr)

    profile, dbr, base_be, ops0, kelvin, asn, cam_ev = setup
    from dataclasses import replace
    base_ops = replace(
        ops0, highlights=0.0, shadows=0.0, whites=0.0, contrast=0.0,
        blacks=0.0, scene_exposure_ev=0.0,
        hsl=type(ops0.hsl)(), color_grade=type(ops0.color_grade)())

    out = {}
    for name, ((h, s), _) in REAL_CROPS.items():
        probe = "CALSH100" if s else "CALHIM100"
        j, i = crops[name]
        cam_crop = cam_ev[j + 8 - CROP // 2: j + 8 + CROP // 2,
                          i + 8 - CROP // 2: i + 8 + CROP // 2]
        cam2 = st.apply_scene_hlsh(np.ascontiguousarray(cam_crop), h, s)
        pp = apply_adobe_pipeline(
            camera_rgb=cam2, profile=profile, as_shot_neutral=asn,
            scene_kelvin=kelvin, dng_baseline_exposure=base_be,
            default_black_render=dbr, stop_after_stage=9)
        pp = apply_develop_ops(pp, base_ops, RenderIntent.FAITHFUL,
                               master_look="bake", capture_sharpen="off")
        from lrt_cinema.output import _prophoto_to_display
        enc = _prophoto_to_display(pp, "srgb")
        if probe not in lr_cache:
            import tifffile
            lr_cache[probe] = tifffile.imread(
                str(ROUND2 / f"{probe}_4053.tif")).astype(np.float32) / 65535.0
        lr_crop = lr_cache[probe][j - CROP // 2: j + CROP // 2,
                                  i - CROP // 2: i + CROP // 2]
        import colour
        ours_lin = colour.models.eotf_sRGB(enc)
        lr_lin = colour.models.eotf_sRGB(lr_crop)
        m = cdr._metrics(ours_lin.reshape(CROP, CROP, 3),
                         lr_lin.reshape(CROP, CROP, 3))
        # zone glow: bright-zone L* offset vs LR (the wall must not glow)
        bl = ours_lin @ cdr._LUM_W
        base_zone = (colour.models.eotf_sRGB(lr_crop) @ cdr._LUM_W) > 0.15
        lab_o, lab_l = cdr._lab(ours_lin), cdr._lab(lr_lin)
        glow = float((lab_o[..., 0] - lab_l[..., 0])[base_zone].mean()) \
            if base_zone.sum() > 500 else float("nan")
        # per-band contrast retention vs the LR anchor (the round-3 axis):
        # ratio of band std ours/LR on display log2 luminance, full crop
        lo_o = np.log2(np.maximum(ours_lin @ cdr._LUM_W, 1e-6))
        lo_l = np.log2(np.maximum(lr_lin @ cdr._LUM_W, 1e-6))
        sl = np.s_[32:-32, 32:-32]
        bands = {f"band{b}px_vs_lr": float(o / max(li_, 1e-9))
                 for b, o, li_ in zip(_BAND_EDGES[:-1],
                                      _band_stds(lo_o, sl),
                                      _band_stds(lo_l, sl), strict=True)}
        out[name] = {"de_mean": m["de_mean"], "dL_mean": m["dL_mean"],
                     "zone_glow_Lstar": glow,
                     "crop_center": [int(j), int(i)], **bands}
        del bl
    return out


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default=None,
                    help="comma-separated arm names (default: all)")
    ap.add_argument("--no-real", action="store_true")
    args = ap.parse_args()

    import lrt_cinema.scene_tone as st

    arts = make_articles()
    arms = arm_configs()
    if args.arms:
        arms = {k: v for k, v in arms.items() if k in args.arms.split(",")}

    setup = crops = None
    lr_cache: dict = {}
    if not args.no_real:
        setup = _real_setup()
        crops = _find_real_crops(setup[-1])

    results: dict = {"pre_registered": "P1-P5 (module docstring)",
                     "arms": {}}
    if EVIDENCE.exists():
        results = json.loads(EVIDENCE.read_text())
        results.setdefault("arms", {})
    defaults = {k: getattr(st, k) for k in (
        "_LLF_ABS_MODE", "_LLF_LAST_LEVEL", "_LLF_GUIDED_EPS",
        "_LLF_GUIDED_RADIUS", "_LLF_GLOBAL_BLEND", "_LLF_SIGMA_R",
        "_LLF_GUARD_TEMP", "_LLF_GUARD_FINE_LEVEL", "_LLF_MULTI_LEVELS",
        "_LLF_GATE_FINE", "_LLF_GATE_LO", "_LLF_GATE_HI")}

    for arm_name, over in arms.items():
        for k, v in defaults.items():
            setattr(st, k, over.get(k, v))
        row: dict = {"config": over}
        for sl_name, (h, s) in SLIDERS.items():
            def dfn(b, h=h, s=s):
                return st._hlsh_delta(np.asarray(b, dtype=np.float64), h, s)
            sm: dict = {}
            o = st.apply_scene_hlsh(arts["step"], h, s)
            sm.update(metric_step(arts["step"], o))
            sm.update(metric_flatness(arts["step"], o))
            sm.update(metric_multiscale(arts["step"], o))
            blob_art = "blob_bright" if s else "blob_dark"
            o = st.apply_scene_hlsh(arts[blob_art], h, s)
            sm.update(metric_blob(arts[blob_art], o,
                                  arts[f"_{blob_art}_centers"], dfn))
            o = st.apply_scene_hlsh(arts["ramp"], h, s)
            sm.update(metric_ramp(arts["ramp"], o))
            row[sl_name] = sm
        if not args.no_real:
            row["real"] = eval_real(over, setup, crops, lr_cache)
        results["arms"][arm_name] = row
        r = row["S100"]
        line = (f"{arm_name:22s} S100 pool_r48 {r['pool_r48']:+.3f} "
                f"intr_r16 {r['interior_err_r16']:.3f} "
                f"halo_ov_br {r['halo_over_bright']:+.3f} "
                f"flat_dk {r['flatness_dark']:.2f}")
        if "real" in row:
            rr = row["real"]["wall_curtain_S100"]
            line += (f" | real ΔE {rr['de_mean']:.3f} "
                     f"glow {rr['zone_glow_Lstar']:+.2f}")
        print(line)

    for k, v in defaults.items():
        setattr(st, k, v)
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(results, indent=1))
    print(f"\nevidence -> {EVIDENCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
