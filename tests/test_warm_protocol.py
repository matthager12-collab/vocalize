"""Real short Unix sockets exercise the private transport without model runtimes."""

import os
import queue
import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from vocalize.local import warm_protocol as p


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def eventually(predicate, timeout=2):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate()


def exchange(path, payload, timeout=1):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(timeout)
        conn.connect(str(path))
        p.send(conn, payload)
        return p.recv(conn, time.monotonic() + timeout)


class Harness:
    """Own the socket directory and stop every test server, including on failures."""

    def __init__(self, factory=None, **kwargs):
        self.base = Path(tempfile.mkdtemp(prefix="vw", dir="/tmp"))
        self.path = self.base / "whisper.sock"
        self.clock = Clock()
        self.errors = []
        self.unblock = threading.Event()
        if factory:
            self.server = factory(self, **kwargs)
        else:
            options = {"load": lambda: None,
                       "handle": lambda req, cancel: {"ok": False, "error": "bad-request"},
                       "fingerprint": {"test": 1}, "warm_seconds": 20, "abandon_seconds": 30,
                       "clock": self.clock, "exit_fn": lambda code: None}
            options.update(kwargs)
            self.server = p.Server(self.path, **options)
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        try:
            self.server.serve()
        except Exception as exc:  # noqa: BLE001 -- report background test errors
            self.errors.append(exc)

    def __enter__(self):
        self.thread.start()
        try:
            eventually(lambda: self.path.exists() or self.errors)
            if self.errors:
                raise self.errors[0]
            return self
        except BaseException:
            self.unblock.set()
            self.server.stopping = True
            self.thread.join(3)
            shutil.rmtree(self.base)
            raise

    def call(self, op, **fields):
        return exchange(self.path, {"op": op, **fields})

    def __exit__(self, *exc):
        self.unblock.set()
        if self.thread.is_alive():
            try:
                self.call("shutdown")
            except (OSError, p.ProtocolError):
                pass
        self.thread.join(3)
        shutil.rmtree(self.base)
        assert not self.thread.is_alive()
        assert not self.errors


class PairHarness(Harness):
    """Exercise the same accept loop over real socket pairs without a named bind.

    Named-socket integration tests remain separate: this isolates worker and
    lifecycle failures even in a sandbox that denies bind().
    """

    def __init__(self, factory=None, **kwargs):
        self.connections = queue.Queue()
        super().__init__(factory, **kwargs)

    def accept(self):
        try:
            return self.connections.get(timeout=0.1), None
        except queue.Empty:
            raise TimeoutError from None

    def run(self):
        threading.Thread(target=self.server._load, daemon=True).start()
        try:
            while not self.server.stopping and not self.server._expired():
                self.server._accept(self)
        except Exception as exc:  # noqa: BLE001 -- surface thread failures in the test
            self.errors.append(exc)

    def __enter__(self):
        self.thread.start()
        self.server.ready.wait(0.1)
        return self

    def connection(self):
        client, server = socket.socketpair()
        self.connections.put(server)
        return client

    def call(self, op, **fields):
        with self.connection() as client:
            p.send(client, {"op": op, **fields})
            return p.recv(client, time.monotonic() + 1)


@pytest.mark.parametrize("raw", [b"!\n", b"[]\n", b"null\n", b'"text"\n',
                                  b"x" * (p.MAX_LINE + 1), b'{}'])
def test_bad_framing(raw):
    reader, writer = socket.socketpair()
    with reader, writer:
        def write():
            try:
                writer.sendall(raw)
                writer.shutdown(socket.SHUT_WR)
            except OSError:
                pass

        thread = threading.Thread(target=write)
        writer.settimeout(0.5)
        thread.start()
        try:
            with pytest.raises(p.ProtocolError, match="bad-request"):
                p.recv(reader, time.monotonic() + 0.2)
        finally:
            thread.join(1)


def test_slow_sender_has_one_absolute_deadline():
    reader, writer = socket.socketpair()
    stop = threading.Event()

    def trickle():
        while not stop.wait(0.01):
            try:
                writer.send(b" ")
            except OSError:
                return

    thread = threading.Thread(target=trickle)
    with reader, writer:
        thread.start()
        started = time.monotonic()
        try:
            with pytest.raises(p.ProtocolError):
                p.recv(reader, started + 0.07)
            assert time.monotonic() - started < 0.2
        finally:
            stop.set()
            thread.join()


def test_send_caps_bytes_including_newline():
    with socket.socket() as sock, pytest.raises(p.ProtocolError):
        p.send(sock, {"text": "x" * p.MAX_LINE})


def test_controls_and_unknown_op():
    with PairHarness() as h:
        eventually(h.server.ready.is_set)
        assert h.call("hello")["fingerprint"] == {"test": 1}
        assert h.call("mystery", id="a") == {"ok": False, "error": "bad-request"}
        assert h.call("cancel", id="unknown") == {"ok": True}
        for i in range(8):
            assert h.call("lease", id=str(i)) == {"ok": True}
        assert h.call("lease", id="ninth")["error"] == "busy"
        assert h.call("lease", id="0")["ok"]
        assert h.call("lease", id="x" * 65)["error"] == "bad-request"
        assert h.call("lease", id=[])["error"] == "bad-request"


def test_socket_path_and_directory_security(tmp_path, monkeypatch):
    with Harness() as h:
        assert p.check_socket_path(h.path)
        assert h.path.stat().st_mode & 0o777 == 0o600
        link = h.base / "link"
        link.symlink_to(h.path)
        assert not p.check_socket_path(link)
        monkeypatch.setattr(p.os, "getuid", lambda: h.path.stat().st_uid + 1)
        assert not p.check_socket_path(h.path)
        with pytest.raises(p.ProtocolError):
            p.ensure_private_dir(h.base)
        monkeypatch.undo()
    assert not p.check_socket_path(tmp_path / ("a" * 101))
    tmp_path.chmod(0o755)
    with pytest.raises(p.ProtocolError):
        p.ensure_private_dir(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(p.ProtocolError):
        p.ensure_private_dir(link)
    with pytest.raises(ValueError):
        p.socket_path("../bad", tmp_path)


def test_fingerprint_changes_with_bytes_and_size(tmp_path):
    worker, model = tmp_path / "worker", tmp_path / "model"
    worker.write_bytes(b"one")
    first = p.fingerprint(worker, "runtime", model)
    assert first["model_size"] == 0
    model.write_bytes(b"model")
    second = p.fingerprint(worker, "runtime", model)
    worker.write_bytes(b"two")
    third = p.fingerprint(worker, "runtime", model)
    assert first != second != third
    assert second["worker_sha256"] != third["worker_sha256"]


def test_peer_credentials_fail_closed(monkeypatch):
    peer = SimpleNamespace(getsockopt=lambda *args: b"\0" * 4 + os.getuid().to_bytes(4, "little"))
    assert p.peer_uid(peer) == os.getuid()
    assert p.peer_uid(SimpleNamespace(getsockopt=lambda *args: b"")) is None
    with PairHarness() as h:
        monkeypatch.setattr(p, "peer_uid", lambda conn: None)
        with pytest.raises((p.ProtocolError, OSError)):
            h.call("hello")
        monkeypatch.undo()


def test_load_and_handle_errors_never_expose_text(capsys):
    def bad(*args):
        raise RuntimeError("secret transcript")

    with PairHarness(load=bad) as h:
        eventually(h.server.ready.is_set)
        assert h.call("work", id="x")["error"] == "failed"
    with PairHarness(handle=bad) as h:
        assert h.call("work", id="x")["error"] == "failed"
    assert capsys.readouterr() == ("", "")


def test_oversized_reply_does_not_kill_server():
    with PairHarness(handle=lambda *args: {"ok": True, "text": "x" * p.MAX_LINE}) as h:
        assert h.call("work", id="x")["error"] == "failed"
        assert h.call("hello")["ok"]


def test_cleanup_preserves_replacement_socket():
    with Harness() as h:
        h.path.unlink()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as replacement:
            replacement.bind(str(h.path))
            h.server.stopping = True
            h.thread.join(1)
            assert h.path.exists()


def test_the_socket_appears_only_once_listening(monkeypatch):
    """A client that sees the path must be able to connect. The path used to
    exist between bind and listen, so a connect there was refused (Linux CI,
    2026-09-27). A slow listen widens that window to make it certain."""
    listen = socket.socket.listen

    def slow_listen(sock, *args):
        time.sleep(0.3)
        listen(sock, *args)

    monkeypatch.setattr(socket.socket, "listen", slow_listen)
    with Harness() as h:
        assert h.call("hello")["ok"]


@pytest.mark.parametrize("seconds", [-1, float("nan"), float("inf"), "60", True])
def test_invalid_deadlines_are_refused(seconds):
    with PairHarness() as h, socket.socket() as conn, pytest.raises(p.ProtocolError):
        h.server._dispatch(conn, {"op": "work", "id": "a", "deadline_s": seconds})


@pytest.mark.parametrize("unsafe", ["mode", "symlink", "owner"])
def test_private_directory_refuses_unsafe_paths(tmp_path, monkeypatch, unsafe):
    path = tmp_path / "private"
    path.mkdir(mode=0o700)
    if unsafe == "mode":
        path.chmod(0o755)
    elif unsafe == "symlink":
        link = tmp_path / "link"
        link.symlink_to(path, target_is_directory=True)
        path = link
    else:
        uid = os.getuid()
        monkeypatch.setattr(p.os, "getuid", lambda: uid + 1)
    with pytest.raises(p.ProtocolError, match="failed"):
        p.ensure_private_dir(path)


def test_work_blocks_idle_expiration_and_unknown_cancel_is_ignored():
    entered, unblock = threading.Event(), threading.Event()

    def handle(request, cancelled):
        entered.set()
        unblock.wait(2)
        return {"ok": True}

    with PairHarness(handle=handle) as h, h.connection() as conn:
        p.send(conn, {"op": "work", "id": "running"})
        assert entered.wait(1)
        try:
            h.clock.advance(25)
            assert h.call("hello")["ok"]
            assert h.call("work", id="other")["error"] == "busy"
            assert h.call("cancel", id="other")["ok"]
            assert not h.server.running[1].is_set()
        finally:
            unblock.set()
            eventually(lambda: h.server.running is None)
        h.clock.advance(19)
        assert h.call("hello")["ok"]
        h.clock.advance(1)
        h.thread.join(1)
        assert not h.thread.is_alive()


def test_disconnected_clients_and_loading_over_socket_pairs():
    loaded = threading.Event()
    with PairHarness(load=lambda: loaded.wait(2)) as h:
        try:
            assert h.call("work", id="a")["error"] == "loading"
            with h.connection():
                pass
            with h.connection() as conn:
                p.send(conn, {"op": "hello"})
            assert h.call("hello")["state"] == "loading"
            loaded.set()
            eventually(h.server.ready.is_set)
            assert h.call("hello")["state"] == "ready"
        finally:
            loaded.set()


def test_the_os_timer_is_armed_only_for_a_real_server(monkeypatch):
    import os
    import signal

    calls = []
    monkeypatch.setattr(signal, "setitimer", lambda which, seconds: calls.append((which, seconds)))
    kwargs = {"load": lambda: None, "handle": lambda req, cancel: {"ok": True},
              "fingerprint": {}, "warm_seconds": 1, "abandon_seconds": 1}
    p.Server("/tmp/vw-x.sock", exit_fn=os._exit, **kwargs)._arm(7)
    p.Server("/tmp/vw-x.sock", exit_fn=lambda code: None, **kwargs)._arm(7)
    assert calls == [(signal.ITIMER_REAL, 7)]


def test_the_kernel_timer_kills_a_process_python_cannot_interrupt():
    """SIGALRM's default action needs no Python code to run, so it works
    while a native call holds the GIL and starves the watchdog thread."""
    import signal
    import subprocess
    import sys

    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, "-c",
         "import signal, time; signal.setitimer(signal.ITIMER_REAL, 0.2); time.sleep(10)"],
        timeout=20, check=False,
    )
    assert done.returncode == -signal.SIGALRM
    assert time.monotonic() - started < 5


def test_the_slot_is_free_before_the_reply_is_sent():
    """A client that asks again the instant it reads a reply must not be told
    busy (full-suite flake, run 3): the slot is freed before replying."""
    seen = []

    class Conn:
        def settimeout(self, _s):
            pass

        def sendall(self, _data):
            seen.append(server.running)

        def close(self):
            pass

    server = p.Server("/tmp/vw-y.sock", load=lambda: None,
                      handle=lambda req, cancel: {"ok": True}, fingerprint={},
                      warm_seconds=1, abandon_seconds=1, exit_fn=lambda code: None)
    done = threading.Event()
    server.running = ("id", threading.Event())
    server._work(Conn(), {"deadline_s": 1}, threading.Event(), done)
    assert seen == [None]
