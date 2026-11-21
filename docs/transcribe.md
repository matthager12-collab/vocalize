# Transcribing audio files

`vocalize transcribe` turns recordings you already have into Markdown
transcripts. It runs on your Mac with the Whisper engine. It does not
record; for live speech, use [dictation](dictation.md).

## Before you start

`transcribe` needs Whisper, even if you dictate with Parakeet:

```bash
vocalize local install --stt --engine whisper
```

## Use it

```bash
vocalize transcribe recording.m4a
vocalize transcribe one.m4a two.wav
vocalize transcribe ~/Recordings
```

You can pass files, folders or both. For a folder, Vocalize transcribes the
supported files in it.

Supported files: `.m4a`, `.mp3`, `.wav`, `.aif`, `.aiff`, `.caf` and `.txt`.
A `.txt` file is imported as the transcript. Video files are not supported,
and speakers are not told apart.

Options:

| Option | What it does |
|---|---|
| `--force` | Transcribe a file again even if it was done before |
| `--keep-audio` | Also save the audio next to the transcript |

## What you get

For each file, Vocalize writes `<folder>/<date>-<name>.md`, readable only by
you (mode 0600). The file starts with frontmatter:

| Key | Value |
|---|---|
| `title`, `date`, `captured` | Taken from the source file |
| `type`, `topics`, `license` | Labels you can edit |
| `source`, `source_sha256` | Which file this came from, and its checksum |
| `duration_seconds` | Length of the audio |
| `transcribed_by` | The engine and model used |
| `template` | Always `"none"` |
| `left_machine` | Always `false` |
| `trust` | Always `"untrusted-transcript"`, because the text is whatever was said |
| vocalize version | The version that wrote the file |

The `summarized_by` key is always `"none"`, because summaries were removed.

The body is one `## Transcript` section with a timestamp at the start of each
line. Your original files are never changed or moved.

## Running it again

Vocalize remembers each source by its path and checksum. Running
`transcribe` again on the same files skips the ones already done, so you can
point it at a folder repeatedly and only new recordings are processed. Use
`--force` to redo a file.

## How it works

1. `afconvert`, which is built into macOS, converts the audio to 16 kHz mono.
2. One Whisper run produces timestamped segments. Whisper runs as a
   separate process, offline.
3. The transcript is written to a temporary file and then moved into place,
   so a crash never leaves a half-written transcript.

If Whisper is not installed, or cannot start offline, the command stops and
tells you to run `vocalize local install` again. It never downloads anything
on its own.

## Settings

```toml
[transcribe]
folder = "~/Documents/Vocalize Notes"
```

`folder` is where transcripts are written. If the recordings are private,
pick a folder that does not sync to iCloud or Dropbox; a synced folder copies
the transcripts off your Mac.

## Coming from 0.15

`vocalize transcribe` replaces `vocalize notes`. The old name still works as
a hidden alias. Because summaries and note templates were removed, the alias accepts and ignores `--template` and `--summarizer off`.

A `[notes]` table in your config file is read as `[transcribe]`. A file you
already processed with `notes` is not transcribed again.
