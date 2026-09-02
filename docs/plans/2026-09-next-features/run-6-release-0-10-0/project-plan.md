# Run 6: Release 0.10.0 (docs, adversarial review, live checks, publish)

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 5; contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-50 | Docs: README (Dictation section, `status`), `docs/dictation.md`, `docs/installation.md` gains a dictation layer, CHANGELOG 0.10.0, version bump | vocalize | Phase 4 | every command in the docs exists in `--help` |
| T-51 | Adversarial review workflow (security, correctness, integration lenses; two refuters per finding with evidence) and fixes | vocalize | Phase 4 | zero confirmed critical/high findings open |
| T-52 | Live verification with the owner present: microphone permission granted to "Vocalize Recorder", a real-voice dictation from TextEdit via ⌃⌥⌘D, `vocalize stop` mid-read unaffected, `status` on the real machine | vocalize | T-50 | verification.md § Manual checks ticked |
| T-53 | Merge and publish on the owner's word; main pushed by the owner | vocalize | T-52 | PyPI 0.10.0 hashes match; clean-venv install pulls no pywhispercpp/numpy |

## Role and isolation

- **Role:** Release manager + independent reviewers (review lenses on Opus; docs on Sonnet); the owner for T-52 and T-53
- **Isolation:** shared checkout on `next-features`; nothing merges to main until the owner says so
- **Workload:** README, docs/dictation.md (new), docs/installation.md, CHANGELOG, pyproject; the review writes `docs/plans/2026-09-next-features/review-0.10.0.md`

## Entry criteria

- on branch next-features
- run 5 validated
- resume seam present
- suite green at entry

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- review findings file exists
- no open critical/high finding
- every documented command exists
- CHANGELOG has 0.10.0
- version bumped to 0.10.0
- dictation doc exists
- wheel builds and pulls no STT runtime
- full suite green
- ruff clean
- work committed
- PyPI 0.10.0 published with matching digest (after the owner publishes)

## Not machine-checkable (owner present)

- Verification § Manual checks 0, 1, 2, 3, 4, 4b, 5 with the owner present (real microphone grant from the real Service, real-voice accuracy, memory with a browser open).
- Owner merges (squash) and publishes; agents never push main. Remove the stale "Vocalize Recorder Spike"/"Vocalize Speech Spike" entries from Privacy & Security first so the real grant is unambiguous.

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-nn: done | partial | skipped — reason`), the security-gate result (the negative tests named in the acceptance criteria, listed with their test ids), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run 7 (`run-7-portal-read`) reads that report as its entry criterion.
