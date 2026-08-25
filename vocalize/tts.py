"""Thin wrapper around the ElevenLabs text-to-speech API.

Kept deliberately small and dependency-injected (the client is passed
in) so it's easy to unit test with a fake/mock client — no network
access or real API key needed to test the logic in this file.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .config import Settings
from .exceptions import TTSRequestError

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "vocalize"


def _cache_key(text: str, settings: Settings) -> str:
    payload = f"{settings.voice_id}|{settings.model_id}|{settings.output_format}|{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
    format), so re-running the same request — e.g. re-reading the
    same document twice — doesn't burn API quota twice.
    """
    if not text.strip():
        raise TTSRequestError("Nothing to speak: input text is empty.")

    cache_path = None
    if cache_dir is not None:
        key = _cache_key(text, settings)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{key}.mp3"
        if cache_path.exists():
            return cache_path.read_bytes()

    try:
        chunks = client.text_to_speech.convert(
            text=text,
            voice_id=settings.voice_id,
            model_id=settings.model_id,
            output_format=settings.output_format,
        )
        audio = b"".join(chunks) if not isinstance(chunks, (bytes, bytearray)) else bytes(chunks)
    except Exception as exc:  # noqa: BLE001 — surface any SDK error uniformly
        raise TTSRequestError(f"ElevenLabs API request failed: {exc}") from exc

    if not audio:
        raise TTSRequestError("ElevenLabs API returned no audio data.")

    if cache_path is not None:
        cache_path.write_bytes(audio)

    return audio


def list_voices(client) -> list[dict]:
    """Return a simplified [{"id": ..., "name": ...}, ...] list of voices."""
    try:
        response = client.voices.search()
    except Exception as exc:  # noqa: BLE001
        raise TTSRequestError(f"Could not list voices: {exc}") from exc

    voices = getattr(response, "voices", response)
    return [
        {"id": getattr(v, "voice_id", getattr(v, "id", None)), "name": getattr(v, "name", "?")}
        for v in voices
    ]


def build_client(api_key: str):
    """Construct the real ElevenLabs SDK client. Imported lazily so the
    rest of the package (and its tests) don't require the `elevenlabs`
    package to be installed just to run unit tests against pure logic.
    """
    from elevenlabs.client import ElevenLabs

    return ElevenLabs(api_key=api_key)
