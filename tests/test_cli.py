"""CLI smoke + dry-run end-to-end."""

import shutil
from pathlib import Path

from lrt_cinema.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


def test_cli_help_returns_zero(capsys):
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    out = capsys.readouterr().out
    assert "lrt-cinema" in out


def test_render_dry_run_emits_xmps_and_skips_darktable(tmp_path):
    src = tmp_path / "input"
    src.mkdir()
    (src / "frame_0001.CR3").write_bytes(b"raw-stub")
    (src / "frame_0002.CR3").write_bytes(b"raw-stub")
    (src / "frame_0003.CR3").write_bytes(b"raw-stub")
    shutil.copy(FIXTURES / "synthetic_keyframe_a.xmp", src / "frame_0001.CR3.xmp")
    shutil.copy(FIXTURES / "synthetic_keyframe_b.xmp", src / "frame_0003.CR3.xmp")

    out = tmp_path / "output"

    rc = main([
        "render",
        "--input", str(src),
        "--output", str(out),
        "--preset", "cinema-linear",
        "--dry-run",
        "--quiet",
    ])
    assert rc == 0

    emitted_xmps = sorted(p.name for p in out.glob("*.dt.xmp"))
    assert emitted_xmps == [
        "frame_0001.CR3.dt.xmp",
        "frame_0002.CR3.dt.xmp",
        "frame_0003.CR3.dt.xmp",
    ]
    src_xmps = sorted(p.name for p in src.glob("*.xmp"))
    assert src_xmps == ["frame_0001.CR3.xmp", "frame_0003.CR3.xmp"]


def test_render_does_not_overwrite_lrt_xmp_input(tmp_path):
    """Regression for P0: emitting darktable XMP must not clobber the LRT input."""
    src = tmp_path / "input"
    src.mkdir()
    (src / "frame_0001.CR3").write_bytes(b"raw-stub")
    lrt_xmp = src / "frame_0001.CR3.xmp"
    shutil.copy(FIXTURES / "synthetic_keyframe_a.xmp", lrt_xmp)
    original_bytes = lrt_xmp.read_bytes()

    out = tmp_path / "output"
    rc = main([
        "render",
        "--input", str(src),
        "--output", str(out),
        "--preset", "cinema-linear",
        "--dry-run",
        "--quiet",
    ])
    assert rc == 0
    assert lrt_xmp.read_bytes() == original_bytes, "LRT XMP must survive render"

    rc2 = main([
        "render",
        "--input", str(src),
        "--output", str(out),
        "--preset", "cinema-linear",
        "--dry-run",
        "--quiet",
    ])
    assert rc2 == 0
    assert lrt_xmp.read_bytes() == original_bytes, "second render also preserves LRT XMP"


def test_render_rejects_invalid_from_frame(tmp_path):
    src = tmp_path / "input"
    src.mkdir()
    (src / "frame_0001.CR3").write_bytes(b"raw-stub")
    (src / "frame_0002.CR3").write_bytes(b"raw-stub")
    shutil.copy(FIXTURES / "synthetic_keyframe_a.xmp", src / "frame_0001.CR3.xmp")

    out = tmp_path / "output"
    rc = main([
        "render", "--input", str(src), "--output", str(out),
        "--preset", "cinema-linear", "--from-frame", "99", "--dry-run", "--quiet",
    ])
    assert rc == 2


def test_render_rejects_unknown_preset(capsys):
    try:
        main([
            "render",
            "--input", "/tmp/nope",
            "--output", "/tmp/nope-out",
            "--preset", "bogus",
        ])
    except SystemExit as exc:
        assert exc.code != 0
    err = capsys.readouterr().err
    assert "invalid choice" in err or "bogus" in err
