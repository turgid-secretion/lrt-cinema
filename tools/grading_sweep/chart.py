"""Synthetic linear-ProPhoto chart for the grading-sweep harness.

A small set of flat patches with *known* (hue, saturation, value) labels,
synthesised directly in linear ProPhoto(D50) — the space the Stage-12 develop
ops (`develop_ops.apply_hsl` / `apply_color_grade`) operate in. This is the
input the sweep drives every HSL band and every Color-Grade tonal zone against.

It is built in the SAME Adobe-hexcone HSV model the ops use (`_hsv_to_rgb_dcp`),
so a "red" patch sits exactly on HSL band 0 and a "shadow" patch sits at a known
low value — which is what lets the sanity test assert band-locality and
zone-locality. The sanity test checks *structural* properties (monotonicity,
locality, non-negativity), NOT exact values vs a reimpl — that is the Axis-1
oracle's job (`tests/test_color_oracle.py`), so reusing the HSV model here is a
labelling convenience, not a circular check.

Patches are emitted as ProPhoto RGB; group with `chart_array()` to feed the ops.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from lrt_cinema.lut3d_baker import _hsv_to_rgb_dcp


@dataclass(frozen=True)
class ChartPatch:
    """One flat patch with its known HSV label and its linear-ProPhoto RGB."""

    name: str
    hue_deg: float       # 0..360 (informational; 0 for neutrals)
    sat: float           # HSV saturation 0..1
    val: float           # HSV value 0..1 (linear ProPhoto)
    is_neutral: bool
    rgb: tuple[float, float, float]


def _prophoto_from_hsv(hue_deg: float, sat: float, val: float) -> tuple[float, float, float]:
    """Linear ProPhoto RGB for an Adobe-hexcone (hue_deg, sat, val)."""
    h_hex = np.array([(hue_deg % 360.0) * (6.0 / 360.0)], dtype=np.float64)
    rgb = _hsv_to_rgb_dcp(h_hex, np.array([sat], dtype=np.float64), np.array([val], dtype=np.float64))[0]
    return (float(rgb[0]), float(rgb[1]), float(rgb[2]))


# Hue ring covers every 30° so all 8 HSL band centres (0/30/60/120/180/240/270/
# 300) and the gaps between them are represented.
_HUE_RING_DEG = (0.0, 30.0, 60.0, 90.0, 120.0, 150.0, 180.0, 210.0, 240.0, 270.0, 300.0, 330.0)
# Tonal column (fixed hue) for Color-Grade shadow/mid/highlight zone tests.
_TONE_VALUES = (0.04, 0.18, 0.45, 0.85)
# Neutral wedge for neutral-protection / global-tint tests.
_NEUTRAL_VALUES = (0.04, 0.18, 0.45, 0.90)


def build_prophoto_chart() -> list[ChartPatch]:
    """A chart exercising every hue band, the tonal zones, neutrals, and the
    saturated gamut edge — all in linear ProPhoto(D50)."""
    patches: list[ChartPatch] = []

    # Hue ring at mid saturation/value (band-locality + hue-rotation tests).
    for hue in _HUE_RING_DEG:
        patches.append(ChartPatch(
            name=f"hue{int(hue):03d}", hue_deg=hue, sat=0.8, val=0.5,
            is_neutral=False, rgb=_prophoto_from_hsv(hue, 0.8, 0.5),
        ))

    # Tonal column at a fixed orange-ish hue (Color-Grade zone tests).
    for val in _TONE_VALUES:
        patches.append(ChartPatch(
            name=f"tone_v{val:.2f}", hue_deg=30.0, sat=0.6, val=val,
            is_neutral=False, rgb=_prophoto_from_hsv(30.0, 0.6, val),
        ))

    # Neutral wedge (sat=0).
    for val in _NEUTRAL_VALUES:
        patches.append(ChartPatch(
            name=f"grey_v{val:.2f}", hue_deg=0.0, sat=0.0, val=val,
            is_neutral=True, rgb=(val, val, val),
        ))

    # Fully-saturated gamut-edge patches (no-negative-channel stress).
    for hue in (0.0, 120.0, 240.0):
        patches.append(ChartPatch(
            name=f"sat_hue{int(hue):03d}", hue_deg=hue, sat=1.0, val=0.7,
            is_neutral=False, rgb=_prophoto_from_hsv(hue, 1.0, 0.7),
        ))

    return patches


def chart_array(patches: list[ChartPatch]) -> np.ndarray:
    """Stack patches into an (N, 1, 3) float64 image for the develop ops."""
    return np.array([p.rgb for p in patches], dtype=np.float64).reshape(-1, 1, 3)


def patch_chroma(rgb: np.ndarray) -> np.ndarray:
    """Per-patch HSV chroma (max−min) over an (N,1,3) image → (N,)."""
    flat = rgb.reshape(-1, 3)
    return flat.max(axis=1) - flat.min(axis=1)
