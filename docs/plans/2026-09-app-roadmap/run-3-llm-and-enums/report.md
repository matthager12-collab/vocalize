# Report: run 3, llm.py and enums

Date 2026-09-07. Branch `local-first` (worktree `.claude/worktrees/local-first`). Full account in [task-report.md](./task-report.md).

- T-20: done — `vocalize/llm.py` with the `claude-cli` and `anthropic` backends, `_complete`, one boundary sentence, per-feature limits, the egress line only before a real send, the budget gate and ledger record, `validate_anthropic_key`; `ANTHROPIC_*` stripped; `--append-system-prompt` and `--setting-sources ""` verified on the installed Claude Code and pinned.
- T-21: done — cleanup moved out of `dictate.py`; ten cleanup tests live in `tests/test_llm.py` with their argv assertions; the spoken `verbatim` keyword and `--verbatim`; bare `--cleanup` keeps its 0.10 meaning.
- T-22: done — `[stt] cleanup` enum with bool coercion, `[stt] verbatim`, `settings` lines; `local` accepted and refused at run time with the 0.14.0 message.
- T-23: done — `[notes]` parsed, validated, resolved, rendered by the wizard (round-trip test), `settings` lines.
- T-24: done — the fixed "cleaned up by Claude — sent off this Mac" notification; docs § Privacy documents the stderr line.
- T-25: done — the hook passes `--strict-mcp-config` and runs from the temp dir.
- T-26: done — provider text values type-checked on load; CLI and portal tests.
- T-27: done — `auth.KEY_SLOTS`, `[providers.anthropic]` accepted, default budget 2,000,000; the key slot's label, env var, keychain username and validator pulled forward from T-40 (needed by the backend).

Security gate: negative tests in `tests/test_llm.py` — transcript never in argv on either backend; the stored key absent from the `claude -p` environment; the 401 body never echoed; a transport error reported by class name only; the egress line exactly once and only before a real send, none for a missing binary or key; injection-shaped text passed as data; escape sequences stripped from model output; the budget gate refuses before any send; `off` and `local` make no call. Plus `tests/test_speak_options.py` (`--strict-mcp-config`, temp cwd) and `tests/test_config.py` / `tests/test_portal.py` (flag-shaped, empty, oversized and non-string provider values refused).

Deferred: nothing.

Suite: 1852 passed, 3 skipped. Ruff clean.

validate-exit: PASS
