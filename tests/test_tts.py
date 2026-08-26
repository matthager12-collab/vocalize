import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from vocalize.config import Settings
from vocalize.exceptions import TTSRequestError
from vocalize.tts import _cache_key, list_voices, synthesize


class FakeTTSNamespace:
    def __init__(self, chunks=(b"fake", b"-audio"), raise_error=None):
        self._chunks = chunks
        self._raise_error = raise_error
        self.calls = []

    def convert(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise_error:
            raise self._raise_error
        return iter(self._chunks)


class FakeVoicesNamespace:
    def __init__(self, voices):
        self._voices = voices

    def search(self):
        return SimpleNamespace(voices=self._voices)


class FakeClient:
    def __init__(self, chunks=(b"fake", b"-audio"), raise_error=None, voices=None):
        self.text_to_speech = FakeTTSNamespace(chunks=chunks, raise_error=raise_error)
        self.voices = FakeVoicesNamespace(voices or [])


def test_synthesize_returns_joined_audio_bytes(tmp_path):
    client = FakeClient(chunks=(b"hello", b"-world"))
    settings = Settings(voice_id="v1", model_id="m1")

    audio = synthesize(client, "hi", settings, cache_dir=tmp_path)

    assert audio == b"hello-world"
    assert client.text_to_speech.calls[0]["text"] == "hi"
    assert client.text_to_speech.calls[0]["voice_id"] == "v1"


def test_synthesize_uses_cache_on_second_call(tmp_path):
    client = FakeClient(chunks=(b"only-once",))
    settings = Settings(voice_id="v1", model_id="m1")

    first = synthesize(client, "cache me", settings, cache_dir=tmp_path)
    second = synthesize(client, "cache me", settings, cache_dir=tmp_path)

    assert first == second == b"only-once"
    # the underlying API should only have been hit once
    assert len(client.text_to_speech.calls) == 1


def test_unreadable_cache_entry_falls_back_to_a_fresh_call(tmp_path):
    client = FakeClient(chunks=(b"fresh",))
    settings = Settings(voice_id="v1", model_id="m1")

    synthesize(client, "hi", settings, cache_dir=tmp_path)

    # A directory in the entry's place still reports exists(), but
    # read_bytes() raises OSError.
    entry = next(tmp_path.glob("*.mp3"))
    entry.unlink()
    entry.mkdir()

    assert synthesize(client, "hi", settings, cache_dir=tmp_path) == b"fresh"
    assert len(client.text_to_speech.calls) == 2


def test_unwritable_cache_dir_still_returns_the_audio(tmp_path, monkeypatch):
    client = FakeClient(chunks=(b"paid-for",))
    settings = Settings()

    def deny(*args, **kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "mkdir", deny)

    assert synthesize(client, "hi", settings, cache_dir=tmp_path / "nope") == b"paid-for"


def test_synthesize_rejects_empty_text(tmp_path):
    client = FakeClient()
    settings = Settings()

    with pytest.raises(TTSRequestError, match="empty"):
        synthesize(client, "   ", settings, cache_dir=tmp_path)


def test_synthesize_wraps_sdk_errors(tmp_path):
    client = FakeClient(raise_error=RuntimeError("rate limited"))
    settings = Settings()

    with pytest.raises(TTSRequestError, match="rate limited"):
        synthesize(client, "hello", settings, cache_dir=tmp_path)


def test_list_voices_returns_id_and_name():
    fake_voice = SimpleNamespace(voice_id="abc123", name="Rachel")
    client = FakeClient(voices=[fake_voice])

    result = list_voices(client)

    assert result == [{"id": "abc123", "name": "Rachel"}]


def test_speed_is_passed_through_as_voice_settings(tmp_path):
    client = FakeClient()
    settings = Settings(voice_id="v1", model_id="m1", speed=1.1)

    synthesize(client, "hi", settings, cache_dir=tmp_path)

    assert client.text_to_speech.calls[0]["voice_settings"].speed == 1.1


def test_unset_speed_sends_no_voice_settings_kwarg(tmp_path):
    client = FakeClient()
    settings = Settings(voice_id="v1", model_id="m1")

    synthesize(client, "hi", settings, cache_dir=tmp_path)

    assert "voice_settings" not in client.text_to_speech.calls[0]


def test_cache_key_differs_by_speed():
    unset = Settings(voice_id="v1", model_id="m1")
    faster = Settings(voice_id="v1", model_id="m1", speed=1.1)

    assert _cache_key("hi", unset) != _cache_key("hi", faster)


def test_cache_key_is_unchanged_when_speed_is_unset():
    # Pins the pre-speed payload scheme so caches written by older
    # versions keep hitting.
    settings = Settings(voice_id="v1", model_id="m1", output_format="f1")
    old_payload = f"{settings.voice_id}|{settings.model_id}|{settings.output_format}|hi"

    assert _cache_key("hi", settings) == hashlib.sha256(old_payload.encode("utf-8")).hexdigest()
