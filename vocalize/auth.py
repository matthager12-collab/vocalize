"""API key storage in the OS keychain, and the shared `auth login` flow.

The keychain is the last tier of the resolution order in vocalize.config:
a flag, an env var, and a .env file all beat it. That ordering is what
makes it safe to store one — a project that pins its own key still wins.

`keyring` is imported lazily, inside _backend(), for two reasons. Backend
discovery is slow enough to notice on a `vocalize speak` that already had
a key in the environment, and routing every call through one seam gives
the tests a single place to swap in an in-memory store so they never
touch the developer's real keychain.
"""

from __future__ import annotations

import os

import click

from .exceptions import AuthError, VocalizeError

SERVICE = "vocalize"
USERNAME = "elevenlabs-api-key"

WHERE = f"the system keychain ({SERVICE}/{USERNAME})"

_DELETE_DENIED = (
    "Could not remove the key — the keychain denied the request. The key is "
    "STILL stored; unlock the keychain or remove it manually, and rotate the "
    "key if you were revoking a leak."
)


def _backend():
    """The keyring module. The one seam every keychain call goes through."""
    import keyring

    return keyring


def _errors() -> tuple[type[BaseException], ...]:
    """What a broken, locked, or absent keychain backend throws at us.

    Wider than keyring's own family on purpose. RuntimeError comes from
    the fail-backend when the platform has no keychain at all, and backend
    *selection* — PYTHON_KEYRING_BACKEND naming a module that isn't
    installed — blows up at import time as ModuleNotFoundError or
    AttributeError, before any KeyringError could be raised.
    """
    try:
        from keyring.errors import KeyringError
    except ImportError:
        return (ImportError, AttributeError, RuntimeError)
    return (KeyringError, ImportError, AttributeError, RuntimeError)


def _short_reason(exc: BaseException) -> str:
    """One line, fit for a status message rather than a traceback."""
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__


def store_key(key: str) -> None:
    """Save the API key in the OS keychain."""
    try:
        _backend().set_password(SERVICE, USERNAME, key)
    except _errors() as exc:
        raise AuthError(f"Could not write to {WHERE}: {exc}") from exc


def probe_keychain() -> tuple[str, str | None]:
    """("ok", key-or-None), or ("error", short reason).

    The honest sibling of stored_key(). Anything that *reports* on the
    keychain has to tell "nothing stored" apart from "could not look" —
    a distinction stored_key() deliberately flattens into None.
    """
    try:
        return "ok", (_backend().get_password(SERVICE, USERNAME) or None)
    except _errors() as exc:
        return "error", _short_reason(exc)


def stored_key() -> str | None:
    """The key held in the OS keychain, or None.

    None also covers a backend that failed outright: resolving a key must
    never blow up because the keychain is locked or missing, when a flag
    or an env var may well have supplied one anyway.
    """
    status, value = probe_keychain()
    return value if status == "ok" else None


def delete_key() -> None:
    """Forget the stored key. A missing entry is not an error.

    The exception alone cannot be trusted here: keyring's macOS backend
    funnels every Security-framework failure — denied, locked, auth
    failed — into the same PasswordDeleteError that a missing entry
    raises. So the outcome is decided by reading the entry back, not by
    whether delete_password threw.
    """
    from keyring.errors import PasswordDeleteError

    try:
        _backend().delete_password(SERVICE, USERNAME)
    except PasswordDeleteError:
        pass  # might mean "already gone", might mean "refused" — read back
    except _errors() as exc:
        raise AuthError(f"Could not delete from {WHERE}: {exc}") from exc

    status, value = probe_keychain()
    if status == "ok" and value is not None:
        raise AuthError(_DELETE_DENIED)
    if status == "error":
        # Unverified is not success: report it rather than claim removal.
        raise AuthError(
            f"Could not confirm the key was removed — the keychain is "
            f"unreadable ({value}). Check it manually, and rotate the key "
            f"if you were revoking a leak."
        )


def key_source(explicit: str | None = None) -> str:
    """Where resolve_api_key would get its key, without revealing the key.

    One of: "flag", "environment", ".env file", "keychain", "not found".
    """
    if explicit:
        return "flag"

    # Imported at call time, not module scope: config imports this module.
    from .config import _load_dotenv_if_present

    if os.environ.get("ELEVENLABS_API_KEY"):
        return "environment"

    _load_dotenv_if_present()
    if os.environ.get("ELEVENLABS_API_KEY"):
        return ".env file"

    if stored_key():
        return "keychain"
    return "not found"


def masked(key: str) -> str:
    """A preview short enough to be safe to print or paste into a bug report."""
    return f"{key[:4]}…"


def scrub(message: str, key: str) -> str:
    """Strip `key` out of someone else's error text before we print it.

    Defense in depth for messages we didn't write: h11 quotes the whole
    offending header value back in LocalProtocolError, and list_voices
    wraps that verbatim — so an API failure can carry the key to stdout
    through a path that never went near masked().
    """
    return message.replace(key, "[key]") if key else message


def _check_shape(key: str) -> None:
    """Reject anything that cannot be an API key, before it reaches a header.

    Interior control characters or whitespace nearly always mean a
    mis-decoded file — a UTF-16 secret piped in as bytes is the usual
    culprit. Catching it here keeps it out of httpx, whose rejection
    would otherwise quote the entire value back at us.
    """
    if not key:
        raise AuthError("No API key given — nothing was stored.")
    if any(character.isspace() or not character.isprintable() for character in key):
        raise AuthError(
            "That doesn't look like an API key: it contains whitespace or "
            "control characters. If you piped it in from a file, check the "
            "file's encoding — a UTF-16 file read as bytes looks like this."
        )


def validate_key(key: str) -> None:
    """Check a key against the API. Raises VocalizeError when it doesn't work."""
    from .tts import build_client, list_voices

    list_voices(build_client(key))


def prompt_for_key() -> str:
    """Ask for a key without echoing it.

    click's hidden prompt is getpass, which reads and writes /dev/tty
    directly — so this works unchanged inside the wizard, which paints
    there rather than on a possibly-relayed stdout.
    """
    return click.prompt("ElevenLabs API key", hide_input=True, show_default=False).strip()


def login(key: str) -> str:
    """Check the shape, validate against the API, then store. Returns where.

    Validation comes first and nothing is written when it fails: a key
    that doesn't work is worse than no key, because every later command
    would fail at the API instead of at a clear "no key found".

    Every failure message is scrubbed, and `from None` drops the cause —
    the original exception text is exactly where a leaked key would be.
    """
    _check_shape(key)
    try:
        validate_key(key)
        store_key(key)
    except VocalizeError as exc:
        raise AuthError(scrub(str(exc), key)) from None
    return f"Stored the API key in {WHERE}."
