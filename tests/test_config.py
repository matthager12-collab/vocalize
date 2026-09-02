import os
import types

import pytest

from vocalize.config import (
    DEFAULT_CHAIN,
    DEFAULT_MODEL,
    DEFAULT_VOICE,
    _load_dotenv_if_present,
    budget_for,
    chain_source,
    config_path,
    provider_table,
    resolve_api_key,
    resolve_chain,
    resolve_overflow,
    resolve_provider_settings,
    resolve_settings,
)
from vocalize.exceptions import ConfigError, MissingAPIKeyError

# Bound at import time on purpose: conftest's autouse fixture replaces the
# module attribute, so this reference is the only way to reach the real one.
real_load_dotenv = _load_dotenv_if_present


def test_explicit_key_wins_over_everything(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env-key")
    assert resolve_api_key("explicit-key") == "explicit-key"


def test_falls_back_to_env_var(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env-key")
    assert resolve_api_key(None) == "env-key"


def test_raises_clear_error_when_nothing_found(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(MissingAPIKeyError):
        resolve_api_key(None)


def test_falls_back_to_the_keychain(monkeypatch, fake_keychain):
    from vocalize import auth

    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    fake_keychain[(auth.SERVICE, auth.USERNAME)] = "keychain-key"

    assert resolve_api_key(None) == "keychain-key"

    # The keychain is the last tier: anything more local still wins.
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env-key")
    assert resolve_api_key(None) == "env-key"


def test_dotenv_loader_reads_the_env_file_in_the_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("ELEVENLABS_API_KEY=from-cwd-file\n", encoding="utf-8")
    # setenv before delenv so monkeypatch restores the var whether or not it
    # was set beforehand — the real loader writes straight to os.environ.
    monkeypatch.setenv("ELEVENLABS_API_KEY", "placeholder")
    monkeypatch.delenv("ELEVENLABS_API_KEY")

    real_load_dotenv()

    assert os.environ["ELEVENLABS_API_KEY"] == "from-cwd-file"


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


def test_config_path_honours_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config_path() == tmp_path / "vocalize" / "config.toml"


def test_config_path_falls_back_to_dot_config(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))
    assert config_path() == tmp_path / ".config" / "vocalize" / "config.toml"


def test_missing_config_file_gives_the_built_in_defaults(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    settings = resolve_settings()

    assert settings.voice_id == DEFAULT_VOICE
    assert settings.model_id == DEFAULT_MODEL
    assert settings.speed is None


def test_config_file_beats_the_default(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, 'voice = "file-voice"\nmodel = "file-model"\nspeed = 0.9\n')

    settings = resolve_settings()

    assert settings.voice_id == "file-voice"
    assert settings.model_id == "file-model"
    assert settings.speed == 0.9


def test_env_var_beats_the_config_file(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, 'voice = "file-voice"\nmodel = "file-model"\nspeed = 0.9\n')
    monkeypatch.setenv("VOCALIZE_VOICE", "env-voice")
    monkeypatch.setenv("VOCALIZE_MODEL", "env-model")
    monkeypatch.setenv("VOCALIZE_SPEED", "1.1")

    settings = resolve_settings()

    assert settings.voice_id == "env-voice"
    assert settings.model_id == "env-model"
    assert settings.speed == 1.1


def test_flag_beats_env_var_and_config_file(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, 'voice = "file-voice"\nmodel = "file-model"\nspeed = 0.9\n')
    monkeypatch.setenv("VOCALIZE_VOICE", "env-voice")
    monkeypatch.setenv("VOCALIZE_MODEL", "env-model")
    monkeypatch.setenv("VOCALIZE_SPEED", "1.1")

    settings = resolve_settings(voice_id="flag-voice", model_id="flag-model", speed=0.8)

    assert settings.voice_id == "flag-voice"
    assert settings.model_id == "flag-model"
    assert settings.speed == 0.8


def test_unknown_config_key_warns_but_still_loads_the_known_ones(monkeypatch, tmp_path, capsys):
    _isolate(monkeypatch, tmp_path, 'voice = "file-voice"\nvoise = "typo"\n')

    settings = resolve_settings()

    assert settings.voice_id == "file-voice"
    captured = capsys.readouterr()
    assert "vocalize: unknown config key 'voise'" in captured.err
    assert captured.err.count("unknown config key") == 1


def test_malformed_config_file_gives_a_clean_error(monkeypatch, tmp_path):
    path = _isolate(monkeypatch, tmp_path, "voice = \n")

    with pytest.raises(ConfigError) as excinfo:
        resolve_settings()

    assert str(path) in str(excinfo.value)


@pytest.mark.parametrize("value", ["fast", "0.2", "5"])
def test_invalid_speed_from_the_env_var_is_rejected(monkeypatch, tmp_path, value):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("VOCALIZE_SPEED", value)

    with pytest.raises(ConfigError) as excinfo:
        resolve_settings()

    assert "VOCALIZE_SPEED" in str(excinfo.value)


@pytest.mark.parametrize("literal", ['"fast"', "0.2", "5"])
def test_invalid_speed_from_the_config_file_is_rejected(monkeypatch, tmp_path, literal):
    path = _isolate(monkeypatch, tmp_path, f"speed = {literal}\n")

    with pytest.raises(ConfigError) as excinfo:
        resolve_settings()

    assert "'speed'" in str(excinfo.value)
    assert str(path) in str(excinfo.value)


@pytest.mark.parametrize("value", ["fast", 0.2, 5])
def test_invalid_speed_from_the_flag_is_rejected(monkeypatch, tmp_path, value):
    _isolate(monkeypatch, tmp_path)

    with pytest.raises(ConfigError) as excinfo:
        resolve_settings(speed=value)

    assert "--speed" in str(excinfo.value)


# --- overflow mode and cap resolution ---------------------------------------


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


# --- chain resolution --------------------------------------------------------


def _isolate_chain(monkeypatch, tmp_path, body=None):
    """Same as _isolate, for VOCALIZE_CHAIN."""
    path = _isolate(monkeypatch, tmp_path, body)
    monkeypatch.delenv("VOCALIZE_CHAIN", raising=False)
    return path


def _stub_providers(monkeypatch, **stubs):
    """Make providers.get(name) return a stub with the given DEFAULTS.

    Any name not in `stubs` falls through to the real registry, so
    'elevenlabs' and 'say' keep working unchanged. google.py/openai.py/
    polly.py are being written by other agents in parallel and may not
    exist yet — tests that need them supply DEFAULTS here instead.
    """
    import vocalize.providers as providers_pkg

    real_get = providers_pkg.get

    def fake_get(name):
        if name in stubs:
            return types.SimpleNamespace(DEFAULTS=stubs[name])
        return real_get(name)

    monkeypatch.setattr("vocalize.providers.get", fake_get)


def test_chain_defaults_to_elevenlabs_then_say(monkeypatch, tmp_path):
    _isolate_chain(monkeypatch, tmp_path)
    assert resolve_chain() == list(DEFAULT_CHAIN) == ["elevenlabs", "say"]


def test_chain_file_beats_default(monkeypatch, tmp_path):
    _isolate_chain(monkeypatch, tmp_path, 'chain = ["google", "polly", "say"]\n')
    assert resolve_chain() == ["google", "polly", "say"]


def test_chain_env_beats_file(monkeypatch, tmp_path):
    _isolate_chain(monkeypatch, tmp_path, 'chain = ["google", "say"]\n')
    monkeypatch.setenv("VOCALIZE_CHAIN", "openai,say")
    assert resolve_chain() == ["openai", "say"]


def test_chain_flag_beats_env_and_file(monkeypatch, tmp_path):
    _isolate_chain(monkeypatch, tmp_path, 'chain = ["google", "say"]\n')
    monkeypatch.setenv("VOCALIZE_CHAIN", "openai,say")
    assert resolve_chain(provider="say") == ["say"]


def test_chain_env_var_parses_with_spaces_and_drops_duplicates(monkeypatch, tmp_path):
    _isolate_chain(monkeypatch, tmp_path)
    monkeypatch.setenv("VOCALIZE_CHAIN", " google ,  say , google ")
    assert resolve_chain() == ["google", "say"]


def test_chain_flag_with_unknown_provider_raises_naming_source(monkeypatch, tmp_path):
    _isolate_chain(monkeypatch, tmp_path)
    with pytest.raises(ConfigError) as excinfo:
        resolve_chain(provider="bogus")
    assert "--provider" in str(excinfo.value)
    assert "bogus" in str(excinfo.value)


def test_chain_env_with_unknown_provider_raises_naming_source(monkeypatch, tmp_path):
    _isolate_chain(monkeypatch, tmp_path)
    monkeypatch.setenv("VOCALIZE_CHAIN", "bogus,say")
    with pytest.raises(ConfigError) as excinfo:
        resolve_chain()
    assert "VOCALIZE_CHAIN" in str(excinfo.value)
    assert "bogus" in str(excinfo.value)


def test_chain_file_with_unknown_provider_raises_naming_source(monkeypatch, tmp_path):
    path = _isolate_chain(monkeypatch, tmp_path, 'chain = ["bogus", "say"]\n')
    with pytest.raises(ConfigError) as excinfo:
        resolve_chain()
    assert "chain" in str(excinfo.value)
    assert str(path) in str(excinfo.value)
    assert "bogus" in str(excinfo.value)


@pytest.mark.parametrize(
    "body",
    [
        'chain = "elevenlabs"\n',  # not a list
        "chain = [1, 2]\n",  # not strings
        'chain = ["elevenlabs", ""]\n',  # empty string entry
    ],
)
def test_chain_bad_shape_in_file_raises(monkeypatch, tmp_path, body):
    _isolate_chain(monkeypatch, tmp_path, body)
    with pytest.raises(ConfigError) as excinfo:
        resolve_chain()
    assert "chain" in str(excinfo.value)


def test_chain_duplicates_in_file_raises(monkeypatch, tmp_path):
    path = _isolate_chain(monkeypatch, tmp_path, 'chain = ["say", "elevenlabs", "say"]\n')
    with pytest.raises(ConfigError) as excinfo:
        resolve_chain()
    assert "Duplicate" in str(excinfo.value)
    assert str(path) in str(excinfo.value)


def test_chain_env_empty_after_parsing_raises(monkeypatch, tmp_path):
    _isolate_chain(monkeypatch, tmp_path)
    monkeypatch.setenv("VOCALIZE_CHAIN", " , , ")
    with pytest.raises(ConfigError):
        resolve_chain()


def test_chain_empty_list_in_file_raises(monkeypatch, tmp_path):
    path = _isolate_chain(monkeypatch, tmp_path, "chain = []\n")
    with pytest.raises(ConfigError) as excinfo:
        resolve_chain()
    assert "chain" in str(excinfo.value)
    assert "at least one provider" in str(excinfo.value)
    assert str(path) not in str(excinfo.value)  # message names the key, not a path


@pytest.mark.parametrize("value", ["", "   "])
def test_chain_env_empty_or_blank_is_treated_as_unset(monkeypatch, tmp_path, value):
    # Falls through to the file when the file has a chain...
    _isolate_chain(monkeypatch, tmp_path / "with-file", 'chain = ["google", "say"]\n')
    monkeypatch.setenv("VOCALIZE_CHAIN", value)
    assert resolve_chain() == ["google", "say"]
    assert chain_source() == "config file"

    # ...and to the built-in default when it doesn't.
    _isolate_chain(monkeypatch, tmp_path / "without-file")
    monkeypatch.setenv("VOCALIZE_CHAIN", value)
    assert resolve_chain() == list(DEFAULT_CHAIN)
    assert chain_source() == "default"


def test_chain_source_reports_each_tier(monkeypatch, tmp_path):
    _isolate_chain(monkeypatch, tmp_path)
    assert chain_source() == "default"

    _isolate_chain(monkeypatch, tmp_path, 'chain = ["google", "say"]\n')
    assert chain_source() == "config file"

    monkeypatch.setenv("VOCALIZE_CHAIN", "openai,say")
    assert chain_source() == "environment"

    assert chain_source(provider="say") == "flag"


# --- [providers.*] tables ----------------------------------------------------


def test_providers_key_must_be_a_table(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, 'providers = "nope"\n')
    with pytest.raises(ConfigError) as excinfo:
        resolve_chain()
    assert "providers" in str(excinfo.value)


def test_providers_value_must_be_a_table_of_tables(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, 'providers = { google = "nope" }\n')
    with pytest.raises(ConfigError) as excinfo:
        resolve_chain()
    assert "providers" in str(excinfo.value)


@pytest.mark.parametrize(
    "literal", ["-5", "1.5", "true", '"1000"']
)
def test_invalid_monthly_chars_raises_naming_the_provider(monkeypatch, tmp_path, literal):
    path = _isolate(monkeypatch, tmp_path, f"[providers.google]\nmonthly_chars = {literal}\n")
    with pytest.raises(ConfigError) as excinfo:
        resolve_chain()
    assert "monthly_chars" in str(excinfo.value)
    assert "google" in str(excinfo.value)
    assert str(path) in str(excinfo.value)


def test_monthly_chars_zero_means_unlimited_budget(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, "[providers.google]\nmonthly_chars = 0\n")
    assert budget_for("google") is None


def test_monthly_chars_positive_is_the_budget(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, "[providers.google]\nmonthly_chars = 1000000\n")
    assert budget_for("google") == 1000000


def test_unknown_provider_name_under_providers_warns_but_still_loads(monkeypatch, tmp_path, capsys):
    _isolate(monkeypatch, tmp_path, '[providers.notaprovider]\nvoice = "x"\n')

    chain = resolve_chain()

    assert chain == list(DEFAULT_CHAIN)
    captured = capsys.readouterr()
    assert "unknown provider 'notaprovider'" in captured.err
    assert "elevenlabs" in captured.err  # names the valid set


def test_unknown_key_inside_provider_table_warns_but_still_loads(monkeypatch, tmp_path, capsys):
    _isolate(monkeypatch, tmp_path, '[providers.google]\nbogus = "x"\n')

    table = provider_table("google")

    assert table == {"bogus": "x"}
    captured = capsys.readouterr()
    assert "unknown config key 'bogus'" in captured.err
    assert "[providers.google]" in captured.err


def test_provider_table_speed_is_known_and_applied(monkeypatch, tmp_path, capsys):
    _isolate(monkeypatch, tmp_path, "[providers.elevenlabs]\nspeed = 0.9\n")

    settings = resolve_provider_settings("elevenlabs")

    assert settings.speed == 0.9
    captured = capsys.readouterr()
    assert "unknown config key" not in captured.err


# --- resolve_provider_settings -----------------------------------------------


def test_resolve_provider_settings_elevenlabs_legacy_flat_keys_beat_defaults(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, 'voice = "file-voice"\nmodel = "file-model"\n')

    settings = resolve_provider_settings("elevenlabs")

    assert settings.voice_id == "file-voice"
    assert settings.model_id == "file-model"
    assert settings.provider == "elevenlabs"


def test_resolve_provider_settings_google_table_voice_and_language(monkeypatch, tmp_path):
    _stub_providers(monkeypatch, google={"voice": "default-voice", "language": "default-lang"})
    _isolate(
        monkeypatch,
        tmp_path,
        '[providers.google]\nvoice = "en-US-Neural2-F"\nlanguage = "en-US"\n',
    )

    settings = resolve_provider_settings("google")

    assert settings.voice_id == "en-US-Neural2-F"
    assert settings.language == "en-US"
    assert settings.provider == "google"


def test_resolve_provider_settings_polly_engine_maps_to_model_id(monkeypatch, tmp_path):
    _stub_providers(monkeypatch, polly={"voice": "Joanna"})
    _isolate(
        monkeypatch,
        tmp_path,
        '[providers.polly]\nvoice = "Matthew"\nengine = "neural"\n'
        'region = "us-east-1"\nprofile = "default"\n',
    )

    settings = resolve_provider_settings("polly")

    assert settings.voice_id == "Matthew"
    assert settings.model_id == "neural"
    assert settings.region == "us-east-1"
    assert settings.profile == "default"


def test_resolve_provider_settings_flags_and_env_apply_only_when_primary(monkeypatch, tmp_path):
    _stub_providers(monkeypatch, google={"voice": "default-voice"})
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("VOCALIZE_VOICE", "env-voice")

    settings = resolve_provider_settings(
        "google", voice_id="flag-voice", primary=False
    )

    assert settings.voice_id == "default-voice"

    # Sanity check the flip side: as the primary, the same flag does win.
    primary_settings = resolve_provider_settings(
        "google", voice_id="flag-voice", primary=True
    )
    assert primary_settings.voice_id == "flag-voice"


def test_resolve_provider_settings_invalid_vocalize_speed_still_rejected_with_source(
    monkeypatch, tmp_path
):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("VOCALIZE_SPEED", "way too fast")

    with pytest.raises(ConfigError) as excinfo:
        resolve_provider_settings("elevenlabs", primary=True)

    assert "VOCALIZE_SPEED" in str(excinfo.value)


def test_resolve_provider_settings_sets_the_provider_field(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    settings = resolve_provider_settings("say")
    assert settings.provider == "say"


# --- the [stt] table (T-42, DEC-006) ----------------------------------
#
# Every value here ends up as one argv entry in a subprocess — the
# recorder's --device, the whisper worker's --model and --language — so
# each is checked before it can get that far, and each check has a test
# that proves it can refuse.


def _load_stt(monkeypatch, tmp_path, body):
    from vocalize.config import load_config_file

    _isolate(monkeypatch, tmp_path, body)
    return load_config_file()


def test_stt_table_loads_with_valid_values(monkeypatch, tmp_path):
    from vocalize.config import resolve_stt

    data = _load_stt(
        monkeypatch, tmp_path,
        '[stt]\nmodel = "base.en"\nlanguage = "en"\ncleanup = true\n'
        'max_seconds = 30\ninput_device = "Built-in Microphone"\n',
    )

    resolved = resolve_stt(data)
    assert resolved["model"] == "base.en"
    assert resolved["cleanup"] is True
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


def test_the_english_only_stt_rule_also_catches_the_default_model(monkeypatch, tmp_path):
    # No model line at all: the default is small.en, which is still .en.
    with pytest.raises(ConfigError):
        _load_stt(monkeypatch, tmp_path, '[stt]\nlanguage = "de"\n')


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


def test_an_stt_value_that_is_not_a_table_is_refused(monkeypatch, tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        _load_stt(monkeypatch, tmp_path, 'stt = "small.en"\n')

    assert "'stt'" in str(excinfo.value)


def test_resolve_stt_revalidates_a_hand_built_dict():
    """The portal and any other caller hand this a dict it never read."""
    from vocalize.config import resolve_stt

    with pytest.raises(ConfigError):
        resolve_stt({"stt": {"model": "--serve"}})
