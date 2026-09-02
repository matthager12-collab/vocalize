"""Custom exceptions for vocalize."""


class VocalizeError(Exception):
    """Base class for all vocalize errors."""


class MissingAPIKeyError(VocalizeError):
    """Raised when no API key can be found for a provider."""

    def __init__(self, provider: str = "elevenlabs") -> None:
        self.provider = provider
        if provider == "elevenlabs":
            super().__init__(
                "No ElevenLabs API key found. The easiest fix is `vocalize auth "
                "login`, which stores one in your system keychain. You can also "
                "set the ELEVENLABS_API_KEY environment variable, add it to a "
                ".env file, or pass --api-key on the command line. Get a free key "
                "at https://elevenlabs.io/app/settings/api-keys"
            )
            return

        # Imported at call time: auth imports this module at import time.
        from .auth import PROVIDER_ENV_VARS, PROVIDER_LABELS

        label = PROVIDER_LABELS.get(provider, provider)
        env_var = PROVIDER_ENV_VARS.get(provider)
        env_hint = f", or set {env_var}" if env_var else ""
        super().__init__(
            f"No {label} API key found. Run `vocalize auth login --provider "
            f"{provider}`{env_hint}."
        )


class AuthError(VocalizeError):
    """Raised when the system keychain cannot be read from or written to."""


class ConfigError(VocalizeError):
    """Raised when the config file or one of its values is invalid."""


class TTSRequestError(VocalizeError):
    """Raised when the ElevenLabs API call itself fails."""


class ProviderError(TTSRequestError):
    """A provider-attributed failure. Subclasses tell the chain what to do.

    Subclassing TTSRequestError keeps every existing `except
    TTSRequestError` handler — the CLI's included — working unchanged.
    """

    def __init__(self, provider: str, message: str) -> None:
        self.provider = provider
        super().__init__(f"{provider}: {message}")


class ProviderAuthError(ProviderError):
    """Bad or missing credentials. The chain skips to the next provider."""


class ProviderQuotaError(ProviderError):
    """Out of credit or over budget. Marked exhausted, then skipped."""


class ProviderTransientError(ProviderError):
    """Rate limit, 5xx, or a network wobble. The chain skips to the next."""


class ProviderUnavailableError(ProviderError):
    """Not usable offline: no key, no binary, no optional dependency."""


class ProviderContentError(ProviderError):
    """The request itself is wrong — bad voice, text too long. Stops the chain."""


class PlaybackStopped(VocalizeError):
    """The user stopped playback mid-run (used by the streaming path).

    `audio` carries whatever had already been rendered when the stop came
    in, joined into one file, and `audio_ext` its extension. A stop that
    came from a broken player rather than a human is the reason: that
    audio is paid for, and throwing it away would be the second failure.

    `remaining_text` is the part of the read nobody heard and `provider`
    the one that spoke, so a stop a dictation asked to remember can be
    continued later (DEC-003). The text never leaves this process unless
    the interrupt record is written.
    """

    def __init__(
        self,
        message: str,
        audio: bytes | None = None,
        audio_ext: str | None = None,
        remaining_text: str = "",
        provider: str | None = None,
    ) -> None:
        super().__init__(message)
        self.audio = audio
        self.audio_ext = audio_ext
        self.remaining_text = remaining_text
        self.provider = provider


class DictationError(VocalizeError):
    """Raised when a dictation cannot be recorded, transcribed or delivered.

    Never carries any part of a transcript: the message is shown in a
    terminal and its wording is the only thing that reaches a log.
    """


class ClipboardError(VocalizeError):
    """Raised when the system clipboard cannot be read."""


class NoAudioPlayerError(VocalizeError):
    """Raised when no supported system audio player can be found."""


class AudioPlaybackError(VocalizeError):
    """Raised when saving or playing audio fails at the OS/subprocess level."""
