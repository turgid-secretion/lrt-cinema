"""Numba twin of the clean-room AMaZE demosaic — the production-speed arm.

DESIGN (2026-07-06, owner directive "drive fundamental algorithm efficiency
to 1/50th of current speed"): `_amaze_demosaic._amaze_rggb` is the validated
spec (evidence amaze_port_v1 / amaze_fc3 / pressure_2026-07-06). This module
re-executes the SAME dataflow as compiled, parallel, cache-friendly passes:

  - One numba kernel per group of stages between the numpy twin's reflect-pad
    sync points. Each kernel prange-parallelises over rows and writes into
    the CORE of a preallocated padded buffer; a cheap band kernel then fills
    the 18-px reflect border (exactly `np.pad(mode="reflect")`: rows first,
    then columns over the full padded height). Intermediates that numpy pads
    with zeros (the Nyquist masks) keep zero bands.
  - The two global Nyquist gates (flag extent, `nyq2.any()`) and the
    majority filter run on the host between kernels — bitwise the same
    numpy code as the twin, preserving the whole-image gate semantics.
  - Quantities that live on one CFA coset (hvwt, nyq, delp/delm, dsq_p/m,
    rbint, pmwt, the stage-9 stencils) are only computed on that coset
    (stride-2 loops) — the numpy twin computes them everywhere and provably
    never reads the off-coset values (parity is preserved by every tap and
    by reflection, which maps index -k to +k).

PARITY CONTRACT: with `fastmath=False`, float32 buffers and every constant
frozen to float32, each output element is computed by the same operations
in the same order as the numpy twin — the test suite asserts BIT-EXACT
equality (max |Δ| == 0) against `_amaze_rggb`, full frame including borders.
Any future edit must keep that test green or re-derive the evidence chain.

Buffers: ~30 padded float32 planes (~3 GB transient at 24 MP — fine on the
production box; the numpy twin's temporary churn peaked similar). First call
pays the numba compile (cached to disk via cache=True).
"""

from __future__ import annotations

import numpy as np

try:
    from numba import njit, prange
    NUMBA_OK = True
except Exception:  # pragma: no cover - exercised only without numba
    NUMBA_OK = False

    def njit(*a, **k):  # type: ignore
        def deco(f):
            return f
        return deco

    prange = range  # type: ignore

from ._amaze_demosaic import (
    _GAUSSEVEN,
    _GAUSSGRAD,
    _GAUSSODD,
    _GQUINC,
    _PAD,
)

P = _PAD
# float32 constants — bit-identical to the numpy twin's scalars.
EPS = np.float32(1e-5)
EPSSQ = np.float32(1e-10)
ART = np.float32(0.75)
F0 = np.float32(0.0)
F025 = np.float32(0.25)
F05 = np.float32(0.5)
F1 = np.float32(1.0)
F2 = np.float32(2.0)
F3 = np.float32(3.0)
W1325 = np.float32(1.325)
W0175 = np.float32(0.175)
W0075 = np.float32(0.075)
GO0, GO1, GO2, GO3 = (np.float32(v) for v in _GAUSSODD)
GG0, GG1, GG2, GG3, GG4, GG5 = (np.float32(v) for v in _GAUSSGRAD)
GE0, GE1 = (np.float32(v) for v in _GAUSSEVEN)
GQ0, GQ1, GQ2, GQ3 = (np.float32(v) for v in _GQUINC)


def _alloc(h: int, w: int) -> np.ndarray:
    return np.empty((h + 2 * P, w + 2 * P), dtype=np.float32)


class _Pool:
    """Per-shape buffer pool — page-faulting ~30 fresh 100 MB planes per
    frame costs real time; production renders reuse them across frames.
    Not re-entrant (the render loop is sequential); guarded by a lock so
    concurrent callers degrade to fresh allocations instead of corruption."""

    def __init__(self):
        import threading
        self._lock = threading.Lock()
        self._shape = None
        self._free: list = []
        self._zeroed: dict = {}     # name -> array kept zero-outside-writes

    def take(self, h, w):
        with self._lock:
            if self._shape != (h, w):
                self._shape = (h, w)
                self._free = []
                self._zeroed = {}
            bufs = self._free
            self._free = []
        return bufs

    def give(self, h, w, bufs):
        with self._lock:
            if self._shape == (h, w) and not self._free:
                self._free = bufs

    def zeroed(self, name, shape, dtype):
        """Array that callers promise to leave zero outside the positions
        they rewrite every call (nyq/nyq2 R/B coset, n2 core)."""
        with self._lock:
            a = self._zeroed.get(name)
            if a is None or a.shape != shape or a.dtype != dtype:
                a = np.zeros(shape, dtype=dtype)
                self._zeroed[name] = a
            return a


_POOL = _Pool()


@njit(cache=True)
def _band_reflect(a, h, w):
    """Fill the P-wide border of `a` by reflection of the (h, w) core —
    rows about the core edge first, then columns over the full height
    (np.pad(mode='reflect') axis order)."""
    for k in range(1, P + 1):
        st, dt_ = P + k, P - k
        sb, db = P + h - 1 - k, P + h - 1 + k
        for x in range(P, P + w):
            a[dt_, x] = a[st, x]
            a[db, x] = a[sb, x]
    for k in range(1, P + 1):
        sl, dl = P + k, P - k
        sr, dr = P + w - 1 - k, P + w - 1 + k
        for y in range(h + 2 * P):
            a[y, dl] = a[y, sl]
            a[y, dr] = a[y, sr]


@njit(cache=True)
def _ul(x, y, z):
    lo = min(y, z)
    hi = max(y, z)
    return min(max(x, lo), hi)


# ---- stage 1: gradients ----------------------------------------------------
@njit(cache=True, parallel=True)
def _k1(cp, d0, d1, dhv, h, w):
    for y in prange(h):
        yp = y + P
        for x in range(w):
            xp = x + P
            c0 = cp[yp, xp]
            delh = abs(cp[yp, xp + 1] - cp[yp, xp - 1])
            delv = abs(cp[yp + 1, xp] - cp[yp - 1, xp])
            d0[yp, xp] = EPS + abs(cp[yp + 2, xp] - c0) \
                + abs(c0 - cp[yp - 2, xp]) + delv
            d1[yp, xp] = EPS + abs(cp[yp, xp + 2] - c0) \
                + abs(c0 - cp[yp, xp - 2]) + delh
            dhv[yp, xp] = delh * delh + delv * delv


# ---- stage 2: H/V colour differences ----------------------------------------
@njit(cache=True, parallel=True)
def _k2(cp, d0, d1, vcd, hcd, vca, hca, dgv, dgh, clip_pt8, h, w):
    for y in prange(h):
        yp = y + P
        for x in range(w):
            xp = x + P
            c0 = cp[yp, xp]
            cu = cp[yp - 1, xp]
            cd_ = cp[yp + 1, xp]
            cl = cp[yp, xp - 1]
            cr = cp[yp, xp + 1]
            w0 = d0[yp, xp]
            w1 = d1[yp, xp]

            cru = cu * (d0[yp - 2, xp] + w0) / (
                d0[yp - 2, xp] * (EPS + c0) + w0 * (EPS + cp[yp - 2, xp]))
            crd = cd_ * (d0[yp + 2, xp] + w0) / (
                d0[yp + 2, xp] * (EPS + c0) + w0 * (EPS + cp[yp + 2, xp]))
            crl = cl * (d1[yp, xp - 2] + w1) / (
                d1[yp, xp - 2] * (EPS + c0) + w1 * (EPS + cp[yp, xp - 2]))
            crr = cr * (d1[yp, xp + 2] + w1) / (
                d1[yp, xp + 2] * (EPS + c0) + w1 * (EPS + cp[yp, xp + 2]))

            guha = cu + F05 * (c0 - cp[yp - 2, xp])
            gdha = cd_ + F05 * (c0 - cp[yp + 2, xp])
            glha = cl + F05 * (c0 - cp[yp, xp - 2])
            grha = cr + F05 * (c0 - cp[yp, xp + 2])

            guar = c0 * cru if abs(F1 - cru) < ART else guha
            gdar = c0 * crd if abs(F1 - crd) < ART else gdha
            glar = c0 * crl if abs(F1 - crl) < ART else glha
            grar = c0 * crr if abs(F1 - crr) < ART else grha

            hwt = d1[yp, xp - 1] / (d1[yp, xp - 1] + d1[yp, xp + 1])
            vwt = d0[yp - 1, xp] / (d0[yp + 1, xp] + d0[yp - 1, xp])

            gintv_ha = vwt * gdha + (F1 - vwt) * guha
            ginth_ha = hwt * grha + (F1 - hwt) * glha
            gintv_ar = vwt * gdar + (F1 - vwt) * guar
            ginth_ar = hwt * grar + (F1 - hwt) * glar

            clipped = (c0 > clip_pt8) or (gintv_ha > clip_pt8) \
                or (ginth_ha > clip_pt8)
            if clipped:
                gintv_ar = gintv_ha
                ginth_ar = ginth_ha
                guar = guha
                gdar = gdha
                glar = glha
                grar = grha

            sign = F1 if ((y + x) % 2) == 1 else -F1
            vcd[yp, xp] = sign * (c0 - gintv_ar)
            hcd[yp, xp] = sign * (c0 - ginth_ar)
            vca[yp, xp] = sign * (c0 - gintv_ha)
            hca[yp, xp] = sign * (c0 - ginth_ha)

            a1 = (guha - gdha) * (guha - gdha)
            a2 = (guar - gdar) * (guar - gdar)
            dgv[yp, xp] = min(a1, a2)
            b1 = (glha - grha) * (glha - grha)
            b2 = (glar - grar) * (glar - grar)
            dgh[yp, xp] = min(b1, b2)


# ---- stage 3: estimator selection + saturation bounding ---------------------
@njit(cache=True, parallel=True)
def _k3(cp, vcdi, hcdi, vcai, hcai, vcd, hcd, cdsq, clip_pt, h, w):
    for y in prange(h):
        yp = y + P
        for x in range(w):
            xp = x + P
            c0 = cp[yp, xp]
            h0 = hcdi[yp, xp]
            v0 = vcdi[yp, xp]
            ha0 = hcai[yp, xp]
            va0 = vcai[yp, xp]

            hm = hcdi[yp, xp - 2]
            hp_ = hcdi[yp, xp + 2]
            hcdvar = F3 * (hm * hm + h0 * h0 + hp_ * hp_) \
                - (hm + h0 + hp_) * (hm + h0 + hp_)
            ham = hcai[yp, xp - 2]
            hap = hcai[yp, xp + 2]
            hcdaltvar = F3 * (ham * ham + ha0 * ha0 + hap * hap) \
                - (ham + ha0 + hap) * (ham + ha0 + hap)
            vm = vcdi[yp - 2, xp]
            vp_ = vcdi[yp + 2, xp]
            vcdvar = F3 * (vm * vm + v0 * v0 + vp_ * vp_) \
                - (vm + v0 + vp_) * (vm + v0 + vp_)
            vam = vcai[yp - 2, xp]
            vap = vcai[yp + 2, xp]
            vcdaltvar = F3 * (vam * vam + va0 * va0 + vap * vap) \
                - (vam + va0 + vap) * (vam + va0 + vap)

            hv = ha0 if hcdaltvar < hcdvar else h0
            vv = va0 if vcdaltvar < vcdvar else v0

            cl = cp[yp, xp - 1]
            cr = cp[yp, xp + 1]
            cu = cp[yp - 1, xp]
            cd_ = cp[yp + 1, xp]

            if ((y + x) % 2) == 1:            # G site
                ginth_g = c0 - hv
                gintv_g = c0 - vv
                if hv > F0:
                    if F3 * hv > (ginth_g + c0):
                        hv = -_ul(ginth_g, cl, cr) + c0
                    else:
                        t = F3 * hv / (EPS + ginth_g + c0)
                        hv = (F1 - t) * hv + t * (-_ul(ginth_g, cl, cr) + c0)
                if vv > F0:
                    if F3 * vv > (gintv_g + c0):
                        vv = -_ul(gintv_g, cu, cd_) + c0
                    else:
                        t = F3 * vv / (EPS + gintv_g + c0)
                        vv = (F1 - t) * vv + t * (-_ul(gintv_g, cu, cd_) + c0)
                if ginth_g > clip_pt:
                    hv = -_ul(ginth_g, cl, cr) + c0
                if gintv_g > clip_pt:
                    vv = -_ul(gintv_g, cu, cd_) + c0
            else:                              # R/B site
                ginth_rb = hv + c0
                gintv_rb = vv + c0
                if hv < F0:
                    if F3 * hv < -(ginth_rb + c0):
                        hv = _ul(ginth_rb, cl, cr) - c0
                    else:
                        t = -F3 * hv / (EPS + ginth_rb + c0)
                        hv = (F1 - t) * hv + t * (_ul(ginth_rb, cl, cr) - c0)
                if vv < F0:
                    if F3 * vv < -(gintv_rb + c0):
                        vv = _ul(gintv_rb, cu, cd_) - c0
                    else:
                        t = -F3 * vv / (EPS + gintv_rb + c0)
                        vv = (F1 - t) * vv + t * (_ul(gintv_rb, cu, cd_) - c0)
                if ginth_rb > clip_pt:
                    hv = _ul(ginth_rb, cl, cr) - c0
                if gintv_rb > clip_pt:
                    vv = _ul(gintv_rb, cu, cd_) - c0

            hcd[yp, xp] = hv
            vcd[yp, xp] = vv
            cdsq[yp, xp] = (vv - hv) * (vv - hv)


# ---- stage 4: hvwt at R/B sites + Nyquist test -------------------------------
@njit(cache=True, parallel=True)
def _k4(d0, d1, vcd, hcd, dgv, dgh, cdsq, dhv, hvwt, nyq, h, w):
    for y in prange(h):
        yp = y + P
        for x in range(y & 1, w, 2):          # R/B coset: (y+x) even
            xp = x + P
            v0 = vcd[yp, xp]
            h0 = hcd[yp, xp]

            uave = v0 + vcd[yp - 1, xp] + vcd[yp - 2, xp] + vcd[yp - 3, xp]
            dave = v0 + vcd[yp + 1, xp] + vcd[yp + 2, xp] + vcd[yp + 3, xp]
            lave = h0 + hcd[yp, xp - 1] + hcd[yp, xp - 2] + hcd[yp, xp - 3]
            rave = h0 + hcd[yp, xp + 1] + hcd[yp, xp + 2] + hcd[yp, xp + 3]

            dv_u = (v0 - uave) * (v0 - uave)
            dv_u += (vcd[yp - 1, xp] - uave) * (vcd[yp - 1, xp] - uave)
            dv_u += (vcd[yp - 2, xp] - uave) * (vcd[yp - 2, xp] - uave)
            dv_u += (vcd[yp - 3, xp] - uave) * (vcd[yp - 3, xp] - uave)
            dv_d = (v0 - dave) * (v0 - dave)
            dv_d += (vcd[yp + 1, xp] - dave) * (vcd[yp + 1, xp] - dave)
            dv_d += (vcd[yp + 2, xp] - dave) * (vcd[yp + 2, xp] - dave)
            dv_d += (vcd[yp + 3, xp] - dave) * (vcd[yp + 3, xp] - dave)
            dh_l = (h0 - lave) * (h0 - lave)
            dh_l += (hcd[yp, xp - 1] - lave) * (hcd[yp, xp - 1] - lave)
            dh_l += (hcd[yp, xp - 2] - lave) * (hcd[yp, xp - 2] - lave)
            dh_l += (hcd[yp, xp - 3] - lave) * (hcd[yp, xp - 3] - lave)
            dh_r = (h0 - rave) * (h0 - rave)
            dh_r += (hcd[yp, xp + 1] - rave) * (hcd[yp, xp + 1] - rave)
            dh_r += (hcd[yp, xp + 2] - rave) * (hcd[yp, xp + 2] - rave)
            dh_r += (hcd[yp, xp + 3] - rave) * (hcd[yp, xp + 3] - rave)

            hwt = d1[yp, xp - 1] / (d1[yp, xp - 1] + d1[yp, xp + 1])
            vwt = d0[yp - 1, xp] / (d0[yp + 1, xp] + d0[yp - 1, xp])

            vcdvar4 = EPSSQ + vwt * dv_d + (F1 - vwt) * dv_u
            hcdvar4 = EPSSQ + hwt * dh_r + (F1 - hwt) * dh_l

            fv_u = dgv[yp, xp] + dgv[yp - 1, xp] + dgv[yp - 2, xp]
            fv_d = dgv[yp, xp] + dgv[yp + 1, xp] + dgv[yp + 2, xp]
            fh_l = dgh[yp, xp] + dgh[yp, xp - 1] + dgh[yp, xp - 2]
            fh_r = dgh[yp, xp] + dgh[yp, xp + 1] + dgh[yp, xp + 2]
            vcdvar1 = EPSSQ + vwt * fv_d + (F1 - vwt) * fv_u
            hcdvar1 = EPSSQ + hwt * fh_r + (F1 - hwt) * fh_l

            varwt = hcdvar4 / (vcdvar4 + hcdvar4)
            diffwt = hcdvar1 / (vcdvar1 + hcdvar1)
            agree = ((F05 - varwt) * (F05 - diffwt) > F0) and (
                abs(F05 - diffwt) < abs(F05 - varwt))
            hvwt[yp, xp] = varwt if agree else diffwt

            nyqutest = (
                GO0 * cdsq[yp, xp]
                + GO1 * (cdsq[yp - 1, xp - 1] + cdsq[yp - 1, xp + 1]
                         + cdsq[yp + 1, xp - 1] + cdsq[yp + 1, xp + 1])
                + GO2 * (cdsq[yp - 2, xp] + cdsq[yp, xp - 2]
                         + cdsq[yp, xp + 2] + cdsq[yp + 2, xp])
                + GO3 * (cdsq[yp - 2, xp - 2] + cdsq[yp - 2, xp + 2]
                         + cdsq[yp + 2, xp - 2] + cdsq[yp + 2, xp + 2])
            ) - (
                GG0 * dhv[yp, xp]
                + GG1 * (dhv[yp - 1, xp] + dhv[yp, xp - 1]
                         + dhv[yp, xp + 1] + dhv[yp + 1, xp])
                + GG2 * (dhv[yp - 1, xp - 1] + dhv[yp - 1, xp + 1]
                         + dhv[yp + 1, xp - 1] + dhv[yp + 1, xp + 1])
                + GG3 * (dhv[yp - 2, xp] + dhv[yp, xp - 2]
                         + dhv[yp, xp + 2] + dhv[yp + 2, xp])
                + GG4 * (dhv[yp - 2, xp - 1] + dhv[yp - 2, xp + 1]
                         + dhv[yp - 1, xp - 2] + dhv[yp - 1, xp + 2]
                         + dhv[yp + 1, xp - 2] + dhv[yp + 1, xp + 2]
                         + dhv[yp + 2, xp - 1] + dhv[yp + 2, xp + 1])
                + GG5 * (dhv[yp - 2, xp - 2] + dhv[yp - 2, xp + 2]
                         + dhv[yp + 2, xp - 2] + dhv[yp + 2, xp + 2]))
            nyq[y, x] = 1 if nyqutest > F0 else 0


# ---- stage 5 host-gate replacements (exact integer semantics) ---------------
@njit(cache=True, parallel=True)
def _nyq_extent(nyq, row_any, col_any, h, w):
    """row_any[y]=1 iff row y has a flag; col_any[x] likewise (benign
    same-value write races on col_any)."""
    for y in prange(h):
        r = 0
        for x in range(y & 1, w, 2):
            if nyq[y, x] != 0:
                r = 1
                col_any[x] = 1
        row_any[y] = r


@njit(cache=True, parallel=True)
def _nyq_majority(nyq, nyq2, h, w):
    """Majority filter over the 8 same-coset neighbours, zero-padded
    boundary — bit-equal to the numpy twin's np.pad(constant) + count:
    cnt>4 -> set, cnt<4 -> clear, ==4 -> keep. R/B coset only (G stays 0)."""
    for y in prange(h):
        for x in range(y & 1, w, 2):
            cnt = 0
            if y >= 2 and nyq[y - 2, x] != 0:
                cnt += 1
            if y >= 1:
                if x >= 1 and nyq[y - 1, x - 1] != 0:
                    cnt += 1
                if x + 1 < w and nyq[y - 1, x + 1] != 0:
                    cnt += 1
            if x >= 2 and nyq[y, x - 2] != 0:
                cnt += 1
            if x + 2 < w and nyq[y, x + 2] != 0:
                cnt += 1
            if y + 1 < h:
                if x >= 1 and nyq[y + 1, x - 1] != 0:
                    cnt += 1
                if x + 1 < w and nyq[y + 1, x + 1] != 0:
                    cnt += 1
            if y + 2 < h and nyq[y + 2, x] != 0:
                cnt += 1
            if cnt > 4:
                nyq2[y, x] = 1
            elif cnt < 4:
                nyq2[y, x] = 0
            else:
                nyq2[y, x] = nyq[y, x]


@njit(cache=True, parallel=True)
def _fill_n2(n2, nyq2, h, w):
    """n2 core R/B coset <- nyq2 as float32 (only coset ever read; the
    zero band and G sites are preserved from the pooled zero state)."""
    for y in prange(h):
        yp = y + P
        for x in range(y & 1, w, 2):
            n2[yp, x + P] = np.float32(nyq2[y, x])


# ---- stage 5: Nyquist area statistics ---------------------------------------
@njit(cache=True, parallel=True)
def _k5a(cp, sh_h, sh_v, sq_h, sq_v, h, w):
    for y in prange(h):
        yp = y + P
        for x in range(y & 1, w, 2):          # R/B coset (taps are even offsets)
            xp = x + P
            c0 = cp[yp, xp]
            cl = cp[yp, xp - 1]
            cr = cp[yp, xp + 1]
            cu = cp[yp - 1, xp]
            cd_ = cp[yp + 1, xp]
            sh_h[yp, xp] = cl + cr
            sh_v[yp, xp] = cu + cd_
            sq_h[yp, xp] = (c0 - cl) * (c0 - cl) + (c0 - cr) * (c0 - cr)
            sq_v[yp, xp] = (c0 - cu) * (c0 - cu) + (c0 - cd_) * (c0 - cd_)


@njit(cache=True, parallel=True)
def _k5b(cp, n2, sh_h, sh_v, sq_h, sq_v, nyq2, hvwt, hvwt2, h, w):
    for y in prange(h):
        yp = y + P
        for x in range(y & 1, w, 2):
            xp = x + P
            if nyq2[y, x] == 0:
                hvwt2[yp, xp] = hvwt[yp, xp]
                continue
            sumcfa = F0
            sumh = F0
            sumv = F0
            sumsqh = F0
            sumsqv = F0
            areawt = F0
            for dy in range(-6, 7, 2):
                for dx in range(-6, 7, 2):
                    m = n2[yp + dy, xp + dx]
                    sumcfa += m * cp[yp + dy, xp + dx]
                    sumh += m * sh_h[yp + dy, xp + dx]
                    sumv += m * sh_v[yp + dy, xp + dx]
                    sumsqh += m * sq_h[yp + dy, xp + dx]
                    sumsqv += m * sq_v[yp + dy, xp + dx]
                    areawt += m
            sumh = sumcfa - F05 * sumh
            sumv = sumcfa - F05 * sumv
            areawt = F05 * areawt
            hv_ = EPSSQ + abs(areawt * sumsqh - sumh * sumh)
            vv_ = EPSSQ + abs(areawt * sumsqv - sumv * sumv)
            hvwt2[yp, xp] = hv_ / (vv_ + hv_)


# ---- stage 6: G at R/B sites -------------------------------------------------
@njit(cache=True, parallel=True)
def _k6(cp, vcd, hcd, hvwt, hvwt3, dgrb0, green, h, w):
    for y in prange(h):
        yp = y + P
        for x in range(w):
            xp = x + P
            if ((y + x) % 2) == 1:            # G site
                green[yp, xp] = cp[yp, xp]
                continue
            hv = hvwt[yp, xp]
            alt = F025 * (hvwt[yp - 1, xp - 1] + hvwt[yp - 1, xp + 1]
                          + hvwt[yp + 1, xp - 1] + hvwt[yp + 1, xp + 1])
            if abs(F05 - hv) < abs(F05 - alt):
                hv = alt
            hvwt3[yp, xp] = hv
            d = hv * vcd[yp, xp] + (F1 - hv) * hcd[yp, xp]
            dgrb0[yp, xp] = d
            green[yp, xp] = cp[yp, xp] + d


@njit(cache=True, parallel=True)
def _k7a(green, nyq2, cvh, cvv, h, w):
    for y in prange(h):
        yp = y + P
        for x in range(y & 1, w, 2):
            xp = x + P
            if nyq2[y, x] == 0:
                cvh[yp, xp] = F0
                cvv[yp, xp] = F0
            else:
                g0 = green[yp, xp]
                th = g0 - F05 * (green[yp, xp - 1] + green[yp, xp + 1])
                tv = g0 - F05 * (green[yp - 1, xp] + green[yp + 1, xp])
                cvh[yp, xp] = th * th
                cvv[yp, xp] = tv * tv


@njit(cache=True, parallel=True)
def _k7b(cp, cvh, cvv, vcd, hcd, nyq2, dgrb0, green, h, w):
    for y in prange(h):
        yp = y + P
        for x in range(y & 1, w, 2):
            xp = x + P
            if nyq2[y, x] == 0:
                continue
            gvarh = EPSSQ + (
                GQ0 * cvh[yp, xp]
                + GQ1 * (cvh[yp - 1, xp - 1] + cvh[yp - 1, xp + 1]
                         + cvh[yp + 1, xp - 1] + cvh[yp + 1, xp + 1])
                + GQ2 * (cvh[yp - 2, xp] + cvh[yp, xp - 2]
                         + cvh[yp, xp + 2] + cvh[yp + 2, xp])
                + GQ3 * (cvh[yp - 2, xp - 2] + cvh[yp - 2, xp + 2]
                         + cvh[yp + 2, xp - 2] + cvh[yp + 2, xp + 2]))
            gvarv = EPSSQ + (
                GQ0 * cvv[yp, xp]
                + GQ1 * (cvv[yp - 1, xp - 1] + cvv[yp - 1, xp + 1]
                         + cvv[yp + 1, xp - 1] + cvv[yp + 1, xp + 1])
                + GQ2 * (cvv[yp - 2, xp] + cvv[yp, xp - 2]
                         + cvv[yp, xp + 2] + cvv[yp + 2, xp])
                + GQ3 * (cvv[yp - 2, xp - 2] + cvv[yp - 2, xp + 2]
                         + cvv[yp + 2, xp - 2] + cvv[yp + 2, xp + 2]))
            d = (hcd[yp, xp] * gvarv + vcd[yp, xp] * gvarh) / (gvarv + gvarh)
            dgrb0[yp, xp] = d
            green[yp, xp] = cp[yp, xp] + d


# ---- stage 7: diagonal R/B interpolation -------------------------------------
@njit(cache=True, parallel=True)
def _k8a(cp, delp, delm, dsqp, dsqm, h, w):
    for y in prange(h):
        yp = y + P
        for x in range(w):
            xp = x + P
            if ((y + x) % 2) == 0:            # R/B coset: delp/delm
                delp[yp, xp] = abs(cp[yp - 1, xp + 1] - cp[yp + 1, xp - 1])
                delm[yp, xp] = abs(cp[yp + 1, xp + 1] - cp[yp - 1, xp - 1])
            else:                              # G coset: Dgrbsq
                c0 = cp[yp, xp]
                a = c0 - cp[yp - 1, xp + 1]
                b = c0 - cp[yp + 1, xp - 1]
                dsqp[yp, xp] = a * a + b * b
                a = c0 - cp[yp + 1, xp + 1]
                b = c0 - cp[yp - 1, xp - 1]
                dsqm[yp, xp] = a * a + b * b


@njit(cache=True, parallel=True)
def _k8b(cp, delp, delm, dsqp, dsqm, rbp_o, rbm_o, pmwt, clip_pt, h, w):
    for y in prange(h):
        yp = y + P
        for x in range(y & 1, w, 2):
            xp = x + P
            c0 = cp[yp, xp]
            cse = cp[yp + 1, xp + 1]
            cnw = cp[yp - 1, xp - 1]
            cne = cp[yp - 1, xp + 1]
            csw = cp[yp + 1, xp - 1]

            crse = F2 * cse / (EPS + c0 + cp[yp + 2, xp + 2])
            crnw = F2 * cnw / (EPS + c0 + cp[yp - 2, xp - 2])
            crne = F2 * cne / (EPS + c0 + cp[yp - 2, xp + 2])
            crsw = F2 * csw / (EPS + c0 + cp[yp + 2, xp - 2])

            rbse = c0 * crse if abs(F1 - crse) < ART \
                else cse + F05 * (c0 - cp[yp + 2, xp + 2])
            rbnw = c0 * crnw if abs(F1 - crnw) < ART \
                else cnw + F05 * (c0 - cp[yp - 2, xp - 2])
            rbne = c0 * crne if abs(F1 - crne) < ART \
                else cne + F05 * (c0 - cp[yp - 2, xp + 2])
            rbsw = c0 * crsw if abs(F1 - crsw) < ART \
                else csw + F05 * (c0 - cp[yp + 2, xp - 2])

            dm0 = delm[yp, xp]
            dp0 = delp[yp, xp]
            wtse = EPS + dm0 + delm[yp + 1, xp + 1] + delm[yp + 2, xp + 2]
            wtnw = EPS + dm0 + delm[yp - 1, xp - 1] + delm[yp - 2, xp - 2]
            wtne = EPS + dp0 + delp[yp - 1, xp + 1] + delp[yp - 2, xp + 2]
            wtsw = EPS + dp0 + delp[yp + 1, xp - 1] + delp[yp + 2, xp - 2]

            rbm = (wtse * rbnw + wtnw * rbse) / (wtse + wtnw)
            rbp = (wtne * rbsw + wtsw * rbne) / (wtne + wtsw)

            rbvarm = EPSSQ + (
                GE0 * (dsqm[yp - 1, xp] + dsqm[yp, xp - 1]
                       + dsqm[yp, xp + 1] + dsqm[yp + 1, xp])
                + GE1 * (dsqm[yp - 2, xp - 1] + dsqm[yp - 2, xp + 1]
                         + dsqm[yp - 1, xp - 2] + dsqm[yp - 1, xp + 2]
                         + dsqm[yp + 1, xp - 2] + dsqm[yp + 1, xp + 2]
                         + dsqm[yp + 2, xp - 1] + dsqm[yp + 2, xp + 1]))
            rbvarp = EPSSQ + (
                GE0 * (dsqp[yp - 1, xp] + dsqp[yp, xp - 1]
                       + dsqp[yp, xp + 1] + dsqp[yp + 1, xp])
                + GE1 * (dsqp[yp - 2, xp - 1] + dsqp[yp - 2, xp + 1]
                         + dsqp[yp - 1, xp - 2] + dsqp[yp - 1, xp + 2]
                         + dsqp[yp + 1, xp - 2] + dsqp[yp + 1, xp + 2]
                         + dsqp[yp + 2, xp - 1] + dsqp[yp + 2, xp + 1]))
            pmwt[yp, xp] = rbvarm / (rbvarp + rbvarm)

            if rbp < c0:
                if F2 * rbp < c0:
                    rbp = _ul(rbp, csw, cne)
                else:
                    pwt = F2 * (c0 - rbp) / (EPS + rbp + c0)
                    rbp = pwt * rbp + (F1 - pwt) * _ul(rbp, csw, cne)
            if rbm < c0:
                if F2 * rbm < c0:
                    rbm = _ul(rbm, cnw, cse)
                else:
                    mwt = F2 * (c0 - rbm) / (EPS + rbm + c0)
                    rbm = mwt * rbm + (F1 - mwt) * _ul(rbm, cnw, cse)
            if rbp > clip_pt:
                rbp = _ul(rbp, csw, cne)
            if rbm > clip_pt:
                rbm = _ul(rbm, cnw, cse)
            rbp_o[yp, xp] = rbp
            rbm_o[yp, xp] = rbm


@njit(cache=True, parallel=True)
def _k8c(cp, pmwt, rbp_o, rbm_o, pmwt3, rbint, h, w):
    for y in prange(h):
        yp = y + P
        for x in range(y & 1, w, 2):
            xp = x + P
            pm = pmwt[yp, xp]
            alt = F025 * (pmwt[yp - 1, xp - 1] + pmwt[yp - 1, xp + 1]
                          + pmwt[yp + 1, xp - 1] + pmwt[yp + 1, xp + 1])
            if abs(F05 - pm) < abs(F05 - alt):
                pm = alt
            pmwt3[yp, xp] = pm
            rbint[yp, xp] = F05 * (cp[yp, xp]
                                   + rbm_o[yp, xp] * (F1 - pm)
                                   + rbp_o[yp, xp] * pm)


# ---- stage 8: diagonal correction of G ----------------------------------------
@njit(cache=True, parallel=True)
def _k9(cp, d0, d1, rbint, hvwt3, pmwt3, dgrb0, green, dgrb_r, dgrb_b,
        clip_pt, h, w):
    for y in prange(h):
        yp = y + P
        for x in range(y & 1, w, 2):
            xp = x + P
            hv = hvwt3[yp, xp]
            if abs(F05 - pmwt3[yp, xp]) >= abs(F05 - hv):
                rb = rbint[yp, xp]
                cu = cp[yp - 1, xp]
                cd_ = cp[yp + 1, xp]
                cl = cp[yp, xp - 1]
                cr = cp[yp, xp + 1]

                cru2 = cu * F2 / (EPS + rb + rbint[yp - 2, xp])
                crd2 = cd_ * F2 / (EPS + rb + rbint[yp + 2, xp])
                crl2 = cl * F2 / (EPS + rb + rbint[yp, xp - 2])
                crr2 = cr * F2 / (EPS + rb + rbint[yp, xp + 2])

                gu2 = rb * cru2 if abs(F1 - cru2) < ART \
                    else cu + F05 * (rb - rbint[yp - 2, xp])
                gd2 = rb * crd2 if abs(F1 - crd2) < ART \
                    else cd_ + F05 * (rb - rbint[yp + 2, xp])
                gl2 = rb * crl2 if abs(F1 - crl2) < ART \
                    else cl + F05 * (rb - rbint[yp, xp - 2])
                gr2 = rb * crr2 if abs(F1 - crr2) < ART \
                    else cr + F05 * (rb - rbint[yp, xp + 2])

                gintv2 = (d0[yp - 1, xp] * gd2 + d0[yp + 1, xp] * gu2) / (
                    d0[yp + 1, xp] + d0[yp - 1, xp])
                ginth2 = (d1[yp, xp - 1] * gr2 + d1[yp, xp + 1] * gl2) / (
                    d1[yp, xp - 1] + d1[yp, xp + 1])

                if gintv2 < rb:
                    if F2 * gintv2 < rb:
                        gintv2 = _ul(gintv2, cu, cd_)
                    else:
                        vwt2 = F2 * (rb - gintv2) / (EPS + gintv2 + rb)
                        gintv2 = vwt2 * gintv2 \
                            + (F1 - vwt2) * _ul(gintv2, cu, cd_)
                if ginth2 < rb:
                    if F2 * ginth2 < rb:
                        ginth2 = _ul(ginth2, cl, cr)
                    else:
                        hwt2 = F2 * (rb - ginth2) / (EPS + ginth2 + rb)
                        ginth2 = hwt2 * ginth2 \
                            + (F1 - hwt2) * _ul(ginth2, cl, cr)
                if ginth2 > clip_pt:
                    ginth2 = _ul(ginth2, cl, cr)
                if gintv2 > clip_pt:
                    gintv2 = _ul(gintv2, cu, cd_)

                gnew = ginth2 * (F1 - hv) + gintv2 * hv
                green[yp, xp] = gnew
                dgrb0[yp, xp] = gnew - cp[yp, xp]

            # split native chroma onto the two planes (stage 9 setup)
            d = dgrb0[yp, xp]
            if (y % 2) == 0:                   # R site
                dgrb_r[yp, xp] = d
                dgrb_b[yp, xp] = F0
            else:                              # B site
                dgrb_r[yp, xp] = F0
                dgrb_b[yp, xp] = d


# ---- stage 9: chrominance propagation -----------------------------------------
@njit(cache=True, parallel=True)
def _k10(dg, out, ry, h, w):
    """4-diagonal chroma stencil: interpolate plane `dg` onto the R/B rows
    of parity `ry` (0 → write at R rows for dgrb_b, 1 → B rows for dgrb_r).
    Reads only the opposite R/B sub-coset (both-odd offsets); writes are
    coset-disjoint from reads, so in/out may alias — kept separate for
    clarity."""
    for i in prange((h - ry + 1) // 2):
        y = ry + 2 * i
        yp = y + P
        for x in range(y & 1, w, 2):
            xp = x + P
            wtnw2 = F1 / (EPS + abs(dg[yp - 1, xp - 1] - dg[yp + 1, xp + 1])
                          + abs(dg[yp - 1, xp - 1] - dg[yp - 3, xp - 3])
                          + abs(dg[yp + 1, xp + 1] - dg[yp - 3, xp - 3]))
            wtne2 = F1 / (EPS + abs(dg[yp - 1, xp + 1] - dg[yp + 1, xp - 1])
                          + abs(dg[yp - 1, xp + 1] - dg[yp - 3, xp + 3])
                          + abs(dg[yp + 1, xp - 1] - dg[yp - 3, xp + 3]))
            wtsw2 = F1 / (EPS + abs(dg[yp + 1, xp - 1] - dg[yp - 1, xp + 1])
                          + abs(dg[yp + 1, xp - 1] - dg[yp + 3, xp + 3])
                          + abs(dg[yp - 1, xp + 1] - dg[yp + 3, xp - 3]))
            wtse2 = F1 / (EPS + abs(dg[yp + 1, xp + 1] - dg[yp - 1, xp - 1])
                          + abs(dg[yp + 1, xp + 1] - dg[yp + 3, xp - 3])
                          + abs(dg[yp - 1, xp - 1] - dg[yp + 3, xp + 3]))
            est = (wtnw2 * (W1325 * dg[yp - 1, xp - 1]
                            - W0175 * dg[yp - 3, xp - 3]
                            - W0075 * dg[yp - 1, xp - 3]
                            - W0075 * dg[yp - 3, xp - 1])
                   + wtne2 * (W1325 * dg[yp - 1, xp + 1]
                              - W0175 * dg[yp - 3, xp + 3]
                              - W0075 * dg[yp - 1, xp + 3]
                              - W0075 * dg[yp + 1, xp + 1])
                   + wtsw2 * (W1325 * dg[yp + 1, xp - 1]
                              - W0175 * dg[yp + 3, xp - 3]
                              - W0075 * dg[yp + 1, xp - 3]
                              - W0075 * dg[yp - 1, xp - 1])
                   + wtse2 * (W1325 * dg[yp + 1, xp + 1]
                              - W0175 * dg[yp + 3, xp + 3]
                              - W0075 * dg[yp + 1, xp + 3]
                              - W0075 * dg[yp + 3, xp + 1])
                   ) / (wtnw2 + wtne2 + wtsw2 + wtse2)
            out[yp, xp] = est


@njit(cache=True, parallel=True)
def _k11(cp, green, hvwt3, dgrb_r, dgrb_b, rgb, h, w):
    for y in prange(h):
        yp = y + P
        for x in range(w):
            xp = x + P
            g0 = green[yp, xp]
            if ((y + x) % 2) == 1:            # G site
                w_u = hvwt3[yp - 1, xp]
                w_d = hvwt3[yp + 1, xp]
                w_l = F1 - hvwt3[yp, xp - 1]
                w_r = F1 - hvwt3[yp, xp + 1]
                wsum = w_u + w_d + w_l + w_r
                r = g0 - (w_u * dgrb_r[yp - 1, xp] + w_d * dgrb_r[yp + 1, xp]
                          + w_l * dgrb_r[yp, xp - 1]
                          + w_r * dgrb_r[yp, xp + 1]) / wsum
                b = g0 - (w_u * dgrb_b[yp - 1, xp] + w_d * dgrb_b[yp + 1, xp]
                          + w_l * dgrb_b[yp, xp - 1]
                          + w_r * dgrb_b[yp, xp + 1]) / wsum
            else:
                r = g0 - dgrb_r[yp, xp]
                b = g0 - dgrb_b[yp, xp]
            # nan_to_num(nan=0.5, posinf=1.0, neginf=0.0) then clip [0, 1]
            if r != r:
                r = F05
            elif r == np.inf:
                r = F1
            elif r == -np.inf:
                r = F0
            if g0 != g0:
                g0 = F05
            elif g0 == np.inf:
                g0 = F1
            elif g0 == -np.inf:
                g0 = F0
            if b != b:
                b = F05
            elif b == np.inf:
                b = F1
            elif b == -np.inf:
                b = F0
            rgb[y, x, 0] = min(max(r, F0), F1)
            rgb[y, x, 1] = min(max(g0, F0), F1)
            rgb[y, x, 2] = min(max(b, F0), F1)


def _amaze_rggb_fast(cfa: np.ndarray, clip_pt: np.float32) -> np.ndarray:
    """Numba-pass execution of `_amaze_demosaic._amaze_rggb` (bit-exact)."""
    h, w = cfa.shape
    clip_pt = np.float32(clip_pt)
    clip_pt8 = np.float32(np.float32(0.8) * clip_pt)

    pool = _POOL.take(h, w)
    used: list = []

    def buf():
        a = pool.pop() if pool else _alloc(h, w)
        used.append(a)
        return a

    cp = buf()
    cp[P:P + h, P:P + w] = cfa
    _band_reflect(cp, h, w)

    d0, d1, dhv = buf(), buf(), buf()
    _k1(cp, d0, d1, dhv, h, w)
    _band_reflect(d0, h, w)
    _band_reflect(d1, h, w)
    _band_reflect(dhv, h, w)

    vcdA, hcdA, vcaA, hcaA, dgv, dgh = (buf() for _ in range(6))
    _k2(cp, d0, d1, vcdA, hcdA, vcaA, hcaA, dgv, dgh, clip_pt8, h, w)
    for a in (vcdA, hcdA, vcaA, hcaA, dgv, dgh):
        _band_reflect(a, h, w)

    vcd, hcd, cdsq = buf(), buf(), buf()
    _k3(cp, vcdA, hcdA, vcaA, hcaA, vcd, hcd, cdsq, clip_pt, h, w)
    _band_reflect(vcd, h, w)
    _band_reflect(hcd, h, w)
    _band_reflect(cdsq, h, w)

    hvwt = vcdA          # stage-2 buffers are dead — reuse
    nyq = _POOL.zeroed("nyq", (h, w), np.uint8)   # G coset stays 0; R/B rewritten
    _k4(d0, d1, vcd, hcd, dgv, dgh, cdsq, dhv, hvwt, nyq, h, w)
    _band_reflect(hvwt, h, w)

    # ---- global Nyquist gates + majority filter (numpy-twin semantics) ----
    row_any = np.zeros(h, dtype=np.uint8)
    col_any = np.zeros(w, dtype=np.uint8)
    _nyq_extent(nyq, row_any, col_any, h, w)
    do_nyq = int(row_any.sum()) > 1 and int(col_any.sum()) > 1
    if do_nyq:
        nyq2 = _POOL.zeroed("nyq2", (h, w), np.uint8)  # R/B fully rewritten
        _nyq_majority(nyq, nyq2, h, w)
        do_nyq2 = bool(nyq2.any())
    else:
        nyq2 = _POOL.zeroed("nyq2_zero", (h, w), np.uint8)  # never written
        do_nyq2 = False

    if do_nyq2:
        sh_h, sh_v, sq_h, sq_v = hcaA, vcaA, dgv, dgh  # dead — reuse
        _k5a(cp, sh_h, sh_v, sq_h, sq_v, h, w)
        for a in (sh_h, sh_v, sq_h, sq_v):
            _band_reflect(a, h, w)
        n2 = _POOL.zeroed("n2", (h + 2 * P, w + 2 * P), np.float32)
        _fill_n2(n2, nyq2, h, w)     # zero band + G coset preserved
        hvwt2 = hcdA
        _k5b(cp, n2, sh_h, sh_v, sq_h, sq_v, nyq2, hvwt, hvwt2, h, w)
        _band_reflect(hvwt2, h, w)
    else:
        hvwt2 = hvwt

    hvwt3, dgrb0, green = buf(), buf(), buf()
    _k6(cp, vcd, hcd, hvwt2, hvwt3, dgrb0, green, h, w)
    _band_reflect(hvwt3, h, w)
    _band_reflect(green, h, w)

    if do_nyq2:
        cvh, cvv = buf(), buf()
        _k7a(green, nyq2, cvh, cvv, h, w)
        _band_reflect(cvh, h, w)
        _band_reflect(cvv, h, w)
        _k7b(cp, cvh, cvv, vcd, hcd, nyq2, dgrb0, green, h, w)

    delp, delm, dsqp, dsqm = buf(), buf(), buf(), buf()
    _k8a(cp, delp, delm, dsqp, dsqm, h, w)
    for a in (delp, delm, dsqp, dsqm):
        _band_reflect(a, h, w)

    rbp_o, rbm_o, pmwt = buf(), buf(), buf()
    _k8b(cp, delp, delm, dsqp, dsqm, rbp_o, rbm_o, pmwt, clip_pt, h, w)
    _band_reflect(pmwt, h, w)

    pmwt3, rbint = buf(), buf()
    _k8c(cp, pmwt, rbp_o, rbm_o, pmwt3, rbint, h, w)
    _band_reflect(rbint, h, w)

    dgrb_r, dgrb_b = buf(), buf()
    _k9(cp, d0, d1, rbint, hvwt3, pmwt3, dgrb0, green, dgrb_r, dgrb_b,
        clip_pt, h, w)
    _band_reflect(dgrb_r, h, w)
    _band_reflect(dgrb_b, h, w)

    # G−R interpolated at B sites (odd rows); G−B at R sites (even rows).
    _k10(dgrb_r, dgrb_r, 1, h, w)
    _k10(dgrb_b, dgrb_b, 0, h, w)
    _band_reflect(dgrb_r, h, w)
    _band_reflect(dgrb_b, h, w)

    rgb = np.empty((h, w, 3), dtype=np.float32)
    _k11(cp, green, hvwt3, dgrb_r, dgrb_b, rgb, h, w)
    used.extend(pool)            # return unused pooled buffers too
    _POOL.give(h, w, used)
    return rgb
