# Run 8a: The menu-bar app, Swift (0.13.0)

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 8a: The menu-bar app, Swift (0.13.0); contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-70 | `vocalize/menubar/VocalizeApp.swift` + `Info.plist.in`, **complete**: status item with the four SF Symbol states, both hotkey backends (selected per DEC-033 at build time), the dispatch table incl. hold (down → `dictate --start`, up → `dictate --stop`) and toggle, speak-the-selection with the Accessibility prompt and `changeCount` check, stop on the concurrent queue, the spawn contract, binary discovery with the `UserDefaults` override, the `~/.config/vocalize` directory watcher and `settings` re-read, the `~/.cache/vocalize` watcher reading `dictate.session` state and nonce, `app.status` and the `app.log` size cap on every dispatch, the `dictate.copied` watcher with the nonce and frontmost checks, the override checked like every candidate, fixed-string notifications, the menu (Dictate, Speak selection, Stop, Cancel dictation, Open portal, Reload settings, Quit) | vocalize | T-60, T-62 | `xcrun swiftc -parse` clean; `plutil -lint` clean; the source contains no string that could carry pasteboard or transcript text into a notification (grep for `NSPasteboard.string` = 0); the override check and the nonce check are visible in source as named functions; the settings parser ignores any `app.*` key it does not register as one of the four chord names, so a later `app.*` key needs no rebuild; an unrecognised `dictate.session` state maps to a distinct attention icon rather than the recording icon, visible in source as a named default branch (DEC-036, owner question 3) |

## Role and isolation

- **Role:** Swift app engineer — Opus (unfamiliar AppKit and Carbon API; the source must be complete first time because every later edit costs a re-grant: any change to `VocalizeApp.swift` after 0.13.0 ships is an Accessibility re-grant, per design.md § LaunchAgent and bundle identity)
- **Isolation:** branch `app`, off `main`; works only under `vocalize/menubar/`; `vocalize/local/install.py` and everything under `vocalize/` outside `menubar/` is read-only for this run (Phase 8b builds the Python side that calls `build_bundle(APP_SPEC)` against this source)
- **Workload:** 2 source files (`vocalize/menubar/VocalizeApp.swift`, `vocalize/menubar/Info.plist.in`, both new) + 0 test files — this run is proven by `swiftc -parse`, `plutil -lint` and source-pattern greps, not pytest; the Python-side stub test that compares the chord-key allowlist against the Swift keycode table belongs to Phase 7 (T-62) and Phase 8b, not here

## Entry criteria

- on branch `app` (not `main`)
- run 7 (`run-7-hotkey-spike-and-builder`) validated: its `report.md` contains `validate-exit: PASS`
- run 7's key artifacts exist: DEC-033 decided (no longer `Deferred` in `decisions.md`) and the `[app]` chord table lands in `vocalize/config.py` (`_validate_app_table`, `resolve_app`) so the Swift keycode table has something to match
- suite green at entry
- ruff clean at entry

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- Swift parses, plist lints (verification.md § Phase 8a exit)
- no text can reach a notification (`NSPasteboard.string`/`readObjects` absent from the source)
- the override check and the nonce check exist as named functions (`checkedBinary`, `pasteIfNonceMatches`)
- source committed under `vocalize/menubar/`
- full suite green (regression: this run touches no Python)
- ruff clean
- work committed

## Not machine-checkable

- Manual checks that need a human pressing the hotkey with real apps frontmost (Claude Code desktop, a self-drawn terminal, a full-screen Electron app) are covered by DEC-033's spike in run 7, not by this run; this run only proves the Swift source parses and carries the required shapes.
- Whether the four SF Symbol states are visually correct, and whether the menu items read sensibly, is a look-at-it check for the owner during Phase 8b or the Phase 10 manual checks — no owner presence is required for this run itself.

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-70: done | partial | skipped — reason`), the security-gate result (the pasteboard/notification and override/nonce checks named above, listed with their grep results), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run `run-8b-app-python` reads that report as its entry criterion.
