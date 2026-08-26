import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import pytest


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    """Prevent tests from loading the developer's real .env file."""
    monkeypatch.setattr("vocalize.config._load_dotenv_if_present", lambda: None)


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
