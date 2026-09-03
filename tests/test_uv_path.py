"""`vocalize.local.uv_path()`: PATH, then ~/.local/bin, then Homebrew.

The Quick Actions run under a Services environment whose PATH is bare, so
a Homebrew uv (Apple silicon or Intel) has to be found by name — seen live
on 2026-09-02, when every hotkey dictation ended in "uv is not installed"
while the same command worked from a terminal.
"""

import shutil
from pathlib import Path

import pytest

from vocalize import local


@pytest.fixture
def off_path(monkeypatch, tmp_path):
    """Nothing on PATH and an empty home, so only the fallbacks can answer."""
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path


def test_path_wins(monkeypatch, tmp_path):
    on_path = tmp_path / "uv"
    on_path.write_text("")
    monkeypatch.setattr(shutil, "which", lambda name: str(on_path) if name == "uv" else None)

    assert local.uv_path() == str(on_path)


def test_the_installer_spot_in_home_is_tried_before_homebrew(off_path, monkeypatch):
    own = off_path / "home" / ".local" / "bin" / "uv"
    own.parent.mkdir(parents=True)
    own.write_text("")
    homebrew = off_path / "opt" / "homebrew" / "bin" / "uv"
    homebrew.parent.mkdir(parents=True)
    homebrew.write_text("")
    monkeypatch.setattr(local, "UV_FALLBACKS", (homebrew,))

    assert local.uv_path() == str(own)


def test_a_homebrew_uv_is_found_off_path(off_path, monkeypatch):
    missing = off_path / "usr" / "local" / "bin" / "uv"
    homebrew = off_path / "opt" / "homebrew" / "bin" / "uv"
    homebrew.parent.mkdir(parents=True)
    homebrew.write_text("")
    monkeypatch.setattr(local, "UV_FALLBACKS", (homebrew, missing))

    assert local.uv_path() == str(homebrew)


def test_no_uv_anywhere_is_none(off_path, monkeypatch):
    monkeypatch.setattr(local, "UV_FALLBACKS", (off_path / "nope" / "uv",))

    assert local.uv_path() is None


def test_the_shipped_fallbacks_are_homebrew_on_both_architectures():
    assert local.UV_FALLBACKS == (Path("/opt/homebrew/bin/uv"), Path("/usr/local/bin/uv"))
