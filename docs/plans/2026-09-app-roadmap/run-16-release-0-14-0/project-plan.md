# Run 16: Release 0.14.0

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 16; contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-150 | CHANGELOG 0.14.0; version bump; docs cross-check | vocalize | — | docs-match-CLI exits 0 |
| T-151 | Adversarial review (untrusted-input tracing: audio file → transcript → prompt → note; the worker; the manifest; the joined multi-segment take from run 15b) in `review-0.14.0.md` | vocalize | T-150 | no Critical or High Open |
| T-152 | Owner: manual checks 12–14 and 17–18; squash-merge; publish; digests | vocalize | T-151 | digests equal |

## Role and isolation

- **Role:** Release manager and independent reviewer — Opus for the adversarial review (T-151, fresh agent, read-only over Phases 13–15b's diff, tracing untrusted input from the source audio/text file through the transcript, the summarizer prompt, the worker and the manifest into the note, plus the joined multi-segment take from run 15b), Sonnet for the docs and version-bump work (T-150); the owner for T-152
- **Isolation:** branch `notes`, off `main`, after Phases 13–15b merge into it; the owner merges (squash) and publishes — agents never push `main`
- **Workload:** 3 doc/source files (`CHANGELOG.md`, `vocalize/__init__.py` for the version bump, `README.md`/`docs/` for the cross-check — no code edits) + 1 new review file (`review-0.14.0.md`) + 0 test files; this is a docs-and-release phase, no source logic changes

## Entry criteria

- on branch `notes`, not `main`
- run 15b (`run-15b-recording-pause`) validated
- run 15b's key artifact present: `_join_segments` in `vocalize/dictate.py` (Phase 15b T-148)
- suite green at entry
- ruff clean at entry

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- review findings file exists (`review-0.14.0.md`)
- no open critical/high finding in that review
- CHANGELOG has a `0.14.0` heading
- version bumped to `0.14.0` in `vocalize/__init__.py`
- docs match the CLI (the commands that exist at 0.14.0: `listen`, `dictate`, `resume`, `pause`, `status`, `doctor`, `notes`, `app install`, `app status`, `integrate claude`, `local install`, `auth login`)
- full suite green
- ruff clean
- work committed
- package builds and names its wheel `vocalize_cli-0.14.0*` in `dist/`
- clean-venv acceptance: installing that wheel leaks no ML runtime (`pywhispercpp`, `onnxruntime`, `mlx`, `sherpa`, `numpy`, `torch`, `boto3`)
- PyPI 0.14.0 published with a digest matching the local build (after the owner publishes)

## Not machine-checkable

- Owner-present manual checks 12–14 and 17–18 (verification.md § Manual checks): the Parakeet spike jargon-clip pass with the owner reading; the 60-minute real-audio pass through `vocalize notes` (wall time, memory, any silence looping, the local cap, one note written, the summary read for sense, peak combined RSS during the summary with a Kokoro read, Claude Code and a browser open); cloud paths (`--summarizer claude-cli` and `--summarizer anthropic` on a 5-minute file — one egress line each, `left_machine: true` in both notes, a transcript-only note when the model is uninstalled); a paused-and-resumed dictation with the seam stopwatch on built-in and Bluetooth input (17); a take over ten minutes across three pauses (18).
- Owner squash-merges `notes` into `main` and publishes to PyPI — agents never push `main` or publish.

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-nn: done | partial | skipped — reason`), the security-gate result (the negative tests named in the acceptance criteria, listed with their test ids), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run 17 (`run-17-optional-spikes`) reads that report as its entry criterion.
