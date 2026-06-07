#!/usr/bin/env python3
"""Adversarially falsify "real AMaZE is fundamental (~0.56, not 0.28)".

The dangerous false-null is a CRIPPLED AMaZE: if the CFA scale / clip_pt / pattern
are wrong, AMaZE's adaptive false-colour suppression silently disables and it looks
fundamental when it isn't. Probes:

  P1  clip_pt sweep. AMaZE's suppression is gated on clip_pt/clip_pt8 comparisons
      against the [0,1] cfa. If clip_pt is LIVE, sweeping it must MOVE the blinds
      chroma-HF. A flat response = dead thresholds = crippled.
  P2  scale robustness. Our CFA is [0,1] (darktable port's native scale). Feed it
      ALSO at the chroma-identical scale and confirm the demosaic geometry is sane
      (green channel correlates with the green CFA sites). A broken demosaic would
      not.
  P3  is it really demosaicing? per-channel stats + a tiny known-pattern check.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from amaze_blinds import (  # noqa: E402
    _load_ops,
    chroma_hf,
    extract_cfa,
    our_rcd_camera_rgb,
    render_with_injected_rgb,
    run_amaze,
)


def main() -> int:
    cfa, pattern = extract_cfa()
    ops = _load_ops()
    print(f"CFA {cfa.shape} pattern={pattern} range=[{cfa.min():.3f},{cfa.max():.3f}]")

    # ---- P1: clip_pt sweep (prove suppression is LIVE) ----
    print("\n[P1] clip_pt sweep — a live threshold MUST move the result:")
    for cp in (0.01, 0.1, 0.5, 1.0, 4.0, 100.0):
        rgb = run_amaze(cfa, pattern, clip_pt=cp)
        srgb = render_with_injected_rgb(rgb, ops)
        hf = chroma_hf(srgb, 0)
        print(f"   clip_pt={cp:>7}  blinds chroma-HF={hf:.4f}  "
              f"rgb_range=[{rgb.min():.3f},{rgb.max():.3f}]")

    # ---- P2: scale robustness (feed CFA*K, divide out; verdict must not depend
    #      on absolute scale because clip_pt scales WITH it) ----
    print("\n[P2] scale robustness — scale CFA by K, set clip_pt=K (white stays at"
          " the threshold). Result must be ~invariant:")
    for K in (0.5, 1.0, 100.0, 65535.0):
        rgb = run_amaze((cfa * K).astype(np.float32), pattern, clip_pt=float(K))
        rgb = rgb / K  # back to normalized for the identical finish
        srgb = render_with_injected_rgb(rgb.astype(np.float32), ops)
        hf = chroma_hf(srgb, 0)
        print(f"   K={K:>9}  clip_pt={K:>9}  blinds chroma-HF={hf:.4f}")

    # ---- P3: is it really a demosaic? green plane must equal the CFA at green
    #      sites (RGGB: green at (0,1) and (1,0)); red/blue interpolated there ----
    print("\n[P3] demosaic sanity — AMaZE green must MATCH the CFA at green sites:")
    rgb = run_amaze(cfa, pattern, clip_pt=1.0)
    # RGGB green sites: (even row, odd col) and (odd row, even col)
    gmask = np.zeros(cfa.shape, bool)
    gmask[0::2, 1::2] = True
    gmask[1::2, 0::2] = True
    g_at_green = rgb[..., 1][gmask]
    cfa_at_green = cfa[gmask]
    resid = np.abs(g_at_green - cfa_at_green)
    print(f"   |amaze_G - cfa_G| at green sites: mean={resid.mean():.2e} "
          f"max={resid.max():.2e}  (≈0 ⇒ true demosaic, green passthrough)")
    # red at red sites (0,0) must match cfa; blue at blue sites (1,1) must match
    rmask = np.zeros(cfa.shape, bool)
    rmask[0::2, 0::2] = True
    bmask = np.zeros(cfa.shape, bool)
    bmask[1::2, 1::2] = True
    rr = np.abs(rgb[..., 0][rmask] - cfa[rmask])
    bb = np.abs(rgb[..., 2][bmask] - cfa[bmask])
    print(f"   |amaze_R - cfa_R| at red sites:   mean={rr.mean():.2e} max={rr.max():.2e}")
    print(f"   |amaze_B - cfa_B| at blue sites:  mean={bb.mean():.2e} max={bb.max():.2e}")

    # ---- cross-check vs our RCD's own passthrough (same expectation) ----
    rcd = our_rcd_camera_rgb(cfa, pattern)
    rcd_resid = np.abs(rcd[..., 1][gmask] - cfa_at_green)
    print(f"\n   (cf. our RCD green passthrough residual mean={rcd_resid.mean():.2e})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
