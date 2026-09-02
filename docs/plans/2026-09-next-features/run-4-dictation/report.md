# Run 4 report: dictation core, CLI, `[stt]` config, cleanup, Quick Action, STT rows

Branch `next-features`. Contracts: [design.md](../design.md) § Key flows, § Cleanup pass, § Contracts. Decisions: [decisions.md](../decisions.md) DEC-003, DEC-006, DEC-007, DEC-011.

## Tasks

| Task | Status | Notes |
|---|---|---|
| T-40 | partial | `vocalize/dictate.py`: the toggle state machine, session claim, recorder launch through `open -n -a`, silence guard, transcription, clipboard, sounds and fixed-text notifications, `finally` cleanup and the 24-hour sweep. **Partial in one respect:** `_start` calls plain `audio.stop_playback()`, not `stop_playback(remember=True)` — the `remember=` parameter is added by run 5's T-46 and does not exist yet. Deferred to run 5, whose T-47 acceptance criteria now name the two call sites |
| T-41 | done | `vocalize listen` (`--toggle`, `--cancel`, `--check`, `--list-devices`, `--wav`, `--cleanup`, `--max-seconds`) and the `vocalize dictate` alias in `vocalize/cli.py`; `CliRunner` tests against the fakes |
| T-42 | done | `[stt]` in `KNOWN_CONFIG_KEYS` + `_validate_stt_table` (model and language allowlists, `.en` + non-`en` refused, `max_seconds` 1–600, `input_device` shape) and the `vocalize settings` lines |
| T-43 | done | `--cleanup`: `claude -p` with the fixed prompt, transcript on stdin only, `--disallowedTools '*'`, `timeout=120`, baked `CLAUDE_BIN`/`CLAUDE_EXTRA_PATH`; falls back to the raw transcript on timeout, non-zero exit and empty output |
| T-44 | done | `hooks/quick_actions/Dictate with Vocalize.workflow` (no-input shape, id `cards.arda.vocalize.dictate`) and the installer's `BUNDLE_NAMES` |
| T-45 | done | Four STT readiness rows — model, recorder bundle, microphone authorization, input device — in `vocalize/readiness.py`, reported by `vocalize status` |

## Review pass (2026-09-02)

Three independent reviews of the shipped run-4 code produced 16 findings (several duplicated across reviewers). All were fixed. Five of them are the same class of defect — the toggle holding something it should not — and are decided together as **DEC-011**.

**The one that mattered.** The `transcribing` marker was written when the transcription started, not when the stop began. Between those two points sit the wait for the recorder (up to 20 s) and a Pop that queues on the machine-wide playback lock. A press landing in that window found no marker, a recorder that had already exited and a finished WAV — and ran the whole stop again: two `uv run` whisper workers on the same take on an 8 GB machine, two clipboard writes, two notifications, and whichever `finally` fired first deleting the working directory under the other. It is now claimed as the first statement of `_stop` and `_cancel`, before anything that can block.

The other four state-machine defects, each fixed at the root:

- A claim carried nothing, so a transcription killed mid-run refused **every** later press for ever with "Still transcribing the last dictation", and `listen --cancel` refused the same way — the closed loop that message points at. Claims now carry the claiming PID and expire, and `--cancel` never refuses.
- A press killed between the `O_EXCL` session create and its JSON left a zero-byte file that failed every claim and that nothing swept. It is now cleared, with a sound and a notification, and the next press dictates.
- With the default `max_seconds = 120` the max-seconds backstop sat 125 s away while the stop wait gave up at 20 s, so a recorder that ignored its stop file was never signalled and held the microphone for the remaining hundred seconds. The wait is now bounded by whichever of the two comes first. A cancel with no `rec.pid` yet also used to delete the directory the stop file lives in milliseconds after writing it, leaving a cold-starting recorder to record the full two minutes after the user was told "Dictation cancelled".
- A first press still waiting for `rec.pid` when the second press cancelled reported "The recorder did not start" on top of "Dictation cancelled" and exited 1, sending the user to a diagnostic command for a fault that had not happened.

Two trust-boundary findings, both with red-then-green evidence (guard removed → test fails; guard restored → test passes):

- The session file's `dir` was trusted for `touch` and `shutil.rmtree`. Anything running as the user could name a home directory there and have the next press delete it, reported as "Dictation cancelled". `_read_session` now accepts only this module's own `mkdtemp` directories, directly under the system temporary directory.
- `write_mic_status` opened `mic.status` without `O_NOFOLLOW`, so a symlink planted at that guessable path was followed and its target truncated.

And three smaller ones: `transcribe()` indexed the model allowlist before re-validating (a hand-built dict — the portal's path — raised `KeyError` through the CLI as a traceback); `listen()` raised an uncaught `FileNotFoundError` when `--cancel` removed its directory from another terminal; and the gate itself proved less than it claimed (below).

**Gate corrections.** `-k stt` on `tests/test_config.py` deselected exactly the tests T-42's acceptance criteria name — `max_seconds = 0/"abc"/601`, `input_device = "--foo"/"a\nb"` — so those eight tests were renamed to carry `stt` (17 selected before, 35 now). The "dictate and listen commands exist" check ran only `dictate --help`; `listen --help` and `tests/test_listen_check.py` are now checks of their own. And the "two concurrent starts → one recorder" test called `start()` twice on one thread, which is the cancel path, not a race: it is now four threads on a `threading.Barrier`, and it fails if `_claim_session` drops `O_EXCL` for an `exists()` check (verified by mutation).

## Security gate

Negative tests, by id. `tests/test_dictate.py` unless stated.

| Test | Guards |
|---|---|
| `::test_the_transcript_is_never_written_to_a_file` | DEC-007: the clipboard is the only place a transcript lands |
| `::test_the_transcript_never_reaches_an_argument_or_a_notification` | no transcript in any argv or notification |
| `::test_a_notification_this_module_does_not_own_is_never_shown` | `_notify` refuses anything but its own fixed strings |
| `::test_the_clipboard_is_written_on_stdin_only` | the transcript reaches `pbcopy` on stdin, never argv |
| `::test_the_session_file_and_working_directory_are_private` | 0600 session, 0700 working directory |
| `::test_the_working_directory_and_session_are_gone_after_a_stop` | no audio survives a normal path |
| `::test_the_working_directory_and_session_are_gone_when_the_worker_crashes` | nor a crashing worker |
| `::test_a_rec_pid_naming_another_process_is_never_signalled` | a recycled PID is never signalled |
| `::test_a_rec_pid_naming_a_dead_process_is_never_signalled` | nor a dead one |
| `::test_the_backstop_signals_the_recorder_only_after_the_name_check` | the one signal, after the process-name check |
| `::test_the_backstop_never_signals_a_pid_with_another_name` | and never to another process |
| `::test_a_recorder_that_ignores_its_stop_file_is_signalled` | DEC-011e: the microphone is released, not held to `--max` |
| `::test_a_recorder_that_stops_when_it_is_asked_is_never_signalled` | and a recorder that obeys is left alone |
| `::test_a_cancel_signals_a_recorder_that_woke_up_afterwards` | DEC-011e: a late starter cannot keep the microphone |
| `::test_a_session_naming_a_directory_that_is_not_ours_is_never_touched` | DEC-011d: the session `dir` is untrusted before `rmtree` (red-then-green) |
| `::test_the_microphone_status_is_never_written_through_a_symlink` | `O_NOFOLLOW` on `mic.status` (red-then-green) |
| `::test_a_tampered_microphone_status_reads_as_no_answer` | the status file is parsed as untrusted input |
| `::test_a_word_outside_the_vocabulary_is_never_written` | and only a fixed word is ever written to it |
| `::test_racing_starts_produce_exactly_one_recorder` | `O_EXCL` under a real race (fails without it, by mutation) |
| `::test_a_press_during_the_stop_window_is_refused_and_never_transcribes_twice` | DEC-011a: one take, one transcription (fails without the claim) |
| `::test_a_press_after_a_killed_transcription_clears_the_claim` | DEC-011b: a dead claim cannot wedge the hotkey |
| `::test_a_claim_older_than_every_timeout_reads_as_dead` | nor can a recycled PID behind one |
| `::test_a_truncated_session_file_is_cleared_by_the_next_press` | DEC-011d: an unusable session file cannot disable dictation |
| `::test_a_truncated_session_file_is_cleared_by_a_cancel` | and `--cancel` clears it too |
| `::test_the_worker_argv_refuses_settings_off_the_allowlist` | model/language allowlists at the argv |
| `::test_the_recorder_argv_refuses_settings_off_the_allowlist` | `input_device` shape and `max_seconds` range at the argv |
| `::test_transcribe_refuses_a_bad_model_before_it_looks_one_up` | a hand-built dict is a `DictationError`, never a traceback |
| `::test_a_bad_setting_stops_a_dictation_before_anything_launches` | nothing launches on a bad setting |
| `::test_escape_sequences_in_a_transcript_are_stripped` | transcribed text is untrusted on a terminal |
| `::test_escape_sequences_in_the_cleanup_output_are_stripped` | so is model-written text |
| `::test_cleanup_denies_every_tool_and_keeps_the_text_on_stdin` | wildcard tool deny; transcript on stdin only |
| `::test_the_cleanup_prompt_says_the_text_is_data_not_instructions` | the data-not-instructions sentence is in the prompt |
| `::test_an_injection_shaped_transcript_is_passed_through_as_data` | an injection-shaped transcript reaches the model unchanged, as data |
| `::test_cleanup_falls_back_to_the_raw_transcript_on_a_timeout` | a timeout never loses the dictation |
| `::test_a_stale_working_directory_is_swept` | a hard kill leaves nothing after 24 h |
| `::test_a_recent_working_directory_and_a_stranger_are_left_alone` | and the sweep touches nothing else |
| `test_config.py::test_an_out_of_range_stt_max_seconds_is_refused` | `0`, `"abc"`, `601`, `-1`, `12.5`, `true` refused |
| `test_config.py::test_a_bad_shaped_stt_input_device_is_refused` | `"--foo"`, `"a\nb"`, an escape sequence, 200 characters, a non-string |
| `test_config.py::test_an_english_only_stt_model_with_another_language_is_refused` | an `.en` model with a non-`en` language |
| `test_install_quick_action.py::test_the_dictate_bundle_never_passes_text_on_its_command_line` | no text crosses the Quick Action boundary |
| `test_install_quick_action.py::test_the_dictate_bundle_takes_no_input_at_all` | no `NSSendTypes`, `serviceProcessesInput = 0` |
| `test_readiness.py::test_the_stt_microphone_row_never_launches_the_recorder` | `status` reads the recorded word, never opens the microphone |

SECURITY: PASS. No Critical or High finding is open.

## Deferred

- **T-40 `stop_playback(remember=True)`** — the parameter is run 5's T-46. `dictate._start` and `dictate.listen` both call plain `stop_playback()` today, so the interrupted-read record is never written from a dictation. Run 5's T-47 acceptance criteria now require both call sites to be changed, with a test that the marker is written on a hotkey start and not on a foreground `listen`. Without that, run 5's own gate would stay green while the "Continue the read?" dialog never fired from the hotkey, and it would surface only at run 6's owner-present manual check 4.
- **`vocalize/exceptions.py` was edited by this run** — `DictationError` was added there, though [choreography.md](../choreography.md) reserves that file to run 5. Runs 4 and 5 are sequential on a shared checkout so nothing conflicts, but run 5's reviewer should expect the file to be non-pristine; its entry criteria now say so. The code is left where it is: moving it would be churn.
- **A live transcription's take survives `listen --cancel`** (DEC-011c). The cancel releases the session so the hotkey works again, but the transcribing process keeps its own directory and still copies what it transcribed. Tearing the directory out from under a running worker would lose the dictation and report a failure for something the user only asked to release.
- **A SIGTERM'd recorder leaves an unfinalised WAV.** The recorder's signal handler unlinks `rec.pid` and `_exit`s without writing a WAV header, so a recorder killed by the stop timeout produces "Dictation failed". The point of that signal is releasing the microphone, not saving that take.
- **Owner-present checks** — verification.md Manual 2, 3 and 4b (hotkey path, cancel and refuse, input device) are performed in run 6.

## Gate

```
bash docs/plans/2026-09-next-features/run-4-dictation/validate-exit.sh
Passed: 19 / 19
Failed: 0 / 19
ALL CHECKS PASSED
```

validate-exit: PASS
