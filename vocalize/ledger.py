"""Local synthesis counts. Historical hosted records are not used or deleted.

Counts are informational, never authorization or spending controls. Files are
bounded private JSON, read without following symlinks and replaced atomically.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .auth import PROVIDER_NAMES
from .exceptions import ConfigError
from .tts import DEFAULT_CACHE_DIR

LEDGER_NAME = "usage.json"
_MAX_BYTES = 1024 * 1024


def path(cache_dir: Path | None = None) -> Path:
    return (cache_dir if cache_dir is not None else DEFAULT_CACHE_DIR) / LEDGER_NAME


def _provider(provider):
    if provider not in PROVIDER_NAMES:
        raise ConfigError("Usage is available only for local providers")


def _warn_unreadable():
    print("vocalize: local usage ledger unavailable", file=sys.stderr)


def load(cache_dir: Path | None = None) -> dict:
    try:
        target = path(cache_dir)
        if any(p.is_symlink() for p in (target, *target.parents)):
            raise ValueError
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.getuid() or info.st_mode & 0o077
                    or info.st_size > _MAX_BYTES):
                raise ValueError
            data = json.loads(stream.read(_MAX_BYTES + 1))
        if not isinstance(data, dict) or not isinstance(data.get("months"), dict):
            raise TypeError
        for month, providers in data["months"].items():
            if not isinstance(month, str) or not isinstance(providers, dict):
                raise TypeError
            for entry in providers.values():
                if not isinstance(entry, dict) or type(entry.get("chars", 0)) is not int or entry.get("chars", 0) < 0:
                    raise ValueError
        return data
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError, RecursionError):
        _warn_unreadable()
        return {}


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def month_key(now: datetime | None = None) -> str:
    return (now or _now()).strftime("%Y-%m")


def status(provider: str, cache_dir: Path | None = None, now: datetime | None = None) -> tuple[int, bool]:
    """Local synthesized characters; exhaustion is always false."""
    _provider(provider)
    entry = load(cache_dir).get("months", {}).get(month_key(now), {}).get(provider, {})
    return entry.get("chars", 0), False


def all_status(cache_dir: Path | None = None, now: datetime | None = None) -> dict:
    month = load(cache_dir).get("months", {}).get(month_key(now), {})
    return {name: {"chars": entry.get("chars", 0), "exhausted": False}
            for name, entry in month.items() if name in PROVIDER_NAMES}


def record(provider: str, chars: int, cache_dir: Path | None = None, now: datetime | None = None) -> None:
    _provider(provider)
    if type(chars) is not int or chars < 0:
        raise ValueError("Character count must be a nonnegative integer")
    data = load(cache_dir)
    if not data and path(cache_dir).exists():
        return  # preserve corrupt/legacy bytes for explicit user disposition
    data.setdefault("version", 1)
    month = data.setdefault("months", {}).setdefault(month_key(now), {})
    entry = month.setdefault(provider, {"chars": 0})
    entry["chars"] += chars
    _save(data, cache_dir)


def _save(data, cache_dir):
    # Informational counts remain last-writer-wins; capture authorization never
    # relies on this ledger. No automatic pruning of historical hosted records.
    target = path(cache_dir)
    temporary = None
    try:
        if any(p.is_symlink() for p in (target, *target.parents)):
            raise ValueError
        if target.exists() and (not target.is_file() or target.stat().st_nlink != 1):
            raise ValueError
        payload = json.dumps(data).encode()
        if len(payload) > _MAX_BYTES:
            raise ValueError
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=".usage-", dir=target.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except (OSError, ValueError):
        _warn_unreadable()
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass
