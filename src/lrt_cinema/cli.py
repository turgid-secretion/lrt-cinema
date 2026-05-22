"""Command-line interface for lrt-cinema."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lrt_cinema import __version__
from lrt_cinema.interpolation import apply_deflicker, materialize_all_frames
from lrt_cinema.presets import PRESETS, get_preset
from lrt_cinema.runner import DarktableCliNotFound, render_sequence
from lrt_cinema.xmp_parser import parse_sequence


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lrt-cinema",
        description=(
            "Translate LRTimelapse XMP develop instructions into darktable "
            "history-stack XMP sidecars; render cinema-native intermediates "
            "via darktable-cli."
        ),
    )
    parser.add_argument("--version", action="version", version=f"lrt-cinema {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    render = sub.add_parser("render", help="Render an LRT sequence.")
    render.add_argument("--input", required=True, type=Path,
                        help="Folder containing source RAW frames + LRT XMP sidecars.")
    render.add_argument("--output", required=True, type=Path,
                        help="Folder to write rendered frames into.")
    render.add_argument("--preset", required=True, choices=sorted(PRESETS),
                        help="Output preset.")
    render.add_argument("--style", type=Path, default=None,
                        help="Optional custom darktable .style overriding the bundled preset style.")
    render.add_argument("--deflicker", choices=("none", "apply-lrt-offsets"),
                        default="apply-lrt-offsets",
                        help="Deflicker mode. 'apply-lrt-offsets' uses the per-frame "
                             "deltas LRT wrote into the XMPs (no measurement pass).")
    render.add_argument("--from-frame", type=int, default=0,
                        help="First frame index to render (inclusive).")
    render.add_argument("--to-frame", type=int, default=None,
                        help="Last frame index to render (exclusive). Default: end of sequence.")
    render.add_argument("--workers", type=int, default=1,
                        help="Parallel render workers. v0.1 supports 1 only.")
    render.add_argument("--dry-run", action="store_true",
                        help="Emit XMPs but skip the darktable-cli invocation.")
    render.add_argument("--quiet", action="store_true",
                        help="Suppress per-frame progress output.")

    return parser


def _emit_progress(idx: int, total: int, ok: bool, stream=sys.stdout) -> None:
    marker = "ok" if ok else "FAIL"
    stream.write(f"[{idx + 1}/{total}] {marker}\n")
    stream.flush()


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "render":
        return _cmd_render(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


def _cmd_render(args: argparse.Namespace) -> int:
    if args.workers != 1:
        sys.stderr.write(
            "warning: --workers > 1 is not yet implemented; falling back to 1.\n"
        )

    preset = get_preset(args.preset)

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
            f"error: --from-frame {args.from_frame} outside [0, {seq.frame_count()})\n"
        )
        return 2
    if args.to_frame is not None and (args.to_frame <= args.from_frame or args.to_frame > seq.frame_count()):
        sys.stderr.write(
            f"error: --to-frame {args.to_frame} must satisfy "
            f"{args.from_frame} < to-frame <= {seq.frame_count()}\n"
        )
        return 2

    per_frame = materialize_all_frames(seq)
    if args.deflicker == "apply-lrt-offsets":
        per_frame = apply_deflicker(per_frame, seq)

    try:
        results = render_sequence(
            source_dir=args.input,
            output_dir=args.output,
            per_frame_ops=per_frame,
            preset=preset,
            source_frames=seq.source_frames,
            from_frame=args.from_frame,
            to_frame=args.to_frame,
            custom_style=args.style,
            dry_run=args.dry_run,
        )
    except DarktableCliNotFound as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 3

    total = len(results)
    failures = 0
    for r in results:
        ok = r.skipped or r.returncode == 0
        if not ok:
            failures += 1
        if not args.quiet:
            _emit_progress(r.frame_index, total, ok)

    if failures:
        sys.stderr.write(f"\n{failures} of {total} frames failed.\n")
        return 1
    return 0
