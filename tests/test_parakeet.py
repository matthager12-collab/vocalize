"""Parakeet engine: manifest, worker input checks, dispatch, install plan.

No model, no uv and no network. The guarded boundaries: only a 16 kHz mono
16-bit WAV reaches the model, the worker is run with binary wheels only and
outside the caller's project, the audio path is the only per-take argument,
and an engine name is validated before it picks a code path.
"""

import json
import os
import re
import subprocess
import wave

import pytest
from click.testing import CliRunner

from vocalize import config, dictate
from vocalize.cli import main
from vocalize.exceptions import ConfigError, DictationError
from vocalize.local import install as install_module
from vocalize.local import parakeet_manifest as manifest
from vocalize.local import parakeet_worker as worker


def write_wav(path, rate=16000, channels=1, width=2, frames=b"\x00\x01" * 160):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(frames)
    return path


@pytest.fixture
def supported(monkeypatch):
    monkeypatch.setattr(manifest, "supported", lambda: True)


@pytest.fixture
def installed_model(monkeypatch, tmp_path, supported):
    monkeypatch.setattr(manifest, "MODEL_DIR", tmp_path / "pk")
    for entry in manifest.FILES:
        path = manifest.MODEL_DIR / entry["name"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * entry["size"] if entry["size"] < 10**6 else b"")
        if entry["size"] >= 10**6:
            with open(path, "wb") as f:
                f.truncate(entry["size"])
    install_module.write_stamp(manifest=manifest)
    monkeypatch.setattr(dictate, "_uv_or_raise", lambda: "/usr/bin/uv")


# --- manifest ----------------------------------------------------------

def test_manifest_is_pinned_https_and_hashed():
    assert re.fullmatch(r"[0-9a-f]{40}", manifest.REVISION)
    for entry in manifest.FILES:
        assert entry["url"].startswith("https://huggingface.co/")
        assert manifest.REVISION in entry["url"]
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
    assert re.fullmatch(r"parakeet-mlx==\d+\.\d+\.\d+", manifest.RUNTIME_PACKAGE)


def test_runtime_is_binary_wheels_only_and_isolated():
    argv = manifest.runtime_argv()
    assert "--no-build" in argv and "--no-project" in argv


# --- worker input checks ----------------------------------------------

def test_worker_reads_the_expected_format(tmp_path):
    assert worker.read_wav(str(write_wav(tmp_path / "a.wav"))) == b"\x00\x01" * 160


@pytest.mark.parametrize("kw", [{"rate": 44100}, {"channels": 2}, {"width": 1}])
def test_worker_refuses_any_other_format(tmp_path, kw):
    with pytest.raises(ValueError):
        worker.read_wav(str(write_wav(tmp_path / "a.wav", **kw)))


def test_worker_refuses_a_non_wav_and_a_symlink(tmp_path):
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"not a wav at all")
    with pytest.raises(ValueError):
        worker.read_wav(str(junk))
    real = write_wav(tmp_path / "real.wav")
    link = tmp_path / "link.wav"
    os.symlink(real, link)
    with pytest.raises(ValueError):
        worker.read_wav(str(link))


def test_worker_errors_are_one_short_line():
    line = worker._one_line(RuntimeError("a\nb\n" + "x" * 500))
    assert "\n" not in line and len(line) <= worker._MAX_ERROR_CHARS


# --- engine choice -----------------------------------------------------

def test_auto_is_whisper_when_parakeet_is_not_installed(supported):
    assert dictate.engine_for({"engine": "auto"}) == "whisper"


def test_auto_is_parakeet_when_installed_and_supported(installed_model):
    assert dictate.engine_for({"engine": "auto"}) == "parakeet"


def test_auto_is_whisper_on_an_unsupported_machine(installed_model, monkeypatch):
    monkeypatch.setattr(manifest, "supported", lambda: False)
    assert dictate.engine_for({"engine": "auto"}) == "whisper"


def test_a_pinned_engine_is_honoured(supported):
    assert dictate.engine_for({"engine": "whisper"}) == "whisper"
    assert dictate.engine_for({"engine": "parakeet"}) == "parakeet"


@pytest.mark.parametrize("bad", ["", "../x", "gpt", 5])
def test_config_rejects_an_unknown_engine(bad):
    with pytest.raises(ConfigError):
        config.resolve_stt({"stt": {"engine": bad}})


def test_engine_defaults_to_auto():
    assert config.resolve_stt({})["engine"] == "auto"


# --- transcribe dispatch ----------------------------------------------

def test_transcribe_runs_the_worker_with_only_the_path(installed_model, tmp_path, monkeypatch):
    wav = write_wav(tmp_path / "take.wav")
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs
        return subprocess.CompletedProcess(argv, 0, json.dumps({"ok": True, "text": " hello world "}) + "\n", "")

    monkeypatch.setattr(dictate.subprocess, "run", fake_run)
    assert dictate.transcribe(wav, {"engine": "parakeet"}) == "hello world"
    assert seen["argv"][-2:] == ["--transcribe", str(wav)]
    assert "--no-build" in seen["argv"]
    assert seen["kwargs"]["cwd"] != "."


@pytest.mark.parametrize("stdout,code", [("", 0), ("garbage", 0), (json.dumps({"ok": False, "error": "x"}), 0), ("", 1)])
def test_a_bad_worker_reply_is_a_dictation_error(installed_model, tmp_path, monkeypatch, stdout, code):
    wav = write_wav(tmp_path / "take.wav")
    monkeypatch.setattr(
        dictate.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, code, stdout, ""),
    )
    with pytest.raises(DictationError):
        dictate.transcribe(wav, {"engine": "parakeet"})


def test_not_installed_says_how_to_fix(supported, tmp_path):
    wav = write_wav(tmp_path / "take.wav")
    with pytest.raises(DictationError) as err:
        dictate.transcribe(wav, {"engine": "parakeet"})
    assert "vocalize local install --stt" in str(err.value)


def test_unsupported_machine_is_refused_with_the_way_out(tmp_path, monkeypatch):
    monkeypatch.setattr(manifest, "supported", lambda: False)
    wav = write_wav(tmp_path / "take.wav")
    with pytest.raises(DictationError) as err:
        dictate.transcribe(wav, {"engine": "parakeet"})
    assert "whisper" in str(err.value)


# --- install command ---------------------------------------------------

def test_stt_install_defaults_to_parakeet_on_apple_silicon(supported, monkeypatch):
    monkeypatch.setattr("vocalize.local.uv_path", lambda: "/usr/bin/uv")
    result = CliRunner().invoke(main, ["local", "install", "--stt"], input="n\n")
    assert "model.safetensors" in result.output
    assert "CC-BY-4.0" in result.output
    assert "nothing downloaded" in result.output.lower()


def test_stt_install_can_still_pick_whisper(supported, monkeypatch):
    monkeypatch.setattr("vocalize.local.uv_path", lambda: "/usr/bin/uv")
    result = CliRunner().invoke(main, ["local", "install", "--stt", "--engine", "whisper"], input="n\n")
    assert "ggml-" in result.output


def test_engine_flag_needs_stt():
    result = CliRunner().invoke(main, ["local", "install", "--engine", "parakeet"])
    assert result.exit_code != 0 and "--stt" in result.output


def test_model_flag_conflicts_with_parakeet(supported, monkeypatch):
    monkeypatch.setattr("vocalize.local.uv_path", lambda: "/usr/bin/uv")
    result = CliRunner().invoke(main, ["local", "install", "--stt", "--engine", "parakeet", "--model", "small.en"])
    assert result.exit_code != 0
