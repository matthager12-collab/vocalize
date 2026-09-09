import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import pytest

import vocalize.audio as audio_module


class FakeToolchain:
    """A stand-in for subprocess.run covering both bundle build steps
    (`build_bundle`'s swiftc compile and codesign sign) — used by both the
    recorder's own build (test_recorder_build.py) and the menu-bar app's
    (test_app_build.py), since both go through the same `build_bundle`.

    `compile_fails` / `sign_fails` take the (returncode, stderr) a broken
    toolchain would produce; `missing` names programs that are not
    installed at all, which is a FileNotFoundError, not an exit code.
    """

    def __init__(self, *, compile_fails=None, sign_fails=None, missing=()):
        self.calls = []
        self.compile_fails = compile_fails
        self.sign_fails = sign_fails
        self.missing = set(missing)

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        program = Path(argv[0]).name
        if program in self.missing:
            raise FileNotFoundError(2, "No such file or directory", argv[0])
        if program == "codesign":
            if self.sign_fails:
                code, stderr = self.sign_fails
                return subprocess.CompletedProcess(argv, code, "", stderr)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if self.compile_fails:
            code, stderr = self.compile_fails
            return subprocess.CompletedProcess(argv, code, "", stderr)
        target = Path(argv[argv.index("-o") + 1])
        target.write_bytes(b"fake-mach-o")
        target.chmod(0o755)
        return subprocess.CompletedProcess(argv, 0, "", "")

    @property
    def programs(self):
        return [Path(argv[0]).name for argv, _ in self.calls]


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    """Prevent tests from loading the developer's real .env file."""
    monkeypatch.setattr("vocalize.config._load_dotenv_if_present", lambda: None)


@pytest.fixture(autouse=True)
def _no_real_config_file(monkeypatch, tmp_path):
    """Keep every test off the developer's real ~/.config/vocalize/config.toml.

    `config_path()` reads `XDG_CONFIG_HOME`, so pointing that at tmp_path
    is the whole isolation. Autouse for the same reason as the keychain
    fixture: a real `[stt] input_device` or `chain` on this machine would
    otherwise reach a command under test and turn an assertion green (or
    red) for a reason that has nothing to do with the code. A test that
    wants a config writes one into this directory.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))


class _FakeKeychain(dict):
    """The stored entries, plus the switches a test needs to break them."""

    deny_delete = False
    deny_read = False  # a locked keychain: every read raises, nothing is absent


class _FakeKeyring:
    """An in-memory stand-in for the keyring module's three calls."""

    def __init__(self, store):
        self._store = store

    def get_password(self, service, username):
        if self._store.deny_read:
            from keyring.errors import KeyringError

            raise KeyringError("User interaction is not allowed.")
        return self._store.get((service, username))

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def delete_password(self, service, username):
        from keyring.errors import PasswordDeleteError

        if self._store.deny_delete:
            # The macOS backend's worst habit: a denied or locked keychain
            # raises the very same error a missing entry does, while the
            # entry is still sitting there.
            raise PasswordDeleteError("failed to delete password")
        if self._store.pop((service, username), None) is None:
            raise PasswordDeleteError("no such password")


@pytest.fixture(autouse=True)
def _no_real_playback_lock(monkeypatch, tmp_path):
    """Keep every test off the real playback lock at ~/.cache/vocalize.

    Autouse for the same reason as the ledger fixture: a real lock held by
    an actual read on the developer's machine would otherwise make any
    play() test block until the audio finished — a hang with no error.
    """
    monkeypatch.setattr("vocalize.audio._LOCK_FILE", tmp_path / "play.lock")
    # The same reasoning for the two files DEC-003 added beside it: a test
    # that wrote a stop marker under the real cache could make the
    # developer's own next read save itself, and one that called `forget()`
    # would delete a read they had asked to continue.
    monkeypatch.setattr("vocalize.audio._INTERRUPT_FILE", tmp_path / "interrupt.request")
    monkeypatch.setattr("vocalize.audio._STOP_CLAIM_FILE", tmp_path / "stop.claim")
    monkeypatch.setattr("vocalize.interrupted.CACHE_DIR", tmp_path / "interrupt-cache")
    # Module state, not a file: a stop recorded by one test must not make
    # the next one think its own player was stopped.
    monkeypatch.setattr("vocalize.audio._last_stop", audio_module.LastStop())


@pytest.fixture(autouse=True)
def _no_real_ledger(monkeypatch, tmp_path):
    """Keep every test off the real usage ledger at ~/.cache/vocalize.

    Autouse for the same reason as fake_keychain: a real usage.json on the
    developer's machine could satisfy or skew a status()/record() call and
    quietly turn a test green (or exhausted) for the wrong reason.
    """
    monkeypatch.setattr("vocalize.ledger.DEFAULT_CACHE_DIR", tmp_path)


@pytest.fixture(autouse=True)
def _no_real_model_cache(monkeypatch, tmp_path):
    """Keep every test off the real model caches at ~/.cache/vocalize/models.

    Autouse for the same reason as the ledger and playback-lock fixtures:
    a test that forgets to override a manifest's MODEL_DIR (or does not
    need to, like a `local status` test focused on the other manifest)
    would otherwise stat — or worse, delete — the developer's real Kokoro
    or Whisper model directory. A test that needs a populated or
    specifically-shaped directory still overrides this with its own
    monkeypatch, applied after this one.
    """
    from vocalize.local import kokoro_manifest, whisper_manifest

    monkeypatch.setattr(kokoro_manifest, "MODEL_DIR", tmp_path / "default-kokoro-cache")
    monkeypatch.setattr(whisper_manifest, "MODEL_DIR", tmp_path / "default-whisper-cache")


@pytest.fixture(autouse=True)
def _no_real_recorder_bin(monkeypatch, tmp_path):
    """Keep every test out of the real recorder bundle at ~/.cache/vocalize/bin.

    Autouse for the same reason as the model-cache fixture: a test that
    reaches `build_recorder()` or `local uninstall --stt` without
    overriding the directory would otherwise compile into — or delete —
    the bundle the developer's microphone grant is attached to.
    """
    from vocalize.local import install

    monkeypatch.setattr(install, "BIN_DIR", tmp_path / "bin")


@pytest.fixture(autouse=True)
def _no_real_audio_cache(monkeypatch, tmp_path):
    """Keep the portal's previews out of the real audio cache.

    Autouse for the same reason as the ledger fixture, and sharper: a
    preview writes a *fake* provider's bytes into `~/.cache/vocalize`
    under the cache key of a real voice's settings, and the developer's
    next real `vocalize speak` of that sentence would play them back.
    `chain.run` binds `DEFAULT_CACHE_DIR` as a default argument at def
    time, so `portal.CACHE_DIR` — which the portal passes explicitly — is
    the only thing there is to point elsewhere.
    """
    monkeypatch.setattr("vocalize.portal.CACHE_DIR", tmp_path / "audio-cache")


@pytest.fixture(autouse=True)
def _no_real_app(monkeypatch, tmp_path):
    """Keep every test off the menu-bar app that may be installed on the
    developer's Mac.

    Autouse for the same reason as the model cache and the recorder bin
    fixtures: from 0.13.0 `readiness()` registers three rows the moment
    `~/Library/Application Support/vocalize/Vocalize.app` exists, and
    `app.status_dict()` runs `launchctl`. A machine that has run
    `vocalize app install` would otherwise turn every "these rows exactly"
    assertion red and spawn real tools from the suite. `tests/test_app_cli.py`
    sets its own fakes over these.
    """
    from vocalize import app as app_module

    home = tmp_path / "no-real-app-home"
    monkeypatch.setattr(app_module, "APP_DIR", home / "Library" / "Application Support" / "vocalize")
    monkeypatch.setattr(app_module, "LAUNCH_AGENTS_DIR", home / "Library" / "LaunchAgents")
    monkeypatch.setattr(app_module, "CACHE_DIR", home / ".cache" / "vocalize")
    monkeypatch.setattr(
        app_module, "SERVICES_WORKFLOW",
        home / "Library" / "Services" / "Dictate with Vocalize.workflow",
    )
    # Tools that do not exist: `_run` answers None, which reads as "unknown"
    # everywhere, and nothing real is ever spawned by a test that forgot.
    missing = str(home / "missing-tool")
    for name in ("LAUNCHCTL", "TCCUTIL", "PGREP", "DEFAULTS"):
        monkeypatch.setattr(app_module, name, missing)


@pytest.fixture(autouse=True)
def _no_real_dictation_cache(monkeypatch, tmp_path):
    """Keep every test off ~/.cache/vocalize's dictation state.

    Autouse for the same reason as the playback-lock fixture: the session
    file is a machine-wide claim. A test that created one under the real
    cache would look, to the developer's own hotkey, exactly like a
    dictation already in progress — and a test that removed one would
    cancel a real recording.
    """
    from vocalize import dictate

    monkeypatch.setattr(dictate, "CACHE_DIR", tmp_path / "dictation-cache")


@pytest.fixture(autouse=True)
def fake_browser(monkeypatch):
    """Keep every test from opening a real browser window.

    Autouse because the damage of missing one is loud and unwanted rather
    than silent: `vocalize portal` calls `webbrowser.open`, which on macOS
    hands the URL to whatever browser is running. Returns True the way a
    successful open does. Request it by name to assert what was opened.
    """
    import webbrowser

    opened: list[str] = []
    monkeypatch.setattr(
        webbrowser, "open", lambda url, *a, **k: (opened.append(url), True)[1]
    )
    return opened


@pytest.fixture(autouse=True)
def fake_keychain(monkeypatch):
    """Keep every test off the real OS keychain.

    Autouse because the damage of missing one is silent: a stored key on
    the developer's machine would otherwise satisfy resolve_api_key and
    quietly turn the "no key found" tests green for the wrong reason.
    Request it by name to seed, inspect, or break the store.
    """
    store = _FakeKeychain()
    monkeypatch.setattr("vocalize.auth._backend", lambda: _FakeKeyring(store))
    return store
