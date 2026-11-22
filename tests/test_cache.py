import hashlib
import os
from pathlib import Path

import pytest

from vocalize import cache
from vocalize.config import Settings


def test_local_key_preserves_existing_provider_namespace():
    # The second field held a model name no engine read; it is always empty
    # now, so an entry saved with the default (empty) model still hits.
    settings = Settings(voice_id="af_heart", output_format="wav", provider="kokoro")
    expected = hashlib.sha256(b"af_heart||wav|hello|provider=kokoro").hexdigest()
    assert cache.cache_key("hello", settings) == expected
    assert cache.cache_key("hello", Settings(voice_id="af_heart", provider="piper")) != expected


def test_the_key_changes_with_voice_speed_engine_and_text_only():
    base = Settings(voice_id="af_heart", provider="kokoro")
    key = cache.cache_key("hello", base)
    assert cache.cache_key("hello", Settings(voice_id="af_heart", provider="kokoro")) == key
    for other in (
        Settings(voice_id="af_bella", provider="kokoro"),
        Settings(voice_id="af_heart", provider="kokoro", speed=0.9),
        Settings(voice_id="af_heart", provider="say"),
    ):
        assert cache.cache_key("hello", other) != key
    assert cache.cache_key("hello!", base) != key
    assert "model_id" not in Settings.__dataclass_fields__


def test_speed_and_language_separate_entries():
    base = Settings(voice_id="voice", provider="kokoro")
    assert cache.cache_key("hi", base) != cache.cache_key("hi", Settings(voice_id="voice", provider="kokoro", speed=1.1))
    assert cache.cache_key("hi", base) != cache.cache_key("hi", Settings(voice_id="voice", provider="kokoro", language="fr"))


def test_put_get_private_under_permissive_umask(tmp_path):
    root = tmp_path / "audio"
    settings = Settings(provider="kokoro")
    previous = os.umask(0o022)
    try:
        cache.put("fixture", settings, b"synthetic audio", root, "wav")
    finally:
        os.umask(previous)
    assert root.stat().st_mode & 0o777 == 0o700
    assert cache.cache_path("fixture", settings, root, "wav").stat().st_mode & 0o777 == 0o600
    assert cache.get("fixture", settings, root, "wav") == b"synthetic audio"
    assert cache.get("fixture", settings, root, "mp3") is None


def test_existing_cache_directory_is_tightened(tmp_path):
    root = tmp_path / "audio"
    root.mkdir(mode=0o755)
    cache.put("fixture", Settings(), b"audio", root, "wav")
    assert root.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory"])
def test_redirected_or_nonregular_entry_is_refused(tmp_path, kind):
    root = tmp_path / "audio"
    root.mkdir(mode=0o700)
    victim = tmp_path / "outside"
    victim.write_bytes(b"untouched")
    victim.chmod(0o600)
    settings = Settings()
    entry = cache.cache_path("fixture", settings, root, "wav")
    if kind == "symlink":
        entry.symlink_to(victim)
    elif kind == "hardlink":
        os.link(victim, entry)
    else:
        entry.mkdir()
    assert cache.get("fixture", settings, root, "wav") is None
    cache.put("fixture", settings, b"replacement", root, "wav")
    assert victim.read_bytes() == b"untouched"
    assert cache.get("fixture", settings, root, "wav") is None


def test_symlink_cache_ancestor_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    redirected = tmp_path / "redirect"
    redirected.symlink_to(outside, target_is_directory=True)
    cache.put("fixture", Settings(), b"private", redirected / "audio", "wav")
    assert not list(outside.iterdir())
    assert cache.get("fixture", Settings(), redirected / "audio", "wav") is None


def test_failed_replace_keeps_complete_previous_entry(tmp_path, monkeypatch):
    settings = Settings()
    cache.put("fixture", settings, b"original", tmp_path, "wav")
    def fail_replace(source, target):
        assert Path(source).read_bytes() == b"replacement"
        assert Path(source).stat().st_mode & 0o777 == 0o600
        assert Path(target).read_bytes() == b"original"
        raise OSError("synthetic disk failure")
    monkeypatch.setattr(cache.os, "replace", fail_replace)
    cache.put("fixture", settings, b"replacement", tmp_path, "wav")
    assert cache.get("fixture", settings, tmp_path, "wav") == b"original"
    assert not list(tmp_path.glob(".audio-*"))


def test_old_public_and_oversized_entries_are_misses(tmp_path, monkeypatch):
    settings = Settings()
    cache.put("fixture", settings, b"audio", tmp_path, "wav")
    entry = cache.cache_path("fixture", settings, tmp_path, "wav")
    entry.chmod(0o644)
    assert cache.get("fixture", settings, tmp_path, "wav") is None
    entry.chmod(0o600)
    monkeypatch.setattr(cache, "MAX_CACHE_BYTES", 2)
    assert cache.get("fixture", settings, tmp_path, "wav") is None
    cache.put("fixture", settings, b"too much", tmp_path, "wav")
    assert entry.read_bytes() == b"audio"


def test_none_or_unwritable_cache_is_nonfatal(tmp_path, monkeypatch):
    cache.put("fixture", Settings(), b"audio", None, "wav")
    assert cache.get("fixture", Settings(), None, "wav") is None
    def deny(*a, **kw):
        raise PermissionError("read-only")
    monkeypatch.setattr(Path, "mkdir", deny)
    cache.put("fixture", Settings(), b"audio", tmp_path / "nope", "wav")


def test_cache_extension_cannot_be_a_path(tmp_path):
    with pytest.raises(ValueError):
        cache.cache_path("fixture", Settings(), tmp_path, "../../outside")


def _cache_tree(tmp_path):
    root = tmp_path / "vocalize"
    root.mkdir(mode=0o700)
    settings = Settings(provider="kokoro")
    cache.put("one", settings, b"1234", root, "wav")
    cache.put("two", settings, b"12", root, "mp3")
    (root / ".audio-leftover").write_bytes(b"1")
    keep = [root / "app.log", root / "models" / "kokoro" / ("a" * 64 + ".wav"),
            root / "envs" / "kokoro-x" / "bin" / "python", root / ("a" * 63 + ".wav")]
    for path in keep:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"keep")
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"keep")
    (root / ("b" * 64 + ".wav")).symlink_to(outside)
    return root, keep, outside


def test_usage_counts_only_audio_cache_files(tmp_path):
    root, _keep, _outside = _cache_tree(tmp_path)
    assert cache.usage(root) == (3, 7)


def test_clear_removes_only_audio_cache_files(tmp_path):
    root, keep, outside = _cache_tree(tmp_path)
    assert cache.clear(root) == (3, 7)
    assert cache.usage(root) == (0, 0)
    assert all(path.read_bytes() == b"keep" for path in keep)
    assert outside.read_bytes() == b"keep"
    assert (root / ("b" * 64 + ".wav")).is_symlink()


def test_missing_cache_is_empty(tmp_path):
    assert cache.usage(tmp_path / "absent") == (0, 0)
    assert cache.clear(tmp_path / "absent") == (0, 0)


def test_a_redirected_cache_is_never_cleared(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    victim = real / ("a" * 64 + ".wav")
    victim.write_bytes(b"keep")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    assert cache.usage(link) == (0, 0)
    with pytest.raises(OSError):
        cache.clear(link)
    assert victim.read_bytes() == b"keep"
