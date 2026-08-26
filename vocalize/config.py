"""Configuration loading: API key resolution, config file, and defaults.

API key resolution order (first one found wins):
  1. --api-key flag passed explicitly on the CLI
  2. ELEVENLABS_API_KEY environment variable
  3. A .env file in the current directory (loaded via python-dotenv,
     if it's installed — this is an optional dependency)

Voice, model and speed are resolved per setting, in this order:
  CLI flag > environment variable > config file > built-in default
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .exceptions import ConfigError, MissingAPIKeyError

DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"  # "Rachel" — a stock ElevenLabs voice
DEFAULT_MODEL = "eleven_multilingual_v2"
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"

# The range the ElevenLabs API accepts; 1.0 is normal speed.
SPEED_MIN = 0.7
SPEED_MAX = 1.2

KNOWN_CONFIG_KEYS = ("voice", "model", "speed")


def _load_dotenv_if_present() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        return
    # Explicit path, not dotenv's default search: bare load_dotenv() walks
    # up from the install directory, so a console-script install would miss
    # the user's project .env and could pick up an unrelated one.
    load_dotenv(dotenv_path=Path.cwd() / ".env")


def resolve_api_key(explicit: str | None = None) -> str:
    """Find an API key from --api-key, the environment, or .env.

    Raises MissingAPIKeyError if none is found.
    """
    if explicit:
        return explicit

    _load_dotenv_if_present()

    key = os.environ.get("ELEVENLABS_API_KEY")
    if key:
        return key

    raise MissingAPIKeyError()


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
            print(f"vocalize: unknown config key {key!r} in {path}", file=sys.stderr)

    return data


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


def resolve_settings(
    voice_id: str | None = None,
    model_id: str | None = None,
    speed: float | None = None,
) -> Settings:
    """Build Settings from flag > env var > config file > built-in default."""
    file_config = load_config_file()

    resolved_speed = None
    for value, source in (
        (speed, "--speed"),
        (os.environ.get("VOCALIZE_SPEED"), "VOCALIZE_SPEED"),
        (file_config.get("speed"), f"'speed' in {config_path()}"),
    ):
        if value is not None:
            resolved_speed = _coerce_speed(value, source)
            break

    return Settings(
        voice_id=_first(voice_id, os.environ.get("VOCALIZE_VOICE"), file_config.get("voice"), DEFAULT_VOICE),
        model_id=_first(model_id, os.environ.get("VOCALIZE_MODEL"), file_config.get("model"), DEFAULT_MODEL),
        speed=resolved_speed,
    )
