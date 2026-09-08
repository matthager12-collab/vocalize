# Task report: run 3, llm.py and enums

Date 2026-09-07. Branch `local-first`, worktree `.claude/worktrees/local-first`. Executed with implement-spec against [project-plan.md](./project-plan.md); exit gate [validate-exit.sh](./validate-exit.sh); result in [report.md](./report.md).

## Changelog

- New `vocalize/llm.py`: `cleanup_transcript`, `summarize`, `egress`, `validate_anthropic_key`, a private `_complete` that names the three backends once, per-feature limits, the boundary sentence appended once, the usability check before the egress line, the Anthropic budget gate and ledger record.
- `claude-cli` argv is `claude -p "Process the text below." --append-system-prompt <prompt> --model haiku --disallowedTools * --strict-mcp-config --setting-sources ""`, run from the temp dir with `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_BASE_URL` removed. Verified on the installed Claude Code (2.1.x): the appended system prompt reaches the model with `--setting-sources ""`; `--safe-mode` silently dropped it, so it is not used.
- `dictate.py` lost the cleanup pass; `_finish_take` calls `llm.cleanup_transcript(text, backend, verbatim)`; `cleanup_backend()` reads legacy bools; the fixed notification "cleaned up by Claude — sent off this Mac" joins the set.
- `[stt] cleanup` is the enum `off | local | claude-cli | anthropic` with `true`/`false` coerced; `[stt] verbatim`; `--verbatim` on `listen` and `dictate`; `settings` prints `stt.cleanup`, `stt.verbatim`, `notes.summarizer`, `notes.template`.
- `[notes]` table: `KNOWN_NOTES_KEYS`, `NOTES_DEFAULTS`, `NOTES_SUMMARIZERS`, `NOTES_TEMPLATES`, `_validate_notes_table`, `resolve_notes`, validated on load and rendered explicitly by the wizard with a round-trip test.
- `hooks/speak_options.py` passes `--strict-mcp-config` and runs from the temp dir.
- Issue #5: `voice`, `model`, `engine`, `language`, `region`, `profile` must be short printable strings not starting with `-`; refused on load with the file and key named; the portal returns 400 on a write and `config_error` on a read.
- `auth.KEY_SLOTS`, the `anthropic` label, env var and keychain username, and the `validate_key` branch to `llm.validate_anthropic_key`; `[providers.anthropic] monthly_chars` accepted; `budget_for("anthropic")` defaults to 2,000,000.
- Docs: `docs/dictation.md` config table and privacy section; CHANGELOG entries under Changed and Added.

## Skipped

Nothing skipped. One item pulled forward from run 5 (T-40): the `anthropic` entries in `PROVIDER_ENV_VARS`, `PROVIDER_USERNAMES`, `PROVIDER_LABELS` and the `validate_key` branch, because `config.resolve_provider_key("anthropic")` needs them for the backend to resolve a key at all. Run 5 keeps the CLI and portal front doors (`--provider anthropic`, the routes, `usage`'s row).

## Learnings

- **`--safe-mode` drops `--append-system-prompt` in print mode** on this Claude Code; `--setting-sources ""` keeps the prompt and excludes user, project and local settings. Tested with a canary word, not assumed.
- **A multi-line argument through a shell fake needs NUL separation.** Recording argv with `printf "%s\n"` split the system prompt across lines and hid the boundary sentence from the test; `printf "%s\0"` with exactly one trailing terminator removed keeps an empty argument (`--setting-sources ""`) intact.
- **`notes` became a reserved top-level key**, which broke one wizard test that used it as an arbitrary unknown key; any other name still rides through.
- **Type-checking provider values changes the portal's failure shape**: a TOML date under `[providers.say]` is now a `config_error` naming the key instead of a value the page must serialize.

## Alternatives

- **Keep the cleanup prompt in `-p` and skip the system channel.** Rejected once `--append-system-prompt` was shown to work; the transcript is now the only untrusted content in the user turn.
- **A `[cloud]` table with a global switch.** Rejected in planning (review R4, R18); per-feature enums plus the egress line.
- **Cross-module fixture imports for the moved tests.** Rejected for a self-contained fake in `test_llm.py`: no dependence on `test_dictate.py`'s autouse fixtures.

## Specification, as built

Identical to [project-plan.md](./project-plan.md) plus the run-5 fields pulled forward (above). No contract in design.md changed; design § Cleanup and summaries' "verify the flag" clause resolved to `--setting-sources ""`.
