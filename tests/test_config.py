import os

import pytest

from vocalize.config import (
    DEFAULT_MODEL,
    DEFAULT_VOICE,
    _load_dotenv_if_present,
    config_path,
    resolve_api_key,
    resolve_overflow,
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
