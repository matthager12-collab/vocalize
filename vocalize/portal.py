"""The config portal's HTTP server: auth bootstrap and read-only state.

A local web page is a trust boundary, so the whole surface is built to be
tested without a socket: `route()` is a plain function of
`(method, path, headers, body, state)` returning
`(status, headers, body_bytes)`, and the `BaseHTTPRequestHandler` below
does nothing but read a capped body, call it, and write the answer. Every
rule in this module — the `Host` check, the token check, the security
headers, the lockout — is therefore a unit test with no network in it.

Authentication (DEC-004). `serve()` mints two secrets. The **one-time
code** goes into the URL's `#fragment`, which browsers never send to the
server, so it reaches the page without ever entering a request line, an
access log, or `Referer`. The page exchanges it once, within
`CODE_TTL_SECONDS`, at `POST /api/session` for the **session token**,
which lives in this process's memory and in the page's closure and is
sent back in the `X-Vocalize-Token` header on every other call. A token
offered in a query string or a body is refused, on every route — that is
what stops a link, a form post or an `<img src>` from carrying it.

`Host` is checked on *every* request, static assets and the session
exchange included. Without that, any web page the user visits could point
a hostname it controls at 127.0.0.1 and talk to this server from its own
origin (DNS rebinding); the browser would send `Host: attacker.example`,
and only this check notices.

Writes are compare-and-swap (DEC-005): `/api/state` hands the page the
config file's fingerprint, every write hands it back, and a file that
moved in between is refused with 409 rather than clobbered. Every value
goes through the same `config._validate_*` the CLI uses, so the page
cannot write anything a hand-edited file could not, and a bad value comes
back with the CLI's own wording.

What this module deliberately does not do yet: the real page is run 9 —
`assets/portal.html` here is a placeholder that does the code exchange
and prints the state.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import auth, config, ledger, readiness, tts, wizard
from .exceptions import ConfigChangedError, ConfigError, ProviderError, VocalizeError

# Loopback only, always. The portal exposes the machine's provider
# settings and can (from run 8) write the config file: it must never be
# reachable from another host, so this is a constant and not an option.
BIND_HOST = "127.0.0.1"

MAX_BODY_BYTES = 64 * 1024
# A connection that opens and then goes quiet must not pin a handler
# thread for the life of the process: this is a ThreadingHTTPServer, one
# unbounded daemon thread per connection, and a declared body that never
# arrives would block `rfile.read` with no deadline at all. Comfortably
# above the page's ping interval, so a keep-alive connection between two
# pings is never dropped under it.
HANDLER_TIMEOUT_SECONDS = 30.0
CODE_TTL_SECONDS = 60.0
MAX_CODE_ATTEMPTS = 5
PING_INTERVAL_SECONDS = 15.0
MISSED_PINGS_BEFORE_SHUTDOWN = 4
DEFAULT_IDLE_TIMEOUT = PING_INTERVAL_SECONDS * MISSED_PINGS_BEFORE_SHUTDOWN

# "Refused", not "wrong": an already-used or expired code counts toward
# the lockout too (see `PortalState.exchange`), and re-opening the URL
# from history or a second browser sends exactly that. Naming wrong codes
# would report five bad guesses to a user who made none.
LOCKOUT_REASON = (
    "the portal shut down after five refused codes — run `vocalize portal` again"
)
IDLE_REASON = "the portal closed after the page stopped answering"

# `media-src 'self' blob:` is load-bearing: run 8's voice previews are
# fetched with the token header and played from a Blob URL, which
# `default-src 'self'` alone would block.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; media-src 'self' blob:; frame-ancestors 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
}

TOKEN_HEADER = "X-Vocalize-Token"

# The preview's fixed sentence. A constant, never text from the page: a
# preview is a voice check, and letting the browser choose the words
# would turn a settings page into an unmetered synthesis endpoint.
PREVIEW_TEXT = "This is how vocalize will sound."

# Content-Type by the provider's AUDIO_EXT. Anything unrecognised is
# served as bytes rather than guessed at — with nosniff, a wrong type is
# a preview that will not play, and a guessed one is worse.
PREVIEW_TYPES = {"mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4"}

# The same audio cache `vocalize speak` uses — that is the point: a
# preview of a voice you then read with is already rendered and paid for.
# Named here rather than left to `chain.run`'s default only so a test can
# point it somewhere else.
CACHE_DIR = tts.DEFAULT_CACHE_DIR

# One preview at a time, process-wide. Kokoro's provider keeps a single
# resident worker session whose JSON-lines protocol has no request ids,
# so two previews at once would read each other's replies; it also bounds
# what a page holding down a preview button can spend.
_preview_lock = threading.Lock()

INSTALL_TARGETS = ("kokoro", "stt")

# The download seam. `install.download_file` takes an `opener=`; None
# means its real HTTPS-only opener. Tests and the live check set this so
# a portal being exercised never fetches a byte.
OPENER = None

# Names that would smuggle the session token somewhere it can leak: a
# query string lands in history and `Referer`, a body lands in a form
# post. Refused wherever they appear, whatever their value.
_TOKEN_PARAM_NAMES = ("token", "access_token", "session", "x-vocalize-token")

_DECIMAL = re.compile(r"[0-9]+")

ASSETS_DIR = Path(__file__).resolve().parent / "assets"

_ASSETS = {
    "/": ("portal.html", "text/html; charset=utf-8"),
    "/portal.js": ("portal.js", "text/javascript; charset=utf-8"),
}


def _now() -> float:
    """Monotonic seconds. A function, so tests can move time."""
    return time.monotonic()


def _same_secret(offered: str, secret: str) -> bool:
    """Constant-time compare that survives a non-ASCII offer.

    `secrets.compare_digest` refuses two `str` arguments unless both are
    ASCII and raises `TypeError` — and the offered half is attacker
    input: a header value http.server decoded as latin-1, or a JSON
    string that may hold anything including a lone surrogate. Raising
    there would mean no response and no security headers at all, so both
    sides are compared as UTF-8 bytes instead, which keeps the
    constant-time property and answers 401 like any other bad secret.
    """
    return secrets.compare_digest(
        offered.encode("utf-8", "surrogatepass"), secret.encode("utf-8", "surrogatepass")
    )


# --- state ------------------------------------------------------------


def _idle_install() -> dict:
    """The install progress dict before anything has been started."""
    return {
        "running": False,
        "target": None,
        "step": "idle",
        "downloaded": 0,
        "total": 0,
        "done": False,
        "error": None,
    }


class PortalState:
    """One running portal's secrets, counters and shutdown switch.

    Mutated from several handler threads at once (the server is a
    `ThreadingHTTPServer`), so every field that decides an auth outcome
    moves under `_lock`.
    """

    def __init__(
        self,
        file_config: dict,
        *,
        port: int = 0,
        readiness_timeout: float = 2.0,
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
    ) -> None:
        self.file_config = file_config
        self.port = port
        self.readiness_timeout = readiness_timeout
        self.idle_timeout = idle_timeout
        # 32 bytes of urlsafe randomness each: the code is guessed at over
        # a loopback socket five times before the server gives up, the
        # token not at all.
        self.code: str | None = secrets.token_urlsafe(32)
        self.token: str = secrets.token_urlsafe(32)
        self.code_expires_at = _now() + CODE_TTL_SECONDS
        self.failed_codes = 0
        self.last_seen = _now()
        # A long install must not let the idle watchdog close the portal
        # out from under it.
        self.watchdog_suspended = False
        self.install: dict = _idle_install()
        self.install_thread: threading.Thread | None = None
        self.shutdown_reason: str | None = None
        self.on_shutdown: Callable[[], None] | None = None
        self._lock = threading.Lock()

    @property
    def origin(self) -> str:
        return f"http://{BIND_HOST}:{self.port}"

    @property
    def expected_host(self) -> str:
        return f"{BIND_HOST}:{self.port}"

    def url(self) -> str:
        """The address to open: the one-time code rides in the fragment."""
        return f"{self.origin}/#code={self.code}"

    def note_ping(self) -> None:
        self.last_seen = _now()

    def request_shutdown(self, reason: str) -> None:
        with self._lock:
            if self.shutdown_reason is not None:
                return
            self.shutdown_reason = reason
            callback = self.on_shutdown
        if callback is not None:
            callback()

    def exchange(self, code: str) -> tuple[str | None, str]:
        """Trade the one-time code for the session token.

        Returns `(token, "")` once and only once. Every failure —
        expired, already used, or simply wrong — counts toward the
        lockout, because after expiry there is no code left to guess and
        a caller still hammering the route is not the browser we opened.
        """
        with self._lock:
            if self.code is None:
                failure = "this code has already been used"
            elif _now() > self.code_expires_at:
                self.code = None
                failure = "this code has expired"
            elif _same_secret(code, self.code):
                self.code = None
                self.last_seen = _now()
                return self.token, ""
            else:
                failure = "wrong code"

            self.failed_codes += 1
            locked_out = self.failed_codes >= MAX_CODE_ATTEMPTS

        if locked_out:
            self.request_shutdown(LOCKOUT_REASON)
        return None, failure

    def begin_install(self, target: str) -> bool:
        """Claim the one install slot. False when one is already running.

        Under the lock with the liveness check, so two clicks arriving on
        two handler threads cannot both start a download into the same
        model directory.
        """
        with self._lock:
            if self.install["running"]:
                # Set here, under this lock, before the caller has even
                # built the thread — so a second click racing the first
                # cannot slip through the gap between claiming and
                # starting.
                return False
            if self.install_thread is not None and self.install_thread.is_alive():
                return False
            self.install = _idle_install()
            self.install.update(running=True, target=target, step="starting")
            # The page legitimately goes quiet during a long install: this
            # is the one time the watchdog must not close the portal.
            self.watchdog_suspended = True
            return True

    def token_matches(self, offered: str | None) -> bool:
        if not offered:
            return False
        return _same_secret(offered, self.token)


# --- bounded probes ---------------------------------------------------
#
# Same shape and the same reason as readiness._run_probe: a keychain read
# can block on a macOS permission dialog, and the page polls /api/state.
# One wedged call must leak one thread for the life of the process, never
# one per poll — so a name already running is joined again rather than
# started again.


class _Slot:
    __slots__ = ("thread", "value")

    def __init__(self) -> None:
        self.thread: threading.Thread | None = None
        self.value: object | None = None


_probe_lock = threading.Lock()
_probes: dict[str, _Slot] = {}


def _start(name: str, work: Callable[[], object]) -> _Slot:
    """Run `work` on a daemon thread — or hand back the one still running.

    Starting is separate from waiting so that a payload needing several
    probes can start them all and then wait once: six wedged keychain
    reads joined one after another cost six timeouts, and the page polls
    this route.
    """
    with _probe_lock:
        slot = _probes.get(name)
        if slot is not None and slot.thread is not None and slot.thread.is_alive():
            return slot

        slot = _Slot()
        _probes[name] = slot

        def target() -> None:
            try:
                slot.value = work()
            except Exception:  # noqa: BLE001 — a probe must never break /api/state
                # Never the message: like readiness's registry this
                # runs credential code, and an exception's text can
                # carry credential-shaped material.
                slot.value = None

        slot.thread = threading.Thread(target=target, daemon=True, name=f"vocalize-portal-{name}")
        slot.thread.start()
        return slot


def _collect(slot: _Slot, timeout: float, fallback):
    """What the probe returned, or `fallback` if it is still running."""
    thread = slot.thread
    if thread is not None:
        thread.join(max(timeout, 0.0))
        if thread.is_alive():
            return fallback
    return fallback if slot.value is None else slot.value


# --- /api/state -------------------------------------------------------


def _key_status(name: str, file_config: dict) -> dict:
    """Where a provider's credential comes from, and a four-character preview.

    Never the key. `auth.masked` is the same preview `vocalize auth
    status` prints; the value itself never enters the returned dict, so
    it cannot reach the response body by any route.
    """
    if name == "polly":
        profile = (
            config.provider_table("polly", file_config).get("profile")
            or os.environ.get("AWS_PROFILE")
            or "default"
        )
        return {"source": auth.polly_credential_status(profile), "masked": None}

    env_var = auth.PROVIDER_ENV_VARS.get(name)
    if env_var is None:
        return {"source": "not needed", "masked": None}

    source = auth.key_source(None, name)
    if source in ("environment", ".env file"):
        key = os.environ.get(env_var)
    elif source == "keychain":
        key = auth.stored_key(name)
    else:
        key = None
    return {"source": source, "masked": auth.masked(key) if key else None}


def _provider_entry(name: str, file_config: dict, primary: bool, key: _Slot, deadline: float) -> dict:
    entry: dict = {
        "monthly_chars": config.budget_for(name, file_config),
        # A floor rather than the bare remainder: once the deadline has
        # passed, a probe that has already finished should still have its
        # answer collected rather than every later one reading "not
        # checked".
        "key": _collect(
            key,
            max(deadline - _now(), 0.05),
            {"source": "not checked", "masked": None},
        ),
    }
    try:
        settings = config.resolve_provider_settings(name, file_config, primary=primary)
    except Exception:  # noqa: BLE001 — a hand-edited table must not break the page
        entry["error"] = "these settings could not be resolved"
        return entry
    entry.update(
        {
            "voice": settings.voice_id,
            "model": settings.model_id,
            "speed": settings.speed,
            "language": settings.language,
            "region": settings.region,
            "profile": settings.profile,
        }
    )
    return entry


def state_payload(file_config: dict, *, timeout: float = 2.0) -> dict:
    """Everything the page renders read-only, with no secret in it.

    Bounded by `timeout` per readiness row, and by one shared deadline
    across all six credential probes — which are started here, before the
    readiness rows, so they overlap those instead of queueing behind
    them. A wedged keychain therefore costs about one timeout for the
    whole payload rather than one per provider, and yields "still
    checking" rather than a hung route.
    """
    deadline = _now() + timeout
    key_probes = {
        name: _start(f"key:{name}", lambda n=name: _key_status(n, file_config))
        for name in auth.PROVIDER_NAMES
    }

    rows = [row._asdict() for row in readiness.readiness(file_config, timeout=timeout)]

    try:
        order = config.resolve_chain(None, file_config)
        chain = {"order": order, "source": config.chain_source(None, file_config)}
    except VocalizeError as exc:
        order = []
        chain = {"order": [], "source": "invalid", "error": str(exc)}

    usage = ledger.all_status()
    budgets = {
        name: {
            "chars": usage.get(name, {}).get("chars", 0),
            "exhausted": usage.get(name, {}).get("exhausted", False),
            "monthly_chars": config.budget_for(name, file_config),
        }
        for name in auth.PROVIDER_NAMES
    }

    try:
        stt = config.resolve_stt(file_config)
    except VocalizeError as exc:
        stt = {"error": str(exc)}

    return {
        # What every write must hand back (DEC-005). An early read is the
        # safe direction: a change landing after it is refused, where a
        # late one would call a change that already happened "unchanged".
        "fingerprint": wizard.fingerprint_config(config.config_path()),
        "rows": rows,
        "chain": chain,
        "providers": {
            name: _provider_entry(
                name, file_config, bool(order) and name == order[0], key_probes[name], deadline
            )
            for name in auth.PROVIDER_NAMES
        },
        "budgets": budgets,
        "stt": stt,
    }


# --- writes (DEC-005) -------------------------------------------------
#
# Every one of these takes a JSON object from the page and turns it into
# a config file the CLI has to be able to read back. Two rules hold for
# all of them:
#
#   * nothing is written that `config._validate_*` would not accept from
#     a hand-edited file — the page gets the CLI's own error text, so a
#     rejected value reads the same wherever you met it;
#   * nothing is written unless the file still matches the fingerprint
#     the page was given, and the merge base is re-read from disk so
#     every key the page never saw survives the write.


class _Refused(Exception):
    """A request this handler answers with a status and one line of text."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(message)


def _answer(work: Callable[[], tuple]):
    """Run a mutating handler, mapping our error families onto responses.

    ConfigChangedError is the compare-and-swap refusal and has its own
    status: 409 is what tells the page to reload rather than retry.
    """
    try:
        return work()
    except _Refused as exc:
        return _json(exc.status, {"error": exc.message})
    except ConfigChangedError as exc:
        return _json(409, {"error": str(exc)})
    except ConfigError as exc:
        return _json(400, {"error": str(exc)})
    except VocalizeError as exc:
        return _json(400, {"error": str(exc)})


def _provider_or_404(name: str) -> str:
    """`name` if it is a provider, else 404 — the allowlist is the check.

    It is also the path check: the name arrives as a URL segment and is
    only ever compared against this tuple, so no `..` or separator of any
    kind can reach a filesystem or a subprocess through it.
    """
    if name not in auth.PROVIDER_NAMES:
        raise _Refused(
            404, f"Unknown provider {name!r}. Known: {', '.join(auth.PROVIDER_NAMES)}"
        )
    return name


def _fingerprint_from(payload: dict):
    """The fingerprint the page is handing back, shape-checked.

    Untrusted, and it decides whether a write lands: accepted only in the
    two shapes `wizard.fingerprint_config` produces, so a caller cannot
    send `{}` (which would match nothing) or a value whose comparison
    quietly succeeds for the wrong reason.
    """
    value = payload.get("fingerprint")
    if value == wizard.ABSENT_CONFIG:
        return wizard.ABSENT_CONFIG
    if (
        isinstance(value, dict)
        and set(value) == {"mtime_ns", "sha256"}
        and isinstance(value["mtime_ns"], int)
        and not isinstance(value["mtime_ns"], bool)
        and isinstance(value["sha256"], str)
    ):
        return {"mtime_ns": value["mtime_ns"], "sha256": value["sha256"]}
    raise _Refused(400, "expected the 'fingerprint' this page was given by /api/state")


def _settings_from(payload: dict) -> dict:
    settings = payload.get("settings")
    if not isinstance(settings, dict) or not all(isinstance(key, str) for key in settings):
        raise _Refused(400, "expected a 'settings' object")
    return settings


def _saved(state: PortalState, data: dict, fingerprint):
    """Write the merged config, then re-read what the CLI would read."""
    path = config.config_path()
    wizard.write_config_if_unchanged(path, data, fingerprint)
    state.file_config = config.load_config_file()
    return _json(200, {"ok": True, "fingerprint": wizard.fingerprint_config(path)})


def post_chain(state: PortalState, payload: dict):
    order = payload.get("order")
    if not isinstance(order, list) or not all(isinstance(name, str) for name in order):
        raise _Refused(400, "expected an 'order' list of provider names")
    fingerprint = _fingerprint_from(payload)
    # The CLI's own validator, so an unknown or duplicated name comes back
    # in the words `vocalize speak` would have used.
    config._validate_chain(order, config.config_path())

    data = dict(config.load_config_file())
    data["chain"] = list(order)
    return _saved(state, data, fingerprint)


def _provider_value(name: str, key: str, value):
    """One `[providers.<name>]` value, coerced and shape-checked."""
    if key == "speed":
        return config._coerce_speed(
            value, f"'speed' in [providers.{name}] in {config.config_path()}"
        )
    if key == "monthly_chars":
        # _validate_providers_table has the message for a bad one; this
        # only keeps a float or a string from reaching it as an int-alike.
        return value
    if not isinstance(value, str):
        raise _Refused(
            400, f"Invalid {key} {value!r} for provider {name!r}: expected a string."
        )
    if len(value) > 200 or any(not character.isprintable() for character in value):
        raise _Refused(
            400,
            f"Invalid {key} for provider {name!r}: expected a short line with no "
            f"control characters.",
        )
    return value


def post_provider(state: PortalState, name: str, payload: dict):
    _provider_or_404(name)
    settings = _settings_from(payload)
    fingerprint = _fingerprint_from(payload)

    data = dict(config.load_config_file())
    tables = {key: dict(table) for key, table in (data.get("providers") or {}).items()}
    table = dict(tables.get(name) or {})
    for key, value in settings.items():
        if key not in config.KNOWN_PROVIDER_KEYS:
            # The CLI only warns on stderr here, which a page cannot see;
            # a write refuses instead, so a typo is never saved.
            raise _Refused(
                400,
                f"unknown config key {key!r} in [providers.{name}]. "
                f"Known: {', '.join(config.KNOWN_PROVIDER_KEYS)}",
            )
        if value is None:
            table.pop(key, None)  # null clears a key rather than writing "None"
        else:
            table[key] = _provider_value(name, key, value)

    if table:
        tables[name] = table
    else:
        tables.pop(name, None)
    if tables:
        data["providers"] = tables
    else:
        data.pop("providers", None)

    config._validate_providers_table(tables, config.config_path())
    return _saved(state, data, fingerprint)


def post_stt(state: PortalState, payload: dict):
    settings = _settings_from(payload)
    fingerprint = _fingerprint_from(payload)

    data = dict(config.load_config_file())
    table = dict(data.get("stt") or {})
    for key, value in settings.items():
        if key not in config.KNOWN_STT_KEYS:
            raise _Refused(
                400,
                f"unknown config key {key!r} in [stt]. "
                f"Known: {', '.join(config.KNOWN_STT_KEYS)}",
            )
        if value is None:
            table.pop(key, None)
        else:
            table[key] = value

    # The whole merged table, not just what changed: the model/language
    # pairing rule is a check on the pair, and half of it may be already
    # in the file.
    config._validate_stt_table(table, config.config_path())
    if table:
        data["stt"] = table
    else:
        data.pop("stt", None)
    return _saved(state, data, fingerprint)


def post_login(state: PortalState, payload: dict):
    """Store an API key. The key never leaves this function.

    Not in the response, not in a log (the handler logs nothing at all),
    not in the URL — the route is a POST and the key is a body field.
    Every error message is scrubbed of it before it is returned, because
    the ones we do not write ourselves quote what they were given.
    """
    name = _provider_or_404(payload.get("provider"))
    key = payload.get("key")
    if not isinstance(key, str) or not key:
        raise _Refused(400, "No API key given — nothing was stored.")

    # The same two refusals `vocalize auth login` makes before it prompts.
    if name == "polly":
        raise _Refused(
            400,
            "Polly uses your AWS credentials (env, ~/.aws/credentials, or a "
            "profile) — nothing to store.",
        )
    if name not in auth.PROVIDER_USERNAMES:
        label = auth.PROVIDER_LABELS.get(name, name)
        raise _Refused(400, f"{label} is local and needs no credentials.")

    try:
        message = auth.login(key, name)
    except VocalizeError as exc:
        raise _Refused(400, auth.scrub(str(exc), key)) from None
    return _json(200, {"ok": True, "message": auth.scrub(message, key)})


def post_logout(state: PortalState, payload: dict):
    name = _provider_or_404(payload.get("provider"))
    if name not in auth.PROVIDER_USERNAMES:
        raise _Refused(400, f"nothing stored for {name}")
    auth.delete_key(name)  # reads the entry back, so this is a fact
    return _json(200, {"ok": True, "message": "Removed the stored API key."})


# --- preview ----------------------------------------------------------


def _one_line(exc: BaseException) -> str:
    """One short line for the page, never an upstream's response body.

    `chain.run`'s failure is a small report — a header line, one line per
    provider tried, then a hint about fallback — so the reason is the
    second line when there is one. Capped as well as cut: a provider's
    own message can carry whatever the API said back.
    """
    lines = [line.strip() for line in str(exc).splitlines() if line.strip()]
    if not lines:
        return type(exc).__name__
    reason = lines[1] if len(lines) > 1 and lines[0].endswith(":") else lines[0]
    return reason[:200]


def post_preview(state: PortalState, name: str):
    """Speak the fixed sentence through one provider and return the bytes.

    Through `chain.run` with the provider forced, exactly as `vocalize
    speak --provider` does, so the budget gate, the ledger and the audio
    cache all apply — a capped provider is refused here for the same
    reason and in the same words as in a terminal. `run` never plays: it
    returns the audio, which goes to the page as bytes for a Blob. That
    is the one sound in this project that does not take the machine-wide
    playback lock, because it is the browser that plays it.
    """
    _provider_or_404(name)
    from . import chain as chain_module
    from . import providers as providers_module

    file_config = state.file_config
    with _preview_lock:
        try:
            # The gate the chain would run, called first so a refusal can
            # be told apart from a provider that broke: same call, same
            # message, but here it is a 402 rather than one line of a
            # multi-provider failure report.
            chain_module._budget_gate(
                name, providers_module.get(name), PREVIEW_TEXT, file_config
            )
        except ProviderError as exc:
            return _json(402, {"error": str(exc)})

        try:
            audio, _spoken, ext = chain_module.run(
                PREVIEW_TEXT,
                chain=[name],
                file_config=file_config,
                cache_dir=CACHE_DIR,
                forced=True,
            )
        except VocalizeError as exc:
            return _json(502, {"error": _one_line(exc)})

    return _response(
        200,
        PREVIEW_TYPES.get(ext, "application/octet-stream"),
        audio,
        # No range requests: this is one short blob fetched once with a
        # header on it, and a partial-content path is only a way for the
        # server to be asked the same question twice.
        extra={"Accept-Ranges": "none"},
    )


# --- the local install thread -----------------------------------------


def _progress(state: PortalState, already: int):
    def report(done: int, total: int) -> None:
        state.install["downloaded"] = already + done

    return report


def _opener_kwargs() -> dict:
    return {} if OPENER is None else {"opener": OPENER}


def _install_kokoro(state: PortalState) -> None:
    from . import local as local_module
    from .local import install as install_module
    from .local import kokoro_manifest as manifest

    uv = _uv_or_raise(local_module, install_module)
    state.install["total"] = sum(entry["size"] for entry in manifest.FILES)
    done = 0
    for entry in manifest.FILES:
        state.install["step"] = f"downloading {entry['name']}"
        if not install_module.file_is_verified(entry):
            install_module.download_file(
                entry["url"],
                manifest.MODEL_DIR / entry["name"],
                entry["size"],
                entry["sha256"],
                progress=_progress(state, done),
                **_opener_kwargs(),
            )
        done += entry["size"]
        state.install["downloaded"] = done

    install_module.write_stamp()
    state.install["step"] = "warming the runtime"
    install_module.selftest(uv)


def _install_stt(state: PortalState, model: str | None) -> None:
    from . import local as local_module
    from .local import install as install_module
    from .local import whisper_manifest as manifest

    uv = _uv_or_raise(local_module, install_module)
    model = model or manifest.DEFAULT_MODEL
    if model not in manifest.MODELS:
        # An allowlist, and the only check there is: this name reaches a
        # file path and a subprocess argv.
        raise install_module.InstallError(
            f"Unknown model {model!r}. Choose one of: {', '.join(manifest.MODELS)}"
        )

    entry = manifest.file_for(model)
    state.install["total"] = entry["size"]
    state.install["step"] = f"downloading {entry['name']}"
    if not install_module.file_is_verified(entry, manifest=manifest):
        install_module.download_file(
            entry["url"],
            manifest.MODEL_DIR / entry["name"],
            entry["size"],
            entry["sha256"],
            progress=_progress(state, 0),
            **_opener_kwargs(),
        )
    state.install["downloaded"] = entry["size"]

    install_module.write_stamp(manifest=manifest, files=[entry])
    state.install["step"] = "warming the runtime"
    install_module.selftest(uv, manifest=manifest, model=model)
    state.install["step"] = "building the recorder"
    install_module.build_recorder()


def _uv_or_raise(local_module, install_module) -> str:
    uv = local_module.uv_path()
    if uv is None:
        raise install_module.InstallError(
            "uv is not installed, and the on-device runtime needs it. Install it "
            "from https://docs.astral.sh/uv/ and try again. Nothing was downloaded."
        )
    return uv


def _install_worker(state: PortalState, target: str, model: str | None) -> None:
    try:
        if target == "kokoro":
            _install_kokoro(state)
        else:
            _install_stt(state, model)
        state.install["step"] = "installed"
    except Exception as exc:  # noqa: BLE001 — a thread's traceback goes nowhere
        # One line, and never a traceback: this reaches a web page, and
        # an installer's exception text can name paths and URLs.
        state.install["error"] = _one_line(exc)
        state.install["step"] = "failed"
    finally:
        state.install["running"] = False
        state.install["done"] = True
        state.watchdog_suspended = False
        # The page was not pinging while the watchdog was suspended; without
        # this the first tick after it resumes could close the portal.
        state.note_ping()


def post_install_start(state: PortalState, payload: dict):
    target = payload.get("target")
    if target not in INSTALL_TARGETS:
        raise _Refused(400, f"expected 'target' to be one of: {', '.join(INSTALL_TARGETS)}")
    model = payload.get("model")
    if model is not None and not isinstance(model, str):
        raise _Refused(400, "expected 'model' to be a model name")

    if not state.begin_install(target):
        raise _Refused(409, "an install is already running")

    state.install_thread = threading.Thread(
        target=_install_worker,
        args=(state, target, model),
        daemon=True,
        name="vocalize-portal-install",
    )
    state.install_thread.start()
    return _json(200, dict(state.install))


# --- routing ----------------------------------------------------------


def _header(headers, name: str) -> str | None:
    """One header value, or None — including when it was sent twice.

    A duplicate is treated as absent rather than resolved to the first
    copy: two `Host` headers is not a shape any browser produces, and
    picking one of them is how header-parsing differences turn into
    bypasses.
    """
    get_all = getattr(headers, "get_all", None)
    if get_all is not None:
        values = get_all(name)
        if not values:
            return None
        return values[0] if len(values) == 1 else None
    if isinstance(headers, dict):
        for key, value in headers.items():
            if key.lower() == name.lower():
                return value
        return None
    return headers.get(name)


def _response(status: int, content_type: str, body: bytes, *, extra: dict | None = None):
    # `extra` first: a caller adding a header must never be able to drop
    # or rewrite one of the security headers by name.
    return status, {**(extra or {}), **SECURITY_HEADERS, "Content-Type": content_type}, body


def _json(status: int, payload: dict):
    return _response(status, "application/json", json.dumps(payload).encode("utf-8"))


def _asset(path: str):
    filename, content_type = _ASSETS[path]
    try:
        body = (ASSETS_DIR / filename).read_bytes()
    except OSError:
        return _json(500, {"error": "the portal page is missing from this install"})
    return _response(200, content_type, body)


def route(method: str, path: str, headers, body: bytes, *, state: PortalState):
    """Answer one request. Pure apart from reading the two static assets.

    Order matters and is the security story: origin checks first (they
    apply to routes that need no token at all), then shape, then the
    token, then the route itself.
    """
    if _header(headers, "Host") != state.expected_host:
        return _json(421, {"error": "this server answers only to 127.0.0.1"})

    origin = _header(headers, "Origin")
    if origin is not None and origin != state.origin:
        # A cross-site page cannot send the token header without a
        # preflight, but it can still make simple posts; refusing a
        # foreign Origin outright keeps those off the lockout counter.
        return _json(403, {"error": "cross-origin requests are refused"})

    if method not in ("GET", "POST"):
        return _json(405, {"error": "only GET and POST are accepted"})

    if len(body) > MAX_BODY_BYTES:
        return _json(413, {"error": "request body too large"})

    parts = urllib.parse.urlsplit(path)
    query = urllib.parse.parse_qs(parts.query, keep_blank_values=True)
    if any(key.lower() in _TOKEN_PARAM_NAMES for key in query):
        return _json(401, {"error": f"the session token is read from {TOKEN_HEADER} only"})

    payload: dict = {}
    if method == "POST":
        if body:
            try:
                parsed = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                return _json(400, {"error": "the request body is not valid JSON"})
            if not isinstance(parsed, dict):
                return _json(400, {"error": "the request body must be a JSON object"})
            payload = parsed
        if any(str(key).lower() in _TOKEN_PARAM_NAMES for key in payload):
            return _json(401, {"error": f"the session token is read from {TOKEN_HEADER} only"})

    if parts.path in _ASSETS:
        if method != "GET":
            return _json(405, {"error": "only GET is accepted here"})
        return _asset(parts.path)

    if parts.path == "/api/session":
        if method != "POST":
            return _json(405, {"error": "only POST is accepted here"})
        code = payload.get("code")
        if not isinstance(code, str) or not code:
            return _json(400, {"error": "expected a JSON object with a 'code' string"})
        token, failure = state.exchange(code)
        if token is None:
            return _json(401, {"error": failure})
        return _json(200, {"token": token})

    if not state.token_matches(_header(headers, TOKEN_HEADER)):
        return _json(401, {"error": f"a valid {TOKEN_HEADER} header is required"})

    if parts.path == "/api/state" and method == "GET":
        state.note_ping()
        return _json(200, state_payload(state.file_config, timeout=state.readiness_timeout))

    if parts.path == "/api/ping" and method == "GET":
        state.note_ping()
        return _json(200, {"ok": True})

    if parts.path == "/api/local/install/status" and method == "GET":
        state.note_ping()
        return _json(200, dict(state.install))

    if method == "POST":
        state.note_ping()
        if parts.path == "/api/chain":
            return _answer(lambda: post_chain(state, payload))
        if parts.path.startswith("/api/provider/"):
            name = parts.path[len("/api/provider/"):]
            return _answer(lambda: post_provider(state, name, payload))
        if parts.path == "/api/stt":
            return _answer(lambda: post_stt(state, payload))
        if parts.path == "/api/auth/login":
            return _answer(lambda: post_login(state, payload))
        if parts.path == "/api/auth/logout":
            return _answer(lambda: post_logout(state, payload))
        if parts.path.startswith("/api/voices/") and parts.path.endswith("/preview"):
            name = parts.path[len("/api/voices/"): -len("/preview")]
            return _answer(lambda: post_preview(state, name))
        if parts.path == "/api/local/install/start":
            return _answer(lambda: post_install_start(state, payload))

    return _json(404, {"error": "no such route"})


# --- server -----------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "vocalize"
    sys_version = ""
    # StreamRequestHandler.setup() applies this with settimeout(), and
    # handle_one_request already treats a timed-out read as a closed
    # connection.
    timeout = HANDLER_TIMEOUT_SECONDS

    def log_message(self, format, *args):
        """Log nothing.

        The one-time code never reaches the server (it lives in the URL
        fragment), and this keeps it that way for everything else: no
        request line, no query string, no header value is written
        anywhere.
        """

    def send_error(self, code, message=None, explain=None):
        """Answer http.server's own refusals through the same path.

        `handle_one_request` refuses a verb with no `do_*` method, a
        request line over 64 KiB and an unparsable request version by
        itself, before `_handle` ever runs — and the stock `send_error`
        answers those with an HTML page carrying none of the security
        headers, so an `<iframe>` pointed at a long URL would get a
        framable page served by this port. The reason is not echoed
        back: it quotes the request line, which is attacker text.
        """
        self._respond(*_json(code, {"error": "the portal refused this request"}), close=True)

    def _respond(self, status, headers, body, *, close=False):
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        if close or self.command == "HEAD":
            self.close_connection = True
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _handle(self, method):
        state = self.server.portal_state

        if self.headers.get("Transfer-Encoding"):
            # http.server does not decode chunked bodies; accepting the
            # header would mean answering a request whose body we never
            # read, and leaving the connection out of step.
            self._respond(
                *_json(400, {"error": "chunked request bodies are not accepted"}),
                close=True,
            )
            return

        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) > 1:
            # Two lengths is the classic smuggling shape: whichever copy
            # this server picks, something in front of it may pick the
            # other. Refused with the connection, because the bytes we
            # would not read are the next request's line to anything that
            # kept talking on it.
            self._respond(*_json(400, {"error": "bad Content-Length"}), close=True)
            return

        # Strictly decimal, not int()'s idea of a number: it accepts
        # "5_0", unicode digits and surrounding space, and a length the
        # server reads differently from the way anything in front of it
        # would is the seed of a request-smuggling bug.
        raw_length = lengths[0] if lengths else None
        if raw_length is not None and not _DECIMAL.fullmatch(raw_length):
            self._respond(*_json(400, {"error": "bad Content-Length"}), close=True)
            return
        length = int(raw_length) if raw_length else 0
        if length > MAX_BODY_BYTES:
            # Refused without reading it: the point of a cap is not to
            # buffer the bytes in the first place.
            self._respond(*_json(413, {"error": "request body too large"}), close=True)
            return

        body = self.rfile.read(length) if length else b""
        try:
            answer = route(method, self.path, self.headers, body, state=state)
        except Exception:  # noqa: BLE001 — never drop a connection over a route bug
            # A route that raises would otherwise reach socketserver, which
            # closes the socket with no status line and no security headers
            # and prints a traceback. The message is fixed: an exception's
            # text here can carry configuration or credential material.
            answer = _json(500, {"error": "the portal hit an internal error"})
        self._respond(*answer)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_HEAD(self):
        self._handle("HEAD")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_OPTIONS(self):
        self._handle("OPTIONS")


class _Server(ThreadingHTTPServer):
    """The portal's server: silent, and never joined at close."""

    # Handler threads are daemons and keep-alive connections outlive the
    # request, so joining them at close would block on an idle browser tab
    # rather than shut anything down.
    daemon_threads = True
    block_on_close = False

    portal_state: PortalState

    def handle_error(self, request, client_address):
        """Say nothing.

        socketserver's default prints the traceback — which names install
        paths and can carry request material — to stderr, which is the one
        thing `log_message` was overridden to prevent. `_handle` already
        turns a route failure into a 500; what reaches here is a broken or
        timed-out connection, and there is nothing to say about it.
        """


@dataclass
class Portal:
    """A running portal: the address to open, and the way to stop it."""

    server: ThreadingHTTPServer
    state: PortalState
    thread: threading.Thread
    url: str

    @property
    def port(self) -> int:
        return self.state.port

    def wait(self, timeout: float | None = None) -> str | None:
        """Block until the server stops; returns why it stopped."""
        self.thread.join(timeout)
        return self.state.shutdown_reason

    def stop(self, reason: str = "the portal was closed") -> None:
        self.state.request_shutdown(reason)
        self.thread.join(5.0)
        self.server.server_close()


def _watchdog(state: PortalState) -> None:
    """Close the portal once the page stops pinging.

    A browser tab that was closed leaves the server holding the machine's
    settings on an open port with a live session token; N missed pings is
    how it finds out. Suspended (run 8's hook) while a long install runs,
    because that is the one time the page legitimately goes quiet.
    """
    interval = max(min(PING_INTERVAL_SECONDS, state.idle_timeout / 2), 0.01)
    while state.shutdown_reason is None:
        time.sleep(interval)
        if state.watchdog_suspended:
            state.note_ping()
            continue
        if _now() - state.last_seen > state.idle_timeout:
            state.request_shutdown(IDLE_REASON)
            return


def serve(
    file_config: dict,
    *,
    open_browser: Callable[[str], object] | None = None,
    port: int = 0,
    readiness_timeout: float = 2.0,
    idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
) -> Portal:
    """Start the portal on a random loopback port and return its address.

    `open_browser` defaults to None — *not* to `webbrowser.open` — so
    that nothing in a test or a script can open a browser by accident.
    The `vocalize portal` command passes `webbrowser.open` explicitly.
    """
    state = PortalState(
        file_config,
        readiness_timeout=readiness_timeout,
        idle_timeout=idle_timeout,
    )
    httpd = _Server((BIND_HOST, port), _Handler)
    state.port = httpd.server_address[1]
    httpd.portal_state = state

    def close() -> None:
        # shutdown() blocks until serve_forever's loop exits, so it can
        # never be called from a request thread. server_close() after it
        # drops the listening socket, so a caller that just spent the
        # last of its five code guesses finds the port refusing, not
        # accepting into a server that no longer answers.
        httpd.shutdown()
        httpd.server_close()

    state.on_shutdown = lambda: threading.Thread(
        target=close, daemon=True, name="vocalize-portal-shutdown"
    ).start()

    thread = threading.Thread(
        target=httpd.serve_forever, daemon=True, name="vocalize-portal-serve"
    )
    thread.start()
    threading.Thread(
        target=_watchdog, args=(state,), daemon=True, name="vocalize-portal-watchdog"
    ).start()

    portal = Portal(server=httpd, state=state, thread=thread, url=state.url())
    if open_browser is not None:
        open_browser(portal.url)
    return portal


def _main() -> int:
    """`python -m vocalize.portal` — a smoke entry, not the CLI command.

    The `vocalize portal` command is T-64 (run 8); this exists so the
    server can be driven by hand without one.
    """
    portal = serve(config.load_config_file())
    print(portal.url, flush=True)
    print(portal.wait() or "the portal stopped")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
