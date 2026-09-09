# Task report: run 8a (the menu-bar app, Swift)

## Changelog

- `vocalize/menubar/VocalizeApp.swift` (new): the whole app, sections Build-time choices, Paths, Fixed user-visible strings, Binary discovery, app.status, Spawning, The chord grammar, Settings, Hotkeys (Carbon and monitor), The status item, Watchers, Synthetic keystrokes, The app, main.
- `vocalize/menubar/Info.plist.in` (new): identity `cards.arda.vocalize.app`, executable `vocalize-app`, `LSUIElement`, version 1.0, no usage strings.
- `docs/plans/2026-09-app-roadmap/run-8a-app-swift/validate-exit.sh` and `verification.md`: one `swiftc -parse` per file; committed checks include untracked files.
- `tests/test_config.py`: the keycode test parses the literal after the `=`.

## Skipped

Nothing skipped. Nothing was built, signed, installed or launched on the owner's Mac; that is run 8b's `build_bundle(APP_SPEC)` and `app install`.

## Learnings

- `swiftc -parse` with two single-file programs rejects both: top-level code is only allowed in the sole input.
- A directory vnode source never reports a write into an existing file; the icon needs a poll or a per-file source.
- `UNUserNotificationCenter.current()` aborts (ObjC exception) when LaunchServices does not know the bundle; an accessory app launched by launchd from Application Support cannot rely on it.
- A status item must be created in `applicationDidFinishLaunching`, never in a global initialiser.
- Fixed-string notices as an enum, with `show(_ notice: Notice)`, make "no dynamic text" a type-level fact the greps cannot miss.

## Alternatives

- Watching `dictate.session` with a per-file vnode source re-armed on every directory event — dropped for the half-second poll: simpler, no re-arm races, and the file is a few hundred bytes.
- `UNUserNotificationCenter` with provisional authorization — dropped for the title flash (the abort path above).
- Requiring `lsregister` in run 8b so notifications could be used — dropped with the above.

## Specification (post)

As `project-plan.md`, with the deviations listed in report.md. The `keyCodes` literal is `let keyCodes: [String: UInt32] = [ ... ]`, flat, 48 names.
