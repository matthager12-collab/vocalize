# Run 3: llm.py and enums

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 3; contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-20 | `vocalize/llm.py` per design § llm.py API with the `claude-cli` and `anthropic` backends, `_complete`, `DATA_BOUNDARY` appended once, `_LIMITS`, `egress()`, the backend check before the egress line, the Anthropic budget gate and ledger record, `validate_anthropic_key`; `_claude_env` removes the three `ANTHROPIC_*` variables; the prompt goes through `--append-system-prompt` if the installed Claude Code accepts it in print mode (checked first; else documented); the flag that excludes user-scope settings (`--setting-sources` or `--safe-mode`, whichever the installed version accepts) is checked the same way and pinned in the argv | vocalize | — | `tests/test_llm.py` green: transcript only on stdin, `--disallowedTools *` and `--strict-mcp-config` in argv, temp cwd, key absent from the fake claude's environment, egress line exactly once before a real send and never for a missing binary or key, 401 body never echoed, `stop_reason` other than `end_turn` handled per feature |
| T-21 | Move `cleanup_transcript` and its constants out of `dictate.py`; `_finish_take` calls `llm.cleanup_transcript(text, stt["cleanup"], stt["verbatim"])`; the spoken first-word `verbatim` keyword; `--verbatim` on `listen`/`dictate`; bare `--cleanup` = configured backend, else `local` if installed, else `claude-cli`; `_FINISH_TIMEOUT` uses `llm.MAX_TIMEOUT` | vocalize | T-20 | the nine existing cleanup tests pass unchanged in their new home; a take starting with "verbatim" is cleaned with the verbatim prompt and the word removed; `--verbatim` does the same |
| T-22 | `[stt] cleanup` becomes the enum `off\|local\|claude-cli\|anthropic` with bool coercion (`true`→`claude-cli`, `false`→`off`); `[stt] verbatim`; `local` accepted by the validator but refused at run time on this release with the message naming 0.14.0; `settings` prints the words | vocalize | T-20 | `tests/test_config.py -k stt` green incl. coercion and the four values; a 0.10 config with `cleanup = true` resolves to `claude-cli` |
| T-23 | `[notes]` table parsed, validated, resolved and **rendered** (`wizard._render_config_text` gains an explicit notes block beside `[stt]`); `settings` prints `notes.summarizer` and `notes.template`; docs say the table is honoured from 0.14.0 | vocalize | — | `tests/test_wizard.py` round-trip: a config carrying `[notes]` survives `vocalize chain kokoro say`; `tests/test_config.py -k notes` green |
| T-24 | Visible egress in dictation: the fixed string "Dictation copied (cleaned up by Claude — sent off this Mac)." joins `_FIXED_NOTIFICATIONS` and is used when `cleaned` is true; `docs/dictation.md` § Privacy documents the stderr line and that notifications stay fixed | vocalize | T-21 | `tests/test_dictate.py -k notify` asserts the string is one of the fixed set and never carries transcript text |
| T-25 | `hooks/speak_options.py::_summarize` gains `--strict-mcp-config` and `cwd=tempfile.gettempdir()` | vocalize | — | `tests/test_speak_options.py` asserts both |
| T-26 | Issue #5: `_validate_providers_table` type-checks `voice`, `model`, `language`, `region`, `profile` (strings, printable, no leading `-`, length-capped) and `speed` as today, raising `ConfigError` naming file, key and expectation | vocalize | — | `voice = 12345` is a `ConfigError`; `/api/state` reports it under `providers.<name>.error`; tests for each key |
| T-27 | Anthropic budget: `[providers.anthropic] monthly_chars` accepted (`KEY_SLOTS` silences the unknown-provider warning), `budget_for("anthropic")` defaulting to 2,000,000 characters a month when unset, ledger entries under `anthropic` | vocalize | T-20 | over-budget `anthropic` call returns `None` with the budget line and no egress; an unset budget is 2,000,000; `vocalize usage` shows the row (T-42 prints it) |

## Role and isolation

- **Model tier:** Opus — a security-sensitive seam: prompts, argv and environment (`llm.py`'s subprocess and HTTP backends, the keychain-adjacent budget gate, and the flags that keep a stored key and the caller's own Claude Code settings out of the `claude -p` child).
- **Branch:** `local-first` (0.12.0), off `main`, continuing the checkout left by run-2-stt-decoding.
- **Isolation:** shared checkout on `local-first`; this run owns `vocalize/llm.py` (new) and is the first to touch `dictate.py`'s cleanup pass, `config.py`'s `[stt]`/`[notes]` tables and `wizard.py`'s renderer since run-1. `hooks/speak_options.py` is edited but stays import-isolated from `vocalize/llm.py` by design (it keeps its own `claude -p` copy).
- **Workload:** 9 unique source files across T-20–T-27 (1 new: `vocalize/llm.py`; 8 edited: `dictate.py`, `cli.py`, `config.py`, `wizard.py`, `auth.py`, `ledger.py`, `portal.py`, `hooks/speak_options.py`) + 8 test files (1 new: `tests/test_llm.py`; 7 edited: `test_dictate.py`, `test_config.py`, `test_wizard.py`, `test_speak_options.py`, `test_portal.py`, `test_cli.py`, `test_ledger.py`); T-20 weighted heaviest — three backends, two argv-shape checks against the installed Claude Code, and the only egress line in the codebase.

## Entry criteria

- Phase 2 (STT decoding) merged: `docs/plans/2026-09-app-roadmap/run-2-stt-decoding/report.md` contains `validate-exit: PASS`.
- Phase 2's key artifact is in place: the `large-v3-turbo-q8_0` row is pinned in `vocalize/local/whisper_manifest.py`.
- The installed Claude Code answers `claude --help` (T-20's flag check needs a real binary to probe).
- On its own branch, not `main`.
- Suite green at entry.
- Ruff clean at entry.

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- backends and boundaries: `tests/test_llm.py` green (stdin-only transcript, pinned argv flags, temp cwd, key never in the child's environment, egress exactly once, 401 body never echoed, budget default)
- cleanup moved intact: the nine original cleanup assertions pass in their new home in `tests/test_llm.py`
- verbatim keyword: the spoken first word and `--verbatim` both take the verbatim path
- enums and coercion: legacy `cleanup = true/false` coerces to `claude-cli`/`off`; `[stt] verbatim` is a real key
- `[notes]` survives a config rewrite (`vocalize chain kokoro say` round-trip)
- hook hardened: `hooks/speak_options.py::_summarize` carries `--strict-mcp-config` and the temp `cwd`
- issue #5: a non-string/oversized provider value (e.g. `voice = 12345`) is a `ConfigError`
- full suite green
- ruff clean
- work committed

## Not machine-checkable

None. Every exit criterion for this phase is machine-checked; `verification.md`'s manual-checks list (1–14) names nothing for Phase 3, and this run needs no owner presence.

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-nn: done | partial | skipped — reason`), the security-gate result (the negative tests named in the acceptance criteria, listed with their test ids — key-in-argv/env/log checks are the ones that must never regress), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run 4 (`run-4-keychain`) reads that report as its entry criterion.
