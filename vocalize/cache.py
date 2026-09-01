"""The on-disk audio cache: one key function, and read/write around it.

Lifted out of tts.py so every provider shares one cache rather than the
ElevenLabs path owning it. The key formula for ElevenLabs is frozen —
entries written by earlier versions must keep hitting — so anything a
new provider adds is appended after the original payload, never woven
into it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .config import Settings

DEFAULT_PROVIDER = "elevenlabs"


def cache_key(text: str, settings: Settings) -> str:
    """A stable hash of everything that changes the audio."""
    payload = f"{settings.voice_id}|{settings.model_id}|{settings.output_format}|{text}"
    # Appended only when speed is set, so an unset speed hashes exactly as
    # it did before speed existed and older cache entries still hit.
    if settings.speed is not None:
        payload += f"|{settings.speed}"

    provider = getattr(settings, "provider", DEFAULT_PROVIDER)
    if provider != DEFAULT_PROVIDER:
        payload += f"|provider={provider}"
        if settings.language:
            payload += f"|lang={settings.language}"
        if settings.region:
            payload += f"|region={settings.region}"

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cache_path(text: str, settings: Settings, cache_dir: Path, ext: str) -> Path:
    return cache_dir / f"{cache_key(text, settings)}.{ext}"


def get(text: str, settings: Settings, cache_dir: Path | None, ext: str) -> bytes | None:
    """The cached audio, or None. Never raises: an unreadable entry is a miss."""
    if cache_dir is None:
        return None
    path = cache_path(text, settings, cache_dir, ext)
    try:
        if path.exists():
            return path.read_bytes()
    except OSError:
        pass
    return None


def put(text: str, settings: Settings, audio: bytes, cache_dir: Path | None, ext: str) -> None:
    """Store the audio. Never raises: a read-only cache dir is not a failure.

    Discarding a paid API response because the cache could not be written
    would be the worse bug — the caller's own save path does the real
    persistence.
    """
    if cache_dir is None:
        return
    path = cache_path(text, settings, cache_dir, ext)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(audio)
    except OSError:
        pass
