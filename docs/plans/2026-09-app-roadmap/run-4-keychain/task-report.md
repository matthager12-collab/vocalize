# Task report: run 4, keychain through the security tool

Date 2026-09-07. Branch `local-first`, worktree `.claude/worktrees/local-first`. Executed with implement-spec against [project-plan.md](./project-plan.md); exit gate [validate-exit.sh](./validate-exit.sh); result in [report.md](./report.md); the check is recorded in [../decisions.md](../decisions.md) § DEC-035.

## Changelog

- T-30: the check ran on throwaway items with every command under a timeout: an item written through `security -i` read back from four parent binaries (the worktree Python, the uv-tool Python, Apple's Python, a freshly signed Swift binary) with no dialog; the comment stamp readable without `-w`; `security` deleted and replaced a keyring-written item without a prompt. Decision A recorded with the commands.
- T-31: `auth._SecurityKeychain` (get, set, delete, comment) over `/usr/bin/security`; `_default_backend()` picks it on macOS and keyring elsewhere; `_backend()` stays the test seam. The secret travels on stdin; keys with a double quote or backslash are refused; a write is verified by read-back; `-U` replaces an older item in place.
- T-32: `store_key` stamps `validated <ISO date>` in the item's comment on macOS; `auth.validated_on()`; `vocalize auth status` prints `Validated: <date>` and `keychain (sk-x…, validated <date>)`.
- T-33: README key section, a "The keychain on macOS" section in `docs/provider-credentials.md`, CHANGELOG under Fixed.
- Three exit scripts (runs 4, 7, 13) widened their `DEC-0NN … Status` grep from three lines to five: the decision template puts Status on the fourth line after the heading.

## Skipped

Nothing skipped.

## Learnings

- **The accessing application is the `security` tool, not its parent.** That is the whole mechanism: any process can spawn it and read an item it created, with no prompt. Four parents proved it.
- **`security` could delete and replace an item keyring wrote, without a prompt.** So migration is one `-U` write on the next login; no manual step.
- **`security -i` does not reliably carry a failed command's status out**, hence the read-back after every write.
- **ruff's DTZ rule** wants a timezone-aware `datetime` for the stamp; `_today()` is the seam tests patch.
- **conftest's autouse keychain fake replaces `_backend` for every test**, so the platform decision lives in `_default_backend()` where a test can reach it.
- **A chain that exits on a red gate must write its reports first**, or the next commit ships without them, as this run's did once.

## Alternatives

- **`security add-generic-password -w <key>` on argv.** Rejected: the secret would be visible to every process on the machine for the call's duration; `-i` keeps it on stdin.
- **Fix the decision entry's layout instead of the three gate scripts.** Rejected: every DEC entry shares the template; the scripts were the odd ones out.
- **Delete-then-add for migration.** Rejected: `-U` did the job in one write with no prompt.

## Specification, as built

Identical to [project-plan.md](./project-plan.md), branch A. One addition outside the plan's scope: the three gate scripts' grep width.
