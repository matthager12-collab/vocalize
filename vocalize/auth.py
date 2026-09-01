"""API key storage in the OS keychain, and the shared `auth login` flow.

The keychain is the last tier of the resolution order in vocalize.config:
a flag, an env var, and a .env file all beat it. That ordering is what
makes it safe to store one — a project that pins its own key still wins.

`keyring` is imported lazily, inside _backend(), for two reasons. Backend
discovery is slow enough to notice on a `vocalize speak` that already had
a key in the environment, and routing every call through one seam gives
the tests a single place to swap in an in-memory store so they never
touch the developer's real keychain.

This is also the dependency-free leaf that owns the provider *names*:
providers import them from here, never the other way round, so nothing
here may import vocalize.providers at module level.
"""

from __future__ import annotations

import os

import click

from .exceptions import AuthError, VocalizeError

PROVIDER_NAMES = ("elevenlabs", "openai", "google", "polly", "say", "kokoro")

PROVIDER_LABELS = {
    "elevenlabs": "ElevenLabs",
    "openai": "OpenAI",
    "google": "Google Cloud",
    "polly": "Amazon Polly",
    "say": "macOS say",
    "kokoro": "Kokoro (local)",
}

# Only providers authenticated by a single API key appear here. Polly uses
# boto3's own credential chain; say and kokoro need no credentials at all.
PROVIDER_ENV_VARS = {
    "elevenlabs": "ELEVENLABS_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
}

PROVIDER_USERNAMES = {
    "elevenlabs": "elevenlabs-api-key",
    "openai": "openai-api-key",
    "google": "google-api-key",
}

DEFAULT_PROVIDER = "elevenlabs"

SERVICE = "vocalize"
# Kept as module constants because the CLI, the wizard and the tests all
# name them; the values are the ElevenLabs slot, unchanged since 0.1.
USERNAME = PROVIDER_USERNAMES[DEFAULT_PROVIDER]

WHERE = f"the system keychain ({SERVICE}/{USERNAME})"

_DELETE_DENIED = (
    "Could not remove the key — the keychain denied the request. The key is "
    "STILL stored; unlock the keychain or remove it manually, and rotate the "
    "key if you were revoking a leak."
)


def _username(provider: str) -> str:
    """The keychain entry name for `provider`, or an error if it has none."""
    try:
        return PROVIDER_USERNAMES[provider]
    except KeyError:
        label = PROVIDER_LABELS.get(provider, provider)
        raise AuthError(f"{label} does not use a stored API key.") from None


def _where(provider: str) -> str:
    return f"the system keychain ({SERVICE}/{_username(provider)})"


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


def store_key(key: str, provider: str = DEFAULT_PROVIDER) -> None:
    """Save the API key in the OS keychain."""
    try:
        _backend().set_password(SERVICE, _username(provider), key)
    except _errors() as exc:
        raise AuthError(f"Could not write to {_where(provider)}: {exc}") from exc


def probe_keychain(provider: str = DEFAULT_PROVIDER) -> tuple[str, str | None]:
    """("ok", key-or-None), or ("error", short reason).

    The honest sibling of stored_key(). Anything that *reports* on the
    keychain has to tell "nothing stored" apart from "could not look" —
    a distinction stored_key() deliberately flattens into None.
    """
    try:
        return "ok", (_backend().get_password(SERVICE, _username(provider)) or None)
    except _errors() as exc:
        return "error", _short_reason(exc)


def stored_key(provider: str = DEFAULT_PROVIDER) -> str | None:
    """The key held in the OS keychain, or None.

    None also covers a backend that failed outright: resolving a key must
    never blow up because the keychain is locked or missing, when a flag
    or an env var may well have supplied one anyway.
    """
    status, value = probe_keychain(provider)
    return value if status == "ok" else None


def delete_key(provider: str = DEFAULT_PROVIDER) -> None:
    """Forget the stored key. A missing entry is not an error.

    The exception alone cannot be trusted here: keyring's macOS backend
    funnels every Security-framework failure — denied, locked, auth
    failed — into the same PasswordDeleteError that a missing entry
    raises. So the outcome is decided by reading the entry back, not by
    whether delete_password threw.
    """
    from keyring.errors import PasswordDeleteError

    try:
        _backend().delete_password(SERVICE, _username(provider))
    except PasswordDeleteError:
        pass  # might mean "already gone", might mean "refused" — read back
    except _errors() as exc:
        raise AuthError(f"Could not delete from {_where(provider)}: {exc}") from exc

    status, value = probe_keychain(provider)
    if status == "ok" and value is not None:
        raise AuthError(_DELETE_DENIED)
    if status == "error":
        # Unverified is not success: report it rather than claim removal.
        raise AuthError(
            f"Could not confirm the key was removed — the keychain is "
            f"unreadable ({value}). Check it manually, and rotate the key "
            f"if you were revoking a leak."
        )


def key_source(explicit: str | None = None, provider: str = DEFAULT_PROVIDER) -> str:
    """Where resolve_provider_key would get its key, without revealing it.

    One of: "flag", "environment", ".env file", "keychain", "not found".
    """
    if explicit:
        return "flag"

    # Imported at call time, not module scope: config imports this module.
    from .config import _load_dotenv_if_present

    env_var = PROVIDER_ENV_VARS.get(provider)
    if env_var:
        if os.environ.get(env_var):
            return "environment"

        _load_dotenv_if_present()
        if os.environ.get(env_var):
            return ".env file"

    if provider in PROVIDER_USERNAMES and stored_key(provider):
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


def validate_key(key: str, provider: str = DEFAULT_PROVIDER) -> None:
    """Check a key against the API. Raises VocalizeError when it doesn't work."""
    if provider == "elevenlabs":
        from .tts import build_client, list_voices

        list_voices(build_client(key))
        return

    if provider in PROVIDER_USERNAMES:
        # Lazy: importing a provider module pulls in its HTTP layer.
        from . import providers

        providers.get(provider).validate(key)
        return

    label = PROVIDER_LABELS.get(provider, provider)
    raise AuthError(f"{label} does not use a stored API key.")


def polly_credential_status(profile: str = "default") -> str:
    """Where Polly's credentials would come from, for `auth status`.

    Offline only, mirroring providers.polly.check()'s own three checks (env,
    ~/.aws/credentials, profile) without ever constructing a boto3 Session —
    a status command must not touch the network any more than check() does.
    """
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        return "environment"

    from . import providers  # lazy: this module must not import providers eagerly

    if providers.get("polly")._profile_in_credentials_file(profile):
        return f"~/.aws/credentials [{profile}]"
    return "not configured"


def prompt_for_key(provider: str = DEFAULT_PROVIDER) -> str:
    """Ask for a key without echoing it.

    click's hidden prompt is getpass, which reads and writes /dev/tty
    directly — so this works unchanged inside the wizard, which paints
    there rather than on a possibly-relayed stdout.
    """
    label = PROVIDER_LABELS.get(provider, provider)
    return click.prompt(f"{label} API key", hide_input=True, show_default=False).strip()


def login(key: str, provider: str = DEFAULT_PROVIDER) -> str:
    """Check the shape, validate against the API, then store. Returns where.

    Validation comes first and nothing is written when it fails: a key
    that doesn't work is worse than no key, because every later command
    would fail at the API instead of at a clear "no key found".

    Every failure message is scrubbed, and `from None` drops the cause —
    the original exception text is exactly where a leaked key would be.
    """
    _check_shape(key)
    try:
        # The ElevenLabs path keeps its one-argument call on purpose: the
        # wizard and the tests replace this seam with a single-parameter
        # stand-in, and a second positional would break them.
        if provider == DEFAULT_PROVIDER:
            validate_key(key)
        else:
            validate_key(key, provider)
        store_key(key, provider)
    except VocalizeError as exc:
        raise AuthError(scrub(str(exc), key)) from None
    return f"Stored the API key in {_where(provider)}."
