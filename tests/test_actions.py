"""The actions registry (T-25): every configuration choice that is a command.

Nothing here installs, removes, builds, opens or plays anything on the
machine running the suite. Every action runs against a scratch HOME with the
functions it calls faked, and `subprocess.run` refuses to start a process.
"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import install_hook
import pytest

from vocalize import actions, app, cache, integrate, settings_registry, tts
from vocalize import local as local_module
from vocalize.local import (
    install,
    kokoro_manifest,
    llm_manifest,
    locks,
    parakeet_manifest,
    piper_manifest,
    whisper_manifest,
)

EXPECTED_IDS = {
    "install_kokoro", "uninstall_kokoro", "install_piper", "uninstall_piper",
    "install_parakeet", "uninstall_parakeet", "install_whisper", "uninstall_whisper",
    "install_cleanup", "uninstall_cleanup", "app_install", "app_restart", "app_uninstall",
    "integrate_claude", "remove_integrations", "hook_install", "hook_remove",
    "clear_audio_cache", "preview_voice", "apply_profile", "open_microphone_settings",
    "open_accessibility_settings", "stop_warm",
}
UV = "/synthetic/uv"
HEX = "a" * 64
REAL_HOOK_MODULE = actions._hook_module


def _no_process(*args, **kwargs):
    raise AssertionError(f"a test tried to start a process: {args[:1]}")


@pytest.fixture(autouse=True)
def scratch(monkeypatch, tmp_path):
    """Every path the actions touch, under tmp_path; no process can start."""
    monkeypatch.setattr(subprocess, "run", _no_process)
    monkeypatch.setattr(subprocess, "Popen", _no_process)
    models = tmp_path / "cache" / "models"
    for manifest, name in ((kokoro_manifest, "kokoro"), (piper_manifest, "piper"),
                           (parakeet_manifest, "parakeet"), (whisper_manifest, "whisper"),
                           (llm_manifest, "qwen")):
        monkeypatch.setattr(manifest, "MODEL_DIR", models / name)
    monkeypatch.setattr(locks, "ENVS_DIR", tmp_path / "cache" / "envs")
    monkeypatch.setattr(install, "BIN_DIR", tmp_path / "cache" / "bin")
    monkeypatch.setattr(tts, "DEFAULT_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(integrate, "SERVICES_DIR", tmp_path / "Services")
    monkeypatch.setattr(integrate, "SKILL_DEST", tmp_path / "claude" / "skills" / "speak" / "SKILL.md")
    monkeypatch.setattr(app, "APP_DIR", tmp_path / "AppSupport")
    monkeypatch.setattr(app, "LAUNCH_AGENTS_DIR", tmp_path / "LaunchAgents")
    monkeypatch.setattr(app, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(app, "_run", lambda argv, timeout=30: None)
    monkeypatch.setattr(install_hook, "SETTINGS_PATH", tmp_path / "claude" / "settings.json")
    monkeypatch.setattr(actions, "_hook_module", lambda: install_hook)
    monkeypatch.setattr(local_module, "uv_path", lambda: UV)
    monkeypatch.setattr(local_module, "physical_ram_bytes", lambda: 64 * 1024**3)
    monkeypatch.setattr(parakeet_manifest, "supported", lambda: True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path


class Calls:
    def __init__(self):
        self.log = []

    def record(self, name, result=None):
        def fake(*args, **kwargs):
            self.log.append((name, args, kwargs))
            return result
        return fake

    def names(self):
        return [name for name, _a, _k in self.log]

    def of(self, name):
        return [(a, k) for n, a, k in self.log if n == name]


@pytest.fixture
def fake_install(monkeypatch):
    calls = Calls()
    monkeypatch.setattr(install, "installed", lambda *a, **k: (False, "not installed"))
    monkeypatch.setattr(install, "file_is_verified", lambda *a, **k: False)
    monkeypatch.setattr(install, "download_file", calls.record("download_file"))
    monkeypatch.setattr(install, "write_stamp", calls.record("write_stamp"))
    monkeypatch.setattr(install, "selftest", calls.record("selftest", ""))
    monkeypatch.setattr(install, "build_recorder", calls.record("build_recorder", ("built", None)))
    return calls


def _write(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _env(engine):
    path = locks.envs_dir() / f"{engine}-{HEX}"
    _write(path / "bin" / "python")
    return path


# --- the schema --------------------------------------------------------


def test_schema_is_json_and_every_action_is_complete():
    listed = json.loads(json.dumps(actions.schema()))
    groups = {group.id for group in settings_registry.GROUPS}
    assert {a["id"] for a in listed} == EXPECTED_IDS
    assert len(listed) == len(actions.ACTIONS)
    for action in listed:
        assert set(action) == {"id", "label", "help", "group", "kind", "confirm", "danger",
                               "params"}
        assert action["label"].strip() and action["help"].strip()
        assert action["group"] in groups
        assert action["kind"] in ("job", "instant")
        for param in action["params"]:
            assert set(param) == {"name", "label", "type", "choices", "choices_from", "required"}
            assert param["type"] in ("enum", "str")
            if param["type"] == "enum":
                assert param["choices"], param
    profile = next(a for a in listed if a["id"] == "apply_profile")["params"][0]
    assert profile["choices"] == ["small", "standard", "large", "studio"]


def test_downloads_are_jobs_and_licences_are_shown_before_confirming():
    by_id = {action.id: action for action in actions.ACTIONS}
    for action_id in EXPECTED_IDS:
        if action_id.startswith(("install_", "uninstall_")):
            assert by_id[action_id].kind == "job"
            assert by_id[action_id].confirm
    for voice, licence in piper_manifest.VOICE_LICENCES.items():
        assert f"{voice}: {licence}" in by_id["install_piper"].help
    assert "GPL-3.0-or-later" in by_id["install_piper"].help
    assert "CC-BY-4.0" in by_id["install_parakeet"].help
    for action_id in ("uninstall_kokoro", "uninstall_whisper", "app_uninstall", "hook_remove",
                      "remove_integrations", "clear_audio_cache"):
        assert by_id[action_id].danger and by_id[action_id].confirm


def test_params_follow_the_manifests():
    by_id = {action.id: action for action in actions.ACTIONS}
    assert by_id["install_piper"].params[0].choices == piper_manifest.VOICES
    assert by_id["install_whisper"].params[0].choices == whisper_manifest.MODELS
    assert by_id["uninstall_whisper"].params[0].choices == whisper_manifest.MODELS
    assert [p.name for p in by_id["preview_voice"].params] == ["engine", "voice"]


# --- run's validation -------------------------------------------------


@pytest.mark.parametrize("action_id, params, words", [
    ("no_such_action", {}, "no such action"),
    (None, {}, "no such action"),
    ("install_whisper", {}, "choose a model"),
    ("install_whisper", {"model": "../etc/passwd"}, "not one of the choices"),
    ("install_piper", {"voice": "--serve"}, "not one of the choices"),
    ("preview_voice", {"engine": "say"}, "choose a voice"),
    ("preview_voice", {"engine": "elevenlabs", "voice": "x"}, "not one of the choices"),
    ("apply_profile", {"profile": "huge"}, "not one of the choices"),
    ("install_kokoro", {"model": "small.en"}, "does not take"),
    ("install_whisper", {"model": 3}, "must be text"),
    ("install_kokoro", ["not", "a", "dict"], "parameters must be"),
])
def test_run_rejects_bad_requests_before_doing_anything(fake_install, action_id, params, words):
    with pytest.raises(actions.ActionError) as excinfo:
        actions.run(action_id, params, lambda *_: None)
    assert words in str(excinfo.value).lower()
    assert fake_install.log == []


def test_an_unexpected_failure_is_one_plain_sentence(monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("secret-looking text sk-123")
    monkeypatch.setattr(install, "installed", broken)
    with pytest.raises(actions.ActionError) as excinfo:
        actions.run("install_kokoro", {}, None)
    assert "sk-123" not in str(excinfo.value)
    assert "Install Kokoro failed" in str(excinfo.value)


# --- installs ---------------------------------------------------------


def test_install_kokoro_downloads_verifies_and_warms(fake_install):
    seen = []
    message = actions.run("install_kokoro", {}, lambda f, m: seen.append((f, m)))
    downloads = fake_install.of("download_file")
    assert [a[1] for a, _k in downloads] == [kokoro_manifest.MODEL_DIR / e["name"]
                                             for e in kokoro_manifest.FILES]
    assert all(k["opener"] is install._default_opener for _a, k in downloads)
    assert fake_install.of("write_stamp") == [((), {})]
    assert fake_install.of("selftest") == [((UV,), {})]
    assert "installed" in message
    fractions = [f for f, _m in seen if f is not None]
    assert fractions and fractions[-1] == 1.0 and all(0 <= f <= 1 for f in fractions)


def test_install_kokoro_when_already_installed_downloads_nothing(fake_install, monkeypatch):
    monkeypatch.setattr(install, "installed", lambda *a, **k: (True, ""))
    assert "already installed" in actions.run("install_kokoro", {}, None)
    assert fake_install.log == []


def test_install_without_uv_names_the_fix(fake_install, monkeypatch):
    monkeypatch.setattr(local_module, "uv_path", lambda: None)
    with pytest.raises(actions.ActionError, match="uv is not installed"):
        actions.run("install_kokoro", {}, None)
    assert fake_install.log == []


def test_install_error_becomes_action_error(fake_install, monkeypatch):
    def fail(*_a, **_k):
        raise install.InstallError("voices-v1.0.bin failed its checksum")
    monkeypatch.setattr(install, "download_file", fail)
    with pytest.raises(actions.ActionError, match="failed its checksum"):
        actions.run("install_kokoro", {}, None)


def test_install_piper_fetches_only_that_voice(fake_install):
    actions.run("install_piper", {"voice": "ryan"}, None)
    entries = piper_manifest.voice_files("ryan")
    assert [a[0] for a, _k in fake_install.of("download_file")] == [e["url"] for e in entries]
    assert fake_install.of("write_stamp") == [((), {"manifest": piper_manifest, "files": entries})]
    assert fake_install.of("selftest") == [((UV,), {"manifest": piper_manifest})]


def test_install_parakeet_needs_apple_silicon(fake_install, monkeypatch):
    monkeypatch.setattr(parakeet_manifest, "supported", lambda: False)
    with pytest.raises(actions.ActionError, match="Apple Silicon"):
        actions.run("install_parakeet", {}, None)
    assert fake_install.log == []


def test_install_parakeet_downloads_warms_and_builds_the_recorder(fake_install, monkeypatch):
    monkeypatch.setattr(install, "build_recorder", fake_install.record("build_recorder",
                                                                       ("rebuilt", None)))
    message = actions.run("install_parakeet", {}, None)
    assert len(fake_install.of("download_file")) == len(parakeet_manifest.FILES)
    assert fake_install.of("write_stamp") == [((), {"manifest": parakeet_manifest})]
    assert fake_install.of("selftest") == [((UV,), {"manifest": parakeet_manifest})]
    assert fake_install.names()[-1] == "build_recorder"
    assert install.REGRANT_WARNING in message


def test_install_whisper_fetches_only_the_chosen_model(fake_install):
    actions.run("install_whisper", {"model": "base.en"}, None)
    entry = whisper_manifest.file_for("base.en")
    assert [a[0] for a, _k in fake_install.of("download_file")] == [entry["url"]]
    assert fake_install.of("write_stamp") == [((), {"manifest": whisper_manifest,
                                                     "files": [entry]})]
    assert fake_install.of("selftest") == [((UV,), {"manifest": whisper_manifest,
                                                    "model": "base.en"})]
    assert "build_recorder" in fake_install.names()


def test_install_cleanup_checks_memory_then_config(fake_install, monkeypatch):
    monkeypatch.setattr(local_module, "physical_ram_bytes", lambda: 8 * 1024**3)
    with pytest.raises(actions.ActionError, match="memory"):
        actions.run("install_cleanup", {}, None)
    assert fake_install.log == []

    monkeypatch.setattr(local_module, "physical_ram_bytes", lambda: 64 * 1024**3)
    monkeypatch.setattr(llm_manifest, "check_config", fake_install.record("check_config"))
    actions.run("install_cleanup", {}, None)
    assert fake_install.of("check_config") == [((llm_manifest.MODEL_DIR,), {})]
    assert fake_install.names().index("check_config") < fake_install.names().index("write_stamp")
    assert fake_install.of("selftest") == [((UV,), {"manifest": llm_manifest})]


# --- uninstalls -------------------------------------------------------


def _stamp(manifest, entries):
    path = manifest.MODEL_DIR / manifest.STAMP_NAME
    files = {e["name"]: {"size": e["size"], "sha256": e["sha256"]} for e in entries}
    _write(path, json.dumps({"manifest_version": manifest.MANIFEST_VERSION,
                             "files": files}).encode())
    return path


def test_uninstall_whisper_removes_only_the_named_model(scratch):
    base, small = whisper_manifest.file_for("base.en"), whisper_manifest.file_for("small.en")
    base_file = _write(whisper_manifest.MODEL_DIR / base["name"])
    small_file = _write(whisper_manifest.MODEL_DIR / small["name"])
    stamp = _stamp(whisper_manifest, [base, small])
    env = _env("whisper")
    recorder = _write(install.BIN_DIR / "Vocalize Recorder.app" / "Contents" / "x")

    assert "removed" in actions.run("uninstall_whisper", {"model": "base.en"}, None)
    assert not base_file.exists()
    assert small_file.exists() and env.exists() and recorder.exists()
    assert set(json.loads(stamp.read_text())["files"]) == {small["name"]}

    actions.run("uninstall_whisper", {"model": "small.en"}, None)
    assert not small_file.exists() and not stamp.exists() and not env.exists()
    assert recorder.exists()


def test_uninstall_kokoro_removes_its_files_and_environment_only(scratch):
    files = [_write(kokoro_manifest.MODEL_DIR / e["name"]) for e in kokoro_manifest.FILES]
    stamp = _stamp(kokoro_manifest, kokoro_manifest.FILES)
    foreign = _write(kokoro_manifest.MODEL_DIR / "my-notes.txt")
    kokoro_env, parakeet_env = _env("kokoro"), _env("parakeet")
    other_env = _write(locks.envs_dir() / "kokoro-extra" / "keep")

    assert actions.run("uninstall_kokoro", {}, None) == "Kokoro is removed."
    assert not any(path.exists() for path in files) and not stamp.exists()
    assert not kokoro_env.exists()
    assert foreign.exists() and parakeet_env.exists() and other_env.exists()


@pytest.mark.parametrize("action_id, manifest, engine", [
    ("uninstall_parakeet", parakeet_manifest, "parakeet"),
    ("uninstall_cleanup", llm_manifest, "llm"),
])
def test_uninstall_removes_model_files_and_that_engines_env(scratch, action_id, manifest, engine):
    files = [_write(manifest.MODEL_DIR / e["name"]) for e in manifest.FILES]
    env, whisper_env = _env(engine), _env("whisper")
    actions.run(action_id, {}, None)
    assert not any(path.exists() for path in files) and not env.exists()
    assert not manifest.MODEL_DIR.exists()
    assert whisper_env.exists()


def test_uninstall_piper_and_nothing_installed(scratch):
    assert "nothing was removed" in actions.run("uninstall_piper", {}, None)
    voice = _write(piper_manifest.MODEL_DIR / piper_manifest.FILES[0]["name"])
    actions.run("uninstall_piper", {}, None)
    assert not voice.exists()


def test_uninstall_leaves_a_symlinked_model_file_alone(scratch):
    outside = _write(scratch / "external" / "weights")
    link = kokoro_manifest.MODEL_DIR / kokoro_manifest.FILES[0]["name"]
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    actions.run("uninstall_kokoro", {}, None)
    assert outside.exists()


# --- the audio cache --------------------------------------------------


def test_clear_audio_cache_removes_only_cache_files(scratch):
    root = tts.DEFAULT_CACHE_DIR
    entries = [_write(root / f"{HEX}.wav", b"1234"), _write(root / f"{'b' * 64}.mp3", b"12"),
               _write(root / ".audio-tmp123", b"1")]
    keep = [_write(root / "app.log"), _write(root / "models" / "kokoro" / f"{HEX}.wav"),
            _write(root / "dictate.session"), _write(root / f"{'c' * 63}.wav")]
    outside = _write(scratch / "outside.wav", b"keep")
    (root / f"{'d' * 64}.wav").symlink_to(outside)

    assert actions.state()["clear_audio_cache"]["status"] == "3 files, 7 B"
    assert actions.run("clear_audio_cache", {}, None) == "Removed 3 cached files (7 B)."
    assert not any(path.exists() for path in entries)
    assert all(path.exists() for path in keep) and outside.read_bytes() == b"keep"
    assert actions.state()["clear_audio_cache"]["available"] is False


# --- the menu-bar app -------------------------------------------------


@pytest.fixture
def fake_app(monkeypatch):
    calls = Calls()
    ok = SimpleNamespace(returncode=0)
    monkeypatch.setattr(app, "discoverable", lambda path: True)
    monkeypatch.setattr(app, "build", calls.record("build", ("built", None)))
    monkeypatch.setattr(app, "write_plist", calls.record("write_plist"))
    monkeypatch.setattr(app, "bootout", calls.record("bootout"))
    monkeypatch.setattr(app, "bootstrap", calls.record("bootstrap", ok))
    monkeypatch.setattr(app, "kickstart", calls.record("kickstart", ok))
    monkeypatch.setattr(app, "reset_accessibility", calls.record("reset_accessibility"))
    monkeypatch.setattr(app, "forget_binary_override", calls.record("forget_binary_override"))
    return calls


def test_app_install_runs_the_cli_sequence(fake_app):
    assert "installed" in actions.run("app_install", {}, None)
    assert fake_app.names() == ["build", "write_plist", "bootout", "bootstrap"]


def test_app_install_after_a_rebuild_resets_the_grant_and_says_so(fake_app, monkeypatch):
    monkeypatch.setattr(app, "build", fake_app.record("build", ("rebuilt", None)))
    message = actions.run("app_install", {}, None)
    assert "reset_accessibility" in fake_app.names()
    assert app.APP_REGRANT_WARNING in message


def test_app_install_refuses_an_undiscoverable_vocalize(fake_app, monkeypatch):
    monkeypatch.setattr(app, "discoverable", lambda path: False)
    with pytest.raises(actions.ActionError, match="looks for vocalize only"):
        actions.run("app_install", {}, None)
    assert fake_app.log == []


def test_app_install_reports_a_failed_load(fake_app, monkeypatch):
    monkeypatch.setattr(app, "bootstrap", lambda: SimpleNamespace(returncode=5))
    with pytest.raises(actions.ActionError, match="launchctl"):
        actions.run("app_install", {}, None)


def test_app_restart_and_uninstall(fake_app):
    actions.run("app_restart", {}, None)
    assert fake_app.names() == ["kickstart"]
    plist = _write(app.plist_path())
    log = _write(app.log_path())
    unrelated = _write(app.CACHE_DIR / "mic.status")
    actions.run("app_uninstall", {}, None)
    assert not plist.exists() and not log.exists() and unrelated.exists()
    assert fake_app.names()[1:] == ["bootout", "forget_binary_override"]


def test_app_restart_failure(fake_app, monkeypatch):
    monkeypatch.setattr(app, "kickstart", lambda: None)
    with pytest.raises(actions.ActionError, match="restart"):
        actions.run("app_restart", {}, None)


# --- integrations and the hook ----------------------------------------


def test_integrate_claude_calls_the_cli_installer(monkeypatch):
    calls = Calls()
    monkeypatch.setattr(integrate, "integrate_claude", calls.record("integrate_claude", 0))
    assert "Quick Actions" in actions.run("integrate_claude", {}, None)
    assert calls.of("integrate_claude") == [((), {"yes": False})]

    monkeypatch.setattr(integrate, "integrate_claude", lambda yes: 1)
    with pytest.raises(actions.ActionError, match="vocalize integrate claude"):
        actions.run("integrate_claude", {}, None)


def test_remove_integrations_calls_the_remover(monkeypatch, scratch):
    monkeypatch.setattr(integrate, "remove_claude", lambda: ([scratch / "a"], [scratch / "b"]))
    assert actions.run("remove_integrations", {}, None) == (
        "Removed 1 item. Kept 1 that vocalize did not write.")


def test_hook_install_and_remove_round_trip():
    settings = install_hook.SETTINGS_PATH
    _write(settings, json.dumps({"model": "opus"}).encode())
    assert actions.state()["hook_remove"]["available"] is False
    actions.run("hook_install", {}, None)
    assert install_hook.installed()
    assert actions.state()["hook_install"]["status"] == "installed"
    actions.run("hook_remove", {}, None)
    assert json.loads(settings.read_text()) == {"model": "opus"}


def test_hook_with_invalid_settings_is_left_alone():
    _write(install_hook.SETTINGS_PATH, b"{not json")
    with pytest.raises(actions.ActionError, match="not valid JSON"):
        actions.run("hook_install", {}, None)
    assert install_hook.SETTINGS_PATH.read_bytes() == b"{not json"


def test_hook_unavailable_when_the_scripts_are_missing(monkeypatch, tmp_path):
    """Neither a packaged copy nor this project's checkout: no button, and
    never a `hooks/` folder found beside the package."""
    monkeypatch.setattr(actions, "_hook_module", REAL_HOOK_MODULE)
    monkeypatch.setattr(actions, "_PACKAGE_DIR", tmp_path / "site-packages" / "vocalize")
    (tmp_path / "site-packages" / "hooks").mkdir(parents=True)
    (tmp_path / "site-packages" / "hooks" / "install_hook.py").write_text("raise SystemExit(9)\n")
    monkeypatch.setattr(actions, "_hook_cache", {})
    hook = actions.state()["hook_install"]
    assert hook["available"] is False and "missing from this copy" in hook["reason"]
    with pytest.raises(actions.ActionError, match="does not include the Claude Code hook"):
        actions.run("hook_install", {}, None)


def test_the_hook_loads_from_this_checkout(monkeypatch):
    monkeypatch.setattr(actions, "_hook_module", REAL_HOOK_MODULE)
    monkeypatch.setattr(actions, "_hook_cache", {})
    module = actions._hook_module()
    assert callable(module.main) and callable(module.remove) and callable(module.installed)
    assert module.HOOK_SCRIPT.parent == actions.hooks_dir().resolve()


# --- preview, profile, permissions ------------------------------------


def test_preview_voice_speaks_the_fixed_sentence(monkeypatch):
    from vocalize import audio, chain

    calls = Calls()
    monkeypatch.setattr(chain, "run", calls.record("chain.run", (b"RIFF", "kokoro", "wav")))
    played = []
    monkeypatch.setattr(audio, "play", lambda path: played.append(path.read_bytes()) or 0)
    actions.run("preview_voice", {"engine": "kokoro", "voice": "bf_emma"}, None)
    (args, kwargs), = calls.of("chain.run")
    assert args == (actions.PREVIEW_TEXT,)
    assert kwargs["chain"] == ["kokoro"] and kwargs["overrides"] == {"voice_id": "bf_emma"}
    assert kwargs["forced"] is True
    assert played == [b"RIFF"]


@pytest.mark.parametrize("engine, voice", [("kokoro", "nobody"), ("piper", "af_heart"),
                                           ("say", "-v Alex")])
def test_preview_voice_refuses_a_voice_the_engine_lacks(monkeypatch, engine, voice):
    from vocalize import chain

    monkeypatch.setattr(chain, "run", _no_process)
    with pytest.raises(actions.ActionError, match="Choose one from the list"):
        actions.run("preview_voice", {"engine": engine, "voice": voice}, None)


def test_apply_profile_writes_its_settings_and_names_its_installs(scratch):
    from vocalize import config, profiles

    path = config.config_path()
    _write(path, b'chain = ["say"]\n')
    message = actions.run("apply_profile", {"profile": "studio"}, None)
    written = config.load_config_file()
    assert written["chain"] == ["say"]
    for key, value in profiles.changes("studio").items():
        table, name = key.split(".")
        assert written[table][name] == value
    for action_id in profiles.installs("studio"):
        assert actions._BY_ID[action_id].label in message


def test_open_settings_panes(monkeypatch):
    opened = []
    monkeypatch.setattr(actions, "_on_mac", lambda: True)
    monkeypatch.setattr(actions, "_run_open",
                        lambda url: opened.append(url) or SimpleNamespace(returncode=0))
    actions.run("open_microphone_settings", {}, None)
    actions.run("open_accessibility_settings", {}, None)
    assert opened == [actions.MICROPHONE_PANE, actions.ACCESSIBILITY_PANE]
    assert all(url.startswith("x-apple.systempreferences:") for url in opened)


# --- state and sources ------------------------------------------------


def test_state_covers_every_action_with_the_documented_shape():
    listed = actions.state()
    assert set(listed) == EXPECTED_IDS
    for entry in listed.values():
        assert set(entry) == {"available", "reason", "status"}
        assert isinstance(entry["available"], bool)
        assert isinstance(entry["reason"], str) and isinstance(entry["status"], str)
        if not entry["available"]:
            assert entry["reason"]


def test_state_reports_installed_engines_and_why_one_cannot_run(monkeypatch):
    monkeypatch.setattr(parakeet_manifest, "supported", lambda: False)
    monkeypatch.setattr(local_module, "uv_path", lambda: None)

    def installed(manifest, model_dir=None, files=None, install_hint=""):
        if manifest is whisper_manifest:
            return files == [whisper_manifest.file_for("small.en")], ""
        return manifest is kokoro_manifest, ""
    monkeypatch.setattr(install, "installed", installed)
    listed = actions.state()
    assert listed["install_kokoro"]["status"] == "installed"
    assert listed["install_kokoro"]["reason"] == actions._UV_REASON
    assert listed["install_parakeet"] == {"available": False, "reason": "needs Apple Silicon",
                                          "status": "not installed"}
    assert listed["install_cleanup"]["reason"] == "needs Apple Silicon"
    assert listed["install_whisper"]["status"] == "installed: small.en"
    assert listed["uninstall_parakeet"]["reason"] == "not installed"


def test_state_and_sources_never_raise_when_everything_underneath_fails(monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("boom")
    from vocalize import profiles, providers

    for module, name in ((install, "installed"), (app, "bundle_state"),
                         (integrate, "installed_paths"), (cache, "usage"),
                         (profiles, "detect"), (profiles, "names"), (providers, "get"),
                         (local_module, "uv_path"), (install, "recorder_binary"),
                         (whisper_manifest, "file_for"), (actions, "_on_mac")):
        monkeypatch.setattr(module, name, broken)
    monkeypatch.setattr(actions, "_hook_module", broken)
    listed = actions.state()
    assert set(listed) == EXPECTED_IDS
    assert all(isinstance(entry["available"], bool) for entry in listed.values())
    found = actions.sources()
    assert set(found) >= {"kokoro_voices", "piper_voices", "say_voices", "input_devices",
                          "whisper_models", "tts_engines", "stt_engines"}
    assert found["kokoro_voices"] == [] and found["whisper_models"] == []
    assert found["input_devices"] == []
    json.dumps(actions.schema())


def test_sources_list_what_the_command_line_lists():
    found = actions.sources()
    assert [v["value"] for v in found["kokoro_voices"]] == list(kokoro_manifest.VOICES)
    assert [v["value"] for v in found["piper_voices"]] == list(piper_manifest.VOICES)
    # The empty value first: what the transcription model's empty choice means.
    assert [v["value"] for v in found["whisper_models"]] == ["", *whisper_manifest.MODELS]
    assert found["whisper_models"][0]["label"] == "Use the dictation model"
    assert [v["value"] for v in found["tts_engines"]] == ["kokoro", "piper", "say"]
    assert [v["value"] for v in found["stt_engines"]] == ["auto", "parakeet", "whisper"]
    assert [v["value"] for v in found["profiles"]] == ["small", "standard", "large", "studio"]
    # `say -v ?` cannot run here and the recorder is not built: empty, not an error.
    assert found["say_voices"] == [] and found["input_devices"] == []
    for entries in found.values():
        assert all(set(e) == {"value", "label"} for e in entries)


def test_input_devices_come_from_the_recorder(monkeypatch, scratch):
    binary = _write(scratch / "recorder")
    monkeypatch.setattr(install, "recorder_binary", lambda: binary)
    monkeypatch.setattr(install, "recorder_is_current", lambda: True)
    argv_seen = []

    def run(argv, **kwargs):
        argv_seen.append(argv)
        return SimpleNamespace(returncode=0, stdout="MacBook Mic\nUSB \x1b[31mMic\n\n")
    monkeypatch.setattr(subprocess, "run", run)
    assert actions.sources()["input_devices"] == [{"value": "MacBook Mic",
                                                    "label": "MacBook Mic"}]
    assert [str(binary), "--list-devices"] in argv_seen


# --- the gaps the browser run found -------------------------------------


def test_every_setting_choice_has_a_label_and_the_empty_model_says_what_it_means():
    """A setting with both `choices` and `choices_from`: the source labels
    the choices, and the empty transcription model is named in words."""
    found = actions.sources()
    for setting in settings_registry.SETTINGS:
        if setting.choices is None or not setting.choices_from:
            continue
        labels = {entry["value"]: entry["label"] for entry in found[setting.choices_from]}
        if "" in setting.choices:
            assert labels.get("", "").strip(), setting.key
    model = settings_registry.by_key("transcribe.model")
    assert "" in model.choices
    labels = {e["value"]: e["label"] for e in found[model.choices_from]}
    assert labels[""] == "Use the dictation model"
    # The installs that share the source still offer only real models.
    install_param = actions._BY_ID["install_whisper"].params[0]
    assert "" not in actions._choices(install_param)
    with pytest.raises(actions.ActionError):
        actions.run("install_whisper", {"model": ""}, None)


def test_source_notes_say_why_the_microphone_list_is_empty(monkeypatch, scratch):
    """No recorder, a stale one, or one that listed nothing: each says what to do."""
    notes = actions.source_notes({"input_devices": []})
    assert "Install a dictation engine" in notes["input_devices"]
    assert "recorder is not built" in notes["input_devices"]

    binary = _write(scratch / "recorder")
    monkeypatch.setattr(install, "recorder_binary", lambda: binary)
    monkeypatch.setattr(install, "recorder_is_current", lambda: False)
    assert "another version of vocalize" in actions.source_notes({"input_devices": []})[
        "input_devices"]
    monkeypatch.setattr(install, "recorder_is_current", lambda: True)
    assert "listed no microphones" in actions.source_notes({"input_devices": []})[
        "input_devices"]
    listed = {"input_devices": [{"value": "Mic", "label": "Mic"}]}
    assert actions.source_notes(listed) == {}


def test_source_notes_never_raise(monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(install, "recorder_binary", broken)
    assert actions.source_notes({})["input_devices"]


def test_apply_profile_says_where_the_backup_of_a_commented_file_went(scratch):
    from vocalize import config, wizard

    path = config.config_path()
    _write(path, b'# my notes\nchain = ["say"]\n')
    message = actions.run("apply_profile", {"profile": "studio"}, None)
    backup = wizard.backup_path(path)
    assert backup.is_file()
    assert f"was kept at {backup}." in message
    assert "with its comments" in message
    # The next apply makes no new backup, so it says nothing about one.
    assert "was kept at" not in actions.run("apply_profile", {"profile": "small"}, None)


class _Written(tuple):
    """What `wizard.write_config_if_unchanged` returns: (text, fingerprint)
    with the backup it made, and whether that backup holds retired keys."""

    def __new__(cls, backup=None, **extra):
        self = super().__new__(cls, ("", "fp"))
        self.backup = backup
        self.__dict__.update(extra)
        return self


def _apply_with(monkeypatch, written):
    from vocalize import wizard

    monkeypatch.setattr(wizard, "write_config_if_unchanged", lambda *a, **k: written)
    return actions.run("apply_profile", {"profile": "studio"}, None)


def test_apply_profile_warns_when_the_backup_keeps_retired_settings(monkeypatch, scratch):
    """L5: the copy keeps everything in the old file, a retired service key
    included, and the message said only where it went."""
    message = _apply_with(monkeypatch, _Written(scratch / "config.toml.bak", backup_has_retired=True))
    assert f"was kept at {scratch / 'config.toml.bak'}." in message
    assert actions.RETIRED_BACKUP_WORDS in message
    assert "service key" in actions.RETIRED_BACKUP_WORDS
    assert "delete" in actions.RETIRED_BACKUP_WORDS


@pytest.mark.parametrize("written", [
    _Written(Path("/b/config.toml.bak"), backup_has_retired=False),
    _Written(Path("/b/config.toml.bak")),  # a writer without the attribute
    _Written(None, backup_has_retired=True),  # no backup, nothing to say
])
def test_apply_profile_says_nothing_about_retired_settings_otherwise(monkeypatch, written):
    assert actions.RETIRED_BACKUP_WORDS not in _apply_with(monkeypatch, written)


def test_apply_profile_names_the_parameters_of_a_recommended_install(monkeypatch):
    from vocalize import profiles

    monkeypatch.setattr(profiles, "installs", lambda name: ("install_whisper", "install_parakeet"))
    monkeypatch.setattr(profiles, "install_params",
                        lambda name: {"install_whisper": {"model": "large-v3-turbo-q8_0"}},
                        raising=False)
    message = _apply_with(monkeypatch, _Written())
    assert "Install a Whisper model (large-v3-turbo-q8_0)" in message
    assert actions._BY_ID["install_parakeet"].label + "." in message


def test_apply_profile_without_install_params_names_the_installs_alone(monkeypatch):
    from vocalize import profiles

    monkeypatch.setattr(profiles, "installs", lambda name: ("install_whisper",))
    monkeypatch.delattr(profiles, "install_params", raising=False)
    message = _apply_with(monkeypatch, _Written())
    assert "It recommends: Install a Whisper model." in message


class _WarmServer:
    """A running warm server, as the uninstall sees it through warm.py: at
    `warm.socket_path(kind)`, in a private folder, answering hello."""

    def __init__(self, monkeypatch, kind, files_to_check=()):
        from vocalize.local import warm, warm_protocol

        self.path = warm.socket_path(kind)
        warm_protocol.ensure_private_dir(self.path.parent)
        self.running = True
        self.stopped = []
        self.files_at_stop = None
        self.files = list(files_to_check)

        def check(path):
            return path == self.path and self.running

        def hello(path, deadline):
            return {"ok": True} if check(path) else None

        def stop(path, ident, deadline):
            self.stopped.append((path, ident))
            self.files_at_stop = [f.exists() for f in self.files]
            self.running = False

        monkeypatch.setattr(warm_protocol, "check_socket_path", check)
        monkeypatch.setattr(warm, "_hello", hello)
        monkeypatch.setattr(warm, "_stop", stop)


def test_uninstall_whisper_stops_its_warm_server_before_removing_files(monkeypatch, scratch):
    entry = whisper_manifest.file_for("base.en")
    model = _write(whisper_manifest.MODEL_DIR / entry["name"])
    env = _env("whisper")
    server = _WarmServer(monkeypatch, "whisper", [model, env])

    message = actions.run("uninstall_whisper", {"model": "base.en"}, None)

    assert server.stopped == [(server.path, None)]
    assert server.files_at_stop == [True, True], "stopped before anything was removed"
    assert not model.exists() and not env.exists()
    assert message == "Whisper base.en is removed. Its loaded model was stopped first."


def test_uninstall_cleanup_stops_the_llm_warm_server(monkeypatch, scratch):
    files = [_write(llm_manifest.MODEL_DIR / e["name"]) for e in llm_manifest.FILES]
    env = _env("llm")
    server = _WarmServer(monkeypatch, "llm", [env])

    message = actions.run("uninstall_cleanup", {}, None)

    assert server.stopped == [(server.path, None)] and server.files_at_stop == [True]
    assert not env.exists() and not any(f.exists() for f in files)
    assert message.endswith("Its loaded model was stopped first.")


def test_an_uninstall_with_no_warm_server_running_says_nothing_about_one(scratch):
    _write(llm_manifest.MODEL_DIR / llm_manifest.FILES[0]["name"])
    assert actions.run("uninstall_cleanup", {}, None) == "The cleanup model is removed."


def test_a_warm_server_that_does_not_go_at_once_is_said_to_be_stopping(monkeypatch, scratch):
    from vocalize.local import warm

    _write(llm_manifest.MODEL_DIR / llm_manifest.FILES[0]["name"])
    _WarmServer(monkeypatch, "llm")
    monkeypatch.setattr(warm, "_stop", lambda *a: None)  # it stays bound
    assert "told to stop" in actions.run("uninstall_cleanup", {}, None)


def test_an_uninstall_finds_a_warm_server_in_the_short_fallback_folder(monkeypatch, scratch):
    """A long HOME moves the socket to a short private folder; the uninstall
    must look there, not at the too-long usual path (W5-B)."""
    import shutil
    import tempfile

    from vocalize.local import warm, warm_protocol

    root = tempfile.mkdtemp(prefix="vw", dir="/tmp")
    try:
        long_dir = scratch / ("d" * 60) / ("e" * 60)
        monkeypatch.setattr(warm_protocol, "warm_dir", lambda base=None: long_dir)
        monkeypatch.setattr(warm, "_fallback_parents", lambda: [root])
        _write(llm_manifest.MODEL_DIR / llm_manifest.FILES[0]["name"])
        server = _WarmServer(monkeypatch, "llm")
        assert str(server.path).startswith(root)

        message = actions.run("uninstall_cleanup", {}, None)

        assert server.stopped == [(server.path, None)]
        assert message.endswith("Its loaded model was stopped first.")
    finally:
        shutil.rmtree(root)


# --- stop_warm: let go of the models kept loaded ------------------------


def test_stop_warm_is_a_plain_instant_dictation_action():
    action = next(a for a in actions.ACTIONS if a.id == "stop_warm")
    assert (action.group, action.kind, action.params) == ("dictation", "instant", ())
    assert not action.danger
    words = action.help.lower()
    assert "memory" in words and "next dictation loads them again" in words


def test_stop_warm_state_names_what_is_loaded(monkeypatch):
    _WarmServer(monkeypatch, "llm")
    assert actions.state()["stop_warm"] == {
        "available": True, "reason": "", "status": "loaded now: the cleanup model"}


def test_stop_warm_state_is_unavailable_when_nothing_is_loaded():
    assert actions.state()["stop_warm"] == {
        "available": False, "reason": "no model is loaded", "status": "nothing loaded"}


def test_stop_warm_state_never_raises(monkeypatch):
    from vocalize.local import warm

    monkeypatch.setattr(warm, "is_running", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    entry = actions.state()["stop_warm"]
    assert entry["available"] is False and entry["reason"]


def test_stop_warm_stops_every_running_server(monkeypatch):
    whisper = _WarmServer(monkeypatch, "whisper")
    assert actions.run("stop_warm", {}, None) == (
        "Whisper was unloaded. The next dictation loads it again.")
    assert whisper.stopped == [(whisper.path, None)]


def test_stop_warm_with_nothing_loaded_says_so():
    assert actions.run("stop_warm", {}, None) == "No model was loaded; there was nothing to unload."


def test_stop_warm_names_both_when_both_were_loaded(monkeypatch):
    from vocalize.local import warm

    monkeypatch.setattr(warm, "stop_all", lambda: {"whisper": True, "llm": True})
    assert actions.run("stop_warm", {}, None) == (
        "Whisper and the cleanup model were unloaded. The next dictation loads them again.")
