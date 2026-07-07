"""v2 H/S translation diagnostics vs the owner's round-1 verdicts.

Two owner-reported defects, made measurable:

1. FLAT LOOK (Highlights): "ours appear to just be a linear pulldown of
   everything above a certain luminance — flat; Adobe retains contrast
   across the affected region." Metric: LOCAL CONTRAST RETENTION — std of
   log2 display luminance in 8-block windows (32 px native), binned by the
   BASE render's luminance, reported as a ratio vs the LR export per bin.
   1.0 = matches LR's local contrast; round-1's guided-base op should read
   well below 1.0 in the affected (bright) bins; v2's LLF core should
   recover toward 1.0.

2. SHADOW BOUNDARY ARTIFACTS (Shadows +100): "weird patches / false
   colors / blur at the interface between total black and adjusted
   shadows." Made visible: native-res crops centred on the strongest
   toe-boundary regions (base luminance crossing the toe floor with high
   gradient), written as flip triplets LR / v1(round-1 cache) / v2 + base.
   Plus a numeric proxy: mean |chroma| (a*b* magnitude) in the boundary
   band, per arm, vs LR.

Run:  python3 tools/hlsh_v2_diagnostics.py --v1-tag <tag> --v2-tag <tag>
      (tags = cached render names in round2/.cal-renders, from the fit
      evidence JSONs' final_render_tags)
Out:  ~/lrt-cinema-fixtures/verify-2026-07-08/hlsh-v2-diag/ + stdout table
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

FIX = Path.home() / "lrt-cinema-fixtures"
ROUND2 = FIX / "production/calibration/round2"
RENDERS = ROUND2 / ".cal-renders"
OUT = FIX / "verify-2026-07-08/hlsh-v2-diag"

WIN = 8            # local-contrast window in DOWN-blocks (32 px native)
N_CROPS = 4
CROP = 512         # native px


def _load16(tif: Path) -> np.ndarray:
    import tifffile
    return tifffile.imread(str(tif)).astype(np.float32) / 65535.0


def _lin(img: np.ndarray) -> np.ndarray:
    import colour
    return colour.models.eotf_sRGB(img)


def _block(a: np.ndarray, k: int) -> np.ndarray:
    h, w = a.shape[:2]
    h2, w2 = (h // k) * k, (w // k) * k
    v = a[:h2, :w2].reshape(h2 // k, k, w2 // k, k, -1)
    return v.mean(axis=(1, 3))


def _local_contrast(lum: np.ndarray, win: int) -> np.ndarray:
    """Std of log2 luminance in win x win windows (non-overlapping)."""
    l2 = np.log2(np.maximum(lum, 1e-6))
    h, w = l2.shape
    h2, w2 = (h // win) * win, (w // win) * win
    v = l2[:h2, :w2].reshape(h2 // win, win, w2 // win, win)
    return v.std(axis=(1, 3))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1-tag", required=True)
    ap.add_argument("--v2-tag", required=True)
    ap.add_argument("--probe", default="CALHIM100")
    ap.add_argument("--shadow-probe", default="CALSH100")
    ap.add_argument("--shadow-v1-tag", default=None)
    ap.add_argument("--shadow-v2-tag", default=None)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    # ---------- 1. local-contrast retention (Highlights probe) ----------
    base = _lin(_block(_load16(RENDERS / "BASE.tif")[8:-8, 8:-8], 4))
    lr = _lin(_block(_load16(ROUND2 / f"{args.probe}_4053.tif"), 4))
    v1 = _lin(_block(_load16(RENDERS / f"{args.v1_tag}.tif")[8:-8, 8:-8], 4))
    v2 = _lin(_block(_load16(RENDERS / f"{args.v2_tag}.tif")[8:-8, 8:-8], 4))

    wl = np.array([0.2126, 0.7152, 0.0722])
    base_lum = base @ wl
    lc = {n: _local_contrast(a @ wl, WIN) for n, a in
          (("lr", lr), ("v1", v1), ("v2", v2))}
    base_win = _block(base_lum[..., None], WIN)[..., 0]

    edges = np.geomspace(max(np.percentile(base_win, 0.5), 1e-5),
                         np.percentile(base_win, 99.5), 13)
    idx = np.digitize(base_win.ravel(), edges) - 1
    print(f"local-contrast retention vs LR ({args.probe}); 1.0 = LR-like")
    print(f"{'base lum':>10s} {'v1/LR':>7s} {'v2/LR':>7s} {'n':>6s}")
    rows = []
    for b in range(12):
        m = idx == b
        if m.sum() < 30:
            continue
        c = float(np.sqrt(edges[b] * edges[b + 1]))
        r1 = float(np.median(lc["v1"].ravel()[m])
                   / max(np.median(lc["lr"].ravel()[m]), 1e-6))
        r2 = float(np.median(lc["v2"].ravel()[m])
                   / max(np.median(lc["lr"].ravel()[m]), 1e-6))
        rows.append((c, r1, r2, int(m.sum())))
        print(f"{c:10.4f} {r1:7.3f} {r2:7.3f} {m.sum():6d}")

    # ---------- 2. shadow toe-boundary crops + chroma proxy ----------
    sp = args.shadow_probe
    v1s_tag = args.shadow_v1_tag or args.v1_tag.replace(args.probe, sp)
    v2s_tag = args.shadow_v2_tag or args.v2_tag.replace(args.probe, sp)
    lr_s = _load16(ROUND2 / f"{sp}_4053.tif")
    v1_s = _load16(RENDERS / f"{v1s_tag}.tif")[8:-8, 8:-8]
    v2_s = _load16(RENDERS / f"{v2s_tag}.tif")[8:-8, 8:-8]
    base_full = _load16(RENDERS / "BASE.tif")[8:-8, 8:-8]

    # boundary band: base display-linear luminance in the deep toe next to
    # a lifted-shadow zone — high local gradient of the toe mask
    bl = _lin(base_full) @ wl
    toe = (bl < 3e-4).astype(np.float32)
    from scipy.ndimage import uniform_filter
    frac = uniform_filter(toe, size=65)
    band = (frac > 0.15) & (frac < 0.85)      # mixed toe/lifted windows

    import colour
    def chroma_in_band(img):
        lab = colour.XYZ_to_Lab(
            colour.RGB_to_XYZ(_lin(img), "sRGB", apply_cctf_decoding=False),
            illuminant=np.array([0.3127, 0.3290]))
        return float(np.hypot(lab[..., 1], lab[..., 2])[band].mean())

    print(f"\ntoe-boundary band ({band.sum()} px): mean C*ab")
    cb = {n: chroma_in_band(a) for n, a in
          (("lr", lr_s), ("v1", v1_s), ("v2", v2_s))}
    for n, v in cb.items():
        print(f"  {n}: {v:.2f}")

    # crops: the N_CROPS strongest boundary neighbourhoods, spaced apart
    score = uniform_filter((band).astype(np.float32), size=CROP // 2)
    picks = []
    s = score.copy()
    for _ in range(N_CROPS):
        j, i = np.unravel_index(np.argmax(s), s.shape)
        picks.append((j, i))
        j0, j1 = max(0, j - CROP), min(s.shape[0], j + CROP)
        i0, i1 = max(0, i - CROP), min(s.shape[1], i + CROP)
        s[j0:j1, i0:i1] = -1
    from PIL import Image

    def crop_png(img: np.ndarray, j: int, i: int, name: str) -> None:
        h, w = img.shape[:2]
        j0 = int(np.clip(j - CROP // 2, 0, h - CROP))
        i0 = int(np.clip(i - CROP // 2, 0, w - CROP))
        c = img[j0:j0 + CROP, i0:i0 + CROP]
        Image.fromarray((c * 255.0 + 0.5).astype(np.uint8)).save(OUT / name)

    for n, (j, i) in enumerate(picks):
        crop_png(lr_s, j, i, f"crop{n}_A-lr.png")
        crop_png(v1_s, j, i, f"crop{n}_B-v1-guided.png")
        crop_png(v2_s, j, i, f"crop{n}_C-v2-llf.png")
        crop_png(base_full, j, i, f"crop{n}_D-base.png")
    (OUT / "README.txt").write_text(
        f"{sp} toe-boundary crops (512px native, strongest mixed toe/lifted"
        f" neighbourhoods).\nA = LR export, B = round-1 guided op,"
        f" C = v2 LLF op, D = zero-slider base.\nFlip A/B/C per crop.\n"
        f"Mean C*ab in boundary band: {cb}\n")
    print(f"\ncrops -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
