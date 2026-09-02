# Run 1: `vocalize status` (readiness aggregation)

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 1; contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-10 | `vocalize/readiness.py`: `readiness(file_config, *, timeout=2.0) -> list[Row]` — one row per chain link (credential source via `key_source`/`polly_credential_status`, budget vs ledger, Kokoro `installed()`), each probe on a daemon thread joined with the timeout, one in-flight probe per name (module registry), never raising | vocalize | — | a probe that raises yields a `warn` row; a probe blocked on a `threading.Event` that is never set yields a `warn` row within `timeout + 0.5 s`, and a second `readiness()` call returns at once without starting another thread (`threading.active_count()` unchanged) |
| T-11 | `vocalize status` command: colored one-screen output, exit 0 when every row is `ok`, 1 otherwise; `--json` prints the rows | vocalize | T-10 | `vocalize status --json` is valid JSON with `name/state/detail/action` per row; exit code matches the worst state |
| T-12 | Tests: rows for each provider state; timeout enforcement with an uninterruptible fake (Event never set); one thread per name across repeated calls; exit codes; `--json` shape; no keychain touched when `ELEVENLABS_API_KEY` etc. are set; the process exits promptly with a probe still blocked | vocalize | T-10, T-11 | ≥ 14 new tests green |

## Role and isolation

- **Role:** Readiness/status — mechanical once the row contract is fixed (Sonnet)
- **Isolation:** own worktree off `next-features`, in parallel with run 2; touches `vocalize/readiness.py` (new), `vocalize/cli.py` (one command), `tests/test_readiness.py` (new) — no file shared with run 2
- **Workload:** 3 unique files, one new module with a threading seam (weight ×2 for the blocked-probe fixture)

## Entry criteria

- on branch next-features
- suite green at entry

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- readiness module exists
- readiness tests green
- status --json has the row shape
- process exits with a probe still blocked
- status exit code matches worst row (fails on a fail row)
- full suite green
- ruff clean
- work committed

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-nn: done | partial | skipped — reason`), the security-gate result (the negative tests named in the acceptance criteria, listed with their test ids), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run 2 (`run-2-stt-runtime`) reads that report as its entry criterion.
