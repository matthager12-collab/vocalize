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

import math
import os
import queue
import subprocess
import sys
import tempfile
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import click

from . import __version__, ledger, providers, wizard
from .audio import play as play_audio
from .audio import play_sequence, stop_playback
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
)
from .exceptions import (
    AudioPlaybackError,
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

    def echo(message: str) -> None:
        click.echo(message, err=True)

    with tempfile.TemporaryDirectory(prefix="vocalize-play-") as workdir:
        player = _StreamPlayer(Path(workdir)) if play else None
        try:
            audio, _name, ext = chain_run(
                text, chain=chain, file_config=file_config, overrides=overrides,
                chunk_chars=chunk_chars, forced=provider is not None,
                echo=echo, on_chunk=player.on_chunk if player else None,
            )
        except PlaybackStopped as stopped:
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

        dest = output_path or (DEFAULT_CACHE_DIR / f"last.{ext}")
        save_audio(audio, dest)
        click.echo(f"Saved audio to {dest}", err=True)

        # A streaming provider already played every piece as it landed;
        # playing the joined file now would read the whole thing twice.
        if play and (player is None or player.pieces == 0):
            play_audio(dest)
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

    data = dict(load_config_file())
    data["chain"] = list(provider_names)
    path = config_path()
    wizard._render_config_text(data)  # fail fast before writing anything
    wizard._write_config(path, data)
    click.echo(f"chain={','.join(provider_names)}")
    click.echo(f"wrote {path}")


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
    """Install and inspect the on-device speech provider (Kokoro)."""


def _kokoro_modules():
    """Imported inside the commands: `vocalize speak` must never pay for this."""
    from .local import install as install_module
    from .local import kokoro_manifest as manifest
    from .providers import kokoro as provider

    return install_module, manifest, provider


def _require_uv(provider) -> str:
    uv = provider.uv_path()
    if uv is None:
        raise click.ClickException(
            "uv is not installed, and Kokoro's runtime needs it. Install it from "
            "https://docs.astral.sh/uv/ and run this again. Nothing was downloaded."
        )
    return uv


@local.command("install")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def local_install(yes) -> None:
    """Download and verify Kokoro's model files, then warm the runtime."""
    install_module, manifest, provider = _kokoro_modules()

    uv = _require_uv(provider)

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
    """Report whether the on-device provider is ready, and what is missing."""
    install_module, manifest, provider = _kokoro_modules()

    uv = provider.uv_path()
    click.echo(f"uv: {uv}" if uv else "uv: not found — see https://docs.astral.sh/uv/")

    click.echo(f"Model directory: {manifest.MODEL_DIR}")
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
