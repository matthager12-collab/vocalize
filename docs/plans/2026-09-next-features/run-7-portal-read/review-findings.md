# Run 7 adversarial review — findings

Workflow `wf_f1826be5-cad`, 2026-09-02. Five review lenses over branch
`config-portal`, each finding independently re-tested by three refuter
agents. 125 agents, 40 raw findings, 10.1M tokens.

**Read the buckets carefully.** 30 refuter agents died on a session
limit part-way through, so eleven findings carry fewer than three votes
and landed in `refuted` by default. They are unverified, not cleared.

## Confirmed — 3/3 refuters agreed (25 raw, ~16 distinct)

1. **[medium]** Any web page can lock the portal out with five unauthenticated simple POSTs  
   `portal.py:505` — The lockout counter is incremented by every failed POST /api/session, and that route needs no token, no CORS preflight and no readable response, so a cross-origin page can shut the user's portal down without knowing anything.

2. **[low]** Non-ASCII token or code raises TypeError: handler crashes, traceback to the user's terminal, attempt not counted  
   `portal.py:477` — secrets.compare_digest() raises TypeError on str arguments containing non-ASCII characters, and neither _token_ok nor _session nor _Handler._respond catches it, so the request dies with no response and a traceback on stderr.

3. **[low]** Methods other than GET/POST and malformed requests bypass the Host check and carry none of the security headers  
   `portal.py:569` — _Handler defines only do_GET and do_POST, so HEAD, OPTIONS, PUT, DELETE and any bad request line are answered by BaseHTTPRequestHandler.send_error with an HTML body, no CSP, no X-Frame-Options, no nosniff, no Cache-Control, and without route() ever running its Host pin.

4. **[low]** Idle watchdog is reset by unauthenticated requests, so a hostile page can hold the portal open indefinitely  
   `portal.py:436` — route() sets `self._seen = time.monotonic()` after the Host check but before the route lookup and token check, so any request with the right Host, including an anonymous GET /, defers the 15-minute shutdown.

5. **[low]** Code, attempt counter and lockout flag are mutated without the lock under ThreadingHTTPServer  
   `portal.py:491` — The single-use check and clear of `_code`, the `_code_attempts += 1`, and the `locked_out` read/set run on concurrent handler threads with no synchronization, although `self._lock` exists and guards only `_suspended`.

6. **[medium]** Listening socket is never closed by code; "server gone" depends on garbage collection  
   `portal.py:338` — stop() calls server.shutdown() on a helper thread but never server_close(), so the bound listener stays open until the ThreadingHTTPServer object is garbage-collected; any in-flight handler thread keeps it alive via self.server, and during that time the kernel keeps accepting connections that nobody will ever service.

7. **[medium]** One malformed ledger entry takes the whole /api/state route down with no response  
   `portal.py:274` — _provider_state and _state catch only VocalizeError/OSError, so any other exception in the per-provider path (ledger.status on a month/provider entry that is not a dict, a non-Row from the open _PROBES seam, etc.) propagates out of route(); socketserver prints a traceback to the user's terminal and closes the connection with no status line and no security headers, hiding every other provider — the exact outcome the code comment at line 260 says it exists to avoid.

8. **[low]** HEAD/OPTIONS/PUT/DELETE bypass route(): stock 501 with no security headers and no Host check  
   `portal.py:569` — _Handler defines only do_GET and do_POST, so any other method is answered by BaseHTTPRequestHandler.send_error(501) before route() runs — no CSP, no X-Frame-Options, no nosniff, no Host pinning — contradicting design § Portal routes ("Every response carries …") and the report's APP-HEADERS claim that the single _reply() exit covers every response.

9. **[low]** Unauthenticated and wrong-token requests reset the idle watchdog  
   `portal.py:436` — route() sets _seen after the Host check but before the token check and route lookup, so a 403 token refusal or a 404 keeps the portal alive; the design's keepalive is the page's authenticated GET /api/ping ("N misses → shutdown"), not any loopback traffic.

10. **[low]** A key probe that fails is reported as "checking" forever, indistinguishable from a pending dialog  
   `portal.py:237` — _key_info maps every non-key_source state to {"source": "checking"}, but readiness._run_probe converts a probe exception into a finished `warn` row ("probe failed: <Type>"), so a probe that raised — not one that is still running — is reported as still checking on every poll, and since its thread is dead each poll re-runs and re-fails it.

11. **[low]** Session state is mutated without the lock the class already holds  
   `portal.py:505` — _code_attempts += 1, _code = None, locked_out and the lock_out() check-then-set all run unsynchronized on ThreadingHTTPServer handler threads, so concurrent /api/session requests can lose increments (more than MAX_CODE_ATTEMPTS tries before lockout) and two concurrent correct-code exchanges both succeed; Portal._lock exists (line 305) but guards only _suspended.

12. **[medium]** Non-ASCII token or code crashes the auth compare instead of refusing  
   `portal.py:477` — `secrets.compare_digest` is called on request-supplied `str` values, and it raises `TypeError` on any non-ASCII character, so a token header or session code containing one escapes `route()` as an unhandled exception rather than a 403.

13. **[medium]** Stdlib error responses bypass route(): no security headers and no Host check  
   `portal.py:563` — `_Handler` only defines do_GET/do_POST and never overrides `send_error`, so every response http.server generates itself (501 for OPTIONS/HEAD/PUT/DELETE, 400 for a bad request line, the drop after an unhandled exception) carries none of the designed headers and is answered regardless of `Host`.

14. **[medium]** Lockout widened from 'wrong codes' to every failed exchange, with no decision record and no run-9 guidance  
   `portal.py:496` — `_refuse_code` counts replays of a used code, expired codes, malformed bodies and body-less cross-origin POSTs toward the five-attempt shutdown, which the design scoped to 'five wrong codes'; the consequence (five page reloads close the portal, and run 9's page must strip the fragment and never retry) is recorded only in report.md, not in decisions.md or run-9's project-plan.

15. **[low]** 413 for an oversized body is answered before the Host check  
   `portal.py:581` — `_Handler._respond` short-circuits to a 413 when Content-Length exceeds MAX_BODY without calling `route()`, so that response is given to any `Host` value, contrary to 'Host on every request'.

16. **[low]** Idle clock replaces 'N misses → shutdown' and is reset by unauthenticated requests  
   `portal.py:436` — The designed keepalive contract (`GET /api/ping`, token-gated, N missed pings → shutdown) is replaced by a 15-minute idle timer that any request resets, including an anonymous `GET /`, so a portal whose page is gone can be kept alive by traffic that never authenticated.

17. **[low]** Fingerprint deferral has no gate: run 8's promise to extend /api/state is unchecked  
   `report.md:155` — Deferring the DEC-005 fingerprint out of `/api/state` is consistent with T-61 (which never lists it), but the hand-off relies on 'Run 8 adds the field to `_state()`' and neither run-8's project-plan nor its validate-exit.sh checks that the field exists.

18. **[low]** stop() relies on garbage collection to release the listening socket  
   `portal.py:335` — `Portal.stop()` calls `server.shutdown()` on a helper thread but never `server_close()`, so the loopback port is released only when the `ThreadingHTTPServer` object is collected; 'the sixth wrong code finds the server gone' therefore holds on CPython by refcount, not by design.

19. **[high]** suspend_idle test passes with the control deleted  
   `test_portal.py:585` — test_idle_watchdog_can_be_suspended never observes a watchdog tick during the suspend window, so a no-op suspend_idle passes it.

20. **[high]** Nothing checks the server binds loopback  
   `test_portal.py:619` — No test reads the bound address; the loopback assertion is made against a string the code hard-codes, not the socket.

21. **[high]** Route negatives trust the table's own auth label  
   `test_portal.py:257` — Every 'on every route' token test filters ROUTES by kind == "token", so a route mislabeled "none" silently drops out of the parametrization and the suite shrinks with no failure.

22. **[medium]** Handler-level body cap has no test  
   `portal.py:578` — The 'refuse before reading' Content-Length pre-check in _Handler._respond (and its bad-Content-Length branch) is exercised by no test; only route()'s post-read check is.

23. **[medium]** Socket release depends on garbage collection, not stop()  
   `portal.py:335` — stop() calls server.shutdown() but never server_close(); the idle and lockout socket tests pass only because CPython refcounting collects the server once the shutdown thread ends.

24. **[low]** Token-refusal 403 not covered by header tests  
   `test_portal.py:356` — test_headers_on_every_refusal only triggers Host refusals; the token-missing/wrong 403 and token-in-query 403 responses have no header assertion.

25. **[low]** MAX_BODY test cannot detect the cap being raised  
   `test_portal.py:181` — The oversized-body tests build their payload from the module's own MAX_BODY, so any value of the constant passes.


## Refuted with a full vote set (genuinely cleared)

- **[low]** Branch and report are two commits behind main; report's baseline line is stale
- **[low]** verification.md Phase 6–7 has no row for T-61 (state under a blocked probe)
- **[low]** Remaining self-reported deviations, judged harmless
- **[medium]** Token-in-query only tested on POST routes

## Unverified — refuters killed by the session limit

- **[low]** (2/3 votes) Keychain and .env branches of _key_row never executed  
  The masked-preview guarantee is tested only for the environment source; the keychain branch (auth.stored_key) and '.env file' branch run in no test despite fake_keychain being available.

- **[high]** (1/3 votes) Unhandled exception in route() drops the connection with no HTTP response  
  _Handler._respond has no catch-all around portal.route(), so any non-VocalizeError exception inside a handler (today: json.dumps in _reply on an unvalidated provider-table value) yields no status line, no security headers and a traceback on the user's terminal, and the page cannot tell it from the portal having exited.

- **[high]** (0/3 votes) DEC-005 fingerprint cannot be taken of the bytes _state actually parsed  
  _state() obtains the config through config.load_config_file(), which returns only the parsed dict; nothing in config.py or wizard.py returns raw bytes or stat alongside the parse, so run 8 has no way to emit an mtime+sha256 fingerprint that is guaranteed to describe the content the page is showing, and the payload cannot distinguish an absent file from an empty one for the "absent" sentinel.

- **[medium]** (0/3 votes) /api/state latency is (wedged probe names × probe_timeout) because joins are sequential  
  readiness() joins each probe for up to probe_timeout one after another, then _state runs three more key probes one after another, so a single blocked keychain makes every poll take 4×2 s with the default chain and 6×2 s with three key providers — on every poll for as long as the dialog is up, not once.

- **[medium]** (0/3 votes) stop() never calls server_close(); the port closes only when the server object is garbage-collected  
  stop() runs server.shutdown() on a daemon thread and drops the reference, but shutdown() only ends the serve_forever loop; the listening socket stays bound and accepting into the backlog until CPython's refcounting happens to free the ThreadingHTTPServer.

- **[medium]** (0/3 votes) Every /api/state poll prints config warnings to the terminal, three times for [stt]  
  _state re-runs load_config_file() (which prints unknown-key warnings) and resolve_stt() (which re-validates and prints again) on every poll, and readiness' input-device probe calls resolve_stt a third time, so a single typo in the config produces a stderr line per poll for the life of the portal.

- **[medium]** (0/3 votes) /api/state error channels are inconsistent and the JSON shape is written down nowhere  
  The payload reports failures through four different shapes — `config_error` (top-level string), `chain_source` overloaded with an exception message, `stt` switching between a settings dict and `{"error": ...}`, and `providers[x].error` merging two unrelated failures with `or` — and no document lists the keys, so run 9 ('mechanical from the route contract', Sonnet) has to reverse-engineer _state() and special-case each.

- **[low]** (0/3 votes) No per-poll cost is stated for run 9 to size its interval against  
  Each poll performs 6-9 keychain reads, up to nine ledger file parses, three [stt] validations and — once dictation is set up — one recorder subprocess, with nothing cached between polls, and neither the code nor the report gives run 9 that number.

- **[low]** (0/3 votes) Auth state is mutated without the lock under a threaded server  
  _session's compare-then-clear of _code and _refuse_code's `_code_attempts += 1` run unlocked on ThreadingHTTPServer handler threads; self._lock (line 305) guards only _suspended, so 'single-use' and 'five attempts' are not enforced under concurrent requests.

- **[low]** (0/3 votes) The single-user-machine assumption is written nowhere run 8 or run 10 can copy it from  
  The module docstring lists five auth properties and never states that any process running as this user can reach the port, read the token from process memory or the fragment from the browser, and drive the page; design.md § Portal auth and DEC-004 do not say it either, and the report asserts 'run 8's `vocalize portal` prints it' about code that does not exist.

- **[low]** (0/3 votes) A previous portal still running is neither detected nor mentioned  
  start() binds a random port with no instance marker under the cache dir, so two `vocalize portal` processes coexist silently, each with its own token, tab and poll loop, and run 8 inherits no hook for mutual exclusion of the install thread.

