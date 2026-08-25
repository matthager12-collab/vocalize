"""Tests for vocalize.audio — player dispatch, save, and error wrapping.

subprocess.run and the platform/shutil probes are monkeypatched so these
tests don't depend on what's actually installed on the machine running
them.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

import pytest

from vocalize.audio import play, save
from vocalize.exceptions import AudioPlaybackError, NoAudioPlayerError, VocalizeError


def test_play_uses_first_available_player(monkeypatch, tmp_path):
    path = tmp_path / "out.mp3"
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(shutil, "which", lambda exe: "/usr/bin/ffplay" if exe == "ffplay" else None)
    calls = []
    call_kwargs = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kwargs: (calls.append(cmd), call_kwargs.append(kwargs))[0],
    )

    play(path)

    assert calls == [["ffplay", "-nodisp", "-autoexit", str(path)]]
    assert call_kwargs[0].get("check") is True


def test_play_raises_when_no_player_found(monkeypatch, tmp_path):
    path = tmp_path / "out.mp3"
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(shutil, "which", lambda exe: None)

    with pytest.raises(NoAudioPlayerError) as excinfo:
        play(path)

    assert str(path) in str(excinfo.value)


def test_play_wraps_player_failure(monkeypatch, tmp_path):
    path = tmp_path / "out.mp3"
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(shutil, "which", lambda exe: "/usr/bin/afplay" if exe == "afplay" else None)

    def fake_run(cmd, **kwargs):
        if kwargs.get("check"):
            raise subprocess.CalledProcessError(1, cmd)
        return subprocess.CompletedProcess(cmd, 1)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(AudioPlaybackError) as excinfo:
        play(path)

    assert isinstance(excinfo.value, VocalizeError)
    assert "afplay" in str(excinfo.value)
    assert str(path) in str(excinfo.value)


def test_windows_command_escapes_quotes_in_path(monkeypatch, tmp_path):
    path = tmp_path / "it's.mp3"
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: calls.append(cmd))

    play(path)

    script = calls[0][2]
    assert "it''s.mp3" in script
    # the raw, un-doubled quote sequence would break out of the PowerShell
    # string literal — it must not appear.
    assert "it's.mp3'" not in script


def test_save_creates_parent_directories(tmp_path):
    dest = tmp_path / "a" / "b" / "c.mp3"

    result = save(b"x", dest)

    assert result == dest
    assert dest.read_bytes() == b"x"


def test_save_wraps_permission_error(monkeypatch, tmp_path):
    def raise_permission_error(self, data):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "write_bytes", raise_permission_error)

    with pytest.raises(AudioPlaybackError):
        save(b"x", tmp_path / "out.mp3")
