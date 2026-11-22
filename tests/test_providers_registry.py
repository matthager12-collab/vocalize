import importlib.util

import pytest

from vocalize import auth, providers
from vocalize.exceptions import ConfigError


@pytest.mark.parametrize("name", ("kokoro", "piper", "say"))
def test_local_provider_contract(name):
    module = providers.get(name)
    assert module.NAME == name
    assert module.AUDIO_EXT in ("wav", "m4a")
    assert callable(module.check) and callable(module.synthesize) and callable(module.list_voices)


@pytest.mark.parametrize("name", ("elevenlabs", "openai", "google", "polly", "anthropic", "../say", "http://127.0.0.1", "http://169.254.169.254", "javascript:alert(1)"))
def test_unsupported_provider_rejected_before_import(name, monkeypatch):
    monkeypatch.setattr(providers.importlib, "import_module", lambda *a: pytest.fail("unapproved import"))
    with pytest.raises(ConfigError):
        providers.get(name)


def test_hosted_implementation_and_credentials_are_absent():
    assert auth.PROVIDER_NAMES == ("kokoro", "piper", "say")
    for name in ("elevenlabs", "openai", "google", "polly", "_http"):
        assert importlib.util.find_spec("vocalize.providers." + name) is None
    for helper in ("stored_key", "store_key", "delete_key", "login", "_backend", "validate_key"):
        assert not hasattr(auth, helper)
