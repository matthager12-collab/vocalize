import hashlib
from pathlib import Path

from vocalize import cache
from vocalize.config import Settings


def _sha(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_elevenlabs_key_is_byte_identical_to_the_shipped_formula():
    # Pinned, not derived: every cache entry ElevenLabs users already have
    # on disk was written under exactly this payload.
    settings = Settings(voice_id="v1", model_id="m1", output_format="f1")

    assert cache.cache_key("hi", settings) == _sha("v1|m1|f1|hi")


def test_elevenlabs_key_with_speed_is_byte_identical():
    settings = Settings(voice_id="v1", model_id="m1", output_format="f1", speed=1.1)

    assert cache.cache_key("hi", settings) == _sha("v1|m1|f1|hi|1.1")


def test_another_provider_gets_its_own_key():
    eleven = Settings(voice_id="v1", model_id="m1", output_format="f1")
    google = Settings(voice_id="v1", model_id="m1", output_format="f1", provider="google")

    assert cache.cache_key("hi", google) != cache.cache_key("hi", eleven)
    assert cache.cache_key("hi", google) == _sha("v1|m1|f1|hi|provider=google")


def test_language_and_region_are_part_of_a_non_elevenlabs_key():
    base = Settings(voice_id="v1", model_id="m1", output_format="f1", provider="polly")
    localized = Settings(
        voice_id="v1", model_id="m1", output_format="f1", provider="polly",
        language="en-GB", region="eu-west-1",
    )

    assert cache.cache_key("hi", localized) == _sha(
        "v1|m1|f1|hi|provider=polly|lang=en-GB|region=eu-west-1"
    )
    assert cache.cache_key("hi", localized) != cache.cache_key("hi", base)


def test_language_and_region_never_touch_the_elevenlabs_key():
    plain = Settings(voice_id="v1", model_id="m1", output_format="f1")
    noisy = Settings(
        voice_id="v1", model_id="m1", output_format="f1", language="en-GB", region="eu-west-1"
    )

    assert cache.cache_key("hi", noisy) == cache.cache_key("hi", plain)


def test_put_then_get_round_trips(tmp_path):
    settings = Settings(voice_id="v1", model_id="m1")

    assert cache.get("hi", settings, tmp_path, "mp3") is None

    cache.put("hi", settings, b"audio", tmp_path, "mp3")

    assert cache.get("hi", settings, tmp_path, "mp3") == b"audio"
    assert cache.cache_path("hi", settings, tmp_path, "mp3").suffix == ".mp3"


def test_extension_keeps_providers_apart_on_disk(tmp_path):
    settings = Settings(voice_id="Samantha", provider="say")

    cache.put("hi", settings, b"m4a-audio", tmp_path, "m4a")

    assert cache.get("hi", settings, tmp_path, "m4a") == b"m4a-audio"
    assert cache.get("hi", settings, tmp_path, "mp3") is None


def test_an_unreadable_entry_is_a_miss_not_an_error(tmp_path):
    settings = Settings(voice_id="v1", model_id="m1")
    cache.put("hi", settings, b"audio", tmp_path, "mp3")

    # A directory in the entry's place still reports exists().
    entry = cache.cache_path("hi", settings, tmp_path, "mp3")
    entry.unlink()
    entry.mkdir()

    assert cache.get("hi", settings, tmp_path, "mp3") is None


def test_an_unwritable_cache_dir_is_not_an_error(tmp_path, monkeypatch):
    settings = Settings(voice_id="v1", model_id="m1")

    def deny(*args, **kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "mkdir", deny)

    cache.put("hi", settings, b"audio", tmp_path / "nope", "mp3")  # must not raise


def test_none_cache_dir_disables_the_cache(tmp_path):
    settings = Settings(voice_id="v1", model_id="m1")

    cache.put("hi", settings, b"audio", None, "mp3")

    assert cache.get("hi", settings, None, "mp3") is None
    assert list(tmp_path.iterdir()) == []
