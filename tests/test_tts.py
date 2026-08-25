from pathlib import Path
from types import SimpleNamespace

import pytest

from vocalize.config import Settings
from vocalize.exceptions import TTSRequestError
from vocalize.tts import list_voices, synthesize


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
