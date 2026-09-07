# Run 5: Keys tab and the Anthropic slot

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 5; contracts in [design.md](../design.md) § Key slots and the Keys tab; proof commands in [verification.md](../verification.md) § Phase 5 exit (keys tab).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-40 | `auth.KEY_SLOTS`; `anthropic` in `PROVIDER_ENV_VARS`, `PROVIDER_USERNAMES`, `PROVIDER_LABELS`; `validate_key` branches to `llm.validate_anthropic_key`; every `--provider` choice on `auth login/status/logout/validate` accepts `KEY_SLOTS + ("polly",)` | vocalize | T-20 | `vocalize auth login --provider anthropic --stdin` stores under `anthropic-api-key` in the fake keychain; `PROVIDER_NAMES` unchanged |
| T-41 | Portal: `_provider_or_404` and the `/api/state` key listing accept `KEY_SLOTS`; `POST /api/auth/remove/<slot>` and `POST /api/auth/test/<slot>` (the test route runs `_check_shape` first and `scrub` on every returned message); the page gets remove and test buttons, `autocomplete="new-password"`, the validated stamp, the clipboard hint, and a select for the cleanup enum | vocalize | T-40 | `tests/test_portal.py -k "auth or keys"` green: responses never carry a key, remove reports the read-back, test stores nothing, a control-character key is refused before any request, an unknown slot is 404 |
| T-42 | `vocalize usage` prints the anthropic ledger row | vocalize | T-27 | test |
| T-43 | Security negatives for the slot: key never in argv, stderr or JSON; test-without-storing leaves the keychain untouched | vocalize | T-40, T-41 | tests named `test_anthropic_*` green |
| T-44 | Docs: `docs/provider-credentials.md` Anthropic section (the budget default and its per-Mac scope); README Keys tab paragraph | vocalize | T-41 | present |

## Role and isolation

- **Role:** Python config and chain engineer (Sonnet), scoped to the Keys tab and the Anthropic key slot; the two new portal routes (`POST /api/auth/remove/<slot>`, `POST /api/auth/test/<slot>`) get an Opus review — argv, stderr and response-body checks for a leaked key — before either is committed.
- **Isolation:** branch `local-first`, after Phase 4 (the keychain engineer's work on `vocalize/auth.py`) has landed on the same branch.
- **Workload:** 7 source files (2 new sections in existing files, no new modules: `vocalize/auth.py`, `vocalize/cli.py`, `vocalize/portal.py`, `vocalize/assets/portal.html`, `vocalize/assets/portal.js`, `docs/provider-credentials.md`, `README.md`) + 3 test files (`tests/test_auth.py`, `tests/test_cli.py`, `tests/test_portal.py`); the two new routes in `portal.py` are the one file with security blast radius (a key leaking into a response, a header or a log) — Opus review applies there specifically.

## Entry criteria

- on branch `local-first`, not `main`
- Phase 4 (`run-4-keychain`) validated: its `report.md` carries `validate-exit: PASS`
- Phase 4's key artifact exists: DEC-035 (keychain backend, branch A or B) is recorded as Decided in `decisions.md`
- suite green at entry
- ruff clean at entry

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status or output check; a pre-build run must show the artifact checks failing.

- the anthropic slot is reachable everywhere: CLI, portal state, auth commands (tests)
- the two new auth routes are safe: no key in any response, test-without-storing leaves the keychain untouched, a control-character key is refused before any request, an unknown slot is 404 (tests)
- portal page discipline holds: no bare inline `<script>` tag
- portal page discipline holds: no external URL in the page or its script
- full suite green
- ruff clean
- work committed

## Not machine-checkable

- **Manual check 3 (owner present, deferred to Phase 6/`run-6-release-0-12-0`).** Add, test, remove an Anthropic key in the portal; the key never appears in the page or the terminal. This run needs no owner (`Owner needed: no`); the check is exercised for real with the owner present at the 0.12.0 release run, per verification.md § Manual checks.

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-nn: done | partial | skipped — reason`), the security-gate result (the negative tests named in the acceptance criteria, listed with their test ids, plus the Opus review's findings for the two new routes), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. `run-6-release-0-12-0` reads that report as its entry criterion.
