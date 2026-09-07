# Run 1: Local-first defaults (0.12.0)

Part of the 2026-09 app-roadmap plan (this plan set has no separate
choreography.md). Source plan: [plan.md](../plan.md) § Phase 1: Local-first
defaults (0.12.0); contracts in [design.md](../design.md) § Fallback note
(DEC-022); proof commands in [verification.md](../verification.md) § Phase 1
exit (defaults).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-01 | `config.DEFAULT_CHAIN = ("kokoro", "say")`; reorder `auth.PROVIDER_NAMES` to `kokoro, say, elevenlabs, openai, google, polly`; `chain.run` appends `— Kokoro is not installed; run: vocalize local install` to the fallback line when the skipped primary was Kokoro's not-installed error (DEC-022) | vocalize | — | with an empty config and no Kokoro model, `vocalize speak hi` speaks via `say` and stderr carries exactly that one line; with Kokoro installed no note is printed; `vocalize usage` lists kokoro first |
| T-02 | Update the four tests pinning the old literal (`tests/test_config.py:291`, `tests/test_cli.py:949`, `:1464`, `:1477`) and add the fallback-note tests | vocalize | T-01 | suite green; a test asserts the note text and that it appears once |
| T-03 | Docs lead with local: README opening, quickstart, the settings table and the upgrader paragraph; `pyproject.toml` description and keywords; `docs/installation.md` layer 1 unchanged | vocalize | T-01 | `grep -n "ElevenLabs" README.md` shows no hit before the "## Providers" heading; `pyproject.toml` description names local speech first |
| T-04 | CHANGELOG entry under Unreleased: the new default and the upgrade note for users who never set a chain | vocalize | T-01 | entry present |

## Role and isolation

- **Role:** Python config and chain defaults — mechanical literal flips plus one message string (model tier: **Sonnet**)
- **Isolation:** branch `local-first`, off `main`, after 0.11.0
- **Workload:** 6 source files (3 edited for T-01: `vocalize/config.py`, `vocalize/auth.py`, `vocalize/chain.py`; 3 edited for docs/release: `README.md`, `pyproject.toml`, `CHANGELOG.md`) + 3 test files (`tests/test_config.py`, `tests/test_cli.py`, `tests/test_chain.py`)

## Entry criteria

- 0.11.0 published to PyPI (owner-confirmed; see `docs/plans/2026-09-app-roadmap/review-plan-2026-09-06.md` / CHANGELOG — no previous run in this plan to gate on)
- on branch `local-first`, not `main`
- suite green at entry
- ruff clean at entry

**Caveat found while validating this run (2026-09-06):** the suite is
currently **not** green on `main`/the planning branch — no Phase 1 code has
touched this — two different pytest invocations under heavy machine load
(many concurrent sibling test runs) each stopped at `-x` on a different
failure: `tests/test_dictate.py::test_a_second_press_within_the_window_cancels`
(reproduces in isolation, so likely a real pre-existing bug, not pure
flakiness) and, on a second run, `tests/test_kokoro_provider.py::test_importing_vocalize_pulls_in_no_machine_learning_runtime`
(a `subprocess.CalledProcessError`, consistent with resource exhaustion under
load rather than a code defect). Whoever actually starts run-1 should re-run
`pytest tests/ -q -x -p no:cacheprovider` on a quiet machine first — if
`test_a_second_press_within_the_window_cancels` still fails alone, that is a
pre-existing bug outside Phase 1's scope (`dictate.py`) that blocks the entry
criterion and needs its own fix or triage before this run's work begins.

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes
to the repository root). Every line is a command's exit status; a pre-build
run must show the artifact checks failing.

- default chain flipped to `("kokoro", "say")` and the fallback note wired (tests)
- fresh machine still speaks: `vocalize speak` on an empty config with no
  Kokoro model falls back to `say` and stderr carries the Kokoro-not-installed
  note exactly once
- docs lead with local: no `ElevenLabs` mention in README.md before the
  `## Providers` heading
- full suite green
- ruff clean
- work committed

The Phase 1 "suite intact" row in verification.md is covered by the
full-suite-green and ruff-clean checks below rather than a separate,
redundant pytest invocation.

## Not machine-checkable

- None for this phase — Phase 1 carries no owner-present manual check (the
  first manual check in verification.md, beam search, belongs to Phase 2).

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task
(`T-nn: done | partial | skipped — reason`), the security-gate result (this
phase carries no security-negative tests — note that explicitly), anything
deferred, and the final line `validate-exit: PASS` copied from a real run of
the script. Run `run-2-stt-decoding` reads that report as its entry criterion.
