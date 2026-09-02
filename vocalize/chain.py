"""Try each provider in turn until one of them speaks.

The chain is the whole point of multi-provider vocalize: a failure that
one provider can't recover from is usually a failure the next one never
has. What counts as "try the next one" is decided by the typed errors in
exceptions.py, not by string matching, and every fallthrough is announced
on stderr — silently switching vendor is the behaviour nobody wants.

Two gates run before a provider is allowed to spend anything:

* `check(settings)` — offline availability (a key, a binary, a dependency).
* the ledger — this month's local budget, and whether the provider already
  answered with a quota error. Both are measured against the WHOLE text
  before the first chunk, so a long read can't creep past the budget one
  chunk at a time.

Streaming (Kokoro) inverts the usual order: pieces are handed to
`on_chunk` as they finish so playback overlaps rendering. Once the first
piece has played, a later failure can no longer fall through to another
provider — you can't un-hear the first half of a document.

`on_chunk(path)` gets a file inside a temporary directory that lives only
until `run` returns, so a caller that plays it asynchronously must take
its own copy (the CLI does). Returning False from it means the user
stopped: `run` raises PlaybackStopped.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from . import audio as audio_module
from . import auth, cache, ledger, providers
from .config import budget_for, resolve_provider_settings
from .exceptions import (
    ConfigError,
    PlaybackStopped,
    ProviderAuthError,
    ProviderContentError,
    ProviderError,
    ProviderQuotaError,
    ProviderTransientError,
    ProviderUnavailableError,
    TTSRequestError,
)
from .preprocess import split_for_synthesis
from .tts import DEFAULT_CACHE_DIR

# The byte-cap re-split halves a chunk each pass; a dozen passes takes any
# realistic chunk under any realistic cap, and bounds a pathological input.
_MAX_RESPLIT_PASSES = 12

# Errors that mean "this provider can't do it, ask the next one".
_SKIPPABLE = (ProviderUnavailableError, ProviderAuthError, ProviderTransientError)


def _count(text: str, provider) -> int:
    """What this provider's quota counts: UTF-8 bytes (Google) or characters."""
    if getattr(provider, "COUNT_UNIT", "chars") == "bytes":
        return len(text.encode("utf-8"))
    return len(text)


def _reason(exc: ProviderError, name: str) -> str:
    """The error's message without the provider prefix it already carries."""
    return str(exc).removeprefix(f"{name}: ")


def _budget_gate(name: str, provider, text: str, file_config: dict) -> None:
    """Raise ProviderUnavailableError when this month's local budget is spent.

    Unavailable rather than Quota on purpose: a local budget is our own
    limit, and marking the provider exhausted for it would outlive the
    budget the user could raise a minute later.
    """
    used, exhausted = ledger.status(name)
    if exhausted:
        raise ProviderUnavailableError(name, "out of quota until next month")

    budget = budget_for(name, file_config)
    if budget is None:
        return
    needed = _count(text, provider)
    if used + needed > budget:
        raise ProviderUnavailableError(
            name,
            f"local budget reached ({used + needed:,}/{budget:,} chars this month)",
        )


def _fit_bytes(chunks: list[str], max_bytes: int) -> list[str]:
    """Re-split any chunk whose UTF-8 length is over the provider's byte cap."""
    out: list[str] = []
    for chunk in chunks:
        pieces = [chunk]
        for _ in range(_MAX_RESPLIT_PASSES):
            if all(len(p.encode("utf-8")) <= max_bytes for p in pieces):
                break
            smaller: list[str] = []
            for piece in pieces:
                if len(piece.encode("utf-8")) <= max_bytes:
                    smaller.append(piece)
                else:
                    smaller.extend(split_for_synthesis(piece, max(1, len(piece) // 2)))
            if smaller == pieces:  # no progress; stop rather than spin
                break
            pieces = smaller
        out.extend(pieces)
    return out


def _chunks_for(provider, text: str, chunk_chars: int | None) -> list[str]:
    limits = [x for x in (getattr(provider, "MAX_CHARS", None), chunk_chars) if x]
    chunks = split_for_synthesis(text, min(limits)) if limits else [text]
    max_bytes = getattr(provider, "MAX_BYTES", None)
    if max_bytes:
        chunks = _fit_bytes(chunks, max_bytes)
    return chunks


# The chunk texts of the last streamed run in this process, for
# `unheard_text` to answer from after the run has returned. Per process and
# main-thread only, like the run that fills it.
_last_chunks: list[str] = []


def unheard_text(ext: str, *, handed: int | None = None) -> str:
    """The text of the last streamed run's chunks nobody heard (DEC-003).

    The piece that came back False was never played — the player is
    already stopped by then — but it is not the piece the stop landed in:
    the CLI hands pieces over one ahead of the one playing, so by the time
    a stop is reported back, one or two rendered pieces have gone by
    unheard. When every piece was handed over before the stop — a read
    whose audio is all cached renders far faster than it plays — nothing
    is reported back at all, and `handed` is None.

    `audio.last_stop()` names the piece the player actually had open, as
    `<n>.<ext>` in the CLI's own copy of it. That piece is saved with the
    record and replayed from its offset, so the text carries on *after*
    it: everything from `n + 1` on. Only the fallback — no usable name,
    which means the stop came from somewhere other than a killed player —
    starts at the piece `handed` names.
    """
    stopped = audio_module.last_stop()
    if stopped.path is not None and stopped.path.name.endswith(f".{ext}"):
        try:
            played = int(stopped.path.name[: -len(ext) - 1])
        except ValueError:
            played = 0
        if 1 <= played <= len(_last_chunks):
            return " ".join(_last_chunks[played:])
    if handed is None:
        return ""  # nothing to place it against: resume the saved piece alone
    return " ".join(_last_chunks[handed - 1:])


def _speak(
    name, provider, settings, text, *, chunk_chars, cache_dir, echo, on_chunk, call_kwargs
):
    """Every chunk of `text` through one provider, as one joined blob.

    Streams pieces to `on_chunk` when the provider supports it. Once a
    piece has been handed over, playback is under way: a later failure is
    re-raised as a plain TTSRequestError so the caller's chain loop stops
    instead of restarting the read on another provider.
    """
    ext = provider.AUDIO_EXT
    chunks = _chunks_for(provider, text, chunk_chars)
    total = len(chunks)
    if total > 1:
        echo(f"Long input: splitting into {total} chunks.")

    streaming = on_chunk is not None and getattr(provider, "STREAMING", False)
    if streaming:
        global _last_chunks
        _last_chunks = chunks  # what `unheard_text` answers from, stop or no stop
    parts: list[bytes] = []
    started = False
    # 0700 by default, and the text only ever reaches it as audio.
    tmp = tempfile.TemporaryDirectory(prefix="vocalize-") if streaming else None
    try:
        for index, chunk in enumerate(chunks, start=1):
            try:
                audio = cache.get(chunk, settings, cache_dir, ext)
                if audio is None:
                    if total == 1:
                        echo(f"Requesting {len(chunk)} characters from {name}...")
                    else:
                        echo(
                            f"Requesting chunk {index}/{total} "
                            f"({len(chunk)} characters) from {name}..."
                        )
                    audio = provider.synthesize(chunk, settings, **call_kwargs)
                    cache.put(chunk, settings, audio, cache_dir, ext)
                    # Cache hits cost the provider nothing, so they count
                    # nothing — the ledger tracks spend, not playback.
                    ledger.record(name, _count(chunk, provider))
            except ProviderError as exc:
                if started:
                    raise TTSRequestError(
                        f"{name}: failed mid-read after playback started: "
                        f"{_reason(exc, name)}"
                    ) from exc
                raise

            parts.append(audio)

            if streaming:
                piece = Path(tmp.name) / f"{index}.{ext}"
                piece.write_bytes(audio)
                started = True
                if on_chunk(piece) is False:
                    # Everything rendered so far travels with the stop: the
                    # CLI saves it when the stop came from a broken player.
                    raise PlaybackStopped(
                        "Playback stopped.", audio_module.join_audio(parts, ext), ext,
                        remaining_text=unheard_text(ext, handed=index),
                        provider=name,
                    )

        return audio_module.join_audio(parts, ext)
    finally:
        if tmp is not None:
            tmp.cleanup()


def run(
    text,
    *,
    chain,
    file_config,
    overrides=None,
    chunk_chars=None,
    cache_dir=DEFAULT_CACHE_DIR,
    echo=lambda m: None,
    on_chunk=None,
    forced=False,
) -> tuple[bytes, str, str]:
    """Returns (audio, provider_name, audio_ext). Raises TTSRequestError when
    every provider fails.

    `forced` says the chain is one provider the user named with --provider,
    where there is no fallback to suggest.
    """
    overrides = dict(overrides or {})
    overrides = {k: v for k, v in overrides.items() if v is not None}
    api_key = overrides.pop("api_key", None)
    if api_key and chain[0] != "elevenlabs":
        # Only the key-holding providers have a login to suggest; say,
        # kokoro and polly have no key slot to point at at all.
        env_var = auth.PROVIDER_ENV_VARS.get(chain[0])
        if env_var:
            raise ConfigError(
                f"--api-key only applies to ElevenLabs, but the chain starts with "
                f"{chain[0]!r}. Use `vocalize auth login --provider {chain[0]}` "
                f"or set {env_var}."
            )
        label = auth.PROVIDER_LABELS.get(chain[0], chain[0])
        raise ConfigError(
            f"{label} takes no API key; --api-key only applies to ElevenLabs, "
            f"but the chain starts with {chain[0]!r}."
        )

    failures: list[tuple[str, str]] = []
    for index, name in enumerate(chain):
        primary = index == 0
        provider = providers.get(name)
        settings = resolve_provider_settings(
            name, file_config, primary=primary, **(overrides if primary else {})
        )
        # --api-key is ElevenLabs' alone; nothing else takes the kwarg.
        call_kwargs = {"api_key": api_key} if (primary and api_key) else {}

        try:
            provider.check(settings, **call_kwargs)
            _budget_gate(name, provider, text, file_config)
            audio = _speak(
                name, provider, settings, text,
                chunk_chars=chunk_chars, cache_dir=cache_dir, echo=echo,
                on_chunk=on_chunk, call_kwargs=call_kwargs,
            )
        except ProviderContentError:
            raise  # a misconfigured voice is a bug to fix, not one to route around
        except ProviderQuotaError as exc:
            ledger.mark_exhausted(name)
            failures.append((name, _reason(exc, name)))
            echo(_skip_message(exc, chain, index))
        except _SKIPPABLE as exc:
            failures.append((name, _reason(exc, name)))
            echo(_skip_message(exc, chain, index))
        else:
            if not primary:
                echo(f"Spoke via {name} (fallback).")
            return audio, name, provider.AUDIO_EXT

    if forced:
        # --provider turned fallback off deliberately; telling the user to
        # add "say" to a chain this run never consulted is just noise.
        hint = "\n  (fallback is off with --provider; drop the flag to use your chain)"
    elif "say" in chain:
        hint = ""
    else:
        hint = '\n  (no local fallback configured — add "say" to your chain)'
    raise TTSRequestError(
        "Every provider in the chain failed:\n"
        + "\n".join(f"  {name}: {why}" for name, why in failures)
        + hint
    )


def _skip_message(exc: ProviderError, chain: list[str], index: int) -> str:
    if index + 1 < len(chain):
        return f"{exc} — trying {chain[index + 1]}"
    return f"{exc} — no providers left"
