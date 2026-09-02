import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import pytest

import vocalize.audio as audio_module


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


class _FakeKeyring:
    """An in-memory stand-in for the keyring module's three calls."""

    def __init__(self, store):
        self._store = store

    def get_password(self, service, username):
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
