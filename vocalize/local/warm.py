"""Best-effort warm reuse: every public entry point preserves one-shot fallback."""

from __future__ import annotations

import fcntl
import hashlib
import math
import os
import socket
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from vocalize.local import runtime
from vocalize.local import warm_protocol as protocol

# AF_UNIX's sun_path is 104 bytes on macOS; the server's pending name adds
# up to 3 more, so the protocol caps a socket path at 100.
_SOCKET_PATH_MAX = 100


def _fits(path) -> bool:
    return len(os.fsencode(path)) <= _SOCKET_PATH_MAX


def _fallback_parents() -> list[str]:
    """Where a short private socket folder may go, in order. A seam: the
    test suite turns the fallback off so no test reaches a shared folder."""
    # Each folder made under these is 0700 and owner-checked (ensure_private_dir).
    return [tempfile.gettempdir(), "/tmp"]


def socket_path(kind, base=None) -> Path:
    """The usual socket path, or a short private one when it is too long.

    A long HOME (or `base`) once disabled every warm server without a word.
    The fallback folder is named for this user and for the usual folder, so
    two homes never share a server, and every caller — client, spawner,
    `listen --check` — computes the same one. It is created 0700 and
    checked like the usual folder (`protocol.ensure_private_dir`) before
    anything binds or connects there.
    """
    path = protocol.socket_path(kind, base)
    if _fits(path):
        return path
    tag = hashlib.sha256(os.fsencode(path.parent)).hexdigest()[:12]
    for parent in _fallback_parents():
        candidate = Path(parent) / f"vocalize-{os.getuid()}-{tag}" / path.name
        if _fits(candidate):
            return candidate
    return path  # nothing short enough: socket_problem says so


def socket_problem(kind, base=None) -> str | None:
    """Why no warm server can use `socket_path(kind, base)`, or None.

    Read-only: a folder that does not exist yet is fine, it is created on
    first use. Never raises.
    """
    try:
        path = socket_path(kind, base)
        if not _fits(path):
            return "the socket path is too long and no short private folder is available"
        try:
            info = path.parent.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            return "the socket folder is not a private folder owned by you"
    except Exception:  # noqa: BLE001 -- a report, never a crash
        return "the socket folder could not be checked"
    return None

# A detached uv process needs time to bind. Keep the reservation across
# short-lived CLI processes, without ever treating a wrapper PID as a worker.
_SPAWN_GRACE = 10.0
# A server still "loading" this long after the first try is treated as
# stuck: shut it down and fall back, rather than hold a take for the whole
# transcription deadline.
_LOADING_GRACE = 20.0


def _exchange(path, payload, deadline, *, answer=True):
    """Give connect, send and read one shared budget, not one timeout each."""
    if not protocol.check_socket_path(path):
        raise protocol.ProtocolError("failed")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        conn.settimeout(remaining)
        conn.connect(str(path))
        conn.settimeout(max(0.000001, deadline - time.monotonic()))
        protocol.send(conn, payload)
        return protocol.recv(conn, deadline) if answer else None


def live(kind, expected_fingerprint, base=None, timeout=0.1) -> dict | None:
    """A fingerprint match is required before any transcript is sent."""
    try:
        reply = _exchange(socket_path(kind, base), {"op": "hello"},
                          time.monotonic() + timeout)
        if reply.get("ok") is True and reply.get("fingerprint") == expected_fingerprint:
            return reply
    except Exception:  # noqa: BLE001, S110 -- warm failures must be silent and preserve fallback
        pass
    return None


def _spawn_pending(path) -> bool:
    """A spawn recorded in the lock file within _SPAWN_GRACE is still coming up."""
    try:
        marker = path.with_suffix(".lock").read_text(encoding="ascii")[:64]
        return 0 <= time.monotonic() - float(marker) < _SPAWN_GRACE
    except (OSError, ValueError):
        return False


def _stand_down(path) -> None:
    """Tell a spawn that has not bound yet to exit as soon as it does: this
    take gave up on it and is falling back, and two copies of a model must
    not load side by side (run 4 review). The server compares the note's
    time with its own start, so a later spawn is never told to stand down."""
    fd = None
    try:
        fd = os.open(path.with_suffix(".lock"), os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        info = os.fstat(fd)
        if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid():
            os.ftruncate(fd, 0)
            os.write(fd, f"stand-down {time.monotonic()}".encode("ascii"))
    except OSError:
        pass
    finally:
        if fd is not None:
            os.close(fd)


def _hello(path, deadline):
    """Treat missing, malformed and unresponsive listeners as unavailable."""
    try:
        return _exchange(path, {"op": "hello"}, deadline)
    except Exception:  # noqa: BLE001 -- warm failures must be silent and preserve fallback
        return None


def _ensure(kind, spawn_argv, expected, lease_id, base, env, spawn, deadline):
    """Serialize spawn and reserve startup time even after this CLI exits."""
    fd = None
    try:
        path = socket_path(kind, base)
        if not _fits(path):
            return  # no short private folder either; `listen --check` says so
        protocol.ensure_private_dir(path.parent)
        reply = _hello(path, min(deadline, time.monotonic() + 0.1))
        if reply and reply.get("ok") is True and reply.get("fingerprint") == expected:
            if lease_id is not None:
                _exchange(path, {"op": "lease", "id": lease_id}, deadline)
            return
        fd = os.open(path.with_suffix(".lock"),
                     os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            return
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        reply = _hello(path, min(deadline, time.monotonic() + 0.05))
        if reply and reply.get("ok") is True:
            if reply.get("fingerprint") == expected:
                if lease_id is not None:
                    _exchange(path, {"op": "lease", "id": lease_id}, deadline)
                return
            _exchange(path, {"op": "shutdown"}, deadline)
            # Let the old listener unlink before the new worker tries to bind.
            while protocol.check_socket_path(path) and time.monotonic() < deadline:
                time.sleep(0.005)
            if protocol.check_socket_path(path):
                return
        else:
            # A listener that missed hello may still own a model. Do not
            # start a duplicate merely because its control loop is stalled.
            if protocol.check_socket_path(path):
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.settimeout(max(0.000001, deadline - time.monotonic()))
                    try:
                        probe.connect(str(path))
                    except ConnectionRefusedError:
                        pass  # stale socket; the new worker removes it
                    else:
                        return
            marker = os.read(fd, 64)
            try:
                pending = 0 <= time.monotonic() - float(marker) < _SPAWN_GRACE
            except ValueError:
                pending = False
            if pending:
                return
        if time.monotonic() >= deadline:
            return
        lease = ["--lease", lease_id] if lease_id is not None else []
        spawn([*spawn_argv, *lease], start_new_session=True,
              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              cwd=tempfile.gettempdir(), close_fds=True,
              # Never the caller's environment: the allowlist is the default.
              env=runtime.environment() if env is None else env)
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, str(time.monotonic()).encode("ascii"))
    except Exception:  # noqa: BLE001, S110 -- warm failures must be silent and preserve fallback
        pass
    finally:
        if fd is not None:
            os.close(fd)


def ensure_warm(kind, spawn_argv: list[str], expected_fingerprint, lease_id, base=None,
                *, env=None, spawn=None) -> None:
    """Bound caller latency even if process creation or filesystem access stalls."""
    try:
        deadline = time.monotonic() + 0.2
        thread = threading.Thread(
            target=_ensure,
            args=(kind, spawn_argv, expected_fingerprint, lease_id, base, env,
                  spawn or subprocess.Popen, deadline), daemon=True,
        )
        thread.start()
        thread.join(0.23)
    except Exception:  # noqa: BLE001, S110 -- warm failures must be silent and preserve fallback
        pass


def lease(kind, expected_fingerprint, lease_id, base=None) -> bool:
    """Hold a live, matching server for this take; never spawn one (DEC-050).
    Bounded to 0.2 s. False when nothing warm is there to hold."""
    try:
        path = socket_path(kind, base)
        deadline = time.monotonic() + 0.2
        reply = _hello(path, deadline)
        if not (reply and reply.get("ok") is True and reply.get("fingerprint") == expected_fingerprint):
            return False
        return _exchange(path, {"op": "lease", "id": lease_id}, deadline).get("ok") is True
    except Exception:  # noqa: BLE001 -- warm failures must be silent and preserve fallback
        return False


def release(kind, lease_id, base=None):
    """A missed release is harmless because leases also expire automatically."""
    try:
        _exchange(socket_path(kind, base), {"op": "release", "id": lease_id},
                  time.monotonic() + 0.2)
    except Exception:  # noqa: BLE001, S110 -- warm failures must be silent and preserve fallback
        pass


def _stop(path, ident, deadline):
    """Retire the listener before fallback. Shutdown always gets its own 0.2 s,
    even when cancel used up the request's deadline (review, run 3)."""
    for payload in ([{"op": "cancel", "id": ident}] if ident else []) + [{"op": "shutdown"}]:
        try:
            shutdown = payload["op"] == "shutdown"
            if shutdown:
                deadline = max(deadline, time.monotonic() + 0.2)
            _exchange(path, payload, deadline, answer=shutdown)
            if shutdown:
                while protocol.check_socket_path(path) and time.monotonic() < deadline:
                    time.sleep(min(0.001, max(0, deadline - time.monotonic())))
        except Exception:  # noqa: BLE001, S110 -- warm failures must be silent and preserve fallback
            pass


#: Every kind of warm server. Parakeet has none: it runs one-shot.
KINDS = ("whisper", "llm")
# How long a stop on request waits for a server to let go of its model.
_STOP_SECONDS = 0.5


def _private_folder(path) -> bool:
    """The socket's folder exists, is a real folder, is yours and is 0700:
    what `ensure_private_dir` demands before anything binds or connects
    there, checked without creating or repairing anything."""
    try:
        info = Path(path).parent.lstat()
    except OSError:
        return False
    return (stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and not info.st_mode & 0o077)


def _known(kind) -> None:
    if kind not in KINDS:
        raise ValueError("unknown worker kind")


def is_running(kind, base=None) -> bool:
    """Whether a warm server of this kind answers at its socket now.

    Read-only, bounded to 0.1 s, never raises for a known kind: it creates
    no folder, and a hello is not use, so asking never keeps a model loaded.
    """
    _known(kind)
    try:
        path = socket_path(kind, base)
        if not (_private_folder(path) and protocol.check_socket_path(path)):
            return False
        reply = _hello(path, time.monotonic() + 0.1)
        return bool(reply) and reply.get("ok") is True
    except Exception:  # noqa: BLE001 -- a report, never a crash
        return False


def stop(kind, base=None) -> bool:
    """Stop the warm server of this kind, if one is running. True when one
    answered and was told to stop; False when none was running.

    Looks where the server really is (`socket_path`, short fallback folder
    included) and only behind the client's own checks: a private folder
    and a socket owned by you. A listener that no longer answers is still
    told to stop, but is not reported as a stopped server.
    """
    _known(kind)
    try:
        path = socket_path(kind, base)
        if not (_private_folder(path) and protocol.check_socket_path(path)):
            return False
        deadline = time.monotonic() + _STOP_SECONDS
        reply = _hello(path, min(deadline, time.monotonic() + 0.1))
        _stop(path, None, deadline)
        return bool(reply) and reply.get("ok") is True
    except Exception:  # noqa: BLE001 -- a stop on request must never crash its caller
        return False


def stop_all(base=None) -> dict:
    """Stop every kind of warm server: {kind: whether one was stopped}."""
    stopped = {}
    for kind in KINDS:
        try:
            stopped[kind] = stop(kind, base)
        except Exception:  # noqa: BLE001 -- one failure must not skip the other kind
            stopped[kind] = False
    return stopped


def request(kind, payload: dict, expected_fingerprint, deadline_s, base=None) -> dict | None:
    """Retry loading within one deadline; any other failure retires the warm model."""
    path = ident = None
    deadline = time.monotonic()
    try:
        if not math.isfinite(deadline_s) or deadline_s <= 0:
            return None
        deadline += deadline_s
        # Reserve a small part of the same deadline for fallback control messages.
        work_deadline = deadline - min(0.02, deadline_s / 10)
        path = socket_path(kind, base)
        give_up = min(work_deadline, time.monotonic() + _LOADING_GRACE)
        while time.monotonic() < give_up:
            if not protocol.check_socket_path(path) and _spawn_pending(path):
                # The server this take spawned has not bound yet. Falling
                # back now would load a second copy of the model beside it
                # (review, run 3), so wait for it within the same deadline.
                time.sleep(min(0.1, max(0, work_deadline - time.monotonic())))
                continue
            hello = _exchange(path, {"op": "hello"}, work_deadline)
            if hello.get("ok") is not True:
                break
            if hello.get("fingerprint") != expected_fingerprint:
                return None
            ident = uuid.uuid4().hex
            reply = _exchange(path, {**payload, "id": ident,
                                    "deadline_s": max(0, work_deadline - time.monotonic())},
                              work_deadline)
            if reply.get("ok") is True:
                return reply
            if reply.get("error") != "loading":
                break
            time.sleep(min(0.1, max(0, work_deadline - time.monotonic())))
    except Exception:  # noqa: BLE001, S110 -- warm failures must be silent and preserve fallback
        pass
    if path is not None:
        if not protocol.check_socket_path(path) and _spawn_pending(path):
            _stand_down(path)
        _stop(path, ident, deadline)
    return None
