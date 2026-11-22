import os
import threading
import time

import pytest
from click.testing import CliRunner

from vocalize import config, wizard
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
        "verbatim": True, "max_take_seconds": 600, "vocabulary": ["Kokoro", "repository root"],
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
            "kokoro": {"voice": "af_bella", "language": "en-us", "speed": 0.9},
            "piper": {"voice": "en_US-amy-medium", "speed": 1.1},
            "say": {"voice": "Samantha", "speed": 1.0},
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
    assert all(set(t) == set(config.KNOWN_PROVIDER_KEYS[name])
               for name, t in data["providers"].items())

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


# --- a save that would drop comments keeps a backup first (L4) ---------------


COMMENTED = '# my voices\nchain = ["say"]\n\n[stt]\n  # tuned by hand\nmax_seconds = 30\n'


def test_the_first_save_over_a_commented_file_backs_it_up_at_0600(tmp_path):
    import stat as stat_module

    path = tmp_path / "config.toml"
    path.write_text(COMMENTED)

    result = wizard.write_config_if_unchanged(
        path, {"chain": ["kokoro"]}, wizard.fingerprint_config(path)
    )

    backup = tmp_path / "config.toml.bak"
    assert result.backup == backup
    assert backup.read_text() == COMMENTED
    assert stat_module.S_IMODE(backup.stat().st_mode) == 0o600
    text, fingerprint = result  # callers that unpack two values keep working
    assert path.read_text() == text == 'chain = ["kokoro"]\n'
    assert fingerprint == wizard.fingerprint_config(path)


def test_an_existing_backup_is_never_overwritten(tmp_path):
    path = tmp_path / "config.toml"
    backup = tmp_path / "config.toml.bak"
    backup.write_text("older backup\n")
    path.write_text(COMMENTED)

    result = wizard.write_config_if_unchanged(
        path, {"chain": ["kokoro"]}, wizard.fingerprint_config(path)
    )

    assert result.backup is None
    assert backup.read_text() == "older backup\n"
    assert path.read_text() == 'chain = ["kokoro"]\n'


def test_a_second_save_makes_no_second_backup(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(COMMENTED)
    first = wizard.write_config_if_unchanged(
        path, {"chain": ["kokoro"]}, wizard.fingerprint_config(path)
    )
    path.write_text("# a new comment\n" + path.read_text())

    second = wizard.write_config_if_unchanged(path, {"chain": ["say"]}, wizard.fingerprint_config(path))

    assert first.backup is not None and second.backup is None
    assert (tmp_path / "config.toml.bak").read_text() == COMMENTED


@pytest.mark.parametrize("before", [None, 'chain = ["say"]\nvoice_label = "a # b"\n'])
def test_no_backup_without_a_comment_line(tmp_path, before):
    path = tmp_path / "config.toml"
    if before is not None:
        path.write_text(before)

    result = wizard.write_config_if_unchanged(
        path, {"chain": ["kokoro"]}, wizard.fingerprint_config(path)
    )

    assert result.backup is None
    assert not (tmp_path / "config.toml.bak").exists()


def test_a_backup_that_cannot_be_written_stops_the_save(tmp_path, monkeypatch):
    import os

    path = tmp_path / "config.toml"
    path.write_text(COMMENTED)
    real_open = os.open

    def refuse_backup(name, flags, *args, **kwargs):
        if str(name).endswith(".bak"):
            raise PermissionError(13, "denied")
        return real_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", refuse_backup)
    with pytest.raises(ConfigError, match="back up"):
        wizard.write_config_if_unchanged(path, {"chain": ["kokoro"]}, wizard.fingerprint_config(path))
    assert path.read_text() == COMMENTED


# --- a table vocalize does not use never blocks a save (review L5) -----------


def _parse(text):
    try:
        import tomllib
    except ImportError:  # Python 3.10
        import tomli as tomllib
    return tomllib.loads(text)


UNKNOWN_TABLES = (
    'chain = ["say"]\n\n[meetings]\ntitle = "Weekly \\"sync\\""\nminutes = 30\nratio = 0.5\n'
    'on = true\nnames = ["a", "b", [1, 2]]\nrows = [{x = 1}, {x = 2}]\n\n'
    '[meetings.room]\nfloor = 3\n\n[meetings.room.desk]\nlamp = false\n\n'
    '[later_feature]\n"odd key" = "kept"\n'
)


def test_an_unknown_table_is_kept_as_it_was_with_one_notice(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(UNKNOWN_TABLES)
    data = _parse(UNKNOWN_TABLES)

    result = wizard.write_config_if_unchanged(
        path, {**data, "chain": ["kokoro"]}, wizard.fingerprint_config(path))

    reread = _parse(path.read_text())
    assert reread == {**data, "chain": ["kokoro"]}
    assert len(result.notices) == 2
    assert "[meetings]" in result.notices[0] and "does not use it" in result.notices[0]
    assert "[later_feature]" in result.notices[1]
    assert all("Weekly" not in notice and "odd key" not in notice for notice in result.notices)
    assert result.backup is None  # nothing was lost


def test_an_array_of_tables_vocalize_does_not_use_is_kept(tmp_path):
    body = 'chain = ["say"]\n\n[[later]]\nname = "one"\n\n[[later]]\nname = "two"\n'
    path = tmp_path / "config.toml"
    path.write_text(body)

    result = wizard.write_config_if_unchanged(path, _parse(body), wizard.fingerprint_config(path))

    assert _parse(path.read_text()) == _parse(body)
    assert len(result.notices) == 1 and "[later]" in result.notices[0]


def test_an_unknown_table_that_cannot_be_copied_is_dropped_after_a_backup(tmp_path):
    body = 'chain = ["say"]\n\n[meetings]\nwhen = 1979-05-27\nkept = "x"\n'
    path = tmp_path / "config.toml"
    path.write_text(body)

    result = wizard.write_config_if_unchanged(path, _parse(body), wizard.fingerprint_config(path))

    assert _parse(path.read_text()) == {"chain": ["say"]}
    assert result.backup == tmp_path / "config.toml.bak"
    assert result.backup.read_text() == body
    (notice,) = result.notices
    assert "[meetings]" in notice and "1979" not in notice
    assert result.backup_has_retired is False


def test_a_trailing_comment_alone_is_backed_up(tmp_path):
    body = 'chain = ["say"]  # the quiet voice\n'
    path = tmp_path / "config.toml"
    path.write_text(body)

    result = wizard.write_config_if_unchanged(
        path, {"chain": ["kokoro"]}, wizard.fingerprint_config(path))

    assert result.backup == tmp_path / "config.toml.bak"
    assert result.backup.read_bytes() == body.encode()


@pytest.mark.parametrize("body", [
    'chain = ["say"]\nlabel = "a # b"\n',
    "chain = [\"say\"]\nlabel = 'a # b'\n",
    'chain = ["say"]\nlabel = """\nline # not a comment\n"""\n',
    'chain = ["say"]\nlabel = "quote \\" # still inside"\n',
])
def test_a_hash_inside_a_string_is_not_a_comment(tmp_path, body):
    path = tmp_path / "config.toml"
    path.write_text(body)
    _parse(body)  # a real TOML file

    result = wizard.write_config_if_unchanged(
        path, {"chain": ["kokoro"]}, wizard.fingerprint_config(path))

    assert result.backup is None


def test_a_save_that_drops_a_retired_setting_is_backed_up_and_says_so(tmp_path):
    body = 'chain = ["say"]\n\n[providers.kokoro]\nvoice = "af_bella"\nmodel = "x"\n' \
           '\n[providers.openai]\napi_key = "SECRET_CANARY"\n'
    path = tmp_path / "config.toml"
    path.write_text(body)
    data = config.without_retired_settings(_parse(body))  # what the loader hands on

    result = wizard.write_config_if_unchanged(path, data, wizard.fingerprint_config(path))

    assert "SECRET_CANARY" not in path.read_text() and "model" not in path.read_text()
    assert result.backup == tmp_path / "config.toml.bak"
    assert result.backup.read_bytes() == body.encode()  # byte for byte, key included
    assert result.backup_has_retired is True
    text, fingerprint = result
    assert text == path.read_text() and fingerprint == wizard.fingerprint_config(path)


def test_no_retired_flag_without_a_backup(tmp_path):
    path = tmp_path / "config.toml"
    (tmp_path / "config.toml.bak").write_text("older\n")
    path.write_text('api_key = "SECRET_CANARY"\nchain = ["say"]\n')

    result = wizard.write_config_if_unchanged(
        path, {"chain": ["say"]}, wizard.fingerprint_config(path))

    assert result.backup is None and result.backup_has_retired is False
    assert (tmp_path / "config.toml.bak").read_text() == "older\n"
