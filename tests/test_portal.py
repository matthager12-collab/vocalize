"""Tests for vocalize.portal — the config portal's auth bootstrap and its
read-only `/api/state` route.

Almost everything here drives `route()` directly, which is the point of
the pure-function contract: the `Host` check, the token rules, the
security headers, the body cap and the lockout are all provable without a
socket. One smoke test binds a real port and drives it with
`http.client`, so the handler that wraps `route()` is proven to pass the
same rules through.
"""

from __future__ import annotations

import email.message
import http.client
import json
import re
import threading
import time

import pytest

import vocalize.readiness as readiness_module
from vocalize import portal

PORT = 45678
TOKEN_HEADER = portal.TOKEN_HEADER


@pytest.fixture(autouse=True)
def _reset_probe_registries():
    """Every test gets clean probe registries.

    Both `readiness._PROBES` and `portal._probes` are module-level and
    deliberately long-lived (that is how one wedged keychain read leaks
    one thread instead of one per poll). Without this, a probe registered
    — or a masked key cached — by one test would show up in every later
    call to `/api/state` in the same process.
    """
    for registry in (readiness_module._PROBES, readiness_module._inflight, portal._probes):
        registry.clear()
    yield
    for registry in (readiness_module._PROBES, readiness_module._inflight, portal._probes):
        registry.clear()


@pytest.fixture(autouse=True)
def _no_real_aws_credentials(monkeypatch):
    """Keep `/api/state` off the developer's real ~/.aws/credentials.

    Polly has no key slot, so its status comes from `auth
    .polly_credential_status`, which reads a file in the real home
    directory. Same reasoning as conftest's keychain and ledger fixtures:
    a machine that happens to have an AWS profile would otherwise change
    the payload these tests assert on.
    """
    monkeypatch.setattr(
        "vocalize.auth.polly_credential_status", lambda profile="default": "not configured"
    )


@pytest.fixture(autouse=True)
def _no_inherited_overrides(monkeypatch):
    """Keep the developer's own VOCALIZE_* environment out of the payload.

    `resolve_chain` and `resolve_provider_settings` read these before the
    config file, so a shell that exports one would change what
    `/api/state` reports and fail an assertion for a reason that has
    nothing to do with the portal.
    """
    for name in ("VOCALIZE_CHAIN", "VOCALIZE_VOICE", "VOCALIZE_MODEL", "VOCALIZE_SPEED"):
        monkeypatch.delenv(name, raising=False)


def _state(file_config=None, **kwargs):
    kwargs.setdefault("port", PORT)
    return portal.PortalState(file_config or {"chain": ["say"]}, **kwargs)


def _headers(state, **extra):
    headers = {"Host": state.expected_host}
    headers.update(extra)
    return headers


def _get(state, path, **extra):
    return portal.route("GET", path, _headers(state, **extra), b"", state=state)


def _authed_get(state, path, **extra):
    return _get(state, path, **{TOKEN_HEADER: state.token}, **extra)


def _post(state, path, payload, **extra):
    body = json.dumps(payload).encode("utf-8")
    return portal.route("POST", path, _headers(state, **extra), body, state=state)


def _payload(result):
    return json.loads(result[2].decode("utf-8"))


def _alive_threads():
    """Live threads only — a joined thread lingers in `active_count()`."""
    return len([t for t in threading.enumerate() if t.is_alive()])


# --- Host: the DNS-rebinding gate -------------------------------------

ROUTES = ("/", "/portal.js", "/api/session", "/api/state", "/api/ping", "/api/nope")

WRONG_HOSTS = (
    f"localhost:{PORT}",  # resolves to the same socket; a different origin
    "127.0.0.1:1",  # right host, wrong port
    "evil.example",  # the DNS-rebinding shape
    f"evil.example:{PORT}",
    f"[::1]:{PORT}",  # the IPv6 loopback form
    "127.0.0.1",  # no port at all
    "",
)


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("host", WRONG_HOSTS)
def test_wrong_host_header_is_refused_on_every_route(path, host):
    state = _state()
    status, headers, _ = portal.route("GET", path, {"Host": host}, b"", state=state)
    assert status == 421
    assert headers["Content-Security-Policy"] == portal.SECURITY_HEADERS["Content-Security-Policy"]


@pytest.mark.parametrize("path", ROUTES)
def test_absent_host_header_is_refused_on_every_route(path):
    state = _state()
    assert portal.route("GET", path, {}, b"", state=state)[0] == 421


def test_duplicate_host_header_is_refused():
    """Two Host headers is not a shape a browser produces; picking one of
    them is how parsing differences become bypasses."""
    state = _state()
    headers = email.message.Message()
    headers["Host"] = state.expected_host
    headers["Host"] = "evil.example"
    assert portal.route("GET", "/", headers, b"", state=state)[0] == 421


def test_correct_host_is_accepted():
    state = _state()
    assert _get(state, "/")[0] == 200


def test_host_check_runs_before_the_session_exchange():
    """A rebound page must not even get to spend a code guess."""
    state = _state()
    status, _, _ = portal.route(
        "POST", "/api/session", {"Host": "evil.example"},
        json.dumps({"code": state.code}).encode(), state=state,
    )
    assert status == 421
    assert state.code is not None
    assert state.failed_codes == 0


# --- Origin -----------------------------------------------------------


def test_cross_site_origin_is_refused_even_with_the_right_host():
    state = _state()
    status, _, _ = portal.route(
        "GET", "/", _headers(state, Origin="http://evil.example"), b"", state=state
    )
    assert status == 403


def test_own_origin_is_accepted():
    state = _state()
    status, _, _ = portal.route(
        "GET", "/", _headers(state, Origin=state.origin), b"", state=state
    )
    assert status == 200


# --- the one-time code and the session token --------------------------


def test_session_exchange_returns_the_token():
    state = _state()
    status, _, body = _post(state, "/api/session", {"code": state.code})
    assert status == 200
    assert json.loads(body) == {"token": state.token}


def test_session_secrets_are_full_length_and_distinct():
    state = _state()
    # secrets.token_urlsafe(32) is 43 characters of base64url.
    assert len(state.code) >= 43
    assert len(state.token) >= 43
    assert state.code != state.token


def test_session_code_is_single_use():
    state = _state()
    code = state.code
    assert _post(state, "/api/session", {"code": code})[0] == 200
    status, _, body = _post(state, "/api/session", {"code": code})
    assert status == 401
    assert "already been used" in json.loads(body)["error"]


def test_session_code_expires_sixty_seconds_after_the_start(monkeypatch):
    assert portal.CODE_TTL_SECONDS == 60.0
    state = _state()
    monkeypatch.setattr(portal, "_now", lambda: state.code_expires_at + 0.001)
    status, _, body = _post(state, "/api/session", {"code": state.code})
    assert status == 401
    assert "expired" in json.loads(body)["error"]


def test_session_code_still_works_just_inside_the_window(monkeypatch):
    state = _state()
    monkeypatch.setattr(portal, "_now", lambda: state.code_expires_at - 0.001)
    assert _post(state, "/api/session", {"code": state.code})[0] == 200


def test_session_route_rejects_a_body_with_no_code():
    state = _state()
    assert _post(state, "/api/session", {})[0] == 400
    assert _post(state, "/api/session", {"code": 7})[0] == 400
    assert state.failed_codes == 0


def test_session_route_is_post_only():
    state = _state()
    assert _get(state, "/api/session")[0] == 405


# --- lockout ----------------------------------------------------------


def test_five_wrong_codes_shut_the_portal_down():
    state = _state()
    stopped = []
    state.on_shutdown = lambda: stopped.append("stopped")

    for _ in range(portal.MAX_CODE_ATTEMPTS - 1):
        assert _post(state, "/api/session", {"code": "wrong"})[0] == 401
        assert state.shutdown_reason is None

    assert _post(state, "/api/session", {"code": "wrong"})[0] == 401
    assert state.shutdown_reason == portal.LOCKOUT_REASON
    assert stopped == ["stopped"]


def test_lockout_message_names_the_command_and_leaks_no_secret():
    state = _state()
    for _ in range(portal.MAX_CODE_ATTEMPTS):
        result = _post(state, "/api/session", {"code": "wrong"})
        assert state.code not in result[2].decode()
        assert state.token not in result[2].decode()
    assert "vocalize portal" in portal.LOCKOUT_REASON


def test_lockout_counts_an_expired_code_too(monkeypatch):
    """After expiry there is no code left to guess, so a caller still
    hammering the route is not the browser we opened."""
    state = _state()
    monkeypatch.setattr(portal, "_now", lambda: state.code_expires_at + 1)
    for _ in range(portal.MAX_CODE_ATTEMPTS):
        _post(state, "/api/session", {"code": "anything"})
    assert state.shutdown_reason == portal.LOCKOUT_REASON


# --- where the token may travel ---------------------------------------


@pytest.mark.parametrize("path", ROUTES)
def test_token_in_the_query_string_is_refused_on_every_route(path):
    state = _state()
    status, _, body = portal.route(
        "GET", f"{path}?token={state.token}", _headers(state), b"", state=state
    )
    assert status == 401
    assert TOKEN_HEADER in json.loads(body)["error"]


@pytest.mark.parametrize("name", ("token", "access_token", "session", "X-Vocalize-Token"))
def test_token_shaped_query_parameters_are_refused(name):
    state = _state()
    status, _, _ = portal.route(
        "GET", f"/api/state?{name}=x", _headers(state), b"", state=state
    )
    assert status == 401


def test_token_in_the_request_body_is_refused_and_costs_no_code():
    state = _state()
    status, _, _ = _post(state, "/api/session", {"code": state.code, "token": state.token})
    assert status == 401
    assert state.code is not None
    assert state.failed_codes == 0


@pytest.mark.parametrize("path", ("/api/state", "/api/ping", "/api/nope"))
def test_api_routes_refuse_a_missing_token(path):
    state = _state()
    assert _get(state, path)[0] == 401


def test_wrong_token_is_refused():
    state = _state()
    assert _get(state, "/api/ping", **{TOKEN_HEADER: "not-the-token"})[0] == 401


def test_a_token_prefix_is_refused():
    """compare_digest, not a prefix match."""
    state = _state()
    assert _get(state, "/api/ping", **{TOKEN_HEADER: state.token[:-1]})[0] == 401
    assert _get(state, "/api/ping", **{TOKEN_HEADER: state.token + "x"})[0] == 401


def test_duplicate_token_headers_are_refused():
    state = _state()
    headers = email.message.Message()
    headers["Host"] = state.expected_host
    headers[TOKEN_HEADER] = state.token
    headers[TOKEN_HEADER] = "smuggled"
    assert portal.route("GET", "/api/ping", headers, b"", state=state)[0] == 401


def test_the_valid_token_opens_the_api():
    state = _state()
    assert _authed_get(state, "/api/ping")[0] == 200


# --- static assets ----------------------------------------------------


@pytest.mark.parametrize("path", ("/", "/portal.js"))
def test_static_routes_need_no_token_and_serve_no_secret(path):
    state = _state()
    status, headers, body = _get(state, path)
    assert status == 200
    text = body.decode("utf-8")
    assert state.code not in text
    assert state.token not in text
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_static_routes_are_get_only():
    state = _state()
    assert _post(state, "/", {})[0] == 405
    assert _post(state, "/portal.js", {})[0] == 405


def test_served_page_has_no_inline_script_and_no_external_url():
    html = (portal.ASSETS_DIR / "portal.html").read_text()
    js = (portal.ASSETS_DIR / "portal.js").read_text()
    assert "<script>" not in html
    assert re.search(r"https?://", html) is None
    assert re.search(r"https?://", js) is None


def test_the_page_stores_the_token_nowhere_persistent():
    # Comments stripped: the file names these APIs to say it avoids them.
    js = "\n".join(
        line for line in (portal.ASSETS_DIR / "portal.js").read_text().splitlines()
        if not line.strip().startswith("//")
    )
    assert "localStorage" not in js
    assert "sessionStorage" not in js
    assert "document.cookie" not in js
    assert "innerHTML" not in js


# --- security headers -------------------------------------------------


def _every_response_shape(state):
    """One response per status this module can produce."""
    return [
        ("html", _get(state, "/")),
        ("script", _get(state, "/portal.js")),
        ("wrong code", _post(state, "/api/session", {"code": "wrong"})),
        ("no token", _get(state, "/api/state")),
        ("unknown route", _authed_get(state, "/api/nope")),
        ("ping", _authed_get(state, "/api/ping")),
        ("bad host", portal.route("GET", "/", {"Host": "evil.example"}, b"", state=state)),
        ("bad origin", portal.route(
            "GET", "/", _headers(state, Origin="http://evil.example"), b"", state=state)),
        ("bad method", portal.route("DELETE", "/api/ping", _headers(state), b"", state=state)),
        ("oversized body", portal.route(
            "POST", "/api/session", _headers(state),
            b"x" * (portal.MAX_BODY_BYTES + 1), state=state)),
        ("bad json", portal.route(
            "POST", "/api/session", _headers(state), b"{not json", state=state)),
        ("token in query", _get(state, "/?token=x")),
    ]


def test_security_headers_are_on_every_response():
    state = _state()
    for label, (_status, headers, _body) in _every_response_shape(state):
        for name, value in portal.SECURITY_HEADERS.items():
            assert headers.get(name) == value, f"{label} is missing {name}"


def test_security_headers_are_the_designed_strings():
    assert portal.SECURITY_HEADERS["Content-Security-Policy"] == (
        "default-src 'self'; media-src 'self' blob:; frame-ancestors 'none'"
    )
    assert portal.SECURITY_HEADERS["X-Frame-Options"] == "DENY"
    assert portal.SECURITY_HEADERS["X-Content-Type-Options"] == "nosniff"
    assert portal.SECURITY_HEADERS["Cache-Control"] == "no-store"
    assert portal.SECURITY_HEADERS["Referrer-Policy"] == "no-referrer"


def test_headers_never_allow_a_cross_origin_reader():
    state = _state()
    for _label, (_status, headers, _body) in _every_response_shape(state):
        assert not any(name.lower().startswith("access-control-") for name in headers)


# --- request shape ----------------------------------------------------


def test_an_oversized_body_is_refused_with_413():
    state = _state()
    body = b"x" * (portal.MAX_BODY_BYTES + 1)
    assert portal.route("POST", "/api/session", _headers(state), body, state=state)[0] == 413
    assert portal.MAX_BODY_BYTES == 64 * 1024


def test_a_malformed_json_body_is_400():
    state = _state()
    assert portal.route("POST", "/api/session", _headers(state), b"{", state=state)[0] == 400
    assert portal.route(
        "POST", "/api/session", _headers(state), b"[1,2,3]", state=state
    )[0] == 400
    assert portal.route(
        "POST", "/api/session", _headers(state), b"\xff\xfe", state=state
    )[0] == 400


@pytest.mark.parametrize("method", ("PUT", "DELETE", "PATCH", "OPTIONS", "HEAD", "TRACE"))
def test_only_get_and_post_are_accepted(method):
    state = _state()
    assert portal.route(method, "/api/ping", _headers(state), b"", state=state)[0] == 405


def test_an_unknown_route_is_404():
    state = _state()
    assert _authed_get(state, "/api/does-not-exist")[0] == 404


# --- /api/state -------------------------------------------------------


def test_state_payload_carries_rows_chain_providers_budgets_and_stt():
    state = _state({"chain": ["say"]})
    status, headers, body = _authed_get(state, "/api/state")
    assert status == 200
    assert headers["Content-Type"] == "application/json"

    payload = json.loads(body)
    assert {"rows", "chain", "providers", "budgets", "stt"} == set(payload)
    assert payload["chain"] == {"order": ["say"], "source": "config file"}
    assert [r["name"] for r in payload["rows"]] == ["say"]
    assert {"name", "state", "detail", "action"} == set(payload["rows"][0])
    assert payload["stt"]["model"] == "small.en"
    assert payload["stt"]["max_seconds"] == 120
    assert payload["providers"]["say"]["voice"] is None
    assert payload["budgets"]["say"] == {
        "chars": 0, "exhausted": False, "monthly_chars": None,
    }


def test_state_reports_budgets_against_the_ledger():
    from vocalize import ledger

    ledger.record("elevenlabs", 1234)
    state = _state({"chain": ["say"], "providers": {"elevenlabs": {"monthly_chars": 5000}}})
    payload = _payload(_authed_get(state, "/api/state"))
    assert payload["budgets"]["elevenlabs"]["chars"] == 1234
    assert payload["budgets"]["elevenlabs"]["monthly_chars"] == 5000


def test_state_reports_an_invalid_chain_instead_of_failing(monkeypatch):
    monkeypatch.setenv("VOCALIZE_CHAIN", "not-a-provider")
    state = _state()
    payload = _payload(_authed_get(state, "/api/state"))
    assert payload["chain"]["source"] == "invalid"
    assert payload["chain"]["order"] == []


def test_state_reports_an_invalid_stt_table_instead_of_failing():
    state = _state({"chain": ["say"], "stt": {"max_seconds": 99999}})
    payload = _payload(_authed_get(state, "/api/state"))
    assert "error" in payload["stt"]


def test_state_never_contains_the_api_key(fake_keychain, monkeypatch):
    """The canary is a real stored key; only its four-character mask may
    reach the response."""
    canary = "sk_canary_never_leaves_the_machine"
    fake_keychain[("vocalize", "elevenlabs-api-key")] = canary
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

    state = _state({"chain": ["elevenlabs", "say"]})
    _status, _headers, body = _authed_get(state, "/api/state")

    assert canary not in body.decode("utf-8")
    payload = json.loads(body)
    assert payload["providers"]["elevenlabs"]["key"] == {
        "source": "keychain", "masked": "sk_c…",
    }


def test_state_masks_a_key_taken_from_the_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-envcanary-0123456789")
    state = _state()
    _status, _headers, body = _authed_get(state, "/api/state")
    assert "sk-envcanary-0123456789" not in body.decode("utf-8")
    assert json.loads(body)["providers"]["openai"]["key"] == {
        "source": "environment", "masked": "sk-e…",
    }


def test_state_route_returns_under_a_blocked_probe():
    blocked = threading.Event()  # never set
    readiness_module._PROBES["wedged"] = blocked.wait
    state = _state(readiness_timeout=0.1)

    start = time.monotonic()
    status, _headers, body = _authed_get(state, "/api/state")
    elapsed = time.monotonic() - start

    assert status == 200
    row = next(r for r in json.loads(body)["rows"] if r["name"] == "wedged")
    assert row["state"] == "warn"
    assert "still checking" in row["detail"]
    assert elapsed < 3.0


def test_state_ten_polls_against_a_blocked_probe_start_one_thread():
    blocked = threading.Event()  # never set
    readiness_module._PROBES["wedged"] = blocked.wait
    state = _state(readiness_timeout=0.05)

    _authed_get(state, "/api/state")
    after_first = _alive_threads()
    for _ in range(9):
        _authed_get(state, "/api/state")

    assert _alive_threads() == after_first


def test_state_survives_a_credential_probe_that_blocks(monkeypatch):
    """A wedged keychain read yields "still checking", not a hung route."""
    blocked = threading.Event()  # never set

    def wedged(name, file_config):
        blocked.wait()

    monkeypatch.setattr(portal, "_key_status", wedged)
    state = _state(readiness_timeout=0.05)

    status, _headers, body = _authed_get(state, "/api/state")
    assert status == 200
    assert json.loads(body)["providers"]["say"]["key"] == {
        "source": "not checked", "masked": None,
    }


def test_state_survives_a_credential_probe_that_raises(monkeypatch):
    def boom(name, file_config):
        raise RuntimeError("sk_secret_in_the_message")

    monkeypatch.setattr(portal, "_key_status", boom)
    state = _state(readiness_timeout=0.2)

    _status, _headers, body = _authed_get(state, "/api/state")
    assert b"sk_secret_in_the_message" not in body
    assert json.loads(body)["providers"]["say"]["key"]["source"] == "not checked"


def test_state_counts_as_activity_for_the_idle_watchdog(monkeypatch):
    state = _state()
    state.last_seen = 0.0
    _authed_get(state, "/api/state")
    assert state.last_seen > 0.0


# --- the server ------------------------------------------------------


@pytest.fixture
def running_portal():
    started = portal.serve({"chain": ["say"]}, readiness_timeout=0.2, idle_timeout=30.0)
    try:
        yield started
    finally:
        started.stop()


def _connect(started):
    return http.client.HTTPConnection(portal.BIND_HOST, started.port, timeout=5)


def _port_is_gone(started, deadline=3.0):
    """Whether the listening socket has stopped answering.

    Polled rather than asserted once: `shutdown()` and `server_close()`
    run on the shutdown thread, so the serve loop can finish a moment
    before the socket is dropped.
    """
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        try:
            conn = http.client.HTTPConnection(portal.BIND_HOST, started.port, timeout=1)
            conn.request("GET", "/")
            conn.getresponse().read()
            conn.close()
        except OSError:
            return True
        time.sleep(0.05)
    return False


def test_the_server_binds_loopback_only(running_portal):
    assert portal.BIND_HOST == "127.0.0.1"
    assert running_portal.server.server_address[0] == "127.0.0.1"


def test_the_opening_url_carries_the_code_in_the_fragment():
    state = _state(port=9999)
    assert state.url() == f"http://127.0.0.1:9999/#code={state.code}"
    assert "?" not in state.url()


def test_serve_opens_the_browser_only_when_asked():
    opened = []
    started = portal.serve({"chain": ["say"]}, open_browser=opened.append, idle_timeout=30.0)
    try:
        assert opened == [started.url]
    finally:
        started.stop()


def test_real_socket_smoke_covers_host_token_state_and_headers(running_portal):
    started = running_portal
    conn = _connect(started)

    conn.request("GET", "/")
    response = conn.getresponse()
    page = response.read()
    assert response.status == 200
    assert response.getheader("Content-Security-Policy") == (
        portal.SECURITY_HEADERS["Content-Security-Policy"]
    )
    assert response.getheader("X-Frame-Options") == "DENY"
    assert response.getheader("Cache-Control") == "no-store"
    assert b"<script src=\"/portal.js\">" in page

    conn.close()

    conn = _connect(started)
    conn.request("GET", "/", headers={"Host": f"localhost:{started.port}"})
    response = conn.getresponse()
    response.read()
    assert response.status == 421

    body = json.dumps({"code": "wrong"}).encode()
    conn.request("POST", "/api/session", body=body,
                 headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    response.read()
    assert response.status == 401

    body = json.dumps({"code": started.state.code}).encode()
    conn.request("POST", "/api/session", body=body,
                 headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    token = json.loads(response.read())["token"]
    assert token == started.state.token

    conn.request("GET", "/api/state", headers={TOKEN_HEADER: token})
    response = conn.getresponse()
    payload = json.loads(response.read())
    assert response.status == 200
    assert response.getheader("X-Content-Type-Options") == "nosniff"
    assert [row["name"] for row in payload["rows"]] == ["say"]

    conn.request("GET", f"/api/state?token={token}")
    response = conn.getresponse()
    response.read()
    assert response.status == 401
    conn.close()


def test_the_sixth_wrong_code_finds_the_server_gone(running_portal):
    started = running_portal
    conn = _connect(started)
    body = json.dumps({"code": "wrong"}).encode()

    for _ in range(portal.MAX_CODE_ATTEMPTS):
        conn.request("POST", "/api/session", body=body,
                     headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        response.read()
        assert response.status == 401
    conn.close()

    assert started.wait(5.0) == portal.LOCKOUT_REASON
    assert _port_is_gone(started), "the port still answers after the lockout"


def test_the_handler_refuses_an_oversized_body_without_reading_it(running_portal):
    started = running_portal
    conn = _connect(started)
    conn.request(
        "POST", "/api/session",
        headers={"Content-Type": "application/json",
                 "Content-Length": str(portal.MAX_BODY_BYTES + 1)},
    )
    response = conn.getresponse()
    response.read()
    assert response.status == 413
    conn.close()


def test_the_handler_refuses_a_chunked_body(running_portal):
    started = running_portal
    conn = _connect(started)
    conn.putrequest("POST", "/api/session", skip_accept_encoding=True)
    conn.putheader("Transfer-Encoding", "chunked")
    conn.endheaders()
    conn.send(b"0\r\n\r\n")
    response = conn.getresponse()
    response.read()
    assert response.status == 400
    conn.close()


def test_the_handler_logs_nothing(running_portal, capsys):
    conn = _connect(running_portal)
    conn.request("GET", "/api/state?token=leaky")
    conn.getresponse().read()
    conn.close()
    captured = capsys.readouterr()
    assert "leaky" not in captured.err
    assert "leaky" not in captured.out


def test_the_idle_watchdog_closes_the_portal():
    started = portal.serve({"chain": ["say"]}, idle_timeout=0.05)
    try:
        assert started.wait(5.0) == portal.IDLE_REASON
    finally:
        started.stop()


def test_suspending_the_watchdog_holds_the_portal_open():
    """Run 8's hook: a long install is the one time the page goes quiet."""
    started = portal.serve({"chain": ["say"]}, idle_timeout=0.05)
    started.state.watchdog_suspended = True
    try:
        assert started.wait(0.5) is None
    finally:
        started.stop()


def test_a_ping_keeps_the_portal_open(running_portal):
    started = running_portal
    started.state.last_seen = 0.0
    conn = _connect(started)
    conn.request("GET", "/api/ping", headers={TOKEN_HEADER: started.state.token})
    response = conn.getresponse()
    response.read()
    conn.close()
    assert response.status == 200
    assert started.state.last_seen > 0.0


def test_the_handler_refuses_a_non_decimal_content_length(running_portal):
    """int() would read "5_0" as 50; anything in front of this server
    would not, and a length two parsers disagree on is a smuggling seed."""
    started = running_portal
    conn = _connect(started)
    conn.putrequest("POST", "/api/session", skip_accept_encoding=True)
    conn.putheader("Content-Length", "1_0")
    conn.endheaders()
    response = conn.getresponse()
    response.read()
    assert response.status == 400
    conn.close()
