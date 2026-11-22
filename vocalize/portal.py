"""The config portal's HTTP server: auth bootstrap, settings and actions.

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

The page renders itself from `GET /api/schema` — the settings registry's
groups and settings, and the actions — and reads current values from
`GET /api/state`. `POST /api/settings` is the one write, and it is
compare-and-swap (DEC-005): `/api/state` hands the page the config file's
fingerprint, taken *before* the values that go with it are parsed, every
write hands that fingerprint back, and a file that moved in between is
refused with 409 rather than clobbered. On the wire the fingerprint's
`mtime_ns` is a decimal string, never a number: it has 19 digits and
JavaScript rounds past 16 (`_wire`). Every change goes through
`settings_registry.validate`, the rules the loader itself uses, and a
request with any invalid or unknown key writes nothing.

`POST /api/action/<id>` runs one of `vocalize.actions`: an "instant"
action inline, a "job" on a daemon thread inside `suspend_idle()` that
reports through `GET /api/action/status`. One job per action at a time.
An `ActionError` is the action's own planned message; any other
exception's text is untrusted and never reaches the page.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import math
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

from . import app, auth, config, readiness, settings_registry, wizard
from .exceptions import (
    ConfigChangedError,
    VocalizeError,
)
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

#: `style-src` is spelled out because `default-src 'self'` does not cover an
#: inline `<style>` element — "self" is a source for *fetched* stylesheets,
#: and an inline one needs `'unsafe-inline'`, a nonce or a hash. The portal
#: serves exactly two asset files and has no route for a third, and the page
#: is served verbatim so a per-response nonce would mean templating it. What
#: the relaxation buys an attacker here is nothing: script is still 'self'
#: with no inline script on the page, the page builds every node with
#: `textContent` so there is no markup injection point to write CSS from,
#: and `default-src 'self'` still refuses the outbound request a CSS
#: exfiltration channel would need.
CSP = (
    "default-src 'self'; style-src 'self' 'unsafe-inline'; "
    "media-src 'self' blob:; frame-ancestors 'none'"
)

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

#: The same audio cache `vocalize speak` uses. Kept as a module name so a
#: test can point it elsewhere (`conftest._no_real_audio_cache`).
CACHE_DIR = DEFAULT_CACHE_DIR

#: The one line a job or an instant action answers with when it raised
#: something other than `ActionError`. Fixed, never the exception's text:
#: an unplanned exception's message is untrusted (`readiness._start_probe`).
ACTION_FAILED = "That action failed. Run the matching vocalize command for details."

#: Longest message — a job's progress line, an action's reply, a settings
#: error — that reaches the page. One line, bounded.
MESSAGE_MAX = 300

#: Finished jobs kept for `GET /api/action/status`. Running jobs are never
#: dropped; the oldest finished ones go first.
MAX_JOBS = 50

#: Longest action parameter value accepted.
PARAM_MAX = 200


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

# Route paths carrying an action id, normalised to a fixed key before
# dispatch. The pattern is the validation: an id that is not a short word
# never reaches a handler, and `/api/action/status` is its own route.
_PARAMETERIZED = (
    (re.compile(r"^/api/action/(?!status$)([a-z0-9][a-z0-9_.-]{0,63})$"), "/api/action/*"),
)


def _normalize(path: str) -> tuple[str, str | None]:
    """`/api/action/clear_audio_cache` -> `("/api/action/*", "clear_audio_cache")`.

    A path matching no pattern comes back unchanged with no name, so an
    action id is the only route parameter that exists and it is
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
    ("GET", "/api/schema", "token"),
    ("POST", "/api/settings", "token"),
    ("POST", "/api/action/clear_audio_cache", "token"),
    ("GET", "/api/action/status", "token"),
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


def _fault(exc: BaseException) -> str:
    """The message for a failure we planned for, the type for one we did not.

    An unplanned exception's message is untrusted text — the same reason
    `readiness._start_probe` reports the type and never `str(exc)`.
    """
    return str(exc) if isinstance(exc, (VocalizeError, OSError)) else type(exc).__name__


def _add_error(entry: dict, exc: BaseException) -> None:
    """Record a local settings error without dropping an earlier one."""
    fault = _fault(exc)
    entry["error"] = f"{entry['error']}; {fault}" if entry["error"] else fault


#: The environment variables that win over a setting for commands run from
#: a shell where they are set (config.py). Voice and speed belong to the
#: first engine of the chain, so their key is named when the state is read.
_ENV_OVERRIDES = (
    ("VOCALIZE_CHAIN", "chain"),
    ("VOCALIZE_VOICE", "voice"),
    ("VOCALIZE_SPEED", "speed"),
    ("VOCALIZE_OVERFLOW", "overflow"),
    ("VOCALIZE_MAX_CHARS", "max_chars"),
)


def _overrides(chain: list[str]) -> dict:
    """Setting key to the name of the variable set in this server's own
    environment that overrides it. Names only: a value never leaves here."""
    out: dict[str, str] = {}
    for variable, key in _ENV_OVERRIDES:
        if not (os.environ.get(variable) or "").strip():
            continue
        if key in ("voice", "speed"):
            if not chain:
                continue
            key = f"providers.{chain[0]}.{key}"
        try:
            settings_registry.by_key(key)
        except KeyError:
            continue
        out[key] = variable
    return out


def _provider_state(name: str, file_config: dict, chain: list[str]) -> dict:
    """Resolve one local engine independently, so invalid settings do not hide others."""
    entry: dict = {
        "label": auth.PROVIDER_LABELS.get(name, name),
        "in_chain": name in chain,
        "settings": None,
        "error": None,
    }
    try:
        # primary=False: the file's values and the engine's defaults, never
        # VOCALIZE_VOICE or VOCALIZE_SPEED — a variable's value stays in the
        # server, and `overrides` names it instead.
        settings = config.resolve_provider_settings(name, file_config, primary=False)
    except Exception as exc:  # noqa: BLE001 — one provider, not the page
        _add_error(entry, exc)
    else:
        entry["settings"] = {
            "voice": settings.voice_id,
            "speed": settings.speed,
            "language": settings.language,
        }
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


#: `mtime_ns` as it crosses the wire: ASCII digits only, and no more of them
#: than a signed 64-bit nanosecond count has (19). Bounded so `int()` can
#: never see the 64 KiB of digits the body cap would otherwise let through.
_WIRE_MTIME = re.compile(r"[0-9]{1,20}")


def _wire(fingerprint):
    """A fingerprint as the page receives it: `mtime_ns` as a decimal string.

    A nanosecond timestamp has 19 digits and JavaScript's one number type
    is exact to 2**53 — 16. Sent as an integer, `JSON.parse` rounds it,
    `JSON.stringify` sends the rounded value back, and the compare-and-swap
    refuses every write the page ever makes. The page never looks inside a
    fingerprint, it hands it back verbatim, so the string costs it nothing.
    `"absent"` and `None` pass through unchanged; `wizard`'s CLI callers
    never see this and keep their dict of ints.
    """
    if isinstance(fingerprint, dict):
        return {"mtime_ns": str(fingerprint["mtime_ns"]), "sha256": fingerprint["sha256"]}
    return fingerprint


def _fingerprint_from(payload: dict):
    """The fingerprint the page is handing back, shape-checked.

    Untrusted, and it decides whether a write lands. Accepted only in the
    two shapes `_wire` sends — `"absent"`, or the dict with `mtime_ns` as
    a digit string — and rebuilt rather than passed through, so no extra
    key can ride along and no comparison can succeed for the wrong reason.
    A number in `mtime_ns` is refused with the rest: a page that parsed the
    value into a double sends one, and it cannot be told from a hand-built
    request that happens to be exact.
    """
    value = payload.get("fingerprint")
    if value == wizard.ABSENT_CONFIG:
        return wizard.ABSENT_CONFIG
    if (
        isinstance(value, dict)
        and set(value) == {"mtime_ns", "sha256"}
        and isinstance(value["mtime_ns"], str)
        and _WIRE_MTIME.fullmatch(value["mtime_ns"])
        and isinstance(value["sha256"], str)
    ):
        return {"mtime_ns": int(value["mtime_ns"]), "sha256": value["sha256"]}
    raise _Refused(400, "Expected the 'fingerprint' this page was given by /api/state.")


def _line(text, limit: int = MESSAGE_MAX) -> str:
    """One short line of planned text for the page: first non-blank line, capped."""
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    return lines[0][:limit] if lines else ""


def _actions_module():
    """`vocalize.actions`, imported when first needed.

    Through `importlib` rather than `from . import actions`: a package
    attribute would win over `sys.modules`, and the tests replace the
    module there. Lazy so this server imports while that module is absent.
    """
    return importlib.import_module(f"{__package__}.actions")


def _profiles_module():
    """`vocalize.profiles`, imported when first needed (see `_actions_module`)."""
    return importlib.import_module(f"{__package__}.profiles")


def _raw_config() -> dict:
    """The config file as TOML, before the loader migrates or validates it.

    Only for the retired-settings notices: `load_config_file` has already
    removed the keys they are about. Unreadable or absent is `{}`.
    """
    try:
        import tomllib
    except ImportError:  # Python 3.10; tomllib is stdlib from 3.11
        import tomli as tomllib  # type: ignore

    path = config.config_path()
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, ValueError):
        return {}


def _notices() -> list[str]:
    """What the file's retired settings would print, as a list for the page."""
    try:
        return list(config.retired_notices(_raw_config()))
    except VocalizeError:
        return []


def _values(file_config: dict) -> dict:
    """Every setting's value from `file_config`, or the defaults if it will not resolve."""
    try:
        return settings_registry.values(file_config)
    except VocalizeError:
        return settings_registry.defaults()


def _changes_from(payload: dict) -> dict:
    changes = payload.get("changes")
    if not isinstance(changes, dict) or not all(isinstance(key, str) for key in changes):
        raise _Refused(400, "Expected a 'changes' object of setting keys.")
    if not changes:
        raise _Refused(400, "Expected at least one change.")
    return changes


def _restarts(changes: dict) -> list[str]:
    """The `restart` fields of the changed settings, each once, in order."""
    out: list[str] = []
    for key in changes:
        restart = settings_registry.by_key(key).restart
        if restart and restart not in out:
            out.append(restart)
    return out


def _find_action(actions, action_id: str | None):
    for action in actions.ACTIONS:
        if action.id == action_id:
            return action
    raise _Refused(400, "Unknown action.")


def _source_values(actions, name: str, cached: dict | None) -> set[str]:
    listed = (cached or {}).get(name)
    if listed is None:
        listed = actions.sources().get(name) or []
    return {entry["value"] for entry in listed if isinstance(entry, dict)}


def _params_for(action, payload: dict, actions, cached_sources: dict | None) -> dict:
    """The request's `params`, checked against the action's declaration.

    Unknown names, a missing required one, a non-string, an overlong or
    unprintable value and a value outside the declared choices are each a
    400 before anything runs. No submitted value is echoed back.
    """
    params = payload.get("params", {})
    if not isinstance(params, dict) or not all(isinstance(key, str) for key in params):
        raise _Refused(400, "Expected a 'params' object.")
    declared = {param.name: param for param in action.params}
    if any(name not in declared for name in params):
        known = ", ".join(declared) or "none"
        raise _Refused(400, f"Unknown parameter for this action. Known: {known}")
    out = {}
    for name, param in declared.items():
        value = params.get(name)
        if value is None:
            if param.required:
                raise _Refused(400, f"The {param.label!s} parameter is required.")
            continue
        if (
            not isinstance(value, str)
            or len(value) > PARAM_MAX
            or any(not character.isprintable() for character in value)
        ):
            raise _Refused(400, f"Invalid {param.label!s}: expected a short line of text.")
        if param.choices is not None and value not in param.choices:
            raise _Refused(400, f"Invalid {param.label!s}: not one of its choices.")
        if param.choices_from is not None:
            allowed = _source_values(actions, param.choices_from, cached_sources)
            if value not in allowed and cached_sources is not None:
                # The cached list may predate an install; ask once more.
                allowed = _source_values(actions, param.choices_from, None)
            if value not in allowed:
                raise _Refused(400, f"Invalid {param.label!s}: not one of its choices.")
        out[name] = value
    return out


def _fraction(value) -> float | None:
    """A progress fraction clamped to 0..1, or None when there is none."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if math.isnan(value):
        return None
    return min(1.0, max(0.0, float(value)))


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
        # Action jobs by id, written by their worker threads and read by
        # `/api/action/status` — both under `_lock`, so a poll never sees a
        # half-updated record and two clicks cannot both claim one action.
        self._jobs: dict[str, dict] = {}
        self._job_count = 0
        # The last `actions.sources()` that finished, refreshed off the
        # request thread on every state poll: a voice or device listing can
        # take seconds, and a poll should not.
        self._sources: dict | None = None

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
        """Say which action jobs the closing portal is cutting short.

        The portal closes on Ctrl-C, on the idle watchdog and on a lockout,
        and a job may be minutes into a download on a daemon thread that is
        about to be killed without unwinding. Cleaning up what that job
        left is the action's own business (`vocalize.actions`); this only
        names what was cut, so the closing message is not silent about it.

        Returns the line the caller should print, or None when no job was
        running — which is the answer nearly every time.
        """
        with self._lock:
            running = [job["action"] for job in self._jobs.values() if job["state"] == "running"]
        if not running:
            return None
        return f"Cut short: {', '.join(running)}. Run it again to finish."


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
        if route_path == "/api/schema":
            return self._schema()
        if route_path == "/api/settings":
            return self._answer(lambda: self._settings(body))
        if route_path == "/api/action/*":
            return self._answer(lambda: self._action(name, body))
        if route_path == "/api/action/status":
            return self._reply(200, {"jobs": self._job_list()})
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
        """Read current settings, readiness, sources and actions with bounded probes."""
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

        try:
            chain = config.resolve_chain(None, file_config)
            chain_source = config.chain_source(None, file_config)
        except VocalizeError as exc:
            chain, chain_source = [], str(exc)

        try:
            stt: dict = config.resolve_stt(file_config)
        except VocalizeError as exc:
            stt = {"error": str(exc)}

        # Defaults when the file's values will not resolve; `config_error`
        # and the per-engine `error` already say why.
        values = _values(file_config)
        sources = self._current_sources()

        return self._reply(
            200,
            {
                "rows": [row._asdict() for row in rows],
                "chain": chain,
                "chain_source": chain_source,
                "providers": {
                    name: _provider_state(name, file_config, chain)
                    for name in auth.PROVIDER_NAMES
                },
                "stt": stt,
                "app": app.status_dict(),
                "config_path": str(config.config_path()),
                "config_error": config_error,
                "fingerprint": _wire(fingerprint),
                "values": values,
                "notices": _notices(),
                "sources": sources,
                "source_notes": self._source_notes(sources),
                "actions": self._action_state(),
                "profile": self._profile(),
                "overrides": _overrides(chain),
            },
        )

    def _current_sources(self) -> dict:
        """`actions.sources()`, refreshed in a bounded probe, last good result kept.

        One listing thread at a time (the readiness registry's in-flight
        rule), so a poll against a slow listing starts nothing new and
        answers with what it had.
        """

        def probe() -> readiness.Row:
            listed = _actions_module().sources()
            with self._lock:
                self._sources = listed
            return readiness.Row("portal sources", "ok", "", "")

        readiness.run_probes([("portal sources", probe)], self.probe_timeout)
        with self._lock:
            return dict(self._sources or {})

    def _source_notes(self, sources: dict) -> dict:
        """`actions.source_notes()`: why a source is empty and what to do,
        source name to one line. `{}` when the module has none or fails."""
        try:
            notes = _actions_module().source_notes(sources)
        except Exception:  # noqa: BLE001 — one section, not the page
            return {}
        if not isinstance(notes, dict):
            return {}
        return {str(name): _line(text) for name, text in notes.items() if _line(text)}

    def _action_state(self) -> dict:
        try:
            return _actions_module().state()
        except Exception:  # noqa: BLE001 — one section, not the page
            return {}

    def _profile(self) -> dict:
        try:
            profiles = _profiles_module()
            return {"detected": profiles.detect(), "names": list(profiles.names())}
        except Exception:  # noqa: BLE001 — one section, not the page
            return {"detected": "", "names": []}

    def _schema(self):
        """The registry's groups and settings, and the actions, for the page to render."""
        schema = settings_registry.schema()
        return self._reply(
            200,
            {
                "groups": schema["groups"],
                "settings": schema["settings"],
                "actions": _actions_module().schema(),
            },
        )

    # --- writes -------------------------------------------------------

    def _answer(self, work):
        """Run a write handler, mapping the error families onto responses.

        Everything planned is a 400 carrying the CLI's own wording — an
        unplanned exception is not caught here at all, so it still becomes
        `_respond`'s fixed 500.
        """
        try:
            return work()
        except _Refused as exc:
            return self._reply(exc.status, {"error": exc.message})
        except VocalizeError as exc:  # ConfigError included
            return self._reply(400, {"error": _line(exc)})

    def _changed(self):
        """The 409 a stale fingerprint gets: the file's own fingerprint and values."""
        try:
            fingerprint = wizard.fingerprint_config(config.config_path())
        except VocalizeError:
            fingerprint = None
        try:
            values = _values(config.load_config_file())
        except VocalizeError:
            values = settings_registry.defaults()
        return self._reply(
            409, {"error": "changed", "fingerprint": _wire(fingerprint), "values": values}
        )

    def _settings(self, body: bytes):
        """`POST /api/settings`: validate every change, merge into a fresh read, save.

        The merge base is the file as it is now, never the page's view, so
        every key the request does not name survives the write. Any invalid
        or unknown key and nothing is written; every bad key is reported,
        not just the first.
        """
        payload = _payload(body)
        changes = _changes_from(payload)
        fingerprint = _fingerprint_from(payload)

        path = config.config_path()
        # Before the parse, as in `_state`.
        if wizard.fingerprint_config(path) != fingerprint:
            return self._changed()
        data = config.load_config_file()

        errors: dict[str, str] = {}
        for key, value in changes.items():
            try:
                settings_registry.by_key(key)
            except KeyError:
                errors[key] = "Unknown setting."
                continue
            if value is None:
                continue
            try:
                settings_registry.validate(key, value)
            except VocalizeError as exc:
                errors[key] = _line(exc)
        if errors:
            return self._reply(400, {"errors": errors})

        try:
            merged = settings_registry.apply_changes(data, changes)
        except VocalizeError as exc:
            # A rule between keys (two equal chords, an English-only model
            # with another language): every changed key is part of it.
            message = _line(exc)
            return self._reply(400, {"errors": {key: message for key in changes}})

        try:
            result = wizard.write_config_if_unchanged(path, merged, fingerprint)
        except ConfigChangedError:
            return self._changed()
        _text, written = result
        # The comment-keeping `config.toml.bak` this save made, if it made one.
        backup = getattr(result, "backup", None)
        return self._reply(
            200,
            {
                "values": _values(merged),
                "fingerprint": _wire(written),
                "notices": _notices(),
                "restart": _restarts(changes),
                "backup": str(backup) if backup else None,
                # The copy still holds settings this save removed (another
                # version's service key among them, perhaps).
                "backup_has_retired": bool(backup)
                and getattr(result, "backup_has_retired", False) is True,
            },
        )

    # --- actions ------------------------------------------------------

    def _action(self, action_id: str | None, body: bytes):
        """`POST /api/action/<id>`: run an instant action, or start a job."""
        actions = _actions_module()
        action = _find_action(actions, action_id)
        payload = _payload(body)
        with self._lock:
            cached = self._sources
        params = _params_for(action, payload, actions, cached)

        if action.kind != "job":
            try:
                message = actions.run(action.id, params, lambda *_args: None)
            except actions.ActionError as exc:
                return self._reply(400, {"error": _line(exc)})
            except Exception:  # noqa: BLE001 — never the exception's text
                return self._reply(400, {"error": ACTION_FAILED})
            return self._reply(200, {"ok": True, "job": None, "message": _line(message or "")})

        job_id = self._claim_job(action.id)
        if job_id is None:
            return self._reply(409, {"error": "busy"})
        thread = threading.Thread(
            target=self._job_worker,
            args=(job_id, actions, action.id, params),
            daemon=True,  # a non-daemon thread would outlive `vocalize portal`
            name=f"vocalize-portal-{action.id}",
        )
        try:
            thread.start()
        except RuntimeError:
            # The OS refused a thread. Without this the action stays
            # "running" for the life of the process and every retry is 409.
            with self._lock:
                self._jobs.pop(job_id, None)
            raise _Refused(503, "Could not start that action.") from None
        return self._reply(200, {"ok": True, "job": job_id, "message": "Started."})

    def _claim_job(self, action_id: str) -> str | None:
        """A new running job record for `action_id`, or None if one is running.

        Check and claim under one lock, so two clicks on two handler
        threads cannot both start the same download.
        """
        with self._lock:
            if any(
                job["action"] == action_id and job["state"] == "running"
                for job in self._jobs.values()
            ):
                return None
            self._job_count += 1
            job_id = f"job-{self._job_count}"
            self._jobs[job_id] = {
                "id": job_id,
                "action": action_id,
                "state": "running",
                "progress": None,
                "message": "Starting.",
            }
            finished = [key for key, job in self._jobs.items() if job["state"] != "running"]
            for key in finished[: max(0, len(self._jobs) - MAX_JOBS)]:
                del self._jobs[key]
            return job_id

    def _update_job(self, job_id: str, **fields) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.update(fields)

    def _job_worker(self, job_id: str, actions, action_id: str, params: dict) -> None:
        """Run one job to its end; whatever happens, its record says how it ended.

        Inside `suspend_idle()`: a download with no request in between is
        a page waiting, not an idle one. The catch-all is load-bearing —
        `_respond`'s covers the request thread only, so an escape here would
        land on the owner's terminal as a traceback.
        """

        def progress(fraction=None, message="") -> None:
            fields: dict = {"progress": _fraction(fraction)}
            if message:
                fields["message"] = _line(message)
            self._update_job(job_id, **fields)

        with self.suspend_idle():
            try:
                message = actions.run(action_id, params, progress)
            except actions.ActionError as exc:
                self._update_job(job_id, state="failed", message=_line(exc) or ACTION_FAILED)
            except Exception:  # noqa: BLE001 — a thread's traceback goes nowhere
                self._update_job(job_id, state="failed", message=ACTION_FAILED)
            else:
                self._update_job(
                    job_id, state="done", progress=1.0, message=_line(message or "") or "Done."
                )

    def _job_list(self) -> list[dict]:
        """A snapshot of every job record, oldest first, taken under the lock."""
        with self._lock:
            return [dict(job) for job in self._jobs.values()]


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
