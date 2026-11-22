"""The registry and the rest of vocalize cannot drift apart.

(a) the registry's keys are exactly the keys the loader accepts, table by
    table, with the same defaults, and the loader itself accepts every
    registered choice and bound and refuses the value just outside;
(b) every record is complete enough for the settings page to render, with
    a control kind the page's renderer map can draw; every machine profile
    sets only registered keys to valid values; the README's settings table
    is the generated one;
(c) no module reads a key from a resolved config dict that is not
    registered (a static scan; its reach is described on the test);
(d) every key set to a non-default value survives a save and a load.
"""
import re
from pathlib import Path

import pytest

from vocalize import auth, config, profiles, providers, wizard
from vocalize import settings_registry as registry
from vocalize.exceptions import ConfigError


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for var in ("VOCALIZE_VOICE", "VOCALIZE_MODEL", "VOCALIZE_SPEED", "VOCALIZE_CHAIN",
                "VOCALIZE_OVERFLOW", "VOCALIZE_MAX_CHARS"):
        monkeypatch.delenv(var, raising=False)


def _subkeys(table: str) -> set[str]:
    return {s.key[len(table) + 1:] for s in registry.SETTINGS if s.key.startswith(table + ".")}


def _write(tmp_path, text: str) -> None:
    path = config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _toml_line(key: str, value) -> str:
    """`key` set to `value` as a config file, through the real writer."""
    data: dict = {}
    node = data
    parts = key.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value
    return wizard._render_config_text(data)


# --- (a) the same keys, defaults and rules as the loader -------------------------


def test_root_keys_and_tables_match_the_loader():
    loader_tables = {"providers", "stt", "transcribe", "notes", "app", "speech"}
    assert set(config.KNOWN_CONFIG_KEYS) - loader_tables == set(registry.ROOT_KEYS)
    assert set(config.KNOWN_CONFIG_KEYS) == (
        set(registry.ROOT_KEYS) | set(registry.TABLES) | set(registry.TABLE_ALIASES)
    )
    assert set(wizard._TABLE_KEYS) == set(registry.TABLES) | set(registry.TABLE_ALIASES)


def test_every_table_has_the_loaders_keys():
    for name in auth.PROVIDER_NAMES:
        assert _subkeys(f"providers.{name}") == set(config.KNOWN_PROVIDER_KEYS[name])
    assert {k for k in _subkeys("providers") if "." not in k} == set()
    assert _subkeys("stt") == set(config.KNOWN_STT_KEYS)
    assert _subkeys("transcribe") == set(config.KNOWN_TRANSCRIBE_KEYS)
    assert _subkeys("app") == set(config.KNOWN_APP_KEYS)
    speech_aliases = {key.split(".", 1)[1] for key in registry.KEY_ALIASES}
    assert _subkeys("speech") | speech_aliases == set(config.KNOWN_SPEECH_KEYS)
    assert all(target in {s.key for s in registry.SETTINGS}
               for target in registry.KEY_ALIASES.values())


def test_defaults_match_the_loader_and_the_engines():
    defaults = registry.defaults()
    for table, loader in (("stt", config.STT_DEFAULTS), ("transcribe", config.TRANSCRIBE_DEFAULTS),
                          ("app", config.APP_DEFAULTS), ("speech", config.SPEECH_DEFAULTS)):
        assert {k: defaults[f"{table}.{k}"] for k in loader} == loader, table
    assert defaults["chain"] == list(config.DEFAULT_CHAIN)
    assert (defaults["overflow"], defaults["max_chars"]) == config.resolve_overflow(file_config={})
    for name in auth.PROVIDER_NAMES:
        engine = config.resolve_provider_settings(name, {}, primary=False)
        assert defaults[f"providers.{name}.voice"] == providers.get(name).DEFAULTS.get("voice")
        assert defaults[f"providers.{name}.voice"] == engine.voice_id
        if "language" in config.KNOWN_PROVIDER_KEYS[name]:
            assert defaults[f"providers.{name}.language"] == engine.language
        assert defaults[f"providers.{name}.speed"] == engine.speed
    # The registry's values for an empty file are what the loader resolves.
    assert registry.values({}) == defaults


def _loader_accepts(tmp_path, key, value) -> bool:
    _write(tmp_path, _toml_line(key, value))
    try:
        data = config.load_config_file()
        registry.values(data)  # the resolvers check the keys the loader leaves to them
    except ConfigError:
        return False
    return True


@pytest.mark.parametrize("setting", [s for s in registry.SETTINGS if s.choices is not None
                                     and s.type == "enum"], ids=lambda s: s.key)
def test_the_loader_takes_every_registered_choice_and_no_other(tmp_path, setting):
    for choice in setting.choices:
        if setting.key == "stt.language" and choice != "en":
            continue  # fine for the default model; English-only pairing is checked below
        assert _loader_accepts(tmp_path, setting.key, choice), choice
        assert registry.validate(setting.key, choice) == choice
    assert not _loader_accepts(tmp_path, setting.key, "not-a-registered-choice")


@pytest.mark.parametrize("setting", [s for s in registry.SETTINGS if s.type == "int"
                                     and s.minimum is not None], ids=lambda s: s.key)
def test_the_loader_enforces_the_registered_bounds(tmp_path, setting):
    assert _loader_accepts(tmp_path, setting.key, int(setting.minimum))
    assert not _loader_accepts(tmp_path, setting.key, int(setting.minimum) - 1)
    if setting.maximum is not None:
        assert _loader_accepts(tmp_path, setting.key, int(setting.maximum))
        assert not _loader_accepts(tmp_path, setting.key, int(setting.maximum) + 1)
        with pytest.raises(ConfigError):
            registry.validate(setting.key, int(setting.maximum) + 1)
    with pytest.raises(ConfigError):
        registry.validate(setting.key, int(setting.minimum) - 1)


def test_the_speed_and_length_bounds_are_the_loaders():
    for name in auth.PROVIDER_NAMES:
        speed = registry.by_key(f"providers.{name}.speed")
        assert (speed.minimum, speed.maximum) == (config.SPEED_MIN, config.SPEED_MAX)
        for key in set(config.PROVIDER_TEXT_KEYS) & set(config.KNOWN_PROVIDER_KEYS[name]):
            assert registry.by_key(f"providers.{name}.{key}").max_length == (
                config.PROVIDER_TEXT_MAX_CHARS)
    with pytest.raises(ConfigError):
        registry.validate("providers.kokoro.voice", "x" * (config.PROVIDER_TEXT_MAX_CHARS + 1))
    with pytest.raises(ConfigError):
        registry.validate("stt.input_device", "x" * (config.STT_DEVICE_MAX_CHARS + 1))
    vocabulary = registry.by_key("stt.vocabulary")
    with pytest.raises(ConfigError):
        registry.validate("stt.vocabulary", ["term"] * ((vocabulary.max_items or 0) + 1))


def test_retired_keys_are_the_loaders():
    retired = {r.key for r in registry.RETIRED}
    assert set(config._RETIRED_ROOT_KEYS) <= retired
    for name, keys in config._RETIRED_ENGINE_KEYS.items():
        assert {f"providers.{name}.{key}" for key in keys} <= retired
    assert {"providers.kokoro.model", "providers.piper.model", "providers.say.model",
            "providers.piper.language", "providers.say.language"} <= retired
    assert not retired & {s.key for s in registry.SETTINGS}
    for entry in registry.RETIRED:
        assert entry.advice


# --- (b) every record can be rendered -----------------------------------------


def test_every_record_is_complete():
    group_ids = [g.id for g in sorted(registry.GROUPS, key=lambda g: g.order)]
    assert group_ids == ["speaking", "reading", "dictation", "transcription", "hotkeys",
                         "engines", "advanced"]
    keys = [s.key for s in registry.SETTINGS]
    assert len(keys) == len(set(keys))
    for s in registry.SETTINGS:
        assert s.label.strip() and s.help.strip(), s.key
        assert s.group in group_ids, s.key
        assert s.type in registry.TYPES, s.key
        assert s.control in registry.CONTROLS_FOR_TYPE[s.type], s.key
        assert s.choices_from is None or s.choices_from in registry.CHOICE_SOURCES, s.key
        assert s.restart in registry.RESTARTS, s.key
        if s.type == "enum":
            assert s.choices, s.key
            assert s.default in s.choices, s.key
        if s.applies_when is not None:
            other, value = s.applies_when
            assert registry.validate(other, value) == value, s.key
    for group in group_ids:
        assert any(s.group == group for s in registry.SETTINGS), group


_PAGE_SCRIPT = Path(registry.__file__).parent / "assets" / "portal.js"


def _page_renderers() -> set[str]:
    """The control kinds the settings page's `RENDERERS` map can draw."""
    script = _PAGE_SCRIPT.read_text(encoding="utf-8")
    start = script.index("var RENDERERS = {")
    end = script.index("\n};", start)
    return set(re.findall(r"^  ([a-z_]+): function", script[start:end], re.MULTILINE))


def test_every_setting_has_a_control_the_page_can_render():
    renderers = _page_renderers()
    assert renderers, "the page's RENDERERS map was not found"
    missing = sorted(f"{s.key}: {s.control}" for s in registry.SETTINGS
                     if s.control not in registry.CONTROLS or s.control not in renderers)
    assert missing == []


@pytest.mark.parametrize("name", profiles.names())
def test_every_profile_sets_only_registered_keys_with_valid_values(name):
    registered = {s.key for s in registry.SETTINGS}
    changes = profiles.changes(name)
    assert set(changes) <= registered, sorted(set(changes) - registered)
    for key, value in changes.items():
        assert registry.validate(key, value) == value, key


# --- the README's settings table is the generated one --------------------------

_README = Path(__file__).resolve().parents[1] / "README.md"
_TABLE_START = "<!-- settings-table:start -->"
_TABLE_END = "<!-- settings-table:end -->"


def test_the_readme_settings_table_is_the_generated_one():
    text = _README.read_text(encoding="utf-8")
    assert text.count(_TABLE_START) == 1 and text.count(_TABLE_END) == 1
    between = text.split(_TABLE_START, 1)[1].split(_TABLE_END, 1)[0]
    assert between.strip("\n") == registry.readme_table().strip("\n"), (
        "README.md's settings table is out of date: put the output of "
        "vocalize.settings_registry.readme_table() between the two marker lines"
    )


# --- (c) no unregistered key is read ----------------------------------------
#
# Reach: every .py file under vocalize/ except config.py (which defines the
# tables) and this registry. It finds `NAME["key"]` and `NAME.get("key"` on
# the names the code uses for a resolved table (`stt`, `stt_cfg`,
# `settings` for [stt]; `speech` for [speech]; `app`, `app_conf` for [app];
# `notes_cfg` for [transcribe]) and `resolve_<table>(...)["key"]`. A key read
# through another name, or built at run time, is not seen.

_TABLE_NAMES = {
    "stt": "stt", "stt_cfg": "stt", "stt_config": "stt", "settings": "stt",
    "speech": "speech", "app": "app", "app_conf": "app",
    "notes_cfg": "transcribe", "transcribe_cfg": "transcribe",
}
_RESOLVERS = {"resolve_stt": "stt", "resolve_speech": "speech", "resolve_app": "app",
              "resolve_notes": "transcribe", "resolve_transcribe": "transcribe"}
_READ = re.compile(r"\b(\w+)(?:\[\"(\w+)\"\]|\.get\(\"(\w+)\")")
_RESOLVED_READ = re.compile(r"\b(resolve_\w+)\([^()]*\)\[\"(\w+)\"\]")


def _scan() -> list[tuple[str, str, int]]:
    root = Path(registry.__file__).parent
    found = []
    for path in sorted(root.rglob("*.py")):
        if path.name in ("config.py", "settings_registry.py"):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in _READ.finditer(line):
                table = _TABLE_NAMES.get(match.group(1))
                if table:
                    found.append((f"{table}.{match.group(2) or match.group(3)}",
                                  f"{path.relative_to(root)}", number))
            for match in _RESOLVED_READ.finditer(line):
                table = _RESOLVERS.get(match.group(1))
                if table:
                    found.append((f"{table}.{match.group(2)}",
                                  f"{path.relative_to(root)}", number))
    return found


def test_no_module_reads_an_unregistered_key():
    found = _scan()
    registered = {s.key for s in registry.SETTINGS}
    # `settings` is also a name for other dicts; only [stt]-shaped reads count.
    not_stt = {"stt.voice_id", "stt.model_id", "stt.speed", "stt.provider"}
    unregistered = sorted({(key, f"{where}:{line}") for key, where, line in found
                           if key not in registered and key not in not_stt})
    assert unregistered == []
    assert len({key for key, _, _ in found}) >= 20  # the scan is really finding reads


# --- (d) every key survives a save and a load ----------------------------------

_NON_DEFAULT = {
    "chain": ["piper", "kokoro", "say"],
    "max_chars": 4000,
    "overflow": "never",
    "providers.kokoro.voice": "af_bella", "providers.kokoro.speed": 0.9,
    "providers.kokoro.language": "en-gb",
    "providers.piper.voice": "amy", "providers.piper.speed": 1.1,
    "providers.say.voice": "Samantha", "providers.say.speed": 1.0,
    "speech.headings": "plain", "speech.blockquotes": "off", "speech.nested_lists": "flat",
    "speech.parentheticals": "drop", "speech.abbreviations": "protect",
    "speech.citations": "speak", "speech.references": False, "speech.furniture": False,
    "speech.urls": "full", "speech.emoji": False, "speech.bracket_max_chars": 200,
    "speech.furniture_max_chars": 80, "speech.furniture_min_repeats": 4,
    "stt.paste": True, "stt.cues": "both", "stt.sounds": False, "stt.verbatim": True,
    "stt.cleanup": "local", "stt.vocabulary": ["Kokoro", "repository root"],
    "stt.max_seconds": 30, "stt.max_take_seconds": 600, "stt.input_device": "Studio Mic",
    "transcribe.folder": "~/Transcripts", "transcribe.keep_audio": True,
    "transcribe.model": "base.en",
    "app.dictate": "ctrl+cmd+d", "app.dictate_mode": "hold", "app.speak": "ctrl+cmd+s",
    "app.stop": "", "app.stop_hotkey": "pause",
    "stt.engine": "whisper", "stt.model": "large-v3-turbo-q8_0", "stt.language": "fr",
    "stt.warm_minutes": 15, "stt.beam_size": 3,
}


def test_every_key_set_to_a_non_default_survives_write_then_load():
    assert set(_NON_DEFAULT) == {s.key for s in registry.SETTINGS}
    defaults = registry.defaults()
    assert all(_NON_DEFAULT[key] != defaults[key] for key in _NON_DEFAULT)

    data = registry.apply_changes({"mystery": "kept"}, _NON_DEFAULT)
    path = config.config_path()
    wizard.write_config_if_unchanged(path, data, wizard.ABSENT_CONFIG)
    loaded = config.load_config_file()

    assert registry.values(loaded) == _NON_DEFAULT
    assert loaded["mystery"] == "kept"
