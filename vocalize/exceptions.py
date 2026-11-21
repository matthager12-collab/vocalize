"""Custom exceptions for vocalize."""


class VocalizeError(Exception):
    """Base class for all vocalize errors."""


class ConfigError(VocalizeError):
    """Raised when the config file or one of its values is invalid."""


class ConfigChangedError(ConfigError):
    """The config file moved between a reader's read and its write.

    DEC-005: every writer — the portal page, the wizard, `vocalize chain`,
    a hand edit — reads the whole file and writes a merged copy back, so
    the last one to write would otherwise silently drop everything the
    others changed in between. The fingerprint taken at the read is
    compared again at the write, and this is what a mismatch raises,
    including the case where the file did not exist at the read and does
    now. Subclassing ConfigError keeps every existing `except ConfigError`
    handler working — `cli.run()`'s error funnel included, so an unhandled
    refusal in a terminal is a clean error and not a traceback.
    """


class TTSRequestError(VocalizeError):
    """Raised when local speech synthesis fails."""


class ProviderError(TTSRequestError):
    """A provider-attributed failure. Subclasses tell the chain what to do.

    Subclassing TTSRequestError keeps every existing `except
    TTSRequestError` handler — the CLI's included — working unchanged.
    """

    def __init__(self, provider: str, message: str) -> None:
        self.provider = provider
        super().__init__(f"{provider}: {message}")


class ProviderTransientError(ProviderError):
    """Recoverable local worker failure. The chain skips to the next."""


class ProviderUnavailableError(ProviderError):
    """Not usable: missing binary, model, or local dependency."""


class ProviderContentError(ProviderError):
    """The request itself is wrong — bad voice, text too long. Stops the chain."""


class PlaybackStopped(VocalizeError):
    """The user stopped playback mid-run (used by the streaming path).

    `audio` carries whatever had already been rendered when the stop came
    in, joined into one file, and `audio_ext` its extension. A stop that
    came from a broken player rather than a human is the reason: that
    audio was already rendered and can be retained.

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
