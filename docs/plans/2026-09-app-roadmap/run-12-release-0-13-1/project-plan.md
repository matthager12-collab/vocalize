# Run 12: Release 0.13.1

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 12; contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-110 | Review of the dictation changes appended to `review-0.13.0.md` § 0.13.1 (Phases 11 and 11b) | vocalize | — | no Critical or High Open |
| T-111 | Owner: manual checks 9–11 and 15–16; squash-merge; publish; digests | vocalize | T-110 | digests equal |

## Role and isolation

- **Role:** Independent reviewer and release manager — Opus for the review (T-110, fresh agent, read-only over Phases 11 and 11b's diff, appending a `§ 0.13.1` section to `review-0.13.0.md`); the owner for T-111
- **Isolation:** branch `hold-to-talk`, off `main`, after Phase 11b merges into it; the owner merges (squash) and publishes — agents never push `main`
- **Workload:** 0 source files, 0 test files, 1 existing review file extended in place (`review-0.13.0.md`, opened in Phase 8b's T-76, extended by Phase 10's T-91, extended again here for both Phase 11 and Phase 11b); no code edits in this phase — CHANGELOG and the version bump to 0.13.1 are Phase 11's T-105, not this run's

## Entry criteria

- on branch `hold-to-talk`, not `main`
- run 11b (`run-11b-playback-pause`) validated
- run 11b's key artifact present: `interrupted.wait_for_record` (Phase 11b T-106); run 11's `_trim_cue` is proven transitively by run 11b's own entry check
- suite green at entry
- ruff clean at entry

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- review file `review-0.13.0.md` exists and carries a `0.13.1` section
- no open critical/high finding in that review
- docs match the CLI (the commands that exist at 0.13.1: `listen`, `dictate`, `pause`, `resume`, `status`, `doctor`, `app install`, `app status`, `integrate claude`, `local install`, `auth login` — `notes` does not exist until 0.14.0)
- full suite green
- ruff clean
- work committed
- package builds and names its wheel `vocalize_cli-0.13.1*` in `dist/`
- clean-venv acceptance: installing that wheel leaks no ML runtime (`pywhispercpp`, `onnxruntime`, `mlx`, `sherpa`, `numpy`, `torch`, `boto3`)
- PyPI 0.13.1 published with a digest matching the local build (after the owner publishes)

## Not machine-checkable

- Owner-present manual checks 9–11 and 15–16 (verification.md § Manual checks): the cue spike across the built-in and Bluetooth inputs (six numbers); the cue trim itself on both inputs ("Start." followed immediately by the first word, no "Start" in the transcript); hold-to-talk end to end plus the "Copied, not pasted (window changed)" case with `paste = true`; a real Kokoro and cloud read paused and resumed; the `stop_hotkey = "pause"` chord end to end.
- Owner squash-merges `hold-to-talk` into `main` and publishes to PyPI — agents never push `main` or publish.

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-nn: done | partial | skipped — reason`), the security-gate result (the negative tests named in the acceptance criteria, listed with their test ids), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run 13 (`run-13-spikes`) reads that report as its entry criterion.
