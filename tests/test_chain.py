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
from vocalize import cache, chain
from vocalize.config import Settings
from vocalize.exceptions import (
    AudioPlaybackError,
    ConfigError,
    PlaybackStopped,
    ProviderContentError,
    ProviderTransientError,
    ProviderUnavailableError,
    TTSRequestError,
)
from vocalize.preprocess import split_for_synthesis


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
        max_bytes=None,
    ):
        self.NAME = name
        self.AUDIO_EXT = ext
        self.MAX_CHARS = max_chars
        self.DEFAULTS = {"voice": f"{name}-voice"}
        self.STREAMING = streaming
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

    def __init__(self):
        super().__init__()
        self.file_configs = []


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
        modules.file_configs.append(file_config)
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






def test_a_transient_error_moves_on(registry):
    registry["piper"] = FakeProvider("piper", error=ProviderTransientError("piper", "HTTP 503"))
    registry["say"] = FakeProvider("say")

    assert _run(registry)[1] == "say"


def test_an_unavailable_provider_moves_on(registry):
    registry["piper"] = FakeProvider(
        "piper", check_error=ProviderUnavailableError("piper", "model missing")
    )
    registry["say"] = FakeProvider("say")

    assert _run(registry)[1] == "say"
    assert registry["piper"].calls == []


def test_a_content_error_stops_the_chain(registry):
    boom = ProviderContentError("say", "invalid voice 'nope'")
    registry["say"] = FakeProvider("say", error=boom)
    registry["piper"] = FakeProvider("piper")

    with pytest.raises(ProviderContentError) as excinfo:
        _run(registry, chain=["say", "piper"])

    # The same exception, not a rewrapped one: a misconfigured voice is a
    # bug to fix, not something to route around.
    assert excinfo.value is boom
    assert registry["piper"].calls == []


def test_a_forced_single_provider_chain_never_falls_back(registry):
    registry["piper"] = FakeProvider("piper", error=ProviderTransientError("piper", "worker exited"))
    registry["say"] = FakeProvider("say")

    with pytest.raises(TTSRequestError):
        _run(registry, chain=["piper"])

    assert registry["say"].calls == []


def test_an_unexpected_exception_propagates_untouched(registry):
    registry["say"] = FakeProvider("say", error=TypeError("our own bug"))
    registry["piper"] = FakeProvider("piper")

    with pytest.raises(TypeError, match="our own bug"):
        _run(registry, chain=["say", "piper"])

    assert registry["piper"].calls == []


# --- what the user is told ---------------------------------------------------


def test_a_skip_names_the_provider_being_tried_next(registry):
    registry["kokoro"] = FakeProvider("kokoro", error=ProviderTransientError("kokoro", "worker unavailable"))
    registry["piper"] = FakeProvider("piper", error=ProviderUnavailableError("piper", "binary missing"))
    registry["say"] = FakeProvider("say")
    lines, echo = _echoes()

    _run(registry, echo=echo)

    assert "kokoro: worker unavailable — trying piper" in lines
    assert "piper: binary missing — trying say" in lines


def test_the_last_skip_says_there_is_nobody_left(registry):
    registry["piper"] = FakeProvider("piper", error=ProviderTransientError("piper", "worker exited"))
    lines, echo = _echoes()

    with pytest.raises(TTSRequestError):
        _run(registry, echo=echo)

    assert lines[-1] == "piper: worker exited — no providers left"


def test_a_fallback_announces_which_provider_actually_spoke(registry):
    registry["piper"] = FakeProvider("piper", error=ProviderTransientError("piper", "worker exited"))
    registry["say"] = FakeProvider("say")
    lines, echo = _echoes()

    _run(registry, echo=echo)

    assert "Spoke via say (fallback)." in lines


def test_the_primary_speaking_is_not_announced_as_a_fallback(registry):
    registry["piper"] = FakeProvider("piper")
    lines, echo = _echoes()

    _run(registry, echo=echo)

    assert not any("fallback" in line for line in lines)


_KOKORO_MISSING = ProviderUnavailableError("kokoro", "not installed — run: vocalize local install")
_KOKORO_NOTE = "Spoke via say (fallback) — Kokoro is not installed; run: vocalize local install"


def test_fallback_note_names_the_kokoro_install_when_the_primary_is_missing(registry):
    # The default chain on a fresh machine: Kokoro's model was never
    # installed, `say` speaks, and the line says why — once (DEC-022).
    registry["kokoro"] = FakeProvider("kokoro", check_error=_KOKORO_MISSING)
    registry["say"] = FakeProvider("say")
    lines, echo = _echoes()

    _run(registry, echo=echo)

    assert [line for line in lines if "Kokoro is not installed" in line] == [_KOKORO_NOTE]


def test_fallback_note_is_not_added_when_the_primary_was_another_local_provider(registry):
    registry["piper"] = FakeProvider("piper", error=ProviderTransientError("piper", "worker exited"))
    registry["say"] = FakeProvider("say")
    lines, echo = _echoes()

    _run(registry, echo=echo)

    assert "Spoke via say (fallback)." in lines
    assert not any("Kokoro is not installed" in line for line in lines)


def test_fallback_note_is_not_added_when_kokoro_was_not_the_primary(registry):
    registry["piper"] = FakeProvider("piper", error=ProviderTransientError("piper", "worker exited"))
    registry["kokoro"] = FakeProvider("kokoro", check_error=_KOKORO_MISSING)
    registry["say"] = FakeProvider("say")
    lines, echo = _echoes()

    _run(registry, echo=echo)

    assert "Spoke via say (fallback)." in lines
    assert not any("Kokoro is not installed" in line for line in lines)


def test_every_failure_is_listed_when_the_whole_chain_fails(registry):
    registry["kokoro"] = FakeProvider("kokoro", error=ProviderTransientError("kokoro", "worker unavailable"))
    registry["piper"] = FakeProvider(
        "piper", check_error=ProviderUnavailableError("piper", "model missing")
    )

    with pytest.raises(TTSRequestError) as excinfo:
        _run(registry)

    message = str(excinfo.value)
    assert "Every provider in the chain failed:" in message
    assert "  kokoro: worker unavailable" in message
    assert "  piper: model missing" in message
    assert 'add "say" to your chain' in message


def test_the_fallback_hint_is_not_offered_when_the_user_forced_one_provider(registry):
    # --provider turns fallback off on purpose; "add say to your chain" is
    # advice about a chain this run never had.
    registry["piper"] = FakeProvider(
        "piper", error=ProviderTransientError("piper", "worker unavailable")
    )

    with pytest.raises(TTSRequestError) as excinfo:
        _run(registry, chain=["piper"], forced=True)

    message = str(excinfo.value)
    assert "fallback is off with --provider" in message
    assert "no local fallback" not in message


def test_the_local_fallback_hint_stays_when_the_chain_was_not_forced(registry):
    registry["piper"] = FakeProvider(
        "piper", error=ProviderTransientError("piper", "worker unavailable")
    )

    with pytest.raises(TTSRequestError) as excinfo:
        _run(registry, chain=["piper"])

    message = str(excinfo.value)
    assert 'add "say" to your chain' in message
    assert "--provider" not in message


def test_no_local_fallback_hint_when_say_is_already_in_the_chain(registry):
    registry["say"] = FakeProvider("say", error=ProviderTransientError("say", "say failed"))

    with pytest.raises(TTSRequestError) as excinfo:
        _run(registry)

    assert "no local fallback" not in str(excinfo.value)


# --- no usage is written (review L1) ---------------------------------------


def test_speaking_writes_no_usage_file(registry, tmp_path, monkeypatch):
    registry["piper"] = FakeProvider("piper", max_chars=20)
    monkeypatch.setattr(chain, "DEFAULT_CACHE_DIR", tmp_path)

    _run(registry, text="one two three four five six seven eight", chain=["piper"])

    assert len(registry["piper"].calls) > 1
    assert not (tmp_path / "usage.json").exists()
    assert not (Path(registry.cache_dir) / "usage.json").exists()
    assert not hasattr(chain, "ledger")


# --- the cache ---------------------------------------------------------------


def test_a_cache_hit_costs_neither_a_request_nor_a_character(registry, tmp_path):
    registry["piper"] = FakeProvider("piper")
    settings = Settings(voice_id="piper-voice", provider="piper")
    cache.put("hello", settings, b"cached-audio", tmp_path, "mp3")

    audio, name, _ext = _run(registry, cache_dir=tmp_path)

    assert (audio, name) == (b"cached-audio", "piper")
    assert registry["piper"].calls == []


def test_a_fresh_chunk_is_written_to_the_cache(registry, tmp_path):
    registry["piper"] = FakeProvider("piper")

    _run(registry, cache_dir=tmp_path)

    settings = Settings(voice_id="piper-voice", provider="piper")
    assert cache.get("hello", settings, tmp_path, "mp3") == b"[piper:hello]"


# --- chunking ----------------------------------------------------------------


def test_long_text_is_split_at_the_providers_own_cap_and_rejoined(registry):
    registry["piper"] = FakeProvider("piper", max_chars=30)
    # Distinct sentences on purpose: identical ones would be cache hits
    # after the first, and the split would look like one call.
    text = " ".join(f"Sentence number {n} here." for n in range(1, 7))
    lines, echo = _echoes()

    audio, _name, _ext = _run(registry, text=text, echo=echo)

    chunks = registry["piper"].calls
    assert len(chunks) > 1
    assert all(len(c) <= 30 for c in chunks)
    assert audio == b"".join(f"[piper:{c}]".encode() for c in chunks)
    assert f"Long input: splitting into {len(chunks)} chunks." in lines


def test_chunk_chars_wins_when_it_is_the_smaller_cap(registry):
    registry["piper"] = FakeProvider("piper", max_chars=9500)
    text = " ".join(f"Sentence number {n} here." for n in range(1, 11))

    _run(registry, text=text, chunk_chars=40)

    assert len(registry["piper"].calls) > 1
    assert all(len(c) <= 40 for c in registry["piper"].calls)


def test_a_provider_without_a_cap_gets_the_whole_text_in_one_call(registry):
    registry["say"] = FakeProvider("say", ext="m4a")
    text = "sentence. " * 500

    _run(registry, text=text)

    assert registry["say"].calls == [text]


def test_a_byte_cap_resplits_a_chunk_that_is_short_but_fat(registry):
    registry["piper"] = FakeProvider("piper", max_chars=40, max_bytes=30)
    text = "ü" * 39 + " " + "ö" * 39

    _run(registry, text=text)

    chunks = registry["piper"].calls
    assert len(chunks) > 2
    assert all(len(c.encode("utf-8")) <= 30 for c in chunks)


def test_chunks_that_already_fit_the_byte_cap_are_left_alone(registry):
    registry["piper"] = FakeProvider("piper", max_chars=40, max_bytes=4900)
    text = "plain ascii sentence. " * 5

    _run(registry, text=text)

    assert all(len(c) <= 40 for c in registry["piper"].calls)


def test_an_unchunkable_format_refuses_to_be_joined(registry):
    # m4a frames cannot be concatenated; better a loud error than a file
    # that plays only its first piece.
    registry["say"] = FakeProvider("say", ext="m4a")

    with pytest.raises(AudioPlaybackError, match="cannot be chunked"):
        _run(registry, text="a much longer sentence than the cap", chunk_chars=10)


# --- overrides ---------------------------------------------------------------


def test_flags_reach_the_primary_provider_only(registry):
    registry["piper"] = FakeProvider(
        "piper", error=ProviderTransientError("piper", "worker exited")
    )
    registry["say"] = FakeProvider("say")

    _run(registry, overrides={"voice_id": "Rachel", "speed": 1.1})

    primary, fallback = registry.resolved
    assert primary == {"name": "piper", "primary": True, "voice_id": "Rachel", "speed": 1.1}
    assert fallback == {"name": "say", "primary": False}








@pytest.mark.parametrize("first", ["say", "piper"])
def test_the_api_key_guard_never_echoes_the_key_it_refuses(registry, first):
    # Both branches of the guard interpolate the provider name; neither may
    # ever put the rejected key into a message that reaches stderr.
    secret = "sk-supersecretcanary1234567890"
    registry[first] = FakeProvider(first)

    with pytest.raises(ConfigError) as excinfo:
        _run(registry, chain=[first], overrides={"api_key": secret})

    assert secret not in str(excinfo.value)
    assert "supersecretcanary" not in str(excinfo.value)




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
    registry["piper"] = FakeProvider("piper", max_chars=25)
    seen, on_chunk = _collect()

    _run(registry, text="First sentence here. Second sentence here.", on_chunk=on_chunk)

    assert len(registry["piper"].calls) > 1
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


@pytest.mark.parametrize("retired", ["elevenlabs", "openai", "google", "polly"])
def test_entire_chain_rejected_before_any_local_side_effect(registry, retired):
    registry["say"] = FakeProvider("say")
    with pytest.raises(ConfigError):
        _run(registry, chain=["say", retired])
    assert registry["say"].checked == []
    assert registry["say"].calls == []


def test_providers_never_see_a_retired_setting(registry, capsys):
    registry["say"] = FakeProvider("say")
    file_config = {
        "chain": ["elevenlabs", "say"],
        "providers": {
            "elevenlabs": {"api_key": "SECRET_CANARY"},
            "say": {"voice": "Samantha", "api_key": "SECRET_CANARY", "monthly_chars": 10},
        },
    }

    assert _run(registry, chain=["say"], file_config=file_config)[1] == "say"

    seen = registry.file_configs
    assert seen and all("SECRET_CANARY" not in repr(cfg) for cfg in seen)
    assert seen[0]["providers"] == {"say": {"voice": "Samantha"}}
    assert seen[0]["chain"] == ["say"]
    assert file_config["providers"]["say"]["api_key"] == "SECRET_CANARY"  # input untouched
    assert "SECRET_CANARY" not in capsys.readouterr().err


# --- a short first chunk, so sound starts soon (Mac Studio run, F4) ------
#
# With Kokoro's model loaded, a ~390-character first chunk took 2.4-4.9 s
# before any sound; one short sentence took 0.32 s. The first chunk of a
# streamed read is now the first sentence — or, for a sentence longer than
# about 120 characters, its first clause(s) — and every later boundary is
# exactly where it was.

_PARAGRAPH = (
    "The installer downloads the weights and checks each file. "
    "A file whose checksum does not match is deleted rather than kept, "
    "and the install stops with a message naming the command to run again. "
    "The verified models are stored under the cache directory. "
    "A small stamp file records which hashes were accepted, so a later "
    "status check can say whether the files on disk are the ones that "
    "were verified. If the checksum step fails, run the install command "
    "again with a stable network connection, then confirm the status "
    "output reports every engine as ready before you continue with the "
    "rest of the setup, which covers the recorder and the cleanup model."
)

_LONG_FIRST = (
    "When the model is already loaded and the text is long, the first piece "
    "of audio used to wait for the whole first chunk, which on this machine "
    "meant several seconds of silence; now it does not. Then the rest follows."
)


def _streaming(max_chars=400):
    return FakeProvider("kokoro", ext="wav", max_chars=max_chars, streaming=True)


def _words(chunks):
    return " ".join(chunks).split()


def test_the_first_chunk_of_a_streamed_read_is_its_first_sentence():
    chunks = chain._chunks_for(_streaming(), _PARAGRAPH, None)

    assert chunks[0] == "The installer downloads the weights and checks each file."
    assert _words(chunks) == _PARAGRAPH.split()  # the same words, in order


def test_only_the_first_boundary_moves():
    before = split_for_synthesis(_PARAGRAPH, 400)
    chunks = chain._chunks_for(_streaming(), _PARAGRAPH, None)

    assert len(before) > 1
    assert chunks[2:] == before[1:]  # later chunks keep today's size and text
    assert " ".join(chunks[:2]) == before[0]


def test_text_under_the_cap_is_still_split_after_its_first_sentence():
    text = "Hello there. This is the second sentence, which is a little longer."

    assert chain._chunks_for(_streaming(), text, None) == [
        "Hello there.", "This is the second sentence, which is a little longer.",
    ]


def test_a_long_first_sentence_is_cut_at_a_clause_never_inside_a_word():
    chunks = chain._chunks_for(_streaming(), _LONG_FIRST, None)

    assert len(chunks[0]) <= 120
    assert chunks[0][-1] in ",;:"
    assert _LONG_FIRST.startswith(chunks[0] + " ")
    assert _words(chunks) == _LONG_FIRST.split()


def test_a_long_first_sentence_without_a_clause_is_cut_between_words():
    text = " ".join(["word"] * 60) + ". Next."

    chunks = chain._chunks_for(_streaming(), text, None)

    assert len(chunks[0]) <= 120
    assert set(chunks[0].split()) == {"word"}
    assert _words(chunks) == text.split()


@pytest.mark.parametrize("text", [
    "One sentence only.",
    "  Leading space. And\nnewlines inside.\n\nA new paragraph here.  ",
    "Heading without a stop\n\nBody text follows here.",
    "a" * 450,
    "Decimal 12.5% and 3.45 p.m. stay whole. Then more.",
])
def test_the_spoken_words_never_change(text):
    before = split_for_synthesis(text, 400)
    chunks = chain._chunks_for(_streaming(), text, None)

    assert _words(chunks) == _words(before)
    assert all(chunk.strip() for chunk in chunks)
    assert all(len(chunk) <= 400 for chunk in chunks)


def test_a_provider_that_does_not_stream_keeps_its_chunks():
    provider = FakeProvider("piper", ext="wav", max_chars=400, streaming=False)

    assert chain._chunks_for(provider, _PARAGRAPH, None) == split_for_synthesis(_PARAGRAPH, 400)


def test_a_short_streamed_text_is_not_called_long_input(registry):
    registry["kokoro"] = _streaming()
    lines, echo = _echoes()

    _run(registry, text="Hello there. And again.", echo=echo)

    assert registry["kokoro"].calls == ["Hello there.", "And again."]
    assert not any(line.startswith("Long input") for line in lines)


def test_the_cache_answers_every_chunk_of_a_repeated_streamed_read(registry):
    registry["kokoro"] = _streaming()

    first, _, _ = _run(registry, text=_PARAGRAPH)
    rendered = list(registry["kokoro"].calls)
    again, _, _ = _run(registry, text=_PARAGRAPH)

    assert registry["kokoro"].calls == rendered  # nothing rendered twice
    assert again == first


def test_a_stop_after_the_short_first_chunk_resumes_with_every_unheard_word(registry):
    registry["kokoro"] = _streaming()
    chunks = chain._chunks_for(registry["kokoro"], _PARAGRAPH, None)
    _seen, on_chunk = _collect(results=[True, False])

    with pytest.raises(PlaybackStopped) as excinfo:
        _run(registry, text=_PARAGRAPH, on_chunk=on_chunk)

    assert excinfo.value.remaining_text == " ".join(chunks[1:])
    assert (chunks[0] + " " + excinfo.value.remaining_text).split() == _PARAGRAPH.split()
