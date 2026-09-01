import configparser
import io

import pytest

from vocalize.config import Settings
from vocalize.exceptions import (
    ProviderAuthError,
    ProviderContentError,
    ProviderQuotaError,
    ProviderTransientError,
    ProviderUnavailableError,
)
from vocalize.providers import polly


def _write_credentials_file(path, *sections):
    parser = configparser.ConfigParser()
    for section in sections:
        parser.add_section(section)
        parser.set(section, "aws_access_key_id", "AKIAEXAMPLE")
    with path.open("w") as fh:
        parser.write(fh)


def _client_error(code, status=400):
    """A fake exception shaped like botocore.exceptions.ClientError."""
    exc = Exception(code)
    exc.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}
    return exc


def _named_exception(class_name):
    """A fake exception whose *class name* is what classify() switches on."""
    return type(class_name, (Exception,), {})(class_name)


class _FakeClient:
    def __init__(self):
        self.synthesize_calls = []
        self.describe_calls = []
        self.audio = b"mp3-bytes"
        self.error = None
        self.pages = [{"Voices": []}]

    def synthesize_speech(self, **kwargs):
        self.synthesize_calls.append(kwargs)
        if self.error:
            raise self.error
        return {"AudioStream": io.BytesIO(self.audio)}

    def describe_voices(self, **kwargs):
        self.describe_calls.append(kwargs)
        if self.error:
            raise self.error
        index = len(self.describe_calls) - 1
        return self.pages[index]


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(polly, "_client", lambda settings: client)
    return client


@pytest.fixture
def boto3_present(monkeypatch):
    monkeypatch.setattr(polly.importlib.util, "find_spec", lambda name: object())


# ---------------------------------------------------------------- check() ---


def test_check_raises_unavailable_when_boto3_is_missing(monkeypatch):
    monkeypatch.setattr(polly.importlib.util, "find_spec", lambda name: None)

    with pytest.raises(ProviderUnavailableError, match="pip install 'vocalize-cli\\[polly\\]'"):
        polly.check()


def test_check_passes_with_env_credentials(boto3_present, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "shh")

    polly.check()  # must not raise


def test_check_passes_via_default_profile_in_credentials_file(boto3_present, monkeypatch, tmp_path):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    creds = tmp_path / "credentials"
    _write_credentials_file(creds, "default")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(creds))

    polly.check()  # must not raise


def test_check_uses_settings_profile_to_pick_the_section(boto3_present, monkeypatch, tmp_path):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    creds = tmp_path / "credentials"
    _write_credentials_file(creds, "work")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(creds))

    polly.check(Settings(profile="work"))  # must not raise


def test_check_falls_back_to_aws_profile_env_var(boto3_present, monkeypatch, tmp_path):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    creds = tmp_path / "credentials"
    _write_credentials_file(creds, "ci")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(creds))
    monkeypatch.setenv("AWS_PROFILE", "ci")

    polly.check()  # must not raise


def test_check_raises_unavailable_when_nothing_is_found(boto3_present, monkeypatch, tmp_path):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "nope"))

    with pytest.raises(ProviderUnavailableError, match="no AWS credentials found"):
        polly.check()


def test_check_never_constructs_a_boto3_session(boto3_present, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "shh")

    def _boom(settings):
        raise AssertionError("check() must not build a client")

    monkeypatch.setattr(polly, "_client", _boom)

    polly.check()  # would raise AssertionError if _client were ever called


def test_the_aws_secret_never_appears_in_a_raised_message(monkeypatch):
    secret = "wJalrXUtnFEMI-super-secret-value"
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", secret)
    monkeypatch.setattr(polly.importlib.util, "find_spec", lambda name: None)

    with pytest.raises(ProviderUnavailableError) as excinfo:
        polly.check()

    assert secret not in str(excinfo.value)


# --------------------------------------------------------------- _client() ---


def test_client_raises_unavailable_when_boto3_is_not_importable(monkeypatch):
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "boto3":
            raise ImportError("no module named boto3")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    with pytest.raises(ProviderUnavailableError, match="pip install 'vocalize-cli\\[polly\\]'"):
        polly._client(Settings())


def test_client_passes_profile_and_region_to_the_session(monkeypatch):
    calls = []

    class _FakeSession:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def client(self, name):
            assert name == "polly"
            return "the-client"

    fake_boto3 = type("boto3", (), {"Session": _FakeSession})()
    monkeypatch.setitem(__import__("sys").modules, "boto3", fake_boto3)

    result = polly._client(Settings(profile="work", region="us-west-2"))

    assert result == "the-client"
    assert calls == [{"profile_name": "work", "region_name": "us-west-2"}]


def test_client_session_errors_are_classified(monkeypatch):
    class _FakeSession:
        def __init__(self, **kwargs):
            raise _named_exception("NoRegionError")

    fake_boto3 = type("boto3", (), {"Session": _FakeSession})()
    monkeypatch.setitem(__import__("sys").modules, "boto3", fake_boto3)

    with pytest.raises(ProviderUnavailableError):
        polly._client(Settings())


# -------------------------------------------------------------- synthesize --


def test_synthesize_returns_the_audio_bytes(fake_client):
    fake_client.audio = b"the-mp3-bytes"

    assert polly.synthesize("hello", Settings(voice_id="Matthew", model_id="neural")) == b"the-mp3-bytes"


def test_synthesize_sends_voice_engine_and_format(fake_client):
    polly.synthesize("hello", Settings(voice_id="Joanna", model_id="standard"))

    assert fake_client.synthesize_calls == [
        {"Text": "hello", "VoiceId": "Joanna", "Engine": "standard", "OutputFormat": "mp3"}
    ]


def test_synthesize_empty_audio_is_transient(fake_client):
    fake_client.audio = b""

    with pytest.raises(ProviderTransientError, match="no audio"):
        polly.synthesize("hello", Settings())


def test_synthesize_quota_exceeded_is_a_quota_error(fake_client):
    fake_client.error = _client_error("ServiceQuotaExceededException")

    with pytest.raises(ProviderQuotaError):
        polly.synthesize("hello", Settings())


@pytest.mark.parametrize(
    "code",
    ["UnrecognizedClientException", "InvalidSignatureException", "AccessDeniedException", "ExpiredTokenException"],
)
def test_synthesize_credential_rejection_codes_are_auth_errors(fake_client, code):
    fake_client.error = _client_error(code)

    with pytest.raises(ProviderAuthError, match="rejected"):
        polly.synthesize("hello", Settings())


@pytest.mark.parametrize(
    "code",
    ["TextLengthExceededException", "InvalidParameterValue", "InvalidSsmlException", "ValidationException"],
)
def test_synthesize_bad_request_codes_are_content_errors(fake_client, code):
    fake_client.error = _client_error(code)

    with pytest.raises(ProviderContentError, match=r"\[providers\.polly\] voice/engine"):
        polly.synthesize("hello", Settings())


def test_synthesize_throttling_is_transient(fake_client):
    fake_client.error = _client_error("ThrottlingException")

    with pytest.raises(ProviderTransientError):
        polly.synthesize("hello", Settings())


def test_synthesize_a_5xx_response_is_transient(fake_client):
    fake_client.error = _client_error("InternalFailure", status=500)

    with pytest.raises(ProviderTransientError):
        polly.synthesize("hello", Settings())


@pytest.mark.parametrize("class_name", ["EndpointConnectionError", "ConnectTimeoutError", "ReadTimeoutError"])
def test_synthesize_connection_failures_are_transient(fake_client, class_name):
    fake_client.error = _named_exception(class_name)

    with pytest.raises(ProviderTransientError):
        polly.synthesize("hello", Settings())


@pytest.mark.parametrize("class_name", ["NoCredentialsError", "ProfileNotFound", "NoRegionError"])
def test_synthesize_credential_setup_failures_are_unavailable(fake_client, class_name):
    fake_client.error = _named_exception(class_name)

    with pytest.raises(ProviderUnavailableError):
        polly.synthesize("hello", Settings())


def test_synthesize_an_unrecognized_error_falls_back_to_transient(fake_client):
    fake_client.error = RuntimeError("something odd")

    with pytest.raises(ProviderTransientError, match="something odd"):
        polly.synthesize("hello", Settings())


# ------------------------------------------------------------- list_voices --


def test_list_voices_paginates_and_formats_names(fake_client):
    fake_client.pages = [
        {
            "Voices": [
                {"Id": "Matthew", "Name": "Matthew", "LanguageCode": "en-US", "SupportedEngines": ["neural", "standard"]},
            ],
            "NextToken": "page-2",
        },
        {
            "Voices": [
                {"Id": "Amy", "Name": "Amy", "LanguageCode": "en-GB", "SupportedEngines": ["standard"]},
            ],
        },
    ]

    voices = polly.list_voices()

    assert voices == [
        {"id": "Matthew", "name": "Matthew (en-US; neural,standard)"},
        {"id": "Amy", "name": "Amy (en-GB; standard)"},
    ]
    assert len(fake_client.describe_calls) == 2
    assert fake_client.describe_calls[1] == {"NextToken": "page-2"}


def test_list_voices_maps_errors_like_synthesize(fake_client):
    fake_client.error = _client_error("AccessDeniedException")

    with pytest.raises(ProviderAuthError):
        polly.list_voices()


# ---------------------------------------------------------------- validate --


def test_validate_succeeds_with_working_credentials(fake_client):
    polly.validate()  # must not raise


def test_validate_raises_the_classified_error(fake_client):
    fake_client.error = _client_error("UnrecognizedClientException")

    with pytest.raises(ProviderAuthError):
        polly.validate()
