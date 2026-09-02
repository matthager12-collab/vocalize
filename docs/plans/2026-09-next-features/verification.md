# Verification: local dictation, readiness status, and the config portal

All commands run from the repository root with the project's own tooling. Any failing command is a red exit, never a judgement call.

## Commands

| Purpose | Command |
|---|---|
| Unit tests | `.venv/bin/python -m pytest tests/ -q` |
| Lint | `.venv/bin/python -m ruff check vocalize hooks tests` |
| Bundle plists | `plutil -lint hooks/quick_actions/*/Contents/Info.plist hooks/quick_actions/*/Contents/Resources/document.wflow` |
| Swift parse | `xcrun swiftc -parse vocalize/recorder/VocalizeRecorder.swift` |
| Build artifacts | `.venv/bin/python -m build` |
| Clean-venv acceptance | `python3 -m venv /tmp/v && /tmp/v/bin/pip install -q --no-cache-dir dist/vocalize_cli-*.whl && /tmp/v/bin/pip list \| grep -iE "pywhispercpp\|onnxruntime\|numpy\|torch\|boto3"; test $? -eq 1` |

Gate on exit codes directly (`cmd > log; RC=$?`), never through a pipe into `tail`.

## Phase 0 exit (spike)

| Criterion | How it is proven | Passing when |
|---|---|---|
| Model hashes come from real files | `test "$(grep -oE '[0-9a-f]{64}' docs/plans/2026-09-next-features/spike-2026-09-01.md \| sort -u \| wc -l)" -eq 3` | exit 0 (and the report's sizes came from completed downloads, stated in the report) |
| Engine decision has evidence | `grep -A3 '^### DEC-002' docs/plans/2026-09-next-features/decisions.md \| grep -q 'Status.*Decided'` | exit 0 |
| Mic-from-Quick-Action answered | `grep -qi 'automator' docs/plans/2026-09-next-features/spike-2026-09-01.md && grep -qi 'RMS' docs/plans/2026-09-next-features/spike-2026-09-01.md` | exit 0; the report names the context that captured non-silent audio and the `automator` ≠ Services runner caveat |

## Phase 1 exit (status)

| Criterion | How it is proven | Passing when |
|---|---|---|
| Readiness never hangs | `pytest tests/test_readiness.py -q` (includes a probe blocked on an Event that is never set, and a repeated-call thread count) | exit 0; the timeout test asserts wall time < timeout + 0.5 s and that a second call starts no new thread |
| Process exits with a probe still blocked | `timeout 10 .venv/bin/python -c "import threading, vocalize.readiness as r; r._PROBES['x'] = lambda: threading.Event().wait(); r.readiness({'chain': ['say']}, timeout=0.2)"` (`_PROBES` is the name→callable registry from design § Readiness aggregation; every registered probe runs regardless of the chain) | exit 0 within 10 s |
| `status` reports and exits correctly | `.venv/bin/vocalize status --json \| python3 -c "import json,sys; rows=json.load(sys.stdin); assert rows and all({'name','state','detail','action'} <= set(r) for r in rows)"` | exit 0 |
| Suite intact | `pytest tests/ -q` and ruff | exit 0 |

## Phase 2 exit (STT runtime)

| Criterion | How it is proven | Passing when |
|---|---|---|
| Manifest pinned | `pytest tests/test_whisper_manifest.py -q` | exit 0 |
| Worker imports nothing at module level | `pytest tests/test_whisper_worker.py -q` (AST test) | exit 0 |
| Installer generalized without regressing Kokoro | `pytest tests/test_local_install.py tests/test_kokoro_provider.py -q` | exit 0 |
| No runtime dependency leaked | Clean-venv acceptance command | grep finds nothing (exit 1 → wrapped `test` passes) |
| Real install works on the reference Mac | `.venv/bin/vocalize local install --stt --yes && .venv/bin/vocalize local status \| grep -q "STT: ready"` | exit 0 |

## Phase 3 exit (recorder)

| Criterion | How it is proven | Passing when |
|---|---|---|
| Source parses | Swift parse command | exit 0 |
| Build-at-install logic | `pytest tests/test_recorder_build.py -q` (fake compiler) | exit 0 |
| Bundle exists after install | `test -x "$HOME/Library/Caches/vocalize/bin/Vocalize Recorder.app/Contents/MacOS/recorder" \|\| test -x "$HOME/.cache/vocalize/bin/Vocalize Recorder.app/Contents/MacOS/recorder"` | exit 0 |
| Authorization reported | `.venv/bin/vocalize listen --check; c=$?; test $c -eq 0 -o $c -eq 1 -o $c -eq 2 -o $c -eq 3 -o $c -eq 5` | exits 0 (authorized), 2/3/5 with a message naming the fix, or 1 when vocalize's own install is incomplete (recorder not built, no model, no report back) — never a traceback, never any other code (DEC-010) |
| The grant measured is the bundle's | `.venv/bin/vocalize listen --check` while a terminal with a microphone grant runs it | the word reported is the bundle's, not the terminal's — the command launches `Vocalize Recorder.app` through `open -W -n -a` and reads its status file (DEC-010) |
| Compiler diagnostics | `pytest tests/test_recorder_build.py -q -k "swiftc or license"` | exit 0 (missing compiler and unaccepted license each name their fix) |

## Phase 4 exit (dictation)

| Criterion | How it is proven | Passing when |
|---|---|---|
| State machine and privacy invariants | `pytest tests/test_dictate.py -q` | exit 0 (includes: transcript never in argv/notification/file; tmpdir and session file removed on every path; cancel; refuse-while-transcribing; silence guard; max-seconds kill only after the process-name check; dead or recycled `rec.pid` never signalled; two concurrent starts → one recorder) |
| `[stt]` validation | `pytest tests/test_config.py -q -k stt` | exit 0 (every `[stt]` test carries `stt` in its name so the filter selects it, `max_seconds` and `input_device` included) |
| Cleanup pass denies tools and keeps text on stdin | `pytest tests/test_dictate.py -q -k cleanup` | exit 0 (wildcard deny in argv; stdin only; fallback on non-zero exit, timeout and empty output; injection-shaped transcript passed as data) |
| Quick Action bundle shape | plists lint + `pytest tests/test_install_quick_action.py -q` | exit 0 |
| Transcribe an existing WAV end to end (no mic) | `.venv/bin/vocalize listen --wav <scratch>/clip.wav \| grep -qi kokoro` | exit 0 |
| Player-side interrupt record | `pytest tests/test_audio.py tests/test_chain.py tests/test_cli.py -q -k interrupt` | exit 0 (record only when the marker names this player and is fresh; plain stop → no record; streamed site saves the current piece, not the joined audio; a stop during a 15 s synthesis still records; non-streamed site; remaining text; 0600) |
| Interrupted-read resume | `pytest tests/test_dictate.py -q -k resume` | exit 0 (slice length = duration − offset; continuation with the remaining text and forced provider; record deleted on decline/`--forget`/staleness; nothing to resume → exit 0) |
| Uninstall | `pytest tests/test_local_install.py -q -k uninstall` | exit 0 |
| `settings` still parses for the hooks | `pytest tests/test_speak_options.py -q` | exit 0 |

## Phase 5 exit (release 0.10.0)

| Criterion | How it is proven | Passing when |
|---|---|---|
| Review closed | `test -f docs/plans/2026-09-next-features/review-0.10.0.md && ! grep -iE '^\| *(critical\|high) *\|.*\| *open *\|' docs/plans/2026-09-next-features/review-0.10.0.md` (T-51 writes the findings table there with a Status column) | exit 0. The `test -f` is not decoration: `grep` on a missing file exits 2, and a bare `!` turns that into a pass — so the gate proving the review closed used to pass hardest on the one state it exists to catch, a review nobody wrote |
| Docs match the CLI | `.venv/bin/python -c 'import subprocess; [subprocess.run([".venv/bin/vocalize", *c.split(), "--help"], check=True, capture_output=True) for c in ("listen", "dictate", "resume", "status", "local install", "local status", "local uninstall")]'` (the same line run-6's validate-exit.sh uses; the shell `for` loop this replaced silently failed under zsh, which does not word-split an unquoted `$c`, so `local install` arrived as one argument) | exit 0 |
| Package | build + clean-venv acceptance; PyPI JSON digests equal local `shasum -a 256 dist/*` | equal |

## Phase 6–7 exit (portal)

| Criterion | How it is proven | Passing when |
|---|---|---|
| Auth invariants | `pytest tests/test_portal.py -q -k "token or host or session or lockout"` | exit 0 (token in query refused per mutating route; `Host` mismatch refused on `/`, `/portal.js`, `/api/session` and every API route; `/` serves no secret; code single-use, expired after 60 s; five wrong codes end the server) |
| Headers | `pytest tests/test_portal.py -q -k headers` | every response has CSP `default-src 'self'; media-src 'self' blob:; frame-ancestors 'none'`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` |
| Writes are safe | `pytest tests/test_portal.py -q -k "write or cas or login"` | a changed file on disk is refused; a file created under an absent fingerprint is refused; invalid values return the CLI validators' messages; the login response never contains the key |
| Preview respects budgets | `pytest tests/test_portal.py -q -k preview` | a capped provider is refused with the CLI's message; a repeat is a cache hit; concurrent previews serialize |
| Review closed | `test -f docs/plans/2026-09-next-features/review-0.11.0.md && ! grep -iE '^\| *(critical\|high) *\|.*\| *open *\|' docs/plans/2026-09-next-features/review-0.11.0.md` | exit 0 (the `test -f` for the same reason as Phase 5) |
| Page discipline | `grep -c "<script>" vocalize/assets/portal.html; test $? -eq 1` and `! grep -E "https?://" vocalize/assets/portal.html vocalize/assets/portal.js` | no inline script, no external URL |
| Live | `.venv/bin/vocalize portal` opens; every tab loads with a hanging provider faked via config | manual check below |

## Manual checks

Only what cannot be automated; performed with the owner present on the reference Mac.

0. **Memory with a browser open.** Before Phase 5 and again before Phase 8, with Claude Code and a browser (a few tabs) running, repeat the spike's `vm_stat` swap measurement across a small.en transcription (and, before Phase 8, with the portal's own tab open and a Kokoro read in progress): zero swap-outs in steady state, or the default model moves to `base.en` and the report says so.
1. **Microphone permission.** Run `vocalize local install --stt`; a macOS prompt names "Vocalize Recorder"; click Allow; `vocalize listen --check` prints `authorized`.
2. **Hotkey path.** In TextEdit, press ⌃⌥⌘D, speak a sentence with two file paths and one flag, press ⌃⌥⌘D again; Tink and Glass are heard; ⌘V pastes the sentence; the two paths and the flag are correct.
3. **Cancel and refuse.** Press twice within 2 s: Pop, nothing on the clipboard. Press during transcription: Pop, transcription completes once.
4. **Playback interaction and resume.** Start a long `vocalize speak-file … --provider kokoro`, then press ⌃⌥⌘D: the read stops before the Tink; recording contains no vocalize audio; after the transcript lands, the "Continue the read?" dialog appears; Continue replays from close to where it stopped and carries on through the rest of the text. Repeat with a non-streamed provider (`--provider say`). Then `vocalize stop` on a plain read: no dialog on the next dictation.
4a. **Preview while reading (0.11.0).** With a CLI read playing, click a portal preview: both are audible (the accepted exception); `vocalize stop` stops only the CLI read.
4b. **Input device.** With Bluetooth earbuds paired but not worn and `[stt] input_device` set to the built-in microphone, `vocalize listen --check` names *the configured device* (not the system default) and reports on that one. The install selftest does **not** record: it transcribes a generated tone, so it cannot and never could refuse silence — the claim was removed from design.md § Input device rather than left as a gate nothing can pass. A silent input still shows up as "Nothing heard" on the first dictation.
4c. **Two reads, one stop.** Start a long read, then a second `vocalize speak-file` in another terminal (it blocks on the playback lock), then press ⌃⌥⌘D: the first read stops, the *second* never becomes audible, and the recording contains no vocalize audio (DEC-013).
5. **Real-voice accuracy.** The owner reads the spike's jargon paragraph; count misses; compare with the synthetic proxy in DEC-002. Decide whether `small.en` stays the default.
6. **Portal (0.11.0).** Open `vocalize portal`; change the chain order, save; `vocalize chain` in a terminal shows the new order; edit the file in the terminal while the page is open, then save from the page: the page reports the conflict. A preview plays under the shipped headers (the `media-src` directive is doing its job). In the Local tab, set the input device to the built-in microphone; `vocalize settings` shows it.
