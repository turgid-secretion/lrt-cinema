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

_VALID = ("numpy", "numba", "mlx", "auto")


class MlxUnsupported(Exception):
    """A render config is outside the MLX GPU fast path (non-FM profile, no
    ProfileToneCurve, non-faithful intent, non-sRGB target). Caller falls back
    to numpy/numba. Defined here (not in `_mlx_kernels`) so it can be caught
    without importing mlx."""
_kernel_mod: Any = None  # lazily imported kernel module (None until first use)
_numba_probe: bool | None = None  # cached "is numba importable?"
_mlx_probe: bool | None = None    # cached "is mlx importable?"
_mlx_renderer: Any = None         # cached MlxFaithfulRenderer (keyed by profile id)
_mlx_renderer_key: Any = None


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


def mlx_available() -> bool:
    """True if mlx.core imports (Apple-Silicon Metal GPU). Cached."""
    global _mlx_probe
    if _mlx_probe is None:
        try:
            import mlx.core  # noqa: F401
            _mlx_probe = True
        except Exception:
            _mlx_probe = False
    return _mlx_probe


def resolve_backend(name: str | None = None) -> str:
    """Resolve the active backend to a concrete ``"numpy"`` / ``"numba"`` / ``"mlx"``.

    `name` (or `$LRT_CINEMA_BACKEND`, default ``"numpy"``) → ``"auto"`` picks
    numba-if-available (the CPU path that covers every preset/intent and is the
    bit-tightest match to numpy; ``mlx`` is GPU and opt-in — it covers only the
    faithful sRGB-TIFF path). Raises ValueError on an unknown name, and on an
    explicit ``numba``/``mlx`` request when that engine is not importable (fail
    loud — never silently fall back when the caller asked for a specific one)."""
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
    if name == "mlx" and not mlx_available():
        raise ValueError(
            "backend 'mlx' requested but mlx is not importable; "
            "`pip install mlx` (Apple Silicon) or use 'numba' / 'numpy' / 'auto'.",
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


# --- Stage 1: RCD-family demosaic ------------------------------------------


def rcd_demosaic(cfa: np.ndarray, pattern: str, *,
                 backend: str | None = None) -> np.ndarray:
    """RCD-family Bayer demosaic (Stage 1) — backend-dispatched.

    `cfa` (H, W) single-channel mosaic; `pattern` the 2×2 phase. Returns
    (H, W, 3) float, finite + non-negative (highlights uncapped).

    NUMBA PATH: the bit-faithful numba twin (`_numba_kernels.rcd_rggb_refined`)
    reproduces the CURRENT numpy reference (`_rcd_demosaic._rcd_rggb`: RCD green +
    Menon directional R/B + chroma-gated a-posteriori refining — the quality path
    that carries the demosaic battery's 39.03 CPSNR). The a-posteriori direction
    `m_dir = (dd_v >= dd_h)` is split: its continuous front half (the colour-
    difference directional-gradient planes d_h/d_v) is the numba kernel
    `menon_dplanes` — bit-identical to the reference's d-planes — and its ONE discrete
    branch is decided by the shared scipy `_menon_decide`, which stays in scipy on
    both backends (its n-D-correlate FP reduction is SIMD-vectorised, not
    scalar-reproducible; a 1-ULP flip would pick the opposite reconstruction).
    Bit-identical d-planes through the same convolve ⇒ a bit-identical m_dir;
    everything the kernel computes downstream is continuous (contractive averaging
    FIRs), so end-to-end parity vs the reference is ~1e-14 (under the test's 1e-9
    tolerance). See the kernel's design note.

    Both branches share the reference's validate / flip / pad / crop / clamp
    wrapper: `backend="numpy"` (the default / fallback) IS the literal reference;
    `backend="numba"` reuses the reference's guards, phase flips, reflect-pad and the
    scipy `_menon_decide`, swaps the padded RGGB core for the kernel (and the m_dir
    d-planes for `menon_dplanes`), then crops / unflips / clamps identically. `"mlx"`
    falls back to the reference (no GPU RCD)."""
    from lrt_cinema import _rcd_demosaic as ref
    if resolve_backend(backend) != "numba":
        return ref.rcd_demosaic(cfa, pattern)

    # --- numba path: the reference wrapper with the kernel as the core ---
    from lrt_cinema.accel._numba_kernels import menon_dplanes, rcd_rggb_refined

    if pattern not in ref._VALID_PATTERNS:
        raise ValueError(
            f"pattern must be one of {ref._VALID_PATTERNS}, got {pattern!r}"
        )
    cfa = np.asarray(cfa)
    if cfa.ndim != 2:
        raise ValueError(f"cfa must be 2-D (H, W), got shape {cfa.shape}")
    if cfa.shape[0] % 2 or cfa.shape[1] % 2:
        raise ValueError(
            f"cfa dimensions must be even for Bayer phase mapping, got {cfa.shape}"
        )

    out_float32 = cfa.dtype == np.float32
    work = cfa.astype(np.float64, copy=False)

    flip_rows, flip_cols = ref._PHASE_FLIP[pattern]
    if flip_rows:
        work = work[::-1, :]
    if flip_cols:
        work = work[:, ::-1]

    padded = np.pad(work, ref._PAD, mode="reflect")
    padded_c = np.ascontiguousarray(padded)
    # The a-posteriori direction `m_dir`: its continuous front half (the colour-
    # difference directional-gradient planes d_h/d_v) is the numba kernel
    # `menon_dplanes` — bit-identical to the numpy reference's d-planes — and its ONE
    # discrete branch (`dd_v >= dd_h`) is decided by the SHARED scipy homogeneity
    # convolve `_menon_decide`, which stays in scipy on both backends (its n-D-correlate
    # FP reduction is SIMD-vectorised and not reproducible bit-for-bit in a scalar
    # kernel; a 1-ULP flip would pick the opposite H/V reconstruction). Bit-identical
    # d-planes through the same convolve ⇒ a bit-identical m_dir by construction.
    d_h, d_v = menon_dplanes(padded_c)
    m_dir = np.ascontiguousarray(ref._menon_decide(d_h, d_v))
    rgb_padded = rcd_rggb_refined(
        padded_c, m_dir,
        ref._REFINE_ITERS, ref._CHROMA_THR, ref._CHROMA_SOFT,
    )
    rgb = rgb_padded[ref._PAD:-ref._PAD, ref._PAD:-ref._PAD, :]

    if flip_cols:
        rgb = rgb[:, ::-1, :]
    if flip_rows:
        rgb = rgb[::-1, :, :]

    rgb = np.ascontiguousarray(rgb)
    np.clip(rgb, 0.0, None, out=rgb)
    return rgb.astype(np.float32) if out_float32 else rgb


# --- Whole-frame MLX (Metal GPU) render path -------------------------------


def _get_mlx_renderer(profile, cache_key=None):
    """Cache an `MlxFaithfulRenderer` per process. Uploads the frame-invariant
    GPU constants (cube, tone LUT, matrices) once, so a sequence reuses them.

    `cache_key` must be STABLE across frames in a process (e.g. the DCP path) —
    a pool worker re-parses the profile every frame, so keying on `id(profile)`
    would rebuild the renderer each time (the cache would never hit). Falls back
    to `id(profile)` when no key is given. Raises `MlxUnsupported` for a profile
    outside the fast path."""
    global _mlx_renderer, _mlx_renderer_key
    key = cache_key if cache_key is not None else id(profile)
    if _mlx_renderer_key != key or _mlx_renderer is None:
        from lrt_cinema.accel._mlx_kernels import MlxFaithfulRenderer
        _mlx_renderer = MlxFaithfulRenderer(profile)
        _mlx_renderer_key = key
    return _mlx_renderer


def mlx_render_frame_to_srgb(raw_path, profile, develop_ops=None,
                             dcp_path=None, preview_scale: int = 1) -> np.ndarray:
    """Full FAITHFUL sRGB colour render on the Metal GPU — one upload, one
    download (decode → stages 2-9 → Stage-11 → Stage-12 faithful → sRGB encode).

    Returns the display-encoded sRGB float array (H, W, 3) in [0, 1] (the writer
    quantises it). Mirrors `pipeline.render_frame`'s preamble (decode + scene
    kelvin + baseline exposure + black-render) then runs the whole colour path on
    the GPU via `MlxFaithfulRenderer`. Raises `MlxUnsupported` for a profile/
    config outside the fast path — the caller (cli worker) then falls back to the
    numpy/numba per-stage path. FAITHFUL intent only (the perceptual EXR path is
    not ported)."""
    from lrt_cinema import pipeline as P
    from lrt_cinema.ir import DevelopOps
    ops = develop_ops if develop_ops is not None else DevelopOps()
    # Key the renderer cache on the DCP path (stable across a worker's frames,
    # unlike the per-call-re-parsed profile object) so a sequence reuses the
    # uploaded constants. None → falls back to id(profile) (single-frame use).
    renderer = _get_mlx_renderer(
        profile, cache_key=(str(dcp_path) if dcp_path is not None else None),
    )  # may raise MlxUnsupported
    # WB resolved BEFORE decode so the demosaic pre-conditioning uses the
    # final render neutral (H1 fix — mirrors pipeline.render_frame). Also
    # passes the develop TINT, which this path previously dropped.
    scene_kelvin = P.DEFAULT_SCENE_KELVIN
    override_asn = None
    if ops.temperature_k is not None:
        scene_kelvin = float(ops.temperature_k)
        override_asn = P.kelvin_to_neutral(
            profile, scene_kelvin, float(ops.tint or 0.0),
        )
    cam, cam_asn = P._decode_raw(
        raw_path, half_size=(preview_scale >= 2), wb_asn=override_asn,
    )
    asn = override_asn if override_asn is not None else cam_asn
    if preview_scale >= 2:
        cam = P._block_downsample(cam, preview_scale // 2)
    dng_be = P.read_dng_baseline_exposure(raw_path)
    dbr = P.read_dcp_default_black_render(dcp_path) if dcp_path is not None else 0
    return renderer.render(cam, asn, scene_kelvin, ops, dng_be, dbr)


# --- Stage 12 (faithful) grade ops — backend-dispatched -------------------
#
# Called by develop_ops.apply_{saturation,vibrance,hsl,color_grade} AFTER their
# byte-exact identity short-circuit, so these always see a non-identity op. The
# numpy branch calls the develop_ops reference body (no recursion); the numba
# branch calls the fused kernel. 'mlx' resolves to the numpy branch here — the
# MLX whole-frame renderer does Stage 12 itself, it never calls these.


def _np2d(rgb: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(rgb, dtype=np.float32).reshape(-1, 3)


def apply_saturation(rgb: np.ndarray, sat: float, *, backend: str | None = None) -> np.ndarray:
    """LR Saturation (HSV S-multiplier), backend-dispatched."""
    if resolve_backend(backend) == "numba":
        out = _kernels().saturation_hsv(_np2d(rgb), 1.0 + sat / 100.0)
        return out.reshape(rgb.shape)
    from lrt_cinema.develop_ops import _scale_hsv_saturation
    mult = 1.0 + sat / 100.0
    return _scale_hsv_saturation(rgb, lambda s: np.clip(s * mult, 0.0, 1.0))


def apply_vibrance(rgb: np.ndarray, vib: float, *, backend: str | None = None) -> np.ndarray:
    """LR Vibrance (non-linear HSV S-boost), backend-dispatched."""
    if resolve_backend(backend) == "numba":
        out = _kernels().vibrance_hsv(_np2d(rgb), vib / 100.0)
        return out.reshape(rgb.shape)
    from lrt_cinema.develop_ops import _scale_hsv_saturation
    k = vib / 100.0
    return _scale_hsv_saturation(rgb, lambda s: np.clip(s + k * s * (1.0 - s), 0.0, 1.0))


def apply_hsl(rgb: np.ndarray, hsl, *, backend: str | None = None) -> np.ndarray:
    """LR HSL (8 hue bands × H/S/L), backend-dispatched."""
    if resolve_backend(backend) == "numba":
        from lrt_cinema.develop_ops import (
            _HSL_BAND_CENTERS_HEX,
            _HSL_HUE_MAX_HEX,
            _HSL_LUM_SAT_GATE,
        )
        lo = np.asarray(_HSL_BAND_CENTERS_HEX, dtype=np.float64)
        hi = np.concatenate([lo[1:], [6.0]]).astype(np.float64)
        nxt = np.array([(j + 1) % 8 for j in range(8)], dtype=np.int64)
        hue_pb = np.asarray(hsl.hue, dtype=np.float64) / 100.0 * _HSL_HUE_MAX_HEX
        sat_pb = 1.0 + np.asarray(hsl.saturation, dtype=np.float64) / 100.0
        lum_pb = 1.0 + np.asarray(hsl.luminance, dtype=np.float64) / 100.0
        out = _kernels().hsl_bands(
            _np2d(rgb), hue_pb, sat_pb, lum_pb, lo, hi, nxt, float(_HSL_LUM_SAT_GATE),
        )
        return out.reshape(rgb.shape)
    from lrt_cinema.develop_ops import _hsl_numpy
    return _hsl_numpy(rgb, hsl)


def apply_color_grade(rgb: np.ndarray, cg, *, backend: str | None = None) -> np.ndarray:
    """LR Color Grade (luminance-masked split-tone), backend-dispatched."""
    if resolve_backend(backend) == "numba":
        from lrt_cinema.develop_ops import _PROPHOTO_LUMINANCE, _color_grade_wheel_tint
        tsh = np.ascontiguousarray(_color_grade_wheel_tint(cg.shadow_hue, cg.shadow_sat, cg.shadow_lum))
        tmid = np.ascontiguousarray(_color_grade_wheel_tint(cg.midtone_hue, cg.midtone_sat, cg.midtone_lum))
        thi = np.ascontiguousarray(_color_grade_wheel_tint(cg.highlight_hue, cg.highlight_sat, cg.highlight_lum))
        tg = np.ascontiguousarray(_color_grade_wheel_tint(cg.global_hue, cg.global_sat, cg.global_lum))
        gamma_balance = 2.0 ** (-cg.balance / 100.0)
        p = 1.0 + 2.0 * (1.0 - min(max(cg.blending, 0.0), 100.0) / 100.0)
        lum_row = np.ascontiguousarray(_PROPHOTO_LUMINANCE, dtype=np.float64)
        out = _kernels().color_grade(_np2d(rgb), tsh, tmid, thi, tg, lum_row,
                                     float(gamma_balance), float(p))
        return out.reshape(rgb.shape)
    from lrt_cinema.develop_ops import _color_grade_numpy
    return _color_grade_numpy(rgb, cg)
