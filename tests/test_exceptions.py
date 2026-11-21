import pytest

from vocalize.exceptions import (
    ProviderContentError,
    ProviderError,
    ProviderTransientError,
    ProviderUnavailableError,
    TTSRequestError,
    VocalizeError,
)


def test_provider_error_names_the_provider():
    error = ProviderError("kokoro", "worker unavailable")

    assert error.provider == "kokoro"
    assert str(error) == "kokoro: worker unavailable"


@pytest.mark.parametrize(
    "cls",
    [
                ProviderTransientError,
        ProviderUnavailableError,
        ProviderContentError,
    ],
)
def test_every_provider_error_is_still_a_tts_request_error(cls):
    # Existing `except TTSRequestError` handlers, the CLI's included, must
    # keep catching provider failures.
    error = cls("piper", "nope")

    assert isinstance(error, TTSRequestError)
    assert isinstance(error, ProviderError)
    assert str(error) == "piper: nope"








def test_playback_stopped_is_a_vocalize_error():
    from vocalize.exceptions import PlaybackStopped

    assert issubclass(PlaybackStopped, VocalizeError)
