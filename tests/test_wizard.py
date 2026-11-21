import os
import threading
import time

import pytest
from click.testing import CliRunner

from vocalize import wizard
from vocalize.cli import main
from vocalize.config import load_config_file
from vocalize.exceptions import (
    ConfigChangedError,
    ConfigError,
)

UP = "\x1b[A"
DOWN = "\x1b[B"
ENTER = "\r"

VOICES = [{"id": "abc123", "name": "Rachel"}, {"id": "def456", "name": "Josh"}]


def test_config_prints_local_guidance_without_a_terminal(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    runner = CliRunner()

    result = runner.invoke(main, ["config"])

    assert result.exit_code == 0
    assert "portal --no-browser" in result.output
    assert not (tmp_path / "config" / "vocalize" / "config.toml").exists()


def test_render_config_text_puts_flat_keys_before_provider_tables():
    # providers listed first in the dict on purpose: the renderer must not
    # just echo insertion order — flat keys always come first, because a
    # bare key after a [section] header would parse into that section.
    data = {
        "providers": {
            "piper": {"voice": "en_US-amy-medium"},
            "say": {"voice": "Samantha"},
        },
        "overflow": "ask",
        "chain": ["kokoro", "piper", "say"],
    }

    text = wizard._render_config_text(data)

    assert text == (
        'overflow = "ask"\n'
        'chain = ["kokoro", "piper", "say"]\n'
        "\n[providers.piper]\n"
        'voice = "en_US-amy-medium"\n'
        "\n[providers.say]\n"
        'voice = "Samantha"\n'
    )


# --- the [stt] table in the renderer ------------------------------------


def test_render_config_text_writes_the_stt_table(tmp_path):
    """0.10.0 ships `[stt]`, so without this every writer of the file —
    the wizard, `vocalize chain` and the portal — refuses to rewrite a
    dictation user's config at all."""
    data = {
        "stt": {"model": "base.en", "max_seconds": 30},
        "chain": ["say"],
        "providers": {"say": {"voice": "Samantha"}},
    }

    text = wizard._render_config_text(data)

    assert text == (
        'chain = ["say"]\n'
        "\n[stt]\n"
        'model = "base.en"\n'
        "max_seconds = 30\n"
        "\n[providers.say]\n"
        'voice = "Samantha"\n'
    )


def test_an_stt_config_round_trips_through_the_renderer(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = tmp_path / "vocalize" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        'chain = ["say"]\n\n[stt]\nmodel = "base.en"\ncleanup = "local"\n', encoding="utf-8"
    )

    once = wizard._render_config_text(load_config_file())
    path.write_text(once, encoding="utf-8")

    assert "[stt]" in once  # not "unchanged" by being silently dropped
    assert load_config_file()["stt"] == {"model": "base.en", "cleanup": "local"}
    assert wizard._render_config_text(load_config_file()) == once


def test_a_notes_table_is_written_back_as_transcribe(monkeypatch, tmp_path):
    """`_TABLE_KEYS` excludes a table from the flat pass; only an explicit
    block writes it back. [notes] is the old name: its keys come back under
    [transcribe], which wins a key both set, and the summary keys go."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = tmp_path / "vocalize" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        'chain = ["say"]\n\n[notes]\nfolder = "~/Old"\nkeep_audio = true\n'
        'summarizer = "local"\ntemplate = "memo"\n\n'
        '[transcribe]\nfolder = "~/New"\n\n[stt]\nmodel = "base.en"\n',
        encoding="utf-8",
    )

    once = wizard._render_config_text(load_config_file())
    path.write_text(once, encoding="utf-8")

    assert "[notes]" not in once and "summarizer" not in once and "template" not in once
    assert load_config_file()["transcribe"] == {"folder": "~/New", "keep_audio": True}
    assert load_config_file()["stt"] == {"model": "base.en"}
    assert wizard._render_config_text(load_config_file()) == once


def test_a_speech_table_is_written_not_refused():
    """The writer refused any config holding [speech]: it was missing from
    `_TABLE_KEYS`, so the flat pass met a table and raised."""
    text = wizard._render_config_text({"chain": ["say"], "speech": {"headings": "plain"}})

    assert text == 'chain = ["say"]\n\n[speech]\nheadings = "plain"\n'


def test_the_writer_drops_retired_keys_without_writing_their_values():
    data = {
        "voice": "old", "model": "eleven", "speed": 1.1, "api_key": "SECRET_CANARY",
        "chain": ["openai", "kokoro"],
        "providers": {"openai": {"api_key": "SECRET_CANARY"}, "kokoro": {"api_key": "x", "voice": "af_bella"}},
        "stt": {"cleanup": "claude-cli"},
    }

    text = wizard._render_config_text(data)

    assert text == (
        'chain = ["kokoro"]\n'
        '\n[stt]\ncleanup = "off"\n'
        '\n[providers.kokoro]\nvoice = "af_bella"\n'
    )
    assert data["providers"]["openai"]["api_key"] == "SECRET_CANARY"  # input untouched


def test_every_table_and_every_key_survives_a_save(monkeypatch, tmp_path):
    from vocalize import config

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("VOCALIZE_CHAIN", raising=False)
    for var in ("VOCALIZE_VOICE", "VOCALIZE_MODEL", "VOCALIZE_SPEED",
                "VOCALIZE_OVERFLOW", "VOCALIZE_MAX_CHARS"):
        monkeypatch.delenv(var, raising=False)
    stt = {
        "engine": "whisper", "model": "small.en", "language": "en",
        "input_device": "Studio Mic", "cleanup": "local", "paste": True,
        "max_seconds": 30, "sounds": False, "cues": "both", "beam_size": 3,
        "verbatim": True, "max_take_seconds": 600, "vocabulary": ["Hermes", "Mat Hager"],
        "warm_minutes": 15,
    }
    speech = {
        "headings": "plain", "blockquotes": "off", "nested_lists": "flat",
        "parentheticals": "drop", "abbreviations": "protect", "citations": "speak",
        "references": False, "furniture": False, "urls": "full", "emoji": False,
        "bracket_max_chars": 200, "furniture_max_chars": 80, "furniture_min_repeats": 4,
        "SPEECH_BRACKET_MAX_CHARS": 200, "SPEECH_FURNITURE_MAX_CHARS": 80,
        "SPEECH_FURNITURE_MIN_REPEATS": 4,
    }
    data = {
        "max_chars": 4000,
        "overflow": "never",
        "chain": ["piper", "kokoro", "say"],
        "providers": {
            "kokoro": {"voice": "af_bella", "model": "kokoro-v1.0", "language": "en-us", "speed": 0.9},
            "piper": {"voice": "en_US-amy-medium", "model": "amy", "language": "en_US", "speed": 1.1},
            "say": {"voice": "Samantha", "model": "system", "language": "en_US", "speed": 1.0},
        },
        "stt": stt,
        "transcribe": {"folder": "~/Transcripts", "keep_audio": True, "model": "base.en"},
        "app": {
            "dictate": "ctrl+cmd+d", "dictate_mode": "hold", "speak": "ctrl+cmd+s",
            "stop": "ctrl+cmd+x", "stop_hotkey": "pause",
        },
        "speech": speech,
    }
    # Every key the loader knows is set, so a key added later fails here.
    assert set(data) == set(config.KNOWN_CONFIG_KEYS) - {"notes"}
    assert set(stt) == set(config.KNOWN_STT_KEYS)
    assert set(speech) == set(config.KNOWN_SPEECH_KEYS)
    assert set(data["transcribe"]) == set(config.KNOWN_TRANSCRIBE_KEYS)
    assert set(data["app"]) == set(config.KNOWN_APP_KEYS)
    assert all(set(t) == set(config.KNOWN_PROVIDER_KEYS) for t in data["providers"].values())

    path = config.config_path()
    wizard.write_config_if_unchanged(path, data, wizard.ABSENT_CONFIG)
    loaded = load_config_file()

    assert loaded == data
    for resolve in (config.resolve_stt, config.resolve_transcribe, config.resolve_app,
                    config.resolve_speech, config.resolve_chain):
        assert resolve(file_config=loaded) == resolve(file_config=data)
    assert config.resolve_overflow(file_config=loaded) == ("never", 4000)
    for name in data["providers"]:
        assert (config.resolve_provider_settings(name, loaded)
                == config.resolve_provider_settings(name, data))


def test_a_non_table_stt_value_is_refused_not_stringified():
    with pytest.raises(ConfigError, match="edit that file by hand"):
        wizard._render_config_text({"stt": 3})


# --- fingerprint_config (DEC-005) ---------------------------------------


def test_a_fingerprint_notices_a_touch_that_changed_no_bytes(tmp_path):
    """mtime is half of it: identical bytes written twice are still two writes."""
    import os

    path = tmp_path / "config.toml"
    path.write_text("x = 1\n")
    before = wizard.fingerprint_config(path)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    assert wizard.fingerprint_config(path) != before


def test_a_fingerprint_notices_bytes_that_changed_under_the_same_mtime(tmp_path):
    """sha256 is the other half: a write inside one filesystem timestamp."""
    import os

    path = tmp_path / "config.toml"
    path.write_text("x = 1\n")
    before = wizard.fingerprint_config(path)
    stat = path.stat()
    path.write_text("x = 2\n")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    assert wizard.fingerprint_config(path) != before


def test_a_missing_file_fingerprints_as_the_absent_sentinel(tmp_path):
    assert wizard.fingerprint_config(tmp_path / "nope.toml") == wizard.ABSENT_CONFIG


def test_both_fingerprint_shapes_survive_a_json_round_trip(tmp_path):
    """The page holds this in a browser and hands it back as JSON."""
    import json

    path = tmp_path / "config.toml"
    path.write_text("x = 1\n")
    for value in (wizard.fingerprint_config(path), wizard.ABSENT_CONFIG):
        assert json.loads(json.dumps(value)) == value


def test_an_unreadable_config_path_raises_configerror(tmp_path):
    """Not a bare OSError: every caller here funnels through ConfigError."""
    directory = tmp_path / "config.toml"
    directory.mkdir()

    with pytest.raises(ConfigError, match="Could not read config file"):
        wizard.fingerprint_config(directory)


# --- write_config_if_unchanged (DEC-005) --------------------------------


def test_an_unchanged_file_is_written_with_exactly_the_rendered_text(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('chain = ["say"]\n')
    data = {"chain": ["kokoro"], "stt": {"model": "base.en"}}

    text, _ = wizard.write_config_if_unchanged(
        path, data, wizard.fingerprint_config(path)
    )

    assert text == wizard._render_config_text(data)
    assert path.read_text() == text


def test_a_file_whose_contents_changed_underneath_is_refused(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('chain = ["say"]\n')
    fingerprint = wizard.fingerprint_config(path)
    path.write_text('chain = ["kokoro"]\n')  # somebody else

    with pytest.raises(ConfigChangedError, match="reload"):
        wizard.write_config_if_unchanged(path, {"chain": ["polly"]}, fingerprint)

    assert path.read_text() == 'chain = ["kokoro"]\n'


def test_a_file_whose_mtime_changed_underneath_is_refused(tmp_path):
    import os

    path = tmp_path / "config.toml"
    path.write_text('chain = ["say"]\n')
    fingerprint = wizard.fingerprint_config(path)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    with pytest.raises(ConfigChangedError):
        wizard.write_config_if_unchanged(path, {"chain": ["polly"]}, fingerprint)


def test_a_file_deleted_underneath_a_real_fingerprint_is_refused(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('chain = ["say"]\n')
    fingerprint = wizard.fingerprint_config(path)
    path.unlink()

    with pytest.raises(ConfigChangedError):
        wizard.write_config_if_unchanged(path, {"chain": ["polly"]}, fingerprint)


def test_an_absent_fingerprint_creates_the_file_at_0600(tmp_path):
    import stat as stat_module

    path = tmp_path / "sub" / "config.toml"

    wizard.write_config_if_unchanged(path, {"chain": ["say"]}, wizard.ABSENT_CONFIG)

    assert path.read_text() == 'chain = ["say"]\n'
    assert stat_module.S_IMODE(path.stat().st_mode) == 0o600


def test_a_file_that_appeared_under_an_absent_fingerprint_is_refused(tmp_path):
    """O_EXCL: the check and the create are one operation on this path."""
    path = tmp_path / "config.toml"
    path.write_text('chain = ["kokoro"]\n')  # it appeared

    with pytest.raises(ConfigChangedError, match="reload"):
        wizard.write_config_if_unchanged(path, {"chain": ["say"]}, wizard.ABSENT_CONFIG)

    assert path.read_text() == 'chain = ["kokoro"]\n'


def test_an_ordinary_rewrite_does_not_widen_the_file_mode(tmp_path):
    """os.replace swaps the inode, so the temp file's mode is the file's."""
    import stat as stat_module

    path = tmp_path / "config.toml"
    wizard.write_config_if_unchanged(path, {"chain": ["say"]}, wizard.ABSENT_CONFIG)

    wizard.write_config_if_unchanged(
        path, {"chain": ["kokoro"]}, wizard.fingerprint_config(path)
    )

    assert stat_module.S_IMODE(path.stat().st_mode) == 0o600


def test_an_unrenderable_value_writes_nothing_on_either_path(tmp_path):
    """The render runs before the file is touched at all.

    On the O_EXCL path that matters most: the open would otherwise create
    the file, and the render would then raise over a new empty config that
    was not there a moment ago.
    """
    path = tmp_path / "config.toml"

    with pytest.raises(ConfigError):
        wizard.write_config_if_unchanged(
            path, {"chain": [["nested"]]}, wizard.ABSENT_CONFIG
        )
    assert not path.exists()

    original = 'chain = ["say"]\n'
    path.write_text(original)

    with pytest.raises(ConfigError):
        wizard.write_config_if_unchanged(
            path, {"chain": [["nested"]]}, wizard.fingerprint_config(path)
        )

    assert path.read_text() == original


def test_the_returned_fingerprint_describes_what_was_written(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('chain = ["say"]\n')

    _, written = wizard.write_config_if_unchanged(
        path, {"chain": ["kokoro"]}, wizard.fingerprint_config(path)
    )

    assert written == wizard.fingerprint_config(path)


def test_the_returned_fingerprint_fails_safe_against_a_racing_writer(tmp_path, monkeypatch):
    """Read back from the file instead and the caller is handed the
    intruder's state as its own — and its next write would clobber them."""
    import os

    path = tmp_path / "config.toml"
    path.write_text('chain = ["say"]\n')
    real_replace = os.replace

    def replace_then_race(src, dst):
        real_replace(src, dst)
        os.utime(dst, ns=(0, 0))  # somebody else wrote, right after the rename

    monkeypatch.setattr(os, "replace", replace_then_race)
    _, written = wizard.write_config_if_unchanged(
        path, {"chain": ["kokoro"]}, wizard.fingerprint_config(path)
    )
    # The one patch back, not every patch: `undo()` drops the autouse
    # fixtures that keep this suite off the developer's own config file.
    monkeypatch.setattr(os, "replace", real_replace)

    assert written != wizard.fingerprint_config(path)
    with pytest.raises(ConfigChangedError):
        wizard.write_config_if_unchanged(path, {"chain": ["polly"]}, written)


def test_two_threads_holding_one_fingerprint_cannot_both_write(tmp_path, monkeypatch):
    """One page, two saves, one process — the portal's own case.

    The comparison and the rename are two steps. Without a lock across the
    pair, both threads compare against a file neither has changed yet, both
    rename, both answer "written", and one of the two changes is gone with
    nothing on the page to say so. The sleep only widens the gap that is
    already there; it does not create it.
    """
    path = tmp_path / "config.toml"
    path.write_text('chain = ["say"]\n')
    fingerprint = wizard.fingerprint_config(path)

    real_fingerprint = wizard.fingerprint_config

    def unhurried(target):
        seen = real_fingerprint(target)
        time.sleep(0.05)
        return seen

    monkeypatch.setattr(wizard, "fingerprint_config", unhurried)

    outcomes = []
    ready = threading.Barrier(2)

    def save(name):
        ready.wait(timeout=10)
        try:
            wizard.write_config_if_unchanged(path, {"chain": [name]}, fingerprint)
            outcomes.append("written")
        except ConfigChangedError:
            outcomes.append("refused")

    threads = [threading.Thread(target=save, args=(n,)) for n in ("kokoro", "polly")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(outcomes) == ["refused", "written"]


def test_two_writers_at_once_do_not_render_through_one_temp_file(tmp_path, monkeypatch):
    """Every writer in the project shared one `config.toml.tmp`.

    Both threads are held at the rename until the other has finished
    rendering, which is the interleave the fixed name loses to: the second
    writer truncates the first's render, and whichever renames second finds
    its own temp file already gone.
    """
    path = tmp_path / "config.toml"
    real_replace = os.replace
    at_rename = threading.Barrier(2)

    def replace_once_both_have_rendered(src, dst):
        at_rename.wait(timeout=10)
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace_once_both_have_rendered)

    names = ("kokoro", "polly")
    failures = []

    def save(name):
        try:
            wizard._write_config(path, {"chain": [name]})
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name}: {exc}")

    threads = [threading.Thread(target=save, args=(n,)) for n in names]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert failures == []
    assert path.read_text() in {
        wizard._render_config_text({"chain": [name]}) for name in names
    }
    assert list(tmp_path.glob("*.tmp")) == []


def test_a_failed_rename_leaves_no_temp_file_behind(tmp_path, monkeypatch):
    """A unique name no longer cleans itself up by being reused."""
    path = tmp_path / "config.toml"

    def refuse(src, dst):
        raise OSError("no")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(ConfigError):
        wizard._write_config(path, {"chain": ["say"]})

    assert list(tmp_path.glob("*.tmp")) == []


def test_an_app_config_round_trips_through_the_renderer(monkeypatch, tmp_path):
    """The same rule as [notes]: `_TABLE_KEYS` only excludes; an explicit
    block writes [app] back, or `vocalize chain` would drop it."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = tmp_path / "vocalize" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        'chain = ["say"]\n\n[app]\ndictate = "ctrl+cmd+d"\ndictate_mode = "hold"\n\n'
        '[stt]\nmodel = "base.en"\n',
        encoding="utf-8",
    )

    once = wizard._render_config_text(load_config_file())
    path.write_text(once, encoding="utf-8")

    assert "[app]" in once
    assert load_config_file()["app"] == {"dictate": "ctrl+cmd+d", "dictate_mode": "hold"}
    assert wizard._render_config_text(load_config_file()) == once
