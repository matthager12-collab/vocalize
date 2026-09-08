# Report: run 6, release 0.12.0

Date 2026-09-07. Branch `local-first` (worktree `.claude/worktrees/local-first`). Review in [../review-0.12.0.md](../review-0.12.0.md).

- T-50: done — `__version__` 0.12.0; CHANGELOG `## 0.12.0 - 2026-09-07` with an empty Unreleased above it; docs-match-CLI passes; README `[stt]` reference brought to 0.12.0 (turbo default, cleanup enum, `verbatim`, `beam_size`); the changelog contradiction and the package docstring fixed (found by the review).
- T-51: done — 56-agent adversarial review (five lenses, triage, three refuters per finding, an Opus fixer). 25 raw, 23 triaged, 16 refuted: 8 confirmed (1 high, 4 medium, 3 low), 8 refuted with reasons. The high and all four mediums fixed in this run with red-then-green tests; the three lows left open and named; the 0.11.0 low (no play/resume tmpdir sweep) still open and named. Seven lows past the refutation cap: six fixed, one (fake `security` tool fidelity) open.
- T-52: done — manual checks 1–3 passed with the owner present (below); 0.12.0 published from this worktree's `dist/` with the token injected by `op run` (never seen by the agent); PyPI digests equal the local `shasum -a 256 dist/*`; the owner authorised the squash-merge, tag and GitHub release, executed by the agent after publish.

Security gate (tests, all green):
- F-01 high: `tests/test_http.py::test_a_header_the_transport_refuses_never_quotes_the_key` (2 cases), `tests/test_llm.py::test_an_unexpected_anthropic_failure_keeps_the_raw_transcript`, `tests/test_llm.py::test_a_claude_cli_reply_that_is_not_utf8_keeps_the_raw_transcript`.
- F-03 medium: `tests/test_config.py::test_an_anthropic_budget_of_zero_means_zero_not_the_default`, `tests/test_cli.py::test_usage_shows_a_zero_anthropic_budget_as_a_budget`, `tests/test_llm.py::test_a_zero_anthropic_budget_refuses_every_send`.
- F-04 medium: `tests/test_llm.py::test_a_failed_send_still_consumes_the_budget`.
- F-05 medium: `tests/test_auth.py::test_security_store_deletes_the_old_item_instead_of_updating_it`.
- F-07 medium: `tests/test_auth.py::test_an_elevenlabs_outage_is_not_a_verdict_on_the_key`.
- Past the cap: `tests/test_config.py::test_a_notes_template_may_not_look_like_a_flag`; harness `keyslots` (no voices request for the Anthropic slot).
- Run 5's `test_anthropic_*` negatives unchanged and green.

Incident: two review agents probed the owner's real login keychain (`vocalize-aclprobe*` items, since deleted), which raised "security wants to use your confidential information" dialogs on the owner's screen. The owner clicked Always Allow on some; those grants died with the probe items, and the real `vocalize` item's access list was verified unchanged. A standing rule now forbids agents from touching the real keychain.

Deferred: the three open lows (F-10 empty-verbatim send, F-13 fallback note wording, F-14 Remove hidden behind an env var), the 0.11.0 tmpdir sweep, and the fake-`security` fidelity — all named in the review.

Manual checks (owner present, 2026-09-07 evening):
1. Beam search: `vocalize dictate` on the owner's voice returned "We are going to get pizza and then the merge will happen. Thank you." — "to get" and "the merge" intact. Pass. (The owner's config had a duplicate `[stt]` table from the run 2 turbo check; repaired by hand first, old file kept as `config.toml.dup-2026-09-07.bak`. No vocalize writer appends to that file.)
2. Keychain: `vocalize auth login --provider elevenlabs` from Terminal, no dialog; `vocalize auth status --provider elevenlabs` from Claude Code's shell answered in 0.1 s with source keychain and the stamp; the item's access list now reads `/usr/bin/security` alone. Pass. Observation: the stamp reads 2026-09-08 at 18:45 local, because `_today()` is UTC — a low for a later run.
3. Keys tab: add, test and remove an Anthropic key in the portal (the owner then stored it again to keep it); the portal's terminal output held no key; the stored item is owned by `security` and reads without a prompt. Pass.

Gate defect found and fixed: the "package builds" row deleted and rebuilt `dist/` before the digest row compared it with PyPI, so the digest row could never pass after a publish (the build is not reproducible). The build and clean-venv rows now use a scratch directory and `dist/` keeps the published files; the same fix is applied to the run 10, 12 and 16 gates. The published files were downloaded back into `dist/` and their sha256 equal the pre-publish build recorded before upload.

Suite: 1948 passed, 3 skipped. Ruff clean. Gate 16 of 16 after publish.

validate-exit: PASS
