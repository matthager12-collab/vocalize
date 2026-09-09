# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added

- **The dictation cue no longer reaches the transcript.** The "Start."
  word and/or Tink now play after the microphone is actually open, timed
  off the real first growth of the recording rather than a guess, and
  those seconds are trimmed back off the head of the take before it's
  transcribed ([#2](https://github.com/matthager12-collab/vocalize/issues/2)).
  A machine where the recording never grows incrementally falls back to
  today's order (word before the microphone opens) with no trim.
- **Hold-to-talk.** `[app] dictate_mode = "hold"` turns the dictate chord
  from a toggle into a press-and-hold: hold it, speak, let go, no cancel
  window. `vocalize dictate --start` / `--stop` drive the same thing from
  a terminal or a script. `--start` is idempotent; `--stop` never treats a
  short hold as a cancel.
- **`[stt] paste`** (default off). When on, a dictation that copies to the
  clipboard also pastes into the app you started it in — a marker file
  carrying the dictation's nonce is how the menu-bar app knows it's safe
  to paste (same session, same frontmost app, under 2 seconds old); a
  window switch leaves the transcript on the clipboard and says "Copied,
  not pasted (window changed)." instead.
- `vocalize settings` prints `stt.paste=…`.

## 0.13.0 - 2026-09-08

### Added

- **The `[app]` config table** for the menu-bar app that arrives in 0.13.0:
  `dictate`, `speak` and `stop` chords (`ctrl+alt+cmd+d`, `+s`, `+x` by
  default; tokens joined by `+`, modifiers ctrl/alt/cmd/shift with their
  long-form aliases, a key of a–z, 0–9 or f1–f12, ctrl or cmd required,
  `""` disables) and `dictate_mode` (`toggle`; `hold` parses and resolves
  to toggle with one warning until 0.13.1). The wizard renders it and
  `vocalize settings` prints `app.<key>=…` in one canonical spelling.
- **`vocalize app install / uninstall / status [--json] / restart`** —
  a menu-bar app (`Vocalize.app`) that owns the dictation, speak and stop
  hotkeys system-wide, as a LaunchAgent, so a hotkey works from any app
  with no Services menu and no per-app shortcut assignment. `install`
  builds the bundle, warns about a running Hammerspoon or a Services
  shortcut assigned to the "Dictate with Vocalize" Quick Action, resets
  the Accessibility grant
  only on a rebuild, and loads the LaunchAgent; `status` reports what's
  built, whether the agent is loaded, and the app's own hotkey and
  Accessibility state; `restart` kicks the running app without touching
  the LaunchAgent; `uninstall` removes everything `install` put down
  and leaves the Accessibility entry in System Settings alone. See
  [docs/app.md](docs/app.md).
- **`dictate.session` gains a `state` and a per-dictation `nonce`.**
  `state` moves through `starting` → `recording` → `transcribing` as a
  dictation runs, so anything reading the session file (the app included)
  knows which stage it's watching, not just that one is in progress.
  `nonce` ties one dictation's session to its eventual `dictate.copied`
  write. Unknown keys in the file are still ignored.
- **`vocalize doctor [--json]`** — every readiness row plus the toolchain
  and environment checks `status` doesn't cover: `cli path`, `uv`,
  `swiftc`, `claude` on PATH, the running console script's shebang
  (brew rot), a Hammerspoon or old Quick Action conflict, `vocalize
  --version` cold-start time, app bundle state with its repair command,
  and the notes folder's size on disk. Exits 1 only on a failing row;
  warn rows print but don't fail a script.
- **`vocalize integrate claude [--yes]`** — one command in place of the
  hand-rolled `/speak` skill and the standalone Quick Action installer.
  Checks `vocalize`, `claude`, `node`, and `python3` on PATH, installs
  `~/.claude/skills/speak/SKILL.md` from the package (kept unless
  `--yes`), installs the four Quick Actions into `~/Library/Services`,
  and prints the GUI-only hotkey and Accessibility steps. The Quick
  Action installer and its `.workflow` bundles moved into the package
  (`vocalize/assets/quick_actions/`); `hooks/install_quick_action.py` is
  now a thin wrapper around it. The baked `claude` path is the stable
  symlink, not `.resolve()`'s version-pinned Caskroom target, so a
  `brew upgrade claude-code` no longer silently breaks the summary
  picker.
- **The Setup tab** in `vocalize portal` — a nine-step checklist
  (toolchain, both bundles, login item, chords, model download,
  microphone, `/speak` and Quick Actions, Accessibility, self-test)
  built from the readiness rows the sidebar shows; its first step points at
  `vocalize doctor`. Its install
  buttons drive the existing `/api/local/install` routes, now including
  target `app`. See [docs/app.md](docs/app.md#the-setup-tab).

### Changed

- **The recorder is built by a general bundle builder.** `build_bundle(BundleSpec)`
  replaces the recorder-only code path; `build_recorder` is the recorder's
  door onto it and its build is unchanged byte for byte (a golden test pins
  the `swiftc` and `codesign` argv, because any drift is a new ad-hoc
  signature and a microphone re-grant). `vocalize local uninstall --stt`
  now removes only the recorder bundle and its stamp, never the whole
  `~/.cache/vocalize/bin`.
- **`vocalize clip` exits 3, not 1, when it refuses a credential-shaped
  clipboard.** A script that already treats exit 1 as "something went
  wrong, try again" can now tell that refusal apart from a real failure.

## 0.12.0 - 2026-09-07

### Changed

- **The default chain is now `kokoro`, then `say`.** A config with no
  `chain` used to try ElevenLabs first; it now tries the on-device voice
  first and falls back to macOS `say`. When `say` speaks because the Kokoro
  model was never installed, the fallback line says so once and names the
  fix: `vocalize local install`. Users who never set a chain hear `say`
  after upgrading until they run that install; anyone with an explicit
  `chain` sees no change. `vocalize usage`, `vocalize status` and the portal
  now list the local providers first.
- **Dictation decodes with beam search.** whisper.cpp ran with its greedy
  decoder, which merged words on fast speech ("toget" for "to get",
  [#4](https://github.com/matthager12-collab/vocalize/issues/4)). The worker
  now uses beam search with five beams, whisper.cpp's own default for that
  strategy. `[stt] beam_size` (1–8) is the escape hatch: `1` restores the
  greedy decoder if a take is slow to land on your machine. Honest result:
  on the owner's own voice both decoders still produced "themerge" for
  "the merge", so beam search improves decoding but does not close #4;
  that issue stays open. The cost was measured on a 43 s synthetic clip
  and recorded in `docs/plans/2026-09-app-roadmap/spike-notes.md`.

- **The default dictation model is now `large-v3-turbo-q5_0`.** On the
  owner's own voice it was the first model to keep "the merge" as two
  words ([#4](https://github.com/matthager12-collab/vocalize/issues/4));
  beam search alone did not. It is no slower than `small.en` on an M4 and
  uses about 90 MB more. **Upgrade note:** if your `[stt]` table sets no
  `model`, dictation looks for the new default — run
  `vocalize local install --stt` once (547 MB), or set
  `model = "small.en"` to keep the lighter model.
- **`[stt] cleanup` names where the cleanup pass runs.** It was `true` or
  `false`; it is now `off`, `claude-cli`, `anthropic` or `local` (`local`
  is accepted today and honoured from 0.14.0). Older `true` / `false`
  values keep working. The pass lives in a new `vocalize/llm.py`, one seam
  for every language-model call, and the default prompt now also drops
  restatements, false starts and filler
  ([#3](https://github.com/matthager12-collab/vocalize/issues/3)); say
  "verbatim" as the first word of a take, pass `--verbatim`, or set
  `[stt] verbatim = true` to keep every word.
- **Every cloud send is visible.** One stderr line, `vocalize: sent to
  <backend>`, prints immediately before text leaves the machine and never
  otherwise; the clipboard notification for a cleaned take says "cleaned
  up by Claude — sent off this Mac".
- **`claude -p` sessions are tighter.** The cleanup pass and the plan-
  speaking hook now pass `--strict-mcp-config`, run from the system
  temporary directory, and the cleanup pass excludes your own hooks,
  skills and `CLAUDE.md` (`--setting-sources ""`) and strips any stored
  Anthropic key from the child's environment.
- **Provider settings are type-checked** (issue
  [#5](https://github.com/matthager12-collab/vocalize/issues/5)): a
  `voice`, `model`, `engine`, `language`, `region` or `profile` that is not
  a short printable string is refused with a message naming the file and
  the key, in the CLI and the portal alike.
- **Sentences no longer run together with the turbo models.** Their
  segments arrive without a leading space and the worker joined them with
  nothing ("working.I want"); segments are now joined with one space.

### Fixed

- **A stored key is readable from every Python on the Mac.** macOS pins a
  keychain item to the binary that created it, so a key stored from the
  terminal could be invisible, or behind an "Allow" dialog, when Claude
  Code's shell or an upgraded vocalize asked for it. Keys are now written
  and read through Apple's own `security` tool (secret on stdin, never on a
  command line), which is the same accessing application whatever spawned
  it; the item also records the date the key was last validated, shown by
  `vocalize auth status`. An older item is replaced in place on the next
  `vocalize auth login`.

### Added

- **An Anthropic key slot, and a Keys tab that can test and remove.**
  `vocalize auth login --provider anthropic` stores the key the `anthropic`
  cleanup backend uses, under its own keychain item; `auth status`,
  `auth logout` and `vocalize usage` know the slot too. It is a key, not a
  voice: the chain does not accept it. On the portal's Keys tab every slot
  (the three voice providers and Anthropic) gets **Test without storing**,
  which checks a key and keeps nothing, and **Remove stored key**, which
  reads the keychain back before it says the key is gone; each card shows
  when its key was last checked. The key field is `autocomplete=
  "new-password"`, the value Safari and Chrome honour on a password field.
  The Local tab gained a select for the cleanup backend.

- **An Anthropic API backend for the cleanup pass** (`[stt] cleanup =
  "anthropic"`): one Messages API call with a key from `ANTHROPIC_API_KEY`
  or the keychain slot `anthropic-api-key`, under a monthly character
  budget (`[providers.anthropic] monthly_chars`, 2,000,000 when unset)
  counted in the usage ledger. The key is stored from the CLI or the
  portal's Keys tab, as described above.
- **A `[notes]` config table** (`folder`, `template`, `summarizer`,
  `keep_audio`, `model`) is parsed, validated and preserved by every writer
  of the config file. `vocalize notes` itself arrives in 0.14.0.
- **`large-v3-turbo-q8_0`** joins the dictation models: the same turbo model
  as `q5_0` with 8-bit weights (834 MB on disk), for machines with 16 GB or
  more. `vocalize local install --stt --model large-v3-turbo-q8_0`. Pinned
  from a completed download like the other three.

## 0.11.0 - 2026-09-05

### Added

- **A web portal page** (`vocalize portal`) for the whole config surface: reorder
  the provider chain; set each provider's voice, speed and monthly budget with a
  live preview; store and check API keys (masked, `autocomplete="off"`); watch
  usage; and drive the local Kokoro and whisper installs with progress — no more
  hand-editing `config.toml`. Five tabs (Chain, Providers, Keys, Usage, Local)
  plus a persistent readiness sidebar reading `/api/state`, with buttons on the
  rows that map to a vocalize command the portal already runs. Every save goes
  through the same compare-and-swap the CLI uses, so a change made elsewhere
  while the page was open is refused rather than silently overwritten. Ships as
  static HTML and JS with no framework, no inline script and no external
  resource — the whole surface has to work with the network cable pulled — over
  a loopback-only, one-time-code session (DEC-004).
- **A voice picker on the Providers tab.** `GET /api/voices/<name>` returns the
  provider's own `list_voices()`, paginated and cached for the life of the
  portal process, so the field is a real dropdown of the account's actual
  voices with the current value pre-selected, and `"Type a voice id…"` for an
  id the list doesn't carry — rather than a hand-typed guess.

### Fixed

- **`vocalize voices` and `vocalize usage` printed the ElevenLabs API key to
  stderr when the API quoted it back.** The SDK's `ApiError` renders the whole
  response body and header dict, and both commands printed that text verbatim —
  so a key the API named in an error reached terminal scrollback, any session
  log, and whatever the user pasted into a bug report. `auth.scrub` already
  existed for exactly this, but three callers never reached it. The scrub now
  happens inside `tts` itself, below every caller, against the key the client
  was built with. Found by the 0.11.0 release review (DEC-021).
- **The portal's `say` voice list failed on the first ask.** The route gave every
  provider the two-second budget meant for network probes, and enumerating the
  system voices takes longer than that on a current Mac — so the one provider
  needing no key, no account and no network was the one that said "couldn't
  fetch the list". Offline lists get their own budget now.
- **A key of four characters or fewer was shown in full** by `vocalize auth
  status` and on the portal's Keys tab — a truncated paste, or another tool's
  short secret in the same environment variable, reproduced whole in output the
  design treats as safe to screenshot. Anything under eight characters now masks
  to `…` alone.
- **The portal's Keys tab pointed at Keychain Access** to remove a stored key.
  It now names `vocalize auth logout --provider <name>`, which reads the entry
  back and refuses to claim a removal it cannot verify.
- **The ElevenLabs SDK client followed a redirect and re-sent the API key
  wherever it pointed, cross-origin, over plain http.** The SDK's httpx client
  follows a 3xx by default; every ElevenLabs endpoint is a fixed URL, so a
  redirect was never legitimate and the key had no business leaving the
  original origin. Present since 0.9.0, when the ElevenLabs provider first
  shipped. Closed by building the client with `follow_redirects=False` — the
  same rule the urllib-based providers already held via `_http._NoRedirects` —
  and proven on two real loopback HTTP servers: the second origin never sees
  the request.
- **Nothing could rewrite a config file containing an `[stt]` table** —
  which is every config a dictation user has had since 0.10.0 added the
  table. The serialiser wrote flat keys and `[providers.*]` and had no
  case for any other table, so `stt` reached the scalar renderer as a
  `dict` and raised: "The config file has a table under 'stt'. The wizard
  only manages flat keys, so it will not rewrite this file — edit that
  file by hand." That refusal came out of `vocalize wizard` before it
  asked its first question, and out of `vocalize chain google say` instead
  of a write. The only way out was to delete the `[stt]` table, run the
  command, and put it back. `[stt]` is now rendered as its own table, so
  both commands rewrite the file and leave the dictation settings alone.
  The rewritten file puts `[stt]` ahead of `[providers.*]` whatever order
  they were in before — identical TOML, a one-off cosmetic diff.
- **Two writers saving at the same moment could lose one of the two
  changes.** The config write compared the file against the fingerprint it
  had read and then renamed a temp file over it, with the two steps not
  held together, so two saves that overlapped could both see an unchanged
  file, both write, and both report success while only the second survived.
  The compare and the rename now happen under one lock. Two `vocalize`
  *processes* saving in the same instant can still race — the fingerprint
  check narrows that to a window of microseconds, and closing it entirely
  needs a lock file, which is deliberately not what this is.
- **A config save rendered through a fixed `config.toml.tmp`**, so two
  writers at once could render through the same temp file and leave one of
  them writing a file the other had already renamed away — reported as
  "Could not write config file", or worse, reported as success for bytes
  that were not in the file. Each writer now gets its own temp file, made
  with `mkstemp` in the same directory, still `0600`, and still renamed
  into place atomically. The predictable name a local process could
  pre-plant as a symlink is gone with it.

### Known limitation

- **Any other process on the Mac can close the portal with five wrong or
  missing-origin requests to `/api/session`**, no code and no token needed —
  the same guard that stops a cross-origin web page from doing it has no way
  to tell that traffic apart from a local script's. This is availability
  only: nothing is read or changed, the portal just exits. Rerun
  `vocalize portal` and carry on. Accepted rather than fixed, since anything
  able to send that traffic locally could kill the process outright anyway
  (DEC-018).

## 0.10.2 - 2026-09-02

### Added

- **`[stt] cues`** picks what a dictation's feedback sounds — say instead
  of, or alongside, the Tink/Pop/Glass system sounds. `"sounds"` (default)
  is unchanged; `"words"` speaks "Start.", "Stopped.", "Ready." in their
  place; `"both"` speaks the word and then plays the sound. The word files
  ship in `vocalize/assets/cues/`, generated with the local Kokoro voice.
  A spoken "Start." plays *before* the recorder launches — played once the
  microphone was open it would be recorded and transcribed along with the
  dictation. In `"both"` mode the Tink still plays *after* the microphone
  opens, so the two cues keep distinct meanings: the word is "get ready",
  the sound is "talk now". The plain Tink is unaffected.

### Known issue

- In `"words"` mode there is no cue for the moment the microphone actually
  opens, which is a second or so after "Start." finishes (LaunchServices
  start-up plus the input device switching on). People start talking too
  soon and lose their first word. The fix — open and warm the microphone
  first, play the cue, and only then capture — is tracked in
  [#2](https://github.com/matthager12-collab/vocalize/issues/2). Until
  then: in `"words"` mode, wait a beat after "Start."; in `"both"` mode,
  talk after the Tink.

## 0.10.1 - 2026-09-02

Three fixes found in the first owner-present run of 0.10.0's dictation.
Together they meant no hotkey dictation could succeed on 0.10.0; upgrade.

### Fixed

- **No dictation could ever start on a fresh install.** The recorder was
  signed with the hardened runtime but without the
  `com.apple.security.device.audio-input` entitlement, so macOS refused the
  microphone on the spot — no permission dialog, status stuck at
  `notDetermined` — and every first press ended in "The recorder did not
  start". The bundle is now signed with
  `vocalize/recorder/Recorder.entitlements`, and the entitlements are part
  of the recorder's fingerprint, so `vocalize local install --stt` rebuilds
  the bundle once (and, as with any rebuild, macOS asks for the microphone
  again — it never actually asked before).
- **Every hotkey dictation ended in "Dictation failed" on a machine whose
  `uv` came from Homebrew.** A Services environment has a bare PATH, and
  `uv_path()` looked only there and in `~/.local/bin`; the same dictation
  worked from a terminal. `/opt/homebrew/bin/uv` and `/usr/local/bin/uv`
  are now tried too (this also covers Kokoro from a Quick Action).
- **Holding the dictation hotkey down turned into a cancel-and-restart
  loop.** macOS re-fires a Service shortcut at the key-repeat rate, and
  every repeat landed as a second press. Presses within half a second of
  the previous one are now ignored as the same press; a deliberate cancel
  is "press, a beat, press" inside the two-second window, as before.
- `hooks/claude_stop_hook.py --latest`, run from inside a Claude Code turn
  (which is how `/speak` runs it), spoke the agent's own status line —
  "Checking settings." — instead of the response the user asked to hear.
  It now skips the turn in progress, back past the `/speak` message
  itself, and speaks the response before it. From a plain terminal, where
  no turn is in progress, `--latest` still speaks the newest response; the
  hook tells the two apart by the `CLAUDECODE` variable Claude Code sets
  in its shell. The Stop-hook path is unchanged.

## 0.10.0 - 2026-09-02

### Added

- **Local dictation.** Press a hotkey, speak, press it again, and the
  transcript is on your clipboard — speech to text, entirely on-device via
  [whisper.cpp](https://github.com/ggerganov/whisper.cpp)
  (`pywhispercpp`). Nothing about a dictation leaves the machine unless
  `[stt] cleanup` is turned on, and even then only the transcript is sent
  (to `claude -p`, tools denied), never the audio. See
  [docs/dictation.md](docs/dictation.md) for the full guide.
- `vocalize listen` (`--toggle`, `--cancel`, `--check`, `--list-devices`,
  `--wav FILE`, `--cleanup`, `--max-seconds`) and `vocalize dictate` (an
  alias for `listen --toggle`, under the name the hotkey uses).
- New Quick Action, **"Dictate with Vocalize"** — a no-input Service for
  the dictation hotkey (⌃⌥⌘D suggested), installed by the existing
  `hooks/install_quick_action.py` alongside the other three.
- `vocalize local install --stt [--model base.en|small.en|large-v3-turbo-q5_0]`
  and `vocalize local uninstall --stt` — opt-in download-and-verify of a
  whisper.cpp model, plus build-and-sign of **Vocalize Recorder**, the
  small `.app` bundle that holds the microphone permission (macOS only
  grants that to something with an identity). Nothing is downloaded or
  compiled until you run `install --stt`, mirroring Kokoro's opt-in
  install; the one-time Metal shader warm-up (~8s) is paid here, never
  during a dictation.
- New `[stt]` config table — `model`, `language`, `input_device`,
  `cleanup`, `paste` (reserved, not implemented yet), `max_seconds`,
  `sounds` — validated on the way in the same way `[providers.*]` is, and
  printed by `vocalize settings` as `stt.*` lines.
- `vocalize status` — a one-screen readiness check across every provider
  in your chain, plus four dictation rows (`stt model`, `recorder`,
  `microphone`, `input device`) once dictation has been set up at all.
  `--json` prints the same rows as a list; exit 0 when everything is `ok`,
  1 otherwise.
- `vocalize resume [--forget]` — continue (or discard) a text-to-speech
  read that a dictation interrupted. Starting a dictation stops any read
  in progress, but vocalize now remembers exactly where it stopped and
  offers to continue once the transcript has landed (a macOS dialog,
  default Continue, 15s to answer); the record lives at
  `~/.cache/vocalize/interrupted.*`, mode 0600, for at most an hour.
- New [docs/dictation.md](docs/dictation.md): install, the hotkey, every
  `[stt]` key, `vocalize status`'s dictation rows, `resume`, and
  troubleshooting keyed on `vocalize listen --check`'s exact messages and
  exit codes.

### Changed

- `vocalize/local/install.py` generalized to support more than one local
  runtime's manifest and model files (previously hard-coded to Kokoro's).
  Kokoro's own install, stamp, and `local status` output are unchanged
  byte-for-byte; the whisper runtime downloads and stamps only the single
  model you selected, not all three.
- `vocalize listen --check` measures the microphone permission by
  launching Vocalize Recorder the same way a real dictation does
  (through LaunchServices), not by exec'ing its binary directly — macOS
  attributes a TCC grant to the *responsible* process, and exec'ing the
  binary as a child of your shell reported the terminal's own grant
  instead. Exit codes: 0 authorized-and-ready, 2 denied, 3 no usable
  input device, 5 not asked yet (macOS `notDetermined`) — matching the
  recorder's own contract — plus a new exit 1 meaning "vocalize's own
  local install isn't finished" (not built, no model on disk, or the
  recorder never reported back), which is a setup problem, not a
  permission one.
- `audio.stop_playback()` gained a `remember=` flag. A dictation's stop
  passes it, leaving a marker so the process that was playing can record
  where it stopped — this is what makes `vocalize resume` possible. A
  plain `vocalize stop` records nothing, as before.
- **A stop now silences every read already in flight**, not only the
  player it kills. Playback is serialized machine-wide, so stopping one
  read used to let the next queued one start speaking immediately — into
  the microphone a dictation had just opened. A read *started* after the
  stop is unaffected.
- Dictated text reaches the clipboard as a single line. Newlines are
  collapsed there so a paste into a terminal cannot run as several
  commands; `vocalize listen`'s stdout keeps them.
- `vocalize listen --check` now measures the input device configured in
  `[stt] input_device` rather than the system default, and records what it
  saw with a timestamp — so `vocalize status` says how old that
  "authorized" verdict is instead of implying it is current.

### Fixed

- `vocalize local install --stt`, re-run against a model that already
  verified, now re-warms the runtime instead of reporting "already
  installed" and stopping — a machine where only the runtime failed to
  start (no Metal, a build hiccup) previously had no way to retry that
  short of a full uninstall and 465 MB re-download.
- `vocalize local status` reports every installed speech-to-text model,
  not just the default — installing a non-default model with `--model`
  no longer looks unfinished.
- `vocalize local uninstall --stt` no longer crashes on a symlinked model
  directory or recorder bundle; it reports the symlink and leaves it for
  you to remove.
- `vocalize status` no longer raises on an unrecognized `VOCALIZE_CHAIN`;
  like any other misconfiguration, it degrades to one failing row instead
  of crashing the command. A probe that raises is reported by exception
  type only — never its message, which could otherwise echo
  credential-shaped text onto the screen.
- A read stopped by a dictation while a streaming provider's next chunk
  was still rendering (nothing audible playing at that exact instant)
  used to lose the rest of the read with no way to get it back; it's now
  recorded and resumable like any other interruption. The same now holds
  for a read still being synthesized (no player exists yet) and for a
  plain `vocalize stop` landing in that gap.
- The first dictation on a fresh install no longer fails while macOS is
  asking for the microphone. The permission dialog can sit on screen for
  minutes; the press now waits for your answer and starts recording when
  you click Allow, instead of giving up after five seconds and reporting
  a failure that had not happened.
- `vocalize resume` continues the read in the voice, model, speed and
  chunk size it was stopped in. It previously fell back to the config
  defaults, which also missed the audio cache and re-synthesized (and
  re-charged for) the whole remainder.
- A ten-minute dictation is no longer mistaken for a crashed one. The
  claim a stop puts on a take is now aged from its own progress rather
  than from when recording began, so a long take or a stop queued behind
  a long read cannot be reaped mid-transcription.
- `~/.cache/vocalize` and `~/.cache/vocalize/bin` are tightened to 0700
  even when they already existed. The files inside were always 0600, but
  the directory listing said whether a dictation was in progress.

## 0.9.1 - 2026-09-01

### Fixed

- Concurrent invocations no longer talk over each other. Playback is now
  serialized machine-wide on an exclusive file lock
  (`~/.cache/vocalize/play.lock`): a read that arrives while another is
  playing queues and starts the moment the first one ends. Only the audible
  part is serialized — synthesis still runs concurrently — and the lock
  dies with its process, so a killed or timed-out waiter can never leave a
  stale lock behind. Chunked reads hold the slot for the whole sequence, so
  pieces of two reads never interleave. On platforms without `fcntl`
  (Windows), the lock is skipped and the old overlapping behavior remains.

### Changed

- `vocalize stop` semantics with a queue: stopping kills the *current*
  player; the next queued read (if any) then begins. Run `stop` again to
  silence that one too.

## 0.9.0 - 2026-09-01

### Added

- Multi-provider text-to-speech with a fallback chain. Alongside ElevenLabs,
  vocalize can now speak through OpenAI, Google Cloud Text-to-Speech, Amazon
  Polly, macOS `say`, and a new local Kokoro provider — tried in order until
  one succeeds. Default chain when nothing is configured: `elevenlabs, say`.
- `--provider` on `speak`/`speak-file`/`clip` forces a single provider and
  turns fallback off. `vocalize chain` shows the resolved order and its
  source (flag/env/config/default), or writes a new one to `config.toml`
  (`vocalize chain google polly say`) with every other key and table
  preserved.
- A local monthly character budget per cloud provider (`monthly_chars` under
  `[providers.<name>]`), tracked in `~/.cache/vocalize/usage.json`. A
  provider that returns a real quota error from the vendor is remembered as
  exhausted for the rest of the calendar month. `vocalize usage` now reports
  every provider's tally against its budget alongside the existing
  ElevenLabs remote quota.
- Per-provider `vocalize auth login|status|logout --provider <name>` and
  `vocalize voices --provider <name>`.
- `vocalize local install` and `vocalize local status` — opt-in setup for
  Kokoro, an offline local voice. Nothing is downloaded until you run
  `install`: it prints exactly what it will fetch (sizes, source URLs,
  destination), verifies every file against a pinned sha256, and runs the
  model under its own `uv`-managed Python 3.12 so vocalize's own environment
  never changes. `pip install vocalize-cli` pulls in none of it.
- Streaming playback for Kokoro: long text renders in ~400-character pieces
  and starts playing after the first one instead of waiting for the whole
  read to finish. `vocalize stop` works mid-read same as any other provider.
- New optional extra `pip install "vocalize-cli[polly]"` for Amazon Polly
  (boto3, lazy-imported — nothing else pays for it).
- New `docs/provider-credentials.md`: click-by-click setup for OpenAI,
  Google, Polly, and Kokoro.

### Changed

- The default output file is now `~/.cache/vocalize/last.<ext>` (`.mp3`,
  `.m4a`, or `.wav` depending on which provider spoke), not always `.mp3`.
- `vocalize usage` no longer fails outright when no ElevenLabs key is
  configured — it prints "no key configured, skipped" for that section and
  still shows every provider's local budget line and the cache stats.
- Request progress on stderr now names the provider that's speaking
  (`Requesting 340 characters from google...`) instead of always saying
  ElevenLabs, and a fallback that succeeds says so (`Spoke via say
  (fallback).`).
- `vocalize config`'s wizard step labels are now suffixed `(ElevenLabs)` —
  the wizard still only sets up ElevenLabs; use `vocalize chain` or hand-edit
  `config.toml` for the rest of the chain.
- `vocalize settings` gains one additive line: `chain=elevenlabs,say`.

### Security

- Every provider's API key stays out of URLs, argv, logs, and error
  messages — headers, the OS keychain, or environment variables only.
- Kokoro's model downloads are pinned by URL, size, and sha256; a mismatch
  deletes the file and refuses rather than installing anything unverified.
- Text reaches every local worker (`say`, Kokoro) through a file or stdin,
  never as a command-line argument or environment variable.

## 0.8.1 - 2026-09-01

### Fixed

- Truncated speech no longer reads the words "dot dot dot truncated" aloud.
  The `... (truncated)` marker was being appended to the text sent to
  ElevenLabs and spoken; it's gone from the audio now. The CLI still prints
  a "Note: input truncated to N characters." line to stderr.

## 0.8.0 - 2026-08-31

### Added

- The "Speak with Vocalize" and "Speak Latest Plan" Quick Actions now show
  a picker when input is over the cap and overflow is `ask`: speak all,
  three summary depths — light (~25s), medium (~1 min), detailed (~2.5 min)
  — or truncate. Summaries are generated by piping the text through
  `claude -p --model haiku` (tools denied); each is spoken with a hard
  ceiling so an over-long summary can't reintroduce a long read. New
  `hooks/speak_options.py` is the picker/summarizer front-end.
- `hooks/install_quick_action.py` now also resolves and bakes a `claude`
  binary path (plus the PATH additions it needs under a bare Services
  environment) and the helper's location. `claude` is optional — without
  it, the picker just omits the three summary depths. Re-run the installer
  to pick up a newly installed `claude`. Picking a summary has a few
  seconds of silent cold-start before audio begins.

## 0.7.1 - 2026-08-31

### Added

- "Speak Latest Plan" Quick Action — a no-input Service that speaks the
  newest plan under `~/.claude/plans/` on demand (dialog-asks when over
  the cap). For hearing a Claude Code plan proposal before accepting it;
  installed by the same `hooks/install_quick_action.py`.

## 0.7.0 - 2026-08-31

### Added

- `vocalize clip` — speaks the macOS clipboard (pbpaste). Stops any
  current playback first, refuses an empty clipboard, and refuses
  credential-shaped content: a single high-entropy token, or one starting
  with a known secret prefix (sk-, pypi-, ghp\_, op://, eyJ, …), is never
  echoed or sent to ElevenLabs. `--allow-secret` bypasses the guard.
- `--ask-dialog` on speak/speak-file/clip — when overflow is `ask` and no
  terminal is attached, ask via a native macOS dialog (Truncate / Speak
  all / Cancel, 30 s timeout defaulting to Truncate) instead of silently
  truncating. Off by default; the Claude Code Stop hook never uses it.
- macOS Quick Actions: "Speak with Vocalize" (highlight text in any app →
  right-click → Services, or a keyboard shortcut) and "Stop Vocalize".
  Install with `python3 hooks/install_quick_action.py`; the checked-in
  bundles live in `hooks/quick_actions/`.

## 0.6.0 - 2026-08-31

### Added

- `vocalize settings` — prints the resolved settings, one key=value per
  line, so wrapper scripts (like the /speak slash command) can read the
  effective `overflow` and `max_chars` instead of hardcoding them.
- `--print-length` on the Stop hook: prints the response's character count
  instead of speaking, so a wrapper can decide to ask about truncation
  interactively before any audio is spent.

## 0.5.0 - 2026-08-31

### Added

- `vocalize stop` — stops in-progress playback from any terminal. play()
  now records the player's PID in `~/.cache/vocalize/play.pid` while audio
  runs; stop kills it only when the PID, its recorded launch timestamp,
  and a known player name all still match — a recycled PID is never
  touched — and a SIGTERM'd playback counts as a clean exit for the
  speak command that started it. Overlapping plays keep the newest
  record: the survivor is what stop stops.
- Chunked synthesis: input longer than the `eleven_multilingual_v2` model's
  10,000-character per-request cap is now split into chunks — preferring
  paragraph, then sentence, then word boundaries — synthesized sequentially,
  and concatenated into one audio file, instead of failing outright. Each
  chunk still goes through the existing disk cache individually, so a
  partially-cached long document only pays for the chunks it's missing.
- `--chunk-chars` flag to control the split size (default: 9,500).
- Configurable overflow behaviour: a new `overflow` setting (`truncate` |
  `ask` | `never`) decides what happens when input exceeds the character
  cap. `ask` prompts on the controlling terminal and degrades to
  `truncate` with a note when there is none. Resolved like every other
  setting: `--overflow` > `VOCALIZE_OVERFLOW` > config file > `truncate`.
- `max_chars` can now come from the environment (`VOCALIZE_MAX_CHARS`) and
  the config file, not just the `--max-chars` flag.
- `--default-max-chars`: a fallback cap that sits below flag, env, and
  config file — for wrapper scripts that want a protective default
  without overriding the user's own settings.

### Changed

- The Stop hook no longer reads `VOCALIZE_MAX_CHARS` itself; it passes
  `--default-max-chars 500` and lets the CLI resolve the user's real
  settings. Its subprocess timeout now scales with the text length
  (60s base, ~12 chars/s, 900s ceiling) instead of killing any clip
  longer than a minute; on timeout the whole process group is killed,
  so the `afplay` child can't keep playing as an orphan.
- The Stop hook launches `vocalize` in its own session (no controlling
  terminal), so an inherited `overflow = "ask"` degrades to truncate
  there instead of blocking on a prompt nobody sees.

## 0.4.0

### Added

- `vocalize usage` — ElevenLabs quota and local cache at a glance.

## 0.3.0

### Added

- `vocalize auth` command group for storing your ElevenLabs API key in the OS
  keychain (macOS Keychain, Windows Credential Locker, or Linux Secret
  Service) instead of an environment variable or `.env` file.
  - `vocalize auth login` prompts for the key (hidden input), validates it
    against the ElevenLabs API, and stores it. `--stdin` reads the key from a
    pipe instead, for secret managers — e.g.
    `op read op://vault/elevenlabs/key | vocalize auth login --stdin`.
  - `vocalize auth status` shows where the active key is coming from (flag,
    environment, `.env` file, keychain, or not found), with a masked preview.
  - `vocalize auth logout` removes the stored key.
- `vocalize config` now offers to set up your API key when none is found,
  before walking through voice, model, and speed — so setup is install, then
  `vocalize config`, done.

### Changed

- API key resolution order is now: `--api-key` flag, then
  `ELEVENLABS_API_KEY`, then a `.env` file in the current directory, then the
  OS keychain.

## 0.2.1

### Fixed

- The config wizard now paints on the controlling terminal (`/dev/tty`)
  instead of stdout, so it still works under output-capturing wrappers like
  `op run` instead of corrupting their captured output.

## 0.2.0

### Added

- A TOML config file (`~/.config/vocalize/config.toml` or
  `$XDG_CONFIG_HOME/vocalize/config.toml`) and matching environment
  variables for voice, model, and speed, resolved as flag, then env var,
  then config file, then default.
- `--speed` flag and `speed` config/env setting (0.7-1.2).
- `vocalize config`, an interactive wizard that walks through voice (with a
  live preview of the highlighted choice), model, and speed, then writes the
  config file — no need to hand-write TOML.

## 0.1.1

### Added

- `--latest` flag on the Claude Code Stop hook, for speaking your most
  recent response on demand instead of installing an automatic hook.

### Changed

- Fenced code blocks are now spoken as a single short placeholder instead of
  being read out character by character.

### Fixed

- The CLI's reported version now comes from one place instead of two.

## 0.1.0

Initial release.

### Added

- `vocalize speak`, `vocalize speak-file`, and `vocalize voices` commands,
  backed by the ElevenLabs TTS API.
- A markdown-to-speech preprocessing pass: tables, bullet lists, links, and
  code blocks are rewritten into short declarative sentences before
  synthesis.
- A disk cache keyed by a hash of (text, voice, model, format, speed), so
  repeat runs don't re-spend API quota.
- A Claude Code Stop hook (`hooks/claude_stop_hook.py`,
  `hooks/install_hook.py`) that speaks Claude's response after every turn.
- Published to PyPI as `vocalize-cli`; CI running lint and tests with
  coverage on every push.
