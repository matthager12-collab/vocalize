import pytest

from vocalize.config import (
    DEFAULT_CHAIN,
    DEFAULT_MODEL,
    DEFAULT_VOICE,
    load_config_file,
    resolve_chain,
    resolve_overflow,
    resolve_provider_settings,
    resolve_settings,
)
from vocalize.exceptions import ConfigError


def _isolate(monkeypatch, tmp_path, body=None):
    """Point the config loader at tmp_path and clear the setting env vars."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for var in ("VOCALIZE_VOICE", "VOCALIZE_MODEL", "VOCALIZE_SPEED"):
        monkeypatch.delenv(var, raising=False)
    path = tmp_path / "vocalize" / "config.toml"
    if body is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return path


def _isolate_overflow(monkeypatch, tmp_path, body=None):
    """Same as _isolate, for the overflow/max_chars env vars."""
    path = _isolate(monkeypatch, tmp_path, body)
    for var in ("VOCALIZE_OVERFLOW", "VOCALIZE_MAX_CHARS"):
        monkeypatch.delenv(var, raising=False)
    return path


def test_overflow_defaults_to_truncate_with_no_cap(monkeypatch, tmp_path):
    _isolate_overflow(monkeypatch, tmp_path)
    assert resolve_overflow() == ("truncate", None)


def test_overflow_flag_beats_env_beats_file(monkeypatch, tmp_path):
    _isolate_overflow(monkeypatch, tmp_path, 'overflow = "never"\nmax_chars = 300\n')
    assert resolve_overflow() == ("never", 300)

    monkeypatch.setenv("VOCALIZE_OVERFLOW", "ask")
    monkeypatch.setenv("VOCALIZE_MAX_CHARS", "200")
    assert resolve_overflow() == ("ask", 200)

    assert resolve_overflow(overflow="truncate", max_chars=100) == ("truncate", 100)


def test_default_max_chars_sits_below_every_real_source(monkeypatch, tmp_path):
    _isolate_overflow(monkeypatch, tmp_path)
    assert resolve_overflow(default_max_chars=500) == ("truncate", 500)

    monkeypatch.setenv("VOCALIZE_MAX_CHARS", "200")
    assert resolve_overflow(default_max_chars=500) == ("truncate", 200)


def test_config_file_max_chars_beats_the_caller_default(monkeypatch, tmp_path):
    _isolate_overflow(monkeypatch, tmp_path, "max_chars = 300\n")
    assert resolve_overflow(default_max_chars=500) == ("truncate", 300)


@pytest.mark.parametrize("value", ["shout", "", "truncate please"])
def test_invalid_overflow_mode_is_rejected_with_its_source(monkeypatch, tmp_path, value):
    _isolate_overflow(monkeypatch, tmp_path)
    monkeypatch.setenv("VOCALIZE_OVERFLOW", value)

    with pytest.raises(ConfigError) as excinfo:
        resolve_overflow()

    assert "VOCALIZE_OVERFLOW" in str(excinfo.value)


@pytest.mark.parametrize("value", ["lots", "0", "-5"])
def test_invalid_max_chars_is_rejected_with_its_source(monkeypatch, tmp_path, value):
    _isolate_overflow(monkeypatch, tmp_path)
    monkeypatch.setenv("VOCALIZE_MAX_CHARS", value)

    with pytest.raises(ConfigError) as excinfo:
        resolve_overflow()

    assert "VOCALIZE_MAX_CHARS" in str(excinfo.value)


def test_overflow_mode_is_case_insensitive(monkeypatch, tmp_path):
    _isolate_overflow(monkeypatch, tmp_path)
    monkeypatch.setenv("VOCALIZE_OVERFLOW", "Never")
    assert resolve_overflow() == ("never", None)


def _load_stt(monkeypatch, tmp_path, body):
    from vocalize.config import load_config_file

    _isolate(monkeypatch, tmp_path, body)
    return load_config_file()


def test_stt_table_loads_with_valid_values(monkeypatch, tmp_path):
    from vocalize.config import resolve_stt

    data = _load_stt(
        monkeypatch, tmp_path,
        '[stt]\nmodel = "base.en"\nlanguage = "en"\ncleanup = "local"\n'
        'max_seconds = 30\ninput_device = "Built-in Microphone"\n',
    )

    resolved = resolve_stt(data)
    assert resolved["model"] == "base.en"
    assert resolved["cleanup"] == "local"
    assert resolved["max_seconds"] == 30
    assert resolved["input_device"] == "Built-in Microphone"
    # Untouched keys still come from the defaults.
    assert resolved["sounds"] is True
    assert resolved["paste"] is False


def test_stt_defaults_apply_with_no_table_at_all(monkeypatch, tmp_path):
    from vocalize.config import STT_DEFAULTS, resolve_stt

    assert resolve_stt(_load_stt(monkeypatch, tmp_path, "")) == STT_DEFAULTS


def test_an_unknown_stt_key_warns_but_still_loads(monkeypatch, tmp_path, capsys):
    from vocalize.config import resolve_stt

    data = _load_stt(monkeypatch, tmp_path, '[stt]\nmodel = "base.en"\nmodle = "typo"\n')

    assert resolve_stt(data)["model"] == "base.en"
    assert "unknown config key 'modle' in [stt]" in capsys.readouterr().err


@pytest.mark.parametrize(
    "model",
    ['"tiny.en"', '"../../etc/passwd"', '"--serve"', '"small.en\\u0007"', "12"],
)
def test_an_stt_model_off_the_allowlist_is_refused(monkeypatch, tmp_path, model):
    with pytest.raises(ConfigError) as excinfo:
        _load_stt(monkeypatch, tmp_path, f"[stt]\nmodel = {model}\n")

    assert "stt.model" in str(excinfo.value)


@pytest.mark.parametrize("language", ['"klingon"', '"--foo"', '"en; rm -rf /"', "7"])
def test_an_stt_language_off_the_allowlist_is_refused(monkeypatch, tmp_path, language):
    with pytest.raises(ConfigError) as excinfo:
        _load_stt(monkeypatch, tmp_path, f"[stt]\nlanguage = {language}\n")

    assert "stt.language" in str(excinfo.value)


def test_an_english_only_stt_model_with_another_language_is_refused(monkeypatch, tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        _load_stt(monkeypatch, tmp_path, '[stt]\nmodel = "small.en"\nlanguage = "fr"\n')

    assert "English-only" in str(excinfo.value)


def test_the_default_stt_model_is_multilingual_so_any_language_loads(monkeypatch, tmp_path):
    # No model line at all: the default is turbo q5_0, which is not an .en
    # model, so a non-English language is accepted (it was refused while
    # small.en was the default).
    from vocalize.config import resolve_stt

    data = _load_stt(monkeypatch, tmp_path, '[stt]\nlanguage = "de"\n')
    assert resolve_stt(data)["language"] == "de"


def test_a_multilingual_stt_model_accepts_another_language(monkeypatch, tmp_path):
    from vocalize.config import resolve_stt

    data = _load_stt(
        monkeypatch, tmp_path, '[stt]\nmodel = "large-v3-turbo-q5_0"\nlanguage = "fr"\n'
    )
    assert resolve_stt(data)["language"] == "fr"


@pytest.mark.parametrize("value", ["0", '"abc"', "601", "-1", "12.5", "true"])
def test_an_out_of_range_stt_max_seconds_is_refused(monkeypatch, tmp_path, value):
    with pytest.raises(ConfigError) as excinfo:
        _load_stt(monkeypatch, tmp_path, f"[stt]\nmax_seconds = {value}\n")

    assert "stt.max_seconds" in str(excinfo.value)


@pytest.mark.parametrize("value", ["1", "600", "120"])
def test_stt_max_seconds_at_the_edges_is_accepted(monkeypatch, tmp_path, value):
    from vocalize.config import resolve_stt

    data = _load_stt(monkeypatch, tmp_path, f"[stt]\nmax_seconds = {value}\n")
    assert resolve_stt(data)["max_seconds"] == int(value)


def test_max_take_seconds_bounds(monkeypatch, tmp_path):
    from vocalize.config import ConfigError, resolve_stt

    assert resolve_stt({})["max_take_seconds"] == 1800

    for val in (60, 1800, 7200):
        data = _load_stt(monkeypatch, tmp_path, f"[stt]\nmax_take_seconds = {val}\n")
        assert resolve_stt(data)["max_take_seconds"] == val

    for bad in (59, 7201, -1, 0, '"abc"', "120.5", "true", "false"):
        with pytest.raises(ConfigError) as excinfo:
            _load_stt(monkeypatch, tmp_path, f"[stt]\nmax_take_seconds = {bad}\n")
        assert "stt.max_take_seconds" in str(excinfo.value)


@pytest.mark.parametrize("value", ["0", "9", "-1", '"5"', "2.5", "true"])
def test_an_out_of_range_stt_beam_size_is_refused(monkeypatch, tmp_path, value):
    with pytest.raises(ConfigError) as excinfo:
        _load_stt(monkeypatch, tmp_path, f"[stt]\nbeam_size = {value}\n")

    assert "stt.beam_size" in str(excinfo.value)


@pytest.mark.parametrize("value", ["1", "8", "5"])
def test_stt_beam_size_at_the_edges_is_accepted(monkeypatch, tmp_path, value):
    from vocalize.config import resolve_stt

    data = _load_stt(monkeypatch, tmp_path, f"[stt]\nbeam_size = {value}\n")
    assert resolve_stt(data)["beam_size"] == int(value)


@pytest.mark.parametrize("literal, expected", [("false", "off"), ('"off"', "off"), ('"local"', "local")])
def test_stt_cleanup_accepts_the_enum_and_coerces_legacy_bools(monkeypatch, tmp_path, literal, expected):
    from vocalize.config import resolve_stt

    data = _load_stt(monkeypatch, tmp_path, f"[stt]\ncleanup = {literal}\n")
    assert resolve_stt(data)["cleanup"] == expected


@pytest.mark.parametrize("literal", ['"claude"', '"yes"', "1", '"ON"', '"Claude-CLI"'])
def test_an_stt_cleanup_off_the_enum_is_refused(monkeypatch, tmp_path, literal):
    with pytest.raises(ConfigError) as excinfo:
        _load_stt(monkeypatch, tmp_path, f"[stt]\ncleanup = {literal}\n")

    assert "stt.cleanup" in str(excinfo.value)


def test_stt_cleanup_defaults_to_off_and_verbatim_to_false(monkeypatch, tmp_path):
    from vocalize.config import resolve_stt

    resolved = resolve_stt(_load_stt(monkeypatch, tmp_path, "[stt]\nmodel = \"small.en\"\n"))
    assert resolved["cleanup"] == "off" and resolved["verbatim"] is False


def test_an_stt_verbatim_that_is_not_a_bool_is_refused(monkeypatch, tmp_path):
    with pytest.raises(ConfigError):
        _load_stt(monkeypatch, tmp_path, '[stt]\nverbatim = "yes"\n')


# --- [transcribe], read under its old name [notes] too (DEC-007) ------------


def test_transcribe_defaults_apply_with_no_table_at_all(monkeypatch, tmp_path):
    from vocalize.config import TRANSCRIBE_DEFAULTS, resolve_notes, resolve_transcribe

    data = _load_stt(monkeypatch, tmp_path, "")
    assert resolve_transcribe(data) == TRANSCRIBE_DEFAULTS
    assert set(TRANSCRIBE_DEFAULTS) == {"folder", "keep_audio", "model"}
    assert resolve_notes(data) == TRANSCRIBE_DEFAULTS


@pytest.mark.parametrize("section", ["transcribe", "notes"])
def test_transcribe_table_loads_under_either_name(monkeypatch, tmp_path, section):
    from vocalize.config import resolve_notes, resolve_transcribe

    data = _load_stt(
        monkeypatch, tmp_path,
        f'[{section}]\nfolder = "~/Notes"\nkeep_audio = true\nmodel = "small.en"\n',
    )
    expected = {"folder": "~/Notes", "keep_audio": True, "model": "small.en"}
    assert resolve_transcribe(data) == expected
    assert resolve_notes(data) == expected


def test_transcribe_wins_a_key_both_tables_set(monkeypatch, tmp_path):
    from vocalize.config import resolve_transcribe

    data = _load_stt(
        monkeypatch, tmp_path,
        '[notes]\nfolder = "~/Old"\nkeep_audio = true\n\n[transcribe]\nfolder = "~/New"\n',
    )
    assert resolve_transcribe(data) == {"folder": "~/New", "keep_audio": True, "model": ""}


@pytest.mark.parametrize("section", ["transcribe", "notes"])
@pytest.mark.parametrize("line", [
    'keep_audio = "no"',
    'folder = ""',
    'model = "nope"',
])
def test_a_bad_transcribe_value_is_refused(monkeypatch, tmp_path, section, line):
    with pytest.raises(ConfigError, match=rf"{section}\."):
        _load_stt(monkeypatch, tmp_path, f"[{section}]\n{line}\n")


@pytest.mark.parametrize("body", ['notes = "keep me"\n', 'transcribe = "keep me"\n'])
def test_a_transcribe_value_that_is_not_a_table_is_refused(monkeypatch, tmp_path, body):
    with pytest.raises(ConfigError):
        _load_stt(monkeypatch, tmp_path, body)


def test_an_unknown_transcribe_key_warns_but_still_loads(monkeypatch, tmp_path, capsys):
    data = _load_stt(monkeypatch, tmp_path, '[transcribe]\ncolour = "blue"\n')

    assert data["transcribe"] == {"colour": "blue"}
    assert "unknown config key 'colour' in [transcribe]" in capsys.readouterr().err


# --- provider values are short strings (issue #5) -----------------------------


@pytest.mark.parametrize("key", ["voice", "model", "language"])
def test_a_provider_voice_type_that_is_not_a_string_is_refused(monkeypatch, tmp_path, key):
    with pytest.raises(ConfigError) as excinfo:
        _load_stt(monkeypatch, tmp_path, f"[providers.kokoro]\n{key} = 12345\n")

    message = str(excinfo.value)
    assert key in message and "[providers.kokoro]" in message


@pytest.mark.parametrize("literal", ['"-flag"', '""', '"' + "x" * 129 + '"', '"a\u0007b"'])
def test_a_provider_voice_type_that_is_flag_shaped_empty_or_long_is_refused(monkeypatch, tmp_path, literal):
    with pytest.raises(ConfigError):
        _load_stt(monkeypatch, tmp_path, f"[providers.kokoro]\nvoice = {literal}\n")


def test_a_plain_provider_voice_string_still_loads(monkeypatch, tmp_path):
    data = _load_stt(monkeypatch, tmp_path, '[providers.kokoro]\nvoice = "en-GB-Neural2-A"\n')
    assert data["providers"]["kokoro"]["voice"] == "en-GB-Neural2-A"






def test_stt_beam_size_defaults_to_five(monkeypatch, tmp_path):
    from vocalize.config import resolve_stt

    data = _load_stt(monkeypatch, tmp_path, "[stt]\nmodel = \"small.en\"\n")
    assert resolve_stt(data)["beam_size"] == 5


@pytest.mark.parametrize(
    "literal",
    [
        '"--foo"',                 # flag-shaped: would become a recorder option
        '"a\\nb"',                 # control character: a newline in a device name
        '"mic\\u001b[31m"',        # an escape sequence a terminal would obey
        '"' + "x" * 129 + '"',     # over the length cap
        "42",                      # not a string at all
    ],
)
def test_a_bad_shaped_stt_input_device_is_refused(monkeypatch, tmp_path, literal):
    with pytest.raises(ConfigError) as excinfo:
        _load_stt(monkeypatch, tmp_path, f"[stt]\ninput_device = {literal}\n")

    assert "stt.input_device" in str(excinfo.value)


def test_an_empty_stt_input_device_is_the_system_default(monkeypatch, tmp_path):
    from vocalize.config import resolve_stt

    data = _load_stt(monkeypatch, tmp_path, '[stt]\ninput_device = ""\n')
    assert resolve_stt(data)["input_device"] == ""


@pytest.mark.parametrize("key", ["cleanup", "paste", "sounds"])
def test_a_non_boolean_stt_flag_is_refused(monkeypatch, tmp_path, key):
    with pytest.raises(ConfigError) as excinfo:
        _load_stt(monkeypatch, tmp_path, f'[stt]\n{key} = "yes"\n')

    assert f"stt.{key}" in str(excinfo.value)


@pytest.mark.parametrize("value", ['"chime"', "1"])
def test_an_invalid_stt_cues_value_is_refused(monkeypatch, tmp_path, value):
    with pytest.raises(ConfigError) as excinfo:
        _load_stt(monkeypatch, tmp_path, f"[stt]\ncues = {value}\n")

    message = str(excinfo.value)
    assert "stt.cues" in message
    assert "sounds" in message and "words" in message and "both" in message


@pytest.mark.parametrize("value", ["sounds", "words", "both"])
def test_each_stt_cues_mode_is_accepted(monkeypatch, tmp_path, value):
    from vocalize.config import resolve_stt

    data = _load_stt(monkeypatch, tmp_path, f'[stt]\ncues = "{value}"\n')
    assert resolve_stt(data)["cues"] == value


def test_stt_cues_defaults_to_sounds(monkeypatch, tmp_path):
    from vocalize.config import resolve_stt

    assert resolve_stt(_load_stt(monkeypatch, tmp_path, ""))["cues"] == "sounds"


def test_an_stt_value_that_is_not_a_table_is_refused(monkeypatch, tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        _load_stt(monkeypatch, tmp_path, 'stt = "small.en"\n')

    assert "'stt'" in str(excinfo.value)


def test_resolve_stt_revalidates_a_hand_built_dict():
    """The portal and any other caller hand this a dict it never read."""
    from vocalize.config import resolve_stt

    with pytest.raises(ConfigError):
        resolve_stt({"stt": {"model": "--serve"}})


def test_an_unknown_key_warns_once_however_often_the_file_is_read(
    monkeypatch, tmp_path, capsys
):
    """The portal re-reads and re-validates the file on every /api/state poll.

    One typo used to write a stderr line every few seconds for the life of
    the process, burying the portal's own lockout and idle-shutdown notices,
    which print to the same terminal.
    """
    import vocalize.config as config_module

    monkeypatch.setattr(config_module, "_warned", set())
    _isolate(monkeypatch, tmp_path, 'voise = "typo"\n[stt]\nmodle = "typo"\n')

    config_module.load_config_file()
    assert "unknown config key" in capsys.readouterr().err

    config_module.load_config_file()
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("section", ["notes", "transcribe"])
def test_a_summary_template_or_summarizer_is_retired_whatever_its_value(
    monkeypatch, tmp_path, section
):
    from vocalize import config

    monkeypatch.setattr(config, "_warned", set())
    data = _load_stt(
        monkeypatch, tmp_path, f'[{section}]\ntemplate = "-rf.md"\nsummarizer = 3\n'
    )
    assert data[section] == {}
    notices = config.retired_notices({section: {"template": "x", "summarizer": "y"}})
    assert [n for n in notices if f"{section}.template" in n] != []
    assert [n for n in notices if f"{section}.summarizer" in n] != []


# --- the [app] table (run 7, T-62) -------------------------------------------


def test_app_table_defaults_apply_with_no_table_at_all(monkeypatch, tmp_path):
    from vocalize.config import APP_DEFAULTS, resolve_app

    assert resolve_app(_load_stt(monkeypatch, tmp_path, "")) == APP_DEFAULTS


def test_app_table_loads_with_valid_values_and_canonical_chords(monkeypatch, tmp_path):
    """Aliases and order are the user's; what resolves is one spelling, the
    one the menu-bar app parses from `vocalize settings`."""
    from vocalize.config import resolve_app

    data = _load_stt(
        monkeypatch, tmp_path,
        '[app]\ndictate = "Command+Control+Option+D"\nspeak = "cmd+shift+f5"\nstop = ""\n'
        'dictate_mode = "toggle"\n',
    )
    assert resolve_app(data) == {
        "dictate": "ctrl+alt+cmd+d", "dictate_mode": "toggle", "speak": "cmd+shift+f5", "stop": "",
        "stop_hotkey": "stop",
    }


@pytest.mark.parametrize("chord", [
    "ctrl+alt",  # modifier-only
    "alt+shift+d",  # neither ctrl nor cmd
    "ctrl+cmd+enter",  # not a key the grammar knows
    "ctrl+ctrl+d",  # a repeated modifier
    "ctrl + alt + d",  # spaces
    "ctrl+alt+",  # an empty token
    "hyper+d",  # an unknown modifier
])
def test_a_bad_app_chord_is_refused(monkeypatch, tmp_path, chord):
    with pytest.raises(ConfigError, match="Invalid app.dictate"):
        _load_stt(monkeypatch, tmp_path, f'[app]\ndictate = "{chord}"\n')


@pytest.mark.parametrize("body", [
    '[app]\ndictate = 3\n',
    '[app]\ndictate_mode = "sticky"\n',
    'app = "keep me"\n',
])
def test_a_bad_app_table_value_is_refused(monkeypatch, tmp_path, body):
    with pytest.raises(ConfigError):
        _load_stt(monkeypatch, tmp_path, body)


def test_duplicate_app_chords_are_refused_against_the_defaults_too(monkeypatch, tmp_path):
    """Only `speak` is set, to the default dictate chord: still a clash."""
    with pytest.raises(ConfigError, match="app.dictate and app.speak .* are the same chord"):
        _load_stt(monkeypatch, tmp_path, '[app]\nspeak = "command+option+control+d"\n')


def test_two_disabled_app_chords_do_not_count_as_a_clash(monkeypatch, tmp_path):
    from vocalize.config import resolve_app

    data = _load_stt(monkeypatch, tmp_path, '[app]\nspeak = ""\nstop = ""\n')
    assert resolve_app(data)["speak"] == "" and resolve_app(data)["stop"] == ""


def test_an_unknown_app_key_warns_but_still_loads(monkeypatch, tmp_path, capsys):
    """An unknown app key warns but still loads."""
    data = _load_stt(monkeypatch, tmp_path, '[app]\nfuture_key = "pause"\n')

    assert data["app"] == {"future_key": "pause"}
    assert "unknown config key 'future_key' in [app]" in capsys.readouterr().err


def test_stop_hotkey_rejects_an_unknown_word(monkeypatch, tmp_path):
    with pytest.raises(ConfigError, match="Invalid app.stop_hotkey 'unknown'"):
        _load_stt(monkeypatch, tmp_path, '[app]\nstop_hotkey = "unknown"\n')


def test_dictate_mode_hold_resolves_to_hold(monkeypatch, tmp_path, capsys):
    """0.13.0 coerced `hold` to toggle with a warning; 0.13.1 dispatches it.

    The settings line the menu-bar app reads is what changes: it now says
    `app.dictate_mode=hold`, which is what sends key-down and key-up to
    `vocalize dictate --start` and `--stop`.
    """
    from vocalize import config
    from vocalize.config import resolve_app

    monkeypatch.setattr(config, "_warned", set())  # once per process: start this one clean
    data = _load_stt(monkeypatch, tmp_path, '[app]\ndictate_mode = "hold"\n')

    assert resolve_app(data)["dictate_mode"] == "hold"
    assert capsys.readouterr().err == ""  # nothing left to warn about


def test_resolve_app_chord_grammar_accepts_every_key_it_lists():
    from vocalize.config import CHORD_KEYS, chord_text, parse_chord

    assert len(CHORD_KEYS) == 26 + 10 + 12
    for key in CHORD_KEYS:
        assert chord_text(parse_chord(f"ctrl+{key}")) == f"ctrl+{key}"
    assert parse_chord("") is None and chord_text(None) == ""


def test_the_chord_key_allowlist_matches_the_swift_keycode_table():
    """The app's Swift source (run 8a) carries a `keyCodes` dictionary keyed
    by the same names; the two must never drift. Until that file exists
    the comparison has nothing to read, so it skips rather than passes."""
    import re
    from pathlib import Path

    from vocalize.config import CHORD_KEYS

    swift = Path(__file__).resolve().parent.parent / "vocalize" / "menubar" / "VocalizeApp.swift"
    if not swift.is_file():
        pytest.skip("vocalize/menubar/VocalizeApp.swift arrives in run 8a")
    text = swift.read_text(encoding="utf-8")
    # `let keyCodes: [String: UInt32] = [ "a": UInt32(kVK_ANSI_A), ... ]`:
    # the literal starts after the `=`, so the type annotation's brackets
    # are skipped, and it holds no nested bracket.
    start = text.index("keyCodes")
    opening = text.index("[", text.index("=", start))
    block = text[opening:text.index("]", opening)]
    names = set(re.findall(r'"([a-z0-9]+)"\s*:', block))
    assert names == set(CHORD_KEYS), sorted(names ^ set(CHORD_KEYS))


def test_speech_table_defaults():
    from vocalize.config import SPEECH_DEFAULTS, resolve_speech

    resolved = resolve_speech({})
    assert resolved == SPEECH_DEFAULTS


def test_speech_table_overrides():
    from vocalize.config import resolve_speech

    data = {
        "speech": {
            "headings": "plain",
            "parentheticals": "drop",
            "urls": "full",
            "bracket_max_chars": 150,
            "references": False,
        }
    }
    resolved = resolve_speech(data)
    assert resolved["headings"] == "plain"
    assert resolved["parentheticals"] == "drop"
    assert resolved["urls"] == "full"
    assert resolved["bracket_max_chars"] == 150
    assert resolved["references"] is False
    assert resolved["emoji"] is True  # default kept


@pytest.mark.parametrize("key,val", [
    ("headings", "bad"),
    ("parentheticals", "bad"),
    ("urls", "bad"),
    ("emoji", "not_a_bool"),
    ("bracket_max_chars", 5),
    ("bracket_max_chars", 5000),
    ("furniture_max_chars", 5),
    ("furniture_min_repeats", 1),
])
def test_invalid_speech_setting_raises(key, val):
    from vocalize.config import resolve_speech
    from vocalize.exceptions import ConfigError

    with pytest.raises(ConfigError):
        resolve_speech({"speech": {key: val}})


def test_unknown_speech_key_warns(monkeypatch, tmp_path, capsys):
    from vocalize import config
    from vocalize.config import resolve_speech

    monkeypatch.setattr(config, "_warned", set())
    data = _load_stt(monkeypatch, tmp_path, '[speech]\nunknown_speech_option = "yes"\n')
    resolve_speech(data)
    assert "unknown config key 'unknown_speech_option' in [speech]" in capsys.readouterr().err


def test_local_default_settings(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    settings = resolve_settings()
    assert (settings.provider, settings.voice_id, settings.model_id) == ("kokoro", DEFAULT_VOICE, DEFAULT_MODEL)
    assert resolve_chain() == list(DEFAULT_CHAIN)


def test_local_provider_precedence_and_fallback_isolation(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, '[providers.kokoro]\nvoice="af_bella"\nspeed=0.9\n')
    assert resolve_provider_settings("kokoro").voice_id == "af_bella"
    monkeypatch.setenv("VOCALIZE_VOICE", "af_heart")
    assert resolve_provider_settings("kokoro").voice_id == "af_heart"
    assert resolve_provider_settings("kokoro", voice_id="af_nicole").voice_id == "af_nicole"
    assert resolve_provider_settings("kokoro", primary=False).voice_id == "af_bella"


# --- a 0.15 config loads with notices, not an error (DEC-005, T-13) ----------

CANARY = "SECRET_CANARY_0123456789"

V015_CONFIG = f"""\
voice = "21m00Tcm4TlvDq8ikWAM"
model = "eleven_multilingual_v2"
speed = 1.1
chain = ["elevenlabs", "openai", "kokoro", "google", "polly", "say"]

[providers.openai]
api_key = "{CANARY}"
voice = "alloy"

[providers.kokoro]
voice = "af_bella"

[stt]
cleanup = "claude-cli"

[notes]
folder = "~/Notes"
template = "meeting"
summarizer = "anthropic"
"""

V015_RETIRED_PATHS = (
    "voice", "model", "speed",
    "chain entry 'elevenlabs'", "chain entry 'openai'", "chain entry 'google'",
    "chain entry 'polly'",
    "[providers.openai]", "stt.cleanup", "notes.template", "notes.summarizer",
)


def _paths_in(notices):
    return [
        path for path in V015_RETIRED_PATHS
        for notice in notices if f"retired setting {path} in " in notice
    ]


def test_a_015_config_loads_with_one_notice_per_retired_key(monkeypatch, tmp_path, capsys):
    from vocalize import config
    from vocalize.config import resolve_stt, resolve_transcribe

    monkeypatch.setattr(config, "_warned", set())
    _isolate(monkeypatch, tmp_path, V015_CONFIG)
    monkeypatch.delenv("VOCALIZE_CHAIN", raising=False)

    data = load_config_file()
    assert resolve_chain(None, data) == ["kokoro", "say"]
    assert resolve_stt(data)["cleanup"] == "off"
    assert resolve_settings(file_config=data).voice_id == "af_bella"
    assert resolve_settings(file_config=data).speed is None  # flat speed ignored
    assert resolve_transcribe(data) == {"folder": "~/Notes", "keep_audio": False, "model": ""}
    # Read again, as the portal does on every poll: nothing new is printed.
    load_config_file()

    captured = capsys.readouterr()
    printed = captured.err.splitlines()
    assert sorted(_paths_in(printed)) == sorted(V015_RETIRED_PATHS)
    assert len(printed) == len(V015_RETIRED_PATHS)
    assert captured.out == ""  # stdout is the menu-bar app's
    assert CANARY not in captured.err + captured.out

    raw = _parse_toml(V015_CONFIG)
    notices = config.retired_notices(raw)
    assert sorted(_paths_in(notices)) == sorted(V015_RETIRED_PATHS)
    assert len(notices) == len(V015_RETIRED_PATHS)
    assert all(CANARY not in notice for notice in notices)
    assert raw["providers"]["openai"]["api_key"] == CANARY  # nothing was mutated


def _parse_toml(text):
    try:
        import tomllib
    except ImportError:  # Python 3.10
        import tomli as tomllib
    return tomllib.loads(text)


@pytest.mark.parametrize("body, path", [
    ('api_key = "SECRET_CANARY"', "api_key"),
    ('endpoint = "https://SECRET_CANARY.example"', "endpoint"),
    ('[providers.kokoro]\napi_key = "SECRET_CANARY"', "providers.kokoro.api_key"),
    ('[providers.polly]\nregion = "SECRET_CANARY"', "[providers.polly]"),
    ("[stt]\ncleanup = true", "stt.cleanup"),
    ('[stt]\ncleanup = "anthropic"', "stt.cleanup"),
])
def test_a_retired_key_is_ignored_with_a_notice_that_never_echoes_its_value(
    monkeypatch, tmp_path, capsys, body, path
):
    from vocalize import config

    monkeypatch.setattr(config, "_warned", set())
    _isolate(monkeypatch, tmp_path, body + "\n")

    data = load_config_file()

    err = capsys.readouterr().err
    assert f"retired setting {path} in " in err
    assert "SECRET_CANARY" not in err + repr(data)
    assert config.resolve_stt(data)["cleanup"] == "off"


def test_hosted_cleanup_never_becomes_local():
    from vocalize.config import resolve_stt

    for value in ("claude-cli", "anthropic", True):
        assert resolve_stt({"stt": {"cleanup": value}})["cleanup"] == "off"


def test_a_chain_of_only_cloud_engines_falls_back_to_the_default(monkeypatch):
    monkeypatch.delenv("VOCALIZE_CHAIN", raising=False)
    assert resolve_chain(file_config={"chain": ["openai", "polly"]}) == list(DEFAULT_CHAIN)
    assert resolve_chain(file_config={"chain": ["piper", "openai"]}) == ["piper"]


@pytest.mark.parametrize("body", [
    'chain = ["kokoro", "nonesuch"]',
    'chain = "kokoro"',
    '[stt]\ncleanup = "claude"',
    '[providers.kokoro]\nvoice = 5',
    '[providers.nonesuch]\nvoice = "x"',
    'max_chars = 0\n[providers.kokoro]\napi_key = "k"',
])
def test_a_malformed_live_value_beside_retired_keys_is_still_an_error(
    monkeypatch, tmp_path, body
):
    _isolate(monkeypatch, tmp_path, 'voice = "old"\n' + body + "\n")
    with pytest.raises(ConfigError):
        data = load_config_file()
        resolve_overflow(file_config=data)


@pytest.mark.parametrize("name", ["elevenlabs", "openai", "google", "polly", "anthropic", "claude-cli"])
def test_cloud_names_in_a_flag_or_env_chain_are_still_rejected(monkeypatch, name):
    with pytest.raises(ConfigError):
        resolve_chain(provider=name, file_config={})
    monkeypatch.setenv("VOCALIZE_CHAIN", "kokoro," + name)
    with pytest.raises(ConfigError):
        resolve_chain(file_config={})


@pytest.mark.parametrize("name", ["anthropic", "claude-cli"])
def test_a_name_that_was_never_a_chain_engine_is_still_rejected(name):
    with pytest.raises(ConfigError):
        resolve_chain(file_config={"chain": ["kokoro", name]})


@pytest.mark.parametrize("value", ["--flag", "x\nforged", "x" * 129])
def test_local_voice_env_rejects_option_control_and_oversize(monkeypatch, value):
    monkeypatch.setenv("VOCALIZE_VOICE", value)
    with pytest.raises(ConfigError):
        resolve_provider_settings("kokoro", {})


def test_ambient_cloud_credentials_are_not_loaded_or_selected(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "SECRET_CANARY")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "SECRET_CANARY")
    assert resolve_chain(file_config={}) == ["kokoro", "say"]
