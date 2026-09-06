# Adversarial review: vocalize 0.11.0 (T-81)

**Date:** 2026-09-05
**Scope:** the config portal as it stands on `main` at `9742ac0` plus the release commit `97cdeb5` — runs 7, 8 and 9 (`vocalize/portal.py`, `vocalize/assets/portal.html`, `vocalize/assets/portal.js`) and the CLI, auth and provider surfaces the portal reaches into.
**Lenses:** auth-surface (token bootstrap, DNS rebinding, CSRF, clickjacking — mandatory for this release), key-handling, release.
**Process:** every finding was put to three independent refuters with evidence of their own (a live portal on 127.0.0.1, a loopback provider fake, a real Chromium, a rebuilt sdist and wheel, or a red-then-green test). A finding entered the table below as **confirmed** only when at least two refuters could not refute it; a finding all three refuted is recorded as **refuted**, with the reason, rather than dropped.

Contract changes the fixes forced are recorded as [DEC-021](./decisions.md#dec-021-the-contract-changes-the-0110-release-review-forced).

All four confirmed findings are fixed in this run's single commit on `release-0-11-0`, the one commit past `97cdeb5` (`git log --oneline 97cdeb5..HEAD`). Each fix has a test that fails without it; the red was produced by inverting the fix and re-running, then restoring.

## Findings

| Severity | Title | File | Status | Resolution |
|---|---|---|---|---|
| medium | `vocalize voices` and `vocalize usage` print the ElevenLabs API key to stderr when the API echoes it | `vocalize/tts.py` | fixed | This run's commit (DEC-021a). `auth.scrub` existed and its docstring named this exact path, but the scrub lived in the callers and three of them — `cli.voices`, `cli.usage`, `wizard._voice_step` — never called it. Fixed once, below all three: `build_client` stashes the key on the client it builds, and new `tts._safe` renders every wrapped SDK exception through `auth.scrub`. Tests: `test_list_voices_does_not_echo_a_key_the_api_quoted_back` (the reviewer's own repro — the real SDK against a loopback 500 whose body quotes the key), `test_get_usage_does_not_echo_a_key_the_sdk_quoted_back`, `test_build_client_stashes_the_key_for_the_scrub` (against the *real* client, so an SDK that refused the attribute fails the suite rather than silently un-scrubbing). |
| medium | Portal's `say` voice list reliably times out on first fetch | `vocalize/portal.py` | fixed | This run's commit (DEC-021b). `GET /api/voices/<name>` was bounded by `probe_timeout` (`STATE_TIMEOUT`, 2 s) for every provider, and `say -v '?'` measures 2.0-2.7 s on the reference Mac — so the one provider needing no key, no account and no network failed on every first ask. New `BUILTIN_VOICES_TIMEOUT` (8 s, under `_Handler.timeout`'s 10 s) applies to every provider outside `LIVE_VOICE_LISTS`; a live list still gets `probe_timeout` and nothing more. Test: `test_an_offline_listing_gets_a_longer_budget_than_a_network_probe`. |
| low | `auth.masked()` shows a key of four characters or fewer in full, on the portal page and in `vocalize auth status` | `vocalize/auth.py` | fixed | This run's commit (DEC-021c). `masked()` now returns `"…"` alone below eight characters. Tests: `test_masked_hides_a_key_too_short_to_preview` (parametrized over `a`, `ab`, `abc`, `abcd`, `abcdefg`) and `test_state_never_reproduces_a_short_key_whole`, which drives the finding's own repro — `ELEVENLABS_API_KEY=abc` through the live `/api/state` route. |
| low | The portal's Keys tab sends the user to Keychain Access to remove a key, when `vocalize auth logout` exists and verifies the removal | `vocalize/assets/portal.js` | fixed | This run's commit (DEC-021d). Copy change only, as the finding recommended — no delete route, no new attack surface. The sentence now names `vocalize auth logout --provider <name>`. Test: `test_the_keys_hint_names_the_command_that_removes_a_key` cross-checks the sentence against the CLI's own command and its `--provider` option, so a rename fails the suite rather than a user's terminal. |
| medium | sdist ships the full internal planning tree and test suite | `pyproject.toml` / the built sdist | refuted | All three refuters rebuilt the sdist and agreed the file listing is accurate — `tests/`, the whole `docs/plans/2026-09-next-features/` tree, `.github/workflows/`, `hooks/` and `.env.example` are in it, and none of them is in the wheel. What none of them could sustain is the severity: the repository is public, so the sdist ships nothing that is not already readable on GitHub; `.env.example` carries no secret; and shipping tests in the sdist is the normal hatchling default, not a leak. No confidential material was identified in any of the listed paths. |
| low | ElevenLabs SDK floor (`>=2.0`) is unverified against the pagination shape the code depends on | `pyproject.toml` / `vocalize/tts.py` | refuted | All three refuters refuted it, on two grounds. The gap is real but already known and deliberately deferred: run 9's `report.md` § Deferred records it almost verbatim, and the "verified on 2.66.0 only" wording the finding attributes to a code comment is that Deferred bullet, not a comment in `tts.py`. And `list_voices` already guards the shape it reads — `getattr(response, "voices", response)`, `getattr(response, "has_more", False)`, `getattr(response, "next_page_token", None)` — so an SDK whose response object lacks the pagination fields degrades to a single page rather than raising. |

### Carried from the 0.10.0 review

| Severity | Title | File | Status | Resolution |
|---|---|---|---|---|
| low | No stale-tmpdir sweep for TTS playback/resume temp directories, unlike the dictation sweep in the same release | `vocalize/cli.py` | open | Still open, and [review-0.10.0.md](./review-0.10.0.md) said "Tracked for 0.11.0" — so this record says plainly that 0.11.0 did not do it. `vocalize/cli.py:355` and `:553` still create `vocalize-play-*` / `vocalize-resume-*` directories with no sweep behind a hard kill. The 0.10.0 reasoning stands unchanged: those directories hold synthesized speech that is already cached under `~/.cache/vocalize`, so a leak costs disk only, and `chain.py`'s `vocalize-*` prefix is too broad to sweep safely from another process. Not re-fixed here because the portal release is not where it belongs, and a wrong sweep (deleting a directory a live read is using) is worse than the leak. |

**No confirmed critical or high finding exists in this review, and none is open.** The four confirmed findings are two mediums and two lows, all fixed with tests. The one open row is a low carried forward from 0.10.0, named here rather than quietly dropped.

## What each lens checked and found clean

The absence of a finding is only evidence if the check is written down. Each bullet below is a check a lens actually ran, against a live portal on 127.0.0.1, a loopback provider fake, a real Chromium, or the built artifacts — never a reading of the code alone.

### Auth-surface lens (mandatory: token bootstrap, rebinding, CSRF, clickjacking)

**Token bootstrap**

- One-time code is single-use: replaying an already-exchanged code returns 403 "already been used" and burns a strike (`POST /api/session`, against a live portal).
- The code expires: after `CODE_TTL` (60 s) the real code is refused with "expired".
- The code never leaves the browser: `start()` puts it only in the URL fragment; the page's exchange POSTs it in the request **body** (browser network log showed `POST /api/session` with no query string); `history.replaceState` strips the fragment from the address bar and the history entry; `log_message` is a no-op, so no request line is ever logged.
- The code is not in the process list: the default `webbrowser` controller is `MacOSXOSAScript('default')`, which pipes the AppleScript — URL included — to `osascript` via stdin, not argv. Both AppleScript branches use stdin.
- The token is accepted from the header only: `X-Vocalize-Token` in any case works; the token in `?token=`, `?X-Vocalize-Token=`, `?TOKEN=`, a `Cookie`, or `Authorization: Bearer` all return 403. Header plus query is refused by the token-in-URL gate before auth. A prefix of the token is refused (`compare_digest`).
- The token is not extractable by another origin: cross-origin `fetch` of `/api/state` and `/` throws; `iframe.contentDocument` is null with a SecurityError; `sessionStorage` is keyed by port and per-origin; `/portal.js` loaded cross-origin runs inert and is served verbatim with no secret in it; error bodies are fixed strings (a junk `/api/session` body did not reflect a `SECRETMARKER` back), and `from None` drops the cause chain on the login path.

**DNS rebinding**

- The `Host` pin holds on every route: `Host: evil.example:PORT` returns 403 on all 13 routes plus `/nope` and a traversal path — the static page and every error path included.
- Every `Host` variant is refused: `evil.example` with no port, bare `127.0.0.1`, `localhost:PORT`, `127.0.0.1.:PORT`, `0177.0.0.1:PORT`, `2130706433:PORT`, `[::1]:PORT`, `PORT+1`, tab/`#`/`?` appended to the authority, and a missing `Host` header. Only the exact `127.0.0.1:<port>` is accepted.
- Duplicate `Host` headers: first-value-wins (good-then-evil 200, evil-then-good 403), matching `http.server`; no bypass. Absolute-form request targets gain nothing — the `Host` header is what is validated.

**CSRF**

- No ambient credential: a real Chromium no-cors simple POST (`text/plain`) to `/api/chain` and `/api/stt` wrote **nothing** — the config directory was never created and `/api/state` stayed serveable. A real `text/plain` `<form>` POST and a `navigator.sendBeacon` to `/api/session` from a cross-origin page did not decrement the strike counter.
- The `Origin` gate: `Origin: http://evil.example` is 403 on every route; `Origin: null` refused; scheme, host and port mismatches (https, localhost, no port, `PORT.evil.com`, trailing space, uppercase scheme) all 403. Cross-origin `/api/session` posts do not burn a lockout strike (`attempts_left` held across three attempts).
- No CORS grant: no `Access-Control-*` header on any response; a real preflighted fetch failed with the OPTIONS returning 403 and the browser aborting. `__getattr__` routes OPTIONS/PUT/TRACE/HEAD/unknown methods through `route()`, so they get 405 behind the `Host` pin rather than `http.server`'s bare 501.

**Clickjacking**

- `frame-ancestors 'none'` **and** `X-Frame-Options: DENY` on `GET /` and on every other response, including 404/405/413/431/400/505/403 and `http.server`'s own `send_error` path. A real cross-origin iframe of `/` was blocked (`net::ERR_BLOCKED_BY_RESPONSE`, null `contentDocument`, SecurityError on access).

**Everything else this lens ran**

- Lockout (DEC-018, not re-reported): five wrong same-origin/no-`Origin` codes shut the portal down, release the listening socket and exit the process — verified by the port being freed and the process gone.
- Config-write injection: a provider voice/model value with a newline is refused at the boundary (`isprintable`); quotes and backslashes are escaped and round-trip through `tomllib` without injecting a key or a table; the CAS fingerprint is shape-checked (`mtime_ns` a digit string, exactly `{mtime_ns, sha256}`), so no extra key rides along and a JS-rounded number is refused.
- The CSP's `style-src 'unsafe-inline'` relaxation has no exploitable partner: `portal.js` uses no `innerHTML`/`outerHTML`/`insertAdjacentHTML`/`document.write`/`eval`/`new Function`; the only `setAttribute` calls set fixed `aria-*`/`role`; `eval` and inline `<script>` are blocked in-page.
- Body and header limits: the 64 KiB cap answers 413 with headers; negative and non-numeric `Content-Length` are refused as oversized and do not burn a code attempt; an over-large header block answers 431 with CSP; HTTP/0.9 gets no answer at all rather than a header-less body; a malformed request line is 400 with headers; a bad version is 505 with headers.
- Request smuggling / pipelining: the protocol stays HTTP/1.0 (close after each request); a duplicate `Content-Length` does not parse trailing bytes as a second request, and a pipelined second request is ignored.
- Suite: `tests/test_portal.py` + `tests/test_portal_assets.py`, 466 passed against an isolated HOME/XDG under `/tmp`.

### Key-handling lens

- A canary key stored through the portal's own `POST /api/auth/login` (fake keychain) was then hunted in **every** response body of **every** route on a live server: `/`, `/portal.js`, `/api/session`, `/api/state` (three polls), `/api/ping`, `/api/chain`, `/api/provider/google`, `/api/voices/google`, `/api/voices/elevenlabs`, `/api/local/install/status`, and the login success body. Zero hits. The only places it appeared were the keychain entry and the outbound `X-goog-api-key` header.
- Portal stdout and stderr across four provider-fake modes (ok, raise-with-key, 401-body-with-key, redirect-`Location`-with-key): zero canary hits, and stderr was zero bytes even for the unplanned exception.
- Every file under an isolated HOME / `XDG_CONFIG_HOME` / `XDG_CACHE_HOME` after each run — `config.toml`, the ledger, the audio cache: `grep -rl` found nothing, in all four modes.
- A provider raising `ValueError` with the key in its message during login answers the fixed "The portal hit an internal error."; a 401 whose body carries the key answers `google: invalid or missing API key…`; a 302 with the key in `Location` answers `google: unexpected redirect (HTTP 302) refused`. Key absent from all three.
- `GET /api/voices/elevenlabs` with the real SDK pointed at a loopback fake echoing the key in a 500 body answered the fixed `VOICES_FAILED` line — key absent from the response and from stderr. `readiness._start_probe` keeping only the exception type is what holds this. (This is the portal half of confirmed finding 1; the CLI half had no such guard.)
- The page and script are read verbatim from `vocalize/assets/` and never templated, so no secret can be interpolated into either.
- Browser storage holds only the session token, under `vocalize-portal-token:<port>`. The key is never written to `sessionStorage`, `localStorage` or a cookie; the input is `type=password` with `autocomplete=off` and is cleared after the POST on success and refusal alike. No `<form>` element exists, so no key can reach a URL.
- The preview path cannot encode a key in a filename: `cache.cache_key` hashes voice, model, output format, text, speed, provider, language and region only.
- `providers/_http`'s opener refuses every redirect — driven at a real loopback 302, the redirect target logged **zero** requests. `_http.request` refuses a non-HTTPS URL before building a request (`http://`, `file:///etc/passwd`, `ftp://`).
- TLS verification is on and disabled nowhere: a loopback HTTPS server with a self-signed cert failed with `CERTIFICATE_VERIFY_FAILED`, the server logged zero requests, and the surfaced error says only "network error: URLError". `grep` for `verify=False`, `CERT_NONE`, `_create_unverified_context`, `check_hostname` and `ssl.` across the package: no hits, and `tests/test_http.py:112` enforces the first three on every run.
- The real client from `tts.build_client` has `follow_redirects=False` and base URL `https://api.elevenlabs.io`; driven at a loopback 302, the second port logged zero requests.
- The key travels only in the header each provider expects, observed on the wire: `xi-api-key`, `X-goog-api-key`, `Authorization: Bearer`. Never in a URL, query string or body. Every provider endpoint is a hardcoded `https://` literal — none is config-derived or overridable.
- Model downloads carry no credentials and refuse an https-to-http redirect.
- Submitted values are not echoed where a mis-wired form could put a key: the key as a `provider` field returns the allowlist; as a `voice` value, the shape error; as an install `model`, the model allowlist.
- The mask reveals the **first** four characters. For the three key formats vocalize accepts (`sk_`+hex, `sk-proj-…`, `AIza…`) that is 0-1 characters of entropy — less than the last four would reveal. The length floor was the real gap, and is confirmed finding 3.
- Request lines are never logged, so a secret in a query string cannot reach a log; `route()` refuses `?token=` / `?x-vocalize-token=` with 403 before anything parses the path.
- Full suite green at review time under an isolated HOME: 1755 passed, 3 skipped.

### Release lens

- `Host`-header pin, `Origin` check, and token-in-query refusal re-verified independently of the auth-surface lens, including on `GET /api/state`.
- Path traversal on the static route (`/../../../../etc/passwd`) and on a parameterized provider route (percent-encoded `..%2f`) both 404, no filesystem escape.
- HEAD and OPTIONS both 405 with the full security headers.
- Negative `Content-Length` on `/api/session` is 413 before the code-exchange counter sees it. Duplicate `Content-Length` uses the first value; no smuggling path given non-persistent HTTP/1.0.
- Malformed request line is 400 with security headers via the `send_error` override; HTTP/0.9 gets no response rather than an unsecured one.
- The five-wrong-codes lockout actually closes the listening socket (the sixth request is refused) — DEC-015/DEC-018, not re-reported.
- Compare-and-swap (DEC-005): a fingerprint submitted as a JSON number is 400; a fingerprint dict with an extra key is 400; an "absent" fingerprint creates the file once and is 409 on a second use.
- `POST /api/auth/login`: a key with whitespace or control characters is 400 before any network call; `polly` and `kokoro` are refused with fixed messages and no network; an unknown provider is 404; the key value is never echoed.
- Unicode injection shapes (RTL override U+202E, zero-width space) in provider free-text fields are rejected by the `isprintable()` check; `NaN`/`Infinity` are rejected by `_coerce_speed`'s range check.
- `GET /api/voices/<name>`: an unknown-but-regex-valid name is a clean 404 from the allowlist; an uppercase name never reaches the allowlist, refused by the lowercase-only route regex first.
- `portal.html` / `portal.js`: no `innerHTML`, `document.write`, `eval`, inline `<script>` or dynamic `.style` assignment anywhere.
- CSP, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy` and `Cache-Control` present on every response tested, success and error alike.
- Fresh venv from the built wheel: `vocalize --version` is 0.11.0, and `--help` exits 0 for every subcommand named in `README.md` and `docs/installation.md` (auth, auth login, auth status, chain, clip, config, dictate, listen, local install, local status, portal, resume, settings, status, stop, usage, voices).
- `vocalize portal --no-browser` in that venv starts, prints the one-time link, and `GET /` with the correct `Host` returns the real page, not the placeholder.
- Importing `vocalize.portal` in that venv (declared deps only) pulls in stdlib plus `click` — no eager `elevenlabs`, `keyring` or `boto3` import.
- Wheel contents: 0.11.0 in the filename and METADATA; `vocalize/assets/portal.html`, `portal.js` and all three `cues/*.wav` present; no `tests/`, `.venv/`, `docs/`, `.claude/` or scratch files.
- Full suite: 1755 passed, 3 skipped, 0 failed, with HOME/XDG redirected under `/tmp`.

## Verification of the fixes

Each fix was proved by inverting it and watching the new test go red, then restoring it. The suite after all four fixes: **1766 passed, 3 skipped**, `ruff check` clean.

| Fix | Red (fix inverted) | Green (fix in place) |
|---|---|---|
| `tts._safe` | `test_list_voices_does_not_echo_a_key_the_api_quoted_back` failed with the canary visible in the message: `body: {'detail': {…'rejected key sk-canary-echo-0123456789abcdef'}}`; the other two also failed | all three pass; the same message renders `'rejected key [key]'` |
| `BUILTIN_VOICES_TIMEOUT` | `test_an_offline_listing_gets_a_longer_budget_than_a_network_probe` failed `assert 502 == 200` | passes |
| `masked` floor | all five parameters of `test_masked_hides_a_key_too_short_to_preview` failed | pass, and `test_state_never_reproduces_a_short_key_whole` passes |
| Keys-tab copy | `test_the_keys_hint_names_the_command_that_removes_a_key` failed on `"Keychain Access" not in script` | passes |

Two live re-runs of the reviewers' own repros, outside the suite, with `HOME`/`XDG_CONFIG_HOME`/`XDG_CACHE_HOME` redirected under `/tmp`:

- The real console entry point (`vocalize.cli:run`) against a loopback fake returning a 500 whose body quotes the key: `vocalize voices` and `vocalize usage` both exit 1 and print `rejected key [key]`. Canary count in stdout + stderr: **0** for each, against **1** each before the fix.
- A real `Portal` on a cold cache, `GET /api/voices/say`: **200, 74 voices, 2.66 s**. With `BUILTIN_VOICES_TIMEOUT` forced back to the old budget in the same process: **502 at 2.01 s**, the failure the finding describes.

## Residual risk

- **The `raise … from exc` chain in `tts` still carries the unscrubbed SDK error as `__cause__`.** The message is scrubbed; the cause is not, because `providers.elevenlabs.validate` classifies auth failure from a passing wobble by reading `exc.__cause__`. The shipped entry point (`cli:run`) catches `VocalizeError` and prints the message, so a traceback is not reachable through the CLI — but a future caller that lets the exception escape to a traceback would re-expose the key. Named here rather than fixed, because `from None` would break the classification.
- **An offline voice list that genuinely hangs now costs 8 s instead of 2** before the page shows "couldn't fetch the list". Deliberate: it trades a slower failure for a listing that succeeds.
- **No stale-tmpdir sweep for TTS playback/resume temp directories** — the open low row above, carried from 0.10.0 and still open.
- **The sdist ships the planning tree and the test suite** (the refuted medium). Refuted on severity, not on fact: the repository is public and nothing confidential was found in those paths, but anyone who wants a smaller sdist has to add the excludes deliberately.
- **The ElevenLabs pagination shape is verified against SDK 2.66.0 only**, with a floor of `>=2.0` (the refuted low). The `getattr` guards degrade to a single page rather than raising, and this remains recorded in run 9's Deferred section.
- **T-82 is not closed by this review.** The owner-present live browser check, the merge and the publish are outstanding by design — an agent may not do them, and this document covers T-81 only.
