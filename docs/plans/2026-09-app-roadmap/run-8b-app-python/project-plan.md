# Run 8b: App install and lifecycle, Python (0.13.0)

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 8b: App install and lifecycle, Python (0.13.0); contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-71 | `dictate.session` gains `state` and a per-dictation `nonce`; `listen()` sets `recording` after `_launch_recorder` and `transcribing` in `_mark_finishing`; `_read_session` ignores unknown keys; `cli.clip` exits 3 on the credential-shaped refusal | vocalize | — | `tests/test_dictate.py -k state` asserts the three words in order, a fresh nonce per claim, and the file removed on every exit path; `clip` exit code test |
| T-72 | `vocalize/app.py` + CLI group `app install [--yes] / uninstall [--yes] / status [--json] / restart`: `build_bundle(APP_SPEC)`, LaunchAgent plist via `plistlib`, `bootout` then `bootstrap`, `tccutil reset Accessibility` on rebuild with `APP_REGRANT_WARNING`, the bundle under `~/Library/Application Support/vocalize/`, liveness from `launchctl list <label>` with parse failure = `unknown`, the binary-discovery check against `sys.argv[0]`, the Hammerspoon and Services warnings, `app.status` reader parsed as untrusted words, `kickstart -k` for restart, uninstall scope per design | vocalize | T-61, T-70 | `tests/test_app_cli.py` green with fake `launchctl`, `tccutil`, `pgrep`: plist keys, bootout-before-bootstrap, tccutil argv only on rebuild, status JSON for absent, stale and garbage `app.status`, uninstall removes exactly the listed files |
| T-73 | Readiness rows `app`, `app agent`, `accessibility` (registered when the bundle exists); `/api/state` gains `app` | vocalize | T-72 | `tests/test_readiness.py -k app` green; `tests/test_portal.py -k app` asserts the key |
| T-74 | Tests not covered above: `tests/test_app_build.py` (layout, stamp, rebuild rule, `FakeToolchain` shared from conftest, swift parse and plutil skipped without tools) | vocalize | T-72 | green |
| T-75 | Docs: `docs/app.md` (install, uninstall, status, restart, the re-grant rule incl. bundle or stamp loss, `VocalizeBinary` override, Quit stays quit), README hotkeys section replaces Hammerspoon with `vocalize app install` and keeps the ten-line snippet, README known-limitations narrows "safe to delete `~/.cache/vocalize`" to exclude `bin/`, `docs/dictation.md` hotkey section, CHANGELOG | vocalize | T-72 | present; docs-match-CLI command exits 0 |
| T-76 | Security review of the app slice: spawn environment, no text in argv or notifications, `app.status` parsing, `tccutil` only on rebuild, LaunchAgent path ownership | vocalize | T-70–T-75 | findings table in `review-0.13.0.md` with no Critical or High Open |

## Role and isolation

- **Role:** App lifecycle, Python — Sonnet (mechanical Python against a design that already names every plist key, path and command); T-72 gets an Opus review before commit (the `tccutil`/`launchctl` sequencing and the uninstall scope are the one place a mistake in this run either bricks the Accessibility grant, leaves stray files under `~/Library`, or races `bootout`/`bootstrap`)
- **Isolation:** branch `app`, after Phase 8a; works in `vocalize/app.py` (new), `vocalize/cli.py`, `vocalize/dictate.py`, `vocalize/readiness.py`, `vocalize/portal.py`; `vocalize/menubar/VocalizeApp.swift` and `Info.plist.in` are read-only for this run — this run calls `build_bundle(APP_SPEC)` against Phase 8a's committed Swift source, it does not edit it (any edit there after 0.13.0 ships is an Accessibility re-grant, per design.md § LaunchAgent and bundle identity)
- **Workload:** 5 source files (1 new: `app.py`; 4 edited: `cli.py`, `dictate.py`, `readiness.py`, `portal.py`) + 5 test files (`test_dictate.py`, `test_app_cli.py`, `test_app_build.py`, `test_readiness.py`, `test_portal.py`)

## Entry criteria

- on branch `app` (not `main`)
- run 8a (`run-8a-app-swift`) validated: its `report.md` contains `validate-exit: PASS`
- run 8a's key artifact exists: `vocalize/menubar/VocalizeApp.swift` and `Info.plist.in` are committed (the Swift source this run's `build_bundle(APP_SPEC)` compiles)
- suite green at entry
- ruff clean at entry

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- install and lifecycle: `tests/test_app_cli.py`, `tests/test_app_build.py` green (verification.md § Phase 8b exit)
- session state and nonce: `tests/test_dictate.py -k "state or nonce"` green
- readiness rows and the `/api/state` key registered and green
- review closed (`review-0.13.0.md`, no Critical or High row left Open)
- full suite green
- ruff clean
- work committed

## Not machine-checkable

- **Real build on this Mac** (verification.md § Phase 8b exit, "Real build on this Mac"): `vocalize app install --yes` followed by `vocalize app status --json` asserting `bundle: current` and `agent: loaded` needs the owner present for the Accessibility permission prompt — a script cannot click "Allow" in the system dialog. The owner runs this once after the automated checks pass, on the reference Mac.
- Manual checks 5–8 in verification.md (first install, speak-selection, relaunch, upgrade) belong to the Phase 10 release gate with the owner present, not to this run.

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-nn: done | partial | skipped — reason`), the security-gate result (the `tccutil`/`launchctl` sequencing and uninstall-scope tests named above, listed with their test ids, plus the Opus review outcome on T-72), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run `run-9-doctor-integrate-setup` reads that report as its entry criterion.
