# Verification: local-first defaults, the menu-bar app, and recorded notes

All commands run from the repository root with the project's own tooling. Any failing command is a red exit, never a judgement call. Gate on exit codes directly (`cmd > log; RC=$?`), never through a pipe into `tail`.

## Commands

| Purpose | Command |
|---|---|
| Unit tests | `.venv/bin/python -m pytest tests/ -q` |
| Lint | `.venv/bin/python -m ruff check vocalize hooks tests` |
| Swift parse (recorder, app) | `xcrun swiftc -parse vocalize/recorder/VocalizeRecorder.swift vocalize/menubar/VocalizeApp.swift` |
| Plists | `plutil -lint vocalize/menubar/Info.plist.in hooks/quick_actions/*/Contents/Info.plist` |
| Build artifacts | `.venv/bin/python -m build` |
| Clean-venv acceptance | `python3 -m venv /tmp/v && /tmp/v/bin/pip install -q --no-cache-dir dist/vocalize_cli-*.whl && /tmp/v/bin/pip list \| grep -iE "pywhispercpp\|onnxruntime\|mlx\|sherpa\|numpy\|torch\|boto3"; test $? -eq 1` |
| Docs match the CLI | `.venv/bin/python -c 'import subprocess; [subprocess.run([".venv/bin/vocalize", *c.split(), "--help"], check=True, capture_output=True) for c in ("listen", "dictate", "resume", "pause", "status", "doctor", "notes", "app install", "app status", "integrate claude", "local install", "auth login")]'` (trim to the commands that exist at each release) |
| Review closed | `test -f <review file> && ! grep -iE '^\| *(critical\|high) *\|.*\| *open *\|' <review file>` |
| Digests | PyPI JSON `digests.sha256` for each file equals `shasum -a 256 dist/*` |

## Phase 1 exit (defaults)

| Criterion | How it is proven | Passing when |
|---|---|---|
| Default chain flipped and noted | `pytest tests/test_config.py tests/test_chain.py tests/test_cli.py -q -k "default_chain or fallback_note"` | exit 0; the note appears once and only when Kokoro was the skipped primary |
| Fresh machine still speaks | `HOME=$(mktemp -d) .venv/bin/vocalize speak "hello" 2>err.log; grep -c "Kokoro is not installed" err.log` | audio via `say`; grep prints 1 |
| Docs lead with local | `awk '/^## Providers/{exit} /ElevenLabs/{f=1} END{exit f}' README.md` | exit 0 (no ElevenLabs mention before the Providers heading) |
| Suite intact | unit tests + lint | exit 0 |

## Phase 2 exit (decoding)

| Criterion | How it is proven | Passing when |
|---|---|---|
| Beam search on, with an escape hatch | `pytest tests/test_whisper_worker.py tests/test_config.py -q -k beam` | exit 0 (stub Model receives the strategy kwargs; `beam_size=1` is greedy; the `[stt]` key validates 1–8) |
| Beam cost measured | `grep -A6 '^## Beam' docs/plans/2026-09-app-roadmap/spike-notes.md \| grep -cE '^(cold\|warm) beam [15]:'` | prints 4 |
| q8_0 row pinned | `pytest tests/test_whisper_manifest.py -q` and `grep -c q8_0 docs/dictation.md` | exit 0; grep ≥ 1 |
| CoreML measured | `grep -A10 '^## CoreML' docs/plans/2026-09-app-roadmap/spike-notes.md \| grep -cE '^(cold\|warm\|rss)'` | prints 8 and a `verdict:` line exists |

## Phase 3 exit (llm.py and enums)

| Criterion | How it is proven | Passing when |
|---|---|---|
| Backends and boundaries | `pytest tests/test_llm.py -q` | exit 0 (stdin-only transcript; `--disallowedTools *`, `--strict-mcp-config` and the user-scope exclusion flag; temp cwd; `ANTHROPIC_*` absent from the claude env; egress exactly once and only before a real send; 401 body never printed; per-feature limits; an unset anthropic budget is 2,000,000) |
| Cleanup moved intact | `pytest tests/test_llm.py -q -k cleanup` | exit 0 with the nine original assertions |
| Verbatim keyword | `pytest tests/test_llm.py tests/test_dictate.py -q -k verbatim` | exit 0 |
| Enums and coercion | `pytest tests/test_config.py -q -k "stt or notes"` | exit 0 (`true`→`claude-cli`; `local` refused at run time with the 0.14.0 message) |
| `[notes]` survives a rewrite | `pytest tests/test_wizard.py -q -k notes` | exit 0 |
| Hook hardened | `pytest tests/test_speak_options.py -q -k "strict or cwd"` | exit 0 |
| Issue #5 | `pytest tests/test_config.py tests/test_portal.py -q -k "provider_settings or voice_type"` | exit 0 (`voice = 12345` is a `ConfigError` and a per-provider portal error) |

## Phase 4 exit (keychain)

| Criterion | How it is proven | Passing when |
|---|---|---|
| Check recorded | `grep -A3 '^### DEC-035' docs/plans/2026-09-app-roadmap/decisions.md \| grep -q 'Status.*Decided'` | exit 0 |
| Backend (branch A) | `pytest tests/test_auth.py -q -k security` | exit 0 (key on stdin, never argv; delete read-back; stamp) |
| Docs (branch B) | `grep -q "Always Allow" docs/provider-credentials.md` | exit 0 |
| Real read on this Mac (branch A) | `.venv/bin/vocalize auth status` from the pipx binary and from the repo venv | both report the same source with no keychain prompt (manual 2) |

## Phase 5 exit (keys tab)

| Criterion | How it is proven | Passing when |
|---|---|---|
| Slot reachable everywhere | `pytest tests/test_cli.py tests/test_portal.py tests/test_auth.py -q -k anthropic` | exit 0 (login stores under `anthropic-api-key`; state JSON carries `anthropic`; usage row) |
| Routes safe | `pytest tests/test_portal.py -q -k "remove or test_without"` | exit 0 (no key in any response; test stores nothing; a control-character key is refused before any request; unknown slot 404) |
| Page discipline | `grep -c "<script>" vocalize/assets/portal.html; test $? -eq 1` and `! grep -E "https?://" vocalize/assets/portal.html vocalize/assets/portal.js` | no inline script, no external URL |

## Phase 6 exit (release 0.12.0)

| Criterion | How it is proven | Passing when |
|---|---|---|
| Review closed | review-closed command on `docs/plans/2026-09-app-roadmap/review-0.12.0.md` | exit 0 |
| Docs match the CLI | docs-match command | exit 0 |
| Package | build + clean-venv acceptance; digests | equal |

## Phase 7 exit (spike and builder)

| Criterion | How it is proven | Passing when |
|---|---|---|
| Hotkey backend decided | `grep -A3 '^### DEC-033' docs/plans/2026-09-app-roadmap/decisions.md \| grep -q 'Status.*Decided'` | exit 0 |
| Recorder build unchanged | `pytest tests/test_recorder_build.py tests/test_app_build.py -q -k golden` | exit 0 (argv byte-identical) |
| `[app]` grammar | `pytest tests/test_config.py -q -k app` | exit 0 |

## Phase 8a exit (app, Swift)

| Criterion | How it is proven | Passing when |
|---|---|---|
| Swift parses, plist lints | Swift parse and plist commands | exit 0 |
| No text can reach a notification | `! grep -n "NSPasteboard.string\|readObjects" vocalize/menubar/VocalizeApp.swift` | exit 0 |
| Override and nonce checks exist | `grep -c "func checkedBinary\|func pasteIfNonceMatches" vocalize/menubar/VocalizeApp.swift` | prints 2 |
| Unknown `app.*` keys are ignored, not treated as a chord | inspect the settings parser: only the four chord names (`dictate`, `dictate_mode`, `speak`, `stop`) are registered, and an unmatched `app.*` line falls through | true (so a later `app.*` key such as `stop_hotkey` needs no rebuild) |
| Source committed | `git diff --quiet HEAD -- vocalize/menubar/` | exit 0 |

## Phase 8b exit (app, Python)

| Criterion | How it is proven | Passing when |
|---|---|---|
| Install and lifecycle | `pytest tests/test_app_cli.py tests/test_app_build.py -q` | exit 0 (plist keys; bootout before bootstrap; tccutil only on rebuild; `launchctl list` parse failure reads `unknown`; status shapes; uninstall scope; bundle path under Application Support) |
| Session state and nonce | `pytest tests/test_dictate.py -q -k "state or nonce"` | exit 0 |
| Rows and state key | `pytest tests/test_readiness.py tests/test_portal.py -q -k app` | exit 0 |
| Real build on this Mac | `.venv/bin/vocalize app install --yes && .venv/bin/vocalize app status --json \| python3 -c "import json,sys; d=json.load(sys.stdin); assert d['bundle']=='current' and d['agent']=='loaded'"` | exit 0 |
| Review closed | review-closed command on `review-0.13.0.md` | exit 0 |

## Phase 9 exit (doctor, integrate, setup)

| Criterion | How it is proven | Passing when |
|---|---|---|
| Doctor | `.venv/bin/vocalize doctor --json \| python3 -c "import json,sys; rows=json.load(sys.stdin); names={r['name'] for r in rows}; assert {'cli path','uv','swiftc','claude'} <= names"` | exit 0 |
| Integrate on a scratch home | `HOME=$(mktemp -d) .venv/bin/vocalize integrate claude --yes && test -f "$HOME/.claude/skills/speak/SKILL.md" && test $(ls "$HOME/Library/Services" \| grep -c workflow) -eq 4` | exit 0 |
| Baked path is the symlink | `grep -l "/opt/homebrew/bin/claude" "$HOME/Library/Services/Speak with Vocalize.workflow/Contents/document.wflow"` on a Homebrew machine | prints the file (no Caskroom path) |
| Setup tab | `pytest tests/test_portal.py -q -k setup` + page discipline | exit 0 |

## Phase 10 exit (release 0.13.0)

Same three rows as Phase 6 against `review-0.13.0.md`.

## Phase 11 exit (cue, hold, paste)

| Criterion | How it is proven | Passing when |
|---|---|---|
| Swift untouched | `git diff --quiet main -- vocalize/menubar/` | exit 0 |
| Cue spike recorded | `grep -A8 '^## Cue' docs/plans/2026-09-app-roadmap/spike-notes.md \| grep -q 'branch:'` | exit 0 |
| No cue word reaches the worker | `pytest tests/test_dictate.py -q -k "cue or trim"` | exit 0 (frame count asserted for three cue modes and both branches) |
| Hold-to-talk | `pytest tests/test_dictate.py tests/test_cli.py -q -k "start or stop_hold"` | exit 0 |
| Paste marker carries the nonce | `pytest tests/test_dictate.py -q -k copied` | exit 0 (marker holds the session's nonce; none on nothing-heard) |

## Phase 11b exit (playback pause)

| Criterion | How it is proven | Passing when |
|---|---|---|
| The verb exists | `.venv/bin/vocalize pause --help` | exit 0 (only when the command is registered) |
| Pause saves what a dictation would | `pytest tests/test_cli.py::test_pause_saves_the_record_like_a_dictation tests/test_cli.py::test_pause_with_nothing_playing_reports_it tests/test_cli.py::test_pause_in_the_chunk_gap_records_the_queued_piece -q` | exit 0 (a version-2 record with the playing piece, the unspoken text and the four settings; no traceback with nothing playing; the PID 0 marker path covered) |
| One record wait, not two | `grep -qE '^def wait_for_record\(' vocalize/interrupted.py && grep -q 'interrupted.wait_for_record' vocalize/dictate.py` | exit 0 |
| The chord can pause, opt-in | `pytest tests/test_config.py::test_stop_hotkey_rejects_an_unknown_word tests/test_cli.py::test_stop_hotkey_pause_pauses_then_resumes tests/test_cli.py::test_stop_hotkey_pause_never_resumes_while_a_dictation_is_live tests/test_cli.py::test_settings_prints_stop_hotkey -q` | exit 0 |
| Plain `stop` is unchanged | `pytest tests/test_cli.py::test_plain_stop_records_nothing_and_never_resumes -q` | exit 0 (with the default `stop_hotkey`, stop writes no record and never speaks) |
| Resume overlaps the last word | `grep -q '_RESUME_REWIND' vocalize/interrupted.py && pytest tests/test_dictate.py::test_resume_rewinds_one_second_before_the_pause_point -q` | exit 0 (the slice equals offset minus one second at 1.0 s, and the whole file at 0.4 s) |
| Failure modes pinned | `pytest tests/test_dictate.py::test_dictation_while_paused_never_offers_the_paused_read tests/test_dictate.py::test_resume_with_an_installed_but_unusable_provider_reports_and_keeps_the_record tests/test_cli.py::test_two_resumes_do_not_corrupt_the_record -q` | exit 0 |
| Swift untouched | `git diff --quiet main -- vocalize/menubar/ && git diff --quiet main -- vocalize/recorder/` | exit 0 |
| Docs name the verb | `grep -q 'vocalize pause' README.md && grep -q 'vocalize pause' docs/dictation.md && grep -q 'vocalize pause' CHANGELOG.md` | exit 0 |
| Suite intact | unit tests + lint | exit 0 |

## Phase 12 exit (release 0.13.1)

Review section closed in `review-0.13.0.md`; docs match; digests equal.

## Phase 13 exit (spikes)

| Criterion | How it is proven | Passing when |
|---|---|---|
| Parakeet decided | `grep -A3 '^### DEC-034' docs/plans/2026-09-app-roadmap/decisions.md \| grep -q 'Status.*Decided'` | exit 0 |
| mlx-lm answered | `grep -A12 '^## LLM' docs/plans/2026-09-app-roadmap/spike-notes.md \| grep -cE '^(signatures\|offline\|model_type\|template\|shards\|cold\|warm):'` | prints 7 |
| Parakeet engine (go only) | `pytest tests/ -q -k parakeet` | exit 0 |

## Phase 14 exit (local model)

| Criterion | How it is proven | Passing when |
|---|---|---|
| Manifest hardened | `pytest tests/test_llm_manifest.py -q` | exit 0 |
| Worker discipline | `pytest tests/test_llm_worker.py -q` | exit 0 (AST test; `split_special_tokens`; truncation reply; selftest and runtime argvs differ only by `--offline`) |
| Install lifecycle | `pytest tests/test_cli.py tests/test_local_install.py tests/test_readiness.py -q -k llm` | exit 0 |
| Local backend | `pytest tests/test_llm.py -q -k local` | exit 0 (`--offline`; stdin request; offline env) |
| No runtime leaked | clean-venv acceptance | grep finds nothing |
| Real install on this Mac | `.venv/bin/vocalize local install --llm --yes && .venv/bin/vocalize local status \| grep -q "LLM: ready"` | exit 0 |

## Phase 15 exit (notes)

| Criterion | How it is proven | Passing when |
|---|---|---|
| Pipeline and negatives | `pytest tests/test_notes.py -q` | exit 0 (self-ingestion guard; done-scan on hash and path with skips printed; every segment sanitized; atomic write; `trust` key; template symlink and size cap; lock; sweep; utf-8; `-x.m4a`; folder never re-moded; egress once; caps) |
| Segments contract | `pytest tests/test_whisper_worker.py -q -k segments` (and `-k parakeet` if shipped) | exit 0 |
| Threads clean | `pytest tests/test_notes.py -q -k threads` | exit 0 |
| Real audio | manual check 13 | note written; constants match the verdict |

## Phase 15b exit (recording pause)

| Criterion | How it is proven | Passing when |
|---|---|---|
| The flags exist | `.venv/bin/vocalize dictate --help \| grep -q -- '--pause' && .venv/bin/vocalize dictate --help \| grep -q -- '--resume'` | exit 0 |
| Pause and resume mechanics | `pytest tests/test_dictate.py::test_pause_finalises_a_segment_and_leaves_the_session_claimed tests/test_dictate.py::test_resume_launches_a_second_recorder_with_the_remaining_budget tests/test_dictate.py::test_resume_max_never_falls_below_one_second -q` | exit 0 (the segment is readable by `wave`, the marker is 0600, the fake recorder's argv carries the remaining seconds and never zero) |
| The frozen app learns nothing new | `pytest tests/test_dictate.py::test_session_state_stays_recording_while_paused -q` | exit 0 (the session JSON never contains "paused") |
| Both budgets bound the take | `grep -q 'max_take_seconds' vocalize/config.py && pytest tests/test_dictate.py::test_resume_refuses_a_twenty_first_segment tests/test_dictate.py::test_resume_refuses_past_the_take_budget tests/test_config.py::test_max_take_seconds_bounds -q` | exit 0 |
| Backstop and marker are safe against a stale or corrupt take | `pytest tests/test_dictate.py::test_wait_for_exit_backstop_uses_the_segment_start_not_the_take_start tests/test_dictate.py::test_resume_treats_a_corrupt_paused_marker_as_no_pause -q` | exit 0 (a segment older than `max_seconds` is never SIGTERMed mid-write; a NaN, negative or unparsable marker is treated as no pause) |
| A self-stopped segment tells the user | `pytest tests/test_dictate.py::test_segment_self_stop_at_max_notifies_before_the_mic_closes -q` | exit 0 (stop cue plus a notification, never a silently closed mic) |
| Transcription budget scales with the take | `pytest tests/test_dictate.py::test_transcribe_timeout_scales_with_take_length -q` | exit 0 |
| One WAV reaches the worker | `pytest tests/test_dictate.py::test_join_segments_frames_and_silence tests/test_dictate.py::test_joined_wav_keeps_16k_mono_16bit tests/test_dictate.py::test_cue_trimmed_per_segment_never_reaches_the_worker -q` | exit 0 (frames equal the sum of the segments plus 0.25 s × 16000 × (n−1); 16 kHz mono 16-bit; no cue frames from any segment) |
| A forgotten pause loses nothing | `pytest tests/test_dictate.py::test_toggle_while_paused_stops_and_transcribes tests/test_dictate.py::test_cancel_while_paused_removes_every_segment tests/test_dictate.py::test_paused_workdir_younger_than_24h_is_not_swept -q` | exit 0 (never the dead-recorder failure path, never the 2 s cancel window; a workdir under 24 h with a marker keeps its segments) |
| Stop precedence | `pytest tests/test_cli.py::test_stop_pauses_a_live_recording_before_playback tests/test_cli.py::test_stop_resumes_a_paused_recording tests/test_cli.py::test_unknown_session_state_falls_through_to_playback -q` | exit 0 |
| Neither Swift source touched | `git diff --quiet main -- vocalize/recorder/ && git diff --quiet main -- vocalize/menubar/` | exit 0 |
| Docs carry the flag and the budget | `grep -q 'dictate --pause' docs/dictation.md && grep -q 'max_take_seconds' docs/dictation.md && grep -q 'dictate --pause' CHANGELOG.md` | exit 0 |
| Real audio | manual checks 17 and 18 | both halves in the transcript; six seam numbers in `spike-notes.md` § Pause |
| Suite intact | unit tests + lint | exit 0 |

## Phase 16 exit (release 0.14.0)

Same three rows as Phase 6 against `review-0.14.0.md`.

## Manual checks

Only what cannot be automated; performed with the owner present on the reference Mac (M4, 16 GB, macOS 26.5.1).

1. **Beam search (0.12.0).** `vocalize listen --wav` on the owner's jargon clip: "to get" no longer merges; count misses against the 0.10.2 transcript.
2. **Keychain (0.12.0, branch A).** Store a key from the terminal; open Claude Code desktop and run `vocalize auth status` from its shell: same source, no prompt.
3. **Keys tab (0.12.0).** Add, test, remove an Anthropic key in the portal; the key never appears in the page or the terminal.
4. **Hotkey spike (0.13.0, T-60).** As specified in the task: three frontmost apps, hold for 3 s.
5. **First install (0.13.0).** `vocalize app install`: the menu-bar icon appears; press control-option-command-D in TextEdit, speak, press again; the icon turns red then back; the transcript pastes with Command-V. With a stopwatch: press-to-red-icon and stop-to-clipboard-ready for a 10 s take, three times each; record in `spike-notes.md` § Latency (the stop press should land under 4 s with `small.en`).
6. **Speak-selection (0.13.0).** Select text in Safari, press control-option-command-S: the Accessibility prompt appears once, then the selection is read; press control-option-command-X: silence within a second.
7. **Relaunch (0.13.0).** `kill -9` the app: it is back within 10 s; menu Quit: it stays quit; `vocalize app restart` brings it back; log out and in: it is running.
8. **Upgrade (0.13.0).** `uv tool upgrade vocalize-cli` with the app running; the next press works without a rebuild.
9. **Cue spike (0.13.1, T-100).** Built-in and Bluetooth inputs, six numbers.
10. **Cue (0.13.1).** With `cues = "words"`, say the first word immediately after "Start."; the transcript begins with it; no "Start" in the transcript. Repeat on the Bluetooth input.
11. **Hold and paste (0.13.1).** `dictate_mode = "hold"`: hold the chord, speak, release; text lands. With `paste = true`, switch windows during transcription: "Copied, not pasted (window changed)."
12. **Parakeet spike (0.14.0, T-120).** The owner reads the jargon clip; numbers as specified.
13. **Real audio (0.14.0, T-146).** A 60-minute recording through `vocalize notes`; note the wall time, memory, any looping; one note written; the summary reads sensibly for the template. During the summary, start a Kokoro read and record the peak combined RSS with Claude Code and a browser open.
14. **Cloud paths (0.14.0).** `--summarizer claude-cli` and `--summarizer anthropic` on a 5-minute file: one egress line each; `left_machine: true` in both notes; the transcript-only note when the model is uninstalled.
15. **Playback pause (0.13.1, T-106, T-108).** Start a long Kokoro read. Run `vocalize pause` mid-sentence from a second terminal: silence within a second. `vocalize resume`: the read continues about a word before where it stopped, in the same voice and speed. Repeat with an ElevenLabs read, pausing in the gap between two spoken pieces, and confirm the continuation starts at the piece that was never heard. Then pause, dictate with the hotkey, and confirm no "Continue the read?" dialog appears and `vocalize resume` still works afterwards.
16. **Pause on the chord (0.13.1, T-107, only if DEC-036 ships the hotkey key).** Set `[app] stop_hotkey = "pause"`. Press control-option-command-X during a `/speak` read: it pauses. Press again: it continues. Confirm no rebuild and no permission prompt. Pause a read, start a dictation with the hotkey, then press the stop chord mid-recording: confirm it ends the recording and does not resume the paused read into the open microphone. Set the key back to `"stop"` and confirm the same chord silences a read and leaves nothing to resume.
17. **Recording pause (0.14.0, T-147, T-148).** Start a dictation, speak, `vocalize dictate --pause`, wait 30 s, resume, speak, stop. The transcript carries both halves in order with no repeated or dropped word at the seam. With a stopwatch, measure resume-command to Tink three times on the built-in microphone and three times on a Bluetooth input; record the six numbers in `spike-notes.md` § Pause. If the Bluetooth gap is over two seconds, say so in `report.md` — it decides whether pause is usable for memos.
18. **A long take (0.14.0, T-147, T-149).** First raise `[stt] max_seconds` to 600 — at the default 120 s, three pauses is only four segments (eight minutes), never ten; the check is unreachable at the default. A take over ten minutes across three pauses: it transcribes as one continuous transcript, `[stt] max_take_seconds` stops it at the cap, and the temporary directory is gone afterwards (`ls` under `$TMPDIR`). Note what the menu-bar icon does while paused — it still says recording by design, so record whether that misled you. Also let one segment self-stop at `--max` while still talking (no explicit pause): confirm the stop cue plays and a notification says the microphone closed, rather than the mic staying shut in silence.
