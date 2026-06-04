"""Clean-room directional color-difference demosaic (RCD family).

WHAT THIS IS
------------
A vectorized, pure-NumPy Bayer demosaicker in the *Ratio Corrected Demosaicing*
(RCD) family: (1) a per-pixel **horizontal-vs-vertical direction discrimination**
from local green first-derivatives and red/blue second-derivatives; (2) a
**green-first** directional interpolation that corrects a directional average by
the high-frequency (Laplacian) term of the center channel; (3) **red and blue**
reconstructed from the now-complete green in the **color-difference** domain
(R-G, B-G) by bilinear interpolation. This is the standard "luminance-first,
chrominance-smooth" structure that RCD shares with the Hamilton-Adams /
directional-decision lineage.

CLEAN-ROOM PROVENANCE (license-sensitive — read before editing)
---------------------------------------------------------------
RCD's *reference* implementation (`LuisSR/RCD-Demosaicing`) is **GPL-3.0**, as is
its incarnation inside RawTherapee and darktable. None of that source was opened,
read, or copied. The RawPedia documentation page (CC-BY-SA) carries no algorithmic
detail. RCD's *exact* internals (its specific "ratio-corrected low-pass" estimator
and its H/V/P/Q discrimination statistic) are reliably described only in that GPL
source, so they are deliberately **not** reproduced here. Instead this module
implements the RCD-*family* directional color-difference method from primary,
permissively-/openly-licensed academic sources:

  [Malvar2004]  H. S. Malvar, L.-w. He, R. Cutler, "High-Quality Linear
                Interpolation for Demosaicing of Bayer-Patterned Color Images,"
                Microsoft Research, Proc. IEEE ICASSP 2004. — the
                bilinear-interpolated **color-difference** (R-G, B-G) framework
                (after Cok's constant-hue), and the gradient-correction view of
                green interpolation.
  [Buades2011]  A. Buades, B. Coll, J.-M. Morel, C. Sbert, "Self-Similarity
                Driven Color Demosaicking," Image Processing On Line (IPOL),
                2011-06-01. — §1.1 gives the **Hamilton-Adams** directional green
                equations VERBATIM (the H/V gradient classifier and the
                Laplacian-corrected directional green), which this module
                implements directly. IPOL articles are open / non-GPL.
  [Lukin2004]   A. Lukin, D. Kubasov, "An Improved Demosaicing Algorithm,"
                GraphiCon 2004. — edge-directional green + color-difference R/B,
                and the rationale for color *differences* over color *ratios*
                (ratios blow up where green is small / noisy).
  [Menon2007]   D. Menon, S. Andriani, G. Calvagno, "Demosaicing With Directional
                Filtering and a posteriori Decision," IEEE Trans. Image Process.
                16(1), 2007. — the directional-candidates + decision structure
                that the RCD family follows.
  [Hamilton1997] J. F. Hamilton Jr., J. E. Adams Jr., U.S. Patent 5,629,734
                (1997) — the original adaptive H/V green disclosure (public
                patent), as re-described mathematically by [Buades2011].

Because the discriminator is a **comparison** (∆H vs ∆V) and the chrominance step
uses **subtraction** (R-G, B-G), there is NO division on pixel data anywhere — so
the result is structurally finite (no NaN/Inf), unlike a ratio formulation.

ALGORITHM (RGGB phase; other phases handled by flips — see `rcd_demosaic`)
--------------------------------------------------------------------------
Per [Buades2011] §1.1, at a non-green (R or B) site with center channel value C,
horizontal green neighbors (G_l, G_r), vertical green neighbors (G_u, G_d), and
same-channel neighbors two pixels away (C_l2, C_r2 horizontally; C_u2, C_d2
vertically):

  1. Horizontal gradient   ∆H = |G_l - G_r| + |2C - C_l2 - C_r2|
  2. Vertical   gradient   ∆V = |G_u - G_d| + |2C - C_u2 - C_d2|
  3. Directional green:
        ∆H > ∆V  ->  G = (G_u + G_d)/2 + (2C - C_u2 - C_d2)/4     # interp vertical
        ∆H < ∆V  ->  G = (G_l + G_r)/2 + (2C - C_l2 - C_r2)/4     # interp horizontal
        else     ->  G = (G_u + G_d + G_l + G_r)/4
                       + (4C - C_u2 - C_d2 - C_l2 - C_r2)/8

(A larger gradient along one axis means an edge running *across* that axis, so the
interpolation is steered along the *lower*-gradient axis — see [Buades2011] step 3.
The Laplacian `2C - C±2` is the second-derivative correction that injects the
center channel's high frequency into green, the [Hamilton1997] insight.)

  4. Red & blue (after green is complete), in the color-difference domain
     [Malvar2004]/[Lukin2004]: form K_R = R - G at red sites and K_B = B - G at
     blue sites, then bilinearly interpolate each color-difference plane to a full
     resolution and add green back:  R = G + K_R,  B = G + K_B. The bilinear fill
     is a two-pass stencil per [Malvar2004] eq. (1):
        - diagonal pass: fill the color difference at the *opposite*-color site
          (e.g. K_R at a blue site) from its 4 diagonal same-difference neighbors;
        - cardinal pass: fill the color difference at green sites from the 4
          cardinal neighbors (all known after the diagonal pass).

Known CFA samples are never overwritten by interpolated values (constant-color and
PSNR fidelity both depend on this). The final RGB is clamped to be **non-negative**
only (``[0, +inf)``) — color-difference reconstruction can ring slightly negative
at edges, which is non-physical, but the **upper** end is deliberately *not* capped
at 1.0: this project is highlight-sensitive (see `highlight_recovery.py`, whose
whole purpose is to avoid clipping highlights to white), so a demosaic primitive
must pass through-range highlight values to the downstream pipeline rather than
crush them. The non-negative clamp is a no-op on flat patches.
"""

from __future__ import annotations

import numpy as np

# Reflect-pad width. The Hamilton-Adams green stencil reaches +/-2 along each
# axis, and the color-difference diagonal fill reaches +/-1 diagonally; pad 2 to
# cover the widest stencil. EVEN width is load-bearing: ``np.pad(mode="reflect")``
# with an even margin preserves the 2x2 Bayer phase (an odd margin would flip the
# parity of every row/column and silently corrupt the mosaic interpretation).
_PAD = 2

_VALID_PATTERNS = ("RGGB", "BGGR", "GRBG", "GBRG")


def _bilinear_fill_diff(diff: np.ndarray, known: np.ndarray) -> np.ndarray:
    """Bilinearly interpolate a color-difference plane over its unknown sites.

    ``diff`` holds a color difference (e.g. R-G or B-G) defined only at ``known``
    (boolean, True where the difference is real); unknown entries are arbitrary.
    Returns a fully-populated plane. Two-pass per [Malvar2004] eq. (1): first the
    opposite-color sites from their 4 *diagonal* same-difference neighbors, then
    the green sites from their 4 *cardinal* neighbors (known after pass 1). Known
    entries are preserved exactly.

    Operates on a reflect-padded interior so borders need no special-casing; the
    caller slices the valid region back out.
    """
    d = diff.astype(np.float64, copy=True)
    k = known
    # Pass 1 — diagonal neighbors (opposite-color sites, e.g. K_R at blue sites).
    # Shift the *masked* plane so absent neighbors contribute 0 and are not
    # counted, giving a true average over however many diagonal neighbors are
    # known (interior: always 4).
    vals = np.where(k, d, 0.0)
    cnt = k.astype(np.float64)
    diag_sum = (
        _shift(vals, 1, 1) + _shift(vals, 1, -1)
        + _shift(vals, -1, 1) + _shift(vals, -1, -1)
    )
    diag_cnt = (
        _shift(cnt, 1, 1) + _shift(cnt, 1, -1)
        + _shift(cnt, -1, 1) + _shift(cnt, -1, -1)
    )
    diag_known = (diag_cnt > 0) & (~k)
    d = np.where(diag_known, diag_sum / np.maximum(diag_cnt, 1.0), d)

    # Pass 2 — cardinal neighbors (green sites). After pass 1 every R/B and
    # opposite-R/B site carries a difference, so the 4 cardinal neighbors of a
    # green site are all populated.
    filled = k | diag_known
    vals = np.where(filled, d, 0.0)
    cnt = filled.astype(np.float64)
    card_sum = (
        _shift(vals, 0, 1) + _shift(vals, 0, -1)
        + _shift(vals, 1, 0) + _shift(vals, -1, 0)
    )
    card_cnt = (
        _shift(cnt, 0, 1) + _shift(cnt, 0, -1)
        + _shift(cnt, 1, 0) + _shift(cnt, -1, 0)
    )
    card_known = (card_cnt > 0) & (~filled)
    d = np.where(card_known, card_sum / np.maximum(card_cnt, 1.0), d)
    return d


def _shift(a: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """Shift a 2-D array by (dy, dx) with zero fill at the exposed edge.

    Vacated cells are 0.0 so the masked-average pattern in `_bilinear_fill_diff`
    treats them as "no neighbor" (paired with a parallel count shift). Positive
    ``dy`` pulls in the neighbor from *below* (row + dy), positive ``dx`` from the
    *right* (col + dx).
    """
    out = np.zeros_like(a)
    ys_src = slice(max(dy, 0), a.shape[0] + min(dy, 0))
    ys_dst = slice(max(-dy, 0), a.shape[0] + min(-dy, 0))
    xs_src = slice(max(dx, 0), a.shape[1] + min(dx, 0))
    xs_dst = slice(max(-dx, 0), a.shape[1] + min(-dx, 0))
    out[ys_dst, xs_dst] = a[ys_src, xs_src]
    return out


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
    # Hamilton-Adams directional green at every R/B site [Buades2011 §1.1].
    # C is the center (known R or B), neighbors are read by shifting the full
    # mosaic (green neighbors are cardinal +/-1; same-channel neighbors +/-2).
    c = f  # center channel value at each pixel (its own CFA sample)
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

    # ∆H > ∆V -> edge is vertical -> interpolate along the column (vertical).
    green_est = np.where(
        grad_h > grad_v, green_vert,
        np.where(grad_h < grad_v, green_horiz, green_avg),
    )

    green = np.where(g_site, f, green_est)  # keep known greens exactly

    # ------------------------------------------------------------- red & blue
    # Color-difference planes, known only at their own sites [Malvar2004].
    kr = np.where(r_site, f - green, 0.0)
    kb = np.where(b_site, f - green, 0.0)
    kr_full = _bilinear_fill_diff(kr, r_site)
    kb_full = _bilinear_fill_diff(kb, b_site)

    red = green + kr_full
    blue = green + kb_full
    # Restore exact known samples (defensive; the fills preserve them already).
    red = np.where(r_site, f, red)
    blue = np.where(b_site, f, blue)

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
    """Demosaic a single-channel Bayer mosaic to full RGB (RCD family).

    Parameters
    ----------
    cfa
        2-D array ``(H, W)`` of a Bayer mosaic, values nominally in ``[0, 1]``.
        ``H`` and ``W`` must be even (a Bayer mosaic always is); the phase-mapping
        flips only preserve the intended pattern on even dimensions.
    pattern
        The 2x2 CFA phase of ``cfa[0:2, 0:2]``: one of ``"RGGB"``, ``"BGGR"``,
        ``"GRBG"``, ``"GBRG"`` (row-major, so e.g. ``"GRBG"`` means
        ``cfa[0,0]`` is green, ``cfa[0,1]`` red, ``cfa[1,0]`` blue, ``cfa[1,1]``
        green).

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
    single source of truth for the directional green + color-difference steps. See
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
    np.clip(rgb, 0.0, None, out=rgb)
    return rgb.astype(np.float32) if out_float32 else rgb
