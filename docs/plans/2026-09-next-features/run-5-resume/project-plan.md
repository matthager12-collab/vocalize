# Run 5: Player-side interrupt record and `vocalize resume` (DEC-003)

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 4b; contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-46 | Player-side interrupt record (DEC-003, design § Interrupted-read resume) in the core modules: `audio.stop_playback(remember=False)` writes `interrupt.request` (0600) before the SIGTERM; `audio._run_tracked` consumes the marker via `audio.take_interrupt_request(proc.pid)` at the moment the player exits by SIGTERM (on the playing thread) and keeps `last_stop()` → (path, elapsed, remembered); `exceptions.PlaybackStopped` gains `remaining_text`, filled by `chain._speak` from `chunks[index:]`; `cli._run_tts` captures `play_audio(dest)`'s return value and writes `interrupted.<ext>` (a copy of `last_stop().path`, i.e. the current piece or the whole file) / `interrupted.txt` / `interrupted.json` (0600) at both stop sites | vocalize | — | tests with a fake player exiting `-SIGTERM`: streamed stop mid-piece 3 of 5 → record holds piece 3's bytes (not the joined audio), piece 3's elapsed offset and the text of pieces 4–5; a stop that lands while a slow chunk is still synthesizing (fake provider sleeping 15 s) still produces a record; non-streamed stop → record with the whole file and empty remaining text; no marker (plain `vocalize stop`) → no record; marker naming another PID or older than 10 s → no record and marker removed; files are 0600; existing playback tests unchanged |
| T-47 | `vocalize resume [--forget]` and the dictation dialog: read the record (ignore and delete when older than 1 h), `afconvert` to WAV when needed, `wave` slice from the offset, `audio.play`, then `chain.run` on the remaining text with the recorded provider forced; `dictate` shows the osascript dialog (default Continue, 15 s give-up = no) only for a record newer than its own stop; record deleted on resume, decline and `--forget` | vocalize | T-40, T-46 | tests with a fake `afconvert` and fake chain: slice length = duration − offset; continuation called with the remaining text and forced provider; declined dialog and `--forget` delete the record; stale record ignored and deleted; `resume` with no record prints "Nothing to resume" and exits 0; `dictate._start` calls `audio.stop_playback(remember=True)` and `dictate.listen` does not, with a test asserting the marker is written on a hotkey start and not on a foreground `listen` (run 4 shipped both call sites as plain `stop_playback()` — see [run-4 report](../run-4-dictation/report.md)) |

## Role and isolation

- **Role:** Core playback engineer — judgement, edits the shared playback path (Opus); independent reviewer on `audio.py`/`chain.py`/`cli.py` before commit
- **Isolation:** shared checkout on `next-features`, after run 4 (T-47 needs `dictate.py`); the only run that edits `audio.py`, `chain.py` and `exceptions.py`
- **Workload:** 4 core files edited (audio.py, chain.py, cli.py, exceptions.py — every existing playback test must stay green, weight ×3) + dictate.py dialog + 4 test files with a fake player exiting -SIGTERM and a fake afconvert

## Entry criteria

- on branch next-features
- run 4 validated
- dictate module present
- suite green at entry
- `exceptions.py` is already non-pristine: run 4 added `DictationError` there (recorded as a divergence in the [run-4 report](../run-4-dictation/report.md)); this run still owns the file and adds `remaining_text` to `PlaybackStopped`

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- interrupt record tests (audio, chain, cli)
- resume tests
- resume command exists with --forget
- marker seam exists in audio.py
- PlaybackStopped carries remaining_text
- existing playback tests untouched and green
- full suite green
- ruff clean
- work committed

## Not machine-checkable (owner present)

- Playback interaction and resume (verification Manual 4) — owner-present, performed in run 6.

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-nn: done | partial | skipped — reason`), the security-gate result (the negative tests named in the acceptance criteria, listed with their test ids), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run 6 (`run-6-release-0-10-0`) reads that report as its entry criterion.
