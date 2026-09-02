# Run 8 report: portal server — writes, preview, install thread, `vocalize portal`

Branch `config-portal`. Files: `vocalize/portal.py`, `vocalize/wizard.py`, `vocalize/cli.py`, `vocalize/exceptions.py`, `tests/test_portal.py` (278 tests), `tests/test_cli.py`, `tests/test_wizard.py`, `tests/conftest.py`.

Commits: `246a5db` (writes, CAS helper, `vocalize portal`), `d3df4e2` (preview and install-thread tests), plus the close-out commit below.

## Tasks

- **T-62: done** — writes for the chain, a provider table, the `[stt]` table and key login go through `config._validate_*` and the new `wizard.write_config_if_unchanged(path, data, fingerprint)`. The fingerprint is `mtime_ns + sha256` taken at page load (`wizard.fingerprint_config`), with the sentinel `wizard.ABSENT_CONFIG = "absent"` for a missing file and an `O_EXCL` first write. A mismatch raises the new `exceptions.ConfigChangedError` and answers 409 with a reload message.
- **T-63: done** — `POST /api/voices/<name>/preview` runs `chain.run(..., forced=True)` under one module lock and returns bytes; the Kokoro/STT install runs on a daemon thread with a progress dict polled by the page, and suspends the idle watchdog while it downloads.
- **T-64: done** — `vocalize portal` mints the code, serves, opens `…/#code=…`, prints the URL, and carries the single-user-machine note. `--no-browser` prints the URL and opens nothing; exit 1 on lockout, 0 on the idle watchdog, and Ctrl-C stops the server.

## Security gate

Negative tests named in the acceptance criteria, by test id:

| Criterion | Test (`tests/test_portal.py` unless noted) |
|---|---|
| a file changed under the reader is refused | `test_cas_refuses_a_file_whose_contents_changed_underneath_it`, `test_cas_refuses_a_file_whose_mtime_changed_underneath_it`, `test_writing_the_chain_with_a_stale_fingerprint_is_409_and_changes_nothing` |
| a file created under an `"absent"` fingerprint is refused | `test_cas_refuses_a_file_created_underneath_an_absent_fingerprint`, `test_writing_into_a_file_created_under_an_absent_fingerprint_is_409` |
| a refused write renders nothing unwritable | `test_cas_refuses_a_write_before_it_renders_an_unwritable_config`, `test_a_write_keeps_every_other_key_and_table` |
| validator errors surface as 400 in the CLI's wording | `test_writing_an_unknown_provider_in_the_chain_uses_the_cli_wording`, `test_writing_a_provider_speed_out_of_range_uses_the_cli_wording`, `test_writing_a_provider_budget_uses_the_cli_wording`, `test_writing_a_bad_stt_model_uses_the_cli_wording`, `test_a_write_with_a_fingerprint_of_the_wrong_shape_is_400` |
| the login response never contains the key | `test_the_login_response_never_contains_the_key`, `test_a_rejected_key_is_scrubbed_out_of_the_error`, `test_the_login_route_never_logs_the_key` |
| the mutating routes keep run 7's auth surface | `test_a_mutating_route_refuses_a_token_in_the_query_string`, `…_in_the_body`, `…_a_missing_token`, `…_a_rebound_host`, `…_a_foreign_origin`, `…_an_oversized_body`, `…_is_not_reachable_with_a_get`, `test_a_mutating_route_carries_the_security_headers` |
| preview respects budgets, cache and the lock | `test_a_budget_capped_preview_is_refused_in_the_chains_own_words`, `test_an_exhausted_provider_is_refused_before_it_is_asked`, `test_a_preview_spends_the_ledger_and_a_repeat_is_a_cache_hit`, `test_two_previews_run_one_at_a_time`, `test_a_failed_preview_is_502_and_never_the_upstream_body`, `test_a_preview_never_goes_through_the_playback_path` |
| the install thread cannot be steered off its allowlist | `test_an_install_of_an_unknown_model_downloads_nothing` (a `../../evil` model name reaches a path and an argv), `test_an_install_start_with_a_bad_target_is_400`, `test_a_failed_install_reports_one_line_and_leaves_nothing_behind` |
| one install at a time, and the watchdog resumes | `test_a_second_install_while_one_runs_is_409`, `test_the_idle_watchdog_never_fires_during_an_install`, `test_a_thread_that_never_starts_frees_the_install_slot` |
| `vocalize portal` opens only what it minted | `tests/test_cli.py::test_portal_opens_the_browser_at_the_one_time_url`, `…::test_portal_no_browser_prints_the_url_and_opens_nothing`, `…::test_portal_exits_1_when_the_server_locked_itself_out`, `…::test_portal_exits_0_when_the_idle_watchdog_closed_it`, `…::test_portal_stops_the_server_on_ctrl_c` |

**SECURITY: PASS** — no known Critical or High issue open in the run's code.

## Close-out fix

One uncommitted fix was left in the working tree when the run stopped, and is committed here with the test it was missing:

| Fix | Where | Evidence |
|---|---|---|
| `threading.Thread.start()` raising `RuntimeError` (the OS refusing a thread) escaped `post_install_start` uncaught: the connection dropped with no response, `begin_install` kept the slot claimed, and `watchdog_suspended` stayed true — so every later install answered 409 and the portal never closed itself. The failure path now restores the idle install record, clears the thread and the watchdog flag, and answers 503. | `portal.py` `post_install_start` | RED: `test_a_thread_that_never_starts_frees_the_install_slot` fails with the uncaught `RuntimeError` against `HEAD:vocalize/portal.py`. GREEN: passes with the fix; portal suite 278 passed, ruff clean. |

Severity: **Medium** — a local denial of the portal's own install and shutdown, reachable only by an authenticated page on a machine already out of threads.

## Divergences from the plan text

1. The report was written at close-out from the run's commits, the plan's acceptance criteria and a full gate run — not by the executor during the run. Task-level narrative is thinner than runs 1–7 as a result.
2. `vocalize portal` grew a `--no-browser` flag the plan does not name; it is what makes the CLI test possible without a fake opener at the `webbrowser` module level.
3. The compare-and-swap contract raises `exceptions.ConfigChangedError` (new in this run) rather than returning a status, so `vocalize chain` and the wizard share the same refusal.

## Deferred

- **The portal page itself** — `assets/portal.html` / `portal.js` are still run 7's placeholders. Run 9 (`run-9-portal-page`) builds the real page against these routes.
- **Live browser exercise** — no run has opened the portal in a real browser yet. Run 9 owns that drill.

validate-exit: PASS
