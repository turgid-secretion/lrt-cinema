"""Output preset definitions.

A `Preset` is a named bundle of darktable-cli invocation parameters
and a reference to a darktable `.style` file (bundled or user-supplied)
that does the heavy lifting for the preset's color treatment.

The three v0.1 presets:

  cinema-linear  — 16-bit TIFF, linear Rec.2020, display transform off.
                   Drop into Resolve, tag clip as Linear Rec.2020 input.
                   The default for cinema delivery.

  cinema-aces    — 32-bit float OpenEXR (PIZ), linear Rec.2020, display
                   transform off. Bundled OCIO config (see preset
                   ocio_config.ocio when wired) names the working space
                   so Resolve tags the clip in one click.

  stills-finished — 16-bit TIFF, Rec.2020 gamma, with darktable's AgX
                    display transform baked in. For users who want
                    finished delivery without downstream grading.

The `.style` file paths reference files under `presets/`. Until the
bundled styles ship (SCOPE.md item), the runner is expected to skip
the `--style` flag and rely on the per-frame XMP plus darktable's
defaults; the style references here are the schema callsite.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

OutputFormat = Literal["tiff", "exr"]


@dataclass(frozen=True)
class Preset:
    name: str
    output_format: OutputFormat
    bpp: int                       # bits per channel
    output_extension: str          # ".tif" or ".exr"
    output_color_profile: str      # darktable iccfile-style name
    style_filename: str | None     # filename under presets/ (or None)
    description: str


PRESETS: dict[str, Preset] = {
    "cinema-linear": Preset(
        name="cinema-linear",
        output_format="tiff",
        bpp=16,
        output_extension=".tif",
        output_color_profile="lin_rec2020",
        style_filename="cinema_linear.style",
        description=(
            "16-bit linear Rec.2020 TIFF. Display transform disabled. "
            "Resolve clip-tag as Linear Rec.2020 input. NOTE: when "
            "--dcp is supplied AND --no-dcp-tone-curve is NOT set, the "
            "DCP's bundled tone curve emits via basecurve, producing a "
            "tone-mapped (not strictly linear) output that visually "
            "matches LR. For truly-linear cinema-linear output suitable "
            "for ACES timelines or OCIO chains, pass --no-dcp-tone-curve."
        ),
    ),
    "cinema-aces": Preset(
        name="cinema-aces",
        output_format="exr",
        bpp=32,
        output_extension=".exr",
        output_color_profile="lin_rec2020",
        style_filename="cinema_aces.style",
        description=(
            "32-bit float OpenEXR (PIZ), linear Rec.2020. Display "
            "transform disabled. Bundled OCIO config tags the working "
            "space for ACES timelines."
        ),
    ),
    "stills-finished": Preset(
        name="stills-finished",
        output_format="tiff",
        bpp=16,
        output_extension=".tif",
        output_color_profile="rec2020",
        style_filename="stills_finished.style",
        description=(
            "16-bit Rec.2020 gamma TIFF with AgX display transform "
            "baked in. Finished delivery without further grading."
        ),
    ),
}


def get_preset(name: str) -> Preset:
    if name not in PRESETS:
        raise KeyError(
            f"Unknown preset {name!r}. Available: {sorted(PRESETS)}"
        )
    return PRESETS[name]
