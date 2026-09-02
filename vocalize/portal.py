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

What this module deliberately does not do yet: writes, previews, installs
and the `vocalize portal` command are run 8 (DEC-005), and the real page
is run 9 — `assets/portal.html` here is a placeholder that does the code
exchange and prints the state.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import auth, config, ledger, readiness
from .exceptions import VocalizeError

# Loopback only, always. The portal exposes the machine's provider
# settings and can (from run 8) write the config file: it must never be
# reachable from another host, so this is a constant and not an option.
BIND_HOST = "127.0.0.1"

MAX_BODY_BYTES = 64 * 1024
CODE_TTL_SECONDS = 60.0
MAX_CODE_ATTEMPTS = 5
PING_INTERVAL_SECONDS = 15.0
MISSED_PINGS_BEFORE_SHUTDOWN = 4
DEFAULT_IDLE_TIMEOUT = PING_INTERVAL_SECONDS * MISSED_PINGS_BEFORE_SHUTDOWN

LOCKOUT_REASON = (
    "the portal shut down after five wrong codes — run `vocalize portal` again"
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

# Names that would smuggle the session token somewhere it can leak: a
# query string lands in history and `Referer`, a body lands in a form
# post. Refused wherever they appear, whatever their value.
_TOKEN_PARAM_NAMES = ("token", "access_token", "session", "x-vocalize-token")

ASSETS_DIR = Path(__file__).resolve().parent / "assets"

_ASSETS = {
    "/": ("portal.html", "text/html; charset=utf-8"),
    "/portal.js": ("portal.js", "text/javascript; charset=utf-8"),
}


def _now() -> float:
    """Monotonic seconds. A function, so tests can move time."""
    return time.monotonic()


# --- state ------------------------------------------------------------


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
        # Run 8's hook: a long install must not let the idle watchdog
        # close the portal out from under it.
        self.watchdog_suspended = False
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
            elif secrets.compare_digest(code, self.code):
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

    def token_matches(self, offered: str | None) -> bool:
        if not offered:
            return False
        return secrets.compare_digest(offered, self.token)


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


def _bounded(name: str, work: Callable[[], object], timeout: float, fallback):
    with _probe_lock:
        slot = _probes.get(name)
        if slot is None or slot.thread is None or not slot.thread.is_alive():
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

            slot.thread = threading.Thread(
                target=target, daemon=True, name=f"vocalize-portal-{name}"
            )
            slot.thread.start()
        thread = slot.thread

    thread.join(timeout)
    if thread.is_alive() or slot.value is None:
        return fallback
    return slot.value


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


def _provider_entry(name: str, file_config: dict, primary: bool, timeout: float) -> dict:
    entry: dict = {
        "monthly_chars": config.budget_for(name, file_config),
        "key": _bounded(
            f"key:{name}",
            lambda: _key_status(name, file_config),
            timeout,
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

    Bounded by `timeout` per readiness row and per credential probe, so a
    wedged keychain yields a "still checking" row and the response still
    returns.
    """
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
        "rows": rows,
        "chain": chain,
        "providers": {
            name: _provider_entry(name, file_config, bool(order) and name == order[0], timeout)
            for name in auth.PROVIDER_NAMES
        },
        "budgets": budgets,
        "stt": stt,
    }


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


def _response(status: int, content_type: str, body: bytes):
    return status, {**SECURITY_HEADERS, "Content-Type": content_type}, body


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

    return _json(404, {"error": "no such route"})


# --- server -----------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "vocalize"
    sys_version = ""

    def log_message(self, format, *args):
        """Log nothing.

        The one-time code never reaches the server (it lives in the URL
        fragment), and this keeps it that way for everything else: no
        request line, no query string, no header value is written
        anywhere.
        """

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

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            self._respond(*_json(400, {"error": "bad Content-Length"}), close=True)
            return
        if length > MAX_BODY_BYTES:
            # Refused without reading it: the point of a cap is not to
            # buffer the bytes in the first place.
            self._respond(*_json(413, {"error": "request body too large"}), close=True)
            return

        body = self.rfile.read(length) if length else b""
        self._respond(*route(method, self.path, self.headers, body, state=state))

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
    httpd = ThreadingHTTPServer((BIND_HOST, port), _Handler)
    httpd.daemon_threads = True
    # Handler threads are daemons and keep-alive connections outlive the
    # request, so joining them at close would block on an idle browser
    # tab rather than shut anything down.
    httpd.block_on_close = False
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
