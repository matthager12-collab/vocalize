"""Command-line interface for vocalize.

    vocalize speak "some text" --play
    vocalize speak-file report.md --play
    cat notes.md | vocalize speak-file - --play
    vocalize clip
    vocalize stop
    vocalize settings
    vocalize voices
    vocalize usage
    vocalize config
    vocalize auth login
"""

from __future__ import annotations

import json
import math
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import click

from . import __version__, dictate, interrupted, ledger, providers, wizard
from .audio import play as play_audio
from .audio import play_sequence, stop_playback, take_gap_stop
from .audio import save as save_audio
from .auth import (
    PROVIDER_LABELS,
    PROVIDER_NAMES,
    PROVIDER_USERNAMES,
    delete_key,
    key_source,
    login,
    masked,
    polly_credential_status,
    probe_keychain,
    prompt_for_key,
    stored_key,
)
from .chain import run as chain_run
from .chain import unheard_text as chain_unheard
from .clipboard import read_clipboard
from .config import (
    DEFAULT_MODEL,
    DEFAULT_VOICE,
    OVERFLOW_MODES,
    budget_for,
    chain_source,
    config_path,
    load_config_file,
    resolve_api_key,
    resolve_chain,
    resolve_overflow,
    resolve_provider_settings,
    resolve_settings,
    resolve_stt,
)
from .exceptions import (
    AudioPlaybackError,
    DictationError,
    MissingAPIKeyError,
    PlaybackStopped,
    TTSRequestError,
    VocalizeError,
)
from .preprocess import (
    DEFAULT_CHUNK_CHARS,
    flatten_markdown,
    truncate_for_budget,
)
from .readiness import readiness
from .tts import DEFAULT_CACHE_DIR, build_client, get_usage, list_voices
from .wizard import run_wizard


def _common_options(f):
    f = click.option("--api-key", default=None, help="ElevenLabs API key (overrides env/.env).")(f)
    f = click.option("--provider", type=click.Choice(PROVIDER_NAMES), default=None,
                      help="Force one provider and turn fallback off (default: the "
                      "configured chain).")(f)
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
                      "~/.cache/vocalize/last.<ext> — mp3, m4a or wav depending on the "
                      "provider — overwritten each run).")(f)
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
    f = click.option("--ask-dialog", is_flag=True, default=False,
                      help="When overflow is 'ask' and there is no terminal, ask via a macOS "
                      "dialog instead of degrading to truncate. For GUI launchers like the "
                      "Quick Action; never used by the Claude Code Stop hook.")(f)
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


_DIALOG_TIMEOUT_SECONDS = 30


def _ask_to_truncate_dialog(input_chars: int, cap: int) -> str:
    """Ask via a macOS dialog when there is no /dev/tty.

    Returns "truncate", "all", or "cancel". Only reached under --ask-dialog
    (the Quick Action); the Stop hook keeps its silent degrade. The dialog
    text contains only integers vocalize computed — never spoken content —
    so nothing user- or attacker-controlled is interpolated into the
    AppleScript source.
    """
    script = (
        f'display dialog "Input is {input_chars:,} characters; the cap is {cap:,}. '
        f'What should vocalize do?" with title "vocalize" '
        f'buttons {{"Cancel", "Speak all", "Truncate"}} '
        f'default button "Truncate" cancel button "Cancel" '
        f'giving up after {_DIALOG_TIMEOUT_SECONDS}'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=_DIALOG_TIMEOUT_SECONDS + 10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "cancel"  # can't confirm intent → speak nothing
    if result.returncode != 0:
        return "cancel"  # Cancel / Esc raises AppleScript error -128
    if "Speak all" in result.stdout:
        return "all"
    # Explicit Truncate click, or the "giving up after" timeout — a
    # given-up dialog exits 0 with an EMPTY button name, so anything that
    # isn't "Speak all" must resolve to truncate, never to speaking more.
    return "truncate"


_CREDENTIAL_PREFIXES = (
    "sk-", "sk_live_", "sk_test_", "pk_live_", "pk_test_", "rk_live_",
    "pypi-", "ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_",
    "op://", "eyJ", "xoxb-", "xoxp-", "xoxa-", "xoxc-", "AKIA", "ASIA",
    "AIza", "glpat-", "npm_", "shpat_", "shpss_", "sq0atp-", "sq0csp-",
)
_CREDENTIAL_MIN_LENGTH = 20
_CREDENTIAL_CHARSET = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+/=_.-"
)
_CREDENTIAL_ENTROPY_THRESHOLD = 3.5  # bits per character


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in Counter(s).values())


def _looks_like_credential(text: str) -> bool:
    """Guard against speaking a secret copied from a password manager.

    Refuses a SINGLE whitespace-free token that starts with a known secret
    prefix, or that is long, token-charset only, mixes letters and digits,
    and is high-entropy — the shape of a generated key, not of a word,
    sentence, or URL. It exists to interrupt habit, not to be an airtight
    scanner, so it never inspects multi-word text at all.
    """
    stripped = text.strip()
    if not stripped or any(ch.isspace() for ch in stripped):
        return False
    if stripped.startswith(_CREDENTIAL_PREFIXES):
        return True
    if len(stripped) < _CREDENTIAL_MIN_LENGTH:
        return False
    if "://" in stripped:
        return False  # a bare URL; op:// already matched as a prefix
    if not set(stripped) <= _CREDENTIAL_CHARSET:
        return False
    if not (any(c.isalpha() for c in stripped) and any(c.isdigit() for c in stripped)):
        return False
    return _shannon_entropy(stripped) >= _CREDENTIAL_ENTROPY_THRESHOLD


class _StreamPlayer:
    """Plays finished pieces on a background thread while the next renders.

    `afplay` blocks, so a thread with a one-slot queue is the whole queue:
    handing over piece i+1 waits until piece i has started playing, which
    is exactly the backpressure we want. Each piece is copied out of the
    chain's temporary directory first — that directory is gone the moment
    chain.run returns, and the last piece is usually still playing.
    """

    def __init__(self, workdir: Path) -> None:
        self._workdir = workdir
        self._queue: queue.Queue = queue.Queue(maxsize=1)
        self._stopped = threading.Event()
        # A stop older than this read is somebody else's; see take_gap_stop.
        self._started = time.time()
        self.pieces = 0
        self.error: BaseException | None = None
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def _drain(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            if self._stopped.is_set():
                continue  # keep draining so put() never blocks forever
            if take_gap_stop(item, self._started):
                # The stop landed while the last piece had finished and
                # this one was still rendering: no player was running, so
                # nothing was killed and this piece has never been heard.
                # Playing it now would read into the open microphone.
                self._stopped.set()
                continue
            try:
                if not play_sequence([item]):
                    self._stopped.set()
            except Exception as exc:  # noqa: BLE001 — see below
                # A dead player thread would wedge the render loop on its
                # next put() forever, so the failure is carried back to the
                # main thread instead of ending the thread here.
                self.error = exc
                self._stopped.set()

    def on_chunk(self, path: Path) -> bool:
        """Take the piece and queue it. False once the user has stopped."""
        if self._stopped.is_set():
            return False
        own_copy = self._workdir / path.name
        own_copy.write_bytes(path.read_bytes())
        self.pieces += 1
        self._queue.put(own_copy)
        return not self._stopped.is_set()

    @property
    def stopped(self) -> bool:
        """Whether a piece of this read was stopped mid-playback."""
        return self._stopped.is_set()

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join()


def _run_tts(raw_text: str, *, api_key, voice_id, model_id, speed, output_path, play, raw, max_chars,
             chunk_chars, overflow, default_max_chars, ask_dialog=False, provider=None) -> None:
    if api_key and provider is not None and provider != "elevenlabs":
        raise click.UsageError(
            f"--api-key only applies to ElevenLabs; use `vocalize auth login "
            f"--provider {provider}` or the provider's env var"
        )

    # When this read began, for `take_gap_stop`: a stop older than this
    # belongs to somebody else and must never silence this read.
    read_started = time.time()

    text = raw_text if raw else flatten_markdown(raw_text)

    # Parsed once here and shared by both resolvers, so a config-file typo
    # warns once per run, not once per resolver.
    file_config = load_config_file()

    mode, cap = resolve_overflow(overflow, max_chars, default_max_chars, file_config=file_config)
    if mode == "never":
        cap = None
    elif mode == "ask" and cap is not None and len(text) > cap:
        answer = _ask_to_truncate(len(text), cap)
        if answer is None and ask_dialog:
            outcome = _ask_to_truncate_dialog(len(text), cap)
            if outcome == "cancel":
                click.echo("Note: cancelled at the truncation prompt; nothing spoken.", err=True)
                return
            answer = outcome == "truncate"
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

    chain = resolve_chain(provider, file_config)
    overrides = {"voice_id": voice_id, "model_id": model_id, "speed": speed,
                 "api_key": api_key}
    # What a resume has to be given back to be the *same* read. Built
    # separately from `overrides` so `api_key` cannot follow it onto disk.
    spoken_with = {"voice_id": voice_id, "model_id": model_id, "speed": speed,
                   "chunk_chars": chunk_chars}

    def echo(message: str) -> None:
        click.echo(message, err=True)

    with tempfile.TemporaryDirectory(prefix="vocalize-play-") as workdir:
        player = _StreamPlayer(Path(workdir)) if play else None
        try:
            audio, spoke_as, ext = chain_run(
                text, chain=chain, file_config=file_config, overrides=overrides,
                chunk_chars=chunk_chars, forced=provider is not None,
                echo=echo, on_chunk=player.on_chunk if player else None,
            )
        except PlaybackStopped as stopped:
            # The piece that was playing is still in `workdir` here: the
            # chain's own copy went with its temporary directory, and
            # player.close() has not run yet (it is in the `finally`).
            interrupted.remember_stop(
                stopped.remaining_text, stopped.provider, stopped.audio_ext, spoken_with
            )
            if player is not None and player.error is not None:
                # The player broke, not the render: everything spoken so far
                # was already paid for, so it gets saved before we complain.
                if stopped.audio:
                    dest = output_path or (DEFAULT_CACHE_DIR / f"last.{stopped.audio_ext}")
                    save_audio(stopped.audio, dest)
                    click.echo(f"Saved audio to {dest}", err=True)
                raise AudioPlaybackError(str(player.error)) from player.error
            click.echo("Stopped.", err=True)
            return
        finally:
            if player is not None:
                player.close()

        if player is not None and player.stopped:
            # The third stop site. A read whose pieces are all cached
            # renders far faster than it plays, so every piece can be
            # handed over before the stop lands — `chain.run` then returns
            # normally and nothing was ever raised, but the read was still
            # cut off mid-sentence (found by the live drill, run 5).
            interrupted.remember_stop(chain_unheard(ext), spoke_as, ext, spoken_with)

        dest = output_path or (DEFAULT_CACHE_DIR / f"last.{ext}")
        save_audio(audio, dest)
        click.echo(f"Saved audio to {dest}", err=True)

        # A streaming provider already played every piece as it landed;
        # playing the joined file now would read the whole thing twice.
        if play and (player is None or player.pieces == 0):
            # A stop that landed while this read was still inside
            # `synthesize()` killed nothing — there was no player yet — so
            # without this the whole read would play seconds later, into
            # the microphone that stop was opening (DEC-013).
            stopped_before_it_started = take_gap_stop(dest, read_started)
            if stopped_before_it_started:
                click.echo("Stopped.", err=True)
            if stopped_before_it_started or play_audio(dest) == -signal.SIGTERM:
                # Nothing was left unspoken: the file is the whole read.
                interrupted.remember_stop("", spoke_as, ext, spoken_with)
        elif player is not None and player.error is not None:
            # Every piece rendered and the file is saved; only playback
            # broke. Say so rather than exiting 0 on a silent read.
            raise AudioPlaybackError(str(player.error))


@main.command()
@click.argument("text")
@_common_options
def speak(text, api_key, provider, voice_id, model_id, speed, output_path, play, raw, max_chars,
          chunk_chars, overflow, default_max_chars, ask_dialog) -> None:
    """Speak TEXT directly."""
    _run_tts(text, api_key=api_key, voice_id=voice_id, model_id=model_id, speed=speed,
              output_path=output_path, play=play, raw=raw, max_chars=max_chars,
              chunk_chars=chunk_chars, overflow=overflow, default_max_chars=default_max_chars,
              ask_dialog=ask_dialog, provider=provider)


@main.command("speak-file")
@click.argument("path", type=click.File("r", encoding="utf-8"))
@_common_options
def speak_file(path, api_key, provider, voice_id, model_id, speed, output_path, play, raw,
               max_chars, chunk_chars, overflow, default_max_chars, ask_dialog) -> None:
    """Speak the contents of PATH (a markdown/text file), or "-" for stdin."""
    try:
        raw_text = path.read()
    except UnicodeDecodeError as exc:
        raise click.FileError(getattr(path, "name", "input"), hint="file is not valid UTF-8 text") from exc
    _run_tts(raw_text, api_key=api_key, voice_id=voice_id, model_id=model_id, speed=speed,
              output_path=output_path, play=play, raw=raw, max_chars=max_chars,
              chunk_chars=chunk_chars, overflow=overflow, default_max_chars=default_max_chars,
              ask_dialog=ask_dialog, provider=provider)


@main.command()
@click.option("--allow-secret", is_flag=True,
              help="Bypass the credential-shaped-clipboard guard. Only when you are "
              "certain the clipboard is not a secret.")
@_common_options
def clip(allow_secret, api_key, provider, voice_id, model_id, speed, output_path, play, raw,
         max_chars, chunk_chars, overflow, default_max_chars, ask_dialog) -> None:
    """Speak the current macOS clipboard contents."""
    text = read_clipboard()
    if not text.strip():
        raise TTSRequestError("Clipboard is empty; nothing to speak.")
    if not allow_secret and _looks_like_credential(text):
        # Deliberately does not echo any part of the clipboard.
        raise click.ClickException(
            "Refusing to speak: the clipboard looks like a secret or credential "
            "(a single high-entropy token). It was not shown or sent anywhere. "
            "If you are sure it is safe, re-run with --allow-secret."
        )
    if play:
        stop_playback()  # fresh content replaces whatever is mid-playback
    _run_tts(text, api_key=api_key, voice_id=voice_id, model_id=model_id, speed=speed,
              output_path=output_path, play=play, raw=raw, max_chars=max_chars,
              chunk_chars=chunk_chars, overflow=overflow, default_max_chars=default_max_chars,
              ask_dialog=ask_dialog, provider=provider)


@main.command()
def settings() -> None:
    """Print the resolved settings, env and config precedence applied.

    One key=value per line, made for wrapper scripts (the /speak slash
    command reads overflow and max_chars from here) — no API call, no key
    needed.
    """
    file_config = load_config_file()
    resolved = resolve_settings(file_config=file_config)
    mode, cap = resolve_overflow(file_config=file_config)
    click.echo(f"voice={resolved.voice_id}")
    click.echo(f"model={resolved.model_id}")
    click.echo(f"speed={resolved.speed if resolved.speed is not None else 'unset'}")
    click.echo(f"max_chars={cap if cap is not None else 'unset'}")
    click.echo(f"overflow={mode}")
    click.echo(f"chain={','.join(resolve_chain(None, file_config))}")
    # Additive, per DEC-006: hooks/speak_options.py reads only the lines it
    # knows and ignores the rest, so new keys can never break the picker.
    stt = resolve_stt(file_config)
    click.echo(f"stt.model={stt['model']}")
    click.echo(f"stt.language={stt['language']}")
    click.echo(f"stt.cleanup={'true' if stt['cleanup'] else 'false'}")
    click.echo(f"stt.max_seconds={stt['max_seconds']}")
    click.echo(f"stt.cues={stt['cues']}")


@main.command()
@click.argument("provider_names", nargs=-1)
def chain(provider_names) -> None:
    """Show the resolved provider chain, or set a new one in the config file.

        vocalize chain                    # show the resolved order and source
        vocalize chain google polly say   # write a new chain to config.toml
    """
    file_config = load_config_file()

    if not provider_names:
        click.echo(f"chain={','.join(resolve_chain(None, file_config))}")
        click.echo(f"source={chain_source(None, file_config)}")
        return

    seen: set[str] = set()
    for name in provider_names:
        if name not in PROVIDER_NAMES:
            raise click.UsageError(
                f"Unknown provider {name!r}. Known: {', '.join(PROVIDER_NAMES)}"
            )
        if name in seen:
            raise click.UsageError(f"Duplicate provider {name!r} in the chain.")
        seen.add(name)

    path = config_path()
    # Fingerprint first, then read. The other order calls a write that
    # landed in between "unchanged" and clobbers it; this order refuses a
    # write that raced us, which is the safe direction (DEC-005).
    fingerprint = wizard.fingerprint_config(path)
    data = dict(load_config_file())
    data["chain"] = list(provider_names)
    wizard._render_config_text(data)  # fail fast before writing anything
    wizard.write_config_if_unchanged(path, data, fingerprint)
    click.echo(f"chain={','.join(provider_names)}")
    click.echo(f"wrote {path}")


def resume_interrupted() -> bool:
    """Continue the read a dictation stopped. False if there was none.

    Plays the saved piece from where the stop landed, then speaks whatever
    was never rendered through the normal path — same provider, same cache,
    same budget gate and the same playback lock — so anything already
    rendered is a cache hit and the continuation starts at once (DEC-003).

    Called by `vocalize resume` and by the dialog `dictate` shows after a
    transcript lands.

    The record outlives every way this can go wrong (DEC-012): a dictation
    that stops the replay re-records what is left of it, and a continuation
    that fails before it speaks leaves the record where it was. It is
    deleted once, at the end, and only if nothing has replaced it.
    """
    record = interrupted.load()
    if record is None:
        return False
    with tempfile.TemporaryDirectory(prefix="vocalize-resume-") as workdir:
        piece = interrupted.slice_from(record, Path(workdir))
        if piece is None:
            if not record.text.strip():
                # Nothing to play and nothing to say — a stop in the last
                # moments of a read, or audio this machine cannot convert.
                # Say so through "Nothing to resume" rather than exiting 0
                # in silence having quietly discarded the record.
                interrupted.forget()
                return False
            click.echo("Could not replay the saved audio; reading on from the text.",
                       err=True)
        elif play_audio(piece) == -signal.SIGTERM:
            # A dictation stopped the replay too: the rest of the slice and
            # the same text become the new record, so the read is still
            # there to continue. A plain `vocalize stop` drops it, as it
            # drops any other read.
            kept = {key: getattr(record, key) for key in interrupted.SETTING_KEYS}
            if not interrupted.remember_stop(record.text, record.provider, "wav", kept):
                interrupted.forget()
            click.echo("Stopped.", err=True)
            return True
    if record.text.strip():
        # raw=True: this text was flattened before it was ever spoken.
        # Anything raised here leaves the record for another try.
        # The voice, model, speed and chunking the read was stopped in.
        # Without them the rest is spoken in the config-default voice, and
        # every already-rendered chunk misses the cache and is paid for a
        # second time (DEC-014).
        _run_tts(record.text, api_key=None, voice_id=record.voice_id,
                  model_id=record.model_id, speed=record.speed,
                  output_path=None, play=True, raw=True, max_chars=None,
                  chunk_chars=record.chunk_chars,
                  overflow=None, default_max_chars=None, provider=record.provider)
    again = interrupted.load()
    if again is not None and again.saved_at == record.saved_at:
        # Still the same record: the read is done with. A stop during the
        # continuation wrote a newer one, and that is not this one to take.
        interrupted.forget()
    return True


@main.command()
@click.option("--forget", "forget_only", is_flag=True,
              help="Discard the interrupted read instead of continuing it.")
def resume(forget_only) -> None:
    """Continue a read a dictation interrupted.

    Plays the piece that was cut off from where it stopped, then reads on
    through the rest of the text. Nothing is kept for more than an hour.
    """
    if forget_only:
        interrupted.forget()
        click.echo("Discarded the interrupted read.")
        return
    if not resume_interrupted():
        click.echo("Nothing to resume.")


@main.command()
def stop() -> None:
    """Stop any audio vocalize is currently playing."""
    if stop_playback():
        click.echo("Stopped playback.")
    else:
        click.echo("Nothing is playing.")


@main.command()
@click.option("--api-key", default=None)
@click.option("--provider", type=click.Choice(PROVIDER_NAMES), default="elevenlabs",
              help="Provider to list voices for (default: elevenlabs).")
def voices(api_key, provider) -> None:
    """List available voices and their IDs."""
    if provider != "elevenlabs" and api_key:
        raise click.UsageError("--api-key only applies to ElevenLabs")

    if provider == "elevenlabs":
        key = resolve_api_key(api_key)
        client = build_client(key)
        for v in list_voices(client):
            click.echo(f"{v['id']}\t{v['name']}")
        return

    for v in providers.get(provider).list_voices():
        click.echo(f"{v['id']}\t{v['name']}")


def _human_readable_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _count_unit(name: str) -> str:
    """'bytes' for google (its limits and billing are byte-based); else 'characters'."""
    if name == "google":
        return providers.get("google").COUNT_UNIT
    return "characters"


_LOCAL_PROVIDERS = ("say", "kokoro")


def _ledger_line(name: str, file_config: dict) -> str:
    if name in _LOCAL_PROVIDERS:
        return f"{name}: local provider, no quota"
    used, exhausted = ledger.status(name)
    budget = budget_for(name, file_config)
    unit = _count_unit(name)
    # A provider a real quota error marked exhausted has to say so whether
    # or not a local budget was ever set — the no-budget line is the common
    # case, and used to hide it.
    if budget:
        percent = used / budget * 100
        flag = " EXHAUSTED" if exhausted or used >= budget else ""
        return f"{name}: {used:,} / {budget:,} {unit} ({percent:.1f}%){flag}"
    flag = " EXHAUSTED" if exhausted else ""
    return f"{name}: {used:,} {unit} (no monthly_chars set — unlimited){flag}"


@main.command()
@click.option("--api-key", default=None)
def usage(api_key) -> None:
    """Show per-provider budget usage, ElevenLabs quota, and local cache stats."""
    file_config = load_config_file()
    for name in PROVIDER_NAMES:
        click.echo(_ledger_line(name, file_config))
    click.echo("")

    try:
        key = resolve_api_key(api_key)
    except MissingAPIKeyError:
        click.echo("ElevenLabs remote quota: no key configured, skipped.")
    else:
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
    cache_files = []
    if DEFAULT_CACHE_DIR.is_dir():
        for pattern in ("*.mp3", "*.m4a", "*.wav"):
            cache_files.extend(DEFAULT_CACHE_DIR.glob(pattern))
    if not cache_files:
        click.echo("Local cache: cache empty")
    else:
        total_bytes = sum(f.stat().st_size for f in cache_files)
        click.echo(f"Local cache: {len(cache_files)} files, {_human_readable_size(total_bytes)}")


_STATE_COLORS = {"ok": "green", "warn": "yellow", "fail": "red"}


@main.command()
@click.option("--json", "as_json", is_flag=True,
              help="Print the rows as a JSON list instead of the formatted screen.")
def status(as_json) -> None:
    """Check whether each provider in the chain is ready to speak.

    Exits 0 when every row is ok, 1 otherwise (a warn row included) — so
    this composes with `&&` in a script the way any other check does.
    """
    file_config = load_config_file()
    rows = readiness(file_config)

    if as_json:
        click.echo(json.dumps([row._asdict() for row in rows]))
    else:
        for row in rows:
            label = click.style(f"[{row.state.upper()}]", fg=_STATE_COLORS.get(row.state), bold=True)
            line = f"{label} {row.name}: {row.detail}"
            if row.action:
                line += f" — {row.action}"
            click.echo(line)

    if any(row.state != "ok" for row in rows):
        sys.exit(1)


_RECORDER_TIMEOUT = 20

# LaunchServices, by absolute path — never a bare name resolved against PATH.
_OPEN = "/usr/bin/open"

# "vocalize's own local install is not finished": the recorder is not built,
# the model is missing, or the recorder did not report back. Distinct from the
# recorder's own 0/2/3/5, and the one code `listen --check` adds (DEC-010).
_CHECK_INCOMPLETE = 1

# The recorder's exit codes, in the words `vocalize listen --check` says them
# in — and the one next step each of them needs (design.md § Recorder contract).
_CHECK_NEXT_STEP = {
    0: "Speech-to-text is ready.",
    2: (
        'Allow "Vocalize Recorder" in System Settings › Privacy & Security › '
        "Microphone, then run this again."
    ),
    3: (
        "No usable input device. Connect or select a microphone — "
        "vocalize listen --list-devices shows what macOS can see."
    ),
    5: (
        'macOS has not asked yet. The first dictation prompts for "Vocalize '
        'Recorder"; answer Allow, then run this again.'
    ),
}
_CHECK_WORDS = {0: "authorized", 2: "denied", 3: "unknown", 5: "notDetermined"}


def _recorder_or_exit():
    """The built recorder binary, or a message naming how to build it.

    Never a traceback: `listen --check` is the command a user runs when
    something is already wrong.
    """
    install_module, _ = _stt_modules()
    binary = install_module.recorder_binary()
    if not binary.is_file():
        click.echo(f"Recorder: not built — run: {_STT_INSTALL_HINT}", err=True)
        sys.exit(_CHECK_INCOMPLETE)
    if not install_module.recorder_is_current():
        click.echo(
            f"Recorder: does not match what vocalize built — run: {_STT_INSTALL_HINT}",
            err=True,
        )
        sys.exit(_CHECK_INCOMPLETE)
    return install_module, binary


def _run_recorder(binary: Path, args: list[str]) -> subprocess.CompletedProcess:
    """Run the recorder directly. `--list-devices` only.

    Enumerating input devices touches no permission, so it does not need
    the bundle's launch path. `--check` does — see `_check_via_bundle`.
    """
    try:
        return subprocess.run(
            [str(binary), *args], capture_output=True, text=True,
            timeout=_RECORDER_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise click.ClickException(f"The recorder would not run: {exc}") from exc


def _printable(text: str, fallback: str = "none") -> str:
    """Hardware-supplied text, fit to print.

    Device names come from CoreAudio, which got them from the hardware — a
    USB device names itself — and recorder diagnostics can quote them. So
    everything on this path is cleaned before it reaches a terminal: control
    characters out (no escape sequences), and the same 128-character shape
    the `[stt] input_device` validator uses.
    """
    cleaned = "".join(ch for ch in text if ch.isprintable())[:128].strip()
    return cleaned or fallback


def _check_via_bundle(bundle: Path, device: str = "") -> tuple[int | None, str, str, str]:
    """(exit code, authorization word, device, note) from the recorder,
    launched the way a dictation launches it.

    TCC answers for the *responsible* process, so exec'ing the binary as a
    child of this shell reports whatever the terminal was granted — the
    wrong identity, and never the one dictation runs under. Going through
    LaunchServices makes the bundle its own responsible process, which is
    the whole point of the command. `open -W` relays neither stdout nor the
    app's exit status, so the recorder reports through a file in a
    directory only this user can enter.

    A None code means the recorder never reported; the note says why.

    `device` is the resolved `[stt] input_device`, passed through so the
    check measures the device a dictation would actually use. Without it
    the recorder resolved the *system default* and reported "ready" on a
    machine whose configured microphone was not even connected — which is
    the failure `input_device` exists for (DEC-014). An empty value still
    means the system default and is not passed at all.
    """
    with tempfile.TemporaryDirectory(prefix="vocalize-check-") as workdir:
        status_file = Path(workdir) / "status"
        try:
            launch = subprocess.run(
                [
                    _OPEN, "-W", "-n", "-a", str(bundle),
                    "--args", "--check", "--status-file", str(status_file),
                    *(["--device", device] if device else []),
                ],
                capture_output=True, text=True, timeout=_RECORDER_TIMEOUT, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise click.ClickException(f"The recorder would not run: {exc}") from exc
        try:
            report = status_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            report = ""

    parsed = _parse_status_report(report)
    if parsed is not None:
        return parsed
    detail = (launch.stderr or "").strip().splitlines()
    return None, "unknown", "none", _printable(detail[-1] if detail else "", "")


def _parse_status_report(report: str) -> tuple[int, str, str, str] | None:
    """The recorder's `key: value` status file, or None if it wrote none."""
    fields = {}
    for line in report.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip()] = value.strip()
    try:
        code = int(fields.get("exit", ""))
    except ValueError:
        return None
    return (
        code,
        _printable(fields.get("status", ""), _CHECK_WORDS.get(code, "unknown")),
        _printable(fields.get("device", "")),
        _printable(fields.get("note", ""), ""),
    )


def _installed_stt_models(install_module, manifest) -> list[str]:
    return [
        model for model in manifest.MODELS
        if install_module.installed(
            manifest, files=[manifest.file_for(model)], install_hint=_STT_INSTALL_HINT,
        )[0]
    ]


def _listen_check() -> None:
    install_module, _ = _recorder_or_exit()
    _, manifest = _stt_modules()

    models = _installed_stt_models(install_module, manifest)
    click.echo(f"Model: {', '.join(models) if models else f'none — run: {_STT_INSTALL_HINT}'}")
    bundle = install_module.recorder_bundle()
    click.echo(f"Recorder: {bundle}")

    try:
        wanted = resolve_stt(load_config_file())["input_device"]
    except VocalizeError as exc:
        click.echo(f"Note: {exc}", err=True)
        wanted = ""  # the config is unusable; report on the system default

    code, word, device, note = _check_via_bundle(bundle, wanted)
    click.echo(f"Input device: {device}")
    # Remember the answer. `vocalize status` reports the microphone from
    # this file rather than launching the bundle itself, so a status screen
    # (and the portal polling it) never opens an app.
    # The recorder's own word, not one derived from its exit code: with the
    # microphone granted but no device connected it exits 3 while reporting
    # `status: authorized`, and `_CHECK_WORDS[3]` would have written
    # "unknown" over a grant we do know about (DEC-014). The missing device
    # has its own `vocalize status` row.
    dictate.write_mic_status(
        word if code is not None and word in dictate.MIC_STATUS_WORDS else "incomplete"
    )

    step = _CHECK_NEXT_STEP.get(code)
    if code is None:
        click.echo(
            "Microphone: unknown — the recorder did not report back. "
            f"Rebuild it with: {_STT_INSTALL_HINT}"
        )
        code = _CHECK_INCOMPLETE
    elif step is None:
        click.echo(
            f"Microphone: {word} — the recorder exited with an unexpected status "
            f"{code}. Rebuild it with: {_STT_INSTALL_HINT}"
        )
        code = _CHECK_INCOMPLETE
    elif code == 0 and not models:
        # Never "ready" with nothing to transcribe with. The exit status is
        # what a script or a Quick Action gates on, so it has to disagree
        # with "ready" here too, not just the wording.
        click.echo(
            f"Microphone: {word} — the microphone is ready; install a model "
            f"with: {_STT_INSTALL_HINT}"
        )
        code = _CHECK_INCOMPLETE
    else:
        click.echo(f"Microphone: {word} — {step}")
    if note:
        # Why, when the three-word vocabulary cannot say it: a policy-blocked
        # microphone shows as "denied" in a System Settings pane the user
        # cannot change.
        click.echo(note)
    sys.exit(code)


def _listen_list_devices() -> None:
    _, binary = _recorder_or_exit()
    result = _run_recorder(binary, ["--list-devices"])
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        reason = _printable(detail[-1], f"exit {result.returncode}") if detail else (
            f"exit {result.returncode}"
        )
        click.echo(f"The recorder could not list input devices: {reason}", err=True)
        sys.exit(result.returncode if result.returncode > 0 else _CHECK_INCOMPLETE)
    names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not names:
        click.echo("No input devices found.")
        return
    for name in names:
        printable = _printable(name)
        if printable == name:
            click.echo(printable)
        else:
            # These names are documented as copy-paste values for
            # `[stt] input_device`, and the recorder matches them exactly.
            # A name we had to clean up would never match what it came from.
            click.echo(
                f"{printable}  — cannot be used as [stt] input_device: "
                "the real name has characters this list cannot show"
            )


def _stt_options(cleanup: bool, max_seconds: int | None) -> dict:
    """The `[stt]` table with this invocation's overrides applied.

    `--max-seconds` is range-checked by click, so both routes into these
    values — the config file and the flag — are validated before any of
    them can become a recorder argument.
    """
    stt = resolve_stt(load_config_file())
    if cleanup:
        stt["cleanup"] = True
    if max_seconds is not None:
        stt["max_seconds"] = max_seconds
    return stt


def _wait_for_enter(deadline: float) -> None:
    """Block until Enter, Ctrl-C, or the recording's own time limit.

    `select` on a terminal rather than a blocking read, so the time limit
    is still enforced when nobody presses anything. With no terminal at
    all (a pipe, a test) there is nothing to press, and the recording runs
    to its limit.
    """
    import select

    click.echo("Recording — press Enter to stop.", err=True)
    interactive = sys.stdin is not None and sys.stdin.isatty()
    while time.time() < deadline:
        if not interactive:
            time.sleep(0.2)
            continue
        ready, _writable, _failed = select.select([sys.stdin], [], [], 0.2)
        if ready:
            sys.stdin.readline()
            return


def _print_transcript(text: str | None) -> None:
    """The transcript on stdout, so `vocalize listen` composes with a pipe."""
    if text is None:
        click.echo("Nothing heard.", err=True)
        sys.exit(1)
    click.echo(text)


@main.command()
@click.option("--check", "check_only", is_flag=True,
              help="Report microphone authorization, the input device and what is installed.")
@click.option("--list-devices", "list_devices", is_flag=True,
              help="Print the input device names [stt] input_device accepts.")
@click.option("--toggle", is_flag=True,
              help="Start a dictation, or stop the one already running (the hotkey's path).")
@click.option("--cancel", is_flag=True, help="Discard a dictation in progress.")
@click.option("--wav", type=click.Path(path_type=Path), default=None,
              help="Transcribe an existing 16 kHz mono 16-bit WAV instead of recording.")
@click.option("--cleanup", is_flag=True,
              help="Tidy the transcript with Claude before delivering it.")
@click.option("--max-seconds", type=click.IntRange(1, 600), default=None,
              help="Stop recording after this many seconds (default: [stt] max_seconds).")
def listen(check_only, list_devices, toggle, cancel, wav, cleanup, max_seconds) -> None:
    """Record from the microphone and print what was said.

    \b
        vocalize listen                 # record until Enter, print the transcript
        vocalize listen --toggle        # start, or stop and copy to the clipboard
        vocalize listen --wav clip.wav  # transcribe a file you already have
        vocalize listen --check         # what the microphone and install look like

    The audio never leaves a temporary directory this command deletes on
    its way out, and the transcript is never written anywhere.
    """
    if check_only:
        _listen_check()
        return
    if list_devices:
        _listen_list_devices()
        return

    modes = [name for name, on in (("--toggle", toggle), ("--cancel", cancel),
                                   ("--wav", wav is not None)) if on]
    if len(modes) > 1:
        raise click.UsageError(f"Use only one of {', '.join(modes)}.")

    stt = _stt_options(cleanup, max_seconds)
    try:
        if cancel:
            sys.exit(dictate.cancel(stt))
        if toggle:
            sys.exit(dictate.toggle(stt))
        if wav is not None:
            # Trusted input: the user named this file. The format is still
            # checked, here and again inside the worker.
            _print_transcript(dictate.transcribe_wav(wav, stt))
            return
        _print_transcript(dictate.listen(stt, wait=_wait_for_enter))
    except DictationError as exc:
        raise click.ClickException(str(exc)) from exc


@main.command("dictate")
@click.option("--cleanup", is_flag=True,
              help="Tidy the transcript with Claude before copying it.")
@click.option("--max-seconds", type=click.IntRange(1, 600), default=None,
              help="Stop recording after this many seconds (default: [stt] max_seconds).")
def dictate_cmd(cleanup, max_seconds) -> None:
    """Start a dictation, or stop the one already running.

    The same thing as `vocalize listen --toggle`, under the name the
    keyboard shortcut uses: press once to record, press again to stop and
    copy what you said to the clipboard.
    """
    try:
        sys.exit(dictate.toggle(_stt_options(cleanup, max_seconds)))
    except DictationError as exc:
        raise click.ClickException(str(exc)) from exc


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
@click.option("--provider", type=click.Choice(PROVIDER_NAMES), default="elevenlabs",
              help="Provider to store a key for (default: elevenlabs).")
def auth_login(from_stdin, provider) -> None:
    """Validate an API key and save it in the system keychain."""
    if provider == "polly":
        raise click.ClickException(
            "Polly uses your AWS credentials (env, ~/.aws/credentials, or a "
            "profile) — nothing to store. Check them with: "
            "vocalize auth status --provider polly"
        )
    if provider in ("say", "kokoro"):
        label = PROVIDER_LABELS.get(provider, provider)
        raise click.ClickException(f"{label} is local and needs no credentials.")

    key = sys.stdin.readline().strip() if from_stdin else prompt_for_key(provider)
    if not key:
        raise click.ClickException("No API key given — nothing was stored.")
    try:
        # The elevenlabs branch keeps its one-argument call on purpose: it's
        # the exact call auth.login's own default-provider path expects, and
        # what other tests exercising this command have always patched.
        if provider == "elevenlabs":
            click.echo(login(key))
        else:
            click.echo(login(key, provider))
    except VocalizeError as exc:
        # Raised before anything is written, so a rejected key leaves
        # whatever was already stored untouched.
        raise click.ClickException(str(exc)) from exc


def _print_elevenlabs_status() -> None:
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


def _provider_status_line(name: str, file_config: dict | None = None) -> str:
    """One-line credential status for any provider other than ElevenLabs."""
    if name in ("openai", "google"):
        source = key_source(None, name)
        if source == "not found":
            # key_source flattens an unreadable keychain into "not found";
            # a status line must not repeat that lie (same as ElevenLabs').
            status, reason = probe_keychain(name)
            if status == "error":
                return f"{name}: keychain unavailable ({reason})"
            return f"{name}: not configured"
        if source == "keychain":
            return f"{name}: keychain ({masked(stored_key(name))})"
        return f"{name}: {source}"
    if name == "polly":
        if file_config is None:
            file_config = load_config_file()
        settings = resolve_provider_settings("polly", file_config, primary=False)
        profile = settings.profile or os.environ.get("AWS_PROFILE") or "default"
        return f"polly: {polly_credential_status(profile)}"
    if name == "say":
        return "say: local, no credentials"
    return "kokoro: local provider (see: vocalize local status)"


@auth.command("status")
@click.option("--provider", type=click.Choice(PROVIDER_NAMES), default=None,
              help="Show only this provider's status (default: ElevenLabs, "
              "plus one line per other provider in the resolved chain).")
def auth_status(provider) -> None:
    """Report where each provider's credentials come from, without revealing them."""
    if provider is not None and provider != "elevenlabs":
        click.echo(_provider_status_line(provider))
        return

    _print_elevenlabs_status()
    if provider == "elevenlabs":
        return

    file_config = load_config_file()
    others = [p for p in resolve_chain(None, file_config) if p != "elevenlabs"]
    for p in ("openai", "google"):
        if p not in others and stored_key(p):
            others.append(p)
    for p in others:
        click.echo(_provider_status_line(p, file_config))


@auth.command("logout")
@click.option("--provider", type=click.Choice(PROVIDER_NAMES), default="elevenlabs",
              help="Provider to remove a stored key for (default: elevenlabs).")
def auth_logout(provider) -> None:
    """Remove a stored API key from the system keychain."""
    if provider not in PROVIDER_USERNAMES:
        raise click.ClickException(f"nothing stored for {provider}")
    try:
        delete_key(provider)
    except VocalizeError as exc:
        raise click.ClickException(str(exc)) from exc
    # delete_key reads the entry back, so this line is a fact, not a hope.
    click.echo("Removed the stored API key from the system keychain.")


@main.command("portal")
@click.option("--no-browser", is_flag=True,
              help="Print the address instead of opening a browser.")
def portal_command(no_browser) -> None:
    """Open the local settings portal in a browser.

    Serves on 127.0.0.1 only. Closes on Ctrl-C, after 15 minutes with
    nothing to do, or after five wrong codes — the last of those exits 1.
    """
    import webbrowser

    from . import portal as portal_module

    served = portal_module.Portal()
    url = served.start()
    try:
        # The code is in the fragment, so this line is the only place it is
        # ever readable. Print it once: repeating it doubles the exposure
        # for nothing.
        click.echo(url)
        click.echo(
            f"This link works once, for {int(portal_module.CODE_TTL)} seconds, "
            "from this Mac only."
        )
        click.echo("")
        # DEC-018, in the one place a user will ever see it.
        click.echo(
            "NOTE: the portal assumes a single-user machine. Everything that "
            "reads or changes your settings is behind a token this link hands "
            "out once — but the socket itself is open to every process on this "
            "Mac, and five junk requests will close the portal under you. "
            "Nothing leaks if that happens; run the command again."
        )
        click.echo("Press Ctrl-C to close it.")
        if not no_browser and not webbrowser.open(url):
            click.echo("No browser opened — paste the link above into one.")
        served.serve_until_stopped()
    finally:
        served.stop()
        # Ctrl-C, the idle watchdog and a lockout all arrive here with a
        # model install possibly still running on a daemon thread that is
        # about to be killed mid-download. Say so — the alternative is a
        # portal that closes quietly over a part-file the size of the
        # model — and take the unusable file back.
        cut_short = served.discard_partial_download()
        if cut_short:
            click.echo(cut_short, err=True)
    if served.locked_out:
        sys.exit(1)


def run() -> None:
    """Entry point that turns our VocalizeError family into clean CLI errors."""
    try:
        main()
    except VocalizeError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    run()


# --- the optional local provider -------------------------------------
# Deliberately last in the file: everything below is Kokoro's opt-in
# setup, and nothing above it knows or cares that it exists.


@main.group("local")
def local() -> None:
    """Install and inspect the on-device speech providers (Kokoro, Whisper)."""


def _kokoro_modules():
    """Imported inside the commands: `vocalize speak` must never pay for this."""
    from .local import install as install_module
    from .local import kokoro_manifest as manifest
    from .providers import kokoro as provider

    return install_module, manifest, provider


def _stt_modules():
    """Imported inside the commands: `vocalize speak` must never pay for this."""
    from .local import install as install_module
    from .local import whisper_manifest as manifest

    return install_module, manifest


_STT_INSTALL_HINT = "vocalize local install --stt"


def _require_uv(uv: str | None) -> str:
    if uv is None:
        raise click.ClickException(
            "uv is not installed, and the on-device runtime needs it. Install it "
            "from https://docs.astral.sh/uv/ and run this again. Nothing was "
            "downloaded."
        )
    return uv


@local.command("install")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
@click.option(
    "--stt", is_flag=True,
    help="Install the on-device speech-to-text runtime (whisper.cpp) instead of Kokoro.",
)
@click.option(
    "--model", "model_name", default=None, metavar="NAME",
    help="Which speech-to-text model to install (--stt only; default: small.en).",
)
def local_install(yes, stt, model_name) -> None:
    """Download and verify a local runtime's model files, then warm it."""
    if stt:
        _install_stt(yes, model_name)
        return
    if model_name is not None:
        raise click.ClickException("--model only applies together with --stt")
    _install_kokoro(yes)


def _install_kokoro(yes: bool) -> None:
    install_module, manifest, provider = _kokoro_modules()

    uv = _require_uv(provider.uv_path())

    ready, _ = provider.installed()
    if ready:
        click.echo("Kokoro is already installed.")
        return

    total = sum(entry["size"] for entry in manifest.FILES)
    click.echo("Kokoro speaks entirely on this machine — no text ever leaves it.")
    click.echo("")
    click.echo(f"This will download {_human_readable_size(total)} of model files:")
    for entry in manifest.FILES:
        click.echo(f"  {entry['name']}  ({_human_readable_size(entry['size'])})")
        click.echo(f"    {entry['url']}")
    click.echo(f"  into {manifest.MODEL_DIR}")
    click.echo("")
    click.echo("It will also have uv fetch, into its own cache (about 230 MB):")
    click.echo(f"  Python {manifest.PYTHON_VERSION} and {manifest.RUNTIME_PACKAGE} from PyPI")
    click.echo("  (which brings onnxruntime, numpy and espeakng-loader)")
    click.echo("")

    if not yes and not click.confirm("Download and install now?", default=False):
        click.echo("Aborted, nothing downloaded.")
        sys.exit(1)

    for entry in manifest.FILES:
        # A half-finished install already has one good file on disk; a
        # re-run should not spend 326 MB re-fetching it.
        if install_module.file_is_verified(entry):
            click.echo(f"  {entry['name']}: already verified, skipping")
            continue
        click.echo(f"Downloading {entry['name']}...")
        try:
            install_module.download_file(
                entry["url"],
                manifest.MODEL_DIR / entry["name"],
                entry["size"],
                entry["sha256"],
                progress=_download_progress(),
            )
        except install_module.InstallError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(f"  verified {entry['name']} (sha256 matches).")

    install_module.write_stamp()

    click.echo("Warming the runtime...")
    try:
        install_module.selftest(uv)
    except install_module.InstallError as exc:
        # The files are verified and staying put — only the runtime failed.
        raise click.ClickException(
            f"The model files are installed, but the Kokoro runtime would not "
            f"start: {exc}"
        ) from exc

    click.echo('Kokoro installed. Try: vocalize speak "hello" --provider kokoro')


def _build_recorder_step(install_module) -> None:
    """Compile the recorder bundle if it is missing or out of date.

    Runs on every `local install --stt`, including the "already installed"
    path: the model is a 488 MB download and the bundle is a two-second
    compile, so a re-run must be able to fix a missing recorder without
    fetching a byte.
    """
    try:
        status, bundle = install_module.build_recorder()
    except install_module.InstallError as exc:
        raise click.ClickException(str(exc)) from exc

    if status == "current":
        click.echo(f"  recorder: already built ({bundle.name})")
    elif status == "built":
        click.echo(f"  recorder: built ({bundle})")
        click.echo("  macOS will ask for microphone access the first time you dictate.")
    else:
        click.echo(f"  recorder: rebuilt ({bundle})")
        click.echo(f"  {install_module.REGRANT_WARNING}")


def _stt_model_or_raise(manifest, model_name: str | None) -> str:
    model = model_name or manifest.DEFAULT_MODEL
    if model not in manifest.MODELS:
        raise click.ClickException(
            f"Unknown model {model!r}. Choose one of: {', '.join(manifest.MODELS)}"
        )
    return model


def _install_stt(yes: bool, model_name: str | None) -> None:
    from . import local as local_module

    install_module, manifest = _stt_modules()
    uv = _require_uv(local_module.uv_path())
    model = _stt_model_or_raise(manifest, model_name)
    entry = manifest.file_for(model)

    ready, _ = install_module.installed(manifest, files=[entry], install_hint=_STT_INSTALL_HINT)
    if ready:
        # write_stamp() runs before the selftest below, so a machine where
        # the runtime itself never started (Metal unavailable, a build
        # failure) still has a verified stamp — "already installed" would
        # otherwise never retry the one thing that actually failed, with
        # no in-CLI way to force it short of a full uninstall/re-download.
        click.echo(f"{model} is already installed. Re-warming the runtime...")
        try:
            install_module.selftest(uv, manifest=manifest, model=model)
        except install_module.InstallError as exc:
            raise click.ClickException(
                f"The model file is installed, but the speech-to-text runtime "
                f"would not start: {exc}"
            ) from exc
        _build_recorder_step(install_module)
        click.echo(f"{model} is ready.")
        return

    click.echo(
        "Speech-to-text runs entirely on this machine — no audio or text ever leaves it."
    )
    click.echo("")
    click.echo(f"This will download {_human_readable_size(entry['size'])}:")
    click.echo(f"  {entry['name']}  ({_human_readable_size(entry['size'])})")
    click.echo(f"    {entry['url']}")
    click.echo(f"  into {manifest.MODEL_DIR}")
    click.echo("")
    click.echo("It will also have uv fetch, into its own cache:")
    click.echo(f"  Python {manifest.PYTHON_VERSION} and {manifest.RUNTIME_PACKAGE} from PyPI")
    click.echo("")

    if not yes and not click.confirm("Download and install now?", default=False):
        click.echo("Aborted, nothing downloaded.")
        sys.exit(1)

    if install_module.file_is_verified(entry, manifest=manifest):
        click.echo(f"  {entry['name']}: already verified, skipping")
    else:
        click.echo(f"Downloading {entry['name']}...")
        try:
            install_module.download_file(
                entry["url"],
                manifest.MODEL_DIR / entry["name"],
                entry["size"],
                entry["sha256"],
                progress=_download_progress(),
            )
        except install_module.InstallError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(f"  verified {entry['name']} (sha256 matches).")

    install_module.write_stamp(manifest=manifest, files=[entry])

    click.echo("Warming the runtime (pays a one-time ~8s Metal shader compile)...")
    try:
        install_module.selftest(uv, manifest=manifest, model=model)
    except install_module.InstallError as exc:
        # The file is verified and staying put — only the runtime failed.
        raise click.ClickException(
            f"The model file is installed, but the speech-to-text runtime "
            f"would not start: {exc}"
        ) from exc

    _build_recorder_step(install_module)

    # `listen` takes its model from [stt] config, not a flag (DEC-006) —
    # this must never suggest a --model option `listen` doesn't define.
    click.echo(f"Speech-to-text installed ({model}). Try: vocalize listen --check")


def _download_progress():
    """Percentage every 10%. Plain lines, so a piped install stays readable."""
    state = {"last": -10}

    def progress(done: int, total: int) -> None:
        if not total:
            return
        percent = min(100, int(done * 100 / total))
        if percent >= state["last"] + 10:
            state["last"] = percent
            click.echo(f"  {percent}%")

    return progress


@local.command("status")
def local_status() -> None:
    """Report whether the on-device providers are ready, and what is missing."""
    from . import local as local_module

    # Both runtimes share one uv invocation, resolved the same way
    # `_install_stt` does (`vocalize.local.uv_path()`) rather than through
    # Kokoro's re-exported name — a test (or caller) patching one now
    # reliably covers both `local install --stt` and `local status`.
    uv = local_module.uv_path()
    click.echo(f"uv: {uv}" if uv else "uv: not found — see https://docs.astral.sh/uv/")
    click.echo("")

    _status_kokoro(uv)
    click.echo("")
    _status_stt(uv)


def _status_kokoro(uv: str | None) -> None:
    install_module, manifest, provider = _kokoro_modules()

    click.echo("Kokoro (text-to-speech):")
    click.echo(f"  Model directory: {manifest.MODEL_DIR}")
    for entry in manifest.FILES:
        path = manifest.MODEL_DIR / entry["name"]
        try:
            size = path.stat().st_size
        except OSError:
            click.echo(f"  {entry['name']}: missing")
            continue
        if size == entry["size"]:
            click.echo(f"  {entry['name']}: present ({_human_readable_size(size)})")
        else:
            click.echo(
                f"  {entry['name']}: wrong size ({_human_readable_size(size)}, "
                f"expected {_human_readable_size(entry['size'])})"
            )

    stamp = install_module.read_stamp()
    click.echo(f"  {manifest.STAMP_NAME}: {'ok' if stamp else 'missing'}")

    ready, reason = provider.installed()
    if ready and uv:
        click.echo("Kokoro is ready.")
    elif ready:
        click.echo("Kokoro's model files are ready, but uv is missing.")
    else:
        click.echo(f"Kokoro is not usable: {reason}")


def _status_stt(uv: str | None) -> None:
    install_module, manifest = _stt_modules()

    click.echo("STT (speech-to-text):")
    click.echo(f"  Model directory: {manifest.MODEL_DIR}")

    stamp = install_module.read_stamp(manifest=manifest)
    recorded = install_module.stamp_files(stamp, manifest)
    any_present = False
    for entry in manifest.FILES:
        path = manifest.MODEL_DIR / entry["name"]
        try:
            size = path.stat().st_size
        except OSError:
            continue
        any_present = True
        seen = recorded.get(entry["name"]) or {}
        verified = size == entry["size"] and (seen.get("sha256"), seen.get("size")) == (
            entry["sha256"], entry["size"],
        )
        state = "verified" if verified else "present, not verified"
        click.echo(f"  {entry['name']}: {state} ({_human_readable_size(size)})")
    if not any_present:
        click.echo("  no models installed")

    click.echo(f"  runtime: {manifest.RUNTIME_PACKAGE} via uv")

    if install_module.recorder_binary().is_file():
        click.echo(f"  recorder: built ({install_module.recorder_bundle()})")
    else:
        click.echo(f"  recorder: not built — run: {_STT_INSTALL_HINT}")

    # Readiness is over every model that verifies, not just the default:
    # `local install --stt --model base.en` is a real, complete install,
    # and reporting it as "not ready" tells the user to redo work they
    # already did.
    ready_models = [
        m for m in manifest.MODELS
        if install_module.installed(
            manifest, files=[manifest.file_for(m)], install_hint=_STT_INSTALL_HINT,
        )[0]
    ]
    if ready_models and uv:
        click.echo(f"STT: ready ({', '.join(ready_models)})")
    elif ready_models:
        click.echo("STT: not ready — uv is missing")
    else:
        _, reason = install_module.installed(
            manifest, files=[manifest.file_for(manifest.DEFAULT_MODEL)],
            install_hint=_STT_INSTALL_HINT,
        )
        click.echo(f"STT: not ready — default model ({manifest.DEFAULT_MODEL}) {reason}")


@local.command("uninstall")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
@click.option(
    "--stt", is_flag=True,
    help="Remove the speech-to-text model files and recorder bundle.",
)
def local_uninstall(yes, stt) -> None:
    """Remove a local runtime's downloaded files."""
    if not stt:
        raise click.ClickException("Specify what to uninstall: --stt")
    _uninstall_stt(yes)


def _uninstall_stt(yes: bool) -> None:
    install_module, manifest = _stt_modules()

    model_dir = manifest.MODEL_DIR
    # ~/.cache/vocalize/bin — the recorder bundle `local install --stt`
    # compiles. Removing it drops the ad-hoc signature the microphone
    # grant is attached to; the grant itself stays in System Settings.
    bin_dir = install_module.BIN_DIR
    # is_dir() + not is_symlink(): path.exists() follows symlinks, and a
    # bare `shutil.rmtree()` on a symlinked target raises OSError instead
    # of removing anything — a user who pointed the model dir at an
    # external disk gets a clean message instead.
    candidates = (model_dir, bin_dir)
    targets = [path for path in candidates if path.is_dir() and not path.is_symlink()]
    symlinked = [path for path in candidates if path.is_symlink()]

    if not targets and not symlinked:
        click.echo("Nothing to remove.")
        return

    click.echo("This will remove:")
    for path in targets:
        click.echo(f"  {path}")
    for path in symlinked:
        click.echo(f"  {path} (a symlink — remove it yourself)")
    click.echo("")
    click.echo("The microphone permission grant, if any, stays in System Settings.")

    if not yes and not click.confirm("Remove now?", default=False):
        click.echo("Aborted, nothing removed.")
        sys.exit(1)

    for path in targets:
        try:
            shutil.rmtree(path)
        except OSError as exc:
            raise click.ClickException(f"Could not remove {path}: {exc}") from exc
        click.echo(f"  removed {path}")

    click.echo("Speech-to-text uninstalled.")
