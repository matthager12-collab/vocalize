"""DEC-047 release gate: real cold starts, reuse, footprint, and cross-take canaries.

Run with VOCALIZE_EVAL=1 and pytest -s to see every measurement. No downloads.
"""

import os
import re
import shlex
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest
from test_whisper_leak import VOCABULARY, write_noise

from vocalize import config, dictate, llm
from vocalize.local import llm_manifest, warm, warm_protocol, whisper_manifest

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(os.environ.get("VOCALIZE_EVAL") != "1", reason="set VOCALIZE_EVAL=1"),
]
REAL_WHISPER = whisper_manifest.MODEL_DIR
REAL_LLM = llm_manifest.MODEL_DIR


def serving_processes(*, python_only=True):
    """Inspect Python workers, excluding uv wrappers with the same argv tail."""
    result = subprocess.run(
        ["/usr/bin/pgrep", "-f", "_worker.py.*--serve"],
        capture_output=True, text=True, check=False, timeout=2,
    )
    assert result.returncode in (0, 1), "pgrep failed"
    workers = {}
    for pid in result.stdout.split():
        result = subprocess.run(
            ["/bin/ps", "-p", pid, "-o", "command="],
            capture_output=True, text=True, check=False, timeout=2,
        )
        argv = shlex.split(result.stdout.strip())
        if not argv or (python_only and not Path(argv[0]).name.lower().startswith("python")):
            continue
        for kind in ("whisper", "llm"):
            if any(Path(arg).name == f"{kind}_worker.py" for arg in argv) and "--serve" in argv:
                workers[int(pid)] = kind
    return workers


def footprint_gb(output):
    match = re.search(r"Physical footprint \(peak\):\s*([\d.]+)\s*([KMGT]?)", output)
    assert match, "vmmap did not report Physical footprint (peak)"
    return float(match[1]) * 1024 ** (" KMGT".index(match[2]) if match[2] else 0) / 1e9


class MemorySamples:
    """Observe the actual model processes throughout startup and inference."""

    def __init__(self):
        self.stop = threading.Event()
        self.samples = []
        self.errors = []
        self.thread = threading.Thread(target=self.sample, daemon=True)

    def sample(self):
        try:
            while not self.stop.is_set():
                workers = serving_processes()
                counts = {kind: list(workers.values()).count(kind) for kind in ("whisper", "llm")}
                peaks = {"whisper": 0.0, "llm": 0.0}
                for pid, kind in workers.items():
                    result = subprocess.run(
                        ["/usr/bin/vmmap", "--summary", str(pid)],
                        capture_output=True, text=True, check=False, timeout=3,
                    )
                    # A just-shut-down process can vanish between pgrep and vmmap.
                    if result.returncode and pid not in serving_processes():
                        continue
                    assert result.returncode == 0, "vmmap failed"
                    peaks[kind] = max(peaks[kind], footprint_gb(result.stdout))
                self.samples.append((counts, peaks))
                self.stop.wait(0.05)
        except Exception as exc:  # noqa: BLE001 -- propagate sampler failures to the test
            self.errors.append(exc)

    def finish(self):
        self.stop.set()
        self.thread.join(10)
        assert not self.thread.is_alive(), "memory sampler did not stop"
        assert not self.errors, self.errors


def shutdown(bases):
    """Send lifecycle control to our private sockets; never signal wrapper PIDs."""
    deadline = time.monotonic() + 5
    while True:
        for base in bases:
            for kind in ("whisper", "llm"):
                path = warm_protocol.socket_path(kind, base)
                if not warm_protocol.check_socket_path(path):
                    continue
                try:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                        conn.settimeout(0.2)
                        conn.connect(str(path))
                        warm_protocol.send(conn, {"op": "shutdown"})
                        warm_protocol.recv(conn, time.monotonic() + 0.2)
                except (OSError, warm_protocol.ProtocolError):
                    pass
        workers = serving_processes(python_only=False)
        sockets = any(warm_protocol.check_socket_path(warm_protocol.socket_path(k, b))
                      for b in bases for k in ("whisper", "llm"))
        if not workers and not sockets:
            return
        if time.monotonic() >= deadline:
            assert not workers and not sockets, "--serve processes or sockets remain after 5 s"
        time.sleep(0.05)


def jargon_clip(tmp_path):
    committed = Path(__file__).parent / "jargon.wav"
    if committed.is_file():
        return committed
    path = tmp_path / "jargon.wav"
    subprocess.run(
        ["/usr/bin/say", "-o", str(path), "--data-format=LEI16@16000", "--channels=1",
         "Read the pyproject at the repository root, then check sha256."],
        check=True, timeout=20,
    )
    return path


def test_warm_timing_memory_and_canaries(tmp_path, monkeypatch):
    try:
        available = ((REAL_WHISPER / "ggml-large-v3-turbo-q5_0.bin").is_file()
                     and (REAL_LLM / "model.safetensors").is_file())
    except OSError:
        available = False
    if not available:
        pytest.skip("both real models must be installed and readable")
    monkeypatch.setattr(whisper_manifest, "MODEL_DIR", REAL_WHISPER)
    monkeypatch.setattr(llm_manifest, "MODEL_DIR", REAL_LLM)
    # The eval is about the models, not the install stamp (as in
    # test_cleanup_eval): a model installed before #16 has no .verified.
    from vocalize.local import install

    monkeypatch.setattr(install, "installed", lambda *args, **kwargs: (True, ""))
    assert not serving_processes(python_only=False), "stop existing warm servers before this isolated eval"
    stt = config.resolve_stt({"stt": {"cleanup": "local", "vocabulary": VOCABULARY}})
    clip = jargon_clip(tmp_path)
    system = llm.CLEANUP_PROMPT.rstrip() + "\n\n" + llm.DATA_BOUNDARY
    waits, samples, bases = {}, {}, []
    vocabulary = config.vocabulary_prompt(VOCABULARY)
    # Short paths are required for sockaddr_un on macOS. TemporaryDirectory is 0700.
    with tempfile.TemporaryDirectory(prefix="vw-", dir="/tmp") as root:
        try:
            # 1. Today's path, with no warm endpoint anywhere: the baseline.
            missing = Path(root) / "missing"
            monkeypatch.setattr(warm_protocol, "warm_dir", lambda base=None: Path(base) if base else missing)
            # The faster of two runs: the first pays for a cold page cache,
            # which would flatter every warm number compared with it.
            runs = []
            for _ in range(2):
                start = time.monotonic()
                text = dictate.transcribe(clip, stt)
                assert text.strip()
                assert llm._local(system, text, 60, 1024, warm_ok=False)
                runs.append(time.monotonic() - start)
            one_shot = min(runs)
            print(f"one-shot = {one_shot:.3f} s (runs {runs[0]:.3f}, {runs[1]:.3f})", flush=True)

            # 2. DEC-050's first take after idle: warm paths tried, nothing
            # there, so it must cost what today costs.
            start = time.monotonic()
            text = dictate.transcribe(clip, stt)
            assert llm._local(system, text, 60, 1024, warm_ok=True)
            waits["cold first take"] = time.monotonic() - start
            print(f"cold first take = {waits['cold first take']:.3f} s", flush=True)

            base = Path(root) / "warm"
            bases.append(base)
            specs = dictate._warm_specs(stt, base=base)
            assert {s[0] for s in specs} == {"whisper", "llm"}, "both models must be ready"
            fingerprints = {kind: fp for kind, argv, env, fp in specs}

            def request(kind, payload):
                reply = warm.request(kind, payload, fingerprints[kind], deadline_s=60, base=base)
                assert reply and reply.get("ok") is True and isinstance(reply.get("text"), str)
                return reply["text"]

            def transcribe(path, prompt):
                return request("whisper", {"op": "transcribe", "wav": str(path),
                                           "language": stt["language"], "initial_prompt": prompt})

            def complete(text):
                return request("llm", {"op": "complete", "system": system,
                                       "text": text, "max_tokens": 1024})

            def take(label):
                stop = time.monotonic()
                text = transcribe(clip, vocabulary)
                assert text.strip(), "jargon transcription was empty"
                assert complete(text).strip(), "jargon cleanup was empty"
                waits[label] = time.monotonic() - stop
                print(f"{label}: wait after stop = {waits[label]:.3f} s", flush=True)

            memory = MemorySamples()
            memory.thread.start()
            try:
                # 3. What `_warm_after` does once a take is delivered: spawn
                # both, unleased, so they idle out after the window.
                for kind, argv, env, fingerprint in specs:
                    warm.ensure_warm(kind, argv, fingerprint, None, base=base, env=env)
                time.sleep(1.0)  # the next take lands while both still load
                take("take 1 s after the last")
                take("back-to-back-1")
                take("back-to-back-2")
                assert "zebracanary" in complete("Remember zebracanary.").lower()
                second = complete("The meeting starts tomorrow.")
                assert "zebracanary" not in second.lower()
                print("LLM canary crossings = 0", flush=True)
                jargon = transcribe(clip, vocabulary)
                assert "pyproject" in jargon.lower(), "jargon clip did not establish the canary"
                noise = tmp_path / "noise.wav"
                write_noise(noise, rms=40, seed=40)
                assert "pyproject" not in transcribe(noise, "").lower()
                print("Whisper canary crossings = 0", flush=True)
            finally:
                memory.finish()
                samples["warm"] = memory.samples
            shutdown([base])

            all_samples = samples["warm"]
            assert all_samples, "no memory samples collected"
            peaks = {kind: max(p[kind] for _, p in all_samples) for kind in ("whisper", "llm")}
            together = max(sum(p.values()) for _, p in all_samples)
            count = max(c["llm"] for c, _ in all_samples)
            print(f"maximum LLM processes = {count}", flush=True)
            assert count == 1, "missing or duplicate LLM server"
            for kind, peak in peaks.items():
                print(f"{kind} physical footprint peak = {peak:.3f} GB", flush=True)
                assert peak > 0, f"no {kind} memory measurement"
            print(f"combined physical footprint peak = {together:.3f} GB", flush=True)
            for label, duration in waits.items():
                print(f"{label}: saving over one-shot = {one_shot - duration:.3f} s", flush=True)
            # DEC-050's gates: never slower than today (0.3 s of noise
            # allowed), and at least 1.0 s faster once warm.
            assert waits["cold first take"] <= one_shot + 0.3
            assert waits["take 1 s after the last"] <= one_shot + 0.3
            assert all(one_shot - waits[label] >= 1.0 for label in ("back-to-back-1", "back-to-back-2"))
            assert peaks["llm"] <= 3.6
            assert peaks["whisper"] <= 1.0
            assert together <= 4.6
        finally:
            shutdown(bases)
            print("remaining --serve processes = 0", flush=True)
