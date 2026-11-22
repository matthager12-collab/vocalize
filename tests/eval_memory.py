"""Owned-evaluation memory helpers; never request task ports or elevated rights.

Apple's public libproc.h / sys/resource.h define proc_pid_rusage and the v4
ABI. Lifetime physical-footprint peaks preserve the existing warm thresholds;
RSS, missing measurements and access errors are never substitutes for a peak.
"""

import ctypes
import errno
import os
import shlex
import sys
from functools import lru_cache
from pathlib import Path


class MemoryMeasurementError(RuntimeError):
    pass


class RUsageInfoV4(ctypes.Structure):
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64) for name in (
            "user_time", "system_time", "pkg_idle_wkups", "interrupt_wkups",
            "pageins", "wired_size", "resident_size", "phys_footprint",
            "proc_start_abstime", "proc_exit_abstime", "child_user_time",
            "child_system_time", "child_pkg_idle_wkups", "child_interrupt_wkups",
            "child_pageins", "child_elapsed_abstime", "diskio_bytesread",
            "diskio_byteswritten", "cpu_time_qos_default", "cpu_time_qos_maintenance",
            "cpu_time_qos_background", "cpu_time_qos_utility", "cpu_time_qos_legacy",
            "cpu_time_qos_user_initiated", "cpu_time_qos_user_interactive",
            "billed_system_time", "serviced_system_time", "logical_writes",
            "lifetime_max_phys_footprint", "instructions", "cycles", "billed_energy",
            "serviced_energy", "interval_max_phys_footprint", "runnable_time",
        )
    ]


@lru_cache(maxsize=1)
def _rusage_function():
    if sys.platform != "darwin":
        raise MemoryMeasurementError("physical footprint measurement requires macOS")
    try:
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        function = library.proc_pid_rusage
        function.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        function.restype = ctypes.c_int
        return function
    except (OSError, AttributeError) as exc:
        raise MemoryMeasurementError("proc_pid_rusage is unavailable; qualification blocked") from exc


def physical_footprint(pid):
    """Caller must establish evaluation ownership before and after this read."""
    if isinstance(pid, bool) or not isinstance(pid, int) or not 1 < pid <= 2**31 - 1:
        raise ValueError("invalid owned PID")
    data = RUsageInfoV4()
    ctypes.set_errno(0)
    if _rusage_function()(pid, 4, ctypes.byref(data)) != 0:
        code = ctypes.get_errno()
        if code == errno.ESRCH:
            raise ProcessLookupError(code, "owned worker exited")
        raise MemoryMeasurementError(
            f"proc_pid_rusage failed for owned PID {pid} (errno {code}); qualification blocked"
        )
    peak = data.lifetime_max_phys_footprint
    if not data.proc_start_abstime or not data.phys_footprint or peak < data.phys_footprint:
        raise MemoryMeasurementError(f"invalid physical-footprint counters for owned PID {pid}")
    return {
        "identity": (data.proc_start_abstime, bytes(data.uuid)),
        "current_bytes": data.phys_footprint,
        "peak_bytes": peak,
    }


def worker_kind(command, *, python_only=True):
    """Match the executed script, not a later uv argument naming a worker."""
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if not argv or "--serve" not in argv:
        return None
    local = Path(__file__).resolve().parent.parent / "vocalize" / "local"
    if not python_only:
        for kind in ("llm", "whisper"):
            if str(local / f"{kind}_worker.py") in argv:
                return kind
        # Explicit interpreter launches use the bootstrap, including while
        # a wrapper is finishing exec; inspect only this known script token.
        bootstrap = str(local / "runtime_worker.py")
        if bootstrap in argv:
            index = argv.index(bootstrap)
            if len(argv) > index + 1 and argv[index + 1] in ("llm", "whisper"):
                return argv[index + 1]
        return None
    if not Path(argv[0]).name.lower().startswith("python"):
        return None
    index = 1
    while index < len(argv) and argv[index] in ("-I", "-B", "-u", "-E", "-s"):
        index += 1
    if index >= len(argv):
        return None
    script = os.path.normpath(argv[index])
    for kind in ("llm", "whisper"):
        if script == str(local / f"{kind}_worker.py"):
            return kind
    if (script == str(local / "runtime_worker.py") and len(argv) > index + 1
            and argv[index + 1] in ("llm", "whisper")):
        return argv[index + 1]
    return None
