"""Physical-footprint qualification is model-free here, never an RSS fallback."""
import ctypes
import errno
import importlib.util
import shlex
import subprocess
import sys
from pathlib import Path

import eval_memory
import pytest


def warm_module(monkeypatch):
    directory = Path(__file__).parent / "eval"
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location("warm_eval_memory_check", directory / "test_warm_timing.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_usage(monkeypatch, *, code=0, error=0, current=10, peak=20, start=30):
    def query(pid, flavor, pointer):
        assert flavor == 4
        data = ctypes.cast(pointer, ctypes.POINTER(eval_memory.RUsageInfoV4)).contents
        data.phys_footprint = current
        data.lifetime_max_phys_footprint = peak
        data.proc_start_abstime = start
        ctypes.set_errno(error)
        return code
    monkeypatch.setattr(eval_memory, "_rusage_function", lambda: query)


def test_public_v4_abi_layout_and_lifetime_metric(monkeypatch):
    assert ctypes.sizeof(eval_memory.RUsageInfoV4) == 296
    assert eval_memory.RUsageInfoV4.phys_footprint.offset == 72
    assert eval_memory.RUsageInfoV4.lifetime_max_phys_footprint.offset == 240
    fake_usage(monkeypatch)
    measurement = eval_memory.physical_footprint(123)
    assert measurement["current_bytes"] == 10
    assert measurement["peak_bytes"] == 20
    assert measurement["identity"][0] == 30


@pytest.mark.parametrize("pid", [True, 0, -1, 1, "123", 2**32])
def test_bad_pid_never_queries_os(monkeypatch, pid):
    monkeypatch.setattr(eval_memory, "_rusage_function", lambda: pytest.fail("must not call libproc"))
    with pytest.raises(ValueError):
        eval_memory.physical_footprint(pid)


@pytest.mark.parametrize("error", [errno.EPERM, errno.EACCES, errno.EINVAL, 0])
def test_denied_or_unsupported_measurement_fails_closed(monkeypatch, error):
    fake_usage(monkeypatch, code=-1, error=error)
    with pytest.raises(eval_memory.MemoryMeasurementError, match="qualification blocked"):
        eval_memory.physical_footprint(123)


@pytest.mark.parametrize("kwargs", [{"current": 0}, {"peak": 0}, {"start": 0}, {"current": 30, "peak": 20}])
def test_empty_or_invalid_counters_are_not_green(monkeypatch, kwargs):
    fake_usage(monkeypatch, **kwargs)
    with pytest.raises(eval_memory.MemoryMeasurementError, match="invalid"):
        eval_memory.physical_footprint(123)


def test_exited_process_is_distinguished_from_permission_denial(monkeypatch):
    fake_usage(monkeypatch, code=-1, error=errno.ESRCH)
    with pytest.raises(ProcessLookupError):
        eval_memory.physical_footprint(123)


def test_only_executed_worker_script_is_a_python_model_process():
    local = Path(__file__).resolve().parent.parent / "vocalize/local"
    worker = str(local / "llm_worker.py")
    assert eval_memory.worker_kind(shlex.join([sys.executable, worker, "--serve"])) == "llm"
    assert eval_memory.worker_kind(shlex.join([sys.executable, "-I", str(local / "runtime_worker.py"), "whisper", "--serve"])) == "whisper"
    for argv in (
        [sys.executable, "/tmp/fake-uv-wrapper.py", "run", worker, "--serve"],
        [sys.executable, "-c", "pass", worker, "--serve"],
        [sys.executable, "-m", "unrelated", worker, "--serve"],
        ["/tmp/uv", "run", worker, "--serve"],
        [sys.executable, "/tmp/llm_worker.py", "--serve"],
        [sys.executable, str(local / "runtime_worker.py"), "../../other", "--serve"],
    ):
        assert eval_memory.worker_kind(shlex.join(argv)) is None
    assert eval_memory.worker_kind("unterminated '") is None


def test_sampler_uses_lifetime_peaks_not_current_or_rss(monkeypatch):
    module = warm_module(monkeypatch)
    monkeypatch.setattr(module, "serving_processes", lambda: {123: "llm", 124: "whisper"})
    monkeypatch.setattr(module, "physical_footprint", lambda pid: {
        "identity": (pid, b"uuid"), "current_bytes": 1,
        "peak_bytes": 3_000_000_000 if pid == 123 else 800_000_000,
    })
    sampler = module.MemorySamples()
    sampler.sample_once()
    assert sampler.samples == [({"llm": 1, "whisper": 1}, {"llm": 3.0, "whisper": 0.8})]


def test_sampler_never_queries_unowned_processes(monkeypatch):
    module = warm_module(monkeypatch)
    monkeypatch.setattr(module, "owned_group", lambda: 321)
    monkeypatch.setattr(module, "group_processes", lambda group: {})
    monkeypatch.setattr(module, "physical_footprint", lambda pid: pytest.fail("must not query"))
    sampler = module.MemorySamples()
    sampler.sample_once()
    assert sampler.samples == [({"llm": 0, "whisper": 0}, {"llm": 0, "whisper": 0})]


def test_duplicate_workers_cannot_hide_combined_footprint(monkeypatch):
    module = warm_module(monkeypatch)
    monkeypatch.setattr(module, "serving_processes", lambda: {123: "llm", 124: "whisper", 125: "whisper"})
    monkeypatch.setattr(module, "physical_footprint", lambda pid: {
        "identity": (pid, b"uuid"), "current_bytes": 1,
        "peak_bytes": 3_500_000_000 if pid == 123 else 800_000_000,
    })
    sampler = module.MemorySamples()
    sampler.sample_once()
    counts, peaks = sampler.samples[0]
    assert counts == {"llm": 1, "whisper": 2}
    assert peaks == {"llm": 3.5, "whisper": 1.6}
    assert sum(peaks.values()) == 5.1


def test_sampler_surfaces_denial_instead_of_substituting_zero(monkeypatch):
    module = warm_module(monkeypatch)
    monkeypatch.setattr(module, "serving_processes", lambda: {123: "llm"})
    def denied(pid):
        raise eval_memory.MemoryMeasurementError("qualification blocked")
    monkeypatch.setattr(module, "physical_footprint", denied)
    sampler = module.MemorySamples()
    with pytest.raises(eval_memory.MemoryMeasurementError, match="blocked"):
        sampler.sample_once()
    assert sampler.samples == []


def test_sampler_rejects_ownership_loss_and_pid_reuse(monkeypatch):
    module = warm_module(monkeypatch)
    membership = iter([{123: "llm"}, {}])
    monkeypatch.setattr(module, "serving_processes", lambda: next(membership))
    monkeypatch.setattr(module, "physical_footprint", lambda pid: {"identity": (1, b"a"), "peak_bytes": 10})
    sampler = module.MemorySamples()
    with pytest.raises(eval_memory.MemoryMeasurementError, match="identity changed"):
        sampler.sample_once()
    monkeypatch.setattr(module, "serving_processes", lambda: {123: "llm"})
    sampler.sample_once()
    monkeypatch.setattr(module, "physical_footprint", lambda pid: {"identity": (2, b"b"), "peak_bytes": 10})
    with pytest.raises(eval_memory.MemoryMeasurementError, match="reused"):
        sampler.sample_once()


@pytest.mark.skipif(sys.platform != "darwin", reason="public macOS libproc API")
def test_real_owned_synthetic_child_has_physical_peak():
    child = subprocess.Popen(
        [sys.executable, "-I", "-c", "import sys; x=bytearray(4*1024*1024); print('ready',flush=True); sys.stdin.read(1)"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        sample = eval_memory.physical_footprint(child.pid)
        assert sample["peak_bytes"] >= sample["current_bytes"] >= 4 * 1024**2
    finally:
        child.communicate("x", timeout=5)
