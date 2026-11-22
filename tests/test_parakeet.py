"""Parakeet engine: manifest, worker input checks, dispatch, install plan.

No model, no uv and no network. The guarded boundaries: only a 16 kHz mono
16-bit WAV reaches the model, the worker is run with binary wheels only and
outside the caller's project, the audio path is the only per-take argument,
and an engine name is validated before it picks a code path.
"""

import json
import math
import os
import re
import subprocess
import sys
import types
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
    _fake_locked_runtime(monkeypatch, tmp_path, manifest)
    monkeypatch.setattr(dictate, "_uv_or_raise", lambda: "/usr/bin/uv")


def _fake_locked_runtime(monkeypatch, tmp_path, *manifests):
    """What `local install` leaves for a locked engine: an environment for
    the current lock and its marker. Nothing is installed in it."""
    from vocalize.local import locks

    monkeypatch.setattr(locks, "ENVS_DIR", tmp_path / "envs")
    for m in manifests:
        python = locks.env_python(m.ENGINE, m.lock_file())
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_bytes(b"")
        locks.write_marker(m.ENGINE, m.lock_file())


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


# --- long recordings: chunked, timestamped (T-32) ------------------------

FAKE_ENGINE = '''
calls = []

def load_audio(path, sampling_rate, dtype=None):
    raise AssertionError("ffmpeg loader must never run")

class Model:
    def __init__(self, result):
        self.result = result

    def transcribe(self, path, *, dtype=None, chunk_duration=None, overlap_duration=15.0,
                   chunk_callback=None):
        audio = load_audio(path, 16000, dtype)
        calls.append({"audio": audio, "dtype": dtype, "chunk": chunk_duration,
                      "overlap": overlap_duration})
        if chunk_callback is not None:
            chunk_callback(len(audio), len(audio))
        return self.result
'''


def _ns(**kw):
    return types.SimpleNamespace(**kw)


@pytest.fixture
def engine(monkeypatch):
    """A stand-in for parakeet_mlx.parakeet: no mlx, no numpy, no ffmpeg."""
    module = types.ModuleType("fake_parakeet_engine")
    exec(FAKE_ENGINE, module.__dict__)  # noqa: S102 -- fixed test source
    fake_mx = types.ModuleType("mlx.core")
    fake_mx.float32 = "float32"
    fake_mlx = types.ModuleType("mlx")
    fake_mlx.core = fake_mx
    monkeypatch.setitem(sys.modules, "mlx", fake_mlx)
    monkeypatch.setitem(sys.modules, "mlx.core", fake_mx)
    monkeypatch.setattr(worker, "_audio", lambda frames: [0.0] * (len(frames) // 2))
    return module



def test_chunk_constants():
    assert worker.CHUNK_SECONDS == 120 and worker.OVERLAP_SECONDS == 15


def test_segments_mode_passes_chunking_and_never_runs_ffmpeg(engine):
    result = _ns(text=" Hello there.  General Kenobi. ", sentences=[
        _ns(text=" Hello there.", start=0.25, end=1.5),
        _ns(text=" General Kenobi.", start=1.75, end=2.9),
    ])
    progress = []
    frames = b"\x00\x00" * 16000 * 3
    reply = worker.transcribe_segments(engine.Model(result), frames, progress.append)
    [call] = engine.calls
    assert call["chunk"] == worker.CHUNK_SECONDS and call["overlap"] == worker.OVERLAP_SECONDS
    assert call["dtype"] == "float32" and len(call["audio"]) == 16000 * 3
    assert reply == {"ok": True, "text": "Hello there. General Kenobi.", "segments": [
        {"start": 0.25, "end": 1.5, "text": "Hello there."},
        {"start": 1.75, "end": 2.9, "text": "General Kenobi."},
    ]}
    assert progress == [3.0]
    # The library's own loader is back once the call is done.
    with pytest.raises(AssertionError, match="ffmpeg"):
        engine.load_audio("x", 16000)


@pytest.mark.parametrize("sentences", [
    None, [], [_ns(text="Hello there.")], [_ns(text="Hello there.", start=math.nan, end=1.0)],
    [_ns(text="Hello there.", start=True, end=1.0)], "not a list",
])
def test_no_sentence_timing_falls_back_to_one_segment(engine, sentences):
    result = _ns(text="Hello there.", sentences=sentences)
    reply = worker.transcribe_segments(engine.Model(result), b"\x00\x00" * 32000)
    assert reply["segments"] == [{"start": 0.0, "end": 2.0, "text": "Hello there."}]
    assert reply["text"] == "Hello there."


def test_segment_times_are_ordered_and_bounded(engine):
    result = _ns(text="b a", sentences=[
        _ns(text="b", start=1.0, end=0.5), _ns(text="a", start=-1.0, end=9.0), _ns(text="  ", start=0, end=0),
    ])
    reply = worker.transcribe_segments(engine.Model(result), b"\x00\x00" * 32000)
    # The engine's order is kept; times are clamped, never re-sorted.
    assert reply["segments"] == [
        {"start": 1.0, "end": 1.0, "text": "b"}, {"start": 1.0, "end": 2.0, "text": "a"},
    ]


def test_overlapping_times_at_a_chunk_join_keep_the_spoken_order(engine):
    """A real 10 minute file: at the join of two 120 s chunks the engine listed
    a sentence starting at 436.0 s before one starting at 434.92 s. Its text
    order was right; sorting by start swapped the two sentences."""
    spoken = [
        _ns(text="Before the join.", start=430.1, end=436.2),
        _ns(text="Spoken second.", start=436.0, end=438.4),
        _ns(text="Spoken third.", start=434.92, end=437.5),
        _ns(text="After the join.", start=439.0, end=600.5),
    ]
    result = _ns(text=" ".join(s.text for s in spoken), sentences=spoken)
    reply = worker.transcribe_segments(engine.Model(result), b"\x00\x00" * 16000 * 600)
    segments = reply["segments"]
    assert [s["text"] for s in segments] == [s.text for s in spoken]
    starts = [s["start"] for s in segments]
    assert starts == sorted(starts) and starts == [430.1, 436.0, 436.0, 439.0]
    assert all(s["start"] <= s["end"] <= 600.0 for s in segments)
    assert segments[-1]["end"] == 600.0


def test_empty_result_has_no_segments(engine):
    reply = worker.transcribe_segments(engine.Model(_ns(text="", sentences=[])), b"\x00\x00" * 160)
    assert reply == {"ok": True, "text": "", "segments": []}


def test_an_engine_without_the_loader_is_refused(engine):
    del engine.load_audio
    with pytest.raises(RuntimeError, match="unexpected shape"):
        worker.transcribe_segments(engine.Model(_ns(text="x", sentences=[])), b"\x00\x00" * 160)


def test_segments_main_prints_progress_then_one_reply(engine, tmp_path, monkeypatch, capsys):
    folder = tmp_path / "model"
    folder.mkdir()
    wav = write_wav(tmp_path / "take.wav", frames=b"\x00\x00" * 16000)
    result = _ns(text="Hi.", sentences=[_ns(text="Hi.", start=0.0, end=0.5)])
    monkeypatch.setattr(worker, "_load", lambda f: engine.Model(result))
    assert worker.main(["--model", str(folder), "--transcribe", str(wav), "--segments"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines == [{"progress": 1.0}, {"ok": True, "text": "Hi.",
                                         "segments": [{"start": 0.0, "end": 0.5, "text": "Hi."}]}]


def test_segments_mode_keeps_the_refusals(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(worker, "_load", lambda f: pytest.fail("no model before the checks"))
    folder = tmp_path / "model"
    folder.mkdir()
    bad = write_wav(tmp_path / "bad.wav", rate=44100)
    assert worker.main(["--model", str(folder), "--transcribe", str(bad), "--segments"]) == 2
    assert worker.main(["--model", "relative", "--transcribe", str(bad), "--segments"]) == 2
    err = capsys.readouterr().err
    assert "refused" in err and str(bad) not in err


def test_segments_needs_transcribe(tmp_path):
    with pytest.raises(SystemExit):
        worker.parse_args(["--model", str(tmp_path), "--selftest", "--segments"])


def test_plain_mode_is_unchanged(engine, tmp_path, monkeypatch, capsys):
    folder = tmp_path / "model"
    folder.mkdir()
    wav = write_wav(tmp_path / "take.wav")
    monkeypatch.setattr(worker, "_load", lambda f: object())
    monkeypatch.setattr(worker, "transcribe", lambda model, frames: "plain text")
    assert worker.main(["--model", str(folder), "--transcribe", str(wav)]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "text": "plain text"}


def test_segment_reply_is_accepted_by_the_notes_reader(tmp_path):
    """The worker's segment line is exactly what notes._transcribe_long reads."""
    from vocalize import notes

    reply = {"ok": True, "text": "Hi. There.", "segments": [
        {"start": 0.0, "end": 0.5, "text": "Hi."}, {"start": 0.5, "end": 1.0, "text": "There."},
    ]}
    script = tmp_path / "fake_worker.py"
    script.write_text(
        "import json\nprint(json.dumps({'progress': 1.0}))\n"
        f"print(json.dumps({reply!r}))\n", encoding="utf-8",
    )
    text, segments = notes._transcribe_long([sys.executable, str(script)], 1.0)
    assert text == "Hi. There." and segments == reply["segments"]
