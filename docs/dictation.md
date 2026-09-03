# Dictation — speech to text, on-device

This is the full guide to vocalize's dictation feature: what it is, how to
install and use it, every `[stt]` config key, and troubleshooting keyed on
the exact messages `vocalize listen --check` prints. The README's
[Dictation](../README.md#dictation-speech-to-text) section is the short
version; this is the long one.

## What it is

Dictation runs the opposite direction from the rest of vocalize: instead of
turning text into speech, it turns your voice into text. Press a hotkey,
speak, press it again, and the transcript lands on your clipboard.

It's built on [whisper.cpp](https://github.com/ggerganov/whisper.cpp) via
the [`pywhispercpp`](https://github.com/absadiki/pywhispercpp) bindings,
running entirely on your Mac. Nothing about a dictation — the audio or the
words — leaves the machine, with one narrow, opt-in exception: turning on
`[stt] cleanup` sends the *transcript* (never the audio) to
`claude -p --model haiku` to fix punctuation and casing. See
[Privacy](#privacy) below for the full accounting.

Two things had to be true for this to work at all, and they shape
everything else in this doc:

- **macOS only grants the microphone to something with an identity.** A
  bare Python script or shell command launched from a Quick Action has
  none, so dictation ships its own small compiled `.app` — **Vocalize
  Recorder** — whose only job is to own that permission.
- **Whisper.cpp needs a model file and a Python runtime vocalize doesn't
  normally carry.** Both are opt-in, downloaded and built only when you run
  `vocalize local install --stt`, exactly like Kokoro's opt-in local voice.

## Installing

```bash
vocalize local install --stt
```

Nothing is downloaded or compiled until you run this. In order, it:

1. **Prints the plan and asks to confirm** (skip with `--yes`): which model,
   its size, its source URL, and where it will land
   (`~/.cache/vocalize/models/whisper/`).
2. **Downloads the model** and verifies it against a pinned sha256 before
   keeping it. A mismatch deletes the file and refuses — nothing
   unverified is ever installed.
3. **Compiles the recorder.** `xcrun swiftc` builds
   `vocalize/recorder/VocalizeRecorder.swift` into a small `.app` bundle
   under `~/.cache/vocalize/bin/`, then ad-hoc signs it with the hardened
   runtime on. This is a few seconds of compile, not a download.
4. **Warms the runtime.** The selftest transcribes a half-second of
   generated tone under `uv run --no-project --python 3.12 --with
   pywhispercpp==1.5.1`, which pays whisper.cpp's one-time Metal shader
   compile (~8 seconds on the reference Mac) here, during install — never
   during an actual dictation.

Model choices, by `--model`:

| Model | Download size | Notes |
|---|---|---|
| `base.en` | ~141 MB | fastest, least accurate |
| `small.en` (default) | ~465 MB | the balance the spike settled on |
| `large-v3-turbo-q5_0` | ~547 MB | most accurate, still fast |

```bash
vocalize local install --stt --model base.en
```

Re-running `local install --stt` (even for a model that's already
installed) always re-warms the runtime and rebuilds the recorder if its
source has changed — it's the in-place repair path for "the model
downloaded fine but the runtime never actually started."

### The first-run microphone prompt

The first time a dictation actually records, macOS shows its standard
permission dialog naming **"Vocalize Recorder."** Click Allow. This is a
one-time prompt per install (see
[Rebuilds and re-granting](#rebuilds-and-re-granting-the-microphone) for
the one case where it comes back).

**That first press waits for you.** Nothing is recording and no microphone
is open while the dialog is up — the recording starts, with its Tink, only
once you click Allow. Take as long as you like: there is a five-minute
ceiling on the wait, after which the press gives up and you simply press
again. Clicking Don't Allow ends the press within a few seconds with "The
recorder did not start."

## The hotkey

```bash
python3 hooks/install_quick_action.py
```

installs (or re-installs) all four Quick Actions, including **"Dictate
with Vocalize"** — a no-input Service, so it never appears in a text
selection's right-click menu, only in the global list. Assign it a
keyboard shortcut:

**System Settings › Keyboard › Keyboard Shortcuts › Services › Text ›
"Dictate with Vocalize."**

⌃⌥⌘D is unclaimed by default on a fresh Mac and is the shortcut this
project was built and tested around, but any unused combination works.

From a terminal, the identical command is `vocalize dictate` (an alias for
`vocalize listen --toggle`) — useful for testing the toggle without
touching System Settings at all.

## Using it

**Press once** to start:

- Any read vocalize was playing stops first, and the process that was
  speaking remembers exactly where (see
  [Interrupted reads and `resume`](#interrupted-reads-and-resume)).
- A Tink plays and the recorder starts listening.

**Speak.**

**Press again** to stop:

- A Pop plays, the recording stops, and the WAV is transcribed on-device.
- If the recording was silence — nothing near a real voice's volume, the
  classic symptom of an unworn Bluetooth microphone — the dictation ends
  quietly: "Nothing heard — nothing was transcribed," no clipboard write.
- If words were heard, a Glass plays and the transcript is copied to the
  clipboard. Nothing is typed or pasted for you; that's a deliberate
  narrowing (`[stt] paste` is reserved for a future release and does
  nothing in 0.10.0).

**A second press within two seconds of the first is a cancel**, not a
stop — treated as "I changed my mind" rather than the end of a very short
sentence. (Presses closer than half a second apart are the key being
*held* — macOS repeats a Service shortcut at the key-repeat rate — and are
ignored, so a cancel is "press, a beat, press".) So is
`vocalize listen --cancel`, from a terminal, at any point
— including while a take is being transcribed. (The transcription that was
already running keeps its own copy of the recording and finishes normally;
`--cancel` only releases the hotkey so your next press starts a fresh
dictation instead of being told "still transcribing.")

**A third press while a take is transcribing is refused**: a Pop, and
"Still transcribing the last dictation." This is deliberate — vocalize
never runs two transcriptions over the same recording, and it never
silently drops one either. Wait for the clipboard notification, or use
`--cancel`.

## `vocalize listen`

The terminal-facing primitive behind the hotkey:

```bash
vocalize listen                     # record until Enter/Ctrl-C, print to stdout
vocalize listen --toggle            # exactly what the hotkey does
vocalize listen --cancel            # discard a dictation in progress
vocalize listen --wav clip.wav      # transcribe a file instead of recording
vocalize listen --check             # microphone + install readiness (see below)
vocalize listen --list-devices      # input device names for [stt] input_device
vocalize listen --cleanup           # tidy the transcript with Claude first
vocalize listen --max-seconds 30    # cap this one recording
```

Plain `vocalize listen` (no flags) records until you press Enter or
Ctrl-C, then prints the transcript to stdout — it composes with a pipe:

```bash
vocalize listen | pbcopy
vocalize listen > note.txt
```

**`--wav FILE`** transcribes an existing recording instead of the
microphone. It's documented, trusted input — you named the file — but the
format is still checked, twice (once here, once inside the whisper
worker): it must be 16 kHz mono 16-bit WAV, exactly what the recorder (and
`say --file-format=WAVE --data-format=LEI16@16000`) produce. A malformed
file gets a plain message instead of a crash:

```
Error: That file is not a readable WAV recording. Speech-to-text needs
16 kHz mono 16-bit WAV.
```

or, for a WAV at the wrong sample rate/channels/bit depth:

```
Error: Speech-to-text needs a 16 kHz mono 16-bit WAV recording. Convert it
with: afconvert -f WAVE -d LEI16@16000 -c 1 <in> <out>
```

`--wav` skips the silence guard and `--cleanup` — it's a literal
transcription primitive, not a stand-in for a live dictation.

**`--cleanup`** applies only to a live recording (`--toggle`/`dictate`, or
plain `listen`). It sends the transcript — never the audio — to
`claude -p --model haiku --disallowedTools '*'` with a 120-second timeout
and a fixed prompt telling the model the text is data to clean, not
instructions to follow (dictated text that happens to read like a command
is still just cleaned, never acted on). A timeout, a non-zero exit, or
empty output falls back to the raw transcript, and the clipboard
notification says so: "Dictation copied to the clipboard (cleanup
skipped)."

**`--max-seconds N`** (1–600) overrides `[stt] max_seconds` for this one
invocation.

## Configuration: the `[stt]` table

```toml
[stt]
model = "small.en"
language = "en"
input_device = ""
cleanup = false
paste = false
max_seconds = 120
sounds = true
```

| Key | Type / allowlist | Default | Notes |
|---|---|---|---|
| `model` | `base.en`, `small.en`, `large-v3-turbo-q5_0` | `small.en` | must already be installed (`vocalize local install --stt --model …`) |
| `language` | a whisper.cpp language code (`en`, `es`, `fr`, `de`, …) | `en` | an `.en` model (both `base.en` and `small.en`) is English-only regardless of this setting — pairing one with a non-`en` language is a `ConfigError` |
| `input_device` | `""` (system default) or an exact name from `vocalize listen --list-devices`; ≤ 128 characters, printable only, can't start with `-` | `""` | see [The input-device gotcha](#the-input-device-gotcha) |
| `cleanup` | `true` / `false` | `false` | see `--cleanup` above |
| `paste` | reserved | `false` | not implemented in 0.10.0 — setting it does nothing |
| `max_seconds` | integer, 1–600 | `120` | the recorder self-stops here; `dictate` backstops it a few seconds later in case the recorder doesn't |
| `sounds` | `true` / `false` | `true` | the Tink/Pop/Glass feedback; `false` silences all three |

Every value here eventually becomes a subprocess argument — the recorder's
`--device`, or the whisper worker's `--model`/`--language` — so each one is
checked against its allowlist or shape before that can happen, on the way
into the config file and again every time it's read. An unrecognized key
in `[stt]` warns on stderr; a bad value for a known key is a `ConfigError`
naming the file, the key, and what was expected.

`vocalize settings` prints the resolved dictation settings alongside
everything else:

```
stt.model=small.en
stt.language=en
stt.cleanup=false
stt.max_seconds=120
```

### The input-device gotcha

The spike this feature was built from found the reference Mac's *default*
input device was a pair of Bluetooth earbuds that were paired but not
actually being worn — delivering perfect digital silence. If dictation
keeps saying "Nothing heard" and you're confident you spoke, this is almost
certainly why.

```bash
vocalize listen --list-devices
```

prints every input device macOS can see, one per line. Copy the exact name
of the one you actually want and set it:

```toml
[stt]
input_device = "MacBook Pro Microphone"
```

An empty string always means "whatever macOS's system default is right
now" — leave it that way once you've fixed the actual default input in
System Settings, or pin a specific device here if you switch between
several.

## `vocalize status`

```bash
vocalize status
vocalize status --json
```

reports readiness across every provider in your chain, plus four dictation
rows — but only once dictation has been set up at all: an `[stt]` table
present in your config file, a built recorder, or a model on disk. A
machine that never opted into dictation doesn't get four permanent
failures for a feature nobody asked for.

| Row | `ok` means | `warn`/`fail` means |
|---|---|---|
| `stt model` | at least one whisper.cpp model verifies on disk | none installed — `vocalize local install --stt` |
| `recorder` | Vocalize Recorder is built | not built — same fix |
| `microphone` | the last `listen --check` saw `authorized` | `denied` (fail, fix in System Settings), not yet asked (warn), or unknown (warn) |
| `input device` | the configured (or default) device is actually present | the configured device isn't connected, or macOS reports no input device at all |

The microphone row is read from a small cache file
(`~/.cache/vocalize/mic.status`, see
[The mic.status cache](#the-micstatus-cache) below) rather than by
launching the recorder itself — `status` (and, in a later release, the
config portal polling it) never opens an app or prompts for permission on
its own.

Exit code is 0 when every row — providers and dictation both — is `ok`,
and 1 otherwise, so it composes with `&&` the same way `vocalize usage`'s
sibling commands do.

## Interrupted reads and `resume`

Starting a dictation always stops whatever vocalize was reading aloud
first — you can't record over your own voice being played back — but it
doesn't throw the rest of that read away (DEC-003 in the project's design
history). The process that was speaking is the only one that knows exactly
what it was playing and how far in it got, so it writes that down before
it exits.

Once your dictation's transcript has landed, vocalize shows a dialog:

> **Continue the read you interrupted?** [Discard] [Continue]

Default button is Continue; the dialog gives up after 15 seconds, which
counts as Discard. From a terminal (or if you missed the dialog), the same
thing is:

```bash
vocalize resume            # play the saved piece, then read the rest
vocalize resume --forget   # discard the interrupted read
vocalize resume            # "Nothing to resume." if there's nothing saved
```

### What's stored, and where

Three files under `~/.cache/vocalize/`, all mode 0600, written and deleted
together:

| File | Contents |
|---|---|
| `interrupted.<ext>` | one piece of audio — the chunk that was playing when the dictation started, or the whole file for a provider that doesn't stream |
| `interrupted.txt` | the text after that piece — everything not yet spoken |
| `interrupted.json` | `version`, `saved_at`, `provider`, `ext`, `offset_seconds`, `remaining_chars` |

`interrupted.txt` is genuinely new plaintext on disk — the audio cache next
to it holds rendered audio, not the words themselves, so this is the first
place in vocalize a read's text is written out in the clear. What protects
it: the 0600 mode, a symlink-proof open on every read and write, exactly
one record at a time (starting a new one replaces the old), deletion the
moment you resume, decline, or `--forget` it, and an automatic expiry after
**one hour** even if you never touch it. Nothing beyond that — a backup job
that runs inside that hour will see it. No dictation audio and no
dictation transcript is ever written into this record (see
[Privacy](#privacy)); it only ever holds a *TTS read* your dictation
interrupted.

Resuming plays the saved piece from where it was cut off (converting to
WAV first if needed, then slicing by sample), then continues through the
rest of the text via the normal chain, same provider, same cache — so
anything already rendered is a cache hit and the continuation starts
immediately.

## Uninstalling

```bash
vocalize local uninstall --stt
```

Removes the model file(s) under `~/.cache/vocalize/models/whisper/` and
the recorder bundle under `~/.cache/vocalize/bin/`. It asks first (skip
with `--yes`) and does nothing if there's nothing to remove. **The
microphone permission grant is not touched** — it lives in System
Settings, not in anything this command can see, so remove it yourself
under Privacy & Security › Microphone if you want it gone too.

## Privacy

- **vocalize never stores a transcript.** It exists only in memory
  between the moment it's transcribed and the moment it reaches your
  clipboard (or stdout, for `vocalize listen`). No file vocalize writes
  holds it, and it never appears in a notification — every notification
  dictation can show is one of a small set of fixed strings baked into the
  code; a transcript literally cannot reach one. The one exception is
  `--cleanup`, below.
- **Recorded audio lives only in a private (0700) temporary directory**,
  deleted the moment a dictation ends, on every exit path — success,
  cancel, or failure. A sweep on the next `listen`/`dictate` clears
  anything a hard kill (`kill -9`, a lost-power crash) left behind after
  24 hours.
- **`--cleanup` is the one opt-in exception**, and it sends text only: the
  transcript goes to `claude -p` with every tool denied by a wildcard and
  no MCP server started, so nothing a dictated sentence could ask for has
  anything to act with. It runs from the system temporary directory, so
  the session never adopts whatever project you happen to be standing in.
  The audio is never part of that call.
- **`--cleanup` also writes the transcript to Claude Code's own session
  log**, in full and in plaintext, under
  `~/.claude/projects/<slug>/<uuid>.jsonl` — Claude Code records the
  prompt and stdin of every print-mode run, and vocalize cannot turn that
  off (pointing `CLAUDE_CONFIG_DIR` elsewhere moves the log but loses the
  login). This is why `[stt] cleanup` is off by default: turning it on
  means accepting that your dictated text is on disk in Claude Code's
  history and goes to Anthropic. Everything above about vocalize's own
  files still holds; this file is not one of them.
- **The microphone grant belongs to the bundle, not to vocalize.** Once
  you allow "Vocalize Recorder", anything running as you can launch
  `~/.cache/vocalize/bin/Vocalize Recorder.app` with its own `--out` and
  `--max` and record under that grant, with no prompt of its own and no
  vocalize process involved — that reusable grant is the whole point of a
  bundle owning the permission. No code can prevent it. To withdraw it:
  `vocalize local uninstall --stt`, then remove "Vocalize Recorder" under
  System Settings › Privacy & Security › Microphone.
- **Text on the clipboard arrives as a single line.** Newlines are
  collapsed to spaces there, so a transcript can never paste into a
  terminal as several lines that run as they arrive. `vocalize listen`'s
  stdout keeps them.
- **The interrupted-read record** (above) is a separate thing entirely —
  it's the text and audio of a TTS read your dictation cut off, never your
  own voice or its transcript.

## Troubleshooting

### `vocalize listen --check`

The command to run whenever dictation isn't behaving. It reports what's
installed, what the microphone permission actually is, and which input
device will be used — then exits with a code a script (or you) can act on.

```bash
vocalize listen --check
```

prints up to four lines:

```
Model: small.en
Recorder: ~/.cache/vocalize/bin/Vocalize Recorder.app
Input device: MacBook Pro Microphone
Microphone: authorized — Speech-to-text is ready.
```

(The exact wording of the last line depends on what it found — see the
exit-code table below.)

### Exit codes

| Exit | Meaning | What the message says to do |
|---|---|---|
| **0** | Authorized, and a model is installed — ready to dictate | nothing; "Speech-to-text is ready." |
| **1** | vocalize's own install is unfinished, in one of several ways: the recorder isn't built, no model is installed, the microphone is authorized but there's nothing to transcribe with, or the recorder didn't report back at all | run `vocalize local install --stt` |
| **2** | Microphone access denied | 'Allow "Vocalize Recorder" in System Settings › Privacy & Security › Microphone, then run this again.' |
| **3** | No usable input device | 'Connect or select a microphone — `vocalize listen --list-devices` shows what macOS can see.' |
| **5** | macOS hasn't asked for the permission yet (`notDetermined`) | 'The first dictation prompts for "Vocalize Recorder"; answer Allow, then run this again.' |

(There's no exit 4 for `--check` — that code means "recorder hit
`max_seconds` mid-recording," which can't happen during a permission
check.)

**Why `--check` specifically launches the recorder through
LaunchServices** (`open -W -n -a`, not a direct exec) rather than just
running its binary: macOS attributes a microphone permission to the
*responsible* process, and on the reference Mac the very same binary
reported `authorized` when exec'd directly from a shell (because the
terminal itself had a grant) and `notDetermined` when launched the way a
real dictation launches it. Exec'ing the binary would answer a question
nobody asked — "does my terminal have microphone access?" — instead of
"does Vocalize Recorder?" `--list-devices` has no such issue (enumerating
devices needs no permission at all) and stays a direct exec.

### The `mic.status` cache

`vocalize listen --check` is the only thing that ever launches the
recorder to ask about permission. It writes what it learned to
`~/.cache/vocalize/mic.status` — one word, one of `authorized`, `denied`,
`notDetermined`, `unknown`, or `incomplete` — mode 0600. `vocalize status`
reads this file for its `microphone` row instead of launching anything
itself, so a status check (or a script polling it in a loop) never opens
an app or triggers a permission prompt on its own.

This means **`vocalize status`'s microphone row can be stale.** If you've
just granted (or revoked) the permission in System Settings, run
`vocalize listen --check` once to refresh the cache before trusting
`status`'s answer.

### The dictation session file

`~/.cache/vocalize/dictate.session` is what makes the hotkey's toggle
atomic: the first press creates it (`O_CREAT|O_EXCL`, so exactly one press
can ever win the race), and it's deleted when that dictation ends, however
it ends. If you ever see the hotkey behave as though a dictation never
properly started or stopped — most likely after a hard crash or a forced
shutdown mid-recording — `vocalize listen --cancel` clears it
unconditionally and is always safe to run.

### The hotkey does nothing

In rough order of likelihood:

1. **No shortcut is assigned yet**, or it's assigned to something else.
   Check System Settings › Keyboard › Keyboard Shortcuts › Services ›
   Text › "Dictate with Vocalize" — if it isn't listed at all, re-run
   `python3 hooks/install_quick_action.py` and look again (Services
   sometimes needs the registry refresh the installer triggers).
2. **The Quick Action was installed before `vocalize`, `claude`, or a
   helper script moved** — the installer bakes absolute paths in at
   install time. Re-run the installer.
3. **Speech-to-text was never installed.** `vocalize listen --check` will
   say so plainly (exit 1).
4. **You're in an app that doesn't expose the Services menu at all**
   (some Electron apps). This affects only whether you can see the
   *menu* entry to confirm it's there — a global keyboard shortcut still
   fires regardless of which app is focused, so this is rarely the actual
   cause for a no-input Service like this one.

`vocalize dictate` from a plain terminal isolates whether the problem is
the hotkey/Service layer or dictation itself — if that works, the Quick
Action installation is where to look next.

### Rebuilds and re-granting the microphone

The microphone permission is tied to Vocalize Recorder's ad-hoc code
signature, which is derived from its compiled binary. `local install --stt`
only rebuilds the bundle when its Swift source has actually changed (most
vocalize upgrades touch nothing under `vocalize/recorder/` and leave the
existing bundle alone). When a rebuild does happen, the signature changes,
and the install prints:

```
Vocalize Recorder was rebuilt — re-grant the microphone in
System Settings › Privacy & Security › Microphone
```

Take it literally: open that pane, and if "Vocalize Recorder" is still
listed but toggled off, or missing entirely, either flip it on or just run
a dictation and answer the fresh prompt. This is not a bug to work around —
it's the same TCC mechanism that protects every other app's microphone
grant from being silently inherited by a different piece of code wearing
the old app's name.
