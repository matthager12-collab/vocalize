# Task report: run 8b (app install and lifecycle, Python)

## Changelog

- `vocalize/app.py` (new): paths, the four tool doors (`_run`, list argv, no shell, timeouts), `build`, `bundle_state` (read-only), `agent_state`, `read_status`/`status_dict`/`status_lines`, `write_plist`, `cli_path`/`discoverable`/`override_command`.
- `vocalize/cli.py`: the `app` group; `clip` exit 3.
- `vocalize/local/install.py`: `_MENUBAR_DIR`, `APP_SOURCE`, `APP_PLIST_TEMPLATE`, `APP_SPEC`.
- `vocalize/dictate.py`: `_write_session`; state and nonce.
- `vocalize/readiness.py`: `_app_row`, `_app_agent_row`, `_accessibility_row`. `vocalize/portal.py`: `"app": app.status_dict()`.
- Tests: `tests/test_app_cli.py` (new, 49), `tests/test_app_build.py`, `tests/conftest.py` (`FakeToolchain`), `tests/test_recorder_build.py` (import only), `tests/test_dictate.py`, `tests/test_cli.py`, `tests/test_readiness.py`, `tests/test_portal.py`.
- Docs: `docs/app.md` (new), README, `docs/dictation.md`, CHANGELOG; `review-0.13.0.md` (new, app slice).

## Skipped

Nothing skipped.

## Owner check

2026-09-08, after the real build: the menu-bar icon and its menu were there; speak-the-selection raised the Accessibility prompt once, was granted, and read the selection through Kokoro (the app's log shows the `clip` child); dictation did nothing at first because the old "Dictate with Vocalize" Quick Action still owned control-option-command-D in the Services shortcut table (and "Stop Vocalize" owned the X chord) — exactly the conflict `app install` warns about. The owner removed the two workflows and flushed `pbs`; dictation then worked, the icon went red while recording. Manual checks 5–8 repeat these with the released tool in run 10. The owner's standing note for run 11: the spoken cue must come after the microphone opens, not before (issue #2, T-100/T-101, manual check 10).

## Learnings

- `launchctl list <label>` prints a plist-style dictionary (`"PID" = 1234;`), not the tabular form; a liveness parser needs both.
- `app.status` is written a moment after launch; a status read in the same second as `bootstrap` sees no file, honestly "unknown".
- The portal's request thread must never wait on a 30 s tool timeout; liveness reads get their own 5 s budget.
- `O_NOFOLLOW` refuses a symlink but not a FIFO; `O_NONBLOCK` plus an `S_ISREG` check is what keeps a reader from hanging.
- The README's hotkey path was the Quick Action, not Hammerspoon; the plan's wording was stale.

## Alternatives

- Threading one `status_dict()` through the probe registry so four rows share one `launchctl list` per poll — dropped as a bigger diff than the cost it saves; four 5 s-bounded spawns per poll is acceptable.
- A `Nothing to remove.` early return on `app uninstall` — dropped: an agent can be loaded from a plist that is already gone, so bootout and `defaults delete` always run.

## Specification (post)

As `project-plan.md`, with `status_dict()` returning six keys (bundle, agent, accessibility, hotkeys, hotkey_backend, vocalize) and `hotkeys: failed:<name>` restricted to the four names the Swift can write.
