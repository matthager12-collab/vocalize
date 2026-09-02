# Run 10: Release 0.11.0 (docs, security review, live browser check, publish)

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 8; contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-80 | Docs (README Portal section, `docs/installation.md`), CHANGELOG 0.11.0, version bump | vocalize | Phase 7 | commands in docs exist |
| T-81 | Adversarial review (security lens mandatory: token bootstrap, rebinding, CSRF, clickjacking, key handling) and fixes | vocalize | Phase 7 | zero confirmed critical/high open |
| T-82 | Live check in a browser with the owner; merge and publish on the owner's word | vocalize | T-81 | PyPI 0.11.0 hashes match |

## Role and isolation

- **Role:** Release manager + independent reviewers (security lens mandatory, Opus); the owner for T-82
- **Isolation:** branch `config-portal`; the owner merges and publishes
- **Workload:** README Portal section, docs/installation.md, CHANGELOG, pyproject; review writes `docs/plans/2026-09-next-features/review-0.11.0.md`

## Entry criteria

- on branch config-portal
- run 9 validated
- assets present
- suite green at entry

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- review findings file exists
- no open critical/high finding
- CHANGELOG has 0.11.0
- version bumped to 0.11.0
- README documents the portal
- full suite green
- ruff clean
- work committed
- PyPI 0.11.0 published with matching digest (after the owner publishes)

## Not machine-checkable (owner present)

- Memory with the portal tab and a Kokoro read (verification Manual 0, second pass); live browser check with the owner; owner merges (squash) and publishes; agents never push main.

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-nn: done | partial | skipped — reason`), the security-gate result (the negative tests named in the acceptance criteria, listed with their test ids), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. This is the last run.
