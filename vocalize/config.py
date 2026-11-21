"""Local-only configuration.

Voice/model/speed precedence is flag, environment, provider table, default.
Settings retired with the cloud providers and the summaries are ignored with
one stderr notice each (DEC-005); a malformed value for a live key is an error.
"""

from __future__ import annotations

import itertools
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from . import auth
from .exceptions import ConfigError

DEFAULT_VOICE = "af_heart"
DEFAULT_MODEL = ""
DEFAULT_OUTPUT_FORMAT = "wav"

# Supported speech speed range; 1.0 is normal speed.
SPEED_MIN = 0.7
SPEED_MAX = 1.2

KNOWN_CONFIG_KEYS = (
    "max_chars", "overflow", "chain", "providers", "stt", "transcribe", "notes", "app", "speech",
)

# Keys inside a [providers.<name>] table that become a request field or an
# argument: short printable strings, or the file is refused (issue #5).
PROVIDER_TEXT_KEYS = ("voice", "model", "language")
PROVIDER_TEXT_MAX_CHARS = 128

# Keys allowed inside a [providers.<name>] table.
KNOWN_PROVIDER_KEYS = (
    "voice",
    "model",
    "language",
    "speed",
)

# What to do when input is longer than the resolved character cap.
OVERFLOW_MODES = ("truncate", "ask", "never")

# Keys allowed inside the [stt] table (DEC-006, design § [stt] config table).
KNOWN_STT_KEYS = (
    "engine",
    "model",
    "language",
    "input_device",
    "cleanup",
    "paste",
    "max_seconds",
    "sounds",
    "cues",
    "beam_size",
    "verbatim",
    "max_take_seconds",
    "vocabulary",
    "warm_minutes",
)

# Where a dictation's cleanup pass runs. `off` is the default: a press must
# never surprise with a pause or reworded text. `local` runs the cleanup
# pass on this Mac. Legacy true and the hosted backends become off with a
# notice (DEC-005); false stays off.
STT_CLEANUP_BACKENDS = ("off", "local")

# What `cues` may be: the fixed system sounds, spoken words instead, or both.
STT_CUE_MODES = ("sounds", "words", "both")

# `paste` was reserved by DEC-006 and did nothing through 0.13.0; from
# 0.13.1 `dictate._stop` honours it (design § Auto-paste).
# "auto" uses Parakeet on an Apple Silicon Mac where it is installed and
# whisper everywhere else; "parakeet" and "whisper" pin one engine.
STT_ENGINES = ("auto", "parakeet", "whisper")

STT_DEFAULTS = {
    "engine": "auto",
    # turbo q5_0: the first model that kept "the merge" as two words on the
    # owner's voice (issue #4), no slower than small.en on an M4 and only
    # ~90 MB more resident. small.en stays the lighter choice for a slow Mac.
    "model": "large-v3-turbo-q5_0",
    "language": "en",
    "input_device": "",
    "cleanup": "off",
    "verbatim": False,
    "paste": False,
    "max_seconds": 120,
    "sounds": True,
    "cues": "sounds",
    "beam_size": 5,
    "max_take_seconds": 1800,
    "vocabulary": [],
    "warm_minutes": 0,
}

# The recorder self-stops at max_seconds and `dictate` backstops it, so this
# is a real resource bound, not a cosmetic one.
STT_MAX_SECONDS_MIN = 1
STT_MAX_SECONDS_MAX = 600

# `max_take_seconds` caps the entire paused-and-resumed take (DEC-037).
STT_MAX_TAKE_SECONDS_MIN = 60
STT_MAX_TAKE_SECONDS_MAX = 7200

# `beam_size` becomes the worker's `--beam-size`: 1 is whisper.cpp's greedy
# decoder (0.10.x behaviour), 5 is whisper.cpp's own beam default and the
# fix for words run together on fast speech (issue #4). Capped at 8 because
# every extra beam is decode time on the stop press.
STT_BEAM_SIZE_MIN = 1
STT_BEAM_SIZE_MAX = 8

# `input_device` is passed to the recorder as one argv entry. It is a device
# name a human copied out of `vocalize listen --list-devices`, so the shape
# check is all that is needed — but it has to be a real one: a control
# character would let a hardware-shaped name drive a terminal, and a leading
# '-' would turn a config value into a recorder flag.
STT_DEVICE_MAX_CHARS = 128

_VOCABULARY_MAX_ITEMS = 50
_VOCABULARY_MAX_CHARS = 40
_VOCABULARY_MAX_WORDS = 4
_VOCABULARY_PROMPT_MAX_CHARS = 800
_VOCABULARY_ALLOWED = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 _.\u002d+/@#:"
)


def vocabulary_prompt(items) -> str:
    """The bounded, configured prompt passed to whisper.cpp."""
    return "" if not items else "Vocabulary: " + ", ".join(items) + "."

# chain = ["kokoro", "say"]: the on-device voice first, degrading to the
# always-present `say` instead of erroring — so a keyless fresh Mac still
# speaks, and the fallback line names the install that is missing (DEC-022).
DEFAULT_CHAIN = ("kokoro", "say")


#: Config warnings already printed. The portal re-reads and re-validates the
#: file on every `/api/state` poll, so without this one typo writes a stderr
#: line every few seconds for the life of the process — burying the two
#: messages the portal actually needs the user to see, its lockout and its
#: idle shutdown, which go to the same terminal. A single-shot CLI run warns
#: exactly as it did before.
_warned: set[str] = set()


def _warn(message: str) -> None:
    """Print a config warning once per process."""
    if message not in _warned:
        _warned.add(message)
        print(message, file=sys.stderr)


def config_path() -> Path:
    """Path of the optional TOML config file."""
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "vocalize" / "config.toml"


# --- retired settings (DEC-005) ------------------------------------------
#
# A 0.15 file keeps working: each retired key is ignored with one notice on
# stderr (stdout is parsed by the menu-bar app), and the next save drops it.
# A notice names the key path and what to do, never the value, because an
# old file can hold an API key.

CLOUD_PROVIDER_NAMES = ("elevenlabs", "openai", "google", "polly")

_REMOVE_IT = "nothing leaves this Mac any more; delete it"
_RETIRED_ROOT_KEYS = {
    "voice": "set voice in a [providers.kokoro], [providers.piper] or [providers.say] table",
    "model": "set model in a [providers.kokoro], [providers.piper] or [providers.say] table",
    "speed": "set speed in a [providers.kokoro], [providers.piper] or [providers.say] table",
    "api_key": _REMOVE_IT,
    "api-key": _REMOVE_IT,
    "endpoint": _REMOVE_IT,
    "base_url": _REMOVE_IT,
}
_RETIRED_PROVIDER_KEYS = (
    "api_key", "key", "base_url", "endpoint", "region", "profile", "monthly_chars", "engine",
)
_RETIRED_TRANSCRIBE_KEYS = ("template", "summarizer")
_HOSTED_CLEANUP = ("claude-cli", "anthropic")


def _migrate(data) -> tuple[dict, list[str]]:
    """A copy of `data` with retired settings removed, and one notice per key.

    Pure: prints nothing. Values that still have a local meaning migrate:
    a chain loses its cloud engines (the default chain if none is left),
    and a hosted dictation cleanup becomes `off`, never `local`.
    """
    if not isinstance(data, dict):
        raise ConfigError("Configuration must be a table")
    path = config_path()
    clean = dict(data)
    notices: list[str] = []

    def retire(key_path: str, advice: str) -> None:
        notice = f"vocalize: ignoring retired setting {key_path} in {path}: {advice}."
        if notice not in notices:
            notices.append(notice)

    for key, advice in _RETIRED_ROOT_KEYS.items():
        if key in clean:
            del clean[key]
            retire(key, advice)

    chain = clean.get("chain")
    if isinstance(chain, list):
        kept = [name for name in chain if name not in CLOUD_PROVIDER_NAMES]
        for name in CLOUD_PROVIDER_NAMES:
            if name in chain:
                retire(f"chain entry {name!r}", "cloud engines were removed; it is skipped")
        if not kept:
            del clean["chain"]  # the default chain applies
        elif len(kept) != len(chain):
            clean["chain"] = kept

    tables = clean.get("providers")
    if isinstance(tables, dict):
        kept_tables = {}
        for name, table in tables.items():
            if name in CLOUD_PROVIDER_NAMES:
                retire(f"[providers.{name}]", "cloud engines were removed; delete the table")
                continue
            if name in auth.PROVIDER_NAMES and isinstance(table, dict):
                table = dict(table)
                for key in _RETIRED_PROVIDER_KEYS:
                    if key in table:
                        del table[key]
                        retire(f"providers.{name}.{key}", _REMOVE_IT)
            kept_tables[name] = table
        clean["providers"] = kept_tables

    stt = clean.get("stt")
    if isinstance(stt, dict) and "cleanup" in stt:
        cleanup = stt["cleanup"]
        if cleanup is True or (isinstance(cleanup, str) and cleanup in _HOSTED_CLEANUP):
            clean["stt"] = {**stt, "cleanup": "off"}
            retire(
                "stt.cleanup",
                "hosted cleanup was removed, so cleanup is off; set it to \"local\" "
                "to clean up on this Mac",
            )

    for section in ("notes", "transcribe"):
        table = clean.get(section)
        if isinstance(table, dict) and any(key in table for key in _RETIRED_TRANSCRIBE_KEYS):
            table = dict(table)
            for key in _RETIRED_TRANSCRIBE_KEYS:
                if key in table:
                    del table[key]
                    retire(f"{section}.{key}", "summaries were removed; delete it")
            clean[section] = table

    return clean, notices


def retired_notices(data) -> list[str]:
    """The notices `data` would print for its retired settings, without printing."""
    return _migrate(data)[1]


def without_retired_settings(data) -> dict:
    """`data` without its retired settings, printing nothing (for the writer)."""
    return _migrate(data)[0]


def migrate_retired_settings(data) -> dict:
    """`data` without its retired settings; each notice printed once per process."""
    clean, notices = _migrate(data)
    for notice in notices:
        _warn(notice)
    return clean


def reject_retired_settings(data) -> None:
    """Kept for callers of the 0.15 name: warns instead of refusing (DEC-005).

    Raises ConfigError only for a value that is not a table at all.
    """
    migrate_retired_settings(data)


def load_config_file() -> dict:
    """Parse the config file. Returns {} when there isn't one.

    Unknown keys warn on stderr rather than failing: a typo shouldn't stop
    the run, but it shouldn't be silently ignored either.
    """
    try:
        import tomllib
    except ImportError:  # Python 3.10; tomllib is stdlib from 3.11
        import tomli as tomllib  # type: ignore

    path = config_path()
    if not path.is_file():
        return {}

    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Could not parse config file {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read config file {path}: {exc}") from exc

    data = migrate_retired_settings(data)

    for key in data:
        if key not in KNOWN_CONFIG_KEYS:
            _warn(f"vocalize: unknown config key {key!r} in {path}")

    if "chain" in data:
        _validate_chain(data["chain"], path)
    if "providers" in data:
        _validate_providers_table(data["providers"], path)
    if "stt" in data:
        _validate_stt_table(data["stt"], path)
    for section in ("notes", "transcribe"):
        if section in data:
            _validate_transcribe_table(data[section], path, section)
    if "app" in data:
        _validate_app_table(data["app"], path)
    if "speech" in data:
        _validate_speech_table(data["speech"], path)

    return data


def _validate_provider_name(name: str, source: str) -> None:
    if name not in auth.PROVIDER_NAMES:
        raise ConfigError(
            f"Unknown provider {name!r} in {source}. Known: {', '.join(auth.PROVIDER_NAMES)}"
        )


def _validate_chain(value, path: Path) -> None:
    if not isinstance(value, list) or not all(isinstance(name, str) and name for name in value):
        raise ConfigError("config key 'chain' must be a list of provider names")
    if not value:
        raise ConfigError("config key 'chain' must list at least one provider")

    source = f"'chain' in {path}"
    seen: set[str] = set()
    for name in value:
        _validate_provider_name(name, source)
        if name in seen:
            raise ConfigError(f"Duplicate provider {name!r} in {source}.")
        seen.add(name)


def _validate_providers_table(value, path: Path) -> None:
    if not isinstance(value, dict) or not all(isinstance(table, dict) for table in value.values()):
        raise ConfigError("config key 'providers' must be a table of provider tables")

    for name, table in value.items():
        _validate_provider_name(name, "provider table")
        for key, val in table.items():
            if key not in KNOWN_PROVIDER_KEYS:
                _warn(
                    f"vocalize: unknown config key {key!r} in [providers.{name}] in {path}"
                )
            if key in PROVIDER_TEXT_KEYS:
                _validate_provider_text(key, val, name, path)


def _validate_provider_text(key: str, val, name: str, path: Path) -> None:
    """A provider's voice, model or language is a
    short printable string: it becomes a request field or an argument, so
    an int, a table or a flag-shaped value is refused, not passed along."""
    if (
        not isinstance(val, str)
        or not val.strip()
        or len(val) > PROVIDER_TEXT_MAX_CHARS
        or not val.isprintable()
        or val.startswith("-")
    ):
        raise ConfigError(
            f"Invalid {key} {val!r} in [providers.{name}] in {path}: expected a short "
            f"printable string (at most {PROVIDER_TEXT_MAX_CHARS} characters, not starting with '-')."
        )


def _validate_stt_table(value, path: Path) -> None:
    """Check the `[stt]` table. Unknown keys warn; bad values raise.

    Every value here becomes an argument to a subprocess — the recorder's
    `--device`, or the whisper worker's `--model` and `--language` — so
    each one is checked against an allowlist or a shape before it can get
    that far, the same way `[providers.*]` values are.
    """
    from .local import whisper_manifest  # lazy: config is imported by everything

    if not isinstance(value, dict):
        raise ConfigError(f"config key 'stt' in {path} must be a table")

    for key in value:
        if key not in KNOWN_STT_KEYS:
            _warn(f"vocalize: unknown config key {key!r} in [stt] in {path}")

    engine = value.get("engine")
    if engine is not None and engine not in STT_ENGINES:
        raise ConfigError(
            f"Invalid stt.engine {engine!r} in {path}. Known: {', '.join(STT_ENGINES)}"
        )

    model = value.get("model")
    if model is not None and model not in whisper_manifest.MODELS:
        raise ConfigError(
            f"Invalid stt.model {model!r} in {path}. "
            f"Known: {', '.join(whisper_manifest.MODELS)}"
        )

    language = value.get("language")
    if language is not None and language not in whisper_manifest.LANGUAGES:
        raise ConfigError(
            f"Invalid stt.language {language!r} in {path}: not a whisper.cpp language code."
        )

    # An .en model transcribes English whatever it is asked for, so this
    # pairing would quietly produce English while claiming otherwise.
    resolved_model = model if model is not None else STT_DEFAULTS["model"]
    if (
        language is not None
        and language != "en"
        and whisper_manifest.is_english_only(resolved_model)
    ):
        raise ConfigError(
            f"stt.model {resolved_model!r} in {path} is English-only, so "
            f"stt.language must be 'en', not {language!r}."
        )

    seconds = value.get("max_seconds")
    if seconds is not None and (
        isinstance(seconds, bool)
        or not isinstance(seconds, int)
        or not STT_MAX_SECONDS_MIN <= seconds <= STT_MAX_SECONDS_MAX
    ):
        raise ConfigError(
            f"Invalid stt.max_seconds {seconds!r} in {path}: expected an integer "
            f"between {STT_MAX_SECONDS_MIN} and {STT_MAX_SECONDS_MAX}."
        )

    take_seconds = value.get("max_take_seconds")
    if take_seconds is not None and (
        isinstance(take_seconds, bool)
        or not isinstance(take_seconds, int)
        or not STT_MAX_TAKE_SECONDS_MIN <= take_seconds <= STT_MAX_TAKE_SECONDS_MAX
    ):
        raise ConfigError(
            f"Invalid stt.max_take_seconds {take_seconds!r} in {path}: expected an integer "
            f"between {STT_MAX_TAKE_SECONDS_MIN} and {STT_MAX_TAKE_SECONDS_MAX}."
        )

    beams = value.get("beam_size")
    if beams is not None and (
        isinstance(beams, bool)
        or not isinstance(beams, int)
        or not STT_BEAM_SIZE_MIN <= beams <= STT_BEAM_SIZE_MAX
    ):
        raise ConfigError(
            f"Invalid stt.beam_size {beams!r} in {path}: expected an integer "
            f"between {STT_BEAM_SIZE_MIN} and {STT_BEAM_SIZE_MAX}."
        )

    device = value.get("input_device")
    if device is not None:
        _validate_input_device(device, path)

    cleanup = value.get("cleanup")
    if cleanup is not None and cleanup is not False and cleanup not in STT_CLEANUP_BACKENDS:
        raise ConfigError(
            f"Invalid stt.cleanup {cleanup!r} in {path}: expected one of "
            f"{', '.join(STT_CLEANUP_BACKENDS)} (or false from older configs)."
        )

    for key in ("paste", "sounds", "verbatim"):
        flag = value.get(key)
        if flag is not None and not isinstance(flag, bool):
            raise ConfigError(f"Invalid stt.{key} {flag!r} in {path}: expected true or false.")

    cues = value.get("cues")
    if cues is not None and cues not in STT_CUE_MODES:
        raise ConfigError(
            f"Invalid stt.cues {cues!r} in {path}. Expected one of: "
            f"{', '.join(STT_CUE_MODES)}."
        )

    if "warm_minutes" in value:
        minutes = value["warm_minutes"]
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 0 <= minutes <= 240:
            raise ConfigError(
                f"Invalid stt.warm_minutes in {path}: expected an integer between 0 and 240."
            )

    vocabulary = value.get("vocabulary")
    if vocabulary is not None:
        _validate_vocabulary(vocabulary, path)


def _validate_vocabulary(vocabulary, path: Path) -> None:
    """Validate the short terms that can become Whisper's initial prompt."""
    if not isinstance(vocabulary, list):
        raise ConfigError(f"Invalid stt.vocabulary in {path}: expected a list of terms.")
    if len(vocabulary) > _VOCABULARY_MAX_ITEMS:
        raise ConfigError(
            f"Invalid stt.vocabulary in {path}: expected at most {_VOCABULARY_MAX_ITEMS} terms."
        )
    for item in vocabulary:
        if not isinstance(item, str):
            raise ConfigError(f"Invalid stt.vocabulary in {path}: every term must be a string.")
        if not 1 <= len(item) <= _VOCABULARY_MAX_CHARS:
            raise ConfigError(
                f"Invalid stt.vocabulary in {path}: every term must be 1 to "
                f"{_VOCABULARY_MAX_CHARS} characters."
            )
        if any(character not in _VOCABULARY_ALLOWED for character in item):
            raise ConfigError(
                f"Invalid stt.vocabulary in {path}: terms may use only letters, digits, "
                "spaces, and _ . - + / @ # :."
            )
        if item != item.strip() or "  " in item:
            raise ConfigError(
                f"Invalid stt.vocabulary in {path}: a term cannot start or end with a "
                "space, or hold two spaces in a row."
            )
        if not 1 <= len(item.split()) <= _VOCABULARY_MAX_WORDS:
            raise ConfigError(
                f"Invalid stt.vocabulary in {path}: every term must contain 1 to "
                f"{_VOCABULARY_MAX_WORDS} space-separated words."
            )
        if item.endswith((".", "!", "?", ";", ",")):
            raise ConfigError(
                f"Invalid stt.vocabulary in {path}: a term cannot end as a sentence.")
    if len(vocabulary_prompt(vocabulary)) > _VOCABULARY_PROMPT_MAX_CHARS:
        raise ConfigError(
            f"Invalid stt.vocabulary in {path}: the built prompt must be at most "
            f"{_VOCABULARY_PROMPT_MAX_CHARS} characters."
        )


def _validate_input_device(device, path: Path) -> None:
    if not isinstance(device, str):
        raise ConfigError(
            f"Invalid stt.input_device {device!r} in {path}: expected a device name."
        )
    if len(device) > STT_DEVICE_MAX_CHARS:
        raise ConfigError(
            f"Invalid stt.input_device in {path}: a device name is at most "
            f"{STT_DEVICE_MAX_CHARS} characters."
        )
    if any(not ch.isprintable() for ch in device):
        raise ConfigError(
            f"Invalid stt.input_device in {path}: a device name cannot contain "
            f"control characters."
        )
    if device.startswith("-"):
        raise ConfigError(
            f"Invalid stt.input_device {device!r} in {path}: a device name cannot "
            f"start with '-'."
        )


def resolve_stt(file_config: dict | None = None) -> dict:
    """The `[stt]` settings with defaults filled in, re-validated.

    Re-validated rather than trusted: `load_config_file` checks the table
    on the way in, but this function is also handed hand-built dicts (the
    portal, tests, a caller that never read the file), and the values it
    returns go straight into a subprocess argv.
    """
    if file_config is None:
        file_config = load_config_file()
    file_config = migrate_retired_settings(file_config)
    table = file_config.get("stt") or {}
    _validate_stt_table(table, config_path())
    resolved = dict(STT_DEFAULTS)
    resolved.update({key: table[key] for key in KNOWN_STT_KEYS if key in table})
    if isinstance(resolved["cleanup"], bool):  # 0.10.x wrote true/false
        resolved["cleanup"] = "off"
    return resolved


# --- [transcribe] (was [notes]) -------------------------------------------
#
# `vocalize transcribe` settings (DEC-007). `[notes]` is still read as the
# table's old name; when both set a key, `[transcribe]` wins. The writer
# saves it all under `[transcribe]`.

KNOWN_TRANSCRIBE_KEYS = ("folder", "keep_audio", "model")
TRANSCRIBE_DEFAULTS = {
    "folder": "~/Documents/Vocalize Notes",
    "keep_audio": False,
    "model": "",  # "" = the [stt] model; else one of whisper_manifest.MODELS
}
TRANSCRIBE_FOLDER_MAX_CHARS = 512

# The old names, for callers that have not moved yet.
KNOWN_NOTES_KEYS = KNOWN_TRANSCRIBE_KEYS
NOTES_DEFAULTS = TRANSCRIBE_DEFAULTS
NOTES_FOLDER_MAX_CHARS = TRANSCRIBE_FOLDER_MAX_CHARS


def _validate_transcribe_table(value, path: Path, section: str = "transcribe") -> None:
    """Check a `[transcribe]` or `[notes]` table. Unknown keys warn; bad values raise."""
    if not isinstance(value, dict):
        raise ConfigError(f"config key {section!r} in {path} must be a table")
    for key in value:
        if key not in KNOWN_TRANSCRIBE_KEYS:
            _warn(f"vocalize: unknown config key {key!r} in [{section}] in {path}")

    folder = value.get("folder")
    if folder is not None and (
        not isinstance(folder, str)
        or not folder.strip()
        or len(folder) > TRANSCRIBE_FOLDER_MAX_CHARS
        or not folder.isprintable()
    ):
        raise ConfigError(
            f"Invalid {section}.folder {folder!r} in {path}: expected a non-empty path."
        )

    keep = value.get("keep_audio")
    if keep is not None and not isinstance(keep, bool):
        raise ConfigError(
            f"Invalid {section}.keep_audio {keep!r} in {path}: expected true or false."
        )

    model = value.get("model")
    if model is not None:
        from .local import whisper_manifest

        if not isinstance(model, str) or (model != "" and model not in whisper_manifest.MODELS):
            raise ConfigError(
                f"Invalid {section}.model {model!r} in {path}: expected \"\" (the [stt] model) "
                f"or one of {', '.join(whisper_manifest.MODELS)}."
            )


def resolve_transcribe(file_config: dict | None = None) -> dict:
    """The `[transcribe]` settings with defaults filled in, re-validated.

    `[notes]` is read first and `[transcribe]` over it, so the new name wins.
    """
    if file_config is None:
        file_config = load_config_file()
    file_config = migrate_retired_settings(file_config)
    resolved = dict(TRANSCRIBE_DEFAULTS)
    for section in ("notes", "transcribe"):
        table = file_config.get(section) or {}
        _validate_transcribe_table(table, config_path(), section)
        resolved.update({key: table[key] for key in KNOWN_TRANSCRIBE_KEYS if key in table})
    return resolved


#: The table's old name; other modules still call it.
resolve_notes = resolve_transcribe


# --- the [app] table -----------------------------------------------------
#
# The menu-bar app's chords (0.13.0, design.md § The [app] table). The app
# reads them from `vocalize settings` as `app.<key>=…` lines, so what is
# printed is the canonical form: modifiers in one fixed order, lowercase,
# aliases resolved. The Swift side keeps its own keycode table; a test
# holds the two allowlists equal.

KNOWN_APP_KEYS = ("dictate", "dictate_mode", "speak", "stop", "stop_hotkey")
APP_CHORD_KEYS = ("dictate", "speak", "stop")
APP_DICTATE_MODES = ("toggle", "hold")
APP_STOP_HOTKEY_MODES = ("stop", "pause")
APP_DEFAULTS = {
    "dictate": "ctrl+alt+cmd+d",
    "dictate_mode": "toggle",  # "hold" parses from 0.13.0, dispatches from 0.13.1
    "speak": "ctrl+alt+cmd+s",
    "stop": "ctrl+alt+cmd+x",
    "stop_hotkey": "stop",
}
CHORD_MODIFIERS = ("ctrl", "alt", "cmd", "shift")  # the canonical order
_CHORD_ALIASES = {"control": "ctrl", "option": "alt", "command": "cmd"}
CHORD_KEYS = (
    tuple("abcdefghijklmnopqrstuvwxyz")
    + tuple("0123456789")
    + tuple(f"f{n}" for n in range(1, 13))
)

def parse_chord(text) -> tuple[tuple[str, ...], str] | None:
    """`"ctrl+alt+cmd+d"` -> `(("ctrl", "alt", "cmd"), "d")`; `""` -> None (disabled).

    Raises ValueError with the reason (TypeError for a non-string). Tokens
    split on `+`; modifiers are
    ctrl, alt, cmd, shift (aliases control, option, command); the key is
    one of a-z, 0-9, f1-f12; a chord must carry ctrl or cmd, because macOS
    refuses to register the rest (error -9868).
    """
    if not isinstance(text, str):
        raise TypeError("expected a string")
    if text == "":
        return None
    tokens = text.lower().split("+")
    if any(not token or token != token.strip() for token in tokens):
        raise ValueError("expected tokens joined by '+', with no spaces")
    *modifiers, key = tokens
    modifiers = [_CHORD_ALIASES.get(token, token) for token in modifiers]
    for token in modifiers:
        if token not in CHORD_MODIFIERS:
            raise ValueError(f"unknown modifier {token!r}: expected ctrl, alt, cmd or shift")
    if len(set(modifiers)) != len(modifiers):
        raise ValueError("a modifier is repeated")
    if key in CHORD_MODIFIERS or key in _CHORD_ALIASES:
        raise ValueError("a chord needs a key after its modifiers (a-z, 0-9 or f1-f12)")
    if key not in CHORD_KEYS:
        raise ValueError(f"unknown key {key!r}: expected a-z, 0-9 or f1-f12")
    if "ctrl" not in modifiers and "cmd" not in modifiers:
        raise ValueError("a chord must include ctrl or cmd (macOS refuses the rest)")
    return tuple(name for name in CHORD_MODIFIERS if name in modifiers), key


def chord_text(parsed: tuple[tuple[str, ...], str] | None) -> str:
    """The canonical spelling of a parsed chord; `""` for a disabled one."""
    if parsed is None:
        return ""
    modifiers, key = parsed
    return "+".join((*modifiers, key))


def _validate_app_table(value, path: Path) -> None:
    """Check the `[app]` table. Unknown keys warn; bad values raise.

    Chords are checked pairwise distinct against the resolved set — the
    file's own values over the defaults — so a file that sets only `speak`
    to the default dictate chord is refused too.
    """
    if not isinstance(value, dict):
        raise ConfigError(f"config key 'app' in {path} must be a table")
    for key in value:
        if key not in KNOWN_APP_KEYS:
            _warn(f"vocalize: unknown config key {key!r} in [app] in {path}")

    resolved = {**APP_DEFAULTS, **{key: value[key] for key in KNOWN_APP_KEYS if key in value}}
    parsed = {}
    for key in APP_CHORD_KEYS:
        try:
            parsed[key] = parse_chord(resolved[key])
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"Invalid app.{key} {resolved[key]!r} in {path}: {exc}.") from None
    for first, second in itertools.combinations(APP_CHORD_KEYS, 2):
        if parsed[first] is not None and parsed[first] == parsed[second]:
            raise ConfigError(
                f"app.{first} and app.{second} in {path} are the same chord "
                f"({chord_text(parsed[first])}); chords must differ."
            )

    mode = resolved["dictate_mode"]
    if mode not in APP_DICTATE_MODES:
        raise ConfigError(
            f"Invalid app.dictate_mode {mode!r} in {path}: expected one of "
            f"{', '.join(APP_DICTATE_MODES)}."
        )

    stop_hotkey = resolved["stop_hotkey"]
    if stop_hotkey not in APP_STOP_HOTKEY_MODES:
        raise ConfigError(
            f"Invalid app.stop_hotkey {stop_hotkey!r} in {path}: expected one of "
            f"{', '.join(APP_STOP_HOTKEY_MODES)}."
        )


def resolve_app(file_config: dict | None = None) -> dict:
    """The `[app]` settings with defaults filled in, chords canonical.

    `dictate_mode` is passed through as written: 0.13.0 parsed `"hold"`
    but resolved it to toggle with a warning, and 0.13.1 has the hold
    dispatch behind it (`dictate --start` / `--stop`), so the mode the
    file asks for is the mode the app is told about.
    """
    if file_config is None:
        file_config = load_config_file()
    table = file_config.get("app") or {}
    _validate_app_table(table, config_path())
    resolved = dict(APP_DEFAULTS)
    resolved.update({key: table[key] for key in KNOWN_APP_KEYS if key in table})
    for key in APP_CHORD_KEYS:
        resolved[key] = chord_text(parse_chord(resolved[key]))
    return resolved


# --- the [speech] table --------------------------------------------------
#
# Spoken rendering rules (docs/research/2026-09-08-spoken-rendering-rules.md).
# Pure string transformations in preprocess.py, honoured by flatten_markdown.

KNOWN_SPEECH_KEYS = (
    "headings",
    "blockquotes",
    "nested_lists",
    "parentheticals",
    "abbreviations",
    "citations",
    "references",
    "furniture",
    "urls",
    "emoji",
    "bracket_max_chars",
    "furniture_max_chars",
    "furniture_min_repeats",
    "SPEECH_BRACKET_MAX_CHARS",
    "SPEECH_FURNITURE_MAX_CHARS",
    "SPEECH_FURNITURE_MIN_REPEATS",
)

SPEECH_HEADINGS_MODES = ("cue", "plain", "off")
SPEECH_BLOCKQUOTES_MODES = ("cue", "plain", "off")
SPEECH_NESTED_LISTS_MODES = ("cue", "flat", "off")
SPEECH_PARENTHETICALS_MODES = ("pause", "drop", "off")
SPEECH_ABBREVIATIONS_MODES = ("expand", "protect", "off")
SPEECH_CITATIONS_MODES = ("drop", "speak", "off")
SPEECH_URLS_MODES = ("domain", "full", "drop")

SPEECH_BRACKET_MAX_CHARS = 120
SPEECH_FURNITURE_MAX_CHARS = 60
SPEECH_FURNITURE_MIN_REPEATS = 3

SPEECH_DEFAULTS = {
    "headings": "cue",
    "blockquotes": "cue",
    "nested_lists": "cue",
    "parentheticals": "pause",
    "abbreviations": "expand",
    "citations": "drop",
    "references": True,
    "furniture": True,
    "urls": "domain",
    "emoji": True,
    "bracket_max_chars": SPEECH_BRACKET_MAX_CHARS,
    "furniture_max_chars": SPEECH_FURNITURE_MAX_CHARS,
    "furniture_min_repeats": SPEECH_FURNITURE_MIN_REPEATS,
}

SPEECH_BRACKET_CHARS_MIN = 10
SPEECH_BRACKET_CHARS_MAX = 1000
SPEECH_FURNITURE_CHARS_MIN = 10
SPEECH_FURNITURE_CHARS_MAX = 500
SPEECH_FURNITURE_REPEATS_MIN = 2
SPEECH_FURNITURE_REPEATS_MAX = 20


def _validate_speech_table(value, path: Path) -> None:
    """Check the `[speech]` table. Unknown keys warn; bad values raise."""
    if not isinstance(value, dict):
        raise ConfigError(f"config key 'speech' in {path} must be a table")
    for key in value:
        if key not in KNOWN_SPEECH_KEYS:
            _warn(f"vocalize: unknown config key {key!r} in [speech] in {path}")

    modes = {
        "headings": SPEECH_HEADINGS_MODES,
        "blockquotes": SPEECH_BLOCKQUOTES_MODES,
        "nested_lists": SPEECH_NESTED_LISTS_MODES,
        "parentheticals": SPEECH_PARENTHETICALS_MODES,
        "abbreviations": SPEECH_ABBREVIATIONS_MODES,
        "citations": SPEECH_CITATIONS_MODES,
        "urls": SPEECH_URLS_MODES,
    }
    for key, allowed in modes.items():
        v = value.get(key)
        if v is not None and v not in allowed:
            raise ConfigError(
                f"Invalid speech.{key} {v!r} in {path}: expected one of {', '.join(allowed)}."
            )

    bool_keys = ("references", "furniture", "emoji")
    for key in bool_keys:
        v = value.get(key)
        if v is not None and not isinstance(v, bool):
            raise ConfigError(f"Invalid speech.{key} {v!r} in {path}: expected true or false.")

    bracket_chars = value.get("bracket_max_chars")
    if bracket_chars is None:
        bracket_chars = value.get("SPEECH_BRACKET_MAX_CHARS")
    if bracket_chars is not None and (
        isinstance(bracket_chars, bool)
        or not isinstance(bracket_chars, int)
        or not SPEECH_BRACKET_CHARS_MIN <= bracket_chars <= SPEECH_BRACKET_CHARS_MAX
    ):
        raise ConfigError(
            f"Invalid speech.bracket_max_chars {bracket_chars!r} in {path}: expected an integer "
            f"between {SPEECH_BRACKET_CHARS_MIN} and {SPEECH_BRACKET_CHARS_MAX}."
        )

    furn_chars = value.get("furniture_max_chars")
    if furn_chars is None:
        furn_chars = value.get("SPEECH_FURNITURE_MAX_CHARS")
    if furn_chars is not None and (
        isinstance(furn_chars, bool)
        or not isinstance(furn_chars, int)
        or not SPEECH_FURNITURE_CHARS_MIN <= furn_chars <= SPEECH_FURNITURE_CHARS_MAX
    ):
        raise ConfigError(
            f"Invalid speech.furniture_max_chars {furn_chars!r} in {path}: expected an integer "
            f"between {SPEECH_FURNITURE_CHARS_MIN} and {SPEECH_FURNITURE_CHARS_MAX}."
        )

    furn_repeats = value.get("furniture_min_repeats")
    if furn_repeats is None:
        furn_repeats = value.get("SPEECH_FURNITURE_MIN_REPEATS")
    if furn_repeats is not None and (
        isinstance(furn_repeats, bool)
        or not isinstance(furn_repeats, int)
        or not SPEECH_FURNITURE_REPEATS_MIN <= furn_repeats <= SPEECH_FURNITURE_REPEATS_MAX
    ):
        raise ConfigError(
            f"Invalid speech.furniture_min_repeats {furn_repeats!r} in {path}: expected an integer "
            f"between {SPEECH_FURNITURE_REPEATS_MIN} and {SPEECH_FURNITURE_REPEATS_MAX}."
        )


def resolve_speech(file_config: dict | None = None) -> dict:
    """The `[speech]` settings with defaults filled in, re-validated."""
    if file_config is None:
        file_config = load_config_file()
    table = file_config.get("speech") or {}
    _validate_speech_table(table, config_path())
    resolved = dict(SPEECH_DEFAULTS)
    for k in (
        "headings", "blockquotes", "nested_lists", "parentheticals",
        "abbreviations", "citations", "references", "furniture", "urls", "emoji",
    ):
        if k in table:
            resolved[k] = table[k]
    if "bracket_max_chars" in table:
        resolved["bracket_max_chars"] = table["bracket_max_chars"]
    elif "SPEECH_BRACKET_MAX_CHARS" in table:
        resolved["bracket_max_chars"] = table["SPEECH_BRACKET_MAX_CHARS"]

    if "furniture_max_chars" in table:
        resolved["furniture_max_chars"] = table["furniture_max_chars"]
    elif "SPEECH_FURNITURE_MAX_CHARS" in table:
        resolved["furniture_max_chars"] = table["SPEECH_FURNITURE_MAX_CHARS"]

    if "furniture_min_repeats" in table:
        resolved["furniture_min_repeats"] = table["furniture_min_repeats"]
    elif "SPEECH_FURNITURE_MIN_REPEATS" in table:
        resolved["furniture_min_repeats"] = table["SPEECH_FURNITURE_MIN_REPEATS"]

    return resolved


def _coerce_speed(value, source: str) -> float:
    try:
        speed = float(value)
    except (TypeError, ValueError):
        raise ConfigError(
            f"Invalid speed {value!r} from {source}: expected a number "
            f"between {SPEED_MIN} and {SPEED_MAX}."
        ) from None
    if not SPEED_MIN <= speed <= SPEED_MAX:
        raise ConfigError(
            f"Invalid speed {speed} from {source}: must be between {SPEED_MIN} and {SPEED_MAX}."
        )
    return speed


def validate_speed(value, source: str) -> float:
    """Public front door to the speed check, for callers outside this module.

    Same bounds and same message as every other speed the CLI resolves.
    """
    return _coerce_speed(value, source)


def _coerce_overflow(value, source: str) -> str:
    mode = str(value).strip().lower()
    if mode not in OVERFLOW_MODES:
        raise ConfigError(
            f"Invalid overflow mode {value!r} from {source}: "
            f"expected one of: {', '.join(OVERFLOW_MODES)}."
        )
    return mode


def _coerce_max_chars(value, source: str) -> int:
    try:
        chars = int(value)
    except (TypeError, ValueError):
        raise ConfigError(
            f"Invalid max_chars {value!r} from {source}: expected a positive integer."
        ) from None
    if chars <= 0:
        raise ConfigError(f"Invalid max_chars {chars} from {source}: must be positive.")
    return chars


def resolve_overflow(
    overflow: str | None = None,
    max_chars: int | None = None,
    default_max_chars: int | None = None,
    file_config: dict | None = None,
) -> tuple[str, int | None]:
    """Resolve the overflow mode and the character cap it applies to.

    Both follow the same order as every other setting:
    flag > env var > config file > default.

    `default_max_chars` sits below all three — it's a fallback for wrapper
    scripts (the Stop hook) that want a protective cap when the user has
    configured nothing, without overriding anything the user did set.
    A cap of None means no cap. Pass `file_config` (from load_config_file)
    when the caller also resolves other settings, so the file is parsed —
    and its unknown-key warnings printed — once, not per resolver.
    """
    if file_config is None:
        file_config = load_config_file()

    mode = "truncate"
    for value, source in (
        (overflow, "--overflow"),
        (os.environ.get("VOCALIZE_OVERFLOW"), "VOCALIZE_OVERFLOW"),
        (file_config.get("overflow"), f"'overflow' in {config_path()}"),
    ):
        if value is not None:
            mode = _coerce_overflow(value, source)
            break

    cap = default_max_chars
    for value, source in (
        (max_chars, "--max-chars"),
        (os.environ.get("VOCALIZE_MAX_CHARS"), "VOCALIZE_MAX_CHARS"),
        (file_config.get("max_chars"), f"'max_chars' in {config_path()}"),
    ):
        if value is not None:
            cap = _coerce_max_chars(value, source)
            break

    return mode, cap


def resolve_chain(provider: str | None = None, file_config: dict | None = None) -> list[str]:
    """The provider chain to try, in order: flag > env > config file > default.

    `--provider` forces a single-provider, no-fallback chain — it always
    wins and short-circuits every other source. `say` is never appended
    automatically to an explicit chain; the all-providers-failed error is
    what tells the user it was missing.
    """
    if file_config is None:
        file_config = load_config_file()

    file_config = migrate_retired_settings(file_config)
    if "chain" in file_config:
        _validate_chain(file_config["chain"], config_path())
    if provider is not None:
        _validate_provider_name(provider, "--provider")
        return [provider]

    env_value = os.environ.get("VOCALIZE_CHAIN")
    if env_value is not None and env_value.strip():
        names: list[str] = []
        seen: set[str] = set()
        for raw in env_value.split(","):
            name = raw.strip()
            if not name:
                continue
            _validate_provider_name(name, "VOCALIZE_CHAIN")
            if name not in seen:
                seen.add(name)
                names.append(name)
        if not names:
            raise ConfigError(
                "VOCALIZE_CHAIN is empty after parsing — set at least one provider name."
            )
        return names

    file_chain = file_config.get("chain")
    if file_chain:
        return list(file_chain)

    return list(DEFAULT_CHAIN)


def chain_source(provider: str | None = None, file_config: dict | None = None) -> str:
    """Which precedence tier resolve_chain would draw its answer from.

    For the `vocalize chain` setter to report, e.g., "chain (from config
    file): kokoro, say".
    """
    if file_config is None:
        file_config = load_config_file()
    file_config = migrate_retired_settings(file_config)

    if provider is not None:
        return "flag"
    if (os.environ.get("VOCALIZE_CHAIN") or "").strip():
        return "environment"
    if file_config.get("chain"):
        return "config file"
    return "default"


def provider_table(name: str, file_config: dict | None = None) -> dict:
    """The `[providers.<name>]` table, or {} when there isn't one."""
    if file_config is None:
        file_config = load_config_file()
    _validate_provider_name(name, "provider settings")
    file_config = migrate_retired_settings(file_config)
    _validate_providers_table(file_config.get("providers", {}), config_path())
    return (file_config.get("providers") or {}).get(name) or {}


def _first(*values):
    for value in values:
        if value is not None:
            return value
    return None


@dataclass(frozen=True)
class Settings:
    voice_id: str = DEFAULT_VOICE
    model_id: str = DEFAULT_MODEL
    output_format: str = DEFAULT_OUTPUT_FORMAT
    speed: float | None = None
    # Local defaults; hosted provider settings are never reinterpreted.
    provider: str = auth.DEFAULT_PROVIDER
    language: str | None = None


def resolve_provider_settings(
    name: str,
    file_config: dict | None = None,
    *,
    voice_id: str | None = None,
    model_id: str | None = None,
    speed: float | None = None,
    primary: bool = True,
) -> Settings:
    """Build one chain link's Settings.

    Per-key precedence: flag > VOCALIZE_VOICE/MODEL/SPEED env > this
    provider's `[providers.<name>]` table > the provider module's defaults.
    A top-level speed is a retired 0.15 setting and is ignored (DEC-005).

    Flags and VOCALIZE_* env vars are the primary provider's alone —
    `primary=False` (every non-first chain link) ignores them and reads
    only its own table and defaults, same as the plan's "no shared knobs
    down the chain" rule.
    """
    if file_config is None:
        file_config = load_config_file()

    from . import providers  # lazy: avoids a config<->providers import cycle

    table = provider_table(name, file_config)
    defaults = providers.get(name).DEFAULTS

    resolved_voice = _first(
        voice_id if primary else None,
        os.environ.get("VOCALIZE_VOICE") if primary else None,
        table.get("voice"),
        defaults.get("voice"),
    )

    resolved_model = _first(
        model_id if primary else None,
        os.environ.get("VOCALIZE_MODEL") if primary else None,
        table.get("model"),
        defaults.get("model", DEFAULT_MODEL),
    )

    resolved_speed = None
    for value, source in (
        (speed if primary else None, "--speed"),
        (os.environ.get("VOCALIZE_SPEED") if primary else None, "VOCALIZE_SPEED"),
        (table.get("speed"), f"'speed' in [providers.{name}] in {config_path()}"),
    ):
        if value is not None:
            resolved_speed = _coerce_speed(value, source)
            break

    for key, value in (("voice", resolved_voice), ("model", resolved_model)):
        if value is not None and value != "":
            _validate_provider_text(key, value, name, config_path())

    return Settings(
        voice_id=resolved_voice,
        model_id=resolved_model,
        speed=resolved_speed,
        provider=name,
        language=_first(table.get("language"), defaults.get("language")),
    )


def resolve_settings(
    voice_id: str | None = None,
    model_id: str | None = None,
    speed: float | None = None,
    file_config: dict | None = None,
) -> Settings:
    """Build Settings from flag > env var > config file > built-in default.

    Resolve Kokoro explicitly. Multi-provider callers use resolve_provider_settings.
    """
    return resolve_provider_settings(
        "kokoro",
        file_config,
        voice_id=voice_id,
        model_id=model_id,
        speed=speed,
        primary=True,
    )
