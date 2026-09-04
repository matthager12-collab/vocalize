"""The interactive `vocalize config` wizard.

Three steps — voice, model, speed — each a keyboard-driven list rendered
with click.echo and driven by click.getchar(). click is already a
dependency, so this needs no curses, prompt_toolkit, or anything else.

Every frame is painted on the controlling terminal rather than on
stdout. Wrappers that relay a child's output — `op run`, the documented
way this project injects its API key — leave sys.stdout non-tty while
the keyboard still works, which makes click.clear() a no-op and lets the
relay's writes land in the middle of a raw-mode read. Painting at
/dev/tty sidesteps both.

This module owns the *interaction* only. Every default, bound, and
validation rule still comes from vocalize.config, and the voice list and
previews go through the same tts/audio functions the rest of the CLI
uses — nothing here re-implements policy that already exists.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

import click

from .audio import play, save
from .auth import login, prompt_for_key, scrub
from .config import (
    DEFAULT_MODEL,
    SPEED_MAX,
    SPEED_MIN,
    Settings,
    config_path,
    load_config_file,
    resolve_api_key,
    resolve_settings,
    validate_speed,
)
from .exceptions import (
    ConfigChangedError,
    ConfigError,
    MissingAPIKeyError,
    VocalizeError,
)
from .tts import DEFAULT_CACHE_DIR, build_client, list_voices, synthesize

PREVIEW_TEXT = "This is how vocalize will sound."
PREVIEW_PATH = DEFAULT_CACHE_DIR / "preview.mp3"

MODEL_CHOICES = (
    (DEFAULT_MODEL, "default, quality"),
    ("eleven_flash_v2_5", "fast, cheaper"),
    ("eleven_turbo_v2_5", "low latency"),
)

HOTKEYS = "↑/↓ or k/j move · Enter select · m manual entry · q quit"
VOICE_HOTKEYS = "↑/↓ or k/j move · Enter select · p preview · m manual entry · q quit"

# On POSIX an arrow key arrives as a whole escape sequence: click.getchar()
# reads up to 32 bytes, so "\x1b[A" comes back in one call, not three. On
# Windows click returns a two-character "\x00"/"\xe0" prefixed code instead.
_UP = ("\x1b[A", "\x00H", "\xe0H", "k")
_DOWN = ("\x1b[B", "\x00P", "\xe0P", "j")
_ENTER = ("\r", "\n")
_QUIT = ("q", "\x1b")

# Lines of chrome around the list: title, two blanks, notes, legend.
_CHROME_LINES = 8

# Row values that mean "write nothing" rather than "write this value".
_KEEP = object()
_UNSET = object()

_NO_TERMINAL = (
    "The config wizard needs an interactive terminal. "
    "Run `vocalize config` in a terminal, not from a pipe or a script."
)


class _Cancelled(Exception):
    """Raised inside a step on q, Escape, or EOF."""


def _open_tty():
    """The controlling terminal as a writable stream, or None."""
    try:
        return open("/dev/tty", "w", encoding="utf-8")
    except OSError:
        return None


def _open_ui_stream():
    """Where to paint the wizard: (stream, did_we_open_it).

    Returns (None, False) when there's no terminal to paint on at all.
    """
    stdout = sys.stdout
    if stdout is not None and stdout.isatty():
        return stdout, False
    tty = _open_tty()
    return (tty, True) if tty is not None else (None, False)


def _clear(ui) -> None:
    # click.clear() only ever writes to stdout, and no-ops when stdout
    # isn't a tty — which is the whole case this wizard has to survive.
    ui.write("\x1b[2J\x1b[H")
    ui.flush()


def _ask(ui, label: str) -> str:
    """Read one typed line. The tty driver echoes the typing itself."""
    click.echo(f"{label}: ", file=ui, nl=False)
    try:
        return input()
    except (EOFError, KeyboardInterrupt):
        return ""


def _confirm(ui, label: str) -> bool:
    """Default no: an empty answer, or an EOF, must not write the file."""
    return _ask(ui, f"{label} [y/N]").strip().lower() in ("y", "yes")


def _render(ui, title: str, rows: list, cursor: int, legend: str, notes: list) -> None:
    _clear(ui)
    click.echo(title, file=ui)
    click.echo(file=ui)

    # A full ElevenLabs voice list is longer than an 80x24 terminal, so the
    # list is windowed onto the cursor rather than echoed whole — otherwise
    # the row you're moving scrolls off the top of the screen.
    height = max(1, shutil.get_terminal_size().lines - _CHROME_LINES)
    start = max(0, min(cursor - height // 2, len(rows) - height)) if len(rows) > height else 0
    end = min(start + height, len(rows))

    if start > 0:
        click.echo("  …", file=ui)
    for index in range(start, end):
        click.echo(f"{'>' if index == cursor else ' '} {rows[index][1]}", file=ui)
    if end < len(rows):
        click.echo("  …", file=ui)

    click.echo(file=ui)
    if notes:
        for note in notes:
            click.echo(note, file=ui)
        click.echo(file=ui)
    click.echo(legend, file=ui)


def _select(ui, title, rows, cursor, *, legend=HOTKEYS, notes=(), manual=None, preview=None):
    """Run one step of the wizard and return the chosen row value.

    `manual` and `preview` are callables for the m and p hotkeys; either
    may be None, which simply makes that key inert on this step.
    """
    notes = list(notes)
    status = None

    while True:
        _render(ui, title, rows, cursor, legend, notes + ([status] if status else []))
        key = click.getchar()

        # An empty read means EOF (a closed pty). It matches no key set, so
        # without this the redraw loop would spin at 100% CPU forever.
        if not key or key in _QUIT:
            raise _Cancelled
        if key in _UP:
            cursor = (cursor - 1) % len(rows)
        elif key in _DOWN:
            cursor = (cursor + 1) % len(rows)
        elif key in _ENTER:
            return rows[cursor][0]
        elif key == "m" and manual is not None:
            value = manual()
            if value is not None:
                return value
        elif key == "p" and preview is not None:
            # Preview reports back through the status line rather than
            # printing, because the next redraw clears the screen.
            status = preview(rows[cursor][0])


def _manual_text(ui, label: str) -> str | None:
    """Type a value by hand. An empty answer means 'never mind'."""
    return _ask(ui, label).strip() or None


def _manual_speed(ui) -> float | None:
    """Type a speed by hand, re-asking until it passes the config check."""
    while True:
        raw = _ask(ui, f"Speed ({SPEED_MIN}–{SPEED_MAX})").strip()
        if not raw:
            return None
        try:
            return validate_speed(raw, "manual entry")
        except ConfigError as exc:
            click.echo(str(exc), file=ui)


def _speed_choices() -> list[float]:
    """0.7 … 1.2 in 0.1 steps, derived from the config module's bounds."""
    steps = round((SPEED_MAX - SPEED_MIN) / 0.1)
    return [round(SPEED_MIN + index * 0.1, 1) for index in range(steps + 1)]


def _keep_label(existing: dict, key: str, resolved) -> str:
    """What "keep current" would actually leave behind.

    The file's value, not the resolved one: keeping writes nothing, so a
    VOCALIZE_* env var must not be named as though the file held it.
    """
    if key in existing:
        return f"{existing[key]}"
    if resolved is None:
        return "unset"
    return f"{resolved} — not in the file"


def _offer_key_setup(ui) -> str | None:
    """Offer to store an API key when there isn't one yet.

    Without this the wizard just degrades to typing a voice ID by hand,
    which is a worse first run than being asked one question. Declining
    keeps that degradation exactly as it was.

    Returns why the attempt failed, or None. The caller carries that into
    the voice step's note: anything printed on this screen is erased by
    the next frame's clear, milliseconds later.
    """
    try:
        resolve_api_key()
    except MissingAPIKeyError:
        pass
    else:
        return None

    _clear(ui)
    if not _confirm(ui, "No API key found. Set one up now?"):
        return None

    key = prompt_for_key()
    if not key:
        return None
    try:
        login(key)
    except VocalizeError as exc:
        # login already scrubs, but this is the last stop before a screen.
        reason = scrub(str(exc), key)
        # Also to stderr, which outlives the wizard and reaches a log.
        click.echo(f"vocalize: could not store that key — {reason}", err=True)
        return reason
    return None


def _voice_step(ui, current: str, keep: str, setup_error: str | None = None):
    rows = [(_KEEP, f"keep current ({keep})")]
    cursor = 0
    notes = []
    client = None

    try:
        client = build_client(resolve_api_key())
        voices = list_voices(client)
    except VocalizeError as exc:
        # Keyless (or offline) still has to work: the list degrades to
        # manual entry rather than taking the whole wizard down.
        client = None
        voices = []
        # A failed setup explains this screen far better than the generic
        # "no key found" it caused, and this note survives the redraws.
        reason = f"key setup failed: {setup_error}" if setup_error else str(exc)
        notes.append(f"No voice list ({reason}) — press m to type a voice ID by hand.")
    else:
        notes.append("Previews spend a few characters of your API quota.")

    for voice in voices:
        label = f"{voice['id']}  {voice['name']}"
        if voice["id"] == current:
            label += "  (current)"
            cursor = len(rows)
        rows.append((voice["id"], label))

    def preview(value):
        voice_id = current if value is _KEEP else value
        if client is None:
            return "Preview needs an API key."
        # Synthesis and playback both block; say so, or the frozen frame
        # reads as a hang.
        click.echo(f"Previewing {voice_id}…", file=ui)
        try:
            play(save(synthesize(client, PREVIEW_TEXT, Settings(voice_id=voice_id)), PREVIEW_PATH))
        except VocalizeError as exc:
            return f"Preview failed: {exc}"
        return f"Previewed {voice_id}."

    return _select(
        ui,
        "Step 1 of 3 — Voice (ElevenLabs)",
        rows,
        cursor,
        legend=VOICE_HOTKEYS,
        notes=notes,
        manual=lambda: _manual_text(ui, "Voice ID"),
        preview=preview,
    )


def _model_step(ui, current: str, keep: str):
    rows = [(_KEEP, f"keep current ({keep})")]
    cursor = 0

    for model_id, blurb in MODEL_CHOICES:
        label = f"{model_id}  ({blurb})"
        if model_id == current:
            label += "  (current)"
            cursor = len(rows)
        rows.append((model_id, label))

    return _select(
        ui,
        "Step 2 of 3 — Model (ElevenLabs)",
        rows,
        cursor,
        manual=lambda: _manual_text(ui, "Model ID"),
    )


def _speed_step(ui, current: float | None, keep: str):
    rows = [(_KEEP, f"keep current ({keep})"), (_UNSET, "unset (API default)")]
    cursor = 0

    for value in _speed_choices():
        label = f"{value:.1f}" + ("  (normal)" if value == 1.0 else "")
        if current is not None and abs(value - current) < 1e-9:
            label += "  (current)"
            cursor = len(rows)
        rows.append((value, label))

    return _select(
        ui, "Step 3 of 3 — Speed (ElevenLabs)", rows, cursor, manual=lambda: _manual_speed(ui)
    )


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

    A list of scalars (e.g. `chain = ["elevenlabs", "say"]`) renders
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
_TABLE_KEYS = ("providers", "stt")


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
    """Render `data` as TOML: flat keys first, then `[stt]` and `[providers.*]`.

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
    """
    lines = [
        f"{key} = {_toml_value(key, value)}"
        for key, value in data.items()
        if key not in _TABLE_KEYS
    ]

    if "stt" in data:
        lines.extend(_table_lines("stt", data["stt"], "'stt'"))

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


def _summary_value(chosen, keep: str) -> str:
    if chosen is _KEEP:
        return f"unchanged ({keep})"
    if chosen is _UNSET:
        return "removed — the API's own default applies"
    return str(chosen)


def run_wizard() -> None:
    """Walk through voice, model and speed, then write the config file."""
    stdin = sys.stdin
    if stdin is None or not stdin.isatty():
        raise ConfigError(_NO_TERMINAL)

    # The keyboard is only half of it — there also has to be somewhere to
    # paint. A relayed stdout is fine as long as /dev/tty opens.
    ui, opened = _open_ui_stream()
    if ui is None:
        raise ConfigError(_NO_TERMINAL)
    try:
        _walk(ui)
    finally:
        if opened:
            ui.close()


def _walk(ui) -> None:
    path = config_path()
    # Before the read, not after it: the wizard then sits on three
    # interactive questions, and what the file looked like when it was
    # parsed is what the write at the end is allowed to replace (DEC-005).
    fingerprint = fingerprint_config(path)
    existing = load_config_file()
    # Dry-run the serialiser before asking any questions: fail fast rather
    # than walking someone through three steps we can't write at the end.
    _render_config_text(existing)

    setup_error = _offer_key_setup(ui)

    try:
        current = resolve_settings()
    except ConfigError as exc:
        # A bad value in the file is exactly why someone runs this wizard,
        # so it must not be the thing that stops them.
        click.echo(f"Note: ignoring an unusable current setting — {exc}", err=True)
        current = Settings()

    keep = {
        "voice": _keep_label(existing, "voice", current.voice_id),
        "model": _keep_label(existing, "model", current.model_id),
        "speed": _keep_label(existing, "speed", current.speed),
    }

    try:
        chosen = {
            "voice": _voice_step(ui, current.voice_id, keep["voice"], setup_error),
            "model": _model_step(ui, current.model_id, keep["model"]),
            "speed": _speed_step(ui, current.speed, keep["speed"]),
        }
    except _Cancelled:
        click.echo("Cancelled — nothing changed.", file=ui)
        return

    data = dict(existing)  # unknown keys ride through untouched
    # A [providers.elevenlabs] table outranks these legacy top-level keys
    # when resolving settings, so a stale table entry would silently
    # shadow whatever the wizard just picked. Strip the same key from the
    # table so the wizard's choice is the one that actually applies.
    providers = dict(data.get("providers") or {})
    elevenlabs_table = dict(providers.get("elevenlabs") or {})
    shadowed = []
    for key, value in chosen.items():
        if value is _KEEP:
            continue
        if value is _UNSET:
            data.pop(key, None)
        else:
            data[key] = value
        if key in elevenlabs_table:
            del elevenlabs_table[key]
            shadowed.append(key)

    if shadowed:
        if elevenlabs_table:
            providers["elevenlabs"] = elevenlabs_table
        else:
            providers.pop("elevenlabs", None)
        if providers:
            data["providers"] = providers
        else:
            data.pop("providers", None)

    _clear(ui)
    click.echo("About to write:", file=ui)
    click.echo(file=ui)
    for key, value in chosen.items():
        click.echo(f"  {key:<5} → {_summary_value(value, keep[key])}", file=ui)
    for key in shadowed:
        click.echo(
            f"[providers.elevenlabs] {key} removed — the wizard's choice now applies",
            file=ui,
        )
    click.echo(file=ui)
    click.echo(f"File: {path}", file=ui)
    click.echo(file=ui)

    if not _confirm(ui, "Write these settings?"):
        click.echo("Cancelled — nothing changed.", file=ui)
        return

    text, _ = write_config_if_unchanged(path, data, fingerprint)
    click.echo(f"Wrote {path}", file=ui)
    if ui is not sys.stdout:
        # The outcome line is the one thing a wrapper or a log should still
        # see when the UI went to the terminal instead of down the pipe.
        click.echo(f"Wrote {path}", file=sys.stdout)
    click.echo(file=ui)
    click.echo(text.rstrip("\n") or "(empty — every setting is back to its default)", file=ui)
