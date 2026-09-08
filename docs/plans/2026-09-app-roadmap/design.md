# Design: local-first defaults, the menu-bar app, and recorded notes

Date 2026-09-06. Full-tier plan. Companions: [plan.md](./plan.md), [verification.md](./verification.md), [decisions.md](./decisions.md). Research: [../../research/2026-09-04-app-roadmap-analysis.md](../../research/2026-09-04-app-roadmap-analysis.md). The previous plan's contracts ([../2026-09-next-features/design.md](../2026-09-next-features/design.md): recorder, whisper worker, `[stt]`, readiness rows, portal routes and the `/api/state` payload) still hold. This document only adds to them.

## Context

vocalize 0.10.2 is on PyPI; 0.11.0 (the portal page, branch `portal-page`) ships before anything here starts. The seams this design builds on, with the files each derives from:

- **Provider chain** — `vocalize/chain.py::run` tries the chain in order, prints `_skip_message` on a failure and `Spoke via <name> (fallback).` when a non-primary provider speaks. `vocalize/config.py:92` holds `DEFAULT_CHAIN`. `auth.PROVIDER_NAMES` drives every print order and is only ever iterated.
- **Dictation** — `vocalize/dictate.py`: the session file claimed with `O_EXCL`, `_launch_recorder` through `/usr/bin/open`, the stop file, `_finish_take` → `transcribe` → `cleanup_transcript` (`claude -p`, tools denied, `--strict-mcp-config`, temp cwd) → `copy_to_clipboard`; fixed notification strings; `_sweep_stale_workdirs`; `worker_argv`; `sanitize`.
- **Local runtimes** — `vocalize/local/install.py`: manifest-driven download with size and sha256, `.verified` stamp; `build_recorder` (staging, `Info.plist`, ad-hoc `codesign` with entitlements, atomic swap, content-addressed stamp). `providers/kokoro.py::_Session` (resident JSON lines), `whisper_worker.py` (one-shot).
- **Credentials** — `vocalize/auth.py`: `_backend()` is the one seam over `keyring`; `PROVIDER_ENV_VARS`, `PROVIDER_USERNAMES`, `login`, `validate_key`, `delete_key`, `key_source`, `masked`.
- **Config writes** — `vocalize/wizard.py::_render_config_text` renders flat keys, then `[stt]` by name, then `[providers.*]`. `_TABLE_KEYS` is an exclusion list, not a render list: a new table must be rendered explicitly or the next rewrite drops it.
- **Portal** — `vocalize/portal.py` routes and the `/api/state` payload; the page under `vocalize/assets/`.
- **Readiness** — `vocalize/readiness.py`: `_PROBES` registry, `Row(name, state, detail, action)`.
- **Hooks** — `hooks/speak_options.py::_summarize` calls `claude -p` without `--strict-mcp-config` and inherits the caller's cwd (fixed in 0.12.0).
- **Budgets** — `vocalize/ledger.py` (`status`, `record`, `mark_exhausted`) and `config.budget_for`.
- **Hotkeys today** — two chords in `~/.hammerspoon/init.lua` and the "Dictate with Vocalize" Quick Action with a Services shortcut.

The constraint that shapes everything: **local first, opt-in downloads, nothing downloaded is executed, transcripts are data, one visible line whenever text leaves the machine, and the recorder's Swift source stays frozen** (its microphone grant is per build, DEC-010).

## Approach

Four releases, smallest first (DEC-032):

- **0.12.0 local-first** — the chain flip with its fallback note (DEC-022), beam search, the cloud opt-in enums and the egress line through one new module `vocalize/llm.py` (DEC-030), the `[notes]` table (parsed, rendered, not yet used), the keychain backend through `/usr/bin/security` (DEC-025), the Keys tab finished, the Anthropic key slot. Every task is a small diff on code that exists.
- **0.13.0 app** — the locally compiled menu-bar app (DEC-020, DEC-028) with toggle dictation, speak-the-selection under one Accessibility grant (DEC-021), stop, an indicator, a login item, `vocalize app install`, `vocalize doctor`, `vocalize integrate claude`, a portal Setup tab. The Swift source ships **complete**, including the hold-to-talk dispatch and the paste watcher, so 0.13.1 changes no Swift and costs no re-grant.
- **0.13.1 hold-to-talk and cue** — Python only: the cue trim (DEC-031, issue #2), `dictate --start/--stop`, the auto-paste marker.
- **0.14.0 notes** — the Parakeet spike as a gate (DEC-026), the local language model (manifest, worker, `local install --llm`), `vocalize notes` (DEC-029, DEC-023, DEC-024).

Spikes that gate nothing (Foundation Models, SpeechAnalyzer, Voice Memos transcript) are listed in plan.md § Phase 17 and are never on a release path.

## Structure

```mermaid
graph TD
  APP[Vocalize.app<br/>Swift menu bar, Carbon hotkeys<br/>one Accessibility grant] -->|spawn vocalize dictate / clip / stop| CLI[vocalize CLI]
  APP -->|watches| SES[~/.cache/vocalize/dictate.session<br/>state: starting/recording/transcribing]
  APP -->|writes| AST[app.status]
  LA[LaunchAgent plist<br/>KeepAlive] --> APP
  CLI --> D[dictate.py]
  D -->|open -a| R[Vocalize Recorder.app<br/>frozen]
  D -->|uv run| W[whisper_worker.py]
  D -->|cleanup enum| LLM[llm.py<br/>_complete: local / claude-cli / anthropic]
  LLM -->|uv run --offline| LW[llm_worker.py<br/>mlx-lm, Qwen3.5-4B]
  LLM -->|claude -p| CC[Claude Code CLI]
  LLM -->|_http.request| API[api.anthropic.com]
  LLM -->|stderr| EG[vocalize: sent to ...]
  N[notes.py] --> W
  N --> LLM
  N -->|0600 notes| NF[~/Documents/Vocalize Notes]
  AUTH[auth.py _backend] -->|macOS| SEC[/usr/bin/security]
  AUTH -->|elsewhere| KR[keyring]
  P[portal.py] --> AUTH
  P --> AST
  DOC[vocalize doctor] --> RD[readiness.doctor_rows]
```

## Key flows

### Hotkey to vocalize (DEC-028)

```mermaid
sequenceDiagram
  participant U as User
  participant A as Vocalize.app
  participant V as vocalize (fresh process)
  participant S as dictate.session
  U->>A: control-option-command-D (down)
  A->>A: isDown[id] = true; a dictate child still running? drop
  A->>V: spawn "vocalize dictate" (fixed env, cwd tmp, stderr to app.log)
  V->>S: claim (O_EXCL) with state "starting"
  V->>V: launch recorder, wait for take.wav to grow, cue, state "recording"
  A->>A: vnode event on ~/.cache/vocalize → read session → red icon
  U->>A: control-option-command-D (down, second press)
  A->>V: spawn "vocalize dictate"
  V->>S: state "transcribing"
  V->>V: transcribe → cleanup (llm) → clipboard → release session
  A->>A: session gone → idle icon
```

The app holds no state. Every hotkey is one fresh `vocalize` process, so an upgrade is picked up on the next press and a crash in one press leaves nothing behind. Stop and cancel run on a concurrent queue, so a long transcription never blocks them.

Speak-the-selection: the app checks `AXIsProcessTrustedWithOptions` (prompting once), records `NSPasteboard.changeCount`, posts a CGEvent Command-C, polls `changeCount` for up to 500 ms, and on a change spawns `vocalize clip`. Unchanged means "Nothing selected". `clip` exits 3 on the credential-shaped refusal; the app maps non-zero exits to fixed strings only. The app never reads pasteboard contents.

### Dictation with the cue trim (DEC-031, issue #2)

The recorder stays frozen, so `dictate._start` sequences the cue around the real open-microphone signal: the first growth of `take.wav`.

1. `_launch_recorder` as today.
2. `_wait_for_audio`: poll `take.wav` size every 20 ms until it grows past its first observed size, up to `_AUDIO_GRACE` (5 s). `t0` is the moment it grew.
3. Play the cue (word and/or sound, `audio.play` blocks). Write `workdir/cue` holding the seconds from `t0` to the end of the cue.
4. State `recording`.
5. `_finish_take` first calls `_trim_cue`: drop exactly `int(seconds * 16000)` frames from the head with stdlib `wave` into `take.trimmed.wav`, then `os.replace`. No padding, so the trim can under-cut by one flush and never eats speech.

Timeout (`rec.pid` present, no growth within `_AUDIO_GRACE`): play the cue anyway, write no `cue` file, no trim. `rec.pid` absent stays on the recorder-failed path.

The 1-hour cue spike (T-100) measures first-growth latency on the built-in and a Bluetooth input. If `take.wav` does not grow incrementally, the fallback keeps today's order: word cues before `_launch_recorder`, the Tink after first growth, no trim. In either branch no cue word reaches the worker; test_dictate asserts the frame count the fake `uv` receives for all three cue modes and both branches.

### Hold-to-talk (0.13.1)

`dictate --start` claims the session, opens the microphone silently (the key-down is the cue; no cue file, no trim) and exits 0 when the session is already claimed. `dictate --stop`: no session → 0; a live claim → 0; a dead recorder → the existing failure path; no `rec.pid` yet and under `_START_GRACE` → wait for it, then stop; otherwise stop. Never the 2-second cancel window. The app maps key-down and key-up to the two flags when `[app] dictate_mode = "hold"`; `isDown[hotKeyID]` replaces the 1-second `_DEBOUNCE` for app-originated presses.

### Auto-paste (0.13.1, `[stt] paste`, default off)

At claim time `dictate.session` carries a fresh `nonce` (`secrets.token_hex(8)`). After `copy_to_clipboard`, `_stop` writes `~/.cache/vocalize/dictate.copied` (0600, `O_NOFOLLOW`) holding the epoch and that nonce. The app, which read the nonce from the session it was watching and recorded the frontmost application's bundle identifier on the dictate key-down, unlinks the marker first, then posts CGEvent Command-V only if the nonce matches, the same application is still frontmost, and the marker is under 2 s old. A marker any other process plants therefore pastes nothing (review R6). Otherwise the transcript stays on the clipboard and the fixed notice "Copied, not pasted (window changed)." shows. No osascript, no second grant.

### Cleanup and summaries through llm.py (DEC-030)

`llm._complete(system, text, *, feature, backend)` is the only place the three backends are named:

1. `backend == "off"` → `None`.
2. Append `DATA_BOUNDARY` once to `system` (the cleanup prompts lose their own trailing boundary sentence and say "the text you receive", not "on stdin").
3. Check the backend is usable: `claude` binary present; a key resolvable for `anthropic`; `install.installed(llm_manifest)` for `local`. Not usable → `None` with a fixed reason word, **no egress line**.
4. `anthropic` only: ledger and `budget_for("anthropic")` gate; over budget → `None`.
5. For `claude-cli` and `anthropic`: `egress(backend)` prints `vocalize: sent to <backend>` to stderr immediately before the call.
6. Dispatch with `_LIMITS[feature]` = (timeout, max_tokens): cleanup (300 s, 4096), notes (1800 s, 8192).
7. `anthropic`: `ledger.record("anthropic", len(text))` on success.

`cleanup_transcript(text, backend, verbatim)`: if the first word of `text` is `verbatim` (lower-cased, punctuation stripped), strip it and use the verbatim prompt (punctuation and casing only). Otherwise the default prompt also drops restatements, false starts and filler (issue #3). Returns `(cleaned, True)` or `(text, False)`, the tuple `dictate._finish_take` consumes today. `bare --cleanup` means "on with the configured backend"; when `[stt] cleanup` is `off` it falls back to `local` if installed, else `claude-cli`.

`_claude_cli`: `[claude, "-p", <fixed one-word instruction>, "--append-system-prompt", system, "--model", "haiku", "--disallowedTools", "*", "--strict-mcp-config"]`, transcript on stdin, `cwd=tempfile.gettempdir()`, env copy with `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_BASE_URL` removed (a stored API key must never bill through the subscription path). T-20 verifies `--append-system-prompt` works in print mode on the installed Claude Code; if it does not, the prompt stays in `-p` and the docstring says claude-cli has no system channel. T-20 also verifies the flag that keeps **user-scope** settings out of the session (`--setting-sources` with an empty or minimal set, or `--safe-mode`, whichever the installed version accepts): `--strict-mcp-config` and the temp cwd drop project scope only, and this Mac's `~/.claude/settings.json` carries SessionStart and PreToolUse hooks that would otherwise run inside every transcript-bearing session (review R3). The flag joins the pinned argv. `claude-cli` draws on the same Claude Code usage pool as coding sessions; `docs/dictation.md` says so.

`_anthropic`: `_http.request("POST", "https://api.anthropic.com/v1/messages", headers x-api-key, anthropic-version 2023-06-01, content-type json; body {model: ANTHROPIC_MODEL, max_tokens, system, messages:[{role:user, content:text}]}, timeout, provider="anthropic")`. Non-200 → `None`, only the status code reported. `stop_reason` other than `end_turn`: cleanup → `None`; notes → keep the text plus one stderr line `vocalize: summary truncated`.

`_local`: `uv run --offline --no-project --python 3.12 --with mlx-lm==<pinned> llm_worker.py --model-dir <dir> --once`, one JSON request line on stdin `{"id":1,"system":…,"text":…,"max_tokens":…}`, one JSON reply line, `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `cwd=tempfile.gettempdir()`. Requesting `local` on 0.12 or 0.13 fails at run time with "local model support arrives in 0.14.0 — set [stt] cleanup = \"claude-cli\"" (the enum accepts all four values from 0.12.0 so a 0.14 config never breaks an older binary).

### Notes (DEC-029, DEC-023, DEC-024)

`vocalize notes SOURCE...` over one module `vocalize/notes.py`. Per source, non-recursive over a directory, sorted, hidden files skipped, allowlisted suffixes only (`.m4a .mp3 .wav .aif .aiff .caf .txt`); anything under the resolved notes folder is skipped (self-ingestion guard):

1. **Done check.** `_done(folder)` scans `folder/*.md`, reads the first 1 KB of each and collects `(source_sha256, source)`. A source is skipped only when **both** its hash and its resolved path match a note, and every skip is printed by name, so a planted or synced note cannot silently suppress a recording (review R5); `--force` rewrites that note in place. The notes folder is the ledger; there is no ledger file.
2. **Transcript.** `.txt` → read verbatim through `dictate.sanitize` (the Plaud CLI's output). Audio → `afconvert -f WAVE -d LEI16@16000 -c 1 <resolved src> <tmp>/take.wav` (timeout 300, capture_output) in a `mkdtemp("vocalize-notes-")` 0700 dir deleted in `finally`; duration from stdlib `wave`; then `_transcribe_long`: `Popen(dictate.worker_argv(...) + ["--segments"])` with `encoding="utf-8", errors="replace"`, **every segment's text through `dictate.sanitize`** before it reaches the summarizer or the note (the audio path is as untrusted as the `.txt` path; review R2), progress lines echoed every 10 % of duration, an **inactivity** timeout `_SEGMENT_TIMEOUT` (600 s without a worker line = wedged, kill, "transcription timed out"). Stale `vocalize-notes-*` dirs are swept at run start (the sweep generalised from dictate).
3. **Summary.** `llm.summarize(transcript, template_text, backend)` with `backend` from `--summarizer` (a `click.Choice` over `config.NOTES_SUMMARIZERS`) else `[notes] summarizer`. Per-destination caps `_MAX_CHARS = {"local": 120_000, "claude-cli": 400_000, "anthropic": 400_000}`; over the cap → transcript-only note and one line naming the cap. Failure never loses the transcript.
4. **Note.** `<folder>/<YYYY-MM-DD>-<slug>.md`, date = `min(st_birthtime, st_mtime)`, slug from the stem (fallback: first 8 hex of the hash). Every write, first or forced, goes to a `.tmp` in the folder (`O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW`, 0600) and is `os.replace`d into place, so a killed run never leaves a half note that the done check would trust (review R9); the final name is chosen after an existence check, collisions `-2`, `-3`. The folder is created 0700 only when this run creates it; an existing folder is never re-moded. A `PermissionError` under `~/Documents` becomes one message naming System Settings › Privacy & Security › Files and Folders.
5. **Lock.** `flock(LOCK_EX|LOCK_NB)` on `<folder>/.vocalize.lock` for the whole run; a second run prints "another notes run is in progress" and exits 1.

A machine with no local model and `summarizer = "local"` writes transcript-only notes and says so (the 8 GB tier). `left_machine` is `true` exactly when the egress line was printed.

### Keychain through security (DEC-025)

On macOS `auth._backend()` returns a thin object with the same three methods keyring exposes, implemented over `/usr/bin/security`: writes through `security -i` with the command on stdin (the secret never in argv), reads with `find-generic-password -s vocalize -a <username> -w`, deletes with `delete-generic-password`, and a comment (`-j "validated <ISO date>"`) as the last-validated stamp. `login` migrates an existing keyring-written item by deleting and re-adding it. keyring stays the backend everywhere else. T-30 (30 minutes) proves the item is readable from a rebuilt caller without a prompt before any of this is built; a failed check keeps keyring and documents the gotcha (DEC-035).

## Pause and resume

Two pauses, one idea: stop cleanly, keep what was captured, carry on from there. Neither touches Swift, so neither costs a grant.

### Playback pause (0.13.1)

**Mechanism.** `vocalize pause` is `audio.stop_playback(remember=True)` — the same call a dictation makes (DEC-003). No new file, no new format, no new lifetime. `vocalize resume` continues it; `vocalize resume --forget` discards it.

**Waiting for the record.** `dictate._wait_for_record` moves to `interrupted.wait_for_record(since)` and both callers use it. It already carries what pause needs: only a record newer than `since`, an early-out when the stop found no player, and `_RESUME_GRACE` for a cloud provider whose record lands seconds after the player died. So pause prints "Paused. Resume it within the hour with: vocalize resume" only when a record actually landed, and "Nothing is playing." otherwise.

**Commands.** New: `vocalize pause`. Unchanged: `vocalize stop`, `vocalize resume`, `vocalize resume --forget`. `stop` keeps today's meaning exactly — it silences, records nothing, and leaves any saved record alone. No shipped stop test is rewritten.

**Hotkey.** New `[app] stop_hotkey = "stop" | "pause"`, default `stop`, printed by `vocalize settings`. Set to `pause`, the app's existing control-option-command-X becomes play/pause: a live read pauses; nothing playing plus a record present resumes it; neither says "Nothing is playing." The chord compiled into 0.13.0 already spawns `vocalize stop`, so this is one Python branch and no re-grant. Default off, because with it on every stop press writes plaintext. The resume branch first checks `dictate._read_session()`: while a dictation is live it refuses and prints nothing, rather than resuming a paused read into a microphone that is recording it — the muscle memory 0.13.0 taught (reach for the stop chord mid-recording) must never wake a stale read instead of ending the take.

**Resume overlap.** `_RESUME_REWIND = 1.0` inside `interrupted.slice_from`, clamped at zero. `offset_seconds` is wall clock since Popen, not a decoded frame position, so a continuation starts a word early rather than mid-syllable. The shipped dictation-interrupt resume gets the same overlap for free.

**State on disk.** Exactly what a dictation interrupt writes: `interrupted.<ext>`, `interrupted.txt`, `interrupted.json` (version 2), all 0600 through `O_NOFOLLOW` under the 0700 cache directory, one record at a time, `MAX_AGE` one hour. The honest delta is frequency: a deliberate pause now writes `interrupted.txt` too. The docs say so (DEC-012e).

**Interlock with DEC-003.** One mechanism, not two. A dictation taken while a read is paused finds no player and never offers the paused record: `wait_for_record` requires a record newer than the dictation's own start, so the dialog stays silent and the record survives untouched. `vocalize resume` is where it waits. One record at a time still holds — a later interrupt replaces a pause, last writer wins.

**Interlock with run 11.** Run 11 owns the dictation state machine; run 11b touches `cli.py`, `config.py` and `interrupted.py`, plus one line of `dictate.py`. `dictate --start` and `--stop` are untouched.

### Recording pause (0.14.0)

**Mechanism.** `vocalize dictate --pause` stops the recorder through the existing stop file and waits for it to exit. That is the only path that finalises the WAV header; the recorder's signal handler exits without finalising, so signals are never used. Run 11's `_trim_cue` runs on the finished take, which then becomes `take.NNN.wav`. A `paused` marker holds the epoch and the seconds recorded so far. `vocalize dictate --resume` launches a fresh recorder into a new `take.wav`, waits for first growth, plays the Tink and writes that segment's own `cue` file. The recorder is never rebuilt.

**Joining.** `_join_segments` runs in `_finish_take` immediately after `_trim_cue`: stdlib `wave`, segments in numeric order then the live take, 0.25 s of zero frames at each seam so whisper does not glue two half-words together, params asserted identical, written to `take.joined.wav` then `os.replace`. It keys on the glob, not the marker, so a crash between the rename and the marker still joins. The worker receives exactly one 16 kHz mono 16-bit WAV and needs no change.

**Budgets.** `--max` stays per segment and still obeys the recorder's frozen 1..600 bound; each resume passes `max(1, min(max_seconds, remaining))`. New `[stt] max_take_seconds` (default 1800, bounded 60..7200) caps the whole take, and `_MAX_SEGMENTS = 20` caps the count. 1800 s is about 57 MB of WAV in the temporary workdir. Per-segment budgets are what make a take longer than ten minutes possible at all. `_TRANSCRIBE_TIMEOUT` and the `_FINISH_TIMEOUT` derived from it stay fixed at 300 s today, sized for a single ten-minute segment; once segments join into one long take, the transcription subprocess must be given a budget that scales with the joined duration (`max(300, take_seconds * k)`, `k` measured against the run-13 spike), or a real long memo is killed mid-transcription and its workdir discarded on the way out.

**State on disk.** Everything sits inside the existing per-take `mkdtemp` workdir: 0700, removed by `_discard` on every exit path, swept after 24 hours. `dictate.session` gains no new state value — a paused take still reads `recording`, so the 0.13.0 Swift app's vocabulary is untouched and nothing is owed a re-grant.

**Never wedged, never lost.** A plain toggle press while paused is a stop: `_second_press` branches on the marker and calls `_stop` with `pid None`. Without that branch the shipped code finds no usable `take.wav`, falls to `_fail`, discards the workdir and says the recorder failed — the whole memo gone, with a misleading notification. `--cancel` while paused removes every segment.

**Hotkey.** With `[app] stop_hotkey = "pause"` (shipped in 0.13.1), the stop chord gains two branches ahead of the playback ones: a live session reading `recording` pauses, one holding the marker resumes. The state word is untrusted input, so anything unrecognised falls through to playback. DEC-003 keeps a read and a recording from ever both being live, so the two can never collide.

**Interlock with run 15.** `vocalize notes` never opens a microphone — it transcribes files that already exist. So `notes.py` is not touched, `[notes] keep_audio` governs a different directory, and no byte reaches the notes folder. Recording pause makes dictation takes pausable, and claims nothing more.

### Out of scope

`SIGSTOP` on the player: it holds the machine-wide `play.lock` for the whole pause, so every other read on the Mac blocks silently. A pause verb in either Swift source: each costs a grant. Sub-second offset precision, more than one paused read, a pause queue. A live memo recorder: that is a separate `vocalize record` command with its own session, budget and disk story, and its own run.

## Contracts

### `[stt]` additions (0.12.0, 0.13.1, 0.14.0)

```toml
[stt]
beam_size = 5        # 1–8; 1 is greedy (0.10 behaviour); the escape hatch if beam search costs too much on a machine
cleanup = "off"      # off | local | claude-cli | anthropic  (legacy true → "claude-cli", false → "off")
verbatim = false     # skip the restatement/filler pass; the spoken first word "verbatim" does the same for one take
paste = false        # 0.13.1: paste after copying, only into the window the dictation started in
max_take_seconds = 1800  # 0.14.0: 60–7200; caps the whole paused-and-resumed take — --max (max_seconds) stays per segment
```

`vocalize settings` prints `stt.cleanup=<word>`, `stt.verbatim=…`, `stt.paste=…`. `listen`/`dictate` gain `--verbatim`; `--cleanup` keeps its meaning.

### `[notes]` table (parsed and rendered from 0.12.0, honoured from 0.14.0)

```toml
[notes]
folder = "~/Documents/Vocalize Notes"   # non-empty, expanduser
template = "memo"                       # memo | meeting | lecture | journal | absolute path to a .md prompt
summarizer = "local"                    # local | claude-cli | anthropic | off
keep_audio = false
model = ""                              # "" = the [stt] model; else allowlisted against whisper_manifest.MODELS
```

`KNOWN_CONFIG_KEYS` gains `notes`; `_validate_notes_table` warns on unknown keys, raises `ConfigError` on bad values; `resolve_notes`; `wizard._render_config_text` renders the table by name beside `[stt]` with a round-trip test; `vocalize settings` prints `notes.summarizer=…` and `notes.template=…`.

### `[app]` table and chord grammar (0.13.0)

```toml
[app]
dictate = "ctrl+alt+cmd+d"
dictate_mode = "toggle"   # toggle | hold; 0.13.0 accepts "hold" but treats it as toggle with one warning until 0.13.1 (a rollback never bricks the CLI)
speak = "ctrl+alt+cmd+s"
stop = "ctrl+alt+cmd+x"
stop_hotkey = "stop"      # 0.13.1: stop | pause; the app never reads this key itself (see note below), it only changes what `vocalize stop` does
```

Chord grammar: tokens split on `+`; modifiers `ctrl|alt|cmd|shift` (aliases control, option, command); key `a–z`, `0–9`, `f1–f12`; must include `ctrl` or `cmd` (macOS refuses otherwise, error -9868); `""` disables a chord; chords pairwise distinct. `_validate_app_table`, `resolve_app`, rendered by the wizard, printed by `vocalize settings` as `app.<key>=…`. A test asserts the Python allowlist equals the key names in the Swift keycode table. The app runs `vocalize settings` at launch and on a vnode event on `~/.config/vocalize` (the directory, because `_write_config` replaces the file) and parses only the `app.*` and `stt.paste` lines. The four chord names (`dictate`, `dictate_mode`, `speak`, `stop`) are registered explicitly; any other `app.*` key the parser does not recognise is ignored rather than treated as a chord, so a later release can add an `app.*` key (`stop_hotkey` included) without costing every user a rebuild and an Accessibility re-grant.

### App spawn contract and binary discovery

Swift `run(args, queue)`: `Process` with a fresh environment `{HOME, USER, TMPDIR (if launchd set it), LANG=en_US.UTF-8, PATH=/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin}`, nothing inherited, no `DYLD_*`; cwd `NSTemporaryDirectory()`; stdin and stdout `/dev/null`; stderr appended to `~/.cache/vocalize/app.log` (0600; checked on every dispatch and truncated in place above 1 MB, not only at launch; egress lines land here). Queues: `dictate` (a key-down is dropped while a dictate child is running), `speak` serial, `stop` concurrent. Verbs used: `dictate [--start|--stop]`, `clip`, `stop`, `listen --cancel`, `settings`, `portal`. These verbs and the `key=value` lines of `settings` are a compatibility contract for one minor release past any change.

Binary: `UserDefaults` key `VocalizeBinary` (override), else the first of `~/.local/bin/vocalize`, `/opt/homebrew/bin/vocalize`, `/usr/local/bin/vocalize`. **The override and every candidate pass the same checks**: a regular file, executable, owned by the user or root, not world-writable; an override that fails them is ignored with a fixed notice and discovery continues (review R1). The app holds the Accessibility grant and is kept alive by launchd, so it never executes a path it has not checked. Re-resolved once on ENOENT or exit 126/127, then the fixed notice "vocalize could not run — run: uv tool install --reinstall vocalize-cli". Nothing is baked at install. `vocalize app install` resolves its own `sys.argv[0]` against the three paths through symlinks and, with no match, prints the exact `defaults write cards.arda.vocalize.app VocalizeBinary <path>` line and exits 1 unless `--yes`.

### Files under `~/.cache/vocalize`

| File | Writer | Reader | Contents |
|---|---|---|---|
| `dictate.session` (existing) | dictate | app, dictate | JSON gains `"state": "starting" \| "recording" \| "transcribing"`, rewritten in place (`O_NOFOLLOW`, 0600); `_read_session` ignores unknown keys |
| `app.status` | app, on launch and every dispatch | `app status`, readiness, doctor, portal | `accessibility: granted\|not granted`, `vocalize: <path\|none>`, `hotkeys: ok\|failed:<name>[:taken]`, `hotkey_backend: carbon\|monitor`; parsed as untrusted words |
| `app.log` | app | user | children's stderr |
| `dictate.copied` | dictate (0.13.1) | app | epoch; unlinked by the app before pasting |
| `<workdir>/cue` | dictate (0.13.1) | dictate | seconds to trim from the head of the take |

### LaunchAgent and bundle identity

`~/Library/LaunchAgents/cards.arda.vocalize.app.plist` (0644, written with `plistlib`): `Label`, `ProgramArguments=[<bundle>/Contents/MacOS/vocalize-app]`, `RunAtLoad`, `KeepAlive={SuccessfulExit: false}`, `ThrottleInterval=10`. Install: `launchctl bootout gui/$UID/<label>` (failure ignored) then `bootstrap`; liveness from `launchctl list <label>` (documented exit code and PID line), a parse failure reads as `unknown`, never as `not running` (review R12); `app restart` = `launchctl kickstart -k`; `app uninstall` = bootout, remove plist, bundle, stamp, `app.status`, `app.log`, `dictate.copied`, `defaults delete … VocalizeBinary` (the Accessibility entry stays, the docs say so).

Bundle `~/Library/Application Support/vocalize/Vocalize.app` (not under `~/.cache`, which the README calls safe to delete; the recorder stays where it is; review R8), identifier `cards.arda.vocalize.app`, executable `vocalize-app`, `LSUIElement`, `CFBundleVersion` fixed `1.0`, no vocalize version substituted, frameworks AppKit and Carbon, no entitlements, `codesign -s - --force --options runtime`, content stamp `.app`. Built by `install.build_bundle(APP_SPEC)`, the generalisation of `build_recorder` (`BundleSpec(source, plist_template, entitlements, bundle_name, binary_name, frameworks, stamp_name, stamp_version)`); a golden test pins the recorder's fingerprint keys, stamp version and `swiftc`/`codesign` argv byte for byte, because any drift there is a microphone re-grant. On `rebuilt`, `app install` runs `tccutil reset Accessibility cards.arda.vocalize.app` before bootstrapping and prints `APP_REGRANT_WARNING`. Every change to `VocalizeApp.swift` after 0.13.0 is a re-grant, which is why the Swift source ships complete in 0.13.0 (hold dispatch, paste watcher, both hotkey backends) and 0.13.1 is Python only. Any loss of the bundle or its stamp (a wiped folder, a restore from backup) costs the same re-grant as a rebuild, and `docs/app.md` and the README's known-limitations section say so (review R13). A Swift defect found after 0.13.0 ships as an out-of-band patch release that costs one re-grant; that is a documented trade-off, not a process failure (DEC-032).

Hotkey backend: Carbon `RegisterEventHotKey` (press and release, no permission) when the spike passes; otherwise `NSEvent.addGlobalMonitorForEvents` gated on `AXIsProcessTrusted()` under the same Accessibility grant (DEC-033 records which). The choice is a build-time constant; a later macOS regression of the chosen backend surfaces as `hotkeys: failed` in `app.status` and a doctor row, and switching backends is a rebuild and a re-grant (an accepted one-way door, DEC-033). `app install` warns when Hammerspoon is running (`pgrep -x Hammerspoon`, prints the two-line removal snippet) or when `~/Library/Services/Dictate with Vocalize.workflow` exists; `eventHotKeyExistsErr` writes `hotkeys: failed:<name>:taken`.

### llm.py API (0.12.0)

```
BACKENDS = ("off", "local", "claude-cli", "anthropic")
DATA_BOUNDARY: one sentence, appended once by _complete
_LIMITS = {"cleanup": (300, 4096), "notes": (1800, 8192)}   # (timeout s, max_tokens)
MAX_TIMEOUT = 300            # dictate._FINISH_TIMEOUT sums this
ANTHROPIC_MODEL = "claude-haiku-4-5"   # one constant, cost not capability
cleanup_transcript(text, backend, verbatim=False) -> (str, bool)
summarize(text, template_text, backend) -> str | None
egress(destination) -> None            # the only writer of "vocalize: sent to <destination>"
validate_anthropic_key(key) -> None    # GET /v1/models, body unread; 401 -> ProviderAuthError
RUN_SEAM = subprocess.run
```

`dictate.py` loses `cleanup_transcript`, `_CLEANUP_PROMPT`, `_DENY_TOOLS`, `_CLEANUP_FLAGS`, `_claude_bin`, `_claude_env`, `_CLEANUP_TIMEOUT`; `_finish_take` calls `llm.cleanup_transcript(text, stt["cleanup"], stt["verbatim"])` when the enum is not `off`. The cleanup tests move to `tests/test_llm.py` with their argv assertions intact. `hooks/speak_options.py` keeps its own copy of the `claude -p` shape (it runs under `/usr/bin/python3` with no vocalize import) and gains `--strict-mcp-config` and `cwd=tempfile.gettempdir()`.

### Local language-model worker and manifest (0.14.0)

`vocalize/local/llm_worker.py` (never imported by vocalize; `mlx_lm` imported inside `_mlx()`, the test seam): `--model-dir DIR --once | --selftest`. `_check_config(dir)` reads `config.json` **and** `tokenizer_config.json`, exits 1 on `model_file`, `auto_map`, `custom_pipelines`, a `tokenizer_class` outside an allowlist, or a `model_type` other than the pinned one. The prompt is built **as token ids**: the fixed ChatML pieces from `_CHAT_TEMPLATE` (a constant in the worker; think block empty) encoded normally, `system` and `text` encoded with `add_special_tokens=False, split_special_tokens=True`, concatenated; `tokenizer.apply_chat_template` is never called and no downloaded template is evaluated (DEC-027). `load(dir, tokenizer_config={"trust_remote_code": False})`; greedy generate; a `length` finish or an unterminated think block replies `{"ok": false, "error": "truncated"}`. The selftest asserts a fixed cleanup (`hello world this is a test` → starts with `Hello`) so a wrong template fails at install.

`vocalize/local/llm_manifest.py`: `REPO`, `REVISION` (a commit hash), `FILES` = `config.json`, `tokenizer.json`, `tokenizer_config.json`, the safetensors file or index plus shards, each with size and sha256 from a verified download on the owner's Mac (T-130); no `.py`, no `.jinja`; `MODEL_DIR = ~/.cache/vocalize/models/qwen`; `RUNTIME_PACKAGE = "mlx-lm==<pinned>"` (recorded in the `.verified` stamp; a mismatch reports "installed by an older vocalize — run: vocalize local install --llm"); `PYTHON_VERSION = "3.12"`; `MODEL_TYPE`; `MIN_RAM_BYTES` (12 GiB via `os.sysconf`, `--force` skips the gate and prints the measured RAM); `check_config`; `worker_path`; `selftest_argv`. `vocalize local install --llm`, `local uninstall --llm`, an LLM block in `local status`, readiness row `llm model`, portal install target `llm`. The install selftest is the one `uv run` **without** `--offline` (it may populate uv's cache); every later `_local` call carries `--offline`, and a test asserts the two argvs differ in exactly that flag. The 1-hour mlx-lm spike (T-121) runs before the manifest is pinned and measures the latency a real press pays: repeated **cold** one-shot runs (a fresh `uv run` each time, page cache warm), not same-process warm calls.

### Whisper worker `--segments` (0.14.0)

`whisper_worker.py --transcribe … --segments` prints `{"progress": <seconds>}` lines (flushed) as segments complete and a final reply `{"ok": true, "text": …, "segments": [{"start": s, "end": s, "text": …}]}`. Without the flag the output is byte-identical to today. If Parakeet ships (DEC-034), `dictate.worker_argv` dispatches on `[stt] engine` and the sherpa worker implements the identical contract with the same stub-Model test.

### Note file (0.14.0)

```
---
title: "standup"
date: "2026-09-04"
type: "recording"
captured: "2026-09-06"
license: "personal-use"
topics: []
source: "/Users/mat/Downloads/standup.m4a"
source_sha256: "…"
duration_seconds: 3600          # null for text input
template: "memo"
transcribed_by: "whisper:small.en"      # | "file"
summarized_by: "local:qwen3.5-4b"       # | "claude-cli:haiku" | "anthropic:claude-haiku-4-5" | "none"
left_machine: false
trust: "untrusted-transcript"      # the transcript and the summary are third-party text; never feed a note to a model as instructions
vocalize: "0.14.0"
---
<summary markdown, or "_No summary — transcript only._">

## Transcript
[00:00] …
```

Strings are `json.dumps`-quoted (a valid YAML subset); the key set covers the knowledge-base source template so a note drops into that repo's `sources/` folder. Templates: `vocalize/assets/notes/{memo,meeting,lecture,journal}.md`; any other value must be an existing `.md` path, opened with `O_NOFOLLOW` and capped at 64 KB (a custom template is trusted as the system prompt, and the docs say so; review R7); `DATA_BOUNDARY` is appended by `llm._complete`, so custom templates are covered by construction. `docs/notes.md` states that a note is untrusted input to any downstream model, and the knowledge-base handoff keeps the `trust` key.

### Keychain via `security` (0.12.0)

| Operation | Command | Notes |
|---|---|---|
| write | `security -i` with `add-generic-password -U -s vocalize -a <username> -j "validated <ISO>" -w <key>` on **stdin** | the secret never in argv |
| read | `security find-generic-password -s vocalize -a <username> -w` | stdout only |
| delete | `security delete-generic-password -s vocalize -a <username>` | read back after, as today |
| status | `find-generic-password … -j` comment | last-validated stamp |

Behind `_backend()` on Darwin only; tests drive it with a fake `security` script on PATH. Gated on T-30 (DEC-035).

### Key slots and the Keys tab (0.12.0)

`auth.KEY_SLOTS = ("elevenlabs", "openai", "google", "anthropic")` beside `PROVIDER_USERNAMES`; `anthropic` is **not** added to `PROVIDER_NAMES` (the TTS chain vocabulary). Every front door accepts the slots: `auth login/status/logout/validate --provider` (`click.Choice(auth.KEY_SLOTS + ("polly",))`), `portal._provider_or_404`, the `/api/state` key listing, `vocalize usage` (prints the anthropic ledger row). New routes: `POST /api/auth/remove/<slot>` (reports `delete_key`'s read-back), `POST /api/auth/test/<slot>` (validate without storing; `ok` or `fail` only). The test route runs `auth._check_shape` before any request and `auth.scrub` on every message it returns, exactly as `login` does, so a control-character key never reaches a header and no response echoes it (review R4). `budget_for("anthropic")` defaults to 2,000,000 characters a month when unset; the ledger is per Mac, and the docs say so. Page: `autocomplete="new-password"`, remove and test buttons, the validated stamp, a hint that the key sits on the clipboard, and the cleanup checkbox becomes a select over the enum. `[providers.anthropic] monthly_chars` is the budget's home.

### Readiness, doctor and the Setup tab (0.13.0)

`readiness` gains rows `app` (bundle current, stale or not built), `app agent` (`launchctl print` pid), `accessibility` (from `app.status`), `llm model` (0.14). `readiness.doctor_rows()` = every row regardless of chain, plus `cli path`, `uv`, `swiftc`, `claude`, the console-script shebang interpreter (brew rot), the Hammerspoon and Services conflict checks, `cli start-up` (wall time of `vocalize --version`, warn above 400 ms), `app bundle` (missing → the `vocalize app install` repair line), `notes folder` (size on disk, kept audio included), and a warning when the notes folder resolves under `~/Library/Mobile Documents`. `vocalize doctor` prints like `status`, exits 0 or 1. `vocalize integrate claude` runs the PATH pre-check, installs the `/speak` skill and the Quick Actions (the existing scripts moved into the package), and prints the GUI-only steps. `/api/state` gains an `app` key from `app.status_dict()`; the Setup tab renders the wizard steps of the analysis over the existing install start/status routes with a new target `app`.

### Fallback note (DEC-022)

`chain.run` already prints `Spoke via say (fallback).` When the failure that caused the fallback is Kokoro's "not installed" `ProviderError`, the line becomes `Spoke via say (fallback) — Kokoro is not installed; run: vocalize local install`. One string, tested through the existing fake providers.

## Decision summary

| # | Decision | Where it shows up |
|---|---|---|
| DEC-020 | Locally compiled Swift menu-bar app; no Developer ID, notarization or prebuilt binary | § Approach, § LaunchAgent and bundle identity |
| DEC-021 | One Accessibility grant to the app for speak-the-selection and paste | § Hotkey to vocalize, § Auto-paste |
| DEC-022 | `DEFAULT_CHAIN = ("kokoro", "say")` with the fallback note | § Fallback note |
| DEC-023 | Notes store the full transcript, 0600, in the notes folder | § Notes, § Note file |
| DEC-024 | Two cloud paths, `claude-cli` first, `anthropic` second | § Cleanup and summaries |
| DEC-025 | macOS keychain through `/usr/bin/security`, gated on the check | § Keychain through security |
| DEC-026 | Whisper only through 0.13; the Parakeet spike gates 0.14 | § Whisper worker `--segments`, plan Phase 13 |
| DEC-027 | No downloaded chat template is evaluated; the worker builds token ids from its own constant | § Local language-model worker |
| DEC-028 | Stateless shell-out app; no resident helper, no baked binary | § Hotkey to vocalize, § App spawn contract |
| DEC-029 | One `notes` command, one module; the notes folder is the ledger | § Notes |
| DEC-030 | One `llm.py` with `_complete`; enum values fixed | § llm.py API |
| DEC-031 | Cue timing fixed by trimming the take in dictate.py; the recorder stays frozen | § Dictation with the cue trim |
| DEC-032 | Four releases; the Swift source complete in 0.13.0 so 0.13.1 is Python only | § Approach |
| DEC-033 | Hotkey backend from the spike (deferred to T-60) | § LaunchAgent and bundle identity |
| DEC-034 | STT engine for 0.14 from the spike (deferred to T-120) | § Whisper worker `--segments` |
| DEC-035 | Keychain backend from the check (deferred to T-30) | § Keychain via `security` |

## Testing strategy

- **Unit, no hardware, network, keychain or Xcode:** every new seam is a fake — `security` as a script on PATH; `claude` as the existing fake script (now also dumping its environment); `uv` as a script logging argv and stdin; `launchctl`, `tccutil`, `pgrep` and `afconvert` as scripts behind one seam; `_http.urlopen` monkeypatched; a fake recorder that appends 3200 bytes every 50 ms so the cue trim is exercised; `FakeToolchain` shared from conftest; autouse fixtures pointing `LAUNCH_AGENTS_DIR`, the notes folder and every cache path at `tmp_path`.
- **Static checks standing in for hardware:** `xcrun swiftc -parse` on `VocalizeApp.swift` (skipped without the compiler); `plutil -lint` on the app plist and the LaunchAgent; the AST import-discipline test on `llm_worker.py`; the golden test on the recorder build argv.
- **Security negatives per class:** transcript never in argv on all three backends and never in a notification; the API key never in stderr, an error message or the `claude -p` environment; injection-shaped text passed as data on all three backends; special-token literals round-trip as plain text through the worker; `--strict-mcp-config` and the temp cwd on every `claude -p`; the egress line printed exactly once and only before a real send; a missing key or binary prints none; no cue word in the WAV the worker receives; a second dictate key-down dropped while a child runs; `dictate.copied` unlinked before any paste; `app.status` parsed as untrusted words; the notes folder never re-moded; a `-x.m4a` file never parsed as a flag; the self-ingestion guard; the run lock; chord grammar rejects modifier-only and unknown keys; `clip` exit 3 mapped to a fixed string; a world-writable or foreign-owned `VocalizeBinary` override is ignored; a `dictate.copied` marker with the wrong nonce pastes nothing; a control-character key is refused by the test route before any request; an escape-sequence-bearing segment is stripped and the note carries `trust`; a note with the right hash but another source path does not suppress a recording; a symlinked or oversized custom template is refused; the selftest argv lacks `--offline` and the runtime argv has it.
- **Deliberately not unit tested:** hotkey delivery, the launchd relaunch, Accessibility prompts, cue latency on real inputs, real model quality and speed — all under verification.md § Manual checks, owner present.
