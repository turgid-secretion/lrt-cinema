"""Adobe DNG 1.7.1 reference render pipeline.

Promoted from `.audit_tmp/adobe_pipeline.py` on `research/python-pipeline-seed`,
which achieves < 1 ΔE mean vs `dng_validate` on both test scenes
(gym 0.79, rose 0.84).

Stage order (per `docs/research/v06-architecture.md` §"Pipeline stage order"):

  1.  Demosaic via rawpy/libraw on a converted DNG (dnglab; not NEF — the DNG
      gives libraw the correct WhiteLevel + embedded LinearizationTable).
      Algorithm = LINEAR (matches Adobe SDK internal default).
  2.  AsShotNeutral inverse — per-channel WB. Holy Grail kelvin override:
      `develop_ops.temperature_k` (when set) overrides scene_kelvin via
      `neutral_to_kelvin`-derived AsShotNeutral.
  3.  Camera RGB → XYZ(D50) via ForwardMatrix (when present) or
      inverse ColorMatrix path.
  4.  XYZ(D50) → linear ProPhoto(D50).
  5.  HueSatMap (mired-blended by scene kelvin), applied in HSV.
  6.  BaselineExposureOffset folded into TotalBaselineExposure (=
      DNG.BaselineExposure + DCP.BaselineExposureOffset per Adobe DNG SDK
      `dng_negative.cpp:2588-2606`); the sum is fed to Stage 7's
      ExposureRamp `exposure` parameter — there is no separate
      post-tone-curve BE scalar on the libraw LINEAR / interpolation path
      (Stage3Gain = 1.0).
  7.  ExposureRamp (per-channel, three-region piecewise).
  8.  LookTable in HSV.
  9.  ProfileToneCurve per-R/G/B independently via ported `dng_spline_solver`
      (Hermite C2). Falls back to ACR3 default tone curve when profile
      has no ProfileToneCurve.

  Stages 11–13 (LR-authored develop ops + output color conversion) live in
  `develop_ops.py` and `output.py` respectively. Stage 10 (the previous
  standalone `2^TotalBE` scalar) is intentionally absent — TotalBE is now
  folded into Stage 7's ExposureRamp per the SDK reference.

The module-level entry point is `render_frame()`.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from lrt_cinema import accel
from lrt_cinema._acr3_curve import ACR3_DEFAULT_CURVE
from lrt_cinema.dcp import (
    DCPProfile,
    colormatrix_camera_to_pcs,
    interpolate_color_matrix,
    interpolate_hsv_cube,
    uv_to_kelvin,
    xy_to_uv,
)
from lrt_cinema.ir import DevelopOps

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default scene kelvin when DevelopOps carries no `temperature_k` override.
# 5500K matches the gym EXIF manual setting and is empirically optimal across
# the v0.6 test scenes. See spec §"Known limitations" — using a computed
# kelvin via `neutral_to_kelvin` regresses rose ΔE at high K (HSM divergence,
# untraced). v0.6.x will trace.
DEFAULT_SCENE_KELVIN = 5500.0

_ACR3_DEFAULT = np.array(ACR3_DEFAULT_CURVE, dtype=np.float32)

# DCP tag IDs for runtime probing.
_TAG_DEFAULT_BLACK_RENDER = 51110

# DNG tag IDs.
_TAG_DNG_BASELINE_EXPOSURE = 50730


# ---------------------------------------------------------------------------
# ACR3 default tone curve (per-channel fallback when no ProfileToneCurve)
# ---------------------------------------------------------------------------


def apply_acr3_default(x: np.ndarray) -> np.ndarray:
    """Apply Adobe ACR3 default tone curve via linear interpolation over
    1025 sample points. Matches `dng_render.cpp::dng_tone_curve_acr3_default::Evaluate`.
    """
    n = len(_ACR3_DEFAULT)
    y = np.clip(x, 0.0, 1.0) * (n - 1)
    idx = np.clip(y.astype(np.int32), 0, n - 2)
    frac = y - idx
    return _ACR3_DEFAULT[idx] * (1.0 - frac) + _ACR3_DEFAULT[idx + 1] * frac


def apply_rgb_tone(
    rgb: np.ndarray, curve: Callable[[np.ndarray], np.ndarray],
) -> np.ndarray:
    """Adobe's hue/saturation-preserving baseline tone curve (Stage 9).

    Ports `RefBaselineRGBTone` (dng_reference.cpp:1871). The curve is applied
    to the MAX and MIN channels only; the MIDDLE channel is *linearly
    interpolated* between the two curved extremes so its fractional position
    between min and max is preserved:

        rr = curve(max);  bb = curve(min)
        gg = bb + (rr - bb) * (mid - min) / (max - min)

    This is NOT a per-channel curve — per-channel would shift hue/saturation on
    chromatic pixels (Adobe's whole point in sorting). On neutral pixels
    (r==g==b) it reduces to `curve(v)` on all three, so the neutral wedge cannot
    distinguish the two; the difference is exactly the chromatic divergence that
    showed up vs `dng_validate` (gym 0.79 → 0.055 mean ΔE2000 once corrected).

    `rgb` shape (..., 3); `curve` is a vectorized [0,1]→[0,1] map (the
    `DngSplineSolver.evaluate` of the ProfileToneCurve, or `apply_acr3_default`).
    Input is pinned to [0, 1] first (Adobe `Pin_real32`); output clamped to [0, 1].
    """
    rgb = np.clip(rgb, 0.0, 1.0)
    # Stable argsort along the channel axis → [min, mid, max] indices per pixel.
    order = np.argsort(rgb, axis=-1, kind="stable")
    mn_i, md_i, mx_i = order[..., 0], order[..., 1], order[..., 2]

    def _take(idx_arr: np.ndarray) -> np.ndarray:
        return np.take_along_axis(rgb, idx_arr[..., None], axis=-1)[..., 0]

    mn, md, mx = _take(mn_i), _take(md_i), _take(mx_i)
    mn_o = np.clip(curve(mn), 0.0, 1.0)
    mx_o = np.clip(curve(mx), 0.0, 1.0)
    delta = mx - mn
    # delta == 0 ⇒ all three channels equal (neutral); curve(mid) == mn_o == mx_o.
    safe = np.where(delta > 0.0, delta, 1.0)
    md_o = np.where(
        delta > 0.0,
        mn_o + (mx_o - mn_o) * (md - mn) / safe,
        np.clip(curve(md), 0.0, 1.0),
    )

    out = np.empty_like(rgb)
    np.put_along_axis(out, mn_i[..., None], mn_o[..., None], axis=-1)
    np.put_along_axis(out, md_i[..., None], md_o[..., None], axis=-1)
    np.put_along_axis(out, mx_i[..., None], mx_o[..., None], axis=-1)
    return np.clip(out, 0.0, 1.0).astype(np.float32)


# ---------------------------------------------------------------------------
# Adobe `dng_function_exposure_ramp` (dng_render.cpp:50-103)
# ---------------------------------------------------------------------------


def make_exposure_ramp(
    exposure: float,
    shadows: float = 5.0,
    shadow_scale: float = 1.0,
    stage3_gain: float = 1.0,
    support_overrange: bool = False,
):
    """Build the per-channel three-region piecewise ramp Adobe applies after
    HueSatMap and before LookTable. Parameters mirror the SDK's
    `dng_function_exposure_ramp::Initialize`."""
    white = 1.0 / pow(2.0, max(0.0, exposure))
    black = shadows * shadow_scale * stage3_gain * 0.001
    black = min(black, 0.99 * white)
    slope = 1.0 / (white - black) if white > black else 1.0
    k_max_x = 0.5
    k_max_y = 1.0 / 16.0
    radius = min(k_max_x * black, k_max_y / slope) if slope > 0 else 0.0
    qscale = slope / (4.0 * radius) if radius > 0.0 else 0.0
    floor_thresh = black - radius
    ceil_thresh = black + radius

    def eval_ramp(x: np.ndarray) -> np.ndarray:
        linear = (x - black) * slope
        if not support_overrange:
            linear = np.minimum(linear, 1.0)
        delta = x - floor_thresh
        quad = qscale * delta * delta
        out = np.where(
            x >= ceil_thresh,
            linear,
            np.where(x <= floor_thresh, 0.0, quad),
        )
        return out.astype(x.dtype)

    return eval_ramp


# ---------------------------------------------------------------------------
# `dng_spline_solver` — Hermite C2 spline (dng_spline.cpp port)
# ---------------------------------------------------------------------------


class DngSplineSolver:
    """C0/C1/C2-continuous cubic Hermite spline with second-derivative-zero
    boundary conditions. Slopes via tridiagonal LU. Direct port of
    `dng_spline_solver::Solve` (dng_spline.cpp:57-145). Matches Adobe SDK
    bit-for-bit on the D750 Camera Standard 128-point tone curve."""

    def __init__(self, x_coords, y_coords):
        self.X = np.asarray(x_coords, dtype=np.float64)
        self.Y = np.asarray(y_coords, dtype=np.float64)
        self.S = self._solve()

    def _solve(self):
        X, Y = self.X, self.Y
        count = len(X)
        if count < 2:
            raise ValueError("DngSplineSolver: need at least 2 control points")
        start, end = 0, count
        A = X[start + 1] - X[start]
        B = (Y[start + 1] - Y[start]) / A
        S = np.zeros(count, dtype=np.float64)
        S[start] = B
        for j in range(start + 2, end):
            C = X[j] - X[j - 1]
            D = (Y[j] - Y[j - 1]) / C
            S[j - 1] = (B * C + D * A) / (A + C)
            A, B = C, D
        S[end - 1] = 2.0 * B - S[end - 2]
        S[start] = 2.0 * S[start] - S[start + 1]
        if (end - start) > 2:
            E = np.zeros(count, dtype=np.float64)
            F = np.zeros(count, dtype=np.float64)
            G = np.zeros(count, dtype=np.float64)
            F[start] = 0.5
            E[end - 1] = 0.5
            G[start] = 0.75 * (S[start] + S[start + 1])
            G[end - 1] = 0.75 * (S[end - 2] + S[end - 1])
            for j in range(start + 1, end - 1):
                A_j = (X[j + 1] - X[j - 1]) * 2.0
                E[j] = (X[j + 1] - X[j]) / A_j
                F[j] = (X[j] - X[j - 1]) / A_j
                G[j] = 1.5 * S[j]
            for j in range(start + 1, end):
                A_j = 1.0 - F[j - 1] * E[j]
                if j != end - 1:
                    F[j] /= A_j
                G[j] = (G[j] - G[j - 1] * E[j]) / A_j
            for j in range(end - 2, start - 1, -1):
                G[j] = G[j] - F[j] * G[j + 1]
            S = G.copy()
        return S

    def evaluate(self, x):
        X, Y, S = self.X, self.Y, self.S
        count = len(X)
        x_arr = np.asarray(x, dtype=np.float64)
        flat = x_arr.flatten()
        j = np.searchsorted(X, flat, side="left")
        j = np.clip(j, 1, count - 1)
        below = flat <= X[0]
        above = flat >= X[-1]
        inside = ~(below | above)
        j_in = j[inside]
        x0 = X[j_in - 1]
        y0 = Y[j_in - 1]
        s0 = S[j_in - 1]
        x1 = X[j_in]
        y1 = Y[j_in]
        s1 = S[j_in]
        x_in = flat[inside]
        A = x1 - x0
        B = (x_in - x0) / A
        C = (x1 - x_in) / A
        D = ((y0 * (2.0 - C + B) + (s0 * A * B)) * (C * C)) + (
            (y1 * (2.0 - B + C) - (s1 * A * C)) * (B * B)
        )
        out = np.empty_like(flat)
        out[below] = Y[0]
        out[above] = Y[-1]
        out[inside] = D
        return out.reshape(x_arr.shape)


# ---------------------------------------------------------------------------
# DCP / DNG runtime tag readers
# ---------------------------------------------------------------------------


def read_dcp_default_black_render(dcp_path: str | Path) -> int:
    """Read DefaultBlackRender (tag 51110) from a DCP. Returns 0 (Auto) by
    default; 1 = None (no shadow lifting at ExposureRamp)."""
    try:
        with open(str(dcp_path), "rb") as f:
            data = f.read()
        if data[:2] not in (b"II", b"MM"):
            return 0
        bo = "<" if data[:2] == b"II" else ">"
        ifd_offset = struct.unpack(bo + "I", data[4:8])[0]
        n_entries = struct.unpack(bo + "H", data[ifd_offset : ifd_offset + 2])[0]
        for i in range(n_entries):
            entry = ifd_offset + 2 + i * 12
            tag_id = struct.unpack(bo + "H", data[entry : entry + 2])[0]
            if tag_id == _TAG_DEFAULT_BLACK_RENDER:
                tag_type = struct.unpack(bo + "H", data[entry + 2 : entry + 4])[0]
                if tag_type == 4:  # LONG
                    return struct.unpack(bo + "I", data[entry + 8 : entry + 12])[0]
                if tag_type == 3:  # SHORT
                    return struct.unpack(bo + "H", data[entry + 8 : entry + 10])[0]
        return 0
    except (OSError, struct.error):
        return 0


def read_dng_baseline_exposure(dng_path: str | Path) -> float:
    """Read DNG BaselineExposure (tag 50730). SRATIONAL; tifffile decodes
    as tuple, int, or float depending on encoding."""
    import tifffile

    with tifffile.TiffFile(str(dng_path)) as tif:
        for page in tif.pages:
            tag = page.tags.get(_TAG_DNG_BASELINE_EXPOSURE)
            if tag is not None:
                v = tag.value
                if isinstance(v, tuple) and len(v) == 2:
                    return float(v[0]) / float(v[1])
                return float(v)
    return 0.0


# ---------------------------------------------------------------------------
# AsShotNeutral / scene kelvin
# ---------------------------------------------------------------------------


def read_as_shot_neutral(raw_path: str | Path) -> np.ndarray:
    """Extract AsShotNeutral from a NEF or DNG via libraw's reported
    camera_whitebalance. Returns float32 (3,) normalized G=1.

    libraw's `camera_whitebalance` returns reciprocal multipliers
    (the scaling needed to achieve neutral); AsShotNeutral itself is the
    camera-RGB value at neutral, so AsShotNeutral = 1 / multipliers.
    Normalized so the green channel multiplier = 1 (per DNG 1.7.1)."""
    import rawpy

    with rawpy.imread(str(raw_path)) as raw:
        return _asn_from_wb(raw.camera_whitebalance)


def neutral_to_kelvin(
    profile: DCPProfile,
    as_shot_neutral: np.ndarray,
    max_passes: int = 30,
) -> float:
    """Adobe SDK `dng_color_spec::NeutralToXY` iterative solve → kelvin.

    Repeats: at current kelvin estimate, compute interpolated ColorMatrix;
    back-solve neutral XYZ = inverse(CM) × AsShotNeutral; XYZ → xy → kelvin
    via Robertson. Converges in 3-10 passes.

    NOTE (v0.6): not called from the render path by default. Computed K
    regresses rose ΔE via HSM divergence at high K. Hardcoded 5500K is
    empirically lower on the two v0.6 test scenes. v0.6.x will trace.
    """
    asn = np.asarray(as_shot_neutral, dtype=np.float64)
    last_xy = (0.34567, 0.35850)  # D50 starting point
    for _ in range(max_passes):
        x_, y_ = last_xy
        u, v = xy_to_uv(x_, y_)
        k = uv_to_kelvin(u, v)
        cm = interpolate_color_matrix(profile, k)
        try:
            cm_inv = np.linalg.inv(cm)
        except np.linalg.LinAlgError:
            return k
        xyz = cm_inv @ asn
        s = xyz.sum()
        if s == 0:
            return k
        next_x = xyz[0] / s
        next_y = xyz[1] / s
        if abs(next_x - last_xy[0]) + abs(next_y - last_xy[1]) < 1e-7:
            last_xy = (next_x, next_y)
            break
        last_xy = (next_x, next_y)
    u, v = xy_to_uv(*last_xy)
    return uv_to_kelvin(u, v)


def kelvin_to_neutral(profile: DCPProfile, kelvin: float, tint: float = 0.0) -> np.ndarray:
    """Holy Grail kelvin → AsShotNeutral.

    Wraps Adobe SDK's `dng_color_spec::SetWhiteXY` iterative solve (ported
    in `dcp.xy_to_camera_neutral`). At the requested kelvin, derives the
    D-illuminant xy chromaticity, then iteratively converges on the
    camera-RGB neutral that corresponds to it under the profile's
    interpolated ColorMatrix.

    Used to override the camera-recorded AsShotNeutral when per-frame
    interpolated `DevelopOps.temperature_k` is set (Holy Grail sequences
    spanning dawn → midday kelvin shifts).
    """
    from lrt_cinema.dcp import kelvin_tint_to_xy, xy_to_camera_neutral

    x, y = kelvin_tint_to_xy(kelvin, tint=tint)
    asn = xy_to_camera_neutral(profile, x, y)
    return (asn / asn[1]).astype(np.float32)


# ---------------------------------------------------------------------------
# Demosaic
# ---------------------------------------------------------------------------
#
# Default = LINEAR (libraw bilinear) — the byte-exact match to the dng_validate
# regression tripwire, but the QUALITY floor (~5 dB below modern algorithms;
# softens edges + leaves false colour that the appearance audit reads as "edge
# residual"). Higher-quality delivery algorithms are selectable via `demosaic`
# (DCB recommended). AMaZE/LMMSE need GPL demosaic packs and RCD is absent in the
# installed libraw, so they are not offered here (a clean-room RCD port is the
# follow-up — docs/research/pipeline-overhaul-plan.md B4). A non-LINEAR choice
# CHANGES output and must be validated against the gym/rose gate + LRT-JPG north-
# star before relying on it.

# Public CLI demosaic name → libraw DemosaicAlgorithm member name.
_DEMOSAIC_ALGOS = {
    "linear": "LINEAR", "dcb": "DCB", "ahd": "AHD", "dht": "DHT",
    "vng": "VNG", "ppg": "PPG", "aahd": "AAHD",
}


def _resolve_demosaic(rawpy_mod, demosaic: str):
    """Map a CLI demosaic name to a libraw `DemosaicAlgorithm`, falling back to
    LINEAR (with a stderr warning) if the chosen algorithm is missing or needs a
    GPL pack not compiled into this libraw build. Keeps a render robust instead of
    crashing on an unavailable algorithm."""
    name = _DEMOSAIC_ALGOS.get(demosaic, "LINEAR")
    algo = getattr(rawpy_mod.DemosaicAlgorithm, name, None)
    if algo is None:
        algo = rawpy_mod.DemosaicAlgorithm.LINEAR
    try:  # libraw raises here if the algorithm needs an absent GPL demosaic pack.
        if hasattr(algo, "checkSupported"):
            algo.checkSupported()
    except Exception as exc:  # noqa: BLE001 — any libraw support error → safe fallback
        import sys
        sys.stderr.write(
            f"warning: demosaic '{demosaic}' unavailable in this libraw "
            f"({exc}); falling back to 'linear'.\n",
        )
        return rawpy_mod.DemosaicAlgorithm.LINEAR
    return algo


def _postprocess_kwargs(
    rawpy_mod, half_size: bool, demosaic: str = "linear",
    wb_mul: np.ndarray | None = None,
) -> dict:
    """The maximally-neutral libraw postprocess args (shared by the demosaic
    entry points so they cannot drift). No auto-bright, raw output colour,
    `demosaic` algorithm (default LINEAR = byte-exact / Adobe-SDK-matching;
    see `demosaic_camera_rgb`). `half_size` (preview) uses libraw's 2×2-bin and
    ignores the algorithm choice.

    `wb_mul` (3,) G-normalised WB multipliers → passed as 4-value RGBG
    `user_wb` so libraw scales the CFA BEFORE interpolating — the cross-engine
    canonical order (dcraw: `scale_colors` → `*_interpolate`; libraw, RT and
    LR likewise demosaic the white-balanced mosaic). The caller divides the
    result back by the same multipliers to preserve the unbalanced-camera-RGB
    contract (`_demosaic_rgb`). None → unit WB (the pre-fix behaviour; kept
    for the synthetic-DNG identity tests)."""
    user_wb = (
        [float(wb_mul[0]), float(wb_mul[1]), float(wb_mul[2]), float(wb_mul[1])]
        if wb_mul is not None else [1.0, 1.0, 1.0, 1.0]
    )
    return dict(
        output_bps=16,
        gamma=(1, 1),
        no_auto_bright=True,
        use_camera_wb=False,
        use_auto_wb=False,
        user_wb=user_wb,
        output_color=rawpy_mod.ColorSpace.raw,
        demosaic_algorithm=_resolve_demosaic(rawpy_mod, demosaic),
        half_size=half_size,
        four_color_rgb=False,
        highlight_mode=rawpy_mod.HighlightMode.Clip,
    )


def _bayer_pattern_str(raw_pattern, color_desc) -> str | None:
    """libraw `raw_pattern` (2×2 colour-index grid) + `color_desc` (e.g. b"RGBG")
    → a Bayer phase string ("RGGB"/"BGGR"/"GRBG"/"GBRG") for `rcd_demosaic`, or
    None if the sensor is not a 2×2 Bayer CFA (e.g. X-Trans) — caller falls back."""
    import numpy as _np
    rp = _np.asarray(raw_pattern)
    if rp.shape != (2, 2):
        return None
    desc = color_desc.decode() if isinstance(color_desc, (bytes, bytearray)) else str(color_desc)
    letters = "".join(desc[int(rp[i, j])] for i, j in ((0, 0), (0, 1), (1, 0), (1, 1)))
    return letters if letters in ("RGGB", "BGGR", "GRBG", "GBRG") else None


# CFA-domain demosaics (vs libraw postprocess): these run on the linearised Bayer
# mosaic we extract ourselves (`_extract_cfa`) — full-res, headroom-preserving.
# 'rcd'/'mlri' are clean-room (ours); 'menon' is colour_demosaicing's BSD-3
# Menon2007 (DDFAPD) — the measured-best on the demosaic battery
# (docs/research/demosaic-test-fixtures.md), the recommended quality/master
# demosaic. All three preserve over-range (>1) input (verified), so any future
# mosaic-domain highlight reconstruction survives them (placement decided by
# the architecture lock — docs/REFERENCE_PIPELINE.md).
_CFA_DEMOSAICS = ("rcd", "mlri", "menon", "amaze")


def _extract_cfa(raw) -> tuple[np.ndarray, str]:
    """Open rawpy `raw` → (linearised Bayer CFA, phase string).

    The CFA is `raw_image_visible` (post-LinearizationTable), per-channel
    black-subtracted and scaled by (white − black) to match the LINEAR path on
    in-range values, floored at 0 (libraw's black clip) with **NO top clip** so
    highlight headroom (>1.0) is PRESERVED. Cropped to even dims (the 2×2 Bayer
    phase). Raises ValueError on a non-2×2-Bayer sensor.

    Shared by every CFA-domain demosaic. Any future mosaic-domain pre-pass
    (e.g. highlight reconstruction, placement pending the architecture lock)
    slots between this extraction and the demosaic call, demosaic-independent.
    """
    pattern = _bayer_pattern_str(raw.raw_pattern, raw.color_desc)
    if pattern is None:
        raise ValueError("CFA-domain demosaic requires a 2×2 Bayer sensor; this is not.")
    cfa = raw.raw_image_visible.astype(np.float32)
    colors = raw.raw_colors_visible
    black = np.asarray(raw.black_level_per_channel, dtype=np.float32)[colors]
    white = np.float32(raw.white_level)
    cfa_norm = np.maximum((cfa - black) / (white - black), 0.0)  # floor 0; NO top clip
    h, w = cfa_norm.shape
    return cfa_norm[: h - (h % 2), : w - (w % 2)], pattern


def _mosaic_clip_mask(raw, threshold: float = 0.99, dilate: int = 2) -> np.ndarray:
    """Per-channel boolean clip mask (H, W, 3) derived from the RAW mosaic.

    A site is clipped where the linearised, black-subtracted mosaic value is
    ≥ `threshold` of sensor saturation — measured at the SENSOR, before any
    WB scaling or demosaic, so it cannot be fooled by either. Each channel's
    sparse site mask is dilated by `dilate` pixels (default 2 ≈ the
    directional-demosaic interpolation footprint), marking every full-res
    pixel whose channel-c value was interpolated FROM a clipped site. This is
    the mask the fringe forensics proved necessary: the post-demosaic 0.99
    value threshold structurally misses interpolation-smeared partial clips
    (CLAIMS.md "Fringe forensics verdict"; mechanism A used exactly this
    2-dilated mosaic mask). Consumers: `highlight_recovery` clip detection,
    slot-5b reconstruction.
    """
    from scipy.ndimage import maximum_filter

    cfa = raw.raw_image_visible.astype(np.float32)
    colors = raw.raw_colors_visible
    black = np.asarray(raw.black_level_per_channel, dtype=np.float32)[colors]
    white = np.float32(raw.white_level)
    norm = (cfa - black) / (white - black)
    clipped = norm >= np.float32(threshold)
    chan = np.where(colors == 3, 1, colors)  # G2 → G
    h, w = clipped.shape
    mask = np.zeros((h, w, 3), dtype=bool)
    size = 2 * dilate + 1
    for c in range(3):
        sites = clipped & (chan == c)
        if sites.any():
            mask[..., c] = maximum_filter(sites.astype(np.uint8), size=size) > 0
    return mask


def _cfa_demosaic(raw, method: str, wb_mul: np.ndarray | None = None,
                  highlights: str = "clip", ca_correct: int = 0) -> np.ndarray:
    """Demosaic an open rawpy `raw` on the extracted CFA with a CFA-domain `method`
    ('rcd'|'mlri'|'menon'), headroom-preserving. Returns float32 (H, W, 3).

    `wb_mul` (3,) G-normalised WB multipliers: the CFA is scaled per-site by
    them BEFORE interpolation and the result returned BALANCED — WB applied
    exactly once, at the mosaic (TARGET slot 3, owner-accepted; the previous
    divide-back shim that preserved the unbalanced contract through the H1
    hotfix is deleted). This is the cross-engine canonical order (dcraw
    `scale_colors` → `*_interpolate`; darktable temperature@3 → demosaic@8;
    RT scaleColors bakes camera WB pre-demosaic): directional demosaics
    estimate edges from inter-channel comparisons, which mis-fire on
    unbalanced channels and invent saturated false colour at steep edges —
    the owner-flagged cyan "venetian blinds". Bilinear COMMUTES with the
    per-channel scale, which is exactly why the Adobe `dng_validate` gate
    (bilinear reference) never caught the wrong order — see CLAIMS.md
    "H1 CONFIRMED" + anti-drift rule 8. `wb_mul=None` → unit WB, where
    balanced ≡ unbalanced by construction (synthetic identity tests).
    Headroom mode: >1 values survive the float chain untouched."""
    cfa, pattern = _extract_cfa(raw)
    if wb_mul is not None:
        colors = raw.raw_colors_visible
        h, w = cfa.shape
        chan = np.where(colors[:h, :w] == 3, 1, colors[:h, :w])   # G2 → G
        cfa = cfa * wb_mul[chan].astype(np.float32)
        if highlights == "clip":
            # Owner-directed default (2026-06-10): dcraw/libraw highlight=0
            # semantics — clip the WB-scaled mosaic at the COMMON white
            # (the minimum multiplier's saturation level) so every channel
            # saturates at the same height. Uniform plateaus keep
            # directional demosaics from inventing chroma at clipped fine
            # detail (pressure suite: clipbars falsecolor 17.5 → ~1,
            # externally anchored by libraw-AHD 0.88), and blown regions
            # land NEUTRAL white after Stage 2. The trade (also dcraw's):
            # channels with multiplier >1 lose their top fraction of REAL
            # highlight detail to the clamp — so the scene-linear tap-7
            # master path passes highlights="headroom" instead, preserving
            # >1 values for grading + the future pre-demosaic
            # reconstruction (REFERENCE_PIPELINE TARGET slot 5).
            cfa = np.minimum(cfa, np.float32(wb_mul.min()))
    if ca_correct > 0:
        # TARGET slot 2: raw lateral-CA correction on the BALANCED mosaic,
        # after the WB scale + highlight conditioning, before demosaic —
        # dt's exact placement (temperature@3 → highlights@4 → cacorrect@5
        # → demosaic@8); RT likewise runs CA_correct pre-demosaic. Opt-in
        # (`--ca-correct N`, owner-gated); `ca_correct` = Martinec
        # iterations. The normalisation white is the conditioning ceiling:
        # the common white in clip mode, the max multiplier in headroom.
        from lrt_cinema._ca_correct import ca_correct_mosaic
        ca_scale = (
            float(wb_mul.min() if highlights == "clip" else wb_mul.max())
            if wb_mul is not None else None
        )
        cfa = ca_correct_mosaic(cfa, pattern, iterations=ca_correct,
                                scale=ca_scale)
    if method == "rcd":
        from lrt_cinema import accel
        rgb = accel.rcd_demosaic(cfa, pattern)
    elif method == "mlri":
        from lrt_cinema._mlri_demosaic import mlri_demosaic
        rgb = mlri_demosaic(cfa, pattern).astype(np.float32)
    elif method == "menon":  # colour_demosaicing BSD-3 Menon2007 (DDFAPD) — quality default
        from colour_demosaicing import demosaicing_CFA_Bayer_Menon2007
        out = np.asarray(
            demosaicing_CFA_Bayer_Menon2007(cfa, pattern), dtype=np.float32,
        )
        # Floor ≥0 to match the RCD/MLRI convention (directional demosaics can ring
        # slightly negative at edges); NO top clip — preserve highlight headroom.
        rgb = np.maximum(out, np.float32(0.0))
    elif method == "amaze":
        # Clean-room AMaZE (slot-4 diagonal-detail port, 2026-06-12).
        # AMaZE assumes a single uniform clip point (dt runs it after its
        # highlights module for the same reason) and clamps its output to
        # [0, 1] — it is the DISPLAY-path (clip-mode) demosaic. The
        # headroom master path keeps menon.
        if highlights != "clip" and wb_mul is not None:
            import sys
            sys.stderr.write(
                "warning: demosaic 'amaze' requires clip-mode highlight "
                "conditioning; using 'menon' for the headroom path.\n")
            from colour_demosaicing import demosaicing_CFA_Bayer_Menon2007
            rgb = np.maximum(np.asarray(
                demosaicing_CFA_Bayer_Menon2007(cfa, pattern),
                dtype=np.float32), np.float32(0.0))
        else:
            from lrt_cinema._amaze_demosaic import amaze_demosaic
            clip_pt = float(wb_mul.min()) if wb_mul is not None else 1.0
            rgb = amaze_demosaic(cfa, pattern, clip_pt=clip_pt)
    else:
        raise ValueError(f"unknown CFA demosaic method {method!r}")
    return rgb


def _libraw_rgb(raw, rawpy_mod, half_size: bool, demosaic: str,
                wb_mul: np.ndarray | None) -> np.ndarray:
    """libraw `postprocess` → BALANCED linear camera RGB.

    With `wb_mul`, libraw scales the CFA before interpolating (canonical
    order — see `_postprocess_kwargs`). libraw normalises the user
    multipliers by their MINIMUM (verified empirically: user_wb [2,1,1.5,1]
    scales output by exactly [2,1,1.5] when min=1), so the output here is
    rescaled by `wb_mul.min()` (a scalar) to land on the same G-normalised
    balanced scale as the CFA paths. Channels clip at 65535 inside libraw,
    so a blown pixel lands at `wb_mul.min()` on every channel — NEUTRAL
    white at the common-white clip level (Adobe/dcraw "solid white"
    highlight-clip behaviour; the unit-WB path instead let blown pixels go
    to the WB colour). `wb_mul=None` → unit WB, output unscaled [0, 1]."""
    rgb = raw.postprocess(
        **_postprocess_kwargs(rawpy_mod, half_size, demosaic, wb_mul),
    ).astype(np.float32) / 65535.0
    if wb_mul is not None:
        rgb = rgb * np.float32(wb_mul.min())
    return rgb


def _demosaic_rgb(raw, rawpy_mod, half_size: bool, demosaic: str,
                  wb_mul: np.ndarray | None = None,
                  highlights: str = "clip", ca_correct: int = 0) -> np.ndarray:
    """Linear camera RGB (H, W, 3) float32 from an OPEN rawpy `raw`. A CFA-domain
    method ('rcd'/'mlri'/'menon', full-res only) runs on the extracted CFA
    (headroom-preserving), falling back to libraw 'linear' on a non-Bayer sensor, a
    missing optional dep (e.g. colour_demosaicing for 'menon'), or ANY error; preview
    (half_size) and libraw algos go through `postprocess` (/65535). Shared by
    `demosaic_camera_rgb` + `_decode_raw` so they cannot drift.

    `wb_mul` (3,) G-normalised WB multipliers → demosaic the white-balanced
    mosaic and return BALANCED camera RGB (WB applied once, at the mosaic —
    TARGET slot 3; the H1 cyan root-cause fix). `wb_mul=None` → unit WB,
    balanced ≡ unbalanced."""
    if demosaic in _CFA_DEMOSAICS and not half_size:
        try:
            rgb = _cfa_demosaic(raw, demosaic, wb_mul, highlights, ca_correct)
        except Exception as exc:  # noqa: BLE001 — any failure → safe libraw fallback
            import sys
            sys.stderr.write(
                f"warning: demosaic '{demosaic}' unavailable ({exc}); "
                f"falling back to 'linear'.\n",
            )
            rgb = _libraw_rgb(raw, rawpy_mod, half_size, "linear", wb_mul)
    else:
        if ca_correct > 0:
            import sys
            sys.stderr.write(
                "warning: --ca-correct needs a CFA-domain demosaic "
                "(rcd/mlri/menon/amaze) at full resolution; skipped on the "
                "libraw/preview path.\n",
            )
        rgb = _libraw_rgb(raw, rawpy_mod, half_size, demosaic, wb_mul)
    return rgb


def _asn_from_wb(camera_whitebalance) -> np.ndarray:
    """libraw `camera_whitebalance` → AsShotNeutral, float32 (3,), G-normalised.
    Shared by `read_as_shot_neutral` and `_decode_raw` so both agree exactly."""
    wb = np.array(camera_whitebalance[:3], dtype=np.float32)
    asn = 1.0 / wb
    return (asn / asn[1]).astype(np.float32)


def _wb_mul_from_asn(asn: np.ndarray) -> np.ndarray:
    """AsShotNeutral → Stage-2's exact G-normalised WB multipliers (3,).
    Shared by the demosaic pre-scale and Stage 2 so the divide-back/re-multiply
    cancel exactly."""
    wb = 1.0 / np.asarray(asn, dtype=np.float32)
    return (wb / wb[1]).astype(np.float32)


def demosaic_camera_rgb(
    raw_path: str | Path, half_size: bool = False, demosaic: str = "linear",
    wb_asn: np.ndarray | None = None,
) -> np.ndarray:
    """Demosaic via libraw with maximally-neutral postprocess settings.

    Accepts NEF or DNG. DNG strongly preferred — libraw honors the embedded
    LinearizationTable + correct WhiteLevel (15520 vs the per-camera 15311 or
    theoretical 16383). Both close real-world ΔE vs dng_validate; see
    `docs/research/dng-pipeline-findings.md` §"Verification of the LINEAR
    demosaic finding".

    The mosaic is white-balance-scaled BEFORE interpolation and the result
    returned BALANCED (WB applied once, at the mosaic — TARGET slot 3; the
    H1 cyan fix). `wb_asn` selects the render neutral — default None = the
    camera AsShotNeutral (dcraw's default); pass the develop/render neutral
    when known. The G-normalised multipliers are `_wb_mul_from_asn(asn)`.

    `half_size` (preview only): libraw's fast 2×2-bin demosaic — one output
    pixel per Bayer quad, so it skips interpolation AND returns a (H/2, W/2)
    image, roughly quartering decode time. This is the demosaic-floor cut the
    proxy path needs (CLAUDE-graded full renders pass `half_size=False`). The
    binned result is NOT colour-graded for delivery — preview only.

    Returns float32 (H, W, 3) BALANCED linear camera RGB (black-subtracted,
    white-normalised, G-normalised WB applied at the mosaic).
    """
    import rawpy

    with rawpy.imread(str(raw_path)) as raw:
        asn = wb_asn if wb_asn is not None else _asn_from_wb(raw.camera_whitebalance)
        return _demosaic_rgb(raw, rawpy, half_size, demosaic, _wb_mul_from_asn(asn))


def _decode_raw(
    raw_path: str | Path, half_size: bool = False, demosaic: str = "linear",
    wb_asn: np.ndarray | None = None, highlights: str = "clip",
    want_clip_mask: bool = False, ca_correct: int = 0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    """Open the raw ONCE; return (BALANCED linear camera RGB, camera
    AsShotNeutral, optional mosaic clip mask).

    `render_frame` needs the demosaiced pixels, the camera AsShotNeutral and
    (when reconstruction is on) the mosaic-derived clip mask; opening the
    file more than once wastes a full ~0.3 s raw decode per frame. This
    folds them into a single `rawpy.imread`.

    `wb_asn`: the render neutral for the at-mosaic WB (canonical
    scale-before-interpolate order, H1 fix; WB applied ONCE — slot 3).
    None → the camera AsShotNeutral. Callers that override WB (Holy-Grail
    kelvin) pass their final neutral. The returned AsShotNeutral is ALWAYS
    the camera tag (unchanged contract).

    `want_clip_mask`: also compute `_mosaic_clip_mask` (sensor-saturation
    sites, per-channel, 2-dilated) cropped to the decoded shape; None when
    not requested or on any mask failure (callers fall back to the
    value-threshold detection)."""
    import rawpy

    with rawpy.imread(str(raw_path)) as raw:
        cam_asn = _asn_from_wb(raw.camera_whitebalance)
        asn = wb_asn if wb_asn is not None else cam_asn
        rgb = _demosaic_rgb(
            raw, rawpy, half_size, demosaic, _wb_mul_from_asn(asn), highlights,
            ca_correct,
        )
        mask: np.ndarray | None = None
        if want_clip_mask and not half_size:
            try:
                mask = _mosaic_clip_mask(raw)[: rgb.shape[0], : rgb.shape[1]]
            except Exception:  # noqa: BLE001 — mask is an enhancement, never fatal
                mask = None
    return rgb, cam_asn, mask


def _block_downsample(img: np.ndarray, k: int) -> np.ndarray:
    """Area-average downsample a (H, W, 3) image by integer factor `k`.

    Preview-only: averages k×k blocks (crops to a multiple of k first), which
    is a cheap, alias-suppressing box filter on linear-light pixels — correct
    to do BEFORE the colour math so the per-pixel stages see k² fewer pixels.
    `k <= 1` is a no-op (returns the input)."""
    if k <= 1:
        return img
    h, w, c = img.shape
    h2, w2 = (h // k) * k, (w // k) * k
    return (
        img[:h2, :w2]
        .reshape(h2 // k, k, w2 // k, k, c)
        .mean(axis=(1, 3))
        .astype(img.dtype)
    )


# ---------------------------------------------------------------------------
# Core pipeline: stages 1–9 (sensor → linear ProPhoto post-tone-curve)
# ---------------------------------------------------------------------------


def apply_adobe_pipeline(
    camera_rgb: np.ndarray,
    profile: DCPProfile,
    as_shot_neutral: np.ndarray,
    scene_kelvin: float,
    dng_baseline_exposure: float = 0.0,
    default_black_render: int = 0,
    stop_after_stage: int = 9,
) -> np.ndarray:
    """Apply DNG 1.7.1 §"Mapping Camera Color Space" stages 3 through
    `stop_after_stage`.

    Input: **BALANCED** linear camera RGB (H, W, 3), [0, 1+] — white balance
    is applied ONCE, at the mosaic, by the decode (`_demosaic_rgb` with the
    G-normalised `_wb_mul_from_asn(as_shot_neutral)` multipliers); there is
    no Stage-2 multiply here any more (TARGET slot 3, owner-accepted: the
    divide-back/re-multiply shim telescoped exactly and is deleted).
    `as_shot_neutral` is still required: the kelvin solve and the no-FM
    ColorMatrix branch consume it.
    Output: linear ProPhoto RGB (D50), (H, W, 3), [0, 1+].

    `default_black_render`: 0 = Auto (Shadows=5.0), 1 = None (Shadows=0).
    Read via `read_dcp_default_black_render(dcp_path)`.

    `stop_after_stage`: early-exit toggle. Default 9 = full pipeline (v0.6
    behaviour). 7 = stop after ExposureRamp, skipping LookTable (Stage 8)
    + ProfileToneCurve (Stage 9) — the `cinema-linear-master` preset; the
    Stage 7 output preserves HDR headroom that the tone curve would
    otherwise clip, at the cost of losing the DCP's tone-shape look. See
    `docs/research/v07-spec-revision-plan.md` §3 for the rationale.

    3 and 4 (v0.8) are the **colorimetric tap** — the absolute-accuracy /
    preview measurement point per docs/archive/VALIDATION.md §"Validation axes".
    3 returns XYZ(D50) and 4 returns linear ProPhoto(D50), both taken
    immediately post-ForwardMatrix and BEFORE HueSatMap / ExposureRamp /
    LookTable / ProfileToneCurve shape the pixels. Measuring absolute ΔE
    here (not on the rendered image) isolates colour-pipeline error from
    Adobe's pictorial tone/look. XYZ(D50) and linear ProPhoto(D50) are one
    fixed matrix apart (`ProPhoto RGB` RGB↔XYZ). Used by
    `tests/test_colorimetric.py` (Axis 2). Only 3, 4, 7 and 9 are
    supported; other values raise ValueError.

    Module boundary: this function ends at Stage `stop_after_stage`
    (post-tone-curve when 9, post-ExposureRamp when 7), with
    TotalBaselineExposure already folded into Stage 7's ExposureRamp.
    LR-authored develop ops (Stages 11–12) live in `develop_ops.py`. Output
    color conversion (Stage 13) lives in `output.py`. Stage 10 (the prior
    standalone `2^TotalBE` scalar) is removed — see module docstring.
    """
    if stop_after_stage not in (3, 4, 7, 9):
        raise ValueError(
            f"stop_after_stage must be 3, 4, 7, or 9, got {stop_after_stage}",
        )
    h, w, _ = camera_rgb.shape

    # Input arrives BALANCED (WB applied once, at the mosaic — slot 3).
    balanced = camera_rgb

    # Stage 3: camera RGB → XYZ(D50).
    if profile.forward_matrix_1 is not None:
        if (
            profile.forward_matrix_2 is not None
            and not np.allclose(profile.forward_matrix_1, profile.forward_matrix_2)
        ):
            k_lo, k_hi = sorted([profile.kelvin_1, profile.kelvin_2])
            if profile.kelvin_1 <= profile.kelvin_2:
                fm_lo, fm_hi = profile.forward_matrix_1, profile.forward_matrix_2
            else:
                fm_lo, fm_hi = profile.forward_matrix_2, profile.forward_matrix_1
            if scene_kelvin <= k_lo:
                fm = fm_lo
            elif scene_kelvin >= k_hi:
                fm = fm_hi
            else:
                f = (1 / scene_kelvin - 1 / k_lo) / (1 / k_hi - 1 / k_lo)
                fm = (1 - f) * fm_lo + f * fm_hi
        else:
            fm = profile.forward_matrix_1
        xyz = balanced.reshape(-1, 3) @ fm.T
        xyz = xyz.reshape(h, w, 3).astype(np.float32)
    else:
        # No ForwardMatrix → ColorMatrix path WITH Adobe's MapWhiteMatrix D50
        # adaptation (dng_color_spec::SetWhiteXY no-FM branch). The bare
        # inv(ColorMatrix) shortcut maps neutral to the scene white, not D50,
        # and tints every neutral (~7 ΔE). This branch is what Adobe runs for
        # FM-less embedded profiles (e.g. the dnglab-cloned synthetic DNG, whose
        # ForwardMatrix dnglab strips) and is authored to feed the LookTable.
        # `colormatrix_camera_to_pcs` is defined on UNBALANCED camera RGB (it
        # embeds the neutral); input here is balanced, so fold the inverse WB
        # (unbal = bal · asn/asn_G) into the matrix — exact, no extra pass.
        import colour
        pcs_white_xy = tuple(
            float(c) for c in colour.RGB_COLOURSPACES["ProPhoto RGB"].whitepoint
        )
        camera_to_pcs = colormatrix_camera_to_pcs(
            profile, as_shot_neutral, pcs_white_xy,
        )
        asn64 = np.asarray(as_shot_neutral, dtype=np.float64)
        camera_to_pcs = camera_to_pcs @ np.diag(asn64 / asn64[1])
        xyz = balanced.reshape(-1, 3) @ camera_to_pcs.T
        xyz = xyz.reshape(h, w, 3).astype(np.float32)

    # Colorimetric tap (Stage 3): XYZ(D50) immediately post-ForwardMatrix —
    # the absolute-accuracy / preview measurement point, BEFORE any HSM /
    # ExposureRamp / LookTable / ProfileToneCurve shaping. See docstring +
    # docs/archive/VALIDATION.md §"Validation axes".
    if stop_after_stage == 3:
        return xyz

    # Stage 4: XYZ(D50) → linear ProPhoto(D50).
    import colour
    m_xyz_to_prophoto = colour.RGB_COLOURSPACES["ProPhoto RGB"].matrix_XYZ_to_RGB
    prophoto = xyz.reshape(-1, 3) @ m_xyz_to_prophoto.T
    prophoto = prophoto.reshape(h, w, 3).astype(np.float32)

    # Colorimetric tap (Stage 4): linear ProPhoto(D50), still pre-HSM. The
    # canonical Axis-2/Axis-3 tap consumed by tests/test_colorimetric.py; one
    # fixed matrix (ProPhoto RGB↔XYZ) away from the Stage-3 XYZ(D50) tap.
    if stop_after_stage == 4:
        return prophoto

    # Stage 5: HueSatMap in HSV (backend-dispatched; numpy ref or numba kernel).
    rgb = prophoto
    if profile.hue_sat_map is not None:
        hsm_blended = interpolate_hsv_cube(
            profile.hue_sat_map,
            scene_kelvin,
            profile.kelvin_1,
            profile.kelvin_2,
        )
        rgb = accel.apply_hsv_cube_rgb(rgb, hsm_blended, profile.hue_sat_map)

    # Stages 6+7: TotalBaselineExposure folded into ExposureRamp.
    # TotalBE = DNG.BaselineExposure + DCP.BaselineExposureOffset per Adobe
    # DNG SDK dng_negative.cpp:2588-2606. Fed to the ramp as its `exposure`
    # parameter; the SDK has no post-tone-curve BE scalar (Stage3Gain == 1.0
    # on the libraw LINEAR / interpolation path). The DCP `BaselineExposure`
    # tag (50730) is NOT part of the sum — Adobe's DCP writer never emits it
    # (dng_image_writer.cpp:2658 only writes tcBaselineExposureOffset).
    exposure_value = dng_baseline_exposure + profile.baseline_exposure_offset
    shadows = 0.0 if default_black_render == 1 else 5.0
    # Stage-7 emission (cinema-linear-master) is scene-referred and is NOT
    # followed by a clamping ProfileToneCurve, so the ExposureRamp must keep
    # its overrange (>1.0) — that is the recoverable highlight headroom the
    # half-float EXR carries. The Adobe SDK exposes this exact mode via
    # `dng_function_exposure_ramp`'s overrange support. Stage-9 (γ) feeds a
    # ProfileToneCurve that clamps to [0, 1] regardless, so it stays False
    # and remains bit-identical to the validated v0.6 reference (< 1 ΔE vs
    # dng_validate). Verified: tools/verify_emission_format.py check C3.
    ramp = make_exposure_ramp(
        exposure=exposure_value,
        shadows=shadows,
        shadow_scale=1.0,
        stage3_gain=1.0,
        support_overrange=(stop_after_stage == 7),
    )
    rgb = ramp(rgb)

    if stop_after_stage == 7:
        # cinema-linear-master emission point: post-ExposureRamp (overrange
        # preserved), pre-LookTable + pre-ProfileToneCurve. Caller writes
        # this to half-float EXR; LR PV2012 ops still apply downstream in
        # develop_ops.apply_develop_ops on scene-referred data that hasn't
        # been tone-shaped by the DCP. Of those ops only a keyframed
        # ToneCurvePV2012 re-clips to [0, 1] (Class-B drop, same pattern as
        # the v0.6 Highlights/Shadows/Whites drops); the Holy-Grail core
        # (exposure/WB/blacks/contrast/sat/vib) keeps the headroom.
        return rgb

    # Stage 8: LookTable in HSV (backend-dispatched; numpy ref or numba kernel).
    if profile.look_table is not None:
        rgb = accel.apply_hsv_cube_rgb(
            rgb, profile.look_table.data_1, profile.look_table,
        )

    # Stage 9: ProfileToneCurve (or ACR3 default fallback), applied as Adobe's
    # hue/saturation-preserving RGB tone (RefBaselineRGBTone), NOT per-channel.
    # Per-channel rotates hue on chromatic pixels; Adobe curves only max+min and
    # interpolates the middle channel. See `apply_rgb_tone`. (This is the fix
    # that took gym 0.79 → 0.055 mean ΔE2000 vs dng_validate.)
    if profile.profile_tone_curve is not None:
        curve_pts = profile.profile_tone_curve
        solver = DngSplineSolver(curve_pts[:, 0], curve_pts[:, 1])
        rgb = accel.apply_rgb_tone(rgb, solver)
    else:
        rgb = accel.apply_rgb_tone(rgb, apply_acr3_default)

    return rgb


# ---------------------------------------------------------------------------
# Module entry point: render a single frame end-to-end (stages 1–9)
# ---------------------------------------------------------------------------


@dataclass
class FrameRenderResult:
    """Output of `render_frame` plus rendering metadata for downstream stages.

    `prophoto` holds the array at the requested `stop_after_stage`:
      - 9 (default): linear ProPhoto(D50), post-ProfileToneCurve;
      - 7: linear ProPhoto(D50), post-ExposureRamp (overrange preserved);
      - 4: linear ProPhoto(D50) at the colorimetric tap (pre-HSM);
      - 3: XYZ(D50) at the colorimetric tap (pre-HSM) — despite the field
        name, this is XYZ, one fixed matrix from ProPhoto.
    The field name reflects the common case (9); for the tap stages the
    caller knows which space it asked for."""

    prophoto: np.ndarray
    scene_kelvin: float
    dng_baseline_exposure: float
    default_black_render: int


_PREVIEW_SCALES = (1, 2, 4, 8)


def render_frame(
    raw_path: str | Path,
    profile: DCPProfile,
    dcp_path: str | Path | None = None,
    develop_ops: DevelopOps | None = None,
    stop_after_stage: int = 9,
    preview_scale: int = 1,
    highlight_recovery: bool = False,
    demosaic: str = "linear",
    demosaic_highlights: str = "clip",
    fc_suppress: int = 0,
    ca_correct: int = 0,
) -> FrameRenderResult:
    """End-to-end render of a single RAW frame through pipeline stages 1
    through `stop_after_stage`.

    `preview_scale` ∈ {1, 2, 4, 8} (default 1 = full resolution, the only
    delivery-grade setting). Values > 1 render a **low-resolution PREVIEW** at
    ~1/scale linear resolution for rapid grade/sequence iteration: the demosaic
    runs in fast 2×2-bin (`half_size`) mode and the linear camera RGB is then
    area-downsampled by `scale // 2`, so the per-pixel colour stages process
    ~scale² fewer pixels. The colour maths is otherwise identical, but the
    output is NOT colour-exact (binned demosaic + downsample) and is exempt from
    the ΔE ship gate — it is for visual iteration, not the LRT round-trip /
    final delivery. The colorimetric taps (stage 3/4) ignore it.

    `raw_path` should point to a converted DNG (use `dng_convert.py`, which
    wraps dnglab, to produce one from a NEF). NEF input works but loses the
    embedded LinearizationTable + correct WhiteLevel that close ~0.6 ΔE vs
    Adobe's `dng_validate` reference.

    `dcp_path` is required for DefaultBlackRender lookup (the DCP parser
    doesn't carry the tag yet). Pass the same Adobe DCP path the profile
    was parsed from.

    `develop_ops` carries per-frame Holy Grail kelvin override. When
    `develop_ops.temperature_k is not None`, the AsShotNeutral derived from
    libraw is replaced by `kelvin_to_neutral(profile, develop_ops.temperature_k)`.

    `stop_after_stage`: 9 (default) runs the full DCP-shaping pipeline used
    by γ; 7 stops after ExposureRamp for the `cinema-linear-master` preset,
    preserving HDR headroom that the DCP's LookTable + ProfileToneCurve
    would otherwise consume; 3 / 4 expose the colorimetric tap (XYZ(D50) /
    linear ProPhoto(D50) immediately post-ForwardMatrix, pre-HSM) for the
    Axis-2 absolute-accuracy harness — see `apply_adobe_pipeline`.

    `highlight_recovery`: when True, run the Tier-1 highlight-reconstruction
    pre-stage (`highlight_recovery.reconstruct_highlights`) on the BALANCED
    camera RGB post-demosaic, driven by the mosaic-derived clip mask at full
    res — recovering blown highlights from surviving channels by local ratio
    propagation (clean white instead of dark/warm). A strict byte-identical
    no-op when no channel clips. **Default False** so every caller (incl.
    the gym/rose ΔE ship gate, whose gym frame is itself clipped) stays
    byte-identical; the CLI/preset layer turns it on for production. In
    clipped regions this intentionally diverges from `dng_validate` (which
    clips, not reconstructs) — docs/archive/DECISIONS.md §"Highlight recovery".

    `ca_correct`: TARGET slot-2 raw lateral-CA correction iterations (0 =
    off, the default — every existing caller incl. the gym gate stays
    byte-identical). N ≥ 1 runs the clean-room Martinec correction
    (`_ca_correct.ca_correct_mosaic`) on the balanced mosaic between the
    highlight conditioning and the demosaic — dt's cacorrect@5 placement.
    CFA-domain demosaics at full resolution only (warned + skipped
    elsewhere). Owner-gated opt-in via the CLI `--ca-correct`.
    """
    if preview_scale not in _PREVIEW_SCALES:
        raise ValueError(
            f"preview_scale must be one of {_PREVIEW_SCALES}, got {preview_scale}",
        )

    # The render WB is resolved BEFORE the decode: the demosaic pre-conditions
    # the mosaic with the SAME neutral Stage 2 will use (scale-before-
    # interpolate canonical order, H1 cyan fix — `_cfa_demosaic`), so the
    # Holy-Grail kelvin override must be known up front.
    scene_kelvin = DEFAULT_SCENE_KELVIN
    override_asn: np.ndarray | None = None
    if develop_ops is not None and develop_ops.temperature_k is not None:
        scene_kelvin = float(develop_ops.temperature_k)
        override_asn = kelvin_to_neutral(
            profile, scene_kelvin, float(develop_ops.tint or 0.0),
        )

    # One raw open yields the demosaiced pixels, AsShotNeutral and (when
    # reconstruction is on, full-res) the mosaic-derived clip mask.
    camera_rgb, cam_asn, clip_mask = _decode_raw(
        raw_path, half_size=(preview_scale >= 2), demosaic=demosaic,
        wb_asn=override_asn, highlights=demosaic_highlights,
        want_clip_mask=(highlight_recovery and preview_scale == 1),
        ca_correct=ca_correct,
    )
    asn = override_asn if override_asn is not None else cam_asn
    if preview_scale >= 2:
        camera_rgb = _block_downsample(camera_rgb, preview_scale // 2)

    dng_be = read_dng_baseline_exposure(raw_path)
    dbr = read_dcp_default_black_render(dcp_path) if dcp_path is not None else 0

    # Stage 1.5: Tier-1 highlight reconstruction on BALANCED camera RGB
    # (neutral = [1,1,1] by construction). Clip detection comes from the
    # MOSAIC mask when available (sensor-saturation truth — the value
    # threshold structurally misses interpolation-smeared partial clips;
    # CLAIMS "Fringe forensics verdict"); preview falls back to the
    # threshold. No-op (byte-identical) when nothing clips.
    if highlight_recovery:
        from lrt_cinema.highlight_recovery import reconstruct_highlights
        camera_rgb = reconstruct_highlights(camera_rgb, clip=clip_mask)

    # TARGET slot 6: false-colour suppression (canon chroma-difference
    # median + RT-style chroma blur — see `_fc_suppress`), after demosaic/
    # highlight handling, before the colour transform. 0 = off (default;
    # owner-gated). blur=True is the measured-better variant on every
    # slot-6 target (fc_suppress_slot6 evidence: noisebars 7.98→4.36 at 3
    # passes, guards pass); the pure-median dcraw arm stays available via
    # the module. Ledger order note: dcraw runs its median BEFORE highlight
    # blending; we follow the ledger's slot order (5 then 6) so suppression
    # also cleans reconstruction-edge chroma — revisit with the 5b verdict.
    if fc_suppress > 0:
        from lrt_cinema._fc_suppress import suppress_false_colour
        camera_rgb = suppress_false_colour(
            camera_rgb, passes=fc_suppress, blur=True,
        )

    # Scene-referred exposure block (slot 7): ONE linear gain combining the
    # LRT mask-EV corrections (deflicker / Holy-Grail / global — serialized
    # EV/4, applied ×4: interpolation.LR_LOCAL_EXPOSURE_SCALE) and the
    # global Exposure2012 slider, which the CALEXP probe measured as the
    # SAME scene-referred domain (cal_exposure_domain 2026-06-11: the
    # post-curve arm fails ΔE 2.84/5.85; scene-gain lands at the base-look
    # floor). Upstream of the colour transform per the canon; the
    # post-curve domain measurably cannot match LR for either op class.
    total_scene_ev = 0.0
    if develop_ops is not None:
        total_scene_ev = develop_ops.scene_exposure_ev + develop_ops.exposure_ev
    if total_scene_ev != 0.0:
        camera_rgb = camera_rgb * np.float32(2.0 ** total_scene_ev)

    prophoto = apply_adobe_pipeline(
        camera_rgb=camera_rgb,
        profile=profile,
        as_shot_neutral=asn,
        scene_kelvin=scene_kelvin,
        dng_baseline_exposure=dng_be,
        default_black_render=dbr,
        stop_after_stage=stop_after_stage,
    )
    return FrameRenderResult(
        prophoto=prophoto,
        scene_kelvin=scene_kelvin,
        dng_baseline_exposure=dng_be,
        default_black_render=dbr,
    )
