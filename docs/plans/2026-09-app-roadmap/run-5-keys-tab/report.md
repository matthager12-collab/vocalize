# Report: run 5, the Keys tab and the Anthropic slot

Date 2026-09-07. Branch `local-first` (worktree `.claude/worktrees/local-first`). Full account in [task-report.md](./task-report.md).

- T-40: done — `auth.CREDENTIAL_CHOICES` (`PROVIDER_NAMES` plus `anthropic`) is the choice list on `auth login/status/logout --provider`; `auth status` lists the slot when a key is stored; `PROVIDER_NAMES` unchanged. The slot itself (`KEY_SLOTS`, env var, username, label, `validate_key` branch) landed in run 3 (T-20) and is only used here.
- T-41: done — `/api/state` gains `keys` (one entry per slot: label, source, masked, validated); `POST /api/auth/remove/<slot>` reads the keychain back (`probe_keychain`, a locked keychain is a 400, never "nothing stored"); `POST /api/auth/test/<slot>` runs `_check_shape` first, stores nothing, scrubs every message, and answers 502 when the provider could not be reached (not a verdict). The page lists the slots from `keys`, with the stamp, `autocomplete="new-password"`, a clipboard hint, "Test without storing" and a two-click "Remove stored key" (keychain keys only); the Local tab has a select for the cleanup enum.
- T-42: done — `vocalize usage` prints the anthropic row against its 2,000,000 default.
- T-43: done — `test_anthropic_*` in test_portal, test_cli, test_auth (listed below).
- T-44: done — `docs/provider-credentials.md` § Anthropic, README Keys bullet, CHANGELOG.

Security gate (all green):
- `test_anthropic_test_without_storing_leaves_the_keychain_untouched`, `test_anthropic_test_without_storing_reports_a_bad_key_without_it`, `test_anthropic_test_refuses_a_control_character_key_before_any_request` (3 keys), `test_anthropic_test_refuses_an_oversized_key_before_any_request`, `test_anthropic_test_reports_an_unreachable_provider_as_502_not_a_refusal`, `test_anthropic_test_route_never_logs_the_key` (real socket), `test_anthropic_test_of_an_unknown_slot_is_404` — tests/test_portal.py.
- `test_anthropic_remove_reports_the_read_back`, `test_anthropic_remove_that_is_denied_is_a_400_not_a_claim`, `test_anthropic_remove_with_an_unreadable_keychain_is_a_400_not_nothing_stored`, `test_remove_of_an_unknown_slot_is_404_without_reflecting_it`, `test_anthropic_routes_need_the_session_token` — tests/test_portal.py; both routes added to `_WRITE_ROUTES` (non-object body, Host/Origin/token loops).
- `test_anthropic_state_lists_every_key_slot_without_a_key`, `test_state_reports_an_unreadable_keychain_as_error_not_missing` — tests/test_portal.py.
- `test_anthropic_key_reaches_the_security_tool_on_stdin_never_in_argv`, `test_a_key_longer_than_any_provider_issues_is_refused_before_any_request` — tests/test_auth.py; `test_auth_login_stores_a_key_for_anthropic` (key in no output line) — tests/test_cli.py.
- Page: `keyslots` harness scenario (typed key in no text, refused key cleared, accepted key kept, 502 keeps the key, remove armed by a first click, no fetch but the one); `new-password` pinned; the cleanup list cross-checked against `config.STT_CLEANUP_BACKENDS`.

Opus review of the two routes (before commit): no Critical or High. Two Medium (remove trusted `stored_key`, which flattens a locked keychain into "nothing stored"; test turned a transient failure into "refused") and two Low (`_key_row` rendered a locked keychain as "not found"; no key length cap) — all four fixed above with tests, plus the review's two test gaps (`_WRITE_ROUTES`, a read-failure switch on the fake keychain). One Info confirmed no path from the stamp in `Row.action` to the page's `<code>` node.

Deferred: manual check 3 (add, test, remove an Anthropic key in the portal with the owner present) to run 6, as the plan says.

Suite: 1940 tests collected in 0.20s collected, green. Ruff clean. Gate 12 of 12 (its DEC-035 grep window widened to `-A5`, the same fix runs 4, 7 and 13 needed).

validate-exit: PASS
