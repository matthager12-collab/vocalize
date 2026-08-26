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

import os
import shutil
import sys
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
from .exceptions import ConfigError, MissingAPIKeyError, VocalizeError
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
        "Step 1 of 3 — Voice",
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
        "Step 2 of 3 — Model",
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

    return _select(ui, "Step 3 of 3 — Speed", rows, cursor, manual=lambda: _manual_speed(ui))


def _toml_value(key: str, value) -> str:
    """Render one scalar as TOML. The config file is flat scalars only."""
    if isinstance(value, (dict, list)):
        # str() would quietly turn a table or array into a Python-repr
        # string and destroy it on write. Refuse instead.
        kind = "table" if isinstance(value, dict) else "array"
        raise ConfigError(
            f"The config file has a {kind} under {key!r}. The wizard only manages "
            f"flat keys, so it will not rewrite this file — edit that file by hand."
        )
    if isinstance(value, bool):  # bool before int — bool is an int subclass
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _write_config(path: Path, data: dict) -> str:
    text = "".join(f"{key} = {_toml_value(key, value)}\n" for key, value in data.items())
    tmp = path.with_suffix(".toml.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise ConfigError(f"Could not write config file {path}: {exc}") from exc
    return text


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
    existing = load_config_file()
    # Dry-run the serialiser before asking any questions: fail fast rather
    # than walking someone through three steps we can't write at the end.
    for key, value in existing.items():
        _toml_value(key, value)

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
    for key, value in chosen.items():
        if value is _KEEP:
            continue
        if value is _UNSET:
            data.pop(key, None)
        else:
            data[key] = value

    _clear(ui)
    click.echo("About to write:", file=ui)
    click.echo(file=ui)
    for key, value in chosen.items():
        click.echo(f"  {key:<5} → {_summary_value(value, keep[key])}", file=ui)
    click.echo(file=ui)
    click.echo(f"File: {path}", file=ui)
    click.echo(file=ui)

    if not _confirm(ui, "Write these settings?"):
        click.echo("Cancelled — nothing changed.", file=ui)
        return

    text = _write_config(path, data)
    click.echo(f"Wrote {path}", file=ui)
    if ui is not sys.stdout:
        # The outcome line is the one thing a wrapper or a log should still
        # see when the UI went to the terminal instead of down the pipe.
        click.echo(f"Wrote {path}", file=sys.stdout)
    click.echo(file=ui)
    click.echo(text.rstrip("\n") or "(empty — every setting is back to its default)", file=ui)
