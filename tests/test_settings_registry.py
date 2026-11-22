"""The settings registry's own behaviour: validate, apply_changes, schema, output."""
import json

import pytest

from vocalize import config
from vocalize import settings_registry as registry
from vocalize.exceptions import ConfigError


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for var in ("VOCALIZE_VOICE", "VOCALIZE_MODEL", "VOCALIZE_SPEED", "VOCALIZE_CHAIN",
                "VOCALIZE_OVERFLOW", "VOCALIZE_MAX_CHARS"):
        monkeypatch.delenv(var, raising=False)


# --- validate, per type ---------------------------------------------------


@pytest.mark.parametrize("key, value, expected", [
    ("stt.paste", True, True),
    ("speech.emoji", False, False),
    ("stt.max_seconds", 30, 30),
    ("max_chars", 4000, 4000),
    ("providers.kokoro.speed", 1, 1.0),
    ("providers.say.speed", 0.9, 0.9),
    ("providers.kokoro.voice", "af_bella", "af_bella"),
    ("stt.input_device", "Studio Mic", "Studio Mic"),
    ("app.speak", "cmd+ctrl+S", "ctrl+cmd+s"),  # canonical chord
    ("app.stop", "", ""),  # disabled
    ("overflow", "ASK", "ask"),
    ("stt.cleanup", False, "off"),  # 0.10.x false
    ("stt.cues", "both", "both"),
    ("transcribe.model", "", ""),
    ("chain", ["say", "kokoro"], ["say", "kokoro"]),
    ("stt.vocabulary", ["Kokoro", "repository root"], ["Kokoro", "repository root"]),
])
def test_validate_accepts_and_normalises(key, value, expected):
    result = registry.validate(key, value)
    assert result == expected
    assert type(result) is type(expected)


@pytest.mark.parametrize("key, value, words", [
    ("stt.paste", "yes", "true or false"),
    ("stt.paste", 1, "true or false"),
    ("stt.max_seconds", True, "whole number"),
    ("stt.max_seconds", "30", "whole number"),
    ("stt.max_seconds", 0, "between 1 and 600"),
    ("stt.max_seconds", 601, "between 1 and 600"),
    ("max_chars", 0, "positive"),
    ("providers.kokoro.speed", "fast", "a number"),
    ("providers.kokoro.speed", 2.0, "between 0.7 and 1.2"),
    ("providers.kokoro.voice", 5, "expected text"),
    ("providers.kokoro.voice", "-v", "not starting with '-'"),
    ("stt.input_device", "-x", "cannot start with '-'"),
    ("app.speak", "alt+s", "ctrl or cmd"),
    ("overflow", "sometimes", "truncate, ask, never"),
    ("stt.cues", "beeps", "sounds, words, both"),
    ("stt.model", "huge", "Known:"),
    ("chain", "kokoro", "expected a list"),
    ("chain", ["kokoro", "kokoro"], "Duplicate"),
    ("chain", ["elevenlabs"], "Unknown provider"),
    ("stt.vocabulary", ["ends."], "sentence"),
    ("speech.bracket_max_chars", 5, "between 10 and 1000"),
])
def test_validate_rejects_with_a_message_a_person_can_act_on(key, value, words):
    with pytest.raises(ConfigError) as caught:
        registry.validate(key, value)
    assert words in str(caught.value)
    assert key.split(".")[-1] in str(caught.value) or key in str(caught.value)


def test_validate_refuses_an_unknown_key():
    with pytest.raises(ConfigError, match="Unknown setting 'stt.nonsense'"):
        registry.validate("stt.nonsense", 1)


def test_by_key_raises_key_error_for_an_unknown_key():
    assert registry.by_key("stt.paste").type == "bool"
    with pytest.raises(KeyError):
        registry.by_key("nope")


# --- apply_changes -----------------------------------------------------------


def test_apply_changes_sets_resets_keeps_unknown_and_does_not_mutate():
    original = {
        "max_chars": 1000,
        "mystery": "kept",
        "stt": {"paste": True, "model": "base.en", "odd_key": 1},
        "providers": {"kokoro": {"voice": "af_bella"}},
    }
    snapshot = json.loads(json.dumps(original))

    result = registry.apply_changes(original, {
        "stt.paste": None,
        "max_chars": None,
        "app.speak": "cmd+ctrl+s",
        "providers.kokoro.voice": "af_sky",
        "speech.headings": "plain",
    })

    assert original == snapshot  # input untouched
    assert result == {
        "mystery": "kept",
        "stt": {"model": "base.en", "odd_key": 1},
        "providers": {"kokoro": {"voice": "af_sky"}},
        "app": {"speak": "ctrl+cmd+s"},
        "speech": {"headings": "plain"},
    }
    assert result["stt"] is not original["stt"]


def test_apply_changes_reset_removes_an_emptied_table_and_old_spellings():
    data = {
        "notes": {"folder": "~/Old"},
        "transcribe": {"folder": "~/New"},
        "speech": {"SPEECH_BRACKET_MAX_CHARS": 200},
    }
    result = registry.apply_changes(data, {
        "transcribe.folder": None,
        "speech.bracket_max_chars": None,
    })
    assert result == {}
    assert registry.values(result)["transcribe.folder"] == config.TRANSCRIBE_DEFAULTS["folder"]
    assert registry.values(result)["speech.bracket_max_chars"] == 120


def test_apply_changes_checks_the_whole_table():
    with pytest.raises(ConfigError, match="same chord"):
        registry.apply_changes({}, {"app.speak": "ctrl+alt+cmd+d"})
    with pytest.raises(ConfigError, match="English-only"):
        registry.apply_changes({"stt": {"model": "small.en"}}, {"stt.language": "fr"})
    with pytest.raises(ConfigError, match="Unknown setting"):
        registry.apply_changes({}, {"stt.bogus": None})


def test_apply_changes_refuses_a_bad_value_and_leaves_the_input_alone():
    data = {"stt": {"max_seconds": 30}}
    with pytest.raises(ConfigError):
        registry.apply_changes(data, {"stt.max_seconds": 0})
    assert data == {"stt": {"max_seconds": 30}}


# --- values and defaults ------------------------------------------------------


def test_defaults_and_values_cover_every_key():
    keys = {s.key for s in registry.SETTINGS}
    assert set(registry.defaults()) == keys
    assert registry.values({}) == registry.defaults()
    resolved = registry.values({"stt": {"cleanup": True}, "app": {"speak": "cmd+ctrl+s"}})
    assert resolved["stt.cleanup"] == "off"
    assert resolved["app.speak"] == "ctrl+cmd+s"


# --- schema ------------------------------------------------------------------


def test_schema_is_json_and_complete():
    schema = registry.schema()
    decoded = json.loads(json.dumps(schema))
    assert set(decoded) == {"groups", "settings"}
    assert [g["id"] for g in decoded["groups"]] == [
        "speaking", "reading", "dictation", "transcription", "hotkeys", "engines", "advanced",
    ]
    assert {tuple(sorted(g)) for g in decoded["groups"]} == {("help", "id", "label", "order")}
    fields = {
        "key", "type", "default", "label", "help", "group", "control", "choices",
        "choices_from", "minimum", "maximum", "step", "max_length", "max_items",
        "applies_when", "restart", "printed", "print_name",
    }
    assert [s["key"] for s in decoded["settings"]] == [s.key for s in registry.SETTINGS]
    for entry in decoded["settings"]:
        assert set(entry) == fields, entry["key"]
    chain = next(s for s in decoded["settings"] if s["key"] == "chain")
    assert chain["choices"] == ["kokoro", "piper", "say"]
    assert chain["default"] == ["kokoro", "say"]


# --- output ------------------------------------------------------------------


def test_settings_lines_keep_the_parsed_lines_first_and_print_every_printed_key():
    lines = registry.settings_lines({})
    assert lines[:20] == [
        "voice=af_heart", "speed=unset", "max_chars=unset", "overflow=truncate",
        "chain=kokoro,say", "stt.model=large-v3-turbo-q5_0", "stt.language=en",
        "stt.beam_size=5", "stt.cleanup=off", "stt.verbatim=false", "stt.max_seconds=120",
        "stt.max_take_seconds=1800", "stt.cues=sounds", "stt.paste=false",
        "notes.summarizer=off", "app.dictate=ctrl+alt+cmd+d", "app.dictate_mode=toggle",
        "app.speak=ctrl+alt+cmd+s", "app.stop=ctrl+alt+cmd+x", "app.stop_hotkey=stop",
    ]
    names = [line.split("=", 1)[0] for line in lines]
    assert len(names) == len(set(names))
    assert "model" not in names  # no engine read it (review M2)
    for setting in registry.SETTINGS:
        assert (setting.print_name or setting.key) in names, setting.key
    assert "speech.headings=cue" in lines
    assert "transcribe.keep_audio=false" in lines
    assert "stt.vocabulary=" in lines


def test_settings_lines_apply_the_environment_where_they_always_did(monkeypatch):
    monkeypatch.setenv("VOCALIZE_MAX_CHARS", "500")
    monkeypatch.setenv("VOCALIZE_CHAIN", "say")
    lines = registry.settings_lines({"max_chars": 1000})
    assert "max_chars=500" in lines
    assert "chain=say" in lines


def test_readme_table_lists_every_key_under_its_group():
    table = registry.readme_table()
    for group in registry.GROUPS:
        assert f"### {group.label}" in table
    for setting in registry.SETTINGS:
        assert f"| `{setting.key}` |" in table
    assert "| Key | Default | Allowed | What it does |" in table


# --- help that is true for both dictation engines (W5-B) -------------------


def test_vocabulary_help_says_it_applies_after_either_engine():
    text = registry.by_key("stt.vocabulary").help
    assert "Whisper engine should expect" not in text
    assert "either engine" in text and "Whisper" in text and "hint" in text


def test_warm_minutes_help_is_true_with_parakeet_too():
    text = registry.by_key("stt.warm_minutes").help
    assert "dictation model stays loaded" not in text
    assert "Parakeet" in text and "cleanup model" in text


@pytest.mark.parametrize("key", ["providers.kokoro.model", "providers.piper.model",
                                 "providers.say.model", "providers.piper.language",
                                 "providers.say.language"])
def test_a_setting_no_engine_reads_is_gone_and_retired(key):
    with pytest.raises(KeyError):
        registry.by_key(key)
    assert key in {r.key for r in registry.RETIRED}
    with pytest.raises(ConfigError):
        registry.apply_changes({}, {key: "x"})


def test_the_kokoro_language_is_still_registered():
    assert registry.by_key("providers.kokoro.language").default == "en-us"
