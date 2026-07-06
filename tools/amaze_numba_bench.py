"""AMaZE numba-twin acceptance evidence — parity, speed, chain identity.

THE CONTRACT (owner directive 2026-07-06, "1/50th of current speed"):
`_amaze_numba._amaze_rggb_fast` must be (a) BIT-EXACT against the validated
numpy spec `_amaze_demosaic._amaze_rggb` on the full 24 MP production frame
— max |delta| == 0, borders included, Nyquist gates engaged — and (b) at
least 50x faster than the numpy twin's measured baseline.

Measures:
  1. gym full-frame parity (numpy vs numba, float32 input contract);
  2. steady-state pooled timing over N runs + the numpy baseline;
  3. full-chain identity: two pressure articles rendered through
     `render_frame(demosaic='amaze')` (which now dispatches to the numba
     twin) scored and compared to the committed 4-arm standings evidence
     (`pressure_2026-07-06.json`) — proves the swap-in is metric-invisible.

Run:  python3 tools/amaze_numba_bench.py
Out:  tests/fixtures/evidence/amaze_numba_<today>.json
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools" / "test_articles"))

FIX = Path.home() / "lrt-cinema-fixtures"
ART = FIX / "test-articles"
GYM_DNG = FIX / "DSC_4053.dng"
DCP = Path(
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Nikon D750/Nikon D750 Camera Standard.dcp"
)
PRESSURE_EVIDENCE = REPO / "tests/fixtures/evidence/pressure_2026-07-06.json"
EVIDENCE = REPO / ("tests/fixtures/evidence/"
                   f"amaze_numba_{_dt.date.today().isoformat()}.json")
ARTICLES = ("diagbars", "clipbars")
RUNS = 5


def main() -> int:
    import rawpy

    from lrt_cinema._amaze_demosaic import _amaze_rggb
    from lrt_cinema._amaze_numba import _amaze_rggb_fast

    results: dict = {}

    # ---- 1+2: gym parity + timing ------------------------------------------
    with rawpy.imread(str(GYM_DNG)) as raw:
        cfa = raw.raw_image_visible.astype(np.float32)
        wl = float(raw.white_level)
        bl = float(np.mean(raw.black_level_per_channel))
        cfa = np.clip((cfa - np.float32(bl)) / np.float32(wl - bl),
                      0, 1).astype(np.float32)
    h, w = cfa.shape
    h -= h % 2
    w -= w % 2
    cfa = np.ascontiguousarray(cfa[:h, :w])
    mp = h * w / 1e6

    _amaze_rggb_fast(np.ascontiguousarray(cfa[:64, :64]), np.float32(1.0))
    fast = _amaze_rggb_fast(cfa, np.float32(1.0))       # populates the pool
    times = []
    for _ in range(RUNS):
        t0 = time.perf_counter()
        out = _amaze_rggb_fast(cfa, np.float32(1.0))
        times.append(time.perf_counter() - t0)
        assert np.abs(out - fast).max() == 0, "pooled rerun nondeterminism"

    t0 = time.perf_counter()
    ref = _amaze_rggb(cfa, np.float32(1.0))
    numpy_s = time.perf_counter() - t0
    maxdiff = float(np.abs(ref.astype(np.float64)
                           - fast.astype(np.float64)).max())
    best = min(times)
    results["gym"] = {
        "frame_mp": round(mp, 2),
        "parity_max_abs_diff": maxdiff,
        "numba_s": [round(t, 4) for t in times],
        "numba_best_s": round(best, 4),
        "numpy_s": round(numpy_s, 3),
        "speedup": round(numpy_s / best, 1),
    }
    print(f"gym {mp:.1f} MP: parity max|d|={maxdiff}  "
          f"numba {best:.3f}s vs numpy {numpy_s:.2f}s = {numpy_s/best:.1f}x")
    assert maxdiff == 0.0, "PARITY BROKEN"

    # ---- 3: full-chain article identity -------------------------------------
    from fields import scene_field
    from run_pressure import NEUTRAL_TRUTH, _score

    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.output import _prophoto_to_display
    from lrt_cinema.pipeline import (
        apply_adobe_pipeline,
        read_dcp_default_black_render,
        read_dng_baseline_exposure,
        render_frame,
    )

    manifest = json.loads((ART / "manifest.json").read_text())
    asn = np.asarray(manifest["asn"], np.float32)
    profile = parse_dcp(DCP)
    profile = type(profile)(**{**profile.__dict__,
                               "forward_matrix_1": None,
                               "forward_matrix_2": None})
    pinned = json.loads(PRESSURE_EVIDENCE.read_text())["articles"]

    def to8(pp):
        return (np.clip(_prophoto_to_display(pp, "srgb"), 0, 1)
                * 255 + 0.5).astype(np.uint8)

    results["chain_identity"] = {}
    for name in ARTICLES:
        dng = ART / f"{name}.dng"
        meta = manifest["articles"][name]
        dng_be = read_dng_baseline_exposure(dng)
        dbr = read_dcp_default_black_render(DCP)
        with rawpy.imread(str(dng)) as r:
            ah, aw = r.raw_image_visible.shape
        ah -= ah % 2
        aw -= aw % 2
        scene = scene_field(meta["spec"], ah, aw)
        unbal = scene * asn[None, None, :]
        wb_mul = (1.0 / asn) / (1.0 / asn)[1]
        exp8 = to8(apply_adobe_pipeline(
            camera_rgb=(np.minimum(unbal, 1.0) * wb_mul).astype(np.float32),
            profile=profile, as_shot_neutral=asn, scene_kelvin=5500.0,
            dng_baseline_exposure=dng_be, default_black_render=dbr,
            stop_after_stage=9))
        nclip = (unbal >= 1.0).sum(axis=-1)
        partial = (nclip > 0) & (nclip < 3)
        res = render_frame(dng, profile, dcp_path=DCP, demosaic="amaze")
        ours8 = to8(res.prophoto)
        oh, ow = ours8.shape[:2]
        s = _score(ours8, exp8[:oh, :ow], name in NEUTRAL_TRUTH,
                   partial[:oh, :ow] if partial.any() else None)
        pin = pinned[name]["arms"]["amaze"]
        deltas = {k: abs(s[k] - pin[k]) for k, v in s.items()
                  if isinstance(v, float) and isinstance(pin.get(k), float)}
        worst = max(deltas.values())
        results["chain_identity"][name] = {
            "scored": s, "pinned_max_abs_delta": worst}
        print(f"{name}: falsecolor {s['falsecolor_mean']:.3f} "
              f"(pinned {pin['falsecolor_mean']:.3f}) "
              f"max metric delta vs evidence: {worst:.2e}")
        assert worst == 0.0, f"{name}: chain no longer reproduces evidence"

    EVIDENCE.write_text(json.dumps(results, indent=1))
    print(f"evidence -> {EVIDENCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
