# Run 6: Release 0.12.0

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 6; contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-50 | CHANGELOG 0.12.0; version bump; README and docs cross-checked against `--help` | vocalize | — | the docs-match-CLI command in verification.md exits 0 |
| T-51 | Adversarial review (security lens over `llm.py`, the keychain backend, the Keys routes) written to `review-0.12.0.md` with a findings table (Severity, Status) | vocalize | T-50 | no Critical or High row left Open |
| T-52 | Owner: manual checks 1–3; squash-merge; publish; agents verify PyPI digests | vocalize | T-51 | PyPI JSON digests equal local `shasum -a 256 dist/*` |

## Role and isolation

- **Role:** Release manager and independent reviewer — Opus for the adversarial review (T-51, fresh agent, read-only over Phases 1–5's diff), Sonnet for the docs and version-bump work (T-50); the owner for T-52
- **Isolation:** branch `local-first`, off `main`, after Phases 1–5 merge into it; the owner merges (squash) and publishes — agents never push `main`
- **Workload:** 3 doc/source files (`CHANGELOG.md`, `vocalize/__init__.py` for the version bump, `README.md`/`docs/` for the cross-check — no code edits) + 1 new review file (`review-0.12.0.md`) + 0 test files; this is a docs-and-release phase, no source logic changes

## Entry criteria

- on branch `local-first`, not `main`
- run 5 (`run-5-keys-tab`) validated
- run 5's key artifact present: `auth.KEY_SLOTS` (Phase 5 T-40)
- suite green at entry
- ruff clean at entry

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- review findings file exists (`review-0.12.0.md`)
- no open critical/high finding in that review
- CHANGELOG has a `0.12.0` heading
- version bumped to `0.12.0` in `vocalize/__init__.py`
- docs match the CLI (the commands that exist at 0.12.0: `listen`, `dictate`, `resume`, `status`, `auth login` — `doctor`, `notes`, `app install/status`, `integrate claude` do not exist until later phases)
- full suite green
- ruff clean
- work committed
- package builds and names its wheel `vocalize_cli-0.12.0*` in `dist/`
- clean-venv acceptance: installing that wheel leaks no ML runtime (`pywhispercpp`, `onnxruntime`, `mlx`, `sherpa`, `numpy`, `torch`, `boto3`)
- PyPI 0.12.0 published with a digest matching the local build (after the owner publishes)

## Not machine-checkable

- Owner-present manual checks 1–3 (verification.md § Manual checks): beam search jargon-clip listen; keychain read from Claude Code desktop's shell with no prompt; add/test/remove an Anthropic key in the portal with the key never surfacing in the page or terminal.
- Owner squash-merges `local-first` into `main` and publishes to PyPI — agents never push `main` or publish.

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-nn: done | partial | skipped — reason`), the security-gate result (the negative tests named in the acceptance criteria, listed with their test ids), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run 7 (`run-7-hotkey-spike-and-builder`) reads that report as its entry criterion.
