"""Whisper always receives an explicit initial prompt and context setting."""

import importlib.util
import wave
from pathlib import Path
from typing import ClassVar

from vocalize import dictate
from vocalize.local import whisper_manifest as manifest


def load_worker():
    spec = importlib.util.spec_from_file_location("whisper_worker_prompt_test", manifest.worker_path())
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_wav(path):
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x00\x00" * 8000)


class Segment:
    text = " hello"


class Model:
    instances: ClassVar[list] = []

    def __init__(self, *_args, **_kwargs):
        self.calls = []
        self.instances.append(self)

    def transcribe(self, media, **kwargs):
        self.calls.append((media, kwargs))
        return [Segment()]


def test_plain_and_segments_paths_pass_prompt_and_disable_context(tmp_path, monkeypatch, capsys):
    worker = load_worker()
    Model.instances = []
    monkeypatch.setattr(worker, "_model_class", lambda: Model)
    wav = tmp_path / "clip.wav"
    write_wav(wav)

    assert worker.main(["--model", "m.bin", "--initial-prompt", "Vocabulary: pyproject.", "--transcribe", str(wav)]) == 0
    capsys.readouterr()
    assert worker.main(["--model", "m.bin", "--initial-prompt", "Vocabulary: pyproject.", "--segments", "--transcribe", str(wav)]) == 0

    calls = [kwargs for model in Model.instances for _media, kwargs in model.calls]
    assert len(calls) == 2  # never vacuous: one plain call, one segments call
    for kwargs in calls:
        assert kwargs["initial_prompt"] == "Vocabulary: pyproject."
        assert kwargs["no_context"] is True


class OldModel(Model):
    """A pywhispercpp without `new_segment_callback`: the TypeError path."""

    def transcribe(self, media, **kwargs):
        if "new_segment_callback" in kwargs:
            raise TypeError("unexpected keyword argument 'new_segment_callback'")
        return super().transcribe(media, **kwargs)


def test_the_segments_fallback_for_an_older_whisper_passes_the_prompt_too(tmp_path, monkeypatch, capsys):
    worker = load_worker()
    Model.instances = []
    monkeypatch.setattr(worker, "_model_class", lambda: OldModel)
    wav = tmp_path / "clip.wav"
    write_wav(wav)

    assert worker.main(["--model", "m.bin", "--initial-prompt", "Vocabulary: sha256.", "--segments", "--transcribe", str(wav)]) == 0

    calls = Model.instances[-1].calls
    assert len(calls) == 1  # only the fallback call reached the model
    assert calls[0][1]["initial_prompt"] == "Vocabulary: sha256."
    assert calls[0][1]["no_context"] is True
    assert '"ok": true' in capsys.readouterr().out


def test_selftest_passes_an_empty_prompt(tmp_path, monkeypatch):
    worker = load_worker()
    Model.instances = []
    monkeypatch.setattr(worker, "_model_class", lambda: Model)
    monkeypatch.setattr(worker, "_write_selftest_wav", lambda path: write_wav(Path(path)))

    assert worker.main(["--model", str(tmp_path / "m.bin"), "--selftest"]) == 0
    assert Model.instances[-1].calls[-1][1]["initial_prompt"] == ""
    assert Model.instances[-1].calls[-1][1]["no_context"] is True


def test_worker_argv_always_has_one_prompt_argument():
    empty = dictate.worker_argv("/opt/uv", Path("/tmp/take.wav"), {})
    configured = dictate.worker_argv(
        "/opt/uv", Path("/tmp/take.wav"), {"vocabulary": ["pyproject", "sha256"]}
    )
    flagged = dictate.worker_argv(
        "/opt/uv", Path("/tmp/take.wav"), {"vocabulary": ["-not-an-option"]}
    )

    assert "--initial-prompt=" in empty
    assert "--initial-prompt=Vocabulary: pyproject, sha256." in configured
    assert "--initial-prompt=Vocabulary: -not-an-option." in flagged
