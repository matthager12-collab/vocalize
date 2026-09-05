"""Thin wrapper around the ElevenLabs text-to-speech API.

Kept deliberately small and dependency-injected (the client is passed
in) so it's easy to unit test with a fake/mock client — no network
access or real API key needed to test the logic in this file.
"""

from __future__ import annotations

from pathlib import Path

from . import cache
from .config import Settings
from .exceptions import ProviderTransientError, TTSRequestError

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "vocalize"

# The SDK's own default is 240s, far beyond the Stop hook's 60s subprocess
# timeout — a hung request would otherwise outlive the hook that spawned it.
REQUEST_TIMEOUT_SECONDS = 30


# The cache moved to cache.py; this alias keeps the old import path working.
_cache_key = cache.cache_key


def synthesize(
    client,
    text: str,
    settings: Settings,
    *,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
) -> bytes:
    """Convert `text` to audio bytes via the ElevenLabs client.

    `client` is expected to expose `.text_to_speech.convert(...)`
    returning an iterable of bytes chunks (this matches the official
    `elevenlabs` SDK's ElevenLabs client) — but any object with that
    shape works, which is what makes this testable with a stub.

    Results are cached on disk by a hash of (text, voice, model,
    format, speed), so re-running the same request — e.g. re-reading
    the same document twice — doesn't burn API quota twice.
    `cache_dir=None` turns caching off, for callers that own it
    themselves.
    """
    if not text.strip():
        raise TTSRequestError("Nothing to speak: input text is empty.")

    cached = cache.get(text, settings, cache_dir, "mp3")
    if cached is not None:
        return cached

    convert_kwargs = {}
    if settings.speed is not None:
        # Imported here, not at module level, for the same reason as in
        # build_client: the default path stays usable without the SDK.
        from elevenlabs import VoiceSettings

        convert_kwargs["voice_settings"] = VoiceSettings(speed=settings.speed)

    try:
        chunks = client.text_to_speech.convert(
            text=text,
            voice_id=settings.voice_id,
            model_id=settings.model_id,
            output_format=settings.output_format,
            **convert_kwargs,
        )
        audio = b"".join(chunks) if not isinstance(chunks, (bytes, bytearray)) else bytes(chunks)
    except Exception as exc:
        # Imported here, not at module level: providers.elevenlabs imports
        # this module. classify re-raises our own bugs (TypeError and
        # friends) untouched rather than dressing them as API failures.
        from .providers.elevenlabs import classify

        raise classify(exc) from exc

    if not audio:
        # Transient, not bare: an empty 200 is a wobble the next provider
        # in the chain can cover, and a bare TTSRequestError aborts the run.
        raise ProviderTransientError("elevenlabs", "returned no audio")

    cache.put(text, settings, audio, cache_dir, "mp3")

    return audio


#: `voices.search` pages at ten by default and at most 100; the API says
#: `has_more` and hands back `next_page_token`. Walked to the end, with a
#: ceiling so an API that always says "more" cannot spin the CLI or the
#: portal: 20 pages of 100 is 2,000 voices, far past any account.
VOICE_PAGE_SIZE = 100
MAX_VOICE_PAGES = 20


def list_voices(client) -> list[dict]:
    """Return a simplified [{"id": ..., "name": ...}, ...] list of voices."""
    voices: list = []
    token = None
    try:
        for _page in range(MAX_VOICE_PAGES):
            response = client.voices.search(page_size=VOICE_PAGE_SIZE, next_page_token=token)
            voices.extend(getattr(response, "voices", response))
            token = getattr(response, "next_page_token", None)
            if not getattr(response, "has_more", False) or not token:
                break
    except Exception as exc:
        raise TTSRequestError(f"Could not list voices: {exc}") from exc

    return [
        {"id": getattr(v, "voice_id", getattr(v, "id", None)), "name": getattr(v, "name", "?")}
        for v in voices
    ]


def get_usage(client) -> dict:
    """Return ElevenLabs subscription usage: tier, used, limit, resets_at.

    `resets_at` is the unix timestamp of the next character-count reset,
    or None on tiers that don't report one. This call costs no quota.
    """
    try:
        subscription = client.user.subscription.get()
    except Exception as exc:
        raise TTSRequestError(f"Could not fetch usage: {exc}") from exc

    return {
        "tier": subscription.tier,
        "used": subscription.character_count,
        "limit": subscription.character_limit,
        "resets_at": subscription.next_character_count_reset_unix,
    }


def build_client(api_key: str):
    """Construct the real ElevenLabs SDK client. Imported lazily so the
    rest of the package (and its tests) don't require the `elevenlabs`
    package to be installed just to run unit tests against pure logic.
    """
    from elevenlabs.client import ElevenLabs

    # follow_redirects=False: the SDK's httpx client otherwise follows a 3xx
    # to whatever `Location` names — another origin, plain http — and
    # re-sends `xi-api-key` there. Every endpoint is a fixed URL, so a
    # redirect is never legitimate; refused, it surfaces as an ApiError.
    # The same rule `providers/_http._NoRedirects` keeps for urllib.
    return ElevenLabs(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=False)
