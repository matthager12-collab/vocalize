# Run 7 report: portal server — auth bootstrap and read-only state

Branch `config-portal`. Files: `vocalize/portal.py`, `vocalize/assets/portal.html`, `vocalize/assets/portal.js`, `tests/test_portal.py` (139 tests). Suite 1362 passed, 3 skipped; ruff clean.

## Tasks

- **T-60: done** — `route(method, path, headers, body, *, state)` as a plain function; `ThreadingHTTPServer` on `127.0.0.1:0`; one-time code (`secrets.token_urlsafe(32)`) in the URL fragment exchanged once at `POST /api/session` for an in-memory session token carried in `X-Vocalize-Token`; `Host` checked on every route including `/`, `/portal.js` and `/api/session`; five refused exchanges shut the server down; the five security headers on every response, `media-src 'self' blob:` included; the token refused in query string and body; idle watchdog with `watchdog_suspended` for run 8's installs.
- **T-61: done** — `GET /api/state` returns readiness rows, chain and its source, per-provider settings, budgets against the ledger, masked key status and the `[stt]` table. Every credential probe is bounded, and the six of them share one deadline.

## Security gate

Negative tests named in the acceptance criteria, by test id in `tests/test_portal.py`:

| Criterion | Test |
|---|---|
| wrong `Host` refused on every route | `test_wrong_host_header_is_refused_on_every_route` (7 hosts × 6 routes), `test_absent_host_header_is_refused_on_every_route`, `test_duplicate_host_header_is_refused`, `test_host_check_runs_before_the_session_exchange` |
| DNS-rebinding shape and cross-origin | `test_wrong_host_header_is_refused_on_every_route[evil.example]`, `test_cross_site_origin_is_refused_even_with_the_right_host` |
| token never in a query string or body | `test_token_in_the_query_string_is_refused_on_every_route`, `test_token_shaped_query_parameters_are_refused`, `test_token_in_the_request_body_is_refused_and_costs_no_code`, `test_duplicate_token_headers_are_refused`, `test_a_token_prefix_is_refused` |
| code single-use and expiring | `test_session_code_is_single_use`, `test_session_code_expires_sixty_seconds_after_the_start`, `test_session_code_still_works_just_inside_the_window` |
| the sixth wrong code finds the server gone | `test_five_wrong_codes_shut_the_portal_down`, `test_the_sixth_wrong_code_finds_the_server_gone`, `test_lockout_counts_an_expired_code_too`, `test_the_lockout_message_never_calls_a_re_sent_code_a_wrong_one` |
| `/` serves no secret, no inline script | `test_static_routes_need_no_token_and_serve_no_secret`, `test_served_page_has_no_inline_script_and_no_external_url`, `test_the_page_stores_the_token_nowhere_persistent` |
| security headers on every response | `test_security_headers_are_on_every_response`, `test_security_headers_are_the_designed_strings`, `test_headers_never_allow_a_cross_origin_reader`, `test_http_servers_own_refusals_carry_the_security_headers` |
| the key never reaches the page | `test_state_never_contains_the_api_key` (a real stored canary), `test_state_masks_a_key_taken_from_the_environment`, `test_state_survives_a_credential_probe_that_raises` (the exception message is never the response) |
| a hanging provider still answers | `test_state_route_returns_under_a_blocked_probe`, `test_state_survives_a_credential_probe_that_blocks`, `test_state_bounds_every_credential_probe_by_one_deadline` |
| ten polls against a blocked probe start one thread | `test_state_ten_polls_against_a_blocked_probe_start_one_thread` (readiness registry), `test_state_ten_polls_against_a_blocked_key_probe_start_one_thread` (the portal's own) |
| request shape | `test_an_oversized_body_is_refused_with_413`, `test_a_malformed_json_body_is_400`, `test_only_get_and_post_are_accepted`, `test_the_handler_refuses_a_chunked_body`, `test_the_handler_refuses_a_non_decimal_content_length`, `test_the_handler_refuses_two_content_length_headers`, `test_a_stalled_connection_is_dropped_instead_of_pinning_a_thread` |
| nothing is logged, nothing traces back | `test_the_handler_logs_nothing`, `test_a_failing_route_answers_500_with_the_security_headers` |
| non-ASCII secrets do not crash the auth routes | `test_a_non_ascii_token_header_is_refused_with_the_full_header_set`, `test_a_non_ascii_code_is_refused_and_counts_toward_the_lockout`, `test_a_non_ascii_secret_is_never_taken_for_the_real_one` |

**SECURITY: PASS** — no known Critical or High issue open in the run's code.

## Review round fixes (post-implementation)

| Fix | Where |
|---|---|
| `secrets.compare_digest` raised `TypeError` on a non-ASCII code or token header, so the connection dropped with no response, no headers, a traceback on stderr, and no lockout count. Both comparisons now go through `_same_secret`, which compares UTF-8 bytes. | `portal.py` `_same_secret`, `exchange`, `token_matches` |
| `route()` had no exception boundary: a corrupt `usage.json` (or any route bug) closed the socket with zero bytes and printed a traceback naming install paths. Now a fixed-text 500 with the full header set; `_Server.handle_error` says nothing. | `portal.py` `_Handler._handle`, `_Server` |
| `http.server`'s own refusals — an unknown verb (501), an over-long request line (414) — answered with an HTML page carrying none of the security headers and no `X-Frame-Options`. `send_error` is overridden to answer through the same path, and never echoes the request line back. | `portal.py` `_Handler.send_error` |
| No socket timeout: a connection that declared a body and never sent it pinned a daemon thread for the life of the process. `timeout = HANDLER_TIMEOUT_SECONDS` (30 s). | `portal.py` `_Handler.timeout` |
| Duplicate `Content-Length` resolved to the first copy — the classic smuggling shape, and the sibling of the strict-decimal check already there. Two copies are now 400 with the connection closed. | `portal.py` `_Handler._handle` |
| The shipped page never pinged, so the watchdog closed the portal about 60 s after the tab loaded. The page now sends `/api/ping` every 15 s. | `assets/portal.js` |
| `/api/state` joined the six credential probes one after another: a wedged keychain cost `timeout × 6` on the first page load. They now start before the readiness rows and share one deadline. | `portal.py` `_start`/`_collect`, `state_payload` |
| `LOCKOUT_REASON` said "five wrong codes" when an already-used code — what re-opening the URL from history sends — counts too. Now "five refused codes"; design.md § Portal auth states what a refused exchange is. | `portal.py`, `design.md` |
| Test-only: the portal's own probe registry had no ten-poll thread assertion, and `serve()`'s inert `open_browser` default was pinned nowhere (a regression there would have opened a real browser from five tests with the suite green). | `tests/test_portal.py` |

## Divergences from the plan text

1. `route()`'s signature is `route(method, path, headers, body, *, state)` — the plan wrote it without the state parameter; a pure function needs the state passed in.
2. `serve(file_config, *, open_browser=None, port=0, readiness_timeout, idle_timeout)` — `open_browser` defaults to **None**, not `webbrowser.open`, so nothing in a test or script can open a browser. `vocalize portal` (run 8) passes it explicitly.
3. Two headers beyond the three the design names are sent on every response: `Cache-Control: no-store` and `Referrer-Policy: no-referrer`.
4. `Origin` is refused when present and foreign, in addition to the `Host` check — it keeps a cross-site simple POST off the lockout counter.
5. A duplicated `Host` or `X-Vocalize-Token` header is treated as absent rather than resolved to the first copy (`_header`).
6. The lockout counts every refused exchange, not only a wrong guess (documented in design.md § Portal auth this run).
7. `python -m vocalize.portal` exists as a smoke entry because the `vocalize portal` command is T-64 in run 8.
8. The handler refuses `Transfer-Encoding` outright: `http.server` does not decode chunked bodies.

## Deferred

- **`vocalize portal` CLI command** — T-64, run 8. Nothing in `cli.py` imports `portal` yet.
- **Writes, previews, installs** — `/api/chain`, `/api/provider/<name>`, `/api/stt`, `/api/auth/login`, `/api/voices/<name>/preview`, `/api/local/install/*` are run 8 (DEC-005). `PortalState.watchdog_suspended` is the hook they need and is already tested.
- **The real page** — run 9. `assets/portal.html` and `assets/portal.js` are a placeholder that does the code exchange, keeps the ping going and prints the state. Run 9 must keep the three rules in the file header (no inline script, no external resource, token in the closure only) and must keep sending `/api/ping`; `test_the_page_keeps_pinging_so_the_watchdog_leaves_it_open` fails if it stops.
- **Manual browser check** — verification.md's Live check needs the owner at the keyboard; it was not run.

validate-exit: PASS
