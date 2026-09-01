import subprocess

import pytest

from vocalize import clipboard
from vocalize.exceptions import ClipboardError


def test_read_clipboard_calls_pbpaste(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="copied text", stderr="")

    monkeypatch.setattr(clipboard.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)

    assert clipboard.read_clipboard() == "copied text"
    assert calls == [["pbpaste"]]


def test_read_clipboard_refuses_non_darwin(monkeypatch):
    monkeypatch.setattr(clipboard.platform, "system", lambda: "Linux")

    with pytest.raises(ClipboardError, match="only supports macOS"):
        clipboard.read_clipboard()


def test_read_clipboard_wraps_subprocess_failure(monkeypatch):
    def fake_run(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(clipboard.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)

    with pytest.raises(ClipboardError, match="Could not read the clipboard"):
        clipboard.read_clipboard()
