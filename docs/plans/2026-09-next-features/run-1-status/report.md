# Run 1 report: `vocalize status` (readiness aggregation)

This run's implementation (T-10/T-11/T-12) was already on branch `next-features`
when this pass started. This report covers that implementation plus a
review-findings fix pass applied on top of it, before handoff to run 2.

## Task status

- **T-10** (`vocalize/readiness.py`): done. `readiness(file_config, *, timeout=2.0) -> list[Row]`
  builds one row per chain-link provider on a daemon thread joined with the
  timeout, keeps at most one in-flight probe per name in a module registry,
  and never raises (including on an unrecognized `VOCALIZE_CHAIN`, fixed in
  this pass — see Security gate below).
- **T-11** (`vocalize status` command): done. Colored one-screen output;
  `--json` prints the rows; exit 0 when every row is `ok`, 1 otherwise.
- **T-12** (tests): done. 24 tests in `tests/test_readiness.py` (>= 14
  required), covering every state, the timeout/threading contract, exit
  codes, `--json` shape, and the credential-isolation and never-raises
  guarantees.

## Review-findings fix pass

Four confirmed findings against `vocalize/readiness.py` were fixed at the
root cause in this pass:

1. **`readiness()` no longer raises on a bad `VOCALIZE_CHAIN`.**
   `config.resolve_chain(None, file_config)` is now called inside a
   `try/except VocalizeError`; an unrecognized provider name degrades to a
   single `fail` row (`name="chain"`) instead of propagating `ConfigError`
   out of a function whose whole contract is "never raises". `cli.py`
   needed no change: once `readiness()` cannot raise, `status --json`
   emits valid JSON unconditionally.
2. **A raising probe's exception message is no longer embedded verbatim.**
   `_run_probe`'s exception handler now reports only `type(exc).__name__`,
   never `str(exc)` — `_PROBES` is an intentionally open, unvalidated
   registry (future portal/dictation probes touch subprocesses and the
   network), so an exception's message text is untrusted and may embed
   credential-shaped text the way `auth.scrub()`'s docstring warns about.
   Dropping the message entirely (rather than truncating or pattern-scrubbing
   it) is the only fix that holds regardless of where in the message a
   leaked secret would sit.
3. **This report.** Written now, per the Handoff section below.
4. **`readiness()` drops a provider once it leaves the chain.** Before
   rebuilding probes for the current chain, any `_PROBES`/`_inflight` entry
   whose name is a real provider (`auth.PROVIDER_NAMES`) but is no longer in
   the resolved chain is deleted. Names that aren't real providers (a test's
   or a future caller's own hand-registered probe -- the design's stated test
   seam) are left untouched, so `test_registry_accepts_a_name_not_in_provider_names`
   and `test_repeated_call_reuses_thread_across_calls` still hold.

## Security gate

**SECURITY: PASS.**

Negative/regression tests added or adjusted for this pass, plus the
existing negative tests they sit alongside (all in `tests/test_readiness.py`):

| Property | Test id |
|---|---|
| Bad `VOCALIZE_CHAIN` degrades to a row, never raises | `test_bad_vocalize_chain_env_var_degrades_to_a_row_never_raises` |
| `status --json` stays valid JSON under a bad `VOCALIZE_CHAIN` | `test_status_json_still_valid_with_bad_vocalize_chain_env_var` |
| An unknown provider already in a config-supplied chain never raises | `test_unknown_provider_in_chain_never_raises` |
| A raising probe's exception message never leaks (credential-shaped canary) | `test_raising_probe_never_leaks_exception_message` |
| A raising probe still yields a `warn` row (type name only) | `test_raising_probe_yields_warn_row` (adjusted: no longer asserts the raw message) |
| `status` never prints the API key value, JSON or plain | `test_status_never_prints_the_api_key_value` |
| `ELEVENLABS_API_KEY` in the environment never touches the keychain | `test_env_var_key_never_touches_keychain` |
| A provider removed from the chain disappears from the next call | `test_stale_provider_dropped_when_chain_changes` |
| Stale-pruning never touches a hand-registered, non-provider name | `test_stale_pruning_leaves_non_provider_names_alone` |
| A blocked probe (`Event` never set) yields `warn` within `timeout + 0.5s` | `test_blocked_probe_yields_warn_within_timeout` |
| A second call reuses the in-flight thread (no thread-per-poll leak) | `test_repeated_call_reuses_thread_across_calls` |
| The process exits promptly with a probe still blocked | `test_process_exits_promptly_with_a_probe_still_blocked` |

Full suite: 830 passed, 1 skipped (`pytest tests/ -q -p no:cacheprovider`).
Ruff: clean (`ruff check vocalize hooks tests`).

## Deferred / divergences

None of the four findings were skipped or deferred -- all four were fixed
and covered by a new or adjusted test.

One pre-existing, out-of-scope observation: `validate-exit.sh`'s "Entry
criteria" section includes `readiness module does not exist yet`, which is
a pre-build-only check (per the script's own comments and per
`choreography.md` Pre-build validation section, run once before any run
started on 2026-09-02). Re-running the full script now -- after the module
has been built across two implementation passes -- necessarily fails that
one entry check, dragging the script's overall exit code to 1 even though
every **exit** criterion (the eight lines under "Exit criteria", the set
this run is actually gated on per `project-plan.md`) passes. This is a
known template property, not a regression from this pass, and was left
unchanged as out of scope for the four findings above.

## validate-exit.sh, real run

```
=== Entry criteria ===
PASS: on branch next-features
PASS: suite green at entry
FAIL: readiness module does not exist yet (exit 1)

=== Exit criteria ===
PASS: readiness module exists
PASS: readiness tests green
PASS: status --json has the row shape
PASS: process exits with a probe still blocked
PASS: status exit code matches worst row (fails on a fail row)
PASS: full suite green
PASS: ruff clean
PASS: work committed

=== Summary ===
Passed: 10 / 11
Failed: 1 / 11
SOME CHECKS FAILED
```

**Exit criteria: 8/8 PASS.** Overall script exit is non-zero solely because
of the pre-build-only entry check explained above -- not because any exit
criterion failed. `validate-exit: PASS` on every criterion this run is
actually gated on (project-plan.md Exit criteria section); the raw script
exit code is 1 for the documented reason above, not a real regression.

Re-run by the orchestrator after removing the pre-build-only entry guard (2026-09-02): 10/10 checks pass.

validate-exit: PASS
