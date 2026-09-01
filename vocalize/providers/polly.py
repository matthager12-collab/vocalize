"""Amazon Polly: authenticates through boto3's own credential chain.

No key slot here — no keychain entry, no env var vocalize itself reads.
boto3.Session resolves credentials the way the AWS CLI does: environment,
`~/.aws/credentials`, a named profile, or an instance/role — so this
module's job is knowing whether that chain has anything to find, not
holding a secret itself.

boto3 is an optional dependency (`vocalize-cli[polly]`) and is imported
lazily in `_client`, never at module import time — `test_providers_registry
.test_importing_the_package_pulls_in_no_provider_sdk` enforces that boto3
stays out of sys.modules until a chain actually reaches this provider.

`check()` must never construct a boto3 Session: Session() can probe IMDS
or trigger an SSO refresh, both of which touch the network, and check()
is documented as offline-only.
"""

from __future__ import annotations

import configparser
import importlib.util
import os
from pathlib import Path

from ..config import Settings
from ..exceptions import (
    ProviderAuthError,
    ProviderContentError,
    ProviderError,
    ProviderQuotaError,
    ProviderTransientError,
    ProviderUnavailableError,
)

NAME = "polly"
AUDIO_EXT = "mp3"
# Polly's own per-request text limit for the neural engine.
MAX_CHARS = 2900
# "model" doubles as the Polly Engine ("neural" | "standard"); there is no
# separate model concept to reuse the key for.
DEFAULTS = {"voice": "Matthew", "model": "neural"}

_NEEDS_BOTO3 = "needs boto3 — install it with: pip install 'vocalize-cli[polly]'"

_QUOTA_CODES = {"ServiceQuotaExceededException"}
_AUTH_CODES = {
    "UnrecognizedClientException",
    "InvalidSignatureException",
    "AccessDeniedException",
    "ExpiredTokenException",
}
_CONTENT_CODES = {
    "TextLengthExceededException",
    "InvalidParameterValue",
    "InvalidSsmlException",
    "ValidationException",
}
_TRANSIENT_CODES = {"ThrottlingException"}
# Real botocore exception classes that carry no .response — connection-
# layer failures, told apart by name so tests need no botocore import.
_TRANSIENT_CLASSES = {"EndpointConnectionError", "ConnectTimeoutError", "ReadTimeoutError"}
_UNAVAILABLE_CLASSES = {"NoCredentialsError", "ProfileNotFound", "NoRegionError"}


def _error_code(exc: Exception) -> str | None:
    """The AWS `Error.Code` a ClientError-shaped exception carries, if any."""
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None
    error = response.get("Error")
    code = error.get("Code") if isinstance(error, dict) else None
    return code if isinstance(code, str) else None


def _http_status(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None
    meta = response.get("ResponseMetadata")
    status = meta.get("HTTPStatusCode") if isinstance(meta, dict) else None
    return status if isinstance(status, int) else None


def classify(exc: Exception) -> ProviderError:
    """Map a boto3/botocore exception to the typed error the chain acts on.

    Duck-typed throughout — by class name, and by a `.response["Error"]
    ["Code"]` dict — so the test suite never has to import botocore to
    build a realistic failure.
    """
    if isinstance(exc, ProviderError):
        return exc

    class_name = exc.__class__.__name__
    if class_name in _UNAVAILABLE_CLASSES:
        return ProviderUnavailableError(NAME, "AWS credentials not usable")
    if class_name in _TRANSIENT_CLASSES:
        return ProviderTransientError(NAME, "network error contacting Polly")

    code = _error_code(exc)
    if code in _QUOTA_CODES:
        return ProviderQuotaError(NAME, "quota exceeded")
    if code in _AUTH_CODES:
        return ProviderAuthError(NAME, "AWS credentials rejected")
    if code in _CONTENT_CODES:
        return ProviderContentError(NAME, "[providers.polly] voice/engine")
    if code in _TRANSIENT_CODES:
        return ProviderTransientError(NAME, f"temporarily unavailable ({code})")

    status = _http_status(exc)
    if status is not None and status >= 500:
        return ProviderTransientError(NAME, f"temporarily unavailable ({status})")

    # No recognizable code or class: an SDK/transport problem rather than a
    # verdict from AWS. Never a secret here — boto3 never hands this module
    # the credential values themselves.
    return ProviderTransientError(NAME, str(exc) or class_name)


def _shared_credentials_path() -> Path:
    configured = os.environ.get("AWS_SHARED_CREDENTIALS_FILE")
    return Path(configured) if configured else Path.home() / ".aws" / "credentials"


def _profile_in_credentials_file(profile: str) -> bool:
    path = _shared_credentials_path()
    if not path.is_file():
        return False
    parser = configparser.ConfigParser()
    try:
        parser.read(path)
    except (configparser.Error, OSError):
        return False
    return parser.has_section(profile)


def check(settings: Settings | None = None) -> None:
    """Offline only: never constructs a boto3 Session (that can hit IMDS/SSO)."""
    if importlib.util.find_spec("boto3") is None:
        raise ProviderUnavailableError(NAME, _NEEDS_BOTO3)

    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        return

    profile = (settings.profile if settings else None) or os.environ.get("AWS_PROFILE") or "default"
    if _profile_in_credentials_file(profile):
        return

    raise ProviderUnavailableError(
        NAME, "no AWS credentials found (env, ~/.aws/credentials, or a profile)"
    )


def _client(settings: Settings):
    """The boto3 Polly client. The one seam the tests replace."""
    try:
        import boto3
    except ImportError as exc:
        raise ProviderUnavailableError(NAME, _NEEDS_BOTO3) from exc

    try:
        session = boto3.Session(
            profile_name=settings.profile or None,
            region_name=settings.region or None,
        )
        return session.client("polly")
    except Exception as exc:
        raise classify(exc) from exc


def synthesize(text: str, settings: Settings) -> bytes:
    # Speed is ignored: Polly's rate control needs SSML <prosody>, which
    # would mean wrapping/escaping plain text as SSML — deferred.
    client = _client(settings)
    try:
        response = client.synthesize_speech(
            Text=text,
            VoiceId=settings.voice_id,
            Engine=settings.model_id,
            OutputFormat="mp3",
        )
    except Exception as exc:
        raise classify(exc) from exc

    audio = response["AudioStream"].read()
    if not audio:
        raise ProviderTransientError(NAME, "polly produced no audio")
    return audio


def list_voices() -> list[dict]:
    client = _client(Settings())
    voices: list[dict] = []
    token = None
    try:
        while True:
            kwargs = {"NextToken": token} if token else {}
            response = client.describe_voices(**kwargs)
            for voice in response.get("Voices", []):
                engines = ",".join(voice.get("SupportedEngines", []))
                voices.append(
                    {
                        "id": voice["Id"],
                        "name": f"{voice['Name']} ({voice['LanguageCode']}; {engines})",
                    }
                )
            token = response.get("NextToken")
            if not token:
                break
    except Exception as exc:
        raise classify(exc) from exc
    return voices


def validate(_key_unused: str | None = None) -> None:
    """Cheapest call that proves the current AWS credentials work."""
    try:
        _client(Settings()).describe_voices()
    except Exception as exc:
        raise classify(exc) from exc
