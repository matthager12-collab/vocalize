# Run 11: Cue trim, hold-to-talk, auto-paste (0.13.1, Python only)

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 11: Cue trim, hold-to-talk, auto-paste (0.13.1, Python only); contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-100 | **Spike, 1 hour:** does `take.wav` grow incrementally, and how long after `rec.pid` does the first growth land, on the built-in microphone and a Bluetooth input? Record in `spike-notes.md` § Cue and pick the branch (trim, or fallback order) | vocalize | — | six numbers and a branch verdict |
| T-101 | `_wait_for_audio`, `_AUDIO_GRACE`, the `cue` file, `_trim_cue` first in `_finish_take`, state `recording` after the cue; fallback branch keeps `only=` and today's order | vocalize | T-100 | the growing fake recorder test asserts the frame count the fake `uv` receives for all three cue modes and both branches; a `cue` past EOF leaves the take untouched |
| T-102 | `dictate --start` / `--stop` per design § Hold-to-talk; `[app] dictate_mode` accepts `hold` | vocalize | — | `--start` idempotent; `--stop` at 0.5 s transcribes and never cancels; no session → 0; dead recorder → failure path |
| T-103 | `[stt] paste`: `_stop` writes `dictate.copied` (epoch plus the session's nonce) after `copy_to_clipboard`; `settings` prints `stt.paste` | vocalize | — | marker written only when `paste` is true, 0600, carries the nonce of the session that produced it; never on nothing-heard |
| T-104 | Tests for T-101–T-103 | vocalize | T-103 | green |
| T-105 | Docs: `docs/dictation.md` cue paragraph rewritten, hold mode, paste privacy note; CHANGELOG 0.13.1; version bump | vocalize | T-104 | present |

## Role and isolation

- **Role:** Opus — a state machine (start/stop/hold, the nonce handshake) and a trim keyed to real audio timing (the cue spike's numbers decide the branch); no mechanical precedent to copy from
- **Isolation:** branch `hold-to-talk`, off `main`, after run 10 (`run-10-release-0-13-0`); works under `vocalize/dictate.py` (`_wait_for_audio`, `_trim_cue`, `_finish_take`, `--start`/`--stop`, `dictate.copied`), `vocalize/cli.py` (the `dictate` command's new flags, `settings` printing `stt.paste`), `vocalize/config.py` (the `[stt] paste` key); does **not** touch `vocalize/menubar/VocalizeApp.swift` — any edit there this phase is a re-grant and must be refused — nor `vocalize/app.py` (the Swift-adjacent lifecycle side is out of scope; this phase is Python only)
- **Workload:** 3 source files (`vocalize/dictate.py`, `vocalize/cli.py`, `vocalize/config.py`) + 3 test files (`tests/test_dictate.py`, `tests/test_cli.py`, `tests/test_config.py`)

## Entry criteria

- on branch `hold-to-talk` (not `main`)
- run 10 (`run-10-release-0-13-0`) validated: its `report.md` contains `validate-exit: PASS`
- run 10's key artifact exists: `dist/vocalize_cli-0.13.0*.whl`
- `vocalize/menubar/` matches `main` (nothing to re-grant yet)
- suite green at entry
- ruff clean at entry

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- Swift untouched: `vocalize/menubar/` still matches `main` (no re-grant slipped in)
- cue spike recorded: `spike-notes.md` § Cue carries a branch verdict
- no cue word reaches the worker: the frame count is asserted for all three cue modes and both branches (trim, and the fallback order)
- hold-to-talk: `--start` is idempotent, `--stop` never cancels
- paste marker carries the session's nonce, and only when `paste` is true
- full suite green
- ruff clean
- work committed

## Not machine-checkable

- Manual check 9 (verification.md): cue spike (T-100) on the built-in **and a Bluetooth input** — owner present, six numbers. T-100 needs the owner for the Bluetooth leg; the built-in-only half is not enough to record a verdict.
- Manual check 10: with `cues = "words"`, the transcript begins with the first spoken word and never carries "Start" — checked by ear on both the built-in and Bluetooth input.
- Manual check 11: `dictate_mode = "hold"` end to end (hold, speak, release, text lands), and the paste privacy note ("Copied, not pasted (window changed).") on a window switch during transcription.

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-100`–`T-105`: done | partial | skipped — reason), the security-gate result (the nonce check and the 0600/`O_NOFOLLOW` marker discipline named in T-103's acceptance criteria, listed with their test ids), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run `run-12-release-0-13-1` reads that report as its entry criterion.
