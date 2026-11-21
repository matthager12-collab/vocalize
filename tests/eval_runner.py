"""Bounded, opt-in local-model evaluations; also tested with synthetic children.

The anchor stays alive and unreaped until its entire group is killed. Only
that group is ever signalled. This is cooperative containment of our reviewed
workers, not a sandbox against a program deliberately calling setsid().
"""

from __future__ import annotations

import argparse
import json
import math
import os
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

OWNER = "_VOCALIZE_EVAL_GROUP_OWNER"
TEMP_ROOT = "_VOCALIZE_EVAL_TEMP_ROOT"
STAGE_SECONDS = 900
CLEANUP_SECONDS = 10
STAGES = {
    "cleanup": "tests/eval/test_cleanup_eval.py::test_cleanup_eval",
    "whisper": "tests/eval/test_whisper_leak.py::test_vocabulary_does_not_leak_into_unrelated_audio",
    "warm": "tests/eval/test_warm_timing.py::test_warm_timing_memory_and_canaries",
}


class EvaluationTimeout(TimeoutError):
    pass


class EvaluationInterrupted(BaseException):
    pass


def owned_group():
    """Reject direct pytest invocation; this marker is not an authorization token."""
    value = os.environ.get(OWNER, "")
    if not value.isascii() or not value.isdigit():
        raise RuntimeError("use tests/eval_runner.py in an approved exclusive window")
    group = int(value)
    if group <= 1 or group == os.getpid() or os.getpgrp() != group or os.getsid(0) != group:
        raise RuntimeError("evaluation is outside its supervisor's process group")
    return group


def contained_popen(real_popen):
    """Keep eval-only children in the anchor's group, including warm uv wrappers."""
    def spawn(argv, *args, **kwargs):
        owned_group()
        if not isinstance(argv, (list, tuple)) or not argv or kwargs.get("shell"):
            raise ValueError("evaluation commands must be argv arrays without a shell")
        if kwargs.get("preexec_fn") is not None or kwargs.get("process_group") not in (None, -1):
            raise ValueError("evaluation children cannot change process groups")
        kwargs["start_new_session"] = False
        return real_popen(argv, *args, **kwargs)
    return spawn


def group_processes(group, *, timeout=2):
    """Query only the owned session/group, then filter before reading command text.

    BSD ps -g selects process groups; Linux ps -g selects sessions. The anchor
    leads both, and contained workers share both. Do not replace with pgrep.
    """
    if not isinstance(group, int) or isinstance(group, bool) or group <= 1:
        raise ValueError("invalid evaluation group")
    result = subprocess.run(
        ["/bin/ps", "-g", str(group), "-o", "pid=,pgid=,stat=,command="],
        capture_output=True, text=True, check=False, timeout=timeout,
    )
    if result.returncode not in (0, 1) or (result.returncode == 1 and result.stderr.strip()):
        raise RuntimeError("could not inspect evaluation process group")
    rows = {}
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 3)
        if len(fields) >= 3 and fields[0].isdigit() and fields[1] == str(group):
            rows[int(fields[0])] = (fields[2], fields[3] if len(fields) == 4 else "")
    return rows


def _anchor(status_fd, lifetime_fd, argv):
    """Keep the group identity reserved, including after the test process exits."""
    if os.getpid() != os.getpgrp() or os.getsid(0) != os.getpid():
        raise RuntimeError("anchor must lead its own session")

    def parent_lost():
        # Only the outer supervisor owns the write end; no child inherits it.
        os.read(lifetime_fd, 1)
        os.killpg(os.getpid(), signal.SIGKILL)

    threading.Thread(target=parent_lost, daemon=True).start()
    os.write(status_fd, b"ready\n")
    env = dict(os.environ, **{OWNER: str(os.getpid())})
    child = subprocess.Popen(argv, env=env, close_fds=True)
    code = child.wait()
    os.write(status_fd, f"done:{code}\n".encode("ascii"))
    # The outer supervisor kills this group on every outcome, even success.
    while True:
        signal.pause()


def _cleanup(anchor):
    deadline = time.monotonic() + CLEANUP_SECONDS
    # No poll()/wait() before the final signal: retain the anchor PID/PGID.
    try:
        os.killpg(anchor.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    anchor.wait(timeout=max(0.001, deadline - time.monotonic()))
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("evaluation cleanup exceeded its 10-second budget")
        rows = group_processes(anchor.pid, timeout=min(2, remaining))
        # Dead orphan zombies hold no model memory or executable work. Record
        # them rather than claiming the OS has reaped processes we cannot reap.
        live = {pid: state for pid, (state, _) in rows.items() if not state.startswith("Z")}
        if not live:
            print(f"eval cleanup: live_owned=0, orphan_zombies={len(rows)}", flush=True)
            return
        time.sleep(min(0.05, remaining))


def run_owned(argv, *, timeout, env=None, pytest_temp=False):
    """One wall-clock deadline, a separate bounded cleanup, and no pipe draining."""
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= STAGE_SECONDS:
        raise ValueError("timeout must be finite and in (0, 900]")
    if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(arg, str) for arg in argv):
        raise ValueError("expected a nonempty string argv array")
    deadline = time.monotonic() + timeout
    root = Path(tempfile.mkdtemp(prefix="ve-", dir="/tmp"))
    child_env = dict(os.environ if env is None else env)
    child_env.update({TEMP_ROOT: str(root), "TMPDIR": str(root)})
    if pytest_temp:
        argv = [*argv, "--basetemp", str(root / "pytest")]
    status_read, status_write = os.pipe()
    lifetime_read, lifetime_write = os.pipe()
    anchor = None
    cleaned = False
    try:
        anchor = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--anchor", str(status_write),
             str(lifetime_read), json.dumps(argv)],
            start_new_session=True, pass_fds=(status_write, lifetime_read), env=child_env,
        )
        os.close(status_write)
        status_write = None
        os.close(lifetime_read)
        lifetime_read = None
        os.set_blocking(status_read, False)
        with selectors.DefaultSelector() as selector:
            selector.register(status_read, selectors.EVENT_READ)
            data = b""
            ready = False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise EvaluationTimeout(f"evaluation exceeded {timeout:g} seconds")
                if not selector.select(min(0.2, remaining)):
                    continue
                part = os.read(status_read, 128)
                if not part:
                    raise RuntimeError("evaluation anchor exited without a completion status")
                data += part
                if len(data) > 128:
                    raise RuntimeError("oversized evaluation status")
                while b"\n" in data:
                    line, data = data.split(b"\n", 1)
                    if line == b"ready" and not ready:
                        ready = True
                    elif ready and line.startswith(b"done:"):
                        return int(line[5:])
                    else:
                        raise RuntimeError("invalid evaluation status")
    finally:
        try:
            if anchor is not None:
                _cleanup(anchor)
            cleaned = True
        finally:
            for fd in (status_read, status_write, lifetime_read, lifetime_write):
                if fd is not None:
                    os.close(fd)
            if cleaned:
                shutil.rmtree(root)
            else:
                print(f"eval cleanup unconfirmed; retained synthetic stage files: {root}", file=sys.stderr)


def _interrupted(_signal, _frame):
    raise EvaluationInterrupted("evaluation interrupted")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["all", *STAGES])
    parser.add_argument("--exclusive-window-confirmed", action="store_true",
                        help="operator has approval and has independently confirmed production is idle")
    args = parser.parse_args(argv)
    if not args.exclusive_window_confirmed:
        parser.error("requires an approved exclusive window; this command does not stop production")
    env = dict(os.environ)
    env.update(VOCALIZE_EVAL="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
               UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never",
               HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    root = Path(__file__).resolve().parent.parent
    env["PYTHONPATH"] = str(root)
    os.chdir(root)
    old = {sig: signal.signal(sig, _interrupted) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        for stage in STAGES if args.stage == "all" else [args.stage]:
            print(f"eval stage: {stage}; deadline={STAGE_SECONDS}s; cleanup<={CLEANUP_SECONDS}s", flush=True)
            code = run_owned(
                [sys.executable, "-m", "pytest", "-q", "-s", "-p", "no:cacheprovider",
                 "-o", "addopts=", STAGES[stage]], timeout=STAGE_SECONDS, env=env, pytest_temp=True,
            )
            if code:
                return code
        return 0
    except (EvaluationTimeout, EvaluationInterrupted) as exc:
        print(str(exc), file=sys.stderr)
        return 124 if isinstance(exc, EvaluationTimeout) else 130
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--anchor":
        _anchor(int(sys.argv[2]), int(sys.argv[3]), json.loads(sys.argv[4]))
    else:
        raise SystemExit(main())
