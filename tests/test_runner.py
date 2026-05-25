"""Runner helpers — no real darktable-cli invocation in this file."""

from __future__ import annotations

from unittest.mock import patch

from lrt_cinema.runner import (
    _DT_VERSION_RE,
    _KNOWN_TESTED_DT_SHAS,
    darktable_version,
    warn_on_untested_darktable_version,
)


def test_dt_version_regex_parses_real_world_output():
    """The dt --version regex must match the dt 5.5.0 line shape lrt-cinema
    has been verified against (SHA 9402c65275)."""
    sample = (
        "darktable 5.5.0+1375~g9402c65275 OpenMP support: yes\n"
        "GraphicsMagick support: no\n"
    )
    m = _DT_VERSION_RE.search(sample)
    assert m is not None
    assert m.group(1) == "5.5.0+1375"
    assert m.group(2) == "9402c65275"
    assert "9402c65275" in _KNOWN_TESTED_DT_SHAS


def test_darktable_version_returns_none_when_binary_missing():
    """Probe returns None silently when darktable-cli is not on PATH —
    the runner's own DarktableCliNotFound check surfaces the issue."""
    with patch("lrt_cinema.runner.shutil.which", return_value=None):
        assert darktable_version() is None


def test_warn_on_untested_dt_emits_one_line_then_caches(capsys):
    """The warning fires once per process; subsequent calls are silent.
    Avoids spamming stderr in long sequences."""
    # Reset the cache flag from any prior test run.
    warn_on_untested_darktable_version.__dict__.pop("_done", None)
    with patch(
        "lrt_cinema.runner.darktable_version",
        return_value=("9.9.9", "deadbeefdead"),
    ):
        warn_on_untested_darktable_version()
        warn_on_untested_darktable_version()
        warn_on_untested_darktable_version()
    err = capsys.readouterr().err
    assert err.count("outside lrt-cinema's tested SHA set") == 1


def test_warn_silent_when_sha_is_known_tested(capsys):
    warn_on_untested_darktable_version.__dict__.pop("_done", None)
    with patch(
        "lrt_cinema.runner.darktable_version",
        return_value=("5.5.0+1375", "9402c65275"),
    ):
        warn_on_untested_darktable_version()
    err = capsys.readouterr().err
    assert err == ""


def test_warn_silent_when_probe_returns_none(capsys):
    warn_on_untested_darktable_version.__dict__.pop("_done", None)
    with patch("lrt_cinema.runner.darktable_version", return_value=None):
        warn_on_untested_darktable_version()
    err = capsys.readouterr().err
    assert err == ""
