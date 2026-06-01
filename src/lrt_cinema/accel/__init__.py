"""Pluggable compute backend for the per-pixel render hotspots.

The render maths is pure-numpy by default — that path is the **reference** the
ΔE ship gate measures and the universal fallback (no extra dependency, runs in
CI and on any box). This package adds an optional **numba** backend: fused,
multi-core JIT ports of the three hottest per-pixel stages (HSV cube, tone
curve), gated behind a try-import so numpy stays the only hard requirement.

Selection — `resolve_backend()` / the `LRT_CINEMA_BACKEND` env var:

  * unset / ``"numpy"`` → numpy reference (the default; keeps the ship gate on
    the reference path by construction),
  * ``"numba"``        → numba kernels (error if numba is not importable),
  * ``"auto"``         → numba if importable, else numpy.

The CLI exposes ``--backend`` (default ``auto`` — fast when numba is present,
correct everywhere) and sets the env var for its worker processes. Tests and
direct library use get numpy unless they opt in. The accelerated path is held
to **max ΔE2000 < 0.01 vs numpy** on a real frame (``tools/perf/bench_render.py
verify``) — far below the 1.0 ship gate — and to numpy-twin equivalence on
synthetic pixels (``tests/test_accel_kernels.py``).

Dispatch entry points (called by `pipeline` / future perceptual ops):
  * `apply_hsv_cube_rgb(rgb, cube, meta)` — Stage 5 HueSatMap / Stage 8 LookTable
  * `apply_rgb_tone(rgb, curve)`          — Stage 9 hue-preserving tone curve
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np

_VALID = ("numpy", "numba", "auto")
_kernel_mod: Any = None  # lazily imported kernel module (None until first use)
_numba_probe: bool | None = None  # cached "is numba importable?"


def numba_available() -> bool:
    """True if numba imports on this interpreter. Cached after the first probe."""
    global _numba_probe
    if _numba_probe is None:
        try:
            import numba  # noqa: F401
            _numba_probe = True
        except Exception:
            _numba_probe = False
    return _numba_probe


def resolve_backend(name: str | None = None) -> str:
    """Resolve the active backend to a concrete ``"numpy"`` or ``"numba"``.

    `name` (or `$LRT_CINEMA_BACKEND`, default ``"numpy"``) → ``"auto"`` picks
    numba-if-available. Raises ValueError on an unknown name, and on an explicit
    ``"numba"`` request when numba is not importable (fail loud, never silently
    fall back when the caller asked for numba)."""
    if name is None:
        name = os.environ.get("LRT_CINEMA_BACKEND", "numpy")
    name = name.lower()
    if name not in _VALID:
        raise ValueError(f"backend must be one of {_VALID}, got {name!r}")
    if name == "auto":
        return "numba" if numba_available() else "numpy"
    if name == "numba" and not numba_available():
        raise ValueError(
            "backend 'numba' requested but numba is not importable; "
            "`pip install numba` or use backend 'numpy' / 'auto'.",
        )
    return name


def _kernels() -> Any:
    """Import (once) and return the numba kernel module."""
    global _kernel_mod
    if _kernel_mod is None:
        import lrt_cinema.accel._numba_kernels as k
        _kernel_mod = k
    return _kernel_mod


def set_threads(n: int) -> None:
    """Cap numba's intra-frame thread pool to `n` (no-op without numba).

    Used to reconcile intra-frame parallelism with the frame-level ProcessPool:
    N workers × 1 thread for sequence throughput, 1 worker × all-cores for
    single-frame latency / preview. Clamped to numba's launch maximum."""
    if not numba_available():
        return
    import numba
    n = max(1, min(int(n), numba.config.NUMBA_NUM_THREADS))
    numba.set_num_threads(n)


# --- Stage 5 / 8: HSV cube --------------------------------------------------


def apply_hsv_cube_rgb(rgb: np.ndarray, cube: np.ndarray, meta, *,
                       backend: str | None = None) -> np.ndarray:
    """Apply an HSV cube (HueSatMap or LookTable) to linear-ProPhoto `rgb`.

    `rgb` (H, W, 3) float; `cube` (V, H, S, 3); `meta` carries hue/sat/val
    divisions + `srgb_gamma`. Returns (H, W, 3). Composes RGB→HSV, the trilinear
    cube sample, HSV→RGB, and the negative-component passthrough into one result
    — identical maths on both backends (the numpy branch is the literal Stage-8
    reference; the numba branch is its fused single-pass twin)."""
    be = resolve_backend(backend)
    if be == "numba":
        cube_c = np.ascontiguousarray(cube, dtype=np.float32)
        rgb_c = np.ascontiguousarray(rgb, dtype=np.float32).reshape(-1, 3)
        out = _kernels().lut_cube_rgb(
            rgb_c, cube_c,
            int(meta.hue_divisions), int(meta.sat_divisions),
            int(meta.val_divisions), bool(meta.srgb_gamma),
        )
        return out.reshape(rgb.shape)
    # numpy reference (the exact current Stage-5/8 composition).
    from lrt_cinema.lut3d_baker import (
        _apply_hsv_cube,
        _hsv_to_rgb_dcp,
        _rgb_to_hsv_dcp,
    )
    h_arr, s_arr, v_arr, valid = _rgb_to_hsv_dcp(rgb)
    h_arr, s_arr, v_arr = _apply_hsv_cube(h_arr, s_arr, v_arr, cube, meta)
    rgb_post = _hsv_to_rgb_dcp(h_arr, s_arr, v_arr)
    return np.where(valid[..., None], rgb_post, rgb)


# --- Stage 9: hue/saturation-preserving tone curve -------------------------


def apply_rgb_tone(rgb: np.ndarray, curve: Any, *,
                   backend: str | None = None) -> np.ndarray:
    """Adobe `RefBaselineRGBTone` (Stage 9), backend-dispatched.

    `curve` is either a `DngSplineSolver` (the profile tone curve — accelerated
    on numba via the solved X/Y/S arrays) or a plain vectorised callable (the
    ACR3 default fallback, which stays on the numpy reference on both backends —
    it is a cheap 1025-pt LUT, not a hotspot). The numpy branch is the literal
    `pipeline.apply_rgb_tone` reference."""
    from lrt_cinema.pipeline import DngSplineSolver
    from lrt_cinema.pipeline import apply_rgb_tone as _np_tone
    be = resolve_backend(backend)
    if be == "numba" and isinstance(curve, DngSplineSolver):
        rgb_c = np.ascontiguousarray(rgb, dtype=np.float32).reshape(-1, 3)
        out = _kernels().rgb_tone_spline(rgb_c, curve.X, curve.Y, curve.S)
        return out.reshape(rgb.shape)
    # numpy reference: a DngSplineSolver exposes `.evaluate`; ACR3 is a callable.
    eval_fn = curve.evaluate if isinstance(curve, DngSplineSolver) else curve
    return _np_tone(rgb, eval_fn)
