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
import hashlib
import http.client
import json
import os
import re
import socket
import stat
import threading
import time
import webbrowser
from types import SimpleNamespace
from typing import ClassVar

import pytest

import vocalize.readiness as readiness_module
from vocalize import config as config_module
from vocalize import portal, wizard
from vocalize.exceptions import ConfigChangedError, ConfigError, VocalizeError

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


def test_the_lockout_message_never_calls_a_re_sent_code_a_wrong_one():
    """Re-opening the URL — a second browser, the Back button, history —
    re-sends a code that has already been used, and that counts toward the
    lockout like any other refusal. The message says so: it must not
    report five wrong guesses to a user who made none."""
    state = _state()
    code = state.code
    assert _post(state, "/api/session", {"code": code})[0] == 200

    for _ in range(portal.MAX_CODE_ATTEMPTS):
        result = _post(state, "/api/session", {"code": code})
        assert result[0] == 401
        assert "already been used" in _payload(result)["error"]

    assert state.shutdown_reason == portal.LOCKOUT_REASON
    assert "wrong" not in portal.LOCKOUT_REASON


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


@pytest.mark.parametrize(
    "path", ("/api/state", "/api/ping", "/api/local/install/status", "/api/nope")
)
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


# --- secrets that are not ASCII ---------------------------------------
#
# `secrets.compare_digest` raises TypeError on a non-ASCII str, and both
# halves of the auth surface compare attacker-supplied text: a header
# http.server decodes as latin-1, and a JSON string that can hold a lone
# surrogate. A raise there is not a 401 — it is no response at all.


@pytest.mark.parametrize("offered", ("café", "\xff", "\ud800", "🔑"))
def test_a_non_ascii_token_header_is_refused_with_the_full_header_set(offered):
    state = _state()
    status, headers, _ = _get(state, "/api/ping", **{TOKEN_HEADER: offered})
    assert status == 401
    for name, value in portal.SECURITY_HEADERS.items():
        assert headers.get(name) == value


@pytest.mark.parametrize("offered", ("café", "\ud800", "🔑"))
def test_a_non_ascii_code_is_refused_and_counts_toward_the_lockout(offered):
    state = _state()
    status, headers, body = _post(state, "/api/session", {"code": offered})
    assert status == 401
    assert json.loads(body)["error"] == "wrong code"
    assert state.failed_codes == 1
    assert headers["Content-Security-Policy"] == portal.SECURITY_HEADERS["Content-Security-Policy"]


def test_a_non_ascii_secret_is_never_taken_for_the_real_one():
    state = _state()
    state.token = "café"
    assert _get(state, "/api/ping", **{TOKEN_HEADER: "cafe"})[0] == 401
    assert _get(state, "/api/ping", **{TOKEN_HEADER: "café"})[0] == 200


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


def test_the_page_keeps_pinging_so_the_watchdog_leaves_it_open():
    """The watchdog closes the portal after four missed pings; the page
    this run ships is the only client that would send them, so a page with
    no keepalive means a server that exits a minute after it loads."""
    js = (portal.ASSETS_DIR / "portal.js").read_text()
    assert "/api/ping" in js

    match = re.search(r"setInterval\([\s\S]*?/api/ping[\s\S]*?,\s*(\d+)\s*\)", js)
    assert match, "the page must schedule a repeating /api/ping"
    every = int(match.group(1)) / 1000
    assert 0 < every <= portal.PING_INTERVAL_SECONDS
    assert every * portal.MISSED_PINGS_BEFORE_SHUTDOWN <= portal.DEFAULT_IDLE_TIMEOUT


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
    assert {"fingerprint", "rows", "chain", "providers", "budgets", "stt"} == set(payload)
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


def test_state_ten_polls_against_a_blocked_key_probe_start_one_thread(monkeypatch):
    """T-61's criterion, aimed at `portal._probes`.

    The readiness sibling above proves `readiness._inflight`, which is run
    5's registry. This one proves the portal's own: it is what makes a
    keychain read wedged behind a permission dialog cost one thread per
    provider for the life of the process, not six per poll.
    """
    blocked = threading.Event()  # never set until the assertions are done

    def wedged(name, file_config):
        blocked.wait()

    monkeypatch.setattr(portal, "_key_status", wedged)
    state = _state(readiness_timeout=0.05)
    try:
        _authed_get(state, "/api/state")
        after_first = _alive_threads()
        for _ in range(9):
            _authed_get(state, "/api/state")
        assert _alive_threads() == after_first
    finally:
        blocked.set()


def test_state_bounds_every_credential_probe_by_one_deadline(monkeypatch):
    """Six wedged keychain reads cost about one timeout between them.

    Joined one after another they made the first page load — the one the
    user is actually waiting on — six times slower than the timeout says,
    and the in-flight registry saves the thread, not the wait.
    """
    from vocalize import auth

    blocked = threading.Event()  # never set until the assertions are done

    def wedged(name, file_config):
        blocked.wait()

    monkeypatch.setattr(portal, "_key_status", wedged)
    timeout = 0.3
    state = _state(readiness_timeout=timeout)
    try:
        start = time.monotonic()
        status, _headers, body = _authed_get(state, "/api/state")
        elapsed = time.monotonic() - start

        assert status == 200
        providers = json.loads(body)["providers"]
        assert len(providers) == len(auth.PROVIDER_NAMES) >= 6
        assert all(
            entry["key"] == {"source": "not checked", "masked": None}
            for entry in providers.values()
        )
        assert elapsed < timeout * 3, f"{elapsed:.2f}s for {len(providers)} providers"
    finally:
        blocked.set()


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


def _raw(started, request: bytes, timeout=5.0) -> bytes:
    """Send bytes http.client would not, and read until the server closes."""
    sock = socket.create_connection((portal.BIND_HOST, started.port), timeout=timeout)
    try:
        sock.sendall(request)
        chunks = []
        while True:
            piece = sock.recv(4096)
            if not piece:
                return b"".join(chunks)
            chunks.append(piece)
    finally:
        sock.close()


def _head_of(raw: bytes) -> str:
    return raw.split(b"\r\n\r\n", 1)[0].decode("latin-1")


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


def test_serve_opens_the_browser_only_when_asked(monkeypatch):
    opened = []
    started = portal.serve({"chain": ["say"]}, open_browser=opened.append, idle_timeout=30.0)
    try:
        assert opened == [started.url]
    finally:
        started.stop()

    # And the default is inert, not `webbrowser.open`: five tests in this
    # file call serve() without one, and a regressed default would open a
    # real browser on the developer's machine with the suite still green.
    launched = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: launched.append(url))
    started = portal.serve({"chain": ["say"]}, idle_timeout=30.0)
    try:
        assert launched == []
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


def test_a_failing_route_answers_500_with_the_security_headers(
    running_portal, monkeypatch, capsys
):
    """A route that raises must not drop the connection.

    A hand-corrupted `usage.json` is enough to make `ledger.all_status()`
    raise inside `/api/state`. Without a boundary the client gets zero
    bytes — no status line, so none of the security headers — and
    socketserver prints a traceback naming install paths to stderr, which
    is the one thing `log_message` exists to prevent.
    """

    def boom():
        raise AttributeError("no attribute 'get' on the corrupt month entry")

    monkeypatch.setattr("vocalize.ledger.all_status", boom)
    started = running_portal
    conn = _connect(started)
    conn.request("GET", "/api/state", headers={TOKEN_HEADER: started.state.token})
    response = conn.getresponse()
    body = response.read()
    conn.close()

    assert response.status == 500
    for name, value in portal.SECURITY_HEADERS.items():
        assert response.getheader(name) == value
    assert b"AttributeError" not in body
    assert b"corrupt month entry" not in body

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


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


@pytest.mark.parametrize(
    ("label", "request_line", "status"),
    (
        # fetch() may send any verb but CONNECT/TRACE/TRACK, and one with
        # no do_* method is refused by http.server itself.
        ("unknown verb", b"FOO / HTTP/1.1", b" 501 "),
        # <iframe src="http://127.0.0.1:PORT/?<padding>"> — the shape that
        # would frame a portal page if X-Frame-Options were missing.
        ("over-long request line", b"GET /?" + b"x" * 70000 + b" HTTP/1.1", b" 414 "),
    ),
)
def test_http_servers_own_refusals_carry_the_security_headers(
    running_portal, label, request_line, status
):
    """Every response means every response, including the ones route()
    never sees: these are answered inside handle_one_request, before the
    Host check and before _handle."""
    raw = _raw(
        running_portal,
        request_line + f"\r\nHost: {portal.BIND_HOST}:{running_portal.port}\r\n\r\n".encode(),
    )
    head = _head_of(raw)
    assert status.decode() in head, head[:200]
    assert "text/html" not in head
    for name, value in portal.SECURITY_HEADERS.items():
        assert f"{name}: {value}" in head, f"{label} is missing {name}"


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


def test_the_handler_refuses_two_content_length_headers(running_portal):
    """The sibling of the check above: `.get()` would take the first copy,
    something in front of this server could take the second, and the bytes
    left unread are the next request line on the same connection."""
    started = running_portal
    body = json.dumps({"code": started.state.code}).encode()
    raw = _raw(
        started,
        b"POST /api/session HTTP/1.1\r\n"
        + f"Host: {portal.BIND_HOST}:{started.port}\r\n".encode()
        + b"Content-Type: application/json\r\n"
        + b"Content-Length: 0\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode()
        + body,
    )
    assert " 400 " in _head_of(raw)
    # And the smuggled exchange never happened.
    assert started.state.code is not None


@pytest.mark.parametrize(
    ("label", "sent"),
    (
        ("no request line at all", b""),
        (
            "a declared body that never arrives",
            b"POST /api/session HTTP/1.1\r\nContent-Length: 65536\r\n\r\n",
        ),
    ),
)
def test_a_stalled_connection_is_dropped_instead_of_pinning_a_thread(
    running_portal, monkeypatch, label, sent
):
    """One thread per connection, unbounded and daemon: without a socket
    timeout a few thousand of these would be a local denial of service,
    and they arrive before any Host or token check."""
    # Pin the shipped default first — the monkeypatch below only makes the
    # same rule quick enough to assert on.
    assert portal._Handler.timeout == portal.HANDLER_TIMEOUT_SECONDS
    assert 0 < portal.HANDLER_TIMEOUT_SECONDS <= 60
    monkeypatch.setattr(portal._Handler, "timeout", 0.2)
    started = running_portal
    sock = socket.create_connection((portal.BIND_HOST, started.port), timeout=5)
    try:
        if sent:
            sock.sendall(sent)
        assert sock.recv(4096) == b"", f"{label}: the connection is still open"
    finally:
        sock.close()


# --- writes: compare-and-swap on the config file (DEC-005) ------------
#
# The helper is exercised directly as well as through the routes: the
# compare-and-swap is what stops the page, the wizard, `vocalize chain`
# and a hand edit from silently dropping each other's changes, and it has
# to hold whether or not there is an HTTP request in front of it.


@pytest.fixture
def config_file():
    """The isolated config path (conftest points XDG_CONFIG_HOME at tmp)."""
    path = config_module.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _authed_post(state, path, payload, **extra):
    return _post(state, path, payload, **{TOKEN_HEADER: state.token}, **extra)


def _fingerprint():
    return wizard.fingerprint_config(config_module.config_path())


ONE_SAY = 'chain = ["say"]\n'


def _seed(path, text=ONE_SAY):
    path.write_text(text, encoding="utf-8")
    return text


def test_cas_writes_when_the_file_has_not_changed(config_file):
    _seed(config_file)
    fingerprint = wizard.fingerprint_config(config_file)
    wizard.write_config_if_unchanged(config_file, {"chain": ["say", "kokoro"]}, fingerprint)
    assert config_file.read_text(encoding="utf-8") == 'chain = ["say", "kokoro"]\n'


def test_cas_refuses_a_file_whose_contents_changed_underneath_it(config_file):
    _seed(config_file)
    fingerprint = wizard.fingerprint_config(config_file)
    other = _seed(config_file, 'chain = ["google"]\n')

    with pytest.raises(ConfigChangedError) as refused:
        wizard.write_config_if_unchanged(config_file, {"chain": ["say"]}, fingerprint)

    assert str(refused.value) == wizard.CONFIG_CHANGED
    assert config_file.read_text(encoding="utf-8") == other


def test_cas_refuses_a_file_whose_mtime_changed_underneath_it(config_file):
    """Same bytes, new mtime — a restored copy is still not the file we read.

    This is why the fingerprint is both halves: content alone would call a
    file replaced with an identical copy unchanged, and mtime alone would
    miss a rewrite inside one filesystem timestamp.
    """
    _seed(config_file)
    fingerprint = wizard.fingerprint_config(config_file)
    later = fingerprint["mtime_ns"] + 5_000_000_000
    os.utime(config_file, ns=(later, later))

    with pytest.raises(ConfigChangedError):
        wizard.write_config_if_unchanged(config_file, {"chain": ["say"]}, fingerprint)


def test_cas_creates_the_file_when_the_fingerprint_is_absent(config_file):
    assert not config_file.exists()
    assert wizard.fingerprint_config(config_file) == wizard.ABSENT_CONFIG

    wizard.write_config_if_unchanged(config_file, {"chain": ["say"]}, wizard.ABSENT_CONFIG)

    assert config_file.read_text(encoding="utf-8") == ONE_SAY
    assert stat.S_IMODE(config_file.stat().st_mode) == 0o600


def test_cas_refuses_a_file_created_underneath_an_absent_fingerprint(config_file):
    """O_EXCL is the check here: a file that appeared is a file that changed."""
    fingerprint = wizard.fingerprint_config(config_file)
    assert fingerprint == wizard.ABSENT_CONFIG
    other = _seed(config_file, 'chain = ["google"]\n')

    with pytest.raises(ConfigChangedError) as refused:
        wizard.write_config_if_unchanged(config_file, {"chain": ["say"]}, fingerprint)

    assert str(refused.value) == wizard.CONFIG_CHANGED
    assert config_file.read_text(encoding="utf-8") == other


def test_cas_refuses_a_write_before_it_renders_an_unwritable_config(config_file):
    """A value the renderer cannot write must not truncate the file first."""
    _seed(config_file)
    with pytest.raises(ConfigError):
        wizard.write_config_if_unchanged(
            config_file, {"chain": [["nested"]]}, wizard.fingerprint_config(config_file)
        )
    assert config_file.read_text(encoding="utf-8") == ONE_SAY


# --- writes: the routes -----------------------------------------------


def test_writing_the_chain_rewrites_the_file_and_returns_the_new_fingerprint(config_file):
    _seed(config_file)
    state = _state()

    status, _headers, body = _authed_post(
        state, "/api/chain", {"order": ["say", "kokoro"], "fingerprint": _fingerprint()}
    )

    assert status == 200
    payload = json.loads(body)
    assert payload["ok"] is True
    assert payload["fingerprint"] == wizard.fingerprint_config(config_file)
    assert config_file.read_text(encoding="utf-8") == 'chain = ["say", "kokoro"]\n'
    # And the state the page reads next agrees with the file.
    assert state.file_config["chain"] == ["say", "kokoro"]


def test_writing_the_chain_with_a_stale_fingerprint_is_409_and_changes_nothing(config_file):
    _seed(config_file)
    state = _state()
    stale = _fingerprint()
    other = _seed(config_file, 'chain = ["google"]\n')

    status, _headers, body = _authed_post(
        state, "/api/chain", {"order": ["say", "kokoro"], "fingerprint": stale}
    )

    assert status == 409
    assert json.loads(body)["error"] == wizard.CONFIG_CHANGED
    assert config_file.read_text(encoding="utf-8") == other


def test_writing_the_chain_into_an_absent_file_creates_it(config_file):
    state = _state()
    status, _headers, _body = _authed_post(
        state, "/api/chain", {"order": ["say"], "fingerprint": wizard.ABSENT_CONFIG}
    )
    assert status == 200
    assert config_file.read_text(encoding="utf-8") == ONE_SAY


def test_writing_into_a_file_created_under_an_absent_fingerprint_is_409(config_file):
    state = _state()
    other = _seed(config_file, 'chain = ["google"]\n')

    status, _headers, body = _authed_post(
        state, "/api/chain", {"order": ["say"], "fingerprint": wizard.ABSENT_CONFIG}
    )

    assert status == 409
    assert json.loads(body)["error"] == wizard.CONFIG_CHANGED
    assert config_file.read_text(encoding="utf-8") == other


def test_a_write_keeps_every_other_key_and_table(config_file):
    """The merge base is the file, not the page: nothing it never saw is lost."""
    _seed(
        config_file,
        'voice = "abc"\n'
        'chain = ["say"]\n'
        "\n[stt]\n"
        'model = "base.en"\n'
        "\n[providers.google]\n"
        'voice = "en-US-Neural2-C"\n'
        "monthly_chars = 1000\n",
    )
    state = _state()

    status, _headers, _body = _authed_post(
        state, "/api/chain", {"order": ["google", "say"], "fingerprint": _fingerprint()}
    )

    assert status == 200
    written = config_module.load_config_file()
    assert written["chain"] == ["google", "say"]
    assert written["voice"] == "abc"
    assert written["stt"] == {"model": "base.en"}
    assert written["providers"] == {
        "google": {"voice": "en-US-Neural2-C", "monthly_chars": 1000}
    }


@pytest.mark.parametrize(
    "order", ([], ["say", "say"], ["nope"], ["say", 3], "say", {"say": True})
)
def test_writing_a_bad_chain_is_400(config_file, order):
    _seed(config_file)
    state = _state()
    status, _headers, _body = _authed_post(
        state, "/api/chain", {"order": order, "fingerprint": _fingerprint()}
    )
    assert status == 400
    assert config_file.read_text(encoding="utf-8") == ONE_SAY


def test_writing_an_unknown_provider_in_the_chain_uses_the_cli_wording(config_file):
    _seed(config_file)
    state = _state()

    status, _headers, body = _authed_post(
        state, "/api/chain", {"order": ["say", "nope"], "fingerprint": _fingerprint()}
    )

    with pytest.raises(ConfigError) as expected:
        config_module._validate_chain(["say", "nope"], config_module.config_path())
    assert status == 400
    assert json.loads(body)["error"] == str(expected.value)


def test_writing_a_provider_speed_out_of_range_uses_the_cli_wording(config_file):
    _seed(config_file)
    state = _state()

    status, _headers, body = _authed_post(
        state,
        "/api/provider/say",
        {"settings": {"speed": 9}, "fingerprint": _fingerprint()},
    )

    with pytest.raises(ConfigError) as expected:
        config_module._coerce_speed(
            9, f"'speed' in [providers.say] in {config_module.config_path()}"
        )
    assert status == 400
    assert json.loads(body)["error"] == str(expected.value)
    assert config_file.read_text(encoding="utf-8") == ONE_SAY


def test_writing_a_provider_budget_uses_the_cli_wording(config_file):
    _seed(config_file)
    state = _state()

    status, _headers, body = _authed_post(
        state,
        "/api/provider/say",
        {"settings": {"monthly_chars": -1}, "fingerprint": _fingerprint()},
    )

    with pytest.raises(ConfigError) as expected:
        config_module._validate_providers_table(
            {"say": {"monthly_chars": -1}}, config_module.config_path()
        )
    assert status == 400
    assert json.loads(body)["error"] == str(expected.value)


def test_writing_a_provider_setting_saves_it_and_keeps_the_rest(config_file):
    _seed(config_file, 'chain = ["say"]\n\n[providers.say]\nvoice = "Alex"\n')
    state = _state()

    status, _headers, _body = _authed_post(
        state,
        "/api/provider/say",
        {"settings": {"speed": 1.1}, "fingerprint": _fingerprint()},
    )

    assert status == 200
    assert config_module.load_config_file()["providers"]["say"] == {
        "voice": "Alex",
        "speed": 1.1,
    }


def test_writing_a_null_provider_setting_clears_that_key(config_file):
    _seed(config_file, 'chain = ["say"]\n\n[providers.say]\nvoice = "Alex"\nspeed = 1.1\n')
    state = _state()

    status, _headers, _body = _authed_post(
        state,
        "/api/provider/say",
        {"settings": {"speed": None}, "fingerprint": _fingerprint()},
    )

    assert status == 200
    assert config_module.load_config_file()["providers"]["say"] == {"voice": "Alex"}


@pytest.mark.parametrize(
    "settings",
    (
        {"nope": "x"},  # not a key any [providers.*] table has
        {"voice": 3},  # not a string
        {"voice": "a\nb"},  # a control character in a value bound for argv
        {"voice": "x" * 201},  # unbounded length
        {"monthly_chars": "lots"},
    ),
)
def test_writing_a_bad_provider_setting_is_400(config_file, settings):
    _seed(config_file)
    state = _state()
    status, _headers, _body = _authed_post(
        state, "/api/provider/say", {"settings": settings, "fingerprint": _fingerprint()}
    )
    assert status == 400
    assert config_file.read_text(encoding="utf-8") == ONE_SAY


@pytest.mark.parametrize("name", ("nope", "..", "../../etc/passwd", "SAY"))
def test_writing_to_an_unknown_provider_is_404(config_file, name):
    _seed(config_file)
    state = _state()
    status, _headers, _body = _authed_post(
        state, f"/api/provider/{name}", {"settings": {}, "fingerprint": _fingerprint()}
    )
    assert status == 404
    assert config_file.read_text(encoding="utf-8") == ONE_SAY


def test_writing_the_stt_table_saves_it(config_file):
    _seed(config_file)
    state = _state()

    status, _headers, _body = _authed_post(
        state,
        "/api/stt",
        {
            "settings": {"model": "base.en", "input_device": "MacBook Pro Microphone"},
            "fingerprint": _fingerprint(),
        },
    )

    assert status == 200
    assert config_module.resolve_stt(config_module.load_config_file())["model"] == "base.en"
    assert config_file.read_text(encoding="utf-8").endswith(
        '[stt]\nmodel = "base.en"\ninput_device = "MacBook Pro Microphone"\n'
    )


def test_writing_a_bad_stt_model_uses_the_cli_wording(config_file):
    _seed(config_file)
    state = _state()

    status, _headers, body = _authed_post(
        state,
        "/api/stt",
        {"settings": {"model": "../../etc/passwd"}, "fingerprint": _fingerprint()},
    )

    with pytest.raises(ConfigError) as expected:
        config_module._validate_stt_table(
            {"model": "../../etc/passwd"}, config_module.config_path()
        )
    assert status == 400
    assert json.loads(body)["error"] == str(expected.value)


@pytest.mark.parametrize(
    "settings",
    (
        {"language": "fr"},  # small.en, the default model, is English-only
        {"input_device": "-rf"},  # a device name that is really a flag
        {"input_device": "mi\x07c"},  # a control character in a device name
        {"max_seconds": 0},
        {"cleanup": "yes"},
        {"nope": 1},
    ),
)
def test_writing_a_bad_stt_value_is_400(config_file, settings):
    _seed(config_file)
    state = _state()
    status, _headers, _body = _authed_post(
        state, "/api/stt", {"settings": settings, "fingerprint": _fingerprint()}
    )
    assert status == 400
    assert config_file.read_text(encoding="utf-8") == ONE_SAY


def test_the_stt_language_rule_is_checked_against_the_merged_table(config_file):
    """Half the pair can already be in the file, so the whole table is checked."""
    _seed(
        config_file,
        'chain = ["say"]\n\n[stt]\nmodel = "large-v3-turbo-q5_0"\nlanguage = "fr"\n',
    )
    state = _state()

    status, _headers, _body = _authed_post(
        state, "/api/stt", {"settings": {"model": "small.en"}, "fingerprint": _fingerprint()}
    )

    assert status == 400  # small.en is English-only and the language is already "fr"


@pytest.mark.parametrize(
    "fingerprint",
    (
        None,
        {},
        "unchanged",
        {"mtime_ns": 1, "sha256": "a", "extra": 1},
        {"mtime_ns": True, "sha256": "a"},
        {"mtime_ns": "1", "sha256": "a"},
    ),
)
def test_a_write_with_a_fingerprint_of_the_wrong_shape_is_400(config_file, fingerprint):
    _seed(config_file)
    state = _state()
    status, _headers, _body = _authed_post(
        state, "/api/chain", {"order": ["say"], "fingerprint": fingerprint}
    )
    assert status == 400
    assert config_file.read_text(encoding="utf-8") == ONE_SAY


# --- the key: in, never out -------------------------------------------

CANARY_KEY = "sk-not-a-real-key-canary-0000"


@pytest.fixture
def fake_login(monkeypatch):
    """`auth.login`'s validation seam, with no API call in it."""
    stored = {}

    def fake_validate(key, provider="elevenlabs"):
        stored["validated"] = (provider, key)

    monkeypatch.setattr("vocalize.auth.validate_key", fake_validate)
    return stored


def test_the_login_response_never_contains_the_key(config_file, fake_login, fake_keychain):
    state = _state()

    status, headers, body = _authed_post(
        state, "/api/auth/login", {"provider": "google", "key": CANARY_KEY}
    )

    assert status == 200
    # The whole response, headers included — not just the JSON body.
    assert CANARY_KEY.encode() not in body
    assert CANARY_KEY not in json.dumps(headers)
    assert CANARY_KEY not in "".join(str(value) for value in json.loads(body).values())
    # And it really was stored, so this is not passing by doing nothing.
    assert fake_keychain[("vocalize", "google-api-key")] == CANARY_KEY


def test_a_rejected_key_is_scrubbed_out_of_the_error(config_file, monkeypatch):
    """Messages we did not write quote what they were given."""

    def rejects(key, provider="elevenlabs"):
        raise VocalizeError(f"HTTP 401 for key {key}")

    monkeypatch.setattr("vocalize.auth.validate_key", rejects)
    state = _state()

    status, _headers, body = _authed_post(
        state, "/api/auth/login", {"provider": "google", "key": CANARY_KEY}
    )

    assert status == 400
    assert CANARY_KEY.encode() not in body
    assert b"[key]" in body


def test_the_login_route_never_logs_the_key(running_portal, capsys, monkeypatch, fake_login):
    started = running_portal
    conn = _connect(started)
    conn.request(
        "POST",
        "/api/auth/login",
        body=json.dumps({"provider": "google", "key": CANARY_KEY}),
        headers={TOKEN_HEADER: started.state.token, "Content-Type": "application/json"},
    )
    response = conn.getresponse()
    body = response.read()
    conn.close()

    assert response.status == 200
    assert CANARY_KEY.encode() not in body
    captured = capsys.readouterr()
    assert CANARY_KEY not in captured.out
    assert CANARY_KEY not in captured.err


@pytest.mark.parametrize(
    ("provider", "key"),
    (
        ("polly", CANARY_KEY),  # AWS credentials, nothing to store
        ("say", CANARY_KEY),  # local, no credentials
        ("kokoro", CANARY_KEY),
        ("google", ""),  # the CLI's "nothing was stored"
        ("google", 7),
        ("google", None),
    ),
)
def test_a_login_the_cli_would_refuse_is_400(config_file, provider, key, fake_keychain):
    state = _state()
    status, _headers, _body = _authed_post(
        state, "/api/auth/login", {"provider": provider, "key": key}
    )
    assert status == 400
    assert fake_keychain == {}


def test_a_login_for_an_unknown_provider_is_404(config_file, fake_keychain):
    state = _state()
    status, _headers, _body = _authed_post(
        state, "/api/auth/login", {"provider": "nope", "key": CANARY_KEY}
    )
    assert status == 404
    assert fake_keychain == {}


def test_logout_removes_the_stored_key(config_file, fake_keychain):
    fake_keychain[("vocalize", "google-api-key")] = CANARY_KEY
    state = _state()

    status, _headers, body = _authed_post(state, "/api/auth/logout", {"provider": "google"})

    assert status == 200
    assert fake_keychain == {}
    assert CANARY_KEY.encode() not in body


def test_logout_for_a_provider_with_no_key_slot_is_400(config_file, fake_keychain):
    state = _state()
    status, _headers, _body = _authed_post(state, "/api/auth/logout", {"provider": "say"})
    assert status == 400


# --- the security matrix, over the mutating routes --------------------
#
# Everything the read-only routes are held to, held to on the routes that
# write the config file, store a key, spend money and start a download.
# Each of these also asserts the config file did not move.

MUTATING_ROUTES = (
    "/api/chain",
    "/api/provider/say",
    "/api/stt",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/voices/say/preview",
    "/api/local/install/start",
)

# A body that would be a valid write on every route above if it were ever
# allowed to reach one.
LIVE_BODY = {
    "order": ["kokoro"],
    "settings": {"speed": 1.1},
    "provider": "google",
    "key": "sk-would-be-stored",
    "target": "stt",
}


@pytest.fixture
def guarded_config(config_file):
    """A config file every refusal below must leave exactly as it is."""
    _seed(config_file)
    LIVE_BODY["fingerprint"] = _fingerprint()
    yield config_file
    assert config_file.read_text(encoding="utf-8") == ONE_SAY


@pytest.mark.parametrize("path", MUTATING_ROUTES)
def test_a_mutating_route_refuses_a_token_in_the_query_string(guarded_config, path):
    state = _state()
    status, _headers, _body = _post(state, f"{path}?token={state.token}", LIVE_BODY)
    assert status == 401


@pytest.mark.parametrize("path", MUTATING_ROUTES)
def test_a_mutating_route_refuses_a_token_in_the_body(guarded_config, path):
    state = _state()
    status, _headers, _body = _post(state, path, {**LIVE_BODY, "token": state.token})
    assert status == 401


@pytest.mark.parametrize("path", MUTATING_ROUTES)
def test_a_mutating_route_refuses_a_missing_token(guarded_config, path):
    state = _state()
    assert _post(state, path, LIVE_BODY)[0] == 401


@pytest.mark.parametrize("path", MUTATING_ROUTES)
def test_a_mutating_route_refuses_a_rebound_host(guarded_config, path):
    state = _state()
    status, _headers, _body = portal.route(
        "POST",
        path,
        {"Host": "evil.example", TOKEN_HEADER: state.token},
        json.dumps(LIVE_BODY).encode(),
        state=state,
    )
    assert status == 421


@pytest.mark.parametrize("path", MUTATING_ROUTES)
def test_a_mutating_route_refuses_a_foreign_origin(guarded_config, path):
    state = _state()
    status, _headers, _body = _authed_post(
        state, path, LIVE_BODY, Origin="http://evil.example"
    )
    assert status == 403


@pytest.mark.parametrize("path", MUTATING_ROUTES)
def test_a_mutating_route_refuses_an_oversized_body(guarded_config, path):
    state = _state()
    body = b"x" * (portal.MAX_BODY_BYTES + 1)
    status, _sent, _body = portal.route(
        "POST", path, _headers(state, **{TOKEN_HEADER: state.token}), body, state=state
    )
    assert status == 413


@pytest.mark.parametrize("path", MUTATING_ROUTES)
def test_a_mutating_route_carries_the_security_headers(guarded_config, path):
    state = _state()
    _status, headers, _body = _post(state, path, LIVE_BODY)
    for name, value in portal.SECURITY_HEADERS.items():
        assert headers[name] == value


@pytest.mark.parametrize("path", MUTATING_ROUTES)
def test_a_mutating_route_is_not_reachable_with_a_get(guarded_config, path):
    """A GET is what a link, an <img> or a prefetch would send."""
    state = _state()
    assert _authed_get(state, path)[0] == 404


# --- previews (T-63) ---------------------------------------------------
#
# The preview goes through `chain.run` with the provider forced, which is
# what makes the budget gate, the ledger and the audio cache apply to it
# exactly as they do to `vocalize speak --provider`. The provider module
# is a fake; nothing here reaches a network or a speaker.


class _FakeProvider:
    """One provider module's contract, and a record of what it was asked."""

    DEFAULTS: ClassVar[dict] = {}

    def __init__(self, ext="m4a", audio=b"AUDIO-BYTES", error=None):
        self.AUDIO_EXT = ext
        self.NAME = "say"
        self.audio = audio
        self.error = error
        self.calls = []
        self.checked = []

    def check(self, settings, **kwargs):
        self.checked.append(settings)

    def synthesize(self, text, settings, **kwargs):
        self.calls.append(text)
        if self.error is not None:
            raise self.error
        return self.audio


@pytest.fixture
def fake_provider(monkeypatch):
    provider = _FakeProvider()
    monkeypatch.setattr("vocalize.providers.get", lambda name: provider)
    return provider


@pytest.mark.parametrize(
    ("ext", "content_type"),
    (("mp3", "audio/mpeg"), ("wav", "audio/wav"), ("m4a", "audio/mp4")),
)
def test_a_preview_answers_with_the_audio_and_its_content_type(
    fake_provider, ext, content_type
):
    fake_provider.AUDIO_EXT = ext
    state = _state()

    status, headers, body = _authed_post(state, "/api/voices/say/preview", {})

    assert status == 200
    assert headers["Content-Type"] == content_type
    assert headers["Accept-Ranges"] == "none"
    assert body == b"AUDIO-BYTES"
    assert fake_provider.calls == [portal.PREVIEW_TEXT]
    for name, value in portal.SECURITY_HEADERS.items():
        assert headers[name] == value


@pytest.mark.parametrize("name", ("nope", "..", "say/../google", ""))
def test_a_preview_of_an_unknown_provider_is_404(fake_provider, name):
    state = _state()
    status, _headers, _body = _authed_post(state, f"/api/voices/{name}/preview", {})
    assert status == 404
    assert fake_provider.calls == []


def test_a_preview_spends_the_ledger_and_a_repeat_is_a_cache_hit(fake_provider):
    from vocalize import ledger

    state = _state()

    assert _authed_post(state, "/api/voices/say/preview", {})[0] == 200
    assert ledger.status("say") == (len(portal.PREVIEW_TEXT), False)

    assert _authed_post(state, "/api/voices/say/preview", {})[0] == 200
    # One synthesis, one charge: the second click was served from the same
    # audio cache `vocalize speak` uses.
    assert fake_provider.calls == [portal.PREVIEW_TEXT]
    assert ledger.status("say") == (len(portal.PREVIEW_TEXT), False)


def test_a_budget_capped_preview_is_refused_in_the_chains_own_words(fake_provider):
    from vocalize import chain as chain_module

    file_config = {"chain": ["say"], "providers": {"say": {"monthly_chars": 5}}}
    state = _state(file_config)

    status, _headers, body = _authed_post(state, "/api/voices/say/preview", {})

    with pytest.raises(VocalizeError) as expected:
        chain_module._budget_gate("say", fake_provider, portal.PREVIEW_TEXT, file_config)
    assert status == 402
    assert json.loads(body)["error"] == str(expected.value)
    assert fake_provider.calls == []


def test_an_exhausted_provider_is_refused_before_it_is_asked(fake_provider):
    from vocalize import ledger

    ledger.mark_exhausted("say")
    state = _state()

    status, _headers, body = _authed_post(state, "/api/voices/say/preview", {})

    assert status == 402
    assert "quota" in json.loads(body)["error"]
    assert fake_provider.calls == []


def test_a_failed_preview_is_502_and_never_the_upstream_body(fake_provider):
    from vocalize.exceptions import ProviderTransientError

    fake_provider.error = ProviderTransientError(
        "say", "HTTP 500\n<html><body>internal trace and quota details</body></html>"
    )
    state = _state()

    status, _headers, body = _authed_post(state, "/api/voices/say/preview", {})

    assert status == 502
    error = json.loads(body)["error"]
    assert "\n" not in error
    assert "<html>" not in error
    assert "internal trace" not in error
    assert len(error) <= 200


def test_two_previews_run_one_at_a_time(fake_provider):
    """One module lock, which is also what keeps Kokoro's session single-threaded."""
    state = _state()
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
        answers[label] = _authed_post(state, "/api/voices/say/preview", {})

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
    assert fake_provider.calls == [portal.PREVIEW_TEXT]


def test_a_preview_never_goes_through_the_playback_path(fake_provider, monkeypatch):
    """The browser plays the blob; nothing here takes the machine-wide lock."""

    def never(*args, **kwargs):
        raise AssertionError("a preview must not play audio on this machine")

    monkeypatch.setattr("vocalize.audio.play", never)
    monkeypatch.setattr("vocalize.audio.play_sequence", never)
    monkeypatch.setattr("vocalize.audio._run_tracked", never)
    state = _state()

    assert _authed_post(state, "/api/voices/say/preview", {})[0] == 200


# --- the local install thread (T-63) -----------------------------------
#
# The download seam is `portal.OPENER`, so these exercise the real
# `download_file` — its size and sha256 checks included — without a byte
# leaving the machine. The runtime selftest and the recorder build are
# fakes: both shell out.


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
        chunk = self._blob[self._at:self._at + size]
        self._at += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fake_install(monkeypatch):
    """A one-file STT install with every subprocess and the network faked."""
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
    monkeypatch.setattr(portal, "OPENER", opener)
    monkeypatch.setattr(local_module, "uv_path", lambda: "/usr/bin/true")
    monkeypatch.setattr(
        install_module, "selftest", lambda *a, **k: seen.selftest.append(k) or "ok"
    )
    monkeypatch.setattr(
        install_module,
        "build_recorder",
        lambda *a, **k: (seen.recorder.append(1), ("current", seen.model_dir))[1],
    )
    return seen


def _install_status(state):
    return json.loads(_authed_get(state, "/api/local/install/status")[2])


def _wait_for_install(state, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        status = _install_status(state)
        if status["done"]:
            return status
        time.sleep(0.01)
    raise AssertionError("the install never finished")


def test_an_install_downloads_verifies_stamps_and_warms_the_runtime(fake_install):
    state = _state()

    status, _headers, body = _authed_post(
        state, "/api/local/install/start", {"target": "stt"}
    )

    assert status == 200
    # A snapshot, not a promise: a fast install can be over before the
    # start call has finished serialising its answer.
    assert json.loads(body)["target"] == "stt"
    final = _wait_for_install(state)

    assert final["error"] is None
    assert final["running"] is False
    assert final["downloaded"] == final["total"] == len(fake_install.blob)
    assert fake_install.urls == [fake_install.entry["url"]]
    assert (fake_install.model_dir / "ggml-fake.bin").read_bytes() == fake_install.blob
    assert fake_install.selftest and fake_install.recorder


def test_an_install_reports_progress_while_it_runs(fake_install):
    fake_install.paused = True
    state = _state()

    assert _authed_post(state, "/api/local/install/start", {"target": "stt"})[0] == 200
    assert fake_install.reached.wait(5)

    midway = _install_status(state)
    assert midway["running"] is True
    assert midway["target"] == "stt"
    assert midway["done"] is False
    assert 0 < midway["downloaded"] < midway["total"]
    assert "ggml-fake.bin" in midway["step"]

    fake_install.pause.set()
    assert _wait_for_install(state)["error"] is None


def test_a_second_install_while_one_runs_is_409(fake_install):
    fake_install.paused = True
    state = _state()

    assert _authed_post(state, "/api/local/install/start", {"target": "stt"})[0] == 200
    assert fake_install.reached.wait(5)

    status, _headers, body = _authed_post(
        state, "/api/local/install/start", {"target": "kokoro"}
    )

    assert status == 409
    assert "already running" in json.loads(body)["error"]

    fake_install.pause.set()
    _wait_for_install(state)
    # And once it is over, the next one is allowed.
    assert _authed_post(state, "/api/local/install/start", {"target": "stt"})[0] == 200
    _wait_for_install(state)


def test_a_thread_that_never_starts_frees_the_install_slot(fake_install, monkeypatch):
    """A refused thread must not wedge the slot or suspend the watchdog for good."""
    state = _state()

    def _refuse(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(portal.threading.Thread, "start", _refuse)
    status, _headers, body = _authed_post(
        state, "/api/local/install/start", {"target": "stt"}
    )

    assert status == 503
    assert "could not start" in json.loads(body)["error"]
    assert state.install["running"] is False
    assert state.watchdog_suspended is False

    monkeypatch.undo()
    # And the next install is allowed, rather than a permanent 409.
    assert _authed_post(state, "/api/local/install/start", {"target": "stt"})[0] == 200
    _wait_for_install(state)


def test_the_idle_watchdog_never_fires_during_an_install(fake_install):
    """The page goes quiet while a 488 MB model downloads; that is allowed."""
    fake_install.paused = True
    started = portal.serve({"chain": ["say"]}, idle_timeout=0.05)
    try:
        result = portal.route(
            "POST",
            "/api/local/install/start",
            {"Host": started.state.expected_host, TOKEN_HEADER: started.state.token},
            json.dumps({"target": "stt"}).encode(),
            state=started.state,
        )
        assert result[0] == 200
        assert fake_install.reached.wait(5)
        assert started.state.watchdog_suspended is True
        assert started.wait(0.4) is None  # still up, long past the idle timeout

        fake_install.pause.set()
        _wait_for_install(started.state)
        assert started.state.watchdog_suspended is False
        # And the watchdog is watching again the moment the install ends.
        assert started.wait(5.0) == portal.IDLE_REASON
    finally:
        started.stop()


def test_a_failed_install_reports_one_line_and_leaves_nothing_behind(fake_install):
    fake_install.blob = b"not the file the manifest names".ljust(64, b".")
    state = _state()

    assert _authed_post(state, "/api/local/install/start", {"target": "stt"})[0] == 200
    final = _wait_for_install(state)

    assert final["running"] is False
    assert final["error"] and "\n" not in final["error"]
    assert "checksum" in final["error"]
    assert not (fake_install.model_dir / "ggml-fake.bin").exists()


@pytest.mark.parametrize(
    "payload",
    ({}, {"target": "say"}, {"target": ["stt"]}, {"target": "stt", "model": 7}),
)
def test_an_install_start_with_a_bad_target_is_400(fake_install, payload):
    state = _state()
    status, _headers, _body = _authed_post(state, "/api/local/install/start", payload)
    assert status == 400
    assert fake_install.urls == []


def test_an_install_of_an_unknown_model_downloads_nothing(fake_install):
    """The model name reaches a file path and an argv: allowlist or nothing."""
    state = _state()

    assert _authed_post(
        state, "/api/local/install/start", {"target": "stt", "model": "../../evil"}
    )[0] == 200
    final = _wait_for_install(state)

    assert "Unknown model" in final["error"]
    assert fake_install.urls == []
