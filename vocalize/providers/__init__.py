"""Strict registry for on-device speech providers.

Providers implement check(settings), synthesize(text, settings), list_voices()
and NAME, AUDIO_EXT, MAX_CHARS, DEFAULTS. Modules are loaded only after the local
allowlist is checked. No hosted adapters, key lookup or dynamic plugin paths exist.
"""
from __future__ import annotations

import importlib

from ..auth import PROVIDER_LABELS, PROVIDER_NAMES
from ..exceptions import ConfigError

__all__ = ["PROVIDER_LABELS", "PROVIDER_NAMES", "get"]


def get(name: str):
    if name not in PROVIDER_NAMES:
        raise ConfigError("Unsupported speech provider; choose kokoro, piper or say")
    return importlib.import_module(f".{name}", __package__)
