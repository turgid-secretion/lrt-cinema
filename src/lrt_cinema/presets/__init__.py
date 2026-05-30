"""Output preset names. The render dispatch lives in `lrt_cinema.output`.

Presets:
  lrtimelapse            → 16-bit sRGB display TIFF (embedded ICC).
                           v0.8 DEFAULT. Display-referred, full LRT look baked;
                           the only emission LRT's video renderer re-ingests
                           (LRT → Render from Intermediate → Motion Blur).
  cinema-linear-finished → 16-bit half EXR DWAB, ACEScg.
                           Scene-linear master for DaVinci Resolve / ACES
                           (bypasses LRT). Full DCP shape baked.
  cinema-linear-master   → 16-bit half EXR DWAB, linear Rec.2020.
                           β (Option B, v0.7.1). Stage 7 emission;
                           skips LookTable + ProfileToneCurve for HDR
                           headroom. LR PV2012 ops still apply.
  cinema-linear          → 32-bit float TIFF, linear Rec.2020. v0.6 back-compat.
  cinema-aces            → 32-bit float EXR PIZ, linear Rec.2020. Deprecated.
  stills-finished        → 16-bit int TIFF, Rec.2020 gamma + AgX. NotImplemented.
"""

from __future__ import annotations

PRESETS: frozenset[str] = frozenset({
    "lrtimelapse",
    "cinema-linear-finished",
    "cinema-linear-master",
    "cinema-linear",
    "cinema-aces",
    "stills-finished",
})

DEFAULT_PRESET = "lrtimelapse"

# Presets that emit at Stage 7 (pre-LookTable, pre-ProfileToneCurve).
# Consumed by the CLI worker to choose `stop_after_stage` for `render_frame`.
STAGE_7_PRESETS: frozenset[str] = frozenset({"cinema-linear-master"})


__all__ = ["DEFAULT_PRESET", "PRESETS", "STAGE_7_PRESETS"]
