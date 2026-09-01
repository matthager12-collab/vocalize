"""The ElevenLabs provider: a thin shell over tts.py, plus error triage.

tts.py stays the ElevenLabs client wrapper it always was. What is new here
is `classify`, which turns the SDK's one ApiError into the typed error the
chain reads: skip, mark exhausted, or stop.
"""

from __future__ import annotations

from .. import auth, tts
from ..config import DEFAULT_MODEL, DEFAULT_VOICE, Settings
from ..exceptions import (
    ProviderAuthError,
    ProviderContentError,
    ProviderError,
    ProviderQuotaError,
    ProviderTransientError,
)
from . import require_key

NAME = "elevenlabs"
AUDIO_EXT = "mp3"
# The API's own per-request cap is 10,000 characters on the multilingual
# models; the margin covers the SSML-ish markup a chunk can carry.
MAX_CHARS = 9500
DEFAULTS = {"voice": DEFAULT_VOICE, "model": DEFAULT_MODEL}

_QUOTA_STATUSES = {"quota_exceeded", "insufficient_credits", "payment_required"}
_AUTH_STATUSES = {"invalid_api_key", "needs_authorization"}
_TRANSIENT_STATUSES = {"rate_limit_exceeded", "concurrent_limit_exceeded"}

# Our own bugs. Dressing a TypeError up as an API failure would send the
# chain to the next provider and hide it — these stay loud.
_OUR_BUGS = (TypeError, AttributeError, KeyError)


def _detail_status(body) -> str | None:
    """The `detail.status` string ElevenLabs puts in its error bodies."""
    if not isinstance(body, dict):
        return None
    detail = body.get("detail")
    if isinstance(detail, dict):
        status = detail.get("status")
        return status if isinstance(status, str) else None
    return None


def classify(exc: Exception) -> ProviderError:
    """Map an SDK exception to the typed error the chain acts on.

    Raises (rather than returns) for TypeError/AttributeError/KeyError:
    those are ours, not the API's, and must not be swallowed as a
    "try the next provider" signal.
    """
    if isinstance(exc, _OUR_BUGS):
        raise exc
    if isinstance(exc, ProviderError):
        return exc

    code = getattr(exc, "status_code", None)
    if not isinstance(code, int):
        code = None
    status = _detail_status(getattr(exc, "body", None))

    if status in _QUOTA_STATUSES or code == 402:
        return ProviderQuotaError(NAME, "out of credit")
    if code in (401, 403) or status in _AUTH_STATUSES:
        return ProviderAuthError(NAME, "invalid or missing API key")
    if status == "max_character_limit_exceeded":
        return ProviderContentError(NAME, "text is longer than the API accepts")
    if code == 429 or status in _TRANSIENT_STATUSES or (code is not None and code >= 500):
        return ProviderTransientError(NAME, f"temporarily unavailable ({status or code})")
    if code is not None and 400 <= code < 500:
        return ProviderContentError(NAME, f"request rejected ({status or code})")

    # No status code and no recognizable body: an SDK or transport problem
    # rather than a verdict from the API. Its own text is all we know.
    return ProviderTransientError(NAME, str(exc) or exc.__class__.__name__)


def _scrubbed(exc: ProviderError, key: str) -> ProviderError:
    """The same error with the API key taken out of its message.

    classify's last branch quotes the SDK's own text, and an SDK that
    echoes a rejected header value would otherwise carry the key into
    stderr and into the chain's all-failed summary.
    """
    message = auth.scrub(str(exc), key).removeprefix(f"{NAME}: ")
    return type(exc)(NAME, message)


def check(settings: Settings | None = None, api_key: str | None = None) -> None:
    """`api_key` is --api-key: ElevenLabs is the only provider that has one."""
    require_key(NAME, api_key)


def _client(api_key: str | None = None):
    return tts.build_client(require_key(NAME, api_key))


def synthesize(text: str, settings: Settings, api_key: str | None = None) -> bytes:
    # cache_dir=None: the chain owns the cache, so it can key on the
    # provider that actually produced the audio.
    key = require_key(NAME, api_key)
    try:
        return tts.synthesize(tts.build_client(key), text, settings, cache_dir=None)
    except ProviderError as exc:
        raise _scrubbed(exc, key) from exc


def list_voices() -> list[dict]:
    return tts.list_voices(_client())


def validate(key: str) -> None:
    """Cheapest call that proves the key works."""
    try:
        tts.list_voices(tts.build_client(key))
    except Exception as exc:
        # list_voices wraps the SDK error; the cause still carries the
        # status code that tells auth failure from a passing wobble.
        raise _scrubbed(classify(exc.__cause__ or exc), key) from exc


def usage() -> dict | None:
    return tts.get_usage(_client())
