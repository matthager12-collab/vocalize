"""Tests for vocalize.portal — the auth bootstrap, settings and actions.

The security properties this file exists to pin are listed in the module's
own docstring and in design.md § Portal auth (DEC-004). Three of them are
enforced by iterating `portal.ROUTES` rather than by naming routes here, so
a route added in run 8 without a `Host` or token check fails these tests
without anyone remembering to extend them:

    * every route refuses a mismatched `Host`
    * every route but the three named in `_NO_TOKEN_NEEDED` refuses a
      missing or wrong token — selected by path, never by the table's own
      `kind` column, which is part of what is under test
    * every mutating route refuses a token offered in the query string

No socket is involved except where a test says so: `Portal.route()` is a
plain function call, which is what makes the negatives cheap enough to run
against every route.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import sys
import threading
import time
import types
from dataclasses import asdict, dataclass

import pytest

import vocalize.actions as real_actions
import vocalize.profiles as real_profiles
import vocalize.readiness as readiness_module
from vocalize import app as app_module
from vocalize import portal as portal_module
from vocalize import settings_registry, wizard
from vocalize.portal import (
    CSP,
    MAX_BODY,
    ROUTES,
    TOKEN_HEADER,
    Portal,
)

PORT = 45999
ORIGIN = f"127.0.0.1:{PORT}"


@pytest.fixture(autouse=True)
def _reset_readiness_registry():
    """A clean probe registry per test — see tests/test_readiness.py.

    /api/state registers probes (the chain's, and one per provider key), so
    without this a blocked probe from one test would still be reported by
    the next one's state payload.
    """
    readiness_module._PROBES.clear()
    readiness_module._inflight.clear()
    yield
    readiness_module._PROBES.clear()
    readiness_module._inflight.clear()


@dataclass(frozen=True)
class _Param:
    name: str
    label: str
    type: str
    choices: tuple | None = None
    choices_from: str | None = None
    required: bool = False


@dataclass(frozen=True)
class _Action:
    id: str
    label: str
    help: str
    group: str
    kind: str
    confirm: bool = False
    danger: bool = False
    params: tuple = ()


class _Gate:
    """Holds one fake job inside `run` until the test lets it go."""

    def __init__(self) -> None:
        self.reached = threading.Event()
        self.release = threading.Event()
        self.thread: threading.Thread | None = None


class _FakeActions(types.ModuleType):
    """A stand-in for `vocalize.actions` that follows the API contract."""

    class ActionError(Exception):
        pass

    def __init__(self) -> None:
        super().__init__("vocalize.actions")
        self.ACTIONS = (
            _Action("clear_audio_cache", "Clear the audio cache", "Deletes cached audio.",
                    "advanced", "instant", confirm=True, danger=True),
            _Action("install_kokoro", "Install Kokoro", "Downloads the voice model.",
                    "speaking", "job"),
            _Action("install_voice", "Install a voice", "Downloads one voice.",
                    "speaking", "job",
                    params=(_Param("voice", "Voice", "str", choices_from="kokoro_voices",
                                   required=True),)),
            _Action("apply_profile", "Apply a profile", "Sets the profile's values.",
                    "engines", "instant",
                    params=(_Param("name", "Profile", "enum", choices=("small", "standard"),
                                   required=True),)),
        )
        self.calls: list[tuple[str, dict]] = []
        self.fail_with: dict[str, BaseException] = {}
        self.gates: dict[str, _Gate] = {}
        self.voices = [{"value": "af_sky", "label": "Sky"}]

    def gate(self, action_id: str) -> _Gate:
        self.gates[action_id] = _Gate()
        return self.gates[action_id]

    def schema(self) -> list:
        return [asdict(action) | {"params": [asdict(p) for p in action.params]}
                for action in self.ACTIONS]

    def state(self) -> dict:
        return {action.id: {"available": True, "reason": "", "status": "not installed"}
                for action in self.ACTIONS}

    def sources(self) -> dict:
        names = {s.choices_from for s in settings_registry.SETTINGS if s.choices_from}
        listed = {name: [{"value": "x", "label": "X"}] for name in names}
        listed["kokoro_voices"] = list(self.voices)
        return listed

    def run(self, action_id, params, progress):
        self.calls.append((action_id, dict(params)))
        if action_id in self.fail_with:
            raise self.fail_with[action_id]
        gate = self.gates.get(action_id)
        if gate is not None:
            gate.thread = threading.current_thread()
            progress(0.5, "downloading model")
            gate.reached.set()
            assert gate.release.wait(10)
        if action_id == "clear_audio_cache":
            return "Cleared 3 files."
        if action_id == "install_kokoro":
            return "Installed Kokoro."
        return "Done."


class _FakeProfiles(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("vocalize.profiles")

    def names(self):
        return ("small", "standard")

    def detect(self):
        return "standard"

    def changes(self, name):
        return {"stt.warm_minutes": 15}


@pytest.fixture(autouse=True)
def fake_actions(monkeypatch):
    """`vocalize.actions` and `vocalize.profiles` are faked for every test.

    The portal imports both lazily through `importlib`, which reads
    `sys.modules` first, so nothing here runs a real install or listing.
    """
    fake = _FakeActions()
    monkeypatch.setitem(sys.modules, "vocalize.actions", fake)
    monkeypatch.setitem(sys.modules, "vocalize.profiles", _FakeProfiles())
    yield fake
    for gate in fake.gates.values():
        gate.release.set()


@pytest.fixture
def portal():
    """An unbound portal with a known port, for driving `route()` directly."""
    made = Portal(probe_timeout=0.2)
    made.port = PORT
    return made


def _headers(extra: dict | None = None) -> dict:
    headers = {"Host": ORIGIN}
    headers.update(extra or {})
    return headers


def _authed(portal: Portal) -> dict:
    """Headers carrying a genuine session token."""
    return _headers({TOKEN_HEADER: portal._token})


def _body(response) -> dict:
    return json.loads(response[2].decode("utf-8"))


def _exchange(portal: Portal) -> str:
    """Do the real code-for-token exchange and return the token."""
    code = portal._code
    status, _, body = portal.route(
        "POST", "/api/session", _headers(), json.dumps({"code": code}).encode()
    )
    assert status == 200
    return json.loads(body)["token"]


# --- Host pinning -----------------------------------------------------


@pytest.mark.parametrize(("method", "path", "kind"), ROUTES)
def test_host_mismatch_refused_on_every_route(portal, method, path, kind):
    """Including `/`, `/portal.js` and `/api/session` (design § Portal auth)."""
    status, _, body = portal.route(method, path, {"Host": "vocalize.attacker.example"})
    assert status == 403
    assert b"loopback" in body


@pytest.mark.parametrize(
    "host",
    [
        "",
        "localhost:45999",  # a name, so it can be pointed anywhere by DNS
        "127.0.0.1",  # no port: any other loopback server would match
        "127.0.0.1:45998",  # a neighbouring port
        "127.0.0.1:45999.attacker.example",
        "0.0.0.0:45999",
        "[::1]:45999",
    ],
)
def test_host_rebinding_shapes_refused(portal, host):
    status, _, _ = portal.route("GET", "/api/ping", _headers({"Host": host}) | {"Host": host})
    assert status == 403


def test_host_checked_before_the_token(portal):
    """A valid token does not buy a pass on the wrong `Host`."""
    token = _exchange(portal)
    status, _, _ = portal.route(
        "GET", "/api/state", {"Host": "evil.example", TOKEN_HEADER: token}
    )
    assert status == 403


def test_host_header_match_is_case_insensitive_on_the_name(portal):
    status, _, _ = portal.route("GET", "/", {"host": ORIGIN})
    assert status == 200


# --- the one-time code ------------------------------------------------


def test_session_code_is_a_32_byte_urlsafe_secret(portal):
    assert len(portal._code) >= 43  # token_urlsafe(32)
    assert portal._code != portal._token


def test_session_exchanges_the_code_for_a_token(portal):
    token = _exchange(portal)
    assert token == portal._token
    status, _, _ = portal.route("GET", "/api/state", _headers({TOKEN_HEADER: token}))
    assert status == 200


def test_session_code_is_single_use(portal):
    code = portal._code
    _exchange(portal)
    status, _, body = portal.route(
        "POST", "/api/session", _headers(), json.dumps({"code": code}).encode()
    )
    assert status == 403
    assert b"already been used" in body


def test_session_code_expires_after_sixty_seconds(portal, monkeypatch):
    monkeypatch.setattr(portal_module.time, "monotonic", lambda: portal._minted + 61.0)
    status, _, body = portal.route(
        "POST", "/api/session", _headers(), json.dumps({"code": portal._code}).encode()
    )
    assert status == 403
    assert b"expired" in body


def test_session_wrong_code_is_refused(portal):
    status, _, _ = portal.route(
        "POST", "/api/session", _headers(), json.dumps({"code": "nope"}).encode()
    )
    assert status == 403
    assert portal._code is not None  # a wrong guess does not burn the real code


@pytest.mark.parametrize("payload", [b"", b"{", b"[]", b'{"code": 5}', b'{"other": "x"}'])
def test_session_malformed_body_is_refused_not_crashed(portal, payload):
    status, _, _ = portal.route("POST", "/api/session", _headers(), payload)
    assert status == 403


def test_session_oversized_body_refused_before_parsing(portal):
    # Pinned, not read off the module: a payload built from MAX_BODY passes
    # whatever MAX_BODY is, so raising the cap would go unnoticed here.
    assert MAX_BODY == 64 * 1024
    status, _, _ = portal.route("POST", "/api/session", _headers(), b"x" * (MAX_BODY + 1))
    assert status == 413
    assert portal._code is not None
    # And the cap is the boundary: a body exactly at it is still parsed.
    status, _, _ = portal.route("POST", "/api/session", _headers(), b"x" * MAX_BODY)
    assert status == 403


def test_session_secrets_never_appear_in_the_served_page(portal):
    """`GET /` serves no secret (DEC-004, option B is what this rules out)."""
    for path in ("/", "/portal.js"):
        status, _, body = portal.route("GET", path, _headers())
        assert status == 200
        assert portal._code.encode() not in body
        assert portal._token.encode() not in body


# --- lockout ----------------------------------------------------------


def test_lockout_after_five_wrong_codes(portal, capsys):
    for attempt in range(4):
        status, _, body = portal.route(
            "POST", "/api/session", _headers(), json.dumps({"code": "wrong"}).encode()
        )
        assert status == 403
        assert _body((status, None, body))["attempts_left"] == 4 - attempt
        assert not portal.locked_out

    status, _, body = portal.route(
        "POST", "/api/session", _headers(), json.dumps({"code": "wrong"}).encode()
    )
    assert status == 403
    assert portal.locked_out
    assert b"shut down" in body
    assert "too many wrong one-time codes" in capsys.readouterr().err


def test_lockout_refuses_every_later_request(portal):
    token = _exchange(portal)
    for _ in range(portal_module.MAX_CODE_ATTEMPTS):
        portal.route("POST", "/api/session", _headers(), b'{"code": "wrong"}')
    for method, path, _kind in ROUTES:
        status, _, _ = portal.route(method, path, _headers({TOKEN_HEADER: token}))
        assert status == 503


def test_lockout_sixth_request_finds_the_server_gone():
    """The real socket, not the route function: five wrong codes close it."""
    served = Portal(idle_timeout=300)
    served.start()
    server = served._server  # held, for the reason test_idle_watchdog gives
    origin = served.origin
    try:
        for _ in range(portal_module.MAX_CODE_ATTEMPTS):
            conn = http.client.HTTPConnection("127.0.0.1", served.port, timeout=5)
            conn.request("POST", "/api/session", b'{"code": "wrong"}', {"Host": origin})
            assert conn.getresponse().status == 403
            conn.close()

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", served.port, timeout=2)
                conn.request("GET", "/", headers={"Host": origin})
                conn.getresponse().read()
                conn.close()
            except (OSError, http.client.HTTPException):
                break
            time.sleep(0.05)
        else:
            pytest.fail("the server was still answering after five wrong codes")
        assert server.socket.fileno() == -1
    finally:
        served.stop()


# --- the session token ------------------------------------------------


#: The paths allowed to answer without a session token, named here rather
#: than read off `ROUTES`. The table's own `kind` column is the thing these
#: tests exist to check, so filtering on it would let a route mislabeled
#: "none" drop silently out of the parametrization and shrink the suite.
_NO_TOKEN_NEEDED = frozenset({"/", "/portal.js", "/api/session"})

_TOKEN_ROUTES = [(m, p) for m, p, _kind in ROUTES if p not in _NO_TOKEN_NEEDED]

#: The state-changing routes, selected the same way and for the same reason:
#: off the path, never off the table's `kind` column.
_MUTATING_ROUTES = [(m, p) for m, p in _TOKEN_ROUTES if m == "POST"]


def test_the_derived_route_lists_cover_something():
    """An empty parametrize list is a silent skip, not a failing suite.

    Every negative below is parameterized over one of these two lists, so a
    derivation that stops matching would take ten cases with it and leave the
    run green. This is the one case that fails instead.
    """
    assert _TOKEN_ROUTES
    assert _MUTATING_ROUTES


@pytest.mark.parametrize(("method", "path"), _TOKEN_ROUTES)
def test_token_required_on_every_api_route(portal, method, path):
    status, _, _ = portal.route(method, path, _headers())
    assert status == 403


@pytest.mark.parametrize(("method", "path"), _TOKEN_ROUTES)
def test_token_wrong_value_refused_on_every_api_route(portal, method, path):
    status, _, _ = portal.route(method, path, _headers({TOKEN_HEADER: "not-the-token"}))
    assert status == 403


@pytest.mark.parametrize(("method", "path"), _MUTATING_ROUTES)
def test_token_in_query_refused_on_every_mutating_route(portal, method, path):
    """DEC-004: the token is accepted from the header only."""
    token = _exchange(portal)
    status, _, body = portal.route(method, f"{path}?token={token}", _headers())
    assert status == 403
    assert b"never accepted from a URL" in body


@pytest.mark.parametrize(("method", "path"), _MUTATING_ROUTES)
def test_token_in_query_refused_even_alongside_a_valid_header(portal, method, path):
    """A URL that carries the secret is refused whatever else is right."""
    token = _exchange(portal)
    status, _, _ = portal.route(
        method, f"{path}?token={token}", _headers({TOKEN_HEADER: token})
    )
    assert status == 403


def test_token_in_body_does_not_authenticate(portal):
    token = _exchange(portal)
    status, _, _ = portal.route(
        "POST", "/api/settings", _headers(), json.dumps({"token": token}).encode()
    )
    assert status == 403


def test_token_comparison_survives_a_prefix(portal):
    token = _exchange(portal)
    status, _, _ = portal.route("GET", "/api/state", _headers({TOKEN_HEADER: token[:-1]}))
    assert status == 403


def test_token_unknown_path_is_404_with_a_valid_token(portal):
    _exchange(portal)
    status, _, _ = portal.route("GET", "/../../etc/passwd", _authed(portal))
    assert status == 404
    status, _, _ = portal.route("GET", "/api/provider/../secret", _authed(portal))
    assert status == 404


def test_token_wrong_method_is_405_even_with_a_valid_token(portal):
    _exchange(portal)
    status, _, _ = portal.route("POST", "/api/state", _authed(portal))
    assert status == 405


# --- security headers -------------------------------------------------

_REQUIRED_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "media-src 'self' blob:; frame-ancestors 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def _assert_headers(headers: dict) -> None:
    for name, value in _REQUIRED_HEADERS.items():
        assert headers.get(name) == value


def test_headers_csp_is_the_designed_string():
    assert CSP == _REQUIRED_HEADERS["Content-Security-Policy"]


@pytest.mark.parametrize(("method", "path", "kind"), ROUTES)
def test_headers_on_every_authorized_response(portal, method, path, kind):
    _exchange(portal) if kind == "token" else None
    headers_in = _authed(portal) if kind == "token" else _headers()
    body = b'{"code": "wrong"}' if kind == "code" else b"{}"
    _, headers, _ = portal.route(method, path, headers_in, body)
    _assert_headers(headers)


@pytest.mark.parametrize(("method", "path", "kind"), ROUTES)
def test_headers_on_every_refusal(portal, method, path, kind):
    """A 403 is the response an attacker sees most: it needs them too."""
    _, headers, _ = portal.route(method, path, {"Host": "evil.example"})
    _assert_headers(headers)


@pytest.mark.parametrize(("method", "path"), _TOKEN_ROUTES)
def test_headers_on_every_token_refusal(portal, method, path):
    """The 403 an attacker actually collects is this one, not the `Host` one."""
    for headers_in in (_headers(), _headers({TOKEN_HEADER: "not-the-token"})):
        status, headers, _ = portal.route(method, path, headers_in)
        assert status == 403
        _assert_headers(headers)


def test_headers_on_a_token_offered_in_the_url(portal):
    token = _exchange(portal)
    status, headers, _ = portal.route("GET", f"/api/state?token={token}", _headers())
    assert status == 403
    _assert_headers(headers)


def test_headers_on_404_405_413_and_lockout(portal):
    _exchange(portal)
    for response in (
        portal.route("GET", "/nope", _authed(portal)),
        portal.route("POST", "/api/state", _authed(portal)),
        portal.route("POST", "/api/settings", _authed(portal), b"x" * (MAX_BODY + 1)),
    ):
        _assert_headers(response[1])
    portal.lock_out()
    _assert_headers(portal.route("GET", "/", _headers())[1])


def test_headers_over_a_real_socket():
    served = Portal(idle_timeout=300)
    served.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", served.port, timeout=5)
        conn.request("GET", "/", headers={"Host": served.origin})
        response = conn.getresponse()
        payload = response.read()
        assert response.status == 200
        _assert_headers({k: v for k, v in response.getheaders()})
        # The banner is suppressed: http.server's default names the exact
        # Python build, which is a free version number for anything scanning.
        assert response.getheader("Server").strip() == "vocalize"
        assert served._token.encode() not in payload
        conn.close()
    finally:
        served.stop()


def test_headers_content_type_is_declared_for_every_body(portal):
    _exchange(portal)
    _, headers, _ = portal.route("GET", "/", _headers())
    assert headers["Content-Type"].startswith("text/html")
    _, headers, _ = portal.route("GET", "/portal.js", _headers())
    assert headers["Content-Type"].startswith("application/javascript")
    _, headers, _ = portal.route("GET", "/api/ping", _authed(portal))
    assert headers["Content-Type"].startswith("application/json")


# --- GET /api/state ---------------------------------------------------


def _write_config(tmp_path, text: str) -> None:
    directory = tmp_path / "config-home" / "vocalize"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.toml").write_text(text, encoding="utf-8")


def test_state_reports_the_app_key_from_status_dict(portal, monkeypatch):
    """/api/state's `app` key is app.status_dict() verbatim (design §
    Readiness, doctor and the Setup tab) — stubbed here so the test asserts
    the wiring, not the real machine's launchctl/app.status.
    """
    stubbed = {
        "bundle": "current",
        "agent": "loaded",
        "accessibility": "granted",
        "hotkeys": "ok",
        "hotkey_backend": "carbon",
        "vocalize": "/opt/homebrew/bin/vocalize",
    }
    monkeypatch.setattr(app_module, "status_dict", lambda: stubbed)
    _exchange(portal)
    payload = _body(portal.route("GET", "/api/state", _authed(portal)))
    assert payload["app"] == stubbed


def test_state_never_reproduces_a_hostile_app_status_word_unfiltered(portal, monkeypatch):
    """`app` on the wire is whatever app.status_dict() returns; app.py's own
    read_status() is what sanitizes it (DEC-028) — this pins that a hostile
    word making it past that guard would still not corrupt the response
    shape, since the key is passed through as a plain dict.
    """
    hostile = "unknown"  # what a hostile word must already have become
    monkeypatch.setattr(
        app_module,
        "status_dict",
        lambda: {
            "bundle": "not built",
            "agent": "unknown",
            "accessibility": hostile,
            "hotkeys": "unknown",
            "hotkey_backend": "unknown",
            "vocalize": "unknown",
        },
    )
    _exchange(portal)
    status, _, raw = portal.route("GET", "/api/state", _authed(portal))
    assert status == 200
    payload = json.loads(raw)
    assert payload["app"]["accessibility"] == "unknown"


def test_state_hanging_probe_yields_a_warn_row_and_the_response_returns(portal):
    """A wedged keychain costs one warn row, never the page."""
    event = threading.Event()  # released in the finally, or its thread outlives us
    readiness_module._PROBES["blocked"] = event.wait
    _exchange(portal)

    try:
        start = time.monotonic()
        status, _, raw = portal.route("GET", "/api/state", _authed(portal))
        elapsed = time.monotonic() - start

        assert status == 200
        row = next(r for r in json.loads(raw)["rows"] if r["name"] == "blocked")
        assert row["state"] == "warn"
        assert "still checking" in row["detail"]
        assert elapsed < portal.probe_timeout + 2.0
    finally:
        event.set()


def test_state_ten_polls_against_a_blocked_probe_start_one_thread(portal):
    """The portal polls; a wedged native call must leak one thread, not ten.

    Counted at the probe rather than with `threading.active_count()`: the
    other probes in a poll are short-lived, so a live thread count is a
    race, not a measurement.
    """
    release = threading.Event()
    started = []

    def wedged():
        started.append(1)
        release.wait(10)
        return readiness_module.Row("wedged", "ok", "", "")

    readiness_module._PROBES["wedged"] = wedged
    _exchange(portal)
    try:
        for _ in range(10):
            status, _, _ = portal.route("GET", "/api/state", _authed(portal))
            assert status == 200
        assert started == [1]
    finally:
        release.set()


def test_state_survives_a_broken_config_file(portal, tmp_path):
    """The page that exists to fix a bad config must still load."""
    _write_config(tmp_path, 'chain = ["say"]\nspeed = 99\n[providers.say]\nspeed = 99\n')
    _exchange(portal)
    status, _, raw = portal.route("GET", "/api/state", _authed(portal))
    payload = json.loads(raw)

    assert status == 200
    # This file parses, so `load_config_file` is happy and the per-provider
    # resolve is what refuses it. Pinned to that half rather than to a
    # disjunction: an `or` here passes with `config_error` dropped entirely.
    # The other half — a file that will not parse at all — is
    # test_state_survives_unreadable_config.
    assert payload["config_error"] is None
    assert "Invalid speed 99" in payload["providers"]["say"]["error"]


def test_state_survives_unreadable_config(portal, monkeypatch):
    from vocalize.exceptions import ConfigError

    monkeypatch.setattr(
        portal_module.config,
        "load_config_file",
        lambda: (_ for _ in ()).throw(ConfigError("bad toml")),
    )
    _exchange(portal)
    status, _, raw = portal.route("GET", "/api/state", _authed(portal))
    assert status == 200
    assert json.loads(raw)["config_error"] == "bad toml"


def test_state_is_not_cached_by_the_browser(portal):
    _exchange(portal)
    _, headers, _ = portal.route("GET", "/api/state", _authed(portal))
    assert headers["Cache-Control"] == "no-store"


# --- blocking until it ends -------------------------------------------


def test_ctrl_c_closes_the_socket_rather_than_escaping():
    """`vocalize portal` has no except of its own: this is where Ctrl-C lands."""
    served = Portal()
    served.start()
    server = served._server  # held, so only server_close() can free the port
    port = served.port
    try:
        def interrupted(timeout=None):
            raise KeyboardInterrupt

        served._stopping.wait = interrupted

        served.serve_until_stopped()  # must not raise

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                probe.settimeout(1)
                if probe.connect_ex(("127.0.0.1", port)) != 0:
                    break
            time.sleep(0.05)
        else:
            pytest.fail("the socket was still open after Ctrl-C")
        assert server.socket.fileno() == -1
    finally:
        served.stop()


def test_the_wait_returns_once_the_portal_is_stopped():
    """The exit-0 path: the watchdog or a lockout calls stop(), and this returns."""
    served = Portal()
    served.start()
    try:
        # In a thread with a deadline, so a loop that never notices fails
        # the test instead of hanging the suite.
        waiter = threading.Thread(
            target=served.serve_until_stopped, kwargs={"poll": 0.01}, daemon=True
        )
        waiter.start()
        served.stop()
        waiter.join(5)
        assert not waiter.is_alive(), "serve_until_stopped never noticed the stop"
    finally:
        served.stop()


# --- the idle watchdog ------------------------------------------------


def test_idle_watchdog_closes_the_portal(capsys):
    served = Portal(idle_timeout=0.05)
    served.start()
    # Held, so the socket can only be closed by stop() calling server_close().
    # Without this reference `stop()` dropping the Portal's own is enough for
    # the collector to close the port, and the check below cannot tell the two
    # apart.
    server = served._server
    port = served.port
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not served._stopping.is_set():
            time.sleep(0.05)
        assert served._stopping.is_set(), "the watchdog never fired"
        assert "closed itself" in capsys.readouterr().err

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                probe.settimeout(1)
                if probe.connect_ex(("127.0.0.1", port)) != 0:
                    break
            time.sleep(0.05)
        else:
            pytest.fail("the socket was still open after the idle timeout")
        assert server.socket.fileno() == -1
    finally:
        served.stop()


def test_idle_watchdog_can_be_suspended(monkeypatch):
    """Run 8 holds an install open for minutes with no request in between.

    The suspension has to outlast several watchdog ticks or a `suspend_idle`
    that did nothing at all would pass too — with the shipped 0.5s poll a
    0.4s window never sees a single tick. Shortening the poll is what makes
    the window worth 40 of them.
    """
    monkeypatch.setattr(portal_module, "_IDLE_POLL", 0.01)
    served = Portal(idle_timeout=0.05)
    served.start()
    try:
        with served.suspend_idle():
            time.sleep(0.4)  # ~40 ticks, ~8 idle timeouts
            assert not served._stopping.is_set()
        # And starts counting again on the way out.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not served._stopping.is_set():
            time.sleep(0.01)
        assert served._stopping.is_set()
    finally:
        served.stop()


def test_idle_watchdog_is_reset_by_a_request(portal):
    """The page's own keepalive: `GET /api/ping`, with the session token."""
    _exchange(portal)
    before = portal._seen
    time.sleep(0.01)
    portal.route("GET", "/api/ping", _authed(portal))
    assert portal._seen > before


def test_idle_watchdog_is_not_reset_by_a_wrong_host(portal):
    before = portal._seen
    time.sleep(0.01)
    portal.route("GET", "/", {"Host": "evil.example"})
    assert portal._seen == before


# --- the opening URL --------------------------------------------------


def test_start_returns_a_loopback_url_with_the_code_in_the_fragment():
    served = Portal(idle_timeout=300)
    url = served.start()
    try:
        assert url.startswith(f"http://127.0.0.1:{served.port}/#code=")
        assert url.endswith(served._code)
        # The fragment is the point: nothing before the '#' carries a secret.
        assert served._code not in url.split("#", 1)[0]
    finally:
        served.stop()


# --- run-7 review fixes -----------------------------------------------
#
# One test per confirmed defect in the run-7 adversarial review
# (docs/plans/2026-09-next-features/run-7-portal-read/review-findings.md).


def test_cross_origin_post_does_not_count_toward_the_lockout(portal):
    """Any open tab could otherwise shut the portal down in five requests.

    A cross-origin POST needs no preflight and reads no answer, so the page
    that sends it learns nothing — but it used to burn a code attempt.
    """
    hostile = _headers({"Origin": "https://evil.example"})
    for _ in range(portal_module.MAX_CODE_ATTEMPTS * 2):
        status, _, body = portal.route(
            "POST", "/api/session", hostile, json.dumps({"code": "wrong"}).encode()
        )
        assert status == 403
        assert b"its own page" in body

    assert portal._code_attempts == 0
    assert not portal.locked_out
    assert _exchange(portal)  # and the real code still works afterwards


def test_cross_origin_is_refused_on_every_route(portal):
    _exchange(portal)
    for method, path, kind in ROUTES:
        headers = _authed(portal) if kind == "token" else _headers()
        status, headers_out, _ = portal.route(
            method, path, headers | {"Origin": "https://evil.example"}, b"{}"
        )
        assert status == 403, path
        _assert_headers(headers_out)


def test_same_origin_requests_are_not_refused(portal):
    """The page's own fetch sends `Origin` on a same-origin POST."""
    mine = _headers({"Origin": f"http://{portal.origin}"})
    status, _, body = portal.route(
        "POST", "/api/session", mine, json.dumps({"code": portal._code}).encode()
    )
    assert status == 200
    assert json.loads(body)["token"] == portal._token


@pytest.mark.parametrize(
    "origin",
    [
        "null",
        f"http://{ORIGIN}.evil.example",
        f"http://evil.example#{ORIGIN}",
        f"https://{ORIGIN}",
    ],
)
def test_near_miss_origins_are_refused(portal, origin):
    """The check is an equality — not a prefix, a suffix, or a truthiness test.

    A `startswith` lets `http://127.0.0.1:PORT.evil.example` through, an
    `endswith` lets `http://evil.example#127.0.0.1:PORT` through, and
    normalising `Origin: null` to empty hands a sandboxed iframe — which
    sends exactly that — the free pass that exists for `curl`.
    """
    status, _, body = portal.route("GET", "/api/state", _headers({"Origin": origin}))
    assert status == 403
    assert b"its own page" in body


@pytest.mark.parametrize("secret", ["tokén", "🔑"])
def test_non_ascii_token_is_refused_not_crashed(portal, secret):
    """compare_digest raises TypeError on non-ASCII: that must not escape."""
    _exchange(portal)
    status, _, _ = portal.route("GET", "/api/state", _headers({TOKEN_HEADER: secret}))
    assert status == 403


@pytest.mark.parametrize("secret", ["cøde", "🔑"])
def test_non_ascii_code_is_refused_not_crashed(portal, secret):
    status, _, _ = portal.route(
        "POST", "/api/session", _headers(), json.dumps({"code": secret}).encode()
    )
    assert status == 403
    assert portal._code_attempts == 1  # counted as the wrong guess it is


def _raw(port: int, request: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(request)
        chunks = []
        while chunk := sock.recv(4096):
            chunks.append(chunk)
    return b"".join(chunks)


@pytest.mark.parametrize(
    "request_line",
    [
        b"OPTIONS / HTTP/1.1",  # a CORS preflight
        b"PUT /api/state HTTP/1.1",
        b"GARBAGE",  # never parsed: http.server answers this one itself
        b"GET / HTTP/9.9",
    ],
)
def test_every_response_over_the_socket_carries_the_headers(request_line):
    served = Portal(idle_timeout=300)
    served.start()
    try:
        raw = _raw(
            served.port,
            request_line + b"\r\nHost: " + served.origin.encode() + b"\r\n\r\n",
        )
        assert raw.startswith(b"HTTP/1.")
        assert b"Content-Security-Policy: " + CSP.encode() in raw
        assert b"X-Frame-Options: DENY" in raw
        assert b"X-Content-Type-Options: nosniff" in raw
    finally:
        served.stop()


def test_the_request_line_is_never_logged(capsys):
    """The module docstring's last property: a path can carry a secret.

    http.server logs every request line to stderr by default, query string
    and all, which is exactly where the portal refuses to read a token from.
    """
    served = Portal(idle_timeout=300)
    served.start()
    try:
        _raw(
            served.port,
            b"GET /?x=sk-canary-9f3 HTTP/1.1\r\nHost: "
            + served.origin.encode()
            + b"\r\n\r\n",
        )
        assert "sk-canary" not in capsys.readouterr().err
    finally:
        served.stop()


def test_head_gets_the_headers_but_never_a_body():
    """A HEAD reaches route() like any other method, and carries no body."""
    served = Portal(idle_timeout=300)
    served.start()
    try:
        raw = _raw(
            served.port,
            b"HEAD /api/state HTTP/1.1\r\nHost: " + served.origin.encode() + b"\r\n\r\n",
        )
        head, _, body = raw.partition(b"\r\n\r\n")
        assert b" 405 " in head  # route()'s answer, not http.server's own 501
        assert b"Content-Length: 0" not in head  # the length is still declared
        assert body == b""  # ...but a HEAD response never carries one
    finally:
        served.stop()


@pytest.mark.parametrize("method", ["OPTIONS", "PUT", "DELETE", "HEAD"])
def test_other_methods_over_the_socket_meet_the_host_pin(method):
    """http.server's own 501 answered these without ever checking `Host`."""
    served = Portal(idle_timeout=300)
    served.start()
    try:
        line = method.encode() + b" /api/state HTTP/1.1\r\nHost: "
        assert b" 403 " in _raw(served.port, line + b"evil.example\r\n\r\n")
        assert b" 405 " in _raw(served.port, line + served.origin.encode() + b"\r\n\r\n")
    finally:
        served.stop()


def test_oversized_body_over_the_socket_meets_the_host_pin():
    """The handler's pre-read 413 used to answer any `Host` at all."""
    served = Portal(idle_timeout=300)
    served.start()
    try:
        raw = _raw(
            served.port,
            b"POST /api/settings HTTP/1.1\r\nHost: evil.example\r\n"
            b"Content-Length: 999999\r\n\r\n",
        )
        assert b" 403 " in raw
        assert b"loopback" in raw
    finally:
        served.stop()


def test_stop_closes_the_listening_socket():
    """Not "the collector gets round to it": stop() releases the port."""
    served = Portal(idle_timeout=300)
    served.start()
    server = served._server  # a reference, so nothing can be freed silently
    port = served.port
    served.stop()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and server.socket.fileno() != -1:
        time.sleep(0.05)
    assert server.socket.fileno() == -1, "the listening socket was never closed"

    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex(("127.0.0.1", port)) != 0


def test_session_exchange_is_serialized_by_the_lock(portal):
    """Single-use and five-attempts are check-then-set on worker threads."""
    portal._lock.acquire()
    done = threading.Event()

    def exchange() -> None:
        portal.route(
            "POST", "/api/session", _headers(), json.dumps({"code": portal._code}).encode()
        )
        done.set()

    threading.Thread(target=exchange, daemon=True).start()
    try:
        assert not done.wait(0.3), "the exchange ran without holding the lock"
    finally:
        portal._lock.release()
    assert done.wait(5)


def test_idle_watchdog_is_not_reset_by_an_unauthenticated_request(portal):
    """A stale or hostile tab must not hold the portal open by polling."""
    _exchange(portal)
    before = portal._seen
    time.sleep(0.01)
    portal.route("GET", "/", _headers())  # anonymous static page
    portal.route("GET", "/api/state", _headers())  # no token: 403
    portal.route("GET", "/api/state", _headers({TOKEN_HEADER: "wrong"}))
    portal.route("GET", "/nope", _headers())  # 404
    assert portal._seen == before


def test_malformed_request_line_is_not_echoed_back():
    """http.server's own 400 puts the request line in the body — and a path
    can carry a secret, which is why this server never logs one either."""
    served = Portal(idle_timeout=300)
    served.start()
    try:
        raw = _raw(
            served.port,
            b"GET /?token=sk-canary-9f3 EXTRA HTTP/1.1\r\nHost: "
            + served.origin.encode()
            + b"\r\n\r\n",
        )
        assert b" 400 " in raw
        assert b"sk-canary" not in raw
    finally:
        served.stop()


def test_start_binds_the_loopback_address_only():
    """Asked of the socket, not of the string `start()` passes in.

    A bind to 0.0.0.0 puts the portal on the LAN with nothing in front of
    it but a `Host` header an attacker writes.
    """
    served = Portal(idle_timeout=300)
    served.start()
    try:
        assert served._server.socket.getsockname()[0] == "127.0.0.1"
    finally:
        served.stop()


@pytest.mark.parametrize("declared", [b"999999", b"nonsense"])
def test_declared_oversized_body_is_refused_without_reading_it(declared):
    """The handler's own cap, which route()'s post-read check never sees.

    The `Content-Length` is a lie in both cases and not one byte of body
    follows it, so a handler that read before it refused would sit here
    until this socket's timeout rather than answering 413.
    """
    served = Portal(idle_timeout=300)
    served.start()
    try:
        raw = _raw(
            served.port,
            b"POST /api/settings HTTP/1.1\r\nHost: "
            + served.origin.encode()
            + b"\r\nContent-Length: "
            + declared
            + b"\r\n\r\n",
        )
        assert b" 413 " in raw
    finally:
        served.stop()


def test_a_negative_content_length_does_not_pass_as_an_empty_body():
    """`Content-Length: -5` is a refusal, not a bodyless `/api/session` POST.

    Read as an empty body it reaches the code exchange as a wrong guess and
    burns one of the five attempts, so five of them shut the portal down.
    """
    served = Portal(idle_timeout=300)
    served.start()
    try:
        raw = _raw(
            served.port,
            b"POST /api/session HTTP/1.1\r\nHost: "
            + served.origin.encode()
            + b"\r\nContent-Length: -5\r\n\r\n",
        )
        assert b" 413 " in raw
        assert served._code_attempts == 0
    finally:
        served.stop()


def test_an_http_0_9_request_gets_no_answer_at_all():
    """0.9 has no status line and no headers, so it gets no response.

    http.server writes the body and silently drops every header for a 0.9
    request, which would make this the one shape on the server answered
    without a CSP.
    """
    served = Portal(idle_timeout=300)
    served.start()
    try:
        assert _raw(served.port, b"GET /\r\n\r\n") == b""
    finally:
        served.stop()


def test_a_half_open_connection_is_dropped_not_held(monkeypatch):
    """A peer that connects and sends nothing must not hold a thread forever.

    Thousands of them and `ThreadingHTTPServer` cannot start a thread for the
    owner's own request. The real timeout is ten seconds; this drives the same
    class attribute at a length a test can wait for.
    """
    assert portal_module._Handler.timeout, "no per-connection timeout is set"
    monkeypatch.setattr(portal_module._Handler, "timeout", 0.2)
    served = Portal(idle_timeout=300)
    served.start()
    try:
        with socket.create_connection(("127.0.0.1", served.port), timeout=5) as sock:
            sock.sendall(b"GET / HTTP/1.1\r\n")  # no blank line: never complete
            assert sock.recv(4096) == b""  # the server hung up on it
    finally:
        served.stop()


def test_the_connection_timeout_is_ten_seconds():
    """The slowloris fix is a number, so the number is what has to be pinned.

    `test_a_half_open_connection_is_dropped_not_held` proves the mechanism
    but monkeypatches the value to run fast, so raising the production
    figure to something useless leaves it — and every other portal test —
    green. Ten seconds is far longer than any real loopback exchange and
    short enough that a flood of silent connections cannot hold threads.
    """
    assert portal_module._Handler.timeout == 10.0


class _Wfile:
    """A socket write that fails the way `error` says."""

    def __init__(self, error: BaseException) -> None:
        self.error = error

    def write(self, data: bytes) -> int:
        raise self.error


def _dead_handler(error: BaseException) -> object:
    """A `_Handler` wired to nothing but a failing write."""
    handler = portal_module._Handler.__new__(portal_module._Handler)
    handler.request_version = "HTTP/1.1"
    handler.requestline = "GET /api/ping HTTP/1.1"
    handler.command = "GET"
    handler.close_connection = False
    handler.wfile = _Wfile(error)
    return handler


def test_a_peer_that_hung_up_mid_answer_is_not_reported():
    """A gone peer is nothing to report — and not a 500 either.

    `_respond` turns a bug in `route()` into a 500, but the write that puts
    that answer on the wire is the last thing it does. A client that closed
    the tab between request and response makes those writes raise
    `BrokenPipeError` or `ConnectionResetError` out of the handler, which
    socketserver prints as the terminal traceback the 500 exists to remove.
    """
    handler = _dead_handler(BrokenPipeError(32, "Broken pipe"))

    handler._write(200, {"Content-Type": "application/json"}, b"{}")

    assert handler.close_connection  # the connection is finished with


def test_a_genuine_write_bug_still_raises():
    """The guard is for a peer that went away, not for every failure.

    Swallowing everything would hide a real defect in `_write` itself —
    which is exactly what a caught exception on this path must not do.
    """
    handler = _dead_handler(ValueError("headers are not bytes"))

    with pytest.raises(ValueError):
        handler._write(200, {}, b"{}")


def test_a_peer_side_connection_failure_prints_no_traceback(capsys):
    """A connection flood must not fill the owner's terminal with stacks.

    The ten-second timeout stops a flood exhausting threads, but every
    dropped socket still reached socketserver's `handle_error`, which
    prints four lines and a stack per connection for something that is not
    a bug.
    """
    server = portal_module._Server.__new__(portal_module._Server)

    try:
        raise ConnectionResetError(54, "Connection reset by peer")
    except ConnectionResetError:
        server.handle_error(None, ("127.0.0.1", 0))

    assert capsys.readouterr().err == ""


def test_a_genuine_handler_bug_still_prints_its_traceback(capsys):
    """Silencing the flood may not silence a real defect."""
    server = portal_module._Server.__new__(portal_module._Server)

    try:
        raise ValueError("a real bug in the handler")
    except ValueError:
        server.handle_error(None, ("127.0.0.1", 0))

    assert "ValueError: a real bug in the handler" in capsys.readouterr().err


def test_state_renders_a_config_value_json_cannot_serialize(portal, tmp_path):
    """TOML has types json does not, and `[providers.*]` values pass unchecked.

    A bare date in a hand-edited table used to raise out of `json.dumps` —
    taking down the page whose whole job is to fix that config.
    """
    _write_config(tmp_path, 'chain = ["say"]\n[providers.say]\nvoice = 1979-05-27\n')
    _exchange(portal)

    status, _, raw = portal.route("GET", "/api/state", _authed(portal))

    # Since 0.12.0 a non-string voice is refused when the file is read
    # (issue #5), so the page gets a config_error naming it instead of a
    # date object it cannot serialize — and stays up either way.
    assert status == 200
    body = json.loads(raw)
    assert "voice" in body["config_error"] and "[providers.say]" in body["config_error"]


def test_state_reports_a_wrong_provider_voice_type_in_config_error(portal, tmp_path):
    _write_config(tmp_path, 'chain = ["say"]\n[providers.kokoro]\nvoice = 12345\n')
    _exchange(portal)

    status, _, raw = portal.route("GET", "/api/state", _authed(portal))

    assert status == 200
    body = json.loads(raw)
    assert "voice" in body["config_error"] and "12345" in body["config_error"]


def test_a_bug_in_route_is_a_500_not_a_dropped_connection(monkeypatch):
    """No status line at all is indistinguishable from the portal exiting."""

    def boom(*args, **kwargs):
        raise ZeroDivisionError("bad-canary-detail")

    served = Portal(idle_timeout=300)
    served.start()
    monkeypatch.setattr(Portal, "route", boom)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", served.port, timeout=5)
        conn.request("GET", "/", headers={"Host": served.origin})
        response = conn.getresponse()
        body = response.read()

        assert response.status == 500
        _assert_headers(dict(response.getheaders()))
        # An unplanned exception's message is untrusted text (readiness'
        # rule): the reply says nothing about what raised.
        assert b"bad-canary-detail" not in body
        assert b"ZeroDivisionError" not in body
        conn.close()
    finally:
        served.stop()


# --- writes: the compare-and-swap fingerprint --------------------------


def _config_file(tmp_path):
    return tmp_path / "config-home" / "vocalize" / "config.toml"


def _fingerprint(portal) -> object:
    """The fingerprint `/api/state` hands the page. The page has no other."""
    return _body(portal.route("GET", "/api/state", _authed(portal)))["fingerprint"]


def _on_the_wire(fingerprint: dict) -> dict:
    """`wizard`'s dict of ints as the portal sends it: `mtime_ns` as a string.

    A nanosecond timestamp has 19 digits and JavaScript's Number is exact
    to 2**53, so an integer would come back rounded and every write would
    be refused. The page returns the value verbatim and never reads it.
    """
    return {"mtime_ns": str(fingerprint["mtime_ns"]), "sha256": fingerprint["sha256"]}


def _as_javascript_would(body: bytes) -> dict:
    """Parse a response the way `JSON.parse` does: every number is a double."""
    return json.loads(body.decode("utf-8"), parse_int=float)


def _post(portal, path: str, payload: dict):
    """One authenticated write. Returns `(status, parsed body)`."""
    status, _, body = portal.route(
        "POST", path, _authed(portal), json.dumps(payload).encode()
    )
    return status, json.loads(body)


def test_state_carries_the_fingerprint_of_the_file_on_disk(portal, tmp_path):
    """The page cannot send back a fingerprint it was never given."""
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    assert _fingerprint(portal) == _on_the_wire(wizard.fingerprint_config(_config_file(tmp_path)))


def test_state_calls_a_missing_config_file_absent(portal):
    _exchange(portal)
    assert _fingerprint(portal) == wizard.ABSENT_CONFIG


def test_state_takes_the_fingerprint_before_the_values_it_ships(portal, tmp_path, monkeypatch):
    """DEC-005's ordering rule, and the one the reference got backwards.

    Taken before the parse, a change landing after it makes the page's
    later write fail the compare-and-swap — a false refusal, which is
    safe. Taken after, the page renders the OLD values while holding a NEW
    fingerprint, its write passes, and the other writer is silently
    overwritten with a decision made on stale data.
    """
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    before = wizard.fingerprint_config(_config_file(tmp_path))

    def load_and_race():
        # Somebody else writes while /api/state is still gathering.
        _write_config(tmp_path, 'chain = ["kokoro"]\n')
        return {}

    monkeypatch.setattr(portal_module.config, "load_config_file", load_and_race)
    issued = _fingerprint(portal)

    assert issued == _on_the_wire(before)
    assert issued != _on_the_wire(wizard.fingerprint_config(_config_file(tmp_path)))


def test_state_survives_a_config_file_it_cannot_fingerprint(portal, monkeypatch):
    """One guarded section, not a dead route — as everywhere else in _state."""
    def unreadable(path):
        raise portal_module.config.ConfigError("Could not read config file")

    monkeypatch.setattr(portal_module.wizard, "fingerprint_config", unreadable)
    _exchange(portal)
    status, _, body = portal.route("GET", "/api/state", _authed(portal))

    assert status == 200
    assert json.loads(body)["fingerprint"] is None


# --- POST /api/settings -----------------------------------------------


def _save(portal, changes: dict, fingerprint=None):
    """One settings save with the fingerprint `/api/state` just issued."""
    if fingerprint is None:
        fingerprint = _fingerprint(portal)
    return _post(portal, "/api/settings", {"changes": changes, "fingerprint": fingerprint})


def _reload() -> dict:
    return portal_module.config.load_config_file()


def test_a_save_writes_the_change_and_answers_with_values_and_a_fresh_fingerprint(
    portal, tmp_path
):
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)

    status, body = _save(portal, {"chain": ["kokoro", "say"]})

    assert status == 200, body
    path = _config_file(tmp_path)
    assert 'chain = ["kokoro", "say"]' in path.read_text()
    # The page writes again without polling, so the fingerprint has to be
    # the one the file now holds.
    assert body["fingerprint"] == _on_the_wire(wizard.fingerprint_config(path))
    assert body["values"]["chain"] == ["kokoro", "say"]
    assert body["values"] == settings_registry.values(_reload())
    assert body["notices"] == []
    assert body["restart"] == []


def test_a_save_of_one_key_leaves_every_other_key_in_the_file_untouched(portal, tmp_path):
    _write_config(
        tmp_path,
        'chain = ["say", "kokoro"]\nmax_chars = 900\n'
        '[stt]\nmax_seconds = 30\nmodel = "base.en"\n'
        '[app]\nstop_hotkey = "pause"\n'
        '[providers.kokoro]\nvoice = "abc"\nspeed = 1.1\n'
        '[providers.say]\nvoice = "Samantha"\n',
    )
    _exchange(portal)
    before = _reload()

    status, body = _save(portal, {"stt.paste": False})

    assert status == 200, body
    after = _reload()
    assert after["stt"].pop("paste") is False
    assert after == before


def test_null_resets_a_key_to_its_default_and_drops_an_emptied_table(portal, tmp_path):
    _write_config(tmp_path, '[stt]\nmax_seconds = 30\n[providers.kokoro]\nvoice = "abc"\n')
    _exchange(portal)

    status, body = _save(portal, {"stt.max_seconds": None})

    assert status == 200, body
    text = _config_file(tmp_path).read_text()
    assert "max_seconds" not in text and "None" not in text and "[stt]" not in text
    assert 'voice = "abc"' in text
    defaults = settings_registry.defaults()
    assert body["values"]["stt.max_seconds"] == defaults["stt.max_seconds"]


def test_a_save_reports_the_restart_each_changed_setting_needs(portal, tmp_path):
    _exchange(portal)
    status, body = _save(
        portal,
        {"app.dictate_mode": "hold", "app.stop_hotkey": "pause", "stt.warm_minutes": 5,
         "stt.paste": False},
    )

    assert status == 200, body
    assert body["restart"] == ["app", "warm"]


def test_a_save_with_invalid_values_reports_every_bad_key_and_writes_nothing(portal, tmp_path):
    _write_config(tmp_path, '[stt]\nmax_seconds = 30\n')
    _exchange(portal)
    before = _config_file(tmp_path).read_bytes()

    status, body = _save(
        portal,
        {
            "stt.max_seconds": 0,
            "stt.cues": "shout",
            "chain": ["nope"],
            "stt.paste": False,  # valid, and still not written
        },
    )

    assert status == 400
    assert set(body["errors"]) == {"stt.max_seconds", "stt.cues", "chain"}
    assert all(isinstance(message, str) and message for message in body["errors"].values())
    assert _config_file(tmp_path).read_bytes() == before


def test_an_unknown_key_is_400_and_never_written(portal, tmp_path):
    _write_config(tmp_path, '[stt]\nmax_seconds = 30\n')
    _exchange(portal)
    before = _config_file(tmp_path).read_bytes()

    status, body = _save(portal, {"stt.modle": "base.en", "stt.paste": False})

    assert status == 400
    assert list(body["errors"]) == ["stt.modle"]
    assert _config_file(tmp_path).read_bytes() == before


def test_a_retired_key_is_not_a_setting_the_page_can_write(portal):
    _exchange(portal)
    status, body = _save(portal, {"providers.kokoro.monthly_chars": 5}, wizard.ABSENT_CONFIG)
    assert status == 400
    assert "providers.kokoro.monthly_chars" in body["errors"]
    assert not portal_module.config.config_path().exists()


def test_a_bad_value_uses_the_loaders_own_wording(portal, tmp_path):
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    status, body = _save(portal, {"providers.kokoro.speed": 99})

    assert status == 400
    assert body["errors"]["providers.kokoro.speed"] == (
        f"Invalid speed 99.0 from 'speed' in [providers.kokoro] in "
        f"{_config_file(tmp_path)}: must be between 0.7 and 1.2."
    )


@pytest.mark.parametrize("voice", [12345, ["a"], "x" * 2001, "a\x00b", True])
def test_an_unusable_provider_value_is_400_and_never_echoed_at_length(portal, tmp_path, voice):
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    before = _config_file(tmp_path).read_bytes()

    status, _, raw = portal.route(
        "POST", "/api/settings", _authed(portal),
        json.dumps({"changes": {"providers.kokoro.voice": voice},
                    "fingerprint": _fingerprint(portal)}).encode(),
    )

    assert status == 400
    assert "providers.kokoro.voice" in json.loads(raw)["errors"]
    assert b"x" * (portal_module.MESSAGE_MAX + 1) not in raw
    assert _config_file(tmp_path).read_bytes() == before


def test_a_rule_between_two_keys_is_checked_against_the_merged_table(portal, tmp_path):
    """Half the pairing rule is already on disk; only `model` is submitted."""
    _write_config(tmp_path, '[stt]\nmodel = "large-v3-turbo-q5_0"\nlanguage = "fr"\n')
    _exchange(portal)
    before = _config_file(tmp_path).read_bytes()

    status, body = _save(portal, {"stt.model": "small.en"})

    assert status == 400
    assert "small.en" in body["errors"]["stt.model"]
    assert _config_file(tmp_path).read_bytes() == before


@pytest.mark.parametrize("backend", ["claude-cli", "anthropic"])
def test_hosted_cleanup_is_refused_without_writing(portal, backend):
    _exchange(portal)
    status, body = _save(portal, {"stt.cleanup": backend}, wizard.ABSENT_CONFIG)
    assert status == 400
    assert "stt.cleanup" in body["errors"]
    assert not portal_module.config.config_path().exists()


def test_a_save_with_a_stale_fingerprint_is_409_with_the_files_own_state(portal, tmp_path):
    """The acceptance criterion: changed on disk between read and write."""
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    fingerprint = _fingerprint(portal)
    _write_config(tmp_path, 'chain = ["kokoro"]\n')  # somebody else wrote
    before = _config_file(tmp_path).read_bytes()

    status, body = _save(portal, {"chain": ["say"]}, fingerprint)

    assert status == 409
    assert body["error"] == "changed"
    assert body["fingerprint"] == _on_the_wire(wizard.fingerprint_config(_config_file(tmp_path)))
    assert body["values"]["chain"] == ["kokoro"]
    assert _config_file(tmp_path).read_bytes() == before


def test_a_file_that_changes_during_the_save_is_409(portal, tmp_path, monkeypatch):
    """The writer's own compare-and-swap catches a write landing mid-request."""
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    fingerprint = _fingerprint(portal)
    real = portal_module.settings_registry.apply_changes

    def apply_and_race(data, changes):
        _write_config(tmp_path, 'chain = ["kokoro"]\n')
        return real(data, changes)

    monkeypatch.setattr(portal_module.settings_registry, "apply_changes", apply_and_race)
    status, body = _save(portal, {"chain": ["say", "kokoro"]}, fingerprint)

    assert status == 409
    assert body["error"] == "changed"
    assert body["values"]["chain"] == ["kokoro"]
    assert 'chain = ["kokoro"]' in _config_file(tmp_path).read_text()


def test_a_save_with_an_absent_fingerprint_creates_the_file(portal, tmp_path):
    _exchange(portal)
    assert not _config_file(tmp_path).exists()

    status, _ = _save(portal, {"chain": ["say"]}, wizard.ABSENT_CONFIG)

    assert status == 200
    assert 'chain = ["say"]' in _config_file(tmp_path).read_text()


def test_a_save_under_an_absent_fingerprint_over_a_file_that_appeared_is_409(portal, tmp_path):
    _exchange(portal)
    _write_config(tmp_path, 'chain = ["kokoro"]\n')  # it appeared underneath
    before = _config_file(tmp_path).read_bytes()

    status, body = _save(portal, {"chain": ["say"]}, wizard.ABSENT_CONFIG)

    assert status == 409
    assert body["error"] == "changed"
    assert _config_file(tmp_path).read_bytes() == before


def test_a_save_onto_a_config_file_that_does_not_validate_is_400(portal, tmp_path):
    """Known limitation: a file the loader refuses cannot be the merge base."""
    _write_config(tmp_path, 'chain = ["nope"]\n')
    _exchange(portal)
    status, body = _save(portal, {"chain": ["say"]})

    assert status == 400
    assert "nope" in body["error"]  # the file's value, not the submitted one


@pytest.mark.parametrize("changes", [None, [], "chain", {}, 5])
def test_a_save_without_a_changes_object_is_400(portal, tmp_path, changes):
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    before = _config_file(tmp_path).read_bytes()

    status, body = _save(portal, changes)

    assert status == 400
    assert "error" in body
    assert _config_file(tmp_path).read_bytes() == before


# --- the fingerprint the page hands back ------------------------------


@pytest.mark.parametrize(
    "fingerprint",
    [
        None,
        {},
        "unchanged",
        {"mtime_ns": "1", "sha256": "a", "extra": True},
        {"mtime_ns": True, "sha256": "a"},
        # The wire carries `mtime_ns` as a string; a number is what a page
        # that parsed it into a double would send, and cannot be told apart.
        {"mtime_ns": 1, "sha256": "a"},
        {"mtime_ns": 1.0, "sha256": "a"},
        {"mtime_ns": -1, "sha256": "a"},
        {"mtime_ns": "-1", "sha256": "a"},
        {"mtime_ns": "1.0", "sha256": "a"},
        {"mtime_ns": "1e3", "sha256": "a"},
        {"mtime_ns": "", "sha256": "a"},
        {"mtime_ns": " 1", "sha256": "a"},
        {"mtime_ns": "\u00b2", "sha256": "a"},  # str.isdigit() says yes, int() raises
        {"mtime_ns": "1" * 21, "sha256": "a"},  # longer than any nanosecond timestamp
        {"mtime_ns": "1", "sha256": 2},
        {"mtime_ns": "1"},
        [1, "a"],
    ],
)
def test_a_save_with_a_fingerprint_of_the_wrong_shape_is_400(portal, tmp_path, fingerprint):
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    before = _config_file(tmp_path).read_bytes()

    status, _ = _post(
        portal, "/api/settings", {"changes": {"chain": ["say"]}, "fingerprint": fingerprint}
    )

    assert status == 400
    assert _config_file(tmp_path).read_bytes() == before


def test_a_save_with_no_fingerprint_at_all_is_400(portal, tmp_path):
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    before = _config_file(tmp_path).read_bytes()

    status, _ = _post(portal, "/api/settings", {"changes": {"chain": ["say"]}})

    assert status == 400
    assert _config_file(tmp_path).read_bytes() == before


def test_a_refused_fingerprint_is_never_echoed_back(portal, tmp_path):
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    status, _, body = portal.route(
        "POST",
        "/api/settings",
        _authed(portal),
        json.dumps({"changes": {"chain": ["say"]}, "fingerprint": CANARY}).encode(),
    )

    assert status == 400
    assert b"sk-can" not in body


def test_a_fingerprint_survives_the_page_and_the_save_is_accepted(portal, tmp_path):
    """`mtime_ns` has 19 digits; JavaScript's Number is exact to 16.

    This goes through the page's own arithmetic: parse as JavaScript would,
    serialise again, post it back, twice — once with the fingerprint
    `/api/state` issued and once with the one the save returned.
    """
    _write_config(tmp_path, 'chain = ["say"]\n')
    path = _config_file(tmp_path)
    stamp = 1788565401918152383  # a real nanosecond timestamp, past 2**53
    os.utime(path, ns=(stamp, stamp))
    mtime = path.stat().st_mtime_ns
    assert int(float(mtime)) != mtime, "this filesystem keeps too little precision to test with"
    _exchange(portal)

    _status, _headers, body = portal.route("GET", "/api/state", _authed(portal))
    fingerprint = _as_javascript_would(body)["fingerprint"]
    status, _, body = portal.route(
        "POST", "/api/settings", _authed(portal),
        json.dumps({"changes": {"chain": ["kokoro"]}, "fingerprint": fingerprint}).encode(),
    )
    assert status == 200, body

    fingerprint = _as_javascript_would(body)["fingerprint"]
    status, _, body = portal.route(
        "POST", "/api/settings", _authed(portal),
        json.dumps({"changes": {"chain": ["say"]}, "fingerprint": fingerprint}).encode(),
    )
    assert status == 200, body
    assert 'chain = ["say"]' in path.read_text()


def test_the_fingerprint_crosses_the_wire_with_mtime_ns_as_a_string(portal, tmp_path):
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)

    issued = _fingerprint(portal)
    assert isinstance(issued["mtime_ns"], str) and issued["mtime_ns"].isdigit()

    _status, body = _save(portal, {"chain": ["kokoro"]}, issued)
    returned = body["fingerprint"]
    assert isinstance(returned["mtime_ns"], str) and returned["mtime_ns"].isdigit()


CANARY = "«redacted:sk-…»"


# --- the write routes' shared shape -----------------------------------


#: Every route that changes something, with a body that would otherwise work.
_WRITE_ROUTES = (
    ("/api/settings", {"changes": {"chain": ["say"]}}),
    ("/api/action/clear_audio_cache", {"params": {}}),
)


@pytest.mark.parametrize(("path", "payload"), _WRITE_ROUTES)
@pytest.mark.parametrize(
    "body",
    [b"\xff\xfe", b"{", b"[]", b"5", b'"text"', b"null"],
)
def test_a_write_with_a_body_that_is_not_a_json_object_is_400(
    portal, tmp_path, fake_actions, path, payload, body
):
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    before = _config_file(tmp_path).read_bytes()

    status, _, _ = portal.route("POST", path, _authed(portal), body)

    assert status == 400
    assert _config_file(tmp_path).read_bytes() == before
    assert fake_actions.calls == []


@pytest.mark.parametrize(("path", "payload"), _WRITE_ROUTES)
@pytest.mark.parametrize(
    ("what", "method", "suffix", "headers", "expected"),
    [
        ("an empty token", "POST", "", {TOKEN_HEADER: ""}, 403),
        ("wrong token", "POST", "", {TOKEN_HEADER: "not-the-token"}, 403),
        ("rebound host", "POST", "", {"Host": "evil.example"}, 403),
        ("foreign origin", "POST", "", {"Origin": "http://evil.example"}, 403),
        ("token in the url", "POST", "?token=x", {}, 403),
        ("a GET", "GET", "", {}, 405),
    ],
)
def test_a_write_route_refuses_before_it_writes(
    portal, tmp_path, fake_actions, path, payload, what, method, suffix, headers, expected
):
    """Each refusal also has to have happened *before* the write or the action."""
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    sent = dict(payload, fingerprint=_fingerprint(portal))
    before = _config_file(tmp_path).read_bytes()

    status, sent_headers, _ = portal.route(
        method,
        path + suffix,
        _authed(portal) | headers,
        json.dumps(sent).encode(),
    )

    assert status == expected, what
    assert _config_file(tmp_path).read_bytes() == before, what
    assert fake_actions.calls == [], what
    for header, value in portal_module.SECURITY_HEADERS.items():
        assert sent_headers[header] == value, what


@pytest.mark.parametrize(("path", "payload"), _WRITE_ROUTES)
def test_a_write_route_refuses_an_oversized_body_before_it_writes(
    portal, tmp_path, fake_actions, path, payload
):
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    before = _config_file(tmp_path).read_bytes()

    status, _, _ = portal.route("POST", path, _authed(portal), b"\0" * (MAX_BODY + 1))

    assert status == 413
    assert _config_file(tmp_path).read_bytes() == before
    assert fake_actions.calls == []


def test_a_write_from_a_client_that_sends_no_origin_still_lands(portal, tmp_path):
    """curl sends no `Origin`, and DEC-016 keeps those callers working."""
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    assert "Origin" not in _authed(portal)

    status, _ = _save(portal, {"chain": ["kokoro"]})

    assert status == 200


def test_a_failed_write_never_touches_the_lockout_counter(portal, tmp_path):
    """Only `/api/session` counts an attempt."""
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    for _ in range(10):
        assert _save(portal, {"chain": ["nope"]})[0] == 400

    assert portal._code_attempts == 0
    assert portal.locked_out is False
    assert portal.route("GET", "/api/ping", _authed(portal))[0] == 200


def test_an_unplanned_exception_in_a_save_is_not_turned_into_a_400(portal, monkeypatch):
    """`_answer` catches the families it knows; a bug stays `_respond`'s fixed 500."""
    def blow_up(*args, **kwargs):
        raise TypeError("a bug, not a refusal")

    monkeypatch.setattr(portal_module.settings_registry, "apply_changes", blow_up)
    _exchange(portal)

    with pytest.raises(TypeError):
        _save(portal, {"chain": ["say"]}, wizard.ABSENT_CONFIG)


@pytest.mark.parametrize(
    ("key", "read_back"),
    [
        ("providers.kokoro.voice", lambda cfg: cfg["providers"]["kokoro"]["voice"]),
        ("stt.input_device", lambda cfg: cfg["stt"]["input_device"]),
    ],
)
def test_a_written_value_cannot_forge_a_toml_section(portal, tmp_path, key, read_back):
    """A quote and a bracket in a value are escaped, not structure."""
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    forged = 'a"] [providers.say] voice = "pwned'

    status, body = _save(portal, {key: forged})

    assert status == 200, body
    reloaded = _reload()
    assert read_back(reloaded) == forged
    assert "say" not in (reloaded.get("providers") or {})


# --- GET /api/schema and the new /api/state fields --------------------


def test_schema_is_the_registry_and_the_actions(portal, fake_actions):
    _exchange(portal)
    status, _, raw = portal.route("GET", "/api/schema", _authed(portal))

    assert status == 200
    body = json.loads(raw)
    expected = settings_registry.schema()
    assert body["groups"] == expected["groups"]
    assert body["settings"] == json.loads(json.dumps(expected["settings"]))
    assert body["actions"] == json.loads(json.dumps(fake_actions.schema()))
    assert set(body) == {"groups", "settings", "actions"}


def test_state_carries_values_notices_sources_actions_and_profile(portal, tmp_path, fake_actions):
    _write_config(
        tmp_path, 'chain = ["say"]\n[stt]\nmax_seconds = 30\n[providers.say]\nmonthly_chars = 5\n'
    )
    _exchange(portal)

    body = _body(portal.route("GET", "/api/state", _authed(portal)))

    assert body["values"] == settings_registry.values(_reload())
    assert body["values"]["stt.max_seconds"] == 30
    assert body["notices"] and all(isinstance(line, str) for line in body["notices"])
    assert any("monthly_chars" in line for line in body["notices"])
    assert body["sources"] == fake_actions.sources()
    assert body["actions"] == fake_actions.state()
    assert body["profile"] == {"detected": "standard", "names": ["small", "standard"]}
    # The fields it always had are still there.
    for kept in ("rows", "chain", "providers", "stt", "app", "config_path",
                 "config_error", "fingerprint"):
        assert kept in body


def test_state_has_a_source_for_every_choices_from_name_in_the_registry(portal, fake_actions):
    _exchange(portal)
    body = _body(portal.route("GET", "/api/state", _authed(portal)))
    wanted = {s.choices_from for s in settings_registry.SETTINGS if s.choices_from}
    assert wanted <= set(body["sources"])


def test_state_survives_an_actions_module_that_fails(portal, fake_actions, monkeypatch):
    def broken():
        raise RuntimeError("secret-ish detail")

    monkeypatch.setattr(fake_actions, "state", broken)
    monkeypatch.setattr(fake_actions, "sources", broken)
    _exchange(portal)
    status, _, raw = portal.route("GET", "/api/state", _authed(portal))

    assert status == 200
    body = json.loads(raw)
    assert body["actions"] == {} and body["sources"] == {}
    assert b"secret-ish" not in raw


def test_state_keeps_the_last_sources_while_a_listing_is_slow(portal, fake_actions, monkeypatch):
    _exchange(portal)
    first = _body(portal.route("GET", "/api/state", _authed(portal)))["sources"]
    release = threading.Event()

    def slow():
        release.wait(10)
        return {}

    monkeypatch.setattr(fake_actions, "sources", slow)
    try:
        start = time.monotonic()
        again = _body(portal.route("GET", "/api/state", _authed(portal)))["sources"]
        assert time.monotonic() - start < portal.probe_timeout + 2.0
        assert again == first
    finally:
        release.set()


# --- POST /api/action/<id> and GET /api/action/status -----------------


def _act(portal, action_id: str, params: dict | None = None):
    payload = {} if params is None else {"params": params}
    return _post(portal, f"/api/action/{action_id}", payload)


def _jobs(portal) -> list[dict]:
    status, _, raw = portal.route("GET", "/api/action/status", _authed(portal))
    assert status == 200
    return json.loads(raw)["jobs"]


def _wait_for_job(portal, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = next(j for j in _jobs(portal) if j["id"] == job_id)
        if job["state"] != "running":
            return job
        time.sleep(0.01)
    raise AssertionError("the job never finished")


def test_status_before_any_action_is_an_empty_list(portal, fake_actions):
    _exchange(portal)
    assert _jobs(portal) == []


def test_an_instant_action_runs_inline_and_answers_with_its_message(portal, fake_actions):
    _exchange(portal)
    status, body = _act(portal, "clear_audio_cache")

    assert status == 200
    assert body == {"ok": True, "job": None, "message": "Cleared 3 files."}
    assert fake_actions.calls == [("clear_audio_cache", {})]
    assert _jobs(portal) == []


def test_an_instant_action_error_is_400_with_its_message(portal, fake_actions):
    _exchange(portal)
    fake_actions.fail_with["clear_audio_cache"] = fake_actions.ActionError("Nothing to clear.")
    status, body = _act(portal, "clear_audio_cache")
    assert status == 400
    assert body == {"error": "Nothing to clear."}


def test_an_instant_action_bug_is_a_400_with_a_fixed_message(portal, fake_actions):
    _exchange(portal)
    fake_actions.fail_with["clear_audio_cache"] = ValueError("/Users/someone/private path")
    status, _, raw = portal.route(
        "POST", "/api/action/clear_audio_cache", _authed(portal), b'{"params": {}}'
    )
    assert status == 400
    assert json.loads(raw) == {"error": portal_module.ACTION_FAILED}
    assert b"private" not in raw


def test_a_job_runs_on_a_thread_and_reports_progress_then_done(portal, fake_actions):
    _exchange(portal)
    gate = fake_actions.gate("install_kokoro")

    status, body = _act(portal, "install_kokoro")
    assert status == 200
    assert body["ok"] is True and isinstance(body["job"], str) and body["message"]
    job_id = body["job"]

    assert gate.reached.wait(5)
    running = next(j for j in _jobs(portal) if j["id"] == job_id)
    assert running == {
        "id": job_id, "action": "install_kokoro", "state": "running",
        "progress": 0.5, "message": "downloading model",
    }
    gate.release.set()

    done = _wait_for_job(portal, job_id)
    assert done["state"] == "done"
    assert done["progress"] == 1.0
    assert done["message"] == "Installed Kokoro."


def test_a_second_job_of_the_same_action_is_409_busy(portal, fake_actions):
    _exchange(portal)
    gate = fake_actions.gate("install_kokoro")
    first = _act(portal, "install_kokoro")
    assert first[0] == 200
    try:
        assert gate.reached.wait(5)
        assert _act(portal, "install_kokoro") == (409, {"error": "busy"})
        # A different job is not blocked by it.
        other = _act(portal, "install_voice", {"voice": "af_sky"})
        assert other[0] == 200
    finally:
        gate.release.set()
    _wait_for_job(portal, first[1]["job"])
    # Finished, so it may run again.
    again = _act(portal, "install_kokoro")
    assert again[0] == 200
    _wait_for_job(portal, again[1]["job"])


def test_two_starts_at_once_make_one_job(portal, fake_actions):
    _exchange(portal)
    gate = fake_actions.gate("install_kokoro")
    results = []

    def start():
        results.append(_act(portal, "install_kokoro")[0])

    threads = [threading.Thread(target=start) for _ in range(8)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        assert sorted(results) == [200] + [409] * 7
    finally:
        gate.release.set()


def test_a_job_that_raises_action_error_fails_with_its_message(portal, fake_actions):
    _exchange(portal)
    fake_actions.fail_with["install_kokoro"] = fake_actions.ActionError("Download failed.")
    job_id = _act(portal, "install_kokoro")[1]["job"]
    job = _wait_for_job(portal, job_id)
    assert job["state"] == "failed"
    assert job["message"] == "Download failed."


def test_a_job_that_raises_anything_else_fails_with_a_fixed_message(portal, fake_actions):
    _exchange(portal)
    fake_actions.fail_with["install_kokoro"] = OSError("/Users/someone/.cache/secret")
    job_id = _act(portal, "install_kokoro")[1]["job"]
    job = _wait_for_job(portal, job_id)
    assert job["state"] == "failed"
    assert job["message"] == portal_module.ACTION_FAILED
    assert "secret" not in json.dumps(_jobs(portal))


def test_a_job_holds_the_idle_watchdog_off_and_gives_it_back(portal, fake_actions):
    _exchange(portal)
    gate = fake_actions.gate("install_kokoro")
    job_id = _act(portal, "install_kokoro")[1]["job"]
    assert gate.reached.wait(5)
    assert portal._suspended == 1
    gate.release.set()
    _wait_for_job(portal, job_id)
    deadline = time.monotonic() + 5
    while portal._suspended and time.monotonic() < deadline:
        time.sleep(0.01)
    assert portal._suspended == 0


def test_the_job_thread_is_a_daemon(portal, fake_actions):
    _exchange(portal)
    gate = fake_actions.gate("install_kokoro")
    _act(portal, "install_kokoro")
    try:
        assert gate.reached.wait(5)
        assert gate.thread.daemon
    finally:
        gate.release.set()


def test_closing_mid_job_names_the_job_cut_short(portal, fake_actions):
    _exchange(portal)
    assert portal.discard_partial_download() is None
    gate = fake_actions.gate("install_kokoro")
    job_id = _act(portal, "install_kokoro")[1]["job"]
    try:
        assert gate.reached.wait(5)
        assert "install_kokoro" in portal.discard_partial_download()
    finally:
        gate.release.set()
    _wait_for_job(portal, job_id)
    assert portal.discard_partial_download() is None


def test_an_unknown_action_is_400_and_never_echoes_the_id(portal, fake_actions):
    _exchange(portal)
    status, _, raw = portal.route(
        "POST", "/api/action/canary9f3", _authed(portal), b'{"params": {}}'
    )
    assert status == 400
    assert b"canary9f3" not in raw
    assert fake_actions.calls == []


@pytest.mark.parametrize(
    "path", ["/api/action/../secret", "/api/action/Clear", "/api/action/", "/api/action/a/b"]
)
def test_an_action_id_the_pattern_refuses_never_reaches_the_handler(portal, fake_actions, path):
    _exchange(portal)
    status, _, _ = portal.route("POST", path, _authed(portal), b"{}")
    assert status == 404
    assert fake_actions.calls == []


def test_status_is_get_only_and_post_to_it_is_not_an_action(portal, fake_actions):
    _exchange(portal)
    assert portal.route("POST", "/api/action/status", _authed(portal), b"{}")[0] == 405
    assert portal.route("GET", "/api/action/clear_audio_cache", _authed(portal))[0] == 405


@pytest.mark.parametrize(
    "params",
    [
        {"nope": "x"},                 # an unknown name
        {},                            # the required one missing
        {"voice": 5},                  # not a string
        {"voice": "x" * 201},          # too long
        {"voice": "a\x00b"},           # unprintable
        {"voice": "not_a_voice"},      # outside its source
    ],
)
def test_bad_params_are_400_before_anything_runs(portal, fake_actions, params):
    _exchange(portal)
    status, _, raw = portal.route(
        "POST", "/api/action/install_voice", _authed(portal),
        json.dumps({"params": params}).encode(),
    )
    assert status == 400
    assert b"x" * 201 not in raw
    assert fake_actions.calls == []
    assert _jobs(portal) == []


def test_an_enum_param_outside_its_choices_is_400(portal, fake_actions):
    _exchange(portal)
    assert _act(portal, "apply_profile", {"name": "huge"})[0] == 400
    status, body = _act(portal, "apply_profile", {"name": "small"})
    assert status == 200 and body["job"] is None
    assert fake_actions.calls == [("apply_profile", {"name": "small"})]


@pytest.mark.parametrize("params", [[], "x", 5])
def test_params_that_are_not_an_object_are_400(portal, fake_actions, params):
    _exchange(portal)
    status, _ = _post(portal, "/api/action/clear_audio_cache", {"params": params})
    assert status == 400
    assert fake_actions.calls == []


def test_a_param_from_a_source_listed_after_the_cache_is_accepted(portal, fake_actions):
    """The cached list may predate an install; it is asked once more."""
    _exchange(portal)
    portal.route("GET", "/api/state", _authed(portal))  # fills the cache
    fake_actions.voices.append({"value": "bf_new", "label": "New"})
    gate = fake_actions.gate("install_voice")
    gate.release.set()
    status, body = _act(portal, "install_voice", {"voice": "bf_new"})
    assert status == 200, body
    _wait_for_job(portal, body["job"])


# --- removed routes ---------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/chain"),
        ("POST", "/api/provider/kokoro"),
        ("POST", "/api/stt"),
        ("GET", "/api/voices/kokoro"),
        ("POST", "/api/voices/kokoro/preview"),
        ("POST", "/api/local/install/start"),
        ("GET", "/api/local/install/status"),
    ],
)
def test_the_removed_routes_are_404(portal, tmp_path, method, path):
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    before = _config_file(tmp_path).read_bytes()
    status, headers, _ = portal.route(
        method, path, _authed(portal),
        json.dumps({"order": ["kokoro"], "fingerprint": _fingerprint(portal)}).encode(),
    )
    assert status == 404
    _assert_headers(headers)
    assert _config_file(tmp_path).read_bytes() == before
    assert all(p != path for _m, p, _k in ROUTES)


@pytest.mark.parametrize("path", ["/api/auth/login", "/api/auth/test/anthropic", "/api/auth/remove/elevenlabs"])
def test_removed_credential_routes_refuse_without_echoing_secrets(portal, path, capsys):
    canary = "synthetic-key-canary\nFORGED LOG ENTRY"
    status, _, body = portal.route("POST", path, _authed(portal), json.dumps({"key": canary}).encode())
    assert status == 404
    assert b"synthetic-key-canary" not in body
    captured = capsys.readouterr()
    assert canary not in captured.out + captured.err


@pytest.mark.parametrize("path", ["/api/provider/elevenlabs", "/api/voices/openai", "/api/voices/polly/preview"])
def test_cloud_portal_engine_is_not_dispatchable(portal, path, monkeypatch):
    from vocalize import providers
    monkeypatch.setattr(providers, "get", lambda *a: pytest.fail("provider reached"))
    method = "GET" if path == "/api/voices/openai" else "POST"
    status, _, _ = portal.route(method, path, _authed(portal), b"{}")
    assert status == 404


def test_local_portal_state_has_no_credentials_or_usage(portal):
    data = _body(portal.route("GET", "/api/state", _authed(portal)))
    assert set(data["providers"]) == {"kokoro", "piper", "say"}
    assert "keys" not in data
    for entry in data["providers"].values():
        assert not {"key", "budget", "used", "exhausted"} & entry.keys()


# --- what a save and the state tell the page --------------------------


def test_a_save_over_a_commented_file_names_the_backup_it_made_once(portal, tmp_path):
    """The rewrite drops comments; the first save keeps a copy and says where."""
    _write_config(tmp_path, '# my own notes\nchain = ["say"]\n')
    _exchange(portal)

    status, body = _save(portal, {"stt.max_seconds": 60})

    assert status == 200, body
    backup = wizard.backup_path(_config_file(tmp_path))
    assert body["backup"] == str(backup)
    assert backup.read_text() == '# my own notes\nchain = ["say"]\n'
    status, body = _save(portal, {"stt.max_seconds": 61})
    assert status == 200 and body["backup"] is None


def test_a_save_over_a_file_with_no_comments_has_a_null_backup(portal, tmp_path):
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    status, body = _save(portal, {"stt.max_seconds": 60})
    assert status == 200 and body["backup"] is None


_OVERRIDE_VALUES = {
    "VOCALIZE_CHAIN": "piper ,  say",
    "VOCALIZE_VOICE": "zz_private_voice_name",
    "VOCALIZE_SPEED": "1.37",
    "VOCALIZE_OVERFLOW": " NeVeR ",
    "VOCALIZE_MAX_CHARS": "98765",
}


def test_state_names_the_variables_that_override_a_setting(portal, tmp_path, monkeypatch):
    """M6: the page showed the file's value with no word that a variable
    in the environment wins over it for commands run from that shell."""
    for name, value in _OVERRIDE_VALUES.items():
        monkeypatch.setenv(name, value)
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)

    body = _body(portal.route("GET", "/api/state", _authed(portal)))

    # Voice and speed go to the first engine of the chain in force.
    assert body["overrides"] == {
        "chain": "VOCALIZE_CHAIN",
        "providers.piper.voice": "VOCALIZE_VOICE",
        "providers.piper.speed": "VOCALIZE_SPEED",
        "overflow": "VOCALIZE_OVERFLOW",
        "max_chars": "VOCALIZE_MAX_CHARS",
    }
    assert all(settings_registry.by_key(key) for key in body["overrides"])


def test_state_has_no_overrides_when_no_variable_is_set(portal, monkeypatch):
    for name in (*_OVERRIDE_VALUES, "VOCALIZE_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOCALIZE_VOICE", "   ")  # blank is not set
    _exchange(portal)
    assert _body(portal.route("GET", "/api/state", _authed(portal)))["overrides"] == {}


def test_an_overriding_variables_value_never_appears_in_any_reply(portal, tmp_path, monkeypatch):
    """Names only: the environment's values stay in the server."""
    for name, value in _OVERRIDE_VALUES.items():
        monkeypatch.setenv(name, value)
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)

    replies = [portal.route("GET", "/api/state", _authed(portal))[2],
               portal.route("GET", "/api/schema", _authed(portal))[2]]
    fingerprint = _body(portal.route("GET", "/api/state", _authed(portal)))["fingerprint"]
    status, _, saved = portal.route("POST", "/api/settings", _authed(portal), json.dumps(
        {"changes": {"stt.max_seconds": 60}, "fingerprint": fingerprint}).encode())
    assert status == 200
    replies.append(saved)
    for reply in replies:
        text = reply.decode("utf-8")
        for value in _OVERRIDE_VALUES.values():
            assert value.strip() not in text and json.dumps(value)[1:-1] not in text


def test_a_save_says_when_its_backup_holds_retired_settings(portal, tmp_path, monkeypatch):
    """L5: the writer says whether the copy kept settings it removed; the
    reply passes that on, and a writer without the attribute means no."""
    _write_config(tmp_path, '# notes\nchain = ["say"]\n')
    _exchange(portal)
    real = wizard.write_config_if_unchanged

    def flagged(*args, **kwargs):
        result = real(*args, **kwargs)
        marked = type("Written", (tuple,), {})(result)
        marked.backup = getattr(result, "backup", None)
        marked.backup_has_retired = True
        return marked

    monkeypatch.setattr(wizard, "write_config_if_unchanged", flagged)
    status, body = _save(portal, {"stt.max_seconds": 60})
    assert status == 200 and body["backup_has_retired"] is True
    monkeypatch.setattr(wizard, "write_config_if_unchanged", real)
    status, body = _save(portal, {"stt.max_seconds": 61})
    assert status == 200 and body["backup_has_retired"] is False


def test_state_carries_why_a_source_is_empty(portal, fake_actions, monkeypatch):
    seen = []

    def notes(listed):
        seen.append(listed)
        return {"input_devices": "Install a dictation engine to list microphones.\nmore"}

    monkeypatch.setattr(fake_actions, "source_notes", notes, raising=False)
    _exchange(portal)
    body = _body(portal.route("GET", "/api/state", _authed(portal)))

    assert body["source_notes"] == {
        "input_devices": "Install a dictation engine to list microphones."
    }
    assert seen == [body["sources"]], "the notes are about the sources the page was sent"


def test_state_source_notes_are_empty_when_the_module_has_none(portal, fake_actions):
    _exchange(portal)
    assert _body(portal.route("GET", "/api/state", _authed(portal)))["source_notes"] == {}


# --- end to end, with the real actions, profiles and registry -----------


def test_the_real_modules_end_to_end(monkeypatch, tmp_path):
    """The server over the real `vocalize.actions`, `vocalize.profiles` and
    `vocalize.settings_registry`, in a temporary HOME. Only process calls are
    faked: nothing installs, plays, or lists devices through a slow program."""
    import subprocess

    import install_hook

    from vocalize import dictate as dictate_module
    from vocalize import integrate, tts
    from vocalize import local as local_module
    from vocalize.local import locks

    monkeypatch.setitem(sys.modules, "vocalize.actions", real_actions)
    monkeypatch.setitem(sys.modules, "vocalize.profiles", real_profiles)
    home = tmp_path / "home"
    runs, popens = [], []

    def run(argv, *args, **kwargs):
        runs.append(list(argv))
        return subprocess.CompletedProcess(argv, 1, "", "")

    def popen(argv, *args, **kwargs):
        popens.append(list(argv))
        raise OSError("no process may start in this test")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(locks, "ENVS_DIR", home / ".cache" / "vocalize" / "envs")
    monkeypatch.setattr(tts, "DEFAULT_CACHE_DIR", home / ".cache" / "vocalize")
    monkeypatch.setattr(dictate_module, "CACHE_DIR", home / ".cache" / "vocalize")
    monkeypatch.setattr(integrate, "SERVICES_DIR", home / "Library" / "Services")
    monkeypatch.setattr(integrate, "SKILL_DEST", home / ".claude" / "skills" / "speak" / "SKILL.md")
    monkeypatch.setattr(install_hook, "SETTINGS_PATH", home / ".claude" / "settings.json")
    monkeypatch.setattr(real_actions, "_hook_module", lambda: install_hook)
    monkeypatch.setattr(local_module, "uv_path", lambda: None)

    portal = Portal(probe_timeout=10)
    portal.port = PORT
    _exchange(portal)

    status, _, raw = portal.route("GET", "/api/schema", _authed(portal))
    assert status == 200
    schema = json.loads(raw)
    assert len(schema["settings"]) == len(settings_registry.SETTINGS)
    assert {a["id"] for a in schema["actions"]} == {a.id for a in real_actions.ACTIONS}

    state = _body(portal.route("GET", "/api/state", _authed(portal)))
    wanted = {s["choices_from"] for s in schema["settings"] if s["choices_from"]}
    wanted |= {p["choices_from"] for a in schema["actions"] for p in a["params"]
               if p["choices_from"]}
    assert wanted <= set(state["sources"]), wanted - set(state["sources"])
    assert set(state["actions"]) == {a["id"] for a in schema["actions"]}
    for entry in state["actions"].values():
        assert set(entry) == {"available", "reason", "status"}
    assert set(state["values"]) == {s["key"] for s in schema["settings"]}
    # The empty transcription model is named, and the empty microphone list says why.
    labels = {e["value"]: e["label"] for e in state["sources"]["whisper_models"]}
    assert labels[""] == "Use the dictation model"
    assert state["sources"]["input_devices"] == []
    assert "Install a dictation engine" in state["source_notes"]["input_devices"]
    assert state["profile"]["detected"] in state["profile"]["names"]

    first = state["fingerprint"]
    status, body = _save(portal, {"stt.max_seconds": 90, "stt.paste": True}, first)
    assert status == 200, body
    assert body["values"]["stt.max_seconds"] == 90 and body["backup"] is None

    status, body = _save(portal, {"stt.max_seconds": 91}, first)
    assert status == 409 and body["error"] == "changed"

    status, body = _save(portal, {"stt.max_seconds": 10**9}, _fingerprint(portal))
    assert status == 400 and set(body["errors"]) == {"stt.max_seconds"}

    status, body = _act(portal, "apply_profile", {"profile": "studio"})
    assert status == 200 and body["job"] is None, body
    assert body["message"].startswith("Applied the studio profile.")
    after = _body(portal.route("GET", "/api/state", _authed(portal)))
    for key, value in real_profiles.changes("studio").items():
        assert after["values"][key] == value, key
    assert after["values"]["stt.max_seconds"] == 90, "the earlier save survives the profile"
    assert after["fingerprint"] != first

    assert popens == [], popens
    assert not any("install" in " ".join(argv) or "afplay" in " ".join(argv)
                   for argv in runs), runs
