# Run 11b: Playback pause and resume (0.13.1, Python only)

Part of [choreography.md](../choreography.md). Source plan: [plan.md](../plan.md) § Phase 11b: Playback pause and resume (0.13.1, Python only); contracts in [design.md](../design.md); proof commands in [verification.md](../verification.md).

## Scope

| # | Task | Repo | Depends on | Acceptance criteria |
|---|---|---|---|---|
| T-106 | Move `_wait_for_record` out of `dictate.py` into `interrupted.wait_for_record(since)` (both callers use it); add `vocalize pause`, calling `audio.stop_playback(remember=True)` then waiting, printing "Paused. Resume it within the hour with: vocalize resume" when a record landed and "Nothing is playing." otherwise | vocalize | — | `tests/test_cli.py::test_pause_saves_the_record_like_a_dictation`, `::test_pause_with_nothing_playing_reports_it` and `::test_pause_in_the_chunk_gap_records_the_queued_piece` green; `grep -q 'interrupted.wait_for_record' vocalize/dictate.py` exits 0 |
| T-107 | `[app] stop_hotkey = "stop"\|"pause"` in `config.py` (default `stop`, an unknown word refused with the key named, printed by `vocalize settings`); in `"pause"` mode `vocalize stop` pauses a live read and, when nothing was playing and a record is present, resumes it — but the resume branch first checks `dictate._read_session()` and refuses silently (prints nothing) while a dictation is live, so the stop chord reached for mid-recording never wakes a stale paused read into the open microphone instead of ending the take; plain `stop` keeps today's meaning exactly and still records nothing | vocalize | T-106 | `tests/test_config.py::test_stop_hotkey_rejects_an_unknown_word`, `tests/test_cli.py::test_stop_hotkey_pause_pauses_then_resumes`, `::test_stop_hotkey_pause_never_resumes_while_a_dictation_is_live`, `::test_settings_prints_stop_hotkey` and `tests/test_cli.py::test_plain_stop_records_nothing_and_never_resumes` green |
| T-108 | `_RESUME_REWIND = 1.0` applied inside `interrupted.slice_from` and clamped at zero, so a continuation overlaps the last word; plus the three named failure modes — a dictation while a read is paused never offers or destroys the record, a resume whose provider is installed but has no usable key (no key, no model) reports the failure and leaves the record in place, two resumes on one record leave it uncorrupted. `load()`'s existing deletion of a record naming a provider this build does not know at all is a separate, correct, unchanged guard — not what this test covers | vocalize | T-107 | `tests/test_dictate.py::test_resume_rewinds_one_second_before_the_pause_point`, `::test_dictation_while_paused_never_offers_the_paused_read`, `::test_resume_with_an_installed_but_unusable_provider_reports_and_keeps_the_record` and `tests/test_cli.py::test_two_resumes_do_not_corrupt_the_record` green; `grep -q '_RESUME_REWIND' vocalize/interrupted.py` exits 0 |
| T-109 | Docs: `docs/dictation.md` pause and resume section naming the hour and the plaintext `interrupted.txt`, and `[app] stop_hotkey` as the only zero-re-grant hotkey pause; README command list; CHANGELOG 0.13.1 | vocalize | T-108 | `grep -q 'vocalize pause' README.md`, `grep -q 'vocalize pause' docs/dictation.md` and `grep -q 'vocalize pause' CHANGELOG.md` each exit 0 (one grep per file, not one grep across all three); docs-match-CLI exits 0 with `pause` in the 0.13.1 command list |

## Role and isolation

- **Role:** Sonnet — the mechanism already ships and is already tested; this run adds one verb, one config key, one constant and a named failure-mode suite. Opus reviews the `stop` routing branch and the `stop_hotkey` validator before commit, because it changes what a shipped command does on one setting.
- **Isolation:** branch `hold-to-talk`, off `main`, after run 11 (`run-11-cue-hold-paste`); works in `vocalize/cli.py` (the `pause` command, the `stop` routing, `settings`), `vocalize/config.py` (`[app] stop_hotkey`), `vocalize/interrupted.py` (`wait_for_record` and `_RESUME_GRACE` move here from `dictate.py`, plus `_RESUME_REWIND`) and `vocalize/dictate.py` (keeps one call site into `interrupted.wait_for_record`, replacing the 24-line `_wait_for_record` it deletes); no circular import results — `interrupted.py` already imports `audio` and `audio.py` does not import `interrupted`, so `audio.stop_found_no_player` stays reachable; does **not** touch `vocalize/audio.py` — `stop_playback(remember=True)` is the whole mechanism and needs no edit — nor `vocalize/menubar/VocalizeApp.swift`, where any edit is a re-grant and must be refused.
- **Workload:** 4 source files (`vocalize/cli.py`, `vocalize/config.py`, `vocalize/interrupted.py`, `vocalize/dictate.py`) + 3 test files (`tests/test_cli.py`, `tests/test_dictate.py`, `tests/test_config.py`) + 3 docs.

## Entry criteria

- on branch `hold-to-talk` (not `main`)
- run 11 (`run-11-cue-hold-paste`) validated: its `report.md` contains `validate-exit: PASS`
- run 11's key artifact present: `dictate --start`/`--stop` in `vocalize/cli.py` (Phase 11 T-102, unconditional — present on both of T-101's cue branches, unlike `_trim_cue`, which only exists if T-100's spike picked the trim branch)
- DEC-036 Decided (how a read pauses, and how it is reached)
- `vocalize/menubar/` matches `main` (nothing to re-grant)
- suite green at entry
- ruff clean at entry

## Exit criteria

Checked by [validate-exit.sh](./validate-exit.sh), run from anywhere (it changes to the repository root). Every line is a command's exit status; a pre-build run must show the artifact checks failing.

- `vocalize pause` exists as a command
- pause saves the record a dictation would have saved, and reports honestly when nothing was playing
- the record wait lives in one place and dictation uses it
- `[app] stop_hotkey` validates, prints from `vocalize settings`, and in "pause" mode the stop chord pauses then resumes — but never resumes while a dictation is live
- plain `vocalize stop` is unchanged: it records nothing and never speaks
- a resumed read overlaps the last second instead of starting mid-syllable
- the three failure modes are pinned: a dictation never takes a paused read, an installed-but-unusable provider keeps the record, two resumes do not corrupt it
- Swift untouched: no re-grant slipped in, in either `vocalize/menubar/` or `vocalize/recorder/`
- docs and CHANGELOG name the pause verb
- full suite green
- ruff clean
- work committed

## Not machine-checkable

- Manual check 15 (verification.md): a real Kokoro read paused and continued mid-sentence, then the same on a cloud read to exercise the gap between two streamed chunks. Silence within a second, and the continuation starts about a word early in the same voice and speed. Owner present.
- Manual check 16: the hotkey leg. With `[app] stop_hotkey = "pause"` the existing control-option-command-X pauses a `/speak` read and the next press continues it, with no rebuild and no permission prompt. Then pause, dictate with the hotkey, and confirm no "Continue the read?" dialog appears and `vocalize resume` still works. Also press the stop chord mid-recording after a pause: it must end the recording, never resume the paused read into the open microphone.

## Handoff

On exit, the executor writes `report.md` in this directory: one line per task (`T-106`–`T-109`: done | partial | skipped — reason), the security-gate result (the plaintext `interrupted.txt` write and the 0600/`O_NOFOLLOW` marker discipline named in DEC-036, listed with their test ids), anything deferred, and the final line `validate-exit: PASS` copied from a real run of the script. Run `run-12-release-0-13-1` reads that report as its entry criterion.
