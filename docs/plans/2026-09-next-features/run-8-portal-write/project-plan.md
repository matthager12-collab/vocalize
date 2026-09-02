# Run 8: Portal server: writes, preview, install thread, `vocalize portal` command

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 6b; contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-62 | Writes: chain, provider table, `[stt]` table, key login — through `config._validate_*` and `wizard.write_config_if_unchanged` with compare-and-swap on mtime+sha256 of the file read at page load, sentinel `"absent"` for a missing file and an `O_EXCL` first write | vocalize | T-60 | a file changed on disk between read and write is refused with a reload message; a file created underneath an `"absent"` fingerprint is refused; validators' errors surface as 400 with the CLI's wording; the login response body never contains the submitted key |
| T-63 | Preview endpoint through `chain.run(text, chain=[name], file_config=file_config, forced=True)` (returns `(audio, name, ext)`; never plays) under one module lock (bytes for `fetch → Blob`, `Accept-Ranges: none`) and Kokoro/STT install thread + progress endpoint | vocalize | T-60, T-21 | preview with a monkeypatched provider module: a budget-capped provider returns the CLI's refusal, a repeat request is a cache hit, two concurrent requests run one at a time; install progress dict advances under a fake opener |
| T-64 | `vocalize portal` command: mint, serve, `webbrowser.open("…/#code=…")`, print the URL, loud note that the portal assumes a single-user machine | vocalize | T-60 | CLI test with a fake browser opener |

## Role and isolation

- **Role:** Portal server — judgement, security (Opus); the mutating surface gets its own reviewer before commit
- **Isolation:** branch `config-portal`, after run 7; edits `portal.py`, `wizard.py` (new `write_config_if_unchanged`), `cli.py`
- **Workload:** portal.py (continued), wizard.py compare-and-swap helper, cli.py command, test_portal.py; preview path reuses chain.run under a lock (fake provider module)

## Entry criteria

- on branch config-portal
- run 7 validated
- portal module present
- suite green at entry

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- compare-and-swap helper exists
- writes are safe (cas, absent sentinel, login never echoes key)
- preview respects budgets, cache and the lock
- install thread progress
- vocalize portal command exists
- chain setter and wizard still green with the helper
- full suite green
- ruff clean
- work committed

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-nn: done | partial | skipped — reason`), the security-gate result (the negative tests named in the acceptance criteria, listed with their test ids), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run 9 (`run-9-portal-page`) reads that report as its entry criterion.
