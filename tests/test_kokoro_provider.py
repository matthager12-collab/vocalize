"""Kokoro's provider seam, driven entirely by fakes.

Nothing here starts a process, touches the network, or needs uv, the
kokoro package or the 326 MB of weights installed. The one thing every
test guards is the boundary: the text goes down stdin as JSON and never
into argv, and the voice is checked against the manifest before it can
become an argument.
"""

import json
import os
import queue
import wave

import pytest

from vocalize import local
from vocalize.config import Settings
from vocalize.exceptions import (
    ProviderContentError,
    ProviderTransientError,
    ProviderUnavailableError,
)
from vocalize.local import install as install_module
from vocalize.local import kokoro_manifest as manifest
from vocalize.providers import kokoro

SECRET = "the quick brown fox says something private"


def wav_bytes(frames: bytes = b"\x00\x01" * 100) -> bytes:
    import io

    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(manifest.SAMPLE_RATE)
        writer.writeframes(frames)
    return buf.getvalue()


class _Stdin:
    """Collects what the provider writes and hands whole lines to the worker."""

    def __init__(self, worker):
        self._worker = worker
        self._buffer = ""
        self.closed = False

    def write(self, text):
        if self.closed:
            raise ValueError("write to closed file")
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self._worker.handle(line)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _Stdout:
    """Blocking line iterator, exactly like a real pipe's stdout."""

    def __init__(self, replies):
        self._replies = replies

    def __iter__(self):
        return self

    def __next__(self):
        line = self._replies.get()
        if line is None:
            raise StopIteration
        return line

    def close(self):
        pass


class FakeWorker:
    """A subprocess stand-in that speaks the worker's JSON-line protocol."""

    def __init__(self, argv, behavior="ok", audio=None, **kwargs):
        self.argv = list(argv)
        self.kwargs = kwargs
        self.requests = []
        self.terminated = False
        self.behavior = behavior
        self.audio = wav_bytes() if audio is None else audio
        self._replies = queue.Queue()
        self.stdin = _Stdin(self)
        self.stdout = _Stdout(self._replies)

    def handle(self, line):
        request = json.loads(line)
        self.requests.append(request)

        if self.behavior == "die":
            self._replies.put(None)  # EOF, as a crashed worker looks
            return
        if self.behavior == "error":
            self._replies.put(
                json.dumps({"id": request["id"], "ok": False, "error": "espeak exploded"})
                + "\n"
            )
            return
        if self.behavior == "garbage":
            self._replies.put("not json at all\n")
            return
        if self.behavior == "stray":
            # One extra line on stdout — a stray print from the runtime.
            # Everything after it pairs with the wrong request.
            self._replies.put(json.dumps({"note": "loading voices"}) + "\n")

        with open(request["out"], "wb") as out:
            out.write(self.audio)
        self._replies.put(json.dumps({"id": request["id"], "ok": True}) + "\n")

    def terminate(self):
        self.terminated = True
        self._replies.put(None)


class Spawned(list):
    """The workers started, plus the switch that decides how they behave."""

    behavior: dict


@pytest.fixture
def spawned(monkeypatch):
    """Replace the process seam. Returns the list of workers started."""
    workers = Spawned()
    workers.behavior = {"mode": "ok"}

    def factory(argv, **kwargs):
        worker = FakeWorker(argv, behavior=workers.behavior["mode"], **kwargs)
        workers.append(worker)
        return worker

    monkeypatch.setattr(kokoro, "SESSION_SEAM", factory)
    return workers


@pytest.fixture(autouse=True)
def _no_resident_worker():
    """No test may inherit (or leak) a session from another."""
    kokoro.close()
    yield
    kokoro.close()


@pytest.fixture
def model_dir(tmp_path, monkeypatch):
    """An empty stand-in for ~/.cache/vocalize/models/kokoro."""
    monkeypatch.setattr(manifest, "MODEL_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def with_uv(monkeypatch):
    # uv_path() lives in vocalize.local now (shared with Whisper); kokoro.py
    # only re-exports the name, so the real `shutil` it calls through is
    # vocalize.local's, not kokoro's own.
    monkeypatch.setattr(local.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    return "/usr/local/bin/uv"


@pytest.fixture
def no_uv(monkeypatch, tmp_path):
    monkeypatch.setattr(local.shutil, "which", lambda name: None)
    # Path.home() reads $HOME, so this empties the ~/.local/bin fallback too.
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))


@pytest.fixture
def installed(model_dir):
    """Both files at their manifest size (sparse), plus a valid stamp."""
    for entry in manifest.FILES:
        path = model_dir / entry["name"]
        path.write_bytes(b"")
        os.truncate(path, entry["size"])
    install_module.write_stamp(model_dir)
    return model_dir


def settings(**kwargs):
    base = {
        "voice_id": manifest.DEFAULT_VOICE,
        "provider": "kokoro",
        "language": manifest.DEFAULT_LANGUAGE,
    }
    base.update(kwargs)
    return Settings(**base)


# --- contract ---------------------------------------------------------


def test_the_contract_values_the_chain_reads():
    assert kokoro.NAME == "kokoro"
    assert kokoro.AUDIO_EXT == "wav"
    assert kokoro.MAX_CHARS == 400
    assert kokoro.STREAMING is True
    assert kokoro.DEFAULTS == {"voice": "af_heart", "language": "en-us"}


def test_list_voices_is_static_and_starts_nothing(spawned):
    voices = kokoro.list_voices()

    assert len(voices) == 54
    assert {"id": "af_heart", "name": "af_heart"} in voices
    assert spawned == []


# --- check ------------------------------------------------------------


def test_check_without_uv_names_the_docs(no_uv, model_dir):
    with pytest.raises(ProviderUnavailableError) as excinfo:
        kokoro.check(settings())

    assert "https://docs.astral.sh/uv/" in str(excinfo.value)
    assert "vocalize local install" in str(excinfo.value)


def test_check_before_install_names_the_command_and_touches_nothing(
    with_uv, model_dir, spawned
):
    with pytest.raises(ProviderUnavailableError) as excinfo:
        kokoro.check(settings())

    assert "vocalize local install" in str(excinfo.value)
    # No process started, and not one byte written where the model goes.
    assert spawned == []
    assert list(model_dir.iterdir()) == []


def test_check_refuses_a_half_install_with_no_stamp(with_uv, model_dir, spawned):
    for entry in manifest.FILES:
        path = model_dir / entry["name"]
        path.write_bytes(b"")
        os.truncate(path, entry["size"])

    with pytest.raises(ProviderUnavailableError, match="not verified"):
        kokoro.check(settings())
    assert spawned == []


def test_check_refuses_a_file_of_the_wrong_size(with_uv, installed, spawned):
    (installed / manifest.MODEL_FILE).write_bytes(b"truncated")

    with pytest.raises(ProviderUnavailableError, match="wrong size"):
        kokoro.check(settings())
    assert spawned == []


def test_check_refuses_a_stamp_from_another_manifest(with_uv, installed):
    stamp = install_module.stamp_path(installed)
    data = json.loads(stamp.read_text())
    data["manifest_version"] = manifest.MANIFEST_VERSION + 1
    stamp.write_text(json.dumps(data))

    with pytest.raises(ProviderUnavailableError, match="older vocalize"):
        kokoro.check(settings())


def test_check_refuses_a_stamp_whose_hashes_do_not_match(with_uv, installed):
    stamp = install_module.stamp_path(installed)
    data = json.loads(stamp.read_text())
    data["files"][manifest.MODEL_FILE]["sha256"] = "0" * 64
    stamp.write_text(json.dumps(data))

    with pytest.raises(ProviderUnavailableError, match="does not match this release"):
        kokoro.check(settings())


def test_check_passes_once_everything_is_in_place(with_uv, installed, spawned):
    kokoro.check(settings())  # must not raise

    assert spawned == []  # check() is offline: it never starts the worker


@pytest.mark.parametrize("voice", ["../etc/passwd", "--serve", "Rachel", "af_heart\n"])
def test_an_unknown_voice_stops_the_chain_before_any_process_starts(
    with_uv, installed, spawned, voice
):
    with pytest.raises(ProviderContentError) as excinfo:
        kokoro.check(settings(voice_id=voice))

    assert "[providers.kokoro] voice" in str(excinfo.value)
    assert spawned == []


# --- synthesize -------------------------------------------------------


def test_the_text_goes_down_stdin_and_never_into_argv(with_uv, installed, spawned):
    audio = kokoro.synthesize(SECRET, settings())

    worker = spawned[0]
    assert worker.requests[0]["text"] == SECRET
    assert SECRET not in " ".join(worker.argv)
    assert audio == wav_bytes()


def test_the_argv_is_the_uv_invocation_the_worker_expects(with_uv, installed, spawned):
    kokoro.synthesize("hello", settings(speed=1.1, language="en-gb"))

    argv = spawned[0].argv
    model, voices = manifest.file_paths()
    # --no-project is a guard, not a nicety: without it uv treats a cwd that
    # holds a pyproject.toml as a project and rebuilds ITS .venv (this bit us
    # live — the repo's 3.14 venv was replaced by a uv-managed 3.12 one).
    assert argv[:7] == [
        with_uv, "run", "--no-project",
        "--python", manifest.PYTHON_VERSION,
        "--with", manifest.RUNTIME_PACKAGE,
    ]
    assert argv[7] == str(manifest.worker_path())
    assert argv[argv.index("--model") + 1] == str(model)
    assert argv[argv.index("--voices") + 1] == str(voices)
    assert argv[argv.index("--voice") + 1] == "af_heart"
    assert argv[argv.index("--speed") + 1] == "1.1"
    assert argv[argv.index("--lang") + 1] == "en-gb"
    assert argv[-1] == "--serve"


def test_the_out_path_is_a_private_temporary_file(with_uv, installed, spawned):
    kokoro.synthesize("hello", settings())

    out = spawned[0].requests[0]["out"]
    assert out.endswith(".wav")
    assert "vocalize-kokoro-" in out


def test_one_worker_serves_every_chunk_of_a_read(with_uv, installed, spawned):
    for piece in ("one", "two", "three"):
        kokoro.synthesize(piece, settings())

    assert len(spawned) == 1  # the 326 MB model loads once, not three times
    assert [r["text"] for r in spawned[0].requests] == ["one", "two", "three"]
    assert [r["id"] for r in spawned[0].requests] == [1, 2, 3]


def test_changing_the_voice_retires_the_old_worker(with_uv, installed, spawned):
    kokoro.synthesize("hello", settings())
    kokoro.synthesize("hello", settings(voice_id="am_adam"))

    assert len(spawned) == 2
    assert spawned[0].terminated is True
    assert spawned[1].argv[spawned[1].argv.index("--voice") + 1] == "am_adam"


def test_an_unknown_voice_never_reaches_synthesize(with_uv, installed, spawned):
    with pytest.raises(ProviderContentError):
        kokoro.synthesize("hello", settings(voice_id="; rm -rf /"))

    assert spawned == []


def test_a_worker_error_reply_is_transient(with_uv, installed, spawned):
    spawned.behavior["mode"] = "error"

    with pytest.raises(ProviderTransientError, match="espeak exploded"):
        kokoro.synthesize("hello", settings())


def test_a_dead_worker_is_transient_and_the_session_is_dropped(
    with_uv, installed, spawned
):
    spawned.behavior["mode"] = "die"

    with pytest.raises(ProviderTransientError, match="exited"):
        kokoro.synthesize("hello", settings())

    assert kokoro._session is None
    # The next attempt starts a fresh worker rather than reusing the corpse.
    spawned.behavior["mode"] = "ok"
    assert kokoro.synthesize("hello", settings()) == wav_bytes()
    assert len(spawned) == 2


def test_a_malformed_reply_is_transient(with_uv, installed, spawned):
    spawned.behavior["mode"] = "garbage"

    with pytest.raises(ProviderTransientError, match="malformed"):
        kokoro.synthesize("hello", settings())


def test_a_stray_line_drops_the_worker_instead_of_desynchronising(
    with_uv, installed, spawned
):
    # Without the id check the stray line is read as this request's reply,
    # and every reply after it belongs to the request before — forever.
    spawned.behavior["mode"] = "stray"

    with pytest.raises(ProviderTransientError, match="out of order"):
        kokoro.synthesize("hello", settings())

    assert spawned[0].terminated
    assert kokoro._session is None


def test_a_timeout_is_transient(with_uv, installed, spawned, monkeypatch):
    monkeypatch.setattr(kokoro, "_REQUEST_TIMEOUT", 0.05)

    def silent(argv, **kwargs):
        worker = FakeWorker(argv, **kwargs)
        worker.handle = lambda line: None  # accepts the request, never answers
        spawned.append(worker)
        return worker

    monkeypatch.setattr(kokoro, "SESSION_SEAM", silent)

    with pytest.raises(ProviderTransientError, match="did not answer"):
        kokoro.synthesize("hello", settings())
    assert kokoro._session is None


def test_an_empty_answer_is_transient(with_uv, installed, spawned, monkeypatch):
    def factory(argv, **kwargs):
        worker = FakeWorker(argv, audio=b"", **kwargs)
        spawned.append(worker)
        return worker

    monkeypatch.setattr(kokoro, "SESSION_SEAM", factory)

    with pytest.raises(ProviderTransientError, match="no audio"):
        kokoro.synthesize("hello", settings())


def test_close_terminates_the_resident_worker(with_uv, installed, spawned):
    kokoro.synthesize("hello", settings())

    kokoro.close()

    assert spawned[0].terminated is True
    assert kokoro._session is None


# --- the opt-in promise -----------------------------------------------


def test_importing_vocalize_pulls_in_no_machine_learning_runtime():
    """`pip install vocalize-cli` must stay as light as it was in 0.8.1."""
    import subprocess
    import sys
    from pathlib import Path

    import vocalize

    root = str(Path(vocalize.__file__).resolve().parent.parent)
    probe = (
        "import sys, vocalize.cli, vocalize.providers.kokoro;"
        "heavy = {'numpy', 'onnxruntime', 'torch', 'kokoro_onnx'} & set(sys.modules);"
        "print(sorted(heavy))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, text=True, env={"PYTHONPATH": root, "PATH": "/usr/bin:/bin"},
        check=True,
    )

    assert result.stdout.strip() == "[]"


def test_speak_before_install_fails_loud_with_no_side_effects(
    monkeypatch, model_dir, spawned, with_uv
):
    from click.testing import CliRunner

    from vocalize import config
    from vocalize.cli import main

    monkeypatch.setattr(config, "load_config_file", dict)  # no user config in the way

    result = CliRunner().invoke(main, ["speak", "hello", "--provider", "kokoro"])

    assert result.exit_code != 0
    assert "vocalize local install" in result.output
    assert spawned == []  # no worker, no uv, no network
    assert list(model_dir.iterdir()) == []  # and not one file written
