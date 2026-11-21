# Verification: vocalize, local only

Every phase exit in [plan.md](./plan.md) maps to `verify.sh`, which prints
one PASS or FAIL per check and exits non-zero if any failed.

## Commands

| Purpose | Command |
|---|---|
| Install for development | `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev]"` |
| Lint | `.venv/bin/ruff check .` |
| Unit tests | `.venv/bin/python -m pytest -q` |
| Page harness | part of the unit tests; needs `node` on the path, skipped without it |
| Build | `.venv/bin/python -m build --outdir <scratch>` |
| Phase exit | `VOCALIZE_VERIFY_PYTHON=.venv/bin/python bash docs/plans/2026-11-local-only/verify.sh <phase>` |
| Plan lint | `bash <toolkit>/skills/plan-project/scripts/lint-plan.sh docs/plans/2026-11-local-only` |
| Decisions lint | `python3 <toolkit>/skills/decision-record/scripts/lint-decisions.py docs/plans/2026-11-local-only/decisions.md` |

These come from this repository's own `pyproject.toml` and
`.github/workflows/ci.yml`. CI runs lint and tests on Python 3.10, 3.12 and
3.14; those three checks are required on `main`.

Tests run with `HOME` pointed at a temporary folder. `verify.sh` does that
itself.

## Baselines measured before any change (2026-11-21, Python 3.12)

| Tree | Lint | Tests |
|---|---|---|
| `main` at `7ce02b1` | passes | 2542 passed, 6 skipped |
| `90b5dbc` | passes | 2360 passed, 6 skipped |

## Phase 0 exit

| Criterion | How it is proven | Passing when |
|---|---|---|
| The branch contains the base and `main` | `git merge-base --is-ancestor` for each | both exit 0 |
| Nothing regressed | lint and suite | exit 0 |

## Phase 1 exit

| Criterion | How it is proven | Passing when |
|---|---|---|
| No meeting feature | `vocalize/meetings.py` absent; `vocalize --help`; text search of `vocalize/`, `hooks/`, `pyproject.toml` | no file, no command, no match for "meeting" or "zoom" |
| No summaries | `vocalize --help`; `hooks/speak_url_gate.py` and `vocalize/assets/notes` absent; text search | no command, no files, no `summarize` function |
| File transcription works | `vocalize --help`; tests for `transcribe` and the `notes` alias | command listed; tests pass |
| A caller's compatibility line survives | `vocalize settings` | prints `notes.summarizer=off` |
| Nothing reaches the network | `tests/test_local_only.py`: import allow-list, offline flags on every worker command line, environment allow-list; suite with sockets disabled | exit 0 |
| Nothing regressed | lint and suite | exit 0 |

## Phase 2 exit

| Criterion | How it is proven | Passing when |
|---|---|---|
| One registry | `vocalize/settings_registry.py` exists; config tests | exit 0 |
| One save route, schema served | route table in `vocalize/portal.py` | `/api/schema` and `/api/settings` present; `/api/stt`, `/api/chain`, `/api/provider/*` absent |
| Every setting has a control | `tests/test_settings_parity.py` | exit 0 |
| Nothing regressed | lint and suite | exit 0 |

## Phase 3 exit

| Criterion | How it is proven | Passing when |
|---|---|---|
| Engines were measured on the Studio | `measurements.md` in this folder | exists, with the commands used |
| The Studio configuration is valid | `vocalize settings` against `studio-config.toml` | exits 0 with nothing on standard error |
| Profiles write only registered keys | `tests/test_profiles.py` | exit 0 |
| New packages were reviewed first | an `intake-report.md` per package, dated before its first install | listed in `measurements.md` with verdicts |
| Nothing regressed | lint and suite | exit 0 |

Thresholds for the measurements, taken from the research notes:

| Measurement | Passing when |
|---|---|
| Dictation, stop to text, 20-second take, Studio | at most 1.0 s warm and 2.5 s cold |
| Speech output transcribed back, plain prose | word error rate at most 3% |
| Speech output transcribed back, technical text | word error rate at most 6% |
| First audio, default voice, model loaded, Studio | at most 1.0 s |
| Silence and noise, ten runs each | no words in the output |
| 60-minute file | completes; memory does not grow with length by more than 10% |
| Offline | the engine runs with no route to the network and opens no network socket |

## Phase 4 exit

| Criterion | How it is proven | Passing when |
|---|---|---|
| Version and changelog | `vocalize/__init__.py`, `CHANGELOG.md` | `1.0.0` in both |
| Docs name no removed feature | text search of `README.md` and `pyproject.toml` | no match |
| It builds | `python -m build` | exit 0 |
| CI | the three required checks on the pull request | all green |
| What was published is what was built | sha256 of each file in the PyPI record against the local build | equal |
| It installs | a fresh environment installs `vocalize-cli==1.0.0` and runs `vocalize --version` | prints 1.0.0 |
| Nothing regressed | lint and suite | exit 0 |

## Manual checks

Only a person can do these, and only on the machine in question.

1. **Microphone.** The first dictation asks for microphone access for
   "Vocalize Recorder". Approve it. Then run `vocalize listen --check`.
2. **Accessibility.** System Settings, Privacy & Security, Accessibility:
   enable Vocalize, then run `vocalize app restart`. Without it the speak
   and paste shortcuts do nothing and report nothing.
3. **Listen once.** Run `vocalize speak` on a paragraph with a table and
   confirm it sounds right. The build checks intelligibility by
   transcribing the audio back; it cannot judge how natural a voice is.
