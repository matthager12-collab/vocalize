"""The local-only contract (design.md § Local-only contract), held by tests.

Nothing here starts a worker, a uv process or a network connection: every
command line is built through the real code path with its process seam
faked, and inspected.
"""

import ast
import io
import socket
import subprocess
from pathlib import Path

import pytest

from vocalize import config, dictate, llm
from vocalize import local as local_module
from vocalize.config import Settings
from vocalize.local import (
    install,
    kokoro_manifest,
    llm_manifest,
    parakeet_manifest,
    piper_manifest,
    runtime,
    warm,
    whisper_manifest,
)
from vocalize.providers import kokoro, piper

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
UV = "/synthetic/uv"
OFFLINE_FLAGS = ("--offline", "--no-python-downloads")

# --- 1. no network or hosted SDK outside the named modules -----------------

BANNED = (
    "urllib.request", "http.client", "http.server", "socket", "ssl", "requests",
    "httpx", "aiohttp", "websockets", "ftplib", "smtplib", "boto3", "elevenlabs",
    "openai", "anthropic", "google.cloud",
)
ALLOWED = {
    "vocalize/local/install.py",        # the pinned model downloader
    "vocalize/portal.py",               # the loopback settings page
    "vocalize/local/warm.py",           # Unix sockets to warm workers
    "vocalize/local/warm_protocol.py",  # Unix sockets to warm workers
}


def _banned(name: str) -> bool:
    return any(name == b or name.startswith(b + ".") for b in BANNED)


def _imported_names(tree) -> set[str]:
    """Every absolute module a file can reach by import statement or by a
    literal `importlib.import_module` / `__import__` call."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if called in ("import_module", "__import__") and isinstance(node.args[0].value, str):
                names.add(node.args[0].value)
    return names


def _sources():
    for top in ("vocalize", "hooks"):
        for path in sorted((ROOT / top).rglob("*.py")):
            yield path.relative_to(ROOT).as_posix(), path


def test_no_module_imports_a_network_client_or_hosted_sdk_outside_the_allowlist():
    offenders = {}
    for rel, path in _sources():
        if rel in ALLOWED:
            continue
        found = sorted(n for n in _imported_names(ast.parse(path.read_text(), rel)) if _banned(n))
        if found:
            offenders[rel] = found
    assert offenders == {}


def test_the_allowlist_names_files_that_exist():
    assert all((ROOT / rel).is_file() for rel in ALLOWED)


@pytest.mark.parametrize("source,caught", [
    ("import urllib.request", True),
    ("from urllib import request", True),
    ("from urllib.request import urlopen", True),
    ("from http import client", True),
    ("import socket as s", True),
    ("from google.cloud import texttospeech", True),
    ("from google import cloud", True),
    ("import importlib; importlib.import_module('requests')", True),
    ("__import__('ssl')", True),
    ("from urllib.parse import urlsplit", False),
    ("import urllib.parse", False),
    ("from .socket_helpers import x", False),
    ("import sockets_are_not_socket", False),
])
def test_the_import_scan_catches_every_spelling(source, caught):
    assert any(_banned(n) for n in _imported_names(ast.parse(source))) is caught


# --- 2. every worker command line is offline --------------------------------


def _assert_offline(argv):
    assert argv[0] == UV
    assert argv[1] == "run"
    for flag in OFFLINE_FLAGS:
        assert flag in argv, (flag, argv)


class _FakeProc:
    def __init__(self):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO("")

    def terminate(self):
        pass


@pytest.fixture
def launches(monkeypatch):
    """Every process start, by any engine, recorded and never run."""
    calls = []

    def run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        if "-f" in argv:  # piper writes its WAV
            Path(argv[argv.index("-f") + 1]).write_bytes(b"RIFFfake")
        return subprocess.CompletedProcess(argv, 0, '{"ok": true, "text": "synthetic"}', "")

    def popen(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return _FakeProc()

    for name in ("VOCALIZE_LLM_PYTHON", "VOCALIZE_WHISPER_PYTHON"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(kokoro, "SESSION_SEAM", popen)
    monkeypatch.setattr(piper, "RUN_SEAM", run)
    monkeypatch.setattr(llm, "LOCAL_RUN_SEAM", run)
    monkeypatch.setattr(local_module, "uv_path", lambda: UV)
    monkeypatch.setattr(kokoro, "uv_path", lambda: UV)
    monkeypatch.setattr(piper, "uv_path", lambda: UV)
    monkeypatch.setattr(install, "installed", lambda *a, **kw: (True, ""))
    monkeypatch.setattr(warm, "request", lambda *a, **kw: None)
    monkeypatch.setattr(dictate, "_wav_duration", lambda path: 1.0)
    monkeypatch.setattr(dictate, "_refresh_claim", lambda path: None)
    return calls


def _kokoro(calls):
    kokoro.close()
    try:
        kokoro._session_for(kokoro_manifest.DEFAULT_VOICE, 1.0, kokoro_manifest.DEFAULT_LANGUAGE)
    finally:
        kokoro.close()
    return calls[-1]


def _piper(calls):
    piper.synthesize("synthetic", Settings(voice_id="lessac", speed=1.0))
    return calls[-1]


def _parakeet(calls, monkeypatch, tmp_path):
    monkeypatch.setattr(parakeet_manifest, "supported", lambda: True)
    dictate.transcribe(tmp_path / "take.wav", config.resolve_stt({"stt": {"engine": "parakeet"}}))
    return calls[-1]


def _whisper(calls, tmp_path):
    dictate.transcribe(tmp_path / "take.wav", config.resolve_stt({"stt": {"engine": "whisper"}}))
    return calls[-1]


def _llm(calls):
    assert llm._local("system", "synthetic", 1, 10) is not None
    return calls[-1]


@pytest.fixture
def cold_launches(launches, monkeypatch, tmp_path):
    return {
        "kokoro": _kokoro(launches),
        "piper": _piper(launches),
        "parakeet": _parakeet(launches, monkeypatch, tmp_path),
        "whisper": _whisper(launches, tmp_path),
        "llm": _llm(launches),
    }


@pytest.fixture
def warm_specs(launches, tmp_path):
    whisper_manifest.MODEL_DIR.mkdir(parents=True)
    stt = config.resolve_stt({"stt": {"engine": "whisper", "cleanup": "local"}})
    whisper_manifest.model_path(stt["model"]).write_bytes(b"synthetic")
    specs = {kind: (argv, env) for kind, argv, env, _ in dictate._warm_specs(stt, base=tmp_path / "w")}
    assert set(specs) == {"whisper", "llm"}
    return specs


@pytest.mark.parametrize("engine", ["kokoro", "piper", "parakeet", "whisper", "llm"])
def test_every_cold_worker_command_line_is_offline(cold_launches, engine):
    argv, _ = cold_launches[engine]
    _assert_offline(argv)


@pytest.mark.parametrize("kind", ["whisper", "llm"])
def test_every_warm_worker_command_line_is_offline(warm_specs, kind):
    argv, _ = warm_specs[kind]
    _assert_offline(argv)
    assert "--serve" in argv


def test_warm_spawn_defaults_to_the_allowlisted_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "CREDENTIAL_CANARY")
    # A Unix socket path must stay under ~100 bytes; tmp_path may not.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(warm.protocol, "socket_path", lambda kind, base=None: Path("w") / f"{kind}.sock")
    spawned = []
    warm.ensure_warm("whisper", [UV, "run", *OFFLINE_FLAGS, "worker.py"], "fp", None,
                     spawn=lambda argv, **kw: spawned.append(kw))
    assert spawned and spawned[0]["env"] == runtime.environment()


@pytest.mark.parametrize("manifest", [
    kokoro_manifest, piper_manifest, parakeet_manifest, whisper_manifest, llm_manifest,
])
def test_only_the_install_selftest_may_fetch(manifest):
    """`vocalize local install` is the one step a user asks to download:
    its selftest populates uv's cache, so it alone carries no offline flags."""
    argv = manifest.selftest_argv()
    assert argv[0] == "run"
    assert not set(OFFLINE_FLAGS) & set(argv)


# --- 3. the worker environment is an allowlist ------------------------------

CANARIES = ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "GITHUB_TOKEN", "HF_TOKEN",
            "AWS_SECRET", "CLIENT_SECRET", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")


def _assert_allowlisted(env):
    assert env is not None, "a worker must never inherit the caller's environment"
    assert env["HF_HUB_OFFLINE"] == env["TRANSFORMERS_OFFLINE"] == "1"
    leaked = [name for name in env
              if name.endswith(("_KEY", "_TOKEN", "_SECRET")) or name == "PYTHONPATH"]
    assert leaked == []
    assert "CREDENTIAL_CANARY" not in env.values()


@pytest.fixture
def canaries(monkeypatch):
    for name in CANARIES:
        monkeypatch.setenv(name, "CREDENTIAL_CANARY")


@pytest.mark.parametrize("engine", ["kokoro", "piper", "parakeet", "whisper", "llm"])
def test_every_cold_worker_gets_the_allowlisted_environment(canaries, cold_launches, engine):
    _, kwargs = cold_launches[engine]
    _assert_allowlisted(kwargs.get("env"))
    assert kwargs["env"] == runtime.environment()


@pytest.mark.parametrize("kind", ["whisper", "llm"])
def test_every_warm_worker_gets_the_allowlisted_environment(canaries, warm_specs, kind):
    _, env = warm_specs[kind]
    _assert_allowlisted(env)


def test_the_environment_also_tells_uv_itself_to_stay_offline(canaries):
    env = runtime.environment()
    _assert_allowlisted(env)
    assert env["UV_OFFLINE"] == "1"
    assert env["UV_PYTHON_DOWNLOADS"] == "never"


# --- 4. an uncached runtime says to install again, and never goes online ----

UV_OFFLINE_STDERR = (
    "  × No solution found when resolving `--with` dependencies:\n"
    "  ╰─▶ Because kokoro-onnx was not found in the cache and you require kokoro-onnx==0.6.1,\n"
    "      we can conclude that your requirements are unsatisfiable.\n\n"
    "      hint: Packages were unavailable because the network was disabled.\n"
)
UV_NO_PYTHON_STDERR = (
    "error: No interpreter found for Python 3.12 in managed installations or search path\n"
)


@pytest.mark.parametrize("stderr", [UV_OFFLINE_STDERR, UV_NO_PYTHON_STDERR])
def test_uv_offline_refusals_are_recognised(stderr):
    assert runtime.offline_failure(stderr)


@pytest.mark.parametrize("stderr", ["", None, "Traceback: worker crashed"])
def test_other_failures_are_not_mistaken_for_offline(stderr):
    assert not runtime.offline_failure(stderr)


def _failing(monkeypatch, stderr, calls):
    def run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 2, "", stderr)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(piper, "RUN_SEAM", run)


@pytest.mark.parametrize("stderr", [UV_OFFLINE_STDERR, UV_NO_PYTHON_STDERR])
def test_piper_offline_failure_says_install_again_without_retrying(launches, monkeypatch, stderr):
    calls = []
    _failing(monkeypatch, stderr, calls)
    with pytest.raises(Exception) as caught:
        piper.synthesize("synthetic", Settings(voice_id="lessac", speed=1.0))
    assert "vocalize local install --piper` again" in str(caught.value)
    assert "kokoro-onnx" not in str(caught.value)  # diagnostics are not relayed
    assert len(calls) == 1


@pytest.mark.parametrize("engine,hint", [
    ("whisper", "vocalize local install --stt` again"),
    ("parakeet", "vocalize local install --stt --engine parakeet` again"),
])
def test_stt_offline_failure_says_install_again_without_retrying(
    launches, monkeypatch, tmp_path, engine, hint,
):
    calls = []
    _failing(monkeypatch, UV_OFFLINE_STDERR, calls)
    monkeypatch.setattr(parakeet_manifest, "supported", lambda: True)
    with pytest.raises(dictate.DictationError, match="could not start offline") as caught:
        dictate.transcribe(tmp_path / "take.wav", config.resolve_stt({"stt": {"engine": engine}}))
    assert hint in str(caught.value)
    assert len(calls) == 1
    _assert_offline(calls[0])


def test_kokoro_worker_that_exits_says_install_again(launches):
    kokoro.close()
    try:
        session = kokoro._session_for(kokoro_manifest.DEFAULT_VOICE, 1.0,
                                      kokoro_manifest.DEFAULT_LANGUAGE)
        with pytest.raises(Exception, match="run `vocalize local install` again"):
            session.request({"text": "synthetic", "out": "/nonexistent.wav"})
    finally:
        kokoro.close()
    assert len(launches) == 1


# --- 5. the runtime dependencies are click and tomli only -------------------


def test_runtime_dependencies_are_exactly_click_and_conditional_tomli():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    deps = metadata["project"]["dependencies"]
    names = sorted(dep.split(">")[0].split("=")[0].split(";")[0].strip().lower() for dep in deps)
    assert names == ["click", "tomli"]
    (tomli,) = [dep for dep in deps if dep.lower().startswith("tomli")]
    assert "python_version < '3.11'" in tomli.replace('"', "'")


# --- 6. the suite itself cannot reach the network ---------------------------


def test_the_suite_refuses_a_non_loopback_connection():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as conn, \
            pytest.raises(pytest.fail.Exception, match="non-loopback"):
        conn.connect(("192.0.2.1", 443))


def test_the_suite_refuses_a_non_loopback_connect_ex():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as conn, \
            pytest.raises(pytest.fail.Exception, match="non-loopback"):
        conn.connect_ex(("example.invalid", 80))


@pytest.mark.parametrize("family,host", [(socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")])
def test_the_suite_allows_loopback(family, host):
    try:
        server = socket.socket(family, socket.SOCK_STREAM)
    except OSError:
        pytest.skip("address family unavailable")
    with server:
        try:
            server.bind((host, 0))
        except OSError:
            pytest.skip("loopback address unavailable")
        server.listen(1)
        port = server.getsockname()[1]
        with socket.create_connection(("localhost" if family == socket.AF_INET else host, port), timeout=2):
            pass


def test_the_suite_allows_unix_sockets(tmp_path):
    # Reaching the OS's own error (no such socket) proves the guard let it by.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn, pytest.raises(OSError):
        conn.connect(str(tmp_path / "missing.sock"))
