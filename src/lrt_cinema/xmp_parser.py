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

import xml.etree.ElementTree as ET
from pathlib import Path

from defusedxml import ElementTree as DefusedET

from lrt_cinema.ir import DevelopOps, Keyframe, LRTSequence, TonePoint

NS = {
    "x": "adobe:ns:meta/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "crs": "http://ns.adobe.com/camera-raw-settings/1.0/",
    "lrt": "http://lrtimelapse.com/ns/1.0/",
}

LRT_NS_HINTS = {
    "keyframe_attr": "{http://lrtimelapse.com/ns/1.0/}keyframe",
    "deflicker_attr": "{http://lrtimelapse.com/ns/1.0/}deflickerExposure",
}


def _q(prefix: str, local: str) -> str:
    return f"{{{NS[prefix]}}}{local}"


def _parse_float(text: str | None, default: float = 0.0) -> float:
    if text is None:
        return default
    try:
        return float(text.strip().lstrip("+"))
    except ValueError:
        return default


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


def parse_xmp_file(path: Path) -> tuple[DevelopOps, bool, float | None]:
    """Parse one XMP file → (DevelopOps, is_keyframe, deflicker_delta_or_None).

    XMP allows multiple `rdf:Description` nodes; we merge them so intent
    split across nodes is not silently dropped. The is_keyframe flag is
    True when any Description carries LRT's keyframe marker (see
    LRT_NS_HINTS). deflicker_delta is taken from the first Description
    that supplies one, or None.

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
    deflicker_delta: float | None = None

    for desc in descriptions:
        ops = _merge_ops(ops, _parse_description(desc))

        kf_value = _read_attr_or_child(desc, LRT_NS_HINTS["keyframe_attr"])
        if kf_value is not None and kf_value.strip() in ("1", "true", "True"):
            is_keyframe = True

        if deflicker_delta is None:
            deflicker_value = _read_attr_or_child(desc, LRT_NS_HINTS["deflicker_attr"])
            if deflicker_value is not None:
                try:
                    deflicker_delta = float(deflicker_value)
                except ValueError:
                    deflicker_delta = None

    return ops, is_keyframe, deflicker_delta


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
            ops, is_kf, deflicker_delta = parse_xmp_file(xmp)
        except (ET.ParseError, ValueError):
            continue

        if is_kf or _has_meaningful_ops(ops):
            seq.keyframes.append(Keyframe(
                frame_index=idx, ops=ops, is_lrt_keyframe=is_kf,
            ))
        if deflicker_delta is not None:
            from lrt_cinema.ir import DeflickerOffset
            seq.deflicker_offsets.append(DeflickerOffset(
                frame_index=idx, exposure_delta_ev=deflicker_delta,
            ))

    return seq


def _has_meaningful_ops(ops: DevelopOps) -> bool:
    """True if the parsed DevelopOps carries any non-default value.

    Used to treat a per-frame XMP without an explicit LRT keyframe
    marker as still being a keyframe in the IR if it carries develop
    intent. This is conservative and matches the LR convention that
    *some* XMP datum is implicit-keyframe-of-intent.
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
        or ops.sharpness != 0.0
        or bool(ops.tone_curve)
    )
