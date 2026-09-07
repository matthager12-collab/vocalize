# Run 10: Release 0.13.0

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 10; contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-90 | CHANGELOG 0.13.0; version bump; docs cross-check | vocalize | — | docs-match-CLI exits 0 |
| T-91 | Adversarial review folded into `review-0.13.0.md` (T-76) plus the Setup tab and integrate paths | vocalize | T-90 | no Critical or High Open |
| T-92 | Owner: manual checks 4–8; squash-merge; publish; digests verified | vocalize | T-91 | digests equal |

## Role and isolation

- **Role:** Release manager and independent reviewer — Opus for the adversarial review (T-91, fresh agent, read-only over Phases 7–9's diff, extending `review-0.13.0.md` from T-76 to cover the Setup tab and the `integrate claude` paths), Sonnet for the docs and version-bump work (T-90); the owner for T-92
- **Isolation:** branch `app`, off `main`, after Phases 7–9 merge into it; the owner merges (squash) and publishes — agents never push `main`
- **Workload:** 3 doc/source files (`CHANGELOG.md`, `vocalize/__init__.py` for the version bump, `README.md`/`docs/` for the cross-check — no code edits) + 1 review file extended in place (`review-0.13.0.md`, opened in Phase 8b's T-76) + 0 test files; this is a docs-and-release phase, no source logic changes

## Entry criteria

- on branch `app`, not `main`
- run 9 (`run-9-doctor-integrate-setup`) validated
- run 9's key artifact present: `readiness.doctor_rows()` (Phase 9 T-80)
- suite green at entry
- ruff clean at entry

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- review file `review-0.13.0.md` exists
- no open critical/high finding in that review (covering Phases 8b, 9, and 10's Setup/integrate extension)
- CHANGELOG has a `0.13.0` heading
- version bumped to `0.13.0` in `vocalize/__init__.py`
- docs match the CLI (the commands that exist at 0.13.0: `listen`, `dictate`, `resume`, `status`, `doctor`, `app install`, `app status`, `integrate claude`, `local install`, `auth login` — `notes` does not exist until 0.14.0)
- full suite green
- ruff clean
- work committed
- package builds and names its wheel `vocalize_cli-0.13.0*` in `dist/`
- clean-venv acceptance: installing that wheel leaks no ML runtime (`pywhispercpp`, `onnxruntime`, `mlx`, `sherpa`, `numpy`, `torch`, `boto3`)
- PyPI 0.13.0 published with a digest matching the local build (after the owner publishes)

## Not machine-checkable

- Owner-present manual checks 4–8 (verification.md § Manual checks): the hotkey spike across three frontmost apps; first install (menu-bar icon, hold-for-3s hotkey, press-to-icon and stop-to-clipboard latency); speak-selection with the Accessibility prompt; relaunch behaviour (`kill -9`, Quit-stays-quit, `app restart`, log out/in); upgrade with the app running.
- Owner squash-merges `app` into `main` and publishes to PyPI — agents never push `main` or publish.

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-nn: done | partial | skipped — reason`), the security-gate result (the negative tests named in the acceptance criteria, listed with their test ids), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run 11 (`run-11-cue-hold-paste`) reads that report as its entry criterion.
