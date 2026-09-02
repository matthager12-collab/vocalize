"""The standalone worker: its import discipline, and its transcribe/selftest protocol.

The worker runs under uv's Python, not this one, so pywhispercpp and
numpy are not installed here. That is exactly what the first test checks
— the file has to import cleanly in an environment that has neither, or
the isolation the whole design rests on is not real.
"""

import ast
import importlib.util
import json
import wave
from typing import ClassVar

import pytest

from vocalize.local import whisper_manifest as manifest

WORKER_PATH = manifest.worker_path()
WORKER_SOURCE = WORKER_PATH.read_text(encoding="utf-8")
TREE = ast.parse(WORKER_SOURCE)

# Everything the module body is allowed to pull in.
STDLIB_AT_MODULE_LEVEL = {"__future__", "argparse", "json", "sys", "wave"}


def module_level_imports():
    names = set()
    for node in TREE.body:
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    return names


def imports_inside_functions():
    names = {}
    for parent in ast.walk(TREE):
        if not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(parent):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.setdefault(alias.name.split(".")[0], parent.name)
            elif isinstance(node, ast.ImportFrom):
                names.setdefault((node.module or "").split(".")[0], parent.name)
    return names


# --- import discipline ------------------------------------------------


def test_the_module_body_imports_nothing_but_stdlib():
    assert module_level_imports() <= STDLIB_AT_MODULE_LEVEL


def test_pywhispercpp_and_numpy_are_imported_inside_functions_only():
    assert "pywhispercpp" not in module_level_imports()
    assert "numpy" not in module_level_imports()
    nested = imports_inside_functions()
    assert "pywhispercpp" in nested
    assert "numpy" in nested


def test_the_worker_never_imports_vocalize():
    assert "vocalize" not in module_level_imports()
    assert "vocalize" not in imports_inside_functions()


def test_the_file_imports_here_where_neither_package_exists():
    with pytest.raises(ImportError):
        importlib.import_module("pywhispercpp")

    assert load_worker() is not None


# --- the protocol -----------------------------------------------------


def load_worker():
    spec = importlib.util.spec_from_file_location("whisper_worker_under_test", WORKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StubSegment:
    def __init__(self, text):
        self.text = text


class StubModel:
    """Stands in for pywhispercpp.model.Model. Records what it was built
    and asked to transcribe with."""

    instances: ClassVar[list] = []

    def __init__(self, model_path, n_threads=None, **kwargs):
        self.model_path = model_path
        self.n_threads = n_threads
        self.calls = []
        StubModel.instances.append(self)

    def transcribe(self, media, language=None, **params):
        self.calls.append((media, language))
        if "boom" in media:
            raise RuntimeError("whisper\nfell over\nmid-decode")
        return [StubSegment(" hello"), StubSegment(" there")]


class BrokenModel:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("no such file:\nggml-base.en.bin")


@pytest.fixture
def worker(monkeypatch):
    module = load_worker()
    monkeypatch.setattr(module, "_model_class", lambda: StubModel)
    StubModel.instances = []
    return module


def write_wav(path, frames=b"\x00\x00" * 8000, channels=1, width=2, rate=16000):
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(rate)
        writer.writeframes(frames)


def run_transcribe(worker, wav, language="en"):
    code = worker.main(["--model", "m.bin", "--language", language, "--transcribe", str(wav)])
    return code


def test_a_good_transcription_prints_exactly_one_ok_line(worker, tmp_path, capsys):
    wav = tmp_path / "clip.wav"
    write_wav(wav)

    code = run_transcribe(worker, wav)

    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert code == 0
    assert len(lines) == 1
    assert json.loads(lines[0]) == {"ok": True, "text": "hello there"}


def test_the_model_path_and_language_reach_the_call(worker, tmp_path):
    wav = tmp_path / "clip.wav"
    write_wav(wav)
    model_path = str(tmp_path / "ggml-small.en.bin")

    worker.main(["--model", model_path, "--language", "en-gb", "--transcribe", str(wav)])

    assert StubModel.instances[-1].model_path == model_path
    assert StubModel.instances[-1].calls == [(str(wav), "en-gb")]


def test_a_transcription_error_is_reported_as_one_line(worker, tmp_path, capsys):
    wav = tmp_path / "boom.wav"
    write_wav(wav)

    code = run_transcribe(worker, wav)

    reply = json.loads(capsys.readouterr().out.strip())
    assert code == 0
    assert reply["ok"] is False
    assert "\n" not in reply["error"]
    assert reply["error"] == "whisper fell over mid-decode"


# --- the malformed-WAV negative test (input WAV must be 16k/mono/16-bit) --


def test_a_file_that_is_not_a_wav_at_all_is_refused(worker, tmp_path, capsys):
    bad = tmp_path / "not-a-wav.bin"
    bad.write_bytes(b"this is definitely not a RIFF/WAVE file")

    code = run_transcribe(worker, bad)

    reply = json.loads(capsys.readouterr().out.strip())
    assert code == 0
    assert reply["ok"] is False
    assert "WAV" in reply["error"]
    assert StubModel.instances[-1].calls == []  # never reached whisper.cpp


def test_a_wav_with_the_wrong_sample_rate_is_refused(worker, tmp_path, capsys):
    wav = tmp_path / "wrong-rate.wav"
    write_wav(wav, rate=44100)

    code = run_transcribe(worker, wav)

    reply = json.loads(capsys.readouterr().out.strip())
    assert code == 0
    assert reply["ok"] is False
    assert "44100" in reply["error"]


def test_a_wav_with_the_wrong_channel_count_is_refused(worker, tmp_path, capsys):
    wav = tmp_path / "stereo.wav"
    write_wav(wav, channels=2, frames=b"\x00\x00\x00\x00" * 100)

    code = run_transcribe(worker, wav)

    reply = json.loads(capsys.readouterr().out.strip())
    assert code == 0
    assert reply["ok"] is False


def test_a_wav_with_the_wrong_sample_width_is_refused(worker, tmp_path, capsys):
    wav = tmp_path / "8bit.wav"
    write_wav(wav, width=1, frames=b"\x00" * 8000)

    code = run_transcribe(worker, wav)

    reply = json.loads(capsys.readouterr().out.strip())
    assert code == 0
    assert reply["ok"] is False


# --- the one-shot modes -----------------------------------------------


def test_a_model_that_will_not_load_still_prints_one_json_line_in_transcribe_mode(
    worker, monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(worker, "_model_class", lambda: BrokenModel)
    wav = tmp_path / "clip.wav"
    write_wav(wav)

    code = run_transcribe(worker, wav)

    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert code == 0
    assert len(lines) == 1
    reply = json.loads(lines[0])
    assert reply["ok"] is False
    assert "\n" not in reply["error"]


def test_a_model_that_will_not_load_exits_one_with_a_single_stderr_line_in_selftest_mode(
    worker, monkeypatch, capsys
):
    monkeypatch.setattr(worker, "_model_class", lambda: BrokenModel)

    code = worker.main(["--model", "m.bin", "--selftest"])

    err = capsys.readouterr().err
    assert code == 1
    assert len(err.strip().splitlines()) == 1
    assert "could not load the model" in err


def test_selftest_says_ok(worker, capsys):
    pytest.importorskip("numpy")  # only installed inside uv's runtime

    code = worker.main(["--model", "m.bin", "--selftest"])

    assert code == 0
    assert capsys.readouterr().out.strip() == "ok"


def test_a_failing_transcription_inside_selftest_exits_one(worker, monkeypatch, capsys):
    pytest.importorskip("numpy")

    class FailsToTranscribe(StubModel):
        def transcribe(self, media, language=None, **params):
            raise RuntimeError("espeak-ng data is missing")

    monkeypatch.setattr(worker, "_model_class", lambda: FailsToTranscribe)

    code = worker.main(["--model", "m.bin", "--selftest"])

    assert code == 1
    assert "selftest failed" in capsys.readouterr().err


def test_a_mode_is_required(worker):
    with pytest.raises(SystemExit):
        worker.parse_args(["--model", "m.bin"])


def test_transcribe_and_selftest_are_mutually_exclusive(worker, tmp_path):
    wav = tmp_path / "clip.wav"
    write_wav(wav)
    with pytest.raises(SystemExit):
        worker.parse_args(["--model", "m.bin", "--transcribe", str(wav), "--selftest"])
