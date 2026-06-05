"""Internal representation of LRT-driven develop intent.

Both the LRT XMP parser and the darktable XMP emitter operate on these
dataclasses, so the parser is decoupled from the renderer and so the
interpolation engine can produce per-frame `DevelopOps` from keyframed
inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum


class RenderIntent(str, Enum):
    """Grading applicator mode for the Stage-12 develop ops (DECISIONS.md §7).

    - **FAITHFUL** — today's Adobe-hexcone HSL + additive split-tone Color
      Grade. Reproduces the Lightroom look; feeds the **sRGB display TIFF**
      (the LRT round-trip). The default.
    - **PERCEPTUAL** — modern primitives (OKLCh HSL, ASC-CDL grade,
      local-Laplacian texture) for the **ACEScg EXR master**.

    The op IR (`HslBands`, `ColorGrade`) is shared across intents; only the
    *applicator* differs (`develop_ops.apply_stage_12_perceptual`). Until the
    perceptual primitives land (steps 2-4) PERCEPTUAL aliases FAITHFUL, so the
    switch is wired but byte-exact. `str, Enum` so the value ("faithful" /
    "perceptual") is directly usable as a CLI choice.
    """

    FAITHFUL = "faithful"
    PERCEPTUAL = "perceptual"


@dataclass(frozen=True)
class TonePoint:
    """One control point on a tone curve. x and y in 0.0–1.0 (parametric)."""

    x: float
    y: float


# The eight LR HSL/Color-panel hue bands, in the fixed Adobe order. Used as the
# canonical index order for every HslBands tuple and as the crs:* tag suffix.
HSL_BAND_NAMES = (
    "Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta",
)


@dataclass(frozen=True)
class HslBands:
    """LR HSL / Color panel: per-hue-band Hue / Saturation / Luminance.

    Eight hue bands (`HSL_BAND_NAMES` order), each adjustment −100..+100.
    Mirrors Camera Raw's ``crs:HueAdjustment{Band}`` /
    ``crs:SaturationAdjustment{Band}`` / ``crs:LuminanceAdjustment{Band}``
    (a PV2012-era field set, so it appears in real LRT-emitted XMPs).

    Each tuple is length 8; the default (all zeros) is the identity — see
    `is_identity`, which lets the renderer short-circuit to a byte-exact
    no-op so the ΔE ship gate is provably unaffected when no HSL is set.
    """

    hue: tuple[float, ...] = (0.0,) * 8
    saturation: tuple[float, ...] = (0.0,) * 8
    luminance: tuple[float, ...] = (0.0,) * 8

    def is_identity(self) -> bool:
        """True when every band of every channel is exactly zero (a no-op)."""
        return not (any(self.hue) or any(self.saturation) or any(self.luminance))

    def blend(self, other: HslBands, t: float) -> HslBands:
        """Per-band linear interpolation (t=0 → self, t=1 → other)."""
        def lerp_tup(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
            return tuple(x + (y - x) * t for x, y in zip(a, b, strict=True))

        return HslBands(
            hue=lerp_tup(self.hue, other.hue),
            saturation=lerp_tup(self.saturation, other.saturation),
            luminance=lerp_tup(self.luminance, other.luminance),
        )


@dataclass(frozen=True)
class ColorGrade:
    """LR Color Grading panel (PV4+ successor to Split Toning).

    Four wheels — Shadows, Midtones, Highlights, Global — each {Hue (0–360),
    Saturation (0–100), Luminance (−100..+100)} — plus `blending` (0–100,
    region overlap) and `balance` (−100..+100, shadow↔highlight pivot).

    Mirrors Camera Raw's ``crs:ColorGrade*`` tags. ACR aliases the
    shadow/highlight Hue+Sat and the Balance onto the legacy
    ``crs:SplitToning*`` tags for backward compat, so the parser reads both
    (a pure Split-Toning edit therefore drives the Shadow/Highlight wheels).

    `blending`/`balance` only shape *where* a tint lands; with every wheel's
    Saturation and Luminance at 0 there is no tint, so `is_identity` ignores
    them — letting the renderer short-circuit to a byte-exact no-op.
    """

    shadow_hue: float = 0.0
    shadow_sat: float = 0.0
    shadow_lum: float = 0.0
    midtone_hue: float = 0.0
    midtone_sat: float = 0.0
    midtone_lum: float = 0.0
    highlight_hue: float = 0.0
    highlight_sat: float = 0.0
    highlight_lum: float = 0.0
    global_hue: float = 0.0
    global_sat: float = 0.0
    global_lum: float = 0.0
    blending: float = 50.0
    balance: float = 0.0

    def is_identity(self) -> bool:
        """True when no wheel carries a tint (all Saturation + Luminance zero).

        Hue, blending and balance are inert without a non-zero Saturation or
        Luminance, so they do not count toward non-identity."""
        return not any((
            self.shadow_sat, self.shadow_lum,
            self.midtone_sat, self.midtone_lum,
            self.highlight_sat, self.highlight_lum,
            self.global_sat, self.global_lum,
        ))

    def blend(self, other: ColorGrade, t: float) -> ColorGrade:
        """Linearly interpolate all 14 fields (t=0 → self, t=1 → other).

        Hue fields lerp linearly like every other field (the project's uniform
        piecewise-linear interp policy). Known limit: across the 0/360 seam a
        sparse-keyframe hue ramp (e.g. 350→10) interpolates the long way round.
        In practice the dominant path is LRT Auto-Transition, which writes
        per-frame values → exact passthrough (no interpolation), so this only
        affects hand-set sparse keyframes that straddle the seam.
        """
        def lf(a: float, b: float) -> float:
            return a + (b - a) * t

        return ColorGrade(
            shadow_hue=lf(self.shadow_hue, other.shadow_hue),
            shadow_sat=lf(self.shadow_sat, other.shadow_sat),
            shadow_lum=lf(self.shadow_lum, other.shadow_lum),
            midtone_hue=lf(self.midtone_hue, other.midtone_hue),
            midtone_sat=lf(self.midtone_sat, other.midtone_sat),
            midtone_lum=lf(self.midtone_lum, other.midtone_lum),
            highlight_hue=lf(self.highlight_hue, other.highlight_hue),
            highlight_sat=lf(self.highlight_sat, other.highlight_sat),
            highlight_lum=lf(self.highlight_lum, other.highlight_lum),
            global_hue=lf(self.global_hue, other.global_hue),
            global_sat=lf(self.global_sat, other.global_sat),
            global_lum=lf(self.global_lum, other.global_lum),
            blending=lf(self.blending, other.blending),
            balance=lf(self.balance, other.balance),
        )


@dataclass
class DevelopOps:
    """Per-frame develop instructions.

    Field names mirror the canonical Camera Raw settings (the surface
    LRTimelapse writes) so the parser is a direct field copy and the
    emitter has unambiguous targets. Units match the Lightroom XMP
    convention; the emitter is responsible for any mapping to
    darktable's module-native units.
    """

    # Exposure / tone
    exposure_ev: float = 0.0          # crs:Exposure2012
    contrast: float = 0.0             # crs:Contrast2012 (-100..100)
    highlights: float = 0.0           # crs:Highlights2012 (-100..100)
    shadows: float = 0.0              # crs:Shadows2012 (-100..100)
    whites: float = 0.0               # crs:Whites2012 (-100..100)
    blacks: float = 0.0               # crs:Blacks2012 (-100..100)

    # White balance
    temperature_k: int | None = None  # crs:Temperature (Kelvin)
    tint: int | None = None           # crs:Tint (-150..150)

    # Saturation / vibrance
    saturation: float = 0.0           # crs:Saturation (-100..100)
    vibrance: float = 0.0             # crs:Vibrance (-100..100)

    # Texture / Clarity — local-contrast (edge-aware detail boost) sliders.
    # PERCEPTUAL-only (the boost-detail mode of the shared base/detail engine,
    # `develop_ops.apply_texture_clarity`); dropped + warn-only on the faithful
    # path (`cli._warn_dropped_ops`), like Highlights/Shadows/Whites. Default 0 =
    # the identity (byte-exact short-circuit). Texture is a fine-scale detail
    # boost; Clarity a larger-radius midtone-weighted local-contrast boost.
    texture: float = 0.0              # crs:Texture2012 (-100..100)
    clarity: float = 0.0              # crs:Clarity2012 (-100..100)

    # Sharpening — ACR/LR capture-sharpening Detail panel. Applied as a
    # clean-room luminance USM (`develop_ops.apply_sharpness`) on the FAITHFUL
    # path only, gated by the CLI `--capture-sharpen {off,xmp,acr}` flag (default
    # off → byte-exact). `sharpness` is the Amount (0 = no-op short-circuit);
    # `sharpen_radius` is the Gaussian radius. Defaults are ACR's raw defaults
    # (Amount 0 here so absent = identity; Radius 1.0). Detail/Masking are a
    # documented follow-up increment. See DECISIONS §5 amendment (citing §9/§11).
    sharpness: float = 0.0            # crs:Sharpness (0..150) — Amount
    sharpen_radius: float = 1.0       # crs:SharpenRadius (0.5..3.0)

    # Tone curve (parametric control points, x and y in 0.0–1.0).
    # Empty list = no parametric tone curve override.
    tone_curve: list[TonePoint] = field(default_factory=list)

    # HSL / Color panel — 8 hue bands × {Hue, Saturation, Luminance}.
    # Default (all-zero) is the identity. See HslBands.
    hsl: HslBands = field(default_factory=HslBands)

    # Color Grading wheels (Shadows/Midtones/Highlights/Global + Blending/
    # Balance). Default is the identity. See ColorGrade.
    color_grade: ColorGrade = field(default_factory=ColorGrade)

    def blend(self, other: DevelopOps, t: float) -> DevelopOps:
        """Linearly interpolate between self (t=0) and other (t=1).

        Used by the interpolation engine to produce per-frame ops
        between two keyframes. Scalar fields lerp; optional fields
        (WB) lerp only when both endpoints carry a value, else the
        non-None endpoint wins; tone curves lerp pointwise when they
        have matching cardinality, else self wins (correct behavior
        is policy-driven, not a math truth — caller should normalize
        tone curve point counts upstream if it cares).
        """
        if t <= 0.0:
            return replace(self)
        if t >= 1.0:
            return replace(other)

        def lerp_f(a: float, b: float) -> float:
            return a + (b - a) * t

        def lerp_opt_int(a: int | None, b: int | None) -> int | None:
            if a is None and b is None:
                return None
            if a is None:
                return b
            if b is None:
                return a
            return int(round(a + (b - a) * t))

        if (
            self.tone_curve
            and other.tone_curve
            and len(self.tone_curve) == len(other.tone_curve)
        ):
            blended_curve = [
                TonePoint(
                    x=lerp_f(p.x, q.x),
                    y=lerp_f(p.y, q.y),
                )
                for p, q in zip(self.tone_curve, other.tone_curve, strict=True)
            ]
        else:
            blended_curve = list(self.tone_curve)

        return DevelopOps(
            exposure_ev=lerp_f(self.exposure_ev, other.exposure_ev),
            contrast=lerp_f(self.contrast, other.contrast),
            highlights=lerp_f(self.highlights, other.highlights),
            shadows=lerp_f(self.shadows, other.shadows),
            whites=lerp_f(self.whites, other.whites),
            blacks=lerp_f(self.blacks, other.blacks),
            temperature_k=lerp_opt_int(self.temperature_k, other.temperature_k),
            tint=lerp_opt_int(self.tint, other.tint),
            saturation=lerp_f(self.saturation, other.saturation),
            vibrance=lerp_f(self.vibrance, other.vibrance),
            texture=lerp_f(self.texture, other.texture),
            clarity=lerp_f(self.clarity, other.clarity),
            sharpness=lerp_f(self.sharpness, other.sharpness),
            sharpen_radius=lerp_f(self.sharpen_radius, other.sharpen_radius),
            tone_curve=blended_curve,
            hsl=self.hsl.blend(other.hsl, t),
            color_grade=self.color_grade.blend(other.color_grade, t),
        )


@dataclass
class Keyframe:
    """A keyframe in an LRT sequence.

    `frame_index` is the zero-based index in the source frame sequence.
    `ops` is the develop intent at that frame.
    `is_lrt_keyframe` tracks whether LRT itself flagged this frame as a
    keyframe (vs being inferred from a per-frame XMP with no marker).
    """

    frame_index: int
    ops: DevelopOps
    is_lrt_keyframe: bool = False


@dataclass
class DeflickerOffset:
    """Per-frame exposure delta written by an LRT deflicker pass.

    LRT's deflicker analyses sequence luminance and writes per-frame
    exposure offsets that smooth out flicker. We carry these as a
    separate channel so the renderer can choose to apply them via
    darktable's exposure module without touching the keyframe-driven
    base exposure value.
    """

    frame_index: int
    exposure_delta_ev: float


@dataclass
class LRTMaskOffset:
    """A per-frame exposure delta from real LRT's mask-correction system.

    Real LRT 7.5.3 emits Holy Grail, Visual Deflicker, and Global per-frame
    deltas as named entries inside `crs:MaskGroupBasedCorrections` rather
    than as top-level `lrt:*` attributes (see
    docs/reference/lrtimelapse/XMP_SCHEMA.md and the ADVERSARIAL_AUDIT
    2026-05-23 HIGH-2 finding). Each correction carries:

      `crs:CorrectionName` — one of:
          "#LRT internal use (HG)"         → kind="hg"
          "#LRT internal use (Deflicker)"  → kind="deflicker"
          "#LRT internal use (Global)"     → kind="global"
      `crs:LocalExposure2012` — additive EV delta applied to that frame's
          base exposure.

    Parser stores only NON-ZERO values (zero is the default for an
    initialized-but-unused correction).
    """

    frame_index: int
    kind: str  # "hg" | "deflicker" | "global"
    exposure_delta_ev: float


@dataclass
class LRTSequence:
    """The complete intent extracted from an LRT-managed XMP sidecar set.

    `keyframes` defines the develop intent over time; `deflicker_offsets`
    is the (optional) per-frame exposure-delta channel. `source_frames`
    is the ordered list of source RAW filenames the keyframes apply to.
    """

    source_frames: list[str] = field(default_factory=list)
    keyframes: list[Keyframe] = field(default_factory=list)
    deflicker_offsets: list[DeflickerOffset] = field(default_factory=list)
    # Real-LRT per-frame mask-correction deltas (HG / Deflicker / Global).
    # Schema observed in LRT 7.5.3 sequence XMPs; see LRTMaskOffset.
    lrt_mask_offsets: list[LRTMaskOffset] = field(default_factory=list)

    def frame_count(self) -> int:
        return len(self.source_frames)

    def keyframe_indices(self) -> list[int]:
        return sorted({kf.frame_index for kf in self.keyframes})
