# Run 15: Notes (0.14.0)

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 15: Notes (0.14.0); contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-140 | `whisper_worker.py --segments` (progress lines, `segments[]`), byte-identical without the flag; the same contract in the Parakeet worker if T-122 shipped | vocalize | — | stub-Model test with centisecond `t0/t1`; existing worker tests unchanged |
| T-141 | `vocalize/notes.py` per design § Notes: sources with the self-ingestion guard, done-scan on hash **and** resolved source path with every skip printed by name, convert with resolved paths and timeout, `_transcribe_long` with the inactivity timeout, utf-8 decoding and `sanitize` on every segment, `llm.summarize` with per-destination caps, the atomic `.tmp`-then-replace note writer with the `trust` key, folder rules, the run lock, the stale sweep generalised from dictate | vocalize | T-140, T-133 | `tests/test_notes.py` green incl. every negative in design § Testing strategy |
| T-142 | Templates `memo`, `meeting`, `lecture`, `journal` in `vocalize/assets/notes/`; custom path handling (`O_NOFOLLOW`, 64 KB cap, documented as trusted) | vocalize | — | an unknown bare name is refused; a `.md` path is accepted; `../` cannot reach the package directory; a symlink or an oversized file is refused with a message |
| T-143 | CLI `vocalize notes SOURCE... [--template] [--summarizer] [--force] [--keep-audio]`; `[notes] model`; `settings` lines | vocalize | T-141 | `--summarizer local` over a cloud config makes no call and prints no egress line; `--summarizer claude-cli` prints it exactly once per file |
| T-144 | Tests for T-140–T-143 | vocalize | T-143 | green; `threading.active_count()` back to baseline after a run |
| T-145 | Docs: `docs/notes.md` (privacy: transcript stored 0600, claude-cli history stub, egress line, iCloud sync caveat, a note is untrusted input to any model, `keep_audio` growth visible in `doctor`), README section, CHANGELOG | vocalize | T-143 | present |
| T-146 | **Real-audio pass, owner present, 3 hours:** one 60-minute recording through `vocalize notes` with the local summarizer; measure the inactivity timeout, any whisper looping on silence, the local cap, and peak combined RSS with a Kokoro read playing during the summary; adjust constants; record in `spike-notes.md` § Notes | vocalize | T-144 | a note exists for the recording; constants in `notes.py` match the recorded verdict |

## Role and isolation

- **Role:** Sonnet for the module and tests (mechanical from the design), with an Opus review of the untrusted-input paths before commit — `notes.py` is the audio-file-to-summary pipeline and every negative in design § Testing strategy that touches it (self-ingestion guard, sanitize on every segment, atomic write, run lock, `-x.m4a` handling, custom-template symlink/size refusal) is untrusted-input handling
- **Isolation:** branch `notes`, after Phase 14 (this run's entry requires run-14-local-llm's report); works in `vocalize/notes.py`, `vocalize/assets/notes/`, `vocalize/local/whisper_worker.py`
- **Workload:** 5 source files (2 new: `vocalize/notes.py`, `vocalize/assets/notes/{memo,meeting,lecture,journal}.md`; 3 edited: `vocalize/local/whisper_worker.py`, `vocalize/cli.py`, `vocalize/config.py`) + 2 test files (1 new: `tests/test_notes.py`; 1 edited: `tests/test_whisper_worker.py`)

## Entry criteria

- on branch `notes` (not `main`)
- run-14-local-llm validated: its `report.md` contains the line `validate-exit: PASS`
- run-14's key artifact exists: `vocalize/local/llm_worker.py`
- suite green at entry
- ruff clean at entry

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- pipeline and negatives: `tests/test_notes.py` green (self-ingestion guard; done-scan on hash and path with skips printed; every segment sanitized; atomic write; `trust` key; template symlink and size cap; lock; sweep; utf-8; `-x.m4a`; folder never re-moded; egress once; caps)
- segments contract: `tests/test_whisper_worker.py -k segments` green
- threads clean: `tests/test_notes.py -k threads` green (`threading.active_count()` back to baseline)
- full suite green
- ruff clean
- work committed

## Not machine-checkable

- **T-146, real-audio pass (owner present, 3 hours).** Manual check 13: a 60-minute recording through `vocalize notes`; note the wall time, memory, any looping; one note written; the summary reads sensibly for the template; during the summary, start a Kokoro read and record the peak combined RSS with Claude Code and a browser open. Constants in `notes.py` are adjusted to match the recorded verdict in `spike-notes.md` § Notes.

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-nn: done | partial | skipped — reason`), the security-gate result (the negative tests named in the acceptance criteria, listed with their test ids), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run 16 (`run-16-release-0-14-0`) reads that report as its entry criterion.
