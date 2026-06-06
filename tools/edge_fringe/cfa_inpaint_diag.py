"""DIAGNOSTIC STUB (not a production fix): same-channel CFA clip-inpaint.

The BLOCKING test for the edge-fringing root cause. Hypothesis: the demosaic
interpolating ACROSS the hard clip seeds a Bayer-phase-locked per-channel oscillation
(the blue↔yellow sawtooth). The clean falsification: if, BEFORE demosaic, we replace
each clipped same-channel CFA sample with an estimate from its unclipped same-channel
neighbours (so no channel is flat-topped relative to the others across the edge), the
sawtooth must collapse — and the residual at NON-clip edges (text/brick) isolates the
general-edge demosaic false-colour component.

This is the diagnostic that PROVES the source (and validates the fix-class = mosaic-
domain clip reconstruction, the B1 hook `pipeline._extract_cfa` already anticipates).
It lives HERE, in the harness — NOT in pipeline.py — and is deliberately crude
(iterative same-channel box fill). The real B1 op is a separate, owner-signed-off task.

NB this is the ONLY intervention that removes the seed: you cannot "clamp all channels
to the same max" on a mosaic (one channel per photosite), and scaling the CFA does not
un-clip (clipped samples are already flat-topped at 1.0 — the info is gone). Only
same-channel inpaint reconstructs the lost variation.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import rawpy

sys.path.insert(0, os.path.dirname(__file__))
import fringe_metric as fm  # noqa: E402

from lrt_cinema import accel  # noqa: E402
from lrt_cinema.pipeline import (  # noqa: E402
    _extract_cfa,
    apply_adobe_pipeline,
    read_dcp_default_black_render,
    read_dng_baseline_exposure,
)


def _same_channel_inpaint(cfa: np.ndarray, pattern: str, clip_level: float = 0.99,
                          iters: int = 24) -> np.ndarray:
    """Replace clipped CFA samples (>= clip_level) with an estimate from UNCLIPPED
    SAME-CHANNEL neighbours, iterated so deep clipped interiors fill from the rim
    inward. Same-channel = step 2 px in each axis (the Bayer same-colour lattice).

    Crude on purpose (this is a diagnostic, not the fix): a 3x3 same-channel
    (stride-2) box average over currently-valid samples, applied only to still-clipped
    samples, repeated until filled or `iters` exhausted. Unclipped samples are NEVER
    modified (so non-clip edges are byte-identical → the residual there is pure
    general-edge demosaic false-colour, the clip-vs-general-edge discriminator).
    """
    h, w = cfa.shape
    out = cfa.astype(np.float64).copy()
    clipped0 = cfa >= clip_level
    valid = ~clipped0  # samples we trust as anchors (grow as we fill)
    still = clipped0.copy()

    # Same-channel neighbour offsets (stride 2 keeps the Bayer colour identity).
    offs = [(-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (-2, 2), (2, -2), (2, 2)]
    for _ in range(iters):
        if not still.any():
            break
        acc = np.zeros((h, w), dtype=np.float64)
        cnt = np.zeros((h, w), dtype=np.float64)
        vval = np.where(valid, out, 0.0)
        for dy, dx in offs:
            sv = np.zeros((h, w), dtype=np.float64)
            sc = np.zeros((h, w), dtype=np.float64)
            ys_src = slice(max(dy, 0), h + min(dy, 0))
            ys_dst = slice(max(-dy, 0), h + min(-dy, 0))
            xs_src = slice(max(dx, 0), w + min(dx, 0))
            xs_dst = slice(max(-dx, 0), w + min(-dx, 0))
            sv[ys_dst, xs_dst] = vval[ys_src, xs_src]
            sc[ys_dst, xs_dst] = valid[ys_src, xs_src].astype(np.float64)
            acc += sv
            cnt += sc
        fillable = still & (cnt > 0.5)
        est = np.zeros((h, w), dtype=np.float64)
        np.divide(acc, cnt, out=est, where=cnt > 0.5)
        out[fillable] = est[fillable]
        valid = valid | fillable
        still = still & ~fillable
    # Any still-unfilled clipped sample (fully-enclosed) keeps its clip value.
    return out.astype(np.float32)


def render_inpainted(nef: str, dcp: str, clip_level: float = 0.99,
                     stop_after_stage: int = 7, do_inpaint: bool = True) -> np.ndarray:
    """Replicate render_frame's rcd path (verified byte-identical) with an optional
    same-channel CFA inpaint inserted between extraction and demosaic. Returns the
    linear ProPhoto array at `stop_after_stage`."""
    from lrt_cinema.dcp import parse_dcp
    from lrt_cinema.pipeline import _asn_from_wb

    profile = parse_dcp(dcp)
    with rawpy.imread(nef) as raw:
        cfa, pattern = _extract_cfa(raw)
        asn = _asn_from_wb(raw.camera_whitebalance)
    if do_inpaint:
        cfa = _same_channel_inpaint(cfa, pattern, clip_level=clip_level)
    camera_rgb = accel.rcd_demosaic(cfa, pattern)
    dng_be = read_dng_baseline_exposure(nef)
    dbr = read_dcp_default_black_render(dcp)
    return apply_adobe_pipeline(
        camera_rgb=camera_rgb, profile=profile, as_shot_neutral=asn,
        scene_kelvin=5500.0, dng_baseline_exposure=dng_be,
        default_black_render=dbr, stop_after_stage=stop_after_stage,
    )


def main():
    os.environ["LRT_CINEMA_BACKEND"] = "numpy"
    nef, dcp = fm.NEF, fm.DCP

    def rf(m):
        return (f"fringe_hp={m['fringe_hp']:6.2f} fringe_b={m['fringe_b']:6.2f} "
                f"fringe_a={m['fringe_a']:6.2f} n={m['n_mask']}")

    # tap-7 linear (fringe lives above 1.0; overrange preserved). Baseline (no inpaint)
    # then inpainted, PINNED to the baseline mask so we measure the same pixels.
    print("=== CFA same-channel clip-inpaint, tap-7 linear, rcd ===")
    pp_base = render_inpainted(nef, dcp, stop_after_stage=7, do_inpaint=False)
    pp_inp = render_inpainted(nef, dcp, stop_after_stage=7, do_inpaint=True)

    for c, (y, x, s) in fm.CROPS.items():
        sub_base = pp_base[y:y + s, x:x + s]
        sub_inp = pp_inp[y:y + s, x:x + s]
        mask = fm.prophoto_clip_edge_mask(sub_base)  # PIN to baseline
        mb = fm.fringe_metrics_prophoto(sub_base, pinned_mask=mask)
        mi = fm.fringe_metrics_prophoto(sub_inp, pinned_mask=mask)
        drop = 100 * (1 - mi["fringe_b"] / mb["fringe_b"]) if mb["fringe_b"] else 0.0
        print(f"\n{c}:")
        print(f"  base    {rf(mb)}")
        print(f"  inpaint {rf(mi)}   fringe_b drop = {drop:.0f}%")

    # Save inpainted vs base crops (sRGB) for owner eyeball — render to tap9 sRGB.
    import pathlib
    out = pathlib.Path("/tmp/fringe_crops")
    pp9_base = render_inpainted(nef, dcp, stop_after_stage=9, do_inpaint=False)
    pp9_inp = render_inpainted(nef, dcp, stop_after_stage=9, do_inpaint=True)
    for c, (y, x, s) in fm.CROPS.items():
        fm.save_crop(fm.prophoto_to_srgb_8bit(pp9_base), (y, x, s), out / f"cfainpaint_BASE_{c}.png")
        fm.save_crop(fm.prophoto_to_srgb_8bit(pp9_inp), (y, x, s), out / f"cfainpaint_INPAINT_{c}.png")
    print("\nsaved cfainpaint BASE/INPAINT crops to /tmp/fringe_crops/")


if __name__ == "__main__":
    main()
