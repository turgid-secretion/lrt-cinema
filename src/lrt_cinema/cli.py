"""Command-line interface for lrt-cinema.

Render path is the in-process Python Adobe DNG 1.7.1 pipeline
(`lrt_cinema.pipeline`); the dt-cli subprocess machinery is gone. See
`docs/archive/PIPELINE.md` for the as-built engine reference.

Flag surface:
  required:  --input  --output
  output:    --target {lrtimelapse,resolve,master}   (--preset overrides — advanced)
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
from lrt_cinema.ir import DevelopOps, RenderIntent
from lrt_cinema.presets import DEFAULT_PRESET, PRESETS, STAGE_7_PRESETS
from lrt_cinema.xmp_parser import parse_sequence

# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


# Friendly output knob: --target expands to a preset bundle. --preset is the
# advanced escape hatch that overrides it.
_TARGET_TO_PRESET = {
    "lrtimelapse": DEFAULT_PRESET,         # 16-bit sRGB display TIFF (LRT round-trip)
    "resolve": "cinema-linear-finished",   # ACEScg EXR master for Resolve/ACES
    "master": "cinema-linear-master",      # ACEScg EXR at Stage 7 (HDR headroom)
}

# Default render-intent per emission target (DECISIONS.md §7; trunk/branch model
# — docs/research/pipeline-overhaul-plan.md). The PERCEPTUAL (scene-referred)
# applicators — DR-compression's fixed scene-linear 0.18 anchor, OKLCh, CDL,
# Texture/Clarity, all "no top clamp" for the downstream RGC — are only coherent
# on **scene-linear** input. So PERCEPTUAL defaults ONLY on the tap-7 trunk master
# (`cinema-linear-master`), which is scene-linear (pre-LookTable/pre-ProfileToneCurve).
# `cinema-linear-finished` is **tap-9** (the full Adobe look already baked + clamped
# to display range), so it defaults to FAITHFUL — running scene-referred ops on
# tone-curved/clamped data is a domain mismatch (the audited "F2b" defect:
# docs/research/pipeline-order-audit.md §F2b). The sRGB TIFF (LRT round-trip) also
# defaults FAITHFUL (match the Lightroom look). `--render-intent` overrides either
# way (perceptual-on-finished is still reachable explicitly, with the caveat above).
_PERCEPTUAL_DEFAULT_PRESETS = frozenset({"cinema-linear-master"})


def _default_intent_for_preset(preset: str) -> RenderIntent:
    return (RenderIntent.PERCEPTUAL if preset in _PERCEPTUAL_DEFAULT_PRESETS
            else RenderIntent.FAITHFUL)


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
        "--target", default="lrtimelapse",
        choices=("lrtimelapse", "resolve", "master"),
        help=(
            "Output target (default: lrtimelapse). Expands to a preset: "
            "lrtimelapse → 16-bit sRGB display TIFF (LRT_NNNNN, embedded ICC) "
            "re-ingestible by LRT's video renderer for Motion Blur; "
            "resolve → half-float DWAB ACEScg EXR (scene-linear master for "
            "DaVinci Resolve / ACES, bypasses LRT); "
            "master → ACEScg EXR at Stage 7 for HDR headroom. "
            "Override with --preset."
        ),
    )
    render.add_argument(
        "--preset", default=None, choices=sorted(PRESETS),
        help=(
            "Advanced: explicit output preset, overrides --target. Choices: "
            "lrtimelapse, cinema-linear-finished, cinema-linear-master, "
            "stills-finished (deferred)."
        ),
    )
    render.add_argument(
        "--render-intent", dest="render_intent",
        default=None,
        choices=[i.value for i in RenderIntent],
        help=(
            "Stage-12 grading math (DECISIONS.md §7) — NOT a creative control; "
            "all creative values come from the XMP knobs. faithful → "
            "Adobe-matching math (the Lightroom look); perceptual → our modern "
            "math (OKLCh / ASC-CDL / local-tone DR-compression). DEFAULT is "
            "per-target: sRGB TIFF (lrtimelapse) → faithful; ACEScg EXR "
            "(cinema-linear-*) → perceptual. This flag overrides that default. "
            "The perceptual applicators (OKLCh HSL, ACEScct CDL, DR-compression, "
            "Texture/Clarity) are fully implemented; both intents are byte-exact "
            "at zero-slider identity."
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
        "--deflicker-scale", dest="deflicker_scale", type=float, default=1.0,
        help=(
            "Trim multiplier on the CORRECTED per-frame DEFLICKER correction "
            "(HG/Global untouched). The serialized LocalExposure2012 is EV/4; "
            "the renderer applies the calibrated 4x as a scene-referred gain "
            "(CALIBRATED 2026-06-10, k*=3.992+/-0.027, CLAIMS.md 'Exact "
            "mask-exposure factor'). Default 1.0 = the Lightroom-faithful "
            "calibrated application; 0.0 disables deflicker; values !=1.0 are "
            "an owner look-trim on top of the correct baseline."
        ),
    )
    render.add_argument(
        "--dcp", type=Path, default=None,
        help=(
            "Explicit DCP path. Accepts a `.dcp` (clean-room reader) or "
            "lrt-cinema's `.npz` extracted profile. Auto-detected from "
            "$LRT_CINEMA_PROFILES / ~/.config/lrt-cinema/profiles when not "
            "supplied."
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
        "--backend", default="auto", choices=("auto", "numpy", "numba", "mlx"),
        help=(
            "Per-pixel compute backend (default: auto). numpy = the reference "
            "(no extra dep, the ΔE-gate path); numba = fused multi-core CPU JIT "
            "kernels (DCP-render stages, colour-identical to numpy); mlx = the "
            "Apple-Silicon Metal GPU, which runs the WHOLE faithful sRGB-TIFF "
            "render incl. Stage-12 grade on-device (~2x identity / ~9x a heavily-"
            "graded full-res frame; mean ΔE2000 < 1e-4 vs numpy; faithful sRGB "
            "only — falls back to numba/numpy for EXR/perceptual/unsupported "
            "profiles); auto = numba if installed else numpy. With >1 CPU worker, "
            "intra-frame numba threads are capped to cores/workers."
        ),
    )
    render.add_argument(
        "--preview-scale", dest="preview_scale", type=int, default=1,
        choices=(1, 2, 4, 8),
        help=(
            "Render a low-resolution PREVIEW at ~1/N linear resolution for "
            "rapid grade/sequence iteration (default 1 = full delivery res). "
            "2/4/8 use a fast 2x2-bin demosaic + area downsample, so frames "
            "render up to ~30x faster. PREVIEW IS NOT COLOUR-EXACT and is "
            "exempt from the ΔE gate — for visual iteration, not the LRT "
            "round-trip / final delivery."
        ),
    )
    render.add_argument(
        "--no-dng-convert", dest="no_dng_convert",
        action="store_true", default=False,
        help=(
            "Skip the dnglab RAW→DNG step — read NEFs directly via libraw. "
            "Expect ~0.5 ΔE regression vs default (libraw lacks the DNG's "
            "embedded LinearizationTable + correct WhiteLevel). Use only when "
            "no dnglab binary is available."
        ),
    )
    render.add_argument(
        "--highlight-recovery", dest="highlight_recovery",
        action=argparse.BooleanOptionalAction, default=None,
        help=(
            "Tier-1 raw highlight reconstruction: recover blown highlights from "
            "surviving channels by cross-channel ratio propagation (camera space, "
            "pre-WB) so clipped highlights neutralise instead of casting warm. "
            "Default AUTO: ON for cinema-linear-master (the scene-linear tap-7 "
            "EXR, where the recovered over-white headroom survives) and OFF for "
            "tone-curve paths (sRGB / cinema-linear-finished), where Adobe's "
            "ProfileToneCurve clamps highlights to white and recovery is a no-op "
            "for non-trivial cost. Force with --highlight-recovery / "
            "--no-highlight-recovery (the latter also re-enables the MLX GPU "
            "fast path). See docs/archive/DECISIONS.md §'Highlight recovery'."
        ),
    )
    render.add_argument(
        "--fc-suppress", dest="fc_suppress", type=int, default=None,
        metavar="N",
        help=(
            "False-colour suppression passes (TARGET slot 6): the cross-engine "
            "canon scheme (darktable color_smoothing / dcraw -m class + "
            "RT-style chroma blur) — per pass, median the R-G and B-G chroma "
            "differences over 3x3, blur, add G back; G (detail) is never "
            "touched. Default AUTO (owner-approved 2026-06-12): 3 passes on "
            "display targets (visibly reduces residual edge artifacts; "
            "noisebars at the LR-product class), OFF on the scene-linear "
            "cinema-linear-master (the tap-7 EXR stays untouched for "
            "grading). Pass an explicit 0 to force off, 1-5 to force a "
            "pass count."
        ),
    )
    render.add_argument(
        "--master-look", dest="master_look", default="defer",
        choices=("bake", "defer"),
        help=(
            "Scene-linear MASTER (PERCEPTUAL tap-7 EXR) only: whether to bake the "
            "STATIC creative look (Stage-12 grade) into the master, or defer it to "
            "the colorist (trunk/branch model). 'defer' (default) = clean negative: "
            "bake ONLY the per-frame corrections (exposure/deflicker/Holy-Grail ramp, "
            "which have no transport across the NLE handoff) and leave the static look "
            "for Resolve. 'bake' = the full perceptual look baked in (the "
            "'better-primitives demo' master). No effect on the faithful sRGB/TIFF "
            "path (which always bakes the Lightroom look) or on a render with no "
            "Stage-12 ops (byte-identical either way). See "
            "docs/research/pipeline-overhaul-plan.md."
        ),
    )
    render.add_argument(
        "--demosaic", dest="demosaic", default="linear",
        choices=("linear", "menon", "rcd", "mlri", "dcb", "ahd", "dht", "vng", "ppg", "aahd"),
        help=(
            "Demosaic algorithm (default: linear). 'linear' = libraw bilinear, the "
            "byte-exact match to the dng_validate regression tripwire (LOW quality). "
            "'menon' = colour_demosaicing Menon2007/DDFAPD (BSD-3) — the RECOMMENDED "
            "quality/delivery demosaic: measured-best on the demosaic battery (CPSNR + "
            "lowest false-colour, ties on resolution; needs `pip install .[demosaic]`). "
            "'rcd' = our clean-room RCD-family (numba-fast, headroom-preserving, "
            "white-box) — sharp (ties Menon on MTF) but more chroma-aliasing; the fast/"
            "preview path. 'mlri' = clean-room MLRI (measured ≈ rcd; experiment). "
            "'dcb'/'ahd'/'dht' = libraw alternatives. Any non-'linear' choice changes "
            "output → validate vs the gym/rose gate + LRT-JPG before relying on it "
            "(also forces the CPU path off MLX; menon/rcd/mlri preserve highlight "
            "headroom for the master). Falls back to 'linear' if the choice is "
            "unavailable. Ignored under --preview-scale>1. See "
            "docs/research/{pipeline-overhaul-plan,demosaic-test-fixtures}.md."
        ),
    )
    render.add_argument(
        "--capture-sharpen", dest="capture_sharpen", default="off",
        choices=("off", "xmp", "acr"),
        help=(
            "FAITHFUL sRGB/TIFF path only: bake ACR-style capture sharpening (a "
            "clean-room luminance unsharp mask, develop_ops.apply_sharpness) to match "
            "the LRT JPG, which has ACR's default-on capture sharpening baked in. "
            "'off' (default) = no sharpening, BYTE-EXACT (the no-op the pipeline "
            "shipped). 'xmp' = apply the colorist's crs:Sharpness / crs:SharpenRadius. "
            "'acr' = ACR's raw defaults (Amount 40 / Radius 1.0) when the XMP carries "
            "no Amount, else the XMP's — reproduces the sharpening the LRT JPG bakes. "
            "NEVER applied to the perceptual master (it defers detail to the grade). "
            "A §9/§11 'deliberately exceed dng_validate' enhancement (no Lightroom-"
            "fidelity claim); validate vs the LRT-JPG north-star before relying on it. "
            "See docs/archive/DECISIONS.md §5 amendment."
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
    intent: RenderIntent = RenderIntent.FAITHFUL
    backend: str = "numpy"        # resolved concrete backend ("numpy"|"numba")
    threads_per_worker: int = 1   # numba intra-frame thread cap (oversubscription guard)
    preview_scale: int = 1        # 1 = full res; 2/4/8 = preview
    highlight_recovery: bool = True  # Tier-1 raw highlight reconstruction (pre-WB)
    master_look: str = "bake"     # "bake"|"defer" (perceptual master static-look gate)
    demosaic: str = "linear"      # libraw demosaic algorithm ("linear" = byte-exact)
    capture_sharpen: str = "off"  # "off"|"xmp"|"acr" (faithful capture sharpening; off=byte-exact)
    fc_suppress: int = 0          # slot-6 false-colour suppression passes (0 = off)


def resolve_fc_suppress(explicit: int | None, preset: str) -> int:
    """Slot-6 false-colour-suppression default resolution (owner-approved
    2026-06-12): AUTO = 3 passes on DISPLAY presets (tone-curved tap-9
    paths, where the owner verified the visible benefit on real frames),
    0 (off) on the scene-linear tap-7 master — the EXR carries untouched
    scene data; suppression belongs to the display render or the grade.
    An explicit --fc-suppress value (including 0) always wins."""
    if explicit is not None:
        return explicit
    return 0 if preset in STAGE_7_PRESETS else 3


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
        from lrt_cinema import accel
        from lrt_cinema.dng_convert import resolve_render_input

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
        provenance = {
            "source_frame": job.src_raw.name,
            "frame_index": job.frame_index,
            "preset": job.preset,
        }
        if job.preview_scale > 1:
            # Self-describe a preview so a downstream tool can't mistake it for
            # a colour-exact delivery frame.
            provenance["preview"] = True
            provenance["preview_scale"] = job.preview_scale

        # MLX (Metal GPU) fast path — the FAITHFUL sRGB-TIFF target only. One
        # upload / one download: the whole colour + Stage-12 grade + sRGB encode
        # runs on-device (accel.mlx_render_frame_to_srgb). For anything outside
        # that (non-FM profile, no ProfileToneCurve, EXR/perceptual) it raises
        # MlxUnsupported and we fall through to the CPU path.
        # Highlight recovery is a camera-space pre-stage the on-device MLX
        # renderer doesn't run, so recovery-on forces the CPU path (rather than
        # silently dropping the recovery). `--no-highlight-recovery` keeps MLX.
        if (job.backend == "mlx" and job.preset == "lrtimelapse"
                and job.intent is RenderIntent.FAITHFUL
                and job.demosaic == "linear"
                and job.capture_sharpen == "off"  # USM is a CPU-only Stage-12 op
                and not job.highlight_recovery
                and job.fc_suppress == 0):  # slot-6 median is CPU-only
            try:
                from lrt_cinema.output import write_tiff_display
                encoded = accel.mlx_render_frame_to_srgb(
                    dng_path, profile, develop_ops=job.ops,
                    dcp_path=job.dcp_path, preview_scale=job.preview_scale,
                )
                write_tiff_display(
                    encoded, job.dst_stem.with_suffix(".tif"),
                    colorspace="srgb", bit_depth=16,
                    provenance=provenance, pre_encoded=True,
                )
                return _RenderResult(job.frame_index, job.src_raw, ok=True)
            except accel.MlxUnsupported:
                pass  # fall through to the numpy/numba CPU path

        # CPU path. If MLX was requested but fell back, use numba (not the
        # per-stage 'mlx'→numpy fallback, which would be slow); else the
        # resolved backend. Cap numba threads to avoid pool oversubscription.
        os.environ["LRT_CINEMA_BACKEND"] = (
            ("numba" if accel.numba_available() else "numpy")
            if job.backend == "mlx" else job.backend
        )
        accel.set_threads(job.threads_per_worker)
        from lrt_cinema.develop_ops import apply_develop_ops
        from lrt_cinema.output import write_preset_output
        from lrt_cinema.pipeline import render_frame

        # cinema-linear-master skips DCP LookTable + ProfileToneCurve
        # (Stages 8 + 9) for HDR headroom. LR PV2012 ops (Stages 11+12)
        # still apply on top of the Stage 7 output. The same split decides
        # demosaic highlight policy: display targets clip-to-common-white
        # at the mosaic (owner default 2026-06-10 — clean neutral clipping
        # whether or not reconstruction is enabled); the scene-linear
        # master keeps headroom for grading + future reconstruction.
        stage7 = job.preset in STAGE_7_PRESETS
        result = render_frame(
            dng_path, profile, dcp_path=job.dcp_path, develop_ops=job.ops,
            stop_after_stage=(7 if stage7 else 9),
            preview_scale=job.preview_scale,
            highlight_recovery=job.highlight_recovery, demosaic=job.demosaic,
            demosaic_highlights=("headroom" if stage7 else "clip"),
            fc_suppress=job.fc_suppress,
        )
        with_dev_ops = apply_develop_ops(
            result.prophoto, job.ops, job.intent, master_look=job.master_look,
            capture_sharpen=job.capture_sharpen,
        )
        write_preset_output(
            with_dev_ops, job.dst_stem, job.preset, provenance=provenance,
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


# Fields dropped on the FAITHFUL path but honoured on PERCEPTUAL (DECISIONS §5/§7).
# Two families, each with its own perceptual op + warn wording — the DR-compression
# story (closed PV5 tone math) does NOT describe Texture/Clarity (edge-aware local
# contrast), so they must not share the same message.
_DROPPED_AT_EMIT_FIELDS = ("highlights", "shadows", "whites")  # → apply_dr_compression
_DROPPED_TEXTURE_CLARITY_FIELDS = ("texture", "clarity")       # → apply_texture_clarity
_ALL_DROPPED_ON_FAITHFUL = _DROPPED_AT_EMIT_FIELDS + _DROPPED_TEXTURE_CLARITY_FIELDS

# crs:* tag name per IR field (the suffix differs: Texture is PV-version-less, the
# rest are PV2012). Used for accurate warn/inspect lines.
_FIELD_TO_CRS_TAG = {
    "highlights": "Highlights2012", "shadows": "Shadows2012", "whites": "Whites2012",
    "texture": "Texture", "clarity": "Clarity2012",
}


def _warn_dropped_ops(
    per_frame: list[DevelopOps], intent: RenderIntent, capture_sharpen: str = "off",
) -> None:
    """Surface, at RENDER time, any develop op that is SET in the XMP but will
    NOT be applied — so a drop is never silent (DECISIONS.md §5/§7).

    Both the PV5 basic-tone ops (Highlights/Shadows/Whites) and Texture/Clarity are
    now **applied on the perceptual path** — the former by the scene-referred
    DR-compression op (`develop_ops.apply_dr_compression`), the latter by the
    edge-aware local-contrast op (`develop_ops.apply_texture_clarity`) — but remain
    **dropped on the faithful path** (Adobe's closed PV5 / Clarity math is
    un-fittable from the flat-patch harness, and a working-domain change there is
    forbidden by §7). So we warn ONLY under FAITHFUL; under PERCEPTUAL these knobs
    drive the ops and are not dropped. Emits one stderr line per set-but-dropped
    field, with the frame count and the perceptual op that honours it.
    """
    n = len(per_frame)

    # Capture sharpening (crs:Sharpness → develop_ops.apply_sharpness) is now
    # IMPLEMENTED (a clean-room luminance USM) but gated by --capture-sharpen,
    # FAITHFUL-only (the perceptual master defers detail to the grade). Surface its
    # state so neither the drop nor the bake is silent (DECISIONS §5 amendment / §9).
    sharp_count = sum(1 for ops in per_frame if getattr(ops, "sharpness", 0.0))
    if intent is RenderIntent.FAITHFUL and capture_sharpen != "off":
        sys.stderr.write(
            f"info: capture sharpening ON (--capture-sharpen {capture_sharpen}) — a "
            f"clean-room luminance USM baked on the faithful path to match the LRT "
            f"JPG's ACR sharpening (no Lightroom-fidelity claim; validate vs the "
            f"LRT-JPG north-star). 'acr' uses ACR's raw defaults (Amount 40 / Radius "
            f"1.0) when the XMP carries no Amount.\n",
        )
    elif sharp_count and intent is RenderIntent.FAITHFUL:
        sys.stderr.write(
            f"warning: crs:Sharpness set on {sharp_count}/{n} frame(s) but NOT applied "
            f"— capture sharpening is OFF by default (byte-exact). Pass --capture-"
            f"sharpen xmp (the colorist's values) or --capture-sharpen acr (ACR's "
            f"default capture sharpening) to bake it and match the sharper LRT JPG "
            f"(DECISIONS §5 amendment / §11).\n",
        )
    elif sharp_count:  # PERCEPTUAL master
        sys.stderr.write(
            f"warning: crs:Sharpness set on {sharp_count}/{n} frame(s) but NOT applied "
            f"on the perceptual master — it defers detail/sharpening to the colorist's "
            f"grade (trunk/branch model). Render the faithful sRGB/TIFF path with "
            f"--capture-sharpen to bake capture sharpening.\n",
        )

    if intent is not RenderIntent.FAITHFUL:
        return
    for field in _DROPPED_AT_EMIT_FIELDS:
        count = sum(1 for ops in per_frame if getattr(ops, field) != 0.0)
        if count:
            sys.stderr.write(
                f"warning: crs:{_FIELD_TO_CRS_TAG[field]} set on {count}/{n} frame(s) "
                f"but NOT applied under --render-intent faithful (Adobe's PV5 tone "
                f"math is closed-source; DECISIONS §5). The perceptual path honours "
                f"it via the scene-referred DR-compression op — render an ACEScg EXR "
                f"target or pass --render-intent perceptual (not Lightroom-faithful; "
                f"no fidelity claim). These frames will not match your Lightroom "
                f"preview.\n",
            )
    for field in _DROPPED_TEXTURE_CLARITY_FIELDS:
        count = sum(1 for ops in per_frame if getattr(ops, field) != 0.0)
        if count:
            sys.stderr.write(
                f"warning: crs:{_FIELD_TO_CRS_TAG[field]} set on {count}/{n} frame(s) "
                f"but NOT applied under --render-intent faithful (Adobe's edge-aware "
                f"{field.capitalize()} is closed-source; DECISIONS §7). The perceptual "
                f"path honours it via the local-contrast op (apply_texture_clarity, the "
                f"boost-detail mode of the shared base/detail engine) — render an ACEScg "
                f"EXR target or pass --render-intent perceptual (not Lightroom-faithful; "
                f"no fidelity claim). These frames will not match your Lightroom "
                f"preview.\n",
            )


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
            "\nFields DROPPED on the faithful path (closed-source Adobe math); "
            "APPLIED on perceptual (DECISIONS §5/§7 — Highlights/Shadows/Whites via "
            "DR-compression, Texture/Clarity via the local-contrast op):\n"
        )
        for name in _ALL_DROPPED_ON_FAITHFUL:
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
    try:
        found = auto_detect_profile(first_raw)
    except (FileNotFoundError, ValueError, struct.error, OSError) as exc:
        # A corrupt/unreadable profile in an auto-detect root must not crash the
        # CLI with a raw traceback (unlike --dcp, this path was unguarded).
        # Treat as "no usable profile found" and fall through to the clear error.
        return None, (
            f"info: auto-detect found a profile for {make} {model} but it was "
            f"malformed/unreadable ({type(exc).__name__}: {exc}). Pass --dcp <path>.\n"
        )
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
            deflicker_scale=args.deflicker_scale,
        )
        per_frame = apply_deflicker(per_frame, seq, scale=args.deflicker_scale)

    args.output.mkdir(parents=True, exist_ok=True)
    dng_cache_dir = args.output / ".dng-cache"

    # --preset (advanced) overrides --target's preset bundle.
    preset = args.preset or _TARGET_TO_PRESET[args.target]
    # Highlight recovery default is AUTO: it only changes output on the tap-7
    # (cinema-linear-master) path; every tap-9 path clamps it away at the Adobe
    # ProfileToneCurve, so default it on only where it pays. Explicit flag wins.
    highlight_recovery = (
        args.highlight_recovery if args.highlight_recovery is not None
        else (preset in STAGE_7_PRESETS)
    )
    fc_suppress = resolve_fc_suppress(args.fc_suppress, preset)
    # Intent default is per-target (sRGB→faithful, EXR→perceptual); --render-intent
    # overrides. All creative values still come from the XMP — intent only picks
    # the grading math (DECISIONS.md §7).
    intent = (RenderIntent(args.render_intent) if args.render_intent
              else _default_intent_for_preset(preset))

    # --master-look defers the static Stage-12 creative look ONLY on the
    # scene-linear PERCEPTUAL master (trunk/branch model). The faithful path always
    # bakes the Lightroom look (that IS its job), so it is forced to "bake".
    master_look = args.master_look if intent is RenderIntent.PERCEPTUAL else "bake"

    # --capture-sharpen bakes ACR-style capture sharpening on the FAITHFUL path
    # ONLY (it matches the LRT JPG's baked ACR sharpening); the perceptual master
    # defers detail to the grade, so it is forced "off". Default "off" = byte-exact.
    capture_sharpen = (args.capture_sharpen
                       if intent is RenderIntent.FAITHFUL else "off")

    _warn_dropped_ops(per_frame, intent, capture_sharpen)

    # Resolve the compute backend once (so all workers agree) and cap each
    # worker's intra-frame numba threads to cores/workers — N processes each
    # spinning all cores would thrash. workers==1 (latency/preview) keeps all
    # cores for the single frame.
    from lrt_cinema import accel
    backend = accel.resolve_backend(args.backend)
    cpu = os.cpu_count() or 2
    # numba (and an mlx→numba fallback) want cores/workers threads each; numpy
    # and the mlx GPU path don't use numba threads.
    threads_per_worker = (max(1, cpu // max(1, args.workers))
                          if backend in ("numba", "mlx") else 1)
    sys.stderr.write(
        f"info: compute backend = {backend}"
        + (f" (GPU; CPU fallback {threads_per_worker} thread(s)/worker)\n"
           if backend == "mlx"
           else f" ({threads_per_worker} thread(s)/worker)\n" if backend == "numba"
           else "\n"),
    )
    if args.preview_scale > 1:
        sys.stderr.write(
            f"info: PREVIEW mode — rendering at ~1/{args.preview_scale} resolution "
            f"(fast 2x2-bin demosaic + downsample). NOT colour-exact / not for "
            f"the LRT round-trip; for visual iteration only.\n",
        )

    jobs = []
    for i in range(args.from_frame, to_frame):
        src_raw = args.input / seq.source_frames[i]
        dst_stem = _output_stem(args.output, preset, i, seq.source_frames[i])
        jobs.append(_RenderJob(
            frame_index=i,
            src_raw=src_raw,
            dst_stem=dst_stem,
            ops=per_frame[i],
            dcp_path=dcp_path,
            preset=preset,
            no_dng_convert=args.no_dng_convert,
            dng_cache_dir=dng_cache_dir,
            intent=intent,
            backend=backend,
            threads_per_worker=threads_per_worker,
            preview_scale=args.preview_scale,
            highlight_recovery=highlight_recovery,
            master_look=master_look,
            demosaic=args.demosaic,
            capture_sharpen=capture_sharpen,
            fc_suppress=fc_suppress,
        ))

    if args.dry_run:
        sys.stderr.write(
            f"dry-run: would render {len(jobs)} frame(s) "
            f"[{args.from_frame}..{to_frame}) → {args.output} "
            f"(target={args.target}, preset={preset}, intent={intent.value}, "
            f"master_look={master_look}, demosaic={args.demosaic}, "
            f"capture_sharpen={capture_sharpen}, "
            f"backend={backend}, preview_scale={args.preview_scale}, "
            f"highlight_recovery={highlight_recovery}, "
            f"fc_suppress={fc_suppress}, "
            f"workers={args.workers}).\n",
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
                job = futures[fut]
                try:
                    r = fut.result()
                except Exception as exc:
                    # A worker that segfaults / OOMs raises BrokenProcessPool
                    # here; unguarded it escapes _run_jobs and aborts the whole
                    # batch, bypassing per-frame FAIL accounting. Count it as a
                    # failed frame and keep going — already-written frames survive.
                    r = _RenderResult(
                        job.frame_index, job.src_raw, ok=False,
                        error=f"{type(exc).__name__}: {exc}",
                    )
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
