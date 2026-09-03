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

import pytest

import vocalize.readiness as readiness_module
from vocalize import portal as portal_module
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


def test_token_write_routes_are_declared_but_not_built_yet(portal):
    """Run 8's routes answer 501 — behind the same auth as everything else."""
    _exchange(portal)
    for method, path in _MUTATING_ROUTES:
        status, _, body = portal.route(method, path, _authed(portal), b"{}")
        assert status == 501, path
        assert b"run 8" in body


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
