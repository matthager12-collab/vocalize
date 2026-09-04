"""The config portal's HTTP server: auth bootstrap and read-only state.

`vocalize portal` opens a page in the browser that edits the same config
file the wizard and `vocalize chain` write. That page talks to a server
running inside the vocalize process, so the whole surface is a loopback
socket on a random port with nothing in front of it — no framework, no
cookies, no CSRF middleware, and no second process to authenticate to.
The auth story is therefore the design's, not a library's (DEC-004):

    1. `start()` mints a one-time code and a session token, both
       `secrets.token_urlsafe(32)`, and returns a URL whose *fragment*
       carries the code. A fragment never leaves the browser, so the code
       reaches no server log, no `Referer`, and no proxy.
    2. The page reads the fragment and POSTs it to `/api/session` within
       `CODE_TTL` seconds. The code is single-use.
    3. Every later call carries the session token in the `X-Vocalize-Token`
       header. The header is the *only* place a token is read from: a
       token in a query string is refused outright, because a URL ends up
       in history, in `Referer`, and in argv.
    4. `Host` must equal `127.0.0.1:<port>` on every request, static page
       included. A DNS-rebinding attack arrives with the attacker's name
       in `Host`, so pinning it is what keeps a hostile page on another
       origin from driving this one.
    5. `MAX_CODE_ATTEMPTS` failed exchanges and the server shuts down.
       32 url-safe bytes are not guessable, but a server that is being
       guessed at has no reason to keep answering.

Two smaller properties fall out of the same reasoning. `GET /` serves a
file verbatim and never interpolates anything, so the page cannot carry a
secret. And the request line is never logged: it can contain a query
string, and a query string is exactly where a secret must never be.

The module is built around `Portal.route()`, which takes a method, a
path, headers and a body and returns `(status, headers, body)` with no
socket anywhere near it. `_Handler` is a thin shell that reads a request
off the wire, calls `route()`, and writes the answer back — so every
security property above is testable as a function call.

Writes (`POST /api/chain`, `/api/provider/<name>`, `/api/stt`,
`/api/auth/login`) are compare-and-swap (DEC-005): `/api/state` hands the
page the config file's fingerprint, taken *before* the values that go with
it are parsed, every write hands that fingerprint back, and a file that
moved in between is refused with 409 rather than clobbered. Each value
goes through the same `config._validate_*` the CLI uses, so a bad one
comes back in the CLI's own wording. Two places are stricter than a hand
edit, deliberately: an unknown key in `[providers.*]` or `[stt]` is
refused rather than warned about, because a warning goes to stderr and the
page cannot see one, and the free-text provider values the CLI never
validates at all must at least be short printable strings.

`POST /api/voices/<name>/preview` speaks one fixed sentence through
`chain.run(..., forced=True)`, which never plays: the bytes are the
response and the browser plays them from a Blob. That is the one sound in
this project that does not take the machine-wide playback lock (design §
Playback). Previews are serialized on one *module* lock — Kokoro's
resident worker is process-global — and the wait for it is bounded, so a
held-down preview button answers 503 rather than queueing one handler
thread and one paid synthesis per click (DEC-018).

`POST /api/local/install/start` runs the download off the request thread
and `GET /api/local/install/status` returns the progress dict the page
polls. One install at a time, the model name allowlisted on the request
thread because it reaches both a file path and a subprocess argv, and the
whole worker inside `suspend_idle()` so a 488 MB download does not look
like an idle page.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import auth, config, ledger, readiness, wizard
from .exceptions import ConfigChangedError, ProviderError, VocalizeError
from .tts import DEFAULT_CACHE_DIR

#: The header the session token is read from. The only place it is read from.
TOKEN_HEADER = "X-Vocalize-Token"

#: How long the one-time code in the URL fragment stays usable.
CODE_TTL = 60.0

#: Failed `/api/session` exchanges before the server stops serving.
MAX_CODE_ATTEMPTS = 5

#: No request for this long and the portal closes itself. The page holds it
#: open with `GET /api/ping`; a browser tab closed without warning does not.
IDLE_TIMEOUT = 15 * 60.0
_IDLE_POLL = 0.5

#: Largest request body accepted, before anything parses it.
MAX_BODY = 64 * 1024

#: Stands in for a body the handler deliberately did not read. Handing this
#: to `route()` rather than answering 413 on the spot keeps the `Host` pin
#: first on every request, without buffering the bytes the cap exists to
#: refuse.
_OVERSIZED = b"\0" * (MAX_BODY + 1)

#: Per-probe budget for `/api/state`, matching `vocalize status`.
STATE_TIMEOUT = 2.0

#: The five words `auth.key_source` can return. Anything else out of a key
#: probe means the probe did not finish — see `_key_info`.
_KEY_SOURCES = ("flag", "environment", ".env file", "keychain", "not found")

CSP = "default-src 'self'; media-src 'self' blob:; frame-ancestors 'none'"

#: On every response, including every error. `media-src ... blob:` is there
#: for the preview audio run 8 serves: the page fetches bytes with the token
#: header and plays them from a Blob, which `default-src 'self'` alone blocks.
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

#: The clause this deliberately does *not* carry is "nothing was changed".
#: It was there to reassure, and it was false twice over: a legitimate page
#: may already have saved settings before the junk requests arrived, and a
#: model install may be part-way through a download the process is about to
#: abandon (`discard_partial_download`). What is always true is the part
#: about whoever sent the codes, so that is what it now says.
LOCKOUT_MESSAGE = (
    "The vocalize portal was shut down: too many wrong one-time codes. "
    "They never got a session token, so they read and changed nothing. "
    "Run `vocalize portal` again to get a fresh link."
)

_HOST_REFUSED = (
    "This portal only answers requests addressed to its own loopback address."
)
_ORIGIN_REFUSED = "This portal only answers requests from its own page."
_TOKEN_IN_URL_REFUSED = (
    "A session token is never accepted from a URL. Send it in the "
    f"{TOKEN_HEADER} header."
)
_TOKEN_REFUSED = "A valid session token is required."

#: The preview's fixed sentence. A constant, never text from the page: a
#: preview is a voice check, and letting the browser choose the words would
#: turn a settings page into an unmetered synthesis endpoint.
PREVIEW_TEXT = "This is how vocalize will sound."

#: Content-Type by the provider's `AUDIO_EXT`. Anything unrecognised is
#: served as bytes rather than guessed at: with `nosniff`, a wrong type is a
#: preview that will not play, and a guessed one is worse.
PREVIEW_TYPES = {"mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4"}

#: The same audio cache `vocalize speak` uses — that is the point: a preview
#: of a voice you then read with is already rendered and paid for. Named
#: here rather than left to `chain.run`'s default because that default is
#: bound at def time, so a test has nothing else to point elsewhere.
CACHE_DIR = DEFAULT_CACHE_DIR

#: One preview at a time, process-wide rather than per-`Portal`: Kokoro's
#: provider keeps a single resident worker whose JSON-lines protocol has no
#: request ids, so two previews at once would read each other's replies.
_preview_lock = threading.Lock()

#: How long a preview waits for the one in front of it. Bounded rather than
#: unbounded: `_Handler.timeout` is a *socket* timeout and never reclaims a
#: thread parked on a lock, so an unbounded wait lets a held-down preview
#: button pile up one handler thread — and one paid synthesis — per click.
PREVIEW_WAIT = 30.0

#: What `POST /api/local/install/start` will install.
INSTALL_TARGETS = ("kokoro", "stt")

#: Where run 9's page will live. Absent until then, so `/` and `/portal.js`
#: fall back to a placeholder rather than 404 — and, more to the point,
#: rather than this run creating the files whose absence is run 9's own
#: entry guard.
_ASSETS = Path(__file__).resolve().parent / "assets"

_PLACEHOLDER_HTML = (
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
    "<title>vocalize</title></head><body>"
    "<p>The vocalize config portal page is not built yet.</p>"
    "</body></html>"
)
_PLACEHOLDER_JS = "// The vocalize config portal script is not built yet.\n"

# Route paths carrying a provider name, normalised to a fixed key before
# dispatch. The pattern is the validation: a name that is not a short
# lowercase word never reaches a handler, so no route parameter can carry a
# path separator, a traversal sequence or a control character.
_PARAMETERIZED = (
    (re.compile(r"^/api/provider/([a-z0-9_-]{1,32})$"), "/api/provider/*"),
    (re.compile(r"^/api/voices/([a-z0-9_-]{1,32})/preview$"), "/api/voices/*/preview"),
)


def _normalize(path: str) -> tuple[str, str | None]:
    """`/api/provider/google` -> `("/api/provider/*", "google")`.

    A path matching no pattern comes back unchanged with no name, so a
    provider name is the only route parameter that exists and it is
    constrained by the pattern that captured it.
    """
    for pattern, key in _PARAMETERIZED:
        match = pattern.match(path)
        if match:
            return key, match.group(1)
    return path, None


#: Every route the portal answers, as (method, path, auth). `auth` is
#: "none" (static, no secret served), "code" (the one-time exchange) or
#: "token" (the session header). The paths are real request paths, so the
#: tests iterate this list to prove the `Host` and token negatives hold on
#: *every* route rather than on the handful someone remembered. A route
#: added without a `Host` or token check therefore fails the suite.
ROUTES: tuple[tuple[str, str, str], ...] = (
    ("GET", "/", "none"),
    ("GET", "/portal.js", "none"),
    ("POST", "/api/session", "code"),
    ("GET", "/api/state", "token"),
    ("GET", "/api/ping", "token"),
    ("POST", "/api/chain", "token"),
    ("POST", "/api/provider/elevenlabs", "token"),
    ("POST", "/api/stt", "token"),
    ("POST", "/api/auth/login", "token"),
    ("POST", "/api/voices/kokoro/preview", "token"),
    ("POST", "/api/local/install/start", "token"),
    ("GET", "/api/local/install/status", "token"),
)

# Both derived from ROUTES rather than written out again, so the table above
# stays the one place a route is declared.
_AUTH_FOR: dict[tuple[str, str], str] = {
    (method, _normalize(path)[0]): kind for method, path, kind in ROUTES
}
_METHODS_FOR: dict[str, set[str]] = {}
for _method, _path, _kind in ROUTES:
    _METHODS_FOR.setdefault(_normalize(_path)[0], set()).add(_method)


def _header(headers, name: str) -> str:
    """One header value, case-insensitively, from a dict or an HTTP message.

    `http.server` hands over an `email.message.Message`, which is already
    case-insensitive; a test hands over a plain dict, which is not. Both
    arrive here.
    """
    value = headers.get(name)
    if value is None:
        wanted = name.lower()
        for key, candidate in headers.items():
            if key.lower() == wanted:
                value = candidate
                break
    return (value or "").strip()


def _key_row(name: str) -> readiness.Row:
    """Where this provider's key comes from, and a preview safe to display.

    Returned as a `Row` so it can run through readiness' one-in-flight-probe
    registry (design § Readiness aggregation) — reading a key can block on a
    macOS keychain dialog, and the portal polls `/api/state`. This is not a
    status row and never reaches `vocalize status`: `state` carries the
    `key_source` word and `detail` the masked preview.
    """
    source = auth.key_source(None, name)
    if source == "keychain":
        key = auth.stored_key(name) or ""
    elif source in ("environment", ".env file"):
        key = os.environ.get(auth.PROVIDER_ENV_VARS.get(name, ""), "")
    else:
        # "flag" cannot happen here (no flag reaches the portal) and
        # "not found" has nothing to preview.
        key = ""
    return readiness.Row(f"key {name}", source, auth.masked(key) if key else "", "")


def _key_info(row: readiness.Row) -> dict:
    """`{"source", "masked"}` from one key probe's row."""
    if row.state in _KEY_SOURCES:
        return {"source": row.state, "masked": row.detail or None}
    # `_join_probe`'s own row: still-checking means the probe thread is alive
    # and the next poll may answer. Anything else is a probe that finished
    # by raising, and a page that renders that as "checking" spins forever.
    if row.detail == readiness.STILL_CHECKING:
        return {"source": "checking", "masked": None}
    return {"source": "error", "masked": None}


def _key_states(timeout: float) -> dict[str, dict]:
    """Every provider's key state, the whole batch inside one `timeout`.

    Through readiness' probe registry, which keeps at most one in-flight
    probe per name, so a wedged keychain leaks one thread however often the
    page polls. In one `run_probes` call rather than one call per name:
    a keychain dialog wedges all three key providers at once, and joining
    them in turn cost three timeouts on every poll.
    """
    names = [
        name
        for name in auth.PROVIDER_NAMES
        if name in auth.PROVIDER_ENV_VARS or name in auth.PROVIDER_USERNAMES
    ]
    rows = readiness.run_probes(
        [(f"key {name}", lambda n=name: _key_row(n)) for name in names], timeout
    )
    states = {name: {"source": "not applicable", "masked": None} for name in auth.PROVIDER_NAMES}
    states.update(zip(names, (_key_info(row) for row in rows)))
    return states


def _fault(exc: BaseException) -> str:
    """The message for a failure we planned for, the type for one we did not.

    An unplanned exception's message is untrusted text — the same reason
    `readiness._start_probe` reports the type and never `str(exc)`.
    """
    return str(exc) if isinstance(exc, (VocalizeError, OSError)) else type(exc).__name__


def _add_error(entry: dict, exc: BaseException) -> None:
    """Record a failure without dropping one already recorded.

    Settings, budget and ledger fail for unrelated reasons and the page
    needs to see both; `entry["error"] or ...` silently kept the first one
    and threw the second away.
    """
    fault = _fault(exc)
    entry["error"] = f"{entry['error']}; {fault}" if entry["error"] else fault


def _provider_state(name: str, file_config: dict, chain: list[str], key: dict) -> dict:
    """One provider's entry. Nothing in here may raise.

    A hand-edited `[providers.<name>]`, a ledger file someone put a list in,
    a probe that blew up — each is *that* provider's problem, and none of
    them may be the traceback that hides every other provider too. So the
    guards are `Exception`, not `VocalizeError`: the failures that reach the
    page are exactly the unplanned ones.
    """
    entry: dict = {
        "label": auth.PROVIDER_LABELS.get(name, name),
        "in_chain": name in chain,
        "budget": None,
        "used": 0,
        "exhausted": False,
        "settings": None,
        "error": None,
        "key": key,
    }
    try:
        settings = config.resolve_provider_settings(
            name, file_config, primary=(bool(chain) and name == chain[0])
        )
    except Exception as exc:  # noqa: BLE001 — one provider, not the page
        _add_error(entry, exc)
    else:
        entry["settings"] = {
            "voice": settings.voice_id,
            "model": settings.model_id,
            "speed": settings.speed,
            "language": settings.language,
            "region": settings.region,
            "profile": settings.profile,
        }
    try:
        entry["budget"] = config.budget_for(name, file_config)
        entry["used"], entry["exhausted"] = ledger.status(name)
    except Exception as exc:  # noqa: BLE001 — one provider, not the page
        _add_error(entry, exc)
    return entry


class _Refused(Exception):
    """A refusal this module chose: one status and one line of text."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(message)


def _payload(body: bytes) -> dict:
    """The request body as a JSON object, or a 400.

    Parsed inside the handler rather than in `route()`: `route()`'s order —
    Host, Origin, lockout, token-in-URL, body size, route, auth — is the
    security story, and a parse inserted into it would put a 400 in front
    of the token check.
    """
    try:
        parsed = json.loads(body.decode("utf-8")) if body else {}
    except (UnicodeDecodeError, ValueError):
        raise _Refused(400, "That request body is not valid JSON.") from None
    if not isinstance(parsed, dict):
        raise _Refused(400, "That request body is not a JSON object.")
    return parsed


def _provider_or_404(name) -> str:
    """`name` if it is a provider, else 404. The allowlist is the check.

    The submitted value is never echoed. On the path routes it is already
    constrained by `_PARAMETERIZED`'s pattern and is visible in the URL
    anyway; on `/api/auth/login` it comes out of the request body, where a
    mis-wired form could put the API key in it.
    """
    if name not in auth.PROVIDER_NAMES:
        raise _Refused(404, f"Unknown provider. Known: {', '.join(auth.PROVIDER_NAMES)}")
    return name


def _fingerprint_from(payload: dict):
    """The fingerprint the page is handing back, shape-checked.

    Untrusted, and it decides whether a write lands. Accepted only in the
    two shapes `wizard.fingerprint_config` produces, and rebuilt rather
    than passed through, so no extra key can ride along and no comparison
    can succeed for the wrong reason.
    """
    value = payload.get("fingerprint")
    if value == wizard.ABSENT_CONFIG:
        return wizard.ABSENT_CONFIG
    if (
        isinstance(value, dict)
        and set(value) == {"mtime_ns", "sha256"}
        # bool is an int subclass, and `True` is not a timestamp.
        and isinstance(value["mtime_ns"], int)
        and not isinstance(value["mtime_ns"], bool)
        and isinstance(value["sha256"], str)
    ):
        return {"mtime_ns": value["mtime_ns"], "sha256": value["sha256"]}
    raise _Refused(400, "Expected the 'fingerprint' this page was given by /api/state.")


def _settings_from(payload: dict) -> dict:
    settings = payload.get("settings")
    if not isinstance(settings, dict) or not all(isinstance(key, str) for key in settings):
        raise _Refused(400, "Expected a 'settings' object.")
    return settings


def _provider_value(name: str, key: str, value):
    """One `[providers.<name>]` value, coerced and shape-checked.

    `speed` goes through the CLI's own coercion with the source phrase
    `resolve_provider_settings` uses, so a bad one reads exactly as it
    would from a hand-edited file. `monthly_chars` is passed through for
    `_validate_providers_table` to judge for the same reason. The rest —
    voice, model, engine, language, region, profile — the CLI never
    validates at all, so this is the only check they ever get: the
    submitted value is never echoed back, because there is no length or
    content bound on what a page can put in one.
    """
    if key == "speed":
        return config._coerce_speed(
            value, f"'speed' in [providers.{name}] in {config.config_path()}"
        )
    if key == "monthly_chars":
        return value
    if (
        not isinstance(value, str)
        or len(value) > 200
        or any(not character.isprintable() for character in value)
    ):
        raise _Refused(
            400,
            f"Invalid {key} for provider {name!r}: expected a short line of "
            f"printable text.",
        )
    return value


def _one_line(exc: BaseException) -> str:
    """One short line of a failure report, for the page.

    `chain.run`'s failure is a small report — a header line, one line per
    provider tried, then a hint about fallback — so the reason is the
    second line when there is one.

    What this guarantees is exactly "one line, at most 200 characters". It
    is not a promise that no upstream text appears: a provider wraps
    whatever the API said back (`ProviderTransientError(NAME, str(exc))`),
    and a single-line body is a single line. That is the same rule `_fault`
    already applies to a `VocalizeError` everywhere else on this surface —
    planned exception, its own message — with a cap added, and the cap is
    the actual bound.
    """
    lines = [line.strip() for line in str(exc).splitlines() if line.strip()]
    if not lines:
        return type(exc).__name__
    reason = lines[1] if len(lines) > 1 and lines[0].endswith(":") else lines[0]
    return reason[:200]


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
        "note": None,
    }


def _install_model(value) -> str:
    """The speech-to-text model to install, allowlisted before anything else.

    This name reaches a filesystem path (`manifest.model_path`) and a
    subprocess argv (`manifest.selftest_argv`), so it is checked against the
    manifest's own list and never sanitised. Checked on the request thread
    rather than in the worker, so a bad name is a 400 instead of a 200
    followed by an error in the progress dict — and nothing is downloaded
    either way. The submitted value is not echoed, for the reason
    `_provider_or_404` gives.
    """
    from .local import whisper_manifest as manifest

    if value is None:
        return manifest.DEFAULT_MODEL
    if not isinstance(value, str) or value not in manifest.MODELS:
        raise _Refused(400, f"Unknown model. Choose one of: {', '.join(manifest.MODELS)}")
    return value


def _uv_or_raise(install_module) -> str:
    """The `uv` both runtimes need, or an `InstallError` naming the fix.

    `local.uv_path` for both targets: `providers.kokoro.uv_path` is that
    same function re-exported, so the CLI's two spellings resolve alike.
    """
    from . import local as local_module

    uv = local_module.uv_path()
    if uv is None:
        raise install_module.InstallError(
            "uv is not installed, and the on-device runtime needs it. Install it "
            "from https://docs.astral.sh/uv/ and try again. Nothing was downloaded."
        )
    return uv


class Portal:
    """One portal server: its secrets, its routes and its lifetime.

    Construct, `start()`, hand the returned URL to a browser, and let the
    idle watchdog close it. Every piece of state that a request can reach
    lives here rather than at module scope, so a test builds one, drives
    `route()` directly, and throws it away.
    """

    def __init__(
        self,
        *,
        idle_timeout: float = IDLE_TIMEOUT,
        probe_timeout: float = STATE_TIMEOUT,
    ) -> None:
        self.port: int | None = None
        self.locked_out = False
        self.idle_timeout = idle_timeout
        self.probe_timeout = probe_timeout
        self._code: str | None = secrets.token_urlsafe(32)
        self._token = secrets.token_urlsafe(32)
        self._code_attempts = 0
        self._minted = time.monotonic()
        self._seen = time.monotonic()
        self._suspended = 0
        # Re-entrant: the code exchange holds it across _refuse_code and
        # lock_out, both of which take it themselves.
        self._lock = threading.RLock()
        self._stopping = threading.Event()
        self._server: ThreadingHTTPServer | None = None
        # Written by the install worker thread, read by `/api/local/install/
        # status` — both under `_lock`, so a poll never catches a half-built
        # dict and a second `/start` never races the first into the slot.
        self._install: dict = _idle_install()
        self._install_thread: threading.Thread | None = None
        # The `.part` the worker currently has open, so a portal that
        # closes mid-download can delete it. `download_file` removes its
        # own part file on any exception, Ctrl-C included — but that
        # cleanup runs in the worker, and the worker is a daemon thread
        # that is killed without unwinding when the process exits.
        self._downloading: Path | None = None

    # --- lifetime -----------------------------------------------------

    @property
    def origin(self) -> str:
        """The one `Host` value this portal answers to."""
        return f"127.0.0.1:{self.port}"

    def start(self) -> str:
        """Bind a random loopback port, serve, and return the opening URL.

        The URL's code is in the fragment, so it is never sent to any
        server — including this one. The caller hands it to
        `webbrowser.open` and prints it; it is the only time the code is
        readable outside this object.
        """
        server = _Server(("127.0.0.1", 0), _Handler)
        server.daemon_threads = True
        server.portal = self  # type: ignore[attr-defined]
        self._server = server
        self.port = server.server_address[1]
        self._minted = time.monotonic()
        self._seen = time.monotonic()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        threading.Thread(target=self._watch_idle, daemon=True).start()
        return f"http://{self.origin}/#code={self._code}"

    def stop(self) -> None:
        """Close the socket. Safe from inside a request handler."""
        self._stopping.set()
        server, self._server = self._server, None
        if server is not None:
            def close() -> None:
                # shutdown() waits for serve_forever to notice, which never
                # happens if we call it from the thread serving this
                # request. server_close() is what actually releases the
                # listening socket — without it the port stays bound and
                # accepting until the collector happens to free the server.
                server.shutdown()
                server.server_close()

            threading.Thread(target=close, daemon=True).start()

    def discard_partial_download(self) -> str | None:
        """Delete the `.part` an abandoned install left, and say what went.

        The portal closes on Ctrl-C, on the idle watchdog and on a lockout,
        and any of the three can land while the install worker is minutes
        into a 488 MB download. The worker is not joined: that download
        will not finish inside any wait worth making, and a 488 MB download
        cannot be made atomic. So the file is abandoned — and because the
        worker is a daemon thread killed without unwinding, nothing runs
        `download_file`'s own part-file cleanup on the way out.

        The part file is not resumable (`download_file` opens it `"wb"`
        every time), so the only thing to do with it is remove it.
        Unlinking one the worker still has open is safe on this platform:
        it keeps writing to an inode nothing can reach, and the space comes
        back when the process exits.

        Returns the line the caller should print, or None when no install
        was in flight — which is the answer nearly every time.
        """
        with self._lock:
            if not self._install["running"]:
                return None
            cut = f"The {self._install['target']} install was cut short."
            part, self._downloading = self._downloading, None
        if part is None:
            # Claimed, but not yet inside a download — resolving `uv`, or
            # warming the runtime. Nothing on disk to take back.
            return cut
        try:
            part.unlink()
        except FileNotFoundError:
            # The path is claimed before `download_file` opens it, so a cut
            # this early has nothing on disk yet. Say only what happened.
            return cut
        except OSError as exc:
            return f"{cut} Delete {part} yourself to reclaim the space ({exc})."
        return f"{cut} Its partial download was deleted ({part})."

    def serve_until_stopped(self, poll: float = 0.5) -> None:
        """Block the caller until the watchdog, a lockout or Ctrl-C ends it."""
        try:
            while not self._stopping.wait(poll):
                pass
        except KeyboardInterrupt:
            self.stop()

    @contextlib.contextmanager
    def suspend_idle(self) -> Iterator[None]:
        """Hold the idle watchdog off while something slow runs.

        Run 8's model install takes minutes with no request in between; the
        page is not idle, it is waiting. Re-entrant, and the idle clock is
        reset on the way out so a long install never lands the portal one
        tick from a shutdown.
        """
        with self._lock:
            self._suspended += 1
        try:
            yield
        finally:
            with self._lock:
                self._suspended -= 1
                self._seen = time.monotonic()

    def _watch_idle(self) -> None:
        while not self._stopping.wait(_IDLE_POLL):
            if self._suspended > 0:
                # Waiting on an install is not idleness.
                self._seen = time.monotonic()
                continue
            if time.monotonic() - self._seen > self.idle_timeout:
                print(
                    "The vocalize portal closed itself after "
                    f"{int(self.idle_timeout)}s with nothing to do.",
                    file=sys.stderr,
                )
                self.stop()
                return

    def lock_out(self) -> None:
        """Too many wrong codes: stop answering and close the socket."""
        with self._lock:
            if self.locked_out:
                return
            self.locked_out = True
            self._code = None
        print(LOCKOUT_MESSAGE, file=sys.stderr)
        self.stop()

    # --- replies ------------------------------------------------------

    def _reply(
        self,
        status: int,
        payload: dict | None = None,
        *,
        body: bytes = b"",
        content_type: str = "application/json; charset=utf-8",
        extra: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """The single exit from `route()`, so no response can miss a header."""
        if payload is not None:
            # default=str: TOML has types json does not (a bare date in a
            # hand-edited [providers.*] value reaches here). Rendering it as
            # text is what the page that exists to fix it needs.
            body = json.dumps(payload, default=str).encode("utf-8")
        # `extra` first, deliberately: spread last, a caller could rewrite
        # `Content-Security-Policy` by name and this function would stop
        # being the thing that guarantees the headers.
        headers = {**(extra or {}), **SECURITY_HEADERS}
        headers["Content-Type"] = content_type
        return status, headers, body

    def _static(self, filename: str, content_type: str, placeholder: str):
        """Serve an asset file verbatim — never a template, never a secret."""
        try:
            text = (_ASSETS / filename).read_text(encoding="utf-8")
        except OSError:
            text = placeholder
        return self._reply(200, body=text.encode("utf-8"), content_type=content_type)

    # --- routing ------------------------------------------------------

    def route(self, method: str, path: str, headers, body: bytes = b""):
        """Answer one request: `(status, headers, body)`. No socket involved.

        The order is deliberate. `Host` is checked before anything else
        looks at the request, because a rebinding attack is a request that
        should never have been considered at all. Then a cross-origin
        `Origin`, then the URL is refused if it carries a token. Then the
        route is resolved, and only a recognised route gets as far as an
        auth check.
        """
        if _header(headers, "Host") != self.origin:
            return self._reply(403, {"error": _HOST_REFUSED})

        origin = _header(headers, "Origin")
        if origin and origin != f"http://{self.origin}":
            # A hostile page's POST needs no preflight and reads no answer,
            # but it does carry `Origin` — and that is the only thing that
            # separates it from our own page, which sends `Origin` too on a
            # same-origin POST. Refused here, before `_refuse_code` can
            # count it: otherwise any tab open anywhere shuts the portal
            # down in five requests without knowing a thing. `curl` sends no
            # `Origin` and is unaffected.
            return self._reply(403, {"error": _ORIGIN_REFUSED})

        if self.locked_out:
            return self._reply(503, {"error": LOCKOUT_MESSAGE})

        split = urlsplit(path)
        query = parse_qs(split.query)
        if any(k.lower() in ("token", TOKEN_HEADER.lower()) for k in query):
            return self._reply(403, {"error": _TOKEN_IN_URL_REFUSED})

        if len(body) > MAX_BODY:
            return self._reply(413, {"error": "That request body is too large."})

        route_path, name = _normalize(split.path)
        allowed = _METHODS_FOR.get(route_path)
        if allowed is None:
            return self._reply(404, {"error": "No such route."})
        if method not in allowed:
            return self._reply(405, {"error": "That method is not allowed here."})

        if _AUTH_FOR[(method, route_path)] == "token":
            if not self._token_ok(headers):
                return self._reply(403, {"error": _TOKEN_REFUSED})
            # Only the page keeps the portal open. An anonymous `GET /` or a
            # 403 is not activity, or a stale tab — or a hostile one — holds
            # the watchdog off for as long as it cares to poll.
            self._seen = time.monotonic()

        return self._handle(route_path, body, name)

    def _handle(self, route_path: str, body: bytes, name: str | None):
        if route_path == "/":
            return self._static("portal.html", "text/html; charset=utf-8", _PLACEHOLDER_HTML)
        if route_path == "/portal.js":
            return self._static(
                "portal.js", "application/javascript; charset=utf-8", _PLACEHOLDER_JS
            )
        if route_path == "/api/session":
            return self._session(body)
        if route_path == "/api/state":
            return self._state()
        if route_path == "/api/ping":
            return self._reply(200, {"ok": True})
        if route_path == "/api/chain":
            return self._answer(lambda: self._chain(body))
        if route_path == "/api/provider/*":
            return self._answer(lambda: self._provider(name, body))
        if route_path == "/api/stt":
            return self._answer(lambda: self._stt(body))
        if route_path == "/api/auth/login":
            return self._answer(lambda: self._login(body))
        if route_path == "/api/voices/*/preview":
            return self._answer(lambda: self._preview(name))
        if route_path == "/api/local/install/start":
            return self._answer(lambda: self._install_start(body))
        if route_path == "/api/local/install/status":
            return self._reply(200, self._install_status())
        # Unreachable: `route()` refused anything `_METHODS_FOR` does not
        # know, and every path in `ROUTES` is answered above.
        return self._reply(500, {"error": "The portal hit an internal error."})

    def _token_ok(self, headers) -> bool:
        offered = _header(headers, TOKEN_HEADER)
        # compare_digest raises TypeError on a str with a non-ASCII
        # character in it. Both secrets are url-safe base64, so a non-ASCII
        # offering cannot be the right one: refusing it here leaks nothing
        # and keeps the compare constant-time for the values that can be.
        return bool(offered) and offered.isascii() and secrets.compare_digest(
            offered, self._token
        )

    # --- the one-time code exchange -----------------------------------

    def _session(self, body: bytes):
        # Under the lock start to finish: ThreadingHTTPServer runs these on
        # concurrent threads, and "single use" and "five attempts" are both
        # check-then-set. `_refuse_code` and `lock_out` take the same
        # re-entrant lock from inside here.
        with self._lock:
            if self._code is None:
                return self._refuse_code("That one-time code has already been used.")
            if time.monotonic() - self._minted > CODE_TTL:
                return self._refuse_code("That one-time code has expired.")
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except (UnicodeDecodeError, ValueError):
                return self._refuse_code("That is not a one-time code.")
            offered = payload.get("code") if isinstance(payload, dict) else None
            if (
                not isinstance(offered, str)
                or not offered.isascii()  # compare_digest raises on non-ASCII
                or not secrets.compare_digest(offered, self._code)
            ):
                return self._refuse_code("That is not this portal's one-time code.")
            self._code = None  # single use, whatever happens next
            return self._reply(200, {"token": self._token})

    def _refuse_code(self, message: str):
        """Every failed exchange counts, including a replay of a used code.

        A used code coming back is indistinguishable from an attacker
        replaying one — the server deliberately keeps nothing to tell them
        apart with. The cost is that reloading the page five times kills the
        portal, which is the designed behaviour and is recoverable by
        running `vocalize portal` again. A cross-origin request never gets
        here: `route()` refuses it before it can be counted.

        Called with `self._lock` held.
        """
        self._code_attempts += 1
        if self._code_attempts >= MAX_CODE_ATTEMPTS:
            self.lock_out()
            return self._reply(403, {"error": LOCKOUT_MESSAGE})
        return self._reply(
            403,
            {"error": message, "attempts_left": MAX_CODE_ATTEMPTS - self._code_attempts},
        )

    # --- read-only state ----------------------------------------------

    def _state(self):
        """Everything the page renders, in one poll.

        Nothing here may block: `readiness()` runs each probe on a daemon
        thread with a timeout and returns a "still checking" row for one
        that hangs, and the key previews go through the same registry. A
        wedged keychain therefore costs one warn row, not a dead page.

        Two batches, each bounded by `probe_timeout` however many probes it
        holds, so a poll against a keychain dialog costs two timeouts and
        not one per provider.
        """
        # Before the parse, never after it. Everything below describes the
        # file as it is right now; a fingerprint taken at the end would
        # describe a file that changed while the probes ran, and the page
        # would then hold a current fingerprint over stale values — its
        # write would pass the compare-and-swap and silently overwrite
        # whoever changed it. Taken here, such a change makes that write
        # refuse instead, which is the safe direction (DEC-005).
        try:
            fingerprint = wizard.fingerprint_config(config.config_path())
        except VocalizeError:
            # Guarded like every other section below: an unreadable path is
            # one missing key on the page, not a dead route. A null here
            # means no write can be made until a poll succeeds.
            fingerprint = None

        config_error = None
        try:
            file_config = config.load_config_file()
        except VocalizeError as exc:
            # A config file the user broke by hand still has to render, or
            # the page that exists to fix it cannot be reached.
            file_config = {}
            config_error = str(exc)

        rows = readiness.readiness(file_config, timeout=self.probe_timeout)
        keys = _key_states(self.probe_timeout)

        try:
            chain = config.resolve_chain(None, file_config)
            chain_source = config.chain_source(None, file_config)
        except VocalizeError as exc:
            chain, chain_source = [], str(exc)

        try:
            stt: dict = config.resolve_stt(file_config)
        except VocalizeError as exc:
            stt = {"error": str(exc)}

        return self._reply(
            200,
            {
                "rows": [row._asdict() for row in rows],
                "chain": chain,
                "chain_source": chain_source,
                "providers": {
                    name: _provider_state(name, file_config, chain, keys[name])
                    for name in auth.PROVIDER_NAMES
                },
                "stt": stt,
                "config_path": str(config.config_path()),
                "config_error": config_error,
                "fingerprint": fingerprint,
            },
        )

    # --- writes -------------------------------------------------------

    def _answer(self, work):
        """Run a write handler, mapping the error families onto responses.

        `ConfigChangedError` has its own status on purpose: 409 is what
        tells the page to reload rather than retry. Everything else in the
        family is the caller's fault and is a 400 carrying the CLI's own
        wording — an unplanned exception is not caught here at all, so it
        still becomes `_respond`'s fixed 500.
        """
        try:
            return work()
        except _Refused as exc:
            return self._reply(exc.status, {"error": exc.message})
        except ConfigChangedError as exc:
            return self._reply(409, {"error": str(exc)})
        except VocalizeError as exc:  # ConfigError included
            return self._reply(400, {"error": str(exc)})

    def _saved(self, data: dict, fingerprint):
        """Write the merged config and hand the page its next fingerprint.

        The fingerprint comes back out of the write itself rather than from
        a fresh read: a writer landing between the two would otherwise have
        its own state handed to the page as "yours".
        """
        _text, written = wizard.write_config_if_unchanged(
            config.config_path(), data, fingerprint
        )
        return self._reply(200, {"ok": True, "fingerprint": written})

    def _chain(self, body: bytes):
        payload = _payload(body)
        fingerprint = _fingerprint_from(payload)
        # The CLI's own validator and nothing in front of it: a non-list, an
        # empty list, a non-string member, an unknown name and a duplicate
        # each come back in the words `vocalize speak` would have used.
        config._validate_chain(payload.get("order"), config.config_path())

        # The merge base is a fresh read, not the page's view, so every key
        # and table the page never saw survives the write.
        data = dict(config.load_config_file())
        data["chain"] = list(payload["order"])
        return self._saved(data, fingerprint)

    def _provider(self, name: str | None, body: bytes):
        _provider_or_404(name)
        payload = _payload(body)
        settings = _settings_from(payload)
        fingerprint = _fingerprint_from(payload)

        data = dict(config.load_config_file())
        tables = {key: dict(table) for key, table in (data.get("providers") or {}).items()}
        table = dict(tables.get(name) or {})
        for key, value in settings.items():
            if key not in config.KNOWN_PROVIDER_KEYS:
                # The CLI only warns on stderr here, which a page cannot
                # see; a write refuses instead, so a typo is never saved.
                raise _Refused(
                    400,
                    f"unknown config key {key!r} in [providers.{name}]. "
                    f"Known: {', '.join(config.KNOWN_PROVIDER_KEYS)}",
                )
            if value is None:
                table.pop(key, None)  # null clears a key, never writes "None"
            else:
                table[key] = _provider_value(name, key, value)

        # An emptied table loses its header, and an emptied `providers`
        # loses the key, so the file does not collect dead sections.
        if table:
            tables[name] = table
        else:
            tables.pop(name, None)
        if tables:
            data["providers"] = tables
        else:
            data.pop("providers", None)

        config._validate_providers_table(tables, config.config_path())
        return self._saved(data, fingerprint)

    def _stt(self, body: bytes):
        payload = _payload(body)
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
        # pairing rule is a check on the pair, and half of it may already be
        # in the file.
        config._validate_stt_table(table, config.config_path())
        if table:
            data["stt"] = table
        else:
            data.pop("stt", None)
        return self._saved(data, fingerprint)

    def _login(self, body: bytes):
        """Store an API key. The key never leaves this method.

        Not in the response, not in a header, not in a log — `log_message`
        writes nothing, so no request line and therefore no query string is
        ever recorded. Every error message is scrubbed of the key before it
        goes back, because the messages we did not write quote what they
        were given: h11's `LocalProtocolError` echoes a whole header value
        and `list_voices` wraps it verbatim. This route writes no config
        and so takes no fingerprint.
        """
        payload = _payload(body)
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
            # `from None` drops __cause__ — the chain is exactly where a
            # leaked key would otherwise still be sitting.
            raise _Refused(400, auth.scrub(str(exc), key)) from None
        return self._reply(200, {"ok": True, "message": auth.scrub(message, key)})

    # --- preview ------------------------------------------------------

    def _preview(self, name: str | None):
        """Speak the fixed sentence through one provider, return the bytes.

        Through `chain.run` with the provider forced, exactly as `vocalize
        speak --provider` does, so the budget gate, the ledger and the
        audio cache all apply — a capped provider is refused here in the
        same words as in a terminal, and a repeat click is a cache hit.
        `run` never plays: it returns the audio, and the browser plays it
        from a Blob. That is the one sound in this project that does not
        take the machine-wide playback lock (design § Playback).
        """
        from . import chain as chain_module
        from . import providers as providers_module

        _provider_or_404(name)
        # A fresh read, never a snapshot: `/api/state` re-reads the file on
        # every poll, and a preview off a startup copy would speak in a
        # voice the page is not showing. A file that does not parse raises,
        # and `_answer` makes that a 400 — better than a preview in default
        # settings the user never configured.
        file_config = config.load_config_file()

        if not _preview_lock.acquire(timeout=PREVIEW_WAIT):
            raise _Refused(503, "A preview is already running. Try again in a moment.")
        try:
            try:
                # The gate `chain.run` runs anyway, called first so a
                # refusal can be told apart from a provider that broke:
                # same call and same words, but a 402 rather than one line
                # of a multi-provider failure report.
                chain_module._budget_gate(
                    name, providers_module.get(name), PREVIEW_TEXT, file_config
                )
            except ProviderError as exc:
                return self._reply(402, {"error": str(exc)})
            try:
                audio, _spoken, ext = chain_module.run(
                    PREVIEW_TEXT,
                    chain=[name],
                    file_config=file_config,
                    cache_dir=CACHE_DIR,
                    forced=True,
                )
            except VocalizeError as exc:
                return self._reply(502, {"error": _one_line(exc)})
        finally:
            _preview_lock.release()

        return self._reply(
            200,
            body=audio,
            content_type=PREVIEW_TYPES.get(ext, "application/octet-stream"),
            # No range requests: one short blob, fetched once with a header
            # on it. A partial-content path is only a way for the server to
            # be asked the same question twice.
            extra={"Accept-Ranges": "none"},
        )

    # --- the local install thread -------------------------------------

    def _install_status(self) -> dict:
        """A snapshot of the progress dict, taken under the worker's lock."""
        with self._lock:
            return dict(self._install)

    def _progress(self, **fields) -> None:
        """The only way the worker thread writes progress."""
        with self._lock:
            self._install.update(fields)

    def _claim_install(self, target: str) -> bool:
        """Claim the one install slot. False when one is already running.

        Under the lock with the liveness check, so two clicks arriving on
        two handler threads cannot both start a download into the same
        model directory. The dict is built complete and assigned once: a
        `/status` poll landing between two statements would otherwise see
        `idle` right after a 200 from `/start`, which is indistinguishable
        from nothing having been started at all.
        """
        with self._lock:
            if self._install["running"]:
                return False
            if self._install_thread is not None and self._install_thread.is_alive():
                return False
            self._install = _idle_install() | {
                "running": True,
                "target": target,
                "step": "starting",
            }
            return True

    def _install_start(self, body: bytes):
        payload = _payload(body)
        target = payload.get("target")
        if target not in INSTALL_TARGETS:
            raise _Refused(
                400, f"Expected 'target' to be one of: {', '.join(INSTALL_TARGETS)}"
            )
        model = payload.get("model")
        if target == "stt":
            model = _install_model(model)
        elif model is not None:
            # The refusal `vocalize local install` makes for `--model`
            # without `--stt`: Kokoro has one model and does not take a name.
            raise _Refused(400, "A model name only applies to the 'stt' target.")

        if not self._claim_install(target):
            raise _Refused(409, "An install is already running.")

        thread = threading.Thread(
            target=self._install_worker,
            args=(target, model),
            daemon=True,  # a non-daemon thread would outlive `vocalize portal`
            name="vocalize-portal-install",
        )
        with self._lock:
            self._install_thread = thread
        try:
            thread.start()
        except RuntimeError:
            # The OS refused a thread. Without this the slot stays claimed
            # for the life of the process, every later install answers 409,
            # and the page can never install anything again. The watchdog
            # needs no unwinding: the suspension lives inside the worker,
            # which never ran.
            with self._lock:
                self._install = _idle_install()
                self._install_thread = None
            raise _Refused(503, "Could not start the install.") from None
        return self._reply(200, self._install_status())

    def _install_worker(self, target: str, model: str | None) -> None:
        # The page legitimately goes quiet for the minutes a 488 MB model
        # takes. `suspend_idle` holds the watchdog off and resets the idle
        # clock on the way out, so the first tick after it resumes cannot
        # close the portal on a clock that stopped when the install began.
        # Inside the worker rather than in `_claim_install`, so a scope is
        # released rather than a flag cleared: the one that got away from
        # the reference was a flag set in one place and cleared in another.
        with self.suspend_idle():
            try:
                if target == "kokoro":
                    self._install_kokoro()
                else:
                    self._install_stt(model)
                self._progress(step="installed")
            except Exception as exc:  # noqa: BLE001 — a thread's traceback goes nowhere
                # One line and never a traceback: this reaches a web page,
                # and an installer's exception text names paths and URLs.
                # Load-bearing, not padding — `_respond`'s catch-all covers
                # the request thread only, so an escape here would land on
                # the user's terminal as the traceback `_Server.handle_error`
                # exists to keep off it.
                self._progress(error=_one_line(exc), step="failed")
            finally:
                # Cleared here rather than after each download: an install
                # that ends any way at all has no part file left for
                # `discard_partial_download` to take, and a stale path
                # would have it delete the next install's.
                self._downloading = None
                self._progress(running=False, done=True)

    def _downloaded(self, already: int):
        """`download_file`'s progress callback, offset by what is already in."""

        def report(done: int, _total: int) -> None:
            self._progress(downloaded=already + done)

        return report

    def _install_kokoro(self) -> None:
        from .local import install as install_module
        from .local import kokoro_manifest as manifest

        uv = _uv_or_raise(install_module)
        self._progress(total=sum(entry["size"] for entry in manifest.FILES))
        done = 0
        for entry in manifest.FILES:
            self._progress(step=f"downloading {entry['name']}")
            # A half-finished install already has one good file on disk; a
            # re-run should not spend 326 MB re-fetching it.
            if not install_module.file_is_verified(entry):
                self._downloading = manifest.MODEL_DIR / (entry["name"] + ".part")
                install_module.download_file(
                    entry["url"],
                    manifest.MODEL_DIR / entry["name"],
                    entry["size"],
                    entry["sha256"],
                    # The module's own default, passed by name rather than
                    # left to the default argument `download_file` bound at
                    # def time — which is the only way a test can fake the
                    # network and still exercise the size and sha256 checks.
                    opener=install_module._default_opener,
                    progress=self._downloaded(done),
                )
            done += entry["size"]
            self._progress(downloaded=done)

        install_module.write_stamp()
        self._progress(step="warming the runtime")
        install_module.selftest(uv)

    def _install_stt(self, model: str) -> None:
        from .local import install as install_module
        from .local import whisper_manifest as manifest

        uv = _uv_or_raise(install_module)
        entry = manifest.file_for(model)
        self._progress(total=entry["size"], step=f"downloading {entry['name']}")
        if not install_module.file_is_verified(entry, manifest=manifest):
            self._downloading = manifest.MODEL_DIR / (entry["name"] + ".part")
            install_module.download_file(
                entry["url"],
                manifest.MODEL_DIR / entry["name"],
                entry["size"],
                entry["sha256"],
                opener=install_module._default_opener,
                progress=self._downloaded(0),
            )
        self._progress(downloaded=entry["size"])

        install_module.write_stamp(manifest=manifest, files=[entry])
        self._progress(step="warming the runtime")
        install_module.selftest(uv, manifest=manifest, model=model)
        self._progress(step="building the recorder")
        status, _bundle = install_module.build_recorder()
        if status == "rebuilt":
            # The CLI prints this. A portal install that swallowed it would
            # invalidate the user's microphone grant and say nothing, and
            # the next dictation would simply fail.
            self._progress(note=install_module.REGRANT_WARNING)


class _Handler(BaseHTTPRequestHandler):
    """Wire in, `route()`, wire out. No decisions of its own."""

    server_version = "vocalize"
    sys_version = ""

    #: Seconds any one socket read or write may stall before the connection
    #: is dropped. `ThreadingHTTPServer` gives every connection a thread and
    #: `BaseHTTPRequestHandler.timeout` is `None`, so a peer that connects and
    #: then sends nothing holds a thread for the life of the process: 4500 of
    #: them and the owner's own request cannot be served ("can't start new
    #: thread"). `StreamRequestHandler.setup` puts this on the socket, and
    #: `handle_one_request` turns the resulting `TimeoutError` into a closed
    #: connection. Ten seconds cannot cut a real request short: every client
    #: is a browser on loopback, the body is capped at 64 KiB, and
    #: `protocol_version` stays HTTP/1.0 so nothing is kept alive between
    #: requests — the exchange is milliseconds, not seconds.
    timeout = 10.0

    def __getattr__(self, name: str):
        """Every method reaches `route()`, not just GET and POST.

        BaseHTTPRequestHandler dispatches on `do_<METHOD>` and answers 501
        itself when that attribute is missing — with no `Host` check and
        none of the security headers. Handing back `_respond` for any
        `do_*` keeps `route()` the single decision point: HEAD, OPTIONS,
        PUT and anything else get its 405, behind its `Host` pin. (A
        preflight therefore fails, which is the intended answer: the
        portal has no cross-origin callers.)
        """
        if name.startswith("do_"):
            return self._respond
        raise AttributeError(name)

    def _respond(self) -> None:
        portal: Portal = self.server.portal  # type: ignore[attr-defined]
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = MAX_BODY + 1  # unreadable length: refuse it as oversized
        if length < 0 or length > MAX_BODY:
            # A negative length is refused with the oversized ones rather than
            # read as an empty body: `Content-Length: -5` on `/api/session`
            # otherwise reaches `route()` as a bodyless request and burns one
            # of the five code attempts. Refuse before reading: the point of
            # the cap is not to hold an arbitrary number of bytes in memory in
            # the first place. It is still `route()` that answers, so the
            # `Host` pin comes first here as it does everywhere else.
            body = _OVERSIZED
            self.close_connection = True  # the unread body is not a request
        else:
            body = self.rfile.read(length) if length > 0 else b""
        try:
            answer = portal.route(self.command, self.path, self.headers, body)
        except Exception:  # noqa: BLE001 — a bug is a 500, not a dead socket
            # Without this the connection closes with no status line, no
            # security headers and a traceback on the user's terminal, and
            # the page cannot tell that from the portal having exited. The
            # message is fixed rather than `str(exc)` for the reason
            # `readiness._start_probe` gives: an unplanned exception's text is
            # untrusted and may embed credential-shaped material.
            answer = portal._reply(500, {"error": "The portal hit an internal error."})
        self._write(*answer)

    def send_error(self, code, message=None, explain=None) -> None:
        """http.server's own errors carry the headers too.

        A malformed request line or an unsupported HTTP version never
        reaches `route()` — the request was never parsed. It still may not
        be answered with a bare HTML page and no CSP. The stdlib's
        `message` is not echoed: it interpolates the request line.
        """
        self.close_connection = True
        # A request line that never parsed carries no version at all, and on
        # CPython below 3.14 `parse_request` leaves `request_version` at its
        # HTTP/0.9 default when it bails out — for which http.server writes no
        # status line and no headers, and `_write` now declines to answer at
        # all. Claiming 1.1 here is what keeps a malformed request line's 400
        # carrying the security headers on 3.10 through 3.13. Dead on 3.14,
        # which sets `request_version = ''` on those paths itself; the floor
        # in pyproject.toml is 3.10, so the line stays.
        self.request_version = "HTTP/1.1"
        portal: Portal = self.server.portal  # type: ignore[attr-defined]
        self._write(
            *portal._reply(int(code), {"error": "That request could not be handled."})
        )

    def _write(self, status: int, headers: dict[str, str], body: bytes) -> None:
        """The one place bytes go on the wire."""
        if self.request_version == "HTTP/0.9":
            # `GET /\r\n\r\n` parses as HTTP/0.9, and 0.9 has no status line
            # and no headers to put a CSP in — http.server drops every header
            # silently and writes the body alone. So that shape gets no answer
            # at all rather than the one response on this server without its
            # security headers. No browser has spoken 0.9 in thirty years.
            self.close_connection = True
            return
        try:
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except OSError:
            # The peer hung up before the answer went out. `_respond`'s
            # `except` turns a bug into a 500 but ends here, outside it, so a
            # `BrokenPipeError` or `ConnectionResetError` on these writes used
            # to land on the owner's terminal as the traceback that guard
            # exists to remove. A gone peer is not a 500 and not a bug: there
            # is nobody left to answer and nothing to report. Anything that is
            # not an `OSError` is a real bug and still raises.
            self.close_connection = True

    def log_message(self, fmt: str, *args: object) -> None:
        """Never log a request line: it contains the path, and a path can
        contain a query string someone put a secret in."""


class _Server(ThreadingHTTPServer):
    """`ThreadingHTTPServer`, minus the tracebacks a flood prints."""

    def handle_error(self, request: object, client_address: object) -> None:
        """A peer that went away is not worth a traceback.

        socketserver's own `handle_error` prints four lines and a stack for
        every connection that dies in `handle()`. The ten-second `timeout`
        keeps a connection flood from exhausting threads, but each dropped
        socket still filled the owner's terminal with a traceback for
        something the portal handled correctly. An `OSError` here is the
        peer's end failing — reset, broken pipe, a stalled read hitting the
        timeout — and is silent. Anything else is a bug in this code and
        still gets its traceback.
        """
        if not isinstance(sys.exc_info()[1], OSError):
            super().handle_error(request, client_address)
