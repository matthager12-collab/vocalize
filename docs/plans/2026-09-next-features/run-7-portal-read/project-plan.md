# Run 7: Portal server: auth bootstrap and read-only state (0.11.0)

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 6a; contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-60 | `vocalize/portal.py`: `route(method, path, headers, body)`; `ThreadingHTTPServer` on `127.0.0.1:0`; one-time code (`secrets.token_urlsafe(32)`) + session token; `Host` check on every request; five wrong codes → shutdown with a message; security headers incl. `media-src 'self' blob:`; token header-only; idle watchdog suspended during installs | vocalize | — | route tests: every mutating route refuses token-in-query; wrong `Host` refused on `/`, `/portal.js`, `/api/session` and every API route; `/` serves no secret; code single-use and expiring; the sixth wrong code finds the server gone |
| T-61 | `GET /api/state` from `readiness()` + chain + settings + budgets + masked keys + the `[stt]` table, with per-provider timeouts | vocalize | T-10, T-60 | a hanging provider yields a `warn` row and the response returns; ten polls against a blocked probe start one thread |

## Role and isolation

- **Role:** Portal server — judgement, security (Opus)
- **Isolation:** branch `config-portal` off main after 0.10.0 merged; new file `vocalize/portal.py` + `tests/test_portal.py`
- **Workload:** portal.py (new, ~500 lines: routing, token bootstrap, headers, threading server — weight ×3) + 1 test file

## Entry criteria

- on branch config-portal
- 0.10.0 shipped (version on main ≥ 0.10.0)
- suite green at entry

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- portal module exists
- auth invariants incl. Host on every route and lockout
- security headers on every response
- state route returns under a blocked probe
- CSP string is the designed one
- full suite green
- ruff clean
- work committed

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-nn: done | partial | skipped — reason`), the security-gate result (the negative tests named in the acceptance criteria, listed with their test ids), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run 8 (`run-8-portal-write`) reads that report as its entry criterion.
