# Transcribing audio files

`vocalize transcribe` turns recordings you already have into Markdown
transcripts. It runs on your Mac with Parakeet or Whisper. It does not
record; for live speech, use [dictation](dictation.md).

## Before you start

Which engine transcribes files depends on `[stt] engine`:

| `[stt] engine` | Files are transcribed by | Install it with |
|---|---|---|
| `parakeet` | Parakeet (Apple Silicon only) | `vocalize local install --stt` |
| `whisper` | Whisper | `vocalize local install --stt --engine whisper` |
| `auto` (the default) | Whisper when a Whisper model is installed, otherwise Parakeet | either of the above |

Under `auto`, dictation prefers Parakeet when it is installed, but files
prefer Whisper. Set `engine = "parakeet"`, or apply any machine profile
other than small on the settings page, to use Parakeet for both.

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
2. A silent recording is not sent to the engine. Its transcript says
   "This recording is silent: it has no words."
3. One engine run produces timestamped segments. The engine runs as a
   separate process, offline. Parakeet cuts a long file into overlapping
   two-minute pieces and keeps the spoken order at each join.
4. Your `[stt] vocabulary` list corrects the text of every segment (see
   [dictation.md](dictation.md#vocabulary)).
5. The transcript is written to a temporary file and then moved into place,
   so a crash never leaves a half-written transcript.

If the engine is not installed, or cannot start offline, the command stops
and tells you to run `vocalize local install` again. It never downloads
anything on its own.

## How fast it is

Measured with `vocalize transcribe` on a Mac Studio (Apple M5 Max,
128 GB), with speech made by the macOS voice:

| File | Engine | Time | Memory | Word error rate |
|---|---|---|---|---|
| 10 minutes | Parakeet | 7.4 s | 944 MB | 2.0 % |
| 60 minutes | Parakeet | 27.6 s | 1.5 GB | 1.6 % |
| 10 minutes | Whisper large-v3-turbo-q5_0 | 19.4 s | 1.03 GB | 1.5 % |

No word was repeated or dropped where Parakeet's pieces join. In that run
two sentences swapped places at one join; 1.0.0 fixes that. Memory is the
largest resident memory and may undercount Parakeet. See
[measurements.md](plans/2026-11-local-only/measurements.md).

## Known limits

- The silence check looks at the level of the whole file. Faint noise
  with no speech in it can get past it. In testing, Whisper wrote "." for
  one such file and a whole invented sentence for another; Parakeet
  returned nothing for either.
  Speakers are not told apart, and video files are not supported.

## Settings

```toml
[transcribe]
folder = "~/Documents/Vocalize Notes"
keep_audio = false
model = ""
```

`folder` is where transcripts are written. If the recordings are private,
pick a folder that does not sync to iCloud or Dropbox; a synced folder copies
the transcripts off your Mac. `keep_audio` saves a copy of each recording
next to its transcript. `model` picks the Whisper model for files; empty
uses the dictation model, `[stt] model`. All three are also on the settings
page, under Transcription.

## Coming from 0.15

`vocalize transcribe` replaces `vocalize notes`. The old name still works as
a hidden alias. Because summaries and note templates were removed, the alias
accepts and ignores `--template` and `--summarizer`.

A `[notes]` table in your config file is read as `[transcribe]`. A file you
already processed with `notes` is not transcribed again.
