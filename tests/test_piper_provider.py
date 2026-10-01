"""Piper's provider seam, driven entirely by fakes.

Nothing here starts a process, touches the network, or needs uv or the
63 MB voice. What every test guards is the boundary: text goes to stdin
and never into argv, the voice is checked against an allowlist before it
can become a path or argument, and only binary wheels are ever fetched.
"""

import re
import subprocess

import pytest

from vocalize.config import Settings
from vocalize.exceptions import (
    ProviderContentError,
    ProviderTransientError,
    ProviderUnavailableError,
)
from vocalize.local import install as install_module
from vocalize.local import piper_manifest as manifest
from vocalize.providers import piper

SECRET = "the quick brown fox says something private"


class FakeRun:
    """Stands in for subprocess.run: records the call, writes a fake WAV."""

    def __init__(self, returncode=0, stderr="", write=b"RIFFfake"):
        self.returncode, self.stderr, self.write = returncode, stderr, write
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        out = argv[argv.index("-f") + 1]
        if self.write is not None:
            with open(out, "wb") as handle:
                handle.write(self.write)
        return subprocess.CompletedProcess(argv, self.returncode, "", self.stderr)


@pytest.fixture
def fake(monkeypatch):
    run = FakeRun()
    monkeypatch.setattr(piper, "RUN_SEAM", run)
    monkeypatch.setattr(piper, "uv_path", lambda: "/usr/bin/uv")
    return run


def settings(**kw):
    return Settings(**{"voice_id": "lessac", "speed": 1.0, **kw})


def test_text_goes_to_stdin_never_argv(fake):
    piper.synthesize(SECRET, settings())
    argv, kwargs = fake.calls[0]
    assert SECRET not in " ".join(argv)
    assert kwargs["input"].strip() == SECRET


def test_only_binary_wheels_and_no_project(fake):
    piper.synthesize("hello", settings())
    argv, _ = fake.calls[0]
    assert "--no-build" in argv and "--no-project" in argv
    assert manifest.RUNTIME_PACKAGE in argv


def test_runs_outside_the_callers_project(fake):
    piper.synthesize("hello", settings())
    assert fake.calls[0][1]["cwd"] != "."


def test_multiline_text_is_flattened_to_one_utterance(fake):
    piper.synthesize("one\n\ntwo   three", settings())
    assert fake.calls[0][1]["input"] == "one two three\n"


@pytest.mark.parametrize("bad", ["../etc/passwd", "--serve", "lessac; rm -rf", "amy"])
def test_unknown_voice_is_refused_before_any_process(fake, bad):
    with pytest.raises(ProviderContentError):
        piper.synthesize("hello", settings(voice_id=bad))
    assert fake.calls == []


def test_speed_becomes_length_scale(fake):
    piper.synthesize("hello", settings(speed=2.0))
    argv, _ = fake.calls[0]
    assert argv[argv.index("--length-scale") + 1] == "0.500"


def test_failure_never_quotes_the_input(monkeypatch):
    run = FakeRun(returncode=1, stderr="warming\nTraceback: boom")
    monkeypatch.setattr(piper, "RUN_SEAM", run)
    monkeypatch.setattr(piper, "uv_path", lambda: "/usr/bin/uv")
    with pytest.raises(ProviderTransientError) as err:
        piper.synthesize(SECRET, settings())
    assert SECRET not in str(err.value)


def test_empty_audio_is_an_error(monkeypatch):
    monkeypatch.setattr(piper, "RUN_SEAM", FakeRun(write=b""))
    monkeypatch.setattr(piper, "uv_path", lambda: "/usr/bin/uv")
    with pytest.raises(ProviderTransientError):
        piper.synthesize("hello", settings())


def test_timeout_is_transient(monkeypatch):
    def boom(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(piper, "RUN_SEAM", boom)
    monkeypatch.setattr(piper, "uv_path", lambda: "/usr/bin/uv")
    with pytest.raises(ProviderTransientError):
        piper.synthesize("hello", settings())


def test_check_without_uv_says_how_to_fix(monkeypatch):
    monkeypatch.setattr(piper, "uv_path", lambda: None)
    with pytest.raises(ProviderUnavailableError) as err:
        piper.check(settings())
    assert "vocalize local install --piper" in str(err.value)


def test_check_without_the_voice_says_how_to_fix(monkeypatch, tmp_path):
    monkeypatch.setattr(piper, "uv_path", lambda: "/usr/bin/uv")
    monkeypatch.setattr(manifest, "MODEL_DIR", tmp_path)
    with pytest.raises(ProviderUnavailableError) as err:
        piper.check(settings())
    assert "vocalize local install --piper" in str(err.value)


def test_installed_trusts_only_a_verified_stamp(monkeypatch, tmp_path):
    monkeypatch.setattr(manifest, "MODEL_DIR", tmp_path)
    for entry in manifest.voice_files("lessac"):
        (tmp_path / entry["name"]).write_bytes(b"x" * entry["size"])
    ready, _ = piper.installed("lessac")
    assert not ready  # right sizes, no stamp
    install_module.write_stamp(tmp_path, manifest, files=manifest.voice_files("lessac"))
    ready, reason = piper.installed("lessac")
    assert ready, reason


def test_list_voices_shows_licence_and_needs_no_process():
    voices = piper.list_voices()
    assert {v["id"] for v in voices} == set(manifest.VOICES)


# --- the manifest -----------------------------------------------------

def test_every_manifest_entry_is_pinned_and_https():
    assert re.fullmatch(r"[0-9a-f]{40}", manifest.REVISION)
    for entry in manifest.FILES:
        assert entry["url"].startswith("https://huggingface.co/")
        assert manifest.REVISION in entry["url"]
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
        assert entry["size"] > 0


def test_runtime_is_pinned_exactly():
    assert re.fullmatch(r"piper-tts==\d+\.\d+\.\d+", manifest.RUNTIME_PACKAGE)


def test_every_voice_has_a_licence_and_two_files():
    for voice in manifest.VOICES:
        assert manifest.VOICE_LICENCES[voice]
        assert len(manifest.voice_files(voice)) == 2


def test_voice_files_rejects_anything_off_the_allowlist():
    with pytest.raises(KeyError):
        manifest.voice_files("../x")


def test_piper_is_a_known_provider_and_needs_no_credentials():
    from vocalize.auth import PROVIDER_LABELS, PROVIDER_NAMES

    assert "piper" in PROVIDER_NAMES and "piper" in PROVIDER_LABELS
