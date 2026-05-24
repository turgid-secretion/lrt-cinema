"""darktable XMP emitter tests."""

import struct
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from lrt_cinema.dcp import DCPProfile
from lrt_cinema.ir import DevelopOps, TonePoint
from lrt_cinema.xmp_emitter import (
    DT_NS,
    RDF_NS,
    emit_darktable_xmp,
    lr_blacks_to_dt_black,
    lr_sharpness_to_dt_amount,
)


def _parse(path):
    return ET.parse(path).getroot()


def test_emitter_writes_well_formed_xml(tmp_path):
    out = tmp_path / "frame.xmp"
    emit_darktable_xmp(DevelopOps(exposure_ev=1.0), out)
    root = _parse(out)
    assert root.tag.endswith("xmpmeta")


def test_emitter_wraps_in_xpacket_for_exiv2_compat(tmp_path):
    # darktable's XMP reader (Exiv2) requires the standard XMP packet
    # wrapper. Without "<?xpacket begin=...?>" / "<?xpacket end=...?>"
    # dt rejects the sidecar with the misleading "can't open XMP file"
    # error. The W5M0Mp... id is the canonical Adobe packet marker.
    out = tmp_path / "frame.xmp"
    emit_darktable_xmp(DevelopOps(exposure_ev=0.5), out)
    raw = out.read_bytes()
    assert raw.startswith(b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>')
    assert raw.rstrip().endswith(b'<?xpacket end="w"?>')


def test_emitter_writes_required_dt5_description_attrs(tmp_path):
    # dt master xmp_version=5 reader requires four attributes on the
    # rdf:Description element to treat the sidecar as fully-specified
    # (src/common/exif.cc#L4065 xmp_version, L4094 auto_presets_applied,
    # L4119-4134 iop_order_version, history_end at L5191-5202 writer).
    # Absent auto_presets_applied, dt re-runs its workflow auto-apply
    # machinery on every render (develop.c#L1822-2106), making output
    # non-deterministic against the runtime workflow conf. See
    # docs/research/DT_WORKFLOW_EXPOSURE_INTERACTION.md.
    out = tmp_path / "frame.xmp"
    emit_darktable_xmp(DevelopOps(exposure_ev=0.0), out)
    root = _parse(out)
    desc = next(root.iter(f"{{{RDF_NS}}}Description"))
    assert desc.get(f"{{{DT_NS}}}xmp_version") == "5"
    assert desc.get(f"{{{DT_NS}}}iop_order_version") == "4"  # V50 RAW
    assert desc.get(f"{{{DT_NS}}}auto_presets_applied") == "1"
    assert desc.get(f"{{{DT_NS}}}history_end") == "1"


def test_emitter_includes_exposure_operation(tmp_path):
    out = tmp_path / "frame.xmp"
    emit_darktable_xmp(DevelopOps(exposure_ev=1.25), out)
    root = _parse(out)
    operations = [
        li.get(f"{{{DT_NS}}}operation")
        for li in root.iter(f"{{{RDF_NS}}}li")
    ]
    assert "exposure" in operations


def test_emitter_does_not_emit_blendop_attrs(tmp_path):
    # ADVERSARIAL_AUDIT_2026-05-23 HIGH-1: emitter's prior
    # blendop_version="11" + 64-byte zero blendop_params was rejected by dt
    # 5.5's reader (logged "blendop v. 11: version WRONG params WRONG") and
    # silently substituted with module->default_blendop_params. We now omit
    # blendop_* attrs entirely, which takes the same dt code path with no
    # version-WRONG warning. Guard against regression.
    out = tmp_path / "frame.xmp"
    emit_darktable_xmp(DevelopOps(exposure_ev=1.0), out)
    root = _parse(out)
    for li in root.iter(f"{{{RDF_NS}}}li"):
        assert li.get(f"{{{DT_NS}}}blendop_version") is None, (
            "blendop_version must not be emitted — see audit HIGH-1"
        )
        assert li.get(f"{{{DT_NS}}}blendop_params") is None, (
            "blendop_params must not be emitted — see audit HIGH-1"
        )


def test_emitter_does_not_emit_temperature_module_without_dcp(tmp_path):
    # Without a DCP profile, we cannot derive correct RGGB multipliers
    # from kelvin alone (DCP color matrix is camera-specific). We skip
    # temperature emission and let darktable's libraw-derived as-shot
    # multipliers apply. This is the documented fallback when the user
    # runs without --dcp.
    for kelvin in (None, 5500):
        out = tmp_path / f"frame_{kelvin}.xmp"
        emit_darktable_xmp(DevelopOps(temperature_k=kelvin), out)
        root = _parse(out)
        operations = [
            li.get(f"{{{DT_NS}}}operation")
            for li in root.iter(f"{{{RDF_NS}}}li")
        ]
        assert "temperature" not in operations


def _make_test_dcp() -> DCPProfile:
    # Plausible-shaped synthetic Nikon-style matrices for emitter tests.
    return DCPProfile(
        color_matrix_1=np.array([
            [1.0, -0.4,  0.0],
            [-0.5, 1.3,  0.3],
            [-0.1,  0.2, 0.8],
        ]),
        color_matrix_2=np.array([
            [0.9, -0.3, -0.1],
            [-0.5, 1.3,  0.2],
            [-0.1,  0.2, 0.7],
        ]),
        kelvin_1=2856.0,
        kelvin_2=6504.0,
        baseline_exposure=0.0,
        baseline_exposure_offset=0.0,
    )


def test_emitter_emits_temperature_module_when_dcp_and_kelvin_present(tmp_path):
    # With both a DCP profile and an explicit kelvin override, the
    # emitter writes a temperature history entry with DCP-derived
    # multipliers. This is the "user explicitly set WB in LRT" code
    # path.
    out = tmp_path / "frame.xmp"
    emit_darktable_xmp(
        DevelopOps(exposure_ev=0.0, temperature_k=5500, tint=0),
        out,
        dcp_profile=_make_test_dcp(),
    )
    root = _parse(out)
    found = False
    for li in root.iter(f"{{{RDF_NS}}}li"):
        if li.get(f"{{{DT_NS}}}operation") == "temperature":
            found = True
            assert li.get(f"{{{DT_NS}}}modversion") == "4"
            params_hex = li.get(f"{{{DT_NS}}}params")
            assert all(c in "0123456789abcdef" for c in params_hex)
            decoded = bytes.fromhex(params_hex)
            # 4 floats + 1 int = 20 bytes (src/iop/temperature.c#L76-82 v4).
            assert len(decoded) == 20, f"expected 20-byte v4 struct, got {len(decoded)}"
            r, g1, b, g2, preset = struct.unpack("<ffffi", decoded)
            # G1 == G2 for Bayer convention.
            assert g1 == g2
            # Green normalized to 1.
            assert g1 == 1.0
            # R and B in sane camera-multiplier range.
            assert 0.1 < r < 10.0
            assert 0.1 < b < 10.0
            assert preset == 2  # DT_IOP_TEMP_USER
    assert found, "temperature module not emitted despite DCP + kelvin set"


def test_emitter_does_not_emit_temperature_module_when_kelvin_unset(tmp_path):
    # Even with a DCP profile, if the LRT XMP says WhiteBalance="As Shot"
    # (no kelvin override → ops.temperature_k is None), we leave dt's
    # as-shot multipliers in effect. This matches LR's behavior under
    # "As Shot".
    out = tmp_path / "frame.xmp"
    emit_darktable_xmp(
        DevelopOps(exposure_ev=0.0, temperature_k=None),
        out,
        dcp_profile=_make_test_dcp(),
    )
    root = _parse(out)
    operations = [
        li.get(f"{{{DT_NS}}}operation")
        for li in root.iter(f"{{{RDF_NS}}}li")
    ]
    assert "temperature" not in operations


def test_emitter_emits_basecurve_from_dcp_profile_curve(tmp_path):
    # DCP carries a ProfileToneCurve (the bundled Adobe "Camera Standard"
    # tone shape). When the user's XMP has the identity LR curve, we emit
    # the DCP curve via the basecurve module — dt's designated camera-
    # baseline-tone-curve module (post-colorin, preserve_colors=MAX matches
    # Adobe's V-channel application).
    dcp = _make_test_dcp()
    xs = np.linspace(0, 1, 32)
    ys = xs ** 0.5
    dcp.profile_tone_curve = np.stack([xs, ys], axis=1)

    out = tmp_path / "frame.xmp"
    emit_darktable_xmp(
        DevelopOps(exposure_ev=0.0),  # no LR curve
        out,
        dcp_profile=dcp,
    )
    root = _parse(out)
    found = False
    for li in root.iter(f"{{{RDF_NS}}}li"):
        if li.get(f"{{{DT_NS}}}operation") == "basecurve":
            found = True
            assert li.get(f"{{{DT_NS}}}modversion") == "6"
            params_hex = li.get(f"{{{DT_NS}}}params")
            assert all(c in "0123456789abcdef" for c in params_hex)
            decoded = bytes.fromhex(params_hex)
            # basecurve_params_t (v6) = 520 bytes
            # (src/iop/basecurve.c#L63-76 + L56).
            assert len(decoded) == 520, (
                f"expected 520-byte v6 struct, got {len(decoded)}"
            )
            # basecurve_nodes[0] should be <= MAXNODES=20 (we resampled).
            nodes = struct.unpack("<iii", decoded[480:480 + 12])
            assert 2 <= nodes[0] <= 20
            assert nodes[1] == 2  # vestigial channels = 2-point identity
            assert nodes[2] == 2
            # exposure_fusion=0 (single exposure), at offset 480+12+12=504
            ef = struct.unpack("<i", decoded[504:508])[0]
            assert ef == 0
            # preserve_colors at offset 504+4+4+4=516 — DT_RGB_NORM_MAX=2.
            pc = struct.unpack("<i", decoded[516:520])[0]
            assert pc == 2, f"preserve_colors should be MAX (=2), got {pc}"
    assert found, "basecurve module not emitted from DCP profile curve"


def test_emitter_skips_dcp_tone_curve_when_disabled(tmp_path):
    # apply_dcp_tone_curve=False preserves the truly-linear cinema-linear
    # contract: DCP supplied (for matrix/exposure) but no curve emitted.
    dcp = _make_test_dcp()
    dcp.profile_tone_curve = np.stack(
        [np.linspace(0, 1, 32), np.linspace(0, 1, 32) ** 0.5], axis=1,
    )
    out = tmp_path / "frame.xmp"
    emit_darktable_xmp(
        DevelopOps(exposure_ev=0.0),
        out,
        dcp_profile=dcp,
        apply_dcp_tone_curve=False,
    )
    root = _parse(out)
    operations = [
        li.get(f"{{{DT_NS}}}operation")
        for li in root.iter(f"{{{RDF_NS}}}li")
    ]
    assert "basecurve" not in operations
    assert "tonecurve" not in operations


def test_emitter_prefers_lr_curve_over_dcp_curve(tmp_path):
    # When the user's LR XMP carries a non-identity tone curve AND
    # the DCP has a ProfileToneCurve, the user's curve wins.
    dcp = _make_test_dcp()
    dcp.profile_tone_curve = np.array([[0.0, 0.0], [1.0, 1.0]])  # identity in DCP

    # User's curve: 4-point S-curve.
    lr_curve = [
        TonePoint(0.0, 0.0),
        TonePoint(0.25, 0.15),
        TonePoint(0.75, 0.85),
        TonePoint(1.0, 1.0),
    ]
    out = tmp_path / "frame.xmp"
    emit_darktable_xmp(
        DevelopOps(exposure_ev=0.0, tone_curve=lr_curve),
        out,
        dcp_profile=dcp,
    )
    root = _parse(out)
    found = False
    for li in root.iter(f"{{{RDF_NS}}}li"):
        if li.get(f"{{{DT_NS}}}operation") == "tonecurve":
            found = True
            decoded = bytes.fromhex(li.get(f"{{{DT_NS}}}params"))
            nodes = struct.unpack("<iii", decoded[480:480 + 12])
            # User's 4-point curve fits without resampling.
            assert nodes[0] == 4
            # Verify the 4 points round-trip (4 points × 2 floats × 4 B = 32 B).
            # float32 precision: assert allclose, not equal.
            l_pts = struct.unpack("<8f", decoded[:32])
            np.testing.assert_allclose(
                l_pts,
                [0.0, 0.0, 0.25, 0.15, 0.75, 0.85, 1.0, 1.0],
                rtol=1e-6, atol=1e-6,
            )
    assert found, "tonecurve module not emitted from LR curve"


def test_emitter_baseline_exposure_offsets_ops_exposure(tmp_path):
    # DCP BaselineExposure + BaselineExposureOffset add additively to
    # ops.exposure_ev when a profile is supplied (LR convention).
    dcp = _make_test_dcp()
    dcp.baseline_exposure = 0.35
    dcp.baseline_exposure_offset = 0.15
    out = tmp_path / "frame.xmp"
    emit_darktable_xmp(DevelopOps(exposure_ev=1.0), out, dcp_profile=dcp)
    root = _parse(out)
    for li in root.iter(f"{{{RDF_NS}}}li"):
        if li.get(f"{{{DT_NS}}}operation") == "exposure":
            decoded = bytes.fromhex(li.get(f"{{{DT_NS}}}params"))
            # Layout: int mode + 4 floats + 2 ints. Exposure at offset 8.
            exposure = struct.unpack("<f", decoded[8:12])[0]
            assert exposure == 1.0 + 0.35 + 0.15
            break
    else:
        raise AssertionError("exposure entry missing")


def test_emitter_history_end_matches_entry_count(tmp_path):
    # dt's history_end attribute must equal the number of active history
    # entries (per src/common/exif.cc#L4250-4280 in dt master). Stale
    # history_end values cause dt to truncate the history at the wrong
    # point.
    out = tmp_path / "frame.xmp"
    dcp = _make_test_dcp()
    dcp.profile_tone_curve = np.array([[0.0, 0.0], [0.5, 0.7], [1.0, 1.0]])
    emit_darktable_xmp(
        DevelopOps(exposure_ev=1.0, temperature_k=5500),
        out,
        dcp_profile=dcp,
    )
    root = _parse(out)
    desc = next(root.iter(f"{{{RDF_NS}}}Description"))
    n_entries = sum(1 for _ in root.iter(f"{{{RDF_NS}}}li"))
    # 3 entries: exposure, temperature, basecurve (DCP curve path).
    assert n_entries == 3
    assert desc.get(f"{{{DT_NS}}}history_end") == "3"


def test_exposure_params_roundtrip(tmp_path):
    # dt master exposure modversion 7 struct: mode int + 4 floats + 2 gbooleans
    # (4 bytes each). Total 28 bytes. compensate_hilite_pres added in v7 with
    # default TRUE. See docs/reference/darktable/MODULES.md.
    #
    # ENCODING: hex ASCII (lowercase 0-9a-f). dt's XMP reader at
    # src/common/exif.cc#L3252-3270 runs strspn against [0-9a-f] and silently
    # falls back to module defaults on any other encoding (e.g. base64).
    # This test guards against that regression.
    out = tmp_path / "frame.xmp"
    emit_darktable_xmp(DevelopOps(exposure_ev=2.5), out)
    root = _parse(out)
    for li in root.iter(f"{{{RDF_NS}}}li"):
        if li.get(f"{{{DT_NS}}}operation") == "exposure":
            params_hex = li.get(f"{{{DT_NS}}}params")
            # Must be pure hex (dt's strspn check).
            assert all(c in "0123456789abcdef" for c in params_hex), (
                f"params must be hex-encoded, got {params_hex!r}"
            )
            decoded = bytes.fromhex(params_hex)
            assert len(decoded) == 28, f"expected 28-byte v7 struct, got {len(decoded)}"
            mode, black, exposure, perc, target, comp_bias, comp_hilite = (
                struct.unpack("<iffffii", decoded)
            )
            assert mode == 0
            assert black == 0.0
            assert exposure == 2.5
            assert perc == 50.0
            assert target == -4.0
            assert comp_bias == 0   # default FALSE
            assert comp_hilite == 1  # default TRUE per v7 introduction
            # And modversion should be "7" on the rdf:li
            assert li.get(f"{{{DT_NS}}}modversion") == "7"
            break
    else:
        raise AssertionError("exposure entry missing")


# ---------------------------------------------------------------------------
# PV2012 → dt mapping helpers (Blacks2012 + Sharpness)
# ---------------------------------------------------------------------------

def test_lr_blacks_to_dt_black_breakpoints_match_dt_lightroom_c():
    # Verbatim from darktable's src/develop/lightroom.c#L279-L285 at SHA
    # 9402c65275. Any drift from these exact values means we've diverged
    # from dt's own LR-import behavior, which is the bug we explicitly
    # don't want. Float comparison uses a tight tolerance — endpoints
    # match exactly, interior interpolation accumulates the usual IEEE
    # noise.
    assert lr_blacks_to_dt_black(-100.0) == pytest.approx(0.020, abs=1e-9)
    assert lr_blacks_to_dt_black( -50.0) == pytest.approx(0.005, abs=1e-9)
    assert lr_blacks_to_dt_black(   0.0) == pytest.approx(0.000, abs=1e-9)
    assert lr_blacks_to_dt_black(  50.0) == pytest.approx(-0.005, abs=1e-9)
    assert lr_blacks_to_dt_black( 100.0) == pytest.approx(-0.010, abs=1e-9)


def test_lr_blacks_to_dt_black_interpolates_between_breakpoints():
    # Midpoints linearly interpolate.
    assert lr_blacks_to_dt_black(-75.0) == pytest.approx(0.0125, abs=1e-9)
    assert lr_blacks_to_dt_black( 25.0) == pytest.approx(-0.0025, abs=1e-9)


def test_lr_blacks_to_dt_black_clamps_out_of_range():
    assert lr_blacks_to_dt_black(-9999.0) == pytest.approx(0.020, abs=1e-9)
    assert lr_blacks_to_dt_black( 9999.0) == pytest.approx(-0.010, abs=1e-9)


def test_lr_sharpness_to_dt_amount_defaults_align():
    # LR default 25 → dt default 0.5 (both modules' own out-of-box values).
    assert lr_sharpness_to_dt_amount(25.0) == 0.5
    assert lr_sharpness_to_dt_amount( 0.0) == 0.0
    assert lr_sharpness_to_dt_amount(100.0) == 2.0
    # Above LR 100 clamps to dt max 2.0 (avoids overdriving dt's USM).
    assert lr_sharpness_to_dt_amount(150.0) == 2.0


def test_emitter_uses_lr2dt_blacks_mapping(tmp_path):
    # Blacks2012=-100 → dt exposure.black=+0.020 reaches the params blob.
    out = tmp_path / "frame.xmp"
    emit_darktable_xmp(DevelopOps(exposure_ev=0.0, blacks=-100.0), out)
    root = _parse(out)
    for li in root.iter(f"{{{RDF_NS}}}li"):
        if li.get(f"{{{DT_NS}}}operation") == "exposure":
            params_hex = li.get(f"{{{DT_NS}}}params")
            decoded = bytes.fromhex(params_hex)
            _mode, black, *_ = struct.unpack("<iffffii", decoded)
            assert black == pytest.approx(0.020, abs=1e-6)
            break
    else:
        raise AssertionError("exposure entry missing")


def test_emitter_omits_sharpen_for_lr_default(tmp_path):
    # LR Sharpness=25 is the out-of-camera default — emit gate must skip,
    # otherwise every neutral keyframe in a real LRT sequence gets a
    # spurious sharpen module added to the history. Also skips
    # Sharpness=0 (LR's explicit "no sharpening").
    for s in (0.0, 25.0):
        out = tmp_path / f"sharp_{s}.xmp"
        emit_darktable_xmp(DevelopOps(exposure_ev=0.0, sharpness=s), out)
        root = _parse(out)
        ops_present = [
            li.get(f"{{{DT_NS}}}operation")
            for li in root.iter(f"{{{RDF_NS}}}li")
        ]
        assert "sharpen" not in ops_present, (
            f"sharpen should not emit for LR default Sharpness={s}, got history "
            f"{ops_present}"
        )


def test_emitter_omits_colorbalancergb_for_neutral_keyframe(tmp_path):
    # Saturation=Vibrance=Contrast=0 (LR defaults / neutral) → no emit.
    out = tmp_path / "neutral.xmp"
    emit_darktable_xmp(DevelopOps(exposure_ev=0.0), out)
    root = _parse(out)
    ops = [li.get(f"{{{DT_NS}}}operation") for li in root.iter(f"{{{RDF_NS}}}li")]
    assert "colorbalancergb" not in ops, (
        f"colorbalancergb should not emit for neutral keyframe, got {ops}"
    )


def test_emitter_emits_colorbalancergb_for_any_authored_sat_vib_contrast(tmp_path):
    # Any one of saturation/vibrance/contrast non-zero triggers emit.
    for fld, val in (("saturation", 50.0), ("vibrance", -30.0), ("contrast", 25.0)):
        out = tmp_path / f"{fld}.xmp"
        emit_darktable_xmp(DevelopOps(exposure_ev=0.0, **{fld: val}), out)
        root = _parse(out)
        ops = [li.get(f"{{{DT_NS}}}operation") for li in root.iter(f"{{{RDF_NS}}}li")]
        assert "colorbalancergb" in ops, (
            f"colorbalancergb should emit when {fld}={val}, got history {ops}"
        )


def test_emitter_colorbalancergb_params_struct_size(tmp_path):
    # Struct must be exactly 132 bytes (32 floats × 4 + 1 int × 4) per
    # src/iop/colorbalancergb.c#L60-L106 v5. Wrong size → dt silently
    # substitutes defaults (HIGH-1 class).
    out = tmp_path / "cbrgb.xmp"
    emit_darktable_xmp(DevelopOps(exposure_ev=0.0, saturation=50.0), out)
    root = _parse(out)
    for li in root.iter(f"{{{RDF_NS}}}li"):
        if li.get(f"{{{DT_NS}}}operation") == "colorbalancergb":
            params_hex = li.get(f"{{{DT_NS}}}params")
            decoded = bytes.fromhex(params_hex)
            assert len(decoded) == 132, f"expected 132-byte v5 struct, got {len(decoded)}"
            # Field-by-field indices into the 32-float sequence (per the
            # C struct order in src/iop/colorbalancergb.c#L60-L106):
            #   0..11  shadows/midtones/highlights/global × {Y, C, H}
            #   12..14 shadows_weight, white_fulcrum, highlights_weight
            #   15..18 chroma_{shadows,highlights,global,midtones}
            #   19..22 saturation_{global,highlights,midtones,shadows}
            #   23     hue_angle
            #   24..27 brilliance_{global,highlights,midtones,shadows}
            #   28     mask_grey_fulcrum (default 0.1845)
            #   29..31 vibrance, grey_fulcrum (default 0.1845), contrast
            # Trailing int: saturation_formula
            unpacked = struct.unpack("<32fi", decoded)
            assert unpacked[19] == pytest.approx(0.5, abs=1e-6), (
                f"saturation_global ({unpacked[19]}) should be LR 50 / 100 = 0.5"
            )
            assert unpacked[29] == pytest.approx(0.0, abs=1e-6), (
                f"vibrance should be 0 (LR vibrance unset), got {unpacked[29]}"
            )
            assert unpacked[31] == pytest.approx(0.0, abs=1e-6), (
                f"contrast should be 0 (LR contrast unset), got {unpacked[31]}"
            )
            # Default grey_fulcrum + mask_grey_fulcrum.
            assert unpacked[28] == pytest.approx(0.1845, abs=1e-4)
            assert unpacked[30] == pytest.approx(0.1845, abs=1e-4)
            # saturation_formula = DT_COLORBALANCE_SATURATION_DTUCS = 1
            assert unpacked[32] == 1
            return
    raise AssertionError("colorbalancergb entry missing")


def test_emitter_emits_sharpen_for_non_default_sharpness(tmp_path):
    # Sharpness=50 is user-set creative intent → emit.
    out = tmp_path / "sharp_50.xmp"
    emit_darktable_xmp(DevelopOps(exposure_ev=0.0, sharpness=50.0), out)
    root = _parse(out)
    sharpen_entries = [
        li for li in root.iter(f"{{{RDF_NS}}}li")
        if li.get(f"{{{DT_NS}}}operation") == "sharpen"
    ]
    assert len(sharpen_entries) == 1
    params_hex = sharpen_entries[0].get(f"{{{DT_NS}}}params")
    assert all(c in "0123456789abcdef" for c in params_hex)
    radius, amount, threshold = struct.unpack("<fff", bytes.fromhex(params_hex))
    assert radius == pytest.approx(2.0, abs=1e-6)
    assert amount == pytest.approx(1.0, abs=1e-6)  # LR 50 → dt 1.0
    assert threshold == pytest.approx(0.5, abs=1e-6)
