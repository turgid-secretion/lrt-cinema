"""MLX (Apple Metal GPU) fused render path for the FAITHFUL sRGB-TIFF target.

Imported only when the `mlx` backend is selected and `mlx.core` imports (see
`lrt_cinema.accel`). Unlike the numba backend (per-stage CPU kernels that share
memory with numpy), the GPU wants **one upload and one download per frame** — a
round-trip across the bus is ~35 ms, so ping-ponging stages would erase the win.
So this module runs the WHOLE colour render on-device:

    camera RGB ─upload─▶ [stages 2-9 · Stage-11 · Stage-12 FAITHFUL · sRGB encode] ─download─▶ uint16

It mirrors the numpy/numba math (same hexcone HSV, same RefBaselineRGBTone, same
faithful Stage-12 ops) but in `mlx.core` array ops. Accuracy is held to **mean
ΔE2000 « 1.0 vs numpy** (the ship gate is mean-based); the GPU's float/`pow`
rounding and array op-order make it looser at the per-pixel MAX than numba
(boundary pixels can land in an adjacent LookTable cell), so this is the
display-TIFF fast path, NOT a bit-exact reference — numpy remains that.

Scope: the FAITHFUL `lrtimelapse` sRGB path with a ForwardMatrix profile (the
production D750 case). Anything else (no FM / no ProfileToneCurve / PERCEPTUAL /
EXR) raises `MlxUnsupported`; the caller falls back to numba/numpy.

The tone curves (ProfileToneCurve + the per-channel ToneCurvePV2012) are baked
to dense 1-D LUTs on the CPU (the exact spline, 16384 entries → < 1e-6 interp
error, verified) and gathered on-device — MLX has no `searchsorted`, and a LUT
is both exact-enough and faster than a per-pixel spline solve.
"""

from __future__ import annotations

import mlx.core as mx
import numpy as np

# tone LUT resolution — 16384-entry bake of a smooth [0,1]→[0,1] curve has
# < 4e-7 max interp error vs the exact Hermite spline (measured), i.e. far below
# the sRGB 16-bit quantisation step. Frame-invariant; uploaded once.
_LUT_N = 16384


from lrt_cinema.accel import MlxUnsupported  # noqa: E402  (defined at pkg root)

# ---------------------------------------------------------------------------
# scalar-ish helpers in mlx (vectorised over (N,) / (N,3))
# ---------------------------------------------------------------------------


def _srgb_oetf(x):
    """Linear → sRGB encoded (IEC piecewise), mlx. Negatives take the linear
    segment (matches numpy/colour); caller clips later."""
    return mx.where(x <= 0.0031308, x * 12.92,
                    1.055 * mx.power(mx.maximum(x, 0.0), 1.0 / 2.4) - 0.055)


def _rgb_to_hsv(x):
    """(N,3) linear ProPhoto → (h,s,v,valid), Adobe hexcone (h in [0,6))."""
    r, g, b = x[:, 0], x[:, 1], x[:, 2]
    vmin = mx.minimum(mx.minimum(r, g), b)
    vmax = mx.maximum(mx.maximum(r, g), b)
    delta = vmax - vmin
    valid = vmin >= 0.0
    v = vmax
    s = mx.where(vmax > 0.0, delta / mx.where(vmax > 0.0, vmax, 1.0), 0.0)
    sd = mx.where(mx.abs(delta) > 1e-10, delta, 1.0)
    h = mx.where(r == vmax, (g - b) / sd,
                 mx.where(g == vmax, 2.0 + (b - r) / sd, 4.0 + (r - g) / sd))
    h = mx.where(mx.abs(delta) > 1e-10, h, 0.0)
    h = mx.where(h < 0.0, h + 6.0, h)
    h = mx.where(h >= 6.0, h - 6.0, h)
    return h, s, v, valid


def _hsv_to_rgb(h, s, v):
    """Adobe hexcone HSV → (N,3) RGB. h in [0,6)."""
    h = mx.where(h < 0.0, h + 6.0, h)
    h = mx.where(h >= 6.0, h - 6.0, h)
    sector = mx.clip(mx.floor(h).astype(mx.int32), 0, 5)
    f = h - mx.floor(h)
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    cand = mx.stack([
        mx.stack([v, t, p], axis=-1), mx.stack([q, v, p], axis=-1),
        mx.stack([p, v, t], axis=-1), mx.stack([p, q, v], axis=-1),
        mx.stack([t, p, v], axis=-1), mx.stack([v, p, q], axis=-1),
    ], axis=0)
    idx = mx.broadcast_to(sector[None, :, None], (1,) + sector.shape + (3,))
    return mx.take_along_axis(cand, idx, axis=0)[0]


def _lut_eval(z, lut):
    """1-D LUT gather + linear interp at z∈[0,1]. `lut` is (_LUT_N,) mlx."""
    idx = mx.clip(z, 0.0, 1.0) * (_LUT_N - 1)
    i0 = mx.floor(idx).astype(mx.int32)
    fr = idx - i0
    i1 = mx.minimum(i0 + 1, _LUT_N - 1)
    return lut[i0] * (1.0 - fr) + lut[i1] * fr


# ---------------------------------------------------------------------------
# Stage 8/5 cube  +  Stage 9 RefBaselineRGBTone (on-device)
# ---------------------------------------------------------------------------


def _apply_cube(rgb, cube, hd, sd, vd, srgb_gamma):
    h, s, v, valid = _rgb_to_hsv(rgb)
    hsx = h * (hd / 6.0)
    ssx = s * (sd - 1)
    if srgb_gamma:
        vc = mx.maximum(v, 0.0)
        v_enc = mx.where(vc <= 0.0031308, vc * 12.92,
                         1.055 * mx.power(vc, 1.0 / 2.4) - 0.055)
    else:
        v_enc = v
    vsx = v_enc * (vd - 1)
    h0 = mx.clip(mx.floor(hsx).astype(mx.int32), 0, hd - 1)
    h1 = mx.where(h0 >= hd - 1, 0, h0 + 1)
    s0 = mx.clip(mx.floor(ssx).astype(mx.int32), 0, sd - 2)
    s1 = mx.minimum(s0 + 1, sd - 1)
    v0 = mx.clip(mx.floor(vsx).astype(mx.int32), 0, vd - 2)
    v1 = mx.minimum(v0 + 1, vd - 1)
    hf1 = mx.clip(hsx - h0, 0.0, 1.0)
    hf0 = 1.0 - hf1
    sf1 = mx.clip(ssx - s0, 0.0, 1.0)
    sf0 = 1.0 - sf1
    vf1 = mx.clip(vsx - v0, 0.0, 1.0)
    vf0 = 1.0 - vf1

    def g(vi, hi, si):
        return cube[vi, hi, si]
    sm = ((vf0 * hf0 * sf0)[:, None] * g(v0, h0, s0)
          + (vf0 * hf0 * sf1)[:, None] * g(v0, h0, s1)
          + (vf0 * hf1 * sf0)[:, None] * g(v0, h1, s0)
          + (vf0 * hf1 * sf1)[:, None] * g(v0, h1, s1)
          + (vf1 * hf0 * sf0)[:, None] * g(v1, h0, s0)
          + (vf1 * hf0 * sf1)[:, None] * g(v1, h0, s1)
          + (vf1 * hf1 * sf0)[:, None] * g(v1, h1, s0)
          + (vf1 * hf1 * sf1)[:, None] * g(v1, h1, s1))
    h_out = h + sm[:, 0] * (6.0 / 360.0)
    h_out = mx.where(h_out < 0.0, h_out + 6.0, h_out)
    h_out = mx.where(h_out >= 6.0, h_out - 6.0, h_out)
    s_out = mx.clip(s * sm[:, 1], 0.0, 1.0)
    if srgb_gamma:
        veo = mx.clip(v_enc * sm[:, 2], 0.0, 1.0)
        v_out = mx.where(veo <= 0.04045, veo / 12.92,
                         mx.power((veo + 0.055) / 1.055, 2.4))
    else:
        v_out = v * sm[:, 2]
    rgb_post = _hsv_to_rgb(h_out, s_out, v_out)
    return mx.where(valid[:, None], rgb_post, rgb)


def _apply_rgb_tone(rgb, lut):
    """RefBaselineRGBTone via LUT, value-based reassembly (no argsort)."""
    rc = mx.clip(rgb, 0.0, 1.0)
    r, g, b = rc[:, 0], rc[:, 1], rc[:, 2]
    mn = mx.minimum(mx.minimum(r, g), b)
    mxx = mx.maximum(mx.maximum(r, g), b)
    md = r + g + b - mn - mxx
    mno = _lut_eval(mn, lut)
    mxo = _lut_eval(mxx, lut)
    d = mxx - mn
    mdo = mx.where(d > 0.0, mno + (mxo - mno) * (md - mn) / mx.where(d > 0.0, d, 1.0),
                   _lut_eval(md, lut))

    def reasm(ch):
        return mx.where(ch >= mxx, mxo, mx.where(ch <= mn, mno, mdo))
    return mx.clip(mx.stack([reasm(r), reasm(g), reasm(b)], axis=-1), 0.0, 1.0)


# ---------------------------------------------------------------------------
# Stage-12 FAITHFUL ops (on-device)
# ---------------------------------------------------------------------------


def _scale_saturation(rgb, s_map):
    h, s, v, valid = _rgb_to_hsv(rgb)
    rgb_post = _hsv_to_rgb(h, s_map(s), v)
    return mx.where(valid[:, None], rgb_post, rgb)


def _apply_hsl(rgb, hue_pb, sat_pb, lum_pb, centers_lo, centers_hi, nxt_idx,
               lum_sat_gate):
    """8-band partition-of-unity HSL in hexcone HSV (matches numpy apply_hsl).

    `*_pb` are length-8 per-band arrays (hue shift hex / sat factor / lum factor)
    as mlx; `centers_lo/hi`, `nxt_idx` describe the 8 segments. Each hue falls in
    one segment → weight splits linearly between band j and its successor."""
    h, s, v, valid = _rgb_to_hsv(rgb)
    # segment index j: number of centers <= h, minus 1 (h in [centers[j], next))
    # centers_lo is the sorted 8 lower edges; compare and sum.
    ge = (h[:, None] >= centers_lo[None, :]).astype(mx.int32)  # (N,8)
    j = mx.clip(mx.sum(ge, axis=1) - 1, 0, 7)
    lo = centers_lo[j]
    hi = centers_hi[j]
    frac = (h - lo) / (hi - lo)
    nj = nxt_idx[j]
    w_j = 1.0 - frac
    hue_shift = w_j * hue_pb[j] + frac * hue_pb[nj]
    sat_mult = w_j * sat_pb[j] + frac * sat_pb[nj]
    lum_mult = w_j * lum_pb[j] + frac * lum_pb[nj]
    h_out = h + hue_shift
    h_out = mx.where(h_out < 0.0, h_out + 6.0, h_out)
    h_out = mx.where(h_out >= 6.0, h_out - 6.0, h_out)
    s_out = mx.clip(s * sat_mult, 0.0, 1.0)
    s_gate = mx.clip(s / lum_sat_gate, 0.0, 1.0)
    eff_lum = 1.0 + s_gate * (lum_mult - 1.0)
    v_out = mx.maximum(v * eff_lum, 0.0)
    rgb_post = _hsv_to_rgb(h_out, s_out, v_out)
    return mx.where(valid[:, None], rgb_post, rgb)


def _apply_color_grade(rgb, tint_sh, tint_mid, tint_hi, tint_glob, lum_row,
                       blending, balance):
    """Luminance-masked additive split-tone (matches numpy apply_color_grade)."""
    lum = rgb @ lum_row
    perceptual = _srgb_oetf(mx.clip(lum, 0.0, 1.0))
    gamma_balance = 2.0 ** (-balance / 100.0)
    t = mx.power(mx.clip(perceptual, 0.0, 1.0), gamma_balance)
    p = 1.0 + 2.0 * (1.0 - min(max(blending, 0.0), 100.0) / 100.0)
    shadow_w = mx.power(1.0 - t, p)
    highlight_w = mx.power(t, p)
    midtone_w = 1.0 - shadow_w - highlight_w
    out = (rgb + shadow_w[:, None] * tint_sh + midtone_w[:, None] * tint_mid
           + highlight_w[:, None] * tint_hi + tint_glob)
    return mx.maximum(out, 0.0)


def _apply_contrast(rgb, contrast):
    gain = 1.0 + contrast / 100.0
    return mx.maximum(0.18 + (rgb - 0.18) * gain, 0.0)


# ---------------------------------------------------------------------------
# Full-frame faithful renderer (one upload / one download)
# ---------------------------------------------------------------------------


class MlxFaithfulRenderer:
    """Render the FAITHFUL sRGB-TIFF colour path entirely on the Metal GPU.

    Frame-invariant constants (the LookTable cube, the ProfileToneCurve LUT, the
    fixed XYZ↔ProPhoto / ProPhoto→sRGB matrices, the luminance row, the HSL band
    geometry) are uploaded ONCE at construction; `render()` then does one upload
    of the camera RGB and one download of the encoded sRGB. Falls back via
    `MlxUnsupported` for any profile/intent outside the fast path."""

    def __init__(self, profile):
        import colour

        from lrt_cinema.develop_ops import (
            _HSL_BAND_CENTERS_HEX,
            _PROPHOTO_LUMINANCE,
        )
        if profile.forward_matrix_1 is None:
            raise MlxUnsupported("MLX path requires a ForwardMatrix profile")
        if profile.profile_tone_curve is None:
            raise MlxUnsupported("MLX path requires a ProfileToneCurve")
        self.profile = profile
        self._fm1 = profile.forward_matrix_1
        self._fm2 = profile.forward_matrix_2
        self._k1 = profile.kelvin_1
        self._k2 = profile.kelvin_2
        self._m_xyz_pp = np.asarray(
            colour.RGB_COLOURSPACES["ProPhoto RGB"].matrix_XYZ_to_RGB, np.float32)
        # frame-invariant GPU constants
        from lrt_cinema.output import _display_matrix
        self.g_m_xyz_pp = mx.array(self._m_xyz_pp)
        self.g_srgb = mx.array(_display_matrix("srgb"))
        self.g_lum = mx.array(np.asarray(_PROPHOTO_LUMINANCE, np.float32))
        lt = profile.look_table
        self._has_cube = lt is not None
        if self._has_cube:
            self.g_cube = mx.array(np.ascontiguousarray(lt.data_1, np.float32))
            self._hd, self._sd, self._vd = (lt.hue_divisions, lt.sat_divisions,
                                            lt.val_divisions)
            self._cube_srgb = bool(lt.srgb_gamma)
        # ProfileToneCurve → dense LUT (exact spline bake)
        self.g_ptc_lut = mx.array(self._bake_lut(profile.profile_tone_curve))
        # HSL band geometry
        lo = np.asarray(_HSL_BAND_CENTERS_HEX, np.float32)
        hi = np.concatenate([lo[1:], [6.0]]).astype(np.float32)
        self.g_hsl_lo = mx.array(lo)
        self.g_hsl_hi = mx.array(hi)
        self.g_hsl_nxt = mx.array(np.array([(j + 1) % 8 for j in range(8)], np.int32))
        # ramp exposure_value (TotalBaselineExposure) is frame-invariant
        self._be_offset = profile.baseline_exposure_offset

    @staticmethod
    def _bake_lut(curve_pts):
        from lrt_cinema.pipeline import DngSplineSolver
        solver = DngSplineSolver(curve_pts[:, 0], curve_pts[:, 1])
        xs = np.linspace(0.0, 1.0, _LUT_N)
        return np.clip(solver.evaluate(xs), 0.0, 1.0).astype(np.float32)

    def _frame_matrix(self, asn, scene_kelvin):
        """Staged stages 2-4 collapsed to one 3x3 (cam→ProPhoto), matching the
        FM mired-blend in pipeline.apply_adobe_pipeline. Cheap CPU 3x3."""
        wb = 1.0 / np.asarray(asn, np.float64)
        wb = wb / wb[1]
        fm1, fm2 = self._fm1, self._fm2
        if fm2 is not None and not np.allclose(fm1, fm2):
            k_lo, k_hi = sorted([self._k1, self._k2])
            if self._k1 <= self._k2:
                fm_lo, fm_hi = fm1, fm2
            else:
                fm_lo, fm_hi = fm2, fm1
            if scene_kelvin <= k_lo:
                fm = fm_lo
            elif scene_kelvin >= k_hi:
                fm = fm_hi
            else:
                f = (1 / scene_kelvin - 1 / k_lo) / (1 / k_hi - 1 / k_lo)
                fm = (1 - f) * fm_lo + f * fm_hi
        else:
            fm = fm1
        return (self._m_xyz_pp.astype(np.float64) @ fm.astype(np.float64)
                @ np.diag(wb)).astype(np.float32)

    def _stage12_params(self, ops):
        """Per-frame Stage-12 CPU constants (tints, per-band arrays, LUT)."""
        from lrt_cinema.develop_ops import _HSL_HUE_MAX_HEX, _color_grade_wheel_tint
        p = {}
        cg = ops.color_grade
        if not cg.is_identity():
            p["cg"] = (
                mx.array(_color_grade_wheel_tint(cg.shadow_hue, cg.shadow_sat, cg.shadow_lum).astype(np.float32)),
                mx.array(_color_grade_wheel_tint(cg.midtone_hue, cg.midtone_sat, cg.midtone_lum).astype(np.float32)),
                mx.array(_color_grade_wheel_tint(cg.highlight_hue, cg.highlight_sat, cg.highlight_lum).astype(np.float32)),
                mx.array(_color_grade_wheel_tint(cg.global_hue, cg.global_sat, cg.global_lum).astype(np.float32)),
                float(cg.blending), float(cg.balance),
            )
        hsl = ops.hsl
        if not hsl.is_identity():
            p["hsl"] = (
                mx.array((np.asarray(hsl.hue, np.float64) / 100.0 * _HSL_HUE_MAX_HEX).astype(np.float32)),
                mx.array((1.0 + np.asarray(hsl.saturation, np.float64) / 100.0).astype(np.float32)),
                mx.array((1.0 + np.asarray(hsl.luminance, np.float64) / 100.0).astype(np.float32)),
            )
        if len(ops.tone_curve) >= 2:
            xs = np.array([pt.x for pt in ops.tone_curve], np.float64)
            ys = np.array([pt.y for pt in ops.tone_curve], np.float64)
            if not (np.allclose(xs, ys, atol=1e-6) and xs[0] == 0.0 and xs[-1] == 1.0):
                from lrt_cinema.pipeline import DngSplineSolver
                sol = DngSplineSolver(xs, ys)
                xt = np.linspace(0.0, 1.0, _LUT_N)
                p["tc_lut"] = mx.array(np.clip(sol.evaluate(xt), 0.0, 1.0).astype(np.float32))
        return p

    def render(self, camera_rgb, asn, scene_kelvin, ops, dng_baseline_exposure,
               default_black_render):
        """camera RGB (H,W,3) → encoded sRGB float (H,W,3) in [0,1] (pre-quantise).
        FAITHFUL intent only."""
        from lrt_cinema.develop_ops import _HSL_LUM_SAT_GATE
        H, W, _ = camera_rgb.shape
        M = mx.array(self._frame_matrix(asn, scene_kelvin))
        x = mx.array(np.ascontiguousarray(camera_rgb.reshape(-1, 3), np.float32))

        # LRT mask-EV corrections: scene-referred gain pre-Stage-2, mirroring
        # pipeline.render_frame (CLAIMS.md "Exact mask-exposure factor").
        if ops.scene_exposure_ev != 0.0:
            x = x * (2.0 ** ops.scene_exposure_ev)

        pp = x @ M.T                                              # stages 2-4
        # stage 7 ExposureRamp (support_overrange=False, clamp linear to 1)
        ev = dng_baseline_exposure + self._be_offset
        white = 1.0 / pow(2.0, max(0.0, ev))
        black = (0.0 if default_black_render == 1 else 5.0) * 0.001
        black = min(black, 0.99 * white)
        slope = 1.0 / (white - black) if white > black else 1.0
        radius = min(0.5 * black, (1.0 / 16.0) / slope) if slope > 0 else 0.0
        qscale = slope / (4.0 * radius) if radius > 0 else 0.0
        floor_t, ceil_t = black - radius, black + radius
        lin = mx.minimum((pp - black) * slope, 1.0)
        quad = qscale * (pp - floor_t) * (pp - floor_t)
        pp = mx.where(pp >= ceil_t, lin, mx.where(pp <= floor_t, 0.0, quad))
        # stage 8 LookTable
        if self._has_cube:
            pp = _apply_cube(pp, self.g_cube, self._hd, self._sd, self._vd, self._cube_srgb)
        # stage 9 ProfileToneCurve
        pp = _apply_rgb_tone(pp, self.g_ptc_lut)
        # stage 11 exposure / blacks
        if ops.exposure_ev != 0.0:
            pp = pp * (2.0 ** ops.exposure_ev)
        if ops.blacks != 0.0:
            pp = mx.maximum(pp + ops.blacks * 0.0005, 0.0)
        # stage 12 faithful: ToneCurve → Sat → Vib → HSL → ColorGrade → Contrast
        s12 = self._stage12_params(ops)
        if "tc_lut" in s12:
            lutc = s12["tc_lut"]
            ppc = mx.clip(pp, 0.0, 1.0)
            pp = mx.stack([_lut_eval(ppc[:, 0], lutc), _lut_eval(ppc[:, 1], lutc),
                           _lut_eval(ppc[:, 2], lutc)], axis=-1)
        if ops.saturation != 0.0:
            mult = 1.0 + ops.saturation / 100.0
            pp = _scale_saturation(pp, lambda s: mx.clip(s * mult, 0.0, 1.0))
        if ops.vibrance != 0.0:
            k = ops.vibrance / 100.0
            pp = _scale_saturation(pp, lambda s: mx.clip(s + k * s * (1.0 - s), 0.0, 1.0))
        if "hsl" in s12:
            hue_pb, sat_pb, lum_pb = s12["hsl"]
            pp = _apply_hsl(pp, hue_pb, sat_pb, lum_pb, self.g_hsl_lo,
                            self.g_hsl_hi, self.g_hsl_nxt, _HSL_LUM_SAT_GATE)
        if "cg" in s12:
            tsh, tmid, thi, tg, blend, bal = s12["cg"]
            pp = _apply_color_grade(pp, tsh, tmid, thi, tg, self.g_lum, blend, bal)
        if ops.contrast != 0.0:
            pp = _apply_contrast(pp, ops.contrast)
        # stage 13 sRGB encode
        lins = pp @ self.g_srgb.T
        enc = _srgb_oetf(lins)
        mx.eval(enc)
        return np.array(enc).reshape(H, W, 3)
