"""OpenAI's `/v1/audio/speech`: a plain REST call over `_http`.

Error triage reads the JSON body OpenAI sends back rather than trusting
the status code alone — a 429 means two different things depending on
`error.code`/`error.type`, and only one of them (out of credit) should be
remembered as exhausted for the month.
"""

from __future__ import annotations

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

NAME = "openai"
AUDIO_EXT = "mp3"
# OpenAI's own docs don't publish a hard input cap; this keeps one request
# comfortably inside typical latency/size limits.
MAX_CHARS = 4000
DEFAULTS = {"voice": "marin", "model": "gpt-4o-mini-tts"}

VOICES = (
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "fable",
    "onyx",
    "nova",
    "sage",
    "shimmer",
    "verse",
    "marin",
    "cedar",
)

# 429s that mean "out of money", not "slow down".
_QUOTA_CODES = {
    "credit_balance_exhausted",
    "organization_spend_limit_exceeded",
    "project_spend_limit_exceeded",
}


def _parse_error(body: bytes) -> dict:
    """{"code", "type", "message"} from OpenAI's error body, or {} if odd."""
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
    code = error.get("code")
    etype = error.get("type")
    message = error.get("message")

    if status == 401:
        return ProviderAuthError(NAME, "invalid or missing API key")
    if status == 429:
        if code in _QUOTA_CODES or etype == "insufficient_quota":
            return ProviderQuotaError(NAME, "out of credit")
        return ProviderTransientError(NAME, f"HTTP {status}")
    if status >= 500:
        return ProviderTransientError(NAME, f"HTTP {status}")
    if status in (400, 404, 422):
        text = message if isinstance(message, str) and message else f"HTTP {status}"
        return ProviderContentError(NAME, auth.scrub(text, key))
    return ProviderTransientError(NAME, f"HTTP {status}")


def check(settings: Settings | None = None) -> None:
    require_key(NAME)


def synthesize(text: str, settings: Settings) -> bytes:
    key = require_key(NAME)

    body: dict = {
        "model": settings.model_id,
        "input": text,
        "voice": settings.voice_id,
        "response_format": "mp3",
    }
    if settings.speed is not None:
        body["speed"] = settings.speed

    status, response_body = _http.request(
        "POST",
        "https://api.openai.com/v1/audio/speech",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        body=json.dumps(body).encode("utf-8"),
        provider=NAME,
    )

    if status == 200:
        if not response_body:
            raise ProviderTransientError(NAME, "returned no audio")
        return response_body

    raise _classify(status, response_body, key)


def list_voices() -> list[dict]:
    """Static: OpenAI has no endpoint that lists TTS voices."""
    return [{"id": v, "name": v} for v in VOICES]


def validate(key: str) -> None:
    """Cheapest authenticated call that proves the key works.

    The body is never read: a bearer-scoped models list is not part of
    what an auth check needs, and not reading it is one fewer place a
    stray key echo could reach stdout.
    """
    status, _ = _http.request(
        "GET",
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {key}"},
        provider=NAME,
    )
    if status == 200:
        return
    if status == 401:
        raise ProviderAuthError(NAME, "invalid or missing API key")
    raise ProviderTransientError(NAME, f"HTTP {status}")
