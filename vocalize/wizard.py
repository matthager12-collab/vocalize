"""Local setup guidance and atomic settings serialization shared by CLI and portal."""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from pathlib import Path

import click

from .config import (
    config_path,
    load_config_file,
    resolve_chain,
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
#: own `[section]` after every flat key rather than as `key = …`.
_TABLE_KEYS = ("providers", "stt", "transcribe", "notes", "app", "speech")


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
    """Render `data` as TOML: flat keys first, then each table, `[providers.*]` last.

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

    lines = [
        f"{key} = {_toml_value(key, value)}"
        for key, value in data.items()
        if key not in _TABLE_KEYS
    ]

    if "stt" in data:
        lines.extend(_table_lines("stt", data["stt"], "'stt'"))

    # `_TABLE_KEYS` is an exclusion list for the flat pass, not a render
    # list: a table it names still has to be written out here by name, or
    # the next rewrite silently drops it (the [notes] table, 0.12.0).
    for section in ("transcribe", "app", "speech"):
        if section in data:
            lines.extend(_table_lines(section, data[section], repr(section)))

    providers = data.get("providers")
    if providers is not None:
        if not isinstance(providers, dict):
            raise ConfigError(
                "The config file has a non-table value under 'providers'. The "
                "wizard only manages [providers.*] tables — edit that file by hand."
            )
        for name, table in providers.items():
            lines.extend(_table_lines(f"providers.{name}", table, f"providers.{name!r}"))

    return "\n".join(lines) + ("\n" if lines else "")


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


def write_config_if_unchanged(path: Path, data: dict, fingerprint) -> tuple[str, dict]:
    """Write `data` only if `path` still matches `fingerprint`. (DEC-005)

    Returns `(text, fingerprint of what was written)`, so a caller that
    goes on writing — the portal page — never has to stat the file back.

    An `"absent"` fingerprint creates the file with `O_EXCL`: the check and
    the create are one atomic operation, which is the one case where the
    stat-then-write race closes completely. On the ordinary path the
    comparison and the rename are two steps, so `_WRITE_LOCK` holds them
    together for every writer in this process; across processes the gap
    stays open, and that residual is what DEC-005 accepted when it took
    compare-and-swap over a lock file.
    """
    text = _render_config_text(data)  # raise before touching the file at all

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
            return text, _fingerprint(text, stat)

        if fingerprint_config(path) != fingerprint:
            raise ConfigChangedError(CONFIG_CHANGED)
        return _write_config(path, data)


def _write_config(path: Path, data: dict) -> tuple[str, dict]:
    text = _render_config_text(data)
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
    return text, _fingerprint(text, stat)


def run_wizard() -> None:
    """Show local setup without opening a browser, probing hardware or changing settings."""
    chain = resolve_chain(None, load_config_file())
    click.echo("Local speech engines: " + ", ".join(chain))
    click.echo(f"Settings file: {config_path()}")
    click.echo("Edit voices and dictation settings: vocalize portal --no-browser")
    click.echo("Choose local engines: vocalize chain kokoro piper say")
    click.echo("Check installed models: vocalize local status")
    click.echo("Speech and text processing stay on this machine. No API keys are needed.")
