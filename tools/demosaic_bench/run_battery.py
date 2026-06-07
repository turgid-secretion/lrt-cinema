"""Consolidated multi-metric demosaic battery — the full picture, not CPSNR alone.

Scores four demosaic methods through EVERY applicable §5 metric and prints one
table so a demosaic-algorithm decision rests on resolution + false-colour + zipper
+ texture, not the single CPSNR number that rewards blur and is false-colour-blind
(docs/research/demosaic-test-fixtures.md §3/§5):

  methods : bilinear · Malvar2004 · our-RCD · Menon2007
  natural : Kodak-24 (sRGB protocol, 10-px border) -> CPSNR, S-CIELAB ΔE,
            zipper%, region-split (edge/smooth CPSNR + ΔE)
  charts  : slanted edge -> MTF50P ; zone plate (neutral) -> false-colour
            chroma-energy ; isoluminant colour edge -> edge ΔE

Caveats baked in: Kodak is the published *sRGB-domain* protocol (a competitiveness
sanity check, NOT lrt-cinema's linear production path — §8 Layer D); the charts
ARE linear (the production domain) and carry the resolution/false-colour metrics
re-mosaicing a natural image cannot. Datasets download-on-demand, never checked in.

GLOBAL SANITY (advisor): bilinear should be WORST on ~every axis. If it is not, a
metric is buggy. The bottom of the table prints that check explicitly.

Run: python3 tools/demosaic_bench/run_battery.py [/tmp/kodak]
Skip-gates cleanly if /tmp/kodak or colour_demosaicing are absent.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import charts  # noqa: E402
import metrics as M  # noqa: E402

from lrt_cinema._mlri_demosaic import mlri_demosaic  # noqa: E402
from lrt_cinema._rcd_demosaic import rcd_demosaic  # noqa: E402

_BORDER = 10


@contextmanager
def _rcd_chroma_median(size: int, iters: int = 1):
    """Force the RCD terminal-chroma-median config for the duration of the block, so
    a median variant is measured against the SAME operator the CLI render uses
    (`LRT_RCD_CHROMA_MEDIAN`). Restores the prior env afterwards."""
    keys = {"LRT_RCD_CHROMA_MEDIAN": str(size), "LRT_RCD_CHROMA_MEDIAN_ITERS": str(iters)}
    saved = {k: os.environ.get(k) for k in keys}
    os.environ.update(keys)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _rcd_med(c, size, iters=1):
    """RCD with the terminal chroma-median forced ON (env-scoped)."""
    with _rcd_chroma_median(size, iters):
        return rcd_demosaic(c, "RGGB")


def _methods():
    """The demosaic methods, each (name, fn(cfa_float, 'RGGB') -> HxWx3).
    Returns None if the colour_demosaicing anchors are unavailable (skip-gate).

    The `our-RCD+medNiK` rows are the experimental terminal chroma-median (square
    median on R-G/B-G, N = window, K = iters; default-OFF in production) — the
    challenger for the venetian-blind false-colour test. They share the exact
    operator the CLI render invokes via `LRT_RCD_CHROMA_MEDIAN`. The decision gate
    here is: does `our-RCD+med` drop neutral falseClr (unambiguous — true chroma is
    zero on the zone-plate) while holding CPSNR/SCIELAB vs `our-RCD`?"""
    try:
        from colour_demosaicing import (
            demosaicing_CFA_Bayer_Malvar2004 as malvar,
        )
        from colour_demosaicing import (
            demosaicing_CFA_Bayer_Menon2007 as menon,
        )
    except Exception:
        return None
    return [
        ("bilinear", lambda c: M.bilinear_rggb(c)),
        ("Malvar2004", lambda c: np.asarray(malvar(c, "RGGB"), dtype=np.float64)),
        ("our-RCD", lambda c: _rcd_med(c, 0)),  # median forced OFF (the baseline)
        ("our-RCD+med3i1", lambda c: _rcd_med(c, 3, 1)),
        ("our-RCD+med5i1", lambda c: _rcd_med(c, 5, 1)),
        ("our-RCD+med3i2", lambda c: _rcd_med(c, 3, 2)),
        ("our-RCD+med5i2", lambda c: _rcd_med(c, 5, 2)),
        ("our-MLRI", lambda c: mlri_demosaic(c, "RGGB")),
        ("Menon2007", lambda c: np.asarray(menon(c, "RGGB"), dtype=np.float64)),
    ]


def _clip01(a: np.ndarray) -> np.ndarray:
    """Identical [0,1] clip for every method before any Lab-domain metric (clip
    policy): anchors ring out of range; unclipped negatives -> bogus huge ΔE that
    differentially punishes whichever method rings. CPSNR stays unclipped."""
    return np.clip(a, 0.0, 1.0)


# --------------------------------------------------------------------------- Kodak
def _run_kodak(kodak_dir: str, methods) -> dict[str, dict[str, float]]:
    import imageio.v3 as iio

    paths = sorted(Path(kodak_dir).glob("kodim*.png"))
    if not paths:
        return {}
    acc = {name: {"cpsnr": [], "scielab": [], "zipper": [],
                  "cpsnr_edge": [], "cpsnr_smooth": [], "de_edge": [], "de_smooth": []}
           for name, _ in methods}
    for p in paths:
        rgb = iio.imread(p).astype(np.float64) / 255.0
        cfa = M.mosaic_rggb(rgb)
        ref_xyz = M.xyz_from_srgb(_clip01(rgb))  # ref clipped identically
        for name, fn in methods:
            out = fn(cfa.copy())
            test_xyz = M.xyz_from_srgb(_clip01(out))
            a = acc[name]
            a["cpsnr"].append(M.cpsnr(rgb, out, border=_BORDER))  # unclipped
            a["scielab"].append(M.s_cielab_de(ref_xyz, test_xyz))
            a["zipper"].append(M.zipper_ratio(ref_xyz, test_xyz))
            rs = M.region_split(ref_xyz, test_xyz, rgb, out, border=_BORDER)
            for k in ("cpsnr_edge", "cpsnr_smooth", "de_edge", "de_smooth"):
                a[k].append(rs[k])
    return {name: {k: float(np.mean(v)) for k, v in d.items()} for name, d in acc.items()}


# -------------------------------------------------------------------------- charts
def _run_charts(methods) -> dict[str, dict[str, float]]:
    """Linear-domain synthetic charts: MTF50P (slanted edge), false-colour
    chroma-energy (neutral zone plate), edge ΔE (isoluminant colour edge)."""
    edge = charts.slanted_edge(256, angle_deg=5.0)            # neutral edge
    zp, zp_mask = charts.zone_plate(256, k=1.1)               # neutral -> false colour
    iso = charts.isoluminant_color_edge(256, angle_deg=5.0)   # isoluminant chroma edge
    # ADVERSARIAL real-colour test: a near-Nyquist isoluminant colour grating (the
    # blinds' frequency + geometry). A false-colour suppressor must NOT flatten it.
    # 2-colour is a degeneracy for MLRI (exact linear tentative) so we ALSO run the
    # 3-colour non-degenerate variant; the median's verdict (does it attenuate real
    # chroma vs rcd?) is read off the 3-colour column.
    grat, grat_gt = charts.isoluminant_color_grating(256, period_px=3.0, axis="h")
    grat3, grat3_gt = charts.isoluminant_color_grating3(256, band_px=1, axis="h")
    iso_xyz_ref = M.xyz_from_linear_rgb(_clip01(iso))
    # The isoluminant transition lives in CHROMA, not L* — localise with the
    # chroma-gradient mask (an L* mask sees ~no signal here). The ΔE is measured
    # over this thin transition band so it reflects edge FRINGING, not the flat
    # colour fields on either side.
    iso_edge_mask = M._chroma_edge_mask(iso_xyz_ref)
    out: dict[str, dict[str, float]] = {}
    for name, fn in methods:
        e_out = fn(M.mosaic_rggb(edge))
        z_out = fn(M.mosaic_rggb(zp))
        i_out = fn(M.mosaic_rggb(iso))
        g_out = fn(M.mosaic_rggb(grat))
        g3_out = fn(M.mosaic_rggb(grat3))
        lab_r = M._xyz_to_lab(iso_xyz_ref)
        lab_t = M._xyz_to_lab(M.xyz_from_linear_rgb(_clip01(i_out)))
        de = np.sqrt(np.sum((lab_r - lab_t) ** 2, axis=-1))
        out[name] = {
            "mtf50p": M.mtf50p(_clip01(e_out), channel="luma"),
            "falsecolor": M.falsecolor_chroma_energy(z_out, zp_mask),
            "iso_edge_de": float(np.mean(de[iso_edge_mask])) if iso_edge_mask.any() else 0.0,
            "grating_recov": M.chroma_amplitude_recovery(g_out, grat_gt),
            "grating3_recov": M.chroma_amplitude_recovery(g3_out, grat3_gt),
        }
    return out


# --------------------------------------------------------------------------- table
def _fmt(v: float, width: int = 9, prec: int = 3) -> str:
    if v == float("inf"):
        return f"{'inf':>{width}}"
    if np.isnan(v):
        return f"{'--':>{width}}"
    return f"{v:>{width}.{prec}f}"


def _print_table(kodak: dict, charts_res: dict, methods) -> None:
    names = [n for n, _ in methods]
    cols = [
        ("CPSNR↑", "kodak", "cpsnr", 2),
        ("SCIELAB↓", "kodak", "scielab", 3),
        ("zipper%↓", "kodak", "zipper", 2),
        ("CPSNRedge↑", "kodak", "cpsnr_edge", 2),
        ("CPSNRsmth↑", "kodak", "cpsnr_smooth", 2),
        ("ΔEedge↓", "kodak", "de_edge", 3),
        ("MTF50P↑", "charts", "mtf50p", 4),
        ("falseClr↓", "charts", "falsecolor", 3),
        ("isoΔE↓", "charts", "iso_edge_de", 3),
        ("grat2↑", "charts", "grating_recov", 3),
        ("grat3↑", "charts", "grating3_recov", 3),
    ]
    print("\n" + "=" * 92)
    print("DEMOSAIC MULTI-METRIC BATTERY  (↑ higher better · ↓ lower better)")
    print("  Kodak-24: sRGB protocol, 10-px border (competitiveness, NOT linear prod path)")
    print("  charts:   LINEAR domain (production domain) — MTF50P/falseClr/isoΔE")
    print("=" * 92)
    hdr = f"{'method':<11}" + "".join(f"{c[0]:>11}" for c in cols)
    print(hdr)
    print("-" * len(hdr))
    src = {"kodak": kodak, "charts": charts_res}
    for name in names:
        row = f"{name:<11}"
        for _label, where, key, prec in cols:
            d = src[where].get(name, {})
            row += _fmt(d.get(key, float("nan")), width=11, prec=prec)
        print(row)
    print("-" * len(hdr))
    _print_sanity(kodak, charts_res, names, cols, src)


# The isoluminant-edge column is a CHARACTERISATION of the documented failure
# boundary (§8), NOT a quality axis: on a saturated isoluminant edge a *blurring*
# demosaic legitimately beats a directional one (less colour fringe), so bilinear
# is EXPECTED to score well there. Excluded from the "bilinear worst" gate with
# that reason; the gate scopes to the genuine quality axes below.
# gratRecov is also excluded: it characterises the adversarial real-colour grating,
# where MORE chroma blur -> LESS recovery, so the floor is whichever method blurs
# chroma MOST (bilinear OR mlri), not a demosaic-quality ranking. Like isoΔE it
# probes the failure boundary, not quality.
_SANITY_EXCLUDE = {"isoΔE↓", "grat2↑", "grat3↑"}


def _print_sanity(kodak, charts_res, names, cols, src) -> None:
    """The cheap global bug-finder: bilinear (the floor) must be WORST on every
    genuine quality axis. Any exception flags a suspect metric. The isoluminant-
    edge column is excluded by design (`_SANITY_EXCLUDE`) — it characterises the
    failure boundary where blur is legitimately favoured, not demosaic quality."""
    if "bilinear" not in names:
        return
    higher_better = {"CPSNR↑", "CPSNRedge↑", "CPSNRsmth↑", "MTF50P↑"}
    anomalies = []
    for label, where, key, _ in cols:
        if label in _SANITY_EXCLUDE:
            continue
        vals = {n: src[where].get(n, {}).get(key, np.nan) for n in names}
        if any(np.isnan(v) or v == float("inf") for v in vals.values()):
            continue
        bil = vals["bilinear"]
        others = [vals[n] for n in names if n != "bilinear"]
        worst_is_bilinear = (bil <= min(others)) if label in higher_better else (bil >= max(others))
        if not worst_is_bilinear:
            anomalies.append(label)
    if anomalies:
        print(f"SANITY WARN: bilinear is NOT worst on {anomalies} — inspect those metrics.")
    else:
        print("SANITY OK: bilinear worst on every quality axis (isoΔE excluded by design "
              "— failure-boundary characterisation, blur legitimately favoured).")


def main(kodak_dir: str) -> int:
    methods = _methods()
    if methods is None:
        print("colour_demosaicing not installed — skipping battery (anchors unavailable).")
        return 0
    have_kodak = bool(sorted(Path(kodak_dir).glob("kodim*.png")))
    if not have_kodak:
        print(f"no kodim*.png in {kodak_dir} — running CHARTS only "
              "(download Kodak-24 to /tmp/kodak for the natural-image leg).")
    kodak = _run_kodak(kodak_dir, methods) if have_kodak else {}
    charts_res = _run_charts(methods)
    _print_table(kodak, charts_res, methods)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/kodak"))
