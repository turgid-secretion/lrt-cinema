#!/usr/bin/env python3
"""Per-frame render benchmark + numpy↔accelerated ΔE-equivalence guard.

Two jobs, one tool:

  * **bench** — wall-clock s/frame + frames/s for the full-quality faithful
    TIFF render, at full res and at proxy scales, for a chosen backend. The
    repeatable perf-regression seed: run on `main`, run on a branch, diff.
  * **verify** — render the SAME frame twice (numpy reference vs the
    accelerated backend) and report max |Δcode16| + ΔE2000 (mean / max / worst
    pixel). This is the PRIMARY local correctness guard: the gym/rose ΔE ship
    gate needs `/tmp/dng_out` fixtures + the system DCP and skips on most boxes,
    but numpy-vs-accelerated equivalence needs only one real DNG + the profile,
    so it runs anywhere. Target: max ΔE2000 << the 1.0 gate floor (< 0.01).

The full-quality fast path must stay colour-identical (CLAUDE.md ship gate);
the proxy path is preview-only and ΔE-exempt (it renders fewer pixels by design).

Usage:
    python3 tools/perf/bench_render.py bench \\
        --dng /tmp/dng_out/DSC_4053_dnglab.dng \\
        --dcp "<system CameraRaw D750 DCP>" \\
        --backend numpy --scales 1,2,4,8 --repeat 2
    python3 tools/perf/bench_render.py verify \\
        --dng /tmp/dng_out/DSC_4053_dnglab.dng --dcp <profile.npz> --backend numba

NOT a CI test (renders full-res frames; needs a real DNG + a profile).
"""

from __future__ import annotations

import argparse
import inspect
import os
import sys
import time
from pathlib import Path

import numpy as np

_DEFAULT_DNG = Path("/tmp/dng_out/DSC_4053_dnglab.dng")
_DEFAULT_DCP = Path("/Library/Application Support/Adobe/CameraRaw/"
                    "CameraProfiles/Camera/Nikon D750/"
                    "Nikon D750 Camera Standard.dcp")


def _load_profile(dcp: Path):
    from lrt_cinema.dcp import load_profile, parse_dcp
    return load_profile(dcp) if dcp.suffix.lower() == ".npz" else parse_dcp(dcp)


def _render_array(dng: Path, profile, *, scale: int, backend: str, intent):
    """Render one DNG to a display-encoded float [0,1] array (pre-quantise).

    Routes `preview_scale` / `backend` through `render_frame` only if that
    build accepts them (signature-filtered), so this same harness measures the
    baseline commit and the accelerated commit unchanged. Returns the encoded
    float array the TIFF writer would quantise (so ΔE is measured on the actual
    delivered colours, before integer rounding)."""
    os.environ["LRT_CINEMA_BACKEND"] = backend
    from lrt_cinema.ir import DevelopOps

    # mlx runs the WHOLE faithful sRGB render on the GPU (one upload/download),
    # so exercise that path directly rather than render_frame's per-stage path
    # (which would resolve 'mlx'→numpy). FAITHFUL sRGB only.
    if backend == "mlx":
        from lrt_cinema import accel
        return accel.mlx_render_frame_to_srgb(
            dng, profile, develop_ops=DevelopOps(), preview_scale=scale,
        )

    from lrt_cinema.develop_ops import apply_develop_ops
    from lrt_cinema.output import _prophoto_to_display
    from lrt_cinema.pipeline import render_frame

    kwargs = {}
    sig = inspect.signature(render_frame).parameters
    if "preview_scale" in sig:
        kwargs["preview_scale"] = scale
    elif scale != 1:
        raise SystemExit("this build has no preview_scale support; use --scales 1")

    res = render_frame(dng, profile, dcp_path=None, develop_ops=DevelopOps(), **kwargs)
    pp = apply_develop_ops(res.prophoto, DevelopOps(), intent)
    return _prophoto_to_display(pp, "srgb")


def _delta_e_stats(a: np.ndarray, b: np.ndarray) -> dict:
    """ΔE2000 between two display-encoded sRGB float arrays (same shape).

    Both are sRGB-encoded [0,1]; decode → XYZ → Lab → ΔE2000 per pixel. Heavy
    (full-frame), but this is a guard, not a hot path. Reports mean/max + the
    worst pixel's location and the two colours there, so a divergence can be
    traced to a hue-wrap / cube-edge / channel-tie rather than dismissed."""
    import colour

    srgb = colour.RGB_COLOURSPACES["sRGB"]
    flat_a = a.reshape(-1, 3).astype(np.float64)
    flat_b = b.reshape(-1, 3).astype(np.float64)
    xyz_a = colour.RGB_to_XYZ(flat_a, srgb, apply_cctf_decoding=True)
    xyz_b = colour.RGB_to_XYZ(flat_b, srgb, apply_cctf_decoding=True)
    lab_a = colour.XYZ_to_Lab(xyz_a, illuminant=srgb.whitepoint)
    lab_b = colour.XYZ_to_Lab(xyz_b, illuminant=srgb.whitepoint)
    de = colour.delta_E(lab_a, lab_b, method="CIE 2000")
    worst = int(np.argmax(de))
    code_a = np.clip(a, 0, 1) * 65535.0 + 0.5
    code_b = np.clip(b, 0, 1) * 65535.0 + 0.5
    max_code = float(np.max(np.abs(code_a.astype(np.int64) - code_b.astype(np.int64))))
    h, w, _ = a.shape
    return {
        "mean_de": float(np.mean(de)),
        "max_de": float(np.max(de)),
        "p999_de": float(np.percentile(de, 99.9)),
        "max_code16": max_code,
        "worst_xy": (worst // w % h, worst % w),
        "worst_a": flat_a[worst].tolist(),
        "worst_b": flat_b[worst].tolist(),
    }


def _cmd_bench(a: argparse.Namespace) -> int:
    profile = _load_profile(a.dcp)
    from lrt_cinema.ir import RenderIntent
    intent = RenderIntent(a.intent)
    scales = [int(s) for s in a.scales.split(",")]
    print(f"=== bench: {a.dng.name}  backend={a.backend}  intent={a.intent}  "
          f"repeat={a.repeat} (1st=cold) ===")
    print(f"{'scale':>5} {'megapixels':>11} {'cold_s':>8} {'warm_s':>8} {'fps':>7}")
    base_s = None
    for scale in scales:
        times = []
        shape = None
        for _ in range(a.repeat):
            t0 = time.perf_counter()
            out = _render_array(a.dng, profile, scale=scale, backend=a.backend, intent=intent)
            times.append(time.perf_counter() - t0)
            shape = out.shape
        cold = times[0]
        warm = min(times[1:]) if len(times) > 1 else times[0]
        mp = shape[0] * shape[1] / 1e6
        print(f"{scale:>5} {mp:>11.2f} {cold:>8.2f} {warm:>8.2f} {1.0 / warm:>7.2f}")
        if scale == 1:
            base_s = warm
    if base_s is not None:
        print(f"\nfull-res warm s/frame = {base_s:.2f}  ({1.0 / base_s:.3f} fps single-frame)")
    return 0


def _cmd_verify(a: argparse.Namespace) -> int:
    profile = _load_profile(a.dcp)
    from lrt_cinema.ir import RenderIntent
    intent = RenderIntent(a.intent)
    print(f"=== verify: numpy (reference) vs {a.backend} — full-quality, scale 1 ===")
    print(f"frame: {a.dng.name}  intent={a.intent}")
    t0 = time.perf_counter()
    ref = _render_array(a.dng, profile, scale=1, backend="numpy", intent=intent)
    t_ref = time.perf_counter() - t0
    t0 = time.perf_counter()
    acc = _render_array(a.dng, profile, scale=1, backend=a.backend, intent=intent)
    t_acc = time.perf_counter() - t0
    if ref.shape != acc.shape:
        print(f"FAIL: shape mismatch {ref.shape} vs {acc.shape}")
        return 1
    st = _delta_e_stats(ref, acc)
    print(f"\n  numpy reference : {t_ref:6.2f} s")
    print(f"  {a.backend:<15s} : {t_acc:6.2f} s   ({t_ref / t_acc:.1f}x)")
    print("\n  ΔE2000 (numpy vs accelerated, full faithful TIFF render):")
    print(f"    mean      = {st['mean_de']:.2e}")
    print(f"    p99.9     = {st['p999_de']:.2e}")
    print(f"    MAX       = {st['max_de']:.2e}   at pixel {st['worst_xy']}")
    print(f"      ref colour {[f'{c:.5f}' for c in st['worst_a']]}")
    print(f"      acc colour {[f'{c:.5f}' for c in st['worst_b']]}")
    print(f"    max|Δcode16| = {st['max_code16']:.0f} (of 65535)")
    ok = st["max_de"] < a.tol
    print(f"\n  === {'PASS' if ok else 'FAIL'}: max ΔE2000 {st['max_de']:.2e} "
          f"{'<' if ok else '>='} tol {a.tol} ===")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("bench", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--dng", type=Path, default=_DEFAULT_DNG)
        p.add_argument("--dcp", type=Path, default=_DEFAULT_DCP)
        p.add_argument("--backend", default="numpy")
        p.add_argument("--intent", default="faithful", choices=("faithful", "perceptual"))
    sub.choices["bench"].add_argument("--scales", default="1")
    sub.choices["bench"].add_argument("--repeat", type=int, default=2)
    sub.choices["verify"].add_argument("--tol", type=float, default=0.01)
    a = ap.parse_args(argv)
    if not a.dng.is_file():
        print(f"error: DNG not found: {a.dng}", file=sys.stderr)
        return 2
    if not a.dcp.is_file():
        print(f"error: DCP/profile not found: {a.dcp}", file=sys.stderr)
        return 2
    if a.cmd == "bench":
        return _cmd_bench(a)
    return _cmd_verify(a)


if __name__ == "__main__":
    sys.exit(main())
