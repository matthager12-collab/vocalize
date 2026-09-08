# Run 15b: Recording pause and resume (0.14.0)

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 15b: Recording pause and resume (0.14.0); contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-147 | `dictate --pause`: `_stop_file`, `_wait_for_exit` timed from *this segment's* own start, not the take's (the backstop is `segment_start + max_seconds + _BACKSTOP_GRACE`; timed from the take's start it goes negative past `max_seconds` and SIGTERMs a live recorder before its WAV is finalised), run 11's `_trim_cue` on the finished take if run 11 shipped it, else the finished take as-is under run 11's fallback cue order (T-100/T-101 may have taken either branch), `os.replace` to `take.NNN.wav`, then a 0600 `paused` marker holding the epoch and the cumulative seconds recorded across the whole take so far; `dictate --resume`: read the marker as untrusted input first — a non-finite, negative or unparsable value means no usable pause and takes the same branch as a missing marker — then refuse past `_MAX_SEGMENTS = 20` or past the new `[stt] max_take_seconds` (default 1800, bounded 60..7200) via `remaining = max_take_seconds - cumulative_seconds`, launch a fresh recorder with `--max = max(1, min(max_seconds, remaining))`, `_wait_for_audio`, the Tink, this segment's own `cue` file, then unlink the marker and roll the cumulative total forward into the next pause's marker. A segment that self-stops at its own `--max` with no explicit pause plays the stop cue and notifies that the microphone closed, rather than recording into a closed mic in silence. `dictate.session` keeps saying `recording` throughout | vocalize | — | `tests/test_dictate.py::test_pause_finalises_a_segment_and_leaves_the_session_claimed`, `::test_session_state_stays_recording_while_paused`, `::test_resume_launches_a_second_recorder_with_the_remaining_budget`, `::test_resume_max_never_falls_below_one_second`, `::test_resume_refuses_past_the_take_budget`, `::test_resume_refuses_a_twenty_first_segment`, `::test_wait_for_exit_backstop_uses_the_segment_start_not_the_take_start`, `::test_resume_treats_a_corrupt_paused_marker_as_no_pause`, `::test_segment_self_stop_at_max_notifies_before_the_mic_closes` and `tests/test_config.py::test_max_take_seconds_bounds` green |
| T-148 | `_join_segments`: stdlib `wave`, segments in numeric order then the live `take.wav`, 0.25 s of zero frames at each seam, params asserted identical, written to `take.joined.wav` then `os.replace`; called from `_finish_take` immediately after `_trim_cue` and only when a segment exists, keying on the glob rather than the marker. `_TRANSCRIBE_TIMEOUT` and `_FINISH_TIMEOUT` scale with the joined take's duration (`max(300, take_seconds * k)`, `k` measured in the run-13 spike) instead of the fixed 300 s, so a long joined memo is not killed mid-transcription with its workdir discarded. Plus the two survival branches: a plain toggle press while paused routes to `_stop` with `pid None`, and `--cancel` while paused discards every segment | vocalize | T-147 | `tests/test_dictate.py::test_join_segments_frames_and_silence`, `::test_joined_wav_keeps_16k_mono_16bit`, `::test_cue_trimmed_per_segment_never_reaches_the_worker`, `::test_toggle_while_paused_stops_and_transcribes`, `::test_cancel_while_paused_removes_every_segment`, `::test_paused_workdir_younger_than_24h_is_not_swept`, `::test_transcribe_timeout_scales_with_take_length` green |
| T-149 | Stop-chord precedence and docs: with `[app] stop_hotkey = "pause"` (shipped in 0.13.1), `vocalize stop` first pauses a live session reading `recording` and resumes one holding the `paused` marker, both ahead of the playback branches, with the state word read as untrusted input so anything unrecognised falls through; `docs/dictation.md` pause section (the seam gap, the per-segment and total budgets, that segments never leave the temporary workdir and never reach the notes folder), `[stt] max_take_seconds` in the settings table, CHANGELOG 0.14.0 | vocalize | T-148 | `tests/test_cli.py::test_stop_pauses_a_live_recording_before_playback`, `::test_stop_resumes_a_paused_recording`, `::test_unknown_session_state_falls_through_to_playback` green; `grep -q 'dictate --pause' docs/dictation.md`, `grep -q 'dictate --pause' CHANGELOG.md` (one grep per file) and `grep -q 'max_take_seconds' docs/dictation.md` exit 0 |

## Role and isolation

- **Role:** Opus — three things decide whether a twenty-minute memo survives: the join, the per-segment budget against the recorder's frozen 1..600 bound, and the `_second_press` branch a paused take falls into. The shipped code, unpatched, reaches `_fail`, discards the workdir and says the recorder failed.
- **Isolation:** branch `notes`, off `main`, after run 15 (`run-15-notes`); works in `vocalize/dictate.py` (`_pause`, `_resume`, `_join_segments`, `_second_press`, `cancel`), `vocalize/cli.py` (the `dictate` command's two new flags and the stop precedence branches) and `vocalize/config.py` (`[stt] max_take_seconds`); does **not** touch `vocalize/recorder/VocalizeRecorder.swift` — every rebuild costs a microphone grant (DEC-010, DEC-031) — nor `vocalize/menubar/VocalizeApp.swift`, nor `vocalize/local/whisper_worker.py`, which still receives exactly one WAV, nor `vocalize/notes.py`, which never opens a microphone.
- **Workload:** 3 source files (`vocalize/dictate.py`, `vocalize/cli.py`, `vocalize/config.py`) + 3 test files (`tests/test_dictate.py`, `tests/test_cli.py`, `tests/test_config.py`) + 3 docs.

## Entry criteria

- on branch `notes` (not `main`)
- run 15 (`run-15-notes`) validated: its `report.md` contains the line `validate-exit: PASS`
- run 15's key artifact present: `vocalize/notes.py` § `_done` (Phase 15 T-141)
- DEC-037 Decided (how a recording pauses without touching the frozen recorder)
- `[app] stop_hotkey` shipped in 0.13.1 (the hotkey branches in T-149 extend it)
- `vocalize/recorder/` and `vocalize/menubar/` match `main`
- suite green at entry
- ruff clean at entry

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- the dictate command carries the two new flags (`--pause`, `--resume`)
- pause and resume mechanics: `tests/test_dictate.py::test_pause_finalises_a_segment_and_leaves_the_session_claimed`, `::test_resume_launches_a_second_recorder_with_the_remaining_budget`, `::test_resume_max_never_falls_below_one_second` green
- the frozen app's vocabulary is untouched: `tests/test_dictate.py::test_session_state_stays_recording_while_paused` green (the session JSON never contains "paused")
- both budgets bound the take: `[stt] max_take_seconds` present in `vocalize/config.py`; `tests/test_dictate.py::test_resume_refuses_a_twenty_first_segment`, `::test_resume_refuses_past_the_take_budget`, `tests/test_config.py::test_max_take_seconds_bounds` green
- the backstop and the marker are safe against the take's own age and against garbage on disk: `tests/test_dictate.py::test_wait_for_exit_backstop_uses_the_segment_start_not_the_take_start`, `::test_resume_treats_a_corrupt_paused_marker_as_no_pause` green
- the transcription budget scales with the take, so a long joined memo is never killed mid-transcription: `tests/test_dictate.py::test_transcribe_timeout_scales_with_take_length` green
- one WAV reaches the worker: `tests/test_dictate.py::test_join_segments_frames_and_silence`, `::test_joined_wav_keeps_16k_mono_16bit`, `::test_cue_trimmed_per_segment_never_reaches_the_worker` green
- a forgotten pause loses nothing: `tests/test_dictate.py::test_toggle_while_paused_stops_and_transcribes`, `::test_cancel_while_paused_removes_every_segment`, `::test_paused_workdir_younger_than_24h_is_not_swept`, `::test_segment_self_stop_at_max_notifies_before_the_mic_closes` green
- stop-chord precedence: `tests/test_cli.py::test_stop_pauses_a_live_recording_before_playback`, `::test_stop_resumes_a_paused_recording`, `::test_unknown_session_state_falls_through_to_playback` green
- the recorder's Swift source is untouched: `git diff --quiet main -- vocalize/recorder/`
- the app's Swift source is untouched: `git diff --quiet main -- vocalize/menubar/`
- docs and CHANGELOG carry `dictate --pause` and `max_take_seconds`
- full suite green
- ruff clean
- work committed

## Not machine-checkable

- **T-147/T-148, pause and resume on real hardware (owner present).** Manual check 17: a paused and resumed dictation transcribes as one continuous transcript with no repeated or dropped word at the seam; a stopwatch on the resume gap, three times on the built-in microphone and three times on a Bluetooth input, recorded in `spike-notes.md` § Pause. A Bluetooth gap over two seconds means pause is not usable for memos, and `report.md` must say so.
- **T-147/T-149, a long take (owner present).** Manual check 18: with `[stt] max_seconds` raised to 600 first (three pauses is only four segments at the default 120 s, never ten minutes), a take over ten minutes across three pauses transcribes as one continuous transcript, `[stt] max_take_seconds` stops it at the cap, the temporary directory is gone afterwards, and what the menu-bar icon does while paused (it still says recording, by design) is recorded as observed. Also let one segment self-stop at `--max` unpaused: the stop cue plays and a notification names the closed microphone.

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-nn: done | partial | skipped — reason`), the security-gate result (the negative tests named in the acceptance criteria, listed with their test ids), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run 16 (`run-16-release-0-14-0`) reads that report as its entry criterion.
