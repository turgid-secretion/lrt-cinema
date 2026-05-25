"""Drive `darktable-cli` per frame.

Single-worker scheduler for v0.1. The N-parallel worker pool noted in
SCOPE.md is a straightforward `concurrent.futures.ProcessPoolExecutor`
wrap of `render_frame()` once we have real benchmark data to size the
pool against.

This module is intentionally thin: it composes XMP-emitter +
preset-definitions + a subprocess call. Anything that requires real
darktable I/O is gated behind `dry_run` so the test suite can exercise
the orchestration without darktable installed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from lrt_cinema.dcp import DCPProfile
from lrt_cinema.ir import DevelopOps
from lrt_cinema.presets import Preset
from lrt_cinema.xmp_emitter import emit_darktable_xmp


class DarktableCliNotFound(RuntimeError):
    """Raised when `darktable-cli` is not on PATH and dry_run is False."""


# SHAs of darktable revisions the emitter's modversion constants have been
# verified against (see audit doc + per-module 'accepts' integration tests).
# A `darktable-cli --version` that reports any other SHA gets a one-line
# stderr warning — every dt release bumps modversions and the established
# failure mode is silent default-substitution producing wrong pixels with
# a successful exit code.
_KNOWN_TESTED_DT_SHAS = frozenset({
    "9402c65275",  # darktable 5.5.0+1375 — primary dev / audit target
})

_DT_VERSION_RE = re.compile(r"darktable\s+(\S+).*~g([0-9a-f]{8,})", re.IGNORECASE)


def darktable_version() -> tuple[str, str] | None:
    """Return (version_string, sha) for the installed darktable-cli, or None.

    None when darktable-cli is not on PATH or `--version` output cannot be
    parsed. Output shape:
        darktable 5.5.0+1375~g9402c65275 OpenMP support: yes...
    """
    bin_path = shutil.which("darktable-cli")
    if bin_path is None:
        return None
    try:
        proc = subprocess.run(
            [bin_path, "--version"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = proc.stdout + proc.stderr
    match = _DT_VERSION_RE.search(text)
    if match is None:
        return None
    return match.group(1), match.group(2)


def warn_on_untested_darktable_version() -> None:
    """One-line stderr warning if the installed dt's SHA is not in our
    tested set. Idempotent within a process (caches the result)."""
    if warn_on_untested_darktable_version.__dict__.get("_done"):
        return
    warn_on_untested_darktable_version.__dict__["_done"] = True
    info = darktable_version()
    if info is None:
        return  # silent when probe fails — runner's own checks surface the issue
    version, sha = info
    if sha not in _KNOWN_TESTED_DT_SHAS:
        sys.stderr.write(
            f"warning: darktable {version} (sha {sha}) is outside lrt-cinema's "
            f"tested SHA set ({sorted(_KNOWN_TESTED_DT_SHAS)}). Module "
            f"params layouts can shift release-to-release; dt's silent "
            f"default-substitution on a rejected params blob produces "
            f"wrong pixels with a successful exit code. Watch the output.\n"
        )


@dataclass
class FrameResult:
    frame_index: int
    source_path: Path
    output_path: Path
    returncode: int
    skipped: bool = False
    error: str | None = None


def darktable_cli_path() -> str | None:
    """Return the resolved path to `darktable-cli`, or None if not found."""
    return shutil.which("darktable-cli")


DEFAULT_PER_FRAME_TIMEOUT_S = 600


def _refuse_dash_prefix(path: Path, label: str) -> None:
    """Reject paths whose basename starts with '-' so darktable-cli does not parse them as flags."""
    if path.name.startswith("-"):
        raise ValueError(
            f"{label} basename begins with '-': {path}. darktable-cli would parse this as a flag."
        )


def render_frame(
    frame_index: int,
    source_path: Path,
    output_dir: Path,
    ops: DevelopOps,
    preset: Preset,
    custom_style: Path | None = None,
    dcp_profile: DCPProfile | None = None,
    apply_dcp_tone_curve: bool = True,
    apply_dcp_hsv_cubes: bool = True,
    dry_run: bool = False,
    timeout_s: float | None = DEFAULT_PER_FRAME_TIMEOUT_S,
) -> FrameResult:
    """Render one frame.

    Steps:
      1. Emit a per-frame darktable XMP sidecar into output_dir
         (NOT next to the source — that file is the user's LRT XMP).
      2. Build the `darktable-cli` argv from the preset.
      3. Run darktable-cli (or skip if dry_run).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    source_path = source_path.resolve()
    output_dir = output_dir.resolve()
    _refuse_dash_prefix(source_path, "source RAW")

    output_path = output_dir / f"{source_path.stem}{preset.output_extension}"
    xmp_path = output_dir / f"{source_path.stem}{source_path.suffix}.dt.xmp"
    # Cube-emission policy: when a DCP carries HSV cubes (HueSatMap or
    # LookTable) and apply_dcp_hsv_cubes is True, the emitter writes a
    # .cube file next to the XMP and the dt-cli invocation below points
    # dt's lut3d module at that directory via the def_path config key.
    cube_will_emit = (
        apply_dcp_hsv_cubes
        and dcp_profile is not None
        and (dcp_profile.hue_sat_map is not None
             or dcp_profile.look_table is not None)
    )
    emit_darktable_xmp(
        ops, xmp_path,
        dcp_profile=dcp_profile,
        apply_dcp_tone_curve=apply_dcp_tone_curve,
        apply_dcp_hsv_cubes=apply_dcp_hsv_cubes,
        dt_lut3d_def_path=output_dir if cube_will_emit else None,
    )

    style_path: Path | None = None
    if custom_style is not None:
        style_path = custom_style.resolve()

    # Build argv per dt EXPORT.md (docs/reference/darktable/EXPORT.md).
    #
    # Three flags do the heavy lifting:
    #   --apply-custom-presets 0  — disables dt's workflow auto-injection
    #     (filmic/sigmoid prepend). Without this, output reflects the user's
    #     local dt workflow config, not our preset intent. Essential for
    #     deterministic reproducible output across machines.
    #   --icc-type LIN_REC2020 — forces the colorout module to emit linear
    #     Rec.2020 regardless of any colorout history entry in the sidecar.
    #     (src/cli/main.c#L863 in dt master forces this override.)
    #   --core --conf plugins/imageio/format/<fmt>/bpp=<N> — the ONLY way
    #     to control bit depth. The documented --bpp flag is a no-op
    #     (src/cli/main.c#L279-290 says "TODO: sorry, due to API
    #     restrictions we currently cannot set the BPP"). A .style file
    #     cannot pin bpp either: it carries module history only, not
    #     format-plugin conf.
    #
    # Without these three flags, dt-cli silently defaults to 8-bit sRGB
    # regardless of preset (data/darktableconfig.xml.in tiff/bpp default = 8).
    argv = [
        "darktable-cli",
        str(source_path),
        str(xmp_path),
        str(output_path),
        "--apply-custom-presets", "0",
    ]

    # ICC type override — maps preset.output_color_profile to dt's
    # --icc-type token. Per src/cli/main.c#L115-144 valid tokens include
    # LIN_REC2020, LIN_REC709, SRGB, REC709, PROPHOTO_RGB, etc.
    _ICC_TYPE_BY_PROFILE = {
        "lin_rec2020": "LIN_REC2020",
        "lin_rec709": "LIN_REC709",
        "srgb": "SRGB",
    }
    icc_type = _ICC_TYPE_BY_PROFILE.get(preset.output_color_profile)
    if icc_type is not None:
        argv += ["--icc-type", icc_type, "--icc-intent", "RELATIVE_COLORIMETRIC"]

    if style_path is not None:
        # No --style-overwrite — that would wipe the per-frame XMP history we
        # just emitted (see docs/reference/darktable/STYLES.md). Default
        # --style behavior appends, with per-frame sidecar entries taking
        # precedence at same op priority — exactly what we want.
        argv += ["--style", str(style_path)]

    # Format-plugin conf (the bit-depth control). Must go LAST after --core.
    # All --conf KEY=VAL pairs after a single --core are accepted.
    core_conf: list[str] = []
    if preset.output_format == "tiff":
        core_conf += [
            f"plugins/imageio/format/tiff/bpp={preset.bpp}",
            "plugins/imageio/format/tiff/compress=0",   # uncompressed
            "plugins/imageio/format/tiff/pixelformat=0",  # 0=int, 1=float
        ]
    elif preset.output_format == "exr":
        core_conf += [
            f"plugins/imageio/format/exr/bpp={preset.bpp}",
            "plugins/imageio/format/exr/compression=2",  # 2=PIZ
        ]
    # DCP HSV-cube emission writes a `.cube` file alongside the XMP and
    # references it from the lut3d module's params. dt resolves the
    # filepath against the `plugins/darkroom/lut3d/def_path` config; we
    # point it at the per-frame output directory. Per the lut3d cube-
    # filepath resolution at src/iop/lut3d.c#L1178-L1196 SHA 9402c65275,
    # the absolute-vs-relative semantics make def_path the cleanest
    # injection point — dt prepends it to the (relative) cube basename.
    if cube_will_emit:
        core_conf.append(f"plugins/darkroom/lut3d/def_path={output_dir}")
    if core_conf:
        argv += ["--core"]
        for kv in core_conf:
            argv += ["--conf", kv]

    if dry_run:
        return FrameResult(
            frame_index=frame_index,
            source_path=source_path,
            output_path=output_path,
            returncode=0,
            skipped=True,
        )

    if darktable_cli_path() is None:
        raise DarktableCliNotFound(
            "darktable-cli not on PATH. Install darktable: "
            "macOS: `brew install --cask darktable` ; "
            "Debian/Ubuntu: `sudo apt install darktable` ; "
            "Fedora: `sudo dnf install darktable` ; "
            "Arch: `sudo pacman -S darktable`"
        )

    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, check=False, timeout=timeout_s,
        )
        return FrameResult(
            frame_index=frame_index,
            source_path=source_path,
            output_path=output_path,
            returncode=proc.returncode,
            error=(proc.stderr if proc.returncode != 0 else None),
        )
    except subprocess.TimeoutExpired as exc:
        return FrameResult(
            frame_index=frame_index,
            source_path=source_path,
            output_path=output_path,
            returncode=-1,
            error=f"darktable-cli timeout after {exc.timeout}s",
        )
    except OSError as exc:
        return FrameResult(
            frame_index=frame_index,
            source_path=source_path,
            output_path=output_path,
            returncode=-1,
            error=str(exc),
        )


def render_sequence(
    source_dir: Path,
    output_dir: Path,
    per_frame_ops: list[DevelopOps],
    preset: Preset,
    source_frames: list[str],
    from_frame: int = 0,
    to_frame: int | None = None,
    custom_style: Path | None = None,
    dcp_profile: DCPProfile | None = None,
    apply_dcp_tone_curve: bool = True,
    apply_dcp_hsv_cubes: bool = True,
    dry_run: bool = False,
    timeout_s: float | None = DEFAULT_PER_FRAME_TIMEOUT_S,
) -> list[FrameResult]:
    """Render a frame range. Single-worker for v0.1."""
    if not dry_run:
        warn_on_untested_darktable_version()
    end = len(source_frames) if to_frame is None else min(to_frame, len(source_frames))
    results: list[FrameResult] = []
    for i in range(from_frame, end):
        source_path = source_dir / source_frames[i]
        ops = per_frame_ops[i]
        result = render_frame(
            frame_index=i,
            source_path=source_path,
            output_dir=output_dir,
            ops=ops,
            preset=preset,
            custom_style=custom_style,
            dcp_profile=dcp_profile,
            apply_dcp_tone_curve=apply_dcp_tone_curve,
            apply_dcp_hsv_cubes=apply_dcp_hsv_cubes,
            dry_run=dry_run,
            timeout_s=timeout_s,
        )
        results.append(result)
    return results
