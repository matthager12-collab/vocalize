"""Exercise client deadlines and retirement against actual Unix listeners."""

import shutil
import socket
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from test_warm_protocol import Harness, eventually

from vocalize.local import warm
from vocalize.local import warm_protocol as p


@contextmanager
def short_dir():
    base = Path(tempfile.mkdtemp(prefix="vw", dir="/tmp"))
    try:
        yield base
    finally:
        shutil.rmtree(base)


@contextmanager
def silent_server(base):
    """Accept connections but never answer, including hello and shutdown."""
    path = base / "whisper.sock"
    stop = threading.Event()
    accepted = []
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(8)
    listener.settimeout(0.02)

    def run():
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
                accepted.append(conn)
            except TimeoutError:
                pass

    thread = threading.Thread(target=run)
    thread.start()
    try:
        yield accepted
    finally:
        stop.set()
        thread.join(1)
        listener.close()
        for conn in accepted:
            conn.close()


def record_controls(monkeypatch):
    calls = []
    original = warm._exchange

    def exchange(path, payload, deadline, **kwargs):
        calls.append(payload)
        return original(path, payload, deadline, **kwargs)

    monkeypatch.setattr(warm, "_exchange", exchange)
    return calls


def test_racing_ensure_calls_spawn_once():
    with short_dir() as base:
        calls = []
        entered, finish = threading.Event(), threading.Event()

        def spawn(argv, **kwargs):
            calls.append((argv, kwargs))
            entered.set()
            assert finish.wait(1)

        first = threading.Thread(target=warm.ensure_warm,
                                 args=("whisper", ["worker", "--serve"], {}, "first", base),
                                 kwargs={"spawn": spawn, "env": {"OFFLINE": "1"}})
        first.start()
        assert entered.wait(1)
        warm.ensure_warm("whisper", ["worker"], {}, "second", base, spawn=spawn)
        finish.set()
        first.join(1)
        # The first CLI can exit before uv binds; the on-disk reservation still wins.
        warm.ensure_warm("whisper", ["worker"], {}, "third", base, spawn=spawn)
        assert len(calls) == 1
        argv, kwargs = calls[0]
        assert argv[-2:] == ["--lease", "first"]
        assert kwargs == {"start_new_session": True, "stdin": -3, "stdout": -3, "stderr": -3,
                          "cwd": tempfile.gettempdir(), "close_fds": True, "env": {"OFFLINE": "1"}}
        assert (base / "whisper.lock").stat().st_mode & 0o777 == 0o600


def test_mismatch_shuts_down_then_respawns(monkeypatch):
    controls = record_controls(monkeypatch)
    with Harness() as h:
        spawned = []
        warm.ensure_warm("whisper", ["worker"], {"new": 2}, "take", h.base,
                         spawn=lambda *args, **kwargs: spawned.append(args))
        assert any(call["op"] == "shutdown" for call in controls)
        assert len(spawned) == 1
        h.thread.join(1)
        assert not h.thread.is_alive()


def test_matching_server_leased_and_released():
    with Harness() as h:
        fingerprint = h.server.fingerprint
        assert warm.live("whisper", fingerprint, h.base)["ok"]
        assert warm.live("whisper", {"different": True}, h.base) is None
        warm.ensure_warm("whisper", [], fingerprint, "take", h.base,
                         spawn=lambda *args, **kwargs: pytest.fail("unexpected spawn"))
        assert "take" in h.server.leases
        warm.release("whisper", "take", h.base)
        assert "take" not in h.server.leases


def test_loading_is_retried_until_ready():
    loaded = threading.Event()
    with Harness(load=lambda: loaded.wait(2),
                 handle=lambda *args: {"ok": True, "text": "answer"}) as h:
        timer = threading.Timer(0.12, loaded.set)
        timer.start()
        try:
            reply = warm.request("whisper", {"op": "work"}, h.server.fingerprint, 1, h.base)
            assert reply == {"ok": True, "text": "answer"}
            assert h.thread.is_alive()
        finally:
            loaded.set()
            timer.join()


@pytest.mark.parametrize("error", ["busy", "failed", "bad-request"])
def test_errors_cancel_then_shutdown_before_return(monkeypatch, error):
    controls = record_controls(monkeypatch)
    with Harness(handle=lambda *args: {"ok": False, "error": error}) as h:
        started = time.monotonic()
        assert warm.request("whisper", {"op": "work", "id": "caller-id"},
                            h.server.fingerprint, 0.3, h.base) is None
        assert time.monotonic() - started < 0.3
        assert [call["op"] for call in controls][-2:] == ["cancel", "shutdown"]
        assert controls[-2]["id"] != "caller-id"
        h.thread.join(1)
        assert not h.thread.is_alive()


def test_actual_busy_server_is_cancelled_and_shutdown(monkeypatch):
    entered, unblock = threading.Event(), threading.Event()

    def handle(req, cancelled):
        entered.set()
        unblock.wait(2)
        return {"ok": True}

    with Harness(handle=handle) as h, socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.connect(str(h.path))
        p.send(conn, {"op": "work", "id": "previous"})
        assert entered.wait(1)
        try:
            assert warm.request("whisper", {"op": "work"}, h.server.fingerprint, 0.3, h.base) is None
            h.thread.join(1)
            assert not h.thread.is_alive()
            assert h.server.running[1].is_set()
        finally:
            unblock.set()
            eventually(lambda: h.server.running is None)


def test_hung_work_deadline_still_sends_shutdown(monkeypatch):
    controls = record_controls(monkeypatch)
    unblock = threading.Event()
    with Harness(handle=lambda *args: (unblock.wait(2) or {"ok": True})) as h:
        try:
            started = time.monotonic()
            assert warm.request("whisper", {"op": "work"}, h.server.fingerprint, 0.15, h.base) is None
            assert time.monotonic() - started < 0.2
            assert [call["op"] for call in controls][-2:] == ["cancel", "shutdown"]
            h.thread.join(1)
            assert not h.thread.is_alive()
        finally:
            unblock.set()
            eventually(lambda: h.server.running is None)


@pytest.mark.parametrize("silent", [False, True])
def test_unavailable_server_returns_within_deadline(monkeypatch, silent):
    controls = record_controls(monkeypatch)
    with short_dir() as base:
        def check():
            started = time.monotonic()
            assert warm.request("whisper", {"op": "work"}, {}, 0.15, base) is None
            # The request's 0.15 s, plus shutdown's own 0.2 s budget, which it
            # keeps even when the request used up its deadline (review, run 3).
            assert time.monotonic() - started < 0.45
            assert controls[-1]["op"] == "shutdown"
        if silent:
            with silent_server(base):
                check()
        else:
            check()


def test_ensure_bounded_when_listener_never_answers():
    with short_dir() as base, silent_server(base):
        started = time.monotonic()
        spawned = []
        warm.ensure_warm("whisper", ["worker"], {}, "take", base,
                         spawn=lambda *args, **kwargs: spawned.append(args))
        assert time.monotonic() - started < 0.25
        assert not spawned


def test_ensure_bounded_even_when_spawn_stalls():
    with short_dir() as base:
        release, finished = threading.Event(), threading.Event()

        def spawn(*args, **kwargs):
            release.wait(2)
            finished.set()

        try:
            started = time.monotonic()
            warm.ensure_warm("whisper", ["worker"], {}, "take", base, spawn=spawn)
            assert time.monotonic() - started < 0.25
        finally:
            release.set()
            assert finished.wait(1)
            # Wait for the background task to release its descriptor before cleanup.
            eventually(lambda: (base / "whisper.lock").stat().st_size > 0)


def test_fingerprint_mismatch_never_sends_payload(monkeypatch):
    calls = record_controls(monkeypatch)
    with Harness() as h:
        assert warm.request("whisper", {"op": "work", "text": "secret"}, {}, 0.2, h.base) is None
        assert calls == [{"op": "hello"}]


def test_symlinked_lock_never_written_or_spawned():
    with short_dir() as base:
        target = base / "protected"
        target.write_text("unchanged")
        (base / "whisper.lock").symlink_to(target)
        spawned = []
        warm.ensure_warm("whisper", [], {}, "take", base,
                         spawn=lambda *args, **kwargs: spawned.append(args))
        assert spawned == []
        assert target.read_text() == "unchanged"


def test_public_functions_are_fail_safe():
    with short_dir() as base:
        assert warm.live("bad-kind", {}, base) is None
        assert warm.ensure_warm("bad-kind", [], {}, "x", base) is None
        assert warm.release("bad-kind", "x", base) is None
        assert warm.request("bad-kind", {}, {}, 0.1, base) is None
        assert warm.request("whisper", {}, {}, float("nan"), base) is None


@pytest.mark.parametrize("error", ["busy", "failed", "bad-request"])
def test_failure_controls_over_socket_pairs(monkeypatch, error):
    """Verify actual server shutdown even where named socket binding is forbidden."""
    from test_warm_protocol import PairHarness

    with PairHarness(handle=lambda *args: {"ok": False, "error": error}) as h:
        controls = []

        def exchange_pair(path, payload, deadline, *, answer=True):
            assert path == h.path
            controls.append(payload)
            with h.connection() as conn:
                p.send(conn, payload)
                return p.recv(conn, deadline) if answer else None

        monkeypatch.setattr(warm, "_exchange", exchange_pair)
        assert warm.request("whisper", {"op": "work"}, h.server.fingerprint, 0.3, h.base) is None
        assert [call["op"] for call in controls][-2:] == ["cancel", "shutdown"]
        h.thread.join(1)
        assert not h.thread.is_alive()


def test_loading_retry_over_socket_pairs(monkeypatch):
    from test_warm_protocol import PairHarness

    loaded = threading.Event()
    with PairHarness(load=lambda: loaded.wait(2),
                     handle=lambda *args: {"ok": True, "text": "answer"}) as h:
        def exchange_pair(path, payload, deadline, *, answer=True):
            with h.connection() as conn:
                p.send(conn, payload)
                return p.recv(conn, deadline) if answer else None

        monkeypatch.setattr(warm, "_exchange", exchange_pair)
        timer = threading.Timer(0.12, loaded.set)
        timer.start()
        try:
            assert warm.request("whisper", {"op": "work"}, h.server.fingerprint, 1, h.base) == {
                "ok": True, "text": "answer"
            }
        finally:
            loaded.set()
            timer.join()


def test_request_waits_for_a_server_this_take_spawned(monkeypatch):
    """A short take's stop can arrive before uv has bound the socket. With a
    spawn pending, request waits for it instead of loading a second model
    beside it (review, run 3)."""
    with short_dir() as base:
        base.chmod(0o700)
        (base / "whisper.lock").write_text(str(time.monotonic()), encoding="ascii")
        server = p.Server(base / "whisper.sock", load=lambda: None,
                          handle=lambda req, cancel: {"ok": True, "text": "late"},
                          fingerprint={"test": 1}, warm_seconds=20, abandon_seconds=30,
                          exit_fn=lambda code: None)
        thread = threading.Thread(target=server.serve, daemon=True)
        timer = threading.Timer(0.3, thread.start)
        timer.start()
        try:
            reply = warm.request("whisper", {"op": "work"}, {"test": 1}, 3, base)
            assert reply == {"ok": True, "text": "late"}
        finally:
            timer.join()
            server.stopping = True
            thread.join(2)


def test_request_without_a_pending_spawn_falls_back_at_once():
    with short_dir() as base:
        started = time.monotonic()
        assert warm.request("whisper", {"op": "work"}, {"test": 1}, 3, base) is None
        assert time.monotonic() - started < 0.5


def test_shutdown_is_attempted_even_after_cancel_used_the_deadline(monkeypatch):
    sent = []

    def exchange(path, payload, deadline, **kwargs):
        sent.append((payload["op"], deadline - time.monotonic()))
        raise TimeoutError

    monkeypatch.setattr(warm, "_exchange", exchange)
    warm._stop(Path("/tmp/vw-none/whisper.sock"), "abc", time.monotonic() - 1)
    ops = dict(sent)
    assert "shutdown" in ops and ops["shutdown"] > 0
