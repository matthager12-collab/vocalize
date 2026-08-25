"""Command-line interface for vocalize.

    vocalize speak "some text" --play
    vocalize speak-file report.md --play
    cat notes.md | vocalize speak-file - --play
    vocalize voices
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from . import __version__
from .config import DEFAULT_MODEL, DEFAULT_VOICE, Settings, resolve_api_key
from .exceptions import TTSRequestError, VocalizeError
from .preprocess import flatten_markdown, truncate_for_budget
from .tts import DEFAULT_CACHE_DIR, build_client, list_voices, synthesize
from .audio import play as play_audio
from .audio import save as save_audio


def _common_options(f):
    f = click.option("--api-key", default=None, help="ElevenLabs API key (overrides env/.env).")(f)
    f = click.option("--voice", "voice_id", default=DEFAULT_VOICE, show_default=True, help="Voice ID to use.")(f)
    f = click.option("--model", "model_id", default=DEFAULT_MODEL, show_default=True, help="ElevenLabs model ID.")(f)
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


def _run_tts(raw_text: str, *, api_key, voice_id, model_id, output_path, play, raw, max_chars) -> None:
    text = raw_text if raw else flatten_markdown(raw_text)
    text, truncated = truncate_for_budget(text, max_chars)
    if truncated:
        click.echo(f"Note: input truncated to {max_chars} characters.", err=True)

    if not text.strip():
        raise TTSRequestError("Nothing to speak: input text is empty.")

    key = resolve_api_key(api_key)
    client = build_client(key)
    settings = Settings(voice_id=voice_id, model_id=model_id)

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
def speak(text, api_key, voice_id, model_id, output_path, play, raw, max_chars) -> None:
    """Speak TEXT directly."""
    _run_tts(text, api_key=api_key, voice_id=voice_id, model_id=model_id,
              output_path=output_path, play=play, raw=raw, max_chars=max_chars)


@main.command("speak-file")
@click.argument("path", type=click.File("r", encoding="utf-8"))
@_common_options
def speak_file(path, api_key, voice_id, model_id, output_path, play, raw, max_chars) -> None:
    """Speak the contents of PATH (a markdown/text file), or "-" for stdin."""
    try:
        raw_text = path.read()
    except UnicodeDecodeError as exc:
        raise click.FileError(getattr(path, "name", "input"), hint="file is not valid UTF-8 text") from exc
    _run_tts(raw_text, api_key=api_key, voice_id=voice_id, model_id=model_id,
              output_path=output_path, play=play, raw=raw, max_chars=max_chars)


@main.command()
@click.option("--api-key", default=None)
def voices(api_key) -> None:
    """List available ElevenLabs voices and their IDs."""
    key = resolve_api_key(api_key)
    client = build_client(key)
    for v in list_voices(client):
        click.echo(f"{v['id']}\t{v['name']}")


def run() -> None:
    """Entry point that turns our VocalizeError family into clean CLI errors."""
    try:
        main()
    except VocalizeError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    run()
