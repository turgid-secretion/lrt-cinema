"""Stable local-fixture resolution for the render/ΔE regression tests.

The render fixtures (D750 DNG, Adobe dng_validate reference TIFFs, the
dng_validate binary itself) are too large to commit and historically lived in
``/tmp/dng_out`` — which macOS wipes periodically, silently disabling the ship
gates. The canonical home is now ``~/lrt-cinema-fixtures`` (override with the
``LRT_CINEMA_FIXTURES`` env var); the legacy ``/tmp/dng_out`` location is still
honoured as a fallback so nothing breaks mid-migration.

Contents + regeneration commands: ``FIXTURES.md`` at the repo root.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_STABLE = Path(
    os.environ.get("LRT_CINEMA_FIXTURES", str(Path.home() / "lrt-cinema-fixtures")),
)
_LEGACY = Path("/tmp/dng_out")
_LEGACY_DNG_VALIDATE = Path("/private/tmp/dng_sdk/_build/dng_sdk/source/dng_validate")


def fixture_root() -> Path:
    """The directory render tests read fixtures from AND write scratch into."""
    return _STABLE if _STABLE.is_dir() else _LEGACY


def fixture(name: str) -> Path:
    """Resolve a named fixture: stable root first, legacy /tmp second.

    Always returns a path (possibly non-existent) so callers keep their own
    ``.is_file()`` skip-gates."""
    stable = _STABLE / name
    if stable.exists():
        return stable
    return _LEGACY / name


def dng_validate_binary() -> Path:
    """The Adobe dng_validate executable: stable copy, legacy SDK build, PATH."""
    for candidate in (_STABLE / "dng_validate", _LEGACY_DNG_VALIDATE):
        if candidate.is_file():
            return candidate
    which = shutil.which("dng_validate")
    return Path(which) if which else _LEGACY_DNG_VALIDATE
