"""Supply-chain review conditions, enforced inside the engine workers.

Each engine package was adopted with conditions (kokoro-onnx 0.6.1,
pywhispercpp 1.5.1, parakeet-mlx 0.5.2, mlx-lm 0.31.3). None of those
packages is installed here: every engine module is faked, and each test
checks that the worker refuses before the fake would be reached, or that
the fake receives only what the review allows.

Every refusal exits 2 with one fixed stderr line that names no path and
never repeats the user's text.
"""

import ast
import ctypes
import importlib.util
import io
import json
import logging
import os
import sys
import types
import wave
from pathlib import Path
from typing import ClassVar

import pytest

from vocalize.local import runtime

LOCAL = Path(__file__).resolve().parent.parent / "vocalize" / "local"
CANARY = "CANARY-7f3a-private-words"


def load(name):
    spec = importlib.util.spec_from_file_location(f"{name}_hardening_under_test", LOCAL / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_wav(path, rate=16000, channels=1, width=2, frames=b"\x00\x01" * 160):
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(rate)
        writer.writeframes(frames)
    return path


def assert_refused(code, captured, *private):
    assert code == 2
    assert captured.out == ""
    lines = captured.err.strip().splitlines()
    assert len(lines) == 1 and "refused" in lines[0]
    for text in (CANARY, *private):
        assert str(text) not in captured.err
    # A refusal must never be mistaken for an uncached offline runtime.
    assert not runtime.offline_failure(captured.err)


def keyword_args(name):
    tree = ast.parse((LOCAL / f"{name}.py").read_text(encoding="utf-8"))
    return {kw.arg for node in ast.walk(tree) if isinstance(node, ast.Call) for kw in node.keywords}


# --- 1. the shared worker environment --------------------------------------

ENGINE_SWITCHES = ("LOG_LEVEL", "ONNX_PROVIDER", "PHONEMIZER_ESPEAK_LIBRARY", "GGML_BACKEND_PATH",
                   "HF_ENDPOINT", "HF_TOKEN", "MLXLM_USE_MODELSCOPE",
                   "PARAKEET_MODEL", "PARAKEET_ANYTHING", "PARAKEET_")


def test_the_environment_never_carries_an_engine_switch(monkeypatch):
    for name in ENGINE_SWITCHES:
        monkeypatch.setenv(name, CANARY)
    env = runtime.environment()
    for name in ENGINE_SWITCHES:
        assert name not in env
    assert not any(name.startswith("PARAKEET_") for name in env)
    assert CANARY not in json.dumps(env)


def test_the_environment_disables_hub_telemetry_beside_the_offline_switches(monkeypatch):
    monkeypatch.setenv("HF_HUB_DISABLE_TELEMETRY", "0")
    env = runtime.environment()
    assert env["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert env["HF_HUB_OFFLINE"] == env["TRANSFORMERS_OFFLINE"] == "1"


# --- 2. parakeet ------------------------------------------------------------


@pytest.fixture
def parakeet(monkeypatch):
    calls = []
    fake = types.ModuleType("parakeet_mlx")
    fake.from_pretrained = lambda path, *a, **kw: calls.append((path, a, kw)) or "model"
    monkeypatch.setitem(sys.modules, "parakeet_mlx", fake)
    module = load("parakeet_worker")
    monkeypatch.setattr(module, "transcribe", lambda model, frames: "hello")
    module.calls = calls
    return module


def test_parakeet_refuses_a_relative_model_folder(parakeet, tmp_path, capsys, monkeypatch):
    (tmp_path / "pk").mkdir()
    monkeypatch.chdir(tmp_path)
    wav = write_wav(tmp_path / "a.wav")
    code = parakeet.main(["--model", "pk", "--transcribe", str(wav)])
    assert_refused(code, capsys.readouterr(), tmp_path)
    assert parakeet.calls == []


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_parakeet_refuses_a_model_folder_that_is_not_a_directory(parakeet, tmp_path, capsys, kind):
    folder = tmp_path / f"pk-{CANARY}"
    if kind == "file":
        folder.write_bytes(b"x")
    wav = write_wav(tmp_path / "a.wav")
    code = parakeet.main(["--model", str(folder), "--transcribe", str(wav)])
    assert_refused(code, capsys.readouterr(), tmp_path)
    assert parakeet.calls == []


def test_parakeet_passes_only_the_resolved_absolute_folder(parakeet, tmp_path, capsys):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    os.symlink(real, link)
    wav = write_wav(tmp_path / "a.wav")
    code = parakeet.main(["--model", str(link), "--transcribe", str(wav)])
    assert code == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "text": "hello"}
    assert parakeet.calls == [(os.path.realpath(real), (), {})]


@pytest.mark.parametrize("name", [f"{CANARY}.mp3", f"{CANARY}.wav", "missing.wav"])
def test_parakeet_refuses_audio_that_is_not_a_readable_wav(parakeet, tmp_path, capsys, name):
    (tmp_path / "pk").mkdir()
    audio = tmp_path / name
    if name != "missing.wav":
        audio.write_bytes(CANARY.encode() * 4)
    code = parakeet.main(["--model", str(tmp_path / "pk"), "--transcribe", str(audio)])
    assert_refused(code, capsys.readouterr(), tmp_path)
    assert parakeet.calls == []


def test_parakeet_never_reaches_the_ffmpeg_loader_or_the_command_line_tool():
    tree = ast.parse((LOCAL / "parakeet_worker.py").read_text(encoding="utf-8"))
    names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    names |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
    modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert "load_audio" not in names | imported
    plain = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert "subprocess" not in modules | plain
    assert not any((module or "").startswith("parakeet_mlx.cli") for module in modules)


# --- 3. whisper -------------------------------------------------------------


class StubWhisper:
    built: ClassVar[list] = []

    def __init__(self, model, **kwargs):
        StubWhisper.built.append(model)
        self.calls = []

    def transcribe(self, media, **params):
        self.calls.append(media)
        return []


@pytest.fixture
def whisper(monkeypatch):
    module = load("whisper_worker")
    StubWhisper.built = []
    monkeypatch.setattr(module, "_model_class", lambda: StubWhisper)
    return module


@pytest.fixture
def ggml(tmp_path):
    path = tmp_path / "ggml-base.en.bin"
    path.write_bytes(b"ggml")
    return str(path)


@pytest.mark.parametrize("model", ["base.en", "ggml-base.en.bin", "/nowhere/ggml-base.en.bin"])
def test_whisper_never_builds_a_model_from_a_name_or_missing_file(whisper, tmp_path, capsys, model):
    wav = write_wav(tmp_path / "a.wav")
    code = whisper.main(["--model", model, "--transcribe", str(wav)])
    assert_refused(code, capsys.readouterr(), tmp_path, "/nowhere")
    assert StubWhisper.built == []


def test_whisper_refuses_a_model_folder_in_selftest_and_serve(whisper, tmp_path, capsys):
    assert_refused(whisper.main(["--model", str(tmp_path), "--selftest"]), capsys.readouterr(), tmp_path)
    code = whisper.main(["--model", "base.en", "--serve", "--socket", str(tmp_path / "s")])
    assert_refused(code, capsys.readouterr(), tmp_path)
    assert StubWhisper.built == []


@pytest.mark.parametrize("name", [f"{CANARY}.mp3", f"{CANARY}.WAV", f"{CANARY}.wav.m4a", "missing.wav"])
def test_whisper_refuses_audio_that_is_not_an_existing_wav_path(whisper, ggml, tmp_path, capsys, name):
    audio = tmp_path / name
    if name != "missing.wav":
        write_wav(audio)
    code = whisper.main(["--model", ggml, "--transcribe", str(audio)])
    assert_refused(code, capsys.readouterr(), tmp_path)
    assert StubWhisper.built == []


def test_whisper_warm_requests_for_a_non_wav_path_never_reach_the_model(whisper, tmp_path):
    audio = write_wav(tmp_path / "a.mp3")
    model = StubWhisper("/m")
    cancelled = types.SimpleNamespace(is_set=lambda: False)
    reply = whisper._warm_transcribe(model, {"op": "transcribe", "wav": str(audio)}, cancelled)
    assert reply == {"ok": False, "error": "bad-request"}
    assert model.calls == []


def test_whisper_one_shot_transcribe_never_hands_a_non_wav_path_to_the_model(whisper, tmp_path):
    audio = write_wav(tmp_path / "a.flac")
    model = StubWhisper("/m")
    assert whisper.transcribe(model, str(audio), "en")["ok"] is False
    assert model.calls == []


def test_whisper_builds_the_model_from_the_absolute_file(whisper, ggml, tmp_path, capsys):
    wav = write_wav(tmp_path / "a.wav")
    assert whisper.main(["--model", ggml, "--transcribe", str(wav)]) == 0
    assert StubWhisper.built == [ggml]


# --- 4. the cleanup model ---------------------------------------------------


class FakeTokenizer:
    def encode(self, text, **kwargs):
        return [1] * max(1, len(text.split()))


@pytest.fixture
def llm(monkeypatch):
    module = load("llm_worker")
    fake = types.SimpleNamespace(loads=[], generate=lambda *a, **kw: "Hello.")

    def load_model(*args, **kwargs):
        fake.loads.append((args, kwargs))
        return "model", FakeTokenizer()

    fake.load = load_model
    monkeypatch.setattr(module, "_mlx", lambda: fake)
    module.fake = fake
    return module


@pytest.fixture
def qwen(tmp_path):
    folder = tmp_path / "qwen"
    folder.mkdir()
    (folder / "config.json").write_text(json.dumps({"model_type": "qwen3_5"}), encoding="utf-8")
    (folder / "tokenizer_config.json").write_text(
        json.dumps({"tokenizer_class": "Qwen2TokenizerFast"}), encoding="utf-8")
    return folder


def run_once(llm, folder, monkeypatch, text="hello world"):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"system": "s", "text": text})))
    return llm.main(["--model-dir", str(folder), "--once"])


def edit(path, **extra):
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(extra)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("change", [
    lambda d: edit(d / "config.json", model_file=f"{CANARY}.py"),
    lambda d: edit(d / "config.json", auto_map={"AutoModel": CANARY}),
    lambda d: edit(d / "tokenizer_config.json", auto_map={"AutoTokenizer": CANARY}),
    lambda d: (d / "deep" / "er").mkdir(parents=True) or (d / "deep" / "er" / "x.py").write_text(CANARY),
    lambda d: (d / "modeling.PY").write_text(CANARY),
])
def test_llm_refuses_a_folder_that_could_run_code(llm, qwen, monkeypatch, capsys, change):
    change(qwen)
    code = run_once(llm, qwen, monkeypatch, text=CANARY)
    assert_refused(code, capsys.readouterr(), qwen)
    assert llm.fake.loads == []


@pytest.mark.parametrize("folder", ["qwen", "/nowhere/qwen"])
def test_llm_refuses_a_relative_or_missing_folder_before_it_could_be_a_hub_name(
    llm, qwen, monkeypatch, capsys, folder
):
    monkeypatch.chdir(qwen.parent)
    code = run_once(llm, folder, monkeypatch)
    assert_refused(code, capsys.readouterr(), qwen, "/nowhere")
    assert llm.fake.loads == []


def test_llm_refuses_in_selftest_and_serve_too(llm, qwen, monkeypatch, capsys, tmp_path):
    edit(qwen / "config.json", model_file="x.py")
    assert_refused(llm.main(["--model-dir", str(qwen), "--selftest"]), capsys.readouterr(), qwen)
    # Serve checks the offline variables first and exits silently without them,
    # so set both to reach the folder check this test is about.
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    code = llm.main(["--model-dir", str(qwen), "--serve", "--socket", str(tmp_path / "s")])
    assert_refused(code, capsys.readouterr(), qwen)
    assert llm.fake.loads == []


def test_llm_loads_the_resolved_folder_and_never_passes_trust_remote_code(llm, qwen, monkeypatch, capsys, tmp_path):
    link = tmp_path / "link"
    os.symlink(qwen, link)
    assert run_once(llm, link, monkeypatch) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "text": "Hello."}
    assert llm.fake.loads == [((os.path.realpath(qwen),), {})]
    assert "trust_remote_code" not in keyword_args("llm_worker")


def test_llm_uses_only_load_and_generate():
    tree = ast.parse((LOCAL / "llm_worker.py").read_text(encoding="utf-8"))
    used = {node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id in {"mlx", "mlx_lm_mod"}}
    assert used <= {"load", "generate", "stream_generate"}


def test_an_llm_engine_error_never_echoes_the_text(llm, qwen, monkeypatch, capsys):
    def generate(*args, **kwargs):
        raise RuntimeError(f"cannot continue {CANARY}")

    llm.fake.generate = generate
    assert run_once(llm, qwen, monkeypatch, text=CANARY) == 0
    captured = capsys.readouterr()
    assert CANARY not in captured.out + captured.err
    assert json.loads(captured.out)["ok"] is False


# --- 5. kokoro --------------------------------------------------------------


@pytest.fixture
def kokoro_env(monkeypatch, tmp_path):
    """Fake kokoro_onnx and espeakng_loader; espeak lib and data on disk."""
    built = []
    lib = tmp_path / "libespeak-ng.dylib"
    lib.write_bytes(b"lib")
    data = tmp_path / "espeak-ng-data"
    data.mkdir()

    class EspeakConfig:
        def __init__(self, lib_path=None, data_path=None):
            self.lib_path, self.data_path = lib_path, data_path

    class Kokoro:
        def __init__(self, model, voices, espeak_config=None):
            built.append((model, voices, espeak_config))

        def get_voices(self):
            return ["af_heart"]

        def create(self, text, **kwargs):
            raise RuntimeError(f"phonemes for {text}")

    onnx = types.ModuleType("kokoro_onnx")
    onnx.Kokoro, onnx.EspeakConfig = Kokoro, EspeakConfig
    loader = types.ModuleType("espeakng_loader")
    loader.get_library_path = lambda: str(lib)
    loader.get_data_path = lambda: str(data)
    monkeypatch.setitem(sys.modules, "kokoro_onnx", onnx)
    monkeypatch.setitem(sys.modules, "espeakng_loader", loader)
    loaded = []
    monkeypatch.setattr(ctypes, "CDLL", lambda path, *a, **kw: loaded.append(path))
    model, voices = tmp_path / "kokoro-v1.0.onnx", tmp_path / "voices-v1.0.bin"
    model.write_bytes(b"onnx")
    voices.write_bytes(b"voices")
    return types.SimpleNamespace(
        built=built, loader=loader, lib=str(lib), data=str(data), loaded=loaded,
        argv=["--model", str(model), "--voices", str(voices)], model=str(model), voices=str(voices),
        Kokoro=Kokoro,
    )


@pytest.fixture
def kokoro():
    return load("kokoro_worker")


@pytest.mark.parametrize("which", ["model", "voices"])
@pytest.mark.parametrize("value", ["kokoro-v1.0.onnx", "/nowhere/file.bin"])
def test_kokoro_refuses_relative_or_missing_files(kokoro, kokoro_env, capsys, which, value):
    argv = ["--model", kokoro_env.model, "--voices", kokoro_env.voices, "--list-voices"]
    argv[argv.index(f"--{which}") + 1] = value
    assert_refused(kokoro.main(argv), capsys.readouterr(), "/nowhere")
    assert kokoro_env.built == []


@pytest.mark.parametrize("broken", ["raises", "relative", "missing", "unloadable"])
def test_kokoro_refuses_when_the_bundled_espeak_is_unavailable(
    kokoro, kokoro_env, capsys, monkeypatch, broken
):
    if broken == "raises":
        def missing():
            raise FileNotFoundError(kokoro_env.lib)
        monkeypatch.setattr(kokoro_env.loader, "get_library_path", missing)
    elif broken == "relative":
        monkeypatch.setattr(kokoro_env.loader, "get_library_path", lambda: "libespeak-ng.dylib")
    elif broken == "missing":
        monkeypatch.setattr(kokoro_env.loader, "get_data_path", lambda: "/nowhere/espeak-ng-data")
    else:
        def unloadable(path, *a, **kw):
            raise OSError(f"dlopen({path}) failed")
        monkeypatch.setattr(ctypes, "CDLL", unloadable)
    assert_refused(kokoro.main([*kokoro_env.argv, "--list-voices"]), capsys.readouterr(),
                   kokoro_env.lib, "/nowhere")
    assert kokoro_env.built == []


def test_kokoro_passes_the_bundled_espeak_explicitly_and_drops_engine_switches(
    kokoro, kokoro_env, capsys, monkeypatch
):
    for name in ("PHONEMIZER_ESPEAK_LIBRARY", "LOG_LEVEL", "ONNX_PROVIDER"):
        monkeypatch.setenv(name, "DEBUG")
    logging.getLogger("kokoro_onnx").setLevel(logging.DEBUG)
    assert kokoro.main([*kokoro_env.argv, "--list-voices"]) == 0
    assert capsys.readouterr().out.split() == ["af_heart"]
    (model, voices, config), = kokoro_env.built
    assert (model, voices) == (kokoro_env.model, kokoro_env.voices)
    assert (config.lib_path, config.data_path) == (kokoro_env.lib, kokoro_env.data)
    assert kokoro_env.loaded == [kokoro_env.lib]
    for name in ("PHONEMIZER_ESPEAK_LIBRARY", "LOG_LEVEL", "ONNX_PROVIDER"):
        assert name not in os.environ
    assert logging.getLogger("kokoro_onnx").getEffectiveLevel() > logging.DEBUG


def test_a_kokoro_engine_error_never_echoes_the_text(kokoro, kokoro_env, tmp_path):
    args = kokoro.parse_args([*kokoro_env.argv, "--serve"])
    out = io.StringIO()
    request = json.dumps({"id": 1, "text": CANARY, "out": str(tmp_path / "1.wav")})
    assert kokoro.serve(kokoro_env.Kokoro("m", "v"), args, stdin=io.StringIO(request + "\n"), stdout=out) == 0
    assert CANARY not in out.getvalue()
    assert json.loads(out.getvalue()) == {"id": 1, "ok": False, "error": "synthesis failed"}


def test_kokoro_load_and_selftest_failures_print_fixed_words(kokoro, kokoro_env, capsys, monkeypatch):
    assert kokoro.main([*kokoro_env.argv, "--selftest"]) == 1  # create() raises with the text
    err = capsys.readouterr().err
    assert err.strip() == "kokoro: selftest failed"

    def broken(*args, **kwargs):
        raise RuntimeError(f"{CANARY} {kokoro_env.model}")

    monkeypatch.setattr(sys.modules["kokoro_onnx"], "Kokoro", broken)
    assert kokoro.main([*kokoro_env.argv, "--list-voices"]) == 1
    err = capsys.readouterr().err
    assert err.strip() == "kokoro: could not load the model"
    assert CANARY not in err


def test_kokoro_never_turns_on_debug_logging():
    tree = ast.parse((LOCAL / "kokoro_worker.py").read_text(encoding="utf-8"))
    attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "DEBUG" not in attrs and "basicConfig" not in attrs
