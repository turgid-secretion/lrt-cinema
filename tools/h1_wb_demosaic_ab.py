"""H1 A/B — does demosaicing BEFORE white-balance scaling cause the cyan artifact?

Hypothesis H1 (CLAIMS.md, cyan/blinds section): our pipeline demosaics the raw
CFA *unscaled* (`_postprocess_kwargs` passes unit WB; `_cfa_demosaic` gets the
raw mosaic) and applies white balance afterwards (Stage 2), while RT/LR/libraw
scale by WB *before* demosaicing. Directional demosaics assume roughly balanced
channels; on unbalanced data they mis-estimate edges and the cool develop WB
(4034K) then amplifies the blue-channel error into saturated cyan at steep
edges (the owner-flagged venetian-blinds defect).

Single-variable A/B, per algorithm (rcd = clean-room production candidate,
menon = BSD-3 quality reference):

    current:  demosaic(cfa)                       -> camera RGB
    H1:       demosaic(cfa * wb_per_site) / wb    -> camera RGB

Everything downstream (Stages 2-9, display transform) is byte-identical; the
develop WB (4034 K / +20, from the production XMPs) is used both for the
pre-scale and for Stage 2, exactly as in production. The cyan hotspot is
located automatically; LR Classic + LRT-internal references are cropped at the
same coordinates for visual comparison.

Run:  python3 tools/h1_wb_demosaic_ab.py
Artifacts: ~/lrt-cinema-fixtures/h1/ (full crops) + a metrics JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

FIX = Path.home() / "lrt-cinema-fixtures"
DNG = FIX / "DSC_4053.dng"
LR_TIF = FIX / "production/lr-export/DSC_4053.tif"
LRT_JPG = FIX / "production/lrt-jpg/LRT_00001.jpg"
DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"
)
KELVIN, TINT = 4034.0, 20.0   # the production develop WB (xmp census)
OUT = FIX / "h1"
CROP = 512                     # crop window (full-res px)


def _srgb8(prophoto: np.ndarray) -> np.ndarray:
    """Linear ProPhoto(D50) -> sRGB 8-bit (same math as tests/test_pipeline.py)."""
    import colour
    m_pp = colour.RGB_COLOURSPACES["ProPhoto RGB"].matrix_RGB_to_XYZ
    m_srgb = colour.RGB_COLOURSPACES["sRGB"].matrix_XYZ_to_RGB
    m_brad = colour.adaptation.matrix_chromatic_adaptation_VonKries(
        np.array([0.96422, 1.0, 0.82521]), np.array([0.95047, 1.0, 1.08883]),
        transform="Bradford")
    h, w, _ = prophoto.shape
    lin = np.clip(prophoto.reshape(-1, 3) @ m_pp.T @ m_brad.T @ m_srgb.T, 0, 1)
    lin = lin.reshape(h, w, 3)
    enc = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * np.maximum(lin, 0) ** (1 / 2.4) - 0.055)
    return (enc * 255).astype(np.uint8)


def _cyanness(srgb8: np.ndarray) -> np.ndarray:
    """Per-pixel cyan excess in linear sRGB: how far min(G,B) exceeds R."""
    import colour
    lin = colour.models.eotf_sRGB(srgb8.astype(np.float64) / 255.0)
    return np.maximum(np.minimum(lin[..., 1], lin[..., 2]) - lin[..., 0], 0.0)


def main() -> int:
    import imageio.v3 as iio
    import rawpy
    from PIL import Image

    from lrt_cinema import accel
    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.pipeline import (
        _extract_cfa,
        apply_adobe_pipeline,
        kelvin_to_neutral,
        read_dng_baseline_exposure,
    )

    OUT.mkdir(parents=True, exist_ok=True)
    profile = parse_dcp(DCP)
    asn = kelvin_to_neutral(profile, KELVIN, TINT)
    wb_mul = (1.0 / asn) / (1.0 / asn)[1]          # Stage-2's exact multiplier
    dng_be = read_dng_baseline_exposure(DNG)

    with rawpy.imread(str(DNG)) as raw:
        cfa, pattern = _extract_cfa(raw)
        colors = raw.raw_colors_visible
        h, w = cfa.shape
        chan = np.where(colors[:h, :w] == 3, 1, colors[:h, :w])   # G2 -> G
        gainmap = wb_mul[chan].astype(np.float32)

    def menon(c):
        from colour_demosaicing import demosaicing_CFA_Bayer_Menon2007
        return np.maximum(np.asarray(
            demosaicing_CFA_Bayer_Menon2007(c, pattern), dtype=np.float32), 0.0)

    algos = {"rcd": lambda c: accel.rcd_demosaic(c, pattern), "menon": menon}
    renders: dict[str, np.ndarray] = {}
    for name, dm in algos.items():
        # "current" = the PRE-FIX behaviour (demosaic the raw mosaic), kept as
        # the historical arm; "h1" = the hand-built target arm; "pipeline" =
        # the SHIPPED production path (`_cfa_demosaic` with wb_mul, post
        # 2026-06-10 WB-before-demosaic fix) — must land on the h1 arm.
        for cond in ("current", "h1", "pipeline"):
            if cond == "current":
                # Pre-fix arm: demosaic UNBALANCED, then emulate the
                # deleted Stage-2 multiply (the historical wrong order).
                rgb = (dm(cfa) * wb_mul[None, None, :]).astype(np.float32)
            elif cond == "h1":
                # Target arm: scale -> demosaic, BALANCED output
                # (slot-3 contract; divide-back shim deleted).
                rgb = dm(cfa * gainmap).astype(np.float32)
            else:
                from lrt_cinema.pipeline import _cfa_demosaic
                with rawpy.imread(str(DNG)) as raw2:
                    rgb = _cfa_demosaic(raw2, name, wb_mul.astype(np.float32))
            pp = apply_adobe_pipeline(rgb, profile, asn, KELVIN,
                                      dng_baseline_exposure=dng_be, stop_after_stage=9)
            renders[f"{name}-{cond}"] = _srgb8(pp)
            print(f"rendered {name}-{cond}")

    # References (their grid is 4016x6016: ours minus 8 px each side).
    lr8 = (iio.imread(LR_TIF).astype(np.float32) / 65535.0 * 255).astype(np.uint8)
    jpg8 = iio.imread(LRT_JPG)
    refs = {"LR-Classic": lr8, "LRT-jpg": jpg8}

    # Locate the ARTIFACT hotspot: maximize cyanness(ours) − cyanness(LR) so
    # legitimately-cyan scene content (blue doors etc.) cancels out and only
    # cyan WE invent remains. Grids aligned by cropping ours 8 px.
    cy_ours = _cyanness(renders["rcd-current"][8:-8, 8:-8])
    cy_lr = _cyanness(lr8)
    diff = np.maximum(cy_ours - cy_lr, 0.0)
    k = 128
    H2, W2 = diff.shape[0] // k, diff.shape[1] // k
    blocks = diff[:H2 * k, :W2 * k].reshape(H2, k, W2, k).sum(axis=(1, 3))
    by, bx = np.unravel_index(np.argmax(blocks), blocks.shape)
    # Convert back to OUR grid coords (+8) and center the crop window.
    cy0 = max(0, by * k + k // 2 - CROP // 2 + 8)
    cx0 = max(0, bx * k + k // 2 - CROP // 2 + 8)
    print(f"artifact hotspot (ours-minus-LR) crop @ y={cy0} x={cx0} (ours grid)")

    metrics, panels = {}, []
    for name in ("rcd-current", "rcd-h1", "rcd-pipeline",
                 "menon-current", "menon-h1", "menon-pipeline"):
        crop = renders[name][cy0:cy0 + CROP, cx0:cx0 + CROP]
        c = _cyanness(crop)
        metrics[name] = {"cyan_mean_x1000": round(float(c.mean()) * 1000, 3),
                         "cyan_p99.5_x1000": round(float(np.percentile(c, 99.5)) * 1000, 2)}
        panels.append((name, crop))
    for name, ref in refs.items():
        ry, rx = max(0, cy0 - 8), max(0, cx0 - 8)
        crop = ref[ry:ry + CROP, rx:rx + CROP]
        c = _cyanness(crop)
        metrics[name] = {"cyan_mean_x1000": round(float(c.mean()) * 1000, 3),
                         "cyan_p99.5_x1000": round(float(np.percentile(c, 99.5)) * 1000, 2)}
        panels.append((name, crop))

    grid = Image.new("RGB", (CROP * len(panels), CROP + 18), "black")
    from PIL import ImageDraw
    for i, (name, crop) in enumerate(panels):
        grid.paste(Image.fromarray(crop), (i * CROP, 18))
        ImageDraw.Draw(grid).text((i * CROP + 4, 2), name, fill="white")
    grid_path = OUT / "h1_blinds_grid.png"
    grid.save(grid_path)

    meta = {"crop_yx": [int(cy0), int(cx0)], "kelvin": KELVIN, "tint": TINT,
            "metrics": metrics}
    (OUT / "h1_metrics.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta, indent=1))
    print(f"grid -> {grid_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
