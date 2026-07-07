"""CLI smoke tests for the v0.6 9-flag surface.

Render-time paths (NEF → DNG → pipeline → output writer) are exercised in
`test_pipeline.py` end-to-end against real fixtures; here we only test the
arg-parser shape, dry-run plumbing, error handling, and the inspect
subcommand. No render-time deps required.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lrt_cinema.cli import _output_stem, main
from lrt_cinema.presets import DEFAULT_PRESET

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Output naming — LRTimelapse strict convention vs source stem
# ---------------------------------------------------------------------------


def test_default_preset_is_lrtimelapse():
    assert DEFAULT_PRESET == "lrtimelapse"


def test_lrtimelapse_uses_lrt_strict_naming(tmp_path):
    """LRT requires LRT_00001, 5-digit, 1-based, to recognise the sequence."""
    assert _output_stem(tmp_path, "lrtimelapse", 0, "DSC_0001.NEF").name == "LRT_00001"
    assert _output_stem(tmp_path, "lrtimelapse", 9, "DSC_9.NEF").name == "LRT_00010"
    assert _output_stem(tmp_path, "lrtimelapse", 99, "x.NEF").name == "LRT_00100"


def test_non_lrt_targets_keep_source_stem(tmp_path):
    assert _output_stem(
        tmp_path, "cinema-linear-finished", 5, "DSC_0042.NEF",
    ).name == "DSC_0042"


# ---------------------------------------------------------------------------
# Help / version
# ---------------------------------------------------------------------------


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "lrt-cinema" in capsys.readouterr().out


def test_render_help_lists_9_flag_surface(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["render", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for flag in (
        "--input", "--output", "--preset",
        "--from-frame", "--to-frame",
        "--dry-run", "--quiet",
        "--apply-lrt-offsets", "--no-lrt-offsets",
        "--dcp", "--workers", "--no-dng-convert",
        "--render-intent",  # v0.9 dual-mode (DECISIONS.md §7)
    ):
        assert flag in out, f"missing {flag}"


def test_dropped_flags_no_longer_accepted(capsys, tmp_path):
    """v0.4 flags that v0.6 dropped must error, not silently accept."""
    src = tmp_path / "input"
    src.mkdir()
    out = tmp_path / "output"
    for old_flag in ("--engine", "--style", "--no-auto-dcp",
                     "--no-dcp-tone-curve", "--no-dcp-hsv-cubes",
                     "--deflicker", "--lrt-mask-offsets"):
        with pytest.raises(SystemExit) as exc:
            main([
                "render", "--input", str(src), "--output", str(out),
                "--preset", "cinema-linear-finished", old_flag, "x",
            ])
        assert exc.value.code != 0, f"{old_flag} should be rejected"


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


def test_render_requires_input_output_preset(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["render"])
    assert exc.value.code != 0


def test_render_rejects_unknown_preset(tmp_path):
    src = tmp_path / "input"
    src.mkdir()
    out = tmp_path / "output"
    with pytest.raises(SystemExit):
        main([
            "render", "--input", str(src), "--output", str(out),
            "--preset", "nonexistent",
        ])


def test_render_rejects_same_input_and_output(tmp_path, capsys):
    src = tmp_path / "input"
    src.mkdir()
    rc = main([
        "render", "--input", str(src), "--output", str(src),
        "--preset", "cinema-linear-finished", "--dry-run",
    ])
    assert rc == 2
    assert "must differ" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Dry-run path (no render-time deps required)
# ---------------------------------------------------------------------------


def test_dry_run_reports_what_would_render(tmp_path, capsys):
    """Dry-run must NOT write output frames or invoke the render pipeline.
    Only stderr message about what would happen."""
    src = tmp_path / "input"
    src.mkdir()
    (src / "frame_0001.CR3").write_bytes(b"raw-stub")
    (src / "frame_0002.CR3").write_bytes(b"raw-stub")
    (src / "frame_0003.CR3").write_bytes(b"raw-stub")
    shutil.copy(FIXTURES / "synthetic_keyframe_a.xmp", src / "frame_0001.CR3.xmp")
    shutil.copy(FIXTURES / "synthetic_keyframe_b.xmp", src / "frame_0003.CR3.xmp")
    out = tmp_path / "output"

    rc = main([
        "render", "--input", str(src), "--output", str(out),
        "--preset", "cinema-linear-finished", "--dry-run", "--quiet",
    ])
    assert rc == 0
    err = capsys.readouterr().err
    assert "dry-run" in err
    assert "3 frame" in err
    # Dry-run must not have written any output TIFFs.
    assert not list(out.glob("*.tif"))
    assert not list(out.glob("*.exr"))


def test_render_intent_defaults_faithful_and_reports_in_dry_run(tmp_path, capsys):
    """--render-intent defaults to faithful and surfaces in the dry-run line;
    an explicit perceptual is accepted (DECISIONS.md §7 dual-mode scaffold)."""
    src = tmp_path / "input"
    src.mkdir()
    (src / "frame_0001.CR3").write_bytes(b"raw-stub")
    shutil.copy(FIXTURES / "synthetic_keyframe_a.xmp", src / "frame_0001.CR3.xmp")
    out = tmp_path / "out"

    rc = main(["render", "--input", str(src), "--output", str(out), "--dry-run", "--quiet"])
    assert rc == 0
    assert "intent=faithful" in capsys.readouterr().err

    rc = main(["render", "--input", str(src), "--output", str(out),
               "--render-intent", "perceptual", "--dry-run", "--quiet"])
    assert rc == 0
    assert "intent=perceptual" in capsys.readouterr().err


def test_render_rejects_unknown_intent(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["render", "--input", str(tmp_path), "--output", str(tmp_path / "o"),
              "--render-intent", "bogus", "--dry-run"])
    assert exc.value.code != 0
    assert "render-intent" in capsys.readouterr().err.lower()


def _seq_input(tmp_path):
    src = tmp_path / "input"
    src.mkdir()
    (src / "f0001.CR3").write_bytes(b"raw-stub")
    shutil.copy(FIXTURES / "synthetic_keyframe_a.xmp", src / "f0001.CR3.xmp")
    return src


def test_render_intent_default_is_per_target(tmp_path, capsys):
    """Default intent is per emission target (DECISIONS §7; trunk/branch F2b fix —
    docs/research/pipeline-order-audit.md §F2b). PERCEPTUAL is only coherent on
    SCENE-LINEAR input, so it defaults ONLY on the tap-7 trunk master
    (`master` → cinema-linear-master). The sRGB TIFF (`lrtimelapse`) AND the tap-9
    `resolve` (cinema-linear-finished — the full Adobe look already baked + clamped)
    both default to FAITHFUL: running scene-referred ops on tone-curved/clamped data
    is a domain mismatch. `--render-intent` overrides either way."""
    src = _seq_input(tmp_path)
    out = tmp_path / "out"
    for target, want in (("lrtimelapse", "faithful"),
                         ("resolve", "faithful"), ("master", "perceptual")):
        rc = main(["render", "--input", str(src), "--output", str(out),
                   "--target", target, "--dry-run", "--quiet"])
        assert rc == 0
        assert f"intent={want}" in capsys.readouterr().err, target


def test_render_intent_overrides_target_default(tmp_path, capsys):
    """--render-intent overrides the per-target default. `master` (tap-7
    cinema-linear-master) defaults PERCEPTUAL; forcing faithful proves the override
    (resolve already defaults faithful post-F2b, so it would no longer demonstrate one)."""
    src = _seq_input(tmp_path)
    rc = main(["render", "--input", str(src), "--output", str(tmp_path / "out"),
               "--target", "master", "--render-intent", "faithful", "--dry-run", "--quiet"])
    assert rc == 0
    assert "intent=faithful" in capsys.readouterr().err


def _sharpness_seq_input(tmp_path):
    """One-frame KEYFRAME sequence carrying crs:Sharpness above LR's default.
    Sharpness alone is not a keyframe trigger (LR writes a default everywhere), so it
    rides a meaningful op (Exposure2012) — the realistic case the drop-warning targets."""
    src = tmp_path / "input"
    src.mkdir()
    (src / "f0001.CR3").write_bytes(b"raw-stub")
    (src / "f0001.CR3.xmp").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about=""\n'
        '    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"\n'
        '    crs:Exposure2012="0.50" crs:Sharpness="80"/>\n'
        ' </rdf:RDF>\n'
        '</x:xmpmeta>\n'
    )
    return src


def test_capture_sharpen_surfaces_state_per_intent(tmp_path, capsys):
    """crs:Sharpness is now an IMPLEMENTED, flag-gated capture USM (D2, DECISIONS §5
    amendment). Its state is surfaced, never silent: faithful default-off warns it's
    set-but-not-applied + how to enable; faithful --capture-sharpen on emits an 'ON'
    info; the perceptual master warns it defers detail to the grade."""
    src = _sharpness_seq_input(tmp_path)
    # faithful, default off → set-but-not-applied warning + how to enable
    assert main(["render", "--input", str(src), "--output", str(tmp_path / "o_off"),
                 "--render-intent", "faithful", "--dry-run", "--quiet"]) == 0
    err = capsys.readouterr().err
    assert "Sharpness set on 1/1" in err and "OFF by default" in err
    # faithful, capture sharpening ON → info line
    assert main(["render", "--input", str(src), "--output", str(tmp_path / "o_on"),
                 "--render-intent", "faithful", "--capture-sharpen", "acr",
                 "--dry-run", "--quiet"]) == 0
    assert "capture sharpening ON" in capsys.readouterr().err
    # perceptual master defers detail to the grade
    assert main(["render", "--input", str(src), "--output", str(tmp_path / "o_perc"),
                 "--render-intent", "perceptual", "--dry-run", "--quiet"]) == 0
    assert "defers detail" in capsys.readouterr().err


def test_master_look_dry_run_default_and_override(tmp_path, capsys):
    """--master-look: default 'defer' on the PERCEPTUAL master (target=master),
    overridable to 'bake'; forced 'bake' on the faithful sRGB path regardless."""
    src = _seq_input(tmp_path)
    out = tmp_path / "out"
    # master (perceptual tap-7) → default defer
    main(["render", "--input", str(src), "--output", str(out),
          "--target", "master", "--dry-run", "--quiet"])
    assert "master_look=defer" in capsys.readouterr().err
    # master + explicit bake
    main(["render", "--input", str(src), "--output", str(out),
          "--target", "master", "--master-look", "bake", "--dry-run", "--quiet"])
    assert "master_look=bake" in capsys.readouterr().err
    # faithful sRGB always bakes the look, even if --master-look defer is passed
    main(["render", "--input", str(src), "--output", str(out),
          "--target", "lrtimelapse", "--master-look", "defer", "--dry-run", "--quiet"])
    assert "master_look=bake" in capsys.readouterr().err


def test_demosaic_flag_dry_run(tmp_path, capsys):
    """--demosaic threads to the job; CLI default 'amaze' (2026-07-06
    decision under owner-authorized criteria — evidence
    seq_spot_amaze_2026-07-06; render_frame keeps 'linear' as the
    byte-exact library default), dcb opt-in."""
    src = _seq_input(tmp_path)
    out = tmp_path / "out"
    main(["render", "--input", str(src), "--output", str(out), "--dry-run", "--quiet"])
    assert "demosaic=amaze" in capsys.readouterr().err
    main(["render", "--input", str(src), "--output", str(out),
          "--demosaic", "dcb", "--dry-run", "--quiet"])
    assert "demosaic=dcb" in capsys.readouterr().err


def test_capture_sharpen_flag_dry_run(tmp_path, capsys):
    """--capture-sharpen: default 'off' (byte-exact), faithful bakes the chosen
    mode, the perceptual master is forced 'off' (defers detail to the grade)."""
    src = _seq_input(tmp_path)
    out = tmp_path / "out"
    main(["render", "--input", str(src), "--output", str(out), "--dry-run", "--quiet"])
    assert "capture_sharpen=off" in capsys.readouterr().err
    # faithful sRGB + acr bakes ACR-default capture sharpening
    main(["render", "--input", str(src), "--output", str(out), "--target", "lrtimelapse",
          "--capture-sharpen", "acr", "--dry-run", "--quiet"])
    assert "capture_sharpen=acr" in capsys.readouterr().err
    # perceptual master forces off even when acr is requested
    main(["render", "--input", str(src), "--output", str(out), "--target", "master",
          "--capture-sharpen", "acr", "--dry-run", "--quiet"])
    assert "capture_sharpen=off" in capsys.readouterr().err


def test_ca_correct_flag_dry_run(tmp_path, capsys):
    """--ca-correct: default 0 = OFF (owner-gated slot-2 opt-in; every
    existing output byte-identical); N threads to the job."""
    src = _seq_input(tmp_path)
    out = tmp_path / "out"
    main(["render", "--input", str(src), "--output", str(out), "--dry-run", "--quiet"])
    assert "ca_correct=0" in capsys.readouterr().err
    main(["render", "--input", str(src), "--output", str(out),
          "--ca-correct", "2", "--dry-run", "--quiet"])
    assert "ca_correct=2" in capsys.readouterr().err


def test_dropped_basic_tone_warns_at_render(tmp_path, capsys):
    """Highlights/Shadows/Whites set in the XMP but dropped on the FAITHFUL path
    surface a per-field, frame-counted warning — never a silent drop (the user's
    explicit requirement; DECISIONS §5/§7). synthetic_keyframe_a sets all three."""
    src = _seq_input(tmp_path)
    rc = main(["render", "--input", str(src), "--output", str(tmp_path / "out"),
               "--render-intent", "faithful", "--dry-run", "--quiet"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "Shadows2012 set on 1/1" in err
    assert "not applied under --render-intent faithful" in err.lower()


def test_dropped_basic_tone_not_warned_under_perceptual(tmp_path, capsys):
    """Under PERCEPTUAL the DR-compression op APPLIES Highlights/Shadows/Whites, so
    they are no longer dropped — the warning must be suppressed (DECISIONS §5
    amendment; the intent-aware `_warn_dropped_ops`)."""
    src = _seq_input(tmp_path)
    rc = main(["render", "--input", str(src), "--output", str(tmp_path / "out"),
               "--render-intent", "perceptual", "--dry-run", "--quiet"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "2012 set on" not in err  # no dropped-op warning under perceptual


def _texture_clarity_seq_input(tmp_path):
    """A one-frame sequence whose XMP sets crs:Texture + crs:Clarity2012."""
    src = tmp_path / "input"
    src.mkdir()
    (src / "f0001.CR3").write_bytes(b"raw-stub")
    xmp = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about=""\n'
        '    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"\n'
        '    crs:Texture="40" crs:Clarity2012="-25"/>\n'
        ' </rdf:RDF>\n'
        '</x:xmpmeta>\n'
    )
    (src / "f0001.CR3.xmp").write_text(xmp)
    return src


def test_dropped_texture_clarity_warns_at_render_with_own_wording(tmp_path, capsys):
    """Texture/Clarity set but dropped on FAITHFUL surface a per-field warning with
    THEIR OWN wording (the local-contrast op), NOT the DR-compression message — a
    drop is never silent and never mislabelled (DECISIONS §7 step 4)."""
    src = _texture_clarity_seq_input(tmp_path)
    rc = main(["render", "--input", str(src), "--output", str(tmp_path / "out"),
               "--render-intent", "faithful", "--dry-run", "--quiet"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "Texture set on 1/1" in err
    assert "Clarity2012 set on 1/1" in err
    assert "apply_texture_clarity" in err  # TC's own op, not the DR-compression story
    assert "DR-compression" not in err     # must NOT be mislabelled as the tone op


def test_dropped_texture_clarity_not_warned_under_perceptual(tmp_path, capsys):
    """Under PERCEPTUAL the local-contrast op APPLIES Texture/Clarity, so they are
    not dropped — the warning is suppressed (the intent-aware _warn_dropped_ops)."""
    src = _texture_clarity_seq_input(tmp_path)
    rc = main(["render", "--input", str(src), "--output", str(tmp_path / "out"),
               "--render-intent", "perceptual", "--dry-run", "--quiet"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "Texture set on" not in err
    assert "Clarity2012 set on" not in err


def test_dry_run_does_not_touch_source_xmp(tmp_path):
    src = tmp_path / "input"
    src.mkdir()
    (src / "frame_0001.CR3").write_bytes(b"raw-stub")
    lrt_xmp = src / "frame_0001.CR3.xmp"
    shutil.copy(FIXTURES / "synthetic_keyframe_a.xmp", lrt_xmp)
    original = lrt_xmp.read_bytes()
    out = tmp_path / "output"
    main([
        "render", "--input", str(src), "--output", str(out),
        "--preset", "cinema-linear-finished", "--dry-run", "--quiet",
    ])
    assert lrt_xmp.read_bytes() == original


def test_from_to_frame_validation(tmp_path, capsys):
    src = tmp_path / "input"
    src.mkdir()
    (src / "frame_0001.CR3").write_bytes(b"raw-stub")
    shutil.copy(FIXTURES / "synthetic_keyframe_a.xmp", src / "frame_0001.CR3.xmp")
    out = tmp_path / "output"
    rc = main([
        "render", "--input", str(src), "--output", str(out),
        "--preset", "cinema-linear-finished", "--dry-run",
        "--from-frame", "10",
    ])
    assert rc == 2
    assert "from-frame" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# inspect subcommand
# ---------------------------------------------------------------------------


def test_inspect_summarizes_sequence(tmp_path, capsys):
    src = tmp_path / "input"
    src.mkdir()
    (src / "frame_0001.CR3").write_bytes(b"raw-stub")
    (src / "frame_0002.CR3").write_bytes(b"raw-stub")
    shutil.copy(FIXTURES / "synthetic_keyframe_a.xmp", src / "frame_0001.CR3.xmp")
    rc = main(["inspect", "--input", str(src)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Source RAW frames: 2" in out
    assert "Keyframes detected:" in out


def test_inspect_show_fields_dumps_per_keyframe_ops(tmp_path, capsys):
    src = tmp_path / "input"
    src.mkdir()
    (src / "frame_0001.CR3").write_bytes(b"raw-stub")
    shutil.copy(FIXTURES / "synthetic_keyframe_a.xmp", src / "frame_0001.CR3.xmp")
    rc = main(["inspect", "--input", str(src), "--show-fields"])
    assert rc == 0
    assert "ev=" in capsys.readouterr().out


def test_render_help_lists_target_flag(capsys):
    with pytest.raises(SystemExit):
        main(["render", "--help"])
    assert "--target" in capsys.readouterr().out


def _stub_seq(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    (src / "frame_0001.CR3").write_bytes(b"raw-stub")
    shutil.copy(FIXTURES / "synthetic_keyframe_a.xmp", src / "frame_0001.CR3.xmp")
    return src


def test_target_default_expands_to_lrtimelapse(tmp_path, capsys):
    src = _stub_seq(tmp_path)
    rc = main(["render", "--input", str(src), "--output", str(tmp_path / "out"),
               "--dry-run", "--quiet"])
    assert rc == 0
    assert "preset=lrtimelapse" in capsys.readouterr().err


def test_target_resolve_expands_to_cinema_linear_finished(tmp_path, capsys):
    src = _stub_seq(tmp_path)
    rc = main(["render", "--input", str(src), "--output", str(tmp_path / "out"),
               "--target", "resolve", "--dry-run", "--quiet"])
    assert rc == 0
    assert "preset=cinema-linear-finished" in capsys.readouterr().err


def test_preset_overrides_target(tmp_path, capsys):
    src = _stub_seq(tmp_path)
    rc = main(["render", "--input", str(src), "--output", str(tmp_path / "out"),
               "--target", "resolve", "--preset", "lrtimelapse",
               "--dry-run", "--quiet"])
    assert rc == 0
    assert "preset=lrtimelapse" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Dropped-op warning — the "never hidden" invariant at the CLI layer.
#
# The drop-warning is `sys.stderr.write` (user-facing CLI output), NOT
# `warnings.warn`, so it is asserted via capsys, not pytest.warns. Under FAITHFUL
# the perceptual-only knobs (Highlights/Shadows/Whites, Texture/Clarity) are
# dropped and MUST be surfaced per-field with a frame count; under PERCEPTUAL
# they drive the ops and must NOT warn (DECISIONS.md §5/§7).
# ---------------------------------------------------------------------------


def test_dropped_ops_warned_under_faithful(capsys):
    from lrt_cinema.cli import _warn_dropped_ops
    from lrt_cinema.ir import DevelopOps, RenderIntent
    per_frame = [
        DevelopOps(highlights=40.0, shadows=-20.0, whites=15.0,
                   texture=30.0, clarity=25.0),
        DevelopOps(),  # a clean frame → the count must be 1/2, not 2/2
    ]
    _warn_dropped_ops(per_frame, RenderIntent.FAITHFUL)
    err = capsys.readouterr().err
    # Every dropped knob is surfaced, by its crs:* tag, with the frame count …
    for tag in ("Highlights2012", "Shadows2012", "Whites2012", "Texture", "Clarity2012"):
        assert f"crs:{tag} set on 1/2 frame" in err, f"{tag} drop not warned"
    # … and the warning names the perceptual path as where the math is honoured.
    assert "perceptual" in err.lower()


def test_dropped_ops_not_warned_under_perceptual(capsys):
    """Under PERCEPTUAL the same knobs DRIVE the ops (DR-compression / Texture-
    Clarity) — they are not dropped, so there must be NO drop warning (warning
    there would be a false 'these won't apply' to the user)."""
    from lrt_cinema.cli import _warn_dropped_ops
    from lrt_cinema.ir import DevelopOps, RenderIntent
    per_frame = [DevelopOps(highlights=40.0, texture=30.0)]
    _warn_dropped_ops(per_frame, RenderIntent.PERCEPTUAL)
    assert capsys.readouterr().err == ""


def test_unset_dropped_ops_produce_no_warning(capsys):
    """No false positives: a render with NONE of the dropped knobs set is silent
    even under faithful (the warning fires only on a SET-but-dropped field)."""
    from lrt_cinema.cli import _warn_dropped_ops
    from lrt_cinema.ir import DevelopOps, RenderIntent
    _warn_dropped_ops([DevelopOps(exposure_ev=1.0, contrast=20.0)],
                      RenderIntent.FAITHFUL)
    assert capsys.readouterr().err == ""
