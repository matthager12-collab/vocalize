import pytest

from vocalize.config import resolve_api_key
from vocalize.exceptions import MissingAPIKeyError


def test_explicit_key_wins_over_everything(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env-key")
    assert resolve_api_key("explicit-key") == "explicit-key"


def test_falls_back_to_env_var(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env-key")
    assert resolve_api_key(None) == "env-key"


def test_raises_clear_error_when_nothing_found(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(MissingAPIKeyError):
        resolve_api_key(None)
