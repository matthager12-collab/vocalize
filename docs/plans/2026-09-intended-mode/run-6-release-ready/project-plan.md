# Run 6: Phase 5 (T-50 to T-52): ready for the owner's release

Part of [the plan](../plan.md); order and handoffs in [choreography.md](../choreography.md). Executed with implement-spec, one task at a time, on a branch cut from `main`. Starts from main after run 5 merged. The PyPI release and the owner's own config are theirs.

## Entry criteria

The entry block of [validate-exit.sh](validate-exit.sh) passes.

## Tasks

| # | Task | Files | Acceptance (exact test file) |
|---|---|---|---|
| T-50 | CHANGELOG Unreleased, README dictation paragraph, docs settings table | `CHANGELOG.md`, `README.md`, `docs/dictation.md` | Every new key and the Quick Action are named |
| T-51 | A proposed config block for the owner, written to `docs/plans/2026-09-intended-mode/run-6-release-ready/mat-config.toml` | plan folder | The file parses as TOML and holds `cleanup = "local"`, `warm_minutes = 15` and a `vocabulary` list |
| T-52 | Owner-present check, verification.md § Manual checks | The owner's Mac | The owner's notes recorded under `## Owner check` in spike-notes.md |

Every builder and reviewer brief says: set `HOME` and `XDG_CONFIG_HOME` to a scratch directory for any command that reads or writes vocalize state. Reviews come from another model family (plan.md § Roles).

## Exit criteria

`bash validate-exit.sh` exits 0, with the real checkout's `pytest` output kept. Then the branch is pushed, CI is green, and it is squash-merged (Published code class: tests kept, scrub clean).
