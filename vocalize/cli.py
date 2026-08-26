"""Command-line interface for vocalize.

    vocalize speak "some text" --play
    vocalize speak-file report.md --play
    cat notes.md | vocalize speak-file - --play
    vocalize voices
    vocalize config
    vocalize auth login
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from . import __version__
from .audio import play as play_audio
from .audio import save as save_audio
from .auth import delete_key, key_source, login, masked, probe_keychain, prompt_for_key
from .config import DEFAULT_MODEL, DEFAULT_VOICE, resolve_api_key, resolve_settings
from .exceptions import TTSRequestError, VocalizeError
from .preprocess import flatten_markdown, truncate_for_budget
from .tts import DEFAULT_CACHE_DIR, build_client, list_voices, synthesize
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
    return f


@click.group()
@click.version_option(__version__, prog_name="vocalize")
def main() -> None:
    """Turn text, markdown, or piped stdin into speech via ElevenLabs."""


def _run_tts(raw_text: str, *, api_key, voice_id, model_id, speed, output_path, play, raw, max_chars) -> None:
    text = raw_text if raw else flatten_markdown(raw_text)
    text, truncated = truncate_for_budget(text, max_chars)
    if truncated:
        click.echo(f"Note: input truncated to {max_chars} characters.", err=True)

    if not text.strip():
        raise TTSRequestError("Nothing to speak: input text is empty.")

    settings = resolve_settings(voice_id=voice_id, model_id=model_id, speed=speed)
    key = resolve_api_key(api_key)
    client = build_client(key)

    click.echo(f"Requesting {len(text)} characters of audio from ElevenLabs...", err=True)
    audio = synthesize(client, text, settings)

    dest = output_path or (DEFAULT_CACHE_DIR / "last.mp3")
    save_audio(audio, dest)
    click.echo(f"Saved audio to {dest}", err=True)

    if play:
        play_audio(dest)


@main.command()
@click.argument("text")
@_common_options
def speak(text, api_key, voice_id, model_id, speed, output_path, play, raw, max_chars) -> None:
    """Speak TEXT directly."""
    _run_tts(text, api_key=api_key, voice_id=voice_id, model_id=model_id, speed=speed,
              output_path=output_path, play=play, raw=raw, max_chars=max_chars)


@main.command("speak-file")
@click.argument("path", type=click.File("r", encoding="utf-8"))
@_common_options
def speak_file(path, api_key, voice_id, model_id, speed, output_path, play, raw, max_chars) -> None:
    """Speak the contents of PATH (a markdown/text file), or "-" for stdin."""
    try:
        raw_text = path.read()
    except UnicodeDecodeError as exc:
        raise click.FileError(getattr(path, "name", "input"), hint="file is not valid UTF-8 text") from exc
    _run_tts(raw_text, api_key=api_key, voice_id=voice_id, model_id=model_id, speed=speed,
              output_path=output_path, play=play, raw=raw, max_chars=max_chars)


@main.command()
@click.option("--api-key", default=None)
def voices(api_key) -> None:
    """List available ElevenLabs voices and their IDs."""
    key = resolve_api_key(api_key)
    client = build_client(key)
    for v in list_voices(client):
        click.echo(f"{v['id']}\t{v['name']}")


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
