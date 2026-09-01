import io
import json
import urllib.error

import pytest

from vocalize.config import Settings
from vocalize.exceptions import (
    ProviderAuthError,
    ProviderContentError,
    ProviderQuotaError,
    ProviderTransientError,
    ProviderUnavailableError,
)
from vocalize.providers import _http, openai

FAKE_KEY = "sk-test-do-not-leak-this-value"


class _FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = io.BytesIO(body)

    def read(self, size=-1):
        return self._body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _seam(monkeypatch, result):
    """Replace the one urllib call. Returns the list of Requests it saw."""
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(_http, "urlopen", fake_urlopen)
    return seen


def _http_error(status, payload) -> urllib.error.HTTPError:
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    return urllib.error.HTTPError("https://api.openai.com/v1/audio/speech", status, "err", {}, io.BytesIO(body))


@pytest.fixture
def openai_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    return FAKE_KEY


def test_check_passes_when_a_key_is_available(openai_key):
    openai.check()  # must not raise


def test_check_is_unavailable_without_a_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ProviderUnavailableError):
        openai.check()


def test_success_returns_the_audio_bytes(openai_key, monkeypatch):
    _seam(monkeypatch, _FakeResponse(200, b"mp3-bytes"))

    audio = openai.synthesize("hello", Settings(voice_id="nova", model_id="gpt-4o-mini-tts"))

    assert audio == b"mp3-bytes"


def test_the_request_carries_bearer_auth_and_json_body(openai_key, monkeypatch):
    seen = _seam(monkeypatch, _FakeResponse(200, b"audio"))

    openai.synthesize("hello there", Settings(voice_id="nova", model_id="gpt-4o-mini-tts"))

    req = seen[0]
    assert req.get_method() == "POST"
    assert req.full_url == "https://api.openai.com/v1/audio/speech"
    assert req.get_header("Authorization") == f"Bearer {FAKE_KEY}"
    assert req.get_header("Content-type") == "application/json"
    body = json.loads(req.data)
    assert body == {
        "model": "gpt-4o-mini-tts",
        "input": "hello there",
        "voice": "nova",
        "response_format": "mp3",
    }


def test_speed_is_included_when_set(openai_key, monkeypatch):
    seen = _seam(monkeypatch, _FakeResponse(200, b"audio"))

    openai.synthesize("hi", Settings(voice_id="nova", model_id="m", speed=1.2))

    assert json.loads(seen[0].data)["speed"] == 1.2


def test_speed_is_absent_when_not_set(openai_key, monkeypatch):
    seen = _seam(monkeypatch, _FakeResponse(200, b"audio"))

    openai.synthesize("hi", Settings(voice_id="nova", model_id="m"))

    assert "speed" not in json.loads(seen[0].data)


def test_the_key_never_appears_in_the_url(openai_key, monkeypatch):
    seen = _seam(monkeypatch, _FakeResponse(200, b"audio"))

    openai.synthesize("hi", Settings(voice_id="nova", model_id="m"))

    assert FAKE_KEY not in seen[0].full_url


def test_an_empty_response_body_is_transient(openai_key, monkeypatch):
    _seam(monkeypatch, _FakeResponse(200, b""))

    with pytest.raises(ProviderTransientError, match="returned no audio"):
        openai.synthesize("hi", Settings(voice_id="nova", model_id="m"))


def test_a_401_is_an_auth_error_that_never_carries_the_key(openai_key, monkeypatch):
    _seam(monkeypatch, _http_error(401, {"error": {"message": "invalid key"}}))

    with pytest.raises(ProviderAuthError) as excinfo:
        openai.synthesize("hi", Settings(voice_id="nova", model_id="m"))

    assert FAKE_KEY not in str(excinfo.value)


@pytest.mark.parametrize(
    "error_body",
    [
        {"error": {"code": "credit_balance_exhausted"}},
        {"error": {"code": "organization_spend_limit_exceeded"}},
        {"error": {"code": "project_spend_limit_exceeded"}},
        {"error": {"type": "insufficient_quota"}},
    ],
    ids=["credit_exhausted", "org_limit", "project_limit", "insufficient_quota_type"],
)
def test_a_429_naming_an_exhausted_budget_is_a_quota_error(openai_key, monkeypatch, error_body):
    _seam(monkeypatch, _http_error(429, error_body))

    with pytest.raises(ProviderQuotaError, match="out of credit"):
        openai.synthesize("hi", Settings(voice_id="nova", model_id="m"))


def test_a_plain_429_rate_limit_is_transient_not_quota(openai_key, monkeypatch):
    _seam(monkeypatch, _http_error(429, {"error": {"code": "rate_limit_exceeded"}}))

    with pytest.raises(ProviderTransientError, match="HTTP 429"):
        openai.synthesize("hi", Settings(voice_id="nova", model_id="m"))


@pytest.mark.parametrize("status", [500, 502, 503])
def test_5xx_is_transient(openai_key, monkeypatch, status):
    _seam(monkeypatch, _http_error(status, {"error": {"message": "server error"}}))

    with pytest.raises(ProviderTransientError, match=f"HTTP {status}"):
        openai.synthesize("hi", Settings(voice_id="nova", model_id="m"))


@pytest.mark.parametrize("status", [400, 404, 422])
def test_4xx_content_errors_carry_the_api_message(openai_key, monkeypatch, status):
    _seam(monkeypatch, _http_error(status, {"error": {"message": "unknown voice 'nova2'"}}))

    with pytest.raises(ProviderContentError, match="unknown voice"):
        openai.synthesize("hi", Settings(voice_id="nova2", model_id="m"))


def test_a_content_error_with_no_message_falls_back_to_the_status(openai_key, monkeypatch):
    _seam(monkeypatch, _http_error(400, {"error": {}}))

    with pytest.raises(ProviderContentError, match="HTTP 400"):
        openai.synthesize("hi", Settings(voice_id="nova", model_id="m"))


def test_a_message_that_echoes_the_key_is_scrubbed(openai_key, monkeypatch):
    _seam(monkeypatch, _http_error(400, {"error": {"message": f"bad header: {FAKE_KEY}"}}))

    with pytest.raises(ProviderContentError) as excinfo:
        openai.synthesize("hi", Settings(voice_id="nova", model_id="m"))

    assert FAKE_KEY not in str(excinfo.value)
    assert "[key]" in str(excinfo.value)


def test_an_unrecognized_status_falls_back_to_transient(openai_key, monkeypatch):
    _seam(monkeypatch, _http_error(418, {"error": {"message": "teapot"}}))

    with pytest.raises(ProviderTransientError, match="HTTP 418"):
        openai.synthesize("hi", Settings(voice_id="nova", model_id="m"))


def test_an_unparseable_error_body_does_not_crash_the_triage(openai_key, monkeypatch):
    _seam(monkeypatch, _http_error(400, None))

    with pytest.raises(ProviderContentError, match="HTTP 400"):
        openai.synthesize("hi", Settings(voice_id="nova", model_id="m"))


def test_list_voices_is_static_and_touches_no_network(monkeypatch):
    def fail_if_called(req, timeout=None):
        raise AssertionError("list_voices must not make an HTTP call")

    monkeypatch.setattr(_http, "urlopen", fail_if_called)

    voices = openai.list_voices()

    assert len(voices) == len(openai.VOICES)
    assert {"id": "marin", "name": "marin"} in voices
    assert {"id": "alloy", "name": "alloy"} in voices


def test_validate_accepts_a_working_key(monkeypatch):
    seen = _seam(monkeypatch, _FakeResponse(200, b'{"data": []}'))

    openai.validate(FAKE_KEY)  # must not raise

    req = seen[0]
    assert req.get_method() == "GET"
    assert req.full_url == "https://api.openai.com/v1/models"
    assert req.get_header("Authorization") == f"Bearer {FAKE_KEY}"
    assert FAKE_KEY not in req.full_url


def test_validate_rejects_a_bad_key(monkeypatch):
    _seam(monkeypatch, _http_error(401, {"error": {"message": "bad key"}}))

    with pytest.raises(ProviderAuthError):
        openai.validate(FAKE_KEY)


def test_validate_treats_a_server_error_as_transient(monkeypatch):
    _seam(monkeypatch, _http_error(500, {"error": {"message": "down"}}))

    with pytest.raises(ProviderTransientError, match="HTTP 500"):
        openai.validate(FAKE_KEY)


def test_a_network_failure_is_attributed_to_openai_not_http(monkeypatch, openai_key):
    # Finding (8): request() used to hardcode "http" as the provider name,
    # so the chain printed "http: network error ... — trying say" instead
    # of naming openai. RED on the old code: exc.provider == "http".
    import urllib.error

    _seam(monkeypatch, urllib.error.URLError("connection refused"))

    with pytest.raises(ProviderTransientError) as excinfo:
        openai.synthesize("hello", Settings(voice_id="nova", model_id="m"))

    assert excinfo.value.provider == "openai"
