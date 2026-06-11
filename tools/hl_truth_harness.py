"""Deterministic highlight-reconstruction harness — HELD-OUT-TRUTH scoring.

THE PROBLEM IT SOLVES (owner request, 2026-06-12): the pressure articles
score against a SENSOR-CLIPPED reference, so they are blind to recovered
real detail — reconstruction quality needed eyes. This harness inverts the
construction: clip data we still HAVE the true values for, reconstruct, and
score against the hidden truth. Every reconstruction parameter becomes
deterministically judgeable; no eyeballs in the loop.

TWO TRUTH SOURCES
-----------------
1. REAL-FRAME BAND-CLIP (primary; real texture, zero synthesis):
   take the gym mosaic (DSC_4053), define a synthetic white W (default
   0.6 of true saturation), divide by W (the exact physics of a longer
   exposure: same scene, hotter) and clamp at 1.0. Every site whose
   original value was in [W, 0.99·W_true) now reads "clipped" but its TRUE
   value is known (original/W, up to 1/W ≈ 1.67 over-range). Sites at TRUE
   sensor clip (≥ 0.99) are EXCLUDED — no truth exists there.
2. ANALYTIC ARTICLES (clipramp / clipfield / clipbars): the scene fields
   are known analytically ABOVE the sensor clip; the article DNG mosaics
   carry the construction-truth clipping. Truth = the unclipped analytic
   field at each CFA site.

DOMAIN: scoring is in BALANCED LINEAR camera units at CFA sites (display
renders hide recovery under the tone-curve clamp). The DISPLAY-side
complement is the pressure suite's falsecolor/clip-zone metrics
(run_pressure / hl_reconstruct_experiment): a candidate must improve
rel_mae HERE without regressing falsecolor THERE — together the two
deterministic metric families bracket the visual trade the owner judged
by eye (recovered signal vs rendered chroma artifacts).

METRICS per arm, over truth-holding clipped sites, per channel + overall:
  rel_mae   — mean |rec − truth| / truth  (the headline recovery error)
  bias      — signed mean (rec − truth) / truth  (under/over-reconstruction)
  rel_mae_partial / rel_mae_full — split by whether the site's 3×3
  neighbourhood keeps ≥1 unclipped channel (partial: cross-channel
  inference possible) or not (full: only the global chrominance prior).

ARMS: clamp (no reconstruction — the 5a baseline), pre-opposed (mosaic
domain, dt placement; with an annulus-dilate parameter sweep 2/3/5 to
demonstrate parameter judgement), post-opposed (RGB domain, RT placement,
mosaic-mask-driven; scored by sampling site channels back off the RGB).

DEFERRED (designed, not built): a darktable-cli cross-engine anchor needs
a WhiteLevel-patched DNG pair + a brightness-matched metric (dt's default
tone is nonlinear, so a plain ×W linear correction is invalid). Build only
if our numbers look implausible vs dt's opposed on the same frame.

PRE-REGISTERED PREDICTIONS (2026-06-12, before first run):
  Q1: pre-opposed beats the clamp on real-frame PARTIAL-clip sites
      (relative error down ≥30 %); near-parity on FULL-clip interiors
      (only the global chrominance offset acts there).
  Q2: post-opposed ≤ pre-opposed everywhere (concordant with 5b).
  Q3: on clipbars (sub-Nyquist detail) ALL arms stay poor — recovery
      quality is content-dependent and the harness must expose that.

Run:  python3 tools/hl_truth_harness.py
Out:  tests/fixtures/evidence/hl_truth_harness_2026-06-12.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools" / "test_articles"))

from fields import scene_field  # noqa: E402

FIX = Path.home() / "lrt-cinema-fixtures"
GYM_DNG = FIX / "DSC_4053.dng"
ART = FIX / "test-articles"
DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"
)
SYN_WHITES = (0.4, 0.6)  # synthetic saturations, fraction of true white
                         # (0.4 = deeper truth, 2.5x over-range, more
                         #  multi-channel clips; 0.6 = the original band)
TRUE_CLIP = 0.99         # sites at/above this had NO truth — excluded
ARTICLES = ("clipramp", "clipramp_deep", "clipfield", "clipbars")
EVIDENCE = REPO / "tests/fixtures/evidence/hl_truth_harness_2026-06-12.json"


def _site_channel_view(rgb: np.ndarray, chan: np.ndarray) -> np.ndarray:
    """Sample (H, W, 3) RGB back to the mosaic: each site's own channel."""
    return np.take_along_axis(
        rgb, chan[..., None].astype(np.int64), axis=-1)[..., 0]


def _partial_full_split(clipped_b: np.ndarray, chan: np.ndarray,
                        clips_b: np.ndarray) -> np.ndarray:
    """True where a site's 3×3 neighbourhood retains ≥1 UNCLIPPED channel
    (partial clip: cross-channel inference is possible)."""
    from scipy.ndimage import uniform_filter

    site_clipped = clipped_b >= clips_b[chan]
    any_unclipped = np.zeros(clipped_b.shape, dtype=bool)
    for c in range(3):
        sel = (chan == c) & ~site_clipped
        cnt = uniform_filter(sel.astype(np.float32), size=3, mode="nearest")
        any_unclipped |= cnt > 1e-6
    return any_unclipped


def _score(rec_b: np.ndarray, truth_b: np.ndarray, band: np.ndarray,
           chan: np.ndarray, partial: np.ndarray) -> dict:
    """Relative recovery error over truth-holding sites, split per channel
    and partial/full."""
    out: dict = {}
    err = (rec_b - truth_b) / np.maximum(truth_b, 1e-6)
    sel_all = band
    out["rel_mae"] = float(np.abs(err[sel_all]).mean())
    out["bias"] = float(err[sel_all].mean())
    for c, nm in enumerate("RGB"):
        s = sel_all & (chan == c)
        if s.any():
            out[f"rel_mae_{nm}"] = float(np.abs(err[s]).mean())
    p = sel_all & partial
    f = sel_all & ~partial
    if p.any():
        out["rel_mae_partial"] = float(np.abs(err[p]).mean())
    if f.any():
        out["rel_mae_full"] = float(np.abs(err[f]).mean())
    out["n_sites"] = int(sel_all.sum())
    return out


def _arms_for(clamped_b: np.ndarray, chan: np.ndarray, wb_mul: np.ndarray,
              pattern: str) -> dict[str, np.ndarray]:
    """Render every reconstruction arm to a site-channel mosaic view."""
    from colour_demosaicing import demosaicing_CFA_Bayer_Menon2007
    from scipy.ndimage import maximum_filter

    from lrt_cinema import _opposed_reconstruct as orc

    arms: dict[str, np.ndarray] = {"clamp": clamped_b}

    # pre-opposed with the annulus-dilate parameter sweep (the point of the
    # harness: parameters are now deterministically judgeable).
    base_dilate = orc._ANNULUS_DILATE
    for dil in (2, 3, 5):
        orc._ANNULUS_DILATE = dil
        arms[f"pre-opposed-d{dil}"] = orc.reconstruct_mosaic_opposed(
            clamped_b, chan, wb_mul)
    orc._ANNULUS_DILATE = base_dilate

    # post-opposed: headroom demosaic of the clamped mosaic, mosaic-derived
    # mask (synthetic saturation), reconstruct on RGB, sample sites back.
    rgb = np.maximum(np.asarray(
        demosaicing_CFA_Bayer_Menon2007(clamped_b, pattern), np.float32), 0.0)
    clips_b = (orc._CLIP_MAGIC * wb_mul).astype(np.float32)
    site_clipped = clamped_b >= clips_b[chan]
    mask = np.zeros((*clamped_b.shape, 3), dtype=bool)
    for c in range(3):
        sites = site_clipped & (chan == c)
        if sites.any():
            mask[..., c] = maximum_filter(
                sites.astype(np.uint8), size=5) > 0
    rec_rgb = orc.reconstruct_rgb_opposed(rgb, mask, wb_mul)
    arms["post-opposed"] = _site_channel_view(rec_rgb, chan)
    return arms


def main() -> int:
    import rawpy

    from lrt_cinema import _opposed_reconstruct as orc
    from lrt_cinema.pipeline import _extract_cfa, _wb_mul_from_asn

    results: dict = {
        "design": "held-out-truth reconstruction scoring (band-clip + analytic)",
        "syn_whites": list(SYN_WHITES),
        "predictions": "Q1 pre beats clamp >=30% on partial sites, parity on "
                       "full; Q2 post <= pre; Q3 clipbars poor for all arms",
        "real_frame": {}, "articles": {},
    }

    # ---- Part 1: real-frame band-clip, per synthetic white -----------------
    with rawpy.imread(str(GYM_DNG)) as raw:
        cfa_norm, pattern = _extract_cfa(raw)
        colors = raw.raw_colors_visible
        h, w = cfa_norm.shape
        chan = np.where(colors[:h, :w] == 3, 1, colors[:h, :w])
        wb = np.array(raw.camera_whitebalance[:3], dtype=np.float32)
        asn = 1.0 / wb
        asn = asn / asn[1]
    wb_mul = _wb_mul_from_asn(asn)
    clips_b = (orc._CLIP_MAGIC * wb_mul).astype(np.float32)

    for syn_w in SYN_WHITES:
        truth = cfa_norm / np.float32(syn_w)          # over-range truth
        clamped = np.minimum(truth, np.float32(1.0))  # synthetic sensor clip
        truth_b = truth * wb_mul[chan]
        clamped_b = clamped * wb_mul[chan]
        band = (cfa_norm >= syn_w) & (cfa_norm < TRUE_CLIP)
        partial = _partial_full_split(clamped_b, chan, clips_b)
        wkey = f"W{syn_w}"
        results["real_frame"][wkey] = {}
        print(f"real frame {wkey}: {int(band.sum())} truth-holding clipped "
              f"sites ({band.mean()*100:.2f} % of mosaic; truth to "
              f"{1/syn_w:.2f}x)")

        for name, rec in _arms_for(clamped_b, chan, wb_mul, pattern).items():
            s = _score(rec, truth_b, band, chan, partial)
            results["real_frame"][wkey][name] = s
            print(f"  {name:16s} rel_mae={s['rel_mae']:.4f}"
                  f"  bias={s['bias']:+.4f}"
                  f"  partial={s.get('rel_mae_partial', float('nan')):.4f}"
                  f"  full={s.get('rel_mae_full', float('nan')):.4f}")

    # ---- Part 2: analytic articles ------------------------------------------
    manifest = json.loads((ART / "manifest.json").read_text())
    art_asn = np.asarray(manifest["asn"], np.float32)
    art_wb = _wb_mul_from_asn(art_asn)
    for aname in ARTICLES:
        with rawpy.imread(str(ART / f"{aname}.dng")) as raw:
            cfa_n, pat = _extract_cfa(raw)
            colors = raw.raw_colors_visible
            h, w = cfa_n.shape
            chan_a = np.where(colors[:h, :w] == 3, 1, colors[:h, :w])
        spec = manifest["articles"][aname]["spec"]
        scene = scene_field(spec, h, w)                  # UNCLIPPED truth
        unbal = scene * art_asn[None, None, :]
        truth_site = np.take_along_axis(
            unbal, chan_a[..., None].astype(np.int64), axis=-1)[..., 0]
        truth_ab = (truth_site * art_wb[chan_a]).astype(np.float32)
        clamped_ab = (cfa_n * art_wb[chan_a]).astype(np.float32)
        band_a = truth_site > 1.0                        # clipped at construction
        clips_ab = (orc._CLIP_MAGIC * art_wb).astype(np.float32)
        partial_a = _partial_full_split(clamped_ab, chan_a, clips_ab)
        row = {}
        for name, rec in _arms_for(clamped_ab, chan_a, art_wb, pat).items():
            s = _score(rec, truth_ab, band_a, chan_a, partial_a)
            row[name] = s
            print(f"{aname:10s} {name:16s} rel_mae={s['rel_mae']:.4f}"
                  f"  bias={s['bias']:+.4f}")
        results["articles"][aname] = row

    EVIDENCE.write_text(json.dumps(results, indent=1))
    print(f"\nevidence → {EVIDENCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
