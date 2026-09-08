# Report: run 1, local-first defaults

Date 2026-09-07. Branch `local-first` (worktree `.claude/worktrees/local-first`), rebased onto `main` at c03cf94 before the gate. Full account in [task-report.md](./task-report.md).

- T-01: done — `DEFAULT_CHAIN = ("kokoro", "say")`, `PROVIDER_NAMES` local-first, the fallback note in `chain._fallback_message` (DEC-022).
- T-02: done — the four pinned literals updated; three fallback-note tests added; four further tests that assumed an all-ok default chain now name their chain or provider.
- T-03: done — README opening and quickstart lead with `vocalize local install`; cloud-key instructions moved under "Providers and fallback"; `pyproject.toml` description and keywords local-first.
- T-04: done — CHANGELOG Unreleased entry with the upgrade note.

Security gate: this phase carries no security-negative tests, as the project plan states; nothing in it touches a prompt, a key path or a subprocess argument. The fallback note is a fixed string.

Deferred: nothing.

Suite: 1769 passed, 3 skipped. Ruff clean.

validate-exit: PASS
