# Vocalize

Vocalize is a command-line tool for macOS on Apple Silicon. It does two
things, both on your Mac:

- **Text to speech.** It reads text, Markdown files, the clipboard or piped
  input aloud.
- **Speech to text.** It turns what you say into text on the clipboard
  (dictation), and turns audio files into Markdown transcripts.

The models run on your Mac. Vocalize does not send your text, audio or
transcripts to an online service. See [What local only means](#what-local-only-means).

New to the command line? [docs/getting-started.md](docs/getting-started.md)
walks through the whole setup one step at a time.

## Install

You need macOS on Apple Silicon, [uv](https://docs.astral.sh/uv/) and
Apple's command line tools (`xcode-select --install`).

```bash
uv tool install vocalize-cli
vocalize local install      # download the Kokoro voice (about 340 MB)
vocalize doctor             # check what is ready and what is missing
```

`vocalize local install` asks before it downloads anything. Every
`local install` command checks each file against a pinned checksum before it
keeps it. Full detail is in [docs/installation.md](docs/installation.md).

**Upgrading from 0.15?** Version 1.0 removes cloud voices, account keys,
usage budgets and summaries. If you need those, stay on 0.15:
`uv tool install 'vocalize-cli==0.15.0'`. An old config file still loads; the
settings that no longer exist print a notice and are ignored. See
[CHANGELOG.md](CHANGELOG.md).

## Text to speech

```bash
vocalize speak "Hello, this is a test."
vocalize speak-file report.md       # a Markdown or text file
cat notes.md | vocalize speak-file - # piped input
vocalize clip                       # whatever is on the clipboard
vocalize pause                      # pause what is playing
vocalize resume                     # continue a paused or interrupted read
vocalize stop                       # stop what is playing
```

### Speech engines

| Engine | How to get it | Role |
|---|---|---|
| Kokoro | `vocalize local install` | The default voice |
| Piper | `vocalize local install --piper` | Optional. Its licence is shown before the download. Piper is GPL software, so it runs as a separate program |
| macOS `say` | Built in | The fallback. Nothing to download |

Vocalize tries the engines in order. The default order is Kokoro, then
`say`. If an engine fails before playback starts, the next one is tried and
Vocalize tells you which engine spoke.

```bash
vocalize chain                    # show the current order
vocalize chain kokoro piper say   # set a new order
vocalize voices                   # list Kokoro voices
vocalize voices --provider piper  # list Piper voices
vocalize speak "Hi" --provider say # use one engine, with no fallback
```

### Markdown and tables

Before speaking, Vocalize rewrites Markdown into plain sentences. A table is
announced with its size and column names, each row is read as its own
numbered paragraph ("Row 1: Plan A. Cost: $5."), and "End of table." marks
where it stops. Bullets become "First, ... Second, ...", links keep their
text and drop the address, and code blocks are replaced with a short spoken
note. Use `--raw` to skip this and speak the text exactly as written.

## Speech to text

### Dictation

```bash
vocalize local install --stt   # Parakeet on Apple Silicon; add --engine whisper for Whisper
vocalize dictate               # press once to record, again to stop
```

When you stop, the text is copied to the clipboard. Set `[stt] paste = true`
to also paste it into the app you were using.

Two engines are available. **Parakeet** is the default on Apple Silicon.
**Whisper** is the alternative; it also supports a vocabulary list and
transcribes audio files. Install both if you want both.

**Optional cleanup.** An on-device language model can remove filler words
and fix punctuation before the text is copied:

```bash
vocalize local install --llm   # about 2.5 GB; needs 12 GB of memory
vocalize dictate --cleanup     # or set [stt] cleanup = "local"
```

If the cleaned text drifts from what you said, Vocalize keeps your original
words. Cleanup is off by default. The full guide is
[docs/dictation.md](docs/dictation.md).

### Transcribe audio files

```bash
vocalize local install --stt --engine whisper
vocalize transcribe recording.m4a
vocalize transcribe ~/Recordings     # every supported file in a folder
```

Each file becomes a Markdown file with timestamped lines, written to your
transcripts folder. Files already transcribed are skipped. See
[docs/transcribe.md](docs/transcribe.md).

## Settings

```bash
vocalize portal     # open the settings page in your browser
vocalize settings   # print the current settings, one per line
```

The settings page runs on your Mac at `127.0.0.1`. It opens with a one-time
link and closes after 15 minutes with nothing to do. Every setting is
stored in one file, `~/.config/vocalize/config.toml`, which you can also edit
by hand.

<!-- settings-table:start -->
<!-- settings-table:end -->

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
commands. See [docs/installation.md](docs/installation.md#5-vocalize-integrate-claude--claude-code-and-quick-actions).

## What local only means

- **Only `vocalize local install` uses the network**, and only when you run
  it. It downloads from the hosts named in a pinned list, over HTTPS, at a
  pinned version, and checks every file's size and sha256.
- **Every engine runs offline.** Each one is a separate process started with
  `uv run --offline --no-python-downloads`, with an environment that turns
  off model downloads and passes on no passwords or tokens.
- **Nothing falls back to the network.** If an engine cannot start offline,
  the error tells you to run `vocalize local install` again.
- **The settings page and the speed-up servers stay on your Mac.** The page
  listens only on `127.0.0.1`; the servers that keep models loaded use local
  Unix sockets.
- **The tests prove it.** The test suite runs with network access turned off
  and checks that no other part of Vocalize can open a network connection.

What Vocalize cannot control: the clipboard is readable by other apps, and
files saved in a synced folder (iCloud, Dropbox) leave the Mac through that
service. Choose a transcripts folder that does not sync if that matters to
you.

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

MIT licensed; see [LICENSE](LICENSE). Each model and engine keeps its own
licence.
