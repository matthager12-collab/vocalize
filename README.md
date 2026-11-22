# Vocalize

Vocalize is a command-line tool for macOS. It does two things, both on your
Mac:

- **Text to speech.** It reads text, Markdown files, the clipboard or piped
  input aloud.
- **Speech to text.** It turns what you say into text on the clipboard
  (dictation), and turns audio files into Markdown transcripts.

The models run on your Mac. Vocalize has no cloud engine, no account and no
key. See [What local only means](#what-local-only-means).

New to the command line? [docs/getting-started.md](docs/getting-started.md)
walks through the whole setup one step at a time.

## What you need

- **A Mac.** Vocalize was built and measured on macOS with Apple Silicon.
  Parakeet (speech to text) and the cleanup model need Apple Silicon. The
  other engines were not tested on an Intel Mac.
- **[uv](https://docs.astral.sh/uv/).** Vocalize uses it to install each
  engine into its own environment. The first engine install also has uv
  fetch a copy of Python 3.12 (about 70 MB).
- **Apple's command line tools** (`xcode-select --install`). Dictation and
  the menu-bar app each build a small Swift app on your Mac.

## Install

```bash
uv tool install vocalize-cli
vocalize local install      # download the Kokoro voice (337 MB)
vocalize doctor             # check what is ready and what is missing
```

`vocalize local install` asks before it downloads anything. Full detail is
in [docs/installation.md](docs/installation.md).

**Upgrading from 0.15?** Version 1.0 removes cloud voices, account keys,
usage budgets, summaries and note templates. If you need those, stay on
0.15: `uv tool install 'vocalize-cli==0.15.0'`. An old config file still
loads: each setting that no longer exists prints a notice and is ignored,
and the next save removes it.

After upgrading, install every engine you use once more. 1.0.0 runs each
engine from its own hash-locked environment, and an install from 0.15 does
not have one. Until you do, speech falls back to the macOS voice and
dictation reports that it is not ready.

```bash
vocalize local install                        # Kokoro
vocalize local install --stt                  # dictation: Parakeet on Apple Silicon (2.3 GB download)
vocalize local install --stt --engine whisper # or keep Whisper instead
vocalize local install --llm                  # only if you use cleanup
vocalize integrate claude --yes               # replace the old /speak skill
```

Model files already on disk are checked and not downloaded again. The old
`/speak` skill file still describes web and summary modes that Vocalize no
longer has, so replace it once with `vocalize integrate claude --yes`. See
[CHANGELOG.md](CHANGELOG.md).

### Downloads and memory

Each engine is a separate download. Sizes are what the installer reports.
Memory is the largest resident memory seen while the engine worked, measured
on one Mac Studio (Apple M5 Max, 128 GB, macOS 27.2). For Parakeet and the
cleanup model that figure may undercount, because memory they hold for the
graphics processor is not always counted. Details are in
[docs/plans/2026-11-local-only/measurements.md](docs/plans/2026-11-local-only/measurements.md).

| Engine | Install command | Model download | Engine environment | Memory while working |
|---|---|---|---|---|
| Kokoro (voice) | `vocalize local install` | 337 MB | 145 MB | about 1.5 GB |
| Piper (voice) | `vocalize local install --piper` | about 60 MB per voice | not measured | not measured |
| Whisper large-v3-turbo-q5_0 | `vocalize local install --stt --engine whisper` | 547 MB | 41 MB | about 0.87 GB |
| Whisper large-v3-turbo-q8_0 | the same, plus `--model large-v3-turbo-q8_0` | 834 MB | shared with q5_0 | about 1.2 GB |
| Parakeet | `vocalize local install --stt` | 2.3 GB | 450 MB | 1.2 to 1.6 GB |
| Cleanup model (Qwen3.5-4B, 4-bit) | `vocalize local install --llm` | 2.8 GB | 371 MB | about 3 GB |

The cleanup install refuses on a Mac with less than 12 GB of memory unless
you add `--force`. uv also keeps a download cache of its own; in the
measurements it grew by a few hundred MB per engine.

## Text to speech

```bash
vocalize speak "Hello, this is a test."
vocalize speak-file report.md        # a Markdown or text file
cat notes.md | vocalize speak-file - # piped input
vocalize clip                        # whatever is on the clipboard
vocalize pause                       # pause what is playing
vocalize resume                      # continue a paused or interrupted read
vocalize stop                        # stop what is playing
```

### Speech engines

| Engine | How to get it | Role |
|---|---|---|
| Kokoro | `vocalize local install` | The default voice |
| Piper | `vocalize local install --piper` | Optional. Its licence is shown before the download |
| macOS `say` | Built in | The fallback. Nothing to download |

Vocalize tries the engines in order. The default order is Kokoro, then
`say`. If an engine fails before playback starts, the next one is tried and
Vocalize tells you which engine spoke.

```bash
vocalize chain                     # show the current order
vocalize chain kokoro piper say    # set a new order
vocalize voices                    # list Kokoro voices
vocalize voices --provider piper   # list Piper voices
vocalize speak "Hi" --provider say # use one engine, with no fallback
```

On the Mac Studio, Kokoro took about 0.11 to 0.13 seconds to make each
second of speech. Vocalize makes the first piece of a text short, so it can
start playing sooner. With the model loaded, the first audio of three test
passages was ready in 0.24 to 0.63 s.

### Markdown and tables

Before speaking, Vocalize rewrites Markdown into plain sentences. A table is
announced with its size and column names, each row is read as its own
numbered paragraph ("Row 1: Plan A. Cost: $5."), and "End of table." marks
where it stops. Bullets become "First, ... Second, ...", links keep their
text and drop the address, and code blocks are replaced with a short spoken
note. Use `--raw` to skip this and speak the text exactly as written.

## Speech to text

Two engines are available:

- **Parakeet** runs on Apple Silicon and is the default there. English only.
- **Whisper** runs on any Mac, handles other languages, and also takes the
  vocabulary list as a hint.

`[stt] engine` chooses: `auto` (the default) uses Parakeet when it is
installed and Whisper otherwise.

### Dictation

```bash
vocalize local install --stt   # Parakeet on Apple Silicon; add --engine whisper for Whisper
vocalize dictate               # run once to record, again to stop
```

When you stop, the text is copied to the clipboard. Set `[stt] paste = true`
to also paste it into the app you were using.

The time from the end of a live recording to text on the clipboard has not
been measured. What was measured is transcribing a saved file on the Mac
Studio: Parakeet took 0.6 to 0.7 s for an 18 to 23 second recording, and
Whisper took 1.1 to 1.2 s for a 45 to 55 second one.

**Optional cleanup.** An on-device language model can remove filler words
and fix punctuation before the text is copied:

```bash
vocalize local install --llm   # 2.8 GB; needs 12 GB of memory
vocalize dictate --cleanup     # or set [stt] cleanup = "local"
```

If the cleaned text drifts from what you said, Vocalize keeps your original
words. Cleanup is off by default. One cleanup took about 1.9 s on the Mac
Studio. The full guide is [docs/dictation.md](docs/dictation.md).

### Transcribe audio files

```bash
vocalize transcribe recording.m4a
vocalize transcribe ~/Recordings     # every supported file in a folder
```

Each file becomes a Markdown file with timestamped lines, written to your
transcripts folder. Files already transcribed are skipped. With
`[stt] engine = "parakeet"` files are transcribed by Parakeet. With
`whisper` they go to Whisper, and with `auto` to Whisper when a Whisper
model is installed, otherwise to Parakeet. On the Mac Studio, Parakeet
transcribed a 10 minute file in 7.4 s (word error rate 2.0 percent) and a
60 minute file in 27.6 s (1.6 percent); Whisper took 19.4 s for the same
10 minute file (1.5 percent). See [docs/transcribe.md](docs/transcribe.md).

### Vocabulary

Speech engines often misspell names and technical words. List them once and
Vocalize puts them right:

```toml
[stt]
vocabulary = ["pyproject", "sha256", "Kokoro", "DuckDB"]
```

After either engine, Vocalize looks for one to four heard words that spell
a term's letters and digits, and replaces them with the term as you wrote
it ("py project" becomes "pyproject", "duck db" becomes "DuckDB"). This
correction step leaves text with no term in it unchanged. There is no
matching by sound, so a term heard as a different word is not corrected,
and a term shorter than three letters or digits is never matched. This
applies to dictation and to `vocalize transcribe`. The list holds up to 50
terms.

Whisper also gets the list as a hint before it starts, and the hint can
change the transcript, even of text with no term in it. On the Mac Studio,
a technical take's word error rate fell from 7.7 to 1.5 percent with the
list, on both engines. On two plain prose takes, the hint made Whisper's
word error rate go from 2.54 to 3.94 percent on one and from 0.84 to 0.28
percent on the other. Parakeet takes no hint.

## Machine profiles

A machine profile is a ready-made set of five settings sized for how much
memory your Mac has. The settings page suggests a profile for your Mac and
applies one with a button. Applying a profile changes only these five
settings. It installs nothing; it names the installs it recommends.

| Profile | Memory | `stt.engine` | `stt.model` (Whisper) | `stt.beam_size` | `stt.warm_minutes` | `stt.cleanup` | Recommended installs |
|---|---|---|---|---|---|---|---|
| small | under 16 GB | `auto` | `large-v3-turbo-q5_0` | 5 | 0 | `off` | Kokoro, Whisper |
| standard | 16 GB and up | `parakeet` | `large-v3-turbo-q5_0` | 5 | 0 | `off` | Kokoro, Parakeet, Whisper |
| large | 32 GB and up | `parakeet` | `large-v3-turbo-q8_0` | 5 | 0 | `off` | Kokoro, Parakeet, Whisper |
| studio | 64 GB and up | `parakeet` | `large-v3-turbo-q8_0` | 5 | 240 | `local` | Kokoro, Parakeet, cleanup model; the `large-v3-turbo-q8_0` Whisper model for use if you switch the engine to Whisper |

`small` holds the package defaults. The values are provisional: engines
were measured only on the 128 GB Mac Studio, never on a smaller Mac.
`stt.warm_minutes` keeps Whisper and the cleanup model loaded between takes;
Parakeet starts fresh for each take. So with Parakeet and cleanup off there
is nothing to keep loaded, and standard and large set 0; studio's 240 keeps
the cleanup model loaded. `vocalize local stop` unloads whatever is loaded.
The Whisper model and beam settings matter only when Whisper is the engine
in use.

## Settings

```bash
vocalize portal     # open the settings page in your browser
vocalize settings   # print the current settings, one per line
```

The settings page runs on your Mac at `127.0.0.1`. It opens with a one-time
link and closes after 15 minutes with nothing to do. It is a single page: a
machine profile box, then the sections Speaking, Reading, Dictation,
Transcription, Hotkeys, Engines and Advanced. Every setting below has a
control there, and every install, removal and integration has a button.

Every setting is stored in one file, `~/.config/vocalize/config.toml`,
which you can also edit by hand. A save from Vocalize does not keep
comments or retired settings, so the first save over a file that has them
keeps one backup copy, `config.toml.bak`. That copy holds everything the
old file held, which can include a service key from an older version.
Delete it when you no longer need it. A table Vocalize does not know does
not block a save.

The tables below are generated from the same list of settings the page is
built from, and a test fails if they differ.

<!-- settings-table:start -->
### Speaking

Which voice engines read text aloud, and how they sound.

| Key | Default | Allowed | What it does |
|---|---|---|---|
| `chain` | `["kokoro", "say"]` | `"kokoro"`, `"piper"`, `"say"` | The engines tried in turn to speak text; the next one is used when one is not installed or fails. |
| `providers.kokoro.voice` | `"af_heart"` | up to 128 characters | The voice Kokoro speaks with. Unset uses the engine's own default. |
| `providers.kokoro.speed` | unset | 0.7 to 1.2 | How fast Kokoro speaks; 1.0 is normal speed. Unset uses the engine's own pace. |
| `providers.piper.voice` | `"lessac"` | up to 128 characters | The voice Piper speaks with. Unset uses the engine's own default. |
| `providers.piper.speed` | unset | 0.7 to 1.2 | How fast Piper speaks; 1.0 is normal speed. Unset uses the engine's own pace. |
| `providers.say.voice` | unset | up to 128 characters | The voice macOS say speaks with. Unset uses the engine's own default. |
| `providers.say.speed` | unset | 0.7 to 1.2 | How fast macOS say speaks; 1.0 is normal speed. Unset uses the engine's own pace. |

### Reading

How long text is handled and how written text is turned into speech.

| Key | Default | Allowed | What it does |
|---|---|---|---|
| `max_chars` | unset | at least 1 | The most characters read in one go. Unset means no limit. |
| `overflow` | `"truncate"` | `"truncate"`, `"ask"`, `"never"` | What happens when text is longer than the longest read: cut it, ask first, or refuse it. |
| `speech.headings` | `"cue"` | `"cue"`, `"plain"`, `"off"` | How headings are read: with a spoken cue, as plain text, or skipped. |
| `speech.blockquotes` | `"cue"` | `"cue"`, `"plain"`, `"off"` | How quoted blocks are read: with a spoken cue, as plain text, or skipped. |
| `speech.nested_lists` | `"cue"` | `"cue"`, `"flat"`, `"off"` | How lists inside lists are read: with a cue, flattened, or skipped. |
| `speech.parentheticals` | `"pause"` | `"pause"`, `"drop"`, `"off"` | How text in brackets is read: with a pause around it, dropped, or read as written. |
| `speech.abbreviations` | `"expand"` | `"expand"`, `"protect"`, `"off"` | Whether abbreviations such as e.g. are expanded into words, kept whole, or left alone. |
| `speech.citations` | `"drop"` | `"drop"`, `"speak"`, `"off"` | Whether citation and footnote marks are dropped, spoken, or left alone. |
| `speech.references` | `true` | `true`, `false` | Skip a references or bibliography section at the end of a document. |
| `speech.furniture` | `true` | `true`, `false` | Skip running heads, footers and page numbers that repeat through a document. |
| `speech.urls` | `"domain"` | `"domain"`, `"full"`, `"drop"` | How a web address is read: just its site name, in full, or not at all. |
| `speech.emoji` | `true` | `true`, `false` | Remove emoji before reading, so they are not spoken. |

### Dictation

What happens when you dictate: cues, cleanup, pasting and recording limits.

| Key | Default | Allowed | What it does |
|---|---|---|---|
| `stt.paste` | `false` | `true`, `false` | Paste the transcript into the app you were using when dictation ends. |
| `stt.cues` | `"sounds"` | `"sounds"`, `"words"`, `"both"` | How dictation tells you it started and stopped: sounds, spoken words, or both. |
| `stt.sounds` | `true` | `true`, `false` | Turn the start and stop cues on or off. |
| `stt.verbatim` | `false` | `true`, `false` | Keep filler words and false starts instead of tidying the transcript. |
| `stt.cleanup` | `"off"` | `"off"`, `"local"` | Tidy a transcript with a local language model on this Mac, or leave it as heard. |
| `stt.vocabulary` | `[]` | up to 50 items; up to 40 characters | Names and terms to spell right. They are corrected in the transcript after either engine, and Whisper also gets them as a hint. |
| `stt.max_seconds` | `120` | 1 to 600 | Recording stops by itself after this many seconds. |
| `stt.max_take_seconds` | `1800` | 60 to 7200 | The most seconds one dictation may last in total, counting every pause and resume. |
| `stt.input_device` | `""` | up to 128 characters | The microphone to record from. Empty uses the system default. |

### Transcription

Where `vocalize transcribe` writes its transcripts and what it keeps.

| Key | Default | Allowed | What it does |
|---|---|---|---|
| `transcribe.folder` | `"~/Documents/Vocalize Notes"` | up to 512 characters | The folder `vocalize transcribe` writes its transcripts to. |
| `transcribe.keep_audio` | `false` | `true`, `false` | Keep a copy of each recording next to its transcript. |
| `transcribe.model` | `""` | `""`, `"base.en"`, `"small.en"`, `"large-v3-turbo-q5_0"`, `"large-v3-turbo-q8_0"` | The Whisper model for transcribing files. Empty uses the dictation model. |

### Hotkeys

The menu-bar app's keyboard shortcuts.

| Key | Default | Allowed | What it does |
|---|---|---|---|
| `app.dictate` | `"ctrl+alt+cmd+d"` | a chord such as `ctrl+alt+cmd+d`, or empty | Starts and stops dictation. Must include ctrl or cmd; empty turns it off. |
| `app.dictate_mode` | `"toggle"` | `"toggle"`, `"hold"` | Press once to start and again to stop, or hold the shortcut while you speak. |
| `app.speak` | `"ctrl+alt+cmd+s"` | a chord such as `ctrl+alt+cmd+d`, or empty | Reads the selected text aloud. Must include ctrl or cmd; empty turns it off. |
| `app.stop` | `"ctrl+alt+cmd+x"` | a chord such as `ctrl+alt+cmd+d`, or empty | Stops reading. Must include ctrl or cmd; empty turns it off. |
| `app.stop_hotkey` | `"stop"` | `"stop"`, `"pause"` | Stop reading for good, or pause it so it can be resumed. |

### Engines

Which speech-to-text engine and model run on this Mac.

| Key | Default | Allowed | What it does |
|---|---|---|---|
| `stt.engine` | `"auto"` | `"auto"`, `"parakeet"`, `"whisper"` | Which speech-to-text engine runs. Auto uses Parakeet on Apple silicon where it is installed, and Whisper everywhere else. |
| `stt.model` | `"large-v3-turbo-q5_0"` | `"base.en"`, `"small.en"`, `"large-v3-turbo-q5_0"`, `"large-v3-turbo-q8_0"` | The Whisper model used for dictation. Larger models are more accurate and slower. |
| `stt.language` | `"en"` | one of 101 names | The language you dictate in, or auto to detect it. English-only models need en. |
| `stt.warm_minutes` | `0` | 0 to 240 | Minutes Whisper and the cleanup model stay loaded after a take, so the next one starts at once. Parakeet is never kept loaded: with Parakeet this keeps only the cleanup model, when cleanup is on. 0 keeps nothing loaded. |

### Advanced

Fine tuning most people never need to change.

| Key | Default | Allowed | What it does |
|---|---|---|---|
| `providers.kokoro.language` | `"en-us"` | up to 128 characters | The language code Kokoro is asked to speak. Unset uses the engine's own default. |
| `stt.beam_size` | `5` | 1 to 8 | How many guesses Whisper weighs at once. Higher can be more accurate and is slower; 1 is the fastest. |
| `speech.bracket_max_chars` | `120` | 10 to 1000 | Brackets longer than this many characters are read as written. |
| `speech.furniture_max_chars` | `60` | 10 to 500 | Only lines up to this many characters can count as page furniture. |
| `speech.furniture_min_repeats` | `3` | 2 to 20 | A line must repeat at least this many times to be skipped as page furniture. |
<!-- settings-table:end -->

### Choices with no control on the settings page

A few choices can only be made in a shell. Each is meant for one command,
for testing, or for a step that should not happen by accident.

| Choice | How to make it | Why there is no control |
|---|---|---|
| Run Whisper or the cleanup model under another Python, outside its hash-locked environment | `VOCALIZE_WHISPER_PYTHON`, `VOCALIZE_LLM_PYTHON` | For evaluation only; such a runtime is checked by version only |
| Override a setting for one command | `VOCALIZE_CHAIN`, `VOCALIZE_VOICE`, `VOCALIZE_SPEED`, `VOCALIZE_OVERFLOW`, `VOCALIZE_MAX_CHARS` | They last for one command; the file keeps its value, and the settings page shows when one is set |
| Size of each piece sent to a voice engine | `--chunk-chars` on `speak`, `speak-file` and `clip` (default 9500) | A per-command tuning option |
| The Claude Code hook's own length cap (500 characters) | a constant in the hook script | It applies only when `max_chars` is unset, so the `max_chars` setting overrides it |
| Install the cleanup model on a Mac with less than 12 GB | `vocalize local install --llm --force` | The memory check protects small Macs |
| Replace an existing `/speak` skill | `vocalize integrate claude --yes` | The button never overwrites a skill file you may have edited |

## The menu-bar app

```bash
vocalize app install
```

This builds **Vocalize.app**, a menu-bar app that owns three keyboard
shortcuts that work in any app:

| Shortcut | Action |
|---|---|
| Control-Option-Command-D | Start or stop dictation |
| Control-Option-Command-S | Speak the selected text |
| Control-Option-Command-X | Stop speaking |

macOS asks for two permissions, and only you can grant them:

1. **Accessibility for Vocalize.app.** Without it, the shortcuts do nothing.
   Turn it on in System Settings › Privacy & Security › Accessibility, then
   run `vocalize app restart`.
2. **Microphone for Vocalize Recorder.** The recorder is a small separate app
   that `vocalize local install --stt` builds. macOS asks the first time you
   dictate. Click Allow.

`vocalize app status` shows whether both are in place. See
[docs/app.md](docs/app.md).

## Claude Code and Quick Actions

`vocalize integrate claude` installs a `/speak` skill for Claude Code and
five macOS Quick Actions (Speak with Vocalize, Stop Vocalize, Speak Latest
Plan, Swap in What I Said, Dictate with Vocalize). They run the same local
commands. An optional Claude Code hook that reads each response aloud can
be installed and removed from the settings page, on a PyPI install too. See [docs/installation.md](docs/installation.md#5-vocalize-integrate-claude--claude-code-and-quick-actions).

## What local only means

- **No cloud engine, key or account exists in the code.**
- **Only installing an engine uses the network**, and only when you ask:
  `vocalize local install`, or an install button on the settings page.
  Model files come from a pinned address. A redirect must stay on HTTPS.
  Every model file is checked against a pinned sha256 and size before it is
  used.
- **Engine packages come from hash-locked lists.** Each engine gets its own
  environment, installed from a list that pins every package by hash, with
  no builds from source. Piper is the exception: its packages are pinned by
  version only, not yet by hash.
- **Every engine starts offline.** Each one is a separate process started
  with `uv run --offline --no-python-downloads` and an environment built
  from an allow-list, so no password or token is passed on. If an engine
  cannot start offline, the error tells you to run `vocalize local install`
  again. Nothing falls back to the network.
- **The settings page and the speed-up servers stay on your Mac.** The page
  listens only on `127.0.0.1`. The servers that keep models loaded use local
  Unix sockets.
- **The tests check it.** The test suite blocks every connection that is
  not to the Mac itself. It also reads the code's imports and fails if a
  network client is imported anywhere except in the model downloader, the
  settings page server and the two modules of the warm servers' Unix
  sockets.
  That scan reads imports only, and its limits are written down in
  `tests/test_local_only.py`. It is a check, not a proof that no connection
  can ever be opened.
- **Tried with the network cut off.** Kokoro, Parakeet and Whisper were run
  with all network access denied by the macOS sandbox. All three still
  worked.

What Vocalize cannot control: the clipboard is readable by other apps, and
files saved in a synced folder (iCloud, Dropbox) leave the Mac through that
service. Choose a transcripts folder that does not sync if that matters to
you.

## Known limits

- Everything was measured on one Mac, a Mac Studio with 128 GB. The
  profiles for smaller Macs are untested.
- Not measured: the time from the end of a live dictation to text, and the
  first start after a restart, when the model is not yet in the file cache.
- Silent recordings are checked before they are transcribed: live takes,
  `vocalize listen --wav` and `vocalize transcribe`. Before that check,
  Whisper wrote "Thank you." for a silent file.
- Faint noise with no speech in it can still get past the silence check.
  A transcript with no letters or digits now counts as nothing heard, but
  Whisper can do worse than that: in a final check it wrote a whole
  invented sentence for 10 s of faint noise. Parakeet returned nothing for
  the same file. If this matters to you, use Parakeet for dictation.
- Nothing was measured through the microphone or the speakers.

## Development

Python 3.10 or later. Runtime dependencies are listed in `pyproject.toml`.

```bash
ruff check .
pytest -q
```

The tests use fakes and need no model, microphone or network. Tests against
real models are separate; see
[docs/local-model-evaluation.md](docs/local-model-evaluation.md).
[docs/for-reviewers.md](docs/for-reviewers.md) explains how the code is laid
out, and [docs/roadmap.md](docs/roadmap.md) lists what is planned.

## Licence

Vocalize is MIT licensed; see [LICENSE](LICENSE). The engines and models
keep their own licences:

- Kokoro's phonemizer and the espeak-ng library bundled with it are
  GPL-3.0-or-later. They run only inside the separate Kokoro worker process.
- Piper's engine is GPL-3.0-or-later. It also runs only in its own process.
- Parakeet's weights are CC-BY-4.0.
