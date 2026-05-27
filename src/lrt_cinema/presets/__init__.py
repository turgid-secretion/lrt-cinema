"""Output preset names. The render dispatch lives in `lrt_cinema.output`."""

from __future__ import annotations

# Three presets — see docs/research/v06-architecture.md §"Output formats".
#  cinema-linear   → 16-bit int TIFF, linear Rec.2020
#  cinema-aces     → 32-bit float EXR (PIZ), linear Rec.2020
#  stills-finished → 16-bit int TIFF, Rec.2020 gamma + AgX (NotImplemented in v0.6)
PRESETS: frozenset[str] = frozenset({
    "cinema-linear",
    "cinema-aces",
    "stills-finished",
})


__all__ = ["PRESETS"]
