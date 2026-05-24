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


def test_inspect_reports_keyframes_and_drops(tmp_path, capsys):
    src = tmp_path / "input"
    src.mkdir()
    (src / "frame_0001.CR3").write_bytes(b"raw-stub")
    (src / "frame_0002.CR3").write_bytes(b"raw-stub")
    (src / "frame_0003.CR3").write_bytes(b"raw-stub")
    shutil.copy(FIXTURES / "synthetic_keyframe_a.xmp", src / "frame_0001.CR3.xmp")
    shutil.copy(FIXTURES / "synthetic_keyframe_b.xmp", src / "frame_0003.CR3.xmp")

    rc = main(["inspect", "--input", str(src)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Source RAW frames: 3" in out
    assert "Keyframes detected: 2" in out
    assert "xmp:Rating or lrt:keyframe): 2 of 2" in out

    emit_section = out.split("Emit warnings")[1].split("What WILL")[0]
    # Real drops: highlights / shadows / whites unconditionally; tint and
    # temperature_k drop here because no DCP is loaded (CR3 stub fixture).
    for truly_dropped in ("highlights", "shadows", "whites", "tint", "temperature_k"):
        assert truly_dropped in emit_section, (
            f"{truly_dropped!r} should be flagged dropped in:\n{emit_section}"
        )
    # v0.4 routes these through emitted dt modules — they must NOT be flagged
    # dropped (the prior stale list caused this exact false positive).
    for emitted in ("contrast", "saturation", "vibrance", "sharpness", "blacks"):
        assert emitted not in emit_section, (
            f"{emitted!r} is emitted in v0.4 and must not appear as DROPPED:\n"
            f"{emit_section}"
        )


def test_inspect_show_fields_dumps_per_keyframe(tmp_path, capsys):
    src = tmp_path / "input"
    src.mkdir()
    (src / "frame_0001.CR3").write_bytes(b"raw-stub")
    shutil.copy(FIXTURES / "synthetic_keyframe_a.xmp", src / "frame_0001.CR3.xmp")

    rc = main(["inspect", "--input", str(src), "--show-fields"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Per-keyframe parsed develop ops:" in out
    assert "ev=+0.50" in out
    assert "k=5500" in out


def test_inspect_rejects_missing_folder(capsys):
    rc = main(["inspect", "--input", "/nonexistent/path/lrt"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Not a directory" in err or "No such" in err


def _make_neutral_lrt_xmp(rating: int = 4) -> bytes:
    """Synthesize a real-LRT-shaped XMP with all values at LR defaults.

    The user's actual LRT keyframes look exactly like this: every crs:* at
    its out-of-camera default, including identity ToneCurvePV2012 and
    sharpness=25. Used to verify the dropped-warning false-positive fix.
    """
    return (
        b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        b'<rdf:Description rdf:about=""\n'
        b'  xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"\n'
        b'  xmlns:xmp="http://ns.adobe.com/xap/1.0/"\n'
        b'  crs:Exposure2012="0.0" crs:Contrast2012="0" crs:Highlights2012="0"\n'
        b'  crs:Shadows2012="0" crs:Whites2012="0" crs:Blacks2012="0"\n'
        b'  crs:Saturation="0" crs:Vibrance="0" crs:Sharpness="25"\n'
        b'  crs:WhiteBalance="As Shot"\n'
        b'  xmp:Rating="' + str(rating).encode() + b'">\n'
        b'  <crs:ToneCurvePV2012>\n'
        b'    <rdf:Seq><rdf:li>0, 0</rdf:li><rdf:li>255, 255</rdf:li></rdf:Seq>\n'
        b'  </crs:ToneCurvePV2012>\n'
        b'</rdf:Description>\n'
        b'</rdf:RDF>\n'
        b'</x:xmpmeta>\n'
        b'<?xpacket end="w"?>\n'
    )


def test_inspect_neutral_lrt_keyframe_no_false_dropped_warnings(tmp_path, capsys):
    """A neutral LRT keyframe (LR defaults: identity curve, sharpness=25)
    must NOT trigger any DROPPED-at-emit warning. Regression for the
    overcount in the dropped-field warning that fired on every neutral
    LRT sequence."""
    src = tmp_path / "input"
    src.mkdir()
    (src / "frame_0001.NEF").write_bytes(b"raw-stub")
    (src / "frame_0001.NEF.xmp").write_bytes(_make_neutral_lrt_xmp())

    rc = main(["inspect", "--input", str(src)])
    assert rc == 0
    out = capsys.readouterr().out
    # The "Emit warnings" section must report no drops on a neutral keyframe.
    assert "Emit warnings" in out
    assert "DROPPED at emit" not in out, (
        f"neutral LRT keyframe falsely flagged dropped fields:\n{out}"
    )


def test_engine_algorithmic_suppresses_dcp_modules(tmp_path, capsys):
    """--engine algorithmic must NOT emit any DCP-derived dt module
    (temperature / basecurve / lut3d) even when --dcp is explicitly
    supplied, and must log that --dcp is being ignored."""
    bundled_npz = (
        Path(__file__).parent / "fixtures" / "dcp_data"
        / "Nikon D750 Camera Standard.npz"
    )
    assert bundled_npz.exists(), "bundled .npz test fixture missing"

    src = tmp_path / "input"
    src.mkdir()
    (src / "frame_0001.NEF").write_bytes(b"raw-stub")
    (src / "frame_0001.NEF.xmp").write_bytes(_make_neutral_lrt_xmp(rating=4))

    out = tmp_path / "output"
    rc = main([
        "render",
        "--input", str(src),
        "--output", str(out),
        "--preset", "cinema-linear",
        "--engine", "algorithmic",
        "--dcp", str(bundled_npz),
        "--dry-run",
        "--quiet",
    ])
    assert rc == 0

    captured = capsys.readouterr()
    assert "--engine algorithmic ignores --dcp" in captured.err
    assert "DCP-derived modules suppressed" in captured.err
    # The DCP "loaded" info line must NOT appear (we skipped loading).
    assert "loaded DCP" not in captured.err

    emitted = (out / "frame_0001.NEF.dt.xmp").read_text()
    for dcp_module in ('operation="temperature"', 'operation="basecurve"',
                       'operation="lut3d"'):
        assert dcp_module not in emitted, (
            f"algorithmic engine leaked DCP-derived module {dcp_module}: {emitted}"
        )


def test_engine_dcp_default_unchanged(tmp_path, capsys):
    """Default --engine=dcp preserves existing behavior: no auto-detect
    happens for an unsupported RAW stub, the 'no DCP supplied' warning
    fires, and render still succeeds with a libraw-default fallback."""
    src = tmp_path / "input"
    src.mkdir()
    (src / "frame_0001.CR3").write_bytes(b"raw-stub")
    (src / "frame_0001.CR3.xmp").write_bytes(_make_neutral_lrt_xmp())

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
    err = capsys.readouterr().err
    assert "no DCP supplied or detected" in err
    # algorithmic-mode-specific messages must NOT appear under the default.
    assert "DCP-derived modules suppressed" not in err
