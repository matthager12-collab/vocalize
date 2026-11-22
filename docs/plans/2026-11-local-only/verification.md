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
| No call-capture feature | the module absent; `vocalize --help`; text search of `vocalize/`, `hooks/`, `pyproject.toml` | no file, no command, no match for the removed feature's words |
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
| Engines were measured on the Studio | `measurements.md` in this folder | exists, with the method |
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

## Status (2026-11-21)

Ticked only where the evidence was seen. Phase exits still need
`verify.sh` and the full suite.

### Phase 1

- [x] No call-capture feature: no module, no command in
  `vocalize --help`, none of the removed feature's words in `vocalize/`,
  `hooks/` or `pyproject.toml`. The review repeated the search.
- [x] No summaries: no command, no `hooks/speak_url_gate.py`, no
  `vocalize/assets/notes`, no `summarize` function.
- [x] A caller's compatibility line survives: `vocalize settings` prints
  `notes.summarizer=off`.
- [x] `tests/test_local_only.py` passes.
- [ ] File transcription tests and the full suite: not run in this pass.

### Phase 2

- [x] One registry: `vocalize/settings_registry.py` exists.
- [x] Schema and one save route: `/api/schema` and `/api/settings` are in
  the route table of `vocalize/portal.py`; `/api/stt`, `/api/chain` and
  `/api/provider/*` are not.
- [x] Every setting has a control: `tests/test_settings_parity.py` passes.
- [ ] Lint and full suite: not run in this pass.

### Phase 3

- [x] Engines were measured on the Studio: [measurements.md](./measurements.md)
  covers four runs, with the method.
- [ ] The Studio configuration through `vocalize settings`: not run in this
  pass.
- [ ] Profiles: `tests/test_settings_parity.py` checks every profile key is
  registered and valid; `tests/test_profiles.py` not run in this pass.
- [ ] Intake reports: not checked in this pass.

Thresholds, from [measurements.md](./measurements.md):

| Measurement | Result | Status |
|---|---|---|
| Dictation, stop to text, 20-second take | not measured from a live take; Parakeet took 0.62 to 0.71 s on saved 18 to 23 s files, model already in the file cache; warm Whisper took 0.45 to 0.48 s on a 22.9 s file | [ ] not shown for a live take |
| Speech output transcribed back, plain prose | 1.3 % (Kokoro to Whisper) | [x] pass |
| Speech output transcribed back, technical text | 6.8 % raw with q5_0, 2.3 % with q8_0, 0.75 % normalised | [ ] fails raw with the default model |
| First audio, default voice, model loaded | release candidate: 0.24 to 0.63 s for three passages (earlier build: 2.4 to 4.9 s) | [x] pass |
| Silence and noise, ten runs each | digital silence on the release candidate: no words in 10 of 10 runs across both engines; Parakeet on white noise: no words, 5 of 5; Whisper wrote "." for one low-level noise file, 2 of 2 runs, before the check for a transcript with no letters or digits was added | [ ] noise not re-measured after that check |
| 60-minute file | completed with Parakeet in 27.6 s; resident memory 944 MB at 10 minutes and 1.5 GB at 60 | [ ] completes; the memory part fails as counted |
| Offline | Kokoro, Whisper and Parakeet worked under `(deny network*)` | [x] pass |

### Review

The independent review (T-41) found no critical finding, 2 high, 6 medium
and 12 low. Its verdict: ready after the listed fixes. The two high
findings, the missing upgrade note and the sentence order at Parakeet chunk
joins, are fixed in this release.

### Phase 4

- [x] Version and changelog: `vocalize/__init__.py` says `1.0.0`;
  `CHANGELOG.md` has a `## 1.0.0 - 2026-11-21` section.
- [x] Docs name no removed feature: the search of `README.md` and `docs/*.md`
  finds none.
- [ ] It builds, CI, published equals built, it installs: see the task
  reports; not ticked here.
