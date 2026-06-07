"""Clean-room directional color-difference demosaic (RCD-family green) + Menon
directional R/B + chroma-gated a-posteriori refining.

WHAT THIS IS
------------
A vectorized Bayer demosaicker composing three permissively-sourced stages:
(1) the RCD-family **Hamilton-Adams directional green** — a per-pixel
horizontal-vs-vertical decision from local green first-derivatives + red/blue
second-derivatives, then a Laplacian-corrected directional average (this stage,
unchanged from the original RCD-family green here, ties the strongest open anchor,
Menon 2007, on resolution / MTF50P and carries that 0.3116 cyc/px);
(2) **Menon directional red/blue** — the color difference (R-G, B-G) reconstructed
along the per-pixel a-posteriori direction, so R/B follow edges instead of smearing
across them; (3) an iterated **a-posteriori refining** pass (Menon 2007) over the
color differences, whose green-update sub-step is **chroma-gated** (this module's
own addition) so it suppresses false-colour / zipper in coloured & aliasing regions
without softening the luminance edges the green resolves.

**Method, plainly:** RCD green + Menon directional R/B + chroma-gated Menon
refining. The green is the RCD-distinctive part (and the resolution carrier); the
R/B reconstruction and the refining are Menon-family; the chroma-gate is novel. The
refining *does* rewrite the green inside saturated / aliasing neighbourhoods (the
chroma gate's whole purpose); it is left untouched on near-neutral luminance edges,
so the green's resolution is preserved there.

CLEAN-ROOM PROVENANCE (license-sensitive — read before editing)
---------------------------------------------------------------
RCD's *reference* implementation (`LuisSR/RCD-Demosaicing`) is **GPL-3.0**, as is
its incarnation inside RawTherapee and darktable. None of that source was opened,
read, or copied. RCD's *exact* internals (its specific "ratio-corrected low-pass"
estimator and its H/V/P/Q discrimination statistic) are deliberately **not**
reproduced here. Instead this module composes the RCD-*family* directional green,
the Menon a-posteriori R/B reconstruction + decision, and the Menon refining step
from primary, permissively-/openly-licensed sources:

  [Malvar2004]  H. S. Malvar, L.-w. He, R. Cutler, "High-Quality Linear
                Interpolation for Demosaicing of Bayer-Patterned Color Images,"
                Microsoft Research, Proc. IEEE ICASSP 2004. — the color-difference
                (R-G, B-G) reconstruction framework (after Cok's constant-hue),
                and the gradient-correction view of green interpolation.
  [Buades2011]  A. Buades, B. Coll, J.-M. Morel, C. Sbert, "Self-Similarity
                Driven Color Demosaicking," Image Processing On Line (IPOL),
                2011-06-01. — §1.1 gives the **Hamilton-Adams** directional green
                equations VERBATIM (the H/V gradient classifier and the
                Laplacian-corrected directional green), which the green stage
                implements directly. IPOL articles are open / non-GPL.
  [Lukin2004]   A. Lukin, D. Kubasov, "An Improved Demosaicing Algorithm,"
                GraphiCon 2004. — edge-directional green + color-difference R/B,
                and the rationale for color *differences* over color *ratios*.
  [Menon2007]   D. Menon, S. Andriani, G. Calvagno, "Demosaicing With Directional
                Filtering and a posteriori Decision," IEEE Trans. Image Process.
                16(1):132-141, 2007. doi:10.1109/TIP.2006.884928 — the
                **a-posteriori homogeneity decision** (the 5x5 `d_H`/`d_V`
                classifier giving the per-pixel best direction `M`), the
                **directional R/B reconstruction** (the color difference filtered
                along `M`), and the **iterated refining step** (color-difference
                FIR smoothing along `M`, with the green updated from the smoothed
                R-G / B-G). All three are transcribed clean-room from the
                BSD-3-Clause `colour_demosaicing` reference implementation
                (`colour_demosaicing.bayer.demosaicing.menon2007`, Copyright 2015
                Colour Developers, BSD-3-Clause), which is permissively licensed
                and explicitly readable. The refining smoother is Menon's **linear
                3-tap FIR** (`[1,1,1]/3`), NOT a median — it is the
                color-difference low-pass of the a-posteriori method, not Freeman's
                iterative median (a different, untried op).
  [Hamilton1997] J. F. Hamilton Jr., J. E. Adams Jr., U.S. Patent 5,629,734
                (1997) — the original adaptive H/V green disclosure (public
                patent), as re-described mathematically by [Buades2011].

The **chroma-gate on the refining green-update** is this module's own addition
(not from any of the sources above): Menon's refining unconditionally re-derives
green from the smoothed color difference at every R/B site, which slightly softens
the green on neutral luminance edges (measured ~0.005 cyc/px of MTF50P). Gating
that re-derivation by the local color-difference magnitude — full inside colored /
aliasing neighbourhoods (where the chroma cleanup is wanted), faded to none where
the neighbourhood is near-neutral (where the green carries the resolution) —
recovers the green's edge acuity while keeping the false-colour / zipper
suppression. The threshold is battery-tuned
(`docs/research/demosaic-test-fixtures.md`, `tools/demosaic_bench/`); it sits at
the MTF knee, so lowering it re-softens the green and raising it lets false-colour
back in.

Because the discriminators are **comparisons** and the chrominance steps use
**subtraction + linear FIR** (no division on pixel data anywhere), the result is
structurally finite (no NaN/Inf), unlike a ratio formulation.

ALGORITHM (RGGB phase; other phases handled by flips — see `rcd_demosaic`)
--------------------------------------------------------------------------
Per [Buades2011] §1.1, at a non-green (R or B) site with center channel value C,
horizontal green neighbors (G_l, G_r), vertical green neighbors (G_u, G_d), and
same-channel neighbors two pixels away (C_l2, C_r2 horizontally; C_u2, C_d2
vertically):

  1. Horizontal gradient   dH = |G_l - G_r| + |2C - C_l2 - C_r2|
  2. Vertical   gradient   dV = |G_u - G_d| + |2C - C_u2 - C_d2|
  3. Directional green:
        dH > dV  ->  G = (G_u + G_d)/2 + (2C - C_u2 - C_d2)/4     # interp vertical
        dH < dV  ->  G = (G_l + G_r)/2 + (2C - C_l2 - C_r2)/4     # interp horizontal
        else     ->  G = (G_u + G_d + G_l + G_r)/4
                       + (4C - C_u2 - C_d2 - C_l2 - C_r2)/8

(A larger gradient along one axis means an edge running *across* that axis, so the
interpolation is steered along the *lower*-gradient axis — see [Buades2011] step 3.
The Laplacian `2C - C+-2` is the second-derivative correction that injects the
center channel's high frequency into green, the [Hamilton1997] insight.)

  4. A-posteriori direction `M` [Menon2007]: under each green hypothesis (G_H, G_V)
     form the color difference, take its directional gradient, low-pass each with
     the 5x5 homogeneity kernel, and pick `M = (d_V >= d_H)` (True -> the row /
     horizontal reconstruction is more homogeneous).

  5. Red & blue initialization [Menon2007]: from the complete green, reconstruct
     R/B in the color-difference domain, interpolated **along `M`** (directional,
     so R/B follow edges) — at green sites by the forced row/column geometry, at
     the opposite-color sites along `M`.

  6. Refining [Menon2007], iterated `_REFINE_ITERS` times: smooth R-G / B-G with a
     3-tap FIR along `M`; re-derive green from the smoothed color difference
     (**chroma-gated**, see provenance); re-derive R/B at green sites and at the
     opposite-color sites from the smoothed differences. Known CFA samples are
     restored exactly after the loop.

Known CFA samples are never overwritten by interpolated values (constant-color and
PSNR fidelity both depend on this). The final RGB is clamped to be **non-negative**
only (``[0, +inf)``) — color-difference reconstruction can ring slightly negative
at edges, which is non-physical, but the **upper** end is deliberately *not* capped
at 1.0: this project is highlight-sensitive (see `highlight_recovery.py`), so a
demosaic primitive must pass through-range highlight values to the downstream
pipeline rather than crush them. Every step is a no-op on flat patches (zero
gradients, zero color difference -> the chroma gate is off -> green is the constant
-> R/B are exact), so a constant color reconstructs bit-exactly in the interior.

BACKEND NOTE
------------
This numpy module is THE reference and the demosaic-battery quality deliverable.
`accel._numba_kernels.rcd_rggb_refined` is a **bit-faithful float64 twin** of the
current reference (directional green + Menon directional R/B + the chroma-gated
refining loop): `accel.rcd_demosaic(backend="numba")` runs the kernel; numpy stays
the reference/fallback. End-to-end parity vs this module is ~1e-15 and the battery
via numba is bit-identical (39.03 CPSNR), so the two paths are interchangeable.

The a-posteriori direction `m_dir` is split (`_menon_direction = _menon_decide ∘
_menon_dplanes`): its CONTINUOUS front half — the colour-difference directional
gradient planes `d_h/d_v` (separable 1-D FIR + abs) — is ported **bit-for-bit** to
numba (`accel._numba_kernels.menon_dplanes`), while its ONE **discrete** branch
(`m_dir = dd_v >= dd_h`, the 5x5 homogeneity convolve + compare, `_menon_decide`)
stays in scipy on BOTH backends. scipy's n-D `convolve` is SIMD-FP-reduced, not a
scalar fold — verified empirically that NO per-pixel summation order reproduces it
(a clean-room scalar port flips ~3 `m_dir` bits / 8 Kodak frames), and a 1-ULP flip
selects the opposite H/V reconstruction. Because the numba d-planes are bit-identical
to numpy's, feeding them through the same scipy `_menon_decide` yields a bit-identical
`m_dir` by construction. Porting the d-planes lifts the kernel speedup past the old
~4x (the bulk — the 1-D folds, incl. the slow axis-0 conv — leaves the numpy
critical path); the residual scipy homogeneity convolve (~a quarter of the old
`m_dir` cost) is the bit-exactness floor. The default pipeline demosaic is `linear`
(libraw); `rcd` is opt-in.
"""

from __future__ import annotations

import os

import numpy as np
from scipy.ndimage import convolve, convolve1d, median_filter, uniform_filter

# Reflect-pad width. The Hamilton-Adams green stencil and the Menon green
# hypotheses reach +/-2 along each axis; the homogeneity kernel reaches +/-2; the
# 5x5 chroma box reaches +/-2. Pad 2 to cover the widest stencil. EVEN width is
# load-bearing: ``np.pad(mode="reflect")`` with an even margin preserves the 2x2
# Bayer phase (an odd margin would flip the parity of every row/column and silently
# corrupt the mosaic interpretation).
_PAD = 2

_VALID_PATTERNS = ("RGGB", "BGGR", "GRBG", "GBRG")

# --- Refining tunables (battery-tuned; docs/research/demosaic-test-fixtures.md) ---
# Number of Menon refining iterations. n=2 is the robust point on Kodak-24: it
# beats the Menon2007 anchor on zipper, ties it on CPSNR, holds MTF50P at 0.3116,
# and keeps a comfortable margin on the synthetic zone-plate gate (more iterations
# over-smooth the chirp and tighten that margin for a sliver of false-colour gain).
_REFINE_ITERS = 2
# Chroma-gate on the green-update: the green is re-derived from the smoothed colour
# difference with weight `alpha = clip((local_chroma - _CHROMA_THR) / _CHROMA_SOFT,
# 0, 1)`. `local_chroma` is a 5x5 box average of |R-G|+|B-G|. Below the threshold
# the green is left untouched (luminance edges keep their acuity / MTF50P); above it
# the full Menon green-update runs (colour / aliasing cleanup). The threshold sits
# at the MTF knee — lowering it re-softens the green, raising it lets false-colour
# back in.
_CHROMA_THR = 0.01
_CHROMA_SOFT = 0.02

# --- TERMINAL Freeman-style chroma-difference median (EXPERIMENTAL, default OFF) ---
# A separate, optional false-colour remedy distinct from the linear chroma-gate
# above: after the directional reconstruction + refining, median-filter the colour
# DIFFERENCES (R-G, B-G) with a small SQUARE window, then re-derive R/B = G +
# median(C-G). This is the classical Freeman [Freeman1988] iterative-median chroma
# cleanup — a *median* (rank, edge-preserving) on the colour difference, NOT the
# Menon linear 3-tap FIR the refining uses. Rationale: demosaic false colour shows
# as a sign-alternating spike in the colour difference riding on a locally-flat true
# chroma; a median rejects that impulse while a linear FIR smears it (and smears
# true chroma). It is part of *demosaicing* (operates only on the demosaic's own
# colour-difference planes), NOT a global chroma denoiser.
#
# Gated by env so the battery / adversarial harness and the CLI render exercise the
# SAME operator without cli.py plumbing (PROPOSE-not-ship; the CLI default leaves it
# OFF -> byte-exact identity preserved):
#   LRT_RCD_CHROMA_MEDIAN      window size (odd int, 0/unset = OFF). 3 or 5.
#   LRT_RCD_CHROMA_MEDIAN_ITERS  iterations (default 1).
# A SQUARE window (size x size) is mandatory: a horizontal-only median would crush
# the *horizontal* chroma-HF metric without fixing the artifact (and is the gaming
# trap the metric's vertical companion exists to catch). Default OFF: the green is
# untouched and the colour difference equals its input -> no-op, so the zero-slider
# identity / ship gate is unchanged.


def _chroma_median_config() -> tuple[int, int]:
    """Read the terminal-chroma-median config from the environment.

    Returns ``(size, iters)``; ``size <= 1`` (the default / unset / ``0``) means the
    pass is OFF. ``size`` is forced odd (a median needs an odd window for a defined
    centre). Read per-call so a test/harness can toggle it without re-import."""
    try:
        size = int(os.environ.get("LRT_RCD_CHROMA_MEDIAN", "0"))
    except ValueError:
        size = 0
    if size <= 1:
        return 0, 0
    if size % 2 == 0:  # medians need an odd window; round the even value down
        size -= 1
    try:
        iters = max(1, int(os.environ.get("LRT_RCD_CHROMA_MEDIAN_ITERS", "1")))
    except ValueError:
        iters = 1
    return size, iters


def _chroma_median(red, green, blue, r_site, b_site, g_site, size: int, iters: int) -> tuple:
    """Freeman-style median on the colour differences (R-G, B-G); re-derive R/B.

    Green is held FIXED (the median touches chroma only, so no luma/MTF50P metric is
    affected — and none can guard it; the guard is the neutral zone-plate falseClr +
    the adversarial real-colour grating, see module note). The known CFA samples are
    re-pinned by the caller (`_rcd_rggb`) after this returns. SQUARE window only."""
    g = green
    r, b = red, blue
    footprint = (size, size)
    for _ in range(iters):
        r = g + median_filter(r - g, size=footprint, mode="reflect")
        b = g + median_filter(b - g, size=footprint, mode="reflect")
    return r, g, b

# Menon green-hypothesis FIR taps (the BSD-3 reference's h_0 + h_1): the directional
# green estimate at a non-green site is 0.5*(near green neighbours)
# - 0.25*(far same-channel neighbours) + 0.5*center.
_H0 = np.array([0.0, 0.5, 0.0, 0.5, 0.0])
_H1 = np.array([-0.25, 0.0, 0.5, 0.0, -0.25])

# Reconstruction / refining FIRs (Menon 2007, BSD-3 reference).
_FIR3 = np.array([1.0, 1.0, 1.0]) / 3.0          # color-difference low-pass (refining)
_KB3 = np.array([0.5, 0.0, 0.5])                 # directional color-difference fill

# 5x5 a-posteriori homogeneity kernel (Menon 2007, BSD-3 reference). `d_H` uses `k`,
# `d_V` uses its transpose; `M = d_V >= d_H`.
_K_HOMOGENEITY = np.array(
    [
        [0.0, 0.0, 1.0, 0.0, 1.0],
        [0.0, 0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 3.0, 0.0, 3.0],
        [0.0, 0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0, 0.0, 1.0],
    ],
    dtype=np.float64,
)


def _shift(a: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """Shift a 2-D array by (dy, dx) with zero fill at the exposed edge.

    Positive ``dy`` pulls in the neighbor from *below* (row + dy), positive ``dx``
    from the *right* (col + dx).
    """
    out = np.zeros_like(a)
    ys_src = slice(max(dy, 0), a.shape[0] + min(dy, 0))
    ys_dst = slice(max(-dy, 0), a.shape[0] + min(-dy, 0))
    xs_src = slice(max(dx, 0), a.shape[1] + min(dx, 0))
    xs_dst = slice(max(-dx, 0), a.shape[1] + min(-dx, 0))
    out[ys_dst, xs_dst] = a[ys_src, xs_src]
    return out


def _green_directional(f: np.ndarray, g_site: np.ndarray) -> np.ndarray:
    """Hamilton-Adams directional green at every R/B site [Buades2011 1.1].

    Unchanged from the original RCD-family green (the resolution-critical stage):
    C is the center (known R or B), green neighbors are cardinal +/-1, same-channel
    neighbors +/-2. Returns the full green plane with known greens kept exactly.
    """
    c = f
    g_l, g_r = _shift(f, 0, 1), _shift(f, 0, -1)   # green left / right (col -/+ 1)
    g_u, g_d = _shift(f, 1, 0), _shift(f, -1, 0)   # green up / down   (row -/+ 1)
    c_l2, c_r2 = _shift(f, 0, 2), _shift(f, 0, -2)  # same-channel +/-2 horizontal
    c_u2, c_d2 = _shift(f, 2, 0), _shift(f, -2, 0)  # same-channel +/-2 vertical

    lap_h = 2.0 * c - c_l2 - c_r2  # horizontal Laplacian of the center channel
    lap_v = 2.0 * c - c_u2 - c_d2  # vertical   Laplacian

    grad_h = np.abs(g_l - g_r) + np.abs(lap_h)
    grad_v = np.abs(g_u - g_d) + np.abs(lap_v)

    green_horiz = 0.5 * (g_l + g_r) + 0.25 * lap_h
    green_vert = 0.5 * (g_u + g_d) + 0.25 * lap_v
    green_avg = 0.25 * (g_u + g_d + g_l + g_r) + 0.125 * (lap_h + lap_v)

    # dH > dV -> edge is vertical -> interpolate along the column (vertical).
    green_est = np.where(
        grad_h > grad_v, green_vert,
        np.where(grad_h < grad_v, green_horiz, green_avg),
    )
    return np.where(g_site, f, green_est)  # keep known greens exactly


def _menon_dplanes(f: np.ndarray, r_site, b_site, g_site) -> tuple:
    """Colour-difference directional-gradient planes (d_h, d_v) — the CONTINUOUS
    front half of the a-posteriori decision [Menon2007].

    Build the colour difference under each green hypothesis (G_H, G_V), then take its
    +2 directional gradient along each axis. Every op here is a separable 1-D FIR
    (`_H0`+`_H1` symmetric fold) + subtraction + abs, so it is reproducible
    **bit-for-bit** in a scalar kernel — which is why the numba accel path
    (`accel._numba_kernels.menon_dplanes`) ports exactly THIS half and shares the
    discrete tail (`_menon_decide`). Operates on the reflect-padded mosaic.
    """
    g_known = g_site
    g_h = np.where(
        ~g_known, convolve1d(f, _H0, mode="mirror") + convolve1d(f, _H1, mode="mirror"), f
    )
    g_v = np.where(
        ~g_known,
        convolve1d(f, _H0, mode="mirror", axis=0) + convolve1d(f, _H1, mode="mirror", axis=0),
        f,
    )
    rb = r_site | b_site
    c_h = np.where(rb, f - g_h, 0.0)
    c_v = np.where(rb, f - g_v, 0.0)
    d_h = np.abs(c_h - np.pad(c_h, ((0, 0), (0, 2)), mode="reflect")[:, 2:])
    d_v = np.abs(c_v - np.pad(c_v, ((0, 2), (0, 0)), mode="reflect")[2:, :])
    return d_h, d_v


def _menon_decide(d_h: np.ndarray, d_v: np.ndarray) -> np.ndarray:
    """The ONE discrete branch of the a-posteriori decision [Menon2007]: low-pass each
    colour-difference gradient with the 5x5 homogeneity kernel and pick the
    more-homogeneous axis (`M = dd_v >= dd_h`, True = horizontal/row reconstruction).

    Stays in scipy on BOTH backends **by design**. scipy's n-D ``convolve`` uses a
    SIMD-vectorised (lane-partitioned) FP reduction, not a scalar left-fold — verified
    empirically: NO per-pixel summation order reproduces it bit-for-bit (a clean-room
    scalar port flips ~3 `M` bits / 8 Kodak frames). Since `M` is a `>=` comparison, a
    1-ULP difference flips the H vs V reconstruction at ties — a large, battery-moving
    divergence. The numba accel path computes its d-planes bit-identically to
    `_menon_dplanes`, then routes them through THIS shared function, so `m_dir` is
    bit-identical on both backends by construction.
    """
    dd_h = convolve(d_h, _K_HOMOGENEITY, mode="constant")
    dd_v = convolve(d_v, _K_HOMOGENEITY.T, mode="constant")
    return dd_v >= dd_h


def _menon_direction(f: np.ndarray, r_site, b_site, g_site) -> np.ndarray:
    """A-posteriori per-pixel best direction `M` (True = horizontal) [Menon2007].

    Clean-room transcription of the directional-decision math from the BSD-3
    `colour_demosaicing` Menon2007 reference: the continuous colour-difference
    gradient planes (`_menon_dplanes`) feed the discrete homogeneity decision
    (`_menon_decide`). Split into those two halves so the numba accel path can port
    the (bit-exact) front half and share the (scipy-only) decision; this wrapper keeps
    the original single-call contract. Operates on the reflect-padded mosaic.
    """
    d_h, d_v = _menon_dplanes(f, r_site, b_site, g_site)
    return _menon_decide(d_h, d_v)


def _reconstruct_rb(green, f, r_site, b_site, g_site, m_dir) -> tuple:
    """Directional R/B reconstruction from a complete green [Menon2007].

    Clean-room transcription of the R/B step of the BSD-3 `colour_demosaicing`
    Menon2007 reference, with the project's own RCD green substituted for Menon's.
    The color difference is interpolated **along the a-posteriori direction `M`**
    (horizontal where True, else vertical) so R/B follow edges. At green sites the
    direction is forced by the row/column geometry (a red row -> horizontal R
    neighbours); at the opposite-color sites it follows `M`. Returns (R, B) full
    planes with the known CFA samples kept exactly.
    """
    r = np.where(r_site, f, 0.0)
    b = np.where(b_site, f, 0.0)
    g = green
    r_m, g_m, b_m = r_site, g_site, b_site

    def ch(x):
        return convolve1d(x, _KB3, mode="mirror")

    def cv(x):
        return convolve1d(x, _KB3, mode="mirror", axis=0)

    r_row = np.any(r_m, axis=1)[:, None] & np.ones_like(r_m)
    b_row = np.any(b_m, axis=1)[:, None] & np.ones_like(b_m)

    # R/B at the green sites: in a red row R neighbours are horizontal; in a blue
    # row they are vertical (and symmetrically for B).
    r = np.where(g_m & r_row, g + ch(r) - ch(g), r)
    r = np.where(g_m & b_row, g + cv(r) - cv(g), r)
    b = np.where(g_m & b_row, g + ch(b) - ch(g), b)
    b = np.where(g_m & r_row, g + cv(b) - cv(g), b)

    # R at blue sites / B at red sites: directional by M (interpolate the R-B
    # difference along the more-homogeneous axis).
    r = np.where(b_row & b_m, np.where(m_dir, b + ch(r) - ch(b), b + cv(r) - cv(b)), r)
    b = np.where(r_row & r_m, np.where(m_dir, r + ch(b) - ch(r), r + cv(b) - cv(r)), b)
    return r, b


def _refine_once(red, green, blue, r_site, b_site, g_site, m_dir) -> tuple:
    """One Menon refining iteration with a chroma-gated green-update [Menon2007].

    Clean-room transcription of `refining_step_Menon2007` (BSD-3), with ONE local
    addition: the green re-derivation is weighted by `alpha`, a soft gate on the
    local color-difference magnitude (full in colored / aliasing regions, zero on
    near-neutral luminance edges) — see the module provenance note. All smoothers
    are the reference's linear 3-tap FIRs; nothing is a median.
    """
    r, g, b = red.copy(), green.copy(), blue.copy()
    r_m, g_m, b_m = r_site, g_site, b_site

    def ch(x):
        return convolve1d(x, _FIR3, mode="mirror")

    def cv(x):
        return convolve1d(x, _FIR3, mode="mirror", axis=0)

    def chk(x):
        return convolve1d(x, _KB3, mode="mirror")

    def cvk(x):
        return convolve1d(x, _KB3, mode="mirror", axis=0)

    r_row = np.any(r_m, axis=1)[:, None] & np.ones_like(r_m)
    r_col = np.any(r_m, axis=0)[None, :] & np.ones_like(r_m)
    b_row = np.any(b_m, axis=1)[:, None] & np.ones_like(b_m)
    b_col = np.any(b_m, axis=0)[None, :] & np.ones_like(b_m)

    # --- chroma-gated green update (the local addition) ---
    # Local color-difference magnitude -> soft alpha in [0, 1]; 0 on neutral edges,
    # so the green re-derivation only fires where there is real colour to clean.
    chroma = uniform_filter(np.abs(r - g) + np.abs(b - g), size=5)
    alpha = np.clip((chroma - _CHROMA_THR) / _CHROMA_SOFT, 0.0, 1.0)
    r_g = r - g
    b_g = b - g
    b_g_m = np.where(b_m, np.where(m_dir, ch(b_g), cv(b_g)), 0.0)
    r_g_m = np.where(r_m, np.where(m_dir, ch(r_g), cv(r_g)), 0.0)
    g = np.where(r_m, (1.0 - alpha) * g + alpha * (r - r_g_m), g)
    g = np.where(b_m, (1.0 - alpha) * g + alpha * (b - b_g_m), g)

    # --- red & blue at the green sites (directional by row/column geometry) ---
    r_g = r - g
    b_g = b - g
    r_g_m = np.where(g_m & b_row, cvk(r_g), 0.0)
    r = np.where(g_m & b_row, g + r_g_m, r)
    r_g_m = np.where(g_m & b_col, chk(r_g), r_g_m)
    r = np.where(g_m & b_col, g + r_g_m, r)
    b_g_m = np.where(g_m & r_row, cvk(b_g), 0.0)
    b = np.where(g_m & r_row, g + b_g_m, b)
    b_g_m = np.where(g_m & r_col, chk(b_g), b_g_m)
    b = np.where(g_m & r_col, g + b_g_m, b)

    # --- red at blue sites / blue at red sites (directional by M) ---
    r_b = r - b
    r_b_m = np.where(b_m, np.where(m_dir, ch(r_b), cv(r_b)), 0.0)
    r = np.where(b_m, b + r_b_m, r)
    r_b_m = np.where(r_m, np.where(m_dir, ch(r_b), cv(r_b)), 0.0)
    b = np.where(r_m, r - r_b_m, b)
    return r, g, b


def _rcd_rggb(cfa: np.ndarray) -> np.ndarray:
    """Core demosaic for the **RGGB** phase. Input/output already padded out.

    ``cfa`` is a reflect-padded 2-D mosaic whose top-left interior pixel (the one
    at ``[_PAD, _PAD]``) is RED, i.e. the phase is RGGB. Returns padded (H, W, 3).
    """
    f = cfa.astype(np.float64, copy=False)
    h, w = f.shape

    # 2x2 RGGB site masks (parity on the *padded* grid; _PAD is even so padded
    # parity == original parity). R at (even, even); B at (odd, odd);
    # G at (even, odd) and (odd, even).
    yy, xx = np.indices((h, w))
    r_site = (yy % 2 == 0) & (xx % 2 == 0)
    b_site = (yy % 2 == 1) & (xx % 2 == 1)
    g_site = ~(r_site | b_site)

    # ------------------------------------------------------------------ green
    green = _green_directional(f, g_site)

    # ------------------------------------------------- a-posteriori direction
    m_dir = _menon_direction(f, r_site, b_site, g_site)

    # ----------------------------------------------- red & blue (directional)
    red, blue = _reconstruct_rb(green, f, r_site, b_site, g_site, m_dir)

    # ------------------------------------------------- chroma-gated refining
    for _ in range(_REFINE_ITERS):
        red, green, blue = _refine_once(red, green, blue, r_site, b_site, g_site, m_dir)

    # ------------------------- TERMINAL Freeman-style chroma-difference median
    # Optional false-colour remedy (default OFF -> no-op). Square median on (R-G,
    # B-G); green fixed. See `_chroma_median` / the module config note.
    _cm_size, _cm_iters = _chroma_median_config()
    if _cm_size:
        red, green, blue = _chroma_median(
            red, green, blue, r_site, b_site, g_site, _cm_size, _cm_iters,
        )

    # Restore exact known CFA samples (flat-exact + PSNR fidelity depend on this;
    # the refining FIRs touch every site, so this re-pins the originals).
    red = np.where(r_site, f, red)
    blue = np.where(b_site, f, blue)
    green = np.where(g_site, f, green)

    out = np.empty((h, w, 3), dtype=np.float64)
    out[..., 0] = red
    out[..., 1] = green
    out[..., 2] = blue
    return out


# Phase -> (flip_rows, flip_cols) that maps the given pattern's top-left 2x2 onto
# RGGB. Flips relocate the real red photosite to (0, 0); crucially they preserve
# the H/V axes (so the directional classifier stays valid) and need NO R<->B
# channel swap afterwards — the flipped-back output channels are already correct.
# (Transpose is intentionally NOT used: it swaps H<->V and would corrupt the
# direction discrimination.)
_PHASE_FLIP: dict[str, tuple[bool, bool]] = {
    "RGGB": (False, False),
    "GRBG": (False, True),   # horizontal flip brings col-1 red to col-0
    "GBRG": (True, False),   # vertical flip brings row-1 red to row-0
    "BGGR": (True, True),    # rot180: red at (1,1) -> (0,0)
}


def rcd_demosaic(cfa: np.ndarray, pattern: str) -> np.ndarray:
    """Demosaic a single-channel Bayer mosaic to full RGB (RCD green + Menon
    directional R/B + chroma-gated refining).

    Parameters
    ----------
    cfa
        2-D array ``(H, W)`` of a Bayer mosaic, values nominally in ``[0, 1]``.
        ``H`` and ``W`` must be even (a Bayer mosaic always is); the phase-mapping
        flips only preserve the intended pattern on even dimensions.
    pattern
        The 2x2 CFA phase of ``cfa[0:2, 0:2]``: one of ``"RGGB"``, ``"BGGR"``,
        ``"GRBG"``, ``"GBRG"`` (row-major).

    Returns
    -------
    np.ndarray
        Float ``(H, W, 3)`` RGB (R, G, B order), finite and **non-negative** (the
        upper range is NOT capped at 1.0 — highlight values pass through; see the
        module docstring). Dtype matches the input float dtype family (float32 in
        -> float32 out; otherwise float64).

    Notes
    -----
    The RGGB math is written once (`_rcd_rggb`); the other three phases are mapped
    onto it by row/column flips (`_PHASE_FLIP`) and flipped back, so there is a
    single source of truth for the directional green + R/B + refining steps. See
    the module docstring for the equations and their non-GPL sources.
    """
    if pattern not in _VALID_PATTERNS:
        raise ValueError(
            f"pattern must be one of {_VALID_PATTERNS}, got {pattern!r}"
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

    flip_rows, flip_cols = _PHASE_FLIP[pattern]
    if flip_rows:
        work = work[::-1, :]
    if flip_cols:
        work = work[:, ::-1]

    # Reflect-pad (even width -> phase-preserving), demosaic the RGGB core, crop.
    padded = np.pad(work, _PAD, mode="reflect")
    rgb_padded = _rcd_rggb(padded)
    rgb = rgb_padded[_PAD:-_PAD, _PAD:-_PAD, :]

    # Undo the flips on the spatial axes (channel order is already correct).
    if flip_cols:
        rgb = rgb[:, ::-1, :]
    if flip_rows:
        rgb = rgb[::-1, :, :]

    # Non-negative clamp only (no upper cap — preserve highlights for downstream).
    rgb = np.ascontiguousarray(rgb)
    np.clip(rgb, 0.0, None, out=rgb)
    return rgb.astype(np.float32) if out_float32 else rgb
