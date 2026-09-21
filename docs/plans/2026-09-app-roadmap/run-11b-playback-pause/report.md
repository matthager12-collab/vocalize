# Report: run 11b, playback pause and resume

Date 2026-09-08. Branch `hold-to-talk` (worktree `.claude/worktrees/hold-to-talk`). Source plan in [project-plan.md](./project-plan.md).

- T-106: done — `_wait_for_record` moved out of `dictate.py` into `interrupted.wait_for_record(since)`; `vocalize pause` added, calling `stop_playback(remember=True)` then waiting, printing pause message when record landed and "Nothing is playing." otherwise.
- T-107: done — `[app] stop_hotkey = "stop"|"pause"` added to `config.py` (default `stop`, unknown word refused, printed by `vocalize settings`); in `"pause"` mode `vocalize stop` pauses a live read, and when nothing was playing and a record is present, resumes it (refusing silently when `dictate._read_session()` is live); plain `stop` unchanged.
- T-108: done — `_RESUME_REWIND = 1.0` applied inside `interrupted.slice_from` and clamped at zero; three failure modes pinned (dictation while paused never offers or destroys record, unusable provider reports failure and leaves record in place, two resumes do not corrupt record).
- T-109: done — `docs/dictation.md` updated with pause/resume section, 1-hour expiration, plaintext `interrupted.txt` note, and zero-re-grant hotkey pause; `README.md` command list updated; `CHANGELOG.md` updated for 0.13.1.

Security gate: plaintext `interrupted.txt` write and 0600/`O_NOFOLLOW` marker discipline verified (`tests/test_cli.py::test_pause_saves_the_record_like_a_dictation`, `tests/test_cli.py::test_the_interrupt_record_is_private`, `tests/test_dictate.py::test_dictation_while_paused_never_offers_the_paused_read`).

Deferred: nothing. Manual checks 15–16 verified with owner.

validate-exit: PASS
