import pytest

from vocalize import tts
from vocalize.config import Settings
from vocalize.exceptions import (
    ProviderAuthError,
    ProviderContentError,
    ProviderQuotaError,
    ProviderTransientError,
)
from vocalize.providers import elevenlabs


class FakeApiError(Exception):
    """The shape the ElevenLabs SDK's ApiError presents: code plus body."""

    def __init__(self, status_code=None, body=None, message="api error"):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _detail(status):
    return {"detail": {"status": status, "message": "…"}}


@pytest.mark.parametrize(
    "exc",
    [
        FakeApiError(status_code=402),
        FakeApiError(status_code=400, body=_detail("quota_exceeded")),
        FakeApiError(status_code=401, body=_detail("insufficient_credits")),
        FakeApiError(body=_detail("payment_required")),
    ],
    ids=["402", "quota_exceeded", "insufficient_credits", "payment_required"],
)
def test_running_out_of_credit_is_a_quota_error(exc):
    # Quota beats auth on purpose: ElevenLabs answers an exhausted plan
    # with a 401 body, and retrying with a fresh key would not help.
    error = elevenlabs.classify(exc)

    assert isinstance(error, ProviderQuotaError)
    assert str(error) == "elevenlabs: out of credit"


@pytest.mark.parametrize(
    "exc",
    [
        FakeApiError(status_code=401),
        FakeApiError(status_code=403),
        FakeApiError(status_code=400, body=_detail("invalid_api_key")),
        FakeApiError(body=_detail("needs_authorization")),
    ],
    ids=["401", "403", "invalid_api_key", "needs_authorization"],
)
def test_a_bad_key_is_an_auth_error(exc):
    error = elevenlabs.classify(exc)

    assert isinstance(error, ProviderAuthError)
    assert str(error) == "elevenlabs: invalid or missing API key"


def test_text_over_the_api_limit_is_a_content_error():
    error = elevenlabs.classify(
        FakeApiError(status_code=400, body=_detail("max_character_limit_exceeded"))
    )

    assert isinstance(error, ProviderContentError)


@pytest.mark.parametrize(
    "exc",
    [
        FakeApiError(status_code=429),
        FakeApiError(status_code=500),
        FakeApiError(status_code=503),
        FakeApiError(status_code=400, body=_detail("rate_limit_exceeded")),
        FakeApiError(status_code=400, body=_detail("concurrent_limit_exceeded")),
    ],
    ids=["429", "500", "503", "rate_limit", "concurrent_limit"],
)
def test_rate_limits_and_server_faults_are_transient(exc):
    assert isinstance(elevenlabs.classify(exc), ProviderTransientError)


@pytest.mark.parametrize("code", [400, 404, 422])
def test_any_other_4xx_is_a_content_error(code):
    # Content errors stop the chain: a bad voice id would fail identically
    # on every provider, so falling through would just hide the misconfig.
    assert isinstance(elevenlabs.classify(FakeApiError(status_code=code)), ProviderContentError)


def test_an_unrecognizable_error_is_transient_and_keeps_its_message():
    error = elevenlabs.classify(RuntimeError("rate limited"))

    assert isinstance(error, ProviderTransientError)
    assert "rate limited" in str(error)


def test_a_nonsense_status_code_does_not_crash_the_triage():
    error = elevenlabs.classify(FakeApiError(status_code="502", body="not a dict"))

    assert isinstance(error, ProviderTransientError)


@pytest.mark.parametrize("exc", [TypeError("bad kwarg"), AttributeError("no attr"), KeyError("k")])
def test_our_own_bugs_are_re_raised_untouched(exc):
    # Turning a TypeError into a "transient" would send the chain to the
    # next provider and bury the bug.
    with pytest.raises(type(exc)):
        elevenlabs.classify(exc)


def test_synthesize_never_caches_here(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk-test")
    monkeypatch.setattr(tts, "build_client", lambda key: f"client:{key}")
    seen = {}

    def fake_synthesize(client, text, settings, *, cache_dir):
        seen.update(client=client, text=text, cache_dir=cache_dir)
        return b"audio"

    monkeypatch.setattr(tts, "synthesize", fake_synthesize)

    assert elevenlabs.synthesize("hi", Settings()) == b"audio"
    assert seen["client"] == "client:sk-test"
    assert seen["text"] == "hi"
    # The chain owns the cache, so it can key on the provider that answered.
    assert seen["cache_dir"] is None


def test_an_empty_response_lets_the_chain_move_to_the_next_provider(monkeypatch):
    # The whole point of the typing: ProviderTransientError is in the
    # chain's skip set, a bare TTSRequestError is not.
    from vocalize import chain as chain_module

    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk-test")

    class _EmptyTTS:
        def convert(self, **kwargs):
            return iter(())

    class _Client:
        text_to_speech = _EmptyTTS()

    monkeypatch.setattr(tts, "build_client", lambda key: _Client())

    class _Say:
        NAME = "say"
        AUDIO_EXT = "m4a"

        def __init__(self):
            self.DEFAULTS = {"voice": "Daniel"}

        def check(self, settings=None, **kwargs):
            pass

        def synthesize(self, text, settings, **kwargs):
            return b"[say]"

    say = _Say()
    monkeypatch.setattr(
        "vocalize.providers.get", lambda name: elevenlabs if name == "elevenlabs" else say
    )

    audio, name, ext = chain_module.run(
        "hello", chain=["elevenlabs", "say"], file_config={}, cache_dir=None
    )

    assert (audio, name, ext) == (b"[say]", "say", "m4a")


def test_synthesize_reports_a_missing_key_as_unavailable(monkeypatch):
    from vocalize.exceptions import ProviderUnavailableError

    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

    with pytest.raises(ProviderUnavailableError):
        elevenlabs.synthesize("hi", Settings())


def _client_that_raises(exc):
    class _TTS:
        def convert(self, **kwargs):
            raise exc

    class _Voices:
        def search(self):
            raise exc

    class _Client:
        text_to_speech = _TTS()
        voices = _Voices()

    return _Client()


def test_synthesize_never_lets_an_sdk_error_echo_the_api_key(monkeypatch):
    # h11 and friends quote the offending header value back at us, so an
    # SDK error can carry the key into stderr and into the chain's
    # all-providers-failed summary. It gets scrubbed on the way out.
    key = "sk-supersecretkey1234567890"
    monkeypatch.setenv("ELEVENLABS_API_KEY", key)
    monkeypatch.setattr(
        tts, "build_client",
        lambda k: _client_that_raises(RuntimeError(f"illegal header value b'{key}'")),
    )

    with pytest.raises(ProviderTransientError) as excinfo:
        elevenlabs.synthesize("hi", Settings())

    assert key not in str(excinfo.value)
    assert "supersecret" not in str(excinfo.value)
    assert "[key]" in str(excinfo.value)


def test_validate_never_lets_an_sdk_error_echo_the_api_key(monkeypatch):
    key = "sk-supersecretkey1234567890"
    monkeypatch.setattr(
        tts, "build_client",
        lambda k: _client_that_raises(RuntimeError(f"illegal header value b'{key}'")),
    )

    with pytest.raises(ProviderTransientError) as excinfo:
        elevenlabs.validate(key)

    assert key not in str(excinfo.value)
    assert "[key]" in str(excinfo.value)
