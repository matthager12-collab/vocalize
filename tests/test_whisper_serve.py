"""Warm whisper lifecycle and file validation, with no pywhispercpp or numpy."""

import importlib.util
import os
import socket
import threading
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_warm_protocol import Harness, PairHarness, eventually

from vocalize.local import warm_protocol as p


def load_worker(name):
    path = Path(__file__).parents[1] / "vocalize" / "local" / f"{name}_worker.py"
    spec = importlib.util.spec_from_file_location(f"{name}_serve_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_wav(path, rate=16000):
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x00\x80\xff\x7f")


@pytest.fixture
def factory(monkeypatch):
    worker = load_worker("whisper")

    def make(h, warm=20, abandon=30, lease=None, loading=False, blocked=False, broken=False):
        h.worker = worker
        h.entered = threading.Event()
        h.exited = threading.Event()
        h.calls = []
        h.samples = object()
        h.wav = h.base / "take.wav"
        write_wav(h.wav)
        h.payload = {"op": "transcribe", "id": "take", "wav": str(h.wav),
                     "language": "en", "initial_prompt": "private vocabulary"}

        class Model:
            def __init__(self, model, **kwargs):
                h.model_args = (model, kwargs)
                if loading:
                    h.unblock.wait(3)

            def transcribe(self, media, **kwargs):
                h.calls.append((media, kwargs))
                h.entered.set()
                if blocked:
                    h.unblock.wait(3)
                if broken:
                    raise RuntimeError("private vocabulary")
                return [SimpleNamespace(text=" hello"), SimpleNamespace(text="world ")]

        monkeypatch.setattr(worker, "_model_class", lambda: Model)
        monkeypatch.setattr(worker, "_pcm_array", lambda frames: h.samples)
        args = worker.parse_args(["--serve", "--model", str(h.base / "model.bin"),
                                  "--socket", str(h.path), "--warm-seconds", str(warm),
                                  "--abandon-seconds", str(abandon)] +
                                 (["--lease", lease] if lease is not None else []))
        return worker._serve_server(args, clock=h.clock, exit_fn=lambda code: h.exited.set())

    return make


def lifecycle_case(factory, case, harness=Harness):
    """Apply identical lifecycle assertions to both independently loaded workers."""
    warm = 0 if case == "zero" else 20
    with harness(factory, warm=warm, lease="take", abandon=30) as h:
        assert h.call("hello")["ok"]
        if case == "abandoned":
            h.clock.advance(31)
            assert h.call("hello")["ok"]
            h.clock.advance(20)
        else:
            h.clock.advance(25)
            assert h.call("hello")["ok"]  # longer than idle grace, still leased
            if case == "held":
                return
            assert h.call("release", id="take")["ok"]
            grace = p.ZERO_GRACE if warm == 0 else warm
            h.clock.advance(grace - 1)
            assert h.call("hello")["ok"]
            h.clock.advance(1)
        h.thread.join(1)
        assert not h.thread.is_alive()
        assert not h.path.exists()


@pytest.mark.parametrize("case", ["idle", "zero", "held", "abandoned"])
def test_lifecycle(factory, case):
    lifecycle_case(factory, case)


def disconnect_case(factory):
    with Harness(factory) as h:
        for payload in (None, {"op": "hello"}, h.payload):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.connect(str(h.path))
                if payload:
                    p.send(conn, payload)
            assert h.call("hello")["ok"]
        assert h.thread.is_alive()


def test_vanished_clients(factory):
    disconnect_case(factory)


def loading_case(factory):
    with Harness(factory, loading=True) as h:
        assert h.call("hello")["state"] == "loading"
        assert h.call(**h.payload) == {"ok": False, "error": "loading"}
        h.unblock.set()
        eventually(h.server.ready.is_set)
        assert h.call(**h.payload)["ok"]


def test_loading(factory):
    loading_case(factory)


def watchdog_case(factory):
    with Harness(factory, blocked=True) as h, socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.connect(str(h.path))
        p.send(conn, {**h.payload, "deadline_s": 1})
        assert h.entered.wait(1)
        h.clock.advance(5.9)
        assert not h.exited.wait(0.08)
        h.clock.advance(0.2)
        assert h.exited.wait(0.5)
        h.unblock.set()
        eventually(lambda: h.server.running is None)


def test_watchdog(factory):
    watchdog_case(factory)


def test_transcribe_uses_array_and_resets_context(factory):
    with PairHarness(factory) as h:
        eventually(h.server.ready.is_set)
        assert h.call(**h.payload) == {"ok": True, "text": "hello world"}
        assert h.calls == [(h.samples, {"language": "en", "initial_prompt": "private vocabulary",
                                        "no_context": True})]
        assert h.model_args[1]["n_threads"] == 4
        assert h.model_args[1]["beam_search"]["beam_size"] == 5
        assert h.call("transcribe", id="second", wav=str(h.wav))["ok"]
        assert h.calls[-1][1]["initial_prompt"] == ""


@pytest.mark.parametrize("kind", ["fifo", "symlink", "oversize", "wrong-rate", "truncated"])
def test_unsafe_wav_refused(factory, kind):
    with PairHarness(factory) as h:
        bad = h.base / "bad.wav"
        if kind == "fifo":
            os.mkfifo(bad)
        elif kind == "symlink":
            bad.symlink_to(h.wav)
        elif kind == "oversize":
            with bad.open("wb") as file:
                file.truncate(201 * 1024**2)
        elif kind == "wrong-rate":
            write_wav(bad, rate=44100)
        else:
            bad.write_bytes(h.wav.read_bytes()[:-2])
        assert h.call(**{**h.payload, "wav": str(bad)})["error"] == "bad-request"
        assert h.calls == []


def test_model_exception_is_private(factory, capsys):
    with PairHarness(factory, broken=True) as h:
        assert h.call(**h.payload) == {"ok": False, "error": "failed"}
    assert capsys.readouterr() == ("", "")


def test_new_flags_and_exclusive_modes():
    worker = load_worker("whisper")
    with pytest.raises(SystemExit):
        worker.parse_args(["--model", "unused", "--serve", "--selftest"])
    with pytest.raises(SystemExit):
        worker.parse_args(["--model", "unused", "--serve"])


@pytest.mark.parametrize("case", ["idle", "zero", "held", "abandoned"])
def test_lifecycle_over_socket_pairs(factory, case):
    lifecycle_case(factory, case, PairHarness)


def test_watchdog_over_socket_pair(factory):
    with PairHarness(factory, blocked=True) as h, h.connection() as conn:
        eventually(h.server.ready.is_set)
        p.send(conn, {**h.payload, "deadline_s": 1})
        assert h.entered.wait(1)
        h.clock.advance(6.1)
        assert h.exited.wait(0.5)
        h.unblock.set()
        eventually(lambda: h.server.running is None)
