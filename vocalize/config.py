"""Configuration loading: API key resolution, config file, and defaults.

API key resolution order (first one found wins):
  1. --api-key flag passed explicitly on the CLI
  2. ELEVENLABS_API_KEY environment variable
  3. A .env file in the current directory (loaded via python-dotenv,
     if it's installed — this is an optional dependency)
  4. The OS keychain, written by `vocalize auth login`

Voice, model and speed are resolved per setting, in this order:
  CLI flag > environment variable > config file > built-in default
"""

from __future__ import annotations

import itertools
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from . import auth
from .exceptions import ConfigError, MissingAPIKeyError

DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"  # "Rachel" — a stock ElevenLabs voice
DEFAULT_MODEL = "eleven_multilingual_v2"
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"

# The range the ElevenLabs API accepts; 1.0 is normal speed.
SPEED_MIN = 0.7
SPEED_MAX = 1.2

KNOWN_CONFIG_KEYS = (
    "voice", "model", "speed", "max_chars", "overflow", "chain", "providers", "stt", "notes", "app",
)

# Keys inside a [providers.<name>] table that become a request field or an
# argument: short printable strings, or the file is refused (issue #5).
PROVIDER_TEXT_KEYS = ("voice", "model", "engine", "language", "region", "profile")
PROVIDER_TEXT_MAX_CHARS = 128

# Keys allowed inside a [providers.<name>] table.
KNOWN_PROVIDER_KEYS = (
    "voice",
    "model",
    "engine",
    "language",
    "region",
    "profile",
    "monthly_chars",
    "speed",
)

# What to do when input is longer than the resolved character cap.
OVERFLOW_MODES = ("truncate", "ask", "never")

# Keys allowed inside the [stt] table (DEC-006, design § [stt] config table).
KNOWN_STT_KEYS = (
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
)

# Where a dictation's cleanup pass runs. `off` is the default: a press must
# never surprise with a pause or reworded text. `local` is accepted from
# 0.12.0 so a 0.14 config never breaks an older binary; llm.py refuses it
# at run time until the model ships. A legacy bool is coerced: true was
# `claude -p`, false was off.
STT_CLEANUP_BACKENDS = ("off", "local", "claude-cli", "anthropic")

# What `cues` may be: the fixed system sounds, spoken words instead, or both.
STT_CUE_MODES = ("sounds", "words", "both")

# `paste` is reserved by DEC-006 and deliberately does nothing in 0.10.0.
STT_DEFAULTS = {
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
}

# The recorder self-stops at max_seconds and `dictate` backstops it, so this
# is a real resource bound, not a cosmetic one.
STT_MAX_SECONDS_MIN = 1
STT_MAX_SECONDS_MAX = 600

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

# chain = ["kokoro", "say"]: the on-device voice first, degrading to the
# always-present `say` instead of erroring — so a keyless fresh Mac still
# speaks, and the fallback line names the install that is missing (DEC-022).
DEFAULT_CHAIN = ("kokoro", "say")


def _load_dotenv_if_present() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        return
    # Explicit path, not dotenv's default search: bare load_dotenv() walks
    # up from the install directory, so a console-script install would miss
    # the user's project .env and could pick up an unrelated one.
    load_dotenv(dotenv_path=Path.cwd() / ".env")


def resolve_provider_key(provider: str, explicit: str | None = None) -> str:
    """Find `provider`'s API key: flag, env var, .env, then the keychain.

    The keychain comes last on purpose: a project that pins its own key in
    a .env file must not be overridden by a machine-wide stored one.

    Providers with no key slot at all (Polly authenticates through boto3,
    `say` needs nothing) fall straight through to MissingAPIKeyError,
    whose message names the right command for the provider.

    Raises MissingAPIKeyError if none is found.
    """
    if explicit:
        return explicit

    _load_dotenv_if_present()

    env_var = auth.PROVIDER_ENV_VARS.get(provider)
    if env_var:
        key = os.environ.get(env_var)
        if key:
            return key

    if provider in auth.PROVIDER_USERNAMES:
        key = auth.stored_key(provider)
        if key:
            return key

    raise MissingAPIKeyError(provider)


def resolve_api_key(explicit: str | None = None) -> str:
    """The ElevenLabs key. Kept as the name every existing caller uses."""
    return resolve_provider_key("elevenlabs", explicit)


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

    for key in data:
        if key not in KNOWN_CONFIG_KEYS:
            _warn(f"vocalize: unknown config key {key!r} in {path}")

    if "chain" in data:
        _validate_chain(data["chain"], path)
    if "providers" in data:
        _validate_providers_table(data["providers"], path)
    if "stt" in data:
        _validate_stt_table(data["stt"], path)
    if "notes" in data:
        _validate_notes_table(data["notes"], path)
    if "app" in data:
        _validate_app_table(data["app"], path)

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


def _validate_monthly_chars(value, provider_name: str, path: Path) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(
            f"Invalid monthly_chars {value!r} for provider {provider_name!r} in {path}: "
            f"expected a non-negative integer."
        )


def _validate_providers_table(value, path: Path) -> None:
    if not isinstance(value, dict) or not all(isinstance(table, dict) for table in value.values()):
        raise ConfigError("config key 'providers' must be a table of provider tables")

    for name, table in value.items():
        if name not in auth.PROVIDER_NAMES and name not in auth.KEY_SLOTS:
            _warn(
                f"vocalize: unknown provider {name!r} under 'providers' in {path}. "
                f"Known: {', '.join(auth.PROVIDER_NAMES + ('anthropic',))}"
            )
        for key, val in table.items():
            if key not in KNOWN_PROVIDER_KEYS:
                _warn(
                    f"vocalize: unknown config key {key!r} in [providers.{name}] in {path}"
                )
            if key == "monthly_chars":
                _validate_monthly_chars(val, name, path)
            elif key in PROVIDER_TEXT_KEYS:
                _validate_provider_text(key, val, name, path)


def _validate_provider_text(key: str, val, name: str, path: Path) -> None:
    """A provider's voice, model, engine, language, region or profile is a
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
    if cleanup is not None and not isinstance(cleanup, bool) and cleanup not in STT_CLEANUP_BACKENDS:
        raise ConfigError(
            f"Invalid stt.cleanup {cleanup!r} in {path}: expected one of "
            f"{', '.join(STT_CLEANUP_BACKENDS)} (or true/false from older configs)."
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
    table = file_config.get("stt") or {}
    _validate_stt_table(table, config_path())
    resolved = dict(STT_DEFAULTS)
    resolved.update({key: table[key] for key in KNOWN_STT_KEYS if key in table})
    if isinstance(resolved["cleanup"], bool):  # 0.10.x wrote true/false
        resolved["cleanup"] = "claude-cli" if resolved["cleanup"] else "off"
    return resolved


# --- [notes] --------------------------------------------------------------
#
# Parsed, validated and rendered from 0.12.0; honoured by `vocalize notes`
# from 0.14.0. Kept here so a config written for 0.14 round-trips through
# every earlier writer of the file.

KNOWN_NOTES_KEYS = ("folder", "template", "summarizer", "keep_audio", "model")
NOTES_SUMMARIZERS = ("local", "claude-cli", "anthropic", "off")
NOTES_TEMPLATES = ("memo", "meeting", "lecture", "journal")
NOTES_DEFAULTS = {
    "folder": "~/Documents/Vocalize Notes",
    "template": "memo",
    "summarizer": "local",  # only claude-cli and anthropic leave the machine
    "keep_audio": False,
    "model": "",  # "" = the [stt] model; else one of whisper_manifest.MODELS
}
NOTES_FOLDER_MAX_CHARS = 512


def _validate_notes_table(value, path: Path) -> None:
    """Check the `[notes]` table. Unknown keys warn; bad values raise."""
    if not isinstance(value, dict):
        raise ConfigError(f"config key 'notes' in {path} must be a table")
    for key in value:
        if key not in KNOWN_NOTES_KEYS:
            _warn(f"vocalize: unknown config key {key!r} in [notes] in {path}")

    folder = value.get("folder")
    if folder is not None and (
        not isinstance(folder, str)
        or not folder.strip()
        or len(folder) > NOTES_FOLDER_MAX_CHARS
        or not folder.isprintable()
    ):
        raise ConfigError(
            f"Invalid notes.folder {folder!r} in {path}: expected a non-empty path."
        )

    template = value.get("template")
    if template is not None and (
        not isinstance(template, str)
        or not template.isprintable()
        or (template not in NOTES_TEMPLATES and not template.endswith(".md"))
        or template.startswith("-")  # a file name that reads as a flag
    ):
        raise ConfigError(
            f"Invalid notes.template {template!r} in {path}: expected one of "
            f"{', '.join(NOTES_TEMPLATES)} or a path to a .md prompt file."
        )

    summarizer = value.get("summarizer")
    if summarizer is not None and summarizer not in NOTES_SUMMARIZERS:
        raise ConfigError(
            f"Invalid notes.summarizer {summarizer!r} in {path}: expected one of "
            f"{', '.join(NOTES_SUMMARIZERS)}."
        )

    keep = value.get("keep_audio")
    if keep is not None and not isinstance(keep, bool):
        raise ConfigError(f"Invalid notes.keep_audio {keep!r} in {path}: expected true or false.")

    model = value.get("model")
    if model is not None:
        from .local import whisper_manifest

        if not isinstance(model, str) or (model != "" and model not in whisper_manifest.MODELS):
            raise ConfigError(
                f"Invalid notes.model {model!r} in {path}: expected \"\" (the [stt] model) "
                f"or one of {', '.join(whisper_manifest.MODELS)}."
            )


def resolve_notes(file_config: dict | None = None) -> dict:
    """The `[notes]` settings with defaults filled in, re-validated."""
    if file_config is None:
        file_config = load_config_file()
    table = file_config.get("notes") or {}
    _validate_notes_table(table, config_path())
    resolved = dict(NOTES_DEFAULTS)
    resolved.update({key: table[key] for key in KNOWN_NOTES_KEYS if key in table})
    return resolved


# --- the [app] table -----------------------------------------------------
#
# The menu-bar app's chords (0.13.0, design.md § The [app] table). The app
# reads them from `vocalize settings` as `app.<key>=…` lines, so what is
# printed is the canonical form: modifiers in one fixed order, lowercase,
# aliases resolved. The Swift side keeps its own keycode table; a test
# holds the two allowlists equal.

KNOWN_APP_KEYS = ("dictate", "dictate_mode", "speak", "stop")
APP_CHORD_KEYS = ("dictate", "speak", "stop")
APP_DICTATE_MODES = ("toggle", "hold")
APP_DEFAULTS = {
    "dictate": "ctrl+alt+cmd+d",
    "dictate_mode": "toggle",  # "hold" parses from 0.13.0, works from 0.13.1
    "speak": "ctrl+alt+cmd+s",
    "stop": "ctrl+alt+cmd+x",
}
CHORD_MODIFIERS = ("ctrl", "alt", "cmd", "shift")  # the canonical order
_CHORD_ALIASES = {"control": "ctrl", "option": "alt", "command": "cmd"}
CHORD_KEYS = (
    tuple("abcdefghijklmnopqrstuvwxyz")
    + tuple("0123456789")
    + tuple(f"f{n}" for n in range(1, 13))
)
_HOLD_ARRIVES = (
    'vocalize: [app] dictate_mode = "hold" arrives in 0.13.1; treated as "toggle" for now'
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


def resolve_app(file_config: dict | None = None) -> dict:
    """The `[app]` settings with defaults filled in, chords canonical.

    0.13.0 parses `dictate_mode = "hold"` but has no hold dispatch, so it
    resolves to toggle with one warning: a config written for 0.13.1 must
    not brick the CLI on a rollback (DEC-032).
    """
    if file_config is None:
        file_config = load_config_file()
    table = file_config.get("app") or {}
    _validate_app_table(table, config_path())
    resolved = dict(APP_DEFAULTS)
    resolved.update({key: table[key] for key in KNOWN_APP_KEYS if key in table})
    for key in APP_CHORD_KEYS:
        resolved[key] = chord_text(parse_chord(resolved[key]))
    if resolved["dictate_mode"] == "hold":
        _warn(_HOLD_ARRIVES)
        resolved["dictate_mode"] = "toggle"
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
        return list(file_chain)  # already validated in load_config_file

    return list(DEFAULT_CHAIN)


def chain_source(provider: str | None = None, file_config: dict | None = None) -> str:
    """Which precedence tier resolve_chain would draw its answer from.

    For the `vocalize chain` setter to report, e.g., "chain (from config
    file): elevenlabs, say".
    """
    if file_config is None:
        file_config = load_config_file()

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
    return (file_config.get("providers") or {}).get(name) or {}


# Anthropic is the one budget that is never unlimited by default: a cleanup on
# every press adds up, and nothing else in the config would say so.
ANTHROPIC_DEFAULT_BUDGET = 2_000_000


def budget_for(name: str, file_config: dict | None = None) -> int | None:
    """The provider's local monthly character budget, or None for unlimited."""
    # Presence, not truthiness: an explicit 0 is "spend nothing" on anthropic
    # and "unlimited" everywhere else, and both are the user's to write.
    budget = provider_table(name, file_config).get("monthly_chars")
    if name == "anthropic":
        return ANTHROPIC_DEFAULT_BUDGET if budget is None else budget
    return budget or None


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
    # Everything below is provider-chain territory and defaults to today's
    # single-provider behavior, so existing construction sites are unchanged.
    provider: str = auth.DEFAULT_PROVIDER
    language: str | None = None
    region: str | None = None
    profile: str | None = None


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
    provider's `[providers.<name>]` table > (ElevenLabs only) the legacy
    top-level voice/model/speed keys > the provider module's own DEFAULTS.

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
    legacy = name == "elevenlabs"

    resolved_voice = _first(
        voice_id if primary else None,
        os.environ.get("VOCALIZE_VOICE") if primary else None,
        table.get("voice"),
        file_config.get("voice") if legacy else None,
        defaults.get("voice"),
    )

    resolved_model = _first(
        model_id if primary else None,
        os.environ.get("VOCALIZE_MODEL") if primary else None,
        _first(table.get("model"), table.get("engine")),
        file_config.get("model") if legacy else None,
        _first(defaults.get("model"), defaults.get("engine")),
    )

    resolved_speed = None
    for value, source in (
        (speed if primary else None, "--speed"),
        (os.environ.get("VOCALIZE_SPEED") if primary else None, "VOCALIZE_SPEED"),
        (table.get("speed"), f"'speed' in [providers.{name}] in {config_path()}"),
        (file_config.get("speed") if legacy else None, f"'speed' in {config_path()}"),
    ):
        if value is not None:
            resolved_speed = _coerce_speed(value, source)
            break

    return Settings(
        voice_id=resolved_voice,
        model_id=resolved_model,
        speed=resolved_speed,
        provider=name,
        language=_first(table.get("language"), defaults.get("language")),
        region=table.get("region"),
        profile=table.get("profile"),
    )


def resolve_settings(
    voice_id: str | None = None,
    model_id: str | None = None,
    speed: float | None = None,
    file_config: dict | None = None,
) -> Settings:
    """Build Settings from flag > env var > config file > built-in default.

    The ElevenLabs case of resolve_provider_settings, kept under its own
    name because every existing caller passes voice/model/speed here.
    """
    return resolve_provider_settings(
        "elevenlabs",
        file_config,
        voice_id=voice_id,
        model_id=model_id,
        speed=speed,
        primary=True,
    )
