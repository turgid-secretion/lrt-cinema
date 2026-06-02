"""Enumerated, deterministic input lattice + emission helpers for the
invariant validation sweep (``test_validation_*.py``).

Why a hand-built lattice and **not** ``np.random``: the perceptual-near-black
bug class lives at scene luminance ``L < 0.01`` — a uniform ``rand`` (median
~0.5) never samples there, which is precisely why 358 random/identity tests
missed it. Every patch here is **addressable by name**, **deterministic** (no
RNG), and the luma axis is **log-spaced down to 1e-5** so the failure region is
densely covered.

Two products:
  * ``build_lattice()`` → a list of named :class:`Patch` (a log luma × hue ×
    saturation grid + named extremes), and ``pack(patches)`` → an ``(N, 1, 3)``
    linear-ProPhoto(D50) image for the **non-spatial** ops (HSL / ColorGrade /
    Saturation / Contrast …). NB the ``(N, 1, 3)`` packing is deliberately
    1-pixel-wide so the spatial guided-filter ops (DR-compression, Texture/
    Clarity) collapse to their global pointwise law — correct + deterministic
    for the *pointwise* invariants, but the genuinely *spatial* assertions
    (halo / ringing) belong in ``test_validation_spatial.py`` on real 2-D
    fields, never on this packed chart (inter-patch box-filter halos would
    contaminate it).
  * emission helpers that drive the **real** ``output.py`` colour path —
    ``emit_acescg`` (in-memory ProPhoto→AP1 Bradford + NaN-scrub + gated ACES
    RGC, the exact EXR maths minus the float→half write) and ``roundtrip_exr`` /
    ``roundtrip_tiff`` (write the real file LOSSLESS + read it back). Assertions
    are taken on the **decoded** emission, never on ``ProPhoto.min()`` — the bug
    is born in the ProPhoto→AP1 Bradford and only visible there.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

import lrt_cinema.develop_ops as _do
from lrt_cinema.develop_ops import _PROPHOTO_LUMINANCE
from lrt_cinema.lut3d_baker import _hsv_to_rgb_dcp

# --- the auto-flipping near-black bug sentinel (shared across the sweep) ----
#
# The perceptual near-black fix ships a shared `_nearblack_gate` (DECISIONS.md
# §7 near-black amendment, branch fix/perceptual-nearblack). On `main` BEFORE the
# fix it is absent → BUG_PRESENT is True → the catchers xfail(strict). When the
# fix merges the attribute appears → BUG_PRESENT flips False → the catchers run
# LIVE and must pass. Conditional-on-the-guard is deliberately STRONGER than a
# static xfail(strict): a static one reports a BROKEN fix (guard present but
# still casting) as green ("expected failure") — the exact "ship silently"
# failure this net exists to kill; conditional makes a broken fix go RED. The
# strict flag still fires if `main` is fixed without this sentinel (forcing the
# line updated). Two-sided-verified vs fix/perceptual-nearblack: red on main,
# green-live with the guard.
#
# FOLLOW-UP (do once fix/perceptual-nearblack is PERMANENTLY on main): convert
# these catchers to UNCONDITIONAL live tests (delete the conditional — keep the
# assertions). The known limit of the conditional form: it keys "is this a
# catcher" on the guard's EXISTENCE, so a future DELETION of `_nearblack_gate`
# would flip BUG_PRESENT back to True and report the reintroduced cast as XFAIL
# (green) — re-blinding the net to its own bug class. That regression IS still
# caught post-merge by the fix branch's own unconditional
# `test_develop_ops.py::test_perceptual_nearblack_*`, but these catchers should
# not lean on that indefinitely. (An always-live version cannot be added now —
# it would fail on this pre-fix branch.)
BUG_PRESENT = not hasattr(_do, "_nearblack_gate")

# Near-black chroma bound (absolute, AP1 scale). The bug blows a near-black
# neutral up to chroma O(0.1–18); a clean/guarded render sits ≤ ~2.4e-3. 5e-3 is
# the fix branch's own bound — ≥ 10× the worst guarded pixel, ≪ the smallest cast.
NB_CHROMA = 5e-3


def nearblack_xfail(reason: str = "perceptual near-black cast/negatives"):
    """The shared conditional xfail(strict) marker for a near-black bug catcher
    (flips to live when `_nearblack_gate` lands). See `BUG_PRESENT`."""
    return pytest.mark.xfail(
        BUG_PRESENT, strict=True,
        reason=f"{reason} — fixed in fix/perceptual-nearblack (_nearblack_gate); "
               f"this catcher flips to live+passing when that lands",
    )

# --- the enumerated lattice ------------------------------------------------

# Log-spaced luma, 1e-5 … 8.0 (≈ 19.6 stops): the near-black floor where the
# cast is born up through clipped/overrange specular. 12 levels.
LUMA_LEVELS: tuple[float, ...] = tuple(
    float(v) for v in np.geomspace(1e-5, 8.0, 12)
)
# 12 hues every 30° (every HSL band centre + the gaps between them).
HUE_DEGREES: tuple[float, ...] = tuple(float(h) for h in range(0, 360, 30))
# 5 saturations: 0.0 = a true neutral at that luma … 1.0 = the gamut edge.
SATURATIONS: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)


@dataclass(frozen=True)
class Patch:
    """One addressable flat patch and its linear-ProPhoto(D50) RGB."""

    name: str
    rgb: tuple[float, float, float]
    is_neutral: bool
    luma: float          # nominal scene luminance (HSV value for the grid)
    hue_deg: float
    sat: float
    group: str           # "grid" | "extreme"


def _pp_from_hsv(hue_deg: float, sat: float, val: float) -> tuple[float, float, float]:
    """Linear-ProPhoto RGB for an Adobe-hexcone (hue_deg, sat, val) — the same
    model the production HSV ops and the grading-sweep chart use, so a patch's
    hue/sat label is exact."""
    h = np.array([(hue_deg % 360.0) * (6.0 / 360.0)], dtype=np.float64)
    rgb = _hsv_to_rgb_dcp(
        h, np.array([sat], dtype=np.float64), np.array([val], dtype=np.float64),
    )[0]
    return (float(rgb[0]), float(rgb[1]), float(rgb[2]))


# Named extremes — the corners a grid misses, each individually addressable.
def _named_extremes() -> list[Patch]:
    def p(name: str, rgb: tuple[float, float, float], neutral: bool) -> Patch:
        luma = float(np.array(rgb, dtype=np.float64) @ _PROPHOTO_LUMINANCE)
        return Patch(name, rgb, neutral, luma, 0.0, 0.0, "extreme")

    return [
        p("pure_black", (0.0, 0.0, 0.0), True),
        p("neutral_1e-4", (1e-4, 1e-4, 1e-4), True),
        p("neutral_1e-3", (1e-3, 1e-3, 1e-3), True),
        # The task's named near-black saturated extreme (a degenerate, almost
        # single-channel dark pixel — the shape `apply_blacks_2012` can leave).
        p("nearblack_saturated", (1e-3, 1e-5, 1e-5), False),
        p("single_channel_r", (0.5, 0.0, 0.0), False),
        p("single_channel_g", (0.0, 0.5, 0.0), False),
        p("single_channel_b", (0.0, 0.0, 0.5), False),
        # A ProPhoto primary is well OUTSIDE AP1 → negative AP1 channels at emit
        # (legit out-of-gamut, the RGC pass's actual job — distinct from the
        # near-black cast which the upstream guard owns).
        p("prophoto_primary_r", (1.0, 0.0, 0.0), False),
        p("prophoto_primary_g", (0.0, 1.0, 0.0), False),
        p("clipped_white_4", (4.0, 4.0, 4.0), True),
        p("overrange_saturated", (8.0, 0.5, 0.1), False),
    ]


def build_lattice() -> list[Patch]:
    """The full deterministic lattice: log luma × 12 hue × 5 sat grid + the
    named extremes. ~720 grid patches + 11 extremes; every patch ``.name`` is
    unique and stable."""
    patches: list[Patch] = []
    for luma in LUMA_LEVELS:
        for hue in HUE_DEGREES:
            for sat in SATURATIONS:
                neutral = sat == 0.0
                rgb = (
                    (luma, luma, luma) if neutral
                    else _pp_from_hsv(hue, sat, luma)
                )
                # A neutral is hue-invariant — emit it once (under hue 0).
                if neutral and hue != HUE_DEGREES[0]:
                    continue
                patches.append(Patch(
                    name=f"L{luma:.2e}_h{int(hue):03d}_s{sat:.2f}",
                    rgb=rgb, is_neutral=neutral, luma=luma, hue_deg=hue, sat=sat,
                    group="grid",
                ))
    patches.extend(_named_extremes())
    return patches


def pack(patches: list[Patch], dtype=np.float64) -> np.ndarray:
    """Stack patches into an ``(N, 1, 3)`` linear-ProPhoto image (1-pixel-wide;
    see the module docstring on why spatial ops must NOT use this)."""
    return np.array([p.rgb for p in patches], dtype=dtype).reshape(-1, 1, 3)


def nearblack_chromatic_field(h: int = 48, w: int = 48) -> np.ndarray:
    """A DETERMINISTIC (no-RNG) 2-D near-black field that reproduces the
    perceptual-near-black bug end-to-end through Stage 11+12.

    The cast is born when ``apply_blacks_2012`` (a uniform additive floor at 0)
    crushes the smaller channels of a *slightly-chromatic dark* pixel to EXACTLY
    0 — leaving a degenerate single-channel near-black pixel — and a shadow-
    LIFTING perceptual reapply (``Contrast<0`` / ``+Shadows`` / Texture) then
    amplifies that degeneracy via the ``lum_out/lum → ∞`` ratio. To trigger it
    the field must (a) straddle the ``Blacks(-10) = 0.005`` additive bias so some
    channels survive the floor as slivers while others zero, and (b) carry a
    small per-channel imbalance so the survivor is a *single* channel.

    Built from ``linspace`` grids (fully addressable, byte-reproducible) — NOT
    ``np.random`` (a uniform rand never samples this L<0.01 region; that gap is
    the whole reason the bug shipped). Returns float32 ``(h, w, 3)``."""
    # Base luminance TIGHTLY straddling 0.005 (the Blacks(-10) bias): 0.0050 …
    # 0.0054. The tight straddle is load-bearing — the surviving sliver
    # (input_channel − 0.005) is then DEEP below the guard's 0.004 floor (post-
    # Blacks luma ≈ 1e-4), so the guard fully neutralises it (gate ≈ 0). A wider
    # base would leave above-floor pixels the guard deliberately does NOT touch
    # (legit shadow colour) carrying legit chroma — which is correct behaviour,
    # NOT the bug, and would make the catcher bound un-meetable by the real fix
    # (verified against fix/perceptual-nearblack: this field → guarded chroma
    # < 2.4e-3 for every grade, vs > 0.01–18 on buggy main).
    base = np.linspace(0.0050, 0.0054, h, dtype=np.float32)[:, None]  # (h, 1)
    # Curated per-channel imbalance triples (±~5-6%): each makes a DIFFERENT
    # single channel the sole survivor of the floor (the degenerate pixel the
    # lift/toe amplify into a cast), plus two-survivor and a near-neutral control.
    # Tiled across the columns → fully enumerated, no RNG.
    triples = np.array([
        [1.06, 0.95, 0.96],  # R-only survivor
        [0.95, 1.06, 0.96],  # G-only
        [0.96, 0.95, 1.06],  # B-only
        [1.05, 1.04, 0.95],  # B falls, R/G survive
        [0.95, 1.05, 1.04],  # R falls
        [1.01, 0.995, 1.005],  # near-neutral (legit-shadow control)
    ], dtype=np.float32)
    imbalance = triples[np.arange(w) % len(triples)]      # (w, 3)
    return (base[:, :, None] * imbalance[None, :, :]).astype(np.float32)


def neutral_indices(patches: list[Patch]) -> list[int]:
    return [i for i, p in enumerate(patches) if p.is_neutral]


def nearblack_neutral_indices(patches: list[Patch], floor: float = 0.004) -> list[int]:
    """Indices of neutral patches whose luminance is below the near-black floor
    (where the perceptual guard forces neutrality)."""
    return [i for i, p in enumerate(patches) if p.is_neutral and p.luma < floor]


# --- chroma / validity metrics (taken on the DECODED emission) -------------


def max_abs_chroma(rgb: np.ndarray) -> np.ndarray:
    """Per-pixel max(|R−G|, |G−B|, |R−B|) — a sign-blind achromatic-distance
    proxy. 0 ⇔ R==G==B (neutral). Works on any (…, 3)."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return np.maximum(np.maximum(np.abs(r - g), np.abs(g - b)), np.abs(r - b))


def chroma_over_luma(rgb: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    """Chroma normalised by luminance — the cast magnitude *relative* to bright-
    ness, the scale-free quantity that exposes a near-black false cast (a 0.02
    chroma on a 0.004 pixel is a 5× cast, invisible in absolute terms)."""
    lum = np.maximum(rgb @ _PROPHOTO_LUMINANCE, eps)
    return max_abs_chroma(rgb) / lum


# --- emission helpers — the REAL output.py colour path ---------------------


def emit_acescg(prophoto: np.ndarray) -> np.ndarray:
    """ProPhoto(D50) → emitted ACEScg(AP1), in memory: the exact ``output.py``
    EXR colour maths — ProPhoto→AP1 Bradford + NaN-scrub + the gated ACES RGC —
    minus the float→half write. This is the surface the near-black bug appears
    on (the Bradford turns an out-of-AP1 cast into negative AP1 channels the
    gated RGC cannot rescue at near-black). float32, matching the writer."""
    from lrt_cinema import output

    ap1 = output._prophoto_to_linear(prophoto, "acescg").astype(np.float32)
    ap1 = np.nan_to_num(ap1, nan=0.0, posinf=65504.0, neginf=0.0)
    return output._aces_rgc_compress_ap1(ap1)


def emit_ap0(prophoto: np.ndarray) -> np.ndarray:
    """ProPhoto(D50) → emitted ACES2065-1 (AP0), in memory. AP0 is NOT gamut-
    compressed (wider than AP1; RGC limits are AP1-specific), so negatives are
    an ALLOWED archival reality here — assert finite + neutral, never min≥0."""
    from lrt_cinema import output

    ap0 = output._prophoto_to_linear(prophoto, "aces2065").astype(np.float32)
    return np.nan_to_num(ap0, nan=0.0, posinf=65504.0, neginf=0.0)


def _read_exr_rgb(path) -> np.ndarray:
    """Read an EXR back as (H, W, 3) float — the ASWF OpenEXR binding."""
    import OpenEXR

    with OpenEXR.File(str(path), separate_channels=True) as exr:
        ch = exr.channels()
        return np.stack(
            [ch["R"].pixels, ch["G"].pixels, ch["B"].pixels], axis=-1,
        ).astype(np.float64)


def roundtrip_exr(
    prophoto: np.ndarray, path, *, colorspace: str = "acescg",
    compression: str = "zip", bit_depth: str = "half",
) -> np.ndarray:
    """Write a **real** scene-linear EXR and read it back. LOSSLESS by default
    (``zip``) — the production DWAB default is DCT-lossy and can quantise a flat
    near-black cast AWAY (a false green on buggy code), so the math legs pin a
    lossless codec. Returns the decoded (H, W, 3)."""
    from lrt_cinema.output import write_exr_scene_linear

    write_exr_scene_linear(
        prophoto, path, bit_depth=bit_depth, compression=compression,
        colorspace=colorspace,
    )
    return _read_exr_rgb(path)


def roundtrip_tiff(
    prophoto: np.ndarray, path, *, colorspace: str = "srgb", bit_depth: int = 16,
    icc_profile: bytes | None = None,
) -> np.ndarray:
    """Write a **real** display TIFF and read it back as float in [0, 1]."""
    import tifffile

    from lrt_cinema.output import write_tiff_display

    write_tiff_display(
        prophoto, path, colorspace=colorspace, bit_depth=bit_depth,
        icc_profile=icc_profile,
    )
    maxv = float((1 << bit_depth) - 1)
    return tifffile.imread(str(path)).astype(np.float64) / maxv
