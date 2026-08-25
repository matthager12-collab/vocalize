# vocalize

[![CI](https://github.com/matthager12-collab/vocalize/actions/workflows/ci.yml/badge.svg)](https://github.com/matthager12-collab/vocalize/actions/workflows/ci.yml)

A command-line tool that turns text, markdown files, or piped stdin into
natural-sounding speech using the [ElevenLabs](https://elevenlabs.io) API —
plus a hook that wires it directly into [Claude Code](https://claude.com/claude-code),
so Claude's responses get read aloud automatically in your terminal or IDE.

## Why this exists

Text-to-speech readers are good at *voices* and bad at *structure*. Point one
at a markdown report and it reads a table cell-by-cell, left to right, with
no sense of which row or column you're in — "Q1. 4.2 million. Q2. 5.1
million" instead of "for Q1, revenue is 4.2 million." Headings, bullet
lists, and inline code fare the same way: read exactly as typed, syntax and
all.

`vocalize` fixes the part of that problem that's actually fixable without a
vision model: a preprocessing pass (`vocalize/preprocess.py`) rewrites
markdown into short, declarative sentences *before* it ever reaches the TTS
API — tables become "for X, Y is Z" sentences, bullets become "First, ...
Second, ...", links keep their text and drop the URL, and fenced code blocks
are replaced with a spoken placeholder instead of being read character by
character. It's a text transform, so it's fully unit tested without any
API key or network access (see `tests/test_preprocess.py`).

## Install

```bash
pipx install vocalize-cli
```

(or `uvx --from vocalize-cli vocalize` for a one-off run without installing
anything). The package is published on PyPI as `vocalize-cli`; the command
it installs is still `vocalize`.

For a from-source or dev install:

```bash
git clone <this-repo>
cd vocalize
pip install -e .
```

Get a free ElevenLabs API key at
[elevenlabs.io/app/settings/api-keys](https://elevenlabs.io/app/settings/api-keys)
(free tier: 10,000 characters/month, API access included, no commercial
license). Then either:

```bash
export ELEVENLABS_API_KEY=your-key-here
```

or copy `.env.example` to `.env` and fill it in (requires the optional
`python-dotenv` extra: `pip install -e ".[dotenv]"`).

## Usage

```bash
# Speak a string directly
vocalize speak "Hello, this is a test."

# Speak a markdown file — tables and formatting get flattened first
vocalize speak-file report.md

# Pipe anything in
cat notes.md | vocalize speak-file -

# List available voices and grab an ID
vocalize voices

# Use a specific voice/model, save without playing
vocalize speak-file report.md --voice <voice-id> --model eleven_flash_v2_5 \
  --output out.mp3 --no-play

# Cap how much gets sent (handy for free-tier character budgets)
vocalize speak-file long-report.md --max-chars 2000

# Skip the markdown flattening entirely
vocalize speak "raw **markdown** stays raw" --raw
```

Every synthesis result is cached on disk under `~/.cache/vocalize/`, keyed
by a hash of (text, voice, model, format) — re-running the same command
twice doesn't burn API quota twice.

## Claude Code integration

The hook scripts ship in the git repository, not the PyPI package — clone
the repo to install the hook (it shells out to the `vocalize` command, so a
pipx-installed CLI plus a cloned repo works fine together).

`hooks/claude_stop_hook.py` is a [Claude Code Stop
hook](https://docs.claude.com/en/docs/claude-code/hooks): a script Claude
Code runs every time it finishes a response. This one reads the transcript,
pulls out Claude's last message, and pipes it through the same `vocalize`
CLI — so it works identically whether Claude Code is running in a bare
terminal or inside an IDE's integrated terminal (VS Code, Cursor, etc.),
since both use the same `~/.claude/settings.json` hook config.

To install it:

```bash
python3 hooks/install_hook.py
```

This merges a `Stop` hook entry into `~/.claude/settings.json` (backing up
the existing file first) rather than overwriting your other hooks. Every
Claude Code response after that gets spoken aloud automatically. Uninstall
by removing the `vocalize` entry from the `Stop` array in that file.

By default the hook truncates each response to 500 characters before
speaking it (`DEFAULT_MAX_CHARS` in `claude_stop_hook.py`) — a Stop hook
fires after every turn, so a long response would burn through the
ElevenLabs free-tier quota fast. Override with `VOCALIZE_MAX_CHARS` in the
environment.

The hook looks up the `vocalize` binary on `PATH`, but Claude Code hooks
run in Claude Code's own environment, not your interactive shell — if
`vocalize` was installed into a virtualenv that isn't on that `PATH`, set
`VOCALIZE_BIN` to the full path (e.g. `/path/to/.venv/bin/vocalize`) to
point the hook at it directly.

## Architecture

```
vocalize/
  __init__.py     # package version
  __main__.py     # python -m vocalize entry point
  preprocess.py   # markdown -> speakable text (pure function, fully unit tested)
  config.py       # API key resolution: --api-key > $ELEVENLABS_API_KEY > .env
  exceptions.py   # VocalizeError / TTSRequestError
  tts.py          # ElevenLabs API wrapper + disk cache (client is injected, so
                   # it's mockable in tests without hitting the network)
  audio.py        # save to disk + play via the OS's native player
                   # (afplay / mpg123 / ffplay / PowerShell, whichever exists)
  cli.py          # click-based CLI wiring the above together
hooks/
  claude_stop_hook.py  # Claude Code Stop hook -> calls the vocalize CLI
  install_hook.py      # safely merges the hook into ~/.claude/settings.json
tests/                  # pytest, all mocked — no API key needed to run these
```

## Testing

```bash
pip install -e ".[dev]"
pytest
```

All tests run offline: the ElevenLabs client is dependency-injected into
`tts.py`, so tests pass in a fake client instead of hitting the real API.

## Known limitations

- **Charts and images aren't described.** Flattening markdown tables is a
  text problem; a rendered chart is an image, and describing it well needs
  a vision model in the loop, not a text transform. Out of scope for this
  project, but a natural next step — pipe the image through a
  vision-capable model first, feed its description into `vocalize` in
  place of the chart.
- **Free tier is 10,000 characters/month** — plenty for reading a handful
  of documents aloud, not for continuous use. `--max-chars` and the disk
  cache both help stretch it.
- Table flattening handles standard GFM pipe tables; it doesn't attempt to
  handle merged cells or nested tables (rare enough in practice that it
  wasn't worth the complexity).
- **Windows playback is untested.** The PowerShell `SoundPlayer` fallback
  only plays WAV, so the mp3 files this tool generates likely won't play
  there. Use `--no-play` and open the saved file with whatever's on hand.
- **The disk cache under `~/.cache/vocalize` grows unbounded.** It's
  content-addressed (keyed by a hash of text, voice, model, and format),
  so it's always safe to delete some or all of it — nothing will break,
  you'll just re-pay for a re-synthesized clip.
- **`--api-key` on the command line is visible to other local processes**
  (anything that can run `ps`). Prefer the `ELEVENLABS_API_KEY` environment
  variable or a `.env` file instead.
- `vocalize voices` lists only the first page of results from the
  ElevenLabs API.

## License

MIT
