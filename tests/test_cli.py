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
    """Default intent is per emission target (DECISIONS §7): sRGB TIFF
    (lrtimelapse) → faithful; ACEScg EXR (resolve/master) → perceptual."""
    src = _seq_input(tmp_path)
    out = tmp_path / "out"
    for target, want in (("lrtimelapse", "faithful"),
                         ("resolve", "perceptual"), ("master", "perceptual")):
        rc = main(["render", "--input", str(src), "--output", str(out),
                   "--target", target, "--dry-run", "--quiet"])
        assert rc == 0
        assert f"intent={want}" in capsys.readouterr().err, target


def test_render_intent_overrides_target_default(tmp_path, capsys):
    """--render-intent overrides the per-target default (EXR target forced faithful)."""
    src = _seq_input(tmp_path)
    rc = main(["render", "--input", str(src), "--output", str(tmp_path / "out"),
               "--target", "resolve", "--render-intent", "faithful", "--dry-run", "--quiet"])
    assert rc == 0
    assert "intent=faithful" in capsys.readouterr().err


def test_dropped_basic_tone_warns_at_render(tmp_path, capsys):
    """Highlights/Shadows/Whites set in the XMP but dropped at render surface a
    per-field, frame-counted warning — never a silent drop (the user's explicit
    requirement; DECISIONS §5/§7). synthetic_keyframe_a sets all three."""
    src = _seq_input(tmp_path)
    rc = main(["render", "--input", str(src), "--output", str(tmp_path / "out"),
               "--dry-run", "--quiet"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "Shadows2012 set on 1/1" in err
    assert "not applied at render" in err.lower()


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
