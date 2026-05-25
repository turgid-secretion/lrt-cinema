"""Per-camera color-correction matrix storage + auto-detection.

The algorithmic engine (`--engine algorithmic`) renders without DCP-derived
modules and uses darktable's libraw-derived defaults for white-balance and
input-color matrix. This produces a per-camera color rendition gap vs the
DCP-driven engine (typical ΔE2000 mean 5-10 on real footage).

A `Calibration` is a 3×3 channelmixer correction matrix fitted offline that
maps the algorithmic engine's "no DCP" output into the DCP-driven engine's
output (or, in Tier 1, into a known target XYZ derived from spectral data).
The fitted matrix lives at:

    $LRT_CINEMA_CALIBRATION  (env-var override, takes a single directory)
    ~/.config/lrt-cinema/calibration/<camera-label>.npz  (XDG default)
    %APPDATA%/lrt-cinema/calibration/<camera-label>.npz  (Windows default)

mirroring the layout of `~/.config/lrt-cinema/profiles/` for DCP profiles.
Auto-detect at render time matches the same Adobe-style camera label
(see `dcp._adobe_camera_label`).

This module ships the storage + lookup infrastructure (Phase 2a).
The matrix-fitting tools (Tier 2 DCP distillation, Tier 1 SSF synthesis,
Tier 3 physical chart) are separate concerns implemented in
`tools/calibrate_camera.py` (Phase 2b+).

Schema notes
------------
* `format_version` is bumped only on backwards-incompatible changes;
  additive fields land at the next minor without a bump.
* `tier` records which tier produced the matrix (2 = DCP distillation,
  1 = SSF synthesis, 3 = physical chart, 0 = explicit user-supplied).
* `source` is the path / identifier of the calibration source (the
  DCP path for Tier 2, SSF dataset name for Tier 1, chart RAW path for
  Tier 3, or "explicit" for Tier 0). Lossless audit trail.
* `matrix` is the 3×3 channelmixer correction in linear-Rec.2020 working
  space (the algorithmic engine's output space). Emitted as
  `channelmixerrgb` v3 in the algorithmic-engine emit path.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from lrt_cinema.dcp import _adobe_camera_label, read_raw_make_model

_CALIBRATION_FORMAT_VERSION = 1


@dataclass
class Calibration:
    """A fitted per-camera channelmixer correction matrix + provenance.

    `matrix` shape: (3, 3), float32, linear-Rec.2020 working-space
    transform applied to the algorithmic-engine output. Identity = no
    correction (a valid calibration meaning "the algorithmic engine
    matches the target for this camera as-is").
    """

    camera_label: str
    matrix: np.ndarray
    tier: int                 # 0=explicit, 1=SSF, 2=DCP-distill, 3=chart
    source: str               # path / identifier of calibration source
    delta_e2000_mean: float = 0.0     # ΔE2000 mean post-fit (0.0 if not measured)
    delta_e2000_max: float = 0.0      # ΔE2000 max post-fit (0.0 if not measured)


def save_calibration(calibration: Calibration, path: Path) -> None:
    """Serialize a Calibration to `.npz`.

    Schema:
        format_version: int32
        camera_label:   0-d unicode
        matrix:         (3, 3) float32
        tier:           int32 (0..3)
        source:         0-d unicode
        delta_e2000_mean: float32
        delta_e2000_max:  float32
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if calibration.matrix.shape != (3, 3):
        raise ValueError(
            f"calibration matrix must be (3, 3), got {calibration.matrix.shape}"
        )
    if calibration.tier not in (0, 1, 2, 3):
        raise ValueError(
            f"calibration tier must be one of 0/1/2/3, got {calibration.tier}"
        )
    np.savez_compressed(
        path,
        format_version=np.int32(_CALIBRATION_FORMAT_VERSION),
        # Coerce None → "" so the .npz roundtrip doesn't turn None into
        # the literal string "None" (same defensive pattern as
        # dcp.save_profile per caveman-review PR #8 #7).
        camera_label=np.array(calibration.camera_label or "", dtype="U"),
        matrix=calibration.matrix.astype(np.float32),
        tier=np.int32(calibration.tier),
        source=np.array(calibration.source or "", dtype="U"),
        delta_e2000_mean=np.float32(calibration.delta_e2000_mean),
        delta_e2000_max=np.float32(calibration.delta_e2000_max),
    )


def load_calibration(path: Path) -> Calibration:
    """Deserialize a Calibration from `.npz`.

    Raises ValueError on missing required fields or on a format-version
    we don't recognize. Helpful error message points the user to
    re-fit if the version is stale (parallel to dcp.load_profile's
    actionable error).
    """
    path = Path(path)
    with np.load(path, allow_pickle=False) as data:
        version = int(data["format_version"])
        if version != _CALIBRATION_FORMAT_VERSION:
            raise ValueError(
                f"{path}: unsupported calibration format_version {version} "
                f"(this lrt-cinema build understands "
                f"format_version={_CALIBRATION_FORMAT_VERSION}). "
                f"Re-fit by running `tools/calibrate_camera.py` against "
                f"the source camera/DCP, or upgrade/downgrade lrt-cinema "
                f"to match the file's version."
            )
        if "matrix" not in data:
            raise ValueError(f"{path}: missing matrix — not a valid calibration")
        return Calibration(
            camera_label=str(data["camera_label"]),
            matrix=data["matrix"].astype(np.float32),
            tier=int(data["tier"]),
            source=str(data["source"]),
            delta_e2000_mean=float(data["delta_e2000_mean"]),
            delta_e2000_max=float(data["delta_e2000_max"]),
        )


def _calibration_search_roots() -> list[Path]:
    """Where to look for `.npz` calibration files, in lookup order.

    Mirrors `dcp._extracted_profile_search_roots`:
      1. `$LRT_CINEMA_CALIBRATION` env var — explicit user override
      2. `~/.config/lrt-cinema/calibration/` (XDG-style per-user)
      3. `%APPDATA%/lrt-cinema/calibration/` on Windows
    """
    roots: list[Path] = []
    env = os.environ.get("LRT_CINEMA_CALIBRATION")
    if env:
        p = Path(env)
        if p.is_dir():
            roots.append(p)
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            roots.append(Path(appdata) / "lrt-cinema" / "calibration")
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        config_home = Path(xdg) if xdg else (Path.home() / ".config")
        roots.append(config_home / "lrt-cinema" / "calibration")
    return [r for r in roots if r.is_dir()]


def find_calibration_for_camera(
    make: str,
    model: str,
    extra_roots: list[Path] | None = None,
) -> Path | None:
    """Locate a `.npz` calibration for (make, model).

    Filename convention: `<label>.npz` where `<label>` is the Adobe-style
    camera label (e.g. "Nikon D750"). Returns the first match across
    the search roots in `_calibration_search_roots()` plus any
    `extra_roots` passed in (used by tests).
    """
    label = _adobe_camera_label(make, model)
    roots = _calibration_search_roots()
    if extra_roots:
        roots.extend(r for r in extra_roots if r.is_dir())
    for root in roots:
        candidate = root / f"{label}.npz"
        if candidate.is_file():
            return candidate
    return None


def auto_detect_calibration(
    raw_path: Path,
    extra_roots: list[Path] | None = None,
) -> tuple[Calibration, Path] | None:
    """End-to-end calibration lookup from a RAW file.

    Probes the RAW's EXIF Make/Model, searches the calibration roots,
    returns `(calibration, source_path)` or None. Caller logs the
    source for the audit trail. Mirrors `dcp.auto_detect_profile`
    contract.
    """
    info = read_raw_make_model(raw_path)
    if info is None:
        return None
    make, model = info
    cal_path = find_calibration_for_camera(make, model, extra_roots=extra_roots)
    if cal_path is None:
        return None
    return load_calibration(cal_path), cal_path


def _default_user_calibration_dir() -> Path:
    """Where `tools/calibrate_camera.py` writes by default.

    Mirrors the `~/.config/lrt-cinema/profiles/` default in
    `tools/extract_dcp_library.py`. Honors `$XDG_CONFIG_HOME` on
    Unix and `%APPDATA%` on Windows.
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else (Path.home() / ".config")
    return base / "lrt-cinema" / "calibration"
