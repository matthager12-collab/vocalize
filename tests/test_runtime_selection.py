"""Explicit runtime tests use only fakes and harmless stdlib children."""

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from vocalize import config, dictate, llm, local
from vocalize.exceptions import DictationError
from vocalize.local import (
    install,
    llm_manifest,
    runtime,
    runtime_worker,
    warm,
    warm_protocol,
    whisper_manifest,
)


@pytest.fixture
def selected(monkeypatch, tmp_path):
    path = tmp_path / "runtime $(literal); --flag" / "bin" / "python"
    path.parent.mkdir(parents=True)
    path.symlink_to(sys.executable)
    monkeypatch.setenv("VOCALIZE_LLM_PYTHON", str(path))
    monkeypatch.setenv("VOCALIZE_WHISPER_PYTHON", str(path))
    return path


def test_absent_selector_preserves_legacy_command():
    assert runtime.selection(llm_manifest.RUNTIME_PACKAGE) is None
    argv = ["/fake/uv", *llm_manifest.runtime_argv()]
    assert runtime.command(llm_manifest, argv, None) == argv


@pytest.mark.parametrize("value", ["", "relative/python", "../python", "--python", "/missing/python", "/tmp/null\x00python"])
def test_invalid_explicit_selection_is_an_error(monkeypatch, value):
    if "\x00" in value:
        # os.environ itself rejects NUL; use a synthetic mapping to test validation.
        monkeypatch.setattr(runtime.os, "environ", {"VOCALIZE_LLM_PYTHON": value})
    else:
        monkeypatch.setenv("VOCALIZE_LLM_PYTHON", value)
    with pytest.raises(runtime.RuntimeSelectionError):
        runtime.selection(llm_manifest.RUNTIME_PACKAGE)


def test_selected_argv_preserves_symlink_and_literal_arguments(selected):
    chosen = runtime.selection(llm_manifest.RUNTIME_PACKAGE)
    argv = runtime.command(llm_manifest, [None, *llm_manifest.runtime_argv()], chosen)
    assert argv[:3] == [str(selected), "-I", "-B"]
    assert Path(argv[3]).name == "runtime_worker.py"
    assert argv[4:] == ["llm", "--model-dir", str(llm_manifest.MODEL_DIR), "--once"]
    assert "uv" not in argv and "--with" not in argv
    assert chosen.python != str(selected.resolve())


def test_same_base_python_in_different_venvs_has_distinct_identity(selected, monkeypatch, tmp_path):
    first = runtime.selection(llm_manifest.RUNTIME_PACKAGE)
    other = tmp_path / "second-python"
    other.symlink_to(sys.executable)
    monkeypatch.setenv("VOCALIZE_LLM_PYTHON", str(other))
    assert runtime.selection(llm_manifest.RUNTIME_PACKAGE).identity != first.identity


def test_scrubbed_environment_excludes_secrets_and_hooks(monkeypatch, selected):
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
                 "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", runtime.IDENTITY_ENV):
        monkeypatch.setenv(name, "SECRET_CANARY")
    env = runtime.environment(runtime.selection(llm_manifest.RUNTIME_PACKAGE))
    result = subprocess.run(
        [sys.executable, "-I", "-c", "import json,os; print(json.dumps(dict(os.environ)))"],
        env=env, capture_output=True, text=True, check=True, timeout=3,
    )
    assert "SECRET_CANARY" not in result.stdout
    observed = json.loads(result.stdout)
    assert observed["HF_HUB_OFFLINE"] == observed["TRANSFORMERS_OFFLINE"] == "1"
    assert runtime.IDENTITY_ENV in observed
    assert runtime.IDENTITY_ENV not in runtime.environment()
    assert "VOCALIZE_LLM_PYTHON" not in observed


@pytest.mark.parametrize("engine,package,pin", [("llm", "mlx-lm", "0.31.3"), ("whisper", "pywhispercpp", "1.5.1")])
def test_bootstrap_checks_pin_before_worker(monkeypatch, engine, package, pin):
    fake_sys = SimpleNamespace(version_info=(3, 12, 0), stderr=io.StringIO(), argv=[])
    monkeypatch.setattr(runtime_worker, "sys", fake_sys)
    events = []
    monkeypatch.setattr(runtime_worker.importlib.metadata, "version", lambda name: events.append(name) or pin)
    monkeypatch.setattr(runtime_worker.runpy, "run_path", lambda path, **kw: events.append((path, kw)))
    assert runtime_worker.main([engine, "--once", "$(literal)"]) == 0
    assert events[0] == package
    assert Path(events[1][0]).name == f"{engine}_worker.py"
    assert fake_sys.argv[1:] == ["--once", "$(literal)"]


@pytest.mark.parametrize("python_version,installed", [((3, 14), "0.31.3"), ((3, 12), "0.30.0"), ((3, 12), None)])
def test_bootstrap_mismatch_never_imports_worker(monkeypatch, python_version, installed):
    fake_sys = SimpleNamespace(version_info=python_version, stderr=io.StringIO(), argv=[])
    monkeypatch.setattr(runtime_worker, "sys", fake_sys)

    def version(_name):
        if installed is None:
            raise runtime_worker.importlib.metadata.PackageNotFoundError
        return installed

    monkeypatch.setattr(runtime_worker.importlib.metadata, "version", version)
    monkeypatch.setattr(runtime_worker.runpy, "run_path", lambda *a, **kw: pytest.fail("must not import worker"))
    assert runtime_worker.main(["llm", "--once"]) == 2
    assert "no provisioning attempted" in fake_sys.stderr.getvalue()


@pytest.mark.parametrize("engine", ["../llm", "/tmp/llm", "%2e%2e%2fllm", "--llm"])
def test_bootstrap_engine_cannot_select_another_file(monkeypatch, engine):
    monkeypatch.setattr(runtime_worker.runpy, "run_path", lambda *a, **kw: pytest.fail("must not import worker"))
    assert runtime_worker.main([engine]) == 2


def test_invalid_explicit_llm_does_not_contact_warm_or_uv(monkeypatch):
    monkeypatch.setenv("VOCALIZE_LLM_PYTHON", "/missing/python")
    monkeypatch.setattr(local, "uv_path", lambda: pytest.fail("must not fall back to uv"))
    monkeypatch.setattr(warm, "request", lambda *a, **kw: pytest.fail("must not reuse warm runtime"))
    assert llm._local("system", "synthetic", 1, 10, warm_ok=True) is None


def test_invalid_explicit_whisper_does_not_contact_warm_or_uv(monkeypatch):
    monkeypatch.setenv("VOCALIZE_WHISPER_PYTHON", "/missing/python")
    monkeypatch.setattr(dictate, "_uv_or_raise", lambda: pytest.fail("must not fall back to uv"))
    monkeypatch.setattr(warm, "request", lambda *a, **kw: pytest.fail("must not reuse warm runtime"))
    with pytest.raises(DictationError, match="VOCALIZE_WHISPER_PYTHON"):
        dictate.transcribe(Path("/synthetic.wav"), config.resolve_stt({"stt": {"engine": "whisper"}}))


def test_cold_and_warm_identity_and_env_match(selected, monkeypatch, tmp_path):
    monkeypatch.setattr(install, "installed", lambda *a, **kw: (True, ""))
    monkeypatch.setattr(local, "uv_path", lambda: pytest.fail("selected runtime needs no uv"))
    monkeypatch.setattr(dictate, "_uv_or_raise", lambda: pytest.fail("selected runtime needs no uv"))
    whisper_manifest.MODEL_DIR.mkdir()
    whisper_manifest.model_path(whisper_manifest.DEFAULT_MODEL).write_bytes(b"synthetic")
    llm_manifest.MODEL_DIR.mkdir()
    (llm_manifest.MODEL_DIR / "model.safetensors").write_bytes(b"synthetic")
    stt = config.resolve_stt({"stt": {"engine": "whisper", "cleanup": "local"}})
    fingerprints, calls = {}, []

    def request(kind, payload, fingerprint, **kwargs):
        fingerprints[kind] = fingerprint

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, '{"ok":true,"text":"synthetic"}', "")

    monkeypatch.setattr(warm, "request", request)
    monkeypatch.setattr(llm, "LOCAL_RUN_SEAM", run)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(dictate, "_wav_duration", lambda path: 1)
    monkeypatch.setattr(dictate, "_refresh_claim", lambda path: None)
    assert llm._local("system", "synthetic", 1, 10, warm_ok=True) == "synthetic"
    assert dictate.transcribe(tmp_path / "synthetic.wav", stt) == "synthetic"
    specs = dictate._warm_specs(stt, base=tmp_path / "warm")
    assert {kind for kind, *_ in specs} == {"llm", "whisper"}
    for kind, argv, env, fingerprint in specs:
        assert fingerprint == fingerprints[kind]
        cold_argv, cold_kwargs = next((a, kw) for a, kw in calls if a[4] == kind)
        assert cold_argv[:5] == argv[:5]
        assert cold_kwargs["env"] == env
        assert fingerprint["runtime_identity"] == env[runtime.IDENTITY_ENV]
        manifest = llm_manifest if kind == "llm" else whisper_manifest
        model = manifest.MODEL_DIR / "model.safetensors" if kind == "llm" else manifest.model_path(stt["model"])
        assert fingerprint == warm_protocol.fingerprint(
            manifest.worker_path(), manifest.RUNTIME_PACKAGE, model,
            runtime_identity=env[runtime.IDENTITY_ENV],
        )


@pytest.mark.parametrize("engine", ["llm", "whisper"])
def test_worker_fingerprint_uses_bootstrap_identity_without_loading_model(engine, monkeypatch, tmp_path):
    manifest = llm_manifest if engine == "llm" else whisper_manifest
    spec = importlib.util.spec_from_file_location("runtime_fingerprint_worker", manifest.worker_path())
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    monkeypatch.setenv(runtime.IDENTITY_ENV, "validated-runtime-identity")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    monkeypatch.setattr(worker, "_warm_protocol", lambda: SimpleNamespace(
        Server=lambda *args, **kwargs: kwargs, fingerprint=warm_protocol.fingerprint,
    ))
    args = worker.parse_args(["--serve", "--socket", str(tmp_path / "test.sock"),
                              "--model-dir" if engine == "llm" else "--model", str(tmp_path / "model")])
    server = worker._serve_server(args)
    assert server["fingerprint"]["runtime_identity"] == "validated-runtime-identity"


@pytest.mark.parametrize("is_eval", [False, True])
def test_root_fixture_isolates_inherited_runtime_selection(monkeypatch, tmp_path, is_eval):
    import os

    from conftest import _no_real_model_cache

    names = ("VOCALIZE_LLM_PYTHON", "VOCALIZE_WHISPER_PYTHON", runtime.IDENTITY_ENV)
    for name in names:
        monkeypatch.setenv(name, "REAL_RUNTIME_CANARY")
    request = SimpleNamespace(node=SimpleNamespace(get_closest_marker=lambda name: object() if is_eval else None))
    _no_real_model_cache.__wrapped__(monkeypatch, tmp_path, request)
    assert all((name in os.environ) == is_eval for name in names)
