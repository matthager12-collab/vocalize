"""Local setup guidance and atomic settings serialization shared by CLI and portal."""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from pathlib import Path

import click

from . import settings_registry
from .config import (
    config_path,
    load_config_file,
    resolve_chain,
    retired_notices,
    without_retired_settings,
)
from .exceptions import ConfigChangedError, ConfigError

_TOML_STRING_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _escape_toml_string(text: str) -> str:
    """Escape `text` for a TOML basic string — every control char, not just \\ and ".

    A raw newline, tab, or other C0/DEL byte inside an unescaped basic
    string makes the file unparseable (or parses fine but truncates the
    value at the control character); tomllib rejects a literal control
    byte outright. Named escapes where TOML defines one, \\uXXXX otherwise.
    """
    out = []
    for ch in text:
        escape = _TOML_STRING_ESCAPES.get(ch)
        if escape is not None:
            out.append(escape)
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def _toml_value(key: str, value) -> str:
    """Render one scalar, or a flat list of scalars, as TOML.

    A list of scalars (e.g. `chain = ["kokoro", "say"]`) renders
    element-by-element through this same function. Anything with a table
    or array nested inside it — and any bare table — is refused: str()
    would quietly turn it into a Python-repr string and destroy it on
    write, so this raises instead and points at hand-editing the file.
    """
    if isinstance(value, list):
        if any(isinstance(item, (dict, list)) for item in value):
            raise ConfigError(
                f"The config file has a list under {key!r} containing a table or "
                f"nested array. The wizard only manages flat keys, so it will not "
                f"rewrite this file — edit that file by hand."
            )
        return "[" + ", ".join(_toml_value(key, item) for item in value) + "]"
    if isinstance(value, dict):
        raise ConfigError(
            f"The config file has a table under {key!r}. The wizard only manages "
            f"flat keys, so it will not rewrite this file — edit that file by hand."
        )
    if isinstance(value, bool):  # bool before int — bool is an int subclass
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    text = _escape_toml_string(str(value))
    return f'"{text}"'


#: Keys whose value is a table, and which are therefore rendered as their
#: own `[section]` after every flat key rather than as `key = …`. Taken
#: from the settings registry, so a table a new setting lives in is always
#: written (the [notes] and [speech] tables were each once dropped).
_TABLE_KEYS = settings_registry.TABLES + tuple(settings_registry.TABLE_ALIASES)

#: Tables whose registered keys sit one table deeper (`providers.<name>.voice`):
#: each inner table is written as its own `[table.<name>]` block, last.
_NESTED_TABLES = tuple(
    table for table in settings_registry.TABLES
    if any(s.key.startswith(table + ".") and s.key.count(".") == 2
           for s in settings_registry.SETTINGS)
)

#: The order the flat tables have always been written in; a table the
#: registry adds later follows them.
_TABLE_ORDER = ("stt", "transcribe", "app", "speech")
_FLAT_TABLES = tuple(sorted(
    (table for table in settings_registry.TABLES if table not in _NESTED_TABLES),
    key=lambda table: _TABLE_ORDER.index(table) if table in _TABLE_ORDER else len(_TABLE_ORDER),
))


_BARE_KEY = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


class _Unrenderable(Exception):
    """A value in a table vocalize does not use that TOML text cannot copy."""


def _toml_key(key) -> str:
    """One key as TOML: bare when it can be, else a quoted string."""
    key = str(key)
    if key and all(ch in _BARE_KEY for ch in key):
        return key
    return f'"{_escape_toml_string(key)}"'


def _faithful_value(value) -> str:
    """Any value a table vocalize does not use can hold, rendered exactly.

    Strings, numbers, booleans, lists (nested too) and inline tables.
    Anything else, a date for one, raises _Unrenderable.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return f'"{_escape_toml_string(value)}"'
    if isinstance(value, list):
        return "[" + ", ".join(_faithful_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(
            f"{_toml_key(key)} = {_faithful_value(item)}" for key, item in value.items()
        ) + "}"
    raise _Unrenderable


def _faithful_table(header: str, table: dict) -> list[str]:
    """`[header]` and its keys, each nested table as its own block after it."""
    lines = ["", f"[{header}]"]
    nested = []
    for key, value in table.items():
        if isinstance(value, dict):
            nested.append((key, value))
        else:
            lines.append(f"{_toml_key(key)} = {_faithful_value(value)}")
    for key, value in nested:
        lines.extend(_faithful_table(f"{header}.{_toml_key(key)}", value))
    return lines


def _label(name) -> str:
    """A table name as a notice shows it: printable and short, never raw."""
    name = str(name)
    return name if name.isprintable() and len(name) <= 64 else repr(name[:64])


def _unknown_tables(data: dict) -> list[str]:
    """The top-level keys holding a table vocalize does not know."""
    return [
        key for key, value in data.items()
        if key not in _TABLE_KEYS
        and (isinstance(value, dict)
             or (isinstance(value, list) and any(isinstance(item, dict) for item in value)))
    ]


def _table_lines(header: str, table, what: str) -> list[str]:
    """One `[header]` block of flat keys, or a refusal to rewrite the file."""
    if not isinstance(table, dict):
        raise ConfigError(
            f"The config file has a non-table value under {what}. The wizard "
            f"only manages tables of flat keys — edit that file by hand."
        )
    return ["", f"[{header}]"] + [
        f"{key} = {_toml_value(key, value)}" for key, value in table.items()
    ]


def _render_config_text(data: dict) -> str:
    """Render `data` as TOML; see `_render` for what is kept and dropped."""
    return _render(data)[0]


def _render(data: dict) -> tuple[str, list[str], bool]:
    """(TOML text, notices, whether a table had to be dropped).

    Flat keys first, then each table, `[providers.*]` after those, and
    last any top-level table vocalize does not use.

    Pure — no filesystem access — so `_walk`'s pre-flight dry run can call
    it purely to raise early, and `_write_config` can call it for the text
    it actually writes; the two can never disagree.

    Flat keys first isn't a style choice: TOML parses a bare `key = value`
    that appears after a `[section]` header as belonging to that section,
    so root keys have to come before every table or a re-read would nest
    them somewhere they don't belong.

    Rendering `[stt]` is not optional either: without it `_toml_value` has
    no way to write a dict, so every writer of the file — `vocalize chain`,
    the wizard and the portal — refuses to rewrite any config carrying an
    `[stt]` table, which 0.10.0 ships. The table order here is fixed rather
    than the file's own, so a hand-arranged file with `[providers.*]` ahead
    of `[stt]` comes back the other way round: identical TOML, a cosmetic
    diff on the first rewrite.

    Retired settings are dropped here, the loader having already given
    their notices (DEC-005), and `[notes]` is written out under its new
    name, `[transcribe]`, with `[transcribe]` winning a key both set.

    A top-level table vocalize does not know (one from a later version,
    say) never blocks a save: it is written back as it was, with one notice
    naming it. One holding a value TOML text cannot copy exactly is left
    out instead, with a notice; the caller backs the old file up first.
    """
    data = without_retired_settings(data)
    if "notes" in data:
        notes = data.pop("notes")
        if not isinstance(notes, dict):
            raise ConfigError(
                "The config file has a non-table value under 'notes'. The wizard "
                "only manages tables of flat keys — edit that file by hand."
            )
        transcribe = data.get("transcribe", {})
        data["transcribe"] = {**notes, **transcribe} if isinstance(transcribe, dict) else transcribe

    path = config_path()
    notices: list[str] = []
    dropped = False
    unknown: list[str] = []
    for name in _unknown_tables(data):
        value = data.pop(name)
        try:
            if isinstance(value, dict):
                block = _faithful_table(_toml_key(name), value)
            else:
                block = [f"{_toml_key(name)} = {_faithful_value(value)}"]
        except _Unrenderable:
            dropped = True
            notices.append(
                f"vocalize: left out the table [{_label(name)}] when saving {path}: vocalize "
                "does not use it, and it holds a value this writer cannot copy exactly. "
                "The old file is kept as a backup."
            )
            continue
        if isinstance(value, dict):
            unknown.extend(block)
        else:
            # An array of tables is written inline under a plain key, so it
            # has to sit with the flat keys, ahead of every table.
            data[name] = _Rendered(block[0])
        notices.append(
            f"vocalize: kept the table [{_label(name)}] in {path} as it was: vocalize "
            "does not use it."
        )

    lines = [
        value.line if isinstance(value, _Rendered) else f"{key} = {_toml_value(key, value)}"
        for key, value in data.items()
        if key not in _TABLE_KEYS
    ]

    # Every table the registry knows is written here, so none can be
    # silently dropped by a rewrite (the [notes] table, 0.12.0).
    for section in _FLAT_TABLES:
        if section in data:
            lines.extend(_table_lines(section, data[section], repr(section)))

    for section in _NESTED_TABLES:
        tables = data.get(section)
        if tables is None:
            continue
        if not isinstance(tables, dict):
            raise ConfigError(
                f"The config file has a non-table value under {section!r}. The "
                f"wizard only manages [{section}.*] tables — edit that file by hand."
            )
        for name, table in tables.items():
            lines.extend(_table_lines(f"{section}.{name}", table, f"{section}.{name!r}"))

    lines.extend(unknown)
    text = "\n".join(lines) + ("\n" if lines else "")
    return text, notices, dropped


class _Rendered:
    """A root line already rendered (an array of tables vocalize does not use)."""

    __slots__ = ("line",)

    def __init__(self, line: str) -> None:
        self.line = line


#: The fingerprint of a file that was not there when it was read. A string
#: sentinel rather than None so it survives a JSON round trip to the portal
#: page and back (DEC-005).
ABSENT_CONFIG = "absent"

#: The one wording for a compare-and-swap refusal. Shared because the
#: portal's 409 body and the CLI's stderr line have to say the same thing.
CONFIG_CHANGED = "config changed on disk — reload"

#: Serialises the compare-and-swap for every writer inside this process.
#: The comparison and the rename are two steps with a gap between them, so
#: two threads holding one fingerprint both found the file unchanged, both
#: answered "written", and one of the two writes was lost — the portal's
#: own case, one page saving twice, no second process needed.
#:
#: It does not close the same race *across* processes — the portal saving
#: while `vocalize chain` runs in a terminal still has the gap. That is the
#: residual DEC-005 accepted knowingly when it took compare-and-swap over
#: option B's advisory lock file, and closing it would be reopening that
#: decision rather than fixing a defect.
_WRITE_LOCK = threading.Lock()


def _fingerprint(text: str, stat) -> dict:
    """The fingerprint of bytes we just wrote, from the fd we wrote them through.

    Never a fresh read of the file afterwards: another writer landing in
    between would hand the caller *its* fingerprint, and the caller's next
    write would then pass the compare-and-swap and clobber it. Built from
    what we wrote instead, this fails safe — a racing write makes the next
    write refuse rather than overwrite (DEC-005).
    """
    return {
        "mtime_ns": stat.st_mtime_ns,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def fingerprint_config(path: Path):
    """What `path` looked like when it was read: mtime_ns + sha256, or `"absent"`.

    Both halves, not either: mtime alone misses two writes inside one
    filesystem timestamp, and content alone would call a restored backup
    no change at all. The stat comes off the open file descriptor, so the
    two halves can never end up describing two different files.

    Take this *before* the parse it will be compared against, never after.
    A fingerprint taken after the read calls a write that landed in between
    "unchanged" — which is the lost update the whole mechanism exists to
    refuse.
    """
    try:
        with path.open("rb") as fh:
            data = fh.read()
            stat = os.fstat(fh.fileno())
    except FileNotFoundError:
        return ABSENT_CONFIG
    except OSError as exc:
        raise ConfigError(f"Could not read config file {path}: {exc}") from exc
    return {"mtime_ns": stat.st_mtime_ns, "sha256": hashlib.sha256(data).hexdigest()}


class ConfigWrite(tuple):
    """What a save returns: `(text, fingerprint)`, so `text, fp = …` still
    unpacks, plus:

    - `.backup`: the `config.toml.bak` this save made, or None. It is made
      when the file it replaced held a comment, or when this save dropped
      a retired setting or a table it could not copy. A caller shows a
      message when it is set; the rewrite itself keeps no comments.
    - `.backup_has_retired`: True when that backup is a copy of a file that
      held a retired setting. The backup is byte for byte, so it still
      holds whatever that setting held (an old service key, say), and the
      caller should say so.
    - `.notices`: one line per top-level table vocalize does not use, kept
      or left out. Never a value.
    """

    backup: Path | None
    backup_has_retired: bool
    notices: tuple

    def __new__(cls, text: str, fingerprint: dict, backup: Path | None = None,
                backup_has_retired: bool = False, notices=()):
        result = super().__new__(cls, (text, fingerprint))
        result.backup = backup
        result.backup_has_retired = bool(backup is not None and backup_has_retired)
        result.notices = tuple(notices)
        return result


def backup_path(path: Path) -> Path:
    """Where the one comment-keeping backup of `path` goes: `config.toml.bak`."""
    return path.with_name(path.name + ".bak")


def _has_comment(data: bytes) -> bool:
    """Whether the file holds a `#` outside a quoted string: a whole-line
    comment or one after a value."""
    text = data.decode("utf-8", errors="replace")
    i, n = 0, len(text)
    while i < n:
        for quote in ('"""', "'''", '"', "'"):
            if text.startswith(quote, i):
                break
        else:
            quote = None
        if quote is None:
            if text[i] == "#":
                return True
            i += 1
            continue
        i += len(quote)
        escapes = quote[0] == '"'
        while i < n and not text.startswith(quote, i):
            if len(quote) == 1 and text[i] == "\n":
                break  # an unterminated one-line string; the parser refused it
            i += 2 if escapes and text[i] == "\\" else 1
        i += len(quote)
    return False


def _held_retired(data: bytes) -> bool:
    """Whether the old file held a setting the writer drops as retired."""
    try:
        import tomllib
    except ImportError:  # Python 3.10
        import tomli as tomllib  # type: ignore
    try:
        parsed = tomllib.loads(data.decode("utf-8"))
        return bool(retired_notices(parsed))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ConfigError):
        return False


def _backup_before_dropping_comments(path: Path, dropping: bool = False) -> tuple[Path | None, bool]:
    """Copy `path` to `config.toml.bak` (0600) when the save loses something.

    The writer re-renders from parsed data, so comments do not survive a
    save, and retired settings and tables it cannot copy are dropped; this
    keeps the file as the user wrote it. Made when the file holds a comment,
    holds a retired setting, or `dropping` says this save leaves something
    else out. Only the first time: an existing backup is never replaced
    (O_EXCL), so it stays the hand-written original. A backup that cannot
    be made stops the save.

    Returns (the backup made or None, whether the old file held a retired
    setting).
    """
    try:
        with path.open("rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        return None, False
    except OSError as exc:
        raise ConfigError(f"Could not read config file {path}: {exc}") from exc
    retired = _held_retired(data)
    if not (_has_comment(data) or retired or dropping):
        return None, retired

    backup = backup_path(path)
    try:
        fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        return None, retired
    except OSError as exc:
        raise ConfigError(
            f"Could not back up {path} to {backup} before saving: {exc}. Nothing was written."
        ) from exc
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        backup.unlink(missing_ok=True)
        raise ConfigError(
            f"Could not back up {path} to {backup} before saving: {exc}. Nothing was written."
        ) from exc
    return backup, retired


def write_config_if_unchanged(path: Path, data: dict, fingerprint) -> ConfigWrite:
    """Write `data` only if `path` still matches `fingerprint`. (DEC-005)

    Returns `(text, fingerprint of what was written)`, so a caller that
    goes on writing — the portal page — never has to stat the file back.
    The result's `.backup` names the `config.toml.bak` made first when the
    save loses comments or settings (see `_backup_before_dropping_comments`),
    `.backup_has_retired` says that copy holds a retired setting, and
    `.notices` names every top-level table vocalize does not use.

    An `"absent"` fingerprint creates the file with `O_EXCL`: the check and
    the create are one atomic operation, which is the one case where the
    stat-then-write race closes completely. On the ordinary path the
    comparison and the rename are two steps, so `_WRITE_LOCK` holds them
    together for every writer in this process; across processes the gap
    stays open, and that residual is what DEC-005 accepted when it took
    compare-and-swap over a lock file.
    """
    text, notices, _ = _render(data)  # raise before touching the file at all

    with _WRITE_LOCK:
        if fingerprint == ABSENT_CONFIG:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                raise ConfigChangedError(CONFIG_CHANGED) from None
            except OSError as exc:
                raise ConfigError(f"Could not write config file {path}: {exc}") from exc
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(text)
                    fh.flush()
                    stat = os.fstat(fh.fileno())
            except OSError as exc:
                raise ConfigError(f"Could not write config file {path}: {exc}") from exc
            return ConfigWrite(text, _fingerprint(text, stat), notices=notices)

        if fingerprint_config(path) != fingerprint:
            raise ConfigChangedError(CONFIG_CHANGED)
        return _write_config(path, data)


def _write_config(path: Path, data: dict) -> ConfigWrite:
    text, notices, dropped = _render(data)
    backup, retired = _backup_before_dropping_comments(path, dropping=dropped)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # A name of our own, in the config's own directory: still one
        # filesystem, so the rename is still atomic. A fixed
        # `config.toml.tmp` was one name shared by every writer in the
        # project — the portal, the wizard and `vocalize chain` — and two
        # at once truncated each other's render, then renamed whatever was
        # left into place. mkstemp creates at 0600, which is the mode this
        # file wants anyway (os.replace swaps the inode, so a config
        # created at 0600 would otherwise widen to the umask default on
        # its first ordinary rewrite).
        fd, name = tempfile.mkstemp(
            dir=path.parent, prefix=path.name + ".", suffix=".tmp"
        )
    except OSError as exc:
        raise ConfigError(f"Could not write config file {path}: {exc}") from exc

    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            # Stat the descriptor we wrote through, before the rename —
            # os.replace keeps the mtime, so this describes the bytes we
            # wrote, where a stat of `path` afterwards would describe
            # whoever wrote last.
            stat = os.fstat(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        # A unique name no longer cleans itself up by being reused.
        # ponytail: any death between the two — SIGKILL, Ctrl-C, a power cut —
        # still leaves one 0600 temp
        # file behind, where the shared name left at most one ever; sweep
        # `config.toml.*.tmp` on startup if they ever pile up.
        tmp.unlink(missing_ok=True)
        raise ConfigError(f"Could not write config file {path}: {exc}") from exc
    return ConfigWrite(text, _fingerprint(text, stat), backup, retired, notices)


def run_wizard() -> None:
    """Show local setup without opening a browser, probing hardware or changing settings."""
    chain = resolve_chain(None, load_config_file())
    click.echo("Local speech engines: " + ", ".join(chain))
    click.echo(f"Settings file: {config_path()}")
    click.echo("Edit voices and dictation settings: vocalize portal --no-browser")
    click.echo("Choose local engines: vocalize chain kokoro piper say")
    click.echo("Check installed models: vocalize local status")
    click.echo("Speech and text processing stay on this machine. No API keys are needed.")
