"""Output preset names. The render dispatch lives in `lrt_cinema.output`.

v0.7 presets (per `docs/research/v07-spec-revision-plan.md` +
`docs/research/v07-beta-xml-deadend.md`):
  cinema-linear-finished → 16-bit half EXR DWAB, linear Rec.2020.
                           γ / v0.7 default. Full DCP shape baked.
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
    "cinema-linear-finished",
    "cinema-linear-master",
    "cinema-linear",
    "cinema-aces",
    "stills-finished",
})

DEFAULT_PRESET = "cinema-linear-finished"

# Presets that emit at Stage 7 (pre-LookTable, pre-ProfileToneCurve).
# Consumed by the CLI worker to choose `stop_after_stage` for `render_frame`.
STAGE_7_PRESETS: frozenset[str] = frozenset({"cinema-linear-master"})


__all__ = ["DEFAULT_PRESET", "PRESETS", "STAGE_7_PRESETS"]
