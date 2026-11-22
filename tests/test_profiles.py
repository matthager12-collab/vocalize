"""Machine profiles: presets of registered settings, chosen by memory."""
from pathlib import Path

import pytest

from vocalize import config, profiles, wizard
from vocalize import settings_registry as registry

STUDIO_CONFIG = (Path(__file__).resolve().parent.parent / "docs" / "plans"
                 / "2026-11-local-only" / "studio-config.toml")
ALLOWED_INSTALLS = {"install_kokoro", "install_parakeet", "install_whisper", "install_cleanup"}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for var in ("VOCALIZE_VOICE", "VOCALIZE_MODEL", "VOCALIZE_SPEED", "VOCALIZE_CHAIN",
                "VOCALIZE_OVERFLOW", "VOCALIZE_MAX_CHARS"):
        monkeypatch.delenv(var, raising=False)


def _toml(path: Path) -> dict:
    try:
        import tomllib
    except ImportError:  # Python 3.10
        import tomli as tomllib
    with path.open("rb") as fh:
        return tomllib.load(fh)


def test_profiles_in_order_with_contract_fields():
    assert profiles.names() == ("small", "standard", "large", "studio")
    assert tuple(p.name for p in profiles.PROFILES) == profiles.names()
    memories = [p.min_memory_gb for p in profiles.PROFILES]
    assert memories == [0, 16, 32, 64]
    for profile in profiles.PROFILES:
        assert profiles.get(profile.name) is profile
        assert profile.label and profile.help
        assert isinstance(profile.installs, tuple)


def test_get_unknown_profile_raises_key_error():
    with pytest.raises(KeyError):
        profiles.get("huge")


@pytest.mark.parametrize("name", ["small", "standard", "large", "studio"])
def test_every_key_registered_and_every_value_validates(name):
    for key, value in profiles.changes(name).items():
        registry.by_key(key)
        assert registry.validate(key, value) == value


@pytest.mark.parametrize("name", ["small", "standard", "large", "studio"])
def test_changes_is_a_fresh_plain_dict(name):
    out = profiles.changes(name)
    assert type(out) is dict
    out["stt.beam_size"] = 3
    assert profiles.changes(name)["stt.beam_size"] != 3


def test_every_profile_sets_the_same_keys_so_a_smaller_one_resets():
    keys = {frozenset(profiles.changes(name)) for name in profiles.names()}
    assert len(keys) == 1


def test_small_matches_the_package_defaults():
    defaults = registry.defaults()
    small = profiles.changes("small")
    assert small
    for key, value in small.items():
        assert value == defaults[key], key


def test_studio_values_follow_the_design_table():
    assert profiles.changes("studio") == {
        "stt.engine": "parakeet", "stt.model": "large-v3-turbo-q8_0",
        "stt.beam_size": registry.defaults()["stt.beam_size"],
        "stt.warm_minutes": 240, "stt.cleanup": "local",
    }
    assert profiles.changes("studio")["stt.beam_size"] == 5
    # Parakeet has no warm server and cleanup is off: a window would keep
    # nothing loaded, so these profiles set none (W5-B).
    assert profiles.changes("standard")["stt.warm_minutes"] == 0
    assert profiles.changes("large")["stt.warm_minutes"] == 0
    assert profiles.changes("large")["stt.model"] == "large-v3-turbo-q8_0"


@pytest.mark.parametrize("name", ["small", "standard", "large", "studio"])
def test_a_warm_window_is_set_only_where_something_can_stay_loaded(name):
    """Only Whisper and the cleanup model have warm servers."""
    changes = profiles.changes(name)
    keeps_something = changes["stt.engine"] != "parakeet" or changes["stt.cleanup"] == "local"
    if not keeps_something:
        assert changes["stt.warm_minutes"] == 0


@pytest.mark.parametrize("name", ["standard", "large"])
def test_a_parakeet_profile_without_cleanup_says_why_nothing_stays_loaded(name):
    text = profiles.get(name).help
    assert "keeps the model loaded" not in text
    assert "Parakeet" in text and "nothing" in text and "loaded" in text


def test_the_studio_help_says_only_the_cleanup_model_stays_loaded():
    text = profiles.get("studio").help
    assert "keeps models loaded" not in text
    assert "keeps the cleanup model loaded for 4 hours" in text


@pytest.mark.parametrize("name", ["standard", "large", "studio"])
def test_a_parakeet_profile_says_what_its_engine_really_does(name):
    text = profiles.get(name).help
    assert profiles.changes(name)["stt.engine"] == "parakeet"
    assert "Dictates and transcribes files with Parakeet" in text
    assert "apply only if you switch the engine to Whisper" in text
    assert "beams" not in text and "wherever Whisper runs" not in text
    assert "Parakeet is never kept loaded" in text


def test_the_small_profile_says_it_uses_whisper():
    text = profiles.get("small").help
    assert profiles.installs("small") == ("install_kokoro", "install_whisper")
    assert "dictation and file transcription use Whisper" in text


def test_only_the_studio_profile_claims_a_measurement():
    assert "Measured on a Mac Studio (Apple M5 Max, 128 GB)" in profiles.get("studio").help
    for name in ("small", "standard", "large"):
        text = profiles.get(name).help
        assert "Not measured on" in text and "Measured on a Mac Studio" not in text
    doc = profiles.__doc__
    assert "provisional" not in doc and "until the engines are measured" not in doc
    assert "Apple M5 Max, 128 GB" in doc and "not measured on a small" in doc


def test_install_params_name_the_model_a_profile_recommends():
    assert profiles.install_params("studio") == {"install_whisper": {"model": "large-v3-turbo-q8_0"}}
    for name in ("small", "standard", "large"):
        assert profiles.install_params(name) == {}
    out = profiles.install_params("studio")
    assert type(out) is dict and type(out["install_whisper"]) is dict
    out["install_whisper"]["model"] = "base.en"
    assert profiles.install_params("studio")["install_whisper"]["model"] == "large-v3-turbo-q8_0"
    with pytest.raises(TypeError):
        profiles.get("studio").install_params["install_whisper"] = {}


@pytest.mark.parametrize("name", ["small", "standard", "large", "studio"])
def test_install_params_are_for_recommended_installs_with_valid_values(name):
    from vocalize.local import whisper_manifest

    params = profiles.install_params(name)
    assert set(params) <= set(profiles.installs(name))
    if "install_whisper" in params:
        assert params["install_whisper"]["model"] in whisper_manifest.MODELS
        assert params["install_whisper"]["model"] == profiles.changes(name)["stt.model"]


@pytest.mark.parametrize("name", ["small", "standard", "large", "studio"])
def test_installs_are_allowed_action_ids(name):
    ids = profiles.installs(name)
    assert ids and set(ids) <= ALLOWED_INSTALLS
    assert len(set(ids)) == len(ids)
    assert ("install_cleanup" in ids) == (profiles.changes(name)["stt.cleanup"] == "local")
    if profiles.changes(name)["stt.engine"] == "parakeet":
        assert "install_parakeet" in ids


def _registered_paths(data: dict, prefix: str = "") -> set[str]:
    out = set()
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            out |= _registered_paths(value, path + ".")
        else:
            out.add(path)
    return out


@pytest.mark.parametrize("name", ["small", "standard", "large", "studio"])
def test_applying_to_an_empty_config_writes_only_registered_keys(name):
    data = registry.apply_changes({}, profiles.changes(name))
    written = _registered_paths(data)
    assert written == set(profiles.changes(name))
    for key in written:
        registry.by_key(key)
    assert registry.values(data) == {**registry.defaults(), **profiles.changes(name)}


@pytest.mark.parametrize("name", ["small", "standard", "large", "studio"])
def test_applying_keeps_unrelated_keys(name):
    before = {
        "chain": ["say", "kokoro"],
        "stt": {"paste": True, "vocabulary": ["Kokoro"], "warm_minutes": 5},
        "app": {"dictate": "ctrl+alt+cmd+d"},
        "providers": {"kokoro": {"voice": "af_bella"}},
    }
    after = registry.apply_changes(before, profiles.changes(name))
    assert before["stt"]["warm_minutes"] == 5  # input untouched
    assert after["chain"] == ["say", "kokoro"]
    assert after["stt"]["paste"] is True
    assert after["stt"]["vocabulary"] == ["Kokoro"]
    assert after["app"] == {"dictate": "ctrl+alt+cmd+d"}
    assert after["providers"] == {"kokoro": {"voice": "af_bella"}}
    added = _registered_paths(after) - _registered_paths(before)
    assert added <= set(profiles.changes(name))
    for key, value in profiles.changes(name).items():
        assert registry.values(after)[key] == value


@pytest.mark.parametrize("memory, expected", [
    (8.0, "small"), (15.99, "small"), (16.0, "standard"), (31.5, "standard"),
    (32.0, "large"), (63.9, "large"), (64.0, "studio"), (128.0, "studio"),
    (0.0, "small"),
])
def test_detect_picks_the_largest_profile_met(monkeypatch, memory, expected):
    monkeypatch.setattr(profiles, "memory_gb", lambda: memory)
    assert profiles.detect() == expected


def test_memory_gb_unreadable_is_zero(monkeypatch):
    def broken(name):
        raise ValueError(name)

    monkeypatch.setattr(profiles.os, "sysconf", broken)
    assert profiles.memory_gb() == 0.0
    assert profiles.detect() == "small"


def test_memory_gb_reads_pages(monkeypatch):
    sizes = {"SC_PAGE_SIZE": 16384, "SC_PHYS_PAGES": 128 * 1024 ** 3 // 16384}
    monkeypatch.setattr(profiles.os, "sysconf", sizes.__getitem__)
    assert profiles.memory_gb() == 128.0
    assert profiles.detect() == "studio"


def test_studio_config_loads_without_notice(monkeypatch, tmp_path, capfd):
    target = tmp_path / "vocalize" / "config.toml"
    target.parent.mkdir(parents=True)
    target.write_text(STUDIO_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    assert config.config_path() == target
    data = config.load_config_file()
    assert config.retired_notices(data) == []
    resolved = registry.values(data)
    captured = capfd.readouterr()
    assert captured.err == ""
    assert captured.out == ""
    assert resolved == {**registry.defaults(), **profiles.changes("studio")}
    assert "vocabulary" not in _toml(STUDIO_CONFIG)["stt"]


def test_studio_config_is_the_writer_output_plus_comments():
    data = registry.apply_changes({}, profiles.changes("studio"))
    rendered = [line for line in wizard._render_config_text(data).splitlines() if line]
    in_file = [line for line in STUDIO_CONFIG.read_text(encoding="utf-8").splitlines()
               if line and not line.startswith("#")]
    assert in_file == rendered


def test_studio_config_header_says_what_it_is():
    header = [line for line in STUDIO_CONFIG.read_text(encoding="utf-8").splitlines()
              if line.startswith("#")]
    text = " ".join(header).lower()
    assert "the config a mac studio has after applying the studio profile" in text
    assert "provisional" not in text and "owner" not in text
