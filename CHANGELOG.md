# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
