"""Command-line interface for lrt-cinema v0.6.

Render path is the in-process Python Adobe DNG 1.7.1 pipeline
(`lrt_cinema.pipeline`); the dt-cli subprocess machinery is gone. See
`docs/research/v06-architecture.md` for the full design.

Flag surface (9 flags, down from 12):
  required:  --input  --output  --preset
  range:     --from-frame  --to-frame
  io:        --dry-run  --quiet
  ops:       --apply-lrt-offsets / --no-lrt-offsets
  profile:   --dcp PATH
  perf:      --workers N
  fallback:  --no-dng-convert

Heavy imports (rawpy, OpenEXR, colour) are deferred to inside the worker
function so `--dry-run` works on a minimal install with no render-time
deps. The `inspect` subcommand is render-independent and likewise touches
only `xmp_parser` + `dcp` (auto-detect path).
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from lrt_cinema import __version__
from lrt_cinema.dcp import (
    DCPProfile,
    auto_detect_profile,
    parse_dcp,
    read_raw_make_model,
)
from lrt_cinema.interpolation import (
    apply_deflicker,
    apply_lrt_mask_offsets,
    materialize_all_frames,
)
from lrt_cinema.ir import DevelopOps
from lrt_cinema.presets import DEFAULT_PRESET, PRESETS, STAGE_7_PRESETS
from lrt_cinema.xmp_parser import parse_sequence

# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lrt-cinema",
        description=(
            "Translate LRTimelapse XMP develop intent into cinema-native "
            "intermediates via an in-process Adobe DNG 1.7.1 render pipeline. "
            "No darktable required."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"lrt-cinema {__version__}",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser(
        "inspect",
        help=(
            "Parse an LRT folder and print what we saw — no rendering. Use "
            "as a parser-validation pass before render."
        ),
    )
    inspect.add_argument("--input", required=True, type=Path)
    inspect.add_argument("--show-fields", action="store_true")

    render = sub.add_parser("render", help="Render an LRT sequence.")
    render.add_argument(
        "--input", required=True, type=Path,
        help="Folder containing source RAW frames + LRT XMP sidecars.",
    )
    render.add_argument(
        "--output", required=True, type=Path,
        help="Folder to write rendered frames into.",
    )
    render.add_argument(
        "--preset", default=DEFAULT_PRESET, choices=sorted(PRESETS),
        help=(
            f"Output preset (default: {DEFAULT_PRESET}). "
            "lrtimelapse (DEFAULT; 16-bit sRGB display TIFF, embedded ICC, "
            "LRT_NNNNN naming — re-ingestible by LRT's video renderer for "
            "Motion Blur), cinema-linear-finished (half-float DWAB EXR, "
            "ACEScg; scene-linear master for DaVinci Resolve), "
            "cinema-linear-master (half-float DWAB EXR at Stage 7 for HDR "
            "headroom), cinema-linear (32-bit float TIFF), "
            "cinema-aces (deprecated; 32-bit float PIZ EXR), "
            "stills-finished (v0.6.x)."
        ),
    )
    render.add_argument(
        "--from-frame", type=int, default=0,
        help="First frame index to render (inclusive).",
    )
    render.add_argument(
        "--to-frame", type=int, default=None,
        help="Last frame index (exclusive). Default: end of sequence.",
    )
    render.add_argument(
        "--dry-run", action="store_true",
        help="Skip the actual render; print what would happen.",
    )
    render.add_argument(
        "--quiet", action="store_true",
        help="Suppress per-frame progress output.",
    )
    render.add_argument(
        "--apply-lrt-offsets", dest="apply_lrt_offsets",
        action="store_true", default=True,
        help=(
            "Apply LRT-authored per-frame exposure deltas — Holy Grail, "
            "Visual Deflicker, and Global mask corrections (default on)."
        ),
    )
    render.add_argument(
        "--no-lrt-offsets", dest="apply_lrt_offsets", action="store_false",
        help="Render keyframe-only ops; ignore LRT-authored per-frame deltas.",
    )
    render.add_argument(
        "--dcp", type=Path, default=None,
        help=(
            "Explicit DCP path. Accepts Adobe `.dcp` or lrt-cinema's `.npz` "
            "extracted profile. Auto-detected from $LRT_CINEMA_PROFILES / "
            "~/.config/lrt-cinema/profiles / Adobe DNG Converter install "
            "when not supplied."
        ),
    )
    render.add_argument(
        "--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2),
        help=(
            "Parallel worker processes (default = os.cpu_count() // 2). "
            "Use 1 for sequential debugging."
        ),
    )
    render.add_argument(
        "--no-dng-convert", dest="no_dng_convert",
        action="store_true", default=False,
        help=(
            "Skip Adobe DNG Converter preprocessing — read NEFs directly "
            "via libraw. Expect ~0.5 ΔE regression vs default (libraw "
            "lacks the DNG's embedded LinearizationTable + correct "
            "WhiteLevel). Required on Linux where Adobe DNG Converter "
            "has no official build."
        ),
    )

    return parser


# ---------------------------------------------------------------------------
# Worker-side render — runs in ProcessPoolExecutor child
# ---------------------------------------------------------------------------


@dataclass
class _RenderJob:
    """Single-frame render unit, must be picklable for ProcessPoolExecutor."""
    frame_index: int
    src_raw: Path
    dst_stem: Path
    ops: DevelopOps
    dcp_path: Path | None
    preset: str
    no_dng_convert: bool
    dng_cache_dir: Path


@dataclass
class _RenderResult:
    frame_index: int
    src_raw: Path
    ok: bool
    error: str | None = None


def _render_one_frame(job: _RenderJob) -> _RenderResult:
    """Subprocess-side render: NEF → DNG (cached) → pipeline → develop_ops →
    output writer. Heavy imports (rawpy, OpenEXR, colour) deferred to here
    so the parent process — and `--dry-run` — stays slim."""
    try:
        # Lazy imports — these are only needed inside the worker.
        from lrt_cinema.develop_ops import apply_develop_ops
        from lrt_cinema.dng_convert import resolve_render_input
        from lrt_cinema.output import write_preset_output
        from lrt_cinema.pipeline import render_frame

        if job.dcp_path is not None:
            profile = (
                _load_dcp_dispatch(job.dcp_path)
                if job.dcp_path.suffix.lower() == ".npz"
                else parse_dcp(job.dcp_path)
            )
        else:
            return _RenderResult(
                job.frame_index, job.src_raw, ok=False,
                error="No DCP profile available; pass --dcp or populate auto-detect roots.",
            )

        dng_path = resolve_render_input(
            job.src_raw, job.dng_cache_dir, no_convert=job.no_dng_convert,
        )
        # cinema-linear-master skips DCP LookTable + ProfileToneCurve
        # (Stages 8 + 9) for HDR headroom. LR PV2012 ops (Stages 11+12)
        # still apply on top of the Stage 7 output.
        stop_after_stage = 7 if job.preset in STAGE_7_PRESETS else 9
        result = render_frame(
            dng_path, profile, dcp_path=job.dcp_path, develop_ops=job.ops,
            stop_after_stage=stop_after_stage,
        )
        with_dev_ops = apply_develop_ops(result.prophoto, job.ops)
        write_preset_output(
            with_dev_ops, job.dst_stem, job.preset,
            provenance={
                "source_frame": job.src_raw.name,
                "frame_index": job.frame_index,
            },
        )
        return _RenderResult(job.frame_index, job.src_raw, ok=True)
    except Exception as exc:
        return _RenderResult(
            job.frame_index, job.src_raw, ok=False, error=f"{type(exc).__name__}: {exc}",
        )


def _load_dcp_dispatch(path: Path) -> DCPProfile:
    """Imported lazily; supports both .dcp (Adobe) + .npz (lrt-cinema's
    extracted format)."""
    from lrt_cinema.dcp import load_profile
    return load_profile(path)


# ---------------------------------------------------------------------------
# `inspect` subcommand (unchanged from v0.4)
# ---------------------------------------------------------------------------


_DROPPED_AT_EMIT_FIELDS = ("highlights", "shadows", "whites")


def _cmd_inspect(args: argparse.Namespace) -> int:
    out = sys.stdout
    try:
        seq = parse_sequence(args.input)
    except (FileNotFoundError, NotADirectoryError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2

    n_frames = seq.frame_count()
    out.write(f"Folder: {args.input}\n")
    out.write(f"Source RAW frames: {n_frames}\n")
    if n_frames:
        out.write(f"  first: {seq.source_frames[0]}\n")
        out.write(f"  last:  {seq.source_frames[-1]}\n")

    kf_count = len(seq.keyframes)
    lrt_flagged = sum(1 for k in seq.keyframes if k.is_lrt_keyframe)
    out.write(f"\nKeyframes detected: {kf_count}\n")
    out.write(
        f"  flagged authoritatively (xmp:Rating or lrt:keyframe): "
        f"{lrt_flagged} of {kf_count}\n"
    )

    if args.show_fields and seq.keyframes:
        out.write("\nPer-keyframe parsed develop ops:\n")
        for kf in seq.keyframes:
            ops = kf.ops
            out.write(
                f"  frame {kf.frame_index:6d} "
                f"lrt_kf={kf.is_lrt_keyframe} "
                f"ev={ops.exposure_ev:+.2f} "
                f"k={ops.temperature_k} tint={ops.tint} "
                f"c={ops.contrast:+.0f} b={ops.blacks:+.0f} "
                f"sat={ops.saturation:+.0f} vib={ops.vibrance:+.0f} "
                f"sharp={ops.sharpness:.0f} curve_pts={len(ops.tone_curve)}\n"
            )

    out.write(
        f"\nLRT mask-correction offsets: {len(seq.lrt_mask_offsets)}\n"
        f"Deflicker offsets (synthetic schema): {len(seq.deflicker_offsets)}\n"
    )

    if seq.keyframes:
        out.write(
            "\nFields parsed but DROPPED at render (closed-source PV5 math):\n"
        )
        for name in _DROPPED_AT_EMIT_FIELDS:
            count = sum(1 for kf in seq.keyframes if getattr(kf.ops, name) != 0.0)
            if count:
                out.write(f"  - {name}: set on {count} of {kf_count} keyframes\n")
    return 0


# ---------------------------------------------------------------------------
# `render` subcommand
# ---------------------------------------------------------------------------


def _resolve_dcp(args: argparse.Namespace, first_raw: Path) -> tuple[Path | None, str]:
    """Decide what DCP path to pass to workers. Returns (path-or-None, info-msg)."""
    if args.dcp is not None:
        try:
            if args.dcp.suffix.lower() == ".npz":
                from lrt_cinema.dcp import load_profile
                load_profile(args.dcp)  # validate
            else:
                parse_dcp(args.dcp)  # validate
        except (FileNotFoundError, ValueError, struct.error) as exc:
            raise SystemExit(f"error: --dcp: malformed or unreadable: {exc}") from exc
        return args.dcp, f"info: using explicit DCP: {args.dcp}\n"

    info = read_raw_make_model(first_raw)
    if info is None:
        return None, (
            f"info: {first_raw.name} is not TIFF-shaped (e.g. Canon CR3); "
            f"DCP auto-detect skipped. Pass --dcp <path> to render with a profile.\n"
        )
    make, model = info
    found = auto_detect_profile(first_raw)
    if found is None:
        return None, (
            f"info: no DCP found for {make} {model}. Pass --dcp or populate "
            f"$LRT_CINEMA_PROFILES / ~/.config/lrt-cinema/profiles.\n"
        )
    _, src_path = found
    return src_path, f"info: auto-detected DCP: {src_path}\n"


def _output_stem(
    output_dir: Path, preset: str, frame_index: int, source_name: str,
) -> Path:
    """Destination path stem (no extension) for a rendered frame.

    The `lrtimelapse` target REQUIRES LRT's strict naming — `LRT_00001`,
    5-digit zero-padded, 1-based — for LRT to recognise the folder as a
    renderable intermediate sequence (LRT → Render from Intermediate). Other
    targets keep the source stem so frames map back to their RAW. Render the
    whole sequence (default range) so the LRT sequence starts at `LRT_00001`.
    """
    if preset == "lrtimelapse":
        return output_dir / f"LRT_{frame_index + 1:05d}"
    return output_dir / Path(source_name).stem


def _cmd_render(args: argparse.Namespace) -> int:
    if args.output.resolve() == args.input.resolve():
        sys.stderr.write(
            "error: --output must differ from --input.\n",
        )
        return 2

    try:
        seq = parse_sequence(args.input)
    except (FileNotFoundError, NotADirectoryError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2

    if seq.frame_count() == 0:
        sys.stderr.write(f"error: no RAW frames found under {args.input}\n")
        return 2

    if args.from_frame < 0 or args.from_frame >= seq.frame_count():
        sys.stderr.write(
            f"error: --from-frame {args.from_frame} outside [0, {seq.frame_count()})\n",
        )
        return 2
    if args.to_frame is not None and (
        args.to_frame <= args.from_frame or args.to_frame > seq.frame_count()
    ):
        sys.stderr.write(
            f"error: --to-frame {args.to_frame} must satisfy "
            f"{args.from_frame} < to-frame <= {seq.frame_count()}\n",
        )
        return 2
    to_frame = args.to_frame if args.to_frame is not None else seq.frame_count()

    dcp_path, info_msg = _resolve_dcp(args, args.input / seq.source_frames[0])
    sys.stderr.write(info_msg)
    if dcp_path is None and not args.dry_run:
        sys.stderr.write(
            "error: cannot render without a DCP profile. See above for "
            "auto-detect failure detail; pass --dcp <path> explicitly.\n",
        )
        return 2

    per_frame = materialize_all_frames(seq)
    if args.apply_lrt_offsets:
        per_frame = apply_lrt_mask_offsets(
            per_frame, seq, kinds=("hg", "deflicker", "global"),
        )
        per_frame = apply_deflicker(per_frame, seq)

    args.output.mkdir(parents=True, exist_ok=True)
    dng_cache_dir = args.output / ".dng-cache"

    jobs = []
    for i in range(args.from_frame, to_frame):
        src_raw = args.input / seq.source_frames[i]
        dst_stem = _output_stem(args.output, args.preset, i, seq.source_frames[i])
        jobs.append(_RenderJob(
            frame_index=i,
            src_raw=src_raw,
            dst_stem=dst_stem,
            ops=per_frame[i],
            dcp_path=dcp_path,
            preset=args.preset,
            no_dng_convert=args.no_dng_convert,
            dng_cache_dir=dng_cache_dir,
        ))

    if args.dry_run:
        sys.stderr.write(
            f"dry-run: would render {len(jobs)} frame(s) "
            f"[{args.from_frame}..{to_frame}) → {args.output} "
            f"(preset={args.preset}, workers={args.workers}).\n",
        )
        return 0

    return _run_jobs(jobs, args.workers, args.quiet)


def _run_jobs(jobs: list[_RenderJob], workers: int, quiet: bool) -> int:
    total = len(jobs)
    failures = 0

    if workers <= 1:
        for j in jobs:
            r = _render_one_frame(j)
            failures += _handle_result(r, total, quiet)
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_render_one_frame, j): j for j in jobs}
            for fut in as_completed(futures):
                r = fut.result()
                failures += _handle_result(r, total, quiet)

    if failures:
        sys.stderr.write(f"\n{failures} of {total} frames failed.\n")
        return 1
    return 0


def _handle_result(r: _RenderResult, total: int, quiet: bool) -> int:
    """Print per-frame progress; return 1 if failure else 0."""
    if not quiet:
        marker = "ok" if r.ok else "FAIL"
        sys.stdout.write(f"[{r.frame_index + 1}/{total}] {marker} {r.src_raw.name}\n")
        sys.stdout.flush()
    if not r.ok:
        sys.stderr.write(
            f"--- render error for frame {r.frame_index} "
            f"({r.src_raw.name}) ---\n{r.error}\n",
        )
        return 1
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "render":
        return _cmd_render(args)
    if args.command == "inspect":
        return _cmd_inspect(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
