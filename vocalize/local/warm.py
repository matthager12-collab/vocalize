"""Best-effort warm reuse: every public entry point preserves one-shot fallback."""

from __future__ import annotations

import fcntl
import math
import os
import socket
import stat
import subprocess
import tempfile
import threading
import time
import uuid

from vocalize.local import warm_protocol as protocol

# A detached uv process needs time to bind. Keep the reservation across
# short-lived CLI processes, without ever treating a wrapper PID as a worker.
_SPAWN_GRACE = 10.0


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
        reply = _exchange(protocol.socket_path(kind, base), {"op": "hello"},
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
        path = protocol.socket_path(kind, base)
        if len(os.fsencode(path)) > 100:
            return
        protocol.ensure_private_dir(path.parent)
        reply = _hello(path, min(deadline, time.monotonic() + 0.1))
        if reply and reply.get("ok") is True and reply.get("fingerprint") == expected:
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
        spawn([*spawn_argv, "--lease", lease_id], start_new_session=True,
              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              cwd=tempfile.gettempdir(), close_fds=True, env=env)
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


def release(kind, lease_id, base=None):
    """A missed release is harmless because leases also expire automatically."""
    try:
        _exchange(protocol.socket_path(kind, base), {"op": "release", "id": lease_id},
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
        path = protocol.socket_path(kind, base)
        while time.monotonic() < work_deadline:
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
        _stop(path, ident, deadline)
    return None
