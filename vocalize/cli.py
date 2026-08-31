"""Command-line interface for vocalize.

    vocalize speak "some text" --play
    vocalize speak-file report.md --play
    cat notes.md | vocalize speak-file - --play
    vocalize stop
    vocalize voices
    vocalize usage
    vocalize config
    vocalize auth login
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from . import __version__
from .audio import play as play_audio
from .audio import save as save_audio
from .audio import stop_playback
from .auth import delete_key, key_source, login, masked, probe_keychain, prompt_for_key
from .config import (
    DEFAULT_MODEL,
    DEFAULT_VOICE,
    OVERFLOW_MODES,
    load_config_file,
    resolve_api_key,
    resolve_overflow,
    resolve_settings,
)
from .exceptions import TTSRequestError, VocalizeError
from .preprocess import (
    DEFAULT_CHUNK_CHARS,
    flatten_markdown,
    split_for_synthesis,
    truncate_for_budget,
)
from .tts import DEFAULT_CACHE_DIR, build_client, get_usage, list_voices, synthesize
from .wizard import run_wizard


def _common_options(f):
    f = click.option("--api-key", default=None, help="ElevenLabs API key (overrides env/.env).")(f)
    # Defaults stay None so a flag can be told apart from "unset" — the
    # built-in default is applied further down, by resolve_settings.
    f = click.option("--voice", "voice_id", default=None,
                      help=f"Voice ID to use (default: {DEFAULT_VOICE}).")(f)
    f = click.option("--model", "model_id", default=None,
                      help=f"ElevenLabs model ID (default: {DEFAULT_MODEL}).")(f)
    f = click.option("--speed", type=float, default=None,
                      help="Speech speed, 0.7–1.2; 1.0 is normal.")(f)
    f = click.option("-o", "--output", "output_path", type=click.Path(path_type=Path), default=None,
                      help="Save the generated audio to this path (default: "
                      "~/.cache/vocalize/last.mp3, overwritten each run).")(f)
    f = click.option("--play/--no-play", default=True, help="Play the audio after generating it.")(f)
    f = click.option("--raw", is_flag=True, help="Skip markdown flattening; speak the text verbatim.")(f)
    f = click.option("--max-chars", type=int, default=None, help="Truncate input to this many characters first.")(f)
    f = click.option("--chunk-chars", type=click.IntRange(min=1), default=None,
                      help="Split long input into chunks of at most this many characters before sending "
                      f"each to ElevenLabs (default: {DEFAULT_CHUNK_CHARS}; the eleven_multilingual_v2 "
                      "model caps a single request at 10,000 characters).")(f)
    f = click.option("--overflow", type=click.Choice(OVERFLOW_MODES), default=None,
                      help="What to do when input exceeds the cap: truncate it (the default), "
                      "ask first, or never truncate.")(f)
    f = click.option("--default-max-chars", type=click.IntRange(min=1), default=None,
                      help="Fallback cap used only when no --max-chars, VOCALIZE_MAX_CHARS, or "
                      "config file value sets one. For wrapper scripts; most users want --max-chars.")(f)
    return f


@click.group()
@click.version_option(__version__, prog_name="vocalize")
def main() -> None:
    """Turn text, markdown, or piped stdin into speech via ElevenLabs."""


def _ask_to_truncate(input_chars: int, cap: int) -> bool | None:
    """Ask on the controlling terminal whether to truncate. None = no terminal.

    The prompt uses /dev/tty, not stdin/stdout: stdin may be the very text
    being spoken (`speak-file -`) and stdout may be piped — the same
    reasoning as the config wizard.
    """
    try:
        with open("/dev/tty", "r", encoding="utf-8") as tty_in, \
             open("/dev/tty", "w", encoding="utf-8") as tty_out:
            tty_out.write(
                f"Input is {input_chars:,} characters; the cap is {cap:,}. "
                f"Truncate to {cap:,}? [Y/n] "
            )
            tty_out.flush()
            answer = tty_in.readline()
    except OSError:
        return None
    if not answer:  # EOF — no human on the other end after all
        return None
    return answer.strip().lower() not in ("n", "no")


def _run_tts(raw_text: str, *, api_key, voice_id, model_id, speed, output_path, play, raw, max_chars,
             chunk_chars, overflow, default_max_chars) -> None:
    text = raw_text if raw else flatten_markdown(raw_text)

    # Parsed once here and shared by both resolvers, so a config-file typo
    # warns once per run, not once per resolver.
    file_config = load_config_file()

    mode, cap = resolve_overflow(overflow, max_chars, default_max_chars, file_config=file_config)
    if mode == "never":
        cap = None
    elif mode == "ask" and cap is not None and len(text) > cap:
        answer = _ask_to_truncate(len(text), cap)
        if answer is None:
            click.echo(
                f"Note: overflow is 'ask' but there is no terminal to ask on; "
                f"truncating to {cap} characters instead.", err=True,
            )
        elif answer is False:
            cap = None

    text, truncated = truncate_for_budget(text, cap)
    if truncated:
        click.echo(f"Note: input truncated to {cap} characters.", err=True)

    if not text.strip():
        raise TTSRequestError("Nothing to speak: input text is empty.")

    settings = resolve_settings(voice_id=voice_id, model_id=model_id, speed=speed,
                                file_config=file_config)
    key = resolve_api_key(api_key)
    client = build_client(key)

    chunks = split_for_synthesis(text, chunk_chars or DEFAULT_CHUNK_CHARS)
    if len(chunks) == 1:
        click.echo(f"Requesting {len(text)} characters of audio from ElevenLabs...", err=True)
        audio = synthesize(client, text, settings)
    else:
        n = len(chunks)
        click.echo(f"Long input: splitting into {n} chunks.", err=True)
        parts = []
        for i, chunk in enumerate(chunks, start=1):
            click.echo(f"Requesting chunk {i}/{n} ({len(chunk)} characters) from ElevenLabs...", err=True)
            parts.append(synthesize(client, chunk, settings))
        audio = b"".join(parts)

    dest = output_path or (DEFAULT_CACHE_DIR / "last.mp3")
    save_audio(audio, dest)
    click.echo(f"Saved audio to {dest}", err=True)

    if play:
        play_audio(dest)


@main.command()
@click.argument("text")
@_common_options
def speak(text, api_key, voice_id, model_id, speed, output_path, play, raw, max_chars,
          chunk_chars, overflow, default_max_chars) -> None:
    """Speak TEXT directly."""
    _run_tts(text, api_key=api_key, voice_id=voice_id, model_id=model_id, speed=speed,
              output_path=output_path, play=play, raw=raw, max_chars=max_chars,
              chunk_chars=chunk_chars, overflow=overflow, default_max_chars=default_max_chars)


@main.command("speak-file")
@click.argument("path", type=click.File("r", encoding="utf-8"))
@_common_options
def speak_file(path, api_key, voice_id, model_id, speed, output_path, play, raw, max_chars,
               chunk_chars, overflow, default_max_chars) -> None:
    """Speak the contents of PATH (a markdown/text file), or "-" for stdin."""
    try:
        raw_text = path.read()
    except UnicodeDecodeError as exc:
        raise click.FileError(getattr(path, "name", "input"), hint="file is not valid UTF-8 text") from exc
    _run_tts(raw_text, api_key=api_key, voice_id=voice_id, model_id=model_id, speed=speed,
              output_path=output_path, play=play, raw=raw, max_chars=max_chars,
              chunk_chars=chunk_chars, overflow=overflow, default_max_chars=default_max_chars)


@main.command()
def stop() -> None:
    """Stop any audio vocalize is currently playing."""
    if stop_playback():
        click.echo("Stopped playback.")
    else:
        click.echo("Nothing is playing.")


@main.command()
@click.option("--api-key", default=None)
def voices(api_key) -> None:
    """List available ElevenLabs voices and their IDs."""
    key = resolve_api_key(api_key)
    client = build_client(key)
    for v in list_voices(client):
        click.echo(f"{v['id']}\t{v['name']}")


def _human_readable_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


@main.command()
@click.option("--api-key", default=None)
def usage(api_key) -> None:
    """Show ElevenLabs quota usage and local cache stats."""
    key = resolve_api_key(api_key)
    client = build_client(key)
    stats = get_usage(client)

    used, limit = stats["used"], stats["limit"]
    percent = (used / limit * 100) if limit else 0.0
    remaining = max(limit - used, 0)

    click.echo(f"Tier: {stats['tier']}")
    click.echo(f"Used: {used:,} / {limit:,} characters ({percent:.1f}%)")
    click.echo(f"Remaining: {remaining:,} characters")
    if stats["resets_at"] is not None:
        # UTC-aware, then converted to the system's local zone explicitly —
        # a bare fromtimestamp() is implicitly local, which ruff (DTZ006)
        # flags as ambiguous.
        local_reset = datetime.fromtimestamp(stats["resets_at"], tz=timezone.utc).astimezone()
        click.echo(f"Resets: {local_reset.strftime('%Y-%m-%d')}")

    click.echo("")
    # Local cache stats are pure filesystem lookups — no API call, no quota.
    cache_files = list(DEFAULT_CACHE_DIR.glob("*.mp3")) if DEFAULT_CACHE_DIR.is_dir() else []
    if not cache_files:
        click.echo("Local cache: cache empty")
    else:
        total_bytes = sum(f.stat().st_size for f in cache_files)
        click.echo(f"Local cache: {len(cache_files)} files, {_human_readable_size(total_bytes)}")


@main.command("config")
def config_cmd() -> None:
    """Interactive setup: pick voice, model, and speed."""
    run_wizard()


@main.group()
def auth() -> None:
    """Store, inspect, or remove the ElevenLabs API key."""


@auth.command("login")
@click.option("--stdin", "from_stdin", is_flag=True,
              help="Read the key from stdin instead of prompting, for piping from a secret manager.")
def auth_login(from_stdin) -> None:
    """Validate an API key and save it in the system keychain."""
    key = sys.stdin.readline().strip() if from_stdin else prompt_for_key()
    if not key:
        raise click.ClickException("No API key given — nothing was stored.")
    try:
        click.echo(login(key))
    except VocalizeError as exc:
        # Raised before anything is written, so a rejected key leaves
        # whatever was already stored untouched.
        raise click.ClickException(str(exc)) from exc


@auth.command("status")
def auth_status() -> None:
    """Report where the API key comes from, without revealing it."""
    source = key_source(None)
    if source != "not found":
        click.echo(f"API key source: {source}")
        click.echo(f"Key: {masked(resolve_api_key())}")
        return

    # key_source flattens an unreadable keychain into "not found" so that
    # resolution never crashes. A status command must not repeat that lie.
    status, reason = probe_keychain()
    if status == "error":
        click.echo(f"API key source: keychain unavailable ({reason})")
        click.echo("Unlock your keychain and try again, or run `vocalize auth login`.")
        return
    click.echo("API key source: not found")
    click.echo("Run `vocalize auth login` to store one in your system keychain.")


@auth.command("logout")
def auth_logout() -> None:
    """Remove the stored API key from the system keychain."""
    try:
        delete_key()
    except VocalizeError as exc:
        raise click.ClickException(str(exc)) from exc
    # delete_key reads the entry back, so this line is a fact, not a hope.
    click.echo("Removed the stored API key from the system keychain.")


def run() -> None:
    """Entry point that turns our VocalizeError family into clean CLI errors."""
    try:
        main()
    except VocalizeError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    run()
