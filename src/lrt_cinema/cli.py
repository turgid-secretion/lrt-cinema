"""Command-line interface for lrt-cinema."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lrt_cinema import __version__
from lrt_cinema.interpolation import (
    apply_deflicker,
    apply_holy_grail_ramps,
    materialize_all_frames,
)
from lrt_cinema.ir import InterpolationMode
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

    inspect = sub.add_parser(
        "inspect",
        help=(
            "Parse an LRT folder and print what we saw — no rendering. "
            "Use this as a parser-validation pass before render."
        ),
    )
    inspect.add_argument("--input", required=True, type=Path,
                         help="Folder containing source RAW frames + LRT XMP sidecars.")
    inspect.add_argument("--show-fields", action="store_true",
                         help="Print every parsed crs field per keyframe (verbose).")

    render = sub.add_parser("render", help="Render an LRT sequence.")
    render.add_argument("--input", required=True, type=Path,
                        help="Folder containing source RAW frames + LRT XMP sidecars.")
    render.add_argument("--output", required=True, type=Path,
                        help="Folder to write rendered frames into.")
    render.add_argument("--preset", required=True, choices=sorted(PRESETS),
                        help="Output preset.")
    render.add_argument("--style", type=Path, default=None,
                        help="Optional custom darktable .style overriding the bundled preset style.")
    render.add_argument("--interpolation", choices=("linear", "smooth"),
                        default="linear",
                        help="Keyframe interpolation mode. 'smooth' uses uniform "
                             "Catmull-Rom with mirror-extrapolated phantom tangents "
                             "(degenerates to linear for 2-keyframe sequences).")
    render.add_argument("--holy-grail", choices=("none", "apply-lrt-ramps"),
                        default="apply-lrt-ramps",
                        help="Holy Grail exposure-ramp mode. 'apply-lrt-ramps' overlays "
                             "the per-segment ramp deltas LRT wrote into the XMPs.")
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
    if args.command == "inspect":
        return _cmd_inspect(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


_DROPPED_AT_EMIT_FIELDS = (
    "contrast", "highlights", "shadows", "whites", "blacks",
    "saturation", "vibrance", "sharpness",
)


def _cmd_inspect(args: argparse.Namespace) -> int:
    """Parse the input folder and print a human-readable diagnostic.

    Side-effect-free: does not write any files and does not invoke
    darktable. Intended to validate parser behavior against real LRT
    XMP before committing to a render — schemas like the LRT keyframe
    marker and the Holy Grail ramp container are calibration items
    (see SCOPE.md), so seeing what the parser actually extracted is
    the cheapest way to catch a schema drift.
    """
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
    if kf_count and lrt_flagged == 0:
        out.write(
            "  note: no authoritative keyframe markers found. All keyframes\n"
            "        were inferred from XMPs carrying non-default develop\n"
            "        intent. Either LRT is not flagging keyframes in this\n"
            "        sequence or the schema differs from what we expect\n"
            "        (xmp:Rating>=1 is real LRT's primary marker; see\n"
            "        SCOPE.md calibration items).\n"
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
                f"c={ops.contrast:+.0f} h={ops.highlights:+.0f} "
                f"s={ops.shadows:+.0f} w={ops.whites:+.0f} "
                f"b={ops.blacks:+.0f} sat={ops.saturation:+.0f} "
                f"vib={ops.vibrance:+.0f} sharp={ops.sharpness:.0f} "
                f"curve_pts={len(ops.tone_curve)}\n"
            )

    out.write(f"\nHoly Grail ramps: {len(seq.holy_grail_ramps)}\n")
    for r in seq.holy_grail_ramps:
        out.write(
            f"  [{r.start_frame}..{r.end_frame}] "
            f"{r.start_exposure_ev:+.2f} EV → {r.end_exposure_ev:+.2f} EV "
            f"smoothness={r.smoothness}\n"
        )
    if not seq.holy_grail_ramps:
        out.write(
            "  (none found. If you used LRT's Holy Grail workflow, the schema\n"
            "   may differ from our current guess of <lrt:HolyGrailRamps> —\n"
            "   see docs/VALIDATION.md and SCOPE.md calibration items.)\n"
        )

    out.write(f"\nDeflicker offsets: {len(seq.deflicker_offsets)}\n")
    if seq.deflicker_offsets:
        evs = [d.exposure_delta_ev for d in seq.deflicker_offsets]
        out.write(
            f"  range: {min(evs):+.3f} EV to {max(evs):+.3f} EV "
            f"(mean abs: {sum(abs(e) for e in evs) / len(evs):.3f} EV)\n"
        )

    if seq.keyframes:
        out.write("\nEmit warnings (fields parsed but dropped at darktable XMP emit):\n")
        any_warning = False
        for name in _DROPPED_AT_EMIT_FIELDS:
            count = sum(
                1 for kf in seq.keyframes
                if getattr(kf.ops, name) != 0.0
            )
            if count:
                out.write(
                    f"  - {name}: set on {count} of {kf_count} keyframes — "
                    f"DROPPED at emit (calibration item, see SCOPE.md)\n"
                )
                any_warning = True
        tone_curve_count = sum(1 for kf in seq.keyframes if kf.ops.tone_curve)
        if tone_curve_count:
            out.write(
                f"  - tone_curve: set on {tone_curve_count} of {kf_count} keyframes — "
                f"DROPPED at emit (calibration item)\n"
            )
            any_warning = True
        tint_count = sum(1 for kf in seq.keyframes if kf.ops.tint is not None)
        if tint_count:
            out.write(
                f"  - tint: set on {tint_count} of {kf_count} keyframes — "
                f"DROPPED at emit (depends on temperature calibration)\n"
            )
            any_warning = True
        temp_count = sum(1 for kf in seq.keyframes if kf.ops.temperature_k is not None)
        if temp_count:
            out.write(
                f"  - temperature_k: set on {temp_count} of {kf_count} keyframes — "
                f"currently NOT emitted (calibration item; darktable's "
                f"as-shot WB will be used instead)\n"
            )
            any_warning = True
        if not any_warning:
            out.write(
                "  (none. All parsed develop ops on the keyframes have a path\n"
                "   to the darktable XMP. Today that means exposure-only.)\n"
            )

    out.write(
        "\nWhat WILL reach the rendered output:\n"
        "  - Per-frame exposure_ev (linear + smooth interp + Holy Grail "
        "ramp delta + deflicker delta).\n"
        "  - darktable's default treatment for everything else "
        "(as-shot WB, no tone curve, no contrast / shadow / highlight, etc.).\n"
    )
    return 0


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

    seq.interpolation_mode = InterpolationMode(args.interpolation)

    per_frame = materialize_all_frames(seq)
    # Pipeline ordering: Holy Grail ramps are the base exposure intent
    # (overlay on top of keyframe-interpolated values), deflicker is a
    # per-frame correction applied on top of that intent. Apply ramps
    # first, deflicker second.
    if args.holy_grail == "apply-lrt-ramps":
        per_frame = apply_holy_grail_ramps(per_frame, seq)
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
        if not ok and r.error:
            # Surface darktable-cli's stderr so the user can see WHY a
            # frame failed without re-running by hand. Silent failures
            # are useless during the first real-footage tests.
            sys.stderr.write(
                f"--- darktable-cli stderr for frame {r.frame_index} "
                f"({r.source_path.name}) ---\n{r.error}\n"
            )

    if failures:
        sys.stderr.write(f"\n{failures} of {total} frames failed.\n")
        return 1
    return 0
