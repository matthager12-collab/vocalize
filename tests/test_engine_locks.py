"""Every reviewed engine runs from a lock of its whole tree, by hash.

The supply-chain reviews of kokoro-onnx, pywhispercpp, mlx-lm and
parakeet-mlx all set one condition: the engine's whole dependency tree is
locked by exact version and sha256, and nothing is ever built from source.
These tests hold the shipped locks to that, and hold every command line
that installs or starts an engine to the lock.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from vocalize import dictate
from vocalize.local import install as install_module
from vocalize.local import (
    kokoro_manifest,
    llm_manifest,
    locks,
    parakeet_manifest,
    warm_protocol,
    whisper_manifest,
)

MANIFESTS = (kokoro_manifest, whisper_manifest, llm_manifest, parakeet_manifest)
REVIEWED = {"kokoro-onnx", "pywhispercpp", "mlx-lm", "parakeet-mlx"}
# Every lock a manifest can select, with the manifest that selects it.
LOCKS = [
    (kokoro_manifest, kokoro_manifest.LOCK_FILE),
    (kokoro_manifest, kokoro_manifest.LOCK_FILE_X86_64),
    (whisper_manifest, whisper_manifest.LOCK_FILE),
    (llm_manifest, llm_manifest.LOCK_FILE),
    (parakeet_manifest, parakeet_manifest.LOCK_FILE),
]
_REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\;]+)")


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock(path: Path) -> dict[str, dict]:
    """{package: {"version", "hashes", "line"}} from a `--generate-hashes`
    lock. Any requirement line that is not `name==version` fails."""
    packages: dict[str, dict] = {}
    current = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if raw[:1].isspace():
            assert current is not None, f"{path.name}: continuation before a package: {raw!r}"
            for digest in re.findall(r"--hash=sha256:([0-9a-f]+)", line):
                assert len(digest) == 64, f"{path.name}: short hash {digest!r}"
                current["hashes"].append(digest)
            continue
        match = _REQUIREMENT.match(line)
        assert match, f"{path.name}: not pinned with ==: {raw!r}"
        name = _normalise(match.group(1))
        assert name not in packages, f"{path.name}: {name} pinned twice"
        current = packages[name] = {"version": match.group(2), "hashes": [], "line": raw}
    return packages


def _top_level(manifest) -> tuple[str, str]:
    name, version = manifest.RUNTIME_PACKAGE.split("==")
    return _normalise(name), version


# --- the lock files ----------------------------------------------------


@pytest.mark.parametrize(("manifest", "lock"), LOCKS, ids=lambda v: getattr(v, "name", ""))
def test_every_lock_exists_and_is_not_empty(manifest, lock):
    assert lock.parent == locks.LOCKS_DIR
    assert lock.is_file()
    assert lock.stat().st_size > 0
    assert parse_lock(lock)


@pytest.mark.parametrize(("manifest", "lock"), LOCKS, ids=lambda v: getattr(v, "name", ""))
def test_every_package_is_pinned_exactly_and_hashed(manifest, lock):
    for name, entry in parse_lock(lock).items():
        assert entry["hashes"], f"{lock.name}: {name} has no --hash=sha256:"


@pytest.mark.parametrize(("manifest", "lock"), LOCKS, ids=lambda v: getattr(v, "name", ""))
def test_every_lock_holds_its_engine_at_the_manifests_version(manifest, lock):
    name, version = _top_level(manifest)
    packages = parse_lock(lock)
    assert name in packages, f"{lock.name} does not lock {name}"
    assert packages[name]["version"] == version


@pytest.mark.parametrize(("manifest", "lock"), LOCKS, ids=lambda v: getattr(v, "name", ""))
def test_no_lock_names_a_source_url_or_an_editable(manifest, lock):
    text = lock.read_text(encoding="utf-8")
    for marker in (" @ ", "-e ", "--editable", "git+", "file:", "--no-binary"):
        assert marker not in text, (lock.name, marker)


def test_every_reviewed_engine_has_a_lock():
    assert {_top_level(m)[0] for m in MANIFESTS} == REVIEWED


def test_kokoro_picks_the_lock_for_the_machines_architecture(monkeypatch):
    monkeypatch.setattr(kokoro_manifest.platform, "machine", lambda: "x86_64")
    assert kokoro_manifest.lock_file() == kokoro_manifest.LOCK_FILE_X86_64
    monkeypatch.setattr(kokoro_manifest.platform, "machine", lambda: "arm64")
    assert kokoro_manifest.lock_file() == kokoro_manifest.LOCK_FILE


# --- every command line names the lock, never a bare --with -------------


def _assert_no_bare_with(argv):
    for i, arg in enumerate(argv):
        assert arg != "--with-requirements", argv
        if arg == "--with" or arg.startswith("--with="):
            value = arg.split("=", 1)[1] if "=" in arg else argv[i + 1]
            assert not any(pkg in value for pkg in REVIEWED), argv
    for arg in argv:
        assert not any(arg.startswith(pkg + "==") for pkg in REVIEWED), argv


def _assert_runs_the_locked_env(manifest, argv):
    assert argv[argv.index("--python") + 1] == str(
        locks.env_python(manifest.ENGINE, manifest.lock_file())
    )
    assert "--offline" in argv
    _assert_no_bare_with(argv)


def _selftest(manifest, tmp_path):
    if manifest is whisper_manifest:
        return manifest.selftest_argv(tmp_path, model=whisper_manifest.DEFAULT_MODEL)
    return manifest.selftest_argv(tmp_path)


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda m: m.ENGINE)
def test_the_install_commands_reference_the_lock(manifest, tmp_path, monkeypatch):
    monkeypatch.setattr(locks, "ENVS_DIR", tmp_path / "envs")
    lock = manifest.lock_file()

    venv = locks.venv_argv(manifest.ENGINE, lock, manifest.PYTHON_VERSION)
    sync = locks.sync_argv(manifest.ENGINE, lock)

    assert venv[0] == "venv"
    assert venv[venv.index("--python") + 1] == manifest.PYTHON_VERSION
    assert sync[:2] == ["pip", "sync"]
    assert "--require-hashes" in sync
    assert sync[sync.index("--only-binary") + 1] == ":all:"
    assert sync[-1] == str(lock)
    for argv in (venv, sync):
        _assert_no_bare_with(argv)
    _assert_runs_the_locked_env(manifest, _selftest(manifest, tmp_path))


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda m: m.ENGINE)
def test_the_runtime_command_references_the_lock(manifest, tmp_path):
    if manifest is kokoro_manifest:
        from vocalize.providers import kokoro

        argv = kokoro._argv("af_heart", 1.0, "en-us", "/uv")[1:]
    elif manifest is whisper_manifest:
        argv = dictate.worker_argv("/uv", tmp_path / "t.wav", {
            "model": "small.en", "language": "en", "beam_size": 5,
        })[1:]
    else:
        argv = manifest.runtime_argv(tmp_path)
    assert argv[0] == "run"
    _assert_runs_the_locked_env(manifest, argv)


def test_the_warm_whisper_spawn_references_the_lock(monkeypatch, tmp_path):
    model = tmp_path / "ggml-small.en.bin"
    model.write_bytes(b"x")
    monkeypatch.setattr(whisper_manifest, "model_path", lambda name: model)
    monkeypatch.setattr(dictate, "_uv_or_raise", lambda: "/uv")
    monkeypatch.delenv("VOCALIZE_WHISPER_PYTHON", raising=False)

    specs = {kind: rest for kind, *rest in dictate._warm_specs(
        {"model": "small.en", "language": "en", "beam_size": 5,
         "warm_minutes": 5, "max_take_seconds": 60, "engine": "whisper"},
        base=tmp_path,
    )}

    argv, env, fingerprint = specs["whisper"]
    _assert_runs_the_locked_env(whisper_manifest, argv[1:])
    identity = locks.identity(whisper_manifest.lock_file())
    assert fingerprint["runtime_identity"] == identity
    assert env["_VOCALIZE_RUNTIME_IDENTITY"] == identity


# --- a changed lock invalidates what was installed from the old one -------


def _copy_lock(tmp_path, monkeypatch, manifest) -> Path:
    lock = tmp_path / manifest.lock_file().name
    lock.write_bytes(manifest.lock_file().read_bytes())
    monkeypatch.setattr(manifest, "LOCK_FILE", lock)
    if manifest is kokoro_manifest:
        monkeypatch.setattr(manifest, "LOCK_FILE_X86_64", lock)
    return lock


def test_a_changed_lock_changes_the_warm_fingerprint(tmp_path, monkeypatch):
    lock = _copy_lock(tmp_path, monkeypatch, whisper_manifest)
    model = tmp_path / "ggml-small.en.bin"

    def fingerprint():
        return warm_protocol.fingerprint(
            whisper_manifest.worker_path(), whisper_manifest.RUNTIME_PACKAGE, model,
            runtime_identity=dictate._whisper_identity(whisper_manifest, None),
        )

    before = fingerprint()
    lock.write_text(lock.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    assert fingerprint() != before


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda m: m.ENGINE)
def test_a_changed_lock_names_a_new_environment(manifest, tmp_path, monkeypatch):
    monkeypatch.setattr(locks, "ENVS_DIR", tmp_path / "envs")
    lock = _copy_lock(tmp_path, monkeypatch, manifest)
    python = locks.env_python(manifest.ENGINE, lock)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    locks.write_marker(manifest.ENGINE, lock)
    assert install_module.runtime_installed(manifest)
    old_python = manifest.runtime_python()

    lock.write_text(lock.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    assert manifest.runtime_python() != old_python
    assert not install_module.runtime_installed(manifest)


def test_a_tampered_marker_is_not_trusted(tmp_path, monkeypatch):
    monkeypatch.setattr(locks, "ENVS_DIR", tmp_path / "envs")
    lock = whisper_manifest.lock_file()
    python = locks.env_python("whisper", lock)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    locks.marker_path("whisper", lock).write_text(
        '{"marker_version": 1, "engine": "whisper", "lock_sha256": "0"}', encoding="utf-8",
    )

    assert not install_module.runtime_installed(whisper_manifest)
