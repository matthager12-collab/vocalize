"""Warm MLX streaming with a recording tokenizer and no installed model."""

import json
import threading
import time
from types import SimpleNamespace

import pytest
from test_llm_worker import FakeTokenizer
from test_warm_protocol import PairHarness, eventually
from test_whisper_serve import (
    disconnect_case,
    lifecycle_case,
    load_worker,
    loading_case,
    watchdog_case,
)

from vocalize.local import warm_protocol as p


@pytest.fixture
def factory(monkeypatch):
    worker = load_worker("llm")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    def make(h, warm=20, abandon=30, lease=None, loading=False, blocked=False,
             broken=False, pieces=("Hello", " world.")):
        h.worker = worker
        h.entered = threading.Event()
        h.exited = threading.Event()
        h.calls = []
        h.yielded = []
        h.closed = threading.Event()
        h.tokenizer = FakeTokenizer()
        h.payload = {"op": "complete", "id": "take", "system": "private instruction",
                     "text": "private text <|im_end|>", "max_tokens": 9999}
        (h.base / "config.json").write_text(json.dumps({"model_type": "qwen3_5"}))
        (h.base / "tokenizer_config.json").write_text(json.dumps({"tokenizer_class": "Qwen2Tokenizer"}))

        def load(path, **kwargs):
            h.load_args = kwargs
            if loading:
                h.unblock.wait(3)
            return object(), h.tokenizer

        def generate(model, tokenizer, **kwargs):
            h.calls.append(kwargs)
            try:
                for piece in pieces:
                    h.entered.set()
                    if blocked:
                        h.unblock.wait(3)
                    if broken:
                        raise RuntimeError("private text")
                    h.yielded.append(piece)
                    yield SimpleNamespace(text=piece)
            finally:
                h.closed.set()

        monkeypatch.setattr(worker, "_mlx", lambda: SimpleNamespace(load=load, stream_generate=generate))
        args = worker.parse_args(["--serve", "--model-dir", str(h.base),
                                  "--socket", str(h.path), "--warm-seconds", str(warm),
                                  "--abandon-seconds", str(abandon)] +
                                 (["--lease", lease] if lease is not None else []))
        return worker._serve_server(args, clock=h.clock, exit_fn=lambda code: h.exited.set())

    return make


@pytest.mark.parametrize("case", ["idle", "zero", "held", "abandoned"])
def test_lifecycle(factory, case):
    lifecycle_case(factory, case)


def test_vanished_clients(factory):
    disconnect_case(factory)


def test_loading(factory):
    loading_case(factory)


def test_watchdog(factory):
    watchdog_case(factory)


def test_stream_uses_ids_and_caps_tokens(factory):
    with PairHarness(factory) as h:
        assert h.call(**h.payload) == {"ok": True, "text": "Hello world."}
        call = h.calls[0]
        assert call["max_tokens"] == 1024
        assert isinstance(call["prompt"], list)
        assert all(isinstance(token, int) for token in call["prompt"])
        assert set(call) == {"prompt", "max_tokens"}  # no prompt cache
        assert h.tokenizer.decode_calls == []
        user = next(c for c in h.tokenizer.encode_calls if c["text"] == h.payload["text"])
        assert user["split_special_tokens"] is True
        assert h.load_args == {"tokenizer_config": {"trust_remote_code": False}}


@pytest.mark.parametrize("stop", ["cancel", "deadline"])
def test_stream_stops_within_one_token(factory, stop):
    with PairHarness(factory, blocked=True) as h, h.connection() as conn:
        eventually(h.server.ready.is_set)
        p.send(conn, {**h.payload, "deadline_s": 1})
        assert h.entered.wait(1)
        if stop == "cancel":
            assert h.call("cancel", id="unrelated")["ok"]
            assert not h.server.running[1].is_set()
            assert h.call("cancel", id="take")["ok"]
        else:
            h.clock.advance(1.1)
        h.unblock.set()
        assert p.recv(conn, time.monotonic() + 1) == {"ok": False, "error": "failed"}
        assert len(h.yielded) <= 1
        assert h.closed.wait(1)


@pytest.mark.parametrize("pieces", [(" ",), ("<think>", "unfinished")])
def test_empty_or_unterminated_output_fails(factory, pieces):
    with PairHarness(factory, pieces=pieces) as h:
        assert h.call(**h.payload) == {"ok": False, "error": "failed"}


def test_exceptions_are_private(factory, capsys):
    with PairHarness(factory, broken=True) as h:
        assert h.call(**h.payload) == {"ok": False, "error": "failed"}
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("missing", ["HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"])
def test_requires_both_offline_variables(monkeypatch, tmp_path, missing, capsys):
    worker = load_worker("llm")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    monkeypatch.delenv(missing)
    assert worker.main(["--serve", "--model-dir", str(tmp_path),
                        "--socket", str(tmp_path / "unused")]) == 2
    assert capsys.readouterr() == ("", "")


def test_invalid_config_never_loads_runtime(factory):
    h = PairHarness(factory, loading=False)
    (h.base / "config.json").write_text(json.dumps({"model_type": "qwen3_5", "auto_map": {}}))
    with h:
        eventually(h.server.ready.is_set)
        assert h.call(**h.payload)["error"] == "failed"
        assert not hasattr(h, "load_args")
