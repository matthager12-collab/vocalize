"""Tests for vocalize.chain — who gets tried, who gets skipped, and why.

Every provider here is a fake object registered through the two seams
chain.run goes through (`providers.get` and `resolve_provider_settings`),
so nothing in this file can reach a network, a keychain or a binary.
"""

from __future__ import annotations

import io
import wave
from pathlib import Path

import pytest

from vocalize import audio as audio_module
from vocalize import cache, chain, ledger
from vocalize.config import Settings
from vocalize.exceptions import (
    AudioPlaybackError,
    ConfigError,
    PlaybackStopped,
    ProviderAuthError,
    ProviderContentError,
    ProviderQuotaError,
    ProviderTransientError,
    ProviderUnavailableError,
    TTSRequestError,
)


def _wav(text: str) -> bytes:
    """A real one-channel WAV whose frame count identifies the text.

    Real bytes rather than a marker string, so the chain's WAV stitching
    is exercised for what it is instead of being mocked out.
    """
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(24000)
        writer.writeframes(b"\x00\x00" * len(text))
    return out.getvalue()


def frame_count(audio: bytes) -> int:
    with wave.open(io.BytesIO(audio), "rb") as reader:
        return reader.getnframes()


class FakeProvider:
    """A provider module's contract, as a plain object."""

    def __init__(
        self,
        name,
        *,
        ext="mp3",
        max_chars=None,
        audio=None,
        error=None,
        check_error=None,
        streaming=False,
        count_unit=None,
        max_bytes=None,
    ):
        self.NAME = name
        self.AUDIO_EXT = ext
        self.MAX_CHARS = max_chars
        self.DEFAULTS = {"voice": f"{name}-voice"}
        self.STREAMING = streaming
        if count_unit is not None:
            self.COUNT_UNIT = count_unit
        if max_bytes is not None:
            self.MAX_BYTES = max_bytes
        self._audio = audio
        self._error = error
        self._check_error = check_error
        self.calls = []
        self.checked = []
        self.produced = []

    def check(self, settings=None, **kwargs):
        self.checked.append((settings, kwargs))
        if self._check_error is not None:
            raise self._check_error

    def synthesize(self, text, settings, **kwargs):
        self.calls.append(text)
        if self._error is not None:
            if isinstance(self._error, list):
                error = self._error[len(self.calls) - 1]
                if error is not None:
                    raise error
            else:
                raise self._error
        audio = self._audio
        if audio is None:
            audio = _wav(text) if self.AUDIO_EXT == "wav" else f"[{self.NAME}:{text}]".encode()
        self.produced.append(audio)
        return audio


class _Registry(dict):
    """The fake providers, keyed by name, plus the settings calls they caused."""

    resolved: list
    cache_dir = None


@pytest.fixture
def registry(monkeypatch, tmp_path):
    """Register fake providers by name and record every settings lookup."""
    modules = _Registry()
    modules.cache_dir = tmp_path / "cache"
    resolved = []

    def fake_get(name):
        if name not in modules:
            raise AssertionError(f"chain asked for an unregistered provider {name!r}")
        return modules[name]

    def fake_resolve(name, file_config=None, *, primary=True, **overrides):
        resolved.append({"name": name, "primary": primary, **overrides})
        return Settings(voice_id=f"{name}-voice", provider=name)

    monkeypatch.setattr("vocalize.providers.get", fake_get)
    monkeypatch.setattr(chain, "resolve_provider_settings", fake_resolve)
    modules.resolved = resolved
    return modules


def _run(registry, text="hello", **kwargs):
    kwargs.setdefault("file_config", {})
    kwargs.setdefault("chain", list(registry))
    # Never the real ~/.cache/vocalize: a fake provider's output must not
    # end up as a cache hit for someone's actual speak run.
    kwargs.setdefault("cache_dir", registry.cache_dir)
    return chain.run(text, **kwargs)


def _echoes():
    lines = []
    return lines, lines.append


# --- falling through ---------------------------------------------------------


def test_a_quota_error_moves_on_and_is_remembered_for_the_month(registry):
    registry["openai"] = FakeProvider("openai", error=ProviderQuotaError("openai", "out of credit"))
    registry["say"] = FakeProvider("say")

    audio, name, ext = _run(registry)

    assert (audio, name, ext) == (b"[say:hello]", "say", "mp3")
    assert ledger.status("openai") == (0, True)


def test_an_auth_error_moves_on_without_marking_exhausted(registry):
    registry["openai"] = FakeProvider(
        "openai", check_error=ProviderAuthError("openai", "invalid or missing API key")
    )
    registry["say"] = FakeProvider("say")

    _audio, name, _ext = _run(registry)

    assert name == "say"
    assert ledger.status("openai") == (0, False)


def test_a_transient_error_moves_on(registry):
    registry["google"] = FakeProvider("google", error=ProviderTransientError("google", "HTTP 503"))
    registry["say"] = FakeProvider("say")

    assert _run(registry)[1] == "say"


def test_an_unavailable_provider_moves_on(registry):
    registry["polly"] = FakeProvider(
        "polly", check_error=ProviderUnavailableError("polly", "needs boto3")
    )
    registry["say"] = FakeProvider("say")

    assert _run(registry)[1] == "say"
    assert registry["polly"].calls == []


def test_a_content_error_stops_the_chain(registry):
    boom = ProviderContentError("say", "invalid voice 'nope'")
    registry["say"] = FakeProvider("say", error=boom)
    registry["elevenlabs"] = FakeProvider("elevenlabs")

    with pytest.raises(ProviderContentError) as excinfo:
        _run(registry, chain=["say", "elevenlabs"])

    # The same exception, not a rewrapped one: a misconfigured voice is a
    # bug to fix, not something to route around.
    assert excinfo.value is boom
    assert registry["elevenlabs"].calls == []


def test_a_forced_single_provider_chain_never_falls_back(registry):
    registry["openai"] = FakeProvider("openai", error=ProviderTransientError("openai", "HTTP 500"))
    registry["say"] = FakeProvider("say")

    with pytest.raises(TTSRequestError):
        _run(registry, chain=["openai"])

    assert registry["say"].calls == []


def test_an_unexpected_exception_propagates_untouched(registry):
    registry["say"] = FakeProvider("say", error=TypeError("our own bug"))
    registry["elevenlabs"] = FakeProvider("elevenlabs")

    with pytest.raises(TypeError, match="our own bug"):
        _run(registry, chain=["say", "elevenlabs"])

    assert registry["elevenlabs"].calls == []


# --- what the user is told ---------------------------------------------------


def test_a_skip_names_the_provider_being_tried_next(registry):
    registry["openai"] = FakeProvider("openai", error=ProviderQuotaError("openai", "out of credit"))
    registry["google"] = FakeProvider("google", error=ProviderAuthError("google", "bad key"))
    registry["say"] = FakeProvider("say")
    lines, echo = _echoes()

    _run(registry, echo=echo)

    assert "openai: out of credit — trying google" in lines
    assert "google: bad key — trying say" in lines


def test_the_last_skip_says_there_is_nobody_left(registry):
    registry["openai"] = FakeProvider("openai", error=ProviderTransientError("openai", "HTTP 500"))
    lines, echo = _echoes()

    with pytest.raises(TTSRequestError):
        _run(registry, echo=echo)

    assert lines[-1] == "openai: HTTP 500 — no providers left"


def test_a_fallback_announces_which_provider_actually_spoke(registry):
    registry["openai"] = FakeProvider("openai", error=ProviderTransientError("openai", "HTTP 500"))
    registry["say"] = FakeProvider("say")
    lines, echo = _echoes()

    _run(registry, echo=echo)

    assert "Spoke via say (fallback)." in lines


def test_the_primary_speaking_is_not_announced_as_a_fallback(registry):
    registry["elevenlabs"] = FakeProvider("elevenlabs")
    lines, echo = _echoes()

    _run(registry, echo=echo)

    assert not any("fallback" in line for line in lines)


def test_every_failure_is_listed_when_the_whole_chain_fails(registry):
    registry["openai"] = FakeProvider("openai", error=ProviderQuotaError("openai", "out of credit"))
    registry["google"] = FakeProvider(
        "google", check_error=ProviderUnavailableError("google", "no key stored")
    )

    with pytest.raises(TTSRequestError) as excinfo:
        _run(registry)

    message = str(excinfo.value)
    assert "Every provider in the chain failed:" in message
    assert "  openai: out of credit" in message
    assert "  google: no key stored" in message
    assert 'add "say" to your chain' in message


def test_the_fallback_hint_is_not_offered_when_the_user_forced_one_provider(registry):
    # --provider turns fallback off on purpose; "add say to your chain" is
    # advice about a chain this run never had.
    registry["openai"] = FakeProvider(
        "openai", error=ProviderQuotaError("openai", "out of credit")
    )

    with pytest.raises(TTSRequestError) as excinfo:
        _run(registry, chain=["openai"], forced=True)

    message = str(excinfo.value)
    assert "fallback is off with --provider" in message
    assert "no local fallback" not in message


def test_the_local_fallback_hint_stays_when_the_chain_was_not_forced(registry):
    registry["openai"] = FakeProvider(
        "openai", error=ProviderQuotaError("openai", "out of credit")
    )

    with pytest.raises(TTSRequestError) as excinfo:
        _run(registry, chain=["openai"])

    message = str(excinfo.value)
    assert 'add "say" to your chain' in message
    assert "--provider" not in message


def test_no_local_fallback_hint_when_say_is_already_in_the_chain(registry):
    registry["say"] = FakeProvider("say", error=ProviderTransientError("say", "say failed"))

    with pytest.raises(TTSRequestError) as excinfo:
        _run(registry)

    assert "no local fallback" not in str(excinfo.value)


# --- budgets and the ledger --------------------------------------------------


def test_the_local_budget_is_checked_before_anything_is_sent(registry):
    registry["google"] = FakeProvider("google")
    registry["say"] = FakeProvider("say")
    file_config = {"providers": {"google": {"monthly_chars": 100}}}
    ledger.record("google", 95)
    lines, echo = _echoes()

    _audio, name, _ext = _run(
        registry, text="a longer sentence than the budget allows", file_config=file_config, echo=echo
    )

    assert name == "say"
    assert registry["google"].calls == []  # nothing was spent to find out
    assert "local budget reached" in lines[0]
    # A local budget is our own limit, not the provider's verdict: marking
    # it exhausted would outlive a budget the user can raise in a minute.
    assert ledger.status("google") == (95, False)


def test_a_budget_with_room_left_is_not_a_skip(registry):
    registry["google"] = FakeProvider("google")
    file_config = {"providers": {"google": {"monthly_chars": 1000}}}
    ledger.record("google", 10)

    assert _run(registry, text="hello", file_config=file_config)[1] == "google"


def test_a_provider_marked_exhausted_is_skipped_without_a_call(registry):
    registry["openai"] = FakeProvider("openai")
    registry["say"] = FakeProvider("say")
    ledger.mark_exhausted("openai")
    lines, echo = _echoes()

    _audio, name, _ext = _run(registry, echo=echo)

    assert name == "say"
    assert registry["openai"].calls == []
    assert registry["openai"].checked  # check still ran; the ledger came after
    assert "openai: out of quota until next month — trying say" in lines


def test_characters_are_recorded_for_every_chunk_that_cost_money(registry):
    registry["elevenlabs"] = FakeProvider("elevenlabs", max_chars=20)
    text = "one two three four five six seven eight"

    _run(registry, text=text, chain=["elevenlabs"])

    chunks = registry["elevenlabs"].calls
    assert len(chunks) > 1
    assert ledger.status("elevenlabs")[0] == sum(len(c) for c in chunks)


def test_a_bytes_counting_provider_records_utf8_bytes(registry):
    registry["google"] = FakeProvider("google", count_unit="bytes")
    text = "ünïcödé — mit Umlauten"

    _run(registry, text=text)

    assert len(text.encode("utf-8")) > len(text)
    assert ledger.status("google") == (len(text.encode("utf-8")), False)


def test_a_bytes_counting_budget_is_measured_in_bytes(registry):
    registry["google"] = FakeProvider("google", count_unit="bytes")
    registry["say"] = FakeProvider("say")
    text = "üüüüüüüüüü"  # 10 characters, 20 bytes
    file_config = {"providers": {"google": {"monthly_chars": 15}}}

    assert _run(registry, text=text, file_config=file_config)[1] == "say"


# --- the cache ---------------------------------------------------------------


def test_a_cache_hit_costs_neither_a_request_nor_a_character(registry, tmp_path):
    registry["elevenlabs"] = FakeProvider("elevenlabs")
    settings = Settings(voice_id="elevenlabs-voice", provider="elevenlabs")
    cache.put("hello", settings, b"cached-audio", tmp_path, "mp3")

    audio, name, _ext = _run(registry, cache_dir=tmp_path)

    assert (audio, name) == (b"cached-audio", "elevenlabs")
    assert registry["elevenlabs"].calls == []
    assert ledger.status("elevenlabs") == (0, False)


def test_a_fresh_chunk_is_written_to_the_cache(registry, tmp_path):
    registry["elevenlabs"] = FakeProvider("elevenlabs")

    _run(registry, cache_dir=tmp_path)

    settings = Settings(voice_id="elevenlabs-voice", provider="elevenlabs")
    assert cache.get("hello", settings, tmp_path, "mp3") == b"[elevenlabs:hello]"


# --- chunking ----------------------------------------------------------------


def test_long_text_is_split_at_the_providers_own_cap_and_rejoined(registry):
    registry["elevenlabs"] = FakeProvider("elevenlabs", max_chars=30)
    # Distinct sentences on purpose: identical ones would be cache hits
    # after the first, and the split would look like one call.
    text = " ".join(f"Sentence number {n} here." for n in range(1, 7))
    lines, echo = _echoes()

    audio, _name, _ext = _run(registry, text=text, echo=echo)

    chunks = registry["elevenlabs"].calls
    assert len(chunks) > 1
    assert all(len(c) <= 30 for c in chunks)
    assert audio == b"".join(f"[elevenlabs:{c}]".encode() for c in chunks)
    assert f"Long input: splitting into {len(chunks)} chunks." in lines


def test_chunk_chars_wins_when_it_is_the_smaller_cap(registry):
    registry["elevenlabs"] = FakeProvider("elevenlabs", max_chars=9500)
    text = " ".join(f"Sentence number {n} here." for n in range(1, 11))

    _run(registry, text=text, chunk_chars=40)

    assert len(registry["elevenlabs"].calls) > 1
    assert all(len(c) <= 40 for c in registry["elevenlabs"].calls)


def test_a_provider_without_a_cap_gets_the_whole_text_in_one_call(registry):
    registry["say"] = FakeProvider("say", ext="m4a")
    text = "sentence. " * 500

    _run(registry, text=text)

    assert registry["say"].calls == [text]


def test_a_byte_cap_resplits_a_chunk_that_is_short_but_fat(registry):
    registry["google"] = FakeProvider("google", max_chars=40, max_bytes=30, count_unit="bytes")
    text = "ü" * 39 + " " + "ö" * 39

    _run(registry, text=text)

    chunks = registry["google"].calls
    assert len(chunks) > 2
    assert all(len(c.encode("utf-8")) <= 30 for c in chunks)


def test_chunks_that_already_fit_the_byte_cap_are_left_alone(registry):
    registry["google"] = FakeProvider("google", max_chars=40, max_bytes=4900)
    text = "plain ascii sentence. " * 5

    _run(registry, text=text)

    assert all(len(c) <= 40 for c in registry["google"].calls)


def test_an_unchunkable_format_refuses_to_be_joined(registry):
    # m4a frames cannot be concatenated; better a loud error than a file
    # that plays only its first piece.
    registry["say"] = FakeProvider("say", ext="m4a")

    with pytest.raises(AudioPlaybackError, match="cannot be chunked"):
        _run(registry, text="a much longer sentence than the cap", chunk_chars=10)


# --- overrides ---------------------------------------------------------------


def test_flags_reach_the_primary_provider_only(registry):
    registry["elevenlabs"] = FakeProvider(
        "elevenlabs", error=ProviderTransientError("elevenlabs", "HTTP 500")
    )
    registry["say"] = FakeProvider("say")

    _run(registry, overrides={"voice_id": "Rachel", "speed": 1.1})

    primary, fallback = registry.resolved
    assert primary == {"name": "elevenlabs", "primary": True, "voice_id": "Rachel", "speed": 1.1}
    assert fallback == {"name": "say", "primary": False}


def test_an_api_key_only_makes_sense_for_elevenlabs(registry):
    registry["openai"] = FakeProvider("openai")

    with pytest.raises(ConfigError, match="--api-key only applies to ElevenLabs"):
        _run(registry, chain=["openai"], overrides={"api_key": "sk-secret"})

    assert registry["openai"].checked == []


def test_an_api_key_error_does_not_suggest_a_login_a_provider_cannot_do(registry):
    # say has no key slot at all: `vocalize auth login --provider say` is
    # a command that refuses itself.
    registry["say"] = FakeProvider("say")

    with pytest.raises(ConfigError) as excinfo:
        _run(registry, chain=["say"], overrides={"api_key": "sk-secret"})

    message = str(excinfo.value)
    assert "macOS say takes no API key" in message
    assert "auth login" not in message
    assert registry["say"].checked == []


def test_the_api_key_error_names_the_env_var_for_a_key_holding_provider(registry):
    registry["openai"] = FakeProvider("openai")

    with pytest.raises(ConfigError) as excinfo:
        _run(registry, chain=["openai"], overrides={"api_key": "sk-secret"})

    assert "OPENAI_API_KEY" in str(excinfo.value)


@pytest.mark.parametrize("first", ["say", "openai"])
def test_the_api_key_guard_never_echoes_the_key_it_refuses(registry, first):
    # Both branches of the guard interpolate the provider name; neither may
    # ever put the rejected key into a message that reaches stderr.
    secret = "sk-supersecretcanary1234567890"
    registry[first] = FakeProvider(first)

    with pytest.raises(ConfigError) as excinfo:
        _run(registry, chain=[first], overrides={"api_key": secret})

    assert secret not in str(excinfo.value)
    assert "supersecretcanary" not in str(excinfo.value)


def test_the_api_key_reaches_elevenlabs_and_nothing_else(registry):
    registry["elevenlabs"] = FakeProvider("elevenlabs")

    _run(registry, overrides={"api_key": "sk-secret"})

    assert registry["elevenlabs"].checked == [
        (Settings(voice_id="elevenlabs-voice", provider="elevenlabs"), {"api_key": "sk-secret"})
    ]
    # The key is not a setting: it never reaches the settings resolver.
    assert registry.resolved == [{"name": "elevenlabs", "primary": True}]


# --- streaming ---------------------------------------------------------------


def _collect(results=None):
    """An on_chunk that records (name, bytes) and answers from `results`."""
    seen = []

    def on_chunk(path):
        seen.append((path.name, path.read_bytes()))
        if results:
            return results.pop(0)
        return True

    return seen, on_chunk


def test_streaming_hands_over_every_piece_in_order(registry):
    registry["kokoro"] = FakeProvider("kokoro", ext="wav", max_chars=25, streaming=True)
    seen, on_chunk = _collect()
    text = "First sentence here. Second sentence here. Third one."

    _run(registry, text=text, on_chunk=on_chunk)

    chunks = registry["kokoro"].calls
    assert len(chunks) > 1
    assert [name for name, _ in seen] == [f"{i}.wav" for i in range(1, len(chunks) + 1)]
    assert [data for _, data in seen] == registry["kokoro"].produced


def test_streaming_returns_the_pieces_stitched_into_one_file(registry):
    # The pieces were played one by one, but -o/last.wav still gets a
    # single WAV: frames appended, header written once.
    registry["kokoro"] = FakeProvider("kokoro", ext="wav", max_chars=25, streaming=True)
    _seen, on_chunk = _collect()
    text = "First sentence here. Second sentence here."

    audio, name, ext = _run(registry, text=text, on_chunk=on_chunk)

    assert (name, ext) == ("kokoro", "wav")
    assert len(registry["kokoro"].calls) > 1
    assert frame_count(audio) == sum(frame_count(p) for p in registry["kokoro"].produced)


def test_a_false_from_on_chunk_stops_the_read(registry):
    registry["kokoro"] = FakeProvider("kokoro", ext="wav", max_chars=25, streaming=True)
    seen, on_chunk = _collect(results=[True, False])
    text = "First sentence here. Second sentence here. Third one here."

    with pytest.raises(PlaybackStopped):
        _run(registry, text=text, on_chunk=on_chunk)

    assert len(seen) == 2  # stopped on the second piece, never rendered past it
    assert len(registry["kokoro"].calls) == 2


def test_a_failure_after_playback_started_never_falls_through(registry):
    # You can't un-hear the first half: switching provider mid-document
    # would replay it in another voice.
    registry["kokoro"] = FakeProvider(
        "kokoro", ext="wav", max_chars=25, streaming=True,
        error=[None, ProviderTransientError("kokoro", "worker died")],
    )
    registry["say"] = FakeProvider("say", ext="m4a")
    seen, on_chunk = _collect()
    text = "First sentence here. Second sentence here. Third one here."

    with pytest.raises(TTSRequestError) as excinfo:
        _run(registry, text=text, on_chunk=on_chunk)

    assert "kokoro: failed mid-read after playback started: worker died" in str(excinfo.value)
    assert len(seen) == 1
    assert registry["say"].calls == []


def test_a_failure_before_the_first_piece_still_falls_through(registry):
    registry["kokoro"] = FakeProvider(
        "kokoro", ext="wav", max_chars=25, streaming=True,
        error=ProviderTransientError("kokoro", "worker died"),
    )
    registry["say"] = FakeProvider("say")
    seen, on_chunk = _collect()

    _audio, name, _ext = _run(registry, text="First sentence here.", on_chunk=on_chunk)

    assert name == "say"
    assert seen == []


def test_a_stop_carries_the_audio_rendered_so_far(registry):
    # The CLI saves this when the "stop" was really a dead player: the
    # pieces were rendered and paid for before playback fell over.
    registry["kokoro"] = FakeProvider("kokoro", ext="wav", max_chars=25, streaming=True)
    _seen, on_chunk = _collect(results=[True, False])
    text = "First sentence here. Second sentence here. Third one here."

    with pytest.raises(PlaybackStopped) as excinfo:
        _run(registry, text=text, on_chunk=on_chunk)

    produced = registry["kokoro"].produced
    assert excinfo.value.audio_ext == "wav"
    assert frame_count(excinfo.value.audio) == sum(frame_count(p) for p in produced)


def test_a_provider_without_streaming_is_never_handed_a_piece(registry):
    registry["elevenlabs"] = FakeProvider("elevenlabs", max_chars=25)
    seen, on_chunk = _collect()

    _run(registry, text="First sentence here. Second sentence here.", on_chunk=on_chunk)

    assert len(registry["elevenlabs"].calls) > 1
    assert seen == []


def test_streamed_pieces_are_gone_once_the_run_returns(registry):
    registry["kokoro"] = FakeProvider("kokoro", ext="wav", max_chars=25, streaming=True)
    paths = []

    def on_chunk(path):
        paths.append(path)
        assert path.parent.stat().st_mode & 0o077 == 0  # 0700 tmpdir
        return True

    _run(registry, text="First sentence here. Second sentence here.", on_chunk=on_chunk)

    assert paths and not any(p.exists() for p in paths)


# --- what a stop leaves behind for a resume (DEC-003) ------------------


def _played(path, elapsed=1.5):
    """Pretend the CLI's player was killed while playing `path`."""
    audio_module._record_stop(Path(path), elapsed, True)


def test_an_interrupted_read_carries_the_text_nobody_heard(registry):
    # The CLI hands pieces over one ahead of the one playing, so the piece
    # that comes back False is not the piece the stop landed in. The read
    # continues after the piece the *player* had open — the one saved with
    # the record and replayed from its offset.
    registry["kokoro"] = FakeProvider("kokoro", ext="wav", max_chars=25, streaming=True)
    text = ("First sentence here. Second sentence here. Third one here. "
            "Fourth sentence here. Fifth sentence here.")
    chunks = chain._chunks_for(registry["kokoro"], text, None)
    assert len(chunks) == 5

    def on_chunk(path):
        if path.name == "3.wav":
            _played("vocalize-play/3.wav")  # a name, never opened
        return path.name != "5.wav"

    with pytest.raises(PlaybackStopped) as excinfo:
        _run(registry, text=text, on_chunk=on_chunk)

    assert excinfo.value.remaining_text == " ".join(chunks[3:])
    assert excinfo.value.provider == "kokoro"


def test_an_interrupted_read_falls_back_to_the_piece_it_was_handed(registry):
    # No usable piece name means the stop came from somewhere other than a
    # killed player (a broken one, say). Nothing after the handover was
    # heard either, so that piece is where the text resumes.
    registry["kokoro"] = FakeProvider("kokoro", ext="wav", max_chars=25, streaming=True)
    text = "First sentence here. Second sentence here. Third one here."
    chunks = chain._chunks_for(registry["kokoro"], text, None)
    _seen, on_chunk = _collect(results=[True, False])

    with pytest.raises(PlaybackStopped) as excinfo:
        _run(registry, text=text, on_chunk=on_chunk)

    assert excinfo.value.remaining_text == " ".join(chunks[1:])


def test_a_stop_from_another_read_never_decides_where_this_one_resumes(registry):
    # last_stop() is per process, and a piece number out of this read's
    # range is not this read's piece.
    registry["kokoro"] = FakeProvider("kokoro", ext="wav", max_chars=25, streaming=True)
    text = "First sentence here. Second sentence here. Third one here."
    chunks = chain._chunks_for(registry["kokoro"], text, None)
    _played("vocalize-play/97.wav")  # a name, never opened
    _seen, on_chunk = _collect(results=[True, False])

    with pytest.raises(PlaybackStopped) as excinfo:
        _run(registry, text=text, on_chunk=on_chunk)

    assert excinfo.value.remaining_text == " ".join(chunks[1:])
