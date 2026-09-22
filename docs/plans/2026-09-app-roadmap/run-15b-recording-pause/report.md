# Report: run 15b, recording pause and resume

Date 2026-09-22. Branch `notes`. Source plan in [project-plan.md](./project-plan.md).

- T-147: done — `dictate --pause` and `dictate --resume`: segment rotation to `take.NNN.wav`, `paused` marker (`0600`), cumulative budget bounds via `[stt] max_take_seconds` (default 1800, 60..7200) and `_MAX_SEGMENTS = 20`, segment-based backstop, self-stop notification at `--max`, and untrusted marker validation.
- T-148: done — `_join_segments` with stdlib `wave` inserting 0.25s silence at seams, dynamic `_transcribe_timeout` scaling with take duration, toggle while paused stops & transcribes, cancel while paused cleans all segments.
- T-149: done — Stop chord precedence (`[app] stop_hotkey = "pause"` pauses live recording, resumes paused recording), docs in `docs/dictation.md`, and CHANGELOG entry.

Security gate: corrupt/untrusted marker parsing (`test_resume_treats_a_corrupt_paused_marker_as_no_pause`), 20-segment cap (`test_resume_refuses_a_twenty_first_segment`), max take budget cap (`test_resume_refuses_past_the_take_budget`), bounds validation (`test_max_take_seconds_bounds`), untrusted workdir protection, private permissions (`0600` marker, `0700` workdir), stale workdir sweeps (`test_paused_workdir_younger_than_24h_is_not_swept`).

Deferred: Manual hardware checks 17 & 18 (real audio pause gap & long take; owner present).

validate-exit: PASS
