# Report: run 11, cue trim, hold-to-talk, auto-paste

Date 2026-09-08. Branch `hold-to-talk` (worktree `.claude/worktrees/hold-to-talk`). Source plan in [project-plan.md](./project-plan.md).

- T-100: done — spike recorded in `spike-notes.md` § Cue: take grows incrementally on both Yeti Stereo Microphone (USB) and Mat's AirPods Pro (Bluetooth) inputs; verdict: `branch: trim` (first growth past 4096-byte header signals open microphone; cue plays after and is trimmed from the head).
- T-101: done — `_wait_for_audio`, `_AUDIO_GRACE` (5 s), cue marker written in `_launch_recorder`, `_trim_cue` first in `_finish_take`, state `recording` reported after the cue.
- T-102: done — `dictate --start` and `--stop` per design; `--start` is idempotent; `--stop` at 0.5 s transcribes and never cancels; `[app] dictate_mode` accepts `hold`.
- T-103: done — `[stt] paste`: `_stop` writes `dictate.copied` carrying epoch + session nonce with 0600 mode and `O_NOFOLLOW`; `settings` prints `stt.paste`.
- T-104: done — tests green for T-101–T-103 across `tests/test_dictate.py`, `tests/test_cli.py`, `tests/test_config.py`.
- T-105: done — `docs/dictation.md` cue paragraph and hold mode documented; paste privacy note; CHANGELOG 0.13.1; version bumped to 0.13.1.

Security gate: session nonce matching, 0600 file mode, and `O_NOFOLLOW` flag verified in `tests/test_dictate.py` (`test_paste_marker_written_securely`, `test_paste_marker_carries_session_nonce`).

Deferred: nothing. Manual checks 9–11 verified with owner.

validate-exit: PASS
