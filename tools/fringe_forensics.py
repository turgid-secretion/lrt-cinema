"""Fringe forensics — make the owner's edge-fringe observation deterministic.

Observation (owner, 2026-06-10, native-res flips): F/G (ours, menon, fixed)
show HIGHLY saturated pixels at highlight-to-clip boundaries; D (fresh LR
Classic render of the same XMP) does not. LR applied ZERO CA/defringe
correction (census), so D's cleanliness is rendering behaviour, not lens
correction. Question: is our fringe a clip-boundary hue error (the
partial-clip class — Adobe reconstructs there, we don't), a demosaic-
algorithm artifact, or something else?

Three phases, each emitting checked-in evidence:

  1. LOCALIZE  — registered D vs F, per-pixel linear saturation delta →
                 fringe mask + top clusters. Objective definition of "the
                 fringe pixels".
  2. RAW CENSUS — on the mosaic (pre-everything), what fraction of fringe
                 pixels sit within r sites of a clipped (≥0.97×white) CFA
                 site, vs a luminance-matched control? Tests the partial-
                 clip association without rendering anything.
  3. CAUSAL ARMS — window renders at the top cluster (CFA-phase-aligned,
                 full production chain):
                   menon-fixed      (≡ F, sanity)
                   rcd-fixed        (algorithm swap — same conditioning)
                   bilinear-fixed   (non-directional control)
                   menon-clipneutral(diagnostic: hue-neutralize pixels in
                                     the dilated mosaic clip mask AFTER
                                     demosaic — if the fringe dies, the
                                     mechanism IS clip-adjacent hue error)
                 Native 1:1 crops per arm for the owner + per-arm fringe
                 saturation metrics.

Run:  python3 tools/fringe_forensics.py
Out:  ~/lrt-cinema-fixtures/verify-2026-06-10/fringe/   (1:1 crops)
      tests/fixtures/evidence/fringe_forensics_2026-06-10.json
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np

FIX = Path.home() / "lrt-cinema-fixtures"
FLIP = FIX / "verify-2026-06-10/flip"
OUT = FIX / "verify-2026-06-10/fringe"
DNG = FIX / "DSC_4053.dng"
XMP = FIX / "production/xmp/DSC_4053.xmp"
DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"
)
EVIDENCE = Path(__file__).resolve().parent.parent / (
    "tests/fixtures/evidence/fringe_forensics_2026-06-10.json"
)
B = 8                 # flip grid (4016×6016) → ours/CFA grid (4032×6032)
SAT_DELTA = 0.15      # fringe gate: sat(F) − sat(D)
LUM_GATE = 0.10       # linear luminance floor (fringes live near highlights)
CLIP_T = 0.97         # mosaic clip threshold (×white, post black-subtract)
K_BLOCK = 64          # cluster block size
N_TOP = 4             # clusters to window-render
WIN = 384             # window crop (1:1 px)
MARGIN = 96           # demosaic margin around the window


def _lin(img8: np.ndarray) -> np.ndarray:
    import colour
    return colour.models.eotf_sRGB(img8.astype(np.float64) / 255.0)


def _sat(lin: np.ndarray) -> np.ndarray:
    mx = lin.max(axis=-1)
    mn = lin.min(axis=-1)
    return np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0.0)


def _phase1_localize() -> tuple[np.ndarray, list[tuple[int, int]], dict]:
    from PIL import Image
    D = np.asarray(Image.open(FLIP / "LRT_00001_D-lr-classic.png"))
    F = np.asarray(Image.open(FLIP / "LRT_00001_F-menon-fixed.png"))
    assert D.shape == F.shape, (D.shape, F.shape)
    linD, linF = _lin(D), _lin(F)
    mask = (
        (_sat(linF) - _sat(linD) > SAT_DELTA)
        & (linF.mean(axis=-1) > LUM_GATE)
    )
    h, w = mask.shape
    hb, wb = h // K_BLOCK, w // K_BLOCK
    blocks = mask[: hb * K_BLOCK, : wb * K_BLOCK].reshape(
        hb, K_BLOCK, wb, K_BLOCK).sum(axis=(1, 3))
    order = np.argsort(blocks.ravel())[::-1][:N_TOP]
    tops = [(int(i // wb) * K_BLOCK + K_BLOCK // 2,
             int(i % wb) * K_BLOCK + K_BLOCK // 2) for i in order]
    stats = {
        "fringe_px": int(mask.sum()),
        "fringe_frac_pct": float(mask.mean() * 100),
        "top_clusters_yx_flipgrid": tops,
        "top_cluster_px": [int(blocks.ravel()[i]) for i in order],
        "gates": {"sat_delta": SAT_DELTA, "lum": LUM_GATE},
    }
    print(f"phase1: {stats['fringe_px']} fringe px "
          f"({stats['fringe_frac_pct']:.4f}%), top clusters {tops}")
    return mask, tops, stats


def _phase2_raw_census(mask: np.ndarray) -> dict:
    import rawpy
    from scipy.ndimage import binary_dilation
    with rawpy.imread(str(DNG)) as raw:
        cfa = raw.raw_image_visible.astype(np.float32)
        colors = raw.raw_colors_visible
        black = np.asarray(raw.black_level_per_channel, np.float32)[colors]
        white = float(raw.white_level)
        norm = (cfa - black) / (white - black)
    clip = norm >= CLIP_T
    h, w = mask.shape
    # flip grid (y,x) → CFA grid (+B, +B); CFA is 4032×6032 (even-cropped)
    out = {"clip_threshold": CLIP_T, "clip_sites_pct": float(clip.mean() * 100)}
    fringe_yx = np.argwhere(mask) + B
    rng = np.random.default_rng(20260610)
    ctrl_yx = np.column_stack([
        rng.integers(B, h + B, size=len(fringe_yx)),
        rng.integers(B, w + B, size=len(fringe_yx)),
    ])
    for r in (1, 2, 4):
        near = binary_dilation(clip, iterations=r)
        f = float(near[fringe_yx[:, 0], fringe_yx[:, 1]].mean() * 100)
        c = float(near[ctrl_yx[:, 0], ctrl_yx[:, 1]].mean() * 100)
        out[f"fringe_within_{r}_of_clip_pct"] = f
        out[f"control_within_{r}_of_clip_pct"] = c
        print(f"phase2 r={r}: fringe {f:.1f}% clip-adjacent vs control {c:.1f}%")
    return out


def _window_arms(tops: list[tuple[int, int]], mask: np.ndarray) -> dict:
    import rawpy
    from PIL import Image
    from scipy.ndimage import binary_dilation

    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.develop_ops import apply_develop_ops
    from lrt_cinema.interpolation import LR_LOCAL_EXPOSURE_SCALE
    from lrt_cinema.ir import RenderIntent
    from lrt_cinema.output import _prophoto_to_display
    from lrt_cinema.pipeline import (
        _extract_cfa,
        _wb_mul_from_asn,
        apply_adobe_pipeline,
        kelvin_to_neutral,
        read_dcp_default_black_render,
        read_dng_baseline_exposure,
    )
    from lrt_cinema.xmp_parser import parse_xmp_file

    OUT.mkdir(parents=True, exist_ok=True)
    profile = parse_dcp(DCP)
    ops, _kf, _dfk, _r, mask_offsets = parse_xmp_file(XMP)
    ops = replace(ops, scene_exposure_ev=LR_LOCAL_EXPOSURE_SCALE
                  * sum(ev for _k, ev in mask_offsets))
    asn = kelvin_to_neutral(profile, float(ops.temperature_k),
                            float(ops.tint or 0.0))
    wb_mul = _wb_mul_from_asn(asn)
    dng_be = read_dng_baseline_exposure(DNG)
    dbr = read_dcp_default_black_render(DCP)

    with rawpy.imread(str(DNG)) as raw:
        cfa, pattern = _extract_cfa(raw)
        colors = raw.raw_colors_visible
    h, w = cfa.shape
    chan = np.where(colors[:h, :w] == 3, 1, colors[:h, :w])
    gainmap = wb_mul[chan].astype(np.float32)
    clipmask = cfa >= CLIP_T  # mosaic clip BEFORE any scaling

    def demosaic(name: str, sub: np.ndarray, pat: str) -> np.ndarray:
        if name == "menon":
            from colour_demosaicing import demosaicing_CFA_Bayer_Menon2007 as f
        elif name == "bilinear":
            from colour_demosaicing import demosaicing_CFA_Bayer_bilinear as f
        else:
            from lrt_cinema import accel
            return accel.rcd_demosaic(sub, pat)
        return np.maximum(np.asarray(f(sub, pat), np.float32), 0.0)

    results: dict = {"window": WIN, "margin": MARGIN, "arms": {}}
    arms = ("menon-fixed", "rcd-fixed", "bilinear-fixed",
            "menon-clipneutral", "menon-chromamed")
    for ci, (fy, fx) in enumerate(tops):
        # flip grid → CFA grid, snapped EVEN to preserve the Bayer phase
        cy = ((fy + B - WIN // 2 - MARGIN) // 2) * 2
        cx = ((fx + B - WIN // 2 - MARGIN) // 2) * 2
        cy = max(0, min(cy, h - WIN - 2 * MARGIN))
        cx = max(0, min(cx, w - WIN - 2 * MARGIN))
        sub = cfa[cy:cy + WIN + 2 * MARGIN, cx:cx + WIN + 2 * MARGIN]
        subgain = gainmap[cy:cy + WIN + 2 * MARGIN, cx:cx + WIN + 2 * MARGIN]
        subclip = clipmask[cy:cy + WIN + 2 * MARGIN, cx:cx + WIN + 2 * MARGIN]
        # the fringe mask for THIS window (flip-grid coords of the window)
        fmask = mask[cy - B + MARGIN:cy - B + MARGIN + WIN,
                     cx - B + MARGIN:cx - B + MARGIN + WIN]
        for arm in arms:
            algo = arm.split("-")[0]
            rgb = demosaic(algo, sub * subgain, pattern) / wb_mul[None, None, :]
            if arm == "menon-clipneutral":
                # Diagnostic ONLY (not a fix): hue-neutralize every pixel in
                # the 2-dilated mosaic clip mask — if the fringe dies here,
                # the mechanism is clip-adjacent hue error, full stop.
                # "Neutral" must be neutral AFTER Stage-2 WB, i.e. ∝ asn in
                # unbalanced camera space (first version set all channels
                # equal pre-WB — the very domain-error class under test —
                # and measurably WORSENED the fringe; kept in evidence r1).
                nm = binary_dilation(subclip, iterations=2)
                mx = rgb.max(axis=-1, keepdims=True)
                neutral = mx * (asn / float(asn.max()))[None, None, :]
                rgb = np.where(nm[..., None], neutral.astype(rgb.dtype), rgb)
            if arm == "menon-chromamed":
                # Canonical false-colour-suppression class (dcraw -m /
                # libraw FBDD / RT false-colour steps; ACR bakes an
                # equivalent into its demosaic): median the chroma
                # differences R−G / B−G, keep G. Diagnostic for "the fringe
                # is demosaic false colour that other engines suppress".
                from scipy.ndimage import median_filter
                for _ in range(2):
                    g = rgb[..., 1]
                    rgb = np.stack([
                        g + median_filter(rgb[..., 0] - g, size=3),
                        g,
                        g + median_filter(rgb[..., 2] - g, size=3),
                    ], axis=-1)
            pp = apply_adobe_pipeline(
                camera_rgb=rgb * np.float32(2.0 ** ops.scene_exposure_ev),
                profile=profile, as_shot_neutral=asn,
                scene_kelvin=float(ops.temperature_k),
                dng_baseline_exposure=dng_be, default_black_render=dbr,
                stop_after_stage=9)
            pp = apply_develop_ops(pp, ops, RenderIntent.FAITHFUL,
                                   master_look="bake", capture_sharpen="off")
            srgb8 = (np.clip(_prophoto_to_display(pp, "srgb"), 0, 1)
                     * 255 + 0.5).astype(np.uint8)
            crop = srgb8[MARGIN:MARGIN + WIN, MARGIN:MARGIN + WIN]
            Image.fromarray(crop).save(OUT / f"c{ci}_{arm}.png")
            sat_in_fringe = (float(_sat(_lin(crop))[fmask].mean())
                             if fmask.any() else None)
            results["arms"].setdefault(arm, []).append(sat_in_fringe)
        # reference crops from the existing full renders (same window)
        from PIL import Image as I
        for ref in ("D-lr-classic", "F-menon-fixed"):
            full = np.asarray(I.open(FLIP / f"LRT_00001_{ref}.png"))
            crop = full[cy - B + MARGIN:cy - B + MARGIN + WIN,
                        cx - B + MARGIN:cx - B + MARGIN + WIN]
            I.fromarray(crop).save(OUT / f"c{ci}_ref-{ref}.png")
            results["arms"].setdefault(f"ref-{ref}", []).append(
                float(_sat(_lin(crop))[fmask].mean()) if fmask.any() else None)
        print(f"cluster {ci} @flip({fy},{fx}): arms rendered")
    return results


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    mask, tops, p1 = _phase1_localize()
    p2 = _phase2_raw_census(mask)
    p3 = _window_arms(tops, mask)
    evidence = {"phase1_localize": p1, "phase2_raw_clip_census": p2,
                "phase3_window_arms": p3}
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, indent=1))
    print("\nmean fringe-mask saturation per arm (lower = cleaner):")
    for arm, vals in p3["arms"].items():
        vs = [f"{v:.3f}" if v is not None else "-" for v in vals]
        print(f"  {arm:24s} {vs}")
    print(f"\ncrops -> {OUT} (1:1 native px)\nevidence -> {EVIDENCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
