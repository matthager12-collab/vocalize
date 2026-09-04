"""Tests for vocalize.portal — the auth bootstrap and the read-only state.

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
import socket
import threading
import time
from typing import ClassVar

import pytest

import vocalize.readiness as readiness_module
from vocalize import portal as portal_module
from vocalize import wizard
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
        "POST", "/api/chain", _headers(), json.dumps({"token": token}).encode()
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
    "Content-Security-Policy": "default-src 'self'; media-src 'self' blob:; frame-ancestors 'none'",
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
        portal.route("POST", "/api/chain", _authed(portal), b"x" * (MAX_BODY + 1)),
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


def test_state_reports_rows_chain_settings_budgets_keys_and_stt(portal, tmp_path):
    _write_config(
        tmp_path,
        'chain = ["say", "kokoro"]\n'
        "[providers.elevenlabs]\nmonthly_chars = 5000\nvoice = \"abc\"\n"
        '[stt]\nmodel = "base.en"\n',
    )
    _exchange(portal)
    payload = _body(portal.route("GET", "/api/state", _authed(portal)))

    assert payload["chain"] == ["say", "kokoro"]
    assert payload["chain_source"] == "config file"  # the tier, not just a string
    assert {r["name"] for r in payload["rows"]} >= {"say", "kokoro"}
    assert all({"name", "state", "detail", "action"} == set(r) for r in payload["rows"])
    assert payload["providers"]["elevenlabs"]["budget"] == 5000
    assert payload["providers"]["elevenlabs"]["settings"]["voice"] == "abc"
    assert payload["providers"]["say"]["in_chain"] is True
    assert payload["providers"]["openai"]["in_chain"] is False
    assert payload["stt"]["model"] == "base.en"
    assert payload["stt"]["max_seconds"] == 120  # defaults filled in
    assert payload["config_error"] is None
    # The page edits this exact file, so the path is the payload, not decoration.
    assert payload["config_path"] == str(
        tmp_path / "config-home" / "vocalize" / "config.toml"
    )


def test_state_reports_the_key_source_without_the_key(portal, monkeypatch, tmp_path):
    secret = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
    monkeypatch.setenv("ELEVENLABS_API_KEY", secret)
    _write_config(tmp_path, 'chain = ["elevenlabs"]\n')
    _exchange(portal)
    status, _, raw = portal.route("GET", "/api/state", _authed(portal))

    assert status == 200
    assert secret.encode() not in raw
    key = json.loads(raw)["providers"]["elevenlabs"]["key"]
    assert key["source"] == "environment"
    assert key["masked"] == "sk-a…"


def test_state_reports_no_key_mechanism_for_local_providers(portal):
    _exchange(portal)
    payload = _body(portal.route("GET", "/api/state", _authed(portal)))
    assert payload["providers"]["say"]["key"] == {"source": "not applicable", "masked": None}
    assert payload["providers"]["kokoro"]["key"]["source"] == "not applicable"


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


def test_state_blocked_key_probe_reports_checking_and_returns(portal, monkeypatch):
    """The same guarantee for the key previews, which read the keychain."""
    release = threading.Event()
    started = []

    def wedged(name):
        started.append(name)
        release.wait(10)
        return readiness_module.Row(f"key {name}", "keychain", "sk-a…", "")

    monkeypatch.setattr(portal_module, "_key_row", wedged)
    _exchange(portal)
    try:
        payload = _body(portal.route("GET", "/api/state", _authed(portal)))
        for _ in range(9):
            portal.route("GET", "/api/state", _authed(portal))

        assert payload["providers"]["elevenlabs"]["key"] == {
            "source": "checking",
            "masked": None,
        }
        # One thread per key-carrying provider, however often the page polls.
        assert sorted(started) == ["elevenlabs", "google", "openai"]
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
            b"POST /api/chain HTTP/1.1\r\nHost: evil.example\r\n"
            b"Content-Length: 999999\r\n\r\n",
        )
        assert b" 403 " in raw
        assert b"loopback" in raw
    finally:
        served.stop()


def test_state_survives_a_malformed_ledger_entry(portal, monkeypatch):
    """One broken provider is that provider's error, not a dead page."""

    def broken(name):
        if name == "elevenlabs":
            raise TypeError("'list' object is not a mapping")
        return 0, False

    monkeypatch.setattr(portal_module.ledger, "status", broken)
    _exchange(portal)
    status, _, raw = portal.route("GET", "/api/state", _authed(portal))
    payload = json.loads(raw)

    assert status == 200
    assert payload["providers"]["elevenlabs"]["error"]
    assert payload["providers"]["say"]["error"] is None  # the rest still render


def test_state_failed_key_probe_is_reported_not_left_checking(portal, monkeypatch):
    """A probe that raised is finished; reporting it as "checking" spins."""

    def broken(name):
        raise RuntimeError("keychain is on fire")

    monkeypatch.setattr(portal_module, "_key_row", broken)
    _exchange(portal)
    payload = _body(portal.route("GET", "/api/state", _authed(portal)))
    assert payload["providers"]["elevenlabs"]["key"] == {"source": "error", "masked": None}


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
            b"POST /api/chain HTTP/1.1\r\nHost: "
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


def test_state_reports_a_keychain_key_source_masked(portal, fake_keychain, monkeypatch, tmp_path):
    """The keychain branch of the preview, not only the environment one."""
    secret = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    auth = portal_module.auth
    fake_keychain[(auth.SERVICE, auth.PROVIDER_USERNAMES["elevenlabs"])] = secret
    _write_config(tmp_path, 'chain = ["elevenlabs"]\n')
    _exchange(portal)

    status, _, raw = portal.route("GET", "/api/state", _authed(portal))

    assert status == 200
    assert secret.encode() not in raw
    assert json.loads(raw)["providers"]["elevenlabs"]["key"] == {
        "source": "keychain",
        "masked": "sk-a…",
    }


def test_state_renders_a_config_value_json_cannot_serialize(portal, tmp_path):
    """TOML has types json does not, and `[providers.*]` values pass unchecked.

    A bare date in a hand-edited table used to raise out of `json.dumps` —
    taking down the page whose whole job is to fix that config.
    """
    _write_config(tmp_path, 'chain = ["say"]\n[providers.say]\nvoice = 1979-05-27\n')
    _exchange(portal)

    status, _, raw = portal.route("GET", "/api/state", _authed(portal))

    assert status == 200
    assert json.loads(raw)["providers"]["say"]["settings"]["voice"] == "1979-05-27"


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


def test_state_wedged_key_probes_are_joined_against_one_deadline(monkeypatch):
    """Three blocked keychain reads cost one timeout per poll, not three."""
    blocked = threading.Event()  # released in the finally: three daemon threads
    monkeypatch.setattr(portal_module, "_key_row", lambda name: blocked.wait())
    made = Portal(probe_timeout=0.5)
    made.port = PORT
    _exchange(made)

    try:
        start = time.monotonic()
        payload = _body(made.route("GET", "/api/state", _authed(made)))
        elapsed = time.monotonic() - start

        assert payload["providers"]["elevenlabs"]["key"]["source"] == "checking"
        assert elapsed < 1.0, f"three wedged key probes took {elapsed:.2f}s of a 0.5s budget"
    finally:
        blocked.set()


def test_state_two_unrelated_provider_failures_are_both_reported(portal, monkeypatch):
    """One error slot, and `or` used to keep the first and drop the second."""

    def broken_settings(name, *args, **kwargs):
        raise TypeError("settings blew up")

    def broken_ledger(name):
        raise ValueError("ledger blew up")

    monkeypatch.setattr(portal_module.config, "resolve_provider_settings", broken_settings)
    monkeypatch.setattr(portal_module.ledger, "status", broken_ledger)
    _exchange(portal)

    payload = _body(portal.route("GET", "/api/state", _authed(portal)))

    error = payload["providers"]["elevenlabs"]["error"]
    assert "TypeError" in error and "ValueError" in error


# --- writes: the compare-and-swap fingerprint --------------------------


def _config_file(tmp_path):
    return tmp_path / "config-home" / "vocalize" / "config.toml"


def _fingerprint(portal) -> object:
    """The fingerprint `/api/state` hands the page. The page has no other."""
    return _body(portal.route("GET", "/api/state", _authed(portal)))["fingerprint"]


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
    assert _fingerprint(portal) == wizard.fingerprint_config(_config_file(tmp_path))


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

    assert issued == before
    assert issued != wizard.fingerprint_config(_config_file(tmp_path))


def test_state_survives_a_config_file_it_cannot_fingerprint(portal, monkeypatch):
    """One guarded section, not a dead route — as everywhere else in _state."""
    def unreadable(path):
        raise portal_module.config.ConfigError("Could not read config file")

    monkeypatch.setattr(portal_module.wizard, "fingerprint_config", unreadable)
    _exchange(portal)
    status, _, body = portal.route("GET", "/api/state", _authed(portal))

    assert status == 200
    assert json.loads(body)["fingerprint"] is None


# --- POST /api/chain --------------------------------------------------


def test_writing_the_chain_saves_it_and_returns_a_fresh_fingerprint(portal, tmp_path):
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    fingerprint = _fingerprint(portal)

    status, body = _post(
        portal, "/api/chain", {"order": ["kokoro", "say"], "fingerprint": fingerprint}
    )

    assert status == 200
    path = _config_file(tmp_path)
    assert 'chain = ["kokoro", "say"]' in path.read_text()
    # The page writes again without polling, so the fingerprint has to be
    # the one the file now holds.
    assert body["fingerprint"] == wizard.fingerprint_config(path)


def test_writing_the_chain_keeps_every_other_key_and_table(portal, tmp_path):
    """The merge base is a fresh read, not the page's view of the file."""
    _write_config(
        tmp_path,
        'chain = ["say"]\nspeed = 1.1\n'
        '[stt]\nmodel = "base.en"\n'
        '[providers.elevenlabs]\nvoice = "abc"\nmonthly_chars = 5000\n',
    )
    _exchange(portal)
    status, _ = _post(
        portal, "/api/chain", {"order": ["kokoro"], "fingerprint": _fingerprint(portal)}
    )

    assert status == 200
    text = _config_file(tmp_path).read_text()
    assert "speed = 1.1" in text
    assert '[stt]' in text and 'model = "base.en"' in text
    assert "[providers.elevenlabs]" in text
    assert 'voice = "abc"' in text and "monthly_chars = 5000" in text


def test_writing_with_a_stale_fingerprint_is_409_and_changes_nothing(portal, tmp_path):
    """The acceptance criterion: changed on disk between read and write."""
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    fingerprint = _fingerprint(portal)
    _write_config(tmp_path, 'chain = ["kokoro"]\n')  # somebody else wrote
    before = _config_file(tmp_path).read_bytes()

    status, body = _post(
        portal, "/api/chain", {"order": ["say"], "fingerprint": fingerprint}
    )

    assert status == 409
    assert body["error"] == wizard.CONFIG_CHANGED
    assert _config_file(tmp_path).read_bytes() == before


def test_writing_with_an_absent_fingerprint_creates_the_file(portal, tmp_path):
    _exchange(portal)
    assert not _config_file(tmp_path).exists()

    status, _ = _post(
        portal,
        "/api/chain",
        {"order": ["say"], "fingerprint": wizard.ABSENT_CONFIG},
    )

    assert status == 200
    assert 'chain = ["say"]' in _config_file(tmp_path).read_text()


def test_writing_under_an_absent_fingerprint_over_a_file_that_appeared_is_409(portal, tmp_path):
    """The acceptance criterion: created underneath an `absent` fingerprint."""
    _exchange(portal)
    fingerprint = wizard.ABSENT_CONFIG
    _write_config(tmp_path, 'chain = ["kokoro"]\n')  # it appeared underneath
    before = _config_file(tmp_path).read_bytes()

    status, body = _post(
        portal, "/api/chain", {"order": ["say"], "fingerprint": fingerprint}
    )

    assert status == 409
    assert body["error"] == wizard.CONFIG_CHANGED
    assert _config_file(tmp_path).read_bytes() == before


@pytest.mark.parametrize(
    "order",
    [[], ["say", "say"], ["nope"], ["say", 3], "say", {"say": True}, None],
)
def test_writing_a_bad_chain_is_400_and_changes_nothing(portal, tmp_path, order):
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    fingerprint = _fingerprint(portal)
    before = _config_file(tmp_path).read_bytes()

    status, _ = _post(portal, "/api/chain", {"order": order, "fingerprint": fingerprint})

    assert status == 400
    assert _config_file(tmp_path).read_bytes() == before


def test_writing_an_unknown_provider_in_the_chain_uses_the_cli_wording(portal, tmp_path):
    """Not a paraphrase: `config._validate_chain`'s own text, verbatim."""
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    status, body = _post(
        portal, "/api/chain", {"order": ["nope"], "fingerprint": _fingerprint(portal)}
    )

    expected = f"Unknown provider 'nope' in 'chain' in {_config_file(tmp_path)}. "
    assert status == 400
    assert body["error"].startswith(expected)


def test_a_write_onto_a_config_file_that_does_not_validate_is_400(portal, tmp_path):
    """Known limitation, recorded rather than fixed.

    Every write handler re-reads the file as its merge base, so a file the
    parser refuses cannot be repaired through the portal — `/api/state`
    renders it (that is what `config_error` is for) and every write then
    answers 400 with a validator message about a value the user did not
    submit. Refusing to merge into a file we could not parse is right; the
    confusing part is the message, and run 9's page has `config_error` to
    tell the user which it is.
    """
    _write_config(tmp_path, 'chain = ["nope"]\n')
    _exchange(portal)
    status, body = _post(
        portal, "/api/chain", {"order": ["say"], "fingerprint": _fingerprint(portal)}
    )

    assert status == 400
    assert "nope" in body["error"]  # the file's value, not the submitted one


# --- the fingerprint the page hands back ------------------------------


@pytest.mark.parametrize(
    "fingerprint",
    [
        None,
        {},
        "unchanged",
        {"mtime_ns": 1, "sha256": "a", "extra": True},
        {"mtime_ns": True, "sha256": "a"},
        {"mtime_ns": "1", "sha256": "a"},
        {"mtime_ns": 1, "sha256": 2},
        {"mtime_ns": 1},
        [1, "a"],
    ],
)
def test_a_write_with_a_fingerprint_of_the_wrong_shape_is_400(portal, tmp_path, fingerprint):
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    before = _config_file(tmp_path).read_bytes()

    status, _ = _post(
        portal, "/api/chain", {"order": ["say"], "fingerprint": fingerprint}
    )

    assert status == 400
    assert _config_file(tmp_path).read_bytes() == before


def test_a_write_with_no_fingerprint_at_all_is_400(portal, tmp_path):
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    before = _config_file(tmp_path).read_bytes()

    status, _ = _post(portal, "/api/chain", {"order": ["say"]})

    assert status == 400
    assert _config_file(tmp_path).read_bytes() == before


def test_a_refused_fingerprint_is_never_echoed_back(portal, tmp_path):
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    status, _, body = portal.route(
        "POST",
        "/api/chain",
        _authed(portal),
        json.dumps({"order": ["say"], "fingerprint": "sk-canary-9f3"}).encode(),
    )

    assert status == 400
    assert b"sk-canary" not in body


# --- POST /api/provider/<name> ----------------------------------------


def test_writing_one_provider_setting_keeps_the_rest_of_the_file(portal, tmp_path):
    _write_config(
        tmp_path,
        'chain = ["say"]\n'
        '[providers.elevenlabs]\nvoice = "abc"\nmonthly_chars = 5000\n'
        '[providers.openai]\nvoice = "nova"\n',
    )
    _exchange(portal)
    status, _ = _post(
        portal,
        "/api/provider/elevenlabs",
        {"settings": {"voice": "xyz"}, "fingerprint": _fingerprint(portal)},
    )

    assert status == 200
    text = _config_file(tmp_path).read_text()
    assert 'voice = "xyz"' in text
    assert "monthly_chars = 5000" in text  # the provider's other keys
    assert 'voice = "nova"' in text  # and every other provider's table
    assert 'chain = ["say"]' in text


def test_writing_null_clears_a_provider_key_and_then_its_table(portal, tmp_path):
    """A cleared key is removed, never written as the string "None"."""
    _write_config(tmp_path, '[providers.elevenlabs]\nvoice = "abc"\nmodel = "m"\n')
    _exchange(portal)

    status, _ = _post(
        portal,
        "/api/provider/elevenlabs",
        {"settings": {"voice": None}, "fingerprint": _fingerprint(portal)},
    )
    assert status == 200
    text = _config_file(tmp_path).read_text()
    assert "voice" not in text and "None" not in text
    assert 'model = "m"' in text

    status, _ = _post(
        portal,
        "/api/provider/elevenlabs",
        {"settings": {"model": None}, "fingerprint": _fingerprint(portal)},
    )
    assert status == 200
    # The last key went, so the header went, so `providers` went.
    text = _config_file(tmp_path).read_text()
    assert "[providers" not in text and "providers" not in text


def test_writing_an_unknown_provider_key_is_400_and_saves_nothing(portal, tmp_path):
    """The CLI only warns on stderr, which a page cannot see."""
    _write_config(tmp_path, '[providers.elevenlabs]\nvoice = "abc"\n')
    _exchange(portal)
    before = _config_file(tmp_path).read_bytes()

    status, body = _post(
        portal,
        "/api/provider/elevenlabs",
        {"settings": {"voise": "typo"}, "fingerprint": _fingerprint(portal)},
    )

    assert status == 400
    assert "voise" in body["error"]
    assert _config_file(tmp_path).read_bytes() == before


def test_writing_a_provider_speed_out_of_range_uses_the_cli_wording(portal, tmp_path):
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    status, body = _post(
        portal,
        "/api/provider/elevenlabs",
        {"settings": {"speed": 99}, "fingerprint": _fingerprint(portal)},
    )

    assert status == 400
    assert body["error"] == (
        f"Invalid speed 99.0 from 'speed' in [providers.elevenlabs] in "
        f"{_config_file(tmp_path)}: must be between 0.7 and 1.2."
    )


@pytest.mark.parametrize("budget", [-1, 1.5, True, "1000"])
def test_writing_a_bad_monthly_chars_uses_the_cli_wording(portal, tmp_path, budget):
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    status, body = _post(
        portal,
        "/api/provider/elevenlabs",
        {"settings": {"monthly_chars": budget}, "fingerprint": _fingerprint(portal)},
    )

    assert status == 400
    assert body["error"].startswith("Invalid monthly_chars ")
    assert "non-negative integer" in body["error"]


@pytest.mark.parametrize("voice", [12345, ["a"], "x" * 201, "a\x00b", True])
def test_writing_an_unusable_provider_value_is_400_without_echoing_it(
    portal, tmp_path, voice
):
    """Stricter than a hand edit, deliberately: nothing else checks these."""
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    before = _config_file(tmp_path).read_bytes()

    status, _, body = portal.route(
        "POST",
        "/api/provider/elevenlabs",
        _authed(portal),
        json.dumps({"settings": {"voice": voice}, "fingerprint": _fingerprint(portal)}).encode(),
    )

    assert status == 400
    assert b"x" * 201 not in body
    assert _config_file(tmp_path).read_bytes() == before


def test_writing_to_an_unknown_provider_is_404_and_never_echoes_the_name(portal):
    _exchange(portal)
    status, _, body = portal.route(
        "POST",
        "/api/provider/canary9f3",
        _authed(portal),
        json.dumps({"settings": {}, "fingerprint": wizard.ABSENT_CONFIG}).encode(),
    )

    assert status == 404
    assert b"canary9f3" not in body


@pytest.mark.parametrize("path", ["/api/provider/../secret", "/api/provider/Say"])
def test_a_provider_name_the_pattern_refuses_never_reaches_the_handler(portal, path):
    _exchange(portal)
    status, _, _ = portal.route("POST", path, _authed(portal), b"{}")
    assert status in (404, 405)


# --- POST /api/stt ----------------------------------------------------


def test_writing_stt_settings_keeps_the_rest_of_the_file(portal, tmp_path):
    _write_config(tmp_path, 'chain = ["say"]\n[stt]\nmax_seconds = 30\n')
    _exchange(portal)
    status, _ = _post(
        portal,
        "/api/stt",
        {
            "settings": {"model": "base.en", "input_device": "MacBook Pro Microphone"},
            "fingerprint": _fingerprint(portal),
        },
    )

    assert status == 200
    text = _config_file(tmp_path).read_text()
    assert 'model = "base.en"' in text
    assert 'input_device = "MacBook Pro Microphone"' in text
    assert "max_seconds = 30" in text
    assert 'chain = ["say"]' in text


def test_writing_null_clears_an_stt_key_and_then_the_table(portal, tmp_path):
    _write_config(tmp_path, '[stt]\nmax_seconds = 30\n')
    _exchange(portal)
    status, _ = _post(
        portal,
        "/api/stt",
        {"settings": {"max_seconds": None}, "fingerprint": _fingerprint(portal)},
    )

    assert status == 200
    assert "stt" not in _config_file(tmp_path).read_text()


def test_writing_a_bad_stt_model_uses_the_cli_wording(portal, tmp_path):
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    status, body = _post(
        portal,
        "/api/stt",
        {"settings": {"model": "nope"}, "fingerprint": _fingerprint(portal)},
    )

    assert status == 400
    assert body["error"].startswith(f"Invalid stt.model 'nope' in {_config_file(tmp_path)}.")


def test_the_stt_pairing_rule_is_checked_against_the_merged_table(portal, tmp_path):
    """Half the rule can already be in the file, so the merge is validated.

    A multilingual model and `language = "fr"` are on disk — a table that
    validates on its own — and only `model` is submitted. Validating the
    submitted keys alone passes, and would save an English-only model
    against a French language for dictation to discover later.
    """
    _write_config(tmp_path, '[stt]\nmodel = "large-v3-turbo-q5_0"\nlanguage = "fr"\n')
    _exchange(portal)
    before = _config_file(tmp_path).read_bytes()

    status, body = _post(
        portal,
        "/api/stt",
        {"settings": {"model": "small.en"}, "fingerprint": _fingerprint(portal)},
    )

    assert status == 400
    assert "small.en" in body["error"]
    assert _config_file(tmp_path).read_bytes() == before


@pytest.mark.parametrize(
    "settings",
    [
        {"cues": "shout"},
        {"cleanup": "yes"},
        {"max_seconds": 0},
        {"max_seconds": 9999},
        {"max_seconds": True},
        {"input_device": "-rf"},
        {"input_device": 3},
        {"modle": "base.en"},
    ],
)
def test_writing_a_bad_stt_value_is_400_and_changes_nothing(portal, tmp_path, settings):
    _write_config(tmp_path, '[stt]\nmax_seconds = 30\n')
    _exchange(portal)
    before = _config_file(tmp_path).read_bytes()

    status, _ = _post(
        portal, "/api/stt", {"settings": settings, "fingerprint": _fingerprint(portal)}
    )

    assert status == 400
    assert _config_file(tmp_path).read_bytes() == before


# --- POST /api/auth/login ---------------------------------------------


CANARY = "sk-canary-9f3aaaaaaaaaaaaaaaaaaaaaaaaaaa"


@pytest.fixture
def no_network_validate(monkeypatch):
    """`auth.login` without the API call it makes to check a key."""
    monkeypatch.setattr(portal_module.auth, "validate_key", lambda *a, **k: None)


def test_the_login_response_never_contains_the_key(
    portal, fake_keychain, no_network_validate
):
    _exchange(portal)
    status, headers, body = portal.route(
        "POST",
        "/api/auth/login",
        _authed(portal),
        json.dumps({"provider": "elevenlabs", "key": CANARY}).encode(),
    )

    assert status == 200
    assert CANARY.encode() not in body
    assert CANARY not in json.dumps(headers)
    assert CANARY not in json.dumps(json.loads(body))
    # ...and the test is not green because nothing happened:
    assert CANARY in fake_keychain.values()


def test_a_rejected_key_is_scrubbed_out_of_the_error(portal, monkeypatch):
    """Defence in depth over `auth.login`'s own scrub, for a second author."""
    def leaky(key, provider):
        raise portal_module.VocalizeError(f"HTTP 401 for key {key}")

    monkeypatch.setattr(portal_module.auth, "login", leaky)
    _exchange(portal)
    status, _, body = portal.route(
        "POST",
        "/api/auth/login",
        _authed(portal),
        json.dumps({"provider": "elevenlabs", "key": CANARY}).encode(),
    )

    assert status == 400
    assert CANARY.encode() not in body
    assert b"[key]" in body


def test_the_login_route_never_logs_the_key(capsys, fake_keychain, monkeypatch):
    """Over a real socket: http.server's own logging is the thing at risk."""
    monkeypatch.setattr(portal_module.auth, "validate_key", lambda *a, **k: None)
    served = Portal(idle_timeout=300)
    served.start()
    try:
        token = served._token
        body = json.dumps({"provider": "elevenlabs", "key": CANARY}).encode()
        raw = _raw(
            served.port,
            b"POST /api/auth/login HTTP/1.1\r\nHost: "
            + served.origin.encode()
            + b"\r\n"
            + TOKEN_HEADER.encode()
            + b": "
            + token.encode()
            + b"\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\n\r\n"
            + body,
        )
        assert b" 200 " in raw
        assert b"sk-canary" not in raw
        captured = capsys.readouterr()
        assert "sk-canary" not in captured.out
        assert "sk-canary" not in captured.err
    finally:
        served.stop()


@pytest.mark.parametrize(
    "payload",
    [
        {"provider": "polly", "key": CANARY},
        {"provider": "say", "key": CANARY},
        {"provider": "kokoro", "key": CANARY},
        {"provider": "elevenlabs", "key": ""},
        {"provider": "elevenlabs", "key": 7},
        {"provider": "elevenlabs", "key": None},
        {"provider": "elevenlabs"},
    ],
)
def test_a_login_that_cannot_store_a_key_is_400_and_stores_nothing(
    portal, fake_keychain, no_network_validate, payload
):
    _exchange(portal)
    status, _ = _post(portal, "/api/auth/login", payload)

    assert status == 400
    assert not fake_keychain


def test_a_login_for_an_unknown_provider_is_404_without_reflecting_it(
    portal, fake_keychain
):
    """The name comes from the body here, where a mis-wired form could
    put the key itself in it."""
    _exchange(portal)
    status, _, body = portal.route(
        "POST",
        "/api/auth/login",
        _authed(portal),
        json.dumps({"provider": CANARY, "key": CANARY}).encode(),
    )

    assert status == 404
    assert b"sk-canary" not in body
    assert not fake_keychain


def test_the_login_route_writes_no_config_file(portal, tmp_path, fake_keychain, no_network_validate):
    """It stores a key. It has no fingerprint and touches no config."""
    _exchange(portal)
    status, _ = _post(portal, "/api/auth/login", {"provider": "elevenlabs", "key": CANARY})

    assert status == 200
    assert not _config_file(tmp_path).exists()


# --- the write routes' shared shape -----------------------------------


#: Every write route this run built, with a body that would otherwise work.
_WRITE_ROUTES = (
    ("/api/chain", {"order": ["say"]}),
    ("/api/provider/elevenlabs", {"settings": {"voice": "abc"}}),
    ("/api/stt", {"settings": {"model": "base.en"}}),
    ("/api/auth/login", {"provider": "elevenlabs", "key": CANARY}),
)


@pytest.mark.parametrize(("path", "payload"), _WRITE_ROUTES)
@pytest.mark.parametrize(
    "body",
    [b"\xff\xfe", b"{", b"[]", b"5", b'"text"', b"null"],
)
def test_a_write_with_a_body_that_is_not_a_json_object_is_400(
    portal, tmp_path, path, payload, body
):
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    before = _config_file(tmp_path).read_bytes()

    status, _, _ = portal.route("POST", path, _authed(portal), body)

    assert status == 400
    assert _config_file(tmp_path).read_bytes() == before


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
    portal, tmp_path, path, payload, what, method, suffix, headers, expected
):
    """Each refusal also has to have happened *before* the write."""
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
    for header, value in portal_module.SECURITY_HEADERS.items():
        assert sent_headers[header] == value, what


@pytest.mark.parametrize(("path", "payload"), _WRITE_ROUTES)
def test_a_write_route_refuses_an_oversized_body_before_it_writes(
    portal, tmp_path, path, payload
):
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    before = _config_file(tmp_path).read_bytes()

    status, _, _ = portal.route("POST", path, _authed(portal), b"\0" * (MAX_BODY + 1))

    assert status == 413
    assert _config_file(tmp_path).read_bytes() == before


def test_a_write_from_a_client_that_sends_no_origin_still_lands(portal, tmp_path):
    """curl sends no `Origin`, and DEC-016 keeps those callers working."""
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    headers = _authed(portal)
    assert "Origin" not in headers

    status, _ = _post(
        portal, "/api/chain", {"order": ["kokoro"], "fingerprint": _fingerprint(portal)}
    )

    assert status == 200


def test_a_failed_write_never_touches_the_lockout_counter(portal, tmp_path):
    """DEC-018's accepted denial of service is not widened by these routes.

    Only `/api/session` counts an attempt. Ten refused writes leave the
    portal answering, where five refused code exchanges would not.
    """
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)
    for _ in range(10):
        assert _post(portal, "/api/chain", {"order": ["nope"]})[0] == 400

    assert portal._code_attempts == 0
    assert portal.locked_out is False
    assert portal.route("GET", "/api/ping", _authed(portal))[0] == 200


def test_an_unplanned_exception_in_a_write_is_not_turned_into_a_400(portal, monkeypatch):
    """`_answer` catches the families it knows; a bug stays a bug.

    `_respond` turns it into the fixed 500 body, which is what keeps an
    unplanned exception's text — untrusted, and the place a credential
    would leak from — off the wire.
    """
    def blow_up(*args, **kwargs):
        raise TypeError("a bug, not a refusal")

    monkeypatch.setattr(portal_module.config, "_validate_chain", blow_up)
    _exchange(portal)

    with pytest.raises(TypeError):
        portal.route(
            "POST",
            "/api/chain",
            _authed(portal),
            json.dumps({"order": ["say"], "fingerprint": "absent"}).encode(),
        )


# --- a written value is data, never structure -------------------------


@pytest.mark.parametrize(
    ("path", "settings", "read_back"),
    [
        (
            "/api/provider/elevenlabs",
            {"voice": 'a"] [providers.say] voice = "pwned'},
            lambda cfg: cfg["providers"]["elevenlabs"]["voice"],
        ),
        (
            "/api/stt",
            {"input_device": 'a"] [providers.say] voice = "pwned'},
            lambda cfg: cfg["stt"]["input_device"],
        ),
    ],
)
def test_a_written_value_cannot_forge_a_toml_section(
    portal, tmp_path, monkeypatch, path, settings, read_back
):
    """A quote and a bracket in a value are escaped, not structure.

    Every value the page writes goes through `_toml_value`, so the closest
    thing this surface has to an injection sink is a string that tries to
    close its own quotes and open a table of its own.
    """
    _write_config(tmp_path, 'chain = ["say"]\n')
    _exchange(portal)

    status, _ = _post(
        portal, path, {"settings": settings, "fingerprint": _fingerprint(portal)}
    )

    assert status == 200
    reloaded = portal_module.config.load_config_file()
    assert read_back(reloaded) == next(iter(settings.values()))
    assert "say" not in (reloaded.get("providers") or {})


# --- previews (T-63) --------------------------------------------------
#
# The preview goes through `chain.run` with the provider forced, which is
# what makes the budget gate, the ledger and the audio cache apply to it
# exactly as they do to `vocalize speak --provider`. The provider module is
# a fake and the audio cache is `conftest._no_real_audio_cache`'s tmp_path;
# nothing here reaches a network or a speaker.


class _FakeProvider:
    """One provider module's contract, and a record of what it was asked."""

    DEFAULTS: ClassVar[dict] = {}

    def __init__(self, ext="m4a", audio=b"AUDIO-BYTES", error=None):
        self.AUDIO_EXT = ext
        self.NAME = "say"
        self.audio = audio
        self.error = error
        self.calls = []
        self.settings = []

    def check(self, settings, **kwargs):
        self.settings.append(settings)

    def synthesize(self, text, settings, **kwargs):
        self.calls.append(text)
        self.settings.append(settings)
        if self.error is not None:
            raise self.error
        return self.audio


@pytest.fixture
def fake_provider(monkeypatch):
    """Every `providers.get` answers with one fake, whatever the name."""
    provider = _FakeProvider()
    monkeypatch.setattr("vocalize.providers.get", lambda name: provider)
    return provider


def _preview(portal, name: str = "say", body: bytes = b"{}"):
    return portal.route("POST", f"/api/voices/{name}/preview", _authed(portal), body)


@pytest.mark.parametrize(
    ("ext", "content_type"),
    [("mp3", "audio/mpeg"), ("wav", "audio/wav"), ("m4a", "audio/mp4")],
)
def test_a_preview_answers_with_the_audio_and_its_content_type(
    portal, fake_provider, ext, content_type
):
    fake_provider.AUDIO_EXT = ext
    _exchange(portal)

    status, headers, body = _preview(portal)

    assert status == 200
    assert headers["Content-Type"] == content_type
    assert headers["Accept-Ranges"] == "none"
    assert body == b"AUDIO-BYTES"
    assert fake_provider.calls == [portal_module.PREVIEW_TEXT]
    for name, value in portal_module.SECURITY_HEADERS.items():
        assert headers[name] == value


def test_an_unrecognised_audio_ext_is_served_as_bytes_not_guessed(portal, fake_provider):
    """With `nosniff` a wrong Content-Type is worse than an honest one."""
    fake_provider.AUDIO_EXT = "flac"
    _exchange(portal)

    _status, headers, _body = _preview(portal)

    assert headers["Content-Type"] == "application/octet-stream"


def test_extra_headers_cannot_rewrite_a_security_header(portal):
    """`_reply`'s merge order is what keeps it the single exit from route()."""
    _status, headers, _body = portal._reply(
        200,
        {"ok": True},
        extra={"Content-Security-Policy": "default-src *", "Accept-Ranges": "none"},
    )

    assert headers["Content-Security-Policy"] == CSP
    assert headers["Accept-Ranges"] == "none"


def test_a_preview_never_speaks_the_pages_words(portal, fake_provider):
    """The sentence is a constant: a page that chose it could spend anything.

    The handler is not merely told to ignore the body — it is never handed
    one. `_handle` calls `_preview(name)`, so there is no argument for a
    later edit to thread through by accident.
    """
    import inspect

    _exchange(portal)

    status, _headers, _body = _preview(
        portal, body=json.dumps({"text": "charge me a million characters"}).encode()
    )

    assert status == 200
    assert fake_provider.calls == [portal_module.PREVIEW_TEXT]
    assert list(inspect.signature(Portal._preview).parameters) == ["self", "name"]


def test_the_preview_calls_chain_run_the_way_the_design_says(portal, fake_provider):
    """`forced=True` and the one-provider chain, pinned at the call.

    Not observable in the response: `_one_line` cuts `chain.run`'s report to
    its reason line, and the fallback hint `forced` suppresses lives on the
    line after that. So the contract is checked where it is made.
    """
    from vocalize import chain as chain_module

    seen = {}

    def fake_run(text, **kwargs):
        seen["text"] = text
        seen.update(kwargs)
        return b"AUDIO-BYTES", "say", "m4a"

    _exchange(portal)
    original, chain_module.run = chain_module.run, fake_run
    try:
        assert _preview(portal)[0] == 200
    finally:
        chain_module.run = original

    assert seen["text"] == portal_module.PREVIEW_TEXT
    assert seen["chain"] == ["say"]
    assert seen["forced"] is True
    assert seen["cache_dir"] == portal_module.CACHE_DIR
    # No `on_chunk`: that turns on the streaming path, which writes pieces to
    # a temp dir and can raise PlaybackStopped.
    assert "on_chunk" not in seen


def test_a_preview_of_an_unknown_provider_is_404_without_echoing_it(portal, fake_provider):
    _exchange(portal)

    status, _headers, body = _preview(portal, "nope")

    assert status == 404
    assert b"nope" not in body
    assert fake_provider.calls == []


@pytest.mark.parametrize("name", ["..", "say%2F..%2Fgoogle", "", "SAY", "a" * 33])
def test_a_preview_path_the_pattern_refuses_never_reaches_the_handler(
    portal, fake_provider, name
):
    """`_PARAMETERIZED`'s regex is the validation, so traversal is a 404."""
    _exchange(portal)

    status, _headers, _body = _preview(portal, name)

    assert status == 404
    assert fake_provider.calls == []


@pytest.mark.parametrize("name", portal_module.auth.PROVIDER_NAMES)
def test_every_provider_name_reaches_the_preview_handler(portal, fake_provider, name):
    """A renamed provider must not drop silently out of the coverage above."""
    _exchange(portal)

    status, _headers, _body = _preview(portal, name)

    assert status == 200
    assert fake_provider.calls == [portal_module.PREVIEW_TEXT]


def test_a_preview_spends_the_ledger_and_a_repeat_is_a_cache_hit(portal, fake_provider):
    from vocalize import ledger

    _exchange(portal)

    assert _preview(portal)[0] == 200
    assert ledger.status("say") == (len(portal_module.PREVIEW_TEXT), False)

    assert _preview(portal)[0] == 200
    # One synthesis, one charge: the second click came out of the same audio
    # cache `vocalize speak` uses.
    assert fake_provider.calls == [portal_module.PREVIEW_TEXT]
    assert ledger.status("say") == (len(portal_module.PREVIEW_TEXT), False)


def test_a_preview_uses_the_test_audio_cache_and_not_the_real_one(portal, fake_provider, tmp_path):
    """conftest's `_no_real_audio_cache`, pinned where it matters.

    Without it a fake provider's bytes land in `~/.cache/vocalize` under the
    cache key of a real voice's settings, and the developer's next real read
    of this sentence plays them back.
    """
    _exchange(portal)
    assert _preview(portal)[0] == 200

    cache_dir = tmp_path / "audio-cache"
    assert portal_module.CACHE_DIR == cache_dir
    assert any(cache_dir.rglob("*")), "the preview wrote nothing to the test cache"


def test_a_budget_capped_preview_is_refused_in_the_chains_own_words(
    portal, fake_provider, tmp_path
):
    from vocalize import chain as chain_module

    _write_config(tmp_path, 'chain = ["say"]\n\n[providers.say]\nmonthly_chars = 5\n')
    _exchange(portal)

    status, _headers, body = _preview(portal)

    file_config = portal_module.config.load_config_file()
    with pytest.raises(portal_module.VocalizeError) as expected:
        chain_module._budget_gate(
            "say", fake_provider, portal_module.PREVIEW_TEXT, file_config
        )
    assert status == 402
    assert json.loads(body)["error"] == str(expected.value)
    assert fake_provider.calls == []


def test_an_exhausted_provider_is_refused_before_it_is_asked(portal, fake_provider):
    from vocalize import ledger

    ledger.mark_exhausted("say")
    _exchange(portal)

    status, _headers, body = _preview(portal)

    assert status == 402
    assert "quota" in json.loads(body)["error"]
    assert fake_provider.calls == []


def test_a_provider_under_its_budget_is_not_refused(portal, fake_provider, tmp_path):
    """The gate cannot be satisfied by refusing everything."""
    _write_config(tmp_path, 'chain = ["say"]\n\n[providers.say]\nmonthly_chars = 100000\n')
    _exchange(portal)

    assert _preview(portal)[0] == 200


def test_a_failed_preview_is_502_and_one_short_line(portal, fake_provider):
    from vocalize.exceptions import ProviderTransientError

    fake_provider.error = ProviderTransientError(
        "say", "HTTP 500\n<html><body>internal trace and quota details</body></html>"
    )
    _exchange(portal)

    status, headers, body = _preview(portal)

    assert status == 502
    error = json.loads(body)["error"]
    assert "\n" not in error
    assert "<html>" not in error
    assert len(error) <= 200
    for name, value in portal_module.SECURITY_HEADERS.items():
        assert headers[name] == value


def test_a_single_line_upstream_body_is_capped_not_dropped(portal, fake_provider):
    """The honest guarantee, pinned so nobody reads a stronger one into it.

    `_one_line` cuts to one line and caps at 200 characters. A provider that
    wraps a one-line upstream body — `elevenlabs.py` builds exactly that —
    has that body cut at the cap and no sooner, so the cap is the bound and
    the line-cut is not a filter.
    """
    from vocalize.exceptions import ProviderTransientError

    fake_provider.error = ProviderTransientError("say", "HTTP 500: " + "x" * 500)
    _exchange(portal)

    error = json.loads(_preview(portal)[2])["error"]

    assert len(error) == 200
    assert "\n" not in error


def test_a_preview_never_goes_through_the_playback_path(portal, fake_provider, monkeypatch):
    """The browser plays the blob; nothing here takes the machine-wide lock."""

    def never(*args, **kwargs):
        raise AssertionError("a preview must not play audio on this machine")

    monkeypatch.setattr("vocalize.audio.play", never)
    monkeypatch.setattr("vocalize.audio.play_sequence", never)
    monkeypatch.setattr("vocalize.audio._run_tracked", never)
    _exchange(portal)

    assert _preview(portal)[0] == 200


def test_a_preview_reads_the_config_file_as_it_is_now(portal, fake_provider, tmp_path):
    """Not a startup snapshot: `/api/state` re-reads too, and they must agree."""
    _write_config(tmp_path, 'chain = ["say"]\n\n[providers.say]\nspeed = 1.1\n')
    _exchange(portal)
    assert _preview(portal)[0] == 200
    assert fake_provider.settings[0].speed == 1.1

    _write_config(tmp_path, 'chain = ["say"]\n\n[providers.say]\nspeed = 0.9\n')
    assert _preview(portal)[0] == 200
    assert fake_provider.settings[-1].speed == 0.9


def test_a_preview_on_a_config_file_that_does_not_parse_is_400(portal, fake_provider, tmp_path):
    """Rather than a preview in settings the user never chose."""
    _write_config(tmp_path, "chain = [not toml\n")
    _exchange(portal)

    status, _headers, _body = _preview(portal)

    assert status == 400
    assert fake_provider.calls == []


def test_two_previews_run_one_at_a_time(portal, fake_provider):
    """One module lock, which is also what keeps Kokoro's session serial."""
    _exchange(portal)
    entered = threading.Event()
    release = threading.Event()

    def blocking_synthesize(text, settings, **kwargs):
        fake_provider.calls.append(text)
        entered.set()
        assert release.wait(5)
        return b"AUDIO-BYTES"

    fake_provider.synthesize = blocking_synthesize
    answers = {}

    def preview(label):
        answers[label] = _preview(portal)

    first = threading.Thread(target=preview, args=("first",), daemon=True)
    first.start()
    assert entered.wait(5)

    second = threading.Thread(target=preview, args=("second",), daemon=True)
    second.start()
    second.join(0.3)
    assert second.is_alive(), "the second preview did not wait for the first"

    release.set()
    first.join(5)
    second.join(5)

    assert answers["first"][0] == 200
    assert answers["second"][0] == 200
    # And the one that waited found the first one's audio in the cache.
    assert fake_provider.calls == [portal_module.PREVIEW_TEXT]


def test_a_queued_preview_gives_up_rather_than_piling_up(portal, fake_provider, monkeypatch):
    """`_Handler.timeout` is a socket timeout and never reclaims a parked
    thread, so the wait for the lock is bounded (DEC-018)."""
    monkeypatch.setattr(portal_module, "PREVIEW_WAIT", 0.05)
    _exchange(portal)
    entered = threading.Event()
    release = threading.Event()

    def blocking_synthesize(text, settings, **kwargs):
        entered.set()
        assert release.wait(5)
        return b"AUDIO-BYTES"

    fake_provider.synthesize = blocking_synthesize
    first = threading.Thread(target=lambda: _preview(portal), daemon=True)
    first.start()
    assert entered.wait(5)

    status, headers, body = _preview(portal)

    assert status == 503
    assert "already running" in json.loads(body)["error"]
    for name, value in portal_module.SECURITY_HEADERS.items():
        assert headers[name] == value
    release.set()
    first.join(5)


# --- the local install thread (T-63) ----------------------------------
#
# The download seam is `install._default_opener`, which the portal passes
# by name rather than leaving to the default argument `download_file` bound
# at def time. So these exercise the REAL `download_file` — its size and
# sha256 checks included — without a byte leaving the machine. The runtime
# selftest and the recorder build are faked: both shell out.


class _FakeDownload:
    """One response, delivered in blocks, pausing once if asked to."""

    def __init__(self, blob, pause=None, reached=None):
        self._blob = blob
        self._at = 0
        self._pause = pause
        self._reached = reached

    def read(self, size):
        if self._pause is not None and self._at:
            # Once, after the first block has been written and reported:
            # the caller wants a half-finished download to look at.
            self._reached.set()
            assert self._pause.wait(5)
            self._pause = None
        chunk = self._blob[self._at : self._at + size]
        self._at += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fake_install(monkeypatch):
    """A one-file STT install with every subprocess and the network faked."""
    import hashlib
    from types import SimpleNamespace

    from vocalize import local as local_module
    from vocalize.local import install as install_module
    from vocalize.local import whisper_manifest as manifest

    blob = b"m" * 64
    entry = {
        "name": "ggml-fake.bin",
        "url": "https://example.invalid/ggml-fake.bin",
        "size": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
    }
    seen = SimpleNamespace(
        blob=blob,
        entry=entry,
        urls=[],
        selftest=[],
        recorder=[],
        recorder_status="current",
        pause=threading.Event(),
        reached=threading.Event(),
        paused=False,
        model_dir=manifest.MODEL_DIR,
    )

    def opener(url, timeout=None):
        seen.urls.append(url)
        pause = seen.pause if seen.paused else None
        return _FakeDownload(seen.blob, pause, seen.reached)

    monkeypatch.setattr(manifest, "file_for", lambda model: seen.entry)
    monkeypatch.setattr(install_module, "_BLOCK", 16)
    monkeypatch.setattr(install_module, "_default_opener", opener)
    monkeypatch.setattr(local_module, "uv_path", lambda: "/usr/bin/true")
    monkeypatch.setattr(
        install_module, "selftest", lambda *a, **k: seen.selftest.append(k) or "ok"
    )
    monkeypatch.setattr(
        install_module,
        "build_recorder",
        lambda *a, **k: (seen.recorder.append(1), (seen.recorder_status, seen.model_dir))[1],
    )
    return seen


def _start_install(portal, payload: dict):
    return portal.route(
        "POST", "/api/local/install/start", _authed(portal), json.dumps(payload).encode()
    )


def _install_status(portal) -> dict:
    return _body(portal.route("GET", "/api/local/install/status", _authed(portal)))


def _wait_for_install(portal, timeout=5.0) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        status = _install_status(portal)
        if status["done"]:
            return status
        time.sleep(0.01)
    raise AssertionError("the install never finished")


def test_an_install_downloads_verifies_stamps_and_warms_the_runtime(portal, fake_install):
    _exchange(portal)

    status, _headers, body = _start_install(portal, {"target": "stt"})

    assert status == 200
    # A snapshot, not a promise: a fast install can be over before the start
    # call has finished serialising its answer.
    assert json.loads(body)["target"] == "stt"
    final = _wait_for_install(portal)

    assert final["error"] is None
    assert final["running"] is False
    assert final["step"] == "installed"
    assert final["downloaded"] == final["total"] == len(fake_install.blob)
    assert fake_install.urls == [fake_install.entry["url"]]
    assert (fake_install.model_dir / "ggml-fake.bin").read_bytes() == fake_install.blob
    assert fake_install.selftest and fake_install.recorder


def test_a_status_poll_before_any_install_is_the_idle_dict(portal, fake_install):
    _exchange(portal)
    assert _install_status(portal) == portal_module._idle_install()


def test_an_install_reports_progress_while_it_runs(portal, fake_install):
    fake_install.paused = True
    _exchange(portal)

    assert _start_install(portal, {"target": "stt"})[0] == 200
    assert fake_install.reached.wait(5)

    midway = _install_status(portal)
    assert midway["running"] is True
    assert midway["target"] == "stt"
    assert midway["done"] is False
    assert 0 < midway["downloaded"] < midway["total"]
    assert "ggml-fake.bin" in midway["step"]

    fake_install.pause.set()
    assert _wait_for_install(portal)["error"] is None


def test_a_status_poll_right_after_a_start_never_reports_idle(portal, fake_install):
    """The page polls the instant `/start` answers.

    The progress dict is built complete and assigned once for exactly this:
    a poll landing between a reset and an update would read `idle`, which is
    indistinguishable from nothing having been started.
    """
    fake_install.paused = True
    _exchange(portal)

    assert _start_install(portal, {"target": "stt"})[0] == 200
    snapshot = _install_status(portal)

    assert snapshot["step"] != "idle"
    assert snapshot["target"] == "stt"
    fake_install.pause.set()
    _wait_for_install(portal)


def test_a_status_poll_cannot_catch_a_half_written_progress_dict(portal):
    """The poll reads under the same lock the claim and the worker write through.

    Held unlocked it would be one added key away from `dictionary changed
    size during iteration`, and it is the only cross-thread state on this
    surface that a lock is already sitting next to.
    """
    _exchange(portal)
    held = threading.Event()

    def hold():
        with portal._lock:
            held.set()
            time.sleep(0.3)

    threading.Thread(target=hold, daemon=True).start()
    assert held.wait(5)

    start = time.monotonic()
    _install_status(portal)

    assert time.monotonic() - start > 0.1, "the status poll did not take the lock"


def test_a_second_install_while_one_runs_is_409(portal, fake_install):
    fake_install.paused = True
    _exchange(portal)

    assert _start_install(portal, {"target": "stt"})[0] == 200
    assert fake_install.reached.wait(5)

    status, _headers, body = _start_install(portal, {"target": "kokoro"})

    assert status == 409
    assert "already running" in json.loads(body)["error"]

    fake_install.pause.set()
    _wait_for_install(portal)
    # And once it is over, the next one is allowed.
    assert _start_install(portal, {"target": "stt"})[0] == 200
    _wait_for_install(portal)


def test_two_starts_at_once_produce_one_install(portal, fake_install):
    """Two clicks on two handler threads, one download directory."""
    fake_install.paused = True
    _exchange(portal)
    answers = []
    ready = threading.Barrier(2)

    def start():
        ready.wait(5)
        answers.append(_start_install(portal, {"target": "stt"})[0])

    threads = [threading.Thread(target=start, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    assert sorted(answers) == [200, 409]
    fake_install.pause.set()
    _wait_for_install(portal)
    assert fake_install.urls == [fake_install.entry["url"]]


def test_a_thread_that_never_starts_frees_the_install_slot(portal, fake_install, monkeypatch):
    """A refused thread must not wedge the slot or suspend the watchdog."""
    _exchange(portal)

    def refuse(self):
        raise RuntimeError("can't start new thread")

    real_start = threading.Thread.start
    monkeypatch.setattr(threading.Thread, "start", refuse)
    status, headers, body = _start_install(portal, {"target": "stt"})

    assert status == 503
    assert "Could not start" in json.loads(body)["error"]
    assert portal._install["running"] is False
    assert portal._suspended == 0
    for name, value in portal_module.SECURITY_HEADERS.items():
        assert headers[name] == value

    # Put back the one patch, not every patch: `monkeypatch.undo()` here
    # also undid `fake_install` and the autouse cache fixtures, and the
    # install below then went to the real manifest URL over the network
    # and at the developer's own ~/.cache/vocalize.
    monkeypatch.setattr(threading.Thread, "start", real_start)
    # And the next install is allowed, rather than a permanent 409.
    assert _start_install(portal, {"target": "stt"})[0] == 200
    _wait_for_install(portal)


def test_an_install_suspends_the_idle_watchdog_and_gives_it_back(portal, fake_install):
    """A 488 MB download is a page waiting, not a page gone."""
    fake_install.paused = True
    _exchange(portal)

    assert _start_install(portal, {"target": "stt"})[0] == 200
    assert fake_install.reached.wait(5)
    assert portal._suspended > 0

    before = portal._seen
    fake_install.pause.set()
    _wait_for_install(portal)
    assert portal._suspended == 0
    # And the idle clock was reset on the way out, so the first tick after
    # the install cannot close a portal that was busy the whole time.
    assert portal._seen > before


def test_a_served_portal_stays_up_across_an_install(monkeypatch, fake_install):
    """The watchdog, running for real, against an install that outlasts it."""
    monkeypatch.setattr(portal_module, "_IDLE_POLL", 0.01)
    fake_install.paused = True
    served = Portal(idle_timeout=0.05)
    served.start()
    try:
        token = _body(
            served.route(
                "POST",
                "/api/session",
                {"Host": served.origin},
                json.dumps({"code": served._code}).encode(),
            )
        )["token"]
        headers = {"Host": served.origin, TOKEN_HEADER: token}
        status, _, _ = served.route(
            "POST",
            "/api/local/install/start",
            headers,
            json.dumps({"target": "stt"}).encode(),
        )
        assert status == 200
        assert fake_install.reached.wait(5)
        time.sleep(0.4)  # ~40 ticks, ~8 idle timeouts
        assert not served._stopping.is_set(), "the watchdog closed a working install"

        fake_install.pause.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not served._stopping.is_set():
            time.sleep(0.01)
        # And it is watching again the moment the install ends.
        assert served._stopping.is_set()
    finally:
        served.stop()


def test_a_failed_install_reports_one_line_and_leaves_nothing_behind(portal, fake_install):
    fake_install.blob = b"not the file the manifest names".ljust(64, b".")
    _exchange(portal)

    assert _start_install(portal, {"target": "stt"})[0] == 200
    final = _wait_for_install(portal)

    assert final["running"] is False
    assert final["step"] == "failed"
    assert final["error"] and "\n" not in final["error"]
    assert len(final["error"]) <= 200
    assert "checksum" in final["error"]
    assert not (fake_install.model_dir / "ggml-fake.bin").exists()
    assert not (fake_install.model_dir / "ggml-fake.bin.part").exists()


def test_a_rebuilt_recorder_puts_the_regrant_warning_in_the_progress_dict(
    portal, fake_install
):
    """The CLI prints it; a portal install that dropped it would silently
    invalidate the microphone grant and the next dictation would just fail."""
    from vocalize.local import install as install_module

    fake_install.recorder_status = "rebuilt"
    _exchange(portal)

    assert _start_install(portal, {"target": "stt"})[0] == 200
    final = _wait_for_install(portal)

    assert final["note"] == install_module.REGRANT_WARNING
    assert "microphone" in final["note"]


def test_the_install_thread_is_a_daemon_and_is_named(portal, fake_install):
    """A non-daemon thread would hold the interpreter open past `portal`."""
    fake_install.paused = True
    _exchange(portal)

    assert _start_install(portal, {"target": "stt"})[0] == 200
    thread = portal._install_thread

    assert thread.daemon is True
    assert thread.name == "vocalize-portal-install"
    fake_install.pause.set()
    _wait_for_install(portal)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"target": "say"},
        {"target": ["stt"]},
        {"target": None},
        {"target": "STT"},
        {"target": "kokoro", "model": "small.en"},
    ],
)
def test_an_install_start_with_a_bad_target_is_400(portal, fake_install, payload):
    _exchange(portal)
    status, _headers, _body = _start_install(portal, payload)
    assert status == 400
    assert fake_install.urls == []
    assert portal._install_thread is None


@pytest.mark.parametrize("body", [b"\xff\xfe", b"{", b"[]", b"5", b'"stt"', b"null"])
def test_an_install_start_with_a_body_that_is_not_an_object_is_400(
    portal, fake_install, body
):
    _exchange(portal)
    status, _headers, _body = portal.route(
        "POST", "/api/local/install/start", _authed(portal), body
    )
    assert status == 400
    assert fake_install.urls == []


@pytest.mark.parametrize(
    "model",
    ["../../evil", "--flag-shaped", "small.en\x00", "small.en\nmodel", 7, ["small.en"], ""],
)
def test_an_install_of_an_unknown_model_is_400_and_downloads_nothing(
    portal, fake_install, model
):
    """This name reaches a file path and a subprocess argv: allowlist or nothing."""
    _exchange(portal)

    status, _headers, body = _start_install(portal, {"target": "stt", "model": model})

    assert status == 400
    assert "Unknown model" in json.loads(body)["error"]
    assert fake_install.urls == []
    assert portal._install_thread is None


def test_a_refused_model_name_is_never_echoed_back(portal, fake_install):
    _exchange(portal)
    status, _headers, body = _start_install(
        portal, {"target": "stt", "model": "../../etc/passwd"}
    )
    assert status == 400
    assert b"passwd" not in body


def test_every_manifest_model_is_accepted(portal, fake_install):
    """The allowlist is the manifest's own list, not a copy that can drift."""
    from vocalize.local import whisper_manifest as manifest

    _exchange(portal)
    for model in manifest.MODELS:
        assert _start_install(portal, {"target": "stt", "model": model})[0] == 200
        _wait_for_install(portal)


def test_an_install_never_touches_the_lockout_counter(portal, fake_install):
    """DEC-018's accepted denial of service is unchanged by these routes."""
    _exchange(portal)
    for _ in range(10):
        assert _start_install(portal, {"target": "say"})[0] == 400

    assert portal._code_attempts == 0
    assert portal.locked_out is False
    assert portal.route("GET", "/api/ping", _authed(portal))[0] == 200


# --- closing the portal over an install that is still running ----------


def test_closing_the_portal_mid_download_takes_the_part_file_back(portal, fake_install):
    """Ctrl-C, the idle watchdog and a lockout all abandon the worker.

    The worker is a daemon thread, so it is killed without unwinding when
    the process exits and `download_file`'s own part-file cleanup never
    runs. Nothing joins it either — a 488 MB download will not finish in
    any wait worth making. Without this, a part file the size of the model
    stays in the cache, and it is not even resumable: `download_file`
    opens it "wb" every time.
    """
    fake_install.paused = True
    _exchange(portal)
    assert _start_install(portal, {"target": "stt"})[0] == 200
    assert fake_install.reached.wait(5)
    part = fake_install.model_dir / "ggml-fake.bin.part"
    assert part.exists(), "the fake download never opened a part file"

    said = portal.discard_partial_download()

    assert not part.exists()
    assert "cut short" in said
    assert str(part) in said, "the line has to name the file it took"
    fake_install.pause.set()


def test_a_portal_that_installed_nothing_has_nothing_to_discard(portal):
    """The answer nearly every time: the command must stay quiet."""
    assert portal.discard_partial_download() is None


def test_a_finished_install_leaves_nothing_to_discard(portal, fake_install):
    """APP-LIFECYCLE: the new unlink must never reach an installed model.

    The path always ends `.part`, and a successful `download_file` renames
    that away — but a path left lying on the portal after the worker ended
    would be deleted by the *next* close, so the worker clears it.
    """
    _exchange(portal)
    assert _start_install(portal, {"target": "stt"})[0] == 200
    assert _wait_for_install(portal)["error"] is None
    model = fake_install.model_dir / "ggml-fake.bin"
    assert model.exists()

    assert portal.discard_partial_download() is None
    assert portal._downloading is None
    assert model.read_bytes() == fake_install.blob


def test_a_cut_before_the_download_started_claims_no_deletion(portal, tmp_path):
    """The path is claimed before `download_file` opens it.

    Cutting an install in that window used to answer "Its partial download
    was deleted", naming a file that was never created — `unlink` took
    `missing_ok=True` and could not tell the two cases apart.
    """
    portal._install["running"] = True
    portal._install["target"] = "stt"
    portal._downloading = tmp_path / "ggml-fake.bin.part"
    assert not portal._downloading.exists()

    said = portal.discard_partial_download()

    assert said == "The stt install was cut short."
    assert "deleted" not in said
    assert portal._downloading is None


@pytest.mark.parametrize(
    "model",
    ["../../../../etc/passwd", "/etc/passwd", "small.en/../../../x", "..", "small.en\0"],
)
def test_a_refused_model_never_reaches_the_path_the_portal_deletes(
    portal, fake_install, model
):
    """APP-PATH on T-63's new sink: `discard_partial_download` unlinks.

    Nothing from a request may reach that path. `_install_model` allowlists
    against the manifest before the worker exists, so a traversal-shaped
    name is a 400 and the portal is left with nothing to delete at all —
    which is the assertion, rather than the weaker "the resolved path
    stayed under MODEL_DIR".
    """
    _exchange(portal)

    status, _headers, body = _start_install(portal, {"target": "stt", "model": model})

    assert status == 400
    assert model not in body.decode()
    assert portal._downloading is None
    assert portal.discard_partial_download() is None


def test_the_lockout_message_makes_no_claim_about_the_users_own_settings(portal):
    """It used to say "Nothing was changed", and that was false two ways.

    A legitimate page may already have saved settings before the junk
    requests arrived, and an install may be part-way through a download
    the process is about to walk away from. What is always true is the
    part about whoever sent the codes.
    """
    lowered = portal_module.LOCKOUT_MESSAGE.lower()

    assert "nothing was changed" not in lowered
    assert "session token" in lowered


# --- the security canaries for T-63's two surfaces ---------------------


def test_a_preview_never_carries_a_stored_key_anywhere(
    portal, fake_provider, fake_keychain, monkeypatch, capsys
):
    """APP-SECRETS on the preview path: a failure is one line, not the key.

    The canary is seeded in both places `chain.run` actually resolves a key
    from — the keychain and the environment variable — so the assertion has
    something to catch rather than only something to miss.
    """
    from vocalize.exceptions import ProviderTransientError

    fake_keychain[("vocalize", "elevenlabs-api-key")] = CANARY
    monkeypatch.setenv("ELEVENLABS_API_KEY", CANARY)
    fake_provider.error = ProviderTransientError("elevenlabs", "the upstream said no")
    _exchange(portal)

    status, headers, body = _preview(portal, "elevenlabs")

    assert status == 502
    assert CANARY.encode() not in body
    assert CANARY not in json.dumps(headers)
    captured = capsys.readouterr()
    assert CANARY not in captured.out
    assert CANARY not in captured.err


def test_the_model_that_reaches_the_runtime_argv_is_the_allowlisted_one(
    portal, fake_install
):
    """APP-CMD: `selftest_argv` puts `--model <path from file_for>` in argv."""
    from vocalize.local import whisper_manifest as manifest

    _exchange(portal)
    assert _start_install(portal, {"target": "stt", "model": "base.en"})[0] == 200
    _wait_for_install(portal)

    assert fake_install.selftest == [{"manifest": manifest, "model": "base.en"}]
