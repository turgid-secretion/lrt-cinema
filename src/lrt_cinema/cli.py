"""Command-line interface for lrt-cinema."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lrt_cinema import __version__
from lrt_cinema.dcp import auto_detect_dcp, parse_dcp, read_raw_make_model
from lrt_cinema.interpolation import (
    apply_deflicker,
    apply_holy_grail_ramps,
    apply_lrt_mask_offsets,
    materialize_all_frames,
)
from lrt_cinema.ir import InterpolationMode
from lrt_cinema.presets import PRESETS, get_preset
from lrt_cinema.runner import DarktableCliNotFound, render_sequence
from lrt_cinema.xmp_parser import _is_identity_tone_curve, parse_sequence

# LR/LRT writes Sharpness=25 as the out-of-camera default into every XMP
# regardless of whether the user touched sharpening. Counting it as "intent"
# in the dropped-emit warning is a false positive — the user's sequence is
# entirely default-sharpness on every keyframe by definition.
_LR_SHARPNESS_DEFAULT = 25.0


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
                             "(degenerates to linear for 2-keyframe sequences). "
                             "NOT validated to match LRT's spline shape — for "
                             "LRT-fidelity, prefer 'linear' or run Auto Transition "
                             "in LRT first (we then exact-match LRT's per-frame values).")
    render.add_argument("--holy-grail", choices=("none", "apply-lrt-ramps"),
                        default="apply-lrt-ramps",
                        help="Synthetic-fixture Holy Grail ramp mode. 'apply-lrt-ramps' "
                             "overlays per-segment ramp deltas from the synthetic "
                             "<lrt:HolyGrailRamps> schema (used by tests). Real LRT "
                             "uses mask-correction per-frame deltas — see "
                             "--lrt-mask-offsets.")
    render.add_argument("--deflicker", choices=("none", "apply-lrt-offsets"),
                        default="apply-lrt-offsets",
                        help="Deflicker mode. 'apply-lrt-offsets' uses the per-frame "
                             "deltas LRT wrote into the XMPs (no measurement pass).")
    render.add_argument("--lrt-mask-offsets",
                        choices=("none", "hg", "deflicker", "global", "all"),
                        default="all",
                        help="Apply real-LRT mask-correction per-frame exposure deltas. "
                             "'all' = HG + Deflicker + Global (default). 'none' = ignore. "
                             "'hg' / 'deflicker' / 'global' = single source. See "
                             "docs/reference/lrtimelapse/XMP_SCHEMA.md for schema.")
    render.add_argument("--dcp", type=Path, default=None,
                        help="Path to an Adobe DNG Camera Profile (DCP) file. When "
                             "supplied, the emitter uses the DCP's bundled tone curve "
                             "and BaselineExposure to close the gap against LR's "
                             "render. If the LRT XMP carries an explicit kelvin "
                             "override, the DCP's color matrices are also used to "
                             "derive the temperature module's RGGB multipliers. "
                             "Without an explicit --dcp the renderer tries to "
                             "AUTO-DETECT a DCP by reading the source RAW's EXIF "
                             "Make/Model and looking up the matching "
                             "<Make> <Model> Camera Standard.dcp under the standard "
                             "Adobe DNG Converter install paths "
                             "(/Library/Application Support/Adobe/CameraRaw/"
                             "CameraProfiles/ on macOS; %%ProgramData%%\\Adobe\\"
                             "CameraRaw\\CameraProfiles\\ on Windows). Auto-detect "
                             "is suppressed by --no-auto-dcp. When no DCP is "
                             "supplied or detected, the renderer falls back to "
                             "darktable's libraw-derived defaults — the output will "
                             "diverge from LR's by the DCP-application gap (typically "
                             "ΔE2000 mean 5-10 on real footage).")
    render.add_argument("--no-auto-dcp", dest="auto_dcp",
                        action="store_false", default=True,
                        help="Suppress the auto-detect-DCP fallback when --dcp is not "
                             "supplied. Useful for reproducible 'no DCP' renders or "
                             "when the Adobe-bundled DCP for a camera is known to "
                             "produce a worse result than dt's libraw default.")
    render.add_argument("--no-dcp-tone-curve", dest="apply_dcp_tone_curve",
                        action="store_false", default=True,
                        help="When --dcp is supplied, suppress emission of the "
                             "DCP-bundled ProfileToneCurve into dt's basecurve "
                             "module. The cinema-linear preset's output stays "
                             "truly linear (consumable by ACES timelines / OCIO "
                             "chains that expect linear input); the trade-off is "
                             "the LR-look midtone lift is not applied. "
                             "BaselineExposure and (when explicit kelvin is set) "
                             "temperature multipliers from the DCP still emit.")
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


def _counts_as_intent(field: str, value) -> bool:
    """True if `value` for `field` represents user intent (vs an LR default).

    LR/LRT writes a small set of non-zero default values into every XMP
    regardless of whether the user touched the corresponding slider.
    Counting those as "intent" in the dropped-emit warning gives the
    misleading impression the renderer is dropping authored work when
    the keyframes are actually creatively neutral. Excluded defaults:

      * sharpness = 25       — LR's out-of-camera sharpness default
                               (validated against LRT Pro 7.5.3 output)

    Tone-curve identity is handled by the caller via the parser's
    `_is_identity_tone_curve` helper.
    """
    if field == "sharpness":
        return value not in (0.0, _LR_SHARPNESS_DEFAULT)
    return value != 0.0


def _emit_dropped_field_warnings(seq, stream) -> None:
    """Audit MEDIUM-6: render-time stderr warnings for parsed-but-dropped fields.

    Same data the inspect command surfaces, in compact one-line form,
    so users who skip `inspect` still see what their LRT keyframes
    intended but our pipeline doesn't propagate yet. Excludes LR-default
    values via `_counts_as_intent` so a sequence with no creative intent
    produces no warning (was a false positive on neutral LRT sequences).
    """
    if not seq.keyframes:
        return
    kf_count = len(seq.keyframes)
    dropped: list[str] = []
    for name in _DROPPED_AT_EMIT_FIELDS:
        count = sum(
            1 for kf in seq.keyframes
            if _counts_as_intent(name, getattr(kf.ops, name))
        )
        if count:
            dropped.append(f"{name} ({count}/{kf_count})")
    tc_count = sum(
        1 for kf in seq.keyframes
        if kf.ops.tone_curve and not _is_identity_tone_curve(kf.ops.tone_curve)
    )
    if tc_count:
        dropped.append(f"tone_curve ({tc_count}/{kf_count})")
    if any(kf.ops.tint is not None for kf in seq.keyframes):
        t_count = sum(1 for kf in seq.keyframes if kf.ops.tint is not None)
        dropped.append(f"tint ({t_count}/{kf_count})")
    if any(kf.ops.temperature_k is not None for kf in seq.keyframes):
        k_count = sum(1 for kf in seq.keyframes if kf.ops.temperature_k is not None)
        dropped.append(f"temperature_k ({k_count}/{kf_count})")
    if dropped:
        stream.write(
            f"warning: dropped at emit (calibration items, see SCOPE.md): "
            f"{', '.join(dropped)}\n"
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

    out.write(f"\nDeflicker offsets (synthetic schema): {len(seq.deflicker_offsets)}\n")
    if seq.deflicker_offsets:
        evs = [d.exposure_delta_ev for d in seq.deflicker_offsets]
        out.write(
            f"  range: {min(evs):+.3f} EV to {max(evs):+.3f} EV "
            f"(mean abs: {sum(abs(e) for e in evs) / len(evs):.3f} EV)\n"
        )

    # Real-LRT mask-correction per-frame deltas (HG/Deflicker/Global).
    # Audit HIGH-2 (2026-05-23) added this section.
    out.write(f"\nLRT mask-correction offsets (real LRT schema): {len(seq.lrt_mask_offsets)}\n")
    if seq.lrt_mask_offsets:
        by_kind: dict[str, list[float]] = {}
        for off in seq.lrt_mask_offsets:
            by_kind.setdefault(off.kind, []).append(off.exposure_delta_ev)
        for kind in ("hg", "deflicker", "global"):
            entries = by_kind.get(kind, [])
            if entries:
                out.write(
                    f"  {kind:9s}: {len(entries)} frame(s)  "
                    f"range {min(entries):+.3f} to {max(entries):+.3f} EV  "
                    f"(mean abs: {sum(abs(e) for e in entries) / len(entries):.3f} EV)\n"
                )
    else:
        out.write(
            "  (none. If your LRT sequence has run Visual Deflicker or Holy\n"
            "   Grail Wizard, non-zero per-frame deltas should appear here.\n"
            "   Zero-valued mask corrections are filtered at parse time.)\n"
        )

    if seq.keyframes:
        out.write("\nEmit warnings (fields parsed but dropped at darktable XMP emit):\n")
        any_warning = False
        for name in _DROPPED_AT_EMIT_FIELDS:
            count = sum(
                1 for kf in seq.keyframes
                if _counts_as_intent(name, getattr(kf.ops, name))
            )
            if count:
                out.write(
                    f"  - {name}: set on {count} of {kf_count} keyframes — "
                    f"DROPPED at emit (calibration item, see SCOPE.md)\n"
                )
                any_warning = True
        tone_curve_count = sum(
            1 for kf in seq.keyframes
            if kf.ops.tone_curve and not _is_identity_tone_curve(kf.ops.tone_curve)
        )
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

    dcp_profile = None
    dcp_path: Path | None = args.dcp
    if dcp_path is None and args.auto_dcp and seq.source_frames:
        first_raw = args.input / seq.source_frames[0]
        info = read_raw_make_model(first_raw)
        if info is not None:
            make, model = info
            detected = auto_detect_dcp(first_raw)
            if detected is not None:
                sys.stderr.write(
                    f"info: auto-detected DCP for {make} {model}: {detected}\n"
                )
                dcp_path = detected
            else:
                sys.stderr.write(
                    f"info: no DCP found for {make} {model} under standard Adobe "
                    f"install paths. Install Adobe DNG Converter for this camera, "
                    f"pass --dcp <path>, or pass --no-auto-dcp to suppress this "
                    f"message.\n"
                )
        else:
            sys.stderr.write(
                f"info: {first_raw.name} is not a TIFF-shaped RAW (Canon CR3 or "
                f"unknown format); auto-detect skipped. Pass --dcp <path> to use "
                f"a DCP-aware render.\n"
            )

    if dcp_path is not None:
        try:
            dcp_profile = parse_dcp(dcp_path)
        except (FileNotFoundError, ValueError) as exc:
            sys.stderr.write(f"error: --dcp: {exc}\n")
            return 2
        sys.stderr.write(
            f"info: loaded DCP {dcp_profile.profile_name!r} "
            f"(baseline_exposure={dcp_profile.baseline_exposure:+.2f} EV, "
            f"tone_curve_pts="
            f"{0 if dcp_profile.profile_tone_curve is None else dcp_profile.profile_tone_curve.shape[0]})"
            f"\n"
        )
    else:
        sys.stderr.write(
            "warning: no DCP supplied or detected; render will diverge from LR's "
            "by the DCP-application gap. See `lrt-cinema render --help`.\n"
        )

    # Audit MEDIUM-6: warn at render-time about parsed fields that don't
    # reach the rendered output. inspect already prints this; render
    # was silent before, causing surprise data loss for users who set
    # WB / contrast / tone-curve / etc. in LRT and didn't run inspect.
    _emit_dropped_field_warnings(seq, sys.stderr)

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
    # Pipeline ordering: keyframe-interpolated values are the base; then
    # overlay Holy Grail ramps (synthetic schema), then real-LRT
    # mask-correction per-frame deltas (HG / Deflicker / Global), then
    # synthetic-schema deflicker offsets. All four sources add
    # exposure_ev linearly.
    if args.holy_grail == "apply-lrt-ramps":
        per_frame = apply_holy_grail_ramps(per_frame, seq)
    if args.lrt_mask_offsets != "none":
        kinds = ("hg", "deflicker", "global") if args.lrt_mask_offsets == "all" \
            else (args.lrt_mask_offsets,)
        per_frame = apply_lrt_mask_offsets(per_frame, seq, kinds=kinds)
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
            dcp_profile=dcp_profile,
            apply_dcp_tone_curve=args.apply_dcp_tone_curve,
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
