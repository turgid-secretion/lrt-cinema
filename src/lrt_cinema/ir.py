"""Internal representation of LRT-driven develop intent.

Both the LRT XMP parser and the darktable XMP emitter operate on these
dataclasses, so the parser is decoupled from the renderer and so the
interpolation engine can produce per-frame `DevelopOps` from keyframed
inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class TonePoint:
    """One control point on a tone curve. x and y in 0.0–1.0 (parametric)."""

    x: float
    y: float


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

    # Sharpening
    sharpness: float = 0.0            # crs:Sharpness (0..150)

    # Tone curve (parametric control points, x and y in 0.0–1.0).
    # Empty list = no parametric tone curve override.
    tone_curve: list[TonePoint] = field(default_factory=list)

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
            sharpness=lerp_f(self.sharpness, other.sharpness),
            tone_curve=blended_curve,
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
