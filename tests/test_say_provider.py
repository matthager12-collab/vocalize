import subprocess
from pathlib import Path

import pytest

from vocalize.config import Settings
from vocalize.exceptions import (
    ProviderContentError,
    ProviderTransientError,
    ProviderUnavailableError,
)
from vocalize.providers import say

VOICE_LIST = (
    "Albert              en_US    # Hello! My name is Albert.\n"
    "Bad News            en_US    # Hello, my name is Bad News.\n"
    "Alice               it_IT    # Ciao, mi chiamo Alice.\n"
)


@pytest.fixture
def on_macos(monkeypatch):
    monkeypatch.setattr(say.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(say.shutil, "which", lambda name: f"/usr/bin/{name}")


@pytest.fixture
def fake_say(monkeypatch):
    """Stand in for the `say` binary. Returns the argv lists it was given."""
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if "-o" in argv:
            Path(argv[argv.index("-o") + 1]).write_bytes(b"m4a-audio")
        return subprocess.CompletedProcess(argv, 0, stdout=VOICE_LIST, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_check_passes_on_macos(on_macos):
    say.check()  # must not raise


def test_check_refuses_anywhere_else(monkeypatch):
    monkeypatch.setattr(say.platform, "system", lambda: "Linux")

    with pytest.raises(ProviderUnavailableError, match="only available on macOS"):
        say.check()


def test_check_refuses_when_the_binary_is_missing(monkeypatch):
    monkeypatch.setattr(say.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(say.shutil, "which", lambda name: None)

    with pytest.raises(ProviderUnavailableError):
        say.check()


def test_the_text_goes_in_a_file_never_in_argv(fake_say):
    secret = "the quick brown fox"

    assert say.synthesize(secret, Settings(voice_id=None)) == b"m4a-audio"

    argv = fake_say[0]
    assert secret not in argv
    assert not any(secret in arg for arg in argv)
    # -f names a real file, and that file holds the text.
    source = Path(argv[argv.index("-f") + 1])
    assert source.name == "in.txt"


def test_the_voice_and_speed_reach_the_command_line(fake_say):
    say.synthesize("hi", Settings(voice_id="Samantha", speed=1.2))

    argv = fake_say[0]
    assert argv[:3] == ["say", "-v", "Samantha"]
    assert argv[argv.index("-r") + 1] == "210"  # round(175 * 1.2)


def test_no_voice_and_no_speed_means_no_flags(fake_say):
    say.synthesize("hi", Settings(voice_id=None))

    argv = fake_say[0]
    assert "-v" not in argv
    assert "-r" not in argv


@pytest.mark.parametrize(
    "voice",
    ["--interactive", "x\n", "-v", "; rm -rf /", "Samantha; say hi", "\x00Sam"],
)
def test_a_dangerous_voice_never_reaches_the_process(fake_say, voice):
    with pytest.raises(ProviderContentError, match=r"\[providers.say\] voice"):
        say.synthesize("hi", Settings(voice_id=voice))

    assert fake_say == []  # rejected before anything was run


def test_a_failing_say_reports_its_first_stderr_line(monkeypatch):
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 1, stdout="", stderr="Voice not found\nsecond line\n"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ProviderTransientError, match="say: Voice not found"):
        say.synthesize("hi", Settings(voice_id=None))


def test_an_empty_output_file_is_a_failure(monkeypatch):
    def fake_run(argv, **kwargs):
        Path(argv[argv.index("-o") + 1]).write_bytes(b"")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ProviderTransientError, match="no audio"):
        say.synthesize("hi", Settings(voice_id=None))


def test_a_timeout_is_transient(monkeypatch):
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 300)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ProviderTransientError, match="timed out"):
        say.synthesize("hi", Settings(voice_id=None))


def test_list_voices_keeps_two_word_names_whole(fake_say):
    voices = say.list_voices()

    assert voices == [
        {"id": "Albert", "name": "Albert (en_US)"},
        {"id": "Bad News", "name": "Bad News (en_US)"},
        {"id": "Alice", "name": "Alice (it_IT)"},
    ]
    assert fake_say[0] == ["say", "-v", "?"]
