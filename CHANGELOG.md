# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 0.5.0 - 2026-08-31

### Added

- Chunked synthesis: input longer than the `eleven_multilingual_v2` model's
  10,000-character per-request cap is now split into chunks — preferring
  paragraph, then sentence, then word boundaries — synthesized sequentially,
  and concatenated into one audio file, instead of failing outright. Each
  chunk still goes through the existing disk cache individually, so a
  partially-cached long document only pays for the chunks it's missing.
- `--chunk-chars` flag to control the split size (default: 9,500).

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
