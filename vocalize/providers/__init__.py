"""The provider registry, and the contract every provider module keeps.

A provider module is plain Python — no base class, no registration
decorator. `get(name)` imports it on demand, which is the point: boto3,
the ElevenLabs SDK and anything else heavy stay unimported until a chain
actually reaches that provider.

Provider module contract
------------------------

Required module attributes:

    NAME: str
        The provider's name, matching its module name and its entry in
        auth.PROVIDER_NAMES.
    AUDIO_EXT: str
        "mp3" | "m4a" | "wav" — the extension of the bytes synthesize
        returns. It decides the cache file's suffix and the output file.
    MAX_CHARS: int | None
        Longest text the provider accepts in one request, or None for no
        limit (the chunker then makes one call).
    DEFAULTS: dict
        Provider defaults, keys drawn from: voice, model, language.

Optional module attributes:

    MAX_BYTES: int | None
        A UTF-8 byte cap applied on top of MAX_CHARS, for APIs that count
        bytes (Google). Default None.
    STREAMING: bool
        True when pieces can be played as they finish. Default False.

Required functions:

    check(settings=None) -> None
        Offline availability only — no network. Raises
        ProviderUnavailableError when credentials, a binary, or an
        optional dependency is missing. The chain always passes the
        provider's resolved Settings; providers that need nothing from
        them ignore the argument (Polly reads `profile`).
    synthesize(text: str, settings: Settings) -> bytes
        The audio for `text`, in AUDIO_EXT format. Raises the typed
        ProviderError subclass that tells the chain what to do.
    list_voices() -> list[dict]
        [{"id": ..., "name": ...}, ...].

Optional functions:

    validate(key: str) -> None
        A cheap network auth check for `auth login`. Raises
        ProviderAuthError or ProviderTransientError.
    usage() -> dict | None
        Whatever the provider reports about its own quota, or None.
"""

from __future__ import annotations

import importlib

from ..auth import PROVIDER_LABELS, PROVIDER_NAMES
from ..exceptions import ConfigError, MissingAPIKeyError, ProviderUnavailableError

__all__ = ["PROVIDER_LABELS", "PROVIDER_NAMES", "get", "require_key"]


def get(name: str):
    """The provider module called `name`, imported on first use."""
    if name not in PROVIDER_NAMES:
        raise ConfigError(
            f"Unknown provider {name!r}. Known: {', '.join(PROVIDER_NAMES)}"
        )
    return importlib.import_module(f".{name}", __package__)


def require_key(name: str, explicit: str | None = None) -> str:
    """The provider's API key, as an availability check rather than a crash.

    "No key stored" is a reason to try the next provider in the chain, not
    an error to stop on — so the missing-key error is translated into the
    one the chain knows how to skip.
    """
    from .. import config

    try:
        return config.resolve_provider_key(name, explicit)
    except MissingAPIKeyError as exc:
        raise ProviderUnavailableError(name, str(exc)) from exc
