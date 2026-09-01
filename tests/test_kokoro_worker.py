"""The standalone worker: its import discipline, and its stdin protocol.

The worker runs under uv's Python, not this one, so kokoro_onnx and numpy
are not installed here. That is exactly what the first test checks — the
file has to import cleanly in an environment that has neither, or the
isolation the whole design rests on is not real.
"""

import ast
import importlib.util
import io
import json
import wave

import pytest

from vocalize.local import kokoro_manifest as manifest

WORKER_PATH = manifest.worker_path()
WORKER_SOURCE = WORKER_PATH.read_text(encoding="utf-8")
TREE = ast.parse(WORKER_SOURCE)

# Everything the module body is allowed to pull in.
STDLIB_AT_MODULE_LEVEL = {"__future__", "argparse", "json", "signal", "sys", "wave"}


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


def test_kokoro_onnx_and_numpy_are_imported_inside_functions_only():
    assert "kokoro_onnx" not in module_level_imports()
    assert "numpy" not in module_level_imports()
    nested = imports_inside_functions()
    assert "kokoro_onnx" in nested
    assert "numpy" in nested


def test_the_worker_never_imports_vocalize():
    assert "vocalize" not in module_level_imports()
    assert "vocalize" not in imports_inside_functions()


def test_the_file_imports_here_where_neither_package_exists():
    with pytest.raises(ImportError):
        importlib.import_module("kokoro_onnx")

    assert load_worker() is not None


# --- the protocol -----------------------------------------------------


def load_worker():
    spec = importlib.util.spec_from_file_location("kokoro_worker_under_test", WORKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StubKokoro:
    """Stands in for kokoro_onnx.Kokoro. Records what it was asked to say."""

    def __init__(self, model, voices):
        self.model = model
        self.voices = voices
        self.calls = []

    def create(self, text, voice=None, speed=None, lang=None):
        self.calls.append((text, voice, speed, lang))
        if text == "boom":
            raise RuntimeError("espeak\nfell over\nmid-sentence")
        return [0.0, 0.5, -0.5] * 20, manifest.SAMPLE_RATE

    def get_voices(self):
        return ["bm_george", "af_heart", "am_adam"]


def stdlib_write_wav(samples, path, sample_rate=manifest.SAMPLE_RATE):
    """The real write_wav's job, without numpy — the protocol is what's tested."""
    with wave.open(path, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(int(sample_rate))
        writer.writeframes(b"\x00\x00" * len(samples))


@pytest.fixture
def worker(monkeypatch):
    module = load_worker()
    monkeypatch.setattr(module, "_kokoro_class", lambda: StubKokoro)
    monkeypatch.setattr(module, "write_wav", stdlib_write_wav)
    return module


def serve(worker, args_list, lines):
    args = worker.parse_args(args_list)
    model = StubKokoro(args.model, args.voices)
    out = io.StringIO()
    code = worker.serve(model, args, stdin=io.StringIO(lines), stdout=out)
    replies = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    return code, replies, model


def base_args(tmp_path):
    return [
        "--model", str(tmp_path / "kokoro-v1.0.onnx"),
        "--voices", str(tmp_path / "voices-v1.0.bin"),
        "--voice", "bm_george",
        "--speed", "1.1",
        "--lang", "en-gb",
        "--serve",
    ]


def test_each_request_renders_a_wav_and_answers_ok(worker, tmp_path):
    first, second = tmp_path / "1.wav", tmp_path / "2.wav"
    lines = (
        json.dumps({"id": 1, "text": "hello there", "out": str(first)}) + "\n"
        + json.dumps({"id": 2, "text": "and again", "out": str(second)}) + "\n"
    )

    code, replies, model = serve(worker, base_args(tmp_path), lines)

    assert code == 0
    assert replies == [{"id": 1, "ok": True}, {"id": 2, "ok": True}]
    assert first.exists() and second.exists()
    with wave.open(str(first)) as reader:
        assert reader.getnchannels() == 1
        assert reader.getsampwidth() == 2
        assert reader.getframerate() == manifest.SAMPLE_RATE
    # The voice, speed and language come from argv; the text does not.
    assert model.calls == [
        ("hello there", "bm_george", 1.1, "en-gb"),
        ("and again", "bm_george", 1.1, "en-gb"),
    ]


def test_a_failed_request_answers_with_one_line_and_the_session_goes_on(worker, tmp_path):
    lines = (
        json.dumps({"id": 7, "text": "boom", "out": str(tmp_path / "a.wav")}) + "\n"
        + json.dumps({"id": 8, "text": "fine", "out": str(tmp_path / "b.wav")}) + "\n"
    )

    code, replies, _ = serve(worker, base_args(tmp_path), lines)

    assert code == 0
    assert replies[0]["id"] == 7 and replies[0]["ok"] is False
    assert "\n" not in replies[0]["error"]
    assert replies[0]["error"] == "espeak fell over mid-sentence"
    assert replies[1] == {"id": 8, "ok": True}  # one bad request is not fatal


def test_a_malformed_request_line_does_not_kill_the_worker(worker, tmp_path):
    lines = "{not json\n" + json.dumps(
        {"id": 2, "text": "fine", "out": str(tmp_path / "b.wav")}
    ) + "\n"

    code, replies, _ = serve(worker, base_args(tmp_path), lines)

    assert code == 0
    assert replies[0]["ok"] is False
    assert replies[1] == {"id": 2, "ok": True}


def test_the_reply_never_repeats_the_text(worker, tmp_path):
    secret = "my bank account number is written here"
    lines = json.dumps({"id": 1, "text": secret, "out": str(tmp_path / "a.wav")}) + "\n"

    _, replies, _ = serve(worker, base_args(tmp_path), lines)

    assert secret not in json.dumps(replies)


def test_blank_lines_are_ignored(worker, tmp_path):
    code, replies, _ = serve(worker, base_args(tmp_path), "\n\n  \n")

    assert (code, replies) == (0, [])


def test_eof_ends_the_session(worker, tmp_path):
    code, replies, _ = serve(worker, base_args(tmp_path), "")

    assert (code, replies) == (0, [])


# --- the one-shot modes -----------------------------------------------


def test_list_voices_prints_them_sorted(worker, tmp_path, capsys):
    code = worker.main(["--model", "m", "--voices", "v", "--list-voices"])

    assert code == 0
    assert capsys.readouterr().out.split() == ["af_heart", "am_adam", "bm_george"]


def test_selftest_says_ok(worker, capsys):
    code = worker.main(["--model", "m", "--voices", "v", "--selftest"])

    assert code == 0
    assert capsys.readouterr().out.strip() == "ok"


def test_a_model_that_will_not_load_exits_one_with_a_single_stderr_line(
    worker, monkeypatch, capsys
):
    class Broken:
        def __init__(self, *args):
            raise RuntimeError("no such file:\nkokoro-v1.0.onnx")

    monkeypatch.setattr(worker, "_kokoro_class", lambda: Broken)

    code = worker.main(["--model", "m", "--voices", "v", "--selftest"])

    err = capsys.readouterr().err
    assert code == 1
    assert len(err.strip().splitlines()) == 1
    assert "could not load the model" in err


def test_a_failing_selftest_exits_one(worker, monkeypatch, capsys):
    class Broken(StubKokoro):
        def create(self, *args, **kwargs):
            raise RuntimeError("espeak-ng data is missing")

    monkeypatch.setattr(worker, "_kokoro_class", lambda: Broken)

    code = worker.main(["--model", "m", "--voices", "v", "--selftest"])

    assert code == 1
    assert "selftest failed" in capsys.readouterr().err


def test_a_mode_is_required(worker):
    with pytest.raises(SystemExit):
        worker.parse_args(["--model", "m", "--voices", "v"])


# --- the float32 -> 16-bit conversion ---------------------------------


def test_write_wav_clips_and_scales(tmp_path):
    pytest.importorskip("numpy")  # only installed inside uv's runtime
    module = load_worker()
    path = tmp_path / "out.wav"

    module.write_wav([0.0, 1.0, -1.0, 2.0, -2.0], str(path), manifest.SAMPLE_RATE)

    with wave.open(str(path)) as reader:
        frames = reader.readframes(reader.getnframes())
        assert reader.getnchannels() == 1
        assert reader.getsampwidth() == 2
    values = [
        int.from_bytes(frames[i : i + 2], "little", signed=True)
        for i in range(0, len(frames), 2)
    ]
    assert values == [0, 32767, -32767, 32767, -32767]
