import pytest

from vocalize.exceptions import (
    MissingAPIKeyError,
    ProviderAuthError,
    ProviderContentError,
    ProviderError,
    ProviderQuotaError,
    ProviderTransientError,
    ProviderUnavailableError,
    TTSRequestError,
    VocalizeError,
)

# The message users have seen since 0.1. Pinned character for character:
# the README, the wizard and the hooks all quote parts of it.
ELEVENLABS_MESSAGE = (
    "No ElevenLabs API key found. The easiest fix is `vocalize auth "
    "login`, which stores one in your system keychain. You can also "
    "set the ELEVENLABS_API_KEY environment variable, add it to a "
    ".env file, or pass --api-key on the command line. Get a free key "
    "at https://elevenlabs.io/app/settings/api-keys"
)


def test_provider_error_names_the_provider():
    error = ProviderError("google", "out of credit")

    assert error.provider == "google"
    assert str(error) == "google: out of credit"


@pytest.mark.parametrize(
    "cls",
    [
        ProviderAuthError,
        ProviderQuotaError,
        ProviderTransientError,
        ProviderUnavailableError,
        ProviderContentError,
    ],
)
def test_every_provider_error_is_still_a_tts_request_error(cls):
    # Existing `except TTSRequestError` handlers, the CLI's included, must
    # keep catching provider failures.
    error = cls("polly", "nope")

    assert isinstance(error, TTSRequestError)
    assert isinstance(error, ProviderError)
    assert str(error) == "polly: nope"


def test_missing_key_default_is_elevenlabs_and_unchanged():
    assert str(MissingAPIKeyError()) == ELEVENLABS_MESSAGE
    assert str(MissingAPIKeyError("elevenlabs")) == ELEVENLABS_MESSAGE
    assert MissingAPIKeyError().provider == "elevenlabs"


def test_missing_key_for_another_provider_names_the_command_and_the_env_var():
    message = str(MissingAPIKeyError("openai"))

    assert "vocalize auth login --provider openai" in message
    assert "OPENAI_API_KEY" in message
    assert "OpenAI" in message


def test_missing_key_for_a_provider_without_an_env_var_still_reads():
    message = str(MissingAPIKeyError("polly"))

    assert "vocalize auth login --provider polly" in message
    assert "_API_KEY" not in message


def test_playback_stopped_is_a_vocalize_error():
    from vocalize.exceptions import PlaybackStopped

    assert issubclass(PlaybackStopped, VocalizeError)
