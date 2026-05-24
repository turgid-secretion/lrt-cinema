"""Emit darktable XMP history-stack sidecars from IR.

darktable reads a `<RAW>.xmp` sidecar to apply per-image edits and
respects it when invoked headlessly via `darktable-cli`. The history
stack is an rdf:Seq under `darktable:history`, with each `rdf:li`
carrying:

  - `darktable:operation` — module name (e.g. "exposure", "temperature")
  - `darktable:enabled` — "1" or "0"
  - `darktable:modversion` — module schema version (per-module, per-dt-release)
  - `darktable:params` — base64-encoded C struct, layout governed by
    operation + modversion. THIS IS THE CALIBRATION GAP. Version-tolerant
    emission requires either:
      (a) a per-darktable-version params encoder generated from headers
      (b) round-tripping through `darktable-cli --bpp ... --style` with a
          bundled `.style` file that darktable's importer normalizes
  - `darktable:blendop_*` — blend-op metadata (default = no blend)
  - `darktable:multi_*` — instance disambiguation (default = singleton)
  - `darktable:num` — execution order

Scaffold approach (v0.1):

  Emit a well-formed XMP that lists the operations our preset needs,
  with conservative modversions known to be stable across darktable 4.6
  through 5.4. Params for `exposure` and `temperature` modules are
  encoded with their known simple layouts (float + zeros, kelvin int +
  fine-tune). Complex modules (sigmoid, color balance rgb, tone curve)
  are emitted as DISABLED placeholders; the bundled preset .style file
  carries their actual values and is applied via `darktable-cli --style`.

  This is the "scaffold ships the shape, calibration ships the bytes"
  split called out in SCOPE.md.
"""

from __future__ import annotations

import io
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

from lrt_cinema.dcp import DCPProfile, kelvin_tint_to_dt_multipliers
from lrt_cinema.ir import DevelopOps, TonePoint

DT_NS = "http://darktable.sf.net/"
X_NS = "adobe:ns:meta/"
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
XMP_NS = "http://ns.adobe.com/xap/1.0/"
XMPMM_NS = "http://ns.adobe.com/xap/1.0/mm/"

DT_XMP_VERSION = "5"
"""dt sidecar xmp_version. dt master writes "5" (src/common/exif.cc#L81 at SHA
9402c65275...); accepts 0..5, rejects >=6 (see docs/reference/darktable/
XMP_FORMAT.md). Our prior value "1" worked because dt's legacy reader accepts
lower versions, but is forward-incompatible with future bumps."""

DT_IOP_ORDER_VERSION = "4"
"""dt v50 (RAW) iop_order table identifier. Per src/common/iop_order.h#L128-140
at dt master: DT_IOP_ORDER_V50 = 4. Required by dt's XMP reader when
xmp_version is 4 or 5 (src/common/exif.cc#L4119-4134); if absent, dt falls
back to the LEGACY table — workable today but semantically wrong for sidecars
authored against dt 5.x module versions.

Pinning to V50 (not V50_JPG=5) because lrt-cinema is exclusively a RAW
workflow."""

DT_AUTO_PRESETS_APPLIED = "1"
"""Tells dt "I have already run the workflow/camera/lens auto-apply preset
injection — don't re-run it." Read at src/common/exif.cc#L4584-4591: if
absent, dt CLEARS the in-memory flag and `_dev_auto_apply_presets` runs full
body on every render (src/develop/develop.c#L1822-2106), which (a) is
non-deterministic across user dt installations with different workflow
settings, and (b) interacts with `--core --conf workflow=none` in
poorly-understood ways (see docs/research/DT_WORKFLOW_EXPOSURE_INTERACTION.md).

Setting this to "1" is the right behavior for a "render exactly what I asked,
no workflow injection" sidecar."""

EXPOSURE_MODVERSION = "7"
"""dt exposure module current modversion (src/iop/exposure.c#L47 in dt master).
Bumped from 6 to 7 in dt 5.x when `compensate_hilite_pres` gboolean was added
to the params struct. dt's legacy_params migration would handle v6→v7 on read,
but emitting the canonical current modversion is preferred."""

SHARPEN_MODVERSION = "1"
"""dt sharpen module modversion (src/iop/sharpen.c#L39 at dt SHA 9402c65275).
Three floats: radius, amount, threshold. dt's USM (unsharp mask). LR's
Sharpness slider (0..150, default 25) maps to dt sharpen.amount via a
linear scale picked so the defaults line up (LR 25 ↔ dt amount 0.5) and
LR's maxed-out 150 lands at dt amount 3.0 (clamped to dt's max 2.0). The
LR sub-knobs SharpenRadius / SharpenDetail / SharpenEdgeMasking are NOT
parsed by lrt-cinema today — we map only the master amount, matching
darktable's own lightroom.c which also drops the sub-knobs."""

TEMPERATURE_MODVERSION = "4"
"""dt temperature module current modversion (src/iop/temperature.c#L46 in dt
master at SHA 9402c65275). v4 added the `int preset` field to the params
struct (20 bytes total). dt's legacy_params chain handles older blobs on
read; we emit v4 directly."""

TONECURVE_MODVERSION = "5"
"""dt tonecurve module current modversion (src/iop/tonecurve.c#L72 in dt
master at SHA 9402c65275). v5 added the `preserve_colors` field for
RGB-norm-preserving curves."""

BASECURVE_MODVERSION = "6"
"""dt basecurve module current modversion (src/iop/basecurve.c#L57 in dt
master at SHA 9402c65275). basecurve is dt's designated module for
applying camera-baseline tone curves — the use case Adobe DCPs were
built for. Empirically reaches a lower post-fit ΔE residual than the
generic tonecurve module against the LRT preview reference: basecurve
2.25 vs tonecurve 3.19 on DSC_4053 (Nikon D750 + Camera Standard.dcp)."""

# Both tonecurve.c#L71 and basecurve.c#L56 cap their per-channel node count
# at MAXNODES=20. DCP-bundled curves commonly carry 128 points and must be
# downsampled before emission.
CURVE_MAXNODES = 20

# RGB norm enum (src/common/rgb_norms.h#L23-30): DT_RGB_NORM_MAX = 2.
# Empirically the closest match to Adobe DNG SDK's algorithm — Adobe
# applies the DCP ProfileToneCurve to the V (max) channel of
# HSV(linear ProPhoto) per DNG 1.7.1 §"Camera Profile Encoding."
# MAX is dt's equivalent norm (max(R,G,B) = V). Setting preserve_colors
# to MAX makes the curve operate on V and rescale RGB proportionally,
# replicating Adobe's chromaticity-preserving tone application.
#
# Empirical post-fit ΔE2000 by preserve_colors on basecurve emission
# (DSC_4053 vs LRT preview, no other gap-closers): NONE=2.30, LUM=2.28,
# MAX=2.25, AVG=2.38. MAX wins and matches the algorithmic intent.
_BASECURVE_PRESERVE_MAX = 2

# Curve interpolation type (src/common/curve_tools.h#L26-28):
# CUBIC_SPLINE=0, CATMULL_ROM=1, MONOTONE_HERMITE=2. We emit MONOTONE_HERMITE
# because (a) it is dt's own default for new curves and (b) it does not
# over/undershoot at sharp inflections — critical when emitting the steep
# highlight rolloff Adobe DCP tone curves carry near the top of the curve.
_CURVE_TYPE_MONOTONE_HERMITE = 2

# Tonecurve autoscale enum (src/iop/tonecurve.c#L88-106):
# DT_S_SCALE_AUTOMATIC_RGB = 3. Selecting this routes the L (index 0) curve
# to apply uniformly across R, G, B in the working color space. The a, b
# (index 1, 2) curves are ignored. This is the right mode for an LR-style
# RGB tone curve.
_TONECURVE_AUTOSCALE_RGB = 3

# RGB norm enum (src/common/rgb_norms.h#L23-30): DT_RGB_NORM_AVERAGE = 3.
# dt's own init() default; preserves perceived hue when the L curve lifts
# luminance.
_TONECURVE_PRESERVE_AVERAGE = 3

# Temperature preset enum (src/iop/temperature.c#L119-124):
# DT_IOP_TEMP_USER = 2. Tells dt's GUI "these multipliers came from a
# user/external setting" (vs as-shot or D65 defaults). The pipe ignores the
# preset value — it only matters for GUI display.
_TEMPERATURE_PRESET_USER = 2

# LR's out-of-camera default for Sharpness. Real LRT 7.5.3 writes this on
# every keyframe XMP regardless of user touch (validated against the user's
# production sequence DSC_*.xmp), so it cannot carry creative intent and
# emit-gating must treat it as "no intent." Public name (no leading
# underscore) so cli.py can import it for the dropped-warning consistency.
LR_SHARPNESS_DEFAULT = 25.0

# Blendop attrs (`darktable:blendop_version`, `darktable:blendop_params`)
# are intentionally NOT emitted. Per docs/research/ADVERSARIAL_AUDIT_2026-05-23.md
# HIGH-1, our prior values (`blendop_version="11"` + 64-byte zero blob) were
# rejected by dt 5.5's reader ("blendop v. 11: version WRONG params WRONG"
# in -d params log) and silently substituted with dt's default blendop
# (mask_mode=DEVELOP_MASK_DISABLED, opacity=100, blend=NORMAL2 → output
# passthrough). Empirically validated: omitting blendop attrs entirely takes
# the SAME dt code path with NO version-WRONG warning. Per-module blendop
# attrs are optional in the dt XMP spec — when absent, dt initializes from
# module->default_blendop_params, which is what we want for an "unblended"
# render. Re-introduce only when we need non-default blending semantics
# (e.g. masked exposure for a specific look).


# PV2012 Blacks2012 → dt exposure.black 5-point LUT.
#
# Verbatim from darktable's own LR-import mapping at
# src/develop/lightroom.c#L279-L285 (SHA 9402c65275). LR Blacks2012 is in
# 0..100 "Adobe perceptual units"; dt exposure.black is in linear-RGB
# displacement units. The LUT is dt's calibrated answer to "what dt black
# offset approximates LR Blacks2012=v". Values between breakpoints linearly
# interpolate. dt drops nothing else from PV2012 — this is the one slider
# they shipped a measured mapping for.
_LR2DT_BLACKS_TABLE: tuple[tuple[float, float], ...] = (
    (-100.0,  0.020),
    ( -50.0,  0.005),
    (   0.0,  0.000),
    (  50.0, -0.005),
    ( 100.0, -0.010),
)


def lr_blacks_to_dt_black(value: float) -> float:
    """Map an LR Blacks2012 value (-100..+100) to a dt exposure.black float.

    Linear interpolation between the 5 breakpoints in `_LR2DT_BLACKS_TABLE`.
    Out-of-range inputs clamp to the nearest endpoint (LR's slider clamps
    at the GUI; the parser allows over-range numerics so this guards).
    """
    if value <= _LR2DT_BLACKS_TABLE[0][0]:
        return _LR2DT_BLACKS_TABLE[0][1]
    if value >= _LR2DT_BLACKS_TABLE[-1][0]:
        return _LR2DT_BLACKS_TABLE[-1][1]
    for i in range(len(_LR2DT_BLACKS_TABLE) - 1):
        lr_lo, dt_lo = _LR2DT_BLACKS_TABLE[i]
        lr_hi, dt_hi = _LR2DT_BLACKS_TABLE[i + 1]
        if lr_lo <= value <= lr_hi:
            if lr_hi == lr_lo:
                return dt_lo
            t = (value - lr_lo) / (lr_hi - lr_lo)
            return dt_lo + t * (dt_hi - dt_lo)
    return 0.0  # unreachable — endpoints clamped above


def lr_sharpness_to_dt_amount(value: float) -> float:
    """Map LR Sharpness (0..150) to dt sharpen.amount (clamped to 0..2.0).

    Linear scale picked so defaults line up: LR 25 (LR's out-of-camera
    default) → dt amount 0.5 (dt's own default per src/iop/sharpen.c#L46
    at SHA 9402c65275). LR 100 → dt 2.0 (dt's max). Above LR 100 we clamp
    rather than overdrive — values that high are unusual in production
    timelapse XMP and dt's max amount of 2.0 is already an aggressive USM.
    """
    return max(0.0, min(2.0, float(value) / 50.0))


def _encode_exposure_params(exposure_ev: float, black: float = 0.0) -> str:
    """Encode darktable exposure module params (modversion 7) as HEX ASCII.

    Struct layout from src/iop/exposure.c#L66-75 at dt master SHA
    9402c65275... (DT_MODULE_INTROSPECTION(7, dt_iop_exposure_params_t)):

        dt_iop_exposure_mode_t mode      // enum = int32, default MANUAL=0
        float black                      // -1.0..1.0, default 0.0
        float exposure                   // -18.0..18.0 EV, default 0.0
        float deflicker_percentile       // 0..100, default 50.0
        float deflicker_target_level     // -18..18, default -4.0
        gboolean compensate_exposure_bias// = gint = int32, default FALSE=0
        gboolean compensate_hilite_pres  // ADDED in v7, default TRUE=1

    Total: 7 fields * 4 bytes = 28 bytes (no struct padding; all 4-aligned).

    The `black` field carries the lr2dt-mapped Blacks2012 displacement
    (see `lr_blacks_to_dt_black`), matching darktable's own LR-import
    behavior at src/develop/lightroom.c#L279-L285.

    ENCODING: hexadecimal ASCII (lowercase 0-9a-f), not base64. dt's XMP
    reader at src/common/exif.cc#L3252-3270 runs
    `strspn(input, "0123456789abcdef")` and rejects anything that fails.
    Base64 fails immediately because of `+`, `/`, `=` characters →
    dt silently substitutes `module->default_params` →
    pipe renders with dt's default exposure (0.7 in scene-referred
    workflows, 0.0 in workflow=none) regardless of what we wrote.
    This was a project-wide silent regression from the day-1 emitter;
    fixed 2026-05-23. See docs/research/DT_WORKFLOW_EXPOSURE_INTERACTION.md.
    """
    payload = struct.pack(
        "<iffffii",
        0,             # mode = manual
        float(black),
        float(exposure_ev),
        50.0,          # deflicker_percentile
        -4.0,          # deflicker_target_level
        0,             # compensate_exposure_bias = FALSE (default)
        1,             # compensate_hilite_pres = TRUE (default per v7)
    )
    return payload.hex()


def _encode_sharpen_params(
    amount: float,
    radius: float = 2.0,
    threshold: float = 0.5,
) -> str:
    """Encode darktable sharpen module params (modversion 1) as HEX ASCII.

    Struct layout from src/iop/sharpen.c#L43-L48 at dt SHA 9402c65275
    (DT_MODULE_INTROSPECTION(1, dt_iop_sharpen_params_t)):

        float radius;    // $DEFAULT: 2.0  ($MAX: 99.0)
        float amount;    // $DEFAULT: 0.5  ($MAX:  2.0)
        float threshold; // $DEFAULT: 0.5  ($MAX:100.0)

    Total: 12 bytes. Module operates in Lab colorspace
    (src/iop/sharpen.c#L83-L88 default_colorspace=IOP_CS_LAB) — sharpen
    runs post-display-transform regardless of the cinema-linear preset.
    That's consistent with LR's own pipeline position for the Sharpness
    slider; the cinema-linear preset's "truly linear" promise is about
    the OUTPUT color encoding, not the absence of any non-linear ops.
    """
    payload = struct.pack(
        "<fff",
        float(radius),
        float(amount),
        float(threshold),
    )
    assert len(payload) == 12, (
        f"sharpen params struct size mismatch: got {len(payload)}, expected 12"
    )
    return payload.hex()


def _encode_temperature_params(
    r_mul: float,
    g1_mul: float,
    b_mul: float,
    g2_mul: float,
    preset: int = _TEMPERATURE_PRESET_USER,
) -> str:
    """Encode darktable temperature module params (modversion 4) as HEX ASCII.

    Struct layout from src/iop/temperature.c#L76-82 at dt master SHA
    9402c65275 (DT_MODULE_INTROSPECTION(4, dt_iop_temperature_params_t)):

        float red;     // R multiplier
        float green;   // G1 multiplier (first green for Bayer)
        float blue;    // B multiplier
        float various; // G2 multiplier (second green for Bayer / X-Trans-G2)
        int preset;    // GUI preset enum; ignored by pipe

    Total: 20 bytes (4 floats + 1 int, no padding).

    For Bayer cameras (the consumer-stills universe lrt-cinema targets)
    G1 = G2. The multipliers are pre-demosaic per-channel scalers
    applied before colorin; combined with the appropriate camera color
    matrix in colorin, they reproduce the LR / Adobe DNG SDK
    white-balanced look. See `lrt_cinema.dcp.kelvin_tint_to_dt_multipliers`
    for the kelvin→multiplier math.
    """
    payload = struct.pack(
        "<ffffi", float(r_mul), float(g1_mul), float(b_mul), float(g2_mul),
        int(preset),
    )
    return payload.hex()


def _is_identity_tone_curve(points: list[TonePoint]) -> bool:
    """True for the LR/LRT default [(0,0), (1,1)] identity curve.

    Mirrors xmp_parser._is_identity_tone_curve; duplicated to avoid a
    parser↔emitter circular import.
    """
    if len(points) != 2:
        return False
    return (
        points[0].x == 0.0 and points[0].y == 0.0
        and points[1].x == 1.0 and points[1].y == 1.0
    )


def _resample_curve(points: list[TonePoint], n: int) -> list[TonePoint]:
    """Resample a tone curve to exactly `n` evenly-spaced control points.

    The DCP-bundled ProfileToneCurve commonly has 128 points; dt's
    tonecurve_params_t allocates room for up to 20. We resample the
    first/last points to (0,0) / (1,1) exactly and linearly interpolate
    the interior. The downsample is intentionally simple — the curve
    is already smooth, and MONOTONE_HERMITE interpolation in dt closes
    the gap between our 20 control points.
    """
    if n < 2:
        raise ValueError("resampled curve needs at least 2 points")
    if len(points) <= n:
        return list(points)
    xs = [p.x for p in points]
    ys = [p.y for p in points]
    out: list[TonePoint] = []
    for i in range(n):
        target_x = i / (n - 1)
        # Linear interpolation on the (x, y) sequence.
        j = 0
        while j < len(xs) - 1 and xs[j + 1] < target_x:
            j += 1
        if j >= len(xs) - 1:
            out.append(TonePoint(xs[-1], ys[-1]))
            continue
        x0, x1 = xs[j], xs[j + 1]
        y0, y1 = ys[j], ys[j + 1]
        if x1 == x0:
            out.append(TonePoint(target_x, y0))
            continue
        t = (target_x - x0) / (x1 - x0)
        out.append(TonePoint(target_x, y0 + t * (y1 - y0)))
    return out


def _encode_tonecurve_params(curve: list[TonePoint]) -> str:
    """Encode darktable tonecurve module params (modversion 5) as HEX ASCII.

    Struct layout from src/iop/tonecurve.c#L108-117 at dt master SHA
    9402c65275 (DT_MODULE_INTROSPECTION(5, dt_iop_tonecurve_params_t)):

        dt_iop_tonecurve_node_t tonecurve[3][20];  // 3 channels × 20 nodes
                                                   // × {float x, float y}
                                                   // = 480 bytes
        int tonecurve_nodes[3];                    // per-channel active node
                                                   // count (12 bytes)
        int tonecurve_type[3];                     // per-channel interp type
                                                   // (12 bytes)
        dt_iop_tonecurve_autoscale_t tonecurve_autoscale_ab;  // 4 bytes
        int tonecurve_preset;                      // 4 bytes
        int tonecurve_unbound_ab;                  // 4 bytes
        dt_iop_rgb_norms_t preserve_colors;        // 4 bytes

    Total: 520 bytes.

    Channels are L, a, b in dt's terminology. In AUTOMATIC_RGB autoscale
    mode (which we always set), the L curve (index 0) is applied
    uniformly across R, G, B in the working color space; the a and b
    curves are ignored. The L curve carries our LR / DCP-derived tone
    shape; a and b stay at dt's init default 3-point identity
    [(0,0), (0.5,0.5), (1,1)] which the AUTOMATIC_RGB path never
    samples but the struct still requires to be valid.

    The L curve is downsampled to <= 20 nodes (the struct's per-channel
    ceiling, src/iop/tonecurve.c#L71 `DT_IOP_TONECURVE_MAXNODES`).

    NOTE: used only when the user's LRT XMP carries a non-identity
    `crs:ToneCurvePV2012` (an explicit LR-authored RGB tone curve).
    DCP-bundled tone curves emit through `_encode_basecurve_params`
    instead — basecurve is the dt-native module for camera-baseline
    curves and reaches a lower structural ΔE residual against the LR
    reference (post-fit 2.25 vs 3.19 on the project's test sequence).
    """
    if not curve:
        raise ValueError("empty tone curve")

    l_curve = _resample_curve(curve, min(len(curve), CURVE_MAXNODES))
    n_l = len(l_curve)

    # a, b channels: dt's init() default 3-point identity (see
    # src/iop/tonecurve.c#L1571-1585: tonecurve_nodes[1]=tonecurve_nodes[2]=3
    # with nodes at (0,0), (0.5,0.5), (1,1)).
    identity_ab = [TonePoint(0.0, 0.0), TonePoint(0.5, 0.5), TonePoint(1.0, 1.0)]

    def _channel_nodes(pts: list[TonePoint]) -> list[float]:
        out: list[float] = []
        for p in pts:
            out.extend([float(p.x), float(p.y)])
        # Pad to MAXNODES * 2 floats with zeros — dt ignores entries past
        # tonecurve_nodes[i] so the padding values don't matter.
        while len(out) < CURVE_MAXNODES * 2:
            out.append(0.0)
        return out

    floats: list[float] = []
    floats.extend(_channel_nodes(l_curve))
    floats.extend(_channel_nodes(identity_ab))
    floats.extend(_channel_nodes(identity_ab))

    payload = struct.pack(
        f"<{CURVE_MAXNODES * 2 * 3}f",  # 3 channels × 20 nodes × 2 floats
        *floats,
    )
    # tonecurve_nodes[3]: active node count per channel.
    payload += struct.pack("<iii", n_l, 3, 3)
    # tonecurve_type[3]: interpolation type per channel.
    payload += struct.pack(
        "<iii",
        _CURVE_TYPE_MONOTONE_HERMITE,
        _CURVE_TYPE_MONOTONE_HERMITE,
        _CURVE_TYPE_MONOTONE_HERMITE,
    )
    # tonecurve_autoscale_ab: AUTOMATIC_RGB → apply L curve uniformly to RGB.
    payload += struct.pack("<i", _TONECURVE_AUTOSCALE_RGB)
    # tonecurve_preset: 0 = "Custom" (no built-in preset selected).
    payload += struct.pack("<i", 0)
    # tonecurve_unbound_ab: 1 = unbounded a, b in scene-referred input.
    # dt's init() default.
    payload += struct.pack("<i", 1)
    # preserve_colors: DT_RGB_NORM_AVERAGE — preserves perceived hue when
    # the L curve lifts luminance. dt's init() default.
    payload += struct.pack("<i", _TONECURVE_PRESERVE_AVERAGE)

    assert len(payload) == 520, (
        f"tonecurve params struct size mismatch: got {len(payload)}, "
        f"expected 520. dt's reader will silently substitute defaults — "
        f"see ADVERSARIAL_AUDIT_2026-05-23."
    )
    return payload.hex()


def _encode_basecurve_params(
    curve: list[TonePoint],
    preserve_colors: int = _BASECURVE_PRESERVE_MAX,
) -> str:
    """Encode darktable basecurve module params (modversion 6) as HEX ASCII.

    Struct layout from src/iop/basecurve.c#L63-76 at dt master SHA
    9402c65275 (DT_MODULE_INTROSPECTION(6, dt_iop_basecurve_params_t)):

        dt_iop_basecurve_node_t basecurve[3][20];  // 3 channels × 20 nodes
                                                   // × {float x, float y}
                                                   // = 480 bytes
        int basecurve_nodes[3];                    // per-channel active node
                                                   // count (12 bytes)
        int basecurve_type[3];                     // per-channel interp type
                                                   // (12 bytes)
        int exposure_fusion;                       // 4 bytes; 0 = single
                                                   // exposure (no HDR fusion)
        float exposure_stops;                      // 4 bytes; ignored when
                                                   // fusion = 0
        float exposure_bias;                       // 4 bytes; ignored when
                                                   // fusion = 0
        dt_iop_rgb_norms_t preserve_colors;        // 4 bytes

    Total: 520 bytes (same size as tonecurve v5 by coincidence —
    different field layout).

    basecurve is dt's designated camera-baseline-tone-curve module. Pipeline
    position 44.0 in V50 (src/common/iop_order.c#L467) — post-colorin,
    in the working color space (linear Rec.2020 for our preset), just
    before the display transform. This matches where Adobe applies the
    DCP ProfileToneCurve in its own pipeline.

    Only the first channel's curve is used by dt's basecurve (the other
    two are vestigial fields kept for struct-layout compatibility with
    tonecurve). We populate the first channel with the resampled DCP
    curve and leave channels 2/3 at 2-point identity.

    Defaults from src/iop/basecurve.c#L1628-1633:
        exposure_fusion=0, exposure_stops=1.0, exposure_bias=1.0,
        basecurve_nodes[0]=2 (a, b = 2 nodes identity).
    """
    if not curve:
        raise ValueError("empty tone curve")

    main_curve = _resample_curve(curve, min(len(curve), CURVE_MAXNODES))
    n_main = len(main_curve)

    # Channels 2 and 3: 2-point identity (basecurve uses only the first
    # channel; trailing channels are vestigial — dt's init() leaves them
    # at the default 2-point identity).
    identity_2 = [TonePoint(0.0, 0.0), TonePoint(1.0, 1.0)]

    def _channel_nodes(pts: list[TonePoint]) -> list[float]:
        out: list[float] = []
        for p in pts:
            out.extend([float(p.x), float(p.y)])
        while len(out) < CURVE_MAXNODES * 2:
            out.append(0.0)
        return out

    floats: list[float] = []
    floats.extend(_channel_nodes(main_curve))
    floats.extend(_channel_nodes(identity_2))
    floats.extend(_channel_nodes(identity_2))

    payload = struct.pack(f"<{CURVE_MAXNODES * 2 * 3}f", *floats)
    # basecurve_nodes[3]
    payload += struct.pack("<iii", n_main, 2, 2)
    # basecurve_type[3]
    payload += struct.pack(
        "<iii",
        _CURVE_TYPE_MONOTONE_HERMITE,
        _CURVE_TYPE_MONOTONE_HERMITE,
        _CURVE_TYPE_MONOTONE_HERMITE,
    )
    # exposure_fusion=0 (no fusion), exposure_stops + bias at defaults.
    payload += struct.pack("<iff", 0, 1.0, 1.0)
    # preserve_colors: DT_RGB_NORM_MAX (=2) matches Adobe's V-channel
    # tone-curve application. See _BASECURVE_PRESERVE_MAX docstring.
    payload += struct.pack("<i", preserve_colors)

    assert len(payload) == 520, (
        f"basecurve params struct size mismatch: got {len(payload)}, "
        f"expected 520. dt's reader will silently substitute defaults — "
        f"see ADVERSARIAL_AUDIT_2026-05-23."
    )
    return payload.hex()


def _make_history_entry(
    parent: ET.Element,
    num: int,
    operation: str,
    enabled: bool,
    modversion: str,
    params_b64: str,
) -> None:
    li = ET.SubElement(parent, f"{{{RDF_NS}}}li")
    li.set(f"{{{DT_NS}}}num", str(num))
    li.set(f"{{{DT_NS}}}operation", operation)
    li.set(f"{{{DT_NS}}}enabled", "1" if enabled else "0")
    li.set(f"{{{DT_NS}}}modversion", modversion)
    li.set(f"{{{DT_NS}}}params", params_b64)
    li.set(f"{{{DT_NS}}}multi_name", "")
    li.set(f"{{{DT_NS}}}multi_priority", "0")
    # blendop_* intentionally omitted — see module-level comment.


def emit_darktable_xmp(
    ops: DevelopOps,
    output_path: Path,
    dcp_profile: DCPProfile | None = None,
    apply_dcp_tone_curve: bool = True,
) -> None:
    """Emit a darktable XMP sidecar for `ops` to `output_path`.

    The sidecar is intended to live next to its RAW file
    (`<RAW>.xmp`) so `darktable-cli` picks it up automatically when
    processing that RAW.

    When `dcp_profile` is supplied, additional modules may be emitted
    to close the gap against LR's DCP-driven pipeline:

      * **exposure** picks up `dcp_profile.baseline_exposure` and
        `dcp_profile.baseline_exposure_offset` as an additive EV
        offset on top of `ops.exposure_ev`. Matches LR's behavior of
        applying these DCP-bundled bias terms before the user's
        Exposure2012 slider.
      * **temperature** is emitted with DCP-derived RGGB multipliers
        only when `ops.temperature_k` is set (explicit kelvin override).
        When the LRT XMP says `WhiteBalance="As Shot"`
        (ops.temperature_k is None), temperature is NOT emitted —
        darktable's libraw-derived as-shot multipliers stay in effect.
      * **basecurve** is emitted when `apply_dcp_tone_curve=True`
        (default) and the DCP carries a `ProfileToneCurve`. Closes the
        midtone-lift gap against LR's render: post-fit ΔE residual
        drops from baseline 2.49 to 2.25 on the project's test
        sequence. Setting `apply_dcp_tone_curve=False` preserves the
        "truly linear" cinema-linear contract for downstream consumers
        (ACES timelines, OCIO chains) that depend on linear input.
      * **tonecurve** is emitted when `ops.tone_curve` carries a
        non-identity LR curve (`crs:ToneCurvePV2012`). Overrides the
        DCP curve when both are present (LR's user-authored intent
        wins).
    """
    ET.register_namespace("x", X_NS)
    ET.register_namespace("rdf", RDF_NS)
    ET.register_namespace("darktable", DT_NS)
    ET.register_namespace("xmp", XMP_NS)
    ET.register_namespace("xmpMM", XMPMM_NS)

    root = ET.Element(f"{{{X_NS}}}xmpmeta", {f"{{{X_NS}}}xmptk": "lrt-cinema"})
    rdf = ET.SubElement(root, f"{{{RDF_NS}}}RDF")
    desc = ET.SubElement(rdf, f"{{{RDF_NS}}}Description", {f"{{{RDF_NS}}}about": ""})
    desc.set(f"{{{DT_NS}}}xmp_version", DT_XMP_VERSION)
    desc.set(f"{{{DT_NS}}}iop_order_version", DT_IOP_ORDER_VERSION)
    desc.set(f"{{{DT_NS}}}auto_presets_applied", DT_AUTO_PRESETS_APPLIED)

    history = ET.SubElement(desc, f"{{{DT_NS}}}history")
    seq = ET.SubElement(history, f"{{{RDF_NS}}}Seq")

    # Exposure picks up DCP baseline bias if a profile is supplied. LR
    # applies BaselineExposure + BaselineExposureOffset additively on
    # top of the user's Exposure2012 — same convention here.
    effective_exposure_ev = ops.exposure_ev
    if dcp_profile is not None:
        effective_exposure_ev += dcp_profile.baseline_exposure
        effective_exposure_ev += dcp_profile.baseline_exposure_offset

    # PV2012 Blacks2012 piggybacks on dt's exposure.black field via dt's
    # own lr2dt mapping (src/develop/lightroom.c#L279-L285). Verbatim port
    # — dt is the source of truth for "what dt black approximates LR
    # Blacks2012". When ops.blacks is 0 (LR default), this resolves to
    # black=0.0 and is a no-op.
    effective_black = lr_blacks_to_dt_black(ops.blacks)

    num = 0
    _make_history_entry(
        seq, num=num, operation="exposure", enabled=True,
        modversion=EXPOSURE_MODVERSION,
        params_b64=_encode_exposure_params(effective_exposure_ev, black=effective_black),
    )
    num += 1

    # Temperature module: only emit when the LRT XMP carries an explicit
    # kelvin override AND we have a DCP to derive multipliers from. When
    # WhiteBalance="As Shot" (ops.temperature_k is None), we leave dt's
    # libraw-derived AsShotWB in effect — it is close enough to LR's
    # AsShot path that the residual is below the headroom our other
    # gap-closers (tone curve, baseline exposure) provide.
    if dcp_profile is not None and ops.temperature_k is not None:
        r_mul, g1_mul, b_mul, g2_mul = kelvin_tint_to_dt_multipliers(
            dcp_profile,
            kelvin=float(ops.temperature_k),
            tint=float(ops.tint) if ops.tint is not None else 0.0,
        )
        _make_history_entry(
            seq, num=num, operation="temperature", enabled=True,
            modversion=TEMPERATURE_MODVERSION,
            params_b64=_encode_temperature_params(r_mul, g1_mul, b_mul, g2_mul),
        )
        num += 1

    # Curve-emission policy:
    #   1. The user's explicit non-identity LR ToneCurvePV2012 (RGB curve
    #      authored in LR/LRT) wins over any DCP-bundled curve — emit via
    #      the tonecurve module (AUTOMATIC_RGB autoscale, applies the L
    #      curve uniformly to R/G/B).
    #   2. Otherwise, if a DCP is supplied AND apply_dcp_tone_curve is True,
    #      emit the DCP's ProfileToneCurve via the basecurve module.
    #      basecurve is dt's designated camera-baseline-tone-curve module
    #      (pipeline position 44.0 — post-colorin, working color space) and
    #      reaches a lower structural ΔE residual than tonecurve for this
    #      use case (see _BASECURVE_PRESERVE_MAX docstring + commit msg).
    #   3. If the caller explicitly set apply_dcp_tone_curve=False, emit no
    #      curve at all from the DCP — preserves a truly-linear render path
    #      for downstream ACES / OCIO consumers.
    if ops.tone_curve and not _is_identity_tone_curve(ops.tone_curve):
        _make_history_entry(
            seq, num=num, operation="tonecurve", enabled=True,
            modversion=TONECURVE_MODVERSION,
            params_b64=_encode_tonecurve_params(ops.tone_curve),
        )
        num += 1
    elif (
        dcp_profile is not None
        and dcp_profile.profile_tone_curve is not None
        and apply_dcp_tone_curve
    ):
        dcp_curve = [
            TonePoint(float(x), float(y))
            for x, y in dcp_profile.profile_tone_curve
        ]
        _make_history_entry(
            seq, num=num, operation="basecurve", enabled=True,
            modversion=BASECURVE_MODVERSION,
            params_b64=_encode_basecurve_params(dcp_curve),
        )
        num += 1

    # Sharpen — emit only when ops.sharpness carries authored intent.
    # Skip the LR out-of-camera default (Sharpness=25, which the dropped-
    # warning helper also ignores) AND the explicit-zero case (LR's "no
    # sharpening"); both should leave dt's pipeline unsharpened. Real
    # creative intent on this slider is rare in production timelapse XMP,
    # so the per-frame cost of the emit gate is acceptable.
    if ops.sharpness not in (0.0, LR_SHARPNESS_DEFAULT):
        _make_history_entry(
            seq, num=num, operation="sharpen", enabled=True,
            modversion=SHARPEN_MODVERSION,
            params_b64=_encode_sharpen_params(
                amount=lr_sharpness_to_dt_amount(ops.sharpness),
            ),
        )
        num += 1

    desc.set(f"{{{DT_NS}}}history_end", str(num))

    tree = ET.ElementTree(root)
    ET.indent(tree, space=" ", level=0)
    # darktable parses sidecars via Exiv2, which requires the standard
    # XMP packet wrapper ("<?xpacket ...?>") around the rdf:RDF document
    # to recognize it as a valid XMP. Without it, dt logs the misleading
    # "can't open XMP file" and the sidecar is ignored. The W5M0Mp...
    # id and "begin" BOM-byte are the canonical Adobe XMP packet markers
    # (see ISO 16684-1 §6.1, "XMP Packet Wrapper"). The trailing "w" in
    # the end marker indicates a writable packet (vs "r" read-only).
    _XMP_PACKET_BEGIN = b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    _XMP_PACKET_END = b'\n<?xpacket end="w"?>\n'
    buf = io.BytesIO()
    tree.write(buf, xml_declaration=False, encoding="utf-8")
    with open(output_path, "wb") as f:
        f.write(_XMP_PACKET_BEGIN)
        f.write(buf.getvalue())
        f.write(_XMP_PACKET_END)
