# Task report: run 7 (hotkey spike and the bundle builder)

## Changelog

- `vocalize/local/install.py`: `BundleSpec` (frozen dataclass), `RECORDER_SPEC`, `_recorder_spec()`, `bundle_path`/`bundle_binary`/`bundle_stamp_path`, `_fingerprint` (no `entitlements_sha256` key when a spec has none), `write_bundle_stamp`/`read_bundle_stamp`/`bundle_is_current`, `build_bundle`; recorder wrappers kept; `_run_build_step`/`_diagnose_compiler`/`_swap_in` take a noun/stem with recorder defaults.
- `vocalize/cli.py`: `_uninstall_stt` by name; `settings` prints the four `app.*` lines; `resolve_app` import.
- `vocalize/config.py`: the `[app]` block; `KNOWN_CONFIG_KEYS` + `app`; load dispatch.
- `vocalize/wizard.py`: `_TABLE_KEYS` + `app`; the render block.
- Tests: `tests/test_app_build.py` (new), test_config (+10), test_wizard (+1), test_cli (settings lines), test_local_install (uninstall by name).
- Docs: decisions.md DEC-033 Decided A and DEC-032 wording, spike-notes.md § Hotkeys, CHANGELOG Unreleased.

## Skipped

Nothing skipped.

## Learnings

- A module-level spec that captures paths at import time breaks every test that monkeypatches the constants; re-read them at call time behind the old function names.
- `write_stamp`/`read_stamp`/`stamp_path` already exist in install.py for the model manifests; bundle helpers need their own names.
- `config._warn` dedupes per process; a test asserting "one warning" must reset `config._warned`.
- The 0.13.0 app lives under `~/Library/Application Support/vocalize`, not `~/.cache/vocalize/bin` (design.md § LaunchAgent and bundle identity); the recorder's bin dir is never shared.
- A review agent with `CliRunner` and a partial monkeypatch can destroy real state; agents get no destructive CLI commands, ever.

## Alternatives

- Replacing `build_recorder`'s callers with `build_bundle(RECORDER_SPEC)` — dropped: the wrappers keep every caller and `tests/test_recorder_build.py` untouched.
- Refusing `dictate_mode = "hold"` in 0.13.0 (DEC-032's original wording) — dropped for accept-and-warn, as T-62 specifies.

## Specification (post)

As `project-plan.md`. One contract for run 8a: the Swift keycode table is a dictionary literal named `keyCodes` with string keys equal to `config.CHORD_KEYS`.
