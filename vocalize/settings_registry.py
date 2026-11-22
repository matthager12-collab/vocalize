"""The settings registry: one declarative list of every setting vocalize has.

Each `Setting` is one key as written in config.toml (a dotted path for a
key inside a table). The rules a value must meet are not written here a
second time: `validate` checks the type and then hands the value to the
same per-table validator `config.load_config_file` uses, so the loader,
the settings page and `vocalize settings` cannot disagree about what is
allowed. `tests/test_settings_parity.py` fails when this list and the
loader's lists drift apart.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass

from . import auth, config
from .exceptions import ConfigError
from .local import kokoro_manifest, piper_manifest, whisper_manifest

TYPES = ("bool", "int", "float", "str", "enum", "list")
CONTROLS = ("toggle", "select", "number", "text", "chord", "device", "list", "voice",
            "folder", "chain")
CHOICE_SOURCES = ("kokoro_voices", "piper_voices", "say_voices", "input_devices",
                  "whisper_models", "tts_engines", "stt_engines")
RESTARTS = (None, "app", "warm")

#: The controls that can edit a value of each type.
CONTROLS_FOR_TYPE = {
    "bool": ("toggle",),
    "int": ("number",),
    "float": ("number",),
    "str": ("text", "chord", "device", "voice", "folder"),
    "enum": ("select",),
    "list": ("list", "chain"),
}


@dataclass(frozen=True)
class Group:
    id: str
    label: str
    help: str
    order: int


@dataclass(frozen=True)
class Setting:
    key: str
    type: str
    default: object
    label: str
    help: str
    group: str
    control: str
    choices: tuple | None = None
    choices_from: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    max_length: int | None = None
    max_items: int | None = None
    applies_when: tuple | None = None
    restart: str | None = None
    printed: bool = True
    print_name: str | None = None


@dataclass(frozen=True)
class Retired:
    key: str
    advice: str


GROUPS = (
    Group("speaking", "Speaking", "Which voice engines read text aloud, and how they sound.", 1),
    Group("reading", "Reading", "How long text is handled and how written text is turned "
          "into speech.", 2),
    Group("dictation", "Dictation", "What happens when you dictate: cues, cleanup, pasting "
          "and recording limits.", 3),
    Group("transcription", "Transcription", "Where `vocalize transcribe` writes its "
          "transcripts and what it keeps.", 4),
    Group("hotkeys", "Hotkeys", "The menu-bar app's keyboard shortcuts.", 5),
    Group("engines", "Engines", "Which speech-to-text engine and model run on this Mac.", 6),
    Group("advanced", "Advanced", "Fine tuning most people never need to change.", 7),
)

_TEXT = config.PROVIDER_TEXT_MAX_CHARS
_STT = config.STT_DEFAULTS
_SPEECH = config.SPEECH_DEFAULTS
_APP = config.APP_DEFAULTS
_TRANSCRIBE = config.TRANSCRIBE_DEFAULTS
_VOICE_SOURCES = {"kokoro": "kokoro_voices", "piper": "piper_voices", "say": "say_voices"}
#: The engine defaults `providers.<name>.DEFAULTS` holds, read from the
#: manifests so this module does not import the engine adapters.
_PROVIDER_DEFAULTS = {
    "kokoro": {"voice": kokoro_manifest.DEFAULT_VOICE,
               "language": kokoro_manifest.DEFAULT_LANGUAGE, "speed": None},
    "piper": {"voice": piper_manifest.DEFAULT_VOICE, "speed": None},
    "say": {"voice": None, "speed": None},
}


def _provider_settings() -> tuple[Setting, ...]:
    out = []
    for name in auth.PROVIDER_NAMES:
        label = auth.PROVIDER_LABELS[name]
        defaults = _PROVIDER_DEFAULTS[name]
        # The kokoro lines keep their 0.x spelling: voice=, speed=.
        legacy = name == auth.DEFAULT_PROVIDER
        out += [
            Setting(f"providers.{name}.voice", "str", defaults["voice"], f"{label} voice",
                    f"The voice {label} speaks with. Unset uses the engine's own default.",
                    "speaking", "voice", choices_from=_VOICE_SOURCES[name], max_length=_TEXT,
                    print_name="voice" if legacy else None),
            Setting(f"providers.{name}.speed", "float", defaults["speed"], f"{label} speed",
                    f"How fast {label} speaks; 1.0 is normal speed. Unset uses the "
                    "engine's own pace.", "speaking", "number", minimum=config.SPEED_MIN,
                    maximum=config.SPEED_MAX, step=0.05,
                    print_name="speed" if legacy else None),
        ]
        if "language" in config.KNOWN_PROVIDER_KEYS[name]:
            out.append(
                Setting(f"providers.{name}.language", "str", defaults["language"],
                        f"{label} language",
                        f"The language code {label} is asked to speak. Unset uses the "
                        "engine's own default.", "advanced", "text", max_length=_TEXT))
    return tuple(out)


def _speech_mode(key: str, label: str, help: str, choices: tuple) -> Setting:
    return Setting(f"speech.{key}", "enum", _SPEECH[key], label, help, "reading", "select",
                   choices=choices)


SETTINGS: tuple[Setting, ...] = (
    # --- speaking ---------------------------------------------------------
    Setting("chain", "list", list(config.DEFAULT_CHAIN), "Voice engines, in order",
            "The engines tried in turn to speak text; the next one is used when one is "
            "not installed or fails.", "speaking", "chain",
            choices=auth.PROVIDER_NAMES, choices_from="tts_engines",
            max_items=len(auth.PROVIDER_NAMES)),
    *_provider_settings(),
    # --- reading ----------------------------------------------------------
    Setting("max_chars", "int", None, "Longest read",
            "The most characters read in one go. Unset means no limit.",
            "reading", "number", minimum=1),
    Setting("overflow", "enum", "truncate", "When text is too long",
            "What happens when text is longer than the longest read: cut it, ask "
            "first, or refuse it.", "reading", "select", choices=config.OVERFLOW_MODES),
    _speech_mode("headings", "Headings", "How headings are read: with a spoken cue, as "
                 "plain text, or skipped.", config.SPEECH_HEADINGS_MODES),
    _speech_mode("blockquotes", "Quotes", "How quoted blocks are read: with a spoken cue, "
                 "as plain text, or skipped.", config.SPEECH_BLOCKQUOTES_MODES),
    _speech_mode("nested_lists", "Nested lists", "How lists inside lists are read: with a "
                 "cue, flattened, or skipped.", config.SPEECH_NESTED_LISTS_MODES),
    _speech_mode("parentheticals", "Brackets", "How text in brackets is read: with a "
                 "pause around it, dropped, or read as written.",
                 config.SPEECH_PARENTHETICALS_MODES),
    _speech_mode("abbreviations", "Abbreviations", "Whether abbreviations such as e.g. "
                 "are expanded into words, kept whole, or left alone.",
                 config.SPEECH_ABBREVIATIONS_MODES),
    _speech_mode("citations", "Citations", "Whether citation and footnote marks are "
                 "dropped, spoken, or left alone.", config.SPEECH_CITATIONS_MODES),
    Setting("speech.references", "bool", _SPEECH["references"], "Skip reference lists",
            "Skip a references or bibliography section at the end of a document.",
            "reading", "toggle"),
    Setting("speech.furniture", "bool", _SPEECH["furniture"], "Skip page furniture",
            "Skip running heads, footers and page numbers that repeat through a document.",
            "reading", "toggle"),
    _speech_mode("urls", "Web links", "How a web address is read: just its site name, in "
                 "full, or not at all.", config.SPEECH_URLS_MODES),
    Setting("speech.emoji", "bool", _SPEECH["emoji"], "Remove emoji",
            "Remove emoji before reading, so they are not spoken.", "reading", "toggle"),
    # --- dictation --------------------------------------------------------
    Setting("stt.paste", "bool", _STT["paste"], "Paste after dictating",
            "Paste the transcript into the app you were using when dictation ends.",
            "dictation", "toggle"),
    Setting("stt.cues", "enum", _STT["cues"], "Start and stop cues",
            "How dictation tells you it started and stopped: sounds, spoken words, or both.",
            "dictation", "select", choices=config.STT_CUE_MODES),
    Setting("stt.sounds", "bool", _STT["sounds"], "Play cues",
            "Turn the start and stop cues on or off.", "dictation", "toggle"),
    Setting("stt.verbatim", "bool", _STT["verbatim"], "Keep every word",
            "Keep filler words and false starts instead of tidying the transcript.",
            "dictation", "toggle"),
    Setting("stt.cleanup", "enum", _STT["cleanup"], "Clean up transcripts",
            "Tidy a transcript with a local language model on this Mac, or leave it as "
            "heard.", "dictation", "select", choices=config.STT_CLEANUP_BACKENDS),
    Setting("stt.vocabulary", "list", list(_STT["vocabulary"]), "Words to recognise",
            "Names and terms to spell right. They are corrected in the transcript after "
            "either engine, and Whisper also gets them as a hint.", "dictation", "list", max_length=config._VOCABULARY_MAX_CHARS,
            max_items=config._VOCABULARY_MAX_ITEMS),
    Setting("stt.max_seconds", "int", _STT["max_seconds"], "Longest recording",
            "Recording stops by itself after this many seconds.", "dictation", "number",
            minimum=config.STT_MAX_SECONDS_MIN, maximum=config.STT_MAX_SECONDS_MAX, step=1),
    Setting("stt.max_take_seconds", "int", _STT["max_take_seconds"], "Longest paused take",
            "The most seconds one dictation may last in total, counting every pause and "
            "resume.", "dictation", "number", minimum=config.STT_MAX_TAKE_SECONDS_MIN,
            maximum=config.STT_MAX_TAKE_SECONDS_MAX, step=1),
    Setting("stt.input_device", "str", _STT["input_device"], "Microphone",
            "The microphone to record from. Empty uses the system default.",
            "dictation", "device", choices_from="input_devices",
            max_length=config.STT_DEVICE_MAX_CHARS),
    # --- transcription ----------------------------------------------------
    Setting("transcribe.folder", "str", _TRANSCRIBE["folder"], "Transcript folder",
            "The folder `vocalize transcribe` writes its transcripts to.",
            "transcription", "folder", max_length=config.TRANSCRIBE_FOLDER_MAX_CHARS),
    Setting("transcribe.keep_audio", "bool", _TRANSCRIBE["keep_audio"], "Keep the audio",
            "Keep a copy of each recording next to its transcript.",
            "transcription", "toggle"),
    Setting("transcribe.model", "enum", _TRANSCRIBE["model"], "Transcription model",
            "The Whisper model for transcribing files. Empty uses the dictation model.",
            "transcription", "select", choices=("", *whisper_manifest.MODELS),
            choices_from="whisper_models"),
    # --- hotkeys ----------------------------------------------------------
    Setting("app.dictate", "str", _APP["dictate"], "Dictate shortcut",
            "Starts and stops dictation. Must include ctrl or cmd; empty turns it off.",
            "hotkeys", "chord", restart="app"),
    Setting("app.dictate_mode", "enum", _APP["dictate_mode"], "Dictate shortcut mode",
            "Press once to start and again to stop, or hold the shortcut while you speak.",
            "hotkeys", "select", choices=config.APP_DICTATE_MODES, restart="app"),
    Setting("app.speak", "str", _APP["speak"], "Speak shortcut",
            "Reads the selected text aloud. Must include ctrl or cmd; empty turns it off.",
            "hotkeys", "chord", restart="app"),
    Setting("app.stop", "str", _APP["stop"], "Stop shortcut",
            "Stops reading. Must include ctrl or cmd; empty turns it off.",
            "hotkeys", "chord", restart="app"),
    Setting("app.stop_hotkey", "enum", _APP["stop_hotkey"], "What the stop shortcut does",
            "Stop reading for good, or pause it so it can be resumed.",
            "hotkeys", "select", choices=config.APP_STOP_HOTKEY_MODES, restart="app"),
    # --- engines ----------------------------------------------------------
    Setting("stt.engine", "enum", _STT["engine"], "Dictation engine",
            "Which speech-to-text engine runs. Auto uses Parakeet on Apple silicon where "
            "it is installed, and Whisper everywhere else.", "engines", "select",
            choices=config.STT_ENGINES, choices_from="stt_engines", restart="warm"),
    Setting("stt.model", "enum", _STT["model"], "Whisper model",
            "The Whisper model used for dictation. Larger models are more accurate and "
            "slower.", "engines", "select", choices=whisper_manifest.MODELS,
            choices_from="whisper_models", restart="warm"),
    Setting("stt.language", "enum", _STT["language"], "Spoken language",
            "The language you dictate in, or auto to detect it. English-only models "
            "need en.", "engines", "select", choices=whisper_manifest.LANGUAGES),
    Setting("stt.warm_minutes", "int", _STT["warm_minutes"], "Keep models loaded",
            "Minutes Whisper and the cleanup model stay loaded after a take, so the next "
            "one starts at once. Parakeet is never kept loaded: with Parakeet this keeps "
            "only the cleanup model, when cleanup is on. 0 keeps nothing loaded.",
            "engines", "number",
            minimum=config.STT_WARM_MINUTES_MIN, maximum=config.STT_WARM_MINUTES_MAX,
            step=1, restart="warm"),
    # --- advanced ---------------------------------------------------------
    Setting("stt.beam_size", "int", _STT["beam_size"], "Whisper beam size",
            "How many guesses Whisper weighs at once. Higher can be more accurate and is "
            "slower; 1 is the fastest.", "advanced", "number",
            minimum=config.STT_BEAM_SIZE_MIN, maximum=config.STT_BEAM_SIZE_MAX, step=1),
    Setting("speech.bracket_max_chars", "int", _SPEECH["bracket_max_chars"],
            "Longest bracket handled",
            "Brackets longer than this many characters are read as written.",
            "advanced", "number", minimum=config.SPEECH_BRACKET_CHARS_MIN,
            maximum=config.SPEECH_BRACKET_CHARS_MAX, step=1),
    Setting("speech.furniture_max_chars", "int", _SPEECH["furniture_max_chars"],
            "Longest furniture line",
            "Only lines up to this many characters can count as page furniture.",
            "advanced", "number", minimum=config.SPEECH_FURNITURE_CHARS_MIN,
            maximum=config.SPEECH_FURNITURE_CHARS_MAX, step=1,
            applies_when=("speech.furniture", True)),
    Setting("speech.furniture_min_repeats", "int", _SPEECH["furniture_min_repeats"],
            "Repeats that make furniture",
            "A line must repeat at least this many times to be skipped as page furniture.",
            "advanced", "number", minimum=config.SPEECH_FURNITURE_REPEATS_MIN,
            maximum=config.SPEECH_FURNITURE_REPEATS_MAX, step=1,
            applies_when=("speech.furniture", True)),
)

#: Older spellings the loader still reads. A table alias is read under the
#: new table (the new one wins); a key alias is read when the new key is
#: absent. Neither is a separate setting.
TABLE_ALIASES = {"notes": "transcribe"}
KEY_ALIASES = {
    "speech.SPEECH_BRACKET_MAX_CHARS": "speech.bracket_max_chars",
    "speech.SPEECH_FURNITURE_MAX_CHARS": "speech.furniture_max_chars",
    "speech.SPEECH_FURNITURE_MIN_REPEATS": "speech.furniture_min_repeats",
}


def _retired() -> tuple[Retired, ...]:
    out = [Retired(key, advice) for key, advice in config._RETIRED_ROOT_KEYS.items()]
    out += [Retired(f"providers.{name}", "cloud engines were removed; delete the table")
            for name in config.CLOUD_PROVIDER_NAMES]
    out += [Retired(f"providers.{name}.{key}", config._REMOVE_IT)
            for name in auth.PROVIDER_NAMES for key in config._RETIRED_PROVIDER_KEYS]
    out += [Retired(f"providers.{name}.{key}", config._UNUSED_IT)
            for name, keys in config._RETIRED_ENGINE_KEYS.items() for key in keys]
    out += [Retired(f"{section}.{key}", "summaries were removed; delete it")
            for section in ("transcribe", "notes") for key in config._RETIRED_TRANSCRIBE_KEYS]
    return tuple(out)


RETIRED: tuple[Retired, ...] = _retired()

_BY_KEY = {setting.key: setting for setting in SETTINGS}


def _tables() -> tuple[str, ...]:
    seen: list[str] = []
    for setting in SETTINGS:
        if "." in setting.key and setting.key.split(".")[0] not in seen:
            seen.append(setting.key.split(".")[0])
    return tuple(seen)


#: Every top-level table a registered key lives in, in registry order.
TABLES: tuple[str, ...] = _tables()
#: Registered keys at the top of the file, outside any table.
ROOT_KEYS: tuple[str, ...] = tuple(s.key for s in SETTINGS if "." not in s.key)


def by_key(key: str) -> Setting:
    """The record for `key`. KeyError when vocalize has no such setting."""
    return _BY_KEY[key]


def defaults() -> dict:
    """Every key mapped to its default (a fresh copy of any list)."""
    return {s.key: copy.copy(s.default) for s in SETTINGS}


def values(file_config: dict) -> dict:
    """Every key mapped to the value the file gives it, or its default.

    The file only: environment variables and flags are not applied, because
    this is what the settings page shows and saves. Old spellings, retired
    keys and legacy values resolve the way the loader resolves them.
    """
    data = config.migrate_retired_settings(file_config)
    path = config.config_path()
    out = defaults()
    if data.get("max_chars") is not None:
        out["max_chars"] = config._coerce_max_chars(data["max_chars"], f"'max_chars' in {path}")
    if data.get("overflow") is not None:
        out["overflow"] = config._coerce_overflow(data["overflow"], f"'overflow' in {path}")
    if data.get("chain"):
        config._validate_chain(data["chain"], path)
        out["chain"] = list(data["chain"])
    for name in auth.PROVIDER_NAMES:
        table = config.provider_table(name, data)
        for key in config.KNOWN_PROVIDER_KEYS[name]:
            if table.get(key) is None:
                continue
            value = table[key]
            if key == "speed":
                value = config._coerce_speed(
                    value, f"'speed' in [providers.{name}] in {path}")
            out[f"providers.{name}.{key}"] = value
    for table, resolved in (
        ("stt", config.resolve_stt(data)),
        ("transcribe", config.resolve_transcribe(data)),
        ("app", config.resolve_app(data)),
        ("speech", config.resolve_speech(data)),
    ):
        for key, value in resolved.items():
            if f"{table}.{key}" in _BY_KEY:
                out[f"{table}.{key}"] = copy.copy(value)
    return out


def _check_type(setting: Setting, value) -> object:
    kind = setting.type
    wrong = None
    if kind == "bool" and not isinstance(value, bool):
        wrong = "true or false"
    elif kind == "int" and (isinstance(value, bool) or not isinstance(value, int)):
        wrong = "a whole number"
    elif kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            wrong = "a number"
        else:
            value = float(value)
    elif kind == "str" and not isinstance(value, str):
        wrong = "text"
    elif kind == "list" and not isinstance(value, list):
        wrong = "a list"
    # "enum" is left to the loader's own rule, which lists the choices.
    if wrong:
        raise ConfigError(f"Invalid {setting.key} {value!r}: expected {wrong}.")
    return value


def validate(key: str, value):
    """`value` normalised for `key`, or ConfigError saying what to change.

    Checks the value on its own. Rules between two keys (distinct chords,
    an English-only model with a language) are checked by `apply_changes`
    on the whole table.
    """
    try:
        setting = by_key(key)
    except KeyError:
        raise ConfigError(f"Unknown setting {key!r}.") from None
    value = _check_type(setting, value)
    path = config.config_path()
    parts = key.split(".")
    if key == "max_chars":
        return config._coerce_max_chars(value, f"'max_chars' in {path}")
    if key == "overflow":
        return config._coerce_overflow(value, f"'overflow' in {path}")
    if key == "chain":
        config._validate_chain(value, path)
        return list(value)
    table, sub = parts[0], parts[-1]
    if table == "providers":
        if sub == "speed":
            return config._coerce_speed(value, f"'speed' in [providers.{parts[1]}] in {path}")
        config._validate_provider_text(sub, value, parts[1], path)
        return value
    if table == "app" and sub in config.APP_CHORD_KEYS:
        try:
            return config.chord_text(config.parse_chord(value))
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"Invalid {key} {value!r}: {exc}.") from None
    validator = _TABLE_VALIDATORS[table]
    validator({sub: value}, path)
    if key == "stt.cleanup" and value is False:  # 0.10.x wrote false
        return "off"
    return copy.copy(value)


_TABLE_VALIDATORS = {
    "stt": config._validate_stt_table,
    "transcribe": config._validate_transcribe_table,
    "app": config._validate_app_table,
    "speech": config._validate_speech_table,
}


def _aliases_of(key: str) -> list[list[str]]:
    """The other paths in a file that can also set `key`."""
    out = [alias.split(".") for alias, target in KEY_ALIASES.items() if target == key]
    table, _, rest = key.partition(".")
    for old, new in TABLE_ALIASES.items():
        if new == table and rest:
            out.append([old, *rest.split(".")])
    return out


def _remove(data: dict, parts: list[str]) -> None:
    """Delete the key at `parts`, and any table that leaves empty."""
    trail = [data]
    for part in parts[:-1]:
        node = trail[-1].get(part)
        if not isinstance(node, dict):
            return
        trail.append(node)
    trail[-1].pop(parts[-1], None)
    for depth in range(len(parts) - 1, 0, -1):
        if not trail[depth]:
            del trail[depth - 1][parts[depth - 1]]


def apply_changes(file_config: dict, changes: dict) -> dict:
    """A new config dict with `changes` applied; the input is not changed.

    `key: value` is validated and set; `key: None` removes the key (and any
    older spelling of it) so the default applies. Unrelated and unknown
    keys are kept. Each changed table is then checked whole, so a pair of
    equal chords is refused even when the two came in one change.
    """
    data = copy.deepcopy(file_config)
    touched: set[str] = set()
    for key, value in changes.items():
        if key not in _BY_KEY:
            raise ConfigError(f"Unknown setting {key!r}.")
        parts = key.split(".")
        if value is not None:
            value = validate(key, value)
        for alias in _aliases_of(key):
            _remove(data, alias)
        if value is None:
            _remove(data, parts)
            continue
        node = data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise ConfigError(
                    f"Cannot set {key}: {part!r} in the config file is not a table.")
        node[parts[-1]] = value
        touched.add(parts[0])
    path = config.config_path()
    for table in touched & set(_TABLE_VALIDATORS):
        _TABLE_VALIDATORS[table](data[table], path)
    return data


def _field(value):
    if isinstance(value, tuple):
        return [_field(item) for item in value]
    if isinstance(value, list):
        return [_field(item) for item in value]
    return value


def schema() -> dict:
    """Groups and every setting, every field, as JSON-ready data."""
    return {
        "groups": [asdict(group) for group in sorted(GROUPS, key=lambda g: g.order)],
        "settings": [{name: _field(getattr(s, name)) for name in Setting.__dataclass_fields__}
                     for s in SETTINGS],
    }


def _text(value) -> str:
    """A value as `vocalize settings` prints it."""
    if value is None:
        return "unset"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value)
    return str(value)


#: The lines other programs parse, in the order they have always printed.
#: `None` marks where the fixed `notes.summarizer=off` line goes.
_LEGACY_ORDER = (
    "providers.kokoro.voice", "providers.kokoro.speed",
    "max_chars", "overflow", "chain",
    "stt.model", "stt.language", "stt.beam_size", "stt.cleanup", "stt.verbatim",
    "stt.max_seconds", "stt.max_take_seconds", "stt.cues", "stt.paste",
    None,
    "app.dictate", "app.dictate_mode", "app.speak", "app.stop", "app.stop_hotkey",
)
#: Summaries were removed; a caller still parses this line (DEC-007).
SUMMARIZER_LINE = "notes.summarizer=off"


def settings_lines(file_config: dict) -> list[str]:
    """The lines `vocalize settings` prints.

    The kokoro voice and speed, the long-read cap and mode, and the
    chain apply the environment the way speaking does, as they always have.
    Every other value is the file's or the default.
    """
    resolved = values(file_config)
    kokoro = config.resolve_settings(file_config=file_config)
    mode, cap = config.resolve_overflow(file_config=file_config)
    resolved.update({
        "providers.kokoro.voice": kokoro.voice_id,
        "providers.kokoro.speed": kokoro.speed,
        "max_chars": cap,
        "overflow": mode,
        "chain": config.resolve_chain(None, file_config),
    })
    order = list(_LEGACY_ORDER) + [s.key for s in SETTINGS if s.key not in _LEGACY_ORDER]
    lines = []
    for key in order:
        if key is None:
            lines.append(SUMMARIZER_LINE)
            continue
        setting = _BY_KEY[key]
        if setting.printed:
            lines.append(f"{setting.print_name or key}={_text(resolved[key])}")
    return lines


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _default_cell(setting: Setting) -> str:
    value = setting.default
    if value is None:
        return "unset"
    if isinstance(value, bool):
        return "`true`" if value else "`false`"
    if isinstance(value, list):
        return "`[" + ", ".join(f'"{item}"' for item in value) + "]`"
    if isinstance(value, str):
        return f'`"{value}"`'
    return f"`{value}`"


def _allowed_cell(setting: Setting) -> str:
    if setting.choices is not None and len(setting.choices) <= 12:
        return ", ".join(f'`"{c}"`' if isinstance(c, str) else f"`{c}`"
                         for c in setting.choices)
    parts = []
    if setting.choices is not None:
        parts.append(f"one of {len(setting.choices)} names")
    if setting.minimum is not None or setting.maximum is not None:
        low = "" if setting.minimum is None else setting.minimum
        high = "" if setting.maximum is None else setting.maximum
        parts.append(f"{low} to {high}" if high != "" else f"at least {low}")
    if setting.type == "bool":
        parts.append("`true`, `false`")
    if setting.max_items is not None:
        parts.append(f"up to {setting.max_items} items")
    if setting.max_length is not None:
        parts.append(f"up to {setting.max_length} characters")
    if setting.control == "chord":
        parts.append("a chord such as `ctrl+alt+cmd+d`, or empty")
    return "; ".join(parts) or "text"


def readme_table() -> str:
    """The README's settings table: key, default, allowed values and help, by group."""
    out = []
    for group in sorted(GROUPS, key=lambda g: g.order):
        out += [f"### {group.label}", "", group.help, "",
                "| Key | Default | Allowed | What it does |", "|---|---|---|---|"]
        for s in SETTINGS:
            if s.group == group.id:
                out.append(f"| `{s.key}` | {_default_cell(s)} | {_cell(_allowed_cell(s))} "
                           f"| {_cell(s.help)} |")
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


