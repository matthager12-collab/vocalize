# Report: run 4, keychain through the security tool

Date 2026-09-07. Branch `local-first` (worktree `.claude/worktrees/local-first`). Full account in [task-report.md](./task-report.md); the check in [../decisions.md](../decisions.md) § DEC-035.

- T-30: done — the check passed on every count with no dialog; DEC-035 Decided, A, with the commands and exit codes.
- T-31: done — `_SecurityKeychain` behind `_backend()` on macOS (secret on stdin, read with `-w`, delete with read-back, quote and backslash refused, write verified by read-back); keyring elsewhere; `login` replaces an older item in place with `-U`.
- T-32: done — the validation stamp in the item's comment; `auth status` shows it.
- T-33: done — README, `docs/provider-credentials.md` § The keychain on macOS, CHANGELOG.

Security gate: negative tests in `tests/test_auth.py` (`-k security`) — the key never in argv; a quote or backslash refused before the tool; a failed read-back refuses to claim success; a locked keychain raises rather than returns; the status output never carries the key; the comment is read without `-w`.

Deferred: nothing.

Suite: 1862 passed, 3 skipped. Ruff clean.

validate-exit: PASS
