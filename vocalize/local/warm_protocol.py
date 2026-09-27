"""Private, bounded socket transport shared by independently installed workers."""

from __future__ import annotations

import hashlib
import json
import math
import os
import socket
import stat
import struct
import threading
import time
from pathlib import Path

PROTOCOL = 1
MAX_LINE = 64 * 1024
READ_DEADLINE = 2.0
MAX_LEASES = 8
ZERO_GRACE = 10.0
ERRORS = ("busy", "loading", "bad-request", "failed")


class ProtocolError(Exception):
    """Expose only fixed codes, never model exceptions or request text."""

    def __init__(self, error):
        self.error = error if error in ERRORS else "failed"
        super().__init__(self.error)


def warm_dir(base: Path | None = None) -> Path:
    """Allow isolated callers and tests to avoid the user's cache."""
    return Path(base) if base is not None else Path.home() / ".cache" / "vocalize" / "warm"


def ensure_private_dir(path):
    """Refuse unsafe existing directories instead of following or repairing them."""
    try:
        path = Path(path)
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ProtocolError("failed")
    except OSError:
        raise ProtocolError("failed") from None


def socket_path(kind, base=None):
    """Keep model names from becoming arbitrary filesystem paths."""
    if kind not in ("whisper", "llm"):
        raise ValueError("unknown worker kind")
    return warm_dir(base) / f"{kind}.sock"


def check_socket_path(path) -> bool:
    """Authenticate the filesystem endpoint before sending private text."""
    try:
        if len(os.fsencode(path)) > 100:
            return False
        info = Path(path).lstat()
        return stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid()
    except OSError:
        return False


def send(sock, obj):
    """Bound replies as well as requests to contain memory and socket usage."""
    try:
        if not isinstance(obj, dict):
            raise ProtocolError("bad-request")
        data = (json.dumps(obj, allow_nan=False) + "\n").encode()
        if len(data) > MAX_LINE:
            raise ProtocolError("bad-request")
        sock.sendall(data)
    except (ValueError, TypeError, RecursionError):
        raise ProtocolError("bad-request") from None


def recv(sock, deadline):
    """Use an absolute deadline so a trickling peer cannot extend its turn."""
    data = bytearray()
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise ProtocolError("bad-request")
            sock.settimeout(left)
            chunk = sock.recv(min(4096, MAX_LINE + 1 - len(data)))
            if not chunk:
                raise ProtocolError("bad-request")
            data.extend(chunk)
            end = data.find(b"\n")
            if end >= 0:
                if end + 1 > MAX_LINE:
                    raise ProtocolError("bad-request")
                obj = json.loads(data[:end])
                if not isinstance(obj, dict) or time.monotonic() >= deadline:
                    raise ProtocolError("bad-request")
                return obj
            if len(data) >= MAX_LINE:
                raise ProtocolError("bad-request")
    except (OSError, ValueError, RecursionError):
        raise ProtocolError("bad-request") from None


def peer_uid(sock) -> int | None:
    """Fail closed when macOS cannot authenticate a connected peer."""
    try:
        return struct.unpack_from("=I", sock.getsockopt(0, 0x001, 256), 4)[0]
    except (OSError, ValueError, struct.error):
        return None


def fingerprint(worker_file, runtime: str, model_path) -> dict:
    """Detect worker, runtime and model changes before reusing a process."""
    model = Path(model_path)
    try:
        size = model.stat().st_size
    except FileNotFoundError:
        size = 0
    return {
        "protocol": PROTOCOL,
        "worker_sha256": hashlib.sha256(Path(worker_file).read_bytes()).hexdigest(),
        "runtime": runtime,
        "model": model.name,
        "model_size": size,
    }


def _seconds(value):
    """Reject NaN and infinity, which would disable expiration and watchdogs."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError("bad-request")
    if not math.isfinite(value) or value < 0:
        raise ProtocolError("bad-request")
    return value


class Server:
    """Keep controls responsive while one model call runs off the accept thread."""

    def __init__(self, sock_path, *, load, handle, warm_seconds, abandon_seconds,
                 fingerprint, initial_lease=None, clock=time.monotonic, exit_fn=os._exit):
        self.path = Path(sock_path)
        self.load = load
        self.handle = handle
        self.warm_seconds = _seconds(warm_seconds) or ZERO_GRACE
        self.abandon_seconds = _seconds(abandon_seconds)
        self.fingerprint = fingerprint
        self.clock = clock
        self.exit_fn = exit_fn
        self.leases = {}
        if initial_lease is not None:
            self._id(initial_lease)
            self.leases[initial_lease] = clock()
        self.idle_since = clock()
        self.ready = threading.Event()
        self.load_failed = False
        self.lock = threading.Lock()
        self.running = None
        self.stopping = False

    @staticmethod
    def _id(value):
        """Bound identifiers before using them as dictionary keys."""
        if not isinstance(value, str) or len(value) > 64:
            raise ProtocolError("bad-request")
        return value

    def _load(self):
        """Publish readiness only after all model state is initialized."""
        try:
            self.load()
        except Exception:  # noqa: BLE001 -- contain runtime failures without disclosing private text
            self.load_failed = True
        finally:
            self.ready.set()

    def _expired(self):
        """Count idle time from actual release, work completion, or lease expiry."""
        with self.lock:
            now = self.clock()
            expired = [key for key, taken in self.leases.items()
                       if now >= taken + self.abandon_seconds]
            last_expiry = max((self.leases[key] + self.abandon_seconds for key in expired),
                              default=now)
            for key in expired:
                del self.leases[key]
            if expired and not self.leases:
                self.idle_since = last_expiry
            return (not self.leases and self.running is None
                    and now - self.idle_since >= self.warm_seconds)

    def _reply(self, conn, reply):
        """A vanished reader or an oversized model output must never stop serving."""
        try:
            conn.settimeout(READ_DEADLINE)
            try:
                send(conn, reply)
            except ProtocolError:
                send(conn, {"ok": False, "error": "failed"})
        except OSError:
            pass

    def _watchdog(self, done, deadline):
        """A separate timer can terminate even a model blocked in native code."""
        while not done.wait(0.05):
            if self.clock() >= deadline:
                if not done.is_set():
                    self.exit_fn(3)
                return

    def _work(self, conn, request, cancelled, done):
        """Own this connection until its model result is sent or discarded."""
        try:
            try:
                reply = self.handle(request, cancelled)
                if not isinstance(reply, dict):
                    raise ProtocolError("failed")
            except ProtocolError as exc:
                reply = {"ok": False, "error": exc.error}
            except Exception:  # noqa: BLE001 -- contain runtime failures without disclosing private text
                reply = {"ok": False, "error": "failed"}
            self._reply(conn, reply)
        finally:
            conn.close()
            with self.lock:
                done.set()
                self.running = None
                self.idle_since = self.clock()

    def _dispatch(self, conn, request):
        """Return whether the work thread took ownership of the connection."""
        op = request.get("op")
        if not isinstance(op, str):
            raise ProtocolError("bad-request")
        if op == "hello":
            self._reply(conn, {"ok": True, "fingerprint": self.fingerprint,
                               "state": "ready" if self.ready.is_set() else "loading"})
            return False
        if op == "shutdown":
            with self.lock:
                if self.running:
                    self.running[1].set()
            self.stopping = True
            self._reply(conn, {"ok": True})
            return False
        ident = self._id(request.get("id"))
        with self.lock:
            if op == "lease":
                if ident not in self.leases and len(self.leases) >= MAX_LEASES:
                    raise ProtocolError("busy")
                self.leases[ident] = self.clock()
            elif op == "release":
                if ident in self.leases:
                    del self.leases[ident]
                    if not self.leases:
                        self.idle_since = self.clock()
            elif op == "cancel":
                if self.running and self.running[0] == ident:
                    self.running[1].set()
            else:
                if not self.ready.is_set():
                    raise ProtocolError("loading")
                if self.load_failed:
                    raise ProtocolError("failed")
                if self.running:
                    raise ProtocolError("busy")
                seconds = min(_seconds(request.get("deadline_s", 60)), 300)
                request = {**request, "deadline_s": seconds}
                cancelled, done = threading.Event(), threading.Event()
                self.running = (ident, cancelled)
                threading.Thread(target=self._watchdog,
                                 args=(done, self.clock() + seconds + 5), daemon=True).start()
                threading.Thread(target=self._work,
                                 args=(conn, request, cancelled, done), daemon=True).start()
                return True
        self._reply(conn, {"ok": True})
        return False

    def _accept(self, listener):
        """Close unauthenticated and malformed clients without disclosing content."""
        try:
            conn, _ = listener.accept()
        except TimeoutError:
            return
        handed_off = False
        try:
            if peer_uid(conn) != os.getuid():
                return
            request = recv(conn, time.monotonic() + READ_DEADLINE)
            handed_off = self._dispatch(conn, request)
        except ProtocolError as exc:
            self._reply(conn, {"ok": False, "error": exc.error})
        except Exception:  # noqa: BLE001 -- one bad connection must never stop the server
            pass
        finally:
            if not handed_off:
                conn.close()

    def serve(self):
        """Load first, bind privately, and remove only this listener's inode."""
        threading.Thread(target=self._load, daemon=True).start()
        ensure_private_dir(self.path.parent)
        if len(os.fsencode(self.path)) > 100:
            raise ProtocolError("failed")
        identity = None
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                if check_socket_path(self.path):
                    # An owned socket is stale only when nobody is listening.
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                        probe.settimeout(0.1)
                        try:
                            probe.connect(str(self.path))
                        except ConnectionRefusedError:
                            self.path.unlink()
                        else:
                            raise ProtocolError("failed")
                listener.bind(str(self.path))
                info = self.path.lstat()
                identity = (info.st_dev, info.st_ino)
                self.path.chmod(0o600)
                listener.listen(8)
                listener.settimeout(0.1)
                while not self.stopping and not self._expired():
                    self._accept(listener)
        finally:
            try:
                info = self.path.lstat()
                if (identity == (info.st_dev, info.st_ino)
                        and check_socket_path(self.path)):
                    self.path.unlink()
            except OSError:
                pass
