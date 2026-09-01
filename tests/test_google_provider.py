import base64
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
from vocalize.providers import _http, google

FAKE_KEY = "goog-test-do-not-leak-this-value"


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
    return urllib.error.HTTPError(
        "https://texttospeech.googleapis.com/v1/text:synthesize", status, "err", {}, io.BytesIO(body)
    )


def _audio_response(raw: bytes) -> _FakeResponse:
    payload = json.dumps({"audioContent": base64.b64encode(raw).decode("ascii")}).encode("utf-8")
    return _FakeResponse(200, payload)


@pytest.fixture
def google_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", FAKE_KEY)
    return FAKE_KEY


def test_check_passes_when_a_key_is_available(google_key):
    google.check()  # must not raise


def test_check_is_unavailable_without_a_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    with pytest.raises(ProviderUnavailableError):
        google.check()


def test_contract_attributes_google_needs_beyond_the_base_contract():
    assert google.MAX_BYTES == 4900
    assert google.COUNT_UNIT == "bytes"


def test_success_decodes_the_base64_audio(google_key, monkeypatch):
    _seam(monkeypatch, _audio_response(b"mp3-bytes"))

    audio = google.synthesize("hello", Settings(voice_id="en-US-Neural2-F"))

    assert audio == b"mp3-bytes"


def test_the_key_travels_in_a_header_never_in_the_url(google_key, monkeypatch):
    seen = _seam(monkeypatch, _audio_response(b"audio"))

    google.synthesize("hi", Settings(voice_id="en-US-Neural2-F"))

    req = seen[0]
    assert req.get_header("X-goog-api-key") == FAKE_KEY
    assert FAKE_KEY not in req.full_url
    assert "key=" not in req.full_url


def test_the_request_body_shape(google_key, monkeypatch):
    seen = _seam(monkeypatch, _audio_response(b"audio"))

    google.synthesize("hello there", Settings(voice_id="en-US-Neural2-F", language="en-US"))

    req = seen[0]
    assert req.get_method() == "POST"
    assert req.full_url == "https://texttospeech.googleapis.com/v1/text:synthesize"
    assert req.get_header("Content-type") == "application/json"
    body = json.loads(req.data)
    assert body == {
        "input": {"text": "hello there"},
        "voice": {"languageCode": "en-US", "name": "en-US-Neural2-F"},
        "audioConfig": {"audioEncoding": "MP3"},
    }


def test_language_is_derived_from_the_voice_name_when_unset(google_key, monkeypatch):
    seen = _seam(monkeypatch, _audio_response(b"audio"))

    google.synthesize("hi", Settings(voice_id="en-US-Neural2-F", language=None))

    assert json.loads(seen[0].data)["voice"]["languageCode"] == "en-US"


def test_an_explicit_language_wins_over_the_voice_name(google_key, monkeypatch):
    seen = _seam(monkeypatch, _audio_response(b"audio"))

    google.synthesize("hi", Settings(voice_id="fr-FR-Neural2-A", language="en-US"))

    assert json.loads(seen[0].data)["voice"]["languageCode"] == "en-US"


def test_speed_becomes_speaking_rate_inside_audio_config(google_key, monkeypatch):
    seen = _seam(monkeypatch, _audio_response(b"audio"))

    google.synthesize("hi", Settings(voice_id="en-US-Neural2-F", speed=1.1))

    assert json.loads(seen[0].data)["audioConfig"]["speakingRate"] == 1.1


def test_no_speed_means_no_speaking_rate(google_key, monkeypatch):
    seen = _seam(monkeypatch, _audio_response(b"audio"))

    google.synthesize("hi", Settings(voice_id="en-US-Neural2-F"))

    assert "speakingRate" not in json.loads(seen[0].data)["audioConfig"]


def test_a_missing_audio_content_field_is_transient(google_key, monkeypatch):
    _seam(monkeypatch, _FakeResponse(200, b'{"somethingElse": true}'))

    with pytest.raises(ProviderTransientError, match="returned no audio"):
        google.synthesize("hi", Settings(voice_id="en-US-Neural2-F"))


def test_invalid_base64_in_audio_content_is_transient(google_key, monkeypatch):
    _seam(monkeypatch, _FakeResponse(200, b'{"audioContent": "not-valid-base64!!"}'))

    with pytest.raises(ProviderTransientError, match="returned no audio"):
        google.synthesize("hi", Settings(voice_id="en-US-Neural2-F"))


def test_a_429_is_a_quota_error(google_key, monkeypatch):
    _seam(monkeypatch, _http_error(429, {"error": {"message": "slow down"}}))

    with pytest.raises(ProviderQuotaError, match="quota exhausted"):
        google.synthesize("hi", Settings(voice_id="en-US-Neural2-F"))


def test_resource_exhausted_status_is_a_quota_error_even_off_a_400(google_key, monkeypatch):
    _seam(
        monkeypatch,
        _http_error(400, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "over budget"}}),
    )

    with pytest.raises(ProviderQuotaError, match="quota exhausted"):
        google.synthesize("hi", Settings(voice_id="en-US-Neural2-F"))


@pytest.mark.parametrize("status", [401, 403])
def test_401_and_403_are_auth_errors(google_key, monkeypatch, status):
    _seam(monkeypatch, _http_error(status, {"error": {"message": "no access"}}))

    with pytest.raises(ProviderAuthError, match="billing not enabled"):
        google.synthesize("hi", Settings(voice_id="en-US-Neural2-F"))


@pytest.mark.parametrize("api_status", ["UNAUTHENTICATED", "PERMISSION_DENIED"])
def test_auth_statuses_are_auth_errors_regardless_of_http_code(google_key, monkeypatch, api_status):
    _seam(monkeypatch, _http_error(400, {"error": {"status": api_status, "message": "nope"}}))

    with pytest.raises(ProviderAuthError):
        google.synthesize("hi", Settings(voice_id="en-US-Neural2-F"))


def test_the_key_never_appears_in_an_auth_error_message(google_key, monkeypatch):
    _seam(monkeypatch, _http_error(403, {"error": {"message": f"key {FAKE_KEY} denied"}}))

    with pytest.raises(ProviderAuthError) as excinfo:
        google.synthesize("hi", Settings(voice_id="en-US-Neural2-F"))

    assert FAKE_KEY not in str(excinfo.value)


@pytest.mark.parametrize("status", [500, 502, 503])
def test_5xx_is_transient(google_key, monkeypatch, status):
    _seam(monkeypatch, _http_error(status, {"error": {"message": "down"}}))

    with pytest.raises(ProviderTransientError, match=f"HTTP {status}"):
        google.synthesize("hi", Settings(voice_id="en-US-Neural2-F"))


def test_unavailable_status_is_transient_even_off_a_400(google_key, monkeypatch):
    _seam(monkeypatch, _http_error(400, {"error": {"status": "UNAVAILABLE", "message": "busy"}}))

    with pytest.raises(ProviderTransientError):
        google.synthesize("hi", Settings(voice_id="en-US-Neural2-F"))


def test_400_invalid_argument_is_a_content_error_naming_the_config_table(google_key, monkeypatch):
    _seam(
        monkeypatch,
        _http_error(400, {"error": {"status": "INVALID_ARGUMENT", "message": "bad voice"}}),
    )

    with pytest.raises(ProviderContentError, match=r"\[providers.google\] voice/language"):
        google.synthesize("hi", Settings(voice_id="not-a-real-voice"))


def test_the_key_never_appears_in_a_content_error_message(google_key, monkeypatch):
    _seam(
        monkeypatch,
        _http_error(
            400,
            {"error": {"status": "INVALID_ARGUMENT", "message": f"rejected key {FAKE_KEY}"}},
        ),
    )

    with pytest.raises(ProviderContentError) as excinfo:
        google.synthesize("hi", Settings(voice_id="en-US-Neural2-F"))

    assert FAKE_KEY not in str(excinfo.value)


def test_an_unparseable_400_body_still_stops_the_chain_as_content(google_key, monkeypatch):
    # No status field to read is not the same as no status at all: a bare
    # 400 with a body we can't parse is still "the request was wrong".
    _seam(monkeypatch, _http_error(400, None))

    with pytest.raises(ProviderContentError, match="HTTP 400"):
        google.synthesize("hi", Settings(voice_id="en-US-Neural2-F"))


def test_an_unparseable_body_on_an_unmapped_status_is_transient(google_key, monkeypatch):
    _seam(monkeypatch, _http_error(409, None))

    with pytest.raises(ProviderTransientError, match="HTTP 409"):
        google.synthesize("hi", Settings(voice_id="en-US-Neural2-F"))


def test_list_voices_shape(google_key, monkeypatch):
    payload = {
        "voices": [
            {"name": "en-US-Neural2-F", "languageCodes": ["en-US"]},
            {"name": "fr-FR-Neural2-A", "languageCodes": ["fr-FR"]},
            "not-a-dict",
            {"languageCodes": ["de-DE"]},  # no name: skipped
        ]
    }
    _seam(monkeypatch, _FakeResponse(200, json.dumps(payload).encode("utf-8")))

    voices = google.list_voices()

    assert voices == [
        {"id": "en-US-Neural2-F", "name": "en-US-Neural2-F (en-US)"},
        {"id": "fr-FR-Neural2-A", "name": "fr-FR-Neural2-A (fr-FR)"},
    ]


def test_list_voices_uses_the_header_and_no_query_key(google_key, monkeypatch):
    seen = _seam(monkeypatch, _FakeResponse(200, b'{"voices": []}'))

    google.list_voices()

    req = seen[0]
    assert req.get_header("X-goog-api-key") == FAKE_KEY
    assert FAKE_KEY not in req.full_url


def test_list_voices_raises_the_classified_error_on_failure(google_key, monkeypatch):
    _seam(monkeypatch, _http_error(401, {"error": {"message": "no"}}))

    with pytest.raises(ProviderAuthError):
        google.list_voices()


def test_validate_accepts_a_working_key(monkeypatch):
    seen = _seam(monkeypatch, _FakeResponse(200, b'{"voices": []}'))

    google.validate(FAKE_KEY)  # must not raise

    req = seen[0]
    assert req.get_method() == "GET"
    assert "languageCode=en-US" in req.full_url
    assert req.get_header("X-goog-api-key") == FAKE_KEY
    assert FAKE_KEY not in req.full_url.split("?")[0]


@pytest.mark.parametrize("status", [401, 403])
def test_validate_rejects_a_bad_key(monkeypatch, status):
    _seam(monkeypatch, _http_error(status, {"error": {"message": "denied"}}))

    with pytest.raises(ProviderAuthError):
        google.validate(FAKE_KEY)


def test_validate_treats_a_server_error_as_transient(monkeypatch):
    _seam(monkeypatch, _http_error(500, {"error": {"message": "down"}}))

    with pytest.raises(ProviderTransientError, match="HTTP 500"):
        google.validate(FAKE_KEY)


def test_a_network_failure_is_attributed_to_google_not_http(monkeypatch, google_key):
    # Finding (8): request() used to hardcode "http" as the provider name,
    # so the chain printed "http: network error ... — trying say" instead
    # of naming google. RED on the old code: exc.provider == "http".
    import urllib.error

    _seam(monkeypatch, urllib.error.URLError("connection refused"))

    with pytest.raises(ProviderTransientError) as excinfo:
        google.synthesize("hello", Settings(voice_id="en-US-Neural2-F"))

    assert excinfo.value.provider == "google"
