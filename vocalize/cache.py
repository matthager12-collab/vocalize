"""Private atomic audio cache shared by local speech engines."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path

from .config import Settings

MAX_CACHE_BYTES = 256 * 1024 * 1024


def cache_key(text: str, settings: Settings) -> str:
    """A stable hash of everything that changes the audio."""
    payload = f"{settings.voice_id}|{settings.model_id}|{settings.output_format}|{text}"
    # Appended only when speed is set, so an unset speed hashes exactly as
    # it did before speed existed and older cache entries still hit.
    if settings.speed is not None:
        payload += f"|{settings.speed}"

    payload += f"|provider={settings.provider}"
    if settings.language:
        payload += f"|lang={settings.language}"

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cache_path(text: str, settings: Settings, cache_dir: Path, ext: str) -> Path:
    if ext not in {"wav", "mp3", "m4a"}:
        raise ValueError("Unsupported cache audio extension")
    return cache_dir / f"{cache_key(text, settings)}.{ext}"


def get(text: str, settings: Settings, cache_dir: Path | None, ext: str) -> bytes | None:
    """The cached audio, or None. Never raises: an unreadable entry is a miss."""
    if cache_dir is None:
        return None
    path = cache_path(text, settings, cache_dir, ext)
    try:
        _private_cache(cache_dir, create=False)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.getuid() or info.st_mode & 0o077
                    or info.st_size > MAX_CACHE_BYTES):
                return None
            data = stream.read(MAX_CACHE_BYTES + 1)
            return data if len(data) <= MAX_CACHE_BYTES else None
    except OSError:
        pass
    return None


def put(text: str, settings: Settings, audio: bytes, cache_dir: Path | None, ext: str) -> None:
    """Store the audio. Never raises: a read-only cache dir is not a failure.

    The caller's own save path does the durable user-requested persistence.
    """
    if cache_dir is None or len(audio) > MAX_CACHE_BYTES:
        return
    path = cache_path(text, settings, cache_dir, ext)
    temporary = None
    try:
        _private_cache(cache_dir, create=True)
        if path.exists() or path.is_symlink():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                return
        fd, temporary = tempfile.mkstemp(prefix=".audio-", dir=cache_dir)
        with os.fdopen(fd, "wb") as stream:
            stream.write(audio)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        pass
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _private_cache(path: Path, *, create: bool) -> None:
    """Refuse redirected caches; tighten only this user's cache directory."""
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise OSError("Unsafe cache directory")
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise OSError("Unsafe cache directory")
    if create:
        path.chmod(0o700)
    elif info.st_mode & 0o077:
        raise OSError("Cache is not private")
