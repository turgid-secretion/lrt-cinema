"""Parse an LRTimelapse-written XMP sidecar into IR.

The LRT XMP format is Lightroom-shaped: an `x:xmpmeta` envelope
wrapping an `rdf:RDF` document with one or more `rdf:Description`
elements carrying Camera Raw Settings (`crs:` namespace) attributes
or child elements. LRT adds:

  - A per-frame keyframe marker (attribute on the rdf:Description).
  - A custom deflicker offset value when its deflicker pass has run.

This parser reads what it understands and ignores what it does not,
because LRT's XMP is a strict superset of Adobe's: encountering
unknown crs attributes is normal and not an error.

The exact namespace URI and attribute name for LRT's keyframe and
deflicker markers are calibrated against real LRT output during the
DP review loop. The values below are best-effort placeholders
captured in `LRT_NS_HINTS`; tests synthesize matching XMPs so the
parser surface is exercised end-to-end even before real-sample
calibration lands.
"""

from __future__ import annotations

import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from defusedxml import ElementTree as DefusedET

from lrt_cinema.ir import (
    DeflickerOffset,
    DevelopOps,
    Keyframe,
    LRTMaskOffset,
    LRTSequence,
    TonePoint,
)

# Real LRT 7.5.3 mask-correction CorrectionName → IR kind mapping.
# Source: docs/reference/lrtimelapse/XMP_SCHEMA.md and observed XMP at
# the user's Pro 7.5.3 sequence. Audit HIGH-2 (2026-05-23).
_LRT_MASK_CORRECTION_NAMES = {
    "#LRT internal use (HG)": "hg",
    "#LRT internal use (Deflicker)": "deflicker",
    "#LRT internal use (Global)": "global",
}

NS = {
    "x": "adobe:ns:meta/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "crs": "http://ns.adobe.com/camera-raw-settings/1.0/",
    "xmp": "http://ns.adobe.com/xap/1.0/",
    # Real LRT writes the bare base URI (no "ns/1.0/"). Validated against
    # LRTimelapse Pro 7.5.3 output. Our older synthetic fixtures use
    # http://lrtimelapse.com/ns/1.0/ — the keyframe-marker check has a
    # fallback path so both still work.
    "lrt": "http://lrtimelapse.com/",
    "lrt_synthetic": "http://lrtimelapse.com/ns/1.0/",
}

# The Adobe standard xmp:Rating attribute is what real LRT uses to mark
# keyframes (validated against LRT Pro 7.5.3). Rating>=1 means "some
# kind of LRT keyframe"; the most common value is 4 (Creative keyframe
# from the Keyframes Wizard). LRT also uses lower rating values for
# visual-drag markers etc., but treating all non-zero ratings as
# keyframes matches LRT's convention that 0-star = not-a-keyframe.
XMP_RATING_ATTR = "{http://ns.adobe.com/xap/1.0/}Rating"

LRT_NS_HINTS = {
    # Legacy synthetic-fixture attributes. Real LRT does NOT carry these
    # — it uses xmp:Rating for keyframes and crs:MaskGroupBasedCorrections
    # for per-frame deflicker / HG / global exposure deltas (parsed via
    # _parse_lrt_mask_offsets). The fallback paths here exist to keep
    # tests/fixtures/synthetic_*.xmp working without rewriting them.
    "keyframe_attr_synthetic": "{http://lrtimelapse.com/ns/1.0/}keyframe",
    "deflicker_attr_synthetic": "{http://lrtimelapse.com/ns/1.0/}deflickerExposure",
}


def _q(prefix: str, local: str) -> str:
    return f"{{{NS[prefix]}}}{local}"


def _parse_float(text: str | None, default: float = 0.0) -> float:
    if text is None:
        return default
    try:
        value = float(text.strip().lstrip("+"))
    except ValueError:
        return default
    # Hostile or corrupted XMPs may carry NaN / Inf. Allowing those through
    # propagates into struct.pack and dt renders the frame solid black with
    # no diagnostic. Treat as a parse failure (use the default).
    return value if math.isfinite(value) else default


def _parse_int(text: str | None) -> int | None:
    if text is None:
        return None
    cleaned = text.strip().lstrip("+")
    try:
        return int(cleaned)
    except ValueError:
        try:
            return int(round(float(cleaned)))
        except ValueError:
            return None


def _read_attr_or_child(elem: ET.Element, qname: str) -> str | None:
    """LR/Adobe XMP can serialize the same datum as an attribute OR a child element."""
    attr_value = elem.get(qname)
    if attr_value is not None:
        return attr_value
    child = elem.find(qname)
    if child is not None and child.text is not None:
        return child.text
    return None


def _parse_tone_curve(desc: ET.Element) -> list[TonePoint]:
    """crs:ToneCurvePV2012 is an rdf:Seq of 'x, y' strings."""
    curve_elem = desc.find(_q("crs", "ToneCurvePV2012"))
    if curve_elem is None:
        return []
    seq = curve_elem.find(_q("rdf", "Seq"))
    if seq is None:
        return []
    points: list[TonePoint] = []
    for li in seq.findall(_q("rdf", "li")):
        if li.text is None:
            continue
        parts = [p.strip() for p in li.text.split(",")]
        if len(parts) != 2:
            continue
        try:
            x = float(parts[0]) / 255.0
            y = float(parts[1]) / 255.0
        except ValueError:
            continue
        points.append(TonePoint(x=x, y=y))
    return points


def _parse_description(desc: ET.Element) -> DevelopOps:
    return DevelopOps(
        exposure_ev=_parse_float(_read_attr_or_child(desc, _q("crs", "Exposure2012"))),
        contrast=_parse_float(_read_attr_or_child(desc, _q("crs", "Contrast2012"))),
        highlights=_parse_float(_read_attr_or_child(desc, _q("crs", "Highlights2012"))),
        shadows=_parse_float(_read_attr_or_child(desc, _q("crs", "Shadows2012"))),
        whites=_parse_float(_read_attr_or_child(desc, _q("crs", "Whites2012"))),
        blacks=_parse_float(_read_attr_or_child(desc, _q("crs", "Blacks2012"))),
        temperature_k=_parse_int(_read_attr_or_child(desc, _q("crs", "Temperature"))),
        tint=_parse_int(_read_attr_or_child(desc, _q("crs", "Tint"))),
        saturation=_parse_float(_read_attr_or_child(desc, _q("crs", "Saturation"))),
        vibrance=_parse_float(_read_attr_or_child(desc, _q("crs", "Vibrance"))),
        sharpness=_parse_float(_read_attr_or_child(desc, _q("crs", "Sharpness"))),
        tone_curve=_parse_tone_curve(desc),
    )


def _merge_ops(base: DevelopOps, override: DevelopOps) -> DevelopOps:
    """Merge two DevelopOps: override wins on any field carrying non-default intent.

    XMP allows splitting the same datum across multiple `rdf:Description`
    nodes (exiftool round-trips often do this — different namespaces in
    separate Descriptions). We merge them rather than letting the first
    one win, which would otherwise silently drop intent.

    Audit MEDIUM-5 (2026-05-23): known limitation — this treats `0.0` as
    "no intent" for scalar float fields, so a later Description that
    explicitly sets a field BACK to 0.0 cannot override a non-zero earlier
    value. Real LRT writes one Description per frame XMP, so this is
    multi-Description-roundtrip-only and has not been observed in
    production. The principled fix is a per-field "explicitly-set"
    bitmask on DevelopOps; deferred until a real LRT or LR round-trip
    surfaces it as a problem.
    """
    from dataclasses import replace
    merged = replace(base)
    if override.exposure_ev != 0.0:
        merged.exposure_ev = override.exposure_ev
    if override.contrast != 0.0:
        merged.contrast = override.contrast
    if override.highlights != 0.0:
        merged.highlights = override.highlights
    if override.shadows != 0.0:
        merged.shadows = override.shadows
    if override.whites != 0.0:
        merged.whites = override.whites
    if override.blacks != 0.0:
        merged.blacks = override.blacks
    if override.temperature_k is not None:
        merged.temperature_k = override.temperature_k
    if override.tint is not None:
        merged.tint = override.tint
    if override.saturation != 0.0:
        merged.saturation = override.saturation
    if override.vibrance != 0.0:
        merged.vibrance = override.vibrance
    if override.sharpness != 0.0:
        merged.sharpness = override.sharpness
    if override.tone_curve:
        merged.tone_curve = list(override.tone_curve)
    return merged


def _parse_lrt_mask_offsets(desc: ET.Element) -> list[tuple[str, float]]:
    """Extract LRT mask-correction per-frame exposure deltas from one
    rdf:Description.

    Real LRT 7.5.3 schema (verified empirically + cross-referenced against
    docs/reference/lrtimelapse/XMP_SCHEMA.md):

        <crs:MaskGroupBasedCorrections>
          <rdf:Seq>
            <rdf:li>
              <rdf:Description
                crs:CorrectionName="#LRT internal use (HG)"
                crs:LocalExposure2012="0.123"
                crs:What="Correction" ...>
                <crs:CorrectionMasks>...</crs:CorrectionMasks>
              </rdf:Description>
            </rdf:li>
            ... (Deflicker, Global, plus user-set masks named
                 "LRT Mask 1" etc. which we ignore)
          </rdf:Seq>
        </crs:MaskGroupBasedCorrections>

    Returns list of (kind, exposure_delta_ev) tuples for non-zero LRT
    internal-use corrections only. Zero values are filtered — they
    represent initialized-but-unused corrections (e.g., user hasn't
    run Visual Deflicker yet).

    See ADVERSARIAL_AUDIT_2026-05-23 HIGH-2 for project context.
    """
    container = desc.find(_q("crs", "MaskGroupBasedCorrections"))
    if container is None:
        return []
    seq = container.find(_q("rdf", "Seq"))
    if seq is None:
        return []
    results: list[tuple[str, float]] = []
    for li in seq.findall(_q("rdf", "li")):
        inner = li.find(_q("rdf", "Description"))
        if inner is None:
            continue
        name = inner.get(_q("crs", "CorrectionName"))
        if name is None or name not in _LRT_MASK_CORRECTION_NAMES:
            continue
        kind = _LRT_MASK_CORRECTION_NAMES[name]
        ev_str = inner.get(_q("crs", "LocalExposure2012"))
        if ev_str is None:
            continue
        try:
            ev = float(ev_str)
        except ValueError:
            continue
        if ev == 0.0:
            continue  # filter initialized-but-unused corrections
        results.append((kind, ev))
    return results


def parse_xmp_file(
    path: Path,
) -> tuple[
    DevelopOps,
    bool,
    float | None,
    int | None,
    list[tuple[str, float]],
]:
    """Parse one XMP file → (DevelopOps, is_keyframe, deflicker_delta, rating, mask_offsets).

    XMP allows multiple `rdf:Description` nodes; we merge them so intent
    split across nodes is not silently dropped. The is_keyframe flag is
    primarily driven by `xmp:Rating` (real LRT convention, validated
    against Pro 7.5.3 output): rating>=1 = keyframe, rating==0 = not.
    When the rating attribute is absent we fall back to the synthetic
    `lrt:keyframe` attribute or, in `parse_sequence`, the
    `_has_meaningful_ops` heuristic. deflicker_delta is taken from the
    first Description that supplies one (synthetic schema only — real
    LRT uses mask corrections, returned as mask_offsets). rating is the
    maximum xmp:Rating value seen across all Descriptions, or None if
    absent. mask_offsets is the concatenation of every Description's
    `crs:MaskGroupBasedCorrections` entries that match real LRT's
    internal-use correction names ("#LRT internal use (HG/Deflicker/
    Global)"), returned as `(kind, exposure_delta_ev)` tuples — only
    non-zero values are returned. See ADVERSARIAL_AUDIT_2026-05-23
    HIGH-2.

    Uses defusedxml to harden against XXE / billion-laughs attacks on
    untrusted XMP input.
    """
    tree = DefusedET.parse(str(path))
    root = tree.getroot()

    rdf = root.find(_q("rdf", "RDF"))
    if rdf is None:
        raise ValueError(f"XMP missing rdf:RDF root: {path}")

    descriptions = rdf.findall(_q("rdf", "Description"))
    if not descriptions:
        raise ValueError(f"XMP missing rdf:Description: {path}")

    ops = DevelopOps()
    is_keyframe = False
    rating_seen: int | None = None
    deflicker_delta: float | None = None
    mask_offsets: list[tuple[str, float]] = []

    for desc in descriptions:
        ops = _merge_ops(ops, _parse_description(desc))

        # Primary keyframe signal — real LRT (validated against Pro 7.5.3)
        # writes xmp:Rating>=1 on keyframes, 0 on interpolated/normal frames.
        rating_str = _read_attr_or_child(desc, XMP_RATING_ATTR)
        if rating_str is not None:
            try:
                rating_int = int(rating_str.strip())
            except ValueError:
                rating_int = 0
            if rating_seen is None or rating_int > rating_seen:
                rating_seen = rating_int

        # Fallback — synthetic-fixture-style lrt:keyframe attribute.
        kf_value = _read_attr_or_child(
            desc, LRT_NS_HINTS["keyframe_attr_synthetic"],
        )
        if kf_value is not None and kf_value.strip() in ("1", "true", "True"):
            is_keyframe = True

        if deflicker_delta is None:
            deflicker_value = _read_attr_or_child(
                desc, LRT_NS_HINTS["deflicker_attr_synthetic"],
            )
            if deflicker_value is not None:
                try:
                    deflicker_delta = float(deflicker_value)
                except ValueError:
                    deflicker_delta = None

        mask_offsets.extend(_parse_lrt_mask_offsets(desc))

    # Rating, when present, is authoritative: rating>=1 = keyframe,
    # rating==0 = explicitly NOT a keyframe (overrides _has_meaningful_ops
    # fallback in parse_sequence). When rating is absent we trust the
    # synthetic lrt:keyframe path or the meaningful-ops heuristic.
    if rating_seen is not None:
        is_keyframe = rating_seen >= 1

    return ops, is_keyframe, deflicker_delta, rating_seen, mask_offsets


def parse_sequence(folder: Path, raw_extensions: tuple[str, ...] = (
    ".CR3", ".cr3", ".NEF", ".nef", ".ARW", ".arw",
    ".DNG", ".dng", ".RAF", ".raf", ".ORF", ".orf",
    ".RW2", ".rw2", ".FFF", ".fff",
)) -> LRTSequence:
    """Walk a folder of RAW frames + XMP sidecars and build an LRTSequence.

    Convention: every RAW frame `IMG_1234.CR3` has a sidecar
    `IMG_1234.CR3.xmp` (LRT's default) or `IMG_1234.xmp` (Lightroom's
    default). We check both. Frames without sidecars are still part of
    the source_frames list but contribute no keyframe.
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise NotADirectoryError(f"Not a directory: {folder}")

    raw_files = sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix in raw_extensions
    )

    seq = LRTSequence(source_frames=[p.name for p in raw_files])

    for idx, raw in enumerate(raw_files):
        xmp = raw.with_suffix(raw.suffix + ".xmp")
        if not xmp.exists():
            xmp = raw.with_suffix(".xmp")
        if not xmp.exists():
            continue
        try:
            ops, is_kf, deflicker_delta, rating, mask_offsets = (
                parse_xmp_file(xmp)
            )
        except (ET.ParseError, ValueError) as exc:
            # Silent skip-on-corruption is a data-loss path: a half-written
            # XMP (LRT crash, partial save) silently degrades the frame to
            # defaults and the render produces a flat frame mid-sequence.
            # Surface the skip so the user can investigate.
            sys.stderr.write(
                f"warning: skipping unreadable XMP {xmp.name}: {exc}\n"
            )
            continue

        # Keyframe gating policy:
        #   - is_kf reflects xmp:Rating>=1 (real LRT) or lrt:keyframe
        #     (synthetic fixtures). Authoritative when present.
        #   - _has_meaningful_ops fires when a frame carries non-default
        #     develop intent. After the LR-default fixes (sharpness=25,
        #     identity tone curve), this no longer over-fires on every
        #     LRT-written XMP and is safe to use as a non-gated fallback.
        #
        # Critically this means lrt-cinema HONORS LRT's Auto-Transition
        # output: after the user presses Auto Transition, LRT writes
        # interpolated values into every per-frame XMP. Each such frame
        # carries non-default crs:Exposure2012 and gets ingested as a
        # Keyframe in our IR. interpolate() then exact-matches and
        # returns LRT's value directly — no re-interpolation. Our own
        # linear/Catmull-Rom interp fires only for TRUE gaps (frames
        # with no per-frame intent), i.e. when the user skipped Auto
        # Transition. See docs/VALIDATION.md "LRT interpolation
        # passthrough model" for the architectural rationale.
        if is_kf or _has_meaningful_ops(ops):
            seq.keyframes.append(Keyframe(
                frame_index=idx, ops=ops, is_lrt_keyframe=is_kf,
            ))
        if deflicker_delta is not None:
            seq.deflicker_offsets.append(DeflickerOffset(
                frame_index=idx, exposure_delta_ev=deflicker_delta,
            ))

        # Real-LRT mask-correction per-frame deltas (HG/Deflicker/Global).
        # Each XMP carries at most one of each kind; we flatten across all
        # frames into seq.lrt_mask_offsets tagged with frame_index + kind.
        # See ADVERSARIAL_AUDIT_2026-05-23 HIGH-2.
        for kind, ev in mask_offsets:
            seq.lrt_mask_offsets.append(LRTMaskOffset(
                frame_index=idx, kind=kind, exposure_delta_ev=ev,
            ))

    return seq


def _is_identity_tone_curve(curve: list[TonePoint]) -> bool:
    """True for the LR/LRT default identity curve [(0,0), (1,1)].

    Real LRT writes this exact curve into every frame's XMP regardless
    of whether the user touched the tone curve; treating it as
    "meaningful intent" would falsely flag every frame as a keyframe.
    """
    if len(curve) != 2:
        return False
    return (
        curve[0].x == 0.0 and curve[0].y == 0.0
        and curve[1].x == 1.0 and curve[1].y == 1.0
    )


def _has_meaningful_ops(ops: DevelopOps) -> bool:
    """True if the parsed DevelopOps carries any non-default creative intent.

    Used ONLY when no xmp:Rating and no lrt:keyframe attribute are
    present (see parse_sequence) — both real-LRT and synthetic-fixture
    XMPs supply one of those, so this fallback is the third-tier
    safety net for XMPs that have neither.

    Excludes two LR/LRT defaults that would otherwise trigger false
    positives on every frame:
      - sharpness=25 (LR's out-of-camera default; we don't emit
        sharpness anyway, so it cannot carry intent for our pipeline)
      - identity tone curve [(0,0), (1,1)] (LR's default ToneCurvePV2012
        encoding written to every XMP regardless of user edits)
    """
    return (
        ops.exposure_ev != 0.0
        or ops.contrast != 0.0
        or ops.highlights != 0.0
        or ops.shadows != 0.0
        or ops.whites != 0.0
        or ops.blacks != 0.0
        or ops.temperature_k is not None
        or ops.tint is not None
        or ops.saturation != 0.0
        or ops.vibrance != 0.0
        or (bool(ops.tone_curve) and not _is_identity_tone_curve(ops.tone_curve))
    )
