# Report: run 7, hotkey spike and the bundle builder

Date 2026-09-07. Branch `app` off `main` after 0.12.0 (worktree `.claude/worktrees/app`). Full account in [task-report.md](./task-report.md).

- T-60: done — the Carbon probe delivered one down and one up with Claude desktop (normal window, held 2 s), Ghostty (3 s) and Claude desktop full-screen (3 s) in front; register status 0 with Hammerspoon running on S and X. DEC-033 Decided A (Carbon); `spike-notes.md` § Hotkeys.
- T-61: done — `BundleSpec`, `RECORDER_SPEC`, `build_bundle`; `build_recorder` is `build_bundle(_recorder_spec())`, with the spec re-read from the module constants at call time so the existing tests' monkeypatches still drive the build; `tests/test_recorder_build.py` unchanged and green; `tests/test_app_build.py` pins the swiftc and codesign argv, the subprocess kwargs, the staging name, the fingerprint keys in order and stamp version 3, and proves the recorder's door and the general one make identical calls. `local uninstall --stt` removes the model dir, the recorder bundle, its stamp and any crash-era `.recorder-build-*` / `.recorder-old-*` leftover, by name; a neighbour in the bin dir survives.
- T-62: done — `[app]` table: `parse_chord`/`chord_text`, `_validate_app_table` (unknown keys warn; chords pairwise distinct against the resolved set; modifier-only, no-ctrl/cmd, unknown key, repeated modifier, spaces and empty tokens refused), `resolve_app` (canonical chords; `hold` resolves to `toggle` with one warning), wizard render, `settings` prints `app.dictate/dictate_mode/speak/stop`. The keycode-table comparison test skips until run 8a creates `vocalize/menubar/VocalizeApp.swift`; it then reads a `keyCodes` dictionary keyed by the same names — run 8a must name it so.

Security gate: the golden tests (`tests/test_app_build.py`, `-k golden`: 3) and `tests/test_recorder_build.py` (unchanged) — the recorder's build argv, kwargs and stamp are byte-identical to main; a 46-agent adversarial review (three lenses, three refuters per finding) confirmed no Medium or above in the code; four lows fixed here (`_recorder_fingerprint` read the import-time spec; the uninstall's stated reason and test premise wrongly said the bin dir is shared with the app; the hold-warning test depended on the process-global warn set; DEC-032's text said 0.13.0 refuses `hold`) plus the refuted-but-hygienic sweep of crash leftovers.

Incident: one reviewer agent ran the real `vocalize local uninstall --stt --yes` through CliRunner with the wrong manifest monkeypatched and deleted the owner's `~/.cache/vocalize/models/whisper`. The recorder bundle, its stamp and Kokoro survived (no re-grant). `large-v3-turbo-q5_0` was re-downloaded and verified in this run; the owner's other whisper models are gone until re-installed. A standing rule now forbids agents from running any command that changes real state under `~/.cache/vocalize`, `~/.config/vocalize`, `~/Library` or the keychain.

Deferred: nothing. The keycode comparison is a skip by design until run 8a.

Suite: 1971 passed, 4 skipped. Ruff clean. Gate 11 of 11.

validate-exit: PASS
