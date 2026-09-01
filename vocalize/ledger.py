"""Local monthly usage tracking per provider — the reactive half of budgets.

Cloud providers don't stop billing at a free tier, so `chain.run` checks
this ledger before spending on a paid provider and records every
successful chunk after. A provider that comes back with a quota error
gets `mark_exhausted` so the rest of the chain — and the rest of the
calendar month — skips straight past it without another network call.

Reading and writing never raise. Losing the count for one request is a
rounding error; refusing to speak because a JSON file is corrupt or a
directory is read-only would be the actual bug.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# tts.py imports cache/config/exceptions only, never this module, so this is
# a plain one-way import — no cycle to guard against.
from .tts import DEFAULT_CACHE_DIR

LEDGER_NAME = "usage.json"


def _short_reason(exc: BaseException) -> str:
    """One line, fit for a stderr warning rather than a traceback."""
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__


def path(cache_dir: Path | None = None) -> Path:
    """Where the ledger lives, or would live."""
    return (cache_dir if cache_dir is not None else DEFAULT_CACHE_DIR) / LEDGER_NAME


def load(cache_dir: Path | None = None) -> dict:
    """The ledger's parsed content, or `{}` for missing/corrupt/wrong-shape.

    A missing file is the normal first-run state and stays silent. Anything
    else unreadable — bad permissions, broken JSON, an unexpected top-level
    shape — gets one stderr line and the same empty result, so a caller
    never has to tell "nothing yet" apart from "couldn't read it".
    """
    p = path(cache_dir)
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        _warn_unreadable(_short_reason(exc))
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        _warn_unreadable(_short_reason(exc))
        return {}

    if not isinstance(data, dict) or not isinstance(data.get("months"), dict):
        _warn_unreadable("unexpected shape")
        return {}
    return data


def _warn_unreadable(reason: str) -> None:
    print(
        f"vocalize: usage ledger unreadable ({reason}); starting fresh",
        file=sys.stderr,
    )


def _now() -> datetime:
    # tz-aware-then-local, not a naive datetime.now(): same wall-clock
    # value, but satisfies the "don't call now() without a tz" lint rule.
    return datetime.now(timezone.utc).astimezone()


def month_key(now: datetime | None = None) -> str:
    """Local-time `YYYY-MM` for `now` (or the current moment)."""
    return (now or _now()).strftime("%Y-%m")


def _previous_month_key(now: datetime | None = None) -> str:
    dt = now or _now()
    year, month = dt.year, dt.month
    if month == 1:
        year, month = year - 1, 12
    else:
        month -= 1
    return f"{year:04d}-{month:02d}"


def status(
    provider: str, cache_dir: Path | None = None, now: datetime | None = None
) -> tuple[int, bool]:
    """(chars spoken this month, exhausted) for `provider`. Zeros when unseen."""
    data = load(cache_dir)
    entry = data.get("months", {}).get(month_key(now), {}).get(provider, {})
    return entry.get("chars", 0), entry.get("exhausted", False)


def all_status(cache_dir: Path | None = None, now: datetime | None = None) -> dict:
    """{provider: {"chars": int, "exhausted": bool}} for the current month only."""
    data = load(cache_dir)
    month = data.get("months", {}).get(month_key(now), {})
    return {
        provider: {"chars": v.get("chars", 0), "exhausted": v.get("exhausted", False)}
        for provider, v in month.items()
    }


def _prune(months: dict, now: datetime | None) -> None:
    """Drop every month but the current one and the one before it."""
    keep = {month_key(now), _previous_month_key(now)}
    for key in list(months):
        if key not in keep:
            del months[key]


def _month_entry(data: dict, provider: str, now: datetime | None) -> dict:
    """The mutable {"chars", "exhausted"} dict for `provider` this month.

    Mutates `data` in place to create the version/months/month/provider
    scaffolding as needed — callers save `data` right after.
    """
    data.setdefault("version", 1)
    months = data.setdefault("months", {})
    month = months.setdefault(month_key(now), {})
    return month.setdefault(provider, {"chars": 0, "exhausted": False})


def record(
    provider: str,
    chars: int,
    cache_dir: Path | None = None,
    now: datetime | None = None,
) -> None:
    """Add `chars` to `provider`'s count for the current month."""
    if chars < 0:
        raise ValueError(f"chars must not be negative, got {chars}")

    data = load(cache_dir)
    entry = _month_entry(data, provider, now)
    entry["chars"] = entry.get("chars", 0) + chars
    _prune(data["months"], now)
    _save(data, cache_dir)


def mark_exhausted(
    provider: str, cache_dir: Path | None = None, now: datetime | None = None
) -> None:
    """Flag `provider` as over budget for the rest of the calendar month."""
    data = load(cache_dir)
    entry = _month_entry(data, provider, now)
    entry["exhausted"] = True
    _prune(data["months"], now)
    _save(data, cache_dir)


def _save(data: dict, cache_dir: Path | None) -> None:
    """Atomic write: tmp file, 0600, `os.replace`. Never raises.

    # ponytail: read-modify-write, last writer wins; two concurrent runs
    # can lose one request's count. Upgrade path: fcntl.flock.
    """
    p = path(cache_dir)
    tmp = p.with_name(p.name + ".tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.chmod(0o600)
        os.replace(tmp, p)
    except OSError as exc:
        print(
            f"vocalize: budget tracking unavailable ({_short_reason(exc)})",
            file=sys.stderr,
        )
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
