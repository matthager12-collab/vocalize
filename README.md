# vocalize

[![CI](https://github.com/matthager12-collab/vocalize/actions/workflows/ci.yml/badge.svg)](https://github.com/matthager12-collab/vocalize/actions/workflows/ci.yml)

A command-line tool that turns text, markdown files, or piped stdin into
natural-sounding speech using the [ElevenLabs](https://elevenlabs.io) API —
plus a hook that wires it directly into [Claude Code](https://claude.com/claude-code),
so Claude's responses get read aloud automatically in your terminal or IDE.

## Quickstart

```bash
pipx install vocalize-cli
```

```bash
vocalize config
```

Walks you through your API key, a voice, and a speed, and saves it all.

```bash
vocalize speak "hello"
```

To set up just the key, skip the wizard and run `vocalize auth login` — it
stores the key in your OS keychain.

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
license). Then, recommended, store it in your OS keychain:

```bash
vocalize auth login
```

This prompts for the key (input hidden), validates it against the
ElevenLabs API, and stores it via your OS's own keychain (macOS Keychain,
Windows Credential Locker, Linux Secret Service) — no plaintext file to
manage. Piping it in from a secret manager works too:

```bash
op read op://vault/elevenlabs/key | vocalize auth login --stdin
```

An environment variable or `.env` file work as well, and take priority over
the keychain if both are set:

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

# Speak whatever is on the macOS clipboard
vocalize clip

# List available voices and grab an ID
vocalize voices

# Check your quota and cache
vocalize usage

# Use a specific voice/model, save without playing
vocalize speak-file report.md --voice <voice-id> --model eleven_flash_v2_5 \
  --output out.mp3 --no-play

# Cap how much gets sent (handy for free-tier character budgets)
vocalize speak-file long-report.md --max-chars 2000

# Slow it down a little
vocalize speak-file report.md --speed 0.9

# Skip the markdown flattening entirely
vocalize speak "raw **markdown** stays raw" --raw

# Speak through one specific provider, no fallback
vocalize speak "Test" --provider google

# See (or set) the order providers are tried in
vocalize chain

# Set up the offline, opt-in local voice (downloads ~354 MB once)
vocalize local install
```

Every synthesis result is cached on disk under `~/.cache/vocalize/`, keyed
by a hash of (text, voice, model, format, speed) — re-running the same
command twice doesn't burn API quota twice.

`vocalize stop` (from any terminal) stops playback immediately — the
player's identity (process ID plus launch timestamp) is tracked in
`~/.cache/vocalize/play.pid`, and stop refuses to touch a process that no
longer matches the full record — a recycled PID is never killed. A
stopped read exits cleanly; the mp3 stays cached.

Long inputs are also split automatically — at paragraph boundaries where
possible, then sentences, then words — into requests no bigger than
`--chunk-chars` (default 9500), so a long read no longer fails the API's
own per-request cap.

`vocalize clip` stops any current playback first, then speaks the
clipboard. It refuses content shaped like a secret — a single
high-entropy token, or one starting with a known credential prefix
(`sk-`, `pypi-`, `ghp_`, `op://`, `eyJ`, …) — without echoing it anywhere;
`--allow-secret` bypasses the guard when you're sure. This is a
habit-breaking guard for text copied out of a password manager, not a
full secret scanner.

## Configuration

Each setting is resolved on its own, taking the first source that supplies
it: CLI flag, then environment variable, then config file, then the
built-in default.

There's an interactive way to set the file up, if you'd rather not write
TOML by hand. It walks through three lists — voice (with a live preview of
the highlighted one), model, and speed — shows you a summary, and writes the
config file below. Unrecognised top-level keys already in that file are
carried through; comments and layout are not preserved. A file containing a
TOML table or array is left alone entirely, with a message saying to edit it
by hand. The wizard paints on the controlling terminal rather than on stdout,
so it still works under output-capturing wrappers like `op run`.

```bash
vocalize config
```

Hotkeys: `↑`/`↓` or `k`/`j` move, `Enter` selects, `p` previews the
highlighted voice, `m` types a value by hand, `q` or `Esc` cancels without
writing anything.

| Setting | Flag | Env var | Config file key | Default |
|---|---|---|---|---|
| API key | `--api-key` | `ELEVENLABS_API_KEY` | not read from the config file | stored via `vocalize auth` |
| Voice ID | `--voice` | `VOCALIZE_VOICE` | `voice` | `21m00Tcm4TlvDq8ikWAM` ("Rachel") |
| Model ID | `--model` | `VOCALIZE_MODEL` | `model` | `eleven_multilingual_v2` |
| Speed | `--speed` | `VOCALIZE_SPEED` | `speed` | unset — the API's own 1.0 |
| Max characters | `--max-chars` | `VOCALIZE_MAX_CHARS` | `max_chars` | unset on the CLI; the hook supplies a 500 fallback |
| Overflow mode | `--overflow` | `VOCALIZE_OVERFLOW` | `overflow` | `truncate` |
| Provider chain | `--provider` (forces one, no fallback) | `VOCALIZE_CHAIN` (comma-separated) | `chain` (array) | `["elevenlabs", "say"]` |
| Hook binary | — | `VOCALIZE_BIN` | not read from the config file | `vocalize` as found on `PATH` |

`--voice`/`--model`/`--speed` and their `VOCALIZE_*` env vars only ever apply
to the **primary** provider — the first in the chain, or the one `--provider`
forces. Every other link reads only its own `[providers.<name>]` table and
its own built-in defaults; see
[Providers and fallback](#providers-and-fallback) below.

The config file is TOML at `$XDG_CONFIG_HOME/vocalize/config.toml`, falling
back to `~/.config/vocalize/config.toml`. Flat keys, no sections:

```toml
chain = ["elevenlabs", "google", "say"]

# Flat keys = ElevenLabs, unchanged since before there was a chain.
voice = "21m00Tcm4TlvDq8ikWAM"
model = "eleven_flash_v2_5"
speed = 0.95
max_chars = 1000
overflow = "ask"

[providers.google]
voice = "en-US-Neural2-F"
language = "en-US"
monthly_chars = 1000000

[providers.say]
voice = "Samantha"

[providers.kokoro]
voice = "af_heart"
```

Every other provider gets its own `[providers.<name>]` table. The keys it
can hold: `voice`, `model`, `engine` (an alias for `model` — Polly's field is
called that), `speed`, `language`, `region`, `profile`, and `monthly_chars`.
A key outside that set warns on stderr rather than failing the run.

`overflow` decides what happens when input is longer than the resolved
`max_chars` cap: `truncate` (the default) cuts it at the cap, `ask` prompts
on the controlling terminal first — and degrades to `truncate` with a note
when there is no terminal to ask on (the Stop hook runs vocalize detached
from the terminal precisely so this always happens there; pipes and scripts
usually have no terminal either) — and `never` speaks the whole thing
regardless. With no cap set anywhere there is no overflow, so the mode
never fires. Hook-triggered speech still lives under the hook's 15-minute
watchdog described below, whatever the mode.

`vocalize settings` prints the resolved values (one `key=value` per
line, env and config applied) — handy for wrapper scripts and for checking
which source won.

Not having a config file is normal and silent. A file that isn't valid TOML
is an error naming the file; a key that isn't recognised is a warning on
stderr, so a typo doesn't pass unnoticed but doesn't stop the run either.
`speed` must be a number between 0.7 and 1.2 — anything else is a one-line
error naming the source it came from.

The API key is separate and never read from this file. It resolves in its
own order: `--api-key` flag, then `ELEVENLABS_API_KEY`, then a `.env` file
in the current directory, then the OS keychain. `vocalize auth login` sets
up the keychain entry; `vocalize auth status` shows which of those sources
is currently supplying the key.

## Providers and fallback

vocalize tries providers in order — a **chain** — until one speaks. The
default is `elevenlabs, say`: ElevenLabs behaves exactly as before, and a
failure now degrades to the always-free `say` instead of erroring out.

| Provider | Credentials | Config table | Per-request cap | Free tier | What `check` needs |
|---|---|---|---|---|---|
| `elevenlabs` | keychain / env / `.env` / `--api-key` | `[providers.elevenlabs]` or the flat legacy keys | 9,500 chars | 10,000 chars/month | an API key |
| `openai` | keychain / env / `.env` | `[providers.openai]` | 4,000 chars | none — prepaid credit only, ~$15/million chars | an API key |
| `google` | keychain / env / `.env` | `[providers.google]` | 4,500 chars (also a 4,900-byte hard cap) | ~4M Standard or ~1M Neural2/WaveNet chars/month, then bills | an API key |
| `polly` | your normal AWS credentials (env, `~/.aws/credentials`, a profile, or a role) — vocalize stores none of it | `[providers.polly]` | 2,900 chars | Standard 5M/month ongoing; Neural 1M/month for 12 months, then $4–$16/million | `boto3` installed + AWS credentials discoverable |
| `say` | none | `[providers.say]` | none (one call, any length) | free, offline | macOS with the `say` binary |
| `kokoro` | none | `[providers.kokoro]` | 400 chars per streamed piece | free, offline, one-time ~354 MB download | `uv` + `vocalize local install` done |

**Fallback rules**, decided by typed errors, not string-matching:

- **Unavailable / auth / transient** errors (missing key, bad credentials, a
  5xx or rate limit) skip straight to the next provider in the chain.
- **Quota** errors do the same, but also mark that provider exhausted in the
  local ledger for the rest of the calendar month — see
  [Budgets and the usage ledger](#budgets-and-the-usage-ledger).
- **Content** errors — a bad voice name, text longer than the API actually
  accepts — **stop the chain immediately**, loudly, naming the bad config
  key. A silent misconfiguration would be the worse bug to ship.
- Anything else (a bug in vocalize itself) is never treated as "try the next
  one" — it propagates as a real error.
- Once Kokoro's streaming playback has started, a later failure can't fall
  through to another provider either — you can't un-hear the first half of
  a read.

You'll see the handoff on stderr as it happens:

```
openai: out of credit — trying google
google: local budget reached (1,004,233/1,000,000 chars this month) — trying polly
Spoke via say (fallback).
```

If every provider fails, the error lists each one's reason, plus a hint to
add `say` to the chain if it's missing.

`--provider` is the opt-out: it forces exactly one provider with no
fallback at all, same as vocalize behaved before it had a chain.

**A chain is multi-vendor egress.** `elevenlabs, google, say` can, on a bad
day, send the same text to both ElevenLabs and Google before `say` finally
speaks it — every attempt that reaches a provider's `synthesize` call is a
real request to that vendor. `say` and `kokoro` are the exception: they
never send text off the machine.

Amazon Polly needs the optional extra:

```bash
pip install "vocalize-cli[polly]"
```

For the click-by-click setup of each provider — where to go, what to click,
the one command that stores the credential, the one command that proves it
works — see [docs/provider-credentials.md](docs/provider-credentials.md).

## Budgets and the usage ledger

Cloud providers don't stop at their free tier — they bill past it. vocalize
can't see your vendor invoice, so it keeps its own local estimate instead
and stops using a provider once you say where the line is.

Set `monthly_chars` under that provider's `[providers.<name>]` table:

```toml
[providers.google]
monthly_chars = 1000000
```

Usage is tracked in `~/.cache/vocalize/usage.json`, one entry per provider
per calendar month, decided by your machine's local time. A provider that
comes back with a real quota error from the vendor is remembered as
exhausted for the rest of that month — no further requests to it, even if
you raise `monthly_chars` in between; only the new month clears it.

`vocalize usage` prints every provider's tally against its budget (or
"unlimited" with no `monthly_chars` set), flags any that are exhausted, then
ElevenLabs's own remote quota (skipped gracefully, not a failure, when no
key is configured), then local disk-cache stats.

The ledger is per-machine and an estimate, not a bill: a cached (repeat)
request costs nothing and isn't counted, and usage from a different machine
never shows up here. Google's own limits and billing are byte-based, not
character-based, so vocalize counts Google's usage in UTF-8 bytes too — the
same text can cost a different amount against Google's cap than everyone
else's.

## Local providers

Two providers never leave the machine.

**`say`** is built in — macOS only, no setup, no network, no quota. Output
is `.m4a`, not `.mp3`. It uses whichever voices `say -v ?` lists on your
Mac; set one with `[providers.say] voice = "Samantha"`.

**Kokoro** is opt-in. `pip install vocalize-cli` brings none of it — no
model weights, no extra runtime — until you ask for it:

```bash
vocalize local install
```

This prints exactly what it's about to download before asking to confirm:
`kokoro-v1.0.onnx` (326 MB) and `voices-v1.0.bin` (28 MB) from a pinned
GitHub release, into `~/.cache/vocalize/models/kokoro/`, plus about 230 MB
more that `uv` fetches into its own cache (Python 3.12 and the `kokoro-onnx`
runtime). Every file is checked against a pinned sha256 before it's kept —
a mismatch deletes it and refuses rather than installing anything
unverified. The runtime runs under `uv run --python 3.12`, entirely apart
from vocalize's own environment, so installing Kokoro never touches or
upgrades the Python vocalize itself runs in. `vocalize local status`
reports what's present, missing, or unverified.

Use it for one read with `--provider kokoro`, or add it to your chain in
`config.toml`.

Long text streams: it's broken into ~400-character pieces, and playback
starts after the first one is ready — roughly 20–25 seconds of speech —
instead of waiting for the whole thing to render. Measured on this Mac
(M3): about 5x faster than real time, peaking around 870 MB of RAM while
rendering. `vocalize stop`, run from any terminal, halts a Kokoro read
mid-sentence the same as any other provider.

## macOS Quick Actions (highlight → speak)

Two Services let you use vocalize from any app without a terminal:

- **Speak with Vocalize** — highlight text anywhere, right-click →
  Services → Speak with Vocalize. It stops whatever was already playing
  and reads the selection. If the selection is over your cap and your
  overflow mode is `ask`, a picker offers **Speak all**, three summary
  depths (**light** ~25s, **medium** ~1 min, **detailed** ~2.5 min), or
  **Truncate**. Summaries are produced by `claude -p --model haiku` (with
  tools denied) — picking one has a few seconds of silent cold-start
  before audio begins. If no `claude` binary was found when you ran the
  installer, the picker simply omits the three summary depths.
- **Stop Vocalize** — appears in every app's application menu → Services;
  silences playback from anywhere.
- **Speak Latest Plan** — reads the newest Claude Code plan file
  (`~/.claude/plans/`) aloud on demand. Made for the plan-approval moment:
  the proposal card is up, you press your shortcut, hear the plan, then
  accept or reject. Nothing reads unless you trigger it.

Install both:

```bash
python3 hooks/install_quick_action.py
```

This copies the bundles from `hooks/quick_actions/` into
`~/Library/Services/` with this machine's absolute `vocalize`, `claude`,
and helper paths baked in, then refreshes the Services registry. Run it
from a normal terminal — its PATH is what gets captured. To trigger the
actions from the keyboard, assign shortcuts under System Settings →
Keyboard → Keyboard Shortcuts → Services (Stop Vocalize is worth a
shortcut of its own so you can silence a read from anywhere).

Some Electron apps (Claude Code desktop among them) don't expose the
Services menu for text selected in their own window. There, copy the
selection and use `/speak clip` (or `vocalize clip` in a terminal)
instead.

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

Whatever speaks a response is whichever provider your chain resolves to —
`vocalize settings` now prints a `chain=` line alongside `overflow=` and
`max_chars=`, which is how a wrapper script like `/speak` can check it.

**On-demand mode.** If you'd rather trigger speech yourself than have every
response spoken, skip the install and run the script with `--latest`. It
finds your most recent Claude Code response — in any session — and speaks
that one:

```bash
python3 hooks/claude_stop_hook.py --latest
```

Combine it with `VOCALIZE_MAX_CHARS` to control how much gets read.

To install it as an automatic hook instead:

```bash
python3 hooks/install_hook.py
```

This merges a `Stop` hook entry into `~/.claude/settings.json` (backing up
the existing file first) rather than overwriting your other hooks. Every
Claude Code response after that gets spoken aloud automatically. Uninstall
by removing the `vocalize` entry from the `Stop` array in that file.

By default the hook caps each response at 500 characters before speaking
it — a Stop hook fires after every turn, so a long response would burn
through the ElevenLabs free-tier quota fast. That 500 is only a fallback
(`--default-max-chars`, supplied by the hook): a `VOCALIZE_MAX_CHARS` env
var, a `max_chars` in the config file, or an `overflow` mode of `never`
all override it, resolved by `vocalize` itself with the usual precedence.

The hook launches `vocalize` in its own session, detached from the
terminal, so an `overflow` of `ask` degrades to truncate there instead of
writing a Y/n prompt into the middle of a session nobody is watching. Its
subprocess timeout scales with the length of the text being spoken (about
twelve characters a second, plus a minute of headroom), capped at a hard
15-minute ceiling as a watchdog against hung processes — a read that
would outlast the ceiling is stopped there, and the whole process group
is killed so no orphaned audio keeps playing.

The hook looks up the `vocalize` binary on `PATH`, but Claude Code hooks
run in Claude Code's own environment, not your interactive shell — if
`vocalize` was installed into a virtualenv that isn't on that `PATH`, set
`VOCALIZE_BIN` to the full path (e.g. `/path/to/.venv/bin/vocalize`) to
point the hook at it directly.

### Speaking files, artifacts, and more

Two primitives cover almost everything: `vocalize speak-file <path>` speaks
any local file (markdown flattened first), and the hook's `--latest` mode
speaks the most recent Claude Code response. Anything Claude itself has to
fetch — a claude.ai artifact, for instance — has to be fetched *by Claude*
(the CLI has no session), summarized, written to a file, and spoken from
the file:

```bash
vocalize speak-file /path/to/summary.txt
```

Never interpolate the summary into the command line itself — see guard 4.

**Web pages.** The CLI has no URL support, by design — it can't fetch
anything. For a URL, Claude fetches the page itself, in an isolated
subagent with a locked-down tool set, and produces either a short spoken
digest or a verbatim extract of the core content. That text comes back to
the main session the same way any other summary does: written to a file
and passed to `speak-file`.

If you wire this into a slash command of your own, treat it as a security
surface, because **every character you speak is sent to whichever provider
in your chain ends up speaking it** — unless that provider is `say` or
`kokoro`. The guard principles that matter, in order:

1. Resolve paths (`realpath`, expand `~`, casefold) and check an
   **allow-list** of speakable directories — symlinks and `../` defeat
   string matching on the raw argument.
2. Hard-refuse secret-shaped files (`.env*`, keys, credentials) and your
   sensitive directories; confirm before speaking anything else unusual.
3. Summarize long or fetched content in an **isolated subagent** that
   returns only the summary — content you fetched can carry instructions
   aimed at your session.
4. Pass summaries as a file path (as above) — never build the shell
   command by interpolating model-written text into a quoted string. A
   summary a model wrote can contain `$(...)`, and the shell will run it;
   `printf '%s' "<summary>"` is exactly that bug.
5. Confirm before any read that will spend real quota; a free tier is
   10,000 characters a month.
6. Remember the disk cache: everything spoken leaves an mp3 under
   `~/.cache/vocalize/`.
7. Fetching is a second egress. Fetch only the URL the user typed — a page
   can carry text telling its reader to fetch another URL, with data
   smuggled out in the query string.
8. Refuse non-public hosts. `localhost` and `169.254.169.254` are
   reachable from your machine and nowhere else; parse the URL with the
   `ipaddress` module rather than pattern-matching the string.

## How it's built

Four decisions shaped the design:

- **The markdown flattener is a pure function.** The hardest logic in the
  project — deciding what a table, list, or code block should *sound* like —
  takes a string and returns a string. No I/O, no client, no key. That's why
  it has the deepest test coverage in the repo, including the edge cases
  that bit during review: prose containing a stray `|`, single-dash GFM
  separators, ragged rows, duplicate column names.
- **One code path for humans and hooks.** The Claude Code hook doesn't
  reimplement synthesis; it shells out to the same `vocalize` CLI you'd
  type by hand (with an `--` argv guard so a response starting with a
  bullet isn't parsed as a flag). Anything the hook can do, you can
  reproduce and debug from your own terminal.
- **The hook may fail; the session may not.** Every failure path in the
  Stop hook logs one line to stderr and exits 0. A dead API key or a hung
  request costs you the audio, never the coding session.
- **The cache is an optimization, never a failure source.** Synthesis
  results are content-addressed on disk; an unreadable or unwritable cache
  degrades to a fresh API call instead of an error.

## Architecture

```
vocalize/
  __init__.py     # package version
  __main__.py     # python -m vocalize entry point
  preprocess.py   # markdown -> speakable text (pure function, fully unit tested)
  config.py       # API key resolution + settings: flag > env > config.toml > default
  exceptions.py   # VocalizeError / TTSRequestError / typed ProviderError family
  tts.py          # ElevenLabs API wrapper (client is injected, so
                   # it's mockable in tests without hitting the network)
  cache.py        # disk cache: cache_key/get/put, shared by every provider
  chain.py        # tries each provider in the chain in turn until one speaks
  ledger.py       # ~/.cache/vocalize/usage.json — local monthly budget tracking
  providers/      # elevenlabs.py, openai.py, google.py, polly.py, say.py, kokoro.py
  local/          # Kokoro's opt-in download/verify + the uv-run worker script
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
  a vision model in the loop, not a text transform. That's out of scope for
  the CLI itself, but the next step now lives outside it, where it belongs:
  the `/speak` command's web-page mode renders a page in a browser, hands it
  to a vision-capable subagent, and the diagram description comes back as
  plain text — same as any other content this tool speaks.
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
  content-addressed (keyed by a hash of text, voice, model, format, and
  speed),
  so it's always safe to delete some or all of it — nothing will break,
  you'll just re-pay for a re-synthesized clip.
- **`--api-key` on the command line is visible to other local processes**
  (anything that can run `ps`). Prefer `vocalize auth login`, the
  `ELEVENLABS_API_KEY` environment variable, or a `.env` file instead.
- `vocalize voices` lists only the first page of results from the
  ElevenLabs API.
- **A chain is multi-vendor egress.** A fallback chain can send the same
  text to more than one cloud vendor before one of them succeeds — see
  [Providers and fallback](#providers-and-fallback).
- **The usage ledger is local and an estimate, not a bill.** It doesn't see
  what your vendor actually charges, doesn't know about usage from another
  machine, and a cached (repeat) request isn't counted at all.
- **Polly ignores `--speed`.** Its rate control needs SSML, which this
  release doesn't wrap plain text into; `[providers.polly]` has no speed
  knob yet.
- **Joined MP3 chunks are a byte concatenation, not a re-encode.** Most
  players handle it fine, but the frame boundary between chunks can
  occasionally produce an audible click.
- **`vocalize config`'s wizard only sets up ElevenLabs.** Configure the rest
  of the chain with `vocalize chain` or by hand-editing `config.toml`.
- **Kokoro needs `uv`**, and its shipped pack carries 54 voices across nine
  languages but is phonemized for `en-us` unless you set `language` under
  `[providers.kokoro]` to match a non-English voice; it's opt-in for a
  reason — see [Local providers](#local-providers).
- **A locked or first-use macOS keychain blocks silently.** Reading a stored
  key can raise a macOS permission dialog ("python wants to use your
  confidential information"); until you click Always Allow, every command
  that needs that key waits. Click it once per Python binary, or supply the
  key through its environment variable instead.

## License

MIT
