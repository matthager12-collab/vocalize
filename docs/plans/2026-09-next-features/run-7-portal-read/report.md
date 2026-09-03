# Run 7 report: portal server — auth bootstrap and read-only state

Branch `config-portal`, executed 2026-09-02 against `main` at 0.10.1. It has since
been rebased onto the 0.10.2 release, so its merge-base with `main` is `6a53744`
(0.10.2) — which is also why the two commit hashes below are not the ones the
handoff recorded.

## Tasks

- **T-60: done** — `vocalize/portal.py`: `Portal.route(method, path, headers, body) -> (status, headers, body)` with no socket in it, a `ThreadingHTTPServer` on `127.0.0.1:0` around it, `secrets.token_urlsafe(32)` one-time code exchanged at `POST /api/session` within 60 s for a session token read from the `X-Vocalize-Token` header only, `Host` pinned to `127.0.0.1:<port>` on every request, five failed exchanges shutting the server down with a message, the designed security headers on every response, and an idle watchdog with a re-entrant `suspend_idle()` context manager for run 8's installs.
- **T-61: done** — `GET /api/state` from `readiness()` + chain + `chain_source` + per-provider `resolve_provider_settings` + budgets + ledger usage + masked key status + the resolved `[stt]` table, everything bounded by `probe_timeout` (default `STATE_TIMEOUT = 2.0`).

## Test counts

| | Passed | Skipped |
|---|---|---|
| Entry | 1236 | 3 |
| Exit | 1349 | 3 |

113 tests added, all in `tests/test_portal.py`. `ruff check vocalize hooks tests` clean at both ends.

Those are the counts at handoff. The adversarial review and the fix rounds that
followed it (see **After the handoff**, below) took the branch to 1419 passed / 3
skipped and spread the additions across `tests/test_config.py`,
`tests/test_readiness.py` and `tests/test_dictate.py` as well.

## Commits

| Hash | Subject |
|---|---|
| `777d19c` | Add the portal server: auth bootstrap and read-only state |
| `e53c48c` | Fix run-7 gate: read the version hatch actually reads |

Files added: `vocalize/portal.py`, `tests/test_portal.py`. Files changed: this run's
`validate-exit.sh` and the shared `design.md` (both in Deviations). The fix rounds
after the handoff also touched `vocalize/config.py`, `vocalize/readiness.py` and
their tests.

## Security gate

Three of the negatives are written against `portal.ROUTES` rather than against named
routes, so a route added in run 8 without a `Host` or token check fails these tests
without anyone remembering to extend them. As handed over, the token negatives filtered
`ROUTES` on its own `kind == "token"` column, so a route *mislabeled* `"none"` dropped
silently out of the parametrization and the suite shrank without failing; a fix round
derives `_TOKEN_ROUTES` by path against an explicit `_NO_TOKEN_NEEDED` list instead, and
a guard test fails if either derived list comes out empty.

```text
Security Gate: PASS

Attack surface:
- A new listening socket (loopback, random port) with no framework, no cookies and
  no reverse proxy in front of it: APP-AUTHN, APP-AUTHZ, APP-CSRF, APP-HEADERS,
  APP-INPUT, APP-SECRETS, APP-PATH, APP-LOG.
- Untrusted inputs: the request method, path, query string, every header (`Host` and
  `X-Vocalize-Token` above all), and the request body.
- Sensitive sinks: the auth decision, the served page, the JSON response (which
  carries key status), stderr, and — from run 8 — the config file.

Findings: none open.

Protections verified:
- Authentication (APP-AUTHN): one-time code, single-use, 60 s TTL, compared with
  secrets.compare_digest; session token compared the same way; five failed
  exchanges end the server.
- Authorization (APP-AUTHZ): the token gate runs before dispatch, so the 501 write
  stubs are already behind it; `Host` is pinned before anything else looks at the
  request, so a valid token does not buy a pass on a rebinding attempt.
- CSRF (APP-CSRF): no ambient credential exists — no cookies, and the token is read
  from a header a cross-origin form cannot set. `Host` pinning and
  `frame-ancestors 'none'` are the belt to that brace.
- Secrets (APP-SECRETS): `GET /` and `GET /portal.js` serve a file verbatim with no
  interpolation, so no secret can reach the page; a token offered in a query string
  is refused outright rather than merely ignored; `/api/state` carries `auth.masked`
  previews (4 characters) and never a key; `log_message` is a no-op so no request
  line — and therefore no query string — is ever written to the terminal.
- Input validation (APP-INPUT): 64 KiB body cap applied in the handler before the
  body is read and again in `route()`; JSON parsed inside try/except; a route
  parameter must match `[a-z0-9_-]{1,32}` before any handler sees it.
- Path handling (APP-PATH): no filesystem path is built from a request; the two
  static files are fixed names under the package's own `assets/`.
- Headers (APP-HEADERS): the single `_reply()` exit from `route()` merges
  `SECURITY_HEADERS` into every response, success and refusal alike.
```

Security evidence — RED then GREEN, by mutation (each control broken in turn, the
named selection re-run, then reverted; all seven were caught):

| Control broken | pytest -k | Result |
|---|---|---|
| `Host` pinning removed | `host` | RED |
| token accepted from the query string | `token` | RED |
| CSP dropped from responses | `headers` | RED |
| lockout disabled | `lockout` | RED |
| one-time code made reusable | `session` | RED |
| session token not verified | `token` | RED |
| body size cap removed | `session or headers` | RED |

Negative tests named in the acceptance criteria, by test id (all in
`tests/test_portal.py`):

| Criterion | Test |
|---|---|
| every mutating route refuses token-in-query | `test_token_in_query_refused_on_every_mutating_route` (parameterized over `_MUTATING_ROUTES`, which lives in `tests/test_portal.py` and is derived from `_TOKEN_ROUTES` by path — a fix round moved it out of `vocalize/portal.py`, where it was a test fixture nothing in production read) |
| …even alongside a valid header | `test_token_in_query_refused_even_alongside_a_valid_header` |
| a token in the body does not authenticate | `test_token_in_body_does_not_authenticate` |
| wrong `Host` refused on `/`, `/portal.js`, `/api/session` and every API route | `test_host_mismatch_refused_on_every_route` (parameterized over `ROUTES`) |
| DNS-rebinding-shaped `Host` refused | `test_host_rebinding_shapes_refused` |
| `Host` outranks a valid token | `test_host_checked_before_the_token` |
| `/` serves no secret | `test_session_secrets_never_appear_in_the_served_page` |
| code is single-use | `test_session_code_is_single_use` |
| code expires after 60 s | `test_session_code_expires_after_sixty_seconds` |
| five wrong codes end the server | `test_lockout_after_five_wrong_codes` |
| the sixth request finds the server gone (real socket) | `test_lockout_sixth_request_finds_the_server_gone` |
| every later request is refused after a lockout | `test_lockout_refuses_every_later_request` |
| a missing or wrong token is refused on every API route | `test_token_required_on_every_api_route`, `test_token_wrong_value_refused_on_every_api_route` |
| headers on every response, including refusals and 404/405/413/503 | `test_headers_on_every_authorized_response`, `test_headers_on_every_refusal`, `test_headers_on_404_405_413_and_lockout`, `test_headers_over_a_real_socket` |
| a hanging probe yields a `warn` row and the response returns | `test_state_hanging_probe_yields_a_warn_row_and_the_response_returns` |
| ten polls against a blocked probe start one thread | `test_state_ten_polls_against_a_blocked_probe_start_one_thread`, `test_state_blocked_key_probe_reports_checking_and_returns` |
| the key never leaves in the state payload | `test_state_reports_the_key_source_without_the_key` |
| oversized and malformed bodies fail closed | `test_session_oversized_body_refused_before_parsing`, `test_session_malformed_body_is_refused_not_crashed` |

Residual risk:

- The portal assumes a single-user machine: anything running as this user can reach
  the loopback port and, with the session token, drive the page. This is the design's
  stated model (DEC-004) and run 8's `vocalize portal` prints it. **Understated as
  written**: the loopback port is reachable by every local process, not only this
  user's, and closing the portal needs no token at all — see
  [DEC-018](../decisions.md#dec-018-any-local-process-can-close-the-portal-with-five-origin-less-posts).
  Reading and writing still need the token; the extra reach is availability only.
- The masked preview is four characters of a live API key, sent to the browser over
  loopback behind the token. It is what `vocalize auth status` already prints, and it
  is what design.md § Portal routes asks the route to carry.

## Deviations from the written design

1. **`validate-exit.sh` entry check amended** (commit `e53c48c`). `0.10.0 shipped
   (version on main ≥ 0.10.0)` read `main:pyproject.toml` for a `version = "…"`
   line. This project is `dynamic = ["version"]` and `[tool.hatch.version]` points at
   `vocalize/__init__.py`, so that line has never existed on any commit: the check
   failed with main at 0.10.1 exactly as it had with main at 0.9.1. It now reads the
   file hatch reads. Proven both ways before use — green on `0.10.1`, red on a
   synthetic `0.9.1` and red on an empty file — so it is not a vacuous pass. It
   changes no contract; flagged here because amending a run's own gate is normally
   the owner's call. Recorded as **DEC-017**.
2. **`route()` is `Portal.route`, a bound method,** not a module-level function. The
   signature is the specified one and it takes no socket; the receiver is what holds
   the code, the token and the attempt counter, so a test builds a `Portal`, drives
   `route()`, and throws it away with no module state to reset.
3. **Key status runs through `readiness`'s probe registry on a repurposed `Row`.**
   (Written here as `readiness._run_probe`; a later fix round split that function
   into `_start_probe` / `_join_probe` and deleted it. The mechanism is unchanged.)
   `_key_row()` returns `Row(f"key {name}", <key_source word>, <masked preview>, "")` —
   not a status row, and it never reaches `vocalize status`. Borrowing `Row` is what
   lets the keychain read use the existing one-in-flight-probe registry instead of a
   second threading implementation, per the instruction to reuse it. A state that is
   not one of `key_source()`'s five words means the probe did not finish, and the
   route distinguishes the two ways that happens: `{"source": "checking", "masked":
   null}` only when the row's `detail` is `readiness.STILL_CHECKING` — the probe
   thread is alive and the next poll may answer — and `{"source": "error", "masked":
   null}` for a probe that finished by raising. Reporting both as `"checking"` left
   run 9's page spinning for ever on a probe that was never coming back. (Originally
   written here as the single `"checking"` answer; made three-way by the fix round,
   and `design.md` § `GET /api/state` payload carries the contract.)
4. **Every failed `/api/session` exchange counts toward the lockout,** including a
   replay of an already-used code and a malformed body — not only a wrong guess. The
   server deliberately keeps nothing that could tell a replay from an attack. The
   cost is that reloading the page five times closes the portal; the fix is to run
   `vocalize portal` again, and **run 9's page must not retry the exchange on its own**.
   A cross-origin POST never reaches the counter — it is refused on `Origin` first, so
   any tab open anywhere cannot shut the portal down in five requests. Both halves are
   recorded as **DEC-015** and **DEC-016**.
5. **Two headers beyond the three designed:** `Referrer-Policy: no-referrer` and
   `Cache-Control: no-store`. Additive, and both keep secrets out of places the three
   named headers do not cover.
6. **`GET /api/ping`'s "N misses → shutdown" is one clock, not a miss counter.** The
   watchdog closes the portal after `IDLE_TIMEOUT` (15 minutes) with nothing
   arriving. A separate counter would measure the same thing twice. **Only a
   token-authenticated request resets `_seen`** — the reset sits inside the token
   branch of `route()`, after `_token_ok`. It was written as "any request resets
   `_seen`", and that was the bug the review found: an anonymous `GET /`, a 403
   token refusal or a 404 held the portal open, so a stale or hostile tab could
   defer the shutdown for ever without ever authenticating. Fixed and proved on a
   live socket; only the page keeps the portal open now.
7. **`design.md` gained a `GET /api/state` payload subsection** (commit `7531b6b`,
   36 lines). Not a run-7 handoff edit — it came out of the fix rounds, and the
   original text above claiming this run's only edit outside `vocalize/` and
   `tests/` was its own `validate-exit.sh` was true when written and is no longer.
   Owning it here rather than moving the subsection into this folder: it is the
   payload contract **run 8 and run 9 both build against**, so it belongs in the
   shared design doc, not in a finished run's private folder where two later runs
   would have to go looking for it.

## Deferred

- **The config fingerprint is not in `/api/state`.** DEC-005's mtime+sha256 and the
  `"absent"` sentinel are defined by `wizard.write_config_if_unchanged`, which run 8
  writes (T-62). Inventing the format here would have handed run 8 a contract to
  match rather than one to define. **Run 8 adds the field to `_state()`.**
- **Write, preview and install routes answer 501.** `POST /api/chain`,
  `/api/provider/<name>`, `/api/stt`, `/api/auth/login`,
  `/api/voices/<name>/preview`, `/api/local/install/start` and
  `GET /api/local/install/status` are declared in `ROUTES` with `auth == "token"` so
  the `Host` and token negatives already cover them; only their bodies are missing.
- **`vocalize portal` (T-64) is not added.** `Portal.start()` returns the
  `http://127.0.0.1:<port>/#code=…` URL and `serve_until_stopped()` blocks; the
  command that opens a browser with them is run 8's.
- **No portal files in `vocalize/assets/`.** (The directory itself exists on `main`:
  0.10.2 ships `assets/cues/`.) `/` and `/portal.js` fall back to a built-in placeholder
  and pick the real files up as soon as they exist, so the absence of
  `vocalize/assets/portal.html` stays run 9's own entry guard rather than being
  satisfied by a stub this run left behind.

## Note for the choreography's pre-build table

This script has 11 checks, not the 12 recorded in choreography.md § Pre-build
validation. The pre-build run on this branch was 5 passed / 6 failed (exit 1), with
the failures being the five artifact checks plus the version check described in
Deviations. Recorded here rather than edited into choreography.md, which is another
run's document.

## Exit

Real output of `docs/plans/2026-09-next-features/run-7-portal-read/validate-exit.sh`,
exit status `0`:

```text
=== Entry criteria ===
PASS: on branch config-portal
PASS: 0.10.0 shipped (version on main ≥ 0.10.0)
PASS: suite green at entry

=== Exit criteria ===
PASS: portal module exists
PASS: auth invariants incl. Host on every route and lockout
PASS: security headers on every response
PASS: state route returns under a blocked probe
PASS: CSP string is the designed one
PASS: full suite green
PASS: ruff clean
PASS: work committed

=== Summary ===
Passed: 11 / 11
Failed: 0 / 11
ALL CHECKS PASSED
```

validate-exit: PASS

## After the handoff

Except where a correction is marked in place, everything above is the run as it was
handed over. The branch then went through an adversarial review ([review-findings.md](./review-findings.md) —
five lenses, 40 raw findings) and the fix rounds that closed it, which is why some of
it needed the corrections marked in place. What a reader of this document should know:

- **The security gate block is the transcript from the handoff, not a current
  statement.** Two of its claims were overstated at the time and are only true now
  that the review's findings are closed. `Findings: none open` was written against
  the run's own analysis, not against the review. Specifically:
  - **APP-HEADERS.** `_reply()` was *not* the single exit: `_Handler` defined only
    `do_GET`/`do_POST`, so HEAD, OPTIONS, PUT, a bad request line and an unhandled
    exception were all answered by `BaseHTTPRequestHandler.send_error` with no CSP
    and no `Host` check. `__getattr__` now hands `_respond` back for any `do_*` and
    `send_error` is overridden, so every method reaches `route()` behind the `Host`
    pin. The one shape that still gets no headers gets no response at all: an
    HTTP/0.9 request, which has no status line to put them on, has the connection
    closed on it instead.
  - **APP-AUTHZ / keepalive.** The idle clock was reset before the token check.
    Deviation 6 above has the correction.
- **Other closed findings that moved behaviour**, none of which change a contract
  this document states: `secrets.compare_digest` no longer raises `TypeError` on a
  non-ASCII token or code (it is refused); `_code`, `_code_attempts` and the lockout
  flag are mutated under `self._lock`; `stop()` calls `server_close()` instead of
  leaving the listener to garbage collection; `/api/state` catches an unplanned
  exception per provider instead of dropping the whole response; `_Handler` carries a
  10 s socket timeout so a peer that connects and sends nothing releases its thread;
  and a negative `Content-Length` is refused with the oversized ones rather than
  passing as an empty body. A last round added the two halves of the same hole on the
  way *out*: `_write` swallows an `OSError` from a peer that hung up mid-answer (and
  only an `OSError` — anything else is a real bug and still raises), and `_Server`
  overrides `handle_error` so a dropped connection does not print a traceback over
  the user's terminal.
- **Three decisions this run made without recording them** are now
  [DEC-015, DEC-016 and DEC-017](../decisions.md#round-6). Run 9 must read DEC-015
  and DEC-016 before writing the page's session exchange, and
  [DEC-018](../decisions.md#dec-018-any-local-process-can-close-the-portal-with-five-origin-less-posts)
  for the limitation those two leave behind — the portal can be closed under the page
  by anything running locally, so the page must survive the server disappearing.
- **The `GET /api/state` payload table in `design.md` was wrong in two places and is
  now corrected**: `stt` can never carry an `{"error": …}` (a bad `[stt]` in the file
  surfaces as `config_error` with `stt` holding the defaults), and a bad `chain` in
  the *config file* does not empty `chain` — only a bad `VOCALIZE_CHAIN` does. Both
  shapes are written out under the table, verified against a running portal.
