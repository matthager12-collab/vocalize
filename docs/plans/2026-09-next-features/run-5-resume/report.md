# Run 5 report: player-side interrupt record and `vocalize resume` (DEC-003)

Branch `next-features`, shared checkout. Source plan: [project-plan.md](./project-plan.md);
contracts in [design.md](../design.md) § Interrupted-read resume; decisions in
[decisions.md](../decisions.md) (DEC-003, and DEC-012 from this run's review round).

## Tasks

- **T-46: done** — the marker seam and the record. `audio.stop_playback(remember=True)`
  writes `interrupt.request` (0600, `O_NOFOLLOW`) before the SIGTERM;
  `audio._run_tracked` consumes it through `audio.take_interrupt_request(proc.pid)` on the
  thread that ran the player, the moment it exits by SIGTERM, and keeps
  `audio.last_stop() -> (path, elapsed_seconds, remembered)`. `exceptions.PlaybackStopped`
  carries `remaining_text`, filled by `chain._speak` from `chain.unheard_text`.
  `cli._run_tts` writes `interrupted.<ext>` / `.txt` / `.json` (all 0600) at three stop
  sites: the streaming `except PlaybackStopped`, the non-streaming `play_audio(dest)`, and
  the case where every piece was handed over before the stop landed.
- **T-47: done** — `vocalize resume [--forget]` and the dialog. `interrupted.load()` (a
  record older than an hour is ignored and deleted), `afconvert` to WAV when needed, stdlib
  `wave` slice from the offset, `audio.play`, then `chain.run` on the remaining text with
  the recorded provider forced. `dictate` shows the fixed-text osascript dialog (default
  Continue, giving up after 15 s = no) for a record newer than its own stop; the record is
  deleted on decline, on `--forget`, when stale, and at the end of a resume.

## Review round (three independent reviews of the shipped code)

Fourteen findings, twelve distinct. Ten fixed, one shipped behaviour kept with the
documentation corrected, one recorded as a deliberate narrowing. Decided together as
DEC-012 in [decisions.md](../decisions.md).

| Fix | Where |
|---|---|
| A stop landing between two streamed pieces killed nothing and recorded nothing, and the queued piece then played into the open microphone | `audio.stop_playback` always writes a marker (PID `0` when nothing is playing), `audio.take_gap_stop`, `cli._StreamPlayer._drain` |
| The stop marker was read through a symlink, unlike every other access to these guessable paths | `audio._read_interrupt_request` (`O_NOFOLLOW`) |
| A marker past the window was left on disk when it named another player | `audio.take_interrupt_request` sweeps it |
| `interrupted.json` accepted `Infinity` / `NaN`: an `OverflowError` out of the Quick Action, and a record that never expired | `interrupted.load` (`math.isfinite`) |
| A dictation stopping the resume's replay deleted the record and lost the read | `cli.resume_interrupted` re-records through `interrupted.remember_stop` |
| A continuation that failed before it spoke destroyed the read it was continuing | `cli.resume_interrupted` forgets after the continuation, and only if `saved_at` is unchanged |
| `vocalize resume` could consume a record and print nothing at all | `cli.resume_interrupted` returns False when there is nothing to play and nothing to say |
| The dialog lost a race with the record it depends on, so a slow chunk meant no dialog ever appeared | `dictate._wait_for_record` (3 s grace, skipped when the stop found nothing playing) |
| The design's privacy note claimed the plaintext was not new on disk | design.md § Interrupted-read resume, `interrupted.py` docstring |
| The exit gate's `-k interrupt` selected 3 of 11 marker tests | eight tests in `tests/test_audio.py` renamed; the check now selects 29 |

## Security gate

Negative tests named in the acceptance criteria, all green:

- `tests/test_cli.py::test_an_interrupt_marker_for_another_player_records_nothing`
- `tests/test_cli.py::test_a_stale_marker_writes_no_interrupt_record`
- `tests/test_cli.py::test_an_interrupted_read_records_nothing_after_a_plain_stop`
- `tests/test_audio.py::test_an_interrupt_marker_is_never_written_through_a_symlink`
- `tests/test_dictate.py::test_a_record_behind_a_symlink_is_never_read_or_resumed`
- `tests/test_dictate.py::test_saved_audio_behind_a_symlink_is_never_resumed`
- `tests/test_dictate.py::test_a_record_naming_an_extension_vocalize_never_wrote_is_never_resumed`
- `tests/test_dictate.py::test_a_record_naming_something_vocalize_never_wrote_is_never_resumed`
- `tests/test_dictate.py::test_the_transcript_is_never_written_to_a_file`

Added by the review round:

- `tests/test_audio.py::test_an_interrupt_marker_is_never_read_through_a_symlink`
- `tests/test_audio.py::test_a_stale_interrupt_marker_naming_another_player_is_swept`
- `tests/test_audio.py::test_an_interrupt_from_before_the_read_is_never_taken_in_the_gap`
- `tests/test_audio.py::test_a_gap_interrupt_marker_past_the_window_is_swept_by_the_next_read`
- `tests/test_cli.py::test_a_gap_interrupt_from_before_the_read_is_ignored`
- `tests/test_dictate.py::test_a_record_holding_infinity_is_never_resumed[offset_seconds]`
- `tests/test_dictate.py::test_a_record_holding_infinity_is_never_resumed[saved_at]`

Red-then-green evidence, by removing each guard and re-running its test: both `Infinity`
tests fail on the record that loads, the symlink-read test fails on the fabricated marker,
and the gap test fails with piece 4 played and no record written. All pass again with the
guards restored.

**SECURITY: PASS.** No Critical or High issue outstanding. Residual, accepted and
documented: `interrupted.txt` is new plaintext of the unread remainder in the user's cache,
0600, for at most an hour (DEC-012 e).

## Divergences and deferred

- **A fresh marker naming another player is still left where it is.** T-46's acceptance
  criterion reads "marker naming another PID or older than 10 s → no record and marker
  removed". A *stale* marker is now removed whatever PID it names, but a fresh foreign one
  is deliberately not: it belongs to another live read, possibly in another process, and
  consuming it would break that read's own resume. Deliberate narrowing, DEC-012 b.
- **A stop while a non-streamed provider is still synthesizing is not caught.** Nothing is
  playing and no `_StreamPlayer` exists, so the gap check has no seam to run in: the whole
  file then plays after the dictation started. Pre-existing, unchanged by this run, and out
  of its scope — it would need a marker check inside `audio.play` itself.
- **The 3 s dialog grace does not cover an arbitrarily slow chunk.** A provider taking 20 s
  over the chunk it was stopped in still writes its record after the dialog has given up;
  the record then waits for `vocalize resume`, which is the documented fallback.
- **Manual 4 (playback interaction and resume) is owner-present** and is performed in run 6,
  as the plan's "not machine-checkable" section says.

validate-exit: PASS
