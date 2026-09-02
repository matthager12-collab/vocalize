# Run 3: Recorder bundle (Swift, build-at-install, `listen --check`)

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 3; contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-30 | `vocalize/recorder/VocalizeRecorder.swift` (~100 lines): `AVAudioRecorder` 16 kHz mono 16-bit with a written-format assertion (`afconvert` fallback), `--device NAME` and `--list-devices`, stop-file polling, `rec.pid`, max-seconds, exit codes 0/2/3/4, `--check` (authorization + device); `Info.plist.in` with `NSMicrophoneUsageDescription`, `LSUIElement`, `LSBackgroundOnly`, `CFBundleIdentifier cards.arda.vocalize.recorder`; the recorder writes its own PID to `rec.pid`; `--check` exits 0/2/3/5 per the contract | vocalize | — | `xcrun swiftc -parse` passes in the suite (skipped without swiftc); the plist template lints; a recorded file opens with stdlib `wave` at 16000 Hz mono 16-bit |
| T-31 | Build-at-install in `install.py`: compile with `xcrun swiftc -O -framework AVFoundation`, assemble the bundle under `~/.cache/vocalize/bin/`, `codesign -s - --force`; rebuild only when the source hash changes (stored in the stamp); print a "re-grant the microphone" warning when a rebuild happens; a missing `swiftc` and an unaccepted Command Line Tools license are detected separately and each prints its fix (`xcode-select --install` / the license command) instead of a raw compiler error | vocalize | T-30, T-21 | tests with a fake compiler: bundle layout, stamp carries the source hash, unchanged source → no rebuild, changed source → rebuild + warning; fake compiler absent → the install message names `xcode-select --install`; fake compiler emitting the license text → the message names the license step |
| T-32 | `vocalize listen --check`: recorder `--check` exit code → authorized (0) / denied (2) / device missing (3) / not determined (5), install state, input device; the message names the next step | vocalize | T-31 | test maps each of the four exit codes to its message; an unknown code prints a generic message and never a traceback |

## Role and isolation

- **Role:** Recorder engineer — judgement (Swift, AVFoundation, codesign, TCC) (Opus)
- **Isolation:** shared checkout on `next-features`, after run 2 (edits `install.py` again, so run 2's T-21 must be committed); new directory `vocalize/recorder/`
- **Workload:** 1 Swift file (unfamiliar API, hardware-adjacent — weight ×3), Info.plist template, install.py build step, cli.py `listen --check`, 2 test files with a fake compiler

## Entry criteria

- on branch next-features
- run 2 validated
- installer generalized (run 2 artifact)
- suite green at entry

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- recorder source exists
- Swift source parses
- plist template lints
- build-at-install logic (fake compiler)
- compiler diagnostics named
- listen --check exists
- listen --check never tracebacks (exit 0/1/2/3/5 only; 1 = vocalize's own install is incomplete, per DEC-010) (pinned to the new artifact after the pre-build audit)
- listen --check measures the bundle's grant, not the calling terminal's (DEC-010)
- full suite green
- ruff clean
- work committed

## Not machine-checkable (owner present)

- First real build on the reference Mac: bundle appears under the cache `bin/` directory, `codesign -dv` shows an ad-hoc signature, first `open -a` shows the microphone prompt naming "Vocalize Recorder" (verification Manual 1). Owner-present.

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-nn: done | partial | skipped — reason`), the security-gate result (the negative tests named in the acceptance criteria, listed with their test ids), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run 4 (`run-4-dictation`) reads that report as its entry criterion.
