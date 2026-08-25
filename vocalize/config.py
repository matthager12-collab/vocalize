"""Configuration loading: API key resolution and defaults.

Resolution order (first one found wins):
  1. --api-key flag passed explicitly on the CLI
  2. ELEVENLABS_API_KEY environment variable
  3. A .env file in the current directory (loaded via python-dotenv,
     if it's installed — this is an optional dependency)
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .exceptions import MissingAPIKeyError

DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"  # "Rachel" — a stock ElevenLabs voice
DEFAULT_MODEL = "eleven_multilingual_v2"
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"


def _load_dotenv_if_present() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        return
    load_dotenv()


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


@dataclass(frozen=True)
class Settings:
    voice_id: str = DEFAULT_VOICE
    model_id: str = DEFAULT_MODEL
    output_format: str = DEFAULT_OUTPUT_FORMAT
