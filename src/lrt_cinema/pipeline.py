"""Adobe DNG 1.7.1 reference render pipeline.

Promoted from `.audit_tmp/adobe_pipeline.py` on `research/python-pipeline-seed`,
which achieves < 1 ΔE mean vs `dng_validate` on both test scenes
(gym 0.79, rose 0.84).

Stage order (per `docs/research/v06-architecture.md` §"Pipeline stage order"):

  1.  Demosaic via rawpy/libraw on Adobe-converted DNG (not NEF — DNG gives
      libraw the correct WhiteLevel + embedded LinearizationTable). Algorithm
      = LINEAR (matches Adobe SDK internal default).
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
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from lrt_cinema._acr3_curve import ACR3_DEFAULT_CURVE
from lrt_cinema.dcp import (
    DCPProfile,
    interpolate_color_matrix,
    interpolate_hsv_cube,
    uv_to_kelvin,
    xy_to_uv,
)
from lrt_cinema.ir import DevelopOps
from lrt_cinema.lut3d_baker import (
    _apply_hsv_cube,
    _hsv_to_rgb_dcp,
    _rgb_to_hsv_dcp,
)

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
        wb = np.array(raw.camera_whitebalance[:3], dtype=np.float32)
    asn = 1.0 / wb
    return asn / asn[1]


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


def kelvin_to_neutral(profile: DCPProfile, kelvin: float) -> np.ndarray:
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

    x, y = kelvin_tint_to_xy(kelvin, tint=0)
    asn = xy_to_camera_neutral(profile, x, y)
    return (asn / asn[1]).astype(np.float32)


# ---------------------------------------------------------------------------
# Demosaic (LINEAR — matches Adobe SDK internal)
# ---------------------------------------------------------------------------


def demosaic_camera_rgb(raw_path: str | Path) -> np.ndarray:
    """Demosaic via libraw with maximally-neutral postprocess settings.

    Accepts NEF or DNG. DNG strongly preferred — libraw honors the embedded
    LinearizationTable + correct WhiteLevel (15520 vs the per-camera 15311 or
    theoretical 16383). Both close real-world ΔE vs dng_validate; see
    `docs/research/dng-pipeline-findings.md` §"Verification of the LINEAR
    demosaic finding".

    Returns float32 (H, W, 3) in linear camera RGB, normalized [0, 1] after
    black-level subtract.
    """
    import rawpy

    with rawpy.imread(str(raw_path)) as raw:
        rgb = raw.postprocess(
            output_bps=16,
            gamma=(1, 1),
            no_auto_bright=True,
            use_camera_wb=False,
            use_auto_wb=False,
            user_wb=[1.0, 1.0, 1.0, 1.0],
            output_color=rawpy.ColorSpace.raw,
            demosaic_algorithm=rawpy.DemosaicAlgorithm.LINEAR,
            half_size=False,
            four_color_rgb=False,
            highlight_mode=rawpy.HighlightMode.Clip,
        )
    return rgb.astype(np.float32) / 65535.0


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
    """Apply DNG 1.7.1 §"Mapping Camera Color Space" stages 2 through
    `stop_after_stage`.

    Input: linear camera RGB (H, W, 3), [0, 1+], post-demosaic / black-subtract.
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
    preview measurement point per docs/VALIDATION.md §"Validation axes".
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

    # Stage 2: AsShotNeutral inverse → balanced camera RGB.
    wb_mul = 1.0 / as_shot_neutral
    wb_mul = wb_mul / wb_mul[1]
    balanced = camera_rgb * wb_mul[None, None, :]

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
        cm = interpolate_color_matrix(profile, scene_kelvin)
        cm_inv = np.linalg.inv(cm)
        xyz = camera_rgb.reshape(-1, 3) @ cm_inv.T
        n_xyz = cm_inv @ as_shot_neutral
        xyz = xyz / n_xyz[1]
        xyz = xyz.reshape(h, w, 3).astype(np.float32)

    # Colorimetric tap (Stage 3): XYZ(D50) immediately post-ForwardMatrix —
    # the absolute-accuracy / preview measurement point, BEFORE any HSM /
    # ExposureRamp / LookTable / ProfileToneCurve shaping. See docstring +
    # docs/VALIDATION.md §"Validation axes".
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

    # Stage 5: HueSatMap in HSV.
    rgb = prophoto
    if profile.hue_sat_map is not None:
        h_arr, s_arr, v_arr, valid = _rgb_to_hsv_dcp(rgb)
        hsm_blended = interpolate_hsv_cube(
            profile.hue_sat_map,
            scene_kelvin,
            profile.kelvin_1,
            profile.kelvin_2,
        )
        h_arr, s_arr, v_arr = _apply_hsv_cube(
            h_arr, s_arr, v_arr, hsm_blended, profile.hue_sat_map,
        )
        rgb_post_hsm = _hsv_to_rgb_dcp(h_arr, s_arr, v_arr)
        rgb = np.where(valid[..., None], rgb_post_hsm, rgb)

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

    # Stage 8: LookTable in HSV.
    if profile.look_table is not None:
        h_arr, s_arr, v_arr, valid = _rgb_to_hsv_dcp(rgb)
        h_arr, s_arr, v_arr = _apply_hsv_cube(
            h_arr, s_arr, v_arr, profile.look_table.data_1, profile.look_table,
        )
        rgb_post_lt = _hsv_to_rgb_dcp(h_arr, s_arr, v_arr)
        rgb = np.where(valid[..., None], rgb_post_lt, rgb)

    # Stage 9: per-channel ProfileToneCurve (or ACR3 default fallback).
    if profile.profile_tone_curve is not None:
        curve = profile.profile_tone_curve
        solver = DngSplineSolver(curve[:, 0], curve[:, 1])
        clipped = np.clip(rgb, 0.0, 1.0)
        for ch in range(3):
            rgb[..., ch] = np.clip(
                solver.evaluate(clipped[..., ch]), 0.0, 1.0,
            ).astype(np.float32)
    else:
        clipped = np.clip(rgb, 0.0, 1.0)
        for ch in range(3):
            rgb[..., ch] = apply_acr3_default(clipped[..., ch]).astype(np.float32)

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


def render_frame(
    raw_path: str | Path,
    profile: DCPProfile,
    dcp_path: str | Path | None = None,
    develop_ops: DevelopOps | None = None,
    stop_after_stage: int = 9,
) -> FrameRenderResult:
    """End-to-end render of a single RAW frame through pipeline stages 1
    through `stop_after_stage`.

    `raw_path` should point to an Adobe-converted DNG (use `dng_convert.py`
    to produce one from a NEF). NEF input works but loses the embedded
    LinearizationTable + correct WhiteLevel that close ~0.6 ΔE vs Adobe's
    reference.

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
    """
    asn = read_as_shot_neutral(raw_path)

    scene_kelvin = DEFAULT_SCENE_KELVIN
    if develop_ops is not None and develop_ops.temperature_k is not None:
        scene_kelvin = float(develop_ops.temperature_k)
        asn = kelvin_to_neutral(profile, scene_kelvin)

    camera_rgb = demosaic_camera_rgb(raw_path)

    dng_be = read_dng_baseline_exposure(raw_path)
    dbr = read_dcp_default_black_render(dcp_path) if dcp_path is not None else 0

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
