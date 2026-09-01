"""Custom exceptions for vocalize."""


class VocalizeError(Exception):
    """Base class for all vocalize errors."""


class MissingAPIKeyError(VocalizeError):
    """Raised when no ElevenLabs API key can be found."""

    def __init__(self) -> None:
        super().__init__(
            "No ElevenLabs API key found. The easiest fix is `vocalize auth "
            "login`, which stores one in your system keychain. You can also "
            "set the ELEVENLABS_API_KEY environment variable, add it to a "
            ".env file, or pass --api-key on the command line. Get a free key "
            "at https://elevenlabs.io/app/settings/api-keys"
        )


class AuthError(VocalizeError):
    """Raised when the system keychain cannot be read from or written to."""


class ConfigError(VocalizeError):
    """Raised when the config file or one of its values is invalid."""


class TTSRequestError(VocalizeError):
    """Raised when the ElevenLabs API call itself fails."""


class ClipboardError(VocalizeError):
    """Raised when the system clipboard cannot be read."""


class NoAudioPlayerError(VocalizeError):
    """Raised when no supported system audio player can be found."""


class AudioPlaybackError(VocalizeError):
    """Raised when saving or playing audio fails at the OS/subprocess level."""
