# Run 7: Hotkey spike and the bundle builder (0.13.0)

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 7; contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-60 | **Spike, 30 minutes:** compile the menu-bar probe, register control-option-command-D with Carbon, press it with Claude Code desktop, a self-drawn terminal and a full-screen Electron app frontmost; hold for 3 s and confirm one down and one up in the log. Record DEC-033 (Carbon, or the event monitor under Accessibility) | vocalize | — | DEC-033 Decided with the three results; `spike-notes.md` § Hotkeys |
| T-61 | `install.build_bundle(BundleSpec)`; `build_recorder` becomes `build_bundle(RECORDER_SPEC)`; golden test pins the recorder's fingerprint keys, stamp version and `swiftc`/`codesign` argv byte for byte; `_uninstall_stt` removes only the recorder bundle and its stamp | vocalize | — | `tests/test_recorder_build.py` unchanged and green; the golden test fails on any argv change; `local uninstall --stt` leaves other bundles |
| T-62 | `[app]` table, chord grammar, `_validate_app_table` (`dictate_mode` accepts `toggle` and `hold`; 0.13.0 treats `hold` as toggle with one warning, so a rollback from 0.13.1 never bricks the CLI), `resolve_app`, wizard render, `settings` lines | vocalize | — | `tests/test_config.py -k app` green incl. modifier-only refusal, unknown key, duplicate chords; a test compares the Python key allowlist with the Swift keycode table |

## Role and isolation

- **Model tier:** Opus — the spike (T-60) is interpreted (three frontmost-app presses read live and judged, not scripted), and the golden test (T-61) guards a microphone re-grant: an argv drift there costs the owner a manual Accessibility re-approval, so the diff needs a careful reader, not a fast one.
- **Branch:** `app`, off `main`, forked after 0.12.0 ships (run 6).
- **Isolation:** shared checkout on `app`; no other run touches `vocalize/local/install.py` or `vocalize/config.py` at this point in the chain. `vocalize/menubar/` does not exist yet — this run does not create it (that is run 8a).
- **Workload:** 3 source files (`vocalize/local/install.py`, `vocalize/config.py`, `vocalize/wizard.py`) + 3 test files (`tests/test_recorder_build.py`, `tests/test_config.py`, `tests/test_app_build.py` — new here with only the golden test, filled out in run 8b). T-60 is a throwaway scratch probe, not a repo file.

## Entry criteria

- on branch `app`, not `main`
- run 6 (`run-6-release-0-12-0`) reported `validate-exit: PASS`
- 0.12.0's key artifact (the built wheel) exists on disk
- suite green at entry
- ruff clean at entry

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- hotkey backend decided (DEC-033 status `Decided` in decisions.md)
- recorder build unchanged (golden test, byte-identical argv)
- `[app]` grammar tests green
- full suite green
- ruff clean
- work committed

## Not machine-checkable (owner present)

- **T-60, the hotkey spike itself.** The owner brings Claude Code desktop, a self-drawn terminal and a full-screen Electron app frontmost in turn, presses control-option-command-D, holds for 3 s, and confirms one down and one up land in the probe's log each time. The DEC-033 record and the automated "Decided" check above only confirm the *outcome* was written down — a human has to run the three presses and watch the log to produce that outcome.

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-nn: done | partial | skipped — reason`), the security-gate result (the negative tests named in the acceptance criteria, listed with their test ids), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run `run-8a-app-swift` reads that report as its entry criterion.
