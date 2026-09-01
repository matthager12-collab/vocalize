"""Google Cloud Text-to-Speech's `v1/text:synthesize`, over `_http`.

The key travels in the `X-goog-api-key` header, never the URL: a key in
a query string ends up in access logs, browser history and shell history
alike, and the alternative costs nothing here.
"""

from __future__ import annotations

import base64
import binascii
import json

from .. import auth
from ..config import Settings
from ..exceptions import (
    ProviderAuthError,
    ProviderContentError,
    ProviderError,
    ProviderQuotaError,
    ProviderTransientError,
)
from . import _http, require_key

NAME = "google"
AUDIO_EXT = "mp3"
MAX_CHARS = 4500
# The API itself refuses a request body over 5000 bytes; this leaves a
# margin for the JSON envelope around the text.
MAX_BYTES = 4900
# Contract addition (see providers/__init__.py's docstring for the rest of
# the contract): when present and equal to "bytes", the ledger counts a
# chunk's UTF-8 byte length against monthly_chars instead of its character
# count — Google's own limits and billing are byte-based, not char-based.
COUNT_UNIT = "bytes"
DEFAULTS = {"voice": "en-US-Neural2-F", "language": "en-US"}

_QUOTA_STATUSES = {"RESOURCE_EXHAUSTED"}
_AUTH_STATUSES = {"UNAUTHENTICATED", "PERMISSION_DENIED"}
_TRANSIENT_STATUSES = {"UNAVAILABLE"}


def _language(settings: Settings) -> str:
    """settings.language, or the "xx-YY" prefix of a voice name like it."""
    if settings.language:
        return settings.language
    voice = settings.voice_id or ""
    return "-".join(voice.split("-")[:2])


def _parse_error(body: bytes) -> dict:
    """{"code", "message", "status"} from Google's error body, or {} if odd."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    error = data.get("error")
    return error if isinstance(error, dict) else {}


def _classify(status: int, body: bytes, key: str) -> ProviderError:
    error = _parse_error(body)
    error_status = error.get("status")
    message = error.get("message")

    if status == 429 or error_status in _QUOTA_STATUSES:
        return ProviderQuotaError(NAME, "quota exhausted")
    if status in (401, 403) or error_status in _AUTH_STATUSES:
        return ProviderAuthError(
            NAME, "invalid or missing API key, or billing not enabled"
        )
    if status >= 500 or error_status in _TRANSIENT_STATUSES:
        return ProviderTransientError(NAME, f"HTTP {status}")
    if status == 400 or error_status == "INVALID_ARGUMENT":
        text = message if isinstance(message, str) and message else f"HTTP {status}"
        return ProviderContentError(
            NAME, f"{auth.scrub(text, key)} (check [providers.google] voice/language)"
        )
    return ProviderTransientError(NAME, f"HTTP {status}")


def _decode_audio(body: bytes) -> bytes:
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ProviderTransientError(NAME, "returned no audio") from None
    content = data.get("audioContent") if isinstance(data, dict) else None
    if not isinstance(content, str) or not content:
        raise ProviderTransientError(NAME, "returned no audio")
    try:
        audio = base64.b64decode(content, validate=True)
    except (binascii.Error, ValueError):
        raise ProviderTransientError(NAME, "returned no audio") from None
    if not audio:
        raise ProviderTransientError(NAME, "returned no audio")
    return audio


def check(settings: Settings | None = None) -> None:
    require_key(NAME)


def synthesize(text: str, settings: Settings) -> bytes:
    key = require_key(NAME)

    audio_config: dict = {"audioEncoding": "MP3"}
    if settings.speed is not None:
        audio_config["speakingRate"] = settings.speed

    body = {
        "input": {"text": text},
        "voice": {"languageCode": _language(settings), "name": settings.voice_id},
        "audioConfig": audio_config,
    }

    status, response_body = _http.request(
        "POST",
        "https://texttospeech.googleapis.com/v1/text:synthesize",
        headers={"X-goog-api-key": key, "Content-Type": "application/json"},
        body=json.dumps(body).encode("utf-8"),
        provider=NAME,
    )

    if status == 200:
        return _decode_audio(response_body)

    raise _classify(status, response_body, key)


def list_voices() -> list[dict]:
    key = require_key(NAME)
    status, body = _http.request(
        "GET",
        "https://texttospeech.googleapis.com/v1/voices",
        headers={"X-goog-api-key": key},
        provider=NAME,
    )
    if status != 200:
        raise _classify(status, body, key)

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ProviderTransientError(NAME, "returned an unreadable voice list") from None

    voices = data.get("voices") if isinstance(data, dict) else None
    if not isinstance(voices, list):
        raise ProviderTransientError(NAME, "returned an unreadable voice list")

    result = []
    for entry in voices:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        codes = entry.get("languageCodes")
        codes = [c for c in codes if isinstance(c, str)] if isinstance(codes, list) else []
        result.append({"id": name, "name": f"{name} ({','.join(codes)})"})
    return result


def validate(key: str) -> None:
    """Cheapest authenticated call that proves the key works.

    The languageCode query param is not a secret, so it's fine alongside
    the header — only the key itself must stay out of the URL.
    """
    status, _ = _http.request(
        "GET",
        "https://texttospeech.googleapis.com/v1/voices?languageCode=en-US",
        headers={"X-goog-api-key": key},
        provider=NAME,
    )
    if status == 200:
        return
    if status in (401, 403):
        raise ProviderAuthError(
            NAME, "invalid or missing API key, or billing not enabled"
        )
    raise ProviderTransientError(NAME, f"HTTP {status}")
