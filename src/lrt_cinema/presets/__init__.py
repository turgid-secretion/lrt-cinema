"""Output preset names. The render dispatch lives in `lrt_cinema.output`.

Presets (only standards-aligned colour spaces — see CLAUDE.md allowlist):
  lrtimelapse            → 16-bit sRGB display TIFF (embedded ICC).
                           v0.8 DEFAULT. Display-referred, full LRT look baked;
                           the only emission LRT's video renderer re-ingests
                           (LRT → Render from Intermediate → Motion Blur).
  cinema-linear-finished → 16-bit half EXR DWAB, scene-linear ACEScg (AP1).
                           Master for DaVinci Resolve / ACES (bypasses LRT).
                           Full DCP shape baked.
  cinema-linear-master   → 16-bit half EXR DWAB, scene-linear ACEScg (AP1).
                           β; Stage 7 emission; skips LookTable +
                           ProfileToneCurve for HDR headroom.
  stills-finished        → display Rec.2020 gamma + AgX. NotImplemented.

Removed: cinema-linear / cinema-aces — both emitted *linear Rec.2020*, a
delivery gamut misused as scene-referred (a colour-science error). ACEScg /
ACES2065-1 are the only standards-aligned scene-linear gamuts.
"""

from __future__ import annotations

PRESETS: frozenset[str] = frozenset({
    "lrtimelapse",
    "cinema-linear-finished",
    "cinema-linear-master",
    "stills-finished",
})

DEFAULT_PRESET = "lrtimelapse"

# Presets that emit at Stage 7 (pre-LookTable, pre-ProfileToneCurve).
# Consumed by the CLI worker to choose `stop_after_stage` for `render_frame`.
STAGE_7_PRESETS: frozenset[str] = frozenset({"cinema-linear-master"})


__all__ = ["DEFAULT_PRESET", "PRESETS", "STAGE_7_PRESETS"]
