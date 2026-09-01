import importlib.util
import subprocess
import sys

import pytest

from vocalize import providers
from vocalize.auth import PROVIDER_NAMES
from vocalize.exceptions import ConfigError, ProviderUnavailableError

# The registry is deliberately ahead of the modules: names land in
# PROVIDER_NAMES first, adapters follow. A name whose module isn't written
# yet is skipped here rather than failing the suite.
BUILT = [n for n in PROVIDER_NAMES if importlib.util.find_spec(f"vocalize.providers.{n}")]


@pytest.mark.parametrize("name", BUILT)
def test_get_returns_the_module_for_every_built_provider(name):
    module = providers.get(name)

    assert module.NAME == name
    assert module.AUDIO_EXT in ("mp3", "m4a", "wav")
    assert module.MAX_CHARS is None or module.MAX_CHARS > 0
    assert isinstance(module.DEFAULTS, dict)
    assert callable(module.check)
    assert callable(module.synthesize)
    assert callable(module.list_voices)


def test_importing_the_package_pulls_in_no_provider_sdk():
    # In a fresh interpreter: another test importing the ElevenLabs SDK
    # would otherwise make this pass for the wrong reason.
    code = (
        "import sys; import vocalize.providers; "
        "print([m for m in ('boto3', 'elevenlabs') if m in sys.modules])"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == "[]"
    assert "boto3" not in sys.modules


def test_an_unknown_provider_names_the_ones_that_exist():
    with pytest.raises(ConfigError, match="Unknown provider 'nope'") as excinfo:
        providers.get("nope")

    for name in PROVIDER_NAMES:
        assert name in str(excinfo.value)


def test_require_key_returns_the_key_from_the_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")

    assert providers.require_key("openai") == "sk-openai"


def test_require_key_turns_a_missing_key_into_an_unavailable_provider(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ProviderUnavailableError) as excinfo:
        providers.require_key("openai")

    # Unavailable, not a hard error: the chain skips to the next provider.
    assert excinfo.value.provider == "openai"
    assert "vocalize auth login --provider openai" in str(excinfo.value)


def test_no_keychain_slot_can_be_invented_from_a_provider_name():
    from vocalize import auth
    from vocalize.exceptions import AuthError

    # _username is the only thing between a config value and a keychain
    # entry name, so it fails closed rather than composing one.
    for name in ("../evil", "polly", "say", "kokoro"):
        with pytest.raises(AuthError, match="does not use a stored API key"):
            auth._username(name)
