# Notes — Transcribe and Summarize Audio and Text

`vocalize notes` turns audio recordings and transcripts into structured markdown notes with timestamps, frontmatter metadata, and optional AI summaries.

```bash
vocalize notes meeting.m4a
vocalize notes *.mp3 --template meeting
vocalize notes memo.m4a --summarizer local
```

## How It Works

1. **Ingestion & Done Check:** Scans sources (audio or `.txt`). Notes folder acts as the ledger — if a file matches both the recorded sha256 and resolved path, it is skipped by name (unless `--force`).
2. **Self-Ingestion Guard:** Files already inside the notes folder are refused.
3. **Audio Conversion:** Audio files are converted to 16 kHz mono WAV via `afconvert` in a private 0700 temporary directory.
4. **Transcription:** Transcribed on-device via `whisper_worker.py --segments` with progress updates and an inactivity timeout.
5. **Summarization:** Cleaned transcript is summarized with the selected template using local Qwen (`--summarizer local`), Claude CLI (`--summarizer claude-cli`), or Anthropic API (`--summarizer anthropic`), or skipped (`--summarizer off`).
6. **Atomic Write:** The note is written to a temporary file (`0600`, `O_NOFOLLOW`) and atomically swapped into place (`<folder>/YYYY-MM-DD-<slug>.md`).

## Configuration

In `~/.config/vocalize/config.toml`:

```toml
[notes]
folder = "~/Documents/Vocalize Notes"   # non-empty path, default ~/Documents/Vocalize Notes
template = "memo"                       # memo | meeting | lecture | journal | path to .md
summarizer = "local"                    # local | claude-cli | anthropic | off
keep_audio = false                      # save converted WAV alongside note
model = ""                              # "" uses [stt] model; or a specific whisper model
```

## Privacy & Security

- **Transcript Storage:** Written `0600` under your notes folder (`~/Documents/Vocalize Notes`).
- **Untrusted Input:** Every note includes frontmatter `trust: "untrusted-transcript"`. Audio and transcript text are third-party data — never feed notes to LLMs as system instructions.
- **Claude CLI Isolation:** When using `claude-cli`, `claude -p` runs with `--strict-mcp-config`, `--setting-sources ""`, and `cwd=tempfile.gettempdir()`. User MCP tools, hooks, and project context are completely isolated from transcript data.
- **Egress Visibility:** Whenever text leaves your machine (`claude-cli` or `anthropic`), an explicit line is printed to stderr: `vocalize: sent to <backend>`. `local` and `off` never print an egress line.
- **iCloud Sync Caveat:** If your notes folder resolves inside `~/Library/Mobile Documents` (iCloud Drive), notes will sync to Apple cloud storage. `vocalize doctor` warns if your notes folder is under iCloud.
- **Storage Growth:** If `keep_audio = true`, WAV files are saved beside notes. `vocalize doctor` displays the total disk space used by your notes folder.

## Templates

Four built-in templates ship in `vocalize/assets/notes/`:
- `memo`: concise summaries, key points, and action items.
- `meeting`: participants, discussion points, decisions, action items.
- `lecture`: core thesis, key concepts, evidence, conclusions.
- `journal`: thoughts, experiences, emotional tone, reflections.

Custom templates can be passed via `--template /path/to/template.md`. Custom templates are opened with `O_NOFOLLOW`, capped at 64 KB, and cannot target symlinks or package internals.
